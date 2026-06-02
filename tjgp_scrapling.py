# -*- coding: utf-8 -*-
"""
天津市政府采购网 定时爬虫
------------------------------------------------------------
功能：
  1. 每隔 CHECK_INTERVAL 秒搜索 KEYWORDS 中的每个关键词
  2. 只保留符合日期条件的采购公告
  3. 对还没抓过的公告，进入详情页提取正文 
  4. 保存为 txt 文件：./天津市政府采购信息/<日期>/，txt 内记录匹配关键词
  5. 用记录文件去重，重复运行不会重复下载

依赖：
  pip install "scrapling[fetchers]"
"""

import json
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from scrapling.fetchers import Fetcher

from feishu_push import try_push_announcement, try_push_text

# ==================== 固定配置 ====================
BASE        = "http://tjgp.cz.tj.gov.cn"
SEARCH_URL  = f"{BASE}/portal/topicView.do"
SAVE_ROOT   = Path("./data/天津市政府采购信息")
SEEN_FILE         = SAVE_ROOT / "_已抓取.txt"
BODY_SCANNED_FILE = SAVE_ROOT / "_已扫描正文.txt"
CONFIG_FILE       = Path("./config.json")

WAIT_JSP_SLEEP = 90   # 遇到 wait.jsp 等待秒数
LOG_DIR        = Path("./log/tjgp")

# 只采集这4个分类：(id, 附加POST参数)
SCAN_CATEGORIES = [
    ("1665", {"type": "1"}),  # 采购公告-市级
    ("1664", {}),              # 采购公告-区级
    ("2021", {}),              # 采购意向公开-市级
    ("2022", {}),              # 采购意向公开-区级
]
CAT_NAMES = {
    "1665": "采购公告-市级",
    "1664": "采购公告-区级",
    "2021": "采购意向公开-市级",
    "2022": "采购意向公开-区级",
}

CONTENT_SELECTORS = [
    "#pageContent",
    "#content",
    ".detail",
    ".article",
    ".TRS_Editor",
]

# ==================== 默认值 ====================
DEFAULT_KEYWORDS       = ["天津工业大学", "能源", "供热", "托管", "空气", "运维", "供暖"]
DEFAULT_CHECK_INTERVAL = 7200
# =================================================

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"tjgp_{datetime.now():%Y-%m-%d}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_config() -> dict:
    """每次调用时重新读取 config.json，进程运行期间修改配置立即生效"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("tjgp", {})
        except Exception as e:
            log(f"读取配置文件失败，使用默认值: {e}")
    return {}


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(SEEN_FILE.read_text(encoding="utf-8").splitlines())
    return set()


def mark_seen(url: str):
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SEEN_FILE.open("a", encoding="utf-8") as f:
        f.write(url + "\n")


def load_body_scanned(keywords: list) -> set:
    """返回已用当前全部关键词扫过的 URL 集合。
    每行格式 url|||kw1,kw2，同一 URL 多行取并集；
    并集覆盖所有当前关键词才算扫过，否则需重扫。
    读取后自动压缩：每 URL 只保留一行（合并关键词）。"""
    if not BODY_SCANNED_FILE.exists():
        return set()
    current = set(keywords)
    url_kws: dict = {}
    for line in BODY_SCANNED_FILE.read_text(encoding="utf-8").splitlines():
        if "|||" not in line:
            continue
        url, kws_str = line.split("|||", 1)
        kws = set(kws_str.split(",")) if kws_str else set()
        url_kws[url] = url_kws.get(url, set()) | kws
    # 压缩：重写为每 URL 一行（合并后的关键词），消除历史重复行
    BODY_SCANNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BODY_SCANNED_FILE.open("w", encoding="utf-8") as f:
        for url, kws in url_kws.items():
            f.write(f"{url}|||{','.join(sorted(kws))}\n")
    return {url for url, kws in url_kws.items() if current <= kws}


def mark_body_scanned(url: str, keywords: list):
    """追加本次扫描所用的关键词集合，与历史记录取并集后覆盖能力只增不减"""
    BODY_SCANNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BODY_SCANNED_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{url}|||{','.join(sorted(keywords))}\n")


def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    name = name.strip().strip(".")
    return name[:max_len] if len(name) > max_len else name



def parse_pub_date(raw: str) -> str:
    """
    支持两种格式：
      "2026-05-22"（method=find 返回）→ 直接截取
      "Fri May 22 16:37:06 CST 2026"（其他接口）→ 手动拆分
    """
    raw = raw.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    parts = raw.split()
    if len(parts) < 6:
        return ""
    try:
        month = _MONTH_MAP.get(parts[1], 0)
        day   = int(parts[2])
        year  = int(parts[5])
        return f"{year}-{month:02d}-{day:02d}"
    except (ValueError, IndexError):
        return ""


def fetch_search(keyword: str, start_date: str, end_date: str, page: int = 1,
                 cat_id: str = "", extra_data: dict | None = None):
    """POST 搜索并返回 Response 对象；cat_id 非空时限定分类"""
    data = {
        "name":      keyword,
        "ldateQGE":  start_date,
        "ldateQLE":  end_date,
        "ldateQGE1": start_date,
        "ldateQLE1": end_date,
        "px":        "2",
        "topId":     "",
        "st":        "1",
        "pageNum":   str(page),
    }
    if cat_id:
        data["id"] = cat_id
    if extra_data:
        data.update(extra_data)
    return Fetcher.post(
        f"{SEARCH_URL}?method=find",
        data=data,
        stealthy_headers=True,
        headers={"Referer": SEARCH_URL},
        follow_redirects=False,
    )


def parse_total_pages(page) -> int:
    """从搜索结果页解析总页数，找不到则返回 1"""
    m = re.search(r"共<b>(\d+)</b>页", page.html_content or "")
    if m:
        return max(1, int(m.group(1)))
    return 1


def parse_result_items(page, start_date: str, end_date: str) -> list:
    """从一页搜索结果中提取符合日期范围的条目"""
    items = []
    lis = page.css("ul.dataList > li")
    if not lis:
        lis = page.css("#div_ul_1 > li")

    for li in lis:
        links = li.css("a")
        if not links:
            continue
        a     = links[0]
        href  = a.attrib.get("href", "")
        title = a.get_all_text(ignore_tags=("font",)).strip()
        if not href or not title:
            continue

        time_nodes = li.css("span.time")
        raw_date   = time_nodes[0].get_all_text().strip() if time_nodes else ""
        pub_date   = parse_pub_date(raw_date)
        if not pub_date or not (start_date <= pub_date <= end_date):
            continue

        url = urljoin(BASE, href)
        items.append({"date": pub_date, "title": title, "url": url})

    return items


def _fetch_pages(keyword: str, start_date: str, end_date: str,
                 cat_id: str, log_prefix: str, extra_data: dict | None = None) -> list:
    """翻页抓取单个分类的结果"""
    results     = []
    total_pages = 1
    for page_num in range(1, 100):
        try:
            resp = fetch_search(keyword, start_date, end_date, page_num, cat_id=cat_id, extra_data=extra_data)
        except Exception as e:
            log(f"  {log_prefix}请求失败 (第{page_num}页): {e}")
            break
        if hasattr(resp, "status") and resp.status in (301, 302, 303):
            break
        items = parse_result_items(resp, start_date, end_date)
        results.extend(items)
        if page_num == 1:
            total_pages = parse_total_pages(resp)
        if page_num >= total_pages or not items:
            break
        time.sleep(1)
    return results


def search_announcements(keyword: str, start_date: str, end_date: str) -> list:
    """POST 搜索，跨 SCAN_CATEGORIES 翻页，返回符合日期范围的所有条目"""
    seen_urls = set()
    results   = []
    for cat_id, extra in SCAN_CATEGORIES:
        items = _fetch_pages(keyword, start_date, end_date, cat_id,
                             log_prefix=f"搜索[{cat_id}]", extra_data=extra or None)
        for it in items:
            if it["url"] not in seen_urls:
                seen_urls.add(it["url"])
                results.append(it)
    log(f"  共 {len(results)} 条（跨 {len(SCAN_CATEGORIES)} 个分类）")
    return results


def fetch_all_listings(start_date: str, end_date: str) -> tuple[list, dict]:
    """无关键词，获取 SCAN_CATEGORIES 日期范围内全部公告列表；同时返回各分类条数 dict"""
    seen_urls = set()
    results   = []
    cat_stats: dict = {}
    for cat_id, extra in SCAN_CATEGORIES:
        items = _fetch_pages("", start_date, end_date, cat_id,
                             log_prefix=f"全量[{cat_id}]", extra_data=extra or None)
        log(f"  分类 {cat_id}: {len(items)} 条")
        cat_stats[cat_id] = len(items)
        for it in items:
            if it["url"] not in seen_urls:
                seen_urls.add(it["url"])
                results.append(it)
    return results, cat_stats


def extract_content(page) -> str:
    """从详情页提取正文"""
    for sel in CONTENT_SELECTORS:
        nodes = page.css(sel)
        node  = nodes[0] if nodes else None
        if node is not None:
            text = node.get_all_text(ignore_tags=("script", "style")).strip()
            if len(text) > 100:
                return text
    paragraphs = [
        p.get_all_text().strip()
        for p in page.css("p")
        if len(p.get_all_text().strip()) > 10
    ]
    return "\n".join(paragraphs)


def save_announcement(item: dict, content: str | None = None):
    """抓取单条公告详情并保存为 txt；content 已知时跳过抓取"""
    url = item["url"]
    if content is None:
        log(f"  抓取详情: {item['title'][:40]}...")
        detail = Fetcher.get(url, stealthy_headers=True, headers={"Referer": SEARCH_URL})
        if "wait.jsp" in (getattr(detail, "url", "") or ""):
            raise RuntimeError("访问受限 (wait.jsp)，跳过")
        content = extract_content(detail)

    folder = SAVE_ROOT / item["date"]
    folder.mkdir(parents=True, exist_ok=True)

    m        = re.search(r"id=(\d+)", url)
    file_id  = m.group(1) if m else str(int(time.time()))
    filename = f"{file_id}_{safe_filename(item['title'])}.txt"

    kw_line = "、".join(item.get("matched_keywords", []))
    text = (
        f"标题：{item['title']}\n"
        f"日期：{item['date']}\n"
        f"链接：{url}\n"
        f"抓取时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"匹配关键词：{kw_line}\n"
        f"{'=' * 60}\n\n"
        f"{content}\n"
    )
    (folder / filename).write_text(text, encoding="utf-8")
    log(f"  已保存: {item['date']}/{filename}  关键词: {kw_line}")


def check_once():
    """执行一次完整检查"""
    cfg      = load_config()
    keywords = cfg.get("keywords", DEFAULT_KEYWORDS)
    use_date = cfg.get("use_date", 0)
    today    = datetime.now().strftime("%Y-%m-%d")

    if use_date == 1:
        start_date = cfg.get("target_date_start", "").strip() or today
        end_date   = cfg.get("target_date_end",   "").strip() or today
    else:
        start_date = end_date = today

    log(f"开始检查，日期范围: {start_date} ~ {end_date}，关键词: {keywords}")

    seen       = load_seen()
    seen_start = set(seen)   # 本次运行前已采集的快照
    # url -> {"item": dict, "keywords": set, "content": str|None}
    pending: dict = {}

    # 阶段1：标题匹配——收集所有命中，允许同一条目积累多个关键词
    for keyword in keywords:
        log(f"搜索关键词（标题）: 「{keyword}」")
        items = search_announcements(keyword, start_date, end_date)
        log(f"  符合条件的公告: {len(items)} 条")
        for it in items:
            if it["url"] in seen:
                continue
            url = it["url"]
            if url not in pending:
                pending[url] = {"item": it, "keywords": set(), "content": None}
            pending[url]["keywords"].add(keyword)

    title_match_count = len(pending)
    log(f"标题匹配共 {title_match_count} 条（去重后）")

    # 阶段2：优先保存标题命中条目（在大量正文抓取触发限流前完成）
    new_count = 0
    for url, data in list(pending.items()):
        it = data["item"]
        it["matched_keywords"] = sorted(data["keywords"])
        try:
            save_announcement(it)
            ok = try_push_announcement(it, source="天津市政府采购")
            if ok:
                mark_seen(url)
                seen.add(url)
                pending.pop(url)
                new_count += 1
            time.sleep(2)
        except Exception as e:
            log(f"  保存出错，跳过: {e}")
            traceback.print_exc()

    # 阶段3：正文匹配——获取全部公告，逐条检查正文
    log("开始正文关键词检查...")
    all_items, cat_stats = fetch_all_listings(start_date, end_date)
    body_scanned = load_body_scanned(keywords)
    remaining    = [it for it in all_items
                    if it["url"] not in seen and it["url"] not in body_scanned]
    log(f"  需检查正文的公告: {len(remaining)} 条（已扫过 {len(body_scanned)} 条）")

    body_checked = 0
    for it in remaining:
        try:
            detail = Fetcher.get(it["url"], stealthy_headers=True, headers={"Referer": SEARCH_URL})
            if "wait.jsp" in (getattr(detail, "url", "") or ""):
                log(f"  触发访问限制 (wait.jsp)，等待 {WAIT_JSP_SLEEP}s 后重试...")
                time.sleep(WAIT_JSP_SLEEP)
                detail = Fetcher.get(it["url"], stealthy_headers=True, headers={"Referer": SEARCH_URL})
                if "wait.jsp" in (getattr(detail, "url", "") or ""):
                    log("  重试仍受限，停止正文扫描（下次从此处继续）")
                    break
            content = extract_content(detail)
            matched = [kw for kw in keywords if kw in content]
            push_ok = True
            if matched:
                log(f"  正文命中{matched}: {it['title'][:40]}...")
                it["matched_keywords"] = sorted(matched)
                try:
                    save_announcement(it, content=content)
                    push_ok = try_push_announcement(it, source="天津市政府采购")
                    if push_ok:
                        mark_seen(it["url"])
                        seen.add(it["url"])
                        new_count += 1
                except Exception as e:
                    log(f"  保存出错，跳过: {e}")
                    push_ok = False
            if push_ok:
                mark_body_scanned(it["url"], keywords)
                body_scanned.add(it["url"])
            body_checked += 1
            time.sleep(2)
        except Exception as e:
            log(f"  正文检查出错，跳过: {e}")
            traceback.print_exc()

    cat_total = sum(cat_stats.values())
    cat_line  = " | ".join(
        f"{CAT_NAMES.get(cid, cid)}={cnt}" for cid, cnt in cat_stats.items()
    )
    collected_total = (
        sum(
            len(list(d.glob("*.txt")))
            for d in SAVE_ROOT.iterdir()
            if d.is_dir() and start_date <= d.name <= end_date
        )
        if SAVE_ROOT.exists() else 0
    )
    already_count = max(0, collected_total - new_count)
    summary = (
        f"【天津市政府采购】本次运行摘要\n"
        f"日期范围：{start_date} ~ {end_date}\n"
        f"关键词：{' '.join(keywords)}\n"
        f"标题匹配：{title_match_count} 条\n"
        f"全量抓取：{cat_line} | 共{cat_total}条（去重后{len(all_items)}条）\n"
        f"正文扫描：本次检查{body_checked}条（已扫{len(body_scanned)}条）\n"
        f"已采集公告：{already_count} 条\n"
        f"新增公告：{new_count} 条"
    )
    log(
        f"\n{'=' * 40}\n"
        f"  本次运行摘要\n"
        f"  天津市政府采购信息\n"
        f"  日期范围  : {start_date} ~ {end_date}\n"
        f"  关键词    : {' '.join(keywords)}\n"
        f"  标题匹配  : {title_match_count} 条（去重后）\n"
        f"  全量抓取  : {cat_line} | 共{len(all_items)}条\n"
        f"  正文扫描  : 本次检查{body_checked}条（已扫{len(body_scanned)}条）\n"
        f"  已采集公告: {already_count} 条\n"
        f"  新增公告  : {new_count} 条\n"
        f"{'=' * 40}"
    )
    if new_count > 0:
        time.sleep(1)
        try_push_text(summary)


def main():
    log(f"监控启动，配置文件: {CONFIG_FILE.resolve()}，按 Ctrl+C 退出")
    while True:
        try:
            check_once()
        except Exception as e:
            log(f"检查过程异常: {e}")
            traceback.print_exc()
        interval = load_config().get("check_interval", DEFAULT_CHECK_INTERVAL)
        log(f"休眠 {interval // 60} 分钟后再次检查...\n")
        time.sleep(interval)


if __name__ == "__main__":
    main()

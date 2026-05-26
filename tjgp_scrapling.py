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

import calendar
import json
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from scrapling.fetchers import Fetcher

# ==================== 固定配置 ====================
BASE        = "http://tjgp.cz.tj.gov.cn"
SEARCH_URL  = f"{BASE}/portal/topicView.do"
SAVE_ROOT   = Path("./天津市政府采购信息")
SEEN_FILE         = SAVE_ROOT / "_已抓取.txt"
BODY_SCANNED_FILE = SAVE_ROOT / "_已扫描正文.txt"
CONFIG_FILE       = Path("./tjgp_config.json")

WAIT_JSP_SLEEP = 90   # 遇到 wait.jsp 等待秒数

CONTENT_SELECTORS = [
    "#pageContent",
    "#content",
    ".detail",
    ".article",
    ".TRS_Editor",
]

# ==================== 默认值 ====================
DEFAULT_KEYWORDS       = ["天津工业大学", "能源", "供热", "托管", "空气", "运维", "供暖"]
DEFAULT_TARGET_DATE    = ""
DEFAULT_TARGET_MONTH   = ""
DEFAULT_CHECK_INTERVAL = 7200
# =================================================

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_config() -> dict:
    """每次调用时重新读取 tjgp_config.json，进程运行期间修改配置立即生效"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
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


def load_body_scanned() -> set:
    if BODY_SCANNED_FILE.exists():
        return set(BODY_SCANNED_FILE.read_text(encoding="utf-8").splitlines())
    return set()


def mark_body_scanned(url: str):
    BODY_SCANNED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with BODY_SCANNED_FILE.open("a", encoding="utf-8") as f:
        f.write(url + "\n")


def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    name = name.strip().strip(".")
    return name[:max_len] if len(name) > max_len else name


def date_range(date_filter: str):
    """
    将 date_filter 转为 (ldateQGE, ldateQLE)。
    "YYYY-MM-DD" → 同一天；"YYYY-MM" → 该月第一天到最后一天。
    """
    if len(date_filter) == 10:  # YYYY-MM-DD
        return date_filter, date_filter
    year, month = int(date_filter[:4]), int(date_filter[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return f"{date_filter}-01", f"{date_filter}-{last_day:02d}"


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


def fetch_search(keyword: str, start_date: str, end_date: str, page: int = 1):
    """POST 搜索并返回 Response 对象"""
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


def parse_result_items(page, date_filter: str) -> list:
    """从一页搜索结果中提取符合日期的条目"""
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
        if not pub_date or not pub_date.startswith(date_filter):
            continue

        url = urljoin(BASE, href)
        items.append({"date": pub_date, "title": title, "url": url})

    return items


def search_announcements(keyword: str, date_filter: str) -> list:
    """
    POST 搜索，自动翻页，返回符合日期过滤的所有条目。
    date_filter: "YYYY-MM-DD" 或 "YYYY-MM"
    """
    start, end = date_range(date_filter)
    results    = []
    total_pages = 1

    for page_num in range(1, 100):
        try:
            resp = fetch_search(keyword, start, end, page_num)
        except Exception as e:
            log(f"  搜索请求失败 (第{page_num}页): {e}")
            break

        if hasattr(resp, "status") and resp.status in (301, 302, 303):
            break

        items = parse_result_items(resp, date_filter)
        results.extend(items)

        if page_num == 1:
            total_pages = parse_total_pages(resp)
            log(f"  共 {total_pages} 页")

        if page_num >= total_pages or not items:
            break

        time.sleep(1)

    return results


def fetch_all_listings(date_filter: str) -> list:
    """无关键词搜索，获取日期范围内全部公告列表（不进详情页）"""
    start, end  = date_range(date_filter)
    results     = []
    total_pages = 1

    for page_num in range(1, 100):
        try:
            resp = fetch_search("", start, end, page_num)
        except Exception as e:
            log(f"  全量搜索失败 (第{page_num}页): {e}")
            break

        if hasattr(resp, "status") and resp.status in (301, 302, 303):
            break

        items = parse_result_items(resp, date_filter)
        results.extend(items)

        if page_num == 1:
            total_pages = parse_total_pages(resp)
            log(f"  全量公告共 {total_pages} 页")

        if page_num >= total_pages or not items:
            break

        time.sleep(1)

    return results


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
    cfg          = load_config()
    keywords     = cfg.get("keywords",      DEFAULT_KEYWORDS)
    target_date  = cfg.get("target_date",   DEFAULT_TARGET_DATE).strip()
    target_month = cfg.get("target_month",  DEFAULT_TARGET_MONTH).strip()

    if target_date:
        date_filter = target_date
    elif target_month:
        date_filter = target_month
    else:
        date_filter = datetime.now().strftime("%Y-%m-%d")

    log(f"开始检查，过滤条件: {date_filter}，关键词: {keywords}")

    seen    = load_seen()
    # url -> {"item": dict, "keywords": set, "content": str|None}
    pending: dict = {}

    # 阶段1：标题匹配——收集所有命中，允许同一条目积累多个关键词
    for keyword in keywords:
        log(f"搜索关键词（标题）: 「{keyword}」")
        items = search_announcements(keyword, date_filter)
        log(f"  符合条件的公告: {len(items)} 条")
        for it in items:
            if it["url"] in seen:
                continue
            url = it["url"]
            if url not in pending:
                pending[url] = {"item": it, "keywords": set(), "content": None}
            pending[url]["keywords"].add(keyword)

    log(f"标题匹配共 {len(pending)} 条（去重后）")

    # 阶段2：优先保存标题命中条目（在大量正文抓取触发限流前完成）
    new_count = 0
    for url, data in list(pending.items()):
        it = data["item"]
        it["matched_keywords"] = sorted(data["keywords"])
        try:
            save_announcement(it)
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
    all_items    = fetch_all_listings(date_filter)
    body_scanned = load_body_scanned()
    remaining    = [it for it in all_items
                    if it["url"] not in seen and it["url"] not in body_scanned]
    log(f"  需检查正文的公告: {len(remaining)} 条（已扫过 {len(body_scanned)} 条）")

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
            if matched:
                log(f"  正文命中{matched}: {it['title'][:40]}...")
                it["matched_keywords"] = sorted(matched)
                try:
                    save_announcement(it, content=content)
                    mark_seen(it["url"])
                    seen.add(it["url"])
                    new_count += 1
                except Exception as e:
                    log(f"  保存出错，跳过: {e}")
            mark_body_scanned(it["url"])
            body_scanned.add(it["url"])
            time.sleep(2)
        except Exception as e:
            log(f"  正文检查出错，跳过: {e}")
            traceback.print_exc()

    log(f"本次新增 {new_count} 条" if new_count else "本次没有新公告")


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

# -*- coding: utf-8 -*-
"""
云南中烟 搜索公告 定时爬虫
------------------------------------------------------------
功能：
  1. 每隔 CHECK_INTERVAL 秒搜索 KEYWORDS 中的每个关键词
  2. 只保留"公告信息"栏目（catalogInnerCode 前缀 001341）且当天发布的条目
  3. 对还没抓过的公告，进入详情页提取正文
  4. 保存为 txt 文件：./云南中烟招标公告信息/<关键词>/<日期>/
  5. 用记录文件去重，重复运行不会重复下载

依赖：
  pip install "scrapling[fetchers]"
"""

import json
import re
import time
import traceback
import urllib.parse
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from scrapling.fetchers import Fetcher

# ==================== 固定配置（不需要动态调整的参数）====================
BASE        = "https://www.ynzy-tobacco.com"
SEARCH_API  = f"{BASE}/zcms/front/search/result"
SITE_ID     = "128"
PAGE_SIZE   = 15

GONGGAO_CODE_PREFIX = "001341"
SAVE_ROOT      = Path("./云南中烟招标公告信息")
SEEN_FILE      = SAVE_ROOT / "_已抓取.txt"
CONFIG_FILE    = Path("./ynzy_config.json")

CONTENT_SELECTORS = [
    ".news_d_text",
    ".article .text",
    ".article",
    ".view",
    ".content",
    ".TRS_Editor",
    "#zoom",
    ".xl_content",
    ".detail",
]

# ==================== 默认值（config.json 缺字段时使用）====================
DEFAULT_KEYWORDS      = ["能源", "信息化"]
DEFAULT_TARGET_DATE   = ""
DEFAULT_TARGET_MONTH  = ""
DEFAULT_CHECK_INTERVAL = 7200
# =========================================================================


def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_config() -> dict:
    """每次调用时重新读取 config.json，进程运行期间修改配置立即生效"""
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


def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    name = name.strip().strip(".")
    return name[:max_len] if len(name) > max_len else name


def fetch_page(url: str):
    """抓取页面，伪装成真实浏览器请求头"""
    return Fetcher.get(url, stealthy_headers=True)


def call_search_api(keyword: str, page_index: int) -> dict:
    """调用搜索 JSON API，返回解析后的字典"""
    params = urllib.parse.urlencode({
        "siteID":    SITE_ID,
        "query":     keyword,
        "pageIndex": page_index,
        "pageSize":  PAGE_SIZE,
        "sort":      "publishDate",
    })
    resp = fetch_page(f"{SEARCH_API}?{params}")
    return json.loads(resp.body.decode("utf-8"))


def search_announcements(keyword: str, date_filter: str) -> list:
    """
    搜索关键词，翻页取回符合日期过滤的公告信息条目。
    date_filter 可以是 "YYYY-MM-DD"（精确日期）或 "YYYY-MM"（整月）。
    每条结构: {"date": "YYYY-MM-DD", "title": "...", "url": "完整链接"}
    """
    results = []
    page_index = 0

    while True:
        try:
            data = call_search_api(keyword, page_index)
        except Exception as e:
            log(f"  搜索API失败 (页{page_index}): {e}")
            break

        if data.get("status") != 1:
            log(f"  搜索API返回异常状态: {data.get('status')}")
            break

        page_data = data.get("data", {})
        items     = page_data.get("data", [])
        total     = page_data.get("total", 0)

        if not items:
            break

        for item in items:
            pub_date = str(item.get("publishDate") or "")[:10]   # YYYY-MM-DD
            inner_code = item.get("catalogInnerCode") or ""
            url   = item.get("url") or ""
            title = (item.get("title") or "").strip()

            if not pub_date.startswith(date_filter):
                continue
            if not inner_code.startswith(GONGGAO_CODE_PREFIX):
                continue
            if not url or not title:
                continue

            if not url.startswith("http"):
                url = urljoin(BASE, url)

            results.append({"date": pub_date, "title": title, "url": url})

        if (page_index + 1) * PAGE_SIZE >= total:
            break

        page_index += 1
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
    # 兜底：拼接所有较长段落
    paragraphs = [
        p.get_all_text().strip()
        for p in page.css("p")
        if len(p.get_all_text().strip()) > 10
    ]
    return "\n".join(paragraphs)


def save_announcement(item: dict):
    """抓取单条公告详情并保存为 txt"""
    url = item["url"]
    log(f"  抓取详情: {item['title'][:40]}...")
    detail  = fetch_page(url)
    content = extract_content(detail)

    folder = SAVE_ROOT / item.get("keyword", "未分类") / item["date"]
    folder.mkdir(parents=True, exist_ok=True)

    m       = re.search(r"/(\d+)\.s?html?$", url)
    file_id = m.group(1) if m else str(int(time.time()))
    filename = f"{file_id}_{safe_filename(item['title'])}.txt"

    text = (
        f"标题：{item['title']}\n"
        f"日期：{item['date']}\n"
        f"链接：{url}\n"
        f"抓取时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"{'=' * 60}\n\n"
        f"{content}\n"
    )
    (folder / filename).write_text(text, encoding="utf-8")
    log(f"  已保存: {item['date']}/{filename}")


def check_once():
    """执行一次完整检查"""
    cfg = load_config()
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

    seen      = load_seen()
    new_count = 0

    for keyword in keywords:
        log(f"搜索关键词: 「{keyword}」")
        items = search_announcements(keyword, date_filter)
        log(f"  符合条件的公告信息: {len(items)} 条")

        for it in items:
            if it["url"] in seen:
                continue
            it["keyword"] = keyword
            try:
                save_announcement(it)
                mark_seen(it["url"])
                seen.add(it["url"])
                new_count += 1
                time.sleep(2)
            except Exception as e:
                log(f"  抓取出错，跳过: {e}")
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

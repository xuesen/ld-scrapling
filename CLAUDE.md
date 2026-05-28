# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```powershell
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Running the scrapers

```powershell
# Run both scrapers concurrently (recommended — each in its own daemon thread)
python main.py

# Run a single scraper continuously (loops every check_interval seconds, Ctrl+C to stop)
python ynzy_scrapling.py
python tjgp_scrapling.py

# Run a single check
python -c "import ynzy_scrapling; ynzy_scrapling.check_once()"
python -c "import tjgp_scrapling; tjgp_scrapling.check_once()"
```

## Configuration

Both scrapers hot-reload their config on every loop iteration — edit `config.json` while the process is running and the next cycle picks it up.

**`config.json`**:
```json
{
  "ynzy": {
    "keywords": ["能源", "信息化"],
    "use_date": 0,
    "target_date_start": "2026-05-01",
    "target_date_end": "2026-05-31",
    "check_interval": 7200
  },
  "tjgp": {
    "keywords": ["天津工业大学", "能源", "供热"],
    "use_date": 0,
    "target_date_start": "2026-05-01",
    "target_date_end": "2026-05-31",
    "check_interval": 7200
  }
}
```

- `use_date=0`: collect today only; `use_date=1`: use `target_date_start` ~ `target_date_end`
- `check_interval`: loop sleep in seconds

## Architecture

**Entry point**: `main.py` runs both scrapers concurrently — each `check_once()` runs in a daemon thread with its own sleep/reload cycle. Logs to `log/main/main_YYYY-MM-DD.log`.

**Notifications**: `feishu_push.py` sends new announcements and run summaries to a Feishu group bot. Requires `feishu_config.json` with `webhook` and `secret` fields. HMAC-SHA256 signing is inlined in `feishu_push.py`. Push calls are always wrapped in `try_push_*` functions that silently skip if config is absent.

Two independent single-file scrapers. Each `check_once()` ends by writing a structured run summary to the log.

Logs: `log/ynzy/ynzy_YYYY-MM-DD.log` and `log/tjgp/tjgp_YYYY-MM-DD.log` (daily rotating, auto-created).

### ynzy_scrapling.py — 云南中烟 (`www.ynzy-tobacco.com`)

Single-phase: the search API already filters by keyword server-side, so results are fetched, then each matching article's detail page is fetched once to save full content. No body scan needed.

```
check_once() → for each keyword:
    search_announcements(keyword, start_date, end_date) → [items]
    for each new item: save_announcement(item)   # fetches detail page
```

- **Search**: JSON API at `/zcms/front/search/result`; params `siteID=128`, `query`, `pageIndex` (0-based), `pageSize=15`. Response parsed via `resp.body.decode("utf-8")` (not `.html_content`). Results sorted by `publishDate` descending — stops fetching pages early when `pub_date < start_date`.
- **Channel filter**: `catalogInnerCode` must start with `"001341"` (公告信息). Other prefixes: `001338` = 新闻资讯, `001337` = 关于我们.
- **Content selector**: `.news_d_text` (primary), falls back to `<p>` concatenation.
- **Output**: `./data/云南中烟招标公告信息/<YYYY-MM-DD>/<article_id>_<title>.txt` — file header includes matched keyword.
- **Dedup file**: `./data/云南中烟招标公告信息/_已抓取.txt` — URL written only after Feishu push succeeds; failed pushes are retried next cycle.

### tjgp_scrapling.py — 天津市政府采购网 (`tjgp.cz.tj.gov.cn`)

Three-phase within each `check_once()`:

```
Phase 1 — title keyword search:
    for each keyword: search_announcements(keyword, start_date, end_date)
    accumulates matched URLs → pending dict (same URL may match multiple keywords)

Phase 2 — save title-matched items immediately (before body scan triggers rate limit)

Phase 3 — full listing body scan:
    fetch_all_listings(start_date, end_date) → all announcements regardless of keyword
    skip URLs already in _已抓取.txt or _已扫描正文.txt (with current keyword coverage)
    fetch each remaining article body → check if any keyword in content → save if matched
    on wait.jsp: wait WAIT_JSP_SLEEP=90s, retry once; if still blocked, stop scan and resume next cycle
```

- **Search**: HTML form POST to `/portal/topicView.do?method=find`; params `name`, `ldateQGE`, `ldateQLE`, `st=1`, `pageNum`. **Must pass `headers={"Referer": SEARCH_URL}`** — the server rejects requests with a Google Referer (scrapling's stealthy default).
- **Categories**: `SCAN_CATEGORIES` = 4 entries: `1665` (采购公告-市级, requires `type=1`), `1664` (采购公告-区级), `2021` (采购意向公开-市级), `2022` (采购意向公开-区级). Both `search_announcements` and `fetch_all_listings` iterate all four.
- **Date parsing**: `parse_pub_date()` handles `"2026-05-22"` and `"Fri May 22 16:37:06 CST 2026"` without locale dependency.
- **Pagination**: parse `共<b>N</b>页` from HTML; POST with increasing `pageNum`.
- **Content selector**: `#pageContent` (primary), falls back to `<p>` concatenation.
- **Output**: `./data/天津市政府采购信息/<YYYY-MM-DD>/<article_id>_<title>.txt` — file header includes matched keywords.
- **Dedup files**:
  - `./data/天津市政府采购信息/_已抓取.txt` — URL written only after Feishu push succeeds; failed pushes are retried next cycle.
  - `./data/天津市政府采购信息/_已扫描正文.txt` — body-scanned URLs with format `url|||kw1,kw2,...`; on load, keyword sets are unioned per URL and the file is rewritten (compacted to one line per URL); a URL is skipped only when its union covers all current keywords, allowing re-scan when new keywords are added. For matched URLs, this file is also only written after a successful push — so a failed push causes both a re-save and a re-push next cycle.

## scrapling API notes

- `page.css(selector)` returns a list; there is **no** `.css_first()` method. Use `nodes[0] if nodes else None`.
- `resp.html_content` wraps content in `<html><body>` tags — use `resp.body.decode("utf-8")` for JSON APIs.
- `stealthy_headers=True` sets Referer to `https://www.google.com/` by default. Override with `headers={"Referer": ...}` for sites that validate the Referer header.
- `Fetcher.post(url, data=Dict[str,str], ...)` for form submissions.
- Check for rate-limit redirects via `getattr(resp, "url", "")` containing `"wait.jsp"` — not via HTTP status, since `follow_redirects=False` means the redirect itself is the response.

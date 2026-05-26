# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```powershell
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install "scrapling[fetchers]"
```

## Running the scrapers

```powershell
# Run continuously (loops every check_interval seconds, Ctrl+C to stop)
python ynzy_scrapling.py
python tjgp_scrapling.py

# Run a single check
python -c "import ynzy_scrapling; ynzy_scrapling.check_once()"
python -c "import tjgp_scrapling; tjgp_scrapling.check_once()"
```

## Configuration

Both scrapers hot-reload their config on every loop iteration — edit the JSON file while the process is running and the next cycle picks it up.

**`ynzy_config.json`** (for `ynzy_scrapling.py`):
```json
{
  "keywords": ["能源", "信息化"],
  "target_date": "",
  "target_month": "",
  "check_interval": 7200
}
```

**`tjgp_config.json`** (for `tjgp_scrapling.py`):
```json
{
  "keywords": ["天津工业大学"],
  "target_date": "",
  "target_month": "",
  "check_interval": 7200
}
```

- `target_date`: `"YYYY-MM-DD"` to collect a specific day; empty = today
- `target_month`: `"YYYY-MM"` to collect an entire month; ignored if `target_date` is set
- `check_interval`: loop sleep in seconds

## Architecture

Two independent single-file scrapers sharing the same structural pattern:

```
main() → loop → check_once() → for each keyword:
    search_announcements(keyword, date_filter) → [items]
    for each new item: save_announcement(item)
```

### ynzy_scrapling.py — 云南中烟 (`www.ynzy-tobacco.com`)

- **Search**: JSON API at `/zcms/front/search/result`; params `siteID=128`, `query`, `pageIndex` (0-based), `pageSize=15`. Response parsed via `resp.body.decode("utf-8")` (not `.html_content`).
- **Channel filter**: `catalogInnerCode` must start with `"001341"` (公告信息). Other prefixes: `001338` = 新闻资讯, `001337` = 关于我们.
- **Content selector**: `.news_d_text` (primary), falls back to `<p>` concatenation.
- **Output**: `./云南中烟招标公告信息/<keyword>/<YYYY-MM-DD>/<article_id>_<title>.txt`
- **Dedup file**: `./云南中烟招标公告信息/_已抓取.txt`

### tjgp_scrapling.py — 天津市政府采购网 (`tjgp.cz.tj.gov.cn`)

- **Search**: HTML form POST to `/portal/topicView.do?method=find`; params `name`, `ldateQGE`, `ldateQLE`, `st=1`, `pageNum`. **Must pass `headers={"Referer": SEARCH_URL}`** — the server rejects requests with a Google Referer (scrapling's stealthy default).
- **Date param**: `date_range()` converts `"YYYY-MM"` to first/last day pair using `calendar.monthrange`; `"YYYY-MM-DD"` maps to identical start/end.
- **Date parsing**: `parse_pub_date()` handles both `"2026-05-22"` (returned by `method=find`) and `"Fri May 22 16:37:06 CST 2026"` (other endpoints) without locale dependency.
- **Pagination**: parse `共<b>N</b>页` from HTML; POST with increasing `pageNum`.
- **Content selector**: `#pageContent` (primary), falls back to `<p>` concatenation.
- **Output**: `./天津市政府采购信息/<keyword>/<YYYY-MM-DD>/<article_id>_<title>.txt`
- **Dedup file**: `./天津市政府采购信息/_已抓取.txt`

## scrapling API notes

- `page.css(selector)` returns a list; there is **no** `.css_first()` method. Use `nodes[0] if nodes else None`.
- `resp.html_content` wraps content in `<html><body>` tags — use `resp.body.decode("utf-8")` for JSON APIs.
- `stealthy_headers=True` sets Referer to `https://www.google.com/` by default. Override with `headers={"Referer": ...}` for sites that validate the Referer header.
- `Fetcher.post(url, data=Dict[str,str], ...)` for form submissions.

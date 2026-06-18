# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```powershell
# Activate virtual environment (Windows)
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

**Requires Python 3.10+** — the scrapers use `X | None` and `tuple[list, dict]` type hints (PEP 604/585); older interpreters raise `SyntaxError`. The Docker image uses `python:3.12-slim`.

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

**There is no test suite, linter, or build step.** Verification is done by running a single `check_once()` against the live sites and reading the resulting log line / `data` output.

**Debugging tips:**
- To test a single keyword in isolation, edit `config.json` to only that keyword and run `check_once()` — config is read fresh on every call.
- To run locally without sending Feishu messages, rename or delete `feishu_config.json`; both scrapers silently skip push when the config is absent, and dedup files are still written so a later run with push enabled will re-send.

## Data and log paths

```
data/云南中烟招标公告信息/{YYYY-MM-DD}/   ynzy fetched articles
data/天津市政府采购信息/{YYYY-MM-DD}/      tjgp fetched articles
data/<site>/_已抓取.txt                    URL dedup (written only after Feishu push succeeds)
data/天津市政府采购信息/_已扫描正文.txt    tjgp body-scan dedup with per-URL keyword coverage
log/{main,ynzy,tjgp}/<name>_YYYY-MM-DD.log daily rotating logs
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
- Dates are interpreted in the **process's local timezone**. The Docker image pins `TZ=Asia/Shanghai`; when running outside Docker on a non-CST host, set `TZ` (or `$env:TZ` on Windows) to avoid the "today" window drifting by a day.

## Architecture

**Entry point**: `main.py` runs both scrapers concurrently — each `check_once()` runs in a daemon thread with its own sleep/reload cycle. Logs to `log/main/main_YYYY-MM-DD.log`.

**Notifications**: `feishu_push.py` sends new announcements and run summaries to a Feishu group bot. Requires `feishu_config.json` with `webhook` and `secret` fields. HMAC-SHA256 signing is inlined in `feishu_push.py`. Push calls are always wrapped in `try_push_*` functions that silently skip if config is absent. The run summary is only pushed to Feishu when `new_count > 0` — silent runs produce no Feishu message.

Two independent single-file scrapers. Each `check_once()` ends by writing a structured run summary to the log.

**Shared dedup invariant (both scrapers)**: the per-article `.txt` is written *unconditionally*, but a URL is recorded in a dedup file *only after its Feishu push returns success*. This makes the dedup files a record of "successfully notified", not "successfully fetched" — a failed/absent push leaves the URL un-deduped so the next cycle re-saves and re-pushes it (the `.txt` may be overwritten with identical content). When editing save/push logic, preserve this ordering or you will get silent drops or duplicate notifications.

**Shared content extraction (both scrapers)**: `extract_content()` walks `CONTENT_SELECTORS` in order and accepts the first selector whose text exceeds **100 chars**; only if none qualify does it concatenate every `<p>` longer than 10 chars. So the "primary" selector named per-scraper below is used only when it yields a substantial block — a short or mis-structured detail page silently falls through to the `<p>` fallback (the usual cause of a saved `.txt` containing nav/footer text or near-empty content).

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
- **Output**: `./data/云南中烟招标公告信息/<YYYY-MM-DD>/<article_id>_<title>.txt` — file header includes matched keyword. The txt is written unconditionally; only the dedup record depends on push success.
- **Dedup file**: `./data/云南中烟招标公告信息/_已抓取.txt` — URL written only after Feishu push succeeds; failed pushes are retried next cycle (txt may be overwritten on retry with identical content).

### tjgp_scrapling.py — 天津市政府采购网 (`www.ccgp-tianjin.gov.cn`)

> **站点迁移 (2026-06-13)**：原域名 `http://tjgp.cz.tj.gov.cn` 已被 nginx 301 永久跳转到现域名 `https://www.ccgp-tianjin.gov.cn`（同一套 portal CMS，仅域名 + 协议变化；搜索接口、分类 ID、列表与正文选择器均不变，故修复只改了 `BASE` 一行）。**排查线索**：列表抓取路径遇 3xx 会静默 `break` 返回 0 条——若某天起全分类持续 0 条且无任何报错，优先怀疑站点再次迁移/改域名，而不是关键词或限流问题。

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

Pinned to `scrapling[fetchers]>=0.4.8` (see `requirements.txt`). The API moves fast across minor versions — re-verify the notes below before bumping.

- `page.css(selector)` returns a list; there is **no** `.css_first()` method. Use `nodes[0] if nodes else None`.
- `resp.html_content` wraps content in `<html><body>` tags — use `resp.body.decode("utf-8")` for JSON APIs.
- `stealthy_headers=True` sets Referer to `https://www.google.com/` by default. Override with `headers={"Referer": ...}` for sites that validate the Referer header.
- `Fetcher.post(url, data=Dict[str,str], ...)` for form submissions.
- Check for rate-limit redirects via `getattr(resp, "url", "")` containing `"wait.jsp"` — not via HTTP status, since `follow_redirects=False` means the redirect itself is the response.

## Docker deployment notes

- The full server runbook (SFTP file list, `docker compose up -d --build`, log watching, `docker image prune -f`) lives in `README.md`.
- `docker-compose.yml` mounts `config.json`, `feishu_config.json`, `data/`, `log/` as volumes — rebuilding the image does not affect these.
- `TZ=Asia/Shanghai` is set in the compose environment to prevent log timestamps from appearing in UTC.
- `Dockerfile` installs dependencies from the Aliyun PyPI mirror (`mirrors.aliyun.com/pypi/simple/`); the default PyPI index times out from the deployment host, so keep the mirror flag when editing it.

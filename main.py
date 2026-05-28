# -*- coding: utf-8 -*-
import json
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import ynzy_scrapling
import tjgp_scrapling


CONFIG_FILE = Path("./config.json")

SCRAPERS = [
    (ynzy_scrapling.check_once, "ynzy", "云南中烟"),
    (tjgp_scrapling.check_once, "tjgp", "天津采购"),
]

DEFAULT_INTERVAL = 7200
LOG_DIR = Path("./log/main")


def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"main_{datetime.now():%Y-%m-%d}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run_loop(check_fn, section: str, name: str):
    log(f"[{name}] 监控线程启动")
    while True:
        try:
            check_fn()
        except Exception as e:
            log(f"[{name}] 异常: {e}")
            traceback.print_exc()
        try:
            interval = (
                json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                .get(section, {})
                .get("check_interval", DEFAULT_INTERVAL)
            )
        except Exception:
            interval = DEFAULT_INTERVAL
        log(f"[{name}] 休眠 {interval // 60} 分钟")
        time.sleep(interval)


def main():
    log("主进程启动，按 Ctrl+C 退出")
    threads = []
    for check_fn, cfg, name in SCRAPERS:
        t = threading.Thread(target=_run_loop, args=(check_fn, cfg, name), daemon=True)
        t.start()
        threads.append(t)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("已停止")


if __name__ == "__main__":
    main()

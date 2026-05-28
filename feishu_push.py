# -*- coding: utf-8 -*-
"""
飞书群机器人推送
------------------------------------------------------------
配置文件 feishu_config.json：
  {
    "webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
    "secret":  "your-secret-key"
  }

对外接口：
  try_push_announcement(item, source) → bool
    item  : {"title", "date", "url", "keyword" or "matched_keywords"}
    source: 来源标识，如 "云南中烟" 或 "天津市政府采购"

  try_push_text(text) → bool
    发送任意纯文本，用于推送运行摘要等。
    推送失败时只打印警告，不抛异常，不影响主流程。
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.request
import urllib.error
from pathlib import Path


def gen_sign(secret: str, timestamp: int = 0) -> tuple:
    if timestamp == 0:
        timestamp = int(time.time())
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return timestamp, sign

FEISHU_CONFIG_FILE = Path("./feishu_config.json")


def _load_config() -> dict:
    if FEISHU_CONFIG_FILE.exists():
        try:
            return json.loads(FEISHU_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _keyword_str(item: dict) -> str:
    kw = item.get("matched_keywords") or item.get("keyword") or ""
    if isinstance(kw, list):
        return "、".join(kw)
    return str(kw)


def _build_payload(secret: str, item: dict, source: str) -> dict:
    timestamp, sign = gen_sign(secret)
    title   = item.get("title", "")
    date    = item.get("date", "")
    url     = item.get("url", "")
    keyword = _keyword_str(item)

    text = (
        f"【{source}】新公告\n"
        f"标题：{title}\n"
        f"日期：{date}\n"
        f"关键词：{keyword}\n"
        f"链接：{url}"
    )
    return {
        "timestamp": str(timestamp),
        "sign":      sign,
        "msg_type":  "text",
        "content":   {"text": text},
    }


def push_announcement(webhook: str, secret: str, item: dict, source: str = "") -> bool:
    """推送单条公告，返回 True 表示成功（code == 0）。"""
    payload = _build_payload(secret, item, source)
    data    = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result.get("code") == 0
    except urllib.error.URLError as e:
        raise RuntimeError(f"HTTP 请求失败: {e}") from e


def try_push_text(text: str) -> bool:
    """推送纯文本消息（用于运行摘要等），失败时打印警告但不抛异常。"""
    cfg     = _load_config()
    webhook = cfg.get("webhook", "").strip()
    secret  = cfg.get("secret", "").strip()
    if not webhook or not secret:
        return False
    try:
        timestamp, sign = gen_sign(secret)
        payload = {
            "timestamp": str(timestamp),
            "sign":      sign,
            "msg_type":  "text",
            "content":   {"text": text},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        ok = result.get("code") == 0
        if not ok:
            print(f"[飞书推送] 服务端拒绝，请检查 webhook/secret 配置")
        return ok
    except Exception as e:
        print(f"[飞书推送] 推送失败，跳过: {e}")
        return False


def try_push_announcement(item: dict, source: str = "") -> bool:
    """
    读取配置并推送，失败时打印警告但不抛异常。
    未配置 feishu_config.json 时静默跳过（返回 False）。
    """
    cfg     = _load_config()
    webhook = cfg.get("webhook", "").strip()
    secret  = cfg.get("secret", "").strip()
    if not webhook or not secret:
        return False
    try:
        ok = push_announcement(webhook, secret, item, source)
        if not ok:
            print(f"[飞书推送] 服务端拒绝，请检查 webhook/secret 配置")
        return ok
    except Exception as e:
        print(f"[飞书推送] 推送失败，跳过: {e}")
        return False

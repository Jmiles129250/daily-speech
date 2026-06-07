#!/usr/bin/env python3
"""
notify.py — push today's speech to WeChat after generation/deploy.

Two channels, picked automatically based on which env vars are set:

1. Server 酱 (https://sct.ftqq.com/) — push to your personal WeChat via
   "方糖" service. Requires a single secret:
       WECHAT_SENDKEY = SCT...
   The push arrives as a message from the 方糖 public account, which
   the user binds to their personal WeChat by scanning a QR code.

2. WeCom (企业微信) group bot — push to a WeCom group via the bot
   webhook. Requires a single secret:
       WECHAT_WEBHOOK = https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
   The script posts a Markdown message; the message is rendered by the
   WeCom client.

If both are set, Server 酱 is preferred.

Exit codes:
  0 — push sent successfully (HTTP 2xx)
  1 — nothing to do (no env vars)
  2 — push attempted but failed (network or upstream error)
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEECHES_DIR = REPO_ROOT / "speeches"
MANIFEST_FILE = SPEECHES_DIR / "manifest.json"
INDEX_FILE = SPEECHES_DIR / "index.json"

WECHAT_SENDKEY = os.environ.get("WECHAT_SENDKEY", "").strip()
WECHAT_WEBHOOK = os.environ.get("WECHAT_WEBHOOK", "").strip()
SITE_URL = os.environ.get("SITE_URL", "https://Jmiles129250.github.io/daily-speech/").rstrip("/") + "/"


def fetch_manifest() -> dict | None:
    """Read manifest.json from disk. Tries manifest.json first, falls back to index.json."""
    for path in (MANIFEST_FILE, INDEX_FILE):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = data.get("entries") if isinstance(data, dict) else data
            if isinstance(entries, list) and entries:
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return None


def fetch_speech_body(file_rel: str) -> tuple[str, str] | None:
    """Return (title, body) of a speech file. Strips YAML frontmatter."""
    path = REPO_ROOT / file_rel
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    title = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > -1:
            fm = text[3:end].strip()
            m = re.search(r"^title:\s*(.+)$", fm, re.MULTILINE)
            if m:
                title = m.group(1).strip()
            body = text[end + 4:].lstrip("\n")
    if not title:
        first = body.strip().splitlines()[0] if body.strip() else ""
        title = first.strip().strip("《》").strip() or "今日演讲"
    return title, body.strip()


def extract_excerpt(body: str, max_chars: int = 220) -> str:
    """Pick a short, readable excerpt from the body, removing markdown noise."""
    text = re.sub(r"^#+\s+.*$", "", body, flags=re.MULTILINE)
    text = re.sub(r"^《[^》]+》\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\n{2,}", "\n\n", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        # try to cut on a sentence boundary
        for sep in ("。", "！", "?", "\n"):
            idx = cut.rfind(sep)
            if idx > max_chars * 0.6:
                cut = cut[: idx + 1]
                break
        text = cut + "…"
    return text


def post_json(url: str, payload: dict, timeout: int = 20) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except URLError as e:
        return 0, f"URLError: {e}"


def post_form(url: str, fields: dict, timeout: int = 20) -> tuple[int, str]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except URLError as e:
        return 0, f"URLError: {e}"


def push_server_chan(sendkey: str, title: str, desp: str) -> tuple[int, str]:
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    return post_form(url, {"title": title, "desp": desp})


def push_wecom(webhook: str, title: str, desp: str) -> tuple[int, str]:
    safe_title = title.replace("\n", " ")
    content = (
        f"## {safe_title}\n\n"
        f"{desp}\n\n"
        f"[👉 去看完整版]({SITE_URL})"
    )
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    return post_json(webhook, payload)


def main() -> int:
    manifest = fetch_manifest()
    if not manifest:
        print("[notify] no manifest found, nothing to push", file=sys.stderr)
        return 1
    entries = manifest.get("entries", [])
    if not entries:
        print("[notify] manifest is empty", file=sys.stderr)
        return 1
    # Pick the most recent entry (manifest is sorted desc by date)
    today = entries[0]
    info = fetch_speech_body(today.get("file", ""))
    if not info:
        print(f"[notify] speech file not found: {today.get('file')}", file=sys.stderr)
        return 1
    title, body = info
    excerpt = extract_excerpt(body)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", body))
    desp = (
        f"**日期**：{today['date']}\n\n"
        f"**摘要**：\n\n> {excerpt}\n\n"
        f"**总长度**：约 {cjk_count} 个汉字 · "
        f"约 5 分钟朗读\n\n"
        f"👉 [打开每日演讲]({SITE_URL})\n"
        f"📄 [查看本次演讲稿原文]({SITE_URL}{today.get('file', '')})"
    )

    if WECHAT_SENDKEY:
        status, resp = push_server_chan(WECHAT_SENDKEY, f"每日演讲 · {title}", desp)
        channel = "serverchan"
    elif WECHAT_WEBHOOK:
        status, resp = push_wecom(WECHAT_WEBHOOK, f"每日演讲 · {title}", desp)
        channel = "wecom"
    else:
        print("[notify] no WECHAT_SENDKEY or WECHAT_WEBHOOK set; skipping", file=sys.stderr)
        return 1

    if 200 <= status < 300:
        print(f"[notify] OK via {channel}: HTTP {status}")
        return 0
    print(f"[notify] FAILED via {channel}: HTTP {status} body={resp[:300]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

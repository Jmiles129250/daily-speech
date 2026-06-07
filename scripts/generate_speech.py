#!/usr/bin/env python3
"""
generate_speech.py

Generates one Chinese speech per day and writes it under speeches/YYYY-MM-DD.md
with YAML frontmatter. Idempotent: if today's file already exists, exits 0.

Environment variables:
  LLM_API_KEY   (required) - API key for the LLM provider
  LLM_API_BASE  (optional) - base URL, default https://api.minimaxi.com/v1 (MiniMax China)
  LLM_API_PATH  (optional) - path appended to LLM_API_BASE; default /text/chatcompletion_v2
                              (MiniMax uses a different path than OpenAI's /chat/completions)
  LLM_MODEL     (optional) - model name, default MiniMax-M2.5
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

# --- Configuration -----------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEECHES_DIR = REPO_ROOT / "speeches"
INDEX_FILE = SPEECHES_DIR / "index.json"

LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://api.minimaxi.com/v1").rstrip("/")
LLM_API_PATH = os.environ.get("LLM_API_PATH", "/text/chatcompletion_v2")
LLM_MODEL = os.environ.get("LLM_MODEL", "MiniMax-M2.5")

# Beijing time (UTC+8) — no DST
BEIJING_TZ = timezone(timedelta(hours=8))

# Body length window, in CJK characters (汉字)
MIN_CHARS = 1200
MAX_CHARS = 1600

SYSTEM_PROMPT = (
    "你是一位擅长中文演讲的资深演讲教练与世界故事编辑。"
    "请根据用户的主题与方向,创作一篇 5 分钟左右的中文演讲稿,"
    "字数严格控制在 1200 到 1600 个汉字之间(不含标点)。\n\n"
    "要求:\n"
    "1. 开场 30 秒内用一个具体画面、悬念或反常识事实抓住听众。\n"
    "2. 必须包含一个清晰的戏剧冲突(转折、误解、艰难选择、意外),"
    "让人产生共鸣。\n"
    "3. 主题要有哲理或管理哲学意味,避免空泛鸡汤。\n"
    "4. 取材可以来自历史人物、企业家、艺术家、科学家、普通人。\n"
    "5. 语言必须口语化,适合朗读,避免书面语和长句。\n"
    "6. 结构: 钩子 -> 故事 -> 转折 -> 启示 -> 行动号召。\n"
    "7. 标题用书名号《》包裹,放在第一行。\n\n"
    "输出格式:\n"
    "《标题》\n"
    "(正文,不要分点列表,自然段即可)"
)

# --- Helpers ----------------------------------------------------------------

CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def count_cjk(text: str) -> int:
    """Count Chinese characters in text (excludes punctuation and ASCII)."""
    return len(CJK_CHAR_RE.findall(text))


def today_str() -> str:
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def strip_think_blocks(text: str) -> str:
    return THINK_BLOCK_RE.sub("", text)


def load_index_entries() -> list[dict]:
    if not INDEX_FILE.exists():
        return []
    try:
        with INDEX_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data["entries"]
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def collect_forbidden_titles(entries: list[dict]) -> list[str]:
    titles: list[str] = []
    for e in entries:
        t = (e.get("title") or "").strip()
        if t:
            titles.append(t)
    return titles


def extract_title_and_body(raw: str) -> tuple[str, str]:
    """
    Pull the title (with 《》 stripped) and body out of the LLM output.
    Falls back to the first heading or first 20 chars of body.
    """
    text = raw.strip()

    # Prefer a leading "《...》" line.
    lines = text.splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("《") and s.endswith("》") and len(s) > 2:
            title = s[1:-1].strip()
            body_start = i + 1
            break

    body = "\n".join(lines[body_start:]).strip()

    if not title:
        # Try first markdown heading.
        for i, line in enumerate(lines):
            s = line.strip()
            m = re.match(r"^#\s+(.+)$", s)
            if m:
                title = m.group(1).strip().strip("《》").strip()
                body = "\n".join(lines[i + 1:]).strip()
                break

    if not title:
        # Try the first non-empty line.
        for line in lines:
            s = line.strip().strip("《》").strip()
            if s:
                title = s[:20]
                break

    if not body:
        # Last-resort: derive body from the raw text minus the first line.
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else text

    if not title:
        title = body[:20].replace("\n", " ").strip() or "今日演讲"

    return title, body


def call_llm(messages: list[dict], temperature: float = 0.9) -> str:
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("LLM_API_KEY is not set")

    url = f"{LLM_API_BASE}{LLM_API_PATH}"
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with urlrequest.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError("LLM returned empty content")
            return content
        except HTTPError as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
        except URLError as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM request failed: {last_err}")


def build_user_prompt(date_str: str, forbidden_titles: list[str]) -> str:
    forbidden_block = ""
    if forbidden_titles:
        recent = forbidden_titles[-30:]
        quoted = "、".join(f"《{t}》" for t in recent)
        forbidden_block = (
            f"\n\n注意:以下是最近已用过的标题,本次请避免重复或近义改写:\n"
            f"{quoted}"
        )
    return (
        f"今天是 {date_str} (北京时间)。"
        "请按你的判断,选一个你今天最想对普通人讲的主题,"
        "创作一篇 5 分钟的中文演讲稿。"
        "请直接输出标题与正文,不要任何额外说明或前言。"
        f"{forbidden_block}"
    )


def length_fix_prompt(current: str, actual_len: int) -> str:
    if actual_len < MIN_CHARS:
        delta = MIN_CHARS - actual_len
        return (
            f"你上一稿只有 {actual_len} 个汉字,偏短约 {delta} 字。"
            "请在保持结构(钩子->故事->转折->启示->行动号召)的前提下,"
            "扩写一个具体场景或补一段细节,让总汉字数落在 1200 到 1600 之间。"
            f"原稿如下,请在此基础上改写,直接输出新稿(标题 + 正文):\n\n{current}"
        )
    return (
        f"你上一稿有 {actual_len} 个汉字,超出 1600 字上限。"
        "请精简非关键细节、压缩重复表达,让总汉字数落在 1200 到 1600 之间,"
        "结构与戏剧冲突必须保留。直接输出新稿(标题 + 正文):\n\n"
        f"{current}"
    )


def write_speech_file(date_str: str, title: str, body: str) -> Path:
    SPEECHES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SPEECHES_DIR / f"{date_str}.md"
    # Avoid YAML-breaking strings: keep title single line, no special quoting needed.
    safe_title = title.replace("\n", " ").strip()
    frontmatter = (
        "---\n"
        f"date: {date_str}\n"
        f"title: {safe_title}\n"
        "---\n\n"
    )
    out_path.write_text(frontmatter + body.strip() + "\n", encoding="utf-8")
    return out_path


def append_index_atomically(date_str: str, title: str, file_rel: str) -> None:
    """Read index.json, append the new entry, write to tmp, rename."""
    entries = load_index_entries()
    # Drop any pre-existing entry for the same date to keep it idempotent.
    entries = [e for e in entries if e.get("date") != date_str]
    entries.append({"date": date_str, "title": title, "file": file_rel})
    # Sort by date ascending (build_index.py will reverse for the manifest).
    entries.sort(key=lambda e: e.get("date", ""))
    payload = {"entries": entries}
    tmp = INDEX_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(INDEX_FILE)


# --- Main --------------------------------------------------------------------


def main() -> int:
    date_str = today_str()
    out_path = SPEECHES_DIR / f"{date_str}.md"
    if out_path.exists():
        print(f"[skip] {out_path.relative_to(REPO_ROOT)} already exists for {date_str}")
        return 0

    entries = load_index_entries()
    forbidden_titles = collect_forbidden_titles(entries)

    user_prompt = build_user_prompt(date_str, forbidden_titles)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    print(f"[gen] calling {LLM_MODEL} via {LLM_API_BASE} for {date_str}")
    raw = call_llm(messages, temperature=0.9)
    raw = strip_think_blocks(raw)
    title, body = extract_title_and_body(raw)

    cjk_len = count_cjk(body)
    print(f"[gen] first pass CJK length = {cjk_len}")

    if not (MIN_CHARS <= cjk_len <= MAX_CHARS):
        fix_messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": length_fix_prompt(raw, cjk_len)},
        ]
        raw2 = call_llm(fix_messages, temperature=0.7)
        raw2 = strip_think_blocks(raw2)
        title2, body2 = extract_title_and_body(raw2)
        cjk_len2 = count_cjk(body2)
        print(f"[gen] retry CJK length = {cjk_len2}")
        if MIN_CHARS <= cjk_len2 <= MAX_CHARS:
            title, body, cjk_len = title2, body2, cjk_len2
        else:
            # Accept best-effort; we still emit the file so the site isn't empty.
            # Prefer the closer one.
            if abs(cjk_len2 - (MIN_CHARS + MAX_CHARS) / 2) < abs(
                cjk_len - (MIN_CHARS + MAX_CHARS) / 2
            ):
                title, body, cjk_len = title2, body2, cjk_len2
            print(f"[gen] warning: final CJK length {cjk_len} outside [{MIN_CHARS},{MAX_CHARS}]")

    file_rel = f"speeches/{date_str}.md"
    write_speech_file(date_str, title, body)
    append_index_atomically(date_str, title, file_rel)
    print(f"[ok] wrote {file_rel} title={title!r} cjk={cjk_len}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

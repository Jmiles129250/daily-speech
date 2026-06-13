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
    "你是一位擅长中文演讲的资深演讲教练与世界经典故事编辑。"
    "你以从中国古典典籍与可考史料中提炼出贴近当下人生的演讲素材而闻名。"
    "请根据用户给定的主题与方向,创作一篇 5 分钟左右的中文演讲稿,"
    "字数严格控制在 1200 到 1600 个汉字之间(不含标点)。\n\n"
    "硬性要求:\n"
    "1. 取材必须来自真实可考的史料,严禁虚构。优先库:\n"
    "   - 中国古典:《资治通鉴》《史记》《左传》《国语》《战国策》《论语》"
    "《孟子》《庄子》《世说新语》《贞观政要》《旧唐书》《新唐书》《资治通鉴》"
    "《宋史》《明史》《清史稿》《古文观止》及历代笔记(如《容斋随笔》《鹤林玉露》"
    "《阅微草堂笔记》)。\n"
    "   - 西方经典:希罗多德《历史》、修昔底德《伯罗奔尼撒战争史》、塔西佗《编年史》、"
    "普鲁塔克《希腊罗马名人传》、马可·奥勒留《沉思录》、蒙田《随笔集》。\n"
    "   - 近代人物:必须是真实姓名、真实年代、真实事件,出处可以是你确信的真实历史事件。\n"
    "2. 每个故事必须明确交代:朝代(纪元)、人物、事件、地点(如有)。引用古文必须"
    "标注书名与篇名(例:\"《资治通鉴·唐纪》载……\"、\"《论语·卫灵公》\")。\n"
    "3. 严禁虚构套路:不许用\"我有一个朋友\"\"我认识一个年轻人\"\"我有个同事\""
    "\"前两天我遇到一个人\"等。演讲者身份(我)可以以\"读史者\"\"讲故事的人\"现身,"
    "但故事主角必须是历史上真实存在过的人。\n"
    "4. 开场 30 秒内用一个具体画面、悬念或反常识事实抓住听众(例如:\"赤壁之战前夜,"
    "周瑜手里只有三万兵,曹操号称八十万。\")。\n"
    "5. 必须包含一个清晰的戏剧冲突(转折、误解、艰难选择、意外),让人产生共鸣。\n"
    "6. 主题要有哲理或管理哲学意味,避免空泛鸡汤;每次必须给出一个有锋芒的判断,而不只是罗列故事。\n"
    "7. 语言口语化,适合朗读,避免书面语和长句。\n"
    "8. 结构: 钩子(30 秒) -> 故事(2 分) -> 转折(1 分) -> 启示(1 分) -> 行动号召(30 秒)。\n"
    "9. 标题用书名号《》包裹,放在第一行,标题要具体(例:《周瑜为什么敢用三万人打赤壁》),"
    "不要用空泛格言式标题(《坚持就是胜利》《平凡的伟大》)。\n\n"
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
    # Rotate the suggestion pool by day-of-year so consecutive days
    # pull from different eras / classics and we get variety.
    try:
        doy = int(datetime.strptime(date_str, "%Y-%m-%d").strftime("%j"))
    except ValueError:
        doy = 0
    buckets = [
        "春秋战国:《左传》《战国策》《论语》《庄子》中的一则人物故事(子路、曾子、"
        "子贡、季康子、庄子、惠施、蔺相如、触龙、赵武灵王任选一)。",
        "秦汉:《史记》中的一则(项羽、刘邦、张良、韩信、萧何、陈平、司马迁任选一),"
        "重点是人物的某个不寻常选择。",
        "三国两晋:《资治通鉴》三国卷或《世说新语》里的一则(曹操、诸葛亮、"
        "周瑜、鲁肃、嵇康、阮籍、王羲之任选一)。",
        "唐五代:《贞观政要》《旧唐书》《新唐书》《资治通鉴》唐纪中的一则("
        "魏徵、房玄龄、长孙无忌、狄仁杰、郭子仪、李泌任选一),或安史之乱中"
        "的边缘人物。",
        "宋:《宋史》《资治通鉴》宋纪或《容斋随笔》中的一则(范仲淹、欧阳修、"
        "司马光、王安石、苏轼、岳飞、文天祥任选一),侧重他们仕途中的关键转折。",
        "明:《明史》《阅微草堂笔记》中的一则(王阳明、张居正、海瑞、徐光启、"
        "左光斗任选一),讲他们面对体制与自我的拉扯。",
        "清及近代:《清史稿》或可信近代史料中的一则(曾国藩、左宗棠、张之洞、"
        "李鸿章、康有为、梁启超任选一),侧重他们在变局中如何做决策。",
        "西方古代:希罗多德《历史》或普鲁塔克《希腊罗马名人传》中的一则("
        "梭伦、克伦威尔式人物请选古希腊;居鲁士、亚历山大、汉尼拔、西塞罗、"
        "恺撒任选一),翻译风格以中信出版社译文为准。",
        "思想史切片:选一位思想家(韩非、董仲舒、朱熹、王阳明、培根、笛卡尔、"
        "尼采)的一则人生小场景(不是讲思想,讲人),让他们的人生决定给当下人启发。",
    ]
    suggested = buckets[doy % len(buckets)]
    return (
        f"今天是 {date_str} (北京时间)。\n\n"
        f"今日素材方向(供参考,可微调):\n{suggested}\n\n"
        "请从这个方向(或其他你判断更适合的史料)挑一个具体故事,创作一篇 5 分钟"
        "的中文演讲稿,必须真实可考,不能虚构人名/事件/年份。"
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
    collection = os.environ.get("COLLECTION", "default").strip() or "default"
    frontmatter = (
        "---\n"
        f"date: {date_str}\n"
        f"title: {safe_title}\n"
        f"collection: {collection}\n"
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
    force = os.environ.get("FORCE_REGENERATE", "").strip() in ("1", "true", "yes")
    if out_path.exists() and not force:
        print(f"[skip] {out_path.relative_to(REPO_ROOT)} already exists for {date_str}")
        return 0
    if force and out_path.exists():
        print(f"[force] regenerating {out_path.relative_to(REPO_ROOT)} for {date_str}")

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

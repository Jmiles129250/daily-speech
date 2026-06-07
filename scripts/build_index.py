#!/usr/bin/env python3
"""
build_index.py

Walks speeches/*.md in lexicographic order, parses the simple --- frontmatter
(no PyYAML required), and writes:
  - speeches/index.json   (entries sorted by date DESC, with excerpt)
  - speeches/manifest.json (same shape; frontend fetches this)

Excerpt = first 80 chars of the body after stripping a leading markdown
heading.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEECHES_DIR = REPO_ROOT / "speeches"
INDEX_FILE = SPEECHES_DIR / "index.json"
MANIFEST_FILE = SPEECHES_DIR / "manifest.json"
EXCERPT_LEN = 80


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Parse a minimal `--- ... ---` frontmatter block.

    Returns (meta_dict, body). If the frontmatter is missing or malformed,
    meta_dict is empty and body is the full text.
    """
    if not text.startswith("---\n"):
        # Tolerate a leading BOM/whitespace line, then `---`.
        stripped = text.lstrip("\ufeff").lstrip()
        if not stripped.startswith("---\n"):
            return {}, text
        text = stripped

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    meta: dict[str, str] = {}
    i = 1
    n = len(lines)
    while i < n and lines[i].strip() != "---":
        line = lines[i].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"').strip("'")
        i += 1
    body = "\n".join(lines[i + 1:]).lstrip("\n")
    return meta, body


def strip_markdown_heading(body: str) -> str:
    """Drop the first line if it is a markdown `# heading` or a `《...》` title line."""
    lines = body.splitlines()
    idx = 0
    while idx < len(lines):
        s = lines[idx].strip()
        if not s:
            idx += 1
            continue
        if s.startswith("#"):
            idx += 1
            break
        if s.startswith("《") and s.endswith("》") and len(s) > 2:
            idx += 1
            break
        break
    return "\n".join(lines[idx:]).strip()


def make_excerpt(body: str) -> str:
    cleaned = strip_markdown_heading(body)
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) <= EXCERPT_LEN:
        return cleaned
    return cleaned[:EXCERPT_LEN].rstrip() + "…"


def main() -> int:
    if not SPEECHES_DIR.exists():
        print(f"[error] speeches directory not found: {SPEECHES_DIR}", file=sys.stderr)
        return 1

    md_files = sorted(SPEECHES_DIR.glob("*.md"))
    if not md_files:
        print(f"[warn] no .md files in {SPEECHES_DIR}")

    entries: list[dict] = []
    for path in md_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[warn] cannot read {path}: {e}", file=sys.stderr)
            continue
        meta, body = parse_frontmatter(text)
        date = meta.get("date") or path.stem
        title = meta.get("title") or path.stem
        rel = path.relative_to(REPO_ROOT).as_posix()
        excerpt = make_excerpt(body)
        entries.append(
            {
                "date": date,
                "title": title,
                "file": rel,
                "excerpt": excerpt,
                "collection": meta.get("collection", "default"),
            }
        )

    # Sort by date descending (newest first)
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)

    payload = {"entries": entries}
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    INDEX_FILE.write_text(serialized, encoding="utf-8")
    MANIFEST_FILE.write_text(serialized, encoding="utf-8")
    print(f"[ok] wrote {len(entries)} entries to index.json and manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Count CJK characters in a file. Used while authoring seed speeches."""
import re
import sys
from pathlib import Path

CJK = re.compile(r"[\u4e00-\u9fff]")

def main() -> int:
    total = 0
    for p in sys.argv[1:]:
        text = Path(p).read_text(encoding="utf-8")
        n = len(CJK.findall(text))
        total += n
        print(f"{p}: {n} CJK chars")
    if len(sys.argv) > 2:
        print(f"TOTAL: {total}")
    return 0

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Find the real source keys for the two broken audiobook rows.

Exact-title matching found nothing, which means the audiobook's stored title
differs from the library's title. Widen to substring + filename search, then
print every candidate so the next step is grounded, not guessed.
"""
import json
import subprocess
import sys
from pathlib import Path

rows = json.loads(subprocess.run(
    ["curl", "-s", "http://127.0.0.1:5000/api/audiobooks"],
    capture_output=True, text=True).stdout)["audiobooks"]
meta = json.load(open("/usr/local/bin/GoodBooks/data/library_metadata.json"))

for r in rows:
    if r["chapters"] not in (None, 0, 1):
        continue
    t = r["title"]
    print(f"== {t[:70]}")
    print(f"   result: {r['result'][:100]}")
    # 1. by result filename stem
    stem = Path(r["result"]).name.split("__")[0]
    print(f"   result stem: {stem[:70]!r}")
    exact = [k for k, v in meta.items() if v.get("title") == t]
    substr = [k for k, v in meta.items()
              if stem.lower()[:24] in (v.get("title") or "").lower()
              or (v.get("title") or "").lower()[:24] in t.lower()]
    print(f"   exact matches : {len(exact)}")
    print(f"   substr matches: {len(substr)}")
    for k in (exact + substr)[:5]:
        print(f"     -> {k[:110]}")
    print()

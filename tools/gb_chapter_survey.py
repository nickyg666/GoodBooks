
#!/usr/bin/env python3
"""Count how common '1-chapter' extraction is across the real library.
Fixes vs the previous version: root join is ROOT + tail (split on "::").
"""
import json, random, subprocess, sys
from pathlib import Path

sys.path.insert(0, "/usr/local/bin/GoodBooks")
import gb_extract as E

ROOT = "/mnt/8tbdas/GoodBooks/"
OUT = Path("/tmp/gb_onechapter_report.json")
meta = json.load(open("/usr/local/bin/GoodBooks/data/library_metadata.json"))
cands = [k for k, v in meta.items()
         if k.split("::",1)[-1].lower().endswith((".mobi",".azw3",".azw",".epub"))]
random.seed(11)
random.shuffle(cands)
cands = cands[:40]

results = []
for k in cands:
    p = Path(ROOT + k.split("::",1)[1])
    if not p.exists():
        continue
    try:
        b = E.read_book(p)
        results.append({"key": k, "n_chapters": len(b.chapters),
                        "title": (b.title or "")[:50]})
        flag = "  <-- ONE CHAPTER" if len(b.chapters) <= 1 else ""
        print(f"  {len(b.chapters):>3} ch  {(b.title or '')[:44]}{flag}")
    except Exception as e:
        results.append({"key": k, "error": f"{type(e).__name__}: {str(e)[:80]}"})
        print(f"  ERR      {p.name[:60]}: {type(e).__name__}")
    sys.stdout.flush()

OUT.write_text(json.dumps(results, indent=1))
ones = [r for r in results if r.get("n_chapters", 0) <= 1]
ok   = [r for r in results if r.get("n_chapters", 0) > 1]
errs = [r for r in results if "error" in r]
print(f"\n  sample={len(results)}  one-or-fewer={len(ones)}  multi={len(ok)}  errors={len(errs)}")

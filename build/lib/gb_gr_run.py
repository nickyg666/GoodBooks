"""Run the Goodreads resolver across the remaining mangled titles.

Same safety contract as the OpenLibrary run: preview by default, invariants
asserted before writing, resume from a state file, and stop the service
first (it rewrites library_metadata.json within ~8s and would clobber us).

Goodreads is rate-limited, so this is slower per item than OpenLibrary but
resolves titles OpenLibrary does not have.
"""
import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path("/usr/local/bin/GoodBooks")
sys.path.insert(0, str(BASE))

import gb_goodreads as gr
import gb_match as M
import gb_openlib as ol

DATA = BASE / "data" / "library_metadata.json"
STATE = Path("/tmp/gb_gr_state.json")

ap = argparse.ArgumentParser()
ap.add_argument("--offset", type=int, default=0)
ap.add_argument("--count", type=int, default=60)
ap.add_argument("--delay", type=float, default=1.4)
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

meta = json.loads(DATA.read_text(encoding="utf-8"))

if STATE.exists():
    todo = json.loads(STATE.read_text())["todo"]
else:
    lst = sorted(k for k, v in meta.items()
                 if isinstance(v, dict) and (v.get("title") or "").strip()
                 and not ol.looks_clean_title(v["title"]))
    STATE.write_text(json.dumps({"todo": lst}))
    todo = lst
    print(f"candidate list: {len(lst)}")

mine = todo[args.offset: args.offset + args.count]
print(f"batch {args.offset}..{args.offset + len(mine)} of {len(todo)}")

fixes = {}
for n, k in enumerate(mine, 1):
    v = meta.get(k)
    if not isinstance(v, dict):
        continue
    title = (v.get("title") or "").strip()
    author = (v.get("author") or "").strip()
    cand = gr.resolve(title, author)
    if not cand and not author:
        cand = gr.resolve(title)
    if cand and cand.get("title"):
        fixes[k] = cand["title"]
    if n % 10 == 0:
        print(f"  ...{n}/{len(mine)} resolved={len(fixes)}", flush=True)
    time.sleep(args.delay)

print(f"resolved: {len(fixes)} / {len(mine)}")

# invariants
bad = 0
applied = 0
for k, nt in fixes.items():
    v = meta.get(k)
    if not isinstance(v, dict) or not nt.strip():
        bad += 1
        continue
    old = (v.get("title") or "").strip()
    # the new title must actually be cleaner than what it replaces
    if ol.looks_clean_title(nt) and not ol.looks_clean_title(old):
        v["title"] = nt
        applied += 1
print(f"applied {applied}, rejected {bad}")
if bad:
    print("ABORT")
    raise SystemExit(1)
if not args.apply or not applied:
    print("PREVIEW" if not args.apply else "nothing to write")
    raise SystemExit(0)

backup = DATA.with_suffix(".json.pre-goodreads-20260929")
if not backup.exists():
    backup.write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    print("backup ->", backup.name)
DATA.write_text(json.dumps(meta, indent=2), encoding="utf-8")
print("written; next offset:", args.offset + len(mine))

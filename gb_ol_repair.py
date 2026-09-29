"""Repair across ALL mangled entries, using a STABLE key order.

The earlier batch loop recomputed `needs` from the live data on every
invocation, so as entries were fixed the list shrank and every offset
skipped a slice of what it should have processed. That is why 10 passes
"wrote" and yet almost nothing changed.

Fix: take the candidate list ONCE, in a fixed order, and record progress
in a state file so a re-run resumes rather than re-deriving.
"""
import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path("/usr/local/bin/GoodBooks")
sys.path.insert(0, str(BASE))
import gb_openlib as ol

DATA = BASE / "data" / "library_metadata.json"
COVERS = BASE / "data" / "covers"
STATE = Path("/tmp/gb_ol_state.json")

ap = argparse.ArgumentParser()
ap.add_argument("--offset", type=int, default=0)
ap.add_argument("--limit", type=int, default=200)
ap.add_argument("--delay", type=float, default=0.2)
ap.add_argument("--covers", action="store_true")
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

meta = json.loads(DATA.read_text(encoding="utf-8"))


def words(t):
    return {w for w in ol._norm(t).split() if w}


# build (or resume) a STABLE candidate list
if STATE.exists():
    st = json.loads(STATE.read_text())
    todo = st["todo"][args.offset: args.offset + args.limit]
    print(f"resuming: {len(st['todo'])} total candidates, "
          f"taking {len(todo)} from {args.offset}")
else:
    todo_all = []
    for k, v in meta.items():
        if not isinstance(v, dict):
            continue
        t = (v.get("title") or "").strip()
        if t and not ol.looks_clean_title(t):
            todo_all.append(k)
    todo_all.sort()                      # stable order
    STATE.write_text(json.dumps({"todo": todo_all}))
    todo = todo_all[args.offset: args.offset + args.limit]
    print(f"new candidate list: {len(todo_all)}, taking {len(todo)} "
          f"from {args.offset}")

print(f"entries in file: {len(meta)}")

ct = cc = 0
unresolved = 0
for n, k in enumerate(todo, 1):
    v = meta.get(k)
    if not isinstance(v, dict):
        continue
    t = (v.get("title") or "").strip()
    a = (v.get("author") or "").strip()
    res = ol.fetch_by_isbn(str(v.get("isbn") or "")) or ol.fetch_by_search(t, a)
    if not res:
        unresolved += 1
        time.sleep(args.delay)
        continue

    nt = (res.get("title") or "").strip()
    if nt and not ol.looks_clean_title(t):
        ow, nw = words(t), words(nt)
        if ow and nw and len(ow & nw) / max(len(ow | nw), 1) >= 0.34 \
                and len(ow - nw) / max(len(ow), 1) <= 0.4:
            meta[k]["_nt"] = nt
            ct += 1

    if args.covers and not (v.get("cover") or "").strip():
        p = ol.fetch_cover_bytes(res.get("cover") or "", COVERS, k)
        if p:
            meta[k]["_nc"] = f"data/covers/{p.name}"
            cc += 1

    time.sleep(args.delay)
    if n % 50 == 0:
        print(f"  ...{n}/{len(todo)} titles={ct} covers={cc}")

bad_t = bad_c = 0
for k, v in meta.items():
    if not isinstance(v, dict):
        continue
    nt = v.pop("_nt", None)
    nc = v.pop("_nc", None)
    if nt is not None:
        if not nt.strip():
            bad_t += 1
        else:
            v["title"] = nt
    if nc is not None:
        if not nc.strip() or not (BASE / nc).exists():
            bad_c += 1
        else:
            v["cover"] = nc

print(f"\ntitles={ct} covers={cc} unresolved={unresolved}")
print(f"empty={bad_t} badcovers={bad_c}")
if bad_t or bad_c:
    print("ABORT")
    raise SystemExit(1)
if not args.apply:
    print("PREVIEW")
    raise SystemExit(0)
DATA.write_text(json.dumps(meta, indent=2), encoding="utf-8")
print("written; next offset:", args.offset + len(todo))

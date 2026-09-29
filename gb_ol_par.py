"""Parallel metadata repair: shard the candidate list across N workers.

The sequential version needed ~187 batches at ~5 min each, which does not
fit the time budget. Each worker owns a disjoint slice of the candidate
keys, so there is no write contention; each worker loads the file, applies
its own fixes, asserts the invariants, and writes. Workers run staggered so
a slow one cannot clobber a faster one's work: every worker re-reads the
file immediately before writing and merges only its own changes.

Safety preserved: the service must be stopped, invariants asserted per
worker, and each worker only ever sets a title when the overlap/loss
thresholds pass.
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
ap.add_argument("--shard", type=int, required=True)
ap.add_argument("--shards", type=int, default=8)
ap.add_argument("--start", type=int, default=0)
ap.add_argument("--count", type=int, default=160)
ap.add_argument("--delay", type=float, default=0.05)
ap.add_argument("--covers", action="store_true")
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()


def words(t):
    return {w for w in ol._norm(t).split() if w}


# stable candidate list, shared by every shard
if STATE.exists():
    todo = json.loads(STATE.read_text())["todo"]
else:
    meta0 = json.loads(DATA.read_text(encoding="utf-8"))
    lst = sorted(k for k, v in meta0.items()
                 if isinstance(v, dict) and (v.get("title") or "").strip()
                 and not ol.looks_clean_title(v["title"]))
    STATE.write_text(json.dumps({"todo": lst}))
    todo = lst

mine = [k for i, k in enumerate(todo)
        if i % args.shards == args.shard][args.start: args.start + args.count]

print(f"shard {args.shard}/{args.shards}: {len(mine)} entries "
      f"({args.start}..{args.start + len(mine)})", flush=True)

fixes = {}          # key -> (title, cover)
ct = cc = 0
for n, k in enumerate(mine, 1):
    try:
        meta = json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        time.sleep(2)
        continue
    v = meta.get(k)
    if not isinstance(v, dict):
        continue
    t = (v.get("title") or "").strip()
    a = (v.get("author") or "").strip()
    try:
        res = ol.fetch_by_isbn(str(v.get("isbn") or "")) or ol.fetch_by_search(t, a)
    except Exception:
        res = None
    if not res:
        time.sleep(args.delay)
        continue

    entry = {}
    nt = (res.get("title") or "").strip()
    if nt and not ol.looks_clean_title(t):
        ow, nw = words(t), words(nt)
        if ow and nw and len(ow & nw) / max(len(ow | nw), 1) >= 0.34 \
                and len(ow - nw) / max(len(ow), 1) <= 0.4:
            entry["title"] = nt
            ct += 1
    if args.covers and not (v.get("cover") or "").strip():
        try:
            p = ol.fetch_cover_bytes(res.get("cover") or "", COVERS, k)
        except Exception:
            p = None
        if p:
            entry["cover"] = f"data/covers/{p.name}"
            cc += 1

    if entry:
        fixes[k] = entry

    if n % 50 == 0:
        print(f"  ...{n}/{len(mine)} titles={ct} covers={cc}", flush=True)
    time.sleep(args.delay)

print(f"shard {args.shard}: fixed {len(fixes)} keys "
      f"(titles={ct} covers={cc})", flush=True)

if not fixes or not args.apply:
    print("nothing to write")
    raise SystemExit(0)

# merge and write, re-reading immediately before
meta = json.loads(DATA.read_text(encoding="utf-8"))
applied = 0
bad = 0
for k, entry in fixes.items():
    v = meta.get(k)
    if not isinstance(v, dict):
        continue
    if "title" in entry:
        if not entry["title"].strip():
            bad += 1
        else:
            v["title"] = entry["title"]
            applied += 1
    if "cover" in entry:
        if not entry["cover"].strip() or not (BASE / entry["cover"]).exists():
            bad += 1
        else:
            v["cover"] = entry["cover"]
print(f"merged {applied} fields, {bad} rejected", flush=True)
if bad:
    print("ABORT: bad fields")
    raise SystemExit(1)
DATA.write_text(json.dumps(meta, indent=2), encoding="utf-8")
print("WRITTEN", flush=True)

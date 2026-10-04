"""Write the derived book_key into library metadata. Additive only.

DESIGN CONSTRAINTS, all deliberate:

  * Nothing is REWRITTEN. title, author, id, cover, description, rating and
    genres are left exactly as they are. book_key is a NEW field.
  * book_key is DERIVED and NON-AUTHORITATIVE. Every code path that matters
    (build_library_entries, the audiobook pipeline, rename, delete) keeps
    using the existing id. If book_key were ever deleted the library would
    behave exactly as it does today.
  * No record is merged, deduplicated or deleted.
  * Atomic write, same as every other metadata write in this project, and the
    service is STOPPED first so its in-memory cache cannot clobber the file.
    That clobber is how 4,869 records became 37 earlier today.

It is written into the live file because that is what makes it usable by an
aggregator without a code change on their side. A dry-run prints the
proposed diff and writes nothing.
"""
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/local/bin/GoodBooks")
ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META_PATH = ROOT / "data" / "library_metadata.json"

DRY = "--apply" not in sys.argv

# Load helpers WITHOUT importing app.py: that boots a second service
# instance with its own metadata cache, and it has destroyed live library
# data three times (1,074 records; 4,869 -> 73; 4,837 -> 37).
import importlib.util
import re


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BK = _load(ROOT / "gb_bookkey.py", "gb_bookkey")
GA = _load(ROOT / "gb_authors.py", "gb_authors")

_app_src = (ROOT / "app.py").read_text()
_ns = {"re": re}
exec(compile(re.search(r"def display_title\(.*?\n(?=def )",
                       _app_src, re.S).group(0), "slice", "exec"), _ns)
display_title = _ns["display_title"]


def real_path(key):
    return LIB / key.split("::", 1)[-1]


print("=" * 74)
print(f"{'DRY RUN' if DRY else 'APPLYING'} -- derived book_key")
print("=" * 74)

print("  service must be stopped before touching the file:")
state = subprocess.run(["systemctl", "is-active", "GoodBooks"],
                       capture_output=True, text=True).stdout.strip()
print(f"    GoodBooks.service = {state}")
if DRY and state == "active":
    print("    (dry run reads only, so this is fine)")

meta = json.loads(META_PATH.read_text())
before_count = len(meta)

changed = same = 0
by_key = {}
samples = []
for k, v in meta.items():
    if not isinstance(v, dict):
        continue
    key = BK.book_key(v.get("title", ""), v.get("author", ""),
                      parse_authors=GA.parse_authors,
                      display_title=display_title)
    by_key.setdefault(key, []).append(k)
    if v.get("book_key") == key:
        same += 1
        continue
    changed += 1
    if len(samples) < 6:
        samples.append((k, v.get("title", "")[:44], key[:56]))

print()
print(f"  records            : {before_count}")
print(f"  book_key unchanged : {same}")
print(f"  book_key to write  : {changed}")
print()
print("  sample:")
for k, t, key in samples:
    print(f"    {t!r}")
    print(f"      -> {key!r}")

multi = {k: v for k, v in by_key.items() if len(v) > 1}
print()
print(f"  distinct book_keys : {len(by_key)}")
print(f"  keys with >1 file  : {len(multi)} ({sum(len(v) for v in multi.values())} files)")
print()
print("  largest clusters:")
for key, ks in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:6]:
    fmts = [real_path(k).suffix.lower() for k in ks]
    print(f"    x{len(ks):<3} {BK.label_for(key)[:58]:60} {fmts}")

if DRY:
    print()
    print("  DRY RUN: nothing written. Re-run with --apply after stopping")
    print("  the service:")
    print("    sudo systemctl stop GoodBooks && python3 gb_bookkey_apply.py --apply")
    raise SystemExit(0)

# ---- apply ------------------------------------------------------------
print()
print("  backing up ...")
bak = META_PATH.with_name(f"library_metadata.json.pre-bookkey-{time.strftime('%Y%m%d%H%M%S')}")
import shutil
shutil.copy2(META_PATH, bak)
print(f"    {bak.name}")

before_fields = set()
for v in meta.values():
    if isinstance(v, dict):
        before_fields |= set(v.keys())

for k, v in meta.items():
    if not isinstance(v, dict):
        continue
    v["book_key"] = BK.book_key(v.get("title", ""), v.get("author", ""),
                                parse_authors=GA.parse_authors,
                                display_title=display_title)

payload = json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True)
tmp = META_PATH.with_suffix(".json.bookkey-tmp")
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(payload)
    fh.flush()
    os.fsync(fh.fileno())
os.replace(tmp, META_PATH)
dirfd = os.open(str(META_PATH.parent), os.O_RDONLY)
try:
    os.fsync(dirfd)
finally:
    os.close(dirfd)

after = json.loads(META_PATH.read_text())
after_fields = set()
for v in after.values():
    if isinstance(v, dict):
        after_fields |= set(v.keys())

print()
print("=== verification ===")
print(f"  records before/after : {before_count} / {len(after)}")
print(f"  fields added         : {sorted(after_fields - before_fields)}")
print(f"  fields removed       : {sorted(before_fields - after_fields) or 'none'}")

# nothing but book_key may differ, per record
old = json.loads(bak.read_text())
bad = 0
for k in old:
    o, a = old[k], after.get(k, {})
    diff = {f for f in set(o) | set(a) if o.get(f) != a.get(f)}
    if diff - {"book_key"}:
        bad += 1
        if bad <= 3:
            print(f"    UNEXPECTED change on {k[-40:]!r}: {sorted(diff)}")
print(f"  records with a change other than book_key: {bad}")
print()
print("RESULT:", "APPLIED CLEANLY" if bad == 0 and len(after) == before_count
      else "PROBLEM")

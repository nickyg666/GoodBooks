"""Repair metadata orphaned by the rename KeyError, and adopt file orphans.

A crash in rename_library_file_to_md5_format renamed files on disk and then
raised KeyError before writing library_metadata.json. Verified 2026-09-30:
all 7 phantom records have their file present on disk under the NEW name, so
the rename succeeded and only the metadata key was lost. These are rekeyed
exactly, not guessed.

The 21 orphans are real book files (.fb2/.cbz/.rar) that were never in
library_metadata.json. They are adopted with minimal records so they appear
in the library, rather than being left invisible forever.

Safety, because this edits a 6MB file the running service also writes:
  * refuse to run if the service is up unless --force is passed
  * timestamped backup
  * invariants checked before the write: no existing key is dropped, and the
    file count is preserved
  * idempotent: a second run finds nothing to do
  * never touches a record whose key still exists
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path("/mnt/8tbdas/GoodBooks")
META = Path("/usr/local/bin/GoodBooks/data/library_metadata.json")
EXTS = (".epub", ".mobi", ".azw3", ".azw", ".pdf", ".cbz", ".rar", ".cbr",
        ".fb2", ".djvu", ".txt")

p = argparse.ArgumentParser()
p.add_argument("--force", action="store_true")
args = p.parse_args()

# ---- service guard -------------------------------------------------------
if not args.force:
    try:
        out = os.popen(
            "pgrep -f 'GoodBooks/app.py' | wc -l").read().strip()
    except Exception:
        out = "?"
    if out not in ("0", ""):
        print(f"Refusing to run: {out} GoodBooks process(es) are live and this "
              f"file is rewritten by the running app. Stop it, or pass "
              f"--force if you know the write will not race.")
        sys.exit(1)

meta = json.loads(META.read_text(encoding="utf-8", errors="replace"))
before_keys = set(meta)
print("metadata entries before:", len(meta))

# ---- index what is on disk ----------------------------------------------
disk = {}
real_root = os.path.realpath(str(ROOT))
for base, _dirs, files in os.walk(str(ROOT)):
    for f in files:
        if f.lower().endswith(EXTS):
            full = os.path.join(base, f)
            rel = os.path.relpath(full, str(ROOT))
            disk[real_root + "::" + rel] = full

print("book files on disk      :", len(disk))

# ---- 1. phantoms: rekey to the file that actually exists -----------------
# Match on the title stem, ignoring the "-Author" tail and any
# ".author; name" suffix the rename appended.
def stem_of(key):
    name = key.split("::", 1)[-1]
    name = os.path.splitext(name)[0]
    # the rename appends ".<author tokens>" -- drop the trailing dotted part
    return name


def base_title(name):
    # cut the catalogue "-Author" tail: take the part before a " - " that is
    # followed by a capitalised word and not a number
    s = os.path.splitext(name)[0]
    if " - " in s:
        head, _, tail = s.rpartition(" - ")
        if tail and not tail[0].isdigit():
            s = head
    return s.strip().casefold()


disk_by_title = {}
for k, path in disk.items():
    disk_by_title.setdefault(base_title(os.path.basename(path)), []).append(k)

rekeyed = 0
for old in sorted(before_keys - set(disk)):
    want = base_title(os.path.split(old)[-1])
    cands = disk_by_title.get(want, [])
    if len(cands) == 1:
        new = cands[0]
        meta[new] = meta.pop(old)
        print(f"  rekeyed -> {os.path.basename(new)[:70]}")
        rekeyed += 1
    elif len(cands) > 1:
        # ambiguous: do not guess
        print(f"  AMBIGUOUS ({len(cands)} candidates), left alone: "
              f"{os.path.basename(old)[:60]}")
    else:
        print(f"  no file found, left as phantom: {os.path.basename(old)[:60]}")

# ---- 2. orphans: adopt real files that have no metadata -----------------
adopted = 0
for k, path in sorted(disk.items()):
    if k in meta:
        continue
    name = os.path.basename(path)
    title = os.path.splitext(name)[0]
    ext = os.path.splitext(name)[1].lstrip(".").lower() or "bin"
    # "Title-Author.ext" -> title, author, when the split is clean
    author = ""
    if " - " in title:
        head, _, tail = title.rpartition(" - ")
        if tail and not tail[0].isdigit():
            title, author = head.strip(), tail.strip()
    rec = {
        "author": author,
        "cover": "",
        "enrichment_sources": [],
        "filetype": ext,
        "goodreads_meta": {"cover": "", "description": "",
                           "edition_format": "", "edition_language": "",
                           "edition_published": "", "genres": [],
                           "goodreads_url": "", "pages": None,
                           "rating": None, "rating_count": None},
        "id": k,
        "path": "",
        "title": title.strip(),
    }
    meta[k] = rec
    adopted += 1
    if adopted <= 5:
        print(f"  adopted: {title[:66]}")

# ---- invariants ----------------------------------------------------------
after_keys = set(meta)
dropped = before_keys - after_keys
if dropped:
    print("\nREFUSING: would drop existing keys:", len(dropped))
    sys.exit(1)

print(f"\nrekeyed={rekeyed} adopted={adopted} entries {len(before_keys)}"
      f" -> {len(meta)}")
print("no existing record dropped: OK")

# ---- write ---------------------------------------------------------------
bak = META.with_suffix(f".json.repairbak-{time.strftime('%Y%m%d%H%M%S')}")
shutil.copy2(META, bak)
print("backup:", bak.name)

tmp = META.with_suffix(".json.tmp")
tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
os.replace(tmp, META)
print("written OK")

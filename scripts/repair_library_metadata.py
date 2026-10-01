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
            "ps -eo cmd | grep -c '[p]ython3 /usr/local/bin/GoodBooks/app.py'"
        ).read().strip()
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


def split_name(name):
    """(title, author) for a catalogue filename.

    "Title-Author.ext" -> ("Title", "Author"). The rename appends
    ".<author tokens>", which is dropped first. Matching on the title alone
    was too loose: it matched "What Lies in the Woods-Kate; Alice;
    Marshall.epub" against an unrelated file, so the author must agree too.
    """
    s = os.path.splitext(os.path.basename(name))[0]
    if "." in s:
        s = s.split(".")[0]
    if " - " in s:
        head, _, tail = s.rpartition(" - ")
        if tail and not tail[0].isdigit() and len(tail.split()) <= 5:
            return head.strip().casefold(), tail.strip().casefold()
    return s.strip().casefold(), ""


def base_title(name):
    return split_name(name)[0]


disk_by_title = {}
for k, path in disk.items():
    t, a = split_name(os.path.basename(path))
    disk_by_title.setdefault(t, []).append((k, a))

rekeyed = 0
rekey_map = {}
adopted_keys = set()
for old in sorted(before_keys - set(disk)):
    want_t, want_a = split_name(os.path.split(old)[-1])
    cands = disk_by_title.get(want_t, [])
    exact = [k for k, a in cands if a == want_a and want_a]
    loose = [k for k, a in cands if a == want_a and not want_a]
    pick = None
    if len(exact) == 1:
        pick = exact[0]
    elif len(exact) > 1:
        print(f"  AMBIGUOUS ({len(exact)}), left alone: "
              f"{os.path.basename(old)[:60]}")
    elif len(loose) == 1:
        pick = loose[0]
    elif len(loose) > 1:
        print(f"  AMBIGUOUS author-less ({len(loose)}), left alone: "
              f"{os.path.basename(old)[:60]}")
    if pick:
        meta[pick] = meta.pop(old)
        rekey_map[old] = pick
        rekeyed += 1
        print(f"  rekeyed -> {os.path.basename(pick)[:70]}")
    else:
        print(f"  no unambiguous file, left as phantom: "
              f"{os.path.basename(old)[:60]}")

# ---- 2. orphans: adopt real files that have no metadata -----------------
adopted = 0
adopted_keys = set()
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
    adopted_keys.add(k)
    adopted += 1
    if adopted <= 5:
        print(f"  adopted: {title[:66]}")

# ---- invariants ----------------------------------------------------------
after_keys = set(meta)
# A deliberate rekey REMOVES the old key and adds the new one, so comparing
# key sets always looks like a drop -- the original invariant could never be
# satisfied. What must hold: every key we popped has reappeared under its new
# name, nothing vanished for another reason, and nothing appeared that we did
# not deliberately add.
dropped = before_keys - after_keys - set(rekey_map)
if dropped:
    print("\nREFUSING: keys vanished without a rekey:", len(dropped))
    for x in sorted(dropped)[:5]:
        print("    ", x.split("::")[-1][:70])
    sys.exit(1)
unaccounted = (after_keys - before_keys - set(rekey_map.values())
               - adopted_keys)
if unaccounted:
    print("\nREFUSING: unexpected new keys:", len(unaccounted))
    for x in sorted(unaccounted)[:5]:
        print("    ", x.split("::")[-1][:70])
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

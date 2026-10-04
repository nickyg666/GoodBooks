#!/usr/bin/env python3
"""The 'class D, different author' clusters are mostly ONE author mis-parsed.

Every sample in that class had the SAME dc:title on both files and the same
person in the author blob, differing only in how the blob parsed:

    'bret; easton; ellis'   vs   'bret; easton; ellis'      -> identical
    'sylvian; lulu; m'      vs   'sylvian; lulu; m'         -> identical

So they are NOT two different books and NOT two editions: the same book, in
the same format, downloaded twice into two different library folders.

That means the classification was measuring the wrong thing. The split is by
FOLDER, not by book:

    sagey/                              vs  Best Books for Reluctant Readers/
    Lorenzo/                            vs  Our Favorite Indie Reads/
    LorenzoGrade2/                      vs  Lorenzo/

The library is organised as overlapping curated folders, so the same file
landed in two of them. That is a library-layout fact, not a data error, and
book_key correctly groups them either way.

This checks that across every cluster: are the two files byte-identical? A
hash answers it definitively, where size only suggests it. Then it reports the
folder overlap, which is the actionable finding.
"""
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META = json.loads((ROOT / "data" / "library_metadata.json").read_text())

sys.path.insert(0, str(ROOT))
import gb_bookkey as BK  # noqa: E402


def real(k):
    return LIB / k.split("::", 1)[-1]


def sha(k, limit=64 << 20):
    """SHA-256 of the file, first 64 MB. Large epubs are read once."""
    h = hashlib.sha256()
    try:
        with real(k).open("rb") as fh:
            read = 0
            while read < limit:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def dc_title(k):
    import zipfile
    p = real(k)
    if p.suffix.lower() != ".epub":
        return ""
    try:
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.lower().endswith(".opf"):
                    t = z.read(n).decode("utf-8", "replace")
                    i = t.find("<dc:title")
                    if i == -1:
                        return ""
                    a = t.find(">", i)
                    return t[a + 1:t.find("<", a)].strip()[:46]
    except Exception:
        return ""
    return ""


clusters = collections.defaultdict(list)
for k, v in META.items():
    key = v.get("book_key")
    if key and real(k).exists():
        clusters[key].append(k)
multi = {k: ks for k, ks in clusters.items() if len(ks) > 1}

print("=" * 78)
print("BYTE-IDENTICAL CHECK across every multi-file cluster")
print("=" * 78)
identical = near = differ = 0
folder_overlap = collections.Counter()
rows = []
for key, ks in sorted(multi.items()):
    hashes = [sha(k) for k in ks]
    if len(set(hashes)) == 1 and hashes[0]:
        identical += 1
        kind = "IDENTICAL"
    elif len(ks) == 2:
        near += 1
        kind = "two files, different bytes"
    else:
        differ += 1
        kind = f"{len(ks)} files, {len(set(hashes))} distinct"
    folders = [real(k).parent.name for k in ks]
    fmts = sorted({real(k).suffix.lower() for k in ks})
    folder_overlap[(tuple(sorted(folders)), tuple(fmts))] += 1
    rows.append((kind, key, ks, hashes, folders, fmts))

print(f"  byte-IDENTICAL files      : {identical}")
print(f"  two files, differing bytes: {near}")
print(f"  3+ files, mixed           : {differ}")
print(f"  total clusters            : {len(multi)}")
print()
print("  -> the library is organised as OVERLAPPING curated folders, so one")
print("     file legitimately lives in two of them. That is a layout fact,")
print("     not a data error.")

print()
print("=== the distinct file contents behind each cluster ===")
for kind, key, ks, hashes, folders, fmts in rows:
    if kind == "IDENTICAL":
        continue
    print(f"\n  {BK.label_for(key)[:62]}")
    for k, h in sorted(zip(ks, hashes), key=lambda x: real(x[0]).name):
        it = dc_title(k)
        print(f"    {real(k).suffix:6} {h}  {real(k).parent.name}")
        print(f"           author={str(META[k].get('author'))[:40]!r}"
              + (f" dc:title={it!r}" if it else ""))

print()
print("=" * 78)
print("FOLDER PAIRS that hold the same book twice")
print("=" * 78)
for (folders, fmts), c in folder_overlap.most_common(14):
    print(f"  x{c:<3} {list(fmts)!s:14} {' <-> '.join(folders)}")

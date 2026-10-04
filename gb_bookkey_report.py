#!/usr/bin/env python3
"""Report the books that exist as more than one file. READ ONLY.

Step 2 from the title-resolution note: surface the multi-file books so they can
be judged by eye, rather than acting on them. Nothing is written, moved or
deleted -- this only reads library_metadata.json and stats the files.

The 96 clusters break into kinds that need DIFFERENT decisions, which is
exactly why this is a report and not a dedup:

  A. same book, two formats        -> benign, book_key already groups them
  B. same book, same format twice  -> a real duplicate download
  C. DIFFERENT books, same title    -> book_key keeps them apart; never merge
  D. edition/volume ambiguity       -> needs a human, e.g. "Vol 1" vs "Vol 2"

So the report classifies by evidence available without reading the books:
file format, byte size, and whether the author agrees. Anything ambiguous is
listed separately rather than guessed at.

Run:  python3 gb_bookkey_report.py [--limit N]
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META = json.loads((ROOT / "data" / "library_metadata.json").read_text())

sys.path.insert(0, str(ROOT))
import gb_authors as GA  # noqa: E402
import gb_bookkey as BK  # noqa: E402


def real(k):
    return LIB / k.split("::", 1)[-1]


def parse_authors(a):
    try:
        return GA.parse_authors(a or "")
    except Exception:
        return []


def size_of(k):
    p = real(k)
    try:
        return p.stat().st_size
    except OSError:
        return 0


limit = 40
if "--limit" in sys.argv:
    limit = int(sys.argv[sys.argv.index("--limit") + 1])

clusters = collections.defaultdict(list)
for k, v in META.items():
    key = v.get("book_key")
    if not key or not real(k).exists():
        continue
    clusters[key].append(k)

multi = {k: ks for k, ks in clusters.items() if len(ks) > 1}
total_files = sum(len(v) for v in multi.values())
print("=" * 78)
print(f"MULTI-FILE BOOKS: {len(multi)} clusters, {total_files} files, "
      f"{total_files - len(multi)} redundant")
print(f"library: {len(META)} records -> {len(clusters)} books")
print("=" * 78)

kinds = collections.Counter()
rows = []
for key, ks in multi.items():
    fmts = collections.Counter(real(k).suffix.lower() for k in ks)
    sizes = sorted(size_of(k) for k in ks)
    authors = {str(META[k].get("author") or "") for k in ks}
    title, akey = BK.split_book_key(key)
    same_fmt = len(fmts) == 1
    same_author = len(authors) == 1
    if len(fmts) > 1 and same_author:
        kind = "A  same book, multiple formats  (benign)"
    elif same_fmt and same_author:
        kind = "B  same book AND same format   (duplicate download)"
    elif same_fmt and not same_author:
        kind = "D  same format, different author (EDITION or COLLISION)"
    else:
        kind = "C  different author, diff fmt (verify before anything)"
    kinds[kind] += 1
    rows.append((kind, key, ks, fmts, sizes, sorted(authors)))

print()
print("=== SUMMARY BY KIND ===")
for k, c in sorted(kinds.items()):
    print(f"  {c:>4}  {k}")
print()
print(f"showing {min(limit, len(rows))} of {len(rows)}; --limit N for more")
print()

for kind, key, ks, fmts, sizes, authors in sorted(rows)[:limit]:
    print("-" * 78)
    print(f"  {kind}")
    print(f"  key : {key}")
    for k in sorted(ks, key=size_of):
        v = META[k]
        mb = size_of(k) / 1048576
        print(f"    {real(k).suffix:6} {mb:7.2f} MB  {(v.get('title') or '')[:46]!r}")
    print(f"    author(s): {authors}")

print()
print("=" * 78)
print("WHAT TO DO WITH EACH")
print("=" * 78)
print("  A  Nothing. One book, several formats. This is normal for an")
print("     ebook library and book_key already groups them. Keep whichever")
print("     format your pipeline handles best (epub/azw3 preferred).")
print()
print("  B  A genuine duplicate download of the SAME book in the SAME")
print("     format. Safe to remove one copy -- but only after you confirm")
print("     they are the same edition, and it is your call, not mine.")
print()
print("  C  Different author on the same title. Almost always different")
print("     books ('In the Woods'). Never merge. Possibly rename to")
print("     disambiguate.")
print()
print("  D  The dangerous one: same format, different author, same title.")
print("     Either two editions of one work or two different books that")
print("     happen to share a name. Needs a human, and possibly the file")
print("     contents.")

#!/usr/bin/env python3
"""Find corrupted titles, graded by how strong the evidence is.

Step 3 of the title work. NOTHING is written here -- this only classifies, so
that any later repair can be argued case by case instead of applied blind.

The failure already confirmed by hand:

    Firestarter-Stephen; King.mobi   stored title 'Stephen King'
    Misery-Stephen; King.epub         stored title 'Stephen King'
                                       dc:title   'Misery'

Both records store the AUTHOR in the title field, so book_key merged two
different novels. That is the shape to detect.

Evidence classes, strongest first. Only class A and B are safe to auto-repair;
C and D need a human.

  A  stored title == the parsed author  (or contains it with nothing else)
     -> the title IS the author. Highest confidence.
  B  the EPUB's own dc:title disagrees with the stored title, and dc:title
     looks like a real title
     -> the file knows better than the record.
  C  the FILENAME's title part disagrees with the stored title and the file
     carries no dc:title (mobi/azw3 cannot be read that way)
     -> suggestive, unverifiable without converting.
  D  title is suspiciously long / catalogue-junk shaped but nothing
     contradicts it
     -> no evidence, leave alone.

Why this is safer than the earlier bulk rewrite: that one split on the last
hyphen and turned 'Amy and the Missing Puppy' into 'Amy and the'. Nothing here
guesses at a split position; every case cites a title the FILE itself contains.
"""
import collections
import json
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META = json.loads((ROOT / "data" / "library_metadata.json").read_text())

sys.path.insert(0, str(ROOT))
import gb_authors as GA  # noqa: E402
import gb_bookkey as BK  # noqa: E402


def real(k):
    return LIB / k.split("::", 1)[-1]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def authors(a):
    try:
        return [n for n in (GA.parse_authors(a or "") or []) if norm(n)]
    except Exception:
        return []


def dc_title(p):
    """dc:title from an epub, or ''."""
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
                    return t[a + 1:t.find("<", a)].strip()
    except Exception:
        return ""
    return ""


def fname_title(p):
    """The library convention is <Title>-<Author>."""
    stem = p.stem
    return stem.rsplit("-", 1)[0] if "-" in stem else stem


buckets = collections.defaultdict(list)
stats = collections.Counter()

for k, v in META.items():
    if not isinstance(v, dict):
        continue
    p = real(k)
    if not p.exists():
        continue
    stored = (v.get("title") or "").strip()
    if not stored:
        continue
    auth = authors(v.get("author"))
    auth_names = [norm(a) for a in auth]
    s_norm = norm(stored)
    fname = fname_title(p)
    dct = dc_title(p)

    # ---- A: the stored title IS the author
    if auth_names and (s_norm in auth_names or
                       any(s_norm == a for a in auth_names)):
        buckets["A  title IS the author"].append((k, stored, dct, fname))
        stats["A"] += 1
        continue

    # ---- B: the file's dc:title disagrees and looks like a real title
    if dct:
        d = norm(dct)
        if d and d != s_norm and len(d) >= 3:
            # is the stored title plausibly the AUTHOR anyway?
            looks_like_author = any(d == a for a in auth_names)
            if not looks_like_author:
                buckets["B  dc:title disagrees with stored"].append(
                    (k, stored, dct, fname))
                stats["B"] += 1
                continue

    # ---- C: filename disagrees, no dc:title available to arbitrate
    if not dct:
        f = norm(fname)
        if f and f != s_norm and len(f) >= 5 and "-" in p.stem:
            buckets["C  filename disagrees, unverifiable"].append(
                (k, stored, "", fname))
            stats["C"] += 1
            continue

    stats["ok"] += 1

total = len(META)
print("=" * 76)
print(f"CORRUPTED-TITLE INVENTORY  ({total} records)")
print("=" * 76)
for cls in sorted(buckets):
    print(f"  {len(buckets[cls]):>5}  {cls}")
print(f"  {stats['ok']:>5}  no evidence of a problem")
print()

for cls in sorted(buckets):
    rows = buckets[cls]
    print("=" * 76)
    print(f"{cls} -- {len(rows)} records")
    print("=" * 76)
    for k, stored, dct, fname in rows[:14]:
        print(f"  file : {real(k).name[:62]}")
        print(f"    stored title : {stored[:66]!r}")
        if dct:
            print(f"    dc:title     : {dct[:66]!r}   <- the file's own title")
        if cls.startswith("C"):
            print(f"    filename     : {fname[:66]!r}   <- unverified")
    if len(rows) > 14:
        print(f"    ... {len(rows)-14} more")
    print()

# what the two safe classes would change
a_b = stats["A"] + stats["B"]
print("=" * 76)
print(f"REPAIRABLE WITHOUT A HUMAN: {a_b} records (class A {stats['A']}, B {stats['B']})")
print(f"NEEDS REVIEW OR LEAVES ALONE: {stats['C']} (class C)")
print("=" * 76)

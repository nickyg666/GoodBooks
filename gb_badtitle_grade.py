#!/usr/bin/env python3
"""Split class B: is dc:title trustworthy, or is it itself junk?

The first pass put 2,745 records in class B, "dc:title disagrees with the
stored title". Read the output and most of those are GOOD corrections:

    stored 'Anna Hibiscus-Atinuke'          dc:title 'Anna Hibiscus'
    stored 'Babymouse 1 Queen of the World!-Holm, Jennifer L. & Holm, Matthew'
                                               dc:title 'Babymouse 1 Queen of the World!'

but some dc:title values are themselves garbage:

    stored 'American Girls, Molly McIntire, ... 6 Book Complete Collection'
                                               dc:title 'upload'

So "the file disagrees" does not mean "the file is right". Repairing 2,745
titles from dc:title without grading dc:title would replace one corrupted
field with another, which is how the earlier bulk rewrite did damage.

dc:title is only trusted when it looks like a real book title. This grades it:

  GOOD   reads as a title: >=1 word, no junk markers, not a filename echo,
         not identical to the author, reasonable length
  JUNK   'upload', single generic word, a path or extension, repeated-word
         noise, or it merely echoes the stored filename

Records split into:
  B1  stored title is junk AND dc:title is GOOD   -> safe to repair
  B2  stored title is junk, dc:title also junk     -> leave, no evidence
  B3  stored title looks fine, dc:title differs    -> leave, do not touch

Nothing is written.
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


def real(k):
    return LIB / k.split("::", 1)[-1]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def authors(a):
    try:
        return [norm(n) for n in (GA.parse_authors(a or "") or []) if norm(n)]
    except Exception:
        return []


def dc_title(p):
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


JUNK_WORDS = {
    "upload", "uploaded", "untitled", "unknown", "none", "book", "title",
    "document", "file", "final", "new", "scan", "scan1", "scan2", "scan3",
    "oebps", "content", "index", "cover", "default", "temp", "copy",
    "ebook", "epub", "pdf", "adobe", "indesign", "converted", "converted2",
}
EXT_RE = re.compile(r"\.(x?html?|epub|mobi|azw3?|jpg|png|pdf|opf|ncx)$", re.I)


def grade_dctitle(d, stored, auth_names, p):
    """GOOD, or a reason it is junk."""
    d = (d or "").strip()
    if not d:
        return False, "empty"
    if len(d) > 200:
        return False, "implausibly long"
    if EXT_RE.search(d):
        return False, "looks like a filename"
    if "/" in d or "\\" in d:
        return False, "looks like a path"
    low = d.lower().strip()
    if low in JUNK_WORDS:
        return False, f"generic word {low!r}"
    words = norm(d).split()
    if not words:
        return False, "no alphanumeric content"
    if len(words) == 1 and low in JUNK_WORDS:
        return False, "generic word"
    # it must not simply echo the stored title
    if norm(d) == norm(stored):
        return False, "echoes the stored title"
    # nor be the author
    if norm(d) in auth_names:
        return False, "is the author"
    # scraper noise: a word repeated 3+ times
    counts = collections.Counter(words)
    if counts and counts.most_common(1)[0][1] >= 3:
        return False, "repeated-word noise"
    return True, "reads as a title"


def stored_looks_junk(stored, auth_names, p):
    """Is the STORED title itself suspect?"""
    s = (stored or "").strip()
    if not s:
        return True, "empty"
    sn = norm(s)
    if auth_names and sn in auth_names:
        return True, "is the author"
    if len(s) > 160:
        return True, "implausibly long"
    words = sn.split()
    if len(words) > 14:
        return True, f"{len(words)} words"
    c = collections.Counter(words)
    if c and c.most_common(1)[0][1] >= 3:
        return True, "repeated-word noise"
    return False, "looks fine"


B1, B2, B3, A = [], [], [], []
for k, v in META.items():
    if not isinstance(v, dict):
        continue
    p = real(k)
    if not p.exists():
        continue
    stored = (v.get("title") or "").strip()
    if not stored:
        continue
    an = authors(v.get("author"))
    dct = dc_title(p)

    junk_s, why_s = stored_looks_junk(stored, an, p)
    if why_s == "is the author":
        A.append((k, stored, dct))
        continue

    if not dct:
        continue
    if norm(dct) == norm(stored):
        continue

    good_d, why_d = grade_dctitle(dct, stored, an, p)
    if junk_s and good_d:
        B1.append((k, stored, dct, why_s))
    elif junk_s and not good_d:
        B2.append((k, stored, dct, why_d))
    else:
        B3.append((k, stored, dct))

print("=" * 76)
print("GRADED TITLE INVENTORY")
print("=" * 76)
print(f"  A  stored title IS the author        : {len(A)}")
print(f"  B1 stored junk, dc:title GOOD       : {len(B1)}   <- safe to repair")
print(f"  B2 stored junk, dc:title also junk  : {len(B2)}   <- no evidence, leave")
print(f"  B3 stored fine, dc:title differs    : {len(B3)}   <- do not touch")
print()
print(f"  repairable with evidence : {len(B1)}")
print()

print("=" * 76)
print(f"A -- title IS the author ({len(A)}): the file's own title is the fix")
print("=" * 76)
for k, s, d in A[:12]:
    print(f"  {s[:44]!r}")
    if d:
        print(f"      -> dc:title {d[:52]!r}")
    else:
        print(f"      -> no dc:title (not an epub) -- NEEDS THE FILENAME")
print()

print("=" * 76)
print(f"B1 -- safe to repair ({len(B1)})")
print("=" * 76)
for k, s, d, why in B1[:16]:
    print(f"  {s[:56]!r}")
    print(f"      -> {d[:56]!r}   ({why})")
if len(B1) > 16:
    print(f"  ... {len(B1)-16} more")
print()

print("=" * 76)
print(f"B2 -- no usable evidence ({len(B2)}): sample of WHY dc:title was rejected")
print("=" * 76)
reasons = collections.Counter(w for _, _, _, w in B2)
for r, c in reasons.most_common(8):
    print(f"  {c:>5}  {r}")
print()
for k, s, d, why in B2[:6]:
    print(f"  {s[:52]!r}")
    print(f"      dc:title {d[:46]!r} rejected: {why}")

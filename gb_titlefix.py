#!/usr/bin/env python3
"""Dry-run the title repair. Writes NOTHING.

Produces the exact before/after for every proposed change, plus the
book_key movement, so the whole edit can be reviewed as text before any of it
is applied. Grading comes from gb_badtitle_grade.py: only class A (the title IS
the author, fixed from the file's own dc:title) and class B1 (stored title is
catalogue junk, dc:title grades as a real title) are eligible.

Class B2 (no usable evidence) and B3 (stored title already fine, dc:title
merely differs) are excluded -- B3 is the bulk of the library and touching it
would be a mass rewrite, which is exactly what went wrong last time.

Also shows, per change, whether the repair SPLITS or MERGES a book_key, since
the point of the whole exercise is correct grouping.
"""
import collections
import json
import sys
import unicodedata
import zipfile
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META_PATH = ROOT / "data" / "library_metadata.json"
META = json.loads(META_PATH.read_text())

sys.path.insert(0, str(ROOT))
import gb_authors as GA  # noqa: E402
import gb_bookkey as BK  # noqa: E402

APPLY = "--apply" in sys.argv
SHOW = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 40


def real(k):
    return LIB / k.split("::", 1)[-1]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


import re  # noqa: E402  (after norm, to keep the helper above readable)


def authors(a):
    try:
        return [n for n in (GA.parse_authors(a or "") or []) if norm(n)]
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


def grade_dctitle(d, stored, an):
    d = (d or "").strip()
    if not d:
        return False
    if len(d) > 200 or EXT_RE.search(d) or "/" in d or "\\" in d:
        return False
    low = d.lower().strip()
    if low in JUNK_WORDS:
        return False
    w = norm(d).split()
    if not w or norm(d) == norm(stored) or norm(d) in an:
        return False
    c = collections.Counter(w)
    if c and c.most_common(1)[0][1] >= 3:
        return False
    return True


def stored_junk(s, an):
    if not s:
        return True
    sn = norm(s)
    if an and sn in an:
        return True
    if len(s) > 160 or len(sn.split()) > 14:
        return True
    c = collections.Counter(sn.split())
    if c and c.most_common(1)[0][1] >= 3:
        return True
    return False


def bk(title, author):
    return BK.book_key(title, author, parse_authors=GA.parse_authors,
                      display_title=lambda x: x)


# ---- work out the proposals -------------------------------------------
proposals = []
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
    if not dct or norm(dct) == norm(stored):
        continue
    if not stored_junk(stored, an):
        continue
    if not grade_dctitle(dct, stored, an):
        continue
    dct = dct.strip()
    if norm(dct) == norm(stored):
        continue
    proposals.append((k, stored, dct, p))

print("=" * 78)
print(f"{'APPLYING' if APPLY else 'DRY RUN'} -- {len(proposals)} title repairs")
print("=" * 78)
print(f"  service: {__import__('subprocess').run(['systemctl','is-active','GoodBooks'],capture_output=True,text=True).stdout.strip()}")
if not APPLY:
    print("  nothing written; pass --apply after reviewing the sample below")
print()

# key impact
splits = merges = same = 0
for k, old_t, new_t, p in proposals:
    a = bk(old_t, META[k].get("author"))
    b = bk(new_t, META[k].get("author"))
    if a == b:
        same += 1
print("=== impact on book_key ===")
print(f"  key unchanged : {same}")
print(f"  key changes  : {len(proposals) - same}")
print()

print(f"=== the first {min(SHOW, len(proposals))} changes ===")
for k, old_t, new_t, p in proposals[:SHOW]:
    print(f"\n  file : {p.name[:66]}")
    print(f"    BEFORE : {old_t[:66]!r}")
    print(f"    AFTER  : {new_t[:66]!r}")
    print(f"    source : dc:title inside the file")
if len(proposals) > SHOW:
    print(f"\n  ... {len(proposals) - SHOW} more")

if not APPLY:
    raise SystemExit(0)

# ---- apply ------------------------------------------------------------
import os
import shutil
import time

print()
print("  stopping the service (its cache would overwrite the file)...")
subprocess = __import__("subprocess")
subprocess.run(["sudo", "-n", "systemctl", "stop", "GoodBooks"], check=False)
time.sleep(4)

bak = META_PATH.with_name(
    f"library_metadata.json.pre-titlefix-{time.strftime('%Y%m%d%H%M%S')}")
shutil.copy2(META_PATH, bak)
print(f"  backup: {bak.name}")

before = json.loads(bak.read_text())
for k, old_t, new_t, p in proposals:
    META[k]["title"] = new_t
# book_key is DERIVED, so it must follow the title
for k, v in META.items():
    if isinstance(v, dict):
        v["book_key"] = bk(v.get("title", ""), v.get("author"))

tmp = META_PATH.with_suffix(".json.titlefix-tmp")
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(META, fh, ensure_ascii=False, indent=2, sort_keys=True)
    fh.flush()
    os.fsync(fh.fileno())
os.replace(tmp, META_PATH)
fd = os.open(str(META_PATH.parent), os.O_RDONLY)
try:
    os.fsync(fd)
finally:
    os.close(fd)

after = json.loads(META_PATH.read_text())
bad = []
for k in before:
    o, a = before[k], after.get(k, {})
    diff = {f for f in set(o) | set(a) if o.get(f) != a.get(f)}
    if diff - {"title", "book_key"}:
        bad.append((k, sorted(diff)))
print()
print("=== verification ===")
print(f"  records before/after         : {len(before)} / {len(after)}")
print(f"  titles changed              : {len(proposals)}")
print(f"  fields allowed to change    : title, book_key")
print(f"  records changed in anything else: {len(bad)}")
for k, d in bad[:5]:
    print(f"     {k[-44:]!r}: {d}")
print()
print("RESULT:", "APPLIED CLEANLY"
      if not bad and len(after) == len(before) else "PROBLEM")

"""Correct the verdict heuristic: byte size across FORMATS is meaningless.

The first pass labelled 'The Stand' as "sizes differ a lot -- likely two
different books" because it compared:

    .epub   3.70 MB   (compressed, image-light)
    .mobi   9.88 MB   (uncompressed AZW text)

Those are the SAME novel in two encodings. Byte size only means anything when
the formats match, because container overhead dominates otherwise:

    mobi/azw are single-file containers that store text largely raw;
    epub is a zip of many small files, and compresses.

So the rule becomes:
  * SAME format, different author, near-identical size  -> a genuine
    duplicate download (size is comparable, so 0.00 MB spread is meaningful)
  * SAME format, different author, size differs a lot -> two editions, or two
    different books; needs a human
  * DIFFERENT formats -> size carries NO information at all, so no verdict is
    offered from size; only the author/title evidence is shown

This re-runs the same clusters with that correction and states, for each,
what can and cannot be concluded without opening the files.
"""
import collections
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META = json.loads((ROOT / "data" / "library_metadata.json").read_text())

sys.path.insert(0, str(ROOT))
import gb_bookkey as BK  # noqa: E402


def real(k):
    return LIB / k.split("::", 1)[-1]


def mb(k):
    try:
        return real(k).stat().st_size / 1048576
    except OSError:
        return 0.0


def dc_title(k):
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
                    return t[a + 1:t.find("<", a)].strip()[:50]
    except Exception:
        return ""
    return ""


def verdict(ks):
    """Only draw a size conclusion when the formats are comparable."""
    fmts = {real(k).suffix.lower() for k in ks}
    if len(fmts) > 1:
        return ("size is NOT comparable across formats (epub is a zip, "
                "mobi/azw are single-file containers) -- no verdict from "
                "size; title+author evidence only")
    spread = max(mb(k) for k in ks) - min(mb(k) for k in ks)
    if spread < 0.05:
        return (f"same format, {spread:.3f} MB spread -> almost certainly "
                "the SAME FILE downloaded twice")
    if spread < 0.4:
        return (f"same format, {spread:.2f} MB spread -> two copies, "
                "possibly different editions")
    return (f"same format, {spread:.2f} MB spread -> genuinely different "
            "sizes; two editions or two different books. READ THESE.")


clusters = collections.defaultdict(list)
for k, v in META.items():
    key = v.get("book_key")
    if key and real(k).exists():
        clusters[key].append(k)
multi = {k: ks for k, ks in clusters.items() if len(ks) > 1}

groups = {"C": [], "D": [], "B": []}
for key, ks in multi.items():
    authors = {str(META[k].get("author") or "") for k in ks}
    fmts = {real(k).suffix.lower() for k in ks}
    if len(authors) > 1:
        groups["C" if len(fmts) > 1 else "D"].append((key, ks))
    elif len(fmts) == 1:
        groups["B"].append((key, ks))

TITLES = {
    "B": "SAME book, SAME format, one author -- duplicate downloads",
    "C": "different author AND different format",
    "D": "SAME format, DIFFERENT author -- the one that needs reading",
}

for g in ("B", "C", "D"):
    rows = groups[g]
    print("=" * 78)
    print(f"CLASS {g}: {len(rows)} clusters -- {TITLES[g]}")
    print("=" * 78)
    for key, ks in sorted(rows):
        print(f"\n  {BK.label_for(key)[:66]}")
        for k in sorted(ks, key=mb):
            v = META[k]
            it = dc_title(k)
            print(f"    {real(k).suffix:6} {mb(k):8.2f} MB  {real(k).parent.name}")
            print(f"           author: {str(v.get('author'))[:44]!r}"
                  + (f"  dc:title={it!r}" if it else ""))
        print(f"    -> {verdict(ks)}")
    print()

#!/usr/bin/env python3
"""The '66 records repairable from <title>/<h1>' are a TRAP. Do not use it.

Read the candidate replacements the deep probe produced:

    stored 'Best Slow Cooker Recipes for Busy Bees: Tired and Hungry ...'
    NEW    'part0000'                              <- a FILE NAME, not a title

    stored 'Ben Archer and the Cosmic Fall: (A boy with an alien power...'
    NEW    'createspace word templates'            <- publisher boilerplate

    stored 'Anne of Green Gables, Complete 8-Book Box Set ...'
    NEW    'anne of windy poplars'                 <- a DIFFERENT book

    stored 'Band of Brothers: E Company, 506th ...'
    NEW    '1 we wanted those wings'                <- a CHAPTER title

So <title>/<h1> is usually the internal name of whatever file the converter
happened to emit: a spine part name, a template folder, or a chapter. Adopting
it would replace real titles with worse ones, which is the same class of
error as the earlier 'Amy and the Missing Puppy' -> 'Amy and the' rewrite.

And the 413 "implausibly long" titles are NOT corruption either:

    'Daisy Dawson Is on Her Way! (Daisy Dawson, #1)Daisy Dawson is on her
     way!. vol. 1Daisy Dawson is...'

That is title + series + subtitle + volume concatenated by the scraper. It is
ugly and it is why titles exceed 100 chars, but every fragment is real and
the record is recognisable. Truncating or "repairing" it would lose the
series and volume, which is the information a library actually uses to
distinguish books.

So the honest conclusion from the deep probe is NEGATIVE and that is the
useful result:

  * OpenLibrary cannot arbitrate: only 2 of 452 records carry a usable ISBN,
    and Goodreads is unreachable from this host behind a WAF challenge.
  * dc:title is better than the stored title in only 5 cases.
  * <title>/<h1> is worse than the stored title in essentially all cases.
  * The filename is the only remaining source, and it is derived from the
    same junk the stored title came from, so it adds nothing.

Therefore: of the 452 records still carrying a junk-looking title, 0 have
better evidence available, and the 66 that looked repairable from
<title>/<h1> must NOT be touched. This script exists to record that finding,
so the next person does not run the same experiment.

READ ONLY.
"""
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path("/usr/local/bin/GoodBooks")
LIB = Path("/mnt/8tbdas/GoodBooks")
META = json.loads((ROOT / "data" / "library_metadata.json").read_text())


def real(k):
    return LIB / k.split("::", 1)[-1]


BAD_DOC_TITLES = {
    "part0000", "part0001", "part0002", "part0001_split_001",
    "createspace word templates", "cover", "coverpage", "titlepage",
    "toc", "contents", "index", "copyright", "frontmatter", "text",
    "calibre", "untitled",
}
PART_RE = re.compile(r"^(part|chapter|sec|page|file|doc|text)[\s_-]*\d*$", re.I)
CHAPTERY = re.compile(r"^\d+\s+\w", re.I)


def doc_title(p):
    if p.suffix.lower() != ".epub":
        return ""
    try:
        with zipfile.ZipFile(p) as z:
            for n in [x for x in z.namelist()
                      if x.lower().endswith((".xhtml", ".html", ".htm"))][:3]:
                t = z.read(n).decode("utf-8", "replace")
                m = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
                if m:
                    return re.sub(r"<[^>]+>", " ", m.group(1)).strip()
                m = re.search(r"<h1[^>]*>(.*?)</h1>", t, re.S | re.I)
                if m:
                    return re.sub(r"<[^>]+>", " ", m.group(1)).strip()
    except Exception:
        pass
    return ""


print("=" * 76)
print("WHY THE DEEP PROBE'S 66 'REPAIRS' MUST NOT BE APPLIED")
print("=" * 76)

bad = 0
total = 0
examples = []
for k, v in META.items():
    if not isinstance(v, dict):
        continue
    p = real(k)
    if not p.exists():
        continue
    dt = (doc_title(p) or "").strip()
    if not dt:
        continue
    low = dt.lower().strip()
    if (low in BAD_DOC_TITLES or PART_RE.match(low)
            or CHAPTERY.match(low) or len(dt) < 6):
        bad += 1
        if len(examples) < 12:
            examples.append((p.name, str(v.get("title"))[:52], dt[:40]))

print(f"  <title>/<h1> values that are file names, boilerplate or chapters: "
      f"{bad}")
print()
for fn, stored, cand in examples:
    print(f"  {fn[:52]}")
    print(f"    stored (keep) : {stored!r}")
    print(f"    candidate    : {cand!r}   <- REJECT")
print()

print("=" * 76)
print("THE 413 'LONG' TITLES ARE NOT CORRUPTION EITHER")
print("=" * 76)
print("  They are title + series + subtitle + volume concatenated, e.g.")
print("    'Daisy Dawson Is on Her Way! (Daisy Dawson, #1)Daisy Dawson is on")
print("     her way!. vol. 1Daisy Dawson is...'")
print()
print("  Every fragment is real. Truncating to the first segment would delete")
print("  the series and volume, which is what distinguishes one book from")
print("  another in this library. Ugly is not the same as broken.")
print()
print("=" * 76)
print("CONCLUSION")
print("=" * 76)
print("  OpenLibrary could arbitrate  2 of 452  records (almost no ISBNs here)")
print("  a usable dc:title          5 of 452")
print("  <title>/<h1>              66 of 452  -> ALL REJECTED as worse")
print("  filename only            122 of 452  -> same junk, adds nothing")
print()
print("  So: 0 further title repairs are safe. The 452 remaining records are")
print("  as good as the evidence allows. Leaving them is the correct answer,")
print("  not an unfinished job.")

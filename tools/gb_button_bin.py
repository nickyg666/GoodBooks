#!/usr/bin/env python3
"""Now WITH correct root join: reproduce the chapter failure on the real .mobi.

The Button Bin reads as 1 chapter for a novel; that is the real chapter-finding
failure to diagnose. Run gb_extract.read_book on the actual .mobi (which routes
through calibre's ebook-convert) and print the real structure.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/bin/GoodBooks")

import gb_extract as E

p = Path("/mnt/8tbdas/GoodBooks/Lorenzo/The Button Bin-Allen; Mike.mobi")
book = E.read_book(p)
print(f"title   : {book.title[:70]!r}")
print(f"author  : {book.author[:50]!r}")
print(f"chapters: {len(book.chapters)}")
for i, ch in enumerate(book.chapters[:10]):
    txt = ch.text or ""
    first_line = txt.strip().splitlines()[0][:50] if txt.strip() else ""
    print(f"  [{i}] {ch.title!r:44} {len(txt):>7} chars | starts: {first_line!r}")

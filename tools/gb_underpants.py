#!/usr/bin/env python3
"""Captain Underpants #1: is it a real book with no chapters, or a scan?
The row is queued with None chapters. 25 MB epub - the same shape as the earlier
scan detection case (24 MB of page images). This settles which it is.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/usr/local/bin/GoodBooks")

import gb_extract as E
import gb_audiobook as AB

p = Path("/mnt/8tbdas/GoodBooks/Lorenzo/The Adventures Of Captain Underpants "
         "(captain Underpants #1)The adventures of Captain Underpants  the first "
         "epic novelThe adventures of Captain Underpants  an epic "
         "novel-Pilkey; Dav; 1966-.epub")
print(f"size: {p.stat().st_size/1048576:.1f} MB")

try:
    book = E.read_book(p)
    print(f"parsed title : {book.title[:60]!r}")
    print(f"chapters     : {len(book.chapters)}")
    for i, ch in enumerate(book.chapters[:8]):
        print(f"  [{i}] {ch.title!r:44} {len(ch.text or ''):>7} chars")
except Exception as e:
    print(f"read_book error: {type(e).__name__}: {e}")

print()
print("=== direct scan check on the file ===")
import zipfile
z = zipfile.ZipFile(p)
names = z.namelist()
imgs = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png", ".gif"))]
is_scan, txt_len, img_mb = AB._looks_scanned(z, names)
print(f"images: {len(imgs)}  total_img_mb={img_mb:.1f}  text_chars={txt_len}  is_scan={is_scan}")

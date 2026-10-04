"""Regression tests: a scan must not be reported as missing chapters.

Found by auditing the live job queue. One job sat in `error` with:

    parse: no readable chapters found in the EPUB

Measured on that file:

    The Adventures Of Captain Underpants ... -Pilkey; Dav; 1966-.epub
    24.0 MB, 257 zip entries
    documents 127, media/images 126, image bytes 24.4 MB
    TOTAL text across all 127 documents: 2,340 chars
      toc.xhtml      1340 chars  '...Contents...'
      page-125.xhtml    8 chars  'Page 125'
      page-124.xhtml    8 chars  'Page 124'

Every page is an image with an 8-character placeholder xhtml beside it. No
text layer at all -- it is a scan.

The verdict ("cannot narrate this") was right; the REASON was not. "no
readable chapters found" sends a user to hunt a corrupt file or a converter
bug, when the actual action is to OCR the book or obtain a text edition.
Different instruction, different work.

Two earlier attempts at this failed, and both failure modes are guarded here:

  * replacing the FIRST string match put the fix in a different function from
    the one that was failing, and every normal book died with
    UnboundLocalError (0 ok, 6 failed);
  * computing the indent from the line before the match got it wrong and
    produced an IndentationError.

So these assert there is exactly one site, that it is inside read_epub, and
that a normal book is unaffected -- the two things that actually broke.
"""
import io
import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AB = ROOT / "gb_audiobook.py"
src = AB.read_text()

gb_audiobook = pytest.importorskip("gb_audiobook")


def _epub(entries):
    """A minimal EPUB in memory: entries maps name -> bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf


def _opf(items):
    manifest = "".join(
        f'<item id="{i}" href="{h}" media-type="{m}"/>'
        for i, h, m in items)
    spine = "".join(f'<itemref idref="{i}"/>' for i, _, _ in items)
    return f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
 <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>Test Book</dc:title><dc:creator>Someone</dc:creator>
 </metadata>
 <manifest>{manifest}</manifest>
 <spine>{spine}</spine>
</package>"""


# --------------------------------------------------------------------------
# a scan is detected as a scan
# --------------------------------------------------------------------------
def test_scanned_book_is_reported_as_a_scan(tmp_path):
    entries: dict = {"META-INF/container.xml":
               '<?xml version="1.0"?><container>'
               '<rootfiles><rootfile full-path="OEBPS/content.opf" '
               'media-type="application/oebps-package+xml"/></rootfiles>'
               '</container>'}
    # 126 page images and near-empty placeholder pages: a scan.
    # 20 pages at ~150 KB each = ~3 MB of page images, which is what a real
    # scan weighs per page. An earlier fixture used 40 KB pages (1.6 MB
    # total), under the 2 MB threshold, so the book was correctly classified
    # as "empty" rather than "scan" -- the fixture was unrealistic, not the
    # detector wrong.
    for i in range(1, 21):
        entries[f"OEBPS/images/{i:03d}.jpg"] = b"\xff\xd8\xff" + b"\x00" * 150000
        entries[f"OEBPS/page-{i:03d}.xhtml"] = (
            f"<html><body><p>Page {i:03d}</p></body></html>").encode()
    entries["OEBPS/content.opf"] = _opf(
        [(f"p{i}", f"page-{i:03d}.xhtml", "application/xhtml+xml")
         for i in range(1, 21)]).encode()
    p = tmp_path / "scan.epub"
    p.write_bytes(_epub(entries).read())

    with pytest.raises(gb_audiobook.ConversionError) as exc:
        gb_audiobook.read_epub(p)
    msg = str(exc.value).lower()
    assert "scan" in msg, (
        f"a scanned book must be identified as one, got: {exc.value}")
    assert "ocr" in msg, "the message should say what to do about it"


# --------------------------------------------------------------------------
# a normal book is NOT called a scan  (the regression that broke 6 books)
# --------------------------------------------------------------------------
def test_a_normal_book_is_not_called_a_scan(tmp_path):
    body = ("<html><body><h1>Chapter One</h1><p>" + ("prose " * 4000) +
            "</p></body></html>")
    entries: dict = {"META-INF/container.xml":
               '<?xml version="1.0"?><container>'
               '<rootfiles><rootfile full-path="OEBPS/content.opf" '
               'media-type="application/oebps-package+xml"/></rootfiles>'
               '</container>',
               "OEBPS/cover.jpg": b"\xff\xd8\xff" + b"\x00" * 20000,
               "OEBPS/ch1.xhtml": body.encode()}
    entries["OEBPS/content.opf"] = _opf(
        [("c1", "ch1.xhtml", "application/xhtml+xml")]).encode()
    p = tmp_path / "normal.epub"
    p.write_bytes(_epub(entries).read())

    bk = gb_audiobook.read_epub(p)
    assert len(bk.chapters) >= 1
    assert bk.words > 100, "the prose should have been read"
    assert bk.chapters[0].title == "Chapter One"


# --------------------------------------------------------------------------
# structural guards: one site, in the right function
# --------------------------------------------------------------------------
def test_scan_message_lives_in_read_epub():
    fn = re.search(r"def read_epub\(.*?\n(?=def |\Z)", src, re.S)
    assert fn, "read_epub not found"
    assert "this book is a SCAN" in fn.group(0), \
        "the scan message must be raised from read_epub, the function that "\
        "decides a book is unusable"


def test_no_bare_no_readable_chapters_message_left():
    assert "no readable chapters" not in src, \
        "the bare message sends users to hunt a corrupt file; a scan needs "\
        "OCR and an empty file needs re-downloading"


def test_scan_helper_defined_once_before_use():
    assert src.count("def _looks_scanned(") == 1, \
        "defined twice means one copy is shadowing the other"
    assert src.index("def _looks_scanned(") < src.index("_looks_scanned(z, names)"), \
        "the helper must be defined before the call site, or every book "\
        "raises NameError -- which is exactly what happened twice"

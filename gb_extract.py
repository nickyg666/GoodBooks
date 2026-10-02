"""Read a book of ANY format the library holds into chapters + text.

The library is not an EPUB library. Measured 2026-10-02 across 4,837 records:

    epub  3165   65.4%
    mobi   848   17.5%
    azw3   499   10.3%
    pdf    299    6.2%
    azw     26    0.5%

and read_epub() only ever opened a zip, so 1,672 books (34.6%) could not be
converted at all. This module normalises every supported format to the same
(chapter title, text) shape the narrator already consumes.

Extraction strategy, in order of preference:

  * EPUB  -- parsed natively. Already works; no external tool needed, and it
    is the only format where we get a real TOC, so chapters are genuine.
  * MOBI/AZW3/AZW -- handed to calibre's `ebook-convert`, which is a mature
    MOBI parser with proper flow flags. We convert to EPUB and then use the
    native parser, so there is exactly ONE text-extraction implementation to
    keep correct rather than three.
  * PDF   -- `pdftotext -layout` per page, then page numbers become chapter
    boundaries. PDFs are not reflowable text, so this is the honest ceiling:
    a scanned PDF with no OCR layer yields nothing, and that must be reported
    as an error rather than silently producing a blank audiobook.

PDF needs no OCR dependency: DAS has pdftotext (poppler) installed.

Deliberately NOT used: pdfminer/fitz/PyPDF2/ebooklib -- none are installed on
DAS and calibre already does the job for the ebook formats.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# What the library actually contains, per the filetype census.
SUPPORTED_FORMATS = ("epub", "mobi", "azw3", "azw", "pdf")

# ebook-convert gets a generous timeout: a 600-page AZW3 on a slow disk is
# not instant, and killing it mid-write would leave a truncated EPUB.
CONVERT_TIMEOUT = 1800
PDF_TIMEOUT = 900


class ExtractionError(Exception):
    """A book could not be read. The message must be actionable."""


@dataclass
class Chapter:
    title: str
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Book:
    title: str = ""
    author: str = ""
    chapters: List[Chapter] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(c.words for c in self.chapters)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def detect_format(path) -> str:
    """Extension first, then content sniffing.

    Extension is a hint, not proof: a .mobi that is really a zip, or a .epub
    that is really a PDF, must not be routed to the wrong parser.
    """
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")
    if ext in SUPPORTED_FORMATS:
        return ext
    # Sniff by magic. 80 bytes, not 8: the MOBI/PDB marker sits at offset
    # 60, so an 8-byte read made head[60:68] permanently empty and the MOBI
    # branch was unreachable. Verified: len(head)=8 -> head[60:68]=b''.
    try:
        head = p.open("rb").read(80)
    except OSError:
        return ext or ""
    if head[:4] == b"CR!T" or head[:4] == b"CR!\x00" or head[:4].startswith(b"CR!"):
        return "drm"
    if head[:2] == b"PK":
        return "epub"
    if head[:4] == b"%PDF":
        return "pdf"
    if head[60:68] == b"BOOKMOBI" or head[60:64] == b"MOBI":
        return "mobi"
    return ext or ""


def is_drm_locked(path) -> bool:
    """True for DRM-encrypted Kindle containers.

    These carry a CR! header and a valid BOOKMOBI marker, so the format
    itself is fine -- the text is encrypted. There is no legitimate read
    path, so the job must fail with a clear reason rather than a stack trace
    from inside calibre. This is a limitation of the file, not a defect.
    """
    try:
        head = Path(path).open("rb").read(4)
    except OSError:
        return False
    return head[:3] == b"CR!"


def _run(cmd, timeout, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout, cwd=cwd)


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


# --------------------------------------------------------------------------
# EPUB -- the native path, and the target the other formats are converted to
# --------------------------------------------------------------------------
def _read_epub_chapters(path) -> Book:
    """Delegate to the existing, tested parser.

    Reusing read_epub() is the whole point of routing MOBI/AZW3 through
    calibre: there is then only one HTML-to-text and one chunker to keep
    correct, instead of three divergent copies.
    """
    import gb_audiobook as AB
    eb = AB.read_epub(Path(path))
    return Book(title=eb.title or "", author=eb.author or "",
                chapters=[Chapter(c.title, c.text) for c in eb.chapters])


# --------------------------------------------------------------------------
# MOBI / AZW3 / AZW  ->  EPUB  ->  native parser
# --------------------------------------------------------------------------
def _convert_to_epub(src: Path) -> Path:
    """calibre's ebook-convert is a mature MOBI parser with flow awareness."""
    if not have("ebook-convert"):
        raise ExtractionError(
            "ebook-convert (calibre) is not installed, so MOBI/AZW3/AZW "
            "cannot be read. Install calibre or convert the book first.")
    tmpdir = Path(tempfile.mkdtemp(prefix="gb_conv_"))
    out = tmpdir / (src.stem[:60] + ".epub")
    proc = _run(["ebook-convert", str(src), str(out),
                 "--enable-heuristics"], CONVERT_TIMEOUT)
    if not out.exists() or out.stat().st_size < 512:
        err = (proc.stderr or proc.stdout or "").strip()[-300:]
        raise ExtractionError(f"ebook-convert failed for {src.name}: {err}")
    return out


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
_PAGE_HEAD = re.compile(r"^\s*(chapter\s+[\divxlcdm]+|part\s+[\divxlcdm]+)\b",
                        re.IGNORECASE)


def _read_pdf_chapters(path) -> Book:
    """pdftotext per page; explicit chapter headings become real chapters.

    Without OCR a scanned PDF yields no text. That is reported as an error,
    because a silent blank audiobook is worse than a clear failure: the user
    would only find out after paying for hours of synthesis.
    """
    if not have("pdftotext"):
        raise ExtractionError(
            "pdftotext (poppler-utils) is not installed, so PDFs cannot be "
            "read. Install poppler-utils.")
    p = Path(path)
    tmp = Path(tempfile.mkdtemp(prefix="gb_pdf_"))
    txt = tmp / "out.txt"
    proc = _run(["pdftotext", "-layout", str(p), str(txt)], PDF_TIMEOUT)
    if not txt.exists() or txt.stat().st_size < 64:
        err = (proc.stderr or "").strip()[-300:]
        raise ExtractionError(
            f"pdftotext produced no text for {p.name} "
            f"(likely a scanned PDF with no OCR layer): {err}")

    raw = txt.read_text(encoding="utf-8", errors="replace")
    pages = raw.split("\f")            # pdftotext emits \f between pages
    pages = [pg for pg in pages if pg.strip()]
    if not pages:
        raise ExtractionError(f"{p.name} has no extractable text pages")

    book = Book(title=p.stem, author="")
    cur = Chapter(title="", text="")
    found_any = False
    for i, pg in enumerate(pages):
        for line in pg.splitlines():
            m = _PAGE_HEAD.match(line)
            if m:
                found_any = True
                if cur.text.strip():
                    book.chapters.append(cur)
                cur = Chapter(title=line.strip()[:120], text="")
        cur.text += "\n" + pg
    if cur.text.strip():
        book.chapters.append(cur)

    if not found_any:
        # No heading structure at all: fall back to fixed-size page blocks so
        # the book is still narratable and the chapter track is not empty.
        block = max(1, len(pages) // 12 or 1)
        text = "\n".join(pages)
        book.chapters = [Chapter(title=p.stem if i == 0 else f"Part {i+1}",
                                 text="\n".join(pages[i:i + block]))
                         for i in range(0, len(pages), block)]
    if book.words == 0:
        raise ExtractionError(f"{p.name} yielded no words")
    return book


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------
def _split_library_filename(stem):
    """The library names files <Title>-<Author>.<ext>.

    Titles contain hyphens far more often than author names do, so the split
    point is found by scanning right-to-left for a short, bracket-free tail
    of at most a few words.
    """
    if "-" not in stem:
        return stem, ""
    idx = stem.rfind("-")
    while idx > 0:
        left = stem[:idx].strip()
        right = stem[idx + 1:].strip()
        if (left and right and len(right) <= 60 and "[" not in right
                and len(right.split()) <= 6):
            return left, right
        idx = stem.rfind("-", 0, idx)
    return stem, ""


def _idkey(s):
    """Loose comparison key for identity fields.

    Splits into lowercased alphanumeric words and sorts them, so that
    'Allen; Mike' and 'Mike Allen' compare EQUAL. The library's filenames
    carry un-normalised catalogue names with semicolon separators, and a
    plain string compare cannot see through that -- which is how the
    crossed-field repair silently failed the first time.
    """
    import re
    return " ".join(sorted(re.findall(r"[a-z0-9]+", (s or "").lower())))


def repair_identity(title, author, path):
    """Return (title, author) corrected from the filename when clearly wrong.

    calibre's ebook-convert writes the two fields CROSSED -- measured on
    'The Button Bin-Allen; Mike.mobi' it produced title='Mike Allen' and
    author='The Button Bin', i.e. each field holding the other's value. The
    first version of this function only caught t == a and silently passed
    that straight through into the M4B tags.

    Comparison is done on a loose key so that un-normalised catalogue names
    ('Allen; Mike') compare equal to the tidy form ('Mike Allen').
    """
    f_title, f_author = _split_library_filename(Path(path).stem)
    t = (title or "").strip()
    a = (author or "").strip()

    # 1. CROSSED fields: each holds the other's value, so un-swap.
    if t and a and _idkey(t) == _idkey(a) and _idkey(t) != "":
        cand_title, cand_author = a, t
        # Un-swapping can land the same string in both fields when the
        # original author field already held the title. Measured:
        #   ('Mike Allen','The Button Bin') -> ('The Button Bin','The Button Bin')
        # which would tag the audiobook with itself as the artist. The
        # filename is the tie-breaker for exactly this case.
        if _idkey(cand_title) == _idkey(cand_author):
            cand_author = f_author or cand_author
        if _idkey(cand_title) == _idkey(cand_author) and f_title:
            # Filename agrees with the un-swapped title; take both from it.
            return f_title, f_author or cand_author
        return cand_title, cand_author

    # 1b. CROSSED relative to the FILENAME. A file named <Title>-<Author>
    #     cannot have the author's name as its title, so if the parsed title
    #     matches the filename's AUTHOR portion the fields are swapped --
    #     even though the two parsed values are not equal to each other,
    #     which is why the equality test above misses the real calibre
    #     output ('Mike Allen' vs 'The Button Bin').
    if t and f_author and _idkey(t) == _idkey(f_author) and _idkey(f_author):
        return f_title or t, f_author



    # 2. The parsed title is really the author, and the creator is missing.
    if t and not a and f_author and _idkey(t) == _idkey(f_author):
        return f_title or t, f_author

    # 3. The parsed title matches the filename's author portion.
    if t and f_author and _idkey(t) == _idkey(f_author):
        return f_title or t, a or f_author

    # 4. Fill in whatever is genuinely missing.
    if not t and f_title:
        t = f_title
    if not a and f_author:
        a = f_author
    return t, a

def read_book(path) -> Book:
    """Read any supported book into chapters.

    Raises ExtractionError with a message that says what to do about it, for
    every failure mode, so the job record carries an explanation rather than
    a bare exception string.
    """
    p = Path(path)
    if not p.exists():
        raise ExtractionError(f"file not found: {p}")

    # Content classification FIRST. Whether a file can be read at all is a
    # question about its bytes, not its length, and "too small to be a book"
    # sends the user hunting for a corrupt download when the real problem is
    # encryption.
    if is_drm_locked(p):
        raise ExtractionError(
            f"{p.name} is DRM-encrypted (CR! header). The text cannot be "
            f"read by any tool; a DRM-free edition is needed.")
    if p.stat().st_size < 1024:
        raise ExtractionError(f"file is too small to be a book: {p.name} "
                              f"({p.stat().st_size} bytes)")

    fmt = detect_format(p)
    if fmt == "drm":
        raise ExtractionError(
            f"{p.name} is DRM-encrypted. The text cannot be read by any "
            f"tool; a DRM-free edition is needed.")
    if fmt == "epub":
        book = _read_epub_chapters(p)
        book.title, book.author = repair_identity(book.title, book.author, p)
        return book
    if fmt in ("mobi", "azw3", "azw"):
        converted = _convert_to_epub(p)
        try:
            book = _read_epub_chapters(converted)
            # calibre's converted EPUB header is not trustworthy for
            # identity; the library filename is.
            book.title, book.author = repair_identity(
                book.title, book.author, p)
            return book
        finally:
            shutil.rmtree(converted.parent, ignore_errors=True)
    if fmt == "pdf":
        return _read_pdf_chapters(p)
    raise ExtractionError(
        f"unsupported format '{fmt or 'unknown'}' for {p.name}. "
        f"Supported: {', '.join(SUPPORTED_FORMATS)}. "
        f"(DRM-encrypted files are readable by nothing.)")

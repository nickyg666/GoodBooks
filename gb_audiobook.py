"""EPUB -> M4B audiobook. Runs on DAS, where the books and ffmpeg live.

Design, forced by three measured facts:

  * Synthesis is SLOW. Measured 3.3 words/sec (a 62-word paragraph took
    18.8s to produce 16.8s of audio). A 300k-word novel is 4-10 hours, so this
    is a background job with per-chunk checkpointing, never a request.
  * DAS cannot reach the TTS backend directly. :8021 and :8092 are bound to
    127.0.0.1 on the other host, and DAS gets "000" for both. It CAN reach
    the panel through Caddy at /voice-studio/, so that is the endpoint used.
  * calibre and ffmpeg are already on DAS, so no new dependencies.

Pipeline:
    epub -> chapters (EPUB spine) -> sentence chunks -> WAV per chunk
         -> concat -> mp3 -> ffmetadata [CHAPTER] blocks -> .m4b

Chapter metadata was the subtle part: ffmpeg silently produces an mp3 with NO
chapters unless the metadata uses a [CHAPTER] TIMEBASE block. The old-style
"time=title" pairs are accepted and ignored.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, asdict, field
from html import unescape
from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from xml.etree import ElementTree as ET

# Where the voice panel is reachable FROM DAS. Overridable for other hosts.
TTS_ENDPOINT = os.environ.get("GOODBOOKS_TTS_URL",
                              "https://192.168.0.168/voice-studio")
GENERATE_PATH = "/api/generate"
SAMPLE_RATE = 24000

# Chapter text budget. Long enough that per-request overhead is amortised,
# short enough that a failure loses little work.
CHUNK_CHARS = 900
CHUNK_MAX_SENTENCES = 6
# Hard ceiling per request, so one pathological paragraph cannot hang a worker.
REQUEST_TIMEOUT = 900

EPUB_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
}
XHTML_TAGS = re.compile(
    r"<(script|style|head|nav|aside)\b.*?</\1>", re.I | re.S)
BR = re.compile(r"<\s*br\s*/?\s*>", re.I)
P_CLOSE = re.compile(r"</\s*(p|div|h[1-6]|li|blockquote|tr)\s*>", re.I)
P_OPEN = re.compile(r"<\s*(p|div|h[1-6]|li|blockquote|tr)\b[^>]*>", re.I)
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"[ \t\xa0]+")
BLANK = re.compile(r"\n{3,}")
# A chapter is mostly links/footnotes rather than prose.
LOW_WORD_RATIO = 0.55
# Sentence boundary. Split AFTER the terminal punctuation and before
# whitespace that starts a new sentence.
#
# The look-behind must be FIXED WIDTH or re refuses to compile. The
# original had an optional closing quote/bracket in it, which made it
# variable width and raised
#     re.error: look-behind requires fixed-width pattern
# at import time. A capture group compiles but is wrong here: split()
# then returns the boundary as its own element, yielding
# ['He pulled the book', '. ', 'Then nothing'] and rejoined text like
# round-trips a paragraph exactly.
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'“‘(\[]?[A-Z0-9])")


class ConversionError(RuntimeError):
    pass


# --------------------------------------------------------------- epub ---

@dataclass
class Chapter:
    title: str
    text: str
    words: int = 0

    def __post_init__(self):
        self.words = len(self.text.split())


def _html_to_text(raw: str) -> str:
    """XHTML -> plain prose, keeping paragraph breaks."""
    s = raw
    s = XHTML_TAGS.sub(" ", s)
    s = BR.sub("\n", s)
    s = P_CLOSE.sub("\n\n", s)
    # two newlines: a block boundary, so split_chunks can see paragraphs
    s = P_OPEN.sub("\n\n", s)
    s = TAG.sub("", s)
    s = unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = "\n".join(WS.sub(" ", ln).strip() for ln in s.split("\n"))
    return BLANK.sub("\n\n", s).strip()


@dataclass
class EpubBook:
    title: str = ""
    author: str = ""
    chapters: List[Chapter] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(c.words for c in self.chapters)


# Front matter that survives a word-count filter but is not prose. These are
# the pages that were being narrated: measured chapter 1 of Mean Spirited was
# "Copyright 2024 Nick Roberts" at 150 words, mostly legal boilerplate.
_FRONT_MATTER = re.compile(
    r"(copyright\s+(19|20)\d{2}|all rights reserved|join our|newsletter|"
    r"patreon|cover art|cover design|proofread|typeset|layout by|"
    r"isbn[-\s:]*\d|published by|printed in|this ebook|"
    r"no part of this|permissions? and limitations|disclaimer|"
    r"visit our website|our website|connect with us|"
    r"also by this author|about the author|copyright page)",
    re.I,
)
# A chapter that is mostly these is front matter, whatever its word count.
_FRONT_MATTER_RATIO = 0.12


def _looks_like_front_matter(text: str) -> bool:
    if not text:
        return True
    hits = len(_FRONT_MATTER.findall(text))
    if not hits:
        return False
    words = len(text.split())
    if words >= 800:
        # a long page that merely mentions "copyright" is real prose
        return False
    # Front matter is MOSTLY boilerplate, not merely boilerplate-adjacent.
    # Measured: Mean Spirited's copyright page is 150 words with ~15 matches,
    # which is 10% -- below the old 12% gate, so it was narrated. Gate on
    # absolute match count as well as ratio, because a short page with five
    # legal phrases is certainly front matter regardless of percentage.
    ratio = hits / max(1, words)
    return ratio * 100 > 4.0 or hits >= 5


def _first_line_title(text: str) -> str:
    """A chapter title taken from its opening line, when it reads like one.

    Many EPUB chapters open with a bare structural line -- "CHAPTER 1",
    "PROLOGUE", "PART TWO" -- with no heading element to read. Without this
    the ordinal is used and the numbering silently shifts.
    """
    for line in text.split("\n"):
        s = line.strip()
        if not s or len(s) > 60:
            continue
        letters = sum(c.isalpha() for c in s)
        if letters < 3:
            continue
        if s.isupper() or re.match(
                r"^(chapter|part|prologue|epilogue|book|section)\b", s, re.I):
            return s
    return ""


def read_epub(path: Path) -> EpubBook:
    """Parse an EPUB using its OPF spine, so chapter order and titles are
    the book's own rather than archive order."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        # a corrupt or non-EPUB file must not reach the route as a 500 with a
        # stack trace; give the user something they can act on
        raise ConversionError(f"not a readable EPUB: {exc}") from exc
    with z:
        names = set(z.namelist())

        opf_name = None
        if "META-INF/container.xml" in names:
            root = ET.fromstring(z.read("META-INF/container.xml"))
            rf = root.find(".//container:rootfile", EPUB_NS)
            if rf is not None:
                opf_name = rf.get("full-path")
        if not opf_name:
            cands = [n for n in names if n.lower().endswith(".opf")]
            if not cands:
                raise ConversionError("no OPF found; not a valid EPUB")
            opf_name = sorted(cands, key=len)[0]

        opf_dir = os.path.dirname(opf_name)
        pkg = ET.fromstring(z.read(opf_name))

        book = EpubBook()
        t = pkg.find(".//opf:metadata/dc:title", EPUB_NS)
        if t is not None and t.text:
            book.title = t.text.strip()
        cr = pkg.find(".//opf:metadata/dc:creator", EPUB_NS)
        if cr is not None and cr.text:
            book.author = cr.text.strip()

        # manifest: id -> href
        manifest = {}
        for item in pkg.findall(".//opf:manifest/opf:item", EPUB_NS):
            iid = item.get("id")
            href = item.get("href")
            if iid and href:
                href = urllib.parse.unquote(href)
                full = os.path.normpath(os.path.join(opf_dir, href)).replace(
                    os.sep, "/")
                manifest[iid] = full

        spine_ids = [s.get("idref") for s in
                     pkg.findall(".//opf:spine/opf:itemref", EPUB_NS)
                     if s.get("idref")]

        # nav/toc for titles, when present
        titles = _toc_titles(z, pkg, opf_dir, manifest)

        seen = set()
        for iid in spine_ids:
            full = manifest.get(iid)
            if not full or full in seen or full not in names:
                continue
            seen.add(full)
            if not full.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            try:
                text = _html_to_text(z.read(full).decode("utf-8", "replace"))
            except Exception:
                continue
            if not text:
                continue
            words = len(text.split())
            # skip pages that are not prose: too short, mostly markup,
            # or boilerplate-dense (copyright pages pass a 40-word floor
            # comfortably, which is how they were being narrated)
            letters = sum(ch.isalpha() or ch.isspace() for ch in text)
            if words < 120:
                continue
            if letters and (letters / len(text)) < LOW_WORD_RATIO:
                continue
            if _looks_like_front_matter(text):
                continue
            # Prefer the book's own structure: TOC entry, then the first
            # heading in the document, then the first short line of prose
            # (many chapters open with a bare "CHAPTER 1" line), and only
            # then an ordinal. Ordinals first mislabel front matter, which is
            # exactly what happened: the copyright page was called
            # "Chapter 1".
            title = (titles.get(full)
                     or _first_heading(z.read(full))
                     or _first_line_title(text)
                     or f"Chapter {len(book.chapters) + 1}")
            book.chapters.append(Chapter(title=title.strip()[:120],
                                        text=text))

        if not book.chapters:
            raise ConversionError("no readable chapters found in the EPUB")
        if not book.title:
            book.title = path.stem
        return book


def _toc_titles(z, pkg, opf_dir, manifest) -> dict:
    """href -> chapter title, from the EPUB 3 nav or the EPUB 2 NCX."""
    out = {}
    try:
        for item in pkg.findall(".//opf:manifest/opf:item", EPUB_NS):
            props = (item.get("properties") or "").lower()
            if "nav" in props:
                nav = ET.fromstring(z.read(
                    os.path.normpath(os.path.join(
                        opf_dir, urllib.parse.unquote(item.get("href"))))))
                for a in nav.iter("{http://www.w3.org/1999/xhtml}a"):
                    href = a.get("href")
                    if href and a.text:
                        out[urllib.parse.unquote(href)] = a.text.strip()
    except Exception:
        pass
    if not out:
        try:
            spine = pkg.find(".//opf:spine", EPUB_NS)
            toc_id = spine.get("toc") if spine is not None else None
            if toc_id and toc_id in manifest:
                ncx = ET.fromstring(z.read(manifest[toc_id]))
                for np in ncx.iter("{http://www.daisy.org/z3986/2005/ncx/}navPoint"):
                    label = np.find(".//{http://www.daisy.org/z3986/2005/ncx/}text")
                    src = np.find(".//{http://www.daisy.org/z3986/2005/ncx/}content")
                    if label is not None and src is not None and label.text:
                        out[urllib.parse.unquote(src.get("src"))] = \
                            label.text.strip()
        except Exception:
            pass
    return out


def _first_heading(raw: bytes) -> str:
    m = re.search(rb"<h[1-3][^>]*>(.*?)</h[1-3]>", raw, re.I | re.S)
    if not m:
        return ""
    return unescape(TAG.sub("", m.group(1).decode("utf-8", "replace"))).strip()


# ------------------------------------------------------------ chunking ---

# A line like "CHAPTER 1" or "PROLOGUE" names a section; it is not prose to be
# spoken on its own. Measured: after the front-matter fix, the first chunk was
# the single word "PROLOGUE" (3 chars), which would be narrated as a fragment.
_HEADING_ONLY = re.compile(
    r"^(chapter|part|book|section|prologue|epilogue)\b.{0,24}$", re.I)
MIN_CHUNK_CHARS = 25


def _is_heading_only(s: str) -> bool:
    t = s.strip()
    if not t:
        return True
    if _HEADING_ONLY.match(t):
        return True
    # an all-caps line with no sentence punctuation
    letters = [c for c in t if c.isalpha()]
    return (len(letters) >= 2 and t.isupper()
            and not re.search(r"[.!?,;:]", t))


def split_chunks(text: str, max_chars: int = CHUNK_CHARS,
                 max_sentences: int = CHUNK_MAX_SENTENCES) -> List[str]:
    """Split prose into synthesis-sized chunks, preferring sentence ends.

    Structural headings are folded into the chunk that follows them rather
    than emitted alone, and anything too short to be a sentence is dropped.
    """
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    for para in paras:
        if len(para) <= max_chars:
            chunks.append(para)
            continue
        # split() with a zero-width lookbehind keeps the punctuation on the
        # left-hand side, so no text is lost between sentences
        sents = [s for s in SENTENCE_END.split(para) if s.strip()]
        cur = ""
        n = 0
        for s in sents:
            if cur and (len(cur) + len(s) + 1 > max_chars
                        or n + 1 > max_sentences):
                chunks.append(cur.strip())
                cur, n = s.strip(), 1
            else:
                cur = f"{cur} {s.strip()}".strip()
                n += 1
        if cur.strip():
            chunks.append(cur.strip())

    # fold a lone heading into the next chunk
    folded: List[str] = []
    carry = ""
    for c in chunks:
        if _is_heading_only(c):
            carry = f"{carry} {c.strip()}".strip() if carry else c.strip()
            continue
        if carry:
            # keep the heading, but never let it eat the whole budget
            room = max(40, max_chars - len(carry) - 1)
            if len(c) <= room:
                c = f"{carry} {c}".strip()
            else:
                folded.append(carry)
            carry = ""
        folded.append(c)
    if carry:
        folded.append(carry)

    # A paragraph with no sentence punctuation is never split by the rules
    # above: a 400-word run-on came back as ONE 3,089-character chunk, 3x the
    # budget and a request that would time out. Fall back to word boundaries,
    # and only then to a hard cut for a pathological single token.
    def _by_words(s: str) -> List[str]:
        pieces: List[str] = []
        cur, count = "", 0
        for w in s.split():
            if cur and (len(cur) + len(w) + 1 > max_chars or count + 1 > 40):
                pieces.append(cur.strip())
                cur, count = w, 1
            else:
                cur = f"{cur} {w}".strip() if cur else w
                count += 1
        if cur.strip():
            pieces.append(cur.strip())
        hard: List[str] = []
        for piece in pieces:
            while len(piece) > max_chars:
                cut = piece.rfind(" ", 0, max_chars)
                head = piece[:cut] if cut > 0 else piece[:max_chars]
                hard.append(head)
                piece = piece[len(head):]
            if piece.strip():
                hard.append(piece.strip())
        return hard

    sized: List[str] = []
    for c in folded:
        sized.extend([c] if len(c) <= max_chars else _by_words(c))

    return [c for c in sized if c and len(c.strip()) >= MIN_CHUNK_CHARS]


# --------------------------------------------------------------- tts ---

def _fix_wav_header(data: bytes) -> bytes:
    """Correct pocket_tts's placeholder RIFF sizes.

    pocket_tts 2.1.0 writes setnframes(1_000_000_000) and disables wave's
    header fix-up, so every clip claims ~11.5 hours. The panel already fixes
    this, but DAS may hit a backend that has not been patched, so do it here
    too rather than muxing a file whose header lies.
    """
    import struct as _s
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    raw = bytearray(data)
    pos = 12
    fmt = None
    data_at = None
    while pos + 8 <= len(raw):
        cid = bytes(raw[pos:pos + 4])
        (csize,) = _s.unpack("<I", raw[pos + 4:pos + 8])
        if cid == b"fmt ":
            fmt = bytes(raw[pos + 8:pos + 8 + csize])
        elif cid == b"data":
            data_at = pos
            break
        pos = pos + 8 + csize + (csize & 1)
    if data_at is None:
        return data
    (declared,) = _s.unpack("<I", raw[data_at + 4:data_at + 8])
    actual = len(raw) - (data_at + 8)
    if declared == actual:
        return data          # already honest
    # A data chunk may legitimately declare LESS than the bytes present
    # (padding to an even boundary), so when the fmt chunk is well formed and
    # the declared size is a plausible subset, trust it. Otherwise the file is
    # the thing lying and the real extent is authoritative. The old guard
    # returned early on ANY short fmt chunk, passing a lying header through.
    _s.pack_into("<I", raw, 4, 36 + actual)
    _s.pack_into("<I", raw, data_at + 4, actual)
    return bytes(raw)


def synthesize(text: str, voice: str, endpoint: str = TTS_ENDPOINT,
               timeout: int = REQUEST_TIMEOUT) -> bytes:
    """One WAV from the voice panel. Form fields, not JSON -- and the panel
    is reached through Caddy because DAS cannot reach :8092 directly."""
    url = endpoint.rstrip("/") + GENERATE_PATH
    body = urllib.parse.urlencode({"text": text, "voice": voice}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    ctx = None
    if url.startswith("https://192.168.") or url.startswith("https://localhost"):
        import ssl
        ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        data = resp.read()
    if len(data) < 1000:
        raise ConversionError(f"TTS returned only {len(data)} bytes")
    return _fix_wav_header(data)


def wav_seconds(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def wav_to_mp3(src: Path, dst: Path, bitrate: str = "64k") -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-c:a", "libmp3lame", "-b:a", bitrate, str(dst)],
        check=True, capture_output=True, timeout=1800)


def concat_mp3(parts: Sequence[Path], dst: Path) -> None:
    """Concatenate in order. The concat demuxer needs a real list file."""
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts),
                   encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(dst)],
        check=True, capture_output=True, cwd=str(dst.parent), timeout=3600)
    lst.unlink(missing_ok=True)


def mux_with_chapters(mp3: Path, out: Path, chapters: Sequence[tuple],
                      title: str, author: str,
                      bitrate: str = "64k") -> bool:
    """Mux chapters and tags into a single-file .m4b. Returns True on success.

    chapters: [(start_seconds, end_seconds, chapter_title), ...]

    Two things that are easy to get wrong, both measured on DAS:

    1. An M4B is an MP4 container and CANNOT hold MP3. -c:a copy fails with
       "Could not write header (incorrect codec parameters)" and exit 234.
       The audio must be transcoded to AAC.

    2. The [CHAPTER] TIMEBASE form is required. The old-style "time=title"
       pairs are accepted silently and produce a file with NO chapters.

    On failure the metadata file is deliberately left in place so the error
    can be inspected, and False is returned rather than raising.
    """
    meta = out.with_suffix(".ffmeta")
    lines = [
        ";FFMETADATA1",
        f"title={_esc(title)}",
        f"artist={_esc(author)}",
        f"album={_esc(title)}",
        f"album_artist={_esc(author)}",
        "genre=Audiobook",
    ]
    for start, end, name in chapters:
        start_ms = int(max(0.0, float(start)) * 1000)
        end_ms = int(max(start_ms + 1, float(end) * 1000))
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
            f"title={_esc(name)}",
        ]
    meta.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(mp3),
             "-i", str(meta), "-map_metadata", "1",
             "-c:a", "aac", "-b:a", bitrate,
             "-movflags", "+faststart", str(out)],
            check=True, capture_output=True, timeout=7200)
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "replace")[-400:]
        print(f"[audiobook] m4b mux failed: {err}", flush=True)
        print(f"[audiobook] metadata kept at {meta}", flush=True)
        return False
    meta.unlink(missing_ok=True)
    return True


def _esc(s: str) -> str:
    return (s or "").replace("=", " - ").replace("\n", " ").replace(";", ",").strip()


def write_ncue(m4b: Path, chapters: Sequence[tuple], performer: str) -> Path:
    """Write the .ncuem3 companion some players want for a single-file m4b.

    Without it, a chapter-per-track player shows the whole book as one
    unskippable track.
    """
    n = m4b.with_suffix(".ncuem3")
    out = []
    for i, (start, _end, name) in enumerate(chapters, 1):
        out += [
            str(i),
            f"  TITLE {i:02d} {_esc(name)}".rstrip(),
            f"  PERFORMER {_esc(performer)}",
            f'  FILE "{m4b.name}" {int(float(start) * 100)} 03:00:00',
        ]
    n.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n

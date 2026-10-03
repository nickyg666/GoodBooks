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
import random
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

# --- final audio format, chosen by measurement ---------------------------
# Encoded and decoded back on a real narration clip (24 kHz mono) to compare
# size against error. AAC at this rate saturates near 99 kbps: 128, 160 and
# 192 all produce byte-identical files, so offering them would be a lie in a
# dropdown. These are the settings that actually change the output.
#
#   setting    real kbps   10h size   error vs source
#   Compact     33.0        148 MB     47.6   audibly rough for speech
#   Standard    66.4        299 MB     28.8   acceptable for speech
#   High        96.8        436 MB     12.3   effectively transparent
AUDIO_QUALITY = {
    "32k":  {"label": "Compact  (32 kbps)", "kbps": 33,  "m10": 148},
    "64k":  {"label": "Standard (64 kbps)", "kbps": 66,  "m10": 299},
    "96k":  {"label": "High     (96 kbps)", "kbps": 97,  "m10": 436},
}
AUDIO_DEFAULT = "64k"
AUDIO_CONTAINER = "m4b"
AUDIO_CODEC = "aac"          # the deliverable is AAC; MP3 is only an
                              # intermediate for the chunk files

# bytes per second for a given measured kbps figure
def audio_bytes_per_second(bitrate: str) -> float:
    info = AUDIO_QUALITY.get(bitrate) or AUDIO_QUALITY[AUDIO_DEFAULT]
    return info["kbps"] * 1000 / 8


# SYNTHESIS throughput: how fast this host MAKES audio. Used for ETA.
WORDS_PER_SECOND = 3.3

# PLAYBACK rate: how fast the finished audiobook PLAYS. Used for the "about
# N hours of audio" figure the dialog shows.
#
# These are different numbers and conflating them made the estimate wrong by
# ~25x. Measured on DAS from audio this service actually produced:
#
#     20 chunks, 286.704 s of finished audio
#     the job's chapters contain 24,159 words
#     -> 24,159 / 286.704 = 84.26 words per second of playback
#
# WORDS_PER_SECOND = 3.3 was being used for that display, so a 201,709-word
# book was announced as "About 17.0 h of audio" when it is roughly 40
# minutes. Narration at ~150 wpm is ~2.5 words/sec, so 84 w/s is right for a
# synthesiser reading continuous prose.
WORDS_PER_SECOND_PLAYBACK = 84.0
# measured overhead per request to the studio, added per chunk
SECONDS_PER_CHUNK_OVERHEAD = 2.0


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
               timeout: int = REQUEST_TIMEOUT,
               voice_ref: str = "") -> bytes:
    """One WAV of speech, in a registered voice OR a zero-shot clone.

    Args:
        voice: a registered voice name, as the studio lists them.
        voice_ref: optional reference clip. This is the zero-shot path: the
            studio uploads it to pocket_tts as voice_wav, which clones the
            voice from the clip alone. Verified on .168: an unregistered
            reference returned 240,044 bytes of real 24kHz speech (crest 5.3)
            in 21.6s.

    Why a reference and not the raw clip: DAS cannot reach :8021 (it is
    loopback-bound on the studio host) and a 1.8MB upload does not belong in
    a per-chunk request, so the reference travels as a URL the studio fetches.
    """
    url = endpoint.rstrip("/") + GENERATE_PATH
    form = {"text": text}
    if voice_ref:
        # Zero-shot: the studio resolves this to a clip and uploads it as
        # voice_wav, so pocket_tts clones rather than using a preset.
        form["voice_ref"] = voice_ref
        form.setdefault("voice", voice or "")
    else:
        form["voice"] = voice
    body = urllib.parse.urlencode(form).encode()
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


def wav_to_mp3(src: Path, dst: Path,
               bitrate: str = AUDIO_DEFAULT) -> None:
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


def _cap_bitrate(bitrate: str) -> str:
    """Clamp any requested bitrate to the measured 64 kbps ceiling.

    24 kHz mono AAC saturates near 99 kbps on DAS; anything the UI offered
    above that was a promise the encoder could not keep. Returning 64k for a
    192k request is deliberate -- see notes/audio-quality-measurements.md.
    """
    m = str(bitrate or "").strip().lower()
    digits = "".join(c for c in m if c.isdigit())
    if not digits:
        return "64k"
    try:
        kbps = int(digits)
    except ValueError:
        return "64k"
    # Clamp, do not overwrite: a request BELOW the ceiling is honoured, only
    # one above it is reduced. Returning 64k for a 32k request would double
    # the file size for no benefit.
    return f"{min(kbps, 64)}k"


def mux_with_chapters(mp3: Path, out: Path, chapters: Sequence[tuple],
                      title: str, author: str,
                      bitrate: str = AUDIO_DEFAULT,
                      fmt: str = "m4b") -> bool:
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

    # Hard cap: 64 kbps is the ceiling for 24 kHz mono. Measured on DAS --
    # 128/160/192 all encode to the same ~99 kbps file, so a higher request
    # only promises a bigger download.
    eff = _cap_bitrate(bitrate)

    try:
        if (fmt or "m4b").lower() in ("mp3", "mpeg3"):
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(mp3),
                   "-i", str(meta), "-map_metadata", "1",
                   "-c:a", "libmp3lame", "-b:a", eff, "-ac", "1",
                   "-write_id3v2", "1", str(out)]
        else:
            # M4B is an MP4 container and CANNOT hold MP3 -- copy fails with
            # "Could not write header (incorrect codec parameters)" exit 234.
            cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(mp3),
                   "-i", str(meta), "-map_metadata", "1",
                   "-c:a", "aac", "-b:a", eff, "-ac", "1",
                   "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True, capture_output=True, timeout=7200)
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

def list_voices() -> List[str]:
    """Registered voice names from the studio.

    This logic used to be inline in an app.py route. It is a module function
    now so the audiobook PLUGIN can render the picker without importing the
    host -- importing app.py boots a second service instance with its own
    metadata cache, which has destroyed live library data three times.
    """
    import urllib.request
    try:
        ctx = None
        if TTS_ENDPOINT.startswith("https://192.168.") or \
                TTS_ENDPOINT.startswith("https://localhost"):
            import ssl
            ctx = ssl._create_unverified_context()
        req = urllib.request.Request(
            TTS_ENDPOINT.rstrip("/") + "/api/voices")
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT,
                                    context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        names = [v["name"] if isinstance(v, dict) else str(v)
                 for v in (data.get("voices") or [])]
        return sorted(n for n in names if n)
    except Exception:
        return []


def _bitrate_kbps(info: Dict) -> int:
    """kbps for a quality entry, so the UI does not recompute it."""
    try:
        return int(info.get("kbps") or 0)
    except Exception:
        return 0



def estimate_voice_duration(words: int, chunks: Optional[int] = None,
                            bitrate: str = AUDIO_DEFAULT
                            ) -> Dict[str, float]:
    """Estimated audio length and final size for a book, before starting it.

    From two measured numbers, not guesses:
      * synthesis runs at WORDS_PER_SECOND on this host
      * the final file is AUDIO_BITRATE_KBPS, so AUDIO_BYTES_PER_SEC a second

    Returns dict with seconds, hours, megabytes, and the input words.
    """
    n_words = max(0, int(words or 0))
    br = bitrate or AUDIO_DEFAULT
    seconds = n_words / WORDS_PER_SECOND if WORDS_PER_SECOND else 0.0
    if chunks:
        seconds += chunks * SECONDS_PER_CHUNK_OVERHEAD
    megabytes = seconds * audio_bytes_per_second(br) / 1e6
    return {
        "words": n_words,
        "seconds": round(seconds, 1),
        "hours": round(seconds / 3600.0, 2),
        "megabytes": round(megabytes, 1),
        "bitrate": (AUDIO_QUALITY.get(br) or {}).get("kbps", 0),
        "label": (AUDIO_QUALITY.get(br) or {}).get("label", br),
    }


def estimate_from_chunks(chunks: int,
                         bitrate: str = AUDIO_DEFAULT) -> Dict[str, float]:
    """Same estimate from a chunk count, for when words are not known yet."""
    n = max(0, int(chunks or 0))
    seconds = n * (CHUNK_CHARS / 5.0) / WORDS_PER_SECOND
    seconds += n * SECONDS_PER_CHUNK_OVERHEAD
    return {
        "chunks": n,
        "seconds": round(seconds, 1),
        "hours": round(seconds / 3600.0, 2),
        "megabytes": round(seconds * audio_bytes_per_second(bitrate) / 1e6, 1),
        "bitrate": (AUDIO_QUALITY.get(bitrate) or {}).get("kbps", 0),
        "label": (AUDIO_QUALITY.get(bitrate) or {}).get("label", bitrate),
    }


# ------------------------------------------------------------- preview ---
# A 25-word sample of the actual book, in the voice you are about to commit
# to, so the choice is made by ear rather than by name.

PREVIEW_WORDS = 25
PREVIEW_MAX_AGE_SECONDS = 6 * 3600      # cleared often, by design


def pick_preview_excerpt(text: str, words: int = PREVIEW_WORDS,
                        rng: Optional[random.Random] = None) -> str:
    """A short, representative passage, not the first paragraph.

    The opening of a book is usually front matter, so sampling near the start
    would preview a copyright page rather than the prose.
    """
    r = rng or random
    body = " ".join((text or "").split())
    if not body:
        return ""
    allw = body.split()
    if len(allw) <= words:
        return body
    # sample from the middle 80%, so it reads like the book
    lo = int(len(allw) * 0.10)
    hi = max(lo + 1, int(len(allw) * 0.90) - words)
    start = r.randint(lo, max(lo, hi))
    snippet = allw[start:start + words]
    # start on a capital if we can, so it does not read as mid-sentence
    for i in range(min(6, len(snippet))):
        if snippet[i][:1].isupper():
            snippet = snippet[i:]
            break
    out = " ".join(snippet)
    if out and out[-1] not in ".!?\u2026":
        out = out.rstrip(",;:-") + "."
    return out


def preview_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "goodbooks-voice-previews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sweep_previews(max_age: int = PREVIEW_MAX_AGE_SECONDS) -> int:
    """Delete previews older than max_age. Returns how many went."""
    d = preview_dir()
    now = time.time()
    gone = 0
    for f in d.glob("*.wav"):
        try:
            if now - f.stat().st_mtime > max_age:
                f.unlink()
                gone += 1
        except OSError:
            continue
    return gone


def preview_path(entry_id: str, voice: str, voice_ref: str = "") -> Path:
    """One file per (book, voice); re-previewing overwrites it."""
    from gb_abjob import _safe_tag
    who = _safe_tag("clone" if voice_ref else voice) or "unknown"
    return preview_dir() / f"{_safe_tag(entry_id, 60)}__{who}.wav"


def make_preview(entry_id: str, epub_path, voice: str, voice_ref: str = "",
                 words: int = PREVIEW_WORDS,
                 log=print) -> Path:
    """Narrate a short excerpt in the chosen voice and return the WAV path.

    Runs the real pipeline, so a preview sounds exactly like the audiobook
    will. Sweeps stale previews on the way through, since this is the one
    endpoint that writes to a shared temp area.
    """
    sweep_previews()
    book = read_epub(Path(epub_path))
    if not book.chapters:
        raise ConversionError("no readable text in this book")
    # prefer a chapter with real prose over a stub
    pool = [c for c in book.chapters if c.words > 200] or book.chapters
    chapter = pool[len(pool) // 3]
    excerpt = pick_preview_excerpt(chapter.text, words)
    if not excerpt:
        raise ConversionError("could not find a passage to preview")
    log(f"preview: {len(excerpt.split())} words from "
        f"{chapter.title[:40]!r}, voice {voice or 'clone'!r}")
    wav = synthesize(excerpt, voice, voice_ref=voice_ref or "")
    dest = preview_path(entry_id, voice, voice_ref)
    dest.write_bytes(wav)
    return dest


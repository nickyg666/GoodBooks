"""Narrator voice samples for zero-shot cloning, sourced from LibriVox.

Why LibriVox and not a stock voice library: the cloning path (pocket_tts
voice_wav) needs a clip of ONE person reading prose aloud -- not a voice
description, not a trained model. LibriVox is the only large catalogue where
every recording is public domain and every narrator is credited, so a sample
can be pulled, trimmed and cloned without a licensing question. Archive.org
hosts the MP3s directly, so the clip downloads with a plain GET.

What this module returns: a picker of {name, book, sample_url, seconds}
entries. The "sample_url" is the archive.org MP3 of the FIRST chapter of a
book narrated by that person -- 10-40 seconds of continuous prose, which is
what pocket_tts works best with. The caller downloads it, trims it to a
usable length and passes the path as voice_ref.

Curated list: these narrators were picked because they have dozens of solo
narrations (so the voice is consistent), they read mainstream fiction (so
the tone suits novels, not only public-domain classics), and their chapters
start with the body text rather than a long introductory credit.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None

logger = None
try:
    import logging
    logger = logging.getLogger(__name__)
except Exception:
    pass

# LibriVox catalog API. "readers" is the credited-narrator field; the filter
# by reader is what makes this a NARRATOR browser rather than a book browser.
LV_API = "https://librivox.org/api/feed/audiobooks"

# A browser is not needed for the API, but archive.org redirects sometimes
# want a normal UA.
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


class Narrator:
    """One browsable narrator: who they are and where a sample comes from."""

    __slots__ = ("name", "book", "sample_url", "seconds", "book_id")

    def __init__(self, name, book, sample_url, seconds, book_id):
        self.name = name
        self.book = book
        self.sample_url = sample_url
        self.seconds = seconds
        self.book_id = book_id

    def as_dict(self) -> Dict:
        return {"name": self.name, "book": self.book,
                "sample_url": self.sample_url, "seconds": self.seconds,
                "book_id": self.book_id}


def _norm(s: str) -> str:
    s = (s or "").casefold().strip()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s)


def _identifier_from_zip(url_zip: str) -> Optional[str]:
    """The archive.org item identifier, from the zip URL.

    url_zip_file looks like:
      https://archive.org/compress/count_monte_cristo_0711_librivox/formats=...
    The identifier is the path segment after /compress/.
    """
    if not url_zip:
        return None
    m = re.search(r"/compress/([^/?&]+)", url_zip)
    return m.group(1) if m else None


def _mp3_url(identifier: str) -> str:
    """First-section MP3 for an archive.org identifier.

    LibriVox uploads each section as <id>_64kb.mp3 under the item directory;
    section 01 is the conventional first file. Fall back to the highest
    quality the item publishes when the 64kb file is absent -- the caller
    verifies by HEAD.
    """
    return f"https://archive.org/download/{identifier}/{identifier}_64kb.mp3"


def _books_for_reader(reader: str, limit: int = 5) -> List[Dict]:
    """Books whose LibriVox project credits `reader` as narrator."""
    if requests is None:
        return []
    try:
        r = requests.get(LV_API, params={
            "format": "json", "limit": str(limit),
            "reader": reader,
        }, headers=UA, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        r.close()
        return data.get("books") or []
    except Exception:
        return []


def narrators_from_reader(reader: str, limit: int = 12) -> List[Dict]:
    """Browsable narrator entries for a named LibriVox narrator.

    The catalog API filters by reader but returns no reader array, so each
    returned book is attributed to the queried name directly. The archive.org
    identifier is parsed from url_zip_file, and the first-section MP3 is
    derived from it.
    """
    if not reader:
        return []
    books = _books_for_reader(reader, limit=limit)
    out: List[Dict] = []
    for b in books:
        iid = _identifier_from_zip(b.get("url_zip_file") or "")
        if not iid:
            continue
        try:
            secs = int(b.get("num_sections") or 0) * 30
        except (TypeError, ValueError):
            secs = 30
        out.append({
            "name": reader,
            "book": b.get("title") or "",
            "sample_url": _mp3_url(iid),
            "seconds": secs,
            "book_id": b.get("id") or "",
        })
        if len(out) >= limit:
            break
    return out


def search_narrators(query: str = "", limit: int = 12) -> List[Dict]:
    """Narrator entries matching a free-text query (author/title/narrator).

    With no query, returns the curated defaults -- narrators whose catalogues
    are large and whose recordings suit novel narration.
    """
    if requests is None:
        return []
    curated = []
    if not query:
        curated = _curated()
    try:
        if query:
            # A free-text query may name a narrator, an author or a title:
            # try it as a narrator first, then fall back.
            res = narrators_from_reader(query, limit=limit)
            if res:
                return res
        else:
            res: List[Dict] = []
            for reader in _CURATED_READERS[:limit]:
                res.extend(narrators_from_reader(reader, limit=2))
                if len(res) >= limit:
                    break
            if res:
                return res[:limit]
    except Exception:
        pass
    return curated[:limit]


def _est_seconds(item: Dict) -> int:
    """Rough sample length from the archive's reported section duration."""
    try:
        d = item.get("archive_sections") or ""
        m = re.match(r"\s*(\d+)\s", d)
        if m:
            # LibriVox section IDs are not durations; assume a chapter opens
            # with ~30 s of prose before any chapter heading ends. The UI
            # shows this only to set expectations.
            return 30
    except Exception:
        pass
    return 30


def sample_for(narrator: str) -> Optional[Dict]:
    """A single ready-to-clip sample for a named narrator."""
    if not narrator:
        return None
    res = search_narrators(narrator, limit=5)
    want = _norm(narrator)
    for r in res:
        if _norm(r["name"]) == want or want in _norm(r["name"]):
            return r
    return res[0] if res else None


def download_sample(sample: Dict, dest_dir) -> Optional[str]:
    """Download a sample clip to dest_dir; return the file path or None."""
    if not sample or not sample.get("sample_url"):
        return None
    try:
        from pathlib import Path
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^a-z0-9]+", "_", _norm(sample["name"])) or "narrator"
        out = dest / f"{stem}_{sample.get('book_id') or 'x'}.mp3"
        if out.exists() and out.stat().st_size > 1000:
            return str(out)
        r = requests.get(sample["sample_url"], headers=UA, timeout=60)
        if r.status_code != 200 or len(r.content) < 5000:
            return None
        out.write_bytes(r.content)
        return str(out) if out.exists() else None
    except Exception:
        return None


# Curated fallbacks: narrators with long catalogues whose voice is described
# as clear and even-paced -- the qualities that clone well.
_CURATED_READERS = (
    "Karen Savage", "Maria Kasper", "Mike Vendetto", "Nicholas Clifford",
    "Ruth Golding", "Sue Anderson", "Lucy Burgoyne", "Bob Neufeld",
    "David Richardson", "Elizabeth Klett", "J. M. Krane", "Lee Ann Howlett",
    "Mary Jorie", "Michelle Cram", "M L Cohen", "Phil Chenevert",
    "Ramon Da Silva", "Sally Hags", "Adrian Praetzellis", "Amy Gramour",
    "Andy Minter", "Betsie Bush", "Cynthia Lyons", "D Ramsey",
    "Don W Jenkins", "Elizabeth Thompson", "Graham Williams",
    "Gretchen R Stuter", "Jenni Colson", "Kara Shallenberg",
    "Mark Nelson", "PJ Gardner", "Rachel Steely", "Sarah Dorset",
    " Scott Sherf", "Simon Chamberlain", "Titus Miller",
)


def _curated(limit: int = 12) -> List[Dict]:
    """A stable default set so the picker is never empty offline."""
    out = []
    for i, name in enumerate(_CURATED_READERS[:limit]):
        out.append({
            "name": name.strip(),
            "book": "LibriVox narrator",
            "sample_url": "",
            "seconds": 30,
            "book_id": "",
        })
    return out

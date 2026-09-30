"""Add OpenLibrary as a metadata source.

WHY: the existing refresh could not fill in covers for 875 entries, and it
could not repair mangled titles, because its sources are Goodreads/Amazon
scrapes that already carry the same mangling. Measured:

  * Goodreads/Amazon ARE reachable (search 200, 192KB; cover CDN 200)
  * 802MB of covers are already cached, 3,934 files
  * but 875 entries have no cover and 4,231 have a raw-filename title

OpenLibrary answers all of it cleanly, verified live:
  * "Clementine Sara Pennypacker"  -> "Clementine", Sara Pennypacker,
                                     cover_i=7368598, image 200 / 35,619 B
  * "Bad Kitty School Daze Nick Bruel" -> "Bad Kitty school daze", cover
  * "Septimus Heap Magyk"          -> "Magyk (Septimus Heap)", cover
  * exact by ISBN: 9780441013593   -> "Dune", covers=[14565843, 284314]

So the failure was never the network: it was the absence of a source that
returns structured data. ISBN lookups are exact and are preferred whenever
an ISBN is present.

Nothing is written to library_metadata.json here -- this is the resolver
that the refresh will call.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

OL_SEARCH = "https://openlibrary.org/search.json"
OL_ISBN = "https://openlibrary.org/isbn/{isbn}.json"
OL_COVER = "https://covers.openlibrary.org/b/id/{cid}-L.jpg"
UA = {"User-Agent": "GoodBooks/1.0 (local library manager)"}

# words that mean the string is a scraped filename, not a clean title
# A title is "mangled" only if it shows these defects. A genuine published
# title may legitimately contain "Series, #N" or "(Series, Book 2)", so
# those are NOT defects on their own.
# Only unambiguous defects. A pattern for "run-together words" was tried
# and removed: it matched ordinary title casing ("The Bad Beginning"), so it
# rejected almost every genuine title.
# 1. semicolon-separated author fragments: "sara; pennypacker; marla"
_SEMI_AUTHORS = re.compile(r";\s*[A-Za-z]")

# 2. a glued "-Author" tail. Must be a hyphen with NO space after it, then
#    capitalised words (so "The Fenway Foul-Up (Ballpark...)" is fine), and
#    the author part must not be a bare series number.
_GLUED_AUTHOR = re.compile(
    r"[a-z\)]-(?!\s)\d*\s*(?:[A-Z][a-z]+(?:\s+[A-Z]\.?\s*[A-Za-z]+){1,3})"
    r"(?!\s*#)")

# 3. a lowercase word fused straight into a capitalised one, with no
#    separator: "The Winter SeaJekyll & Hyde".
_FUSED_WORD = re.compile(r"[a-z]{3}[A-Z][a-z]{2,}")


def _has_defect(t: str) -> bool:
    return bool(_SEMI_AUTHORS.search(t)
                or _GLUED_AUTHOR.search(t)
                or _FUSED_WORD.search(t))

# A title fused with a series fragment mid-string, e.g.
# "Clementine  Clementine Series, Book 1" -- only a defect when the words
# immediately before "Series" repeat the words that started the title.
_FUSED_SERIES = re.compile(
    r"^(.{4,40}?)\b[A-Za-z0-9'\- ]{2,40}\s+Series\b", re.S)

_STOP = {"the", "a", "an", "of", "and", "to", "in", "on", "for", "is", "vol",
         "book", "series", "part", "edition"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _title_tokens(s: str):
    return [t for t in _norm(s).split() if t and t not in _STOP]


def looks_clean_title(t: str) -> bool:
    """True when a title looks like a real book title, not a filename.

    Accepts the shapes real catalogues produce -- subtitles, "(Series, #N)",
    "Book 2" in a series name -- and rejects only genuine defects:
    semicolon-separated author fragments, a glued "-Author" tail, run-together
    words, or excessive length.
    """
    if not t or len(t) < 3:
        return False
    if ";" in t:                       # "sara; pennypacker; marla" author split
        return False
    if len(t) > 160:
        return False
    if _has_defect(t):
        return False
    return True


def _pick_best(docs, want_title: str, want_author: str) -> Optional[dict]:
    """Score OpenLibrary docs against what we already hold."""
    if not docs:
        return None
    want_t = set(_title_tokens(want_title))
    want_a = _norm(want_author)
    best, best_score = None, -1.0

    for d in docs:
        title = d.get("title") or ""
        if not title:
            continue
        t_tok = set(_title_tokens(title))
        if not t_tok:
            continue
        overlap = len(want_t & t_tok)
        denom = max(len(want_t | t_tok), 1)
        score = overlap / denom

        authors = d.get("author_name") or []
        if want_a and authors:
            a_tok = set(_norm(want_a).split())
            for name in authors:
                b_tok = set(_norm(name).split())
                if b_tok and a_tok:
                    score += 0.15 * (len(a_tok & b_tok) / max(len(a_tok | b_tok), 1))

        if score > best_score:
            best, best_score = d, score
    return best if best_score >= 0.34 else None


def fetch_by_isbn(isbn: str) -> Optional[dict]:
    """Exact lookup. Returns {title, authors, cover, isbn} or None."""
    import requests
    isbn = re.sub(r"[^0-9Xx]", "", isbn or "")
    if len(isbn) not in (10, 13):
        return None
    try:
        r = requests.get(OL_ISBN.format(isbn=isbn), headers=UA, timeout=25)
    except Exception as exc:
        logger.debug("openlibrary isbn lookup failed: %s", exc)
        return None
    if r.status_code != 200:
        return None
    try:
        j = r.json()
    except Exception:
        return None
    covers = j.get("covers") or []
    authors = j.get("authors") or []
    names = []
    for a in authors[:3]:
        if isinstance(a, dict) and a.get("name"):
            names.append(a["name"])
    return {
        "title": j.get("title") or "",
        "authors": names,
        "cover": OL_COVER.format(cid=covers[0]) if covers else "",
        "isbn": isbn,
        "year": j.get("publish_date") or "",
    }


def fetch_by_search(title: str, author: str = "", limit: int = 5) -> Optional[dict]:
    """Fuzzy search. Returns the best match or None."""
    import requests
    q = _norm(title)
    if not q:
        return None
    if author:
        q = f"{q} {author}"
    try:
        r = requests.get(OL_SEARCH, timeout=30, headers=UA, params={
            "q": q[:180], "limit": limit,
            "fields": "title,author_name,cover_i,isbn,first_publish_year",
        })
    except Exception as exc:
        logger.debug("openlibrary search failed: %s", exc)
        return None
    if r.status_code != 200:
        return None
    try:
        docs = r.json().get("docs") or []
    except Exception:
        return None
    best = _pick_best(docs, title, author)
    if not best:
        return None
    ci = best.get("cover_i")
    return {
        "title": best.get("title") or "",
        "authors": best.get("author_name") or [],
        "cover": OL_COVER.format(cid=ci) if ci else "",
        "isbn": (best.get("isbn") or [None])[0],
        "year": best.get("first_publish_year") or "",
    }


def fetch_cover_bytes(url: str, dest_dir: Path, key: str) -> Optional[Path]:
    """Download a cover to the cache and return its path, or None."""
    import requests
    if not url or not url.startswith("http"):
        return None
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = hashlib.md5(url.encode()).hexdigest()
    ext = "png" if url.lower().endswith(".png") else "jpg"
    out = dest_dir / f"{stem}.{ext}"
    if out.exists() and out.stat().st_size > 1024:
        return out
    try:
        r = requests.get(url, headers=UA, timeout=45, stream=True)
    except Exception as exc:
        logger.debug("cover download failed %s: %s", url, exc)
        return None
    if r.status_code != 200 or not r.content or len(r.content) < 1024:
        return None
    if r.content[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        return None
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.rename(out)
    return out

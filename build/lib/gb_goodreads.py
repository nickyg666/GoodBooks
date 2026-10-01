"""Resolve a single book from Goodreads, for the metadata repair.

The project ships scrape_genre_lists / scrape_list_detail /
scrape_series_books, but nothing that resolves ONE book from a title --
which is what the ~4,100 remaining mangled entries need.

Markup note: with a plain browser User-Agent, goodreads.com/search returns
the classic row markup (a.bookTitle / a.authorName), 6 results, ~106KB.
An earlier probe with a different Accept header and the author appended to
the query returned a newer BookCard layout with none of those classes. So
send a minimal, consistent header set and query the title alone.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

SEARCH = "https://www.goodreads.com/search"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/127.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
LAST_CALL = [0.0]
MIN_INTERVAL = 1.2          # be polite; Goodreads throttles aggressively


def _throttle() -> None:
    dt = time.time() - LAST_CALL[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    LAST_CALL[0] = time.time()


def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


_SERIES_TAIL = re.compile(
    r"\s*[-\u2013]\s*[^-]{0,60}$")


def shorten_query(title: str) -> str:
    """Turn a mangled filename into a query Goodreads can actually match.

    The stored titles are filenames with the title, a repeated series
    fragment and a trailing author all glued together. Feeding that to a
    search box returns nothing, so reduce it in a fixed order:

      1. cut a "Series, Book N" / "Series #N" tail
      2. cut a glued duplicate of the leading words
      3. drop a trailing "-Author" fragment
      4. collapse a repeated word, then hard-limit the length
    """
    q = _clean(title)
    if not q:
        return ""

    # 1. series tail. NOT anchored to the end: in the real data an author
    #    fragment follows it ("... Amber Brown Series, Book 1-Paula
    #    Danziger;"), so an end-anchored pattern never matched.
    #    Cut at the series marker, keeping the words before it.
    m = re.search(r"\b(?:Series|Collection|Trilogy)\b", q, re.I)
    if m and m.start() > 0:
        head = q[: m.start()].strip()
        # only cut if what we keep is still substantial
        if len(head.split()) >= 2:
            q = head

    # 2. a glued duplicate: the scraper pasted the title twice with no
    #    separator, so the text after the head must START with the head again
    words = q.split()
    for size in (8, 7, 6, 5, 4, 3):
        if len(words) <= size:
            continue
        head = " ".join(words[:size]).lower()
        if q.lower()[len(head):].lstrip().startswith(head):
            q = " ".join(words[:size])
            break

    # 3. trailing "-Author"
    m = re.search(r"\s*[-–]\s*([A-Za-z.;'\- ]{2,60})$", q)
    if m:
        head, tail = q[: m.start()].strip(), m.group(1).strip()
        if head and ";" not in head:
            q = head

    # 4. collapse a repeated word and cap the length
    q = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", q, flags=re.I)
    return q[:70].strip()


def search_books(title: str, limit: int = 6) -> List[Dict]:
    """Search Goodreads and return normalised book dicts."""
    import requests
    from bs4 import BeautifulSoup

    q = shorten_query(title)
    if not q:
        return []

    # 202 with an empty body is Goodreads' rate-limit acknowledgement, not an
    # empty result set. Back off and retry; only give up after the attempts.
    last_status = None
    for attempt in range(3):
        _throttle()
        try:
            r = requests.get(SEARCH, params={"q": q, "search_type": "books"},
                             headers=HEADERS, timeout=30)
        except Exception as exc:
            logger.debug("goodreads search failed for %r: %s", q, exc)
            time.sleep(4 * (attempt + 1))
            continue

        last_status = r.status_code
        if r.status_code == 200 and len(r.text) > 20000:
            break
        # 202/empty, or a short body: treat as throttled
        logger.debug("goodreads %s (%d bytes) for %r; backing off",
                     r.status_code, len(r.content), q)
        time.sleep(6 * (attempt + 1))
    else:
        if last_status not in (200,):
            return []
    if r.status_code != 200:
        logger.debug("goodreads search %s for %r", last_status, q)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    out: List[Dict] = []
    for a in soup.select("a.bookTitle")[:limit]:
        row = a.find_parent("tr")
        if row is None:
            continue
        author_el = row.select_one("a.authorName")
        img = row.select_one("img.imageField") or row.find("img")
        href = str(a.get("href") or "")
        out.append({
            "title": _clean(a.get_text(" ", strip=True)),
            "author": _clean(author_el.get_text(" ", strip=True)) if author_el else "",
            "url": urljoin("https://www.goodreads.com", href) if href else "",
            "cover": (img.get("src") or "") if img else "",
        })
    return out


def resolve(title: str, author: str = "") -> Optional[Dict]:
    """Best Goodreads match for a title, using the strict matcher.

    The strict matcher is used deliberately: downloading and emailing the
    wrong book to a Kindle is a user-visible failure, so a marginal match is
    treated as no match.
    """
    try:
        import gb_match
    except Exception:
        gb_match = None

    for cand in search_books(title):
        if gb_match is not None:
            ok, _reason = gb_match.is_match(title, cand.get("title", ""),
                                           author, cand.get("author", ""))
            if not ok:
                continue
        if author and cand.get("author"):
            # when we know the author, require agreement too
            from gb_match import token_set
            a, b = token_set(author), token_set(cand["author"])
            if a and b and not (a & b):
                continue
        return cand
    return None

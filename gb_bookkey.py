"""Book identity: a derived, stable key that groups one book across formats.

WHY THIS EXISTS

The library holds 4,837 files but fewer books. Measured 2026-10-04:

    4,837 resolvable records -> 4,739 distinct books, 94 keys holding >1 file

and the same book appears under different keys depending on which acquisition
produced it. Comparing metadata["title"] to the file stem:

    identical                   3,892   80.5%   <- raw filename, author glued on
    equal after normalisation      16    0.3%
    genuinely different           929   19.2%   <- a proper scrape

So title quality is BIMODAL. Any single field is therefore an unsafe key:

  * title alone SPLITS one book -- 'Big Little Lies' stores as
    'Big Little Lies-Liane; Moriarty' in the .mobi and 'Big Little Lies' in
    the .azw;
  * title alone MERGES two books -- 'In the Woods' is Robin Stevenson
    (.epub) and Tana French (.azw3);
  * id is exact but unique per FILE, so it can never group formats.

This module therefore derives a key and stores nothing authoritative.

THE DERIVATION

    title  -> display_title()            collapse a word repeated by the scraper
           -> strip a trailing "-<author blob>"  ONLY when it matches the
                                               author, never guessed by position
    author -> parse_authors()           existing and already correct:
                                            "west, tracey" -> Tracey West
                                            "jeff; smith"  -> Jeff Smith
    key    -> norm(title) + "|" + sorted(norm(author names))

Sorting the author names matters: co-author ORDER differs between two
acquisitions of the same book ("avi, brian floca" vs a variant), and an
unsorted join would split them.

VALIDATED against ground truth, not assumed:

    12 of 13 multi-format title clusters COLLAPSE to one key
    'in the Woods' correctly stays 2 keys (Stevenson / French)
    4,837 files -> 4,739 keys, 94 keys holding >1 file

KNOWN LIMITATIONS, stated rather than hidden

  * The author suffix is strippable for 75.5% of records. A further 6.0%
    have a dash that does not match the author, and 18.5% have no dash at
    all. Those titles keep their suffix and therefore key differently.
    A non-matching dash is left ALONE -- splitting on the last hyphen
    position would eat real words ("In the Fast Lane").
  * A record with an unparseable author keys with an empty author segment.
    That still groups correctly on title alone, which is the old behaviour,
    so nothing gets WORSE.
  * This is a derivation. It does not rewrite titles, does not deduplicate
    files, and does not touch the id scheme, because the last bulk title
    rewrite destroyed real titles ('Amy and the Missing Puppy' ->
    'Amy and the') and the id is referenced by every job file.

Everything here is pure: same inputs, same output, no I/O, no globals.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional, Sequence, Tuple

# A dash followed by the author's own words. Matched, never positional:
# real titles contain hyphens, so position-based splitting destroys words.
_AUTHOR_WORDS = re.compile(r"[a-z0-9']+")

# Words that appear in a trailing blob but are never part of a person's
# name, so they must not be required for a match.
_NAME_STOPWORDS = frozenset({
    "the", "a", "an", "of", "and", "or", "in", "on", "to", "for",
    "vol", "volume", "bk", "book", "part", "edition", "ed", "ret",
    "retail", "pdf", "epub", "mobi", "azw", "azw3", "kindle",
    "illustrated", " unabridged", "unabridged", "audiobook",
})


def norm_key(text: str) -> str:
    """Fold a string to a comparison key: case, accents, punctuation, space.

    Used for COMPARISON only. The stored title is never altered.
    """
    s = unicodedata.normalize("NFKD", text or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def title_words(text: str) -> List[str]:
    return _AUTHOR_WORDS.findall((text or "").lower())


def _meaningful(words: Iterable[str]) -> set:
    return {w for w in words if w not in _NAME_STOPWORDS and len(w) > 1}


def strip_author_suffix(title: str, author: str) -> Tuple[str, bool]:
    """Drop a trailing '-<author blob>' only when the blob really is the author.

    Returns (title, stripped).

    Matching rather than guessing is the whole point: the library convention
    is <Title>-<Author>, but real filenames also carry the author in FRONT of
    the title ('Joan Lowery Nixon The Dark and Deadly Pool-Joan Lowery Nixon'),
    and titles legitimately contain hyphens. A positional split on the last
    hyphen produced 'Amy and the' from 'Amy and the Missing Puppy' in an
    earlier attempt, which is why this refuses to split unless the tail
    actually contains the author's name.
    """
    t = (title or "").strip()
    if not t or "-" not in t:
        return t, False

    author_words = _meaningful(title_words(author))
    if not author_words:
        return t, False

    head, _, tail = t.rpartition("-")
    if not head.strip() or not tail.strip():
        return t, False

    tail_words = set(title_words(tail))
    # Every meaningful author word must appear in the tail, OR the tail must
    # be a superset of the author plus incidental words.
    if author_words <= tail_words:
        return head.strip(), True
    # Tolerate a middle initial dropped by the scraper: 'simone; st.; james'
    # vs a tail of 'Simone St. James' already matches above; this covers
    # 'james, simone' style blobs whose tail lacks a middle initial.
    strong = {w for w in author_words if len(w) > 3}
    if strong and len(strong & tail_words) >= max(1, len(strong) - 1):
        return head.strip(), True
    return t, False


def author_key(author: str, parse_authors=None) -> str:
    """Normalised, order-independent author identity.

    parse_authors is injected so this module never imports app.py or
    gb_authors at module scope; the caller passes the existing, tested
    implementation.
    """
    names: Sequence[str] = []
    if parse_authors is not None:
        try:
            names = parse_authors(author or "") or []
        except Exception:
            names = []
    if not names:
        # fall back to the raw blob, folded: still stable, just less precise
        return norm_key(author or "")
    folded = sorted({norm_key(n) for n in names if norm_key(n)})
    return "|".join(folded)


def book_key(title: str, author: str = "", *, parse_authors=None,
             display_title=None) -> str:
    """The derived identity for one book. Pure; stores nothing.

    parse_authors and display_title are injected from the host so this stays
    importable in isolation -- importing app.py boots a second service
    instance, and that has destroyed live library data three times.
    """
    t = display_title(title) if callable(display_title) else (title or "")
    t, _ = strip_author_suffix((t or "").strip(), author)
    tkey = norm_key(t)
    akey = author_key(author, parse_authors)
    return f"{tkey}|{akey}"


def split_book_key(key: str) -> Tuple[str, str]:
    """Inverse of book_key, for display and debugging."""
    t, _, a = (key or "").partition("|")
    return t, a


def label_for(key: str) -> str:
    """A human-readable label from a key, for reports and logs."""
    t, a = split_book_key(key)
    return f"{t.replace(' ,', ',')} ({a.replace('|', ' & ')})" if a else t

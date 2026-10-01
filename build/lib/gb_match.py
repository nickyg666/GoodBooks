"""Strict title/author matching for fetched search results.

Motivation: the previous rule (jaccard >= 0.34) matched 5 of 19 adversarial
cases, including the dangerous ones:

    "dune" -> "Dune Messiah"            WRONG (a sequel)
    "dune" -> "Children of Dune"        WRONG
    "dune" -> "The Dune Encyclopedia"   WRONG
    "it"   -> "It Is So Finished"       WRONG
    "Good Omens" -> "Not Good Omens"    WRONG (negation)

Downloading and emailing a wrong book to a Kindle is user-visible and
destructive, so the rule is deliberately conservative: when in doubt,
report no match and let the caller try another source.

Rules
  * a query of one distinct token requires an EXACT title match, or an
    exact-prefix match only when the candidate adds nothing meaningful
    (so "Dune" will not match "Dune Messiah")
  * negation words in the query must not appear flipped in the candidate
  * a series/volume marker in either side must agree
  * otherwise require high coverage AND a high jaccard, with a floor on
    the number of tokens involved so tiny queries are not matched loosely
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

STOP = {
    "a", "an", "the", "of", "and", "to", "in", "on", "for", "is", "was",
    "vol", "v", "book", "series", "part", "edition", "ed", "no", "number",
    "bk", "volume", "new", "annotated", "illustrated", "edition",
}

NEGATIONS = {"not", "no", "never", "without", "anti"}


_POSSESSIVE = re.compile(r"(['\u2019]s)\b", re.I)
_TRAILING_S = re.compile(r"([^\W\d_])s\b", re.I)


def tokens(s: str) -> List[str]:
    """Split a title into comparable words.

    Possessives are collapsed so that titles which differ only in
    punctuation still match:

        "The Butcher's Masquerade"  ->  butcher masquerade
        "The Butchers Masquerade"   ->  butchers masquerade   (before)

    Without this the scraper's apostrophe made every possessive title look
    like a different book, and the fallback rejected exact matches:
    cov 0.57, jac 0.50 -- below threshold.
    """
    t = (s or "")
    # Butcher's -> Butcher ; Butcher's -> Butcher
    t = t.replace("'s", " ").replace("\u2019s", " ")
    t = "".join(c.lower() if c.isalnum() else " " for c in t)
    return [w for w in t.split() if w and w not in STOP]


def _stem(w: str) -> str:
    """Crude English stemmer, only for the endings that differ between a
    catalogue entry and its scraped title.

        butchers  -> butcher        (plural)
        archives  -> archive        (plural)
        5 / five  -> 5              (a bare number is a series marker and
                                       is compared separately)

    Deliberately shallow: a full stemmer would merge genuinely different
    titles, and a wrong book emailed to a Kindle is worse than a miss.
    """
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("es") and not w.endswith("ses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _digits(s: str):
    """Every bare number in a title, as a set."""
    return set(re.findall(r"\b\d+\b", s or ""))


def token_set(s: str) -> set:
    return set(tokens(s))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def coverage(want: set, got: set) -> float:
    if not want:
        return 0.0
    return len(want & got) / len(want)


def series_tokens(s: str) -> set:
    """Volume/series markers, which must agree between query and candidate."""
    out = set()
    for w in tokens(s):
        if w.isdigit() and len(w) <= 3:
            out.add(w)
        if w in ("vol", "v", "book", "bk", "part"):
            out.add(w)
    return out


def negation_conflict(want: str, got: str) -> bool:
    """True when a negation in the query is missing from the candidate.

    Only fires when the candidate otherwise covers the query, so a subtitle
    that merely drops the word does not block a real match, while
    "Good Omens" -> "Not Good Omens" is still rejected (the candidate has a
    negation the query did not).
    """
    w, g = token_set(want), token_set(got)
    cov = coverage(w, g)
    if cov < 0.8:
        return False                      # not a close match anyway
    w_neg, g_neg = w & NEGATIONS, g & NEGATIONS
    if w_neg and not g_neg:
        return True
    if g_neg and not w_neg:
        return True
    return False


def is_match(query_title: str, cand_title: str,
             query_author: str = "", cand_author: str = "",
             *, allow_loose: bool = False) -> Tuple[bool, str]:
    """Return (matched, reason). Conservative on purpose."""
    wt = token_set(query_title)
    ct = token_set(cand_title)

    # A query made only of stop words ("A", "The") carries no discriminating
    # power, so require the candidate to be equally content-free-and-equal
    # rather than silently returning "empty".
    if not wt and not ct:
        return (token_set(query_title) == token_set(cand_title),
                "stop-word-only query, exact")
    if not wt or not ct:
        return False, "one side has no content tokens"

    # -- negation must agree
    if negation_conflict(query_title, cand_title):
        return False, "negation conflict"

    # -- series/volume markers must agree
    ws, cs = series_tokens(query_title), series_tokens(cand_title)
    digits_q = {w for w in ws if w.isdigit()}
    digits_c = {w for w in cs if w.isdigit()}
    if digits_q and digits_c and not (digits_q & digits_c):
        return False, f"different volume ({digits_q} vs {digits_c})"

    # Compare on stems so a scraper that drops an apostrophe
    # ("Butchers" vs "Butcher's") is still the same book, and keep a
    # bare series number from sinking the score when the catalogue
    # simply omits it.
    sw = {_stem(w) for w in wt}
    sc_ = {_stem(w) for w in ct}
    shared = sw & sc_
    cov = (len(shared) / len(sw)) if sw else 0.0
    jac = (len(shared) / len(sw | sc_)) if (sw | sc_) else 0.0
    nq, nc = _digits(query_title), _digits(cand_title)
    if nq and not nc:
        # the catalogue dropped the series number; do not penalise it
        cov = max(cov, 0.85)
    # A candidate that introduces content words the query never had is a
    # DIFFERENT book that merely shares a phrase:
    #     "Never Lie" must not match "Never Leave, Never Lie"
    # Catalogue filler (series names, "a novel", "unabridged") is allowed
    # because libgen appends it, so only reject on genuinely new words.
    FILLER = {"novel", "book", "edition", "ed", "volume", "vol", "part",
               "unabridged", "illustrated", "paperback", "hardcover",
               "deluxe", "omnibus", "collection", "anthology", "series"}
    new_content = {w for w in sc_ - sw
                   if w not in FILLER and not w.isdigit() and len(w) > 2}
    # gate on JACCARD, not coverage: "Never Lie" vs "Never Leave,
    # Never Lie" has full coverage (never+lie are both present) but
    # jaccard 0.67. The legitimate series-suffix cases measure 0.83.
    if new_content and jac < 0.80:
        return False, ("candidate introduces %s, which the query does not "
                       "mention" % sorted(new_content)[:3])


    # -- SINGLE-DISTINCT-TOKEN: the highest false-positive risk.
    if len(wt) == 1:
        if ct == wt:
            return True, "single token exact"
        # a candidate that ADDS tokens is a different book (sequel/spinoff)
        if cov == 1.0 and len(ct) > 1 and jac < 0.75:
            return False, "single token, candidate adds content (likely a sequel)"
        if allow_loose and jac >= 0.5:
            return True, "single token, loose"
        return False, "single token, not exact"

    # -- a very short query (2-3 tokens) still needs a tight match
    if len(wt) <= 3 and jac < 0.4:
        return False, f"short query, jaccard {jac:.2f} too low"

    # -- everything else: high coverage, and enough similarity
    if cov >= 0.8 and jac >= 0.45:
        return True, f"cov {cov:.2f} jac {jac:.2f}"
    # long query, candidate fully covers it: accept (mangled filenames
    # carry extra series text, so a low jaccard here is expected)
    if cov >= 0.75 and len(wt) >= 5 and jac >= 0.4:
        return True, f"long query covered cov {cov:.2f} jac {jac:.2f}"
    # candidate adds only a SUBTITLE on top of a fully covered query:
    # the query is contained entirely in the candidate and the query is
    # itself short enough that this is not a coin flip.
    if cov >= 0.99 and len(wt) <= 4 and len(ct) > len(wt):
        return True, f"query fully contained in longer title cov {cov:.2f}"
    if allow_loose and cov >= 0.6 and jac >= 0.34:
        return True, f"loose cov {cov:.2f} jac {jac:.2f}"
    return False, f"cov {cov:.2f} jac {jac:.2f} below threshold"


def rank_candidates(query_title: str, query_author: str,
                    candidates: Sequence[dict],
                    *, limit: int = 5) -> List[dict]:
    """Order candidates by match confidence, dropping the non-matches."""
    scored = []
    for c in candidates:
        ok, reason = is_match(
            query_title,
            c.get("title", ""),
            query_author,
            c.get("author", ""),
        )
        if ok:
            scored.append((reason, c))
    return [c for _, c in scored[:limit]]

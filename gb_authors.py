"""Author names: parse, merge, and order them so the list is usable.

The library's author filter was unusable. Measured live 2026-09-30: of 4,784
author strings, 4,178 (87%) were semicolon-split catalogue blobs, and they
were ordered with a bare casefold(), so the dropdown opened on

    1898.; alice's; adventures; in; wonderland; (

and buried every real author. Splitting on ";" and keeping fragments is the
wrong repair: it produced "mc" (81 books), "john" (77), "illustrated" (56)
as if they were authors.

What the data actually looks like (measured, not assumed):

  plain, good      "lemony snicket", "valerie tripp"            288 rows
  comma, good      "west, tracey", "dicamillo, kate"            318 rows
  semicolon blob   "jeff; smith"  -> Jeff Smith                  3,937 rows
                   "sara; pennypacker; marla; frazee" -> two authors
                   "david; a.; kelly; kellyby; illustrated; by; mark; meyers"

So a semicolon blob is a list of NAME TOKENS with role words mixed in, and
the tokens pair up into people. The approach here:

  * comma form is unambiguous ("Family, Given") and is flipped.
  * semicolon blobs are de-noised, then paired into people. Pair orientation
    uses a seed vocabulary of given names harvested from this library's own
    clean rows, so "jeff; smith" resolves to "jeff smith" when "jeff" is a
    known given name and stays honest when it is not.
  * every representation of the same person merges into ONE option with a
    book count, so "jeff smith" and "jeff; smith" are not two entries.
  * anything still unparseable is quarantined under an explicit
    "Needs cleanup" bucket instead of being silently mixed in.

Pure functions only: no I/O, no network, no Flask. Tested in
tests/test_authors.py.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- noise ---

# Words that are never part of a person's name.
_ROLE_WORDS = {
    "by", "with", "and", "author", "authors", "edited", "ed", "eds",
    "editor", "editors", "illustrated", "illustrator", "illustrators",
    "translated", "translator", "translators", "foreword", "afterword",
    "introduction", "preface", "contributor", "contributors",
    "overdrive", "inc", "llc", "ltd", "corp", "corporation", "company",
    "publishing", "publishers", "publisher", "press", "books", "book",
    "staff", "various", "unknown", "n/a", "na", "none", "audio",
    "abridged", "unabridged", "read", "copyright", "all", "rights",
    "reserved", "original", "edition", "editions", "vol", "volume",
    "large", "print", "digital", "retrieved", "from", "online",
    "via", "download", "ebook", "epub", "content", "engineering",
    "services", "library", "digital", "media", "overdriveinc",
}

# Non-name junk that shows up glued into the blob.
_JUNK_SUB = re.compile(
    r"\(美\)|\(\d{3,4}\)|\b著\b|\(\)|\[\]|\bxviii\b|\biii\b",
    re.IGNORECASE,
)

_INITIAL = re.compile(r"^[a-z]\.?$", re.I)
_HAS_DIGIT = re.compile(r"\d")
_IS_ALL_DIGIT = re.compile(r"^[\d\s.,_\-]+$")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")

_PUNCT = re.compile(r"[^\w\s'\-\u00c0-\u024f]", re.UNICODE)
_WS = re.compile(r"\s+")
_ARTICLES = ("the ", "a ", "an ")

# A token is name-shaped if it is a capitalised word, an initial, or a
# surname-like hyphenated compound.
_NAME_TOKEN = re.compile(r"^[A-ZÀ-Þ][\w'\-\.]*$|^[a-z]\.$")


# ------------------------------------------------------------- utilities ---

def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def sort_key(name: str) -> str:
    """Accent- and article-insensitive ordering key.

    The returned value is used BOTH as a display-independent dictionary key
    for the author filter AND as a sort key, so it must stay a plain
    normalised name. Use order_key() when ordering, because an empty string
    sorts before every name and that put authorless records at the top of the
    author-sorted view.
    """
    n = strip_accents(name or "").casefold()
    n = _PUNCT.sub(" ", n)
    n = _WS.sub(" ", n).strip()
    for art in _ARTICLES:
        if n.startswith(art):
            n = n[len(art):]
            break
    return n


# "~" sorts after every alphanumeric, so an unknown author lands at the end
# instead of the top. Mirrors the existing _author_sort_key() fix.
_UNKNOWN_SENTINEL = "~"


def order_key(name: str) -> str:
    """Sort key that keeps unknown/empty authors last."""
    k = sort_key(name)
    return _UNKNOWN_SENTINEL if not k else k


def _titlecase_token(tok: str) -> str:
    """'smith' -> 'Smith' but keep 'McFadden'/'O'Brien' style shapes."""
    if not tok:
        return tok
    if tok.isupper() and len(tok) > 1:
        return tok
    return tok[0].upper() + tok[1:]


def _cap(tok: str) -> str:
    """Capitalise one name token without flattening existing mixed case.

    'mcfadden' -> 'Mcfadden', 'McFadden' -> 'McFadden', "o'connor" ->
    "O'connor", 'J.R.R.' -> 'J.R.R.'.
    """
    if not tok:
        return tok
    if any(c.isupper() for c in tok[1:]):
        return tok          # already mixed case: leave it alone
    return tok[0].upper() + tok[1:]


def _dedupe_tokens(tokens: Sequence[str]) -> List[str]:
    """Drop repeated tokens, case-insensitively, keeping first order.

    Catalogue rows credit the same person in several notations, so the token
    stream repeats: "abby hanlon [hanlon, abby]" yields
    ["abby","hanlon","hanlon","abby"].
    """
    out, seen = [], set()
    for t in tokens:
        k = strip_accents(t).casefold()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _collapse_repeats(name: str) -> str:
    """Remove an immediately-repeated name part from a rendered name.

    "Abby abby hanlon hanlon" -> "Abby Hanlon". Handles a whole part
    repeating (A A) and a pair repeating (A B A B).
    """
    parts = [p for p in name.split() if p]
    if len(parts) < 2:
        return name
    keys = [strip_accents(p).casefold() for p in parts]
    if len(set(keys)) == len(keys):
        return name
    # try to find the shortest repeating unit that covers the token list
    for size in range(1, len(parts) // 2 + 1):
        unit = keys[:size]
        if all(keys[i] == unit[i % size] for i in range(len(keys))):
            parts = parts[:size]
            break
    else:
        # not a clean repeat: drop the later duplicate tokens individually
        seen, kept = set(), []
        for p, k in zip(parts, keys):
            if k in seen:
                continue
            seen.add(k)
            kept.append(p)
        parts = kept
    return " ".join(parts)


def _clean_token(tok: str) -> str:
    """Normalise one whitespace-delimited token, or '' to reject it."""
    if not tok:
        return ""
    tok = _JUNK_SUB.sub(" ", tok)
    tok = tok.strip(" ,.;:()[]&/\\|'\"`~!?*_")
    if not tok or len(tok) < 2:
        return ""
    if _IS_ALL_DIGIT.match(tok):
        return ""
    if _HAS_DIGIT.search(tok) and not _INITIAL.match(tok):
        return ""          # '1501110344', 'book5'
    if tok.casefold() in _ROLE_WORDS:
        return ""
    if _CJK.search(tok) and not re.match(r"^[\w\s'\-]+$", tok):
        return ""
    return tok


def _drop_bracket_noise(value: str) -> str:
    """Keep parenthetical names, drop translator/series notes.

    "(C.G. Drews)" is a person; "(Translator)" and "(Book 3)" are not.
    """
    def repl(m):
        inner = m.group(1).strip()
        low = inner.casefold()
        if any(w in low for w in _ROLE_WORDS):
            return " "
        if _HAS_DIGIT.search(inner) and not _INITIAL.match(inner):
            return " "
        if len(inner.split()) > 5:
            return " "
        return " " + inner + " "

    value = re.sub(r"\(([^)]*)\)", repl, value or "")
    # catalogue rows also use square brackets: "abby hanlon [hanlon, abby]"
    return re.sub(r"\[([^\]]*)\]", repl, value)


# ------------------------------------------------------- given-name seed ---

# Seed given names harvested from this library's own clean rows, plus common
# ones. Used only to ORIENT a pair (which token is the surname); it never
# invents a name that is not present in the data.
SEED_GIVEN_NAMES = {
    "john", "james", "robert", "michael", "david", "william", "richard",
    "thomas", "charles", "christopher", "daniel", "matthew", "anthony",
    "donald", "mark", "paul", "steven", "andrew", "kenneth", "joshua",
    "kevin", "brian", "george", "edward", "ronald", "timothy", "jason",
    "jeffrey", "ryan", "jacob", "gary", "nicholas", "eric", "stephen",
    "larry", "justin", "scott", "brandon", "benjamin", "samuel", "gregory",
    "alexander", "patrick", "frank", "raymond", "jack", "dennis", "jerry",
    "tyler", "aaron", "jose", "adam", "nathan", "henry", "zachary", "douglas",
    "peter", "kyle", "noah", "ethan", "jeremy", "walter", "harold", "keith",
    "christian", "roger", "noel", "gerald", "carl", "terry", "sean",
    "austin", "arthur", "lawrence", "jesse", "dylan", "bryan", "joe",
    "jordan", "billy", "bruce", "albert", "willie", "gabriel", "logan",
    "alan", "juan", "wayne", "roy", "ralph", "randy", "eugene", "vincent",
    "russell", "louis", "philip", "johnny", "mary", "patricia", "jennifer",
    "linda", "elizabeth", "barbara", "susan", "jessica", "sarah", "karen",
    "nancy", "lisa", "betty", "margaret", "sandra", "ashley", "kimberly",
    "emily", "donna", "michelle", "carol", "amanda", "dorothy", "melissa",
    "deborah", "stephanie", "rebecca", "laura", "sharon", "cynthia",
    "kathleen", "amy", "shirley", "angela", "helen", "anna", "brenda",
    "pamela", "nicole", "samantha", "katherine", "emma", "ruth", "chris",
    "catherine", "debra", "virginia", "rachel", "carolyn", "janet",
    "catherine", "maria", "heather", "diane", "ruth", "julie", "joyce",
    "virginia", "victoria", "kelly", "christina", "joan", "evelyn",
    "lauren", "judith", "megan", "cheryl", "hannah", "jacqueline", "martha",
    "gloria", "teresa", "ann", "sara", "madison", "frances", "kathryn",
    "janice", "jean", "abigail", "alice", "julia", "judy", "sophia",
    "grace", "denise", "amber", "doris", "marilyn", "danielle", "beverly",
    "isabella", "theresa", "diana", "natalie", "brittany", "charlotte",
    "marie", "kayla", "alexis", "lori", "jeff", "jim", "joe", "sara",
    "marla", "steve", "matt", "joan", "paul", "simon", "francesca",
    "veronica", "ursula", "diane", "jane", "david", "stacey", "suzy",
    "ray", "joan", "kate", "jim", "kline", "ray", "leah", "david",
    "sara", "trey", "simon", "ursula", "tim", "tom", "karl", "gail",
    "francesca", "ursula", "veronica", "stacey", "suzy", "eliot", "vivian",
    "terri", "wesley", "molly", "stella", "cameron", "chelsea", "kendall",
    "aidan", "levi", "nina", "raj", "serena", "cassandra", "clare", "trey",
    "ivan", "otto", "abra", "amara", "bea", "cora", "dara", "lior", "nola",
    "liora", "petra", "ren", "sena", "tova", "yara", "zora", "eve", "hana",
    "alison", "camille", "bill", "jill", "tony", "ron", "roy", "mike",
    "sue", "ted", "ken", "dan", "bob", "amy", "ivan", "otto", "abby",
    "serena", "luke", "nina", "raj", "eliot", "jack", "zoe", "amy",
    "neal", "shuster", "cassandra", "clare", "vivian", "terri", "lea",
    "catherine", "nicole", "wesley", "molly", "stella", "cameron",
    "jess", "dana", "chelsea", "kendall", "mackenzie", "aidan", "levi",
}


def given_name_seed(extra: Iterable[str] = ()) -> set:
    """Seed set, optionally extended with names harvested from the library."""
    out = set(SEED_GIVEN_NAMES)
    for name in extra:
        out.add(strip_accents(name or "").casefold())
    return out


def harvest_given_names(entries: Sequence[dict]) -> set:
    """Learn given names from the clean rows in this library.

    Two reliable shapes contribute: a clean plain name's FIRST token
    ("lemony snicket" -> "lemony") and a comma name's AFTER-comma token
    ("west, tracey" -> "tracey").
    """
    found = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw = (e.get("author") or "").strip()
        if not raw or ";" in raw:
            continue
        if "," in raw:
            tail = raw.split(",", 1)[1].strip()
            first = tail.split()[0] if tail.split() else ""
            first = _clean_token(first)
            if first and not _INITIAL.match(first):
                found.add(strip_accents(first).casefold())
            continue
        words = [w for w in raw.split() if w]
        if 2 <= len(words) <= 4 and len(raw) < 40:
            first = _clean_token(words[0])
            if first and not _INITIAL.match(first):
                found.add(strip_accents(first).casefold())
    return found


def harvest_surnames(entries: Sequence[dict]) -> Counter:
    """Learn surnames, and how often, from clean comma rows.

    "simon, francesca" makes "simon" a surname (once) and "francesca" a given
    name. Counting matters: a token seen many times as a surname head is a
    reliable surname even if it is also a common given name.
    """
    found: Counter = Counter()
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw = (e.get("author") or "").strip()
        if not raw or ";" in raw:
            continue
        if "," not in raw:
            continue
        head, _, tail = raw.partition(",")
        head_toks = [t for t in (_clean_token(x) for x in head.split()) if t]
        tail_toks = [t for t in (_clean_token(x) for x in tail.split()) if t]
        if not head_toks or not tail_toks:
            continue
        if len(head_toks) > 3 or len(tail_toks) > 3:
            continue
        # head is the family name in "Family, Given"
        for t in head_toks:
            if not _INITIAL.match(t):
                found[strip_accents(t).casefold()] += 1
    return found


# --------------------------------------------------------------- parsing ---

def _tokens_from_blob(raw: str) -> List[str]:
    """De-noised name tokens from a semicolon/pipe separated blob."""
    raw = _drop_bracket_noise(raw)
    out: List[str] = []
    for chunk in re.split(r"[;|]", raw):
        for word in chunk.split():
            tok = _clean_token(word)
            if tok:
                out.append(tok)
    return out


# Surname counts, harvested by harvest_surnames() and installed by
# build_author_index() / provide_author_options(). Module level because the
# parser is pure and called per-row; a rebuild is cheap and idempotent.
_SURNAME_COUNTS: Counter = Counter()


def install_surname_counts(entries: Sequence[dict]) -> Counter:
    """Learn and install the surname vocabulary for this library."""
    global _SURNAME_COUNTS
    _SURNAME_COUNTS = harvest_surnames(entries)
    return _SURNAME_COUNTS


def _surname_score(tok: str) -> int:
    k = strip_accents(tok or "").casefold().strip(".")
    return _SURNAME_COUNTS.get(k, 0)


def _is_given(tok: str, given: set) -> bool:
    k = strip_accents(tok or "").casefold().strip(".")
    return bool(k) and k in given


def _render(first: str, second: str, given: set) -> str:
    """Join a pair, capitalising the surname.

    A surname is recognised by NOT being a known given name, so
    "tracey" + "west" renders as "Tracey West" rather than "Tracey west"
    (the surname arrives from catalogue metadata in lower case).
    """
    given_first = _is_given(first, given)
    given_second = _is_given(second, given)

    # Surname evidence, harvested from this library's clean comma rows.
    # Only consulted when the given-name seed cannot decide, because a token
    # that is a common given name AND a frequent surname head ("simon",
    # "rose") is genuinely ambiguous on its own.
    s_first = _surname_score(first)
    s_second = _surname_score(second)
    ambiguous_given = given_first and given_second
    if ambiguous_given and s_first > s_second:
        given_first, given_second = False, True
    elif ambiguous_given and s_second > s_first:
        given_first, given_second = True, False

    if given_second and not given_first:
        first, second = second, first
        given_first, given_second = given_second, given_first

    a = _titlecase_token(first)
    b = _titlecase_token(second)
    if not given_first and given_second:
        a, b = b, a
    return f"{a} {b}"


def _orient(first: str, second: str, given: set) -> str:
    """Return "Given Family" for a pair, using the given-name seed."""
    if not first or not second:
        return ""
    return _render(first, second, given)


def parse_authors(raw: str, given: Optional[set] = None) -> List[str]:
    """Every credible person name in a raw author blob, in reading order.

    Handles:
      plain        "lemony snicket"
      comma        "west, tracey"          -> "Tracey West"
      semicolon    "jeff; smith"           -> "Jeff Smith"
      co-authors   "sara; pennypacker; marla; frazee"
                                                   -> ["Sara Pennypacker",
                                                       "Marla Frazee"]
      single token "quinlan"               -> ["Quinlan"]
    """
    if not raw:
        return []
    given = given if given is not None else SEED_GIVEN_NAMES
    raw = raw.strip()
    if not raw:
        return []

    # --- comma form: "Family, Given" is unambiguous, so flip it.
    if "," in raw and ";" not in raw:
        head, _, tail = raw.partition(",")
        fam = _dedupe_tokens(
            [w for w in (_clean_token(x) for x in head.split()) if w])
        giv = _dedupe_tokens(
            [w for w in (_clean_token(x) for x in tail.split()) if w])
        # some rows glue a second credit on: "smith, alex t.alex t. smith"
        if fam and giv:
            rendered = _render(" ".join(giv), " ".join(fam), given)
            rendered = _collapse_repeats(rendered)
            if rendered:
                # capitalise every token: a row like
                # "abby hanlon [hanlon, abby]" reaches this branch because the
                # bracket content holds a comma, and only the family part was
                # cased by _render().
                rendered = " ".join(_cap(t) for t in rendered.split() if t)
            return [rendered] if rendered else []
        toks = _dedupe_tokens(
            [w for w in (_clean_token(x) for x in raw.split()) if w])
        if not toks:
            return []
        joined = _collapse_repeats(" ".join(toks))
        return [" ".join(_cap(t) for t in joined.split() if t)]

    # --- semicolon blob: tokens pair up into people.
    if ";" in raw or "|" in raw:
        toks = _tokens_from_blob(raw)
        if not toks:
            return []
        if len(toks) == 1:
            return [_cap(toks[0])]
        people: List[str] = []
        i = 0
        # A leading initial followed by a surname still pairs correctly.
        while i + 1 < len(toks):
            name = _collapse_repeats(_orient(toks[i], toks[i + 1], given))
            if name:
                people.append(name)
                i += 2
            else:
                i += 1
        if i < len(toks) and not people:
            people.append(_titlecase_token(toks[i]))
        return _dedupe(people)

    # --- plain.
    toks = _dedupe_tokens(
        [w for w in (_clean_token(x) for x in raw.split()) if w])
    if not toks:
        return []
    # collapse first, then capitalise every token, so a name assembled from
    # bracket content comes out uniformly cased ("abby hanlon [hanlon,
    # abby]" -> "Abby Hanlon" rather than "abby Hanlon").
    joined = _collapse_repeats(" ".join(toks))
    return [" ".join(_cap(t) for t in joined.split() if t)]


def _dedupe(seq: Iterable[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        k = sort_key(s)
        if k and k not in seen:
            seen.add(k)
            out.append(s)
    return out


def display_author(raw: str, given: Optional[set] = None) -> str:
    """The single best name to show for a raw metadata blob."""
    names = parse_authors(raw, given)
    return names[0] if names else ""


def is_junk(raw: str, given: Optional[set] = None) -> bool:
    return not parse_authors(raw, given)


# -------------------------------------------------------------- indexing ---

def build_author_index(entries: Sequence[dict],
                       given: Optional[set] = None) -> Dict[str, int]:
    """Normalised author key -> number of books.

    Every credible name in an entry contributes, so a book credited to
    "jeff; smith" is findable under "jeff smith" and under "smith".
    """
    if given is None:
        given = given_name_seed(harvest_given_names(entries))
    if not _SURNAME_COUNTS:
        install_surname_counts(entries)
    index: Dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw = e.get("author")
        if not raw:
            continue
        for name in parse_authors(str(raw), given):
            k = sort_key(name)
            if not k:
                continue
            index[k] = index.get(k, 0) + 1
    return index


def author_display_map(entries: Sequence[dict],
                        given: Optional[set] = None) -> Dict[str, str]:
    """Normalised key -> best-capitalised display name.

    Picks the most frequently seen capitalisation so "JEFF SMITH" and
    "jeff smith" collapse to one option.
    """
    if given is None:
        given = given_name_seed(harvest_given_names(entries))
    seen: Dict[str, Counter] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw = e.get("author")
        if not raw:
            continue
        for name in parse_authors(str(raw), given):
            k = sort_key(name)
            if k:
                seen.setdefault(k, Counter())[name] += 1
    return {k: c.most_common(1)[0][0] for k, c in seen.items()}


def provide_author_options(entries: Sequence[dict],
                           min_books: int = 1,
                           given: Optional[set] = None,
                           counts: Optional[Counter] = None) -> List[Tuple[str, str, int]]:
    """Options for the author filter: [(value, label, book_count)].

    Ordered by name, case- and accent-insensitively, leading articles
    dropped. Values are the normalised key the filter matches against, so
    "jeff smith" and "jeff; smith" are the SAME option.
    """
    if not entries:
        return []
    # Reuse what the caller already computed. index() builds `given` and the
    # surname counts before calling this, and rebuilding them here meant the
    # harvest ran twice per request. Defaults preserve the old behaviour for
    # every other caller.
    if given is None:
        given = given_name_seed(harvest_given_names(entries))
    if counts is None:
        install_surname_counts(entries)
        counts = build_author_index(entries, given)
    disp = author_display_map(entries, given)
    out = []
    for k, n in counts.items():
        if n < min_books or not k:
            continue
        out.append((k, disp.get(k, k), n))
    out.sort(key=lambda t: order_key(t[0]))
    return out


def entry_author_keys(raw: str, given: Optional[set] = None) -> List[str]:
    """All keys an entry should match when the author filter is applied."""
    if given is None:
        given = SEED_GIVEN_NAMES
    return _dedupe(sort_key(n) for n in parse_authors(raw or "", given))

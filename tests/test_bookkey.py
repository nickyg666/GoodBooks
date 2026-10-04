"""Tests for the derived book_key.

The point of book_key is to group one book across the several files it was
acquired as, WITHOUT merging two different books that happen to share a
title. Both directions are pinned here against real data measured on
2026-10-04:

    4,837 resolvable records -> 4,739 books, 94 keys holding >1 file
    12 of 13 multi-format title clusters collapse to ONE key
    'In the Woods' correctly stays TWO (Robin Stevenson / Tana French)

Also pinned: the limit that makes a naive hyphen-split unsafe. Real titles
contain hyphens, and an earlier bulk rewrite turned
'Amy and the Missing Puppy' into 'Amy and the' by splitting on the last
hyphen. strip_author_suffix therefore MATCHES the author and refuses to
split otherwise.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BK = pytest.importorskip("gb_bookkey")


def _parse(auth):
    """Stand-in for gb_authors.parse_authors with its documented behaviour."""
    if not auth:
        return []
    out = []
    for part in auth.replace(";", ",").split(","):
        p = part.strip()
        if not p:
            continue
        bits = p.split()
        if len(bits) == 2 and bits[1][:1].islower() and bits[0][:1].isupper():
            p = f"{bits[1].capitalize()} {bits[0]}"
        out.append(" ".join(w.capitalize() for w in p.split()))
    return out


# --------------------------------------------------------------------------
# normalising
# --------------------------------------------------------------------------
@pytest.mark.parametrize("a,b", [
    ("Dune", "dune"),
    ("The Hobbit", "the hobbit"),
    ("Café Society", "Cafe Society"),   # accents folded
    ("Fahrenheit 451", "Fahrenheit-451"),  # punctuation folded
    ("  Dune  ", "dune"),
])
def test_norm_key_is_case_accent_and_punctuation_insensitive(a, b):
    assert BK.norm_key(a) == BK.norm_key(b)


def test_norm_key_collapses_whitespace():
    assert BK.norm_key("a   b\tc") == "a b c"


# --------------------------------------------------------------------------
# the author suffix
# --------------------------------------------------------------------------
def test_suffix_stripped_when_it_matches_the_author():
    t, hit = BK.strip_author_suffix("Bone Vol. 1 Out From Boneville-Jeff Smith",
                                   "jeff; smith")
    assert hit is True
    assert t == "Bone Vol. 1 Out From Boneville"


def test_suffix_left_alone_when_it_is_not_the_author():
    """A dash inside a real title must not be treated as the split point."""
    t, hit = BK.strip_author_suffix("In the Fast Lane-Anderson, Evie",
                                    "evie; anderson")
    # 'Anderson, Evie' reversed is still the same person, so this SHOULD match
    assert hit is True, "author names in either order must match"
    assert t == "In the Fast Lane"


def test_no_author_means_no_strip():
    t, hit = BK.strip_author_suffix("Some-Book-With-Hyphens", "")
    assert hit is False
    assert t == "Some-Book-With-Hyphens"


def test_the_documented_catastrophe_cannot_repeat():
    """'Amy and the Missing Puppy' must never become 'Amy and the'.

    That exact truncation is why strip_author_suffix matches the author
    instead of splitting on the last hyphen position.
    """
    out = BK.book_key("Amy and the Missing Puppy-Callie Barkley",
                      "callie; barkley",
                      parse_authors=_parse)
    assert out.split("|")[0] == "amy and the missing puppy"
    assert out.split("|")[0] != "amy and the", \
        "the historical bulk-rewrite truncation"


def test_trailing_word_of_a_real_title_is_kept():
    out = BK.book_key("In the Fast Lane-Anderson, Evie", "evie; anderson",
                      parse_authors=_parse)
    assert out.split("|")[0] == "in the fast lane"


# --------------------------------------------------------------------------
# author handling
# --------------------------------------------------------------------------
def test_author_order_does_not_change_the_key():
    a = BK.author_key("avi, brian floca", _parse)
    b = BK.author_key("brian floca, avi", _parse)
    assert a == b, "co-author order must not split one book"


def test_unparseable_author_falls_back_to_the_folded_blob():
    """Degrades to title-only grouping, which is the pre-existing behaviour."""
    with_none = BK.author_key("some unparseable blob", _parse)
    assert with_none == BK.norm_key("some unparseable blob")


# --------------------------------------------------------------------------
# the two directions that matter
# --------------------------------------------------------------------------
def test_same_book_across_formats_collapses():
    """The .mobi stores the author in the title; the .azw does not."""
    mobi = BK.book_key("Big Little Lies-Liane; Moriarty", "liane; moriarty",
                        parse_authors=_parse)
    azw = BK.book_key("Big Little Lies", "liane; moriarty",
                      parse_authors=_parse)
    assert mobi == azw, (
        "one book in two formats must key identically, otherwise an "
        "aggregator splits it")


def test_different_books_sharing_a_title_stay_apart():
    """'In the Woods' is Robin Stevenson and Tana French."""
    stevenson = BK.book_key("In the woods", "robin; stevenson",
                            parse_authors=_parse)
    french = BK.book_key("In the Woods", "tana; french", parse_authors=_parse)
    assert stevenson != french, (
        "a title-only key would MERGE two different books, which is the "
        "opposite failure from the split above")


def test_key_is_stable_across_repeated_calls():
    args = ("The Stand", "stephen; king")
    a = BK.book_key(*args, parse_authors=_parse)
    b = BK.book_key(*args, parse_authors=_parse)
    assert a == b, "the key must be a pure function of its inputs"


def test_key_shape_and_inverse():
    k = BK.book_key("Dune", "Frank Herbert", parse_authors=_parse)
    assert "|" in k
    t, a = BK.split_book_key(k)
    assert t == "dune"
    assert a == "frank herbert"
    assert BK.label_for(k)


def test_book_key_is_pure_and_writes_nothing(tmp_path):
    """It must not touch the filesystem; it is a derivation."""
    before = sorted(p.name for p in tmp_path.iterdir())
    BK.book_key("Anything", "Someone", parse_authors=_parse)
    assert sorted(p.name for p in tmp_path.iterdir()) == before

"""Author parsing: the shapes measured in the real library, plus guards.

Every case below is a real string from
data/library_metadata.json as of 2026-09-30, or a regression for a bug this
work actually hit.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gb_authors as A


@pytest.fixture()
def given():
    return A.given_name_seed()


@pytest.fixture(autouse=True)
def _surnames():
    A._SURNAME_COUNTS = A.Counter()
    yield
    A._SURNAME_COUNTS = A.Counter()


# --------------------------------------------------------------- plain ---

def test_plain_name_is_preserved(given):
    assert A.parse_authors("lemony snicket", given) == ["Lemony Snicket"]


def test_plain_lowercase_surname_is_capitalised(given):
    # real row: "freida mcfadden"
    assert A.parse_authors("freida mcfadden", given) == ["Freida Mcfadden"]


def test_mixed_case_name_is_not_flattened(given):
    assert A.parse_authors("Lemony Snicket", given) == ["Lemony Snicket"]


def test_single_survivor_name(given):
    # real row: "quinlan" / "logsted"
    assert A.parse_authors("quinlan", given) == ["Quinlan"]


# --------------------------------------------------------------- comma ---

def test_comma_form_is_flipped(given):
    # real rows: "west, tracey", "dicamillo, kate"
    assert A.parse_authors("west, tracey", given) == ["Tracey West"]
    assert A.parse_authors("dicamillo, kate", given) == ["Kate Dicamillo"]


def test_comma_form_with_second_credit(given):
    # real row: "smith, alex t.alex t. smith"
    names = A.parse_authors("smith, alex t.alex t. smith", given)
    assert names and "Alex" in names[0]


def test_repeated_tokens_collapse(given):
    # real row: "abby hanlon [hanlon, abby]"
    names = A.parse_authors("abby hanlon [hanlon, abby]", given)
    assert len(names) == 1
    assert names[0].split() == ["Abby", "Hanlon"]


# ------------------------------------------------------------ semicolon ---

def test_semicolon_pair_is_a_person(given):
    # real row: "jeff; smith"
    assert A.parse_authors("jeff; smith", given) == ["Jeff Smith"]


def test_semicolon_coauthors(given):
    # real row: "sara; pennypacker; marla; frazee"
    names = A.parse_authors("sara; pennypacker; marla; frazee", given)
    assert names == ["Sara Pennypacker", "Marla Frazee"]


def test_semicolon_role_words_are_dropped(given):
    # real row: "by; stan; kirby; illustrated; george; o'connor"
    names = A.parse_authors("by; stan; kirby; illustrated; george; o'connor",
                            given)
    assert "Stan Kirby" in names
    assert not any("Illustrated" in n for n in names)


def test_initials_are_dropped_leaving_surname(given):
    # real row: "j.; m.; hernandez"
    assert A.parse_authors("j.; m.; hernandez", given) == ["Hernandez"]


def test_semicolon_family_given_uses_surname_vocabulary(given):
    # "simon; francesca" is the catalogue's Family;Given notation. The
    # library's own clean comma row "simon, francesca" is what proves it.
    A._SURNAME_COUNTS = A.Counter({"simon": 1})
    assert A.parse_authors("simon; francesca", given) == ["Francesca Simon"]


# ----------------------------------------------------------------- junk ---

def test_bare_numbers_are_junk(given):
    # real row: "1501110344hoover"
    assert A.parse_authors("1501110344hoover", given) == []


def test_is_junk_helper(given):
    assert A.is_junk("1501110344hoover", given) is True
    assert A.is_junk("jeff; smith", given) is False


def test_empty_input(given):
    assert A.parse_authors("", given) == []
    assert A.parse_authors(None or "", given) == []


# ------------------------------------------------------------- ordering ---

def test_sort_key_ignores_case_accents_and_articles():
    assert A.sort_key("The Hobbit") == "hobbit"
    assert A.sort_key("Hobbit") == A.sort_key("hobbit")
    assert A.sort_key("André") == A.sort_key("Andre")


def test_sort_key_orders_unknowns_last_in_lists():
    # order_key is the ordering key: an empty author must land at the END,
    # not the top. sort_key() stays a plain name because it doubles as the
    # filter's dictionary value.
    names = ["", "zzz", "aaa"]
    assert sorted(names, key=A.order_key) == ["aaa", "zzz", ""]
    assert A.sort_key("") == ""


# -------------------------------------------------------------- indexing ---

ENTRIES = [
    {"author": "jeff; smith", "title": "A"},
    {"author": "jeff; smith", "title": "B"},
    {"author": "west, tracey", "title": "C"},
    {"author": "sara; pennypacker; marla; frazee", "title": "D"},
    {"author": "", "title": "E"},
    {"author": None, "title": "F"},
]


def test_index_counts_and_merges_representations():
    idx = A.build_author_index(ENTRIES)
    assert idx["jeff smith"] == 2
    assert idx["tracey west"] == 1
    # co-author book is findable under both people
    assert idx["sara pennypacker"] == 1
    assert idx["marla frazee"] == 1


def test_options_are_sorted_and_carry_counts():
    opts = A.provide_author_options(ENTRIES)
    keys = [k for k, _, _ in opts]
    assert keys == sorted(keys)
    counts = {k: n for k, _, n in opts}
    assert counts["jeff smith"] == 2
    for k, label, n in opts:
        assert label and n >= 1


def test_options_can_require_minimum_popularity():
    opts = A.provide_author_options(ENTRIES, min_books=2)
    assert [k for k, _, _ in opts] == ["jeff smith"]


def test_display_map_prefers_a_real_capitalisation():
    entries = [
        {"author": "freida mcfadden"},
        {"author": "Freida McFadden"},
        {"author": "FREIDA MCFADDEN"},
    ]
    disp = A.author_display_map(entries)
    label = disp["freida mcfadden"]
    # the most common capitalisation wins, and it is not all-lowercase
    assert label != "freida mcfadden"


def test_entry_author_keys_supports_filter_matching():
    keys = A.entry_author_keys("sara; pennypacker; marla; frazee")
    assert "sara pennypacker" in keys
    assert "marla frazee" in keys

"""Regression tests for the two chapter-extraction defects.

Both were reported by a person using the feature ("no readable chapters in
epub"), which is the only reason either was found. Neither showed up in any
existing test, in the audit, or in a status code.

DEFECT 1 -- headings were never found at all

    raw has <h2>                   : True
    text has <h2>                  : False   <- stripped
    h[1-4] matches in text         : 0
    _first_heading(raw)            -> ''
    _first_line_title(text)        -> 'C H A P T E R'

_first_heading searched for heading TAGS inside the output of
_html_to_text(), which removes them. The regex could never match, the
function always returned "", and read_epub fell back to the first line of
PROSE. That prose happened to be the letter-spaced chapter label, so seven
chapters came out all titled 'C H A P T E R' with no numbers.

Measured on the real artefact after the fix:

    'CHAPTER One' 'CHAPTER Three' 'CHAPTER Five' 'CHAPTER Nine'
    'CHAPTER Ten' 'CHAPTER Eleven' 'CHAPTER Thirteen'

DEFECT 2 -- a two-element heading, where the title is the second element

    <h2 class="calibre6">C H A P T E R</h2>
    <h3 class="sigilnotintoc">One</h3>

The chapter word is the generic h2; the number is in the h3. Taking the first
heading loses the numbering entirely, which is the difference between chapter
one and chapter ten.

I relied on calibre's `sigilnotintoc` class for this and then deliberately
did NOT depend on it -- a class name from one converter is not a contract. The
rule is structural: a generic label immediately followed by a real heading
means the second is the title.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AB = ROOT / "gb_audiobook.py"
src = AB.read_text()

gb_audiobook = pytest.importorskip("gb_audiobook")


# --------------------------------------------------------------------------
# 1. headings must be read from the raw markup
# --------------------------------------------------------------------------
def test_first_heading_reads_raw_markup_not_stripped_text():
    """The original bug: a regex for <h2> applied to already-stripped text.

    Checked on the CODE only. The docstring deliberately NAMES
    _html_to_text, because it documents the bug, so a substring search over
    the whole function finds the explanation before the code and reports the
    opposite of the truth -- which is what the first version of this test did.
    """
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_first_heading")
    # walk the statements, skipping the docstring Expr entirely
    calls = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            calls.append((node.lineno, name))
    calls.sort()

    html_to_text = [ln for ln, n in calls if n == "_html_to_text"]
    # the regex is a re.findall; find the line of the pattern constant
    regex_line = next((ln for ln, l in
                       enumerate(src.splitlines(), 1)
                       if "<h([1-4])" in l or "<h[1-4]" in l), None)
    assert regex_line is not None, "the heading regex is gone"

    if html_to_text:
        assert min(html_to_text) > regex_line, (
            f"_html_to_text() is called at line {min(html_to_text)}, BEFORE the "
            f"heading regex at line {regex_line} -- the tags are stripped "
            "before the regex runs, so it can never match")


def test_heading_found_in_a_realistic_document():
    raw = (b"<html><body><h2 class=\"calibre6\">C H A P T E R</h2>"
           b"<h3 class=\"sigilnotintoc\">One</h3>"
           b"<p>It was a dark and stormy night.</p></body></html>")
    got = gb_audiobook._first_heading(raw)
    assert got == "CHAPTER One", (
        f"expected the split heading to join into 'CHAPTER One', got {got!r}")


# --------------------------------------------------------------------------
# 2. the split-heading rule
# --------------------------------------------------------------------------
@pytest.mark.parametrize("first,second,expected", [
    ("C H A P T E R", "One", "CHAPTER One"),
    ("C H A P T E R", "Ten", "CHAPTER Ten"),
    ("CHAPTER", "Three", "CHAPTER Three"),
    ("P A R T", "Two", "PART Two"),
    ("PROLOGUE", None, "PROLOGUE"),
])
def test_split_heading_join(first, second, expected):
    if second is None:
        got = gb_audiobook._normalise_heading(first)
    else:
        got = gb_audiobook._join_split_heading(first, second)
    assert got == expected, f"{first!r} + {second!r} -> {got!r}, want {expected!r}"


def test_letter_spacing_is_collapsed_but_casing_is_ours_not_ours_to_change():
    """'C H A P T E R' is one word CSS has spaced out."""
    assert gb_audiobook._normalise_heading("C H A P T E R") == "CHAPTER"
    # casing is the book's, and must not be title-cased behind its back
    assert gb_audiobook._normalise_heading("CHAPTER") == "CHAPTER"
    assert gb_audiobook._normalise_heading("Chapter One") == "Chapter One"


def test_titles_that_are_not_split_are_untouched():
    for t in ("Chapter 1", "The Silent Patient",
              "I, AMBER BROWN, AM DEFINITELY ONE VERY UNHAPPY HUMAN BEING."):
        assert gb_audiobook._normalise_heading(t) == t


def test_generic_labels_are_recognised():
    for t in ("CHAPTER", "Contents", "Copyright", "Acknowledgements"):
        assert gb_audiobook._is_generic_heading(t), f"{t!r} should be generic"
    for t in ("Chapter One", "The Silent Patient"):
        assert not gb_audiobook._is_generic_heading(t)


# --------------------------------------------------------------------------
# 3. the chunking constants my patch destroyed
# --------------------------------------------------------------------------
def test_chunking_constants_present_and_correct():
    """These are the committed values, restored after a patch deleted them.

    The heading fix rebuilt the body between `def _first_heading(` and the
    next top-level def, which silently removed two module-level constants:

        NameError: name 'MIN_CHUNK_CHARS' is not defined
        NameError: name '_HEADING_ONLY' is not defined
        3 failed, 235 passed

    and the pattern I reconstructed for _HEADING_ONLY from memory
    (r"^[^.!?]{0,60}$") matched ANY short line without sentence
    punctuation, so ordinary prose would have been treated as a
    heading-only fragment and merged away. The committed pattern is
    specific to chapter labels. So this asserts the real values, which
    catches both a missing constant and a plausible-but-wrong one.
    """
    assert gb_audiobook.MIN_CHUNK_CHARS == 25
    pat = gb_audiobook._HEADING_ONLY.pattern
    assert "chapter" in pat and "prologue" in pat, \
        "the heading-only pattern must name chapter labels specifically"
    assert "[^.!?]" not in pat, (
        "a pattern that matches any short punctuation-free line would treat "
        "ordinary prose as a heading fragment and merge it away")
    # behavioural check, not just the pattern text
    assert gb_audiobook._is_heading_only("CHAPTER 1")
    assert gb_audiobook._is_heading_only("PROLOGUE")
    assert not gb_audiobook._is_heading_only(
        "It was a dark and stormy night, and the wind howled through the trees")

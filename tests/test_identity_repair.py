"""Regressions for calibre's crossed title/author fields.

Measured on 'The Button Bin-Allen; Mike.mobi': calibre's ebook-convert wrote
the two metadata fields CROSSED, producing

    title  = 'Mike Allen'       (the author)
    author = 'The Button Bin'   (the title)

which propagated into the finished M4B as

    {'title': 'Mike Allen', 'artist': 'The Button Bin',
     'album_artist': 'The Button Bin', 'album': 'Mike Allen'}

Getting this right took four attempts, and each earlier version had a
specific, identifiable flaw that these tests now pin:

  1. t == a only            -> misses the real case, fields are not equal
  2. exact string compare   -> 'Allen; Mike' != 'Mike Allen'
  3. returning `a` as the author -> ('The Button Bin', 'The Button Bin')
  4. a duplicate patch branch shadowed the fixed one, so the stale version
     kept winning even though the corrected code was present in the file
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

gb_extract = pytest.importorskip("gb_extract")


# --------------------------------------------------------------------------
# the loose comparison key: catalogue junk must not defeat equality
# --------------------------------------------------------------------------
def test_idkey_ignores_separators_case_and_word_order():
    assert gb_extract._idkey("Mike Allen") == gb_extract._idkey("Allen; Mike")
    assert gb_extract._idkey("Harlan Coben") == gb_extract._idkey("coben, harlan")
    assert gb_extract._idkey("") == ""
    assert gb_extract._idkey("Sarah; Waters") != gb_extract._idkey("Mike Allen")


def test_split_library_filename_uses_the_last_plausible_hyphen():
    # titles contain hyphens; the author tail is short and bracket-free
    assert gb_extract._split_library_filename(
        "The Button Bin-Allen; Mike") == ("The Button Bin", "Allen; Mike")
    assert gb_extract._split_library_filename(
        "Dune-Frank Herbert") == ("Dune", "Frank Herbert")
    assert gb_extract._split_library_filename("NoHyphenHere") == ("NoHyphenHere", "")
    # A hyphen inside a bracketed series tag must not be treated as the split
    t, a = gb_extract._split_library_filename(
        "Ruthless Fae [Zodiac Academy #2]-Caroline Peckham & Susanne Valenti")
    assert t.startswith("Ruthless Fae"), t
    assert "Caroline" in a, a


# --------------------------------------------------------------------------
# the repair itself
# --------------------------------------------------------------------------
def test_crossed_fields_are_unswapped():
    """The real calibre output: fields crossed but NOT equal."""
    t, a = gb_extract.repair_identity(
        "Mike Allen", "The Button Bin", "The Button Bin-Allen; Mike.mobi")
    assert t == "The Button Bin"
    assert gb_extract._idkey(a) == gb_extract._idkey("Mike Allen")


def test_repair_never_yields_title_equal_to_author():
    """Regression for flaw 3: returning `a` gave ('The Button Bin','The Button Bin')."""
    for title, author, fname in [
        ("Mike Allen", "The Button Bin", "The Button Bin-Allen; Mike.mobi"),
        ("Mike Allen", "Mike Allen", "The Button Bin-Allen; Mike.mobi"),
        ("The Button Bin", "Mike Allen", "The Button Bin-Allen; Mike.mobi"),
    ]:
        t, a = gb_extract.repair_identity(title, author, fname)
        assert gb_extract._idkey(t) != gb_extract._idkey(a), \
            f"({title!r},{author!r}) produced a self-titled book: {t!r}"


def test_healthy_identity_is_left_untouched():
    """A correct pair must survive verbatim -- no speculative rewriting."""
    for title, author, fname in [
        ("Dune", "Frank Herbert", "Dune-Frank Herbert.epub"),
        ("Never Lie", "Harlan Coben", "Never Lie-Harlan Coben.epub"),
        ("Fingersmith", "Sarah Waters", "Fingersmith-Sarah; Waters.azw3"),
    ]:
        t, a = gb_extract.repair_identity(title, author, fname)
        assert (t, a) == (title, author), f"{fname} was rewritten"


def test_missing_fields_are_filled_from_the_filename():
    t, a = gb_extract.repair_identity("Fingersmith", "", "Fingersmith-Sarah; Waters.azw3")
    assert t == "Fingersmith"
    assert gb_extract._idkey(a) == gb_extract._idkey("Sarah; Waters")


# --------------------------------------------------------------------------
# regression for flaw 4: a duplicated patch branch shadowing the fix
# --------------------------------------------------------------------------
def test_exactly_one_crossed_field_branch_exists():
    """A stale duplicate branch silently shadowed the corrected one.

    The patch script anchored on a later comment and INSERTED rather than
    replacing, so the file held two branch-1b blocks and the first (wrong)
    one always won. This fails if that ever happens again.
    """
    src = (ROOT / "gb_extract.py").read_text(encoding="utf-8")
    body = ("    if t and f_author and _idkey(t) == _idkey(f_author) "
            "and _idkey(f_author):")
    assert src.count(body) == 1, \
        f"found {src.count(body)} crossed-field branches; exactly 1 required"
    # and the surviving one must return the filename author, not `a`
    idx = src.index(body)
    ret = src[idx:idx + 220].splitlines()[1].strip()
    assert ret == "return f_title or t, f_author", \
        f"wrong return in the crossed-field branch: {ret!r}"


def test_drm_is_checked_before_the_size_guard():
    """An 80-byte DRM fixture once reported 'too small to be a book'.

    That sends the user hunting for a corrupt download when the real problem
    is encryption, so content classification must come first.
    """
    import tempfile
    d = Path(tempfile.mkdtemp())
    p = d / "locked.azw"
    b = bytearray(80)
    b[0:4] = b"CR!T"
    b[60:68] = b"BOOKMOBI"
    p.write_bytes(bytes(b))
    with pytest.raises(gb_extract.ExtractionError) as exc:
        gb_extract.read_book(p)
    assert "drm" in str(exc.value).lower(), str(exc.value)

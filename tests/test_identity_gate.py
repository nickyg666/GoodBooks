"""Identity gate: a book may only inherit metadata from a page about it.

The failure this locks down: a record with no stored title displays its
FILENAME, that string was sent to a Goodreads search, and the first result was
adopted wholesale -- cover, rating, description, genres. An unrelated book's
art then appeared on the entry, permanently and indistinguishable from a
correct one.
"""
import re
from pathlib import Path

APP = Path('/usr/local/bin/GoodBooks/app.py').read_text()


def _load_helpers():
    """Pull the two helpers out of app.py.

    app.py must never be imported: doing so has rebuilt library metadata as a
    side effect and destroyed records before.
    """
    start = APP.index('def apply_adopted_metadata(')
    end = APP.index('def build_library_entries()')
    ns = {
        're': re, 'Dict': dict, 'Any': object, 'List': list,
        'logger': type('L', (), {'debug': staticmethod(lambda *a, **k: None)})(),
    }
    exec(compile(APP[start:end], 'helpers', 'exec'), ns)
    return ns


NS = _load_helpers()
same = NS['titles_are_the_same_book']
apply_meta = NS['apply_adopted_metadata']


# ---------------------------------------------------------------- accept ---
def test_accepts_ordinary_title_drift():
    assert same("The Bad Beginning", "The Bad Beginning (A Series of Unfortunate Events, #1)")
    assert same("The Bad Beginning", "The Bad Beginning: Lemony Snicket")
    assert same("Harry Potter and the Sorcerer's Stone", "harry potter and the sorcerer's stone")
    assert same("The Hobbit", "The Hobbit (Middle-earth Universe, #0)")


def test_accepts_subtitle_difference():
    # a subtitle on one side only is the same book
    assert same("Harry Potter", "Harry Potter and the Chamber of Secrets")


# ---------------------------------------------------------------- reject ---
def test_rejects_filename_garbage():
    # the reported corruption: an untitled record whose filename-derived query
    # landed on somebody else's book
    assert not same("chapter00014x9qz", "When the Side Nigga Is His Brother")
    assert not same("asdf1234xyz", "The Great Gatsby")
    assert not same("page0002", "The Great Gatsby")
    assert not same("Cover", "Misery")


def test_rejects_different_books_that_share_an_article():
    # "the" is the commonest word in English titles. Mapping articles to
    # ordinals (an early version of this gate did) let any two titles that
    # shared one match on that single token.
    assert not same("The Bad Beginning", "The Narrow Road Between Desires")
    assert not same("The Mentor", "The Casual Vacancy")


def test_rejects_different_books():
    assert not same("Misery", "Firestarter")
    assert not same("Harry Potter and the Sorcerer's Stone", "The Lord of the Rings")


def test_empty_input_is_never_a_match():
    assert not same("", "1984")
    assert not same("1984", "")
    assert not same("", "")


# ------------------------------------------------------------------ gate ---
def test_untitled_record_refuses_a_web_cover():
    meta = {'title': '', 'author': 'sue; grafton'}
    written = apply_meta(meta, {'cover': 'https://example/abc.jpg', 'rating': '4.2'})
    assert written == []
    assert 'cover' not in meta
    assert meta.get('enrichment_note') == 'refused_no_identity'


def test_titled_record_accepts_a_cover():
    meta = {'title': 'Misery', 'author': 'stephen king'}
    written = apply_meta(meta, {'cover': 'https://example/def.jpg'})
    assert 'cover' in written
    assert meta['cover'] == 'https://example/def.jpg'


def test_cover_extracted_from_the_file_is_always_allowed():
    # bytes from this book's own EPUB cannot describe a different book
    meta = {'title': ''}
    written = apply_meta(meta, {'cover': 'data/covers/xyz.jpg'}, from_file=True)
    assert 'cover' in written


def test_empty_values_are_not_written():
    meta = {'title': 'Misery'}
    written = apply_meta(meta, {'cover': '', 'rating': None, 'genres': []})
    assert written == []
    assert meta == {'title': 'Misery'}


# ------------------------------------------------------------- wiring ------
def test_every_cover_write_goes_through_the_gate():
    """No enrichment path may write meta['cover'] directly.

    A direct assignment is how the wrong cover came back after the first fix:
    the gate existed but three call sites bypassed it.
    """
    unguarded = [ln for ln in APP.splitlines()
                 if re.search(r'meta\["cover"\]\s*=', ln)]
    assert not unguarded, (
        "meta['cover'] assigned directly, bypassing the identity gate:\n"
        + "\n".join(unguarded[:6]))


def test_goodreads_search_verifies_the_hit():
    body = APP[APP.index('def enrich_library_metadata_from_goodreads('):
               APP.index('def enrich_library_metadata_comprehensive(')]
    assert 'titles_are_the_same_book(' in body, \
        "the Goodreads search must check the result is the book we asked for, " \
        "not adopt the first hit of any kind"
    assert 'continue' in body
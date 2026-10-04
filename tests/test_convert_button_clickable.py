"""Regression test for the invisible Convert button.

MEASURED 2026-10-03 by hit-testing all 50 chips with elementFromPoint, which
is the only method that answers "can a person click this". Calling
b.click() in JavaScript bypasses hit-testing entirely, so it reported success
while the control was unclickable.

THE BUG

    button  offsetParent = .library-cover
    cover   overflow     = HIDDEN
    parent  = .library-cover

The chip was a child of .library-cover, which clips its overflow. So the chip
was CLIPPED -- never painted, therefore never the target of a click:

    document.elementFromPoint(button centre) -> DIV.card   (not the button)
    hit-test over 50 chips: blocked 7, by DIV.library-meta

Two earlier diagnoses were wrong and the fixes they prompted did nothing:

  * "the meta strip covers it" -- true about which elements overlap, but
    z-index and pointer-events only reorder or re-target PAINTED elements.
    They cannot make a clipped element receive a click. Measured before and
    after adding both rules: still blocked 7.
  * only 7 of 50, because .library-meta only exists on cards that have meta
    content -- so it read as "the button is sometimes missing" rather than
    "the button is never clickable".

THE FIX: the chip is a direct child of .card, absolutely positioned over the
bottom-left of the cover. The cover keeps its own link underneath for normal
click-to-open; the chip is a later sibling and so sits above it.

These assertions are structural on purpose. They cannot run a browser, but
they DO catch the exact regression, which is the chip ending up as a
descendant of the clipping element again.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "templates" / "library.html"
CSS = ROOT / "static" / "desktop.css"

tpl = TPL.read_text()
css = CSS.read_text()


def _chip_span():
    """The chip markup together with its format guard.

    Built from explicit tokens rather than one clever regex: the guard is
    `{% if _ab_fmt in audiobook_supported_formats %}` with plain spaces, and
    an earlier attempt with `{%-?` (dash-optional) did not match because the
    template never uses the dash form.
    """
    IF = "{% if _ab_fmt in audiobook_supported_formats %}"
    END = "{% endif %}"
    i = tpl.find(IF)
    assert i != -1, "the format guard `if _ab_fmt ...` was not found"
    j = tpl.find(END, i)
    assert j != -1, "the format guard has no endif"
    body = tpl[i:j + len(END)]
    assert "ab-start-btn" in body, "the guard does not contain the chip"
    return i, j + len(END), body


def test_chip_is_not_inside_the_clipping_cover():
    """The whole bug: overflow:hidden on .library-cover clipped the chip."""
    chip_at, chip_end, _ = _chip_span()

    # find every .library-cover block and whether the chip falls inside one
    for cm in re.finditer(r'<div class="library-cover[^"]*"[^>]*>', tpl):
        depth = 0
        cover_end = None
        for mm in re.finditer(r'<div\b[^>]*>|</div>', tpl[cm.start():]):
            tok = mm.group(0)
            depth += 1 if not tok.startswith("</") else -1
            if depth == 0:
                cover_end = cm.start() + mm.end()
                break
        assert cover_end is not None, "could not match a </div> for a cover"
        inside = cm.start() <= chip_at < cover_end
        assert not inside, (
            "the convert chip is inside a .library-cover block again. That "
            "element is overflow:hidden, so the chip is clipped and cannot "
            "be clicked -- measured elementFromPoint returned DIV.card, and "
            "z-index / pointer-events cannot rescue a clipped element.")


def test_chip_is_inside_the_book_card_loop():
    """It must render per book, so it must live in the `entries` loop.

    An earlier attempt anchored on the first .library-cover on the page,
    which belongs to a FOLDER card. The chip then rendered nothing at all
    (0 elements in the served HTML) because _ab_fmt is only defined inside
    the book-card markup.
    """
    chip_at, chip_end, body = _chip_span()
    book_loop = tpl.index("{# Book cards #}")
    assert chip_at > book_loop, (
        "the chip is above the book-card loop, so it is inside the folder "
        "markup and silently renders nothing (measured: 0 ab-start-btn in "
        "the served page)")
    assert "ab-start-btn" in body, "the chip lost its markup"


def test_chip_appears_exactly_once():
    assert tpl.count("ab-start-btn") == 2, (
        f"expected one chip (class + data-action), found "
        f"{tpl.count('ab-start-btn')}")


def test_card_is_a_positioning_context():
    """The chip is absolutely placed, so .card must be position:relative."""
    assert re.search(r"\.library-card\s*\{[^}]*position:\s*relative", css), \
        ".library-card must be position:relative or the absolutely "\
        "positioned chip has nothing to anchor to"


def test_chip_has_a_stacking_context():
    """The LAST matching rule wins in the cascade, so read that one."""
    rules = re.findall(r"\.library-card \.ab-start-btn\s*\{([^}]*)\}", css)
    assert rules, "no rule for .library-card .ab-start-btn"
    body = rules[-1]
    assert "z-index" in body, "the chip needs a z-index above the meta strip"
    assert "position: absolute" in body or "position: relative" in body, \
        "the chip must be positioned so it sits over the cover"


def test_meta_strip_cannot_swallow_clicks():
    assert re.search(
        r"\.library-card \.library-meta:not\(:has\([^)]*\)\)\s*\{\s*"
        r"pointer-events:\s*none", css), \
        "the meta strip must not intercept clicks meant for the chip"
    assert ".library-card .library-meta > *" in css, \
        "text inside the meta strip must stay selectable"


def test_no_route_handler_is_the_only_way_in():
    """A chip can exist, be visible, and still be unclickable.

    The handler was verified to exist by calling .click() directly, which
    skips hit-testing. So the DOM wiring must be asserted too.
    """
    assert re.search(
        r"document\.body\.addEventListener\('click'[\s\S]{0,400}?"
        r"closest\('\.ab-start-btn'\)", tpl), (
        "no click handler bound to .ab-start-btn; the chip will exist, look "
        "correct, and do nothing")

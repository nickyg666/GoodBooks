"""Regressions for the audiobook modal UI, all found in a real browser.

Verified against http://192.168.0.9:5000 (GoodBooks is on DAS -- 127.0.0.1
in a browser on .168 is Wyze-Bridge and redirects to /login, which cost me
one round of testing the wrong service).

Three defects, none of which any server-side test could have caught:

1. The Convert button was gated on filetype == 'epub', so 12 of 50 cards had
   no way to start an audiobook at all -- every mobi and every pdf:

       filetype     cards   button   no button
       epub            33       33           0
       mobi             9        0           9
       ?                6        5           1
       pdf              2        0           2

   The modal and the endpoint both worked; there was simply nothing to click.

2. The narrator dropdown rendered "[object Object]" for every voice.
   /audiobook/options returns objects ({name, size, added}); loadOptions()
   concatenated them as if they were strings, so both the label AND the
   submitted value were unusable -- a narrator could not be chosen at all.

3. The estimate read "About 17.0 h of audio" for a 201,709-word book -- off
   by ~25x. WORDS_PER_SECOND (3.3) is SYNTHESIS throughput and was being
   displayed as listening length. Measured from real output on DAS:
   24,159 words in 286.704 s = 84.26 words/sec of playback.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "templates" / "library.html"
APP = ROOT / "app.py"
AB = ROOT / "gb_audiobook.py"

tpl = TPL.read_text()
app = APP.read_text()
ab = AB.read_text()


# --------------------------------------------------------------------------
# 1. the Convert button must not be gated on epub alone
# --------------------------------------------------------------------------
def test_button_not_gated_on_epub_alone():
    assert "|lower == 'epub'" not in tpl, \
        "the Convert button is gated on filetype == 'epub', which removed "\
        "it from 12 of 50 cards (every mobi and every pdf)"


def test_button_gates_on_the_supported_list():
    assert "audiobook_supported_formats" in tpl, \
        "the button must gate on what the converter can actually read"


def test_index_passes_the_supported_formats():
    assert "audiobook_supported_formats=set(gb_extract.SUPPORTED_FORMATS)" in app
    # and the import must be at statement scope, before the call
    fn = app[app.index("def index("):app.index("def index(") + 200000]
    imp = fn.find("import gb_extract")
    use = fn.find("gb_extract.SUPPORTED_FORMATS")
    assert imp != -1 and use != -1, "gb_extract must be imported and used"
    assert imp < use, "the import must precede the use"


# --------------------------------------------------------------------------
# 2. voices must render from objects
# --------------------------------------------------------------------------
def test_no_voice_option_stringifies_an_object():
    """`'<option value="' + v + '">'` with v an object yields
    "[object Object]" for BOTH the label and the submitted value."""
    bad = re.findall(r"'<option value=\"' \+ [a-z] \+ '\">' \+ [a-z] \+ '</option>'",
                     tpl)
    assert not bad, f"{len(bad)} voice renderer(s) still stringify raw values"


def test_live_voice_renderer_reads_the_name_field():
    """loadOptions() is the renderer that RUNS; the /audiobook/voices one
    is dead code that loses the race. Both must be correct."""
    live = tpl[tpl.index("opts.voices || []"):][:400]
    assert "(o && o.name)" in live or "(v && v.name)" in live, \
        "loadOptions() must map voices by their .name field"


def test_voice_renderers_are_correct_in_both_places():
    assert tpl.count("v.map(function (o)") + tpl.count(
        "(opts.voices || []).map(function (o)") >= 2, \
        "both voice renderers should map objects"
# --------------------------------------------------------------------------
# 3. estimate: playback length, not synthesis time
# --------------------------------------------------------------------------
def test_playback_rate_is_separate_from_synthesis_rate():
    assert "WORDS_PER_SECOND_PLAYBACK" in ab, \
        "a single WORDS_PER_SECOND cannot express both 'how long to MAKE' "\
        "and 'how long it PLAYS' -- conflating them overstated a "\
        "201,709-word book as 17 hours instead of ~40 minutes"
    assert "WORDS_PER_SECOND = 3.3" in ab, \
        "the measured synthesis rate must be preserved for ETA"


def test_playback_rate_is_documented_as_measured():
    assert "84.26 words per second" in ab, \
        "the playback constant must record where the number came from"


def test_estimate_returns_both_durations():
    assert '"play_hours"' in app and '"synth_hours"' in app, \
        "the estimate must report listening length and render time "\
        "separately"
    assert "words_per_second_playback" in app, \
        "both rates should be exposed so the UI cannot re-conflate them"


def test_dialog_shows_listening_length():
    assert "d.play_hours" in tpl, \
        "the dialog must display play_hours, not the synthesis figure"
    assert "to render here" in tpl, \
        "the longer render time should be stated separately, not hidden"


def test_estimate_works_for_every_library_format():
    """It used AB.read_epub(), the EPUB-only parser, so 34.6% of the
    library answered 422 'could not read this book'."""
    # Bound the slice by the next route, not a fixed character count: the
    # reference sits ~20 lines in and a 2500-char window was cutting it off.
    start = app.index('@app.route("/audiobook/estimate")')
    nxt = app.find("\n@app.", start + 10)
    seg = app[start:nxt if nxt != -1 else start + 6000]
    # Strip comments before asserting. The explanation above the fix names
    # AB.read_epub() in prose, so a raw substring check trips on its own
    # documentation -- the same false alarm as the "only EPUB books can be
    # converted" string that only survived inside a comment.
    code = "\n".join(ln for ln in seg.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "AB.read_epub(" not in code, \
        "the estimate route must not use the EPUB-only parser"
    assert "EX.read_book(" in code, \
        "the estimate route must read any supported format"


def test_estimate_error_surfaces_the_reason():
    assert '"error": str(exc)[:200]' in app, \
        "a failed estimate must say WHY (unsupported format, DRM, etc.) "\
        "rather than a generic message"

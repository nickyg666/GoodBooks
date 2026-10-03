"""Re-point the estimate assertions at the plugin.

test_estimate_returns_both_durations, test_estimate_works_for_every_library_format
and test_estimate_error_surfaces_the_reason in test_audiobook_create_path.py
now cover these behaviours, because the estimate route moved out of app.py
into plugins/audiobook/. Duplicating the same three assertions against app.py
here would fail by construction -- app.py no longer owns the route.

What this file still owns, and still asserts: the things that live in
app.py and the service modules rather than in the plugin's handler --
the playback rate existing and being measured, the dialog template consuming
it, and the button gate. Those are unchanged by the refactor.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "templates" / "library.html"
AB = ROOT / "gb_audiobook.py"
APP = ROOT / "app.py"
PLUGIN = ROOT / "plugins" / "audiobook" / "__init__.py"

tpl = TPL.read_text()
ab = AB.read_text()
app = APP.read_text()
plugin = PLUGIN.read_text()


# --------------------------------------------------------------------------
# the rates (gb_audiobook owns these)
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


# --------------------------------------------------------------------------
# the dialog (template owns this)
# --------------------------------------------------------------------------
def test_dialog_shows_listening_length():
    assert "d.play_hours" in tpl, \
        "the dialog must display play_hours, not the synthesis figure"
    assert "to render here" in tpl, \
        "the longer render time should be stated separately, not hidden"


# --------------------------------------------------------------------------
# the Convert button gate (template + app.py kwarg)
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
    fn = app[app.index("def index("):app.index("def index(") + 200000]
    imp = fn.find("import gb_extract")
    use = fn.find("gb_extract.SUPPORTED_FORMATS")
    assert imp != -1 and use != -1, "gb_extract must be imported and used"
    assert imp < use, "the import must precede the use"


# --------------------------------------------------------------------------
# the estimate route now lives in the plugin
# --------------------------------------------------------------------------
def test_estimate_route_is_owned_by_the_plugin_not_the_host():
    """The refactor moved it deliberately; app.py must not re-add it."""
    tree = ast.parse(app)
    for n in ast.walk(tree):
        if not isinstance(n, ast.FunctionDef):
            continue
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and d.args and \
                    isinstance(d.args[0], ast.Constant) and \
                    str(d.args[0].value) == "/audiobook/estimate":
                raise AssertionError(
                    f"{n.name} still defines /audiobook/estimate in app.py; "
                    "the audiobook generator is a plugin")
    assert "/audiobook/estimate" in plugin, \
        "the plugin must provide the estimate route"


def test_estimate_uses_the_extractor_for_any_format():
    tree = ast.parse(plugin)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "audiobook_estimate":
            body = ast.get_source_segment(plugin, n) or ""
            assert "EX.read_book(" in body, \
                "the estimate must read any supported format, not EPUB only"
            assert "AB.read_epub(" not in body
            return
    raise AssertionError("audiobook_estimate not found in the plugin")


def test_estimate_reports_both_durations_from_the_plugin():
    tree = ast.parse(plugin)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "audiobook_estimate":
            body = ast.get_source_segment(plugin, n) or ""
            assert '"play_hours"' in body and '"synth_hours"' in body
            return
    raise AssertionError("audiobook_estimate not found in the plugin")

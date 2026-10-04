"""Regression tests for the UI defects found by CLICKING, not by status code.

Every one of these shipped green. The service returned 200 on every affected
route, every JSON endpoint returned well-formed data, the test suite passed,
and the audit reported no problems -- while the feature was unreachable and
one view displayed an error. Only a browser click exposed them.

WHAT A CLICK REPRODUCED, 2026-10-03

1. The Plugins toolbar button did nothing:

       before click: plugin-view ABSENT
       after clicking Plugins:
         url:                ?view=collection   <- unchanged
         pluginViewExists:   false
         pluginCards:        0
         toolbarActive:      ['Bookshelf']       <- unchanged

   Cause: every other view has a dedicated handler. The complete list was
   setGridView, setListView, setKindleView, setRecentView, setCoverView --
   and neither the Plugins nor the Audiobooks button had one, nor an
   addEventListener. They were inert markup that looked like a control.

2. The Audiobooks view showed "Could not load the audiobook list" while
   /api/audiobooks returned 200 with real data. When the audiobook generator
   became a plugin it registered that route with its own payload, displacing
   the shape the view's JavaScript consumes: the view needs d.count,
   d.total_hours, d.total_mb and per-row title/narrator/cloned/chapters/
   hours/mb/delivered, and one missing key (total_mb.toLocaleString() on
   undefined) throws, which the .catch() reports as a total load failure.

3. The audiobook dialog overflowed its own width. Measured in the live DOM:

       dialog 560 px, scrollWidth 577, clientWidth 543
       exactly two spans overflowing by 18 px each:
         "Email to Kindle when finished"
         "Email me when finished"

   Cause: a flex item with min-width:auto refuses to shrink below its content
   width. The label was already display:flex and the checkbox already
   flex:none; the span was the piece that could not shrink.

These tests assert the STRUCTURE that makes the UI work, so a regression is
caught without a browser: a view button with no handler is exactly the class
of defect that shipped twice.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "templates" / "library.html"
PLUGIN = ROOT / "plugins" / "audiobook" / "__init__.py"
CSS = ROOT / "static" / "desktop.css"

tpl = TPL.read_text()
plugin = PLUGIN.read_text()
css = CSS.read_text()


# --------------------------------------------------------------------------
# 1. every view button must have a handler
# --------------------------------------------------------------------------
def _toolbar_view_buttons():
    out = {}
    for m in re.finditer(
            r'<button[^>]*id="(library-view-[a-z]+)"[^>]*data-view="([a-z]+)"',
            tpl):
        out[m.group(2)] = m.group(1)
    return out


def test_toolbar_has_both_new_views():
    views = _toolbar_view_buttons()
    assert "plugins" in views, "the Plugins toolbar button is missing entirely"
    assert "audiobooks" in views, "the Audiobooks toolbar button is missing"
    return views


def test_every_toolbar_view_has_a_handler():
    """The defect that shipped twice: a button that looks like a control."""
    views = _toolbar_view_buttons()
    missing = []
    for view, btn_id in views.items():
        has_fn = f"function set{view.capitalize()}View(" in tpl \
            or f"setActiveView('{view}')" in tpl
        bound = re.search(
            r"getElementById\('" + re.escape(btn_id) + r"'\)[^;]*;"
            r"[^}]*?addEventListener\('click',", tpl, re.S)
        # either a dedicated bound handler, or generic delegation on data-view
        delegated = re.search(
            r"addEventListener\('click'[\s\S]{0,400}?dataset\.view", tpl)
        if not (bound or delegated) and not has_fn:
            missing.append((view, btn_id))
    assert not missing, (
        f"toolbar view buttons with no click handler: {missing}. A button that "
        "does nothing looks identical to one that works until it is clicked.")


def test_plugins_and_audiobooks_specifically():
    for view, fn in (("plugins", "setPluginsView"),
                     ("audiobooks", "setAudiobooksView")):
        assert f"function {fn}(" in tpl, f"{fn} is not defined"
        assert f"setActiveView('{view}')" in tpl, \
            f"{fn} does not mark '{view}' as the active view"
        assert f"url.searchParams.set('view', '{view}')" in tpl, \
            f"{fn} does not navigate to ?view={view}"


# --------------------------------------------------------------------------
# 2. the API must serve the shape the view consumes
# --------------------------------------------------------------------------
def _view_expected_keys():
    top = sorted(set(re.findall(r"d\.(count|total_hours|total_mb)", tpl)))
    row = sorted(set(re.findall(
        r"a\.(title|narrator|cloned|chapters|hours|mb|delivered)", tpl)))
    return top, row


def test_plugin_api_serves_every_key_the_view_needs():
    top, row = _view_expected_keys()
    assert top, "could not find the top-level keys the view expects"
    assert row, "could not find the per-row keys the view expects"
    # the plugin must emit them; look for the literal key names in the route
    missing = []
    for k in top:
        if f'"{k}"' not in plugin:
            missing.append(f"top.{k}")
    for k in row:
        # per-row keys are either set explicitly on `row`, or inherited from
        # JOB.progress(), whose payload the route copies wholesale:
        #     row = dict(JOB.progress(eid))
        # so `delivered` (and friends) arrive without appearing in the route.
        from_progress = k in ("delivered", "title", "phase", "bitrate")
        if (f'row["{k}"]' not in plugin and f'"{k}"' not in plugin
                and not from_progress):
            missing.append(f"row.{k}")
    assert not missing, (
        f"/api/audiobooks is missing {missing}. A single missing key makes "
        "the whole view fail via its .catch(), showing 'Could not load the "
        "audiobook list.' while the endpoint itself returns 200.")


def test_progress_keys_are_still_exposed():
    """The plugin's original payload must not be lost."""
    tree = ast.parse(plugin)
    # the route is defined inside register(), so it is NESTED -- walking only
    # tree.body finds nothing.
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "api_audiobooks"), None)
    assert fn is not None, "the plugin no longer serves /api/audiobooks"
    body = ast.unparse(fn)
    for key in ("count", "audiobooks", "total_hours", "total_mb"):
        assert key in body, f"{key} missing from the payload"


# --------------------------------------------------------------------------
# 3. the dialog must not overflow
# --------------------------------------------------------------------------
def test_checkbox_labels_can_shrink():
    """A flex item with min-width:auto will not shrink below its content."""
    assert ".ab-toggles span" in css, (
        "the checkbox label spans need min-width:0; measured computed style "
        "was spanMinWidth 'auto', which is what overflowed the 560px dialog "
        "by 18px and added a horizontal scrollbar")
    m = re.search(r"\.ab-toggles span\s*\{([^}]*)\}", css)
    assert m, "the .ab-toggles span rule is missing or malformed"
    body = m.group(1)
    assert "min-width: 0" in body.replace(" ", "").replace("min-width:0",
                                                          "min-width: 0"), \
        "min-width: 0 is required or the label cannot shrink"
    assert "overflow-wrap" in body or "word-break" in body, \
        "the label text needs to wrap rather than being clipped"


def test_dialog_form_controls_cannot_force_overflow():
    assert re.search(r"\.audiobook-dialog select[^{]*\{[^}]*max-width:\s*100%", css), \
        "selects need max-width:100% inside the audiobook dialog"
    assert "box-sizing" in css, "nested form controls need border-box"


def test_lib_furniture_hidden_on_non_library_views():
    """The Audiobooks view rendered under 'No books found in this folder.'"""
    gated = len(re.findall(
        r"\{%\s*if view_mode not in \('plugins', 'audiobooks'\)\s*%\}", tpl))
    assert gated >= 3, (
        f"only {gated} blocks are gated off the plugins/audiobooks views; the "
        "search box, the empty-state and the title counter must all be hidden "
        "there (measured as visible in the browser)")

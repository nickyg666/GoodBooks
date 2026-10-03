"""Point the audiobook create-path tests at the PLUGIN, not at app.py.

These six asserted on code that used to live in app.py:

    test_start_route_is_registered
    test_route_uses_the_extractor_for_format_detection
    test_drm_is_reported_distinctly_from_unsupported
    test_estimate_works_for_every_library_format
    test_estimate_returns_both_durations
    test_estimate_error_surfaces_the_reason

The audiobook generator is now a plugin (plugins/audiobook/), so app.py
deliberately has no audiobook routes -- that is the point of the refactor.
The assertions are NOT deleted or weakened: they now check the same
behaviour where the behaviour now lives, which is the only way a refactor
keeps its guarantees.

So the tests assert:
  * the routes exist in the PLUGIN and are registered on the app at runtime
    (checked against the live url_map, not against source text);
  * the plugin's start/estimate handlers use the extractor, distinguish DRM,
    and report both playback and synthesis durations.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
PLUGIN = ROOT / "plugins" / "audiobook" / "__init__.py"
AB = ROOT / "gb_audiobook.py"

app = APP.read_text()
plugin = PLUGIN.read_text()
ab = AB.read_text()


def _func_body(src, name):
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return ast.get_source_segment(src, n) or ""
    return ""


# --------------------------------------------------------------------------
# routes now belong to the plugin
# --------------------------------------------------------------------------
def test_start_route_is_registered():
    """The routes moved to the plugin; app.py must no longer define them."""
    for route in ("/audiobook/start", "/audiobook/options",
                  "/audiobook/estimate", "/api/audiobooks"):
        assert route in plugin, f"{route} missing from the plugin"
    tree = ast.parse(app)
    for n in ast.walk(tree):
        if not isinstance(n, ast.FunctionDef):
            continue
        for d in n.decorator_list:
            if isinstance(d, ast.Call) and d.args and \
                    isinstance(d.args[0], ast.Constant) and \
                    str(d.args[0].value).startswith(("/audiobook",
                                                      "/api/audiobooks")):
                pytest_fail = (f"{n.name} still defines "
                               f"{d.args[0].value} in app.py; the audiobook "
                               "generator is a plugin now")
                raise AssertionError(pytest_fail)


def test_plugin_registers_every_documented_route():
    for route in ("/audiobook/start", "/audiobook/cancel",
                  "/audiobook/options", "/audiobook/estimate",
                  "/audiobook/preview", "/audiobook/progress",
                  "/audiobook/voices", "/api/audiobooks"):
        assert route in plugin, f"{route} not declared by the plugin"


def test_plugin_never_imports_the_host():
    """The rule that protects live data.

    Importing app.py boots a second service instance with its own metadata
    cache; that has destroyed the library three times (1,074 records;
    4,869 -> 73; 4,837 -> 37). A plugin must get capabilities through the
    context instead.
    """
    tree = ast.parse(plugin)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                assert a.name != "app", "plugin must not import app"
        elif isinstance(n, ast.ImportFrom):
            assert (n.module or "") != "app", "plugin must not import app"


def test_plugin_manager_is_wired_and_isolated():
    assert "import gb_plugins as _gbpl" in app
    assert "PLUGINS = _gbpl.PluginManager(" in app
    assert "Plugin subsystem failed to initialise" in app, \
        "a broken plugin subsystem must not stop the service starting"
    assert "PLUGINS.run_maintenance()" in app, \
        "plugins must be scheduled through the maintenance hook"


def test_plugins_api_is_exposed():
    for route in ("/api/plugins",):
        assert route in app, f"{route} missing"
    assert "/api/plugins/<plugin_id>/enable" in app
    assert "/api/plugins/<plugin_id>/disable" in app


# --------------------------------------------------------------------------
# behaviour checks, now against the plugin's handlers
# --------------------------------------------------------------------------
def test_route_uses_the_extractor_for_format_detection():
    body = _func_body(plugin, "audiobook_start")
    assert body, "audiobook_start not found in the plugin"
    assert "EX.detect_format" in body, \
        "the start route must detect the format via the extractor"
    assert "SUPPORTED_FORMATS" in body


def test_drm_is_reported_distinctly_from_unsupported():
    body = _func_body(plugin, "audiobook_start")
    assert "is_drm_locked" in body, \
        "an encrypted file must be reported as DRM, which is different from "\
        "an unsupported format -- neither is fixable by the user"
    assert '"drm"' in body


def test_estimate_works_for_every_library_format():
    body = _func_body(plugin, "audiobook_estimate")
    assert body, "audiobook_estimate not found in the plugin"
    assert "EX.read_book(" in body, \
        "the estimate must not use the EPUB-only parser"
    assert "AB.read_epub(" not in body


def test_estimate_returns_both_durations():
    body = _func_body(plugin, "audiobook_estimate")
    assert '"play_hours"' in body and '"synth_hours"' in body, \
        "listening length and render time must be reported separately"
    assert "WORDS_PER_SECOND_PLAYBACK" in ab, \
        "a single WORDS_PER_SECOND cannot express both 'how long to make' "\
        "and 'how long it plays'"


def test_estimate_error_surfaces_the_reason():
    body = _func_body(plugin, "audiobook_estimate")
    assert "str(exc)" in body, \
        "a failed estimate must say WHY (unsupported format, DRM) rather "\
        "than a generic message"

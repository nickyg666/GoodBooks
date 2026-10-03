"""Regressions for audiobook creation, which was broken for every book.

Four defects stood between the UI and a queued job. All four were found by
POSTing to /audiobook/start, not by reading the code.

1. HTTPException was never imported.

       File "app.py", line 9725, in _ab_entry
         raise HTTPException(404, "book not found in library")
       NameError: name 'HTTPException' is not defined

   The intended 404 became a NameError, which Flask turned into a 500. This
   was the single reason audiobook creation did not work: it fired before
   the route could look at the book at all.

2. The exception was then also used with the WRONG API.

       raise HTTPException(404, "book not found in library")

   The second positional argument of HTTPException is `response`, not
   `description`. Werkzeug's HTTPException.__call__ expects a WSGI callable
   there, so it raised:

       TypeError: 'str' object is not callable
       File "werkzeug/exceptions.py", line 162, in __call__
       "The view function did not return a valid response ... but it was a
        HTTPException."

   NotFound(description=...) is the correct form and Flask renders it as a
   real 404.

3. The route still refused anything that was not .epub.

       if os.path.splitext(src_path)[1].lower() != ".epub":
           ... "only EPUB books can be converted"

   Reproduced verbatim against the live service. The multi-format extractor
   (gb_extract: epub/mobi/azw3/azw/pdf, verified on 30 real books) was never
   wired into this route, so 34.6% of the library was unreachable from the UI
   despite the work being done. The guard now asks gb_extract.detect_format,
   which also sniffs content and so can distinguish "drm" from "unsupported".

4. `user_obj_name_for(entry_id)` did not exist.

       NameError: name 'user_obj_name_for' is not defined
       File "app.py", line 10233, in audiobook_start

   Nothing in the codebase defined it. It was a legitimate FALLBACK -- the
   form's "user" field takes priority, this infers the owner when the form
   omits it -- so it is implemented rather than the call deleted. The library
   is laid out in per-user top-level directories
   (/mnt/8tbdas/GoodBooks/{sagey,Lorenzo,Listopia,nick-to-read}/), and entry
   ids carry a legacy '::' composite, so the path is normalised before the
   first segment is read, and the segment is matched against configured users
   rather than guessed.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
SRC = APP.read_text()
TREE = ast.parse(SRC)


def _func(name):
    for n in ast.walk(TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


def _body(name):
    """Source of a function, or '' when it is absent.

    Returning '' rather than None keeps the call sites free of None-checks
    and makes a missing function fail as a plain assertion with the real
    message rather than an AttributeError from ast.
    """
    fn = _func(name)
    if fn is None:
        return ""
    return ast.get_source_segment(SRC, fn) or ""


# --------------------------------------------------------------------------
# 1 + 2: the 404 path
# --------------------------------------------------------------------------
def test_http_exception_is_imported():
    assert "from werkzeug.exceptions import" in SRC, \
        "werkzeug.exceptions is not imported at all"
    assert re.search(r"from werkzeug\.exceptions import [^\n]*\bHTTPException\b",
                     SRC), "HTTPException is used but never imported"


def test_notfound_is_imported():
    assert re.search(r"from werkzeug\.exceptions import [^\n]*\bNotFound\b",
                     SRC), "NotFound is raised but never imported"


def test_no_two_arg_httpexception_raises():
    """HTTPException(404, 'msg') passes the message as `response`.

    HTTPException.__call__ expects a WSGI callable in that slot, so this
    raises TypeError: 'str' object is not callable instead of returning a
    404.
    """
    bad = re.findall(r"raise HTTPException\(\s*\d+\s*,\s*['\"]", SRC)
    assert not bad, \
        f"{len(bad)} HTTPException(code, str) raise(s) -- use NotFound(description=...)"


def test_ab_entry_raises_a_proper_notfound():
    body = _body("_ab_entry")
    assert body, "_ab_entry missing"
    assert "raise NotFound(" in body, \
        "_ab_entry must raise NotFound(description=...)"
    assert "description=" in body, \
        "NotFound must carry description=, not a positional response"


def test_import_precedes_use():
    imp = SRC.index("from werkzeug.exceptions import")
    use = min(i for i in [SRC.find("raise HTTPException"), SRC.find("raise NotFound")]
              if i != -1)
    assert imp < use, "the werkzeug import must precede its first use"


# --------------------------------------------------------------------------
# 3: format support reachable from the UI
# --------------------------------------------------------------------------
def test_no_epub_only_guard():
    live = "\n".join(ln for ln in SRC.splitlines()
                     if "only EPUB books can be converted" not in ln)
    assert "only EPUB books can be converted" not in live, \
        "the EPUB-only rejection is still live code (a comment quoting the "\
        "old error is fine, an executable return is not)"
    assert '!= ".epub"' not in live, \
        "a suffix comparison is still gating the audiobook route"


def test_route_uses_the_extractor_for_format_detection():
    body = _body("audiobook_start")
    assert body, "audiobook_start route missing"
    assert "gb_extract.detect_format" in body, \
        "the route must detect the format via gb_extract, not a suffix test"
    assert "SUPPORTED_FORMATS" in body, \
        "the route must answer with the supported format list"


def test_drm_is_reported_distinctly_from_unsupported():
    body = _body("audiobook_start")
    assert '"drm"' in body, \
        "an encrypted file must be reported as DRM, which is different "\
        "from an unsupported format -- neither is fixable by the user"


# --------------------------------------------------------------------------
# 4: the missing fallback
# --------------------------------------------------------------------------
def test_user_obj_name_for_exists():
    fn = _func("user_obj_name_for")
    assert fn is not None, \
        "user_obj_name_for is called by audiobook_start but never defined "\
        "(this was a NameError, i.e. a 500 on every request)"


def test_user_obj_name_for_defined_before_use():
    defs = [n.name for n in TREE.body if isinstance(n, ast.FunctionDef)]
    assert "user_obj_name_for" in defs, "not defined at module level"
    # the call site is inside audiobook_start, so it must resolve at runtime
    assert SRC.index("def user_obj_name_for(") < SRC.index(
        'if __name__ == "__main__":'), \
        "user_obj_name_for must be defined before the app starts serving"


def test_user_obj_name_for_normalises_the_legacy_separator():
    body = _body("user_obj_name_for")
    assert '"::"' in body or "'::'" in body, \
        "entry ids use a '<root>::<relpath>' composite; the helper must "\
        "normalise it or it will read the wrong path segment"
    assert "get_library_roots" in body, \
        "the owner must be derived relative to a configured library root"


def test_user_obj_name_for_returns_empty_rather_than_guessing():
    body = _body("user_obj_name_for")
    assert 'return ""' in body, \
        "an unknown owner must return \"\" -- auto-sending to the wrong "\
        "Kindle address is worse than not auto-sending"


# --------------------------------------------------------------------------
# the real regression: no undefined name on the create path
# --------------------------------------------------------------------------
def test_no_undefined_names_in_the_audiobook_create_path():
    """AST-checks every name the audiobook routes load.

    This is the defect class that actually broke creation twice: a missing
    import and a function that was called but never written. Neither
    py_compile nor a route-registration test can see either -- they are only
    reachable at request time.
    """
    import builtins

    module_names = set(dir(builtins))
    for n in ast.walk(TREE):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in ast.walk(n):
                if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                    module_names.add(t.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                module_names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.arg):
            module_names.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            module_names.add(n.name)

    targets = ["audiobook_start", "_ab_entry", "_ab_resolve_path",
               "_ab_user_options", "user_obj_name_for", "audiobook_cancel"]
    problems = {}
    for fn in [n for n in ast.walk(TREE)
               if isinstance(n, ast.FunctionDef) and n.name in targets]:
        local = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        for n2 in ast.walk(fn):
            if isinstance(n2, ast.Assign):
                for t in ast.walk(n2):
                    if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store):
                        local.add(t.id)
            elif isinstance(n2, ast.ExceptHandler) and n2.name:
                local.add(n2.name)
            elif isinstance(n2, ast.For):
                for t in ast.walk(n2.target):
                    if isinstance(t, ast.Name):
                        local.add(t.id)
        used = {x.id for x in ast.walk(fn)
                if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}
        missing = sorted(used - module_names - local)
        if missing:
            problems[fn.name] = missing

    assert not problems, (
        f"undefined names on the audiobook create path: {problems}. "
        "These are runtime-only failures -- a 500 the moment a user clicks "
        "Convert.")


def test_start_route_is_registered():
    """@app.post is a different decorator from @app.route; grepping for the
    latter missed this route entirely for part of this debugging session."""
    assert re.search(r'@app\.post\(\s*"/audiobook/start"', SRC), \
        "POST /audiobook/start is not registered"
    assert re.search(r'@app\.post\(\s*"/audiobook/cancel"', SRC), \
        "POST /audiobook/cancel is not registered"

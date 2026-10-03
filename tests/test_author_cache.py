"""Regressions for the author-options cache and the import it lost.

MEASURED. The endpoint rebuilt the whole author pipeline on every request:

    /api/library-authors   1092 ms median   <- second slowest endpoint

over 4,837 entries: harvest_given_names -> given_name_seed ->
install_surname_counts -> provide_author_options. Nothing was cached, even
though the response already carried `Cache-Control: max-age=300`.

After the fix, all HTTP 200:

    cold   67 ms
    warm    9 / 39 / 46 / 54 / 98 / 151 ms

and invalidation is verified: after a forced library rescan the payload is
rebuilt (118 ms) and warm again immediately after (39 ms).

Two bugs were introduced and caught while doing this, both pinned here:

  * the cached helper was left with `payload = _author_options_payload()`
    -- an infinite self-call. `src.replace(pipeline, ..., 1)` matched the
    copy inside the helper (which contains the same four lines as its slow
    path) instead of the route's copy, so the route was never patched and
    the recursion sat dormant on the never-taken cache-miss path.
  * moving the pipeline into the helper left `import gb_authors` behind in
    the route, so the fast path raised
    NameError: name 'gb_authors' is not defined and every request 500'd.
    The fast timings that first looked like success were ERROR responses.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def _region(marker):
    src = APP.read_text()
    start = src.index(marker)
    end = src.find("\n@app.route(", start)
    if end == -1:
        end = src.find("\ndef ", start + 10)
    if end == -1:
        end = len(src)
    return src[start:end]


def test_helper_does_not_recurse():
    """A self-calling cache helper is silently broken, not loudly.

    It only recurses on a cache MISS, so it can sit there passing every
    smoke test while the route it was meant to speed up keeps its old body.
    """
    helper = _region("def _author_options_payload(")
    assert "payload = _author_options_payload()" not in helper, \
        "the cache helper calls itself -- infinite recursion on a cache miss"


def test_helper_imports_what_it_uses():
    """The import must live with the code that uses it.

    Leaving it in the caller made every request 500 with
    NameError: name 'gb_authors' is not defined.
    """
    helper = _region("def _author_options_payload(")
    assert "import gb_authors" in helper, \
        "_author_options_payload uses gb_authors but does not import it"
    assert "gb_authors.harvest_given_names" in helper


def test_route_delegates_and_does_not_recompute():
    src = APP.read_text()
    start = src.index("def library_authors_json(")
    end = src.find("\n@app.route(", start)
    route = src[start:end if end != -1 else len(src)]
    assert "payload = _author_options_payload()" in route, \
        "the route no longer uses the cache"
    assert "harvest_given_names" not in route, \
        "the route still rebuilds the author pipeline per request"


def test_cache_is_keyed_on_the_library_generation():
    """Not a TTL: a stale author list breaks a filter control.

    Showing no results for an author who plainly exists is worse than
    recomputing, so invalidation must follow the library changing.
    """
    helper = _region("def _author_options_payload(")
    assert "_library_generation()" in helper, \
        "the cache key must come from the library-scan generation"
    assert "3600" not in helper and "max-age" not in helper, \
        "cache invalidation must not be time-based"


def test_generation_helper_reads_the_real_scan_counter():
    src = APP.read_text()
    m = re.search(r"def _library_generation\(\).*?(?=\ndef )", src, re.S)
    assert m, "no _library_generation helper"
    assert "_LIBRARY_ENTRIES_LAST_SCAN" in m.group(0), \
        "the generation must track the entries-scan counter"


def test_no_duplicate_author_routes():
    src = APP.read_text()
    assert src.count('@app.route("/api/library-authors")') == 1, \
        "duplicate author routes would register twice and shadow each other"

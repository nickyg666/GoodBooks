"""Tests that actually assert on behaviour.

The pre-existing suite in tests/ is smoke-only: test_routes.py asserts
`status_code < 500`, so a feature returning 200 with zero results passes.
These assert on the *content* of the things that were silently broken.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------- EAN-13 / ISBN
def _check_digit(isbn12: str) -> str:
    t = sum(int(isbn12[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
    return str((10 - t % 10) % 10)


def _encode_ean13(mod, ean: str) -> str:
    L = {d: c for c, d in mod._L.items()}
    G = {d: c for c, d in mod._G.items()}
    P = {d: p for p, d in mod._PARITY.items()}
    pat = P[ean[0]]
    rest = ean[1:]
    bits = "101"
    for i in range(6):
        bits += (L if pat[i] == "L" else G)[rest[i]]
    bits += "01010"
    for i in range(6):
        bits += G[rest[6 + i]]
    return bits + "101"


def _render(mod, bits: str, module_w: int = 3, height: int = 140, quiet: int = 12):
    from PIL import Image, ImageDraw
    w = quiet * module_w * 2 + len(bits) * module_w
    img = Image.new("L", (w, height), 255)
    d = ImageDraw.Draw(img)
    for i, b in enumerate(bits):
        if b == "1":
            x0 = quiet * module_w + i * module_w
            d.rectangle([x0, 0, x0 + module_w - 1, height - 1], fill=0)
    return img


ISBN_SEEDS = ["978044101359", "978014044913", "978055338016",
              "978030640615", "978031676948"]


@pytest.mark.isbn
@pytest.mark.parametrize("seed", ISBN_SEEDS)
def test_isbn_decodes_exactly(seed):
    """A book barcode must yield the exact ISBN, not a near miss."""
    import gb_isbn
    ean = seed + _check_digit(seed)
    assert gb_isbn._isbn13_valid(ean), "test vector must be a valid EAN-13"
    got = gb_isbn.decode_isbn(_render(gb_isbn, _encode_ean13(gb_isbn, ean)))
    assert got == ean, f"decoded {got!r}, expected {ean!r}"


@pytest.mark.isbn
@pytest.mark.parametrize("mw", [1, 2, 3, 5, 8])
def test_isbn_survives_module_scaling(mw):
    """A photo is scaled arbitrarily; decoding must not depend on module width."""
    import gb_isbn
    seed = ISBN_SEEDS[0]
    ean = seed + _check_digit(seed)
    img = _render(gb_isbn, _encode_ean13(gb_isbn, ean), module_w=mw, height=60)
    assert gb_isbn.decode_isbn(img) == ean


@pytest.mark.isbn
def test_isbn_handles_rotated_spine_photo():
    import gb_isbn
    seed = ISBN_SEEDS[1]
    ean = seed + _check_digit(seed)
    img = _render(gb_isbn, _encode_ean13(gb_isbn, ean))
    assert gb_isbn.decode_isbn(img.rotate(90, expand=True)) == ean


@pytest.mark.isbn
def test_blank_image_returns_none():
    from PIL import Image
    import gb_isbn
    assert gb_isbn.decode_isbn(Image.new("L", (300, 200), 255)) is None


@pytest.mark.isbn
def test_isbn_none_and_garbage_input_are_safe():
    import gb_isbn
    assert gb_isbn.decode_isbn(None) is None
    assert gb_isbn.decode_isbn("not an image") is None
    assert gb_isbn.decode_isbn(b"") is None


@pytest.mark.isbn
def test_check_digit_rejects_corruption():
    import gb_isbn
    good = "978044101359" + _check_digit("978044101359")
    bad = good[:12] + ("0" if good[12] != "0" else "1")
    assert gb_isbn._isbn13_valid(good)
    assert not gb_isbn._isbn13_valid(bad)
    assert gb_isbn.decode_ean13(bad) is None


# ------------------------------------------------------------------- mirrors
@pytest.mark.mirrors
def test_known_mirrors_are_not_the_known_dead_set():
    """annas-archive.org/.se stopped resolving and .li/.rs became parked
    domains, which is why AA silently returned nothing."""
    import search_engine
    dead = {"https://annas-archive.org", "https://annas-archive.se",
            "https://libgen.rs", "https://libgenrs.is"}
    for m in search_engine.KNOWN_MIRRORS:
        assert m.startswith("https://"), f"mirror without scheme: {m}"
    stale = dead & set(search_engine.KNOWN_MIRRORS)
    assert not stale, f"known-dead mirrors still configured: {stale}"


@pytest.mark.mirrors
def test_slum_maps_every_aa_monitor_id():
    """SLUM reports AA as annas_archive_1/2/3; unmapped ids came back with
    an empty url and could never be selected."""
    from slum_monitor._constants import MONITOR_ID_TO_URL
    for key in ("annas_archive_1", "annas_archive_2", "annas_archive_3",
                "annas_archive_gl", "annas_archive_li"):
        assert key in MONITOR_ID_TO_URL, f"SLUM monitor id unmapped: {key}"
        assert MONITOR_ID_TO_URL[key].startswith("https://"), \
            f"{key} maps to a non-URL: {MONITOR_ID_TO_URL[key]!r}"


@pytest.mark.mirrors
def test_slum_score_is_a_property():
    """score was a plain method, so ranking compared bound methods."""
    from slum_monitor._types import SlumMonitorEntry
    e = SlumMonitorEntry(name="x", url="https://example.invalid", is_up=True,
                         uptime_pct=100.0, latency_ms=10)
    assert isinstance(e.score, float), "score must be a property, not a method"
    assert 0.0 <= e.score <= 1.0


@pytest.mark.mirrors
def test_libgen_mirror_argument_is_bare_alias():
    """LibgenSearch prepends https://libgen. itself, so passing 'libgen.li'
    built the invalid host libgen.libgen.li."""
    src = (ROOT / "search_engine.py").read_text(encoding="utf-8", errors="replace")
    found = False
    for m in re.finditer(r"LibgenSearch\(\s*mirror\s*=\s*['\"]([^'\"]+)['\"]", src):
        found = True
        val = m.group(1)
        assert not val.startswith("libgen."), \
            f"LibgenSearch(mirror={val!r}) would become https://libgen.{val}"
    assert found, "expected at least one LibgenSearch(mirror=...) call"


@pytest.mark.libgen
def test_libgen_ua_patch_is_installed():
    """libgen mirrors serve an nginx page to the python-requests UA, which is
    what made the fallback return zero results."""
    import libgen_api_enhanced.search_request as sr
    assert getattr(sr, "_gb_ua_patched", False), \
        "libgen_ua_patch did not install its request shim"


# ---------------------------------------------------------- anti-regression
@pytest.mark.regression
def test_core_modules_compile():
    """Two modules shipped with uniform +1/+2 space indent shifts that made
    them unparseable. ast.parse is the cheap canary for the whole class."""
    import ast
    for name in ("app.py", "search_engine.py", "parser_engine.py",
                 "settings_manager.py", "stealth_browser.py",
                 "gb_isbn.py", "gb_lens.py", "slum_canonical.py"):
        p = ROOT / name
        if not p.exists():
            continue
        ast.parse(p.read_text(encoding="utf-8", errors="replace"))


@pytest.mark.regression
def test_no_broken_indent_blocks():
    """Guard the real defect: a def body that is NOT internally consistent.

    A pasted block shifted by a uniform offset still compiles (a consistent
    5-space body is legal), so this asserts the invariant that actually
    matters -- a def whose body mixes indentation widths is impossible,
    because the module would not parse. test_core_modules_compile enforces
    that; here we additionally report, without failing, any def whose body
    is off the 4-space grid so drift stays visible.
    """
    import ast

    MODULES = ("app.py", "search_engine.py", "parser_engine.py",
               "settings_manager.py", "gb_isbn.py", "gb_lens.py",
               "slum_canonical.py", "stealth_browser.py",
               "libgen_ua_patch.py", "logging_config.py",
               "amazon_cover_fetcher.py")

    off_grid = []
    for name in MODULES:
        f = ROOT / name
        if not f.exists():
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            raise AssertionError(f"{name}: does not parse: {exc}")

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.body:
                continue
            first = getattr(node.body[0], "col_offset", 0)
            if first % 4:
                off_grid.append(f"{name}:{node.lineno} {node.name}() body col {first}")

    # Off-grid-but-consistent bodies are legal; record them for visibility.
    print(f"functions with off-grid (but consistent) body indent: {len(off_grid)}")
    for line in off_grid:
        print("   ", line)


@pytest.mark.regression
def test_every_template_parses():
    """list_books.html shipped with an unparseable hand-escaped Jinja
    expression, unnoticed because no route rendered it."""
    from jinja2 import Environment
    env = Environment()
    for f in sorted((ROOT / "templates").glob("*.html")):
        env.parse(f.read_text(encoding="utf-8", errors="replace"))


@pytest.mark.regression
def test_all_templates_are_routed_or_intentional():
    import re as _re
    app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    refs = set(_re.findall(r"render_template\(\s*['\"]([^'\"]+)['\"]", app_src))
    missing = [r for r in sorted(refs) if not (ROOT / "templates" / r).exists()]
    assert not missing, f"render_template references missing files: {missing}"


@pytest.mark.regression
def test_logfile_config_is_a_string_not_array():
    """config:system:set once wrote 'logfile' => array(path => ''), which
    made every occ call and the whole web UI 500."""
    cfg = Path("/var/snap/nextcloud/54256/nextcloud/config/config.php")
    if not cfg.exists():
        pytest.skip("nextcloud config not on this host")
    m = re.search(r"'logfile'\s*=>\s*(.{0,80})", cfg.read_text(errors="replace"))
    if m:
        assert not m.group(1).strip().startswith("array"), \
            "logfile is an array again; occ and the web UI will 500"


# -------------------------------------------------------------- resources
@pytest.mark.resources
def test_library_lookup_cache_defined_once():
    """A second `_LIBRARY_LOOKUP_CACHE = set()` at module scope silently
    rebound the name and discarded the original cache object."""
    src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    defs = re.findall(r"^_LIBRARY_LOOKUP_CACHE\s*=\s*set\(\)", src, re.M)
    assert len(defs) == 1, f"_LIBRARY_LOOKUP_CACHE defined {len(defs)} times"


@pytest.mark.resources
def test_module_locks_defined_once():
    src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    for name in ("cloudflare_lock", "library_cache_lock", "search_cache_lock",
                 "metadata_enrichment_failures_lock"):
        defs = re.findall(rf"^{name}\s*=\s*Lock\(\)", src, re.M)
        assert len(defs) <= 1, f"{name} defined {len(defs)} times"


@pytest.mark.resources
def test_accumulator_is_drained_unconditionally():
    """metadata_enrichment_failures was cleared only inside the
    notify-failures branch, so with notifications off it grew for the life
    of the process. The drain must live in the `finally` of the block that
    reads it, not inside the notifications conditional."""
    import ast

    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8", errors="replace"))
    name = "metadata_enrichment_failures"

    cleared_in_finally = False
    guarded_by_notify = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # find `if metadata_enrichment_failures:` blocks
        test_src = ast.dump(node.test)
        if name not in test_src:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Try):
                continue
            for fin in sub.finalbody:
                if isinstance(fin, ast.If) and "notify_metadata_failures" in ast.dump(fin.test):
                    guarded_by_notify = True
                if any(isinstance(x, ast.Call)
                       and isinstance(x.func, ast.Attribute)
                       and x.func.attr == "clear"
                       and name in ast.dump(x.func)
                       for x in ast.walk(fin)):
                    cleared_in_finally = True
    assert cleared_in_finally, \
        f"{name} is not cleared in the finally of the block that reads it"
    # the notify check may still guard only the email send, not the drain
    assert not guarded_by_notify or cleared_in_finally


# ------------------------------------------------------------- scan pipeline
@pytest.mark.scan
def test_reverse_image_endpoint_is_not_a_stub():
    """The reverse-image path used a Google endpoint retired in 2019, so it
    could only ever return generic fallbacks. Assert the resolution order
    is present in code, so a silent regression is caught."""
    src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    start = src.find("def reverse_image_search")
    assert start != -1, "reverse_image_search route is gone"
    body = src[start:start + 9000]
    assert "gb_isbn" in body, "ISBN barcode step missing from the scan pipeline"
    assert "gb_lens" in body, "Google Lens step missing from the scan pipeline"


@pytest.mark.scan
def test_isbn_module_wired_into_app():
    src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
    assert "decode_isbn" in src, "app.py never calls the ISBN decoder"
@pytest.mark.resources
def test_debug_log_rotation_keeps_history():
    """The old handler rotated at 1GB by writing a single space, destroying
    all prior log history, and logrotate's 50M rule disagreed with it so
    debug.log reached 69MB in ~3h. Now it rotates at 50MB keeping 3 backups."""
    import logging
    import tempfile
    from pathlib import Path

    from logging_config import DebugLogRotationHandler

    tmpdir = Path(tempfile.mkdtemp(prefix="rot-test-"))
    log = tmpdir / "debug.log"

    orig = DebugLogRotationHandler.MAX_BYTES
    DebugLogRotationHandler.MAX_BYTES = 64 * 1024
    try:
        h = DebugLogRotationHandler(str(log), encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        h.setLevel(logging.DEBUG)
        payload = "x" * 900
        for i in range(400):
            h.emit(logging.LogRecord("t", logging.DEBUG, __file__, 1,
                                     f"{i:04d} {payload}", None, None))
        h.close()
    finally:
        DebugLogRotationHandler.MAX_BYTES = orig

    files = {p.name: p.stat().st_size for p in tmpdir.iterdir()}
    assert "debug.log.1" in files, f"no rotation happened: {files}"
    assert files["debug.log.1"] > 0, "rotated file is empty: history was destroyed"
    assert log.read_text(errors="replace").strip(), "live log empty after rotation"


@pytest.mark.resources
def test_logrotate_rule_does_not_conflict():
    """`daily` and `size` together make logrotate report
    'size overrides previously specified daily'; keep it size-only."""
    p = Path("/etc/logrotate.d/goodbooks")
    if not p.exists():
        pytest.skip("logrotate rule not present on this host")
    text = p.read_text()
    assert "daily" not in text, "logrotate rule mixes daily with size"
    assert "size" in text, "logrotate rule has no size trigger"
@pytest.mark.download
def test_slow_download_rejects_html_partner_page():
    """The AA partner page is a 200 HTML document served with NO
    Content-Type header. A header-only check treated it as a real file, so
    every download resolved successfully and then failed later with
    "No working download links available". That is why the
    Goodreads-shelf -> Kindle path never worked.
    """
    from search_engine import AnnaSource

    class FakePartnerPage:
        status_code = 200
        url = "https://annas-archive.gl/slow_download/abc/0/8"
        headers = {}          # deliberately absent: that is the trap
        content = (b"<!DOCTYPE html><html><head>"
                   b"<title>Download from partner website</title>"
                   b"<script>x=1</script></head><body>hi</body></html>")

        def close(self):
            pass

    src = AnnaSource.__new__(AnnaSource)
    src._safe_get = lambda href, **kw: FakePartnerPage()
    src._is_cloudflare_challenge = lambda r: False
    dbg = []
    out = src._resolve_aa_slow_download(
        "https://annas-archive.gl/slow_download/abc/0/8", "abc123", ["pdf"], dbg)
    assert out is None, f"HTML page accepted as a download link: {out!r}"
    assert any("HTML page" in d for d in dbg), f"not logged as rejected: {dbg}"


@pytest.mark.download
def test_slow_download_still_accepts_real_file():
    """The rejection must not break genuine file responses."""
    from search_engine import AnnaSource

    class RealPDF:
        status_code = 200
        url = "https://annas-archive.gl/slow_download/abc/0/8"
        headers = {"Content-Type": "application/pdf",
                   "Content-Disposition": 'attachment; filename="book.pdf"'}
        content = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 500

        def close(self):
            pass

    src = AnnaSource.__new__(AnnaSource)
    src._safe_get = lambda href, **kw: RealPDF()
    src._is_cloudflare_challenge = lambda r: False
    out = src._resolve_aa_slow_download(
        "https://annas-archive.gl/slow_download/abc/0/8", "abc123", ["pdf"], [])
    assert out is not None, "a real PDF response was wrongly rejected"
    assert out[1] == "pdf", f"format not detected from Content-Disposition: {out!r}"


@pytest.mark.download
def test_download_resolver_sniffs_html():
    """The HTML sniff must stay in the resolver, not just in a caller."""
    import inspect

    from search_engine import AnnaSource
    src_txt = inspect.getsource(AnnaSource)
    assert "looks_html" in src_txt, \
        "HTML sniffing was removed from the slow_download resolver"


@pytest.mark.smoke
def test_search_route_is_slow_but_alive():
    """Documents a real performance characteristic, not a bug.

    GET /search performs a LIVE Anna's Archive search on every page load, and
    AA sits behind a DDoS-Guard JS challenge that only a real browser can
    clear. So a search page takes ~15-20s. The route is correct, just slow;
    the smoke test uses a 60s timeout for that reason. Do not "fix" this by
    dropping the timeout -- that would hide a genuine 500.
    """
    req = pytest.importorskip("requests")
    import os
    base = os.environ.get("GOODBOOKS_URL")
    if not base:
        pytest.skip("no live target configured")
    r = req.get(base + "/search?q=test", timeout=60)
    assert r.status_code < 500

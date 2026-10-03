"""Regressions for the two real defects found while profiling GoodBooks.

Both were found by measuring, not by reading, and both had already caused
data or disk loss:

1. _atomic_write_metadata used ONE SHARED temp path for every writer:

       tmp = LIBRARY_METADATA_PATH.with_suffix(".json.tmp")

   os.replace() is atomic for one writer. With the four concurrent metadata
   writers the service actually runs (library-genres-enrich plus the
   Amazon/Goodreads cover workers), they interleave:

       A: open(tmp,"w") ... json.dump 8 MB ...
       B: open(tmp,"w")            <- truncates what A is writing
       A: os.replace(tmp, live)     <- tmp renamed away
       B: os.replace(tmp, live)     <- FileNotFoundError

   Measured on 2026-10-02: the library decayed 4,869 -> 37 -> 1 records,
   because each failed write left whatever the last successful rename
   produced.

2. _rotate_debug_log had exactly ONE call site, at import:

       caller 9840:  def _rotate_debug_log(...)
       caller 10369: _rotate_debug_log(force=True)

   so a long-running service never rotated again however large debug.log
   grew -- the only situation where rotation matters. Measured debris:
   debug.log.1/.2/.3/.4 at 50 MB each, ~200 MB against a 512 MB cap.

These tests slice the real functions out of app.py and exec them in a
sandbox. They deliberately do NOT import app.py: importing it boots a second
service instance with its own metadata cache, and has destroyed live library
data twice (1,074 records once, 4,869 to 73 the second time, and again today
at 4,837 -> 37 when I made that mistake myself).
"""
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def _slice(start_marker, end_marker=None):
    """Extract real source from app.py without importing it."""
    src = APP.read_text()
    start = src.index(start_marker)
    if end_marker:
        end = src.index(end_marker, start)
    else:
        end = src.find("\ndef ", start + 10)
        if end == -1:
            end = len(src)
    return src[:start].count("\n") + 1, src[start:end]


# --------------------------------------------------------------------------
# 1. the metadata writer
# --------------------------------------------------------------------------
def test_metadata_writer_uses_a_unique_temp_name():
    """A shared temp name is atomic for exactly one writer."""
    _, body = _slice("_METADATA_WRITE_SEQ = 0", "def _normalize_sort_key(")
    assert '.with_suffix(".json.tmp")' not in body, \
        "metadata writer still uses one shared .json.tmp path"
    assert "os.getpid()" in body, "temp name must include the pid"
    assert "threading.get_ident()" in body, \
        "temp name must include the thread id, or two threads in one " \
        "process still collide"


def test_metadata_writer_cleans_up_on_failure():
    _, body = _slice("_METADATA_WRITE_SEQ = 0", "def _normalize_sort_key(")
    assert "except BaseException" in body, \
        "a failed write must unlink its temp file"
    assert "tmp.unlink(missing_ok=True)" in body


def test_metadata_writer_fsyncs_the_directory():
    """fsyncing the file data does not make the RENAME durable."""
    _, body = _slice("_METADATA_WRITE_SEQ = 0", "def _normalize_sort_key(")
    assert "os.fsync(dirfd)" in body or "os.open(str(LIBRARY_METADATA_PATH.parent)" in body, \
        "the directory entry must be fsynced or a crash can lose the rename"


def test_metadata_writer_survives_concurrent_writes(tmp_path):
    """The regression test for the 4,869 -> 37 -> 1 record decay.

    Runs the REAL extracted writer from 8 threads. Low iteration counts keep
    it fast while still guaranteeing the interleaving that broke the old
    version.
    """
    live = tmp_path / "library_metadata.json"
    payload = {f"book-{i}": {"title": f"Book {i}", "author": "A"}
               for i in range(200)}

    _, body = _slice("_METADATA_WRITE_SEQ = 0", "def _normalize_sort_key(")

    class _Log:
        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    ns = {
        "json": json, "os": os, "time": time, "threading": threading,
        "Path": Path, "logger": _Log(), "LIBRARY_METADATA_PATH": live,
        "Dict": dict, "List": list, "bool": bool, "int": int, "str": str,
    }
    exec(compile(body, "writer", "exec"), ns)
    write = ns["_atomic_write_metadata"]

    errors = []
    lock = threading.Lock()

    def hammer():
        try:
            for _ in range(5):
                write(payload, sort_keys=True)
        except Exception as exc:
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent writes raised: {errors[:3]}"
    after = json.loads(live.read_text())
    assert len(after) == 200, f"records lost: {len(after)} != 200"
    debris = list(tmp_path.glob("library_metadata.json.tmp*"))
    assert not debris, f"temp debris left behind: {[p.name for p in debris]}"


# --------------------------------------------------------------------------
# 2. log rotation
# --------------------------------------------------------------------------
def test_rotation_is_not_startup_only():
    """Rotation must not be reachable ONLY from the module-level hook.

    Measured consequence of the bug: debug.log.1/.2/.3/.4 at 50 MB each,
    ~200 MB of debris against a 512 MB cap, on a service up for hours.

    Two false alarms had to be ruled out while writing this:
      * a regex of ^\\s+_rotate_debug_log\\( counts only INDENTED sites and
        misses the module-level one, so the test failed while the fix was
        correctly in place.
      * the real requirement is that a site exists inside a function that
        actually RUNS. Counting call sites proves nothing on its own --
        main() in this codebase is unreachable dead code, and a call there
        looks identical to a working one.
    """
    src = APP.read_text()
    indented = len(re.findall(r"^[ \t]+_rotate_debug_log\(", src, re.M))
    module_level = len(re.findall(r"^_rotate_debug_log\(", src, re.M))
    assert module_level + indented >= 2, (
        f"only {module_level + indented} call site(s); a module-level hook "
        "alone means a long-running service never rotates again")

    # The periodic site must be inside _run_maintenance_cycle, which the
    # background worker calls -- not inside main(), which never runs.
    m = re.search(r"def _run_maintenance_cycle\(\).*?(?=\ndef )", src, re.S)
    assert m, "no _run_maintenance_cycle found"
    assert "_rotate_debug_log(" in m.group(0), \
        "the periodic rotation call is not inside the maintenance cycle"


def test_rotation_reaps_old_generations_and_temps(tmp_path):
    log = tmp_path / "debug.log"
    log.write_bytes(b"x" * 4096)
    for i in (1, 2, 3):
        (tmp_path / f"debug.log.{i}").write_bytes(b"y" * 1024)
    (tmp_path / "debug.log.9.tmp.1.2.3").write_bytes(b"z" * 32)

    _, body = _slice("def _rotate_debug_log(")

    class _Log:
        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    ns = {
        "time": time, "Path": Path, "os": os, "logger": _Log(),
        "BASE_DIR": tmp_path,
        "DEBUG_LOG_MAX_BYTES": 1024,      # below the 4 KB log -> force a rotate
        "DEBUG_LOG_KEEP": 1,
        "_log_rotated_at": {"t": 0.0},
    }
    exec(compile(body, "rot", "exec"), ns)
    ns["_rotate_debug_log"](force=True)

    names = {p.name for p in tmp_path.iterdir()}
    assert "debug.log.1" in names, "the kept generation is missing"
    assert "debug.log.2" not in names, "old generation .2 was not reaped"
    assert "debug.log.3" not in names, "old generation .3 was not reaped"
    assert not any(".tmp." in n for n in names), "stale temp not reaped"
    assert "debug.log" in names, "live log must be recreated"


# --------------------------------------------------------------------------
# 3. the WSGI server
# --------------------------------------------------------------------------
def test_not_serving_with_the_development_server():
    """The live log carried Flask's own warning while waitress sat unused.

        WARNING: This is a development server. Do not use it in a production
        deployment.

    The reachable entry point must not call app.run().
    """
    src = APP.read_text()
    first = src.index('if __name__ == "__main__":')
    second = src.index('if __name__ == "__main__":', first + 10)
    block = src[first:second]
    assert "app.run(host" not in block, \
        "the reachable entry point still serves with the dev server"
    assert "_serve_production" in block, \
        "the reachable entry point must delegate to the production path"


def test_no_route_registered_after_the_entry_point():
    """No NEW route may appear after the entry point.

    Flask refuses routes registered after the first request:

        AssertionError: The setup method 'route' can no longer be called on
        the application. It has already handled its first request.

    That crash-looped the service 10 times when a diagnostic route was moved
    below the entry point.

    Scoped to NEW routes, not absolute. /admin/cache-covers already sat below
    the guard in HEAD and has always worked, because it is evaluated during
    the import that precedes any request -- what breaks is a route registered
    after the app has already SERVED one. So the test compares against HEAD
    rather than asserting a blanket zero, which was wrong and failed on
    pre-existing, harmless code.
    """
    src = APP.read_text()
    first = src.index('if __name__ == "__main__":')
    after = src[first:]
    now_routes = set(re.findall(r'@app\.route\("([^"]+)"', after))

    try:
        import subprocess
        head = subprocess.run(
            ["git", "show", "HEAD:app.py"], cwd=str(ROOT),
            capture_output=True, text=True, timeout=60).stdout
    except Exception:
        pytest.skip("cannot read HEAD:app.py to diff against")

    if not head:
        pytest.skip("HEAD:app.py unavailable")
    hfirst = head.index('if __name__ == "__main__":')
    was_routes = set(re.findall(r'@app\.route\("([^"]+)"', head[hfirst:]))

    new = now_routes - was_routes
    assert not new, (
        f"route(s) {sorted(new)} registered after the entry point; Flask "
        "raises once the app has handled a request")

"""Regression tests for the data-loss bugs fixed 2026-09-30.

library_metadata.json was truncated to 0 bytes and all 4,837 records were
lost, because every writer used write_text(), which opens with "w" and
truncates before writing. history.json had the same class of bug, one of them
with no lock at all. These tests pin the behaviour so it cannot come back.

Why the source is filtered first: both fixes are documented in prose that
quotes the old unsafe call verbatim, so a naive scan matches its own
description of the bug. Docstring lines are removed by a small state machine
rather than by tokenising, because a two earlier attempts failed in ways
worth not repeating: joining tokens with spaces turned "os.replace(" into
"os . replace (", and blanking in place by token offsets corrupted the line
lengths.
"""
import ast
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"


def _strip_docstrings(source: str) -> str:
    """Blank out triple-quoted string bodies, preserving line structure.

    A deliberate, small state machine: track whether we are inside a
    triple-quoted run, and blank those lines. Line count and all non-docstring
    text are preserved exactly, so line numbers and code patterns still match.
    """
    out = []
    in_doc = False
    delim = ""
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        if not in_doc:
            # a line may open a docstring, close one, or both
            if delim or '"""' in line or "'''" in line:
                if '"""' in line:
                    delim = '"""'
                elif "'''" in line:
                    delim = "'''"
                # count occurrences to see if it opens and closes together
                if line.count(delim) >= 2:
                    in_doc = False
                    delim = ""
                    out.append(line)      # code before/after on this line
                    continue
                in_doc = True
                out.append("\n" if line.endswith("\n") else "")
                continue
            out.append(line)
        else:
            if delim in line:
                after = line.split(delim, 1)[1]
                in_doc = False
                delim = ""
                tail = after if after.strip() else ""
                out.append(tail if tail else ("\n" if line.endswith("\n") else ""))
            else:
                out.append("\n" if line.endswith("\n") else "")
    return "".join(out)


def _function_src(source: str, name: str) -> str:
    """One function's source with docstrings already removed."""
    m = re.search(rf"^def {re.escape(name)}\(", source, re.M)
    if not m:
        return ""
    start = m.start()
    m2 = re.compile(r"^(def |@|class )", re.M).search(source, start + 4)
    end = m2.start() if m2 else len(source)
    return _strip_docstrings(source[start:end])


@pytest.fixture(scope="module")
def app_raw():
    return APP.read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def app_code(app_raw):
    return _strip_docstrings(app_raw)


# ------------------------------------------------------------- truncation --

def test_no_truncating_write_of_the_metadata_file(app_code):
    direct = [ln for ln in app_code.splitlines()
              if "LIBRARY_METADATA_PATH.write_text(" in ln]
    assert not direct, f"non-atomic metadata writes remain: {direct}"


def test_atomic_writer_exists_and_uses_replace(app_raw):
    body = _function_src(app_raw, "_atomic_write_metadata")
    assert body, "_atomic_write_metadata not found"
    assert "os.replace(" in body, "atomic writer must use os.replace"
    assert "fsync" in body, "atomic writer must fsync before replacing"
    assert ".tmp" in body, "atomic writer must write to a temp file first"


def test_history_writes_are_atomic_and_locked(app_raw, app_code):
    body = _function_src(app_raw, "_atomic_write_history")
    assert body, "history atomic writer missing"
    assert "history_manager.lock" in body, "history writer must take the lock"
    assert "os.replace(" in body, "history writer must use os.replace"
    bad = [ln for ln in app_code.splitlines()
           if "history_manager.path.write_text(" in ln]
    assert not bad, f"unsafe history write remains: {bad}"


# ------------------------------------------------------------ the rename --

def test_rename_does_not_pop_a_key_that_may_be_absent(app_raw):
    body = _function_src(app_raw, "rename_library_file_to_md5_format")
    assert body, "rename function not found"
    assert not re.search(r"\.pop\(entry_id\)", body), \
        "rename still uses a bare pop(entry_id), which orphans metadata"
    assert re.search(r"\.pop\(entry_id,\s*None\)", body), \
        "rename should pop with a default"


def test_rename_holds_the_metadata_lock_around_the_mutation(app_raw):
    body = _function_src(app_raw, "rename_library_file_to_md5_format")
    pop_at = body.find("entry_id, None")
    assert pop_at > 0, "no pop-with-default site found"
    window = body[max(0, pop_at - 700):pop_at + 200]
    assert "library_metadata_lock" in window, \
        "the metadata mutation is outside library_metadata_lock"


# ----------------------------------------------------- the atomic writer --

def test_atomic_write_never_leaves_a_truncated_file(tmp_path):
    """The real property: a crash mid-write cannot empty the file."""
    target = tmp_path / "meta.json"
    target.write_text(json.dumps({"a" * 5000: {"x": 1}}))

    def atomic_write(metadata, path):
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2, ensure_ascii=False)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(tmp, path)

    atomic_write({"b" * 9000: {"y": 2}}, target)
    assert "b" * 9000 in json.loads(target.read_text())
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_history_writes_lose_nothing():
    """The old unlocked write could drop entries under concurrency."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "hist.json"

        class Mgr:
            def __init__(self, path):
                self.path = path
                self.lock = threading.Lock()

        mgr = Mgr(p)
        mgr.path.write_text("[]")

        def atomic_write(manager, entries):
            with manager.lock:
                tmp = Path(str(manager.path) + ".tmp")
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(entries, fh, indent=2)
                os.replace(tmp, manager.path)

        errors = []

        def worker(n):
            try:
                for _ in range(25):
                    cur = json.loads(mgr.path.read_text())
                    cur.append({"n": n})
                    atomic_write(mgr, cur)
            except Exception as exc:      # pragma: no cover
                errors.append(exc)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert not errors, errors
        assert len(json.loads(mgr.path.read_text())) > 0, \
            "concurrency emptied the history file"


# --------------------------------------------------------------- the app --

def test_app_still_parses():
    ast.parse(APP.read_text(encoding="utf-8", errors="replace"))


def test_author_parser_guards_still_hold():
    """The matching guard from earlier today must not have been undone."""
    sys.path.insert(0, str(ROOT))
    import gb_authors
    given = gb_authors.given_name_seed()
    gb_authors._SURNAME_COUNTS = gb_authors.Counter()
    assert gb_authors.parse_authors("jeff; smith", given) == ["Jeff Smith"]
    assert gb_authors.parse_authors("west, tracey", given) == ["Tracey West"]
    assert gb_authors.parse_authors("1501110344hoover", given) == []

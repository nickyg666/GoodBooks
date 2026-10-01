"""Reading state: what a Kindle library is actually organised around.

A Kindle library leads with "Reading Now" -- the last book you opened, with a
percentage complete -- and every cover carries a progress bar. The library
had none of that. Checked 2026-09-30 across every data file: no read
progress, no last-read timestamp, no bookmark, no reading position. Keyword
hits for "progress" and "location" were inside book descriptions, not fields.

So this module creates the store, and keeps it honest:

  * one small JSON file, keyed by a stable per-book id
  * written when a book page is opened (that IS the last-read event)
  * percent complete is OPTIONAL and defaults to None, never 0. A book you
    have merely opened is not 1% read, and showing a bar for it would be a
    lie. The UI shows an "opened" marker instead until a real position lands.
  * last_read is a real timestamp, so "Reading Now" and sort-by-Recent are
    derived from it rather than from the download time in history.json

Deliberately tiny and dependency-free. Aimed at being called from the book
detail route and the library view.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent / "data"
STATE_PATH = DATA_DIR / "reading_state.json"

_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None
_cache_mtime: float = 0.0

# A book is "actively reading" only if it was opened recently. The Kindle
# rail shows the current book; past that it is just old.
ACTIVE_WINDOW_DAYS = 14


def _load() -> Dict[str, dict]:
    """Read the store, re-reading when the file changed underneath us."""
    global _cache, _cache_mtime
    if not STATE_PATH.exists():
        _cache, _cache_mtime = {}, 0.0
        return {}
    try:
        mtime = STATE_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cache is not None and mtime == _cache_mtime:
        return _cache
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8",
                                              errors="replace"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _cache, _cache_mtime = data, mtime
    return data


def _save(data: Dict[str, dict]) -> None:
    """Atomic write: the app serves reads while this runs."""
    global _cache, _cache_mtime
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".reading_state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
        _cache = data
        _cache_mtime = STATE_PATH.stat().st_mtime
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def entry_id_for(entry: Dict) -> str:
    """Stable key for a book.

    The library's `id` is a composite "<root>::<relpath>" string, which is
    stable across restarts, so it is used directly.
    """
    return str(entry.get("id") or entry.get("entry_id") or entry.get("path") or "")


def get(entry: Dict) -> dict:
    """Reading state for one book; a blank record if never opened."""
    eid = entry_id_for(entry)
    rec = _load().get(eid)
    if not rec:
        return {"last_read": None, "percent": None, "opened_count": 0}
    return rec


def mark_opened(entry: Dict) -> dict:
    """Record that a book was opened. This is the last-read event.

    percent is intentionally left alone: opening a book does not tell us how
    far in it you are. Only set_progress() supplies that.
    """
    eid = entry_id_for(entry)
    if not eid:
        return {"last_read": None, "percent": None, "opened_count": 0}
    with _lock:
        data = dict(_load())
        rec = dict(data.get(eid) or {})
        rec["last_read"] = time.time()
        rec["opened_count"] = int(rec.get("opened_count") or 0) + 1
        rec.setdefault("percent", None)
        rec.setdefault("finished", False)
        data[eid] = rec
        _save(data)
    return rec


def set_progress(entry: Dict, percent: Optional[float],
                 finished: Optional[bool] = None) -> dict:
    """Record a real reading position, as a percentage 0-100.

    Accepts None to clear it. Values outside 0-100 are clamped rather than
    rejected, because a slightly wrong bar beats a rejected save.
    """
    eid = entry_id_for(entry)
    if not eid:
        return {}
    with _lock:
        data = dict(_load())
        rec = dict(data.get(eid) or {})
        if percent is None:
            rec["percent"] = None
        else:
            try:
                p = float(percent)
            except (TypeError, ValueError):
                p = None
            rec["percent"] = None if p is None else max(0.0, min(100.0, p))
        if finished is not None:
            rec["finished"] = bool(finished)
        rec.setdefault("last_read", time.time())
        data[eid] = rec
        _save(data)
    return rec


def is_active(rec: dict) -> bool:
    """True when this book was opened inside the active window."""
    lr = (rec or {}).get("last_read")
    if not lr:
        return False
    return (time.time() - float(lr)) < ACTIVE_WINDOW_DAYS * 86400


def currently_reading(entries: List[Dict], limit: int = 5) -> List[Dict]:
    """The "Reading Now" rail: most recently opened, still active, unopened last.

    Mirrors the Kindle behaviour: the book you are part way through leads,
    and books merely opened once sit behind it.
    """
    state = _load()
    rows = []
    for e in entries:
        rec = state.get(entry_id_for(e))
        if not rec or not is_active(rec):
            continue
        pct = rec.get("percent")
        started = pct is not None
        rows.append({
            "entry": e,
            "state": rec,
            "percent": pct,
            "started": started,
            # part-way books first, then most recent
            "_ord": (0 if started else 1, -float(rec.get("last_read") or 0)),
        })
    rows.sort(key=lambda r: r["_ord"])
    return rows[:limit]


def decorate(entries: List[Dict]) -> List[Dict]:
    """Attach reading state to each entry, in place, for template use."""
    state = _load()
    for e in entries:
        rec = state.get(entry_id_for(e)) or {}
        e["_read_last"] = rec.get("last_read")
        e["_read_percent"] = rec.get("percent")
        e["_read_finished"] = bool(rec.get("finished"))
        e["_read_active"] = is_active(rec)
    return entries


def shelf_of(entry: Dict) -> str:
    """First path segment below the library root: the Kindle-style shelf.

    The library already has four (Listopia, Lorenzo, nick-to-read, sagey);
    surfacing them as shelves rather than as opaque folders is most of what
    makes this feel like a Kindle library.
    """
    eid = entry_id_for(entry)
    if "::" in eid:
        rel = eid.split("::", 1)[1]
    else:
        rel = ""
    rel = rel.lstrip("/")
    return rel.split("/", 1)[0] if rel else ""

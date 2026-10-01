"""Canonical mirror resolution backed by SLUM.

Per project rule: when no hardcoded source is usable, refetch the live
mirror set from SLUM (the canonical source of which mirrors work at request
time). This is consulted

  * at startup, and
  * immediately before a feed run pulls books,

so a dead-mirror list is never used to burn time on doomed attempts.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

_LAST_REFRESH: float = 0.0
_CACHE: dict = {}


def _monitor():
    try:
        from slum_monitor import get_slum_monitor
        return get_slum_monitor()
    except Exception as exc:  # pragma: no cover
        logger.debug("SLUM unavailable: %s", exc)
        return None


def live_mirrors(ttl: int = 300) -> List[str]:
    """Return currently-up mirrors, best score first.

    Cached for `ttl` seconds so a feed run hitting many books does not
    re-fetch SLUM per lookup.
    """
    global _LAST_REFRESH
    now = time.monotonic()
    if _CACHE.get("list") and (now - _LAST_REFRESH) < ttl:
        return list(_CACHE["list"])

    m = _monitor()
    if m is None:
        return []

    entries = []
    try:
        entries = m.get_monitors()
    except Exception as exc:
        logger.warning("SLUM get_monitors failed: %s", exc)
        return []

    live = []
    for e in entries:
        url = getattr(e, "url", "") or ""
        if not url:
            continue
        if not getattr(e, "is_up", False):
            continue
        try:
            score = float(getattr(e, "score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        live.append((score, url))

    live.sort(key=lambda t: t[0], reverse=True)
    out = [u for _, u in live]
    _CACHE["list"] = out
    _LAST_REFRESH = now
    logger.info("SLUM: %d live mirrors resolved", len(out))
    return list(out)


def live_aa_mirrors(ttl: int = 300) -> List[str]:
    """Live mirrors that look like Anna's Archive frontends."""
    aa = [u for u in live_mirrors(ttl) if "annas-archive" in u or "welib" in u]
    return aa


def live_libgen_mirrors(ttl: int = 300) -> List[str]:
    return [u for u in live_mirrors(ttl) if "libgen" in u or "1lib" in u]


def refresh_now() -> Optional[List[str]]:
    """Force an immediate SLUM refetch (used at startup and pre-feed)."""
    global _LAST_REFRESH
    _LAST_REFRESH = 0.0
    _CACHE.pop("list", None)
    lst = live_mirrors(ttl=0)
    logger.info("SLUM refetch on demand -> %d mirrors", len(lst))
    return lst or None

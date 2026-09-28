"""
Internal module — do not import from outside :mod:`slum_monitor`.

Convenience helpers exported to callers (e.g. ``search_engine.py``):

* :func:`libgen_alias_for_url` — URL → short alias for libgen-api-enhanced.
* :func:`rank_libgen_mirrors`   — sort libgen candidates by SLUM score.
* :func:`rank_aa_mirrors`      — sort AA candidates by SLUM score.

Plus the process-wide singleton (:func:`get_slum_monitor`,
:func:`reset_slum_monitor`).
"""

from __future__ import annotations

import logging
import threading
from typing import List, Optional, Sequence
from urllib.parse import urlparse

from ._constants import AA_MIRRORS, LIBGEN_SHORT_ALIASES
from ._monitor import SlumMonitor
from ._settings import load_settings_into_monitor

logger = logging.getLogger(__name__)

__all__ = [
    "get_slum_monitor",
    "reset_slum_monitor",
    "libgen_alias_for_url",
    "rank_libgen_mirrors",
    "rank_aa_mirrors",
]


# ----------------------------------------------------------------------
# Singleton
# ----------------------------------------------------------------------

_singleton: Optional[SlumMonitor] = None
_singleton_lock = threading.Lock()


def get_slum_monitor() -> SlumMonitor:
    """
    Return the process-wide :class:`SlumMonitor`, configured from
    settings.  Lazy-initialised; call :func:`reset_slum_monitor` after
    a settings change.
    """
    global _singleton
    if _singleton is not None:
        return _singleton
    with _singleton_lock:
        if _singleton is None:  # double-checked
            mon = SlumMonitor()
            load_settings_into_monitor(mon)
            _singleton = mon
    return _singleton


def reset_slum_monitor() -> None:
    """Drop the singleton (call after a settings change)."""
    global _singleton
    with _singleton_lock:
        _singleton = None


# ----------------------------------------------------------------------
# Libgen alias helper
# ----------------------------------------------------------------------

def libgen_alias_for_url(url: str) -> Optional[str]:
    """
    Given a libgen URL (e.g. ``https://libgen.li/foo``), return the
    short alias accepted by ``libgen_api_enhanced.LibgenSearch``
    (e.g. ``"li"``).  Returns ``None`` if the URL isn't a recognised
    libgen host.
    """
    if not url:
        return None
    host = (urlparse(url).hostname or "").lower()
    if not host.startswith("libgen."):
        return None
    tld = host.split(".", 1)[1]  # "li", "la", ...
    return tld if tld in LIBGEN_SHORT_ALIASES else None


# ----------------------------------------------------------------------
# Rank-by-SLUM helpers
# ----------------------------------------------------------------------

def _rank_via_slum(
    candidate_urls: Sequence[str],
    slum: Optional[SlumMonitor],
    only_up: bool,
    fallback_message: str,
) -> List[str]:
    """Shared body for :func:`rank_libgen_mirrors` and :func:`rank_aa_mirrors`."""
    urls = list(candidate_urls)
    slum = slum or get_slum_monitor()
    if not slum.enabled:
        return urls
    try:
        return slum.rank_urls(urls, only_up=only_up)
    except Exception as e:  # noqa: BLE001
        logger.warning("SLUM: %s, falling back to input order: %s", fallback_message, e)
        return urls


def rank_libgen_mirrors(
    candidate_urls: Sequence[str],
    slum: Optional[SlumMonitor] = None,
    only_up: bool = True,
) -> List[str]:
    """
    Take a list of candidate libgen URLs and return them sorted by
    SLUM score, best first.  The first element is the recommended
    mirror for ``LibgenSearch(mirror=...)``.

    If SLUM is disabled, unreachable, or has no signal for these
    hosts, the original order is returned (preserves current
    behaviour).
    """
    return _rank_via_slum(
        candidate_urls, slum, only_up,
        fallback_message="rank_libgen_mirrors failed",
    )


def rank_aa_mirrors(
    candidate_urls: Optional[Sequence[str]] = None,
    slum: Optional[SlumMonitor] = None,
    only_up: bool = True,
) -> List[str]:
    """
    Take a list of candidate Anna's Archive URLs and return them
    sorted by SLUM score, best first.  Defaults to :data:`AA_MIRRORS`
    when no candidates are supplied.

    AA has the most comprehensive shadow-library catalogue (every
    libgen / zlib / sci-hub record is mirrored), so we want to fall
    back to AA before falling back to libgen-only searches.
    """
    urls = list(candidate_urls) if candidate_urls else list(AA_MIRRORS)
    return _rank_via_slum(
        urls, slum, only_up,
        fallback_message="rank_aa_mirrors failed",
    )
"""
Internal module — do not import from outside :mod:`slum_monitor`.

Per-source-family affinity scores used in :meth:`SlumMonitorEntry.score`.

The integrated borrower (libgen-api-enhanced) can actually search and
resolve downloads from libgen-family mirrors, so they get the highest
bonus.  Anna's Archive is a strong secondary; Z-Library is similar to
libgen; Sci-Hub is mostly for papers, not books, so a lower affinity.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse


def _libgen_affinity(host: str) -> Optional[float]:
    if "libgen" in host or "library.memoryoftheworld" in host or "libstc" in host:
        return 1.0
    if "1lib" in host or "welib" in host or "go-to-library" in host or "library-access" in host:
        return 0.95
    return None


def _zlib_affinity(host: str) -> Optional[float]:
    if "z-lib" in host or "zlib" in host:
        return 0.9
    return None


def _aa_affinity(host: str) -> Optional[float]:
    if "annas-archive" in host or "anna" in host:
        return 0.95
    return None


def _scihub_affinity(host: str) -> Optional[float]:
    if "sci-hub" in host or "sci-net" in host or "scihub" in host:
        return 0.6
    return None


def _other_affinity(host: str) -> float:
    return 0.5


#: The order matters: the first helper to return non-None wins.
_AFFINITY_DETECTORS = (
    _libgen_affinity,
    _zlib_affinity,
    _aa_affinity,
    _scihub_affinity,
)


def source_affinity(url_or_host: str) -> float:
    """Return a 0..1 affinity score for a URL or hostname."""
    if not url_or_host:
        return 0.0
    if "://" in url_or_host:
        host = (urlparse(url_or_host).hostname or "").lower()
    else:
        host = url_or_host.lower()
    for fn in _AFFINITY_DETECTORS:
        v = fn(host)
        if v is not None:
            return v
    return _other_affinity(host)
"""
SLUM (Shadow Library Uptime Monitor) integration for GoodBooks.

Dynamically fetches live availability data for shadow‑library sources
(Anna's Archive, Library Genesis, Z‑Library, Sci‑Hub, etc.) and exposes
a ranked view that can be used to pick the most available mirror when
borrowing/downloading a book.

Data sources (tried in order, the first that yields data wins):

  1. ``{slum_url}/api/data`` — the UptimeFlare JSON API on Cloudflare
     Pages.  Clean, ~2 KB, CORS open.  This is the primary feed.
  2. ``slum_extra_endpoints`` — any extra Uptime Kuma status‑page JSON
     endpoints configured by the user (defaults to
     https://open-slum.org/api/status-page/slum).  Useful as a fallback
     if the primary site is down.
  3. ``slum_local_html_fallback`` — an on‑disk copy of the
     ``__NEXT_DATA__`` SSR HTML.  This is the file the user originally
     captured; parsing it lets the integration keep working even when
     every external site is unreachable (e.g. offline installs).
  4. The previous in‑memory cache (stale), returned with
     ``last_error`` populated so callers can see freshness.

Public API (kept stable for any callers)::

    SlumMonitor(url=..., cache_ttl_seconds=..., fetch_timeout_seconds=...,
                enabled=..., extra_endpoints=[...], local_html_fallback="...")
        .get_monitors()        -> List[SlumMonitorEntry]
        .get_status(url)       -> Optional[SlumMonitorEntry]
        .get_ranked_sources(...) -> List[SlumMonitorEntry]   # sorted by score
        .rank_urls(urls, ...)  -> List[str]                  # best mirror first
        .get_report()          -> dict                        # for /settings UI

    get_slum_monitor()         -> SlumMonitor                # process-wide singleton
    reset_slum_monitor()       -> None                       # drop the singleton
    rank_libgen_mirrors(urls)  -> List[str]                  # libgen-only ranking
    rank_aa_mirrors(urls)      -> List[str]                  # AA-only ranking
    libgen_alias_for_url(url)  -> Optional[str]              # "li"/"la"/...
    source_affinity(host)      -> float                      # 0..1
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from ._affinity import source_affinity
from ._constants import (
    AA_MIRRORS,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_EXTRA_ENDPOINTS,
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    DEFAULT_LOCAL_HTML_FALLBACK,
    DEFAULT_SLUM_URL,
    LIBGEN_SHORT_ALIASES,
    MONITOR_ID_TO_URL,
)
from ._helpers import (
    get_slum_monitor,
    libgen_alias_for_url,
    rank_aa_mirrors,
    rank_libgen_mirrors,
    reset_slum_monitor,
)
from ._monitor import SlumMonitor
from ._types import SlumCache, SlumMonitorEntry

__all__ = [
    # Classes
    "SlumMonitor",
    "SlumMonitorEntry",
    "SlumCache",
    # Singleton
    "get_slum_monitor",
    "reset_slum_monitor",
    # Helpers
    "rank_libgen_mirrors",
    "rank_aa_mirrors",
    "libgen_alias_for_url",
    "source_affinity",
    # Constants — defaults
    "DEFAULT_SLUM_URL",
    "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_FETCH_TIMEOUT_SECONDS",
    "DEFAULT_EXTRA_ENDPOINTS",
    "DEFAULT_LOCAL_HTML_FALLBACK",
    # Constants — mirror lists
    "AA_MIRRORS",
    "LIBGEN_SHORT_ALIASES",
    "MONITOR_ID_TO_URL",
]

__version__ = "2.0.0"

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Self-test when run directly: ``python -m slum_monitor``
# ----------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mon = SlumMonitor(
        url=os.environ.get("SLUM_URL", DEFAULT_SLUM_URL),
        cache_ttl_seconds=300,
        fetch_timeout_seconds=10,
    )
    entries = mon.refresh(force=True)
    print(f"Loaded {len(entries)} monitors from {mon._cache.last_source!r}")
    for e in sorted(entries, key=lambda x: -x.score())[:15]:
        d = e.to_dict()
        print(
            f"  {d['name']:30} up={d['is_up']!s:5} "
            f"score={d['score']:.3f} url={d['url'][:50]:50} "
            f"lat={d['latency_ms']} uptime={d['uptime_pct']:.1f}"
        )
    print()
    print("Report keys:", sorted(mon.get_report().keys()))
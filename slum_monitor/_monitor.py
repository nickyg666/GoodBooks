"""
Internal module — do not import from outside :mod:`slum_monitor`.

The :class:`SlumMonitor` class — fetches, parses, and ranks SLUM
monitor data with cascading fallbacks.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Iterable, List, Optional, Sequence, Tuple

import requests

from ._constants import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_EXTRA_ENDPOINTS,
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    DEFAULT_LOCAL_HTML_FALLBACK,
    DEFAULT_SLUM_URL,
    HTTP_HEADERS,
)
from ._parsers import parse_kuma_data, parse_next_data_html, parse_uptimeflare_data
from ._types import SlumCache, SlumMonitorEntry

logger = logging.getLogger(__name__)


class SlumMonitor:
    """
    Fetches, parses, and ranks SLUM monitor data.

    Thread-safe.  Caches results in-process with a TTL.  Uses the
    cascading-fallback fetch strategy described in the package
    docstring.
    """

    def __init__(
        self,
        url: str = DEFAULT_SLUM_URL,
        cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        fetch_timeout_seconds: int = DEFAULT_FETCH_TIMEOUT_SECONDS,
        enabled: bool = True,
        extra_endpoints: Optional[Sequence[str]] = None,
        local_html_fallback: Optional[str] = None,
    ) -> None:
        self.url = (url or DEFAULT_SLUM_URL).rstrip("/")
        self.cache_ttl_seconds = max(30, int(cache_ttl_seconds))
        self.fetch_timeout_seconds = max(2, int(fetch_timeout_seconds))
        self.enabled = bool(enabled)
        # An empty list is meaningful ("no extra endpoints"), so only
        # fall back to the default when None.
        if extra_endpoints is None:
            self.extra_endpoints: List[str] = list(DEFAULT_EXTRA_ENDPOINTS)
        else:
            self.extra_endpoints = [str(u) for u in extra_endpoints if u]
        self.local_html_fallback: Optional[str] = (
            local_html_fallback if local_html_fallback is not None
            else DEFAULT_LOCAL_HTML_FALLBACK
        )
        self._cache = SlumCache()
        self._lock = threading.Lock()
        self._inflight = False

    # ------------------------------------------------------------------
    # Cache state
    # ------------------------------------------------------------------

    def is_fresh(self) -> bool:
        return (
            self._cache.fetched_at > 0
            and (time.time() - self._cache.fetched_at) < self.cache_ttl_seconds
        )

    def age_seconds(self) -> Optional[float]:
        if self._cache.fetched_at <= 0:
            return None
        return time.time() - self._cache.fetched_at

    # ------------------------------------------------------------------
    # Fetchers (one per source)
    # ------------------------------------------------------------------

    def _fetch_uptimeflare_api(self) -> List[SlumMonitorEntry]:
        """Primary feed: GET {self.url}/api/data (UptimeFlare JSON)."""
        api_url = f"{self.url}/api/data"
        logger.debug("SLUM: GET %s", api_url)
        resp = requests.get(
            api_url, headers=HTTP_HEADERS, timeout=self.fetch_timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or "monitors" not in data:
            raise ValueError("SLUM: /api/data response missing 'monitors'")
        return parse_uptimeflare_data(data, source="uptimeflare")

    def _fetch_kuma_endpoints(self) -> List[SlumMonitorEntry]:
        """Secondary feed: any configured Uptime Kuma status pages."""
        last_err: Optional[Exception] = None
        for ep in self.extra_endpoints:
            try:
                logger.debug("SLUM: GET (kuma) %s", ep)
                resp = requests.get(
                    ep, headers=HTTP_HEADERS, timeout=self.fetch_timeout_seconds,
                )
                resp.raise_for_status()
                monitors = parse_kuma_data(resp.json(), source=f"kuma:{ep}")
                if monitors:
                    return monitors
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("SLUM: kuma endpoint %s failed: %s", ep, e)
        if last_err:
            raise last_err
        return []

    def _fetch_local_html(self) -> List[SlumMonitorEntry]:
        """Tertiary feed: parse the on-disk captured HTML, if available."""
        path = self.local_html_fallback
        if not path or not os.path.exists(path):
            raise FileNotFoundError(
                f"SLUM: local HTML fallback not found at {path!r}"
            )
        with open(path, "rb") as f:
            html_bytes = f.read()
        monitors = parse_next_data_html(html_bytes, source=f"local:{path}")
        if not monitors:
            raise ValueError("SLUM: local HTML fallback yielded no monitors")
        return monitors

    def _fetch(self) -> Tuple[List[SlumMonitorEntry], str]:
        """
        Try each feed in order, return ``(monitors, source)`` on success.
        Raises the last error if all fail and cache is empty.
        """
        # Each entry is (name, fetcher_no_args).  The cascade is
        # expressed as data, not control flow, so adding/removing a
        # source is a one-line change.
        cascade: Tuple[Tuple[str, str], ...] = (
            ("uptimeflare", "primary feed"),
            ("kuma",        "kuma feed"),
            ("local",       "local fallback"),
        )
        errors: List[str] = []
        for source, label in cascade:
            try:
                if source == "uptimeflare":
                    monitors = self._fetch_uptimeflare_api()
                elif source == "kuma":
                    monitors = self._fetch_kuma_endpoints()
                    if not monitors:
                        errors.append(f"{label}: no monitors in any kuma endpoint")
                        continue
                else:  # "local"
                    monitors = self._fetch_local_html()
                return monitors, source
            except Exception as e:  # noqa: BLE001
                errors.append(f"{label}: {e}")
                logger.info("SLUM: %s failed: %s", label, e)

        raise RuntimeError("SLUM: all feeds failed: " + "; ".join(errors))

    # ------------------------------------------------------------------
    # Public refresh + lookup API
    # ------------------------------------------------------------------

    def refresh(self, force: bool = False) -> List[SlumMonitorEntry]:
        """
        Fetch and cache fresh SLUM data.

        If ``force=False`` and the cache is still fresh, returns the
        cached monitors without making a network request.  If all feeds
        fail, returns the previous cache (possibly stale) so the app
        keeps working offline.
        """
        if not self.enabled:
            logger.debug("SLUM: disabled, returning empty cache")
            return list(self._cache.monitors)

        with self._lock:
            if not force and self.is_fresh():
                return list(self._cache.monitors)
            if self._inflight:
                # Another thread is already refreshing; return what we have
                return list(self._cache.monitors)
            self._inflight = True

        try:
            monitors, source = self._fetch()
            with self._lock:
                self._cache = SlumCache(
                    monitors=monitors,
                    fetched_at=time.time(),
                    last_error=None,
                    last_source=source,
                )
            logger.info(
                "SLUM: cached %d monitors from source=%s url=%s",
                len(monitors), source, self.url,
            )
            return list(monitors)
        except Exception as e:  # noqa: BLE001
            logger.warning("SLUM refresh failed: %s", e)
            with self._lock:
                self._cache.last_error = str(e)
            return list(self._cache.monitors)
        finally:
            with self._lock:
                self._inflight = False

    def get_monitors(self, refresh_if_stale: bool = True) -> List[SlumMonitorEntry]:
        """Return current monitors, refreshing from network if cache is stale."""
        if refresh_if_stale and not self.is_fresh():
            self.refresh(force=False)
        with self._lock:
            return list(self._cache.monitors)

    @staticmethod
    def _host_of(url_or_host: str) -> str:
        if not url_or_host:
            return ""
        from urllib.parse import urlparse
        if "://" in url_or_host:
            return (urlparse(url_or_host).hostname or "").lower()
        return url_or_host.lower()

    def get_status(self, url_or_host: str) -> Optional[SlumMonitorEntry]:
        """Look up a single source by URL or hostname."""
        from urllib.parse import urlparse
        target = self._host_of(url_or_host)
        if not target:
            return None
        for m in self.get_monitors():
            if not m.url:
                continue
            host = (urlparse(m.url).hostname or "").lower()
            if host and (host == target or host.endswith("." + target)):
                return m
        return None

    def get_ranked_sources(
        self,
        candidate_urls: Optional[Iterable[str]] = None,
        only_up: bool = True,
    ) -> List[SlumMonitorEntry]:
        """
        Return sources ordered by score (highest first).

        If ``candidate_urls`` is provided, only monitors whose hostname
        matches one of those URLs are considered.
        """
        from urllib.parse import urlparse
        monitors = self.get_monitors()
        if candidate_urls is not None:
            target_hosts = {
                self._host_of(u) for u in candidate_urls if u
            }
            target_hosts.discard("")
            monitors = [
                m for m in monitors
                if (urlparse(m.url).hostname or "").lower() in target_hosts
            ]
        if only_up:
            monitors = [m for m in monitors if m.is_up]
        return sorted(monitors, key=lambda m: m.score, reverse=True)

    def rank_urls(
        self,
        urls: Sequence[str],
        only_up: bool = True,
    ) -> List[str]:
        """
        Given a list of candidate URLs, return them sorted by SLUM score
        (best first).  URLs not present in SLUM fall to the end in
        their original order.
        """
        ranked = self.get_ranked_sources(candidate_urls=urls, only_up=only_up)
        ranked_urls = [m.url for m in ranked]
        seen = set(ranked_urls)
        for u in urls:
            if u and u not in seen:
                ranked_urls.append(u)
                seen.add(u)
        return ranked_urls

    def get_report(self) -> dict:
        """Return a dict suitable for the settings/debug UI."""
        monitors = self.get_monitors(refresh_if_stale=False)
        with self._lock:
            return {
                "url": self.url,
                "enabled": self.enabled,
                "cache_ttl_seconds": self.cache_ttl_seconds,
                "fetched_at": self._cache.fetched_at,
                "age_seconds": self.age_seconds(),
                "is_fresh": self.is_fresh(),
                "last_error": self._cache.last_error,
                "last_source": self._cache.last_source,
                "extra_endpoints": list(self.extra_endpoints),
                "local_html_fallback": self.local_html_fallback,
                "monitor_count": len(monitors),
                "up_count": sum(1 for m in monitors if m.is_up),
                "down_count": sum(1 for m in monitors if not m.is_up),
                "monitors": [
                    m.to_dict()
                    for m in sorted(monitors, key=lambda x: x.score, reverse=True)
                ],
            }
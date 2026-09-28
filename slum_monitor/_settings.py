"""
Internal module — do not import from outside :mod:`slum_monitor`.

Best-effort: re-configure a :class:`SlumMonitor` from the user's
``data/settings.json`` file (via ``settings_manager``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ._constants import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    DEFAULT_SLUM_URL,
)

if TYPE_CHECKING:
    from ._monitor import SlumMonitor

logger = logging.getLogger(__name__)

__all__ = ["load_settings_into_monitor"]


def load_settings_into_monitor(mon: "SlumMonitor") -> None:
    """Best-effort: re-configure ``mon`` from settings_manager."""
    try:
        from settings_manager import SettingsManager  # type: ignore
    except Exception:
        return
    try:
        path = Path("data/settings.json")
        if not path.exists():
            return
        sm = SettingsManager(path)
        s = sm.settings
        mon.enabled = bool(getattr(s, "slum_enabled", True))
        mon.url = (getattr(s, "slum_url", DEFAULT_SLUM_URL) or DEFAULT_SLUM_URL).rstrip("/")
        mon.cache_ttl_seconds = max(
            30, int(getattr(s, "slum_cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS))
        )
        mon.fetch_timeout_seconds = max(
            2, int(getattr(s, "slum_fetch_timeout_seconds", DEFAULT_FETCH_TIMEOUT_SECONDS))
        )
        ee = getattr(s, "slum_extra_endpoints", None)
        if ee is not None:
            # Empty list is meaningful: don't substitute the default
            mon.extra_endpoints = [str(u) for u in ee if u]
        lh = getattr(s, "slum_local_html_fallback", None)
        if lh is not None:
            mon.local_html_fallback = lh
    except Exception as e:  # noqa: BLE001
        logger.debug("SLUM: settings load failed: %s", e)
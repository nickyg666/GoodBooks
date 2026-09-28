"""
Internal module — do not import from outside :mod:`slum_monitor`.

Parsers for the three SLUM data sources:

* UptimeFlare ``/api/data`` (primary, live).
* Uptime Kuma status-page JSON (secondary, live).
* Next.js ``__NEXT_DATA__`` SSR HTML (tertiary, on-disk fallback).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List

from ._constants import MONITOR_ID_TO_URL
from ._types import SlumMonitorEntry

logger = logging.getLogger(__name__)

__all__ = [
    "parse_uptimeflare_data",
    "parse_kuma_data",
    "parse_next_data_html",
]


# ----------------------------------------------------------------------
# UptimeFlare /api/data
# ----------------------------------------------------------------------

def parse_uptimeflare_data(data: dict, source: str) -> List[SlumMonitorEntry]:
    """
    Parse the UptimeFlare ``/api/data`` JSON payload.

    Schema (per the lyc8503/UptimeFlare source)::

        {
          "up": 26, "down": 1,
          "updatedAt": 1780554089,
          "monitors": {
            "<id>": {
              "up": true, "latency": 443,
              "location": "CDG", "message": "OK"
            }, ...
          },
          "maintenances": []
        }

    URLs are filled in from :data:`MONITOR_ID_TO_URL` since this feed
    only carries the monitor id.
    """
    monitors_in = data.get("monitors") or {}
    if not isinstance(monitors_in, dict):
        return []
    out: List[SlumMonitorEntry] = []
    for mid, m in monitors_in.items():
        if not isinstance(m, dict):
            continue
        is_up = bool(m.get("up", False))
        msg = (m.get("message") or "").strip()
        if is_up:
            uptime_pct = 100.0
            last_error = None
        else:
            uptime_pct = 0.0
            last_error = msg or None
        latency = m.get("latency")
        try:
            latency_int: int | None = int(latency) if latency is not None else None
        except (TypeError, ValueError):
            latency_int = None
        url = MONITOR_ID_TO_URL.get(mid, "")
        out.append(
            SlumMonitorEntry(
                name=mid,
                url=url,
                is_up=is_up,
                uptime_pct=uptime_pct,
                latency_ms=latency_int,
                last_error=last_error,
                source=source,
            )
        )
    return out


# ----------------------------------------------------------------------
# Uptime Kuma status-page
# ----------------------------------------------------------------------

def _kuma_active_incident_text(data: dict) -> str:
    """Return a short text description of any active Kuma incident, or ''."""
    inc = data.get("incident")
    if isinstance(inc, dict):
        return (inc.get("content") or inc.get("title") or "").strip()
    if isinstance(inc, list) and inc:
        return " / ".join(
            str(x.get("content") or x.get("title") or "") for x in inc
        )
    return ""


def parse_kuma_data(data: dict, source: str) -> List[SlumMonitorEntry]:
    """
    Parse an Uptime Kuma status-page JSON payload.

    Schema::

        {
          "config": {...},
          "incident": {...} or [...],
          "publicGroupList": [
            {"name": "Group", "monitorList": [
              {"id": 7, "name": "Libgen+ VG", "type": "keyword",
               "url": "https://libgen.vg/"}, ...
            ]}, ...
          ],
          "maintenanceList": [...]
        }

    Kuma doesn't expose per-monitor up/down in the public status page,
    so we conservatively assume every monitor is UP.  The active
    incident text is logged for debugging.
    """
    groups = data.get("publicGroupList") or []
    if not isinstance(groups, list):
        return []
    active_incident_text = _kuma_active_incident_text(data)

    out: List[SlumMonitorEntry] = []
    for g in groups:
        for m in g.get("monitorList") or []:
            if not isinstance(m, dict):
                continue
            if m.get("type") == "group":  # nested group, not a leaf monitor
                continue
            url = m.get("url") or ""
            name = m.get("name") or url
            out.append(
                SlumMonitorEntry(
                    name=name,
                    url=url,
                    is_up=True,
                    uptime_pct=100.0,
                    latency_ms=None,
                    last_error=None,
                    source=source,
                )
            )
    if active_incident_text:
        logger.info("SLUM: kuma has pinned incident: %s", active_incident_text)
    return out


# ----------------------------------------------------------------------
# Next.js __NEXT_DATA__ SSR HTML
# ----------------------------------------------------------------------

_NEXT_DATA_RE = re.compile(
    r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _down_monitor_ids(incident: dict) -> set:
    """From a UptimeFlare ``incident`` dict, return the set of currently-DOWN monitor ids."""
    down: set = set()
    for mid, info in incident.items():
        if not isinstance(info, dict):
            continue
        ends = info.get("end") or []
        if not ends:
            continue
        last_end = ends[-1]
        if last_end is None:
            down.add(mid)
    return down


def parse_next_data_html(html: bytes, source: str) -> List[SlumMonitorEntry]:
    """
    Parse the SSR ``__NEXT_DATA__`` blob from the captured HTML file.

    Walks the UptimeFlare-compacted state to recover monitor names,
    URLs, and the currently-DOWN monitor set.
    """
    try:
        text = html.decode("utf-8", errors="replace")
    except Exception:
        return []
    match = _NEXT_DATA_RE.search(text)
    if not match:
        return []
    try:
        outer = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    page_props = (outer.get("props") or {}).get("pageProps") or {}
    monitors_list = page_props.get("monitors") or []
    compacted = page_props.get("compactedStateStr") or ""
    inner: dict = {}
    if compacted:
        try:
            inner = json.loads(compacted)
        except json.JSONDecodeError:
            inner = {}

    incident = (inner.get("incident") or {}) if isinstance(inner, dict) else {}
    down_ids = _down_monitor_ids(incident)
    public_ids = {m.get("id") for m in monitors_list if isinstance(m, dict)}

    out: List[SlumMonitorEntry] = []
    for mon in monitors_list:
        if not isinstance(mon, dict):
            continue
        mid = mon.get("id") or ""
        name = mon.get("name") or mid
        url = mon.get("statusPageLink") or ""
        is_up = mid not in down_ids
        out.append(
            SlumMonitorEntry(
                name=name,
                url=url,
                is_up=is_up,
                uptime_pct=100.0 if is_up else 0.0,
                latency_ms=None,
                last_error=None,
                source=source,
            )
        )
    # Hidden monitors (incident data but not in the public list)
    for mid, info in incident.items():
        if mid in public_ids or not isinstance(info, dict):
            continue
        is_up = mid not in down_ids
        out.append(
            SlumMonitorEntry(
                name=mid,
                url="",
                is_up=is_up,
                uptime_pct=100.0 if is_up else 0.0,
                latency_ms=None,
                last_error=None,
                source=source,
            )
        )
    return out


# ----------------------------------------------------------------------
# Back-compat aliases (the public module used underscore-prefixed names
# before the package refactor; keep them so any test or caller that
# referenced them still works).
# ----------------------------------------------------------------------

_parse_uptimeflare_data = parse_uptimeflare_data
_parse_kuma_data = parse_kuma_data
_parse_next_data_html = parse_next_data_html
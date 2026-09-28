#!/usr/bin/env python3
"""
Self-test for the SLUM monitor package.

Run with: python -m slum_monitor
"""

from __future__ import annotations

import logging
import os

from ._monitor import SlumMonitor

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mon = SlumMonitor(
        url=os.environ.get("SLUM_URL", "https://open-slum.pages.dev/"),
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
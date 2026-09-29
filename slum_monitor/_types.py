"""
Internal module — do not import from outside :mod:`slum_monitor`.

Dataclasses for the SLUM monitor layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional

from ._affinity import source_affinity


@dataclass
class SlumMonitorEntry:
    """One monitor from a SLUM status feed."""

    name: str
    url: str
    is_up: bool
    uptime_pct: float = 0.0
    latency_ms: Optional[int] = None
    last_error: Optional[str] = None
    source: str = ""  # which feed this came from ("uptimeflare", "kuma", "local")

    @property
    def score(self) -> float:
        """0..1 availability score used for ranking.

        NOTE: this MUST be a property. Without the decorator every ranking
        compared bound method objects, so get_ranked_sources() effectively
        returned an arbitrary order instead of score order.
        """
        if not self.is_up:
            return 0.0
        uptime_component = max(0.0, min(1.0, self.uptime_pct / 100.0)) * 0.5
        if self.latency_ms is None or self.latency_ms <= 0:
            latency_component = 0.0
        else:
            latency_component = max(0.0, 1.0 - (self.latency_ms / 10000.0)) * 0.2
        affinity_component = source_affinity(self.url) * 0.3
        return uptime_component + latency_component + affinity_component

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = self.score
        return d


@dataclass
class SlumCache:
    """In-process cache state for a :class:`SlumMonitor`."""

    monitors: List[SlumMonitorEntry] = field(default_factory=list)
    fetched_at: float = 0.0
    last_error: Optional[str] = None
    last_source: str = ""  # which feed was used last ("uptimeflare", "kuma", "local", "")
"""Single source of truth for which mirrors are live right now.

Everything that used to hardcode `https://libgen.li` or
`https://annas-archive.gl` now asks SLUM (the canonical source) via
slum_canonical, with a cached, ordered, de-duplicated list.

Why this matters: the download path had `libgen.li` hardcoded in at least
six places (get.php URLs, Referer headers, link rewriting), so when a
mirror goes away the app keeps trying that one host and silently returns
nothing -- which is exactly how the pipeline ended up delivering zero books
while every individual function looked correct.
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# Fallbacks only, used when SLUM cannot be reached at all.
_FALLBACK_AA = ("https://annas-archive.gl",)
_FALLBACK_LG = ("https://libgen.li", "https://libgen.lc")


def aa_mirrors(refresh: bool = False) -> List[str]:
    """Live Anna's Archive frontends, best first."""
    try:
        import slum_canonical as sc
        if refresh:
            sc.refresh_now()
        got = sc.live_aa_mirrors()
        if got:
            return got
    except Exception as exc:
        logger.debug("SLUM unavailable for AA mirrors: %s", exc)
    return list(_FALLBACK_AA)


def libgen_mirrors(refresh: bool = False) -> List[str]:
    """Live libgen mirrors, best first."""
    try:
        import slum_canonical as sc
        if refresh:
            sc.refresh_now()
        got = sc.live_libgen_mirrors()
        if got:
            return got
    except Exception as exc:
        logger.debug("SLUM unavailable for libgen mirrors: %s", exc)
    return list(_FALLBACK_LG)


def best_aa(refresh: bool = False) -> str:
    m = aa_mirrors(refresh)
    return m[0] if m else _FALLBACK_AA[0]


def best_libgen(refresh: bool = False) -> str:
    m = libgen_mirrors(refresh)
    return m[0] if m else _FALLBACK_LG[0]


def libgen_get_urls(md5: str, limit: int = 6) -> List[str]:
    """get.php URLs for an md5 across every live libgen mirror."""
    out, seen = [], set()
    for host in libgen_mirrors():
        if not md5:
            continue
        u = f"{host.rstrip('/')}/get.php?md5={md5}"
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= limit:
            break
    return out


def resolve_relative(url: str, base: Optional[str] = None) -> str:
    """Rewrite a relative or host-specific libgen URL onto a live mirror."""
    if not url:
        return url
    if url.startswith("http"):
        return url
    root = (base or best_libgen()).rstrip("/")
    if url.startswith("/"):
        return root + url
    return f"{root}/{url}"

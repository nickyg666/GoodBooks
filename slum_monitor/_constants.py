"""
Internal module — do not import from outside :mod:`slum_monitor`.

Defaults, mirrors, and the canonical monitor-id → URL map.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------

DEFAULT_SLUM_URL = "https://open-slum.pages.dev/"
DEFAULT_CACHE_TTL_SECONDS = 300      # 5 min; the page itself reloads after 5 min
DEFAULT_FETCH_TIMEOUT_SECONDS = 15   # the JSON endpoints are tiny

DEFAULT_EXTRA_ENDPOINTS: List[str] = [
    "https://open-slum.org/api/status-page/slum",
]

DEFAULT_LOCAL_HTML_FALLBACK = (
    "/home/das/.local/share/opencode/tool-output/"
    "tool_e937bb3ba001a4uQP5aq0h8wbC"
)

HTTP_HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.5",
}


# ----------------------------------------------------------------------
# Mirror lists (ordered by "natural" preference; SLUM re-orders by score)
# ----------------------------------------------------------------------

# Anna's Archive mirrors.  SLUM tracks these and we re-try on them when
# the primary AA host is down.  AA mirrors more shadow-library content
# than libgen alone, so this list is consulted before any libgen-only
# fallback.
AA_MIRRORS: Tuple[str, ...] = (
    "https://annas-archive.org",
    "https://annas-archive.se",
    "https://annas-archive.li",
    "https://annas-archive.gl",
    "https://annas-archive.in",
    "https://software.annas-archive.li",
    "https://welib.org",
)

# Map of libgen short aliases → canonical URL.  Used by the integrated
# borrower (libgen-api-enhanced) which accepts these short aliases.
LIBGEN_SHORT_ALIASES: Dict[str, str] = {
    "li":  "https://libgen.li",
    "lc":  "https://libgen.lc",
    "la":  "https://libgen.la",
    "rs":  "https://libgen.rs",
    "is":  "https://libgen.is",
    "bz":  "https://libgen.bz",
    "vg":  "https://libgen.vg",
    "gl":  "https://libgen.gl",
}


# ----------------------------------------------------------------------
# Canonical monitor-id → URL map
# ----------------------------------------------------------------------
# The /api/data feed (UptimeFlare) only gives us the monitor id, not the
# URL.  We need this map to make rank_urls() and get_status(url_or_host)
# work.  The list comes from the SSR HTML's pageProps.monitors[].statusPageLink
# for each public monitor.  Hidden monitors (e.g. annas_archive_pm,
# annas_archive_in) intentionally have no URL.
MONITOR_ID_TO_URL: Dict[str, str] = {
    "annas_archive_li":           "https://annas-archive.li",
    "annas_archive_gl":           "https://annas-archive.gl",
    "annas_archive_in":           "",  # hidden
    "annas_archive_pm":           "",  # hidden
    "welib_org":                  "https://welib.org",
    "software_annas_archive_li":  "https://software.annas-archive.li",
    "search_test_aali":           "",
    "libgen_bz":                  "https://libgen.bz",
    "libgen_li":                  "https://libgen.li",
    "libgen_la":                  "https://libgen.la",
    "libgen_vg":                  "https://libgen.vg",
    "libgen_gl":                  "https://libgen.gl",
    "search_test_libgen_bz":      "",
    "z_library_sk":               "https://z-library.sk",
    "1lib_sk":                    "https://1lib.sk",
    "z_lib_gd":                   "https://z-lib.gd",
    "z_lib_gl":                   "https://z-lib.gl",
    "go_to_library_sk":           "https://go-to-library.sk",
    "library_access_sk":          "https://library-access.sk",
    "scihub_ru":                  "https://sci-hub.ru",
    "scihub_su":                  "https://sci-hub.su",
    "scihub_st":                  "https://sci-hub.st",
    "scihub_red":                 "https://sci-hub.red",
    "scihub_box":                 "https://sci-hub.box",
    "scinet_xyz":                 "https://sci-net.xyz",
    "libstc_cc":                  "https://libstc.cc",
    "libstc_nexus":               "https://libstc.nexus",
    "liber3":                     "https://liber3.eth.limo/",
    "motw":                       "https://library.memoryoftheworld.org/",
}
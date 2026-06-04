import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union
from urllib.parse import urljoin, urlparse, urlencode

import re
import time
import threading
import socket

import requests
from lxml import html
from stealth_browser import resolve_slow_download_link, solve_cloudflare_challenge

try:
    from libgen_api_enhanced import LibgenSearch
    LIBGEN_AVAILABLE = True
except ImportError:
    LIBGEN_AVAILABLE = False

# Set global socket timeout to prevent hung connections
socket.setdefaulttimeout(20)

logger = logging.getLogger(__name__)

ENABLE_ZLIB = True  # Z-lib re-enabled now that it's back up
SAFE_FILENAME_CHARS = set(
    "-_.() abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)

# Global semaphore for concurrent downloads
_DOWNLOAD_SEMAPHORE = threading.Semaphore(1)
# Global semaphore for Cloudflare resolution (single-threaded to avoid rate limiting)
_CLOUDFLARE_SEMAPHORE = threading.Semaphore(1)
# Track 429 errors for auto-throttling
_429_ERROR_COUNT = 0
_429_RESET_TIME = time.time()
_429_LOCK = threading.Lock()
MAX_429_ERRORS_PER_MINUTE = 3
_FEED_CANCEL_FLAG = False
# Download URL cache to avoid re-extracting the same URLs
MAX_DOWNLOAD_RETRIES = 2
MAX_CLOUDFLARE_ATTEMPTS = 1
CLOUDFLARE_TIMEOUT = 8


def set_download_concurrency(max_concurrent: int) -> None:
    """
    Configure the global max number of concurrent downloads.

    Safe bounds:
      - min: 1
      - max: 16
    """
    global _DOWNLOAD_SEMAPHORE
    try:
        value = int(max_concurrent)
    except Exception:
        value = 2

    if value < 1:
        value = 1
    if value > 16:
        value = 16

    _DOWNLOAD_SEMAPHORE = threading.Semaphore(value)
    logger.info("Download concurrency set to %d", value)


# Mirror health tracking
_MIRROR_HEALTH = {}  # {hostname: {"status": "up|down", "last_checked": time, "errors": int}}
_MIRROR_HEALTH_LOCK = threading.Lock()
MIRROR_HEALTH_CHECK_INTERVAL = 300  # Re-check mirrors every 5 minutes
MIRROR_ERROR_THRESHOLD = 3  # Mark mirror as down after 3 consecutive errors

KNOWN_MIRRORS = [
    "https://libgen.li",
    "https://libgen.lc", 
    "https://libgen.rs",
    "https://libgenrs.is",
    "https://annas-archive.se",
    "https://annas-archive.org",
]


def check_mirror_health(url: str, timeout: int = 5) -> bool:
    """
    Check if a mirror is reachable with a simple HEAD/GET request.
    Returns True if reachable, False otherwise.
    """
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname or parsed.netloc
        
        with _MIRROR_HEALTH_LOCK:
            last_check = _MIRROR_HEALTH.get(hostname, {}).get("last_checked", 0)
            if time.time() - last_check < MIRROR_HEALTH_CHECK_INTERVAL:
                # Use cached status if checked recently
                return _MIRROR_HEALTH.get(hostname, {}).get("status") == "up"
        
        # Test reachability with HEAD request
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        is_reachable = response.status_code < 500
        
        with _MIRROR_HEALTH_LOCK:
            _MIRROR_HEALTH[hostname] = {
                "status": "up" if is_reachable else "down",
                "last_checked": time.time(),
                "status_code": response.status_code,
                "errors": 0 if is_reachable else (_MIRROR_HEALTH.get(hostname, {}).get("errors", 0) + 1),
            }
            if is_reachable:
                logger.debug("Mirror %s is reachable (status=%d)", hostname, response.status_code)
            else:
                logger.warning("Mirror %s returned status %d", hostname, response.status_code)
        
        return is_reachable
    except Exception as e:
        hostname = urlparse(url).hostname or urlparse(url).netloc
        with _MIRROR_HEALTH_LOCK:
            error_count = _MIRROR_HEALTH.get(hostname, {}).get("errors", 0) + 1
            _MIRROR_HEALTH[hostname] = {
                "status": "down" if error_count >= MIRROR_ERROR_THRESHOLD else "degraded",
                "last_checked": time.time(),
                "errors": error_count,
                "error": str(e)[:100],
            }
            logger.warning("Mirror %s health check failed: %s (error count: %d)", hostname, e, error_count)
        return False


def get_reachable_mirrors(mirror_list: List[str] = None) -> List[str]:
    """
    Filter mirror list to only reachable ones.
    If no list provided, uses KNOWN_MIRRORS.
    """
    if mirror_list is None:
        mirror_list = KNOWN_MIRRORS
    
    reachable = []
    for mirror_url in mirror_list:
        if check_mirror_health(mirror_url):
            reachable.append(mirror_url)
    
    if reachable:
        logger.info("Available mirrors: %s", ', '.join([urlparse(m).hostname for m in reachable]))
    else:
        logger.warning("No reachable mirrors found! Will attempt all mirrors.")
        return mirror_list  # Fall back to all if none reachable
    
    return reachable


def report_mirror_status() -> str:
    """Generate a status report of all known mirrors."""
    with _MIRROR_HEALTH_LOCK:
        if not _MIRROR_HEALTH:
            return "No mirror health data collected yet"
        
        report = []
        for hostname, info in sorted(_MIRROR_HEALTH.items()):
            status = info.get("status", "unknown")
            errors = info.get("errors", 0)
            last_checked = info.get("last_checked", 0)
            time_ago = int(time.time() - last_checked)
            report.append(f"  {hostname}: {status} (errors={errors}, checked {time_ago}s ago)")
        
        return "Mirror Status Report:\n" + "\n".join(report)


def check_and_reset_feed_cancel_flag() -> bool:
    """Check if feed was cancelled due to 429 errors, and reset the flag."""
    global _FEED_CANCEL_FLAG
    was_cancelled = _FEED_CANCEL_FLAG
    _FEED_CANCEL_FLAG = False
    return was_cancelled


def sanitize_filename_preserve_ext(name: str, max_length: int = 180) -> str:
    """
    Make a filesystem- and Kindle-friendly filename, preserving the extension.

    - Split on the last '.' and keep that suffix unchanged.
    - Normalize whitespace in the base.
    - Replace non-safe characters with '_'.
    - Trim and truncate long names.
    """
    name = (name or "").strip() or "download"

    if "." in name:
        base, ext = name.rsplit(".", 1)
        ext_part = "." + ext
    else:
        base = name
        ext_part = ""

    # Normalize whitespace
    base = " ".join(base.split())

    # Filter characters
    cleaned = "".join(
        c if c in SAFE_FILENAME_CHARS else "_" for c in base
    ).strip(" ._")

    if not cleaned:
        cleaned = "download"

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" ._")

    return cleaned + ext_part


# ----------------------------------------------------------------------
# Search options + ranking helpers
# ----------------------------------------------------------------------


@dataclass
class SearchOptions:
    query: str = ""
    language: str = "en"
    extensions: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    autodownload: bool = False

    # Used for ranking + device-awareness (feeds / HTML lists)
    preferred_formats: List[str] = field(default_factory=list)
    kindle_type: str = ""

    # Manual-search tuning
    # - resolve_downloads=False => cheap search, no detail page scraping
    resolve_downloads: bool = True
    # Maximum number of <tr> rows to parse from AA result table
    max_rows: int = 50
    # Optional override for result limit (default is AnnaSource.max_results)
    max_results: Optional[int] = None


def _normalize_string(s: str) -> str:
    """
    Normalize text for fuzzy matching:
      - lowercase
      - convert common punctuation/special chars to spaces (preserve word boundaries)
      - collapse multiple spaces
      - strip leading/trailing whitespace
    """
    s = (s or "").lower()
    # Replace punctuation with space to preserve word boundaries
    # Keep alphanumeric and spaces, convert everything else to space
    s = re.sub(r"[^\w\s]", " ", s)
    # Collapse multiple spaces into one
    s = re.sub(r"\s+", " ", s).strip()
    return s


_FORMAT_PRIORITY_BASE: Dict[str, float] = {
    "azw3": 0.95,
    "azw": 0.95,
    "mobi": 0.9,
    "epub": 1.0,
    "pdf": 0.6,
    "djvu": 0.5,
    "txt": 0.45,
    "cbz": 0.4,
    "cbr": 0.35,
}


def _normalize_fmt(fmt: str) -> str:
    return (fmt or "").lower().strip().lstrip(".")


def _kindle_profile(kindle_type: str) -> str:
    """
    Rough device buckets:
      - 'paperwhite' -> legacy e-ink that strongly prefers AZW3/AZW/MOBI
      - 'oasis' / 'scribe' -> e-ink that is happy with EPUB too
      - everything else -> generic
    """
    kt = (kindle_type or "").lower()
    if not kt:
        return "generic"
    if "paperwhite" in kt:
        return "eink_legacy"
    if any(k in kt for k in ("oasis", "scribe")):
        return "modern_epub"
    return "generic"


def _format_preference_score(formats: List[str], opts: SearchOptions) -> float:
    """
    Score how "nice" this result's available formats are for the given SearchOptions
    (device + preferred formats/ext filters).

    Higher is better. Typical range ~0.0–1.5.
    """
    fmts = {_normalize_fmt(f) for f in (formats or []) if f}
    if not fmts:
        return 0.0

    preferred = {_normalize_fmt(f) for f in (opts.preferred_formats or []) if f}
    ext_filter = {_normalize_fmt(e) for e in (opts.extensions or []) if e}
    profile = _kindle_profile(opts.kindle_type)

    best = 0.0
    for fmt in fmts:
        base = _FORMAT_PRIORITY_BASE.get(fmt, 0.3)
        score = base

        # Explicit preferred_formats (feeds / UI filetype filters)
        if preferred and fmt in preferred:
            score += 0.3

        # Respect ext filters from AA (if present)
        if ext_filter and fmt in ext_filter:
            score += 0.15

        # Device-specific nudges
        if profile == "eink_legacy":
            if fmt in {"azw3", "azw", "mobi"}:
                score += 0.25
            elif fmt == "epub":
                score += 0.05
            elif fmt == "pdf":
                score -= 0.15
        elif profile == "modern_epub":
            if fmt in {"azw3", "azw", "mobi", "epub"}:
                score += 0.2
            elif fmt == "pdf":
                score -= 0.1

        if score > best:
            best = score

    return max(0.0, min(1.5, best))


# Optional: playwright for Cloudflare / human-check bypass
try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # ImportError or anything else
    sync_playwright = None  # type: ignore

class _FakeResponse:
    """
    A minimal mock response object used internally when a real
    ``requests.Response`` cannot be produced. This shim aims to
    emulate enough of the ``requests.Response`` API to allow callers
    (notably ``AnnaSource.download``) to introspect the result and
    handle failures gracefully without raising attribute errors.

    Parameters
    ----------
    status_code : int, optional
        The HTTP status code to expose. Defaults to ``200``.
    text : str, optional
        The textual payload associated with this response. Defaults to
        an empty string.
    reason : str, optional
        A human‑readable reason describing why this fake response was
        constructed (e.g. ``"HTTP 403"``, ``"Connection Error"``). If
        omitted, the ``text`` parameter is reused as the reason.

    Additional positional and keyword arguments are accepted for
    forwards‑compatibility; any unexpected values are ignored.
    """

    def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Default values
        self.status_code: int = 200
        self.text: str = ""
        self.reason: str = ""
        self.headers: Dict[str, str] = {}
        self.content: bytes = b""

        # Allow passing status_code and text positionally, similar to the
        # previous implementation. Additional positional args are ignored.
        if args:
            if len(args) >= 1 and isinstance(args[0], int):
                self.status_code = args[0]
            if len(args) >= 2 and isinstance(args[1], str):
                self.text = args[1]

        # Handle keyword arguments
        status_code = kwargs.get("status_code")
        if isinstance(status_code, int):
            self.status_code = status_code
        text = kwargs.get("text")
        if isinstance(text, str):
            self.text = text
        reason = kwargs.get("reason")
        if isinstance(reason, str):
            self.reason = reason

        # Fallback: if no explicit reason was supplied, derive it from
        # provided textual content or default to an empty string.
        if not self.reason:
            self.reason = self.text or ""

        # Encode the textual content for binary APIs like iter_content
        self.content = (self.text or "").encode("utf-8", errors="ignore")

    def raise_for_status(self) -> None:
        """Mimic ``requests.Response.raise_for_status()`` behavior."""
        if self.status_code >= 400:
            import requests  # Local import to avoid circular import issues
            raise requests.HTTPError(f"HTTP Error: {self.status_code}")

    def iter_content(self, chunk_size: int = 8192):  # type: ignore[no-untyped-def]
        """
        Provide an iterator over the raw content. This mirrors
        ``requests.Response.iter_content()`` and allows consumers to
        stream the response body to disk. For fake responses the
        content is typically very small, so we yield it once and stop.
        """
        if self.content:
            # Yield at most one chunk for fake responses
            yield self.content
        # If there is no content, yield nothing.
        return
# --- End of _FakeResponse Definition ---

ENABLE_ZLIB = True  # Z-lib re-enabled now that it's back up


class AnnaSource:
    """
    Search + download wrapper around Anna's Archive.

    search(query) -> (results, debug_log)
      results: list of dicts with keys:
        id, title, author, cover, detail (md5), formats, downloads
    """

    def __init__(
        self,
        timeout: int = 30,
        base_url: str = "https://annas-archive.org",
        max_results: int = 10,
        max_concurrent_downloads: int = 2,
        enable_zlib: Optional[bool] = None,
        host_throttle_seconds: Optional[float] = None,
        cloudflare_lock: Optional[object] = None,
        **_: object,
    ) -> None:
        """
        Arguments you *can* pass safely from app.py:

        - timeout: request timeout in seconds
        - base_url: AA base URL (if you ever want to swap mirrors)
        - max_results: default max results to return from `search`
        - max_concurrent_downloads: global concurrency for downloads
        - enable_zlib: optional toggle for z-lib mirrors
        - host_throttle_seconds: optional per-host delay between requests
        - cloudflare_lock: threading.Lock to serialize Cloudflare challenge resolution

        Any extra kwargs are accepted via **_ and ignored, so older/newer
        app.py versions won't crash with TypeError.
        """
        global ENABLE_ZLIB

        self.base_url = base_url.rstrip("/")
        self.cache: Dict[str, Dict] = {}
        self.timeout = timeout
        self.max_results = max_results
        self.detail_cache: Dict[str, Dict] = {}
        self.cloudflare_lock = cloudflare_lock
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        
        # Track temporarily unreachable hosts & light per-host throttling
        self.unreachable_hosts: set[str] = set()
        self._last_host_request: Dict[str, float] = {}

        # Allow overriding these from settings, but keep sane defaults
        if host_throttle_seconds is not None:
            self.host_throttle_seconds = float(host_throttle_seconds)
        else:
            self.host_throttle_seconds = 1.0

        if enable_zlib is not None:
            ENABLE_ZLIB = bool(enable_zlib)

        # Wire up global concurrency control from constructor argument
        set_download_concurrency(max_concurrent_downloads)
    def _make_request(self, url: str, stream: bool = False, headers: Optional[Dict] = None, is_download: bool = False) -> Union[requests.Response, _FakeResponse, None]:
        """
        Manages the request, including the stealth/browser resolution for slow_download links.
        For direct momot.rs downloads, includes retry logic and Cloudflare bypass.

        Returns:
            - requests.Response if successful (can be streamed)
            - _FakeResponse if request failed (prevents AttributeError in download)
            - None if request failed outright
        """
        # Copy the base headers from the underlying requests session
        _headers: Dict[str, str] = {}
        try:
            _headers = dict(self.session.headers)
        except Exception:
            _headers = {}
        if headers:
            _headers.update(headers)
        
        # Add robust headers for download requests to avoid CDN rejection
        if is_download:
            _headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Pragma': 'no-cache',
                'Cache-Control': 'no-cache',
            })
            
        try:
            # 1. Check for slow_download link (Anna's Archive protection)
            if "/slow_download/" in url:
                # Use the stealth browser to resolve the challenge and get the *final* download URL
                final_url = resolve_slow_download_link(url, self.timeout)
                
                if final_url is None:
                    logger.warning("Stealth browser failed to resolve challenge for %s", url)
                    fake_resp = _FakeResponse()
                    fake_resp.reason = "Stealth resolution failed"
                    fake_resp.status_code = 0
                    return fake_resp
                
                logger.debug("Stealth browser succeeded for %s, now fetching from final URL", url)
                # For momot.rs direct URLs, retry with Cloudflare bypass if needed
                return self._fetch_with_retries(final_url, _headers, stream, is_download=True)
                
            # 1.5. Check for LibGen /get.php links (need special handling for redirects)
            if "/get.php" in url or "get.php?" in url:
                logger.debug("LibGen /get.php link detected, attempting direct HTTP download with proper headers: %s", url)
                try:
                    # Try direct HTTP request with LibGen-friendly headers
                    _headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                        'Referer': 'https://libgen.li/',
                        'Accept': '*/*',
                        'Accept-Encoding': 'gzip, deflate, br',
                    }
                    
                    # Use session with redirect following and stream to get raw content
                    resp = self.session.get(
                        url,
                        headers=_headers,
                        timeout=self.timeout,
                        stream=False,  # Don't stream - we need the full body
                        allow_redirects=True,  # Follow redirects
                    )
                    
                    # Check if we got a file (binary content)
                    if resp.status_code < 400 and len(resp.content) > 10000:
                        logger.debug("Direct HTTP /get.php download successful: %d bytes from %s", len(resp.content), url)
                        return resp
                    else:
                        logger.warning("Direct HTTP /get.php returned unexpected content: status=%d, size=%d bytes", resp.status_code, len(resp.content))
                        fake_resp = _FakeResponse()
                        fake_resp.status_code = resp.status_code
                        fake_resp.reason = f"Unexpected response: {resp.status_code}"
                        fake_resp._content = resp.content
                        return fake_resp
                        
                except Exception as e:
                    logger.warning("Direct HTTP /get.php download failed: %s", str(e))
                    fake_resp = _FakeResponse()
                    fake_resp.reason = f"Direct HTTP failed: {str(e)}"
                    fake_resp.status_code = 0
                    return fake_resp
                
             # 2. For direct momot.rs URLs, use retry logic with Cloudflare bypass
            if "momot.rs" in url:
                # Try direct fetch first, with retries
                return self._fetch_with_retries(url, _headers, stream, is_download=True)
                
            # 3. Standard direct request (used for covers, search, etc.)
            resp = self.session.get(
                url,
                headers=_headers,
                timeout=self.timeout,
                stream=stream,
            )
            resp.raise_for_status()
            return resp
        
        except requests.exceptions.HTTPError as e:
            global _429_ERROR_COUNT, _429_RESET_TIME, _FEED_CANCEL_FLAG
            
            if e.response is not None:
                status_code = e.response.status_code
                
                # Handle HTTP 429 (Too Many Requests)
                if status_code == 429:
                    with _429_LOCK:
                        current_time = time.time()
                        # Reset counter if 60 seconds have passed
                        if current_time - _429_RESET_TIME >= 60:
                            _429_ERROR_COUNT = 0
                            _429_RESET_TIME = current_time
                        
                        _429_ERROR_COUNT += 1
                        logger.warning("HTTP 429 error (%d occurrences in last 60s) on %s", _429_ERROR_COUNT, url)
                        
                        # If we've hit 3+ 429 errors, flag the feed to cancel
                        if _429_ERROR_COUNT >= MAX_429_ERRORS_PER_MINUTE:
                            logger.critical("Too many 429 errors (%d); cancelling feed scan", _429_ERROR_COUNT)
                            _FEED_CANCEL_FLAG = True
                    
                    fake_resp = _FakeResponse()
                    fake_resp.status_code = 429
                    fake_resp.reason = "Too Many Requests"
                    return fake_resp
                
                # Handle other HTTP errors
                if status_code in {403, 404, 503}:
                    logger.warning("HTTP error (%d) on URL %s", status_code, url)
                    fake_resp = _FakeResponse()
                    fake_resp.status_code = status_code
                    fake_resp.reason = f"HTTP {status_code}"
                    return fake_resp
            
            logger.error("HTTP error fetching %s: %s", url, e)
            fake_resp = _FakeResponse()
            fake_resp.status_code = e.response.status_code if e.response is not None else 0
            fake_resp.reason = "General HTTP Error"
            return fake_resp
        except requests.exceptions.ConnectionError as e:
            logger.error("Connection error fetching %s: %s", url, e)
            fake_resp = _FakeResponse()
            fake_resp.reason = "Connection Error"
            fake_resp.status_code = 0
            return fake_resp
        except Exception:
            logger.exception("Unexpected error during request to %s", url)
            fake_resp = _FakeResponse()
            fake_resp.reason = "Unexpected Error"
            fake_resp.status_code = 0
            return fake_resp
    
    def _fetch_with_retries(self, url: str, headers: Dict[str, str], stream: bool, is_download: bool = False) -> Union[requests.Response, _FakeResponse]:
        """
        Fetch a URL with retry logic. For 403 errors on momot.rs, fail fast (rate-limited).
        No backoff between retries - immediate retries only.
        """
        last_error = None
        is_momot = "momot.rs" in url
        
        for attempt in range(MAX_DOWNLOAD_RETRIES):
            try:
                logger.debug("Download attempt %d/%d for %s", attempt + 1, MAX_DOWNLOAD_RETRIES, url)
                resp = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    stream=stream,
                    allow_redirects=True,
                )
                
                # Check response status
                if resp.status_code == 200 or (resp.status_code >= 200 and resp.status_code < 300):
                    logger.debug("Download succeeded on attempt %d", attempt + 1)
                    return resp
                elif resp.status_code == 403:
                    # For momot.rs 403, we're rate-limited - don't retry, return 403 to trigger fallback
                    if is_momot:
                        logger.warning("HTTP 403 on momot.rs (attempt %d/%d); will trigger fallback to slow_download", attempt + 1, MAX_DOWNLOAD_RETRIES)
                        last_error = "HTTP 403 (momot.rs rate-limited - fallback to slow_download)"
                        # Return a 403 response so caller can trigger slow_download fallback
                        from requests.models import Response
                        resp = Response()
                        resp.status_code = 403
                        resp._content = b""
                        resp.url = url
                        return resp
                    else:
                        # For other hosts, retry
                        last_error = f"HTTP 403 after {attempt + 1} attempts"
                        if attempt < MAX_DOWNLOAD_RETRIES - 1:
                            logger.warning("HTTP 403 on attempt %d/%d, retrying", attempt + 1, MAX_DOWNLOAD_RETRIES)
                            continue
                else:
                    # Other status codes
                    resp.raise_for_status()
                    return resp
                    
            except requests.exceptions.HTTPError as e:
                last_error = str(e)
                if attempt < MAX_DOWNLOAD_RETRIES - 1:
                    logger.warning("HTTP error on attempt %d/%d: %s, retrying", attempt + 1, MAX_DOWNLOAD_RETRIES, e)
                    continue
                else:
                    logger.error("HTTP error on final attempt: %s", e)
            except Exception as e:
                last_error = str(e)
                if attempt < MAX_DOWNLOAD_RETRIES - 1:
                    logger.warning("Error on attempt %d/%d: %s, retrying", attempt + 1, MAX_DOWNLOAD_RETRIES, e)
                    continue
                else:
                    logger.error("Error on final attempt: %s", e)
        
        # All retries failed
        fake_resp = _FakeResponse()
        fake_resp.status_code = 403
        fake_resp.reason = f"Failed after {MAX_DOWNLOAD_RETRIES} attempts: {last_error}"
        return fake_resp

    # ------------------------------------------------------------------
    # Internal network helper
    # ------------------------------------------------------------------
    def _safe_get(self, href: str, for_download: bool = False, allow_redirects: bool = True) -> Optional[requests.Response]:
        """
        Best-effort GET that:

          - Adds a small per-host delay between requests.
          - Tracks *network* failures per host (connection/timeout) and
            temporarily skips them.
          - For metadata/HTML requests (for_download=False), detects Cloudflare /
            anti-bot interstitials and falls back to the stealth browser.

        Returns:
            Response-like object on success, or None on any failure.

        For Cloudflare-bypassed HTML, we return a lightweight object that
        exposes .content, .text, .status_code, .headers, and .url so the
        rest of the parsing code can treat it like a requests.Response.
        """

        # ------------------------------
        # Per-host throttling
        # ------------------------------
        parsed = urlparse(href)
        host = parsed.hostname or parsed.netloc

        if host:
            now = time.monotonic()
            last = self._last_host_request.get(host, 0.0)
            delta = now - last
            if delta < self.host_throttle_seconds:
                time.sleep(self.host_throttle_seconds - delta)
            self._last_host_request[host] = time.monotonic()

        # ------------------------------
        # Raw GET attempt
        # ------------------------------
        try:
            resp = self.session.get(
                href,
                timeout=self.timeout,
                stream=for_download,
                allow_redirects=allow_redirects,
            )
        except requests.RequestException:
            # Network-level failure: just log and return None, will retry on next search
            logger.debug("Network error fetching href=%s", href, exc_info=True)
            return None

        # For pure file downloads we do NOT try to run a browser;
        # the slow_download resolver already handles CF for that path.
        if for_download:
            return resp

        # ------------------------------
        # Metadata / HTML path: Cloudflare detection
        # ------------------------------
        try:
            # This will also read the error page body.
            text_sample = (resp.text or "")[:4096]
        except Exception:
            text_sample = ""

        # Use our local Cloudflare heuristic
        if self._is_cloudflare_challenge(resp):
            logger.warning(
                "Cloudflare / anti-bot challenge detected at %s (status=%s); "
                "attempting stealth browser bypass",
                href,
                resp.status_code,
            )
            resp.close()

            # Try to use the shared stealth browser helper
            try:
                from stealth_browser import solve_cloudflare_challenge
            except Exception as exc:
                logger.warning(
                    "stealth_browser module not available; cannot bypass Cloudflare for %s (%s)",
                    href,
                    exc,
                )
                return None

            if sync_playwright is None:
                logger.warning(
                    "Playwright is not installed; cannot bypass Cloudflare for %s",
                    href,
                )
                return None

            if self.cloudflare_lock:
                with self.cloudflare_lock:
                    content = solve_cloudflare_challenge(
                        href, timeout=self.timeout * 2, wait_seconds=15
                    )
            else:
                content = solve_cloudflare_challenge(
                    href, timeout=self.timeout * 2, wait_seconds=15
                )
            if not content:
                logger.warning(
                    "Stealth browser failed to bypass Cloudflare for %s", href
                )
                return None
            
            # Guard: if content is suspiciously small (< 100 bytes), assume it's an empty response
            # and return None instead of returning corrupted data
            if len(content) < 100:
                logger.warning(
                    "Stealth browser returned suspiciously small content (%d bytes) for %s; "
                    "likely failed to fetch actual page content",
                    len(content),
                    href,
                )
                return None
            
            # Check if the stealth browser returned a direct URL vs HTML content
            # If it's a URL string, we need to return it differently
            if content.strip().startswith("http://") or content.strip().startswith("https://"):
                # The stealth browser extracted a direct download link
                logger.debug(
                    "Stealth browser extracted direct URL for %s: %s", href, content[:80]
                )
                # Return a fake response with the URL as content so it can be handled 
                # as a non-HTML response upstream
                class _DirectURLResponse:
                    def __init__(self, url: str, direct_url: str):
                        self.url = url
                        self.content = direct_url.encode("utf-8")
                        self.status_code = 200
                        self.headers = {"Content-Type": "text/plain"}
                    def iter_content(self, chunk_size=8192):
                        """Yield the content in chunks."""
                        for i in range(0, len(self.content), chunk_size):
                            yield self.content[i:i+chunk_size]
                    def close(self):
                        pass
                return _DirectURLResponse(href, content.strip())

            # Wrap the HTML content in a tiny Response-like shim
            class _FakeResponse:
                def __init__(self, url: str, html_text: str):
                    self.url = url
                    self._text = html_text
                    self.content = html_text.encode("utf-8", errors="ignore")
                    self.status_code = 200
                    self.headers: Dict[str, str] = {}
                def iter_content(self, chunk_size=8192):
                    yield from []
                def close(self):
                    pass
                @property
                def text(self) -> str:
                    return self._text

            logger.debug(
                "Stealth browser succeeded for %s, returning synthetic HTML Response", href
            )
            return _FakeResponse(href, content)

        # ------------------------------
        # Non-Cloudflare HTTP status handling
        # Handle rate limiting with backoff
        if resp.status_code == 429:
            logger.warning(
                "Received 429 Too Many Requests for href=%s; backing off for 15 seconds",
                href,
            )
            resp.close()
            time.sleep(15)  # Back off for 15 seconds on 429
            return None

        try:
            resp.raise_for_status()
        except requests.RequestException:
            logger.debug(
                "HTTP error status=%s for href=%s; returning None",
                resp.status_code,
                href,
                exc_info=True,
            )
            resp.close()
            return None

        return resp

    def _deduplicate_authors(self, author_str: str) -> str:
        """
        Remove duplicate author names from author string.
        Handles formats like:
        - "Author Name [Name, Author]"
        - "Author1; Author2Author1; Author2"
        - "KathiKathi Daley" (CamelCase concatenation)
        """
        if not author_str:
            return ""
        
        # Check if this is "LastName; FirstName" format (single author, 1 semicolon)
        # Don't split these apart
        semicolon_count = author_str.count(';')
        if semicolon_count == 1:
            parts_on_semi = author_str.split(';')
            if len(parts_on_semi) == 2:
                last = parts_on_semi[0].strip()
                first = parts_on_semi[1].strip()
                # Check if both parts look like names (start with uppercase, contain letters)
                if (last and first and 
                    last[0].isupper() and first[0].isupper() and
                    any(c.isalpha() for c in last) and any(c.isalpha() for c in first)):
                    # This is "Last; First" format, return as "First Last"
                    return f"{first} {last}"
        
        # First split by explicit separators
        parts = re.split(r'[;,\[\]]', author_str)
        
        # Expand parts with CamelCase concatenations
        expanded = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Split on CamelCase boundaries: "KathiKathi" -> "Kathi Kathi"
            part = re.sub(r'([a-z])([A-Z])', r'\1 \2', part)
            expanded.extend(part.split())
        
        seen = set()
        unique = []
        
        for part in expanded:
            cleaned = part.strip()
            if not cleaned:
                continue
            # Normalize for comparison
            normalized = re.sub(r'\s+', ' ', cleaned.lower())
            if normalized not in seen:
                seen.add(normalized)
                unique.append(cleaned)
        
        return "; ".join(unique)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        options: Optional[SearchOptions] = None,
    ) -> Tuple[List[Dict], List[str]]:
        """
        Perform a search on Anna's Archive and return ranked results.

        Ranking:
          * Primary: fuzzy similarity between normalized query and "title + author"
          * Secondary: token overlap / near-exact title match
          * Tertiary: format preference (AZW3 > AZW > MOBI > EPUB > PDF > others),
                      nudged by SearchOptions.preferred_formats and kindle_type.

        NEW:
          - We parse and rank all rows FIRST.
          - We only hit AA detail pages / slow_download for the final top-N results
            when opts.resolve_downloads is True.
        """
        opts = options or SearchOptions(query=query)
        if not opts.query:
            opts.query = query

        params: List[Tuple[str, str]] = [
            ("q", opts.query),
            ("display", "table"),
            ("lang", opts.language or "en"),
            ("page", "1"),
            ("index", ""),
            ("sort", ""),
        ]
        for ext in opts.extensions:
            params.append(("ext", ext))
        for src in opts.sources:
            params.append(("acc", src))
        if opts.autodownload:
            params.append(("autodownload", "1"))

        # Cache lookup: avoid repeated fetches for the same logical query
        cache_key = (opts.query or query).strip().lower()
        if cache_key in self.cache:
            logger.debug("Cache hit for query=%r", opts.query)
            cached = self.cache[cache_key]
            # New-style cache: {"results": [...]}
            if isinstance(cached, dict) and "results" in cached:
                cached_results = cached.get("results") or []
                return list(cached_results), [f"Cache hit for query: {opts.query}"]
            # Old-style: list of results
            if isinstance(cached, list):
                return list(cached), [f"Cache hit for query: {opts.query}"]
            # Oldest style: single result entry
            return [cached], [f"Cache hit for query: {opts.query}"]

        url = f"{self.base_url}/search?{urlencode(params, doseq=True)}"
        debug_log: List[str] = [f"Searching: {url}"]
        logger.debug("Issuing search request for query='%s'", opts.query)

        resp = self._safe_get(url)
        if resp is None:
            msg = "Search request failed (network error)"
            debug_log.append(msg)
            logger.error(msg)
            return [], debug_log

        tree = html.fromstring(resp.content)
        logger.debug("Search page fetched (%d bytes)", len(resp.content))

        # All table rows that actually contain <td> cells
        all_rows = tree.xpath("//table//tr[td]")
        logger.debug("Found %d raw table rows with <td>", len(all_rows))

        # Be more forgiving: AA changes column counts sometimes.
        # We treat any row with >=3 <td> as a "result row".
        rows: List[html.HtmlElement] = []
        for r in all_rows:
            cols = r.findall("td")
            if len(cols) >= 3:
                rows.append(r)

        max_rows = opts.max_rows or 15
        rows = rows[:max_rows]
        logger.debug(
            "Filtered to %d rows with >= 3 <td> cells (max_rows=%d)",
            len(rows),
            max_rows,
        )

        results: List[Dict] = []

        # ------------------------------------------------------------------
        # Parse rows with MINIMAL work (no detail/slow_download calls here).
        # ------------------------------------------------------------------
        for row_idx, row in enumerate(rows):
            cols = row.findall("td")
            if len(cols) < 3:
                logger.debug(
                    "Row %d skipped: only %d <td> cells after filter",
                    row_idx,
                    len(cols),
                )
                continue

            try:
                title = "".join(cols[1].xpath(".//text()")).strip()
                author_raw = "".join(cols[2].xpath(".//text()")).strip()
                author = self._deduplicate_authors(author_raw)
                # Get only the FIRST img src to avoid concatenating multiple URLs
                img_srcs = cols[0].xpath(".//img/@src")
                cover = (img_srcs[0] if img_srcs else "").strip()
            except IndexError:
                logger.debug(
                    "Row %d skipped: IndexError while accessing columns (len(cols)=%d)",
                    row_idx,
                    len(cols),
                    exc_info=True,
                )
                continue

            if not title:
                debug_log.append(f"Skipping row {row_idx} without a title")
                continue

            if cover:
                if cover.startswith("//"):
                    # Handle protocol-relative URLs (may have double slashes from malformed HTML)
                    # Convert //domain//path to https://domain/path
                    cover = cover.lstrip("/")  # Remove leading slashes
                    cover = "https://" + cover  # Prepend https://
                elif cover.startswith("/"):
                    cover = urljoin(self.base_url, cover)

            # Formats:
            #   Prefer column 9 (file column) if it exists,
            #   otherwise fall back to the last <td>.
            try:
                if len(cols) > 9:
                    fmt_cell = cols[9]
                else:
                    fmt_cell = cols[-1]
                raw_formats_text = "".join(fmt_cell.xpath(".//text()"))
            except Exception:
                raw_formats_text = ""

            raw_formats = raw_formats_text.lower().split(",")
            formats = [f.strip() for f in raw_formats if f.strip()]
            
            # If no formats extracted from table, use common defaults
            # (table format doesn't always show format info, detail page will have it)
            if not formats:
                formats = ["epub", "mobi", "azw3", "pdf"]

            # --- MD5 extraction (from row links) ---
            md5 = ""
            # Prefer a /md5/ link in the title column
            md5_href_candidates = cols[1].xpath('.//a[contains(@href, "/md5/")]/@href')
            if not md5_href_candidates:
                # Fallback: any /md5/ link in the row
                md5_href_candidates = row.xpath('.//a[contains(@href, "/md5/")]/@href')

            if md5_href_candidates:
                detail_href = md5_href_candidates[0].strip()
                parsed = urlparse(detail_href)
                path = parsed.path or ""
                if "/md5/" in path:
                    md5 = path.split("/md5/", 1)[1].strip()
                else:
                    # last path segment as a fallback
                    md5 = path.rsplit("/", 1)[-1].strip()

            if not md5:
                logger.debug(
                    "Row %d skipped: no md5 link found (title=%r)",
                    row_idx,
                    title,
                )
                continue
            # --- end md5 extraction ---

            # At this stage we DO NOT resolve downloads yet.
            entry: Dict = {
                "title": title,
                "author": author,
                "cover": cover,
                "detail": md5,
                "formats": formats,
                "downloads": {},
                "description": "",
            }

            # Stable ID derived from md5
            entry["id"] = hashlib.sha256(entry["detail"].encode("utf-8")).hexdigest()
            result_id = entry["id"]

            # Normalize formats list (only declared formats for now)
            detected_formats = set(entry["formats"])
            if detected_formats:
                entry["formats"] = sorted(detected_formats)

            results.append(entry)
            # Cache per-result lookup by ID for manual download
            self.cache[result_id] = entry

        debug_log.append(f"Found {len(results)} raw results")
        logger.debug("Search parsed %d raw rows (no ranking)", len(results))

        # If AA returns no results, try libgen fallback
        if not results:
            logger.info("AA search returned no results, trying libgen fallback")
            libgen_results, libgen_log = self._search_libgen_fallback(opts.query)
            debug_log.extend(libgen_log)
            if libgen_results:
                results = libgen_results
                logger.info("libgen fallback provided %d results", len(results))

        # Return raw results immediately - no ranking or download resolution
        # Cache full raw list for this query
        if results:
            self.cache[cache_key] = {"results": results}

        debug_log.append(f"Returning {len(results)} raw results (no ranking)")
        logger.debug(
            "Returning %d raw results (no ranking, no download resolution)",
            len(results),
        )
        return results, debug_log

    def _search_libgen_fallback(self, query: str) -> Tuple[List[Dict], List[str]]:
        """
        Fallback search using libgen-api-enhanced when AA returns no results.
        Converts libgen results to AA format for compatibility.
        """
        debug_log: List[str] = []
        
        if not LIBGEN_AVAILABLE:
            debug_log.append("libgen-api-enhanced not available")
            return [], debug_log
        
        try:
            logger.info("Trying libgen fallback search for query=%r", query)
            ls = LibgenSearch(mirror='libgen.li')
            
            # Search libgen using correct API signature
            results_libgen = ls.search_title(query)
            
            if not results_libgen:
                debug_log.append("libgen fallback returned no results")
                return [], debug_log
            
            logger.debug("libgen returned %d results", len(results_libgen))
            debug_log.append(f"libgen fallback returned {len(results_libgen)} results")
            
            # Convert libgen results to AA format
            results: List[Dict] = []
            for item in results_libgen[:15]:  # Limit to top 15
                try:
                    # Extract fields from libgen result
                    title = item.get("Title", "").strip()
                    author = item.get("Author", "").strip()
                    if not title:
                        continue
                    
                    # Generate a unique ID based on title+author
                    unique_key = f"{title}|{author}".lower()
                    result_id = hashlib.sha256(unique_key.encode()).hexdigest()
                    
                    entry: Dict = {
                        "title": title,
                        "author": author,
                        "cover": "",  # libgen doesn't provide covers
                        "detail": item.get("MD5", ""),
                        "formats": ["pdf", "epub"],  # Default formats
                        "downloads": {},
                        "description": "",
                        "source": "libgen_fallback",
                        "libgen_item": item,  # Store original item for download
                        "id": result_id,
                    }
                    
                    results.append(entry)
                    self.cache[result_id] = entry
                    
                except Exception as e:
                    logger.debug("Error converting libgen result: %s", e)
                    continue
            
            if results:
                debug_log.append(f"Converted {len(results)} libgen results to AA format")
                logger.info("libgen fallback provided %d results", len(results))
            
            return results, debug_log
            
        except Exception as e:
            error_msg = str(e)
            # Check if error is due to mirror connectivity
            if "Failed to connect" in error_msg or "ConnectTimeout" in error_msg or "ConnectionError" in error_msg:
                logger.warning("libgen mirror unavailable: %s (fallback will retry when mirrors come online)", error_msg)
                debug_log.append(f"libgen mirror currently unavailable - will retry when mirrors come back online")
            else:
                logger.warning("libgen fallback search failed: %s", error_msg)
                debug_log.append(f"libgen fallback error: {error_msg}")
            return [], debug_log

    def manual_search(self, query: str, options: Optional['SearchOptions'] = None) -> Tuple[List[Dict], List[str]]:
        """
        Fast manual search - returns results with title/author/cover instantly.
        Download links are lazy-loaded on demand via mirrors (LibGen, Z-Lib).
        NO ranking, NO enrichment, just raw AA results.
        """
        if not query or not query.strip():
            return [], ["No query provided"]
        
        debug_log: List[str] = []
        results: List[Dict] = []
        
        try:
            query = query.strip()
            logger.info("Manual search: query=%r (raw AA, no ranking)", query)
            
            # Direct AA fetch - no ranking overhead
            from urllib.parse import quote
            url = f"https://annas-archive.org/search?q={quote(query)}&display=table&lang=en&page=1&index=&sort="
            logger.debug("Manual search URL: %s", url)
            
            resp = self._safe_get(url)
            if resp is None:
                debug_log.append("AA fetch failed")
                return [], debug_log
            
            tree = html.fromstring(resp.content)
            rows = tree.xpath("//table//tr[td]")
            debug_log.append(f"Found {len(rows)} raw AA rows")
            
            seen_md5s = set()
            for idx, row in enumerate(rows[:50]):
                try:
                    cells = row.xpath(".//td")
                    if len(cells) < 3:
                        continue
                    
                    # Extract MD5
                    first_cell = cells[0]
                    md5_links = first_cell.xpath(".//a[contains(@href, '/md5/')]")
                    if not md5_links:
                        continue
                    
                    href = md5_links[0].get("href", "").strip()
                    match = re.search(r'/md5/([a-f0-9]{32})', href)
                    if not match:
                        continue
                    md5 = match.group(1).lower()
                    
                    if md5 in seen_md5s:
                        continue
                    seen_md5s.add(md5)
                    
                    # Extract title, author, formats from cells
                    title = cells[1].text_content().strip() if len(cells) > 1 else ""
                    author = cells[2].text_content().strip() if len(cells) > 2 else ""
                    
                    if not title:
                        continue
                    
                    # Extract formats from row text
                    row_text = row.text_content()
                    formats = []
                    for fmt in ['pdf', 'epub', 'mobi', 'azw3', 'txt', 'cbr', 'cbz']:
                        if fmt.upper() in row_text.upper():
                            formats.append(fmt)
                    
                    # Extract cover from first cell
                    cover_url = None
                    imgs = first_cell.xpath(".//img")
                    if imgs:
                        cover_url = imgs[0].get("src", "").strip()
                        # Handle protocol-relative URLs
                        if cover_url:
                            if cover_url.startswith("//"):
                                cover_url = "https:" + cover_url
                            elif cover_url.startswith("/"):
                                cover_url = urljoin(self.base_url, cover_url)
                    
                    result = {
                        "id": md5,
                        "md5": md5,
                        "detail": md5,
                        "title": title,
                        "author": author,
                        "formats": formats or ["pdf"],
                        "cover": cover_url,
                        "description": None,
                    }
                    results.append(result)
                    
                except Exception as e:
                    logger.debug("Row parse error: %s", e)
                    continue
            
            debug_log.append(f"Parsed {len(results)} results (no ranking)")
            logger.info("Manual search returned %d results", len(results))
            
            # Cache results
            for result in results:
                result_id = result.get("id") or result.get("md5", "")
                if result_id:
                    self.cache[result_id] = result
            
            return results, debug_log
            
        except Exception as e:
            logger.exception("Manual search failed for %r", query)
            debug_log.append(f"Error: {e}")
            return [], debug_log
            
        except Exception as e:
            logger.exception("Manual search failed for %r", query)
            debug_log.append(f"Manual search error: {e}")
            return [], debug_log

    # ------------------------------------------------------------------
    # Download discovery / AA detail page
    # ------------------------------------------------------------------
    def _get_downloads(
        self, md5: str, formats: List[str], debug_log: List[str]
    ) -> Tuple[Dict[str, str], Optional[str], str]:
        """
        For a given md5, go to the AA detail page, then follow
        /slow_download/... links (prefer 'no waitlist') to get real file URLs.
        """

        # If we already resolved this md5 once, reuse it
        if md5 in self.detail_cache:
            cached = self.detail_cache[md5]
            logger.debug("Using cached detail for md5=%s", md5)
            debug_log.append(f"Using cached detail for md5={md5}")
            return (
                dict(cached.get("downloads", {})),
                cached.get("cover"),
                cached.get("description", ""),
            )

        detail_url = f"{self.base_url}/md5/{md5}"
        debug_log.append(f"Fetching AA detail page: {detail_url}")

        # Use _safe_get so Cloudflare / host throttling are respected
        resp = self._safe_get(detail_url)
        if resp is None:
            msg = f"Failed to fetch AA detail page for md5={md5}"
            debug_log.append(msg)
            logger.error(msg)
            return {}, None, ""

        tree = html.fromstring(resp.content)

        # --- cover + description ---
        cover_url = self._extract_cover(tree)
        description = self._extract_description(tree)

        downloads: Dict[str, str] = {}

        # Find "Slow Partner" links – we prefer ones mentioning "no waitlist"
        slow_link_els = tree.xpath(
            '//ul//li[contains(@class, "list-disc")]'
            '//a[contains(@href, "/slow_download/")]'
        )
        if not slow_link_els:
            # Fallback: any anchor with /slow_download/
            slow_link_els = tree.xpath('//a[contains(@href, "/slow_download/")]')

        primary_links = []
        secondary_links = []

        for a in slow_link_els:
            href = (a.get("href") or "").strip()
            if not href:
                continue

            # Combine anchor text + parent text to look for "no waitlist"
            anchor_text = " ".join(a.itertext()).lower()
            parent_text = ""
            parent = a.getparent()
            if parent is not None:
                parent_text = " ".join(parent.itertext()).lower()

            text_blob = f"{anchor_text} {parent_text}"

            if "no waitlist" in text_blob:
                primary_links.append(href)
            else:
                secondary_links.append(href)

        ordered_hrefs = primary_links or secondary_links

        debug_log.append(
            f"Found {len(ordered_hrefs)} slow_download links on AA detail page"
        )
        logger.debug(
            "Found %d slow_download links for md5=%s", len(ordered_hrefs), md5
        )

        # Extract external mirror links - ONLY ads.php links with MD5 (direct download) or IPFS
        # Skip biblioservice.php and other search pages
        external_links = []
        for a in tree.xpath('//a[@href]'):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            # Only accept ads.php links with MD5 parameter (direct download) or IPFS links
            if "ads.php" in href and "md5=" in href:
                external_links.append(href)
            elif "ipfs" in href.lower():
                external_links.append(href)
        
        if external_links:
            debug_log.append(f"Found {len(external_links)} external mirror links")
            logger.debug("Found %d external mirror links for md5=%s", len(external_links), md5)

        # Quick test: Try ONE no-waitlist slow_download link to detect DDoS-Guard blocks
        # If all AA slow_download are behind DDoS-Guard, skip directly to external mirrors
        ddos_blocked = False
        if ordered_hrefs and external_links:
            test_href = urljoin(self.base_url, ordered_hrefs[0])
            logger.debug("Testing first slow_download link for DDoS-Guard blocks=%s", test_href)
            test_resp = self._safe_get(test_href)
            if test_resp is None:
                # If _safe_get returns None for AA slow_download, it's likely DDoS-Guard
                # (stealth browser timeout or other CF issue)
                ddos_blocked = True
                logger.warning("AA slow_download test returned None for md5=%s; likely DDoS-Guard block; using external mirrors", md5)
                debug_log.append(f"AA slow_download blocked (stealth browser failed); using external mirrors instead")
            elif test_resp is not None:
                content = test_resp.content.decode("utf-8", errors="replace")
                # Check for DDoS-Guard challenge page (look for ddos-guard signature)
                if "ddos-guard" in content.lower() and len(test_resp.content) < 2000:
                    ddos_blocked = True
                    logger.warning("Detected DDoS-Guard block on AA slow_download for md5=%s; switching to external mirrors", md5)
                    debug_log.append(f"DDoS-Guard detected on slow_download; using external mirrors instead")
        
        # If AA slow_download links are blocked by DDoS-Guard, skip directly to external mirrors
        if ddos_blocked and external_links:
            logger.debug("Skipping AA slow_download links for md5=%s due to DDoS-Guard; trying external mirrors", md5)
            ordered_hrefs = []  # Skip AA slow_download entirely

        # Walk through slow_download URLs until we get at least one real file URL
        for raw_href in ordered_hrefs:
            slow_href = urljoin(self.base_url, raw_href)
            logger.debug("Resolving AA slow_download link=%s md5=%s", slow_href, md5)
            debug_log.append(f"Resolving AA slow_download link={slow_href} md5={md5}")
            try:
                resolved = self._resolve_aa_slow_download(
                    slow_href, md5, formats, debug_log
                )
            except Exception as exc:
                logger.warning(
                    "AA slow_download resolution failed for %s: %s",
                    slow_href,
                    str(exc),
                    exc_info=True,
                )
                debug_log.append(f"AA slow_download resolution failed: {exc}")
                resolved = None

            if not resolved:
                continue

            download_url, fmt = resolved
            fmt = (fmt or "").strip().lower() or "unknown"

            # Don't overwrite existing format
            if fmt in downloads:
                continue

            downloads[fmt] = download_url
            debug_log.append(
                f"Stopping resolution after first success: {fmt} -> {download_url}."
            )
            logger.debug(
                "Stopping slow_download resolution after first success: md5=%s fmt=%s",
                md5,
                fmt,
            )
            break
        
        # If primary (no-waitlist) links failed, try waitlist mirrors as fallback
        # BUT: skip if DDoS-Guard was detected (waitlist mirrors will fail too)
        if not downloads and secondary_links and not ddos_blocked:
            logger.debug("Primary slow_download links failed for md5=%s; trying waitlist mirrors", md5)
            debug_log.append(f"Primary slow_download links failed; trying waitlist mirrors")
            
            for raw_href in secondary_links:
                slow_href = urljoin(self.base_url, raw_href)
                logger.debug("Trying waitlist mirror %s for md5=%s", slow_href, md5)
                debug_log.append(f"Trying waitlist mirror: {slow_href}")
                
                try:
                    resolved = self._resolve_aa_slow_download(
                        slow_href, md5, formats, debug_log
                    )
                except Exception as exc:
                    logger.warning("Waitlist mirror resolution failed for %s: %s", slow_href, str(exc))
                    debug_log.append(f"Waitlist mirror resolution failed: {exc}")
                    resolved = None
                
                if not resolved:
                    continue
                
                download_url, fmt = resolved
                fmt = (fmt or "").strip().lower() or "unknown"
                
                if fmt in downloads:
                    continue
                
                downloads[fmt] = download_url
                debug_log.append(f"Got download from waitlist mirror: {fmt} -> {download_url}")
                logger.debug("Got download from waitlist mirror: md5=%s fmt=%s", md5, fmt)
                break
        
        # If all AA slow_download links failed, try external mirrors as last resort
        if not downloads and external_links:
            logger.debug("AA slow_download links failed for md5=%s; trying external mirrors", md5)
            debug_log.append(f"AA slow_download links failed; trying external mirrors")
            
            # Check health of external mirrors before attempting resolution
            reachable_mirrors = get_reachable_mirrors(KNOWN_MIRRORS)
            if reachable_mirrors:
                logger.info("Available mirrors for external link resolution: %s", 
                           ', '.join([urlparse(m).hostname or m for m in reachable_mirrors]))
                debug_log.append(f"Reachable mirrors: {', '.join([urlparse(m).hostname or m for m in reachable_mirrors])}")
            
            for raw_href in external_links:
                ext_href = urljoin(self.base_url, raw_href) if raw_href.startswith("/") else raw_href
                logger.debug("Trying external mirror %s for md5=%s", ext_href, md5)
                debug_log.append(f"Trying external mirror: {ext_href}")
                
                try:
                    resolved = self._resolve_download_link(ext_href, md5=md5)
                    if not resolved:
                        continue
                    download_url, fmt = resolved
                    fmt = (fmt or "").strip().lower() or "unknown"
                    
                    if fmt in downloads:
                        continue
                    
                    downloads[fmt] = download_url
                    debug_log.append(f"Got download from external mirror: {fmt} -> {download_url}")
                    logger.debug("Got download from external mirror: md5=%s fmt=%s", md5, fmt)
                    break
                except Exception as exc:
                    logger.warning("External mirror resolution failed for %s: %s", ext_href, str(exc))
                    debug_log.append(f"External mirror resolution failed: {exc}")
                    continue

        debug_log.append(
            f"Resolved {len(downloads)} download links for md5={md5} "
            f"(cover={bool(cover_url)} description_len={len(description)})"
        )
        logger.debug(
            "Resolved %d download links for md5=%s (cover=%s description_len=%d)",
            len(downloads),
            md5,
            bool(cover_url),
            len(description),
        )

        # Cache the result so subsequent searches don't re-scrape the same md5 page
        self.detail_cache[md5] = {
            "downloads": dict(downloads),
            "cover": cover_url,
            "description": description,
        }

        return downloads, cover_url, description

    def _get_waitlist_mirrors(self, md5: str) -> Dict[str, str]:
        """
        When primary (no-waitlist) mirrors fail, fetch the waitlist mirrors as fallback.
        This is a lighter-weight version of _get_downloads that only tries secondary links.
        """
        detail_url = f"{self.base_url}/md5/{md5}"
        
        try:
            resp = self._safe_get(detail_url)
            if resp is None:
                return {}
            
            tree = html.fromstring(resp.content)
            
            # Find slow download links
            slow_link_els = tree.xpath(
                '//ul//li[contains(@class, "list-disc")]'
                '//a[contains(@href, "/slow_download/")]'
            )
            if not slow_link_els:
                slow_link_els = tree.xpath('//a[contains(@href, "/slow_download/")]')
            
            # Separate primary (no waitlist) and secondary (with waitlist)
            primary_links = []
            secondary_links = []
            
            for a in slow_link_els:
                href = (a.get("href") or "").strip()
                if not href:
                    continue
                
                anchor_text = " ".join(a.itertext()).lower()
                parent_text = ""
                parent = a.getparent()
                if parent is not None:
                    parent_text = " ".join(parent.itertext()).lower()
                
                text_blob = f"{anchor_text} {parent_text}"
                
                if "no waitlist" in text_blob:
                    primary_links.append(href)
                else:
                    secondary_links.append(href)
            
            # Use secondary (waitlist) links only
            downloads: Dict[str, str] = {}
            formats = []
            debug_log: List[str] = []
            
            for raw_href in secondary_links:
                slow_href = urljoin(self.base_url, raw_href)
                logger.debug("Resolving waitlist mirror for md5=%s: %s", md5, slow_href)
                
                try:
                    resolved = self._resolve_aa_slow_download(
                        slow_href, md5, formats, debug_log
                    )
                except Exception as exc:
                    logger.debug(
                        "Waitlist mirror resolution failed for %s: %s",
                        slow_href,
                        str(exc),
                    )
                    continue
                
                if not resolved:
                    continue
                
                download_url, fmt = resolved
                fmt = (fmt or "").strip().lower() or "unknown"
                
                # Don't overwrite existing format
                if fmt in downloads:
                    continue
                
                downloads[fmt] = download_url
                logger.debug("Got waitlist mirror for md5=%s fmt=%s", md5, fmt)
                break
            
            return downloads
        except Exception:
            logger.debug("Failed to get waitlist mirrors for md5=%s", md5, exc_info=True)
            return {}

    # ------------------------------------------------------------------
    # Resolver helpers (Cloudflare / HTML / mirrors)
    # ------------------------------------------------------------------
    def _is_cloudflare_challenge(self, resp: requests.Response) -> bool:
        """
        Heuristically detect Cloudflare/anti-bot interstitials.
        Only return True for strong indicators to avoid false positives.
        
        EDGE CASE: 403 errors with "invalid" or "expired" in response body are 
        momot.rs rate-limiting, NOT Cloudflare challenges. Return False to trigger 
        fallback to alternative link grab methods.
        """
        if resp is None:
            return False

        # Check for explicit Cloudflare headers FIRST (most reliable)
        server_header = (resp.headers or {}).get("Server", "").lower()
        cf_ray = (resp.headers or {}).get("cf-ray") or (resp.headers or {}).get("CF-RAY")
        
        if "cloudflare" in server_header or cf_ray is not None:
            return True
        
        # For 403/503 errors, check for momot.rs rate-limiting edge case BEFORE treating as Cloudflare
        if resp.status_code in {403, 503}:
            try:
                lower_text = (resp.text or "").lower()
                
                # EDGE CASE: If response contains "invalid" or "expired", this is momot.rs 
                # rate-limiting, NOT Cloudflare. Don't try stealth browser on this.
                if "invalid" in lower_text or "expired" in lower_text:
                    logger.info(
                        "HTTP %d with 'invalid' or 'expired' in response body detected; "
                        "this is momot.rs rate-limiting, not Cloudflare. Will use alternative methods.",
                        resp.status_code
                    )
                    return False
                
                # Check for Cloudflare-specific indicators
                cloudflare_indicators = [
                    "just a moment",
                    "attention required",
                    "checking your browser",
                    "verify you are human",
                ]
                if any(indicator in lower_text for indicator in cloudflare_indicators):
                    return True
            except Exception:
                pass
        
        return False

    def _is_html_response(self, url: str) -> bool:
        """Best-effort HEAD check to avoid returning HTML interstitials as downloads."""
        try:
            resp = self.session.head(
                url, allow_redirects=True, timeout=self.timeout, stream=False
            )
        except Exception as exc:
            logger.debug("HEAD request to %s failed: %s, assuming not HTML", url, exc)
            return False

        try:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            is_html = "text/html" in content_type
            logger.debug("HEAD check for %s: content_type=%s is_html=%s", url, content_type, is_html)
            return is_html
        finally:
            resp.close()

    def _resolve_aa_slow_download(
        self,
        slow_href: str,
        md5: str,
        formats: List[str],
        debug_log: List[str],
    ) -> Optional[Tuple[str, str]]:
        """
        Follow an AA /slow_download/... link and try to extract the final
        direct file URL.

        If the response is a Cloudflare challenge page, optionally try
        using Playwright (if available).
        
        Uses a single-threaded semaphore to avoid rate-limiting from parallel
        Cloudflare resolution attempts.
        """
        with _CLOUDFLARE_SEMAPHORE:
            return self._resolve_aa_slow_download_impl(slow_href, md5, formats, debug_log)

    def _resolve_aa_slow_download_impl(
        self,
        slow_href: str,
        md5: str,
        formats: List[str],
        debug_log: List[str],
    ) -> Optional[Tuple[str, str]]:
        """Implementation of slow_download resolution (single-threaded via semaphore)."""
        debug_log.append(f"Resolving AA slow_download link: {slow_href}")
        logger.debug("Resolving AA slow_download link=%s md5=%s", slow_href, md5)

        resp = self._safe_get(slow_href)
        if resp is None:
            return None

        content_type = (resp.headers.get("Content-Type") or "").lower()
        
        # Log small responses for debugging
        if len(resp.content) < 100:
            logger.warning(
                "Small response (%d bytes) from slow_download link %s (md5=%s) - dumping content",
                len(resp.content),
                slow_href,
                md5,
            )
            try:
                content_preview = resp.content.decode("utf-8", errors="replace")
                logger.warning("Small response content:\n%s", content_preview)
                debug_log.append(f"SMALL_RESPONSE: {len(resp.content)} bytes: {content_preview[:200]}")
            except Exception as e:
                logger.warning("Failed to decode small response: %s", e)
                debug_log.append(f"SMALL_RESPONSE: {len(resp.content)} bytes (binary)")

        # If it's already a non-HTML response, treat it as a direct URL result.
        # This happens when stealth browser extracted a direct download link.
        if "text/html" not in content_type:
            # Check if this is a _DirectURLResponse (content is the actual URL)
            try:
                direct_url = resp.content.decode("utf-8").strip()
                if direct_url.startswith(("http://", "https://")):
                    # This is the actual extracted URL from stealth browser
                    logger.debug("Using stealth-browser-extracted URL: %s", direct_url[:80])
                    fmt = self._detect_format("", direct_url, formats) or "bin"
                    resp.close()
                    debug_log.append(
                        f"AA slow_download returned stealth-browser-extracted URL fmt={fmt}"
                    )
                    return direct_url, fmt
            except Exception as e:
                logger.debug("Failed to decode direct URL from response: %s", e)
            
            # Fallback: treat slow_href as direct URL (for actual file responses)
            cd = resp.headers.get("Content-Disposition") or ""
            filename_match = re.search(r'filename="?([^";]+)"?', cd)
            ext = ""
            if filename_match:
                fname = filename_match.group(1)
                if "." in fname:
                    ext = fname.rsplit(".", 1)[-1].lower()

            fmt = ext or self._detect_format("", slow_href, formats) or "bin"
            resp.close()
            debug_log.append(
                f"AA slow_download returned non-HTML; using slow_href directly fmt={fmt}"
            )
            return slow_href, fmt

        # HTML response – parse and look for a real file URL
        if self._is_cloudflare_challenge(resp):
            resp.close()
            debug_log.append(
                "Cloudflare / human-check detected on slow_download page; "
                "skipping (Anna's Archive is blocking us)"
            )
            logger.debug(
                "Cloudflare challenge detected at slow_href=%s (md5=%s); skipping immediately",
                slow_href,
                md5,
            )
            return None

        doc = html.fromstring(resp.content)

        # 1) Look for an obvious "Download" button/link
        candidates: List[str] = []
        candidates.extend(
            doc.xpath(
                '//a[contains(@class, "btn") or '
                'contains(translate(text(),"DOWNLOAD","download"), "download")]'
                "/@href"
            )
        )

        # 2) Sometimes the URL is plain text inside <p><span><span>...</span></span>
        if not candidates:
            for txt in doc.xpath("//p//text()"):
                s = txt.strip()
                if s.startswith("http://") or s.startswith("https://"):
                    candidates.append(s)
                    break

        if not candidates:
            debug_log.append(
                f"No direct download link found on slow_download page {slow_href}"
            )
            # Dump the page content for debugging
            logger.debug(
                "No download candidates found on %s (md5=%s); dumping HTML:\n%s",
                slow_href,
                md5,
                resp.content.decode("utf-8", errors="replace")[:2000],
            )
            logger.debug(
                "No direct link candidates on slow_download page=%s md5=%s",
                slow_href,
                md5,
            )
            return None

        final_url = candidates[0].strip()
        if not final_url:
            return None

        # Normalize relative URLs
        if not urlparse(final_url).netloc:
            final_url = urljoin(self.base_url, final_url)

        if self._is_html_response(final_url):
            debug_log.append(
                f"Slow download candidate looked like HTML; skipping {final_url}"
            )
            logger.debug(
                "Skipping slow_download candidate because HEAD was HTML url=%s md5=%s",
                final_url,
                md5,
            )
            return None

        fmt = self._detect_format("", final_url, formats) or "bin"
        debug_log.append(
            f"Resolved AA slow_download {slow_href} -> {final_url} ({fmt})"
        )
        logger.debug(
            "Resolved AA slow_download %s -> %s fmt=%s", slow_href, final_url, fmt
        )
        return final_url, fmt

    def _resolve_aa_slow_download_browser(
        self,
        slow_href: str,
        formats: List[str],
        debug_log: List[str],
    ) -> Optional[Tuple[str, str]]:
        """
        Optional: use Playwright+stealth to get past Cloudflare human detection.
        Requires `pip install playwright playwright-stealth` + `playwright install`.
        """
        try:
            from stealth_browser import solve_cloudflare_challenge
        except Exception as exc:  # pragma: no cover - optional dependency path
            debug_log.append(
                "Stealth browser not available; cannot bypass Cloudflare for slow_download"
            )
            logger.warning(
                "Stealth browser unavailable for %s: %s", slow_href, exc
            )
            return None

        if sync_playwright is None:
            debug_log.append(
                "Playwright not installed; cannot bypass Cloudflare for slow_download"
            )
            logger.warning(
                "Playwright not available but Cloudflare challenge encountered at %s",
                slow_href,
            )
            return None

        if self.cloudflare_lock:
            with self.cloudflare_lock:
                content = solve_cloudflare_challenge(
                    slow_href, timeout=self.timeout * 2, wait_seconds=15
                )
        else:
            content = solve_cloudflare_challenge(
                slow_href, timeout=self.timeout * 2, wait_seconds=15
            )
        if not content:
            debug_log.append(
                f"Stealth browser timed out or was blocked at {slow_href}"
            )
            logger.warning("Stealth browser returned None for %s", slow_href)
            return None

        logger.debug("Stealth browser returned content type=%s len=%d", type(content).__name__, len(content) if content else 0)
        if isinstance(content, str) and content.strip().lower().startswith(("http://", "https://")):
            final_url = content.strip()
            logger.debug("Stealth browser returned direct URL: %s", final_url)

            if self._is_html_response(final_url):
                debug_log.append(f"For some reason, the stealth browser gave us what looks like HTML; skipping {final_url}")
                logger.debug("skipping 'URL' because HEAD looks like HTML for %s",final_url,)
                return None

            fmt = self._detect_format("", final_url, formats) or "bin"
            debug_log.append(
                f"Playwright returned direct URL for slow_download {slow_href} -> {final_url} ({fmt})"
            )
            logger.debug(
                "Playwright returned direct URL for slow_download %s -> %s fmt=%s",
                slow_href,
                final_url,
                fmt,
            )
            return final_url, fmt
            
        doc = html.fromstring(content)
        candidates: List[str] = []

        candidates.extend(
            doc.xpath(
                '//a[contains(@class, "btn") or '
                'contains(translate(text(),"DOWNLOAD","download"), "download")]'
                "/@href"
            )
        )

        if not candidates:
            for txt in doc.xpath("//p//text()"):
                s = txt.strip()
                if s.startswith("http://") or s.startswith("https://"):
                    candidates.append(s)
                    break

        if not candidates:
            debug_log.append(
                f"Playwright did not find any direct link on {slow_href}, even though they are probably there!"
            )
            # Dump the browser-rendered content for debugging
            logger.warning(
                "Playwright resolved %s but found no download links; dumping content:\n%s",
                slow_href,
                content[:2000] if isinstance(content, str) else str(content)[:2000],
            )
            return None

        final_url = candidates[0].strip()
        if not final_url:
            return None

        if not urlparse(final_url).netloc:
            final_url = urljoin(self.base_url, final_url)

        if self._is_html_response(final_url):
            debug_log.append(
                f"Playwright candidate looked like HTML; skipping {final_url}"
            )
            logger.debug(
                "Skipping Playwright slow_download candidate because HEAD was HTML url=%s",
                final_url,
            )
            return None

        fmt = self._detect_format("", final_url, formats) or "bin"
        debug_log.append(
            f"Stealth browser got the direct download link! {slow_href} -> {final_url} ({fmt})"
        )
        logger.debug(
            "Playwright resolved AA slow_download %s -> %s fmt=%s",
            slow_href,
            final_url,
            fmt,
        )
        return final_url, fmt

    # ------------------------------------------------------------------
    # LibGen / Sci-Hub / Z-Lib resolvers
    # ------------------------------------------------------------------
    def _resolve_libgen_li(self, href: str) -> str:
        resp = self._safe_get(href)
        if resp is None:
            return href
        doc = html.fromstring(resp.content)
        scheme, _, host, _ = resp.url.split("/", 3)
        url = "".join(doc.xpath('//a[h2[text()="GET"]]/@href'))
        return f"{scheme}//{host}/{url}" if url else href

    def _resolve_libgen_nonfiction(self, href: str, md5: Optional[str] = None) -> Optional[str]:
        # Handle libgen.li ads.php?md5= format (NO redirects to get the ads page with GET button)
        parsed = urlparse(href)
        if "ads.php" in href and "md5=" in href:
            # Extract MD5 from URL immediately - this is the most reliable method
            from urllib.parse import parse_qs
            query_params = parse_qs(parsed.query)
            md5_val = query_params.get("md5", [None])[0]
            if md5_val:
                # Use direct download URL from MD5 - libgen ads.php pages are unreliable
                download_url = f"https://libgen.li/get.php?md5={md5_val}"
                logger.debug("Using MD5 from ads.php URL to form direct libgen download: %s", download_url)
                return download_url
            
            # Fallback: try to parse ads.php page (may fail)
            resp = self._safe_get(href, allow_redirects=False)
            if resp is not None:
                try:
                    doc = html.fromstring(resp.content)
                    url = (
                        "".join(doc.xpath('//a[contains(@href, "/get.php")]/@href'))
                        or "".join(doc.xpath('//tr//a[contains(@href, "get.php")]/@href'))
                        or "".join(doc.xpath('//a[contains(., "GET")]/@href'))
                    )
                    if url:
                        if not url.startswith("http"):
                            url = "https://libgen.li" + url if url.startswith("/") else f"https://libgen.li/{url}"
                        logger.debug("Resolved libgen ads.php to download URL: %s", url)
                        return url
                except Exception as e:
                    logger.debug("Failed to parse ads.php page: %s", e)
        
        # Handle libgen.li/file.php?id= format (book info page) - skip it but use MD5 if available
        if "file.php" in href and "id=" in href:
            logger.debug("Skipping libgen.li/file.php?id= page (book info, not download): %s", href)
            # If we have MD5 from Anna's Archive, use it to form the download URL
            if md5:
                download_url = f"https://libgen.li/get.php?md5={md5}"
                logger.debug("Using Anna's Archive MD5 to form libgen download URL: %s", download_url)
                return download_url
            return None
        
        resp = self._safe_get(href)
        if resp is None:
            return href
        doc = html.fromstring(resp.content)
        url = "".join(doc.xpath('//h2/a[text()="GET"]/@href'))
        return url or href

    def _resolve_scihub(self, href: str) -> str:
        resp = self._safe_get(href)
        if resp is None:
            return href
        doc = html.fromstring(resp.content)
        scheme, _ = resp.url.split("/", 1)
        url = "".join(doc.xpath('//embed[@id="pdf"]/@src'))
        return (scheme + url) if url else href

    def _resolve_zlib(self, href: str) -> str:
        resp = self._safe_get(href)
        if resp is None:
            return href
        doc = html.fromstring(resp.content)
        scheme, _, host, _ = resp.url.split("/", 3)
        url = "".join(
            doc.xpath('//a[contains(@class, "addDownloadedBook")]/@href')
        )
        if url:
            return f"{scheme}//{host}/{url}"
        return href

    def _resolve_download_link(self, href: str, md5: Optional[str] = None) -> Optional[Tuple[str, str]]:
        """
        Given a link found on Anna's Archive detail page, try to turn it into
        a direct download URL and a format label ("epub", "pdf", etc.).

        We *skip*:
          - onion links
          - Anna's Archive torrents helper page
          - z-lib mirrors (Cloudflare / human check) when ENABLE_ZLIB is False
        """
        parsed = urlparse(href)
        netloc = (parsed.netloc or "").lower()
        path = parsed.path or ""

        # Skip Tor/onion links – we can't use them from here
        if parsed.hostname and parsed.hostname.endswith(".onion"):
            logger.debug("Skipping onion link href=%s", href)
            return None

        # Skip AA torrents helper page (not a direct file)
        if "annas-archive.org" in netloc and "/torrents" in path:
            logger.debug("Skipping torrents helper link href=%s", href)
            return None

        # Skip z-lib mirrors by default – Cloudflare/human check
        if ("z-lib." in netloc or "zlib." in netloc):
            if not ENABLE_ZLIB:
                logger.debug(
                    "Skipping z-lib mirror href=%s (Cloudflare / manual-only)", href
                )
                return None
            else:
                # Try to resolve z-lib link
                resolved_url = self._resolve_zlib(href)
                if resolved_url and resolved_url != href:
                    fmt = self._detect_format(href, resolved_url, ["epub", "mobi", "azw3", "pdf", "txt"]) or "unknown"
                    return resolved_url, fmt
                return None

        # Libgen / Library Genesis mirrors
        if "libgen.li" in netloc or "libgen.is" in netloc or "library.lol" in netloc:
            resolved_url = self._resolve_libgen_nonfiction(href, md5=md5)
            if resolved_url:
                # Detect format from the original href or resolved URL
                fmt = self._detect_format(href, resolved_url, ["epub", "mobi", "azw3", "pdf", "txt"]) or "unknown"
                return resolved_url, fmt
            logger.debug("Could not resolve libgen link to download URL: %s", href)
            return None

        logger.debug("Unrecognized download host for href=%s", href)
        return None

    # ------------------------------------------------------------------
    # Cover / description helpers
    # ------------------------------------------------------------------
    def _extract_cover(self, doc: html.HtmlElement) -> str:
        candidates = doc.xpath(
            '//meta[@property="og:image"]/@content'
            ' | //img[@id="cover-img"]/@src'
            ' | //div[contains(@class, "cover")]/img/@src'
            ' | //img[contains(@class, "book-cover")]/@src'
        )
        for cover in candidates:
            cover = cover.strip()
            if not cover:
                continue
            # Handle protocol-relative URLs (starting with //)
            # These may have malformed double slashes from Anna's Archive HTML
            if cover.startswith("//"):
                # Remove leading slashes and prepend https://
                # This converts //domain//path to https://domain/path
                clean_cover = cover.lstrip("/")
                return "https://" + clean_cover
            # Handle absolute paths (starting with /)
            if cover.startswith("/"):
                return urljoin(self.base_url, cover)
            return cover
        return ""

    def _extract_description(self, doc: html.HtmlElement) -> str:
        """Extract a human-readable description from the detail page."""
        xpaths = [
            'string(//meta[@name="description"]/@content)',
            'string(//meta[@property="og:description"]/@content)',
            'normalize-space(//div[contains(@class, "book-description")])',
            'normalize-space(//div[@id="book-description"])',
        ]
        for expr in xpaths:
            desc = doc.xpath(expr)
            if desc and str(desc).strip():
                return str(desc).strip()
        return ""

    # ------------------------------------------------------------------
    # Format detection
    # ------------------------------------------------------------------
    def _detect_format(
        self, text: str, href: Optional[str], formats: List[str]
    ) -> str:
        text_lower = (text or "").lower()

        # 1) If an allowed format appears in the button text, trust that
        for fmt in formats:
            if fmt and fmt.lower() in text_lower:
                return fmt.lower()

        # 2) If an allowed format matches the URL suffix, use that
        if href:
            lower_href = href.lower()
            for fmt in formats:
                if fmt and lower_href.endswith(fmt.lower()):
                    return fmt.lower()

            # 3) Fallback: probe common ebook extensions
            for candidate in ["mobi", "azw3", "azw", "epub", "pdf", "txt"]:
                if candidate in text_lower or lower_href.endswith(candidate):
                    return candidate

        # 4) Last resort: first declared format, if any
        if formats:
            return formats[0].lower()

        return ""

    # ------------------------------------------------------------------
    # Cache + direct download
    # ------------------------------------------------------------------
    def cached_result(self, result_id: str) -> Optional[Dict]:
        return self.cache.get(result_id)


    def resolve_downloads_for_result(self, result: Dict) -> Dict:
        """Ensure a search result has its download links resolved.

        Used by the Flask app for manual and auto-download flows when search
        ran in "cheap" mode (no detail page calls). Reuses the same detail-page
        logic as `download`, but without fetching the file.
        """
        if not result:
            return result

        downloads_map: Dict[str, Any] = result.get("downloads") or {}
        md5 = (result.get("detail") or "").strip()
        formats = list(result.get("formats") or [])
        debug_log: List[str] = []

        # If we already have downloads and at least one format, keep them.
        if downloads_map and formats:
            return result

        if md5:
            try:
                downloads_map, cover, description = self._get_downloads(
                    md5, formats, debug_log
                )
                if downloads_map:
                    result["downloads"] = downloads_map
                if cover and not result.get("cover"):
                    result["cover"] = cover
                if description and not result.get("description"):
                    result["description"] = description
            except Exception:
                logger.exception(
                    "resolve_downloads_for_result: failed for %s (md5=%s)",
                    result.get("title"),
                    md5,
                )

        return result
    def download(self, result: Dict, fmt: str, dest_dir: Path) -> Path:
        downloads_map: Dict[str, Any] = result.get("downloads") or {}
        md5 = (result.get("detail") or "").strip()
        
        if not downloads_map and md5:
            # Try to lazily resolve downloads using the detail (md5) page
            formats = list(result.get("formats") or [])
            debug_log: List[str] = []

            try:
                downloads_map, _, _ = self._get_downloads(md5, formats, debug_log)
                result["downloads"] = downloads_map
            except Exception:
                logger.exception(
                    "Lazy download resolution failed for %s (md5=%s)",
                    result.get("title"),
                    md5,
                )
                downloads_map = {}

        if not downloads_map:
            raise ValueError(
                f"No download links available for any format (requested={fmt or 'none'})"
            )
        
        requested_fmt = (fmt or "").lower()
        
        # Define Kindle-convertible formats
        CONVERTIBLE_FORMATS = {"epub", "mobi", "azw", "azw3", "pdf", "txt"}
        
        # Build list of formats to try in order
        candidate_formats: List[str] = []
        if requested_fmt and requested_fmt in downloads_map:
            candidate_formats.append(requested_fmt)
        
        # Then add other formats in preference order (only convertible formats)
        for f in (result.get("formats") or []):
            f_l = f.lower()
            if f_l in downloads_map and f_l not in candidate_formats and f_l in CONVERTIBLE_FORMATS:
                candidate_formats.append(f_l)
        
        if not candidate_formats:
            # No convertible formats available
            available_formats = list(downloads_map.keys())
            raise ValueError(f"No convertible formats available (requested={fmt or 'none'}, available={available_formats})")
        
        # Try each format's links in order
        for fmt_to_try in candidate_formats:
            raw_links = downloads_map.get(fmt_to_try)
            
            # Convert to list of links
            if isinstance(raw_links, str):
                link_list = [raw_links]
            elif isinstance(raw_links, (list, tuple)):
                link_list = list(raw_links)
            else:
                continue
            
            # Try each link
            for link_idx, url in enumerate(link_list):
                if not url:
                    continue
                
                if fmt_to_try != requested_fmt and requested_fmt:
                    logger.warning(
                        "Requested format %s unavailable; trying %s",
                        requested_fmt,
                        fmt_to_try,
                    )
                
                logger.debug("Attempting to download %s from %s (format=%s, link %d/%d)", 
                            result.get("title"), url, fmt_to_try, link_idx + 1, len(link_list))
                
                try:
                    final_path = self._download_from_url(
                        url, result, fmt_to_try, dest_dir
                    )
                    return final_path
                except ValueError as e:
                    error_msg = str(e).lower()
                    # If link is expired or rate-limited, try next link
                    if "expired" in error_msg or "rate" in error_msg or "403" in error_msg:
                        logger.warning(
                            "Download link failed for %s, trying next link (format=%s, link %d/%d)",
                            result.get("title"),
                            fmt_to_try,
                            link_idx + 1,
                            len(link_list),
                        )
                        continue
                    # For other errors, raise immediately
                    raise
                except Exception as e:
                    # Log and try next link on unexpected errors
                    logger.warning(
                        "Error downloading from %s: %s, trying next link",
                        url,
                        e,
                    )
                    continue
        
        # All formats and links failed - try fallback with waitlist links if available
        if md5:
            logger.info("All primary links failed, attempting fallback with waitlist mirrors for %s", result.get("title"))
            try:
                fallback_map = self._get_waitlist_mirrors(md5)
                if fallback_map:
                    result["downloads"] = fallback_map
                    return self.download(result, fmt, dest_dir)
            except Exception as e:
                logger.warning("Waitlist mirrors also failed for %s: %s", result.get("title"), e)
                # Fall through to LibGen fallback
        
        # All Anna's Archive mirrors failed - try LibGen as last resort
        try:
            logger.info("All Anna's Archive mirrors failed for '%s' - attempting LibGen fallback", result.get("title"))
            title = result.get("title", "").strip()
            author = result.get("author", "").strip()
            query = f"{title}" + (f" {author}" if author else "")
            
            return self._try_libgen_fallback(query, fmt, dest_dir, result.get("title"))
        except Exception as e:
            logger.error("LibGen fallback also failed for '%s': %s", result.get("title"), e)
            # Fall through to final error
        
        # All formats and links failed
        raise ValueError(
            f"No working download links available after trying all formats for {result.get('title')}"
        )
    
    def _download_from_url(self, url: str, result: Dict, fmt: str, dest_dir: Path) -> Path:
        """
        Attempt to download from a specific URL.
        Raises ValueError if link is expired or download fails.
        """
        # Handle libgen ads.php redirects before making the final request
        if "ads.php" in url and "md5=" in url:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            md5_val = query_params.get("md5", [None])[0]
            if md5_val:
                # Use direct download URL instead of ads page
                url = f"https://libgen.li/get.php?md5={md5_val}"
                logger.debug("Converted ads.php URL to direct download: %s", url)
        
        # 1. Acquire semaphore for concurrency control
        with _DOWNLOAD_SEMAPHORE:
            # 2. Make the request via the integrated handler with download headers and retries
            # It will resolve slow_download using the browser if necessary
            resp = self._make_request(url, stream=True, is_download=True)
        
        if resp is None or isinstance(resp, _FakeResponse):
            status_code = getattr(resp, "status_code", None) if resp is not None else "N/A"
            reason = getattr(resp, "reason", "") if resp is not None else "N/A"
            logger.error(
                "Download failed via _FakeResponse (Status %s, Reason: %s) for title=%s URL=%s",
                status_code,
                reason,
                result.get("title"),
                url,
            )
            # Include status code in error message for better detection
            error_msg = f"Failed to GET download URL or resolve stealth challenge. Status: {status_code}. URL: {url}"
            if status_code in (403, 429):
                error_msg = f"Rate limited (HTTP {status_code}). URL: {url}"
            raise ValueError(error_msg)
        
        # 3. Check for failed response object
        if not hasattr(resp, "iter_content"):
            logger.error(
                "Download failed (bad object %r) for title=%s URL=%s",
                type(resp),
                result.get("title"),
                url,
            )
            raise ValueError(f"Failed to GET download URL or resolve stealth challenge. URL: {url}")

        # 4. Content Type Check
        # NOTE: We used to block ALL HTML responses here, but that was too aggressive.
        # LibGen and other sources serve book detail pages as HTML, which contain
        # download links we need to extract. Instead, we check the content body later.
        content_type = (resp.headers.get("Content-Type") or "").lower()
        
        # Only block obvious non-ebook mime types (but not text/html, which might contain download links)
        if content_type and not any(x in content_type for x in ["html", "json", "xml", "text", "octet-stream", "application"]):
            logger.warning(
                "Download URL returned unexpected Content-Type %s for title=%s; continuing anyway",
                content_type,
                result.get("title"),
            )

        # 5. Determine save path
        # Build filename as {title}-{author}.{format}
        # Sanitize to remove invalid path characters (/, \, :, etc.)
        def sanitize_filename(s: str) -> str:
            """Remove/replace invalid path characters from filename."""
            invalid_chars = r'[<>:"/\\|?*]'
            s = re.sub(invalid_chars, '', s)  # Remove invalid chars
            s = s.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')  # Replace whitespace chars
            return s.strip()
        
        title = sanitize_filename(result.get('title') or 'untitled')
        author = (result.get("author") or "").strip()
        
        # Linux filename limit is 255 chars - truncate to ensure we stay well under
        # Reserve space for separator, author, and extension
        max_filename_len = 255
        ext_with_dot = f".{fmt}"
        reserved_for_ext = len(ext_with_dot)
        
        if author:
            author = sanitize_filename(author)
            # Format: {title}-{author}.ext
            separator = "-"
            reserved = len(separator) + len(author) + reserved_for_ext
            max_title_len = max(50, max_filename_len - reserved - 10)  # At least 50 chars for title
            
            if len(title) > max_title_len:
                title = title[:max_title_len].rstrip()
            
            filename = f"{title}{separator}{author}{ext_with_dot}"
        else:
            # Format: {title}.ext
            max_title_len = max(50, max_filename_len - reserved_for_ext - 10)
            if len(title) > max_title_len:
                title = title[:max_title_len].rstrip()
            
            filename = f"{title}{ext_with_dot}"
        
        # Final safety check
        if len(filename) > max_filename_len:
            filename = filename[:max_filename_len].rstrip('.-')
            if not filename.endswith(ext_with_dot):
                filename = filename[:max_filename_len - len(ext_with_dot)] + ext_with_dot
        
        final_path = dest_dir / filename

        first_chunk: Optional[bytes] = None
        try:
            logger.debug("Opening file for writing: %s", final_path)
            with final_path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    if first_chunk is None:
                        first_chunk = chunk
                        # Check for actual HTML, but exclude ebook file signatures
                        # EPUB: PK (zip), MOBI: BOOKMOBI, AZW: TPZ, PDF: %PDF
                        is_ebook_header = (
                            b"PK" in chunk[:2] or  # EPUB/ZIP
                            b"BOOKMOBI" in chunk or  # MOBI
                            b"TPZ" in chunk or  # AZW
                            chunk.startswith(b"%PDF") or  # PDF
                            b"<?xml" in chunk[:50]  # XML-based ebooks
                        )
                        if b"<html" in chunk.lower() and not is_ebook_header:
                            # Capture up to 50KB of HTML for debugging (instead of just 2000 bytes)
                            html_bytes = first_chunk[:50000]
                            html_snippet = html_bytes.decode('utf-8', errors='ignore')
                            
                            # Save error HTML to file for manual inspection
                            try:
                                error_html_dir = Path(__file__).parent / "data" / "error_html_pages"
                                error_html_dir.mkdir(parents=True, exist_ok=True)
                                timestamp = time.strftime("%Y%m%d_%H%M%S")
                                title_safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in result.get("title", "unknown")[:50])
                                error_html_file = error_html_dir / f"error_{timestamp}_{title_safe}.html"
                                error_html_file.write_text(html_snippet, encoding='utf-8')
                                logger.error("Saved error HTML page to %s", error_html_file)
                            except Exception as e:
                                logger.debug("Failed to save error HTML to file: %s", e)
                            
                            logger.error(
                                "Download URL returned HTML payload for title=%s; first 2000 bytes: %s; aborting",
                                result.get("title"),
                                html_snippet[:2000],
                            )
                            # Check if this is an expiration message
                            if "expired" in html_snippet.lower():
                                raise ValueError("Download link expired")
                            raise ValueError(
                                f"Download URL returned HTML payload instead of ebook. HTML snippet: {html_snippet}"
                            )
                    f.write(chunk)
        except Exception:
            try:
                if final_path.exists():
                    final_path.unlink()
            except Exception:
                logger.debug(
                    "Failed to remove partial file %s after download error",
                    final_path,
                    exc_info=True,
                )
            raise
        finally:
            try:
                resp.close()
            except Exception:
                pass
        
        # Verify file size isn't suspiciously small
        if final_path.exists():
            file_size = final_path.stat().st_size
            if file_size < 1000:  # Less than 1KB
                logger.warning(
                    "Downloaded file is suspiciously small (%d bytes) for title=%s from %s",
                    file_size,
                    result.get("title"),
                    url,
                )
                # Read and log the content for debugging
                try:
                    content = final_path.read_bytes()
                    content_str = content.decode('utf-8', errors='replace')
                    logger.warning(
                        "Small file content preview (%d bytes):\n%s",
                        file_size,
                        content_str[:500],
                    )
                except Exception as e:
                    logger.warning("Failed to read small file for logging: %s", e)

        logger.debug("Saved download to %s", final_path)
        return final_path
    
    def _try_libgen_fallback(self, query: str, fmt: str, dest_dir: Path, original_title: str) -> Path:
        """
        Fallback method to download from LibGen when Anna's Archive fails.
        This is called when all AA mirrors (including waitlist) fail with permanent errors.
        """
        if not LIBGEN_AVAILABLE:
            raise ValueError("LibGen fallback not available (libgen-api-enhanced not installed)")
        
        logger.info("Attempting LibGen fallback download for '%s' (format=%s)", original_title, fmt)
        
        try:
            # Filter to reachable mirrors before attempting search
            reachable_mirrors = get_reachable_mirrors(KNOWN_MIRRORS)
            mirror_status = report_mirror_status()
            logger.info(mirror_status)
            
            if not reachable_mirrors:
                logger.error("No reachable LibGen mirrors found")
                raise ValueError("No reachable LibGen mirrors available")
            
            # Extract just the domain from the mirror URLs for LibgenSearch
            libgen_mirror = reachable_mirrors[0]
            from urllib.parse import urlparse
            mirror_domain = urlparse(libgen_mirror).netloc or "libgen.li"
            
            logger.debug("Using LibGen mirror: %s", mirror_domain)
            
            # Search LibGen using the first reachable mirror
            ls = LibgenSearch(mirror=mirror_domain)
            results_libgen = ls.search_title(query)
            
            if not results_libgen:
                raise ValueError(f"LibGen returned no results for '{original_title}'")
            
            logger.debug("LibGen returned %d results from %s, attempting download", len(results_libgen), mirror_domain)
            
            # Try to download from the first result
            for idx, libgen_item in enumerate(results_libgen[:3]):  # Try top 3 results
                try:
                    # Extract download link from LibGen result
                    download_url = libgen_item.get("URL") or libgen_item.get("download_url")
                    if not download_url:
                        logger.warning("LibGen result #%d has no download URL, skipping", idx + 1)
                        continue
                    
                    logger.debug("Attempting LibGen download from URL #%d: %s", idx + 1, download_url[:80])
                    
                    # Create a minimal result dict for _download_from_url
                    libgen_result = {
                        "title": libgen_item.get("Title", original_title),
                        "author": libgen_item.get("Author", ""),
                        "detail": libgen_item.get("MD5", ""),
                        "source": "libgen_fallback",
                    }
                    
                    # Attempt download via _download_from_url
                    final_path = self._download_from_url(download_url, libgen_result, fmt, dest_dir)
                    logger.info("LibGen fallback download succeeded for '%s' using mirror %s", original_title, mirror_domain)
                    return final_path
                    
                except (ValueError, Exception) as e:
                    logger.warning("LibGen result #%d failed: %s, trying next", idx + 1, e)
                    continue
            
            raise ValueError(f"All LibGen mirrors failed for '{original_title}'")
            
        except Exception as e:
            logger.error("LibGen fallback completely failed for '%s': %s", original_title, e)
            raise ValueError(f"LibGen fallback failed: {e}")


# ======================================================================
# ArchiveOrgSource - Internet Archive / Archive.org book source
# ======================================================================

class ArchiveOrgSource:
    """
    Search + download wrapper around Archive.org (Internet Archive).
    
    Uses RSS search API and metadata API to find and download books.
    No API key required.
    """
    
    def __init__(
        self,
        timeout: int = 30,
        max_results: int = 10,
        **_: object,
    ) -> None:
        """
        Arguments:
        - timeout: request timeout in seconds
        - max_results: default max results to return from search
        """
        self.timeout = timeout
        self.max_results = max_results
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.cache: Dict[str, Dict] = {}
    
    def search(
        self,
        query: str,
        options: Optional["SearchOptions"] = None,
    ) -> Tuple[List[Dict], List[str]]:
        """
        Search Archive.org for books via RSS feed.
        
        Returns:
            (results, debug_log)
            results: list of dicts with keys: id, title, author, cover, formats, downloads, detail_url
        """
        opts = options or SearchOptions(query=query)
        if not opts.query:
            opts.query = query
        
        debug_log = []
        cache_key = (opts.query or query).strip().lower()
        
        # Check cache
        if cache_key in self.cache:
            debug_log.append(f"Archive.org cache hit for: {opts.query}")
            return list(self.cache[cache_key]), debug_log
        
        try:
            # Build search query with mediatype:texts filter
            search_query = f'{opts.query} AND mediatype:texts'
            
            # RSS search endpoint
            search_url = 'https://archive.org/advancedsearch.php'
            params = {
                'q': search_query,
                'output': 'rss',
            }
            
            debug_log.append(f"Searching Archive.org for: {opts.query}")
            response = self.session.get(search_url, params=params, timeout=self.timeout)
            response.raise_for_status()
            
            # Parse RSS
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.content)
            
            results = []
            items = root.findall('.//item')
            debug_log.append(f"Found {len(items)} Archive.org results")
            
            for item in items[:self.max_results]:
                try:
                    guid_elem = item.find('guid')
                    title_elem = item.find('title')
                    
                    if guid_elem is None or title_elem is None:
                        continue
                    
                    # Extract identifier from /details/... URL
                    guid_url = guid_elem.text or ""
                    identifier = guid_url.split('/details/')[-1] if '/details/' in guid_url else guid_url
                    title = title_elem.text or "Unknown"
                    
                    # Get metadata to find downloadable files
                    files_info = self._get_downloadable_files(identifier)
                    
                    if not files_info:
                        continue
                    
                    result = {
                        'id': identifier,
                        'title': title,
                        'author': '',
                        'cover': '',
                        'formats': files_info['formats'],
                        'downloads': 0,
                        'detail': identifier,
                        'detail_url': f'https://archive.org/details/{identifier}',
                        'source': 'archive.org',
                    }
                    results.append(result)
                    
                except Exception as e:
                    debug_log.append(f"Failed to parse Archive.org result: {e}")
                    continue
            
            # Cache results
            self.cache[cache_key] = results
            debug_log.append(f"Returning {len(results)} Archive.org results")
            return results, debug_log
            
        except Exception as e:
            error_msg = f"Archive.org search failed: {e}"
            logger.error(error_msg)
            debug_log.append(error_msg)
            return [], debug_log
    
    def _get_downloadable_files(self, identifier: str) -> Optional[Dict]:
        """
        Get metadata for an identifier and extract downloadable files.
        
        Returns:
            {
                'formats': ['PDF', 'EPUB', ...],
                'files': [
                    {'name': filename, 'format': format, 'size': bytes}
                ]
            }
            or None if no downloadable files found
        """
        try:
            meta_url = f'https://archive.org/metadata/{identifier}'
            response = self.session.get(meta_url, timeout=self.timeout)
            response.raise_for_status()
            
            metadata = response.json()
            files = metadata.get('files', [])
            
            # Find downloadable files (PDF, EPUB, MOBI, etc.)
            downloadable = []
            formats = set()
            
            for f in files:
                fmt = f.get('format', '')
                fname = f.get('name', '')
                
                # Prioritize text-based formats
                if any(x in fmt for x in ['PDF', 'EPUB', 'MOBI', 'Text']):
                    # Skip encrypted/DRM versions
                    if any(x in fmt for x in ['Encrypted', 'LCP']):
                        continue
                    
                    downloadable.append({
                        'name': fname,
                        'format': fmt,
                        'size': f.get('size', '0'),
                    })
                    formats.add(fmt)
            
            if downloadable:
                return {
                    'formats': sorted(list(formats)),
                    'files': downloadable,
                }
            
            return None
            
        except Exception as e:
            logger.debug(f"Failed to get Archive.org metadata for {identifier}: {e}")
            return None
    
    def download(self, result: Dict, fmt: str, dest_dir: Path) -> Path:
        """
        Download a book from Archive.org.
        
        Args:
            result: Search result dict with 'detail' (identifier) key
            fmt: Format to download (PDF, EPUB, etc.)
            dest_dir: Destination directory
            
        Returns:
            Path to downloaded file
        """
        identifier = result.get('detail', '')
        if not identifier:
            raise ValueError("Result missing 'detail' (identifier)")
        
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Get metadata
            files_info = self._get_downloadable_files(identifier)
            if not files_info:
                raise ValueError(f"No downloadable files found for {identifier}")
            
            # Find best file matching format preference
            best_file = None
            
            # Prefer: Additional Text PDF > EPUB > Text PDF > regular PDF
            format_priority = [
                'Additional Text PDF',
                'EPUB',
                'PDF',
            ]
            
            for priority_fmt in format_priority:
                for f in files_info['files']:
                    if priority_fmt in f['format']:
                        best_file = f
                        break
                if best_file:
                    break
            
            if not best_file:
                best_file = files_info['files'][0]
            
            filename = best_file['name']
            
            # Build download URL
            download_url = f'https://archive.org/download/{identifier}/{filename}'
            
            logger.debug(f"Downloading from Archive.org: {download_url}")
            
            # Download with streaming
            response = self.session.get(download_url, timeout=self.timeout, stream=True)
            response.raise_for_status()
            
            # Save file
            safe_filename = filename.replace('/', '_')
            file_path = dest_dir / safe_filename
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            logger.info(f"Downloaded from Archive.org: {file_path}")
            return file_path
            
        except Exception as e:
            error_msg = f"Failed to download from Archive.org: {e}"
            logger.error(error_msg)
            raise ValueError(error_msg)


# ----------------------------------------------------------------------
# Helper for feeds / auto flows: pick best result given allowed formats
# ----------------------------------------------------------------------
def select_best_result(
    results: List[Dict],
    allowed_formats: List[str],
    kindle_type: str,
) -> Optional[Dict]:
    """
    Given a list of AA results and allowed formats (e.g. feed.filetypes),
    choose the "best" candidate:

      - Prefer results that have at least one allowed format.
      - Tie-break using the same _format_preference_score heuristic.
      - Fall back to overall _rank_score if present.

    This is used by RSS/HTML feed processing so that we always pick a sane
    candidate before calling source.download().
    """
    if not results:
        return None

    allowed = {(_normalize_fmt(f)) for f in (allowed_formats or []) if f}

    # We construct a temporary SearchOptions to reuse _format_preference_score.
    opts = SearchOptions(
        preferred_formats=list(allowed),
        kindle_type=kindle_type or "",
    )

    best_result: Optional[Dict] = None
    best_score: float = -1.0

    for r in results:
        formats = [(_normalize_fmt(f)) for f in (r.get("formats") or []) if f]
        has_allowed = bool(allowed & set(formats)) if allowed else bool(formats)

        if not has_allowed:
            # Still consider it, but with a big penalty.
            base = r.get("_rank_score", 0.0) - 0.5
        else:
            base = r.get("_rank_score", 0.0)

        fmt_score = _format_preference_score(r.get("formats", []), opts)
        total = base + fmt_score

        if total > best_score:
            best_score = total
            best_result = r

    if best_result and allowed:
        # Choose a concrete selected_format for download convenience
        fmts = [(_normalize_fmt(f)) for f in (best_result.get("formats") or []) if f]
        chosen = None
        for f in fmts:
            if f in allowed:
                chosen = f
                break
        if not chosen and fmts:
            chosen = fmts[0]
        if chosen:
            best_result["selected_format"] = chosen

    return best_result

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import urljoin, urlparse, urlencode

import re
import time
import threading

import requests
from lxml import html
from stealth_browser import resolve_slow_download_link
logger = logging.getLogger(__name__)

ENABLE_ZLIB = True  # we still skip most zlib links by default

SAFE_FILENAME_CHARS = (
    "-_.() abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)

# Global semaphore for concurrent downloads
_DOWNLOAD_SEMAPHORE = threading.Semaphore(2)


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
    max_rows: int = 15
    # Optional override for result limit (default is AnnaSource.max_results)
    max_results: Optional[int] = None


def _normalize_string(s: str) -> str:
    """
    Normalize text for fuzzy matching:
      - lowercase
      - strip punctuation
      - collapse whitespace
    """
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_FORMAT_PRIORITY_BASE: Dict[str, float] = {
    "azw3": 1.0,
    "azw": 0.95,
    "mobi": 0.9,
    "epub": 0.8,
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

ENABLE_ZLIB = True  # we still skip most zlib links by default

SAFE_FILENAME_CHARS = (
    "-_.() abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)

# Global semaphore for concurrent downloads
_DOWNLOAD_SEMAPHORE = threading.Semaphore(2)



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

        Any extra kwargs are accepted via **_ and ignored, so older/newer
        app.py versions won't crash with TypeError.
        """
        global ENABLE_ZLIB

        self.base_url = base_url.rstrip("/")
        self.cache: Dict[str, Dict] = {}
        self.timeout = timeout
        self.max_results = max_results
        self.detail_cache: Dict[str, Dict] = {}
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
    def _make_request(self, url: str, stream: bool = False, headers: Optional[Dict] = None) -> Optional[requests.Response]:
        """
        Manages the request, including the stealth/browser resolution for slow_download links.

        Returns:
            - requests.Response if successful (can be streamed)
            - None if request failed outright
        """
        # Copy the base headers from the underlying requests session. ``self.headers``
        # is not defined on AnnaSource, so we use ``self.session.headers`` as the
        # canonical store for our default headers. Avoiding ``self.headers`` here
        # prevents attribute errors.
        _headers: Dict[str, str] = {}
        try:
            _headers = dict(self.session.headers)
        except Exception:
            _headers = {}
        if headers:
            _headers.update(headers)
            
        try:
            # 1. Check for slow_download link (Anna's Archive protection)
            if "/slow_download/" in url:
                # Use the stealth browser to resolve the challenge and get the *final* download URL
                final_url = resolve_slow_download_link(url, self.timeout)

                if final_url is None:
                    logger.warning("Stealth browser failed to resolve challenge for %s", url)
                    return None
                
                # The browser succeeded and gave us the final direct URL.
                # Now, perform a standard requests GET to get the actual file stream.
                logger.debug("Stealth browser succeeded for %s, now fetching actual file from: %s", url, final_url)
                resp = self.session.get(
                    final_url,
                    headers=_headers,
                    timeout=self.timeout,
                    stream=stream
                )
                resp.raise_for_status() # Raise for HTTP errors on the final download
                logger.debug("Successfully fetched actual file stream (HTTP %d)", resp.status_code)
                return resp
                
            # 2. Standard direct request (used for covers, search, etc.)
            resp = self.session.get(
                url,
                headers=_headers,
                timeout=self.timeout,
                stream=stream,
            )
            resp.raise_for_status()
            return resp

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            logger.warning("HTTP error (%s) on URL %s", status, url)
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error("Connection error fetching %s: %s", url, e)
            return None
        except Exception:
            logger.exception("Unexpected error during request to %s", url)
            return None

    # ------------------------------------------------------------------
    # Internal network helper
    # ------------------------------------------------------------------
    def _safe_get(self, href: str, for_download: bool = False) -> Optional[requests.Response]:

        # ------------------------------
        # Raw GET attempt
        # ------------------------------
        try:
            resp = self.session.get(
                href,
                timeout=self.timeout,
                stream=for_download,
            )
        except requests.RequestException:
            # Network-level failure: mark host unreachable for the rest of the run
            logger.debug("Network error fetching href=%s", href, exc_info=True)
            return None

        # For pure file downloads we do NOT try to run a browser;
        # the slow_download resolver already handles CF for that path.
        if for_download:
            try:
                resp.raise_for_status()
            except requests.RequestException:
                logger.debug(
                    "HTTP error status=%s for download href=%s", resp.status_code, href
                )
                resp.close()
                return None
            return resp

        # ------------------------------
        # Metadata / HTML path: Cloudflare detection
        # ------------------------------
        # Use our local Cloudflare heuristic
        if self._is_cloudflare_challenge(resp):
            # Some AA search pages still render useful HTML even while tripping our
            # Cloudflare heuristic. Before invoking Playwright, inspect the parsed
            # tree to see if we already have usable rows.
            try:
                parsed_tree = html.fromstring(resp.content)
                if parsed_tree.xpath("//table//tr[td]"):
                    logger.debug(
                        "Cloudflare heuristic triggered for %s but table rows present; "
                        "using raw response",
                        href,
                    )
                    return resp
            except Exception:
                # Fall back to the solver
                logger.debug("Failed to inspect HTML before Cloudflare bypass", exc_info=True)

            logger.warning(
                "Cloudflare / anti-bot challenge detected at %s (status=%s); "
                "attempting stealth browser bypass",
                href,
                resp.status_code,
            )
            resp.close()

            # Only invoke the challenge solver when we actually detect Cloudflare
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

            try:
                solved = solve_cloudflare_challenge(
                    href, timeout=self.timeout * 2, wait_seconds=60
                )
            except Exception:
                logger.debug("stealing failed check logs", exc_info=True)
                solved = None

            if not solved:
                logger.warning(
                    "Stealth browser failed to bypass Cloudflare for %s", href
                )
                return None

            # If the solver returned a URL, perform a real HTTP GET so downstream
            # callers always receive a genuine requests.Response object.
            if isinstance(solved, str) and solved.startswith("http"):
                try:
                    resp = self.session.get(
                        solved,
                        timeout=self.timeout,
                        stream=for_download,
                    )
                    resp.raise_for_status()
                    return resp
                except requests.RequestException:
                    logger.debug(
                        "HTTP error after browser bypass for %s", solved, exc_info=True
                    )
                    return None

            # Otherwise treat the returned HTML as page content (matches the
            # earlier FakeResponse behavior without the shim class).
            logger.debug(
                "Stealth browser succeeded for %s, using rendered HTML content", href
            )
            rendered = requests.Response()
            rendered.status_code = 200
            rendered._content = str(solved).encode("utf-8", errors="ignore")  # type: ignore[attr-defined]
            rendered.url = href
            rendered.headers["Content-Type"] = "text/html; charset=utf-8"
            rendered.encoding = "utf-8"
            return rendered

        # ------------------------------
        # Non-Cloudflare HTTP status handling
        # ------------------------------
        if resp.status_code == 429:
            logger.warning(
                "Received 429 Too Many Requests for href=%s; backing off but not "
                "marking host unreachable",
                href,
            )
            resp.close()
            return None

        try:
            resp.raise_for_status()
        except requests.RequestException:
            logger.debug(
                "HTTP error status=%s for href=%s; marking host unreachable",
                resp.status_code,
                href,
                exc_info=True,
            )
            resp.close()
            return None

        return resp

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

        # Cache lookup: avoid repeated fetches for the same logical query. Keep the
        # exact query text (including spacing/punctuation) so lookups don't mutate
        # titles that users or feeds provided.
        cache_key = opts.query or query
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
                author = "".join(cols[2].xpath(".//text()")).strip()
                cover = "".join(cols[0].xpath(".//img/@src")).strip()
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

            if cover and cover.startswith("/"):
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
                debug_log.append(f"Skipping row without md5 link for title='{title}'")
                continue
            # --- end md5 extraction ---

            # At this stage we DO NOT resolve downloads yet.
            entry: Dict = {
                "title": title,
                "author": author,
                "cover": cover,
                "detail": md5,
                "formats": formats,
                "downloads": {},     # will be filled later (or lazily in download())
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

            logger.debug(
                "Row %d parsed (pre-ranking): title=%r author=%r md5=%s formats=%s",
                row_idx,
                title,
                author,
                md5,
                entry["formats"],
            )

        debug_log.append(f"Found {len(results)} results before ranking")
        logger.debug("Search parsed %d rows (before ranking)", len(results))

        if not results:
            return [], debug_log

        # ------------------------------------------------------------------
        # Ranking stage (TEXT + FORMAT preferences)
        # ------------------------------------------------------------------
        import difflib

        normalized_query = _normalize_string(opts.query or query)
        query_tokens = set(normalized_query.split())

        ranked_results: List[Dict] = []

        for result in results:
            title = result.get("title", "") or ""
            author = result.get("author", "") or ""

            full_text = f"{title} {author}".strip()
            normalized_text = _normalize_string(full_text)
            text_tokens = set(normalized_text.split()) if normalized_text else set()

            # 1) difflib similarity between normalized query and normalized "title + author"
            match_score = difflib.SequenceMatcher(
                None, normalized_query, normalized_text
            ).ratio()

            # 2) Token overlap / "exact-ish" title matching
            token_overlap = 0.0
            if query_tokens and text_tokens:
                token_overlap = len(query_tokens & text_tokens) / float(
                    max(len(query_tokens), len(text_tokens))
                )

            title_norm = _normalize_string(title)
            exact_title_match = bool(title_norm) and title_norm == normalized_query
            starts_with_title = bool(title_norm) and normalized_query.startswith(
                title_norm
            )

            token_score = token_overlap
            if exact_title_match:
                token_score += 0.5
            elif starts_with_title:
                token_score += 0.25

            # 3) Format preference score (Kindle-aware)
            format_score = _format_preference_score(
                result.get("formats", []),
                opts,
            )

            # Combine into a single rank score.
            # Text similarity dominates; token + format are tie-breakers.
            rank_score = (
                match_score * 0.6
                + token_score * 0.25
                + format_score * 0.15
            )

            result["_match_score"] = match_score
            result["_token_score"] = token_score
            result["_format_score"] = format_score
            result["_rank_score"] = rank_score
            ranked_results.append(result)

            debug_log.append(
                "  - Score rank={:.3f} text={:.3f} tokens={:.3f} fmt={:.3f} | Title: {}".format(
                    rank_score,
                    match_score,
                    token_score,
                    format_score,
                    result.get("title", "N/A"),
                )
            )

        ranked_results.sort(key=lambda x: x.get("_rank_score", 0.0), reverse=True)

        # Apply max_results limit AFTER ranking (allow per-call override)
        limit = getattr(opts, "max_results", None) or self.max_results
        final_results = ranked_results[:limit]

        # ------------------------------------------------------------------
        # OPTIONAL: resolve downloads ONLY for the final, ranked top-N.
        # This keeps "matching before downloading" while still giving you
        # ready-to-go results when resolve_downloads=True.
        # ------------------------------------------------------------------
        if opts.resolve_downloads:
            for result in final_results:
                md5 = (result.get("detail") or "").strip()
                if not md5:
                    continue

                try:
                    downloads, detail_cover, description = self._get_downloads(
                        md5,
                        list(result.get("formats") or []),
                        debug_log,
                    )
                except Exception:
                    logger.exception(
                        "Error resolving downloads for md5=%s title=%r",
                        md5,
                        result.get("title"),
                    )
                    continue

                if downloads:
                    result["downloads"] = downloads
                if detail_cover:
                    result["cover"] = detail_cover
                if description:
                    result["description"] = description

                # Normalize formats list (union of declared + detected)
                detected_formats = set(result.get("formats") or []) | set(
                    result.get("downloads", {}).keys()
                )
                if detected_formats:
                    result["formats"] = sorted(detected_formats)

                # Refresh per-result cache entry with enriched data
                rid = result.get("id")
                if rid:
                    self.cache[rid] = result

        # Cache full ranked list for this query
        if final_results:
            self.cache[cache_key] = {"results": final_results}

        debug_log.append(f"Returning {len(final_results)} ranked results")
        logger.debug(
            "Returning %d ranked results (max_results=%d)",
            len(final_results),
            self.max_results,
        )
        return final_results, debug_log

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

        # Walk through slow_download URLs until we get at least one real file URL
        for raw_href in ordered_hrefs:
            slow_href = urljoin(self.base_url, raw_href)
            logger.debug("Resolving AA slow_download link=%s md5=%s", slow_href, md5)
            debug_log.append(f"Resolving AA slow_download link={slow_href} md5={md5}")
            try:
                resolved = self._resolve_aa_slow_download(
                    slow_href, md5, formats, debug_log
                )
            except Exception:
                logger.debug(
                    "AA slow_download resolution failed for %s",
                    slow_href,
                    exc_info=True,
                )
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

        debug_log.append(
            f"Resolved {len(downloads)} AA slow_download links for md5={md5} "
            f"(cover={bool(cover_url)} description_len={len(description)})"
        )
        logger.debug(
            "Resolved %d AA slow_download links for md5=%s (cover=%s description_len=%d)",
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

    # ------------------------------------------------------------------
    # Resolver helpers (Cloudflare / HTML / mirrors)
    # ------------------------------------------------------------------
    def _is_cloudflare_challenge(self, resp: requests.Response) -> bool:
        """Heuristically detect Cloudflare/anti-bot interstitials."""
        if resp is None:
            return False

        indicators = [
            "cloudflare",
            "just a moment",
            "attention required",
            "checking your browser",
            "verify you are human",
            "ddos-guard",
        ]

        try:
            lower_text = (resp.text or "").lower()
        except Exception:
            lower_text = ""

        return (
            resp.status_code in {403, 503}
            or any(indicator in lower_text for indicator in indicators)
        )

    def _is_html_response(self, url: str) -> bool:
        """Best-effort HEAD check to avoid returning HTML interstitials as downloads."""
        try:
            resp = self.session.head(
                url, allow_redirects=True, timeout=self.timeout, stream=False
            )
        except Exception:
            return False

        try:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            return "text/html" in content_type
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
        """
        debug_log.append(f"Resolving AA slow_download link: {slow_href}")
        logger.debug("Resolving AA slow_download link=%s md5=%s", slow_href, md5)

        resp = self._safe_get(slow_href)
        if resp is None:
            return None

        content_type = (resp.headers.get("Content-Type") or "").lower()

        # If it's already a non-HTML response, treat slow_href as direct URL.
        # Try to infer format from Content-Disposition or URL.
        if "text/html" not in content_type:
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
            debug_log.append(
                "Cloudflare / human-check detected on slow_download page; "
                "attempting browser automation if available"
            )
            logger.warning(
                "Cloudflare challenge detected at slow_href=%s (md5=%s)",
                slow_href,
                md5,
            )
            browser_result = self._resolve_aa_slow_download_browser(
                slow_href, formats, debug_log
            )
            if browser_result:
                return browser_result
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

        content = solve_cloudflare_challenge(
            slow_href, timeout=self.timeout * 2, wait_seconds=60
        )
        if not content:
            debug_log.append(
                f"Stealth browser timed out or was blocked at {slow_href}"
            )
            return None

        if isinstance(content, str) and content.strip().lower().startswith(("http://", "https://")):
            final_url = content.strip()

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

    def _resolve_libgen_nonfiction(self, href: str) -> str:
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

    def _resolve_download_link(self, href: str) -> Optional[Tuple[str, str]]:
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
        if ("z-lib." in netloc or "zlib." in netloc) and not ENABLE_ZLIB:
            logger.debug(
                "Skipping z-lib mirror href=%s (Cloudflare / manual-only)", href
            )
            return None

        # Libgen / Library Genesis mirrors
        if "libgen.li" in netloc or "libgen.is" in netloc or "library.lol" in netloc:
            resolved_url = self._resolve_libgen_nonfiction(href)
            if resolved_url:
                return resolved_url, ""
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
        if not downloads_map:
            # Try to lazily resolve downloads using the detail (md5) page
            md5 = (result.get("detail") or "").strip()
            formats = list(result.get("formats") or [])
            debug_log: List[str] = []

            if md5:
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
        selected_fmt: Optional[str] = None
        url: Optional[str] = None
        if requested_fmt and requested_fmt in downloads_map:
            selected_fmt = requested_fmt
            raw_links = downloads_map[selected_fmt]
            if isinstance(raw_links, str):
                url = raw_links
            elif isinstance(raw_links, (list, tuple)) and raw_links:
                url = raw_links[0]

        if not url:
            # Try to respect the result["formats"] ordering if present
            candidate_order: List[str] = []
            for f in (result.get("formats") or []):
                f_l = f.lower()
                if f_l in downloads_map and f_l not in candidate_order:
                    candidate_order.append(f_l)

            # If that produced nothing, just use any key order
            if not candidate_order:
                candidate_order = list(downloads_map.keys())

            for alt_fmt in candidate_order:
                alt_links = downloads_map.get(alt_fmt)
                if isinstance(alt_links, str) and alt_links:
                    if requested_fmt and alt_fmt != requested_fmt:
                        logger.warning(
                            "Requested format %s unavailable for %s; "
                            "falling back to %s",
                            requested_fmt,
                            result.get("title"),
                            alt_fmt,
                        )
                    selected_fmt = alt_fmt
                    url = alt_links
                    break
                elif isinstance(alt_links, (list, tuple)) and alt_links:
                    if requested_fmt and alt_fmt != requested_fmt:
                        logger.warning(
                            "Requested format %s unavailable for %s; "
                            "falling back to %s",
                            requested_fmt,
                            result.get("title"),
                            alt_fmt,
                        )
                    selected_fmt = alt_fmt
                    url = alt_links[0]
                    break

        if not url or not selected_fmt:
            raise ValueError(f"No DL link available, for any format! Last checked for {fmt}" )
        fmt = selected_fmt
        logger.info("Downloading %s (%s) from %s", result.get("title"), fmt, url)

        # 1. Acquire semaphore for concurrency control
        with _DOWNLOAD_SEMAPHORE:
            # 2. Make the request via the integrated handler
            # It will resolve slow_download using the browser if necessary
            resp = self._make_request(url, stream=True)
        if resp is None:
            logger.error(
                "Download failed: no response object for title=%s", result.get("title")
            )
            raise ValueError("Failed to GET download URL or resolve stealth challenge")
        
        # 3. Check for failed response object
        if not hasattr(resp, "iter_content"):
            logger.error(
                "Download failed (bad object %r) for title=%s",
                type(resp),
                result.get("title"),
            )
            raise ValueError("Failed to GET download URL or resolve stealth challenge")


        # 4. Content Type Check (only useful if it's a direct link or stealth failed to find the file)
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type:
            logger.error(
                "Download URL returned HTML (Content-Type=%s) for title=%s; likely a homepage / error page",
                content_type,
                result.get("title"),
            )
            resp.close()
            raise ValueError("Download URL returned HTML instead of a file")

        # 5. Determine save path
        filename = f"{result['title']}.{fmt}"
        # Strip characters illegal on Windows / Unix filenames
        safe_name = sanitize_filename_preserve_ext(filename)
        final_path = dest_dir / safe_name

        first_chunk: Optional[bytes] = None
        try:
            with final_path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    if first_chunk is None:
                        first_chunk = chunk
                        if b"<html" in chunk.lower():
                            logger.error(
                                "Download URL returned HTML payload for title=%s; aborting",
                                result.get("title"),
                            )
                            raise ValueError(
                                "Download URL returned HTML payload instead of ebook"
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

        logger.info("Saved download to %s", final_path)
        return final_path
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

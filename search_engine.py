import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote_plus, urljoin, urlparse, urlencode

import re
import requests
from lxml import html

logger = logging.getLogger(__name__)
ENABLE_ZLIB = False

# Optional: playwright for Cloudflare / human-check bypass
try:
    from playwright.sync_api import sync_playwright  # type: ignore
except Exception:  # ImportError or anything else
    sync_playwright = None  # type: ignore


@dataclass
class SearchOptions:
    query: str = ""
    language: str = "en"
    extensions: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    autodownload: bool = False


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
    ):
        self.base_url = base_url.rstrip("/")
        self.cache: Dict[str, Dict] = {}
        self.timeout = timeout
        self.max_results = max_results
        self.detail_cache: Dict[str, Dict] = {}
        self.session = requests.Session()
        # ensure _safe_get has somewhere to store failed hosts
        self.unreachable_hosts: set[str] = set()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def search(self, query: str, options: Optional[SearchOptions] = None) -> Tuple[List[Dict], List[str]]:
        opts = options or SearchOptions(query=query)
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
        # Cache lookup: avoid repeated fetches for the same query
        cache_key = query.strip().lower()
        if cache_key in self.cache:
            logger.debug("Cache hit for query=%r", query)
            return [self.cache[cache_key]], [f"Cache hit for query: {query}"]

        url = f"{self.base_url}/search?{urlencode(params, doseq=True)}"
        debug_log: List[str] = [f"Searching: {url}"]
        logger.debug("Issuing search request for query='%s'", query)

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

        # Only keep rows that look like real result rows (>= 10 columns)
        rows: List[html.HtmlElement] = []
        for r in all_rows:
            cols = r.findall("td")
            if len(cols) > 10:
                rows.append(r)
        logger.debug("Filtered to %d rows with >= 10 <td> cells", len(rows))

        results: List[Dict] = []
        if results:
            self.cache[cache_key] = results[0]
        for row_idx, row in enumerate(rows):
            cols = row.findall("td")
            if len(cols) <= 10:
                logger.debug(
                    "Row %d skipped: only %d <td> cells after filter",
                    row_idx,
                    len(cols),
                )
                continue

            if len(results) >= self.max_results:
                debug_log.append(
                    f"Reached max results ({self.max_results}); "
                    "skipping remaining rows"
                )
                break

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

            if cover and cover.startswith("/"):
                cover = urljoin(self.base_url, cover)

            # Formats: usually in column 9, but fall back to last column just in case
            raw_formats_text = "".join(
                (cols[9].xpath(".//text()") if len(cols) > 9 else cols[-1].xpath(".//text()"))
            )
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

            downloads, detail_cover, description = self._get_downloads(
                md5, formats, debug_log
            )

            if detail_cover:
                cover = detail_cover

            entry: Dict = {
                "title": title,
                "author": author,
                "cover": cover,
                "detail": md5,
                "formats": formats,
                "downloads": downloads,
                "description": description,
            }
            entry["id"] = hashlib.sha256(entry["detail"].encode("utf-8")).hexdigest()
            result_id = entry["id"]

            # Stable ID derived from md5
            entry["id"] = hashlib.sha256(entry["detail"].encode("utf-8")).hexdigest()
            result_id = entry["id"]

            # Normalize formats list (union of declared + detected from downloads)
            detected_formats = set(entry["formats"]) | set(entry["downloads"].keys())
            if detected_formats:
                entry["formats"] = sorted(detected_formats)

            results.append(entry)
            self.cache[result_id] = entry

            logger.debug(
                "Row %d parsed: title=%r author=%r md5=%s formats=%s",
                row_idx,
                title,
                author,
                md5,
                entry["formats"],
            )

        debug_log.append(f"Found {len(results)} results")
        logger.debug("Search parsed %d rows", len(results))
        return results, debug_log

    # ------------------------------------------------------------------
    # Download discovery
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Download discovery (Anna's Archive slow mirrors)
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
        resp = self.session.get(detail_url, timeout=self.timeout)
        resp.raise_for_status()

        tree = html.fromstring(resp.content)

        # --- cover + description ---
        cover_url = self._extract_cover(tree)
        description = self._extract_description(tree)

        downloads: Dict[str, str] = {}

        # ------------------------------------------------------------------
        # Find "Slow Partner" links – we prefer ones mentioning "no waitlist"
        # Layout today: <ul> ... <li class="list-disc"><a href="/slow_download/md5/...">Slow Partner Server #5</a> (no waitlist, ...)</li>
        # ------------------------------------------------------------------
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
            try:
                resolved = self._resolve_aa_slow_download(
                    slow_href, md5, formats, debug_log
                )
            except Exception:
                logger.debug(
                    "AA slow_download resolution failed for %s", slow_href, exc_info=True
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
    # Resolver helpers (LibGen / Sci-Hub / Z-Lib)
    # ------------------------------------------------------------------
    def _is_cloudflare_challenge(self, resp: requests.Response) -> bool:
        """Heuristically detect Cloudflare/anti-bot interstitials."""

        if resp is None:
            return False

        server_header = (resp.headers or {}).get("Server", "").lower()
        cf_ray = (resp.headers or {}).get("cf-ray") or (resp.headers or {}).get(
            "CF-RAY"
        )
        indicators = [
            "cloudflare",
            "just a moment",
            "attention required",
            "checking your browser",
            "verify you are human",
        ]

        try:
            lower_text = (resp.text or "").lower()
        except Exception:
            lower_text = ""

        return (
            resp.status_code in {403, 503}
            or "cloudflare" in server_header
            or cf_ray is not None
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

        resp = self.session.get(slow_href, timeout=self.timeout * 2)
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
        html_text = resp.text or ""
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
            # Try Playwright if present
            browser_result = self._resolve_aa_slow_download_browser(
                slow_href, formats, debug_log
            )
            if browser_result:
                return browser_result
            # If browser path failed or not available, give up on this mirror
            return None

        doc = html.fromstring(resp.content)

        # 1) Look for an obvious "Download" button/link
        candidates: List[str] = []
        candidates.extend(
            doc.xpath(
                '//a[contains(@class, "btn") or contains(translate(text(),"DOWNLOAD","download"), "download")]/@href'
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
        Optional: use Playwright to get past Cloudflare human detection.
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

        doc = html.fromstring(content)
        candidates: List[str] = []

        candidates.extend(
            doc.xpath(
                '//a[contains(@class, "btn") or contains(translate(text(),"DOWNLOAD","download"), "download")]/@href'
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
                f"Playwright did not find any direct link on {slow_href}"
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
            f"Playwright resolved AA slow_download {slow_href} -> {final_url} ({fmt})"
        )
        logger.debug(
            "Playwright resolved AA slow_download %s -> %s fmt=%s",
            slow_href,
            final_url,
            fmt,
        )
        return final_url, fmt

    def _resolve_download_link(
        self, href: str
    ) -> Optional[Tuple[str, str]]:
        """
        Given a link found on Anna's Archive detail page, try to turn it into
        a direct download URL and a format label ("epub", "pdf", etc.).

        We *skip*:
          - onion links
          - Anna's Archive torrents helper page
          - z-lib mirrors (Cloudflare / human check)
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
                # Format is still unknown at this point; _detect_format will refine later
                return resolved_url, ""
            return None

        # Sci-Hub etc. can be handled inside the libgen resolver, so
        # anything else we don't recognize we just ignore.
        logger.debug("Unrecognized download host for href=%s", href)
        return None

    def _extract_cover(self, doc: html.HtmlElement) -> str:
        candidates = doc.xpath(
            '//meta[@property="og:image"]/@content'
            ' | //img[@id="cover-img"]/@src'
            ' | //div[contains(@class, "cover")]/img/@src'
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
        """Return the first non-empty description candidate.

        The previous XPath attempted to union string() calls, which raises an
        ``XPathEvalError: Invalid type``. Instead, check each source
        sequentially and return the first populated value.
        """

        candidates = [
            "".join(doc.xpath('string(//meta[@name="description"]/@content)')).strip(),
            "".join(
                doc.xpath('string(//meta[@property="og:description"]/@content)')
            ).strip(),
            " ".join(
                [text.strip() for text in doc.xpath('//div[contains(@class, "book-description")]//text()')]
            ).strip(),
            " ".join(
                [text.strip() for text in doc.xpath('//div[@id="book-description"]//text()')]
            ).strip(),
        ]

        for candidate in candidates:
            if candidate:
                return candidate

        return ""

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

    def _safe_get(self, href: str) -> Optional[requests.Response]:
        """Best-effort GET that tracks failed hosts to avoid repeat slowdowns."""
        host = urlparse(href).hostname
        if host and host in self.unreachable_hosts:
            logger.debug("Skipping GET for previously failed host=%s", host)
            return None

        try:
            resp = self.session.get(href, timeout=self.timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if host:
                self.unreachable_hosts.add(host)
            logger.debug("Resolution failed for href=%s", href, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Cover / description helpers
    # ------------------------------------------------------------------
    def _extract_cover(self, doc: html.HtmlElement) -> str:
        candidates = doc.xpath(
            '//meta[@property="og:image"]/@content'
            ' | //img[@id="cover-img"]/@src'
            ' | //img[contains(@class, "cover")]/@src'
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
        """Extract a human-readable description from the detail page.

        We deliberately avoid XPath unions of string() results because lxml
        requires node-sets for union and will raise XPathEvalError otherwise.
        Instead we query each candidate separately and fall back in Python.
        """
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
    def _detect_format(self, text: str, href: Optional[str], formats: List[str]) -> str:
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

    def download(self, result: Dict, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Prefer explicitly selected_format if set, otherwise first in formats
        fmt = result.get("selected_format") or (result.get("formats") or ["bin"])[0]
        downloads = result.get("downloads") or {}

        url = downloads.get(fmt)
        if not url and downloads:
            # Fallback to any available link, and keep its format key
            fmt, url = next(iter(downloads.items()))

        if not url:
            logger.error(
                "No download link available for title=%s", result.get("title")
            )
            raise ValueError("No download link available")

        logger.info("Downloading from %s", url)
        logger.debug("Requested format=%s available=%s", fmt, downloads)

        resp = self._safe_get(url)
        if resp is None:
            logger.error("Download GET failed for title=%s", result.get("title"))
            raise ValueError("Failed to GET download URL")

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type:
            logger.error(
                "Download URL returned HTML (Content-Type=%s) for title=%s; likely a homepage / error page",
                content_type,
                result.get("title"),
            )
            raise ValueError("Download URL returned HTML instead of a file")

        filename = f"{result['title']}.{fmt}"
        # Strip characters illegal on Windows / Unix filenames
        safe_name = "".join(c for c in filename if c not in '\\/:*?"<>|')
        path = dest_dir / safe_name

        first_chunk: Optional[bytes] = None
        with path.open("wb") as f:
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

        logger.debug("Saved download to %s", path)
        return path

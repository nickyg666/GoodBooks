import re
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from lxml import etree, html

from settings_manager import FeedSettings

logger = logging.getLogger(__name__)


@dataclass
class ParsedItem:
    title: str
    author: str = ""
    link: str = ""
    description: str = ""
    cover: str = ""


class FeedMetadataStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Simple in-memory cache for feed metadata so we don't re-read the
        # JSON file on every use.
        self._cache: Dict = {}
        self._mtime: float = 0.0
        self._loaded: bool = False

    def _load_from_disk(self) -> Dict:
        if not self.path.exists():
            self._cache = {}
            self._mtime = 0.0
            self._loaded = True
            return {}

        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            # If stat fails, fall back to a direct read without touching the cache.
            try:
                data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                data = {}
            return data

        try:
            text = self.path.read_text()
            data = json.loads(text) if text.strip() else {}
        except json.JSONDecodeError:
            data = {}

        self._cache = data
        self._mtime = mtime
        self._loaded = True
        return data

    def load(self) -> Dict:
        if not self._loaded:
            return self._load_from_disk()

        if not self.path.exists():
            self._cache = {}
            self._mtime = 0.0
            self._loaded = True
            return {}

        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return dict(self._cache)

        if self._mtime == mtime:
            return dict(self._cache)

        return self._load_from_disk()

    def save(self, data: Dict):
        # Persist to disk
        self.path.write_text(json.dumps(data, indent=2))
        # Keep in-memory cache aligned with the latest write.
        self._cache = data
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = 0.0
        self._loaded = True

    def cache_item(self, feed_url: str, item: ParsedItem):
        data = self.load()
        feed_cache = data.setdefault(feed_url, {})
        feed_cache[item.title] = {
            "title": item.title,
            "author": item.author,
            "link": item.link,
            "description": item.description,
            "cover": item.cover,
        }
        self.save(data)
class FeedParser:
    def __init__(self, cache_path: Path, timeout: int = 30):
        self.cache = FeedMetadataStore(cache_path)
        self.timeout = timeout
         # Per-run in-memory cache: (url, mode) -> List[ParsedItem]
        self._run_cache: Dict[Tuple[str, str], List[ParsedItem]] = {}
    def reset_run_cache(self) -> None:
        """Clear the per-run cache (call at the start of /feeds/run)."""
        self._run_cache.clear()

    def parse(
        self, feed: FeedSettings, debug: Optional[List[str]] = None
    ) -> List[ParsedItem]:
        debug = debug if debug is not None else []

        # Lazily create a per-run cache on the instance
        run_cache: Dict[str, List[ParsedItem]]
        if not hasattr(self, "_run_cache"):
            self._run_cache = {}
        run_cache = self._run_cache  # type: ignore[attr-defined]

        cache_key = f"{feed.mode}:{feed.url}"

        # If we've already parsed this feed during this run, just reuse it
        if cache_key in run_cache:
            items = run_cache[cache_key]
            debug.append(
                f"[CACHE HIT] {feed.mode.upper()} {feed.url} "
                f"({len(items)} items from this run)"
            )
            return items

        # Otherwise parse fresh
        if feed.mode == "rss":
            logger.info("Parsing RSS feed url=%s", feed.url)
            debug.append(f"[RSS] {feed.url}")
            items = self._parse_rss(feed.url, debug)
        else:
            logger.info("Parsing HTML feed url=%s", feed.url)
            debug.append(f"[HTML] {feed.url}")
            items = self._parse_html(feed.url, debug)

        run_cache[cache_key] = items
        return items
    # ------------------------------------------------------------------
    # RSS parsing
    # ------------------------------------------------------------------
    def _listopia_page_url(self, url: str, page: int) -> str:
        """Return the URL for a given Listopia page.

        Goodreads Listopia uses a `?page=` query parameter. We preserve any
        existing query params and just override/append `page`.
        """
        if page <= 1:
            return url

        try:
            from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        except ImportError:
            # Extremely defensive; these are in the stdlib.
            return f"{url}?page={page}"

        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        qs["page"] = [str(page)]
        new_query = urlencode(qs, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    def _parse_rss(self, url: str, debug: List[str]) -> List[ParsedItem]:
        # Try Goodreads RSS first to capture extended metadata
        if "goodreads.com" in url:
            try:
                return self._parse_goodreads_rss(url, debug)
            except Exception as e:
                # Fall back to generic parsing if Goodreads format changes
                logger.warning("Goodreads RSS parse failed for %s: %s", url, e)
                debug.append(f"Goodreads RSS parse failed, falling back to generic: {e}")

        items: List[ParsedItem] = []

        # ------------------------------------------------------------------
        # First attempt: parse raw XML with lxml so we can use the exact
        # XPath you provided: /rss/channel/item/author_name
        # ------------------------------------------------------------------
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            root = etree.fromstring(resp.content)

            xml_items = root.xpath("/rss/channel/item")
        except Exception as e:
            xml_items = []
            logger.warning("XML RSS parse failed for %s: %s", url, e)
            debug.append(f"XML RSS parse failed for {url}: {e}")

        if xml_items:
            debug.append(
                f"Parsed RSS via XML/XPath with {len(xml_items)} items "
                f"(using /rss/channel/item/author_name)"
            )
            for entry in xml_items:
                title = (entry.xpath("string(title)") or "").strip()

                # Preferred: <author_name> as per your XPath
                author = (entry.xpath("string(author_name)") or "").strip()

                # Fallback: plain <author>
                if not author:
                    author = (entry.xpath("string(author)") or "").strip()

                link = (entry.xpath("string(link)") or "").strip()
                description = (entry.xpath("string(description)") or "").strip()

                # Very loose cover fallbacks, in case your RSS has any of these
                cover = (
                    entry.xpath("string(image/url)")
                    or entry.xpath("string(cover)")
                    or entry.xpath("string(cover_url)")
                    or ""
                ).strip()

                item = ParsedItem(
                    title=title,
                    author=author,
                    link=link,
                    description=description,
                    cover=cover,
                )

                # Skip empty titles
                if not item.title:
                    continue

                items.append(item)
                self.cache.cache_item(url, item)

            logger.info("Parsed %d items from RSS (XML/XPath)", len(items))
            debug.append(f"Parsed {len(items)} RSS entries via XML/XPath")
            return items

        # ------------------------------------------------------------------
        # Fallback: feedparser (legacy behaviour, but now also tries author_name)
        # ------------------------------------------------------------------
        debug.append(
            "XML/XPath RSS parsing returned no items; falling back to feedparser"
        )
        parsed = feedparser.parse(url)

        for entry in parsed.entries:
            # Try a few common keys for author
            author = (
                entry.get("author", "")
                or entry.get("author_name", "")
                or entry.get("dc_creator", "")
            )

            item = ParsedItem(
                title=entry.get("title", ""),
                author=author,
                link=entry.get("link", ""),
                description=entry.get("description", ""),
                cover=entry.get("image", ""),
            )
            items.append(item)
            self.cache.cache_item(url, item)

        logger.info("Parsed %d items from RSS (feedparser fallback)", len(items))
        debug.append(f"Parsed {len(items)} RSS entries via feedparser fallback")
        return items

    def _parse_goodreads_rss(self, url: str, debug: List[str]) -> List[ParsedItem]:
        """
        Parse Goodreads "review/list_rss" feeds with extended metadata.
    
        We primarily use:
          * <title>
          * <author_name> (preferred)
          * <book_description> / <description> (fallback for author)
          * <book_large_image_url>
          * <link>
        """
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        tree = etree.fromstring(resp.content)
    
        items: List[ParsedItem] = []
        for entry in tree.xpath("/rss/channel/item"):
            title = (entry.xpath("string(title)") or "").strip()
    
            # Preferred: explicit <author_name>
            author = (entry.xpath("string(author_name)") or "").strip()
    
            # Fallback: try to extract "by Author" from description fields
            if not author:
                raw_desc = (
                    entry.xpath("string(book_description)")
                    or entry.xpath("string(description)")
                    or ""
                )
                raw_desc = raw_desc.strip()
                if raw_desc:
                    # Very loose heuristic: capture text after "by "
                    # up until a line break or HTML tag.
                    m = re.search(r"\bby\s+([^<\n]+)", raw_desc, flags=re.IGNORECASE)
                    if m:
                        author = m.group(1).strip()
    
            description = (entry.xpath("string(book_description)") or "").strip()
            cover = (entry.xpath("string(book_large_image_url)") or "").strip()
            link = (entry.xpath("string(link)") or "").strip()
    
            item = ParsedItem(
                title=title,
                author=author,
                link=link,
                description=description,
                cover=cover,
            )
            items.append(item)
            self.cache.cache_item(url, item)
    
        logger.info("Parsed %d items from Goodreads RSS", len(items))
        debug.append(f"Goodreads RSS items: {len(items)}")
        return items
    
    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------
    def _parse_html(self, url: str, debug: List[str]) -> List[ParsedItem]:
        # Special handling for Goodreads Listopia pages
        if "goodreads.com" in url:
            return self._parse_goodreads_listopia(url, debug)

        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items: List[ParsedItem] = []
        for link in soup.find_all("a"):
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if not title:
                continue
            if href and not urlparse(href).netloc:
                href = requests.compat.urljoin(url, href)
            item = ParsedItem(title=title, author="", link=href)
            items.append(item)
            self.cache.cache_item(url, item)

        logger.info("Parsed %d items from HTML feed", len(items))
        debug.append(f"HTML items parsed: {len(items)}")
        return items

    def _parse_goodreads_listopia(
        self, url: str, debug: List[str]
    ) -> List[ParsedItem]:
        # ------------------------------------------------------------------
        # Simple in-memory cache so we don't re-fetch the same Listopia list
        # over and over again for RSS/HTML-based discovery.
        # ------------------------------------------------------------------
        if not hasattr(self, "_goodreads_listopia_cache"):
            self._goodreads_listopia_cache: Dict[str, List[ParsedItem]] = {}

        cache_key = url
        if cache_key in self._goodreads_listopia_cache:
            logger.debug("Goodreads Listopia cache hit for %s", url)
            debug.append(f"Goodreads Listopia cache hit for {url}")
            # Return a shallow copy so callers can't mutate the cache by accident
            return list(self._goodreads_listopia_cache[cache_key])

        items: List[ParsedItem] = []
        seen_titles: set[str] = set()
        page = 1
        max_pages = 50  # safety guard so we never loop forever

        while page <= max_pages:
            page_url = self._listopia_page_url(url, page)
            logger.info("Fetching Listopia page %s", page_url)
            debug.append(f"Fetching Listopia page: {page_url}")

            try:
                resp = requests.get(
                    page_url,
                    timeout=self.timeout,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) "
                            "Gecko/20100101 Firefox/145.0"
                        )
                    },
                )
                # If Goodreads starts returning 404 for higher pages, treat that as "no more pages"
                if resp.status_code == 404:
                    debug.append(
                        f"Got HTTP 404 for {page_url}, assuming end of Listopia pages"
                    )
                    break

                resp.raise_for_status()
            except requests.RequestException as e:
                logger.warning("Failed to fetch Listopia page %s: %s", page_url, e)
                debug.append(f"Error fetching Listopia page {page_url}: {e}")
                break

            doc = html.fromstring(resp.content)
            rows = doc.xpath('//table[contains(@class, "tableList")]/tr')

            # No rows == we've reached past the end of the list, stop paginating
            if not rows:
                debug.append(
                    f"No table rows found on {page_url}, stopping Listopia pagination"
                )
                break

            page_had_new = False

            for row in rows:
                title = (row.xpath('.//td[3]/a/span/text()') or [""])[0].strip()
                author = (
                    row.xpath('.//td[3]/span[2]/div/a/span/text()') or [""]
                )[0].strip()
                link = (row.xpath('.//td[3]/a/@href') or [""])[0]
                cover = (row.xpath('.//td[2]//img/@src') or [""])[0]

                if link and not urlparse(link).netloc:
                    link = requests.compat.urljoin(url, link)

                meta = self._scrape_goodreads_book(link, debug) if link else {}
                description = meta.get("description", "")
                cover = meta.get("cover", "")
                item = ParsedItem(
                    title=title,
                    author=author,
                    link=link,
                    description=description,
                    cover=cover,
                )

                # Skip empty titles and duplicates
                if not item.title:
                    continue
                if item.title in seen_titles:
                    continue

                seen_titles.add(item.title)
                page_had_new = True
                items.append(item)
                # Keep your existing per-item cache behaviour
                self.cache.cache_item(url, item)

            # If this page didn't add anything new, bail to avoid weird loops
            if not page_had_new:
                debug.append(
                    f"No new items found on Listopia page {page}, stopping pagination"
                )
                break

            page += 1  # <-- unconditionally go to next numeric page

        logger.info("Parsed %d items from Goodreads Listopia", len(items))
        debug.append(f"Listopia items parsed: {len(items)}")

        # Store the whole list in the in-memory cache
        self._goodreads_listopia_cache[cache_key] = list(items)

        return items


    def _scrape_goodreads_book(self, url: str, debug_log: List[str]) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "cover": "",
            "description": "",
            "genres": [],
            "rating": None,
            "rating_count": None,
            "edition_format": "",
            "edition_published": "",
            "edition_language": "",
            "reviews_html": "",
            "goodreads_url": url,
        }

        try:
            html = self._browser_get(url, debug_log)
            if not html:
                return meta

            from lxml import html as _html
            tree = _html.fromstring(html)

        # --- Cover ---------------------------------------------------------
            try:
                img_nodes = tree.cssselect("img.BookCover__image") or tree.cssselect("img[src*='books']")
                if img_nodes:
                    meta["cover"] = img_nodes[0].get("src") or meta["cover"]
            except Exception:
                pass

        # --- Description ---------------------------------------------------
            try:
            # New layout often uses data-testid or specific sections
                desc_nodes = tree.cssselect("[data-testid='description']") or tree.cssselect("div[data-test-id='description']")
                if not desc_nodes:
                # Fallback: any element with a lot of text under the main column
                    candidates = tree.xpath("//div[contains(@class, 'DetailsLayoutRightParagraph__widthConstrained')]")
                    if candidates:
                        desc_nodes = [candidates[0]]
                if desc_nodes:
                    text = " ".join(desc_nodes[0].itertext()).strip()
                    if text:
                        meta["description"] = text
            except Exception:
                pass

        # --- Genres (top 5) -----------------------------------------------
            try:
                genre_nodes = tree.cssselect("span.BookPageMetadataSection__genreButton")
                genres = []
                for node in genre_nodes[:5]:
                    txt = "".join(node.itertext()).strip()
                    if txt and txt not in genres:
                        genres.append(txt)
                meta["genres"] = genres
            except Exception:
                pass

        # --- Rating + rating count ----------------------------------------
            try:
            # Look for the main rating column
                rating_nodes = tree.cssselect("div.RatingStatistics__rating")
                if rating_nodes:
                    txt = "".join(rating_nodes[0].itertext()).strip()
                    txt = txt.replace(",", "")
                    try:
                        meta["rating"] = float(txt.split()[0])
                    except Exception:
                        pass
    
                count_nodes = tree.cssselect("div.RatingStatistics__meta span[data-testid='ratingsCount']")
                if count_nodes:
                    txt = "".join(count_nodes[0].itertext()).strip()
                    txt = txt.replace(",", "")
                # extract first integer-ish token
                    m = re.search(r"[0-9]+", txt)
                    if m:
                        try:
                            meta["rating_count"] = int(m.group(0))
                        except Exception:
                            pass
            except Exception:
                pass

        # --- Edition / format / language / published ----------------------
            try:
            # DescList dl with dt/dd pairs
                dl_nodes = tree.cssselect("dl.DescList")
                for dl in dl_nodes:
                    dts = dl.cssselect("dt")
                    dds = dl.cssselect("dd")
                    for dt_node, dd_node in zip(dts, dds):
                        key = " ".join(dt_node.itertext()).strip().lower()
                        val = " ".join(dd_node.itertext()).strip()
                        if not key or not val:
                            continue
                        if "format" in key and not meta["edition_format"]:
                            meta["edition_format"] = val
                        elif "publish" in key and not meta["edition_published"]:
                            meta["edition_published"] = val
                        elif "language" in key and not meta["edition_language"]:
                            meta["edition_language"] = val
            except Exception:
                pass

        # --- Reviews summary block (HTML) ---------------------------------
            try:
                reviews_nodes = tree.cssselect("div.ReviewsSectionStatistics")
                if reviews_nodes:
                    from lxml import etree as _etree
                    meta["reviews_html"] = _etree.tostring(
                        reviews_nodes[0],
                        encoding="unicode",
                        with_tail=False,
                    )
            except Exception:
                pass

        except Exception:
            debug_log.append(f"Failed to scrape Goodreads book page: {url}")
        # Let meta default values stand

        return meta
    def _listopia_page_url(self, url: str, page: int) -> str:
        if page <= 1:
            # ensure a page param exists for page 1 when missing
            if "page=" not in url:
                if "?" in url:
                    base, rest = url.split("?", 1)
                    return f"{base}?page=1&{rest}" if rest else f"{base}?page=1"
                return f"{url}?page=1"
            return url
        if "?" in url:
            base, rest = url.split("?", 1)
            return f"{base}?page={page}&{rest}" if rest else f"{base}?page={page}"
        return f"{url}?page={page}"

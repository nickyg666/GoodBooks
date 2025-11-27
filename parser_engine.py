import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
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

    def load(self) -> Dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return {}

    def save(self, data: Dict):
        self.path.write_text(json.dumps(data, indent=2))

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

    def parse(
        self, feed: FeedSettings, debug: Optional[List[str]] = None
    ) -> List[ParsedItem]:
        """Parse a feed, optionally collecting debug strings."""
        debug = debug if debug is not None else []
        if feed.mode == "rss":
            logger.info("Parsing RSS feed url=%s", feed.url)
            debug.append(f"[RSS] {feed.url}")
            return self._parse_rss(feed.url, debug)

        logger.info("Parsing HTML feed url=%s", feed.url)
        debug.append(f"[HTML] {feed.url}")
        return self._parse_html(feed.url, debug)

    # ------------------------------------------------------------------
    # RSS parsing
    # ------------------------------------------------------------------
    def _parse_rss(self, url: str, debug: List[str]) -> List[ParsedItem]:
        # Try Goodreads RSS first to capture extended metadata
        if "goodreads.com" in url:
            try:
                return self._parse_goodreads_rss(url, debug)
            except Exception:
                # Fall back to generic parsing if Goodreads format changes
                pass

        parsed = feedparser.parse(url)
        items: List[ParsedItem] = []
        for entry in parsed.entries:
            item = ParsedItem(
                title=entry.get("title", ""),
                author=entry.get("author", ""),
                link=entry.get("link", ""),
                description=entry.get("description", ""),
                cover=entry.get("image", ""),
            )
            items.append(item)
            self.cache.cache_item(url, item)

        logger.info("Parsed %d items from RSS feed", len(items))
        debug.append(f"Parsed {len(items)} RSS entries")
        return items

    def _parse_goodreads_rss(self, url: str, debug: List[str]) -> List[ParsedItem]:
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        tree = etree.fromstring(resp.content)

        items: List[ParsedItem] = []
        for entry in tree.xpath("/rss/channel/item"):
            title = (entry.xpath("string(title)") or "").strip()
            author = (entry.xpath("string(author_name)") or "").strip()
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
            logger.debug("Fetching Listopia page %s", page_url)
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

                description, cover = (
                    self._scrape_goodreads_book(link, debug)
                    if link
                    else ("", "")
                )

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

    def _scrape_goodreads_book(
        self, link: str, debug: List[str]
    ) -> (str, str):
        if not link:
            return "", ""

        debug.append(f"Scraping book detail: {link}")
        resp = requests.get(
            link,
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0"},
        )
        resp.raise_for_status()
        doc = html.fromstring(resp.content)

        description = "".join(
            doc.xpath(
                '//div[contains(@class, "BookPageMetadataSection")]'
                '/div[contains(@data-testid, "description-container")]//text()'
            )
        ).strip()
        if not description:
            description = "".join(
                doc.xpath('//div[@id="description"]//span/text()')
            ).strip()

        cover = "".join(
            doc.xpath(
                '//img[contains(@class, "ResponsiveImage")]/@src'
                ' | //img[@id="coverImage"]/@src'
            )
        ).strip()

        if cover:
            # Use the first discovered cover URL to avoid duplicated concatenation
            cover = cover.split()[0]

        return description, cover

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

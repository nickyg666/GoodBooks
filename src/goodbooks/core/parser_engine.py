import re
import json
import logging
import hashlib
import time
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


def _clean_rss_title(title: str) -> str:
    """Remove cruft from RSS feed titles: edition info, series numbers, publisher names."""
    if not title:
        return title
    
    # Remove common publisher/source names
    title = re.sub(r'\s*\(.*?OverDrive.*?\)', '', title, flags=re.IGNORECASE)
    
    # Remove edition info like "[Kindle Edition]", "[Audiobook]"
    title = re.sub(r'\s*\[.*?Edition.*?\]', '', title, flags=re.IGNORECASE)
    
    # Remove series/book numbers at end like "[The Long Winter 02]"
    title = re.sub(r'\s*\[.*?Book\s+\d+.*?\]', '', title, flags=re.IGNORECASE)
    
    # Remove duplicate series info: "Series Name Series Name"
    parts = title.split('-')
    if len(parts) > 1:
        # Check if last part looks like duplicate metadata
        last = parts[-1].strip()
        first_words = parts[0].strip().split()
        if len(first_words) > 0 and last.lower().startswith(first_words[0].lower()):
            title = parts[0].strip()
    
    return title.strip()


def _clean_rss_author(author: str) -> str:
    """Extract just author name, removing duplicates, publisher, ISBN, and extra metadata."""
    if not author:
        return author
    
    # Remove ISBN patterns
    author = re.sub(r'ISBN[\s:0-9\-]*', '', author, flags=re.IGNORECASE)
    
    # Remove common publisher names
    author = re.sub(r'OverDrive[\s,;]*', '', author, flags=re.IGNORECASE)
    author = re.sub(r'(Penguin|Bantam|Simon|Random House|Macmillan)[\s,;]*', '', author, flags=re.IGNORECASE)
    
    # Check if this is "LastName; FirstName" format (single author, 1 semicolon, proper capitalization)
    # Don't split these apart
    semicolon_count = author.count(';')
    if semicolon_count == 1:
        parts_on_semi = author.split(';')
        if len(parts_on_semi) == 2:
            last = parts_on_semi[0].strip()
            first = parts_on_semi[1].strip()
            # Check if both parts look like names (start with uppercase, contain letters)
            if (last and first and 
                last[0].isupper() and first[0].isupper() and
                any(c.isalpha() for c in last) and any(c.isalpha() for c in first)):
                # This is "Last; First" format, keep it as one author
                return f"{first} {last}"
    
    # Split on semicolon or comma to get individual names
    parts = re.split(r'[,;]', author)
    
    # Extract author surnames (Lastname; Firstname format)
    authors_seen = set()
    cleaned_parts = []
    for part in parts:
        part = part.strip()
        if not part or len(part) < 2:
            continue
        # Remove "by", "author:", etc
        part = re.sub(r'^(by|author|illustrated|editor)[\s:]*', '', part, flags=re.IGNORECASE).strip()
        if part and part.lower() not in authors_seen:
            authors_seen.add(part.lower())
            cleaned_parts.append(part)
    
    return ', '.join(cleaned_parts).strip()


@dataclass
class ParsedItem:
    title: str
    author: str = ""
    link: str = ""
    description: str = ""
    cover: str = ""
    goodreads_url: str = ""  # Goodreads book page URL - should be populated when available


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
            "goodreads_url": item.goodreads_url,
        }
        self.save(data)

    def get_listopia_page_hash(self, page_url: str) -> Optional[str]:
        """Get cached content hash for a Listopia page."""
        data = self.load()
        hashes = data.get("__listopia_page_hashes__", {})
        return hashes.get(page_url)

    def set_listopia_page_hash(self, page_url: str, content_hash: str):
        """Cache content hash for a Listopia page."""
        data = self.load()
        hashes = data.setdefault("__listopia_page_hashes__", {})
        hashes[page_url] = content_hash
        self.save(data)

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """Compute SHA256 hash of page content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()



class FeedParser:
    def __init__(self, cache_path: Path, timeout: int = 30):
        self.cache = FeedMetadataStore(cache_path)
        self.timeout = timeout
         # Per-run in-memory cache: (url, mode) -> List[ParsedItem]
        self._run_cache: Dict[Tuple[str, str], List[ParsedItem]] = {}
        # In-memory cache for Goodreads URL lookups: "title|author" -> goodreads_url
        self._goodreads_url_cache: Dict[str, str] = {}
    
    def reset_run_cache(self) -> None:
        """Clear the per-run cache (call at the start of /feeds/run)."""
        self._run_cache.clear()
    
    def _deduplicate_authors(self, author_str: str) -> str:
        """
        Remove duplicate author names from author string.
        Handles formats like:
        - "Author Name [Name, Author]"
        - "Author1; Author2Author1; Author2"
        - "Marcus KliewerKliewer, Marcus"
        - "Timothy RolandTimothy Roland"
        """
        if not author_str:
            return ""
        
        # Step 1: Expand CamelCase/NumberCase splits
        # Handle: lowercase/number followed by uppercase letter with lowercase (e.g., "Author2Author1" -> "Author2 Author1")
        # or: 2+ lowercase followed by uppercase
        expanded = re.sub(r'([a-z0-9]{2,})([A-Z][a-z])', r'\1 \2', author_str)
        expanded = re.sub(r'([a-z]{2,})([A-Z])', r'\1 \2', expanded)
        
        # Step 2: Split by separators with word boundaries
        parts = re.split(r'[;&\[\]]|\band\b', expanded)
        
        seen_authors = set()
        unique = []
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Normalize the full author name for comparison
            author_normalized = part.lower().strip(',. ')
            if author_normalized in seen_authors:
                continue
            
            seen_authors.add(author_normalized)
            unique.append(part)
        
        return "; ".join(unique)

    def _clean_title_for_goodreads_search(self, title: str) -> str:
        """
        Clean title for Goodreads searching by removing series info, author names, etc.
        
        Examples:
        - "The Perfect Neighborhood (The Secrets of Suburbia Book 3)-Jo; Crow" 
          -> "The Perfect Neighborhood"
        - "Miss Moss: The Long Winter (Book 2)" -> "Miss Moss: The Long Winter"
        - "The House on the Strand-Daphne; Du; Maurier" -> "The House on the Strand"
        """
        if not title:
            return title
        
        # Remove anything in parentheses that looks like series info
        # Pattern: "(The Series Name Book N)" or similar
        title = re.sub(r'\s*\([^)]*(?:book|series|volume|vol|part|book\s+\d+)[^)]*\)', 
                       '', title, flags=re.IGNORECASE)
        
        # Remove author names after dash or hyphen
        # Pattern: "-Author Name" or similar
        title = re.sub(r'\s*-\s*[A-Z][a-z]+(?:\s*;\s*[A-Z].*)?$', '', title)
        
        # Remove any remaining parenthetical info that's not essential
        # Only keep if it looks like it's part of the actual title (e.g., "Sense and Sensibility (film)")
        # Remove if it contains keywords like "book", "volume", "series", "author"
        title = re.sub(r'\s*\([^)]*(?:book|series|volume|vol|part|author|edition|annotated)[^)]*\)',
                       '', title, flags=re.IGNORECASE)
        
        # Clean up extra whitespace
        title = re.sub(r'\s+', ' ', title).strip()
        
        return title

    def _resolve_goodreads_url(self, title: str, author: str) -> str:
        """
        Try to resolve a Goodreads URL from title and author.
        Returns empty string if not found (book might not be on Goodreads).
        
        Uses multiple search strategies to handle messy titles:
        1. Clean title + author (removes series info like "Book 3", author name suffixes)
        2. Just clean title (if author search fails)
        3. Original title + author as fallback
        4. Original title only (final attempt)
        
        Includes caching to avoid re-searching for the same book.
        """
        cache_key = f"{title}|{author}".lower()
        if cache_key in self._goodreads_url_cache:
            return self._goodreads_url_cache[cache_key]
        
        if not title or not author:
            return ""
        
        try:
            # Clean the title for better matching
            clean_title = self._clean_title_for_goodreads_search(title)
            
            # Try multiple search strategies in order of preference
            search_attempts = [
                (f"{clean_title} {author}", "clean title + author"),
                (clean_title, "clean title only"),
                (f"{title} {author}", "original title + author"),
                (title, "original title only"),
            ]
            
            for search_query, strategy_label in search_attempts:
                search_url = f"https://www.goodreads.com/search?q={requests.utils.quote(search_query)}"
                
                max_retries = 2  # Reduced from 3 since we have multiple strategies
                for attempt in range(max_retries):
                    try:
                        response = requests.get(
                            search_url,
                            timeout=self.timeout,
                            headers={
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) "
                                    "Gecko/20100101 Firefox/145.0"
                                )
                            }
                        )
                        response.raise_for_status()
                        
                        # Extract first book result from the HTML
                        # Look for pattern: <a href="/book/show/12345-title" or similar
                        match = re.search(r'href="(/book/show/\d+[^"]*)"', response.text)
                        if match:
                            gr_url = "https://www.goodreads.com" + match.group(1)
                            self._goodreads_url_cache[cache_key] = gr_url
                            logger.debug(f"Resolved Goodreads URL for '{title}' using {strategy_label}: {gr_url}")
                            return gr_url
                        # No match with this strategy, try next one
                        break
                    except requests.exceptions.Timeout:
                        if attempt < max_retries - 1:
                            wait_time = min(5 * (2 ** attempt), 30)
                            logger.debug("Goodreads timeout (attempt %d/%d), retrying in %ds", attempt + 1, max_retries, wait_time)
                            time.sleep(wait_time)
                        # Timeout on last attempt, try next strategy
                        break
                    except Exception as e:
                        logger.debug(f"Error searching with strategy '{strategy_label}': {e}")
                        break  # Try next strategy
            
            # No match found with any strategy - cache empty result
            self._goodreads_url_cache[cache_key] = ""
            logger.debug(f"No Goodreads URL found for '{title}' by '{author}' after trying all strategies")
            return ""
        
        except Exception as e:
            logger.debug(f"Failed to resolve Goodreads URL for '{title}' by '{author}': {e}")
            return ""

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
        """
        Parse RSS feeds using feedparser (reliable and handles all RSS variants).
        For Goodreads, try the specialized parser first, but fall back to generic.
        """
        # Try Goodreads RSS first to capture extended metadata
        if "goodreads.com" in url:
            try:
                return self._parse_goodreads_rss(url, debug)
            except Exception as e:
                # Fall back to generic parsing if Goodreads format changes
                logger.warning("Goodreads RSS parse failed for %s: %s", url, e)
                debug.append(f"Goodreads RSS parse failed, falling back to generic: {e}")

        # Use feedparser for all generic RSS feeds
        # feedparser is robust and handles malformed XML gracefully
        items: List[ParsedItem] = []
        
        try:
            parsed = feedparser.parse(url)
            
            if parsed.bozo and parsed.bozo_exception:
                logger.warning(
                    "Feedparser encountered issues parsing %s: %s",
                    url,
                    parsed.bozo_exception
                )
                debug.append(f"Feedparser warnings: {parsed.bozo_exception}")
            
            for entry in parsed.entries:
                # Extract title with fallback
                title = (entry.get("title", "") or "").strip()
                
                # Try multiple author field names
                author = (
                    entry.get("author", "")
                    or entry.get("author_name", "")
                    or entry.get("dc_creator", "")
                    or entry.get("creator", "")
                    or ""
                ).strip()
                
                # Clean title and author of RSS metadata cruft
                title = _clean_rss_title(title)
                author = _clean_rss_author(author)
                
                # Deduplicate author names if present
                if author:
                    author = self._deduplicate_authors(author)
                
                # Try multiple description field names
                description = (
                    entry.get("description", "")
                    or entry.get("summary", "")
                    or entry.get("content", "")
                    or ""
                ).strip()
                
                # Try multiple link field names
                link = (entry.get("link", "") or "").strip()
                
                # Try multiple image/cover field names
                cover = (
                    entry.get("image", "")
                    or entry.get("media_content", "")
                    or entry.get("cover", "")
                    or entry.get("book_large_image_url", "")
                    or ""
                ).strip()
                
                # If no cover directly, try to extract from media or image sub-objects
                if not cover and hasattr(entry, 'media_content') and entry.media_content:
                    if isinstance(entry.media_content, list) and len(entry.media_content) > 0:
                        cover = entry.media_content[0].get('url', '')
                
                # Skip empty titles
                if not title:
                    continue
                
                item = ParsedItem(
                    title=title,
                    author=author,
                    link=link,
                    description=description,
                    cover=cover,
                )
                items.append(item)
                self.cache.cache_item(url, item)
            
            logger.info("Parsed %d items from RSS feed %s", len(items), url)
            debug.append(f"Parsed {len(items)} RSS entries via feedparser")
            return items
            
        except Exception as e:
            logger.exception("Failed to parse RSS feed %s: %s", url, e)
            debug.append(f"RSS feed parse failed: {e}")
            return []


    def _parse_goodreads_rss(self, url: str, debug: List[str]) -> List[ParsedItem]:
        """
        Parse Goodreads "review/list_rss" feeds with extended metadata.
        Uses feedparser which is robust and handles various RSS formats.
        """
        items: List[ParsedItem] = []
        
        try:
            parsed = feedparser.parse(url)
            
            if parsed.bozo and parsed.bozo_exception:
                logger.warning(
                    "Feedparser encountered issues parsing Goodreads RSS %s: %s",
                    url,
                    parsed.bozo_exception
                )
            
            for entry in parsed.entries:
                # Extract title
                title = (entry.get("title", "") or "").strip()
                
                # Preferred: explicit <author_name>
                author = (entry.get("author_name", "") or entry.get("author", "") or "").strip()
                
                # Fallback: try to extract "by Author" from description fields
                if not author:
                    raw_desc = (
                        entry.get("book_description", "")
                        or entry.get("description", "")
                        or entry.get("summary", "")
                        or ""
                    ).strip()
                    
                    if raw_desc:
                        # Heuristic: capture text after "by "
                        m = re.search(r"\bby\s+([^<\n]+)", raw_desc, flags=re.IGNORECASE)
                        if m:
                            author = m.group(1).strip()
                
                # Clean title and author of RSS metadata cruft
                title = _clean_rss_title(title)
                author = _clean_rss_author(author)
                
                # Try description fields in order of preference
                description = (
                    entry.get("book_description", "")
                    or entry.get("description", "")
                    or entry.get("summary", "")
                    or ""
                ).strip()
                
                # Try image/cover fields
                cover = (
                    entry.get("book_large_image_url", "")
                    or entry.get("image", "")
                    or entry.get("media_content", "")
                    or ""
                ).strip()
                
                # If no cover directly, try to extract from media sub-objects
                if not cover and hasattr(entry, 'media_content') and entry.media_content:
                    if isinstance(entry.media_content, list) and len(entry.media_content) > 0:
                        cover = entry.media_content[0].get('url', '')
                
                link = (entry.get("link", "") or "").strip()
                
                # Skip empty titles
                if not title:
                    continue
                
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
            
        except Exception as e:
            logger.exception("Failed to parse Goodreads RSS %s: %s", url, e)
            debug.append(f"Goodreads RSS parse failed: {e}")
            return []

    
    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------
    def _parse_html(self, url: str, debug: List[str]) -> List[ParsedItem]:
        # Special handling for Goodreads pages
        if "goodreads.com" in url:
            # Check if it's a genres/most_read page
            if "/genres/most_read" in url or "/genres/" in url:
                return self._parse_goodreads_genre_page(url, debug)
            # Check if it's a series page
            elif "/series/" in url:
                return self._parse_goodreads_series(url, debug)
            # Otherwise treat as Listopia
            return self._parse_goodreads_listopia(url, debug)

        items: List[ParsedItem] = []
        
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to fetch HTML feed %s: %s", url, e)
            debug.append(f"HTML feed fetch failed: {e}")
            return []
        
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            logger.warning("Failed to parse HTML with BeautifulSoup: %s", e)
            debug.append(f"HTML parse failed: {e}")
            return []

        try:
            for link in soup.find_all("a"):
                try:
                    title = link.get_text(strip=True)
                    if not title:
                        continue
                    
                    href = link.get("href", "")
                    if href and not urlparse(href).netloc:
                        href = requests.compat.urljoin(url, href)
                    
                    item = ParsedItem(title=title, author="", link=href)
                    items.append(item)
                    self.cache.cache_item(url, item)
                except Exception as e:
                    # Skip malformed links, don't fail entire parse
                    logger.debug("Error processing HTML link: %s", e)
                    continue
        except Exception as e:
            logger.warning("Error iterating HTML links: %s", e)
            debug.append(f"HTML link iteration failed: {e}")
            # Return what we've collected so far instead of nothing

        logger.info("Parsed %d items from HTML feed", len(items))
        debug.append(f"HTML items parsed: {len(items)}")
        return items

    @staticmethod
    def _upgrade_goodreads_cover_resolution(url: str) -> str:
        """
        Upgrade Goodreads cover URL to high resolution.
        Strategy:
        1. Try clean URL without any SX/SY parameters: {base}.jpg
        2. If that fails, use high-res: _SX500_SY800_
        """
        if not url or 'goodreads.com' not in url:
            return url
        
        # Extract the clean base URL (everything before _SX or _SY)
        # Example: https://i.gr-assets.com/images/S/compressed.photo.goodreads.com/books/1687354230i/179899724._SX50_.jpg
        # Becomes: https://i.gr-assets.com/images/S/compressed.photo.goodreads.com/books/1687354230i/179899724.jpg
        clean_url = re.sub(r'\._S[XY]\d+(?:_S[XY]\d+)*_\.', '.', url)
        
        # Also handle case where dimensions are at the end without trailing underscore
        if clean_url == url:  # No _SX/_SY found, try alternative pattern
            clean_url = re.sub(r'_S[XY]\d+(?:_S[XY]\d+)*$', '', url)
        
        # Return the clean URL (Goodreads will serve default resolution)
        return clean_url

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
            # For "Most Read This Week" lists (Goodreads list ID 8306), only fetch the first page
            # This list is meant to show top 100 most-read books of the week, not paginated results
            if page > 1 and "/list/show/8306" in url:
                logger.info("Stopping Listopia pagination after first page for Most Read list (ID 8306)")
                debug.append("Stopping after page 1 for Most Read This Week list (ID 8306)")
                break
            
            page_url = self._listopia_page_url(url, page)
            logger.info("Fetching Listopia page %s", page_url)
            debug.append(f"Fetching Listopia page: {page_url}")

            try:
                resp = None
                max_retries = 3
                for attempt in range(max_retries):
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
                        break
                    except requests.exceptions.Timeout:
                        if attempt < max_retries - 1:
                            wait_time = min(10 * (2 ** attempt), 60)
                            logger.warning("Goodreads Listopia timeout for page %d (attempt %d/%d), retrying in %ds", page, attempt + 1, max_retries, wait_time)
                            time.sleep(wait_time)
                        else:
                            logger.error("Goodreads Listopia timeout for page %d (final attempt failed)", page)
                            debug.append(f"Listopia timeout after {max_retries} attempts")
                            raise
                
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

            # Check if page content has changed using content hash
            current_hash = self.cache.compute_content_hash(resp.text)
            cached_hash = self.cache.get_listopia_page_hash(page_url)
            
            if cached_hash == current_hash:
                logger.debug("Listopia page %s unchanged (hash match), skipping parse", page_url)
                debug.append(f"Listopia page {page} unchanged (cached), skipping")
                page += 1
                continue
            
            # Content changed or first time seeing this page, cache the new hash
            self.cache.set_listopia_page_hash(page_url, current_hash)
            logger.info("Listopia page %s has new content, parsing", page_url)
            debug.append(f"Listopia page {page} has new content, parsing")

            try:
                doc = html.fromstring(resp.content)
            except Exception as e:
                logger.warning("Failed to parse Listopia HTML: %s", e)
                debug.append(f"Listopia HTML parse failed: {e}")
                break
            
            try:
                rows = doc.xpath('//table[contains(@class, "tableList")]/tr')
            except Exception as e:
                logger.warning("Failed to extract rows from Listopia page: %s", e)
                debug.append(f"Listopia row extraction failed: {e}")
                break

            # No rows == we've reached past the end of the list, stop paginating
            if not rows:
                debug.append(
                    f"No table rows found on {page_url}, stopping Listopia pagination"
                )
                break

            page_had_new = False

            for row in rows:
                try:
                    # Extract title - get text from FIRST span only to avoid concatenation
                    # (Goodreads sometimes has multiple spans in the same anchor tag)
                    title_elem = row.xpath('.//td[3]/a/span[1]')
                    if title_elem:
                        # Get text from first element only, not concatenated from all
                        title = (row.xpath('string(.//td[3]/a/span[1])') or "").strip()
                    else:
                        title = ""
                    
                    # Some Goodreads lists append metadata/tags to titles like:
                    # "Title, Description, Tags" - keep only the book title part (before first comma)
                    # This is a heuristic: if title is very long (>100 chars) and has commas,
                    # it likely has appended metadata
                    if title and len(title) > 100 and ',' in title:
                        # Split by comma and take the first part (the actual book title)
                        first_part = title.split(',')[0].strip()
                        if len(first_part) > 20:  # Sanity check: title should be reasonably long
                            logger.debug(
                                "Listopia: Trimmed title with appended metadata. Before: %r len=%d; After: %r len=%d",
                                title,
                                len(title),
                                first_part,
                                len(first_part),
                            )
                            title = first_part
                    
                    # DEBUG: Log the extracted title with repr to see exact characters
                    if title:
                        logger.debug(
                            "Listopia row: extracted title (repr)=%r title_len=%d has_spaces=%s",
                            title,
                            len(title),
                            " " in title,
                        )
                    
                    # Extract author similarly - use [1] to select only first span element
                    author_elem = row.xpath('.//td[3]/span[2]/div/a/span[1]')
                    if author_elem:
                        author = (row.xpath('string(.//td[3]/span[2]/div/a/span[1])') or "").strip()
                    else:
                        author = ""
                    
                    # DEBUG: Log extracted author (if any)
                    if author:
                        logger.debug(
                            "Listopia row: extracted author (repr)=%r for title=%r",
                            author,
                            title,
                        )
                    elif title:
                        # Log when we have a title but NO author (data quality issue)
                        logger.debug(
                            "Listopia row: NO AUTHOR found for title=%r (xpath may have failed)",
                            title,
                        )
                    
                    # Extract link with safe attribute access
                    try:
                        link = (row.xpath('.//td[3]/a/@href') or [""])[0]
                    except (IndexError, TypeError):
                        link = ""
                    
                    # Extract cover with safe attribute access
                    try:
                        cover = (row.xpath('.//td[2]//img/@src') or [""])[0]
                    except (IndexError, TypeError):
                        cover = ""
                    
                    # Upgrade Goodreads cover to high resolution
                    if cover:
                        cover = self._upgrade_goodreads_cover_resolution(cover)

                    if link and not urlparse(link).netloc:
                        link = requests.compat.urljoin(url, link)

                    # For Goodreads Listopia, the link is the Goodreads book page
                    goodreads_url = link if (link and "goodreads.com" in link) else ""

                    # Skip per-book scraping during feed parsing to avoid blocking.
                    # Book metadata (description, cover, etc.) is already extracted from the Listopia page.
                    # If needed, can be fetched later during search phase when items are being processed.
                    description = ""
                    # cover is already extracted from Listopia page XPath above

                    item = ParsedItem(
                        title=title,
                        author=author,
                        link=link,
                        description=description,
                        cover=cover,
                        goodreads_url=goodreads_url,
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
                
                except Exception as e:
                    # Skip malformed rows, don't fail entire page
                    logger.debug("Error processing Listopia row: %s", e)
                    continue

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

    def _parse_goodreads_series(
        self, url: str, debug: List[str]
    ) -> List[ParsedItem]:
        """Parse a Goodreads series page and extract all books."""
        import re
        from goodreads_scraper import scrape_series_books
        
        try:
            # Extract series ID from URL: https://www.goodreads.com/series/237987
            match = re.search(r'/series/(\d+)', url)
            if not match:
                logger.warning("Could not extract series ID from URL: %s", url)
                debug.append(f"Failed to extract series ID from: {url}")
                return []
            
            series_id = match.group(1)
            logger.info("Parsing Goodreads series %s from URL %s", series_id, url)
            debug.append(f"Parsing Goodreads series {series_id}")
            
            # Use the existing scraper to get series books
            series_data = scrape_series_books(series_id, "")
            
            if not series_data or not series_data.get("books"):
                logger.warning("No books found in series %s", series_id)
                debug.append(f"No books found in series {series_id}")
                return []
            
            items: List[ParsedItem] = []
            for book in series_data.get("books", []):
                try:
                    title = book.get("title", "").strip()
                    author = book.get("author", "").strip()
                    goodreads_url = book.get("url", "")
                    
                    if not title:
                        continue
                    
                    # Create a ParsedItem with Goodreads URL as the link
                    item = ParsedItem(
                        title=title,
                        author=author,
                        link=goodreads_url  # This will be used for metadata lookup
                    )
                    items.append(item)
                except Exception as e:
                    logger.debug("Error processing book from series: %s", e)
                    continue
            
            logger.info("Parsed %d items from Goodreads series %s", len(items), series_id)
            debug.append(f"Goodreads series items parsed: {len(items)}")
            return items
        
        except Exception as e:
            logger.exception("Error parsing Goodreads series: %s", e)
            debug.append(f"Error parsing Goodreads series: {e}")
            return []

    def _parse_goodreads_genre_page(
       self, url: str, debug: List[str]
    ) -> List[ParsedItem]:
        """
        Parse Goodreads genre/most_read pages.
        Example: https://www.goodreads.com/genres/most_read/thriller
         
         Books are in divs with class "bigBoxBody", each containing an <a> with href="/book/show/{id}-{name}"
        """
        items: List[ParsedItem] = []
        seen_titles: set[str] = set()
        
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to fetch Goodreads genre page %s: %s", url, e)
            debug.append(f"Goodreads genre page fetch failed: {e}")
            return []
        
        try:
            doc = html.fromstring(resp.content)
        except Exception as e:
            logger.warning("Failed to parse Goodreads genre page: %s", e)
            debug.append(f"Goodreads genre page parse failed: {e}")
            return []
        
        # Find all book containers with class "bigBoxBody"
        try:
            book_divs = doc.xpath('//div[@class="bigBoxBody"]')
            logger.debug("Found %d book containers on Goodreads genre page", len(book_divs))
            
            for book_div in book_divs:
                try:
                    # Extract book link: href="/book/show/{id}-{name}"
                    links = book_div.xpath('.//a[contains(@href, "/book/show/")]')
                    if not links:
                        continue
                    
                    link_elem = links[0]
                    href = link_elem.get("href", "").strip()
                    if not href:
                        continue
                    
                    # Make absolute URL
                    if not href.startswith("http"):
                        href = "https://www.goodreads.com" + href
                    
                    # Extract title from link text
                    title = link_elem.get_text(strip=True)
                    if not title:
                        continue
                    
                    # Skip duplicates
                    if title in seen_titles:
                        continue
                    
                    # Extract author if available (may be in a nearby span or link)
                    author = ""
                    author_elems = book_div.xpath('.//span[@class="greyText"]//a')
                    if author_elems:
                        author = author_elems[0].get_text(strip=True)
                    
                    # Extract cover image if available
                    cover = ""
                    img_elems = book_div.xpath('.//img[@class="bookSmallImg"]')
                    if img_elems:
                        cover = img_elems[0].get("src", "").strip()
                        if cover and not cover.startswith("http"):
                            cover = "https:" + cover if cover.startswith("//") else "https://www.goodreads.com" + cover
                    
                    item = ParsedItem(
                        title=title,
                        author=author,
                        link=href,
                        description="",
                        cover=cover,
                        goodreads_url=href,
                    )
                    
                    seen_titles.add(title)
                    items.append(item)
                    self.cache.cache_item(url, item)
                    
                except Exception as e:
                    logger.debug("Error processing Goodreads genre book item: %s", e)
                    continue
            
            logger.info("Parsed %d items from Goodreads genre page", len(items))
            debug.append(f"Goodreads genre page items: {len(items)}")
            
        except Exception as e:
            logger.warning("Error parsing Goodreads genre page items: %s", e)
            debug.append(f"Error parsing genre page items: {e}")
        
        return items


    def _scrape_goodreads_book(self, url: str, debug_log: List[str]) -> Dict[str, Any]:
        # Normalize relative URLs to absolute
        if url and not url.startswith("http"):
            url = "https://www.goodreads.com" + url
        
        # Ensure we link to /book/ page, not /reviews/ page
        if "/reviews/" in url.lower():
            # Convert /reviews/ link to /book/ link
            url = url.replace("/reviews/", "/book/").split("?")[0]  # Remove query params
        
        meta: Dict[str, Any] = {
            "cover": "",
            "description": "",
            "genres": [],
            "rating": None,
            "rating_count": None,
            "pages": None,
            "edition_format": "",
            "edition_published": "",
            "edition_language": "",
            "reviews_html": "",
            "goodreads_url": url,
        }

        try:
            # Fetch the Goodreads book page using requests with standard headers
            # Include Accept-Language header to prefer English content
            # With retry logic for timeouts
            html_text = None
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    resp = requests.get(
                        url,
                        timeout=self.timeout,
                        headers={
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) "
                                "Gecko/20100101 Firefox/145.0"
                            ),
                            "Accept-Language": "en-US,en;q=0.9"
                        }
                    )
                    resp.raise_for_status()
                    html_text = resp.text
                    break
                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        wait_time = min(10 * (2 ** attempt), 60)
                        logger.warning("Goodreads timeout fetching %s (attempt %d/%d), retrying in %ds", url, attempt + 1, max_retries, wait_time)
                        time.sleep(wait_time)
                    else:
                        logger.error("Goodreads timeout fetching %s (final attempt failed)", url)
                        debug_log.append(f"Failed to fetch Goodreads page (timeout after {max_retries} attempts)")
                        return meta
                except Exception as e:
                    logger.debug("Failed to fetch Goodreads book page %s: %s", url, e)
                    debug_log.append(f"Failed to fetch Goodreads page: {e}")
                    return meta
            
            if not html_text:
                return meta

            try:
                from lxml import html as _html
                tree = _html.fromstring(html_text)
            except Exception as e:
                logger.warning("Failed to parse Goodreads book HTML from %s: %s", url, e)
                debug_log.append(f"Goodreads book page parse failed: {e}")
                return meta

            # --- Cover ---------------------------------------------------------
            try:
                # Search for all book cover images using multiple strategies
                # Primary: ResponsiveImage inside BookCover__image div (highest quality)
                img_nodes = (
                    tree.cssselect("div.BookCover__image img.ResponsiveImage")
                    or tree.cssselect("img.ResponsiveImage")
                    or tree.cssselect("img.BookCover__image") 
                    or tree.cssselect("img[data-testid='BookCoverImage']")
                    or tree.xpath("//img[contains(translate(@alt, 'COVER', 'cover'), 'cover')]")  # Case-insensitive alt search
                    or tree.cssselect("img[src*='books'][src*='_SX']")
                    or tree.xpath("//img[contains(@src, 'books.google') or contains(@src, 'goodreads') or contains(@src, '_SX')]")
                )
                if img_nodes:
                    covers = []
                    for img_node in img_nodes:
                        src = img_node.get("src", "").strip()
                        if src and ('_SX' in src or 'books' in src):
                            covers.append(src)
                    
                    # If we have multiple covers, prefer the highest resolution
                    # Goodreads URLs often have patterns like: url._SX600_SY900_
                    # We want the largest dimensions available, don't upscale
                    if covers:
                        def get_cover_dimensions(url: str) -> tuple[int, int]:
                            """Extract dimensions from Goodreads cover URL."""
                            # Look for patterns like _SX600_SY900_
                            match = re.search(r'_SX(\d+)_SY(\d+)_', url)
                            if match:
                                return (int(match.group(1)), int(match.group(2)))
                            return (0, 0)
                        
                        # Sort by total pixels (width * height) descending
                        covers.sort(
                            key=lambda url: get_cover_dimensions(url)[0] * get_cover_dimensions(url)[1],
                            reverse=True
                        )
                        
                        # Use the highest resolution available, but ensure minimum decent size
                        best_cover = covers[0]
                        
                        # Upgrade Goodreads covers to high resolution (SX500)
                        if 'goodreads.com' in best_cover:
                            best_cover = self._upgrade_goodreads_cover_resolution(best_cover)
                        
                        meta["cover"] = best_cover
            except Exception as e:
                logger.debug("Failed to extract cover: %s", e)

            # --- Description ---------------------------------------------------
            try:
                desc_nodes = tree.cssselect("[data-testid='description']") or tree.cssselect("div[data-test-id='description']")
                if not desc_nodes:
                    # Fallback: any element with a lot of text under the main column
                    candidates = tree.xpath("//div[contains(@class, 'DetailsLayoutRightParagraph__widthConstrained')]")
                    if candidates:
                        desc_nodes = [candidates[0]]
                if desc_nodes:
                    try:
                        text = " ".join(desc_nodes[0].itertext()).strip()
                        if text:
                            meta["description"] = text
                    except Exception as e:
                        logger.debug("Failed to extract description text: %s", e)
            except Exception as e:
                logger.debug("Failed to find description nodes: %s", e)

            # --- Genres (top 5) -----------------------------------------------
            try:
                genre_nodes = tree.cssselect("span.BookPageMetadataSection__genreButton")
                genres = []
                for node in genre_nodes[:5]:
                    try:
                        txt = "".join(node.itertext()).strip()
                        if txt and txt not in genres:
                            genres.append(txt)
                    except Exception:
                        continue
                meta["genres"] = genres
            except Exception as e:
                logger.debug("Failed to extract genres: %s", e)

            # --- Rating + rating count ----------------------------------------
            try:
                rating_nodes = tree.cssselect("div.RatingStatistics__rating")
                if rating_nodes:
                    try:
                        txt = "".join(rating_nodes[0].itertext()).strip()
                        txt = txt.replace(",", "")
                        meta["rating"] = float(txt.split()[0])
                    except (ValueError, IndexError):
                        pass
    
                count_nodes = tree.cssselect("div.RatingStatistics__meta span[data-testid='ratingsCount']")
                if count_nodes:
                    try:
                        txt = "".join(count_nodes[0].itertext()).strip()
                        txt = txt.replace(",", "")
                        # extract first integer-ish token
                        m = re.search(r"[0-9]+", txt)
                        if m:
                            meta["rating_count"] = int(m.group(0))
                    except (ValueError, AttributeError):
                        pass
            except Exception as e:
                logger.debug("Failed to extract rating: %s", e)

            # --- Edition / format / language / published / pages ----------------
            try:
                dl_nodes = tree.cssselect("dl.DescList")
                for dl in dl_nodes:
                    try:
                        dts = dl.cssselect("dt")
                        dds = dl.cssselect("dd")
                        for dt_node, dd_node in zip(dts, dds):
                            try:
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
                                elif "pages" in key and not meta["pages"]:
                                    # Extract just the number of pages
                                    try:
                                        pages_match = re.search(r"(\d+)\s*pages?", val, re.IGNORECASE)
                                        if pages_match:
                                            meta["pages"] = int(pages_match.group(1))
                                    except (ValueError, AttributeError):
                                        pass
                            except Exception:
                                continue
                    except Exception:
                        continue
            except Exception as e:
                logger.debug("Failed to extract edition details: %s", e)

            # --- Reviews summary block (HTML) ---------------------------------
            try:
                reviews_nodes = tree.cssselect("div.ReviewsSectionStatistics")
                if reviews_nodes:
                    try:
                        from lxml import etree as _etree
                        meta["reviews_html"] = _etree.tostring(
                            reviews_nodes[0],
                            encoding="unicode",
                            with_tail=False,
                        )
                    except Exception as e:
                        logger.debug("Failed to serialize reviews HTML: %s", e)
            except Exception as e:
                logger.debug("Failed to extract reviews: %s", e)

        except Exception as e:
            logger.warning("Failed to scrape Goodreads book page %s: %s", url, e)
            debug_log.append(f"Failed to scrape Goodreads book page: {url}")
        
        return meta
    
    @staticmethod
    def extract_cover_from_goodreads_html(html_content: str) -> str:
        """Extract and upgrade the best cover URL from Goodreads HTML."""
        try:
            tree = html.fromstring(html_content)
            # Search for all book cover images using multiple strategies
            # Primary: ResponsiveImage inside BookCover__image div (highest quality)
            img_nodes = (
                tree.cssselect("div.BookCover__image img.ResponsiveImage")
                or tree.cssselect("img.ResponsiveImage")
                or tree.cssselect("img.BookCover__image") 
                or tree.cssselect("img[data-testid='BookCoverImage']")
                or tree.xpath("//img[contains(translate(@alt, 'COVER', 'cover'), 'cover')]")
                or tree.cssselect("img[src*='books'][src*='_SX']")
                or tree.xpath("//img[contains(@src, 'books.google') or contains(@src, 'goodreads') or contains(@src, '_SX')]")
            )
            if img_nodes:
                covers = []
                for img_node in img_nodes:
                    src = img_node.get("src", "").strip()
                    if src and ('_SX' in src or 'books' in src):
                        covers.append(src)
                
                if covers:
                    def get_cover_dimensions(url: str) -> tuple:
                        match = re.search(r'_SX(\d+)_SY(\d+)_', url)
                        if match:
                            return (int(match.group(1)), int(match.group(2)))
                        return (0, 0)
                    
                    covers.sort(
                        key=lambda url: get_cover_dimensions(url)[0] * get_cover_dimensions(url)[1],
                        reverse=True
                    )
                    
                    # Use highest resolution available, but ensure minimum decent size
                    best_cover = covers[0]
                    
                    # If Goodreads cover is below 300px, upgrade to 300px width
                    if 'goodreads.com' in best_cover:
                        w, h = get_cover_dimensions(best_cover)
                        if w < 300:
                            # Upgrade to 300x450 (standard Goodreads size)
                            best_cover = re.sub(r'_SX\d+_SY\d+_', '_SX300_SY450_', best_cover)
                            if '_SX' not in best_cover:
                                # If it didn't have dimensions, add them
                                best_cover = best_cover.replace('i/', 'i/300_SY450/')
                    
                    return best_cover
        except Exception as e:
            logger.debug("Failed to extract cover from HTML: %s", e)
        
        return None
    
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

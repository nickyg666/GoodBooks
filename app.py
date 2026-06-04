#!/usr/bin/python3
import re
import logging
import os
import random
import traceback
import json
import threading
from threading import Lock
from dataclasses import asdict
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import html
import math
import hashlib
import base64
import requests
import sys
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw
from io import BytesIO
from bs4 import BeautifulSoup
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
    send_from_directory,
    Response,
    stream_with_context,
    jsonify,
)

from logging_config import configure_logging
from parser_engine import FeedParser, ParsedItem
from search_engine import AnnaSource, SearchOptions, set_download_concurrency
from settings_manager import HistoryManager, SettingsManager, UserSettings, FeedSettings
from ebook_metadata_extractor import extract_book_metadata
import time
import uuid
from datetime import datetime

from ebook_metadata_extractor import convert_to_epub
from cover_cache_manager import get_cache_manager
from settings_manager import filter_genres, is_genre_allowed
from epub_distributor import check_and_distribute_epub_update, send_epub_to_new_user

# Global instance for cleanup_author - will be initialized when history_manager is ready
_global_history_manager = None
from goodreads_scraper import scrape_genre_lists, scrape_list_detail
feed_progress_lock = Lock()
metadata_progress_lock = Lock()
cloudflare_lock = Lock()  # Serialize Cloudflare challenge resolution across threads
library_cache_lock = Lock()
search_cache_lock = Lock()  # Serialize search cache reads/writes across threads
_LIBRARY_LOOKUP_CACHE = set()  # Global cache of (title, author) tuples already in library

feed_progress_state = {
    "run_id": None,            # uuid4 hex string for current run
    "active": False,

    # overall
    "overall": {
        "total_items": 0,
        "completed_items": 0,
        "start_time": None,    # float seconds (time.time())
        "eta_seconds": None,   # computed
    },

    # per feed: key -> dict
    # key could be f"{user.name}::{feed.url}"
    "feeds": {
        # "user::feed_url": {
        #     "user": "...",
        #     "feed_url": "...",
        #     "feed_mode": "rss/html/...",
        #     "label": "nick: Goodreads to-read", # nice display
        #     "total_items": 0,
        #     "completed_items": 0,
        #     "start_time": None,
        #     "eta_seconds": None,
        #     "active": True/False,
        # }
    },
}

metadata_progress_state = {
    "active": False,
    "total_books": 0,
    "completed_books": 0,
    "start_time": None,
    "eta_seconds": None,
    "percentage": 0,
    "type": None,  # "manual" or "background"
    "current_book": "",
    "current_step": "",
}
# Queue for collecting metadata enrichment failures during a maintenance cycle
metadata_enrichment_failures = []
metadata_enrichment_failures_lock = Lock()


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret-key")

@app.context_processor
def inject_kindle_detection():
    """Inject is_kindle flag based on request user agent."""
    is_kindle = bool(__import__('re').search(
        r'Kindle|KF|K8|KFOTT|KFGIWI|KFJWI|KFJWA|KFAPWI|KFSOWI|KFTHWI|KFTHWA',
        request.headers.get('User-Agent', '')
    ))
    return {'is_kindle': is_kindle}

@app.context_processor
def inject_settings():
    """Inject settings into all templates."""
    return {'settings': settings_manager.settings}


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)
# Ensure covers directory exists
COVERS_DIR = DATA_DIR / "covers"
COVERS_DIR.mkdir(exist_ok=True)

KINDLE_ATTACHMENT_LIMIT_BYTES = 20 * 1024 * 1024
# Settings / history managers
settings_manager = SettingsManager(DATA_DIR / "settings.json")
history_manager = HistoryManager(DATA_DIR / "history.json")
history_lock = threading.Lock()
debug_lock = threading.Lock()
library_metadata_lock = threading.Lock()
_SEARCH_CACHE_LOCK = threading.Lock()

# Library addition queue for batch notifications (120-second timeout)
library_addition_queue: Dict[str, List[Dict]] = {}  # user_name -> list of entries
library_queue_lock = threading.Lock()
library_queue_timers: Dict[str, threading.Timer] = {}  # user_name -> timer

# Kindle auto-send queue for batch sending (25 files, 24MB limit)
kindle_queue: Dict[str, List[Tuple[Path, Dict]]] = {}  # user_name -> list of (path, result) tuples
kindle_queue_lock = threading.Lock()

# In-memory mirror of disk-backed search cache
_SEARCH_CACHE_LOADED = False
_SEARCH_CACHE: Dict[str, Dict] = {}

# Configure logging based on settings
logger = configure_logging(BASE_DIR, getattr(settings_manager.settings, "log_level", "INFO"))

# Feed parser + search source
FEED_CACHE_PATH = DATA_DIR / "feed_cache.json"
FEED_DEBUG_LOG = DATA_DIR / "feed_debug.log"
METADATA_MISS_LOG = DATA_DIR / "metadata_misses.log"
SEARCH_CACHE_PATH = DATA_DIR / "search_cache.json"
# Library metadata + constants
LIBRARY_METADATA_PATH = DATA_DIR / "library_metadata.json"
_LIBRARY_METADATA_CACHE: Dict[str, Dict] = {}
_LIBRARY_ENTRIES_CACHE: List[Dict] = []
_LIBRARY_ENTRIES_LAST_SCAN: float = 0.0
_LIBRARY_METADATA_MTIME: float = 0.0
_LIBRARY_ENRICHMENT_IN_PROGRESS = False  # Flag to prevent concurrent enrichment threads

EBOOK_EXTENSIONS = {".pdf", ".epub", ".mobi", ".azw", ".azw3", ".prc"}
DIRECT_DL_EXTENSIONS = {"mobi", "prc", "azw", "azw3"}
LIBRARY_SORT_MODES: Dict[str, str] = {
    "date_newest": "Date added (newest first)",
    "date_oldest": "Date added (oldest first)",
    "title_az": "Title A–Z",
    "title_za": "Title Z–A",
    "author_az": "Author A–Z",
    "author_za": "Author Z–A",
}

feed_parser = FeedParser(
    FEED_CACHE_PATH,
    timeout=settings_manager.settings.request_timeout,
    )

# Concurrency configuration
_env_feed_workers = int(os.environ.get("MAX_FEED_WORKERS", "16"))  # Increased from 4 to 16 for better parsing parallelism
_cfg_feed_workers = getattr(settings_manager.settings, "max_feed_workers", 0) or 0
MAX_FEED_WORKERS = _cfg_feed_workers if _cfg_feed_workers > 0 else _env_feed_workers

_cfg_downloads = getattr(settings_manager.settings, "max_concurrent_downloads", 4) or 4  # Use configured value (default 4, Anna's Archive friendly)

# Anna's Archive source with per-process download semaphore
source = AnnaSource(
    base_url="https://annas-archive.se",  # Use .se mirror (updated Jan 2026 - original .org domain is down)
    timeout=settings_manager.settings.request_timeout,
    max_concurrent_downloads=_cfg_downloads,
    cloudflare_lock=cloudflare_lock,
)

# Global executor for background feed jobs
BACKGROUND_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_FEED_WORKERS)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
KINDLE_ATTACHEMENT_LIMIT_BYTES = 20 * 1024 * 1024 #20MB
def is_oversize_for_kindle(path: Path, limit_bytes: int = KINDLE_ATTACHMENT_LIMIT_BYTES) -> bool:
    """
    Return True if the file is likely to exceed Kindle's single-email size limit.
    """
    try:
        return path.stat().st_size > limit_bytes
    except OSError:
        return False

def create_kindle_safe_copy(original_path: Path) -> Optional[Path]:
    """
    Create a temporary copy of the file with spaces removed from the filename.
    Amazon's Kindle service rejects filenames with spaces (E001 error).
    Returns the path to the temp file, or None if copy fails.
    The original file is left untouched with its pretty name in the library.
    """
    try:
        # Remove spaces from filename
        cleaned_name = original_path.name.replace(" ", "")
        if cleaned_name == original_path.name:
            # No spaces, no need for a copy
            return original_path

        # Create temp copy in same directory with cleaned name
        temp_path = original_path.parent / cleaned_name

        # Copy file
        import shutil
        shutil.copy2(original_path, temp_path)
        logger.info("Created Kindle-safe copy: %s -> %s", original_path.name, temp_path.name)
        return temp_path
    except Exception as e:
        logger.warning("Failed to create Kindle-safe copy for %s: %s", original_path.name, e)
        return None


def resolve_download_dir(path_str: str) -> Path:
    """
    Resolve a (possibly relative) download directory to an absolute path
    and ensure it exists.
    """
    path = Path(path_str)
    if not path.is_absolute():
        path = BASE_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path

# ---------------------------------------------------------------------------
# User cleanup on configuration changes
# ---------------------------------------------------------------------------

def cleanup_deleted_users(current_user_names: set) -> None:
    """
    Remove cached data and history for users that have been deleted from settings.
    This keeps the system clean when users are removed from configuration.
    NOTE: User library files are NOT deleted - only cached/system data.

    Args:
        current_user_names: Set of user names currently in settings
    """
    logger.info("Checking for deleted users to clean up (current users: %s)", current_user_names)

    # Track what was cleaned
    cleaned = {
        "feed_cache": 0,
        "history": 0,
        "search_cache": 0,
    }

    # STEP 1: Clean feed cache
    try:
        if FEED_CACHE_PATH.exists():
            with open(FEED_CACHE_PATH, 'r') as f:
                feed_cache = json.load(f)

            original_size = len(feed_cache)
            # Feed cache structure: {feed_url: {item_title: item_data}}
            # We don't track users in feed cache, so no cleanup needed there
            logger.debug("Feed cache has no user-specific data to clean")
    except Exception as e:
        logger.warning("Failed to check feed cache for cleanup: %s", e)

    # STEP 2: Clean history
    try:
        if history_manager.path.exists():
            history_data = history_manager.load()
            if isinstance(history_data, list):
                # Filter out entries for deleted users
                original_count = len(history_data)
                filtered_data = [
                    item for item in history_data 
                    if item.get("user") in current_user_names
                ]

                if len(filtered_data) < original_count:
                    removed_count = original_count - len(filtered_data)
                    logger.info("Removing %d history entries for deleted users", removed_count)

                    # Write back cleaned history
                    with history_manager.lock:
                        with open(history_manager.path, 'w') as f:
                            json.dump(filtered_data, f, indent=2)
                    cleaned["history"] = removed_count
                    logger.info("Cleaned history: removed %d entries", removed_count)
    except Exception as e:
        logger.warning("Failed to clean history: %s", e)

    # STEP 3: Clean search cache
    try:
        if SEARCH_CACHE_PATH.exists():
            with open(SEARCH_CACHE_PATH, 'r') as f:
                search_cache = json.load(f)

            # Search cache structure: {query: {results}}
            # Search cache doesn't track users, but we log for transparency
            logger.debug("Search cache has no user-specific data to clean")
    except Exception as e:
        logger.warning("Failed to check search cache for cleanup: %s", e)

    # Log summary
    if any(cleaned.values()):
        logger.info(
            "Deleted user cleanup complete: removed %d history entries",
            cleaned["history"]
        )
    else:
        logger.debug("No deleted user data found to clean")


# Search cache helpers (disk-backed, used for RSS/HTML feed searches only)
# ---------------------------------------------------------------------------

def _load_search_cache() -> Dict[str, Dict]:
    """Load search cache from disk once into _SEARCH_CACHE."""
    global _SEARCH_CACHE_LOADED, _SEARCH_CACHE
    if _SEARCH_CACHE_LOADED:
        return _SEARCH_CACHE

    with search_cache_lock:
        # Double-check after acquiring lock
        if _SEARCH_CACHE_LOADED:
            return _SEARCH_CACHE
            
        if SEARCH_CACHE_PATH.exists():
            try:
                _SEARCH_CACHE = json.loads(SEARCH_CACHE_PATH.read_text())
            except Exception:
                logger.exception("Failed to load search cache from %s", SEARCH_CACHE_PATH)
                _SEARCH_CACHE = {}
        else:
            _SEARCH_CACHE = {}

        _SEARCH_CACHE_LOADED = True
    return _SEARCH_CACHE


def _save_search_cache() -> None:
    """Persist _SEARCH_CACHE to disk."""
    if not _SEARCH_CACHE_LOADED:
        return
    
    with search_cache_lock:
        try:
            SEARCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Write to temp file first, then atomic rename to prevent corruption
            import tempfile
            temp_fd, temp_path = tempfile.mkstemp(dir=SEARCH_CACHE_PATH.parent, text=True)
            try:
                with os.fdopen(temp_fd, 'w') as f:
                    json.dump(_SEARCH_CACHE, f, indent=2)
                # Atomic rename to prevent partial writes
                os.replace(temp_path, SEARCH_CACHE_PATH)
            except Exception:
                # Clean up temp file if write failed
                try:
                    os.unlink(temp_path)
                except:
                    pass
                raise
        except Exception:
            logger.exception("Failed to save search cache to %s", SEARCH_CACHE_PATH)


def search_with_cache(
    query: str,
    options: SearchOptions,
    persist: bool,
) -> Tuple[List[Dict], List[str]]:
    """
    Proxy around AnnaSource.search.

    - If persist=True (RSS/HTML feeds):
        * Use a disk-backed cache keyed by normalized query.
        * Short-circuit to cached results when available.
        * Persist new results to SEARCH_CACHE_PATH.
    - If persist=False (manual UI search):
        * Just call AnnaSource.search; no disk writes/reads.
    """
    # DEBUG: Log the query parameter with repr to see exact characters
    logger.debug(
        "search_with_cache called with query (repr)=%r options.query (repr)=%r persist=%s",
        query,
        options.query if options else None,
        persist,
    )

    cache_key = (options.query or query or "").strip().lower()
    debug_log: List[str] = []

    if persist:
        with _SEARCH_CACHE_LOCK:
            cache = _load_search_cache()
            entry = cache.get(cache_key)
            if isinstance(entry, dict) and "results" in entry:
                cached_results = entry.get("results") or []
                debug_log.append(
                    f"Disk search cache hit for query: {options.query or query}"
                )
                # Return a shallow copy so callers don't mutate the shared cache
                return list(cached_results), debug_log

     # Check if item is already in library before searching
    # Extract title and author from query to match against library
    with library_cache_lock:
        q = options.query or query or ""
        cache_size = len(_LIBRARY_LOOKUP_CACHE)

        if cache_size > 0:
            # Try exact match first
            if q.lower() in _LIBRARY_LOOKUP_CACHE:
                logger.debug(f"Library cache hit (exact): {q}")
                debug_log.append(f"Item already in library (exact): {q}")
                return [], debug_log

            # Try normalized match
            if "-" in q:
                parts = q.split("-", 1)
                q_title = parts[0].strip().lower()
                q_author_raw = parts[1].strip() if len(parts) > 1 else ""
                q_author = history_manager.cleanup_author(q_author_raw).lower()

                for lib_title, lib_author in _LIBRARY_LOOKUP_CACHE:
                    if lib_title == q_title and lib_author == q_author:
                        logger.debug(f"Library cache hit (normalized): {q}")
                        debug_log.append(f"Item already in library (normalized): {q}")
                        return [], debug_log

    # No disk hit (or persist=False): do a live search
    results, search_debug = source.search(query, options=options)
    debug_log.extend(search_debug)

    # Persist to disk only for feed-driven searches
    if persist and results:
        with _SEARCH_CACHE_LOCK:
            cache = _load_search_cache()
            cache[cache_key] = {"results": results}
            _save_search_cache()

    return results, debug_log
def select_best_result(
    results: List[Dict],
    allowed_formats: List[str],
    kindle_type: str,
    expected_title: Optional[str] = None,
    expected_author: Optional[str] = None,
) -> Optional[Dict]:
    """
    Pick the "best" search result.

    Heuristic:
      * Filter out study guides (case-insensitive "studyguide" or "study guide" in title)
      * Prefer results that have at least one of allowed_formats.
      * Among them, prefer non-PDF for e-ink devices.
      * Prefer results whose title matches the expected title.
      * Strongly prefer results whose author matches the expected author.
      * Fall back to the first result if all else fails.
    """
    # Filter out study guides first
    filtered_results = []
    for result in results:
        title_lower = (result.get("title") or "").lower()
        # English-focused filtering: skip non-English content and study guides
        if "studyguide" in title_lower or "study guide" in title_lower:
            logger.debug("Filtering out study guide: %s", result.get("title"))
            continue

        # Additional English language preference: prioritize clear English titles
        title_text = result.get("title", "")
        # Skip if title contains non-English characters or patterns
        non_english_patterns = [
            r'[\u4e00-\u9fff]',  # Full CJK blocks
            r'[あ-ゟ]',  # Hiragana/Katakana range
            r'[а-я]',   # Cyrillic range  
            r'[공-힣]',  # Korean Hangul range
        ]
        if any(re.search(pattern, title_text) for pattern in non_english_patterns):
            logger.debug("Filtering non-English title: %s", title_text)
            continue

        filtered_results.append(result)

    if not filtered_results:
        # If all results were filtered, return None
        logger.warning("All results filtered out (study guides)")
        return None

    results = filtered_results
    allowed = [f.lower() for f in (allowed_formats or [])]

    def tokens(text: str) -> set[str]:
        if not text:
            return set()
        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return {t for t in text.split() if t}

    expected_title_tokens = tokens(expected_title) if expected_title else set()
    expected_author_tokens = tokens(expected_author) if expected_author else set()

    def score(result: Dict) -> int:
        formats = [f.lower() for f in result.get("formats", [])]
        has_allowed = any(f in formats for f in allowed) if allowed else bool(formats)
        is_pdf_only = formats and all(f == "pdf" for f in formats)

        score_val = 0

        # Base format scoring
        if has_allowed:
            score_val += 10
        if not is_pdf_only and kindle_type.lower() in {"paperwhite", "oasis", "voyage"}:
            score_val += 5
        score_val += len(formats)

        # STRICT SHORT TITLE MATCHING:
        # If title has <3 tokens (e.g., "The Gift"), require author to match
        # and heavily weight author match in ranking
        if expected_title_tokens and len(expected_title_tokens) < 3:
            # Short title - MUST have author match to score well, author determines ranking
            if expected_author_tokens:
                atoks = tokens(result.get("author") or "")
                if atoks:
                    # Calculate author overlap percentage
                    common_a = expected_author_tokens & atoks
                    author_overlap = len(common_a) / max(1, len(expected_author_tokens))

                    if author_overlap > 0:
                        # Author has some match - give strong bonus based on overlap %
                        score_val += int(round(author_overlap * 100))  # 0-100 points based on match
                    else:
                        # Author mismatch on short title - very strong penalty
                        score_val -= 500
                else:
                    # Result has no author info - can't verify on short title
                    score_val -= 500
            else:
                # Short title but no author provided - can't safely match
                score_val -= 500
        else:
            # Title similarity (for longer titles)
            if expected_title_tokens:
                rtoks = tokens(result.get("title") or "")
                if rtoks:
                    common = expected_title_tokens & rtoks
                    if common:
                        overlap = len(common) / max(1, len(expected_title_tokens))
                        score_val += int(round(overlap * 10))  # up to +10
                    else:
                        score_val -= 5  # no shared title tokens -> mild penalty

            # Author similarity - strong weighting when author provided
            if expected_author_tokens:
                atoks = tokens(result.get("author") or "")
                if atoks:
                    common_a = expected_author_tokens & atoks
                    if common_a:
                        author_overlap = len(common_a) / max(1, len(expected_author_tokens))
                        score_val += int(round(author_overlap * 50))  # 0-50 points based on overlap
                    else:
                        # Author mismatch: heavily penalize
                        score_val -= 100
                else:
                    # Result has no author info - neutral for matching
                    score_val -= 5

        return score_val

    if not results:
        return None

    # If we have an expected author, filter to only results with matching authors
    if expected_author_tokens:
        author_matched = []
        for result in results:
            atoks = tokens(result.get("author") or "")
            if atoks:
                common_a = expected_author_tokens & atoks
                if common_a:
                    author_matched.append(result)

        # If we found results with matching authors, use only those
        if author_matched:
            results = author_matched
            logger.debug("Filtered to %d results with matching author: %s", len(results), expected_author)
        else:
            # No results with matching author - log warning but continue
            logger.warning("No search results with matching author '%s' - will use best available match", expected_author)
    
    # If no author was explicitly provided but we have an expected title,
    # filter to results with at least some title match to avoid wrong books
    elif expected_title_tokens and not expected_author_tokens:
        # Only apply this filter if we have multiple results to choose from
        if len(results) > 1:
            title_matched = []
            for result in results:
                rtoks = tokens(result.get("title") or "")
                if rtoks:
                    common = expected_title_tokens & rtoks
                    if common:
                        # At least one significant token from title matches
                        title_matched.append(result)
            
            # If we found results with title matches, use only those
            if title_matched:
                results = title_matched
                logger.debug("Filtered to %d results with matching title (no explicit author provided): %s", len(results), expected_title)
            else:
                # No title matches - log warning but continue
                logger.warning("No search results with matching title '%s' (and no explicit author provided) - will use best available match", expected_title)

    best_result = max(results, key=score)

    fmts = [f.lower() for f in best_result.get("formats", [])]
    for f in allowed or []:
        if f.lower() in fmts:
            best_result["selected_format"] = f.lower()
            break
    else:
        if fmts:
            best_result["selected_format"] = fmts[0]

    return best_result

def strip_html_tags(text: str) -> str:
    """
    Very simple HTML tag stripper for email bodies.
    """
    if not text:
        return ""
    # Remove tags like <br>, <p>, <div ...>, </a>, etc.
    return re.sub(r"<[^>]+>", "", text)


def get_file_format(path: Path) -> str:
    """Get the file format from path suffix (lowercase, without dot)."""
    return path.suffix.lower().lstrip(".")

def is_goodreads_image(url: str) -> bool:
    """Check if URL is from Goodreads only"""
    if not url:
        return False
    url_lower = url.lower()
    # Only Goodreads images
    return 'goodreads' in url_lower or 'gr-assets' in url_lower

def normalize_cover_url(raw: str) -> str:
    """
    Normalize cover URL - remove duplicates and extract valid URLs.
    Handles cases where URL got duplicated (e.g., "url1url1" or "url1url2")
    NEVER returns Anna's Archive or piracy site URLs - only Goodreads and legitimate sources.
    """
    if not raw:
        return ""
    cover = raw.strip()

    # Reject problematic sources entirely
    forbidden_domains = [
        "cdn-zlib", "zlib.sk", "z-lib", "libgen", "anna", "annas-archive",
        "bookfi", "b-ok", "manybooks"
    ]
    cover_lower = cover.lower()
    if any(domain in cover_lower for domain in forbidden_domains):
        return ""

    # If multiple http/https present, take first one only
    if cover.startswith(("http://", "https://")):
        second_http_idx = cover.find("http", 8)  # search after "https://"
        if second_http_idx != -1:
            cover = cover[:second_http_idx].strip()
            if cover and cover.startswith(("http://", "https://")):
                # Final check: reject forbidden domains in the cleaned URL
                if any(domain in cover.lower() for domain in forbidden_domains):
                    return ""
                return cover
    # Use regex to extract a valid URL if present
    match = re.search(r"https?://[^\s]+", cover)
    if match:
        url = match.group(0)
        # Final check: reject forbidden domains in the extracted URL
        if any(domain in url.lower() for domain in forbidden_domains):
            return ""
        return url
    return ""

def fix_description_spacing(text: str) -> str:
    """
    Insert spaces after periods where they're missing (e.g., "end.Another" -> "end. Another").
    This handles common formatting issues in descriptions.
    """
    if not text:
        return text

    # Replace period followed directly by capital letter with period + space + capital letter
    # This handles cases like "word.Word" or "end.Another"
    text = re.sub(r'\.([A-Z])', r'. \1', text)

    return text


def sanitize_author(author_string: str) -> str:
    """
    Sanitize author string: remove duplicated author names.
    Example: "Timothy Roland Timothy Roland" → "Timothy Roland"
    Example: "Caroline Peckham & Susanne Valenti Susanne Valenti" → "Caroline Peckham & Susanne Valenti"
    Example: "Holly Beth Walker Holly Beth Walker" → "Holly Beth Walker"
    Example: "Author1, Author2, Author1" → "Author1, Author2"
    """
    if not author_string:
        return ""

    author_string = author_string.strip()
    if not author_string:
        return ""

    # Split on known delimiters first
    delimiter = None
    parts = []
    for sep in [" & ", " and ", "; ", ","]:
        if sep in author_string:
            delimiter = sep
            parts = [p.strip() for p in author_string.split(sep) if p.strip()]
            break

    # If we found delimited parts, deduplicate each part internally
    if parts:
        # Remove duplicates across parts
        seen = set()
        unique_parts = []
        for part in parts:
            # Also check if part itself has internal duplication (e.g., "Name Name")
            words = part.split()
            if len(words) > 1:
                mid = len(words) // 2
                first_half = " ".join(words[:mid])
                second_half = " ".join(words[mid:])
                if first_half.lower() == second_half.lower():
                    part = first_half

            part_lower = part.lower()
            if part_lower not in seen:
                seen.add(part_lower)
                unique_parts.append(part)

        # Rejoin with original delimiter
        if delimiter:
            return delimiter.join(unique_parts)
        else:
            return "".join(unique_parts)

    # No delimiters found, check for space-separated repeated names
    words = author_string.split()
    if len(words) > 1:
        # Check if the string is a repeated author name (e.g., "Author Author")
        mid = len(words) // 2
        first_half = " ".join(words[:mid])
        second_half = " ".join(words[mid:])
        if first_half.lower() == second_half.lower():
            return first_half

    # No duplication found, return as-is
    return author_string


def cache_cover_locally(cover_url: str, library_id: str) -> Optional[Path]:
    """
    Download and cache a Goodreads cover image locally.
    Returns the path to the cached cover file, or None if download fails.
    """
    if not cover_url or not is_goodreads_image(cover_url):
        return None

    # Use library_id as filename to create unique cache
    cache_filename = hashlib.md5(library_id.encode()).hexdigest()
    cache_file = COVERS_DIR / f"{cache_filename}.jpg"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0",
            "Referer": "https://www.goodreads.com/",
            "Accept": "image/*,*/*;q=0.8"
        }
        resp = requests.get(cover_url, timeout=5, headers=headers, allow_redirects=True)

        if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", "").lower():
            # Determine actual extension
            content_type = resp.headers.get("Content-Type", "image/jpeg").lower()
            if "png" in content_type:
                cache_file = COVERS_DIR / f"{cache_filename}.png"
            elif "webp" in content_type:
                cache_file = COVERS_DIR / f"{cache_filename}.webp"
            elif "gif" in content_type:
                cache_file = COVERS_DIR / f"{cache_filename}.gif"

            # Write cached file
            try:
                cache_file.write_bytes(resp.content)
                logger.debug("Cached cover for %s at %s (%d bytes)", library_id, cache_file.name, len(resp.content))
                return cache_file
            except OSError as e:
                logger.warning("Failed to write cover cache for %s to disk: %s", library_id, e)
    except Exception as e:
        logger.debug("Failed to cache cover for %s: %s", library_id, e)

    return None


def get_cover_for_email(
    file_path: Optional[Path] = None,
    cover_url: Optional[str] = None,
    title: str = "Book",
    library_id: Optional[str] = None
) -> Tuple[Optional[bytes], str]:
    """
    Get cover image data for email display, trying multiple sources.

    Priority:
    1. Check local cache (if library_id provided)
    2. Extract from ebook file (if path provided and file exists)
    3. Download from URL (if cover_url provided)
    4. Return None if none available

    Returns:
        Tuple of (image_bytes, mime_type) or (None, "image/jpeg") if not found
    """
    # Try checking local cache first
    if library_id:
        for ext in ["jpg", "png", "webp", "gif"]:
            cache_file = COVERS_DIR / f"{library_id}.{ext}"
            if cache_file.exists():
                try:
                    mime_type = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    logger.debug("Using cached cover for %s from %s", title, cache_file.name)
                    return cache_file.read_bytes(), mime_type
                except Exception as e:
                    logger.debug("Failed to read cached cover %s: %s", cache_file.name, e)

    # Try extracting from ebook file
    if file_path and file_path.exists():
        try:
            metadata = extract_book_metadata(file_path)
            if metadata.get('cover_image'):
                mime = "image/jpeg"
                if metadata['cover_format'] == 'png':
                    mime = "image/png"
                elif metadata['cover_format'] == 'gif':
                    mime = "image/gif"
                logger.debug("Using extracted cover from %s for email", file_path.name)
                return metadata['cover_image'], mime
        except Exception as e:
            logger.debug("Failed to extract cover from %s: %s", file_path, e)

    # Try downloading from URL - from allowed sources only
    if cover_url and is_goodreads_image(cover_url):
        try:
            # Add browser-like headers to avoid 403 blocks
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0",
                "Referer": "https://www.goodreads.com/",
                "Accept": "image/*,*/*;q=0.8"
            }
            resp = requests.get(cover_url, timeout=5, headers=headers, allow_redirects=True)
            logger.debug("Cover download response: status=%d, content-type=%s", resp.status_code, resp.headers.get("Content-Type", ""))
            if resp.status_code == 200 and "image" in resp.headers.get("Content-Type", "").lower():
                mime = "image/jpeg"
                if "png" in resp.headers.get("Content-Type", "").lower():
                    mime = "image/png"
                elif "webp" in resp.headers.get("Content-Type", "").lower():
                    mime = "image/webp"
                logger.debug("Downloaded cover from %s for email", cover_url)
                return resp.content, mime
            else:
                logger.debug("Cover download failed: status=%d for %s", resp.status_code, cover_url)
        except Exception as e:
            logger.debug("Failed to download cover from %s: %s", cover_url, e)

    return None, "image/jpeg"


def queue_library_addition_notification(user: UserSettings, entry: Dict) -> None:
    """
    Queue a library addition for batch notification email.
    Batches up to 200 items and sends after 600 seconds (10 minutes) of no new additions.
    Uses global notification_emails from settings or per-user notification_email.
    """
    # Get list of emails to send to (global first, fall back to per-user)
    emails = []
    settings = settings_manager.settings

    if settings.notification_emails:
        # Parse comma-separated emails from global settings
        emails = [e.strip() for e in settings.notification_emails.split(",") if e.strip()]

    if not emails and user.notification_email:
        # Fallback to single per-user notification_email
        emails = [user.notification_email]

    if not emails or not settings.smtp.is_configured():
        return

    with library_queue_lock:
        if user.name not in library_addition_queue:
            library_addition_queue[user.name] = []

        library_addition_queue[user.name].append(entry)
        queue_size = len(library_addition_queue[user.name])

        # Cancel existing timer
        if user.name in library_queue_timers:
            library_queue_timers[user.name].cancel()

        # Send immediately if we hit 200 items
        if queue_size >= 200:
            entries = library_addition_queue[user.name]
            library_addition_queue[user.name] = []
            if user.name in library_queue_timers:
                del library_queue_timers[user.name]

            logger.info("Library queue reached 200 items, sending batch for user=%s", user.name)
            # Send in background to avoid blocking
            threading.Thread(
                target=lambda: send_batch_notification_email(
                    settings_manager.settings.smtp,
                    user,
                    entries,
                    sent_to_kindle=False
                ),
                daemon=True,
                name=f"library-notification-{user.name}"
            ).start()
        else:
            # Set 600-second (10 minute) timer to flush queue
            timer = threading.Timer(
                600.0,
                lambda: flush_library_queue(user.name)
            )
            timer.daemon = True
            timer.start()
            library_queue_timers[user.name] = timer
            logger.debug("Queued library addition for user=%s (queue_size=%d)", user.name, queue_size)


def flush_library_queue(user_name: str) -> None:
     """
     Flush the library addition queue for a user if it has items.
     Called after 600 seconds (10 minutes) of no new additions.
     """
     with library_queue_lock:
         if user_name not in library_addition_queue or not library_addition_queue[user_name]:
             return

         entries = library_addition_queue[user_name]
         library_addition_queue[user_name] = []
         if user_name in library_queue_timers:
             del library_queue_timers[user_name]

         logger.info("Flushing library queue for user=%s with %d items", user_name, len(entries))

         # Find the user object
         user = next((u for u in settings_manager.settings.users if u.name == user_name), None)
         if not user:
             return

         # Check if notifications are configured (global or per-user)
         settings = settings_manager.settings
         has_notification_emails = (
             (settings.notification_emails and any(e.strip() for e in settings.notification_emails.split(","))) or
             user.notification_email
         )
         if not has_notification_emails or not settings.smtp.is_configured():
             return

         # Send in background to avoid blocking
         threading.Thread(
             target=lambda: send_batch_notification_email(
                 settings_manager.settings.smtp,
                 user,
                 entries,
                 sent_to_kindle=False
             ),
             daemon=True,
             name=f"library-notification-flush-{user_name}"
         ).start()


def queue_kindle_auto_send(user: UserSettings, file_path: Path, result: Dict) -> None:
    """
    Queue a file for batch Kindle auto-send (25 files, 24MB limit).
    Ensures file is converted to EPUB before queueing.
    Checks library and history for duplicates (by filename without extension).
    """
    logger.info("queue_kindle_auto_send called: user=%s, file=%s", user.name, file_path.name)
    # Check for Kindle email: global first, then per-user
    kindle_email = None
    settings = settings_manager.settings
    if settings.kindle_emails:
        kindle_emails_list = [e.strip() for e in settings.kindle_emails.split(",") if e.strip()]
        if kindle_emails_list:
            kindle_email = kindle_emails_list[0]  # Use first global email
    if not kindle_email and user.kindle_email:
        kindle_email = user.kindle_email

    if not kindle_email or not settings_manager.settings.smtp.is_configured():
        return

    # Check if file already exists in library or history (by filename without extension)
    base_name = file_path.stem  # filename without extension

    # Check library entries
    entries = build_library_entries()
    for entry in entries:
        lib_path = Path(entry.get("root", "")) / entry.get("relpath", "")
        if lib_path.stem.lower() == base_name.lower():
            logger.info("Skipping Kindle queue: file already in library: %s", file_path.name)
            return

    # Check history
    try:
        with history_lock:
            if history_manager.has_file(user.name, base_name):
                logger.info("Skipping Kindle queue: file already in history: %s", file_path.name)
                return
    except Exception:
        pass  # If history check fails, proceed

    # Ensure file is in EPUB format
    fmt = file_path.suffix.lower().lstrip(".")
    file_to_send = file_path

    if fmt != "epub":
        logger.info("Converting %s to EPUB for Kindle queueing", file_path.name)
        temp_dir = DATA_DIR / "temp"
        temp_dir.mkdir(exist_ok=True)
        temp_epub = temp_dir / f"{file_path.stem}_{uuid.uuid4().hex[:8]}.epub"

        try:
            file_to_send = convert_to_epub(file_path, temp_epub)
            logger.info("Converted %s to %s for Kindle", file_path.name, file_to_send.name)
        except Exception as e:
            logger.error("Failed to convert %s to EPUB: %s, skipping Kindle send", file_path.name, e)
            return

    with kindle_queue_lock:
        if user.name not in kindle_queue:
            kindle_queue[user.name] = []

        kindle_queue[user.name].append((file_to_send, result))
        queue_size = len(kindle_queue[user.name])

        logger.debug("Queued file for Kindle send: user=%s queue_size=%d", user.name, queue_size)

        # Check if we should flush (25 files or would exceed 24MB)
        total_size = 0
        for p, _ in kindle_queue[user.name]:
            try:
                total_size += p.stat().st_size
            except OSError:
                logger.warning("File disappeared from Kindle queue: %s", p)
                continue
        if queue_size >= 25 or total_size > (24 * 1024 * 1024):
            flush_kindle_queue(user.name)


def flush_kindle_queue(user_name: str) -> None:
    """
    Flush the Kindle auto-send queue for a user if it has items.
    Deduplicates by filename (without extension) against library and history.
    """
    with kindle_queue_lock:
        if user_name not in kindle_queue or not kindle_queue[user_name]:
            logger.debug("Kindle queue flush: nothing to flush for user=%s", user_name)
            return

        files_to_send = []
        skipped = []

        # Deduplicate against library and history
        entries = build_library_entries()
        lib_stems = set((Path(e.get("root", "")) / e.get("relpath", "")).stem.lower() 
                       for e in entries)

        for file_path, result in kindle_queue[user_name]:
            base_name = file_path.stem.lower()

            # Check if already in library
            if base_name in lib_stems:
                logger.debug("Deduplicating: %s already in library", file_path.name)
                skipped.append(file_path.name)
                continue

            # Check history
            try:
                with history_lock:
                    if history_manager.has_file(user_name, file_path.stem):
                        logger.debug("Deduplicating: %s already in history", file_path.name)
                        skipped.append(file_path.name)
                        continue
            except Exception:
                pass  # If history check fails, include the file

            files_to_send.append((file_path, result))

        if skipped:
            logger.info("Deduplicating Kindle queue: skipped %d files already in library/history", len(skipped))

        if not files_to_send:
            kindle_queue[user_name] = []
            return

        files = files_to_send
        kindle_queue[user_name] = []

        logger.info("Flushing Kindle queue for user=%s with %d files", user_name, len(files))

        # Find the user object
        user = next((u for u in settings_manager.settings.users if u.name == user_name), None)
        if not user:
            return

        # Check for Kindle email: global first, then per-user
        kindle_email = None
        settings = settings_manager.settings
        if settings.kindle_emails:
            kindle_emails_list = [e.strip() for e in settings.kindle_emails.split(",") if e.strip()]
            if kindle_emails_list:
                kindle_email = kindle_emails_list[0]  # Use first global email
        if not kindle_email and user.kindle_email:
            kindle_email = user.kindle_email

        if not kindle_email:
            return

        # Send in background to avoid blocking
        threading.Thread(
            target=lambda: send_kindle_batch_email(
                settings_manager.settings.smtp,
                user,
                files,
                kindle_email=kindle_email
            ),
            daemon=True,
            name=f"kindle-batch-{user_name}"
        ).start()


def send_kindle_auto_send_immediately(
    user: UserSettings, 
    file_path: Path, 
    result: Dict,
    smtp_config = None
) -> bool:
    """
    Send a single file to Kindle immediately for feed autosend.
    Sends synchronously (in calling thread), records send in history.
    Returns True if successful, False otherwise.

    This is used for feed autosend items to allow immediate troubleshooting.
    Compared to queue_kindle_auto_send which batches items.
    """
    if not smtp_config:
        smtp_config = settings_manager.settings.smtp

    # Determine Kindle email
    kindle_email = user.kindle_email
    if not kindle_email:
        settings = settings_manager.settings
        if settings.kindle_emails:
            kindle_emails_list = [e.strip() for e in settings.kindle_emails.split(",") if e.strip()]
            if kindle_emails_list:
                kindle_email = kindle_emails_list[0]

    if not kindle_email or not smtp_config.is_configured():
        logger.warning("Cannot send to Kindle for %s: no Kindle email or SMTP configured", user.name)
        return False

    logger.info("Sending %s to Kindle immediately for user %s", file_path.name, user.name)

    # Convert to EPUB if needed
    file_to_send = file_path
    temp_file_to_cleanup = None

    try:
        fmt = file_path.suffix.lower().lstrip(".")

        # Layer 3a: Validate format is convertible for Kindle send
        CONVERTIBLE_FORMATS = {"epub", "mobi", "azw", "azw3", "pdf", "txt"}
        if fmt not in CONVERTIBLE_FORMATS:
            logger.warning("Skipping Kindle send: format %s not convertible for Kindle (file=%s)", fmt, file_path.name)
            return False

        if fmt != "epub":
            logger.info("Converting %s to EPUB for Kindle", file_path.name)
            temp_dir = DATA_DIR / "temp"
            temp_dir.mkdir(exist_ok=True)
            temp_epub = temp_dir / f"{file_path.stem}_{uuid.uuid4().hex[:8]}.epub"

            try:
                file_to_send = convert_to_epub(file_path, temp_epub)
                temp_file_to_cleanup = temp_epub

                # Layer 3b: Verify conversion actually succeeded
                if not temp_epub.exists():
                    logger.error("Conversion failed: output file missing for %s", file_path.name)
                    return False
                if temp_epub.stat().st_size == 0:
                    logger.error("Conversion failed: output file is empty for %s", file_path.name)
                    return False

                logger.info("Converted to %s for Kindle", file_to_send.name)
            except Exception as e:
                logger.error("Failed to convert %s to EPUB: %s - SKIPPING KINDLE SEND", file_path.name, e)
                return False

        # Build email
        msg = EmailMessage()
        msg["From"] = smtp_config.from_email
        msg["To"] = kindle_email
        msg["Subject"] = result.get("title", file_path.name)

        body_lines = [
            f"Here is your book: {result.get('title', file_path.name)}",
            "",
            "Sent via CodexBooks feeder.",
        ]
        msg.set_content("\n".join(body_lines))

        # Attach file
        with file_to_send.open("rb") as f:
            data = f.read()
        msg.add_attachment(
            data,
            maintype="application",
            subtype="octet-stream",
            filename=file_to_send.name,
        )

        # Send email
        try:
            smtp_config.send(msg)
            logger.info("Sent Kindle email to %s for %s", kindle_email, file_path.name)

            # Record in history immediately
            try:
                with history_lock:
                    history_manager.record_kindle_send(
                        user.name,
                        result.get("title", file_path.stem),
                        result.get("author", ""),
                        kindle_email
                    )
                logger.info("Recorded Kindle send in history for %s", file_path.name)
            except Exception as e:
                logger.error("Failed to record Kindle send in history: %s", e)
                return False

            return True
        except Exception as e:
            logger.error("Failed to send Kindle email for %s: %s", file_path.name, e)
            return False
    finally:
        if temp_file_to_cleanup and temp_file_to_cleanup.exists():
            try:
                temp_file_to_cleanup.unlink()
            except Exception:
                logger.warning("Failed to clean up temp file %s", temp_file_to_cleanup)



def send_library_item_to_kindle(
    user: UserSettings,
    item_title: str,
    item_author: str,
    library_file_path: Path,
    smtp_config = None
) -> bool:
    """
    Send an existing library item to Kindle.
    Used for back-filling Kindle sends for items from auto-send feeds.

    Args:
        user: User to send to
        item_title: Title of the item
        item_author: Author of the item
        library_file_path: Path to the file in the library
        smtp_config: SMTP config (uses global if not provided)

    Returns:
        True if successfully sent and recorded, False otherwise
    """
    if not smtp_config:
        smtp_config = settings_manager.settings.smtp

    # Determine Kindle email
    kindle_email = user.kindle_email
    if not kindle_email:
        settings = settings_manager.settings
        if settings.kindle_emails:
            kindle_emails_list = [e.strip() for e in settings.kindle_emails.split(",") if e.strip()]
            if kindle_emails_list:
                kindle_email = kindle_emails_list[0]

    if not kindle_email or not smtp_config.is_configured():
        logger.warning("Cannot send library item to Kindle for %s: no Kindle email or SMTP configured", user.name)
        return False

    if not library_file_path.exists():
        logger.warning("Library file not found: %s (for %s)", library_file_path, item_title)
        return False

    logger.info("Sending library item to Kindle: %s by %s (file=%s, user=%s)", item_title, item_author, library_file_path.name, user.name)

    # Convert to EPUB if needed
    file_to_send = library_file_path
    temp_file_to_cleanup = None

    try:
        fmt = library_file_path.suffix.lower().lstrip(".")

        # Validate format is convertible for Kindle send
        CONVERTIBLE_FORMATS = {"epub", "mobi", "azw", "azw3", "pdf", "txt"}
        if fmt not in CONVERTIBLE_FORMATS:
            logger.warning("Skipping Kindle send: format %s not convertible for Kindle (file=%s)", fmt, library_file_path.name)
            return False

        if fmt != "epub":
            logger.info("Converting %s to EPUB for Kindle (library item)", library_file_path.name)
            temp_dir = DATA_DIR / "temp"
            temp_dir.mkdir(exist_ok=True)
            temp_epub = temp_dir / f"{library_file_path.stem}_{uuid.uuid4().hex[:8]}.epub"

            try:
                file_to_send = convert_to_epub(library_file_path, temp_epub)
                temp_file_to_cleanup = temp_epub

                # Verify conversion succeeded
                if not temp_epub.exists():
                    logger.error("Conversion failed: output file missing for %s", library_file_path.name)
                    return False
                if temp_epub.stat().st_size == 0:
                    logger.error("Conversion failed: output file is empty for %s", library_file_path.name)
                    return False

                logger.info("Converted %s to EPUB for Kindle", file_to_send.name)
            except Exception as e:
                logger.error("Failed to convert %s to EPUB: %s - SKIPPING KINDLE SEND", library_file_path.name, e)
                return False

        # Build email
        msg = EmailMessage()
        msg["From"] = smtp_config.from_email
        msg["To"] = kindle_email
        msg["Subject"] = item_title

        body_lines = [
            f"Here is your book: {item_title}",
            f"by {item_author}",
            "",
            "Sent via CodexBooks feeder (library item).",
        ]
        msg.set_content("\n".join(body_lines))

        # Attach file
        with file_to_send.open("rb") as f:
            data = f.read()
        msg.add_attachment(
            data,
            maintype="application",
            subtype="octet-stream",
            filename=file_to_send.name,
        )

        # Send email
        try:
            smtp_config.send(msg)
            logger.info("Sent Kindle email to %s for library item: %s", kindle_email, item_title)

            # Record in history immediately
            try:
                with history_lock:
                    history_manager.record_kindle_send(
                        user.name,
                        item_title,
                        item_author,
                        kindle_email
                    )
                logger.info("Recorded Kindle send in history for library item: %s", item_title)
            except Exception as e:
                logger.error("Failed to record Kindle send in history for library item: %s", e)
                return False

            return True
        except Exception as e:
            logger.error("Failed to send Kindle email for library item %s: %s", item_title, e)
            return False
    finally:
        if temp_file_to_cleanup and temp_file_to_cleanup.exists():
            try:
                temp_file_to_cleanup.unlink()
            except Exception:
                logger.warning("Failed to clean up temp file %s", temp_file_to_cleanup)


from ebook_metadata_extractor import convert_to_epub
from cover_cache_manager import get_cache_manager
from settings_manager import filter_genres, is_genre_allowed
from goodreads_scraper import scrape_genre_lists, scrape_list_detail
feed_progress_lock = Lock()
metadata_progress_lock = Lock()
cloudflare_lock = Lock()  # Serialize Cloudflare challenge resolution across threads
library_cache_lock = Lock()
_LIBRARY_LOOKUP_CACHE = set()  # Global cache of (title, author) tuples already in library

feed_progress_state = {
    "run_id": None,            # uuid4 hex string for current run
    "active": False,

    # overall
    "overall": {
        "total_items": 0,
        "completed_items": 0,
        "start_time": None,    # float seconds (time.time())
        "eta_seconds": None,   # computed
    },

    # per feed: key -> dict
    # key could be f"{user.name}::{feed.url}"
    "feeds": {
        # "user::feed_url": {
        #     "user": "...",
        #     "feed_url": "...",
        #     "feed_mode": "rss/html/...",
        #     "label": "nick: Goodreads to-read", # nice display
        #     "total_items": 0,
        #     "completed_items": 0,
        #     "start_time": None,
        #     "eta_seconds": None,
        #     "active": True/False,
        # }
    },
}

metadata_progress_state = {
    "active": False,
    "total_books": 0,
    "completed_books": 0,
    "start_time": None,
    "eta_seconds": None,
    "percentage": 0,
    "type": None,  # "manual" or "background"
    "current_book": "",
    "current_step": "",
}

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret-key")

def sanitize_filename_for_kindle(filename: str) -> str:
    """
    Sanitize filename for Amazon Kindle compatibility.

    Removes or replaces special characters that cause E999 errors:
    - Semicolon (;) - replaced with comma
    - Other problematic chars (: < > | ? * /) - replaced with dash

    Amazon Kindle's E999 "Internal Error" occurs when email attachments
    contain certain special characters that the system cannot process.

    Returns the sanitized filename with the same extension preserved.
    """
    if not filename:
        return filename

    # Get extension
    parts = filename.rsplit(".", 1)
    name = parts[0]
    ext = f".{parts[1]}" if len(parts) > 1 else ""

    # Replace problematic characters
    # Semicolon -> comma (most common issue)
    sanitized = name.replace(";", ",")
    # Colons -> dash
    sanitized = sanitized.replace(":", "-")
    # Other problematic chars
    for char in ['<', '>', '|', '?', '*', '/']:
        sanitized = sanitized.replace(char, '-')

    # Remove duplicate spaces/dashes that might have been created
    while "  " in sanitized:
        sanitized = sanitized.replace("  ", " ")
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")

    return sanitized + ext

def send_kindle_email(
    smtp_config,
    user: UserSettings,
    saved_path: Path,
    result: Dict,
):
    """
    Email the downloaded file directly to the user's Kindle address.
    Automatically converts non-EPUB formats to EPUB for Kindle compatibility.

    Uses global kindle_emails setting if available, falls back to per-user kindle_email.
    """
    # Determine the email address to use
    kindle_email = None
    if user.kindle_email:
        kindle_email = user.kindle_email
    else:
        # Try global setting
        settings = settings_manager.settings
        if settings.kindle_emails:
            kindle_emails_list = [e.strip() for e in settings.kindle_emails.split(",") if e.strip()]
            if kindle_emails_list:
                kindle_email = kindle_emails_list[0]  # Use first global email

    if not kindle_email or not smtp_config.is_configured():
        return

    # Convert to EPUB if needed
    file_to_send = saved_path
    temp_file_to_cleanup = None

    try:
        fmt = saved_path.suffix.lower().lstrip(".")
        logger.info(f"send_kindle_email: File format is {fmt}")

        if fmt != "epub":
            logger.info(f"send_kindle_email: Converting {fmt} to EPUB")
            temp_dir = DATA_DIR / "temp"
            temp_dir.mkdir(exist_ok=True)
            temp_epub = temp_dir / f"{saved_path.stem}_{uuid.uuid4().hex[:8]}.epub"

            try:
                file_to_send = convert_to_epub(saved_path, temp_epub)
                temp_file_to_cleanup = temp_epub
                logger.info(f"send_kindle_email: Converted to {file_to_send.name}")
            except Exception as e:
                logger.error(f"send_kindle_email: Conversion failed: {e}")
                file_to_send = saved_path

        msg = EmailMessage()
        msg["From"] = smtp_config.from_email
        msg["To"] = user.kindle_email
        msg["Subject"] = f"{result.get('title', saved_path.name)}"

        body_lines = [
            f"Here is your book: {result.get('title', saved_path.name)}",
            "",
            "Sent via CodexBooks feeder.",
        ]
        msg.set_content("\n".join(body_lines))

        with file_to_send.open("rb") as f:
            data = f.read()
        maintype = "application"
        subtype = "octet-stream"
        # Sanitize filename for Kindle compatibility (fixes E999 error)
        safe_filename = sanitize_filename_for_kindle(file_to_send.name)
        msg.add_attachment(
            data,
            maintype=maintype,
            subtype=subtype,
            filename=safe_filename,
        )

        try:
            smtp_config.send(msg)
            logger.info(
                "Sent Kindle email to %s for %s (sent as %s)",
                user.kindle_email,
                saved_path.name,
                file_to_send.name,
            )
            # Record in history that this book was sent to Kindle
            try:
                with history_lock:
                    history_manager.record_kindle_send(
                        user.name,
                        result.get('title', saved_path.stem),
                        result.get('author', ''),
                        user.kindle_email
                    )
            except Exception as e:
                logger.error("Failed to record Kindle send in history: %s", e)
        except Exception:
            logger.exception("Failed to send Kindle email for %s", saved_path)
    finally:
        if temp_file_to_cleanup and temp_file_to_cleanup.exists():
            try:
                temp_file_to_cleanup.unlink()
            except Exception:
                logger.warning("Failed to clean up temp file %s", temp_file_to_cleanup)



def fetch_goodreads_cover(title: str, author: str = "") -> Optional[str]:
    """
    Fetch cover image URL from Goodreads API if available.
    Returns URL string or None if not found.
    """
    try:
        import urllib.request
        import xml.etree.ElementTree as ET

        # Build search query
        search_query = title
        if author:
            search_query = f"{title} {author}"

        # URL encode
        search_query = urllib.parse.quote(search_query)

        # Query Goodreads API (free search endpoint, no key needed for basic search)
        url = f"https://www.goodreads.com/search/index.xml?key=YOUR_KEY&q={search_query}"

        # Actually, Goodreads requires API key. Let's use a simpler approach:
        # Try to construct a cover URL directly if we can identify the book
        # For now, return None and rely on existing cover URLs
        logger.debug(f"Cover lookup for '{title}' by '{author}': skipping (requires API key)")
        return None
    except Exception as e:
        logger.debug(f"Error fetching Goodreads cover: {e}")
        return None


def fetch_zlib_cover_fallback(title: str, author: str = "") -> Optional[str]:
    """
    Fallback: Try to fetch a cover from z-lib (zlib.sk) if Goodreads doesn't have one.
    This is used as a last resort when no other cover source is available.
    
    Returns URL string or None if not found.
    """
    try:
        import requests
        from lxml import html as lxml_html
        
        # Clean title/author for search
        search_title = (title or "").strip()
        search_author = (author or "").strip()
        
        if not search_title:
            logger.debug("fetch_zlib_cover_fallback: empty title, skipping")
            return None
        
        # Try z-lib search
        search_query = f"{search_title}"
        if search_author:
            search_query = f"{search_title} {search_author}"
        
        logger.debug("Attempting z-lib cover fallback for: %s by %s", search_title, search_author or "Unknown")
        
        # Make request to z-lib search
        url = f"https://zlib.sk/s/?q={requests.utils.quote(search_query)}"
        
        resp = requests.get(
            url,
            timeout=8,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0",
            }
        )
        resp.raise_for_status()
        
        # Parse response and look for first result's cover
        tree = lxml_html.fromstring(resp.text)
        
        # z-lib results have covers in <img class="cover"> tags
        cover_imgs = tree.cssselect("img.cover")
        if cover_imgs:
            src = cover_imgs[0].get("src", "").strip()
            if src:
                # Make sure it's absolute URL
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://zlib.sk" + src
                elif not src.startswith("http"):
                    src = "https://zlib.sk/" + src
                
                logger.info("Successfully fetched z-lib fallback cover for '%s': %s", search_title, src)
                return src
        
        logger.debug("z-lib fallback: no cover found for '%s by %s'", search_title, search_author)
        return None
        
    except requests.exceptions.Timeout:
        logger.debug("z-lib fallback: timeout fetching cover for '%s'", title)
        return None
    except requests.exceptions.ConnectionError:
        logger.debug("z-lib fallback: connection error fetching cover for '%s'", title)
        return None
    except Exception as e:
        logger.debug("z-lib fallback: error fetching cover for '%s': %s", title, e)
        return None


def send_notification_email(
    smtp_config,
    user: UserSettings,
    result: Dict,
    item: Optional[ParsedItem] = None,
    sent_to_kindle: bool = True,
):
    """
    Notify the user that a book was downloaded.
    """
    if not user.notification_email or not smtp_config.is_configured():
        return

    title = result.get("title", "") or (item.title if item else "")
    author = (
        result.get("author")
        or (item.author if item else "")
        or "Unknown"
    )
    # Prefer explicit selected_format, fall back to formats list
    fmt = result.get("selected_format", "") or ", ".join(
        result.get("formats", [])
    )

    # Strip HTML from description coming from RSS or scraped source
    raw_description = result.get("description") or (item.description if item else "")
    description = strip_html_tags(raw_description).strip()

    # Try to get cover from file first, then URL (same strategy as batch emails)
    file_path_str = result.get("file_path")
    file_path = Path(file_path_str) if file_path_str else None
    cover_url = result.get("cover") or (item.cover if item else "") or ""

    # ONLY use Goodreads images
    if not is_goodreads_image(cover_url):
        cover_url = None

    cover_data, mime_type = get_cover_for_email(
        file_path=file_path,
        cover_url=cover_url,
        title=title
    )

    # Store cover data for MIME embedding
    cover_cid = None
    if cover_data:
        cover_cid = f"cover_{hash(title) & 0x7fffffff}"
        esc_cover = f"cid:{cover_cid}"
    else:
        esc_cover = ""

    # Chip text: sent vs added
    if sent_to_kindle and user.kindle_email:
        status_text = f"Sent to {user.name} Kindle"
    else:
        status_text = "Added to library"

    # HTML-escape text fields
    esc_title = html.escape(title or "")
    esc_author = html.escape(author or "")
    esc_fmt = html.escape(fmt or "")
    esc_desc = html.escape(description or "")

    msg = EmailMessage()
    msg["From"] = smtp_config.from_email
    msg["To"] = user.notification_email
    msg["Subject"] = f"{status_text}: {esc_title}"

    # Inline CSS so this works in most email clients
    html_body = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{esc_title}</title>
  <style>
    body {{
      background-color: #0b1220;
      color: #f9fafb;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      margin: 0;
      padding: 16px;
    }}
    .card {{
      max-width: 480px;
      margin: 0 auto;
      background-color: #020617;
      border-radius: 12px;
      border: 1px solid #1e293b;
      overflow: hidden;
    }}
    .card-header {{
      position: relative;
      background-color: #020617;
    }}
    .status-chip {{
      display: inline-block;
      font-size: 11px;
      line-height: 1.2;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid #38bdf8;
      background-color: rgba(56, 189, 248, 0.08);
      color: #e0f2fe;
      white-space: nowrap;
      margin: 8px;
    }}
    .book-cover-img {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .card-body {{
      padding: 12px 16px 16px;
    }}
    .book-title {{
      font-size: 16px;
      font-weight: 600;
      margin-bottom: 4px;
    }}
    .book-author {{
      font-size: 13px;
      color: #9ca3af;
      margin-bottom: 6px;
    }}
    .book-format {{
      font-size: 11px;
      color: #e5e7eb;
      margin-bottom: 10px;
    }}
    .book-description {{
      font-size: 12px;
      color: #e5e7eb;
      line-height: 1.4;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header">
      {"<img src='" + esc_cover + "' alt='Cover' class='book-cover-img' />" if esc_cover else ""}
      <span class="status-chip">{html.escape(status_text)}</span>
    </div>
    <div class="card-body">
      <div class="book-title">{esc_title}</div>
      <div class="book-author">{esc_author}</div>
      {"<div class='book-format'>Format: " + esc_fmt + "</div>" if esc_fmt else ""}
      {"<div class='book-description'>" + esc_desc + "</div>" if esc_desc else ""}
    </div>
  </div>
</body>
</html>
"""

    # Set HTML content
    msg.set_content(html_body, subtype="html")

    # Attach cover image if we have it - MUST be done after set_content
    if cover_data and cover_cid:
        maintype, subtype = mime_type.split('/')
        msg.add_related(
            cover_data,
            maintype=maintype,
            subtype=subtype,
            cid=f"<{cover_cid}>",
            filename="cover.jpg" if maintype == "image" and subtype == "jpeg" else None
        )

    try:
        smtp_config.send(msg)
        logger.info(
            "Sent notification email to %s for %s (status=%s)",
            user.notification_email,
            title,
            status_text,
        )
    except Exception:
        logger.exception("Failed to send notification email")



def send_download_error_notification(
    smtp_config: "SMTPConfig",
    user: UserSettings,
    title: str,
    author: str,
    error_type: str,
    error_details: str,
    html_snippet: Optional[str] = None,
):
    """
    Send an error notification email when a download fails.
    Sends to global notification_emails or per-user notification_email.

    Args:
        smtp_config: SMTP configuration
        user: User settings
        title: Book title
        author: Book author
        error_type: Type of error (e.g., "HTML_RETURNED", "NETWORK_ERROR", "FORMAT_UNAVAILABLE")
        error_details: Human-readable error message
        html_snippet: Optional HTML content that was returned instead of ebook
    """
    # Get list of emails to send to (global first, fall back to per-user)
    emails = []
    settings = settings_manager.settings

    if settings.notification_emails:
        # Parse comma-separated emails from global settings
        emails = [e.strip() for e in settings.notification_emails.split(",") if e.strip()]

    if not emails and user.notification_email:
        # Fallback to single per-user notification_email
        emails = [user.notification_email]

    if not emails or not smtp_config.is_configured():
        return

    # HTML-escape text fields
    esc_title = html.escape(title or "Unknown")
    esc_author = html.escape(author or "Unknown")
    esc_error_type = html.escape(error_type or "")
    esc_error_details = html.escape(error_details or "")

    # Parse HTML snippet if available
    html_content = ""
    if html_snippet:
        try:
            # Try to extract readable text from HTML
            soup = BeautifulSoup(html_snippet, 'html.parser')
            # Remove script and style tags
            for script in soup(['script', 'style']):
                script.decompose()
            # Get text
            text = soup.get_text()
            # Clean up whitespace
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            text = '\n'.join(lines)[:5000]  # Increased to 5000 chars to capture more content
            if text.strip():
                logger.debug("Successfully extracted %d bytes of text from HTML error page", len(text))
                html_content = f"<p><strong>Server Response Content:</strong></p><pre style='background: #f0f0f0; padding: 10px; overflow-x: auto; max-height: 600px; overflow-y: auto; font-size: 12px;'>{html.escape(text)}</pre>"
            else:
                logger.debug("HTML snippet parsed but contained no visible text, showing raw HTML instead")
                # If no text was extracted, show raw HTML - capture up to 5000 chars
                html_content = f"<p><strong>Server Response (Raw HTML):</strong></p><pre style='background: #f0f0f0; padding: 10px; overflow-x: auto; max-height: 600px; overflow-y: auto; font-size: 12px;'>{html.escape(html_snippet[:5000])}</pre>"
        except Exception as e:
            logger.debug("Failed to parse HTML snippet: %s (snippet was: %s)", e, html_snippet[:100] if html_snippet else "None")
            # Fallback: show raw HTML if parsing failed - capture up to 5000 chars
            if html_snippet:
                html_content = f"<p><strong>Server Response (Raw HTML - parse failed):</strong></p><pre style='background: #f0f0f0; padding: 10px; overflow-x: auto; max-height: 600px; overflow-y: auto; font-size: 12px;'>{html.escape(html_snippet[:5000])}</pre>"
    else:
        logger.debug("No HTML snippet provided to send_download_error_notification")

    msg = EmailMessage()
    msg["From"] = smtp_config.from_email
    msg["To"] = ", ".join(emails)
    msg["Subject"] = f"Download Error: {esc_title}"

    # Build error-themed HTML email
    error_color_class = "error-warning" if error_type == "HTML_RETURNED" else "error-critical"

    html_body = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Download Error: {esc_title}</title>
  <style>
    body {{
      background-color: #0b1220;
      color: #f9fafb;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      margin: 0;
      padding: 16px;
    }}
    .card {{
      max-width: 600px;
      margin: 0 auto;
      background-color: #020617;
      border-radius: 12px;
      border: 2px solid #dc2626;
      overflow: hidden;
    }}
    .card-header {{
      background-color: #7f1d1d;
      padding: 20px;
      border-bottom: 1px solid #dc2626;
    }}
    .error-icon {{
      font-size: 32px;
      margin-bottom: 10px;
    }}
    .card-title {{
      font-size: 20px;
      font-weight: 600;
      color: #fca5a5;
      margin: 0;
    }}
    .card-subtitle {{
      font-size: 14px;
      color: #fecaca;
      margin-top: 5px;
    }}
    .card-body {{
      padding: 20px;
    }}
    .info-block {{
      margin: 15px 0;
      padding: 12px;
      background-color: #1e1b4b;
      border-left: 3px solid #dc2626;
      border-radius: 4px;
    }}
    .info-label {{
      font-weight: 600;
      color: #a5f3fc;
      font-size: 12px;
      text-transform: uppercase;
      margin-bottom: 4px;
    }}
    .info-value {{
      color: #f1f5f9;
      font-size: 14px;
      word-break: break-word;
    }}
    .error-type {{
      display: inline-block;
      background-color: #dc2626;
      color: white;
      padding: 4px 12px;
      border-radius: 4px;
      font-size: 12px;
      font-weight: 600;
      margin-bottom: 12px;
    }}
    .action-note {{
      margin-top: 20px;
      padding: 12px;
      background-color: #1a3a3a;
      border-left: 3px solid #06b6d4;
      border-radius: 4px;
      font-size: 13px;
      color: #cffafe;
    }}
    pre {{
      background: #1e293b;
      color: #e2e8f0;
      padding: 10px;
      overflow-x: auto;
      border-radius: 4px;
      font-size: 12px;
      line-height: 1.4;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header">
      <div class="error-icon">⚠️</div>
      <h1 class="card-title">Download Failed</h1>
      <p class="card-subtitle">Error occurred while attempting to download</p>
    </div>
    <div class="card-body">
      <div class="error-type">{esc_error_type}</div>

      <div class="info-block">
        <div class="info-label">Book Title</div>
        <div class="info-value">{esc_title}</div>
      </div>

      <div class="info-block">
        <div class="info-label">Author</div>
        <div class="info-value">{esc_author}</div>
      </div>

      <div class="info-block">
        <div class="info-label">Error Details</div>
        <div class="info-value">{esc_error_details}</div>
      </div>

      {html_content}

      <div class="action-note">
        <strong>Note:</strong> This book will be retried on the next feed run. If the issue persists, 
        it may indicate a temporary issue with the download source or network connectivity.
      </div>
    </div>
  </div>
</body>
</html>
"""

    msg.set_content("Download Error: " + esc_title)
    msg.add_alternative(html_body, subtype="html")

    try:
        logger.debug("Sending download error notification to %s for %s", emails, esc_title)
        smtp_config.send(msg)
    except Exception:
        logger.exception("Failed to send download error notification email")






def send_metadata_enrichment_failure_notification(
    smtp_config: "SMTPConfig",
    user: UserSettings,
    title: str,
    author: str,
    failure_reason: str,
    enrichment_stage: str,
    debug_info: Optional[str] = None,
    response_html: Optional[str] = None,
):
    """
    Send a notification when metadata enrichment fails for a book.
    Similar to download error notifications but for metadata scraping issues.

    Args:
        smtp_config: SMTP configuration
        user: User settings
        title: Book title
        author: Book author
        failure_reason: Why enrichment failed (e.g., "Goodreads search returned no results")
        enrichment_stage: What stage failed (e.g., "goodreads_search", "scraping_details")
        debug_info: Optional debug/technical details
        response_html: Optional HTML response that caused the issue
    """
    # Get list of emails to send to (global first, fall back to per-user)
    emails = []
    settings = settings_manager.settings

    if settings.notification_emails:
        # Parse comma-separated emails from global settings
        emails = [e.strip() for e in settings.notification_emails.split(",") if e.strip()]

    if not emails and user.notification_email:
        # Fallback to single per-user notification_email
        emails = [user.notification_email]

    if not emails or not smtp_config.is_configured():
        return

    # HTML-escape text fields
    esc_title = html.escape(title or "Unknown")
    esc_author = html.escape(author or "Unknown")
    esc_reason = html.escape(failure_reason or "Unknown reason")
    esc_stage = html.escape(enrichment_stage or "unknown")
    esc_debug = html.escape(debug_info or "")

    # Parse HTML response if available
    html_content = ""
    if response_html:
        try:
            # Try to extract readable text from HTML
            soup = BeautifulSoup(response_html, 'html.parser')
            # Remove script and style tags
            for script in soup(['script', 'style']):
                script.decompose()
            # Get text
            text = soup.get_text()
            # Clean up whitespace
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            text = '\n'.join(lines)[:10000]  # Capture more for debugging
            if text.strip():
                logger.debug("Extracted %d bytes from response HTML for metadata failure", len(text))
                html_content = f"<p><strong>Server Response (Full HTML):</strong></p><pre style='background: #f0f0f0; padding: 10px; overflow-x: auto; max-height: 600px; overflow-y: auto; font-size: 11px;'>{html.escape(text)}</pre>"
            else:
                # If no text extracted, show raw HTML
                html_content = f"<p><strong>Server Response (Raw HTML):</strong></p><pre style='background: #f0f0f0; padding: 10px; overflow-x: auto; max-height: 600px; overflow-y: auto; font-size: 11px;'>{html.escape(response_html[:10000])}</pre>"
        except Exception as e:
            logger.debug("Failed to parse response HTML: %s", e)
            if response_html:
                html_content = f"<p><strong>Server Response (Raw HTML):</strong></p><pre style='background: #f0f0f0; padding: 10px; overflow-x: auto; max-height: 600px; overflow-y: auto; font-size: 11px;'>{html.escape(response_html[:10000])}</pre>"

    msg = EmailMessage()
    msg["From"] = smtp_config.from_email
    msg["To"] = ", ".join(emails)
    msg["Subject"] = f"Metadata Enrichment Failed: {esc_title}"

    # Build email body with styled HTML
    debug_block = ""
    if esc_debug:
        debug_block = f'<div class="info-block"><div class="info-label">Debug Information</div><div class="info-value"><pre style="margin: 0; font-size: 12px;">{esc_debug}</pre></div></div>'

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Metadata Enrichment Failed: {esc_title}</title>
  <style>
    body {{ background-color: #0b1220; color: #f9fafb; font-family: system-ui, sans-serif; margin: 0; padding: 16px; }}
    .card {{ max-width: 700px; margin: 0 auto; background-color: #020617; border-radius: 12px; border: 2px solid #f59e0b; overflow: hidden; }}
    .card-header {{ background-color: #92400e; padding: 20px; border-bottom: 1px solid #f59e0b; }}
    .card-title {{ font-size: 20px; font-weight: 600; color: #fcd34d; margin: 0; }}
    .card-subtitle {{ font-size: 14px; color: #fde047; margin-top: 5px; }}
    .card-body {{ padding: 20px; }}
    .info-block {{ margin: 15px 0; padding: 12px; background-color: #1e1b4b; border-left: 3px solid #f59e0b; border-radius: 4px; }}
    .info-label {{ font-weight: 600; color: #fbbf24; font-size: 12px; text-transform: uppercase; margin-bottom: 4px; }}
    .info-value {{ color: #f1f5f9; font-size: 14px; word-break: break-word; }}
    .stage-tag {{ display: inline-block; background-color: #f59e0b; color: #000; padding: 4px 12px; border-radius: 4px; font-size: 12px; font-weight: 600; margin-bottom: 12px; }}
    .action-note {{ margin-top: 20px; padding: 12px; background-color: #3a2a1a; border-left: 3px solid #f59e0b; border-radius: 4px; font-size: 13px; color: #fde047; }}
    pre {{ background: #1e293b; color: #e2e8f0; padding: 10px; overflow-x: auto; border-radius: 4px; font-size: 11px; line-height: 1.4; max-height: 600px; overflow-y: auto; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header">
      <h1 class="card-title">⚠️ Metadata Enrichment Failed</h1>
      <p class="card-subtitle">Could not fetch complete metadata for this book</p>
    </div>
    <div class="card-body">
      <div class="stage-tag">{esc_stage}</div>

      <div class="info-block">
        <div class="info-label">Book Title</div>
        <div class="info-value">{esc_title}</div>
      </div>

      <div class="info-block">
        <div class="info-label">Author</div>
        <div class="info-value">{esc_author}</div>
      </div>

      <div class="info-block">
        <div class="info-label">Failure Reason</div>
        <div class="info-value">{esc_reason}</div>
      </div>
      {debug_block}

      {html_content}

      <div class="action-note">
        <strong>Note:</strong> This book's metadata will be retried on the next maintenance cycle. 
        If this issue persists, you may need to manually add the missing metadata or check if the 
        book's Goodreads page is accessible.
      </div>
    </div>
  </div>
</body>
</html>"""

    msg.set_content("Metadata Enrichment Failed: " + esc_title)
    msg.add_alternative(html_body, subtype="html")

    try:
        logger.debug("Sending metadata enrichment failure notification to %s for %s (stage: %s)", 
                    emails, esc_title, enrichment_stage)
        smtp_config.send(msg)
    except Exception:
        logger.exception("Failed to send metadata enrichment failure notification")


def send_batched_metadata_enrichment_failures(smtp_config, user, failures):
    """Send a single batch email with all metadata enrichment failures from cycle."""
    if not failures:
        return

    emails = []
    settings = settings_manager.settings

    if settings.notification_emails:
        emails = [e.strip() for e in settings.notification_emails.split(",") if e.strip()]

    if not emails and user and user.notification_email:
        emails = [user.notification_email]

    if not emails or not smtp_config.is_configured():
        logger.debug("No notification emails for metadata failures")
        return

    # Build failure items HTML
    failures_html = ""
    for idx, failure in enumerate(failures, 1):
        title = html.escape(failure.get("title", "Unknown"))
        author = html.escape(failure.get("author", "Unknown"))
        reason = html.escape(failure.get("reason", "Unknown"))
        stage = html.escape(failure.get("stage", "unknown"))
        debug = html.escape(failure.get("debug_info", ""))

        failures_html += f"""      <div class="failure-item">
        <div class="failure-num">#{idx}</div>
        <div class="failure-title">{title}</div>
        <div class="failure-author">by {author}</div>
        <div class="failure-stage">{stage}</div>
        <div class="failure-reason">{reason}</div>
        <details>
          <summary>Debug Info</summary>
          <pre>{debug}</pre>
        </details>
      </div>
"""

    msg = EmailMessage()
    msg["From"] = smtp_config.from_email
    msg["To"] = ", ".join(emails)
    msg["Subject"] = f"📚 Metadata Enrichment Report: {len(failures)} issues"

    html_body = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Metadata Enrichment Report</title>
  <style>
    body {{ background-color: #0b1220; color: #f9fafb; font-family: system-ui, sans-serif; margin: 0; padding: 16px; }}
    .card {{ max-width: 900px; margin: 0 auto; background-color: #020617; border-radius: 12px; border: 2px solid #f59e0b; overflow: hidden; }}
    .card-header {{ background-color: #92400e; padding: 20px; border-bottom: 1px solid #f59e0b; }}
    .card-title {{ font-size: 22px; font-weight: 600; color: #fcd34d; margin: 0; }}
    .card-subtitle {{ font-size: 14px; color: #fde047; margin-top: 5px; }}
    .card-body {{ padding: 20px; }}
    .summary {{ background-color: #1e1b4b; padding: 15px; border-radius: 8px; border-left: 3px solid #f59e0b; margin-bottom: 20px; }}
    .summary-num {{ font-size: 24px; font-weight: bold; color: #fcd34d; }}
    .failures-list {{ margin-top: 20px; }}
    .failure-item {{ background-color: #1a1625; border-left: 4px solid #f59e0b; padding: 15px; margin-bottom: 15px; border-radius: 4px; }}
    .failure-num {{ display: inline-block; background-color: #f59e0b; color: #000; font-weight: bold; padding: 4px 10px; border-radius: 3px; margin-right: 10px; font-size: 12px; }}
    .failure-title {{ font-size: 15px; font-weight: 600; color: #fde047; }}
    .failure-author {{ font-size: 13px; color: #cbd5e1; }}
    .failure-stage {{ display: inline-block; background-color: #92400e; color: #fcd34d; padding: 2px 8px; border-radius: 3px; font-size: 11px; margin-top: 8px; }}
    .failure-reason {{ font-size: 12px; color: #f1f5f9; background-color: #0f172a; padding: 8px; border-radius: 3px; margin-top: 8px; }}
    details {{ margin-top: 10px; }}
    summary {{ cursor: pointer; color: #a5f3fc; font-size: 12px; }}
    pre {{ background: #0f172a; color: #e2e8f0; padding: 8px; overflow-x: auto; border-radius: 3px; font-size: 10px; max-height: 300px; overflow-y: auto; margin-top: 8px; }}
    .action-note {{ margin-top: 20px; padding: 12px; background-color: #1e1b4b; border-left: 3px solid #06b6d4; border-radius: 4px; font-size: 13px; color: #cffafe; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="card-header">
      <h1 class="card-title">📚 Metadata Enrichment Report</h1>
      <p class="card-subtitle">Summary from background maintenance cycle</p>
    </div>
    <div class="card-body">
      <div class="summary">
        <div class="summary-num">{len(failures)}</div>
        Books failed to enrich
      </div>

      <div class="failures-list">
{failures_html}
      </div>

      <div class="action-note">
        <strong>Note:</strong> These books will be retried next cycle. Click "Debug Info" to see technical details.
        Common causes: Goodreads unavailable, network timeout, or book not found.
      </div>
    </div>
  </div>
</body>
</html>"""

    msg.set_content(f"Metadata Enrichment Report: {len(failures)} issues")
    msg.add_alternative(html_body, subtype="html")

    try:
        logger.info("Sending metadata enrichment batch report to %s (%d failures)", emails, len(failures))
        smtp_config.send(msg)
    except Exception:
        logger.exception("Failed to send metadata enrichment batch email")

def send_missing_metadata_report_email(
    smtp_config,
    missing_entries: List[Dict],
    enriched_count: int = 0
):
    """
    Send an email report of entries with missing metadata after enrichment.
    Shows what metadata is missing (genres, rating, description, cover) for each entry.
    """
    # Get list of emails to send to
    emails = []
    settings = settings_manager.settings

    if settings.notification_emails:
        # Parse comma-separated emails from global settings
        emails = [e.strip() for e in settings.notification_emails.split(",") if e.strip()]

    if not emails or not smtp_config.is_configured() or not missing_entries:
        return

    # Build email HTML content
    missing_html_rows = ""
    
    for entry in missing_entries:
        title = entry.get("title", "Unknown")
        author = entry.get("author", "Unknown")
        missing_fields = entry.get("missing_fields", [])
        
        # Create list of missing fields with styling
        missing_list = ", ".join(["<span style='color: #c62828;'>" + field + "</span>" for field in missing_fields])
        
        esc_title = html.escape(title or "")
        esc_author = html.escape(author or "")
        
        missing_html_rows += "<tr style=\"border-bottom: 1px solid #e0e0e0;\">\n"
        missing_html_rows += "            <td style=\"padding: 10px; text-align: left; font-size: 13px;\">" + esc_title + "</td>\n"
        missing_html_rows += "            <td style=\"padding: 10px; text-align: left; font-size: 12px; color: #666;\">" + esc_author + "</td>\n"
        missing_html_rows += "            <td style=\"padding: 10px; text-align: left; font-size: 12px;\">" + missing_list + "</td>\n"
        missing_html_rows += "        </tr>\n"
    
    # Build HTML email body
    html_body = "<html>\n    <head>\n        <style>\n"
    html_body += "            body { font-family: Arial, sans-serif; background-color: #f5f5f5; }\n"
    html_body += "            .email-container { max-width: 800px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }\n"
    html_body += "            .header { background-color: #2c3e50; color: white; padding: 15px; border-radius: 4px; margin-bottom: 20px; }\n"
    html_body += "            .header h2 { margin: 0 0 10px 0; font-size: 24px; }\n"
    html_body += "            .stats { display: flex; gap: 20px; margin-bottom: 20px; font-size: 14px; }\n"
    html_body += "            .stat-box { padding: 10px; background-color: #f9f9f9; border-radius: 4px; border-left: 4px solid #2c3e50; }\n"
    html_body += "            .stat-box .number { font-size: 18px; font-weight: bold; color: #2c3e50; }\n"
    html_body += "            table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }\n"
    html_body += "            table th { background-color: #f0f0f0; padding: 12px; text-align: left; font-weight: 600; color: #333; border-bottom: 2px solid #ddd; }\n"
    html_body += "            .missing-items { max-height: 600px; overflow-y: auto; }\n"
    html_body += "            .footer { font-size: 12px; color: #999; text-align: center; margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd; }\n"
    html_body += "        </style>\n    </head>\n    <body>\n"
    html_body += "        <div class=\"email-container\">\n"
    html_body += "            <div class=\"header\">\n"
    html_body += "                <h2>📚 Library Metadata Report</h2>\n"
    html_body += "                <p>Entries with incomplete metadata after enrichment cycle</p>\n"
    html_body += "            </div>\n"
    html_body += "            \n"
    html_body += "            <div class=\"stats\">\n"
    html_body += "                <div class=\"stat-box\">\n"
    html_body += "                    <div class=\"number\">" + str(enriched_count) + "</div>\n"
    html_body += "                    <div>Entries Enriched</div>\n"
    html_body += "                </div>\n"
    html_body += "                <div class=\"stat-box\">\n"
    html_body += "                    <div class=\"number\">" + str(len(missing_entries)) + "</div>\n"
    html_body += "                    <div>Still Missing Metadata</div>\n"
    html_body += "                </div>\n"
    html_body += "            </div>\n"
    html_body += "            \n"
    html_body += "            <div class=\"missing-items\">\n"
    html_body += "                <table>\n"
    html_body += "                    <thead>\n"
    html_body += "                        <tr>\n"
    html_body += "                            <th style=\"width: 40%;\">Title</th>\n"
    html_body += "                            <th style=\"width: 30%;\">Author</th>\n"
    html_body += "                            <th style=\"width: 30%;\">Missing Fields</th>\n"
    html_body += "                        </tr>\n"
    html_body += "                    </thead>\n"
    html_body += "                    <tbody>\n"
    html_body += "                        " + missing_html_rows
    html_body += "                    </tbody>\n"
    html_body += "                </table>\n"
    html_body += "            </div>\n"
    html_body += "            \n"
    html_body += "            <div class=\"footer\">\n"
    html_body += "                <p>This is an automated report from GoodBooks Library metadata enrichment cycle.</p>\n"
    html_body += "                <p>Check the application for more details or manually search for missing metadata.</p>\n"
    html_body += "            </div>\n"
    html_body += "        </div>\n"
    html_body += "    </body>\n"
    html_body += "</html>"
    
    # Create email message using EmailMessage API
    msg = EmailMessage()
    msg["Subject"] = "Library Metadata Report: " + str(len(missing_entries)) + " entries with missing data"
    msg["From"] = smtp_config.from_email
    msg["To"] = ", ".join(emails)
    
    text_body = "Library Metadata Report\n\n"
    text_body += "Enrichment Cycle Complete:\n"
    text_body += "- Entries Enriched: " + str(enriched_count) + "\n"
    text_body += "- Still Missing Metadata: " + str(len(missing_entries)) + "\n\n"
    text_body += "Entries with missing metadata:\n"
    for e in missing_entries:
        text_body += "- " + e.get("title", "Unknown") + " by " + e.get("author", "Unknown") + ": " + ", ".join(e.get("missing_fields", [])) + "\n"
    
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    
    try:
        logger.info("Sending missing metadata report to %s (%d entries with missing fields)", emails, len(missing_entries))
        smtp_config.send(msg)
    except Exception:
        logger.exception("Failed to send missing metadata report email")


def get_missing_fields_for_entry(metadata: Dict, entry_id: str) -> List[str]:
    """
    Determine which metadata fields are missing for a given entry.
    Returns list of field names that are missing or empty.
    """
    entry_meta = metadata.get(entry_id, {})
    goodreads_meta = entry_meta.get("goodreads_meta", {}) or {}
    
    missing_fields = []
    
    # Check genres
    if not goodreads_meta.get("genres"):
        missing_fields.append("Genres")
    
    # Check rating
    if goodreads_meta.get("rating") is None:
        missing_fields.append("Rating")
    
    # Check description
    if not (entry_meta.get("description") or goodreads_meta.get("description")):
        missing_fields.append("Description")
    
    # Check cover
    if not entry_meta.get("cover"):
        missing_fields.append("Cover")
    
    return missing_fields


def log_metadata_miss(entry_id: str, title: str, missing_fields: List[str], reason: str = ""):
    """
    Log a metadata fetch failure or incomplete metadata to the metadata misses log.
    Helps identify patterns in which books are missing metadata and why.
    
    Args:
        entry_id: The library entry ID
        title: Book title for easy identification
        missing_fields: List of missing field names (e.g., ["Cover", "Rating"])
        reason: Optional reason for the miss (e.g., "timeout", "goodreads_unavailable", "no_match")
    """
    try:
        timestamp = datetime.now().isoformat()
        log_line = f"{timestamp} | {title[:60]} | Missing: {', '.join(missing_fields)} | Reason: {reason}\n"
        
        # Append to metadata misses log in a thread-safe way
        with open(METADATA_MISS_LOG, "a") as f:
            f.write(log_line)
    except Exception as e:
        logger.debug("Failed to write to metadata miss log: %s", e)



def send_batch_notification_email(
    smtp_config,
    user: UserSettings,
    results: List[Dict],
    sent_to_kindle: bool = True,
):
    """
    Send a grid-style HTML notification email for bulk downloads.
    Shows cover images in a responsive grid layout with titles, authors, ratings, descriptions, and Goodreads links.
    Sends to global notification_emails (comma-separated) or falls back to per-user notification_email.
    """
    # Get list of emails to send to (global first, fall back to per-user)
    emails = []
    settings = settings_manager.settings

    if settings.notification_emails:
        # Parse comma-separated emails from global settings
        emails = [e.strip() for e in settings.notification_emails.split(",") if e.strip()]

    if not emails and user.notification_email:
        # Fallback to single per-user notification_email
        emails = [user.notification_email]

    if not emails or not smtp_config.is_configured():
        return

    # Build email HTML content (same for all recipients)
    status_label = "Sent to Kindle" if sent_to_kindle else "Added to Library"

    cover_attachments = {}  # Track covers for MIME embedding
    grid_html = ""
    for result in results:
        title = result.get("title", "Unknown")
        author = result.get("author", "Unknown")
        cover = result.get("cover") or ""
        file_path = result.get("file_path")  # Optional: path to ebook file for cover extraction
        rating = result.get("goodreads_meta", {}).get("rating") or result.get("rating")
        description = result.get("description", "")
        goodreads_url = result.get("goodreads_meta", {}).get("goodreads_url", "")

        # Strip HTML from description if needed
        if description:
            description = strip_html_tags(description).strip()

        # Build cover HTML - extract from local cache, then EPUB file
        cover_html = None
        cover_cid = None
        library_id = result.get("library_id")

        try:
            logger.debug("Getting cover for batch notification: %s (library_id=%s)", title, library_id)
            # Try to get cover from cache, file, or URL (if it's a Goodreads image)
            cover_url = cover if is_goodreads_image(cover) else None
            cover_data, cover_mimetype = get_cover_for_email(
                file_path=Path(file_path) if file_path else None,
                cover_url=cover_url,  # Use cover URL if it's from Goodreads
                title=title,
                library_id=library_id
            )
            logger.debug("Cover result for %s: has_data=%s, mime=%s", title, cover_data is not None, cover_mimetype)

            if cover_data:
                logger.debug("Using cover for %s (%d bytes)", title, len(cover_data))
                # Create MIME-embedded cover with Content-ID
                cover_cid = f"cover_{hash(title) & 0x7fffffff}"
                cover_attachments[cover_cid] = (cover_data, cover_mimetype)
                cover_html = f'<img src="cid:{cover_cid}" alt="{html.escape(title or "")}" style="max-width: 100%; max-height: 140px; object-fit: contain; border-radius: 4px;" />'
        except Exception as e:
            logger.debug("Error getting cover for %s: %s", title, e)

        # Build cover container - ONLY if we have actual cover_html (no placeholder)
        cover_container_html = ""
        if cover_html:
            cover_container_html = f'<div style="margin-bottom: 8px; display: flex; justify-content: center; align-items: center; min-height: 140px; background-color: white; border-radius: 4px; overflow: hidden;">{cover_html}</div>'

        esc_title = html.escape(title or "")
        esc_author = html.escape(author or "")

        # Build rating HTML
        rating_html = ""
        if rating:
            try:
                rating_val = float(rating)
                rating_html = f"<div style='font-size: 12px; color: #ff9800; margin-top: 4px; font-weight: 500;'>★ {rating_val:.1f}</div>"
            except (TypeError, ValueError):
                pass

        # Build description with better formatting and Goodreads link
        desc_html = ""
        if description:
            # Show full description without truncation
            display_desc = html.escape(description)

            desc_html = f"<div style='font-size: 12px; color: #666; margin-top: 6px; line-height: 1.4; word-break: break-word;'>{display_desc}</div>"

        # Build Goodreads link button
        gr_link_html = ""
        if goodreads_url:
            gr_link_html = f"<div style='margin-top: 8px;'><a href='{html.escape(goodreads_url)}' style='display: inline-block; padding: 6px 12px; background-color: #d4a574; color: white; text-decoration: none; border-radius: 4px; font-size: 11px; font-weight: 500;'>View on Goodreads</a></div>"

        grid_html += f'''<div style="border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; text-align: center; background-color: #fafafa; display: flex; flex-direction: column;">
            {cover_container_html}
            <div style="font-weight: 600; font-size: 13px; margin: 8px 0 0 0; word-break: break-word; line-height: 1.3;">{esc_title}</div>
            <div style="font-size: 11px; color: #666; margin: 4px 0 0 0; word-break: break-word;">{esc_author}</div>
            {rating_html}
            {desc_html}
            {gr_link_html}
        </div>'''

    html_body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{status_label}</title>
    <style>
        body {{
            background-color: #f5f5f5;
            color: #333;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
            margin: 0;
            padding: 16px;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            background-color: white;
            border-radius: 8px;
            padding: 24px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
        }}
        .header {{
            margin-bottom: 24px;
            border-bottom: 2px solid #e0e0e0;
            padding-bottom: 16px;
        }}
        .header h1 {{
            margin: 0 0 8px 0;
            font-size: 28px;
            color: #333;
        }}
        .header p {{
            margin: 0;
            color: #666;
            font-size: 14px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        @media (max-width: 600px) {{
            .grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
            .container {{
                padding: 16px;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{status_label}</h1>
            <p>{len(results)} book{"s" if len(results) != 1 else ""} {"sent to Kindle" if sent_to_kindle else "added to your library"}</p>
        </div>
        <div class="grid">
            {grid_html}
        </div>
    </div>
</body>
</html>"""

    # Send to all configured emails
    for recipient_email in emails:
        try:
            msg = EmailMessage()
            msg["From"] = smtp_config.from_email
            msg["To"] = recipient_email
            msg["Subject"] = f"{status_label}: {len(results)} books"
            msg.set_content(html_body, subtype="html")

            # Attach embedded images with Content-IDs
            logger.debug("Attaching %d cover images to batch notification message", len(cover_attachments))
            for cid, (cover_data, mime_type) in cover_attachments.items():
                maintype, subtype = mime_type.split('/', 1)
                msg.add_related(
                    cover_data,
                    maintype=maintype,
                    subtype=subtype,
                    cid=f"<{cid}>",
                    filename="cover.jpg" if maintype == "image" and subtype == "jpeg" else None
                )

            logger.info("Sent batch notification email to %s for %d books (%s, %d with covers)", 
                       recipient_email, len(results), 
                       "Kindle" if sent_to_kindle else "Library",
                       len(cover_attachments))
            smtp_config.send(msg)
        except Exception:
            logger.exception("Failed to send batch notification email to %s", recipient_email)


def _normalize_sort_key(value: str) -> str:
    return (value or "").casefold()


def load_library_metadata() -> Dict[str, Dict]:
    """
    Load library_metadata.json if present, with a simple in-memory cache.

    Keys are arbitrary strings; in this phase we use a simple composite:
    "<absolute-root>::<relpath-unix-style>".
    """
    global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME

    if not LIBRARY_METADATA_PATH.exists():
        _LIBRARY_METADATA_CACHE = {}
        _LIBRARY_METADATA_MTIME = 0.0
        return {}

    try:
        mtime = LIBRARY_METADATA_PATH.stat().st_mtime
    except OSError:
        logger.exception("Failed to stat library metadata file %s", LIBRARY_METADATA_PATH)
        return {}

    # Fast path: if file has not changed since last load, reuse cached data
    if _LIBRARY_METADATA_CACHE and _LIBRARY_METADATA_MTIME == mtime:
        return _LIBRARY_METADATA_CACHE

    try:
        text = LIBRARY_METADATA_PATH.read_text()
        data = json.loads(text) if text.strip() else {}
    except Exception:
        logger.exception("Failed to load library metadata from %s", LIBRARY_METADATA_PATH)
        _LIBRARY_METADATA_CACHE = {}
        _LIBRARY_METADATA_MTIME = 0.0
        return {}

    _LIBRARY_METADATA_CACHE = data
    _LIBRARY_METADATA_MTIME = mtime
    return data

def get_library_roots() -> List[Path]:
    """
    Determine effective library roots from settings.

    - library_root (or default_download_dir if empty)
    - library_extra_dirs
    """
    settings = settings_manager.settings
    roots: List[Path] = []

    root_str = getattr(settings, "library_root", "") or settings.default_download_dir
    roots.append(resolve_download_dir(root_str))

    for extra in getattr(settings, "library_extra_dirs", []):
        if not extra:
            continue
        try:
            roots.append(resolve_download_dir(extra))
        except Exception:
            logger.exception("Failed to resolve extra library dir %s", extra)

    # De-duplicate while preserving order
    seen = set()
    unique_roots: List[Path] = []
    for r in roots:
        r_resolved = r.resolve()
        if r_resolved in seen:
            continue
        seen.add(r_resolved)
        unique_roots.append(r_resolved)
    return unique_roots

def find_duplicate_in_library_by_md5(file_path: Path) -> Optional[Dict[str, Any]]:
    """
    Check if a file with the same MD5 already exists in the library.

    Returns: 
        - dict with 'id', 'title', 'author', 'path' if found
        - None if not found

    This helps avoid downloading duplicates when processing feeds.
    """
    try:
        # Compute MD5 of the candidate file
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5_hash.update(chunk)
        candidate_md5 = md5_hash.hexdigest()

        # Check library entries
        entries = build_library_entries()
        for entry in entries:
            try:
                lib_path = Path(entry["root"]) / entry["relpath"]
                if lib_path == file_path:
                    continue  # Skip the file itself

                if not lib_path.exists():
                    continue

                lib_md5 = hashlib.md5()
                with open(lib_path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        lib_md5.update(chunk)

                if lib_md5.hexdigest() == candidate_md5:
                    return {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "author": entry.get("author"),
                        "path": str(lib_path),
                    }
            except Exception:
                continue

        return None
    except Exception as e:
        logger.warning("Failed to compute MD5 for %s: %s", file_path, e)
        return None

def find_book_in_library_by_title_author(title: str, author: str = "") -> Optional[Dict[str, Any]]:
    """
    Check if a book with the same title/author already exists in the library.
    This is used to avoid fetching download links for files that are already
    available locally (e.g., when loading from Anna's Archive).

    Returns:
        - dict with 'id', 'title', 'author', 'path' if found
        - None if not found

    Matching is case-insensitive and ignores punctuation/spacing variations.
    """
    if not title or not title.strip():
        return None

    try:
        # Normalize the search title/author for comparison
        search_title = title.strip().lower()
        search_author = history_manager.cleanup_author(author or "").lower().strip()

        entries = build_library_entries()
        for entry in entries:
            # Normalize library entry title/author
            lib_title = entry.get("title", "").strip().lower()
            lib_author = history_manager.cleanup_author(entry.get("author", "") or "").lower().strip()

            # Match on title and optionally author
            if lib_title == search_title:
                # If author provided, it should match; if not provided, title match is enough
                if not search_author or lib_author == search_author:
                    return {
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "author": entry.get("author"),
                        "path": str(Path(entry.get("root", "")) / entry.get("relpath", "")),
                    }

        return None
    except Exception as e:
        logger.warning("Failed to check for duplicate book title=%r author=%r: %s", title, author, e)
        return None

def generate_folder_cover(folder_prefix: str, all_entries: List[Dict]) -> Optional[str]:
    """
    Generate a composite cover image for a folder using locally cached covers.
    Shows up to 12 book covers that have cover images available.
    Returns base64-encoded PNG data URL, or None if no covers available.

    This version:
    - Only uses books that already have cached covers (no internet downloads during composite generation)
    - Filters out books without covers for fast preview
    - Generates a quick composite from up to 12 cover images
    """
    try:
        # Get books directly in this folder (not subfolders) that have covers
        folder_books = [
            e for e in all_entries 
            if (e.get("relpath", "").startswith(folder_prefix + "/") if folder_prefix else True)
            and e.get("cover")  # Only books with existing cover metadata
            and "/" not in e.get("relpath", "").lstrip((folder_prefix + "/") if folder_prefix else "")  # Direct children only
        ]

        # Get up to 12 covers for the composite
        cover_entries = folder_books[:12]

        if len(cover_entries) < 2:
            # Need at least 2 covers for a composite, otherwise use default folder icon
            return None

        # Create a simple composite grid
        # 4x3 grid if we have 12+ covers, otherwise smaller grid
        cols = 4
        rows = min(3, (len(cover_entries) + cols - 1) // cols)

        cover_size = 75  # Size of each cover in the composite
        composite_width = cols * cover_size
        composite_height = rows * cover_size

        composite = Image.new("RGB", (composite_width, composite_height), color=(200, 200, 200))
        covers_added = 0

        for idx, entry in enumerate(cover_entries[:12]):
            if idx >= 12:
                break

            col = idx % cols
            row = idx // cols

            try:
                # Try to load cover from entry
                cover_data = entry.get("cover")
                if not cover_data:
                    continue
                
                cover_img = None
                
                # Try file path first (fastest, already local)
                if isinstance(cover_data, str):
                    cover_path = None
                    
                    # Check if it's a relative path like "data/covers/..."
                    if cover_data.startswith("data/"):
                        cover_path = BASE_DIR / cover_data
                    # Check if it's an absolute path
                    elif cover_data.startswith("/"):
                        cover_path = Path(cover_data)
                    
                    if cover_path and cover_path.exists():
                        try:
                            cover_img = Image.open(cover_path)
                        except Exception:
                            logger.debug(f"Failed to open cover image at {cover_path}")
                    
                    # Try base64 data URL
                    if not cover_img and cover_data.startswith("data:"):
                        try:
                            _, b64 = cover_data.split(",", 1)
                            cover_img = Image.open(BytesIO(base64.b64decode(b64)))
                        except Exception:
                            pass
                    
                    # Skip HTTP URLs - don't download during folder composite generation
                    # They should have been cached to local files already
                    if not cover_img and (cover_data.startswith("http://") or cover_data.startswith("https://")):
                        # These should have been cached - log if not
                        logger.debug(f"Skipping uncached URL for folder composite: {cover_data[:60]}...")
                        continue
                
                if not cover_img:
                    continue

                # Resize to fit in grid cell
                cover_img.thumbnail((cover_size, cover_size), Image.Resampling.LANCZOS)

                # Paste into composite
                x = col * cover_size
                y = row * cover_size
                composite.paste(cover_img, (x, y))
            except Exception as e:
                logger.debug(f"Could not add cover to folder composite: {e}")
                continue

        # Convert to base64 data URL
        buffer = BytesIO()
        composite.save(buffer, format="PNG")
        b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{b64}"

    except Exception as e:
        logger.debug(f"Could not generate folder cover composite: {e}")
        return None

def get_metadata_field(meta: Dict, field: str, title: str = "", default=""):
    """Get metadata field: goodreads_meta > top-level > extract from title > default"""
    # Try goodreads_meta first (best source)
    gm = meta.get("goodreads_meta") or {}
    if field in gm and gm[field] not in (None, "", [], {}):
        return gm[field]
    
    # Then top-level field
    if field in meta and meta[field] not in (None, "", [], {}):
        return meta[field]
    
    # For author, extract from title "Title - Author" or "Title-Author"
    if field == "author" and title and "-" in title:
        # Try new format first: "Title - Author" (with spaces around hyphen)
        if " - " in title:
            parts = title.rsplit(" - ", 1)
        else:
            # Fallback to old format: "Title-Author" (split on last hyphen)
            parts = title.rsplit("-", 1)
        
        if len(parts) == 2:
            author = parts[1].strip()
            if author and any(c.isupper() for c in author) and any(c.isalpha() for c in author):
                return author.lower()
    
    return default

def build_library_entries() -> List[Dict]:
    """
    Scan all configured library roots for ebook-like files and return a flat list
    of entries. Hierarchical navigation and folder cards are handled in the view.

    To keep the UI responsive on low-powered devices, we avoid re-scanning the
    filesystem on every request when possible. A simple in-memory cache is used,
    with a short TTL so that new files still show up without a restart.
    """
    global _LIBRARY_ENTRIES_CACHE, _LIBRARY_ENTRIES_LAST_SCAN

    # Per-process TTL in seconds; if settings expose a custom value, prefer it.
    try:
        ttl = float(
            getattr(settings_manager.settings, "library_scan_ttl_seconds", 7200.0) or 7200.0
        )
    except Exception:
        ttl = 7200.0

    now = time.time()
    if _LIBRARY_ENTRIES_CACHE and _LIBRARY_ENTRIES_LAST_SCAN:
        if now - _LIBRARY_ENTRIES_LAST_SCAN < ttl:
            # Fast path: reuse the previous scan results.
            return list(_LIBRARY_ENTRIES_CACHE)

    roots = get_library_roots()
    metadata = load_library_metadata()
    entries: List[Dict] = []

    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in EBOOK_EXTENSIONS:
                continue

            relpath = path.relative_to(root)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                logger.warning("Cannot stat library file (permissions?): %s", path)
                mtime = 0.0
            rel_unix = str(relpath).replace(os.sep, "/")
            key = f"{str(root.resolve())}::{rel_unix}"

            meta = metadata.get(key, {})
            title = meta.get("title") or path.stem
            author = get_metadata_field(meta, "author", title)
            rating = get_metadata_field(meta, "rating")
            genres = get_metadata_field(meta, "genres")
            cover = meta.get("cover", "")
            goodreads_link = meta.get("goodreads_link")
            filetype = path.suffix.lower().lstrip(".")
            is_direct = meta.get("is_direct", False)
            language = meta.get("language", "")
            publish_date = meta.get("publish_date", "")

            # Save extracted author back to metadata if it was extracted from filename
            if author and not meta.get("author"):
                meta["author"] = author
                metadata[key] = meta
            
            entries.append(
                {
                    "id": key,
                    "root": str(root.resolve()),
                    "relpath": rel_unix,
                    "title": title,
                    "author": author,
                    "cover": cover,
                    "filetype": filetype,
                    "mtime": mtime,
                    "is_direct": is_direct,
                    "genres": genres,
                    "language": language,
                    "publish_date": publish_date,
                    "rating": rating,
                    "goodreads_link": goodreads_link,
                }
            )

    # Save updated metadata back to file
    try:
        LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
    except Exception as e:
        logger.debug("Failed to save library metadata after build_library_entries: %s", e)

    # Update cache and return the freshly scanned list.
    _LIBRARY_ENTRIES_CACHE = entries
    _LIBRARY_ENTRIES_LAST_SCAN = now
    return entries
def filter_entries_needing_enrichment(entries: List[Dict], metadata: Dict[str, Dict]) -> List[Dict]:
    """Filter entries needing enrichment - missing description, rating, genres, or goodreads_url.
    
    Skips entries that have been marked as 'failed_to_enrich' to avoid infinite loops
    on unfindable books.
    """
    incomplete = []
    for entry in entries:
        library_id = entry["id"]
        meta = metadata.get(library_id, {})
        
        # Skip entries that previously failed enrichment (don't loop infinitely)
        if meta.get("failed_to_enrich"):
            continue
        
        goodreads_meta = meta.get("goodreads_meta", {}) or {}
        if (not bool(goodreads_meta.get("genres")) or
            goodreads_meta.get("rating") is None or
            not bool(meta.get("description") or goodreads_meta.get("description")) or
            not bool(goodreads_meta.get("goodreads_url"))):
            incomplete.append(entry)
    return incomplete


def sort_library_entries(entries: List[Dict], sort_key: str) -> List[Dict]:
    """
    Sort entries according to the configured sort key.
    """
    if sort_key == "date_newest":
        return sorted(entries, key=lambda e: e.get("mtime", 0), reverse=True)
    if sort_key == "date_oldest":
        return sorted(entries, key=lambda e: e.get("mtime", 0))
    if sort_key == "title_az":
        return sorted(
            entries,
            key=lambda e: (
                _normalize_sort_key(e.get("title", "")),
                _normalize_sort_key(e.get("author", "")),
            ),
        )
    if sort_key == "title_za":
        return sorted(
            entries,
            key=lambda e: (
                _normalize_sort_key(e.get("title", "")),
                _normalize_sort_key(e.get("author", "")),
            ),
            reverse=True,
        )
    if sort_key == "author_az":
        return sorted(
            entries,
            key=lambda e: (
                _normalize_sort_key(e.get("author", "")),
                _normalize_sort_key(e.get("title", "")),
            ),
        )
    if sort_key == "author_za":
        return sorted(
            entries,
            key=lambda e: (
                _normalize_sort_key(e.get("author", "")),
                _normalize_sort_key(e.get("title", "")),
            ),
            reverse=True,
        )

    # Fallback: newest first
    return sorted(entries, key=lambda e: e.get("mtime", 0), reverse=True)

def get_library_entry(entry_id: str) -> Optional[Dict]:
    """
    Look up a single library entry by its ID (real path).
    """
    target = entry_id.lstrip("/")
    entries = build_library_entries()
    for entry in entries:
        eid = str(entry.get("id", ""))
        if eid.lstrip("/") == target:
            return entry
    return None

def rename_library_file_to_md5_format(entry_id: str) -> bool:
    """
    Rename a library file to the format: {title}.{author}.{fmt}
    using the metadata stored in library_metadata.json.

    Returns True if rename was successful, False otherwise.
    """
    entry = get_library_entry(entry_id)
    if not entry:
        logger.warning("Cannot rename: entry not found for id=%s", entry_id)
        return False

    metadata = load_library_metadata()
    meta = metadata.get(entry_id, {})

    # Get the components for the new filename
    title = meta.get("title") or entry.get("title", "")
    author = meta.get("author") or entry.get("author", "")
    filetype = meta.get("filetype") or entry.get("filetype", "")

    if not title or not filetype:
        logger.warning("Cannot rename: missing title or filetype for id=%s", entry_id)
        return False

    # Build the new filename WITHOUT sanitization (as requested)
    new_filename = f"{title}"
    if author:
        new_filename = f"{title}.{author}"
    new_filename = f"{new_filename}.{filetype}"

    # Get the old file path
    root = Path(entry["root"])
    relpath = Path(entry["relpath"])
    old_path = (root / relpath).resolve()

    if not old_path.exists():
        logger.warning("Cannot rename: file does not exist at %s", old_path)
        return False

    # Determine the new path (same directory, new filename)
    new_path = old_path.parent / new_filename

    # If the new name is the same, nothing to do
    if old_path == new_path:
        logger.debug("New filename is the same as old, skipping rename for id=%s", entry_id)
        return True

    # Avoid overwriting an existing file
    if new_path.exists():
        logger.warning(
            "Cannot rename %s to %s: destination file already exists",
            old_path,
            new_path
        )
        return False

    try:
        old_path.rename(new_path)
        logger.info("Renamed %s to %s", old_path, new_path)

        # Update the library metadata with the new relative path
        new_relpath = new_path.relative_to(root)
        new_relpath_unix = str(new_relpath).replace(os.sep, "/")
        new_key = f"{str(root.resolve())}::{new_relpath_unix}"

        # Copy metadata to the new key and remove the old key
        metadata[new_key] = metadata.pop(entry_id)

        with library_metadata_lock:
            try:
                LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
            except OSError as e:
                logger.exception("Failed to write library metadata after rename: %s", e)
                # Restore the metadata to original key since write failed
                metadata[entry_id] = metadata.pop(new_key)
                return False
            # Update in-memory cache
            global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME
            _LIBRARY_METADATA_CACHE = metadata
            try:
                _LIBRARY_METADATA_MTIME = LIBRARY_METADATA_PATH.stat().st_mtime
            except OSError:
                _LIBRARY_METADATA_MTIME = 0.0

        # Clear the library entries cache so the next scan picks up the renamed file
        global _LIBRARY_ENTRIES_CACHE, _LIBRARY_ENTRIES_LAST_SCAN
        _LIBRARY_ENTRIES_CACHE = []
        _LIBRARY_ENTRIES_LAST_SCAN = 0.0
        return True
    except Exception:
        logger.exception("Failed to rename file %s to %s", old_path, new_path)
        return False

def batch_rename_library_files_to_md5_format() -> Tuple[int, int]:
    """
    Batch rename all library files to the format: {title}.{author}.{fmt}

    Returns: (success_count, failure_count)
    """
    entries = build_library_entries()
    success_count = 0
    failure_count = 0

    for entry in entries:
        entry_id = entry.get("id")
        if not entry_id:
            continue

        if rename_library_file_to_md5_format(entry_id):
            success_count += 1
        else:
            failure_count += 1

    return success_count, failure_count


def batch_rename_library_files_to_title_author_format() -> Tuple[int, int]:
    """
    Batch rename all library files to the format: {title}-{author}.{fmt}

    Returns: (success_count, failure_count)
    """
    entries = build_library_entries()
    success_count = 0
    failure_count = 0

    with library_metadata_lock:
        metadata = load_library_metadata()

    for entry in entries:
        entry_id = entry.get("id")
        file_path_str = entry.get("path")

        if not entry_id or not file_path_str:
            continue

        try:
            file_path = Path(file_path_str).resolve()
            if not file_path.exists():
                failure_count += 1
                continue

            # Get metadata for this file
            with library_metadata_lock:
                metadata = load_library_metadata()
                meta = metadata.get(entry_id, {})

            title = meta.get("title") or entry.get("title") or file_path.stem
            author = meta.get("author") or entry.get("author") or ""
            ext = file_path.suffix  # Keep original extension

            # Build new filename: {title} - {author}.{ext} (with spaces around hyphen)
            if author:
                new_filename = f"{title} - {author}{ext}"
            else:
                new_filename = f"{title}{ext}"

            new_path = file_path.parent / new_filename

            # Skip if already has correct name
            if new_path == file_path:
                success_count += 1
                continue

            # Rename the file
            file_path.rename(new_path)

            # Update metadata with new path
            with library_metadata_lock:
                metadata = load_library_metadata()
                if entry_id in metadata:
                    # Recompute entry_id for new path
                    new_entry_id = get_library_entry_id(new_path)
                    if new_entry_id and new_entry_id != entry_id:
                        metadata[new_entry_id] = metadata.pop(entry_id)
                    metadata[new_entry_id or entry_id]["path"] = str(new_path)
                    LIBRARY_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
                    LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
                    # Clear the library entries cache since metadata changed
                    global _LIBRARY_ENTRIES_CACHE, _LIBRARY_ENTRIES_LAST_SCAN
                    _LIBRARY_ENTRIES_CACHE = []
                    _LIBRARY_ENTRIES_LAST_SCAN = 0.0

            success_count += 1
            logger.info("Renamed %s to %s", file_path.name, new_filename)

        except Exception as e:
            failure_count += 1
            logger.exception("Failed to rename %s: %s", entry_id, e)

    return success_count, failure_count

def get_library_entry_id(file_path: Path) -> Optional[str]:
    """
    Given a file path, return the library entry ID (root::relpath).
    Returns None if the file is not in any configured library root.
    """
    try:
        file_path = file_path.resolve()
    except Exception:
        return None

    roots = get_library_roots()
    for root in roots:
        try:
            rel = file_path.relative_to(root.resolve())
            rel_unix = str(rel).replace(os.sep, "/")
            return f"{str(root.resolve())}::{rel_unix}"
        except Exception:
            continue

    return None

def upsert_library_metadata_for_download(
    file_path: Path,
    best: Dict,
    item: Optional[object] = None,
) -> None:
    """
    Update library_metadata.json for a downloaded file.

    - file_path: absolute path to the saved file
    - best: selected search result dict (AnnaSource result)
    - item: optional ParsedItem from feeds (may be None for manual downloads)

    We only write metadata if the file lives under one of the configured
    library roots. Keys follow the same scheme as build_library_entries:
        "<absolute-root>::<relpath-unix-style>".
    """
    try:
        file_path = file_path.resolve()
    except Exception:
        logger.exception("Failed to resolve file path for library metadata: %s", file_path)
        return

    roots = []
    try:
        roots = get_library_roots()
    except Exception:
        logger.exception("Failed to get library roots when updating metadata")
        return

    # Compute keys for any library root that contains this file
    keys: List[str] = []
    for root in roots:
        try:
            rel = file_path.relative_to(root.resolve())
        except Exception:
            continue
        rel_unix = str(rel).replace(os.sep, "/")
        key = f"{str(root.resolve())}::{rel_unix}"
        keys.append(key)

    if not keys:
        # File is not in any configured library root; nothing to index
        return

    # Gather metadata from best result + optional feed item
    title = best.get("title") or getattr(item, "title", None) or file_path.stem
    author = best.get("author") or getattr(item, "author", "") or ""

    # Deduplicate author names if they were concatenated
    if author:
        from parser_engine import FeedParser
        from pathlib import Path as PathlibPath
        temp_parser = FeedParser(PathlibPath.home() / ".feed_metadata")
        author = history_manager.cleanup_author(author)
    # Prefer Goodreads cover if available
    goodreads_cover = (best.get("goodreads_meta") or {}).get("cover", "")
    cover = goodreads_cover or best.get("cover") or getattr(item, "cover", "") or ""
    if cover:
        cover = normalize_cover_url(cover)

    # Raw description (HTML allowed) for details page
    description = getattr(item, "description", None) or best.get("description", "")

    # Goodreads link if we can see it
    goodreads_link = (
        best.get("goodreads_url")
        or best.get("goodreads_link")
        or getattr(item, "goodreads_url", "")
    )

    # Optional extra metadata (only stored if present)
    genres = best.get("genres") or (best.get("goodreads_meta") or {}).get("genres") or getattr(item, "genres", None)
    language = best.get("language") or getattr(item, "language", None)
    publish_date = (
        best.get("publish_date")
        or best.get("year")
        or getattr(item, "publish_date", None)
        or getattr(item, "year", None)
    )
    rating = (
        best.get("rating")
        or best.get("goodreads_rating")
        or (best.get("goodreads_meta") or {}).get("rating")
        or getattr(item, "rating", None)
    )
    filetype = best.get("selected_format") or file_path.suffix.lstrip(".").lower()

    # Store full Goodreads metadata if available
    goodreads_meta = best.get("goodreads_meta") or {}

    with library_metadata_lock:
        # Load current metadata from disk
        if LIBRARY_METADATA_PATH.exists():
            try:
                metadata = json.loads(LIBRARY_METADATA_PATH.read_text())
            except Exception:
                logger.exception("Failed to load library metadata from %s", LIBRARY_METADATA_PATH)
                metadata = {}
        else:
            metadata = {}

        def set_if_value(entry: Dict, field: str, value):
            # Only set if value is not empty AND different from existing
            if value not in (None, "", [], {}):
                if entry.get(field) != value:
                    entry[field] = value
                    return True
            return False

        metadata_changed = False
        for key in keys:
            entry = metadata.get(key, {})
            metadata_changed |= set_if_value(entry, "title", title)
            metadata_changed |= set_if_value(entry, "author", author)
            metadata_changed |= set_if_value(entry, "cover", cover)
            metadata_changed |= set_if_value(entry, "description", description)
            metadata_changed |= set_if_value(entry, "goodreads_link", goodreads_link)
            metadata_changed |= set_if_value(entry, "genres", genres)
            metadata_changed |= set_if_value(entry, "language", language)
            metadata_changed |= set_if_value(entry, "publish_date", publish_date)
            metadata_changed |= set_if_value(entry, "rating", rating)
            metadata_changed |= set_if_value(entry, "filetype", filetype)
            metadata_changed |= set_if_value(entry, "goodreads_meta", goodreads_meta)
            metadata[key] = entry

        # Only write to disk if something actually changed
        if metadata_changed:
            try:
                LIBRARY_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
                LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
                # Keep in-memory library metadata cache in sync with disk
                global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME
                _LIBRARY_METADATA_CACHE = metadata
                try:
                    _LIBRARY_METADATA_MTIME = LIBRARY_METADATA_PATH.stat().st_mtime
                except OSError:
                    _LIBRARY_METADATA_MTIME = 0.0
            except Exception:
                logger.exception("Failed to save library metadata to %s", LIBRARY_METADATA_PATH)


def enrich_library_metadata_from_goodreads(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fast metadata enrichment from Goodreads only (no Anna's Archive search).
    Used during background metadata refresh on existing library books.

    - Searches Goodreads for the book
    - Extracts cover, rating, description, genres directly from Goodreads
    - No Anna's Archive searching needed since files already exist

    Skips enrichment if:
    - Description > 500 chars (rich metadata already exists)
    - Rating already present (don't re-scrape)
    - 3+ genres already present (sufficient categorization)
    """
    library_id = entry["id"]
    metadata = load_library_metadata()
    meta: Dict[str, Any] = dict(metadata.get(library_id) or {})

    # Ensure basics are set
    meta.setdefault("id", library_id)
    meta.setdefault("title", entry.get("title", ""))
    meta.setdefault("author", entry.get("author", ""))
    meta.setdefault("path", entry.get("path", ""))
    meta.setdefault("filetype", entry.get("filetype", ""))
    meta.setdefault("cover", entry.get("cover", "") or meta.get("cover", ""))

    # Check skip conditions - if already has good metadata in goodreads_meta, skip enrichment
    goodreads_meta_existing = meta.get("goodreads_meta", {}) or {}
    has_genres = bool(goodreads_meta_existing.get("genres"))
    has_rating = goodreads_meta_existing.get("rating") is not None
    has_description = bool(meta.get("description") or goodreads_meta_existing.get("description"))
    has_url = bool(goodreads_meta_existing.get("goodreads_url"))
    
    # Only skip if ALL metadata fields are present and complete
    if has_genres and has_rating and has_description and has_url:
        # Already has complete metadata, skip enrichment
        logger.debug("Skipping metadata enrichment for %s: complete metadata already present", library_id)
        return meta

    try:
        title = entry.get("title", "").strip()
        author = (entry.get("author") or "").strip()

        if not title:
            return meta

        # Try to find Goodreads link
        gr_link = meta.get("goodreads_link")

        if not gr_link:
            # Search Goodreads directly for the link
            try:
                import requests
                search_url = f"https://www.goodreads.com/search?q={requests.utils.quote(f'{title} {author}'.strip())}"

                resp = requests.get(
                    search_url,
                    timeout=5,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0",
                        "Accept-Language": "en-US,en;q=0.9"
                    }
                )
                if resp.status_code == 200:
                    from lxml import html as _html
                    tree = _html.fromstring(resp.text)
                    links = tree.cssselect("a.bookTitle")
                    for link in links:
                        href = link.get("href")
                        if href:
                            # Skip study guides and audiobooks
                            if "study-guide" not in href.lower() and "audiobook" not in href.lower():
                                gr_link = href
                                if not gr_link.startswith("http"):
                                    gr_link = "https://www.goodreads.com" + gr_link
                                if gr_link:
                                    meta["goodreads_link"] = gr_link
                                    logger.debug("Found Goodreads link for %s: %s", title, gr_link)
                                    break
            except Exception as e:
                logger.debug("Failed to search Goodreads for %s: %s", title, e)

        # If we have a Goodreads link, scrape it for rich metadata
        if gr_link:
            try:
                from parser_engine import FeedParser
                from pathlib import Path as _Path
                cache_path = _Path.home() / ".feed_metadata"
                parser = FeedParser(cache_path)
                debug_log_scrape = []
                scraped_meta = parser._scrape_goodreads_book(gr_link, debug_log_scrape)

                if scraped_meta:
                    # Update metadata with scraped data - only pull missing fields
                    # Skip rating/rating_count if already present
                    if scraped_meta.get("rating") and not has_rating:
                        meta["rating"] = scraped_meta["rating"]
                        logger.debug("Goodreads rating for %s: %s", gr_link, scraped_meta["rating"])
                    if scraped_meta.get("rating_count") and not has_rating:
                        meta["rating_count"] = scraped_meta["rating_count"]
                    # Skip pages if already present
                    if scraped_meta.get("pages") and not meta.get("pages"):
                        meta["pages"] = scraped_meta["pages"]
                    # Skip genres if already has 3+ genres
                    if scraped_meta.get("genres") and not has_many_genres:
                        meta["genres"] = scraped_meta["genres"]
                        logger.debug("Goodreads genres for %s: %s", gr_link, scraped_meta["genres"])
                    # Skip language if already present
                    if scraped_meta.get("edition_language") and not meta.get("language"):
                        meta["language"] = scraped_meta["edition_language"]
                    # Skip publish_date if already present
                    if scraped_meta.get("edition_published") and not meta.get("publish_date"):
                        meta["publish_date"] = scraped_meta["edition_published"]
                    # Skip format if already present
                    if scraped_meta.get("edition_format") and not meta.get("format"):
                        meta["format"] = scraped_meta["edition_format"]
                    # Skip cover if already has high-res cover (with _SX in URL)
                    if scraped_meta.get("cover") and (not meta.get("cover") or "_SX" not in str(meta.get("cover", ""))):
                        # Cache the cover locally for email use
                        cached_path = cache_cover_locally(scraped_meta["cover"], library_id)
                        if cached_path:
                            # Store relative path to local cache file
                            meta["cover"] = str(cached_path.relative_to(DATA_DIR.parent))
                            logger.debug("Cached Goodreads cover for %s to: %s", gr_link, meta["cover"])
                        else:
                            # Fallback to URL if caching failed
                            meta["cover"] = scraped_meta["cover"]
                            logger.debug("Goodreads cover for %s: %s", gr_link, scraped_meta["cover"])
                    # Skip description if already has one > 100 chars
                    if scraped_meta.get("description") and len(str(meta.get("description", ""))) < 100:
                        meta["description"] = fix_description_spacing(scraped_meta["description"])
            except Exception as e:
                logger.debug("Failed to scrape Goodreads metadata for %s: %s", gr_link, e)

        # Note: Cover extraction is already handled in scraping above (lines 3715-3725)
        # No need for second search - if we found a gr_link, scraping already got the cover
        # If scraping didn't get a cover, it won't help to search again

    except Exception:
        logger.exception("Failed to enrich library metadata from Goodreads for %s", library_id)

    # Build goodreads_meta object
    cover_for_meta = meta.get("cover", "")
    # Ensure cover is not from Anna's Archive or other piracy sites
    forbidden_domains = [
        "cdn-zlib", "zlib.sk", "z-lib", "libgen", "anna", "annas-archive",
        "bookfi", "b-ok", "manybooks"
    ]
    cover_lower = cover_for_meta.lower()
    if any(domain in cover_lower for domain in forbidden_domains):
        cover_for_meta = ""

    goodreads_meta = {
        "description": meta.get("description", ""),
        "rating": meta.get("rating"),
        "rating_count": meta.get("rating_count"),
        "pages": meta.get("pages"),
        "genres": meta.get("genres", []),
        "edition_language": meta.get("language", ""),
        "edition_published": meta.get("publish_date", ""),
        "edition_format": meta.get("format", ""),
        "cover": cover_for_meta,
        "goodreads_url": meta.get("goodreads_link", ""),
    }
    meta["goodreads_meta"] = goodreads_meta

    # Persist updates
    metadata[library_id] = meta
    try:
        with library_metadata_lock:
            LIBRARY_METADATA_PATH.write_text(
                json.dumps(metadata, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME
            _LIBRARY_METADATA_CACHE = metadata
            try:
                _LIBRARY_METADATA_MTIME = LIBRARY_METADATA_PATH.stat().st_mtime
            except OSError:
                _LIBRARY_METADATA_MTIME = 0.0
    except Exception:
        logger.exception("Failed to write library metadata to disk")

    return meta


def ensure_library_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Ensure we have a reasonably rich metadata block for a library entry.

    - Start from whatever is in library_metadata.json (if anything)
    - Fill in basics from the entry (title, author, path, filetype, cover)
    - If description / goodreads_link / genres / language / publish_date / rating
      are missing, try to backfill using the search cache + AnnaSource.
    - Persist any improvements back to library_metadata.json.
    """
    library_id = entry["id"]
    metadata = load_library_metadata()  # safe read with the existing helper
    meta: Dict[str, Any] = dict(metadata.get(library_id) or {})

    # ---------- 1) Always make sure basics are set ----------
    meta.setdefault("id", library_id)
    meta.setdefault("title", entry.get("title", ""))
    meta.setdefault("author", entry.get("author", ""))
    meta.setdefault("path", entry.get("path", ""))
    meta.setdefault("filetype", entry.get("filetype", ""))
    meta.setdefault("cover", entry.get("cover", "") or meta.get("cover", ""))

    # ---------- 2) Decide if we need enrichment ----------
    needs_rich_fields = (
        not meta.get("description") or
        not meta.get("goodreads_link") or
        not meta.get("genres") or
        not meta.get("language") or
        not meta.get("publish_date") or
        meta.get("rating") in (None, 0, 0.0)
    )

    if needs_rich_fields:
        try:
            author = entry.get('author', '')
            # Deduplicate author if needed
            if author:
                from parser_engine import FeedParser
                from pathlib import Path as PathlibPath
                temp_parser = FeedParser(PathlibPath.home() / ".feed_metadata")
                author = history_manager.cleanup_author(author)

            query = f"{entry.get('title', '')} {author}".strip()
            if query:
                # Make a small, format-aware search
                allowed_formats = [entry.get("filetype", "epub") or "epub"]

                # Pick *some* Kindle type – use first user if we have one
                try:
                    settings = settings_manager.settings
                except (AttributeError, NameError):
                    settings = None
                kindle_type = "standard"
                if settings and getattr(settings, "users", None):
                    kindle_type = settings.users[0].kindle_type or "standard"

                options = SearchOptions(
                    query=query,
                    language="en",
                    extensions=allowed_formats,
                    autodownload=False,
                    preferred_formats=allowed_formats,
                    kindle_type=kindle_type,
                    resolve_downloads=False,  # Don't fetch AA detail pages; we only need metadata
                )
                results, _debug = search_with_cache(
                    query,
                    options,
                    persist=True,
                )
                best = select_best_result(
                    results,
                    allowed_formats,
                    kindle_type,
                    expected_title=entry.get("title"),
                    expected_author=entry.get("author"),
                )

                if best:
                    # Description (strip HTML if present)
                    desc = best.get("description") or meta.get("description", "")
                    if desc:
                        meta["description"] = fix_description_spacing(strip_html_tags(desc).strip())

                    # Goodreads link
                    gr_link = (
                        best.get("goodreads_link")
                        or best.get("goodreads_url")
                        or meta.get("goodreads_link")
                    )
                    # Filter out study guides and audiobooks from search results
                    if gr_link and ("study-guide" in gr_link.lower() or "audiobook" in gr_link.lower()):
                        gr_link = None

                    # Normalize relative URLs to absolute
                    if gr_link and not gr_link.startswith("http"):
                        gr_link = "https://www.goodreads.com" + gr_link
                    if gr_link:
                        meta["goodreads_link"] = gr_link

                    # If we don't have a Goodreads link yet, try to find it using search query
                    if not gr_link and (meta.get("title") or entry.get("title")):
                        try:
                            import requests
                            title = meta.get("title") or entry.get("title")
                            author = meta.get("author") or entry.get("author") or ""

                            # Build a simple search URL for Goodreads
                            # This attempts to find the book's Goodreads page
                            search_url = f"https://www.goodreads.com/search?q={requests.utils.quote(f'{title} {author}'.strip())}"

                            # Try to fetch and parse the first result's link
                            resp = requests.get(
                                search_url,
                                timeout=5,
                                headers={
                                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0",
                                    "Accept-Language": "en-US,en;q=0.9"
                                }
                            )
                            if resp.status_code == 200:
                                from lxml import html as _html
                                tree = _html.fromstring(resp.text)
                                # Look for first book result link (exclude study guides)
                                links = tree.cssselect("a.bookTitle")
                                for link in links:
                                    href = link.get("href")
                                    if href:
                                        # Skip study guides and other non-book results
                                        if "study-guide" not in href.lower() and "audiobook" not in href.lower():
                                            gr_link = href
                                            # Convert relative URLs to absolute
                                            if gr_link and not gr_link.startswith("http"):
                                                gr_link = "https://www.goodreads.com" + gr_link
                                            if gr_link:
                                                meta["goodreads_link"] = gr_link
                                                logger.debug("Found Goodreads link for %s: %s", title, gr_link)
                                                break
                        except Exception as e:
                            logger.debug("Failed to search for Goodreads link: %s", e)

                    # If we have a Goodreads link and don't have detailed metadata,
                    # scrape it now to get ratings, pages, edition details, etc.
                    if gr_link and not meta.get("rating"):
                        try:
                            from parser_engine import FeedParser
                            from pathlib import Path
                            cache_path = Path.home() / ".feed_metadata"
                            parser = FeedParser(cache_path)
                            debug_log_scrape = []
                            scraped_meta = parser._scrape_goodreads_book(gr_link, debug_log_scrape)
                            if scraped_meta:
                                # Update metadata with scraped Goodreads data
                                if scraped_meta.get("rating"):
                                    meta["rating"] = scraped_meta["rating"]
                                    logger.info("Scraped rating for %s: %s", gr_link, scraped_meta["rating"])
                                if scraped_meta.get("rating_count"):
                                    meta["rating_count"] = scraped_meta["rating_count"]
                                if scraped_meta.get("pages"):
                                    meta["pages"] = scraped_meta["pages"]
                                if scraped_meta.get("genres"):
                                    meta["genres"] = scraped_meta["genres"]
                                if scraped_meta.get("edition_language"):
                                    meta["language"] = scraped_meta["edition_language"]
                                if scraped_meta.get("edition_published"):
                                    meta["publish_date"] = scraped_meta["edition_published"]
                                if scraped_meta.get("edition_format"):
                                    meta["format"] = scraped_meta["edition_format"]
                                if scraped_meta.get("cover"):
                                    meta["cover"] = scraped_meta["cover"]
                                if scraped_meta.get("description"):
                                    meta["description"] = fix_description_spacing(scraped_meta["description"])
                                    logger.info("Scraped description for %s: %d chars", gr_link, len(scraped_meta["description"]))
                                else:
                                    logger.debug("No description found in Goodreads scrape for %s", gr_link)
                            else:
                                logger.debug("Goodreads scraping returned empty result for %s", gr_link)
                        except Exception as e:
                            logger.exception("Failed to scrape Goodreads metadata for %s: %s", gr_link, e)

                    # Cover - prefer Goodreads cover if available, reject Anna's Archive covers
                    goodreads_cover = (best.get("goodreads_meta") or {}).get("cover", "")
                    if goodreads_cover:
                        logger.debug("Using Goodreads cover for: %s by %s", entry.get("title"), entry.get("author"))
                        meta["cover"] = goodreads_cover
                    elif best.get("cover"):
                        # Only accept covers from legitimate sources, reject piracy sites
                        cover = best.get("cover")
                        forbidden_domains = [
                            "cdn-zlib", "zlib.sk", "z-lib", "libgen", "anna", "annas-archive",
                            "bookfi", "b-ok", "manybooks"
                        ]
                        if not any(domain in cover.lower() for domain in forbidden_domains):
                            logger.debug("Using search result cover for: %s by %s", entry.get("title"), entry.get("author"))
                            meta["cover"] = cover
                        else:
                            logger.debug("Rejected piracy site cover for: %s by %s (domain: %s)", entry.get("title"), entry.get("author"), cover[:60])
                    
                    # If still no cover, try z-lib as fallback
                    if not meta.get("cover"):
                        logger.debug("No cover found from Goodreads/search, trying z-lib fallback for: %s by %s", 
                                    entry.get("title"), entry.get("author"))
                        zlib_cover = fetch_zlib_cover_fallback(entry.get("title", ""), entry.get("author", ""))
                        if zlib_cover:
                            logger.info("Using z-lib fallback cover for: %s by %s", entry.get("title"), entry.get("author"))
                            meta["cover"] = zlib_cover
                        else:
                            logger.debug("z-lib fallback also failed for: %s by %s", entry.get("title"), entry.get("author"))

                    # Genres (ensure list)
                    genres = best.get("genres") or meta.get("genres") or []
                    if isinstance(genres, str):
                        genres = [g.strip() for g in genres.split(",") if g.strip()]
                    meta["genres"] = genres

                    # Language
                    lang = best.get("language") or meta.get("language")
                    if lang:
                        meta["language"] = lang

                    # Publish date / year
                    pub = (
                        best.get("publish_year")
                        or best.get("publish_date")
                        or meta.get("publish_date")
                    )
                    if pub:
                        meta["publish_date"] = str(pub)

                    # Rating
                    rating = best.get("rating")
                    if rating is not None:
                        try:
                            meta["rating"] = float(rating)
                        except (TypeError, ValueError):
                            pass

        except Exception:
            logger.exception(
                "Failed to enrich library metadata for id=%s title=%s",
                library_id,
                entry.get("title"),
            )


    # ---------- 3) Build goodreads_meta object from individual fields ----------
    # Construct a goodreads_meta dict from the fields we have
    cover_for_meta = meta.get("cover", "")
    # Ensure cover is not a zlib URL
    if "cdn-zlib" in cover_for_meta.lower() or "zlib.sk" in cover_for_meta.lower():
        cover_for_meta = ""

    goodreads_meta = {
        "description": meta.get("description", ""),
        "rating": meta.get("rating"),
        "rating_count": meta.get("rating_count"),
        "pages": meta.get("pages"),
        "genres": meta.get("genres", []),
        "edition_language": meta.get("language", ""),
        "edition_published": meta.get("publish_date", ""),
        "edition_format": meta.get("format", ""),
        "cover": cover_for_meta,
        "goodreads_url": meta.get("goodreads_link", ""),
    }
    meta["goodreads_meta"] = goodreads_meta


    # ---------- 3) Persist any updates ----------
    # (We already loaded metadata once; just overwrite this entry and write it back)
    metadata[library_id] = meta
    try:
        with library_metadata_lock:
            LIBRARY_METADATA_PATH.write_text(
                json.dumps(metadata, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            # Keep in-memory library metadata cache in sync with disk
            global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME
            _LIBRARY_METADATA_CACHE = metadata
            try:
                _LIBRARY_METADATA_MTIME = LIBRARY_METADATA_PATH.stat().st_mtime
            except OSError:
                _LIBRARY_METADATA_MTIME = 0.0
    except Exception:
        logger.exception("Failed to write library metadata to disk")

    return meta


def send_kindle_batch_email(
    smtp_config,
    user: UserSettings,
    downloads: List[tuple[Path, Dict]],
    max_bytes: int = 20 * 1024 * 1024,
    kindle_email: str = "",
):
    """
    Send one or more Kindle emails for all downloads of a user in a feed run,
    grouping multiple attachments into each email up to ~max_bytes (~20MB).
    Creates temp copies with spaces removed from filenames (Amazon requirement).

    If kindle_email is not provided, falls back to user.kindle_email, then global settings.
    """
    if not downloads or not smtp_config.is_configured():
        return

    # Determine the email address to use
    if not kindle_email:
        # Try per-user email first
        if user.kindle_email:
            kindle_email = user.kindle_email
        else:
            # Try global setting
            settings = settings_manager.settings
            if settings.kindle_emails:
                kindle_emails_list = [e.strip() for e in settings.kindle_emails.split(",") if e.strip()]
                if kindle_emails_list:
                    kindle_email = kindle_emails_list[0]  # Use first global email

    if not kindle_email:
        return

    def flush_batch(batch: List[tuple[Path, Dict]]):
        if not batch:
            return

        msg = EmailMessage()
        msg["From"] = smtp_config.from_email
        msg["To"] = user.kindle_email

        if len(batch) == 1:
            title = batch[0][1].get("title") or batch[0][0].name
            msg["Subject"] = title
            body_lines = [
                f"Here is your book: {title}",
                "",
                "Sent via CodexBooks feeder.",
            ]
        else:
            titles = [result.get("title") or path.name for path, result in batch]
            msg["Subject"] = f"{len(batch)} new books from CodexBooks feeder"
            body_lines = ["Here are your books:", ""]
            body_lines.extend(f"- {t}" for t in titles)

        msg.set_content("\n".join(body_lines))

        # Track temp files to clean up
        temp_files = []

        for saved_path, result in batch:
            # Convert to EPUB if needed for Kindle compatibility
            file_to_send = saved_path

            fmt = saved_path.suffix.lower().lstrip(".")
            if fmt != "epub":
                logger.info(f"send_kindle_batch_email: Converting {fmt} to EPUB")
                temp_dir = DATA_DIR / "temp"
                temp_dir.mkdir(exist_ok=True)
                temp_epub = temp_dir / f"{saved_path.stem}_{uuid.uuid4().hex[:8]}.epub"

                try:
                    file_to_send = convert_to_epub(saved_path, temp_epub)
                    temp_files.append(temp_epub)
                    logger.info(f"send_kindle_batch_email: Converted {saved_path.name} to {file_to_send.name}")
                except Exception as e:
                    logger.error(f"send_kindle_batch_email: Conversion failed: {e}, using original")
                    file_to_send = saved_path

            with file_to_send.open("rb") as f:
                data = f.read()
            # Sanitize filename for Kindle compatibility (fixes E999 error)
            safe_filename = sanitize_filename_for_kindle(file_to_send.name)
            msg.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=safe_filename,
            )

        try:
            smtp_config.send(msg)
            total_mb = sum(p.stat().st_size for p, _ in batch) / (1024 * 1024)
            logger.info(
                "Sent Kindle batch (%d books, %.1f MB) to %s",
                len(batch),
                total_mb,
                user.kindle_email,
            )
        except Exception:
            logger.exception("Failed to send Kindle batch email for user=%s", user.name)
        finally:
            # Clean up all temp files
            for temp_path in temp_files:
                try:
                    temp_path.unlink()
                    logger.debug("Cleaned up temp Kindle batch file: %s", temp_path.name)
                except Exception as e:
                    logger.warning("Failed to delete temp Kindle batch file %s: %s", temp_path.name, e)

    current_batch: List[tuple[Path, Dict]] = []
    current_size = 0

    for saved_path, result in downloads:
        try:
            size = saved_path.stat().st_size
        except OSError:
            logger.warning("File no longer exists for batch send: %s", saved_path)
            continue
        # If adding this file would push us over max_bytes, flush current batch
        if current_batch and (current_size + size) > max_bytes:
            flush_batch(current_batch)
            current_batch = []
            current_size = 0

        current_batch.append((saved_path, result))
        current_size += size

    # Flush remainder
    flush_batch(current_batch)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/cover.png")
def navbar_cover():
    """Serve the GoodBooks cover image for navbar branding."""
    cover_path = BASE_DIR / "GoodBooks.epub"
    if not cover_path.exists():
        # Return a placeholder or 404
        return "", 404

    # Extract cover image from EPUB
    try:
        import zipfile
        with zipfile.ZipFile(cover_path, 'r') as z:
            # Try to get the cover image from the EPUB
            for name in z.namelist():
                if 'cover' in name.lower() and name.endswith(('.png', '.jpg', '.jpeg')):
                    return send_file(
                        z.open(name),
                        mimetype='image/png' if name.endswith('.png') else 'image/jpeg',
                        as_attachment=False
                    )
    except Exception as e:
        logger.warning(f"Failed to extract cover from EPUB: {e}")

    return "", 404

@app.route("/library/recently-added")
def library_recently_added():
    """Show recently added books to the library with filtering options."""
    try:
        from datetime import datetime
        from pathlib import Path
        
        limit = request.args.get("limit", 50, type=int)
        limit = max(1, min(limit, 500))  # Clamp to 1-500
        
        # Get source filter (feed URL) - optional
        source_filter = request.args.get("source", "").strip()

        # Load all entries
        entries_all = build_library_entries()

        # Helper to extract date from entry
        def get_date_added(entry):
            # Try multiple date fields in order of preference
            for field in ['added_date', 'timestamp', 'date_added']:
                if field in entry and entry[field]:
                    try:
                        if isinstance(entry[field], str):
                            # Parse ISO format datetime
                            if entry[field].endswith('Z'):
                                return datetime.fromisoformat(entry[field].replace('Z', '+00:00'))
                            else:
                                return datetime.fromisoformat(entry[field])
                        return entry[field]
                    except Exception:
                        continue
            # Fallback to mtime from path
            if 'path' in entry:
                try:
                    p = Path(entry['path'])
                    if p.exists():
                        return datetime.fromtimestamp(p.stat().st_mtime)
                except Exception:
                    pass
            return None

        # Apply source filter if specified
        if source_filter:
            entries_all = [e for e in entries_all if e.get('source') == source_filter]

        # Filter out entries without dates and sort
        entries_with_dates = [(e, get_date_added(e)) for e in entries_all]
        entries_with_dates = [(e, d) for e, d in entries_with_dates if d is not None]
        entries_with_dates.sort(key=lambda x: x[1], reverse=True)
        entries_recent = [e for e, d in entries_with_dates[:limit]]

        # Collect unique sources for filter dropdown
        all_sources = sorted(set(e.get('source') for e in entries_all if e.get('source')))

        return render_template(
            "recently_added.html",
            settings=settings_manager.settings,
            books=entries_recent,
            total=len(entries_recent),
            limit=limit,
            source_filter=source_filter,
            available_sources=all_sources,
        )
    except Exception as e:
        logger.exception("Error loading recently added books: %s", e)
        flash("Error loading recently added books", "danger")
        return redirect(url_for("index"))



@app.route("/")
def index():
    """
    Library home page with dual view modes.

    View modes:
      * 'folder': Hierarchical folder navigation (original behavior)
      * 'collection': Flat collection of all books from all folders

    Phase 8:
      * Hierarchical folder navigation using a 'prefix' query param.
      * Filters by genre, author, and direct-download-only.
      * Sorting + pagination over file entries.
      * When filters are active, we show a flat view (no folder cards) for the
        current folder subtree.
    """
    settings = settings_manager.settings

    # View mode (folder vs collection)
    view_mode = request.args.get("view", "folder").strip().lower()
    if view_mode not in {"folder", "collection"}:
        view_mode = "folder"

    # Sorting: query param overrides default
    sort_key = request.args.get("sort", "").strip() or getattr(
        settings, "library_default_sort", "date_newest"
    )
    if sort_key not in LIBRARY_SORT_MODES:
        sort_key = "date_newest"

    # Folder prefix within library roots (e.g. "Sci-Fi", "Sci-Fi/Asimov")
    prefix = request.args.get("prefix", "").strip()

    # Filters
    genre_filter = request.args.get("genre", "").strip()
    author_filter = request.args.get("author", "").strip()
    search_query = request.args.get("search", "").strip().lower()
    direct_only = request.args.get("direct_only", "") == "on"

    # Pagination
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    if page < 1:
        page = 1

    # Results per page (from query param or settings default)
    try:
        per_page = int(request.args.get("per_page", "").strip() or "0")
    except ValueError:
        per_page = 0
    if per_page not in {15, 25, 50, 100, 200, 500}:
        per_page = max(1, int(getattr(settings, "library_items_per_page", 50) or 50))

    # Load all entries + narrow to the current prefix subtree (if in folder mode)
    entries_all = build_library_entries()

    def under_prefix(entry: Dict) -> bool:
        if view_mode == "collection":
            # Collection mode: all files are in scope
            return True
        # Folder mode: check prefix
        rel = entry.get("relpath", "")
        if not prefix:
            # Root: everything is in scope
            return True
        # Under prefix if rel == prefix or starts with "prefix/"
        return rel == prefix or rel.startswith(prefix + "/")

    entries_in_scope = [e for e in entries_all if under_prefix(e)]

    # Lazy-enrich metadata for entries missing genres (async in background)
    # This allows genre filtering to work without waiting for full Goodreads lookup
    def enrich_genres_lazy():
        global _LIBRARY_ENRICHMENT_IN_PROGRESS
        try:
            # Don't start enrichment if already in progress (prevents hammering with concurrent requests)
            if _LIBRARY_ENRICHMENT_IN_PROGRESS:
                logger.debug("Genre enrichment already in progress, skipping new enrichment request")
                return
            
            _LIBRARY_ENRICHMENT_IN_PROGRESS = True
            logger.debug("Starting background genre enrichment for %d entries", len(entries_in_scope))
            
            # Add a small delay so enrichment only starts if user stays on page
            import time
            time.sleep(2)
            
            for idx, e in enumerate(entries_in_scope):
                if not e.get("genres"):
                    try:
                        meta = ensure_library_metadata(e)
                        if meta.get("genres"):
                            e["genres"] = meta.get("genres")
                            logger.debug("Enriched genres for entry %d/%d: %s", idx+1, len(entries_in_scope), e.get("title", "?"))
                    except Exception as e2:
                        logger.debug("Failed to enrich genres for entry: %s", e2)
            
            logger.info("Completed background genre enrichment for library")
        finally:
            _LIBRARY_ENRICHMENT_IN_PROGRESS = False

    # Run enrichment in background thread so UI doesn't block
    # Only start if no enrichment is already in progress
    try:
        if not _LIBRARY_ENRICHMENT_IN_PROGRESS:
            enrich_thread = threading.Thread(
                target=enrich_genres_lazy,
                daemon=True,
                name="library-genres-enrich"
            )
            enrich_thread.start()
    except Exception as e:
        logger.debug("Failed to start genre enrichment thread: %s", e)

    # Build filter option sets (within current folder subtree, recursing 8 levels deep)
    genre_set = set()
    author_set = set()
    for e in entries_in_scope:
        # Genres may be a string or list
        genres = e.get("genres")
        if isinstance(genres, str):
            if genres:
                genre_set.add(genres)
        elif isinstance(genres, (list, tuple)):
            for g in genres:
                if g:
                    genre_set.add(str(g))
        author = e.get("author")
        if author:
            author_set.add(author)

    # Filter out adult/explicit genres
    genre_set = {g for g in genre_set if is_genre_allowed(g)}
    genre_options = sorted(genre_set, key=lambda s: s.casefold())
    author_options = sorted(author_set, key=lambda s: s.casefold())

    # Apply filters
    filtered_entries = entries_in_scope

    if genre_filter:
        def has_genre(entry: Dict) -> bool:
            g = entry.get("genres")
            if isinstance(g, str):
                return g == genre_filter
            if isinstance(g, (list, tuple)):
                return genre_filter in g
            return False

        filtered_entries = [e for e in filtered_entries if has_genre(e)]

    if author_filter:
        filtered_entries = [
            e for e in filtered_entries if e.get("author") == author_filter
        ]

    if search_query:
        filtered_entries = [
            e for e in filtered_entries if (
                search_query in (e.get("title", "") or "").lower() or
                search_query in (e.get("author", "") or "").lower()
            )
        ]

    if direct_only:
        filtered_entries = [e for e in filtered_entries if e.get("is_direct")]

    filters_active = bool(genre_filter or author_filter or direct_only or search_query)

    # ------------------------------------------------------------------
    # Collection view: always show flat list
    # ------------------------------------------------------------------
    folder_cards: List[Dict] = []
    if view_mode == "collection":
        entries_sorted = sort_library_entries(filtered_entries, sort_key)
        total_items = len(entries_sorted)
        total_pages = max(1, (total_items + per_page - 1) // per_page)
        if page > total_pages:
            page = total_pages
        start = (page - 1) * per_page
        end = start + per_page
        page_entries = entries_sorted[start:end]

    # ------------------------------------------------------------------
    # Folder view: hierarchical with optional flat view when filters active
    # ------------------------------------------------------------------
    else:
        if filters_active:
            entries_sorted = sort_library_entries(filtered_entries, sort_key)
            total_items = len(entries_sorted)
            total_pages = max(1, (total_items + per_page - 1) // per_page)
            if page > total_pages:
                page = total_pages
            start = (page - 1) * per_page
            end = start + per_page
            page_entries = entries_sorted[start:end]

        # ------------------------------------------------------------------
        # When filters are NOT active: hierarchical folder + file view
        # ------------------------------------------------------------------
        else:
            folders = set()
            files_here: List[Dict] = []

            for e in filtered_entries:
                rel = e.get("relpath", "")
                # Compute remainder path relative to current prefix
                if prefix:
                    if not rel.startswith(prefix + "/") and rel != prefix:
                        continue
                    if rel == prefix:
                        remainder = ""
                    else:
                        remainder = rel[len(prefix) + 1 :]
                else:
                    remainder = rel

                if not remainder:
                    # This is exactly the folder path itself; not a file
                    continue

                parts = remainder.split("/")
                if len(parts) == 1:
                    # File directly in this folder
                    files_here.append(e)
                else:
                    # Immediate subfolder
                    folders.add(parts[0])

            # Build folder cards (one card per immediate subfolder)
            folder_cards = []
            for folder_name in sorted(folders, key=lambda s: s.casefold()):
                if prefix:
                    sub_prefix = f"{prefix}/{folder_name}"
                else:
                    sub_prefix = folder_name

                # Generate composite cover image for folder
                cover = generate_folder_cover(sub_prefix, entries_all)

                folder_cards.append(
                    {
                        "name": folder_name,
                        "prefix": sub_prefix,
                        "cover": cover,  # Composite cover or None
                    }
                )

            entries_sorted = sort_library_entries(files_here, sort_key)
            # For folder view, total_items should count all titles recursively under current prefix
            # not just files directly in this folder
            if prefix:
                # Count all entries that start with this prefix
                total_recursive = len([e for e in entries_all if e.get("relpath", "").startswith(prefix)])
            else:
                # At root: count all entries
                total_recursive = len(entries_all)
            total_items = total_recursive
            total_pages = max(1, (total_items + per_page - 1) // per_page)
            if page > total_pages:
                page = total_pages
            start = (page - 1) * per_page
            end = start + per_page
            page_entries = entries_sorted[start:end]

    return render_template(
        "library.html",
        settings=settings,
        title="Library",
        entries=page_entries,
        folder_cards=folder_cards,
        page=page,
        total_pages=total_pages,
        sort_key=sort_key,
        sort_options=LIBRARY_SORT_MODES,
        total_items=total_items,
        per_page=per_page,
        prefix=prefix,
        genre_options=genre_options,
        author_options=author_options,
        genre_filter=genre_filter,
        author_filter=author_filter,
        direct_only=direct_only,
        filters_active=filters_active,

        view_mode=view_mode,
    )
def ensure_mobi_for_direct_download(src: Path) -> tuple[Path, Optional[Path]]:
    """
    Convert file to MOBI for direct download if needed.
    Returns tuple of (file_to_download, temp_file_to_cleanup)
    """
    fmt = src.suffix.lower().lstrip(".")
    if fmt == "mobi":
        return (src, None)

    if fmt not in ("epub", "azw", "azw3", "pdf", "html", "txt"):
        # Unsupported format, return original
        return (src, None)

    try:
        temp_dir = DATA_DIR / "temp"
        temp_dir.mkdir(exist_ok=True)
        temp_mobi = temp_dir / f"{src.stem}_{uuid.uuid4().hex[:8]}.mobi"

        logger.info(f"Converting {src.name} ({fmt}) to MOBI for download")
        result = convert_to_epub(src, temp_mobi)  # Use converthelper for now
        logger.info(f"Converted {src.name} to MOBI")
        return (result, result)
    except Exception as e:
        logger.warning(f"Failed to convert {src.name} to MOBI: {e}, serving original")
        return (src, None)


@app.route("/library/download/<path:entry_id>")
def library_direct_download(entry_id):
    """
    Directly download a library file to the browser.
    """
    entry = get_library_entry(entry_id)
    if not entry:
        flash("Book not found for download.", "warning")
        return redirect(url_for("index"))

    root = Path(entry["root"])
    relpath = Path(entry["relpath"])

    # Ensure root is one of the configured library roots
    allowed_roots = {r.resolve() for r in get_library_roots()}
    if root.resolve() not in allowed_roots:
        flash("Download path is not in configured library roots.", "danger")
        return redirect(url_for("index"))

    directory = root / relpath.parent
    filename = relpath.name
    file_to_serve = Path(directory) / filename
    file_to_download, temp_cleanup = ensure_mobi_for_direct_download(file_to_serve)

    try:
        return send_from_directory(
            file_to_download.parent,
            file_to_download.name,
            as_attachment=True
        )
    finally:
        if temp_cleanup and temp_cleanup.exists():
            try:
                temp_cleanup.unlink()
            except Exception:
                pass
@app.route("/library/send-to-kindle", methods=["POST"])
def library_send_to_kindle():
    """
    Send a library file to a selected user's Kindle address using the existing
    send_kindle_email pipeline.
    """
    entry_id = request.form.get("entry_id", "").strip()
    user_name = request.form.get("user_name", "").strip()

    if not entry_id or not user_name:
        flash("Missing book or user selection.", "danger")
        return redirect(url_for("index"))

    entry = get_library_entry(entry_id)
    if not entry:
        flash("Book not found in library.", "warning")
        return redirect(url_for("index"))

    user = next(
        (u for u in settings_manager.settings.users if u.name == user_name),
        None,
    )
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("index"))

    root = Path(entry["root"])
    relpath = Path(entry["relpath"])
    allowed_roots = {r.resolve() for r in get_library_roots()}
    if root.resolve() not in allowed_roots:
        flash("File path is not in a configured library folder.", "danger")
        return redirect(url_for("index"))

    file_path = (root / relpath).resolve()
    if not file_path.exists():
        flash("File no longer exists on disk.", "danger")
        return redirect(url_for("index"))

    metadata_all = load_library_metadata()
    meta = metadata_all.get(entry_id, {})

    result = {
        "title": entry.get("title"),
        "author": entry.get("author"),
        "selected_format": entry.get("filetype"),
        "cover": entry.get("cover") or meta.get("cover", ""),
        "description": meta.get("description", ""),
        "file_path": str(file_path),
        "library_id": entry_id,  # Include library_id for cached cover lookup
        "goodreads_meta": meta.get("goodreads_meta", {}),  # Include goodreads metadata
    }

    smtp_config = settings_manager.settings.smtp

    oversize = is_oversize_for_kindle(file_path)

    try:
        if oversize:
            logger.warning(
                "Library send: file %s is larger than 20MB; Kindle may reject it, attempting send anyway.",
                file_path,
            )
        send_kindle_email(smtp_config, user, file_path, result)
        # Optional: also send a notification email
        send_notification_email(smtp_config, user, result)

        if oversize:
            flash(
                f"Sent '{entry.get('title')}' to {user.kindle_email or user.name}, "
                "but the file is larger than 20MB so Kindle may reject it.",
                "warning",
            )
        else:
            flash(
                f"Sent '{entry.get('title')}' to {user.kindle_email or user.name}.",
                "success",
            )
    except Exception:
        logger.exception("Failed to send library item to Kindle")
        flash("Failed to send book to Kindle.", "danger")

    return redirect(url_for("index"))

@app.route("/send-goodbooks-to-kindle", methods=["POST"])
def send_goodbooks_to_kindle():
    """
    Generate and send the GoodBooks.epub file with current IP/port configuration
    to the specified user's Kindle address.
    """
    try:
        data = request.get_json() or {}
        user_name = data.get("user_name", "").strip()

        if not user_name:
            return jsonify({"error": "User name required"}), 400

        # Find the user in settings
        user = None
        for u in settings.users:
            if u.name == user_name:
                user = u
                break

        if not user:
            return jsonify({"error": f"User '{user_name}' not found"}), 404

        if not user.kindle_email:
            return jsonify({"error": f"User '{user_name}' has no Kindle email configured"}), 400

        # Get SMTP config
        smtp_config = settings.smtp
        if not smtp_config or not smtp_config.host:
            return jsonify({"error": "SMTP not configured"}), 400

        # Get current server IP and port
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_address = s.getsockname()[0]
            s.close()
        except Exception:
            ip_address = None

        # If IP detection failed, try to get from settings
        if not ip_address:
            try:
                ip_address = "192.168.0.9"  # Default fallback
            except Exception:
                ip_address = "192.168.0.9"

        # Get port from settings
        port = getattr(settings_manager.settings, "server_port", 5000)

        # Generate EPUB with current IP/port
        logger.info(f"Generating GoodBooks EPUB for {user_name} with IP={ip_address}, port={port}")
        cmd = [
            sys.executable,
            str(BASE_DIR / "build_epub_v2.py"),
            str(ip_address),
            str(port)
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                logger.error(f"EPUB build failed: {result.stderr}")
                return jsonify({"error": f"Failed to generate EPUB: {result.stderr}"}), 500
            logger.info(f"EPUB build output: {result.stdout}")
        except subprocess.TimeoutExpired:
            logger.error("EPUB build timed out")
            return jsonify({"error": "EPUB generation timed out"}), 500
        except Exception as e:
            logger.error(f"Failed to run EPUB build: {e}")
            return jsonify({"error": f"Failed to generate EPUB: {e}"}), 500

        # Get the generated EPUB file
        goodbooks_path = BASE_DIR / "GoodBooks.epub"
        if not goodbooks_path.exists():
            return jsonify({"error": "Generated GoodBooks.epub not found"}), 500

        result = {
            "title": "GoodBooks",
            "author": "GoodBooks Team",
            "file_path": str(goodbooks_path),
        }

        # Send to Kindle
        send_kindle_email(smtp_config, user, str(goodbooks_path), result)

        return jsonify({"success": True, "message": f"GoodBooks (IP: {ip_address}:{port}) sent to {user.kindle_email}"}), 200

    except Exception as e:
        logger.exception("Failed to send GoodBooks to Kindle")
        return jsonify({"error": str(e)}), 500

@app.route("/library/send-batch", methods=["POST"])
def library_send_batch():
    """
    Batch send selected library entries (and/or entire folders) to a user's
    Kindle address. Uses the same send_kindle_email / send_notification_email
    pipeline as the single-item endpoint.
    """
    user_name = request.form.get("user_name", "").strip()
    raw_entry_ids = request.form.get("entry_ids", "").strip()
    raw_folder_prefixes = request.form.get("folder_prefixes", "").strip()

    if not user_name:
        flash("Select a user to send to Kindle.", "danger")
        return redirect(url_for("index"))

    entry_ids = [e.strip() for e in (raw_entry_ids or "").split(",") if e.strip()]
    folder_prefixes = [
        p.strip() for p in (raw_folder_prefixes or "").split(",") if p.strip()
    ]

    if not entry_ids and not folder_prefixes:
        flash("No books or folders selected.", "warning")
        return redirect(url_for("index"))

    settings = settings_manager.settings
    user = next((u for u in settings.users if u.name == user_name), None)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("index"))

    # Build the set of entries to send
    all_entries = build_library_entries()
    by_id: Dict[str, Dict] = {e.get("id"): e for e in all_entries if e.get("id")}
    selected: Dict[str, Dict] = {}

    for eid in entry_ids:
        entry = by_id.get(eid)
        if entry:
            selected[eid] = entry

    def under_prefix(entry: Dict, prefix: str) -> bool:
        rel = entry.get("relpath", "")
        if not prefix:
            return False
        return rel == prefix or rel.startswith(prefix + "/")

    for prefix in folder_prefixes:
        for entry in all_entries:
            if under_prefix(entry, prefix):
                entry_id = entry.get("id")
                if entry_id:
                    selected[entry_id] = entry

    if not selected:
        flash("No matching books found for selection.", "warning")
        return redirect(url_for("index"))

    smtp_config = settings.smtp
    sent = 0
    errors = 0

    # Collect all files for batch Kindle send
    kindle_batch = []
    notification_entries = []

    for entry in selected.values():
        try:
            root = Path(entry["root"])
            relpath = Path(entry["relpath"])
            allowed_roots = {r.resolve() for r in get_library_roots()}
            if root.resolve() not in allowed_roots:
                logger.warning(
                    "Skipping library entry outside configured roots in batch send: %s",
                    entry.get("id"),
                )
                continue

            file_path = (root / relpath).resolve()
            if not file_path.exists():
                logger.warning(
                    "Skipping missing file in batch send: %s", file_path
                )
                continue

            metadata_all = load_library_metadata()
            meta = metadata_all.get(entry["id"], {})

            result = {
                "title": entry.get("title")
                or meta.get("title")
                or file_path.stem,
                "author": entry.get("author") or meta.get("author") or "",
                "cover": entry.get("cover") or meta.get("cover", ""),
                "source": "library",
                "download_url": "",
                "ext": file_path.suffix.lstrip(".").lower(),
                "description": meta.get("description", ""),
                "file_path": str(file_path),
                "library_id": entry.get("id"),
                "goodreads_meta": meta.get("goodreads_meta", {}),  # Include goodreads metadata
            }

            kindle_batch.append((file_path, result))
            notification_entries.append(result)
            sent += 1
        except Exception:
            errors += 1
            logger.exception(
                "Failed to process library item in batch send: %s",
                entry.get("id"),
            )

    # Send all files in one Kindle email (batched)
    if kindle_batch:
        try:
            logger.info("library_send_batch: Calling send_kindle_batch_email with %d files", len(kindle_batch))
            send_kindle_batch_email(smtp_config, user, kindle_batch)
            logger.info("library_send_batch: Sent batch Kindle email with %d books to %s", len(kindle_batch), user.name)
        except Exception as e:
            logger.exception("library_send_batch: Failed to send batch Kindle email for user=%s: %s", user.name, e)
            flash("Failed to send books to Kindle.", "danger")

    # Send notification email for all books (batched with cover images)
    if notification_entries:
        try:
            logger.info("library_send_batch: Calling send_batch_notification_email with %d entries", len(notification_entries))
            send_batch_notification_email(
                smtp_config,
                user,
                notification_entries,
                sent_to_kindle=True  # Mark as sent to Kindle since we just sent the batch
            )
            logger.info("library_send_batch: Sent batch notification email with %d books", len(notification_entries))
        except Exception as e:
            logger.exception("library_send_batch: Failed to send batch notification email for user=%s: %s", user.name, e)

    if sent:
        flash(
            f"Sent {sent} book(s) to {user.kindle_email or user.name}.",
            "success",
        )
    if errors:
        flash(
            f"{errors} book(s) could not be sent.",
            "warning",
        )

    return redirect(url_for("index"))

@app.route("/library/delete-batch", methods=["POST"])
def library_delete_batch():
    """
    Batch delete selected library entries (and/or entire folders) from library
    metadata and optionally from the filesystem.
    """
    raw_entry_ids = request.form.get("entry_ids", "").strip()
    raw_folder_prefixes = request.form.get("folder_prefixes", "").strip()
    delete_files = request.form.get("delete_files", "0") == "1"

    entry_ids = [e.strip() for e in (raw_entry_ids or "").split(",") if e.strip()]
    folder_prefixes = [
        p.strip() for p in (raw_folder_prefixes or "").split(",") if p.strip()
    ]

    if not entry_ids and not folder_prefixes:
        flash("No books or folders selected.", "warning")
        return redirect(url_for("index"))

    # Build the set of entries to delete
    all_entries = build_library_entries()
    by_id: Dict[str, Dict] = {e.get("id"): e for e in all_entries if e.get("id")}
    selected: Dict[str, Dict] = {}

    for eid in entry_ids:
        entry = by_id.get(eid)
        if entry:
            selected[eid] = entry

    def under_prefix(entry: Dict, prefix: str) -> bool:
        rel = entry.get("relpath", "")
        if not prefix:
            return False
        return rel == prefix or rel.startswith(prefix + "/")

    for prefix in folder_prefixes:
        for entry in all_entries:
            if under_prefix(entry, prefix):
                entry_id = entry.get("id")
                if entry_id:
                    selected[entry_id] = entry

    if not selected:
        flash("No matching books found for selection.", "warning")
        return redirect(url_for("index"))

    # Delete from library metadata and optionally from filesystem
    deleted = 0
    errors = 0
    metadata = load_library_metadata()

    for entry in selected.values():
        try:
            entry_id = entry.get("id")
            if entry_id and entry_id in metadata:
                # Remove from metadata
                del metadata[entry_id]
                deleted += 1
                logger.info("Deleted library entry: %s", entry_id)

                # Optionally delete from filesystem
                if delete_files:
                    try:
                        root = Path(entry["root"])
                        relpath = Path(entry["relpath"])
                        allowed_roots = {r.resolve() for r in get_library_roots()}
                        if root.resolve() not in allowed_roots:
                            logger.warning(
                                "Skipping deletion outside configured roots: %s",
                                entry.get("id"),
                            )
                            continue

                        file_path = (root / relpath).resolve()
                        if file_path.exists():
                            file_path.unlink()
                            logger.info("Deleted file from filesystem: %s", file_path)
                    except Exception as e:
                        logger.exception(
                            "Failed to delete file from filesystem: %s", e
                        )
                        errors += 1
        except Exception as e:
            logger.exception("Failed to delete library entry: %s", e)
            errors += 1

    # Save updated metadata
    try:
        with library_metadata_lock:
            LIBRARY_METADATA_PATH.write_text(
                json.dumps(metadata, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME
            _LIBRARY_METADATA_CACHE = metadata
            try:
                _LIBRARY_METADATA_MTIME = LIBRARY_METADATA_PATH.stat().st_mtime
            except Exception:
                pass
    except Exception as e:
        logger.exception("Failed to save library metadata after delete: %s", e)
        errors += 1

    if deleted:
        flash(
            f"Deleted {deleted} book(s) from library" + 
            (" and filesystem" if delete_files else "") + ".",
            "success",
        )
    if errors:
        flash(
            f"{errors} book(s) could not be deleted.",
            "warning",
        )

    return redirect(url_for("index"))

@app.route("/api/users")
def api_users():
    """
    API endpoint to return list of configured users for frontend use.
    """
    try:
        users = [
            {
                "name": user.name,
                "kindle_email": user.kindle_email or "",
                "notification_email": user.notification_email or "",
            }
            for user in settings_manager.settings.users
        ]
        return jsonify({"success": True, "users": users})
    except Exception as e:
        logger.error("Failed to get users list: %s", e)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/add-genre-feed", methods=["POST"])
def api_add_genre_feed():
    """
    API endpoint to add a genre-based feed for a user.
    """
    try:
        data = request.get_json() or {}
        genre = data.get("genre", "").strip()
        user_name = data.get("user", "").strip()
        auto_kindle = data.get("auto_kindle", False)
        storage_location = data.get("storage_location", "").strip()

        if not genre or not user_name:
            return jsonify({"success": False, "error": "Missing genre or user"}), 400

        # Find user
        user = next(
            (u for u in settings_manager.settings.users if u.name == user_name),
            None,
        )
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        # Create a feed URL for the genre
        # We need to find the actual Goodreads list ID for this genre
        # For now, use a sensible default based on common genre list patterns
        genre_safe = genre.replace(" ", "-").lower()

        # Common mystery & thriller lists
        genre_list_ids = {
            "mystery": "8306",  # Thrillers You Must Read!
            "thriller": "8306",  # Thrillers You Must Read!
            "mystery-thriller": "8306",  # Thrillers You Must Read!
            "psychological-thriller": "194094",  # Best Psychological Thrillers
            "crime": "88796",  # Criminal/Forensic/Profiler/Psychiatrist
        }

        # Try to find matching list ID, default to 8306 if not found
        list_id = None
        for key, value in genre_list_ids.items():
            if key in genre_safe:
                list_id = value
                break

        if not list_id:
            # Fallback to a generic most-read list - use the Thrillers list as default
            list_id = "8306"

        feed_url = f"https://www.goodreads.com/list/show/{list_id}"

        # Create FeedSettings object
        from settings_manager import FeedSettings
        new_feed = FeedSettings(
            url=feed_url,
            mode="html",  # Use HTML mode for Goodreads list scraping
            filetypes=["epub", "mobi", "pdf"],  # Default formats
            save_dir=storage_location or "",
            auto_send_to_kindle=auto_kindle if auto_kindle else None,
        )

        # Add feed to user
        user.feeds.append(new_feed)

        # Save settings
        settings_manager.save()

        logger.info(
            "Added genre feed for user=%s genre=%s url=%s",
            user_name,
            genre,
            feed_url,
        )

        return jsonify({"success": True, "message": f"Feed added for {genre}"})
    except Exception as e:
        logger.exception("Failed to add genre feed")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/search-stream")
def search_stream():
    """
    Streaming search endpoint that returns results as JSON
    for progressive loading in the UI.
    """
    query = request.args.get("q", "").strip()
    user_id = request.args.get("user", "").strip()
    selected_language = request.args.get("lang", "en").strip() or "en"
    selected_ext = request.args.getlist("ext")

    if not query:
        return jsonify({"error": "No query provided"}), 400

    def generate():
        try:
            search_options = SearchOptions(
                query=query,
                language=selected_language,
                extensions=selected_ext,
                resolve_downloads=False,
                max_rows=45,
                max_results=45,
            )
            results, debug_log = source.search(query, options=search_options)

            # Send results in batches for progressive loading
            for i, result in enumerate(results):
                yield f"data: {json.dumps(result)}\n\n"
                if (i + 1) % 5 == 0:  # Send in batches of 5
                    time.sleep(0.01)  # Small delay to allow UI update
        except Exception as e:
            logger.exception("Search stream error")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/search")
def search():
    """
    Search page (moved from the old '/' route).

    Phase 3:
      * Cheap manual search:
          - Parse AA search table but do not hit detail pages.
      * Up to 45 ranked results, paginated server-side (15 per page).
      * Sources filter removed; we search all sources.
      * Autodownload + Auto-send to Kindle (requires user selected):
          - Select best result.
          - Resolve downloads lazily.
          - Download, record to history, upsert library metadata.
          - Optionally send Kindle + notification emails.
    """
    # Basic inputs
    query = request.args.get("q", "").strip()
    user_id = request.args.get("user", "").strip()
    selected_language = request.args.get("lang", "en").strip() or "en"
    selected_ext = request.args.getlist("ext")
    autodownload = request.args.get("autodownload", "0") in {"1", "on", "true"}
    autosend = request.args.get("autosend", "0") in {"1", "on", "true"}
    display_results = []
    total_pages = 0

    # Debug logging for autodownload
    if autodownload:
        logger.info(
            "Autodownload requested: query=%r user_id=%r lang=%r ext=%s autosend=%s",
            query, user_id, selected_language, selected_ext, autosend
        )
    # Pagination
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(page, 1)
    page_size = 15

    results: List[Dict] = []
    debug_log: List[str] = []

    # Resolve user + kindle_type
    kindle_type = ""
    user_obj = None
    if user_id:
        user_obj = next(
            (u for u in settings_manager.settings.users if u.name == user_id),
            None,
        )
        if user_obj:
            kindle_type = user_obj.kindle_type

    # Validate auto flags vs user selection
    if (autodownload or autosend) and not user_obj:
        logger.warning(
            "Autodownload/autosend requested but no valid user: user_id=%r user_obj=%s",
            user_id, user_obj
        )
        flash(
            "Select a user or disable Autodownload / Auto-send to Kindle.",
            "danger",
        )
        autodownload = False
        autosend = False

    # Validate autosend: require user with kindle_email and SMTP configured
    settings = settings_manager.settings
    if autosend and (not user_obj or not user_obj.kindle_email or not settings.smtp.is_configured()):
        logger.warning(
            "Auto-send to Kindle requested but user not properly configured: "
            "user_obj=%s kindle_email=%s smtp_configured=%s",
            bool(user_obj),
            user_obj.kindle_email if user_obj else "N/A",
            settings.smtp.is_configured()
        )
        flash(
            "Auto-send to Kindle requires a user with Kindle email configured and SMTP settings.",
            "warning",
        )
        autosend = False

    # Derive available extensions from cache + defaults
    base_ext = ["pdf", "epub", "mobi", "azw3"]
    extra_ext: set[str] = set()
    try:
        cache = _load_search_cache()
        for entry in cache.values():
            for res in entry.get("results") or []:
                for fmt in res.get("formats") or []:
                    fmt_lower = str(fmt).lower()
                    if fmt_lower and fmt_lower not in base_ext:
                        extra_ext.add(fmt_lower)
    except Exception:
        logger.exception("Failed to derive extensions from search cache")
    available_ext = base_ext + sorted(e for e in extra_ext if e not in base_ext)

    # Run search (cheap mode for manual UI)
    if query:
        logger.info("Running manual search for query=%r", query)
        try:
            # Use full search() method with SearchOptions (manual_search is broken)
            # Get up to 45 results with no ranking/download resolution
            search_options = SearchOptions(
                query=query,
                language=selected_language,
                extensions=selected_ext,
                max_rows=45,
                max_results=45,
                resolve_downloads=False,
            )
            results, debug_log = source.search(query, options=search_options)
            logger.info(
                "Manual search completed for query='%s' with %d ranked results",
                query,
                len(results),
            )

            # Optional: Autodownload + (optional) Auto-send
            if autodownload:
                logger.info("Autodownload check: autodownload=True user_obj=%s results_count=%d", 
                           bool(user_obj), len(results) if results else 0)
                if not user_obj:
                    logger.warning("Autodownload: user_obj is None, skipping")
                elif not results:
                    logger.warning("Autodownload: no search results, skipping")

            if autodownload and user_obj and results:
                logger.info("Autodownload: selected=%s results found=%d", bool(user_obj), len(results))
                # Pass query as expected_title to enable strict title matching when no explicit author provided
                best = select_best_result(results, selected_ext, kindle_type, expected_title=query)
                if not best:
                    logger.warning("Autodownload: select_best_result returned None")
                    flash("No suitable result found for autodownload.", "warning")
                else:
                    logger.info("Autodownload: selected best result id=%s title=%s", best.get("id"), best.get("title"))

                    # Check if file already exists in library by title/author
                    existing = find_book_in_library_by_title_author(
                        best.get("title", ""),
                        best.get("author", "")
                    )
                    if existing:
                        logger.info("Autodownload: file already exists in library: %s", existing.get("path"))
                        flash(f"Book already in library: {existing.get('title')}", "info")
                        return redirect(url_for("history"))

                    # Resolve downloads lazily now that we know which one to grab
                    try:
                        best = source.resolve_downloads_for_result(best)
                    except Exception as exc:
                        logger.exception("Autodownload: failed to resolve downloads")
                        flash(f"Autodownload failed while resolving links: {exc}", "danger")
                        return redirect(url_for("search", q=query))

                    settings = settings_manager.settings
                    dest_dir = resolve_download_dir(
                        user_obj.save_dir or settings.default_download_dir
                    )

                    # Choose a concrete file format to download. Prefer an explicit
                    # `selected_format` (set by select_best_result or UI), else
                    # fall back to the first available format listed on the result.
                    file_format = (
                        best.get("selected_format")
                        or (best.get("formats") or [None])[0]
                    )
                    if not file_format:
                        logger.warning("Autodownload: no format available for result %s", best.get("id"))
                        flash("Autodownload failed: no downloadable format available.", "warning")
                        return redirect(url_for("search", q=query))

                    try:
                        saved_path = source.download(best, file_format, dest_dir)
                        logger.info("Autodownload: successfully saved to %s", saved_path)
                    except Exception as exc:
                        logger.exception("Autodownload failed")
                        flash(f"Autodownload failed: {exc}", "danger")
                        return redirect(url_for("search", q=query))

                    cover = normalize_cover_url(best.get("cover", ""))
                    description = strip_html_tags(best.get("description", "")).strip()
                    history_manager.record(
                        user_obj.name,
                        best.get("title", saved_path.stem),
                        cover,
                        best.get("author", ""),
                        best.get("selected_format", ""),
                        "manual",
                        description,
                        str(saved_path),
                    )
                    upsert_library_metadata_for_download(saved_path, best)

                    # Add file_path and library_id to best dict for notification email cover extraction
                    best["file_path"] = str(saved_path)
                    entry_id = get_library_entry_id(saved_path)
                    if entry_id:
                        best["library_id"] = entry_id

                    oversize = is_oversize_for_kindle(saved_path)
                    if autosend and user_obj.kindle_email and settings.smtp.is_configured():
                        if oversize:
                            logger.warning(
                                "Autodownload: file %s is larger than 20MB; Kindle may reject it, attempting send anyway.",
                                saved_path,
                            )
                            flash(
                                "Autodownload file is larger than 20MB; Kindle may reject it, "
                                "but we attempted to send it anyway.",
                                "warning",
                            )
                        send_kindle_email(settings.smtp, user_obj, saved_path, best)
                    if user_obj.notification_email and settings.smtp.is_configured():
                        send_notification_email(settings.smtp, user_obj, best)

                    flash(
                        f"Downloaded {best.get('title', saved_path.name)}",
                        "success",
                    )
                    # Same UX as manual_download: land on History
                    return redirect(url_for("history"))

        except Exception as exc:
            logger.exception("Search failed for query '%s'", query)
            flash(f"Search failed: {exc}", "danger")

    # Pagination over returned results (up to 45)
    total_results = len(results)
    if total_results:
        total_pages = math.ceil(total_results / page_size)
        page = min(page, total_pages)
        start = (page - 1) * page_size
        end = start + page_size
        display_results = results[start:end]

    # Opportunistically hydrate covers/downloads for the current page of results
    if display_results:
        try:
            hydrated: List[Dict] = []
            for r in (display_results or []):
                try:
                    hydrated.append(source.resolve_downloads_for_result(r))
                except Exception:
                    hydrated.append(r)
            display_results = hydrated
        except Exception:
            logger.exception("Failed to hydrate search results with download metadata")

    return render_template(
        "index.html",
        settings=settings_manager.settings,
        title="Search",
        query=query,
        user_id=user_id,
        users=settings_manager.settings.users,
        selected_language=selected_language,
        selected_ext=selected_ext,
        autodownload=autodownload,
        autosend=autosend,
        available_ext=available_ext,
        results=display_results,
        debug_log=debug_log,
        page=page,
        total_pages=total_pages,
    )

@app.route("/book/random")
def random_books():
    """
    Select and display random books from the library.
    Respects current view filters (genre, author, folder prefix).
    """
    try:
        # Get parameters
        count = request.args.get("count", 1, type=int)
        count = max(1, min(count, 50))  # Clamp to 1-50

        view = request.args.get("view", "folder").strip().lower()
        prefix = request.args.get("prefix", "").strip()
        genre = request.args.get("genre", "").strip()
        author = request.args.get("author", "").strip()

        # Build list of entries to select from
        entries_all = build_library_entries()

        # Filter by prefix (respecting current folder/view)
        def under_prefix(entry: Dict) -> bool:
            if view == "collection":
                return True
            rel = entry.get("relpath", "")
            if not prefix:
                return True
            # Recurse down entire subtree from current folder (no depth limit)
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
            return False

        entries_in_scope = [e for e in entries_all if under_prefix(e)]

        # Apply genre and author filters
        if genre:
            def has_genre(entry: Dict) -> bool:
                g = entry.get("genres")
                if isinstance(g, str):
                    return g == genre
                if isinstance(g, (list, tuple)):
                    return genre in g
                return False
            entries_in_scope = [e for e in entries_in_scope if has_genre(e)]
        if author:
            entries_in_scope = [e for e in entries_in_scope if e.get("author") == author]

        # Select random books from all books in scope (not just direct DL formats)
        books_in_scope = entries_in_scope

        if not books_in_scope:
            flash(f"No books found in current {view} view to select from.", "info")
            return redirect(url_for("index", view=view, prefix=prefix, genre=genre, author=author))

        # Select random books
        selected = random.sample(books_in_scope, min(count, len(books_in_scope)))

        # If only 1 book selected, just redirect to book_detail instead of showing results page
        if count == 1 and len(selected) == 1:
            entry_id = selected[0].get("id")
            if entry_id:
                return redirect(url_for("book_detail", entry_id=entry_id))

        return render_template(
            "random_books.html",
            settings=settings_manager.settings,
            books=selected,
            view=view,
            prefix=prefix,
            genre=genre,
            author=author,
        )

    except Exception as e:
        logger.error(f"Error selecting random books: {e}", exc_info=True)
        flash("Error selecting random books", "danger")
        return redirect(url_for("index"))

@app.route("/book/<path:entry_id>")
def book_detail(entry_id):
    """
    Detailed view for a single library item.
    Only reachable by clicking a cover image (no nav entry).
    """
    # Handle legacy "random" requests (should use /book/random instead)
    if entry_id == "random":
        # Convert request.args to dict to pass to url_for
        args_dict = {k: request.args.get(k) for k in request.args.keys()}
        return redirect(url_for("random_books", **args_dict))

    entry = get_library_entry(entry_id)
    if not entry:
        flash("Book not found in library.", "warning")
        return redirect(url_for("index"))

    meta = ensure_library_metadata(entry)
    return render_template(
        "book_detail.html",
        settings=settings_manager.settings,
        title=entry["title"],
        entry=entry,
        meta=meta,
    )

@app.route("/goodreads/<genre>/lists")
def goodreads_genre_lists(genre):
    """Display Goodreads lists for a genre with pagination."""
    try:
        page = request.args.get("page", 1, type=int)
        page = max(1, min(page, 10))  # Clamp to 1-10

        # Fetch 1 page at a time (30 lists per page)
        lists = scrape_genre_lists(genre, page=page, max_pages=1)

        return render_template(
            "goodreads_lists.html",
            settings=settings_manager.settings,
            genre=genre,
            lists=lists,
            current_page=page,
            users=settings_manager.settings.users  # Explicitly pass users for modal
        )
    except Exception as e:
        logger.error(f"Failed to load Goodreads lists: {e}")
        flash(f"Failed to load lists for {genre}", "danger")
        return redirect(request.referrer or url_for("index"))

@app.route("/goodreads/<genre>/list/<list_id>/add-feed", methods=["POST"])
def add_goodreads_list_feed(genre, list_id):
    """Add a Goodreads list as an HTML feed to a user."""
    try:
        data = request.get_json()
        user_name = data.get("user_name", "")
        send_to_kindle = data.get("send_to_kindle", False)

        user = next((u for u in settings_manager.settings.users if u.name == user_name), None)
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        list_details = scrape_list_detail(list_id, data.get("list_name", ""))
        list_name = list_details.get("name", f"GR List {list_id}")

        user_root = Path(user.save_dir)
        list_folder = user_root / list_name
        list_folder.mkdir(parents=True, exist_ok=True)

        feed_url = f"https://www.goodreads.com/list/show/{list_id}"

        new_feed = FeedSettings(
            url=feed_url,
            mode="html",
            filetypes=["epub", "mobi", "azw", "azw3"],
            save_dir=str(list_folder),
            auto_send_to_kindle=send_to_kindle
        )

        user.feeds.append(new_feed)
        settings_manager.save()

        logger.info(f"Added {list_name} feed for user {user_name}, starting feed download...")
        BACKGROUND_EXECUTOR.submit(_run_feeds_background)

        return jsonify({
            "success": True,
            "message": f"Added {list_name} feed for user {user_name}",
            "folder": str(list_folder)
        })
    except Exception as e:
        logger.error(f"Failed to add feed: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/book/<path:entry_id>/refresh-metadata", methods=["POST"])
def book_refresh_metadata(entry_id):
    """
    POST endpoint to refresh metadata for a specific book.
    Runs the metadata enrichment and returns updated metadata as JSON.
    """
    entry = get_library_entry(entry_id)
    if not entry:
        return {"error": "Book not found"}, 404

    try:
        meta = ensure_library_metadata(entry)
        return {
            "success": True,
            "metadata": meta,
        }, 200
    except Exception as exc:
        logger.exception("Failed to refresh metadata for %s", entry_id)
        return {
            "error": str(exc),
        }, 500

@app.route("/library/batch-rename", methods=["POST"])
def library_batch_rename():
    """
    Batch rename all library files to the format: {title}.{author}.{fmt}
    """
    try:
        success_count, failure_count = batch_rename_library_files_to_md5_format()
        if success_count > 0:
            flash(
                f"Renamed {success_count} file(s) to MD5 format.",
                "success"
            )
        if failure_count > 0:
            flash(
                f"{failure_count} file(s) could not be renamed (check logs).",
                "warning"
            )
        if success_count == 0 and failure_count == 0:
            flash("No files to rename.", "info")
    except Exception:
        logger.exception("Batch rename failed")
        flash("Batch rename failed (check logs).", "danger")

    return redirect(url_for("index"))


@app.route("/library/batch-rename-to-titleauthor", methods=["POST"])
def library_batch_rename_to_title_author():
    """
    Batch rename all library files to the format: {title}-{author}.{fmt}
    """
    try:
        success_count, failure_count = batch_rename_library_files_to_title_author_format()
        if success_count > 0:
            flash(
                f"Renamed {success_count} file(s) to title-author format.",
                "success"
            )
        if failure_count > 0:
            flash(
                f"{failure_count} file(s) could not be renamed (check logs).",
                "warning"
            )
        if success_count == 0 and failure_count == 0:
            flash("No files to rename.", "info")
    except Exception:
        logger.exception("Batch rename to title-author format failed")
        flash("Batch rename failed (check logs).", "danger")

    return redirect(url_for("index"))

@app.route("/library/clear-cover-cache", methods=["POST"])
def clear_cover_cache():
    """
    Clear the cover image cache to force re-fetching and re-processing of all covers.
    Useful when cover resolution has improved or covers were cached at low quality.
    """
    try:
        cache_manager = get_cache_manager()
        removed = cache_manager.clear_all_covers()
        flash(f"Cleared {removed} cached cover images. Covers will be re-fetched on next view.", "success")
        logger.info("Cover cache cleared: %d images removed", removed)
    except Exception as e:
        logger.exception("Failed to clear cover cache")
        flash(f"Failed to clear cover cache: {e}", "danger")

    return redirect(url_for("index"))

@app.route("/settings", methods=["GET", "POST"], endpoint="settings")
def settings_view():
     global MAX_FEED_WORKERS
     if request.method == "POST":
         try:
             logger.debug("Received POST to /settings with %d form fields", len(request.form))
             
             # Get list of existing users BEFORE update
             old_user_names = {u.name for u in settings_manager.settings.users}
             
             settings_manager.update_from_form(request.form)
             MAX_FEED_WORKERS = getattr(
                 settings_manager.settings,
                 "max_feed_workers",
                 int(os.environ.get("MAX_FEED_WORKERS", "4")),
             )
             set_download_concurrency(
                 getattr(settings_manager.settings, "max_concurrent_downloads", 2)
             )
             logger.info("Settings updated via form")
             # Reload settings from disk to ensure consistency
             settings_manager.settings = settings_manager._load()
             
             # Check for new users and send EPUB to them as SMTP test
             try:
                 new_user_names = {u.name for u in settings_manager.settings.users}
                 newly_added = new_user_names - old_user_names
                 
                 if newly_added:
                     logger.info(f"Found {len(newly_added)} newly added user(s): {newly_added}")
                     for user in settings_manager.settings.users:
                         if user.name in newly_added:
                             try:
                                 if send_epub_to_new_user(user, settings_manager):
                                     logger.info(f"Successfully sent EPUB to new user {user.name}")
                                 else:
                                     logger.warning(f"Failed to send EPUB to new user {user.name}")
                             except Exception as e:
                                 logger.exception(f"Error sending EPUB to new user {user.name}: {e}")
             except Exception as e:
                 logger.exception("Failed during new user EPUB distribution: %s", e)
             
             # Return JSON for fetch requests (JavaScript in settings.html)
             return jsonify({"success": True, "message": "Settings saved"}), 200
         except Exception as e:
             logger.exception("Failed to update settings from form: %s", str(e))
             # Return JSON error for fetch requests
             return jsonify({"success": False, "error": str(e)}), 500

     # Build JSON-friendly structure for settings.js
     existing_users = [
         {
             **asdict(user),
             "feeds": [asdict(feed) for feed in user.feeds],
         }
         for user in settings_manager.settings.users
     ]

     return render_template(
         "settings.html",
         settings=settings_manager.settings,
         existing_users=existing_users,
     )

@app.route("/history")
def history():
    # Full history list (disk-backed, but cached by HistoryManager in Phase 3)
    entries = history_manager.load()

    # Get search and filter parameters
    search_query = request.args.get("search", "").strip().lower()
    date_start = request.args.get("date_start", "").strip()
    date_end = request.args.get("date_end", "").strip()

    # Filter by search query (title or author)
    if search_query:
        entries = [e for e in entries if (
            search_query in (e.get("title", "") or "").lower() or
            search_query in (e.get("author", "") or "").lower()
        )]

    # Filter by date range
    if date_start or date_end:
        from datetime import datetime
        filtered = []
        for entry in entries:
            try:
                entry_date_str = entry.get("timestamp", "")
                if entry_date_str:
                    # Parse ISO format: "2025-12-11T14:15:53.670Z"
                    entry_date = datetime.fromisoformat(entry_date_str.replace('Z', '+00:00')).date()
                else:
                    continue

                if date_start:
                    start_date = datetime.fromisoformat(date_start).date()
                    if entry_date < start_date:
                        continue

                if date_end:
                    end_date = datetime.fromisoformat(date_end).date()
                    if entry_date > end_date:
                        continue

                filtered.append(entry)
            except Exception:
                pass
        entries = filtered

    # Apply sorting by timestamp and title
    sort_param = request.args.get("sort", "newest").strip().lower()
    logger.info("History sort parameter: %s, entries before sort: %d", sort_param, len(entries))

    def parse_timestamp(ts_str: str):
        """Parse timestamp string and return datetime for proper comparison.
        Handles both naive (no timezone) and UTC Z-suffix formats.
        Returns a tuple (is_valid, datetime_obj) for reliable sorting.
        All datetimes are normalized to UTC for consistent comparison."""
        if not ts_str:
            return (False, None)
        try:
            from datetime import datetime, timezone
            # Try to parse with Z suffix first (UTC)
            if ts_str.endswith('Z'):
                dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                # Already UTC
            elif '+' in ts_str or ts_str.count('-') > 2:  # Has timezone offset
                # Parse with timezone
                dt = datetime.fromisoformat(ts_str)
                # Convert to UTC
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc)
            else:
                # Parse as naive datetime and assume UTC
                dt = datetime.fromisoformat(ts_str)
                # Make it UTC-aware for consistent comparison
                dt = dt.replace(tzinfo=timezone.utc)
            return (True, dt)
        except Exception:
            return (False, None)

    try:
        if sort_param == "newest":
            # Sort by timestamp descending (newest first)
            # Put unparseable entries last
            logger.debug("Sorting by newest (descending)")
            entries.sort(key=lambda e: parse_timestamp(e.get("timestamp", "")), reverse=True)
        elif sort_param == "oldest":
            # Sort by timestamp ascending (oldest first)
            # Put unparseable entries first
            logger.debug("Sorting by oldest (ascending)")
            entries.sort(key=lambda e: parse_timestamp(e.get("timestamp", "")))
        elif sort_param == "title_az":
            logger.debug("Sorting by title A-Z")
            # Sort by title ascending
            entries.sort(key=lambda e: (e.get("title", "") or "").lower())
        elif sort_param == "title_za":
            logger.debug("Sorting by title Z-A")
            # Sort by title descending
            entries.sort(key=lambda e: (e.get("title", "") or "").lower(), reverse=True)
    except Exception as e:
        logger.debug("Error sorting history entries: %s", e)
    logger.info("History entries after sort: %d", len(entries))

    # Only keep a reasonable tail of the debug log so we don't dump a huge file
    # into the history view on low-powered devices.
    if FEED_DEBUG_LOG.exists():
        try:
            debug_lines = FEED_DEBUG_LOG.read_text().splitlines()
        except Exception:
            debug_lines = []
    else:
        debug_lines = []
    # Keep only the last 400 lines; enough for troubleshooting but still light.
    debug_log = debug_lines[-400:]

    # Optional: library roots for mapping history items to /book/<id>
    library_roots = []
    try:
        if "get_library_roots" in globals():
            library_roots = get_library_roots()
    except Exception:
        logger.exception("Failed to get library roots when building history view")

    # Annotate entries with convenience fields and stable index used by the
    # download / send-to-Kindle routes.
    for idx, entry in enumerate(entries):
        # Index used for history download/send routes
        entry.setdefault("index", idx)

        path_str = entry.get("path") or ""
        entry["has_path"] = bool(path_str)
        entry["is_direct"] = (entry.get("filetype") or "").lower() in {
            "mobi",
            "prc",
            "azw",
            "azw3",
        }
        entry["entry_id"] = None

        # Try to map to a library entry id (root::relpath) if within a library root
        if path_str and library_roots:
            try:
                p = Path(path_str).resolve()
            except Exception:
                pass  # Continue anyway, just can't map to entry_id
            else:
                # Successfully resolved path, now try to map to a root
                for root in library_roots:
                    try:
                        rel = p.relative_to(root.resolve())
                    except Exception:
                        continue
                    rel_unix = str(rel).replace(os.sep, "/")
                    entry["entry_id"] = f"{str(root.resolve())}::{rel_unix}"
                    break

    # ------------------------------------------------------------------
    # Pagination: keep response sizes small for Kindle / low-end clients.
    # Entries are now properly sorted by the sort parameter above.
    # ------------------------------------------------------------------
    total_items = len(entries)
    per_page = max(
        1, int(getattr(settings_manager.settings, "library_items_per_page", 50) or 50)
    )

    # Clamp page to a sane integer >= 1
    try:
        page = int(request.args.get("page", "1"))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1

    total_pages = max(1, (total_items + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages

    # Simple pagination: entries are already sorted correctly by sort parameter
    if total_items == 0:
        page_entries = []
    else:
        start = (page - 1) * per_page
        end = start + per_page
        page_entries = entries[start:end]
    

    return render_template(
        "history.html",
        entries=page_entries,
        feed_debug=debug_log,
        settings=settings_manager.settings,
        page=page,
        total_pages=total_pages,
        total_items=total_items,
        per_page=per_page,
        search_query=search_query,
        date_start=date_start,
        date_end=date_end,
    )
@app.route("/history/download")
@app.route("/history/download/<int:index>")
def history_direct_download(index: int = None):
    """
    Directly download a file referenced by a history entry.
    Uses file_path from query parameter for safety when entries are filtered.
    Falls back to index for backward compatibility.
    """
    # Try to get file_path from query parameters first (new way)
    file_path = request.args.get("file_path", "").strip()
    
    if file_path:
        # Use the provided file path
        path = Path(file_path)
        if not path.exists():
            flash("File no longer exists on disk.", "danger")
            return redirect(url_for("history"))

        # Verify the file is in a safe location
        try:
            safe_path = path.resolve()
            library_root = Path(settings_manager.settings.library_root).resolve()
            allowed_bases = [library_root]
            try:
                allowed_bases.append(Path.home() / "Downloads")
            except Exception:
                pass
            # Also allow GoodBooks app root directory (for downloads and temporary saves)
            try:
                app_root = Path(__file__).parent.resolve()
                allowed_bases.append(app_root)
            except Exception:
                pass
            
            is_safe = any(safe_path.is_relative_to(base) for base in allowed_bases)
            if not is_safe:
                logger.warning("Attempted download from unsafe path: %s", file_path)
                flash("Access denied: file is not in a safe location.", "danger")
                return redirect(url_for("history"))
        except Exception as e:
            logger.warning("Failed to validate download path: %s", e)
            flash("Invalid file path.", "danger")
            return redirect(url_for("history"))
    else:
        # Fallback to index-based download (old way, less reliable with filters)
        entries = history_manager.load()
        if index < 0 or index >= len(entries):
            flash("History entry not found for download.", "warning")
            return redirect(url_for("history"))

        entry = entries[index]
        path_str = entry.get("path") or ""
        if not path_str:
            flash("No file path stored for this history entry.", "danger")
            return redirect(url_for("history"))

        path = Path(path_str)
        if not path.exists():
            flash("File no longer exists on disk.", "danger")
            return redirect(url_for("history"))

    directory = path.parent
    filename = path.name
    return send_from_directory(directory, filename, as_attachment=True)

@app.route("/history/send-to-kindle", methods=["POST"])
def history_send_to_kindle():
    """
    Send a history entry's file to a selected user's Kindle address.
    Accepts file_path from form for safety when entries are filtered.
    Falls back to index for backward compatibility.
    """
    user_name = request.form.get("user_name", "").strip()
    file_path = request.form.get("file_path", "").strip()
    
    # Fall back to index if file_path not provided
    if not file_path:
        try:
            index = int(request.form.get("index", "-1"))
        except ValueError:
            index = -1
        
        entries = history_manager.load()
        if index < 0 or index >= len(entries):
            flash("History entry not found.", "warning")
            return redirect(url_for("history"))

        entry = entries[index]
        path_str = entry.get("path") or ""
        if not path_str:
            flash("No file path stored for this history entry.", "danger")
            return redirect(url_for("history"))
    else:
        # Use provided file_path
        path_str = file_path
        # Need to find the entry in history to get metadata
        entries = history_manager.load()
        entry = None
        for e in entries:
            if e.get("path") == path_str:
                entry = e
                break
        
        if not entry:
            flash("History entry not found.", "warning")
            return redirect(url_for("history"))

    path = Path(path_str)
    if not path.exists():
        flash("File no longer exists on disk.", "danger")
        return redirect(url_for("history"))

    user = next(
        (u for u in settings_manager.settings.users if u.name == user_name),
        None,
    )
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("history"))

    smtp_config = settings_manager.settings.smtp
    if not smtp_config.is_configured():
        flash("SMTP is not configured.", "danger")
        return redirect(url_for("history"))

    result = {
        "title": entry.get("title"),
        "author": entry.get("author"),
        "selected_format": entry.get("filetype"),
        "cover": entry.get("cover", ""),
        "description": entry.get("description", ""),
        "file_path": str(path),
        "library_id": get_library_entry_id(path),  # Include library_id for cached cover lookup
    }

    try:
        send_kindle_email(smtp_config, user, path, result)
        send_notification_email(smtp_config, user, result)
        
        # Record the Kindle send in history
        try:
            history_manager.record_kindle_send(user.name, entry.get("title"), entry.get("author"), user.kindle_email)
        except Exception as e:
            logger.debug("Failed to record Kindle send in history: %s", e)
        
        flash(
            f"Sent '{entry.get('title')}' to {user.kindle_email or user.name}.",
            "success",
        )
    except Exception:
        logger.exception("Failed to send history item to Kindle")
        flash("Failed to send book to Kindle.", "danger")

    return redirect(url_for("history"))

@app.route("/history/bulk-send-to-kindle", methods=["POST"])
def history_bulk_send_to_kindle():
    """
    Send multiple history entries to a user's Kindle address.
    Accepts JSON with 'indices' (list of indices) and 'user_name'.
    """
    try:
        data = request.get_json() or {}
        indices = data.get("indices", [])
        user_name = data.get("user_name", "").strip()

        if not indices or not user_name:
            return jsonify({"error": "Missing indices or user_name"}), 400

        # Validate user exists
        user = next(
            (u for u in settings_manager.settings.users if u.name == user_name),
            None,
        )
        if not user:
            return jsonify({"error": "User not found"}), 400

        # Validate SMTP is configured
        smtp_config = settings_manager.settings.smtp
        if not smtp_config.is_configured():
            return jsonify({"error": "SMTP not configured"}), 400

        entries = history_manager.load()
        sent_count = 0
        failed_count = 0
        failed_titles = []

        for index in indices:
            try:
                index = int(index)
                if index < 0 or index >= len(entries):
                    failed_count += 1
                    continue

                entry = entries[index]
                path_str = entry.get("path") or ""
                if not path_str:
                    failed_count += 1
                    continue

                path = Path(path_str)
                if not path.exists():
                    failed_count += 1
                    continue

                result = {
                    "title": entry.get("title"),
                    "author": entry.get("author"),
                    "selected_format": entry.get("filetype"),
                    "cover": entry.get("cover", ""),
                    "description": entry.get("description", ""),
                    "file_path": str(path),
                    "library_id": get_library_entry_id(path),
                }

                try:
                    send_kindle_email(smtp_config, user, path, result)
                    sent_count += 1
                    logger.info("Bulk send to Kindle: user=%s title=%s", user_name, entry.get("title"))
                except Exception as e:
                    failed_count += 1
                    failed_titles.append(entry.get("title"))
                    logger.error("Failed to send to Kindle: user=%s title=%s error=%s", user_name, entry.get("title"), e)

            except (ValueError, TypeError) as e:
                failed_count += 1
                logger.error("Invalid index in bulk send: %s", e)

        response = {
            "sent": sent_count,
            "failed": failed_count,
            "total": len(indices),
            "user": user_name
        }

        if failed_titles:
            response["failed_titles"] = failed_titles

        logger.info("Bulk Kindle send complete: sent=%d failed=%d user=%s", sent_count, failed_count, user_name)

        return jsonify(response), 200

    except Exception as e:
        logger.exception("Error in bulk_send_to_kindle")
        return jsonify({"error": str(e)}), 500

@app.route("/history/delete", methods=["POST"])
def history_delete():
    """
    Delete a history entry, optionally also removing from library.
    """
    try:
        index = int(request.form.get("index", "-1"))
    except ValueError:
        index = -1

    delete_library = request.form.get("delete_library") == "1"

    entries = history_manager.load()
    if index < 0 or index >= len(entries):
        flash("History entry not found.", "warning")
        return redirect(url_for("history"))

    entry = entries[index]
    path_str = entry.get("path") or ""

    # Remove from history
    removed_entry = entries.pop(index)
    history_manager.path.write_text(json.dumps(entries, indent=2))

    # Optionally delete the file from library
    if delete_library and path_str:
        try:
            file_path = Path(path_str)
            if file_path.exists():
                file_path.unlink()
                logger.info("Deleted file from library: %s", file_path)
                flash(f"Deleted '{removed_entry.get('title')}' from library and history.", "success")
            else:
                flash(f"Removed '{removed_entry.get('title')}' from history (file already gone).", "info")
        except Exception as e:
            logger.error("Failed to delete file: %s", e)
            flash(f"Removed from history, but failed to delete file: {e}", "warning")
    else:
        flash(f"Removed '{removed_entry.get('title')}' from history.", "success")

    return redirect(url_for("history"))


@app.route("/cover/<library_id>")
def serve_cached_cover(library_id: str):
    """
    Serve a cached cover image for a library entry.
    Tries multiple extensions (.jpg, .png, .webp, .gif).
    Returns 404 if cover doesn't exist or is corrupted.
    """
    try:
        # Sanitize library_id to prevent directory traversal
        library_id = library_id.replace("/", "").replace("\\", "").replace(".", "")
        if not library_id:
            return "", 404

        # Try different extensions
        for ext in ["jpg", "png", "webp", "gif"]:
            cache_path = COVERS_DIR / f"{library_id}.{ext}"
            if cache_path.exists() and cache_path.is_file():
                return send_file(
                    str(cache_path),
                    mimetype=f"image/{ext}" if ext != "jpg" else "image/jpeg",
                    as_attachment=False,
                    download_name=None
                )

        return "", 404
    except Exception as e:
        logger.debug("Error serving cover for %s: %s", library_id, e)
        return "", 404


@app.route("/downloads/<path:filename>")
def downloads(filename: str):
    """
    Serve downloaded files back via HTTP, mainly for debugging.
    """
    download_root = resolve_download_dir(settings_manager.settings.default_download_dir)
    return send_from_directory(download_root, filename, as_attachment=True)


@app.route("/manual-download", methods=["POST"])
def manual_download():
    """
    Handle manual download from the search results page.
    """
    # Handle both JSON and form-encoded POST data
    data = request.get_json() if request.is_json else request.form

    user_name = data.get("user", "").strip()
    result_id = data.get("result_id") or data.get("md5", "")  # Support both result_id and md5
    result_id = result_id.strip() if result_id else ""
    selected_format = data.get("format", "").strip()

    logger.debug(
        "Manual download requested user=%s result_id=%s format=%s",
        user_name,
        result_id,
        selected_format,
    )

    if not user_name or not result_id:
        error_msg = "Missing user or result selection."
        if request.is_json:
            return jsonify({"success": False, "error": error_msg}), 400
        flash(error_msg, "danger")
        return redirect(url_for("search"))

    user = next(
        (u for u in settings_manager.settings.users if u.name == user_name),
        None,
    )
    if not user:
        error_msg = "User not found."
        if request.is_json:
            return jsonify({"success": False, "error": error_msg}), 404
        flash(error_msg, "danger")
        return redirect(url_for("search"))

    cached = source.cached_result(result_id)
    if not cached:
        error_msg = "Search result not found in cache. Please search again."
        if request.is_json:
            return jsonify({"success": False, "error": error_msg}), 404
        flash(error_msg, "danger")
        return redirect(url_for("search"))

    # Copy so we don't mutate the cached object in-place
    best = dict(cached)
    if selected_format:
        best["selected_format"] = selected_format

    # Check if file already exists in library by title/author
    existing = find_book_in_library_by_title_author(
        best.get("title", ""),
        best.get("author", "")
    )
    if existing:
        logger.info("Manual download: file already exists in library: %s", existing.get("path"))
        error_msg = f"Book already in library: {existing.get('title')}"
        if request.is_json:
            return jsonify({"success": False, "error": error_msg}), 400
        flash(error_msg, "info")
        return redirect(url_for("search"))

    # Ensure downloads are resolved if search ran in "cheap" mode.
    try:
        best = source.resolve_downloads_for_result(best)
    except Exception:
        logger.exception("Failed to resolve downloads for manual download")
        error_msg = "Failed to resolve download links for the selected result."
        if request.is_json:
            return jsonify({"success": False, "error": error_msg}), 500
        flash(error_msg, "danger")
        return redirect(url_for("search"))

    settings = settings_manager.settings
    dest_dir = resolve_download_dir(
        user.save_dir or settings.default_download_dir
    )


    # Determine the format to download (respect explicit form selection first).
    file_format = (
        selected_format or best.get("selected_format") or (best.get("formats") or [None])[0]
    )
    if not file_format:
        error_msg = "No downloadable format selected for this result."
        if request.is_json:
            return jsonify({"success": False, "error": error_msg}), 400
        flash(error_msg, "danger")
        return redirect(url_for("search"))

    try:
        saved_path = source.download(best, file_format, dest_dir)
    except Exception as exc:
        logger.exception("Manual download failed")
        error_msg = f"Download failed: {exc}"
        if request.is_json:
            return jsonify({"success": False, "error": error_msg}), 500
        flash(error_msg, "danger")
        return redirect(url_for("search"))

    cover = normalize_cover_url(best.get("cover", ""))
    description = strip_html_tags(best.get("description", "")).strip()
    history_manager.record(
        user.name,
        best.get("title", saved_path.stem),
        cover,
        best.get("author", ""),
        best.get("selected_format", ""),
        "manual",
        description,
        str(saved_path),
    )
    # Also upsert metadata for the Library/details page
    upsert_library_metadata_for_download(saved_path, best)

    # Add file_path and library_id to best dict for notification email cover extraction
    best["file_path"] = str(saved_path)
    entry_id = get_library_entry_id(saved_path)
    if entry_id:
        best["library_id"] = entry_id

    oversize = is_oversize_for_kindle(saved_path)
    sent_to_kindle = False
    if user.kindle_email and settings.smtp.is_configured():
        if oversize:
            logger.warning(
                "File %s is larger than 20MB; Kindle may reject it, attempting send anyway.",
                saved_path,
            )
            if not request.is_json:
                flash(
                    "File is larger than 20MB; Kindle may reject it, but we attempted to send it anyway.",
                    "warning",
                )
        send_kindle_email(settings.smtp, user, saved_path, best)
        sent_to_kindle = True
    if user.notification_email and settings.smtp.is_configured():
        send_notification_email(settings.smtp, user, best)

    if request.is_json:
        return jsonify({"success": True, "title": best.get("title", saved_path.name)})

    flash(f"Downloaded {best.get('title', saved_path.name)}", "success")
    return redirect(url_for("history"))

def refresh_library_metadata_background() -> None:
     """
     Background task to refresh library metadata from Goodreads only.
     Uses title-based Goodreads search to fetch missing genres, ratings, descriptions, and covers.

     Only refreshes entries with MISSING metadata fields:
     - Missing or empty genres
     - Missing or empty rating  
     - Missing or empty description
     - Missing or empty cover

     Does NOT search Anna's Archive - files already exist in library.
     """
     global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME
     global _LIBRARY_ENTRIES_CACHE, _LIBRARY_ENTRIES_LAST_SCAN
     try:
         logger.info("Starting background library metadata refresh from Goodreads...")
         entries = build_library_entries()
         metadata = load_library_metadata()
         updated_count = 0
         skipped_count = 0

         # Filter to only entries needing enrichment
         entries_needing_enrichment = filter_entries_needing_enrichment(entries, metadata)

         # Initialize progress tracking with filtered count
         total_entries = len(entries_needing_enrichment)
         with metadata_progress_lock:
             metadata_progress_state["active"] = True
             metadata_progress_state["total_books"] = total_entries
             metadata_progress_state["completed_books"] = 0
             metadata_progress_state["start_time"] = time.time()
             metadata_progress_state["percentage"] = 0
             metadata_progress_state["type"] = "background"
             metadata_progress_state["eta_seconds"] = None

         logger.info(f"Metadata refresh starting: {total_entries} books need enrichment (out of {len(entries)} total)")

         for idx, entry in enumerate(entries_needing_enrichment):
             entry_id = entry.get("id")
             if not entry_id:
                 continue

             # Update current book being processed
             book_title = entry.get("title", "Unknown")
             with metadata_progress_lock:
                 metadata_progress_state["current_book"] = book_title[:60]  # Truncate long titles
                 metadata_progress_state["current_step"] = "Checking..."

             # Metadata is incomplete - fetch from Goodreads
             try:
                 with metadata_progress_lock:
                     metadata_progress_state["current_step"] = "Fetching from Goodreads..."
                 meta = enrich_library_metadata_from_goodreads(entry)
                 
                 # Track what was actually fetched
                 fetched_fields = []
                 if meta.get("genres"):
                     fetched_fields.append("genres")
                 if meta.get("rating"):
                     fetched_fields.append("rating")
                 if meta.get("cover"):
                     fetched_fields.append("cover")
                 if meta.get("description"):
                     fetched_fields.append("description")
                 
                 # Log metadata misses (fields that were NOT fetched)
                 all_fields = {"genres", "rating", "cover", "description"}
                 still_missing = [f for f in all_fields if f not in fetched_fields]
                 if still_missing:
                     log_metadata_miss(entry_id, book_title, still_missing, "goodreads_fetch_incomplete")
                 
                 if meta.get("genres") or meta.get("rating") or meta.get("cover") or meta.get("description"):
                     updated_count += 1
                     with metadata_progress_lock:
                         metadata_progress_state["current_step"] = "Saving..."
                     # Save to disk
                     metadata[entry_id] = meta
                     logger.debug("Updated metadata for %s: genres=%s rating=%s", 
                                book_title[:50], 
                                bool(meta.get("genres")), 
                                meta.get("rating"))
             except Exception as e:
                 logger.debug("Failed to refresh metadata for %s: %s", entry_id, e)
                 # Log the fetch error
                 log_metadata_miss(entry_id, book_title, ["genres", "rating", "cover", "description"], f"error: {str(e)[:50]}")

             # Update progress
             with metadata_progress_lock:
                 metadata_progress_state["completed_books"] = idx + 1
                 if total_entries > 0:
                     metadata_progress_state["percentage"] = int((idx + 1) / total_entries * 100)
                     # Calculate ETA
                     elapsed = time.time() - metadata_progress_state["start_time"]
                     if idx + 1 > 0:
                         rate = elapsed / (idx + 1)
                         remaining = rate * (total_entries - idx - 1)
                         metadata_progress_state["eta_seconds"] = max(0, int(remaining))

             # Partial flush every 50 entries to preserve progress
             if (idx + 1) % 50 == 0 and updated_count > 0:
                 # Clear caches on partial flush so updates show up immediately
                 global _LIBRARY_METADATA_CACHE, _LIBRARY_METADATA_MTIME
                 global _LIBRARY_ENTRIES_CACHE, _LIBRARY_ENTRIES_LAST_SCAN
                 try:
                     with library_metadata_lock:
                         LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
                         _LIBRARY_METADATA_CACHE = {}
                         _LIBRARY_METADATA_MTIME = 0.0
                         _LIBRARY_ENTRIES_CACHE = []
                         _LIBRARY_ENTRIES_LAST_SCAN = 0.0
                     logger.debug("Partial metadata flush at entry %d/%d", idx + 1, total_entries)
                 except Exception as e:
                     logger.debug("Failed to flush metadata at entry %d: %s", idx + 1, e)
         # Persist all updates
         if updated_count > 0:
             try:
                 with library_metadata_lock:
                     DATA_DIR.mkdir(exist_ok=True)
                     LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
                 logger.info("Background metadata refresh completed: %d updated, %d already complete", updated_count, skipped_count)
                 # Clear cache so next load reads the updated file
                 _LIBRARY_METADATA_CACHE = {}
                 _LIBRARY_METADATA_MTIME = 0.0
                 
                 # Clear the library entries cache since metadata changed
                 _LIBRARY_ENTRIES_CACHE = []
                 _LIBRARY_ENTRIES_LAST_SCAN = 0.0
             except Exception as e:
                 logger.exception("Failed to persist refreshed library metadata: %s", e)
         else:
             logger.info("Background metadata refresh completed: no updates needed, all %d entries have complete metadata", skipped_count)
     except Exception as e:
         logger.exception("Background metadata refresh failed: %s", e)
     finally:
         # Set progress to 100% to indicate completion
         with metadata_progress_lock:
             metadata_progress_state["percentage"] = 100
             metadata_progress_state["eta_seconds"] = 0
             metadata_progress_state["active"] = False
             logger.info("Metadata refresh completed: progress=100%")

@app.route("/library/refresh-metadata", methods=["POST"])
def refresh_library_metadata():
    """
    Manually trigger library metadata refresh from Goodreads.
    Runs in background thread to avoid blocking the UI.
    """
    # Check if background jobs are disabled
    settings = settings_manager.settings
    if settings.disable_background_jobs:
        flash("Background jobs are disabled in settings.", "warning")
        return redirect(url_for("index"))

    try:
        # Check if background maintenance is already running
        with metadata_progress_lock:
            if metadata_progress_state.get("active") and metadata_progress_state.get("type") == "background-maintenance":
                flash("Background maintenance is currently running. Please wait for it to complete.", "warning")
                return redirect(url_for("index"))

            # Initialize progress state before starting thread
            metadata_progress_state["active"] = True
            metadata_progress_state["total_books"] = 0
            metadata_progress_state["completed_books"] = 0
            metadata_progress_state["start_time"] = time.time()
            metadata_progress_state["percentage"] = 0
            metadata_progress_state["type"] = "manual"
            metadata_progress_state["eta_seconds"] = None
            logger.info("Metadata refresh initiated: progress state active=True")

        refresh_thread = threading.Thread(
            target=refresh_library_metadata_background,
            daemon=True,
            name="library-metadata-refresh-manual"
        )
        refresh_thread.start()
        logger.info("Metadata refresh background thread started")
        flash("Starting library metadata refresh in background (this may take a few minutes)...", "info")
    except Exception as e:
        logger.exception("Failed to start metadata refresh thread")
        flash("Failed to start metadata refresh.", "danger")

    return redirect(url_for("index"))

def extract_title_and_author(dirty_title: str) -> tuple:
    """Extract clean title and author from filename-style title.
    Works with mixed case input. Returns lowercase output.
    Examples:
      'Hide ( D.D Warren #2 )-Gardner; Lisa.epub' -> ('hide', 'gardner; lisa')
      'The Gift-Danielle Steel.mobi' -> ('the gift', 'danielle steel')
    """
    import re
    import os

    # First, strip file extension so it doesn't interfere with parsing
    dirty_title_no_ext = os.path.splitext(dirty_title)[0]

    # Try to extract author if format is "Title-Author"
    author = ""
    if '-' in dirty_title_no_ext:
        parts = dirty_title_no_ext.rsplit('-', 1)  # Split from right to get last hyphen
        if len(parts) == 2:
            potential_author = parts[1].strip()
            # Check if it looks like an author (has letters, starts with cap)
            if potential_author and any(c.isupper() for c in potential_author) and any(c.isalpha() for c in potential_author):
                author = potential_author.lower()
                dirty_title_no_ext = parts[0].strip()

    # Remove parenthetical info: "Title (Series #N)" -> "Title"
    clean_title = re.sub(r'\s*\([^)]*\)\s*', ' ', dirty_title_no_ext).strip()
    clean_title = clean_title.lower()

    # Extract first author (before semicolon) for matching
    first_author = author.split(";")[0].strip() if author else ""

    return clean_title, first_author, author

@app.route("/feeds/run", methods=["POST"])
def run_feeds():
    """
    Run all configured RSS/HTML feeds.
    This version also:
    - Prioritizes smaller feeds first (by number of items to process).
    - Tracks per-user/per-feed progress in the in-memory feed_progress_state
      so that the UI can subscribe via Server-Sent Events.

    Note: Feeds run in the foreground, NOT in the background.
    During other background tasks, the feeds/view page will be refreshed.
    """
    # Check if background jobs are disabled
    settings = settings_manager.settings
    if settings.disable_background_jobs:
        flash("Background jobs are disabled in settings.", "warning")
        return redirect(url_for("history"))

    # Initialize progress state immediately so SSE clients see active=True
    run_id = uuid.uuid4().hex
    now = time.time()
    with feed_progress_lock:
        feed_progress_state["run_id"] = run_id
        feed_progress_state["active"] = True
        overall = feed_progress_state.get("overall", {})
        overall["total_items"] = 0
        overall["completed_items"] = 0
        overall["start_time"] = now
        overall["eta_seconds"] = None
        feed_progress_state["overall"] = overall
        feed_progress_state["feeds"] = {}

    # Submit feed processing to background executor for non-blocking operation
    # Note: Feed runs happen in background to not block UI, but they do not run 
    # automatically - they require explicit user trigger via /feeds/run endpoint
    BACKGROUND_EXECUTOR.submit(_run_feeds_background)
    flash("Feed run started in background. Check progress below.", "info")
    return redirect(url_for("history"))
def _parse_single_feed(user: UserSettings, feed: FeedSettings) -> Tuple[Optional[UserSettings], Optional[FeedSettings], List[ParsedItem], List[str]]:
    """Parse a single feed and return (user, feed, items, debug_messages)."""
    local_debug = []
    try:
        items = feed_parser.parse(feed, local_debug)
        return user, feed, items if items else [], local_debug
    except Exception as exc:
        logger.exception("Failed to parse feed url=%s", feed.url)
        local_debug.append(f"Failed to parse feed: {exc}")
        return user, feed, [], local_debug



def _run_feeds_background():
    """
    Background worker function that actually processes feeds.
    This runs in a background thread pool so the HTTP endpoint can return immediately.
    """
    import time
    import uuid

    # Initialize progress state (same as run_feeds() does)
    run_id = uuid.uuid4().hex
    now = time.time()
    with feed_progress_lock:
        feed_progress_state["run_id"] = run_id
        feed_progress_state["active"] = True
        overall = feed_progress_state.get("overall", {})
        overall["total_items"] = 0
        overall["completed_items"] = 0
        overall["start_time"] = now
        overall["eta_seconds"] = None
        feed_progress_state["overall"] = overall
        feed_progress_state["feeds"] = {}

    run_start = time.time()
    # Reload settings from disk to pick up any newly added feeds
    settings_manager.settings = settings_manager._load()
    settings = settings_manager.settings
    total_downloads = 0
    debug_messages: List[str] = []
    # Make sure the debug log directory exists
    FEED_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)

    # Load metadata ONCE at the start instead of for every item
    metadata_start = time.time()
    library_metadata = load_library_metadata()

    # Build library lookup from ACTUAL library entries, not just metadata.json
    library_entries = build_library_entries()
    metadata_time = time.time() - metadata_start
    logger.info("TIMING: load_library_metadata + build_library took %.3f seconds", metadata_time)
    library_lookup = set()

    for entry in library_entries:
        raw_title = entry.get("title") or ""

        # Extract title and author from filename format "Title-Author" or use metadata
        clean_title, author_from_title, full_author = extract_title_and_author(raw_title)

        # Fall back to metadata author if available
        author_full = (entry.get("author") or "").lower().strip()

        # Normalize author using cleanup_author() - consistent with deduplication checks
        if author_full:
            author_norm = history_manager.cleanup_author(author_full)
        else:
            # Use author from filename
            author_norm = history_manager.cleanup_author(full_author) if full_author else ""

        # Add matches if we have both title and author
        if clean_title and author_norm:
            library_lookup.add((clean_title, author_norm))

    logger.info("Background: built library_lookup with %d entries from %d library files", len(library_lookup), len(library_entries))

    # Build feed-level file caches (one per feed, per user) to avoid per-item scans
    feed_file_cache = {}  # key: (user.name, feed.save_dir) -> set of normalized titles in feed
    for user in settings.users:
        for feed in user.feeds:
            feed_save_path = Path(feed.save_dir)
            feed_titles = set()
            if feed_save_path.exists():
                try:
                    for file in feed_save_path.glob("*"):
                        if file.is_file():
                            # Extract title from filename and normalize
                            feed_titles.add(file.name.lower())
                except OSError:
                    pass
            feed_file_cache[(user.name, str(feed.save_dir))] = feed_titles
    logger.info("Built feed file cache: %d feeds pre-scanned", len(feed_file_cache))

    # Build user library file cache (one per user) to avoid per-item scans
    user_file_cache = {}  # key: user.name -> set of normalized titles in user library
    for user in settings.users:
        user_lib_path = Path(user.save_dir or settings.default_download_dir)
        user_titles = set()
        if user_lib_path.exists():
            try:
                # Check top level files
                for file in user_lib_path.glob("*"):
                    if file.is_file():
                        user_titles.add(file.name.lower())
                # Check one level deep in subdirectories
                for subdir in user_lib_path.glob("*/"):
                    if subdir.is_dir():
                        for file in subdir.glob("*"):
                            if file.is_file():
                                user_titles.add(file.name.lower())
            except OSError:
                pass
        user_file_cache[user.name] = user_titles
    logger.info("Built user file cache: %d users, %d total files", len(user_file_cache), sum(len(v) for v in user_file_cache.values()))

    # Pre-load history into memory for O(1) lookups
    history_lookup = set()  # key: (user_name, title)
    try:
        history_data = history_manager.load()
        # history_data is a list of dicts, each with 'user' and 'title' keys
        for item_record in history_data:
            if isinstance(item_record, dict) and "title" in item_record and "user" in item_record:
                user_name = item_record.get("user")
                hist_title = (item_record.get("title") or "").lower().strip()
                history_lookup.add((user_name, hist_title))
    except Exception as e:
        logger.warning("Failed to pre-load history: %s", e)
    logger.info("Pre-loaded history: %d (user, title) pairs", len(history_lookup))

    # Update global library cache so search_with_cache can skip items already owned
    with library_cache_lock:
        _LIBRARY_LOOKUP_CACHE.clear()
        _LIBRARY_LOOKUP_CACHE.update(library_lookup)
    def append_debug(lines: List[str]) -> None:
        """Merge per-thread debug lines into the shared debug_messages."""
        if not lines:
            return
        with debug_lock:
            debug_messages.extend(lines)
    # ------------------------------------------------------------------
    # Progress helpers (in-memory, per-process only)
    # ------------------------------------------------------------------
    def init_progress():
        """Reset global feed_progress_state for a new run."""
        # Skip if already initialized (happens when called from /feeds/run endpoint)
        with feed_progress_lock:
            if feed_progress_state.get("active"):
                return feed_progress_state.get("run_id")

        run_id = uuid.uuid4().hex
        now = time.time()
        with feed_progress_lock:
            feed_progress_state["run_id"] = run_id
            feed_progress_state["active"] = True
            overall = feed_progress_state.get("overall", {})
            overall["total_items"] = 0
            overall["completed_items"] = 0
            overall["start_time"] = now
            overall["eta_seconds"] = None
            feed_progress_state["overall"] = overall
            feed_progress_state["feeds"] = {}
        return run_id
    def register_feed_progress(user: UserSettings, feed: FeedSettings, total_items: int):
        """Register a feed with its total items before queuing work."""
        key = f"{user.name}::{feed.url}"
        now = time.time()
        with feed_progress_lock:
            feeds = feed_progress_state.get("feeds", {})
            feeds[key] = {
                "user": user.name,
                "feed_url": feed.url,
                "feed_mode": getattr(feed, "mode", "rss"),
                "label": getattr(feed, "url", ""),
                "total_items": int(total_items),
                "completed_items": 0,
                "start_time": now,
                "eta_seconds": None,
                "active": True,
            }
            feed_progress_state["feeds"] = feeds
            # Bump overall total_items
            overall = feed_progress_state.get("overall", {})
            overall["total_items"] = int(overall.get("total_items", 0)) + int(
                total_items
            )
            feed_progress_state["overall"] = overall
        return key
    def mark_item_completed(user: UserSettings, feed: FeedSettings):
        """Increment completed counts and recompute simple ETA."""
        key = f"{user.name}::{feed.url}"
        now = time.time()
        with feed_progress_lock:
            overall = feed_progress_state.get("overall", {})
            feeds = feed_progress_state.get("feeds", {})
            # Overall counters
            overall_total = int(overall.get("total_items", 0))
            overall_completed = int(overall.get("completed_items", 0)) + 1
            overall["completed_items"] = overall_completed
            start_time = overall.get("start_time") or now
            elapsed = max(0.0, now - start_time) if overall_completed > 0 else 0.0
            if overall_total > 0 and overall_completed > 0 and elapsed > 0:
                rate = overall_completed / elapsed  # items per second
                remaining = max(0, overall_total - overall_completed)
                overall["eta_seconds"] = int(remaining / rate) if rate > 0 else None
            else:
                overall["eta_seconds"] = None
            feed_progress_state["overall"] = overall
            # Per-feed counters
            feed_state = feeds.get(key)
            if feed_state is not None:
                feed_total = int(feed_state.get("total_items", 0))
                feed_completed = int(feed_state.get("completed_items", 0)) + 1
                feed_state["completed_items"] = feed_completed
                f_start = feed_state.get("start_time") or now
                f_elapsed = max(0.0, now - f_start) if feed_completed > 0 else 0.0
                if feed_total > 0 and feed_completed > 0 and f_elapsed > 0:
                    f_rate = feed_completed / f_elapsed
                    f_remaining = max(0, feed_total - feed_completed)
                    feed_state["eta_seconds"] = int(
                        f_remaining / f_rate
                    ) if f_rate > 0 else None
                else:
                    feed_state["eta_seconds"] = None
                feeds[key] = feed_state
                feed_progress_state["feeds"] = feeds
    def finalize_progress():
        """Mark the current run as inactive without discarding data."""
        with feed_progress_lock:
            feed_progress_state["active"] = False
            # Mark any still-active feeds as inactive
            feeds = feed_progress_state.get("feeds", {})
            for key, fstate in feeds.items():
                if fstate.get("active"):
                    fstate["active"] = False
            feed_progress_state["feeds"] = feeds
    # ------------------------------------------------------------------
    # Worker for a single (user, feed, item)
    # ------------------------------------------------------------------
    def process_item(user: UserSettings, feed: FeedSettings, item) -> Tuple[int, str, List[Tuple[Path, Dict]]]:
        """
        Worker for a single (user, feed, item).
        Returns:
            Tuple of (success_count, user_name, downloads_list) where success_count is 1 if successful else 0.
            downloads_list contains (Path, Dict) tuples for files to be Kindle-emailed.
        """
        downloads: List[Tuple[Path, Dict]] = []
        local_debug: List[str] = []

        # DEBUG: Log the exact title and author values
        logger.debug(
            "process_item received: title (repr)=%r author (repr)=%r title_len=%d author_len=%d",
            item.title,
            item.author,
            len(item.title),
            len(item.author),
        )

        # FAST PATH: Check library lookup first (O(1) operation)
        raw_title = (item.title or "").lower().strip()
        raw_author = (item.author or "").lower().strip()

        # Clean title: remove parenthetical info like series numbers
        import re
        title_norm = re.sub(r'\s*\([^)]*\)\s*', ' ', raw_title).strip()

        # Normalize author using cleanup_author() function
        author_norm = history_manager.cleanup_author(item.author or "")

        lookup_key = (title_norm, author_norm)
        if lookup_key in library_lookup:
            logger.debug("Item already in library (fast path): title=%s author=%s", item.title, item.author)
            local_debug.append(f"    Skipping: book already in library (fast match)")

            # AUTO-SEND: Check if this book should be sent to Kindle for this user/feed
            if feed.auto_send_to_kindle is True and user.kindle_email:
                # Find the library entry to get its path
                library_entry = None
                for lib_entry in library_entries:
                    lib_title = (lib_entry.get("title") or "").lower().strip()
                    clean_lib_title, _, full_lib_author = extract_title_and_author(lib_title)
                    lib_author_norm = history_manager.cleanup_author(full_lib_author) if full_lib_author else ""
                    if (clean_lib_title, lib_author_norm) == lookup_key:
                        library_entry = lib_entry
                        break

                if library_entry and library_entry.get("path"):
                    # Check if already sent to this user's Kindle
                    already_sent = history_manager.kindle_sent(user.name, item.title, item.author)
                    if not already_sent:
                        try:
                            lib_path = Path(library_entry["path"])
                            if lib_path.exists():
                                logger.info("Auto-sending library match to Kindle: title=%s author=%s user=%s", 
                                           item.title, item.author, user.name)
                                local_debug.append(f"    Queuing for Kindle (library match): {item.title}")
                                downloads.append((lib_path, item))
                                # Note: actual send happens in batch after this function
                        except Exception as e:
                            logger.warning("Failed to queue library match for Kindle: %s", e)
                    else:
                        logger.debug("Library match already sent to Kindle for user: title=%s user=%s", 
                                    item.title, user.name)

            append_debug(local_debug)
            mark_item_completed(user, feed)  # Update progress bar
            return (1 if downloads else 0), user.name, downloads

        # SLOW PATH: Do detailed verification checks only if not in fast lookup

        # Check if this book already exists in the feed's save directory (using pre-built cache)
        feed_cache_key = (user.name, str(feed.save_dir))
        feed_titles = feed_file_cache.get(feed_cache_key, set())
        if any(item.title.lower() in fname for fname in feed_titles):
            logger.info("Book already in feed folder: title=%s", item.title)
            local_debug.append(f"    Skipping: book already in feed folder")
            append_debug(local_debug)
            mark_item_completed(user, feed)  # Update progress bar
            return 0, user.name, downloads

        # Check user's actual library directory for files (using pre-built cache)
        user_titles = user_file_cache.get(user.name, set())
        if any(item.title.lower() in fname for fname in user_titles):
            logger.info("Book already in user library: title=%s", item.title)
            local_debug.append(f"    Skipping: book already in user library")
            append_debug(local_debug)
            mark_item_completed(user, feed)  # Update progress bar
            return 0, user.name, downloads

        # Check history to avoid re-processing items from previous runs (using pre-built cache)
        if (user.name, raw_title) in history_lookup:
            logger.info("Item already in history: title=%s author=%s", item.title, item.author)
            local_debug.append(f"    Skipping: item already processed (history)")
            append_debug(local_debug)
            mark_item_completed(user, feed)  # Update progress bar
            return 0, user.name, downloads

        query = f"{item.title} {item.author}".strip()

        # DEBUG: Log the query being built
        logger.debug(
            "Built query from title+author: (repr)=%r query_len=%d has_spaces=%s",
            query,
            len(query),
            " " in query,
        )

        local_debug.append(f"    Searching for {query}")
        logger.info("Searching for item title=%s author=%s", item.title, item.author)
        # First attempt: full title + author
        try:
            search_options = SearchOptions(
                query=query,
                language="en",
                extensions=feed.filetypes,
                autodownload=False,
                preferred_formats=feed.filetypes,
                kindle_type=user.kindle_type,
                max_results=1,  # Only need the top ranked result
            )
            results, search_debug = search_with_cache(
                query,
                search_options,
                persist=True,
            )
            local_debug.extend([f"      {msg}" for msg in search_debug])
        except Exception as exc:
            logger.exception("Search failed for item title=%s", item.title)
            local_debug.append(f"      Search failed: {exc}")
            append_debug(local_debug)
            mark_item_completed(user, feed)  # Update progress bar
            return 0, user.name, downloads
        # If no results, retry with sanitized author from feed (no fallback to title-only)
        if not results:
            if item.author:
                # Sanitize the author string to remove duplicates
                sanitized_author = sanitize_author(item.author)
                if sanitized_author != item.author:
                    logger.info("Sanitized author: '%s' → '%s'", item.author, sanitized_author)
                    local_debug.append(f"      Author sanitized: '{item.author}' → '{sanitized_author}'")

                # Retry with sanitized author
                retry_query = f"{item.title} {sanitized_author}".strip()
                local_debug.append(f"      No results, retrying with sanitized author: {retry_query}")
                try:
                    search_options = SearchOptions(
                        query=retry_query,
                        language="en",
                        extensions=feed.filetypes,
                        autodownload=False,
                        preferred_formats=feed.filetypes,
                        kindle_type=user.kindle_type,
                        max_results=1,
                    )
                    results, search_debug = search_with_cache(
                        retry_query,
                        search_options,
                        persist=True,
                    )
                    local_debug.extend([f"      {msg}" for msg in search_debug])
                except Exception as exc:
                    logger.exception(
                        "Search retry failed for item title=%s author=%s", item.title, sanitized_author
                    )
                    local_debug.append(f"      Retry search failed: {exc}")
                    append_debug(local_debug)
                    mark_item_completed(user, feed)  # Update progress bar
                    return 0, user.name, downloads
            else:
                # No author in feed - don't retry
                local_debug.append("      No results and no author in feed - skipping")
                logger.info("No results for title=%s (no author in feed)", item.title)
                append_debug(local_debug)
                mark_item_completed(user, feed)  # Update progress bar
                return 0, user.name, downloads
        elif not results:
            # Should not reach here but keep as safety check
            local_debug.append("      No search results found")
            logger.info("No results for title=%s", item.title)
            append_debug(local_debug)
            mark_item_completed(user, feed)  # Update progress bar
            return 0, user.name, downloads
        best = select_best_result(
            results,
            feed.filetypes,
            user.kindle_type,
            expected_title=item.title,
            expected_author=item.author,
        )
        # Determine the desired file format once we have the best match.  We don't
        # attempt to download here because the destination directory (dest_dir)
        # depends on the user and feed.
        file_format = None
        if best:
            file_format = best.get("selected_format")
            if not file_format:
                # Unexpected state: select_best_result should set selected_format
                local_debug.append("      No format selected for best result")
                logger.warning(
                    "select_best_result returned a result without selected_format for title=%s",
                    item.title,
                )
                best = None
        if not best:
            local_debug.append("      No matching formats found")
            logger.info(
                "No matching formats for title=%s allowed=%s",
                item.title,
                feed.filetypes,
            )
            append_debug(local_debug)
            mark_item_completed(user, feed)  # Update progress bar
            return 0, user.name, downloads
        local_debug.append(
            f"      Selected best match {best.get('title')} ({best.get('selected_format')}) "
            f"from {len(results)} results"
        )
        logger.info(
            "Downloading title=%s format=%s",
            best.get("title"),
            file_format,
        )

        # Resolve downloads for the selected result (handles Cloudflare challenges via stealth browser)
        try:
            logger.info("Resolving downloads for result title=%s md5=%s", best.get("title"), best.get("detail"))
            best = source.resolve_downloads_for_result(best)
            downloads_resolved = best.get("downloads") or {}
            logger.info("Downloads resolved: found %d formats after resolution", len(downloads_resolved))
        except Exception as exc:
            logger.exception("Failed to resolve downloads for result")
            local_debug.append(f"      Failed to resolve download links: {exc}")
            append_debug(local_debug)
            mark_item_completed(user, feed)
            return 0, user.name, downloads

        # Download to the correct directory
        try:
            if feed.mode == "html" and getattr(feed, "save_dir", ""):
                # For feed-specific save_dir: if relative, resolve relative to user's root
                feed_save_dir = feed.save_dir
                if not Path(feed_save_dir).is_absolute():
                    # Relative path: resolve relative to user's root directory
                    user_root = Path(user.save_dir or settings.default_download_dir)
                    feed_save_dir = str(user_root / feed_save_dir)
                dest_dir = resolve_download_dir(feed_save_dir)
            else:
                dest_dir = resolve_download_dir(
                    user.save_dir or settings.default_download_dir
                )
        except Exception as exc:
            logger.exception("Failed to resolve destination directory")
            local_debug.append(f"      Failed to resolve destination directory: {exc}")
            append_debug(local_debug)
            mark_item_completed(user, feed)
            return 0, user.name, downloads

        # Check if file already exists in library before downloading
        title = best.get("title", "").strip()
        author = best.get("author", "").strip()
        filename_base = f"{title}"
        if author:
            filename_base = f"{filename_base}-{author}"

        # Look for any existing file matching this book (any format)
        existing_file = None
        try:
            dest_path = Path(dest_dir)
            for fmt in ["epub", "mobi", "azw3", "pdf", "fb2", "rtf"]:
                test_files = list(dest_path.glob(f"*{title}*.{fmt}"))
                if test_files:
                    existing_file = test_files[0]
                    logger.info("File already exists in library: %s - skipping download", existing_file)
                    local_debug.append(f"      File already exists in library: {existing_file.name} - skipping")
                    append_debug(local_debug)
                    return 1, user.name, downloads  # Return 1 = success, already have it
        except Exception as exc:
            logger.debug("Error checking for existing file: %s", exc)

        # Layer 2: Validate format is convertible before attempting download
        CONVERTIBLE_FORMATS = {"epub", "mobi", "azw", "azw3", "pdf", "txt"}
        if file_format.lower() not in CONVERTIBLE_FORMATS:
            logger.warning(
                "Skipping item: format %s not convertible to EPUB for Kindle (title=%s)",
                file_format,
                best.get("title")
            )
            local_debug.append(f"      ⚠️  SKIPPED: Format {file_format.upper()} not convertible for Kindle delivery")
            local_debug.append(f"         Supported formats: EPUB, MOBI, AZW, AZW3, PDF, TXT")
            append_debug(local_debug)
            mark_item_completed(user, feed)
            return 0, user.name, downloads

        # Actually download using the configured AnnaSource instance.
        try:
            logger.info("Starting download: title=%s format=%s dest_dir=%s", best.get("title"), file_format, dest_dir)
            saved_path = source.download(best, file_format, dest_dir)
            logger.info("Download succeeded: saved to %s", saved_path)
        except ValueError as exc:
            # Handle 429/403 errors gracefully - mark as try later, no traceback
            error_msg = str(exc).lower()
            exc_str = str(exc)  # Keep original for URL extraction

            if "429" in error_msg or "403" in error_msg or "too many requests" in error_msg:
                logger.info("Download throttled (429/403): marking %s for retry later", best.get("title"))
                local_debug.append(f"      Download throttled (too many requests): will retry later")
                # Return 0 (failed) but without traceback - item will retry on next feed run
                append_debug(local_debug)
                mark_item_completed(user, feed)
                return 0, user.name, downloads
            elif "html payload" in error_msg or "returned html" in error_msg:
                # HTML payload error - likely Anna's Archive returned error page
                logger.error("Download returned HTML instead of ebook: %s", exc_str)
                local_debug.append(f"      ERROR: Download returned HTML page instead of ebook file")
                local_debug.append(f"      Book: {best.get('title')} by {best.get('author')}")
                local_debug.append(f"      MD5: {best.get('detail')}")
                local_debug.append(f"      Format requested: {file_format}")
                local_debug.append(f"      Issue details: {exc_str[:300]}")

                # Send error notification
                try:
                    # Extract HTML snippet from error message more effectively
                    html_snippet = None
                    if "HTML snippet:" in exc_str:
                        start_idx = exc_str.find("HTML snippet:") + len("HTML snippet:")
                        html_snippet = exc_str[start_idx:].strip()
                        logger.info("Extracted HTML snippet of %d bytes for error notification", len(html_snippet))
                    else:
                        logger.info("No 'HTML snippet:' marker found in error message. Full error: %s", exc_str[:500])

                    # Check if download failure notifications are enabled
                    if settings_manager.settings.notify_download_failures:
                        send_download_error_notification(
                            settings_manager.settings.smtp,
                            user,
                            title=best.get('title', 'Unknown'),
                            author=best.get('author', 'Unknown'),
                            error_type="HTML_RETURNED",
                            error_details="Server returned an HTML error page instead of the ebook file. This usually means the download link expired or the file is temporarily unavailable.",
                            html_snippet=html_snippet
                        )
                except Exception as e:
                    logger.debug("Failed to send error notification: %s", e)

                append_debug(local_debug)
                mark_item_completed(user, feed)
                return 0, user.name, downloads
            elif "failed to get" in error_msg or "stealth challenge" in error_msg:
                # Failed to GET the download URL (network error, stealth challenge failed, etc.)
                logger.error("Failed to GET download URL: %s", exc_str)
                local_debug.append(f"      ERROR: Failed to GET download URL")
                local_debug.append(f"      Book: {best.get('title')} by {best.get('author')}")
                local_debug.append(f"      MD5: {best.get('detail')}")
                local_debug.append(f"      Format requested: {file_format}")
                # Extract URL from error message if present (after "URL: ")
                if "URL: " in exc_str:
                    url_start = exc_str.find("URL: ") + 5
                    url_part = exc_str[url_start:].strip()
                    local_debug.append(f"      URL: {url_part}")
                else:
                    local_debug.append(f"      Details: {exc_str[:200]}")
                append_debug(local_debug)
                mark_item_completed(user, feed)
                return 0, user.name, downloads
            else:
                # Other download errors - log exception
                logger.exception("Failed to download result")
                local_debug.append(f"      Failed to download: {exc_str}")
                append_debug(local_debug)
                mark_item_completed(user, feed)
                return 0, user.name, downloads
        except Exception as exc:
            logger.exception("Failed to download result")
            local_debug.append(f"      Failed to download: {exc}")
            append_debug(local_debug)
            mark_item_completed(user, feed)
            return 0, user.name, downloads
        # History + normalized cover (prefer Goodreads cover) + stripped description
        goodreads_cover = (best.get("goodreads_meta", {}) or {}).get("cover", "")
        cover = normalize_cover_url(goodreads_cover or item.cover or best.get("cover", ""))
        description = strip_html_tags(
            item.description or best.get("description", "")
        ).strip()
        with history_lock:
            history_manager.record(
                user.name,
                item.title,
                cover,
                best.get("author") or item.author,
                best.get("selected_format", ""),
                feed.url,
                description,
                str(saved_path),
            )

        # Aggressive Goodreads metadata scraping BEFORE library metadata storage
        # so that goodreads_meta gets persisted to library_metadata.json
        if item.link and "goodreads.com" in item.link:
            try:
                logger.info("Scraping Goodreads metadata from %s", item.link)
                goodreads_meta = feed_parser._scrape_goodreads_book(item.link, local_debug)
                if goodreads_meta:
                    best["goodreads_meta"] = goodreads_meta
                    logger.info("Successfully scraped Goodreads metadata: rating=%s cover=%s", 
                               goodreads_meta.get("rating"), 
                               "yes" if goodreads_meta.get("cover") else "no")
                    local_debug.append(f"      Scraped Goodreads metadata: rating={goodreads_meta.get('rating')}, genres={goodreads_meta.get('genres')}")
            except Exception as e:
                logger.debug("Failed to scrape Goodreads metadata for %s: %s", item.link, e)
                local_debug.append(f"      Goodreads scraping failed: {e}")

        upsert_library_metadata_for_download(saved_path, best, item)

        # Cache the cover image to disk for use in emails
        entry_id = get_library_entry_id(saved_path)
        if entry_id:
            # Prefer Goodreads cover if available, otherwise use best result cover
            cover_url = (best.get("goodreads_meta", {}) or {}).get("cover") or best.get("cover")
            if cover_url:
                try:
                    cache_cover_locally(cover_url, entry_id)
                    logger.debug("Cached cover for library entry %s", entry_id)
                except Exception as e:
                    logger.debug("Failed to cache cover for %s: %s", entry_id, e)

        # NOTE: Metadata enrichment (Goodreads search, cover download) is now deferred to
        # background refresh tasks to avoid blocking downloads with heavy I/O
        # Basic metadata (title, author, path) is still recorded immediately
        entry_id = get_library_entry_id(saved_path)
        if entry_id:
            try:
                # Store minimal metadata immediately (no Goodreads lookups)
                metadata = load_library_metadata()
                meta = metadata.get(entry_id) or {}
                meta.update({
                    "id": entry_id,
                    "title": best.get("title") or item.title,
                    "author": best.get("author") or item.author,
                    "path": str(saved_path),
                    "filetype": best.get("selected_format", ""),
                    "cover": best.get("cover", ""),
                })
                metadata[entry_id] = meta
                with library_metadata_lock:
                    LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
                    # Clear the library entries cache since metadata changed
            except Exception:
                logger.debug("Failed to record library metadata for %s", item.title, exc_info=True)

        # Decide whether to auto-send to Kindle for this item
        # Feed-level setting is explicit:
        #   - If feed.auto_send_to_kindle is True, send
        #   - If feed.auto_send_to_kindle is False, don't send
        #   - If feed.auto_send_to_kindle is None, don't send (default is off)
        auto_send = feed.auto_send_to_kindle is True
        oversize = is_oversize_for_kindle(saved_path)
        sent_to_kindle = False

        # Debug auto-send decision
        logger.info("Auto-send decision for %s: feed.auto_send_to_kindle=%s, auto_send=%s, user.kindle_email=%s", 
                    item.title, feed.auto_send_to_kindle, auto_send, user.kindle_email)

        # Check if already sent to Kindle to prevent duplicates
        already_sent = history_manager.kindle_sent(user.name, item.title, item.author)
        if already_sent:
             logger.info("Skipping Kindle auto-send for %s: already sent to %s", item.title, user.kindle_email)
             sent_to_kindle = True
        else:
             sent_to_kindle = False

        # Collect for batch sending later if auto_send is enabled
        logger.debug("Auto-send conditions: auto_send=%s, has_kindle_email=%s, not_already_sent=%s, oversize=%s", 
                     auto_send, bool(user.kindle_email), not already_sent, oversize)
        if auto_send and user.kindle_email and not already_sent:
             if oversize:
                 local_debug.append(
                     "      Skipping auto-send: file exceeds Kindle size limit"
                 )
                 logger.info("Skipping Kindle auto-send for %s: file exceeds size limit (%d bytes)", item.title, saved_path.stat().st_size if saved_path.exists() else 0)
             else:
                  # Queue for batch send (not immediate send)
                  # This prevents duplicate sends and respects rate limits
                  sent_to_kindle = True
                  local_debug.append(
                      f"      Queued for batch Kindle send: {user.kindle_email}"
                  )
                  logger.info("Queued for batch Kindle send: title=%s author=%s user=%s", item.title, best.get('author', item.author), user.name)
                  # Add to downloads list for batch send collection
                  downloads.append((saved_path, best))
                  logger.debug("Added to batch downloads queue: path=%s", saved_path)
        elif auto_send:
             logger.info("Auto-send NOT triggered for %s - conditions: auto_send=%s, has_kindle_email=%s, not_already_sent=%s", 
                        item.title, auto_send, bool(user.kindle_email), not already_sent)

        # Queue notification email (batched, not sent immediately)
        if user.notification_email and settings.smtp.is_configured():
            # Prepare entry for library queue
            entry_for_queue = {
                "title": best.get("title") or item.title,
                "author": best.get("author") or item.author,
                "cover": best.get("cover", ""),
                "file_path": str(saved_path),  # Include path for cover extraction
                "description": strip_html_tags(best.get("description", "")).strip(),
                "rating": best.get("rating") or best.get("goodreads_meta", {}).get("rating"),
                "goodreads_meta": best.get("goodreads_meta", {}),
                "library_id": entry_id,  # Include library_id for cached cover lookup
            }
            queue_library_addition_notification(user, entry_for_queue)
            local_debug.append(
                f"      Queued notification for {user.notification_email}"
            )

        # Progress bookkeeping (one item completed for this feed)
        mark_item_completed(user, feed)
        append_debug(local_debug)
        return 1, user.name, downloads
    # ------------------------------------------------------------------
    # Single pass: parse feeds, register progress, then queue jobs
    # ------------------------------------------------------------------
    run_id = init_progress()
    logger.info(
        "Starting feed run %s with global executor (max_workers=%d)",
        run_id,
        MAX_FEED_WORKERS,
    )
    # First pass: parse all feeds and collect items per feed and per user
    feed_items: List[Tuple[UserSettings, FeedSettings, List[ParsedItem]]] = []
    user_downloads: Dict[str, List[tuple[Path, Dict]]] = {}  # Collect downloads per user for batch sending
    feed_parse_start = time.time()
    # Collect all (user, feed) tuples to parse in parallel
    all_feeds_to_parse = []
    for user in settings.users:
        user_downloads[user.name] = []
        for feed in user.feeds:
            all_feeds_to_parse.append((user, feed))

    logger.info("Parsing %d feeds in parallel with %d workers", len(all_feeds_to_parse), MAX_FEED_WORKERS)

    # Submit all feed parsing tasks to the executor
    parse_futures = {}
    for user, feed in all_feeds_to_parse:
        fut = BACKGROUND_EXECUTOR.submit(_parse_single_feed, user, feed)
        parse_futures[fut] = (user, feed)


    for fut in as_completed(parse_futures.keys()):
        try:
            user, feed, items, local_debug = fut.result(timeout=60)
            if local_debug:
                debug_messages.extend(local_debug)
            if not user or not feed:
                continue

            debug_messages.append(f"  Feed: {feed.url} [{feed.mode}] for {user.name}")

            if not items:
                debug_messages.append("    No parsed items")
                continue

            logger.info("Feed %s has %d total items", feed.url, len(items))

            # DEDUPLICATION: Remove duplicate items within the feed (same title/author)
            # This prevents processing the same book twice if it appears in the feed multiple times
            seen_in_feed = set()
            deduped_items = []
            for item in items:
                # Create a normalized key for deduplication
                title_norm = (item.title or "").lower().strip()
                author_norm = (item.author or "").lower().strip()
                key = (title_norm, author_norm)

                if key not in seen_in_feed:
                    seen_in_feed.add(key)
                    deduped_items.append(item)
                else:
                    logger.debug("Skipping duplicate in feed: title=%s author=%s", item.title, item.author)

            if len(deduped_items) < len(items):
                removed = len(items) - len(deduped_items)
                logger.info("Feed deduplication: removed %d duplicates from %d items", removed, len(items))
                debug_messages.append(f"    Deduplication: removed {removed} duplicate items from feed")

            items_to_process = deduped_items
            logger.info("Feed %s: %d items to process (filtered from %d total)", feed.url, len(items_to_process), len(items))
            if not items_to_process:
                debug_messages.append(f"    All {len(items)} items already processed")
                continue

            # Register this feed for progress tracking
            register_feed_progress(user, feed, len(items_to_process))
            feed_items.append((user, feed, items_to_process))
        except Exception as e:
            user, feed = parse_futures[fut]
            logger.exception("Exception while collecting parsed feed %s: %s", feed.url, e)
            debug_messages.append(f"Failed to process {feed.url}: {e}")
    feed_parse_time = time.time() - feed_parse_start
    logger.info("TIMING: Feed parsing (all feeds) took %.3f seconds", feed_parse_time)
    # No work queued: nothing to do
    logger.info("Feed parsing complete: feed_items has %d feed groups total", len(feed_items))
    for user, feed, items in feed_items:
        feed_mode = getattr(feed, 'mode', 'unknown')
        logger.info("  Feed group: user=%s feed_mode=%s items=%d url=%s", 
                    user.name, feed_mode, len(items), feed.url)

    # STEP 3: Match all feed items against library BEFORE processing any
    logger.info("STEP 3: Starting library comparison for all feed items")
    items_to_skip = set()  # Track items to skip (already in library, and not auto-send)
    items_in_library = {}  # Track items found in library with their metadata: id(item) -> (lookup_key, user, feed, item)

    for user, feed, items_list in feed_items:
         for item in items_list:
             # Check if book already exists in library
             raw_title = (item.title or "").lower().strip()
             raw_author = (item.author or "").lower().strip()
             title_norm = re.sub(r'\s*\([^)]*\)\s*', ' ', raw_title).strip()
             # Normalize author using standardized cleanup_author() function
             author_norm = history_manager.cleanup_author(item.author or "").lower().strip()
             lookup_key = (title_norm, author_norm)

             if lookup_key in library_lookup:
                 # Item is in library - track it for potential auto-send
                 items_in_library[id(item)] = (lookup_key, user, feed, item)
                 # Only skip it if feed is NOT marked for auto-send to Kindle
                 if not (feed.auto_send_to_kindle is True):
                     items_to_skip.add(id(item))
                     logger.info("STEP 3: Matched in library (skipping - not auto-send): title=%s author=%s", item.title, item.author)
                 else:
                     logger.info("STEP 3: Matched in library (keeping for auto-send): title=%s author=%s feed.auto_send_to_kindle=%s", item.title, item.author, feed.auto_send_to_kindle)
             else:
                 logger.debug("STEP 3: Not in library: (%r, %r)", title_norm, author_norm)

    logger.info("STEP 3 complete: matched %d items in library, %d items to process for download, items_in_library for auto-send: %d",
                 len(items_in_library), sum(len(items) for _, _, items in feed_items) - len(items_to_skip), len(items_in_library))

    # Filter out items that are already in library
    filtered_feed_items = []
    for user, feed, items_list in feed_items:
        filtered_items = [item for item in items_list if id(item) not in items_to_skip]
        if filtered_items:
            filtered_feed_items.append((user, feed, filtered_items))
        else:
            # Feed is fully downloaded - skip it entirely
            logger.info("Skipping fully-downloaded feed: user=%s feed_url=%s (0 new items)",
                       user.name, feed.url)

    # If all items were matched, we're done
    if not filtered_feed_items:
        logger.info("All feed items are already in library, nothing to process")
        finalize_progress()
        if debug_messages:
            FEED_DEBUG_LOG.write_text("\n".join(debug_messages))
        return

    feed_items = filtered_feed_items

    # STEP 4: Skip processing library items for auto-send
    # Items that are already in the library should NOT be re-sent to Kindle on every feed run
    # Only NEW items downloaded in THIS feed run should be sent to Kindle
    logger.info("STEP 4: Skipping auto-send of library items - only new downloads get sent to Kindle")
    logger.debug("Library items found: %d (not sending, only new downloads will be sent)", len(items_in_library))

    # NOTE: DISABLED - Library items should NEVER be sent to Kindle via feed processing
    # Only NEW items downloaded in THIS feed run should be sent
    # The following code is commented out to prevent duplicate Kindle sends
    # 
    # items_to_send_by_user = {}
    # for item_id, (lookup_key, user, feed, item) in items_in_library.items():
    #     if feed.auto_send_to_kindle is True:
    #         # ... file finding and batch send logic (DISABLED) ...

    auto_send_library_sent = 0
    logger.info("STEP 4 complete: sent %d library items to Kindle (library items disabled - only new downloads sent)", auto_send_library_sent)



    if not feed_items:
        logger.info("No items to process: feed_items is empty")
        finalize_progress()
        if debug_messages:
            FEED_DEBUG_LOG.write_text("\n".join(debug_messages))
        return

    # Second pass: prioritize smaller feeds first, process sequentially per feed
    # Sort by the number of items to process (ascending) - process smallest feeds first
    # This ensures small feeds complete quickly and aren't blocked by large ones
    feed_items.sort(key=lambda t: len(t[2]))
    logger.info("SORTED: %d feeds to process in smallest-first order", len(feed_items))
    for idx, (user, feed, items_to_process) in enumerate(feed_items):
        logger.info("  Order %d: user=%s items=%d url=%s",
                   idx + 1, user.name, len(items_to_process), feed.url)
    job_count = 0
    logger.info("Starting sequential feed processing for %d feed groups (smallest first)", len(feed_items))
    # Log the processing order
    for idx, (user, feed, items_to_process) in enumerate(feed_items):
        feed_mode = getattr(feed, 'mode', 'unknown')
        logger.info("  Processing order %d: user=%s feed_mode=%s items=%d", 
                    idx + 1, user.name, feed_mode, len(items_to_process))

    for user, feed, items_to_process in feed_items:
        logger.info("Processing feed: user=%s feed_url=%s items=%d", user.name, feed.url, len(items_to_process))
        feed_futures = []

        # Queue all items for this feed
        for item in items_to_process:
            job_count += 1
            logger.info("Queueing job %d: user=%s item_title=%s", job_count, user.name, item.title)
            fut = BACKGROUND_EXECUTOR.submit(process_item, user, feed, item)
            feed_futures.append(fut)

        # Wait for all items in this feed to complete before moving to next feed
        logger.info("Waiting for all %d items in this feed to complete", len(feed_futures))
        completed_count = 0
        for fut in feed_futures:
            try:
                success_count, user_name, downloads = fut.result()
                completed_count += 1
                total_downloads += success_count
                logger.info("Job completed in feed: user=%s success=%d downloads_count=%d (completed %d/%d)", 
                           user_name, success_count, len(downloads), completed_count, len(feed_futures))
                # Collect downloads per user for batch sending
                if downloads and user_name:
                    if user_name not in user_downloads:
                        user_downloads[user_name] = []
                    user_downloads[user_name].extend(downloads)
                # Progress is tracked within process_item
            except Exception as e:
                logger.exception("Error processing feed item: %s", e)

        logger.info("Completed feed: user=%s feed_url=%s items_processed=%d", user.name, feed.url, completed_count)

    logger.info("Processed %d total jobs across all feeds, total downloads: %d", job_count, total_downloads)
    finalize_progress()

    # BATCH SEND all downloaded items marked for auto-send to Kindle (per user)
    # This ensures exactly ONE batch send per user, not individual sends per item
    logger.info("Starting batch send for downloaded auto-send items across %d users", len(user_downloads))
    batch_sent_count = 0
    for user_name, downloads in user_downloads.items():
        if not downloads:
            logger.debug("No downloads to batch send for user: %s", user_name)
            continue

        # Get user object
        user_obj = next((u for u in settings.users if u.name == user_name), None)
        if not user_obj:
            logger.warning("User not found for batch send: %s", user_name)
            continue

        if not user_obj.kindle_email:
            logger.warning("No Kindle email configured for user: %s", user_name)
            continue

        # Batch send all downloads for this user
        try:
            logger.info("Batch sending %d downloaded items to Kindle for user=%s email=%s", 
                       len(downloads), user_name, user_obj.kindle_email)
            send_kindle_batch_email(settings.smtp, user_obj, downloads)
            batch_sent_count += len(downloads)
            logger.info("Successfully batch sent %d items to %s", len(downloads), user_obj.kindle_email)
        except Exception as e:
            logger.exception("Failed to batch send downloaded items for user=%s: %s", user_name, e)

    logger.info("Batch send complete: sent %d total items to Kindle across all users", batch_sent_count)

    # Flush any remaining queued emails (Kindle auto-send and library notifications)
    for user in settings.users:
        # Flush Kindle queue for this user
        flush_kindle_queue(user.name)
        # Flush library notification queue for this user
        flush_library_queue(user.name)

    # Persist feed debug log
    if debug_messages:
        FEED_DEBUG_LOG.write_text("\n".join(debug_messages))

    logger.info(f"Background feed run complete: downloaded {total_downloads} new books")

@app.route("/feeds/stream")
def feeds_stream():
    """
    Server-Sent Events stream for live feed progress updates.
    Emits the entire feed_progress_state as JSON once per second while
    the connection is open.
    """
    def event_stream():
        while True:
            with feed_progress_lock:
                payload = json.dumps(feed_progress_state)
            yield f"data: {payload}\n\n"
            time.sleep(1)
    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

@app.route("/metadata_progress")
def metadata_progress():
    """
    Server-Sent Events stream for live metadata refresh progress updates.
    Emits the metadata_progress_state as JSON once per second while active.
    Always sends final event with active=false before closing.
    """
    logger.info("metadata_progress endpoint accessed, active=%s", metadata_progress_state.get("active"))
    def event_stream():
        event_count = 0
        last_active = None

        while True:
            with metadata_progress_lock:
                state = metadata_progress_state.copy()
                state["total_items"] = state.get("total_books", 0)
                state["completed_items"] = state.get("completed_books", 0)
                payload = json.dumps(state)
                is_active = state.get("active", False)

                if event_count == 0:
                    pass

                event_count += 1

            # Always send the current event
            yield f"data: {payload}\n\n"

            # If we transitioned from active to inactive, close the stream
            if last_active is True and is_active is False:
                logger.info("SSE: State transitioned from active to inactive, closing stream")
                break

            # If started as inactive, close after first event
            if is_active is False and event_count == 1:
                logger.info("SSE: Started with inactive state, closing immediately after first event")
                break

            last_active = is_active
            time.sleep(1)

    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

@app.route("/feeds/view")
def feed_view():
    """Show a lazy, per-user view of current feed items without downloading.
    This page parses each configured feed (similar to /feeds/run) but only
    displays the items we *would* process, grouped by user and feed. Actual
    downloads are triggered lazily, one item at a time, via the manual search
    / autodownload pipeline.
    """
    settings = settings_manager.settings
    feed_groups: Dict[str, List[Dict]] = {}
    debug_log: List[str] = []
    # We reuse the feed parser as in /feeds/run, but we do not launch any
    # downloads. This keeps the page cheap and allows on-demand actions.
    for user in settings.users:
        user_items: List[Dict] = []
        for feed in user.feeds:
            debug_log.append(f"User={user.name} feed={feed.url} [{feed.mode}]")
            try:
                items = feed_parser.parse(feed, debug_log)
            except Exception as exc:
                logger.exception("Failed to parse feed url=%s", feed.url)
                debug_log.append(f"  Failed to parse feed: {exc}")
                continue
            if not items:
                debug_log.append("  No parsed items")
                continue
            for item in items:
                # Skip items we've already downloaded for this user
                if history_manager.seen(user.name, item.title):
                    continue
                user_items.append(
                    {
                        "user": user,
                        "feed": feed,
                        "title": item.title,
                        "author": item.author,
                        "description": strip_html_tags(item.description or "").strip(),
                        "cover": normalize_cover_url(item.cover or ""),
                        "link": item.link,
                    }
                )
        if user_items:
            feed_groups[user.name] = user_items
    return render_template(
        "feed_view.html",
        title="Feed Items",
        feed_groups=feed_groups,
        debug_log=debug_log,
    )


# ---------------------------------------------------------------------------
# Background maintenance worker
# ---------------------------------------------------------------------------

# Default interval (seconds) between maintenance cycles; can be overridden
# via settings. Kept relatively small but not too aggressive to avoid
# hammering low-powered devices or network resources.
DEFAULT_MAINTENANCE_INTERVAL = 900  # 15 minutes



@app.route("/api/generate-epub", methods=["POST"])
def generate_epub():
    """
    Generate GoodBooks EPUB with optional custom IP/port.

    Request JSON:
    {
        "ip_address": "192.168.1.10" (optional, auto-detect if not provided),
        "port": 3000 (optional, defaults to settings.json or 5000)
    }

    Response JSON:
    {
        "success": true/false,
        "epub_path": "/usr/local/bin/GoodBooks/GoodBooks.epub",
        "url": "http://192.168.1.10:3000",
        "size_kb": 1397.2,
        "message": "EPUB generated successfully"
    }
    """
    import subprocess
    import socket

    try:
        # Parse request parameters
        data = request.get_json() or {}
        ip_address = data.get("ip_address")
        port = data.get("port")

        # If IP not provided, detect it
        if not ip_address:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ip_address = s.getsockname()[0]
                s.close()
            except Exception:
                # Fallback to reading from settings
                try:
                    settings = settings_manager.settings
                    # Try to get from local network config
                    ip_address = "192.168.0.9"  # Default fallback
                except Exception:
                    ip_address = "192.168.0.9"

        # If port not provided, get from settings
        if port is None:
            try:
                port = getattr(settings_manager.settings, "server_port", 5000)
            except Exception:
                port = 5000
        else:
            port = int(port)

        # Build the command to run build_epub_v2.py
        cmd = [
            sys.executable,
            str(BASE_DIR / "build_epub_v2.py"),
            str(ip_address),
            str(port)
        ]

        logger.info(f"Generating EPUB with URL: http://{ip_address}:{port}")

        # Run the build script
        result = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            error_msg = result.stderr or "Unknown error"
            logger.error(f"EPUB generation failed: {error_msg}")
            return jsonify({
                "success": False,
                "error": error_msg,
                "message": "Failed to generate EPUB"
            }), 500

        # Get the generated EPUB file info
        epub_path = BASE_DIR / "GoodBooks.epub"
        if not epub_path.exists():
            return jsonify({
                "success": False,
                "error": "EPUB file not found after generation",
                "message": "Generation completed but file missing"
            }), 500

        try:
            size_kb = epub_path.stat().st_size / 1024
        except OSError as e:
            logger.exception("Generated EPUB exists but cannot stat: %s", epub_path)
            return jsonify({
                "success": False,
                "error": "File stat failed after generation",
                "message": "Cannot read file size"
            }), 500

        logger.info(f"EPUB generated successfully: {epub_path} ({size_kb:.1f} KB)")

        return jsonify({
            "success": True,
            "epub_path": str(epub_path),
            "url": f"http://{ip_address}:{port}",
            "size_kb": round(size_kb, 1),
            "message": "EPUB generated successfully"
        }), 200

    except Exception as e:
        logger.exception("Error in /api/generate-epub endpoint")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Internal server error"
        }), 500


def _enrich_entry_worker(entry_idx: int, entry: Dict[str, Any], library_metadata: Dict) -> Tuple[int, Optional[Dict[str, Any]]]:
    """
    Worker function for parallel metadata enrichment.
    Enriches a single library entry and returns (idx, metadata_dict or None).
    Designed to be called from ThreadPoolExecutor.
    """
    try:
        entry_id = entry.get("id")
        if not entry_id:
            return (entry_idx, None)
        
        book_title = entry.get("title", "Unknown")
        meta: Dict[str, Any] = dict(library_metadata.get(entry_id) or {})
        
        # Always set basics
        meta.setdefault("id", entry_id)
        meta.setdefault("title", entry.get("title", ""))
        meta.setdefault("author", entry.get("author", ""))
        meta.setdefault("path", entry.get("path", ""))
        meta.setdefault("filetype", entry.get("filetype", ""))
        meta.setdefault("cover", entry.get("cover", "") or meta.get("cover", ""))
        
        # Check if enrichment is needed
        goodreads_meta = meta.get("goodreads_meta", {}) or {}
        has_genres = bool(goodreads_meta.get("genres"))
        has_rating = goodreads_meta.get("rating") is not None
        has_goodreads_url = bool(goodreads_meta.get("goodreads_url"))
        has_description = bool(meta.get("description") or goodreads_meta.get("description"))
        
        needs_enrichment = (
            not has_description or
            not has_goodreads_url or
            not has_genres or
            not has_rating
        )
        
        if not needs_enrichment:
            # Already has complete metadata
            return (entry_idx, meta)
        
        # Fetch enrichment from Goodreads
        enriched = enrich_library_metadata_from_goodreads(entry)
        if enriched:
            meta.update(enriched)
            # Clear failed flag if enrichment succeeded
            meta.pop("failed_to_enrich", None)
        else:
            # Mark as failed to prevent infinite loops on unfindable books
            meta["failed_to_enrich"] = True
            logger.debug("Marking entry %s as failed_to_enrich (could not find on Goodreads)", entry_id)
        
        return (entry_idx, meta)
    except Exception as e:
        logger.exception("Worker thread failed to enrich entry %d: %s", entry_idx, e)
        return (entry_idx, None)


def _run_maintenance_cycle() -> None:
    """Perform a single maintenance cycle.

    This is designed to be safe and best-effort only. It should never raise
    out of the function; all errors are logged and ignored so the thread can
    keep running.
    """
    # Check if background jobs are disabled
    try:
        settings = settings_manager.settings
        if settings.disable_background_jobs:
            logger.debug("Background maintenance: skipped (background jobs disabled)")
            return
    except Exception:
        logger.exception("Failed to check disable_background_jobs setting")
        return

    logger.info("Background maintenance: cycle start")

    # 1) Warm the library scan cache
    try:
        entries = build_library_entries()
        logger.info("Background maintenance: library scan returned %d entries", len(entries))
    except Exception:
        logger.exception("Background maintenance: build_library_entries() failed")
        entries = []

    # 2) Enrich library metadata for entries missing rich fields
    #    Follows same guidelines as run_feeds():
    #    - Only fetches search results metadata (no AA download link discovery)
    #    - Scrapes Goodreads for ratings, genres, descriptions
    #    - Best-effort only; errors don't block the cycle
    library_metadata = load_library_metadata()
    any_changes = False
    enriched_count = 0
    logger.debug("Background maintenance: loaded library metadata with %d entries", len(library_metadata))
    
    # Pre-scan to filter items needing enrichment
    entries_needing_enrichment = filter_entries_needing_enrichment(entries, library_metadata)

    logger.info(
        "Background maintenance: %d of %d library items need enrichment (missing description, rating, genres, or Goodreads URL)",
        len(entries_needing_enrichment),
        len(entries)
    )
    
    # Initialize progress tracking for background maintenance with filtered count
    with metadata_progress_lock:
        metadata_progress_state["active"] = True
        metadata_progress_state["total_books"] = len(entries_needing_enrichment)
        metadata_progress_state["completed_books"] = 0
        metadata_progress_state["start_time"] = time.time()
        metadata_progress_state["percentage"] = 0
        metadata_progress_state["type"] = "background-maintenance"
        metadata_progress_state["eta_seconds"] = None
        metadata_progress_state["current_book"] = ""
        metadata_progress_state["current_step"] = ""
        metadata_progress_state["incomplete_books"] = len(entries_needing_enrichment)
    
    # Track last progress report time for 20-minute interval reports
    last_progress_report = time.time()
    enriched_this_session = 0

    # Use ThreadPool for parallel metadata enrichment
    # Experiments show ~8 workers before hitting Goodreads rate limits
    max_workers = min(8, max(2, (os.cpu_count() or 4)))
    logger.info("Starting metadata enrichment with %d parallel workers", max_workers)
    
    # Dictionary to hold enrichment results
    enrichment_results = {}
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all jobs to thread pool
        futures = {
            executor.submit(_enrich_entry_worker, idx, entry, library_metadata): idx 
            for idx, entry in enumerate(entries_needing_enrichment)
        }
        
        # Process results as they complete (not in submission order)
        from concurrent.futures import as_completed
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result_idx, enriched_meta = future.result(timeout=120)
                if enriched_meta:
                    enrichment_results[result_idx] = enriched_meta
                    any_changes = True
                    enriched_count += 1
                    
                    # Update progress
                    with metadata_progress_lock:
                        metadata_progress_state["completed_books"] = result_idx + 1
                        total = len(entries_needing_enrichment)
                        if total > 0:
                            metadata_progress_state["percentage"] = int((result_idx + 1) / total * 100)
                            elapsed = time.time() - metadata_progress_state["start_time"]
                            if result_idx + 1 > 0:
                                rate = elapsed / (result_idx + 1)
                                remaining = rate * (total - result_idx - 1)
                                metadata_progress_state["eta_seconds"] = max(0, int(remaining))
            except Exception as e:
                logger.warning("Parallel enrichment failed for entry %d: %s", idx, e)
    
    # Update library_metadata with all results
    for idx, enriched_meta in enrichment_results.items():
        entry_id = entries_needing_enrichment[idx].get("id")
        if entry_id:
            library_metadata[entry_id] = enriched_meta
            any_changes = True
            enriched_count += 1

    # Persist to disk only if changes were made
    if any_changes:
        try:
            with library_metadata_lock:
                LIBRARY_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
                LIBRARY_METADATA_PATH.write_text(json.dumps(library_metadata, indent=2))
                # Clear the library entries cache since metadata changed
                global _LIBRARY_ENTRIES_CACHE, _LIBRARY_ENTRIES_LAST_SCAN
                _LIBRARY_ENTRIES_CACHE = []
                _LIBRARY_ENTRIES_LAST_SCAN = 0.0
            logger.info(
                "Background maintenance: enriched %d of %d incomplete items in this cycle, persisted metadata. Total now complete: %d/%d",
                enriched_count,
                len(entries_needing_enrichment),
                len(entries) - (len(entries_needing_enrichment) - enriched_count),
                len(entries)
            )
        except Exception:
            logger.exception("Background maintenance: failed to save library metadata")

    # 3) Backfill metadata for history entries (when they map to files)
    try:
        history_entries = history_manager.load()
    except Exception:
        logger.exception("Background maintenance: failed to load history for backfill")
        history_entries = []

    from pathlib import Path as _Path

    for h in history_entries:
        try:
            path_str = h.get("path") or ""
            if not path_str:
                continue
            file_path = _Path(path_str)
            if not file_path.exists():
                continue

            # Build a minimal "best" dict from the history entry so that
            # upsert_library_metadata_for_download can attach metadata if the
            # file lives under a library root.
            best = {
                "title": h.get("title") or file_path.stem,
                "author": h.get("author", ""),
                "description": h.get("description", ""),
                "cover": h.get("cover", ""),
            }
            upsert_library_metadata_for_download(file_path, best)
        except Exception:
            logger.exception(
                "Background maintenance: failed to backfill metadata for history entry %s",
                h.get("title"),
            )

    # 4) Run feeds automatically with progress tracking
    #    Reuses _run_feeds_background() to show progress bar and process all items
    logger.info("Background maintenance: starting automatic feed run")
    try:
        _run_feeds_background()
        logger.info("Background maintenance: automatic feed run completed")
    except Exception:
        logger.exception("Background maintenance: automatic feed run failed")
    finally:
        # ALWAYS guarantee active is set to False immediately after feeds, even on exception
        with metadata_progress_lock:
            metadata_progress_state["active"] = False
            metadata_progress_state["percentage"] = 0
            metadata_progress_state["eta_seconds"] = None
    
    # Send batch email with all metadata enrichment failures
    if metadata_enrichment_failures and settings.notify_metadata_failures:
        try:
            user_obj = settings.users[0] if settings.users else None
            send_batched_metadata_enrichment_failures(settings.smtp, user_obj, metadata_enrichment_failures)
        except Exception as e:
            logger.exception("Failed to send enrichment batch email: %s", e)
        finally:
            with metadata_enrichment_failures_lock:
                metadata_enrichment_failures.clear()

                logger.info("Background maintenance: cycle end - progress state reset")

    # Send missing metadata report email
    try:
        logger.info("Background maintenance: generating missing metadata report")
        missing_metadata_list = []
        
        for entry in entries:
            entry_id = entry.get("id")
            if not entry_id:
                continue
            
            missing_fields = get_missing_fields_for_entry(library_metadata, entry_id)
            if missing_fields:
                missing_metadata_list.append({
                    "title": entry.get("title", "Unknown"),
                    "author": entry.get("author", "Unknown"),
                    "missing_fields": missing_fields
                })
        
        if missing_metadata_list:
            logger.info("Background maintenance: %d entries with missing metadata, sending report", len(missing_metadata_list))
            send_missing_metadata_report_email(
                settings.smtp,
                missing_metadata_list,
                enriched_count=enriched_count
            )
        else:
            logger.info("Background maintenance: all metadata complete, no report needed")
    except Exception as e:
        logger.exception("Failed to send missing metadata report: %s", e)

    # Check if EPUB has been updated and send to all users if it has
    try:
        logger.debug("Checking for EPUB updates...")
        if check_and_distribute_epub_update(settings_manager):
            logger.info("EPUB updated and distributed to users")
        else:
            logger.debug("EPUB is up-to-date, no distribution needed")
    except Exception as e:
        logger.exception("Failed during EPUB distribution check: %s", e)
    
    # 4) Cache all metadata covers to local files
    try:
        covers_cached = _cache_metadata_covers_background(library_metadata, limit=99999)
        if covers_cached > 0:
            logger.info("Background maintenance: cached %d cover URLs to local files", covers_cached)
            # Save updated metadata
            save_library_metadata(library_metadata)
    except Exception as e:
        logger.debug("Failed to cache covers in background: %s", e)

    logger.info("Background maintenance: cycle end")


def _background_maintenance_worker() -> None:
    """Background thread body that periodically runs maintenance cycles."""
    import time as _time

    while True:
        try:
            # Allow settings to override the interval if present
            try:
                interval = float(
                    getattr(
                        settings_manager.settings,
                        "maintenance_interval_seconds",
                        DEFAULT_MAINTENANCE_INTERVAL,
                    )
                    or DEFAULT_MAINTENANCE_INTERVAL
                )
            except Exception:
                interval = DEFAULT_MAINTENANCE_INTERVAL

        except Exception:
            # Fallback to the default if anything goes wrong
            interval = DEFAULT_MAINTENANCE_INTERVAL

        # Clamp to something sane (at least 60 seconds)
        if interval < 60:
            interval = 60.0

        _run_maintenance_cycle()

        try:
            _time.sleep(interval)
        except Exception:
            # If sleep is interrupted for some reason, just continue
            continue


def start_background_maintenance_thread() -> threading.Thread:
    """Start the background maintenance worker thread (daemon)."""
    t = threading.Thread(
        target=_background_maintenance_worker,
        name="background-maintenance",
        daemon=True,
    )
    t.start()
    return t


# Start the background maintenance worker immediately when the module is imported.
BACKGROUND_MAINTENANCE_THREAD = start_background_maintenance_thread()
# Configure global download concurrency (Semaphore in search_engine.py).
set_download_concurrency(
    getattr(settings_manager.settings, "max_concurrent_downloads", 2)
)


if __name__ == "__main__":
    try:
        port = int(os.environ.get("PORT", 5000))
    except Exception:
        port = 5000
    try:
        app.run(host="0.0.0.0", port=port)
    except Exception:
        logging.exception("Flask crashed")
        raise

def _cache_metadata_covers_background(metadata: Dict, limit: int = 50) -> int:
    """
    Background task to cache HTTP cover URLs to local files.
    Caches up to `limit` covers per call to avoid timeouts.
    Updates metadata entries to point to cached files.
    Returns count of covers cached.
    """
    import hashlib
    from io import BytesIO
    
    cached_count = 0
    
    for entry_id, meta in metadata.items():
        cover = meta.get('cover', '')
        if not cover or not cover.startswith(('http://', 'https://')):
            continue
        
        # Already cached?
        if '/covers/' in cover:
            continue
        
        try:
            # Download cover
            resp = requests.get(cover, timeout=5)
            if resp.status_code != 200:
                continue
            
            # Validate it's an image
            img = Image.open(BytesIO(resp.content))
            
            # Generate cache filename from URL hash
            url_hash = hashlib.md5(cover.encode()).hexdigest()
            cache_file = COVERS_DIR / f"{url_hash}.jpg"
            
            # Save as JPEG
            if img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            img.save(cache_file, 'JPEG', quality=95, optimize=True)
            # Store relative path from app root
            meta['cover'] = f"data/covers/{url_hash}.jpg"
            cached_count += 1
            
            if cached_count >= limit:
                break
                
        except Exception as e:
            logger.debug(f"Failed to cache cover from {cover[:50]}...: {e}")
    
    return cached_count

@app.route("/admin/cache-covers", methods=["POST"])
def admin_cache_covers():
    """
    Manual trigger to cache all HTTP cover URLs to local files.
    This is useful for testing or accelerating the background process.
    Returns JSON with caching results.
    """
    try:
        metadata = load_library_metadata()
        limit = request.json.get("limit", 999999) if request.json else 999999
        
        cached_count = _cache_metadata_covers_background(metadata, limit=limit)
        
        # Save updated metadata
        LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
        
        # Clear caches since metadata changed
        _LIBRARY_METADATA_CACHE = {}
        _LIBRARY_METADATA_MTIME = 0.0
        return jsonify({
            "success": True,
            "cached": cached_count,
            "message": f"Cached {cached_count} cover URLs to local files"
        })
    except Exception as e:
        logger.exception("Failed to cache covers: %s", e)
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def main():
    """Main entry point for GoodBooks application."""
    import os
    from waitress import serve
    
    # Get port from environment or default to 5000
    port = int(os.environ.get('GOODBOOKS_PORT', 5000))
    host = os.environ.get('GOODBOOKS_HOST', '0.0.0.0')
    
    logger.info(f"Starting GoodBooks on {host}:{port}")
    serve(app, host=host, port=port)


if __name__ == "__main__":
    main()


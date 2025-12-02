#!/usr/bin/python3
import re
import logging
import os
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
from concurrent.futures import ThreadPoolExecutor
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
)

from logging_config import configure_logging
from parser_engine import FeedParser, ParsedItem
from search_engine import AnnaSource, SearchOptions, set_download_concurrency
from settings_manager import HistoryManager, SettingsManager, UserSettings, FeedSettings
import time
import uuid

feed_progress_lock = Lock()

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

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret-key")

BASE_DIR = Path(__file__).resolve().parent
KINDLE_ATTACHMENT_LIMIT_BYTES = 20 * 1024 * 1024
# Settings / history managers
settings_manager = SettingsManager(BASE_DIR / "data" / "settings.json")
history_manager = HistoryManager(BASE_DIR / "data" / "history.json")
history_lock = threading.Lock()
debug_lock = threading.Lock()
library_metadata_lock = threading.Lock()
_SEARCH_CACHE_LOCK = threading.Lock()

# In-memory mirror of disk-backed search cache
_SEARCH_CACHE_LOADED = False
_SEARCH_CACHE: Dict[str, Dict] = {}

# Configure logging based on settings
logger = configure_logging(BASE_DIR, getattr(settings_manager.settings, "log_level", "INFO"))

# Feed parser + search source
FEED_CACHE_PATH = BASE_DIR / "data" / "feed_cache.json"
FEED_DEBUG_LOG = BASE_DIR / "data" / "feed_debug.log"
SEARCH_CACHE_PATH = BASE_DIR / "data" / "search_cache.json"
# Library metadata + constants
LIBRARY_METADATA_PATH = BASE_DIR / "data" / "library_metadata.json"
_LIBRARY_METADATA_CACHE: Dict[str, Dict] = {}
_LIBRARY_ENTRIES_CACHE: List[Dict] = []
_LIBRARY_ENTRIES_LAST_SCAN: float = 0.0
_LIBRARY_METADATA_MTIME: float = 0.0

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
_env_feed_workers = int(os.environ.get("MAX_FEED_WORKERS", "4"))
_cfg_feed_workers = getattr(settings_manager.settings, "max_feed_workers", 0) or 0
MAX_FEED_WORKERS = _cfg_feed_workers if _cfg_feed_workers > 0 else _env_feed_workers

_cfg_downloads = getattr(settings_manager.settings, "max_concurrent_downloads", 2) or 2

# Anna's Archive source with per-process download semaphore
source = AnnaSource(
    timeout=settings_manager.settings.request_timeout,
    max_concurrent_downloads=_cfg_downloads,
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
# Search cache helpers (disk-backed, used for RSS/HTML feed searches only)
# ---------------------------------------------------------------------------

def _load_search_cache() -> Dict[str, Dict]:
    """Load search cache from disk once into _SEARCH_CACHE."""
    global _SEARCH_CACHE_LOADED, _SEARCH_CACHE
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
    try:
        SEARCH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SEARCH_CACHE_PATH.write_text(json.dumps(_SEARCH_CACHE, indent=2))
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
    # Preserve the exact query text (including whitespace/punctuation) for cache keys
    # so feed/user-provided titles aren't altered before lookups.
    cache_key = options.query or query or ""
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
      * Prefer results that have at least one of allowed_formats.
      * Among them, prefer non-PDF for e-ink devices.
      * Prefer results whose title matches the expected title.
      * Strongly prefer results whose author matches the expected author.
      * Fall back to the first result if all else fails.
    """
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

        # Title similarity
        if expected_title_tokens:
            rtoks = tokens(result.get("title") or "")
            if rtoks:
                common = expected_title_tokens & rtoks
                if common:
                    overlap = len(common) / max(1, len(expected_title_tokens))
                    score_val += int(round(overlap * 10))  # up to +10
                else:
                    score_val -= 5  # no shared title tokens -> mild penalty

        # Author similarity
        if expected_author_tokens:
            atoks = tokens(result.get("author") or "")
            if atoks:
                common_a = expected_author_tokens & atoks
                if common_a:
                    score_val += 20  # strong bump when authors overlap
                else:
                    score_val -= 10  # explicit author mismatch -> penalty

        return score_val

    if not results:
        return None

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


def normalize_cover_url(raw: str) -> str:
    if not raw:
        return ""
    cover = raw.strip()
    if cover.startswith("http"):
        second = cover.find("http", 1)
        if second != -1:
            cover = cover[:second]
    match = re.search(r"https?://[^\s]+", cover)
    return match.group(0) if match else cover


def send_kindle_email(
    smtp_config,
    user: UserSettings,
    saved_path: Path,
    result: Dict,
):
    """
    Email the downloaded file directly to the user's Kindle address.
    """
    if not user.kindle_email or not smtp_config.is_configured():
        return

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

    with saved_path.open("rb") as f:
        data = f.read()
    maintype = "application"
    subtype = "octet-stream"
    msg.add_attachment(
        data,
        maintype=maintype,
        subtype=subtype,
        filename=saved_path.name,
    )

    try:
        smtp_config.send(msg)
        logger.info(
            "Sent Kindle email to %s for %s",
            user.kindle_email,
            saved_path.name,
        )
    except Exception:
        logger.exception("Failed to send Kindle email for %s", saved_path)

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

    cover_url = result.get("cover") or (item.cover if item else "") or ""

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
    esc_cover = html.escape(cover_url or "")

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

    # HTML-only body (no plaintext fallback, per your instructions)
    msg.set_content(html_body, subtype="html")

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
            getattr(settings_manager.settings, "library_scan_ttl_seconds", 30.0) or 30.0
        )
    except Exception:
        ttl = 30.0

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
            mtime = path.stat().st_mtime
            rel_unix = str(relpath).replace(os.sep, "/")
            key = f"{str(root.resolve())}::{rel_unix}"

            meta = metadata.get(key, {})
            title = meta.get("title") or path.stem
            author = meta.get("author", "")
            cover = meta.get("cover", "")
            filetype = (meta.get("filetype") or path.suffix.lstrip(".")).lower()
            is_direct = filetype in DIRECT_DL_EXTENSIONS

            genres = meta.get("genres")
            if isinstance(genres, str):
                genres = [g.strip() for g in genres.split(",") if g.strip()]
            elif genres is None:
                genres = []
            language = meta.get("language")
            publish_date = meta.get("publish_date")
            rating = meta.get("rating")
            goodreads_link = meta.get("goodreads_link")

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
                    # Surface genres as tags for chips/filter UI
                    "tags": genres,
                }
            )

    # Update cache and return the freshly scanned list.
    _LIBRARY_ENTRIES_CACHE = entries
    _LIBRARY_ENTRIES_LAST_SCAN = now
    return entries
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
    cover = best.get("cover") or getattr(item, "cover", "") or ""
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
    genres = best.get("genres") or getattr(item, "genres", None)
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
        or getattr(item, "rating", None)
    )
    rating_count = best.get("rating_count") or getattr(item, "rating_count", None)
    edition_format = best.get("edition_format") or getattr(item, "edition_format", None)
    edition_published = best.get("edition_published") or getattr(item, "edition_published", None)
    edition_language = best.get("edition_language") or getattr(item, "edition_language", None)
    reviews_html = best.get("reviews_html") or getattr(item, "reviews_html", "")
    filetype = best.get("selected_format") or file_path.suffix.lstrip(".").lower()

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
            if value not in (None, "", [], {}):
                entry[field] = value

        for key in keys:
            entry = metadata.get(key, {})
            set_if_value(entry, "title", title)
            set_if_value(entry, "author", author)
            set_if_value(entry, "cover", cover)
            set_if_value(entry, "description", description)
            set_if_value(entry, "goodreads_link", goodreads_link)
            set_if_value(entry, "genres", genres)
            set_if_value(entry, "language", language)
            set_if_value(entry, "publish_date", publish_date)
            set_if_value(entry, "rating", rating)
            set_if_value(entry, "rating_count", rating_count)
            set_if_value(entry, "edition_format", edition_format)
            set_if_value(entry, "edition_published", edition_published)
            set_if_value(entry, "edition_language", edition_language)
            set_if_value(entry, "reviews_html", reviews_html)
            set_if_value(entry, "filetype", filetype)
            metadata[key] = entry

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
    meta.setdefault("rating_count", entry.get("rating_count"))
    meta.setdefault("edition_format", entry.get("edition_format", ""))
    meta.setdefault("edition_published", entry.get("edition_published", ""))
    meta.setdefault("edition_language", entry.get("edition_language", ""))
    meta.setdefault("reviews_html", entry.get("reviews_html", ""))

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
            query = f"{entry.get('title', '')} {entry.get('author', '')}".strip()
            if query:
                # Make a small, format-aware search
                allowed_formats = [entry.get("filetype", "epub") or "epub"]

                # Pick *some* Kindle type – use first user if we have one
                settings = settings_manager.settings
                kindle_type = "standard"
                if getattr(settings, "users", None):
                    kindle_type = settings.users[0].kindle_type or "standard"

                options = SearchOptions(
                    query=query,
                    language="en",
                    extensions=allowed_formats,
                    autodownload=False,
                    preferred_formats=allowed_formats,
                    kindle_type=kindle_type,
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
                        meta["description"] = strip_html_tags(desc).strip()

                    # Goodreads link
                    gr_link = (
                        best.get("goodreads_link")
                        or best.get("goodreads_url")
                        or meta.get("goodreads_link")
                    )
                    if gr_link:
                        meta["goodreads_link"] = gr_link

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

                    # Rating count
                    r_count = best.get("rating_count")
                    if r_count is not None:
                        try:
                            meta["rating_count"] = int(r_count)
                        except (TypeError, ValueError):
                            pass

                    # Edition + reviews
                    for field in (
                        "edition_format",
                        "edition_published",
                        "edition_language",
                        "reviews_html",
                    ):
                        if best.get(field):
                            meta[field] = best[field]

        except Exception:
            logger.exception(
                "Failed to enrich library metadata for id=%s title=%s",
                library_id,
                entry.get("title"),
            )

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
):
    """
    Send one or more Kindle emails for all downloads of a user in a feed run,
    grouping multiple attachments into each email up to ~max_bytes (~20MB).
    """
    if not downloads or not user.kindle_email or not smtp_config.is_configured():
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

        for saved_path, result in batch:
            with saved_path.open("rb") as f:
                data = f.read()
            msg.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=saved_path.name,
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

    current_batch: List[tuple[Path, Dict]] = []
    current_size = 0

    for saved_path, result in downloads:
        size = saved_path.stat().st_size
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
@app.route("/")
def index():
    """
    Library home page.

    Phase 8:
      * Hierarchical folder navigation using a 'prefix' query param.
      * Filters by genre, author, and direct-download-only.
      * Sorting + pagination over file entries.
      * When filters are active, we show a flat view (no folder cards) for the
        current folder subtree.
    """
    settings = settings_manager.settings

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
    direct_only = request.args.get("direct_only", "") == "on"

    # Pagination
    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    if page < 1:
        page = 1

    # Load all entries + narrow to the current prefix subtree
    entries_all = build_library_entries()

    def under_prefix(entry: Dict) -> bool:
        rel = entry.get("relpath", "")
        if not prefix:
            # Root: everything is in scope
            return True
        # Under prefix if rel == prefix or starts with "prefix/"
        return rel == prefix or rel.startswith(prefix + "/")

    entries_in_scope = [e for e in entries_all if under_prefix(e)]

    # Build filter option sets (only within current prefix subtree)
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

    if direct_only:
        filtered_entries = [e for e in filtered_entries if e.get("is_direct")]

    filters_active = bool(genre_filter or author_filter or direct_only)

    # ------------------------------------------------------------------
    # When filters are active: flat view over the subtree
    # ------------------------------------------------------------------
    folder_cards: List[Dict] = []
    if filters_active:
        entries_sorted = sort_library_entries(filtered_entries, sort_key)
        per_page = max(
            1, int(getattr(settings, "library_items_per_page", 50) or 50)
        )
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
            folder_cards.append(
                {
                    "name": folder_name,
                    "prefix": sub_prefix,
                }
            )

        entries_sorted = sort_library_entries(files_here, sort_key)
        per_page = max(
            1, int(getattr(settings, "library_items_per_page", 50) or 50)
        )
        total_items = len(entries_sorted)
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
    )
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
    return send_from_directory(directory, filename, as_attachment=True)
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
                "source": "library",
                "download_url": "",
                "ext": file_path.suffix.lstrip(".").lower(),
                "description": meta.get("description", ""),
            }

            send_kindle_email(smtp_config, user, file_path, result)
            send_notification_email(smtp_config, user, result)
            sent += 1
        except Exception:
            errors += 1
            logger.exception(
                "Failed to send library item in batch to Kindle: %s",
                entry.get("id"),
            )

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
    # Preserve user-entered spacing; avoid trimming so AA sees the exact text.
    query = request.args.get("q", "")
    user_id = request.args.get("user", "").strip()
    selected_language = request.args.get("lang", "en").strip() or "en"
    selected_ext = request.args.getlist("ext")
    autodownload = request.args.get("autodownload", "0") in {"1", "on", "true"}
    autosend = request.args.get("autosend", "0") in {"1", "on", "true"}
    display_results = []
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
        flash(
            "Select a user or disable Autodownload / Auto-send to Kindle.",
            "danger",
        )
        autodownload = False
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
        try:
            search_options = SearchOptions(
                query=query,
                language=selected_language,
                extensions=selected_ext,
                # We no longer filter sources from the UI; AA sees all sources.
                autodownload=autodownload,
                preferred_formats=selected_ext,
                kindle_type=kindle_type,
                # Cheap manual-search mode:
                resolve_downloads=False,
                max_rows=45,
                max_results=45,
            )
            results, debug_log = source.search(query, options=search_options)
            logger.info(
                "Search completed for query='%s' with %d results",
                query,
                len(results),
            )

            # Optional: Autodownload + (optional) Auto-send
            if autodownload and user_obj and results:
                best = select_best_result(results, selected_ext, kindle_type)
                if not best:
                    flash("No suitable result found for autodownload.", "warning")
                else:
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
                        "manual",  # still a "manual" type from the UI
                        description,
                        str(saved_path),
                    )
                    upsert_library_metadata_for_download(saved_path, best)
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


                    # Auto-send to Kindle if requested
                    if autosend and user_obj.kindle_email and settings.smtp.is_configured():
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
    total_pages = max(1, math.ceil(total_results / page_size) if total_results else 1)
    if total_results:
        page = min(page, total_pages)
        start = (page - 1) * page_size
        end = start + page_size
        display_results = results[start:end]

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

@app.route("/book/<path:entry_id>")
def book_detail(entry_id):
    """
    Detailed view for a single library item.
    Only reachable by clicking a cover image (no nav entry).
    """
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

@app.route("/settings", methods=["GET", "POST"], endpoint="settings")
def settings_view():
    global MAX_FEED_WORKERS
    if request.method == "POST":
        try:
            settings_manager.update_from_form(request.form)
            MAX_FEED_WORKERS = getattr(
                settings_manager.settings,
                "max_feed_workers",
                int(os.environ.get("MAX_FEED_WORKERS", "4")),
            )
            set_download_concurrency(
                getattr(settings_manager.settings, "max_concurrent_downloads", 2)
            )
            flash("Settings saved.", "success")
            logger.info("Settings updated via form")
            return redirect(url_for("settings"))
        except Exception:
            logger.exception("Failed to update settings from form")
            flash("Failed to save settings, check logs.", "danger")

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
        entry["is_direct"] = entry.get("filetype", "").lower() in {
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
                continue
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
    # We keep the existing "entries|reverse" behavior in the template by
    # slicing from the tail of the list: page 1 shows the newest items.
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

    # For page 1 we want the newest items; entries are stored oldest -> newest,
    # and the template uses entries|reverse. To keep that behavior, we slice
    # from the tail and still let the template reverse within the page.
    if total_items == 0:
        page_entries = []
    else:
        end = total_items - (page - 1) * per_page
        start = max(0, end - per_page)
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
    )
@app.route("/history/download/<int:index>")
def history_direct_download(index: int):
    """
    Directly download a file referenced by a history entry.
    """
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
    """
    try:
        index = int(request.form.get("index", "-1"))
    except ValueError:
        index = -1
    user_name = request.form.get("user_name", "").strip()

    entries = history_manager.load()
    if index < 0 or index >= len(entries):
        flash("History entry not found.", "warning")
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
    }

    try:
        send_kindle_email(smtp_config, user, path, result)
        send_notification_email(smtp_config, user, result)
        flash(
            f"Sent '{entry.get('title')}' to {user.kindle_email or user.name}.",
            "success",
        )
    except Exception:
        logger.exception("Failed to send history item to Kindle")
        flash("Failed to send book to Kindle.", "danger")

    return redirect(url_for("history"))


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
    user_name = request.form.get("user", "").strip()
    result_id = request.form.get("result_id", "").strip()
    selected_format = request.form.get("format", "").strip()

    logger.debug(
        "Manual download requested user=%s result_id=%s format=%s",
        user_name,
        result_id,
        selected_format,
    )

    if not user_name or not result_id:
        flash("Missing user or result selection.", "danger")
        return redirect(url_for("search"))

    user = next(
        (u for u in settings_manager.settings.users if u.name == user_name),
        None,
    )
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("search"))

    cached = source.cached_result(result_id)
    if not cached:
        flash("Search result not found in cache. Please search again.", "danger")
        return redirect(url_for("search"))
  # Copy so we don't mutate the cached object in-place
    best = dict(cached)
    if selected_format:
        best["selected_format"] = selected_format

    # Ensure downloads are resolved if search ran in "cheap" mode.
    try:
        best = source.resolve_downloads_for_result(best)
    except Exception:
        logger.exception("Failed to resolve downloads for manual download")
        flash("Failed to resolve download links for the selected result.", "danger")
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
        flash("No downloadable format selected for this result.", "danger")
        return redirect(url_for("search"))

    try:
        saved_path = source.download(best, file_format, dest_dir)
    except Exception as exc:
        logger.exception("Manual download failed")
        flash(f"Download failed: {exc}", "danger")
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

    oversize = is_oversize_for_kindle(saved_path)
    sent_to_kindle = False
    if user.kindle_email and settings.smtp.is_configured():
        if oversize:
            logger.warning(
                "File %s is larger than 20MB; Kindle may reject it, attempting send anyway.",
                saved_path,
            )
            flash(
                "File is larger than 20MB; Kindle may reject it, but we attempted to send it anyway.",
                "warning",
            )
        send_kindle_email(settings.smtp, user, saved_path, best)
        sent_to_kindle = True
    if user.notification_email and settings.smtp.is_configured():
        send_notification_email(settings.smtp, user, best)

    flash(f"Downloaded {best.get('title', saved_path.name)}", "success")
    return redirect(url_for("history"))

@app.route("/feeds/run", methods=["POST"])
def run_feeds():
    """
    Run all configured RSS/HTML feeds.
    This version also:
    - Prioritizes smaller feeds first (by number of items to process).
    - Tracks per-user/per-feed progress in the in-memory feed_progress_state
      so that the UI can subscribe via Server-Sent Events.
    """
    settings = settings_manager.settings
    total_downloads = 0
    debug_messages: List[str] = []
    # Make sure the debug log directory exists
    FEED_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
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
    def process_item(user: UserSettings, feed: FeedSettings, item) -> int:
        """
        Worker for a single (user, feed, item).
        Returns:
            1 if a book was successfully downloaded, 0 otherwise.
        """
        local_debug: List[str] = []
        # Preserve the feed-provided query exactly (including spaces/punctuation)
        # so Anna's Archive sees the same text the user curated.
        query = html.unescape(f"{item.title} {item.author}")
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
            return 0
        # If no results, try relaxed author-less search
        if not results:
            local_debug.append("      No results, retrying without author")
            try:
                search_options = SearchOptions(
                    query=item.title,
                    language="en",
                    extensions=feed.filetypes,
                    autodownload=False,
                    preferred_formats=feed.filetypes,
                    kindle_type=user.kindle_type,
                )
                results, search_debug = search_with_cache(
                    item.title,
                    search_options,
                    persist=True,
                )
                local_debug.extend([f"      {msg}" for msg in search_debug])
            except Exception as exc:
                logger.exception(
                    "Search retry failed for item title=%s", item.title
                )
                local_debug.append(f"      Retry search failed: {exc}")
                append_debug(local_debug)
                return 0
        if not results:
            local_debug.append("      No search results found")
            logger.info("No results for title=%s", item.title)
            append_debug(local_debug)
            return 0
        best = select_best_result(
            results,
            feed.filetypes,
            user.kindle_type,
            expected_title=item.title,
            expected_author=item.author,
        )
        # Resolve downloads now that we know which match to grab. This also
        # ensures we persist the final resolved URL so later stages don't need
        # to fall back to FakeResponse-style shims.
        try:
            best = source.resolve_downloads_for_result(best)
        except Exception as exc:
            local_debug.append(f"      Failed to resolve downloads: {exc}")
            logger.exception("Failed to resolve downloads for feed item")
            append_debug(local_debug)
            return 0

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
            return 0
        local_debug.append(
            f"      Selected best match {best.get('title')} ({best.get('selected_format')}) "
            f"from {len(results)} results"
        )
        logger.info(
            "Downloading title=%s format=%s",
            best.get("title"),
            file_format,
        )
        # Download to the correct directory
        try:
            if feed.mode == "html" and getattr(feed, "save_dir", ""):
                dest_dir = resolve_download_dir(feed.save_dir)
            else:
                dest_dir = resolve_download_dir(
                    user.save_dir or settings.default_download_dir
                )
        except Exception as exc:
            logger.exception("Failed to resolve destination directory")
            local_debug.append(f"      Failed to resolve destination directory: {exc}")
            append_debug(local_debug)
            return 0
        # Actually download using the configured AnnaSource instance.
        try:
            saved_path = source.download(best, file_format, dest_dir)
        except Exception as exc:
            logger.exception("Failed to download result")
            local_debug.append(f"      Failed to download: {exc}")
            append_debug(local_debug)
            return 0
        # History + normalized cover + stripped description
        cover = normalize_cover_url(item.cover or best.get("cover", ""))
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
        upsert_library_metadata_for_download(saved_path, best, item)
        # Decide whether to auto-send to Kindle for this item
        auto_send = getattr(feed, "auto_send_to_kindle", False) or getattr(
            user, "auto_send_to_kindle", False
        )
        oversize = is_oversize_for_kindle(saved_path)
        sent_to_kindle = False
        user_download.append((saved_path, best))
        # Kindle delivery (conditional on auto_send)
        if auto_send and user.kindle_email and settings.smtp.is_configured():
            if oversize:
                local_debug.append(
                    "      Skipping auto-send: file exceeds Kindle size limit"
                )
            else:
                local_debug.append(
                    f"      Auto-sending to Kindle address {user.kindle_email}"
                )
                sent_to_kindle = send_to_kindle(
                    settings.smtp,
                    user.kindle_email,
                    saved_path,
                    user.kindle_type,
                    debug_log=local_debug,
                )
        # Notification email
        if user.notification_email and settings.smtp.is_configured():
            local_debug.append(
                f"      Sending notification email to {user.notification_email}"
            )
            send_notification_email(
                settings.smtp,
                user,
                best,
                item,
                sent_to_kindle=sent_to_kindle,
            )
        # Progress bookkeeping (one item completed for this feed)
        mark_item_completed(user, feed)
        append_debug(local_debug)
        return 1
    # ------------------------------------------------------------------
    # Single pass: parse feeds, register progress, then queue jobs
    # ------------------------------------------------------------------
    futures = []
    run_id = init_progress()
    logger.info(
        "Starting feed run %s with global executor (max_workers=%d)",
        run_id,
        MAX_FEED_WORKERS,
    )
    # First pass: parse all feeds and collect items per feed and per user
    feed_items: List[Tuple[UserSettings, FeedSettings, List[ParsedItem]]] = []
    for user in settings.users:
        debug_messages.append(f"Processing user {user.name}")
        logger.info("Processing feeds for user=%s", user.name)
        user_download = []
        for feed in user.feeds:
            debug_messages.append(f"  Feed: {feed.url} [{feed.mode}] for {user.name}")
            logger.info("Fetching feed url=%s mode=%s", feed.url, feed.mode)
            try:
                items = feed_parser.parse(feed, debug_messages)
            except Exception as exc:
                logger.exception("Failed to parse feed url=%s", feed.url)
                debug_messages.append(f"    Failed to parse feed: {exc}")
                continue
            if not items:
                debug_messages.append("    No parsed items")
                continue
            # Filter out items we've already processed for this user
            items_to_process: List[ParsedItem] = []
            for item in items:
                if history_manager.seen(user.name, item.title):
                    debug_messages.append(f"    Skipping already downloaded: {item.title}")
                    logger.debug(
                        "Skipping already downloaded item title=%s user=%s",
                        item.title,
                        user.name,
                    )
                    continue
                items_to_process.append(item)
            if not items_to_process:
                continue
            # Register this feed for progress tracking
            register_feed_progress(user, feed, len(items_to_process))
            feed_items.append((user, feed, items_to_process))
    # No work queued: nothing to do
    if not feed_items:
        finalize_progress()
        flash("No new feed items to process.", "info")
        if debug_messages:
            FEED_DEBUG_LOG.write_text("\n".join(debug_messages))
        return redirect(url_for("history"))
    # Second pass: prioritize smaller feeds first, then queue jobs
    # Sort by the number of items to process (ascending)
    feed_items.sort(key=lambda t: len(t[2]))
    for user, feed, items_to_process in feed_items:
        for item in items_to_process:
            fut = BACKGROUND_EXECUTOR.submit(process_item, user, feed, item)
            futures.append(fut)
    # ------------------------------------------------------------------
    # Wait for all queued jobs, aggregate results
    # ------------------------------------------------------------------
    if not futures:
        finalize_progress()
        flash("No new feed items to process.", "info")
        if debug_messages:
            FEED_DEBUG_LOG.write_text("\n".join(debug_messages))
        return redirect(url_for("history"))
    for fut in futures:
        try:
            total_downloads += fut.result()
        except Exception:
            logger.exception("Feed worker crashed")
            total_downloads += 0
    finalize_progress()
    # Persist feed debug log and show a summary message
    flash(
        f"Feeds complete: downloaded {total_downloads} new books.",
        "success",
    )
    if debug_messages:
        FEED_DEBUG_LOG.write_text("\n".join(debug_messages))
    return redirect(url_for("history"))
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


def _run_maintenance_cycle() -> None:
    """Perform a single maintenance cycle.

    This is designed to be safe and best-effort only. It should never raise
    out of the function; all errors are logged and ignored so the thread can
    keep running.
    """
    logger.info("Background maintenance: cycle start")

    # 1) Warm the library scan cache
    try:
        entries = build_library_entries()
        logger.info("Background maintenance: library scan returned %d entries", len(entries))
    except Exception:
        logger.exception("Background maintenance: build_library_entries() failed")
        entries = []

    # 2) Backfill library metadata for scanned entries
    for entry in entries:
        try:
            ensure_library_metadata(entry)
        except Exception:
            logger.exception(
                "Background maintenance: ensure_library_metadata() failed for %s",
                entry.get("id"),
            )

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

    # 4) Warm feed parser cache for configured feeds so that /feeds/view and
    #    /feeds/run have cheaper first-use latency.
    try:
        settings = settings_manager.settings
        debug_log: List[str] = []
        # Reset per-run cache between cycles to avoid unbounded growth
        try:
            feed_parser.reset_run_cache()
        except Exception:
            # If reset_run_cache is not available for any reason, we simply skip it
            pass

        for user in getattr(settings, "users", []):
            for feed in getattr(user, "feeds", []):
                try:
                    feed_parser.parse(feed, debug_log)
                except Exception:
                    logger.exception(
                        "Background maintenance: failed to parse feed %s for user %s",
                        getattr(feed, "url", ""),
                        getattr(user, "name", ""),
                    )
        if debug_log:
            try:
                FEED_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
                FEED_DEBUG_LOG.write_text("\n".join(debug_log))
            except Exception:
                logger.exception("Background maintenance: failed to write feed debug log")
    except Exception:
        logger.exception("Background maintenance: feed warm-up failed")

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

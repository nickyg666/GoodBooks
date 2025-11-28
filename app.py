#!/usr/bin/python3
import re
import logging
import os
import traceback
import json
import threading
from dataclasses import asdict
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import html
from concurrent.futures import ThreadPoolExecutor
from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from logging_config import configure_logging
from parser_engine import FeedParser, ParsedItem
from search_engine import AnnaSource, SearchOptions
from settings_manager import HistoryManager, SettingsManager, UserSettings

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret-key")

BASE_DIR = Path(__file__).resolve().parent

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
    FEED_DEBUG_LOG
    )
source = AnnaSource(timeout=settings_manager.settings.request_timeout)
MAX_FEED_WORKERS = int(os.environ.get("MAX_FEED_WORKERS", "4"))

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

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
) -> Optional[Dict]:
    """
    Pick the "best" search result.

    Heuristic:
      * Prefer results that offer high-priority Kindle formats:
          azw3 > azw > mobi > epub > pdf > others
      * Respect feed allowed_formats when present.
      * Nudge toward formats that work best for the given kindle_type:
          - paperwhite: strongly prefers azw3/azw/mobi, dislikes pdf
          - oasis/scribe: happy with azw3/azw/mobi/epub, mild dislike of pdf
      * Slightly prefer earlier (higher-ranked) search results.
    """
    if not results:
        return None

    allowed = {f.lower() for f in (allowed_formats or []) if f}

    def fmt_score(fmt: str) -> int:
        fmt = (fmt or "").lower()
        base_order = {
            "azw3": 6,
            "azw": 5,
            "mobi": 4,
            "epub": 3,
            "pdf": 1,
        }
        score = base_order.get(fmt, 0)

        if allowed and fmt in allowed:
            score += 3

        kt = (kindle_type or "").lower()
        if "paperwhite" in kt:
            # Old-school e-ink: really wants azw/mobi
            if fmt in {"azw3", "azw", "mobi"}:
                score += 3
            elif fmt == "epub":
                score += 1
            elif fmt == "pdf":
                score -= 2
        elif any(k in kt for k in ("oasis", "scribe")):
            # Newer devices: epub is first-class
            if fmt in {"azw3", "azw", "mobi", "epub"}:
                score += 2
            elif fmt == "pdf":
                score -= 1

        return score

    def overall_score(result: Dict, idx: int) -> int:
        formats = [f.lower() for f in result.get("formats", [])]
        if not formats:
            # Very hard to use a result if it has no format metadata
            return -10_000

        per_format_scores = [fmt_score(f) for f in formats]
        best_format_score = max(per_format_scores) if per_format_scores else 0

        # Encourage results that have *some* overlap with allowed_formats
        has_allowed = any(f in allowed for f in formats) if allowed else True
        allowed_bonus = 5 if has_allowed else 0

        # Slight bias toward earlier search results
        positional_bonus = max(0, 5 - idx)

        # Small bump for variety of available formats
        variety_bonus = min(len(formats), 3)

        return best_format_score * 10 + allowed_bonus + positional_bonus + variety_bonus

    best_result: Optional[Dict] = None
    best_idx = -1
    best_score = -10**9

    for idx, r in enumerate(results):
        score = overall_score(r, idx)
        if score > best_score:
            best_score = score
            best_result = r
            best_idx = idx

    if not best_result:
        return None

    formats = [f.lower() for f in best_result.get("formats", [])]
    chosen_fmt: Optional[str] = None

    # Choose actual download format with the same priority order
    priority_order = ["azw3", "azw", "mobi", "epub", "pdf"]
    for candidate in priority_order:
        if candidate in formats and (not allowed or candidate in allowed):
            chosen_fmt = candidate
            break

    if not chosen_fmt and formats:
        # Fallback: prefer something in allowed, otherwise first format
        for f in formats:
            if allowed and f in allowed:
                chosen_fmt = f
                break
        if not chosen_fmt:
            chosen_fmt = formats[0]

    if chosen_fmt:
        best_result["selected_format"] = chosen_fmt

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
):
    """
    Notify the user that a book was downloaded.
    """
    if not user.notification_email or not smtp_config.is_configured():
        return

    msg = EmailMessage()
    msg["From"] = smtp_config.from_email
    msg["To"] = user.notification_email
    msg["Subject"] = f"Downloaded: {result.get('title')} by {result.get('author') or (item.author if item else '')}"

    # Title / author / format
    title = result.get("title", "")
    author = result.get("author") or (item.author if item else "") or "Unknown"
    selected_format = result.get("selected_format", ", ".join(result.get("formats", [])))

    body_lines = [
        f"Title: {title}",
        f"Author: {author}",
        f"Format: {selected_format}",
    ]

    # Description: strip HTML from RSS or scraped source for the *text* part
    raw_description = result.get("description") or (item.description if item else "")
    description = strip_html_tags(raw_description).strip()
    if description:
        body_lines.append("")
        body_lines.append(description)

    # Cover: prefer source cover; don't mix RSS + source covers into one line
    cover = result.get("cover") or ""
    if cover:
        body_lines.append("")
        body_lines.append(f"Cover (source): {cover}")

    # Plain-text body (unchanged behavior)
    msg.set_content("\n".join(body_lines))

    # ----- NEW: HTML alternative with embedded cover + raw HTML description -----

    # Escape basic fields; leave raw_description as-is so its tags (<br>, <p>, etc.) are preserved
    esc_title = html.escape(title)
    esc_author = html.escape(author)
    esc_format = html.escape(selected_format)

    html_parts: list[str] = []
    html_parts.append("<html><body>")
    html_parts.append(f"<p><strong>Title:</strong> {esc_title}</p>")
    html_parts.append(f"<p><strong>Author:</strong> {esc_author}</p>")
    html_parts.append(f"<p><strong>Format:</strong> {esc_format}</p>")

    if raw_description:
        html_parts.append("<hr>")
        html_parts.append("<div>")
        # raw_description may already contain <br>, <p>, etc. – do NOT escape it
        html_parts.append(str(raw_description))
        html_parts.append("</div>")

    if cover:
        esc_cover = html.escape(cover, quote=True)
        html_parts.append("<hr>")
        html_parts.append(
            f'<p><img src="{esc_cover}" alt="Cover image" '
            'style="max-width: 300px; height: auto;"/></p>'
        )

    html_parts.append("</body></html>")
    html_body = "\n".join(html_parts)

    # Attach HTML alternative
    msg.add_alternative(html_body, subtype="html")

    try:
        smtp_config.send(msg)
        logger.info(
            "Sent notification email to %s for %s",
            user.notification_email,
            result.get("title"),
        )
    except Exception:
        logger.exception("Failed to send notification email")
def _normalize_sort_key(value: str) -> str:
    return (value or "").casefold()


def load_library_metadata() -> Dict[str, Dict]:
    """
    Load library_metadata.json if present.

    Keys are arbitrary strings; in this phase we use a simple composite:
    "<absolute-root>::<relpath-unix-style>".
    """
    if not LIBRARY_METADATA_PATH.exists():
        return {}
    try:
        return json.loads(LIBRARY_METADATA_PATH.read_text())
    except Exception:
        logger.exception("Failed to load library metadata from %s", LIBRARY_METADATA_PATH)
        return {}


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
    """
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
                }
            )

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
    Look up a single library entry by its ID (root::relpath).
    """
    for entry in build_library_entries():
        if entry.get("id") == entry_id:
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
            set_if_value(entry, "filetype", filetype)
            metadata[key] = entry

        try:
            LIBRARY_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
            LIBRARY_METADATA_PATH.write_text(json.dumps(metadata, indent=2))
        except Exception:
            logger.exception("Failed to save library metadata to %s", LIBRARY_METADATA_PATH)




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

    try:
        send_kindle_email(smtp_config, user, file_path, result)
        # Optional: also send a notification email
        send_notification_email(smtp_config, user, result)
        flash(
            f"Sent '{entry.get('title')}' to {user.kindle_email or user.name}.",
            "success",
        )
    except Exception:
        logger.exception("Failed to send library item to Kindle")
        flash("Failed to send book to Kindle.", "danger")

    return redirect(url_for("index"))

@app.route("/search")
def search():
    """
    Search page (moved from the old '/' route).

    This preserves the existing search behavior; only the URL and endpoint name
    have changed.
    """
    # Render search UI + run search if query provided
    query = request.args.get("q", "").strip()
    user_id = request.args.get("user", "").strip()
    selected_language = request.args.get("lang", "en").strip() or "en"
    selected_ext = request.args.getlist("ext")
    selected_sources = request.args.getlist("acc")
    autodownload = request.args.get("autodownload", "0") in {"1", "on", "true"}
    results: List[Dict] = []
    debug_log: List[str] = []

    # If a user is selected, use their Kindle type to influence ranking
    kindle_type = ""
    if user_id:
        user_obj = next(
            (u for u in settings_manager.settings.users if u.name == user_id),
            None,
        )
        if user_obj:
            kindle_type = user_obj.kindle_type

    if query:
        try:
            search_options = SearchOptions(
                query=query,
                language=selected_language,
                extensions=selected_ext,
                sources=selected_sources,
                autodownload=autodownload,
                # Use ext filters as preferred formats, plus device-aware scoring
                preferred_formats=selected_ext,
                kindle_type=kindle_type,
            )
            results, debug_log = source.search(query, options=search_options)
            logger.info(
                "Search completed for query='%s' with %d results",
                query,
                len(results),
            )
        except Exception as exc:
            logger.exception("Search failed for query '%s'", query)
            flash(f"Search failed: {exc}", "danger")

    return render_template(
        "index.html",
        settings=settings_manager.settings,
        title="Search",
        query=query,
        user_id=user_id,
        users=settings_manager.settings.users,
        selected_language=selected_language,
        selected_ext=selected_ext,
        selected_sources=selected_sources,
        autodownload=autodownload,
        available_ext=["pdf", "epub", "mobi", "azw3"],
        available_sources=[
            ("aa_download", "Anna's Archive partners"),
            ("external_download", "External mirrors"),
        ],
        results=results,
        debug_log=debug_log,
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

    metadata_all = load_library_metadata()
    meta = metadata_all.get(entry_id, {})

    return render_template(
        "book_detail.html",
        settings=settings_manager.settings,
        title=entry["title"],
        entry=entry,
        meta=meta,
    )

@app.route("/settings", methods=["GET", "POST"], endpoint="settings")
def settings_view():
    if request.method == "POST":
        try:
            settings_manager.update_from_form(request.form)
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
    entries = history_manager.load()
    debug_log = (
        FEED_DEBUG_LOG.read_text().splitlines()
        if FEED_DEBUG_LOG.exists()
        else []
    )

    # Optional: library roots for mapping history items to /book/<id>
    library_roots = []
    try:
        if "get_library_roots" in globals():
            library_roots = get_library_roots()
    except Exception:
        logger.exception("Failed to get library roots when building history view")

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

    return render_template(
        "history.html",
        entries=entries,
        feed_debug=debug_log,
        settings=settings_manager.settings,
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

    settings = settings_manager.settings
    dest_dir = resolve_download_dir(
        user.save_dir or settings.default_download_dir
    )

    try:
        saved_path = source.download(best, dest_dir)
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

    if user.kindle_email and settings.smtp.is_configured():
        send_kindle_email(settings.smtp, user, saved_path, best)
    if user.notification_email and settings.smtp.is_configured():
        send_notification_email(settings.smtp, user, best)

    flash(f"Downloaded {best.get('title', saved_path.name)}", "success")
    return redirect(url_for("history"))

@app.route("/feeds/run", methods=["POST"])
def run_feeds():
    """
    Process all configured feeds for all users.

    Phase 5 changes:
      * Still does an initial "parse only" pass to log parser errors.
      * Second pass:
          - Builds a list of (user, feed, item) jobs.
          - Uses a ThreadPoolExecutor to run search+download+email per item.
      * Uses disk-backed search cache (search_with_cache with persist=True)
        so RSS/HTML searches are cached to data/search_cache.json.
      * Uses history_lock for thread-safe writes to history.json.
    """
    settings = settings_manager.settings
    total_downloads = 0
    debug_messages: List[str] = []

    # Make sure the debug log directory exists
    FEED_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # First pass: log what we're about to do and catch parse errors
    # ------------------------------------------------------------------
    for user in settings.users:
        debug_messages.append(f"Processing user {user.name}")
        logger.info("Processing feeds for user=%s", user.name)
        for feed in user.feeds:
            debug_messages.append(f"  Feed: {feed.url} [{feed.mode}] for {user.name}")
            logger.info("Fetching feed url=%s mode=%s", feed.url, feed.mode)
            try:
                # Just to capture early parser errors in the log
                _ = feed_parser.parse(feed, debug_messages)
            except Exception:
                logger.exception("Failed to parse feed url=%s", feed.url)

    # ------------------------------------------------------------------
    # Second pass: actual search/download in a thread pool
    # ------------------------------------------------------------------
    jobs = []  # list of (user, feed, item)

    for user in settings.users:
        debug_messages.append(f"Processing user {user.name}")
        for feed in user.feeds:
            debug_messages.append(f"  Feed: {feed.url} [{feed.mode}] for {user.name}")
            try:
                items = feed_parser.parse(feed, debug_messages)
            except Exception as exc:
                logger.exception("Failed to parse feed url=%s", feed.url)
                debug_messages.append(f"    Failed to parse feed: {exc}")
                continue

            for item in items:
                if history_manager.seen(user.name, item.title):
                    debug_messages.append(f"    Skipping already downloaded: {item.title}")
                    logger.debug(
                        "Skipping already downloaded item title=%s user=%s",
                        item.title,
                        user.name,
                    )
                    continue

                jobs.append((user, feed, item))

    def append_debug(lines: List[str]) -> None:
        """Merge per-thread debug lines into the shared debug_messages."""
        if not lines:
            return
        with debug_lock:
            debug_messages.extend(lines)

    def process_item(user, feed, item) -> int:
        """
        Worker for a single (user, feed, item).

        Returns:
            1 if a book was successfully downloaded, 0 otherwise.
        """
        local_debug: List[str] = []
        query = f"{item.title} {item.author}".strip()
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
            # For feeds, we persist search cache to disk
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

        # Retry with simpler query if no results
        if not results:
            alt_query = item.title.strip()
            local_debug.append(
                f"      No results; retrying with normalized title: {alt_query}"
            )
            try:
                retry_options = SearchOptions(
                    query=alt_query,
                    language="en",
                    extensions=feed.filetypes,
                    autodownload=False,
                    preferred_formats=feed.filetypes,
                    kindle_type=user.kindle_type,
                )
                results, search_debug = search_with_cache(
                    alt_query,
                    retry_options,
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

        best = select_best_result(results, feed.filetypes, user.kindle_type)
        if not best:
            local_debug.append("      No matching formats found")
            logger.info(
                "No matching formats for title=%s allowed=%s",
                item.title,
                feed.filetypes,
            )
            append_debug(local_debug)
            return 0

        # Downloads are resolved lazily; show formats from search
        candidate_formats = best.get("formats") or list(
            (best.get("downloads") or {}).keys()
        )
        local_debug.append(
            "      Candidate formats (from search): "
            f"{candidate_formats or 'none'}"
        )
        local_debug.append(
            "      Selected "
            f"{best.get('title')} format={best.get('selected_format')} "
            f"from {len(results)} results"
        )
        logger.info(
            "Downloading title=%s format=%s",
            best.get("title"),
            best.get("selected_format"),
        )

        # Download
        try:
            dest_dir = resolve_download_dir(
                user.save_dir or settings.default_download_dir
            )
            local_debug.append(f"      Saving to {dest_dir}")
            saved_path = source.download(best, dest_dir)
            local_debug.append(f"      Saved: {saved_path.name}")
        except Exception as exc:
            logger.exception(
                "Download failed for title=%s", best.get("title")
            )
            local_debug.append(f"      Download failed: {exc}")
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
        # Kindle delivery
        if user.kindle_email and settings.smtp.is_configured():
            local_debug.append(
                f"      Sending to Kindle: {user.kindle_email}"
            )
            send_kindle_email(settings.smtp, user, saved_path, best)

        # Notification email
        if user.notification_email and settings.smtp.is_configured():
            local_debug.append(
                f"      Sending notification email to {user.notification_email}"
            )
            send_notification_email(settings.smtp, user, best, item)

        append_debug(local_debug)
        return 1

    # Run jobs in a thread pool
    if jobs:
        worker_count = min(MAX_FEED_WORKERS, len(jobs))
        logger.info(
            "Starting threaded feed run with %d workers for %d jobs",
            worker_count,
            len(jobs),
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(process_item, user, feed, item)
                for (user, feed, item) in jobs
            ]
            for fut in futures:
                try:
                    total_downloads += fut.result()
                except Exception:
                    # The worker already logged details; just continue
                    logger.exception("Feed worker crashed")

    flash(
        f"Feed processing complete. Downloaded {total_downloads} new books.",
        "success",
    )
    if debug_messages:
        FEED_DEBUG_LOG.write_text("\n".join(debug_messages))
    return redirect(url_for("history"))

# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = settings_manager.settings.server_port or int(os.environ.get("PORT", 5000))
    debug_mode = settings_manager.settings.log_level.upper() == "DEBUG"
    logger.info("Starting app on port=%s debug=%s", port, debug_mode)
    app.run(host="0.0.0.0", port=port, debug=debug_mode)

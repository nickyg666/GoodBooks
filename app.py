#!/usr/bin/python3
import re
import logging
import os
import traceback
from dataclasses import asdict
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
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

# Configure logging based on settings
logger = configure_logging(BASE_DIR, getattr(settings_manager.settings, "log_level", "INFO"))

# Feed parser + search source
FEED_CACHE_PATH = BASE_DIR / "data" / "feed_cache.json"
FEED_DEBUG_LOG = BASE_DIR / "data" / "feed_debug.log"

feed_parser = FeedParser(
    FEED_CACHE_PATH,
    timeout=settings_manager.settings.request_timeout,
)
source = AnnaSource(timeout=settings_manager.settings.request_timeout)


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


def select_best_result(
    results: List[Dict],
    allowed_formats: List[str],
    kindle_type: str,
) -> Optional[Dict]:
    """
    Pick the "best" search result.

    Heuristic:
      * Prefer results that have at least one of allowed_formats.
      * Among them, prefer non-PDF if we have a Kindle e-ink (Paperwhite, Oasis etc).
      * Fall back to the first result.
    """
    allowed = [f.lower() for f in (allowed_formats or [])]

    def score(result: Dict) -> int:
        formats = [f.lower() for f in result.get("formats", [])]
        has_allowed = any(f in formats for f in allowed) if allowed else bool(formats)
        is_pdf_only = formats and all(f == "pdf" for f in formats)
        score_val = 0
        if has_allowed:
            score_val += 10
        if not is_pdf_only and kindle_type.lower() in {"paperwhite", "oasis", "voyage"}:
                score_val += 5
        # Slight bump for more formats available
        score_val += len(formats)
        return score_val

    if not results:
        return None

    best = max(results, key=score)
    # Set selected_format to something reasonable for later download()
    fmts = [f.lower() for f in best.get("formats", [])]
    for f in allowed or []:
        if f.lower() in fmts:
            best["selected_format"] = f.lower()
            break
    else:
        if fmts:
            best["selected_format"] = fmts[0]

    return best

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
    body_lines = [
        f"Title: {result.get('title', '')}",
        f"Author: {result.get('author') or (item.author if item else '') or 'Unknown'}",
        f"Format: {result.get('selected_format', ', '.join(result.get('formats', [])))}",
    ]

    # Description: strip HTML from RSS or scraped source
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

    msg.set_content("\n".join(body_lines))

    try:
        smtp_config.send(msg)
        logger.info(
            "Sent notification email to %s for %s",
            user.notification_email,
            result.get("title"),
        )
    except Exception:
        logger.exception("Failed to send notification email")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    # Render search UI + run search if query provided
    query = request.args.get("q", "").strip()
    user_id = request.args.get("user", "").strip()
    selected_language = request.args.get("lang", "en").strip() or "en"
    selected_ext = request.args.getlist("ext")
    selected_sources = request.args.getlist("acc")
    autodownload = request.args.get("autodownload", "0") in {"1", "on", "true"}
    results: List[Dict] = []
    debug_log: List[str] = []

    if query:
        try:
            search_options = SearchOptions(
                query=query,
                language=selected_language,
                extensions=selected_ext,
                sources=selected_sources,
                autodownload=autodownload,
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
    return render_template(
        "history.html",
        entries=entries,
        feed_debug=debug_log,
    )


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
        return redirect(url_for("index"))

    user = next(
        (u for u in settings_manager.settings.users if u.name == user_name),
        None,
    )
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("index"))

    cached = source.cached_result(result_id)
    if not cached:
        flash("Search result not found in cache. Please search again.", "danger")
        return redirect(url_for("index"))

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
        return redirect(url_for("index"))

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
    )

    if user.kindle_email and settings.smtp.is_configured():
        send_kindle_email(settings.smtp, user, saved_path, best)
    if user.notification_email and settings.smtp.is_configured():
        send_notification_email(settings.smtp, user, best)

    flash(f"Downloaded {best.get('title', saved_path.name)}", "success")
    return redirect(url_for("history"))


@app.route("/feeds/run", methods=["POST"])
def run_feeds():
    settings = settings_manager.settings
    total_downloads = 0
    debug_messages: List[str] = []

    # Make sure the debug log directory exists
    FEED_DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)

    # First pass: log what we're about to do and catch parse errors with extra detail
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

    # Second pass: actual populate/search/download
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

                query = f"{item.title} {item.author}".strip()
                debug_messages.append(f"    Searching for {query}")
                logger.info("Searching for item title=%s author=%s", item.title, item.author)

                try:
                    results, search_debug = source.search(query)
                    debug_messages.extend([f"      {msg}" for msg in search_debug])
                except Exception as exc:
                    logger.exception("Search failed for item title=%s", item.title)
                    debug_messages.append(f"      Search failed: {exc}")
                    continue

                if not results:
                    # Try a simpler query (just title)
                    alt_query = item.title.strip()
                    debug_messages.append(
                        f"      No results; retrying with normalized title: {alt_query}"
                    )
                    try:
                        results, search_debug = source.search(alt_query)
                        debug_messages.extend([f"      {msg}" for msg in search_debug])
                    except Exception as exc:
                        logger.exception(
                            "Search retry failed for item title=%s", item.title
                        )
                        debug_messages.append(f"      Retry search failed: {exc}")
                        continue

                if not results:
                    debug_messages.append("      No search results found")
                    logger.info("No results for title=%s", item.title)
                    continue

                best = select_best_result(results, feed.filetypes, user.kindle_type)
                if not best:
                    debug_messages.append("      No matching formats found")
                    logger.info(
                        "No matching formats for title=%s allowed=%s",
                        item.title,
                        feed.filetypes,
                    )
                    continue

                debug_messages.append(
                    "      Candidate download formats: "
                    f"{list(best.get('downloads', {}).keys()) or 'none'}"
                )
                debug_messages.append(
                    "      Selected "
                    f"{best.get('title')} format={best.get('selected_format')} "
                    f"from {len(results)} results"
                )
                logger.info(
                    "Downloading title=%s format=%s",
                    best.get("title"),
                    best.get("selected_format"),
                )

                try:
                    dest_dir = resolve_download_dir(
                        user.save_dir or settings.default_download_dir
                    )
                    debug_messages.append(f"      Saving to {dest_dir}")
                    saved_path = source.download(best, dest_dir)
                    debug_messages.append(f"      Saved: {saved_path.name}")
                    total_downloads += 1
                except Exception as exc:
                    logger.exception(
                        "Download failed for title=%s", best.get("title")
                    )
                    debug_messages.append(f"      Download failed: {exc}")
                    continue

                cover = normalize_cover_url(item.cover or best.get("cover", ""))
                description = strip_html_tags(
                    item.description or best.get("description", "")
                ).strip()
                history_manager.record(
                    user.name,
                    item.title,
                    cover,
                    best.get("author") or item.author,
                    best.get("selected_format", ""),
                    feed.url,
                    description,
                )

                # Kindle delivery
                if user.kindle_email and settings.smtp.is_configured():
                    debug_messages.append(
                        f"      Sending to Kindle: {user.kindle_email}"
                    )
                    send_kindle_email(settings.smtp, user, saved_path, best)

                # Notification email
                if user.notification_email and settings.smtp.is_configured():
                    debug_messages.append(
                        f"      Sending notification email to {user.notification_email}"
                    )
                    send_notification_email(settings.smtp, user, best, item)

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

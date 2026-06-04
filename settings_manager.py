import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import logging
import threading

logger = logging.getLogger(__name__)


def _parse_optional_bool(value) -> Optional[bool]:
    """
    Parse a value into Optional[bool].
    - If value is None or not provided, return None (use default)
    - If value is truthy (1, "1", "true", "on", True), return True
    - Otherwise, return False (explicitly disabled)
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "on", "yes"}
    return bool(value)


@dataclass
class FeedSettings:
    url: str
    mode: str = "rss"  # "rss" or "html"
    filetypes: List[str] = field(default_factory=list)
    # HTML-only save directory override; ignored for RSS feeds.
    save_dir: str = ""
    # Per-feed auto-send to Kindle toggle.
    # None = use user setting (default), True = enable, False = disable
    auto_send_to_kindle: Optional[bool] = None


@dataclass
class UserSettings:
    name: str
    save_dir: str
    kindle_type: str = "paperwhite"
    kindle_email: str = ""
    notification_email: str = ""
    feeds: List[FeedSettings] = field(default_factory=list)


@dataclass
class SMTPSettings:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = ""
    use_tls: bool = True

    def is_configured(self) -> bool:
        return bool(self.host and self.from_email)

    def send(self, msg) -> None:
        """
        Send an EmailMessage using the configured SMTP settings.
        """
        if not self.is_configured():
            return

        import smtplib

        with smtplib.SMTP(self.host, self.port) as server:
            if self.use_tls:
                server.starttls()
            if self.username:
                server.login(self.username, self.password)
            server.send_message(msg)


@dataclass
class Settings:
    users: List[UserSettings] = field(default_factory=list)
    default_download_dir: str = "downloads"
    smtp: SMTPSettings = field(default_factory=SMTPSettings)
    log_level: str = "INFO"
    server_port: int = 5000
    request_timeout: int = 60
    # Legacy Goodreads users list (kept for backward compatibility)
    goodreads_users: List[str] = field(default_factory=list)

    # Library configuration
    # If library_root is empty, fall back to default_download_dir.
    library_root: str = ""
    library_extra_dirs: List[str] = field(default_factory=list)
    library_items_per_page: int = 50
    library_default_sort: str = "date_newest"

    # Threading / concurrency
    max_feed_workers: int = 4
    max_concurrent_downloads: int = 4
    
    # Background jobs control
    disable_background_jobs: bool = False
    maintenance_interval_seconds: int = 900  # 15 minutes default
    
    # Global notifications (comma-separated emails)
    notification_emails: str = ""  # Global notification emails
    kindle_emails: str = ""  # Global Kindle delivery emails
    
    # Notification toggles
    notify_download_failures: bool = True  # Send email on download failures
    notify_metadata_failures: bool = True  # Send email on metadata enrichment failures


class SettingsManager:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = self._load()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------
    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()

        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.warning("Failed to load settings from %s: %s", self.path, e)
            return Settings()

        smtp = SMTPSettings(**data.get("smtp", {}))

        users: List[UserSettings] = []
        for u in data.get("users", []):
            feeds: List[FeedSettings] = []
            for f in u.get("feeds", []):
                raw_filetypes = f.get("filetypes", [])
                if isinstance(raw_filetypes, str):
                    filetypes = [
                        ft.strip()
                        for ft in raw_filetypes.split(",")
                        if ft.strip()
                    ]
                else:
                    filetypes = [
                        str(ft).strip()
                        for ft in raw_filetypes
                        if str(ft).strip()
                    ]

                feeds.append(
                    FeedSettings(
                        url=f.get("url", ""),
                        mode=f.get("mode", "rss"),
                        filetypes=filetypes,
                        save_dir=f.get("save_dir", ""),
                        auto_send_to_kindle=_parse_optional_bool(
                            f.get("auto_send_to_kindle")
                        ),
                    )
                )

            users.append(
                UserSettings(
                    name=u.get("name", ""),
                    save_dir=u.get("save_dir", "downloads"),
                    kindle_type=u.get("kindle_type", "paperwhite"),
                    kindle_email=u.get("kindle_email", ""),
                    notification_email=u.get("notification_email", ""),
                    feeds=feeds,
                )
            )

        default_download_dir = data.get("default_download_dir", "downloads")
        log_level = data.get("log_level", "INFO")
        server_port = int(data.get("server_port", 5000))
        request_timeout = int(data.get("request_timeout", 60))

        library_root = data.get("library_root", "")
        library_extra_dirs = data.get("library_extra_dirs", []) or []
        library_items_per_page = int(data.get("library_items_per_page", 50))
        library_default_sort = data.get("library_default_sort", "date_newest")

        max_feed_workers = int(data.get("max_feed_workers", 4))
        max_concurrent_downloads = int(
            data.get("max_concurrent_downloads", 2)
        )

        # Background jobs and maintenance settings (with defaults for old configs)
        disable_background_jobs = bool(data.get("disable_background_jobs", False))
        maintenance_interval_seconds = int(
            data.get("maintenance_interval_seconds", 900)
        )
        
        # Global notifications (with defaults for old configs)
        notification_emails = data.get("notification_emails", "")
        kindle_emails = data.get("kindle_emails", "")
        goodreads_users = data.get("goodreads_users", []) or []
        
        # Notification toggles (with defaults for old configs)
        notify_download_failures = bool(data.get("notify_download_failures", True))
        notify_metadata_failures = bool(data.get("notify_metadata_failures", True))

        return Settings(
            users=users,
            default_download_dir=default_download_dir,
            smtp=smtp,
            log_level=log_level,
            server_port=server_port,
            request_timeout=request_timeout,
            library_root=library_root,
            library_extra_dirs=library_extra_dirs,
            library_items_per_page=library_items_per_page,
            library_default_sort=library_default_sort,
            max_feed_workers=max_feed_workers,
            max_concurrent_downloads=max_concurrent_downloads,
            disable_background_jobs=disable_background_jobs,
            maintenance_interval_seconds=maintenance_interval_seconds,
            notification_emails=notification_emails,
            kindle_emails=kindle_emails,
            goodreads_users=goodreads_users,
            notify_download_failures=notify_download_failures,
            notify_metadata_failures=notify_metadata_failures,
        )

    def save(self) -> None:
        """
        Serialize current settings to JSON.
        """
        import os
        data: Dict[str, object] = {
            "default_download_dir": self.settings.default_download_dir,
            "smtp": asdict(self.settings.smtp),
            "log_level": self.settings.log_level,
            "server_port": self.settings.server_port,
            "request_timeout": self.settings.request_timeout,
            "library_root": self.settings.library_root,
            "library_extra_dirs": self.settings.library_extra_dirs,
            "library_items_per_page": self.settings.library_items_per_page,
            "library_default_sort": self.settings.library_default_sort,
            "max_feed_workers": self.settings.max_feed_workers,
            "max_concurrent_downloads": self.settings.max_concurrent_downloads,
            "disable_background_jobs": self.settings.disable_background_jobs,
            "maintenance_interval_seconds": self.settings.maintenance_interval_seconds,
            "notification_emails": self.settings.notification_emails,
            "kindle_emails": self.settings.kindle_emails,
            "notify_download_failures": self.settings.notify_download_failures,
            "notify_metadata_failures": self.settings.notify_metadata_failures,
            "goodreads_users": self.settings.goodreads_users,
            "users": [],
        }

        for user in self.settings.users:
            user_dict = asdict(user)
            # asdict already converts FeedSettings to dicts, so feeds should already be correct
            data["users"].append(user_dict)

        # Write and sync to disk
        with open(self.path, 'w') as f:
            f.write(json.dumps(data, indent=2))
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------------
    # Update from HTML form
    # ------------------------------------------------------------------
    def update_from_form(self, form: Dict[str, str]) -> None:
        users: List[UserSettings] = []
        user_count = int(form.get("user-count", 0))

        for i in range(user_count):
            prefix = f"user-{i}-"
            name = form.get(prefix + "name", "").strip()
            if not name:
                continue

            save_dir = form.get(prefix + "save_dir", "downloads").strip()
            if not save_dir:
                save_dir = "downloads"

            kindle_type = form.get(prefix + "kindle_type", "paperwhite")
            kindle_email = form.get(prefix + "kindle_email", "").strip()
            notification_email = form.get(
                prefix + "notification_email", ""
            ).strip()

            feeds: List[FeedSettings] = []
            feed_count = int(form.get(prefix + "feed-count", 0))
            for j in range(feed_count):
                feed_prefix = f"{prefix}feed-{j}-"
                removed = form.get(feed_prefix + "removed", "0") == "1"
                if removed:
                    continue

                url = form.get(feed_prefix + "url", "").strip()
                if not url:
                    continue

                mode = form.get(feed_prefix + "mode", "rss").strip() or "rss"
                filetypes_str = form.get(
                    feed_prefix + "filetypes", ""
                ).strip()
                if filetypes_str:
                    filetypes = [
                        ft.strip()
                        for ft in filetypes_str.split(",")
                        if ft.strip()
                    ]
                else:
                    filetypes = []

                # HTML-only save dir and per-feed auto-send
                save_dir_override = form.get(
                    feed_prefix + "save_dir", ""
                ).strip()
                feed_auto_send_value = form.get(
                    feed_prefix + "auto_send_to_kindle", ""
                )
                # If checkbox is checked, it's "on" or "1"
                # If checkbox is unchecked, it's empty string
                # Treat unchecked as False (explicitly disable)
                if feed_auto_send_value in {"1", "on", "true"}:
                    feed_auto_send = True
                elif feed_auto_send_value == "":
                    # Unchecked checkbox - treat as False to veto user setting
                    feed_auto_send = False
                else:
                    feed_auto_send = None

                feeds.append(
                    FeedSettings(
                        url=url,
                        mode=mode,
                        filetypes=filetypes,
                        save_dir=save_dir_override,
                        auto_send_to_kindle=feed_auto_send,
                    )
                )

            users.append(
                UserSettings(
                    name=name,
                    save_dir=save_dir,
                    kindle_type=kindle_type,
                    kindle_email=kindle_email,
                    notification_email=notification_email,
                    feeds=feeds,
                )
            )

        # SMTP settings
        current_smtp = self.settings.smtp
        smtp_settings = SMTPSettings(
            host=form.get("smtp-host", current_smtp.host),
            port=int(form.get("smtp-port", current_smtp.port or 587)),
            username=form.get("smtp-username", current_smtp.username),
            password=form.get("smtp-password", current_smtp.password),
            use_tls=form.get("smtp-use-tls", "on") == "on",
            from_email=form.get(
                "smtp-from-email", current_smtp.from_email
            ),
        )

        # Default download dir
        default_download_dir = (
            form.get(
                "default-download-dir",
                self.settings.default_download_dir,
            ).strip()
            or self.settings.default_download_dir
        )

        # Log level / server settings
        log_level = form.get("log-level", self.settings.log_level)
        server_port = int(
            form.get("server-port", self.settings.server_port)
        )
        request_timeout = int(
            form.get("request-timeout", self.settings.request_timeout)
        )

        # Library + threading config
        library_root = form.get(
            "library-root",
            self.settings.library_root or default_download_dir,
        ).strip()
        if not library_root:
            library_root = default_download_dir

        extra_dirs_str = form.get("library-extra-dirs", "").strip()
        if extra_dirs_str:
            library_extra_dirs = [
                line.strip()
                for line in extra_dirs_str.splitlines()
                if line.strip()
            ]
        else:
            library_extra_dirs = self.settings.library_extra_dirs

        library_items_per_page = int(
            form.get(
                "library-items-per-page",
                self.settings.library_items_per_page or 50,
            )
        )
        library_default_sort = form.get(
            "library-default-sort",
            self.settings.library_default_sort or "date_newest",
        )

        max_feed_workers = int(
            form.get(
                "max-feed-workers",
                self.settings.max_feed_workers or 4,
            )
        )
        max_concurrent_downloads = int(
            form.get(
                "max-concurrent-downloads",
                self.settings.max_concurrent_downloads or 2,
            )
        )

        # Background jobs toggle
        disable_background_jobs = form.get("disable-background-jobs", "") in {"1", "on", "true"}
        
        # Maintenance interval
        maintenance_interval_seconds = int(
            form.get(
                "maintenance-interval-seconds",
                self.settings.maintenance_interval_seconds or 900,
            )
        )
        # Clamp to reasonable values (minimum 60 seconds, maximum 24 hours)
        if maintenance_interval_seconds < 60:
            maintenance_interval_seconds = 60
        if maintenance_interval_seconds > 86400:
            maintenance_interval_seconds = 86400
        
        # Global notifications and Kindle emails
        notification_emails = form.get("notification-emails", "").strip()
        kindle_emails = form.get("kindle-emails", "").strip()
        
        # Notification toggles
        notify_download_failures = form.get("notify-download-failures", "") in {"1", "on", "true"}
        notify_metadata_failures = form.get("notify-metadata-failures", "") in {"1", "on", "true"}

        self.settings = Settings(
            users=users,
            default_download_dir=default_download_dir,
            smtp=smtp_settings,
            log_level=log_level,
            server_port=server_port,
            request_timeout=request_timeout,
            library_root=library_root,
            library_extra_dirs=library_extra_dirs,
            library_items_per_page=library_items_per_page,
            library_default_sort=library_default_sort,
            max_feed_workers=max_feed_workers,
            max_concurrent_downloads=max_concurrent_downloads,
            disable_background_jobs=disable_background_jobs,
            maintenance_interval_seconds=maintenance_interval_seconds,
            notification_emails=notification_emails,
            kindle_emails=kindle_emails,
            notify_download_failures=notify_download_failures,
            notify_metadata_failures=notify_metadata_failures,
        )
        self.save()


class HistoryManager:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Simple in-memory cache for history entries so we don't re-read
        # the JSON file on every request.
        self._cache: List[Dict] = []
        self._mtime: float = 0.0
        self._loaded: bool = False
        # Lock for thread-safe file operations (read-modify-write)
        self._lock = threading.Lock()

    def _load_from_disk(self) -> List[Dict]:
        """Load history entries from disk, updating the in-memory cache."""
        if not self.path.exists():
            self._cache = []
            self._mtime = 0.0
            self._loaded = True
            return []

        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            # If we cannot stat the file for some reason, fall back to a direct
            # read without caching.
            try:
                data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                data = []
            return data

        try:
            text = self.path.read_text()
            data = json.loads(text) if text.strip() else []
        except json.JSONDecodeError:
            data = []

        self._cache = data
        self._mtime = mtime
        self._loaded = True
        return data

    def load(self) -> List[Dict]:
        if not self._loaded:
            return self._load_from_disk()

        if not self.path.exists():
            # File was removed after we loaded it; reset cache.
            self._cache = []
            self._mtime = 0.0
            self._loaded = True
            return []

        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            # If stat fails, just return the last known cache.
            return list(self._cache)

        if self._mtime == mtime:
            # Fast path: return cached data.
            return list(self._cache)

        return self._load_from_disk()

    def seen(self, user: str, title: str) -> bool:
        return any(
            entry
            for entry in self.load()
            if entry.get("user") == user and entry.get("title") == title
        )
    
    def has_file(self, user: str, filename_stem: str) -> bool:
        """
        Check if a file (by stem/basename without extension) exists in history for this user.
        """
        filename_lower = filename_stem.lower()
        for entry in self.load():
            if entry.get("user") != user:
                continue
            # Check if the path field contains the filename
            path = entry.get("path", "")
            if path:
                from pathlib import Path
                path_stem = Path(path).stem.lower()
                if path_stem == filename_lower:
                    return True
        return False
    
    def kindle_sent(self, user: str, title: str, author: str) -> bool:
        """Check if book already sent to Kindle for user to prevent duplicates."""
        # Normalize the incoming author using cleanup_author for consistent comparison
        author_norm = self.cleanup_author(author)
        
        return any(
            entry.get("user") == user and 
            entry.get("title") == title and
            self.cleanup_author(entry.get("author", "")) == author_norm
            for entry in self.load()
            if entry.get("kindle_sent", False)
        )
    
    def cleanup_author(self, author: str) -> str:
        """
        Clean up author names by removing unnecessary semicolons and fixing spacing.
        Attempts to convert formats like "Freida; Mc; Fadden" to "Freida McFadden"
        
        Examples:
            "Freida; Mc; Fadden" -> "Freida McFadden"
            "Amanda; Brittany" -> "Amanda Brittany"
            "Adams; Douglas" -> "Adams Douglas"
            "A.A.; Milne" -> "A.A. Milne"
        """
        if not author:
            return ""
        
        author = author.strip()
        
        # Split by semicolons to get parts
        parts = [p.strip() for p in author.split(';')]
        
        # Filter out empty parts and very short parts that are likely abbreviations of other names
        # Keep parts that are either:
        # 1. Capital letters (like "A." for initial)
        # 2. Words (longer than 1 char, or single capital letter followed by period)
        # 3. Important connectors (like "von", "de", "van", etc.)
        cleaned_parts = []
        for part in parts:
            if not part:
                continue
            # Skip if it's just a period or very short unless it's an initial like "A."
            if len(part) == 1 and part != '.':
                # Single letter - could be initial, keep it
                cleaned_parts.append(part + '.')
            elif len(part) > 1 or part == '.':
                cleaned_parts.append(part)
        
        # Join with spaces, but handle cases where parts should be merged (like Mc + Fadden -> McFadden)
        if not cleaned_parts:
            return author.lower()
        
        # Join the first two parts normally
        if len(cleaned_parts) == 1:
            result = cleaned_parts[0]
        else:
            # Start with first part
            result = cleaned_parts[0]
            
            # For remaining parts, check if previous part is "Mc", "Mac", "O'", "De", "Van", "Von"
            # If so, don't add space (these are name prefixes)
            for i in range(1, len(cleaned_parts)):
                curr_part = cleaned_parts[i]
                prev_part = cleaned_parts[i-1]
                
                # Check if previous part is a name prefix that shouldn't have space
                if prev_part.lower() in ('mc', 'mac', "o'", 'de', 'van', 'von', 'des', 'du', 'da', 'le', 'la'):
                    # No space before this part
                    result += curr_part
                else:
                    # Normal space
                    result += ' ' + curr_part
        
        return result.lower().strip()
    
    def record(
        self,
        user: str,
        title: str,
        cover: str,
        author: str,
        selected_format: str,
        source: str,
        description: str,
        file_path: str,
    ) -> None:
        """
        Record a download entry in history with full metadata.
        
        Args:
            user: Username
            title: Book title
            cover: Cover URL
            author: Author name
            selected_format: File format (epub, mobi, pdf, etc.)
            source: Where it came from ("manual", "feed", feed URL, etc.)
            description: Book description
            file_path: Path to saved file
        """
        try:
            with self._lock:
                # Always reload from disk to ensure we have the latest data
                # This prevents lost updates when multiple threads write concurrently
                entries = self._load_from_disk()
                
                # Determine is_direct based on file extension
                # Direct download formats are those that work with Kindle's native reader
                DIRECT_DL_EXTENSIONS = {"mobi", "prc", "azw", "azw3"}
                filetype = selected_format.lower().strip(".")
                is_direct = filetype in DIRECT_DL_EXTENSIONS
                
                # Generate entry_id for the file
                from pathlib import Path as PathlibPath
                file_path_obj = PathlibPath(file_path)
                # Entry ID format: /absolute/path::relative/path
                # For history, we use a simplified format
                entry_id = file_path
                
                # Create entry with correct field names and structure
                # Clean up author name to remove unnecessary semicolons
                cleaned_author = self.cleanup_author(author)
                entry = {
                    "user": user,
                    "title": title,
                    "cover": cover,
                    "author": cleaned_author,
                    "filetype": filetype,  # Use 'filetype' not 'selected_format'
                    "source": source,
                    "description": description,
                    "path": file_path,
                    "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                    "index": len(entries),  # Sequential index in history
                    "has_path": True,  # File has been downloaded
                    "is_direct": is_direct,  # Whether it's a direct-download format
                    "entry_id": entry_id,  # File identifier
                    "genres": [],  # Empty for now, can be populated later
                }
                entries.append(entry)
                # Write back all entries
                self.path.write_text(json.dumps(entries, indent=2))
                # Invalidate cache to force reload on next access
                self._loaded = False
                self._cache = []
                self._mtime = 0.0
                logger.info(
                    "Recorded download for user=%s title=%s filetype=%s source=%s",
                    user,
                    title,
                    filetype,
                    source,
                )
        except Exception as e:
            logger.error("Failed to record download history: %s", e)
    
    def record_kindle_send(self, user: str, title: str, author: str, email: str) -> None:
        """Record Kindle send to prevent future duplicates."""
        try:
            with self._lock:
                # Always reload from disk to ensure we have the latest data
                entries = self._load_from_disk()
                # Clean the incoming author for comparison
                cleaned_author = self.cleanup_author(author)
                for entry in entries:
                    if (entry.get("user") == user and 
                        entry.get("title") == title and 
                        self.cleanup_author(entry.get("author", "")) == cleaned_author):
                        entry["kindle_sent"] = True
                        entry["kindle_sent_email"] = email
                        entry["kindle_sent_timestamp"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                        # Write back all entries
                        self.path.write_text(json.dumps(entries, indent=2))
                        # Invalidate cache to force reload on next access
                        self._loaded = False
                        self._cache = []
                        self._mtime = 0.0
                        logger.info("Recorded Kindle send for user=%s title=%s", user, title)
                        return
        except Exception as e:
            logger.error("Failed to record Kindle send: %s", e)
    
    # ============================================================================
    # Genre Filtering (consolidated from genre_filter.py)
# ============================================================================

# Genres to exclude from public dropdowns
EXCLUDED_GENRES = {
    'erotica',
    'erotic',
    'bdsm',
    'adult',
    'explicit',
    'hardcore',
    'pornography',
    'adult fiction',
    'adult contemporary'
}

def is_genre_allowed(genre):
    """Check if a genre is allowed (not in exclusion list)"""
    if not genre:
        return False
    return genre.lower().strip() not in EXCLUDED_GENRES

def filter_genres(genres_list):
    """Filter a list of genres, returning only allowed genres"""
    if not genres_list:
        return []
    return [g for g in genres_list if is_genre_allowed(g)]

def filter_genre_dict(genre_dict):
    """Filter a dictionary of genres (keys), returning only allowed genres"""
    if not genre_dict:
        return {}
    return {g: v for g, v in genre_dict.items() if is_genre_allowed(g)}

def get_excluded_genres():
    """Return a copy of the excluded genres set"""
    return EXCLUDED_GENRES.copy()

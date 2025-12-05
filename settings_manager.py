import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List


@dataclass
class FeedSettings:
    url: str
    mode: str = "rss"  # "rss" or "html"
    filetypes: List[str] = field(default_factory=list)
    # HTML-only save directory override; ignored for RSS feeds.
    save_dir: str = ""
    # Per-feed auto-send to Kindle toggle.
    auto_send_to_kindle: bool = False


@dataclass
class UserSettings:
    name: str
    save_dir: str
    kindle_type: str = "paperwhite"
    kindle_email: str = ""
    notification_email: str = ""
    feeds: List[FeedSettings] = field(default_factory=list)
    # Default behavior when this user is selected in the UI.
    auto_send_to_kindle: bool = False


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

    # Library configuration
    # If library_root is empty, fall back to default_download_dir.
    library_root: str = ""
    library_extra_dirs: List[str] = field(default_factory=list)
    library_items_per_page: int = 50
    library_default_sort: str = "date_newest"

    # Threading / concurrency
    max_feed_workers: int = 4
    max_concurrent_downloads: int = 2
    
    # Background jobs control
    disable_background_jobs: bool = False
    maintenance_interval_seconds: int = 900  # 15 minutes default
    
    # Global notifications (comma-separated emails)
    notification_emails: str = ""  # Global notification emails
    kindle_emails: str = ""  # Global Kindle delivery emails


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
        except json.JSONDecodeError:
            return Settings()

        smtp = SMTPSettings(**data.get("smtp", {}))

        users: List[UserSettings] = []
        for u in data.get("users", []):
            # Per-user auto-send, default False if not present.
            user_auto_send = bool(u.get("auto_send_to_kindle", False))

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
                        auto_send_to_kindle=bool(
                            f.get("auto_send_to_kindle", False)
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
                    auto_send_to_kindle=user_auto_send,
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
        )

    def save(self) -> None:
        """
        Serialize current settings to JSON.
        """
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
            "users": [],
        }

        for user in self.settings.users:
            user_dict = asdict(user)
            # Ensure feeds are serialized as simple dicts
            user_dict["feeds"] = [asdict(f) for f in user.feeds]
            data["users"].append(user_dict)

        self.path.write_text(json.dumps(data, indent=2))

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
            auto_send_to_kindle = form.get(
                prefix + "auto_send_to_kindle", ""
            ) in {"1", "on", "true"}

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
                feed_auto_send = form.get(
                    feed_prefix + "auto_send_to_kindle", ""
                ) in {"1", "on", "true"}

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
                    auto_send_to_kindle=auto_send_to_kindle,
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

    def record(
        self,
        user: str,
        title: str,
        cover: str,
        author: str,
        filetype: str,
        source: str,
        description: str = "",
        path: str = "",
    ) -> None:
        """
        Record a single download in history.

        path:
            Optional filesystem path to the saved file. If provided, this enables
            direct-download and send-to-Kindle actions from the history view.
        """
        entries = self.load()
        entry = {
            "user": user,
            "title": title,
            "cover": cover,
            "author": author,
            "filetype": filetype,
            "source": source,
            "description": description,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if path:
            entry["path"] = path
        entries.append(entry)
        # Persist to disk
        self.path.write_text(json.dumps(entries, indent=2))
        # Keep the in-memory cache in sync with what we just wrote.
        self._cache = entries
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = 0.0
        self._loaded = True
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List


@dataclass
class FeedSettings:
    url: str
    mode: str = "rss"  # rss or html
    filetypes: List[str] = field(default_factory=list)


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
    use_tls: bool = True
    from_email: str = ""

    def is_configured(self) -> bool:
        return bool(self.host and self.username and self.from_email)

    def send(self, msg):
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


class SettingsManager:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.settings = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        data = json.loads(self.path.read_text())

        smtp = SMTPSettings(**data.get("smtp", {}))
        users = [
            UserSettings(
                name=u.get("name", ""),
                save_dir=u.get("save_dir", "downloads"),
                kindle_type=u.get("kindle_type", "paperwhite"),
                kindle_email=u.get("kindle_email", ""),
                notification_email=u.get("notification_email", ""),
                feeds=[FeedSettings(**feed) for feed in u.get("feeds", [])],
            )
            for u in data.get("users", [])
        ]
        default_download_dir = data.get("default_download_dir", "downloads")

        return Settings(
            users=users,
            default_download_dir=default_download_dir,
            smtp=smtp,
            log_level=data.get("log_level", "INFO"),
            server_port=int(data.get("server_port", 5000)),
            request_timeout=int(data.get("request_timeout", 60)),
        )

    def save(self):
        payload = {
            "users": [
                {
                    **asdict(user),
                    "feeds": [asdict(feed) for feed in user.feeds],
                }
                for user in self.settings.users
            ],
            "default_download_dir": self.settings.default_download_dir,
            "smtp": asdict(self.settings.smtp),
            "log_level": self.settings.log_level,
            "server_port": self.settings.server_port,
            "request_timeout": self.settings.request_timeout,
        }
        self.path.write_text(json.dumps(payload, indent=2))

    def update_from_form(self, form: Dict[str, str]):
        users: List[UserSettings] = []
        user_count = int(form.get("user-count", 0))

        for i in range(user_count):
            prefix = f"user-{i}-"
            name = form.get(prefix + "name", "").strip()
            if not name:
                continue
            save_dir = form.get(prefix + "save_dir", "downloads").strip()
            kindle_type = form.get(prefix + "kindle_type", "paperwhite")
            kindle_email = form.get(prefix + "kindle_email", "").strip()
            notification_email = form.get(prefix + "notification_email", "").strip()

            feeds: List[FeedSettings] = []
            feed_count = int(form.get(prefix + "feed-count", 0))
            for j in range(feed_count):
                feed_prefix = f"{prefix}feed-{j}-"
                url = form.get(feed_prefix + "url", "").strip()
                if not url:
                    continue
                mode = form.get(feed_prefix + "mode", "rss")
                filetypes_str = form.get(feed_prefix + "filetypes", "")
                filetypes = [
                    f.strip()
                    for f in filetypes_str.split(",")
                    if f.strip()
                ]
                feeds.append(
                    FeedSettings(url=url, mode=mode, filetypes=filetypes)
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

        smtp_settings = SMTPSettings(
            host=form.get("smtp-host", ""),
            port=int(form.get("smtp-port", 587)),
            username=form.get("smtp-username", ""),
            password=form.get("smtp-password", ""),
            use_tls=form.get("smtp-use-tls", "on") == "on",
            from_email=form.get("smtp-from-email", ""),
        )

        default_download_dir = form.get(
            "default-download-dir", self.settings.default_download_dir
        )

        self.settings = Settings(
            users=users,
            default_download_dir=default_download_dir,
            smtp=smtp_settings,
            log_level=form.get("log-level", self.settings.log_level),
            server_port=int(
                form.get("server-port", self.settings.server_port)
            ),
            request_timeout=int(
                form.get("request-timeout", self.settings.request_timeout)
            ),
        )
        self.save()


class HistoryManager:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> List[Dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text())

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
    ):
        entries = self.load()
        entries.append(
            {
                "user": user,
                "title": title,
                "cover": cover,
                "author": author,
                "filetype": filetype,
                "source": source,
                "description": description,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        )
        self.path.write_text(json.dumps(entries, indent=2))

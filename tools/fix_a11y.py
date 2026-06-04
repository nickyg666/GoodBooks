#!/usr/bin/env python3
"""One-shot a11y fix: add aria-label to form fields on settings.html and
recently_added.html. Idempotent — safe to re-run."""
import re
import sys
from pathlib import Path

BASE = Path("/usr/local/bin/GoodBooks")

LABELS = {
    "server-port": "Server Port",
    "log-level": "Log Level",
    "request-timeout": "Request Timeout in seconds",
    "maintenance-interval-seconds": "Maintenance Interval in seconds",
    "notification-emails": "Notification emails (comma separated)",
    "kindle-emails": "Kindle emails (comma separated)",
    "smtp-host": "SMTP host",
    "smtp-port": "SMTP port",
    "smtp-username": "SMTP username",
    "smtp-password": "SMTP password",
    "smtp-from-email": "SMTP from email address",
    "default-download-dir": "Default download directory",
    "library-root": "Library root directory",
    "library-items-per-page": "Library items per page",
    "library-default-sort": "Library default sort order",
    "max-feed-workers": "Max feed workers",
    "max-concurrent-downloads": "Max concurrent downloads",
    "library-extra-dirs": "Library extra directories (one per line)",
}

def add_aria(match: re.Match) -> str:
    full = match.group(0)
    name = match.group(2)
    label = LABELS.get(name)
    if not label:
        return full
    if "aria-label=" in full:
        return full
    if full.endswith("/>"):
        return full[:-2] + f' aria-label="{label}"/>'
    if full.endswith(">"):
        return full[:-1] + f' aria-label="{label}">'
    return full

PATTERN = re.compile(r'<(input|select|textarea)\b[^>]*\bname="([^"]+)"[^>]*>')


def fix_settings():
    p = BASE / "templates" / "settings.html"
    src = p.read_text()
    new = PATTERN.sub(add_aria, src)
    # user-selector
    new = new.replace(
        '<select id="user-selector" style="width: 300px;" onchange="selectUser()">',
        '<select id="user-selector" name="user-selector" aria-label="Select user" style="width: 300px;" onchange="selectUser()">',
    )
    p.write_text(new)
    return src != new


def fix_recently_added():
    p = BASE / "templates" / "recently_added.html"
    src = p.read_text()
    new = src.replace(
        '<select name="limit" onchange="updateFilters()" id="limit-select"',
        '<select name="limit" onchange="updateFilters()" id="limit-select" aria-label="Items per page"',
    )
    p.write_text(new)
    return src != new


if __name__ == "__main__":
    s = fix_settings()
    r = fix_recently_added()
    print(f"settings.html: {'changed' if s else 'no change'}")
    print(f"recently_added.html: {'changed' if r else 'no change'}")
    sys.exit(0)

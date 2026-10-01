"""Resolve the epub navigation URLs from CONFIG, never hardcoded.

build_epub_v2.py hardcoded:

    HOME_URL = f"http://192.168.0.9:5000"
    AWAY_URL = "https://books.a1e.lol/?token=foDcuQIAF5_yVW1ngwAKgeQ-TQYcESvE7XQFDhnaiCw"

That bakes a LAN address and a live auth token into a distributed file, so
every reader gets a link to one specific server and the token is published
to anyone the epub is shared with. It also ignored `server_port` from
settings even though get_server_port() exists two functions above.

Precedence for each URL:
  1. explicit config file value (data/epub_config.json, or the *_url env vars)
  2. settings.json public_url / server_port
  3. the LAN address discovered at build time
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent


def _settings() -> dict:
    p = BASE_DIR / "data" / "settings.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def epub_config() -> dict:
    """data/epub_config.json -- editable without touching code."""
    p = BASE_DIR / "data" / "epub_config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _discover_lan_ip() -> Optional[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip and ip != "0.0.0.0" else None
    except Exception:
        return None


def get_home_url() -> str:
    """In-network / local server URL.

    Order: env GOODBOOKS_HOME_URL -> epub_config home_url -> settings
    public_url -> discovered LAN IP + configured port.
    """
    env = os.environ.get("GOODBOOKS_HOME_URL", "").strip()
    if env:
        return env.rstrip("/")

    cfg = epub_config()
    if cfg.get("home_url"):
        return str(cfg["home_url"]).rstrip("/")

    st = _settings()
    if st.get("public_url"):
        return str(st["public_url"]).rstrip("/")

    ip = _discover_lan_ip()
    if not ip:
        return ""
    port = st.get("server_port", epub_config().get("server_port", 5000))
    return f"http://{ip}:{port}"


def get_away_url() -> str:
    """Public / away-from-home server URL.

    Order: env GOODBOOKS_AWAY_URL -> epub_config away_url (with optional
    token) -> settings public_url -> "" (rendered as a disabled link).
    """
    env = os.environ.get("GOODBOOKS_AWAY_URL", "").strip()
    if env:
        return env.rstrip("/")

    cfg = epub_config()
    away = str(cfg.get("away_url") or "").strip().rstrip("/")
    if away:
        token = str(cfg.get("away_token") or "").strip()
        if token and "?" not in away:
            away = f"{away}?token={token}"
        return away

    st = _settings()
    if st.get("away_url"):
        away = str(st["away_url"]).rstrip("/")
        token = str(st.get("away_token") or "").strip()
        if token and "?" not in away:
            away = f"{away}?token={token}"
        return away

    if st.get("public_url"):
        return str(st["public_url"]).rstrip("/")
    return ""


def resolve_urls() -> Tuple[str, str]:
    return get_home_url(), get_away_url()


if __name__ == "__main__":
    h, a = resolve_urls()
    print("home:", h or "(unset)")
    print("away:", a or "(unset)")

"""
Smoke tests for every public route. <500 = pass.
"""
from __future__ import annotations

import pytest


SMOKE_ROUTES = [
    ("GET", "/"),
    ("GET", "/library/recently-added"),
    ("GET", "/api/users"),
    ("GET", "/settings"),
    ("GET", "/history"),
    ("GET", "/feeds/view"),
    ("GET", "/cover.png"),
    ("GET", "/search"),
    ("GET", "/search?q=test"),
    ("GET", "/book/random"),
]


@pytest.mark.smoke
@pytest.mark.parametrize("method,path", SMOKE_ROUTES)
def test_route_does_not_500(client, method, path):
    r = client.request(method, client.base_url + path, timeout=10, allow_redirects=False)  # type: ignore[attr-defined]
    assert r.status_code < 500, f"{method} {path} -> {r.status_code}: {r.text[:200]}"


@pytest.mark.smoke
def test_api_users_returns_json(client):
    r = client.get(client.base_url + "/api/users", timeout=10)  # type: ignore[attr-defined]
    assert r.status_code == 200
    body = r.json()
    assert body.get("success") is True
    assert isinstance(body.get("users"), list)
    for u in body["users"]:
        assert "name" in u


@pytest.mark.smoke
def test_root_renders_known_landmarks(client):
    r = client.get(client.base_url + "/", timeout=10)  # type: ignore[attr-defined]
    assert r.status_code == 200
    text = r.text.lower()
    for needle in ("library", "history", "settings"):
        assert needle in text, f"expected '{needle}' in /"


@pytest.mark.smoke
def test_settings_page_renders(client):
    r = client.get(client.base_url + "/settings", timeout=10)  # type: ignore[attr-defined]
    assert r.status_code == 200
    text = r.text.lower()
    for needle in ("smtp", "server port", "library"):
        assert needle in text, f"expected '{needle}' on /settings"


@pytest.mark.smoke
def test_history_page_renders(client):
    r = client.get(client.base_url + "/history", timeout=10)  # type: ignore[attr-defined]
    assert r.status_code == 200
    assert "<html" in r.text.lower()


@pytest.mark.smoke
def test_static_theme_files_served(client):
    for theme in ("dark", "sepia", "high-contrast"):
        r = client.get(client.base_url + f"/static/themes/{theme}.css", timeout=10)  # type: ignore[attr-defined]
        assert r.status_code == 200, f"{theme} theme not served"
        assert ":root" in r.text

"""Settings round-trip and validation tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


REQUIRED_TOP_LEVEL = {
    "default_download_dir", "smtp", "log_level", "server_port", "library_root", "users",
}


@pytest.mark.integration
def test_settings_file_is_valid_json(data_dir: Path):
    p = data_dir / "settings.json"
    assert p.exists(), f"missing settings.json at {p}"
    data = json.loads(p.read_text())
    assert isinstance(data, dict)


@pytest.mark.integration
def test_settings_has_required_keys(data_dir: Path):
    p = data_dir / "settings.json"
    data = json.loads(p.read_text())
    missing = REQUIRED_TOP_LEVEL - set(data.keys())
    assert not missing, f"settings.json missing keys: {missing}"


@pytest.mark.integration
def test_smtp_block_is_well_formed(data_dir: Path):
    p = data_dir / "settings.json"
    data = json.loads(p.read_text())
    smtp = data.get("smtp", {})
    for k in ("host", "port", "use_tls"):
        assert k in smtp, f"smtp block missing '{k}'"
    assert isinstance(smtp["port"], int)
    assert isinstance(smtp["use_tls"], bool)


@pytest.mark.integration
def test_users_have_name_and_save_dir(data_dir: Path):
    p = data_dir / "settings.json"
    data = json.loads(p.read_text())
    users = data.get("users", [])
    assert isinstance(users, list)
    for u in users:
        assert "name" in u and u["name"]
        assert "save_dir" in u


@pytest.mark.integration
def test_settings_page_accepts_get(client):
    r = client.get(client.base_url + "/settings", timeout=10)  # type: ignore[attr-defined]
    assert r.status_code == 200

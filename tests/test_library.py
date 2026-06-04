"""Library metadata store tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.mark.integration
def test_library_metadata_loads(data_dir: Path):
    p = data_dir / "library_metadata.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert isinstance(data, dict)


@pytest.mark.integration
def test_library_metadata_keys_are_paths(data_dir: Path):
    p = data_dir / "library_metadata.json"
    data = json.loads(p.read_text())
    sample = list(data.keys())[:20]
    for k in sample:
        assert isinstance(k, str)
        if "/" in k:
            assert k.startswith("/")


@pytest.mark.integration
def test_random_book_redirects(client):
    r = client.get(client.base_url + "/book/random", timeout=10, allow_redirects=False)  # type: ignore[attr-defined]
    assert r.status_code in (301, 302, 303, 307, 308)
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        assert not loc.startswith("http://evil")

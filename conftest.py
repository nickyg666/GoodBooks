"""
Pytest fixtures for GoodBooks.

Strategy:
- By default we test the LIVE running instance (the systemd service).
  That's how the agents.md workflow operates: the user runs the service, the
  tests poke it. This catches real regressions in the integration with the
  real library data on /mnt/8tbdas.
- A `--spawn` flag spawns an in-process Flask app on a free port for hermetic
  unit-ish testing. Library paths can be redirected via env vars.
- `client` fixture returns a requests Session pointed at whichever target.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest
import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def pytest_addoption(parser):
    parser.addoption(
        "--spawn",
        action="store_true",
        help="Spawn an in-process Flask app instead of testing the live service.",
    )
    parser.addoption(
        "--spawn-timeout",
        default=20,
        type=int,
        help="Seconds to wait for a spawned app to become responsive.",
    )


@pytest.fixture(scope="session")
def base_url(request) -> str:
    try:
        url = request.config.getoption("--base-url")
    except (ValueError, Exception):
        url = None
    if not url:
        url = os.environ.get("GOODBOOKS_URL") or "http://127.0.0.1:5000"
    return url


@pytest.fixture(scope="session")
def live_server(request) -> Iterator[str]:
    if not request.config.getoption("--spawn"):
        try:
            url = request.config.getoption("--base-url")
        except (ValueError, Exception):
            url = ""
        if not url:
            url = os.environ.get("GOODBOOKS_URL") or "http://127.0.0.1:5000"
        try:
            r = requests.get(url, timeout=3)
            assert r.status_code < 500, f"live server returned {r.status_code}"
        except Exception as e:
            pytest.skip(f"live GoodBooks not reachable at {url}: {e}")
        yield url
        return

    port = _free_port()
    env = os.environ.copy()
    env["FLASK_RUN_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + request.config.getoption("--spawn-timeout")
    try:
        while time.time() < deadline:
            try:
                r = requests.get(url, timeout=1)
                if r.status_code < 500:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.4)
        else:
            out = proc.stdout.read(4000) if proc.stdout else b""
            proc.terminate()
            pytest.fail(f"spawned app never came up at {url}\n{out.decode(errors='replace')}")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def client(live_server) -> requests.Session:
    s = requests.Session()
    s.base_url = live_server  # type: ignore[attr-defined]
    return s


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return ROOT / "data"


@pytest.fixture
def settings_snapshot(data_dir, request):
    path = data_dir / "settings.json"
    backup = path.with_suffix(path.suffix + f".pytest-bak-{request.node.name}")
    if path.exists():
        backup.write_text(path.read_text())
    yield path
    if backup.exists():
        path.write_text(backup.read_text())
        backup.unlink()

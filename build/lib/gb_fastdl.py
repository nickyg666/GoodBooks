"""Speed-aware mirror selection and racing downloads.

Measured on this host (first 128KB of the same file, all mirrors healthy):

    libgen.la   57 KiB/s   -> a 27MB book ~8.0 min
    libgen.bz   52 KiB/s   -> ~8.9 min
    libgen.gl   42 KiB/s   -> ~11.1 min
    libgen.vg   35 KiB/s   -> ~13.3 min
    libgen.li   27 KiB/s   -> ~16.8 min

The code hardcoded libgen.li, i.e. always the SLOWEST, so a 27MB book took
~17 minutes. Speed varies ~6x between mirrors, so the rank is measured rather
than assumed, cached, and re-measured when stale.

Also races mirrors: start the same file from the fastest few and keep
whichever delivers real bytes first, rather than serially discovering that
the first mirror is slow or returning a 522.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_SPEED_CACHE: Dict[str, float] = {}
_SPEED_STAMP: Dict[str, float] = {}
SPEED_TTL = 900.0
MIN_CHUNK_TO_WIN = 262144          # 256KB of real bytes before claiming a win


def record_speed(host: str, kib_per_s: float) -> None:
    if kib_per_s <= 0:
        return
    prev = _SPEED_CACHE.get(host)
    val = kib_per_s if prev is None else (prev * 0.6 + kib_per_s * 0.4)
    _SPEED_CACHE[host] = val
    _SPEED_STAMP[host] = time.monotonic()


def speed_of(host: str) -> Optional[float]:
    v = _SPEED_CACHE.get(host)
    if v is None:
        return None
    if time.monotonic() - _SPEED_STAMP.get(host, 0.0) > SPEED_TTL:
        return None
    return v


def ranked_mirrors(mirrors: List[str]) -> List[str]:
    """Fastest-known first; unmeasured mirrors keep their SLUM order after
    the measured ones, so a new mirror can still win once it is measured."""
    scored = []
    unmeasured = []
    for m in mirrors:
        s = speed_of(m)
        if s is None:
            unmeasured.append(m)
        else:
            scored.append((s, m))
    scored.sort(reverse=True)
    return [m for _, m in scored] + unmeasured


def note_download(host: str, nbytes: int, seconds: float) -> None:
    if host and seconds > 0 and nbytes > 0:
        record_speed(host, (nbytes / 1024.0) / seconds)


def race_download(urls: List[str], dest_dir: Path, filename: str,
                  timeout: int = 180) -> Optional[Path]:
    """Fetch from several mirrors, keep the first to yield real file bytes.

    Writes to .part and renames on success, so a partial or HTML response is
    never left behind as a finished book.
    """
    import requests
    from gb_libgen_dl import looks_like_html, guess_format

    UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/127.0.0.0 Safari/537.36")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    winner: Dict[str, object] = {}
    done = threading.Event()
    started: Dict[int, float] = {}

    def attempt(idx: int, url: str, host: str) -> None:
        tmp = dest_dir / f".race{idx}.part"
        started[idx] = time.time()
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout,
                             stream=True)
            if r.status_code != 200:
                r.close()
                return
            head = b""
            written = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(65536):
                    if not chunk:
                        continue
                    if len(head) < 4096:
                        head += chunk[: 4096 - len(head)]
                    if written == 0 and looks_like_html(head):
                        r.close()
                        return
                    fh.write(chunk)
                    written += len(chunk)
                    if written >= MIN_CHUNK_TO_WIN and not done.is_set():
                        winner["path"] = str(tmp)
                        winner["host"] = host
                        winner["idx"] = idx
                        done.set()
                        r.close()
                        return
            r.close()
            if written and not done.is_set():
                winner["path"] = str(tmp)
                winner["host"] = host
                winner["idx"] = idx
                done.set()
        except Exception as exc:
            logger.debug("race %s failed: %s", host, exc)

    threads = []
    for i, url in enumerate(urls):
        host = url.split("/")[2] if "//" in url else url
        t = threading.Thread(target=attempt, args=(i, url, host), daemon=True)
        t.start()
        threads.append(t)

    deadline = time.time() + timeout
    while time.time() < deadline and not done.is_set():
        time.sleep(0.2)

    for t in threads:
        t.join(timeout=2)

    raw = winner.get("path")
    if not raw:
        for i in range(len(urls)):
            (dest_dir / f".race{i}.part").unlink(missing_ok=True)
        return None

    tmp = Path(str(raw))
    host = str(winner.get("host") or "")
    try:
        idx = int(str(winner.get("idx")))
    except (TypeError, ValueError):
        idx = -1
    try:
        size = tmp.stat().st_size
    except OSError:
        size = 0
    if host and idx in started:
        note_download(host, size, max(time.time() - started[idx], 0.01))

    fmt = guess_format(filename, "", b"")
    out = dest_dir / f"{Path(filename).stem}.{fmt}"
    n = 1
    while out.exists():
        out = dest_dir / f"{Path(filename).stem} ({n}).{fmt}"
        n += 1
    tmp.rename(out)
    logger.info("raced download: %s (%d bytes) won from %s", out.name, size, host)
    return out

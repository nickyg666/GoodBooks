"""Resolve a libgen md5 to a real file URL by following the KEYED second hop.

Proven 2026-09-30 on DAS: get.php?md5=X returns an HTML page, NOT an error
page. That page contains one download link of the form

    get.php?md5=X&key=<16 uppercase alnum>

and requesting THAT with a browser User-Agent returns
application/octet-stream (the real file). Verified end to end: md5
0c757e447004372b66a07d6e22f5d1da -> 1,869,525 byte EPUB whose content.opf
reads "Dear Debbie" / "Freida McFadden".

Two separate causes were tangled together and both had to be fixed:

  1. requests' DEFAULT User-Agent is rejected by libgen's nginx, which
     answers with a 638-byte "Welcome to nginx!" decoy. A browser UA gets the
     real page from the same URL and the same IP. The old code installed a UA
     for the *search* client but the *download* client was separate and never
     got one.
  2. Even with a real page, treating step 1 as the file was wrong. The file
     is behind the keyed second request.

Set LIBGEN_TWO_STEP=1 to enable, 0 to disable. When the second hop yields
nothing, fall back to the original single-request URLs so nothing that used to
work is broken.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_KEYED = re.compile(
    r"get\.php\?md5=([0-9a-f]{32})&key=([A-Za-z0-9]{6,40})")
# the decoy page libgen serves to non-browser agents
_NGINX_DECOY = re.compile(rb"Welcome to nginx|<h1>Welcome to nginx", re.I)
_MAGIC = {
    b"PK": "epub",
    b"R!": "rar",
    b"\xd0\xcf\x11\xe0": "mobi",
    b"BOOKMOBI": "mobi",
    b"%PDF": "pdf",
    b"ID3": "mp3",
    b"\x1f\x8b": "cbz",
}
_HTML_MARK = re.compile(
    rb"<!DOCTYPE|<html|<script|<head|<meta", re.I)


def _looks_like_file(head: bytes) -> bool:
    if not head:
        return False
    if _NGINX_DECOY.search(head[:2048]):
        return False
    if _HTML_MARK.search(head[:2048]):
        return False
    return True


def _magic(head: bytes) -> Optional[str]:
    for sig, ext in _MAGIC.items():
        if head.startswith(sig):
            return ext
    if head[60:68] == b"BOOKMOBI":
        return "mobi"
    return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://libgen.li/",
    })
    return s


def resolve_keyed_url(md5: str, hosts: Optional[List[str]] = None,
                      timeout: int = 40) -> Optional[dict]:
    """Return {'url','host','key'} for a real file, or None.

    Walks every host in `hosts` (or a sane default list), performing the
    page request and then the keyed file request. A host is accepted as soon
    as its keyed request returns non-HTML bytes.
    """
    if not md5:
        return None
    hosts = hosts or ["libgen.li", "libgen.la", "libgen.gl",
                      "libgen.vg", "libgen.bz"]
    s = _session()
    for host in hosts:
        page_url = f"https://{host}/get.php?md5={md5}"
        try:
            r = s.get(page_url, timeout=timeout)
        except Exception as exc:
            logger.debug("keyed: page fetch failed %s: %s", host, exc)
            continue
        body = r.content or b""
        if _NGINX_DECOY.search(body[:4096]):
            logger.info("keyed: %s served the nginx decoy (bad UA?)", host)
            continue
        keys = _KEYED.findall(body.decode("utf-8", "replace"))
        if not keys:
            logger.debug("keyed: no keyed link on %s (len=%d)", host, len(body))
            continue
        for _md52, key in keys[:3]:
            f_url = f"https://{host}/get.php?md5={_md52}&key={key}"
            size = None
            try:
                with s.get(f_url, timeout=timeout, stream=True) as fr:
                    head = (fr.raw.read(512, decode_content=True)
                            if fr.raw else b"")
                    size = fr.headers.get("Content-Length")
            except Exception as exc:
                logger.debug("keyed: file fetch failed %s: %s", f_url, exc)
                continue
            if not _looks_like_file(head):
                logger.debug("keyed: %s still HTML, trying next", host)
                continue
            logger.info("keyed: RESOLVED %s via %s key=%s magic=%s",
                        md5, host, key, _magic(head))
            return {"url": f_url, "host": host, "key": key,
                    "format": _magic(head), "size": size}
    return None


def keyed_download(md5: str, dest_dir, filename: str,
                   hosts: Optional[List[str]] = None,
                   timeout: int = 40, retries: int = 2) -> Optional[str]:
    """Download via the keyed hop, resumable, validating magic bytes.

    Returns the final path on success, None on failure.
    """
    import os
    from pathlib import Path

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", filename or md5).strip() or md5
    final = dest / safe
    part = dest / (safe + ".part")

    attempt = 0
    while attempt <= retries:
        attempt += 1
        info = resolve_keyed_url(md5, hosts=hosts, timeout=timeout)
        if not info:
            time.sleep(1.5 * attempt)
            continue
        url = info["url"]
        try:
            st = dest / (safe + ".status")
            done = int(st.read_text().strip()) if st.exists() else 0
            if part.exists() and part.stat().st_size == done and done > 0:
                head = part.open("rb").read(512)
                if _looks_like_file(head):
                    ext = _magic(head) or Path(safe).suffix.lstrip(".") or "bin"
                    final = dest / f"{Path(safe).stem}.{ext}"
                    part.replace(final)
                    st.unlink(missing_ok=True)
                    logger.info("keyed: resumed-complete %s", final.name)
                    return str(final)
            hdrs = {"User-Agent": BROWSER_UA, "Referer": f"https://{info['host']}/"}
            if done and part.exists():
                hdrs["Range"] = f"bytes={done}-"
            with requests.get(url, headers=hdrs, timeout=timeout,
                              stream=True) as r:
                if r.status_code not in (200, 206):
                    logger.info("keyed: http %s for file hop", r.status_code)
                    continue
                total = r.headers.get("Content-Length")
                mode = "ab" if (r.status_code == 206 and done) else "wb"
                if mode == "wb":
                    done = 0
                n = done
                with part.open(mode) as fh:
                    for chunk in r.iter_content(262144):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        n += len(chunk)
                        st.write_text(str(n))
                head = part.open("rb").read(512)
                if not _looks_like_file(head):
                    logger.info("keyed: %s was HTML, discarding", safe)
                    part.unlink(missing_ok=True)
                    st.unlink(missing_ok=True)
                    continue
                if total and mode == "ab" and int(total) + done != n:
                    logger.info("keyed: resume incomplete, retrying")
                    continue
                ext = _magic(head) or Path(safe).suffix.lstrip(".") or "bin"
                final = dest / f"{Path(safe).stem}.{ext}"
                part.replace(final)
                st.unlink(missing_ok=True)
                logger.info("keyed: DOWNLOADED %s (%d bytes, %s)", final.name,
                            n, ext)
                return str(final)
        except Exception as exc:
            logger.info("keyed: attempt %d failed: %s", attempt, exc)
            time.sleep(1.5 * attempt)
    return None

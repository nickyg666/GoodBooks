"""Real libgen download path.

libgen's get.php endpoint serves genuine file bytes (verified: HTTP 206,
application/octet-stream, Content-Disposition filename, PK\\x03\\x04 magic for
a CBZ). The app never used it -- it only ever called search_title() and then
reported failure.

libgen_api_enhanced.Book.resolve_direct_download_link cannot be used:
  - it calls requests.get() on the module-global requests, which bypasses the
    browser-UA shim, so libgen answers with the nginx landing page and no GET
    link is found
  - and it ends in a bare `return`, handing the caller None on success

This module does the resolution and the fetch directly, with:
  - a browser User-Agent
  - the same HTML sniffing the AA resolver now uses, so a landing page can
    never be written to disk as a book
  - format detection from Content-Disposition
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

HTMLISH = (b"<!doctype", b"<html", b"<script", b"<head", b"<meta")

# file extension -> magic bytes we can confirm
_MAGIC = {
    "epub": (b"PK\x03\x04", b"application/epub+zip"),
    "cbz": (b"PK\x03\x04", b"application/vnd.comicbook+zip"),
    "zip": (b"PK\x03\x04", b"application/zip"),
    "pdf": (b"%PDF", b"application/pdf"),
    "mobi": (b"BOOKMOBI", b"application/x-mobipocket-ebook"),
    "azw3": (b"BOOKMOBI", b"application/vnd.amazon.ebook"),
    "djvu": (b"AT&TFORM", b"image/vnd.djvu"),
    # Archives that are NOT zips. Comic-book releases are frequently RAR, and
    # a 30MB RAR was previously saved as ".cbr" and then failed ZipFile.
    "rar": (b"Rar!\x1a\x07", b"application/vnd.rar"),
    "cbr": (b"Rar!\x1a\x07", b""),
    "7z": (b"7z\xbc\xaf\x27\x1c", b"application/x-7z-compressed"),
}


def looks_like_html(head: bytes) -> bool:
    return any(m in (head or b"")[:400].lstrip().lower() for m in HTMLISH)


def guess_format(filename: str, ctype: str = "", head: bytes = b"") -> str:
    """Determine the file format.

    Magic bytes are authoritative and are checked FIRST: a real RAR was
    being saved as ".cbr" and then failed ZipFile, because the extension from
    Content-Disposition was trusted over the actual content. Only when the
    content matches nothing known do we fall back to the name, then the
    content-type.
    """
    for ext, (magic, _) in _MAGIC.items():
        if (head or b"").startswith(magic):
            return ext
    ct = (ctype or "").lower()
    if ct:
        for ext, (_, ctype_needle) in _MAGIC.items():
            if ctype_needle and ctype_needle in ct:
                return ext
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].strip().lower()
        if ext.isalnum() and 2 <= len(ext) <= 5:
            return ext
    return "bin"


def _filename_from(headers) -> str:
    cd = ""
    try:
        cd = headers.get("Content-Disposition") or ""
    except Exception:
        cd = ""
    m = re.search(r'filename="?([^";]+)"?', cd)
    return m.group(1).strip() if m else ""


def pick_download_url(book, root: str = "https://libgen.li") -> Optional[str]:
    """Return a real get.php URL for a libgen Book.

    Book.mirrors is not reliable: for many rows it contains only an
    `ads.php` advertising page, which returns a 200 HTML document. Prefer a
    get.php entry if one is present, but otherwise BUILD the get.php URL from
    the md5 -- that endpoint works and is what actually serves the file.
    """
    urls = []
    try:
        urls = [u for u in (getattr(book, "mirrors", None) or []) if u]
    except Exception:
        urls = []
    for u in urls:
        if "get.php" in u:
            return u

    md5 = getattr(book, "md5", None)
    if md5:
        return f"{root.rstrip('/')}/get.php?md5={md5}"
    return urls[0] if urls else None


def _safe_name(name: str) -> str:
    name = re.sub(r"[/\\:*?\"<>|]+", "_", (name or "").strip())
    return re.sub(r"\s+", " ", name)[:180] or "book"


def download_from_libgen(get_url: str, dest_dir: Path, title: str = "",
                         timeout: int = 120, retries: int = 3) -> Optional[Path]:
    """Fetch a real file from a libgen get.php URL and save it.

    Returns the written path, or None if the source did not yield real file
    bytes (an HTML landing page, an error page, or a hard failure). Never
    writes HTML to disk as if it were a book.

    libgen sits behind Cloudflare and returns intermittent 522s (origin
    timeout) even though the same URL succeeds seconds later, so a small
    retry is warranted here.
    """
    import time

    import requests

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, retries + 1):
        try:
            r = requests.get(get_url, headers={"User-Agent": BROWSER_UA},
                             timeout=timeout, stream=True, allow_redirects=True)
        except Exception as exc:
            logger.warning("libgen fetch failed (try %d/%d) %s: %s",
                           attempt, retries, get_url, exc)
            if attempt < retries:
                time.sleep(5)
            continue

        try:
            if r.status_code in (520, 521, 522, 523, 429) and attempt < retries:
                logger.info("libgen %s -> HTTP %s (transient); retrying",
                            get_url, r.status_code)
                r.close()
                time.sleep(5)
                continue
            if r.status_code >= 400:
                logger.warning("libgen %s -> HTTP %s", get_url, r.status_code)
                return None

            ctype = r.headers.get("Content-Type") or ""
            disp_name = _filename_from(r.headers)

            # stream to a temp file, sniffing as we go
            head = b""
            tmp = dest_dir / ((disp_name or "download.bin") + ".part")
            written = 0
            reject = False
            try:
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        if len(head) < 4096:
                            head += chunk[: 4096 - len(head)]
                        if looks_like_html(head) and written == 0:
                            logger.info("libgen returned HTML for %s; not a file",
                                        get_url)
                            reject = True
                            break
                        fh.write(chunk)
                        written += len(chunk)
            finally:
                r.close()

            if reject:
                tmp.unlink(missing_ok=True)
                return None
            break
        except Exception as exc:
            logger.warning("libgen download error (try %d/%d): %s",
                           attempt, retries, exc)
            if attempt < retries:
                time.sleep(5)
                continue
            return None

    if written == 0:
        tmp.unlink(missing_ok=True)
        return None

    fmt = guess_format(disp_name, ctype, head)
    base = _safe_name(Path(disp_name).stem if disp_name else (title or "book"))
    final = dest_dir / f"{base}.{fmt}"
    n = 1
    while final.exists():
        final = dest_dir / f"{base} ({n}).{fmt}"
        n += 1

    tmp.rename(final)
    logger.info("libgen download -> %s (%d bytes, fmt=%s)", final, written, fmt)
    return final

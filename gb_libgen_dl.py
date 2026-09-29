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


def pick_download_urls(book, root: str = "https://libgen.li") -> list:
    """Return candidate download URLs for a libgen Book, best first.

    Book.mirrors is not reliable as a single value: for many rows it
    contains only an `ads.php` advertising page, which returns a 200 HTML
    document. But the list typically has several entries (ads.php, other
    libgen hosts, an AA link), and a real file may be served by one of them.

    Returns a de-duplicated ordered list so the caller can try each.
    """
    urls = []
    try:
        urls = [u for u in (getattr(book, "mirrors", None) or []) if u]
    except Exception:
        urls = []

    ordered = []
    for u in urls:
        if "get.php" in u:
            ordered.append(u)
    for u in urls:
        if "get.php" not in u and "/book/" in u:
            ordered.append(u)
    for u in urls:
        # ads.php is a known advertising page that always answers 200 HTML, so
        # it is never worth a request: leave it out entirely.
        if "ads.php" in u:
            continue
        if "get.php" not in u and "/book/" not in u:
            ordered.append(u)

    md5 = getattr(book, "md5", None)
    if md5:
        for host in ("libgen.li", "libgen.pw", "libgen.la", "libgen.gl",
                     "libgen.bz", "libgen.vg"):
            ordered.append(f"https://{host}/get.php?md5={md5}")

    seen, out = set(), []
    for u in ordered:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def pick_download_url(book, root: str = "https://libgen.li") -> Optional[str]:
    """Return the single best download URL (first from pick_download_urls)."""
    urls = pick_download_urls(book, root=root)
    return urls[0] if urls else None


def _safe_name(name: str) -> str:
    name = re.sub(r"[/\\:*?\"<>|]+", "_", (name or "").strip())
    return re.sub(r"\s+", " ", name)[:180] or "book"


def download_from_libgen(get_url, dest_dir: Path, title: str = "",
                         timeout: int = 120, retries: int = 3) -> Optional[Path]:
    """Fetch a real file from a libgen URL and save it.

    get_url may be a single URL or a LIST of candidate URLs -- a given title
    is often hosted on only one mirror, so every candidate is tried in turn
    before giving up.

    Returns the written path, or None if no source yielded real file bytes
    (an HTML landing page, an error page, or a hard failure). Never writes
    HTML to disk as if it were a book.

    libgen sits behind Cloudflare and returns intermittent 522s (origin
    timeout) even though the same URL succeeds seconds later, so a small
    retry is warranted here too.
    """
    import time

    import requests

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    candidates = list(get_url) if isinstance(get_url, (list, tuple)) else [get_url]
    candidates = [u for u in candidates if u]

    for url in candidates:
        for attempt in range(1, retries + 1):
            try:
                r = requests.get(url, headers={"User-Agent": BROWSER_UA},
                                 timeout=timeout, stream=True,
                                 allow_redirects=True)
            except Exception as exc:
                logger.warning("libgen fetch failed (try %d/%d) %s: %s",
                               attempt, retries, url, exc)
                if attempt < retries:
                    time.sleep(5)
                    continue
                break

            try:
                if r.status_code in (520, 521, 522, 523, 429) and attempt < retries:
                    logger.info("libgen %s -> HTTP %s (transient); retrying",
                                url, r.status_code)
                    r.close()
                    time.sleep(5)
                    continue
                if r.status_code >= 400:
                    logger.info("libgen %s -> HTTP %s", url, r.status_code)
                    r.close()
                    break

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
                                logger.info(
                                    "libgen returned HTML for %s; not a file", url)
                                reject = True
                                break
                            fh.write(chunk)
                            written += len(chunk)
                finally:
                    r.close()

                if reject:
                    tmp.unlink(missing_ok=True)
                    break          # this mirror has nothing; try the next URL

                fmt = guess_format(disp_name, ctype, head)
                base = _safe_name(Path(disp_name).stem if disp_name
                                  else (title or "book"))
                final = dest_dir / f"{base}.{fmt}"
                n = 1
                while final.exists():
                    final = dest_dir / f"{base} ({n}).{fmt}"
                    n += 1
                tmp.rename(final)
                logger.info("libgen download -> %s (%d bytes, fmt=%s)",
                            final, written, fmt)
                return final
            except Exception as exc:
                logger.warning("libgen download error on %s (try %d/%d): %s",
                               url, attempt, retries, exc)
                if attempt < retries:
                    time.sleep(5)
                    continue
                break

    logger.info("no libgen mirror served a real file for %r", title)
    return None

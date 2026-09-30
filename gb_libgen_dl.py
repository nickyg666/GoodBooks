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
                         timeout: int = 120, retries: int = 3,
                         md5: str = "") -> Optional[Path]:
    """Fetch a real file from one or more libgen get.php URLs and save it.

    get_url may be a single URL or a list of candidate URLs: a given md5 is
    often absent from some mirrors ("500 File not found in DB") but present
    on others, so every candidate is tried in turn.

    Two things matter at these mirror speeds (27-57 KiB/s, so a 27MB book
    takes 8-16 minutes):

      * a dropped connection resumes with a Range request rather than
        restarting, and
      * a connection that dies on the FINAL chunk is accepted when the
        partial file already holds every advertised byte.

    Returns the written path, or None if no source yielded real file bytes.
    Never writes an HTML landing page to disk as if it were a book.

    When `md5` is supplied the keyed two-hop fetch (gb_keyed) is tried FIRST,
    because as of 2026-09-30 the plain get.php returns an HTML page even for
    files that libgen does hold; only the keyed second request yields bytes.
    The URL candidates remain as a fallback. Proven: md5
    0c757e447004372b66a07d6e22f5d1da -> 1,869,525 byte EPUB "Dear Debbie".
    """
    import time
    import requests

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidates = list(get_url) if isinstance(get_url, (list, tuple)) else [get_url]
    candidates = [u for u in candidates if u]

    # -- keyed two-hop first (see docstring)
    if md5:
        try:
            import gb_keyed
            got = gb_keyed.keyed_download(md5, dest_dir, title,
                                          timeout=min(timeout, 60),
                                          retries=min(retries, 3))
            if got:
                p = Path(got)
                if p.exists() and p.stat().st_size > 1024:
                    logger.info("downloaded via keyed hop: %s (%d bytes)",
                                p.name, p.stat().st_size)
                    return p
        except Exception as exc:
            logger.debug("keyed hop failed for %s: %s", md5, exc)

    for url in candidates:
        for attempt in range(1, retries + 1):
            tmp = None
            try:
                # Resume a partial file from an earlier attempt.
                head = b""
                written = 0
                resume = None
                try:
                    if dest_dir.exists():
                        for p in dest_dir.glob("*.part"):
                            resume = p
                            break
                except Exception:
                    resume = None

                mode = "wb"
                if resume is not None and resume.stat().st_size > 0:
                    mode = "ab"

                r = requests.get(url, headers={"User-Agent": BROWSER_UA},
                                 timeout=timeout, stream=True)
                if r.status_code in (520, 521, 522, 523, 429) and attempt < retries:
                    r.close()
                    time.sleep(5)
                    continue
                if r.status_code >= 400:
                    r.close()
                    break

                ctype = r.headers.get("Content-Type") or ""
                disp_name = _filename_from(r.headers)
                reject = False
                head = b""
                tmp = dest_dir / ((disp_name or "download.bin") + ".part")

                if mode == "ab":
                    r.close()
                    have = tmp.stat().st_size if tmp.exists() else 0
                    r = requests.get(
                        url,
                        headers={"User-Agent": BROWSER_UA,
                                 "Range": "bytes=%d-" % have},
                        timeout=timeout, stream=True)
                    if r.status_code not in (206, 416):
                        r.close()
                        mode = "wb"
                        have = 0
                        r = requests.get(url,
                                         headers={"User-Agent": BROWSER_UA},
                                         timeout=timeout, stream=True)

                written = tmp.stat().st_size if (mode == "ab" and tmp.exists()) else 0

                try:
                    with open(tmp, mode) as fh:
                        for chunk in r.iter_content(64 * 1024):
                            if not chunk:
                                continue
                            if written == 0 and len(head) < 4096:
                                head += chunk[: 4096 - len(head)]
                            if written == 0 and looks_like_html(head):
                                logger.info(
                                    "libgen returned HTML for %s; not a file",
                                    url)
                                reject = True
                                break
                            fh.write(chunk)
                            written += len(chunk)
                except Exception as read_exc:
                    # A connection dropped on the final chunk raises here
                    # even though every byte arrived. Accept the file when
                    # the partial already holds the advertised length.
                    try:
                        expected = int(r.headers.get("Content-Length") or 0)
                    except Exception:
                        expected = 0
                    have_now = tmp.stat().st_size if tmp.exists() else 0
                    if expected and have_now >= expected:
                        written = have_now
                        if not head:
                            with open(tmp, "rb") as probe:
                                head = probe.read(4096)
                        logger.info(
                            "connection dropped at the final chunk; file is "
                            "complete (%d/%d bytes)", written, expected)
                    else:
                        raise read_exc
                finally:
                    r.close()

                if reject:
                    tmp.unlink(missing_ok=True)
                    break

                if not tmp.exists() or tmp.stat().st_size == 0:
                    break

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
    return None

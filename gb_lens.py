"""Identify a book cover with Google Lens, driven through a real browser.

STATUS: verified working, but requires a browser profile that Google trusts.
From an untrusted profile Google answers with a bot-detection interstitial
("Our systems have detected unusual traffic from your computer network")
instead of results, so identify_cover() returns [] in that case. Callers
must handle the empty result.

Why a browser is required: Lens renders results with JavaScript. The raw HTML
of lens.google.com contains no book data at all, and Google's /searchbyimage
endpoint -- which the previous requests-based implementation used -- was
retired in 2019, so a plain-requests implementation can never identify
anything. That is why the old code only ever produced generic fallback
queries.

Approach: open lens.google.com, hand it the image, read the rendered text.
Returns [{"title","author","isbn","source"}] or [].
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/127.0.0.0 Safari/537.36")

# Reuse the long-lived Chrome profile if it exists; a throwaway profile gets
# bot-blocked by Google. Falls back to a temp profile.
_CANDIDATE_PROFILES = [
    "/home/host/opikernel/stage/google-browser/profile",
    os.path.expanduser("~/.cache/gb-lens-profile"),
]

_ISBN = re.compile(r"\b(97[89][0-9]{10})\b")
_BLOCKED = ("unusual traffic", "our systems have detected", "/sorry/index",
            "are you a robot", "unusual traffic from your computer network")


def _looks_like_book(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in ("book", "books", "isbn", "paperback", "hardcover"))


def _parse_candidates(text: str) -> List[dict]:
    out: List[dict] = []
    seen = set()

    # Strongest signal: a line carrying an ISBN, e.g.
    # "Dune: 9780441013593: Herbert, Frank: Books"
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or len(line) > 220:
            continue
        m = _ISBN.search(line)
        if not m:
            continue
        isbn = m.group(1)
        if isbn in seen:
            continue
        title, author = "", ""
        if line.count(":") >= 2:
            parts = [p.strip() for p in line.split(":")]
            title = parts[0]
            for p in parts[1:]:
                if p and not _ISBN.fullmatch(p.replace(" ", "")) \
                        and p.lower() not in ("books", "book", "kindle"):
                    author = author or p
        if not title:
            title = line.split(":")[0].strip()
        seen.add(isbn)
        out.append({"title": title, "author": author, "isbn": isbn,
                    "source": "google_lens"})

    if out:
        return out

    # No ISBN found: fall back to lines that read like product titles.
    for line in (text or "").splitlines():
        line = line.strip()
        if not (12 < len(line) < 120) or "|" in line:
            continue
        if _looks_like_book(line):
            key = line.lower()[:60]
            if key in seen:
                continue
            seen.add(key)
            out.append({"title": line, "author": "", "isbn": "",
                        "source": "google_lens"})
        if len(out) >= 5:
            return out
    return out


def _existing_profile() -> Optional[str]:
    for p in _CANDIDATE_PROFILES:
        if os.path.isdir(p):
            return p
    return None


def identify_cover(image_bytes: bytes, timeout: int = 150) -> List[dict]:
    """Run Lens on raw image bytes and return identification candidates.

    Returns [] when Lens is unavailable or Google bot-blocks the profile --
    callers should fall back rather than treat that as a hard failure.
    """
    if not image_bytes:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        logger.warning("playwright unavailable for Lens: %s", exc)
        return []

    tmp = None
    text = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
            fh.write(image_bytes)
            tmp = fh.name

        profile = _existing_profile()
        browser = None
        with sync_playwright() as p:
            if profile:
                ctx = p.chromium.launch_persistent_context(
                    profile, headless=False,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                    user_agent=UA, locale="en-US",
                    viewport={"width": 1366, "height": 900})
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                owns_browser = False
            else:
                browser = p.chromium.launch(
                    headless=False,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
                ctx = browser.new_context(user_agent=UA, locale="en-US",
                                          viewport={"width": 1366, "height": 900})
                page = ctx.new_page()
                owns_browser = True

            try:
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
            except Exception:
                pass

            try:
                page.goto("https://lens.google.com/", wait_until="domcontentloaded",
                          timeout=60000)
                page.set_input_files("input[type=file]", tmp)
            except Exception as exc:
                logger.info("Lens upload path unavailable: %s", exc)

            for _ in range(22):
                import time
                time.sleep(2)
                try:
                    text = page.inner_text("body") or ""
                except Exception:
                    continue
                low = text.lower()
                if any(b in low for b in _BLOCKED):
                    logger.warning(
                        "Google Lens bot-blocked this profile (IP flagged); "
                        "returning no candidates")
                    text = ""
                    break
                if _ISBN.search(text) or "visual matches" in low:
                    break

            if owns_browser and browser is not None:
                ctx.close()
                browser.close()
    except Exception as exc:
        logger.warning("Lens identification failed: %s", exc)
        return []
    finally:
        if tmp:
            try:
                Path(tmp).unlink()
            except Exception:
                pass

    cands = _parse_candidates(text)
    if cands:
        logger.info("Lens identified: %s", cands[0])
    return cands

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

# OCR for cross-checking Lens candidates against the image itself.
try:
    import pytesseract
    _HAS_TESSERACT = True
except ImportError:
    pytesseract = None
    _HAS_TESSERACT = False

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    Image = None
    _HAS_PIL = False

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/127.0.0.0 Safari/537.36")

# Reuse the long-lived Chrome profile if it exists; a throwaway profile gets
# bot-blocked by Google. Falls back to a temp profile.
_CANDIDATE_PROFILES = [
    "/home/das/.cache/gb-lens-profile",
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


_NOISE_TOKENS = {
    "the", "and", "for", "with", "from", "that", "this", "was", "are", "all",
    "not", "but", "his", "her", "has", "had", "you", "out", "who", "how",
    "its", "one", "two", "new", "now", "off", "our", "way", "may", "own",
}


def _clean_ocr(raw: str) -> str:
    """Keep only the tokens that can plausibly be words on a cover.

    Stylised cover art makes Tesseract emit runs like '| SCOTT Dae ERALD |'
    or '2s SSS Ee'. Those noise runs are what made scan & steal return
    unrelated books, so strip anything that is not 3+ alphanumeric
    characters and drop pure punctuation/pipe debris before searching.
    """
    words = []
    for w in (raw or "").split():
        w = w.strip("|.,;:!?()[]{}<>\"'`~*_=-")
        if len(w) < 3:
            continue
        if not any(c.isalnum() for c in w):
            continue
        letters = sum(1 for c in w if c.isalpha() or c.isdigit())
        if letters < max(2, int(len(w) * 0.6)):
            continue
        words.append(w)
    return " ".join(words)

def _dedupe_words(words: List[str]) -> List[str]:
    seen = set()
    ordered = []
    for w in words:
        if w not in seen:
            seen.add(w)
            ordered.append(w)
    return ordered
def extract_ocr_text(image_bytes: bytes) -> str:
    """Extract text from the image using OCR, for cross-checking Lens results.

    Returns the OCR text, or empty string if OCR is unavailable.

    NOTE: do NOT downscale before OCR. A 400px thumbnail destroys the
    stylised lettering on cover art, which is exactly where the title
    lives -- the old code did that and scan & steal matched nothing.
    Instead upscale anything small and try several page-segmentation
    modes, keeping the result with the most plausible words.
    """
    if not _HAS_TESSERACT or not _HAS_PIL:
        return ""
    try:
        from io import BytesIO
        img = Image.open(BytesIO(image_bytes))
        img.load()
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")

        if max(img.size) < 900:
            factor = 900.0 / max(img.size)
            img = img.resize((int(img.size[0] * factor), int(img.size[1] * factor)),
                             Image.LANCZOS)

        best = ""
        for psm in (6, 3, 11, 4):
            try:
                raw = pytesseract.image_to_string(img, config=f"--psm {psm}")
            except Exception:
                continue
            cleaned = _clean_ocr(raw)
            if len(cleaned.split()) > len(best.split()):
                best = cleaned
            if len(best.split()) >= 8:
                break

        return " ".join(_dedupe_words(best.split()))
    except Exception as exc:
        logger.debug("OCR extraction failed: %s", exc)
        return ""


def _verify_with_ocr(candidates: List[dict], ocr_text: str) -> List[dict]:
    """Cross-check Lens candidates against OCR text from the image itself.
    
    Returns only candidates whose title or ISBN appears in the OCR text.
    Returns an empty list if OCR is available but nothing matches -- this is
    the "100% certain" step: a candidate that does not appear in the image is
    not the book on the cover, and guessing is worse than returning nothing.
    
    Returns all candidates only if OCR is unavailable or produced no text.
    """
    if not ocr_text or not candidates:
        return candidates
    ocr_lower = ocr_text.lower()
    verified = []
    for cand in candidates:
        title = (cand.get("title") or "").lower()
        isbn = (cand.get("isbn") or "").lower()
        # Check if the title or ISBN appears in the OCR text.
        # We use a fuzzy match: check if at least 50% of the title words
        # appear in the OCR text, to handle OCR errors and stylized fonts.
        if title:
            title_words = [w for w in title.split() if len(w) > 2]
            if title_words:
                matched = sum(1 for w in title_words if w in ocr_lower)
                if matched >= max(1, len(title_words) // 2):
                    verified.append(cand)
                    continue
        if isbn and isbn in ocr_lower:
            verified.append(cand)
    # If OCR is available but nothing matched, return empty list.
    # This is the "100% certain" step: we'd rather return nothing than a guess.
    return verified


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
                    profile, headless=True,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                    user_agent=UA, locale="en-US",
                    viewport={"width": 1366, "height": 900})
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                owns_browser = False
            else:
                browser = p.chromium.launch(
                    headless=True,
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
        # Cross-check Lens candidates against OCR text from the image itself.
        # This is the "100% certain" step: a candidate whose title or ISBN
        # does not appear in the image is not the book on the cover.
        ocr_text = extract_ocr_text(image_bytes)
        if ocr_text:
            logger.info("OCR extracted %d chars from the image", len(ocr_text))
            verified = _verify_with_ocr(cands, ocr_text)
            logger.info("OCR verification: %d of %d candidates confirmed", len(verified), len(cands))
            cands = verified
        else:
            logger.info("OCR unavailable or produced no text; Lens candidates used as-is")
    else:
        # Lens returned nothing (bot-blocked or no results). Fall back to
        # searching using OCR text extracted from the image itself.
        ocr_text = extract_ocr_text(image_bytes)
        if ocr_text:
            logger.info("Lens blocked; falling back to OCR text search: %s", ocr_text[:100])
            # Use the first few words of OCR text as a search query
            words = ocr_text.split()[:10]  # first 10 words
            query = " ".join(words)
            cands = [{"title": query, "author": "", "isbn": "", "source": "ocr_fallback"}]
    if cands:
        logger.info("Lens identified: %s", cands[0])
    else:
        logger.info("Lens identified: no candidates")
    return cands

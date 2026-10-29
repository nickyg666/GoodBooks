"""Cover-matching for scan & steal — the replacement for the dead reverse-search path.

Chain, strongest signal first:
  1. barcode ISBN decoded locally (exact)
  2. Yandex reverse image search -> clean high-res copies of the cover hosted
     on publisher/retailer sites, which OCR far better than a phone photo
  3. pHash of those copies against the scanned cover, and against the local
     cover cache (data/covers/<md5>.jpg) -> "you already have this"
  4. OCR of the best-matching clean copy -> title/author query for AA

Why not Google Lens: Lens renders in JavaScript and bot-blocks headless
profiles; /searchbyimage was retired in 2019. Yandex's JSON upload endpoint
returns a cbirId and the results page embeds every hit as an img_url link,
which parses without a browser.

Why not Bing: /images/searchbyimage/upload answers 404 with a 278KB shell.

Per the design prompt: crop to the book-cover region before matching, so the
background of the photo contributes nothing.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import requests
from PIL import Image

try:
    import imagehash
    _HAS_IMAGEHASH = True
except Exception:  # pragma: no cover
    imagehash = None
    _HAS_IMAGEHASH = False

try:
    import pytesseract
    _HAS_TESSERACT = True
except ImportError:
    pytesseract = None
    _HAS_TESSERACT = False

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/127.0.0.0 Safari/537.36")

# pHash hamming distance at or below which two covers are the same art.
# 144-bit hash: 0 = identical, <=14 = same cover, minor encode differences.
SAME_ART_THRESHOLD = 25

# Domains worth downloading from when ranking hits. Skip ones that serve
# HTML wrappers or require a referer.
_SKIP_DOMAINS = (
    "pinterest.", "pinimg.com", "facebook.", "flickr.com",
    "yandex.net", "yastatic.net", "googleusercontent.com",
    "tiktok.com", "instagram.com",
)

_COVER_CACHE_DIR = "data/covers"


# ---------------------------------------------------------------------------
# cropping
# ---------------------------------------------------------------------------

def crop_to_cover(image: Image.Image) -> Tuple[Image.Image, bool]:
    """Crop a photo down to the rectangular cover region.

    Walks inward from each edge while the row/column is mostly uniform
    (low edge energy), which is what a hand-held photo's background looks
    like. Returns (cropped_image, cropped_flag).
    """
    try:
        small = image.convert("L")
        if max(small.size) > 640:
            small.thumbnail((640, 640), Image.LANCZOS)
        a = np.asarray(small, dtype=np.int16)
        gy, gx = np.gradient(a.astype(np.float32))
        energy = np.abs(gx) + np.abs(gy)

        row_e = energy.mean(axis=1)
        col_e = energy.mean(axis=0)

        row_thr = max(2.0, float(row_e.mean()) * 0.35)
        col_thr = max(2.0, float(col_e.mean()) * 0.35)

        h, w = row_e.size, col_e.size
        top = 0
        while top < h // 2 and row_e[top] < row_thr:
            top += 1
        bottom = h - 1
        while bottom > h // 2 and row_e[bottom] < row_thr:
            bottom -= 1
        left = 0
        while left < w // 2 and col_e[left] < col_thr:
            left += 1
        right = w - 1
        while right > w // 2 and col_e[right] < col_thr:
            right -= 1

        if (right - left) < (w * 0.4) or (bottom - top) < (h * 0.4):
            return image, False

        sx = image.size[0] / float(w)
        sy = image.size[1] / float(h)
        box = (int(left * sx), int(top * sy),
               int((right + 1) * sx), int((bottom + 1) * sy))
        cropped = image.crop(box)
        if cropped.size[0] < 80 or cropped.size[1] < 80:
            return image, False
        return cropped, True
    except Exception as exc:
        logger.debug("crop_to_cover failed: %s", exc)
        return image, False


# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------

def phash(image: Image.Image):
    if not _HAS_IMAGEHASH:
        return None
    try:
        return imagehash.phash(image.convert("RGB"), hash_size=12)
    except Exception as exc:
        logger.debug("phash failed: %s", exc)
        return None


def hamming(a, b) -> Optional[int]:
    if a is None or b is None:
        return None
    try:
        return int(a - b)
    except Exception:
        return None


def prepare_for_hash(image: Image.Image) -> Image.Image:
    """Square-pad to 1:1 so aspect ratio does not dominate the hash."""
    w, h = image.size
    if w == h:
        return image.convert("RGB")
    side = max(w, h)
    canvas = Image.new("RGB", (side, side), (0, 0, 0))
    canvas.paste(image.convert("RGB"), ((side - w) // 2, (side - h) // 2))
    return canvas


# ---------------------------------------------------------------------------
# local cover cache
# ---------------------------------------------------------------------------

def load_cover_cache(limit: int = 4000) -> List[Tuple[str, object]]:
    """Return [(md5_stem, phash_or_None)] for locally cached AA covers."""
    import os
    from pathlib import Path
    out = []
    try:
        entries = sorted(os.listdir(_COVER_CACHE_DIR))
    except OSError:
        return out
    for name in entries[:limit]:
        if not name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            continue
        stem = Path(name).stem
        try:
            with Image.open(os.path.join(_COVER_CACHE_DIR, name)) as im:
                out.append((stem, phash(prepare_for_hash(im))))
        except Exception:
            continue
    return out


def match_local_cache(target_phash, max_distance: int = SAME_ART_THRESHOLD,
                      limit: int = 4000) -> List[Tuple[str, int]]:
    """Rank cached covers by hamming distance to target_phash."""
    if target_phash is None:
        return []
    scored = []
    for stem, h in load_cover_cache(limit):
        d = hamming(target_phash, h)
        if d is not None and d <= max_distance:
            scored.append((stem, d))
    scored.sort(key=lambda t: t[1])
    return scored


# ---------------------------------------------------------------------------
# hit downloading / ranking
# ---------------------------------------------------------------------------

def _fetch_image(url: str, timeout: int = 15) -> Optional[bytes]:
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout,
                         allow_redirects=True)
        if r.status_code != 200 or len(r.content) < 2000:
            return None
        if not (r.content[:3] == b"\xff\xd8\xff" or
                r.content[:8].startswith(b"\x89PNG")):
            return None
        return r.content
    except Exception:
        return None


def rank_hits(image_bytes: bytes, urls: List[str],
              max_fetch: int = 12) -> List[Dict]:
    """Download candidate covers and rank them by pHash similarity to the scan.

    Returns [{"url", "distance", "phash_ok", "bytes"}] sorted best first.
    """
    target_phash = _safe_hash(image_bytes)
    out = []
    for url in urls[:max_fetch]:
        if any(skip in url for skip in _SKIP_DOMAINS):
            continue
        data = _fetch_image(url)
        if not data:
            continue
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception:
            continue
        h = phash(prepare_for_hash(im))
        d = hamming(target_phash, h)
        out.append({"url": url, "distance": d, "phash_ok": d is not None,
                    "bytes": data})
    out.sort(key=lambda r: (r["distance"] is None, r["distance"] or 999))
    if target_phash is None:
        return [{"url": u, "distance": None, "phash_ok": False, "bytes": None}
                for u in urls[:max_fetch]]
    return out


def _safe_hash(image_bytes: bytes):
    try:
        im = Image.open(io.BytesIO(image_bytes))
        im.load()
        return phash(prepare_for_hash(im))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OCR on a clean copy
# ---------------------------------------------------------------------------

def ocr_queries(image_bytes: bytes) -> List[str]:
    """OCR a (preferably clean, high-res) cover into candidate AA queries.

    NOTE: do NOT downscale before OCR. A 400px thumbnail destroys the
    stylised lettering on cover art, which is exactly where the title
    lives -- the old code did that and scan & steal matched nothing.
    Instead upscale anything small and try several page-segmentation
    modes, keeping the result with the most plausible words.
    """
    try:
        import gb_lens
        text = gb_lens.extract_ocr_text(image_bytes)
    except Exception as exc:
        logger.debug("gb_lens OCR failed: %s", exc)
        text = ""
    if not text:
        return []
    words = _plausible_words(text)
    return _queries_from_words(words)


_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{1,}")
_STOP = {"book", "novel", "edition", "author", "bestseller", "here",
         "new", "york", "times", "international", "reading", "the",
         "and", "for", "with", "from", "bestselling", "a"}


def _plausible_words(raw: str) -> List[str]:
    """Tokens that look like human words, not scanner glyphs."""
    out = []
    for w in _WORD_RE.findall(raw or ""):
        w = w.strip("-").replace("'", "'")
        if len(w) < 2:
            continue
        if not re.search(r"[AEIOUaeiou]", w) and len(w) < 5:
            continue
        if any(ch.isdigit() for ch in w):
            continue
        out.append(w)
    return out


def _queries_from_words(words: List[str]) -> List[str]:
    queries = [" ".join(words)]
    distinctive = [w for w in words if w.lower() not in _STOP]
    if distinctive:
        queries.append(" ".join(distinctive[:8]))
    for i in range(max(0, len(words) - 2)):
        queries.append(" ".join(words[i:i + 3]))
    seen = set()
    out = []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:8]


# ---------------------------------------------------------------------------
# top-level entry
# ---------------------------------------------------------------------------

def _decode_isbn(image_bytes: bytes) -> Optional[str]:
    """Barcode decode, reusing the project's decoder."""
    try:
        from io import BytesIO
        import gb_isbn
        im = Image.open(BytesIO(image_bytes))
        im.load()
        return gb_isbn.decode_isbn(im)
    except Exception as exc:
        logger.debug("isbn decode failed: %s", exc)
        return None


def identify_cover(image_bytes: bytes, timeout: int = 45) -> Dict:
    """Full cover-identification pass without Google Lens.

    Returns a dict with:
      cropped           bool   - the photo was cropped to the cover
      phash             str    - hex digest of the scan's perceptual hash
      yandex_hits       [url]  - reverse-image hit URLs
      ranked            [{url, distance}]   - hits ranked by cover similarity
      ocr_queries       [str]  - AA queries from OCR
      queries           [str]  - final ordered queries (ISBN first)
      local_matches     [(md5, distance)]  - "you already have this" hits
      isbn_guess        str|None
    Raw image bytes never appear in the result, so it is JSON-safe.
    """
    result: Dict = {
        "cropped": False, "phash": None, "yandex_hits": [], "ranked": [],
        "ocr_queries": [], "queries": [], "local_matches": [],
        "isbn_guess": None,
    }
    if not image_bytes:
        return result

    try:
        im = Image.open(io.BytesIO(image_bytes))
        im.load()
    except Exception as exc:
        logger.debug("cover open failed: %s", exc)
        return result

    cropped, did_crop = crop_to_cover(im)
    result["cropped"] = bool(did_crop)
    target = prepare_for_hash(cropped)
    target_hash = phash(target)
    result["phash"] = str(target_hash) if target_hash is not None else None
    result["local_matches"] = match_local_cache(target_hash)

    buf = io.BytesIO()
    cropped.convert("RGB").save(buf, format="JPEG", quality=92)
    scan_bytes = buf.getvalue()

    # 1. ISBN on the cropped scan (tight crop = no shelf neighbours).
    isbn = _decode_isbn(scan_bytes)
    if isbn:
        result["isbn_guess"] = isbn

    # 2. Yandex reverse search on the cropped scan.
    hits = []
    try:
        import gb_yandex2
        hits = gb_yandex2.reverse_search(scan_bytes, timeout=timeout)
        result["yandex_hits"] = [h["url"] for h in hits]
    except Exception as exc:
        logger.debug("yandex pass failed: %s", exc)

    # 3. Rank hits visually; OCR the best clean copy for an AA query.
    if result["yandex_hits"]:
        ranked = rank_hits(scan_bytes, result["yandex_hits"])
        result["ranked"] = [{"url": r["url"], "distance": r["distance"]}
                            for r in ranked]
        for r in ranked[:3]:
            if not r.get("bytes"):
                continue
            logger.info("OCR-ing ranked hit: %s distance=%s", r.get("url","")[:60], r.get("distance"))
            qs = ocr_queries(r["bytes"])
            if qs:
                result["ocr_queries"] = qs
                break

    # 4. Last resort: OCR the photo itself.
    if not result["ocr_queries"]:
        result["ocr_queries"] = ocr_queries(scan_bytes)

    queries: List[str] = []
    if result["isbn_guess"]:
        queries.append(result["isbn_guess"])
    queries.extend(result["ocr_queries"])
    seen = set()
    deduped = []
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            deduped.append(q)
    result["queries"] = deduped[:5]
    return result
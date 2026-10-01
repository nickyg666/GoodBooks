"""ISBN / EAN-13 barcode reading for the scan+steal+send path.

No new dependencies: zbar has no apt candidate on this host and OpenCV is
absent, so this decodes EAN-13 straight from the PIL image.

EAN-13 module layout (95 modules):
    0-2    left guard     101
    3-44   6 left digits  7 modules each, L (odd parity) or G (even parity)
    45-49  centre guard   01010
    50-91  6 right digits 7 modules each, always G encoded
    92-94  right guard    101

The L/G choice of the six left digits encodes the first digit; _PARITY maps
that letter pattern to the first digit. A candidate is accepted only when
all three guards, all twelve digit encodings and the ISBN-13 check digit
agree, so false positives are unlikely.

Entry point: decode_isbn(img) -> Optional[str]
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# code -> digit, odd parity (left-hand, "L")
_L = {
    "0001101": "0", "0011001": "1", "0010011": "2", "0111101": "3",
    "0100011": "4", "0110001": "5", "0101111": "6", "0111011": "7",
    "0110111": "8", "0001011": "9",
}
# code -> digit, even parity (right-hand, "G")
_G = {
    "0100111": "0", "0110011": "1", "0011011": "2", "0100001": "3",
    "0011101": "4", "0111001": "5", "0000101": "6", "0010001": "7",
    "0001001": "8", "0010111": "9",
}
# letter pattern of the six left digits -> first digit
_PARITY = {
    "LLLLLL": "0", "LLGLGG": "1", "LLGGLG": "2", "LLGGGL": "3", "LGLLGG": "4",
    "LGGLLG": "5", "LGGGLL": "6", "LGLGLG": "7", "LGLGGL": "8", "LGGLGL": "9",
}

EAN13_MODULES = 95
LEFT_START = 3
CENTRE_START = 45
RIGHT_START = 50
RIGHT_GUARD_START = 92


def _row_bits(img, y: int) -> str:
    w = img.width
    return "".join("1" if img.getpixel((x, y)) < 128 else "0" for x in range(w))


def _isbn13_valid(isbn: str) -> bool:
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    total = sum(int(isbn[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
    return (10 - total % 10) % 10 == int(isbn[12])


def _decode_bits(line: str, start: int, mw: int) -> Optional[str]:
    """Read 95 EAN-13 modules from `line` starting at pixel `start`."""
    if start < 0 or start + EAN13_MODULES * mw > len(line):
        return None

    def bit(i: int) -> str:
        a = start + i * mw
        b = a + mw
        if b > len(line):
            return ""
        seg = line[a:b]
        return "1" if seg.count("1") * 2 >= len(seg) else "0"

    if "".join(bit(i) for i in range(0, 3)) != "101":
        return None
    if "".join(bit(i) for i in range(CENTRE_START, CENTRE_START + 5)) != "01010":
        return None
    if "".join(bit(i) for i in range(RIGHT_GUARD_START, 95)) != "101":
        return None

    # Build the LETTER pattern (L/G), which is what _PARITY is keyed by, and
    # keep the left-hand digit values too -- they are part of the ISBN, not
    # just the parity carrier.
    letters, left_digits, idx = "", "", LEFT_START
    for _ in range(6):
        b = "".join(bit(i) for i in range(idx, idx + 7))
        if b in _L:
            letters += "L"
            left_digits += _L[b]
        elif b in _G:
            letters += "G"
            left_digits += _G[b]
        else:
            return None
        idx += 7

    first = _PARITY.get(letters)
    if first is None:
        return None
    # full 13 digits = first digit + 6 left + 6 right
    digits = first + left_digits

    idx = RIGHT_START
    for _ in range(6):
        b = "".join(bit(i) for i in range(idx, idx + 7))
        if b not in _G:
            return None
        digits += _G[b]
        idx += 7

    return digits


def _try_row(img, y: int) -> Optional[str]:
    line = _row_bits(img, y)
    if len(line) < 60:
        return None

    widths = set()
    run = 1
    for i in range(1, len(line)):
        if line[i] == line[i - 1]:
            run += 1
        else:
            widths.add(run)
            run = 1
    widths.add(run)
    cands = [w for w in sorted(widths) if 1 <= w <= 12]

    for mw in cands:
        for start in range(0, min(len(line), 600)):
            d = _decode_bits(line, start, mw)
            if d and _isbn13_valid(d):
                return d
    return None


def decode_ean13(digits: str) -> Optional[str]:
    if not digits or not digits.isdigit() or len(digits) != 13:
        return None
    if not _isbn13_valid(digits):
        logger.debug("EAN-13 failed ISBN check digit: %s", digits)
        return None
    return digits


def decode_isbn(img) -> Optional[str]:
    """Scan a PIL image for an EAN-13 book barcode (rows, then rotated)."""
    from PIL import Image

    if img is None or not isinstance(img, Image.Image):
        return None
    gray = img.convert("L")

    for f in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        y = int(gray.height * f)
        if y >= gray.height:
            continue
        try:
            got = _try_row(gray, y)
        except Exception:
            got = None
        if got:
            isbn = decode_ean13(got)
            if isbn:
                logger.info("Decoded ISBN from barcode: %s", isbn)
                return isbn

    for angle in (90, 270):
        try:
            rot = gray.rotate(angle, expand=True)
        except Exception:
            continue
        for f in (0.2, 0.35, 0.5, 0.65, 0.8):
            y = int(rot.height * f)
            if y >= rot.height:
                continue
            try:
                got = _try_row(rot, y)
            except Exception:
                got = None
            if got:
                isbn = decode_ean13(got)
                if isbn:
                    logger.info("Decoded ISBN from rotated barcode: %s", isbn)
                    return isbn
    return None

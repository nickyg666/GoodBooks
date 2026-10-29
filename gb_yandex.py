#!/usr/bin/env python3
"""Yandex reverse image search: parseable via HTML escape + balanced-brace walk.

STATUS: probe-verified 2026-10-10 on DAS against a real 1984 cover (311x500).
Chain: POST upfile -> cbirId -> GET cbir_id -> unescape HTML -> balanced-brace
walk to the initialState object -> serpList.items[].snippet.{title,url}.

Caveats:
  * Yandex sometimes returns an interstitial instead of the SERP; treat that
    as an empty result, never an exception.
  * Do not regex for the JSON -- the blob is HTML-escaped and spans 126KB.
    Extracting with a balanced-brace scanner is the only robust parse.
"""

from __future__ import annotations

import html
import json
import logging
from typing import List

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/127.0.0.0 Safari/537.36")

_UPLOAD = "https://yandex.com/images/search"


def _extract_balanced(text: str, start: int) -> str | None:
    """Return the balanced {...} substring beginning at text[start]."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def _parse_serp(page_html: str) -> List[dict]:
    """Pull {title, url, source} hits out of a Yandex results page."""
    upage = html.unescape(page_html)
    idx = upage.find('initialState":')
    if idx == -1:
        return []
    start = upage.find("{", idx)
    blob = _extract_balanced(upage, start)
    if not blob:
        return []
    try:
        st = json.loads(blob)
    except Exception as exc:
        logger.debug("yandex serp JSON parse failed: %s", exc)
        return []
    items = st.get("internalState", {}).get("serpList", {}).get("items", {})
    if isinstance(items, dict):
        items = [v for k, v in sorted(items.items(), key=lambda kv: kv[0])
                 if isinstance(v, dict)]
    hits = []
    for it in items or []:
        sn = it.get("snippet") or {}
        title = (sn.get("title") or "").strip()
        url = (sn.get("url") or "").strip()
        if title or url:
            hits.append({"title": title, "url": url, "source": "yandex"})
    return hits


def reverse_search(image_bytes: bytes, timeout: int = 45) -> List[dict]:
    """Upload bytes, follow to the SERP, return candidate page hits."""
    if not requests or not image_bytes:
        return []
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        r = s.post(
            _UPLOAD,
            params={
                "rpt": "imageview",
                "format": "json",
                "request": json.dumps({"blocks": [
                    {"block": "b-page_type_search-by-image__link"}]}),
            },
            files={"upfile": ("blob", image_bytes, "image/jpeg")},
            timeout=timeout,
        )
        cbir = r.json()["blocks"][0]["params"]["cbirId"]
        r2 = s.get(_UPLOAD, params={"rpt": "imageview", "cbir_id": cbir},
                   timeout=timeout)
        return _parse_serp(r2.text)
    except Exception as exc:
        logger.debug("yandex reverse search failed: %s", exc)
        return []

#!/usr/bin/env python3
"""Yandex serp extraction via the img_url= links embedded in the results page.

The initialState.serpsList.items is empty (client-side rendering), but the
page embeds every "similar image" result as an <a href="/images/search?img_url=...">.
Those URLs ARE the answer: each one points at a page that contains the same
image. We unescape, dedupe, and return them as candidate source URLs.
"""
import html
import json
import logging
import re
import urllib.parse
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
    """Return [{title, url, source}] from a Yandex results page."""
    upage = html.unescape(page_html)
    # The results are embedded as /images/search?img_url=<url> links.
    urls = re.findall(r'href="[^"]*img_url=([^"&]+)', upage)
    hits = []
    seen = set()
    for raw in urls:
        try:
            u = urllib.parse.unquote(raw)
        except Exception:
            continue
        if u in seen:
            continue
        seen.add(u)
        hits.append({"title": "", "url": u, "source": "yandex"})
    return hits


def reverse_search(image_bytes: bytes, timeout: int = 45) -> List[dict]:
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
"""Give libgen_api_enhanced a browser User-Agent.

libgen.li (and its mirrors) answer requests carrying the default
`python-requests/x.y` User-Agent with the stock nginx landing page -- 639
bytes, no results table -- which surfaced as
"No results table found on search page" and an empty BookList. Sending any
non-requests UA returns the real 101-row table.

This installs a Session subclass at import time so every request the library
makes carries a browser UA, without forking the package.
"""
import requests

_REAL = "python-requests"


def _patch() -> bool:
    try:
        import libgen_api_enhanced.search_request as sr
    except Exception:
        return False

    if getattr(sr, "_gb_ua_patched", False):
        return True

    UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

    class _BrowserSession(requests.Session):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.headers.update({
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })

    # The library calls requests.get(...) directly in search_request, so patch
    # the module-level symbol it resolves at call time.
    orig_get = sr.requests.get

    def _get(url, *a, **kw):
        headers = dict(kw.get("headers") or {})
        if _REAL in headers.get("User-Agent", "") or "User-Agent" not in headers:
            headers["User-Agent"] = UA
            headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
            headers.setdefault("Accept-Language", "en-US,en;q=0.9")
            kw["headers"] = headers
        return orig_get(url, *a, **kw)

    class _RequestsShim:
        """Proxy to the real requests module, but with a browser-UA get()."""
        Session = _BrowserSession
        exceptions = requests.exceptions
        Response = requests.Response
        RequestException = requests.RequestException

        def __getattr__(self, name):
            return getattr(requests, name)

        @staticmethod
        def get(url, *a, **kw):
            return _get(url, *a, **kw)

    sr.requests = _RequestsShim()
    sr._gb_ua_patched = True
    return True


_ok = _patch()

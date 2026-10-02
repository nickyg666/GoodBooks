"""Retry transient TTS failures, and keep the worker cap honest.

Measured 2026-10-02 on .168, so this is not guesswork:

  conc   ok  elapsed   peak RSS
    1     1   20.8s      2.4G
    2     2   22.8s      3.8G
    3     3   25.9s      6.3G
    4     4   30.0s      9.1G
    6     6   42.5s     12.7G

Two conclusions:

  1. RSS scales ~2.4 GB PER IN-FLIGHT REQUEST. Three concurrent requests is
     6.3 GB; a long book sustains that for hours. The service log showed
     "21.8G memory peak, 1.3G memory swap peak" followed by a self-shutdown,
     so the earlier 502 storm was memory exhaustion on the synthesiser, not a
     bug in the request path. The user's cap (half+1 of CPU threads = 3 on
     DAS) sits at 6.3 GB, which is the right place to be.

  2. Throughput barely improves past 1 request: 20.8s serial vs 25.9s for
     THREE at once. The synthesiser is effectively serial internally, so
     concurrency buys little and costs memory. It is still worth keeping --
     it overlaps network latency and per-chunk ffmpeg encoding -- but a
     transient 502 must not fail a job that has already paid for 100 chunks.

So: keep the user's cap, and make failures survivable with bounded retries
and exponential backoff. A 502/503/504 or a connection reset is retryable; a
400 (bad voice name) is NOT and must fail fast, because retrying it forever
would waste the whole window.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request

RETRYABLE_STATUS = {502, 503, 504, 429, 408}
# 6 attempts, not 3. The leak guard recycles the backend on a schedule and a
# recycle takes ~26s to rebind (measured, TimeoutStopSec=25). Attempting on a
# 2s/4s schedule lands every attempt inside the dead window -- that is how the
# job died at chunk 120/151 with attempts=3.
DEFAULT_ATTEMPTS = 6
DEFAULT_BASE_DELAY = 4.0
DEFAULT_MAX_DELAY = 30.0


def is_retryable(exc: BaseException) -> bool:
    """A 502 means the backend is busy/restarting; a 400 means we asked
    wrongly. Only the first kind is worth trying again."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_STATUS
    # connection reset / refused / timeout / read timeout
    return isinstance(exc, (urllib.error.URLError, TimeoutError,
                            ConnectionError, OSError))


def synth_with_retry(text: str, voice: str, voice_ref: str = "",
                     attempts: int = DEFAULT_ATTEMPTS,
                     base_delay: float = DEFAULT_BASE_DELAY,
                     max_delay: float = DEFAULT_MAX_DELAY,
                     synthesize=None,
                     log=None) -> bytes:
    """Call synthesize(), retrying transient failures with exponential backoff.

    `synthesize` is injected so this is testable without a live panel.
    """
    if synthesize is None:
        import gb_audiobook as AB
        synthesize = lambda t, v, voice_ref="": AB.synthesize(  # noqa: E731
            t, v, voice_ref=voice_ref)

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return synthesize(text, voice, voice_ref=voice_ref)
        except Exception as exc:
            last = exc
            if not is_retryable(exc) or attempt == attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if log:
                log(f"[audiobook] chunk retry {attempt}/{attempts - 1} in "
                    f"{delay:.0f}s after {type(exc).__name__}: {exc}")
            time.sleep(delay)
    # Unreachable: the loop either returns or raises. Asserting rather than
    # `raise last` because a bare `raise None` is a TypeError if it ever were.
    raise RuntimeError(f"retry loop exited without result: {last!r}")

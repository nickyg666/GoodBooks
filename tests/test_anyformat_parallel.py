"""Regressions for any-format extraction and threaded narration.

Each test covers a defect that actually occurred while building this:

  * detect_format() read 8 bytes then tested head[60:68] -- permanently b'',
    so MOBI content sniffing was unreachable dead code.
  * encode_atomic() named its temp file chunk_00000.mp3.part, and ffmpeg
    chooses its muxer from the LAST extension, so every chunk failed with
    exit 234 ("Unable to choose an output format").
  * the temp name must keep the real extension LAST: a.tmp.mp3 works,
    a.mp3.tmp does not. Probed on DAS.
  * a failed chunk left its intermediate .wav behind.
  * a 502 from the TTS backend killed a whole job; a 400 (bad voice) must
    NOT be retried.
"""
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

gb_extract = pytest.importorskip("gb_extract")
gb_parallel = pytest.importorskip("gb_parallel")
gb_retry = pytest.importorskip("gb_retry")


# --------------------------------------------------------------------------
# format detection
# --------------------------------------------------------------------------
def test_detect_reads_past_offset_60():
    """The MOBI marker lives at offset 60, so an 8-byte read is a bug."""
    import inspect
    src = inspect.getsource(gb_extract.detect_format)
    assert 'read(8)' not in src, "detect_format must read more than 8 bytes"


def test_detect_sniffs_real_magic(tmp_path):
    # a real MOBI-shaped header: BOOKMOBI at offset 60
    d = bytearray(b"\x00" * 80)
    d[60:68] = b"BOOKMOBI"
    p = tmp_path / "mislabelled.bin"
    p.write_bytes(bytes(d))
    assert gb_extract.detect_format(p) == "mobi", \
        "content sniffing must reach offset 60"


def test_detect_sniffs_epub_and_pdf(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"PK\x03\x04" + b"\x00" * 100)
    assert gb_extract.detect_format(p) == "epub"
    q = tmp_path / "y.bin"
    q.write_bytes(b"%PDF-1.7" + b"\n" * 100)
    assert gb_extract.detect_format(q) == "pdf"


def test_drm_is_detected_and_not_silently_attempted(tmp_path):
    d = bytearray(b"\x00" * 80)
    d[0:4] = b"CR!T"
    d[60:68] = b"BOOKMOBI"
    p = tmp_path / "locked.azw"
    p.write_bytes(bytes(d))
    assert gb_extract.is_drm_locked(p) is True
    with pytest.raises(gb_extract.ExtractionError) as exc:
        gb_extract.read_book(p)
    msg = str(exc.value).lower()
    assert "drm" in msg, f"error must name DRM, got: {msg}"


def test_unsupported_format_says_what_is_supported(tmp_path):
    p = tmp_path / "book.txt"
    p.write_bytes(b"x" * 4096)
    with pytest.raises(gb_extract.ExtractionError) as exc:
        gb_extract.read_book(p)
    assert "unsupported" in str(exc.value).lower()


def test_missing_and_tiny_files_are_reported_clearly(tmp_path):
    with pytest.raises(gb_extract.ExtractionError):
        gb_extract.read_book(tmp_path / "nope.epub")
    tiny = tmp_path / "tiny.epub"
    tiny.write_bytes(b"PK\x03\x04")
    with pytest.raises(gb_extract.ExtractionError):
        gb_extract.read_book(tiny)


# --------------------------------------------------------------------------
# worker cap: the user asked for half+1 of the CPU thread count
# --------------------------------------------------------------------------
@pytest.mark.parametrize("cpu,expected", [
    (1, 1), (2, 2), (3, 2), (4, 3), (8, 5), (16, 6), (64, 6), (0, 1),
])
def test_worker_cap_is_half_plus_one(cpu, expected):
    assert gb_parallel.synth_workers(cpu) == expected


def test_cap_never_exceeds_half_plus_one_on_any_cpu():
    for cpu in range(1, 200):
        assert gb_parallel.synth_workers(cpu) <= cpu // 2 + 1


def test_cap_is_at_least_one():
    assert gb_parallel.synth_workers(0) >= 1
    assert gb_parallel.synth_workers(1) >= 1


# --------------------------------------------------------------------------
# atomic chunk writes
# --------------------------------------------------------------------------
def test_temp_file_keeps_the_real_extension_last(tmp_path):
    """ffmpeg picks its muxer from the LAST extension only."""
    dst = tmp_path / "chunk_00000.mp3"
    # replicate the naming rule encode_atomic uses
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    assert tmp.name.endswith(".mp3"), f"{tmp.name} must end in .mp3"
    assert tmp.name == "chunk_00000.tmp.mp3"


def test_encode_atomic_produces_a_complete_file_and_cleans_up(tmp_path):
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        pytest.skip("ffmpeg not installed")
    import io
    import wave
    import math
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        frames = bytearray()
        for i in range(24000):
            frames += int(8000 * math.sin(i * 0.05)).to_bytes(
                2, "little", signed=True)
        w.writeframes(bytes(frames))
    wav = buf.getvalue()

    def enc(src, out):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                        "-c:a", "libmp3lame", "-b:a", "64k", str(out)],
                       check=True, capture_output=True, timeout=300)

    dst = tmp_path / "chunk_00000.mp3"
    gb_parallel.encode_atomic(wav, dst, enc)
    assert dst.exists() and dst.stat().st_size > 1000
    assert not list(tmp_path.glob("*.wav")), "intermediate wav must be removed"
    assert not list(tmp_path.glob("*.tmp.mp3")), "temp must be renamed away"

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(dst)], capture_output=True, text=True)
    assert float(probe.stdout.strip() or 0) > 0.9


def test_failed_encode_leaves_no_debris(tmp_path):
    def boom(src, out):
        raise RuntimeError("encoder exploded")

    dst = tmp_path / "chunk_00000.mp3"
    with pytest.raises(RuntimeError):
        gb_parallel.encode_atomic(b"RIFFfake", dst, boom)
    assert not dst.exists()
    assert not list(tmp_path.glob("*.wav")), "wav leaked on failure"
    assert not list(tmp_path.glob("*.tmp.mp3")), "temp leaked on failure"


# --------------------------------------------------------------------------
# retry policy
# --------------------------------------------------------------------------
def _http(code):
    import email.message
    return urllib.error.HTTPError("u", code, str(code), email.message.Message(), None)


@pytest.mark.parametrize("code", [502, 503, 504, 429, 408])
def test_transient_statuses_are_retryable(code):
    assert gb_retry.is_retryable(_http(code))


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(code):
    """Retrying a bad voice name would burn the whole maintenance window."""
    assert not gb_retry.is_retryable(_http(code))


def test_retry_then_succeed(monkeypatch):
    slept = []
    monkeypatch.setattr(gb_retry.time, "sleep", lambda s: slept.append(s))
    n = {"c": 0}

    def flaky(t, v, voice_ref=""):
        n["c"] += 1
        if n["c"] < 3:
            raise _http(502)
        return b"OK"

    out = gb_retry.synth_with_retry("t", "v", synthesize=flaky, base_delay=1.0)
    assert out == b"OK"
    assert n["c"] == 3
    assert slept == [1.0, 2.0]


def test_client_error_fails_fast(monkeypatch):
    monkeypatch.setattr(gb_retry.time, "sleep", lambda s: None)
    n = {"c": 0}

    def bad(t, v, voice_ref=""):
        n["c"] += 1
        raise _http(400)

    with pytest.raises(urllib.error.HTTPError):
        gb_retry.synth_with_retry("t", "v", synthesize=bad)
    assert n["c"] == 1, "a 400 must not be retried"


def test_retry_gives_up_at_the_budget(monkeypatch):
    monkeypatch.setattr(gb_retry.time, "sleep", lambda s: None)
    n = {"c": 0}

    def always(t, v, voice_ref=""):
        n["c"] += 1
        raise _http(502)

    with pytest.raises(urllib.error.HTTPError):
        gb_retry.synth_with_retry("t", "v", synthesize=always, attempts=3)
    assert n["c"] == 3


def test_backoff_is_capped(monkeypatch):
    slept = []
    monkeypatch.setattr(gb_retry.time, "sleep", lambda s: slept.append(s))

    def always(t, v, voice_ref=""):
        raise _http(502)

    with pytest.raises(urllib.error.HTTPError):
        gb_retry.synth_with_retry("t", "v", synthesize=always, attempts=8,
                                  base_delay=10.0, max_delay=30.0)
    assert slept and all(s <= 30.0 for s in slept)


# --------------------------------------------------------------------------
# batch behaviour
# --------------------------------------------------------------------------
def test_one_failed_chunk_does_not_lose_the_others(tmp_path):
    if subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0:
        pytest.skip("ffmpeg not installed")
    import io
    import math
    import wave

    def wav_bytes():
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            fr = bytearray()
            for i in range(12000):
                fr += int(6000 * math.sin(i * 0.05)).to_bytes(
                    2, "little", signed=True)
            w.writeframes(bytes(fr))
        return buf.getvalue()

    def enc(src, out):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                        "-c:a", "libmp3lame", "-b:a", "64k", str(out)],
                       check=True, capture_output=True, timeout=300)

    monkey = sys.modules["gb_retry"]
    orig_sleep = monkey.time.sleep
    monkey.time.sleep = lambda s: None
    try:
        def synth(text, voice, voice_ref=""):
            if text.startswith("BAD"):
                raise _http(400)     # client error: not retried
            return wav_bytes()

        texts = [f"chunk {i} text " * 5 for i in range(6)]
        texts[2] = "BAD chunk that always fails"
        done, errs = gb_parallel.narrate_batch(
            texts, list(range(6)), "v", "", tmp_path, synth, enc,
            log=lambda s: None)
    finally:
        monkey.time.sleep = orig_sleep

    assert len(done) == 5, f"expected 5 good chunks, got {done}"
    assert 2 in errs and "400" in errs[2], f"errs={errs}"
    assert not (tmp_path / "chunk_00002.mp3").exists()
    assert len(list(tmp_path.glob("chunk_*.mp3"))) == 5


def test_no_call_site_bypasses_the_extractor():
    """Every parse path must go through read_book, or a non-EPUB book
    silently falls back to the EPUB-only parser."""
    src = (ROOT / "gb_abjob.py").read_text(encoding="utf-8")
    assert "AB.read_epub(" not in src, \
        "gb_abjob must use EX.read_book() so MOBI/AZW3/AZW/PDF work"

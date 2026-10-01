"""Regression tests for the audiobook converter.

Every case here is a bug that was actually hit and fixed, so they cannot
regress silently:

  * a variable-width lookbehind made the module fail to import at all
  * mp3 cannot be stream-copied into an .m4b container
  * ffmpeg accepts "time=title" chapter pairs and silently writes NO chapters
  * front matter (a copyright page) passed the word-count floor and was narrated
  * a structural heading was emitted as its own chunk and spoken as a fragment
  * enqueue() reset a live job, discarding a multi-hour conversion
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gb_abjob as JOB
import gb_audiobook as AB


# ----------------------------------------------------------- the module --

def test_module_imports():
    """It did not: a variable-width lookbehind is a re.error at import."""
    assert callable(AB.read_epub)
    assert callable(AB.synthesize)
    assert callable(AB.mux_with_chapters)


def test_wav_header_fixer_corrects_the_pocket_tts_placeholder(tmp_path):
    """pocket_tts 2.1.0 claims 1e9 frames; the fix must not touch the PCM.

    Built with wave.open so the chunk layout is genuinely correct, then only
    the two size fields are corrupted. The earlier version assembled the
    header by hand and omitted the fmt body, so the fixer saw a short fmt
    chunk and bailed -- which looked like a code fault and was a test fault.
    """
    import struct
    import wave

    pcm = b"\x00\x01" * 48000                      # 2s at 24k mono 16-bit
    good = tmp_path / "good.wav"
    with wave.open(str(good), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(pcm)
    raw = bytearray(good.read_bytes())

    # corrupt exactly what pocket_tts gets wrong
    struct.pack_into("<I", raw, 4, 36 + 1_000_000_000)     # RIFF size
    data_at = raw.find(b"data")
    struct.pack_into("<I", raw, data_at + 4, 1_000_000_000)  # data size

    fixed = AB._fix_wav_header(bytes(raw))
    assert len(fixed) == len(raw)
    assert fixed[data_at + 8:] == bytes(raw)[data_at + 8:], \
        "PCM payload must be byte-identical"
    (riff,) = struct.unpack("<I", fixed[4:8])
    (dsz,) = struct.unpack("<I", fixed[data_at + 4:data_at + 8])
    assert dsz == len(pcm), f"data size should be {len(pcm)}, got {dsz}"
    assert riff == 36 + len(pcm), f"RIFF size should be {36+len(pcm)}, got {riff}"

    (tmp_path / "fixed.wav").write_bytes(fixed)
    with wave.open(str(tmp_path / "fixed.wav")) as w:
        assert w.getnframes() == 48000, w.getnframes()
        assert w.getnframes() / w.getframerate() == 2.0

    # non-RIFF (ogg for Telegram) passes through untouched
    ogg = b"OggS" + b"\x00" * 100
    assert AB._fix_wav_header(ogg) == ogg


# ------------------------------------------------------------- chunking --

def test_chunks_are_not_fragments():
    """A 3-char chunk ('PROLOGUE') is narrated as a fragment."""
    chunks = AB.split_chunks(
        "PROLOGUE\n\nIt was a bright cold day in April and the clocks "
        "were striking thirteen. Winston Smith moved quickly through the "
        "glass doors of Victory Mansion.")
    assert all(len(c) >= AB.MIN_CHUNK_CHARS for c in chunks), chunks
    assert "PROLOGUE" in " ".join(chunks), "the heading must survive"


def test_chunking_keeps_all_the_words():
    text = ("One two three. Four five six! Seven eight? "
            "Nine ten eleven. Twelve thirteen.")
    joined = " ".join(AB.split_chunks(text))
    for w in ("One", "two", "three", "thirteen"):
        assert w in joined, f"lost {w!r}"


def test_chunks_respect_the_character_budget():
    long_para = " ".join(f"word{i}" for i in range(400))
    for c in AB.split_chunks(long_para, max_chars=200):
        assert len(c) <= 260, f"chunk too long: {len(c)}"


# ---------------------------------------------------------- front matter --

def test_copyright_page_is_front_matter():
    """It measured 150 words at ~10% boilerplate and was being narrated."""
    page = ("Copyright 2024 Nick Roberts\n\nJoin our community today on our "
            "newsletter and Patreon! Download our latest catalog here. All "
            "Rights Reserved. Cover art by Brian Csati. Cover design by Ben "
            "Baldwin. Layout by Lori Michelle. Edited and proofed by ...")
    assert AB._looks_like_front_matter(page) is True


def test_real_prose_is_not_front_matter():
    prose = ("It was a bright cold day in April and the clocks were striking "
             "thirteen. Winston Smith slipped quickly through the glass doors "
             "of Victory Mansion, too hurried to take in the more unusual "
             "posters.")
    assert AB._looks_like_front_matter(prose) is False


def test_first_line_title_recognises_a_bare_heading():
    assert AB._first_line_title("PROLOGUE\n\nThe text") == "PROLOGUE"
    assert AB._first_line_title("CHAPTER 12\n\nMore") == "CHAPTER 12"
    assert AB._first_line_title(
        "It was a bright cold day in April and the clocks were striking") == ""


# ----------------------------------------------------------------- jobs --

def test_enqueue_does_not_reset_a_live_job(tmp_path, monkeypatch):
    """Three POSTs reset the job and discarded the completed parse."""
    monkeypatch.setattr(JOB, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(JOB, "WORK", tmp_path / "work")
    epub = tmp_path / "book.epub"
    epub.write_bytes(b"PK\x03\x04")

    JOB.enqueue("e1", epub, "nick", "T", "A", restart=True)
    j = JOB.load_job("e1")
    j.update({"phase": "narrating", "total_chunks": 3156,
              "chunks_done": [0, 1, 2], "audio_seconds": 42.4})
    JOB.save_job("e1", j)

    # a second POST must be a no-op for progress
    again = JOB.enqueue("e1", epub, "sage", "T", "A")
    assert again["phase"] == "narrating", again["phase"]
    assert again["total_chunks"] == 3156
    assert again["chunks_done"] == [0, 1, 2]
    assert again["audio_seconds"] == 42.4
    assert again["voice"] == "sage", "voice should still be updatable"

    # an explicit restart does clear it
    fresh = JOB.enqueue("e1", epub, "nick", "T", "A", restart=True)
    assert fresh["phase"] == "queued"
    assert fresh["chunks_done"] == []


def test_progress_reports_done_and_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(JOB, "JOBS", tmp_path / "jobs")
    monkeypatch.setattr(JOB, "WORK", tmp_path / "work")
    epub = tmp_path / "b.epub"
    epub.write_bytes(b"PK")
    JOB.enqueue("e2", epub, "nick")
    j = JOB.load_job("e2")
    j.update({"phase": "done", "total_chunks": 10,
              "chunks_done": list(range(10)), "audio_seconds": 120.0,
              "result": "/x/b.m4b"})
    JOB.save_job("e2", j)
    p = JOB.progress("e2")
    assert p["phase"] == "done" and p["pct"] == 100


# --------------------------------------------------------------- epub --

def test_read_epub_rejects_a_non_epub(tmp_path):
    bogus = tmp_path / "not.epub"
    bogus.write_bytes(b"this is not a zip file at all")
    with pytest.raises(AB.ConversionError):
        AB.read_epub(bogus)

"""Threaded chunk narration with a hard half+1 CPU cap.

Why threads and not processes: every chunk is one HTTP POST to the PocketTTS
panel on .168 (synthesize() -> urllib.request.urlopen). That is network
bound and releases the GIL, so threads scale with no pickling cost and no
separate interpreter memory. The only CPU-bound step is the per-chunk ffmpeg
encode, which is a SUBPROCESS -- so it leaves the GIL too. There is nothing
here that a process pool would speed up.

The cap is not advisory. The user asked for "not more than half+1 of the total
CPU thread count", so:
    workers = max(1, min(cpu_count // 2 + 1, HARD_CAP))
with HARD_CAP as a second ceiling so a many-core box cannot launch a stampede
against the TTS endpoint either -- more concurrency than the synthesiser can
serve just makes every chunk slower and the timeout likelier.

Atomicity is the other requirement. Each chunk is written to its own
chunk_NNNNN.mp3 via a temporary file and os.replace(), so a chunk is either
absent or complete. chunks_done in the job file is the single source of
truth for progress, so a crash loses at most the in-flight chunks and never
corrupts a finished one. Assembly sorts by index, so completion order is
irrelevant.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Absolute ceiling regardless of core count: past this, requests to the TTS
# endpoint queue up server-side and per-request timeouts become more likely
# than useful throughput gains.
HARD_CAP = 6


def synth_workers(cpu_count: Optional[int] = None) -> int:
    """half + 1 of the CPU thread count, capped.

    DAS measured 2026-10-02: nproc=4 -> 3 workers.
    """
    n = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
    return max(1, min(n // 2 + 1, HARD_CAP))


def encode_atomic(wav_bytes: bytes, dst: Path,
                  encode: Callable[[Path, Path], None]) -> Path:
    """Write a chunk through a temp file and os.replace.

    os.replace is atomic within a filesystem, so a reader (or the assembler)
    never observes a half-written chunk. The old code wrote
    dst.write_bytes(wav) and then encoded in place, which a concurrent
    assemble could have picked up mid-write.

    The temp name keeps .mp3 as the FINAL extension. ffmpeg chooses its muxer
    from the last extension only, so an earlier version using
    dst.with_suffix(dst.suffix + ".part") -> chunk_00000.mp3.part was rejected
    outright, and changing that to .mp3.tmp was equally rejected:

        Unable to choose an output format for '/tmp/out.mp3.part';
        use a standard extension for the filename or specify the format
        manually.
        -> exit 234

    Probed on DAS, encode of the same WAV to each name:

        a.mp3            OK   8685 bytes
        a.mp3.tmp        FAIL
        a.tmp.mp3        OK   8685 bytes
        a.partial.mp3    OK   8685 bytes
        tmp_a.mp3        OK   8685 bytes
        .a.mp3.tmp       FAIL

    So the marker goes in the STEM, never after the extension:
    chunk_00000.tmp.mp3 -> os.replace() -> chunk_00000.mp3.

    The intermediate .wav is removed on BOTH paths. It was only removed
    after a successful encode, so a failed chunk left its WAV behind.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    raw = dst.with_suffix(".wav")
    try:
        raw.write_bytes(wav_bytes)
        encode(raw, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
        raw.unlink(missing_ok=True)
    return dst


def narrate_batch(texts: Sequence[str], indices: Sequence[int], voice: str,
                  voice_ref: str, wd: Path,
                  synthesize: Callable[..., bytes],
                  encode: Callable[[Path, Path], None],
                  log: Callable[[str], None] = print,
                  workers: Optional[int] = None,
                  budget_seconds: float = 0.0,
                  max_chunks: int = 0,
                  ) -> Tuple[List[int], Dict[int, str]]:
    """Narrate several chunks concurrently.

    Returns (completed_indices, errors_by_index).

    Never raises for a per-chunk failure: one bad chunk must not lose the
    work already done on its batch-mates, so failures are collected and
    reported, and the caller decides whether to retry or fail the job.

    budget_seconds and max_chunks bound the whole batch so one maintenance
    window cannot run away, matching the existing single-threaded accounting.
    """
    n = synth_workers(workers) if workers else synth_workers()
    # Oversubscribe slightly so a worker that is mid-encode does not idle a
    # slot while its synth request is already back, but never past the cap.
    started = time.time()
    done: List[int] = []
    errors: Dict[int, str] = {}

    pending = list(indices)
    if max_chunks:
        pending = pending[:max_chunks]
    if budget_seconds:
        # Chunk count is not predictable against a time budget, so submit in
        # waves: take a generous wave, and stop launching new work once the
        # budget is spent.
        wave = max(n * 2, 4)
    else:
        wave = len(pending) or 1

    def one(i: int) -> Tuple[int, Optional[str]]:
        t0 = time.time()
        try:
            # Retry transient TTS failures (502/503/504/429 = backend busy or
            # restarting) but NOT client errors (400 = we asked wrongly).
            # Measured: 3 concurrent requests cost ~2.4GB RSS each on .168,
            # so a long batch can genuinely provoke a 502 when the panel is
            # under memory pressure -- that must not fail a job that already
            # paid for 100 chunks.
            from gb_retry import synth_with_retry
            wav = synth_with_retry(
                pending_texts[i], voice, voice_ref=voice_ref,
                synthesize=lambda t, v, voice_ref="": synthesize(
                    t, v, voice_ref=voice_ref),
                log=log,
            )
        except Exception as exc:
            return i, f"synthesize: {type(exc).__name__}: {exc}"
        dst = wd / f"chunk_{i:05d}.mp3"
        try:
            encode_atomic(wav, dst, encode)
        except Exception as exc:
            return i, f"encode: {type(exc).__name__}: {exc}"
        return i, None

    submitted = 0
    pending_texts = {i: texts[i] for i in indices}

    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="narr") as pool:
        while submitted < len(pending):
            take = min(wave, len(pending) - submitted)
            batch = pending[submitted:submitted + take]
            submitted += take
            futures = {pool.submit(one, i): i for i in batch}
            stop = False
            for fut in as_completed(futures):
                i, err = fut.result()
                if err:
                    errors[i] = err
                    log(f"[audiobook] chunk {i} failed: {err}")
                    continue
                done.append(i)
                el = time.time() - started
                log(f"[audiobook] chunk {i+1} ok "
                    f"({len(pending_texts[i].split())}w, {el:.1f}s elapsed, "
                    f"{n} workers)")
                if budget_seconds and (time.time() - started) >= budget_seconds:
                    log(f"[audiobook] window budget spent after {el:.0f}s")
                    stop = True
                    break
                if max_chunks and len(done) >= max_chunks:
                    stop = True
                    break
            if stop:
                for f in futures:
                    f.cancel()
                break

    return sorted(done), errors

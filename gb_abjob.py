"""Background audiobook conversion with checkpointing.

Synthesis runs at ~3.3 words/sec (measured), so a 300k-word novel is 4-10
hours. A request cannot wait for that, so this is a job: started once, run by
the maintenance thread, resumable after a crash or a restart.

State lives in data/audiobook_jobs/<entry_id>.json:
  * phase: queued | parsing | narrating | assembling | done | error
  * chapters done, with their audio already on disk
  * a cursor so a restart continues rather than restarting the book

Chunk audio is written to data/audiobook_work/<entry_id>/ and kept until the
final mux, so a failure costs at most one chunk of work.

Deliberately single-book-per-job and sequential: the backend is one GPU-ish
process on another host, so parallel jobs would thrash it.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

import gb_audiobook as AB

DATA = Path("/usr/local/bin/GoodBooks/data")
JOBS = DATA / "audiobook_jobs"
WORK = DATA / "audiobook_work"


def _jobs_dir() -> Path:
    JOBS.mkdir(parents=True, exist_ok=True)
    return JOBS


def job_path(entry_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in entry_id)
    return _jobs_dir() / f"{safe}.json"


def work_dir(entry_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in entry_id)
    d = WORK / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_job(entry_id: str) -> Optional[dict]:
    p = job_path(entry_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def save_job(entry_id: str, job: dict) -> None:
    p = job_path(entry_id)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(job, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def new_job(entry_id: str, epub: Path, voice: str, title: str = "",
            author: str = "") -> dict:
    return {
        "entry_id": entry_id,
        "epub": str(epub),
        "voice": voice,
        "voice_ref": "",
        "title": title or epub.stem,
        "author": author,
        "phase": "queued",
        "error": None,
        "created": time.time(),
        "updated": time.time(),
        "total_chunks": 0,
        "done_chunks": 0,
        "chapters": [],          # [{title, words, start, end, chunks:[n]}]
        "chunks_done": [],       # indices already rendered to disk
        "audio_seconds": 0.0,
    }


LIVE_PHASES = ("queued", "parsing", "narrating", "assembling")


def enqueue(entry_id: str, epub: Path, voice: str, title: str = "",
            author: str = "", restart: bool = False,
            voice_ref: str = "") -> dict:
    """Queue a book, WITHOUT destroying a conversion already in progress.

    Measured bug: this used to set phase="queued" unconditionally, so every
    POST /audiobook/start reset the job. With three POSTs the worker parsed
    once ("parsed 33 chapters, 3156 chunks") and the job sat at queued with
    total=3156 forever -- reopening the dialog, or double-clicking, silently
    threw away a multi-hour conversion.

    Now a live job is idempotent: a re-POST only updates the voice. Only an
    errored or cancelled job re-queues, or an explicit restart=True.
    """
    existing = load_job(entry_id)
    if existing and existing.get("phase") == "done" and not restart:
        return existing
    if (existing and existing.get("phase") in LIVE_PHASES
            and not restart):
        # already running: adopt the new voice, keep all progress
        changed = False
        if voice and existing.get("voice") != voice:
            existing["voice"] = voice
            changed = True
        if voice_ref and existing.get("voice_ref") != voice_ref:
            existing["voice_ref"] = voice_ref
            changed = True
        if changed:
            existing["updated"] = time.time()
            save_job(entry_id, existing)
        return existing

    job = existing or new_job(entry_id, epub, voice, title, author)
    job.update({"epub": str(epub), "voice": voice,
                "voice_ref": voice_ref or job.get("voice_ref") or "",
                "title": job.get("title") or title or epub.stem,
                "author": job.get("author") or author,
                "phase": "queued", "error": None, "updated": time.time()})
    if restart:
        # a real restart discards partial work
        job["chunks_done"] = []
        job["chapters"] = []
        job.pop("chunk_texts", None)
        job["total_chunks"] = 0
        job["audio_seconds"] = 0.0
    save_job(entry_id, job)
    return job


def cancel(entry_id: str) -> bool:
    job = load_job(entry_id)
    if not job or job.get("phase") == "done":
        return False
    job["phase"] = "cancelled"
    job["updated"] = time.time()
    save_job(entry_id, job)
    shutil.rmtree(work_dir(entry_id), ignore_errors=True)
    return True


def progress(entry_id: str) -> dict:
    """Compact progress for the UI."""
    job = load_job(entry_id)
    if not job:
        return {"phase": "none"}
    total = job.get("total_chunks") or 0
    done = len(job.get("chunks_done") or [])
    pct = int(100 * done / total) if total else 0
    return {
        "phase": job.get("phase"),
        "pct": pct,
        "done": done,
        "total": total,
        "error": job.get("error"),
        "voice": job.get("voice"),
        "voice_ref": job.get("voice_ref") or "",
        "title": job.get("title"),
        "result": job.get("result"),
        "audio_seconds": round(job.get("audio_seconds") or 0.0, 1),
    }


# ------------------------------------------------------------- the work ---

def _chunk_files(wd: Path) -> List[Path]:
    return sorted(wd.glob("chunk_*.mp3"),
                  key=lambda p: int(p.stem.split("_")[1]))


def run_job(entry_id: str, log=print) -> dict:
    """Advance a job as far as it can. Safe to call repeatedly.

    Each call does at most ONE chunk, so the maintenance thread stays
    responsive and a crash costs one chunk, not the book.
    """
    job = load_job(entry_id)
    if not job:
        raise AB.ConversionError(f"no job for {entry_id}")
    if job.get("phase") in ("done", "cancelled"):
        return job

    epub = Path(job["epub"])
    wd = work_dir(entry_id)
    voice = job.get("voice") or ""
    # (voice_ref is read from the job inside the narrate branch, so a job
    # updated between chunks picks the change up on its next pass)

    # ---- 1. parse -------------------------------------------------------
    if job["phase"] == "queued":
        try:
            book = AB.read_epub(epub)
        except Exception as exc:
            job.update({"phase": "error", "error": f"parse: {exc}",
                        "updated": time.time()})
            save_job(entry_id, job)
            return job
        chapters = []
        idx = 0
        for ch in book.chapters:
            chunks = AB.split_chunks(ch.text)
            if not chunks:
                continue
            chapters.append({
                "title": ch.title, "words": ch.words,
                "start": None, "end": None,
                "chunk_from": idx, "chunk_to": idx + len(chunks) - 1,
            })
            idx += len(chunks)
        job.update({
            "phase": "narrating",
            "book_title": book.title or job.get("title"),
            "book_author": book.author or job.get("author"),
            "chapters": chapters,
            "total_chunks": idx,
            "chunk_texts": None,      # rebuilt on demand; not persisted
            "updated": time.time(),
        })
        save_job(entry_id, job)
        log(f"[audiobook {entry_id[:28]}] parsed {len(chapters)} chapters, "
            f"{idx} chunks, {book.words:,} words")
        return job

    # ---- 2. narrate one chunk ------------------------------------------
    if job["phase"] == "narrating":
        if "chunk_texts" not in job or not job.get("chunk_texts"):
            book = AB.read_epub(epub)
            texts = []
            for ch in book.chapters:
                texts.extend(AB.split_chunks(ch.text))
            job["chunk_texts"] = texts
            save_job(entry_id, job)

        texts = job["chunk_texts"]
        done = set(job.get("chunks_done") or [])
        nxt = next((i for i in range(len(texts)) if i not in done), None)
        if nxt is None:
            job["phase"] = "assembling"
            job["updated"] = time.time()
            save_job(entry_id, job)
            return job

        text = texts[nxt]
        t0 = time.time()
        try:
            # Zero-shot: when the job carries a reference clip, it is the
            # voice, and the named voice is irrelevant.
            wav = AB.synthesize(text, voice,
                                voice_ref=job.get("voice_ref") or "")
        except Exception as exc:
            job.update({"phase": "error",
                        "error": f"chunk {nxt}: {exc}",
                        "updated": time.time()})
            save_job(entry_id, job)
            return job

        raw = wd / f"chunk_{nxt:05d}.wav"
        raw.write_bytes(wav)
        mp3 = wd / f"chunk_{nxt:05d}.mp3"
        try:
            AB.wav_to_mp3(raw, mp3)
            raw.unlink(missing_ok=True)
        except Exception as exc:
            job.update({"phase": "error", "error": f"encode {nxt}: {exc}",
                        "updated": time.time()})
            save_job(entry_id, job)
            return job

        job.setdefault("chunks_done", []).append(nxt)
        job["audio_seconds"] = round(
            (job.get("audio_seconds") or 0.0) + AB.wav_seconds(mp3), 1)
        job["updated"] = time.time()
        save_job(entry_id, job)
        words = len(text.split())
        log(f"[audiobook {entry_id[:28]}] chunk {nxt+1}/{len(texts)} "
            f"{words}w in {time.time()-t0:.1f}s")
        return job

    # ---- 3. assemble ---------------------------------------------------
    if job["phase"] == "assembling":
        parts = _chunk_files(wd)
        if len(parts) < (job.get("total_chunks") or 0):
            job.update({"phase": "error",
                        "error": f"only {len(parts)} of "
                                 f"{job['total_chunks']} chunks on disk",
                        "updated": time.time()})
            save_job(entry_id, job)
            return job

        full = wd / "full.mp3"
        AB.concat_mp3(parts, full)

        # chapter boundaries: duration of each chunk in order
        bounds, t = [], 0.0
        for i, p in enumerate(parts):
            d = AB.wav_seconds(p)  # mp3; ffprobe still reads duration
            for ch in job.get("chapters", []):
                if ch.get("chunk_from") == i and ch.get("start") is None:
                    ch["start"] = round(t, 3)
            t += d
        for ch in job.get("chapters", []):
            if ch.get("start") is not None and ch.get("end") is None:
                ch["end"] = round(t, 3)
        bounds = [(c["start"], c["end"], c["title"])
                  for c in job.get("chapters", [])
                  if c.get("start") is not None and c.get("end") is not None]

        title = job.get("book_title") or job.get("title") or epub.stem
        author = job.get("book_author") or job.get("author") or ""
        out = epub.with_suffix(".m4b")
        # mux_with_chapters returns False rather than raising, and leaves the
        # metadata file behind for diagnosis. A failed chapter mux must NOT
        # discard the mp3: it is a usable audiobook, and throwing it away
        # would destroy hours of synthesis.
        if AB.mux_with_chapters(full, out, bounds, title, author):
            AB.write_ncue(out, bounds, author)
            result, failed = out, False
        else:
            result, failed = full, True

        job.update({
            "phase": "done",
            "result": str(result),
            "m4b_failed": failed,
            "chapters": job.get("chapters"),
            "bytes": result.stat().st_size if result.exists() else 0,
            "updated": time.time(),
        })
        job.pop("chunk_texts", None)
        save_job(entry_id, job)
        for p in parts:
            p.unlink(missing_ok=True)
        if not failed:
            # the m4b is the deliverable, so the intermediate mp3 can go
            full.unlink(missing_ok=True)
        log(f"[audiobook {entry_id[:28]}] DONE {result.name} "
            f"{job.get('bytes', 0):,}b, {len(bounds)} chapters"
            + (" (m4b mux failed, mp3 delivered)" if failed else ""))
        return job

    return job


def pending_jobs() -> List[str]:
    out = []
    for p in _jobs_dir().glob("*.json"):
        try:
            j = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if j.get("phase") in ("queued", "narrating", "assembling"):
            out.append(j["entry_id"])
    return out


def active_job() -> Optional[str]:
    p = pending_jobs()
    return p[0] if p else None

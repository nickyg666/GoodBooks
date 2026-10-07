"""GoodBooks audiobook generator plugin.

This is the audiobook generator that used to be 16 functions and 8 routes
hardcoded into app.py, moved behind the plugin contract in gb_plugins.py.

What changed, and why it matters:

  * the host passes a PluginContext, so nothing here imports app.py. That
    import is the mistake that has destroyed live library data three times in
    this project (1,074 records; 4,869 -> 73; 4,837 -> 37) because importing
    the host boots a second service instance with its own metadata cache.
  * the routes register from register(ctx) instead of at app.py import time,
    so the audiobook surface can be added or removed without touching the
    host.
  * the maintenance tick is a hook, so the narration budget, the enrichment
    budget and the job advance are all scheduled by the same mechanism.

The heavy lifting stays in the modules that were already extracted and tested:
gb_audiobook (synthesis/chapters/mux), gb_abjob (queue, variants, assembly),
gb_extract (any library format -> chapters), gb_parallel (threaded narration
with the half+1 CPU cap) and gb_retry (bounded retry for transient TTS
failures). This file is the integration surface only.
"""
from __future__ import annotations

import time
from pathlib import Path

PLUGIN_ID = "audiobook"
PLUGIN_NAME = "Audiobook generator"
PLUGIN_VERSION = "1.0.0"

# Narration budget: of the 900 s maintenance window, leaving room for the
# other hooks. Measured ~18 s/chunk on 3 workers, so 600 s is ~30 chunks.
NARRATION_BUDGET_SECONDS = 600
NARRATION_MAX_CHUNKS = 40

_state = {"ctx": None, "log": print}



# The paths this plugin owns. Exposed at module level (not only inside the
# dict returned by register) because the manager needs them on the
# runtime-enable path too: when register() is skipped -- which it must be,
# since Flask forbids adding routes after the first request -- the guard
# still has to know which URLs belong here in order to stop answering while
# the plugin is disabled.
ROUTES = [
    "/audiobook/start",
    "/audiobook/cancel",
    "/audiobook/options",
    "/audiobook/estimate",
    "/audiobook/preview",
    "/audiobook/progress",
    "/audiobook/voices",
    "/api/audiobooks",
]

def register(ctx):
    """Called once at load. Returns the hooks the host may invoke.

    `register` receives the context and does the wiring -- routes, in this
    case -- then hands back callables. Everything the plugin needs arrives
    through `ctx`; nothing is imported from the host.
    """
    _state["ctx"] = ctx
    _state["log"] = ctx.logger.info

    from flask import jsonify, request, send_file
    app = ctx.app
    svc = ctx.services
    settings = ctx.settings
    DATA_DIR = ctx.data_dir

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _entry(entry_id: str):
        e = svc.get_library_entry(entry_id)
        if not e:
            from werkzeug.exceptions import NotFound
            raise NotFound(description="book not found in library")
        return e

    def _resolve_path(entry) -> str:
        """The real file for a library entry.

        Delegates to the host service because the '<root>::<relpath>'
        composite id format is the host's convention, not the plugin's.
        """
        return svc.resolve_book_file(str(entry.get("id") or "")) or ""

    def _quality_choices():
        import gb_audiobook as AB
        out = []
        for bitrate, info in AB.AUDIO_QUALITY.items():
            out.append({
                "bitrate": bitrate,
                "kbps": AB._bitrate_kbps(info),
                "label": info.get("label", bitrate),
                "m10": info.get("m10"),
            })
        return out

    def _user_options():
        out = []
        for u in (getattr(settings, "users", None) or []):
            out.append({
                "name": getattr(u, "name", "") or "",
                "kindle_email": getattr(u, "kindle_email", "") or "",
                "notification_email": getattr(u, "notification_email", "") or "",
            })
        return out

    # ------------------------------------------------------------------
    # routes
    # ------------------------------------------------------------------
    @app.route("/audiobook/preview", methods=["POST"])
    def audiobook_preview_post():
        import gb_audiobook as AB
        entry_id = (request.form.get("entry_id") or "").strip()
        if not entry_id:
            return jsonify({"ok": False, "error": "entry_id is required"}), 400
        entry = _entry(entry_id)
        src = _resolve_path(entry)
        if not src:
            return jsonify({"ok": False,
                            "error": "the file for this book is missing on disk"}), 400
        voice = (request.form.get("voice") or "").strip()
        voice_ref = (request.form.get("voice_ref") or "").strip()
        try:
            path = AB.make_preview(entry_id, Path(src), voice,
                                   voice_ref=voice_ref)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)[:200]}), 422
        if not path:
            return jsonify({"ok": False, "error": "no preview produced"}), 422
        return send_file(str(path), mimetype="audio/mpeg")

    @app.route("/audiobook/preview")
    def audiobook_preview_get():
        import gb_audiobook as AB
        entry_id = (request.args.get("entry_id") or "").strip()
        voice = (request.args.get("voice") or "").strip()
        voice_ref = (request.args.get("voice_ref") or "").strip()
        path = AB.preview_path(entry_id, voice, voice_ref)
        if not path or not Path(path).exists():
            return jsonify({"ok": False, "error": "no preview"}), 404
        return send_file(str(path), mimetype="audio/mpeg")

    @app.route("/audiobook/estimate")
    def audiobook_estimate():
        """Estimated LISTENING length and final size.

        Deliberately separates the two rates: WORDS_PER_SECOND is synthesis
        throughput (how long this host takes to make the audio) and
        WORDS_PER_SECOND_PLAYBACK is how long the finished book plays. Showing
        the synthesis figure under the words "of audio" overstated a
        201,709-word book as 17 hours instead of ~40 minutes.
        """
        import gb_audiobook as AB
        import gb_extract as EX

        entry_id = (request.args.get("entry_id") or "").strip()
        words = request.args.get("words")
        if not words and entry_id:
            entry = _entry(entry_id)
            src = _resolve_path(entry)
            if not src:
                return jsonify({"ok": False, "error": "file missing"}), 404
            try:
                book = EX.read_book(src)      # any supported format
                words = book.words
            except Exception as exc:
                return jsonify({"ok": False, "error": str(exc)[:200]}), 422
        try:
            n = int(words) if words else 0
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "words must be a number"}), 400

        bitrate = (request.args.get("bitrate") or AB.AUDIO_DEFAULT).strip()
        if bitrate not in AB.AUDIO_QUALITY:
            bitrate = AB.AUDIO_DEFAULT
        est = AB.estimate_voice_duration(n, bitrate=bitrate)
        play = (n / AB.WORDS_PER_SECOND_PLAYBACK) if AB.WORDS_PER_SECOND_PLAYBACK else 0.0
        return jsonify({
            "ok": True, "words": n, "bitrate": bitrate, **est,
            "play_seconds": round(play, 1),
            "play_hours": round(play / 3600.0, 2),
            "synth_hours": est.get("hours"),
            "words_per_second": AB.WORDS_PER_SECOND,
            "words_per_second_playback": AB.WORDS_PER_SECOND_PLAYBACK,
        })

    @app.route("/audiobook/options")
    def audiobook_options():
        import gb_audiobook as AB
        import gb_abjob as JOB
        return jsonify({
            "codec": "aac",
            "container": "m4b",
            "default_bitrate": AB.AUDIO_DEFAULT,
            "sample_rate": AB.SAMPLE_RATE,
            "note": ("The finished file is AAC in an M4B container. At this "
                     "sample rate AAC stops improving above about 96 kbps, so "
                     "there is no 192 kbps option: it would encode identically "
                     "to 96."),
            "qualities": _quality_choices(),
            "voices": AB.list_voices(),
            "users": _user_options(),
            "queue": {"depth": len(JOB._all_jobs()), "limit": JOB.MAX_QUEUED_JOBS},
            "words_per_second": AB.WORDS_PER_SECOND,
            "words_per_second_playback": AB.WORDS_PER_SECOND_PLAYBACK,
            "supported_formats": list(__import__("gb_extract").SUPPORTED_FORMATS),
        })

    @app.route("/audiobook/reference", methods=["POST"])
    def audiobook_upload_reference():
        """Store a reference clip so the studio can fetch it for cloning."""
        entry_id = (request.form.get("entry_id") or "").strip()
        clip = request.files.get("clip")
        if not entry_id or clip is None:
            return jsonify({"ok": False, "error": "entry_id and clip required"}), 400
        target = DATA_DIR / "audiobook_refs" / (svc.read_metadata(entry_id)
                                                .get("title", "book")
                                                .replace("/", "_")[:80] + ".wav")
        target.parent.mkdir(parents=True, exist_ok=True)
        clip.save(str(target))
        meta = svc.load_library_metadata()
        meta.setdefault(entry_id, {})["audiobook_voice_ref"] = str(target)
        svc.atomic_write_metadata(meta)
        return jsonify({"ok": True, "path": str(target)})

    @app.route("/audiobook/reference")
    def audiobook_reference_status():
        entry_id = (request.args.get("entry_id") or "").strip()
        path = svc.read_metadata(entry_id).get("audiobook_voice_ref")
        if not path or not Path(path).exists():
            return jsonify({"ok": False, "error": "no reference stored"}), 404
        return jsonify({"ok": True, "path": path})

    @app.post("/audiobook/start")
    def audiobook_start():
        """Queue a book for narration. Returns immediately.

        Every format the extractor supports is accepted -- epub, mobi, azw3,
        azw, pdf -- so a guard against .epub here would make 34.6% of the
        library unconvertible despite the extractor handling all of it.
        """
        import gb_abjob as JOB
        import gb_extract as EX

        entry_id = (request.form.get("entry_id")
                    or request.form.get("id") or "").strip()
        if not entry_id:
            return jsonify({"ok": False, "error": "entry_id is required"}), 400
        entry = _entry(entry_id)
        voice = (request.form.get("voice") or "").strip() or \
            (getattr(settings, "audiobook_voice", "") or "nick")

        src = _resolve_path(entry)
        if not src:
            return jsonify({"ok": False,
                            "error": "the file for this book is missing on disk"}), 400

        # DRM is reported distinctly: the text cannot be read by any tool,
        # which is different from a format we do not support.
        if EX.is_drm_locked(src):
            return jsonify({
                "ok": False, "drm": True,
                "error": ("This book is DRM-encrypted, so the text cannot be "
                          "read. Try a DRM-free edition."),
            }), 400
        fmt = EX.detect_format(src)
        if fmt not in EX.SUPPORTED_FORMATS:
            return jsonify({
                "ok": False, "format": fmt,
                "error": (f"Cannot convert '{fmt or 'unknown'}'. Supported: "
                          f"{', '.join(EX.SUPPORTED_FORMATS)}"),
            }), 400

        voice_ref = (request.form.get("voice_ref") or "").strip()
        fmt_out = (request.form.get("format") or "m4b").strip() or "m4b"
        bitrate = (request.form.get("bitrate") or "64k").strip() or "64k"
        restart = (request.form.get("restart") or "").strip() in ("1", "true", "on")
        regenerate = (request.form.get("regenerate") or "").strip() in ("1", "true", "on")
        auto_send = (request.form.get("auto_send") or "").strip() in ("1", "true", "on", "yes")
        notify = (request.form.get("notify") or "").strip() in ("1", "true", "on", "yes")
        for_user = (request.form.get("user") or "").strip()

        rewrite = bool(restart or regenerate)
        if not rewrite:
            done = JOB.completed_for(
                entry_id, JOB.variant_key(voice, voice_ref, bitrate, fmt_out))
            if done:
                return jsonify({
                    "ok": True, "already_done": True, "entry_id": entry_id,
                    "result": done.get("result"), "title": done.get("title"),
                    "message": ("This book is already narrated. Use regenerate "
                                "to do it again (for example if the audio is "
                                "damaged)."),
                    "progress": JOB.progress(entry_id),
                })

        try:
            job = JOB.enqueue(
                entry_id, Path(src), voice,
                entry.get("title", ""), entry.get("author", ""),
                restart=restart, voice_ref=voice_ref, for_user=for_user,
                auto_send=auto_send, notify=notify, bitrate=bitrate,
                regenerate=regenerate, fmt=fmt_out)
        except JOB.QueueFull as exc:
            return jsonify({"ok": False, "queued": False, "error": str(exc),
                            "queue_full": True,
                            "limit": JOB.MAX_QUEUED_JOBS}), 409

        return jsonify({"ok": True, "queued": True, "entry_id": entry_id,
                        "position": JOB.queue_position(entry_id),
                        "progress": JOB.progress(entry_id),
                        "message": "Queued. Progress is polled from /api/audiobooks."})

    @app.route("/api/audiobooks")
    def api_audiobooks():
        """The audiobook list, in the shape the Audiobooks view consumes.

        The view's JavaScript expects, per row:
            title, narrator, cloned, chapters, hours, mb, delivered
        and at the top level:
            count, total_hours, total_mb

        The first version of this plugin returned only progress keys
        (audio_seconds, done, total, pct, phase), so fmtDur(a.hours) rendered
        NaN and d.total_mb.toLocaleString() threw -- which the view's .catch()
        reported as "Could not load the audiobook list." even though this
        endpoint answered 200 with real data.

        So both shapes are served: the view's keys are authoritative, and the
        progress keys are kept alongside them so nothing that already reads
        them loses anything.
        """
        import re
        import gb_abjob as JOB
        import gb_audiobook as AB

        live_phases = ("queued", "parsing", "narrating", "assembling")
        out = []
        total_seconds = 0.0
        total_mb = 0.0

        for j in JOB._all_jobs():
            eid = j.get("entry_id")
            row = dict(JOB.progress(eid))
            row["result"] = j.get("result")
            row["bytes"] = j.get("bytes")

            # ---- the keys the view needs -------------------------------
            secs = float(row.get("audio_seconds") or 0.0)
            row["hours"] = round(secs / 3600.0, 2) if secs else 0.0

            mb = 0.0
            if j.get("bytes"):
                try:
                    mb = int(j["bytes"]) / 1048576.0
                except (TypeError, ValueError):
                    mb = 0.0
            elif secs and row.get("bitrate") in AB.AUDIO_QUALITY:
                info = AB.AUDIO_QUALITY[row["bitrate"]]
                mb = round(secs * AB._bitrate_kbps(info) * 1000.0
                           / 8.0 / 1048576.0, 1)
            row["mb"] = round(mb, 1)

            voice = (j.get("voice") or "").strip()
            voice_ref = (j.get("voice_ref") or "").strip()
            row["narrator"] = voice or ("cloned voice" if voice_ref
                                        else "unknown")
            row["cloned"] = bool(voice_ref)

            # Chapters only exist once assembly has run.
            row["chapters"] = len(j.get("chapters") or []) or None

            # Who this audiobook belongs to and what cover to show.
            # entry_id lets the view link back to the source book; cover
            # is the entry's cover URL when one exists, else ''.
            row["entry_id"] = eid
            cover = ""
            try:
                e = svc.get_library_entry(eid)
                if e:
                    cover = (e.get("cover") or "") if isinstance(e, dict) else getattr(e, "cover", "") or ""
            except Exception:
                cover = ""
            # 'data/covers/<32-hex>.jpg' is a FILE path; the URL the browser can
            # load is the /cover/<32-hex> route. Emitting the raw relative path
            # 404'd and showed a broken-image X on rows that DO have covers.
            if cover:
                m = re.match(r".*data/covers/([0-9a-fA-F]{32})\.(?:jpg|jpeg|png|webp|gif)$", cover)
                if m:
                    cover = "/cover/" + m.group(1)
                elif not cover.startswith(("http://", "https://", "/")):
                    cover = ""            # unknown form: show the empty cover
            row["cover"] = cover

            if j.get("phase") == "done":
                total_seconds += secs
                total_mb += mb

            out.append(row)

        out.sort(key=lambda d: str(d.get("title") or "").casefold())
        return jsonify({
            "ok": True,
            "count": len(out),
            "total_hours": round(total_seconds / 3600.0, 2),
            "total_mb": round(total_mb, 1),
            "audiobooks": out,
        })

    @app.route("/audiobook/progress")
    def audiobook_progress():
        import gb_abjob as JOB
        entry_id = (request.args.get("entry_id") or "").strip()
        return jsonify(JOB.progress(entry_id))

    @app.post("/audiobook/cancel")
    def audiobook_cancel():
        import gb_abjob as JOB
        entry_id = (request.form.get("entry_id") or "").strip()
        return jsonify({"ok": JOB.cancel(entry_id), "entry_id": entry_id})

    @app.route("/audiobook/voices")
    def audiobook_voices():
        import gb_audiobook as AB
        voices = AB.list_voices()
        return jsonify({"voices": voices})

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------
    def maintenance(**kwargs):
        """Advance narration inside the maintenance window.

        Runs FIRST in the cycle and has a hard budget, because narration is
        the visible progress. When enrichment had no budget it held this
        thread for minutes and narration logged zero chunks -- measured 0
        chunks against 231 enrichment actions over 12 minutes.
        """
        import gb_abjob as JOB
        entry_id = JOB.active_job()
        if not entry_id:
            return None
        state = JOB.load_job(entry_id) or {}
        if state.get("phase") not in ("queued", "narrating", "assembling"):
            return None
        job = JOB.run_job(
            entry_id, log=ctx.logger.info,
            budget_seconds=NARRATION_BUDGET_SECONDS,
            max_chunks=NARRATION_MAX_CHUNKS)
        return {"entry_id": entry_id, "phase": job.get("phase"),
                "chunks": len(job.get("chunks_done") or []),
                "total": job.get("total_chunks")}

    return {
        "maintenance": maintenance,
        # exposed for tests and the host's plugin route
        "_routes": ["/audiobook/start", "/audiobook/options",
                    "/audiobook/estimate", "/audiobook/preview",
                    "/audiobook/progress", "/audiobook/cancel",
                    "/audiobook/voices", "/api/audiobooks"],
    }

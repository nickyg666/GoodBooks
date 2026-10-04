---
tags: [goodbooks, audiobook, das, service, plugin]
host: das@192.168.0.9
repo: nickyg666/GoodBooks (branch 2026, local tmp-push)
audited: 2026-10-04
status: healthy
---

# GoodBooks on DAS — service state and audiobook subsystem

Single-page operational notebook. Everything here was **measured** against the
running service on 2026-10-04, not inferred from logs or source. Where a
number came from a probe, the probe is named.

## Where it lives

| | |
|---|---|
| Host | `das@192.168.0.9` (password `1`, passwordless `sudo -n` works) |
| Project | `/usr/local/bin/GoodBooks` |
| Service | `GoodBooks.service`, port `5000`, under **waitress** (not Flask's dev server) |
| Repo | `nickyg666/GoodBooks`, remote branch `2026`; local branch `tmp-push` |
| Library | `/mnt/8tbdas/GoodBooks` (7.3 TB, 75% used, 1.8 TB free) |
| TTS backend | PocketTTS on `.168` via Caddy `https://192.168.0.168/voice-studio/` |

⚠️ **GoodBooks is on DAS, not on `.168`.** `127.0.0.1:5000` in a browser on
`.168` is **Wyze-Bridge** and redirects to `/login`. Testing the wrong host
wasted a round early on.

## Health at audit time

```
repo        398 tracked, clean, HEAD == origin/2026
data        4,869 metadata records (12.9 MB), 29 history entries
            0 stray .tmp, 0 records with no title
service     active, 0 restarts, 24 threads, 263 MB RSS
errors      NONE in the last hour
tests       254 passed, 2 skipped
packaging   all checks pass
audit       NO STRUCTURAL PROBLEMS FOUND
plugin      audiobook v1.0.0, enabled, 8 routes, no error
```

## The plugin format

The audiobook generator is a **plugin**, not part of app.py. It was 16
functions and 8 routes inline before, which is why editing it kept risking
the whole service.

```
plugins/audiobook/plugin.json     manifest: id, name, version, provides
plugins/audiobook/__init__.py     register(ctx) -> routes + hooks
gb_plugins.py                     PluginManager: discover/load/enable/disable
```

* `PluginContext` carries `app`, `settings`, `base_dir`, `data_dir`, `logger`
  and a `ServiceAPI` of permitted calls.
* **A plugin must never `import app`.** Importing the host boots a second
  service instance with its own metadata cache. That has destroyed live
  library data **three times**: 1,074 records; 4,869 → 73; 4,837 → 37.
  There is a test that fails if any plugin imports the host.
* A plugin that raises on load is reported and skipped — it never takes the
  service down.
* Management: `/api/plugins`, `POST /api/plugins/<id>/enable|disable`.
  Enable/disable is a **gate** (a `before_request` check), not an
  unregister — Flask's `url_map` cannot be subtracted from.

Supported formats, by the extractor (`gb_extract`): **epub, mobi, azw3, azw,
pdf** — 4,835 of 4,837 books. DRM files are reported, not cracked.

## Routes and latency

```
/                        1129 ms   <- outlier, see open items
/?view=collection         207 ms
/?view=folder              85 ms
/?view=cover               46 ms
/?view=audiobooks          71 ms
/?view=plugins             61 ms
/api/audiobooks             9 ms
/api/plugins               1 ms
/api/library-authors      755 ms   cold generation; warm is 8-50 ms
/audiobook/options        125 ms
```

## Performance work that stuck

| Change | Result |
|---|---|
| Author-options cache | `/?view=compact` **993 ms → 66 ms** |
| Genre dropdown bounded | 2,100 `<option>` → 31; page 504 KB → 285 KB |
| Log rotation moved to the maintenance cycle | was never running; ~200 MB debris reclaimed |

The author cache was the big one: `provide_author_options()` took **506 ms**
over the real 4,869-entry library and `index()` called it on **every** page
view to rebuild an identical list. Cached against the library generation —
deliberately **not** a TTL, since a stale author list makes the filter hide a
book the user can plainly see.

Narration uses **half+1 of the CPU thread count** (DAS: 4 cores → **3
workers**), as a hard cap, with a second ceiling so a many-core box cannot
stampede the synthesiser.

## Open items

1. **32 metadata keys resolve to no file** (4,837/4,869 do). All
   `::sagey/…`; only 1 shares a title with a working key, so they are not
   simple duplicates. Left alone — pruning library metadata is destructive
   and there is no evidence they should go. **Needs a decision.**
2. **`/` is 1.1 s** while every library view is 40–210 ms. Real outlier;
   the views actually used are all fast.
3. **43 backup files in `/tmp` on DAS** from this work. Outside the repo,
   cleared on reboot, but worth knowing if anything reads `/tmp`.

## Traps this project keeps hitting

Every one of these cost real time. They are properties of the codebase, not
one-offs.

* **Never `import app`** — see above. Test scripts must slice functions out
  and `exec` them, or run against the live HTTP service.
* **`b.click()` in JavaScript bypasses hit-testing.** It proves a handler
  exists and nothing about whether a person can click the control. Use
  `elementFromPoint` at the element's own centre, or click by coordinates.
* **A 200 on `?view=x` proves the view renders when asked for by URL.** It
  proves nothing about whether a person can get there.
* **`replace(old, new, 1)` hits the wrong site** when two regions share text.
  This caused a duplicated cycle head, a duplicate `_library_generation`, a
  self-calling author cache, and a patch landing in the wrong function. Locate
  the target structurally — by AST or line range — and assert the result.
* **`src.split("\n")` + `"".join(...)` collapses the file to one line.**
  Use `splitlines(keepends=True)` or `"\n".join`.
* **Rebuilding a function body silently deletes module-level constants** in
  the span (they are not `def`s). This killed `MIN_CHUNK_CHARS` and
  `_HEADING_ONLY`. When history has the value, take it from
  `git show HEAD:file` rather than reconstructing it.
* **Inherited `overflow:hidden` clips a child permanently.** A Convert button
  inside `.library-cover` was fully painted-but-unclickable; z-index and
  pointer-events cannot rescue a clipped element. 7 of 50 cards were
  affected, which read as "the button is sometimes missing".
* **Job-file mtime is not liveness** — it is written once per window. Read the
  journal.
* **`glob()` order is arbitrary but stable**, so `pending_jobs()[0]` starves
  every other job forever. Sort by staleness.

## Operational commands

```bash
# health
systemctl is-active GoodBooks
curl -s -o /dev/null -w '%{http_code} %{time_total}\n' http://127.0.0.1:5000/

# tests + gates
cd /usr/local/bin/GoodBooks
python3 -m pytest tests/ -p no:warnings      # 254 passed, 2 skipped
python3 scripts/check_packaging.py            # all checks passed
python3 gb_audit.py                           # structural audit

# plugins
curl -s http://127.0.0.1:5000/api/plugins | python3 -m json.tool
curl -s -X POST http://127.0.0.1:5000/api/plugins/audiobook/disable

# audiobook queue (liveness from the journal, not the job file)
sudo -n journalctl -u GoodBooks --since=-10min --no-pager | grep -c 'chunk '
python3 /tmp/gb_job_report.py
```

## Scanned books

A book with page **images** and no text layer cannot be narrated. It is now
identified rather than misreported:

```
this book is a SCAN: 24 MB of page images with only 0 characters of text,
so there is nothing to narrate. It needs OCR, or a text edition.
```

Measured on a real library file: 126 page images (24.4 MB) and **2,340
characters of text** across 127 documents.

## Related

- [[Nextcloud-Todos]] — the other house service, on `.168`
- [[A733-I2C-Register-Map]] — the kernel lane on `.168`

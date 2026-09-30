# GoodBooks Documentation

**Last reviewed: 2026-09-29.**

Documents here describe the current system. Point-in-time session reports
(`FINAL_SESSION_REPORT.md` and friends) have been moved to
`docs/archive/sessions/` — they were accurate when written and are kept only
as a record; do not follow them.

## Start here

| Document | Read it for |
|---|---|
| [README.md](README.md) | What the app does, how to run it, configuration, architecture |
| [INSTALLER_GUIDE.md](INSTALLER_GUIDE.md) | Installation, systemd setup, first run |

## How it works

| Document | Read it for |
|---|---|
| [METADATA_REFRESH_OPTIMIZATION.md](METADATA_REFRESH_OPTIMIZATION.md) | How the metadata refresh decides what to skip |
| [SEARCH_MATCHING_ANALYSIS.md](SEARCH_MATCHING_ANALYSIS.md) | Result ranking and the strict matcher |
| [LIBGEN_FALLBACK_IMPLEMENTATION.md](LIBGEN_FALLBACK_IMPLEMENTATION.md) | The LibGen fallback chain |
| [DOWNLOAD_FIXES.md](DOWNLOAD_FIXES.md) | Download resolution, mirrors, concurrency |
| [RANDOM_BUTTON.md](RANDOM_BUTTON.md) | Random selection, and the render-time network bug that made it slow |

## The UI

| Document | Read it for |
|---|---|
| [RECENTLY_ADDED_VIEW_GUIDE.md](RECENTLY_ADDED_VIEW_GUIDE.md) | The recently-added view |
| [PROGRESS_BARS_FIXED.md](PROGRESS_BARS_FIXED.md) | Feed and metadata progress bars |
| [CRITICAL_ISSUES_AND_FIXES.md](CRITICAL_ISSUES_AND_FIXES.md) | Known issues and their fixes |

## Things that will waste your time if you don't know them

1. **Anna's Archive `.org` and `.se` are dead.** They no longer resolve in
   DNS. `.li` and `.rs` are parked domains. The only live frontend is
   `annas-archive.gl`, behind a DDoS-Guard challenge that needs a real
   browser. If a log shows `annas-archive.se`, that log predates the fix.

2. **Nothing hardcodes a mirror.** `gb_mirrors_live.py` asks SLUM which
   mirrors are up and ranks them by measured speed; `gb_fastdl.py` races the
   fastest few. Measured spread across live LibGen mirrors: 27–57 KiB/s.

3. **Stop the service before repairing metadata.** It holds the file in
   memory and rewrites it within ~8 seconds, silently discarding external
   writes.

4. **A search page used to never paint.** It performed a live AA fetch while
   rendering. Now it renders immediately and streams results from
   `/api/search-stream`.

5. **Strict matching is deliberate.** Single-word titles require an exact
   match, so *"dune"* will not fetch *"Dune Messiah"*. A wrong book emailed
   to a Kindle is worse than no book.

## Adding to this index

If a document describes past-tense work rather than how the system behaves,
put it in `docs/archive/sessions/`. The index should only carry things that
are true today.

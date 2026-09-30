# Random Book Button

**Status: working.** This replaces three earlier documents
(`RANDOM_BUTTON_FIXES.md`, `RANDOM_BUTTON_IMPLEMENTATION.md`,
`RANDOM_BUTTON_IMPROVEMENTS.md`), which described a 2025 bug and cosmetic
tweaks. They are archived in `docs/archive/`.

## What it does

`GET /book/random?count=N` picks N random books from the library, honouring
the filters and folder you are currently viewing (`view`, `prefix`, `genre`,
`author`), and redirects to the first one.

## The bug that mattered

Through 2026-09 the button was not slow because of the random selection —
it was slow because **rendering the chosen book did network I/O**.
`ensure_library_metadata()` performed a live Goodreads search and an
Anna's Archive lookup *while the page was rendering*. Goodreads throttles
with `202` responses and a zero-length body, so the page blocked:

```
0.17s   for books with complete metadata
16-28s  for books with a thin metadata block   (same click, different book)
```

Fixed by giving `ensure_library_metadata()` an `allow_network=False`
parameter (the default), which gates both lookups. Backfill happens in the
background maintenance cycle instead. Measured after the fix: **0.006s to
0.36s** across repeated picks, including the books that previously took 28s.

If random ever feels slow again, check for a live request in
`debug.log` during page render before looking at the selection logic.

## Earlier fix, kept for reference

An earlier version of the button posted to `/book/random`, which was never a
Flask route, so every click produced "book not found". The route is a plain
`GET` and returns a `302` to the selected book.

## Tests

`tests/test_routes.py` covers `/book/random`; the behaviour suite covers the
metadata-render change.

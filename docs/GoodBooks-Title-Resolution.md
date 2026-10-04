# Title resolution in GoodBooks — why aggregation fragments

Investigation only. **Nothing was changed or deleted.** 2026-10-04.

## The finding in one line

There are **two different keys in play for the same book** — the filename stem
and the stored title — and they disagree for **19% of the library**, in both
directions. (An earlier draft of this note claimed titles were *always* the
filename; the data says otherwise and the note is corrected below.)

## The chain, traced

`build_library_entries()` sets every card's title with:

```
title = display_title(meta.get("title") or path.stem)
```

and `display_title()` says so itself:

> The stored title is the raw scraped filename, e.g.
> `"Clementine  Clementine Series, Book 1-Sara Pennypacker; Marla Frazee"`.
> A previous attempt to clean that up in bulk destroyed real titles
> (`"Amy and the Missing Puppy"` → `"Amy and the"`), so the only transform
> applied here is one that cannot lose content.

So the clean-up was already tried and **broke real data**, which is why the
function is deliberately near-identity today. That history is the reason the
data is inconsistent, not an oversight.

Meanwhile filenames on disk are `<Title>-<Author>.<ext>`, so the *filename*
half has no author and the *metadata* half does.

## Measured (verified against the data, not read off the docstring)

Comparing `metadata["title"]` to the file's stem for all 4,837 resolvable
records:

```
identical                   3892  (80.5%)
equal after normalisation    16  ( 0.3%)
genuinely different          929  (19.2%)
```

So **8 in 10 titles ARE the filename stem**, author suffix and all. The 929
that differ are *better*: they came from a real scrape, with the author
suffix removed and case normalised.

```
file: 'Amy and the Missing Puppy-Callie Barkley'
meta: 'Amy and the missing puppy'

file: 'In the Fast Lane-Anderson, Evie'
meta: 'In the Fast Lane'

file: 'Joan Lowery Nixon The Dark and Deadly Pool-Joan Lowery Nixon'
meta: 'The Dark and Deadly Pool'          <- author removed from the FRONT too
```

That last one is important: the filename convention is `<Title>-<Author>`,
but real filenames often carry the author *before* the title as well, so a
filename-derived title is not reliably clean.

The 19% that differ are the well-behaved subset. The other 80.5% have the
catalogue blob glued on, which is what `display_title()`'s docstring means by
"the raw scraped filename".

Separately, across all 4,869 records:

```
distinct normalised titles  4,768
titles held by >1 record    97 clusters, 198 records
  ...spanning MULTIPLE formats   14 clusters
titles over 100 chars      908
titles over 12 words       1202
empty titles               0
```

## Why an aggregator fragments

The key problem: **title quality is bimodal**. 80.5% of titles carry the
author suffix glued on; 19.2% are clean scrapes. Any grouping rule has to cope
with both forms of the same book.

The 14 multi-format clusters are the same book acquired twice:

```
'big little lies'    .mobi 1.1MB  title='Big Little Lies'         author='liane; moriarty'
                     .azw 1.0MB  title='Big Little Lies'         author='liane; moriarty'

'local woman missing' .epub 0.8MB  title='Local Woman Missing'    author='mary; kubica'
                     .azw3 0.9MB  title='Local Woman Missing'    author='mary; kubica'

'stephen king'       .mobi 2.5MB  title='Stephen King'           author='stephen; king'
                     .epub 0.5MB  title='Stephen King'           author='stephen; king'

'the stand stephen king' .epub/.mobi/.mobi   x3
'a story of yesterday'   .mobi/.epub/.mobi   x3
```

Note `'in the woods'` is a genuine collision, not a duplicate:
`.epub` is **Robin Stevenson**, `.azw3` is **Tana French**. Any
title-only key merges two different books. That is the failure mode in the
other direction, and it is the reason a title-only key is not safe even as a
dedup signal.

And because 80.5% of titles embed the author blob, even a *title-only* key
fails on the majority: `'big little lies'` stores as
`'Big Little Lies-Liane; Moriarty'` in one record and `'Big Little Lies'` in
another, so the two would not group at all without stripping the suffix.

So two opposite errors are live at once:
* **split** — one book under epub *and* azw3, matched by filename but not by title;
* **merge** — two different books sharing a title, matched by title but not by
  author.

Both come from indexing on a single field that is not a stable identity.

## Why the current fields are unsafe as keys

| Field | Problem |
|---|---|
| `title` | 80.5% is the raw filename stem **including** the author suffix; 19.2% is a proper scrape. So it is inconsistent by construction: 908 titles exceed 100 chars, 1,202 exceed 12 words |
| `author` | raw catalogue blob — `sergio; cobo`, `avi, brian floca`, `robin; stevenson` |
| filename | title and author are only separable by guessing which hyphen is the split point |
| `id` | `<root>::<relpath>` — exact, but unique per *file*, so it can never group formats of one book |

`parse_authors()` already normalises the author side properly (`west, tracey`
→ `Tracey West`, `jeff; smith` → `Jeff Smith`, multi-author splits on
semicolons). That machinery exists and is used by the author filter — the
problem is that nothing applies the equivalent to titles, and nothing
*combines* normalised title + normalised author into a book identity.

## What an aggregator can safely use today

Without changing anything, a reliable identity key is:

```
norm(title with the trailing "-<author blob>" removed)  +  "|"  +  norm(parse_authors(author))
```

which distinguishes the two `in the woods` books by author and collapses the
epub/azw3 pairs of `Big Little Lies`.

That is a **derivation over existing data**, not a migration — but it is
written up here rather than applied, because:
1. it would need to agree with whatever you want the UI to display, and
2. the previous bulk title fix is exactly the kind of change that should be
   reviewed before it runs against 4,869 records.

## Recommendation, and what I deliberately did not do

**Nothing was changed.** Specifically not done:

* no titles rewritten — the last bulk rewrite destroyed real titles;
* no records deduplicated or deleted;
* no `id` scheme changed, which would break every job file and every reference
  to the library;
* the 32 metadata keys that resolve to no file were left alone.

Suggested next steps, in order of increasing risk:

1. **Add a derived `book_key` field** — computed, never authoritative, ignored
   by the UI. Zero risk: nothing existing reads it, and an aggregator can use
   it immediately. Reversible by ignoring it.
2. **Surface the 14 multi-format clusters as a report**, not a change. Lets
   you see which are true duplicates (`Big Little Lies`) and which are
   collisions (`In the woods` — two different books) before anything acts.
3. Only then consider a display-level title cleanup, with a dry-run diff of
   every proposed change, because step 3 is where the earlier damage happened.

## Related

- [[GoodBooks-DAS-Notebook]] — service state, plugin format, measured latency
- 32 metadata keys resolve to no file; also untouched, and also needing a
  decision

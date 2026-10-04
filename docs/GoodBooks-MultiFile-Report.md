---
tags: [goodbooks, library, book-key, duplicate-audit, read-only]
host: das@192.168.0.9
audited: 2026-10-04
changed: nothing
---

# Multi-file book report — 94 clusters, and what they actually are

Step 2 from [[GoodBooks-Title-Resolution]]. **Read only: nothing was moved,
renamed or deleted.**

```
4,869 records -> 4,769 book_keys -> 94 clusters holding >1 file
                          (192 files, 98 redundant)
```

## The headline

**71 of the 94 clusters are byte-identical files.** They are the same book
downloaded twice into two overlapping curated folders. That is a library
*layout* fact, not data corruption — and `book_key` groups them correctly
regardless.

Byte comparison (SHA-256 of each file):

```
byte-IDENTICAL files      : 71
two files, differing bytes: 21
3+ files, mixed           :  2
```

## Where the duplicates come from

The library is organised as overlapping reading folders, so one file
legitimately lives in several:

```
x13  ['.epub']  Best Books for Reluctant Readers <-> Our Favorite Indie Reads
x12  ['.epub']  Lorenzo <-> Lorenzo                     (same folder, 12x)
x6   ['.epub']  Our Favorite Indie Reads <-> The Best Disturbing Thrillers You Must Read
x5   ['.epub']  Best Books for Reluctant Readers <-> Mystery Thriller Most_read_this_week
x5   ['.azw3','.epub'] Middle Grade Most_read_this_week <-> sagey
x4   ['.epub']  Middle Grade Most_read_this_week <-> Mystery Thriller Most_read_this_week
x4   ['.epub']  Best Books for Reluctant Readers <-> Lorenzo
```

`Lorenzo <-> Lorenzo` appearing 12 times is the same-folder case: the same
title acquired into one folder twice under different filenames.

## A correction I made to my own classification

The first pass split clusters by "same format / different author" and then
used **byte size** to call them duplicates. That heuristic is wrong across
formats:

```
The Stand   .epub 3.70 MB   vs   .mobi 9.88 MB
```

epub is a zip of many small files and compresses; mobi/azw are single-file
containers that store text largely raw. Those are the same novel in two
encodings, and the report had flagged them as "likely two different books".

Size is only meaningful **within one format**, and hashing is definitive in
both cases. `gb_bookkey_risky2.py` now refuses to draw a size conclusion
across formats.

Separately, the clusters that looked like "different author" are the *same*
author parsed differently from the raw blob — both files carried
`dc:title='American Psycho'` and `bret; easton; ellis`. The split was never
between books; it was between folders.

## The four classes, and what each needs

| | count | meaning | action |
|---|---|---|---|
| **A** | 13 | one book, several formats | **nothing** — normal; book_key already groups them |
| **B** | 60 | one book, one format, downloaded twice | removable, but only after you confirm the edition |
| **C** | 2 | different author *and* different format | never merge without reading |
| **D** | 19 | same format, different author | resolves to A/B once hashed — not a separate problem |

C and D were the alarming ones and they dissolved under hashing:

```
a story of yesterday (sergio cobo)
    .epub  c749b8d9…  Our Favorite Indie Reads
    .mobi  e925a93d…  Middle Grade Most_read_this_week
    .mobi  e925a93d…  Mystery Thriller Most_read_this_week   <- identical pair
```

Two genuinely distinct files (the epub is a different edition) plus a
byte-identical mobi pair. So even inside a class-D cluster the members are not
equivalent, which is precisely why nothing was auto-removed.

## What I recommend

1. **Do nothing about A.** It is an ebook library; several formats per book is
   normal and `book_key` already groups them.
2. **B is where real space is** — 60 clusters. Worth deduplicating, but the
   safe form is *hardlink or symlink*, never a delete: the folders are
   curated views, so removing the copy in one folder changes what that folder
   shows.
3. **Leave C and D.** 2 and 19 clusters are small, and the evidence says most
   are the same book in different formats rather than two different books.

I have not acted on any of this. Deleting or linking library files is a
decision about what your folders should contain, and I have no basis for it.

## Tools

```
gb_bookkey_report.py            the 94 clusters, classified, with sizes/folders
gb_bookkey_risky2.py            classes C and D in detail, no bogus size verdicts
gb_bookkey_hash.py              byte-identity check + folder overlap
```

All read-only.

## Related

- [[GoodBooks-Title-Resolution]] — why `book_key` exists at all
- [[GoodBooks-DAS-Notebook]] — service state, plugin format, measured latency

# AUTHOR NORMALIZATION INCONSISTENCIES - QUICK REFERENCE

## Critical Problem Statement
Author field normalization is INCONSISTENTLY applied across the codebase, causing books to be treated as new even when they're already in the library. Different code paths apply different transformations to the same author data.

---

## All Normalization Methods Currently Used

| # | Function/Location | Method | Line(s) | Input Format | Output Format | Clean? |
|---|---|---|---|---|---|---|
| 1 | Library lookup construction | `re.sub(r'[;]+', ' ')` | app.py:6033 | "A; B; C" | "a b c" | Partial |
| 2 | history_manager.cleanup_author() | Smart merge + prefixes | settings_manager.py:557 | "A; B; C" | "a b" or "a bc" | YES |
| 3 | Fast path check | cleanup_author() | app.py:6236 | "A; B; C" | "a b" or "a bc" | YES |
| 4 | _clean_rss_author() | Regex split + dedup | parser_engine.py:47 | "A; B" or "A, B" | Variable | Partial |
| 5 | _deduplicate_authors() | CamelCase expand + split | parser_engine.py:218 | "AB" or "A; B" | "a; b" | NO |
| 6 | select_best_result() | tokens() word split | app.py:500 | "A; B; C" | {"a", "b", "c"} | Tokenized |
| 7 | sanitize_author() | Custom dedup | app.py:680 | Variable | Variable | CUSTOM |
| 8 | sort_library_entries() | casefold() only | app.py:2220 | "A; B; C" | "a; b; c" | NO |
| 9 | upsert_library_metadata_for_download() | _deduplicate_authors() | app.py:2867 | "A; B" | "a; b" | NO |
| 10 | ensure_library_metadata() | _deduplicate_authors() | app.py:3202 | "A; B" | "a; b" | NO |
| 11 | Query building | No cleanup (raw) | app.py:6275 | Item.author | Unchanged | NO |

---

## Most Critical Mismatches

### Mismatch A: Library Lookup vs Item Matching
**Lines: 6033 vs 6236**

```
Library scan normalization (line 6033):
  Input:  "Freida; Mc; Fadden"
  Method: re.sub(r'[;]+', ' ', x) → re.sub(r'\s+', ' ', x)
  Output: "freida mc fadden"

Item matching normalization (line 6236):
  Input:  "Freida; Mc; Fadden"
  Method: cleanup_author()
  Output: "freida mcfadden"

RESULT: MISMATCH - Item appears new even if in library
```

### Mismatch B: Metadata Storage vs Query Building
**Lines: 2867 vs 6275**

```
Metadata storage (line 2867):
  Input:  "Smith; John"
  Method: _deduplicate_authors()
  Output: "smith; john"

Query building (line 6275):
  Input:  "Smith; John"
  Method: No cleanup (raw)
  Output: "Smith; John"

RESULT: Query format different from stored format
```

### Mismatch C: Goodreads Lookup vs Stored Metadata
**Lines: 274 vs 3202**

```
Goodreads search (line 274):
  Input:  "Smith; John"
  Method: No cleanup (raw in search query)
  Output: "smith; john" (in query string)

ensure_library_metadata (line 3202):
  Input:  "Smith; John"  
  Method: _deduplicate_authors()
  Output: "smith; john" (stored with semicolon)

Might work, but cache key at line 265 doesn't normalize:
  cache_key = f"{title}|{author}".lower()
  With spaces: "title|smith john"
  With semicolons: "title|smith; john"
  RESULT: Cache misses on format variation
```

---

## Code Path Analysis: Where Authors Go Uncleaned

### Path 1: Feed Item → Search (UNCLEANED)
```
ParsedItem.author (raw from RSS)
  ↓ (maybe) _clean_rss_author() → still has ; or ,
  ↓ (maybe) _deduplicate_authors() → has ; delimiters
  ↓
process_item() line 6275: query = f"{title} {item.author}"
  → Search uses UNCLEANED author
  ↓
select_best_result(expected_author=item.author)
  → Tokenizes UNCLEANED author
  → Compares with cleaned results
  ↓
CONSEQUENCE: Author matching may fail if feed format differs from search result format
```

### Path 2: Metadata Storage (MIXED CLEANING)
```
Download complete
  ↓ upsert_library_metadata_for_download() line 2867
  ↓ _deduplicate_authors(best.author or item.author)
  → Stored as "a; b" format (with semicolons)
  ↓
library_metadata.json: {"author": "a; b"}
  ↓
build_library_entries() line 2512: author = meta.get("author", "")
  → Returns "a; b" (UNCLEANED from metadata)
  ↓
_run_feeds_background() line 6033
  ↓ re.sub(r'[;]+', ' ', author_full)
  → Library lookup: "a b" (spaces)
  ↓
CONSEQUENCE: Metadata stores one format, lookup uses different format
```

### Path 3: History Storage (CLEANED CONSISTENTLY)
```
send_library_item_to_kindle() line 1226-1227
  ↓
history_manager.record_kindle_send()
  ↓ cleanup_author() at line 705
  → Stored as cleaned format in history
  ↓
GOOD: History uses consistent cleanup_author()
BUT: Library lookup uses different normalization!
```

---

## Specific Test Cases That FAIL

### Case 1: "Mc" Prefix Handling
```
Author in library_metadata.json: "Freida; Mc; Fadden"

Library scan (line 6033):
  re.sub(r'[;]+', ' ', "freida; mc; fadden")
  = "freida mc fadden"

Item from feed: "Freida; Mc; Fadden"

Fast path check (line 6236):
  cleanup_author("Freida; Mc; Fadden")
  = "freida mcfadden" (no space before Mc)

Lookup: ("title", "freida mc fadden") IN library_lookup? YES
Fast path: ("title", "freida mcfadden") IN library_lookup? NO!

RESULT: Item will be processed as if not in library despite being there
```

### Case 2: Multiple Authors Order Variation
```
Author in library: "John Smith; Jane Doe"

Library scan:
  "john smith; jane doe" → "john smith jane doe"

Item from feed: "Jane Doe; John Smith" (different order)

Fast path:
  cleanup_author("Jane Doe; John Smith")
  = "jane doe john smith"

Lookup fails because order is different!

RESULT: DUPLICATE DOWNLOAD
```

### Case 3: Comma vs Semicolon
```
Author in library: "Smith, John"

Library scan:
  "smith, john" → NO change (doesn't match regex r'[;]')
  = "smith, john" (KEPT AS-IS)

Item from feed: "Smith; John"

Fast path:
  cleanup_author("Smith; John")
  = "smith john" (merged, no punctuation)

Lookup: "smith, john" ≠ "smith john"

RESULT: DUPLICATE DOWNLOAD
```

---

## Lines That MUST Change

| Priority | File | Lines | Change | Reason |
|----------|------|-------|--------|--------|
| CRITICAL | app.py | 6030-6038 | Replace regex with cleanup_author() | Build library_lookup consistently |
| CRITICAL | app.py | 6275 | Add cleanup_author() to query building | Normalize before searching |
| CRITICAL | app.py | 2867 | Replace _deduplicate_authors() with cleanup_author() | Consistent metadata storage |
| HIGH | app.py | 3202 | Replace _deduplicate_authors() with cleanup_author() | Consistent metadata enrichment |
| HIGH | app.py | 6364-6365 | Pre-clean author before passing to select_best_result() | Normalize before matching |
| HIGH | parser_engine.py | 274, 265 | Normalize in cache key | Prevent cache misses |
| MEDIUM | app.py | 2342-2382 | Add cleanup_author() to find_book_in_library_by_title_author() | Consistent dedup checking |
| MEDIUM | app.py | 5896-5927 | Return cleaned author from extract_title_and_author() | Normalize at extraction point |

---

## How to Verify the Bug

### Test 1: Library Lookup Mismatch
1. Add book with author "Freida; Mc; Fadden" to library
2. In settings.json, ensure library has this book
3. Run feed with same book, author format "Freida McFadden"
4. Check: Should match, but probably won't due to normalization mismatch

### Test 2: Metadata vs Query Format
1. Download book with author from Anna's Archive: "Smith; John"
2. Check library_metadata.json - stored with _deduplicate_authors() output
3. Check how it's queried next time
4. Verify query format matches stored format

### Test 3: History vs Library
1. Send item to Kindle - author gets cleanup_author()
2. Check history.json - should see cleaned author
3. Check library_metadata.json - might see different format
4. Verify inconsistency in storage

---

## Root Cause

**cleanup_author()** in settings_manager.py is the CORRECT, COMPREHENSIVE function that:
- Handles name prefixes ("Mc", "Van", etc.)
- Normalizes spacing intelligently
- Returns consistent lowercase format
- Handles "LastName; FirstName" format

**But it's only used in:**
- Line 6236 (fast path check) 
- Lines 705, 664 (history storage)

**It's NOT used in:**
- Library lookup construction (line 6033) → uses regex
- Metadata storage (line 2867) → uses _deduplicate_authors()
- Query building (line 6275) → no cleanup
- Goodreads lookup (line 265, 274) → no cleanup
- Metadata enrichment (line 3202) → uses _deduplicate_authors()

---

## Solution Strategy

1. **Standardize on cleanup_author()** - It's the most comprehensive function
2. **Replace all ad-hoc normalization** - Remove regex, _deduplicate_authors(), sanitize_author() variants
3. **Normalize at entry points** - Clean authors as they enter the system (RSS, feed items, search results)
4. **Normalize before comparison** - Clean before any library lookup or matching
5. **Test with "Mc", "Van", comma/semicolon variations** - Ensure edge cases work


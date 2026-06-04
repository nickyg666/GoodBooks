# COMPREHENSIVE AUTHOR FIELD USAGE ANALYSIS - GoodBooks

## CRITICAL INCONSISTENCY MAP

Author field handling is INCONSISTENTLY normalized across the codebase, creating potential for library mismatches and data integrity issues.

---

## 1. LIBRARY LOOKUP OPERATIONS (SEVERITY: CRITICAL)

### 1.1 Library Lookup Cache Construction - HARD CODED SEMICOLON HANDLING
**File**: `app.py`, Lines 6019-6042  
**Function**: `_run_feeds_background()`

```python
6019:     library_lookup = set()
6020:     
6021:     for entry in library_entries:
6022:         raw_title = entry.get("title") or ""
6023:         
6024:         # Extract title and author from filename format "Title-Author"
6025:         clean_title, author_from_title, full_author = extract_title_and_author(raw_title)
6026:         
6027:         # Fall back to metadata author if available
6028:         author_full = (entry.get("author") or "").lower().strip()
6029:         
6030:         # Normalize author: handle both "A; B; C" and "A B C" formats
6031:         if author_full:
6032:             # Replace semicolons with spaces and collapse multiple spaces
6033:             author_norm = re.sub(r'[;]+', ' ', author_full)
6034:             author_norm = re.sub(r'\s+', ' ', author_norm).strip()
6035:         else:
6036:             # Use author from filename, also normalize to remove semicolons
6037:             full_author = re.sub(r'[;]+', ' ', full_author) if full_author else ""
6038:             author_norm = re.sub(r'\s+', ' ', full_author).strip()
6039:         
6040:         # Add matches if we have both title and author
6041:         if clean_title and author_norm:
6042:             library_lookup.add((clean_title, author_norm))
```

**ISSUE**: Uses **hardcoded regex** `re.sub(r'[;]+', ' ', ...)` to convert semicolons to spaces.  
**MISMATCH**: This does NOT use `history_manager.cleanup_author()` function.  
**CONSEQUENCE**: Author format in library_lookup is DIFFERENT from normalized format used in item matching.

---

### 1.2 Query Building - USES ITEM AUTHOR DIRECTLY
**File**: `app.py`, Line 6275  
**Function**: `process_item()`

```python
6275:         query = f"{item.title} {item.author}".strip()
```

**ISSUE**: Uses `item.author` directly from feed without cleanup.  
**MISMATCH**: This creates a query with potentially non-normalized author (semicolons intact).  
**CONSEQUENCE**: If feed has "A; B; C" format, query won't match library entry with "A B C" format.

---

### 1.3 Fast Path Library Lookup - INCONSISTENT NORMALIZATION
**File**: `app.py`, Lines 6227-6244

```python
6227:         # FAST PATH: Check library lookup first (O(1) operation)
6228:         raw_title = (item.title or "").lower().strip()
6229:         raw_author = (item.author or "").lower().strip()
6230:          
6231:         # Clean title: remove parenthetical info like series numbers
6232:         import re
6233:         title_norm = re.sub(r'\s*\([^)]*\)\s*', ' ', raw_title).strip()
6234:          
6235:         # Normalize author using cleanup_author() function
6236:         author_norm = history_manager.cleanup_author(item.author or "")
6237:          
6238:         lookup_key = (title_norm, author_norm)
6239:         if lookup_key in library_lookup:
6240:             logger.debug("Item already in library (fast path): title=%s author=%s", item.title, item.author)
6241:             lookup_key in library_lookup:
```

**GOOD PRACTICE**: Line 6236 uses `history_manager.cleanup_author()`.  
**CRITICAL MISMATCH**: But `library_lookup` was built (lines 6030-6038) using DIFFERENT normalization (hardcoded regex).  

**Issue**: `cleanup_author()` at line 6236 LOWERS case, while library_lookup line 6033 also lowers case BUT:
- `cleanup_author()` handles "Mc", "Mac", "von", etc. differently
- `cleanup_author()` converts 2+ semicolons to proper author merging
- Simple regex at line 6033 just replaces semicolons with spaces

---

### 1.4 Global Cache Update - PERSISTENCE POINT
**File**: `app.py`, Lines 6101-6102

```python
6101:         _LIBRARY_LOOKUP_CACHE.clear()
6102:         _LIBRARY_LOOKUP_CACHE.update(library_lookup)
```

**ISSUE**: Updates global cache with inconsistently-normalized entries from `library_lookup` (lines 6019-6042).

---

### 1.5 Global Cache Initialization - DUPLICATE DECLARATION
**File**: `app.py`, Lines 59 and 1389  
**ISSUE**: `_LIBRARY_LOOKUP_CACHE` declared TWICE:
```python
59:   _LIBRARY_LOOKUP_CACHE = set()  # Global cache of (title, author) tuples already in library

1389: _LIBRARY_LOOKUP_CACHE = set()  # Global cache of (title, author) tuples already in library
```

This is a code duplication issue suggesting copy-paste error.

---

## 2. BOOK MATCHING & DEDUPLICATION (SEVERITY: HIGH)

### 2.1 find_book_in_library_by_title_author() - NO CLEANUP
**File**: `app.py`, Lines 2342-2382

```python
2342: def find_book_in_library_by_title_author(title: str, author: str = "") -> Optional[Dict[str, Any]]:
2358:         # Normalize the search title/author for comparison
2359:         search_title = title.strip().lower()
2360:         search_author = author.strip().lower() if author else ""
2361:         
2362:         entries = build_library_entries()
2363:         for entry in entries:
2364:             # Normalize library entry title/author
2365:             lib_title = entry.get("title", "").strip().lower()
2366:             lib_author = entry.get("author", "").strip().lower()
2367:             
2368:             # Match on title and optionally author
2369:             if lib_title == search_title:
2370:                 # If author provided, it should match; if not provided, title match is enough
2371:                 if not search_author or lib_author == search_author:
```

**ISSUE**: Uses simple `.lower().strip()` but NO `cleanup_author()` call.  
**CONSEQUENCE**: Won't match "Freida McFadden" vs "freida; mc; fadden".

---

### 2.2 Find Duplicate by MD5 - RETURNS RAW AUTHOR
**File**: `app.py`, Lines 2293-2340

```python
2328:                     return {
2329:                         "id": entry.get("id"),
2330:                         "title": entry.get("title"),
2331:                         "author": entry.get("author"),  # <-- RAW, UNCLEANED
2332:                         "path": str(lib_path),
2333:                     }
```

**ISSUE**: Returns raw author from library_metadata without cleanup.

---

## 3. AUTHOR FIELD EXTRACTION FROM FILES (SEVERITY: HIGH)

### 3.1 extract_title_and_author() - SPLITS ON SEMICOLON
**File**: `app.py`, Lines 5896-5927

```python
5896: def extract_title_and_author(dirty_title: str) -> tuple:
5897:     """Extract clean title and author from filename-style title.
5898:     Examples:
5899:       'Hide ( D.D Warren #2 )-Gardner; Lisa.epub' -> ('hide', 'gardner; lisa')
5900:       'The Gift-Danielle Steel.mobi' -> ('the gift', 'danielle steel')
5901:     """
5924:     # Extract first author (before semicolon) for matching
5925:     first_author = author.split(";")[0].strip() if author else ""
5926:     
5927:     return clean_title, first_author, author
```

**ISSUE**: Returns BOTH `first_author` (before semicolon) AND `full_author` (with semicolon).  
**INCONSISTENCY**: Used at line 6025 to extract, then processed with regex at line 6037 differently.  
**CONSEQUENCE**: Author format from filenames goes through multiple normalization steps:
1. Extracted with `extract_title_and_author()` (splits on ";")
2. Processed with regex `re.sub(r'[;]+', ' ',...)` at line 6037
3. But ALSO later normalized with `cleanup_author()` at line 6236

---

### 3.2 Filename Author Extraction in Feed Processing - HARDCODED SPLIT
**File**: `app.py`, Line 6869

```python
6869:                         file_title, file_author = stem.rsplit('-', 1)
```

**ISSUE**: Naively splits filename on last dash WITHOUT considering author format normalization.  
**CONTEXT**: Used for stem matching in library.

---

## 4. HISTORY MANAGER OPERATIONS (SEVERITY: HIGH)

### 4.1 cleanup_author() - DIFFERENT NORMALIZATION
**File**: `settings_manager.py`, Lines 557-617

```python
557:  def cleanup_author(self, author: str) -> str:
558:      """
559:      Clean up author names by removing unnecessary semicolons and fixing spacing.
560:      Examples:
561:          "Freida; Mc; Fadden" -> "Freida McFadden"
562:          "Amanda; Brittany" -> "Amanda Brittany"
563:          "Adams; Douglas" -> "Adams Douglas"
564:          "A.A.; Milne" -> "A.A. Milne"
565:      """
566:      ...
573:      # Split by semicolons to get parts
574:      parts = [p.strip() for p in author.split(';')]
575:      ...
603:      # Check if previous part is a name prefix that shouldn't have space
604:      if prev_part.lower() in ('mc', 'mac', "o'", 'de', 'van', 'von', 'des', 'du', 'da', 'le', 'la'):
605:          # No space before this part
606:          result += curr_part
607:      else:
608:          # Normal space
609:          result += ' ' + curr_part
610:      
611:      return result.lower().strip()
```

**KEY FEATURES**:
- Handles "Mc", "Mac", "Van", etc. name prefixes specially
- Lowers case
- Removes periods intelligently
- Returns lowercase

**VS. Library lookup regex (line 6033)**:
- Just replaces semicolons with spaces
- Doesn't handle name prefixes
- Also lowers case (similar)

**MISMATCH**: `"Freida; Mc; Fadden"` becomes:
- Via `cleanup_author()`: `"freida mcfadden"` (no space before Mc)
- Via `re.sub()`: `"freida mc fadden"` (spaces everywhere)

---

### 4.2 kindle_sent() - USES cleanup_author()
**File**: `settings_manager.py`, Lines 544-555

```python
544:  def kindle_sent(self, user: str, title: str, author: str) -> bool:
546:      author_norm = self.cleanup_author(author)
547:      return any(
548:          entry.get("user") == user and 
549:          entry.get("title") == title and
550:          self.cleanup_author(entry.get("author", "")) == author_norm
551:      )
```

**GOOD**: Uses `cleanup_author()` consistently.  
**ISSUE**: But if library_lookup was built with different normalization, this check might fail.

---

### 4.3 record() - CALLS cleanup_author()
**File**: `settings_manager.py`, Lines 619-696

```python
663:      # Clean up author name to remove unnecessary semicolons
664:      cleaned_author = self.cleanup_author(author)
665:      entry = {
666:          ...
667:          "author": cleaned_author,
```

**GOOD**: Uses `cleanup_author()` before storing.

---

### 4.4 record_kindle_send() - USES cleanup_author()
**File**: `settings_manager.py`, Lines 698-722

```python
704:      cleaned_author = self.cleanup_author(author)
705:      for entry in entries:
706:          if (...and self.cleanup_author(entry.get("author", "")) == cleaned_author):
```

**GOOD**: Consistent use of `cleanup_author()`.

---

## 5. FEED ITEM PROCESSING (SEVERITY: MEDIUM)

### 5.1 Parser _clean_rss_author() - SEMICOLON HANDLING
**File**: `parser_engine.py`, Lines 47-90

```python
47:  def _clean_rss_author(author: str) -> str:
48:      """Extract just author name, removing duplicates, publisher, ISBN, and extra metadata."""
59:      # Check if this is "LastName; FirstName" format (single author, 1 semicolon)
61:      semicolon_count = author.count(';')
62:      if semicolon_count == 1:
63:          parts_on_semi = author.split(';')
67:              # This is "Last; First" format, keep it as one author
68:              return f"{first} {last}"
69:      
70:      # Split on semicolon or comma to get individual names
71:      parts = re.split(r'[,;]', author)
```

**ISSUE**: Handles semicolons but returns potentially MULTI-AUTHOR strings like "Author1, Author2".  
**CONSEQUENCE**: Feed item authors might have semicolons or commas, but library_lookup expects spaces.

---

### 5.2 Parser _deduplicate_authors() - DIFFERENT NORMALIZATION
**File**: `parser_engine.py`, Lines 218-255

```python
218:  def _deduplicate_authors(self, author_str: str) -> str:
230:      # Expand CamelCase/NumberCase splits
231:      expanded = re.sub(r'([a-z0-9]{2,})([A-Z][a-z])', r'\1 \2', author_str)
232:      expanded = re.sub(r'([a-z]{2,})([A-Z])', r'\1 \2', expanded)
233:      
234:      # Step 2: Split by separators with word boundaries
235:      parts = re.split(r'[;&\[\]]|\band\b', expanded)
237:      ...
250:      seen_authors.add(author_normalized)
251:      unique.append(part)
252:      
253:      return "; ".join(unique)
```

**ISSUE**: Splits on `[;&\[\]]` (semicolon, ampersand, brackets) but REJOINS with `"; "` (semicolon+space).  
**CONSEQUENCE**: Feed authors go through THIS deduplication, not `cleanup_author()`.  
**RESULT**: Feed items might have format like "Author1; Author2" which doesn't match library "Author1 Author2".

---

### 5.3 _resolve_goodreads_url() - NO AUTHOR CLEANUP
**File**: `parser_engine.py`, Lines 257-274

```python
257:  def _resolve_goodreads_url(self, title: str, author: str) -> str:
265:      cache_key = f"{title}|{author}".lower()
274:      search_query = f"{title} {author}".strip()
```

**ISSUE**: Uses raw author string in cache key and search query without cleanup.  
**CONSEQUENCE**: Cache misses if same book comes with different author formats.

---

## 6. LIBRARY METADATA STORAGE (SEVERITY: MEDIUM)

### 6.1 upsert_library_metadata_for_download() - USES _deduplicate_authors()
**File**: `app.py`, Lines 2814-2951

```python
2860:      author = best.get("author") or getattr(item, "author", "") or ""
2861:      
2862:      # Deduplicate author names if they were concatenated
2863:      if author:
2864:          from parser_engine import FeedParser
2865:          from pathlib import Path as PathlibPath
2866:          temp_parser = FeedParser(PathlibPath.home() / ".feed_metadata")
2867:          author = temp_parser._deduplicate_authors(author)
```

**ISSUE**: Uses `_deduplicate_authors()` (from parser_engine) instead of `history_manager.cleanup_author()`.  
**MISMATCH**: These two functions normalize differently!
- `_deduplicate_authors()`: splits on `[;&\[\]]`, rejoins with `"; "`
- `cleanup_author()`: splits on `;`, handles name prefixes, returns lowercase

---

### 6.2 build_library_entries() - RETURNS RAW AUTHOR
**File**: `app.py`, Lines 2463-2545

```python
2510:      meta = metadata.get(key, {})
2511:      title = meta.get("title") or path.stem
2512:      author = meta.get("author", "")
...
2529:          "author": author,
```

**ISSUE**: Returns raw author from metadata without normalizing.  
**CONSEQUENCE**: Library entries have whatever author format was stored in metadata.json.

---

## 7. SEARCH & MATCHING OPERATIONS (SEVERITY: HIGH)

### 7.1 select_best_result() - TOKENIZES AUTHOR
**File**: `app.py`, Lines 443-602

```python
500:  expected_author_tokens = tokens(expected_author) if expected_author else set()
501:  
502:  def tokens(text: str) -> set[str]:
503:      if not text:
504:          return set()
505:      text = text.lower()
506:      text = re.sub(r"[^a-z0-9]+", " ", text)
507:      return {t for t in text.split() if t}
508:  
521:      atoks = tokens(result.get("author") or "")
522:      ...
555:      atoks = tokens(result.get("author") or "")
```

**APPROACH**: Tokenizes author (splits into words, removes punctuation) for fuzzy matching.  
**ISSUE**: This is a THIRD different author normalization method!  
**CONSEQUENCE**: Author from search results tokenized differently than library lookup normalization.

**Examples**:
- `"Freida; Mc; Fadden"` tokens: `{"freida", "mc", "fadden"}`
- Library lookup: `"freida mc fadden"` (string comparison)
- cleanup_author(): `"freida mcfadden"` (merged)

---

### 7.2 process_item() - USES expected_author IN select_best_result()
**File**: `app.py`, Lines 6360-6366

```python
6360:      best = select_best_result(
6361:          results,
6362:          feed.filetypes,
6363:          user.kindle_type,
6364:          expected_title=item.title,
6365:          expected_author=item.author,
6366:      )
```

**ISSUE**: Passes `item.author` directly (potentially with semicolons/commas) to select_best_result().  
**CONSEQUENCE**: Author tokenization in select_best_result() operates on feed author format, not normalized.

---

### 7.3 sanitize_author() - DEDUPLICATES BUT DOESN'T USE cleanup_author()
**File**: `app.py`, Lines 680-741

```python
680:  def sanitize_author(author_string: str) -> str:
698:      for sep in [" & ", " and ", "; ", ","]:
699:          if sep in author_string:
700:              delimiter = sep
701:              parts = [p.strip() for p in author_string.split(sep) if p.strip()]
```

**ISSUE**: Custom deduplication logic instead of using `history_manager.cleanup_author()`.  
**CONSEQUENCE**: Another inconsistent author normalization path at line 6314.

---

## 8. SORTING OPERATIONS (SEVERITY: LOW)

### 8.1 sort_library_entries() - USES _normalize_sort_key()
**File**: `app.py`, Lines 2546-2590

```python
2571:  if sort_key == "author_az":
2572:      return sorted(
2573:          entries,
2574:          key=lambda e: (
2575:              _normalize_sort_key(e.get("author", "")),
2576:              _normalize_sort_key(e.get("title", "")),
2577:          ),
2578:      )
```

**FUNCTION**: Line 2220:
```python
2220: def _normalize_sort_key(value: str) -> str:
2221:     return (value or "").casefold()
```

**ISSUE**: Uses simple `casefold()` for sorting, doesn't normalize author format.  
**CONSEQUENCE**: Sorting by author might not be alphabetically intuitive for "A; B" vs "A B" formats.

---

## 9. METADATA ENRICHMENT (SEVERITY: MEDIUM)

### 9.1 ensure_library_metadata() - USES _deduplicate_authors()
**File**: `app.py`, Lines 3162-3433

```python
3196:      author = entry.get('author', '')
3197:      # Deduplicate author if needed
3198:      if author:
3199:          from parser_engine import FeedParser
3200:          from pathlib import Path as PathlibPath
3201:          temp_parser = FeedParser(PathlibPath.home() / ".feed_metadata")
3202:          author = temp_parser._deduplicate_authors(author)
```

**ISSUE**: Uses `_deduplicate_authors()` instead of `cleanup_author()`.

---

### 9.2 enrich_library_metadata_from_goodreads() - SIMILARLY USES _deduplicate_authors()
**File**: `app.py`, Lines 2954-3159

```python
2992:      author = (entry.get("author") or "").strip()
```

**ISSUE**: No cleanup call at all.

---

## 10. DATA PIPELINE ISSUES (SEVERITY: CRITICAL)

### Flow 1: Feed Item → Library (Inconsistent Cleanup)
```
Feed Parser
  ↓ _clean_rss_author() → "A; B" or "A, B" format
  ↓ (optional) _deduplicate_authors() → "A; B" format
  ↓ item.author with potential semicolons/commas
  ↓
Feed Item Processor
  ↓ NO cleanup_author() call before matching
  ↓ search_with_cache(f"{item.title} {item.author}") - RAW AUTHOR
  ↓
select_best_result(expected_author=item.author) - TOKENIZED, not cleaned
  ↓
Process Item
  ↓ upsert_library_metadata_for_download()
  ↓ Uses _deduplicate_authors() (NOT cleanup_author())
  ↓
library_metadata.json STORES with semicolon format
  ↓
build_library_entries() RETURNS raw author
  ↓ [NOW INCONSISTENT WITH CLEANUP_AUTHOR()]
```

### Flow 2: Library Scan → Deduplication Check (Inconsistent Lookup)
```
Library Entries
  ↓ extract_title_and_author() - splits on "-", returns author with semicolons
  ↓ re.sub(r'[;]+', ' ') - HARDCODED regex, not cleanup_author()
  ↓
library_lookup SET with space-separated authors
  ↓
Global _LIBRARY_LOOKUP_CACHE updated
  ↓
Fast path check at line 6236
  ↓ cleanup_author() applied to item.author
  ↓ MISMATCH: cleanup_author() != regex normalization
```

---

## SUMMARY OF INCONSISTENCIES

| Location | Normalization Method | Format | Clean? |
|----------|---------------------|--------|--------|
| Library lookup cache (line 6033) | `re.sub(r'[;]+', ' ',...)` | "A B C" | NO |
| cleanup_author() | Smart merge, name prefixes | "A McB" or "A B" | YES |
| Fast path check (line 6236) | cleanup_author() | Lowercase merged | YES |
| Feed _deduplicate_authors() | `re.split(r'[;&\[\]]')` rejoin with `;` | "A; B" | NO |
| select_best_result() | tokens() - word split | Token set | TOKENIZED |
| sanitize_author() | Custom dedup | Variable | CUSTOM |
| History record() | cleanup_author() | Lowercase merged | YES |
| sort_library_entries() | casefold() only | "A; B" or "A B" | NO |

---

## SPECIFIC MISMATCHES

### Match 1: "Freida McFadden" vs "Freida; Mc; Fadden"
```
Library entry: "Freida; Mc; Fadden" (stored as-is in metadata)
  ↓ extract_title_and_author() at line 6025
  ↓ Library scan regex at line 6033: "freida mc fadden"
  ↓
Feed item: "Freida; Mc; Fadden"
  ↓ cleanup_author() at line 6236: "freida mcfadden"
  ↓
MISMATCH: "freida mc fadden" ≠ "freida mcfadden"
RESULT: Item appears to be new, downloaded again
```

### Match 2: Multiple Authors
```
Library entry: "John Smith, Jane Doe"
  ↓ Extract, split on comma: "john smith, jane doe"
  ↓ Regex replace: "john smith  jane doe" (double space)
  ↓ Collapse: "john smith jane doe"
  ↓
Feed item: "Smith; John; Doe; Jane"
  ↓ cleanup_author(): "smith john doe jane"
  ↓
MISMATCH on order: "john smith jane doe" ≠ "smith john doe jane"
RESULT: Different authors detected
```

---

## RECOMMENDATIONS

1. **Use cleanup_author() everywhere** - Replace all ad-hoc normalization with `history_manager.cleanup_author()`
2. **Fix library_lookup construction** - Line 6033 should use cleanup_author()
3. **Remove _deduplicate_authors() duplication** - Consolidate to cleanup_author()
4. **Update select_best_result()** - Pass pre-cleaned author, not raw
5. **Add cleanup to extract_title_and_author()** - Return normalized authors
6. **Consistent storage** - Always store cleaned authors in metadata
7. **Test author format variations** - Add regression tests for semicolon, comma, mixed formats


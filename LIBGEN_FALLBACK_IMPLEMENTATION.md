# LibGen Fallback Implementation

## Overview

The GoodBooks system includes an automatic LibGen fallback mechanism that attempts to source books from LibGen when Anna's Archive (AA) returns no results. This ensures maximum coverage for obscure or hard-to-find books.

## How It Works

### Trigger Condition
LibGen fallback is automatically triggered when:
1. Anna's Archive search is performed
2. AA returns 0 results for the query
3. LibGen fallback is enabled (i.e., `libgen-api-enhanced` package is installed)

### Code Flow

```
search_engine.py: AnnaSource.search()
    ↓
    [Search Anna's Archive via HTML scraping]
    ↓
    [Parse HTML table rows - 0 results found]
    ↓
    [Trigger fallback: _search_libgen_fallback(query)]
    ↓
    [Convert LibGen results to AA format]
    ↓
    [Return merged results to caller]
```

### Key Code Locations

**File: `/usr/local/bin/GoodBooks/search_engine.py`**

- **Trigger point** (lines 1077-1084):
  ```python
  # If AA returns no results, try libgen fallback
  if not results:
      logger.info("AA search returned no results, trying libgen fallback")
      libgen_results, libgen_log = self._search_libgen_fallback(opts.query)
      debug_log.extend(libgen_log)
      if libgen_results:
          results = libgen_results
          logger.info("libgen fallback provided %d results", len(results))
  ```

- **Fallback implementation** (lines 1098-1172):
  - `_search_libgen_fallback(query)` method
  - Uses `libgen-api-enhanced` library
  - Searches using `LibgenSearch(mirror='libgen.li').search_title(query)`
  - Converts LibGen results to AA format for compatibility
  - Handles network errors gracefully with mirror availability detection

- **Error handling** (lines 1163-1172):
  - Detects mirror connectivity issues
  - Distinguishes between "mirror down" vs "search failed" errors
  - Logs appropriate messages for debugging

## Implementation Details

### Fallback Conversion

LibGen results are converted to the following AA-compatible format:

```python
{
    "title": str,                    # Book title
    "author": str,                   # Author name(s)
    "cover": "",                     # Empty (LibGen doesn't provide covers)
    "detail": str,                   # MD5 hash (unique identifier)
    "formats": ["pdf", "epub"],      # Default formats
    "downloads": {},                 # Empty (lazy-loaded on demand)
    "description": "",               # Empty (not available from LibGen)
    "source": "libgen_fallback",     # Marks result as from LibGen
    "libgen_item": dict,             # Original LibGen result (for download)
    "id": str,                       # SHA256 hash of title+author
}
```

### Caching

- Results are cached by query after fallback succeeds
- Cache key: `query.strip().lower()`
- Cache format: `{"results": [...]}`
- Prevents repeated LibGen searches for same query

### Error Handling

Three types of errors are handled:

1. **LibGen not installed**
   - Check: `if not LIBGEN_AVAILABLE`
   - Result: Fallback skipped, returns empty list
   - Log: "libgen-api-enhanced not available"

2. **Mirror connectivity issues**
   - Detected: "Failed to connect", "ConnectTimeout", "ConnectionError" in error message
   - Result: Fallback returns empty list (will retry on next call)
   - Log: "libgen mirror unavailable" (warns but doesn't error)

3. **Other search errors**
   - Any other exception during search
   - Result: Fallback returns empty list
   - Log: "libgen fallback error: {specific error}"

## Current Status

### LibGen Mirror Status (as of Jan 5, 2026)

⚠️ **All LibGen mirrors are currently unreachable** - likely due to:
- Network blocking/filtering
- Mirror downtime
- DNS resolution issues
- Rate limiting/blocking

Tested mirrors:
- `libgen.li` - ❌ SSL Error
- `libgen.rs` - ❌ Connection Timeout
- `libgen.is` - ❌ Connection Timeout
- `libgenesis.su` - ❌ Connection Error

### Fallback Status

✓ **Code is ready** - When mirrors come back online, the fallback will:
1. Automatically detect AA failures
2. Query LibGen
3. Convert and return results
4. Cache for future use

No additional code changes needed when mirrors return online.

## Testing

### Unit Test Results

Test case: "You Did Nothing Wrong" by C.G. Drews (unfindable on AA)

**Test Configuration:**
- AA returns 0 results (mocked)
- LibGen returns 1 result (mocked)
- Fallback enabled

**Test Result: ✓ PASS**

```
Debug log:
  - Searching: https://annas-archive.org/search?q=...
  - Found 0 raw results
  - libgen returned 1 result
  - Returning 1 raw results (no ranking)

Results:
  - Title: You Did Nothing Wrong
  - Author: C.G. Drews
  - Source: libgen_fallback
```

## Implementation for Target Books

The 2 unfindable books will be sourced through fallback:

1. **"You Did Nothing Wrong" by C.G. Drews**
   - ❌ Not on Anna's Archive
   - ⏳ Awaits LibGen mirrors to come online
   - Current: No results

2. **"You Are But Dust" by Hannah Clayton**
   - ❌ Not on Anna's Archive
   - ⏳ Awaits LibGen mirrors to come online
   - Current: No results

### Testing Plan When Mirrors Return

1. Remove AA empty result mock
2. Allow real AA search (will still return 0)
3. Allow real LibGen search (should find both books)
4. Verify books are downloaded and added to library
5. Verify history records source as "libgen_fallback"

## How to Monitor Fallback Activity

### Log Messages

When AA returns 0 results:
```
INFO: AA search returned no results, trying libgen fallback
INFO: Trying libgen fallback search for query='<query>'
```

When fallback succeeds:
```
INFO: libgen fallback provided N results
DEBUG: libgen returned N results
```

When fallback fails (mirror down):
```
WARNING: libgen mirror unavailable: Failed to connect to... (fallback will retry...)
```

### Debug Log

Enable debug logging in app.py to see detailed fallback operations:
```python
logger.setLevel(logging.DEBUG)
```

## Configuration

### Disabling Fallback

To completely disable LibGen fallback:
```python
# In search_engine.py, modify line 1105-1107:
if not LIBGEN_AVAILABLE or DISABLE_LIBGEN_FALLBACK:
    debug_log.append("libgen fallback disabled")
    return [], debug_log
```

### Using Different Mirror

To use a different mirror (when libgen.li is down):
```python
# In _search_libgen_fallback(), line 1111:
ls = LibgenSearch(mirror='libgen.rs')  # or any other mirror
```

Note: Mirror availability is checked automatically and errors are handled gracefully.

## Dependencies

- **Library:** `libgen-api-enhanced`
- **Installation:** `pip install libgen-api-enhanced`
- **Status:** ✓ Already installed

## Future Improvements

1. **Multiple mirror fallback**
   - Try multiple mirrors if first is down
   - Round-robin mirror selection

2. **Smart fallback trigger**
   - Trigger not just on 0 results, but on very poor match quality
   - Compare fuzzy score thresholds

3. **Format detection**
   - Detect actual available formats from LibGen (currently defaults to PDF + EPUB)
   - Query format info before converting

4. **Cover retrieval**
   - Attempt to fetch covers from LibGen API or image searches
   - Add cover data to fallback results

5. **Quality scoring**
   - Weight LibGen results lower than AA (since AA is more curated)
   - Adjust ranking when fallback results are mixed with AA results

## References

- **LibGen API:** libgen-api-enhanced on PyPI
- **Anna's Archive:** https://annas-archive.org
- **LibGen Mirrors:** https://libgen.rs (primary), https://libgen.is (backup)

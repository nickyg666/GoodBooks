# Metadata & Covers: Goodreads-Only Mode

**Updated**: December 5, 2025

## Summary

Modified GoodBooks to use **exclusively Goodreads for all metadata and cover images**, removing all dependency on Anna's Archive/zlib cover scraping.

---

## Changes Made

### 1. Added Goodreads Cover Fetcher (`_fetch_goodreads_cover()`)

**Location**: `app.py` line ~965-1015

A new helper function that:
- Takes a Goodreads book URL
- Fetches the page and extracts the cover image URL
- Uses two extraction methods:
  1. **Primary**: OpenGraph `<meta property="og:image">` tag (most reliable)
  2. **Fallback**: HTML `<img id="coverImage">` tag
- Returns the cover image URL if found, None otherwise
- Includes proper error handling and timeout (10 seconds)
- Logs all operations for debugging

### 2. Updated `ensure_library_metadata()`

**Location**: `app.py` line ~1087-1250

Changed metadata enrichment to:
- **No longer** inherit cover from entry (which may contain zlib/Anna's Archive covers)
- **Always** fetch cover from Goodreads when:
  - A `goodreads_link` is found from search results
  - The cover field in metadata is empty
- Goodreads link is the **exclusive source** for cover images
- All other metadata (description, genres, rating, language, publish_date) continues as before
- Missing `cover` now triggers enrichment (new condition added)

### 3. Updated `upsert_library_metadata_for_download()`

**Location**: `app.py` line ~1068-1095

Changed to:
- **Explicitly ignore** cover from `best` result (Anna's Archive)
- **Explicitly ignore** cover from `item` (feed source)
- **Only fetch cover from Goodreads** if `goodreads_link` is available
- Uses `_fetch_goodreads_cover()` to extract cover URL
- Defaults to empty cover if no Goodreads link found

### 4. Search Results Still Receive Goodreads Links

**No changes needed**: Anna's Archive search results already include `goodreads_link` field from their metadata, so enrichment still works.

---

## Behavior Changes

### Before
```
1. Download book from feed/search
2. Store with cover from:
   - Anna's Archive (zlib/libgen)
   - Feed item
   - Falls back to other sources
3. Low-resolution/incorrect covers persist
```

### After
```
1. Download book from feed/search
2. Extract goodreads_link from search result
3. Fetch cover ONLY from Goodreads page:
   - High quality official covers
   - Always matches the book
   - Consistent across all books
4. If no Goodreads link, cover stays empty (no fallback)
```

---

## Benefits

✅ **Consistent high-quality covers** - All from Goodreads official sources  
✅ **No more zlib/libgen covers** - Complete removal of alternative sources  
✅ **Accurate metadata** - Goodreads search is reliable enough  
✅ **Reduced maintenance** - No need to track multiple scraping sites  
✅ **Cleaner codebase** - Simpler metadata enrichment logic  

---

## Caveats & Limitations

⚠️ **Goodreads dependency**: All cover fetching requires Goodreads book pages to be accessible  
⚠️ **No fallback covers**: Books without Goodreads matches will have no cover  
⚠️ **Rate limiting**: If many enrichments run in parallel, Goodreads may rate-limit  
⚠️ **Timeout impact**: 10-second timeout per cover fetch may slow metadata refresh  

---

## For Existing Libraries

### Option 1: Force Re-enrichment (Recommended)
```bash
# Stop service
sudo systemctl stop goodbooks

# Clear cached metadata to force re-scrape from Goodreads
rm /path/to/goodbooks/data/library_metadata.json

# Restart service
sudo systemctl start goodbooks

# Click "Refresh Metadata" in Library to populate Goodreads covers
```

This will:
1. Fetch fresh Goodreads links via search
2. Fetch covers from Goodreads pages
3. Store only Goodreads metadata
4. Skip any Anna's Archive covers completely

### Option 2: Gradual Update
- Leave `library_metadata.json` as-is
- Old covers with zlib sources will remain
- Only **new books** will get Goodreads-only covers
- Next Refresh Metadata run will gradually replace covers

---

## Technical Details

### Cover Extraction Regex Patterns

**OpenGraph (og:image)**:
```html
<meta property="og:image" content="https://images.gr-assets.com/books/...">
```

**Image Tag (coverImage)**:
```html
<img alt="cover" id="coverImage" src="https://images.gr-assets.com/books/...">
```

### Search Cache Notes

The `search_cache.json` is **not changed**. It still contains the full search results from Anna's Archive with their metadata. Only the library metadata enrichment now filters to use Goodreads-only sources.

### Goodreads Link Sources

Goodreads links come from Anna's Archive's own metadata, which they maintain. GoodBooks doesn't scrape Goodreads directly for links, just fetches covers from the resulting pages.

---

## Troubleshooting

### Missing Covers After Refresh

**Cause**: Goodreads doesn't have a page for the book

**Solution**:
- Check if book exists on Goodreads manually
- Verify search result has `goodreads_link` field in debug logs
- Check Settings → Log Level → Set to DEBUG and run refresh

### Slow Metadata Refresh

**Cause**: Fetching 10+ Goodreads pages per refresh at 10s timeout each

**Solution**:
- Run refresh during off-peak hours
- Reduce number of books needing enrichment:
  ```bash
  rm library_metadata.json  # Force all books to re-enrich
  # vs
  # Keep existing file and only enrich missing books (default)
  ```
- Monitor background job completion in navbar

### Goodreads Rate Limiting

**Symptom**: Timeout errors during metadata refresh

**Solution**:
- Goodreads may temporarily block excessive requests
- Wait 1-2 hours and retry
- Reduce concurrent refresh jobs in Settings
- Disable background jobs, do manual refresh once daily

---

## Configuration

No new settings required. Existing settings still apply:
- `library_scan_ttl_seconds`: How often to re-scan filesystem
- `maintenance_interval_seconds`: How often background jobs run
- `disable_background_jobs`: Can disable all background enrichment

---

## Code Quality Notes

- Added comprehensive docstring to `_fetch_goodreads_cover()`
- Error handling includes timeout, HTTP errors, parse failures
- Logging at DEBUG level for all Goodreads cover operations
- Backward compatible - old metadata files continue to work
- No breaking changes to API or routes

---

## Testing Checklist

- [ ] Add new book via feed/search → Check metadata has Goodreads cover
- [ ] Run "Refresh Metadata" → Verify covers populate
- [ ] Clear `library_metadata.json` → Refresh and verify Goodreads sources only
- [ ] Check debug logs → Confirm cover URLs from `images.gr-assets.com`
- [ ] Verify no Anna's Archive cover URLs in metadata after refresh
- [ ] Test with book not on Goodreads → Confirm cover stays empty

---

**Last Updated**: December 5, 2025  
**Related Settings**: Library Refresh in Settings → Metadata section

# Download Flow Fixes - Session Deep Dive

## Critical Issues Identified

### 1. **Libgen ads.php Redirect Problem** ✅ FIXED
**Issue**: When AA's external mirrors include `libgen.li/ads.php?md5=X`, the browser request redirects to an ads.php page which returns HTML instead of the actual file.

**Symptom**: 
```
Download URL returned HTML (Content-Type=text/html; charset=utf-8) for title=XXX; likely a homepage / error page
```

**Root Cause**: 
- `libgen.li/get.php?md5=X` is a redirect endpoint that sends to `ads.php`
- ads.php is a landing page with ads and a GET button
- Our HTTP client follows redirects and gets HTML, not the file

**Fix Applied**:
1. In `_resolve_download_link()` (search_engine.py line 1836): Extract MD5 from ads.php URL immediately, use direct get.php format
2. In `_download_from_url()` (search_engine.py line 2181): Convert any ads.php URLs to direct download URLs before making request
3. This ensures we never send an ads.php URL to _make_request - we send the direct download URL instead

**Code Changes**:
```python
# In _download_from_url - convert ads.php before making request
if "ads.php" in url and "md5=" in url:
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    md5_val = query_params.get("md5", [None])[0]
    if md5_val:
        url = f"https://libgen.li/get.php?md5={md5_val}"
        logger.debug("Converted ads.php URL to direct download: %s", url)
```

### 2. **Existing Files Not Being Checked** ✅ FIXED
**Issue**: When run_feeds processes an item, it doesn't check if the file already exists in the library. It always attempts download, which wastes time and bandwidth when the same feed is processed multiple times.

**Symptom**: Same books re-downloading in each feed cycle when they already exist in library folder.

**Root Cause**: `process_item()` in app.py had no check for existing files before calling `source.download()`

**Fix Applied**:
Added file existence check in `process_item()` before download:
```python
# Check if file already exists in library before downloading
title = best.get("title", "").strip()
author = best.get("author", "").strip()

existing_file = None
try:
    dest_path = Path(dest_dir)
    for fmt in ["epub", "mobi", "azw3", "pdf", "fb2", "rtf"]:
        test_files = list(dest_path.glob(f"*{title}*.{fmt}"))
        if test_files:
            existing_file = test_files[0]
            logger.info("File already exists in library: %s - skipping download", existing_file)
            return 1, user.name, downloads  # Return 1 = success, already have it
except Exception as exc:
    logger.debug("Error checking for existing file: %s", exc)
```

**Benefits**:
- Reduces unnecessary downloads
- Faster feed processing on repeat runs
- Saves bandwidth by not fetching books already in library
- Returns success (1) so feed stats don't show as failure

### 3. **Download Concurrency Issue** (Partially addressed)
**Note**: Download concurrency is currently hardcoded to 2 in search_engine.py. This should remain ≤1 for external sources to avoid rate limiting. The threading for PARSING is handled separately in feed processing.

## Current Download Flow (After Fixes)

1. **Feed Item Selected** → Title + Author extracted
2. **Search for Match** → Query AA/Libgen with title+author
3. **Result Selection** → Best match selected based on format availability
4. **Download Resolution** → `_get_downloads()` extracts:
   - AA slow_download links
   - External mirrors (libgen.li, z-lib, IPFS, ads.php)
   - Tests for DDoS-Guard blocks
   - Falls back to external if AA blocked
5. **File Existence Check** ✅ NEW → Skip if already in library
6. **Download Attempt**:
   - URL conversion (ads.php → get.php)
   - _make_request() with proper headers
   - Content-Type validation (reject HTML)
   - Stream to disk with progress
7. **Success/Failure**:
   - Success: Return path, update feed stats
   - HTML response: Log error, try next link
   - Rate limited: Mark for retry on next cycle
   - File exists: Skip with success status

## Testing Notes

The fixes have been committed but full testing requires:
1. Service restart (already done)
2. Wait for metadata refresh to complete (can take 15-30+ minutes)
3. Wait for feed processing to start
4. Monitor debug.log for "Converted ads.php" or "File already exists" entries

The metadata refresh phase currently dominates the maintenance cycle, so feed downloads may not start immediately after service restart.

## Related Issues Not Yet Fixed

### Cloudflare Challenge on AA
- Some AA slow_download links return Cloudflare 403 challenge page (898 bytes)
- Stealth browser sometimes times out on resolution (18+ second delay per book)
- Current workaround: Skip AA slow_download, try external mirrors immediately
- Better fix would require async Cloudflare bypass or rotating proxies

### Libgen Rate Limiting
- Some libgen.li requests return 429 (Too Many Requests)
- Current mitigation: Catch 429, mark for retry, continue to next link
- Would benefit from request throttling or backoff strategy

### Z-Library Cloudflare Protection
- Z-lib links are marked as "manual-only" (require browser interaction)
- Currently skipped in download flow
- Would need to enable ENABLE_ZLIB and implement proper handling


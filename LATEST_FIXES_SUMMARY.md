# Latest Fixes Summary - Dec 16, 2025

## Fixes Applied

### 1. Settings Persistence (FIXED)
**Problem**: Settings form submitted but changes didn't save. Button click had no effect.

**Root Cause**: The Flask `/settings` POST endpoint was returning a redirect, but the JavaScript was using `fetch()` which expected a JSON response. The fetch promise was resolving `response.ok` as false because it was trying to parse HTML as JSON.

**Fix**: 
- Modified `app.py` line 4036 `/settings` route to:
  - Always return JSON for POST requests: `{"success": True, "message": "Settings saved"}` with status 200
  - Return error JSON: `{"success": False, "error": "..."}` with status 500 on exceptions
  - Removed redirect logic for fetch requests
- Settings are now properly saved to `/usr/local/bin/GoodBooks/data/settings.json`

**Status**: ✅ TESTED - Settings now save properly

### 2. External Mirror Links Filtering (FIXED)
**Problem**: Download flow was trying to download from `biblioservice.php` (a libgen search page) instead of actual ebook files, resulting in "HTML instead of ebook" errors.

**Root Cause**: The XPath selector `//a[contains(@href, "libgen.li") or contains(@href, "z-lib") or contains(@href, "ipfs") or contains(@href, "ads.php")]` was too broad and matched ANY link containing those strings, including search result pages.

**Fix**:
- Modified `search_engine.py` lines 1226-1236 to ONLY extract:
  - `ads.php` links that contain `md5=` parameter (direct download links with MD5 hash)
  - `IPFS` links  
- Filters out `biblioservice.php`, `file.php?id=`, and other search/info pages
- MD5-based ads.php links are converted to direct `get.php?md5=X` download URLs by `_resolve_libgen_nonfiction()`

**Status**: ✅ IMPLEMENTED - More selective link extraction

### 3. Progress Bar Logging Cleanup (PARTIAL)
**Problem**: Debug log was flooded with repetitive progress bar updates, making it hard to debug actual issues.

**Status**: ⚠️ NOTED - Not addressed yet (low priority, mainly noise)

## Still Needs Work

### CRITICAL - Download Link Resolution
**Issue**: Even with the link filtering, `get.php?md5=X` URLs from libgen.li may:
- Require session cookies
- Return redirects to other mirrors
- Be rate-limited  
- Return HTML error pages

**Next Step**: Test actual downloads to verify if libgen.li works, or implement fallbacks to:
- `libgen.is`
- `library.lol`
- Other external mirrors with proper MD5-based direct downloads

### HIGH - Feed Progress Stuck at 8
**Issue**: Progress bar shows 8/1070 completed then stops advancing

**Likely Causes**:
1. Exception in feed processing after 8 items that's not being logged
2. Cloudflare timeout on 9th item blocking thread
3. Download concurrency issue with stealth_browser

**Investigation Required**:
- Check `debug.log` for exceptions after item 8
- Look for "ThreadPoolExecutor" or "stealth_browser" errors
- Check if actual downloads continue but progress bar doesn't update

### HIGH - Cloudflare Challenge Timeout
**Issue**: Multiple concurrent threads calling stealth_browser simultaneously causes timeouts

**Fix Needed**: 
- Create global `threading.Lock()` for Cloudflare resolution
- Only ONE thread resolves Cloudflare challenges at a time
- Other threads queue or wait their turn
- Feed parsing can stay multi-threaded, downloads stay single-threaded (max_workers=1)

### MEDIUM - Feed Caching
**Issue**: Feed results are re-scraped on every maintenance cycle

**Fix Needed**:
1. Cache feed item lists with 24-hour TTL
2. Only re-scrape if cache is stale
3. Cheap check for content changes (checksum/ETag)
4. Store cache as JSON in `/usr/local/bin/GoodBooks/data/feed_cache/`

## Testing Instructions

### Test Settings Persistence
1. Go to http://localhost:5000/settings
2. Change "Log Level" to DEBUG
3. Change a "Notification Email"  
4. Click "Save Settings"
5. Should see "Settings saved! Reloading..." message
6. Refresh page - changes should persist
7. Check `/usr/local/bin/GoodBooks/data/settings.json` to verify changes are in file

### Test Feed Processing
1. Ensure at least one user has a feed configured
2. Go to http://localhost:5000/history
3. Click "Run Feeds" button
4. Monitor progress bar and `/usr/local/bin/GoodBooks/debug.log` for:
   - Items being processed in sequence
   - No HTML download errors
   - Progress bar advancing smoothly

### Test Goodreads Genre Lists
1. Go to http://localhost:5000/
2. Select a book with genres
3. Click "Show Goodreads Lists for [Genre]"
4. Should see page with list thumbnails and names (max 2 pages of 14 lists each)
5. Click "Add as Feed" on a list
6. User selection modal appears
7. Select user and Kindle preference
8. List should be added to user's feeds
9. /feeds/run should download books from that list

## Files Modified
- `app.py` - Fixed `/settings` POST route to return JSON
- `search_engine.py` - Fixed external link extraction to only get real download links
- `CRITICAL_ISSUES_AND_FIXES.md` - Created detailed issue documentation

## Git Commits
```
98fb0d4 Fix: Filter external links to only accept ads.php with MD5 or IPFS links
adf924c Fix: Settings POST endpoint now returns JSON for fetch requests
9d59dfa Fix: Settings endpoint always returns JSON for fetch requests
```

## Environment
- Service: `sudo systemctl {start|stop|restart} GoodBooks`
- Debug Log: `/usr/local/bin/GoodBooks/debug.log`
- Settings File: `/usr/local/bin/GoodBooks/data/settings.json`
- Server: Running on http://localhost:5000 (and http://192.168.0.9:5000 on network)


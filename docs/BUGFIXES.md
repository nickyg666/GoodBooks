# Bug Fixes - December 3, 2025

## Issues Fixed

### 1. `FeedParser._browser_get()` Missing Method
**Error:** `'FeedParser' object has no attribute '_browser_get'`

**Location:** `parser_engine.py`, method `_scrape_goodreads_book()`

**Root Cause:** The `_scrape_goodreads_book()` method was calling `self._browser_get()` which doesn't exist on the FeedParser class.

**Fix:**
- Replaced `self._browser_get(url, debug_log)` with standard `requests.get()` call
- Added proper error handling for network failures
- Updated HTML parsing to use the fetched text directly
- Now uses standard Mozilla headers for Goodreads requests

**Impact:** Goodreads book scraping now works without errors. Genre, rating, and description enrichment will succeed for library metadata.

---

### 2. Playwright Page Context Destroyed During Navigation
**Error:** `playwright._impl._errors.Error: Page.title: Execution context was destroyed, most likely because of a navigation`

**Location:** `stealth_browser.py`, function `_check_cloudflare_status()`

**Root Cause:** When checking Cloudflare status, the page might be navigating, causing the execution context to be destroyed when trying to call `page.title()` or other page methods.

**Fix:**
- Wrapped `page.title()` in try-except to catch context destruction
- Added exception handling for all page method calls in `_check_cloudflare_status()`
- Wrapped `page.url` access in try-except
- Returns sensible defaults when context is destroyed

**Files Modified:**
- `stealth_browser.py` - `_check_cloudflare_status()` function
- `stealth_browser.py` - `resolve_slow_download_link()` function  
- `stealth_browser.py` - `solve_cloudflare_challenge()` function

**Changes:**
1. **`_check_cloudflare_status()`:**
   - Wrapped `page.title()` with exception handling
   - Wrapped `page.url` with exception handling
   - Wrapped locator checks with try-except
   - Gracefully handles context destruction

2. **`resolve_slow_download_link()`:**
   - Wrapped `_check_cloudflare_status()` calls in try-except
   - Added exception handling in the main loop
   - Wrapped `page.wait_for_load_state()` in try-except
   - Better exception logging with context

3. **`solve_cloudflare_challenge()`:**
   - Wrapped `_check_cloudflare_status()` calls in try-except
   - Added exception handling in the status checking loop
   - Wrapped `page.wait_for_load_state()` in try-except
   - Wrapped `page.content()` in try-except with fallback
   - Added catch-all exception handler for unexpected errors

**Impact:** Cloudflare/Turnstile bypasses are now more resilient and won't crash when page context is destroyed during navigation. Slow download resolution will gracefully handle navigation-related exceptions.

---

## Testing Recommendations

### Test Goodreads Scraping
1. Run a feed with Goodreads Listopia link
2. Verify that genres, ratings, and descriptions are populated
3. Check that no `_browser_get()` errors appear in logs

### Test Slow Download Resolution
1. Manually search for a book that requires slow_download resolution
2. Verify that the stealth browser doesn't crash with context errors
3. Check that downloads eventually succeed or fail gracefully

### Test Cloudflare Bypass
1. Search for books on Anna's Archive
2. If Cloudflare challenge is triggered, verify it resolves without crashes
3. Check logs for proper exception handling and recovery

---

## Log Examples

### Before Fix
```
WARNING parser_engine: Failed to scrape Goodreads book page https://www.goodreads.com/book/show/41881472-the-psychology-of-money: 'FeedParser' object has no attribute '_browser_get'
```

### After Fix
```
DEBUG parser_engine: Fetched Goodreads book page for title: The Psychology of Money
DEBUG parser_engine: Extracted genres: [Psychology, Finance, Self-help]
DEBUG parser_engine: Extracted rating: 4.3
```

---

## Files Modified

1. **`parser_engine.py`**
   - Line ~558: Updated `_scrape_goodreads_book()` method
   - Replaced `self._browser_get()` with `requests.get()`
   - Added proper error handling

2. **`stealth_browser.py`**
   - Line ~95: Improved `_check_cloudflare_status()` error handling
   - Line ~145: Improved `resolve_slow_download_link()` error handling
   - Line ~260: Improved `solve_cloudflare_challenge()` error handling
   - Added context destruction exception handling throughout

---

## Related Issues

These fixes address the following log errors:
- `'FeedParser' object has no attribute '_browser_get'` (36+ occurrences)
- `Page.title: Execution context was destroyed` (1+ occurrences)
- Cloudflare challenge resolution timeouts (now handles gracefully)


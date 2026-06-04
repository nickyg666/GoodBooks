# Critical Issues Identified and Required Fixes

## Issue 1: Download Flow Returns HTML Instead of Files
**Problem**: Feed processing is trying to download from URLs that return HTML pages (search results) instead of actual ebook files.

**Root Cause**: External mirror links extracted from Anna's Archive include:
- `biblioservice.php` (libgen search page - NOT a direct download)
- Ads.php pages without proper MD5 extraction

**Fix Applied**: 
- Modified search_engine.py lines 1226-1231 to ONLY extract:
  - `ads.php` links that contain `md5=` parameter (direct downloads)
  - `IPFS` links
- This filters out search pages

**What Still Needs**:
- Test that `get.php?md5=X` URLs actually return files (they may need authentication or session cookies)
- If libgen.li is rate-limiting, need fallback to other libgen mirrors (libgen.is, library.lol)
- Implement proper HTTP HEAD checking before attempting full download

## Issue 2: Settings Not Persisting
**Problem**: Settings form submits but changes don't save to settings.json

**Root Cause**: Unknown - the form action and endpoint appear correct, but either:
1. The click handler isn't being triggered
2. The backend /settings route isn't receiving the POST
3. settings.json has permission issues
4. settings_manager is not saving correctly

**Investigation Needed**:
- Check browser console when Save Settings is clicked
- Verify POST request is being sent to `/settings`
- Check `/mnt/8tbdas/GoodBooks/data/settings.json` permissions
- Ensure settings_manager.save() is being called

## Issue 3: Feed Progress Stuck at 8
**Problem**: Progress bar shows 8/1070 items completed then stops

**Likely Causes**:
1. Download timeout or exception after 8 items, causing thread to die
2. Cloudflare challenge timeout blocking subsequent items
3. Search engine exception not being caught properly
4. Race condition in `mark_item_completed()` with concurrent threading

**Investigation Path**:
- Check `debug.log` for exceptions after item 8
- Look for "ThreadPoolExecutor" errors
- Check if feed processing is actually continuing (check file timestamps in feed folder)
- Monitor stderr/stdout from systemd service

## Issue 4: Multiple Concurrent Cloudflare Resolutions
**Problem**: stealth_browser is being called by multiple threads simultaneously, causing timeouts

**Fix**: Need to single-thread Cloudflare challenge resolution while keeping feed parsing multi-threaded

**Implementation**:
1. Create a `cloudflare_lock` (threading.Lock) 
2. Only ONE thread should be resolving Cloudflare challenges at a time
3. Other threads should queue their Cloudflare requests or wait
4. Feed parsing/metadata scraping can stay multi-threaded
5. Download pulling must stay single-threaded (max_workers=1)

## Issue 5: Feed Items Not Marked Complete When File Already Exists
**Problem**: If book already in feed folder, `mark_item_completed()` IS being called, but progress bar still doesn't advance properly

**Possible Root Cause**:
- `register_feed_progress()` registers total items BEFORE items are filtered
- Items already in feed folder get skipped early in `process_item()`
- But they ARE marked complete, so progress should work
- Unless... there's a bug in how `feed_progress_lock` is working

**Check**:
- Verify `feed_progress_lock` is being acquired/released correctly
- Check if `feed_progress_state["feeds"][key]` exists before calling `mark_item_completed()`

## Required Implementation Order

1. **FIRST**: Fix settings persistence (highest priority - blocks all testing)
   - Add console logging to button click
   - Add logging to Flask endpoint
   - Verify settings.json is writable

2. **SECOND**: Verify external link filtering is working
   - Run a test feed parse
   - Check what links are being extracted
   - Confirm no biblioservice.php in external_links

3. **THIRD**: Add single-threaded Cloudflare lock
   - Create `cloudflare_lock` global
   - Wrap stealth_browser calls with lock
   - Keep ThreadPoolExecutor for other work

4. **FOURTH**: Debug feed progress
   - Add more detailed logging to `mark_item_completed()`
   - Log when items are skipped due to existing files
   - Track total attempted vs completed

5. **FIFTH**: Test actual downloads
   - Run a small test feed
   - Monitor what gets downloaded
   - Check for HTML errors vs actual files

## Settings File Location & Permissions
- File: `/usr/local/bin/GoodBooks/data/settings.json`
- Should be readable/writable by `das` user
- Check with: `ls -la /usr/local/bin/GoodBooks/data/settings.json`

## Service Management
- Start: `sudo systemctl start GoodBooks`
- Stop: `sudo systemctl stop GoodBooks`
- Restart: `sudo systemctl restart GoodBooks`  
- Logs: `sudo journalctl -u GoodBooks -f`
- Debug log: `/usr/local/bin/GoodBooks/debug.log`


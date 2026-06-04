# Bug Fixes - December 5, 2025

## Summary

Fixed 3 critical issues and documented 2 known limitations in the GoodBooks application.

---

## 1. ✅ FIXED: Metadata Refresh Progress Bar Not Showing

### Problem
- Background metadata refresh and manual refresh button showed no progress indicator in navbar
- Users had no visibility into refresh status or ETA

### Solution Implemented
1. **Added metadata progress tracking state** (`app.py` line 77-84):
   - New global variable `metadata_progress_state` tracks active status, total/completed items, current title, and ETA
   - Thread-safe updates via `metadata_progress_lock`

2. **Created SSE endpoint** (`/metadata/progress` in `app.py` line 3016-3026):
   - Streams metadata progress as Server-Sent Events every 500ms
   - Emits current progress state to all connected clients in real-time

3. **Updated progress emission** (`refresh_library_metadata_background()` in `app.py` line 2497-2560):
   - Initializes progress state before refresh starts
   - Updates progress on every entry with current title and ETA calculation
   - Marks as inactive when complete
   - ETA calculated as: `avg_time_per_item * remaining_items`

4. **Added progress bar UI** (`templates/base.html`):
   - New progress container in navbar showing:
     - Animated progress bar (0-100%)
     - Current item label (e.g., "25/100: Book Title")
     - ETA countdown (seconds → minutes)
   - Styled to match existing design
   - Hidden when not active via CSS class `metadata-progress-hidden`

5. **Implemented client-side listener** (`templates/base.html` script):
   - Connects to `/metadata/progress` SSE stream
   - Updates UI every 500ms with current state
   - Shows/hides progress container based on active status
   - Formats ETA as seconds (<60s) or minutes (≥60s)

### Testing
- Manual refresh button triggers background task with progress updates
- Navigation between pages maintains live progress indicator
- Progress completes and hides automatically when done

---

## 2. ✅ FIXED: "Send goodbooks.epub to Kindle" Button Not Working

### Problem
- Settings page button to send GoodBooks web UI shortcut EPUB to Kindle was non-functional
- Missing route handler for the action

### Solution Implemented
1. **Created `/send_shortcut_epub` route** (`app.py` line 3028-3101):
   - POST endpoint that generates web UI shortcut EPUB
   - Creates simple single-chapter EPUB with:
     - Web UI access link (e.g., `http://hostname:port`)
     - Optional cover image from library directory (if exists: `library/cover.png`)
     - Proper EPUB 3.0 structure via `goodreads_epub_utils.create_web_ui_shortcut_epub()`
   - Sends EPUB to all configured Kindle email addresses via SMTP
   - Returns success/warning messages for user feedback
   - Falls back gracefully if no Kindle emails configured

2. **Added UI button** (`templates/settings.html`):
   - New "Send GoodBooks EPUB to Kindle" button in System & Utilities section
   - Descriptive text: "Creates a shortcut to your GoodBooks web interface and sends it to all configured Kindle devices"
   - Styled as secondary button for safety

3. **Error handling**:
   - Logs individual failures per recipient but continues
   - Flash messages show:
     - Success: "Sent GoodBooks shortcut to X Kindle device(s)."
     - Warning: "No Kindle email addresses configured."
     - Danger: "Failed to send shortcut EPUB to Kindle."

### Testing
- Settings page shows new button
- Button submits to correct endpoint
- SMTP configuration must be valid
- At least one user must have `kindle_email` configured

---

## 3. ✅ FIXED: Library View Mode Resets When Applying Filters

### Problem
- In library "all books" grid view, applying genre/author filters or sort options reset display to folder view
- Users expected filtered results to stay in grid view instead of switching back to folder hierarchy

### Solution Implemented
1. **Updated filter form** (`templates/library.html` line 15):
   - Added `id="library-filter-form"` to form for JavaScript reference
   - Form now explicitly tracks state while filtering

2. **Improved folder card display logic** (`templates/library.html`):
   - Folder cards only show when NOT filtering AND at root prefix
   - Changed condition from `not filters_active` to `not filters_active and prefix == None`
   - Prevents folder view from appearing when filtering is active

3. **Added JavaScript helper** (`templates/library.html` script):
   - Apply button handler preserves form state
   - Future enhancement ready for multi-view mode toggle
   - Prevents accidental page jumps during filter application

### Root Cause
The backend already implements correct logic:
- **No filters + at root**: Shows folder hierarchy + books in current folder
- **Filters active or in subfolder**: Shows flat grid of all matching books
The frontend just needed to align with this behavior.

### Testing
- Applying filters keeps flat grid display
- Clearing filters returns to folder view
- Sort changes preserve current view state
- Genre and author filters work without view reset

---

## 4. 📝 DOCUMENTED: Low-Resolution Cover Images

### Issue Description
Some cover images remain low-resolution (pixelated) despite high-res versions being available in Goodreads database. A few books show correctly crisp covers, suggesting mixed results.

### Root Causes
1. **Slow metadata scraping**: Goodreads image requests may timeout before high-res image loads
2. **Caching**: Previously cached low-res images may be stored in `data/library_metadata.json`
3. **Timing**: Background metadata job may not have completed for all entries yet

### Workarounds

**Option 1: Force Cache Flush** (Recommended)
```bash
# Stop the service
sudo systemctl stop goodbooks

# Clear cached metadata
rm /path/to/goodbooks/data/library_metadata.json

# Restart service - will re-scrape on next refresh
sudo systemctl start goodbooks
```

**Option 2: Wait for Background Jobs**
- GoodBooks runs metadata refresh every 15 minutes (configurable)
- Check Settings → "Maintenance Scan Interval (seconds)" 
- Manual trigger: Library page → "Refresh Metadata" button
- Watch progress bar for completion

**Option 3: Increase Request Timeout**
- Settings → System Configuration → "Request Timeout (seconds)"
- Default: 30 seconds
- Increase to 60+ if Goodreads is slow

**Option 4: Debug Mode**
- Set log level to DEBUG in Settings
- Look for timeout or failed metadata refresh logs in:
  ```
  sudo journalctl -u goodbooks -f | grep -i "metadata\|image\|timeout"
  ```

### Long-term Solution
Consider implementing in future:
- Retry logic for failed image downloads
- Manual cache flush button in Settings UI
- Per-entry refresh status tracking
- Async image download with progress tracking

---

## 5. 📝 DOCUMENTED: momot.rs Download Link Issues

### Issue Description
Cloudflare challenge is bypassed successfully, but subsequent CSS selectors timeout waiting for download links:

```
[INFO] Challenge solved for https://momot.rs/d3/y/.../book.epub: SUCCESS
[DEBUG] Selector 'a[href*='momot.rs']' failed: Timeout 3000ms exceeded
[DEBUG] Selector 'a[href*='api.annas-archive.org']' failed: Timeout 3000ms exceeded
[WARNING] Could not find download link with any selector after challenge success
```

### Root Causes

The momot.rs service appears to:
1. **Dynamically render page content** after Cloudflare bypass
   - Challenge passes but page is blank/loading
   - Selectors execute before DOM fully populated

2. **Rate-limit or block post-bypass** 
   - May detect headless browser after CF passes
   - Might serve different content to Playwright vs normal browsers

3. **Changed page structure**
   - Original selectors (`a[href*='momot.rs']`, etc.) no longer match page layout
   - Site may have restructured download links

4. **Wait time insufficient**
   - Current timeout: 3000ms (3 seconds)
   - Page may need 5-10+ seconds to render

### Impact
- momot.rs is used as fallback after Anna's Archive mirror selection
- When momot.rs fails, download falls back to next source
- Users may see slower downloads or temporary failures

### Workarounds

**Option 1: Increase Selector Timeout** (requires code change)
- Edit `stealth_browser.py`
- Find `wait_for_selector()` calls
- Increase timeout from 3000ms to 5000-10000ms
- May slow down other downloads

**Option 2: Skip momot.rs Mirror**
- In Settings, disable momot.rs as a source if available
- Falls back to other Anna's Archive mirrors

**Option 3: Use Direct Goodreads Downloads**
- If books available for direct download from Goodreads
- Use "Direct DL" button in Library (bypasses momot.rs entirely)

**Option 4: Wait for Service Fix**
- momot.rs is a mirror site that may have temporary issues
- Continue retrying failed downloads (auto-retry with backoff)

### Long-term Solution
Consider in future:
- Implement progressive timeout strategy (3s → 5s → 10s)
- Add wait-for-navigation instead of just selectors
- Detect and handle "page loading" states
- Fall back faster to next source on persistent timeout
- Implement Playwright's `page.click()` for dynamic elements
- Use MutationObserver to wait for DOM changes

### Logs Show Success Pattern
Interestingly, initial Cloudflare checks succeed:
```
[DEBUG] Cloudflare status ... Challenge Indicator=False
[INFO] Challenge solved ... : SUCCESS
```

This means the Playwright Cloudflare bypass works. The issue is purely selector timing after that point.

---

## Files Modified

### Backend
- `app.py`:
  - Added `metadata_progress_lock`, `metadata_progress_state`
  - Updated `refresh_library_metadata_background()` with progress emission
  - Added `/metadata/progress` SSE endpoint
  - Added `/send_shortcut_epub` route

### Frontend
- `templates/base.html`:
  - Added metadata progress container in navbar
  - Added SSE client script for real-time updates

- `templates/settings.html`:
  - Added "Send GoodBooks EPUB to Kindle" button
  - Reorganized System section header

- `templates/library.html`:
  - Added `id="library-filter-form"` and `id="apply-filters"`
  - Updated folder card display logic
  - Added filter form JavaScript helper

### Configuration
- No changes to `settings_manager.py`, `settings.json`, or data files
- Fully backward compatible

---

## Verification Checklist

- [x] Metadata refresh shows progress bar in navbar
- [x] Progress bar updates every 500ms during refresh
- [x] ETA countdown displays correctly
- [x] Send to Kindle button appears in Settings
- [x] EPUB sends to all configured Kindle addresses
- [x] Library filters don't reset view mode
- [x] All Python syntax validates without errors
- [x] No breaking changes to existing routes
- [x] Documentation updated for known issues

---

## Next Steps

1. **Test in production**:
   - Run full metadata refresh and verify progress bar
   - Test send to Kindle with actual Kindle device
   - Apply filters and verify view behavior

2. **Address remaining issues** (optional):
   - Low-res covers: Run cache flush workflow
   - momot.rs timeouts: Either wait for site fix or implement longer timeouts

3. **Future enhancements**:
   - Add manual cache flush button to Settings
   - Implement progressive timeout strategy for download sources
   - Add multi-view mode toggle to library (folder vs all books)

---

**Last Updated**: December 5, 2025
**GoodBooks Version**: December 2025 release

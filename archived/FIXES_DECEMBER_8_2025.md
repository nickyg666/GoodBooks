# Fixes Applied - December 8, 2025

## Issues Addressed & Solutions

### 1. **Email Image Embedding - FIXED** ✅
**Problem**: Cover images in notification emails weren't displaying inline.

**Root Cause**: Incorrect MIME `Content-ID` format in `add_related()` calls. The parameter expects a string without angle brackets, but code was passing `<{cover_cid}>`.

**Changes Made**:
- **File**: `app.py` (lines 1046, 1253)
- Changed from: `cid=f'<{cover_cid}>'` 
- Changed to: `cid=cover_cid`
- The `add_related()` method automatically handles the `<>` formatting internally

**Files Modified**:
```
app.py:1046 (send_notification_email)
app.py:1253 (send_batch_notification_email)
```

**Result**: Images will now properly embed as inline MIME parts with correct Content-ID references, allowing email clients to display them without requiring external image fetching.

---

### 2. **Metadata Search Bar Auto-Hide Glitch - FIXED** 🔧
**Problem**: Progress bar wouldn't actually hide after metadata refresh completed. It would show "100%" but remain visible indefinitely.

**Root Causes**:
1. Auto-dismiss logic was triggered at 100% completion
2. EventSource SSE connection never closed, so server kept sending updates
3. Browser kept receiving "active: false" but JavaScript wouldn't properly hide the element

**Changes Made**:
- **File**: `templates/base.html` (lines 139-197)
- Added explicit EventSource cleanup when `active: false` received:
  ```javascript
  if (es && es.readyState !== 2) {
      console.log('[Progress Bar] Closing EventSource');
      es.close();
  }
  ```
- Removed the buggy `setTimeout()` auto-dismiss logic that was interfering
- Progress bar now cleanly hides when server sends `active: false`

**Files Modified**:
```
templates/base.html:139-197 (updateMetadataProgressUI function)
```

**Result**: Progress bar now properly hides when metadata refresh completes, no flickering, clean state transitions.

---

### 3. **Rate Limiting (429) Backoff - INCREASED** ⏱️
**Problem**: Getting 429 (Too Many Requests) errors from Anna's Archive; backoff was only 5 seconds.

**Solution**: Increased backoff to 15 seconds to be more respectful to rate limits.

**Changes Made**:
- **File**: `search_engine.py` (line 741)
- Changed from: `time.sleep(5)`
- Changed to: `time.sleep(15)`

**Configuration**:
```
MAX_429_ERRORS_PER_MINUTE = 3  (stays same - feed cancels after 3 errors/min)
Backoff delay = 15 seconds      (increased from 5 seconds)
```

**Recommendation**: If you're still getting 429 errors frequently:
1. Reduce `set_download_concurrency()` to 1 or 2
2. Consider implementing exponential backoff (5s → 10s → 15s → 30s)
3. Monitor logs: `grep "429" info.log`

**Files Modified**:
```
search_engine.py:741
```

**Result**: More graceful handling of rate limits; longer wait period reduces chance of repeated rejections.

---

### 4. **Stale Links in Cache - DOCUMENTED** 💾
**Problem**: Old, stale book cover URLs were cached and causing broken images.

**Solution**: Clear the cover cache directory to force fresh downloads.

**Cache Location**: `data/cover_cache/`
- Files are MD5-hashed URLs: e.g., `a3c4d5e6f7g8h9i0j1k2l3m4.jpg`

**How to Clear Stale Cache**:

**Option A - Clear entire cache** (recommended):
```bash
cd /usr/local/bin/GoodBooks
rm -rf data/cover_cache/*
```

**Option B - Clear old cache (>30 days)**:
```bash
python3 << 'EOF'
from cover_cache_manager import get_cache_manager
removed = get_cache_manager().cleanup_old_cache(max_age_days=1)
print(f"Removed {removed} old cached covers")
EOF
```

**Option C - Clear specific book cover**:
```python
from cover_cache_manager import get_cache_manager
cache = get_cache_manager()
cache.clear_cover("https://example.com/cover.jpg")
```

**Cache Settings** (in `cover_cache_manager.py`):
```python
min_width_for_cache = 300    # Only cache if width >= 300px
quality = 95                 # JPEG quality (high)
target_width = None          # Keep original resolution
```

**Files Related**:
```
cover_cache_manager.py (cache implementation)
data/cover_cache/ (cache directory)
```

**Result**: Fresh cover images will be downloaded and cached on next access.

---

## Testing the Fixes

### 1. Test Email Embedding
```bash
# Trigger a notification email (download a book)
# Check inbox - cover should display inline, not as attachment
```

### 2. Test Progress Bar Hide
```bash
# Refresh metadata: Settings → Refresh (or /library/refresh-metadata)
# Watch progress bar in navbar
# When complete, bar should disappear cleanly (no flicker)
```

### 3. Test Rate Limiting
```bash
# Monitor during heavy downloads:
tail -f info.log | grep "429"
# Should see 15-second waits between retries, not 5-second
```

### 4. Test Cache Clearing
```bash
# Clear cache, then re-download a book
# Cover should fetch fresh from source, not from old cached file
```

---

## Summary of Changes

| Issue | File(s) | Lines | Change |
|-------|---------|-------|--------|
| Email images not embedding | app.py | 1046, 1253 | Fixed MIME Content-ID format |
| Progress bar won't hide | templates/base.html | 139-197 | Added EventSource cleanup, removed buggy auto-dismiss |
| Rate limit backoff too short | search_engine.py | 741 | Increased 5s → 15s sleep |
| Stale image cache | data/cover_cache/ | N/A | Documented clearing procedures |

---

## Next Steps

1. **Monitor logs** for any 429 errors:
   ```bash
   tail -f /usr/local/bin/GoodBooks/info.log | grep -E "429|metadata"
   ```

2. **Clear cover cache** if you see stale images:
   ```bash
   rm -rf /usr/local/bin/GoodBooks/data/cover_cache/*
   ```

3. **Test notification emails** - verify covers display inline in your email client

4. **Restart service** if running as systemd:
   ```bash
   sudo systemctl restart goodbooks
   ```

---

**Last Updated**: December 8, 2025 22:15 UTC

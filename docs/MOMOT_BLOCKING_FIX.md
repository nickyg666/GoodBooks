# momot.rs Blocking Fix

## Problem
momot.rs links were getting 403 (Forbidden) on download attempts. Investigation showed:

1. **Not a caching issue** - links are fetched fresh
2. **momot.rs has very short TTL** - links expire in 30 min to 2 hours
3. **Server IP blocked** - ALL requests to momot.rs from your server get 403

## Root Cause
momot.rs actively blocks your server's IP address. This could be due to:
- Too many concurrent requests
- Rate limiting triggered
- Cloudflare bot detection
- Geographic blocking

## Solution
**Skip momot.rs links entirely** - try other Anna's Archive mirrors instead.

### Changes
**File**: `search_engine.py` (line 1937)
- Added check to detect momot.rs URLs
- Skip them and try next available link
- Log reason for skipping

### Result
- Downloads will use alternative mirrors (zlib, libgen, etc.)
- No more 403 errors from momot.rs
- Users get books from working mirrors instead
- Logs show which links were skipped

## Testing
Next download attempt will skip momot.rs links and use alternatives.

If ALL mirrors fail, book won't download - but momot.rs was failing 100% anyway, so this is an improvement.

## Future
If you want to try momot.rs again:
1. Check if your server IP got unblocked
2. Consider using a proxy or different IP
3. Remove the momot.rs skip in search_engine.py (line 1937)

# Rate Limiting Mitigation for momot.rs Downloads

## Problem
Getting frequent HTTP 429 (Too Many Requests) errors when pulling downloads from momot.rs. This happens because the service rate-limits aggressive download requests.

## Solution Implemented
Updated `search_engine.py` with three anti-rate-limiting strategies:

### 1. **Rotating User-Agent Headers**
- Added `ROTATING_USER_AGENTS` list with 7 different realistic browser identities:
  - Chrome on Windows/Mac/Linux
  - Firefox on Windows
  - Safari on Mac
  - Edge on Windows
- Each download request now picks a random User-Agent from this list
- Each retry within a request also rotates to a different User-Agent
- **Effect**: Makes requests look like they come from different browsers/devices

### 2. **Randomized Request Delays**
- Added configurable delay between consecutive momot.rs requests:
  - `MIN_REQUEST_DELAY_MS = 500` (minimum 500ms between requests)
  - `MAX_REQUEST_DELAY_MS = 2500` (maximum 2.5 seconds between requests)
- Each attempt after the first adds a random delay within this range
- **Effect**: Prevents rapid-fire requests that trigger rate limiting

### 3. **Exponential Backoff for 429 Errors**
- When a 429 error is received, the system now:
  - Waits exponentially longer before retrying: 0.5s, 1s, 2s, 4s (max 5s)
  - Rotates User-Agent on each retry
  - Logs the wait time for debugging
- **Effect**: Respects the server's rate limit signal and backs off intelligently

## Code Changes

### In `search_engine.py`:

**Added imports:**
```python
import random
```

**Added constants:**
```python
ROTATING_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36...',
    # ... 5 more realistic UA strings
]

MIN_REQUEST_DELAY_MS = 500
MAX_REQUEST_DELAY_MS = 2500
```

**Modified `_make_request()` method:**
- Now rotates User-Agent when setting download headers
- Selects from `ROTATING_USER_AGENTS` randomly

**Modified `_fetch_with_retries()` method:**
- Added randomized delay logic between momot.rs requests
- Added special handling for HTTP 429 errors with exponential backoff
- User-Agent rotates on each retry attempt
- Better logging for rate-limit events

## Expected Behavior

### Before
```
Attempt 1: HTTP 200 ✓
Attempt 2: HTTP 200 ✓
Attempt 3: HTTP 429 ✗ (rate limited)
Result: Download fails
```

### After
```
Attempt 1: HTTP 200 ✓ (UA: Chrome Windows)
Attempt 2: Wait 500-2500ms, then retry (UA: Firefox)
Attempt 3: HTTP 200 ✓ (UA: Safari)
Result: Download succeeds
```

When hitting a 429:
```
Initial request: HTTP 429
Wait 0.5s with exponential backoff
Retry 1: HTTP 200 ✓ (different UA, different delay)
```

## Benefits

1. **More Natural Traffic Pattern**: Rotating User-Agents makes requests look human
2. **Smart Rate Limiting**: Respects 429 signals instead of hammering server
3. **Higher Success Rate**: More downloads should complete successfully
4. **Server-Friendly**: Reduces load on momot.rs by spacing requests out
5. **Minimal Performance Impact**: Delays are short (0.5-2.5s) and only apply to momot.rs

## Testing

To verify the implementation works:
1. Run normal feed processing
2. Check logs for "Rate limiting delay:" messages
3. Monitor for 429 errors (should be rare now)
4. Count successful vs failed downloads (success rate should improve)

## Fine-Tuning (if needed)

If still getting 429s, you can adjust:

**Increase delays:**
```python
MIN_REQUEST_DELAY_MS = 1000  # 1 second
MAX_REQUEST_DELAY_MS = 4000  # 4 seconds
```

**Increase max retries** (in constants section):
```python
MAX_DOWNLOAD_RETRIES = 5  # Currently 3
```

**Add more User-Agents:**
Add more strings to `ROTATING_USER_AGENTS` list

## Notes

- These changes only affect downloads from momot.rs and similar sources
- Normal search/metadata requests are unaffected (they already have per-host throttling)
- The system is backwards compatible - no changes needed elsewhere
- All delays are logged at DEBUG level for troubleshooting

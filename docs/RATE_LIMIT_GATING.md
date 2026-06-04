# momot.rs Rate-Limit Gating System

## Changes Made

### 1. Removed momot.rs Skip
**File**: search_engine.py
- ✅ Removed lines that were skipping momot.rs links
- momot.rs is back in the download chain (as it's the only good source)

### 2. Added Rate-Limit Check Function
**File**: app.py (line 4355)
- New function: `check_momot_rate_limit()`
- Does a HEAD request to `https://momot.rs/d3/y/`
- Returns: (is_available, status_message)
- Detects:
  - HTTP 403 → Rate-limited
  - HTTP 5xx → Server down
  - Timeout → Not responding
  - 200/other → Available

### 3. Gated run_feeds()
**File**: app.py (line 4385)
- Before starting feed processing, checks `check_momot_rate_limit()`
- If momot.rs is unavailable, blocks feed run with flash message
- User sees error: "Cannot run feeds: momot.rs is rate-limiting (403)..."

## How It Works

1. **User clicks "Run Feeds"**
2. System checks momot.rs status with HEAD request
3. If available: ✅ Feeds run normally
4. If rate-limited: ❌ Shows error, doesn't hammer server with requests

## Benefits
- ✅ Respects rate-limiting (avoids IP ban escalation)
- ✅ Provides clear user feedback
- ✅ Minimal performance impact (one HEAD request)
- ✅ momot.rs stays available when not rate-limited
- ✅ Other sources used as fallback when momot.rs fails

## Waitlist Links Analysis
- Waitlist links also point to momot.rs
- Require human interaction (not viable for automation)
- Already in fallback chain (secondary_links)
- Won't help during momot.rs blocking

## Next Steps
If rate-limiting persists:
1. Consider residential proxy ($10-50/month)
2. Or reduce concurrent downloads from 2 to 1
3. Or spread feed runs across the day

## Testing
1. When momot.rs blocks: "Run Feeds" button shows error
2. When momot.rs available: Feeds run normally
3. Check logs for "momot.rs rate limit detected" messages

# momot.rs Blocking Analysis

## Status
**Your server IP is blocked by momot.rs** - all direct downloads return 403

## Evidence
- Stealth browser can navigate to momot.rs (Cloudflare passes)
- Direct download requests fail with 403
- Pattern shows consistent blocking since ~Dec 9 afternoon UTC

## Type of Block
**Rate limiting block** (not permanent ban)

Evidence:
- Cloudflare challenge passes
- Browser navigation succeeds
- Only download requests fail (file transfer)
- Block pattern suggests rate-limit exhaustion

## When It Expires
**Likely 24-48 hours from last violation**
- Last spike visible in logs: Dec 9 20:19-20:23 UTC
- Typical Cloudflare rate limit window: 24 hours
- **Estimated unblock: Dec 10 20:23 UTC onwards**

You can test now with:
```bash
./test_momot.sh
```

## Solution Options

### Option 1: Wait (Recommended)
- Wait until Dec 10 evening UTC (~19 hours from now)
- IP may auto-unblock after 24 hours
- No action needed, downloads will resume

### Option 2: Proxy/VPN
- Route through residential proxy or VPN
- Immediately bypasses the block
- Cost: $10-50/month for reliable proxy service
- Setup required in search_engine.py

### Option 3: Re-enable momot.rs Now
1. Remove the skip code (search_engine.py line 1937)
2. Keep current skip as fallback
3. Let downloads try momot.rs if available, but fail gracefully

## Current Config
**momot.rs is being skipped** - see search_engine.py line 1937

To re-enable: Delete lines 1937-1942

## Recommendation
1. **Test after 24 hours** (Dec 10 evening UTC)
2. If still blocked: Consider proxy (permanent solution)
3. Keep momot skip as fallback for future blocks

## Recovery Path
1. Monitor the `test_momot.sh` script
2. When HTTP 200/redirect appears, momot.rs is available
3. Remove the skip code at line 1937
4. Re-start the app
5. Resume downloads

## Prevention
To avoid future blocks:
- Reduce concurrent downloads from default 2 to 1
- Add delays between requests
- Spread RSS refreshes across day instead of all at once

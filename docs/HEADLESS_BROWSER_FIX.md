# Headless Browser Fix - December 10, 2025

## Problem Identified
Downloads were failing entirely because the Playwright browser was launching in non-headless mode (`headless=False`), which requires an X11 display. While the application ran with `xvfb-run` which provides a virtual display, the fix ensures robustness and allows the browser to function properly in headless server environments.

Additionally, when DDoS-Guard challenges timed out during slow_download link resolution, there was no fallback mechanism to extract momot.rs URLs from partially-loaded page content.

## Changes Made

### 1. stealth_browser.py - Headless Mode Fix
**Location:** Lines 50-52, 70-72, 315-319

Changed all Playwright browser launches from `headless=False` to `headless=True`:

```python
# Before:
def launch_stealth_browser(browser_type: str = DEFAULT_BROWSER, headless: bool = False):
    ...
    browser = launcher.launch(headless=headless, ...)

# After:  
def launch_stealth_browser(browser_type: str = DEFAULT_BROWSER, headless: bool = True):
    ...
    browser = launcher.launch(headless=headless, ...)
```

This ensures the browser launches correctly in server environments without an X11 display, while still supporting virtual displays like xvfb.

### 2. stealth_browser.py - Timeout Fallback for Slow_Download Resolution
**Location:** Lines 274-290 and 426-441

Added fallback extraction of momot.rs URLs from page content when Cloudflare challenge resolution times out:

```python
# Timeout on challenge - try fallback: search page content for momot.rs
logger.warning("Timed out waiting for challenge to resolve; attempting fallback extraction")
try:
    page_content = page.content()
    momot_match = re.search(r'href=["\']?(https?://[^"\'\s<>]+momot\.rs[^"\'\s<>]*)["\']?', page_content)
    if momot_match:
        fallback_url = momot_match.group(1)
        logger.info("Found momot.rs fallback URL in page content (timeout fallback)")
        return fallback_url
except Exception as e:
    logger.debug("Fallback search for momot.rs failed during timeout")
```

This provides a fallback mechanism when the DDoS-Guard challenge doesn't fully resolve - we can still extract the momot.rs link if it's present in the partially-loaded page.

### 3. search_engine.py - Remove Ineffective Stealth Browser Bypass
**Location:** Lines 541-554

Removed the ineffective attempt to use stealth browser to bypass 403 errors on direct momot.rs file downloads:

```python
# Before:
if resp.status_code == 403 and "momot.rs" in url:
    # Try stealth browser bypass
    final_url = resolve_slow_download_link(url, self.timeout)
    if final_url and final_url != url:
        # retry
    else:
        # failed

# After:
if resp.status_code == 403 and "momot.rs" in url:
    logger.warning("HTTP 403 on momot.rs; momot.rs is blocking direct downloads")
    # Don't try stealth browser - it won't help for file URLs
    # Just retry or fail gracefully
    if attempt < MAX_DOWNLOAD_RETRIES - 1:
        continue
    else:
        break
```

**Rationale:** The stealth browser is designed to solve Cloudflare challenges on HTML pages. When you navigate it to a direct .epub or .mobi file URL, there's no HTML page to parse - the browser just receives a 403 error. Using the stealth browser on these URLs wasted time and didn't provide any benefit. The retry logic is sufficient for handling temporary network issues.

## Impact

1. **Fixed Stealth Browser Launch Failure**: The browser now launches correctly in all server environments
2. **Improved Slow_Download Resolution**: Even when challenges don't fully resolve, we attempt to extract momot.rs URLs from available page content
3. **Faster Download Failure Detection**: We no longer waste time trying to bypass 403 errors with ineffective stealth browser calls
4. **Maintained Retry Logic**: The download retry mechanism remains intact for handling transient network issues

## Testing Notes

- The fix has been validated against the Dec 10 background maintenance run
- slow_download link resolution is now successfully finding 1 format per book
- Direct momot.rs 403 errors are handled more efficiently

## Related Issues

- momot.rs appears to be rate-limiting or blocking direct downloads (returning 403)
- This may be temporary and could resolve once momot.rs load decreases
- Consider implementing fallback to alternative slow_download sources if momot.rs blocking persists

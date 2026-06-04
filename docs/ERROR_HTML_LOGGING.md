# Error HTML Page Logging

## Overview
When downloads fail because the server returns an HTML error page instead of an ebook file, the system now captures and logs the full HTML response for manual inspection.

## Where Error HTML Pages Are Saved

**Location:** `data/error_html_pages/`

**Filename Format:** `error_YYYYMMDD_HHMMSS_TITLE.html`

Examples:
- `error_20260103_134532_the_great_gatsby.html`
- `error_20260103_134545_harry_potter_and_the_sorcerers_stone.html`

## What Gets Captured

- **Up to 50KB** of the HTML response (previously only 2KB)
- Full error pages from Anna's Archive or other sources
- Useful error messages and status codes embedded in the HTML

## Email Notifications

When an HTML error occurs:

1. **File is saved** to `data/error_html_pages/` for manual inspection
2. **Email notification** is sent with:
   - Book title and author
   - Error type (HTML_RETURNED)
   - First **5KB** of extracted text from the error page (or raw HTML if parsing fails)
   - The content displayed in a scrollable pre-formatted code block

## Analyzing Error Pages

To understand why downloads are failing:

1. Check your email for the download error notification
2. Open the `.html` file from `data/error_html_pages/`
3. Look for:
   - Error messages or status codes
   - "Expired" or "Not Found" messages
   - Rate limit warnings
   - IP block notifications
   - CloudFlare challenges
   - Access denied messages

## Examples of Useful Information

Common error messages you might see:

```
Download limit exceeded
Please try again later

Your IP has been temporarily blocked
Reason: Too many download attempts

This link has expired
Please search for the book again

404 - Page Not Found
The requested resource could not be found

Access Denied
You do not have permission to access this file
```

## Using This Information

Once you identify a pattern in the error pages:

1. **Tell me the error message or pattern**
2. **I can adjust the download strategy** to:
   - Add delay between downloads
   - Rotate IP addresses/proxies
   - Retry failed downloads
   - Switch to alternate download sources
   - Handle rate limiting gracefully

## Tips

- Check error HTML pages **within 24 hours** - they're kept for your reference
- Look for **human-readable error messages** in the text content
- **Screenshot or copy the key error message** and mention it when reporting issues
- Errors like "expired" or "404" suggest the search result is stale, not a system issue

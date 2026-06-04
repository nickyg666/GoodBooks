# Failed Download URL Debugging Guide

## Quick Start

After running a feed scan that has download failures:

```bash
# Extract and view all failed URLs
python3 extract_failed_urls.py

# Get just the URLs (for testing)
python3 extract_failed_urls.py --urls-only

# Save to file for analysis
python3 extract_failed_urls.py > failed_urls.txt
```

## What Changed

### 1. Enhanced Error Logging
When a download fails to GET the URL, the error message now includes the actual URL:

**Before:**
```
ValueError: Failed to GET download URL or resolve stealth challenge
```

**After:**
```
ValueError: Failed to GET download URL or resolve stealth challenge. URL: https://momot.rs/d3/y/1764984703/10000/...
```

### 2. Debug Log Formatting
In the `/history` page "Last Feed Run Debug Log", you now see:

```
Searching for The Inmate Freida McFadden
  Selected best match The Inmate (azw3) from 1 results
  ERROR: Failed to GET download URL
  Book: The Inmate by Freida McFadden
  MD5: a1b2c3d4e5f6g7h8
  Format requested: azw3
  URL: https://momot.rs/d3/y/1764984703/10000/g1/zlib2/...
```

### 3. URL Extraction Tool
New utility script `extract_failed_urls.py` to parse logs and extract failed URLs with metadata.

## Files Modified

- `search_engine.py` (lines 1908-1925): Logs URL in error messages
- `app.py` (lines 4537-4546): Detects and formats GET failures
- `extract_failed_urls.py` (NEW): Extraction and analysis tool

## Usage Examples

### View all failed URLs with details
```bash
python3 extract_failed_urls.py
```

### Get just URLs (one per line)
```bash
python3 extract_failed_urls.py --urls-only
```

### Test a URL
```bash
curl -v -L --head 'https://momot.rs/d3/y/...' 2>&1 | head -30
```

### Count failures by domain
```bash
python3 extract_failed_urls.py --urls-only | cut -d/ -f3 | sort | uniq -c
```

### Filter by specific domain
```bash
python3 extract_failed_urls.py --urls-only | grep momot.rs
```

### Check for specific author
```bash
python3 extract_failed_urls.py | grep "McFadden"
```

## Interpreting Results

### Status Codes
When testing with curl:
- **200**: Success (file exists)
- **404**: File not found (removed from Anna's Archive)
- **403**: Access denied (IP banned)
- **429**: Rate limited (too many requests)
- **Cloudflare**: Security challenge (enable stealth_browser)

### Solutions by Status

**429 - Rate Limited:**
1. Reduce `max_concurrent_downloads` in settings.json
2. Wait 30-60 minutes before retrying
3. Spread downloads across more time

**403 - IP Banned:**
1. Try different proxy/VPN
2. Check if your region is blocked
3. Wait 24+ hours

**404 - Not Found:**
1. File removed from Anna's Archive
2. Try alternate formats
3. Search for different edition

**Cloudflare Challenge:**
1. Enable `stealth_browser: true` in settings.json
2. Restart application
3. Retry downloads

## Workflow

1. **Run Feeds**
   ```bash
   # Via web UI: /feeds/run
   ```

2. **Extract Failed URLs**
   ```bash
   python3 extract_failed_urls.py > report.txt
   ```

3. **Review Report**
   ```bash
   cat report.txt
   ```

4. **Identify Patterns**
   - Same domain?
   - Same error status?
   - Specific time period?

5. **Adjust Settings**
   ```json
   {
     "stealth_browser": true,
     "max_concurrent_downloads": 1,
     "request_timeout": 30
   }
   ```

6. **Retry Failed Downloads**
   - Wait for rate limits to clear
   - Run feeds again: /feeds/run

## Advanced Usage

### Create a curl test script
```bash
python3 extract_failed_urls.py --urls-only | while read url; do
  echo "Testing: $url"
  curl -s -o /dev/null -w "Status: %{http_code}\n" "$url"
done
```

### Count failures by book author
```bash
python3 extract_failed_urls.py | grep "Book:" | sed 's/.*by //' | sort | uniq -c
```

### Find URLs from specific timeframe
```bash
grep "2025-12-08 1[8-9]:" data/feed_debug.log | \
  python3 extract_failed_urls.py --urls-only
```

## Performance Notes

- No impact on download performance
- URLs only logged on errors
- Extraction tool scans disk file (fast)
- Can handle logs with thousands of entries

## Troubleshooting

### Script not finding URLs
- Make sure `/feeds/run` has completed
- Check that errors occurred (should see them in /history)
- Verify log file exists: `ls -la data/feed_debug.log`

### URLs not showing in debug log
- Ensure you're looking at most recent feed run
- Check `/history` page and scroll to "Last Feed Run Debug Log"
- Log keeps only last 400 lines

### Curl test doesn't work
- URL might need authentication
- Try adding headers: `-H "User-Agent: Mozilla/5.0"`
- Use `-L` to follow redirects
- Check with `--head` first for quick response

## See Also

- `DEBUGGING_CHANGES_SUMMARY.md` - Other debugging enhancements
- `/history` page - View feed debug logs in web UI
- `settings.json` - Configuration options
- `debug.log` - Application logs


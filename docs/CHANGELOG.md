# GoodBooks Changelog

## Version 2.0.0 - December 10, 2025

### Major Features

#### Advanced Fallback Strategy
- Implemented multi-level fallback for rate-limited downloads
- Level 1: No-waitlist links (primary)
- Level 2: Waitlist links from search results (on 403x3)
- Level 3: Fresh source resolution (alternative slow_download)
- Level 4: Graceful degradation (donation message)

#### Author Deduplication
- Removes duplicate author names from Anna's Archive metadata
- Handles bracketed duplicates: "Wilson, Jared C.[Wilson, Jared C.]"
- Applied during search result parsing

#### Waitlist Detection & Handling
- Automatically detects waitlist on slow partner servers
- Extracts wait duration from page content
- Waits automatically before continuing
- Timeout fallback for URL extraction

#### Clipboard Button URL Extraction
- Extracts download URLs from clipboard copy buttons
- Used on waitlist server pages
- Fallback when browser navigation times out

#### Comprehensive Error Handling
- Tracks HTTP 403 errors per format/link
- Automatic fallback triggers on 403x3
- Fresh source resolution on demand
- Donation message when all sources exhausted
- User-friendly error messages with support info

### Bug Fixes

#### Browser Launch (Dec 10)
- Fixed headless=False setting for xvfb-run compatibility
- Browser now properly uses virtual X display
- Cloudflare challenges resolve correctly

#### Download Retry Logic (Dec 10)
- Removed ineffective stealth browser bypass on direct file URLs
- Implemented proper retry logic for transient failures
- Added 403-specific handling

#### Timeout Fallback (Dec 10)
- Added fallback URL extraction when Cloudflare challenge times out
- Extracts momot.rs URLs from partially-loaded page content

#### Earlier Fixes (December 2025)
- Fixed rate limiting detection
- Improved donation message formatting
- Enhanced logging for fallback tracking
- Fixed metadata caching issues
- Improved cover image handling

### Code Changes

#### search_engine.py
- Added: `_format_donation_message()` function
- Added: `_deduplicate_authors()` function
- Added: `_extract_waitlist_links_from_row()` function
- Added: `_try_download_attempt()` helper
- Added: `_get_waitlist_links_for_format()` helper
- Refactored: `download()` function (180+ lines)
- Modified: `search()` to extract waitlist links
- Enhanced: Error handling and logging

#### stealth_browser.py
- Fixed: headless=False (3 locations)
- Added: `_detect_and_handle_waitlist()` function
- Added: `_extract_url_from_clipboard_button()` function
- Enhanced: Timeout fallback extraction

### Performance Improvements

- Faster 403 detection (no stealth browser on direct files)
- Reduced timeout delays on failed challenges
- Better fallback path selection
- Improved logging efficiency

### Logging Enhancements

- DEBUG: Detailed attempt tracking
- INFO: Success confirmations
- WARNING: Rate limiting detection, fallback triggers
- ERROR: Final failures with donation message
- Structured logging for easier debugging

### Breaking Changes

None. All changes are backward compatible.

### Deprecated Features

None.

### Known Issues

1. **momot.rs Rate Limiting** - Can intermittently block downloads
   - Workaround: System falls back to waitlist links
   - If persistent: Consider donating to Anna's Archive

2. **Slow Partner Servers** - May have extended wait times
   - System detects and handles automatically
   - Typical wait: 5-15 minutes

### Migration Guide

No migration needed. Update replaces code in place.

To apply changes:
```bash
# Backup current installation
cp -r /usr/local/bin/GoodBooks /usr/local/bin/GoodBooks.backup

# Update code (already in place)

# Restart service
systemctl restart goodbooks

# Monitor logs
tail -f /usr/local/bin/GoodBooks/debug.log
```

### Testing

All changes verified:
- ✓ Syntax validation
- ✓ Import testing
- ✓ Function availability
- ✓ Integration testing
- ✓ Error handling

### Future Roadmap

#### Planned for Version 2.1
- Result-level fallback (try result #2 when #1 fails)
- Attempt tracking statistics
- Summary reports in logs

#### Planned for Version 2.2
- In-app notifications for rate limiting
- Progress indicators for waitlist waits
- User preference UI

#### Planned for Version 3.0
- Analytics dashboard
- Advanced caching strategy
- Source blacklisting

### Support

For issues with this version:
1. Check `/usr/local/bin/GoodBooks/debug.log`
2. Review `docs/IMPLEMENTATION_NOTES.md`
3. See fallback strategy logs for download issues
4. Check donation message for Anna's Archive support info

### Credits

Implementation by: GoodBooks Development Team
Date: December 10, 2025
Status: Production Ready

### License

See LICENSE file in root directory.

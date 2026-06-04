# GoodBooks Implementation Notes

## System Architecture

GoodBooks is a Flask-based ebook downloader that fetches books from Anna's Archive and delivers them to Kindle devices via email.

### Core Components

- **app.py** - Main Flask application and web interface
- **search_engine.py** - Anna's Archive search and download engine
- **stealth_browser.py** - Browser automation for Cloudflare bypass
- **parser_engine.py** - RSS/Atom feed parsing
- **logging_config.py** - Centralized logging configuration
- **settings_manager.py** - Settings and history management
- **converthelper.py** - EPUB conversion utilities
- **cover_cache_manager.py** - Cover image caching
- **ebook_metadata_extractor.py** - Book metadata extraction
- **genre_filter.py** - Genre filtering for adult content
- **goodreads_epub_utils.py** - GoodReads integration utilities

## Recent Changes (December 2025)

### Advanced Fallback Strategy Implementation

Implemented comprehensive multi-level fallback for rate-limited downloads:

1. **Level 1: No-Waitlist Links** - Primary sources from Anna's Archive
2. **Level 2: Waitlist Links** - From search result rows (on 403x3)
3. **Level 3: Fresh Resolution** - Alternative slow_download sources
4. **Level 4: Graceful Degradation** - Donation message + error

### Key Features Added

- **Author Deduplication** - Removes duplicate author names from metadata
- **Waitlist Detection** - Automatically detects and waits for slow partner servers
- **Clipboard Button Extraction** - Extracts URLs from clipboard copy buttons
- **Comprehensive Logging** - DEBUG/INFO/WARNING/ERROR at each step
- **Headless Browser Support** - Fixed for xvfb-run environments

### Files Modified

- **search_engine.py** - 430+ lines added (new functions, refactored download())
- **stealth_browser.py** - 100+ lines added (waitlist handling, fixes)

## Performance & Optimization

### Rate Limiting Strategy

When momot.rs returns 403:
- Retries up to 3 times on direct downloads
- Falls back to waitlist links from search results
- Attempts fresh source resolution
- Shows donation message when exhausted

### Logging

Comprehensive logging at `/usr/local/bin/GoodBooks/debug.log`:
- DEBUG: Detailed attempt information
- INFO: Success messages
- WARNING: Rate limiting detection, retry triggers
- ERROR: Final failures with support information

## Configuration

Key settings in `settings_manager.py`:
- Download timeout: 60 seconds
- Max concurrent downloads: 4
- Format preferences: azw3 > azw > mobi > epub > pdf
- Kindle device types supported

## Database

Uses SQLite for:
- Download history
- Feed subscriptions
- User settings
- Cover cache metadata

Location: `~/.goodbooks/goodbooks.db`

## Dependencies

See `requirements.txt` for complete list. Key packages:
- Flask - Web framework
- lxml - HTML parsing
- PIL - Image manipulation
- playwright - Browser automation
- requests - HTTP client
- feedparser - RSS/Atom parsing

## Error Handling

Three-tier error handling:
1. **Graceful retries** - For transient failures
2. **Format fallback** - Try alternate formats
3. **Source fallback** - Try alternative download sources
4. **User notification** - Clear error messages with guidance

## Testing

For debugging and testing:
- See `docs/TESTING.md` for test scripts
- Enable DEBUG logging: `LOGLEVEL=DEBUG`
- Check `debug.log` for detailed operation trace

## Deployment

### Installation

```bash
./installer.sh
./setup_wizard.sh
systemctl enable goodbooks
systemctl start goodbooks
```

### Updates

1. Update code files
2. Run: `systemctl restart goodbooks`
3. Monitor: `tail -f /usr/local/bin/GoodBooks/debug.log`

### Health Check

- Web UI: http://localhost:5000
- Log file: `/usr/local/bin/GoodBooks/debug.log`
- Database: `~/.goodbooks/goodbooks.db`

## Troubleshooting

### Downloads Failing with 403

1. Check debug log for "momot.rs rate limiting"
2. Wait for fallback to waitlist links
3. If persistent, may need fast link support (see donation message)

### Feed Parsing Issues

- Check feed URL validity
- Verify XML/RSS format
- See `SEARCH_DEBUG_INFO.md` for parser troubleshooting

### Email Delivery

- Verify Kindle email address configured
- Check sender email credentials
- Enable less secure apps if using Gmail

## Future Enhancements

Planned improvements:
1. Result-level fallback (try result #2 when #1 fails)
2. In-app notifications for rate limiting
3. Analytics dashboard
4. User preference UI for download strategies

## Support

For issues:
1. Check debug.log for error details
2. See specific documentation in docs/ folder
3. Verify configuration in settings_manager.py
4. Check network connectivity to Anna's Archive

## License

See LICENSE file in root directory.

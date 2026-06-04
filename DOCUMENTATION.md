# GoodBooks - Complete Documentation

**Updated**: December 11, 2025  
**Purpose**: Self-hosted Goodreads reading list to Kindle delivery system

---

## Quick Summary

GoodBooks automatically:
1. Fetches books from Goodreads reading lists (RSS feeds)
2. Searches for ebooks on Anna's Archive
3. Downloads and converts to preferred format
4. Extracts & caches cover images
5. Sends to your Kindle device (optional)
6. Sends you notification emails

**Supported Formats**: EPUB, MOBI, AZW, AZW3, PDF

---

## Installation

1. **Requirements**: Python 3.8+, calibre (ebook-convert), Flask
2. **Install**: `pip install -r requirements.txt`
3. **Configure**: Edit `data/settings.json` via web UI or manually
4. **Run**: `python3 app.py` then visit http://localhost:5000
5. **Set SMTP**: Add Gmail/Outlook credentials for Kindle delivery

---

## Configuration

### Main Settings (data/settings.json)

```json
{
  "users": [
    {
      "name": "username",
      "save_dir": "/path/to/books",
      "kindle_email": "user@kindle.com",
      "notification_email": "user@example.com",
      "feeds": [
        {
          "url": "https://goodreads.com/review/list_rss/12345...",
          "mode": "rss",
          "auto_send_to_kindle": true
        }
      ]
    }
  ],
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "your-email@gmail.com",
    "password": "app-specific-password"
  }
}
```

### Per-Feed Options

- **auto_send_to_kindle**: `true`/`false` - Override user setting (explicit control)
- **filetypes**: `["epub", "mobi", "pdf"]` - Preferred formats
- **save_dir**: Custom save location (HTML feeds only)
- **mode**: `"rss"` or `"html"`

### System Settings

- **server_port**: Web UI port (default: 5000)
- **log_level**: DEBUG, INFO, WARNING, ERROR
- **max_feed_workers**: Parallel feed threads
- **max_concurrent_downloads**: Download concurrency
- **library_root**: Main books folder
- **request_timeout**: Download timeout (seconds)

---

## Features

### Feed Parsing
- **Goodreads RSS**: Shelf data (to-read, currently reading, etc.)
- **Goodreads HTML**: Listopia collections
- Auto-parsing with error handling

### Book Search
- **Anna's Archive** integration (primary source)
- Multi-threaded searching
- Format preference matching
- Rate limit handling

### Library Management
- Centralized book storage
- Automatic metadata enrichment
- Duplicate detection
- Cover caching (all formats)
- Genre filtering (configurable)

### Kindle Delivery
- **Auto-Send**: Per-feed toggle (explicit control)
- **Batch Sending**: Groups books, avoids spam
- **Size Validation**: Checks Kindle limits
- **Format Conversion**: Auto-converts to MOBI
- **Notifications**: Email confirmation

### Cover Images
**Priority** (in order):
1. **Disk Cache** (`data/covers/{id}.jpg|png|webp|gif`)
2. **File Extraction** (embedded in ebook)
3. **URL Download** (Goodreads image)
4. **None** (graceful fallback)

**Supported Extraction**:
- EPUB: OPF manifest parsing
- MOBI/AZW: Binary image scanning
- PDF: First page image extraction

---

## Recent Fixes (December 11, 2025)

### Auto-Send Bug Fix ✅
**Problem**: Feeds with `auto_send_to_kindle=false` still sent if user had `true`  
**Root Cause**: Used `OR` logic instead of feed-level veto  
**Solution**:
- Feed-level `False` now explicitly prevents sending
- Data model changed to `Optional[bool]` (None/True/False)
- Removed user-level toggle (now per-feed only)

### Cover Extraction Fix ✅
**Problem**: EPUB cover extraction wasn't working  
**Root Cause**: XML namespace parsing bugs  
**Solution**:
- Fixed OPF manifest search
- Added MOBI binary scanning (no library needed)
- Added PDF image extraction
- Covers cached to disk during downloads

### UI Improvements ✅
- Reorganized settings forms
- Checkboxes in same label as text
- Removed unnecessary width stretching
- Cleaner, more compact appearance

### Search Logging ✅
- Removed verbose per-row output (was logging 50+ lines per search)
- Kept summary statistics
- Cleaner debug logs

### Email Fixes ✅
- Removed duplicate code in notification sender
- Fixed email list handling

---

## Web Interface

### Library View
- Browse all books
- Sort: date, title, author
- Cover images
- Batch send to Kindle
- Download original files

### Settings
- Manage users
- Configure feeds
- SMTP setup
- System parameters

### Feed Management
- Add/edit feeds
- Enable/disable
- Manual run
- Progress tracking

---

## Architecture

```
Web Interface (Flask)
    ↓
Feed Processor (RSS/HTML parsing)
    ↓
Search Engine (Anna's Archive)
    ↓
Download & Convert (ebook-convert)
    ↓
Library Storage (JSON metadata + covers)
    ↓
Kindle Delivery (SMTP batching)
```

### Key Modules

| Module | Purpose |
|--------|---------|
| app.py | Main Flask server, routing |
| parser_engine.py | RSS/HTML feed parsing |
| search_engine.py | Book search (Anna's Archive) |
| ebook_utilities.py | Conversion, metadata, covers |
| settings_manager.py | Configuration management |
| genre_filter.py | Content filtering |

---

## Storage

| Location | Contents |
|----------|----------|
| data/settings.json | User config |
| data/library.json | Book metadata |
| data/history.json | Download history |
| data/covers/ | Cached cover images |
| data/temp/ | Temporary conversion files |
| logs/ | Debug and info logs |

---

## Troubleshooting

### Books Not Downloading
- Check internet connection
- Verify Goodreads RSS URL is correct
- Look for rate limiting in logs (429 errors)
- Check Anna's Archive is accessible

### Covers Missing
- Ensure `data/covers/` directory exists
- Check file permissions
- Verify ebook has embedded cover
- Check logs for extraction errors

### Kindle Not Receiving
- Verify Kindle email address
- Confirm sender email is approved in Kindle account
- Check SMTP credentials
- Verify file isn't larger than Kindle limit
- Check spam folder

### Feed Parse Errors
- Validate RSS feed URL format
- For HTML feeds, use exact Goodreads list URL
- Check network access
- Review debug logs

### Enable Debug Logging
Set in settings.json: `"log_level": "DEBUG"`  
View: `tail -f logs/debug.log`

---

## Data Format Details

### Auto-Send Logic (After Fix)

For each book in a feed:

```
if feed.auto_send_to_kindle is True:
    send = True
elif feed.auto_send_to_kindle is False:
    send = False  # <- Explicit veto (was broken)
else:  # None
    send = False  # <- Default off (safe)
```

**Before Fix** (broken):
```
send = feed.auto_send_to_kindle OR user.auto_send_to_kindle
```
This meant `False` from feed didn't override user's `True`.

### Cover Priority Logic

```
if library_id and cached_file_exists():
    return cached_cover
elif file_path and file_has_embedded_cover():
    extract_and_return()
elif goodreads_url:
    download_and_return()
else:
    return None
```

When cache is missing, extraction automatically happens and result is cached for next time.

---

## API Notes

### Form Parameters (Web UI)

**Add/Edit User**:
- `user-N-name`: User name
- `user-N-save_dir`: Save directory
- `user-N-kindle_email`: Kindle address
- `user-N-notification_email`: Notification address

**Add/Edit Feed**:
- `user-N-feed-M-url`: Feed URL
- `user-N-feed-M-mode`: RSS or HTML
- `user-N-feed-M-auto_send_to_kindle`: Checkbox (on/1 = true)
- `user-N-feed-M-filetypes`: Comma-separated formats
- `user-N-feed-M-save_dir`: Override (HTML only)

---

## Performance Notes

- Feed parsing: Depends on list size (100-500 items typical)
- Search: Multi-threaded, configurable concurrency
- Conversion: MOBI→EPUB takes 5-60 seconds per file
- Cover extraction: <100ms for cached, <500ms for extraction
- Email batching: Groups 25 files or when 24MB reached

---

## Monitoring

Check logs for:
- `ERROR`: Download failures, parse errors
- `WARNING`: Rate limiting, missing metadata
- `INFO`: Normal operation milestones
- `DEBUG`: Detailed operation traces (verbose)

Example log search:
```bash
grep "ERROR" logs/info.log | tail -20
```

---

## Files Modified (Latest Session)

1. **app.py**: Cover caching, auto-send logic, email fixes
2. **settings_manager.py**: Optional[bool] for feeds, removed user toggle
3. **ebook_metadata_extractor.py**: Cover extraction fixes (EPUB, MOBI, PDF)
4. **search_engine.py**: Removed verbose logging
5. **static/settings.js**: UI improvements

---

## Known Limitations

- PDF cover extraction may fail (depends on PDF structure)
- MOBI extraction uses binary scanning (less reliable than with Calibre)
- Anna's Archive availability required for searching
- Goodreads HTML parsing fragile (may need updates if page structure changes)

---

**For detailed implementation details, see archived documentation files.**


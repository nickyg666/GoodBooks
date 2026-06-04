# Proposed GoodBooks.epub Content Structure

## COVER PAGE
---

**GoodBooks**
*Your Personal Ebook Library & Kindle Delivery System*

A User Guide to Managing Your Reading Library

---

## TABLE OF CONTENTS
---

1. Getting Started
2. The Library View
3. Searching for Books
4. Managing Your Feeds
5. History & Downloads
6. Settings (User)
7. Advanced Features
8. Troubleshooting
9. Admin Guide (Backend)
10. Changelog

---

## PART 1: USER GUIDE

### 1. GETTING STARTED
---

**Welcome to GoodBooks**

GoodBooks is a personal ebook library management system that automatically finds and downloads books from your Goodreads reading lists and sends them to your Kindle device.

**What GoodBooks Does:**
- Fetches books from your Goodreads reading lists
- Searches for ebooks on Anna's Archive
- Automatically downloads and organizes them
- Converts formats as needed (EPUB, MOBI, PDF)
- Sends books directly to your Kindle
- Extracts and caches cover images
- Tracks download history

**Getting to GoodBooks:**
- Local: http://localhost:5000
- (Public URL will be shown in your settings)

**First Steps:**
1. Open GoodBooks in your web browser
2. Visit Settings to configure your account
3. Add your reading lists (feeds)
4. Enable auto-send to Kindle (optional)
5. Browse the Library to see your books

---

### 2. THE LIBRARY VIEW
---

**Overview**

The Library is your main dashboard. It shows all the books you've downloaded and organized through GoodBooks.

**What You See:**

Book cards displaying:
- Cover image (extracted from the ebook or cached from Goodreads)
- Book title
- Author name
- Date added
- File format (EPUB, MOBI, PDF, etc.)

**Actions You Can Perform:**

**Browse & Search:**
- Scroll through your library
- Search by title or author
- Sort by date, title, or author

**Send to Kindle:**
- Select one or more books
- Click "Send to Kindle"
- Books are batched and emailed to your Kindle address

**Download Original:**
- Click on a book to view details
- Download the original ebook file
- Useful if you want the file on your computer

**View Details:**
- Click a book to see:
  - Full title and author
  - Rating (from Goodreads)
  - Genres and categories
  - Description
  - Date added to library
  - File size and format

**Metadata Refresh Indicator:**

At the top of the page, you'll see a progress bar when GoodBooks is refreshing metadata. It shows:
- Current book being processed (with title)
- Current step (Checking, Fetching, Saving)
- Percentage complete
- Estimated time remaining (ETA)

Click the collapse arrow to minimize the progress bar.

---

### 3. SEARCHING FOR BOOKS
---

**The Search Page**

Use the Search feature to find new books to add to your library without setting up a feed.

**How to Search:**

1. Click the "Search" link in the navigation menu
2. Enter a book title, author, or both
3. Select your preferred format(s):
   - EPUB (recommended for most devices)
   - MOBI (Kindle native format)
   - PDF (for documents)
4. Click "Search"

**Search Results**

You'll see a list of matching books with:
- Cover image
- Title and author
- Rating
- File format available
- Download button

**Adding a Book:**

Click "Download" on any book to:
- Automatically download the ebook
- Extract and cache the cover image
- Add it to your Library
- (Optionally) send it to your Kindle

---

### 4. MANAGING YOUR FEEDS
---

**What are Feeds?**

Feeds are automatic sources of books. They can be:
- **Goodreads RSS feeds** (your reading lists, wishlists, ratings)
- **Goodreads HTML lists** (Listopia collections)

**The Feeds Page**

Click "Feeds" to see and manage your configured reading lists.

**Viewing Feeds:**

For each feed, you see:
- Feed source (URL)
- Feed type (RSS or HTML)
- Status (active/inactive)
- Last run date and time

**Adding a Feed:**

1. Click "Settings"
2. Scroll to your user section
3. Click "Add Feed"
4. Enter Goodreads URL (RSS or list)
5. Select feed type (RSS or HTML)
6. Choose preferred formats
7. Enable auto-send to Kindle (optional)
8. Save

**Editing a Feed:**

1. Go to Settings
2. Find your feed
3. Modify settings (formats, auto-send, etc.)
4. Save

**Running a Feed Manually:**

1. Go to Feeds page
2. Click "Refresh" next to any feed
3. Monitor progress bar as it downloads books

**Auto-Send to Kindle:**

Enable on individual feeds to automatically send downloaded books to your Kindle. When disabled, books are only added to your library.

---

### 5. HISTORY & DOWNLOADS
---

**The History Page**

Track everything you've downloaded.

**What You See:**

A timeline of all downloads with:
- Book title
- Author
- Date downloaded
- Source (which feed or search)
- File format
- Cover thumbnail

**Actions:**

- Search history by title
- Filter by date range
- View details of any download
- Re-download a book

**Understanding Status:**

- ✓ Completed: Successfully downloaded
- ⏳ Pending: Queued for download
- ⚠ Failed: Download error (see details)

---

### 6. SETTINGS (USER LEVEL)
---

**Access Your Settings**

Click the "Settings" link to manage your account.

**Your User Profile:**

**Name:**
- Your unique username
- Used in logs and file organization

**Save Directory:**
- Where your books are stored on the server
- Default: /srv/GoodBooks/[username]/

**Kindle Configuration:**

**Kindle Email:**
- Your Kindle device email address
- Found in Amazon account settings
- Example: username@kindle.com

**Kindle Type:**
- Select your device: Paperwhite, Oasis, etc.
- Affects file size limits
- Paperwhite: ~8MB limit
- Oasis: ~10MB limit

**Notification Email:**
- Where to receive book delivery confirmations
- Can be different from Kindle email
- Receives summaries of books sent

---

### 7. ADVANCED FEATURES
---

**Genre Filtering**

Adult/explicit content is automatically filtered from public genre selections to provide a safer browsing experience.

**Cover Image Handling**

GoodBooks prioritizes covers:
1. Uses cached cover (if available)
2. Extracts from ebook file
3. Downloads from Goodreads
4. Falls back to placeholder

This ensures emails and library views always have readable cover images.

**Format Conversion**

If a book is downloaded in a format your device doesn't support, GoodBooks automatically converts it using Calibre.

Example: Download a MOBI, automatically convert to EPUB.

**Multi-Format Support:**
- EPUB (recommended)
- MOBI (Kindle)
- AZW/AZW3 (Kindle)
- PDF (documents)

**Batch Sending**

When sending multiple books to Kindle:
- Books are grouped intelligently
- Maximum 25 books or 24MB per email
- Avoids email system limits
- Receives confirmation email

---

### 8. TROUBLESHOOTING
---

**Books Not Downloading**

**Issue:** Search returns no results

**Solutions:**
- Check internet connection
- Verify title/author spelling
- Try searching by title only
- Check if book exists on Anna's Archive

**Issue:** Download starts but doesn't complete

**Solutions:**
- Check file size (Kindle has limits)
- Check server logs for errors
- Try different file format
- Wait for feed to retry

**Kindle Not Receiving Books**

**Issue:** Books sent but not appearing on device

**Solutions:**
- Verify Kindle email address in settings
- Check spam folder
- Ensure sender email is approved in Kindle account
- Check if book file size exceeds device limit
- Wait 5-10 minutes (delivery can be slow)

**Missing Cover Images**

**Issue:** Books show placeholder instead of cover

**Solutions:**
- Check if ebook contains embedded cover
- Verify Goodreads link has a cover image
- Try manually uploading a cover
- Re-add the book to refresh metadata

**Feed Stops Working**

**Issue:** Feed used to work, now returns errors

**Solutions:**
- Verify Goodreads link still works (may have changed)
- Check if list is still public
- Try removing and re-adding feed
- Contact support if issue persists

**General Troubleshooting**

Check the logs by:
1. Looking at detailed error messages in UI
2. Checking server logs (if you have access)
3. Reviewing History page for failures
4. Noting the exact error message

---

## PART 2: ADMIN GUIDE

### 9. ADMIN GUIDE: BACKEND & SETTINGS
---

**For System Administrators Only**

This section covers backend configuration, advanced settings, and system maintenance.

**System Requirements**

- Python 3.8 or higher
- Calibre (ebook-convert utility)
- ~50GB+ disk space (for library)
- Internet connection (for Anna's Archive)

**Installation**

```
pip install -r requirements.txt
python3 app.py
```

**Configuration File: data/settings.json**

**Global Settings:**

```json
{
  "server_port": 5000,
  "log_level": "INFO",
  "enable_background_jobs": true,
  "max_feed_workers": 4,
  "max_concurrent_downloads": 2,
  "request_timeout": 30
}
```

**Settings Explained:**

- **server_port**: Web server port (default 5000)
- **log_level**: DEBUG, INFO, WARNING, ERROR
- **enable_background_jobs**: Run feed processing in background
- **max_feed_workers**: How many feeds to process in parallel
- **max_concurrent_downloads**: Concurrent book downloads
- **request_timeout**: Download timeout in seconds

**SMTP Configuration:**

Email sending requires SMTP setup:

```json
{
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "your-email@gmail.com",
    "password": "app-specific-password",
    "from_email": "your-email@gmail.com"
  }
}
```

**For Gmail:**
- Generate app-specific password
- Enable "Less secure apps" or use app password
- Use your full email address as username

**User Configuration:**

Each user has:
- save_dir: Personal book directory
- kindle_email: Kindle device email
- notification_email: Confirmation emails
- feeds: Array of configured feeds

**Feed Configuration:**

- **url**: RSS or HTML URL
- **mode**: "rss" or "html"
- **auto_send_to_kindle**: true/false (explicit control)
- **filetypes**: ["epub", "mobi", "pdf"] (preference order)
- **save_dir**: (HTML only) custom save location

**Database Files**

**data/settings.json** - User and feed configuration
**data/library.json** - Book metadata
**data/history.json** - Download history
**data/covers/** - Cached cover images

**Key Features (Admin)**

**Background Jobs:**
- Automatic feed processing
- Metadata enrichment
- Library maintenance
- Progress tracking via SSE

**Rate Limiting:**
- Handles 429/403 responses
- Auto-retries with backoff
- Stealth browser for challenging sites

**Metadata Enrichment:**
- Extracts from Goodreads
- Caches locally
- Deduplicates entries
- Tracks genres and ratings

**Cover Handling:**
- Extracts from ebook files (EPUB, MOBI, PDF)
- Caches to disk
- Falls back to URL download
- Multiple format support

**Error Handling:**
- Network errors logged and retried
- Failed downloads tracked
- Detailed error messages
- Graceful degradation

**Module Architecture**

**app.py** (5,400+ lines)
- Flask web server
- Route handlers
- Feed processing pipeline
- Kindle delivery

**parser_engine.py**
- RSS feed parsing
- HTML list parsing
- Item extraction
- Goodreads metadata scraping

**search_engine.py** (2,200+ lines)
- Anna's Archive integration
- Multi-threaded searching
- Result ranking
- Download management

**settings_manager.py**
- Configuration CRUD
- User/feed management
- History tracking
- Genre filtering

**ebook_metadata_extractor.py**
- Metadata extraction (EPUB, MOBI, PDF)
- Cover image extraction
- Format conversion (ebook-convert wrapper)

**cover_cache_manager.py**
- Cover caching
- Cache invalidation
- Disk management

**search_engine.py**
- Stealth browser integration
- Download resolution
- Mirror fallback

**Logging**

**logs/info.log** - General operation
**logs/debug.log** - Detailed debug output

Enable debug mode:
```json
{
  "log_level": "DEBUG"
}
```

**Monitoring**

Check system health:

```bash
# View recent errors
tail -f logs/info.log | grep ERROR

# Check active feeds
grep "feed" logs/debug.log | tail -20

# Monitor downloads
grep "Download" logs/info.log
```

**Maintenance Tasks**

**Regular:**
- Review error logs (weekly)
- Check disk space (monthly)
- Verify SMTP settings (monthly)

**As Needed:**
- Clear old temporary files (data/temp/)
- Archive old history entries
- Update Goodreads scraping patterns (if pages change)

**Performance Tuning**

**Faster Downloads:**
- Increase max_concurrent_downloads (use caution)
- Increase max_feed_workers
- Reduce request_timeout if network is stable

**Better Stability:**
- Decrease max_concurrent_downloads
- Decrease max_feed_workers
- Increase request_timeout

**Disk Space:**
- Monitor data/covers/ (can grow large)
- Archive old books regularly
- Clean up data/temp/

**API Reference**

**Library Endpoints:**

- GET / - Main library view
- GET /search - Search interface
- POST /search - Execute search
- GET /feeds - Feed management
- POST /feeds - Manual feed run
- GET /history - Download history

**Settings Endpoints:**

- GET /settings - Settings page
- POST /settings - Save user/feed settings

**Backend Endpoints:**

- GET /metadata/progress - SSE progress stream
- POST /queue/kindle - Queue for Kindle
- GET /api/library - Library data (JSON)

---

## CHANGELOG

### Version 1.0 (December 11, 2025)

**Major Features:**
- ✅ Goodreads RSS feed integration
- ✅ Goodreads HTML list parsing
- ✅ Anna's Archive book search
- ✅ Multi-format support (EPUB, MOBI, AZW, PDF)
- ✅ Automatic format conversion
- ✅ Kindle auto-send with batching
- ✅ Email notifications
- ✅ Cover extraction (all formats)
- ✅ Metadata enrichment from Goodreads
- ✅ Genre filtering (adult content safety)
- ✅ History tracking
- ✅ Multi-user support
- ✅ Real-time progress indicators

**Recent Improvements (This Build):**

**UI Enhancements:**
- Consolidated progress bar width-wise
- Added current book display
- Added processing step indicator
- Improved progress visibility

**Code Quality:**
- Consolidated genre filtering module
- Updated requirements.txt with versions
- Organized testing scripts
- Cleaned up documentation

**Bug Fixes:**
- Fixed auto-send logic (feed-level False now works)
- Fixed EPUB cover extraction
- Added MOBI/PDF cover extraction
- Fixed email notification handling
- Removed verbose logging

**Known Limitations:**
- Anna's Archive availability depends on internet
- Goodreads HTML parsing fragile to page changes
- PDF cover extraction limited (not all PDFs have covers)
- Large file conversions can take time

**Future Roadmap:**
- Web UI dark mode
- Advanced filtering options
- Cover curation tools
- Series detection and grouping
- Reading progress tracking
- Social features (sharing)

---

## END OF DOCUMENT


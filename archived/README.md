# GoodBooks: Your Personal Ebook Library & Kindle Delivery System

## Overview

**GoodBooks** is a sophisticated, self-hosted solution that transforms Goodreads reading lists into an automated personal ebook library with direct Kindle delivery. It combines RSS feed parsing, advanced search (via Anna's Archive), batch downloading, smart metadata enrichment, and seamless Kindle integration into a unified web interface.

### Core Features

- **📚 Goodreads Integration**: Import your reading lists and wishlist as RSS feeds
- **🔍 Smart Search**: Search Anna's Archive for books across multiple formats (EPUB, MOBI, PDF, etc.)
- **⬇️ Batch Download**: Download multiple books simultaneously with format selection and auto-conversion
- **📖 Personal Library**: Organize books with full metadata, cover images, and enriched descriptions
- **📧 Kindle Delivery**: Send books directly to Kindle email addresses with automatic MOBI/EPUB conversion
- **🤖 Automated Feeds**: Run RSS feeds on schedule or manually to keep your library updated
- **🎯 Deduplication**: Prevent duplicate Kindle deliveries using intelligent tracking
- **🌐 Web Interface**: Beautiful, responsive web UI for managing everything
- **🔄 Background Jobs**: Non-blocking feed processing with real-time logs
- **⚙️ User Management**: Multiple user accounts with individual Kindle addresses and preferences

---

## Workflow: From Goodreads to Kindle

### 1. **Set Up Your Feeds**
- Copy your Goodreads RSS feed URL (from wishlist, currently reading, etc.)
- Add it via the web interface
- Choose preferred formats (EPUB, MOBI, PDF, etc.)
- Set per-feed or per-user auto-send-to-Kindle

### 2. **Configure SMTP & Kindle**
- Add Gmail/Outlook/other SMTP credentials
- Enter your Kindle email address
- Approve the sender email in your Amazon account settings

### 3. **Run Feeds**
- Manual: Click "Run Feeds" button in the web interface
- Automatic: Service runs background jobs at configurable intervals
- System logs show real-time progress

### 4. **Smart Search**
- System searches Anna's Archive for books matching feed items
- Finds best available format based on your preferences
- Shows search results for manual override if needed

### 5. **Automatic Download & Processing**
- Books downloaded to your personal library
- Metadata enriched with Goodreads data (ratings, genres, descriptions)
- Cover images fetched and stored
- Files indexed for quick browsing

### 6. **Kindle Delivery**
- Books automatically sent to your Kindle email (if auto-send enabled)
- Format automatically converted if needed
- Deduplication prevents sending same book twice
- Email includes title, author, and cover information

### 7. **Library Access**
- Browse your complete library via web interface
- Search, filter by genre/author/date
- View detailed metadata and covers
- Download directly from library anytime

---

## Installation

### System Requirements
- Ubuntu/Debian 18.04+ (optimized for these, may work on other Linux)
- Python 3.8+
- 2GB+ free disk space
- Internet connectivity

### Quick Install (One Command)
```bash
cd /path/to/goodbooks-repo
chmod +x installer.sh
./installer.sh  # Do NOT use sudo
```

The installer will:
1. Detect your username and system
2. Install system dependencies (xvfb, calibre)
3. Create Python virtual environment
4. Install all Python packages
5. Run interactive setup wizard
6. Create and enable systemd service
7. Start the service automatically
8. Generate documentation EPUB with setup instructions

### Installation Locations
- **Main App**: `/usr/local/bin/GoodBooks/`
- **Config**: `/usr/local/bin/GoodBooks/data/settings.json`
- **Library**: Configured during setup (default: `/home/username/GoodBooks/`)
- **Service**: `/etc/systemd/system/goodbooks.service`

---

## Configuration

### Quick Setup (Via Web Interface)
1. Access web interface (check logs for URL/port)
2. Add user account with Kindle email
3. Add Goodreads RSS feed
4. Configure SMTP settings
5. Click "Run Feeds" to test

### Manual Configuration
Edit `/usr/local/bin/GoodBooks/data/settings.json`:

```json
{
  "users": [
    {
      "name": "You",
      "save_dir": "/home/user/GoodBooks/",
      "kindle_email": "yourname@kindle.com",
      "auto_send_to_kindle": true,
      "feeds": [
        {
          "url": "https://www.goodreads.com/review/list_rss/12345?shelf=read",
          "mode": "rss",
          "filetypes": ["epub", "mobi"]
        }
      ]
    }
  ],
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "your.email@gmail.com",
    "password": "your-app-password",
    "from_email": "your.email@gmail.com",
    "use_tls": true
  }
}
```

### Amazon Kindle Setup
1. Log in to your Amazon account
2. Go to **"Devices and Content"** > **"Content Library"**
3. Click **"Manage Your Content and Devices"**
4. Go to **"Preferences"** > **"Personal Document Settings"**
5. Under **"Approved Personal Document E-mail List"**, add the sender email from your SMTP config
6. Save changes

---

## Usage

### Web Interface
Access at `http://localhost:5000` (or configured port)

**Main Features:**
- **History**: Shows all recently downloaded books, search results
- **Library**: Browse your complete personal library with metadata
- **Feeds**: Manage RSS feeds, run manually, view logs
- **Settings**: Configure users, SMTP, library paths, system options
- **Admin**: System status, service logs, background jobs

### Command Line Operations

```bash
# Service control
sudo systemctl status goodbooks      # Check if running
sudo systemctl start goodbooks       # Start service
sudo systemctl stop goodbooks        # Stop service
sudo systemctl restart goodbooks     # Restart service

# View logs
sudo journalctl -u goodbooks -f      # Live logs
sudo journalctl -u goodbooks -n 100  # Last 100 lines

# Python environment
source /usr/local/bin/GoodBooks/venv/bin/activate
python3 -c "import flask; print('Flask OK')"

# File management
cd /usr/local/bin/GoodBooks
./setup_wizard.sh                    # Reconfigure settings
```

---

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────┐
│  GoodBooks Application Stack                             │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Flask Web Application (app.py)                  │   │
│  │  - REST API for feeds, books, settings          │   │
│  │  - Web interface (HTML/CSS/JS)                  │   │
│  │  - WebSocket for real-time logs                 │   │
│  └──────────────────────────────────────────────────┘   │
│                          │                                │
│  ┌───────────────────────┴───────────────────────────┐   │
│  │                                                      │  │
│  ▼                    ▼                     ▼          │  │
│ Parser Engine    Search Engine         Kindle Sender  │  │
│ - RSS parsing    - Anna's Archive      - SMTP driver  │  │
│ - Feed caching   - Book lookup         - Format conv. │  │
│ - Item tracking  - Cover fetch         - Dedup check  │  │
│                                                        │  │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Data Layer (JSON-based storage)                │   │
│  │  - settings.json (config)                      │   │
│  │  - feed_cache.json (RSS state)                │   │
│  │  - history.json (downloads)                   │   │
│  │  - library_metadata.json (book metadata)      │   │
│  └──────────────────────────────────────────────────┘   │
│                                                           │
└─────────────────────────────────────────────────────────┘
        │                        │
        ▼                        ▼
    User Library        Kindle Device
   (File System)       (Email Delivery)
```

### Data Flow: Feed → Download → Kindle

```
1. RSS Feed URL
   ↓
2. Parser reads feed
   ↓
3. For each item:
   a. Check if already processed
   b. Search Anna's Archive
   c. Download best match
   d. Enrich metadata (Goodreads API)
   e. Save to library
   f. Record in history
   ↓
4. Batch Email to Kindle:
   a. Check deduplication (already sent?)
   b. Convert format if needed
   c. Send via SMTP
   d. Mark as sent in cache
   ↓
5. Appear on Kindle device
```

---

## Advanced Features

### Deduplication System
- Tracks which books were sent to which Kindle email
- Prevents same book from being sent multiple times
- Can be manually reset via settings if needed
- Works across feed runs and user sessions

### Metadata Enrichment
- Fetches Goodreads data during feed processing
- Stores: ratings, genres, descriptions, publication date
- Adds book covers and thumbnails
- Enables smart library organization

### Background Jobs
- Non-blocking feed processing
- Configurable worker threads
- Real-time progress logs
- Graceful error handling

### Multi-User Support
- Individual save directories per user
- Per-user Kindle email addresses
- Per-feed auto-send preferences
- Separate feed configurations

---

## Troubleshooting

### Service won't start?
```bash
sudo journalctl -u goodbooks -n 50 | grep -i error
```

### Can't access web interface?
```bash
# Check if service is running
sudo systemctl status goodbooks

# Find listening port
sudo journalctl -u goodbooks | grep -i "port\|listening"
```

### Books not appearing in library?
1. Check feed logs in web interface
2. Verify Anna's Archive search works manually
3. Ensure library directory is readable/writable
4. Check system disk space

### Kindle not receiving books?
1. Verify SMTP settings in settings.json
2. Check Amazon account approvals for sender email
3. Review Kindle delivery logs: `sudo journalctl -u goodbooks | grep -i kindle`
4. Check spam folder on Kindle email account

### Slow performance?
- Adjust `max_concurrent_downloads` in settings (increase for faster downloads)
- Adjust `max_feed_workers` for parallel feed processing
- Check system disk I/O and memory usage

---

## File Structure

```
/usr/local/bin/GoodBooks/
├── app.py                          # Main Flask application
├── parser_engine.py               # RSS feed parser
├── search_engine.py               # Anna's Archive search
├── stealth_browser.py             # Playwright headless browser
├── settings_manager.py            # Settings/config management
├── logging_config.py              # Logging setup
├── venv/                          # Python virtual environment
├── templates/                     # HTML templates
│   ├── base.html
│   ├── index.html
│   ├── library.html
│   ├── feeds.html
│   ├── settings.html
│   └── history.html
├── static/                        # CSS, JavaScript
│   ├── style.css
│   └── settings.js
└── data/                          # User data
    ├── settings.json              # Configuration
    ├── history.json               # Download history
    ├── feed_cache.json            # Feed state
    └── library_metadata.json      # Book metadata

/etc/systemd/system/
└── goodbooks.service              # Systemd service definition

/home/username/GoodBooks/          # Your book library (configurable)
└── [Downloaded books and covers]
```

---

## Performance Tips

### Optimize for Your Setup
```json
{
  "max_feed_workers": 4,              // Increase for parallel feeds
  "max_concurrent_downloads": 3,      // Increase for faster bulk downloads
  "maintenance_interval_seconds": 600 // Reduce for more frequent updates
}
```

### Monitor Resource Usage
```bash
# Check memory/CPU
ps aux | grep "[g]oodbooks"

# Monitor disk usage
du -sh /usr/local/bin/GoodBooks
du -sh ~/GoodBooks

# Check network
sudo nethogs -p goodbooks  # (if installed)
```

---

## Security Notes

### SMTP Password Security
- Passwords stored in plain text in settings.json
- Consider using environment variables for production
- Restrict file permissions: `chmod 600 /usr/local/bin/GoodBooks/data/settings.json`

### Network Security
- Service listens on localhost by default
- For remote access, use reverse proxy (nginx) with SSL
- Restrict firewall access to trusted networks

### Kindle Email Spoofing
- Only sender emails approved by Amazon will work
- Add email to "Approved Personal Document E-mail List" in Amazon settings
- Cannot send from arbitrary addresses

---

## Uninstall

```bash
# Stop the service
sudo systemctl stop goodbooks

# Disable auto-start
sudo systemctl disable goodbooks

# Remove service file
sudo rm /etc/systemd/system/goodbooks.service
sudo systemctl daemon-reload

# Remove application
sudo rm -rf /usr/local/bin/GoodBooks

# Keep library (if desired)
# rm -rf ~/GoodBooks  # Only if you want to remove books too
```

---

## Support & Development

### Getting Logs for Debugging
```bash
# Full service logs
sudo journalctl -u goodbooks > goodbooks-logs.txt

# Export settings (without passwords)
cat /usr/local/bin/GoodBooks/data/settings.json | jq '.smtp.password = "*hidden*'

# Check Python version/packages
/usr/local/bin/GoodBooks/venv/bin/python3 --version
/usr/local/bin/GoodBooks/venv/bin/pip list
```

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| "Service failed to start" | Check logs: `journalctl -u goodbooks` |
| "Can't find books on Anna's Archive" | Verify book title/author spelling; site may be blocked in your region |
| "SMTP authentication failed" | Use app-specific password (Gmail); check credentials |
| "Kindle not receiving books" | Verify sender email approved in Amazon account; check spam |
| "High CPU/Memory usage" | Reduce `max_concurrent_downloads` and `max_feed_workers` |

---

## License & Credits

GoodBooks combines:
- **Flask** for web framework
- **Feedparser** for RSS parsing
- **Playwright** for headless browsing
- **Calibre** for ebook format conversion
- **Anna's Archive** for book search
- **Goodreads** API for metadata

---

## Changelog

### Version 1.0
- Initial release
- Core feed parsing and searching
- Kindle delivery with deduplication
- Web interface with library browsing
- Metadata enrichment system
- Background job processing

---

## Next Steps

1. **Complete Installation**: Run `./installer.sh`
2. **Configure Feeds**: Add your Goodreads RSS feeds
3. **Set Up Kindle**: Add SMTP and Kindle email settings
4. **Test**: Run a single feed manually
5. **Enjoy**: Let the books flow to your Kindle!

---

*Happy reading!* 📚🎉


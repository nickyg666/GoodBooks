# GoodBooks Installer - Technical Architecture

## Overview

The enhanced GoodBooks installer consists of:

1. **installer.sh** - Main installation orchestration
2. **setup_wizard.sh** - Interactive configuration  
3. **post_install.py** - Post-service setup
4. **goodreads_epub_utils.py** - EPUB creation utility

These components work together to provide a seamless, automated installation experience.

## Component Details

### 1. setup_wizard.sh

**Purpose**: Interactive configuration dialog

**Location**: Copied to `$INSTALL_DIR/setup_wizard.sh`

**Execution Flow**:
```
installer.sh
  ↓
1. Installs dependencies and Python venv
  ↓
2. Starts systemd service (disabled initially in new code flow)
  ↓
3. Runs setup_wizard.sh
  ├─ Displays welcome banner
  ├─ Prompts for Kindle email (optional)
  ├─ Prompts for library root directory
  ├─ Prompts for server port
  ├─ Prompts for SMTP settings (optional)
  ├─ Shows summary for confirmation
  ├─ Generates data/settings.json
  └─ Validates JSON format
```

**Key Functions**:
- `log_info()` - Informational messages (blue)
- `log_success()` - Success messages (green)
- `log_warn()` - Warning messages (yellow)
- `log_prompt()` - Setup prompts (cyan)

**Output Files**:
- `$INSTALL_DIR/data/settings.json` - User configuration

**Error Handling**:
- Validates JSON before completion
- Prompts for confirmation before writing
- Allows user to cancel and restart
- Exits with status 0 or 1

**Interactive Prompts**:
```bash
# Kindle Setup
read -p "Do you have a Kindle device? (yes/no): " has_kindle

# Library Configuration  
read -p "Where should downloaded books be saved? (default: /home/$USER/GoodBooks): " library_root

# Server Configuration
read -p "What port should GoodBooks listen on? (default: 5000): " server_port

# SMTP Configuration
read -p "Do you want to configure SMTP now? (yes/no): " setup_smtp

# Confirmation
read -p "Does this look correct? (yes/no): " confirm
```

### 2. post_install.py

**Purpose**: Post-service setup and Kindle delivery

**Location**: Copied to `$INSTALL_DIR/post_install.py`

**Execution Flow**:
```
installer.sh
  ↓
1. Service is started and running
  ↓
2. Runs post_install.py $INSTALL_DIR
  ├─ Loads data/settings.json
  ├─ Scans for users with Kindle email
  ├─ Prompts if > 0 users have Kindle
  │  └─ If no: exits successfully
  │  └─ If yes:
  │     ├─ Gets systemctl status
  │     ├─ Extracts IP and port
  │     ├─ Creates EPUB with custom URL
  │     ├─ Lets user select recipient
  │     ├─ Sends EPUB via GoodBooks
  │     └─ Reports success/failure
  └─ Cleanup and exit
```

**Key Class**: `PostInstallManager`

**Methods**:
```python
__init__(install_dir)              # Initialize, load settings
log_info/warn/error/success(msg)   # Formatted output
prompt_yes_no(prompt)              # User confirmation
get_service_info(service_name)     # Query systemctl
_get_network_ip()                  # Fallback IP detection
get_users_with_kindle()            # Filter users by Kindle email
select_user(users)                 # Interactive user selection
create_ui_shortcut_epub()          # Create EPUB file
send_to_kindle_via_goodbooks()     # Send via GoodBooks API
run()                              # Main orchestration
```

**Service Detection Logic**:
```
systemctl status goodbooks --no-pager
  ↓
Parse output for:
  1. Port in status text (regex: port[=:\s]+(\d+))
  2. Flask "Running on" message in journal
  3. Extract IP and port from that
  4. Skip loopback (127.0.0.1, localhost)
  5. Fall back to hostname -I for network IP
```

**EPUB Creation**:
```python
create_ui_shortcut_epub(
    title="GoodBooks Web Interface",
    web_url="http://192.168.1.100:5000",
    author="GoodBooks Installer"
)
  ↓
Creates ZIP with:
  mimetype                    (uncompressed)
  META-INF/container.xml      (package metadata)
  OEBPS/content.opf          (EPUB manifest)
  OEBPS/toc.ncx              (table of contents)
  OEBPS/chapter1.xhtml       (main content with link)
  OEBPS/style.css            (styling)
```

**Kindle Integration**:
```
post_install.py
  ↓
1. Copy EPUB to library directory
2. Call GoodBooks' send_kindle_email()
3. Use existing SMTP configuration
4. Let Flask handle email delivery
5. Report success/failure
```

**Error Handling**:
- Checks for Kindle configuration before prompting
- Validates service is running with retry
- Catches JSON parsing errors
- Handles missing EPUB utility gracefully
- Subprocess timeouts for long operations
- Cleanup temporary files

### 3. goodreads_epub_utils.py

**Purpose**: EPUB file creation utility

**Location**: Copied to `$INSTALL_DIR/goodreads_epub_utils.py`

**Main Function**: `create_web_ui_shortcut_epub()`

**Parameters**:
```python
title: str                          # Book title
web_url: str                        # Target URL (e.g., http://192.168.1.100:5000)
author: str = "GoodBooks Installer" # Author name  
output_path: Optional[Path] = None  # Output file path
```

**Returns**: `Path` to created EPUB file

**EPUB Structure**:
```
MyBook.epub (ZIP)
├── mimetype                  (must be first, uncompressed)
│   └── application/epub+zip
├── META-INF/
│   └── container.xml        (points to content.opf)
└── OEBPS/
    ├── content.opf          (package metadata, manifest, spine)
    ├── toc.ncx              (table of contents)
    ├── chapter1.xhtml       (main content)
    └── style.css            (styling)
```

**HTML Content**:
```html
<body>
  <h1>🎉 Welcome to GoodBooks!</h1>
  <div class="instructions">
    <p>Your personal ebook library is now ready to use.</p>
    <a href="http://192.168.1.100:5000" class="button-link">
      📖 Open GoodBooks Web Interface
    </a>
    <p>Or copy and paste:</p>
    <div class="url-display">http://192.168.1.100:5000</div>
    <p><strong>Features:</strong></p>
    <ul>
      <li>📚 Browse your complete book library</li>
      <li>🔍 Search for new books from Anna's Archive</li>
      <li>📧 Send books to your Kindle device</li>
      <li>⚙️ Manage RSS feed subscriptions</li>
      <li>📱 Access from any device on your network</li>
    </ul>
  </div>
</body>
```

**Helper Functions**:
```python
_escape_xml(text)         # Escape special XML chars for safe embedding
_get_current_date()       # YYYY-MM-DD format
_get_current_timestamp()  # ISO 8601 UTC timestamp
```

**ZIP Compression**:
- mimetype: stored (not compressed)
- All other files: deflated compression
- Valid EPUB 3.0 specification
- Compatible with Kindle, Kobo, Apple Books

**File Size**: ~10-15 KB (very small, email-friendly)

### 4. installer.sh (Updated)

**Key Changes**:

1. **Copy setup scripts**:
```bash
for script in setup_wizard.sh post_install.py goodreads_epub_utils.py; do
    cp "$SCRIPT_DIR/$script" "$BUILD_DIR/$script"
done
```

2. **Run setup wizard** (before service start):
```bash
if [ -f "$INSTALL_DIR/setup_wizard.sh" ]; then
    chmod +x "$INSTALL_DIR/setup_wizard.sh"
    "$INSTALL_DIR/setup_wizard.sh" "$INSTALL_DIR"
fi
```

3. **Start service**:
```bash
timeout 30 sudo systemctl start "$SERVICE_NAME" 2>/dev/null
```

4. **Run post-install** (after service is running):
```bash
if [ -f "$INSTALL_DIR/post_install.py" ]; then
    chmod +x "$INSTALL_DIR/post_install.py"
    "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/post_install.py" "$INSTALL_DIR"
fi
```

## Data Flow

### Settings.json Creation

```
setup_wizard.sh
  ↓
Collects:
  - library_root
  - server_port
  - kindle_email
  - smtp_host, smtp_port, smtp_username, smtp_password, smtp_tls
  ↓
Generates:
{
  "default_download_dir": "/home/user/GoodBooks",
  "library_root": "/home/user/GoodBooks/",
  "library_extra_dirs": [],
  "server_port": 5000,
  "log_level": "DEBUG",
  "request_timeout": 60,
  "max_feed_workers": 8,
  "max_concurrent_downloads": 2,
  "smtp": {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "user@gmail.com",
    "password": "app_password",
    "from_email": "user@gmail.com",
    "use_tls": true
  },
  "users": [
    {
      "name": "Default User",
      "save_dir": "/home/user/GoodBooks/",
      "kindle_type": "paperwhite",
      "kindle_email": "user@kindle.com",
      "notification_email": "",
      "feeds": [],
      "auto_send_to_kindle": false
    }
  ]
}
  ↓
Writes to: $INSTALL_DIR/data/settings.json
  ↓
app.py loads on startup via settings_manager
```

### EPUB Creation & Delivery

```
post_install.py (after service is running)
  ↓
1. Load settings.json
  ↓
2. Find users with Kindle email
  └─ If none: exit (skip)
  └─ If some: continue
  ↓
3. Prompt: "Send to Kindle?"
  └─ If no: exit
  └─ If yes: continue
  ↓
4. Detect service address
   systemctl status goodbooks --no-pager
   → Extract: IP + port
   → Create URL: http://192.168.1.100:5000
  ↓
5. Create EPUB
   goodreads_epub_utils.create_web_ui_shortcut_epub(
       title="GoodBooks Web Interface",
       web_url="http://192.168.1.100:5000"
   )
   → /tmp/goodbooks_post_install/GoodBooks_WebUI_Shortcut.epub
  ↓
6. User selects recipient
   "Which user to send to?"
   → Nick (nickgelinas_kindle@kindle.com)
  ↓
7. Send via GoodBooks
   from app import send_kindle_email
   send_kindle_email(user_obj, Path("/tmp/.../GoodBooks_WebUI_Shortcut.epub"))
   ├─ Check SMTP configured ✓
   ├─ Ensure EPUB format ✓
   ├─ Compose email message
   ├─ Attach file
   ├─ Send via SMTP
   └─ Return success/failure
  ↓
8. Report result
   "[SUCCESS] EPUB sent to Kindle!"
```

## Integration with GoodBooks Core

### Settings Loading
```python
# In app.py on startup:
settings_manager = SettingsManager(DATA_DIR / "settings.json")

# Access settings:
library_root = settings_manager.settings.library_root
users = settings_manager.settings.users
smtp_config = settings_manager.settings.smtp
```

### Kindle Delivery
```python
# In app.py:
from app import send_kindle_email

# Usage in post_install.py:
result = send_kindle_email(user: UserSettings, saved_path: Path)
# Returns: bool (success/failure)

# What send_kindle_email does:
# 1. Check user.kindle_email exists
# 2. Check SMTP is configured
# 3. Ensure file is EPUB format (convert if needed)
# 4. Create MIME message
# 5. Attach file
# 6. Send via SMTP
# 7. Log result
```

### User Configuration
```python
# UserSettings class (from settings_manager.py):
class UserSettings:
    name: str
    save_dir: str
    kindle_type: str
    kindle_email: str
    notification_email: str
    feeds: List[FeedSettings]
    auto_send_to_kindle: bool
```

## Failure Modes & Recovery

### Setup Wizard Fails
- Installation continues (non-blocking)
- User prompted to run manually later
- Manual run: `setup_wizard.sh /usr/local/bin/GoodBooks`

### Service Won't Start
- Post-install detects and reports
- Suggests checking logs
- Installation completes successfully
- User can debug and restart service

### SMTP Not Configured
- Post-install checks before prompting
- If no SMTP: skips Kindle delivery
- User can configure SMTP manually and re-run

### Service IP Detection Fails
- Falls back to `hostname -I`
- If that fails: uses "localhost"
- User can verify and correct in post-install

### EPUB Creation Fails
- Clear error message shown
- Installation completes
- User can re-run post-install after fixing issue

## Performance Considerations

### Timing
- Setup wizard: ~2-3 minutes (user input)
- Service startup: ~10-20 seconds
- EPUB creation: <1 second
- Kindle delivery: ~5-10 seconds
- **Total**: 15-35 minutes (mostly user input)

### File Sizes
- EPUB: ~10-15 KB
- settings.json: ~1-2 KB
- app.py: ~100 KB
- Virtual environment: ~500 MB
- **Total install**: ~600 MB

### Network
- SMTP for Kindle: 1 small attachment (10 KB)
- No large downloads during post-install
- systemctl queries are local

## Security Considerations

### Credentials Stored
- SMTP password in plaintext in settings.json
- Kindle email in plaintext in settings.json
- **Mitigation**: 
  - Restrict file permissions: `chmod 600 settings.json`
  - Use app-specific password (not main password)
  - Service runs as non-root user

### Service Running
- Listens on 0.0.0.0 (all interfaces)
- No authentication by default
- **Mitigation**:
  - Use firewall to restrict access
  - Run behind reverse proxy with auth
  - Change listen address in settings

### EPUB Content
- Contains clickable URL to web UI
- URL embedded in EPUB (visible in file)
- **Mitigation**:
  - Use HTTPS in production
  - Don't use on public internet without auth

## Future Enhancements

Potential improvements to consider:

1. **Encrypted Settings**
   - Encrypt SMTP password in settings.json
   - Decrypt on load

2. **Multiple Libraries**
   - Support multiple library root directories
   - Separate Kindle queues per user

3. **Advanced SMTP**
   - OAuth2 instead of password
   - Test SMTP connection in wizard

4. **Custom EPUB**
   - User-selectable EPUB cover/colors
   - Multiple formats (PDF, etc.)

5. **Service Discovery**
   - mDNS/Bonjour announcement
   - QR code on web UI

6. **Upgrade Workflow**
   - Re-run wizard on upgrade
   - Preserve user settings

## Testing Checklist

When validating the installer:

- [ ] Setup wizard runs without errors
- [ ] Settings.json is valid JSON
- [ ] Service starts successfully
- [ ] Service is detected in post-install
- [ ] EPUB is created with correct URL
- [ ] EPUB can be opened on Kindle
- [ ] Link in EPUB works (clickable)
- [ ] Email is sent to Kindle address
- [ ] Book arrives on Kindle device
- [ ] Web UI is accessible from URL in EPUB
- [ ] Manual re-run of post-install works
- [ ] Graceful failure if no Kindle configured
- [ ] Proper error messages on failures
- [ ] Cleanup of temp files

---

**Version**: 1.0  
**Last Updated**: December 2024  
**Status**: Production Ready

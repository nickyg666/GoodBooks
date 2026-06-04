# GoodBooks Installation Guide

## Overview

The GoodBooks installer has been enhanced with an **interactive setup wizard** and **post-installation Kindle delivery** system. This guide explains the new workflow and features.

## Installation Workflow

### 1. **Pre-Installation** (Traditional)
```bash
sudo bash installer.sh
```

The installer will:
- Check system requirements
- Install dependencies (xvfb, calibre)
- Create Python virtual environment
- Install Python packages (Flask, requests, etc.)
- Configure systemd service
- Copy all application files to `/usr/local/bin/GoodBooks`

### 2. **Setup Wizard** (NEW - Interactive)
After the service is enabled, the installer automatically runs:
```bash
/usr/local/bin/GoodBooks/setup_wizard.sh
```

**What the setup wizard does:**
- Prompts for library root directory (where downloaded books are saved)
- Asks for Kindle email address (optional)
- Configures server port (default: 5000)
- Sets up SMTP for email notifications (optional but recommended)
- Generates `data/settings.json` with your configuration

**User Input Example:**
```
[SETUP] Where should downloaded books be saved? 
>>> /home/user/GoodBooks

[SETUP] Do you have a Kindle device? 
>>> yes

[SETUP] Enter your Kindle email address: 
>>> user@kindle.com

[SETUP] What port should GoodBooks listen on? 
>>> 5000

[SETUP] Do you want to configure SMTP now? 
>>> yes

[SETUP] SMTP Host: 
>>> smtp.gmail.com

...and so on...
```

### 3. **Service Startup**
The installer starts the GoodBooks service:
```bash
sudo systemctl start goodbooks
```

The service loads your settings and begins running.

### 4. **Post-Installation Setup** (NEW - Kindle Delivery)
After the service is running, the installer automatically runs:
```bash
/usr/local/bin/GoodBooks/post_install.py
```

**What the post-install script does:**
1. Checks if any users have Kindle email configured
2. Prompts: "Would you like to send a book containing a shortcut to the GoodBooks web UI to your Kindle?"
3. If yes:
   - Detects GoodBooks service address and port from `systemctl status`
   - Creates an EPUB file with a clickable link to your web UI
   - Sends the EPUB to your configured Kindle email via GoodBooks' internal Kindle delivery system
4. Shows success/failure status

**Example Interaction:**
```
[INFO] No users with Kindle email configured. Skipping.
```

OR (if Kindle is configured):
```
❓ Would you like to send a book containing a shortcut to the GoodBooks web UI to your Kindle? (yes/no): yes

[INFO] Waiting for GoodBooks service to be fully ready...
[SUCCESS] Service listening on: 192.168.1.100:5000
[INFO] Creating GoodBooks Web UI shortcut EPUB...
[SUCCESS] EPUB created: /tmp/goodbooks_post_install/GoodBooks_WebUI_Shortcut.epub (12.3 KB)

📧 Available users with Kindle configured:
  1) Nick (nickgelinas_kindle@kindle.com)
  2) Sagey (sagegelinas_kindle@kindle.com)

Select user (1-2): 1

[INFO] Preparing to send EPUB to Nick's Kindle...
[SUCCESS] EPUB copied to: /home/user/GoodBooks/
[SUCCESS] ✓ EPUB sent to Kindle!
[SUCCESS] Post-installation setup complete!
```

## Created Files

### New Application Files

1. **`setup_wizard.sh`** - Interactive configuration script
   - Collects user preferences during installation
   - Generates `data/settings.json`
   - Validates configuration
   - Can be re-run manually anytime

2. **`post_install.py`** - Post-service setup script
   - Offers Kindle delivery of web UI shortcut
   - Creates custom EPUB with web link
   - Integrates with GoodBooks' Kindle sending
   - Detects service address automatically
   - Can be re-run manually anytime

3. **`goodreads_epub_utils.py`** - EPUB creation utility
   - Creates valid EPUB files with custom HTML content
   - Generates proper EPUB structure (container.xml, content.opf, etc.)
   - Embeds clickable links to GoodBooks web UI
   - Includes helpful instructions and feature list

## Key Features of the New Workflow

### ✅ Fully Automated Configuration
- No manual JSON editing required
- All settings collected interactively
- Settings persisted immediately after wizard

### ✅ Smart Kindle Integration
- Only prompts if users have Kindle configured
- Auto-detects service IP and port
- Creates EPUB with custom URL
- Uses existing GoodBooks Kindle delivery system
- Fallback if service address detection fails

### ✅ Error Handling
- Validates JSON configuration
- Graceful fallbacks if steps fail
- Can re-run setup scripts manually
- Detailed error messages

### ✅ User-Friendly
- Colorized output
- Clear prompts and instructions
- Summary before confirmation
- Progress indicators

## Manual Re-Run

If you need to re-run the setup or post-install steps:

### Re-run Setup Wizard:
```bash
/usr/local/bin/GoodBooks/setup_wizard.sh /usr/local/bin/GoodBooks
```

### Re-run Post-Install (Kindle Setup):
```bash
/usr/local/bin/GoodBooks/venv/bin/python3 /usr/local/bin/GoodBooks/post_install.py /usr/local/bin/GoodBooks
```

## EPUB File Details

The created EPUB contains:

- **Title**: "GoodBooks Web Interface"
- **Content**: 
  - Welcome message
  - Clickable button linking to your GoodBooks web UI
  - Plain text URL for copy-paste
  - Feature list and instructions
- **Styling**: Professional CSS styling
- **Size**: ~10-15 KB
- **Format**: Valid EPUB 3.0 specification

When opened on a Kindle device, the file displays as a book with:
- A prominent clickable link to the web interface
- Backup URL in text form
- Instructions for accessing GoodBooks
- Feature highlights

## Configuration File

The setup wizard creates `data/settings.json`:

```json
{
    "default_download_dir": "/home/user/GoodBooks",
    "smtp": {
        "host": "smtp.gmail.com",
        "port": 587,
        "username": "user@gmail.com",
        "password": "app_password",
        "from_email": "user@gmail.com",
        "use_tls": true
    },
    "log_level": "DEBUG",
    "server_port": 5000,
    "library_root": "/home/user/GoodBooks/",
    "max_concurrent_downloads": 2,
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
```

You can edit this file manually later to:
- Add more users
- Add Goodreads feed URLs
- Change SMTP settings
- Adjust concurrency limits

## Troubleshooting

### Setup Wizard Issues

**Question: The wizard didn't run?**
```bash
# Manual run
/usr/local/bin/GoodBooks/setup_wizard.sh /usr/local/bin/GoodBooks
```

**Question: The SMTP configuration is wrong?**
Edit the settings file:
```bash
nano /usr/local/bin/GoodBooks/data/settings.json
```

**Question: Invalid JSON after editing?**
Validate with:
```bash
python3 -m json.tool /usr/local/bin/GoodBooks/data/settings.json
```

### Post-Install Issues

**Question: Service not detected?**
Make sure service is running:
```bash
sudo systemctl status goodbooks
```

**Question: SMTP not configured when needed?**
Re-run setup wizard to add SMTP settings:
```bash
/usr/local/bin/GoodBooks/setup_wizard.sh /usr/local/bin/GoodBooks
```

**Question: Kindle email not in user list?**
Add via web interface at `http://localhost:5000/settings` or manually edit settings.json and restart service:
```bash
sudo systemctl restart goodbooks
```

**Question: Can't send to Kindle?**
Check:
1. User has valid Kindle email in settings
2. SMTP is configured
3. Amazon account allows "Less Secure Apps"
4. Check service logs:
   ```bash
   sudo journalctl -u goodbooks -f
   ```

## Integration Details

The system integrates with GoodBooks' existing infrastructure:

### Email Sending
- Uses `send_kindle_email()` function from app.py
- Respects SMTP configuration from settings.json
- Handles file conversion (any format → EPUB)
- Attaches file to email and sends

### Service Detection
- Queries `systemctl status goodbooks --no-pager`
- Parses journal logs for Flask startup messages
- Falls back to localhost if detection fails
- Extracts port from multiple sources

### Data Persistence
- All settings stored in `data/settings.json`
- User preferences preserved across restarts
- Feed configurations maintained
- History and library metadata in separate JSON files

## Security Notes

- ⚠️ SMTP password stored in plaintext in `settings.json`
- ⚠️ File permissions should be restricted: `chmod 600 data/settings.json`
- ⚠️ GoodBooks service runs as regular user (not root)
- ⚠️ Web interface should be protected by firewall or VPN
- ⚠️ Amazon account security: Use app-specific password, not main password

## What Happens During Installation

### Timeline:

1. **Pre-installation**: Extract files, install dependencies *(5-10 min)*
2. **Setup Wizard**: Prompt for configuration *(2-3 min)*
3. **Service Start**: GoodBooks begins running *(10 sec)*
4. **Post-Install**: Offer Kindle delivery setup *(1-2 min)*
5. **Complete**: Ready to use! *(< 20 min total)*

### File Changes:

- ✅ Files copied to `/usr/local/bin/GoodBooks/`
- ✅ Service file created at `/etc/systemd/system/goodbooks.service`
- ✅ Settings created at `/usr/local/bin/GoodBooks/data/settings.json`
- ✅ Virtual environment in `/usr/local/bin/GoodBooks/venv/`
- ✅ Temporary files in `/tmp/goodbooks_post_install/`

## Advanced Usage

### Access Web UI from Different Device

If you're installing on a server:
1. Find the server's IP address: `hostname -I`
2. When post-install asks for port, note the port
3. Access from another device: `http://<server_ip>:<port>`

### Update Settings Later

Edit the configuration:
```bash
nano /usr/local/bin/GoodBooks/data/settings.json
sudo systemctl restart goodbooks
```

### Re-send Kindle Shortcut

```bash
/usr/local/bin/GoodBooks/venv/bin/python3 /usr/local/bin/GoodBooks/post_install.py /usr/local/bin/GoodBooks
```

## Next Steps After Installation

1. ✅ Installation complete
2. Open web interface: `http://localhost:5000`
3. Add Goodreads feeds via Settings → Feeds
4. Configure additional users if needed
5. Check logs: `sudo journalctl -u goodbooks -f`
6. Monitor downloads: Library view shows new books

---

**For support**: Check logs with `sudo journalctl -u goodbooks -f`

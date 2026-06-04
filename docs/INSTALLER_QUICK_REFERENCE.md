# GoodBooks Installer - Quick Reference

## Files Overview

### New Components
| File | Purpose | Size | Type |
|------|---------|------|------|
| `goodreads_epub_utils.py` | EPUB file creation | 7.4 KB | Python |
| `setup_wizard.sh` | Interactive setup | 9.2 KB | Bash |
| `post_install.py` | Kindle delivery | 14.3 KB | Python |
| `installer.sh` | Main orchestrator | 19.1 KB | Bash (UPDATED) |

### Documentation
| File | Purpose | Audience |
|------|---------|----------|
| `INSTALLER_GUIDE.md` | User-friendly walkthrough | End users |
| `INSTALLER_TECHNICAL.md` | Architecture & implementation | Developers |
| `INSTALLER_IMPLEMENTATION.md` | What was added | Project managers |

## Installation Command

```bash
sudo bash installer.sh
```

## What Happens

```
1. Installs system dependencies
2. Sets up Python virtual environment
3. Installs Python packages
4. Prompts for configuration (setup_wizard.sh)
5. Starts GoodBooks service
6. Offers to send Kindle shortcut (post_install.py)
7. Done!
```

## Setup Wizard Questions

```
Library directory: (where books are saved)
Kindle device: (yes/no)
Kindle email: (if yes to above)
Server port: (default: 5000)
Configure SMTP: (yes/no)
SMTP settings: (if yes to above)
```

## Manual Scripts

### Re-run Setup
```bash
/usr/local/bin/GoodBooks/setup_wizard.sh /usr/local/bin/GoodBooks
```

### Re-run Kindle Setup
```bash
/usr/local/bin/GoodBooks/venv/bin/python3 \
  /usr/local/bin/GoodBooks/post_install.py \
  /usr/local/bin/GoodBooks
```

## Configuration File

**Location**: `/usr/local/bin/GoodBooks/data/settings.json`

**Created by**: `setup_wizard.sh`

**Loaded by**: `app.py` on startup

**Edit with**: `nano /usr/local/bin/GoodBooks/data/settings.json`

**Validate with**: `python3 -m json.tool /usr/local/bin/GoodBooks/data/settings.json`

**Restart after editing**: `sudo systemctl restart goodbooks`

## Service Commands

```bash
# View status
sudo systemctl status goodbooks

# View logs (live)
sudo journalctl -u goodbooks -f

# View recent logs
sudo journalctl -u goodbooks -n 50

# Restart service
sudo systemctl restart goodbooks

# Stop service
sudo systemctl stop goodbooks

# Start service
sudo systemctl start goodbooks
```

## EPUB Details

- **Created by**: `post_install.py` using `goodreads_epub_utils.py`
- **Sent via**: GoodBooks' `send_kindle_email()` function
- **Format**: EPUB 3.0 specification
- **Size**: ~10-15 KB
- **Content**: Web UI link + instructions

## Troubleshooting

### Setup Wizard Didn't Run
```bash
/usr/local/bin/GoodBooks/setup_wizard.sh /usr/local/bin/GoodBooks
```

### Invalid JSON in settings.json
```bash
nano /usr/local/bin/GoodBooks/data/settings.json
# Edit file, then validate:
python3 -m json.tool /usr/local/bin/GoodBooks/data/settings.json
```

### Service Won't Start
```bash
sudo journalctl -u goodbooks -n 50
# Check logs for errors, fix, restart:
sudo systemctl restart goodbooks
```

### Kindle Email Not Detected
1. Edit settings.json
2. Add `kindle_email` to user
3. Restart service: `sudo systemctl restart goodbooks`
4. Re-run post_install.py

### Can't Send to Kindle
- Check SMTP is configured: `cat /usr/local/bin/GoodBooks/data/settings.json | grep -A5 smtp`
- Check logs: `sudo journalctl -u goodbooks -f`
- Test SMTP credentials
- Enable "Less Secure Apps" on Amazon account

## Key Integration Points

```python
# In app.py
from goodreads_epub_utils import create_web_ui_shortcut_epub
from app import send_kindle_email

# Used by post_install.py
epub_path = create_web_ui_shortcut_epub(...)
send_kindle_email(user, epub_path)
```

## File Permissions

```bash
# Secure settings file (passwords in plaintext!)
chmod 600 /usr/local/bin/GoodBooks/data/settings.json

# Ensure service can read
chown $USER:$USER /usr/local/bin/GoodBooks/data/settings.json
```

## Network Diagram

```
┌─────────────────────────────────┐
│   Running GoodBooks Service     │
│  (systemd goodbooks)            │
│  Listening on: 0.0.0.0:5000     │
└──────────────┬──────────────────┘
               │
         ┌─────┴─────┐
         │            │
    ┌────▼──────┐  ┌─▼────────────────┐
    │ Web UI    │  │ Kindle Delivery  │
    │ (Flask)   │  │ (SMTP)           │
    │ HTTP      │  │                  │
    └───────────┘  └──────────────────┘
```

## Storage Structure

```
/usr/local/bin/GoodBooks/
├── app.py                          (Flask app)
├── search_engine.py                (Anna's Archive)
├── parser_engine.py                (Feed parsing)
├── stealth_browser.py              (Cloudflare bypass)
├── settings_manager.py             (Config loading)
├── logging_config.py               (Logging setup)
├── goodreads_epub_utils.py         (EPUB creation) ← NEW
├── setup_wizard.sh                 (Interactive setup) ← NEW
├── post_install.py                 (Kindle delivery) ← NEW
├── installer.sh                    (Installer) ← UPDATED
├── data/
│   ├── settings.json               (User configuration)
│   ├── library_metadata.json       (Book library)
│   ├── history.json                (User history)
│   ├── search_cache.json           (Search cache)
│   └── feed_cache.json             (Feed cache)
├── templates/                      (HTML templates)
├── static/                         (CSS, JS)
├── venv/                           (Python environment)
└── goodbooks.service               (Systemd file)
```

## Success Indicators

After installation, you should see:

1. ✅ `systemctl status goodbooks` shows "running"
2. ✅ `sudo journalctl -u goodbooks` shows service started
3. ✅ Access `http://localhost:5000` in browser
4. ✅ EPUB sent to your Kindle email
5. ✅ Shortcut book appears on Kindle device

## Performance

| Step | Duration |
|------|----------|
| System setup | 5-10 min |
| Setup wizard | 2-3 min |
| Service start | 10 sec |
| EPUB creation | <1 sec |
| Kindle delivery | 5-10 sec |
| **Total** | **15-35 min** |

## Important Notes

⚠️ **Before installing**:
- Have Goodreads account ready
- Know your Kindle email address
- Prepare SMTP credentials (Gmail app password recommended)
- Decide on library location

⚠️ **After installing**:
- Check "Less Secure Apps" on Amazon account
- Don't share settings.json (contains passwords!)
- Use firewall to protect web UI
- Monitor logs for errors

## Support Resources

- **User Guide**: `INSTALLER_GUIDE.md`
- **Technical Details**: `INSTALLER_TECHNICAL.md`
- **Implementation Notes**: `INSTALLER_IMPLEMENTATION.md`
- **Service Logs**: `sudo journalctl -u goodbooks -f`
- **Configuration File**: `/usr/local/bin/GoodBooks/data/settings.json`

---

**Version**: 1.0  
**Status**: Production Ready  
**Last Updated**: December 2024

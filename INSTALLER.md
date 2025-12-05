# GoodBooks Installer Guide

## Overview

The `installer.sh` script provides a complete, production-ready installation of GoodBooks on Ubuntu/Debian systems. It automates:

- System dependency installation (xvfb, calibre)
- Python virtual environment creation and dependency installation
- Application installation to `/usr/local/bin/GoodBooks`
- Systemd service configuration and setup
- Automatic service startup and monitoring

## Prerequisites

- Ubuntu 18.04 or later (or any Debian-based system)
- Sudo access (required for system package installation and service setup)
- Python 3.8 or later
- At least 2GB free disk space

## Installation

### Step 1: Prepare the Installer

```bash
cd /path/to/goodbooks/source
chmod +x installer.sh
```

### Step 2: Run the Installer

```bash
./installer.sh
```

**Important**: Do NOT run with `sudo`. The script will request elevation when needed.

The installer will:
1. Detect the user running the script (non-root)
2. Check for required system packages
3. Install missing system dependencies (xvfb, calibre)
4. Create a Python virtual environment
5. Install all Python dependencies from `requirements.txt`
6. Generate a systemd service file
7. Install files to `/usr/local/bin/GoodBooks`
8. Configure and enable the systemd service
9. Start the service and verify it's running

## What Gets Installed

```
/usr/local/bin/GoodBooks/
├── app.py                          # Main application
├── venv/                           # Python virtual environment
│   ├── bin/
│   │   ├── python3                 # Python interpreter
│   │   ├── pip                     # Package manager
│   │   └── [other binaries]
│   └── lib/python*/site-packages/  # Installed packages
├── data/                           # Data directory (created at runtime)
│   ├── settings.json               # Configuration
│   ├── history.json                # Download history
│   └── library_metadata.json       # Book metadata
├── [all other project files]
└── goodbooks.service               # Systemd service template
```

## Systemd Service

The installer creates a systemd service file that:

- Runs GoodBooks under your user account (not root)
- Uses Xvfb for headless browser rendering
- Automatically restarts on failure
- Integrates with system logging (journalctl)
- Starts automatically on system boot

### Service Configuration

```
Service: goodbooks
File: /etc/systemd/system/goodbooks.service
User: [your username]
Working Directory: /usr/local/bin/GoodBooks
Command: xvfb-run -a -s "-screen 0 1280x1024x24" {venv python} app.py
```

## Usage After Installation

### View Service Status

```bash
sudo systemctl status goodbooks
```

### View Logs

```bash
# Real-time logs
sudo journalctl -u goodbooks -f

# Last 50 lines
sudo journalctl -u goodbooks -n 50

# Today's logs
sudo journalctl -u goodbooks --since today
```

### Control Service

```bash
# Start
sudo systemctl start goodbooks

# Stop
sudo systemctl stop goodbooks

# Restart
sudo systemctl restart goodbooks

# Reload configuration
sudo systemctl reload goodbooks
```

### Disable Auto-Start

```bash
sudo systemctl disable goodbooks
```

## Configuration

After installation, configure GoodBooks by editing:

```
/usr/local/bin/GoodBooks/data/settings.json
```

Key configuration items:

1. **SMTP Settings** - For Kindle email delivery
   - Host, port, username, password
   - From email address
   - Don't forget to enable "Less Secure Apps" on your Amazon account

2. **Users** - Add users with Kindle emails
   - Save directory for downloads
   - Kindle email address
   - Notification email addresses

3. **Feeds** - Configure RSS/HTML feeds
   - Feed URLs
   - Download formats (epub, mobi, etc.)
   - Auto-send to Kindle settings

4. **Library** - Configure library locations
   - Master library directory
   - Additional library folders
   - Sort preferences

## Troubleshooting

### Service Won't Start

1. Check systemd logs:
   ```bash
   sudo journalctl -u goodbooks -n 100
   ```

2. Verify installation:
   ```bash
   ls -la /usr/local/bin/GoodBooks/
   ```

3. Check permissions:
   ```bash
   stat /usr/local/bin/GoodBooks
   ```

4. Test service file:
   ```bash
   systemctl show goodbooks
   ```

### Python Module Import Errors

The installer automatically checks for critical modules. If imports fail:

```bash
# Activate the venv and check
source /usr/local/bin/GoodBooks/venv/bin/activate
python3 -c "import flask; import requests; import feedparser"
```

### Xvfb Issues

If Xvfb isn't working:

```bash
# Verify xvfb is installed
which xvfb-run

# Check if X11 libraries are available
apt list --installed | grep -i xvfb
```

### Virtual Environment Issues

If the venv is corrupted:

1. Stop the service:
   ```bash
   sudo systemctl stop goodbooks
   ```

2. Backup and remove:
   ```bash
   sudo mv /usr/local/bin/GoodBooks /usr/local/bin/GoodBooks.broken
   ```

3. Re-run the installer:
   ```bash
   ./installer.sh
   ```

## Uninstallation

To remove GoodBooks:

```bash
# Stop and disable service
sudo systemctl stop goodbooks
sudo systemctl disable goodbooks

# Remove service file
sudo rm /etc/systemd/system/goodbooks.service
sudo systemctl daemon-reload

# Remove application
sudo rm -rf /usr/local/bin/GoodBooks

# Optional: Remove backup
sudo rm -rf /usr/local/bin/GoodBooks.backup.*
```

## Advanced Configuration

### Custom Xvfb Resolution

Edit `/etc/systemd/system/goodbooks.service` and modify the ExecStart line:

```ini
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1920x1080x24" ...
```

### Environment Variables

To set custom environment variables, add to the service file:

```ini
[Service]
Environment="FLASK_ENV=production"
Environment="LOG_LEVEL=INFO"
```

### Custom Port

Modify the Flask port in `data/settings.json`:

```json
{
  "server_port": 5000
}
```

Then restart:

```bash
sudo systemctl restart goodbooks
```

## Automation

The installer can be automated for multiple machines:

```bash
# Copy installer to multiple servers
for server in server1 server2 server3; do
    scp installer.sh user@$server:/tmp/
    ssh user@$server "cd /tmp && ./installer.sh"
done
```

## Support

For issues and support:

1. Check the logs: `sudo journalctl -u goodbooks -f`
2. Verify installation: `ls -la /usr/local/bin/GoodBooks/`
3. Check system requirements: `dpkg -l | grep -E "xvfb|calibre"`

## Version Information

- **Created**: 2025-12-04
- **Compatible with**: Ubuntu 18.04+, Debian 10+
- **Python**: 3.8+
- **System packages**: xvfb, calibre

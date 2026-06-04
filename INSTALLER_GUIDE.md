# GoodBooks Installer Guide

**Last Updated**: December 13, 2025

## Quick Start (30 seconds)

```bash
cd /path/to/goodbooks
chmod +x installer.sh
./installer.sh  # Do NOT use sudo - it will request elevation when needed
```

That's it! The installer will:
1. ✅ Install system dependencies (xvfb, calibre, python3-venv)
2. ✅ Create Python virtual environment
3. ✅ Install Python dependencies from requirements.txt
4. ✅ Create systemd service
5. ✅ Generate initial configuration
6. ✅ Start the service automatically

---

## Installation Process Details

### What the Installer Does

The `installer.sh` script performs the following steps:

1. **Pre-flight Checks**
   - Verifies running on Ubuntu/Debian
   - Checks for required system tools
   - Verifies non-root execution

2. **System Dependencies**
   - Installs: `xvfb`, `calibre`, `python3-venv`
   - Uses `sudo` only for apt operations
   - Verifies successful installation

3. **Python Environment Setup**
   - Creates `/usr/local/bin/GoodBooks` directory
   - Creates Python virtual environment
   - Installs all Python dependencies from `requirements.txt`
   - Verifies installation with test import

4. **Application Setup**
   - Copies all Python modules and templates
   - Creates `data/` directory for settings/metadata
   - Creates `logs/` directory for application logs

5. **Systemd Service Configuration**
   - Creates `/etc/systemd/system/goodbooks.service`
   - Configures to run as `goodbooks` user
   - Sets up Xvfb display for headless rendering
   - Enables automatic startup

6. **Service Initialization**
   - Starts the GoodBooks service
   - Displays service status
   - Shows web interface URL and logs location

---

## After Installation

### Access the Web Interface

Once installation completes, the application is available at:
- **Default**: `http://localhost:5000`
- **Check logs** for actual port: `sudo journalctl -u goodbooks | grep -i "port\|running"`

### Service Management

```bash
# Check service status
sudo systemctl status goodbooks

# View live logs (last 50 lines, follow mode)
sudo journalctl -u goodbooks -f -n 50

# Restart service
sudo systemctl restart goodbooks

# Stop service
sudo systemctl stop goodbooks

# Start service
sudo systemctl start goodbooks

# Disable auto-start on boot
sudo systemctl disable goodbooks

# Enable auto-start on boot
sudo systemctl enable goodbooks
```

### Verify Installation

```bash
# Check Python environment
/usr/local/bin/GoodBooks/venv/bin/python3 --version

# Check if service is running
curl http://localhost:5000/

# View service logs
sudo systemctl status goodbooks
```

---

## Configuration

### Initial Settings

After installation, configure the application via the web interface:
- Navigate to `http://localhost:5000/settings`
- Add users (Kindle email, library folders)
- Configure SMTP for Kindle delivery
- Set up feed subscriptions

### Manual Configuration

Edit configuration file directly:
```
/usr/local/bin/GoodBooks/data/settings.json
```

**Critical Settings**:
- `users[]` - User profiles with Kindle emails
- `smtp` - Email configuration for Kindle delivery
- `server_port` - Web interface port
- `library_root` - Primary book storage directory
- `default_download_dir` - Fallback download location

---

## Troubleshooting

### Service Won't Start

```bash
# Check systemd status
sudo systemctl status goodbooks

# View detailed logs
sudo journalctl -u goodbooks -n 100

# Check Python environment
/usr/local/bin/GoodBooks/venv/bin/python3 -c "import flask; print('✓ Flask OK')"

# Manually test app
cd /usr/local/bin/GoodBooks
source venv/bin/activate
python3 app.py
```

### Port Already in Use

```bash
# Find process using port 5000
sudo lsof -i :5000

# Kill the process
sudo kill -9 <PID>

# Or configure different port in settings.json
```

### Permission Errors

```bash
# Check directory ownership
ls -la /usr/local/bin/GoodBooks/

# Should be owned by goodbooks user and group
# If not, fix permissions:
sudo chown -R goodbooks:goodbooks /usr/local/bin/GoodBooks
```

### Xvfb Display Issues

```bash
# Check if Xvfb is running
ps aux | grep Xvfb

# Verify xvfb-run is available
which xvfb-run

# Test manually
xvfb-run -a calibredb --help
```

---

## Common Tasks

### View All Logs

```bash
# Last 50 lines
sudo journalctl -u goodbooks -n 50

# Last hour
sudo journalctl -u goodbooks --since "1 hour ago"

# Follow live logs
sudo journalctl -u goodbooks -f

# Save to file
sudo journalctl -u goodbooks > ~/goodbooks_logs.txt
```

### Backup Settings

```bash
cp /usr/local/bin/GoodBooks/data/settings.json \
   /usr/local/bin/GoodBooks/data/settings.json.backup
```

### Reset Settings

```bash
# Stop service
sudo systemctl stop goodbooks

# Remove settings (will recreate on restart)
rm /usr/local/bin/GoodBooks/data/settings.json

# Restart service
sudo systemctl start goodbooks
```

### Restart Application

```bash
sudo systemctl restart goodbooks

# Wait for it to come back online
sleep 5
curl http://localhost:5000/
```

---

## Uninstall

Complete removal of GoodBooks:

```bash
# Stop the service
sudo systemctl stop goodbooks

# Disable auto-start
sudo systemctl disable goodbooks

# Remove systemd service file
sudo rm /etc/systemd/system/goodbooks.service

# Reload systemd daemon
sudo systemctl daemon-reload

# Remove application directory
sudo rm -rf /usr/local/bin/GoodBooks

# Optional: Remove goodbooks user/group if created
sudo userdel -r goodbooks 2>/dev/null || true
```

---

## File Structure

After installation:

```
/usr/local/bin/GoodBooks/
├── app.py                      # Main Flask application
├── requirements.txt            # Python dependencies
├── installer.sh               # Installation script
├── setup_wizard.sh            # Configuration wizard (if used)
├── venv/                      # Python virtual environment
│   └── bin/
│       ├── python3            # Python interpreter
│       └── pip                # Package manager
├── data/
│   ├── settings.json          # Application configuration
│   ├── library_metadata.json  # Book metadata cache
│   └── history.json           # User activity history
├── logs/
│   ├── info.log              # Application logs
│   └── debug.log             # Debug logs
├── static/                    # Web assets (CSS, JS, images)
├── templates/                 # HTML templates
├── docs/                      # Documentation
└── archived/                  # Archived documentation

/etc/systemd/system/
└── goodbooks.service         # Systemd service configuration
```

---

## System Requirements

### Minimum Specifications

- **OS**: Ubuntu 20.04 LTS or newer, Debian 10+
- **CPU**: 2 cores (4 recommended for multiple feeds)
- **RAM**: 2GB (4GB+ recommended for concurrent downloads)
- **Disk**: 10GB+ for books and metadata

### Required Packages

Installer automatically installs:
- `xvfb` - Virtual display for headless rendering
- `calibre` - EPUB conversion and Kindle delivery
- `python3-venv` - Python virtual environments

### Python Requirements

Specified in `requirements.txt`:
- Flask (web framework)
- Requests (HTTP client)
- BeautifulSoup4 (HTML parsing)
- Pillow (image processing)
- And 15+ additional packages

---

## Advanced Configuration

### Custom Installation Location

The installer uses `/usr/local/bin/GoodBooks` by default. To use a different location:

```bash
# Edit installer.sh before running
nano installer.sh

# Change this line:
# INSTALL_DIR="/usr/local/bin/GoodBooks"

# Then run the installer
./installer.sh
```

### Running Without Systemd

```bash
# If systemd is not available, run manually:
cd /usr/local/bin/GoodBooks
source venv/bin/activate
python3 app.py

# Or with Xvfb:
xvfb-run -a python3 app.py
```

### Docker Deployment

For containerized deployment, use the application code with Docker. Contact maintainers for Docker image availability.

---

## Support & Troubleshooting

### Check Installation Logs

Installer creates logs during execution. For manual troubleshooting:

1. **Check system packages**:
   ```bash
   dpkg -l | grep -E "xvfb|calibre|python3-venv"
   ```

2. **Check Python environment**:
   ```bash
   /usr/local/bin/GoodBooks/venv/bin/python3 --version
   /usr/local/bin/GoodBooks/venv/bin/pip list
   ```

3. **Test Flask import**:
   ```bash
   /usr/local/bin/GoodBooks/venv/bin/python3 -c "import flask; print(flask.__version__)"
   ```

4. **Check systemd service**:
   ```bash
   sudo systemctl cat goodbooks
   sudo systemctl status goodbooks
   ```

### Common Issues & Fixes

| Issue | Solution |
|-------|----------|
| Service won't start | Check logs: `sudo journalctl -u goodbooks -n 50` |
| Port already in use | Change port in settings.json or kill process on port |
| Settings not saving | Check file permissions: `sudo chown -R goodbooks:goodbooks /usr/local/bin/GoodBooks` |
| Python import errors | Reinstall venv: `./installer.sh` |
| Web interface not accessible | Verify port: `sudo lsof -i :5000` |
| Kindle delivery not working | Check SMTP settings and user Kindle email addresses |

---

## Documentation References

- **QUICKSTART.md** - 30-second setup guide
- **INSTALLER_TECHNICAL.md** - Technical architecture details
- **INSTALLER_ARCHITECTURE.md** - Component design
- **docs/README.md** - Full documentation index
- **archived/** - Legacy documentation and implementation notes

---

## Version History

- **2025-12-13** - Updated with current features and Goodreads integration
- **2025-12-09** - Added pagination support for genre lists
- **2025-12-08** - UI improvements and fixes
- **2025-12-06** - Initial comprehensive installer

For latest updates, check the docs folder.

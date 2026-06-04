# GoodBooks Quick Start Guide

## Installation (One Command)

```bash
cd /path/to/goodbooks
chmod +x installer.sh
./installer.sh  # Do NOT use sudo
```

## After Installation

The application is installed to: `/usr/local/bin/GoodBooks`

Service name: `goodbooks`

## Common Commands

```bash
# Check if running
sudo systemctl status goodbooks

# View live logs
sudo journalctl -u goodbooks -f

# Start/stop/restart
sudo systemctl start goodbooks
sudo systemctl stop goodbooks
sudo systemctl restart goodbooks

# Disable auto-start
sudo systemctl disable goodbooks

# Enable auto-start
sudo systemctl enable goodbooks
```

## Configuration

Edit: `/usr/local/bin/GoodBooks/data/settings.json`

Critical settings:
- SMTP configuration (for Kindle email delivery)
- User Kindle email addresses
- Feed URLs
- Library directories

## Troubleshooting

### Service won't start?
```bash
sudo journalctl -u goodbooks -n 50
```

### Check Python venv?
```bash
/usr/local/bin/GoodBooks/venv/bin/python3 --version
```

### Reinstall after errors?
```bash
sudo systemctl stop goodbooks
sudo rm -rf /usr/local/bin/GoodBooks
./installer.sh
```

## File Locations

```
/usr/local/bin/GoodBooks/
├── app.py (main application)
├── venv/ (Python environment)
└── data/ (settings, history, metadata)

/etc/systemd/system/goodbooks.service
```

## Web Interface

Once running, check logs for the port:
```bash
sudo journalctl -u goodbooks | grep -i "port\|running"
```

Default is port 5000: `http://localhost:5000`

## Uninstall

```bash
sudo systemctl stop goodbooks
sudo systemctl disable goodbooks
sudo rm /etc/systemd/system/goodbooks.service
sudo systemctl daemon-reload
sudo rm -rf /usr/local/bin/GoodBooks
```

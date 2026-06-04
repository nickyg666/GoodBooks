# GoodBooks Deployment Checklist

## Pre-Installation

- [ ] Ubuntu/Debian system available (18.04+)
- [ ] User account ready (non-root)
- [ ] Sudo access available
- [ ] Python 3.8+ installed
- [ ] 2GB+ free disk space available
- [ ] Internet connectivity for package downloads

## During Installation

- [ ] Run installer.sh without sudo
- [ ] Installer detects your username
- [ ] System packages install successfully (xvfb, calibre)
- [ ] Python virtual environment created
- [ ] All Python dependencies installed
- [ ] Service file generated and installed
- [ ] Application files copied to `/usr/local/bin/GoodBooks`
- [ ] Systemd service enabled and started

## Post-Installation Verification

- [ ] Service is running: `sudo systemctl status goodbooks`
- [ ] Logs are clean: `sudo journalctl -u goodbooks`
- [ ] Python venv works: `/usr/local/bin/GoodBooks/venv/bin/python3 -V`
- [ ] Critical modules load: `flask`, `requests`, `feedparser`, `lxml`, `playwright`
- [ ] Playwright browsers installed
- [ ] Data directory exists: `/usr/local/bin/GoodBooks/data`
- [ ] Service file in place: `/etc/systemd/system/goodbooks.service`

## Configuration Setup

- [ ] Edit `/usr/local/bin/GoodBooks/data/settings.json`
- [ ] Configure SMTP settings
  - [ ] Host (e.g., smtp.gmail.com)
  - [ ] Port (usually 587)
  - [ ] Username (email address)
  - [ ] Password (Gmail: use app-specific password)
  - [ ] From email address
- [ ] Add at least one user account
  - [ ] Username
  - [ ] Save directory
  - [ ] Kindle email address
  - [ ] Notification email (optional)
- [ ] Configure library locations
  - [ ] Master library directory
  - [ ] Extra directories (optional)
- [ ] Add feed URLs
  - [ ] At least one Goodreads RSS feed
  - [ ] Set download formats
  - [ ] Configure auto-send if desired

## Amazon Account Setup

- [ ] Log in to Amazon Account settings
- [ ] Go to "Devices and Content"
- [ ] Go to "Content Library" > "Manage Your Content and Devices"
- [ ] Go to "Preferences" > "Personal Document Settings"
- [ ] In "Approved Personal Document E-mail List", add the SMTP from_email
- [ ] Save changes

## Testing

- [ ] Access web interface (check logs for port)
  ```bash
  sudo journalctl -u goodbooks | grep -i port
  ```
- [ ] Log into web interface
- [ ] Configure first feed
- [ ] Run manual feed parse
  - [ ] Click "Run Feeds" on History page
  - [ ] Wait for feed completion
  - [ ] Check if books appear in history
- [ ] Test direct download
  - [ ] Click "Direct DL" on history entry
  - [ ] Verify file downloads
- [ ] Test library
  - [ ] Browse library page
  - [ ] Check if downloaded books appear
  - [ ] Test metadata enrichment
- [ ] Test Kindle send (if configured)
  - [ ] Click "Send to Kindle" on history entry
  - [ ] Select user and confirm
  - [ ] Check Kindle device for receipt
  - [ ] Check spam folder if not received

## Performance Tuning (Optional)

- [ ] Adjust Xvfb resolution if needed
  ```bash
  # Edit /etc/systemd/system/goodbooks.service
  # Change: -s "-screen 0 1280x1024x24"
  # Then: sudo systemctl daemon-reload && sudo systemctl restart goodbooks
  ```
- [ ] Increase concurrent downloads if desired
  ```json
  {
    "max_concurrent_downloads": 2
  }
  ```
- [ ] Adjust feed worker count for parallel processing
  ```json
  {
    "max_feed_workers": 4
  }
  ```

## Monitoring Setup (Optional)

- [ ] Configure log rotation:
  ```bash
  sudo tee /etc/logrotate.d/goodbooks << EOF
  /var/log/goodbooks/*.log {
      weekly
      rotate 4
      compress
      delaycompress
      missingok
  }
  EOF
  ```

- [ ] Set up monitoring alerts (e.g., with monit or nagios)
  ```bash
  # Check if service is running
  systemctl is-active goodbooks
  ```

## Security Configuration (Optional)

- [ ] Configure firewall (if service exposes network)
  ```bash
  sudo ufw allow 5000/tcp
  ```
- [ ] Set up SSL/TLS (if exposing externally)
- [ ] Configure reverse proxy (nginx/apache)
- [ ] Restrict service permissions:
  ```bash
  sudo chmod 755 /usr/local/bin/GoodBooks
  ```

## Backup and Recovery

- [ ] Set up regular backups of configuration:
  ```bash
  # Add to crontab
  0 2 * * * tar -czf /backup/goodbooks-$(date +\%Y\%m\%d).tar.gz /usr/local/bin/GoodBooks/data
  ```

- [ ] Document recovery procedure
- [ ] Test backup restore

## Documentation

- [ ] Document custom configuration
- [ ] Note any non-standard settings
- [ ] Create runbook for common operations
- [ ] Document escalation contacts

## Final Checklist

- [ ] All tests passed
- [ ] Service stable for 24+ hours
- [ ] Logs show no errors
- [ ] Performance acceptable
- [ ] Backups verified
- [ ] Documentation complete
- [ ] Team trained on operations

## Deployment Signed Off

- [ ] Installer: __________________ Date: __________
- [ ] Verifier: __________________ Date: __________
- [ ] Approver: __________________ Date: __________

---

## Post-Deployment Monitoring

Monitor these items regularly:

1. Service health:
   ```bash
   sudo systemctl status goodbooks
   ```

2. Recent errors:
   ```bash
   sudo journalctl -u goodbooks -n 100 | grep -i error
   ```

3. Disk usage:
   ```bash
   du -sh /usr/local/bin/GoodBooks
   du -sh /usr/local/bin/GoodBooks/data
   ```

4. Service restarts:
   ```bash
   sudo journalctl -u goodbooks | grep -i "restart\|started"
   ```

5. Performance:
   ```bash
   ps aux | grep goodbooks
   ```

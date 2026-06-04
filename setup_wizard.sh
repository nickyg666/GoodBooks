#!/bin/bash

################################################################################
# GoodBooks Setup Wizard
# Interactive configuration during installation
# Creates settings.json with user preferences before starting the service
################################################################################

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Log functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_prompt() {
    echo -e "${CYAN}[SETUP]${NC} $1"
}

################################################################################
# Configuration
################################################################################

INSTALL_DIR="${1:-.}"
DATA_DIR="$INSTALL_DIR/data"
SETTINGS_FILE="$DATA_DIR/settings.json"
PYTHON_BIN="$INSTALL_DIR/venv/bin/python3"

# Check if settings already exists
if [ -f "$SETTINGS_FILE" ]; then
    log_warn "Settings file already exists: $SETTINGS_FILE"
    read -p "Do you want to reconfigure? (yes/no): " reconfigure
    if [ "$reconfigure" != "yes" ] && [ "$reconfigure" != "y" ]; then
        log_info "Keeping existing settings"
        exit 0
    fi
fi

################################################################################
# Welcome
################################################################################

clear
cat << 'WELCOME'
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║                    🎉 GoodBooks Setup Wizard 🎉                ║
║                                                                ║
║  Your personal Goodreads → Anna's Archive → Kindle platform    ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
WELCOME

log_prompt "Welcome to GoodBooks! This wizard will help you set up the application."
echo ""

################################################################################
# Get Kindle Information (Optional but Important)
################################################################################

log_prompt "Kindle Setup (Optional)"
echo "If you want to send books to your Kindle, we'll need some information."
read -p "Do you have a Kindle device? (yes/no): " has_kindle

kindle_info=""
if [ "$has_kindle" = "yes" ] || [ "$has_kindle" = "y" ]; then
    read -p "Enter your Kindle email address (from Amazon account): " kindle_email
    kindle_info="$kindle_email"
    log_success "Kindle email configured: $kindle_email"
else
    log_info "Skipping Kindle setup for now. You can configure this later via the web interface."
fi

################################################################################
# Library Configuration
################################################################################

log_prompt "Library Configuration"
read -p "Where should downloaded books be saved? (default: /home/$USER/GoodBooks): " library_root
library_root="${library_root:=/home/$USER/GoodBooks}"

# Create library directory
mkdir -p "$library_root"
log_success "Library directory set to: $library_root"

################################################################################
# Server Configuration
################################################################################

log_prompt "Server Configuration"
read -p "What port should GoodBooks listen on? (default: 5000): " server_port
server_port="${server_port:=5000}"

log_success "Server port set to: $server_port"

################################################################################
# SMTP Configuration (Optional)
################################################################################

log_prompt "Email Notifications (Optional)"
echo "SMTP settings are needed to send books to Kindle via email."
read -p "Do you want to configure SMTP now? (yes/no): " setup_smtp

smtp_host=""
smtp_port="587"
smtp_from=""
smtp_password=""
smtp_tls_value="true"

if [ "$setup_smtp" = "yes" ] || [ "$setup_smtp" = "y" ]; then
    read -p "SMTP Host (e.g., smtp.gmail.com): " smtp_host
    read -p "SMTP Port (e.g., 587): " smtp_port
    read -p "Email address (sender): " smtp_from
    read -p "Email password (or app password): " -s smtp_password
    echo ""
    read -p "Use TLS? (yes/no): " smtp_tls
    
    smtp_tls_lower="${smtp_tls:=yes}"
    if [ "$smtp_tls_lower" = "yes" ] || [ "$smtp_tls_lower" = "y" ]; then
        smtp_tls_value="true"
    else
        smtp_tls_value="false"
    fi
    
    log_success "SMTP configured"
else
    log_info "Skipping SMTP setup. You can configure this later via settings.json"
    smtp_host="smtp.gmail.com"
    smtp_port="587"
    smtp_from=""
    smtp_password=""
    smtp_tls_value="true"
fi

################################################################################
# Verify Configuration
################################################################################

clear
cat << VERIFY
╔════════════════════════════════════════════════════════════════╗
║                   Configuration Summary                        ║
╚════════════════════════════════════════════════════════════════╝

Library Configuration:
  • Save directory: $library_root
  • Kindle email: ${kindle_info:-"Not configured"}

Server Configuration:
  • Port: $server_port

SMTP Configuration:
  • Configured: ${setup_smtp:-"no"}

VERIFY

while true; do
    read -p "Does this look correct? (yes/no): " confirm
    if [ "$confirm" = "yes" ] || [ "$confirm" = "y" ]; then
        break
    elif [ "$confirm" = "no" ] || [ "$confirm" = "n" ]; then
        log_warn "Let's reconfigure. Starting over...\n"
        # Restart the configuration process
        exec "$0" "$INSTALL_DIR"
    else
        log_warn "Please answer 'yes' or 'no'"
    fi
done

################################################################################
# Generate settings.json
################################################################################

log_info "Generating configuration file..."

# Create data directory
mkdir -p "$DATA_DIR"

# Generate settings.json with user configuration
cat > "$SETTINGS_FILE" << 'SETTINGS_EOF'
{
    "default_download_dir": "PLACEHOLDER_LIBRARY_ROOT",
    "smtp": {
        "host": "PLACEHOLDER_SMTP_HOST",
        "port": PLACEHOLDER_SMTP_PORT,
        "username": "PLACEHOLDER_SMTP_FROM",
        "password": "PLACEHOLDER_SMTP_PASSWORD",
        "from_email": "PLACEHOLDER_SMTP_FROM",
        "use_tls": PLACEHOLDER_SMTP_TLS
    },
    "log_level": "DEBUG",
    "server_port": PLACEHOLDER_SERVER_PORT,
    "request_timeout": 60,
    "library_root": "PLACEHOLDER_LIBRARY_ROOT/",
    "library_extra_dirs": [],
    "library_items_per_page": 50,
    "library_default_sort": "date_newest",
    "max_feed_workers": 8,
    "max_concurrent_downloads": 2,
    "disable_background_jobs": false,
    "maintenance_interval_seconds": 900,
    "notification_emails": "",
    "kindle_emails": "",
    "users": [
        {
            "name": "Default User",
            "save_dir": "PLACEHOLDER_LIBRARY_ROOT/",
            "kindle_type": "paperwhite",
            "kindle_email": "PLACEHOLDER_KINDLE_EMAIL",
            "notification_email": "",
            "feeds": [],
            "auto_send_to_kindle": false
        }
    ]
}
SETTINGS_EOF

# Replace placeholders with actual values
sed -i "s|PLACEHOLDER_LIBRARY_ROOT|$library_root|g" "$SETTINGS_FILE"
sed -i "s|PLACEHOLDER_SMTP_HOST|$smtp_host|g" "$SETTINGS_FILE"
sed -i "s|PLACEHOLDER_SMTP_PORT|$smtp_port|g" "$SETTINGS_FILE"
sed -i "s|PLACEHOLDER_SMTP_FROM|$smtp_from|g" "$SETTINGS_FILE"
sed -i "s|PLACEHOLDER_SMTP_PASSWORD|$smtp_password|g" "$SETTINGS_FILE"
sed -i "s|PLACEHOLDER_SMTP_TLS|$smtp_tls_value|g" "$SETTINGS_FILE"
sed -i "s|PLACEHOLDER_SERVER_PORT|$server_port|g" "$SETTINGS_FILE"
sed -i "s|PLACEHOLDER_KINDLE_EMAIL|$kindle_info|g" "$SETTINGS_FILE"

if [ -f "$SETTINGS_FILE" ]; then
    log_success "Configuration file created: $SETTINGS_FILE"
    
    # Validate JSON
    if "$PYTHON_BIN" -m json.tool "$SETTINGS_FILE" > /dev/null 2>&1; then
        log_success "✓ Configuration is valid JSON"
    else
        log_error "Configuration file has invalid JSON format"
        exit 1
    fi
else
    log_error "Failed to create settings file"
    exit 1
fi

################################################################################
# Summary
################################################################################

echo ""
log_success "=========================================="
log_success "Setup Wizard Complete!"
log_success "=========================================="

cat << SUMMARY

📋 Your Configuration:
   • Library root: $library_root
   • Server port: $server_port
   • Settings file: $SETTINGS_FILE

🔑 Important Notes:
   • You can modify settings later at: $SETTINGS_FILE
   • Access the web UI at: http://localhost:$server_port
   • Add more users and feeds via the web interface
   
📚 Next Steps:
   1. GoodBooks will start automatically
   2. Open the web interface to add Goodreads feeds
   3. Configure additional users if needed
   4. Monitor logs: sudo journalctl -u goodbooks -f

SUMMARY

log_info "Setup wizard finished. Ready to start GoodBooks!"

exit 0

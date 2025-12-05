#!/bin/bash

################################################################################
# GoodBooks Installer
# Installs GoodBooks application with all dependencies to /usr/local/bin/GoodBooks
# Includes systemd service setup with Xvfb support
################################################################################

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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

################################################################################
# Pre-flight checks
################################################################################

log_info "Starting GoodBooks installation..."

# Check if running on Ubuntu/Debian
if ! grep -q "Ubuntu\|Debian" /etc/os-release; then
    log_warn "This script is optimized for Ubuntu/Debian systems"
fi

# Detect non-root user before elevation
if [ "$EUID" -eq 0 ]; then
   log_error "Please run this script without sudo. It will request elevation when needed."
   exit 1
fi

# Store original user and group
ORIGINAL_USER="$USER"
ORIGINAL_UID="$(id -u)"
ORIGINAL_GID="$(id -g)"
ORIGINAL_HOME="$(eval echo ~$ORIGINAL_USER)"

log_info "Installation will run as user: $ORIGINAL_USER (UID: $ORIGINAL_UID, GID: $ORIGINAL_GID)"

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/usr/local/bin/GoodBooks"
VENV_DIR="$INSTALL_DIR/venv"
SERVICE_NAME="goodbooks"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

log_info "Script directory: $SCRIPT_DIR"
log_info "Install directory: $INSTALL_DIR"
log_info "Virtual environment: $VENV_DIR"

################################################################################
# Update system and install system dependencies
################################################################################

log_info "Checking and installing system dependencies..."

# Check if running with sudo for apt operations
check_and_install_apt_packages() {
    local packages=("xvfb" "calibre" "python3-venv")
    local missing_packages=()
    
    for package in "${packages[@]}"; do
        if ! dpkg -l | grep -q "^ii  $package"; then
            missing_packages+=("$package")
        fi
    done
    
    if [ ${#missing_packages[@]} -gt 0 ]; then
        log_warn "Missing packages: ${missing_packages[@]}"
        log_info "Installing missing packages (requires sudo)..."
        echo "${missing_packages[@]}" | sudo xargs apt-get install -y
        
        if [ $? -eq 0 ]; then
            log_success "System packages installed"
        else
            log_error "Failed to install system packages"
            return 1
        fi
    else
        log_success "All system packages already installed"
    fi
    
    return 0
}

check_and_install_apt_packages || {
    log_warn "Some system packages failed to install, continuing anyway..."
}

################################################################################
# Prepare installation directory
################################################################################

log_info "Preparing installation directory..."

# Create temporary build directory
BUILD_DIR=$(mktemp -d)
trap "rm -rf $BUILD_DIR" EXIT

log_info "Using temporary build directory: $BUILD_DIR"

# Copy all project files to build directory
log_info "Copying project files..."
cp -r "$SCRIPT_DIR"/* "$BUILD_DIR/" 2>/dev/null || true

# Create app.py in build directory if it doesn't exist
if [ ! -f "$BUILD_DIR/app.py" ]; then
    log_error "app.py not found in source directory"
    exit 1
fi

################################################################################
# Create and setup Python virtual environment
################################################################################

log_info "Setting up Python virtual environment..."

# Check Python version
if ! command -v python3 &> /dev/null; then
    log_error "Python 3 is not installed"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
log_info "Using Python: $PYTHON_VERSION"

# Check if python3-venv is available for creating virtual environments
if ! python3 -m venv --help &>/dev/null; then
    log_error "python3-venv module is not available"
    log_info "Installing python3-venv..."
    echo "python3-venv" | sudo xargs apt-get install -y || {
        log_error "Failed to install python3-venv"
        exit 1
    }
fi

# Create venv in build directory temporarily for package installation
log_info "Creating virtual environment in build directory..."
python3 -m venv "$BUILD_DIR/venv"

# Activate venv
source "$BUILD_DIR/venv/bin/activate"

log_info "Upgrading pip, setuptools, and wheel..."
pip install --upgrade pip setuptools wheel 2>&1 | grep -E "(Successfully|Requirement already|ERROR)" || true

# Extract python requirements from requirements.txt
log_info "Installing Python dependencies..."
if [ -f "$BUILD_DIR/requirements.txt" ]; then
    # Filter out comments and system package instructions
    grep -v "^#" "$BUILD_DIR/requirements.txt" | \
    grep -v "^apt" | \
    grep -v "^pipx" | \
    grep -v "^$" | \
    while read -r line; do
        pip install "$line" 2>&1 | grep -E "(Successfully|Requirement already|ERROR)" || true
    done
    
    if [ $? -ne 0 ]; then
        log_warn "Some Python packages failed to install"
    else
        log_success "Python dependencies installed"
    fi
else
    log_warn "requirements.txt not found"
fi

# Verify critical packages are installed
CRITICAL_PACKAGES=("flask" "requests" "feedparser" "beautifulsoup4" "lxml" "playwright")
for package in "${CRITICAL_PACKAGES[@]}"; do
    if python3 -c "import ${package//-/_}" 2>/dev/null; then
        log_success "✓ $package installed"
    else
        log_warn "✗ $package not found"
    fi
done

# Playwright browser setup (non-interactive)
log_info "Installing Playwright browsers..."
python3 -m playwright install chromium 2>&1 | grep -E "(installed|Skipping|ERROR)" || true

# Deactivate venv
deactivate

################################################################################
# Clean up venv for distribution
################################################################################

log_info "Cleaning up virtual environment for distribution..."

# Remove unnecessary files to reduce size
find "$BUILD_DIR/venv" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR/venv" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$BUILD_DIR/venv" -type f -name "*.pyo" -delete 2>/dev/null || true

# Remove tests and development files
rm -rf "$BUILD_DIR/venv/lib/python*/site-packages/"*"/tests" 2>/dev/null || true
rm -rf "$BUILD_DIR/venv/lib/python*/site-packages/"*"/test" 2>/dev/null || true

log_success "Virtual environment cleaned"

################################################################################
# Create data directory template
################################################################################

log_info "Creating data directory structure..."
mkdir -p "$BUILD_DIR/data"
touch "$BUILD_DIR/data/.gitkeep"

################################################################################
# Copy setup and post-install scripts
################################################################################

log_info "Copying setup scripts..."

# Ensure setup_wizard.sh and post_install.py are in build directory
for script in setup_wizard.sh post_install.py goodreads_epub_utils.py; do
    if [ -f "$SCRIPT_DIR/$script" ]; then
        cp "$SCRIPT_DIR/$script" "$BUILD_DIR/$script"
        log_success "✓ $script copied"
    else
        log_warn "⚠ $script not found (will create placeholder)"
    fi
done

################################################################################
# Create systemd service file
################################################################################

log_info "Creating systemd service file..."

cat > "$BUILD_DIR/${SERVICE_NAME}.service" << 'SERVICEEOF'
[Unit]
Description=GoodBooks: Goodreads > Search Anna's Archive > Download > Send to Kindle > Notify Service (with Xvfb)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=PLACEHOLDER_USER
Group=PLACEHOLDER_GROUP
WorkingDirectory=PLACEHOLDER_INSTALL_DIR

# Use Xvfb for headless browser rendering (required for Playwright/Calibre)
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1280x1024x24" PLACEHOLDER_PYTHON_BIN PLACEHOLDER_INSTALL_DIR/app.py

# Service restart policy
Restart=on-failure
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

# Resource limits
LimitNOFILE=65536
LimitNPROC=4096

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=PLACEHOLDER_INSTALL_DIR/data PLACEHOLDER_HOME/.local/share/goodbooks

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=goodbooks

[Install]
WantedBy=multi-user.target
SERVICEEOF

log_success "Service file created"

################################################################################
# Install to system directory
################################################################################

log_info "Installing to $INSTALL_DIR..."

# Request sudo for system operations
sudo bash << SUDOEOF
set -e

# Remove existing installation if present
if [ -d "$INSTALL_DIR" ]; then
    echo "[INFO] Backing up existing installation..."
    sudo mv "$INSTALL_DIR" "${INSTALL_DIR}.backup.\$(date +%Y%m%d_%H%M%S)"
fi

# Create installation directory
mkdir -p "$INSTALL_DIR"
chown "$ORIGINAL_USER:$ORIGINAL_GID" "$INSTALL_DIR"

# Copy files from build directory
cp -r "$BUILD_DIR"/* "$INSTALL_DIR/" || {
    echo "[ERROR] Failed to copy files to $INSTALL_DIR"
    exit 1
}

# Set proper ownership recursively
chown -R "$ORIGINAL_USER:$ORIGINAL_GID" "$INSTALL_DIR"

# Set permissions
chmod 755 "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR/app.py"
chmod -R 755 "$INSTALL_DIR/venv/bin"

echo "[SUCCESS] Files installed to $INSTALL_DIR"

SUDOEOF

if [ $? -ne 0 ]; then
    log_error "Failed to install to system directory"
    exit 1
fi

log_success "Installation to $INSTALL_DIR completed"

# NOW perform service file configuration OUTSIDE sudo context
# This ensures variables are properly expanded
log_info "Configuring systemd service file..."

# Determine Python binary path
PYTHON_BIN="$INSTALL_DIR/venv/bin/python3"

log_info "Substituting values into service file:"
log_info "  User: $ORIGINAL_USER"
log_info "  Group: $ORIGINAL_GID"
log_info "  Install dir: $INSTALL_DIR"
log_info "  Python: $PYTHON_BIN"
log_info "  Home: $ORIGINAL_HOME"

# Update service file with actual values
sed -i "s|PLACEHOLDER_USER|$ORIGINAL_USER|g" "$INSTALL_DIR/${SERVICE_NAME}.service"
sed -i "s|PLACEHOLDER_GROUP|$ORIGINAL_GID|g" "$INSTALL_DIR/${SERVICE_NAME}.service"
sed -i "s|PLACEHOLDER_INSTALL_DIR|$INSTALL_DIR|g" "$INSTALL_DIR/${SERVICE_NAME}.service"
sed -i "s|PLACEHOLDER_PYTHON_BIN|$PYTHON_BIN|g" "$INSTALL_DIR/${SERVICE_NAME}.service"
sed -i "s|PLACEHOLDER_HOME|$ORIGINAL_HOME|g" "$INSTALL_DIR/${SERVICE_NAME}.service"

# Verify substitutions were successful
if grep -q "PLACEHOLDER_" "$INSTALL_DIR/${SERVICE_NAME}.service"; then
    log_error "Some placeholders were not substituted"
    exit 1
fi

log_success "Service file configured"

# Copy service file to systemd directory (this needs sudo)
sudo cp "$INSTALL_DIR/${SERVICE_NAME}.service" "$SERVICE_FILE"
sudo chown root:root "$SERVICE_FILE"
sudo chmod 644 "$SERVICE_FILE"

log_success "Service file installed to $SERVICE_FILE"

################################################################################
# Verify installation
################################################################################

log_info "Verifying installation..."

ERRORS=0

# Check if app.py exists and is executable
if [ ! -f "$INSTALL_DIR/app.py" ]; then
    log_error "app.py not found in installation directory"
    ((ERRORS++))
else
    log_success "✓ app.py found"
fi

# Check if venv exists
if [ ! -d "$INSTALL_DIR/venv" ]; then
    log_error "Virtual environment not found"
    ((ERRORS++))
else
    log_success "✓ Virtual environment found"
fi

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
    log_error "Service file not installed"
    ((ERRORS++))
else
    log_success "✓ Service file installed"
fi

# Verify venv python works
if "$INSTALL_DIR/venv/bin/python3" --version &>/dev/null; then
    VENV_PYTHON_VERSION=$("$INSTALL_DIR/venv/bin/python3" --version)
    log_success "✓ Venv Python works: $VENV_PYTHON_VERSION"
else
    log_error "Venv Python is not working"
    ((ERRORS++))
fi

# Try importing critical modules
log_info "Checking Python module imports..."
"$INSTALL_DIR/venv/bin/python3" -c "
import sys
modules = ['flask', 'requests', 'feedparser', 'bs4', 'lxml', 'playwright']
failed = []
for mod in modules:
    try:
        __import__(mod)
        print(f'✓ {mod}')
    except ImportError:
        print(f'✗ {mod}')
        failed.append(mod)
if failed:
    sys.exit(1)
" 2>&1 | while read line; do
    if [[ $line == ✓* ]]; then
        log_success "$line"
    elif [[ $line == ✗* ]]; then
        log_error "$line"
        ((ERRORS++))
    fi
done

################################################################################
# Setup systemd service
################################################################################

log_info "Setting up systemd service..."

sudo systemctl daemon-reload

# Enable service
if sudo systemctl enable "$SERVICE_NAME" &>/dev/null; then
    log_success "Service enabled"
else
    log_warn "Failed to enable service"
fi

################################################################################
# Run Setup Wizard (Interactive Configuration)
################################################################################

log_info "Starting interactive setup wizard..."
echo ""

if [ -f "$INSTALL_DIR/setup_wizard.sh" ]; then
    chmod +x "$INSTALL_DIR/setup_wizard.sh"
    "$INSTALL_DIR/setup_wizard.sh" "$INSTALL_DIR"
    
    if [ $? -eq 0 ]; then
        log_success "Setup wizard completed"
    else
        log_warn "Setup wizard did not complete successfully"
        log_info "You can run it manually later with: $INSTALL_DIR/setup_wizard.sh $INSTALL_DIR"
    fi
else
    log_warn "Setup wizard not found. Please configure $INSTALL_DIR/data/settings.json manually"
fi

################################################################################
# Test service and provide user options
################################################################################

log_info "Testing service..."

# Test if service can start (with timeout to prevent hanging)
if timeout 30 sudo systemctl start "$SERVICE_NAME" 2>/dev/null; then
    log_success "Service started successfully"
    
    # Check service status
    sleep 2
    if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
        log_success "Service is running"
        
        # Show service status
        log_info "Service status:"
        sudo systemctl status "$SERVICE_NAME" --no-pager | head -n 10
        
        log_success "✓ GoodBooks is running as a systemd service"
        log_info "Service name: $SERVICE_NAME"
        log_info "Service file: $SERVICE_FILE"
        log_info "Installation directory: $INSTALL_DIR"
        
        ################################################################################
        # Run Post-Installation Setup (Kindle Delivery & Documentation)
        ################################################################################
        
        log_info "Running post-installation setup..."
        echo ""
        
        if [ -f "$INSTALL_DIR/post_install.py" ]; then
            chmod +x "$INSTALL_DIR/post_install.py"
            "$INSTALL_DIR/venv/bin/python3" "$INSTALL_DIR/post_install.py" "$INSTALL_DIR"
            
            post_install_result=$?
            if [ $post_install_result -eq 0 ]; then
                log_success "Post-installation setup completed"
            else
                log_warn "Post-installation setup had issues"
                log_info "You can run it manually later with: $INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/post_install.py $INSTALL_DIR"
            fi
        else
            log_warn "Post-install script not found. Skipping setup."
        fi
        
        ################################################################################
        # Clean Up Installer Scripts and Files
        ################################################################################
        
        log_info "Cleaning up installer files..."
        
        # List of files to remove (installer-only scripts)
        installer_cleanup_files=(
            "setup_wizard.sh"
            "post_install.py"
            "goodreads_epub_utils.py"
            "goodbooks.service"
            "installer.sh"
            "set.py.bak"
        )
        
        for file in "${installer_cleanup_files[@]}"; do
            if [ -f "$INSTALL_DIR/$file" ]; then
                rm -f "$INSTALL_DIR/$file"
                log_success "Removed: $file"
            fi
        done
        
        # Verify critical files still exist
        critical_files=("app.py" "parser_engine.py" "search_engine.py" "settings_manager.py")
        for file in "${critical_files[@]}"; do
            if [ ! -f "$INSTALL_DIR/$file" ]; then
                log_error "ERROR: Critical file missing: $file"
            fi
        done
        
        log_success "Installer cleanup completed"
    else
        log_warn "Service started but may not be running"
        log_info "Showing recent logs:"
        sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager || true
    fi
else
    log_warn "Service failed to start initially"
    log_info "This may be expected for first-time setup (needs configuration)"
    log_info "Showing service logs:"
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager || true
fi

################################################################################
# Installation Summary and Next Steps
################################################################################

log_success "=========================================="
log_success "GoodBooks Installation Complete!"
log_success "=========================================="

cat << SUMMARY

📍 Installation Details:
   • Installation directory: $INSTALL_DIR
   • Service name: $SERVICE_NAME
   • Service file: $SERVICE_FILE
   • Running as user: $ORIGINAL_USER
   • Virtual environment: $INSTALL_DIR/venv

🔧 Useful Commands:
   • View logs:        sudo journalctl -u $SERVICE_NAME -f
   • Service status:   sudo systemctl status $SERVICE_NAME
   • Start service:    sudo systemctl start $SERVICE_NAME
   • Stop service:     sudo systemctl stop $SERVICE_NAME
   • Restart service:  sudo systemctl restart $SERVICE_NAME
   • View config:      cat $SERVICE_FILE

⚙️  Configuration:
   • Data directory: $INSTALL_DIR/data
   • Config file: $INSTALL_DIR/data/settings.json
   • History file: $INSTALL_DIR/data/history.json
   • Documentation: Check your library directory for GoodBooks_Complete_Guide.epub

📚 Generated Guides:
   • Complete guide with setup, deployment checklist, and troubleshooting
   • Guide saved to your configured library directory
   • Can be sent to Kindle or read on any ebook reader

⚡ Important Notes:
   • Xvfb (X virtual framebuffer) is used for headless Playwright/Calibre
   • Service will auto-restart on failure (max 5 restarts per 300 seconds)
   • All installer scripts have been removed from installation directory
   • Only core application files and configuration data remain
   • SMTP must be configured for Kindle email delivery to work
   • Amazon: Enable "Allow Less Secure Apps" on your Amazon account
   • Setup wizard stored your settings in: $INSTALL_DIR/data/settings.json

❓ Troubleshooting:
   • Check logs: sudo journalctl -u $SERVICE_NAME
   • Verify installation: ls -la $INSTALL_DIR
   • Check permissions: stat $INSTALL_DIR
   • Re-run setup wizard: $INSTALL_DIR/setup_wizard.sh $INSTALL_DIR
   • Restart service: sudo systemctl restart $SERVICE_NAME

SUMMARY

if [ $ERRORS -eq 0 ]; then
    log_success "Installation successful with no errors!"
    exit 0
else
    log_warn "Installation completed with $ERRORS warning(s)"
    log_info "Please check the logs above and troubleshoot as needed"
    exit 0
fi

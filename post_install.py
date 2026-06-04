#!/usr/bin/env python3
"""
Post-installation setup for GoodBooks
Initializes user directories, settings, and database
"""

import os
import sys
import json
from pathlib import Path

def setup_user_directories(install_dir):
    """Create necessary user directories"""
    dirs = [
        'logs',
        'data',
        'cache',
        'feeds'
    ]
    
    for dir_name in dirs:
        dir_path = Path(install_dir) / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"✓ Created directory: {dir_path}")

def initialize_settings(install_dir):
    """Initialize default settings"""
    settings_file = Path(install_dir) / 'data' / 'settings.json'
    
    default_settings = {
        "random_count": 5,
        "send_to_kindle": False,
        "kindle_email": "",
        "calibre_library_path": "",
        "theme": "light"
    }
    
    if not settings_file.exists():
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_file, 'w') as f:
            json.dump(default_settings, f, indent=2)
        print(f"✓ Created settings file: {settings_file}")
    else:
        print(f"✓ Settings file already exists: {settings_file}")

def main():
    if len(sys.argv) < 2:
        print("Usage: post_install.py <install_dir>")
        sys.exit(1)
    
    install_dir = sys.argv[1]
    
    print("[*] Running post-installation setup...")
    setup_user_directories(install_dir)
    initialize_settings(install_dir)
    print("[✓] Post-installation setup complete!")

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Post-installation script for GoodBooks.

Runs after the GoodBooks systemd service is started.
Offers to send a web UI shortcut EPUB to user's Kindle device.
Integrates with GoodBooks' existing Kindle delivery mechanism.

Usage: python3 post_install.py /usr/local/bin/GoodBooks
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, List

# Try to import the EPUB utility
try:
    from goodreads_epub_utils import (
        create_web_ui_shortcut_epub,
        create_comprehensive_documentation_epub
    )
except ImportError:
    print("[ERROR] goodreads_epub_utils.py not found. Cannot create EPUB files.")
    sys.exit(1)


class PostInstallManager:
    """Manages post-installation setup and Kindle delivery."""
    
    def __init__(self, install_dir: str):
        self.install_dir = Path(install_dir)
        self.data_dir = self.install_dir / "data"
        self.settings_file = self.data_dir / "settings.json"
        self.temp_epub_dir = Path("/tmp/goodbooks_post_install")
        self.temp_epub_dir.mkdir(parents=True, exist_ok=True)
        
        # Load settings
        self.settings = self._load_settings()
        
        # Colors for output
        self.BLUE = '\033[0;34m'
        self.GREEN = '\033[0;32m'
        self.YELLOW = '\033[1;33m'
        self.RED = '\033[0;31m'
        self.NC = '\033[0m'
    
    def _load_settings(self) -> dict:
        """Load settings.json."""
        try:
            with open(self.settings_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.log_error(f"Failed to load settings: {e}")
            return {}
    
    def log_info(self, msg: str):
        print(f"{self.BLUE}[INFO]{self.NC} {msg}")
    
    def log_success(self, msg: str):
        print(f"{self.GREEN}[SUCCESS]{self.NC} {msg}")
    
    def log_warn(self, msg: str):
        print(f"{self.YELLOW}[WARN]{self.NC} {msg}")
    
    def log_error(self, msg: str):
        print(f"{self.RED}[ERROR]{self.NC} {msg}")
    
    def prompt_yes_no(self, prompt: str) -> bool:
        """Prompt user for yes/no answer."""
        while True:
            response = input(f"\n{self.YELLOW}❓ {prompt} (yes/no): {self.NC}").strip().lower()
            if response in ('yes', 'y'):
                return True
            elif response in ('no', 'n'):
                return False
            else:
                print("Please answer 'yes' or 'no'")
    
    def get_service_info(self, service_name: str = "goodbooks") -> Optional[Tuple[str, int]]:
        """
        Extract listening address and port from systemd service status.
        
        Returns:
            Tuple of (hostname/ip, port) or None if service not running
        """
        try:
            result = subprocess.run(
                ["sudo", "systemctl", "status", service_name, "--no-pager"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            status_output = result.stdout + result.stderr
            
            # Try to find port in status output
            port_match = re.search(r'port[=:\s]+(\d+)', status_output, re.IGNORECASE)
            port = int(port_match.group(1)) if port_match else 5000
            
            # Try to find listening on in status output
            listen_match = re.search(r'listen[a-z\s=:]*(\d+\.\d+\.\d+\.\d+)[=:\s]+(\d+)', status_output, re.IGNORECASE)
            if listen_match:
                ip = listen_match.group(1)
                port = int(listen_match.group(2))
            else:
                # Check journal logs for the actual listening address
                journal_result = subprocess.run(
                    ["sudo", "journalctl", "-u", service_name, "-n", "50", "--no-pager"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                journal_output = journal_result.stdout + journal_result.stderr
                
                # Look for Flask startup message with address
                flask_match = re.search(r'Running on\s+(?:https?://)?(\d+\.\d+\.\d+\.\d+|\[[\da-f:]+\]|localhost|[\w\-\.]+):(\d+)', journal_output, re.IGNORECASE)
                if flask_match:
                    ip_raw = flask_match.group(1)
                    port = int(flask_match.group(2))
                    # Skip loopback
                    if ip_raw in ('127.0.0.1', 'localhost', '0.0.0.0'):
                        # Try to get actual network interface
                        ip = self._get_network_ip()
                    else:
                        ip = ip_raw
                else:
                    # Default to localhost, user will need to verify
                    ip = self._get_network_ip()
            
            return (ip, port)
        except Exception as e:
            self.log_warn(f"Failed to get service info: {e}")
            return None
    
    def _get_network_ip(self) -> str:
        """Try to get the actual network IP of the system."""
        try:
            result = subprocess.run(
                ["hostname", "-I"],
                capture_output=True,
                text=True,
                timeout=5
            )
            ips = result.stdout.strip().split()
            if ips:
                # Return first non-loopback IP
                for ip in ips:
                    if not ip.startswith('127.'):
                        return ip
                return ips[0]
        except Exception:
            pass
        
        # Fallback
        return "localhost"
    
    def get_users_with_kindle(self) -> List[dict]:
        """Get list of users that have Kindle email configured."""
        users = []
        try:
            for user in self.settings.get('users', []):
                if user.get('kindle_email'):
                    users.append(user)
        except Exception as e:
            self.log_warn(f"Failed to parse users: {e}")
        
        return users
    
    def select_user(self, users: List[dict]) -> Optional[dict]:
        """Let user select which user to send the book to."""
        if not users:
            return None
        
        if len(users) == 1:
            self.log_info(f"Using user: {users[0]['name']}")
            return users[0]
        
        print(f"\n{self.YELLOW}📧 Available users with Kindle configured:{self.NC}")
        for idx, user in enumerate(users, 1):
            print(f"  {idx}) {user['name']} ({user.get('kindle_email', 'N/A')})")
        
        while True:
            try:
                choice = input(f"\n{self.YELLOW}Select user (1-{len(users)}): {self.NC}").strip()
                idx = int(choice) - 1
                if 0 <= idx < len(users):
                    return users[idx]
            except (ValueError, IndexError):
                pass
            print("Invalid selection. Please try again.")
    
    def create_ui_shortcut_epub(self, hostname: str, port: int) -> Optional[Path]:
        """Create EPUB with web UI shortcut."""
        try:
            web_url = f"http://{hostname}:{port}"
            
            self.log_info(f"Creating GoodBooks Web UI shortcut EPUB...")
            self.log_info(f"Target URL: {web_url}")
            
            # Check if cover image exists in install directory
            cover_image_path = self.install_dir / "cover.png"
            if not cover_image_path.exists():
                cover_image_path = None
                self.log_info("No cover.png found in install directory (optional)")
            else:
                self.log_info(f"Using cover image: {cover_image_path}")
            
            epub_path = create_web_ui_shortcut_epub(
                title="GoodBooks Web Interface",
                web_url=web_url,
                author="GoodBooks Installer",
                output_path=self.temp_epub_dir / "GoodBooks_WebUI_Shortcut.epub",
                cover_image_path=cover_image_path
            )
            
            if epub_path.exists():
                size_kb = epub_path.stat().st_size / 1024
                self.log_success(f"EPUB created: {epub_path} ({size_kb:.1f} KB)")
                return epub_path
            else:
                self.log_error("EPUB creation failed")
                return None
        
        except Exception as e:
            self.log_error(f"Failed to create EPUB: {e}")
            return None
    
    def send_to_kindle_via_goodbooks(self, user: dict, epub_path: Path) -> bool:
        """
        Send the EPUB to user's Kindle using GoodBooks' internal API.
        
        This integrates with the existing Flask application's Kindle delivery mechanism.
        """
        try:
            self.log_info(f"Preparing to send EPUB to {user.get('name')}'s Kindle...")
            
            # Copy EPUB to a library location where GoodBooks can pick it up
            library_root = Path(self.settings.get('library_root', '/tmp/goodbooks'))
            library_root.mkdir(parents=True, exist_ok=True)
            
            # Copy to user's save directory if available
            user_save_dir = user.get('save_dir')
            if user_save_dir:
                target_dir = Path(user_save_dir)
                target_dir.mkdir(parents=True, exist_ok=True)
            else:
                target_dir = library_root
            
            # Copy EPUB to target location
            target_epub = target_dir / epub_path.name
            with open(epub_path, 'rb') as src:
                with open(target_epub, 'wb') as dst:
                    dst.write(src.read())
            
            self.log_success(f"EPUB copied to: {target_epub}")
            
            # Call GoodBooks to send the email
            # Use Flask app's internal function via subprocess
            python_bin = self.install_dir / "venv" / "bin" / "python3"
            
            send_script = f"""
import sys
sys.path.insert(0, '{self.install_dir}')

from pathlib import Path
from app import app, settings_manager, send_kindle_email

# Get user object
user = None
for u in settings_manager.settings.users:
    if u.name == '{user['name']}':
        user = u
        break

if not user:
    print('User not found')
    sys.exit(1)

# Send the EPUB
try:
    result = send_kindle_email(user, Path('{target_epub}'))
    if result:
        print('SUCCESS: Email sent to Kindle')
    else:
        print('FAILED: Could not send email')
except Exception as e:
    print(f'ERROR: {{e}}')
    sys.exit(1)
"""
            
            # Write temp script
            script_path = self.temp_epub_dir / "send_kindle.py"
            with open(script_path, 'w') as f:
                f.write(send_script)
            
            # Execute the script
            result = subprocess.run(
                [str(python_bin), str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.install_dir)
            )
            
            if result.returncode == 0:
                self.log_success("✓ EPUB sent to Kindle!")
                output = result.stdout.strip()
                if output:
                    self.log_info(output)
                return True
            else:
                self.log_error("Failed to send EPUB to Kindle")
                if result.stderr:
                    self.log_error(result.stderr)
                return False
        
        except Exception as e:
            self.log_error(f"Error during Kindle delivery: {e}")
            return False
    
    def gather_documentation_files(self) -> List[Path]:
        """Gather all documentation files to include in EPUB."""
        doc_files = []
        doc_names = [
            'DEPLOYMENT_CHECKLIST.md',
            'QUICKSTART.md',
            'README.md',
            'IMPLEMENTATION_SUMMARY.md',
            'BUGFIXES.md',
        ]
        
        for doc_name in doc_names:
            doc_path = self.install_dir / doc_name
            if doc_path.exists():
                doc_files.append(doc_path)
                self.log_info(f"  ✓ Including: {doc_name}")
            else:
                # Also check parent directory
                parent_doc = self.install_dir.parent / doc_name
                if parent_doc.exists():
                    doc_files.append(parent_doc)
                    self.log_info(f"  ✓ Including: {doc_name}")
        
        return doc_files
    
    def create_comprehensive_documentation_epub(
        self, 
        hostname: str, 
        port: int, 
        library_path: Path,
        cover_image_path: Optional[Path],
        doc_files: List[Path]
    ) -> Optional[Path]:
        """Create comprehensive EPUB with all documentation."""
        try:
            output_path = library_path / "GoodBooks_Complete_Guide.epub"
            
            epub_path = create_comprehensive_documentation_epub(
                title="GoodBooks Complete Guide",
                web_url=f"http://{hostname}:{port}",
                output_path=output_path,
                cover_image_path=cover_image_path if cover_image_path and cover_image_path.exists() else None,
                documentation_files=doc_files,
            )
            
            if epub_path.exists():
                size_kb = epub_path.stat().st_size / 1024
                self.log_success(f"Complete guide created ({size_kb:.1f} KB)")
                return epub_path
            else:
                self.log_error("Failed to create comprehensive EPUB")
                return None
        
        except Exception as e:
            self.log_error(f"Error creating comprehensive EPUB: {e}")
            return None
    
    def run(self) -> bool:
        """Run the post-install flow."""
        print("\n" + "=" * 60)
        print("GoodBooks Post-Installation Setup")
        print("=" * 60)
        
        # Step 1: Get service info
        self.log_info("Waiting for GoodBooks service to be fully ready...")
        time.sleep(3)  # Give service time to fully start
        
        service_info = self.get_service_info("goodbooks")
        if not service_info:
            self.log_error("Could not determine GoodBooks service address/port")
            self.log_warn("Continuing without service info...")
            hostname, port = "localhost", 5000
        else:
            hostname, port = service_info
            self.log_success(f"Service listening on: {hostname}:{port}")
        
        # Step 2: Create comprehensive documentation EPUB
        web_url = f"http://{hostname}:{port}"
        self.log_info("Generating comprehensive GoodBooks documentation EPUB...")
        
        # Gather documentation files
        doc_files = self.gather_documentation_files()
        
        # Generate comprehensive EPUB
        library_root = self.settings.get("library_root", 
                                        self.settings.get("default_download_dir", "/home/user/GoodBooks"))
        library_path = Path(library_root)
        library_path.mkdir(parents=True, exist_ok=True)
        
        cover_image_path = self.install_dir / "cover.png"
        
        doc_epub_path = self.create_comprehensive_documentation_epub(
            hostname, port, library_path, cover_image_path, doc_files
        )
        
        if doc_epub_path:
            self.log_success(f"Documentation EPUB created: {doc_epub_path}")
        else:
            self.log_warn("Failed to create comprehensive documentation EPUB")
        
        # Step 3: Check for users with Kindle and offer Kindle delivery
        users_with_kindle = self.get_users_with_kindle()
        
        if not users_with_kindle:
            self.log_info("No users with Kindle email configured.")
            self.log_info("You can still access GoodBooks at: " + web_url)
            print("=" * 60)
            return True
        
        # Step 4: Prompt user for Kindle delivery
        if not self.prompt_yes_no("Would you like to send a book containing a shortcut to the GoodBooks web UI to your Kindle?"):
            self.log_info("Skipping Kindle delivery. Documentation saved to library.")
            print("=" * 60)
            return True
        
        # Step 5: Create simple web UI shortcut EPUB for Kindle
        ui_epub_path = self.create_ui_shortcut_epub(hostname, port)
        if not ui_epub_path:
            self.log_warn("Failed to create web UI shortcut EPUB")
            print("=" * 60)
            return True  # Still return success since doc EPUB was created
        
        # Step 6: Select user
        selected_user = self.select_user(users_with_kindle)
        if not selected_user:
            print("=" * 60)
            return True
        
        # Step 7: Send via Kindle
        success = self.send_to_kindle_via_goodbooks(selected_user, ui_epub_path)
        
        print("\n" + "=" * 60)
        if success:
            self.log_success("Post-installation setup complete!")
            self.log_info(f"Web UI shortcut sent to {selected_user['name']}'s Kindle")
            self.log_info(f"Full documentation saved to: {doc_epub_path}")
        else:
            self.log_warn("Post-installation setup completed with some issues")
            self.log_info(f"Documentation saved to: {doc_epub_path}")
        print("=" * 60)
        return True


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 post_install.py /usr/local/bin/GoodBooks")
        sys.exit(1)
    
    install_dir = sys.argv[1]
    manager = PostInstallManager(install_dir)
    
    try:
        success = manager.run()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n[INFO] Installation cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

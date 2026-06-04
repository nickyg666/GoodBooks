#!/usr/bin/env python3
"""
EPUB Distribution System for GoodBooks

Handles sending GoodBooks.epub to users:
- Send to new users as an SMTP test
- Send to all users when EPUB is updated
- Track EPUB version to detect updates
"""

import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from email.message import EmailMessage
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
EPUB_PATH = BASE_DIR / "GoodBooks.epub"
EPUB_STATE_FILE = BASE_DIR / "data" / ".epub_distribution_state.json"


class EPUBDistributor:
    """Manages EPUB distribution to users."""

    def __init__(self, epub_path: Path = EPUB_PATH):
        self.epub_path = epub_path
        self.state_file = EPUB_STATE_FILE
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Load the EPUB distribution state file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load EPUB state file: {e}")
        
        return {
            "last_epub_hash": None,
            "last_sent_timestamp": None,
            "users_sent_to": [],
            "version": "1.0"
        }

    def _save_state(self) -> None:
        """Save the EPUB distribution state file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save EPUB state file: {e}")

    def get_epub_hash(self) -> Optional[str]:
        """Get SHA256 hash of the EPUB file."""
        if not self.epub_path.exists():
            return None
        
        try:
            sha256_hash = hashlib.sha256()
            with open(self.epub_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Could not hash EPUB file: {e}")
            return None

    def epub_has_changed(self) -> bool:
        """Check if EPUB has been updated since last distribution."""
        current_hash = self.get_epub_hash()
        if not current_hash:
            return False
        
        last_hash = self.state.get("last_epub_hash")
        if last_hash != current_hash:
            logger.info(f"EPUB update detected (hash changed from {last_hash[:8]} to {current_hash[:8]})")
            return True
        
        return False

    def create_epub_email(self, recipient_email: str, user_name: str, is_test: bool = False) -> EmailMessage:
        """
        Create an EmailMessage with the EPUB attached.
        
        Args:
            recipient_email: Email address to send to
            user_name: Name of the user
            is_test: If True, this is being sent as an SMTP test
        
        Returns:
            EmailMessage with EPUB attachment
        """
        msg = EmailMessage()
        msg["From"] = "GoodBooks System"
        msg["To"] = recipient_email
        
        if is_test:
            msg["Subject"] = "GoodBooks SMTP Test - User Guide"
            body = f"""Hello {user_name},

This is an SMTP connectivity test for your GoodBooks account.

Your GoodBooks User Guide (GoodBooks.epub) is attached. This guide contains:
- Complete walkthrough of all GoodBooks features
- Instructions for finding and downloading books
- How to send books to your Kindle
- Using the Random Books feature
- Setting up feeds and automation
- Troubleshooting and tips

The guide includes quick links on every page:
🏠 Home Network: http://192.168.0.9:5000
🌐 Remote Access: https://books.a1e.lol/

Steps to use on your Kindle:
1. Transfer this EPUB file to your Kindle via USB
2. Open it on your Kindle device
3. Tap the 🏠 or 🌐 buttons on any page to access GoodBooks
4. Browse books, send to device, manage your library

If you received this email, your SMTP configuration is working correctly!

Best regards,
GoodBooks Team
"""
        else:
            msg["Subject"] = "GoodBooks User Guide Updated"
            body = f"""Hello {user_name},

The GoodBooks User Guide has been updated!

Your new GoodBooks.epub is attached. This guide contains complete instructions for:
- Searching for and finding books
- Sending books to your Kindle (manual and automatic)
- Using all GoodBooks features
- Troubleshooting and tips

To use on your Kindle:
1. Transfer the EPUB file to your Kindle
2. Open it on your device
3. Tap the navigation buttons (🏠 or 🌐) to access GoodBooks

Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Best regards,
GoodBooks Team
"""
        
        msg.set_content(body)
        
        # Attach EPUB file
        if self.epub_path.exists():
            try:
                with open(self.epub_path, 'rb') as f:
                    epub_data = f.read()
                
                msg.add_attachment(
                    epub_data,
                    maintype='application',
                    subtype='epub+zip',
                    filename='GoodBooks.epub'
                )
                logger.info(f"Attached EPUB ({len(epub_data)} bytes) to email for {user_name}")
            except Exception as e:
                logger.error(f"Could not attach EPUB to email: {e}")
                msg.set_content(body + f"\n\n[Note: EPUB attachment failed: {e}]")
        else:
            logger.error("EPUB file not found at {self.epub_path}")
            msg.set_content(body + f"\n\n[Note: EPUB file not found at {self.epub_path}]")
        
        return msg

    def send_epub_to_user(self, user: 'UserSettings', smtp_config: 'SMTPSettings', 
                         is_test: bool = False) -> bool:
        """
        Send EPUB to a single user.
        
        Args:
            user: UserSettings object
            smtp_config: SMTPSettings for email
            is_test: If True, this is being sent as an SMTP test (for new users)
        
        Returns:
            True if successful, False otherwise
        """
        if not user.notification_email:
            logger.warning(f"User '{user.name}' has no notification email configured, skipping EPUB")
            return False
        
        if not smtp_config.is_configured():
            logger.warning("SMTP not configured, cannot send EPUB")
            return False
        
        try:
            msg = self.create_epub_email(user.notification_email, user.name, is_test=is_test)
            smtp_config.send(msg)
            logger.info(f"EPUB sent to {user.name} ({user.notification_email})")
            return True
        except Exception as e:
            logger.error(f"Failed to send EPUB to {user.name}: {e}")
            return False

    def send_epub_to_all_users(self, users: List['UserSettings'], 
                              smtp_config: 'SMTPSettings') -> Dict[str, bool]:
        """
        Send EPUB to all users.
        
        Args:
            users: List of UserSettings objects
            smtp_config: SMTPSettings for email
        
        Returns:
            Dictionary mapping user names to success status
        """
        results = {}
        
        logger.info(f"Sending EPUB to {len(users)} users")
        
        for user in users:
            if not user.notification_email:
                logger.debug(f"Skipping {user.name} - no notification email")
                results[user.name] = False
                continue
            
            success = self.send_epub_to_user(user, smtp_config, is_test=False)
            results[user.name] = success
        
        if all(results.values()):
            # Update state only if all sends succeeded
            current_hash = self.get_epub_hash()
            self.state["last_epub_hash"] = current_hash
            self.state["last_sent_timestamp"] = datetime.now().isoformat()
            self.state["users_sent_to"] = [name for name, success in results.items() if success]
            self._save_state()
            logger.info(f"EPUB distribution complete. Hash saved: {current_hash[:8]}")
        else:
            failed = [name for name, success in results.items() if not success]
            logger.warning(f"EPUB distribution had {len(failed)} failures: {failed}")
        
        return results

    def should_send_to_new_user(self, user: 'UserSettings') -> bool:
        """
        Check if we should send EPUB to a new user as SMTP test.
        
        Args:
            user: UserSettings object to check
        
        Returns:
            True if user has never received EPUB, False otherwise
        """
        users_sent_to = self.state.get("users_sent_to", [])
        return user.name not in users_sent_to

    def mark_user_received_epub(self, user_name: str) -> None:
        """Mark that a user has received the EPUB."""
        users_sent_to = self.state.get("users_sent_to", [])
        if user_name not in users_sent_to:
            users_sent_to.append(user_name)
            self.state["users_sent_to"] = users_sent_to
            self._save_state()
            logger.info(f"Marked {user_name} as having received EPUB")

    def get_distribution_status(self) -> Dict:
        """Get current EPUB distribution status."""
        current_hash = self.get_epub_hash()
        last_hash = self.state.get("last_epub_hash")
        
        return {
            "epub_exists": self.epub_path.exists(),
            "epub_size_kb": self.epub_path.stat().st_size / 1024 if self.epub_path.exists() else 0,
            "current_hash": current_hash[:8] if current_hash else None,
            "last_sent_hash": last_hash[:8] if last_hash else None,
            "epub_changed": current_hash != last_hash if current_hash and last_hash else False,
            "last_sent_timestamp": self.state.get("last_sent_timestamp"),
            "users_previously_sent": len(self.state.get("users_sent_to", []))
        }


def check_and_distribute_epub_update(settings_manager: 'SettingsManager') -> bool:
    """
    Check if EPUB has been updated and send to all users if it has.
    
    This should be called periodically (e.g., during maintenance cycles).
    
    Args:
        settings_manager: The application's SettingsManager instance
    
    Returns:
        True if EPUB was distributed, False if no update needed
    """
    distributor = EPUBDistributor()
    
    # Check if EPUB has changed
    if not distributor.epub_has_changed():
        return False
    
    logger.info("EPUB update detected, sending to all users")
    
    # Get SMTP config and users
    smtp_config = settings_manager.settings.smtp
    users = settings_manager.settings.users
    
    # Send to all users
    results = distributor.send_epub_to_all_users(users, smtp_config)
    
    # Log results
    successful = sum(1 for success in results.values() if success)
    logger.info(f"EPUB update distribution complete: {successful}/{len(users)} successful")
    
    return True


def send_epub_to_new_user(user: 'UserSettings', settings_manager: 'SettingsManager') -> bool:
    """
    Send EPUB to a newly created user as an SMTP test.
    
    This tests that SMTP is working and gives the user the guide immediately.
    
    Args:
        user: The new UserSettings object
        settings_manager: The application's SettingsManager instance
    
    Returns:
        True if successful, False otherwise
    """
    distributor = EPUBDistributor()
    
    # Check if user should receive EPUB
    if not distributor.should_send_to_new_user(user):
        logger.debug(f"User {user.name} has already received EPUB")
        return False
    
    # Check SMTP configuration
    smtp_config = settings_manager.settings.smtp
    if not smtp_config.is_configured():
        logger.warning("SMTP not configured, cannot send EPUB to new user")
        return False
    
    # Send EPUB as test
    logger.info(f"Sending EPUB to new user {user.name} as SMTP test")
    success = distributor.send_epub_to_user(user, smtp_config, is_test=True)
    
    if success:
        distributor.mark_user_received_epub(user.name)
    
    return success

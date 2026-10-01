#!/usr/bin/env python3
"""
GoodBooks: Send books to users and broadcast notifications via Chromecast.

This script:
1. Downloads books from Archive.org
2. Emails them to a user
3. Broadcasts an announcement via all discovered Chromecast/Google Home speakers
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
import logging
from typing import List, Optional, Dict
import time

logger = logging.getLogger(__name__)


def send_books_via_email(
    recipient_email: str,
    recipient_name: str,
    book_files: List[Path],
    smtp_server: str = "localhost",
    smtp_port: int = 25,
    sender_email: str = "goodbooks@example.com",
    use_tls: bool = False,
) -> bool:
    """
    Send book files to a user via email.
    
    Args:
        recipient_email: Email address to send to
        recipient_name: Name of recipient (for greeting)
        book_files: List of Path objects to PDF/EPUB files
        smtp_server: SMTP server address
        smtp_port: SMTP port
        sender_email: Sender email address
        use_tls: Whether to use TLS
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Create message
        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = recipient_email
        msg['Subject'] = f"Your Books from GoodBooks!"
        
        # Body
        file_list = "\n".join([f"  • {f.name}" for f in book_files])
        total_size = sum(f.stat().st_size for f in book_files) / 1024 / 1024
        
        body = f"""Hi {recipient_name}!

Your requested books are ready for download! Here's what we found:

{file_list}

Total size: {total_size:.1f} MB

These are high-quality scans from Archive.org's extensive library.
Download them and enjoy!

Best,
GoodBooks System
"""
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach files
        for book_file in book_files:
            try:
                with open(book_file, 'rb') as attachment:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        'Content-Disposition',
                        f'attachment; filename= {book_file.name}',
                    )
                    msg.attach(part)
                logger.info(f"Attached: {book_file.name}")
            except Exception as e:
                logger.error(f"Failed to attach {book_file}: {e}")
        
        # Send email
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            if use_tls:
                server.starttls()
            server.send_message(msg)
        
        logger.info(f"Email sent to {recipient_email}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def broadcast_to_speakers(
    message: str,
    speaker_hosts: Optional[List[str]] = None,
) -> Dict[str, bool]:
    """
    Broadcast a message to all discovered Chromecast/Google Home speakers.
    
    Args:
        message: Text message to speak
        speaker_hosts: List of speaker IPs. If None, discovers all.
        
    Returns:
        Dict mapping speaker hostname to success status
    """
    try:
        import pychromecast
    except ImportError:
        logger.error("pychromecast not installed. Cannot broadcast to speakers.")
        return {}
    
    results = {}
    
    try:
        # Discover devices if not provided
        if speaker_hosts is None:
            logger.info("Discovering Chromecast devices...")
            speaker_hosts = pychromecast.discover_devices(timeout=5)
            logger.info(f"Found {len(speaker_hosts)} devices")
        
        for host in speaker_hosts:
            try:
                logger.info(f"Connecting to {host}...")
                cast = pychromecast.Chromecast(host)
                cast.wait()
                
                # Cast the message
                mc = cast.media_controller
                
                # Try to play a TTS (text-to-speech) URL
                # This is a simple approach using Google Translate TTS
                tts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&q={message}&tl=en&total=1&idx=0"
                
                try:
                    mc.play_media(tts_url, 'audio/mp3')
                    results[host] = True
                    logger.info(f"✓ Broadcasted to {host}")
                    time.sleep(3)  # Let message play
                except Exception as e:
                    logger.warning(f"Failed to play TTS on {host}: {e}")
                    results[host] = False
                
                cast.quit_app()
                
            except Exception as e:
                logger.error(f"Failed to broadcast to {host}: {e}")
                results[host] = False
    
    except Exception as e:
        logger.error(f"Speaker broadcast failed: {e}")
    
    return results


def deliver_books_to_user(
    user_name: str,
    user_email: str,
    book_files: List[Path],
    broadcast_message: Optional[str] = None,
    speaker_hosts: Optional[List[str]] = None,
) -> Dict:
    """
    Complete delivery flow: email books and broadcast notification.
    
    Args:
        user_name: User's name
        user_email: User's email
        book_files: List of book file paths
        broadcast_message: Optional message to broadcast (if None, auto-generated)
        speaker_hosts: Optional list of speaker IPs to broadcast to
        
    Returns:
        Dict with 'email_sent' and 'speakers_notified' keys
    """
    result = {
        'email_sent': False,
        'speakers_notified': {},
        'files': [f.name for f in book_files],
    }
    
    # Send email
    if book_files:
        result['email_sent'] = send_books_via_email(
            user_email,
            user_name,
            book_files,
        )
    
    # Broadcast message
    if broadcast_message:
        result['speakers_notified'] = broadcast_to_speakers(
            broadcast_message,
            speaker_hosts,
        )
    
    return result


if __name__ == '__main__':
    # Example usage
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # Test with our Mad Libs
    madlibs = list(Path('/tmp/goodbooks_test').glob('*.pdf'))
    
    if madlibs:
        result = deliver_books_to_user(
            user_name='Lorenzo',
            user_email='lorenzo@example.com',
            book_files=madlibs,
            broadcast_message='Lorenzo, check your iPad email for some Mad Libs books from GoodBooks!',
        )
        
        print("\nDelivery Result:")
        print(f"  Email sent: {result['email_sent']}")
        print(f"  Files: {', '.join(result['files'])}")
        print(f"  Speakers notified: {len(result['speakers_notified'])} devices")

#!/usr/bin/env python3
"""
Email debugger - auto-configured from msmtprc
Captures notification emails to show MIME structure and image embedding.
"""

import imaplib
import email
from email import policy
from pathlib import Path
import time
import re

def parse_msmtprc():
    """Extract Gmail credentials from msmtprc"""
    msmtprc_path = Path("/etc/msmtprc")
    if not msmtprc_path.exists():
        msmtprc_path = Path.home() / ".msmtprc"
    
    if not msmtprc_path.exists():
        return None
    
    config = {}
    with open(msmtprc_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('#') or not line:
                continue
            
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                key, value = parts
                config[key] = value.strip('"')
    
    return config

def analyze_email(msg):
    """Analyze email structure and report image issues"""
    print("\n" + "="*80)
    print("EMAIL ANALYSIS REPORT")
    print("="*80)
    
    print(f"\nFrom: {msg['From']}")
    print(f"To: {msg['To']}")
    print(f"Subject: {msg['Subject']}")
    print(f"Content-Type: {msg.get_content_type()}")
    
    # Check structure
    print(f"\nIs multipart: {msg.is_multipart()}")
    
    if msg.is_multipart():
        print(f"Number of parts: {len(msg.get_payload())}")
        
        for i, part in enumerate(msg.get_payload()):
            print(f"\n--- Part {i} ---")
            print(f"Content-Type: {part.get_content_type()}")
            cid = part.get('Content-ID')
            print(f"Content-ID: {cid if cid else 'NONE'}")
            print(f"Content-Disposition: {part.get('Content-Disposition', 'NONE')}")
            
            if part.get_content_type().startswith('text/'):
                content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                # Show only img tags
                imgs = re.findall(r'<img[^>]*src="([^"]*)"[^>]*>', content)
                if imgs:
                    print("\n✓ Image references found in HTML:")
                    for img_src in imgs:
                        print(f"    {img_src}")
                else:
                    print("\n✗ No image tags found in HTML")
                    
            elif part.get_content_type().startswith('image/'):
                data = part.get_payload(decode=True)
                print(f"Size: {len(data)} bytes")
                print(f"First bytes: {data[:20].hex()}")
    else:
        print("✗ Message is NOT multipart - no embedded images possible!")
    
    # Show the raw message structure
    print("\n" + "="*80)
    print("RAW EMAIL SOURCE (first 2000 chars)")
    print("="*80)
    raw = msg.as_string()
    print(raw[:2000])
    if len(raw) > 2000:
        print("\n[... truncated ...]")
    
    # Save full email
    email_file = Path("test_email.eml")
    email_file.write_text(msg.as_string())
    print(f"\n✓ Full email saved to: {email_file}")
    print(f"   View with: python3 view_eml.py test_email.eml")


def main():
    print("="*80)
    print("GoodBooks Email Debugger - Auto-Configured from msmtprc")
    print("="*80)
    
    # Parse msmtprc
    config = parse_msmtprc()
    if not config:
        print("\n✗ Could not find msmtprc config")
        return
    
    gmail_user = config.get('user')
    gmail_pass = config.get('password', '').replace(' ', '')  # Remove spaces from app password
    gmail_from = config.get('from')
    
    if not gmail_user or not gmail_pass:
        print("\n✗ Missing user or password in msmtprc")
        return
    
    print(f"\n✓ Loaded config from msmtprc")
    print(f"  User: {gmail_user}")
    print(f"  From: {gmail_from}")
    
    try:
        print(f"\nConnecting to Gmail IMAP...")
        server = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        server.login(gmail_user, gmail_pass)
        print("✓ Connected and authenticated")
        
        # Select SENT folder
        status, mailbox = server.select('"[Gmail]/Sent Mail"')
        if status != 'OK':
            status, mailbox = server.select('INBOX')
        print("✓ Selected SENT folder")
        
        print("\n" + "="*80)
        print("READY - Listening for emails...")
        print("="*80)
        print("\nNow download a book in GoodBooks to trigger a notification email.")
        print("Waiting for new email to appear in Sent folder...\n")
        
        # Poll for new emails
        email_count = 0
        poll_interval = 2  # Check every 2 seconds
        
        while True:
            time.sleep(poll_interval)
            
            # Get email count
            status, msg_nums = server.search(None, 'ALL')
            if msg_nums[0]:
                current_count = len(msg_nums[0].split())
                if current_count > email_count:
                    # New email arrived!
                    new_emails = current_count - email_count
                    print(f"\n✓ {new_emails} new email(s) detected!")
                    
                    # Fetch the latest email
                    msg_list = msg_nums[0].split()
                    latest_num = msg_list[-1]
                    
                    status, msg_data = server.fetch(latest_num, '(RFC822)')
                    if status == 'OK':
                        msg = email.message_from_bytes(msg_data[0][1], policy=policy.default)
                        
                        # Check if it looks like a notification email
                        subject = msg.get('Subject', '').lower()
                        if any(keyword in subject for keyword in ['kindle', 'library', 'notification', 'sent to', 'added']):
                            print("\n✓ Found notification email!")
                            analyze_email(msg)
                            print("\n" + "="*80)
                            print("SUCCESS - Email captured and analyzed")
                            print("="*80)
                            break
                        else:
                            print(f"  (Found email but doesn't look like notification: '{subject}')")
                            email_count = current_count
                    else:
                        email_count = current_count
            
            print(".", end="", flush=True)
        
        server.close()
        
    except imaplib.IMAP4.error as e:
        print(f"\n✗ Gmail login failed: {e}")
        print("\nDebug info:")
        print(f"  User: {gmail_user}")
        print(f"  Password: {'*' * len(gmail_pass)}")
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()

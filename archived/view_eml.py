#!/usr/bin/env python3
"""View the structure of a .eml file"""

import sys
from pathlib import Path
import email
from email import policy

def view_eml(filepath):
    eml = Path(filepath)
    if not eml.exists():
        print(f"File not found: {filepath}")
        return
    
    msg = email.message_from_bytes(eml.read_bytes(), policy=policy.default)
    
    print("\n" + "="*80)
    print(f"EMAIL FILE: {filepath}")
    print("="*80)
    
    print(f"\nFrom: {msg['From']}")
    print(f"To: {msg['To']}")
    print(f"Subject: {msg['Subject']}")
    print(f"Date: {msg['Date']}")
    print(f"Main Content-Type: {msg.get_content_type()}")
    
    if msg.is_multipart():
        print(f"\n✓ This is a multipart message ({len(msg.get_payload())} parts)")
        
        for i, part in enumerate(msg.get_payload()):
            ct = part.get_content_type()
            cid = part.get('Content-ID', '(none)')
            disp = part.get('Content-Disposition', '(none)')
            
            print(f"\n[Part {i}]")
            print(f"  Type: {ct}")
            print(f"  Content-ID: {cid}")
            print(f"  Disposition: {disp}")
            
            if ct.startswith('text/'):
                payload = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                # Count img tags
                import re
                imgs = re.findall(r'<img[^>]*src="([^"]*)"', payload)
                if imgs:
                    print(f"  Image references in HTML:")
                    for img_src in imgs:
                        print(f"    - {img_src}")
            elif ct.startswith('image/'):
                data = part.get_payload(decode=True)
                print(f"  Size: {len(data)} bytes")
                print(f"  Data starts with: {data[:20].hex()}")
    else:
        print("\n✗ This is NOT multipart - no embedded images")
    
    print("\n" + "="*80)
    print("FIRST 2000 CHARACTERS OF RAW EMAIL:")
    print("="*80)
    print(msg.as_string()[:2000])
    print("\n[... truncated ...]")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 view_eml.py <file.eml>")
        print("Example: python3 view_eml.py test_email.eml")
        sys.exit(1)
    view_eml(sys.argv[1])

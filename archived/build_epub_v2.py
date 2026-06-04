#!/usr/bin/env python3
"""
Build GoodBooks.epub with proper EPUB3 structure.
Kindle-friendly layout with simple, user-friendly content.
Dynamic IP/Port detection with SVG cover as first page.
Content loaded from goodbooks_content.txt for easy editing.
"""
import os
import re
import zipfile
import shutil
import socket
import json
import sys
from pathlib import Path
from typing import List, Tuple, Optional
import html
from datetime import datetime

# Dynamic IP and port detection
def get_local_ip() -> str:
    """
    Detect actual local IP address by attempting socket connection to 8.8.8.8:80.
    Falls back through multiple strategies to find the real local network IP.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and ip != '0.0.0.0' and ip != '127.0.0.1':
            return ip
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and ip != '0.0.0.0' and ip != '127.0.0.1':
            return ip
    except Exception:
        pass

    return '127.0.0.1'

def get_server_port(settings_file: Optional[Path] = None) -> int:
    """Get the server port from multiple sources."""
    port_env = os.environ.get('PORT')
    if port_env:
        try:
            return int(port_env)
        except ValueError:
            pass

    if settings_file is None:
        settings_file = Path(__file__).parent / 'data' / 'settings.json'

    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                if 'server_port' in settings:
                    return int(settings['server_port'])
        except Exception:
            pass

    return 5000

def get_base_url(ip_address: Optional[str] = None, port: Optional[int] = None) -> str:
    """Generate base URL from IP and port."""
    if ip_address is None:
        ip_address = get_local_ip()
    if port is None:
        port = get_server_port()

    return f"http://{ip_address}:{port}"

def load_content() -> dict:
    """Load content from goodbooks_content.txt and parse sections."""
    content_file = Path(__file__).parent / 'goodbooks_content.txt'
    
    if not content_file.exists():
        print(f"[WARNING] Content file not found: {content_file}")
        return {}

    with open(content_file, 'r') as f:
        text = f.read()

    sections = {}
    current_section = None
    current_content = []

    for line in text.split('\n'):
        if line.startswith('=== ') and line.endswith(' ==='):
            if current_section:
                sections[current_section] = '\n'.join(current_content).strip()
            current_section = line.replace('=== ', '').replace(' ===', '')
            current_content = []
        else:
            current_content.append(line)

    if current_section:
        sections[current_section] = '\n'.join(current_content).strip()

    return sections

def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return html.escape(text)

# Kindle-friendly CSS - minimal styling for compatibility
KINDLE_CSS = """
body {
  font-family: Georgia, serif;
  margin: 0;
  padding: 0;
  line-height: 1.6;
  font-size: 1em;
  color: #000;
  background: #fff;
}

h1 {
  font-size: 1.8em;
  margin: 1.2em 0 0.6em 0;
  font-weight: bold;
  text-align: center;
  page-break-after: avoid;
}

h2 {
  font-size: 1.5em;
  margin: 1.2em 0 0.6em 0;
  font-weight: bold;
  page-break-after: avoid;
}

h3 {
  font-size: 1.2em;
  margin: 0.9em 0 0.5em 0;
  font-weight: bold;
  page-break-after: avoid;
}

p {
  margin: 0.8em 0;
  text-align: justify;
}

ul {
  margin: 0.8em 0;
  padding-left: 1.5em;
}

li {
  margin: 0.3em 0;
}

a {
  color: #0066cc;
  text-decoration: underline;
}

.cover-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 2em;
  text-align: center;
  page-break-after: always;
}

.cover-title {
  font-size: 2.5em;
  font-weight: bold;
  margin: 1em 0 0.5em 0;
  color: #000;
}

.cover-subtitle {
  font-size: 1.3em;
  margin: 0.5em 0 2em 0;
  color: #333;
  font-style: italic;
}

.cover-link {
  margin: 2em 0;
  padding: 1em;
  border: 1px solid #ccc;
  text-align: center;
}

.cover-link a {
  font-size: 1.1em;
  font-weight: bold;
}

.content-page {
  padding: 1em;
  page-break-inside: avoid;
}

.page-footer {
  margin-top: 3em;
  padding-top: 1.5em;
  border-top: 1px solid #ccc;
  text-align: center;
  font-size: 0.9em;
  color: #666;
  page-break-before: avoid;
}

.footer-link {
  color: #0066cc;
  text-decoration: underline;
  font-weight: bold;
}

.pagebreak {
  page-break-after: always;
}

@media amzn-mobi {
  body {
    font-size: 0.8em;
  }
  
  h1 {
    font-size: 1.5em;
  }
}
"""

def create_cover_page(base_url: str) -> str:
    """Create a Kindle-friendly cover page with image."""
    title = "GoodBooks"
    subtitle = "Your Personal Ebook Library & Kindle Delivery System"
    url_display = base_url
    external_url = "https://books.a1e.lol?token=foDcuQIAF5_yVW1ngwAKgeQ-TQYcESvE7XQFDhnaiCw"
    
    xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Cover</title>
    <style type="text/css">
      body {{
        margin: 0;
        padding: 0;
        font-family: Georgia, serif;
        background: #fff;
        color: #000;
      }}
      
      .cover-page {{
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
        padding: 1em;
        text-align: center;
      }}
      
      .cover-image {{
        max-width: 100%;
        height: auto;
        margin: 0 auto;
        display: block;
      }}
      
      .cover-content {{
        max-width: 500px;
        margin-top: 2em;
      }}
      
      .cover-title {{
        font-size: 2.5em;
        font-weight: bold;
        margin: 0.5em 0;
        color: #000;
      }}
      
      .cover-subtitle {{
        font-size: 1.2em;
        margin: 1.5em 0 2em 0;
        color: #333;
        font-style: italic;
        line-height: 1.4;
      }}
      
      .cover-link {{
        margin: 2em 0;
        padding: 1.5em;
        border: 2px solid #666;
        border-radius: 8px;
        background: #f9f9f9;
      }}
      
      .cover-link-text {{
        display: block;
        font-size: 0.9em;
        color: #666;
        margin-bottom: 0.5em;
      }}
      
      .cover-link a {{
        display: block;
        color: #0066cc;
        text-decoration: underline;
        font-weight: bold;
        font-size: 1.1em;
        word-wrap: break-word;
        word-break: break-all;
      }}
    </style>
  </head>
  <body>
    <div class="cover-page">
      <img src="cover.png" alt="GoodBooks Cover" class="cover-image" />
      
      <div class="cover-content">
        <h1 class="cover-title">{escape_html(title)}</h1>
        <p class="cover-subtitle">{escape_html(subtitle)}</p>
        
        <div class="cover-link">
          <span class="cover-link-text">Open Locally:</span>
          <a href="{escape_html(base_url)}">{escape_html(url_display)}</a>
        </div>
        
        <div class="cover-link">
          <span class="cover-link-text">Away from Home?</span>
          <a href="{escape_html(external_url)}">Access via Internet</a>
        </div>
      </div>
    </div>
  </body>
</html>
'''
    return xhtml

def create_content_page(title: str, content: str, base_url: str, page_num: int = 1) -> str:
    """Create a content page with footer link."""
    title_safe = escape_html(title)
    
    # Convert bullet points and basic formatting
    content_html = escape_html(content)
    content_html = content_html.replace('\n\n', '</p><p>')
    content_html = content_html.replace('• ', '&#8226; ')
    
    # Wrap in paragraphs
    content_html = f'<p>{content_html}</p>'
    
    xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{title_safe}</title>
    <style type="text/css">
{KINDLE_CSS}
    </style>
  </head>
  <body>
    <div class="content-page">
      <h1>{title_safe}</h1>
      
      <div class="main-content">
        {content_html}
      </div>
      
      <div class="page-footer">
        <p><span class="footer-link"><a href="{escape_html(base_url)}">← Open GoodBooks</a></span></p>
      </div>
    </div>
  </body>
</html>
'''
    return xhtml

def create_opf(base_url: str, num_pages: int) -> str:
    """Create the OPF package document."""
    timestamp = datetime.now().isoformat()
    
    # Create spine items
    spine_items = '\n    '.join([
        '<itemref idref="cover" linear="yes" />',
        *[f'<itemref idref="page{i}" linear="yes" />' for i in range(1, num_pages)]
    ])
    
    manifest_items = '\n    '.join([
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />',
        '<item id="css" href="style.css" media-type="text/css" />',
        '<item id="cover-image" href="cover.png" media-type="image/png" />',
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml" />',
        *[f'<item id="page{i}" href="page{i}.xhtml" media-type="application/xhtml+xml" />' for i in range(1, num_pages)]
    ])
    
    opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>GoodBooks</dc:title>
    <dc:creator>GoodBooks</dc:creator>
    <dc:date>{timestamp}</dc:date>
    <dc:identifier id="uuid">{timestamp}</dc:identifier>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">{timestamp}</meta>
    <meta name="cover" content="cover-image" />
  </metadata>
  
  <manifest>
    {manifest_items}
  </manifest>
  
  <spine toc="ncx">
    {spine_items}
  </spine>
</package>
'''
    return opf

def create_ncx(num_pages: int) -> str:
    """Create the EPUB2 compatibility NCX file."""
    nav_points = '\n    '.join([
        '<navPoint id="cover" playOrder="1"><navLabel><text>Cover</text></navLabel><content src="cover.xhtml" /></navPoint>',
        *[f'<navPoint id="page{i}" playOrder="{i+1}"><navLabel><text>Page {i}</text></navLabel><content src="page{i}.xhtml" /></navPoint>' for i in range(1, num_pages)]
    ])
    
    ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="goodbooks-epub" />
    <meta name="dtb:depth" content="1" />
    <meta name="dtb:totalPageCount" content="0" />
    <meta name="dtb:maxPageNumber" content="0" />
  </head>
  <docTitle>
    <text>GoodBooks</text>
  </docTitle>
  <navMap>
    {nav_points}
  </navMap>
</ncx>
'''
    return ncx

def create_epub(base_url: str, output_path: Path = None) -> Path:
    """Create the complete EPUB file."""
    if output_path is None:
        output_path = Path(__file__).parent / 'GoodBooks.epub'
    
    # Create temporary directory
    temp_dir = Path(__file__).parent / '.epub_temp'
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir()
    
    # Create EPUB directory structure
    (temp_dir / 'META-INF').mkdir()
    (temp_dir / 'OEBPS').mkdir()
    
    # Create container.xml
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""
    with open(temp_dir / 'META-INF' / 'container.xml', 'w') as f:
        f.write(container_xml)
    
    # Create mimetype file
    with open(temp_dir / 'mimetype', 'w') as f:
        f.write('application/epub+zip')
    
    # Load content from file
    sections = load_content()
    
    if not sections:
        print("[ERROR] No content loaded from goodbooks_content.txt")
        return output_path
    
    # Create cover page
    cover_xhtml = create_cover_page(base_url)
    with open(temp_dir / 'OEBPS' / 'cover.xhtml', 'w', encoding='utf-8') as f:
        f.write(cover_xhtml)
    
    # Copy cover image to EPUB
    cover_src = Path(__file__).parent / 'cover.png'
    if cover_src.exists():
        shutil.copy(cover_src, temp_dir / 'OEBPS' / 'cover.png')
    
    # Create content pages (skip "COVER PAGE" section, use others)
    page_num = 1
    for section_name, section_content in sections.items():
        if section_name.upper() == 'COVER PAGE' or not section_content.strip():
            continue
        
        page_xhtml = create_content_page(section_name, section_content, base_url, page_num)
        filename = f'page{page_num}.xhtml'
        with open(temp_dir / 'OEBPS' / filename, 'w', encoding='utf-8') as f:
            f.write(page_xhtml)
        page_num += 1
    
    # Create OPF (manifest)
    num_pages = page_num
    opf_content = create_opf(base_url, num_pages)
    with open(temp_dir / 'OEBPS' / 'content.opf', 'w', encoding='utf-8') as f:
        f.write(opf_content)
    
    # Create NCX (table of contents)
    ncx_content = create_ncx(num_pages)
    with open(temp_dir / 'OEBPS' / 'toc.ncx', 'w', encoding='utf-8') as f:
        f.write(ncx_content)
    
    # Create style.css
    with open(temp_dir / 'OEBPS' / 'style.css', 'w', encoding='utf-8') as f:
        f.write(KINDLE_CSS)
    
    # Create the EPUB zip file
    if output_path.exists():
        output_path.unlink()
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # Add mimetype first (uncompressed)
        epub.write(temp_dir / 'mimetype', 'mimetype', compress_type=zipfile.ZIP_STORED)
        
        # Add all other files
        for root, dirs, files in os.walk(temp_dir):
            for file in files:
                if file == 'mimetype':
                    continue
                file_path = Path(root) / file
                arcname = file_path.relative_to(temp_dir)
                epub.write(file_path, arcname)
    
    # Cleanup
    shutil.rmtree(temp_dir)
    
    # Print success message
    size_kb = output_path.stat().st_size / 1024
    print(f"✓ Created GoodBooks.epub ({size_kb:.1f} KB)")
    print(f"✓ Server URL: {base_url}")
    print(f"✓ Cover image embedded and marked for Kindle")
    print(f"✓ Cover page with clickable link included")
    print(f"✓ {num_pages} total pages with footer navigation")
    
    return output_path

def main(ip_address: Optional[str] = None, port: Optional[int] = None):
    """Main entry point."""
    if ip_address:
        try:
            if port:
                port = int(port)
        except (ValueError, TypeError):
            port = None
    
    base_url = get_base_url(ip_address, port)
    output = create_epub(base_url)
    print(f"✓ EPUB ready at: {output}")

if __name__ == "__main__":
    ip_address = sys.argv[1] if len(sys.argv) > 1 else None
    port = sys.argv[2] if len(sys.argv) > 2 else None
    
    main(ip_address, port)

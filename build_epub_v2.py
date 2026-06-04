#!/usr/bin/env python3
"""
Build GoodBooks.epub with Kindle-optimized layout.

Features:
- 6" diagonal page layout (Kindle-standard)
- Navigation links on every page for home/away access
- Grayscale-friendly CSS styling
- User guide focused on practical features:
  * How to search for books
  * How to send to Kindle
  * Random book function
  * Library browsing
- Links disguised as styled text buttons
"""

import os
import sys
import json
import zipfile
import shutil
import html
from pathlib import Path
from datetime import datetime
from typing import Optional

BASE_DIR = Path(__file__).parent


def get_local_ip() -> str:
    """Get local IP address for in-home links."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip and ip != '0.0.0.0' else '192.168.0.9'
    except Exception:
        return '192.168.0.9'


def get_server_port() -> int:
    """Get server port from settings."""
    settings_file = BASE_DIR / 'data' / 'settings.json'
    if settings_file.exists():
        try:
            with open(settings_file, 'r') as f:
                settings = json.load(f)
                return int(settings.get('server_port', 5000))
        except Exception:
            pass
    return 5000


# Define the two URLs
HOME_URL = f"http://192.168.0.9:5000"
AWAY_URL = "https://books.a1e.lol/?token=foDcuQIAF5_yVW1ngwAKgeQ-TQYcESvE7XQFDhnaiCw"

# Kindle-optimized CSS (grayscale friendly, 6" diagonal = 600px width)
KINDLE_CSS = """
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: Georgia, serif;
  font-size: 12pt;
  line-height: 1.5;
  color: #000;
  background: #fff;
  max-width: 600px;
  margin: 0 auto;
  padding: 0.5em;
}

/* Navigation header - appears on every page */
.page-header {
  border-bottom: 2px solid #333;
  margin-bottom: 1em;
  padding-bottom: 0.8em;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.5em;
  page-break-after: avoid;
  page-break-inside: avoid;
}

.page-header h1 {
  font-size: 1.2em;
  margin: 0;
  font-weight: bold;
}

/* Navigation links - styled as text buttons */
.nav-links {
  display: flex;
  gap: 0.3em;
  flex-wrap: wrap;
  page-break-inside: avoid;
}

.nav-button {
  display: inline-block;
  padding: 0.4em 0.6em;
  border: 1px solid #333;
  background: #f5f5f5;
  color: #000;
  text-decoration: none;
  font-size: 0.85em;
  font-weight: bold;
  border-radius: 2px;
  cursor: pointer;
}

.nav-button:active {
  background: #ddd;
  border-style: inset;
}

/* Main content */
.main-content {
  padding: 0.5em 0;
}

.main-content > * {
  page-break-inside: avoid;
}

/* Headings */
h1 {
  font-size: 1.6em;
  margin: 1em 0 0.5em 0;
  font-weight: bold;
  border-bottom: 1px solid #666;
  padding-bottom: 0.3em;
  page-break-after: avoid;
}

h2 {
  font-size: 1.3em;
  margin: 0.8em 0 0.4em 0;
  font-weight: bold;
  page-break-after: avoid;
}

h3 {
  font-size: 1.1em;
  margin: 0.6em 0 0.3em 0;
  font-weight: bold;
  page-break-after: avoid;
}

p {
  margin: 0.5em 0;
  text-align: justify;
}

/* Lists */
ul, ol {
  margin: 0.5em 0 0.5em 1.5em;
  padding: 0;
}

li {
  margin: 0.3em 0;
  line-height: 1.4;
}

/* Emphasis */
strong {
  font-weight: bold;
}

em {
  font-style: italic;
}

code {
  font-family: monospace;
  font-size: 0.9em;
  background: #f5f5f5;
  padding: 0.1em 0.3em;
}

/* Section breaks */
.section-break {
  border-top: 1px dotted #999;
  margin: 1.5em 0;
  padding-top: 1em;
  page-break-after: avoid;
}

.page-break {
  page-break-after: always;
}

/* Info boxes */
.info-box {
  border-left: 3px solid #333;
  padding: 0.6em 0.8em;
  margin: 0.8em 0;
  background: #f9f9f9;
  page-break-inside: avoid;
}

.info-box strong {
  display: block;
  margin-bottom: 0.3em;
}

/* Cover page */
.cover {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  text-align: center;
  padding: 2em 1em;
  page-break-after: always;
}

.cover h1 {
  font-size: 2.4em;
  border: none;
  margin: 0.5em 0;
}

.cover p {
  font-size: 1.1em;
  margin: 0.5em 0;
}

/* Table of contents */
.toc {
  page-break-after: always;
}

.toc ul {
  list-style: none;
  margin-left: 0;
  padding-left: 0;
}

.toc li {
  margin: 0.4em 0;
}

.toc a {
  color: #000;
  text-decoration: none;
}

/* Footer - page numbers and navigation reminder */
.page-footer {
  border-top: 1px solid #ccc;
  margin-top: 1.5em;
  padding-top: 0.5em;
  font-size: 0.85em;
  text-align: center;
  color: #666;
  page-break-inside: avoid;
}
"""

# HTML for cover page with navigation
COVER_HTML = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
    <title>GoodBooks User Guide</title>
    <meta charset="UTF-8"/>
    <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
<div class="cover">
    <h1>📚 GoodBooks</h1>
    <p>Personal Ebook Library</p>
    <p>User Guide & Quick Reference</p>
    <div class="page-header" style="border: none; margin-top: 2em; padding-top: 2em; border-top: 1px solid #333;">
        <div>Access GoodBooks:</div>
    </div>
    <div class="nav-links" style="justify-content: center; margin-top: 1em;">
        <a href="{HOME_URL}" class="nav-button">🏠 Home Network</a>
        <a href="{AWAY_URL}" class="nav-button">🌐 Away</a>
    </div>
    <p style="margin-top: 2em; font-size: 0.9em; color: #666;">
        Find books, manage your library, send to Kindle
    </p>
</div>
</body>
</html>"""

def create_page_header(title: str) -> str:
    """Create page header with navigation links."""
    return f"""<div class="page-header">
    <h1>{title}</h1>
    <div class="nav-links">
        <a href="{HOME_URL}" class="nav-button">🏠</a>
        <a href="{AWAY_URL}" class="nav-button">🌐</a>
    </div>
</div>"""


def create_toc_html() -> str:
    """Create Table of Contents."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
    <title>Table of Contents</title>
    <meta charset="UTF-8"/>
    <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{create_page_header("Contents")}
<div class="main-content">
    <div class="toc">
        <ul>
            <li><a href="content.xhtml#getting-started">Getting Started</a></li>
            <li><a href="content.xhtml#finding-books">Finding Books</a></li>
            <li><a href="content.xhtml#sending-to-kindle">Sending to Kindle</a></li>
            <li><a href="content.xhtml#random-books">Random Books</a></li>
            <li><a href="content.xhtml#library-view">Library & Folders</a></li>
            <li><a href="content.xhtml#feeds">Feeds & Automation</a></li>
            <li><a href="content.xhtml#tips">Tips & Tricks</a></li>
        </ul>
    </div>
</div>
<div class="page-footer">
    Press the forward button to begin.
</div>
</body>
</html>"""


def create_content_html() -> str:
    """Create main content with user guide."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head>
    <title>GoodBooks User Guide</title>
    <meta charset="UTF-8"/>
    <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
{create_page_header("GoodBooks Guide")}
<div class="main-content">

<h1 id="getting-started">Getting Started</h1>

<div class="info-box">
    <strong>What is GoodBooks?</strong>
    <p>GoodBooks is your personal ebook library. Find books, download them, and send them to your Kindle device—all from one place.</p>
</div>

<h2>Access GoodBooks</h2>
<p>Two ways to access depending on where you are:</p>
<ul>
    <li><strong>Home Network:</strong> Use the home button (🏠) to access your local server</li>
    <li><strong>Away from Home:</strong> Use the globe button (🌐) for secure remote access</li>
</ul>

<p>Both buttons are available on every page of this guide.</p>

<div class="section-break"></div>

<h1 id="finding-books">Finding Books</h1>

<h2>Search the Library</h2>
<ol>
    <li>Click the <strong>Search</strong> button from the main menu</li>
    <li>Type the book <strong>title</strong> or <strong>author name</strong></li>
    <li>Choose your preferred format:
        <ul>
            <li><strong>EPUB</strong> - Best for tablets and e-readers (recommended)</li>
            <li><strong>MOBI</strong> - Traditional Kindle format</li>
            <li><strong>PDF</strong> - For documents and special formatting</li>
        </ul>
    </li>
    <li>Optionally select genres to filter results</li>
    <li>Click <strong>Search</strong> to see results</li>
    <li>Download the book you want</li>
</ol>

<div class="info-box">
    <strong>Search Tips:</strong>
    <ul>
        <li>Try searching just the author name to see all their books</li>
        <li>Search "Harry Potter" to find specific titles</li>
        <li>Genre filters help you discover similar books</li>
    </ul>
</div>

<h2>Browse Your Feeds</h2>
<ol>
    <li>Go to <strong>Feeds</strong> to see your Goodreads lists</li>
    <li>Each feed shows how many books remain to download</li>
    <li>Click <strong>Refresh</strong> to check for new books</li>
    <li>Books are automatically added to your library</li>
</ol>

<div class="section-break"></div>

<h1 id="sending-to-kindle">Sending Books to Kindle</h1>

<h2>Send from Library</h2>
<ol>
    <li>Go to <strong>Library</strong> to see your downloaded books</li>
    <li>Click on a book to open its details</li>
    <li>Click <strong>Send to Kindle</strong></li>
    <li>The book will arrive on your device in minutes</li>
</ol>

<h2>Automatic Sending</h2>
<p>If you've configured auto-send in Settings:</p>
<ul>
    <li>Books from feeds automatically send to your Kindle</li>
    <li>Saves time—just review your library, books arrive automatically</li>
    <li>See your Kindle email in Settings → User Profile</li>
</ul>

<div class="info-box">
    <strong>Getting Your Kindle Email:</strong>
    <ol>
        <li>Go to Amazon account settings</li>
        <li>Find "Devices and Accessories"</li>
        <li>Look for your Kindle device name</li>
        <li>Your send-to email is shown there</li>
    </ol>
</div>

<div class="section-break"></div>

<h1 id="random-books">Random Books Feature</h1>

<h2>Discover Something New</h2>
<p>Can't decide what to read?</p>
<ol>
    <li>Click the <strong>Random</strong> button from the main menu</li>
    <li>A random book from your library appears</li>
    <li>Read the description and rating</li>
    <li>Click to send to Kindle or view more details</li>
    <li>Click again for another random book</li>
</ol>

<div class="info-box">
    <strong>Perfect for:</strong>
    <ul>
        <li>Breaking out of your usual reading patterns</li>
        <li>Rediscovering books you'd forgotten about</li>
        <li>Quick decision-making when browsing feels overwhelming</li>
    </ul>
</div>

<div class="section-break"></div>

<h1 id="library-view">Library & Folders</h1>

<h2>Browse Your Books</h2>
<p>The Library view shows all your downloaded books organized by folders.</p>
<ul>
    <li>Click on a folder to view books inside</li>
    <li>See book covers, titles, and authors</li>
    <li>Sort by name, date added, or author</li>
</ul>

<h2>Library Actions</h2>
<p>From any book in your library:</p>
<ul>
    <li><strong>View Details:</strong> See the full description, rating, and genres</li>
    <li><strong>Send to Kindle:</strong> Instantly send to your device</li>
    <li><strong>Download:</strong> Save the file for other uses</li>
    <li><strong>Read Online:</strong> Preview in your browser (where supported)</li>
</ul>

<h2>Metadata</h2>
<p>GoodBooks automatically improves book information:</p>
<ul>
    <li>Fetches descriptions from Goodreads</li>
    <li>Adds ratings and genres</li>
    <li>Updates covers</li>
    <li>Runs in the background—you don't need to do anything</li>
</ul>

<div class="section-break"></div>

<h1 id="feeds">Feeds & Automation</h1>

<h2>What Are Feeds?</h2>
<p>Feeds automatically download books from your Goodreads lists.</p>
<ol>
    <li>Set up feeds in <strong>Settings</strong></li>
    <li>Add your Goodreads list URL or RSS feed</li>
    <li>Choose your preferred format (EPUB, MOBI, PDF order)</li>
    <li>Enable auto-send if desired</li>
</ol>

<h2>Feeds Page</h2>
<p>The Feeds view shows:</p>
<ul>
    <li>Your active feeds</li>
    <li>Books remaining in each feed</li>
    <li>Last refresh date</li>
    <li>Manual refresh button for immediate updates</li>
</ul>

<h2>Automatic Refresh</h2>
<p>Feeds automatically check for new books:</p>
<ul>
    <li>Runs every 6 hours by default</li>
    <li>New books go straight to your Library</li>
    <li>If auto-send enabled, books go to Kindle</li>
</ul>

<div class="section-break"></div>

<h1 id="tips">Tips & Tricks</h1>

<h2>Search Tips</h2>
<ul>
    <li>Search just author names: "Margaret Atwood" finds all her books</li>
    <li>Use quotation marks for exact title matches</li>
    <li>Add genre filters to narrow results</li>
</ul>

<h2>Format Preference</h2>
<ul>
    <li><strong>EPUB is most compatible</strong> - Works on any e-reader</li>
    <li>MOBI works well on older Kindles</li>
    <li>PDF preserves original layout (takes more space)</li>
</ul>

<h2>Getting the Most from GoodBooks</h2>
<ul>
    <li>Set up multiple feeds for different reading lists</li>
    <li>Enable auto-send for your favorite feeds</li>
    <li>Use the random feature for discovery</li>
    <li>Check feeds regularly to stay current</li>
    <li>Sort library by date to see your newest additions</li>
</ul>

<h2>Troubleshooting</h2>
<ul>
    <li><strong>Book not arriving on Kindle?</strong>
        <ul>
            <li>Check your Kindle email is correct in Settings</li>
            <li>Try manual send from Library</li>
            <li>Verify SMTP settings in Settings</li>
        </ul>
    </li>
    <li><strong>Search results seem wrong?</strong>
        <ul>
            <li>Try a different search term</li>
            <li>Filter by genre to narrow results</li>
            <li>Author names work better than partial titles</li>
        </ul>
    </li>
    <li><strong>Feed not updating?</strong>
        <ul>
            <li>Click Refresh on Feeds page</li>
            <li>Check your Goodreads list URL is correct</li>
            <li>Verify internet connection</li>
        </ul>
    </li>
</ul>

<div class="section-break"></div>

<h2>Quick Reference</h2>
<p><strong>Main Features:</strong></p>
<ul>
    <li>📚 <strong>Library:</strong> Browse your books</li>
    <li>🔍 <strong>Search:</strong> Find new books</li>
    <li>📡 <strong>Feeds:</strong> Auto-download from Goodreads</li>
    <li>🎲 <strong>Random:</strong> Discover something new</li>
    <li>⚙️ <strong>Settings:</strong> Configure your preferences</li>
</ul>

<div class="page-footer">
    <strong>Need Help?</strong>
    Use the navigation buttons (🏠 🌐) at the top of each page to access GoodBooks.
</div>

</div>
</body>
</html>"""


def create_metadata_xml() -> str:
    """Create OPF metadata file."""
    now = datetime.now().isoformat()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uuid" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>GoodBooks User Guide</dc:title>
    <dc:creator>GoodBooks Team</dc:creator>
    <dc:language>en</dc:language>
    <dc:subject>Ebook Library, User Guide</dc:subject>
    <dc:description>Complete guide to using GoodBooks for finding books, managing your library, and sending to Kindle.</dc:description>
    <dc:date>{now}</dc:date>
    <dc:identifier id="uuid">goodbooks-guide-{now[:10]}</dc:identifier>
    <dc:rights>GoodBooks</dc:rights>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="style" href="style.css" media-type="text/css"/>
    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="toc" href="toc.xhtml" media-type="application/xhtml+xml"/>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="cover"/>
    <itemref idref="toc"/>
    <itemref idref="content"/>
  </spine>
  <guide>
    <reference type="cover" title="Cover" href="cover.xhtml"/>
    <reference type="toc" title="Table of Contents" href="toc.xhtml"/>
    <reference type="bodymatter" href="content.xhtml"/>
  </guide>
</package>"""


def create_ncx_toc() -> str:
    """Create NCX (DAISY) table of contents."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="goodbooks-guide"/>
    <meta name="dtb:depth" content="2"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>GoodBooks User Guide</text>
  </docTitle>
  <navMap>
    <navPoint id="cover" playOrder="1">
      <navLabel><text>Cover</text></navLabel>
      <content src="cover.xhtml"/>
    </navPoint>
    <navPoint id="toc" playOrder="2">
      <navLabel><text>Table of Contents</text></navLabel>
      <content src="toc.xhtml"/>
    </navPoint>
    <navPoint id="getting-started" playOrder="3">
      <navLabel><text>Getting Started</text></navLabel>
      <content src="content.xhtml#getting-started"/>
    </navPoint>
    <navPoint id="finding-books" playOrder="4">
      <navLabel><text>Finding Books</text></navLabel>
      <content src="content.xhtml#finding-books"/>
    </navPoint>
    <navPoint id="sending-to-kindle" playOrder="5">
      <navLabel><text>Sending to Kindle</text></navLabel>
      <content src="content.xhtml#sending-to-kindle"/>
    </navPoint>
    <navPoint id="random-books" playOrder="6">
      <navLabel><text>Random Books</text></navLabel>
      <content src="content.xhtml#random-books"/>
    </navPoint>
    <navPoint id="library-view" playOrder="7">
      <navLabel><text>Library & Folders</text></navLabel>
      <content src="content.xhtml#library-view"/>
    </navPoint>
    <navPoint id="feeds" playOrder="8">
      <navLabel><text>Feeds & Automation</text></navLabel>
      <content src="content.xhtml#feeds"/>
    </navPoint>
    <navPoint id="tips" playOrder="9">
      <navLabel><text>Tips & Tricks</text></navLabel>
      <content src="content.xhtml#tips"/>
    </navPoint>
  </navMap>
</ncx>"""


def create_container_xml() -> str:
    """Create container.xml for EPUB structure."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""


def build_epub(output_path: Path) -> bool:
    """Build the EPUB file."""
    print(f"Building GoodBooks.epub...")
    
    # Create temporary directory
    temp_dir = Path("/tmp/goodbooks_epub_build")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Create directory structure
    (temp_dir / "META-INF").mkdir(exist_ok=True)
    
    # Write files
    print("  Writing XHTML files...")
    (temp_dir / "cover.xhtml").write_text(COVER_HTML, encoding="utf-8")
    (temp_dir / "toc.xhtml").write_text(create_toc_html(), encoding="utf-8")
    (temp_dir / "content.xhtml").write_text(create_content_html(), encoding="utf-8")
    
    print("  Writing metadata...")
    (temp_dir / "content.opf").write_text(create_metadata_xml(), encoding="utf-8")
    (temp_dir / "toc.ncx").write_text(create_ncx_toc(), encoding="utf-8")
    (temp_dir / "META-INF" / "container.xml").write_text(create_container_xml(), encoding="utf-8")
    
    print("  Writing CSS...")
    (temp_dir / "style.css").write_text(KINDLE_CSS, encoding="utf-8")
    
    # Create mimetype file (must be first in zip, uncompressed)
    print("  Creating EPUB archive...")
    (temp_dir / "mimetype").write_text("application/epub+zip", encoding="utf-8")
    
    # Create EPUB zip file
    if output_path.exists():
        output_path.unlink()
    
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # Add mimetype first, uncompressed
        epub.write(
            temp_dir / "mimetype",
            "mimetype",
            compress_type=zipfile.ZIP_STORED
        )
        
        # Add all other files
        for file_path in temp_dir.rglob("*"):
            if file_path.is_file() and file_path.name != "mimetype":
                arcname = file_path.relative_to(temp_dir)
                epub.write(file_path, arcname, compress_type=zipfile.ZIP_DEFLATED)
    
    # Cleanup
    shutil.rmtree(temp_dir)
    
    # Verify
    if output_path.exists():
        size = output_path.stat().st_size
        size_kb = size / 1024
        print(f"✓ GoodBooks.epub created: {size_kb:.1f} KB")
        print(f"  Location: {output_path}")
        print(f"\n  Features:")
        print(f"  • Kindle 6\" diagonal format")
        print(f"  • Navigation links on every page")
        print(f"  • Home URL: {HOME_URL}")
        print(f"  • Away URL: {AWAY_URL}")
        print(f"  • Grayscale-friendly styling")
        return True
    else:
        print(f"✗ Failed to create EPUB")
        return False


def main():
    """Main entry point."""
    output = BASE_DIR / "GoodBooks.epub"
    
    try:
        success = build_epub(output)
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"Error building EPUB: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

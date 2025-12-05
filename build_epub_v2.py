#!/usr/bin/env python3
"""
Build GoodBooks.epub with proper EPUB3 structure and comprehensive content.
Cover: cover.png
Content order: cover, README, QUICKSTART, INSTALLER.md, User Guide, LICENSE
Each page includes header with navigation link and logo.
"""
import os
import re
import zipfile
import shutil
from pathlib import Path
from typing import List, Tuple
import html

PROJECT_DIR = Path("c:\\Users\\nickg\\OneDrive\\Documents\\progressbar")
BUILD_DIR = PROJECT_DIR / "epub_build_v2"
OEBPS_DIR = BUILD_DIR / "OEBPS"
META_INF_DIR = BUILD_DIR / "META-INF"
IMAGES_DIR = OEBPS_DIR / "images"
CONTENT_DIR = OEBPS_DIR / "content"

# Navigation link and styling
NAV_HEADER = """
    <div class="nav-header">
      <a href="http://192.168.0.9:5000" class="nav-link">
        <img src="../images/logo.png" alt="GoodBooks" class="nav-logo" />
        <span>Go to GoodBooks</span>
      </a>
    </div>
"""

BASE_CSS = """
  <style type="text/css">
    body {
      font-family: serif;
      line-height: 1.6;
      margin: 0;
      padding: 0;
      color: #333;
    }
    
    .nav-header {
      background-color: #f8f8f8;
      border-bottom: 2px solid #ddd;
      padding: 1em;
      margin-bottom: 2em;
      text-align: center;
    }
    
    .nav-link {
      display: inline-flex;
      align-items: center;
      gap: 0.5em;
      color: #0066cc;
      text-decoration: none;
      font-weight: bold;
    }
    
    .nav-link:hover {
      text-decoration: underline;
    }
    
    .nav-logo {
      height: 40px;
      width: auto;
    }
    
    .content {
      margin: 0 2em;
      max-width: 800px;
      margin-left: auto;
      margin-right: auto;
    }
    
    h1 {
      color: #222;
      border-bottom: 3px solid #0066cc;
      padding-bottom: 0.5em;
      margin-top: 1.5em;
      margin-bottom: 0.5em;
    }
    
    h1:first-child {
      margin-top: 0;
    }
    
    h2 {
      color: #444;
      border-bottom: 1px solid #ddd;
      padding-bottom: 0.3em;
      margin-top: 1.5em;
      margin-bottom: 0.5em;
    }
    
    h3 {
      color: #666;
      margin-top: 1.2em;
      margin-bottom: 0.4em;
    }
    
    h4, h5, h6 {
      color: #888;
      margin-top: 1em;
      margin-bottom: 0.3em;
    }
    
    p {
      margin: 0.5em 0;
      text-align: justify;
    }
    
    ul, ol {
      margin: 0.5em 0;
      padding-left: 2em;
    }
    
    li {
      margin: 0.3em 0;
    }
    
    code {
      background-color: #f5f5f5;
      padding: 0.2em 0.4em;
      font-family: monospace;
      font-size: 0.95em;
    }
    
    pre {
      background-color: #f5f5f5;
      border-left: 4px solid #0066cc;
      padding: 1em;
      overflow-x: auto;
      margin: 1em 0;
    }
    
    pre code {
      background: none;
      padding: 0;
    }
    
    a {
      color: #0066cc;
      text-decoration: underline;
    }
    
    a:visited {
      color: #551a8b;
    }
    
    blockquote {
      margin: 1em 0;
      padding-left: 1em;
      border-left: 4px solid #ddd;
      color: #666;
      font-style: italic;
    }
    
    strong, b {
      font-weight: bold;
      color: #222;
    }
    
    em, i {
      font-style: italic;
    }
    
    table {
      border-collapse: collapse;
      width: 100%;
      margin: 1em 0;
    }
    
    table th {
      background-color: #f0f0f0;
      border: 1px solid #ddd;
      padding: 0.5em;
      text-align: left;
      font-weight: bold;
    }
    
    table td {
      border: 1px solid #ddd;
      padding: 0.5em;
    }
    
    .pagebreak {
      page-break-after: always;
    }
  </style>
"""

def clean_build_dir():
    """Remove and recreate build directory."""
    import time
    if BUILD_DIR.exists():
        # Wait for any file locks to clear
        time.sleep(0.5)
        try:
            shutil.rmtree(BUILD_DIR)
        except PermissionError:
            # If permission denied, try removing with ignore_errors
            shutil.rmtree(BUILD_DIR, ignore_errors=True)
            # Create a new one
            if BUILD_DIR.exists():
                time.sleep(0.5)
                shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    OEBPS_DIR.mkdir(parents=True, exist_ok=True)
    META_INF_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

def markdown_to_xhtml(md_content: str, title: str, include_nav: bool = True) -> str:
    """Convert markdown to XHTML with optional navigation header."""
    # Escape HTML first
    html_content = html.escape(md_content)
    
    # Convert code blocks first (before converting other markdown)
    html_content = re.sub(
        r'```(.*?)\n(.*?)```',
        lambda m: f'<pre><code>{m.group(2)}</code></pre>',
        html_content,
        flags=re.DOTALL
    )
    
    # Convert headings
    html_content = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html_content, flags=re.MULTILINE)
    html_content = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html_content, flags=re.MULTILINE)
    html_content = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html_content, flags=re.MULTILINE)
    
    # Convert bold (must be before italic to avoid conflicts)
    html_content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html_content)
    html_content = re.sub(r'__(.*?)__', r'<strong>\1</strong>', html_content)
    
    # Convert italic
    html_content = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html_content)
    html_content = re.sub(r'_(.*?)_', r'<em>\1</em>', html_content)
    
    # Convert inline code
    html_content = re.sub(r'`([^`]+)`', r'<code>\1</code>', html_content)
    
    # Convert links [text](url)
    html_content = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', html_content)
    
    # Convert blockquotes
    html_content = re.sub(r'^> (.*?)$', r'<blockquote>\1</blockquote>', html_content, flags=re.MULTILINE)
    
    # Convert lists
    lines = html_content.split('\n')
    output_lines = []
    in_ul = False
    in_ol = False
    
    for line in lines:
        # Unordered list
        if line.strip().startswith('- '):
            if not in_ul:
                output_lines.append('<ul>')
                in_ul = True
            output_lines.append(f'<li>{line.strip()[2:]}</li>')
        # Ordered list
        else:
            match = re.match(r'^\d+\. ', line.strip())
            if match:
                if not in_ol:
                    output_lines.append('<ol>')
                    in_ol = True
                output_lines.append(f'<li>{line.strip()[match.end():]}</li>')
            else:
                if in_ul:
                    output_lines.append('</ul>')
                    in_ul = False
                if in_ol:
                    output_lines.append('</ol>')
                    in_ol = False
                output_lines.append(line)
    
    if in_ul:
        output_lines.append('</ul>')
    if in_ol:
        output_lines.append('</ol>')
    
    html_content = '\n'.join(output_lines)
    
    # Convert double newlines to paragraphs
    paragraphs = re.split(r'\n\n+', html_content)
    html_content = ''.join(
        f'<p>{p}</p>' if p.strip() and not p.strip().startswith('<') else p
        for p in paragraphs
        if p.strip()
    )
    
    # Build XHTML document
    nav = NAV_HEADER if include_nav else ""
    
    xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>{html.escape(title)}</title>
{BASE_CSS}
  </head>
  <body>
{nav}
    <div class="content">
{html_content}
    </div>
  </body>
</html>"""
    
    return xhtml

def create_cover_page() -> str:
    """Create cover page with image and title."""
    xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Cover</title>
    <style type="text/css">
      body {
        margin: 0;
        padding: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
        background-color: #fff;
      }
      img.cover-image {
        max-width: 100%;
        height: auto;
        display: block;
      }
    </style>
  </head>
  <body>
    <img class="cover-image" src="../images/cover.png" alt="GoodBooks Cover" />
  </body>
</html>"""
    return xhtml

def create_user_guide() -> str:
    """Generate User Guide from key documentation."""
    guide_md = """# GoodBooks User Guide

## Getting Started

GoodBooks is a self-hosted ebook library and Kindle delivery system that automates downloading books from your Goodreads reading lists and sending them to your Kindle device.

### Key Features

- **Goodreads Integration**: Import your reading lists and wishlists as RSS feeds
- **Smart Search**: Search Anna's Archive for books in multiple formats (EPUB, MOBI, PDF)
- **Batch Download**: Download multiple books simultaneously with auto-conversion
- **Personal Library**: Organize books with full metadata and cover images
- **Kindle Delivery**: Send books directly to your Kindle email address
- **Automated Feeds**: Schedule feed updates to keep your library current
- **Web Interface**: Beautiful, responsive interface for managing everything

## Setting Up Your System

### 1. Initial Configuration

After installation, access GoodBooks at `http://192.168.0.9:5000`

Configure:
- SMTP settings (for Kindle email delivery)
- Your Kindle email address
- Goodreads RSS feed URLs
- Preferred book formats

### 2. Adding Feeds

1. Go to the Feeds section
2. Add your Goodreads RSS feed URL
3. Choose preferred formats (EPUB, MOBI, PDF)
4. Set whether to auto-send to Kindle

Goodreads feed URLs:
- **Currently Reading**: `https://www.goodreads.com/review/list_rss/USER_ID?shelf=currently-reading`
- **To Read (Wishlist)**: `https://www.goodreads.com/review/list_rss/USER_ID?shelf=to-read`
- **Read**: `https://www.goodreads.com/review/list_rss/USER_ID?shelf=read`

### 3. Running Feeds

**Manual Feed Run:**
1. Click "Run Feeds" in the web interface
2. Monitor progress in the Activity Log
3. Downloaded books appear in your Library

**Automatic Feed Running:**
- Configure scheduled feed runs in Settings
- System runs feeds at your specified intervals
- Check Activity Log for results

## Understanding the Workflow

### Search Process

When you add a book to a feed:
1. System extracts book details from Goodreads
2. Searches Anna's Archive for matching editions
3. Selects best format based on your preferences
4. Falls back to manual search if not found

### Quality Filtering

The system filters out:
- Study guides and similar materials
- Low-quality editions
- Invalid file formats

### Deduplication

GoodBooks prevents sending the same book twice:
- Tracks downloaded books by filename
- Checks history before queuing Kindle deliveries
- Prevents duplicate entries in your library

## Managing Your Library

### Viewing Books

The Library section shows:
- All downloaded books with metadata
- Cover images from Goodreads
- Ratings and genres
- Direct download links

### Organizing Books

You can:
- View book details and ratings
- Re-download books in different formats
- Download directly to your device or Kindle

## Troubleshooting

### Feed Won't Run

1. Check your SMTP configuration
2. Verify Goodreads feed URL is correct
3. Ensure the server is running: `systemctl status goodbooks`
4. Check logs: `sudo journalctl -u goodbooks -f`

### Books Not Downloading

1. Verify Anna's Archive is accessible
2. Check your preferred format (some books may not exist in that format)
3. Try searching manually in the Search section
4. Check activity logs for error messages

### Kindle Delivery Failing

1. Verify SMTP settings are correct
2. Confirm your Kindle email address is correct
3. Add the sender email to your Amazon approved senders list
4. Check that Kindle email is registered with your Amazon account

### Service Issues

**Service won't start:**
```
sudo systemctl start goodbooks
sudo journalctl -u goodbooks -f
```

**Check service status:**
```
sudo systemctl status goodbooks
```

**View live logs:**
```
sudo journalctl -u goodbooks -f
```

**Restart service:**
```
sudo systemctl restart goodbooks
```

## Advanced Usage

### Custom Search

If automatic search doesn't find a book:
1. Go to the Search section
2. Enter book title or author
3. Browse results and select the best edition
4. Download to library or send to Kindle

### Batch Kindle Sending

1. Select multiple books from your library
2. Click "Send to Kindle"
3. Choose destination Kindle email
4. Books are queued and sent automatically

### Format Conversion

GoodBooks automatically converts books:
- EPUB → MOBI (for Kindle)
- PDF → EPUB (for flexibility)
- Maintains original if compatible

### Settings and Preferences

Configure:
- Notification preferences
- Feed update frequency
- Default book formats
- Download location
- Kindle email addresses (multiple users)

## Tips & Best Practices

1. **Start Small**: Add one feed and let it run successfully before adding more
2. **Monitor First Run**: Watch logs during initial feed runs to catch issues
3. **Verify SMTP**: Test SMTP settings before running feeds
4. **Check Formats**: Not all books exist in all formats; EPUB is most reliable
5. **Use Goodreads**: Rate and organize books on Goodreads to refine your feeds
6. **Regular Updates**: Run feeds weekly or as books are added to your lists

## Getting Help

- Check the Activity Log for detailed error messages
- Review service logs: `sudo journalctl -u goodbooks -f`
- Verify configuration in settings.json
- Test manually in the Search section before using feeds
"""
    return markdown_to_xhtml(guide_md, "User Guide")

def build_content_opf(content_files: List[Tuple[str, str]]) -> str:
    """Build content.opf manifest and spine."""
    manifest_items = [
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="cover-image" href="images/cover.png" media-type="image/png"/>',
        '    <item id="logo-image" href="images/logo.png" media-type="image/png"/>',
    ]
    
    spine_items = []
    
    for file_id, filename in content_files:
        manifest_items.append(f'    <item id="{file_id}" href="content/{filename}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'    <itemref idref="{file_id}"/>')
    
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>GoodBooks</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="uuid_id">urn:uuid:goodbooks-2025-12-05</dc:identifier>
    <dc:creator>nick gelinas</dc:creator>
    <dc:date>2025-12-05</dc:date>
    <dc:description>Your Personal Ebook Library & Kindle Delivery System</dc:description>
    <meta property="dcterms:modified">2025-12-05T00:00:00Z</meta>
    <meta name="cover" content="cover-image"/>
  </metadata>
  <manifest>
    <item id="cover" href="content/cover.xhtml" media-type="application/xhtml+xml"/>
{chr(10).join(manifest_items)}
  </manifest>
  <spine toc="ncx">
    <itemref idref="cover" linear="no"/>
{chr(10).join(spine_items)}
  </spine>
</package>"""
    return opf

def build_nav_xhtml(content_files: List[Tuple[str, str]]) -> str:
    """Build nav.xhtml for EPUB3 navigation."""
    nav_items = [
        """    <li><a href="content/cover.xhtml">Cover</a></li>"""
    ]
    
    labels = {
        'readme': 'README',
        'quickstart': 'Quick Start',
        'installer': 'Installation',
        'user_guide': 'User Guide',
        'license': 'License'
    }
    
    for file_id, filename in content_files:
        label = labels.get(file_id, filename.replace('.xhtml', '').replace('_', ' ').title())
        nav_items.append(f'    <li><a href="content/{filename}">{label}</a></li>')
    
    nav = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
  <head>
    <title>Table of Contents</title>
    <meta charset="UTF-8" />
  </head>
  <body>
    <nav epub:type="toc">
      <h1>Table of Contents</h1>
      <ol>
{chr(10).join(nav_items)}
      </ol>
    </nav>
  </body>
</html>"""
    return nav

def build_toc_ncx(content_files: List[Tuple[str, str]]) -> str:
    """Build toc.ncx for backwards compatibility."""
    nav_points = [
        """    <navPoint id="navpoint1" playOrder="1">
      <navLabel><text>Cover</text></navLabel>
      <content src="content/cover.xhtml"/>
    </navPoint>"""
    ]
    
    labels = {
        'readme': 'README',
        'quickstart': 'Quick Start',
        'installer': 'Installation',
        'user_guide': 'User Guide',
        'license': 'License'
    }
    
    for i, (file_id, filename) in enumerate(content_files, 2):
        label = labels.get(file_id, filename.replace('.xhtml', '').replace('_', ' ').title())
        nav_points.append(f"""    <navPoint id="navpoint{i}" playOrder="{i}">
      <navLabel><text>{label}</text></navLabel>
      <content src="content/{filename}"/>
    </navPoint>""")
    
    toc = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:goodbooks-2025-12-05"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>GoodBooks</text></docTitle>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>"""
    return toc

def copy_images():
    """Copy cover and create logo placeholder."""
    cover_src = PROJECT_DIR / "cover.png"
    if cover_src.exists():
        shutil.copy(cover_src, IMAGES_DIR / "cover.png")
        print("  ✓ Copied cover.png")
    else:
        print("  ⚠ Warning: cover.png not found")
    
    # Create a simple logo placeholder if needed (can be replaced with actual logo)
    # For now, we'll use the cover as logo too
    logo_src = PROJECT_DIR / "cover.png"
    if logo_src.exists():
        shutil.copy(logo_src, IMAGES_DIR / "logo.png")
        print("  ✓ Copied logo.png")

def main():
    """Build the EPUB."""
    print("Building GoodBooks EPUB v2...")
    print()
    
    # Clean and create directories
    print("Creating directory structure...")
    clean_build_dir()
    
    # Create required EPUB files
    print("Creating EPUB metadata...")
    
    # mimetype (must be first in ZIP, uncompressed)
    (BUILD_DIR / "mimetype").write_text("application/epub+zip")
    
    # container.xml
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    (META_INF_DIR / "container.xml").write_text(container)
    
    # Copy images
    print("Processing images...")
    copy_images()
    
    # Read source files
    print("Converting content...")
    content_files = []
    
    # 1. Cover
    (CONTENT_DIR / "cover.xhtml").write_text(create_cover_page())
    print("  ✓ Created cover page")
    
    # 2. README
    readme_path = PROJECT_DIR / "README.md"
    if readme_path.exists():
        readme_content = readme_path.read_text(encoding='utf-8')
        (CONTENT_DIR / "readme.xhtml").write_text(markdown_to_xhtml(readme_content, "README"), encoding='utf-8')
        content_files.append(('readme', 'readme.xhtml'))
        print("  ✓ Converted README.md")
    
    # 3. QUICKSTART
    quickstart_path = PROJECT_DIR / "QUICKSTART.md"
    if quickstart_path.exists():
        quickstart_content = quickstart_path.read_text(encoding='utf-8')
        (CONTENT_DIR / "quickstart.xhtml").write_text(markdown_to_xhtml(quickstart_content, "Quick Start"), encoding='utf-8')
        content_files.append(('quickstart', 'quickstart.xhtml'))
        print("  ✓ Converted QUICKSTART.md")
    
    # 4. INSTALLER
    installer_path = PROJECT_DIR / "INSTALLER.md"
    if installer_path.exists():
        installer_content = installer_path.read_text(encoding='utf-8')
        (CONTENT_DIR / "installer.xhtml").write_text(markdown_to_xhtml(installer_content, "Installation"), encoding='utf-8')
        content_files.append(('installer', 'installer.xhtml'))
        print("  ✓ Converted INSTALLER.md")
    
    # 5. User Guide (generated)
    (CONTENT_DIR / "user_guide.xhtml").write_text(create_user_guide(), encoding='utf-8')
    content_files.append(('user_guide', 'user_guide.xhtml'))
    print("  ✓ Created User Guide")
    
    # 6. LICENSE
    license_path = PROJECT_DIR / "LICENSE"
    if license_path.exists():
        license_content = license_path.read_text(encoding='utf-8')
        (CONTENT_DIR / "license.xhtml").write_text(markdown_to_xhtml(license_content, "License"), encoding='utf-8')
        content_files.append(('license', 'license.xhtml'))
        print("  ✓ Converted LICENSE")
    
    # Generate manifest and spine
    print("Generating metadata files...")
    (OEBPS_DIR / "content.opf").write_text(build_content_opf(content_files), encoding='utf-8')
    (OEBPS_DIR / "toc.ncx").write_text(build_toc_ncx(content_files), encoding='utf-8')
    (OEBPS_DIR / "nav.xhtml").write_text(build_nav_xhtml(content_files), encoding='utf-8')
    print("  ✓ Generated content.opf, toc.ncx, nav.xhtml")
    
    # Package as EPUB
    print("Packaging EPUB...")
    epub_path = PROJECT_DIR / "GoodBooks.epub"
    if epub_path.exists():
        epub_path.unlink()
    
    with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as z:
        # Add mimetype first (uncompressed, as per EPUB spec)
        z.write(BUILD_DIR / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)
        
        # Add all other files
        for root, dirs, files in os.walk(BUILD_DIR):
            for file in files:
                if file == 'mimetype':
                    continue
                file_path = Path(root) / file
                arcname = str(file_path.relative_to(BUILD_DIR))
                z.write(file_path, arcname)
    
    print(f"  ✓ Created GoodBooks.epub")
    
    # Cleanup
    print("Cleaning up...")
    shutil.rmtree(BUILD_DIR)
    
    # Summary
    print()
    print("=" * 60)
    print("✓ EPUB Build Complete!")
    print("=" * 60)
    print(f"File: {epub_path}")
    print(f"Size: {epub_path.stat().st_size / 1024:.1f} KB")
    print()
    print("Content structure:")
    print("  1. Cover (cover.png)")
    print("  2. README")
    print("  3. Quick Start")
    print("  4. Installation Guide")
    print("  5. User Guide")
    print("  6. License")
    print()
    print("Features:")
    print("  ✓ EPUB3 compliant")
    print("  ✓ Navigation header on each page")
    print("  ✓ Link to http://192.168.0.9:5000")
    print("  ✓ Logo image on header")
    print("  ✓ Professional CSS styling")
    print("  ✓ Table of contents")
    print()

if __name__ == "__main__":
    main()

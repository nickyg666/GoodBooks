#!/usr/bin/env python3
"""
Utility module for creating comprehensive EPUB files with documentation.
Includes web UI shortcut with cover image and full documentation guides.
"""

import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, List
from html import escape as html_escape


# EPUB MIME type constants
EPUB_MIME_TYPE = "application/epub+zip"
XHTML_MIME_TYPE = "application/xhtml+xml"
CSS_MIME_TYPE = "text/css"


def _escape_xml(text: str) -> str:
    """Escape special XML characters."""
    if not isinstance(text, str):
        text = str(text)
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))


def _get_image_extension(image_path: Optional[Path]) -> str:
    """Get image file extension without the dot."""
    if not image_path:
        return ""
    return Path(image_path).suffix.lstrip('.')


def _get_current_date() -> str:
    """Get current date in YYYY-MM-DD format."""
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d')


def _get_current_timestamp() -> str:
    """Get current timestamp in ISO 8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def create_comprehensive_documentation_epub(
    title: str,
    web_url: str,
    output_path: Optional[Path] = None,
    cover_image_path: Optional[Path] = None,
    documentation_files: Optional[List[Path]] = None,
) -> Path:
    """
    Create comprehensive EPUB with web UI shortcut and documentation.
    
    Args:
        title: Book title
        web_url: Full URL to GoodBooks web interface
        output_path: Where to save the EPUB
        cover_image_path: Optional path to cover image
        documentation_files: List of markdown files to include
    
    Returns:
        Path to created EPUB file
    """
    if output_path is None:
        output_path = Path(tempfile.gettempdir()) / f"{title}.epub"
    else:
        output_path = Path(output_path)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Load and validate cover image
    cover_data = None
    cover_mime_type = None
    if cover_image_path:
        cover_path = Path(cover_image_path)
        if cover_path.exists():
            ext = cover_path.suffix.lower()
            mime_types = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.svg': 'image/svg+xml',
            }
            if ext in mime_types:
                cover_mime_type = mime_types[ext]
                cover_data = cover_path.read_bytes()
    
    # Prepare documentation chapters
    doc_chapters = []
    if documentation_files:
        for doc_path in documentation_files:
            if Path(doc_path).exists():
                try:
                    content = Path(doc_path).read_text(encoding='utf-8')
                    filename = Path(doc_path).stem
                    doc_chapters.append({
                        'filename': filename,
                        'content': content
                    })
                except Exception:
                    pass
    
    # Create EPUB
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # 1. mimetype (must be first, uncompressed)
        epub.writestr('mimetype', EPUB_MIME_TYPE, compress_type=zipfile.ZIP_STORED)
        
        # 2. META-INF/container.xml
        container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>'''
        epub.writestr('META-INF/container.xml', container_xml)
        
        # Build manifest and spine for all chapters
        manifest_items = [
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
            '<item id="style" href="style.css" media-type="text/css"/>',
        ]
        
        spine_items = []
        
        # Cover chapter (with image if available)
        manifest_items.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine_items.append('<itemref idref="cover"/>')
        
        if cover_data and cover_mime_type:
            cover_filename = f"cover-image.{_get_image_extension(cover_image_path)}"
            manifest_items.append(f'<item id="cover-image" href="{cover_filename}" media-type="{cover_mime_type}"/>')
        
        # Documentation chapters
        for idx, doc in enumerate(doc_chapters):
            chapter_id = f"chapter{idx+1}"
            chapter_file = f"chapter{idx+1}.xhtml"
            manifest_items.append(f'<item id="{chapter_id}" href="{chapter_file}" media-type="{XHTML_MIME_TYPE}"/>')
            spine_items.append(f'<itemref idref="{chapter_id}"/>')
        
        # 3. OEBPS/content.opf (package metadata)
        content_opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" unique-identifier="uuid_id" xmlns="http://www.idpf.org/2007/opf">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="uuid_id">goodbooks-guide-{hash(web_url)}</dc:identifier>
        <dc:title>{_escape_xml(title)}</dc:title>
        <dc:creator>GoodBooks</dc:creator>
        <dc:language>en</dc:language>
        <dc:date>{_get_current_date()}</dc:date>
        <meta property="dcterms:modified">{_get_current_timestamp()}</meta>
    </metadata>
    <manifest>
        {chr(10).join(manifest_items)}
    </manifest>
    <spine toc="ncx">
        {chr(10).join(spine_items)}
    </spine>
</package>'''
        epub.writestr('OEBPS/content.opf', content_opf)
        
        # 4. OEBPS/toc.ncx (table of contents)
        toc_items = ['<navPoint id="navpoint1" playOrder="1"><navLabel><text>Welcome</text></navLabel><content src="cover.xhtml"/></navPoint>']
        for idx, doc in enumerate(doc_chapters):
            navpoint_id = f"navpoint{idx+2}"
            order = idx + 2
            title_text = doc['filename'].replace('_', ' ').replace('-', ' ').title()
            toc_items.append(f'<navPoint id="{navpoint_id}" playOrder="{order}"><navLabel><text>{html_escape(title_text)}</text></navLabel><content src="chapter{idx+1}.xhtml"/></navPoint>')
        
        toc_ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <head>
        <meta name="dtb:uid" content="goodbooks-guide"/>
        <meta name="dtb:depth" content="1"/>
        <meta name="dtb:totalPageCount" content="0"/>
        <meta name="dtb:maxPageNumber" content="0"/>
    </head>
    <docTitle><text>GoodBooks Guide</text></docTitle>
    <navMap>
        {chr(10).join(toc_items)}
    </navMap>
</ncx>'''
        epub.writestr('OEBPS/toc.ncx', toc_ncx)
        
        # 5. OEBPS/style.css
        style_css = '''body {
    font-family: Georgia, serif;
    line-height: 1.6;
    margin: 1.5em;
    color: #333;
}

h1 {
    color: #1a5490;
    font-size: 2.2em;
    margin-top: 0.5em;
    margin-bottom: 0.3em;
    border-bottom: 2px solid #ddd;
    padding-bottom: 0.2em;
}

h2 {
    color: #2b6cb8;
    font-size: 1.8em;
    margin-top: 0.8em;
    margin-bottom: 0.3em;
}

h3 {
    color: #3b7cc8;
    font-size: 1.4em;
    margin-top: 0.6em;
    margin-bottom: 0.2em;
}

p {
    margin: 0.8em 0;
    text-align: justify;
}

code {
    background-color: #f5f5f5;
    padding: 0.2em 0.4em;
    font-family: monospace;
    font-size: 0.9em;
    color: #d63384;
}

pre {
    background-color: #f8f8f8;
    border-left: 4px solid #1a5490;
    padding: 1em;
    overflow-x: auto;
    font-family: monospace;
    font-size: 0.9em;
    margin: 1em 0;
}

ul, ol {
    margin: 0.8em 0;
    padding-left: 2em;
}

li {
    margin: 0.4em 0;
}

strong {
    color: #1a5490;
    font-weight: bold;
}

em {
    font-style: italic;
    color: #555;
}

blockquote {
    border-left: 4px solid #ddd;
    padding-left: 1em;
    margin-left: 0;
    color: #666;
    font-style: italic;
}

.cover-image {
    text-align: center;
    margin: 2em 0;
}

.cover-image img {
    max-width: 100%;
    height: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
}

table th, table td {
    border: 1px solid #ddd;
    padding: 0.5em;
    text-align: left;
}

table th {
    background-color: #f5f5f5;
    font-weight: bold;
}
'''
        epub.writestr('OEBPS/style.css', style_css)
        
        # 6. Cover chapter with web UI shortcut and cover image
        cover_image_html = ''
        if cover_data and cover_mime_type:
            cover_filename = f"cover-image.{_get_image_extension(cover_image_path)}"
            cover_image_html = f'''    <div class="cover-image">
        <img src="{cover_filename}" alt="GoodBooks Cover"/>
    </div>
'''
        
        cover_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
    <meta charset="UTF-8"/>
    <title>Welcome to GoodBooks</title>
    <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
    <h1>🎉 Welcome to GoodBooks!</h1>
{cover_image_html}
    <div>
        <p>Your personal ebook library and Kindle delivery system is now ready to use.</p>
        
        <p><strong>Click below to access your GoodBooks web interface:</strong></p>
        
        <p style="text-align: center;">
            <a href="{html_escape(web_url)}" style="display: inline-block; padding: 1em 2em; background-color: #4CAF50; color: white; text-decoration: none; border-radius: 4px; font-size: 1.2em;">
                📖 Open GoodBooks Web Interface
            </a>
        </p>
        
        <p>Or copy and paste this address in your browser:</p>
        
        <div style="background-color: #f0f0f0; padding: 1em; margin: 1em 0; border-left: 4px solid #4CAF50; font-family: monospace; word-break: break-all;">
            {html_escape(web_url)}
        </div>
        
        <p><strong>📚 Features Included in This Guide:</strong></p>
        <ul>
            <li>Quick start guide</li>
            <li>Complete deployment checklist</li>
            <li>Configuration walkthrough</li>
            <li>Full documentation and reference</li>
            <li>Troubleshooting tips</li>
        </ul>
        
        <p><strong>🚀 Quick Start:</strong></p>
        <ol>
            <li>Click the link above to open GoodBooks</li>
            <li>Add your Goodreads RSS feed URL</li>
            <li>Configure SMTP settings for Kindle email</li>
            <li>Click "Run Feeds" to download your first books</li>
            <li>Watch your Kindle library grow!</li>
        </ol>
    </div>
</body>
</html>'''
        epub.writestr('OEBPS/cover.xhtml', cover_xhtml)
        
        # 7. Documentation chapters
        for idx, doc in enumerate(doc_chapters):
            chapter_content = doc['content']
            
            # Escape HTML in content first
            chapter_content = html_escape(chapter_content)
            
            # Convert markdown headers
            chapter_content = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', chapter_content, flags=re.MULTILINE)
            chapter_content = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', chapter_content, flags=re.MULTILINE)
            chapter_content = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', chapter_content, flags=re.MULTILINE)
            
            # Convert paragraphs
            chapter_content = re.sub(r'\n\n+', '</p><p>', chapter_content)
            chapter_content = f'<p>{chapter_content}</p>'
            
            chapter_title = doc['filename'].replace('_', ' ').replace('-', ' ').title()
            
            chapter_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
    <meta charset="UTF-8"/>
    <title>{html_escape(chapter_title)}</title>
    <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
    {chapter_content}
</body>
</html>'''
            epub.writestr(f'OEBPS/chapter{idx+1}.xhtml', chapter_xhtml)
        
        # Add cover image if available
        if cover_data and cover_mime_type:
            cover_filename = f"cover-image.{_get_image_extension(cover_image_path)}"
            epub.writestr(f'OEBPS/{cover_filename}', cover_data)
    
    return output_path


def create_web_ui_shortcut_epub(
    title: str,
    web_url: str,
    author: str = "GoodBooks Installer",
    output_path: Optional[Path] = None,
    cover_image_path: Optional[Path] = None,
) -> Path:
    """
    Create simple EPUB with web UI shortcut.
    
    Args:
        title: Book title
        web_url: Full URL to GoodBooks web interface
        author: Author name
        output_path: Where to save the EPUB
        cover_image_path: Optional path to cover image
    
    Returns:
        Path to created EPUB file
    """
    if output_path is None:
        output_path = Path(tempfile.gettempdir()) / f"{title}.epub"
    else:
        output_path = Path(output_path)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Validate cover image if provided
    cover_data = None
    cover_mime_type = None
    if cover_image_path:
        cover_path = Path(cover_image_path)
        if cover_path.exists():
            ext = cover_path.suffix.lower()
            mime_types = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.svg': 'image/svg+xml',
            }
            if ext in mime_types:
                cover_mime_type = mime_types[ext]
                cover_data = cover_path.read_bytes()
    
    # Create EPUB
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as epub:
        # 1. mimetype
        epub.writestr('mimetype', EPUB_MIME_TYPE, compress_type=zipfile.ZIP_STORED)
        
        # 2. META-INF/container.xml
        container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>'''
        epub.writestr('META-INF/container.xml', container_xml)
        
        # 3. OEBPS/content.opf
        content_opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package version="3.0" unique-identifier="uuid_id" xmlns="http://www.idpf.org/2007/opf">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="uuid_id">goodbooks-webui-shortcut-{hash(web_url)}</dc:identifier>
        <dc:title>{_escape_xml(title)}</dc:title>
        <dc:creator>{_escape_xml(author)}</dc:creator>
        <dc:language>en</dc:language>
        <dc:date>{_get_current_date()}</dc:date>
        <meta property="dcterms:modified">{_get_current_timestamp()}</meta>
    </metadata>
    <manifest>
        <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
        <item id="chapter1" href="chapter1.xhtml" media-type="{XHTML_MIME_TYPE}"/>
        <item id="style" href="style.css" media-type="{CSS_MIME_TYPE}"/>
        {f'<item id="cover" href="cover.{_get_image_extension(cover_image_path)}" media-type="{cover_mime_type}"/>' if cover_data else ''}
    </manifest>
    <spine toc="ncx">
        <itemref idref="chapter1"/>
    </spine>
</package>'''
        epub.writestr('OEBPS/content.opf', content_opf)
        
        # 4. OEBPS/toc.ncx
        toc_ncx = '''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <head>
        <meta name="dtb:uid" content="goodbooks-webui"/>
        <meta name="dtb:depth" content="1"/>
        <meta name="dtb:totalPageCount" content="0"/>
        <meta name="dtb:maxPageNumber" content="0"/>
    </head>
    <docTitle>
        <text>GoodBooks Web UI</text>
    </docTitle>
    <navMap>
        <navPoint id="navpoint1" playOrder="1">
            <navLabel>
                <text>Open GoodBooks</text>
            </navLabel>
            <content src="chapter1.xhtml"/>
        </navPoint>
    </navMap>
</ncx>'''
        epub.writestr('OEBPS/toc.ncx', toc_ncx)
        
        # 5. OEBPS/style.css
        style_css = '''body {
    font-family: Georgia, serif;
    margin: 2em;
    text-align: center;
    background-color: #f5f5f5;
}

h1 {
    color: #333;
    font-size: 2.5em;
    margin-bottom: 0.5em;
}

.instructions {
    background-color: white;
    padding: 2em;
    border-radius: 8px;
    margin: 2em 0;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    max-width: 600px;
    margin-left: auto;
    margin-right: auto;
}

.instructions p {
    font-size: 1.1em;
    line-height: 1.6;
    color: #555;
    margin: 1em 0;
}

.button-link {
    display: inline-block;
    padding: 1em 2em;
    margin: 1em;
    background-color: #4CAF50;
    color: white;
    text-decoration: none;
    border-radius: 4px;
    font-size: 1.2em;
    transition: background-color 0.3s;
}

.button-link:hover {
    background-color: #45a049;
}

.url-display {
    background-color: #f0f0f0;
    padding: 1em;
    margin: 1em 0;
    border-left: 4px solid #4CAF50;
    font-family: monospace;
    word-break: break-all;
}
'''
        epub.writestr('OEBPS/style.css', style_css)
        
        # Add cover image if provided
        if cover_data and cover_image_path:
            cover_filename = f"cover.{_get_image_extension(Path(cover_image_path))}"
            epub.writestr(f'OEBPS/{cover_filename}', cover_data)
        
        # 6. OEBPS/chapter1.xhtml
        chapter_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
    <meta charset="UTF-8"/>
    <title>{_escape_xml(title)}</title>
    <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
    <h1>🎉 Welcome to GoodBooks!</h1>
    
    <div class="instructions">
        <p>Your personal ebook library is now ready to use.</p>
        
        <p>Click the button below to open your GoodBooks web interface:</p>
        
        <a href="{_escape_xml(web_url)}" class="button-link">
            📖 Open GoodBooks Web Interface
        </a>
        
        <p>Or copy and paste this address in your browser:</p>
        
        <div class="url-display">
            {_escape_xml(web_url)}
        </div>
        
        <p><strong>Features:</strong></p>
        <ul style="text-align: left; display: inline-block;">
            <li>📚 Browse your complete book library</li>
            <li>🔍 Search for new books from Anna's Archive</li>
            <li>📧 Send books to your Kindle device</li>
            <li>⚙️ Manage RSS feed subscriptions</li>
            <li>📱 Access from any device on your network</li>
        </ul>
    </div>
</body>
</html>'''
        epub.writestr('OEBPS/chapter1.xhtml', chapter_xhtml)
    
    return output_path


if __name__ == "__main__":
    test_epub = create_web_ui_shortcut_epub(
        title="GoodBooks Web UI Shortcut",
        web_url="http://192.168.1.100:5000",
    )
    print(f"Created test EPUB at: {test_epub}")
    print(f"File size: {test_epub.stat().st_size} bytes")

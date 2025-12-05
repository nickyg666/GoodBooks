#!/usr/bin/env python3
"""
Rebuild GoodBooks.epub to include all markdown files with cover logo on each page.
"""
import os
import re
import zipfile
from pathlib import Path
import html
from datetime import datetime
import shutil

# Markdown files to include (in order)
MD_FILES = [
    "AGENTS.md",
    "BUGFIXES.md",
    "DEPLOYMENT_CHECKLIST.md",
    "IMPLEMENTATION_SUMMARY.md",
    "INSTALLER.md",
    "INSTALLER_ARCHITECTURE.md",
    "INSTALLER_GUIDE.md",
    "INSTALLER_IMPLEMENTATION.md",
    "INSTALLER_INDEX.md",
    "INSTALLER_QUICK_REFERENCE.md",
    "INSTALLER_TECHNICAL.md",
    "PROJECT_COMPLETION_SUMMARY.md",
    "QUICKSTART.md",
    "README.md",
]

COVER_IMAGE = "images/goodbooks_cover.jpg"
DOCUMENTS_DIR = "c:\\Users\\nickg\\OneDrive\\Documents\\progressbar"

def markdown_to_html(md_content: str, title: str) -> str:
    """Convert markdown to basic HTML."""
    html_content = html.escape(md_content)
    
    # Convert headings
    html_content = re.sub(r'^### (.*?)$', r'<h3>\1</h3>', html_content, flags=re.MULTILINE)
    html_content = re.sub(r'^## (.*?)$', r'<h2>\1</h2>', html_content, flags=re.MULTILINE)
    html_content = re.sub(r'^# (.*?)$', r'<h1>\1</h1>', html_content, flags=re.MULTILINE)
    
    # Convert bold and italic
    html_content = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html_content)
    html_content = re.sub(r'\*(.*?)\*', r'<em>\1</em>', html_content)
    html_content = re.sub(r'__(.*?)__', r'<strong>\1</strong>', html_content)
    html_content = re.sub(r'_(.*?)_', r'<em>\1</em>', html_content)
    
    # Convert links
    html_content = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', html_content)
    
    # Convert line breaks to paragraphs
    paragraphs = html_content.split('\n\n')
    html_content = ''.join(f'<p>{p}</p>' for p in paragraphs if p.strip())
    
    # Create full XHTML document
    xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
  <head>
    <title>{html.escape(title)}</title>
    <meta charset="UTF-8" />
    <style type="text/css">
      body {{
        font-family: serif;
        margin: 2em;
        line-height: 1.6;
      }}
      img.logo {{
        max-width: 150px;
        height: auto;
        margin-bottom: 2em;
        display: block;
        border-bottom: 1px solid #ccc;
        padding-bottom: 1em;
      }}
      h1 {{
        color: #333;
        border-bottom: 2px solid #333;
        padding-bottom: 0.5em;
      }}
      h2 {{
        color: #666;
        margin-top: 1.5em;
      }}
      h3 {{
        color: #999;
      }}
      a {{
        color: #0066cc;
        text-decoration: none;
      }}
      a:hover {{
        text-decoration: underline;
      }}
      code {{
        background-color: #f5f5f5;
        padding: 0.2em 0.4em;
        font-family: monospace;
      }}
      pre {{
        background-color: #f5f5f5;
        padding: 1em;
        overflow-x: auto;
        border-left: 3px solid #ccc;
      }}
      pre code {{
        padding: 0;
      }}
    </style>
  </head>
  <body>
    <img class="logo" src="../{COVER_IMAGE}" alt="GoodBooks Logo" />
    <div class="content">
      {html_content}
    </div>
  </body>
</html>"""
    
    return xhtml

def create_manifest_items(md_files: list) -> str:
    """Create manifest items for all markdown documents."""
    items = [
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />',
        '    <item id="content" href="content.xhtml" media-type="application/xhtml+xml" />',
        '    <item id="cover-image" href="images/goodbooks_cover.jpg" media-type="image/jpeg" />',
    ]
    
    for i, md_file in enumerate(md_files):
        file_id = f"doc{i}"
        file_name = md_file.replace('.md', '.xhtml')
        items.append(f'    <item id="{file_id}" href="docs/{file_name}" media-type="application/xhtml+xml" />')
    
    return '\n'.join(items)

def create_spine_items(md_files: list) -> str:
    """Create spine items for all documents."""
    items = ['    <itemref idref="content" />']
    
    for i in range(len(md_files)):
        items.append(f'    <itemref idref="doc{i}" />')
    
    return '\n'.join(items)

def create_toc_ncx(md_files: list) -> str:
    """Create table of contents NCX file."""
    nav_points = [
        """    <navPoint id="navpoint-1">
      <navLabel>
        <text>GoodBooks</text>
      </navLabel>
      <content src="content.xhtml" />
    </navPoint>"""
    ]
    
    for i, md_file in enumerate(md_files, 2):
        title = md_file.replace('.md', '').replace('_', ' ')
        file_name = md_file.replace('.md', '.xhtml')
        nav_points.append(f"""    <navPoint id="navpoint-{i}">
      <navLabel>
        <text>{html.escape(title)}</text>
      </navLabel>
      <content src="docs/{file_name}" />
    </navPoint>""")
    
    toc = """<?xml version='1.0' encoding='utf-8'?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:f336adc4-19e6-429c-bdeb-4ac8977d437f" />
    <meta name="dtb:depth" content="2" />
    <meta name="dtb:totalPageCount" content="0" />
    <meta name="dtb:maxPageNumber" content="0" />
  </head>
  <docTitle>
    <text>GoodBooks</text>
  </docTitle>
  <navMap>
""" + '\n'.join(nav_points) + """
  </navMap>
</ncx>"""
    
    return toc

# Create temporary build directory
build_dir = "epub_build"
if os.path.exists(build_dir):
    shutil.rmtree(build_dir)

oebps_dir = os.path.join(build_dir, "OEBPS")
meta_inf_dir = os.path.join(build_dir, "META-INF")
docs_dir = os.path.join(oebps_dir, "docs")
images_dir = os.path.join(oebps_dir, "images")

os.makedirs(docs_dir, exist_ok=True)
os.makedirs(images_dir, exist_ok=True)
os.makedirs(meta_inf_dir, exist_ok=True)

print("Creating EPUB with all markdown files...")

# Copy cover image
cover_src = os.path.join(DOCUMENTS_DIR, "GoodBooks.epub")
if os.path.exists(cover_src):
    # Extract cover from old EPUB
    with zipfile.ZipFile(cover_src, 'r') as z:
        try:
            cover_data = z.read('OEBPS/images/goodbooks_cover.jpg')
            with open(os.path.join(images_dir, 'goodbooks_cover.jpg'), 'wb') as f:
                f.write(cover_data)
        except:
            print("  Warning: Could not extract cover from old EPUB")

# Convert markdown files to XHTML
for i, md_file in enumerate(MD_FILES):
    md_path = os.path.join(DOCUMENTS_DIR, md_file)
    if not os.path.exists(md_path):
        print(f"  Warning: {md_file} not found")
        continue
    
    with open(md_path, 'r', encoding='utf-8') as f:
        md_content = f.read()
    
    title = md_file.replace('.md', '').replace('_', ' ')
    xhtml_content = markdown_to_html(md_content, title)
    
    xhtml_file = md_file.replace('.md', '.xhtml')
    xhtml_path = os.path.join(docs_dir, xhtml_file)
    
    with open(xhtml_path, 'w', encoding='utf-8') as f:
        f.write(xhtml_content)
    
    print(f"  Created {xhtml_file}")

# Create content.opf
manifest = create_manifest_items(MD_FILES)
spine = create_spine_items(MD_FILES)

content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>GoodBooks</dc:title>
    <dc:language>en</dc:language>
    <dc:identifier id="BookId">urn:uuid:f336adc4-19e6-429c-bdeb-4ac8977d437f</dc:identifier>
    <dc:creator>GoodBooks</dc:creator>
    <dc:date>2025-12-05</dc:date>
    <meta name="cover" content="cover-image" />
  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
  <guide>
    <reference type="cover" title="Cover" href="content.xhtml" />
  </guide>
</package>"""

with open(os.path.join(oebps_dir, "content.opf"), 'w', encoding='utf-8') as f:
    f.write(content_opf)
print("  Updated content.opf")

# Create toc.ncx
toc_ncx = create_toc_ncx(MD_FILES)
with open(os.path.join(oebps_dir, "toc.ncx"), 'w', encoding='utf-8') as f:
    f.write(toc_ncx)
print("  Updated toc.ncx")

# Create content.xhtml
content_xhtml = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
  <head>
    <title>GoodBooks</title>
    <meta charset="UTF-8" />
    <style type="text/css">
      body {
        font-family: serif;
        text-align: center;
        margin: 2em;
      }
      img.logo {
        max-width: 80%;
        height: auto;
        margin-bottom: 2em;
      }
      a {
        font-size: 1.2em;
        color: #0066cc;
        text-decoration: none;
        margin: 1em 0;
        display: block;
      }
      a:hover {
        text-decoration: underline;
      }
      .toc-link {
        font-size: 1em;
        margin-top: 2em;
        padding-top: 2em;
        border-top: 1px solid #ccc;
      }
      .toc-link a {
        display: inline;
      }
    </style>
  </head>
  <body>
    <div>
      <img class="logo" src="images/goodbooks_cover.jpg" alt="GoodBooks Logo" />
    </div>
    <p>
      <a href="http://192.168.0.9:5000">Browse and download new books with GoodBooks!</a>
    </p>
    <p class="toc-link">
      <a href="docs/AGENTS.xhtml">Start reading documentation →</a>
    </p>
  </body>
</html>"""

with open(os.path.join(oebps_dir, "content.xhtml"), 'w', encoding='utf-8') as f:
    f.write(content_xhtml)
print("  Updated content.xhtml with documentation link")

# Create container.xml
container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>"""

with open(os.path.join(meta_inf_dir, "container.xml"), 'w', encoding='utf-8') as f:
    f.write(container_xml)

# Create mimetype
with open(os.path.join(build_dir, "mimetype"), 'w', encoding='utf-8') as f:
    f.write("application/epub+zip")

# Create EPUB
epub_path = "GoodBooks.epub"
if os.path.exists(epub_path):
    os.remove(epub_path)

print("\nPackaging EPUB...")
with zipfile.ZipFile(epub_path, 'w', zipfile.ZIP_DEFLATED) as z:
    # Add mimetype first (uncompressed, as per EPUB spec)
    z.write(os.path.join(build_dir, 'mimetype'), 'mimetype', compress_type=zipfile.ZIP_STORED)
    
    # Add all other files
    for root, dirs, files in os.walk(build_dir):
        for file in files:
            if file == 'mimetype':
                continue  # Already added
            file_path = os.path.join(root, file)
            arcname = os.path.relpath(file_path, build_dir)
            z.write(file_path, arcname)

print(f"Successfully rebuilt {epub_path}")
print(f"Total pages: {len(MD_FILES) + 1} (cover + {len(MD_FILES)} documents)")

# Clean up
shutil.rmtree(build_dir)
print("Cleaned up temporary files")

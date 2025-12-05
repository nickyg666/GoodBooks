# GoodBooks EPUB Build Information

## Build Date
December 5, 2025

## EPUB Specifications

### Format & Compliance
- **EPUB Version**: EPUB 3.0
- **Standard**: EPUB3 with EPUB2 backwards compatibility (NCX)
- **File Size**: ~2.8 MB
- **Character Encoding**: UTF-8
- **Compression**: ZIP with deflate (except mimetype which is stored uncompressed per EPUB spec)

### Content Structure

The EPUB contains the following content pages in order:

1. **Cover Page** - Display of cover.png with full-screen presentation
2. **README** - Project overview and core features
3. **Quick Start** - Installation and common commands
4. **Installation Guide** - Detailed installer documentation
5. **User Guide** - Comprehensive usage guide (auto-generated)
6. **License** - MIT License

### Navigation Features

**Every page includes:**
- Navigation header with logo image
- Link to http://192.168.0.9:5000
- Professional CSS styling
- Table of contents navigation
- Consistent typography and layout

**Header Components:**
- Application logo (cover.png resized to 40px height)
- Clickable link to web interface
- Styled with background color and border
- Responsive design

### Images Included

1. **cover.png** (1.4 MB)
   - Main cover image for display
   - Used as both cover and navigation logo
   - Optimized for screen display

2. **logo.png** (1.4 MB)
   - Reference image in navigation headers
   - Resized via CSS to 40px height
   - Maintains aspect ratio

### File Manifest

```
mimetype                    (20 bytes) - EPUB media type
META-INF/
  └── container.xml         (256 bytes) - Package metadata
OEBPS/
  ├── content.opf           (1.8 KB) - Package manifest & spine
  ├── toc.ncx              (1.5 KB) - Table of contents (EPUB2 compat)
  ├── nav.xhtml            (745 bytes) - Navigation document (EPUB3)
  ├── content/
  │   ├── cover.xhtml      (718 bytes) - Cover page
  │   ├── readme.xhtml     (22.5 KB) - README content
  │   ├── quickstart.xhtml (5.4 KB) - Quick start guide
  │   ├── installer.xhtml  (11.5 KB) - Installation guide
  │   ├── user_guide.xhtml (10.1 KB) - User guide
  │   └── license.xhtml    (4.5 KB) - License
  └── images/
      ├── cover.png        (1.4 MB)
      └── logo.png         (1.4 MB)

Total: 13 files, ~2.8 MB
```

### Styling Features

- **Fonts**: Serif body font with monospace for code
- **Colors**: Professional blue (#0066cc) for links and headers
- **Layout**: 2em margins with max-width of 800px for readability
- **Typography**:
  - H1: 3px blue underline, 1.5em top margin
  - H2: 1px gray underline
  - H3-H6: Progressive color grading
  - Code blocks: Light gray background with blue left border
  - Blockquotes: Italic with left border
  - Links: Blue with underline, purple when visited

### Markdown Conversion

The build script converts markdown files to XHTML with support for:
- Headings (# ## ###)
- Bold (**text** or __text__)
- Italic (*text* or _text_)
- Code blocks (```...```)
- Inline code (backticks)
- Links ([text](url))
- Lists (- and 1., 2., 3...)
- Blockquotes (> text)
- Paragraphs (double newlines)

### EPUB3 Compliance

**Advantages:**
- Modern EPUB3 format with `nav.xhtml`
- Proper semantic markup with `epub:type="toc"`
- UTF-8 encoded throughout
- Valid XHTML structure
- Proper mimetype as first uncompressed file

**Backwards Compatibility:**
- NCX (toc.ncx) for older EPUB readers
- CSS2-compatible styling
- No advanced EPUB3-only features

## Build Script Details

**Location**: `build_epub_v2.py`

**Process**:
1. Creates clean build directory structure
2. Copies and validates images (cover.png)
3. Converts markdown files to XHTML with navigation headers
4. Generates auto-guide from documentation
5. Creates package manifest (content.opf)
6. Builds table of contents (toc.ncx and nav.xhtml)
7. Packages everything into ZIP format
8. Verifies EPUB structure
9. Cleans temporary files

**Features**:
- Error handling for missing source files
- UTF-8 encoding support
- Robust temporary directory cleanup
- Detailed build output
- No external dependencies beyond Python stdlib

## Testing Recommendations

1. **EPUB Readers to Test**:
   - Apple Books (macOS/iOS)
   - Kindle Cloud Reader
   - Calibre
   - Google Play Books
   - Adobe Digital Editions

2. **Validation**:
   - Check cover displays on first open
   - Verify navigation links work
   - Confirm table of contents navigates correctly
   - Test that all pages render with proper styling

3. **Quality Checks**:
   - Verify images display at proper resolution
   - Check that links go to correct external URL
   - Confirm header appears on all pages
   - Test font rendering and readability

## Rebuilding the EPUB

To rebuild the EPUB after making changes:

```bash
python build_epub_v2.py
```

The script will:
- Remove old GoodBooks.epub
- Recreate from current source files
- Place new EPUB in project root
- Display build summary

**Source files** (in read order):
- cover.png (image asset)
- README.md
- QUICKSTART.md
- INSTALLER.md
- User Guide (auto-generated)
- LICENSE

## Notes

- Cover image is 1.4 MB; EPUB readers should handle this efficiently
- Navigation URL (192.168.0.9:5000) is hardcoded in all pages
- User Guide is generated from key documentation sections
- Script uses Python 3.8+ built-in libraries only (no external dependencies)
- EPUB is fully self-contained; no external resources loaded

## Future Enhancements

Potential improvements for future versions:
1. Add proper EPUB3 landmarks (preface, introduction, etc.)
2. Include chapter-level bookmarks
3. Add CSS media queries for different device sizes
4. Implement EPUB3 metadata enhancements
5. Create separate fixed-layout cover image
6. Add embedded fonts for custom typography

# Multi-Format Cover Image Extraction

## Supported Formats

Cover image extraction is now available for multiple ebook formats:

### 1. EPUB ✅
- **Status**: Full support
- **Method**: Parses OPF manifest to find cover-id, extracts from package document
- **Supported Types**: JPEG, PNG, GIF
- **Success Rate**: High - EPUB files typically have explicit cover metadata

### 2. MOBI/AZW/AZW3 ✅
- **Status**: Supported
- **Method**: Binary scanning for image signatures (JPEG/PNG markers)
- **Supported Types**: JPEG, PNG
- **Success Rate**: Good - Most commercial ebooks have embedded covers
- **Note**: Extracts the first image found in the file

### 3. PDF ✅
- **Status**: Supported (optional)
- **Method**: Attempts to extract images from first page using pypdf
- **Supported Types**: Depends on PDF content
- **Success Rate**: Varies - PDFs may not have embedded cover images
- **Note**: May not work for all PDFs; image extraction is not guaranteed

## Implementation Details

### Cover Priority (in `get_cover_for_email`)

For any ebook format:
1. **Disk Cache** - Check `data/covers/{library_id}.jpg|png|webp|gif`
2. **File Extraction** - Extract from embedded file resources
3. **URL Download** - Download from Goodreads (if available)
4. **None** - Return no cover

### Binary Scanning (MOBI)

For MOBI files without specialized libraries:
- Searches for JPEG marker: `0xFF 0xD8 0xFF` and `0xFF 0xD9`
- Searches for PNG marker: `0x89 0x50 0x4E 0x47` and `IEND` chunk
- Returns first valid image found
- Reliable but may not always find the "best" image

### PDF Image Extraction

Uses pypdf library (if available) to:
- Extract images from first page of document
- Fallback to manual XObject resource parsing
- Returns raw image data (may need format detection)

## Testing Results

✅ EPUB: Successfully extracts covers (tested with Tess Gerritsen books)
✅ MOBI: Successfully extracts covers (tested with Lisa Gardner, Julia Spencer-Fleming)
✅ PDF: Supported, but books may not have embedded images

## Files Modified

- `ebook_metadata_extractor.py`
  - Enhanced `_extract_mobi()` to call new `_extract_mobi_cover()`
  - Enhanced `_extract_pdf()` to call new `_extract_pdf_cover()`
  - New `_extract_mobi_cover()` - Binary image extraction for MOBI
  - New `_extract_pdf_cover()` - Image extraction from PDF pages

## Fallback Behavior

If cover extraction fails:
1. Logs debug message with reason
2. Returns None for cover_image
3. get_cover_for_email() tries next source (URL download)
4. Emails are still sent without cover (graceful degradation)

## Performance Notes

- MOBI/AZW: Requires reading entire file into memory (typically < 1MB)
- PDF: Only reads and processes first page
- EPUB: Already using zipfile, minimal overhead
- Cache lookup is O(1) filesystem check

## Future Improvements

- Implement Calibre integration for better MOBI support (if available)
- Add AZW format-specific handling
- Cache extracted covers alongside Goodreads downloads
- Support for other formats (CBZ, DJVU, etc.)

"""
Extract metadata and cover images from ebook files without scraping.

Supports:
  - EPUB: Full metadata extraction + high-quality cover extraction
  - PDF: Basic metadata (title, author, creation date)
  - MOBI/AZW/AZW3: Basic metadata via KindleUnpack
  
No external scraping or network calls required - pure file parsing.
"""

import logging
import zipfile
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from io import BytesIO
import re
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# XML namespaces used in EPUB files
EPUB_NAMESPACES = {
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'calibre': 'http://calibre.kovidgoyal.net/2009/metadata',
    'xhtml': 'http://www.w3.org/1999/xhtml',
}


class EbookMetadataExtractor:
    """Extract metadata from various ebook formats."""
    
    @staticmethod
    def extract(ebook_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from an ebook file.
        
        Returns dict with keys:
            - title: str or None
            - author: str or None
            - language: str or None (e.g., 'en')
            - publish_date: str or None (YYYY-MM-DD format)
            - publisher: str or None
            - description: str or None
            - genres: List[str] (empty if not found)
            - cover_image: bytes or None (image data)
            - cover_format: str or None ('png', 'jpg', 'jpeg')
        """
        suffix = ebook_path.suffix.lower()
        
        if suffix == '.epub':
            return EbookMetadataExtractor._extract_epub(ebook_path)
        elif suffix == '.pdf':
            return EbookMetadataExtractor._extract_pdf(ebook_path)
        elif suffix in {'.mobi', '.azw', '.azw3', '.prc'}:
            return EbookMetadataExtractor._extract_mobi(ebook_path)
        else:
            logger.warning("Unsupported ebook format: %s", suffix)
            return EbookMetadataExtractor._empty_metadata()
    
    @staticmethod
    def _extract_epub(epub_path: Path) -> Dict[str, Any]:
        """Extract metadata from EPUB file."""
        result = EbookMetadataExtractor._empty_metadata()
        
        try:
            with zipfile.ZipFile(epub_path, 'r') as zf:
                # Find the package document (usually OEBPS/content.opf)
                container_xml = zf.read('META-INF/container.xml')
                container = ET.fromstring(container_xml)
                
                # Find the rootfile path
                rootfile_elem = container.find(
                    './/{urn:oasis:names:tc:opendocument:xmlns:container}rootfile'
                )
                if rootfile_elem is None:
                    logger.warning("No rootfile found in EPUB container")
                    return result
                
                opf_path = rootfile_elem.get('full-path')
                if not opf_path:
                    return result
                
                # Read and parse the OPF (package) file
                opf_content = zf.read(opf_path)
                opf_root = ET.fromstring(opf_content)
                
                # Extract metadata
                result['title'] = EbookMetadataExtractor._extract_opf_text(
                    opf_root, './/dc:title', 'dc'
                )
                result['author'] = EbookMetadataExtractor._extract_opf_text(
                    opf_root, './/dc:creator', 'dc'
                )
                result['language'] = EbookMetadataExtractor._extract_opf_text(
                    opf_root, './/dc:language', 'dc'
                )
                result['publish_date'] = EbookMetadataExtractor._extract_opf_text(
                    opf_root, './/dc:issued', 'dc'
                ) or EbookMetadataExtractor._extract_opf_text(
                    opf_root, './/dc:date', 'dc'
                )
                result['publisher'] = EbookMetadataExtractor._extract_opf_text(
                    opf_root, './/dc:publisher', 'dc'
                )
                result['description'] = EbookMetadataExtractor._extract_opf_text(
                    opf_root, './/dc:description', 'dc'
                )
                
                # Extract cover image
                cover_id = EbookMetadataExtractor._find_cover_id_in_opf(opf_root)
                if cover_id:
                    cover_data, cover_format = EbookMetadataExtractor._extract_cover_from_epub(
                        zf, opf_root, opf_path, cover_id
                    )
                    result['cover_image'] = cover_data
                    result['cover_format'] = cover_format
                
        except Exception as e:
            logger.warning("Error extracting EPUB metadata from %s: %s", epub_path, e)
        
        return result
    
    @staticmethod
    def _extract_opf_text(root: ET.Element, xpath: str, namespace_key: str) -> Optional[str]:
        """Extract text from OPF XML using namespace."""
        try:
            # Replace namespace prefix with actual namespace
            ns_map = {
                'dc': 'http://purl.org/dc/elements/1.1/',
                'opf': 'http://www.idpf.org/2007/opf',
                'calibre': 'http://calibre.kovidgoyal.net/2009/metadata',
            }
            
            # Convert xpath like './/dc:title' to use full namespace
            updated_xpath = xpath
            for key, ns in ns_map.items():
                updated_xpath = updated_xpath.replace(f'{key}:', f'{{{ns}}}')
            
            elem = root.find(updated_xpath)
            if elem is not None and elem.text:
                return elem.text.strip()
        except Exception:
            pass
        return None
    
    @staticmethod
    def _find_cover_id_in_opf(opf_root: ET.Element) -> Optional[str]:
        """Find the cover image ID from OPF metadata."""
        try:
            # Try to find meta tag with name="cover"
            for meta in opf_root.findall('.//{{http://www.idpf.org/2007/opf}}meta'):
                if meta.get('name') == 'cover':
                    return meta.get('content')
        except Exception:
            pass
        return None
    
    @staticmethod
    def _extract_cover_from_epub(
        zf: zipfile.ZipFile,
        opf_root: ET.Element,
        opf_path: str,
        cover_id: str
    ) -> Tuple[Optional[bytes], Optional[str]]:
        """Extract cover image from EPUB and return image data + format."""
        try:
            # Find the manifest item with the cover_id
            for item in opf_root.findall('.//{{http://www.idpf.org/2007/opf}}item'):
                if item.get('id') == cover_id:
                    href = item.get('href')
                    media_type = item.get('media-type', '')
                    
                    if not href:
                        continue
                    
                    # Resolve relative path
                    opf_dir = str(Path(opf_path).parent)
                    cover_path = str(Path(opf_dir) / href).replace('\\', '/')
                    
                    # Determine format
                    cover_format = None
                    if 'png' in media_type.lower():
                        cover_format = 'png'
                    elif 'jpeg' in media_type.lower() or 'jpg' in media_type.lower():
                        cover_format = 'jpg'
                    elif href.lower().endswith('.png'):
                        cover_format = 'png'
                    elif href.lower().endswith(('.jpg', '.jpeg')):
                        cover_format = 'jpg'
                    elif href.lower().endswith('.gif'):
                        cover_format = 'gif'
                    
                    # Try to read the image
                    try:
                        cover_data = zf.read(cover_path)
                        return cover_data, cover_format
                    except KeyError:
                        logger.debug("Cover file not found at %s", cover_path)
        except Exception as e:
            logger.debug("Error extracting cover from EPUB: %s", e)
        
        return None, None
    
    @staticmethod
    def _extract_pdf(pdf_path: Path) -> Dict[str, Any]:
        """Extract metadata from PDF file."""
        result = EbookMetadataExtractor._empty_metadata()
        
        try:
            # Try using pypdf if available, otherwise return basic info
            try:
                from pypdf import PdfReader
                
                with open(pdf_path, 'rb') as f:
                    reader = PdfReader(f)
                    metadata = reader.metadata
                    
                    if metadata:
                        result['title'] = metadata.get('/Title') or metadata.get('Title')
                        result['author'] = metadata.get('/Author') or metadata.get('Author')
                        result['publish_date'] = metadata.get('/CreationDate') or metadata.get('CreationDate')
                        
                        # Try to clean up the date if it's in PDF format
                        if result['publish_date']:
                            result['publish_date'] = EbookMetadataExtractor._parse_pdf_date(
                                result['publish_date']
                            )
            except ImportError:
                logger.debug("pypdf not available for PDF metadata extraction")
        except Exception as e:
            logger.debug("Error extracting PDF metadata from %s: %s", pdf_path, e)
        
        return result
    
    @staticmethod
    def _parse_pdf_date(date_str: str) -> Optional[str]:
        """Parse PDF date format (D:YYYYMMDDHHmmSS) to YYYY-MM-DD."""
        try:
            # PDF date format: D:20231215143022
            if isinstance(date_str, bytes):
                date_str = date_str.decode('utf-8', errors='ignore')
            
            # Extract just the date part
            match = re.search(r'D?:?(\d{4})(\d{2})(\d{2})', str(date_str))
            if match:
                year, month, day = match.groups()
                return f"{year}-{month}-{day}"
        except Exception:
            pass
        return None
    
    @staticmethod
    def _extract_mobi(mobi_path: Path) -> Dict[str, Any]:
        """
        Extract metadata from MOBI/AZW/AZW3 file.
        
        MOBI files have a complex binary format. We use a simple header parser
        for basic metadata, or fallback to Calibre if available.
        """
        result = EbookMetadataExtractor._empty_metadata()
        
        try:
            # Try using calibre's ebooks library if available
            try:
                from calibre.ebooks.mobi.reader import MOBIReader
                from calibre.ebooks.mobi.writer import MOBIWriter
                
                with open(mobi_path, 'rb') as f:
                    reader = MOBIReader(f)
                    metadata = reader.title
                    # This is a simplified approach - calibre has more sophisticated handling
                    if metadata:
                        result['title'] = metadata
            except ImportError:
                logger.debug("Calibre not available for MOBI metadata extraction")
                # Fallback: try basic header parsing
                result = EbookMetadataExtractor._extract_mobi_basic(mobi_path)
        except Exception as e:
            logger.debug("Error extracting MOBI metadata from %s: %s", mobi_path, e)
            # Try basic extraction as fallback
            result = EbookMetadataExtractor._extract_mobi_basic(mobi_path)
        
        return result
    
    @staticmethod
    def _extract_mobi_basic(mobi_path: Path) -> Dict[str, Any]:
        """Basic MOBI header parsing for title extraction."""
        result = EbookMetadataExtractor._empty_metadata()
        
        try:
            with open(mobi_path, 'rb') as f:
                # MOBI header starts at offset 60 (PalmDOC header is 78 bytes)
                f.seek(78)
                header_data = f.read(232)  # MOBI header is up to 232 bytes
                
                if header_data[:4] == b'MOBI':
                    # Title length is at offset 0x50-0x54 in MOBI header
                    try:
                        title_offset = int.from_bytes(header_data[0x50:0x54], 'big')
                        title_length = int.from_bytes(header_data[0x54:0x58], 'big')
                        
                        if title_offset < 10000 and title_length < 500:  # Sanity checks
                            f.seek(title_offset)
                            title_data = f.read(title_length)
                            result['title'] = title_data.decode('utf-8', errors='ignore').strip()
                    except (ValueError, IndexError):
                        pass
        except Exception as e:
            logger.debug("Error in basic MOBI parsing: %s", e)
        
        return result
    
    @staticmethod
    def _empty_metadata() -> Dict[str, Any]:
        """Return empty metadata dict with all fields."""
        return {
            'title': None,
            'author': None,
            'language': None,
            'publish_date': None,
            'publisher': None,
            'description': None,
            'genres': [],
            'cover_image': None,
            'cover_format': None,
        }


def extract_book_metadata(ebook_path: Path) -> Dict[str, Any]:
    """
    Convenience function to extract metadata from an ebook.
    
    Args:
        ebook_path: Path to the ebook file
    
    Returns:
        Dict with extracted metadata
    """
    if not ebook_path.exists():
        logger.warning("Ebook file does not exist: %s", ebook_path)
        return EbookMetadataExtractor._empty_metadata()
    
    return EbookMetadataExtractor.extract(ebook_path)


def save_cover_image(ebook_path: Path, output_dir: Path) -> Optional[Path]:
    """
    Extract and save cover image from ebook.
    
    Args:
        ebook_path: Path to the ebook file
        output_dir: Directory to save the cover image
    
    Returns:
        Path to the saved image, or None if no cover found
    """
    metadata = extract_book_metadata(ebook_path)
    
    if not metadata['cover_image'] or not metadata['cover_format']:
        logger.debug("No cover found in %s", ebook_path.name)
        return None
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create filename from book title or use default
    title = metadata.get('title', 'cover').replace('/', '_').replace('\\', '_')[:50]
    cover_filename = f"{title}_cover.{metadata['cover_format']}"
    cover_path = output_dir / cover_filename
    
    try:
        cover_path.write_bytes(metadata['cover_image'])
        logger.info("Saved cover image to %s", cover_path)
        return cover_path
    except Exception as e:
        logger.error("Error saving cover image: %s", e)
        return None


if __name__ == '__main__':
    # Test the extractor
    import sys
    
    logging.basicConfig(level=logging.DEBUG)
    
    if len(sys.argv) > 1:
        test_path = Path(sys.argv[1])
        if test_path.exists():
            metadata = extract_book_metadata(test_path)
            print(f"\nMetadata from {test_path.name}:")
            print(json.dumps({
                k: v if k != 'cover_image' else (
                    f"<{len(v)} bytes>" if v else None
                )
                for k, v in metadata.items()
            }, indent=2))
        else:
            print(f"File not found: {test_path}")
    else:
        print("Usage: python ebook_metadata_extractor.py <ebook_path>")

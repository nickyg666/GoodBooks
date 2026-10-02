"""
Amazon high-resolution cover fetcher for GoodBooks.

Adapted from calibre-amazon-hires-covers by Leonardo Brondani Schenkel
(https://github.com/lbschenkel/calibre-amazon-hires-covers)

Fetches high-resolution cover images from Amazon for Kindle editions.
"""

import re
import logging
import requests
from contextlib import closing
from lxml.html import fromstring
from six.moves.urllib.parse import urljoin, quote_plus

logger = logging.getLogger(__name__)

# User agent for requests
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Goodreads base URL
GOODREADS_URL = 'https://www.goodreads.com'

# Amazon high-resolution cover URL patterns
# These patterns are known to work for Kindle book covers
AMAZON_COVER_SOURCES = frozenset([
    'https://images-na.ssl-images-amazon.com/images/P/{0}.01.LZZZZZZZ.jpg',  # Large size
    'https://images-na.ssl-images-amazon.com/images/P/{0}.01.MAIN._SCLZZZZZZZ_.jpg',  # Medium-large
    'https://images-na.ssl-images-amazon.com/images/P/{0}.01.MAIN._SCRM_.jpg',  # Original pattern from calibre plugin
    'https://images-na.ssl-images-amazon.com/images/P/{0}.01.MAIN._SCL_.jpg',  # Smaller variant
])


def is_kindle_asin(value):
    """Check if a string is a valid Kindle ASIN (10 chars, starts with B)."""
    return value and len(value) == 10 and value.startswith('B')


def get_cover_urls(title=None, authors=None, identifiers=None, timeout=10):
    """
    Get Amazon cover URLs for a book using various identification methods.
    
    Args:
        title (str): Book title
        authors (list): List of author names
        identifiers (dict): Dictionary of identifiers (asin, isbn, goodreads, etc.)
        timeout (int): Request timeout in seconds
        
    Returns:
        list: List of cover image URLs
    """
    if identifiers is None:
        identifiers = {}
    if authors is None:
        authors = []
        
    urls = set()
    asins = set()
    
    # Check for ASINs identifiers first
    for id, value in identifiers.items():
        # Normalize identifier key
        id_lower = id.lower().replace('-', '_').replace(' ', '_')
        is_asin = id_lower in ['asin', 'mobi_asin'] or id_lower.startswith('amazon')
        is_kindle = is_kindle_asin(value)
        if is_asin and is_kindle:
            logger.info('ASIN present in metadata: %s', value)
            asins.add(value)
    
    # Otherwise use Goodreads id to find Kindle editions
    if not asins:
        goodreads_id = identifiers.get('goodreads') or identifiers.get('goodreads_id')
        if goodreads_id:
            logger.info('Goodreads id present in metadata: %s', goodreads_id)
            asins = search_asins_goodreads(goodreads_id, timeout)
        else:
            logger.info('Goodreads id not present in metadata')
    
    # Otherwise search Goodreads to find Kindle editions
    # Use ISBN if available, otherwise use title+author
    if not asins:
        isbn = identifiers.get('isbn') or identifiers.get('isbn13')
        if isbn:
            logger.info('ISBN present in metadata: %s', isbn)
            query = isbn
        else:
            logger.info('ISBN not present in metadata')
            query = ''
            if title:
                query = title
                if authors and authors[0]:
                    query = query + ' ' + authors[0]
        
        if query:
            edition_url = search_edition_goodreads(query, timeout)
            if edition_url:
                asins = search_asins_goodreads(edition_url, timeout)
    
    # Now convert all ASINs into the download URLs
    if asins:
        for asin in asins:
            for source in AMAZON_COVER_SOURCES:
                url = source.format(asin)
                urls.add(url)
                logger.debug('Generated Amazon cover URL: %s', url)
    
    return list(urls)


import threading as _gb_threading
import time as _gb_time

# Goodreads refuses under load by returning a 202/200 with an EMPTY body.
# That is a throttle signal, not a parse failure, and treating it as one is
# how a block gets earned: the thread pool keeps re-asking a site that is
# already saying no.
#
# Measured 2026-10-02: the log filled with
#   "Error searching Goodreads for edition: Document is empty"
# from 6 concurrent workers, continuously, for hours.
_GOODREADS_LOCK = _gb_threading.Lock()
_GOODREADS_REFUSALS = 0
_GOODREADS_OPEN_UNTIL = 0.0
# After this many refusals, stop searching entirely for a cool-down.
_GOODREADS_REFUSAL_LIMIT = 3
_GOODREADS_BASE_COOLDOWN = 300.0   # 5 min, doubling to a 30 min ceiling
_GOODREADS_MAX_COOLDOWN = 1800.0


def goodreads_is_throttled() -> bool:
    """True while Goodreads is refusing us and we should stay quiet."""
    global _GOODREADS_OPEN_UNTIL
    with _GOODREADS_LOCK:
        return _GOODREADS_OPEN_UNTIL > _gb_time.time()


def _note_goodreads_refusal() -> None:
    """Count a refusal and open the circuit breaker when it is sustained."""
    global _GOODREADS_REFUSALS, _GOODREADS_OPEN_UNTIL
    with _GOODREADS_LOCK:
        _GOODREADS_REFUSALS += 1
        if _GOODREADS_REFUSALS >= _GOODREADS_REFUSAL_LIMIT:
            over = _GOODREADS_REFUSALS - _GOODREADS_REFUSAL_LIMIT
            cd = min(_GOODREADS_BASE_COOLDOWN * (2 ** min(over, 3)),
                     _GOODREADS_MAX_COOLDOWN)
            _GOODREADS_OPEN_UNTIL = _gb_time.time() + cd
            logger.warning(
                "Goodreads refusing (empty document) %d times -- pausing "
                "searches for %.0f min", _GOODREADS_REFUSALS, cd / 60.0)


def _note_goodreads_ok() -> None:
    """A real response clears the refusal counter."""
    global _GOODREADS_REFUSALS
    with _GOODREADS_LOCK:
        if _GOODREADS_REFUSALS:
            logger.info("Goodreads responding again after %d refusals",
                        _GOODREADS_REFUSALS)
        _GOODREADS_REFUSALS = 0


def _looks_like_refusal(exc: Exception) -> bool:
    """Is this exception Goodreads saying 'go away' rather than a bug?

    An empty document reaches us as a parser IndexError or lxml exception;
    a 202 reaches us as an HTTP error. Both are refusals, not code faults,
    and both must drive the back-off instead of being logged and ignored.
    """
    # By TYPE first: str(IndexError(...)) is the message, never the type
    # name, so matching "indexerror" against the text could never fire.
    # An empty Goodreads result set reaches us as books[0] on an empty list.
    if isinstance(exc, (IndexError, StopIteration)):
        return True
    # HTTP cases: the status code only appears in the message text.
    text = str(exc).lower()
    return ("document is empty" in text
            or "202" in text
            or "no elements found" in text
            or "empty document" in text)


def search_edition_goodreads(query, timeout=10):
    """
    Search Goodreads for a book and return the edition URL.
    
    Args:
        query (str): Search query (title, author, or ISBN)
        timeout (int): Request timeout in seconds
        
    Returns:
        str: Edition URL or None if not found
    """
    try:
        br = requests.Session()
        br.headers.update({'User-Agent': USER_AGENT})
        
        if goodreads_is_throttled():
            logger.debug('Goodreads circuit open -- skipping search for %s',
                         query)
            return None

        search_url = urljoin(GOODREADS_URL, '/search?q=' + quote_plus(query))
        logger.info('Searching Goodreads for book: %s', search_url)

        resp = br.get(search_url, timeout=timeout)
        # Goodreads signals a throttle with an empty body on 200 or 202.
        # raise_for_status() would let the 202 through as success.
        if not resp.content.strip():
            _note_goodreads_refusal()
            logger.debug('Goodreads returned an empty document for %s', query)
            return None
        resp.raise_for_status()
        
        # Check if we were redirected (perfect match)
        if resp.url == search_url:
            # No perfect match, get the first result
            doc = fromstring(resp.content)
            edition_url = None
            books = doc.xpath('//*[@itemtype="http://schema.org/Book"]')
            if books:
                book = books[0]
                url_elements = book.xpath('.//*[@itemprop="url"]/@href')
                if url_elements:
                    edition_url = url_elements[0]
        else:
            # Perfect match, we were redirected to the edition
            edition_url = resp.url
        
        if edition_url:
            _note_goodreads_ok()
            # Make sure it's a full URL
            if not edition_url.startswith('http'):
                edition_url = urljoin(GOODREADS_URL, edition_url)
            return edition_url
            
    except Exception as e:
        if _looks_like_refusal(e):
            _note_goodreads_refusal()
            logger.debug('Goodreads refused the search for %s: %s', query, e)
        else:
            # A genuine fault, not a throttle. Swallowing it into a debug
            # line is how this became invisible in the first place.
            logger.warning('Unexpected error searching Goodreads for %s: %s',
                           query, e, exc_info=True)

    return None


def search_asins_goodreads(edition_url_or_id, timeout=10):
    """
    Search Goodreads for ASINs of Kindle editions of a book.
    
    Args:
        edition_url_or_id (str): Goodreads edition URL or numeric ID
        timeout (int): Request timeout in seconds
        
    Returns:
        set: Set of ASINs for Kindle editions
    """
    try:
        # Handle numeric ID
        if edition_url_or_id.isdigit():
            edition_url = '/book/show/' + edition_url_or_id
        else:
            edition_url = edition_url_or_id
        
        edition_url = urljoin(GOODREADS_URL, edition_url)
        
        br = requests.Session()
        br.headers.update({'User-Agent': USER_AGENT})
        
        # Parse the details page and get the link to list all editions
        logger.info('Fetching book details: %s', edition_url)
        resp = br.get(edition_url, timeout=timeout)
        resp.raise_for_status()
        
        doc = fromstring(resp.content)
        editions_url_elements = doc.xpath('//div[@class="otherEditionsLink"]/a/@href')
        if not editions_url_elements:
            return set()
        
        editions_url = editions_url_elements[0]
        editions_url = urljoin(GOODREADS_URL, editions_url)
        
        # List all Kindle editions of the book
        editions_url = urljoin(editions_url, '?expanded=true&filter_by_format=Kindle+Edition&per_page=100')
        logger.info('Fetching Kindle editions: %s', editions_url)
        
        resp = br.get(editions_url, timeout=timeout)
        resp.raise_for_status()
        
        doc = fromstring(resp.content)
        asins = set()
        
        # Look for ASINs in the page
        for value in doc.xpath('//*[@class="dataValue"]//text()'):
            value = value.strip()
            if is_kindle_asin(value):
                asins.add(value)
        
        return asins
        
    except Exception as e:
        logger.debug('Error searching Goodreads for ASINs: %s', e)
    
    return set()


def fetch_amazon_cover(title=None, authors=None, identifiers=None, timeout=15):
    """
    Fetch a high-resolution cover image from Amazon for a book.
    
    This is the main function to call from the application.
    
    Args:
        title (str): Book title
        authors (list): List of author names
        identifiers (dict): Dictionary of identifiers (asin, isbn, goodreads, etc.)
        timeout (int): Request timeout in seconds
        
    Returns:
        tuple: (cover_url, image_data) or (None, None) if not found
    """
    try:
        # Get potential cover URLs
        cover_urls = get_cover_urls(title, authors, identifiers, timeout=5)  # Short timeout for URL discovery
        
        if not cover_urls:
            logger.debug('No Amazon cover URLs generated for %s by %s', 
                        title, ', '.join(authors) if authors else 'Unknown')
            return None, None
        
        # Try each URL until we find one that works
        for cover_url in cover_urls:
            try:
                logger.debug('Trying Amazon cover URL: %s', cover_url)
                
                headers = {
                    'User-Agent': USER_AGENT,
                    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
                
                resp = requests.get(cover_url, headers=headers, timeout=timeout)
                
                # Check if we got a valid image
                if resp.status_code == 200:
                    content_type = resp.headers.get('content-type', '').lower()
                    if content_type.startswith('image/'):
                        # Additional check: make sure it's not an error image
                        if len(resp.content) > 1000:  # Reasonable minimum size for a book cover
                            logger.info('Successfully fetched Amazon cover: %s (%d bytes)', 
                                       cover_url, len(resp.content))
                            return cover_url, resp.content
                        else:
                            logger.debug('Amazon cover too small: %d bytes', len(resp.content))
                    else:
                        logger.debug('Amazon URL did not return an image: %s', content_type)
                else:
                    logger.debug('Amazon cover request failed with status %d', resp.status_code)
                    
            except requests.exceptions.Timeout:
                logger.debug('Timeout fetching Amazon cover: %s', cover_url)
                continue
            except requests.exceptions.RequestException as e:
                logger.debug('Request error fetching Amazon cover %s: %s', cover_url, e)
                continue
            except Exception as e:
                logger.debug('Unexpected error fetching Amazon cover %s: %s', cover_url, e)
                continue
        
        logger.debug('No valid Amazon cover found for %s by %s', 
                    title, ', '.join(authors) if authors else 'Unknown')
        return None, None
        
    except Exception as e:
        logger.warning('Error in fetch_amazon_cover: %s', e)
        return None, None


# For backward compatibility and testing
if __name__ == "__main__":
    import sys
    
    # Simple command-line interface for testing
    title = None
    authors = []
    identifiers = {
        'asin': None,
        'goodreads': None,
        'isbn': None,
    }
    
    # Parse command line arguments
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == '--title' and i + 1 < len(sys.argv):
            title = sys.argv[i + 1]
            i += 2
        elif arg == '--author' and i + 1 < len(sys.argv):
            authors.append(sys.argv[i + 1])
            i += 2
        elif arg == '--asin' and i + 1 < len(sys.argv):
            identifiers['asin'] = sys.argv[i + 1]
            i += 2
        elif arg == '--goodreads' and i + 1 < len(sys.argv):
            identifiers['goodreads'] = sys.argv[i + 1]
            i += 2
        elif arg == '--isbn' and i + 1 < len(sys.argv):
            identifiers['isbn'] = sys.argv[i + 1]
            i += 2
        else:
            i += 1
    
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # Fetch cover
    cover_url, image_data = fetch_amazon_cover(title, authors, identifiers)
    
    if cover_url:
        print(f"Cover URL: {cover_url}")
        print(f"Image size: {len(image_data) if image_data else 0} bytes")
        
        # Save to file for testing
        if image_data:
            with open('/tmp/test_cover.jpg', 'wb') as f:
                f.write(image_data)
            print("Cover saved to /tmp/test_cover.jpg")
    else:
        print("No cover found")
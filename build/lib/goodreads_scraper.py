"""
Goodreads scraping module for genre lists and list details.
Includes caching to minimize requests to Goodreads.
"""
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_DIR.mkdir(exist_ok=True)

GENRE_LISTS_CACHE_HOURS = 24
LIST_DETAIL_CACHE_HOURS = 168  # 7 days
REQUEST_DELAY = 2  # seconds between requests to avoid rate limiting

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _get_cache_path(key: str) -> Path:
    """Generate cache file path for a key."""
    return CACHE_DIR / f"{key}.json"


def _load_cache(key: str, max_age_hours: int) -> Optional[Dict]:
    """Load cache if it exists and is fresh."""
    cache_path = _get_cache_path(key)
    if not cache_path.exists():
        return None
    
    try:
        with open(cache_path) as f:
            data = json.load(f)
        
        timestamp = datetime.fromisoformat(data.get("timestamp", ""))
        age = (datetime.now() - timestamp).total_seconds() / 3600
        
        if age > max_age_hours:
            logger.debug(f"Cache expired for {key} ({age:.1f}h old)")
            return None
        
        logger.debug(f"Cache hit for {key}")
        return data.get("data")
    except Exception as e:
        logger.warning(f"Failed to load cache for {key}: {e}")
        return None


def _save_cache(key: str, data: Dict):
    """Save data to cache."""
    cache_path = _get_cache_path(key)
    try:
        with open(cache_path, "w") as f:
             json.dump({
                "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                "data": data
            }, f)
        logger.debug(f"Cached {key}")
    except Exception as e:
        logger.warning(f"Failed to cache {key}: {e}")


def scrape_genre_lists(genre: str, page: int = 1, max_pages: int = 3) -> List[Dict]:
    """
    Scrape Goodreads lists for a genre with pagination.
    Returns list of dicts with: id, name, description, covers (URLs)
    
    Args:
        genre: The genre to search for
        page: Starting page (1-based)
        max_pages: Maximum number of pages to fetch (default 3 = ~90 lists)
    """
    cache_key = f"genre_lists_{genre.lower().replace(' ', '_')}_p{page}"
    
    # Try cache first
    cached = _load_cache(cache_key, GENRE_LISTS_CACHE_HOURS)
    if cached is not None:
        return cached
    
    lists = []
    
    for current_page in range(page, page + max_pages):
        try:
            # URL format: /list/tag/fiction or /list/tag/fiction?page=2
            url = f"https://www.goodreads.com/list/tag/{quote(genre)}"
            if current_page > 1:
                url += f"?page={current_page}"
            
            logger.debug(f"Fetching {genre} lists page {current_page}: {url}")
            time.sleep(REQUEST_DELAY)
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to fetch lists for {genre} page {current_page}: {e}")
            break  # Stop paginating on error
        
        try:
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Find all list cells in the listRowsFull container
            cells = soup.find_all("div", class_="cell")
            
            if not cells:
                logger.debug(f"No lists found on page {current_page}, stopping pagination")
                break
            
            for cell in cells:
                try:
                    # Extract list link and ID from a.listTitle
                    list_title = cell.find("a", class_="listTitle")
                    if not list_title:
                        continue
                    
                    href = list_title.get("href", "")
                    if "/list/show/" not in href:
                        continue
                    
                    # Extract list ID from href like "/list/show/1.Best_Books_Ever"
                    list_id = href.split("/list/show/")[1].split(".")[0]
                    name = list_title.get_text(strip=True)
                    
                    # Get description from listFullDetails
                    details_div = cell.find("div", class_="listFullDetails")
                    description = details_div.get_text(strip=True) if details_div else ""
                    
                    # Get covers from img elements in listImgs
                    covers = []
                    imgs = cell.find_all("img", limit=5)
                    for img in imgs:
                        src = img.get("src", "")
                        if src:
                            covers.append(src)
                    
                    if list_id and name:
                        lists.append({
                            "id": list_id,
                            "name": name[:100],
                            "description": description[:150],
                            "covers": covers[:5]
                        })
                except Exception as e:
                    logger.debug(f"Failed to parse list cell: {e}")
                    continue
            
            logger.debug(f"Fetched {len(cells)} lists from page {current_page}, total: {len(lists)}")
        
        except Exception as e:
            logger.error(f"Failed to parse lists HTML for {genre} page {current_page}: {e}")
            break
    
    # If no items found, generate some example lists
    if not lists:
        logger.debug(f"No lists found for {genre}, returning examples")
        lists = [
            {
                "id": "1",
                "name": f"Best {genre} Books of All Time",
                "description": f"Classic and modern favorites in {genre}",
                "covers": []
            },
            {
                "id": "2",
                "name": f"Popular {genre} Series",
                "description": f"Popular {genre} series and sequels",
                "covers": []
            }
        ]
    
    # Cache the result
    _save_cache(cache_key, lists)
    return lists


def scrape_list_detail(list_id: str, list_name: str = "") -> Dict:
    """
    Scrape all books from a Goodreads list.
    Returns: {"id": list_id, "name": list_name, "books": [...], "total": N}
    """
    cache_key = f"list_detail_{list_id}"
    
    # Try cache first
    cached = _load_cache(cache_key, LIST_DETAIL_CACHE_HOURS)
    if cached is not None:
        return cached
    
    url = f"https://www.goodreads.com/list/show/{list_id}"
    
    books = []
    page = 1
    max_pages = 5  # Limit to 5 pages (250 books max)
    
    try:
        while page <= max_pages:
            page_url = f"{url}?page={page}"
            time.sleep(REQUEST_DELAY)
            
            response = requests.get(
                page_url,
                headers={"User-Agent": USER_AGENT},
                timeout=15
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Find book rows (usually in table rows with class "book")
            book_rows = soup.find_all("tr", class_="book")
            
            if not book_rows:
                break  # No more books on this page
            
            for row in book_rows:
                try:
                    # Extract book info
                    title_elem = row.find("a", class_="title")
                    author_elem = row.find("a", class_="author")
                    img_elem = row.find("img", class_="cover")
                    
                    if not title_elem:
                        continue
                    
                    book = {
                        "title": title_elem.get_text(strip=True),
                        "author": author_elem.get_text(strip=True) if author_elem else "Unknown",
                        "cover": img_elem.get("src", "") if img_elem else "",
                        "link": title_elem.get("href", "") or ""
                    }
                    
                    if book["cover"] and "goodreads" in book["cover"]:
                        books.append(book)
                except Exception as e:
                    logger.debug(f"Failed to parse book row: {e}")
                    continue
            
            # Check if there's a next page
            next_btn = soup.find("a", class_="next_page")
            if not next_btn:
                break
            
            page += 1
        
        result = {
            "id": list_id,
            "name": list_name,
            "books": books,
            "total": len(books)
        }
        
        # Cache the result
        _save_cache(cache_key, result)
        return result
    
    except Exception as e:
         logger.error(f"Failed to scrape list {list_id}: {e}")
         return {"id": list_id, "name": list_name, "books": [], "total": 0}


def scrape_series_books(series_id: str, series_name: str = "") -> Dict:
    """
    Scrape all books in a Goodreads series with pagination support.
    Returns dict with: id, name, books (list of book dicts), total count
    
    Args:
        series_id: The Goodreads series ID (e.g., "237987" from /series/237987-mad-libs)
        series_name: Optional series name for caching/display
    """
    cache_key = f"series_{series_id}_{series_name.lower().replace(' ', '_') if series_name else 'unnamed'}"
    
    # Try cache first
    cached = _load_cache(cache_key, LIST_DETAIL_CACHE_HOURS)
    if cached is not None:
        return cached
    
    try:
        url = f"https://www.goodreads.com/series/{series_id}"
        logger.debug(f"Fetching series {series_id}: {url}")
        
        import re
        book_data = {}  # Store book data by ID for deduplication
        series_authors = ""
        page = 1
        max_pages = 20  # Limit to 20 pages (~400 books max for large series like Mad Libs)
        
        while page <= max_pages:
            page_url = f"{url}?page={page}"
            time.sleep(REQUEST_DELAY)
            
            try:
                response = requests.get(page_url, headers={"User-Agent": USER_AGENT}, timeout=15)
                response.raise_for_status()
            except requests.RequestException as e:
                logger.debug(f"Failed to fetch series page {page}: {e}")
                break
            
            soup = BeautifulSoup(response.content, "html.parser")
            
            # Extract series name from page if not provided (only on first page)
            if page == 1 and not series_name:
                h1 = soup.find("h1")
                if h1:
                    series_name = h1.get_text(strip=True).replace(" Series", "").strip()
            
            # Extract series-level author info on first page only
            if page == 1 and not series_authors:
                all_links = soup.find_all("a", href=True)
                author_links = [link for link in all_links if "/author/show/" in link.get("href", "")]
                if author_links:
                    # Get unique authors from the series page (limit to first 5 to avoid duplication)
                    seen_authors = set()
                    for link in author_links[:5]:
                        author_name = link.get_text(strip=True)
                        if author_name and author_name not in seen_authors:
                            seen_authors.add(author_name)
                    if seen_authors:
                        series_authors = ", ".join(sorted(seen_authors))
            
            # Find all book links on this page
            links = soup.find_all("a", href=True)
            books_found = False
            
            for link in links:
                href = link.get("href", "")
                if "/book/show/" in href:
                    books_found = True
                    # Extract book ID and title
                    match = re.search(r'/book/show/(\d+)[-.]', href)
                    if match:
                        book_id = match.group(1)
                        
                        # Get the link text - prefer non-empty text which contains the actual title
                        link_text = link.get_text(strip=True)
                        
                        # Store or update book data - always prefer non-empty titles over "Book {id}" placeholders
                        if book_id not in book_data:
                            # First time seeing this book
                            book_data[book_id] = {
                                "id": book_id,
                                "title": link_text or f"Book {book_id}",
                                "url": f"https://www.goodreads.com{href}" if href.startswith("/") else href,
                                "author": series_authors,  # Use series-level authors as default
                            }
                        elif link_text and book_data[book_id]["title"].startswith("Book "):
                            # We have a better title now - update the placeholder with actual title
                            book_data[book_id]["title"] = link_text
            
            # If no books found on this page, we're done
            if not books_found:
                break
            
            # Check if there's a next page
            # Try new button-based pagination (gr-paginationLinks__nextButton)
            next_btn = soup.find("button", class_="gr-paginationLinks__nextButton")
            if next_btn and next_btn.get("disabled") is not None:
                # Next button is disabled, no more pages
                break
            elif not next_btn:
                # Try old link-based pagination (for backward compatibility)
                next_btn = soup.find("a", class_="next_page")
                if not next_btn:
                    break
            
            logger.debug(f"Series {series_id} page {page}: found {len(book_data)} books so far")
            page += 1
        
        # Convert to list, maintaining order
        books = list(book_data.values())
        
        result = {
            "id": series_id,
            "name": series_name or f"Series {series_id}",
            "books": books,
            "total": len(books)
        }
        
        logger.info(f"Scraped series {series_id} ({series_name or result['name']}): {len(books)} books across {page - 1} pages")
        
        # Cache the result
        _save_cache(cache_key, result)
        return result
    
    except Exception as e:
        logger.error(f"Failed to scrape series {series_id}: {e}")
        return {"id": series_id, "name": series_name or f"Series {series_id}", "books": [], "total": 0}


def clear_genre_cache(genre: str = ""):
    """Clear cache for a genre or all genre caches."""
    if genre:
        cache_key = f"genre_lists_{genre.lower().replace(' ', '_')}"
        cache_path = _get_cache_path(cache_key)
        if cache_path.exists():
            cache_path.unlink()
            logger.info(f"Cleared cache for genre: {genre}")
    else:
        for cache_file in CACHE_DIR.glob("genre_lists_*.json"):
            cache_file.unlink()
        logger.info("Cleared all genre caches")


def clear_list_cache(list_id: str = ""):
    """Clear cache for a list or all list caches."""
    if list_id:
        cache_key = f"list_detail_{list_id}"
        cache_path = _get_cache_path(cache_key)
        if cache_path.exists():
            cache_path.unlink()
            logger.info(f"Cleared cache for list: {list_id}")
    else:
        for cache_file in CACHE_DIR.glob("list_detail_*.json"):
            cache_file.unlink()
        logger.info("Cleared all list caches")

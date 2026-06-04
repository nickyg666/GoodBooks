"""
Download and cache book covers locally instead of relying on external URLs.
This enables:
- Offline cover display
- Email notifications with embedded covers
- Faster loading (local vs CDN)
"""

import requests
import logging
from pathlib import Path
from typing import Optional
import hashlib

logger = logging.getLogger(__name__)

COVER_CACHE_DIR = Path("data/covers")
COVER_CACHE_DIR.mkdir(exist_ok=True)

def get_cover_path(book_id: str, cover_url: str) -> Path:
    """Generate local cache path for a cover image."""
    # Use book ID and URL hash to generate unique filename
    url_hash = hashlib.md5(cover_url.encode()).hexdigest()[:8]
    return COVER_CACHE_DIR / f"{book_id}_{url_hash}.jpg"

def download_cover(cover_url: str, book_id: str, timeout: int = 10) -> Optional[Path]:
    """
    Download and cache a cover image locally.
    
    Returns:
        Path to cached image, or None if download failed
    """
    if not cover_url:
        return None
    
    # Skip if already a local path
    if str(cover_url).startswith('data/covers/'):
        return Path(cover_url) if Path(cover_url).exists() else None
    
    try:
        cache_path = get_cover_path(book_id, cover_url)
        
        # Already cached
        if cache_path.exists():
            logger.debug(f"Cover already cached: {cache_path}")
            return cache_path
        
        # Download with reasonable timeout
        logger.debug(f"Downloading cover from {cover_url}")
        resp = requests.get(cover_url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        
        # Verify it's an image
        content_type = resp.headers.get('content-type', '').lower()
        if 'image' not in content_type:
            logger.warning(f"Cover URL returned non-image content type: {content_type}")
            return None
        
        # Save to cache
        cache_path.write_bytes(resp.content)
        logger.debug(f"Cached cover to {cache_path}")
        return cache_path
        
    except Exception as e:
        logger.warning(f"Failed to download cover from {cover_url}: {e}")
        return None

def update_history_with_local_covers():
    """Update history.json entries to use local cover paths instead of URLs."""
    import json
    
    hist_path = Path("data/history.json")
    with open(hist_path) as f:
        history = json.load(f)
    
    updated = False
    for item in history:
        cover_url = item.get('cover')
        if cover_url and cover_url.startswith('http'):
            book_id = item.get('book_id') or item.get('title', '').replace(' ', '_')[:20]
            local_path = download_cover(cover_url, book_id)
            if local_path:
                # Store both original URL and local path
                item['cover_url'] = cover_url
                item['cover'] = str(local_path)
                updated = True
    
    if updated:
        with open(hist_path, 'w') as f:
            json.dump(history, f, indent=2)
        logger.info(f"Updated history with {len([i for i in history if i.get('cover', '').startswith('data/')])} local covers")

if __name__ == '__main__':
    update_history_with_local_covers()

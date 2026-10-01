"""
Cover Cache Manager - Intelligent image caching for high-resolution covers
Only caches high-res covers (width >= 400px), resizes to 500px width
"""

import os
import hashlib
import base64
from pathlib import Path
from PIL import Image
from io import BytesIO
import requests
from datetime import datetime, timedelta

class CoverCacheManager:
    def __init__(self, cache_dir='data/cover_cache'):
        self.cache_dir = cache_dir
        self.min_width_for_cache = 300  # Only cache if width >= 300px
        self.target_width = None  # Don't resize - keep original high-res images
        self.quality = 95  # JPEG quality - high quality for best display
        
        # Create cache directory if it doesn't exist
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
    
    def _get_cache_path(self, url):
        """Generate cache file path using MD5 hash of URL"""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return os.path.join(self.cache_dir, f"{url_hash}.jpg")
    
    def _is_high_res(self, image_path):
        """Check if image is high resolution (width >= 400px)"""
        try:
            img = Image.open(image_path)
            width, height = img.size
            return width >= self.min_width_for_cache
        except Exception as e:
            print(f"Error checking image resolution: {e}")
            return False
    
    def _resize_to_target(self, image_path, target_width=None):
        """Resize image to target width (or keep original if target_width is None)"""
        if target_width is None:
            target_width = self.target_width
        
        try:
            img = Image.open(image_path)
            # Convert RGBA to RGB for JPEG
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            
            # If no target width specified, just save without resizing
            if target_width is None:
                img.save(image_path, 'JPEG', quality=self.quality, optimize=True)
                return True
            
            # Calculate new height maintaining aspect ratio
            width, height = img.size
            ratio = target_width / width
            new_height = int(height * ratio)
            
            # Resize using LANCZOS (high quality)
            img = img.resize((target_width, new_height), Image.LANCZOS)
            
            # Save with quality setting
            img.save(image_path, 'JPEG', quality=self.quality, optimize=True)
            return True
        except Exception as e:
            print(f"Error resizing image: {e}")
            return False
    
    def get_cached_cover(self, url, download_func):
        """
        Get cached cover image, or download and cache if high-res
        download_func should be a callable that takes URL and returns image bytes
        Returns: bytes of image, or None if not available
        """
        cache_path = self._get_cache_path(url)
        
        # Return from cache if exists
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'rb') as f:
                    return f.read()
            except Exception as e:
                print(f"Error reading cache: {e}")
                return None
        
        # Download the image
        try:
            image_bytes = download_func(url)
            if not image_bytes:
                return None
            
            # Check if high-res, resize, and cache
            temp_path = cache_path + '.tmp'
            with open(temp_path, 'wb') as f:
                f.write(image_bytes)
            
            if self._is_high_res(temp_path):
                # Resize to target width
                self._resize_to_target(temp_path)
                # Move to final cache location
                os.rename(temp_path, cache_path)
                
                with open(cache_path, 'rb') as f:
                    return f.read()
            else:
                # Low-res, don't cache, just return original
                os.remove(temp_path)
                return image_bytes
        
        except Exception as e:
            print(f"Error caching cover: {e}")
            return None
    
    def get_cover_as_bytes(self, url, download_func=None):
        """Get cover image as bytes"""
        if download_func:
            return self.get_cached_cover(url, download_func)
        
        # Try to get from cache
        cache_path = self._get_cache_path(url)
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'rb') as f:
                    return f.read()
            except:
                return None
        return None
    
    def get_cover_base64(self, url, download_func=None):
        """Get cover image as base64 string (for email embedding)"""
        image_bytes = self.get_cover_as_bytes(url, download_func)
        if image_bytes:
            return base64.b64encode(image_bytes).decode('utf-8')
        return None
    
    def clear_cover(self, url):
        """Remove a specific cover from cache to force re-fetch"""
        cache_path = self._get_cache_path(url)
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                return True
            except Exception as e:
                print(f"Error removing cached cover: {e}")
                return False
        return False
    
    def clear_all_covers(self):
        """Clear entire cover cache including data/covers directory"""
        removed = 0
        
        # Clear old cache_dir if it exists
        try:
            if os.path.exists(self.cache_dir):
                for filename in os.listdir(self.cache_dir):
                    filepath = os.path.join(self.cache_dir, filename)
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        removed += 1
        except Exception as e:
            print(f"Error clearing cover cache: {e}")
        
        # Clear data/covers directory
        covers_dir = 'data/covers'
        try:
            if os.path.exists(covers_dir):
                for filename in os.listdir(covers_dir):
                    filepath = os.path.join(covers_dir, filename)
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        removed += 1
        except Exception as e:
            print(f"Error clearing data/covers: {e}")
        
        return removed
    
    def cleanup_old_cache(self, max_age_days=30):
        """Remove cached images older than max_age_days"""
        try:
            cutoff_time = datetime.now() - timedelta(days=max_age_days)
            removed = 0
            
            for filename in os.listdir(self.cache_dir):
                filepath = os.path.join(self.cache_dir, filename)
                if os.path.isfile(filepath):
                    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if mtime < cutoff_time:
                        os.remove(filepath)
                        removed += 1
            
            return removed
        except Exception as e:
            print(f"Error during cache cleanup: {e}")
            return 0

# Global instance
_cache_manager = None

def get_cache_manager():
    """Get or create global cache manager instance"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CoverCacheManager()
    return _cache_manager

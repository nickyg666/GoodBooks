# Cover Cache Manager Implementation
# Purpose: Manage cover caching with resolution filtering
# Location: cover_cache_manager.py

import os
from pathlib import Path
from PIL import Image
import hashlib
import json
from datetime import datetime

class CoverCacheManager:
    def __init__(self, cache_dir="data/cover_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_file = self.cache_dir / "metadata.json"
        self.load_metadata()
        self.min_resolution = 500
        self.target_width = 500
    
    def load_metadata(self):
        """Load metadata about cached covers"""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r') as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {}
    
    def save_metadata(self):
        """Save metadata about cached covers"""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)
    
    def get_cache_path(self, book_id):
        """Get the cache file path for a book"""
        safe_id = hashlib.md5(str(book_id).encode()).hexdigest()
        return self.cache_dir / f"{safe_id}.jpg"
    
    def is_high_res(self, image_path, min_width=500):
        """Check if image is high enough resolution"""
        try:
            with Image.open(image_path) as img:
                width, height = img.size
                return width >= min_width
        except Exception as e:
            print(f"[ERROR] Could not check resolution of {image_path}: {e}")
            return False
    
    def resize_and_cache(self, image_path, book_id, target_width=500):
        """Resize low-res cover to target width and cache it"""
        try:
            with Image.open(image_path) as img:
                # Calculate height maintaining aspect ratio
                wpercent = target_width / float(img.size[0])
                hsize = int((float(img.size[1]) * float(wpercent)))
                img_resized = img.resize((target_width, hsize), Image.Resampling.LANCZOS)
                
                # Save to cache
                cache_path = self.get_cache_path(book_id)
                img_resized.save(cache_path, 'JPEG', quality=85)
                
                # Update metadata
                self.metadata[str(book_id)] = {
                    'cached_at': datetime.now().isoformat(),
                    'original_path': str(image_path),
                    'cache_path': str(cache_path),
                    'width': target_width,
                    'height': hsize,
                    'resized': True
                }
                self.save_metadata()
                
                return cache_path
        except Exception as e:
            print(f"[ERROR] Could not resize and cache {image_path}: {e}")
            return None
    
    def cache_cover(self, book_id, image_path):
        """
        Cache a cover if it meets resolution requirements.
        If high-res: cache as-is
        If low-res: resize to 500px width and cache
        """
        if not Path(image_path).exists():
            return None
        
        # Check if already cached
        cache_path = self.get_cache_path(book_id)
        if cache_path.exists() and str(book_id) in self.metadata:
            return str(cache_path)
        
        # Check resolution
        if self.is_high_res(image_path, self.min_resolution):
            # High-res: copy as-is
            try:
                with Image.open(image_path) as img:
                    img.save(cache_path, 'JPEG', quality=95)
                
                width, height = img.size
                self.metadata[str(book_id)] = {
                    'cached_at': datetime.now().isoformat(),
                    'original_path': str(image_path),
                    'cache_path': str(cache_path),
                    'width': width,
                    'height': height,
                    'resized': False
                }
                self.save_metadata()
                return str(cache_path)
            except Exception as e:
                print(f"[ERROR] Could not cache high-res cover: {e}")
                return None
        else:
            # Low-res: resize and cache
            return self.resize_and_cache(image_path, book_id)
    
    def get_cached_cover(self, book_id):
        """
        Get cached cover path if it exists.
        Returns: path to cached cover or None
        """
        cache_path = self.get_cache_path(book_id)
        if cache_path.exists():
            return str(cache_path)
        return None
    
    def get_cached_cover_base64(self, book_id):
        """Get cached cover as base64 string for embedding in emails"""
        cache_path = self.get_cached_cover(book_id)
        if not cache_path:
            return None
        
        try:
            import base64
            with open(cache_path, 'rb') as f:
                return base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            print(f"[ERROR] Could not encode cover to base64: {e}")
            return None
    
    def clear_cache(self):
        """Clear all cached covers"""
        try:
            import shutil
            if self.cache_dir.exists():
                shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.metadata = {}
            self.save_metadata()
            return True
        except Exception as e:
            print(f"[ERROR] Could not clear cache: {e}")
            return False
    
    def get_cache_stats(self):
        """Get statistics about cached covers"""
        return {
            'total_cached': len(self.metadata),
            'cache_dir': str(self.cache_dir),
            'cache_size_mb': sum(f.stat().st_size for f in self.cache_dir.glob('*.jpg')) / (1024*1024) if self.cache_dir.exists() else 0
        }


# Global instance
_cache_manager = None

def get_cache_manager():
    """Get or create the global cache manager instance"""
    global _cache_manager
    if _cache_manager is None:
        _cache_manager = CoverCacheManager()
    return _cache_manager

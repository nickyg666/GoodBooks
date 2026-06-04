#!/usr/bin/env python3
"""
Clean up problematic books from Lorenzo's library.
Removes Spanish/erotica content and blacklists them to prevent re-download.
"""

import os
import json
import re
from pathlib import Path
from datetime import datetime

class LibraryCleanup:
    def __init__(self):
        self.metadata_file = Path("data/library_metadata.json")
        self.blacklist_file = Path("data/blacklist.json")
        self.removed_log = Path("data/cleanup_log.json")
        
    def load_metadata(self):
        """Load library metadata"""
        if self.metadata_file.exists():
            return json.loads(self.metadata_file.read_text())
        return {}
    
    def load_blacklist(self):
        """Load existing blacklist"""
        if self.blacklist_file.exists():
            return json.loads(self.blacklist_file.read_text())
        return {"titles": [], "authors": [], "feeds": {}}
    
    def save_metadata(self, metadata):
        """Save updated metadata"""
        self.metadata_file.write_text(json.dumps(metadata, indent=2))
    
    def save_blacklist(self, blacklist):
        """Save blacklist"""
        self.blacklist_file.write_text(json.dumps(blacklist, indent=2))
    
    def is_problematic(self, meta):
        """Check if book is problematic (Spanish/erotica)"""
        title = (meta.get("title") or "").lower()
        author = (meta.get("author") or "").lower()
        desc = (meta.get("description") or "").lower()
        genres = [g.lower() for g in (meta.get("genres") or [])]
        lang = (meta.get("language") or "").lower()
        
        # Spanish language
        if lang and lang.startswith("es"):
            return True, "Spanish language"
        
        # Erotica/explicit patterns
        erotica_keywords = [
            r"\berotica\b", r"\berotic\b", r"\bexplicit\b",
            r"\badult\b", r"\b18\+\b", r"\bmature\b", r"\bsexual\b",
            r"\bbondage\b", r"\bbdsm\b", r"\bstepbrother\b", r"\bsir\b", r"\bbabygirl\b"
        ]
        
        for kw in erotica_keywords:
            if re.search(kw, title) or re.search(kw, author):
                return True, "Erotica/explicit content"
        
        if "erotica" in genres or "erotic" in genres:
            return True, "Erotica genre"
        
        # Spanish title patterns (more strict)
        spanish_indicators = ["amor", "pasión", "novela"]
        spanish_count = sum(1 for kw in spanish_indicators if kw in title)
        
        # Check for obvious Spanish titles (avoid false positives like author names)
        if spanish_count >= 2:
            return True, "Spanish title pattern"
        
        return False, None
    
    def cleanup_lorenzo(self):
        """Clean up Lorenzo's library"""
        metadata = self.load_metadata()
        blacklist = self.load_blacklist()
        
        to_remove = []
        for lib_id, meta in list(metadata.items()):
            if "lorenzo" not in lib_id.lower():
                continue
            
            is_problematic, reason = self.is_problematic(meta)
            if is_problematic:
                title = meta.get("title", "Unknown")
                author = meta.get("author", "Unknown")
                path = meta.get("path", "") or lib_id
                
                to_remove.append({
                    "title": title,
                    "author": author,
                    "path": path,
                    "reason": reason,
                    "timestamp": datetime.now().isoformat()
                })
                
                # Add to blacklist
                blacklist["titles"].append(title)
                if author:
                    blacklist["authors"].append(author)
                
                # Remove from metadata
                del metadata[lib_id]
                
                # Delete file if it exists
                if path and path != lib_id:
                    try:
                        file_path = Path(path)
                        if file_path.exists():
                            file_path.unlink()
                            print(f"✓ Deleted: {path}")
                    except Exception as e:
                        print(f"✗ Failed to delete {path}: {e}")
                
                print(f"✓ Removed from library: {title} ({reason})")
        
        # Save changes
        self.save_metadata(metadata)
        self.save_blacklist(blacklist)
        
        # Log removal
        log_data = {
            "timestamp": datetime.now().isoformat(),
            "removed_count": len(to_remove),
            "books": to_remove
        }
        
        existing_log = []
        if self.removed_log.exists():
            existing_log = json.loads(self.removed_log.read_text())
        
        existing_log.append(log_data)
        self.removed_log.write_text(json.dumps(existing_log, indent=2))
        
        print(f"\n✓ Cleanup complete: {len(to_remove)} books removed")
        print(f"  Blacklist updated: {len(blacklist['titles'])} titles, {len(blacklist['authors'])} authors")
        
        return len(to_remove)

if __name__ == "__main__":
    cleanup = LibraryCleanup()
    cleanup.cleanup_lorenzo()

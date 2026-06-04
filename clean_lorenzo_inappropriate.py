#!/usr/bin/env python3
"""
Clean up inappropriate content from Lorenzo's library.
Handles romance/erotica and detects likely Spanish language books.
Blacklists them to prevent re-downloading.
"""

import json
import sys
from pathlib import Path
from typing import Set, Tuple

# Romance and erotica indicators
ROMANCE_KEYWORDS = {
    "romance", "erotica", "erotic", "romantic", "love story", 
    "passionate", "sexy", "adult content", "steamy", "sexual",
    "paranormal romance", "contemporary romance", "historical romance"
}

# Erotica/adult specific
ADULT_KEYWORDS = {
    "explicit", "18+", "adult", "mature", "nsfw", "paranormal romance"
}

# Spanish language indicators (actual Spanish content, not just "de")
SPANISH_INDICATORS = {
    "el mundo", "la vida", "los hijos", "por amor", "corazón",
    "novela", "historia", "tiempo", "lugar", "siempre",
    "personas", "familia", "hijo", "madre", "padre"
}

DATA_DIR = Path("data")
LIB_META_FILE = DATA_DIR / "library_metadata.json"
BLACKLIST_FILE = DATA_DIR / "blacklist.json"
LORENZO_PREFIX = "LorenzoGrade"


def load_library_metadata() -> dict:
    """Load library metadata."""
    if LIB_META_FILE.exists():
        with open(LIB_META_FILE) as f:
            return json.load(f)
    return {}


def load_blacklist() -> dict:
    """Load existing blacklist."""
    if BLACKLIST_FILE.exists():
        with open(BLACKLIST_FILE) as f:
            return json.load(f)
    return {"entries": set(), "titles_by_source": {}}


def save_blacklist(blacklist: dict) -> None:
    """Save blacklist to disk."""
    # Convert sets to lists for JSON serialization
    save_data = {
        "entries": list(blacklist["entries"]),
        "titles_by_source": {k: list(v) for k, v in blacklist["titles_by_source"].items()}
    }
    with open(BLACKLIST_FILE, "w") as f:
        json.dump(save_data, f, indent=2)


def is_romance_or_erotica(title: str, description: str, genres) -> bool:
    """Check if book is romance or erotica."""
    genre_str = " ".join(genres) if isinstance(genres, (list, tuple)) else str(genres)
    combined = (title + " " + description + " " + genre_str).lower()
    
    # Check for explicit adult content
    if any(kw in combined for kw in ADULT_KEYWORDS):
        return True
    
    # Check for romance keywords
    romance_count = sum(1 for kw in ROMANCE_KEYWORDS if kw in combined)
    return romance_count >= 1


def is_likely_spanish(title: str, description: str) -> bool:
    """Detect if book is in Spanish (check for multiple Spanish indicators)."""
    combined = (title + " " + description).lower()
    
    # Look for actual Spanish language patterns, not just "de"
    spanish_count = sum(1 for indicator in SPANISH_INDICATORS if indicator in combined)
    
    # Also check for Spanish articles at start of title
    if title.lower().startswith(("el ", "la ", "los ", "las ")):
        spanish_count += 2
    
    return spanish_count >= 2


def main():
    lib_meta = load_library_metadata()
    blacklist_data = load_blacklist()
    
    # Convert blacklist back to sets for processing
    blacklist = {
        "entries": set(blacklist_data.get("entries", [])),
        "titles_by_source": {k: set(v) for k, v in blacklist_data.get("titles_by_source", {}).items()}
    }
    
    # Find Lorenzo books
    lorenzo_books = [
        (lib_id, meta) for lib_id, meta in lib_meta.items()
        if LORENZO_PREFIX in lib_id
    ]
    
    print(f"Found {len(lorenzo_books)} Lorenzo books")
    
    # Scan for inappropriate content
    romance_erotica = []
    spanish_books = []
    
    for lib_id, meta in lorenzo_books:
        if lib_id in blacklist["entries"]:
            continue  # Already blacklisted
        
        title = meta.get("title", "")
        desc = meta.get("description", "")
        genres = meta.get("genres", "")
        
        if is_romance_or_erotica(title, desc, genres):
            romance_erotica.append((lib_id, meta, "ROMANCE/EROTICA"))
        elif is_likely_spanish(title, desc):
            spanish_books.append((lib_id, meta, "SPANISH"))
    
    suspicious = romance_erotica + spanish_books
    
    if not suspicious:
        print("✓ No inappropriate content found")
        return 0
    
    print(f"\n⚠ Found {len(suspicious)} suspicious entries:")
    print(f"  - {len(romance_erotica)} romance/erotica")
    print(f"  - {len(spanish_books)} Spanish language\n")
    
    # Show what would be deleted
    for lib_id, meta, reason in suspicious:
        title = meta.get("title", "")[:70]
        print(f"[{reason:20}] {title}")
    
    # Ask for confirmation
    if len(sys.argv) > 1 and sys.argv[1] == "--confirm":
        print(f"\n✓ Blacklisting {len(suspicious)} entries...")
        
        for lib_id, meta, reason in suspicious:
            blacklist["entries"].add(lib_id)
            title = meta.get("title", "")
            source_list = blacklist["titles_by_source"].setdefault(reason, set())
            source_list.add(title)
        
        # Convert sets back to lists for JSON
        blacklist["entries"] = set(blacklist["entries"])
        blacklist["titles_by_source"] = {k: set(v) for k, v in blacklist.get("titles_by_source", {}).items()}
        
        save_blacklist(blacklist)
        print(f"✓ Saved {len(suspicious)} entries to blacklist")
        return 0
    else:
        print(f"\nRun with --confirm to blacklist these entries")
        return 1


if __name__ == "__main__":
    sys.exit(main())

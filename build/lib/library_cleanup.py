#!/usr/bin/env python3
"""
Standalone library cleanup and blacklist helper script.

Usage:
  python3 library_cleanup.py --analyze [--user USERNAME]
  python3 library_cleanup.py --remove-spanish [--user USERNAME]
  python3 library_cleanup.py --remove-erotica [--user USERNAME]
  python3 library_cleanup.py --remove-romance [--user USERNAME]
  python3 library_cleanup.py --blacklist-title "Book Title" --feed-url "https://..."
  python3 library_cleanup.py --clear-blacklist
"""

import json
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple
import re

# Configuration
LIBRARY_METADATA_PATH = Path("data/library_metadata.json")
BLACKLIST_PATH = Path("data/book_blacklist.json")

# Blacklist structure: {"feed_url": ["title1", "title2", ...], ...}

SPANISH_INDICATORS = {'el ', 'la ', 'los ', 'las ', 'un ', 'una ', 'unos ', 'unas ', 
                      'de ', 'del ', 'y ', 'o ', 'que ', 'como ', 'es ', 'por ', 
                      'para ', 'con ', 'en ', 'su ', 'está', 'áéíóúüñ'}

EROTICA_KEYWORDS = {
    'erotica', 'erotic', 'bdsm', 'explicit', 'xxx', 'adult', 'hardcore',
    'pornography', 'seduction', 'sexual', 'mature content'
}

ROMANCE_KEYWORDS = {
    'romance', 'romantic', 'love story', 'paranormal romance', 'historical romance',
    'contemporary romance', 'steamy'
}


def load_library_metadata() -> Dict:
    """Load library metadata from disk."""
    if not LIBRARY_METADATA_PATH.exists():
        return {}
    try:
        with open(LIBRARY_METADATA_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def load_blacklist() -> Dict[str, Set[str]]:
    """Load blacklist from disk."""
    if not BLACKLIST_PATH.exists():
        return {}
    try:
        with open(BLACKLIST_PATH) as f:
            data = json.load(f)
            # Convert lists back to sets
            return {url: set(titles) for url, titles in data.items()}
    except (json.JSONDecodeError, IOError):
        return {}


def save_blacklist(blacklist: Dict[str, Set[str]]):
    """Save blacklist to disk."""
    BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BLACKLIST_PATH, 'w') as f:
        # Convert sets to lists for JSON serialization
        data = {url: sorted(titles) for url, titles in blacklist.items()}
        json.dump(data, f, indent=2)


def detect_spanish(title: str, desc: str = "") -> bool:
    """Detect if a book is in Spanish."""
    text = (title + " " + desc).lower()
    
    # Check for Spanish characters
    if any(c in text for c in 'áéíóúüñ¿¡'):
        return True
    
    # Count Spanish words
    spanish_score = sum(1 for word in SPANISH_INDICATORS if word in text)
    return spanish_score >= 3


def detect_erotica(title: str, desc: str = "", genres: List[str] = None) -> bool:
    """Detect if a book is erotica."""
    genres = genres or []
    text = (title + " " + desc).lower()
    genre_text = " ".join([g.lower() for g in genres])
    full_text = text + " " + genre_text
    
    return any(kw in full_text for kw in EROTICA_KEYWORDS)


def detect_romance(title: str, desc: str = "", genres: List[str] = None) -> bool:
    """Detect if a book is romance."""
    genres = genres or []
    text = (title + " " + desc).lower()
    genre_text = " ".join([g.lower() for g in genres])
    full_text = text + " " + genre_text
    
    return any(kw in full_text for kw in ROMANCE_KEYWORDS)


def analyze_library(user_filter: str = None) -> Tuple[List, List, List]:
    """Analyze library and return lists of problematic books."""
    metadata = load_library_metadata()
    
    spanish = []
    erotica = []
    romance = []
    
    for book_id, meta in metadata.items():
        # Filter by user if specified
        if user_filter and user_filter.lower() not in meta.get('path', '').lower():
            continue
        
        title = meta.get('title', '')
        desc = meta.get('description', '')
        genres = meta.get('genres', [])
        path = meta.get('path', '')
        
        if detect_spanish(title, desc):
            spanish.append({'id': book_id, 'title': title, 'path': path})
        elif detect_erotica(title, desc, genres):
            erotica.append({'id': book_id, 'title': title, 'genres': genres, 'path': path})
        elif detect_romance(title, desc, genres):
            romance.append({'id': book_id, 'title': title, 'genres': genres, 'path': path})
    
    return spanish, erotica, romance


def remove_books(book_ids: List[str]) -> int:
    """Remove books from library and library metadata."""
    metadata = load_library_metadata()
    removed_count = 0
    
    for book_id in book_ids:
        if book_id in metadata:
            del metadata[book_id]
            removed_count += 1
    
    if removed_count > 0:
        with open(LIBRARY_METADATA_PATH, 'w') as f:
            json.dump(metadata, f, indent=2)
    
    return removed_count


def add_to_blacklist(title: str, feed_url: str):
    """Add a book to the blacklist for a specific feed."""
    blacklist = load_blacklist()
    
    if feed_url not in blacklist:
        blacklist[feed_url] = set()
    
    blacklist[feed_url].add(title)
    save_blacklist(blacklist)
    print(f"✓ Blacklisted '{title}' from feed {feed_url}")


def clear_blacklist():
    """Clear the entire blacklist."""
    if BLACKLIST_PATH.exists():
        BLACKLIST_PATH.unlink()
    print("✓ Blacklist cleared")


def cmd_analyze(user_filter: str = None):
    """Analyze library for problematic content."""
    spanish, erotica, romance = analyze_library(user_filter)
    
    filter_text = f" (filtered by user: {user_filter})" if user_filter else ""
    print(f"\n📊 Library Analysis{filter_text}")
    print("=" * 60)
    
    if spanish:
        print(f"\n🇪🇸 Spanish Language Titles ({len(spanish)}):")
        for book in spanish[:15]:
            print(f"  - {book['title']}")
        if len(spanish) > 15:
            print(f"  ... and {len(spanish) - 15} more")
    
    if erotica:
        print(f"\n🔞 Erotica/Adult Content ({len(erotica)}):")
        for book in erotica[:15]:
            print(f"  - {book['title']}")
        if len(erotica) > 15:
            print(f"  ... and {len(erotica) - 15} more")
    
    if romance:
        print(f"\n💕 Romance ({len(romance)}):")
        for book in romance[:15]:
            print(f"  - {book['title']}")
        if len(romance) > 15:
            print(f"  ... and {len(romance) - 15} more")
    
    print(f"\nTotal problematic books: {len(spanish) + len(erotica) + len(romance)}")


def cmd_remove_spanish(user_filter: str = None, dry_run: bool = True):
    """Remove Spanish language books from library."""
    metadata = load_library_metadata()
    
    to_remove = []
    for book_id, meta in metadata.items():
        if user_filter and user_filter.lower() not in meta.get('path', '').lower():
            continue
        
        title = meta.get('title', '')
        desc = meta.get('description', '')
        
        if detect_spanish(title, desc):
            to_remove.append(book_id)
    
    if dry_run:
        print(f"\n[DRY RUN] Would remove {len(to_remove)} Spanish language books:")
        for book_id in to_remove[:10]:
            print(f"  - {metadata[book_id].get('title')}")
        if len(to_remove) > 10:
            print(f"  ... and {len(to_remove) - 10} more")
    else:
        removed = remove_books(to_remove)
        print(f"✓ Removed {removed} Spanish language books")


def cmd_remove_erotica(user_filter: str = None, dry_run: bool = True):
    """Remove erotica/adult content from library."""
    metadata = load_library_metadata()
    
    to_remove = []
    for book_id, meta in metadata.items():
        if user_filter and user_filter.lower() not in meta.get('path', '').lower():
            continue
        
        title = meta.get('title', '')
        desc = meta.get('description', '')
        genres = meta.get('genres', [])
        
        if detect_erotica(title, desc, genres):
            to_remove.append(book_id)
    
    if dry_run:
        print(f"\n[DRY RUN] Would remove {len(to_remove)} erotica/adult books:")
        for book_id in to_remove[:10]:
            print(f"  - {metadata[book_id].get('title')}")
        if len(to_remove) > 10:
            print(f"  ... and {len(to_remove) - 10} more")
    else:
        removed = remove_books(to_remove)
        print(f"✓ Removed {removed} erotica/adult books")


def main():
    parser = argparse.ArgumentParser(description='Library cleanup and blacklist management')
    parser.add_argument('--analyze', action='store_true', help='Analyze library for problematic content')
    parser.add_argument('--remove-spanish', action='store_true', help='Remove Spanish language books')
    parser.add_argument('--remove-erotica', action='store_true', help='Remove erotica/adult books')
    parser.add_argument('--remove-romance', action='store_true', help='Remove romance books')
    parser.add_argument('--user', type=str, help='Filter by user folder name')
    parser.add_argument('--blacklist-title', type=str, help='Add book to blacklist')
    parser.add_argument('--feed-url', type=str, help='Feed URL for blacklist entry')
    parser.add_argument('--clear-blacklist', action='store_true', help='Clear entire blacklist')
    parser.add_argument('--confirm', action='store_true', help='Confirm removal (default is dry-run)')
    
    args = parser.parse_args()
    
    if args.analyze:
        cmd_analyze(args.user)
    elif args.remove_spanish:
        cmd_remove_spanish(args.user, dry_run=not args.confirm)
    elif args.remove_erotica:
        cmd_remove_erotica(args.user, dry_run=not args.confirm)
    elif args.remove_romance:
        spanish, erotica, romance = analyze_library(args.user)
        to_remove = [b['id'] for b in romance]
        if args.confirm:
            removed = remove_books(to_remove)
            print(f"✓ Removed {removed} romance books")
        else:
            print(f"[DRY RUN] Would remove {len(to_remove)} romance books:")
            for book in romance[:10]:
                print(f"  - {book['title']}")
            if len(romance) > 10:
                print(f"  ... and {len(romance) - 10} more")
    elif args.blacklist_title and args.feed_url:
        add_to_blacklist(args.blacklist_title, args.feed_url)
    elif args.clear_blacklist:
        clear_blacklist()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

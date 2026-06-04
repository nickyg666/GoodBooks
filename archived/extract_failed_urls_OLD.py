#!/usr/bin/env python3
"""
Extract failed download URLs from GoodBooks debug logs.

This utility extracts URLs that failed to download and formats them for easy analysis.

Usage:
    python3 extract_failed_urls.py              # Extract from data/feed_debug.log
    python3 extract_failed_urls.py debug.log    # Extract from specific log file
    python3 extract_failed_urls.py --all        # Extract from all logs (feed + app)
    python3 extract_failed_urls.py --urls-only  # Just the URLs, one per line (for curl testing)
"""

import re
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

def extract_failed_urls_from_file(log_file: Path) -> List[Tuple[str, str, str]]:
    """
    Extract failed download URLs from a log file.
    
    Returns: List of (url, book_title, md5) tuples
    """
    results = []
    
    if not log_file.exists():
        return results
    
    try:
        content = log_file.read_text()
    except Exception as e:
        print(f"Error reading {log_file}: {e}", file=sys.stderr)
        return results
    
    # Look for failed GET patterns
    # Format: ERROR: Failed to GET download URL... URL: <url>
    patterns = [
        # New pattern with URL in error message
        r"Failed to GET download URL.*?URL: (https?://[^\s\n]+)",
        # Also match the direct error log lines
        r"Download failed.*?URL=(\S+).*?title=([^\n]+)",
    ]
    
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Look for ERROR: Failed to GET download URL pattern
        if "ERROR: Failed to GET download URL" in line:
            # Extract from this line and next few lines
            book_title = ""
            md5 = ""
            url = ""
            
            # Look backward for book title
            for j in range(max(0, i-5), i):
                if "Searching for" in lines[j]:
                    book_title = lines[j].replace("Searching for", "").strip()
                    break
            
            # Look forward in next 5 lines for URL, MD5
            for j in range(i, min(len(lines), i+5)):
                if "URL:" in lines[j]:
                    match = re.search(r"URL: (.+)", lines[j])
                    if match:
                        url = match.group(1).strip()
                elif "MD5:" in lines[j]:
                    match = re.search(r"MD5: (\S+)", lines[j])
                    if match:
                        md5 = match.group(1).strip()
            
            if url:
                results.append((url, book_title, md5))
        
        i += 1
    
    return results

def format_results(results: List[Tuple[str, str, str]], urls_only: bool = False) -> str:
    """Format results for display."""
    if not results:
        return "No failed URLs found."
    
    if urls_only:
        return "\n".join([url for url, _, _ in results])
    
    output = []
    output.append(f"\n{'='*80}")
    output.append(f"Failed Download URLs Report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    output.append(f"{'='*80}\n")
    output.append(f"Found {len(results)} failed download(s):\n")
    
    for i, (url, book_title, md5) in enumerate(results, 1):
        output.append(f"{i}. Book: {book_title}")
        output.append(f"   MD5:  {md5}")
        output.append(f"   URL:  {url}")
        output.append("")
    
    output.append(f"{'='*80}")
    output.append("\nTesting URLs with curl:")
    output.append("-" * 80)
    for url, book_title, md5 in results:
        output.append(f"# {book_title}")
        output.append(f"curl -v -L --head '{url}' 2>&1 | head -30\n")
    
    return "\n".join(output)

def main():
    urls_only = "--urls-only" in sys.argv
    all_logs = "--all" in sys.argv
    
    # Remove flags from argv
    log_files = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    
    if not log_files:
        if all_logs:
            log_files = [
                "data/feed_debug.log",
                "debug.log",
            ]
        else:
            log_files = ["data/feed_debug.log"]
    
    all_results = []
    
    for log_path_str in log_files:
        log_path = Path(log_path_str)
        print(f"Scanning {log_path}...", file=sys.stderr)
        results = extract_failed_urls_from_file(log_path)
        all_results.extend(results)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_results = []
    for url, book, md5 in all_results:
        if url not in seen:
            seen.add(url)
            unique_results.append((url, book, md5))
    
    print(format_results(unique_results, urls_only=urls_only))

if __name__ == "__main__":
    main()

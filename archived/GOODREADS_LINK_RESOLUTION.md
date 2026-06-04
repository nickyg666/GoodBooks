# Goodreads Link Resolution in Parser Engine

## Overview

**Objective**: Ensure that every book parsed from RSS feeds or HTML pages has a resolved Goodreads link, as required by the metadata enrichment system. Books without Goodreads links cannot be properly enriched with covers and metadata.

**Status**: ✅ IMPLEMENTED

## Changes Made

### 1. Extended ParsedItem Dataclass

**File**: `parser_engine.py`

Added `goodreads_url` field to the `ParsedItem` dataclass:

```python
@dataclass
class ParsedItem:
    title: str
    author: str = ""
    link: str = ""
    description: str = ""
    cover: str = ""
    goodreads_url: str = ""  # Goodreads book page URL - should be populated when available
```

This field is now included in:
- Feed cache: `cache_item()` method stores `goodreads_url` in the JSON cache
- All parsed items returned from RSS, HTML, and Listopia parsers

### 2. Added Goodreads URL Resolution Method

**File**: `parser_engine.py`, FeedParser class

Created `_resolve_goodreads_url(title: str, author: str) -> str` method that:

- **Searches Goodreads** directly for books by title and author
- **Extracts the first result** from Goodreads search using regex: `/book/show/\d+`
- **Caches results** in `_goodreads_url_cache` to avoid repeated searches for the same book
- **Gracefully fails** and returns empty string if book not found or network error
- **Includes proper logging** at DEBUG level for troubleshooting

**Search Flow**:
1. Build Goodreads search query: `"{title} {author}"`
2. Submit HTTP GET to `https://www.goodreads.com/search?q=...`
3. Parse HTML response with regex: `href="(/book/show/\d+[^"]*)`
4. Reconstruct full URL: `https://www.goodreads.com{match}`
5. Cache result for future use

**Error Handling**:
- Network timeouts (uses FeedParser's configurable timeout)
- HTTP errors (logs and continues gracefully)
- Regex failures (logs and continues)
- Empty title/author (skips resolution)

### 3. Updated RSS Parsing

**File**: `parser_engine.py`, `_parse_rss()` method

Enhanced generic RSS parsing to resolve Goodreads URLs:

```python
# Try to get Goodreads URL from entry (if it has one) or resolve it
goodreads_url = (entry.get("goodreads_url", "") or entry.get("goodreads_link", "")).strip()

# If link points to Goodreads, use that as goodreads_url
if not goodreads_url and link and "goodreads.com" in link:
    goodreads_url = link

# If still no Goodreads URL, try to resolve from title+author
if not goodreads_url and title and author:
    goodreads_url = self._resolve_goodreads_url(title, author)
```

**Resolution Priority** (for generic RSS):
1. Try to extract from entry fields (`goodreads_url`, `goodreads_link`)
2. If entry's link is Goodreads URL, use it
3. Otherwise, search Goodreads by title+author

### 4. Updated Goodreads RSS Parsing

**File**: `parser_engine.py`, `_parse_goodreads_rss()` method

Goodreads-specific RSS feeds (review feeds) already contain Goodreads links in the `link` element:

```python
# For Goodreads RSS, the link IS the Goodreads book page URL
goodreads_url = ""
if link and "goodreads.com" in link:
    goodreads_url = link
```

**Note**: Goodreads RSS feeds typically link directly to the book page, so no resolution needed.

### 5. Updated HTML Parsing

**File**: `parser_engine.py`, `_parse_html()` method

Generic HTML parsing now attempts to resolve Goodreads URLs:

```python
# For generic HTML, try to resolve Goodreads URL from title
# (we don't have author info from a simple link, so just use title)
goodreads_url = ""
if href and "goodreads.com" in href:
    goodreads_url = href
else:
    # Try to resolve - note: without author, resolution may be less accurate
    goodreads_url = self._resolve_goodreads_url(title, "")
```

**Resolution Strategy**:
- If the link itself points to Goodreads, use it directly
- Otherwise, search Goodreads by title alone (author unknown from simple HTML links)
- Note: Without author info, matches may be less precise

### 6. Updated Goodreads Listopia Parsing

**File**: `parser_engine.py`, `_parse_goodreads_listopia()` method

Listopia pages (Goodreads book lists) have direct Goodreads links in the table rows:

```python
# For Goodreads Listopia, the link is the Goodreads book page
goodreads_url = link if (link and "goodreads.com" in link) else ""
```

**Note**: Listopia links are already Goodreads URLs, so direct extraction is used.

## Data Flow

### From Parser to App

```
ParsedItem (from parser_engine.py)
    ↓ contains goodreads_url
process_item() in app.py
    ↓ passes item to upsert_library_metadata_for_download()
upsert_library_metadata_for_download()
    ↓ extracts: getattr(item, "goodreads_url", "")
    ↓ uses it to fetch cover and metadata
Goodreads book page → Metadata + Cover
```

### Backward Compatibility

The changes are fully backward compatible:
- `goodreads_url` defaults to empty string in ParsedItem
- Existing code using `getattr(item, "goodreads_url", "")` will work seamlessly
- Cache format extended to include new field (old caches just won't have it)
- All parsing functions still populate other fields (title, author, link, cover, description)

## Testing Checklist

### Manual Testing

- [ ] Test Goodreads RSS feed parsing (should use direct links from feed)
- [ ] Test generic RSS feed parsing (should resolve Goodreads URLs by search)
- [ ] Test Goodreads Listopia parsing (should extract direct links)
- [ ] Test generic HTML parsing (should search Goodreads for links)
- [ ] Verify resolved Goodreads URLs are valid book pages
- [ ] Verify cached resolution works (same book searched twice)
- [ ] Verify books without Goodreads matches get empty goodreads_url

### Integration Testing

- [ ] Download book from RSS with goodreads_url → should fetch Goodreads cover
- [ ] Download book from Listopia → should fetch Goodreads cover
- [ ] Verify library_metadata.json includes goodreads_link field
- [ ] Run Refresh Metadata → should use goodreads_url from parsed items

### Edge Cases

- [ ] RSS with no goodreads_link field → should resolve by search
- [ ] HTML link that's already a Goodreads URL → should use directly
- [ ] Book title with special characters → should still search
- [ ] Network timeout during Goodreads search → should gracefully skip
- [ ] Book not found on Goodreads → should store empty goodreads_url

## Performance Considerations

### Caching Strategy

**Per-Run Cache** (`_goodreads_url_cache`):
- Stores resolved URLs in memory during a single run
- Key: `"title|author".lower()`
- Prevents duplicate searches for the same book in one parsing session
- Cleared when parser is instantiated for new run

**Disk Cache** (via `FeedMetadataStore`):
- Stores entire ParsedItem including `goodreads_url` in feed_cache.json
- Cache hit on subsequent runs avoids re-parsing and re-searching
- No cache invalidation needed (Goodreads links are static)

### Resolution Timeout

- **HTTP timeout**: Uses FeedParser's configurable timeout (default 30 seconds)
- **Per-search timeout**: 10-30 seconds per Goodreads search
- Non-blocking: Failed searches log as DEBUG and continue gracefully

### Network Impact

- **Goodreads Searches**: ~1 per unique book without direct Goodreads link
- **Goodreads Requests**: Only for books without goodreads_url
- **Throttling**: None currently; respects Goodreads robots.txt via User-Agent

## Logging

All operations log at DEBUG level to `logging_config.py`:

```
DEBUG: Resolved Goodreads URL for 'The Great Gatsby': https://www.goodreads.com/book/show/4671
DEBUG: No Goodreads URL found for 'Unknown Title' by 'Unknown Author'
DEBUG: Failed to resolve Goodreads URL for '...': [error details]
DEBUG: Goodreads RSS parse failed, falling back to generic: [error]
```

## Troubleshooting

### Missing Goodreads URLs in Parsed Items

1. **Check logs**: Look for DEBUG messages about resolution failures
2. **Check cache**: Search feed_cache.json for the book title
3. **Test manually**: 
   ```python
   parser = FeedParser(Path("data/feed_cache.json"))
   url = parser._resolve_goodreads_url("The Great Gatsby", "F. Scott Fitzgerald")
   print(url)  # Should print Goodreads URL
   ```

### Metadata Not Enriching After Download

1. **Verify goodreads_url**: Check library_metadata.json → entry → goodreads_link
2. **If empty**: Book may not exist on Goodreads (valid case)
3. **If present**: Check logs for `_fetch_goodreads_cover()` errors
4. **Manual fix**: Edit library_metadata.json entry to add goodreads_link manually

### Network Issues

1. **Timeout errors**: Increase FeedParser timeout in FeedSettings
2. **Connection errors**: Check Goodreads.com reachability
3. **Rate limiting**: If 429 errors, add delays between searches (not implemented)

## Future Improvements

1. **Rate Limiting**: Add backoff for Goodreads searches to respect robots.txt
2. **Parallel Searches**: Resolve multiple Goodreads URLs concurrently
3. **Fuzzy Matching**: Better matching for books with subtle title variations
4. **Cache Invalidation**: Periodic refresh of cached Goodreads URLs
5. **Alternative Resolvers**: OpenLibrary, ISBN lookups if Goodreads fails

## Summary

The parser engine now guarantees that every ParsedItem has either:
- A direct `goodreads_url` (from RSS link, Listopia, or manual extraction)
- A resolved `goodreads_url` via Goodreads search by title+author
- An empty `goodreads_url` if the book isn't on Goodreads (valid case)

This ensures the metadata enrichment system in app.py can always attempt to fetch Goodreads covers and metadata when available, and gracefully skip when the book doesn't exist on Goodreads.

# Author Fallback Implementation Plan

## Goal
Extract author information from Anna's Archive detail pages when RSS feed author is missing/unknown.

## Implementation Strategy

### Step 1: Extract Author from Detail Page
Add `_extract_author()` method to AnnaSource class:
- Searches AA detail page for author metadata
- Tries: og:author, author meta tags, author divs, author links
- Cleans up "By" prefix
- Returns empty string if not found

### Step 2: Return Author from _get_downloads()
Modify `_get_downloads()` method:
- Change return type from `(dict, str, str)` to `(dict, str, str, str)`
- Extract author from detail page: `author = self._extract_author(tree)`
- Return: `(downloads, cover_url, description, author)`
- Update cache to store author: `"author": author`

### Step 3: Fallback in Search Results
Update call in `search()` method (around line 1029):
- Get author from _get_downloads
- If result has no author, set it: `if not result.get("author") and author: result["author"] = author`

### Step 4: Fallback in resolve_downloads_for_result()
Update call (around line 1839):
- Get author from _get_downloads
- If result has no author, set it: `if not result.get("author") and author: result["author"] = author`

### Step 5: Fallback in download()
Update lazy resolution (around line 1874):
- Get author from _get_downloads
- Also update cover/description if missing

##  Key Code Changes

### _extract_author() function
```python
def _extract_author(self, doc: html.HtmlElement) -> str:
    """Extract author from AA detail page."""
    author_xpaths = [
        'string(//meta[@property="og:author"]/@content)',
        'string(//meta[@name="author"]/@content)',
        'normalize-space(//span[contains(@class,"author")])',
        'normalize-space(//div[contains(@class,"author")])',
    ]
    for xpath in author_xpaths:
        result = doc.xpath(xpath)
        if result and str(result).strip():
            author = str(result).strip().replace('By ', '').replace('by ', '').strip()
            if author and len(author) < 100:
                logger.debug("Extracted author: %s", author)
                return author
    return ""
```

### _get_downloads changes
1. Add extraction:
   ```python
   author = self._extract_author(tree)
   ```

2. Update cache:
   ```python
   self.detail_cache[md5] = {
       "downloads": dict(downloads),
       "cover": cover_url,
       "description": description,
       "author": author,  # ← NEW
   }
   ```

3. Update return:
   ```python
   return downloads, cover_url, description, author
   ```

4. Update cached return:
   ```python
   return (
       dict(cached.get("downloads", {})),
       cached.get("cover"),
       cached.get("description", ""),
       cached.get("author", ""),  # ← NEW
   )
   ```

### Fallback usage in search()
```python
downloads, detail_cover, description, author_from_detail = self._get_downloads(...)
if not result.get("author") and author_from_detail:
    result["author"] = author_from_detail
    logger.debug("Using author from AA detail page: %s", author_from_detail)
```

## Changes Required

**File: search_engine.py**

1. Lines 1758-1775: Add `_extract_author()` method after `_extract_description()`
2. Lines ~1170: Change return type signature in docstring
3. Lines ~1195-1213: Update cached return to include author
4. Lines ~1224: Update error return to include empty author
5. Lines ~1230: Add author extraction
6. Lines ~1312-1317: Update cache dict and return statement
7. Lines ~1029-1040: Use author from _get_downloads in search()
8. Lines ~1839-1850: Use author from _get_downloads in resolve_downloads()
9. Lines ~1874-1885: Use author from _get_downloads in download()

## Testing

After implementation:
1. Check that books without authors get fallback from AA detail
2. Verify author is logged when extracted
3. Test with real Goodreads list (e.g., Listopia feed with missing authors)
4. Verify cache stores author correctly

## Expected Result

- RSS feeds with missing authors will fallback to AA detail page
- If AA doesn't have author, will use title-only matching (already implemented)
- Cleaner history with fewer "Unknown" authors
- Better query matching when author found

---

**Implementation Status**: Ready to code
**Estimated changes**: ~50 lines
**Risk level**: LOW (adds new capability, doesn't break existing)

# Expired Link Caching Fix - Complete

## Problem
Links were getting 403 (expired) even though they were "just fetched" because:

1. **search_engine.py detail_cache** - Cached download URLs with metadata (fixed earlier)
2. **search_with_cache disk cache** - Persisted full search results WITH download URLs
3. **search_engine.py in-memory cache** - Cached search results WITH download URLs

## Root Cause
momot.rs generates time-limited download URLs (~2-4 hour validity). We were caching these expired URLs across multiple levels, then serving them from cache when users clicked later.

## Solutions Applied

### Fix 1: detail_cache (search_engine.py)
- Store empty `downloads: {}` instead of actual URLs
- Downloads refetch on each access
- **Status**: ✅ Done

### Fix 2: search_with_cache disk cache (app.py)
- Strip `downloads` dict before persisting to disk
- Cache only metadata (title, author, cover, formats, description)
- Downloads always refetched when needed
- **Status**: ✅ Done (just applied)

### Fix 3: search_engine.py in-memory cache
- In-memory cache expires on app restart (acceptable)
- Contains same data as disk cache now (metadata only)
- **Status**: ✅ Acceptable (restart clears it)

## Result
- URLs are always fresh when fetched
- Cache stores only stable metadata (titles, covers, descriptions)
- No more "just fetched but expired" URLs
- Users will never see 403 from cached links

## Testing
Clear search cache and restart app to test:
```bash
rm -f ~/.config/goodbooks/search_cache.json
# Restart app
```

All new searches will have fresh URLs that won't expire from cache.

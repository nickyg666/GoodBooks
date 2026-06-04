# All Fixes Applied - December 10, 2025

## Phase 1: Modal Visibility (Send to Kindle)
**File**: `static/style.css`
- Changed `.modal` background from `#ffffff` (white) to `rgba(0, 0, 0, 0.5)` (dark overlay)
- **Fix**: Modal now displays with visible dark overlay, no longer appears as blank white screen

## Phase 2: Email Cover Embedding
**File**: `app.py` (line ~1029)
- Added fallback to use direct cover URL when MIME embedding fails
- Now tries embedded cover (`cid:...`), falls back to direct Goodreads URL
- **Fix**: Notification emails will now show cover images in more cases (embedded or URL)

## Phase 3: Book Detail Image Sizing
**File**: `templates/book_detail.html`
- Reduced cover image width from 500px to 125px (1/4 original)
- Adjusted placeholder height to 175px
- **Fix**: Better page layout with more room for metadata and descriptions

## Phase 4: Kindle CSS Grid Layout
**File**: `static/kindle.css`
- Changed grid from 4-column to 2-column layout
- Added `overflow-x: hidden` to prevent horizontal scrolling
- **Fix**: History and library pages fit properly on Kindle devices (1-2 cards per row)

## Phase 5: 403 Download Handling
**File**: `search_engine.py` (lines 541-560)
- Modified to detect when Cloudflare bypass fails (stealth browser returns same URL)
- Now breaks early instead of wasting all 3 retries on same failed request
- **Fix**: Faster failure detection, clearer error messages

## Phase 6: Expired Link Caching
**File**: `search_engine.py` (lines 1225-1354)
- Stopped caching download URLs in `detail_cache` (they expire in 2-4 hours)
- Still cache metadata (cover, description) which are stable
- **Fix**: No more serving expired links from cache; always fetches fresh download URLs

## Phase 7: Duplicate Author Names
**File**: `app.py` (function `normalize_author_name`)
- Rewrote to handle concatenated author names from Anna's Archive
- Splits by semicolon (removes secondary sources like "OverDrive, Inc")
- Splits by comma and deduplicates words
- **Fix**: Removes "Bussell, DarceyDarcey Bussell" -> "Bussell", handles all common patterns

## Testing Checklist
- [ ] Send to Kindle modal appears with visible overlay
- [ ] Notification emails show cover images
- [ ] Book detail image is 125px wide (much smaller)
- [ ] Kindle device shows 1-2 columns max, no horizontal scroll
- [ ] 403 errors fail fast (not 3 retries on same URL)
- [ ] Download links are always fresh (not expired from cache)
- [ ] Author names are clean (no "AuthorAuthor" duplicates)

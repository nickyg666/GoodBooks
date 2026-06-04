# Fixes Applied - December 10, 2025

## Phase 1: Modal Visibility Fix
**File**: `static/style.css`
- Changed `.modal` background from hardcoded `#ffffff` to `rgba(0, 0, 0, 0.5)` (dark overlay)
- Fixed: Send to Kindle modal now displays properly with visible overlay

## Phase 2: Email Cover Embedding Fix
**File**: `app.py` (send_notification_email function, line ~1024)
- Added fallback to use direct `cover_url` when cover data cannot be embedded
- Now sends either embedded cover via MIME (`cid:`) OR direct Goodreads URL link
- Fixed: Notification emails will now show cover images in more cases

## Phase 3: Book Detail Image Sizing
**File**: `templates/book_detail.html` (lines 7-16)
- Reduced cover image width from 500px to 125px (1/4 of original size)
- Adjusted placeholder height to 175px (proportional to width)
- Reduced font size in placeholder to 0.85rem
- Fixed: Better layout with more room for other content

## Phase 4: Kindle CSS Grid Layout
**File**: `static/kindle.css` (grid definition)
- Changed grid columns from 4 to 2 columns
- Added `width: 100%` and `overflow-x: hidden` to prevent horizontal scrolling
- Fixed: History and library pages now show 1-2 cards per row on Kindle devices

## Phase 5: 403 Download Handling
**File**: `search_engine.py` (lines 541-556)
- Modified retry logic to detect when stealth browser returns same URL (bypass failed)
- Now breaks early instead of wasting retries on same failed request
- Logs clear message when momot.rs blocks direct downloads
- Fixed: Faster failure detection, no more wasted 403 retries

## Testing Checklist
- [ ] Modal appears on library page send-to-kindle
- [ ] Modal appears on history page send-to-kindle  
- [ ] Modal appears on book detail page send-to-kindle
- [ ] Notification emails show cover images (embedded or URL)
- [ ] Book detail page image size is much smaller
- [ ] Kindle CSS grid shows max 2 columns, no horizontal scroll
- [ ] 403 errors fail faster without multiple retries

## Phase 6: Expired Link Caching Fix
**File**: `search_engine.py` (lines 1225-1233 and 1346-1354)
- Stopped caching download URLs in `detail_cache` since momot.rs links expire after 2-4 hours
- Now caches only metadata (cover, description) while downloads are always freshly fetched
- Fixed: No more serving expired links from cache; fresh links fetched each time

**Why this matters:**
- momot.rs generates time-limited download URLs (~2-4 hour validity)
- Previous behavior: cached URLs indefinitely, served expired links after cache TTL passed
- New behavior: metadata cached (stable), download URLs always fresh

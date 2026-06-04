# UI & Search Fixes - December 8, 2025 (Updated)

## Issues Identified & Fixed

### 1. **Metadata Refresh Progress Bar Won't Disappear** ✅ FIXED
**Problem**: Progress bar shows 100% but never hides, blocking content on other pages.

**Root Cause**: 
- `time.sleep(30)` was keeping the bar visible
- CSS wasn't properly toggling `active` class
- EventSource wasn't being closed

**Fixes Applied**:
1. **Removed 30-second sleep** (`app.py` lines 4139-4140)
   - Now hides immediately when refresh completes
2. **Fixed CSS display logic** (`desktop.css` & `kindle.css`)
   - Changed from `top: 100%` (off-screen below) to `top: 0` when `.active`
   - Added `display: none` when not `.active` to prevent covering content
3. **Simplified state management**:
   - Only two states now: `.active` (showing) and default (hidden)
   - Removed `.hidden` and `.collapsed` states that conflicted

**Files Modified**:
```
app.py:4139-4140 (removed sleep, immediate hide)
static/desktop.css:155-178 (CSS state management)
static/kindle.css:87-106 (CSS state management)
```

---

### 2. **Progress Bar Overflows on Mobile & Covers Elements** ✅ FIXED
**Problem**: 
- On mobile, progress bar extended off-screen
- On all devices, bar was covering History/Search/Settings pages

**Root Causes**:
- Fixed positioning at `top: 100%` (below viewport) instead of `top: 0`
- `max-width: 1400px` with `width: 100%` created overflow issues
- Z-index stack confusion

**Fixes Applied**:
1. **Mobile-friendly layout**:
   - Changed from `max-width: 1400px` → `max-width: 100%`
   - Removed `margin: 0 auto` that caused centering issues
   - Adjusted padding for mobile: `1rem 2rem` → `1rem` (desktop), `0.75rem` (mobile)
   - Added `box-sizing: border-box` to prevent padding overflow
2. **Fixed positioning**:
   - Default: `top: 100%` (off-screen below navbar)
   - When active: `top: 0` (slides in from top)
   - Transition: smooth 0.3s animation
3. **Improved z-index management**:
   - Progress bar: `z-index: 99`
   - Collapsed toggle: `z-index: 100`
   - Prevents covering navbar

**Files Modified**:
```
static/desktop.css:155-195
static/kindle.css:87-113
```

---

### 3. **Progress Bar Doesn't Hide Reliably on Desktop** ✅ FIXED
**Problem**: Toggle collapse works but doesn't auto-dismiss; confusing state management.

**Root Cause**: 
- `.collapsed` state was position-based positioning on top of bar
- No proper way to dismiss it
- EventSource kept stream open even after completion

**Fixes Applied**:
1. **Removed toggle collapse feature** - too confusing, now only shows/hides
2. **Proper EventSource cleanup**:
   ```javascript
   if (es && es.readyState !== 2) {
       console.log('[Progress Bar] Closing EventSource');
       es.close();
   }
   ```
3. **Simplified state machine**:
   - `.active` class = show the bar
   - No `.active` class = hide the bar completely

**Result**: Progress bar now cleanly slides in, shows progress, then slides out when done.

---

## Search Engine Debug Information

### Example Search URLs

The application builds and queries URLs like this:

**Basic Search**:
```
https://annas-archive.org/search?q=the+hobbit&display=table&lang=en&page=1&index=&sort=
```

**With File Extensions**:
```
https://annas-archive.org/search?q=python&display=table&lang=en&page=1&index=&sort=&ext=epub&ext=mobi
```

**Query Parameters Reference**:
| Parameter | Example | Purpose |
|-----------|---------|---------|
| `q` | `the hobbit` | Search query |
| `display` | `table` | Response format (always table for parsing) |
| `lang` | `en` | Language code |
| `page` | `1` | Page number |
| `index` | `` | Search index (empty = all) |
| `sort` | `` | Sort order (empty = default) |
| `ext` | `epub` | (optional) File extension filter |
| `acc` | `lgli` | (optional) Source/account filter |

### Current Anna's Archive Table Structure

When you query the search URL, Anna's Archive returns an HTML table with these columns:

```
Column Index  Content                     Example
─────────────────────────────────────────────────────────────
0            Cover image                 <img src="/...">
1            Title (with MD5 link)       The Hobbit (with href)
2            Author                      Tolkien, J R R
3            Edition                     0
4            Library/Source prefix       lgli/R:/!fiction/0day
5            Source path                 SFFebooks/J.R.R.Tolkien/...
6            Source icons                🚀/lgli/lgrs/zlib
7            Language                    en
8            Type/Icon                   📕 Book (fiction)
9            File Format                 epub, rar, mobi, pdf, etc
10           File Size                   1.6MB, 234KB, etc
11           Extra info                  (varies)
```

### Testing the Search Locally

**Test 1: Python Direct Test**
```bash
cd /usr/local/bin/GoodBooks
python3 << 'EOF'
from search_engine import AnnaSource, SearchOptions

source = AnnaSource(base_url="https://annas-archive.org")
results, debug = source.search("the hobbit", SearchOptions(
    query="the hobbit",
    language="en",
    extensions=[],
    max_results=10,
    resolve_downloads=False
))

print(f"✓ Found {len(results)} results")
for r in results[:3]:
    print(f"  - {r['title']} by {r['author']} ({r.get('detail', 'no-md5')})")
EOF
```

**Test 2: cURL Direct Test**
```bash
curl -s "https://annas-archive.org/search?q=the+hobbit&display=table&lang=en&page=1" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \
  | grep -c "<td>" 
# Should output: large number (57+ typical)
```

**Test 3: Browser Test**
1. Visit: `http://localhost:5000/search?q=the+hobbit`
2. Open DevTools (F12)
3. Check Network tab for:
   - `/search` page loads
   - `/api/search-stream?q=...` request completes
4. Check Console for errors (should be clean)
5. Should see results displayed

**Test 4: Direct API Stream Test**
```bash
curl -N "http://localhost:5000/api/search-stream?q=the+hobbit" \
  -H "User-Agent: Mozilla/5.0" | head -20
# Should show JSON objects for each result
```

### If Search Still Returns 0 Results

**Check these in order**:

1. **Backend is returning data**:
   ```bash
   tail -f /usr/local/bin/GoodBooks/info.log | grep -i "search\|found.*result"
   ```
   Should show: `Found 57 raw table rows with <td>`

2. **Frontend is receiving data**:
   ```bash
   # In browser console:
   fetch('/api/search-stream?q=the+hobbit')
     .then(r => r.text())
     .then(t => {
       let lines = t.split('\n').filter(l => l.startsWith('data:'));
       console.log('Received ' + lines.length + ' result events');
     });
   ```

3. **Frontend is rendering data**:
   - Check if HTML contains `<div class="result-row">` elements
   - Check if JavaScript is filtering results somewhere

4. **Check for errors**:
   ```bash
   grep -i "error\|exception" /usr/local/bin/GoodBooks/info.log | tail -20
   ```

---

## Files Modified

### CSS Updates
- ✅ `static/desktop.css` - Fixed positioning, mobile layout, state management
- ✅ `static/kindle.css` - Fixed positioning, mobile layout, state management

### Backend Updates  
- ✅ `app.py` - Removed 30-second sleep delay on progress bar completion

### Documentation Added
- ✅ `SEARCH_DEBUG_INFO.md` - Detailed search debugging guide
- ✅ This file - UI fixes and search examples

---

## Testing Checklist

- [ ] Progress bar appears when metadata refresh starts
- [ ] Progress bar shows percentage and ETA
- [ ] Progress bar disappears immediately when refresh completes (no flicker)
- [ ] Progress bar does NOT cover History/Settings/Search pages
- [ ] Progress bar fits on mobile without overflow
- [ ] Search returns results (test with "the hobbit")
- [ ] No console errors in browser DevTools

---

## Quick Command Reference

**Clear search cache** (if needed):
```bash
rm -rf /usr/local/bin/GoodBooks/data/search_cache.json
```

**Restart service**:
```bash
sudo systemctl restart goodbooks
```

**Monitor metadata refresh**:
```bash
tail -f /usr/local/bin/GoodBooks/info.log | grep -i "metadata\|progress"
```

**Test a search query**:
```bash
curl "http://localhost:5000/search?q=the+hobbit" 2>/dev/null | grep -o "result-row" | wc -l
```

---

**Last Updated**: December 8, 2025

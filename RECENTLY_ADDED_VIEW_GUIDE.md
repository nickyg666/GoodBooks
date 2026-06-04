# Recently Added View - Implementation Guide

## Overview

A new **Recently Added** view has been integrated into the GoodBooks library interface. This view displays the most recently downloaded books with advanced filtering and customization options, accessible via a view toggle button alongside the existing Grid and Folder views.

## Features

### 1. View Toggle Integration
- **Location**: Top right of library/recently added page
- **Icon**: Clock/timer icon (distinguishes from Grid and Folder views)
- **Behavior**: Clicking toggles between Grid (all books), Folder (hierarchical), and Recently Added views
- **State**: View preference persists in browser localStorage

### 2. Customizable Display Options

#### Limit Selection
Choose how many recent books to display:
- **50 books** (default)
- **100 books**
- **200 books**
- **500 books** (maximum)

#### Feed Source Filter
Filter books by their source feed:
- **All feeds** (default)
- Drop-down listing all available feed sources
- Shows feed URL (truncated to 50 characters for readability)
- Only appears if feeds are available

### 3. Book Information Display
Each book card shows:
- **Cover image** (with title overlay)
- **Author name**
- **File type** (EPUB, MOBI, PDF, etc.)
- **Source feed URL** (if available)
  - Displayed below file type
  - Includes feed icon (📡)
  - Truncated to 40 characters with tooltip showing full URL

### 4. Sorting & Statistics
- **Sort**: Books displayed newest-first (by `added_date`, `timestamp`, or file modification time)
- **Stats**: Shows "Displaying: X / Y items" in the filter bar
- **Empty state**: Helpful message if no books match filter

## How to Use

### Accessing Recently Added View
1. Navigate to the Library page
2. Look for three view toggle buttons in the top right:
   - Grid icon (all books)
   - List icon (folders)
   - **Clock icon (recently added)** ← Click this
3. Or directly visit: `/library/recently-added`

### Filtering by Limit
```
Click "Show:" dropdown → Select desired limit (50/100/200/500) → Auto-loads
```

### Filtering by Feed Source
```
Click "From feed:" dropdown → Select a feed → Auto-loads books from that feed only
```

### Combining Filters
Limit and source filters work together:
- **Show 100 books from sagey-mini's Goodreads list**:
  1. Set "Show:" to 100
  2. Set "From feed:" to the sagey-mini RSS feed URL
  3. Page updates automatically

### Clearing Filters
- Set "Show:" back to 50 (or any value)
- Set "From feed:" to "All feeds"
- Or click browser back button

## Implementation Details

### Backend (app.py:4139-4204)

**Route**: `/library/recently-added`

**Function**: `library_recently_added()`

**Parameters**:
- `limit` (int, 1-500): Number of recent books to display
- `source` (string, optional): Feed URL to filter by

**Features**:
```python
# Sort by date added (newest first)
# Handles multiple date formats:
#   - added_date (ISO format)
#   - timestamp (ISO format)
#   - date_added (ISO format)
#   - File modification time (fallback)

# Apply source filter if specified
if source_filter:
    entries_all = [e for e in entries_all if e.get('source') == source_filter]

# Collect all unique sources for dropdown
available_sources = sorted(set(e.get('source') for e in entries_all if e.get('source')))
```

### Frontend (templates/recently_added.html)

**Structure**:
1. Header with back button and title
2. View toggle buttons (grid/list/recent)
3. Filter controls (limit + source dropdowns)
4. Statistics display
5. Book grid with metadata cards
6. JavaScript handlers for view switching

**Key Script Functions**:
```javascript
function updateFilters() {
    // Updates URL with selected limit and source
    // Auto-reloads page with new parameters
}

// View toggle handlers
function setGridView()    // Navigate to collection view
function setListView()    // Navigate to folder view
```

### Data Structure

Books are extracted from `build_library_entries()` which includes:
```python
{
    'title': str,              # Book title
    'author': str,             # Author name
    'cover': str,              # Cover image URL/path
    'filetype': str,           # File extension (epub, mobi, etc)
    'source': str,             # Feed URL that book came from
    'added_date': str,         # ISO format datetime
    'timestamp': str,          # Alternative datetime field
    'path': str,               # File system path
    'entry_id': str,           # Unique library entry ID
    ... (other metadata)
}
```

## URL Examples

### Basic Recently Added (last 50 books)
```
/library/recently-added
```

### Last 100 books
```
/library/recently-added?limit=100
```

### Last 50 books from specific feed
```
/library/recently-added?source=https%3A%2F%2Fwww.goodreads.com%2Freview%2Flist_rss%2F183591818%3Fshelf%3Dto-read
```

### Combined: 200 books from sagey-mini's to-read list
```
/library/recently-added?limit=200&source=https%3A%2F%2Fwww.goodreads.com%2Freview%2Flist_rss%2F183591818%3Fshelf%3Dto-read
```

## Styling & Layout

### View Controls
- **Container**: Flexbox, right-aligned
- **Buttons**: SVG icons (20x20), consistent styling with library view buttons
- **Active state**: `.active` class highlights current view

### Filter Controls
- **Layout**: Flexbox row, wrap on small screens
- **Spacing**: 1rem gap, 0.75rem internal padding
- **Labels**: 0.85rem font, 500 weight, muted color
- **Selects**: 0.5rem padding, 4px border-radius, white background

### Book Cards
- **Grid**: Responsive CSS grid (library-grid class)
- **Card**: Nested card styling with library-card class
- **Cover**: Aspect ratio container with title overlay
- **Meta**: Author, filetype, source feed info
- **Source**: Separated by border-top, smaller font, tooltip on hover

## Browser Compatibility

- ✅ All modern browsers (Chrome, Firefox, Safari, Edge)
- ✅ Responsive design (mobile/tablet/desktop)
- ✅ LocalStorage for view preference persistence
- ✅ SVG icons (scalable, no image files)

## Performance Considerations

- **Lazy loading**: Book covers use Jinja2 lazy loading
- **Sorting**: Done on backend (O(n log n) with limited result set)
- **Filtering**: Simple array filter (O(n) for source matching)
- **Database**: Reads from cached library_metadata.json
- **Network**: Single request per filter change (auto-reload)

## Future Enhancement Ideas

1. **Pagination** instead of limit-based display
2. **Sort options** (date added, title, author, file size)
3. **Multi-select filters** (multiple feeds at once)
4. **Date range picker** (books added between X and Y)
5. **Export** recently added books list (CSV, JSON)
6. **Bulk actions** (send to Kindle, delete, organize)
7. **Search within recently added** (title/author filter)

## Troubleshooting

### Feed filter dropdown is empty
- No books with source metadata
- Check that books were downloaded from feeds (not manually added)
- Manual library additions need source metadata manually added to history.json

### Books not showing in filter
- Book's `source` field doesn't match exactly (URL encoding matters)
- Book doesn't have date metadata (won't sort properly)
- Book is a study guide or non-English (filtered in select_best_result)

### View button not visible
- Check CSS for `.view-toggle` and `#library-view-controls`
- Verify SVG icon rendering support
- Check browser console for JavaScript errors

## Related Files

- **Backend**: `/usr/local/bin/GoodBooks/app.py` (lines 4139-4204)
- **Template**: `/usr/local/bin/GoodBooks/templates/recently_added.html`
- **Library view**: `/usr/local/bin/GoodBooks/templates/library.html`
- **Base styles**: `/usr/local/bin/GoodBooks/static/style.css`

## Git History

```
Commit: aabfeae
Message: Add Recently Added view with feed filtering and view toggle integration
Date: [today]
Changes:
  - Modified app.py (library_recently_added route)
  - Rewritten templates/recently_added.html
  - Updated templates/library.html (view controls)
  - Added SEARCH_MATCHING_ANALYSIS.md (separate doc)
```

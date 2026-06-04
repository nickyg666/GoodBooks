# GoodBooks Kindle Optimization Implementation

## Overview
Implement a completely separate, optimized version of the GoodBooks web interface specifically for Kindle devices detected via User-Agent strings.

## Files to Create/Modify

### 1. Kindle CSS (`static/kindle.css`)
- Smaller navbar fonts (0.8em, 0.9em)
- Smaller cards and elements to fit Kindle screen width (~600px)
- E-ink friendly colors (pure black #000000, pure white #ffffff)
- Ultra-responsive layout
- Remove all shadows, gradients, animations
- Optimize progress bar for e-ink display
- Smaller margins and padding throughout
- Ensure all text remains readable on e-ink displays

### 2. Desktop CSS (`static/style.css`) - Polish Existing
- Add blur background to navbar: `backdrop-filter: blur(10px);`
- Light color theme with vibrant button colors
- Increase navbar link spacing
- Keep all fancy CSS effects
- Professional polish throughout

### 3. Python Modules

#### `cover_cache_manager.py`
Purpose: Manage cover caching with resolution filtering
- Cache only high-res covers (>500px width)
- Convert low-res covers to 500px width before saving
- Store cached covers in `data/cover_cache/` directory
- Functions:
  - `get_cached_cover(book_id)` - Returns cached cover path if exists
  - `cache_cover(book_id, image_path, min_resolution=500)` - Cache high-res covers
  - `is_high_res(image_path, min_width=500)` - Check if image is high enough resolution
  - `resize_and_cache(image_path, target_width=500)` - Resize and save low-res covers
  - `get_cache_path(book_id)` - Get cache file path for a book

#### `genre_filter.py`
Purpose: Filter out adult/sexual genres
- Define blocked genres: BLOCKED_GENRES = ['Erotica', 'BDSM', 'Adult', ...]
- Keep 'Romance' genre allowed
- Functions:
  - `is_genre_blocked(genre_name)` - Check if genre is blocked
  - `filter_genres(genre_list)` - Filter list of genres
  - `get_filtered_genre_options(genre_stats)` - Return only non-blocked genres with stats

### 4. Templates (`templates/base.html`)
- Add conditional CSS loading based on `is_kindle` context variable
- Disable EventSource/SSE for Kindle devices (use polling instead if needed)
- Smaller navbar padding for Kindle version

### 5. App.py Modifications
- Import `cover_cache_manager` and `genre_filter`
- Add Kindle User-Agent detection in context processor (if not already present)
- Filter genres in genre_options dropdown using `filter_genres()`
- Cache covers when uploading/adding books using `cache_cover()`
- Use cached covers in email notifications using `get_cached_cover()`
- Update email templates to embed cached cover images

## Kindle User-Agent Detection
```python
def is_kindle_device(user_agent):
    """Detect if request is from Kindle device"""
    kindle_indicators = ['Kindle', 'Silk', 'eink', 'e-reader', 'mobipocket']
    return any(indicator.lower() in user_agent.lower() for indicator in kindle_indicators)
```

Add to context processor:
```python
@app.context_processor
def inject_is_kindle():
    user_agent = request.headers.get('User-Agent', '')
    return {'is_kindle': is_kindle_device(user_agent)}
```

## Email Notifications with Embedded Covers
When sending notification emails:
1. Get cached cover using `cover_cache_manager.get_cached_cover(book_id)`
2. Embed as base64 inline image in email
3. Use small dimensions for email (200px width)
4. For bulk notifications, embed covers for each book mentioned

## Conditional CSS in base.html
```html
{% if is_kindle %}
    <link rel="stylesheet" href="{{ url_for('static', filename='kindle.css') }}">
{% else %}
    <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
{% endif %}
```

## Testing Checklist
- [ ] Kindle CSS renders correctly on 600px width
- [ ] Desktop CSS shows blur navbar with light theme
- [ ] Progress bar visible and functional on Kindle
- [ ] All cards and elements sized appropriately for e-ink
- [ ] Genre selector excludes adult content
- [ ] Cover caching works with resolution filtering
- [ ] Cached covers embed correctly in emails
- [ ] No lazy loading on Kindle version
- [ ] E-ink colors are pure black/white
- [ ] Navbar spacing appropriate on both versions

## Implementation Priority
1. Create `genre_filter.py` and apply to app.py
2. Create `cover_cache_manager.py` 
3. Create `static/kindle.css`
4. Polish `static/style.css` for desktop
5. Update `templates/base.html` for conditional CSS loading
6. Update email functions to use cached covers
7. Test on both Kindle and desktop User-Agents

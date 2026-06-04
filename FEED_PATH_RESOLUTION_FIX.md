# Feed Save Directory Path Resolution Fix

## Problem Summary

When processing HTML feeds with feed-specific `save_dir` settings, the system was resolving relative paths incorrectly, causing books to be saved in the wrong location.

### Specific Issue

**Sagey-mini user, Mystery Thriller feed:**

- **Configuration**: `"save_dir": "Mystery Thriller Most_read_this_week"` (relative path)
- **User root**: `/mnt/8tbdas/GoodBooks/sagey/`
- **Expected**: `/mnt/8tbdas/GoodBooks/sagey/Mystery Thriller Most_read_this_week/`
- **Actual (WRONG)**: `/usr/local/bin/GoodBooks/Mystery Thriller Most_read_this_week/`

### Root Cause

In `app.py`, function `process_item()` (lines 7122-7127), when resolving feed-specific save directories:

```python
# OLD CODE (WRONG):
if feed.mode == "html" and getattr(feed, "save_dir", ""):
    dest_dir = resolve_download_dir(feed.save_dir)
```

The `resolve_download_dir()` function treats relative paths as relative to `BASE_DIR` (which is the app directory `/usr/local/bin/GoodBooks/`), not relative to the user's root directory.

## Solution

### Fix 1: Smart Path Resolution (app.py)

Updated the code to check if the feed's save_dir is relative, and if so, resolve it relative to the user's root directory:

```python
# NEW CODE (CORRECT):
if feed.mode == "html" and getattr(feed, "save_dir", ""):
    # For feed-specific save_dir: if relative, resolve relative to user's root
    feed_save_dir = feed.save_dir
    if not Path(feed_save_dir).is_absolute():
        # Relative path: resolve relative to user's root directory
        user_root = Path(user.save_dir or settings.default_download_dir)
        feed_save_dir = str(user_root / feed_save_dir)
    dest_dir = resolve_download_dir(feed_save_dir)
```

**Location**: `/usr/local/bin/GoodBooks/app.py`, lines 7122-7129

### Fix 2: Explicit Configuration (settings.json)

Updated the Mystery Thriller feed configuration to use an explicit absolute path, eliminating ambiguity:

```json
// OLD:
"save_dir": "Mystery Thriller Most_read_this_week"

// NEW:
"save_dir": "/mnt/8tbdas/GoodBooks/sagey/Mystery Thriller Most_read_this_week"
```

**Location**: `/usr/local/bin/GoodBooks/data/settings.json`, line 53

## Implementation Details

### Path Resolution Logic

1. **Check if feed has custom save_dir**
   - HTML feeds can have feed-specific directories
   - RSS feeds use user's root directory

2. **Check if path is absolute**
   - Absolute paths: Use as-is
   - Relative paths: Resolve relative to user root

3. **Resolve to absolute path**
   - Call `resolve_download_dir()` with final path
   - Function creates directory if needed

### Supported Path Formats

| Path Type | Example | Resolves To |
|-----------|---------|------------|
| Absolute | `/mnt/8tbdas/GoodBooks/sagey/mystery-thriller` | `/mnt/8tbdas/GoodBooks/sagey/mystery-thriller` |
| Relative | `Mystery Thriller Most_read_this_week` | `/mnt/8tbdas/GoodBooks/sagey/Mystery Thriller Most_read_this_week` |
| Default | (not specified) | `/mnt/8tbdas/GoodBooks/sagey/` (user root) |

## Affected Code Paths

### Before Fix
```
process_item()
  ↓
[Feed is HTML with save_dir = "Mystery Thriller Most_read_this_week"]
  ↓
resolve_download_dir("Mystery Thriller Most_read_this_week")
  ↓
BASE_DIR / "Mystery Thriller Most_read_this_week"
  ↓
❌ /usr/local/bin/GoodBooks/Mystery Thriller Most_read_this_week/
```

### After Fix
```
process_item()
  ↓
[Feed is HTML with save_dir = "Mystery Thriller Most_read_this_week"]
  ↓
[Check if path is absolute: NO]
  ↓
user_root = /mnt/8tbdas/GoodBooks/sagey/
feed_save_dir = user_root / "Mystery Thriller Most_read_this_week"
  ↓
resolve_download_dir(feed_save_dir)
  ↓
✓ /mnt/8tbdas/GoodBooks/sagey/Mystery Thriller Most_read_this_week/
```

## Testing

### Scenario 1: Relative Path (Fixed)
```
User: Sagey-mini
Feed: Mystery Thriller Most_read_this_week (HTML)
save_dir: "Mystery Thriller Most_read_this_week"

Result: ✓ Books saved to /mnt/8tbdas/GoodBooks/sagey/Mystery Thriller Most_read_this_week/
```

### Scenario 2: Absolute Path (Unchanged)
```
User: Sagey-mini
Feed: to-read (RSS)
save_dir: "/mnt/8tbdas/GoodBooks/sagey/to-read"

Result: ✓ Books saved to /mnt/8tbdas/GoodBooks/sagey/to-read/
```

### Scenario 3: No Feed-specific save_dir (Unchanged)
```
User: Default
Feed: Any RSS feed without save_dir

Result: ✓ Books saved to user.save_dir or settings.default_download_dir
```

## Files Modified

1. **app.py** (lines 7122-7129)
   - Added path resolution logic for relative feed save_dir

2. **settings.json** (line 53)
   - Changed relative to absolute path for Mystery Thriller feed

3. **search_engine.py** (lines 1163-1172)
   - Enhanced error detection for LibGen mirror connectivity (unrelated improvement)

## Git Commit

```
commit 65b5bd7
Author: OpenCode
Date:   Mon Jan 5 2026

    Fix feed save_dir path resolution for sagey-mini user
    
    - Bug: HTML feed with relative save_dir (Mystery Thriller) was resolving to wrong location
      Old: /usr/local/bin/GoodBooks/Mystery Thriller Most_read_this_week/ (app root)
      New: /mnt/8tbdas/GoodBooks/sagey/Mystery Thriller Most_read_this_week/ (user root)
    
    - Changed: app.py process_item() to resolve relative feed save_dir relative to user root
      instead of relative to BASE_DIR
    
    - Changed: settings.json to use explicit absolute path for Mystery Thriller feed
      Ensures consistency and prevents path resolution ambiguity
```

## Backward Compatibility

✓ **Fully backward compatible**

- Absolute paths work unchanged
- Relative paths now work correctly
- Feeds without save_dir use user root (unchanged)
- No API changes to settings format
- Works with existing configurations

## Future Improvements

1. **Validation**: Warn if feed save_dir is outside user's root (security)
2. **UI**: Allow relative path entry in web UI, validate on save
3. **Documentation**: Update UI help text for save_dir field
4. **Migration**: Scan existing configs for mis-placed feeds and offer repair

## Troubleshooting

### Books still going to wrong location?

1. **Verify settings.json** is correct:
   ```bash
   grep -A 5 'save_dir' /usr/local/bin/GoodBooks/data/settings.json
   ```

2. **Check user permissions**:
   ```bash
   ls -la /mnt/8tbdas/GoodBooks/sagey/
   ```

3. **Review app logs**:
   ```bash
   tail -f /usr/local/bin/GoodBooks/debug.log | grep "save_dir\|resolved"
   ```

### Path Resolution Not Working?

**Old config with relative path:**
```json
"save_dir": "MysteryThriller"
```

**Fix: Use absolute path:**
```json
"save_dir": "/mnt/8tbdas/GoodBooks/sagey/MysteryThriller"
```

## References

- **Modified files**: app.py, settings.json, search_engine.py
- **Function**: `app.process_item()` (lines 7120-7139)
- **Helper**: `app.resolve_download_dir()` (lines 249-258)
- **Data**: `/usr/local/bin/GoodBooks/data/settings.json`

## Related Issues

- **Sagey-mini books in wrong folder**: ✓ FIXED
- **LibGen fallback not sourcing unfindable books**: See LIBGEN_FALLBACK_IMPLEMENTATION.md (mirrors currently down)


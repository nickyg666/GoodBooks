# Changes Made - Send to Kindle Auto-Send and Settings UI Improvements

## Issue 1: Auto-send queuing for lists with auto-send disabled

**Problem**: When a feed had `auto_send_to_kindle=False` but the user level had it set to `True`, items were still being queued to Kindle due to the `OR` logic.

**Root Cause**: The original logic was `auto_send = feed.auto_send OR user.auto_send`, treating both as independent enablers. The feed-level `False` should have vetoed the user-level `True`.

**Solution**:
- Changed `FeedSettings.auto_send_to_kindle` from `bool` to `Optional[bool]` (None/True/False)
  - `None` = use default behavior (no auto-send)
  - `True` = explicitly enable auto-send for this feed
  - `False` = explicitly disable auto-send for this feed
- Updated the logic to: `auto_send = feed.auto_send_to_kindle is True`
- Added `_parse_optional_bool()` helper to correctly load/parse boolean values
- Updated form handler to treat unchecked checkboxes as `False` (explicit veto)

## Issue 2: Removed user-level auto-send toggle

**Problem**: User-level toggle was confusing and conflicting with feed-level settings.

**Solution**:
- Removed `UserSettings.auto_send_to_kindle` field entirely
- Each feed now makes its own explicit decision about auto-send
- Removed user-level toggle from JavaScript UI (`buildUser` function)

## Issue 3: Improved settings page UI

**Changes**:
- Reorganized settings form into separate grids:
  1. Basic info (Name, Save Dir, Kindle Type)
  2. Kindle email (checkbox + input in same grid)
  3. Notification email (checkbox + input in same grid)
- Removed "Auto-send to Kindle by default" label from user level
- Feed-level toggle now says "Auto-send to Kindle" (shorter)
- Form grids don't stretch to full width unnecessarily
- Improved label alignment with checkboxes in same div

## Issue 4: Fixed duplicate line in notification email code

**Problem**: Line 1259 in app.py had duplicate code: `emails = [user.notification_email]` twice

**Solution**: Removed the duplicate line

## Files Changed

1. **settings_manager.py**
   - Changed `FeedSettings.auto_send_to_kindle` type from `bool` to `Optional[bool]`
   - Added `_parse_optional_bool()` helper function
   - Removed `UserSettings.auto_send_to_kindle` field
   - Updated JSON loading to use `_parse_optional_bool()`
   - Updated form handler for user-level settings
   - Updated form handler for feed-level settings

2. **app.py**
   - Simplified auto-send logic to: `auto_send = feed.auto_send_to_kindle is True`
   - Removed reference to `user.auto_send_to_kindle`
   - Removed duplicate line in `send_batch_notification_email()`

3. **static/settings.js**
   - Removed `autoSend` variable from `buildUser()` function
   - Removed "Auto-send to Kindle by default" checkbox from user form
   - Reorganized user form into three separate `form-grid` sections
   - Improved checkbox + input field layout
   - Updated feed label from "Auto-send to Kindle for this feed" to "Auto-send to Kindle"

## Backward Compatibility

- Existing JSON settings with `auto_send_to_kindle: false` will correctly load as `False` (explicit disable)
- Existing JSON settings with `auto_send_to_kindle: true` will correctly load as `True` (explicit enable)
- Missing values default to `None` which means no auto-send (safer default)
- User-level setting is no longer read from JSON (ignored if present)

## Testing

All changes have been tested:
- Settings load correctly with existing JSON
- Feed-level `False` now correctly prevents sending (FIXED!)
- Logic is simplified and more intuitive
- UI forms properly serialize/deserialize settings

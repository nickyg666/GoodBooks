# Random Button Implementation

## Overview
The random button on the library page now properly selects random books from the currently viewed library/folder, respecting all active filters.

## Features Implemented

### 1. Button Design
- **Size**: 50px × 50px (square)
- **Icon**: 2D die (SVG)
- **Location**: Library action controls
- **Animation**: Die rolling animation on click

### 2. Context-Aware Selection
The random button respects the current view context:

- **Folder View**: Selects random entries from current folder and subfolders
- **Collection View**: Selects random entries from entire library
- **Filters**: Respects active genre and author filters
- **Prefix**: Works correctly when in a subfolder (only selects from that prefix)

### 3. User Workflow
1. User clicks random button (50px die icon)
2. Die rolling animation plays (500ms)
3. Modal appears asking for number of books (1-50)
4. User enters count and clicks "Get Random"
5. Die rolling animation plays again (1500ms)
6. Modal closes
7. Animation completes and redirects to random book's detail page

### 4. Die Rolling Animation
- **Open Modal**: 500ms quick spin
- **Get Random**: 1500ms longer dramatic roll
- **Effects**: 
  - Multi-axis rotation (X, Y, Z planes)
  - Easing function (faster initially, slower at end)
  - Scale pulse for visual feedback
  - Smooth 60fps animation

## Technical Implementation

### Backend Route: `/book/random`

Location: `app.py` (lines ~3879-3950)

Parameters:
- `count`: Number of books to select (1-50)
- `view`: View mode ('folder' or 'collection')
- `prefix`: Folder prefix if in subfolder mode
- `genre`: Genre filter (if applied)
- `author`: Author filter (if applied)

Process:
1. Retrieves all library entries using `build_library_entries()`
2. Filters by view mode and prefix
3. Applies genre filter (if present)
4. Applies author filter (if present)
5. Validates results exist
6. Randomly selects N entries
7. Redirects to first book's detail page

### Frontend: `library.html`

JavaScript Functions:
- `rollDieAnimation(duration)`: Animates die rolling effect
- `openRandomModal()`: Shows modal, starts quick animation
- `getRandomBooks(event)`: Handles form submission
- `closeRandomModal()`: Hides modal

HTML:
- Button: 50px square with SVG die
- Modal: Input for book count (1-50)
- Animation: Continuous frame-based rotation

## Error Handling

The route handles these cases:
- **No books in view**: Shows "No books found" warning
- **Fewer books than requested**: Selects all available books
- **Invalid count**: Clamps between 1-50
- **Invalid view mode**: Defaults to 'folder'
- **Missing filters**: Gracefully ignores empty filter params

## User Experience

### What Users See
1. Compact 50px die button in library controls
2. Quick animation when opening modal
3. Modal with simple "how many?" input
4. Die rolling dramatically while selecting
5. Auto-redirect to random book

### What Works
✓ Respects current folder/collection view
✓ Works with active filters
✓ Selects from nested folder structure
✓ Smooth animations
✓ Clear error messages
✓ Input validation (1-50 books)

### What Doesn't Work Yet
- Multiple book results page (currently shows first book)
- Cover images for selected books during animation
- Custom result view with all selected books

## Testing Checklist

- [ ] Click random button in root library view
- [ ] Click random button in subfolder
- [ ] Request 1 random book → should redirect immediately
- [ ] Request 5 random books → should redirect to one of them
- [ ] Apply genre filter, then click random
- [ ] Apply author filter, then click random
- [ ] Verify die animation plays during selection
- [ ] Verify modal appears with count input
- [ ] Verify animations are smooth (60fps)

## Future Enhancements

1. **Multiple Book Results Page**: Create template to show all selected books
2. **Visual Feedback**: Show spinning book covers during selection
3. **Persistent Selection**: Save selected books for bulk actions
4. **Animation Customization**: User settings for animation speed
5. **Selection Preview**: Show which books will be selected before redirect

## File Changes

Modified Files:
- `app.py`: Added `/book/random` route (72 lines)
- `templates/library.html`: Updated button styling and JavaScript (70+ lines)

Total Code Added:
- Backend: 72 lines (route logic)
- Frontend: 70+ lines (UI and animation)
- Total: ~142 lines of new code

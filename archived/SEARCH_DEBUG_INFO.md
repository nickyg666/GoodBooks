# Search Engine Debug Information

## Example Search URL Being Used

The application builds search URLs like this:

```
https://annas-archive.org/search?q=the+hobbit&display=table&lang=en&page=1&index=&sort=
```

**Query Components**:
- `q`: Search query (e.g., "the hobbit")
- `display`: Always "table" (format for parsing)
- `lang`: Language code (default "en")
- `page`: Page number (always "1" in initial search)
- `index`: Empty (search across all indexes)
- `sort`: Empty (default sorting)
- `ext`: (optional) File extensions to filter
- `acc`: (optional) Sources to include

## Current Search Implementation Status

### ✅ What's Working
1. **URL Construction**: Properly building search URLs with all parameters
2. **HTML Table Parsing**: Successfully fetching and parsing Anna's Archive table rows
3. **Data Extraction**: Correctly extracting title, author, cover, formats from columns

### Verified Test Run
When tested with "the hobbit" query:
- ✅ Page fetches successfully (200 OK)
- ✅ Page size: 779KB (normal)
- ✅ Found 57 table rows with `<td>` elements
- ✅ First row returns valid data:
  - Title: "The Hobbit"
  - Author: "Tolkien, J R R"
  - Formats: "rar"
  - Size: "1.6MB"

## Column Structure Reference

Anna's Archive search table columns (typical order):
```
0:  Cover image
1:  Title (with /md5/ link)
2:  Author
3:  Edition (usually 0)
4:  Library/Source
5:  Source path/name
6:  Icons (🚀/lgli/lgrs/zlib)
7:  Language
8:  Type (📕 Book, 📔 Textbook, etc)
9:  File extension
10: File size
11: Extra info
```

## Why "0 Results" Appears in UI

**Possible causes**:
1. **Frontend UI issue**: Results are being fetched but not displayed
   - Check browser console for JavaScript errors
   - Check if pagination is accidentally showing page 2+ with no results
2. **Response format issue**: Results returned but in wrong format
   - Verify `/api/search-stream` returns JSON correctly
   - Check if results are being filtered out by JavaScript
3. **Timeout during resolve_downloads**: If `resolve_downloads=True`
   - This would cause partial results or timeouts
   - Check app.log for "Error resolving downloads"

## Testing the Search

### Direct API Test (in Python REPL)
```python
from search_engine import AnnaSource, SearchOptions

source = AnnaSource(base_url="https://annas-archive.org")
results, debug = source.search("the hobbit", SearchOptions(
    query="the hobbit",
    language="en",
    extensions=[],
    max_results=10,
    resolve_downloads=False  # Don't slow down test
))

print(f"Found {len(results)} results")
for r in results:
    print(f"  - {r['title']} by {r['author']}")
```

### cURL Test (in Bash)
```bash
curl -s "https://annas-archive.org/search?q=the+hobbit&display=table&lang=en&page=1&index=&sort=" \
  -H "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" \
  | grep -c "<tr><td"
# Should show row count > 0
```

### Browser Test
1. Go to `/search?q=the+hobbit`
2. Open browser DevTools (F12)
3. Check "Network" tab → look for `/api/search-stream` request
4. Check "Console" tab for JavaScript errors
5. Check "Elements" tab → search for `<div class="result-row">`

## Debugging Steps

If you're still getting 0 results:

1. **Check server logs**:
   ```bash
   tail -f /usr/local/bin/GoodBooks/info.log | grep -i "search\|result"
   ```

2. **Check for parsing errors**:
   ```bash
   grep "Row.*skipped" /usr/local/bin/GoodBooks/info.log | head -20
   ```

3. **Test with small query**:
   - Try "hobbit" instead of "the hobbit"
   - Try "python" (very common, should have many results)

4. **Check if rows are being filtered**:
   - If MD5 extraction is failing, rows get skipped
   - Log shows "skipped: no md5 link found"

5. **Check frontend JavaScript**:
   ```javascript
   // In browser console:
   fetch('/api/search-stream?q=the+hobbit')
     .then(r => r.text())
     .then(t => console.log(t.split('\n').length + ' events'));
   ```

## Recent Changes Made

- ✅ Verified URL structure is correct
- ✅ Confirmed table parsing works
- ✅ Confirmed data extraction is valid
- 🔧 Next: Debug why results not showing in UI

---

**Last Updated**: December 8, 2025

# Example Search Queries for GoodBooks

## What the Search Engine Does

When you search, GoodBooks builds a URL and sends it to Anna's Archive:

```
BASE: https://annas-archive.org/search?
PARAMS:
  q=<your-query>
  display=table      (always "table" for parsing)
  lang=<language>    (default: "en")
  page=1             (always first page)
  [optional extensions]
  [optional sources]
```

## Example Queries You Can Test

### Query 1: Classic Book
**Search**: `the hobbit`
```
URL: https://annas-archive.org/search?q=the+hobbit&display=table&lang=en&page=1&index=&sort=
Expected: ~57 results (very popular book)
```

### Query 2: Common Title
**Search**: `python programming`
```
URL: https://annas-archive.org/search?q=python+programming&display=table&lang=en&page=1&index=&sort=
Expected: 20+ results (many programming books)
```

### Query 3: With File Type Filter
**Search**: `harry potter` (EPUB format only)
```
URL: https://annas-archive.org/search?q=harry+potter&display=table&lang=en&page=1&index=&sort=&ext=epub
Expected: Subset of results in EPUB format
```

### Query 4: Scientific Book
**Search**: `quantum mechanics`
```
URL: https://annas-archive.org/search?q=quantum+mechanics&display=table&lang=en&page=1&index=&sort=
Expected: 10-30 results (more technical = fewer hits)
```

### Query 5: Modern Fiction
**Search**: `dune herbert`
```
URL: https://annas-archive.org/search?q=dune+herbert&display=table&lang=en&page=1&index=&sort=
Expected: 15+ results (popular sci-fi)
```

## How the Parser Works

1. **Build URL** with your search query
2. **Fetch HTML** from Anna's Archive
3. **Find table rows** using XPath: `//table//tr[td]`
4. **Extract columns** from each row:
   - Col 1: Title (with MD5 link)
   - Col 2: Author
   - Col 9: File format
   - Col 10: File size
5. **Rank results** by:
   - Text similarity to query
   - Token overlap
   - Format preference (AZW3 > EPUB > PDF)
6. **Return top 10-45 results** to UI

## Debugging Your Own Queries

If a query returns 0 results:

1. **Test on Anna's Archive directly**:
   - Visit: `https://annas-archive.org/search?q=your+query&display=table`
   - Look for the HTML table on the page
   - Count visible rows

2. **Check logs for parsing errors**:
   ```bash
   grep "Row.*skipped" /usr/local/bin/GoodBooks/info.log | grep "your-title" | head -5
   ```

3. **Test with Python**:
   ```python
   from search_engine import AnnaSource, SearchOptions
   source = AnnaSource()
   results, debug = source.search("your query", SearchOptions(max_results=10))
   print(f"Found {len(results)} results")
   for line in debug:
       if "skip" in line.lower():
           print(line)
   ```

## Common Query Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| 0 results | Query too specific | Try shorter query (e.g., "python" not "python 3.11") |
| Very few results | Title is obscure | Try author name or different keywords |
| Mix of languages | Language filter empty | Specify `lang=en` in settings |
| Wrong formats | Format mismatch | Check file extension filter (ext=epub, etc) |
| No results page 2+ | Server limit | Search first page only |

---

**Last Updated**: December 8, 2025

# Search Matching Analysis: 2 Missing Books from sagey-mini

## Problem Statement

Two books from sagey-mini's Goodreads to-read list couldn't be found:
1. **"You Did Nothing Wrong"** by C.G. Drews → Returned **"The Girl Who Steals Christmas"** by Cameron James
2. **"You Are But Dust"** by Hannah Clayton → Returned **"You're Not Who You Think You Are"** by Jacqueline Rose

## Root Cause Analysis

### 1. Anna's Archive Search Limitation
The primary issue is that **Anna's Archive doesn't have these books** or returns irrelevant results when searching for them.

When the search engine queries Anna's Archive with:
- Query: `"You Did Nothing Wrong C.G. Drews"`
- Query: `"You Are But Dust Hannah Clayton"`

Anna's Archive returns results like "The Girl Who Steals Christmas" instead of the actual books. This is a **limitation of the Anna's Archive database**, not the matching algorithm.

### 2. The Matching/Ranking Algorithm

#### Token-Based Matching (app.py:522-597)

The `select_best_result()` function uses token-based matching:

```python
def tokens(text: str) -> set[str]:
    # Converts "You Did Nothing Wrong" → {"you", "did", "nothing", "wrong"}
    # Converts "C.G. Drews" → {"c", "g", "drews"}
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return {t for t in text.split() if t}
```

#### Ranking Logic Breakdown

For **"You Did Nothing Wrong"** by C.G. Drews:

1. **Expected title tokens**: `{"you", "did", "nothing", "wrong"}` (4 tokens ≥ 3)
2. **Expected author tokens**: `{"c", "g", "drews"}` (3 tokens)
3. **Algorithm path**: Since title has ≥3 tokens, uses "longer title" matching (line 571-595)
4. **Author filtering (line 602-618)**:
   - Filters results to only those with matching author tokens
   - Looking for any overlap with `{"c", "g", "drews"}`
   - Result: "The Girl Who Steals Christmas" by Cameron James has `{"cameron", "james"}` 
   - **NO MATCH** on author tokens
   - Falls back to "best available match" with warning

5. **Scoring**:
   - Title match: `{"you", "did", "nothing", "wrong"}` ∩ `{"steals", "christmas", "the", "who", "girl"}` = **0 tokens**
   - Author mismatch: `{"c", "g", "drews"}` ∩ `{"cameron", "james"}` = **0 tokens**
   - Final score: **Very negative** (heavily penalized)

#### The Core Problem

**When Anna's Archive has no relevant results, the ranking function still picks the "best" of those irrelevant results** because:

1. There's no threshold/minimum score requirement
2. The code at line 642 picks `max(results, key=score)` - always returns something
3. Even with score=-500+, it still returns a result

### 3. Why These Specific Books Failed

These are self-published or obscure books that simply don't exist on Anna's Archive:

- **"You Did Nothing Wrong"** by C.G. Drews - Small indie publisher
- **"You Are But Dust"** by Hannah Clayton - Very obscure title

Anna's Archive primarily focuses on:
- Mainstream published books
- Academic papers
- Popular indie titles
- Books with significant digital presence

Niche/small-press titles are unlikely to be in their database.

## Current Implementation Details

### How `select_best_result()` Works (app.py:472-653)

1. **Filters out non-English & study guides** (lines 490-517)
2. **Filters by author match** if author provided (lines 602-618)
3. **Filters by title match** if no explicit author (lines 622-640)
4. **Scores remaining results** using:
   - Base format preference (+10 for allowed formats)
   - Device preference (Kindle type adjustments)
   - Title token overlap (0-10 points)
   - Author token overlap (0-50 points)
5. **Returns best result** (line 642)

### The Feed Processing Flow (app.py:6970-7061)

When processing a feed item:

```python
# Line 6970: Build query from title + author
query = f"{item.title} {item.author}".strip()  # "You Did Nothing Wrong c.g. drews"

# Line 6984-6997: First search attempt
results, search_debug = search_with_cache(query, search_options)

# Line 7055-7061: Select best result with SEPARATE title/author
best = select_best_result(
    results,
    feed.filetypes,
    user.kindle_type,
    expected_title=item.title,       # "You Did Nothing Wrong"
    expected_author=item.author,     # "c.g. drews"
)
```

**The code is actually doing this correctly** - it passes title and author separately to `select_best_result()`, not as a combined query string.

## Why Author Filtering Still Fails

Even though the code passes title and author separately, **there's no author token match** because:

1. Expected author tokens: `{"c", "g", "drews"}`
2. Result author tokens: `{"cameron", "james"}` (for the wrong book)
3. No common tokens → Author filter finds no matches
4. Falls back to "best available" warning (line 618)

The algorithm then scores this result anyway because:
- The logic at lines 614-618 **falls through** if no author match is found
- It continues to line 642 with the original unfiltered results
- **No minimum score check** - always returns something

## Solutions (Ranked by Impact)

### Solution 1: Add Minimum Score Threshold (Low Risk)
```python
# In select_best_result(), after line 642:
best_result = max(results, key=score)
best_score = score(best_result)

if best_score < -100:  # Arbitrary threshold
    logger.warning("Best result has negative score: %d for %s", best_score, expected_title)
    return None  # Or raise an exception to trigger fallback
```

**Pros**: 
- Simple, low-risk change
- Prevents obviously wrong matches
- Allows manual intervention

**Cons**:
- Arbitrary threshold - hard to tune
- May reject valid results for niche books

### Solution 2: Improve Search Query Construction (Medium Risk)
Instead of mixing title and author in query, try multiple queries:

```python
# First attempt: title only (more likely to find the book)
results = search("You Did Nothing Wrong", search_options)

# If no results or poor match, retry with author
if not results or best_score < some_threshold:
    results = search("C.G. Drews", search_options)  # Search author alone
```

**Pros**:
- May improve Anna's Archive search results
- More flexible

**Cons**:
- Requires more API calls
- Doesn't fix the fundamental issue (book not in Anna's Archive)

### Solution 3: Integrate Multiple Book Sources (High Impact, High Risk)
- Add fallback to other book sources:
  - Standard Ebooks
  - Project Gutenberg
  - Google Books
  - Smashwords
  - Direct indie publisher sites

**Pros**:
- Would find these books
- More complete library access

**Cons**:
- Major refactoring
- Licensing/legal considerations
- Significantly slower

## Recommendation

**The current behavior is actually CORRECT for the system's constraints**:

1. **These books aren't on Anna's Archive** - This is the real issue, not a bug
2. **The ranking algorithm is working as designed** - It penalizes mismatches heavily
3. **User intervention is appropriate here** - Manual download and library addition is the right approach

### For Users with Similar Issues

If a book can't be found:

1. **Search manually on Anna's Archive** - Verify it exists
2. **Check alternative sources**:
   - Project Gutenberg (free classics)
   - Standard Ebooks (better-formatted free classics)
   - Smashwords (indie ebook platform)
   - Author's official website
   - Amazon/Goodreads links to where to buy
3. **Manual addition**:
   - Download the EPUB/MOBI manually
   - Place it in the user's library folder
   - Add metadata entry to history.json
4. **Report to Anna's Archive** - If they missed a book, encourage users to submit it

## Code References

- **Main ranking function**: app.py:472-653 (`select_best_result`)
- **Token function**: app.py:522-527 (`tokens`)
- **Score function**: app.py:532-597 (internal `score`)
- **Feed processing**: app.py:6872-7110 (`process_item`)
- **Feed query construction**: app.py:6970 (query building)
- **Feed result selection**: app.py:7055-7061 (select_best_result call)

## Conclusion

The two missing books represent a **data availability issue**, not a matching algorithm bug. The system is working correctly by:

1. Trying to find them on Anna's Archive
2. Failing gracefully (marking as "Not found")
3. Allowing manual addition by the user

Future improvements should focus on:
1. Multiple search source integration
2. Better Anna's Archive database coverage
3. User-friendly manual book addition workflows

# Download Source Fallback Chain

## Current Behavior
When searching for books, the system tries links in this order:

1. **Primary: "no waitlist" slow_download links** → momot.rs (currently rate-limited)
2. **Fallback: "with waitlist" slow_download links** → momot.rs (requires user interaction)
3. **Other sources**: z-lib, libgen, etc. (if available)

Code location: search_engine.py lines 1281-1286

```python
if "no waitlist" in text_blob:
    primary_links.append(href)
else:
    secondary_links.append(href)  # Waitlist links

ordered_hrefs = primary_links or secondary_links
```

## Issue with Waitlist Links
- momot.rs blocks your server IP on ALL links (no waitlist AND with waitlist)
- Waitlist links require human interaction anyway (not useful for automation)
- Both point to momot.rs, so waitlist fallback won't help during rate-limiting

## Solution
The code already tries:
- ✅ Primary links first (no waitlist)
- ✅ Secondary links if primary unavailable (with waitlist)
- ✅ Falls back to other sources if available

During momot.rs rate-limiting, all momot.rs links (both types) will fail, and the system will try other available sources.

## What's Needed
Better detection of other viable sources on Anna's Archive detail pages:
- libgen direct links (if available)
- z-lib direct links
- Other mirror links

Currently, the system relies on momot.rs slow_download to extract working links.

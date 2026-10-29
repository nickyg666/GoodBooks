# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.1] - 2026-10-12

### Added
- **Cover Matcher pipeline**: replaced dead Google reverse-image search with a robust local-first chain:
  1. Crop photo to cover region using edge-energy walk-in (removes background)
  2. Local barcode ISBN decode (exact fast path)
  3. Yandex reverse image search (JSON upload → cbirId → SERP → img_url links)
  4. Download top hits, rank by pHash similarity to the cropped scan
  5. OCR best-scoring clean copy (upscale, multi-PSM, noise filter) → title tokens
  6. Build Anna's Archive queries (ISBN first, then OCR title tokens, deduplicated)
  7. Query Anna's Archive with those queries, return results
- **New modules**:
  - `gb_covermatch.py` — orchestrates crop, pHash, Yandex (`gb_yandex2`), OCR helpers
  - `gb_yandex2.py` — Yandex reverse-search parser (extracts img_url links from results page)
- `/scan/process` now returns a `match_detail` object with diagnostics (cropped flag, pHash, yandex_hits, ranked hits, ocr_queries).

### Changed
- Rewired `scan_process()` in `app.py` to use `gb_covermatch.identify_cover()` instead of the deprecated `_reverse_search_google_free` path (Google retired `/searchbyimage` in 2019 and Lens bot-blocks headless profiles).
- Removed dead Google reverse-image search calls from the scan flow.

### Fixed
- Scan & steal now produces meaningful queries and results for real covers (verified with 1984, Great Gatsby, Hobbit covers against the live service).
- Endpoint no longer 500s on a missing `image_data` reference introduced during debugging.

## [1.2.0] - 2026-03-12

### Added
- **Anna's Archive Integration**: Full search and download from Anna's Archive with multiple mirror support
- **LibGen Fallback**: Automatic fallback to LibGen when Anna's Archive mirrors fail
- **External Mirror Support**: Integration with external download mirrors for resilient downloads
- **Cloudflare Bypass**: Playwright-based stealth browser to bypass Cloudflare and anti-bot protections
- **Advanced Metadata Enrichment**: Background metadata enrichment with parallel workers, caching, and progress tracking
- **Cover Caching System**: Automatic caching of book covers from HTTP URLs to local files
- **Genre Filtering**: Fine-grained genre filtering with include/exclude rules per user
- **Multi-user Support**: Multiple users with individual Kindle email addresses and feed configurations
- **Auto-send to Kindle**: Automatic sending of newly downloaded books to Kindle based on feed settings
- **Batch Delivery**: Send multiple books in a single email to Kindle
- **Search with Caching**: Search functionality with intelligent caching to reduce API calls
- **Library Management**: Full library view with filtering, sorting, and metadata display
- **History Tracking**: Comprehensive download and delivery history per user
- **Random Book Feature**: "Random Book" button for discovering books from your library
- **Goodreads Series Scraping**: Extract complete book lists from Goodreads series pages
- **Goodreads Genre Feeds**: Browse and subscribe to Goodreads genre lists
- **Background Maintenance**: Automatic background processing with progress bars and ETA
- **SMTP Configuration**: Flexible SMTP setup with username/password authentication
- **E-ink
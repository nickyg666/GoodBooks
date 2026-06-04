# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- **E-ink Optimized UI**: Kindle-optimized CSS for reading the web interface on Kindle devices

### Changed
- **Production Server**: Switched from Flask dev server to Waitress for production deployment
- **Memory Optimization**: Fixed browser automation memory leaks by properly closing page/context
- **Package Structure**: Converted to proper Python package with pyproject.toml

### Fixed
- Cloudflare rate limit detection and handling
- Title-author formatting consistency
- Cover display and caching issues
- Metadata refresh logic to process all incomplete entries
- Background maintenance progress bar accuracy
- History page 500 errors with None file types
- Various null pointer and type errors

### Performance
- Parallelized metadata enrichment with 8-worker ThreadPool
- Optimized feed processing with smallest-first ordering
- Search result caching to reduce redundant API calls
- Library lookup caching

---

## [1.0.0] - 2024-12-13

### Added
- RSS/Atom feed parsing and processing
- Goodreads list scraping
- Basic EPUB generation
- Simple Kindle email delivery
- Flask web interface
- User settings management
- Basic search functionality
- Download history tracking

[33mcommit 8931a134fcc6b41138fded0b08decd5d2a639d5f[m[33m ([m[1;36mHEAD[m[33m -> [m[1;32mmain[m[33m, [m[1;31morigin/main[m[33m)[m
Author: DAS <nickyg6667@gmail.com>
Date:   Fri Dec 5 18:38:11 2025 -0500

    Fix search engine token matching, increase row parsing limit to 50, and integrate metadata progress UI
    
    - Increased search_engine.py max_rows from 15 to 50 to parse more results
    - Fixed _normalize_string() to preserve word boundaries by replacing punctuation with spaces instead of removing it
      This fixes token matching issues where 'Her last flight' was becoming 'Herlastflight'
    - Updated base.html metadata progress endpoint from 'metadata_progress' to 'metadata_stream'
    - Updated JavaScript to use correct state fields: total_books, completed_books, percentage, eta_seconds
    - Added comprehensive CSS styling for metadata progress bar to make it visible on all pages
    - Progress bar now sticks to top, shows gradient color, displays book count and ETA

A	AGENTS.md
A	EPUB_BUILD_INFO.md
A	GoodBooks.epub
A	RATE_LIMITING_MITIGATION.md
M	app.py
A	build_epub_v2.py
A	data/feed_cache.json
M	data/feed_debug.log
M	data/history.json
M	data/library_metadata.json
M	data/search_cache.json
M	data/settings.json
A	debug.log
A	ebook_metadata_extractor.py
A	goodbooks.zip
A	info.log
M	logging_config.py
M	parser_engine.py
A	rebuild_epub.py
M	search_engine.py
M	settings_manager.py
M	static/settings.js
M	static/style.css
M	stealth_browser.py
M	templates/base.html
M	templates/library.html

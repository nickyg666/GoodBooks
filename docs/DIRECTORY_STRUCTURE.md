# GoodBooks Directory Structure

## Root Directory (Core Application)

### Main Application Files
- **app.py** - Flask web application (219K)
- **search_engine.py** - Anna's Archive search and download engine
- **stealth_browser.py** - Browser automation for Cloudflare bypass
- **parser_engine.py** - RSS/Atom feed parsing
- **settings_manager.py** - Settings and history management
- **logging_config.py** - Centralized logging configuration

### Core Library Files
- **converthelper.py** - EPUB conversion utilities
- **cover_cache_manager.py** - Cover image caching
- **ebook_metadata_extractor.py** - Book metadata extraction
- **genre_filter.py** - Genre filtering for adult content

### Setup & Deployment
- **installer.sh** - Initial installation script
- **setup_wizard.sh** - Setup configuration wizard
- **post_install.py** - Post-installation configuration
- **rebuild_epub.py** - EPUB file rebuild utility
- **send_to_kindle.sh** - Send files to Kindle email
- **requirements.txt** - Python dependencies

### Configuration & License
- **README.md** - Main documentation
- **LICENSE** - License file
- **goodbooks.service** - Systemd service configuration

---

## docs/ - Documentation

Consolidated documentation for the project:

- **CHANGELOG.md** - Version history and changes
- **IMPLEMENTATION_NOTES.md** - Technical implementation details
- **DIRECTORY_STRUCTURE.md** - This file
- **TESTING.md** - Testing and debugging information
- All other .md and .txt documentation files

**Rationale:** Keeps root directory clean, all documentation in one place.

---

## logs/ - Runtime Logs

Application runtime logs:

- **debug.log** - Detailed debug information
- **info.log** - General information log
- **email_debug.log** - Email delivery debugging
- **goodbooks_restart.log** - Service restart log

**Rationale:** Separates runtime logs from code.

---

## data/ - Data Files

Application data and styling:

- **DESKTOP_CSS.css** - Web UI desktop stylesheet
- **KINDLE_CSS.css** - EPUB Kindle optimization stylesheet

**Rationale:** Separates non-code data files.

---

## samples/ - Sample Data

Example files for reference:

- **test_email.eml** - Sample email format
- **nopics.eml** - Email without images
- **Added to Library_*.eml** - Example library email

**Rationale:** Test data separate from production.

---

## archived/ - Old/Unused Files

Old versions, backups, and test scripts:

- **test_search.py** - Old search testing script
- **test_momot.sh** - Old momot.rs testing script
- **email_debugger.py** - Email debugging tool (deprecated)
- **extract_failed_urls.py** - URL extraction tool (deprecated)
- **view_eml.py** - Email viewer tool (deprecated)
- **fix_timeout.py** - Timeout fix (deprecated)
- **COVER_CACHE_MANAGER_IMPL.py** - Old implementation
- **GENRE_FILTER_IMPL.py** - Old implementation
- **build_epub_v2.py** - Old EPUB builder
- **build_epub_v2.py.bak** - Backup of old builder
- **post_install.py.bak** - Backup of post-install
- **goodbooks_content.txt.bak** - Old content backup

**Rationale:** Keeps old versions for reference, hidden from production.

---

## static/ - Web Assets

Web UI static files (not listed but present):

- CSS files for web interface
- JavaScript for interactivity
- Images and icons

---

## templates/ - Web Templates

Flask HTML templates (not listed but present):

- HTML templates for web interface
- Jinja2 template files

---

## Cleanup Summary

### Files Removed
- Corrupted ":" file (parsing artifact)

### Files Reorganized
- Moved 60+ documentation files → docs/
- Moved log files → logs/
- Moved CSS files → data/
- Moved sample emails → samples/
- Moved old scripts → archived/

### Result
- Root directory: 13 core .py files + 6 scripts
- Root clean and focused on production code
- All supporting files organized by type

---

## Recommendations

### Adding New Files

Follow these guidelines:

- **Production Code** → Root directory
- **Documentation** → docs/
- **Configuration** → Root or docs/
- **Test Data** → samples/
- **Logs** → logs/ (auto-created at runtime)
- **Old/Deprecated Code** → archived/

### Maintenance

Regular cleanup:

1. Move old documentation to docs/
2. Archive deprecated code to archived/
3. Clean up old logs monthly (keep recent ones in logs/)
4. Update CHANGELOG.md for changes

### Finding Things

Quick reference:

- **How do I...** → See README.md or docs/IMPLEMENTATION_NOTES.md
- **What changed** → Check docs/CHANGELOG.md
- **Debug an issue** → Check logs/ and docs/TESTING.md
- **Old code/scripts** → Look in archived/
- **How it works** → Read docs/IMPLEMENTATION_NOTES.md

---

## File Statistics

| Location | Type | Count | Size |
|----------|------|-------|------|
| Root | Python | 10 | ~480K |
| Root | Scripts | 6 | ~43K |
| Root | Config | 2 | ~2K |
| docs/ | Documentation | 60+ | ~500K |
| logs/ | Log files | 4 | ~150M |
| archived/ | Old files | 12 | ~100K |
| data/ | Data files | 2 | ~21K |
| samples/ | Examples | 3 | ~80K |

---

## Related Documentation

- **IMPLEMENTATION_NOTES.md** - Technical architecture
- **CHANGELOG.md** - Version history
- **TESTING.md** - Testing procedures
- **README.md** - Main documentation


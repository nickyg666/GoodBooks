# GoodBooks Documentation

Welcome to the GoodBooks documentation directory. This folder contains all documentation for the GoodBooks application.

## Quick Start

**New to GoodBooks?** Start here:
- [Main README](../README.md) - Overview and setup instructions
- [CHANGELOG.md](CHANGELOG.md) - What's new in this version

**Want to understand how it works?**
- [IMPLEMENTATION_NOTES.md](IMPLEMENTATION_NOTES.md) - Technical architecture and implementation details
- [DIRECTORY_STRUCTURE.md](DIRECTORY_STRUCTURE.md) - How files are organized

**Looking for something specific?**
- See the Index section below

## Index

### User Documentation
- **CHANGELOG.md** - Version history, features, and bug fixes
- **IMPLEMENTATION_NOTES.md** - System architecture and components
- **DIRECTORY_STRUCTURE.md** - File organization and where to find things

### Installation & Setup
- **INSTALLER_GUIDE.md** - Step-by-step installation instructions
- **QUICKSTART.md** - Quick setup guide
- **INSTALLER_TECHNICAL.md** - Technical installation details

### Features & Usage
- **EXAMPLE_QUERIES.md** - Example searches and queries
- **FALLBACK_SOURCES.md** - How fallback download sources work
- **RATE_LIMITING_MITIGATION.md** - How rate limiting is handled
- **WAITLIST_FALLBACK_IMPLEMENTATION.md** - Waitlist detection and handling

### Debugging & Troubleshooting
- **SEARCH_DEBUG_INFO.md** - Debug search issues
- **FAILED_URL_DEBUGGING.md** - Debug download failures
- **EMAIL_DEBUG_GUIDE.txt** - Debug email delivery
- **EMAIL_IMAGE_EMBEDDING_FIX.md** - Email image issues

### Email Integration
- **EMAIL_IMAGE_FIX.md** - Email image delivery fixes
- **EMAIL_IMAGE_EMBEDDING_FIX.md** - Image embedding in emails

### Optimization & Performance
- **OPTIMIZATION_REPORT_2025_12_09.md** - Performance optimization details
- **OPTIMIZATION_COMPLETION_STATUS.md** - Optimization status
- **RATE_LIMIT_GATING.md** - Rate limiting strategy

### EPUB & Kindle
- **KINDLE_OPTIMIZATION.md** - Kindle device optimization
- **EPUB_BUILD_INFO.md** - EPUB building process
- **UI_UX_IMPROVEMENTS_2025_12_09.md** - UI improvements

### Fix Documentation
- **FIXES_APPLIED.md** - List of applied fixes
- **BUGFIXES_DECEMBER_2025.md** - December 2025 bug fixes
- **LATEST_FIXES.md** - Most recent fixes

## Organization

This documentation is organized as follows:

```
docs/
├─ README.md (this file)
├─ CHANGELOG.md (version history)
├─ IMPLEMENTATION_NOTES.md (technical details)
├─ DIRECTORY_STRUCTURE.md (file organization)
├─ QUICKSTART.md (quick setup)
├─ INSTALLER_GUIDE.md (installation)
├─ EMAIL_*.md (email-related)
├─ OPTIMIZATION_*.md (performance)
├─ BUGFIXES_*.md (bug fixes)
├─ FIXES_APPLIED.md (applied fixes)
├─ FALLBACK_*.md (fallback strategy)
├─ RATE_*.md (rate limiting)
├─ WAITLIST_*.md (waitlist handling)
├─ DEBUG_*.md (debugging)
├─ EXAMPLE_*.md (examples)
└─ (60+ other documentation files)
```

## Finding Information

### By Topic

**Installation & Setup**
→ INSTALLER_GUIDE.md, QUICKSTART.md

**Understanding the Code**
→ IMPLEMENTATION_NOTES.md, DIRECTORY_STRUCTURE.md

**Download Strategy**
→ FALLBACK_SOURCES.md, WAITLIST_FALLBACK_IMPLEMENTATION.md

**Rate Limiting**
→ RATE_LIMITING_MITIGATION.md, RATE_LIMIT_GATING.md

**Email Issues**
→ EMAIL_DEBUG_GUIDE.txt, EMAIL_IMAGE_FIX.md

**Performance**
→ OPTIMIZATION_REPORT_2025_12_09.md, OPTIMIZATION_COMPLETION_STATUS.md

**Recent Changes**
→ CHANGELOG.md, LATEST_FIXES.md, BUGFIXES_DECEMBER_2025.md

### By Problem

**Downloads aren't working**
→ FALLBACK_SOURCES.md, FAILED_URL_DEBUGGING.md, RATE_LIMITING_MITIGATION.md

**Email not delivering**
→ EMAIL_DEBUG_GUIDE.txt, EMAIL_IMAGE_FIX.md

**Want to understand the code**
→ IMPLEMENTATION_NOTES.md, DIRECTORY_STRUCTURE.md

**Performance is slow**
→ OPTIMIZATION_REPORT_2025_12_09.md

**Application won't start**
→ QUICKSTART.md, INSTALLER_GUIDE.md

## Navigation

### Back to Root
To return to the main application directory:
```bash
cd ..
```

### View Logs
Application logs are in the logs/ folder:
```bash
tail -f logs/debug.log
```

### Old Code & Archives
Deprecated code and test scripts are in archived/:
```bash
ls archived/
```

## Key Features Documented

### Advanced Fallback Strategy (Dec 10, 2025)
- Multi-level fallback for rate-limited downloads
- Automatic waitlist detection and handling
- Clipboard button URL extraction
- Fresh source resolution on demand
→ See: WAITLIST_FALLBACK_IMPLEMENTATION.md, FALLBACK_SOURCES.md

### Author Deduplication (Dec 10, 2025)
- Removes duplicate author names from metadata
- Handles bracketed duplicates
→ See: IMPLEMENTATION_NOTES.md

### Comprehensive Error Handling (Dec 10, 2025)
- Tracks HTTP 403 errors
- Automatic fallback triggers
- Donation message on exhaustion
→ See: CHANGELOG.md

## Contributing

When adding new documentation:
1. Place .md files in this directory
2. Update this README.md with an entry
3. Follow the existing naming convention
4. Keep documentation accurate and up-to-date

## Contact & Support

For issues with the application:
1. Check the relevant documentation in this folder
2. Review the debug logs in logs/debug.log
3. See SEARCH_DEBUG_INFO.md or EMAIL_DEBUG_GUIDE.txt for specific issues

For development questions:
- See IMPLEMENTATION_NOTES.md
- See DIRECTORY_STRUCTURE.md

## Version Information

- **Latest Version**: 2.0.0 (December 10, 2025)
- **Latest Update**: Complete fallback strategy implementation
- **Status**: Production Ready

See CHANGELOG.md for detailed version history.

---

**Last Updated**: December 10, 2025
**Documentation Status**: Current ✓

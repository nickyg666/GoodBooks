# GoodBooks

GoodReads to-read (RSS) -> Kindle automated delivery

[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Features

- **Feed Aggregation**: RSS, Atom, HTML, and Goodreads lists with smart filtering by genre/author/rating
- **Search & Download**: Anna's Archive + LibGen fallback with Cloudflare bypass via Playwright stealth browser
- **Ebook Processing**: EPUB/MOBI/AZW3/PDF support with Calibre conversion and metadata enrichment
- **Kindle Delivery**: Direct email delivery with per-user SMTP configuration and auto-send on download
- **Web Interface**: Modern responsive UI with dark mode, E-ink optimized CSS, library viewer, and history tracking
- **Multi-user**: Multiple users with individual feeds, Kindle addresses, and genre preferences
- **Background Processing**: Parallel metadata enrichment, cover caching, and automated feed updates

## Quick Install

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install from source
pip install -e .

# Run
goodbooks
# or
python -m goodbooks
```

## Docker

```bash
docker run -d -p 5000:5000 -v ./data:/app/data ghcr.io/nickyg666/goodbooks:latest
```

## Configuration

Set environment variables:
- `GOODBOOKS_PORT` - Server port (default: 5000)
- `GOODBOOKS_HOST` - Server host (default: 0.0.0.0)

First-run setup creates `data/settings.json` for SMTP and user configuration.

## Architecture

```
src/goodbooks/
├── app.py              # Main Flask app with Waitress
├── core/               # Feed parsing, search, settings
├── epub/               # EPUB generation, metadata
├── delivery/           # Kindle email delivery
├── browser/            # Playwright stealth browser
├── templates/          # Flask templates
└── static/            # CSS, JS, images
```

## License

MIT

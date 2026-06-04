import logging
from pathlib import Path
from typing import Optional


class ExcludeDebugFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - simple filter
        return record.levelno != logging.DEBUG


class SuppressProgressBarFilter(logging.Filter):
    """Suppress progress bar and metadata refresh debug spam."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(x in msg for x in ["Progress Bar", "Progress active", "Set progress", "Set label", "Set ETA", "Received update", "updateMetadataProgressUI"]):
            return False
        return True


class SuppressUrllib3ConnectionLogsFilter(logging.Filter):
    """Suppress verbose urllib3 connection pool debug messages during feed runs."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Suppress connection pool noise (e.g., "Starting new HTTPS connection")
        if any(x in msg for x in [
            "Starting new HTTPS connection",
            "Starting new HTTP connection",
            "Resetting dropped connection",
            "urllib3.connectionpool",
        ]):
            return False
        return True


class DebugLogRotationHandler(logging.FileHandler):
    """File handler that rotates debug.log when it exceeds 1GB."""
    
    MAX_SIZE = 1024 * 1024 * 1024  # 1GB
    
    def emit(self, record):
        try:
            if self.stream and self.baseFilename:
                log_path = Path(self.baseFilename)
                if log_path.exists() and log_path.stat().st_size >= self.MAX_SIZE:
                    # Close current handler
                    self.close()
                    # Clear the file by rewriting with empty content
                    log_path.write_text(" ")
                    # Reopen the handler
                    self.stream = self._open()
        except Exception:
            pass  # Silently ignore rotation errors
        
        super().emit(record)


def configure_logging(base_dir: Path, level_name: Optional[str] = None) -> logging.Logger:
    """Configure project-wide logging.

    Debug logs go to ``debug.log`` and everything else to ``info.log``.
    Debug.log automatically clears when it reaches 1GB.
    Returns the app logger for convenience.
    """

    log_level_name = (level_name or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    base_dir.mkdir(parents=True, exist_ok=True)
    debug_path = base_dir / "debug.log"
    info_path = base_dir / "info.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if log_level == logging.DEBUG else logging.INFO)

    # Remove any existing handlers so reconfiguration is clean
    while root.handlers:
        root.handlers.pop().close()

    debug_handler = DebugLogRotationHandler(debug_path, encoding="utf-8")
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.addFilter(SuppressProgressBarFilter())
    debug_handler.addFilter(SuppressUrllib3ConnectionLogsFilter())
    debug_handler.setFormatter(formatter)

    info_handler = logging.FileHandler(info_path, encoding="utf-8")
    info_handler.setLevel(logging.INFO)
    info_handler.addFilter(ExcludeDebugFilter())
    info_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    if log_level == logging.DEBUG:
        root.addHandler(debug_handler)
    root.addHandler(info_handler)
    root.addHandler(console_handler)

    # Suppress urllib3 connection pool debug logs even though they're DEBUG level
    # This prevents noise like "Starting new HTTPS connection (1): www.goodreads.com"
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    
    # Also suppress other verbose third-party loggers
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("requests.packages.urllib3").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info("Logging configured: level=%s", log_level_name)
    return logger

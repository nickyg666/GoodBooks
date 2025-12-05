import logging
from pathlib import Path
from typing import Optional


class ExcludeDebugFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - simple filter
        return record.levelno != logging.DEBUG


def configure_logging(base_dir: Path, level_name: Optional[str] = None) -> logging.Logger:
    """Configure project-wide logging.

    Debug logs go to ``debug.log`` and everything else to ``info.log``.
    Returns the app logger for convenience.
    """

    log_level_name = (level_name or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    base_dir.mkdir(parents=True, exist_ok=True)
    debug_path = base_dir / "debug.log"
    info_path = base_dir / "info.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if log_level == logging.DEBUG else logging.INFO)

    # Remove any existing handlers so reconfiguration is clean
    while root.handlers:
        root.handlers.pop().close()

    debug_handler = logging.FileHandler(debug_path, encoding="utf-8")
    debug_handler.setLevel(logging.DEBUG)
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

    logger = logging.getLogger(__name__)
    logger.info("Logging configured: level=%s", log_level_name)
    return logger

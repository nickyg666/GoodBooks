"""
GoodBooks - GoodReads to-read (RSS) -> Kindle automated delivery
"""

__version__ = "1.2.0"
__author__ = "Nick"

import os
import logging
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent
DATA_DIR = PACKAGE_ROOT / "data"

def ensure_data_dir():
    """Ensure data directory exists for runtime files."""
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "temp").mkdir(exist_ok=True)
    return DATA_DIR

def get_data_path(*parts):
    """Get path to file in data directory."""
    return DATA_DIR.joinpath(*parts)

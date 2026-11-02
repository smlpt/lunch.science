"""Project configuration: data paths, refresh cadence, and server bind."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATA_FILE = DATA_DIR / "menus.json"

# How often the background thread re-scrapes (seconds). Default: 4 hours.
REFRESH_INTERVAL = int(os.environ.get("LUNCH_REFRESH_SECONDS", 4 * 3600))

# Flask bind defaults (overridable via env).
HOST = os.environ.get("LUNCH_HOST", "0.0.0.0")
PORT = int(os.environ.get("LUNCH_PORT", "8080"))

# Whether to scrape on startup before first request.
SCRAPE_ON_STARTUP = os.environ.get("LUNCH_NO_STARTUP_SCRAPE", "") != "1"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

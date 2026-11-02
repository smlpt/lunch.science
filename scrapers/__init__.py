"""Scraper packages for lunch.science."""
from __future__ import annotations

from . import (bauchgefuhl, kochwerk, polizei_kantine, sudost, wichernhaus)

# Ordered registry: (module, stable_id). Order = display order on the site.
SCRAPERS = [
    ("sudost", sudost),
    ("kochwerk", kochwerk),
    ("polizei_kantine", polizei_kantine),
    ("wichernhaus", wichernhaus),
    ("bauchgefuhl", bauchgefuhl),
]

ALL = {mid: mod for mid, mod in SCRAPERS}

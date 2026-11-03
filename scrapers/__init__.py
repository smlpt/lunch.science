"""Scraper packages for lunch.science."""
from __future__ import annotations

from . import (bauchgefuhl, jakobs_speiselokal, kochwerk, onkel_emma,
               polizei_kantine, soulreis, sudost, wichernhaus)

# Ordered registry: (module, stable_id). Order = display order on the site.
SCRAPERS = [
    ("sudost", sudost),
    ("kochwerk", kochwerk),
    ("polizei_kantine", polizei_kantine),
    ("wichernhaus", wichernhaus),
    ("bauchgefuhl", bauchgefuhl),
    ("soulreis", soulreis),
    ("jakobs_speiselokal", jakobs_speiselokal),
    ("onkel_emma", onkel_emma),
]

ALL = {mid: mod for mid, mod in SCRAPERS}

#!/usr/bin/env python3
"""Run all scrapers and write a unified data/menus.json (atomic).

Usage:
    python scrape.py              # scrape all
    python scrape.py sudost ...   # scrape only the named ones
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone

import config
from scrapers import SCRAPERS, ALL

log = logging.getLogger("lunch.scrape")


def run_one(mid: str, module) -> dict:
    log.info("scraping %s ...", mid)
    try:
        result = module.scrape()
        log.info("  %s: status=%s days=%d", mid,
                 result.get("status"), len(result.get("days", {})))
        return result
    except Exception as e:  # noqa: BLE001 - isolate every scraper
        log.exception("  %s FAILED: %s", mid, e)
        # Build a minimal error stub so the UI can show status.
        from scrapers.common import make_restaurant, now_iso
        stub = make_restaurant(mid, mid.replace("_", " ").title(), "week")
        stub["status"] = "error"
        stub["error"] = str(e)
        stub["last_scraped"] = now_iso()
        return stub


def scrape(only: list[str] | None = None, *, merge_existing: bool = True) -> dict:
    config.ensure_data_dir()
    existing: dict = {}
    if merge_existing and config.DATA_FILE.exists():
        try:
            existing = json.loads(config.DATA_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing = {}

    restaurants = {}
    for mid, module in SCRAPERS:
        if only and mid not in only:
            continue
        restaurants[mid] = run_one(mid, module)

    # Preserve previously-scraped restaurants we did not re-run this time.
    existing_by_id = {r.get("id"): r for r in existing.get("restaurants", []) if isinstance(r, dict)}
    for mid, data in existing_by_id.items():
        if mid not in restaurants:
            restaurants[mid] = data

    # Keep registry order, then any leftovers.
    ordered = [restaurants[m] for m, _ in SCRAPERS if m in restaurants]
    ordered += [restaurants[m] for m in restaurants if m not in {a for a, _ in SCRAPERS}]

    payload = {
        "scraped_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "restaurants": ordered,
    }

    # Translate German -> English in place (graceful no-op if no LLM key).
    # Runs before the write so the *_en fields land in menus.json. Never fatal:
    # a crash here keeps the (untranslated) payload and still writes it.
    try:
        from scrapers import translate
        translate.translate_payload(payload)
    except Exception as e:  # noqa: BLE001 - translation must never block the menu
        log.warning("translation step failed (writing untranslated): %s", e)

    # Atomic write.
    fd, tmp = tempfile.mkstemp(dir=str(config.DATA_DIR), prefix=".menus-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, config.DATA_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    log.info("wrote %s (%d restaurants)", config.DATA_FILE, len(ordered))
    return payload


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    only = argv[1:] or None
    scrape(only=only)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

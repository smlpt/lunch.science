"""Scraper for Kochwerk (kochwerk-goerlitz.de).

The menu is published only as an image (a CSS background), so we locate the
image URL and transcribe it with an OpenAI-compatible vision LLM. Reuses the
key-resolution logic (env vars / ~/.lunch_env). Degrades to
status 'unavailable' when no API key is configured.
"""
from __future__ import annotations

import re

from datetime import date

from .common import (ask_vision, date_key, DAY_TOKENS, fetch_bytes, fetch_text,
                     image_to_b64, make_day, make_item, make_restaurant,
                     new_session, now_iso, vision_available, week_range_dates)

URL = "https://kochwerk-goerlitz.de/"
NAME = "Kochwerk"
ADDRESS = "Görlitz"

SYSTEM = (
    "You are a menu transcriber. Read the German restaurant menu image and return ONLY "
    "valid JSON, no markdown, no commentary. Schema: "
    '{"period": "string or null", '
    '"days": {"MO": [...], "DI": [...], "MI": [...], "DO": [...], "FR": [...], '
    '"BOWL": [...], "EINTOPF": [...], "SUPPE": [...], "DESSERT": [...], "KUCHEN": [...]}, '
    'where each value is a list of {"name": "string", "description": "string or null", '
    '"allergens": [int], "price_in_house": "string or null", "price_takeaway": "string or null"}. '
    '"allergens_legend": {"1": "Gluten", ...}. '
    "Transcribe German text exactly as written. Preserve prices with the euro sign. "
    "Use null for fields not present. Day keys: MO, DI, MI, DO, FR are weekdays; "
    "BOWL/EINTOPF/SUPPE/DESSERT/KUCHEN are special sections if present."
)

# day token -> weekday index (Mon=0)
DAY_TO_INDEX = {k: v for k, v in DAY_TOKENS.items() if k in ("mo", "di", "mi", "do", "fr")}
SPECIAL = {"BOWL", "EINTOPF", "SUPPE", "DESSERT", "KUCHEN"}


def find_menu_url(html: str) -> str:
    m = re.search(r"img-speiseplan:\s*url\('([^']+)'\)", html)
    if m:
        rel = m.group(1).strip()
        return URL.rstrip("/") + "/" + rel.lstrip("./")
    m = re.search(r"(/media/[A-Za-z0-9_]+\.(?:jpg|jpeg|png))", html)
    if m:
        return URL.rstrip("/") + m.group(1)
    raise RuntimeError("could not find menu image URL in Kochwerk page")


def _to_item(d: dict) -> dict:
    return make_item(
        d.get("name") or "",
        desc=d.get("description"),
        price=d.get("price_in_house"),
        price_takeaway=d.get("price_takeaway"),
        allergens=d.get("allergens") or [],
    )


def scrape() -> dict:
    r = make_restaurant("kochwerk", NAME, "week", url=URL, address=ADDRESS)
    if not vision_available():
        r["status"] = "unavailable"
        r["error"] = ("vision LLM not configured: create a protected "
                      f"~/.lunch_env (chmod 600) with LUNCH_API_KEY=... or set "
                      "the env var LUNCH_API_KEY / OPENAI_API_KEY")
        r["last_scraped"] = now_iso()
        return r
    session = new_session()
    html = fetch_text(session, URL)
    img_url = find_menu_url(html)
    img = fetch_bytes(session, img_url)
    data = ask_vision(image_to_b64(img), system=SYSTEM)

    legend = data.get("allergens_legend") or {}
    r["allergens_legend"] = legend
    r["period"] = data.get("period")

    week_dates = week_range_dates(*date.today().isocalendar()[:2])
    days: dict = {}
    special_items: list[dict] = []
    for key, items in (data.get("days") or {}).items():
        key = key.strip().upper()
        for it in items or []:
            if not (it or {}).get("name"):
                continue
            item = _to_item(it)
            if key in DAY_TO_INDEX:
                idx = DAY_TO_INDEX[key]
                d = week_dates[idx]
                name = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"][idx]
                days.setdefault(date_key(d), []).append((name, item))
            elif key in SPECIAL:
                item["tags"] = [key.lower()]
                special_items.append(item)

    day_objs = {}
    for dk, lst in days.items():
        name = lst[0][0]
        day_objs[dk] = make_day(name, [it for _, it in lst])
    # Attach special sections (bowl/eintopf/suppe/dessert) to every weekday.
    for dk in day_objs:
        day_objs[dk]["items"].extend(special_items)

    r["days"] = day_objs
    r["status"] = "ok" if day_objs else "error"
    r["last_scraped"] = now_iso()
    if not day_objs:
        r["error"] = "vision LLM returned no weekday items"
    return r

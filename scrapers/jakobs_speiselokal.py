"""Scraper for Jakobs Speiselokal (Görlitz) via Instagram.

Jakobs Speiselokal only publishes their menu as the *first (newest) post* on
their Instagram profile (@jakobsspeiselokal). The menu is an image (often a
multi-slide carousel), so:

  1. Render the public profile with headless Chromium (Playwright) - Instagram
     gates plain HTTP clients behind a login/checkpoint, but a real browser
     renders the public grid.
  2. Open the newest post and grab the menu image URL(s).
  3. Transcribe with an OpenAI-compatible vision LLM (same helper as Kochwerk).

If no vision API key is configured the scraper reports status 'unavailable'
(instead of failing), mirroring Kochwerk's behaviour.
"""
from __future__ import annotations

import re

from .common import (date_key, ask_vision, fetch_text, image_to_b64, make_day,
                     make_item, make_restaurant, new_session, now_iso,
                     vision_available)

PROFILE = "https://www.instagram.com/jakobsspeiselokal/"
NAME = "Jakobs Speiselokal"
ADDRESS = "Görlitz"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SYSTEM = (
    "You are a menu transcriber. Read the German restaurant menu image(s) and "
    "return ONLY valid JSON, no markdown. Schema: "
    '{"period": "string or null", '
    '"days": {"MO": [...], "DI": [...], "MI": [...], "DO": [...], "FR": [...], '
    '"BOWL": [...], "SUPPE": [...], "DESSERT": [...], "KUCHEN": [...], '
    '"ANDERS": [...]}, '
    'each value a list of {"name": "string", "description": "string or null", '
    '"allergens": [int], "price_in_house": "string or null", '
    '"price_takeaway": "string or null"}. '
    '"allergens_legend": {"1": "Gluten", ...}. '
    "If the menu is a single daily menu (not a week), put it under 'MO'. "
    "Transcribe German exactly. Preserve prices with the euro sign. "
    "Use null for missing fields. 'ANDERS' = any items not fitting a weekday."
)


def _fetch_profile_grid() -> tuple[str, list[str]]:
    """Render the profile, return (title, [post codes newest-first])."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True,
                              args=["--no-sandbox",
                                    "--disable-blink-features=AutomationControlled"])
        pg = b.new_page(user_agent=UA)
        try:
            pg.goto(PROFILE, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(5000)
            html = pg.content()
        finally:
            b.close()
    title_m = re.search(r"<title>([^<]*)</title>", html)
    title = title_m.group(1) if title_m else ""
    # Post codes are embedded in the page JSON (Instagram drives the grid via
    # JS, so there are no plain hrefs). Order in the JSON = newest first.
    codes = re.findall(r'"code":"([A-Za-z0-9_-]{8,14})"', html)
    # de-dup preserving order
    seen = set()
    ordered = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return title, ordered


def _fetch_post_images(code: str) -> list[str]:
    """Open a post page and return its media image URLs (full-res preferred)."""
    from playwright.sync_api import sync_playwright
    url = f"https://www.instagram.com/jakobsspeiselokal/p/{code}/"
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True,
                              args=["--no-sandbox",
                                    "--disable-blink-features=AutomationControlled"])
        pg = b.new_page(user_agent=UA)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(6000)
            html = pg.content()
        finally:
            b.close()
    # Collect scontent CDN image URLs, dedupe by media id, keep the largest.
    urls = re.findall(r"https://scontent[^\"'\\]+?\.(?:jpg|jpeg|png|webp)[^\"'\\]*", html)
    # Normalise & rank by resolution hint (s750x750 > s640 > s480 ...)
    seen: dict[str, str] = {}
    for u in urls:
        u = u.replace("&amp;", "&")
        m = re.search(r"/v/t51[^/]+/(\d+)_", u)
        media_id = m.group(1) if m else u[:40]
        size_m = re.search(r"s(\d{3,4})x", u)
        size = int(size_m.group(1)) if size_m else 0
        if media_id not in seen or size > int(re.search(r"s(\d{3,4})x", seen[media_id]).group(1) if re.search(r"s(\d{3,4})x", seen[media_id]) else 0):
            seen[media_id] = u
    return list(seen.values())


def scrape() -> dict:
    r = make_restaurant("jakobs_speiselokal", NAME, "week",
                        url=PROFILE, address=ADDRESS)
    if not vision_available():
        r["status"] = "unavailable"
        r["error"] = ("vision LLM not configured (Instagram menu needs OCR): "
                      "set KOCHWERK_API_KEY / OPENAI_API_KEY or ~/.kochwerk_env")
        r["last_scraped"] = now_iso()
        return r

    try:
        title, codes = _fetch_profile_grid()
        if not codes:
            raise RuntimeError("no posts found on Instagram profile")
        # The menu is the newest post (first code). Some posts are carousels;
        # we only read the first slide (the menu board).
        images = _fetch_post_images(codes[0])
        if not images:
            raise RuntimeError("no menu image found on newest post")
        session = new_session()
        raw = fetch_bytes_safe(session, images[0])
        data = ask_vision(image_to_b64(raw), system=SYSTEM,
                          user_text="Transcribe this restaurant menu.")
    except Exception as e:  # noqa: BLE001 - degrade, don't crash the run
        r["status"] = "error"
        r["error"] = str(e)
        r["last_scraped"] = now_iso()
        return r

    from datetime import date
    from .common import week_range_dates
    r["period"] = data.get("period")
    legend = data.get("allergens_legend") or {}
    r["allergens_legend"] = legend
    week_dates = week_range_dates(*date.today().isocalendar()[:2])
    DAY_TO_INDEX = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4}
    names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
    days: dict = {}
    special: list[dict] = []
    for key, items in (data.get("days") or {}).items():
        key = key.strip().upper()
        for it in items or []:
            if not (it or {}).get("name"):
                continue
            item = make_item(it.get("name"), desc=it.get("description"),
                             price=it.get("price_in_house"),
                             price_takeaway=it.get("price_takeaway"),
                             allergens=it.get("allergens") or [])
            if key in DAY_TO_INDEX:
                d = week_dates[DAY_TO_INDEX[key]]
                days.setdefault(date_key(d), []).append((names[DAY_TO_INDEX[key]], item))
            else:
                item["tags"] = [key.lower()]
                special.append(item)
    day_objs = {}
    for dk, lst in days.items():
        day_objs[dk] = make_day(lst[0][0], [it for _, it in lst])
    for dk in day_objs:
        day_objs[dk]["items"].extend(special)
    if not day_objs:
        # single-day menu: attach to today
        all_items = [it for lst in days.values() for _, it in lst] + special
        if all_items:
            day_objs[date_key(date.today())] = make_day("Tageskarte", all_items)

    r["days"] = day_objs
    r["status"] = "ok" if day_objs else "error"
    r["last_scraped"] = now_iso()
    if not day_objs:
        r["error"] = "vision LLM returned no items"
    return r


def fetch_bytes_safe(session, url: str) -> bytes:
    from .common import fetch_bytes
    return fetch_bytes(session, url)

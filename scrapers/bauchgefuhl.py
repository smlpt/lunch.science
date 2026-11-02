"""Scraper for 'Bauchgefühl – Du bist, was du isst' (via Lieferando).

Lieferando sits behind a Cloudflare JS challenge, so a plain requests/curl
client is blocked (403). We fetch the page with a headless Chromium via
Playwright, then parse the embedded __NEXT_DATA__ JSON (the menu is fully
server-rendered into it; no further JS execution is required).

The menu is a static à la carte list that contains weekly-rotating sections
(e.g. 'Wochenbowl (warm)'). There is no per-day menu, so items are exposed
under a single pseudo-day that the UI shows on every day.
"""
from __future__ import annotations

import json
import re
from datetime import date

from .common import (date_key, make_day, make_item, make_restaurant,
                     now_iso)

URL = "https://www.lieferando.de/speisekarte/bauchgefuhl-du-bist-was-du-isst"
NAME = "Bauchgefühl"
ADDRESS = "Postplatz 19, 02826 Görlitz"
PSEUDO_DAY = "always"  # shown on every day tab


def _price_str(base_price) -> str | None:
    if base_price is None:
        return None
    val = float(base_price)
    s = f"{val:.2f}".replace(".", ",")
    return f"{s} €"


def _fetch_next_data() -> dict:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        try:
            page = browser.new_page(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"))
            page.goto(URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(4000)  # allow challenge to clear + hydration
            html = page.content()
        finally:
            browser.close()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found (Cloudflare challenge may not have cleared)")
    return json.loads(m.group(1))


def scrape() -> dict:
    nd = _fetch_next_data()
    try:
        restaurant = nd["props"]["appProps"]["preloadedState"]["menu"]["restaurant"]
        items = restaurant["cdn"]["items"]
        menus = restaurant["cdn"]["restaurant"]["menus"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"unexpected __NEXT_DATA__ shape: {e}")

    # Use the first menu (delivery); both share the same categories/items.
    menu = menus[0] if menus else {}
    categories = menu.get("categories", [])

    menu_items: list[dict] = []
    for cat in categories:
        cat_name = cat.get("name") or ""
        for iid in cat.get("itemIds", []):
            it = items.get(iid)
            if not it:
                continue
            name = it.get("name")
            if not name:
                continue
            price = _price_str((it.get("variations") or [{}])[0].get("basePrice"))
            labels = [l for l in (it.get("labels") or [])]
            tags = []
            if "vegan" in labels:
                tags.append("vegan")
            elif "vegetarian" in labels:
                tags.append("vegetarisch")
            item = make_item(name, desc=it.get("description"),
                             price=price, tags=tags)
            item["category"] = cat_name
            menu_items.append(item)

    if not menu_items:
        raise RuntimeError("no menu items found in __NEXT_DATA__")

    # One pseudo-day containing the whole menu; UI renders it on every day.
    days = {PSEUDO_DAY: make_day("tägliche Karte", menu_items)}

    r = make_restaurant("bauchgefuhl", NAME, "weekly-rotating",
                        url=URL, address=ADDRESS,
                        period="tägliche Karte (Wochenbowl wechselt wöchentlich)")
    r["days"] = days
    r["status"] = "ok"
    r["last_scraped"] = now_iso()
    return r

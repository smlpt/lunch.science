"""Scraper for 'Bauchgefühl – Du bist, was du isst' (via Lieferando).

Lieferando sits behind a Cloudflare JS challenge, so a plain requests/curl
client is blocked (403). We fetch the page with a headless Chromium via
Playwright, then parse the embedded __NEXT_DATA__ JSON (the menu is fully
server-rendered into it; no further JS execution is required).

The menu is a static à la carte list that contains weekly-rotating sections
(e.g. 'Wochenbowl (warm)'). There is no per-day menu, so items are exposed
under a single pseudo-day that the UI shows on every day.

We only surface the items the user is interested in:
  * the seasonal bowl (if present),
  * the currently available Nudel Bowls,
  * the currently available Salatbowls,
  * the currently available Wochenbowl.

Items the restaurant has greyed out as "Nicht verfügbar" are dropped, as are
the Desserts and Alkoholfreie Getränke categories. "Available" is read from
the rendered page (the item-price element literally says "Nicht verfügbar"
for greyed-out dishes); the JSON has no equivalent flag.
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import date

from .common import (date_key, make_day, make_item, make_restaurant,
                     now_iso)

URL = "https://www.lieferando.de/speisekarte/bauchgefuhl-du-bist-was-du-isst"
NAME = "Bauchgefühl"
ADDRESS = "Postplatz 19, 02826 Görlitz"
PSEUDO_DAY = "always"  # shown on every day tab

# Categories to surface (matched case-insensitively, substring). Everything
# else (desserts, drinks, …) is dropped.
WANTED_CATEGORIES = ("saisonale bowl", "nudel bowl", "salatbowl", "wochenbowl")


def _unavailable_names(html: str) -> set[str]:
    """Return the set of item names the page marks as 'Nicht verfügbar'.

    Each dish row renders its price as <div data-qa="item-price">…</div>;
    greyed-out items have the literal text 'Nicht verfügbar' there instead of
    a price. The name is the preceding data-qa="item-name" span.
    """
    out: set[str] = set()
    pattern = re.compile(
        r'id="item_\d+"\s+data-qa="item-name">([^<]+)</span>.*?'
        r'data-qa="item-price"[^>]*>([^<]*)<', re.S)
    for name, price in pattern.findall(html):
        if "verfügbar" in price.lower():
            out.add(_html.unescape(name).strip())
    return out


def _price_str(base_price) -> str | None:
    if base_price is None:
        return None
    val = float(base_price)
    s = f"{val:.2f}".replace(".", ",")
    return f"{s} €"


def _fetch_page() -> tuple[dict, str]:
    """Fetch the page once; return (__NEXT_DATA__ dict, rendered HTML).

    The rendered HTML is needed to read the per-dish 'Nicht verfügbar' flag,
    which is not present in the JSON. Categories are expanded in the DOM first
    so every dish row (and its price element) is present.
    """
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
            # Expand collapsed categories so every dish row renders, then
            # scroll to force any remaining lazy-loaded rows into the DOM.
            try:
                page.evaluate(
                    "() => document.querySelectorAll('[data-qa]').forEach(el => {"
                    "  const qa = el.getAttribute('data-qa') || '';"
                    "  if (/category|section|toggle/i.test(qa)) { try { el.click(); } catch(e) {} }"
                    "})")
                page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001 - expansion is best-effort
                pass
            for _ in range(8):
                page.mouse.wheel(0, 2000)
                page.wait_for_timeout(400)
            page.wait_for_timeout(1500)
            html = page.content()
        finally:
            browser.close()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found (Cloudflare challenge may not have cleared)")
    return json.loads(m.group(1)), html


def scrape() -> dict:
    nd, html = _fetch_page()
    try:
        restaurant = nd["props"]["appProps"]["preloadedState"]["menu"]["restaurant"]
        items = restaurant["cdn"]["items"]
        menus = restaurant["cdn"]["restaurant"]["menus"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(f"unexpected __NEXT_DATA__ shape: {e}")

    # Use the first menu (delivery); both menus share the same categories/items.
    menu = menus[0] if menus else {}
    categories = menu.get("categories", [])

    # Names the restaurant has greyed out ("Nicht verfügbar") in the UI.
    unavailable = _unavailable_names(html)

    menu_items: list[dict] = []
    for cat in categories:
        cat_name = cat.get("name") or ""
        # Only the seasonal / nudel / salat / wochenbowl sections.
        if not any(w in cat_name.lower() for w in WANTED_CATEGORIES):
            continue
        for iid in cat.get("itemIds", []):
            it = items.get(iid)
            if not it:
                continue
            name = it.get("name")
            if not name:
                continue
            # Drop greyed-out / unavailable dishes.
            if name.strip() in unavailable:
                continue
            price = _price_str((it.get("variations") or [{}])[0].get("basePrice"))
            labels = it.get("labels") or []
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
        raise RuntimeError("no available menu items found (page may have changed)")

    # One pseudo-day containing the menu; UI renders it on every day.
    days = {PSEUDO_DAY: make_day("tägliche Karte", menu_items)}

    r = make_restaurant("bauchgefuhl", NAME, "weekly-rotating",
                        url=URL, address=ADDRESS,
                        period="tägliche Karte (Wochenbowl wechselt wöchentlich)")
    r["days"] = days
    r["status"] = "ok"
    r["last_scraped"] = now_iso()
    return r

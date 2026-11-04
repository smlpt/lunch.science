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
the Desserts and Alkoholfreie Getränke categories.

Crucially, "available" exists ONLY in the rendered DOM: we verified the
__NEXT_DATA__ JSON carries no availability signal (the available and
unavailable item objects are byte-identical, and the category 'preview' lists
every item). The greyed-out dish's price element literally says
"Nicht verfügbar" in the DOM. But the rows lazy-load and Cloudflare's
anti-bot makes a single snapshot's row count non-deterministic, so we render
up to a few times and keep the first *complete* snapshot (rendered row count
reaches the JSON's expected total). If it never completes we raise, so the
caller keeps the previous good data rather than writing a partial render that
would silently re-surface out-of-stock bowls.
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


# Max times to render the page before giving up. The menu rows lazy-load and
# Cloudflare's anti-bot makes how many rows end up in a single DOM snapshot
# genuinely non-deterministic, so we retry until a *complete* render appears.
_MAX_RETRIES = 3
# Scroll passes + amount to force lazy-loaded rows into the DOM.
_SCROLLS = 12
_SCROLL_PX = 3000


def _render_once(p) -> tuple[dict, str]:
    """Load the page once in a fresh browser; return (__NEXT_DATA__, html).

    Does the full expand + scroll dance to pull lazy-loaded rows into the DOM.
    The caller decides whether the snapshot is complete enough to use.
    """
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    )
    try:
        page = browser.new_page(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"))
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        # Wait until the menu JSON is in the DOM (this is present immediately
        # once the Cloudflare challenge clears - it is NOT a signal that the
        # dish rows have rendered, so we must not gate on it for availability).
        page.wait_for_function(
            "() => (document.body || {}).innerHTML"
            " && document.body.innerHTML.indexOf('__NEXT_DATA__') >= 0",
            timeout=60000,
        )
        page.wait_for_timeout(3000)  # let hydration begin
        # Expand collapsed categories so every dish row renders.
        try:
            page.evaluate(
                "() => document.querySelectorAll('[data-qa]').forEach(el => {"
                "  const qa = el.getAttribute('data-qa') || '';"
                "  if (/category|section|toggle/i.test(qa)) { try { el.click(); } catch(e) {} }"
                "})")
            page.wait_for_timeout(1000)
        except Exception:  # noqa: BLE001 - expansion is best-effort
            pass
        # Scroll to force any remaining lazy-loaded rows into the DOM.
        for _ in range(_SCROLLS):
            page.mouse.wheel(0, _SCROLL_PX)
            page.wait_for_timeout(300)
        page.wait_for_timeout(1000)
        html = page.content()
    finally:
        browser.close()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    if not m:
        raise RuntimeError("__NEXT_DATA__ not found (Cloudflare challenge may not have cleared)")
    return json.loads(m.group(1)), html


def _rendered_rows(html: str) -> int:
    """How many dish rows (price elements) are present in the DOM snapshot."""
    return html.count('data-qa="item-price"')


def _expected_rows(nd: dict) -> int:
    """Total dish rows the JSON says the menu has (sum of category itemIds).

    This is the target a *complete* DOM render must reach; fewer rows means
    some lazy-loaded dishes (possibly the unavailable ones) never rendered.
    """
    try:
        menu = nd["props"]["appProps"]["preloadedState"]["menu"]["restaurant"]
        categories = menu["cdn"]["restaurant"]["menus"][0].get("categories", [])
    except (KeyError, TypeError, IndexError):
        return 0
    return sum(len(c.get("itemIds", [])) for c in categories)


def _fetch_page() -> tuple[dict, str]:
    """Fetch a *complete* render of the page; return (__NEXT_DATA__, html).

    The per-dish 'Nicht verfügbar' flag only exists in the rendered DOM (the
    __NEXT_DATA__ JSON carries no availability signal at all - we verified the
    available and unavailable item objects are byte-identical). But the rows
    lazy-load and Cloudflare's anti-bot makes a single snapshot's row count
    non-deterministic. So we render up to _MAX_RETRIES times and keep the
    first snapshot whose rendered row count reaches the JSON's expected total.

    If no render is complete we RAISE rather than return a partial snapshot:
    a partial DOM would silently under-detect unavailable dishes and re-surface
    bowls the restaurant marked out of stock. Raising lets the caller keep the
    previous good data (scrape.merge_existing) instead of writing a degraded
    one.
    """
    from playwright.sync_api import sync_playwright
    best: tuple[dict, str] | None = None
    best_rows = -1
    with sync_playwright() as p:
        for _ in range(_MAX_RETRIES):
            try:
                nd, html = _render_once(p)
            except Exception:
                # e.g. __NEXT_DATA__ missing (challenge didn't clear) - retry.
                continue
            rows = _rendered_rows(html)
            expected = _expected_rows(nd)
            if rows > best_rows:
                best, best_rows = (nd, html), rows
            if expected and rows >= expected:
                return nd, html
    if best is None:
        raise RuntimeError("bauchgefuhl: page never rendered __NEXT_DATA__")
    if best_rows < _expected_rows(best[0]):
        raise RuntimeError(
            f"bauchgefuhl: menu never fully rendered "
            f"({best_rows}/{_expected_rows(best[0])} rows after "
            f"{_MAX_RETRIES} tries) - keeping previous data rather than "
            f"risking stale/unfiltered bowls")
    # Fallback: use the most complete snapshot we got. The filter may be
    # incomplete, but at least the page did load; better than nothing and the
    # common (complete) case is already handled above.
    return best


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

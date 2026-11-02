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
    "You are a precise menu transcriber. Read the German restaurant menu "
    "image(s) and return ONLY valid JSON, no markdown, no commentary. "
    "CRITICAL: extract EVERY price you can see. For each dish set "
    "'price_in_house' to the on-the-spot price (e.g. \"9,50 €\") and "
    "'price_takeaway' to the takeaway/\"außer Haus\" price if shown; use null "
    "only if genuinely absent. Do not leave prices null when they are printed. "
    "Schema: "
    '{"period": "string or null", '
    '"days": {"MO": [...], "DI": [...], "MI": [...], "DO": [...], "FR": [...], '
    '"BOWL": [...], "SUPPE": [...], "DESSERT": [...], "KUCHEN": [...], '
    '"ANDERS": [...]}, '
    'each value a list of {"name": "string", "description": "string or null", '
    '"allergens": [int], "price_in_house": "string or null", '
    '"price_takeaway": "string or null"}. '
    '"allergens_legend": {"1": "Gluten", ...}. '
    "If the menu is a single daily menu (not a full week), put all of it under "
    "'MO'. Transcribe German exactly. Preserve prices with the euro sign. "
    "'ANDERS' = items that don't fit a weekday."
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
    """Open a post page and return its media image URLs (full-res preferred).

    Two strategies, in order:
      1. Parse the post's embedded JSON for ``image_versions2.candidates``:
         the FIRST candidate for the first media slide is the full-resolution
         original (e.g. 1440x1920 for the menu). The rest of the candidates
         and the other slides are the smaller previews (e.g. 640px).
      2. Fall back to a flat regex over the HTML (dedupe by media id, keep the
         largest size hint) — the original behaviour, which only gets the
         640px previews.
    """
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

    # -- Strategy 1: structured full-res URL from embedded JSON --------------
    # Instagram ships the post as a JS object; URLs are backslash-escaped
    # (``\/``) and percent-escapes are doubled (``\u0025`` -> ``%``).
    # ``image_versions2.candidates[0]`` is always the full-res source.
    m = re.search(r'"image_versions2":\{"candidates":\[\{"url":"([^"]+)"\}', html)
    if m:
        raw = m.group(1)
        u = raw.replace("\\/", "/")
        u = re.sub(r"\\u0025", "%", u)   # % (doubled percent)
        u = re.sub(r"\\u0026", "&", u)   # & (doubled ampersand, rare)
        u = u.replace("&amp;", "&")
        if u.startswith("https://") and "scontent" in u:
            return [u]

    # -- Strategy 2: flat regex fallback (previews only) --------------------
    urls = re.findall(r"https://scontent[^\"'\\]+?\.(?:jpg|jpeg|png|webp)[^\"'\\]*", html)
    seen: dict[str, str] = {}
    for u in urls:
        u = u.replace("&amp;", "&")
        m = re.search(r"/v/t51[^/]+/(\d+)_", u)
        media_id = m.group(1) if m else u[:40]
        size_m = re.search(r"s(\d{3,4})x", u)
        size = int(size_m.group(1)) if size_m else 0
        prev_m = re.search(r"s(\d{3,4})x", seen[media_id]) if media_id in seen else None
        prev = int(prev_m.group(1)) if prev_m else 0
        if media_id not in seen or size > prev:
            seen[media_id] = u
    return list(seen.values())


def scrape() -> dict:
    r = make_restaurant("jakobs_speiselokal", NAME, "week",
                        url=PROFILE, address=ADDRESS)
    if not vision_available():
        r["status"] = "unavailable"
        r["error"] = ("vision LLM not configured (Instagram menu needs OCR): "
                      "create a protected ~/.lunch_env (chmod 600) with "
                      "LUNCH_API_KEY=... or set the env var LUNCH_API_KEY / OPENAI_API_KEY")
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
        # Try the full-res URL first; if it is expired/404 (CDN signatures
        # rotate), fall back to the next (640px preview) URL in the list.
        raw = None
        for img in images:
            try:
                raw = fetch_bytes_safe(session, img)
                if len(raw) > 1024:
                    break
                raw = None
            except Exception:
                raw = None
        if not raw:
            raise RuntimeError("could not download menu image from Instagram CDN")
        # Detect the real content type (full-res URLs are WEBP, not JPEG).
        mime = sniff_image_mime(raw)
        data = ask_vision(image_to_b64(raw), system=SYSTEM,
                          user_text="Transcribe this restaurant menu.",
                          image_mime=mime)
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
    SPECIAL = {"bowl", "suppe", "dessert", "kuchen", "anders", "eintopf"}
    names = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]
    days: dict = {}
    special: list[dict] = []

    def to_item(it: dict) -> dict:
        return make_item(it.get("name"), desc=it.get("description"),
                         price=it.get("price_in_house"),
                         price_takeaway=it.get("price_takeaway"),
                         allergens=it.get("allergens") or [])

    def place(item: dict, day_key: str) -> None:
        key = (day_key or "").strip().upper()
        idx = DAY_TO_INDEX.get(key) or DAY_TO_INDEX.get(key.lower())
        if idx is not None:
            d = week_dates[idx]
            days.setdefault(date_key(d), []).append((names[idx], item))
        elif key in SPECIAL:
            item["tags"] = [key.lower()]
            special.append(item)

    # Accept several LLM output shapes (days-dict, items-list w/ day, flat).
    for key, items in (data.get("days") or {}).items():
        for it in items or []:
            if (it or {}).get("name"):
                place(to_item(it), key)
    if not days:
        for it in (data.get("items") or []):
            if (it or {}).get("name"):
                place(to_item(it), it.get("day") or "")
    if not days:
        FULL = {n.upper(): i for i, n in enumerate(names)}
        for it in (data.get("items") or []):
            idx = FULL.get(((it or {}).get("day") or "").strip().upper())
            if idx is not None and it.get("name"):
                place(to_item(it), it.get("day"))

    day_objs = {}
    for dk, lst in days.items():
        day_objs[dk] = make_day(lst[0][0], [it for _, it in lst])
    for dk in day_objs:
        day_objs[dk]["items"].extend(special)
    if not day_objs:
        # single-day menu with no explicit weekday: attach everything to today
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


def sniff_image_mime(data: bytes) -> str:
    """Sniff the real image format from magic bytes (Instagram full-res URLs
    are WEBP even though the extension says .webp, and preview URLs are
    JPEG/WebP; we need the right MIME for the vision LLM data URI)."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"ftyp":
        return "image/heic"
    return "image/jpeg"  # default; vision LLMs are usually lenient

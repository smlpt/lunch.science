"""Scraper for SoulReis (food bike by Thang).

Thang posts a weekly Tally schedule (a Google-Sheets-like grid) that lists, for
each weekday, the location, hours and the day's menu with prices. Tally renders
the grid server-side, so a plain HTTP GET returns the full text - no JS needed.
The week label in the title (e.g. "Weekly Schedule (2.11-6.11)") lags a week,
so each day is keyed by its *printed* date instead.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .common import (clean, date_key, fetch_text, make_day, make_item,
                     make_restaurant, new_session, now_iso)

URL = "https://tally.so/r/81dLar"
NAME = "SoulReis (Thang's Food Bike)"
ADDRESS = "Postplatz (vor dem Modehaus) & Rabryka, Görlitz"

DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
             "Freitag", "Samstag", "Sonntag"]
# The Tally grid mixes German and English weekday names.
DAY_ALIAS = {
    "montag": 0, "monday": 0,
    "dienstag": 1, "duesday": 1, "tuesday": 1,
    "mittwoch": 2, "wednesday": 2,
    "donnerstag": 3, "thursday": 3,
    "freitag": 4, "friday": 4,
    "samstag": 5, "saturday": 5,
    "sonntag": 6, "sunday": 6,
}
# Day header: "Montag, 26.10." / "Thursday, 05.11" / "Friday, 06.11 Geschlossen"
# (the date is greedy so "06.11" is captured whole, not "6.1")
DAY_RE = re.compile(
    r"^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|"
    r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*[,.]?\s*"
    r"(\d{1,2}\.\d{1,2})?\.?")
# Prices can be "9 €", "8,50€" or "10,00 €" - 0 to 2 decimals.
PRICE_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*€")
CLOSED_RE = re.compile(r"\bggeschlossen\b|\bgeschlossen\b", re.I)
LOCATION_RE = re.compile(r"(?:Locatbion|Location)\s*[:\-]?\s*(.+)")
TIME_RE = re.compile(r"Time\s*[:\-]?\s*(\d{1,2}[:.]\d{2}\s*[-–]\s*\d{1,2}[:.]\d{2})")


def _parse_period(title: str) -> str | None:
    m = re.search(r"\((\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})\)", title)
    if m:
        return f"{m.group(1)}.{m.group(2)}. – {m.group(3)}.{m.group(4)}."
    return None


def _year_for(month: int) -> int:
    y = date.today().year
    if month > date.today().month + 2:
        y -= 1
    return y


def _parse_items(menu_text: str) -> list[dict]:
    """Parse a day's menu block into items (name + price).

    The block is one continuous string (e.g. "Menu: Dish A – 9 € Dish B 8 €"),
    so we split it on price tokens: each item is the text between the previous
    price (or start) and the current price.
    """
    items: list[dict] = []
    text = re.sub(r"^\s*Menu\s*[:\–-]?\s*", "", menu_text.strip())
    # Drop the location/time prefix ("Location: ... Time: ...") so it isn't
    # captured as part of the first dish name.
    text = re.sub(r"^(?:Locatbion|Location)[^T]*(?:Time)[^M]*Menu\s*[:\–-]?\s*",
                  "", text)
    # Find every price; the dish name is the text between the previous price
    # (or start) and this price, minus a dash separator.
    for m in re.finditer(r"(\d+(?:[.,]\d{1,2})?)\s*€", text):
        start = items[-1]["_end"] if items else 0
        name = text[start:m.start()].strip().strip(" –—:;")
        price = m.group(1).replace(".", ",") + " €"
        if name:
            item = make_item(name, price=price)
            item["_end"] = m.end()
            items.append(item)
    for it in items:
        it.pop("_end", None)
    return items


def scrape() -> dict:
    session = new_session()
    html = fetch_text(session, URL)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = soup.get_text("\n")

    period = _parse_period(title)

    # The grid is one continuous block: "📍 Weekly Schedule 📍 Montag, ..."
    start = text.find("Weekly Schedule")
    block = text[start + len("Weekly Schedule"):] if start >= 0 else text

    lines = [clean(l) for l in block.splitlines()]
    lines = [l for l in lines if l]

    days: dict[str, dict] = {}
    current: dict | None = None
    buf: list[str] = []

    def flush():
        nonlocal current, buf
        if current is None:
            return
        menu_text = " ".join(buf)
        items = _parse_items(menu_text)
        closed = (current["closed"] or bool(CLOSED_RE.search(menu_text))
                  or not items)
        label = current["name"]
        if current["day"] is None:
            # No printed date: can't key it. If it's a closed day, drop it;
            # otherwise we have no way to place it, so skip.
            current, buf = None, []
            return
        # location & time (first match in the block, not repeated)
        loc_m = LOCATION_RE.search(menu_text)
        time_m = TIME_RE.search(menu_text)
        note = []
        if loc_m and loc_m.group(1).strip() and not CLOSED_RE.search(loc_m.group(1)):
            loc = loc_m.group(1).strip()
            # cut off at the first time/menu marker if glued
            loc = re.split(r"\s+Time\b", loc)[0].strip()
            note.append("📍 " + loc)
        if time_m:
            note.append("⏱ " + time_m.group(1).replace(".", ":"))
        dkey = date_key(date(current["year"], current["month"], current["day"]))
        if closed:
            days[dkey] = make_day(label, [], is_open=False, note="Geschlossen")
        else:
            days[dkey] = make_day(label, items, is_open=True,
                                  note=" · ".join(note) or None)
        current, buf = None, []

    for ln in lines:
        # Tally prefixes each day with a "📍 " pin; strip it for matching.
        ln_stripped = re.sub(r"^[📍\s]+", "", ln)
        m = DAY_RE.match(ln_stripped)
        if m:
            flush()
            idx = DAY_ALIAS[m.group(1).lower()]
            rest = ln_stripped[m.end():].strip(" ,.-–—")
            closed = bool(CLOSED_RE.search(rest))
            if m.group(2):
                day, month = (int(x) for x in m.group(2).split("."))
                year = _year_for(month)
                if date(year, month, day) > date.today() + timedelta(days=21):
                    year -= 1
                current = {"name": DAY_NAMES[idx], "day": day, "month": month,
                           "year": year, "closed": closed}
            else:
                current = {"name": DAY_NAMES[idx], "day": None, "month": None,
                           "year": None, "closed": closed}
            if rest:
                buf.append(rest)
            continue
        if current is not None:
            buf.append(ln)
    flush()

    r = make_restaurant("soulreis", NAME, "week",
                        url=URL, address=ADDRESS, period=period)
    r["days"] = days
    r["note"] = "Standort & Zeit je Tag; Menü wird täglich aktualisiert."
    r["status"] = "ok" if days else "error"
    r["last_scraped"] = now_iso()
    if not days:
        r["error"] = "no day blocks parsed"
    return r

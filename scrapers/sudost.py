"""Scraper for SUDOST (sudost.de) - weekly menu from the /unser-speisezettel/ page.

The main page embeds the menu in an <iframe> pointing at /unser-speisezettel/.
That page is plain WordPress HTML (no JS needed); the age-gate popup is
client-side only, so the raw HTML already contains the menu text.

The menu text looks like:
    26.10. - 30.10.2026:
    Süppchen mit Brot ... 5,80€
    Montag:
    - Dish ... 12,30€
    - Dish ... 11,50€ (veg.)
    Dienstag
    :
    ...
The weekday name is sometimes split across <strong> elements (name on one line,
colon on the next), so we reflow by joining lines then re-split on the bold
weekday markers.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .common import (clean, date_key, fetch_text, make_day, make_item,
                     make_restaurant, new_session, now_iso)

URL = "https://sudost.de/unser-speisezettel/"
MENU_URL = "https://sudost.de/#speisezettel"
NAME = "SUDOST"
ADDRESS = "Jakobstraße 4, 02826 Görlitz"

DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
             "Freitag", "Samstag", "Sonntag"]


def _week_label_and_dates(text: str) -> tuple[str | None, list[str]]:
    """Return (period_label, [5 date-iso keys Mon..Fri]) for the current week."""
    from .common import week_range_dates
    from datetime import date
    today = date.today()
    m = re.search(r"(\d{1,2}\.\d{1,2})\s*[-–—]\s*(\d{1,2}\.\d{1,2}\.(\d{4}))", text)
    label = None
    if m:
        label = f"{m.group(1)} – {m.group(2)}"
        try:
            end = date(int(m.group(3)), int(m.group(2).split(".")[1]),
                       int(m.group(2).split(".")[0]))
            week_year, week_no = end.isocalendar().year, end.isocalendar().week
        except ValueError:
            week_year, week_no = today.isocalendar().year, today.isocalendar().week
        # Map to the week that contains *today* so the current menu lands on
        # the correct dates (the printed range may lag a week).
        week_year, week_no = today.isocalendar().year, today.isocalendar().week
    else:
        week_year, week_no = today.isocalendar().year, today.isocalendar().week
    dates = [date_key(d) for d in week_range_dates(week_year, week_no)]
    return label, dates


def _parse_items(block: str) -> list[dict]:
    items = []
    for raw in re.split(r"[–—]", block):
        line = raw.strip()
        if not line or not re.search(r"\d", line):
            continue
        m = re.match(r"^(.*?)\s*(?:…|\.{2,}|\s*)\s*(\d+[.,]\d{2})\s*€\s*(.*)$", line)
        if m:
            name, price, tail = m.group(1).strip(), m.group(2), m.group(3)
        else:
            name, price, tail = line, None, ""
        if not name:
            continue
        # Join hyphen line-wraps ("Tomaten-\nKapern") into a single token.
        name = re.sub(r"-\s+", "", name).strip(" –—")
        if not name:
            continue
        price = (price.replace(".", ",") + " €") if price else None
        tags = ["vegetarisch"] if re.search(r"\(veg", name + " " + tail, re.I) else []
        items.append(make_item(name, price=price, tags=tags))
    return items


def scrape() -> dict:
    session = new_session()
    html = fetch_text(session, URL)
    soup = BeautifulSoup(html, "html.parser")

    # Re-flow: capture <strong> weekday markers so we can re-split reliably.
    for s in soup.find_all("strong"):
        s.replace_with("\u0001" + s.get_text() + "\u0002")
    menu_text = " ".join(p.get_text(" ") for p in soup.find_all("p"))
    menu_text = re.sub(r"\s+", " ", menu_text)
    if "€" not in menu_text:
        raise RuntimeError("could not locate SUDOST menu text block")

    label, week_dates = _week_label_and_dates(menu_text)

    # Split on weekday markers (bold tokens). Each marker opens a day block.
    parts = re.split(r"\u0001([A-Za-zäöü]+\s*[:.]?)\u0002", menu_text)
    # parts = [pre, name1, body1, name2, body2, ...]
    days: dict[str, dict] = {}
    soup_note = None
    for k in range(1, len(parts), 2):
        token = parts[k].strip(" .:")
        body = parts[k + 1] if k + 1 < len(parts) else ""
        low = token.lower()
        if low in ("montag", "dienstag", "mittwoch", "donnerstag", "freitag"):
            idx = DAY_NAMES.index(token[0].upper() + token[1:])
            items = _parse_items(body)
            # Drop trailing garbage (e.g. the closing contact sentence on Friday).
            items = [it for it in items if it["price"] is not None]
            if items and idx < len(week_dates):
                days[week_dates[idx]] = make_day(DAY_NAMES[idx], items)

    # Soup line: "Süppchen mit Brot ... 5,80€" (before the first weekday),
    # ignoring the leading date-range label.
    pre = re.sub(r"\d{1,2}\.\d{1,2}\s*[-–—]\s*\d{1,2}\.\d{1,2}\.\d{4}\s*:?[\s:]*",
                 "", parts[0], count=1)
    m = re.search(r"([^…]+?)\s*…\s*(\d+[.,]\d{2})\s*€", pre)
    if m:
        soup_note = (f"Täglich: {m.group(1).strip()} "
                     f"{m.group(2).replace('.', ',')} €")

    r = make_restaurant("sudost", NAME, "week",
                        url=MENU_URL, address=ADDRESS, period=label)
    r["days"] = days
    r["note"] = soup_note
    r["status"] = "ok" if days else "error"
    r["last_scraped"] = now_iso()
    if not days:
        r["error"] = "no day blocks parsed"
    return r

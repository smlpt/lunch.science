"""Scraper for Wichernhaus (Schneider Speisen Service) - single-day lunch menu."""
from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

from .common import (DAY_TOKENS, clean, date_key, fetch_text, make_day,
                     make_item, make_restaurant, new_session, now_iso)

URL = "https://www.wichernhaus-goerlitz.de/index.php?hauptmenue=SPK"
NAME = "Wichernhaus"
ADDRESS = "Johannes-Wüsten-Str. 23A, 02826 Görlitz"
FLAT_PRICE = "8,00 €"


def _parse_day_and_date(text: str) -> tuple[int | None, str | None]:
    m = re.search(r"heutiges\s+Mittagsangebot:\s*([A-Za-zäöü]+)\s*(\d{2}\.\d{2}\.\d{2,4})", text)
    if not m:
        return None, None
    weekday = m.group(1).strip().lower()
    dd, mm, yy = m.group(2).split(".")
    year = int(yy) if len(yy) == 4 else 2000 + int(yy)
    try:
        d = date(year, int(mm), int(dd))
    except ValueError:
        return DAY_TOKENS.get(weekday), None
    return d.weekday(), d.isoformat()


def scrape() -> dict:
    session = new_session()
    html = fetch_text(session, URL)
    soup = BeautifulSoup(html, "html.parser")

    # The menu content lives inside a table; pull all its text in document order.
    content = soup.find("table")
    raw_lines = [clean(l) for l in content.get_text("\n").splitlines()] if content else \
        [clean(l) for l in soup.get_text("\n").splitlines()]
    raw_lines = [l for l in raw_lines if l]

    weekday, iso = _parse_day_and_date(" ".join(raw_lines))

    items = []
    i = 0
    n = len(raw_lines)
    while i < n:
        m = re.match(r"^Menü\s+(\d+)$", raw_lines[i])
        if m:
            menu_no = m.group(1)
            # Description is the next non-empty line (skip the next 'Menü N' if adjacent).
            desc = ""
            j = i + 1
            while j < n and re.match(r"^Menü\s+\d+$", raw_lines[j]):
                j += 1
            if j < n and not re.match(r"^Menü\s+\d+$", raw_lines[j]):
                desc = raw_lines[j]
            if desc:
                items.append(make_item(f"Menü {menu_no}", desc=desc, price=FLAT_PRICE))
            i = j
            continue
        i += 1

    if weekday is None:
        weekday = date.today().weekday()
    label = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
             "Samstag", "Sonntag"][weekday]
    days = {date_key(date.today()): make_day(label, items)}
    if iso:
        # keep the actual published date key instead of today if it differs
        days = {iso: make_day(label, items)}

    r = make_restaurant("wichernhaus", NAME, "day",
                        url=URL, address=ADDRESS, period=label)
    r["days"] = days
    r["status"] = "ok" if items else "error"
    r["last_scraped"] = now_iso()
    if not items:
        r["error"] = "no menu items parsed"
    return r

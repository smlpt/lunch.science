"""Scraper for Onkel Emma Görlitz (weekly "Wochenkarte", one dish per day).

Onkel Emma publishes a weekly menu: a single house dish per day, Tuesday to
Saturday, rotating through a fixed set of weeks. The menu is *not* in the
page HTML - the page fetches ``wochenkarte.json`` and renders it client-side
(see the site's ``assets/pp.js``). We fetch that JSON directly with plain
HTTP (no JS needed) and reproduce the site's exact week-selection logic so we
show the same dish the website shows for the current week.

Only Monday-Friday are surfaced (weekends are irrelevant to the site's
consumers). Onkel Emma is a "Hausmannskost" place:
  * Monday   = Ruhetag (closed) -> shown as a closed day
  * Tue-Fri  = the day's dish (slot 0..3); Saturday (slot 4) is dropped.

The site's rotation has two subtleties we must match faithfully:
  1. ``dienstag()`` - the current week's anchor Tuesday, computed from "now".
  2. ``urlaubsWochen()`` - whole vacation weeks (Di-Sa all closed) consume no
     rotation step, so they are subtracted from the week offset.
We port both verbatim (including the JS short-circuit in the vacation
counter) so our result always equals what the page renders.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from .common import (clean, date_key, fetch_text, make_day, make_item,
                     make_restaurant, new_session, now_iso, week_range_dates)

BASE = "https://onkel-emma-gr.de"
JSON_URL = f"{BASE}/wochenkarte.json"
I18N_URL = f"{BASE}/assets/i18n.js"
MENU_URL = f"{BASE}/wochenkarte.html"
NAME = "Onkel Emma"
ADDRESS = "Demianiplatz 39, 02826 Görlitz"

# Monday..Sunday labels (index = weekday(), Mon=0).
DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
             "Freitag", "Samstag", "Sonntag"]
# The dish slots (relative to the anchor Tuesday) -> weekday index (Mon=0).
# slot 0 = Tue ... slot 4 = Sat. We keep Tue..Fri (slots 0..3); Saturday (4)
# and Monday (no slot) are excluded (Ruhetag / weekend).
SLOT_TO_WEEKDAY = {0: 1, 1: 2, 2: 3, 3: 4}  # -> Tue, Wed, Thu, Fri


def _js_getday(d: date) -> int:
    """JS Date.getDay(): Sunday=0 .. Saturday=6."""
    return (d.weekday() + 1) % 7


def _dienstag(now: date, offset: int = 0) -> date:
    """Port of pp.js ``dienstag(offset)`` - this week's anchor Tuesday."""
    dow = _js_getday(now)
    delta = 2 if dow == 0 else (1 if dow == 1 else -(dow - 2))
    return now + timedelta(days=delta + 7 * offset)


def _urlaub_range(session) -> tuple[date, date] | None:
    """Parse the site's vacation window from ``i18n.js`` (``URLAUB``).

    The vacation is *not* in wochenkarte.json; the site keeps it as a constant
    in assets/i18n.js:  ``const URLAUB = { von: 'YYYY-MM-DD', bis: ..., ... }``.
    We fetch that file and extract the window. If it can't be fetched/parsed
    (or the restaurant has no vacation set), we treat it as "no vacation" -
    which is correct whenever the window is empty or already in the past.
    """
    try:
        js = fetch_text(session, I18N_URL, timeout=30)
    except Exception:  # noqa: BLE001 - no vacation is a safe default
        return None
    m = re.search(r"URLAUB\s*=\s*\{[^}]*?von\s*:\s*['\"](\d{4}-\d{2}-\d{2})['\"][^}]*?"
                  r"bis\s*:\s*['\"](\d{4}-\d{2}-\d{2})['\"]", js)
    if not m:
        return None
    try:
        return (date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)))
    except ValueError:
        return None


def _im_urlaub(d: date, rng: tuple[date, date] | None) -> bool:
    return bool(rng) and rng[0] <= d <= rng[1]


def _urlaubs_wochen(tue: date, ref: date,
                    rng: tuple[date, date] | None) -> int:
    """Port of pp.js ``urlaubsWochen(tue)``.

    Counts whole weeks (Di-Sa) that are *entirely* inside the vacation window;
    those consume no rotation step. Faithful to the JS, including its
    short-circuit loop (a week counts only if its first days are all in
    vacation and it never finds a non-vacation day).
    """
    vor = tue >= ref
    t = ref if vor else tue
    ende = tue if vor else ref
    n = 0
    cur = t
    while cur < ende:
        ganz = True
        for i in range(5):
            if not ganz:
                break
            ganz = _im_urlaub(cur + timedelta(days=i), rng)
        if ganz:
            n += 1
        cur = cur + timedelta(days=7)
    return n if vor else -n


def _active_week(wk: dict, now: date,
                 rng: tuple[date, date] | None) -> dict:
    """Return the currently-served week object (reproducing the site)."""
    ref = date.fromisoformat(wk["ref_dienstag"])
    ref_nr = int(wk["ref_woche_nr"])
    weeks = wk["wochen"]
    tue = _dienstag(now)
    diff = round((tue - ref).days / 7) - _urlaubs_wochen(tue, ref, rng)
    n = len(weeks)
    num = (((ref_nr - 1 + diff) % n) + n) % n + 1
    for w in weeks:
        if w.get("nr") == num:
            return w
    raise RuntimeError(f"active week nr {num} not found in wochenkarte.json")


def _period_label(wk: dict, now: date) -> str:
    """The Di-Sa range the site shows (e.g. '03.11. - 07.11.2026')."""
    tue = _dienstag(now)
    sat = tue + timedelta(days=4)

    def dm(d: date) -> str:
        return f"{d.day:02d}.{d.month:02d}."
    return f"{dm(tue)} - {dm(sat)}{sat.year}"


def scrape() -> dict:
    session = new_session()
    raw = fetch_text(session, JSON_URL, timeout=40)
    try:
        wk = json.loads(raw)
        weeks = wk["wochen"]
        if not weeks:
            raise ValueError("empty 'wochen' list")
    except (ValueError, KeyError) as e:
        raise RuntimeError(f"unexpected wochenkarte.json shape: {e}")

    # Vacation window (Di-Sa) from the site's i18n.js; None if none/in the past.
    rng = _urlaub_range(session)
    week = _active_week(wk, date.today(), rng)

    # Key days by the current ISO week (Mon..Fri), like the other weekly scrapers.
    week_dates = week_range_dates(*date.today().isocalendar()[:2])
    days: dict[str, dict] = {}

    # Monday: Onkel Emma has Ruhetag (closed).
    days[date_key(week_dates[0])] = make_day(
        DAY_NAMES[0], [], is_open=False, note="Ruhetag (geschlossen)")

    # Tue-Fri: one dish each (slots 0..3). Skip a day if it is in vacation.
    for slot, weekday in SLOT_TO_WEEKDAY.items():
        d = week_dates[weekday]
        if _im_urlaub(d, rng):
            days[date_key(d)] = make_day(DAY_NAMES[weekday], [], is_open=False,
                                         note="Urlaub (geschlossen)")
            continue
        # Find the dish for this slot. Prefer an explicit tag_idx; fall back to
        # positional index (the site does the same).
        dish = None
        for i, g in enumerate(week["gerichte"]):
            idx = g.get("tag_idx")
            idx = i if idx in (None, "") else int(idx)
            if idx == slot:
                dish = g
                break
        if dish:
            item = make_item(dish.get("name"),
                             desc=clean(dish.get("beilage") or None),
                             price=clean(dish.get("preis") or None))
            days[date_key(d)] = make_day(DAY_NAMES[weekday], [item])
        else:
            # No dish published for this day -> show it as having no offering.
            days[date_key(d)] = make_day(DAY_NAMES[weekday], [],
                                         note="kein Gericht")

    r = make_restaurant("onkel_emma", NAME, "week",
                        url=MENU_URL, address=ADDRESS,
                        period=_period_label(wk, date.today()))
    r["days"] = days
    r["note"] = "Ein Tagesgericht pro Tag (Di-Fr), solange der Vorrat reicht."
    r["status"] = "ok" if days else "error"
    r["last_scraped"] = now_iso()
    if not days:
        r["error"] = "no day blocks parsed"
    return r

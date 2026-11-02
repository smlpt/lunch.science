"""Scraper for 'Unsere Kantine' Polizeidirektion Görlitz.

The page lists a set of weekly PDFs (KW <n> <year>). We pick the one whose
ISO week matches today, download it, and extract the text with pypdf. The PDF
is text-based, so no vision LLM is required. Allergens are legend numbers 1-10.
"""
from __future__ import annotations

import io
import re
from datetime import date

import pypdf
from bs4 import BeautifulSoup

from .common import (date_key, fetch_bytes, fetch_text, make_day, make_item,
                     make_restaurant, new_session, now_iso, week_range_dates)

PAGE_URL = "https://www.unsere-kantine.de/speiseplan-polizei-kantine-g%C3%B6rlitz/"
NAME = "Unsere Kantine · Polizeidirektion"
ADDRESS = "Conrad Schiedt Straße 2, 02826 Görlitz"

DAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
             "Freitag", "Samstag", "Sonntag"]

ALLERGEN_LEGEND = {
    "1": "Geschmacksverstärker", "2": "Antioxidationsmittel",
    "3": "Pökelsalz", "4": "Konservierungsstoff", "5": "Senf",
    "6": "Laktose", "7": "Phosphat", "8": "Erdnüssen",
    "9": "Sellerie", "10": "Gluten-Weizen",
}


def _find_current_pdf(session) -> str:
    html = fetch_text(session, PAGE_URL)
    soup = BeautifulSoup(html, "html.parser")
    year, week = date.today().isocalendar().year, date.today().isocalendar().week
    base = "https://www.unsere-kantine.de"
    # The PDF <a> tags carry no visible text; the week is in the filename
    # (URL-encoded as KW+45+2026). Fall back to the <a> text and the
    # .cc-m-download-file-name sibling.
    wanted = re.compile(rf"KW\+{week}\+{year}", re.I)
    wanted_plain = re.compile(rf"KW\s+{week}\D+{year}", re.I)
    cands: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href:
            continue
        text = a.get_text(" ", strip=True)
        mod = a.find_parent(class_="j-downloadDocument")
        name_el = mod.select_one(".cc-m-download-file-name") if mod else None
        name = name_el.get_text(" ", strip=True) if name_el else ""
        hay = f"{href} {text} {name}"
        if wanted.search(href) or wanted_plain.search(text) or wanted_plain.search(name):
            url = base + href if href.startswith("/") else href
            cands.append(url)
    if not cands:
        raise RuntimeError(f"no PDF found for current week KW {week} {year}")
    return cands[0]


def _extract_menu(text: str) -> tuple[dict, str | None]:
    """Return ({date_iso: day}, soup_note) from the extracted PDF text.

    Layout (one page, Mon..Fri): a 'Wochentag / TAGESESSEN 1..n / Tagessuppe'
    header, then per day a date line, followed by dish blocks. A dish block is
    a name line, optional description lines, a bare price line (e.g. '6,10'),
    and an '6,50 €/Außer Haus Gäste 7,00 €' line. pypdf sometimes glues the
    bare price onto the last description line.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    start = next((i for i, ln in enumerate(lines) if "Wochentag" in ln), None)
    if start is None:
        raise RuntimeError("PDF header row not found")
    body = lines[start + 1:]

    day_re = re.compile(
        r"^(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag),\s*(\d{2})\.(\d{2})\.(\d{4})")
    blocks: list[tuple[int, date, list[str]]] = []
    cur: tuple[int, date, list[str]] | None = None
    for ln in body:
        m = day_re.match(ln.strip())
        if m:
            if cur:
                blocks.append(cur)
            idx = DAY_NAMES.index(m.group(1))
            d = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
            cur = (idx, d, [])
        elif cur is not None:
            cur[2].append(ln)
    if cur:
        blocks.append(cur)

    SUPPE_MARKER = "Tagessuppe"
    PRICE_E = re.compile(r"(\d+[.,]\d{2})\s*€")
    BARE_PRICE = re.compile(r"^\d+[.,]\d{2}$")
    # German quotes can be opened with „ (U+201E) or “ (U+201C); closed with ”
    # (U+201D) or " (U+201C).
    TITLE_OPEN_RE = re.compile(r"^[„“]")
    META = ("außer haus", "außerhaus", "tagessuppe", "tasse", "schüssel",
            "salatbaukasten", "salatteller", "job & fit", "vorbestellung",
            "dessert", "vitrine", "salat wahlweise")
    # Drop a glued trailing number: a 2-digit stray ('...streifen10') or a
    # glued decimal price ('...Salat6,10').
    TRAILING_NUM = re.compile(r"(?:\d{1,2}|,\d{2})$")

    def is_meta(s: str) -> bool:
        low = s.lower()
        return any(x in low for x in META)

    def parse_group(glines: list[str]) -> tuple[dict | None, str | None, bool]:
        """Parse one dish/soup group. Return (item_or_None, soup_note_or_None, is_soup).

        Price handling: the *last* bare in-house price and the *last*
        in-house/takeaway line win, so a second dish's price (in a merged
        group) is preferred over the first dish's.
        """
        soup_txt: list[str] = []
        desc_lines: list[str] = []
        price_in = price_out = None
        for raw in glines:
            t = raw.strip()
            if not t:
                continue
            if BARE_PRICE.match(t):
                if price_in is None:
                    price_in = t.replace(".", ",") + " €"
                continue
            if PRICE_E.search(t):
                nums = re.findall(r"(\d+[.,]\d{2})\s*€", t)
                if price_in is None and nums:
                    price_in = nums[0].replace(".", ",") + " €"
                if len(nums) >= 2:
                    price_out = nums[1].replace(".", ",") + " €"
                if is_meta(t):
                    continue
                continue
            if SUPPE_MARKER in t:
                soup_txt.append(t)
                continue
            if is_meta(t):
                continue
            # A glued decimal price at end of a text line.
            m = re.search(r"(\d+[.,]\d{2})\s*$", t)
            if m:
                t = t[:m.start()].rstrip()
                if price_in is None:
                    price_in = m.group(1).replace(".", ",") + " €"
            if t:
                desc_lines.append(t)
        if soup_txt:
            note = " ".join(re.sub(r"\s+", " ", x)
                            for x in (soup_txt + desc_lines) if x.strip()).strip()
            return None, note, True
        if not desc_lines:
            return None, None, False
        full = " ".join(desc_lines)
        full = re.sub(r"-\s+", "", full)            # join hyphen line-wraps
        full = TRAILING_NUM.sub("", full).strip()    # drop glued trailing number
        full = re.sub(r"\s+", " ", full).strip()
        if not full:
            return None, None, False
        name_final, desc_final = _split_name_desc(full)
        if not name_final:
            return None, None, False
        return make_item(name_final, desc=desc_final,
                         price=price_in, price_takeaway=price_out), None, False

    days: dict[str, dict] = {}
    soup_note = None
    for idx, d, blines in blocks:
        # Split the day block into groups at blank lines.
        groups: list[list[str]] = []
        cur: list[str] = []
        for ln in blines:
            if ln.strip():
                cur.append(ln)
            else:
                if cur:
                    groups.append(cur)
                    cur = []
        if cur:
            groups.append(cur)

        items: list[dict] = []
        for g in groups:
            joined = " ".join(x.strip() for x in g if x.strip())
            # 1) A group may glue a "Salat ..." line onto the soup block.
            # Emit only the salad; the soup is captured separately.
            if SUPPE_MARKER in joined and not TITLE_OPEN_RE.match(joined):
                salad_part = joined.split(SUPPE_MARKER, 1)[0].strip()
                if salad_part and re.search(r"salat", salad_part, re.I):
                    it = _build_item(salad_part)
                    if it:
                        items.append(it)
                    continue
            # 2) A missing blank line can merge two dishes where the second
            # begins mid-group with a quoted title (e.g. Matjes + "Spaghetti").
            # Only split when the head ends with a glued price (a complete
            # first dish) and the tail (the quoted second dish) has a price.
            m_open = TITLE_OPEN_RE.match(joined)
            if not m_open:
                m_mid = TITLE_OPEN_RE.search(joined)
                if m_mid and m_mid.start() > 3:
                    head, tail = joined[:m_mid.start()].strip(), joined[m_mid.start():].strip()
                    if (len(head) >= 3 and len(tail) >= 3
                            and re.search(r"(\d+[.,]\d{2})\s*€\s*$", head)
                            and re.search(r"(\d+[.,]\d{2})\s*€", tail)):
                        for part in (head, tail):
                            it = _build_item(part)
                            if it:
                                items.append(it)
                        continue
            # 3) Normal single dish / soup group.
            item, note, is_soup = parse_group(g)
            if is_soup:
                if soup_note is None and note:
                    soup_note = note
                continue
            if item:
                items.append(item)
        if items:
            days[date_key(d)] = make_day(DAY_NAMES[idx], items)
    return days, soup_note


def _build_item(text: str) -> dict | None:
    """Build an item from a chunk of dish text (used by merged-group splits).

    The *last* price wins so a second dish's price isn't shadowed by the
    first.
    """
    PRICE_E = re.compile(r"(\d+[.,]\d{2})\s*€")
    BARE_PRICE = re.compile(r"^\d+[.,]\d{2}$")
    price_in = price_out = None
    for tok in re.split(r"\s+", text):
        if BARE_PRICE.match(tok):
            price_in = tok.replace(".", ",") + " €"
            continue
        if PRICE_E.search(tok):
            nums = re.findall(r"(\d+[.,]\d{2})\s*€", tok)
            if nums:
                price_in = nums[0].replace(".", ",") + " €"
                if len(nums) >= 2:
                    price_out = nums[1].replace(".", ",") + " €"
    full = re.sub(r"-\s+", "", text)
    full = re.sub(r"(?:\d{1,2}|,\d{2})$", "", full).strip()
    full = re.sub(r"\s+", " ", full).strip()
    if not full:
        return None
    name_final, desc_final = _split_name_desc(full)
    if not name_final:
        return None
    # Clean a "Salat wahlweise siehe ..."-style name down to "Salat".
    if re.match(r"^\s*salat\s", name_final, re.I):
        name_final = "Salat"
    return make_item(name_final, desc=desc_final, price=price_in, price_takeaway=price_out)


def _split_name_desc(full: str) -> tuple[str, str | None]:
    """Split a joined dish string into a display name and a description.

    Quoted titles ("Black Burger") are names. Otherwise the name is the leading
    segment up to the first lowercase conjunction/verb cue; the rest is the
    description.
    """
    q = re.match(r"^\s*[„“\"](.*?)[”\"]\s*(.*)$", full)
    if q and q.group(1).strip():
        name = q.group(1).strip().strip("„“”\"")
        return name, (q.group(2).strip() or None)
    m = re.match(
        r"^(.{3,60}?)(?:\s+(?:dazu|mit|und|oder|auf|aus|vom|belegt|gefüllt|"
        r"gebraten|verfeinert|gratiniert|überbacken|paniert|serviert)\s.*)$",
        full, re.I)
    if m:
        return m.group(1).strip(), (full[m.end(1):].strip() or None)
    return full.strip(), None


def scrape() -> dict:
    session = new_session()
    pdf_url = _find_current_pdf(session)
    raw = fetch_bytes(session, pdf_url)
    reader = pypdf.PdfReader(io.BytesIO(raw))
    text = "\n".join(p.extract_text() for p in reader.pages)
    days, note = _extract_menu(text)

    week_dates = week_range_dates(*date.today().isocalendar()[:2])
    period = f"{week_dates[0].strftime('%d.%m.')} – {week_dates[4].strftime('%d.%m.%Y')}"

    r = make_restaurant("polizei_kantine", NAME, "week",
                        url=PAGE_URL, address=ADDRESS, period=period,
                        allergens_legend=ALLERGEN_LEGEND)
    r["days"] = days
    r["note"] = note
    r["status"] = "ok" if days else "error"
    r["last_scraped"] = now_iso()
    if not days:
        r["error"] = "no day blocks parsed from PDF"
    return r

"""Post-scrape English translation of the menu data.

After scrape.scrape() builds the unified payload (the dict that becomes
data/menus.json), this step walks every restaurant and translates the German
human-readable strings into English, stored alongside the originals as
``*_en`` fields. The UI's DE/EN toggle picks which to show.

Approach:
  * One LLM call per restaurant (menus are small; this keeps each call bounded
    and isolates a failing restaurant so the others still translate).
  * We send a DEDUPLICATED list of strings, each tagged with a stable key
    (e.g. "name:0", "desc:3", "legend:2"), and ask the model to return a JSON
    object mapping each key to its English translation. This avoids asking it
    to re-emit whole item structures (fewer tokens, no schema drift).
  * Allergens: the per-restaurant legend (e.g. {"3": "SOJA"}) is translated as
    whole strings. The numeric codes themselves are stable and are never
    translated - only the words.
  * Prices, numbers, allergen codes, and tags that are already English
    (vegan/bowl) are left as-is; only genuinely German words change.

Graceful degradation (the whole point): if there is no API key, or a
restaurant's call fails, that restaurant simply keeps its German strings and
gets no ``*_en`` fields. The UI then shows German for those in EN mode - the
menu is never blocked or empty because translation is unavailable.
"""
from __future__ import annotations

import json
import logging
import re

from .common import ask_text, now_iso

log = logging.getLogger("lunch.translate")

# Tags that are already English / language-neutral - translate only the rest.
_PASSTHROUGH_TAGS = {"vegan", "bowl"}
# German tag -> English (covers the tags the scrapers emit).
_TAG_FALLBACK = {
    "vegetarisch": "vegetarian",
    "eintopf": "stew",
    "kuchen": "cake",
    "suppe": "soup",
    "dessert": "dessert",
}

SYSTEM = (
    "You translate German restaurant menu text into natural, concise British "
    "English. You are given a JSON object mapping keys to German strings. "
    "Return ONLY a JSON object with the SAME keys, each value the English "
    "translation. Do not add, remove, or rename keys. Do not wrap in markdown "
    "or add commentary. Keep dish names appetizing but accurate; keep "
    "descriptions short. For allergen words use the standard EU allergen name "
    "(e.g. GLUTEN, PEANUT, SOYA, TREE NUTS, CELERY, MUSTARD, SESAME, "
    "SULPHITES, LUPIN, MILD, CRUSTACEANS, FISH, EGGS, MILK). If a string is "
    "already English or is a number/price, return it unchanged."
)


def _extract_strings(rest: dict) -> tuple[dict[str, str], dict[str, str],
                                          dict[tuple[str, int], int]]:
    """Collect the German strings for one restaurant.

    Returns (strings, tag_keys, item_idx):
      * ``strings``  - {key: german_string}, stable keys for the LLM response.
      * ``tag_keys`` - original German tag value -> its ``tagv`` key.
      * ``item_idx`` - (daykey, item_position) -> the item's global index (the
        number used in the ``name:N`` / ``desc:N`` / ``cat:N`` keys), so
        _apply_translations reads back the matching translation.
    """
    out: dict[str, str] = {}
    tag_keys: dict[str, str] = {}
    # A GLOBAL per-restaurant item index (not per-day) so keys are unique even
    # when several days each carry an item at position 0. item_idx maps
    # (daykey, item_position) -> that item's global index (the number used in
    # the name:N / desc:N / cat:N keys) so _apply_translations reads back the
    # matching translation.
    item_idx: dict[tuple[str, int], int] = {}
    counter = 0

    def put(kind: str, idx: int, value: str | None) -> None:
        if not value:
            return
        v = value.strip()
        if not v:
            return
        out[f"{kind}:{idx}"] = v

    for dk, day in rest.get("days", {}).items():
        for i, it in enumerate(day.get("items", [])):
            item_idx[(dk, i)] = counter
            put("name", counter, it.get("name"))
            put("desc", counter, it.get("description"))
            put("cat", counter, it.get("category"))
            counter += 1
        # Day-level constant strings (closed-day notes like "Geschlossen",
        # SoulReis location/time notes, and the day label).
        put("dnote", dk, day.get("note"))
        put("dlabel", dk, day.get("label"))

    # Restaurant-level constant strings shown in the UI (address, period,
    # the "note" line). Keyed by name so _apply_translations can read them back.
    put("const", "period", rest.get("period"))
    put("const", "address", rest.get("address"))
    put("const", "note", rest.get("note"))

    # Tags are few and shared; give them their own namespace, dedup by value,
    # and keep the value->key map so _apply_translations can look them up by
    # the (original) tag string rather than by index.
    tag_values: list[str] = []
    for dk, day in rest.get("days", {}).items():
        for it in day.get("items", []):
            for t in it.get("tags", []):
                if t and t not in _PASSTHROUGH_TAGS and t not in tag_values:
                    tag_values.append(t)
    for i, t in enumerate(tag_values):
        out[f"tagv:{i}"] = t
        tag_keys[t] = f"tagv:{i}"

    # Allergen legend values.
    for code, word in (rest.get("allergens_legend") or {}).items():
        if word:
            out[f"legend:{code}"] = str(word)

    return out, tag_keys, item_idx


def _apply_translations(rest: dict, tr: dict[str, str],
                        tag_keys: dict[str, str],
                        item_idx: dict[tuple[str, int], int]) -> int:
    """Write the LLM's translations back onto the restaurant dict.

    ``tag_keys`` maps an original German tag value -> its tagv key in ``tr``.
    Returns the number of fields actually changed (0 if nothing mapped).
    """
    changed = 0

    def get(key: str) -> str | None:
        v = tr.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    def tag_en(t: str) -> str:
        if t in _PASSTHROUGH_TAGS:
            return t
        key = tag_keys.get(t)
        if key:
            e = get(key)
            if e:
                return e
        # Deterministic fallback for known German tags the model may miss.
        return _TAG_FALLBACK.get(t, t)

    for dk, day in rest.get("days", {}).items():
        # Day-level note/label (e.g. "Geschlossen", location & time notes).
        dn = get(f"dnote:{dk}")
        if dn and dn != (day.get("note") or ""):
            day["note_en"] = dn
            changed += 1
        dl = get(f"dlabel:{dk}")
        if dl and dl != (day.get("label") or ""):
            day["label_en"] = dl
            changed += 1
        for i, it in enumerate(day.get("items", [])):
            g = item_idx.get((dk, i))
            if g is None:
                continue
            n = get(f"name:{g}")
            if n and n != it.get("name"):
                it["name_en"] = n
                changed += 1
            d = get(f"desc:{g}")
            if d and d != (it.get("description") or ""):
                it["description_en"] = d
                changed += 1
            c = get(f"cat:{g}")
            if c and c != (it.get("category") or ""):
                it["category_en"] = c
                changed += 1
            tags = it.get("tags") or []
            if tags:
                it["tags_en"] = [tag_en(t) for t in tags]
                changed += 1

    # Restaurant-level constants (address, period, note).
    for field in ("period", "address", "note"):
        e = get(f"const:{field}")
        if e and e != (rest.get(field) or ""):
            rest[field + "_en"] = e
            changed += 1

    legend = rest.get("allergens_legend") or {}
    if legend:
        legend_en = {}
        for code, word in legend.items():
            legend_en[code] = get(f"legend:{code}") or word
        rest["allergens_legend_en"] = legend_en
        changed += 1

    return changed


def _translate_restaurant(rest: dict) -> bool:
    """Translate one restaurant in place. Returns True if anything changed.

    Never raises: on any error the restaurant keeps its German and we log.
    """
    if rest.get("status") not in (None, "ok"):
        # Skip error/unavailable stubs (they have no real dishes).
        return False
    strings, tag_keys, item_idx = _extract_strings(rest)
    if not strings:
        return False
    user_text = (
        "Translate the following German strings to English. Return a JSON "
        "object with the same keys:\n"
        + json.dumps(strings, ensure_ascii=False, indent=2)
    )
    try:
        raw = ask_text(SYSTEM, user_text)
    except Exception as e:  # noqa: BLE001 - degradation is the point
        log.warning("translation failed for %s: %s", rest.get("id"), e)
        return False
    tr = _parse_translation(raw)
    if not tr:
        log.warning("no usable translation for %s (raw: %.200s)",
                    rest.get("id"), raw)
        return False
    return _apply_translations(rest, tr, tag_keys, item_idx) > 0


def _parse_translation(raw: str) -> dict[str, str]:
    """Parse the model's JSON, tolerating a surrounding code fence."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Last resort: find the first {...} block.
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def translate_payload(payload: dict) -> dict:
    """Translate every restaurant in the payload in place; return payload.

    Safe to call when no LLM is configured (it no-ops per restaurant). Records
    ``translation`` metadata (status + timestamp) at the top level so the UI /
    ops can tell whether EN is populated.
    """
    translated = 0
    total = 0
    errors = 0
    for rest in payload.get("restaurants", []):
        total += 1
        try:
            if _translate_restaurant(rest):
                translated += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            log.warning("translate crashed for %s: %s", rest.get("id"), e)
    payload["translation"] = {
        "status": "ok" if translated else ("partial" if errors else "none"),
        "translated": translated,
        "total": total,
        "at": now_iso(),
    }
    log.info("translation: %d/%d restaurants translated (%d errors)",
             translated, total, errors)
    return payload

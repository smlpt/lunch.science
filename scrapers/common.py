"""Shared helpers for all scrapers: HTTP session, vision-LLM, and data-model helpers."""
from __future__ import annotations

import base64
import html as _html
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

import requests

log = logging.getLogger("lunch")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}

# LLM config files, checked in order. The first file that exists (and is
# readable) is used. The primary is the project's own protected file; the
# legacy kochwerk file is kept as a fallback for backwards compatibility.
ENV_FILES = (
    os.path.join(os.path.expanduser("~"), ".lunch_env"),
    os.path.join(os.path.expanduser("~"), ".kochwerk_env"),
)
# Canonical name for the primary file, used in error messages / docs.
ENV_FILE = ENV_FILES[0]

# German day-of-week tokens used across sources, mapped to weekday index (Mon=0).
DAY_TOKENS = {
    "mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6,
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6,
}


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


def fetch_text(session: requests.Session, url: str, timeout: int = 40) -> str:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def fetch_bytes(session: requests.Session, url: str, timeout: int = 60) -> bytes:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def clean(text: str) -> str:
    """Collapse whitespace and unescape HTML entities."""
    text = _html.unescape(text or "")
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Data-model helpers
# ---------------------------------------------------------------------------

def make_item(name: str,
              desc: Optional[str] = None,
              price: Optional[str] = None,
              price_takeaway: Optional[str] = None,
              tags: Optional[list[str]] = None,
              allergens: Optional[list] = None,
              **_: Any) -> dict:
    return {
        "name": clean(name),
        "description": clean(desc) if desc else None,
        "price": clean(price) if price else None,
        "price_takeaway": clean(price_takeaway) if price_takeaway else None,
        "tags": [t for t in (tags or []) if t],
        "allergens": list(allergens or []),
    }


def make_day(label: str, items: list[dict], is_open: bool = True, note: Optional[str] = None) -> dict:
    return {
        "label": clean(label) if label else None,
        "items": items,
        "is_open": is_open,
        "note": clean(note) if note else None,
    }


def make_restaurant(id: str, name: str, kind: str, *,
                    period: Optional[str] = None,
                    url: Optional[str] = None,
                    address: Optional[str] = None,
                    allergens_legend: Optional[dict] = None) -> dict:
    return {
        "id": id,
        "name": name,
        "kind": kind,
        "period": period,
        "url": url,
        "address": address,
        "allergens_legend": allergens_legend or {},
        "days": {},
        "status": "ok",
        "last_scraped": None,
        "error": None,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def date_key(d: date) -> str:
    return d.isoformat()


def week_range_dates(year: int, iso_week: int) -> list[date]:
    """Return the 5 weekday dates (Mon..Fri) for an ISO year+week."""
    from datetime import timedelta
    monday = date.fromisocalendar(year, iso_week, 1)
    return [monday + timedelta(days=i) for i in range(5)]


def current_week() -> tuple[int, int]:
    today = date.today()
    return today.isocalendar().year, today.isocalendar().week


# ---------------------------------------------------------------------------
# Vision LLM (reused logic from the original kochwerk.py)
# ---------------------------------------------------------------------------

def _read_env_file(path: str) -> dict:
    """Parse a KEY=VALUE env file into a dict.

    Accepts `export FOO=bar`, `FOO="bar"`, `FOO='bar'`, `FOO=bar` and ignores
    blank lines and `#` comments. Returns {} if the file is missing/unreadable.
    """
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    out[k] = v
    except Exception as e:  # noqa: BLE001
        log.warning("could not read env file %s: %s", path, e)
    return out


def _llm_config() -> dict:
    """Resolve the LLM config (key, base_url, model).

    Resolution order per setting:
      1. process environment variable
      2. the first existing env file (~/.lunch_env, then ~/.kochwerk_env)
      3. default

    Env-file variables accepted:
      LUNCH_API_KEY | KOCHWERK_API_KEY | OPENAI_API_KEY   -> api key
      LUNCH_BASE_URL | OPENAI_BASE_URL                    -> endpoint
      LUNCH_MODEL | KOCHWERK_MODEL                        -> model
    """
    file_vars: dict[str, str] = {}
    for path in ENV_FILES:
        file_vars = _read_env_file(path)
        if file_vars:
            log.info("LLM config file: %s", path)
            break

    def pick(*names, default: str = "") -> str:
        for n in names:
            if os.environ.get(n):
                return os.environ[n]
        for n in names:
            if file_vars.get(n):
                return file_vars[n]
        return default

    return {
        "api_key": pick("LUNCH_API_KEY", "KOCHWERK_API_KEY", "OPENAI_API_KEY"),
        "base_url": pick("LUNCH_BASE_URL", "OPENAI_BASE_URL",
                         default="https://api.openai.com/v1"),
        "model": pick("LUNCH_MODEL", "KOCHWERK_MODEL", default="gpt-4o"),
    }


def resolve_api_key() -> str:
    return _llm_config()["api_key"]


def vision_available() -> bool:
    return bool(resolve_api_key())


def ask_vision(image_b64: str, *, system: str, user_text: str = "Transcribe this menu.",
               image_mime: str = "image/jpeg") -> dict:
    cfg = _llm_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise RuntimeError(
            "no vision LLM API key configured. Create a protected config file "
            f"{ENV_FILE} (chmod 600) containing LUNCH_API_KEY=your-key, or set "
            "the env var LUNCH_API_KEY / OPENAI_API_KEY.")
    base_url = cfg["base_url"].rstrip("/")
    model = cfg["model"]
    payload = {
        "model": model,
        "max_tokens": 4000,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url",
                 "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
            ]},
        ],
    }
    log.info("POST %s/chat/completions model=%s", base_url, model)
    r = requests.post(base_url + "/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json=payload, timeout=180)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    log.info("LLM raw response (%d chars):\n%s", len(text), text[:2000])
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        log.error("LLM JSON parse failed: %s", e)
        raise
    if isinstance(data, dict):
        log.info("LLM parsed keys: %s", list(data.keys()))
    return data


def image_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def ask_text(system: str, user_text: str, *, max_tokens: int = 4000,
             timeout: int = 180) -> str:
    """Plain text->text chat completion on the same LLM endpoint as ask_vision.

    Returns the raw content string (not parsed). Raises RuntimeError if no API
    key is configured, and the underlying requests/HTTP error otherwise. Used by
    the post-scrape translation step.
    """
    cfg = _llm_config()
    api_key = cfg["api_key"]
    if not api_key:
        raise RuntimeError(
            "no LLM API key configured. Create a protected config file "
            f"{ENV_FILE} (chmod 600) containing LUNCH_API_KEY=your-key, or set "
            "the env var LUNCH_API_KEY / OPENAI_API_KEY.")
    base_url = cfg["base_url"].rstrip("/")
    model = cfg["model"]
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
    }
    log.info("POST %s/chat/completions (text) model=%s", base_url, model)
    r = requests.post(base_url + "/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json=payload, timeout=timeout)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    log.info("LLM text response (%d chars):\n%s", len(text), text[:1500])
    return text

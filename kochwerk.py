#!/usr/bin/env python3
"""
kochwerk_menu.py - weekly menu grabber for kochwerk-goerlitz.de
Uses an OpenAI-compatible vision LLM endpoint to read the menu image.

Key resolution order:
    1. --api-key CLI arg
    2. KOCHWERK_API_KEY or OPENAI_API_KEY env var
    3. %USERPROFILE%\\.kochwerk_env  (file containing KOCHWERK_API_KEY=...)

Endpoint resolution order:
    1. --base-url CLI arg
    2. OPENAI_BASE_URL env var
    3. default https://api.openai.com/v1

Model resolution order:
    1. --model CLI arg
    2. KOCHWERK_MODEL env var
    3. default gpt-4o

One-time setup for the env file:
    "KOCHWERK_API_KEY=your-key" | Out-File "$env:USERPROFILE\.kochwerk_env" -Encoding ascii
    # then restrict it to your user (fix your shell's variable expansion first):
    $me = [Environment]::UserName
    icacls "$env:USERPROFILE\.kochwerk_env" /inheritance:r /grant:r "$($me):R"

Run:
    python kochwerk_menu_vision.py
    python kochwerk_menu_vision.py --json
    python kochwerk_menu_vision.py --save menu.md
    python kochwerk_menu_vision.py --debug
"""
import argparse, base64, json, os, re, sys, logging
import requests

log = logging.getLogger("kochwerk")
SITE = "https://kochwerk-goerlitz.de/"
DAY_ORDER = ["MO","DI","MI","DO","FR","BOWL","EINTOPF","SUPPE"]
ENV_FILE = os.path.join(os.path.expanduser("~"), ".kochwerk_env")

SYSTEM = (
    "You are a menu transcriber. Read the restaurant menu image and return ONLY "
    "valid JSON, no markdown, no commentary. Schema: "
    '{"period": "string or null", "items": [ {"day": "MO|DI|MI|DO|FR|BOWL|EINTOPF|SUPPE|DESSERT|KUCHEN", '
    '"name": "string", "description": "string", "allergens": [int], "price_in_house": "string or null", '
    '"price_takeaway": "string or null"} ], "allergens_legend": {"1": "Gluten", ...}}. '
    "Transcribe German text exactly as written. Preserve prices with their euro sign. "
    "Use null for any field not present in the image."
)

def read_key_from_env_file() -> str:
    """Parse KOCHWERK_API_KEY=... from %USERPROFILE%\\.kochwerk_env."""
    if not os.path.isfile(ENV_FILE):
        log.debug("env file not found: %s", ENV_FILE)
        return ""
    try:
        with open(ENV_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                if "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() == "KOCHWERK_API_KEY":
                        v = v.strip().strip('"').strip("'")
                        if v:
                            log.debug("key read from env file: %s", ENV_FILE)
                            return v
    except Exception as e:
        log.debug("failed reading env file: %s", e)
    return ""

def resolve_key(cli_key) -> str:
    if cli_key:
        log.info("key source: --api-key")
        return cli_key
    for var in ("KOCHWERK_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(var):
            log.info("key source: env %s", var)
            return os.environ[var]
    k = read_key_from_env_file()
    if k:
        log.info("key source: %s", ENV_FILE)
        return k
    return ""

def find_menu_url(html: str) -> str:
    m = re.search(r"--img-speiseplan:\s*url\('([^']+)'\)", html)
    if m:
        rel = m.group(1).strip()
        url = SITE.rstrip("/") + "/" + rel.lstrip("./")
        log.debug("speiseplan var -> %s", url)
        return url
    m = re.search(r"(/media/[A-Za-z0-9_]+\.jpg)", html)
    if m:
        url = SITE.rstrip("/") + m.group(1)
        log.debug("fallback media -> %s", url)
        return url
    raise RuntimeError("Could not find menu image URL in page HTML")

def ask_vision(image_b64: str, model: str, base_url: str, api_key: str) -> dict:
    payload = {
        "model": model,
        "max_tokens": 2000,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Transcribe this menu."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ]},
        ],
    }
    log.info("POST %s/chat/completions model=%s", base_url, model)
    r = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload, timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    text = data["choices"][0]["message"]["content"]
    log.debug("LLM raw reply (%d chars):\n%s", len(text), text)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)

def to_short(data: dict) -> str:
    """One line: the 5 weekdays + BOWL/EINTOPF/SUPPE, names only, no prices/desc/allergens."""
    by_day = {it["day"]: it for it in data.get("items", [])}
    parts = []
    for tok in DAY_ORDER:
        it = by_day.get(tok)
        if not it or not it.get("name"):
            continue
        parts.append(f"{tok}: {it['name'].strip()}")
    return "\n".join(parts)

def to_markdown(data: dict) -> str:
    lines = [f"# KOCHWERK Speiseplan {data.get('period') or ''}".rstrip(), ""]
    by_day = {it["day"]: it for it in data.get("items", [])}
    for tok in DAY_ORDER + ["DESSERT","KUCHEN"]:
        it = by_day.get(tok)
        if not it:
            continue
        al = f"  *Allergene: {', '.join(str(a) for a in it.get('allergens', []))}*" if it.get("allergens") else ""
        prices = []
        if it.get("price_in_house"): prices.append(f"im Haus {it['price_in_house']}")
        if it.get("price_takeaway"): prices.append(f"au\u00dfer Haus {it['price_takeaway']}")
        lines.append(f"## {tok}")
        lines.append(f"**{it.get('name','')}**{al}")
        if it.get("description"):
            lines.append(it["description"])
        if prices:
            lines.append("Preis: " + " / ".join(prices))
        lines.append("")
    leg = data.get("allergens_legend")
    if leg:
        lines.append("**Allergene:** " + ", ".join(f"{k} {v}" for k,v in sorted(leg.items())))
    return "\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--short", action="store_true",
                    help="condensed one-line output: weekdays + bowl/eintopf/suppe, names only")
    ap.add_argument("--save", metavar="FILE")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--model", default=os.environ.get("KOCHWERK_MODEL","gpt-4o"))
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL","https://api.openai.com/v1"))
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(levelname)s %(message)s", stream=sys.stderr)

    api_key = resolve_key(args.api_key)
    if not api_key:
        sys.exit("No API key found. Set KOCHWERK_API_KEY, pass --api-key, or create "
                 f"{ENV_FILE} containing: KOCHWERK_API_KEY=your-key")

    html = requests.get(SITE, timeout=30).text
    log.info("site HTML: %d bytes", len(html))
    url = find_menu_url(html)
    log.info("menu image: %s", url)
    img = requests.get(url, timeout=30); img.raise_for_status()
    log.info("image: %d bytes", len(img.content))
    b64 = base64.b64encode(img.content).decode()

    data = ask_vision(b64, args.model, args.base_url, api_key)
    if args.json:
        rendered = json.dumps(data, ensure_ascii=False, indent=2)
    elif args.short:
        rendered = to_short(data)
    else:
        rendered = to_markdown(data)
    print(rendered)
    if args.save:
        open(args.save, "w", encoding="utf-8").write(rendered)
        log.info("saved -> %s", args.save)

if __name__ == "__main__":
    main()


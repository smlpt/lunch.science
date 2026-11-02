# lunch.science

Internes Speiseplan-Tool für Görlitz. Ruft regelmäßig die Speisepläne mehrerer
Gastronomiebetriebe ab, normalisiert sie in ein gemeinsames Datenformat und
zeigt sie als eine schlanke, leicht retro-/wissenschaftlich anmutende Website
(Übersicht + Tage-Tabs, "Heute" per Default).

## Quellen

| Restaurant            | Quelle                                        | Abfrage                                        |
|-----------------------|-----------------------------------------------|------------------------------------------------|
| SUDOST                | `sudost.de/unser-speisezettel/` (im iFrame)   | HTML-Text, ganze Woche                         |
| Kochwerk              | `kochwerk-goerlitz.de` (Bild)                 | Vision-LLM (OpenAI-kompatibel)                 |
| Unsere Kantine · Polizei | `unsere-kantine.de` (PDF, KW <n>)           | `pypdf`-Textextraktion, aktuelle KW            |
| Wichernhaus           | `wichernhaus-goerlitz.de` (HTML-Tabelle)      | HTML, nur der heutige Tag                      |
| Bauchgefühl           | via Lieferando (`__NEXT_DATA__`)              | Headless-Chromium (Playwright), à la carte     |
| SoulReis (Thang)      | `tally.so/r/81dLar` (Wochenplan)              | HTML-Text (Tally rendert serverseitig)         |
| Jakobs Speiselokal    | `instagram.com/jakobsspeiselokal` (1. Post)   | Playwright + Vision-LLM (Menü ist ein Bild)    |

## Architektur

```
scrape.py        → ruft alle Scraper, schreibt data/menus.json (atomar)
scrapers/        → ein Modul pro Restaurant + common.py (HTTP, Vision-LLM, Schema)
config.py        → Pfade, Refresh-Intervall, Bind-Host/Port
app.py           → Flask-App, rendert die UI, Hintergrund-Thread scrapet alle 4 h
templates/ static/ → Frontend (Vanilla HTML/CSS/JS, kein Build)
data/menus.json  → generiert (gitignored)
```

Gemeinsames Schema pro Restaurant:

```jsonc
{
  "id": "sudost", "name": "SUDOST", "kind": "week",   // week | day | weekly-rotating
  "period": "26.10. – 30.10.2026", "url": "...", "address": "...",
  "allergens_legend": {"1": "Gluten", ...},
  "days": { "2026-11-02": { "label": "Montag", "items": [
        { "name": "...", "description": "...", "price": "12,30 €",
          "price_takeaway": null, "tags": ["vegetarisch"], "allergens": [1,2] } ] } },
  "status": "ok",  // ok | stale | error | unavailable
  "last_scraped": "2026-11-02T11:45:31+01:00", "error": null
}
```

`kind=weekly-rotating` (Bauchgefühl) hat keinen Tagesbegriff — die Karte wird auf
jeden Tag gezeigt.

## Setup (VM)

```bash
cd lunch.science
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # einmalig, ~150 MB
```

Start:

```bash
.venv/bin/python app.py            # bindet 0.0.0.0:8080, scrapet bei Start + alle 4 h
```

Nur einmalig scrape (z. B. für Cron / Debug):

```bash
.venv/bin/python scrape.py                     # alle Quellen
.venv/bin/python scrape.py sudost wichernhaus  # nur ausgewählte
```

### LLM-Konfiguration (Vision-OCR für Kochwerk & Jakobs)

Zwei Quellen (Kochwerk, Jakobs Speiselokal) geben ihr Menü nur als Bild ab und
brauchen daher ein Vision-LLM. Die Konfiguration wird **zentral in einer
geschützten Datei** abgelegt, die nur du lesen kannst:

```bash
cat > ~/.lunch_env <<'EOF'
# lunch.science LLM config – nur dieses Konto darf lesen (chmod 600)
LUNCH_API_KEY=sk-...
# optional:
LUNCH_BASE_URL=https://api.openai.com/v1
LUNCH_MODEL=gpt-4o
EOF
chmod 600 ~/.lunch_env      # nur Eigentümer: lesen/schreiben
```

Aufruf-Reihenfolge pro Einstellung (Datei gewinnt, falls Env-Var leer):

1. Umgebungsvariable (`LUNCH_API_KEY` / `OPENAI_API_KEY`, …)
2. `~/.lunch_env`  → dann legacy `~/.kochwerk_env`
3. Default

Alle akzeptierten Schlüssel in der Datei:

| Schlüssel (Datei)              | Env-Var-Äquivalent               | Bedeutung          |
|--------------------------------|----------------------------------|--------------------|
| `LUNCH_API_KEY`                | `LUNCH_API_KEY` / `OPENAI_API_KEY` | API-Key        |
| `LUNCH_BASE_URL`               | `OPENAI_BASE_URL`                | LLM-Endpoint       |
| `LUNCH_MODEL`                  | `KOCHWERK_MODEL`                 | LLM-Modell         |

> `chmod 600` = nur der datei-erstellende Nutzer (der App-Nutzer auf der VM)
> kann sie lesen. Andere Nutzer bekommen `Permission denied`.

### Umgebungsvariablen (App)

| Variable                 | Default      | Bedeutung                                   |
|--------------------------|--------------|---------------------------------------------|
| `LUNCH_HOST`             | `0.0.0.0`    | Bind-Host                                    |
| `LUNCH_PORT`             | `8080`       | Port                                         |
| `LUNCH_REFRESH_SECONDS`  | `14400`      | Refresh-Intervall (4 h)                      |
| `LUNCH_NO_STARTUP_SCRAPE`| `0`          | `=1` → kein Scrape bei Start                 |

Ohne API-Key laufen Kochwerk & Jakobs im Status **offline** (unavailable) — alle
anderen Quellen sind davon unabhängig.

### systemd-Unit (Beispiel)

```ini
[Unit]
Description=lunch.science
After=network-online.target

[Service]
WorkingDirectory=/pfad/zu/lunch.science
ExecStart=/pfad/zu/lunch.science/.venv/bin/python app.py
Restart=on-failure
Environment=LUNCH_PORT=8080

[Install]
WantedBy=multi-user.target
```

## API

- `GET /`            → Website
- `GET /api/menus`   → komplettes `menus.json`
- `GET /api/status`  → `{scraped_at, scraping, last_run, next_run, error, refresh_seconds}`
- `POST /api/scrape` → löst einen manuellen Scrape aus

## Troubleshooting

- **Bauchgefühl leer/403:** Lieferando liegt hinter Cloudflare. Playwright
  (Headless-Chromium) muss installiert sein (`playwright install chromium`).
  Ggf. `--no-sandbox` ist nötig (setzt der Scraper bereits).
- **Kochwerk / Jakobs offline:** Kein API-Key. `~/.lunch_env` anlegen
  (siehe oben) mit `LUNCH_API_KEY=...` und `chmod 600`, oder Env-Var setzen.
  `chmod 600` stellt sicher, dass nur der App-Nutzer die Datei liest.
- **Polizei-Kantine leer:** Es existiert noch kein PDF für die aktuelle KW
  (KW <n> <jahr>) auf der Seite.
- **Jakobs Speiselokal offline:** Das Menü ist ein Instagram-Bild und braucht
  einen Vision-LLM-Key (wie Kochwerk). Ohne Key → Status **offline**.
- **SoulReis leer:** Der Tally-Wochenplan listet nur Tage, an denen Thang steht.
  Geschlossene Tage (z. B. Mo/Fr) werden als „Geschlossen" gezeigt; Tage ohne
  Eintrag in der aktuellen Woche erscheinen nicht.
- **Daten stale:** `last_scraped` älter als ~3 h → gelbe LED im UI.

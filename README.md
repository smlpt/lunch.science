# lunch.science

Internal menu-board tool for Görlitz. Periodically fetches the menus of several
local restaurants, normalizes them into a common data format, and displays them
on a slim, slightly retro/scientific-looking website (overview + day tabs,
"today" by default).

## Sources

| Restaurant             | Source                                          | Method                                             |
|------------------------|-------------------------------------------------|----------------------------------------------------|
| SUDOST                 | `sudost.de/unser-speisezettel/` (in an iFrame)  | HTML text, whole week                              |
| Kochwerk               | `kochwerk-goerlitz.de` (image)                  | Vision LLM (OpenAI-compatible)                     |
| Our Kantine · Police   | `unsere-kantine.de` (PDF, week <n>)             | `pypdf` text extraction, current week              |
| Wichernhaus            | `wichernhaus-goerlitz.de` (HTML table)          | HTML, today only                                   |
| Bauchgefühl            | via Lieferando (`__NEXT_DATA__`)                | Headless Chromium (Playwright), à la carte         |
| SoulReis (Thang)       | `tally.so/r/81dLar` (weekly plan)               | HTML text (Tally renders server-side)              |
| Jakobs Speiselokal     | `instagram.com/jakobsspeiselokal` (1st post)    | Playwright + Vision LLM (menu is an image)         |

## Architecture

```
scrape.py        → runs all scrapers, writes data/menus.json (atomically)
scrapers/        → one module per restaurant + common.py (HTTP, vision LLM, schema)
config.py        → paths, refresh interval, bind host/port
app.py           → Flask app, renders the UI, background thread scrapes every 4 h
templates/ static/ → frontend (vanilla HTML/CSS/JS, no build step)
data/menus.json  → generated (gitignored)
```

Common schema per restaurant:

```jsonc
{
  "id": "sudost", "name": "SUDOST", "kind": "week",   // week | day | weekly-rotating
  "period": "26.10. – 30.10.2026", "url": "...", "address": "...",
  "allergens_legend": {"1": "Gluten", ...},
  "days": { "2026-11-02": { "label": "Monday", "items": [
        { "name": "...", "description": "...", "price": "12,30 €",
          "price_takeaway": null, "tags": ["vegetarian"], "allergens": [1,2] } ] } },
  "status": "ok",  // ok | stale | error | unavailable
  "last_scraped": "2026-11-02T11:45:31+01:00", "error": null
}
```

`kind=weekly-rotating` (Bauchgefühl) has no day concept — the menu is shown
for every day.

## Setup (VM)

```bash
cd lunch.science
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # one-time, ~150 MB
```

Start:

```bash
.venv/bin/python app.py            # binds 0.0.0.0:8080, scrapes at start + every 4 h
```

One-off scrape only (e.g. for cron / debugging):

```bash
.venv/bin/python scrape.py                     # all sources
.venv/bin/python scrape.py sudost wichernhaus  # only selected ones
```

### LLM configuration (vision OCR for Kochwerk & Jakobs)

Two sources (Kochwerk, Jakobs Speiselokal) only publish their menu as an image
and therefore need a vision LLM. The configuration is stored **centrally in a
protected file** that only you can read:

```bash
cat > ~/.lunch_env <<'EOF'
# lunch.science LLM config – only this account may read it (chmod 600)
LUNCH_API_KEY=sk-...
# optional:
LUNCH_BASE_URL=https://api.openai.com/v1
LUNCH_MODEL=gpt-4o
EOF
chmod 600 ~/.lunch_env      # owner only: read/write
```

Resolution order per setting (file wins if the env var is empty):

1. Environment variable (`LUNCH_API_KEY` / `OPENAI_API_KEY`, …)
2. `~/.lunch_env`  → then legacy `~/.kochwerk_env`
3. Default

All accepted keys in the file:

| Key (file)                 | Env-var equivalent                 | Meaning          |
|----------------------------|------------------------------------|------------------|
| `LUNCH_API_KEY`            | `LUNCH_API_KEY` / `OPENAI_API_KEY` | API key          |
| `LUNCH_BASE_URL`           | `OPENAI_BASE_URL`                  | LLM endpoint     |
| `LUNCH_MODEL`              | `KOCHWERK_MODEL`                   | LLM model        |

> `chmod 600` = only the user who created the file (the app user on the VM)
> can read it. Other users get `Permission denied`.

### Environment variables (app)

| Variable                 | Default      | Meaning                                     |
|--------------------------|--------------|---------------------------------------------|
| `LUNCH_HOST`             | `0.0.0.0`    | Bind host                                   |
| `LUNCH_PORT`             | `8080`       | Port                                        |
| `LUNCH_REFRESH_SECONDS`  | `14400`      | Refresh interval (4 h)                      |
| `LUNCH_NO_STARTUP_SCRAPE`| `0`          | `=1` → no scrape at startup                 |

Without an API key, Kochwerk & Jakobs run with status **offline** (unavailable)
— all other sources are independent of this.

### Headless deployment (systemd, for the VM)

Running `python app.py` in the foreground dies when you close the terminal.
For the VM, run it as a **systemd service** instead — it starts on boot,
survives closing the terminal and logouts, and auto-restarts on crash.

One command (as root / with sudo, from a checkout of this repo):

```bash
sudo bash install-service.sh
```

The script creates a dedicated non-root user `lunch`, copies the repo to
`/opt/lunch.science`, builds the venv, installs the Playwright Chromium
browser, sets up `~/.lunch_env` (vision-LLM key), and installs + enables +
starts the service. The site is then up on `http://<vm>:8080`.

Useful commands after install:

```bash
systemctl status  lunch.science     # is it running?
journalctl -u lunch.science -f      # live logs
systemctl restart lunch.science     # after a code change (see below)
systemctl stop    lunch.science
```

To pick up code changes, redeploy (idempotent — re-runs the whole setup):

```bash
git pull && sudo bash install-service.sh
```

The unit file lives in the repo as `lunch.science.service`; `install-service.sh`
copies it to `/etc/systemd/system/` and patches `LUNCH_PORT`. Edit that file to
change the bind host/port, refresh interval, or resource limits.

> Manual / alternative: a **systemd *user* service** also works, but you must
> enable lingering (`sudo loginctl enable-linger <user>`) so it survives
> logout. The system service above avoids that step entirely.

## API

- `GET /`            → website
- `GET /api/menus`   → the full `menus.json`
- `GET /api/status`  → `{scraped_at, scraping, last_run, next_run, error, refresh_seconds}`
- `POST /api/scrape` → triggers a manual scrape

## Troubleshooting

- **Bauchgefühl empty/403:** Lieferando sits behind Cloudflare. Playwright
  (headless Chromium) must be installed (`playwright install chromium`).
  `--no-sandbox` may be required (the scraper already sets it).
- **Kochwerk / Jakobs offline:** No API key. Create `~/.lunch_env`
  (see above) with `LUNCH_API_KEY=...` and `chmod 600`, or set an env var.
  `chmod 600` ensures only the app user can read the file.
- **Police canteen empty:** No PDF for the current week
  (week <n> <year>) exists on the page yet.
- **Jakobs Speiselokal offline:** The menu is an Instagram image and needs
  a vision LLM key (like Kochwerk). Without a key → status **offline**.
- **SoulReis empty:** The Tally weekly plan only lists the days Thang is open.
  Closed days (e.g. Mon/Fri) are shown as "Closed"; days with no entry in the
  current week do not appear.
- **Data stale:** `last_scraped` older than ~3 h → yellow LED in the UI.

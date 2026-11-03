#!/usr/bin/env python3
"""lunch.science - Flask web app.

Serves the menu UI from data/menus.json and runs a background thread that
re-scrapes all sources every REFRESH_INTERVAL seconds (and once at startup).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template

import config
import scrape

log = logging.getLogger("lunch.app")

app = Flask(__name__)

_lock = threading.Lock()
_state = {"scraping": False, "last_run": None, "next_run": None, "error": None}
_thread: threading.Thread | None = None
_stop = threading.Event()


def _load_data() -> dict:
    if not config.DATA_FILE.exists():
        return {"scraped_at": None, "restaurants": []}
    try:
        with open(config.DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("could not read %s: %s", config.DATA_FILE, e)
        return {"scraped_at": None, "restaurants": []}


def _run_scrape_once() -> None:
    with _lock:
        if _state["scraping"]:
            log.info("scrape already running, skipping")
            return
        _state["scraping"] = True
        _state["error"] = None
    try:
        scrape.scrape(merge_existing=True)
        _state["last_run"] = datetime.now().isoformat(timespec="seconds")
    except Exception as e:  # noqa: BLE001
        log.exception("scrape failed")
        _state["error"] = str(e)
    finally:
        _state["scraping"] = False
        _state["next_run"] = (
            (datetime.now().timestamp() + config.REFRESH_INTERVAL)
            and datetime.fromtimestamp(
                time.time() + config.REFRESH_INTERVAL).isoformat(timespec="seconds"))


def _loop() -> None:
    if config.SCRAPE_ON_STARTUP:
        _run_scrape_once()
    while not _stop.is_set():
        _stop.wait(config.REFRESH_INTERVAL)
        if _stop.is_set():
            break
        _run_scrape_once()


def _ensure_thread() -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_loop, name="lunch-scrape", daemon=True)
        _thread.start()


@app.template_filter("fmt_price")
def _fmt_price(p):
    return p


@app.after_request
def _no_cache(resp):
    """Never let the browser cache the menu page or APIs.

    The index HTML embeds the menu data server-side, so a cached copy would
    show stale dishes (e.g. bowls that just went out of stock) until a hard
    refresh. The JSON APIs are polled, but no-cache keeps them honest too.
    Static assets (app.js/style.css) are revalidated via ETag by Werkzeug,
    which is fine.
    """
    if resp.content_type and resp.content_type.startswith("text/html"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    elif resp.content_type and resp.content_type.startswith("application/json"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.route("/")
def index():
    data = _load_data()
    return render_template("index.html", data=data, state=_state)


@app.route("/api/menus")
def api_menus():
    return jsonify(_load_data())


@app.route("/api/status")
def api_status():
    d = _load_data()
    return jsonify({
        "scraped_at": d.get("scraped_at"),
        "scraping": _state["scraping"],
        "last_run": _state["last_run"],
        "next_run": _state["next_run"],
        "error": _state["error"],
        "refresh_seconds": config.REFRESH_INTERVAL,
    })


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    threading.Thread(target=_run_scrape_once, daemon=True).start()
    return jsonify({"started": True})


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    config.ensure_data_dir()
    _ensure_thread()
    app.run(host=config.HOST, port=config.PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()

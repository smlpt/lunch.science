/* lunch.science - frontend logic */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("menu-data").textContent || "{}");
  var STATE = JSON.parse(document.getElementById("scrape-state").textContent || "{}");
  var RESTS = DATA.restaurants || [];
  // Day labels are language-dependent; read from the active dict each render.
  function dayLabel(i) { return dict().days[i]; }
  var content = document.getElementById("content");
  var tabsEl = document.getElementById("tabs");

  var today = new Date();
  var todayWd = (today.getDay() + 6) % 7;          // Mon=0
  var todayKey = isoDate(new Date());
  var current = "today";

  // Menu language: "de" (original) or "en" (LLM-translated *_en fields).
  // Persisted in localStorage so the choice sticks across reloads/polls.
  var LANG = (function () {
    try { return localStorage.getItem("lunch.lang") === "en" ? "en" : "de"; }
    catch (e) { return "de"; }
  })();
  function setLang(lang) {
    LANG = (lang === "en" ? "en" : "de");
    try { localStorage.setItem("lunch.lang", LANG); } catch (e) {}
    Array.prototype.forEach.call(
      document.querySelectorAll("#lang-toggle .lang-opt"),
      function (b) { b.classList.toggle("active", b.getAttribute("data-lang") === LANG); });
    renderChrome();                 // re-label header/footer/tabs for the language
    if (current) setTab(current);   // re-render the menu with the new language
    updateMeta();                   // re-render the status/refresh meta line too
  }

  // Re-label the static chrome that lives outside #content (header subtitle,
  // tab buttons, footer, page title, empty-state) for the active language.
  function renderChrome() {
    var d = document;
    d.documentElement.setAttribute("lang", LANG);
    d.title = t("title");
    var brandSub = d.getElementById("brand-sub");
    if (brandSub) brandSub.textContent = t("brand_sub");
    // Tab buttons: first two are Overview/Today, then Mon..Fri.
    var tabBtns = Array.prototype.slice.call(tabsEl.querySelectorAll(".tab"));
    tabBtns.forEach(function (b, i) {
      var day = b.getAttribute("data-day");
      if (day === "overview") b.textContent = t("overview");
      else if (day === "today") b.textContent = t("today");
      else b.textContent = dayLabel(parseInt(day, 10)).slice(0, 3);
    });
    var foot = d.getElementById("foot-left");
    if (foot) foot.textContent = t("foot");
    var empty = d.querySelector("#content .empty");
    if (empty) empty.textContent = t("empty");
  }
  // Pick the displayed value for a data field: EN falls back to the German
  // original when no *_en translation exists (graceful degradation).
  function L(obj, field) {
    if (!obj) return null;
    if (LANG === "en") {
      var en = obj[field + "_en"];
      if (en) return en;
    }
    return obj[field];
  }

  // UI string table. Every hardcoded label in the app lives here so the whole
  // UI (not just the menus) responds to the DE/EN toggle. t(key, ...args)
  // substitutes {0},{1},… into the chosen language's string.
  var I18N = {
    de: {
      overview: "Übersicht", today: "Heute",
      days: ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"],
      source: "Quelle ↗", loading: "lädt …",
      status_ok: "OK", status_stale: "veraltet", status_err: "Fehler",
      status_unavailable: "offline",
      scraping: "scrape läuft …", no_data: "noch keine Daten",
      updated: "aktualisiert {0} ({1})", ago_min: "vor {0} Min.", ago_lt1: "vor <1 Min.",
      refresh: "Aktualisierung alle {0} h · {1} Quellen",
      no_entry: "kein Eintrag für diesen Tag", closed: "Geschlossen", no_dishes: "keine Gerichte",
      allergens: "Allergene:", allergen_key: "ALLERGEN-LEGENDE",
      ov_restaurant: "Restaurant", ov_dishes: "Gerichte",
      ov_no_entry: "— kein Eintrag —", ov_error: "Fehler",
      offline: "offline — {0}", no_data_short: "keine Daten",
      err_unavailable: "nicht verfügbar", err_scrape: "Scrape fehlgeschlagen",
      takeaway: "zum Mitnehmen",
      foot: "lunch.science — interne Menü-Aggregation für Görlitz",
      empty: "Noch keine Daten. Ein Scrape läuft — gleich laden.",
      title: "lunch.science · Görlitz Menü-Tafel",
      brand_sub: "Görlitz · Menü-Beobachter"
    },
    en: {
      overview: "Overview", today: "Today",
      days: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
      source: "source ↗", loading: "loading …",
      status_ok: "OK", status_stale: "stale", status_err: "error",
      status_unavailable: "offline",
      scraping: "scraping …", no_data: "no data yet",
      updated: "updated {0} ({1})", ago_min: "{0} min ago", ago_lt1: "<1 min ago",
      refresh: "refresh every {0} h · {1} sources",
      no_entry: "no entry for this day", closed: "Closed", no_dishes: "no dishes",
      allergens: "Allergens:", allergen_key: "ALLERGEN KEY",
      ov_restaurant: "Restaurant", ov_dishes: "Dishes",
      ov_no_entry: "— no entry —", ov_error: "error",
      offline: "offline — {0}", no_data_short: "no data",
      err_unavailable: "not available", err_scrape: "scrape failed",
      takeaway: "takeaway",
      foot: "lunch.science — internal menu aggregation for Görlitz",
      empty: "No data yet. A scrape is running — refresh in a moment.",
      title: "lunch.science · Görlitz Menu Board",
      brand_sub: "Görlitz · Menu Observer"
    }
  };
  function dict() { return I18N[LANG] || I18N.de; }
  function t(key, a0, a1) {
    var s = (dict()[key] != null ? dict()[key] : I18N.de[key]);
    if (s == null) return key;
    if (a0 !== undefined) s = s.replace("{0}", a0);
    if (a1 !== undefined) s = s.replace("{1}", a1);
    return s;
  }

  function isoDate(d) {
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + "-" + pad(m) + "-" + pad(day);
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmt(dkey) {
    var p = dkey.split("-");
    return p[2] + "." + p[1] + "." + p[0];
  }

  function staleStatus(r) {
    if (r.status && r.status !== "ok") return r.status;
    if (!r.last_scraped) return "stale";
    var age = Date.now() - new Date(r.last_scraped).getTime();
    if (age > 3 * 3600 * 1000) return "stale";
    return "ok";
  }

  function statusWord(s) {
    return { ok: t("status_ok"), stale: t("status_stale"), err: t("status_err"),
             unavailable: t("status_unavailable"), unavail: t("status_unavailable") }[s] || s;
  }

  // Which day key to show for a tab value ("overview"/"today"/0..4).
  function tabDayKey(tab) {
    if (tab === "today") return todayKey;
    if (tab === "overview") return null;
    var wd = parseInt(tab, 10);
    var d = new Date(today);
    d.setDate(d.getDate() + (wd - todayWd));
    return isoDate(d);
  }

  function itemsForDay(rest, dkey) {
    // weekly-rotating (bauchgefuhl) always exposes its menu.
    if (rest.kind === "weekly-rotating") {
      var pd = rest.days && rest.days["always"];
      return pd ? pd.items : [];
    }
    var day = rest.days && rest.days[dkey];
    return day ? day.items : null;
  }

  // Full day object (items + is_open + note) for a restaurant on a day.
  function dayFor(rest, dkey) {
    if (rest.kind === "weekly-rotating") return rest.days && rest.days["always"];
    return rest.days && rest.days[dkey];
  }

  // Translate an allergen code via the restaurant legend (EN mode), else "A{n}".
  function allergenLabel(rest, code) {
    if (LANG === "en" && rest && rest.allergens_legend_en) {
      var w = rest.allergens_legend_en[String(code)];
      if (w) return w;
    }
    return "A" + code;
  }

  function dishHTML(it, rest) {
    var name = L(it, "name") || it.name;
    var h = '<div class="dish"><div class="row1"><span class="name">' + esc(name) + '</span>';
    var prices = [];
    if (it.price) prices.push('<span>' + esc(it.price) + '</span>');
    if (it.price_takeaway) prices.push('<span class="out">/ ' + esc(it.price_takeaway) + ' ' + esc(t("takeaway")) + '</span>');
    h += '<span class="price">' + (prices.join("") || "&nbsp;") + '</span></div>';
    var desc = L(it, "description");
    if (desc) h += '<div class="desc">' + esc(desc) + '</div>';
    var tags = [];
    // Show EN tags when available, else the German originals.
    var tagList = (LANG === "en" && it.tags_en) ? it.tags_en : (it.tags || []);
    tagList.forEach(function (t) {
      var key = (t === "vegetarisch" || t === "vegetarian" || t === "vegan") ? "veg" : "";
      tags.push('<span class="tag ' + key + '">' + esc(t) + '</span>');
    });
    (it.allergens || []).forEach(function (a) {
      tags.push('<span class="tag allergen">' + esc(allergenLabel(rest, a)) + '</span>');
    });
    var cat = L(it, "category");
    if (cat) tags.push('<span class="tag">' + esc(cat) + '</span>');
    if (tags.length) h += '<div class="tags">' + tags.join("") + '</div>';
    h += '</div>';
    return h;
  }

  function cardHTML(rest, dkey) {
    var st = staleStatus(rest);
    var items = itemsForDay(rest, dkey);
    var h = '<article class="card ' + (st === "ok" ? "" : st) + '">';
    h += '<div class="card-head"><h3>' + esc(rest.name) + '</h3>' +
         '<span class="status ' + st + '">' + statusWord(st) + '</span></div>';
    var sub = [];
    if (rest.address) sub.push(esc(L(rest, "address") || rest.address));
    if (rest.period) sub.push(esc(L(rest, "period") || rest.period));
    if (rest.url) sub.push('<a href="' + esc(rest.url) + '" target="_blank" rel="noopener">' + esc(t("source")) + '</a>');
    if (sub.length) h += '<div class="card-sub">' + sub.join(" · ") + '</div>';
    if (rest.status === "unavailable" || st === "unavail") {
      h += '<div class="errmsg">' + esc(rest.error || t("err_unavailable")) + '</div>';
    } else if (st === "err") {
      h += '<div class="errmsg">' + esc(rest.error || t("err_scrape")) + '</div>';
    } else {
      if (rest.note) h += '<div class="note">' + esc(L(rest, "note") || rest.note) + '</div>';
      var day = dayFor(rest, dkey);
      if (!items) {
        h += '<div class="card-sub">' + esc(t("no_entry")) + '</div>';
      } else if (day && day.is_open === false) {
        h += '<div class="closed">' + esc(L(day, "note") || day.note || t("closed")) + '</div>';
      } else {
        // A day can carry both a note (e.g. location & time for SoulReis) and
        // its dishes - render the note on top of the menu, not instead of it.
        if (day && day.note) h += '<div class="daynote">' + esc(L(day, "note") || day.note) + '</div>';
        if (!items.length) {
          h += '<div class="card-sub">' + esc(t("no_dishes")) + '</div>';
        } else {
          h += '<div class="dishes">' + items.map(function (it) {
            return dishHTML(it, rest);
          }).join("") + '</div>';
        }
      }
      var legend = (LANG === "en" && rest.allergens_legend_en)
        ? rest.allergens_legend_en : rest.allergens_legend;
      if (legend && Object.keys(legend).length) {
        var leg = Object.keys(legend).sort(function (a, b) {
          return parseInt(a) - parseInt(b);
        }).map(function (k) {
          return '<span class="li"><b>' + esc(k) + '</b> ' + esc(legend[k]) + '</span>';
        }).join('<span class="li-sep">·</span>');
        h += '<div class="legend">' + esc(t("allergens")) + leg + '</div>';
      }
    }
    h += '</article>';
    return h;
  }

  function renderDay(tab) {
    var dkey = tabDayKey(tab);
    var label = tab === "today"
      ? dayLabel(todayWd) + ", " + fmt(todayKey)
      : (tab === "overview" ? t("overview") : dayLabel(parseInt(tab, 10)));
    var html = '<div class="dayhead"><h2>' + esc(label) + '</h2>';
    if (dkey) html += '<span class="d">' + fmt(dkey) + '</span>';
    html += '<span class="periods">' + RESTS.map(function (r) {
      return r.period ? '<span class="p"><b>' + esc(r.name) + '</b> ' + esc(L(r, "period") || r.period) + '</span>' : "";
    }).join("") + '</span></div>';
    html += '<div class="cards">';
    RESTS.forEach(function (r) { html += cardHTML(r, dkey); });
    html += '</div>';
    html += legendBlock();
    content.innerHTML = html;
  }

  function renderOverview(tab) {
    var dkey = tabDayKey(tab) || todayKey;
    var html = '<div class="dayhead"><h2>' + esc(t("overview")) + '</h2>' +
      '<span class="d">' + esc(t("overview") + " · " + fmt(dkey)) + '</span></div>';
    html += '<div style="overflow-x:auto"><table class="ov"><thead><tr><th>' + esc(t("ov_restaurant")) + '</th>';
    html += '<th>' + esc(t("ov_dishes")) + '</th></tr></thead><tbody>';
    RESTS.forEach(function (r) {
      var items = itemsForDay(r, dkey);
      var day = dayFor(r, dkey);
      html += '<tr><td class="rest">' + esc(r.name) + '</td><td>';
      if (r.status === "unavailable" || staleStatus(r) === "unavail") {
        html += '<span class="none">' + esc(t("offline", r.error || t("no_data_short"))) + '</span>';
      } else if (staleStatus(r) === "err") {
        html += '<span class="none">' + esc(t("ov_error")) + '</span>';
      } else if (!items) {
        html += '<span class="none">' + esc(t("ov_no_entry")) + '</span>';
      } else if (day && day.is_open === false) {
        html += '<span class="none">' + esc(L(day, "note") || day.note || t("closed")) + '</span>';
      } else if (!items.length) {
        html += '<span class="none">' + esc(t("no_dishes")) + '</span>';
      } else {
        html += items.map(function (it) {
          return '<div class="item">' + esc(L(it, "name") || it.name) +
            (it.price ? ' <span class="p">(' + esc(it.price) + (it.price_takeaway ? ' / ' + esc(it.price_takeaway) : '') + ')</span>' : '') +
            '</div>';
        }).join("");
      }
      html += '</td></tr>';
    });
    html += '</tbody></table></div>';
    html += legendBlock();
    content.innerHTML = html;
  }

  function legendBlock() {
    var blocks = RESTS.filter(function (r) {
      var legend = (LANG === "en" && r.allergens_legend_en) ? r.allergens_legend_en : r.allergens_legend;
      return legend && Object.keys(legend).length;
    });
    if (!blocks.length) return "";
    var h = '<div class="legblock"><h4>' + esc(t("allergen_key")) + '</h4>';
    blocks.forEach(function (r) {
      var legend = (LANG === "en" && r.allergens_legend_en) ? r.allergens_legend_en : r.allergens_legend;
      h += '<div style="margin-top:6px"><b style="color:var(--ink)">' + esc(r.name) + '</b></div><div>';
      h += Object.keys(legend).sort(function (a, b) {
        return parseInt(a) - parseInt(b);
      }).map(function (k) {
        return '<span class="li"><b>' + esc(k) + '</b> ' + esc(legend[k]) + '</span>';
      }).join('<span class="li-sep">·</span>');
      h += '</div>';
    });
    h += '</div>';
    return h;
  }

  function setTab(tab) {
    current = tab;
    var dkey = tabDayKey(tab);
    Array.prototype.forEach.call(tabsEl.querySelectorAll(".tab"), function (b) {
      b.classList.toggle("active", b.getAttribute("data-day") === tab);
    });
    // mark "today" tab as active if a weekday tab equals today's weekday
    if (tab !== "today" && tab !== "overview" && parseInt(tab, 10) === todayWd) {
      // keep simple: only the clicked tab is highlighted.
    }
    if (tab === "overview") renderOverview(tab);
    else renderDay(tab);
  }

  tabsEl.addEventListener("click", function (e) {
    var b = e.target.closest(".tab");
    if (b) setTab(b.getAttribute("data-day"));
  });

  // DE/EN language toggle.
  var langToggle = document.getElementById("lang-toggle");
  if (langToggle) {
    langToggle.addEventListener("click", function (e) {
      var b = e.target.closest(".lang-opt");
      if (b) setLang(b.getAttribute("data-lang"));
    });
  }

  function updateMeta() {
    var led = document.getElementById("led");
    var txt = document.getElementById("meta-text");
    var foot = document.getElementById("foot-right");
    if (STATE.scraping || (STATE.error && false)) {
      led.className = "led scraping";
    } else if (STATE.error) {
      led.className = "led err";
    } else if (DATA.scraped_at) {
      // stale if any restaurant is stale and none ok
      var anyOk = RESTS.some(function (r) { return staleStatus(r) === "ok"; });
      led.className = "led " + (anyOk ? "ok" : "stale");
    } else {
      led.className = "led";
    }
    if (STATE.scraping) {
      txt.textContent = t("scraping");
    } else if (DATA.scraped_at) {
      var age = Math.round((Date.now() - new Date(DATA.scraped_at).getTime()) / 60000);
      txt.textContent = t("updated", DATA.scraped_at.replace("T", " ").slice(0, 16),
        age < 1 ? t("ago_lt1") : t("ago_min", age));
      foot.textContent = t("refresh", Math.round((STATE.refresh_seconds || 14400) / 3600),
        RESTS.length);
    } else {
      txt.textContent = t("no_data");
    }
  }

  // poll status so the LED/labels stay live across the 4h refresh
  function poll() {
    fetch("/api/status").then(function (r) { return r.json(); }).then(function (s) {
      STATE = s;
      var anyChanged = s.scraped_at && s.scraped_at !== DATA.scraped_at;
      updateMeta();
      if (anyChanged) {
        fetch("/api/menus").then(function (r) { return r.json(); }).then(function (d) {
          DATA = d;
          RESTS = d.restaurants || [];
          setTab(current);
          updateMeta();
        });
      }
    }).catch(function () {});
  }

  // initial render: default to "today", reflect the persisted language.
  Array.prototype.forEach.call(
    document.querySelectorAll("#lang-toggle .lang-opt"),
    function (b) { b.classList.toggle("active", b.getAttribute("data-lang") === LANG); });
  renderChrome();
  setTab("today");
  updateMeta();
  setInterval(poll, 20000);
})();

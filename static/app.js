/* lunch.science - frontend logic */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("menu-data").textContent || "{}");
  var STATE = JSON.parse(document.getElementById("scrape-state").textContent || "{}");
  var DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
  var RESTS = DATA.restaurants || [];
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
    if (current) setTab(current);   // re-render with the new language
  }
  // Pick the displayed value for a field: EN falls back to the German original
  // when no *_en translation exists (graceful degradation).
  function L(it, field) {
    if (!it) return null;
    if (LANG === "en") {
      var en = it[field + "_en"];
      if (en) return en;
    }
    return it[field];
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
    return { ok: "OK", stale: "stale", err: "error", unavailable: "offline",
             unavail: "offline" }[s] || s;
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
    if (it.price_takeaway) prices.push('<span class="out">/ ' + esc(it.price_takeaway) + ' takeaway</span>');
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
    if (rest.address) sub.push(esc(rest.address));
    if (rest.period) sub.push(esc(rest.period));
    if (rest.url) sub.push('<a href="' + esc(rest.url) + '" target="_blank" rel="noopener">source ↗</a>');
    if (sub.length) h += '<div class="card-sub">' + sub.join(" · ") + '</div>';
    if (rest.status === "unavailable" || st === "unavail") {
      h += '<div class="errmsg">' + esc(rest.error || "nicht verfügbar") + '</div>';
    } else if (st === "err") {
      h += '<div class="errmsg">' + esc(rest.error || "Scrape fehlgeschlagen") + '</div>';
    } else {
      if (rest.note) h += '<div class="note">' + esc(rest.note) + '</div>';
      var day = dayFor(rest, dkey);
      if (!items) {
        h += '<div class="card-sub">no entry for this day</div>';
      } else if (day && day.is_open === false) {
        h += '<div class="closed">' + esc(day.note || "Closed") + '</div>';
      } else {
        // A day can carry both a note (e.g. location & time for SoulReis) and
        // its dishes - render the note on top of the menu, not instead of it.
        if (day && day.note) h += '<div class="daynote">' + esc(day.note) + '</div>';
        if (!items.length) {
          h += '<div class="card-sub">no dishes</div>';
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
        }).join("");
        h += '<div class="legend">Allergens: ' + leg + '</div>';
      }
    }
    h += '</article>';
    return h;
  }

  function renderDay(tab) {
    var dkey = tabDayKey(tab);
    var label = tab === "today"
      ? DAY_LABELS[todayWd] + ", " + fmt(todayKey)
      : (tab === "overview" ? "Overview" : DAY_LABELS[parseInt(tab, 10)]);
    var html = '<div class="dayhead"><h2>' + esc(label) + '</h2>';
    if (dkey) html += '<span class="d">' + fmt(dkey) + '</span>';
    html += '<span class="periods">' + RESTS.map(function (r) {
      return r.period ? '<span class="p"><b>' + esc(r.name) + '</b> ' + esc(r.period) + '</span>' : "";
    }).join("") + '</span></div>';
    html += '<div class="cards">';
    RESTS.forEach(function (r) { html += cardHTML(r, dkey); });
    html += '</div>';
    html += legendBlock();
    content.innerHTML = html;
  }

  function renderOverview(tab) {
    var dkey = tabDayKey(tab) || todayKey;
    var html = '<div class="dayhead"><h2>Overview</h2>' +
      '<span class="d">Overview · ' + fmt(dkey) + '</span></div>';
    html += '<div style="overflow-x:auto"><table class="ov"><thead><tr><th>Restaurant</th>';
    html += '<th>Dishes</th></tr></thead><tbody>';
    RESTS.forEach(function (r) {
      var items = itemsForDay(r, dkey);
      var day = dayFor(r, dkey);
      html += '<tr><td class="rest">' + esc(r.name) + '</td><td>';
      if (r.status === "unavailable" || staleStatus(r) === "unavail") {
        html += '<span class="none">offline — ' + esc(r.error || "no data") + '</span>';
      } else if (staleStatus(r) === "err") {
        html += '<span class="none">error</span>';
      } else if (!items) {
        html += '<span class="none">— no entry —</span>';
      } else if (day && day.is_open === false) {
        html += '<span class="none">' + esc(day.note || "Closed") + '</span>';
      } else if (!items.length) {
        html += '<span class="none">no dishes</span>';
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
    var h = '<div class="legblock"><h4>' + (LANG === "en" ? "ALLERGEN KEY" : "ALLERGEN-LEGENDE") + '</h4>';
    blocks.forEach(function (r) {
      var legend = (LANG === "en" && r.allergens_legend_en) ? r.allergens_legend_en : r.allergens_legend;
      h += '<div style="margin-top:6px"><b style="color:var(--ink)">' + esc(r.name) + '</b></div><div>';
      h += Object.keys(legend).sort(function (a, b) {
        return parseInt(a) - parseInt(b);
      }).map(function (k) {
        return '<span class="li"><b>' + esc(k) + '</b> ' + esc(legend[k]) + '</span>';
      }).join("");
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
      txt.textContent = "scraping …";
    } else if (DATA.scraped_at) {
      var age = Math.round((Date.now() - new Date(DATA.scraped_at).getTime()) / 60000);
      txt.textContent = "updated " + DATA.scraped_at.replace("T", " ").slice(0, 16) +
        " (" + (age < 1 ? "<1 min" : age + " min") + " ago)";
      foot.textContent = "refresh every " + Math.round((STATE.refresh_seconds || 14400) / 3600) +
        " h · " + RESTS.length + " sources";
    } else {
      txt.textContent = "no data yet";
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
  setTab("today");
  updateMeta();
  setInterval(poll, 20000);
})();

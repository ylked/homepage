(function () {
  "use strict";

  var runtime = JSON.parse(document.getElementById("runtime").textContent);
  var root = document.documentElement;

  /* ------------------------------------------------------------ search */
  var search = document.getElementById("search");
  var cards = Array.prototype.slice.call(document.querySelectorAll(".card"));
  var sections = Array.prototype.slice.call(document.querySelectorAll("[data-category]"));
  var empty = document.getElementById("empty");

  function applyFilter() {
    var terms = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    var shown = 0;
    cards.forEach(function (card) {
      var haystack = card.dataset.search;
      var match = terms.every(function (t) { return haystack.indexOf(t) !== -1; });
      card.hidden = !match;
      if (match) shown++;
    });
    sections.forEach(function (section) {
      section.hidden = !section.querySelector(".card:not([hidden])");
    });
    empty.hidden = shown !== 0;
  }

  search.addEventListener("input", applyFilter);

  search.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      search.value = "";
      applyFilter();
      search.blur();
    } else if (event.key === "Enter") {
      /* Enter opens the first visible, linked result. */
      var first = document.querySelector(".card:not([hidden]) .card-link");
      if (first) first.click();
    }
  });

  document.addEventListener("keydown", function (event) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      search.focus();
    }
  });

  /* ------------------------------------------------------------- theme */
  var THEMES = ["auto", "dark", "light"];
  var toggle = document.getElementById("theme-toggle");

  function currentTheme() {
    return root.dataset.theme || "auto";
  }

  function setTheme(theme) {
    if (theme === "auto") delete root.dataset.theme;
    else root.dataset.theme = theme;
    try { localStorage.setItem("homepage-theme", theme); } catch (e) {}
    toggle.title = "Theme: " + theme;
  }

  toggle.title = "Theme: " + currentTheme();
  toggle.addEventListener("click", function () {
    var next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
    setTheme(next);
  });

  /* ------------------------------------------------------- uptime kuma */
  var kuma = runtime.kuma;
  if (!kuma) return;

  var STATES = { 0: "down", 1: "up", 2: "pending", 3: "maintenance" };
  var LABELS = { up: "Up", down: "Down", pending: "Pending", maintenance: "Maintenance", unknown: "Status unknown" };
  var base = kuma.url + "/api/status-page/";
  var summary = document.getElementById("status-summary");
  var watched = Array.prototype.slice.call(document.querySelectorAll(".card[data-monitor]"));
  var idsByName = null;

  function getJSON(url) {
    return fetch(url, { cache: "no-store", credentials: "omit" }).then(function (response) {
      if (!response.ok) throw new Error(url + " answered " + response.status);
      return response.json();
    });
  }

  /* Monitor names live on the status page; heartbeats are keyed by id. */
  function loadMonitorNames() {
    return getJSON(base + encodeURIComponent(kuma.slug)).then(function (page) {
      idsByName = {};
      (page.publicGroupList || []).forEach(function (group) {
        (group.monitorList || []).forEach(function (monitor) {
          idsByName[String(monitor.name).toLowerCase()] = String(monitor.id);
        });
      });
    });
  }

  function monitorId(ref) {
    if (/^\d+$/.test(ref)) return ref;
    return idsByName ? idsByName[ref.toLowerCase()] : undefined;
  }

  function describe(state, uptime, beat) {
    var parts = [LABELS[state]];
    if (typeof uptime === "number") parts.push((uptime * 100).toFixed(uptime >= 0.9995 ? 0 : 2) + "% (24h)");
    if (beat && typeof beat.ping === "number") parts.push(beat.ping + " ms");
    if (state === "down" && beat && beat.msg) parts.push(beat.msg);
    return parts.join(" · ");
  }

  function paint(data) {
    var beats = data.heartbeatList || {};
    var uptimes = data.uptimeList || {};
    var up = 0;
    var tracked = 0;
    var down = [];

    watched.forEach(function (card) {
      var dot = card.querySelector(".status");
      var id = monitorId(card.dataset.monitor);
      var list = id !== undefined ? beats[id] : undefined;
      var beat = list && list.length ? list[list.length - 1] : null;
      var state = beat ? STATES[beat.status] || "unknown" : "unknown";
      var text = state === "unknown" && id === undefined
        ? "No monitor named “" + card.dataset.monitor + "” on the status page"
        : describe(state, uptimes[id + "_24"], beat);

      dot.dataset.state = state;
      dot.title = text;
      dot.setAttribute("aria-label", text);

      if (state !== "unknown") tracked++;
      if (state === "up" || state === "maintenance") up++;
      if (state === "down") down.push(card.querySelector(".card-title").textContent.trim());
    });

    if (!tracked) {
      showSummary("error", "No status data");
      return;
    }
    var stamp = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (down.length) {
      showSummary("down", down.length + " down: " + down.join(", "), "Checked " + stamp);
    } else {
      showSummary("up", up + "/" + tracked + " up", "Checked " + stamp);
    }
  }

  function showSummary(state, text, title) {
    summary.hidden = false;
    summary.dataset.state = state;
    summary.textContent = text;
    summary.title = title || "";
  }

  function refresh() {
    var names = idsByName ? Promise.resolve() : loadMonitorNames();
    names
      .then(function () { return getJSON(base + "heartbeat/" + encodeURIComponent(kuma.slug)); })
      .then(paint)
      .catch(function (error) {
        /* Usually CORS or the status page being unreachable: keep dots neutral. */
        idsByName = null;
        showSummary("error", "Status unavailable", String(error && error.message || error));
        if (window.console) console.warn("[homepage] Uptime Kuma:", error);
      });
  }

  if (watched.length) {
    refresh();
    setInterval(function () {
      if (!document.hidden) refresh();
    }, kuma.refresh * 1000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refresh();
    });
  }
})();

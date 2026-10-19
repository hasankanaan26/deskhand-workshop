/* Deskhand Lab — setup: health, the Setup popover, one-click service start,
   and the checkpoint cards (which show each checkpoint's readiness). */

/* Owns S.health, S.starting and S.flagDraft. GET /api/health describes the
   LLM credentials, the mock order API, every checkpoint service found on
   the probed ports, and the model picker's choices. views.js polls it every
   10s; each answer redraws the top-bar dots, the cards, the model picker
   and an open Setup popover, then emits "health" for the views' hints.
   Starting and stopping go through POST /api/services/{name}/start|stop;
   the lab server owns the processes, and the page only asks.
   Reads from L: S, $, el, add, getJSON, postJSON, chip, cmdBlock, on, emit. */
(function () {
  "use strict";
  var S = L.S, $ = L.$, el = L.el, add = L.add;

  /* ── health ────────────────────────────────────────────── */

  // When the lab server is unreachable, S.health becomes null and the top bar
  // shows a single red dot. Every renderer treats null health as "unknown".
  function loadHealth() {
    return L.getJSON("/api/health").then(function (h) {
      S.health = h;
      renderDots();
      if (!$("setup-pop").hidden && !editingPop()) renderPop();
      L.emit("health", h);
    }).catch(function () {
      S.health = null;
      $("setup-dots").textContent = "";
      add($("setup-dots"), dot("down", "lab server unreachable"));
    });
  }

  function dot(state, title) {
    var d = el("span", "dot " + state);
    if (title) d.title = title;
    return d;
  }

  // The health entry for one checkpoint. 00 and 01 have one too (readiness
  // and provider notes), with kind other than "http".
  function serviceOf(cp) {
    return ((S.health || {}).services || []).filter(function (s) { return s.checkpoint === cp; })[0] || null;
  }

  // The status dots inside the Setup button: LLM, mock API, then one per
  // service. A "warn" dot means running with flags (or chaos), or starting.
  function renderDots() {
    var h = S.health, box = $("setup-dots");
    box.textContent = "";
    if (!h) return;
    add(box, dot(h.llm.ready ? "up" : "down", "LLM " + h.llm.provider + " · " + (h.llm.deployment || "no deployment")));
    var mockFlags = h.mock_api.flags || [];
    add(box, dot(!h.mock_api.up ? "down" : mockFlags.length ? "warn" : "up", "mock order API " + (h.mock_api.up ? "up" : "down") + (mockFlags.length ? " · " + flagText(mockFlags) : "")));
    if (h.server && h.server.stale) add(box, dot("warn", "lab/server.py changed after the lab started — restart it"));
    h.services.forEach(function (s) {
      if (s.kind !== "http") return;
      var fl = s.flags || [];
      add(box, dot(S.starting[s.checkpoint] || (s.up && fl.length) ? "warn" : s.up ? "up" : "off",
        "checkpoint " + s.checkpoint + (s.up ? " running" : " not running") + (fl.length ? " · " + flagText(fl) : "")));
    });
  }

  /* ── start-time flags ──────────────────────────────────────
     Chosen here, passed by the server as the environment of that one process
     (whitelisted there; .env is never edited). Drafts live in memory only: a
     crash switch must never come back on by itself after a reload. */

  var FLAG_HELP = {
    crash_after_refund: "DESKHAND_CRASH_AFTER_REFUND=1: the service exits right after the refund POST, before saving state. Start it again without the flag: recovery re-runs execute_actions and the same Idempotency-Key gets the same refund back.",
    kill_switch: "DESKHAND_KILL_SWITCH=1: every run stops before its next node with KILL_SWITCH_ENGAGED and goes to a human. Runs paused or mid-flight resume straight into it.",
    budget_usd: "DESKHAND_BUDGET_USD_PER_RUN: the per-run cap in USD. 0.001 trips BUDGET_EXCEEDED on the first LLM call."
  };
  var CHAOS_HELP = {
    none: "none: behaves", flaky: "flaky: half the calls fail with 500", error: "error: every call fails with 500",
    timeout: "timeout: every call hangs 30s", inconsistent: "inconsistent: lookups drop total_usd"
  };

  function flagText(flags) { return (flags || []).map(function (f) { return f.label; }).join(", "); }

  // The flags ticked for the next start of one service (or "mock").
  function draft(name) { return (S.flagDraft = S.flagDraft || {})[name] || (S.flagDraft[name] = {}); }

  // The body of POST /api/services/{name}/start for the flags ticked in Setup.
  function flagBody(name) {
    var d = draft(name), body = {};
    if (name === "mock") { if (d.chaos && d.chaos !== "none") body.chaos = d.chaos; return body; }
    if (d.crash_after_refund) body.crash_after_refund = true;
    if (d.kill_switch) body.kill_switch = true;
    if (d.budget_usd !== undefined && d.budget_usd !== "") body.budget_usd = Number(d.budget_usd);
    return body;
  }

  // True while focus is in a popover input. The 10s health poll would
  // otherwise rebuild the popover under the cursor and drop what was typed.
  function editingPop() {
    var a = document.activeElement;
    return !!a && $("setup-pop").contains(a) && /^(INPUT|SELECT)$/.test(a.tagName);
  }

  // Badges for a running service's flags; "from .env" / "from the shell" when the lab did not set them.
  function flagBadges(flags, lead) {
    if (!flags || !flags.length) return null;
    var row = el("div", "flag-badges");
    add(row, el("span", "warn", lead));
    flags.forEach(function (f) {
      row.appendChild(L.chip(f.label + (f.source === "lab" ? "" : " (from " + (f.source === "shell" ? "the lab's shell" : ".env") + ")"), "chip-flag", f.var + "=" + f.value));
    });
    return row;
  }

  // Checkboxes / inputs for the flags this service reads (the server says which).
  function flagControls(name, fields) {
    var row = el("div", "flag-row");
    var d = draft(name);
    if (name === "mock") {
      var lab = el("label", null, "chaos");
      var sel = el("select");
      sel.setAttribute("aria-label", "MOCK_CHAOS for the next start");
      ((S.health && S.health.mock_api.chaos_modes) || ["none"]).forEach(function (m) {
        var o = el("option", null, CHAOS_HELP[m] || m);
        o.value = m;
        sel.appendChild(o);
      });
      sel.value = d.chaos || "none";
      sel.onchange = function () { d.chaos = sel.value; };
      lab.appendChild(sel);
      add(row, lab, el("span", "tiny", "MOCK_CHAOS for this process only; Start with “none” to go back"));
      return row;
    }
    if (!fields || !fields.length) return null;
    add(row, el("span", null, "start with:"));
    fields.forEach(function (f) {
      var lab = el("label");
      lab.title = FLAG_HELP[f] || "";
      if (f === "budget_usd") {
        var inp = el("input");
        inp.type = "number"; inp.min = "0.0001"; inp.max = "5"; inp.step = "0.001";
        inp.placeholder = String((S.health && S.health.llm.budget_per_run) || "0.25");
        inp.value = d.budget_usd || "";
        inp.setAttribute("aria-label", "per-run budget in USD (DESKHAND_BUDGET_USD_PER_RUN); empty keeps .env");
        inp.oninput = function () { d.budget_usd = inp.value; };
        add(lab, "budget $", inp, el("span", "tiny", "/run"));
      } else {
        var cb = el("input");
        cb.type = "checkbox";
        cb.checked = !!d[f];
        cb.onchange = function () { d[f] = cb.checked; };
        add(lab, cb, f === "crash_after_refund" ? "crash after refund" : "kill switch");
      }
      row.appendChild(lab);
    });
    return row;
  }

  /* ── model picker ──────────────────────────────────────── */
  // POST /api/model changes which model the lab passes to the processes it
  // starts, and restarts the services it manages so they pick it up. .env is
  // not edited. A service started in a terminal keeps its own model; Setup
  // flags that as model_mismatch. switching blocks the health poll from
  // redrawing the menu mid-switch.

  var switching = false;

  function renderModelPick() {
    var h = S.health, sel = $("model-pick");
    if (!h || switching) return;
    if (!h.model) {  // a lab server started before the picker existed
      sel.textContent = "";
      sel.appendChild(el("option", null, "restart the lab to switch models"));
      sel.disabled = true;
      sel.title = "This lab server predates the model picker. Stop it (Ctrl+C) and run: py lab\\server.py";
      return;
    }
    sel.disabled = false;
    sel.title = "";
    if (document.activeElement === sel && sel.options.length) return;  // don't yank an open menu
    sel.textContent = "";
    h.model.options.forEach(function (o) {
      var opt = el("option", null, o.label + (o.model ? " · " + o.model : "") + (o.ready ? "" : " (no key)"));
      opt.value = o.id;
      opt.disabled = !o.ready && o.id !== h.model.choice;
      opt.title = o.ready ? o.provider : (o.not_ready || "fill .env first");
      sel.appendChild(opt);
    });
    sel.value = h.model.choice;
  }

  $("model-pick").onchange = function () {
    var sel = $("model-pick"), status = $("model-status"), choice = sel.value;
    switching = true;
    sel.disabled = true;
    var restarting = ((S.health || {}).services || []).some(function (s) { return s.managed; });
    status.textContent = restarting ? "restarting services…" : "";
    L.postJSON("/api/model", { choice: choice }).then(function (r) {
      var failed = Object.keys(r.failed || {});
      status.textContent = failed.length ? "could not restart " + failed.join(", ") + " — see Setup" : "";
    }).catch(function (e) {
      status.textContent = "";
      alert("Could not switch the model: " + e.message);
    }).then(function () {
      switching = false;
      sel.disabled = false;
      sel.blur();  // so the refresh below re-renders it
      return loadHealth();
    });
  };

  L.on("health", renderModelPick);

  /* ── start / stop ──────────────────────────────────────── */

  // opts: a 02 runtime string, or {runtime?, chaos?, crash_after_refund?, kill_switch?, budget_usd?}.
  // Without flags the service starts plain: that is how a flag is switched off.
  // S.starting[name] makes the dots and cards show a spinner until the
  // server answers; the start call returns once the service is up or failed.
  // Health is reloaded on both paths, so the cards show the real result.
  function startService(name, opts) {
    var body = typeof opts === "string" ? { runtime: opts } : (opts || {});
    S.starting[name] = true;
    renderDots();
    L.emit("health", S.health);
    return L.postJSON("/api/services/" + name + "/start", body)
      .then(function (r) { return loadHealth().then(function () { return r; }); })
      .catch(function (e) {
        if (/^Not Found$/i.test(e.message)) e = new Error("the lab server is out of date (no /api/services endpoint) — restart it with `py lab\\server.py` from the repo root");
        return loadHealth().then(function () { throw e; });
      })
      .then(function (r) { delete S.starting[name]; renderDots(); L.emit("health", S.health); return r; },
        function (e) { delete S.starting[name]; renderDots(); L.emit("health", S.health); throw e; });
  }

  function stopService(name) {
    return L.postJSON("/api/services/" + name + "/stop").then(function (r) { return loadHealth().then(function () { return r; }); });
  }

  /* ── setup popover ─────────────────────────────────────── */
  // Rebuilt from S.health on every open and every poll (unless an input has
  // focus). Rows: a stale-server warning, the LLM, the mock order API, a note
  // on 00/01, then one row per HTTP service with Start/Stop, start-time
  // flags, the terminal command and the service's own log.

  function togglePop() {
    var pop = $("setup-pop");
    pop.hidden = !pop.hidden;
    if (!pop.hidden) renderPop();
  }

  // 02 gets two Start buttons, one per runtime (plain loop or LangGraph).
  // stopPropagation keeps the document click handler below from closing
  // the popover.
  function startButtons(name, label) {
    var wrap = el("span", "chips");
    function btn(text, runtime) {
      var b = el("button", "btn btn-sm btn-primary", text);
      b.type = "button";
      b.onclick = function (e) {
        e.stopPropagation();
        b.disabled = true; b.textContent = "starting…";
        var body = flagBody(name);
        if (runtime) body.runtime = runtime;
        startService(name, body).then(function (r) {
          S.flagDraft[name] = {};  // flags are per start: the next Start is plain unless ticked again
          if (r.note) flash(r.note, /NOT applied/.test(r.note));
          else if (r.flags && r.flags.length) flash(label + " started with " + flagText(r.flags) + ". Stop and Start it without flags to go back to plain.");
        }).catch(function (err) { flash("could not start " + label + ": " + err.message, true); });
      };
      return b;
    }
    if (name === "02") add(wrap, btn("Start · plain", "plain"), btn("Start · LangGraph", "langgraph"));
    else add(wrap, btn("Start"));
    return wrap;
  }

  function stopButton(name) {
    var b = el("button", "btn btn-sm", "Stop");
    b.type = "button";
    b.onclick = function (e) {
      e.stopPropagation();
      b.disabled = true; b.textContent = "stopping…";
      stopService(name).then(function (r) {
        if (r.note) flash(r.note, r.status !== "stopped");
      }).catch(function (err) { flash("could not stop: " + err.message, true); });
    };
    return b;
  }

  // A message pinned to the top of the popover for 6 seconds.
  var flashTimer = null;
  function flash(text, bad) {
    var pop = $("setup-pop");
    var old = pop.querySelector(".flash");
    if (old) old.remove();
    var n = el("div", "note-box flash " + (bad ? "bad" : "info"), text);
    pop.insertBefore(n, pop.firstChild);
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { n.remove(); }, 6000);
  }

  function renderPop() {
    var h = S.health, pop = $("setup-pop");
    pop.textContent = "";
    if (!h) { pop.textContent = "lab server unreachable"; return; }
    if (h.server && h.server.stale) {
      var stale = el("div", "note-box warn");
      add(stale, el("div", null, "lab/server.py changed after this lab server started, so the page may call endpoints it does not have yet. Stop it (Ctrl+C) and start it again from the repo root:"));
      add(stale, L.cmdBlock({ label: "Restart the lab", cmd: h.server.restart }));
      pop.appendChild(stale);
    }

    var r1 = el("div", "row");
    var h1 = el("div", "row-head");
    add(h1, el("h3", null, dot(h.llm.ready ? "up" : "down"), "LLM"), el("span", "muted small", h.llm.provider + " · " + (h.llm.deployment || "no deployment") + (h.llm.strong_deployment ? " · plan model " + h.llm.strong_deployment : "")));
    add(r1, h1);
    add(r1, el("div", "muted tiny", h.llm.ready ? "Credentials read from the repo .env by name only; the lab never shows or logs keys. Per-run budget for checkpoint 04: $" + h.llm.budget_per_run + "."
      : "Fill .env (see .env.example): a key" + (h.llm.provider === "azure" ? ", an endpoint and a deployment name" : " and a model") + " for " + h.llm.provider + "."));
    pop.appendChild(r1);

    var r2 = el("div", "row");
    var h2 = el("div", "row-head");
    add(h2, el("h3", null, dot(h.mock_api.up ? "up" : "down"), "Mock order API"), el("span", "muted small", h.mock_api.up ? "up at " + h.mock_api.url : "down · needed by checkpoints 02–04"));
    var right2 = el("div", "right");
    if (h.mock_api.up) right2.appendChild(stopButton("mock"));  // also stops one started in a terminal
    else right2.appendChild(startButtons("mock", "the mock API"));
    add(h2, right2);
    add(r2, h2);
    if (h.mock_api.up) {
      var mb = flagBadges(h.mock_api.flags, "running with");
      if (mb) add(r2, mb, el("div", "muted tiny", "Every lookup and refund misbehaves as above. Stop, then Start with chaos “none” to go back."));
    } else add(r2, flagControls("mock"));
    add(r2, el("div", "muted tiny", "Stands in for the order system: lookups, refunds with idempotency keys, chaos modes. In-memory; restart it to forget refunds. Checkpoint 00 never calls it — that's incident #6."));
    var d2 = el("details");
    add(d2, el("summary", null, "start it in a terminal instead"), L.cmdBlock({ label: "Own terminal, leave it running", cmd: h.mock_api.start }), L.cmdBlock({ label: "Check it", cmd: h.mock_api.check }));
    r2.appendChild(d2);
    pop.appendChild(r2);

    var r0 = el("div", "row");
    add(r0, el("div", "muted small", "Checkpoints 00 and 01 have no server: the lab runs them as a subprocess on demand."));
    var v1 = serviceOf("00");
    if (v1 && v1.provider_note) add(r0, el("div", (v1.ready ? "muted" : "warn") + " tiny", "Checkpoint 00: " + v1.provider_note + (v1.ready ? "." : ", and OPENAI_API_KEY is not set.")));
    pop.appendChild(r0);

    h.services.forEach(function (s) {
      if (s.kind !== "http") return;
      var cp = S.cpById[s.checkpoint] || {};
      var r = el("div", "row");
      var hh = el("div", "row-head");
      var status = S.starting[s.checkpoint] ? "starting…" : s.up ? "running at " + s.url + (s.runtime ? " · " + s.runtime + " runtime" : "") + (s.managed ? "" : " · started outside the lab") : "not running";
      add(hh, el("h3", null, dot(S.starting[s.checkpoint] ? "warn" : s.up ? "up" : "off"), "Checkpoint " + s.checkpoint + " · " + (cp.name || "")), el("span", "muted small", status));
      var right = el("div", "right");
      if (S.starting[s.checkpoint]) right.appendChild(el("span", "spinner"));
      else if (s.up) { if (s.managed) right.appendChild(stopButton(s.checkpoint)); }
      else right.appendChild(startButtons(s.checkpoint, "checkpoint " + s.checkpoint));
      add(hh, right);
      add(r, hh);
      if (s.up) {
        var fb = flagBadges(s.flags, "running with");
        if (fb) add(r, fb, el("div", "muted tiny", "These hold for every run on this service until it restarts. Stop, then Start without flags to go back to plain."));
        else if (!s.managed) add(r, el("div", "muted tiny", "Started outside the lab, so the lab cannot see its flags (DESKHAND_CRASH_AFTER_REFUND and friends): check its terminal."));
      } else if (!S.starting[s.checkpoint]) add(r, flagControls(s.checkpoint, s.flag_fields));
      if (!s.up && s.exit_code !== null && s.exit_code !== undefined) add(r, el("div", "bad small", "the last start exited with code " + s.exit_code + " — see the service log below"));
      if (!s.up && s.last_flags && s.last_flags.length) add(r, el("div", "warn tiny", "That process ran with " + flagText(s.last_flags) + ". Start leaves them off unless you tick them again."));
      if (!h.mock_api.up) add(r, el("div", "warn tiny", "needs the mock order API: start it first"));
      if (s.model_mismatch) add(r, el("div", "warn tiny", s.managed
        ? "still on the previous model: the restart failed — see the service log below"
        : "started outside the lab, so it ignores the model picker and calls whatever .env says. Stop it in its terminal and press Start here to run it on the picked model."));
      if (s.needs_index) add(r, el("div", "muted tiny", "No policy index yet: Start will run `py ingest.py` once before the server (embeds the six policy docs, well under a cent)."));
      if (s.checkpoint === "02") add(r, el("div", "muted tiny", "Two runtimes for the same nodes: plain is the hand-written loop (Session 4), LangGraph the Session 5 migration. The HTTP contract and the events are identical."));
      var d = el("details");
      add(d, el("summary", null, "start it in a terminal instead"), L.cmdBlock({ label: "Own terminal, leave it running", cmd: s.start }));
      r.appendChild(d);
      // Fetched when opened, not on every redraw.
      var dl = el("details");
      var logBox = el("pre", "code short", "…");
      add(dl, el("summary", null, "service log (what it printed)"), logBox);
      dl.ontoggle = function () {
        if (!dl.open) return;
        L.getJSON("/api/services/" + s.checkpoint + "/log").then(function (lg) {
          logBox.textContent = lg.managed ? (lg.lines.join("\n") || "(nothing yet)") : "The lab did not start this service, so it has no log for it. Look at its own terminal.";
          logBox.scrollTop = logBox.scrollHeight;
        }).catch(function (e) { logBox.textContent = e.message; });
      };
      r.appendChild(dl);
      pop.appendChild(r);
    });
    add(pop, el("div", "muted tiny", "Services are found on ports " + h.probed_ports.join(", ") + " by their FastAPI title (LAB_SERVICE_PORTS to change). Services the lab starts stop with it."));
  }

  // A click anywhere outside the popover and its button closes it.
  document.addEventListener("click", function (e) {
    var pop = $("setup-pop");
    if (!pop.hidden && !pop.contains(e.target) && !$("setup-btn").contains(e.target)) pop.hidden = true;
  });
  $("setup-btn").onclick = function (e) { e.stopPropagation(); togglePop(); };

  /* ── checkpoint cards ──────────────────────────────────── */
  // The row of five checkpoint cards appears on Run, Compare and Repeat.
  // views.js registers each row with a getter and setter for its own piece
  // of S, and this file owns the drawing: every row is rebuilt on each
  // health poll and on each click, so a card always shows live status.

  var cardSets = [];

  // mode "single": get() -> id, set(id); mode "multi": get() -> {id: bool}, set(id, bool)
  function registerCards(box, mode, get, set) {
    cardSets.push({ box: box, mode: mode, get: get, set: set });
  }

  // The status line at the bottom of a card, with a start button when the
  // service is down. Clicking it starts the service without selecting the card.
  function cardStatus(cp) {
    var h = S.health;
    var wrap = el("div", "cp-status");
    if (!h) { add(wrap, dot("off"), "checking…"); return wrap; }
    if (cp.kind !== "http") {
      var sub = serviceOf(cp.id);
      var ok = sub && sub.ready !== undefined ? sub.ready : h.llm.ready;
      add(wrap, dot(ok ? "up" : "down"), ok ? "ready — runs on demand" + (sub && sub.provider_note ? " on " + sub.model : "") : (sub && sub.not_ready) || "fill .env first");
      if (sub && sub.provider_note) wrap.title = sub.provider_note;
      return wrap;
    }
    var s = serviceOf(cp.id);
    if (S.starting[cp.id]) { add(wrap, el("span", "spinner"), " starting…"); return wrap; }
    if (s && s.up) {
      add(wrap, dot(h.mock_api.up && !(s.flags || []).length ? "up" : "warn"), "running :" + s.ports[0] + (s.runtime ? " · " + s.runtime : ""));
      (s.flags || []).forEach(function (f) { wrap.appendChild(L.chip(f.label, "chip-flag", f.var + "=" + f.value)); });
      (h.mock_api.flags || []).forEach(function (f) { wrap.appendChild(L.chip("mock " + f.label, "chip-flag", f.var + "=" + f.value)); });
      if (!h.mock_api.up) add(wrap, el("span", "warn", "· mock API down"));
      if (s.model_mismatch) add(wrap, el("span", "warn", "· not on the picked model (see Setup)"));
      return wrap;
    }
    add(wrap, dot("off"), "not running");
    if (cp.id === "02") {
      ["plain", "langgraph"].forEach(function (rt) {
        var b = el("button", "btn btn-sm", "start · " + (rt === "plain" ? "plain" : "LangGraph"));
        b.type = "button";
        b.onclick = function (e) { e.stopPropagation(); b.disabled = true; startService(cp.id, rt).catch(function (err) { alert("Could not start checkpoint 02: " + err.message); }); };
        wrap.appendChild(b);
      });
    } else {
      var b = el("button", "btn btn-sm", "start");
      b.type = "button";
      b.onclick = function (e) { e.stopPropagation(); b.disabled = true; startService(cp.id).catch(function (err) { alert("Could not start checkpoint " + cp.id + ": " + err.message); }); };
      wrap.appendChild(b);
    }
    return wrap;
  }

  function renderCards() {
    cardSets.forEach(function (set) {
      set.box.textContent = "";
      S.checkpoints.forEach(function (cp) {
        var on = set.mode === "single" ? set.get() === cp.id : !!set.get()[cp.id];
        var card = el("button", "cp-card" + (on ? " on" : ""));
        card.type = "button";
        card.dataset.cp = cp.id;
        card.setAttribute("aria-pressed", String(on));
        add(card, el("div", "cp-num", cp.id + " · " + cp.sessions), el("div", "cp-title", cp.name), el("div", "cp-blurb", cp.blurb), cardStatus(cp));
        if (set.mode === "multi") card.appendChild(el("span", "check", on ? "✓" : ""));
        card.onclick = function () {
          if (set.mode === "single") set.set(cp.id); else set.set(cp.id, !on);
          renderCards();
          // renderCards rebuilt every card: keep keyboard focus on this one.
          var again = set.box.querySelector('[data-cp="' + cp.id + '"]');
          if (again) again.focus();
        };
        set.box.appendChild(card);
      });
    });
  }

  L.on("health", renderCards);
  L.on("tickets", renderCards);

  L.loadHealth = loadHealth;
  L.startService = startService;
  L.stopService = stopService;
  L.serviceOf = serviceOf;
  L.flagText = flagText;
  L.registerCards = registerCards;
  L.renderCards = renderCards;
})();

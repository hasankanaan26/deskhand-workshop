/* Cadence as steps (cadence-as-steps.html): Nimbus's three schedules drawn as one workflow,
   every rule copied into every stage, for comparison with policies.json. A drawing: nothing
   here calls the engine, the API or a model, so the page also works opened from disk.

   The schedules (days, channels, tones, quiet hours, caps) are policies.json's. The drift
   entries are the made-up history of a workflow edited by hand for a year; each is marked
   on the canvas with its number.

   Theme and notes share the console's localStorage keys (dunning.*) and accept
   ?theme=light&notes=1. Everything is rendered with textContent. */
(function () {
  "use strict";

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function store(key, value) {
    try { if (value === undefined) return window.localStorage.getItem("dunning." + key); window.localStorage.setItem("dunning." + key, value); }
    catch (e) { return null; }
    return null;
  }

  /* ── the schedules, as policies.json has them ─────────── */

  var ALL_REGIONS = ["Americas", "Europe", "Asia-Pacific"];
  var INTENTS = ["says it paid", "dispute", "promise", "stop contact", "wrong contact", "other"];

  var LANES = [
    { id: "retail", tenant: "nimbus-retail", name: "Retail invoices", version: 4,
      quiet: ["21:00", "08:00"], cap: 3, weekend: true, regions: ALL_REGIONS,
      stages: [
        { name: "soft_reminder", day: -2, channel: "email", tone: "friendly" },
        { name: "first_notice", day: 3, channel: "email", tone: "formal" },
        { name: "second_notice", day: 10, channel: "sms+email", tone: "firm", cap: 2 },
        { name: "final_notice", day: 21, channel: "email", tone: "final", human: true }
      ] },
    { id: "business", tenant: "nimbus-business", name: "Business accounts", version: 2,
      quiet: ["20:00", "09:00"], cap: 1, weekend: true, regions: ["Americas", "Europe"],
      stages: [
        { name: "first_notice", day: 5, channel: "email", tone: "formal" },
        { name: "second_notice", day: 14, channel: "email", tone: "firm" },
        { name: "final_notice", day: 30, channel: "email", tone: "final", human: true, quiet: ["21:00", "08:00"] }
      ] },
    { id: "subscriptions", tenant: "nimbus-subscriptions", name: "Care+ subscriptions", version: 1,
      quiet: ["22:00", "07:00"], cap: 2, weekend: false, regions: ALL_REGIONS,
      stages: [
        { name: "payment_failed", day: 0, channel: "email", tone: "friendly", weekend: true },
        { name: "retry_notice", day: 2, channel: "sms", tone: "formal", weekend: true },
        { name: "second_retry", day: 4, channel: "sms", tone: "firm" },
        { name: "last_call", day: 6, channel: "email", tone: "firm" },
        { name: "suspension_notice", day: 9, channel: "email", tone: "final", human: true }
      ] }
  ];

  var V5_STAGE = { name: "sms_heads_up", day: -1, channel: "sms", tone: "friendly", added: true };

  var DRIFTS = [
    { n: 1, lane: "business", stage: "final_notice", node: "region",
      title: "Business quiet hours, copied from retail",
      text: "Business v2 moved quiet hours to 20:00–09:00. Someone updated the first two stages and missed the third, so Umbrella's final notice can land at 08:00 in Paris." },
    { n: 2, lane: "retail", stage: "second_notice", node: "cap",
      title: "A weekly cap of 2 in one stage",
      text: "Lowered during a complaint, in the one stage that was open on screen. The other three retail stages still allow 3." },
    { n: 3, lane: "business", stage: "first_notice", node: "region",
      title: "No Asia-Pacific branch",
      text: "Asia-Pacific was added to retail and subscriptions when Tailspin signed up. Business has no branch, so Koala Freight in Sydney takes the default path: no quiet-hours check, a notice at 03:00." },
    { n: 4, lane: "subscriptions", stage: "retry_notice", node: "weekend",
      title: "A weekend check subscriptions should not have",
      text: "Subscriptions never pause for the weekend. The lane was copied from retail and the check was deleted from three stages, not five: Proseware's Sunday retry notice waits until Monday." },
    { n: 5, lane: "retail", stage: "first_notice", node: "load",
      title: "The balance is read before the wait",
      text: "The invoice is loaded, then the run waits for 08:00 in the customer's time. Acme pays at 07:00; the notice still asks for $240.00. The engine reads the case again on every wake-up." },
    { n: 6, lane: "retail", stage: "final_notice", node: "log",
      title: "Which rules sent this?",
      text: "A run waiting days for a person's yes keeps the definition it started on. Deploy a change and runs in flight use the old boxes, new runs the new ones, and the log line cannot say which. The engine writes the policy version on every event." }
  ];

  function dLabel(d) { return d === 0 ? "D" : d > 0 ? "D+" + d : "D" + d; }
  function words(name) { return name.replace(/_/g, " "); }

  /* ── one stage → its boxes ────────────────────────────── */

  function boxesFor(lane, stage, next) {
    var quiet = stage.quiet || lane.quiet;
    var cap = stage.cap || lane.cap;
    var weekend = stage.weekend !== undefined ? stage.weekend : lane.weekend;
    var b = [];
    b.push({ key: "wait", type: "wait", text: "Wait until " + dLabel(stage.day) + ", 00:00 UTC" });
    b.push({ key: "load", type: "read", text: "Load the invoice from the ledger" });
    b.push({ key: "paid", type: "if", text: "Balance is $0?", yes: "end: settled" });
    b.push({ key: "dispute", type: "if", text: "Disputed?", yes: "a person, end" });
    b.push({ key: "stop", type: "if", text: "Asked us to stop?", yes: "a person, end" });
    b.push({ key: "promise", type: "if", text: "Promised a date?", yes: "wait for it, back to Balance" });
    b.push({ key: "region", type: "switch", text: "Customer's region?",
      cases: lane.regions.map(function (r) { return r + ": " + quiet[0] + "–" + quiet[1] + " there?"; }),
      miss: lane.regions.length < ALL_REGIONS.length ? "default: no check" : null });
    b.push({ key: "quietwait", type: "wait", text: "Wait until " + quiet[1] + " their time, back to region" });
    if (weekend) b.push({ key: "weekend", type: "if", text: "Saturday or Sunday there?", yes: "wait until Monday " + quiet[1] });
    b.push({ key: "cap", type: "if", text: cap + " or more messages this week?", yes: "wait 7 days from the oldest" });
    b.push({ key: "draft", type: "model", text: "Model drafts the " + words(stage.name) + " (" + stage.tone + ")" });
    b.push({ key: "amount", type: "if", text: "Draft's amount differs from the balance?", yes: "escalate, end" });
    if (stage.human) b.push({ key: "human", type: "person", text: "Wait for a person's yes", yes: "no: end" });
    b.push({ key: "send", type: "send", text: "Send " + stage.channel });
    b.push({ key: "log", type: "log", text: "Log “" + stage.name + " sent”" });
    if (next) {
      b.push({ key: "reply", type: "switch", text: "A reply before " + dLabel(next.day) + "? Model classifies it", cases: INTENTS });
    } else {
      b.push({ key: "done", type: "person", text: "Every stage sent, still owed: a person" });
    }
    return b;
  }

  function stagesOf(lane, withV5) {
    if (lane.id !== "retail" || !withV5) return lane.stages;
    return [lane.stages[0], V5_STAGE].concat(lane.stages.slice(1));
  }

  function countBoxes(boxes) {
    return boxes.reduce(function (n, x) { return n + 1 + (x.cases ? x.cases.length : 0); }, 0);
  }

  /* ── the canvas ───────────────────────────────────────── */

  var TYPES = [
    ["wait", "Wait"], ["read", "Read"], ["if", "If"], ["switch", "Switch"],
    ["model", "Model"], ["person", "Person"], ["send", "Send"], ["log", "Log"]
  ];
  var TYPE_LABEL = {};
  TYPES.forEach(function (t) { TYPE_LABEL[t[0]] = t[1]; });

  var S = { zoomed: false, v5: false };

  function driftAt(lane, stage, key) {
    for (var i = 0; i < DRIFTS.length; i++) {
      var d = DRIFTS[i];
      if (d.lane === lane.id && d.stage === stage.name && d.node === key) return d;
    }
    return null;
  }

  function renderLegend() {
    var box = $("cw-legend");
    TYPES.forEach(function (t) {
      var s = el("span", "cw-legend-item");
      s.appendChild(el("span", "cw-swatch cw-t-" + t[0]));
      s.appendChild(document.createTextNode(t[1]));
      box.appendChild(s);
    });
  }

  function renderNode(lane, stage, x) {
    var li = el("li", "cw-node cw-t-" + x.type);
    li.id = "n-" + lane.id + "-" + stage.name + "-" + x.key;
    li.appendChild(el("span", "cw-badge cw-t-" + x.type, TYPE_LABEL[x.type]));
    var body = el("span", "cw-node-body");
    body.appendChild(el("span", "cw-node-text", x.text));
    if (x.yes) body.appendChild(el("span", "cw-yes", "yes → " + x.yes));
    if (x.cases) {
      var cs = el("span", "cw-cases");
      x.cases.forEach(function (c) { cs.appendChild(el("span", "cw-case", c)); });
      if (x.miss) cs.appendChild(el("span", "cw-case cw-case-miss", x.miss));
      body.appendChild(cs);
    }
    li.appendChild(body);
    var d = driftAt(lane, stage, x.key);
    if (d) {
      li.classList.add("cw-drifted");
      var m = el("span", "cw-mark", String(d.n));
      m.title = d.title;
      li.appendChild(m);
    }
    return li;
  }

  function renderCanvas() {
    var lanesBox = $("cw-lanes");
    lanesBox.textContent = "";
    $("cw-canvas").classList.toggle("cw-zoomed", S.zoomed);
    LANES.forEach(function (lane) {
      var col = el("section", "cw-lane");
      col.setAttribute("aria-label", lane.name);
      var head = el("div", "cw-lane-head");
      head.appendChild(el("b", null, lane.name));
      head.appendChild(el("span", "small muted", lane.tenant + " · quiet " + lane.quiet[0] + "–" + lane.quiet[1] +
        " · " + lane.cap + " a week" + (lane.weekend ? " · weekend pause" : "")));
      col.appendChild(head);
      var stages = stagesOf(lane, S.v5);
      stages.forEach(function (stage, i) {
        var g = el("div", "cw-stage" + (stage.added ? " cw-added" : ""));
        var gh = el("div", "cw-stage-head");
        gh.appendChild(el("b", null, stage.name));
        gh.appendChild(el("span", "small muted", dLabel(stage.day) + " · " + stage.channel + ", " + stage.tone));
        var boxes = boxesFor(lane, stage, stages[i + 1]);
        gh.appendChild(el("span", "cw-stage-count small", countBoxes(boxes) + " boxes"));
        g.appendChild(gh);
        var ol = el("ol", "cw-nodes");
        boxes.forEach(function (x) { ol.appendChild(renderNode(lane, stage, x)); });
        g.appendChild(ol);
        col.appendChild(g);
      });
      col.appendChild(el("div", "cw-lane-end", "end"));
      lanesBox.appendChild(col);
    });
  }

  /* ── the numbers above the canvas ─────────────────────── */

  function totals() {
    var t = { stages: 0, boxes: 0, quietChecks: 0, businessQuiet: 0, caps: 0 };
    LANES.forEach(function (lane) {
      var stages = stagesOf(lane, S.v5);
      stages.forEach(function (stage, i) {
        var boxes = boxesFor(lane, stage, stages[i + 1]);
        t.stages++;
        t.boxes += countBoxes(boxes);
        boxes.forEach(function (x) {
          if (x.key === "region") {
            t.quietChecks += x.cases.length;
            if (lane.id === "business") t.businessQuiet += x.cases.length + 1;
          }
          if (x.key === "cap") t.caps++;
        });
      });
    });
    return t;
  }

  function stat(box, value, label, note) {
    var s = el("div", "cw-stat");
    s.appendChild(el("b", "cw-stat-value", value));
    s.appendChild(el("span", "cw-stat-label", label));
    if (note) s.appendChild(el("span", "small muted", note));
    box.appendChild(s);
  }

  function renderStats() {
    var t = totals(), box = $("cw-stats");
    box.textContent = "";
    stat(box, String(t.boxes), "boxes", t.stages + " stages in 3 lanes");
    stat(box, String(t.quietChecks), "quiet-hours checks", "one per stage per region");
    stat(box, String(t.caps), "weekly-cap checks", "one per stage");
    stat(box, String(t.businessQuiet), "boxes to edit", "to move business quiet hours");
    stat(box, "0", "policy versions logged", "the rules are whatever ran");
    var data = el("div", "cw-stat cw-stat-data");
    data.appendChild(el("b", "cw-stat-value", "3 + 1"));
    data.appendChild(el("span", "cw-stat-label", "as data"));
    data.appendChild(el("span", "small muted", "three records, one engine"));
    box.appendChild(data);
  }

  /* ── drift list, costs, the policy, the file ──────────── */

  function flash(id) {
    var n = $(id);
    if (!n) return;
    n.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
    n.classList.remove("cw-flash");
    void n.offsetWidth;
    n.classList.add("cw-flash");
  }

  function renderDrifts() {
    var ol = $("cw-drifts");
    DRIFTS.forEach(function (d) {
      var li = el("li", "cw-drift");
      var btn = el("button", "cw-drift-btn");
      btn.type = "button";
      btn.appendChild(el("span", "cw-mark", String(d.n)));
      var body = el("span", "cw-drift-body");
      body.appendChild(el("b", null, d.title));
      body.appendChild(el("span", "small muted", d.lane + " · " + d.stage));
      body.appendChild(el("span", "small", d.text));
      btn.appendChild(body);
      btn.onclick = function () {
        if (S.zoomed) setZoom(false);
        flash("n-" + d.lane + "-" + d.stage + "-" + d.node);
      };
      li.appendChild(btn);
      ol.appendChild(li);
    });
  }

  var COSTS = [
    ["Retail adds an SMS heads-up at D-1 (v5)",
      "Copy a stage block (about 25 boxes), rewire two arrows, redeploy. Runs already past D-2 never see it.",
      "One row in the retail stages, version 5. Cases move to it at their next look."],
    ["Business quiet hours become 20:00–09:00",
      "Edit the region switch in every business stage, one hour pair per branch.",
      "One field: quiet_hours."],
    ["A fourth schedule (a new kind of customer)",
      "A fourth lane: every stage with every rule copied in again.",
      "A new record in policies.json."],
    ["A seventh kind of reply",
      "A new branch on the reply switch after every stage but the last.",
      "One intent in inbound.py, one row in its table of what each intent may do."],
    ["“Which rules sent this at 2 a.m.?”",
      "Whichever definition that run started on. The log does not say.",
      "The policy version on the event row, and policy_versions."]
  ];

  function renderCosts() {
    var body = $("cw-costs");
    COSTS.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(el("th", null, r[0])).setAttribute("scope", "row");
      tr.appendChild(el("td", "cw-cost-steps", r[1]));
      tr.appendChild(el("td", "cw-cost-data", r[2]));
      body.appendChild(tr);
    });
  }

  function renderPolicy() {
    var lane = LANES[0], p = {
      tenant: lane.tenant, name: "Nimbus Electronics, retail invoices", version: lane.version,
      stages: lane.stages.map(function (s) {
        var o = { name: s.name, day: s.day, channel: s.channel, tone: s.tone };
        if (s.human) o.requires_human = true;
        return o;
      }),
      quiet_hours: { start: lane.quiet[0], end: lane.quiet[1] },
      max_contacts_per_week: lane.cap, weekend_pause: lane.weekend
    };
    var text = JSON.stringify(p, null, 2).replace(/\{\n\s+("name": "[a-z_]+",)\n\s+("day": -?\d+,)\n\s+("channel": "[a-z+]+",)\n\s+("tone": "[a-z]+")(,\n\s+"requires_human": true)?\n\s+\}/g,
      function (m, a, b, c, d, e) { return "{ " + a + " " + b + " " + c + " " + d + (e ? ", \"requires_human\": true" : "") + " }"; });
    $("cw-policy").textContent = text;
  }

  function yamlFor() {
    var out = ["workflow: nimbus-collections", "trigger: case.opened", "start: choose_schedule", "", "steps:",
      "  - id: choose_schedule", "    type: switch", "    on: case.tenant", "    cases:"];
    LANES.forEach(function (lane) { out.push("      " + lane.tenant + ": " + lane.id + "_" + stagesOf(lane, S.v5)[0].name + "_wait"); });
    LANES.forEach(function (lane) {
      var stages = stagesOf(lane, S.v5);
      stages.forEach(function (stage, i) {
        var boxes = boxesFor(lane, stage, stages[i + 1]);
        out.push("", "  # ── " + lane.tenant + " · " + stage.name + " (" + dLabel(stage.day) + ") ──");
        boxes.forEach(function (x, j) {
          var id = lane.id + "_" + stage.name + "_" + x.key;
          var nextId = j + 1 < boxes.length ? lane.id + "_" + stage.name + "_" + boxes[j + 1].key :
            (stages[i + 1] ? lane.id + "_" + stages[i + 1].name + "_wait" : "end");
          out.push("  - id: " + id, "    type: " + x.type, "    label: \"" + x.text.replace(/"/g, "'") + "\"");
          if (x.cases) {
            out.push("    cases:");
            x.cases.forEach(function (c) { out.push("      - when: \"" + c + "\"", "        next: " + id + "_" + c.split(":")[0].replace(/[^a-z]+/gi, "_").toLowerCase()); });
            if (x.miss) out.push("    default: " + nextId);
          }
          if (x.yes) out.push("    then: \"" + x.yes + "\"", "    else: " + nextId);
          else out.push("    next: " + nextId);
        });
      });
    });
    return out;
  }

  function renderFile() {
    var lines = yamlFor();
    $("cw-file").textContent = lines.join("\n");
    $("cw-file-count").textContent = lines.length + " lines (policies.json: 3 records, 116 lines)";
  }

  /* ── toggles ──────────────────────────────────────────── */

  function setZoom(on) {
    S.zoomed = on;
    $("cw-zoom").setAttribute("aria-pressed", on ? "true" : "false");
    $("cw-canvas").classList.toggle("cw-zoomed", on);
  }

  function setV5(on) {
    S.v5 = on;
    $("cw-v5").setAttribute("aria-pressed", on ? "true" : "false");
    var note = $("cw-v5-note");
    if (on) {
      var added = countBoxes(boxesFor(LANES[0], V5_STAGE, LANES[0].stages[1]));
      note.textContent = "Retail v5: " + added + " new boxes in one lane, the arrows on either side rewired, and a redeploy. " +
        "As data it is one row: { \"name\": \"sms_heads_up\", \"day\": -1, \"channel\": \"sms\", \"tone\": \"friendly\" }.";
    }
    note.hidden = !on;
    renderCanvas();
    renderStats();
    renderFile();
    if (on) flash("n-retail-sms_heads_up-wait");
  }

  function setNotes(on, save) {
    document.body.classList.toggle("notes-on", on);
    $("toggle-notes").setAttribute("aria-pressed", on ? "true" : "false");
    if (save) store("notes", on ? "1" : "0");
  }

  function setTheme(theme, save) {
    document.documentElement.setAttribute("data-theme", theme);
    $("toggle-theme").setAttribute("aria-pressed", theme === "light" ? "true" : "false");
    if (save) store("theme", theme);
  }

  /* ── start ────────────────────────────────────────────── */

  var q = window.location.search;
  var qTheme = /[?&]theme=(light|dark)/.exec(q), qNotes = /[?&]notes=([01])/.exec(q), qZoom = /[?&]zoom=([01])/.exec(q);
  setTheme(qTheme ? qTheme[1] : (store("theme") || "dark"), false);
  setNotes(qNotes ? qNotes[1] === "1" : store("notes") === "1", false);
  $("toggle-notes").onclick = function () { setNotes(!document.body.classList.contains("notes-on"), true); };
  $("toggle-theme").onclick = function () { setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light", true); };
  $("cw-zoom").onclick = function () { setZoom(!S.zoomed); };
  $("cw-v5").onclick = function () { setV5(!S.v5); };

  renderLegend();
  renderCanvas();
  setZoom(qZoom ? qZoom[1] === "1" : false);
  renderStats();
  renderDrifts();
  renderCosts();
  renderPolicy();
  renderFile();
})();

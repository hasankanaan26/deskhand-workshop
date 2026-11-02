/* The Triage view (#triage): one customer reply through triage/'s five steps.

   A client of GET /triage/replies and POST /triage/run, which run
   triage/try_reply.py in its own process. Three ways to run a reply:
   with its labels (a careful person's reading and proposal, no model, free),
   with the model (two real calls), or as a what-if (the labels, edited:
   "what if the model had said this?"), which shows the check that catches it.

   textContent only: the replies are customer-written, and some are prompt
   injections. console.js calls window.DunningTriage.show(id) when the tab opens;
   #triage/R-13 opens that reply. */
(function () {
  "use strict";

  var INTENTS = ["already_paid", "promise_to_pay", "partial_payment_request", "dispute", "out_of_office",
    "wrong_contact", "stop_contact", "question", "other"];
  var ACTIONS = ["record_payment_claim", "pause_for_promise", "offer_payment_plan", "open_dispute",
    "snooze_until_return", "update_contact", "stop_contact", "reply_only", "escalate", "no_action"];

  var T = { loaded: false, replies: [], accounts: [], byId: {}, current: null, labels: null, last: null, busy: false };

  function $(id) { return document.getElementById(id); }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) { node.textContent = ""; return node; }

  function detailOf(text) {
    try { var j = JSON.parse(text); return typeof j.detail === "string" ? j.detail : text; } catch (e) { return text; }
  }

  function request(url, body) {
    var opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    return fetch(url, opts).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error(detailOf(t)); });
      return r.json();
    });
  }

  function money(v) {
    var n = Number(v || 0);
    return "$" + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function fill(select, values) {
    clear(select);
    values.forEach(function (v) { var o = el("option", "", v); o.value = v; select.appendChild(o); });
  }

  /* ── loading and picking ──────────────────────────────── */

  // show("R-13") opens that reply: #triage/R-13 is a link you can project.
  function show(id) {
    if (T.loaded) { if (id && T.byId[id] && (!T.current || T.current.id !== id)) pick(id); return; }
    T.loaded = true;
    fill($("wi-intent"), INTENTS);
    fill($("wi-action"), ACTIONS);
    request("/triage/replies").then(function (r) {
      T.replies = r.replies; T.accounts = r.accounts;
      r.replies.forEach(function (x) { T.byId[x.id] = x; });
      var filter = $("tri-filter");
      INTENTS.forEach(function (i) { var o = el("option", "", i); o.value = i; filter.appendChild(o); });
      clear($("tri-account"));
      r.accounts.forEach(function (a) { var o = el("option", "", a.name + " (" + a.account_id + ")"); o.value = a.account_id; $("tri-account").appendChild(o); });
      renderList();
      if (id && T.byId[id]) pick(id);
    }).catch(function (err) {
      clear($("tri-list")).appendChild(el("p", "small muted", "Couldn't load the replies: " + err.message));
      T.loaded = false;
    });
  }

  function renderList() {
    var box = clear($("tri-list"));
    var f = $("tri-filter").value;
    T.replies.filter(function (r) { return !f || r.intent === f; }).forEach(function (r) {
      var b = el("button", "tri-item");
      b.type = "button";
      if (T.current && T.current.id === r.id) b.setAttribute("aria-current", "true");
      var top = el("span", "tri-item-top");
      top.appendChild(el("b", "mono", r.id));
      top.appendChild(el("span", "intent-name", r.intent));
      top.appendChild(el("span", "small muted", r.account_name));
      b.appendChild(top);
      b.appendChild(el("span", "small muted tri-item-hard", r.hard_because));
      b.onclick = function () { pick(r.id); };
      box.appendChild(b);
    });
  }

  function pick(id) {
    T.current = T.byId[id];
    T.labels = null;
    if (window.history && window.history.replaceState) window.history.replaceState(null, "", "#triage/" + encodeURIComponent(id));
    renderList();
    $("tri-empty").hidden = true;
    $("tri-reply-panel").hidden = false;
    $("tri-result").hidden = true;
    $("tri-whatif").open = false;
    $("tri-reply-h").textContent = id + " · " + T.current.account_name;
    $("tri-hard").textContent = "hard because: " + T.current.hard_because;
    $("tri-subject").textContent = T.current.subject;
    $("tri-from").textContent = "";
    $("tri-body").textContent = "";
    clear($("tri-account-facts"));
    $("tri-status").textContent = "";
    ["tri-run-labels", "tri-run-model", "tri-run-whatif"].forEach(function (b) { $(b).disabled = false; });
    // Run with the labels straight away: it is free, and it fills the reply,
    // the account and the what-if fields from the same response.
    run({ reply_id: id }, "with the labels");
  }

  /* ── running ──────────────────────────────────────────── */

  function setBusy(on, note) {
    T.busy = on;
    ["tri-run-labels", "tri-run-model", "tri-run-whatif", "tri-run-own"].forEach(function (b) { $(b).disabled = on; });
    $("tri-status").textContent = note || "";
  }

  function run(body, how) {
    if (T.busy) return;
    setBusy(true, body.live ? "asking the model (two calls)…" : "running " + how + "…");
    request("/triage/run", body).then(function (r) {
      T.last = r;
      // The labels run is the base every what-if edits.
      if (r.mode === "labels") { T.labels = r; loadWhatIf(r); }
      setBusy(false, "ran " + how + (r.mode === "model" ? costNote(r) : ""));
      if (!T.current) ["tri-run-labels", "tri-run-model", "tri-run-whatif"].forEach(function (b) { $(b).disabled = true; });
      renderReply(r);
      renderResult(r);
    }).catch(function (err) {
      setBusy(false, "failed: " + err.message);
    });
  }

  function costNote(r) {
    var c = 0;
    r.usage.forEach(function (u) { if (u) c += u.cost_usd; });
    return " · cost $" + c.toFixed(4);
  }

  function renderReply(r) {
    $("tri-from").textContent = "from " + r.reply.from + " · received " + r.reply.received_at.replace("T", " ").slice(0, 16) + " UTC · judged at " + r.now.replace("T", " ").slice(0, 16) + " UTC";
    $("tri-body").textContent = r.reply.body;
    var facts = clear($("tri-account-facts"));
    facts.appendChild(el("b", "", r.account.name + "'s open invoices: "));
    facts.appendChild(document.createTextNode(r.account.open_invoices.map(function (i) {
      return i.invoice_id + " " + money(i.balance_usd) + " (due " + i.due + ")";
    }).join(" · ") || "none"));
    if (r.account.unmatched_payments.length) {
      facts.appendChild(el("br"));
      facts.appendChild(el("b", "", "Money in, not yet matched: "));
      facts.appendChild(document.createTextNode(r.account.unmatched_payments.map(function (p) {
        return money(p.amount_usd) + " ref " + p.reference + (p.invoice_hint ? " (hint " + p.invoice_hint + ")" : "");
      }).join(" · ")));
    }
  }

  function loadWhatIf(r) {
    $("wi-intent").value = r.reading.intent;
    $("wi-confidence").value = r.reading.confidence;
    $("wi-action").value = r.proposal.action;
    $("wi-pause").value = r.proposal.pause_until || "";
    $("wi-contact").value = r.proposal.new_contact_email || "";
    $("wi-draft").value = r.proposal.draft_reply || "";
  }

  function whatIfOverride() {
    var base = T.labels;
    var o = { reading: {}, proposal: {} };
    if ($("wi-intent").value !== base.reading.intent) o.reading.intent = $("wi-intent").value;
    var conf = Number($("wi-confidence").value);
    if (!isNaN(conf) && conf !== base.reading.confidence) o.reading.confidence = conf;
    if ($("wi-action").value !== base.proposal.action) o.proposal.action = $("wi-action").value;
    var pause = $("wi-pause").value.trim() || null;
    if (pause !== base.proposal.pause_until) o.proposal.pause_until = pause;
    var contact = $("wi-contact").value.trim() || null;
    if (contact !== base.proposal.new_contact_email) o.proposal.new_contact_email = contact;
    var draft = $("wi-draft").value.trim() || null;
    if (draft !== (base.proposal.draft_reply || null)) o.proposal.draft_reply = draft;
    return o;
  }

  /* ── the five steps ───────────────────────────────────── */

  function kv(list, key, value) {
    if (value === null || value === undefined || value === "" || (Array.isArray(value) && !value.length)) return;
    var row = el("div", "tri-kv");
    row.appendChild(el("span", "muted", key));
    row.appendChild(el("span", "mono", Array.isArray(value) ? value.join(", ") : value));
    list.appendChild(row);
  }

  function step(n, name, who, tone) {
    var li = el("li", "tri-step tri-" + tone);
    var head = el("div", "tri-step-head");
    head.appendChild(el("b", "", n + " · " + name));
    head.appendChild(el("span", "chip tri-who-" + tone, who));
    li.appendChild(head);
    var body = el("div", "tri-step-body");
    li.appendChild(body);
    $("tri-steps").appendChild(li);
    return body;
  }

  function renderResult(r) {
    $("tri-result").hidden = false;
    clear($("tri-steps"));
    var who = r.mode === "model" ? "the model" : r.mode === "override" ? "the labels, edited" : "the labels (no model)";
    var tone = r.mode === "model" ? "model" : "labels";

    var rd = r.reading;
    var b1 = step(1, "read", who, tone);
    var line = el("div", "tri-big");
    line.appendChild(el("span", "intent-name", rd.intent));
    if (rd.other_intents.length) line.appendChild(el("span", "small muted", " + " + rd.other_intents.join(", ")));
    line.appendChild(el("span", "small muted", "  confidence " + Number(rd.confidence).toFixed(2)));
    if (rd.churn_risk) line.appendChild(el("span", "pill pill-escalated", "churn risk"));
    b1.appendChild(line);
    kv(b1, "invoice refs", rd.invoice_refs);
    kv(b1, "payment ref", rd.payment_reference);
    kv(b1, "amounts", rd.amounts_usd.map(money));
    kv(b1, "promised date", rd.promised_date);
    kv(b1, "return date", rd.return_date);
    kv(b1, "forward to", rd.forward_to_email);
    kv(b1, "disputed", rd.disputed_items.map(function (d) { return d.item + " (" + d.reason + ")"; }));
    rd.evidence.forEach(function (q) { b1.appendChild(el("blockquote", "tri-quote", q)); });
    b1.appendChild(el("p", "small muted", rd.summary));

    var m = r.match;
    var b2 = step(2, "match", "code", "code");
    b2.appendChild(el("div", "tri-big mono", (m.invoice_ids.join(", ") || "no invoice") + "  ·  " + m.method));
    if (m.ambiguous) b2.appendChild(el("p", "small tri-warn", "ambiguous: more than one invoice fits, so nobody guesses"));
    if (m.unknown_refs.length) b2.appendChild(el("p", "small tri-warn", "not this customer's open invoices: " + m.unknown_refs.join(", ")));

    var p = r.proposal;
    var b3 = step(3, "propose", who, tone);
    b3.appendChild(el("div", "tri-big mono", p.action + (p.invoice_ids.length ? "  " + p.invoice_ids.join(", ") : "")));
    kv(b3, "until", p.pause_until);
    kv(b3, "instalments", p.instalments.map(function (i) { return money(i.amount_usd) + " on " + i.due; }));
    kv(b3, "new contact", p.new_contact_email);
    if (p.draft_reply) b3.appendChild(el("div", "draft tri-draft", p.draft_reply));
    b3.appendChild(el("p", "small muted", p.reason));

    var b4 = step(4, "check", "code", "code");
    if (!r.failures.length) b4.appendChild(el("div", "tri-big tri-ok", "all eleven passed"));
    r.failures.forEach(function (f) {
      var row = el("div", "tri-fail");
      row.appendChild(el("b", "mono", f.code));
      row.appendChild(el("span", "", f.detail));
      b4.appendChild(row);
    });

    var d = r.disposition;
    var b5 = step(5, "gate", "code", "code");
    var verdict = el("div", "tri-big");
    verdict.appendChild(el("span", "mono", d.action));
    verdict.appendChild(el("span", "pill " + (d.escalated ? "pill-escalated" : d.runs === "now" ? "pill-settled" : "pill-awaiting_approval"),
      d.escalated ? "a person takes the case" : d.runs === "now" ? "runs now, can be undone" : "waits for a person's yes"));
    b5.appendChild(verdict);
    b5.appendChild(el("p", "small", d.reply === "queued_for_approval" ? "The draft reply waits for a person before it is sent." : "Nothing is sent to the customer."));

    var v = clear($("tri-verdict"));
    var lab = r.labelled;
    if (lab) {
      var e = lab.expected;
      var expectedText = "The label says: " + lab.gold_intent + " → " + e.action + ", " + e.runs.replace("_", " ") +
        (e.failure_codes.length ? ", fails " + e.failure_codes.join(", ") : "");
      v.appendChild(el("p", "small muted", expectedText));
      if (r.mode === "model") {
        v.appendChild(el("span", "pill " + (lab.intent_agrees ? "pill-settled" : "pill-escalated"), "intent " + (lab.intent_agrees ? "agrees" : "differs")));
        v.appendChild(el("span", "pill " + (lab.outcome_agrees ? "pill-settled" : "pill-escalated"), "outcome " + (lab.outcome_agrees ? "agrees" : "differs")));
      } else if (r.mode === "override") {
        v.appendChild(el("span", "pill pill-awaiting_approval", lab.outcome_agrees ? "same outcome as the label" : "a different outcome from the label"));
      }
    } else {
      v.appendChild(el("p", "small muted", "Your own reply: no label to compare with. Read each step and decide whether you agree."));
    }
  }

  /* ── wiring ───────────────────────────────────────────── */

  function wire() {
    $("tri-filter").onchange = renderList;
    $("tri-run-labels").onclick = function () { if (T.current) run({ reply_id: T.current.id }, "with the labels"); };
    $("tri-run-model").onclick = function () { if (T.current) run({ reply_id: T.current.id, live: true }, "with the model"); };
    $("tri-run-whatif").onclick = function () {
      if (!T.current || !T.labels) return;
      var o = whatIfOverride();
      if (!Object.keys(o.reading).length && !Object.keys(o.proposal).length) {
        $("tri-status").textContent = "Nothing changed yet: edit a field above first.";
        return;
      }
      run({ reply_id: T.current.id, override: o }, "the what-if");
    };
    $("tri-whatif-reset").onclick = function () { if (T.labels) loadWhatIf(T.labels); };
    $("tri-run-own").onclick = function () {
      var text = $("tri-text").value.trim();
      if (!text) { $("tri-status").textContent = "Type a reply first."; return; }
      T.current = null;
      renderList();
      $("tri-empty").hidden = true;
      $("tri-reply-panel").hidden = false;
      $("tri-reply-h").textContent = "Your reply";
      $("tri-hard").textContent = "";
      $("tri-subject").textContent = "Re: your invoice";
      ["tri-run-labels", "tri-run-model", "tri-run-whatif"].forEach(function (b) { $(b).disabled = true; });
      $("tri-whatif").open = false;
      run({ account_id: $("tri-account").value, text: text, live: true }, "with the model");
    };
  }

  wire();
  window.DunningTriage = { show: show };
})();

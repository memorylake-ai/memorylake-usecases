/* Compliance memory with audit trails — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one casebook project", view: "setup" },
    { n: 3, title: "Intake", sub: "21 records, two dates each", view: "intake" },
    { n: 4, title: "One clock", sub: "event clock · record clock", view: "oneclock" },
    { n: 5, title: "Both clocks", sub: "on file that day, current", view: "both" },
    { n: 6, title: "Write it back", sub: "late entries say so", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, oneclock: null, both: null, writeback: null,
    ask: { q: "", day: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const custName = (c) => c ? (story()?.customers?.[c] || c) : "policy";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const latePill = (r) => r.lag > (story()?.late_days ?? 2) ? `<span class="pill err">late +${esc(r.lag)} d</span>` : "";
  const verdict = (v, bad) => `<span class="pill ${v === "breach" ? "err" : "ok"} ${bad ? "wrong" : ""}">${v === "breach" ? "breach" : "ok"}${bad ? " ✗" : ""}</span>`;
  const findingsText = (fs) => { const by = {}; (fs || []).forEach((f) => (by[f.by] = by[f.by] || []).push(`${f.alert} ${f.rule.split(" ")[1]}`));
    const e = Object.entries(by).sort(); return e.length ? e.map(([p, v]) => `<b>${esc(p)}</b>: ${esc(v.join(", "))}`).join(" · ") : "none"; };
  function hitTable(hits, day) {
    return `<table class="iso"><tr><th>rank</th><th>record</th><th>happened</th><th>logged</th><th>text</th></tr>
      ${hits.map((h) => { const late = day && h.logged_on > day && h.event_on <= day;
        return `<tr class="${late ? "hl" : ""}"><td class="mono small">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small" style="white-space:nowrap">${esc(h.event_on)}</td><td class="small faint" style="white-space:nowrap">${esc(h.logged_on)}${late ? '<br><span class="pill err">not on file yet</span>' : ""}</td><td class="small">${esc(h.body)}</td></tr>`; }).join("")}</table>`;
  }
  function checkRow(c, truth) {
    const t = truth || {};
    return `<tr><td class="mono small" style="white-space:nowrap">${esc(c.alert)}<br><span class="faint">${esc(c.day)}</span></td><td class="small" style="white-space:nowrap">${esc(custName(c.customer))}<br><span class="faint">${esc(c.by)}</span></td>
      <td class="small">rating <b>${esc(c.rating || "—")}</b> <span class="faint mono">${esc(c.rating_record || "")}</span><br>§3 ${verdict(c.s3, t.s3 && t.s3 !== c.s3)}</td>
      <td class="small">open items <b class="mono">${esc((c.items || []).join(", ") || "none")}</b><br>§5 ${verdict(c.s5, t.s5 && t.s5 !== c.s5)}</td></tr>`;
  }
  const truthOf = (alert) => (S.both?.truth?.checks || []).find((c) => c.alert === alert);

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>search · as of a day</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, oneclock, both, writeback, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays the audit morning on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about a minute and a half · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>What did she know when she closed it?</h1>
      <p class="lead">Halden Mutual keeps its Q3 transaction-monitoring casebook in one MemoryLake project: policy, risk ratings, screening items, resolutions, alert closures.
      Every record says <b>when it happened</b>. Late entries — branch paper forms typed in weeks later, a screening inbox cleared in October — also have a second date: <b>when the casebook got them</b>.
      An audit of an alert closure needs both. Read through one clock, it blames the wrong analyst and misses the real breach. This page drives the <code>memorylake</code> CLI for real.
      The bank, its customers and its people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/compliance-memory-for-audit-trails" target="_blank" rel="noopener">memorylake.ai › Compliance memory for audit trails</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One casebook project");
    return `<h1>One casebook project</h1>
      <p class="lead">“Provenance — what data, what version, what decision, what time.” Every record behind a Q3 alert decision goes into one project, word for word.
      Metadata carries the day it happened (<code>event_on</code>) and the day it entered the casebook (<code>logged_on</code>).</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const groups = ["", ...Object.keys(story()?.customers || {})];
    return `<h1>Intake — ${x.records.length} records, ${x.late.length} of them late entries</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each. The text of every record says when it happened.
      Only <code>metadata.logged_on</code> says when the casebook got it — for ${x.late.length} records, weeks later.
      ${x.restored ? ` <span class="faint">(${x.restored} record(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      ${groups.map((g) => { const rs = x.records.filter((r) => (r.customer || "") === g); if (!rs.length) return "";
        return `<div class="card"><h2 style="margin-top:0">${esc(custName(g))}</h2><table class="iso"><tr><th>record</th><th>happened</th><th>logged</th><th>by</th><th>text</th></tr>
        ${rs.map((r) => `<tr class="${latePill(r) ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small" style="white-space:nowrap"><b>${esc(r.event_on)}</b></td>
          <td class="small" style="white-space:nowrap">${esc(r.logged_on)}<br>${latePill(r)}</td><td class="small" style="white-space:nowrap">${esc(r.by)}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`; }).join("")}`;
  }

  function oneclock() {
    const x = S.oneclock; if (!x) return empty("One clock");
    const clocks = S.data?.clocks || {};
    const read = (k) => { const a = x.reads[k];
      return `<div class="card"><h2 style="margin-top:0">${esc(clocks[k]?.short || k)}</h2>
        <table class="iso"><tr><th>alert</th><th>customer · closed by</th><th>TM-4 §3 (high risk → second reviewer)</th><th>TM-4 §5 (open screening item → escalate)</th></tr>
        ${a.checks.map((c) => checkRow(c, truthOf(c.alert))).join("")}</table>
        <p class="small">rating today: ${Object.entries(a.ratings).map(([c, v]) => `${esc(custName(c))} <b>${esc(v.rating)}</b> <span class="faint mono">${esc(v.record)}</span>`).join(" · ")}</p>
        <p class="small" style="margin-bottom:0">→ findings: ${findingsText(a.findings)}</p></div>`; };
    return `<h1>One clock — two reads, two different sets of findings</h1>
      <p class="lead">Mara Quist checks every Q3 closure against TM-4 §3 and §5. First, what retrieval hands her: a <code>search</code> per alert. Every hit's text carries one date — the day it happened.
      The highlighted rows had happened before the closure but were not on file yet; only <code>metadata.logged_on</code> says so.</p>
      ${x.searches.map((s) => { const c = x.reads.event.checks.find((k) => k.alert === s.alert) || {};
        return `<details class="card" ${s.alert === "A-1042" ? "open" : ""}><summary><b>${esc(s.alert)}</b> · ${esc(custName(c.customer))} · closed ${esc(c.day)} by ${esc(c.by)} <span class="faint small">— search top ${s.hits.length}</span></summary>${hitTable(s.hits, c.day)}</details>`; }).join("")}
      <p class="lead">Then every record (<code>fact list</code>, every page), read through one clock at a time. ✗ marks where it disagrees with both clocks (step 5).</p>
      ${read("event")}${read("record")}`;
  }

  function both() {
    const x = S.both; if (!x) return empty("Both clocks");
    const ev = S.oneclock?.reads?.event?.checks || [];
    const m = (v, t) => `<span class="${v !== t ? "pill err" : ""}">${esc(v)}${v !== t ? " ✗" : ""}</span>`;
    const shown = x.rows.filter((r) => !(r.event === r.record && r.record === r.both && r.both === "ok"));
    return `<h1>Both clocks — event clock wrong on ${x.wrong.event.length} of ${x.rows.length}, record clock on ${x.wrong.record.length}</h1>
      <p class="lead">The <b>record clock</b> decides what was on the analyst's file that day; the <b>event clock</b> decides which of those records was current. Same records, same code — only those two choices differ.</p>
      <div class="card"><table class="iso"><tr><th>check</th><th>event clock</th><th>record clock</th><th>both clocks</th></tr>
        ${shown.map((r) => `<tr class="${r.event !== r.both || r.record !== r.both ? "hl" : ""}"><td class="small">${esc(r.label)}</td><td class="small">${m(r.event, r.both)}</td><td class="small">${m(r.record, r.both)}</td><td class="small"><b>${esc(r.both)}</b></td></tr>`).join("")}</table>
        <p class="small faint">${x.rows.length - shown.length} more checks: all three reads say ok.</p>
        <table class="iso"><tr><td class="small">findings by the event clock</td><td class="small">${findingsText(S.oneclock?.reads?.event?.findings)}</td></tr>
          <tr><td class="small">findings by the record clock</td><td class="small">${findingsText(S.oneclock?.reads?.record?.findings)}</td></tr>
          <tr class="hl"><td class="small"><b>findings by both clocks</b></td><td class="small">${findingsText(x.truth.findings)}</td></tr></table></div>
      ${x.truth.checks.map((c) => { const e = ev.find((k) => k.alert === c.alert); const missing = (e?.on_file || []).filter((r) => !c.on_file.includes(r));
        return `<div class="card ${missing.length ? "mine" : ""}"><h2 style="margin-top:0">${esc(c.alert)} · ${esc(custName(c.customer))} · closed ${esc(c.day)} by ${esc(c.by)}</h2>
        <table class="iso"><tr><td class="small" style="white-space:nowrap">on file that day</td><td class="mono small">${esc(c.on_file.join(", "))}</td></tr>
          ${missing.length ? `<tr class="hl"><td class="small" style="white-space:nowrap">happened, not on file yet</td><td class="mono small">${esc(missing.join(", "))}</td></tr>` : ""}</table>
        <table class="iso">${checkRow(c)}</table></div>`; }).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    const c = x.census || {};
    return `<h1>Write it back — the plain read is now wrong on ${x.after.length} of ${x.total}</h1>
      <p class="lead">Each late entry's text now says when it was logged (<code>fact update --text</code>; the old text goes to <code>metadata.previous</code>), and <code>fact trace</code> keeps both versions.
      A read that only knows what the records' text says — the event date, plus a logged date if the record states one — was wrong on ${x.before.length} of ${x.total} before, exactly like the event clock.</p>
      <div class="card"><h2 style="margin-top:0"><code>fact list --query '${esc(c.query)}'</code> <span class="small faint">exact substring, every record</span></h2><table class="iso">
        <tr><td class="small">before</td><td class="small"><b>${(c.before || []).length}</b></td><td class="mono small">${esc((c.before || []).join(", "))}</td></tr>
        <tr class="hl"><td class="small">after</td><td class="small"><b>${(c.after || []).length}</b></td><td class="mono small">${esc((c.after || []).join(", "))}</td></tr></table></div>
      <div class="card mine"><h2 style="margin-top:0">Late entries</h2><table class="iso"><tr><th>record</th><th>now reads</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small" style="white-space:nowrap">${esc(u.record)}<br><span class="pill err">late +${esc(u.lag)} d</span></td><td class="small"><span class="muted">${esc(u.was)}</span> <b>${esc(u.now.slice(u.was.length).trim())}</b></td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace</code> <span class="small faint">newest first — MemoryLake's own record clock for this fact</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">The plain read again</h2><table class="iso">
        <tr><td class="small">before the write-back</td><td class="small">wrong on <b>${x.before.length}</b> of ${x.total} <span class="mono faint">${esc(x.before.join(", "))}</span></td></tr>
        <tr class="hl"><td class="small">after</td><td class="small">wrong on <b>${x.after.length}</b> of ${x.total}</td></tr>
        <tr><td class="small">findings now</td><td class="small">${findingsText(x.findings)}</td></tr></table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, custs = Object.entries(story()?.customers || {});
    return `<h1>Ask</h1>
      <p class="lead">Search the casebook with any question; give a day to see which hits were on file then. Or open the casebook as it stood on a day: what had happened, what was on file, and what each clock reads from it.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Was anything known about Orion Freight's director?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <input id="ask-day" placeholder="as of YYYY-MM-DD" value="${esc(a.day)}" ${ready ? "" : "disabled"} style="max-width:170px">
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-day="${esc(e.day)}">${esc(e.q)} · ${esc(e.day)}</button>`).join("")}</div>
        <div class="chips">${custs.map(([k, v]) => `<button type="button" class="chip" data-asof="${esc(k)}">${esc(v)} as of ${esc(a.day || "2026-09-18")}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? a.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  // ---------------------------------------------------------------- behaviour
  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("#ask-q").value.trim(), $("#ask-day").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-day").value = c.dataset.day; askQuestion(c.dataset.q, c.dataset.day); }));
      st.querySelectorAll(".chips .chip[data-asof]").forEach((c) => c.addEventListener("click", () => askAsof($("#ask-day").value.trim() || "2026-09-18", c.dataset.asof)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQuestion(q, day) {
    if (!q) return; S.ask.q = q; S.ask.day = day;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching the casebook…</span>`;
    const r = await api("/api/ask", { q, day });
    if (!r) { box.innerHTML = ""; return; }
    const n = r.day ? r.hits.filter((h) => h.happened && !h.on_file).length : 0;
    putResult(box, `<div class="row spread"><h2>“${esc(r.question)}”</h2>${r.day ? `<span class="pill">as of ${esc(r.day)}</span>` : ""}</div>
      <table class="iso"><tr><th>rank</th><th>record</th><th>happened</th><th>logged</th>${r.day ? "<th>on file?</th>" : ""}<th>text</th></tr>
      ${r.hits.map((h) => `<tr class="${r.day && h.happened && !h.on_file ? "hl" : ""}"><td class="mono small">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small" style="white-space:nowrap">${esc(h.event_on)}</td><td class="small" style="white-space:nowrap">${esc(h.logged_on)}</td>
        ${r.day ? `<td class="small">${h.on_file ? '<span class="pill ok">yes</span>' : h.happened ? '<span class="pill err">not yet</span>' : '<span class="faint">later</span>'}</td>` : ""}<td class="small">${esc(h.body)}</td></tr>`).join("")}</table>
      ${r.day ? `<p class="small">On ${esc(r.day)}: <b>${n}</b> of these had happened but were not on file yet.</p>` : ""}`);
  }
  async function askAsof(day, customer) {
    S.ask.day = day;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the casebook…</span>`;
    const r = await api("/api/asof", { day, customer });
    if (!r) { box.innerHTML = ""; return; }
    const clocks = S.data?.clocks || {};
    putResult(box, r.customers.map((c) => `<div class="row spread"><h2>${esc(c.name)} on ${esc(r.day)}</h2><span class="pill">fact list · every record</span></div>
      <table class="iso"><tr><th>record</th><th>happened</th><th>logged</th><th>on file?</th><th>text</th></tr>
      ${c.rows.map((x) => `<tr class="${x.on_file ? "" : "hl"}"><td class="mono small">${esc(x.record)}</td><td class="small" style="white-space:nowrap">${esc(x.event_on)}</td><td class="small" style="white-space:nowrap">${esc(x.logged_on)}</td>
        <td class="small">${x.on_file ? '<span class="pill ok">yes</span>' : '<span class="pill err">not yet</span>'}</td><td class="small">${esc(x.body)}</td></tr>`).join("") || '<tr><td class="small faint" colspan="5">nothing had happened yet</td></tr>'}</table>
      <table class="iso">${["event", "record", "both"].map((k) => `<tr class="${k === "both" ? "hl" : ""}"><td class="small" style="white-space:nowrap">${esc(clocks[k]?.label || k)}</td>
        <td class="small">rating <b>${esc(c.reads[k].rating || "—")}</b> <span class="faint mono">${esc(c.reads[k].record || "")}</span> · open items <b class="mono">${esc(c.reads[k].items.join(", ") || "none")}</b></td></tr>`).join("")}</table>`).join(""));
  }
  async function api(path, body) {
    try { const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const j = await res.json(); if (!res.ok || j.error) { banner(j.error || `HTTP ${res.status}`); return null; } return j;
    } catch (e) { banner(String(e)); return null; }
  }
  function banner(msg, ok) { const b = $("#banner"); if (!msg) { b.hidden = true; return; } b.hidden = false; b.className = "banner" + (ok ? " ok" : ""); b.textContent = msg; }
  function setConn() {
    const st = S.st; const c = $("#conn");
    c.className = "conn " + (st.status === "running" ? "busy" : st.connected ? "on" : "");
    $("#conn-text").textContent = st.status === "running" ? `running ${st.job}…` : st.connected ? `${st.team?.name || "connected"} · ${st.workspace?.name || st.workspace?.id || ""}` : "Not connected";
    const busy = st.status === "running";
    ["#btn-run", "#btn-reset", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.setup = null; S.intake = null; S.oneclock = null; S.both = null; S.writeback = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's casebook project and every record on it from your account?")) return;
    banner(""); const r = await api("/api/cleanup", {}); if (r) { S.st = r; setConn(); }
  });

  // ---------------------------------------------------------------- terminal
  const termLog = $("#term-log");
  function term(cls, text) {
    const atBottom = termLog.scrollHeight - termLog.scrollTop - termLog.clientHeight < 40;
    const span = document.createElement("span"); span.className = cls; span.textContent = text; termLog.appendChild(span); termLog.appendChild(document.createTextNode("\n"));
    while (termLog.childNodes.length > 4000) termLog.removeChild(termLog.firstChild);
    if (cls === "cmd") { S.termCount++; $("#term-count").textContent = `${S.termCount} commands`; }
    if (atBottom) termLog.scrollTop = termLog.scrollHeight;
  }
  if (window.innerWidth < 900) { $("#terminal").classList.add("collapsed"); $("#term-toggle").setAttribute("aria-expanded", "false"); }
  $("#term-toggle").addEventListener("click", () => { const t = $("#terminal"); t.classList.toggle("collapsed"); $("#term-toggle").setAttribute("aria-expanded", String(!t.classList.contains("collapsed"))); });

  // ---------------------------------------------------------------- events
  const stepOf = (i) => STEPS.find((s) => s.n === i);
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; S.step = d.index || 0;
      if (d.index === 2) resetView();
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "intake") S.intake = d;
    else if (k === "oneclock") S.oneclock = d;
    else if (k === "both") S.both = d;
    else if (k === "writeback") S.writeback = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and every record on it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Search the casebook as of any day, or open it as it stood on a day — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "oneclock", "both", "writeback",
    "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    // "error" is also EventSource's own connection-error event, which has no data: skip those.
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => { if (e.data) onEvent(JSON.parse(e.data)); }));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  (async () => {
    renderSteps();
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data;
    const target = state.last_event_id; setConn();
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const h = (e) => { if (!e.data) return; const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.writeback ? "writeback" : "connect");
    subscribe();
  })();
})();

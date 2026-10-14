/* Bug triage memory for small SaaS teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, five tools", view: "setup" },
    { n: 3, title: "Intake", sub: "investigations, PRs, deploys…", view: "intake" },
    { n: 4, title: "Quick read", sub: "closest investigation's status", view: "quick" },
    { n: 5, title: "Memory read", sub: "follow the fix through the records", view: "memory" },
    { n: 6, title: "Write it back", sub: "status lines from the records", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", on: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const CLS = {
    regression: ["err", "regression → new investigation"], open: ["ok", "known, open → reopen + workaround"],
    pending: ["ok", "known, fix not shipped → workaround + date"], investigating: ["ok", "known regression → link to it"],
    unknown: ["warn", "unclear"],
  };
  const clsPill = (c, wrongIf) => `<span class="pill ${wrongIf ? "err" : (CLS[c] || CLS.unknown)[0]}">${esc((CLS[c] || CLS.unknown)[1])}</span>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill ${t === "deploys" ? "accent" : ""}">${esc(toolName(t))}</span>`;
  function hitTable(hits, inv, extra) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · tool</th></tr>
      ${hits.map((h) => `<tr class="${h.record === inv ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.record === inv ? '<span class="pill warn">closest investigation</span>' : ""}${extra ? extra(h) : ""}</td></tr>`).join("")}</table>`;
  }
  function timeline(rows, ranks, where = "step 4's top 8") {
    return `<table class="iso"><tr><th>date</th><th>tool</th><th>id</th><th>record</th><th>${ranks ? "in " + esc(where) : ""}</th></tr>
      ${rows.map((r) => { const k = ranks ? ranks[r.record] : undefined; return `<tr class="${r.kind === "rollback" || r.kind === "scheduled" ? "hl" : ""}"><td class="small muted" style="white-space:nowrap">${esc(r.on)}</td><td>${toolPill(r.tool)}</td>
        <td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body)}</td>
        <td class="mono small" style="white-space:nowrap">${ranks ? (k ? "#" + esc(k) : '<span class="pill warn">— not there</span>') : ""}</td></tr>`; }).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Triage<small>any report, any date · trace an id</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, memory, writeback, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays triage morning on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about two minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The closed ticket says “fixed”. The deploy log says otherwise.</h1>
      <p class="lead">Tallyfold keeps every tool's records in one MemoryLake project — Linear investigations, GitHub merges, the deploy log, Sentry, the helpdesk.
      Three new tickets come in. The quick read retrieves the closest prior investigation (right one, every time) and trusts its status line: “fixed”, so each ticket looks like a regression.
      Two of those fixes are not in production — one was rolled back in August, one is merged but ships next week. The records that say so are filed under a PR number, not under the symptom.
      The memory read follows the identifiers through the records; writing its answer back into the status lines makes the quick read right too.
      This page drives the <code>memorylake</code> CLI for real. The company, customers and engineers are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/bug-triage-memory-for-small-saas-teams" target="_blank" rel="noopener">memorylake.ai › Bug triage memory for small SaaS teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for every tool's records");
    return `<h1>One project for every tool's records</h1>
      <p class="lead">“Cross-tool retrieval — Linear, GitHub, Sentry, support tools unified.” Each record keeps its tool and kind in metadata; the identifiers in its text (PR numbers, INV-, ENG- ids) are what tie the tools together.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const invs = x.records.filter((r) => r.kind === "investigation");
    const byTool = Object.keys(story()?.tools || {}).map((t) => [t, x.records.filter((r) => r.tool === t)]);
    return `<h1>Intake — ${x.records.length} records from five tools</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each (tool, kind, who, when).
      Every investigation's status line says “fixed”.${x.restored ? ` <span class="faint">(${x.restored} change(s) an earlier run wrote back were undone, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">The investigations, as Linear has them</h2><table class="iso"><tr><th>id</th><th>opened</th><th>investigation</th></tr>
        ${invs.map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td class="small muted" style="white-space:nowrap">${esc(r.on)}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>
      <div class="cols">${byTool.filter(([t]) => t !== "linear").map(([t, rs]) => `<div class="card"><h2 style="margin-top:0">${esc(toolName(t))} · ${rs.length}</h2><table class="iso">
        ${rs.map((r) => `<tr class="${r.kind === "rollback" || r.kind === "scheduled" ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`).join("")}</div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — ${x.reads.length} of ${x.reads.length} look like regressions</h1>
      <p class="lead">What a triage assistant does with “new bug reports retrieve similar prior investigations first”: <code>search</code> the report, take the closest investigation, trust its status line.
      It finds the right investigation every time.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.ticket.id)} · ${esc(q.ticket.customer)} <span class="small faint">${esc(q.ticket.on)}</span></h2>${clsPill(q.verdict.cls)}</div>
        <p class="small">“${esc(q.ticket.text)}”</p>${hitTable(q.hits, q.inv)}
        <p class="small" style="margin-bottom:0">${esc(q.inv)}: <b>“Status: ${esc(q.status)}”</b> → ${esc(q.verdict.said)} → ${esc(q.verdict.action)}</p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} quick reads were wrong</h1>
      <p class="lead">Same matched investigation, then every identifier it names — the fix's PR, a re-land's PR, a follow-up task, earlier tickets — through <code>fact list --query &lt;id&gt;</code> (exact substring, every page).
      Where the fix stands on the ticket's date comes from the GitHub, deploy-log and tracker records alone. The last column: where each deciding record sat in step 4's top 8.</p>
      <div class="card"><table class="iso"><tr><th>ticket</th><th>matched</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="mono small" style="white-space:nowrap">${esc(r.ticket.id)}</td><td class="mono small" style="white-space:nowrap">${esc(r.inv)}</td>
          <td class="small">${esc(r.quick.said)}<br>${clsPill(r.quick.cls, !r.agree)}</td><td class="small">${esc(r.memory.said)}<br>${clsPill(r.memory.cls)}</td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => { const ranks = Object.fromEntries(r.decide.map(([k, v]) => [k, v])); const miss = r.decide.filter(([, v]) => !v).length;
        return `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.ticket.id)} → ${esc(r.inv)}</h2><span class="pill ${miss ? "warn" : ""}">${miss} of ${r.decide.length} deciding records never in the top 8</span></div>
        <p class="small">asked: ${r.memory.asked.map(([a, n]) => `<code>${esc(a)}</code> (${n})`).join(" · ")}</p>${timeline(r.timeline, ranks)}
        ${r.memory.earlier?.length ? `<p class="small">earlier reports: ${r.memory.earlier.map((e) => `${esc(e.record)} ${esc(e.by)} (${esc(e.on)})`).join(", ")}</p>` : ""}
        <p class="small" style="margin-bottom:0"><b>${esc(r.memory.said)}</b> → ${esc(r.memory.action)}${r.memory.workaround ? ` · workaround: ${esc(r.memory.workaround)}` : ""}</p></div>`; }).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">Each investigation's status line is rewritten from its records (<code>fact update --text</code>, the old text kept in <code>metadata.previous</code>); the real regression becomes ${esc(x.reg)}; each ticket gets a triage note linked to its investigation.
      Later the same day, three more tickets about the same bugs go through the <b>same quick-read code</b>, asked in new words (<code>search</code> answers a repeated question from its cache).</p>
      <div class="card mine"><h2 style="margin-top:0">Status lines</h2><table class="iso"><tr><th>id</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small">${esc(u.record)}</td><td class="small muted">${esc(u.was)}</td><td class="small"><b>${esc(u.now)}</b></td></tr>`).join("")}</table>
        <p class="small" style="margin-bottom:0">new: ${x.added.map((a) => `<span class="pill">${esc(a)}</span>`).join(" ")}</p></div>
      <div class="card"><h2 style="margin-top:0">Later tickets, quick read again</h2><table class="iso"><tr><th>ticket</th><th>closest investigation</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.ticket.id)}</span><br>“${esc(a.ticket.text)}”</td><td class="mono small" style="white-space:nowrap">${esc(a.inv)} <span class="faint">#${esc(a.inv_rank)}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td class="small">${esc(a.quick.said)}<br>${clsPill(a.quick.cls)}</td><td>${clsPill(a.memory.cls)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask;
    return `<h1>Triage</h1>
      <p class="lead">Paste any bug report and pick its date: the quick read (closest investigation's status line) and the memory read (its fix, followed through the records as of that day) side by side.
      Or trace an identifier: every record, in every tool, that names it.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Customers get two copies of every invoice in QuickLedger" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <input id="ask-on" class="date" placeholder="2026-10-14" value="${esc(a.on || story()?.today || "")}" ${ready ? "" : "disabled"} style="max-width:130px"><button class="btn primary" ${ready ? "" : "disabled"}>Triage</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-on="${esc(e.on)}">${esc(e.q)} · ${esc(e.on)}</button>`).join("")}</div>
        <div class="chips">${(story()?.trace_examples || []).map((i) => `<button type="button" class="chip" data-trace="${esc(i)}">trace ${esc(i)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askTriage($("#ask-q").value.trim(), $("#ask-on").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; $("#ask-on").value = c.dataset.on; askTriage(c.dataset.q, c.dataset.on); }));
      st.querySelectorAll(".chips .chip[data-trace]").forEach((c) => c.addEventListener("click", () => askTrace(c.dataset.trace)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askTriage(q, on) {
    if (!q) return; S.ask.q = q; S.ask.on = on;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> triaging…</span>`;
    const r = await api("/api/triage", { q, on });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.ticket.text)}” · ${esc(r.ticket.on)}</h2>${r.memory ? agreePill(r.agree) : ""}</div>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${esc(r.inv)}: “Status: ${esc(r.status)}”<br>${esc(r.quick.said)} ${clsPill(r.quick.cls, r.memory && !r.agree)}</p></div>
        <div><h3>Memory read</h3><p class="small">${r.memory ? `${esc(r.memory.said)} ${clsPill(r.memory.cls)}<br>asked: ${r.memory.asked.map(([a, n]) => `<code>${esc(a)}</code> (${n})`).join(" · ")}` : "no investigation in the top 8"}</p></div></div>
      ${r.timeline.length ? timeline(r.timeline, Object.fromEntries(r.hits.map((h) => [h.record, h.rank])), `this search's top ${r.hits.length}`) : ""}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.inv)}</details>`);
  }
  async function askTrace(ident) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/trace", { ident });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2><code>fact list --query "${esc(r.ident)}"</code></h2><span class="pill">${r.records.length} record(s)</span></div>${timeline(r.records)}`);
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.memory = null; S.writeback = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's triage project and every record on it from your account?")) return;
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
    else if (k === "quick") S.quick = d;
    else if (k === "memory") S.memory = d;
    else if (k === "writeback") S.writeback = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and every record on it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Triage a report of your own, on any date — “Triage” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick", "memory", "writeback",
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

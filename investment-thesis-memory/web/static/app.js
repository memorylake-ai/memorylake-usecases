/* Investment thesis memory for portfolio managers — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project per holding", view: "setup" },
    { n: 3, title: "Intake", sub: "29 records, one fact each", view: "intake" },
    { n: 4, title: "Holding by holding", sub: "the weekly review", view: "review" },
    { n: 5, title: "The whole book", sub: "same check, every holding", view: "book" },
    { n: 6, title: "Write it back", sub: "cross-reference · mark theses", view: "written" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, review: null, book: null, written: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const kname = (k) => story()?.kinds?.[k] || k;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const statusPill = (s) => s === "broken" ? `<span class="pill err">✗ broken</span>` : s === "holding" ? `<span class="pill ok">✓ holding</span>` : `<span class="pill">· untested</span>`;
  const countPill = (rows) => { const n = rows.filter((r) => r.flagged).length; const w = rows.filter((r) => r.flagged).reduce((a, r) => a + r.weight, 0);
    return `<span class="pill ${n > 1 ? "err" : "warn"}">${n} of ${rows.length} flagged · ${w}% of the book</span>`; };

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the book<small>search every holding · audit by date</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, review, book, written, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Book</dt><dd>${st.ready ? "from an earlier run — Run again reads it back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>The bad news was in memory. It was filed under the wrong holding.</h1>
      <p class="lead">Ardent Hollow Partners keeps one MemoryLake project per holding: the thesis, the premises it rests on, earnings and news notes, conviction updates — every record word for word, with metadata.
      On September 30 a note filed under Corvane Robotics says its largest customer, Halvern Logistics, is bringing its warehouse technology in-house and leaving its Rotterdam hub.
      The weekly review, holding by holding, flags one holding. The same check over the whole book flags three: two more theses rest on Halvern, and their own memory never heard the news.
      This page drives the <code>memorylake</code> CLI for real. The fund, companies and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the seven projects and every record on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/investment-memory-for-portfolio-managers" target="_blank" rel="noopener">memorylake.ai › Investment memory for portfolio managers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project per holding");
    const total = su.holdings.reduce((a, h) => a + h.weight, 0);
    return `<h1>One project per holding — the way research is filed, and the way an AI tool reads it</h1>
      <p class="lead">Seven holdings, ${total}% of the book. Each gets its own project, so a research assistant working on one holding sees that holding's memory.</p>
      <div class="card"><table class="iso"><tr><th>ticker</th><th>holding</th><th>weight</th><th>analyst</th><th>project</th></tr>
        ${su.holdings.map((h) => `<tr><td class="mono"><b>${esc(h.ticker)}</b></td><td>${esc(h.name)}</td><td><b>${h.weight}%</b></td><td class="small">${esc(h.analyst)}</td>
          <td class="mono small faint">${esc(su.projects[h.ticker])}</td></tr>`).join("")}</table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const tag = (r) => r.kind === "premise" ? `<span class="pill">${esc(r.premise)} · ${esc(r.entity)}</span><br><span class="small faint">breaks on ${esc((r.breaks_on || []).join(", "))}</span>`
      : (r.events || []).length ? `<span class="small">about ${esc((r.entities || []).join(", "))}</span><br><span class="small faint">${esc(r.events.join(", "))}</span>`
      : r.conviction ? `<span class="pill">conviction ${esc(r.conviction)}</span>` : "";
    return `<h1>Intake — every record, word for word, filed under its holding</h1>
      <p class="lead">One <code>fact add --project</code> per holding, then <code>fact update --metadata</code> on each record: who logged it, when — and for a premise, the company it depends on and the events that would break it; for a note, the companies it is about and the events it reports.</p>
      <div class="card"><table class="iso"><tr><th>holding</th><th>id</th><th>logged</th><th>kind · by</th><th>metadata</th><th>record</th></tr>
        ${x.records.map((r) => `<tr class="${r.record === "CRVN-N1" ? "hl" : ""}"><td class="mono small"><b>${esc(r.holding)}</b></td><td class="mono small">${esc(r.record)}</td><td class="small muted" style="white-space:nowrap">${esc(r.logged_on)}</td>
          <td class="small">${esc(kname(r.kind))}<br><span class="faint">${esc(r.logged_by)}</span></td><td>${tag(r)}</td>
          <td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`;
  }

  function reviewTable(rows) {
    return `<table class="iso"><tr><th>holding</th><th>weight</th><th>conviction</th><th>premises</th></tr>
      ${rows.map((r) => `<tr class="${r.flagged ? "hl" : ""}"><td><span class="mono"><b>${esc(r.ticker)}</b></span> ${esc(r.name)}${r.flagged ? ' <span class="pill err">flagged</span>' : ""}</td><td><b>${r.weight}%</b></td>
        <td class="small">${esc(r.conviction)} <span class="faint">${esc(r.conviction_on)}</span></td>
        <td class="small">${r.premises.map((p) => `<div>${esc(p.label)} <b>${esc(p.entity)}</b> ${statusPill(p.status)}${p.by ? ` <span class="mono faint">${esc(p.by.record)} ${esc(p.by.on)}</span>${p.by.filed !== r.ticker ? ` <span class="pill warn">filed under ${esc(p.by.filed)}</span>` : ""}` : ""}</div>`).join("")}</td></tr>`).join("")}</table>`;
  }

  function review() {
    const x = S.review; if (!x) return empty("Holding by holding");
    return `<h1>The weekly review, holding by holding — one flagged</h1>
      <p class="lead">What an AI research tool scoped to one holding sees: <code>search</code> that holding's project, then the premise check over that holding's memory. Every premise is tested against every note about the company it depends on.</p>
      <div class="card"><div class="row spread"><h2 style="margin:0">Each holding against its own memory</h2>${countPill(x.rows)}</div>${reviewTable(x.rows)}</div>
      <div class="card"><h2 style="margin-top:0">What the holding-scoped search returned</h2>
        ${Object.entries(x.searches).map(([t, hits]) => `<div class="small" style="margin-bottom:8px"><b class="mono">${esc(t)}</b> — top 3 of ${hits.length}:
          ${hits.slice(0, 3).map((h) => `<div class="faint">#${h.rank} <span class="mono">${esc(h.record)}</span> ${esc(h.body)}</div>`).join("")}</div>`).join("")}</div>`;
  }

  function book() {
    const x = S.book; if (!x) return empty("The whole book");
    const a = x.audit, c = x.control;
    return `<h1>The same check over the whole book — three flagged</h1>
      <p class="lead">One <code>fact list</code> per holding, every premise tested against every note about the company it depends on — whichever holding the note was filed under. Plain code over the metadata, no model.</p>
      <div class="card mine"><div class="row spread"><h2 style="margin:0">Every holding against the whole book</h2>${countPill(a.book)}</div>${reviewTable(a.book)}
        ${x.blind.length ? `<p class="small" style="margin-bottom:0"><b>${x.blind.length}</b> broken premises rest on a note their own holding's memory does not hold: ${x.blind.map((b) => `<span class="mono">${esc(b.ticker)} ${esc(b.premise)}</span> ← <span class="mono">${esc(b.evidence)}</span> (filed under ${esc(b.filed)})`).join(" · ")}</p>` : ""}</div>
      <div class="cols"><div class="card"><h2 style="margin-top:0">Shared dependencies</h2><table class="iso"><tr><th>company</th><th>holdings</th><th>book</th><th>premises</th></tr>
        ${a.exposure.map((e) => `<tr><td><b>${esc(e.entity)}</b></td><td class="mono small">${esc(e.holdings.join(", "))}</td><td><b>${e.weight}%</b></td>
          <td>${e.broken.length ? `<span class="pill err">${e.broken.length} broken</span>` : `<span class="pill ok">${e.holding.length} holding</span>`}</td></tr>`).join("")}</table>
        ${a.reaffirmed.map((r) => `<p class="small" style="margin-bottom:0"><span class="pill warn">${esc(r.ticker)} reaffirmed high ${esc(r.on)}</span> — ${r.days} days after <span class="mono">${esc(r.evidence)}</span> was in memory under ${esc(r.filed)}: “${esc(r.body)}”</p>`).join("")}</div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Control — as of ${esc(c.day)}</h2>${countPill(c.book)}</div>
        <p class="small">The day before the news, the book-wide check flags nothing — and the concentration is already visible:</p>
        <table class="iso">${c.exposure.map((e) => `<tr><td><b>${esc(e.entity)}</b></td><td class="mono small">${esc(e.holdings.join(", "))}</td><td><b>${e.weight}%</b> of the book</td></tr>`).join("")}</table></div></div>`;
  }

  function written() {
    const x = S.written; if (!x) return empty("Write it back");
    return `<h1>Write it back — each blind holding gets the note, each thesis gets a flag</h1>
      <p class="lead">${x.xrefs.length} cross-reference(s) filed with <code>fact add</code> (the original note's text, where it came from, its metadata plus <code>xref_of</code>); ${x.marked.length ? `${x.marked.length} theses marked` : `all ${x.already.length} theses were marked by an earlier run`} with <code>fact update --metadata</code> (<code>review: premise-broken</code>). The theses' text stays word for word.</p>
      <div class="card"><h2 style="margin-top:0">Cross-references</h2><table class="iso"><tr><th>new record</th><th>holding</th><th>copy of</th><th>breaks</th></tr>
        ${x.xrefs.map((r) => `<tr><td class="mono">${esc(r.record)}</td><td class="mono"><b>${esc(r.ticker)}</b></td><td class="mono">${esc(r.xref_of)}</td><td>${esc(r.premises.join(", "))}</td></tr>`).join("")}</table></div>
      <div class="card mine"><div class="row spread"><h2 style="margin:0">The weekly review again, holding by holding</h2>${countPill(x.rows)}</div>${reviewTable(x.rows)}</div>
      ${x.probe ? `<div class="card"><h2 style="margin-top:0">${esc(x.probe)}'s holding-scoped search, same question as this morning</h2>
        ${x.hits.slice(0, 3).map((h) => `<div class="small ${h.xref_of ? "" : "faint"}">#${h.rank} <span class="mono">${esc(h.record)}</span> ${esc(h.body)}</div>`).join("")}
        <p class="small" style="margin-bottom:0">${x.xref_rank ? `The cross-reference is hit <b>#${x.xref_rank}</b>.` : "The cross-reference is not in the top 8."}</p></div>` : ""}`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the book</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 across every holding's project, each hit labelled with the holding it was filed under. Or run the premise check as of any date.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What do we know about Halvern Logistics?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="chips">${["2026-09-29", "2026-10-01", "2026-10-14"].map((d) => `<button type="button" class="chip" data-day="${d}">premise check as of ${d}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    st.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => askAudit(b.dataset.day)));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length} across every holding</h2><table class="iso"><tr><th>rank</th><th>filed under</th><th>hit</th></tr>
      ${r.hits.map((h) => `<tr><td class="mono small">#${h.rank}</td><td class="mono"><b>${esc(h.holding)}</b></td><td class="small"><span class="mono">${esc(h.record || "?")}</span> <span class="faint">${esc(h.logged_on)}</span> ${esc(h.body)}${h.xref_of ? ` <span class="pill warn">cross-reference of ${esc(h.xref_of)}</span>` : ""}</td></tr>`).join("")}</table>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askAudit(day) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/audit", { day });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="row spread"><h2>Holding by holding, as of ${esc(r.day)}</h2>${countPill(r.each)}</div>${reviewTable(r.each)}
      <div class="row spread"><h2>The whole book, as of ${esc(r.day)}</h2>${countPill(r.book)}</div>${reviewTable(r.book)}`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
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
  function resetView() { S.setup = null; S.intake = null; S.review = null; S.book = null; S.written = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's seven holding projects and every record on them from your account?")) return;
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
    else if (k === "holding_review") S.review = d;
    else if (k === "book_audit") S.book = d;
    else if (k === "written") S.written = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the seven holding projects and every record on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask the book a question of your own — “Ask the book” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "holding_review", "book_audit", "written",
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
    show(state.status === "running" ? S.view : S.written ? "written" : "connect");
    subscribe();
  })();
})();

/* Campaign memory for marketing teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, four exports", view: "setup" },
    { n: 3, title: "Intake", sub: "retro results + “what worked”", view: "intake" },
    { n: 4, title: "Plan the launch", sub: "the way memory says", view: "plan" },
    { n: 5, title: "The yardstick check", sub: "every number, back to its export", view: "yardsticks" },
    { n: 6, title: "One yardstick", sub: "re-rank, write back, plan again", view: "common" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, plan: null, yardsticks: null, common: null, planned: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const pct = (x) => (x === null || x === undefined ? "—" : `${Number(x).toFixed(1)}%`);
  const kindPill = (h) => h.kind === "reflection" ? `<span class="pill accent">reflection</span>`
    : h.kind === "common" ? `<span class="pill ok">one yardstick</span>` : `<span class="pill">retro result</span>`;
  const stickPill = (h) => h.kind === "result" && h.yardstick ? ` <span class="pill ${h.yardstick === "unknown" ? "err" : "warn"}">${esc(h.yardstick)}</span>` : "";
  function hitTable(hits) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>fact</th></tr>
      ${hits.map((h) => `<tr class="${h.kind === "reflection" ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${kindPill(h)}${stickPill(h)}</td></tr>`).join("")}</table>`;
  }
  const rankList = (r) => (r || []).map(([ch, v], i) => `<span class="pill ${i === 0 ? "accent" : ""}">${i + 1}. ${esc(ch)} ${pct(v)}</span>`).join(" ");

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>search the campaign memory</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, plan, yardsticks, common, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays planning day on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about two minutes the first time · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Memory kept every number. It lost every yardstick.</h1>
      <p class="lead">Larkspur Outdoor keeps one MemoryLake project as its campaign memory: the exports from each tool, the results the team wrote down after each campaign, and a “what worked” reflection built from them.
      Every remembered number is true — each equals a cell in its tool's export. But the email platform, the ads manager and the search console each count conversion their own way, and the creator's 12% comes from the creator's own recap.
      Ranked side by side, they say: lead the Q1 launch with creators and email, trim search. On one yardstick, the ranking turns over.
      This page drives the <code>memorylake</code> CLI for real. The brand, people, campaigns and numbers are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project, its four exports and every record in it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/campaign-memory-for-marketing-teams" target="_blank" rel="noopener">memorylake.ai › Campaign memory for marketing teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project, one export per tool");
    return `<h1>One project for campaign memory, one export per tool</h1>
      <p class="lead">Each tool's campaign export is uploaded to the Library and imported into the project (<code>lib upload</code> + <code>proj doc import --wait</code>). Each one says how it counts conversion — in its column headers.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>tool</th><th>status</th><th>id</th></tr>
        <tr><td><span class="pill accent">project</span></td><td colspan="3"><b>${esc(story()?.project?.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr>
        ${su.exports.map((e) => `<tr><td><span class="pill">export</span></td><td class="mono small">${esc(e.file)}</td><td class="small">${esc(e.tool)}${e.common ? ' <span class="pill ok">every campaign</span>' : ""}</td><td class="small">${esc(e.status)}</td><td></td></tr>`).join("")}</table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const rows = x.records.filter((r) => r.kind !== "common");
    return `<h1>Intake — what the team wrote down, and what it concluded</h1>
      <p class="lead">Seven results, as written in each campaign's retro, pinned word for word (<code>fact add</code>), then labelled with <code>fact update --metadata</code>: campaign, channel, who, when, and the report the number came from.
      W1 is the reflection memory — “what worked” — built from all seven.${x.restored ? ` <span class="faint">(${x.restored} change(s) an earlier run wrote back were undone, so this run starts from the same memory.)</span>` : ""}</p>
      <div class="card"><table class="iso"><tr><th>id</th><th>who · when</th><th>record</th><th>source</th></tr>
        ${rows.map((r) => `<tr class="${r.kind === "reflection" ? "hl" : ""}"><td class="mono small">${esc(r.record)}</td><td class="small muted" style="white-space:nowrap">${esc(r.by)}<br>${esc(r.on)}</td>
          <td class="small">${esc(r.body)}</td><td class="small mono faint">${esc(r.source || "")}</td></tr>`).join("")}</table></div>`;
  }

  function plan() {
    const x = S.plan; if (!x) return empty("Plan the launch");
    return `<h1>Plan the Q1 launch the way memory says</h1>
      <p class="lead">Dana drafts the Q1 2027 launch brief from campaign memory: one <code>search</code>, top 8 facts. The reflection comes first, and the results agree with it.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">search · top ${x.hits.length}</h2><p class="small">“${esc(x.q)}”</p>${hitTable(x.hits)}</div>
      <div class="card mine"><h2 style="margin-top:0">The numbers in those hits</h2>
        <table class="iso"><tr><th>#</th><th>campaign</th><th>channel</th><th>remembered</th></tr>
        ${x.read.map((r, i) => `<tr><td class="mono small">${i + 1}</td><td class="small"><span class="mono">${esc(r.record)}</span> ${esc(r.campaign)}</td><td class="small">${esc(r.channel)}</td><td class="mono">${pct(r.rate)}</td></tr>`).join("")}</table>
        <p class="small">By channel: ${rankList(x.ranking)}</p>
        <div class="callout err"><b>The brief, as memory has it:</b> ${esc(x.brief)}.</div></div></div>`;
  }

  function yardsticks() {
    const x = S.yardsticks; if (!x) return empty("The yardstick check");
    const found = x.rows.filter((r) => r.found).length;
    return `<h1>${found} of ${x.rows.length} numbers are true. They are measured ${x.yardsticks.length} different ways.</h1>
      <p class="lead">Every remembered number, back to the export it names. <code>search --types document</code> returns each .xlsx as a table — one <code>Column: value</code> line per row, cited by cells.
      The yardstick is the header of the column whose cell equals the remembered number: what is counted, and what it is divided by.</p>
      <div class="card"><table class="iso"><tr><th>id</th><th>campaign</th><th>remembered</th><th>yardstick (from the export's headers)</th><th>cited</th></tr>
        ${x.rows.map((r) => `<tr class="${r.found ? "" : "hl"}"><td class="mono small">${esc(r.record)}</td><td class="small">${esc(r.campaign)}</td><td class="mono">${pct(r.remembered)}</td>
          <td class="small">${r.found ? esc(r.label) : `<span class="pill err">✗ unknown</span> <span class="faint">${esc(r.why)}</span>`}</td>
          <td class="mono small faint">${r.found ? esc(r.cite) : esc(r.source)}</td></tr>`).join("")}</table></div>
      <div class="card mine"><h2 style="margin-top:0">${x.yardsticks.length} yardsticks in one ranking</h2>
        ${x.yardsticks.map((s) => `<p class="small" style="margin:6px 0"><span class="pill ${s === "unknown" ? "err" : "warn"}">${esc(s)}</span> ${x.rows.filter((r) => r.label === s).map((r) => `<span class="mono">${esc(r.record)}</span>`).join(", ")}</p>`).join("")}
        <p class="small" style="margin-bottom:0">The step-4 ranking was not comparing channels. It was comparing yardsticks.</p></div>`;
  }

  function common() {
    const x = S.common; if (!x) return empty("One yardstick");
    const p = S.planned;
    return `<h1>One yardstick — the ranking turns over</h1>
      <p class="lead">One export measures every campaign the same way: <span class="mono">${esc(x.file)}</span> — <b>${esc(x.yardstick.label)}</b>. Its rows, read the same way, cited by cells.</p>
      <div><div class="card"><table class="iso"><tr><th>now</th><th>was</th><th>campaign</th><th>remembered</th><th>one yardstick</th></tr>
        ${x.table.map((t) => `<tr><td class="mono">${t.after}</td><td class="mono faint">${t.before ?? "—"}</td><td class="small"><span class="mono">${esc(t.record)}</span> ${esc(t.campaign)} <span class="faint mono small">· ${esc(t.cite || "")}</span></td>
          <td class="mono faint">${pct(t.remembered)}</td><td class="mono"><b>${pct(t.common)}</b></td></tr>`).join("")}</table></div>
      <div class="card mine"><h2 style="margin-top:0">By channel</h2>
        <p class="small">Before: ${rankList(x.before)}</p><p class="small">After: ${rankList(x.ranking)}</p>
        <div class="callout err small"><b>Was:</b> ${esc(x.was)}.</div>
        <div class="callout ok"><b>On one yardstick:</b> ${esc(x.brief)}.</div></div></div>
      ${p ? `<div class="card"><div class="row spread"><h2 style="margin:0">Written back — and planned again</h2><span class="pill ok">${esc(p.after_brief)}</span></div>
        <p class="small">Each result now carries its yardstick as a label; each campaign has its number on the common yardstick; the reflection is rewritten in place (same fact id, old text kept in metadata).
        Asked in new words — <code>search</code> answers a repeated question from its cache${p.tries > 1 ? ` · index caught up on try ${p.tries}` : ""}.</p>
        <p class="small">“${esc(p.q)}”</p>${hitTable(p.hits)}
        <p class="small" style="margin-bottom:0">Every number on that yardstick (<code>fact list</code>): ${rankList(p.after_rank)}</p></div>` : `<div class="card"><span class="pill busy"><span class="spinner"></span> writing back…</span></div>`}`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask;
    return `<h1>Ask</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 facts in the campaign memory, each labelled — retro result (with its yardstick, once step 6 has run), number on the common yardstick, or reflection. Or run the yardstick check again.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Which channel converts best?" value="${esc(a.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}
          <button type="button" class="chip" data-ys="1">Yardstick check now</button></div>
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
    st.querySelector("[data-ys]")?.addEventListener("click", askYardsticks);
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
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length}</h2>${hitTable(r.hits)}`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askYardsticks() {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the exports…</span>`;
    const r = await api("/api/yardsticks", {});
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="row spread"><h2>Yardstick check, now</h2><span class="pill warn">${r.yardsticks.length} yardsticks</span></div>
      <table class="iso"><tr><th>id</th><th>campaign</th><th>remembered</th><th>yardstick</th></tr>${r.rows.map((x) => `<tr class="${x.found ? "" : "hl"}"><td class="mono small">${esc(x.record)}</td><td class="small">${esc(x.campaign)}</td><td class="mono">${pct(x.remembered)}</td><td class="small">${esc(x.label)}</td></tr>`).join("")}</table>`;
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
  function resetView() { S.setup = null; S.intake = null; S.plan = null; S.yardsticks = null; S.common = null; S.planned = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's campaign project, its four exports and every record in it from your account?")) return;
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
    else if (k === "plan") S.plan = d;
    else if (k === "yardsticks") S.yardsticks = d;
    else if (k === "common") S.common = d;
    else if (k === "planned") S.planned = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project, its exports and every record in it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question of your own — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "plan", "yardsticks", "common",
    "planned", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.common ? "common" : "connect");
    subscribe();
  })();
})();

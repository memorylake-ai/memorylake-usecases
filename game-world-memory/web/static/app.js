/* Game world memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "The cast", sub: "world, player, NPCs", view: "setup" },
    { n: 3, title: "Sessions 1–3", sub: "who knows what", view: "sessions" },
    { n: 4, title: "Quick read", sub: "search every scope", view: "quick" },
    { n: 5, title: "Memory read", sub: "each caller, own scope", view: "memory" },
    { n: 6, title: "Gossip", sub: "a conflict on Mira", view: "gossip" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, sessions: null, quick: null, memory: null, gossip: null,
    ask: { q: "", as: "doran", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const scopeName = (s) => s === "world" ? "world canon" : s === story()?.player?.key ? `${story().player.name}'s event log` : (story()?.npcs || []).find((n) => n.key === s)?.name || s;
  const tagPills = (t) => (t || []).map((x) => `<span class="pill ${x === "envoy" ? "err" : x === "no-envoy" || x === "lie" ? "ok" : ""}" title="${esc(story()?.tags?.[x] || x)}">${esc(x)}</span>`).join(" ");
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  function hitTable(hits, c) {
    const mine = new Set(c?.kind === "quest" ? ["world", story()?.player?.key] : [c?.key]);
    return `<table class="iso"><tr><th>rank</th><th>record</th><th>scope</th><th>text</th></tr>
      ${hits.map((h) => { const leak = !mine.has(h.scope) && (h.asserts || []).length;
        return `<tr class="${leak ? "hl" : ""}"><td class="mono small">#${esc(h.rank)}</td><td class="mono small">${esc(h.record)}</td>
        <td class="small" style="white-space:nowrap">${esc(scopeName(h.scope))}</td><td class="small">${esc(h.body)} ${tagPills(h.asserts)}${leak ? ' <span class="pill accent">not theirs to know</span>' : ""}</td></tr>`; }).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any question · any NPC</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, sessions, quick, memory, gossip, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>World</dt><dd>${st.ready ? "from an earlier run — Run again replays the morning of session 4 on it, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">one to two minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Who knows that Rook lied?</h1>
      <p class="lead">Ashford's NPCs speak from memory, kept at three scopes: the world's canon in one project, the player's event log on one actor, and what each NPC has seen or been told on that NPC's actor.
      In session 2 the player told Mira the innkeeper they were <b>the King's envoy</b>. Mira believed it — that is her memory, and she should act on it. Read as the world's memory, it turns
      every NPC into someone who has heard it. This page drives the <code>memorylake</code> CLI for real. The studio, the game and the characters are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the world project, the four actors and every fact on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/game-world-memory-for-developers" target="_blank" rel="noopener">memorylake.ai › Game world memory for developers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("The cast");
    return `<h1>The cast — every scope its own</h1>
      <p class="lead">“Per-player + per-world memory — player history at one scope; world state at another; character relationships at a third.” One project for the world's canon,
      one actor for the player, one actor per NPC.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>holds</th><th>id</th></tr>
        ${su.rows.map((r) => `<tr><td><span class="pill">${esc(r.kind)}</span></td><td><b>${esc(r.name)}</b></td><td class="small">${esc(r.holds)}</td><td class="mono small faint">${esc(r.id)}</td></tr>`).join("")}</table></div>`;
  }

  function sessions() {
    const x = S.sessions; if (!x) return empty("Sessions 1–3");
    return `<h1>Sessions 1–3 — what happened, written where it belongs</h1>
      <p class="lead">One <code>fact add</code> per scope, word for word, then <code>fact update --metadata</code> (scope, session, source, and what the record asserts).
      Only <b>Mira's</b> actor says Rook is the King's envoy; the canon and Captain Doran say the King has sent no one; Rook's own log says it was a lie.
      ${x.replayed ? ` <span class="faint">(An earlier run played the gossip; it was taken back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="cols">${x.scopes.map((s) => `<div class="card ${s.scope === "mira" ? "mine" : ""}"><h2 style="margin-top:0">${esc(s.name)} <span class="small faint">${s.rows.length} fact(s)</span></h2>
        <table class="iso">${s.rows.map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td class="small faint" style="white-space:nowrap">s${esc(r.session)}</td><td class="small">${esc(r.body)} ${tagPills(r.asserts)}</td></tr>`).join("")}</table></div>`).join("")}</div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — one search across every scope</h1>
      <p class="lead">“Every NPC dialogue and dynamic quest call retrieves the relevant memory before generation.” The quick way: <code>search</code> the world, the player and every NPC
      for what is known about Rook, and give the hits to the <b>same reply code</b> for every caller. Rows marked <span class="pill accent">not theirs to know</span> come from a scope the caller cannot know.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.caller.label)}</h2><span class="pill">${esc(q.ans.short)}</span></div>
        <p class="small faint">${esc(q.caller.scene)} · <code>search "${esc(q.query)}"</code></p>
        ${hitTable(q.hits, q.caller)}
        <p class="small" style="margin-bottom:0">→ <b>${esc(q.ans.said)}</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} callers change what they say</h1>
      <p class="lead">The <b>same reply code</b>; each caller reads only the scope it is entitled to — an NPC its own actor (<code>fact list --actors</code>), the quest system the canon and Rook's log.
      The quest system is the control: it may know the whole truth of the world, and both reads agree.</p>
      <div class="card"><table class="iso"><tr><th>caller</th><th>quick read (every scope)</th><th>memory read (own scope)</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><b>${esc(r.caller.label)}</b></td><td class="small">${esc(r.quick.said)}</td><td class="small"><b>${esc(r.memory.said)}</b></td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table>
        <p class="small faint" style="margin-bottom:0">Every ✗ is someone else's memory read as the world's: Mira's belief, Doran's orders, Rook's own log.</p></div>
      <div class="cols">${x.rows.map((r) => `<div class="card"><h2 style="margin-top:0">${esc(r.caller.label)} <span class="small faint">reads ${esc(r.scopes.map(scopeName).join(" + "))}</span></h2>
        <table class="iso">${r.recs.map((e) => `<tr><td class="mono small">${esc(e.record)}</td><td class="small">${esc(e.body)} ${tagPills(e.asserts)}</td></tr>`).join("")}</table></div>`).join("")}</div>`;
  }

  function gossip() {
    const x = S.gossip; if (!x) return empty("Gossip");
    const g = story()?.gossip;
    if (!x.conflict) return `<h1>Gossip — no conflict yet</h1><p class="lead">${esc(g?.event)}</p>
      <div class="card"><p class="small">No conflict naming the gossip after ${esc(x.waited)} s — the check sometimes answers minutes later, as a batch. <code>python3 demo.py conflicts</code> shows it when it lands.</p></div>`;
    const c = x.conflict;
    return `<h1>Gossip — a conflict on Mira's actor, ${esc(x.waited)} s after the write</h1>
      <p class="lead">${esc(g?.event)} Written to Mira's actor, it contradicts what she believes, and MemoryLake raises a conflict on her actor.
      <code>keep_fact</code> keeps what Doran said and forgets the belief — and the conflict keeps its text, so Mira remembers being told.</p>
      <div class="card mine"><div class="row spread"><h2 style="margin:0"><code>fact conflict list --actor</code> · ${esc(c.category)} · ${esc(c.conflict_type)}</h2><span class="pill">${esc(c.name)}</span></div>
        <table class="iso">${c.facts.map((f) => `<tr><td class="mono small">${esc(f.record)}</td><td class="small">${esc(f.text)}</td><td>${f.fid === x.kept ? '<span class="pill ok">kept</span>' : '<span class="pill err">forgotten</span>'}</td></tr>`).join("")}</table>
        <p class="small faint" style="margin-bottom:0">${esc(c.id)}</p></div>
      <div class="cols"><div class="card"><h2 style="margin-top:0">Mira's actor now</h2><table class="iso">${x.mira.map((e) => `<tr><td class="mono small">${esc(e.record)}</td><td class="small">${esc(e.body)}</td></tr>`).join("")}</table></div>
        <div class="card"><h2 style="margin-top:0"><code>fact conflict get</code> · <span class="small faint">fact_snapshots</span></h2><table class="iso">${x.forgotten.map((e) => `<tr><td class="mono small">${esc(e.record)}</td><td class="small">${esc(e.body)} <span class="pill">forgotten · still on record</span></td></tr>`).join("")}</table></div></div>
      <div class="card"><h2 style="margin-top:0">Session 4, after the gossip — memory read</h2><table class="iso">
        ${x.after.map((a) => `<tr class="${a.caller.key === "mira" ? "hl" : ""}"><td class="small"><b>${esc(a.caller.label)}</b></td><td class="small">${esc(a.ans.said)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, callers = story()?.callers || [];
    return `<h1>Ask</h1>
      <p class="lead">Type any question and pick who is asking: the quick read (search every scope) and the memory read (the caller's own scope) side by side.
      Or see what an NPC says now, and from which facts.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Who is the traveller at the gate?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-as" ${ready ? "" : "disabled"} style="max-width:260px">${callers.map((c) => `<option value="${esc(c.key)}" ${c.key === a.as ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-as="${esc(e.as)}">${esc(e.q)}</button>`).join("")}
          ${(story()?.npcs || []).map((n) => `<button type="button" class="chip" data-npc="${esc(n.key)}">npc: ${esc(n.name)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("#ask-q").value.trim(), $("#ask-as").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-as").value = c.dataset.as; askQuestion(c.dataset.q, c.dataset.as); }));
      st.querySelectorAll(".chips .chip[data-npc]").forEach((c) => c.addEventListener("click", () => askNpc(c.dataset.npc)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQuestion(q, as) {
    if (!q) return; S.ask.q = q; S.ask.as = as;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading Ashford's memory…</span>`;
    const r = await api("/api/ask", { q, as });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.query)}” · ${esc(r.caller.label)}</h2>${agreePill(r.agree)}</div>
      <div class="cols"><div><h3>Quick read <span class="small faint">search, every scope</span></h3><p class="small">${r.leaks.length} hit(s) from a scope ${esc(r.caller.label)} cannot know<br>→ <b>${esc(r.quick.said)}</b></p></div>
        <div><h3>Memory read <span class="small faint">own scope</span></h3><p class="small">→ <b>${esc(r.memory.said)}</b></p></div></div>
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.caller)}</details>`);
  }
  async function askNpc(key) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/npc", { npc: key });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>${esc(r.caller.label)} — now</h2><span class="pill">${r.recs.length} fact(s) on their actor</span></div>
      <table class="iso">${r.recs.map((e) => `<tr><td class="mono small">${esc(e.record)}</td><td class="small faint">s${esc(e.session)}</td><td class="small">${esc(e.body)} ${tagPills(e.asserts)}</td></tr>`).join("")}
        ${r.forgotten.map((e) => `<tr class="hl"><td class="mono small">${esc(e.record)}</td><td class="small faint">s${esc(e.session)}</td><td class="small">${esc(e.body)} <span class="pill">forgotten · from <code>fact conflict get</code></span></td></tr>`).join("")}</table>
      <p class="small">→ <b>${esc(r.ans.said)}</b></p>`);
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
  function resetView() { S.setup = null; S.sessions = null; S.quick = null; S.memory = null; S.gossip = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's world project, the player and NPC actors and every fact on them from your account?")) return;
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
    else if (k === "sessions") S.sessions = d;
    else if (k === "quick") S.quick = d;
    else if (k === "memory") S.memory = d;
    else if (k === "gossip") S.gossip = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the world project, the four actors and every fact on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question as any caller, or see what an NPC says now — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "sessions", "quick", "memory", "gossip",
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
    show(state.status === "running" ? S.view : S.gossip ? "gossip" : "connect");
    subscribe();
  })();
})();

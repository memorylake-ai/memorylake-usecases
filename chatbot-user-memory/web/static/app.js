/* Chatbot user memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  const initials = (n) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const stripDate = (t) => t.replace(/\s*\(as of [^)]*\)\s*\.?$/, "");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Bot & users", sub: "one actor per end user", view: "setup" },
    { n: 3, title: "Sessions", sub: "chats become conversations", view: "sessions" },
    { n: 4, title: "Memory", sub: "facts owned by each user", view: "memory" },
    { n: 5, title: "Returning user", sub: "one scoped search per turn", view: "returning" },
    { n: 6, title: "Forget", sub: "the right to be forgotten", view: "forget" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, bot: null, users: {}, project: null,
    sessions: {}, profiles: {}, contexts: {}, isolation: null, forgot: {}, llm: {},
    ask: { user: "alice", q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const userOf = (key) => (S.data?.users || []).find((u) => u.key === key);

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask as a user<small>scoped search</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() { const r = { connect, setup, sessions, memory, returning, forget, ask }[S.view]; $("#stage").innerHTML = r ? r() : ""; bindStage(); }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Sessions project</dt><dd>${S.project ? `${esc(S.project.name)} <span class="mono muted">${esc(S.project.id)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.project ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2 minutes · everything it creates is prefixed <code>mlu-cbm-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Your chatbot forgets every user. This is the memory layer that fixes it.</h1>
      <p class="lead">Two users talk to <b>Nimbus</b>, a travel concierge bot, over five sessions on four channels. Every session is stored as a
      conversation between the user and the bot; the facts MemoryLake extracts land on <em>the user</em>. When they come back, one search scoped to
      their actor is the memory for the turn — and one user's memory never answers for another. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the users and their memory.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/add-long-term-memory-to-your-chatbot" target="_blank" rel="noopener">memorylake.ai › add long-term memory to your chatbot</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const bot = S.data.bot;
    return `<h1>The bot, and one actor per end user</h1>
      <p class="lead">An <b>actor</b> is who a memory belongs to. The bot is an <code>ASSISTANT</code> actor; each of your users is a <code>HUMAN</code> actor whose
      <code>custom_id</code> is simply your own user id. A project holds the bot's sessions, but the facts will be owned by the users.</p>
      <h2>The bot</h2>
      <div class="card person"><span class="avatar bot">${initials(bot.display)}</span><div><div class="name">${esc(bot.display)}</div><div class="role">${esc(bot.role)} · ASSISTANT</div></div>
        <span class="status pill ${S.bot ? "ok" : ""}">${S.bot ? "actor ready" : "pending"}</span></div>
      <h2>End users</h2>
      <div class="grid">${S.data.users.map((u) => `<div class="card person"><span class="avatar user">${initials(u.display)}</span>
        <div><div class="name">${esc(u.display)}</div><div class="role">HUMAN · custom_id <code>mlu-cbm-user-${esc(u.user_id)}</code></div></div>
        <span class="status pill ${S.users[u.key] ? "ok" : ""}">${S.users[u.key] ? "actor ready" : "pending"}</span></div>`).join("")}</div>
      <h2>Sessions project</h2>
      <div class="card row spread"><div><b>${esc(S.data.project.name)}</b><div class="muted small">custom id <code>${esc(S.data.project.custom_id)}</code>${S.project ? ` · <span class="mono">${esc(S.project.id)}</span>` : ""}</div></div>
        <span class="pill ${S.project ? "ok" : ""}">${S.project ? (S.project.fresh ? "created" : "exists") : "pending"}</span></div>`;
  }

  function sessionCard(u, s) {
    const st = S.sessions[s.custom_id] || {}; const stored = st.stored || 0; const total = s.turns.length;
    const status = st.cooked ? `<span class="pill ok">memory ready</span>` : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting…</span>`
      : stored ? `<span class="pill accent">${stored}/${total}</span>` : `<span class="pill">queued</span>`;
    return `<div class="card session"><div class="session-head"><h3>${esc(s.title)}</h3><span class="chan">${esc(s.channel)}</span><span class="muted small">${fmtDate(s.date)}</span><span style="margin-left:auto">${status}</span></div>
      <div class="bubbles">${s.turns.map(([who, text], i) => `<div class="bubble ${who} ${i < stored ? "stored" : ""}">${esc(text)}${i < stored ? '<span class="tick">✓</span>' : ""}</div>`).join("")}</div></div>`;
  }
  function sessions() {
    if (!S.data) return "";
    return `<h1>Every chat becomes a conversation between the user and the bot</h1>
      <p class="lead">One <code>DIRECT</code> conversation per session, the channel in metadata, each message attributed to its speaker and time-stamped.
      Messages store instantly; the facts are extracted in the background, so the page waits for <code>cook-status</code>.</p>
      <div class="cols">${S.data.users.map((u) => `<div><div class="user-head"><span class="avatar user">${initials(u.display)}</span><h2>${esc(u.display)}</h2><span class="muted small">${esc(u.user_id)}</span></div>
        ${u.sessions.map((s) => sessionCard(u, s)).join("")}</div>`).join("")}</div>`;
  }

  function memory() {
    if (!S.data) return "";
    if (!Object.keys(S.profiles).length) return `<h1>What the bot knows</h1><div class="empty">Run the demo first — the per-user facts show up here once the sessions are processed.</div>`;
    return `<h1>What the bot knows — per user, owned by the user</h1>
      <p class="lead">Nobody tagged anything. Every fact below is owned by the user's actor: <code>fact list --actors &lt;user&gt;</code>. The bot and the project own none.
      Changed preferences arrive dated, latest first.</p>
      <div class="cols">${S.data.users.map((u) => { const facts = S.profiles[u.key] || []; return `<div><div class="user-head"><span class="avatar user">${initials(u.display)}</span><h2>${esc(u.display)}</h2><span class="pill accent">${facts.length} facts</span><span class="mono faint small" style="margin-left:auto">${esc(S.users[u.key]?.id || "")}</span></div>
        ${facts.slice().reverse().map((f) => `<div class="memfact ${S.forgot[u.key]?.deleted?.id === f.id ? "gone" : ""}"><span>${esc(stripDate(f.fact))}</span><span class="when">${f.date || ""}</span></div>`).join("") || '<div class="empty">no facts yet</div>'}</div>`; }).join("")}</div>`;
  }

  function block(text) { return `<div class="block">${esc(text).replace(/(&lt;\/?memory[^&]*&gt;)/g, '<span class="tag">$1</span>')}</div>`; }
  function returning() {
    if (!S.data) return "";
    if (!Object.keys(S.contexts).length) return `<h1>A returning user on a new channel</h1><div class="empty">Run the demo first.</div>`;
    const iso = S.isolation ? `<h2>Isolation check</h2><div class="card"><p class="muted small" style="margin:0 0 6px">A query only one user's memory can answer, asked in the other user's memory, must return none of their facts.</p>
      <table class="iso"><tr><th>asked in</th><th>query</th><th>owner's own hits</th><th>leaked</th><th></th></tr>
      ${S.isolation.map((r) => `<tr><td>${esc(userOf(r.asker)?.display || r.asker)}</td><td>“${esc(r.query)}”</td><td>${r.owner_hits} in ${esc(userOf(r.owner)?.display || r.owner)}'s</td><td>${r.leaked}</td><td class="verdict ${r.leaked ? "bad" : "ok"}">${r.leaked ? "LEAK" : "isolated ✓"}</td></tr>`).join("")}</table></div>` : "";
    return `<h1>A returning user on a new channel</h1>
      <p class="lead">The user's incoming message is the search query, scoped to their actor. The result is the memory block your backend prepends to the model prompt — one call, no re-discovery.</p>
      <div class="cols">${S.data.users.map((u) => { const c = S.contexts[u.key]; if (!c) return ""; return `<div class="card"><div class="user-head"><span class="avatar user">${initials(u.display)}</span><h2>${esc(u.display)}</h2><span class="chan">${esc(c.channel)}</span></div>
        <div class="question">${esc(c.question)}</div>${block(c.block)}
        <div class="faint small mono" style="margin-top:6px">search "${esc(c.question)}" --actors ${esc(S.users[u.key]?.id || "…")} --types fact</div>
        ${S.llm[u.key]?.reply ? `<div class="bubble bot stored" style="margin-top:10px;max-width:100%"><b>Nimbus:</b> ${esc(S.llm[u.key].reply)}</div>` : ""}</div>`; }).join("")}</div>${iso}`;
  }

  function forget() {
    const entries = Object.entries(S.forgot);
    if (!entries.length) return `<h1>The right to be forgotten</h1><div class="empty">Run the demo first.</div>`;
    return `<h1>The right to be forgotten</h1>
      <p class="lead">Facts are immutable, so forgetting is a delete: find the fact with a scoped search, delete it by id, search again. Deleting the actor removes everything they own — that is what <b>Clean up</b> does.</p>
      ${entries.map(([key, f]) => { const u = userOf(key); return `<div class="card"><div class="user-head"><span class="avatar user">${initials(u.display)}</span><h2>${esc(u.display)}</h2></div>
        <div class="question">${esc(f.request)}</div>
        <h2 style="font-size:14px">Before — <span class="mono muted">search "${esc(userOf(key)?.forget?.query || "")}" --actors …</span></h2>
        ${f.before.map((x) => `<div class="memfact ${f.deleted && x.id === f.deleted.id ? "gone" : ""}"><span>${esc(x.fact)}</span>${f.deleted && x.id === f.deleted.id ? '<span class="when">deleted</span>' : ""}</div>`).join("")}
        ${f.deleted ? `<div class="mono small" style="margin:8px 0">$ memorylake fact delete --actor … ${esc(f.deleted.id)}</div>` : ""}
        <h2 style="font-size:14px">After — same search</h2>
        ${f.after.length ? f.after.map((x) => `<div class="memfact"><span>${esc(x.fact)}</span></div>`).join("") : '<div class="muted small">nothing matched</div>'}
        <div style="margin-top:8px">${f.deleted && !f.after.some((x) => x.id === f.deleted.id) ? '<span class="pill ok">forgotten ✓</span>' : '<span class="pill err">still returned</span>'}</div></div>`; }).join("")}`;
  }

  function ask() {
    const users = S.data?.users || [];
    const ready = !!S.users[S.ask.user];
    return `<h1>Ask as a user</h1>
      <p class="lead">The same scoped search the bot would run at the start of a turn. Pick a user, type what they would say, and see the memory block that comes back.</p>
      <div class="card"><div class="userpick">${users.map((u) => `<button type="button" class="chip ${S.ask.user === u.key ? "on" : ""}" data-user="${u.key}">${esc(u.display)}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. book me a table for Friday" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${["what do you know about me", "any dietary restrictions I told you about", "where should flights depart from", "how do I like my invoices", "what seat do I prefer"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelectorAll(".userpick .chip").forEach((c) => c.addEventListener("click", () => { S.ask.user = c.dataset.user; S.ask.html = ""; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { user: S.ask.user, query: q, top_k: 8 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `${r.facts.length ? r.facts.map((f) => `<div class="memfact"><span>${esc(f.fact)}</span><span class="when">${(f.score ?? 0).toFixed(2)}</span></div>`).join("") : '<p class="muted">No facts matched.</p>'}${block(r.block)}`;
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
  function resetView() { S.sessions = {}; S.profiles = {}; S.contexts = {}; S.isolation = null; S.forgot = {}; S.llm = {}; }
  async function runDemo(reset) {
    banner(""); if (reset || !S.project) { resetView(); S.stepStatus = { 1: "done" }; }
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo sessions, the project, both users (and every fact they own) and the bot from your account?")) return;
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
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView(); term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "bot") S.bot = d;
    else if (k === "user") S.users[d.key] = d;
    else if (k === "project") S.project = { id: d.id, name: d.name, fresh: !!d.fresh };
    else if (k === "session") S.sessions[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false };
    else if (k === "turn") (S.sessions[d.custom_id] ||= {}).stored = d.index;
    else if (k === "cooked") Object.values(S.sessions).forEach((s) => { if (s.id === d.id) s.cooked = true; });
    else if (k === "profile") S.profiles[d.user] = d.facts;
    else if (k === "context") S.contexts[d.user] = d;
    else if (k === "isolation") S.isolation = d.results;
    else if (k === "forgot") S.forgot[d.user] = d;
    else if (k === "llm") S.llm[d.user] = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.project = null; S.bot = null; S.users = {}; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the users and their memory are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Both users have a memory of their own; try asking as either of them.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "bot", "user", "project", "session", "turn", "cooked", "profile", "context", "isolation", "forgot", "llm", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  (async () => {
    renderSteps();
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data; if (state.project) S.project = state.project;
    const target = state.last_event_id; setConn();
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const h = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : Object.keys(S.contexts).length ? "returning" : "connect");
    subscribe();
  })();
})();

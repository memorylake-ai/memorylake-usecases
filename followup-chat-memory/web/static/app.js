/* Follow-up chat memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "What Hearth remembers", sub: "65 facts from earlier chats", view: "memory" },
    { n: 4, title: "Latest message", sub: "the usual DIY lookup", view: "last" },
    { n: 5, title: "Last few turns", sub: "the usual fix", view: "window" },
    { n: 6, title: "MemoryRouter", sub: "the conversation goes in", view: "router" },
    { n: 7, title: "What was attached", sub: "Claude copies it out", view: "attached" },
    { n: 8, title: "Scoreboard", sub: "same questions, same model", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, turns: { last: [], window: [], router: [] }, inspect: [], score: null, unavailable: null, summary: null,
    chat: [], termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Chat<small>your own follow-ups</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, last: () => arm("last"), window: () => arm("window"), router: () => arm("router"), attached, score, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (ok) => ok == null ? '<span class="mark">·</span>' : `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2½ minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-fcm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“And Maya?” — a follow-up doesn't say what it is about.</h1>
      <p class="lead">Hearth is a family-assistant chatbot that remembers what Dana told it in earlier chats. Tonight she asks seven quick questions, and only the first one names
      who it is about. A chatbot that looks up memory with <b>the latest message</b> gets someone else's facts — or nothing — and says it doesn't know.
      <b>MemoryRouter</b> takes the conversation and reads it. This demo drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 21 calls on gpt-4o-mini and 3 on claude-haiku-4-5 per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/conversation-memory-api-for-chatbots" target="_blank" rel="noopener">memorylake.ai › Conversation memory API for chatbots</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    return `<h1>One project for Hearth's memory, one boundary for the chatbot</h1>
      <p class="lead">Everything Hearth remembers about Dana lives in one MemoryLake project. The chatbot never assembles memory itself when it uses MemoryRouter:
      it sends the conversation and adds <code>--boundary</code> to an ordinary model call.</p>
      <div class="cols"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(S.world?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(S.world?.boundary || "pending")}</b></dd></dl>
        <h3>Each MemoryRouter turn</h3>
        <div class="cmdline">router chat "&lt;Dana's message&gt;" --model gpt-4o-mini --body &lt;conversation so far&gt; --boundary ${esc(S.world?.boundary || "…")}</div></div>
        <div class="card"><h3 style="margin-top:0">Dana's family</h3><div class="factlist">${S.data.members.map((m) => `<div class="memfact"><span><b>${esc(m.name)}</b> · ${esc(m.role)}</span></div>`).join("")}</div></div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What Hearth remembers") : empty("What Hearth remembers");
    return `<h1>What Hearth remembers — ${fmt(S.memory.facts)} facts</h1>
      <p class="lead">${S.memory.members.length} family members × ${S.memory.attributes.length} things Dana mentioned in earlier chats (allergies, doctors, bedtimes, birthdays …), stored with <code>fact add</code>.</p>
      <div class="cols"><div class="card"><h3 style="margin-top:0">A few of the facts</h3><div class="factlist">${S.data.sample.map((f) => `<div class="memfact"><span>${esc(f)}</span></div>`).join("")}</div></div>
        <div class="card"><h3 style="margin-top:0">Tonight's chat</h3><ol class="steps-list">${S.data.chat.map((t) => `<li>${esc(t.say)} <span class="faint small">(about ${esc(t.about)})</span></li>`).join("")}</ol>
        <p class="muted small">Only the first question names who it is about. Every answer is in memory.</p></div></div>`;
  }

  function bubble(t, armKey) {
    const mem = t.memory ? `<div class="lookup"><span class="faint small">${armKey === "window" ? `lookup “${esc(t.query)}”` : "lookup"} → top ${t.memory.length}${t.found ? "" : ' · <b class="bad-ink">not the fact this turn needs</b>'}</span>
        ${t.memory.map((m) => `<div class="memfact small"><span>${esc(m)}</span></div>`).join("")}</div>` : "";
    return `<div class="msg user"><span class="who">Dana</span>${esc(t.say)}</div>${mem}
      <div class="msg bot ${t.ok === false ? "wrong" : ""}"><span class="who">Hearth ${markOf(t.ok)}</span>${esc(t.reply || "…")}</div>`;
  }

  function arm(key) {
    const a = S.data?.arms?.[key] || { label: key, how: "" };
    const ts = S.turns[key];
    if (!ts.length) return unavailable() || (running() ? waiting(a.label) : empty(a.label));
    const got = ts.filter((t) => t.ok != null);
    return `<h1>${esc(a.label)} — ${got.filter((t) => t.ok).length}/${got.length} right</h1>
      <p class="lead">${esc(a.how).replace(/`([^`]+)`/g, "<code>$1</code>")}. Same seven questions, same model (gpt-4o-mini).</p>
      <div class="card chat">${ts.map((t) => bubble(t, key)).join("")}</div>`;
  }

  function attached() {
    if (!S.inspect.length) return unavailable() || (running() ? waiting("What MemoryRouter attached") : empty("What MemoryRouter attached"));
    return `<h1>What MemoryRouter attached to the follow-ups</h1>
      <p class="lead">The same conversation through the same boundary to <b>claude-haiku-4-5</b>, told in its system prompt to copy out every remembered entry before answering.
      Next to it: what a lookup on the latest message alone brought back.</p>
      <div class="grid2">${S.inspect.map((c) => `<div class="card"><h3 style="margin-top:0">“${esc(c.say)}” <span class="faint small">about ${esc(c.about)}</span></h3>
        <p class="small muted">latest-message lookup</p>${c.lookup.map((l) => `<div class="memfact small"><span>${esc(l)}</span></div>`).join("")}
        <p class="small muted">MemoryRouter attached ${c.lines.length}${c.target ? "" : " — not the fact this turn needs"}</p>${c.lines.map((l) => `<div class="memfact rule small"><span>${esc(l)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function score() {
    if (!S.score) return unavailable() || (running() ? waiting("Scoreboard") : empty("Scoreboard"));
    const tot = S.score.totals || {};
    return `<h1>Scoreboard</h1><p class="lead">The same seven questions, the same model; only how memory gets into the request differs.</p>
      <div class="card"><table class="lint"><thead><tr><th>Dana asks</th><th>latest message</th><th>last few turns</th><th>MemoryRouter</th></tr></thead><tbody>
        ${S.score.rows.map((r) => `<tr><td>${esc(r.say)}</td><td>${markOf(r.last)}</td><td>${markOf(r.window)}</td><td>${markOf(r.router)}</td></tr>`).join("")}
        <tr class="hl"><td><b>right</b></td><td>${tot.last ?? "–"}/7</td><td>${tot.window ?? "–"}/7</td><td>${tot.router ?? "–"}/7</td></tr></tbody></table>
        <p class="muted small">Lookup on the latest message misses the follow-ups that don't name anyone. Adding the last few turns to the query fixes those and breaks
        the ones right after a change of subject. MemoryRouter reads the conversation.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    return `<h1>Chat — try your own follow-ups</h1>
      <p class="lead">Each message is answered twice: with a lookup on your latest message, and through MemoryRouter with the conversation. Both see the same history
      (MemoryRouter's replies). Start with a name, then follow up without one.</p>
      <div class="card"><div class="chat" id="chat-log">${S.chat.map((x) => `<div class="msg user"><span class="who">Dana</span>${esc(x.say)}</div>
          <div class="pair"><div class="msg bot"><span class="who">latest-message lookup</span>${esc(x.last.reply)}<div class="faint small">${x.last.memory.map(esc).join(" · ")}</div></div>
          <div class="msg bot router"><span class="who">MemoryRouter</span>${esc(x.router.reply)}</div></div>`).join("") || (ready ? '<div class="empty">No messages yet.</div>' : '<div class="empty">Run the demo first.</div>')}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What does Sam play on weekends?" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Send</button><button type="button" class="btn ghost" data-act="newchat" ${ready ? "" : "disabled"}>New chat</button></form>
        <div class="chips">${["What does Sam play on weekends?", "And Maya?", "Who's her doctor?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>`;
  }

  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo());
    st.querySelector('[data-act="newchat"]')?.addEventListener("click", () => { S.chat = []; renderStage(); });
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); send($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => send(c.dataset.q)));
    }
  }
  async function send(say) {
    if (!say) return;
    const history = []; S.chat.forEach((x) => history.push({ role: "user", content: x.say }, { role: "assistant", content: x.router.reply }));
    const log = $("#chat-log"); if (log) log.insertAdjacentHTML("beforeend", `<div class="msg user"><span class="who">Dana</span>${esc(say)}</div><span class="pill busy" id="sending"><span class="spinner"></span> answering two ways…</span>`);
    const r = await api("/api/ask", { history, say });
    if (r) S.chat.push(r);
    if (S.view === "ask") renderStage();
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
    ["#btn-run", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.memory = null; S.turns = { last: [], window: [], router: [] }; S.inspect = []; S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's memory project (and every fact in it) and its boundary?")) return;
    banner(""); const r = await api("/api/cleanup", {}); if (r) { S.st = r; setConn(); }
  });

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

  const stepOf = (i) => STEPS.find((s) => s.n === i);
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView(); term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "memory") S.memory = d;
    else if (k === "turn") { const a = S.turns[d.arm]; a[d.turn - 1] = d; }
    else if (k === "inspect") S.inspect.push(d);
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the memory project, its facts and its boundary are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Chat: your own follow-ups, answered two ways.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "turn", "inspect", "scoreboard", "summary", "router_unavailable", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
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
    S.st = state; setConn();
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.score ? "score" : "connect");
    subscribe();
  })();
})();

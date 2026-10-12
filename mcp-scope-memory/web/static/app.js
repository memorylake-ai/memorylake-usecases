/* Memory scopes for MCP tools — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const initials = (n) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "projects, people, the bot", view: "setup" },
    { n: 3, title: "Boundaries", sub: "one per tool", view: "boundaries" },
    { n: 4, title: "One question", sub: "three tools, three answers", view: "answers" },
    { n: 5, title: "The team changes", sub: "what the scopes still say", view: "changes" },
    { n: 6, title: "Audit & repair", sub: "every reference checked", view: "audit" },
    { n: 7, title: "After the repair", sub: "the same question again", view: "after" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0, inRun: false,
    stepStatus: {}, setup: null, boundaries: null, answers: null, changes: null, audit: null, repair: null, after: null, audit2: null,
    ask: { q: "", key: "priya-ide", html: "" }, askAudit: null, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>through any boundary</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, boundaries, answers, changes, audit, after, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const failLine = (r) => r.rc ? `HTTP ${r.status} ${r.code || ""} — ${r.message}` : "accepted";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Boundaries</dt><dd>${st.ready ? "from an earlier run — a new run cleans them up first" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>One memory scope per AI tool — and what happens when the team changes.</h1>
      <p class="lead">An MCP tool connects to MemoryLake with an API key and a <b>memory store identifier</b>: a <b>boundary</b>, a saved scope of projects, one person and one agent.
      MCP clients pick it on the OAuth consent screen; Memory Router takes it as <code>?boundary_id=</code>. Fernhill Robotics gives Priya's editor, Tom's editor and the CI review bot one boundary each,
      asks all three the same question, then changes the team — and finds out what a boundary does with references to things that are gone. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the boundaries, the agents and their memories, the projects and the people.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/persistent-memory-via-mcp-for-ai-tools" target="_blank" rel="noopener">memorylake.ai › persistent memory via MCP for AI tools</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  const factList = (facts) => facts.map((f) => `<div class="memfact"><span class="mk">•</span><span class="body">${esc(f)}</span></div>`).join("");
  function setup() {
    if (!story()) return "";
    const su = S.setup;
    const projects = story().projects.map((p) => `<div class="card"><h2 style="margin-top:0">${esc(p.name)}</h2><div class="muted small">${esc(p.description)}</div>${factList(p.facts)}</div>`).join("");
    const people = story().people.map((p) => `<div class="card person"><span class="avatar user">${initials(p.display)}</span><div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.role)}</div></div>
        <span class="status pill ${su ? (p.bind === false ? "warn" : "ok") : ""}">${su ? (p.bind === false ? "not on the workspace" : "bound") : "pending"}</span></div>`).join("");
    const bot = story().agents.find((a) => a.key === "bot");
    return `<h1>Four projects, four people, one review bot</h1>
      <p class="lead">Every memory is pinned with <code>fact add</code>: project notes on the projects, preferences on each person (<code>--actor</code>), review lessons on the bot (<code>--agent</code>).
      This demo is about <em>scope</em>, so nothing depends on extraction.</p>
      <div class="grid">${people}</div>
      <div class="grid" style="margin-top:16px">${projects}</div>
      <div class="card"><h2 style="margin-top:0">${esc(bot.name)} <span class="muted small">agent</span></h2>${factList(bot.facts)}</div>`;
  }

  function scopeLine(b) {
    return `<div class="muted small">projects: ${esc(b.projects)} · person: ${esc(b.human)} · agent: ${esc(b.agent)}</div>`;
  }
  function boundaries() {
    const b = S.boundaries; if (!b) return empty("One boundary per tool");
    return `<h1>One memory store identifier per tool</h1>
      <p class="lead"><code>boundary create --projects … --human-actor … --agent …</code>. For an agent the server adds <code>assistant_actor_id</code>, the agent's own actor.</p>
      ${b.boundaries.map((x) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(x.name)}</h2><span class="mono small">${esc(x.id)}</span></div>
        <div class="small" style="margin:6px 0">← ${esc(x.tool)}</div>${scopeLine(x.view)}
        ${x.assistant_actor_id ? `<div class="mono small faint">assistant_actor_id ${esc(x.assistant_actor_id)}</div>` : ""}</div>`).join("")}
      <div class="card"><h2 style="margin-top:0">Checked when it is written</h2>
        <p class="small">A boundary for Kai, a contractor not yet on the workspace:</p>
        <p><span class="pill ${b.refused.rc ? "ok" : "err"}">${b.refused.rc ? "refused" : "accepted (unexpected)"}</span> <span class="mono small">${esc(failLine(b.refused))}</span></p></div>`;
  }

  function answerCard(a) {
    if (!a.ok) return `<div class="card"><h2 style="margin-top:0">${esc(a.name)}</h2><p><span class="pill err">search fails</span></p><p class="mono small">${esc(a.error)}</p></div>`;
    return `<div class="card"><h2 style="margin-top:0">${esc(a.name)}</h2>
      ${a.hits.map((h) => `<div class="memfact ${h.inside ? "clean" : "personal"}"><span class="mk">${h.inside ? "✓" : "✗"}</span><span class="body"><b>[${esc(h.owner)}]</b> ${esc(h.fact)}</span></div>`).join("") || '<p class="muted">nothing</p>'}
      <p class="tally"><span class="pill ${a.outside ? "err" : "ok"}">${a.hits.length} hit(s) · ${a.outside} from outside this boundary</span></p></div>`;
  }
  function answerBlock(r, title, lead) {
    return `<h1>${title}</h1><p class="lead">${lead}</p>
      <div class="card"><div class="question"><b>Q:</b> ${esc(r.question)}</div></div>
      <div class="grid3">${r.answers.map(answerCard).join("")}</div>`;
  }
  function answers() {
    const r = S.answers; if (!r) return empty("One question, three tools");
    return answerBlock(r, "One question, three tools, three answers",
      "Each tool's boundary is read with <code>boundary get</code> and expanded into a search: its projects, its person and its agent's actor. Every hit is labelled with the scope it belongs to and checked against the boundary.");
  }

  function changes() {
    const c = S.changes; if (!c) return empty("The team changes");
    const p = c.probes;
    return `<h1>The team changes — the boundaries do not</h1>
      <div class="card"><ol class="steps-list">${Object.values(story().changes).map((t) => `<li>${esc(t)}</li>`).join("")}</ol></div>
      <p class="lead"><code>boundary get</code> after the changes: nothing was cascaded.</p>
      ${Object.values(c.after).map((b) => `<div class="card"><h2 style="margin-top:0">${esc(b.name)}</h2>${scopeLine(b.view)}</div>`).join("")}
      <div class="card"><h2 style="margin-top:0">What that does to the tools</h2>
        <p class="small">CI review bot, searching through its boundary → <span class="pill ${p.search.ok ? "warn" : "err"}">${p.search.ok ? "still answers" : "fails"}</span></p><p class="mono small">${esc(p.search.error || "")}</p>
        <p class="small">rename Tom's boundary → <span class="pill ${p.rename_tom.rc ? "err" : "warn"}">${p.rename_tom.rc ? "refused" : "accepted"}</span></p><p class="mono small">${esc(failLine(p.rename_tom))}</p>
        <p class="small">rename the CI bot's boundary → <span class="pill ${p.rename_bot.rc ? "err" : "warn"}">${p.rename_bot.rc ? "refused" : "accepted"}</span></p><p class="mono small">${esc(failLine(p.rename_bot))}</p>
        <p class="muted small" style="margin-bottom:0">A deleted project lingers silently and breaks every search that uses it; a sealed person or a deleted agent locks the boundary until that reference is cleared or replaced.</p></div>`;
  }

  function auditCards(a) {
    return a.rows.map((r) => `<div class="card"><div class="row spread"><h2 style="margin:0">${r.ok ? "✓" : "✗"} ${esc(r.name)}</h2><span class="mono small">${esc(r.id)}</span></div>
      ${r.refs.map((x) => `<div class="memfact ${x.ok ? "clean" : "personal"}"><span class="mk">${x.ok ? "✓" : "✗"}</span><span class="body">${esc(x.kind)} · ${esc(x.name)}${x.why ? `<div class="leakwhy">↳ ${esc(x.why)}</div>` : ""}</span></div>`).join("")}</div>`).join("")
      + `<p><span class="pill ${a.stale ? "err" : "ok"}">${a.rows.length} boundaries · ${a.stale} stale reference(s)</span></p>`;
  }
  function audit() {
    const a = S.audit; if (!a) return empty("Audit and repair");
    const r = S.repair;
    return `<h1>Audit — every reference checked against what exists today</h1>
      <p class="lead">Projects with <code>proj get</code>, the person with <code>fact list --actors</code> (a sealed actor answers 404), the agent with <code>agent get</code>.</p>
      ${auditCards(a)}
      ${r ? `<div class="card mine"><h2 style="margin-top:0">Repaired — one <code>boundary update</code> each, same id</h2>
        ${r.fixes.map((f) => `<div class="memfact clean"><span class="mk">✓</span><span class="body">${esc(f.name)} → <b>${esc(f.now)}</b><div class="trace">${esc(f.scope)}</div><div class="mono small faint">boundary update … ${esc(f.args.join(" "))}</div></span></div>`).join("") || '<p class="muted">nothing to repair</p>'}
        <p class="muted small" style="margin-bottom:0"><code>--projects</code> replaces the list, so it names every project to keep. The tools keep their identifier.</p></div>` : ""}`;
  }

  function after() {
    const r = S.after; if (!r) return empty("After the repair");
    return answerBlock(r, "After the repair — the same question works again",
      "Ana took Tom's seat; review-bot-v2 took the bot's, with the lessons copied over. Tom's preferences appear in no answer: his seat moved, his memory did not.")
      + (S.audit2 ? `<h2>Audit again</h2>${auditCards(S.audit2)}` : "")
      + `<p class="muted small">The boundary ids are in <code>out/boundaries.json</code>.</p>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    const bs = story()?.boundaries || [];
    const nameOf = (b) => (b.key === "tom-ide" && S.repair ? "Ana's editor assistant" : b.name);
    const opt = (b) => `<button type="button" class="chip scope ${S.ask.key === b.key ? "on" : ""}" data-key="${b.key}">${esc(nameOf(b))}</button>`;
    return `<h1>Ask through a boundary</h1>
      <p class="lead"><code>boundary get</code>, then <code>search</code> with its projects and actors. Hits are labelled with the scope they come from.</p>
      <div class="card"><div class="chips" style="margin:0 0 10px">${bs.map(opt).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. where do network errors get retried" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${["where do network errors get retried", "how are tests written for the payout screen", "who needs to review a retry change", "what happens to a parked payout"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Audit the boundaries now</h2><button class="btn" data-act="audit" ${ready ? "" : "disabled"}>Run audit</button></div>
        <div id="audit-result" style="margin-top:10px">${S.askAudit ? auditCards(S.askAudit) : ""}</div></div>`;
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
    st.querySelector('[data-act="audit"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; const box = $("#audit-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> auditing…</span>`;
      const r = await api("/api/audit", {}); e.target.disabled = false;
      if (r) { S.askAudit = r; if (document.body.contains(box)) box.innerHTML = auditCards(r); } else box.innerHTML = "";
    });
    st.querySelectorAll(".chip.scope").forEach((b) => b.addEventListener("click", () => {
      S.ask.key = b.dataset.key; st.querySelectorAll(".chip.scope").forEach((x) => x.classList.toggle("on", x === b)); if (S.ask.q) askQuery(S.ask.q);
    }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip:not(.scope)").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, boundary: S.ask.key, top_k: 6 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = answerCard(r) + `<p class="muted small">Search always returns its closest facts, related or not — but only from inside the boundary.</p>`;
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
  function resetView() { S.setup = null; S.boundaries = null; S.answers = null; S.changes = null; S.audit = null; S.repair = null; S.after = null; S.audit2 = null; S.askAudit = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, agents and their memories, projects and people from your account?")) return;
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
  // A boundary as names, for display (the event carries ids; setup gave us the names).
  function names() {
    const m = {};
    (S.setup?.projects || []).forEach((p) => (m[p.id] = p.name));
    (S.setup?.people || []).forEach((p) => (m[p.id] = p.name));
    (S.setup?.agents || []).forEach((a) => { m[a.id] = a.name; m[a.actor_id] = a.name; });
    Object.assign(m, S.extraNames || {});
    return m;
  }
  function view(b) {
    const m = names();
    return { projects: (b.project_ids || []).map((p) => m[p] || p).join(", ") || "(none)", human: b.human_actor_id ? (m[b.human_actor_id] || b.human_actor_id) : "—", agent: b.agent_id ? (m[b.agent_id] || b.agent_id) : "—" };
  }
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; S.step = d.index || 0;
      if (d.index === 2) { resetView(); S.inRun = true; S.extraNames = {}; }
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") {
      term("note", ev.text);
      const m = ev.text.match(/created the agent (\S+) → (agent-\w+) \(its own actor: (actor-\w+)\)/);
      if (m) { S.extraNames = S.extraNames || {}; S.extraNames[m[2]] = m[1]; S.extraNames[m[3]] = m[1]; }
    }
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "boundaries") { d.boundaries.forEach((b) => (b.view = view(b))); S.boundaries = d; }
    else if (k === "answers") S.answers = d;
    else if (k === "changes") { Object.values(d.after).forEach((b) => (b.view = view(b))); S.changes = d; }
    else if (k === "audit") { if (!S.inRun) S.askAudit = d; else if (S.step === 7) S.audit2 = d; else S.audit = d; }
    else if (k === "repair") S.repair = d;
    else if (k === "answers_after") S.after = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; S.inRun = false; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, agents, projects and people are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — the same search through any boundary, or audit them again.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); S.inRun = false; term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "boundaries", "answers", "changes", "audit", "repair",
    "answers_after", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.after ? "after" : "connect");
    subscribe();
  })();
})();

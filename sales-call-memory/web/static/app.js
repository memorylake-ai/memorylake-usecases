/* Sales call memory — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const side = (key) => (key === "sdr-jordan" || key === "ae-sam") ? "northwind" : "acme";

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "People & account", sub: "actors, one project per account", view: "setup" },
    { n: 3, title: "Calls", sub: "transcripts become memory", view: "calls" },
    { n: 4, title: "Notes", sub: "documents, indexed", view: "notes" },
    { n: 5, title: "Memory", sub: "what MemoryLake remembers", view: "memory" },
    { n: 6, title: "Brief", sub: "the hand-off, from search", view: "brief" },
  ];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},            // n -> pending | running | done | error
    actors: {}, project: null,
    calls: {},                 // custom_id -> {id, stored, cooked, pinned}
    notes: {},                 // name -> {status}
    facts: null, brief: null, briefSections: [],
    ask: { q: "", html: "" },
    term: [], termCount: 0, lastEvent: 0, caughtUp: false,
  };

  // ------------------------------------------------------------------ rendering: steps
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `
      <button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span>
        <span class="t">${s.title}<small>${s.sub}</small></span>
      </button>`).join("") + `
      <button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask">
        <span class="n">?</span><span class="t">Ask the memory<small>free-form search</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  // ------------------------------------------------------------------ rendering: stage
  function renderStage() {
    const stage = $("#stage");
    const r = { connect, setup, calls, notes, memory, brief, ask }[S.view];
    stage.innerHTML = r ? r() : "";
    bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card">
        <div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px">
          <dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Project</dt><dd>${S.project ? `${esc(S.project.name)} <span class="mono muted">${esc(S.project.id)}</span>` : '<span class="muted">not created yet</span>'}</dd>
        </dl>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.project ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>mlu-scm-</code></span>
        </div>
      </div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required>
          <button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url">
          <option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option>
          <option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option>
        </select>
        <label for="workspace">Workspace id <span class="faint">(optional — defaults to your default workspace)</span></label>
        <input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px">
          <button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code> next to the demo.</span>
        </div>
      </form>`;
    return `
      <h1>Give revenue teams a sales call memory that survives every hand-off</h1>
      <p class="lead">Three calls and two documents about one account go into MemoryLake. It extracts the durable facts on its own,
      the reps pin a few notes, and the CSM taking over the pilot gets a brief in seconds by <em>searching the account memory</em>.
      This page drives the <code>memorylake</code> CLI for real — watch the terminal at the bottom.</p>
      <div class="connect">
        ${form}
        <div class="card">
          <h2 style="margin-top:0">Before you start</h2>
          <ol class="steps-list">
            <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one and copy it. A free personal account is enough.</li>
            <li><b>The CLI is already here</b> — this server found it on PATH, otherwise it would not have started.</li>
            <li><b>Paste the key and connect.</b> Then run the demo; re-running is safe, and <b>Clean up</b> removes everything it created.</li>
          </ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/sales-call-memory-for-revenue-teams" target="_blank" rel="noopener">memorylake.ai › sales call memory</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p>
        </div>
      </div>`;
  }

  function personCard(key) {
    const p = S.data.people[key]; const a = S.actors[key];
    return `<div class="card person"><span class="avatar ${side(key)}">${initials(p.display)}</span>
      <div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.role)}</div></div>
      <span class="status pill ${a ? "ok" : ""}">${a ? "actor ready" : "pending"}</span></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const pr = S.data.project;
    return `
      <h1>The people on the deal, and one project per account</h1>
      <p class="lead">An <b>actor</b> is who a memory is attributed to; actors are account-wide and get bound to the workspace.
      A <b>project</b> holds everything about one account — its conversations, documents and facts — so <code>search --projects</code> is the account memory.</p>
      <h2>Northwind — us</h2>
      <div class="grid">${["sdr-jordan", "ae-sam"].map(personCard).join("")}</div>
      <h2>Acme Corp — the prospect</h2>
      <div class="grid">${["dana-li", "priya-raman", "mark-chen"].map(personCard).join("")}</div>
      <h2>Account project</h2>
      <div class="card row spread"><div><b>${esc(pr.name)}</b><div class="muted small">custom id <code>${esc(pr.custom_id)}</code>${S.project ? ` · <span class="mono">${esc(S.project.id)}</span>` : ""}</div></div>
        <span class="pill ${S.project ? "ok" : ""}">${S.project ? (S.project.fresh ? "created" : "exists") : "pending"}</span></div>`;
  }

  function calls() {
    if (!S.data) return "";
    return `
      <h1>Each call is a conversation; MemoryLake extracts the facts</h1>
      <p class="lead">Every transcript line is appended as a message from its speaker, time-stamped to when it was said —
      that is where the <i>(as of …)</i> on the facts comes from. After each call the rep pins two CRM notes with <code>fact add</code>.
      Messages store instantly; the facts are extracted in the background, so the page waits for <code>cook-status</code>.</p>
      ${S.data.calls.map((c) => {
        const st = S.calls[c.custom_id] || {};
        const stored = st.stored || 0; const total = c.turns.length;
        const cook = st.cooked ? `<span class="pill ok">memory ready</span>`
          : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
          : stored ? `<span class="pill accent">${stored}/${total} turns stored</span>` : `<span class="pill">queued</span>`;
        return `<div class="card">
          <div class="call-head"><h3>${esc(c.name)}</h3><span class="muted small">${fmtDate(c.date)}</span>
            <span class="row" style="gap:4px">${c.participants.map((k) => `<span class="avatar ${side(k)}" title="${esc(S.data.people[k].display)}" style="width:22px;height:22px;font-size:10px">${initials(S.data.people[k].display)}</span>`).join("")}</span>
            <span style="margin-left:auto">${cook}</span></div>
          <div class="turns">${c.turns.map(([k, text], i) => `
            <div class="turn ${i < stored ? "stored" : ""}"><span class="avatar ${side(k)}">${initials(S.data.people[k].display)}</span>
              <div><div class="who">${esc(S.data.people[k].display)} · ${esc(S.data.people[k].role)}</div><div class="what">${esc(text)}</div></div>
              <span class="mark">${i < stored ? "✓ stored" : "turn " + (i + 1)}</span></div>`).join("")}</div>
          ${c.rep_notes?.length ? `<div class="notes">${c.rep_notes.map((n) => `<span class="note-chip ${st.pinned ? "" : "pending"}" title="${st.pinned ? "pinned with fact add" : "will be pinned after the call"}">📌 ${esc(n)}</span>`).join("")}</div>` : ""}
        </div>`;
      }).join("")}`;
  }

  function notes() {
    if (!S.data) return "";
    return `
      <h1>Hand-off doc and email thread become searchable documents</h1>
      <p class="lead">Files go into the Library with <code>lib upload</code>, then <code>proj doc import --wait</code> parses and indexes them inside the account project.
      Search returns documents and facts as two separate sets.</p>
      ${S.data.notes.map((n) => { const st = S.notes[n.name] || {}; return `<div class="card doc">
        <div class="row spread"><b class="mono">${esc(n.name)}</b>
          <span class="pill ${st.status === "imported" ? "ok" : st.status === "uploaded" ? "busy" : ""}">${st.status === "imported" ? "imported & indexed" : st.status === "uploaded" ? "uploaded, importing…" : "pending"}</span></div>
        <pre>${esc(n.text)}</pre></div>`; }).join("")}`;
  }

  function memory() {
    if (!S.facts) return `<h1>What did MemoryLake remember?</h1><div class="empty">Run the demo first — the facts show up here once the calls are processed.</div>`;
    const pinned = S.facts.filter((f) => f.pinned).length;
    const groups = {};
    S.facts.forEach((f) => { (groups[f.date || "undated"] ||= []).push(f); });
    const dates = Object.keys(groups).sort();
    return `
      <h1>What did MemoryLake remember?</h1>
      <p class="lead">Nobody tagged anything. The dated facts were extracted from the transcripts; the pinned ones are the reps' notes, stored verbatim.</p>
      <div class="counts"><div class="count"><b>${S.facts.length}</b><span>facts in the account memory</span></div>
        <div class="count"><b>${S.facts.length - pinned}</b><span>extracted from calls</span></div>
        <div class="count"><b>${pinned}</b><span>pinned by reps</span></div>
        <div class="count" style="margin-left:auto"><button class="btn sm" data-act="refresh-facts">Refresh</button></div></div>
      <div class="timeline">${dates.map((d) => `<div class="tl-date">${d === "undated" ? "Pinned notes (no date)" : fmtDate(d)}</div>
        ${groups[d].map((f) => `<div class="fact ${f.pinned ? "pinned" : ""} ${f.expired ? "expired" : ""}"><span>${esc(f.fact.replace(/\s*\(as of [0-9-]+\)\s*$/, ""))}</span><span class="tag">${f.pinned ? "pinned" : "extracted"}</span></div>`).join("")}`).join("")}</div>`;
  }

  function brief() {
    const secs = S.brief?.sections || S.briefSections;
    if (!secs.length) return `<h1>The hand-off brief</h1><div class="empty">Run the demo first — the brief is generated from six searches once the memory is ready.</div>`;
    return `
      <div class="brief-head"><div><h1>Acme Corp — pre-kickoff brief for the CSM</h1>
        <p class="lead" style="margin:0">Every bullet is a retrieved memory, not a summary. ${S.brief ? "" : '<span class="pill busy"><span class="spinner"></span> generating…</span>'}</p></div>
        ${S.brief ? `<button class="btn" data-act="download">Download .md</button>` : ""}</div>
      <div class="card brief">${secs.map((s) => `<h3>${esc(s.heading)}</h3>
        ${s.facts.length ? `<ul>${s.facts.map((f) => `<li>${esc(f.fact)}</li>`).join("")}</ul>` : '<p class="muted">No facts matched.</p>'}
        ${s.documents.length ? `<div class="sources">Source documents: ${[...new Set(s.documents.map((d) => d.name))].map(esc).join(", ")}</div>` : ""}
        <div class="faint small mono" style="margin-top:4px">search "${esc(s.query)}"</div>`).join("")}</div>`;
  }

  function ask() {
    const sugg = [...(S.data?.brief_questions || []).map((q) => q.query),
      "what is the deal timeline", "what did Mark Chen ask for", "any open action items", "what does Priya need before the pilot"];
    return `
      <h1>Ask the account memory</h1>
      <p class="lead">The same <code>memorylake search</code> the brief is built from, scoped to the Acme Corp project. Try your own question.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. who signs off on spend above 20k?" value="${esc(S.ask.q)}" ${S.project ? "" : "disabled"}><button class="btn primary" ${S.project ? "" : "disabled"}>Search</button></form>
        <div class="chips">${sugg.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${S.project ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  // ------------------------------------------------------------------ stage behaviour
  function bindStage() {
    const stage = $("#stage");
    stage.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); }
      else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    stage.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    stage.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    stage.querySelector('[data-act="refresh-facts"]')?.addEventListener("click", async () => { const r = await api("/api/facts", {}); if (r) { S.facts = r.facts; renderStage(); } });
    stage.querySelector('[data-act="download"]')?.addEventListener("click", () => {
      const blob = new Blob([S.brief.markdown], { type: "text/markdown" }); const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "acme-handoff-brief.md"; a.click(); URL.revokeObjectURL(a.href);
    });
    const form = stage.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      stage.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }

  async function askQuery(q) {
    if (!q) return;
    S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, top_k: 6 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `
      ${r.facts.length ? `<h2 style="margin-top:8px">Facts</h2>${r.facts.map((f) => `<div class="fact"><span>${esc(f.fact)}</span><span class="tag" title="score">${(f.score ?? 0).toFixed(2)}</span></div>`).join("")}` : '<p class="muted">No facts matched.</p>'}
      ${r.documents.length ? `<h2>Documents</h2>${r.documents.map((d) => `<div class="docres"><b class="mono">${esc(d.name)}</b><span class="muted small">${esc(d.summary || "")}</span></div>`).join("")}` : ""}`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }

  // ------------------------------------------------------------------ api + top bar
  async function api(path, body) {
    try {
      const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const j = await res.json();
      if (!res.ok || j.error) { banner(j.error || `HTTP ${res.status}`); return null; }
      return j;
    } catch (e) { banner(String(e)); return null; }
  }

  function banner(msg, ok) {
    const b = $("#banner"); if (!msg) { b.hidden = true; return; }
    b.hidden = false; b.className = "banner" + (ok ? " ok" : ""); b.textContent = msg;
  }

  function setConn() {
    const st = S.st; const c = $("#conn");
    c.className = "conn " + (st.status === "running" ? "busy" : st.connected ? "on" : "");
    $("#conn-text").textContent = st.status === "running" ? `running ${st.job}…` : st.connected ? `${st.team?.name || "connected"} · ${st.workspace?.name || st.workspace?.id || ""}` : "Not connected";
    const busy = st.status === "running";
    $("#btn-run").disabled = !st.connected || busy;
    $("#btn-reset").disabled = !st.connected || busy;
    $("#btn-cleanup").disabled = !st.connected || busy;
  }

  async function runDemo(reset) {
    banner("");
    if (reset || !S.project) { S.calls = {}; S.notes = {}; S.facts = null; S.brief = null; S.briefSections = []; S.stepStatus = { 1: "done" }; }
    S.follow = true;
    const r = await api("/api/run", { reset });
    if (r) { S.st = r; setConn(); }
  }

  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo project, its conversations and documents, the demo actors and the uploaded notes from your account?")) return;
    banner("");
    const r = await api("/api/cleanup", {});
    if (r) { S.st = r; setConn(); }
  });

  // ------------------------------------------------------------------ terminal
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

  // ------------------------------------------------------------------ event stream
  function stepOf(index) { return STEPS.find((s) => s.n === index); }

  function onEvent(ev) {
    S.lastEvent = ev.id;
    const k = ev.kind, d = ev.data || {};
    if (k === "step") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.index) S.stepStatus[d.index] = "running";
      if (d.index === 2) { S.calls = {}; S.notes = {}; S.facts = null; S.brief = null; S.briefSections = []; }
      term("step", ev.text);
      if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    } else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "actor") S.actors[d.key] = d;
    else if (k === "project") S.project = { id: d.id, name: d.name, fresh: !!d.fresh };
    else if (k === "call") S.calls[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false, pinned: (d.done || 0) > 0 };
    else if (k === "turn") (S.calls[d.custom_id] ||= {}).stored = d.index;
    else if (k === "pinned") (S.calls[d.custom_id] ||= {}).pinned = true;
    else if (k === "cooked") Object.values(S.calls).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "uploaded") S.notes[d.name] = { status: "uploaded" };
    else if (k === "imported") Object.keys(S.notes).forEach((n) => (S.notes[n].status = "imported"));
    else if (k === "facts") S.facts = d.facts;
    else if (k === "brief_section") { if (!S.brief) S.briefSections.push(d); }
    else if (k === "brief") { S.brief = d; S.briefSections = d.sections; }
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.project = null; S.actors = {}; S.calls = {}; S.notes = {}; S.facts = null; S.brief = null; S.briefSections = []; S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. The brief is ready, and you can ask the memory anything.", true);
      term("dim", `── ${d.job} finished`);
      S.st.status = "done"; setConn();
      if (S.caughtUp) refreshState();
    } else if (k === "error") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "error"; });
      term("error", ev.text); if (S.caughtUp) banner(ev.text);
      S.st.status = "error"; setConn(); refreshState();
    }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }

  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }

  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "call", "turn", "pinned", "cooked", "uploaded", "imported", "facts", "brief_section", "brief", "done", "error", "text"]
      .forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  // ------------------------------------------------------------------ boot
  (async () => {
    renderSteps();
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data; if (state.project) S.project = state.project;
    const target = state.last_event_id;
    setConn();
    // Replay the whole event log silently, then render once.
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const handler = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "call", "turn", "pinned", "cooked", "uploaded", "imported", "facts", "brief_section", "brief", "done", "error", "text"]
        .forEach((kind) => es.addEventListener(kind, handler));
      es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : state.has_brief ? "brief" : state.connected ? "connect" : "connect");
    subscribe();
  })();
})();

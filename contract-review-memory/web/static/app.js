/* Contract review that remembers your positions — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const side = (key) => key === "assistant" ? "pm" : "analyst";
  const kindOf = (name) => name.endsWith(".pdf") ? "PDF" : name.endsWith(".md") ? "Markdown" : "File";
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
  // "Harbrook standard" / "Northwind Freight precedent" / "review sessions" → a coloured owner tag
  const ownerKey = (o) => o.startsWith("Harbrook") ? "standard" : o.startsWith("Northwind") ? "northwind-freight" : o.startsWith("Halden") ? "halden-analytics" : "reviews";
  const ownerTag = (o) => `<span class="owner o-${ownerKey(o)}">${esc(o)}</span>`;
  const cpTag = (k) => `<span class="owner o-${esc(k)}">${esc(S.data?.counterparties[k]?.display || k)}</span>`;

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "People & scopes", sub: "actors, counterparties, projects", view: "setup" },
    { n: 3, title: "Playbook & redlines", sub: "Library + recursive import", view: "library" },
    { n: 4, title: "Standard positions", sub: "pinned to the playbook", view: "positions" },
    { n: 5, title: "2025 review sessions", sub: "redline attached, precedents pinned", view: "sessions" },
    { n: 6, title: "Renewal context", sub: "playbook + one counterparty", view: "context" },
    { n: 7, title: "Replay & diff", sub: "last year's session, its redline", view: "replay" },
    { n: 8, title: "Renewal brief", sub: "per changed clause", view: "brief" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "uploaded", "tree", "imported",
    "documents", "positions", "session", "turn", "precedents", "cooked", "memory", "context", "isolation", "replay", "diff",
    "brief", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, projects: {},
    uploaded: {},              // "playbook/…" -> true
    imports: {},               // project key -> {expanded, success_count, duplicate_count, failure_count}
    documents: null,           // project key -> [{name, status}]
    positions: false,
    sessions: {},              // custom_id -> {id, stored, cooked, pinned}
    memory: null,              // [{title, facts:[{fact, pinned}]}]
    context: [], isolation: null, replay: null, diff: null, brief: null,
    ask: { q: "", cp: "northwind-freight", html: "" },
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.uploaded = {}; S.imports = {}; S.documents = null; S.positions = false; S.sessions = {}; S.memory = null;
    S.context = []; S.isolation = null; S.replay = null; S.diff = null; S.brief = null;
  }

  // ------------------------------------------------------------------ rendering: steps
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `
      <button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span>
        <span class="t">${s.title}<small>${s.sub}</small></span>
      </button>`).join("") + `
      <button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask">
        <span class="n">?</span><span class="t">Ask the memory<small>playbook + a counterparty</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  function renderStage() {
    const r = { connect, setup, library, positions, sessions, context, replay, brief, ask }[S.view];
    $("#stage").innerHTML = r ? r() : "";
    bindStage();
  }

  const hasProjects = () => Object.keys(S.projects).length > 0;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card">
        <div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px">
          <dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Projects</dt><dd>${hasProjects() ? Object.values(S.projects).map((p) => `${esc(p.name)} <span class="mono muted">${esc(p.id)}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd>
        </dl>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${hasProjects() ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>mlu-crm-</code></span>
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
      <h1>AI-assisted contract review that remembers your positions</h1>
      <p class="lead">Harbrook Logistics' legal team keeps its standard positions in a playbook, and every deal leaves precedents behind: last year Northwind Freight got an 18-month liability cap,
      Halden Analytics a 24-month data-breach super-cap. Now Northwind's renewal redline lands on a new reviewer's desk. One search loads our positions plus <i>Northwind's</i> precedents — nobody else's —
      and last year's review session is replayed, its attached redline fetched back and diffed against the new one. This page drives the <code>memorylake</code> CLI for real — watch the terminal at the bottom.</p>
      <div class="connect">
        ${form}
        <div class="card">
          <h2 style="margin-top:0">Before you start</h2>
          <ol class="steps-list">
            <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one and copy it. A free personal account is enough.</li>
            <li><b>The CLI is already here</b> — this server found it on PATH, otherwise it would not have started.</li>
            <li><b>Paste the key and connect.</b> Then run the demo; re-running is safe, and <b>Clean up</b> removes everything it created.</li>
          </ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-contract-review-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for contract review teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p>
        </div>
      </div>`;
  }

  function personCard(p, a, tag) {
    return `<div class="card person"><span class="avatar ${p.key === "assistant" ? "pm" : "analyst"}">${initials(p.display)}</span>
      <div><div class="name">${esc(p.display)} ${tag || ""}</div><div class="role">${esc(p.role)}</div></div>
      <span class="status pill ${a ? "ok" : ""}">${a ? "actor ready" : "pending"}</span></div>`;
  }

  function setup() {
    if (!S.data) return "";
    return `
      <h1>A reviewer, her AI assistant, two counterparties, two projects</h1>
      <p class="lead">Memory needs <b>scopes</b>. The playbook is a <b>project</b> Legal curates; each counterparty is an <b>actor</b> that holds the exceptions accepted from it;
      the review sessions live in a second project. Step 6 searches <code>--projects &lt;playbook&gt; --actors &lt;counterparty&gt;</code> — the union of exactly those two scopes.</p>
      <h2>Review session participants</h2>
      <div class="grid">${Object.values(S.data.people).map((p) => personCard(p, S.actors[p.key], p.type === "ASSISTANT" ? '<span class="pill">ASSISTANT</span>' : "")).join("")}</div>
      <h2>Counterparties → actors</h2>
      <div class="grid">${Object.values(S.data.counterparties).map((p) => personCard(p, S.actors[p.key], cpTag(p.key))).join("")}</div>
      <h2>Projects</h2>
      ${Object.values(S.data.projects).map((r) => `<div class="card row spread"><div><b>${esc(r.name)}</b>
        <div class="muted small">${esc(r.description)} · custom id <code>${esc(r.custom_id)}</code>${S.projects[r.key] ? ` · <span class="mono">${esc(S.projects[r.key].id)}</span>` : ""}</div></div>
        <span class="pill ${S.projects[r.key] ? "ok" : ""}">${S.projects[r.key] ? "ready" : "pending"}</span></div>`).join("")}`;
  }

  function library() {
    if (!S.data) return "";
    const dirs = {};
    S.data.files.forEach((f) => { const d = f.path.split("/").slice(0, -1).join("/"); (dirs[d] ||= []).push(f); });
    const target = { playbook: "playbook", redlines: "reviews" };
    return `
      <h1>The playbook PDF and last year's redlines</h1>
      <p class="lead">The files are mirrored into the Library under <code>${esc(S.data.library_root)}/</code>. Then one <code>proj doc import &lt;folder&gt; --recursive --wait</code> per project:
      the playbook PDF into the playbook project, both redlines into the reviews project — that is where step 7 fetches last year's redline back from.</p>
      <div class="card tree"><div class="mono"><b>${esc(S.data.library_root)}/</b></div>
      ${Object.entries(dirs).map(([dir, files]) => `<div class="tree-dir mono">${esc(dir)}/</div>
        ${files.map((f) => { const up = S.uploaded[f.path]; return `<details class="tree-file"><summary>
          <span class="kind k-${kindOf(f.path).toLowerCase()}">${kindOf(f.path)}</span>
          <a class="mono" href="/library/${esc(f.path)}" target="_blank" rel="noopener">${esc(f.path.split("/").pop())}</a>
          <span class="faint small">${kb(f.size)}</span><span class="pill ${up ? "ok" : ""}" style="margin-left:auto">${up ? "uploaded" : "pending"}</span></summary>
          ${f.text ? `<pre>${esc(f.text)}</pre>` : `<p class="muted small">PDF — MemoryLake parses it on import.</p>`}</details>`; }).join("")}`).join("")}
      </div>
      <h2>Imports</h2>
      ${Object.entries(target).map(([folder, key]) => {
        const imp = S.imports[key]; const docs = S.documents?.[key] || [];
        return `<div class="card"><div class="row spread"><div><b>${esc(S.data.projects[key].name)}</b> <span class="faint small mono">import ${esc(folder)}/ --recursive --wait</span></div>
          ${imp ? `<span class="pill ok">${imp.success_count} new · ${imp.duplicate_count} already there · ${imp.failure_count} failed</span>`
            : S.stepStatus[3] === "running" ? `<span class="pill busy"><span class="spinner"></span> parsing…</span>` : `<span class="pill">pending</span>`}</div>
          ${imp?.expanded ? `<div class="expanded mono">${esc(imp.expanded)}</div>` : ""}
          ${docs.length ? `<ul class="doclist">${docs.map((d) => `<li><span class="kind k-${kindOf(d.name).toLowerCase()}">${kindOf(d.name)}</span> <span class="mono">${esc(d.name)}</span> <span class="pill ${d.status === "okay" ? "ok" : ""}">${esc(d.status)}</span></li>`).join("")}</ul>` : ""}
        </div>`;
      }).join("")}`;
  }

  function positions() {
    if (!S.data) return "";
    return `
      <h1>Pin the playbook's standard positions</h1>
      <p class="lead">Each position becomes one fact in the playbook project with <code>fact add --project</code> — stored verbatim and searchable immediately.
      Nothing else writes to this project, so the text you pin is the text you get back.</p>
      <div class="card">${S.data.positions.map((p) => `<div class="fact ${S.positions ? "pinned" : ""}"><span>${esc(p)}</span><span class="tag">${S.positions ? "pinned" : "pending"}</span></div>`).join("")}</div>
      <p class="muted small">Source: <a href="/library/playbook/harbrook-contract-playbook-2026.pdf" target="_blank" rel="noopener">harbrook-contract-playbook-2026.pdf</a></p>`;
  }

  function sessionCard(c) {
    const st = S.sessions[c.custom_id] || {};
    const stored = st.stored || 0; const total = c.turns.length;
    const cook = st.cooked || (st.pinned && stored >= total) ? `<span class="pill ok">memory ready</span>`
      : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
      : stored ? `<span class="pill accent">${stored}/${total} messages stored</span>` : `<span class="pill">queued</span>`;
    const attach = c.attachment.split("/").pop();
    return `<div class="card">
      <div class="call-head">${cpTag(c.counterparty)}<h3>${esc(c.name)}</h3><span class="muted small">${fmtDate(c.date)}</span>
        <span style="margin-left:auto">${cook}</span></div>
      <div class="meta"><span class="pill">kind=contract-review</span><span class="pill">counterparty=${esc(c.counterparty)}</span><span class="pill">contract=${esc(c.contract)}</span></div>
      <div class="turns">${c.turns.map(([k, text], i) => `
        <div class="turn ${i < stored ? "stored" : ""}"><span class="avatar ${side(k)}">${initials(S.data.people[k].display)}</span>
          <div><div class="who">${esc(S.data.people[k].display)} · ${esc(S.data.people[k].role)}</div><div class="what">${esc(text)}</div>
          ${i === 0 ? `<div class="attach">📎 FILE block <span class="mono">${esc(attach)}</span></div>` : ""}</div>
          <span class="mark">${i < stored ? "✓ stored" : "msg " + (i + 1)}</span></div>`).join("")}</div>
      <div class="notes">${c.precedents.map((n) => `<span class="note-chip ${st.pinned ? "" : "pending"}">📌 ${esc(n)}</span>`).join("")}</div>
      <div class="muted small" style="margin-top:6px">↑ pinned to the ${esc(S.data.counterparties[c.counterparty].display)} actor with <code>fact add --actor</code></div>
    </div>`;
  }

  function sessions() {
    if (!S.data) return "";
    return `
      <h1>Last year's review sessions become memory</h1>
      <p class="lead">Each review is a conversation in the reviews project, time-stamped to the day it happened and tagged with <code>--metadata counterparty=… contract=…</code>.
      The redline under review rides along in the first message as a <b>FILE block</b> (<code>--content-json</code>) pointing at the Library item. When MemoryLake has extracted the facts,
      the exceptions Elena accepted are pinned to the counterparty's actor.</p>
      ${S.data.sessions.map(sessionCard).join("")}
      <h2>What each scope remembers</h2>
      ${S.memory ? `<div class="row" style="margin-bottom:8px"><button class="btn sm" data-act="refresh-memory">Refresh</button></div>
        <div class="cols">${S.memory.map((sc) => `<div class="col"><div class="row spread"><b>${esc(sc.title)}</b><span class="muted small">${sc.facts.length} facts</span></div>
        ${sc.facts.map((f) => `<div class="fact ${f.pinned ? "pinned" : ""}"><span>${esc(f.fact)}</span><span class="tag">${f.pinned ? "pinned" : "extracted"}</span></div>`).join("")}</div>`).join("")}</div>`
        : `<div class="empty">Shows up once both sessions are processed.</div>`}`;
  }

  function hitList(c) {
    return `${c.hits.length ? c.hits.map((h) => `<div class="hit">${ownerTag(h.owner)}<span>${esc(h.fact)}</span></div>`).join("") : '<p class="muted">Nothing matched.</p>'}`;
  }

  function context() {
    const ctx = S.context.filter((c) => c.clause !== "Q:");
    if (!ctx.length) return `<h1>Renewal context</h1><div class="empty">Run the demo first — the searches run once the sessions are processed.</div>`;
    return `
      <h1>Northwind's renewal: our positions + Northwind's precedents, nobody else's</h1>
      <p class="lead">One <code>memorylake search</code> per clause, scoped <code>--projects &lt;playbook&gt; --actors &lt;Northwind Freight&gt;</code>. The two scopes are combined (a union):
      the standard position and the exception Northwind got last time come back together. Search results carry no owner, so the demo maps every fact id back to its scope; hits not about the clause are left out.</p>
      ${ctx.map((c) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(c.clause)} ${esc(c.title)}</h3>${cpTag(c.counterparty)}</div>
        ${hitList(c)}</div>`).join("")}
      ${S.isolation ? `<div class="isolation ${S.isolation.leaked ? "bad" : "ok"}">${esc(S.isolation.verdict)}</div>` : ""}`;
  }

  function replay() {
    const r = S.replay, d = S.diff;
    if (!r) return `<h1>Replay last year's review</h1><div class="empty">Run the demo first.</div>`;
    const who = (name) => name.startsWith("Harbrook") ? "pm" : "analyst";
    return `
      <h1>Reopen last year's Northwind review</h1>
      <p class="lead"><code>conv list</code> has no server-side filter, so the demo picks the session by its metadata, then <code>conv msg list</code> replays it message by message —
      with the timestamps from 2025 and the FILE block exactly as it was sent. The block's URI names the Library item; <code>lib get</code> gives its file name and
      <code>proj doc download</code> brings the original bytes back.</p>
      <div class="card replay"><div class="call-head"><h3>${esc(r.name)}</h3><span class="mono faint">${esc(r.id)}</span></div>
        <div class="meta">${Object.entries(r.metadata || {}).map(([k, v]) => `<span class="pill">${esc(k)}=${esc(v)}</span>`).join("")}</div>
        <div class="turns">${r.messages.map((m) => m.file ? `<div class="turn stored"><span></span><div class="attach">📎 ${esc(m.mime)} <span class="mono">${esc(m.file)}</span></div></div>`
          : `<div class="turn stored"><span class="avatar ${who(m.who)}">${initials(m.who)}</span><div><div class="who">${esc(m.who)} · <span class="when">${esc(m.when)}</span></div><div class="what">${esc(m.text)}</div></div></div>`).join("")}</div></div>
      <h2>${d ? `${esc(d.old)} → ${esc(d.new)}: ${d.changes.length} of ${d.total} clauses changed` : "Diff"}</h2>
      ${d ? `<div class="diff">${d.changes.map((c) => `<div class="clause"><div class="num">${esc(c.clause)}</div><div class="old">${esc(c.old || "(absent)")}</div><div class="new">${esc(c.new || "(removed)")}</div></div>`).join("")}</div>`
        : `<span class="pill busy"><span class="spinner"></span> fetching the attachment…</span>`}`;
  }

  function brief() {
    const b = S.brief, d = S.diff;
    if (!b || !d) return `<h1>Renewal brief</h1><div class="empty">Run the demo first — the brief is written at the end.</div>`;
    const ctx = Object.fromEntries(S.context.filter((c) => c.counterparty === "northwind-freight").map((c) => [c.clause, c]));
    return `
      <div class="brief-head"><div><h1>Renewal brief — Northwind Freight</h1>
        <p class="lead" style="margin:0">Every clause that changed since the text Northwind signed in 2025, with the position and the precedent behind it. A clause with a precedent goes to the General Counsel with the precedent attached (playbook §6).</p></div>
        <button class="btn" data-act="download">Download brief .md</button></div>
      ${d.changes.map((ch) => { const s = b.summary.find((x) => x.clause === ch.clause) || {}; const c = ctx[ch.clause];
        return `<div class="card"><h3 style="margin-top:0">${esc(ch.clause)} — changed</h3>
          <div class="diff"><div class="clause"><div class="old">${esc(ch.old || "(absent)")}</div><div class="new">${esc(ch.new || "(removed)")}</div></div></div>
          ${c ? hitList(c) : ""}
          <div class="action ${s.escalate ? "escalate" : "check"}">${s.escalate ? "Escalate — departs from the 2025 precedent with Northwind" : "No precedent with Northwind — check against the standard position"}</div></div>`; }).join("")}`;
  }

  function ask() {
    const cps = S.data ? Object.values(S.data.counterparties) : [];
    const sugg = ["liability cap", "payment terms", "who owns the reports", "data breach claims", "indemnification"];
    const ready = hasProjects();
    return `
      <h1>Ask the contract memory</h1>
      <p class="lead">The same scoped search as step 6: our playbook plus the counterparty you pick. Switch the counterparty and the other one's precedents are simply not there.</p>
      <div class="card"><form class="ask-form" id="ask-form">
          <select id="ask-cp">${cps.map((c) => `<option value="${esc(c.key)}" ${S.ask.cp === c.key ? "selected" : ""}>${esc(c.display)}</option>`).join("")}</select>
          <input id="ask-q" placeholder="e.g. liability cap" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${sugg.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    stage.querySelector('[data-act="refresh-memory"]')?.addEventListener("click", async () => { const r = await api("/api/memory", {}); if (r) { S.memory = r.scopes; renderStage(); } });
    stage.querySelector('[data-act="download"]')?.addEventListener("click", () => {
      const blob = new Blob([S.brief.markdown], { type: "text/markdown" }); const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "renewal-brief.md"; a.click(); URL.revokeObjectURL(a.href);
    });
    stage.querySelector("#ask-cp")?.addEventListener("change", (e) => { S.ask.cp = e.target.value; });
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
    const r = await api("/api/search", { query: q, counterparty: S.ask.cp, top_k: 4 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="faint small" style="margin:8px 0">searched: <span class="owner o-standard">Harbrook playbook</span> + ${cpTag(r.counterparty)}</div>` + hitList(r);
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
    if (reset || !hasProjects()) { resetRun(); S.stepStatus = { 1: "done" }; }
    S.follow = true;
    const r = await api("/api/run", { reset });
    if (r) { S.st = r; setConn(); }
  }

  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete both demo projects, the review sessions, the demo actors (with the counterparties' precedents) and the Library folder from your account?")) return;
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
      if (d.index === 2) resetRun();
      term("step", ev.text);
      if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    } else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "actor") S.actors[d.key] = d;
    else if (k === "project") S.projects[d.key] = { id: d.id, name: d.name };
    else if (k === "uploaded") S.uploaded[d.path] = true;
    else if (k === "tree") (d.files || []).forEach((f) => (S.uploaded[f] = true));
    else if (k === "imported") S.imports[d.project] = d;
    else if (k === "documents") S.documents = d.projects;
    else if (k === "positions") S.positions = true;
    else if (k === "session") S.sessions[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false, pinned: false };
    else if (k === "turn") (S.sessions[d.custom_id] ||= {}).stored = d.index;
    else if (k === "precedents") Object.values(S.data.sessions).forEach((c) => { if (c.counterparty === d.counterparty) (S.sessions[c.custom_id] ||= {}).pinned = true; });
    else if (k === "cooked") Object.values(S.sessions).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "memory") S.memory = d.scopes;
    else if (k === "context") { if (d.clause !== "Q:") { if (S.context.some((c) => c.clause === d.clause && c.counterparty === d.counterparty)) S.context = []; S.context.push(d); } }
    else if (k === "isolation") S.isolation = d;
    else if (k === "replay") { S.replay = d; S.diff = null; }
    else if (k === "diff") S.diff = d;
    else if (k === "brief") S.brief = d;
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.projects = {}; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. The renewal brief is ready, and you can ask the memory anything.", true);
      term("dim", `── ${d.job} finished`);
      S.st.status = "done"; setConn();
      if (S.caughtUp) refreshState();
    } else if (k === "error") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "error"; });
      term("error", ev.text); if (S.caughtUp) banner(ev.text);
      S.st.status = "error"; setConn(); refreshState();
    }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "uploaded", "turn"].includes(k)) { renderSteps(); renderStage(); }
    else if (S.caughtUp && (k === "uploaded" || k === "turn") && S.view !== "ask") renderStage();
  }

  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }

  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    ALL_KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  // ------------------------------------------------------------------ boot
  (async () => {
    renderSteps();
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data; if (state.projects) S.projects = { ...state.projects };
    const target = state.last_event_id;
    setConn();
    // Replay the whole event log silently, then render once.
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const handler = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      ALL_KINDS.forEach((kind) => es.addEventListener(kind, handler));
      es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : state.has_brief ? "brief" : "connect");
    subscribe();
  })();
})();

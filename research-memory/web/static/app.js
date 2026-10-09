/* Research memory for analysts — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const side = (key) => key === "mira-holt" ? "analyst" : "pm";
  const kindOf = (name) => name.endsWith(".pdf") ? "PDF" : name.endsWith(".xlsx") ? "Excel" : name.endsWith(".md") ? "Notes" : "File";
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
  // Highlight the two cycle-life numbers wherever they appear.
  const hl = (s) => esc(s).replace(/\b(4,?200|2,?900)\b/g, "<mark>$1</mark>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "People & project", sub: "one project per thesis", view: "setup" },
    { n: 3, title: "Q3 reading", sub: "PDF, Excel, notes", view: "sources" },
    { n: 4, title: "Model reviews", sub: "July sets, September revises", view: "reviews" },
    { n: 5, title: "Memory", sub: "what MemoryLake remembers", view: "memory" },
    { n: 6, title: "Q4 questions", sub: "answers from last quarter", view: "answers" },
    { n: 7, title: "Back to source", sub: "download the original", view: "source" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "uploaded", "imported", "documents",
    "review", "turn", "pinned", "cooked", "facts", "answer", "brief", "downloaded", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, project: null,
    files: {},                 // name -> {status}
    reviews: {},               // custom_id -> {id, stored, cooked, pinned}
    facts: null, revisionKind: "none",
    brief: null, answers: [],
    downloaded: null,
    ask: { q: "", html: "" },
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.files = {}; S.reviews = {}; S.facts = null; S.revisionKind = "none";
    S.brief = null; S.answers = []; S.downloaded = null;
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
        <span class="n">?</span><span class="t">Ask the memory<small>free-form search</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  // ------------------------------------------------------------------ rendering: stage
  function renderStage() {
    const stage = $("#stage");
    const r = { connect, setup, sources, reviews, memory, answers, source, ask }[S.view];
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
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>mlu-rma-</code></span>
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
      <h1>Give analysts a research memory that compounds quarter over quarter</h1>
      <p class="lead">A quarter of research on one question — two PDFs that disagree, a policy brief, an Excel cost model, reading notes and two model reviews —
      goes into one MemoryLake project. Next quarter, each new question starts from <em>what was already learned</em>: the right file, the right sheet,
      and last quarter's reasoning, including the number that was revised and why.
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
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/research-memory-for-analysts" target="_blank" rel="noopener">memorylake.ai › research memory for analysts</a>
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
      <h1>The analyst, the PM, and one project for the thesis</h1>
      <p class="lead">An <b>actor</b> is who a memory is attributed to; actors are account-wide and get bound to the workspace.
      A <b>project</b> holds everything about one research question — its documents, its review meetings and the facts drawn from them —
      so <code>search --projects</code> is the research memory.</p>
      <h2>Larkspur Research</h2>
      <div class="grid">${["mira-holt", "theo-grant"].map(personCard).join("")}</div>
      <h2>Research project</h2>
      <div class="card row spread"><div><b>${esc(pr.name)}</b><div class="muted small">custom id <code>${esc(pr.custom_id)}</code>${S.project ? ` · <span class="mono">${esc(S.project.id)}</span>` : ""}</div></div>
        <span class="pill ${S.project ? "ok" : ""}">${S.project ? (S.project.fresh ? "created" : "exists") : "pending"}</span></div>`;
  }

  function sources() {
    if (!S.data) return "";
    const pill = (st) => st === "okay" ? `<span class="pill ok">parsed & indexed</span>`
      : st === "imported" ? `<span class="pill ok">imported</span>`
      : st === "uploaded" ? `<span class="pill busy"><span class="spinner"></span> uploaded, parsing…</span>` : `<span class="pill">pending</span>`;
    return `
      <h1>Q3 reading: PDFs, a spreadsheet and notes become documents</h1>
      <p class="lead">Each file goes into the Library with <code>lib upload</code>, then <code>proj doc import --wait</code> parses it inside the project —
      PDFs page by page, the workbook sheet by sheet. Note the two PDFs disagree on cycle life: the lab study says 4,200 cycles, the field report 2,900.</p>
      ${S.data.sources.map((f) => { const st = S.files[f.name]?.status; return `<div class="card doc">
        <div class="row spread"><div class="row" style="gap:10px"><span class="kind k-${kindOf(f.name).toLowerCase()}">${kindOf(f.name)}</span>
          <a class="mono" href="/sources/${encodeURIComponent(f.name)}" target="_blank" rel="noopener">${esc(f.name)}</a><span class="faint small">${kb(f.size)}</span></div>
          ${pill(st)}</div>
        ${f.text ? `<pre>${esc(f.text)}</pre>` : ""}</div>`; }).join("")}`;
  }

  function reviews() {
    if (!S.data) return "";
    return `
      <h1>Two model reviews: July sets a base case, September revises it</h1>
      <p class="lead">Each review is a conversation between Mira and Theo, time-stamped to the day it happened — that is where the <i>(as of …)</i> on the facts comes from.
      Mira's own takeaways are pinned verbatim with <code>fact add</code>. Messages store instantly; facts are extracted in the background, so the page waits for <code>cook-status</code>.</p>
      ${S.data.reviews.map((c) => {
        const st = S.reviews[c.custom_id] || {};
        const stored = st.stored || 0; const total = c.turns.length;
        const cook = st.cooked ? `<span class="pill ok">memory ready</span>`
          : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
          : stored ? `<span class="pill accent">${stored}/${total} turns stored</span>` : `<span class="pill">queued</span>`;
        return `<div class="card">
          <div class="call-head"><h3>${esc(c.name)}</h3><span class="muted small">${fmtDate(c.date)}</span>
            <span style="margin-left:auto">${cook}</span></div>
          <div class="turns">${c.turns.map(([k, text], i) => `
            <div class="turn ${i < stored ? "stored" : ""}"><span class="avatar ${side(k)}">${initials(S.data.people[k].display)}</span>
              <div><div class="who">${esc(S.data.people[k].display)} · ${esc(S.data.people[k].role)}</div><div class="what">${hl(text)}</div></div>
              <span class="mark">${i < stored ? "✓ stored" : "turn " + (i + 1)}</span></div>`).join("")}</div>
          ${c.analyst_notes?.length ? `<div class="notes">${c.analyst_notes.map((n) => `<span class="note-chip ${st.pinned ? "" : "pending"}" title="${st.pinned ? "pinned with fact add" : "pinned after the review"}">📌 ${hl(n)}</span>`).join("")}</div>` : ""}
        </div>`;
      }).join("")}`;
  }

  function memory() {
    if (!S.facts) return `<h1>What did MemoryLake remember?</h1><div class="empty">Run the demo first — the facts show up here once the reviews are processed.</div>`;
    const pinned = S.facts.filter((f) => f.pinned).length;
    const rev = S.facts.filter((f) => f.revision);
    const groups = {};
    S.facts.filter((f) => !f.revision).forEach((f) => { (groups[f.pinned ? "pinned" : (f.date || "undated")] ||= []).push(f); });
    const keys = Object.keys(groups).sort((a, b) => (a === "pinned") - (b === "pinned") || a.localeCompare(b));
    const revCard = rev.length ? `<div class="card revision">
        <div class="rev-head">${S.revisionKind === "merged"
          ? "September revised July's number without erasing it — one fact, both values, both dates"
          : "Both cycle-life figures are in memory, each dated to the review that set it"}</div>
        ${rev.map((f) => `<div class="rev-fact">${hl(f.fact)}</div>`).join("")}
        <div class="muted small" style="margin-top:8px">Nothing was averaged: the old value and the date it was set are still there when you ask “why did this change?”.</div></div>` : "";
    return `
      <h1>What did MemoryLake remember?</h1>
      <p class="lead">Nobody tagged anything. The dated facts were written by MemoryLake from the two reviews; the pinned ones are Mira's notes, stored verbatim.</p>
      <div class="counts"><div class="count"><b>${S.facts.length}</b><span>facts in the research memory</span></div>
        <div class="count"><b>${S.facts.length - pinned}</b><span>written from the reviews</span></div>
        <div class="count"><b>${pinned}</b><span>pinned by the analyst</span></div>
        <div class="count" style="margin-left:auto"><button class="btn sm" data-act="refresh-facts">Refresh</button></div></div>
      ${revCard}
      <div class="timeline">${keys.map((d) => `<div class="tl-date">${d === "pinned" ? "Pinned by Mira" : d === "undated" ? "Undated" : fmtDate(d)}</div>
        ${groups[d].map((f) => `<div class="fact ${f.pinned ? "pinned" : ""}"><span>${hl(f.fact.replace(/\s*\(as of [0-9-]+\)\.?\s*$/, ""))}</span><span class="tag">${f.pinned ? "pinned" : "extracted"}</span></div>`).join("")}`).join("")}</div>`;
  }

  function docRow(d) {
    const where = d.sheet ? `${d.kind} · sheet “${d.sheet}”` : d.kind;
    return `<div class="docres"><div class="row" style="gap:8px"><span class="kind k-${String(d.kind).toLowerCase()}">${esc(where)}</span><b class="mono">${esc(d.name)}</b></div>
      <span class="muted small">${esc((d.summary || "").replace(/^\S+\.\w+\s+/, ""))}</span></div>`;
  }

  function answers() {
    const list = S.brief?.answers || S.answers;
    if (!list.length) return `<h1>Q4: new questions</h1><div class="empty">Run the demo first — four searches run once the memory is ready.</div>`;
    return `
      <div class="brief-head"><div><h1>Q4: each question starts from last quarter</h1>
        <p class="lead" style="margin:0">One <code>memorylake search</code> per question returns two sets: facts (what was concluded, with dates) and documents (which file, which sheet).
        ${S.brief ? "" : '<span class="pill busy"><span class="spinner"></span> searching…</span>'}</p></div>
        ${S.brief ? `<button class="btn" data-act="download">Download brief .md</button>` : ""}</div>
      ${list.map((a) => `<div class="card answer"><h3>${esc(a.heading)}</h3>
        <div class="faint small mono">search "${esc(a.query)}"</div>
        ${a.facts.length ? `<ul>${a.facts.slice(0, 4).map((f) => `<li>${hl(f.fact)}</li>`).join("")}</ul>` : '<p class="muted">No facts matched.</p>'}
        ${a.documents.slice(0, 3).map(docRow).join("")}</div>`).join("")}`;
  }

  function source() {
    const d = S.downloaded;
    return `
      <h1>Back to the source</h1>
      <p class="lead">The summary says which file it was; sometimes you need the file itself. One document search, then
      <code>proj doc download</code> brings the original back — the same bytes that were uploaded in Q3.</p>
      ${d ? `<div class="card">
        <div class="faint small mono">search "${esc(S.data?.reopen_query || "")}" --types document --top-k 1</div>
        <div class="row spread" style="margin-top:10px"><div class="row" style="gap:10px"><span class="kind k-${kindOf(d.name).toLowerCase()}">${kindOf(d.name)}</span>
          <b class="mono">${esc(d.path)}</b><span class="faint small">${d.size.toLocaleString()} bytes</span></div>
          <span class="pill ${d.identical ? "ok" : ""}">${d.identical ? "byte-for-byte the uploaded file" : "downloaded"}</span></div>
        <div class="row" style="margin-top:14px"><a class="btn primary" href="/out/${encodeURIComponent(d.name)}" target="_blank" rel="noopener">Open ${esc(d.name)}</a></div>
      </div>` : `<div class="empty">Run the demo first — this is the last step.</div>`}`;
  }

  function ask() {
    const sugg = [...(S.data?.questions || []).map((q) => q.query),
      "how much do we discount vendor-funded studies", "what happens in cold climates", "what did we recommend for 2027", "what would change our view"];
    return `
      <h1>Ask the research memory</h1>
      <p class="lead">The same <code>memorylake search</code> the Q4 answers come from, scoped to the research project. Try your own question.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. why did we stop using the lab number?" value="${esc(S.ask.q)}" ${S.project ? "" : "disabled"}><button class="btn primary" ${S.project ? "" : "disabled"}>Search</button></form>
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
      a.href = URL.createObjectURL(blob); a.download = "q4-research-brief.md"; a.click(); URL.revokeObjectURL(a.href);
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
    const r = await api("/api/search", { query: q, top_k: 5 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `
      ${r.facts.length ? `<h2 style="margin-top:8px">Facts</h2>${r.facts.map((f) => `<div class="fact"><span>${hl(f.fact)}</span><span class="tag" title="score">${(f.score ?? 0).toFixed(2)}</span></div>`).join("")}` : '<p class="muted">No facts matched.</p>'}
      ${r.documents.length ? `<h2>Documents</h2>${r.documents.map(docRow).join("")}` : ""}`;
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
    if (reset || !S.project) { resetRun(); S.stepStatus = { 1: "done" }; }
    S.follow = true;
    const r = await api("/api/run", { reset });
    if (r) { S.st = r; setConn(); }
  }

  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo project, its reviews and documents, the demo actors and the uploaded source files from your account?")) return;
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
    else if (k === "project") S.project = { id: d.id, name: d.name, fresh: !!d.fresh };
    else if (k === "uploaded") S.files[d.name] = { status: "uploaded" };
    else if (k === "imported") Object.keys(S.files).forEach((n) => (S.files[n].status = "imported"));
    else if (k === "documents") (d.documents || []).forEach((x) => (S.files[x.name] = { status: x.status }));
    else if (k === "review") S.reviews[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false, pinned: (d.done || 0) > 0 };
    else if (k === "turn") (S.reviews[d.custom_id] ||= {}).stored = d.index;
    else if (k === "pinned") (S.reviews[d.custom_id] ||= {}).pinned = true;
    else if (k === "cooked") Object.values(S.reviews).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "facts") { S.facts = d.facts; S.revisionKind = d.revision_kind || "none"; }
    else if (k === "answer") { if (!S.brief) S.answers.push(d); }
    else if (k === "brief") { S.brief = d; S.answers = d.answers; }
    else if (k === "downloaded") S.downloaded = d;
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.project = null; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. The Q4 answers are ready, and you can ask the memory anything.", true);
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
    ALL_KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
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
      ALL_KINDS.forEach((kind) => es.addEventListener(kind, handler));
      es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : state.has_brief ? "answers" : "connect");
    subscribe();
  })();
})();

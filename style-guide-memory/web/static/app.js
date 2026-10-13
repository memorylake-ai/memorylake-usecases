/* Style guide memory for design teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (i) => (i ? String(i).split(":").pop().slice(0, 18) + "…" : "—");
  const hms = (s) => (s ? String(s).slice(11, 19) : "—");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "Library folder + project", view: "setup" },
    { n: 3, title: "Version 1", sub: "six files, imported", view: "v1" },
    { n: 4, title: "Ask the guide", sub: "answers vs the files", view: "ask1" },
    { n: 5, title: "Version 2 ships", sub: "three ways to upload", view: "release" },
    { n: 6, title: "Ask again", sub: "the same questions", view: "ask2" },
    { n: 7, title: "Obvious fixes", sub: "re-import · reload", view: "obvious" },
    { n: 8, title: "Freshness audit", sub: "documents vs files", view: "audit" },
    { n: 9, title: "Repair", sub: "re-import what is stale", view: "repair" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, v1: null, answers: {}, release: null, obvious: null, audits: {}, repair: null,
    ask: { q: "", html: "" }, extra: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>the guide · audit now</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, v1, ask1, release, ask2, obvious, audit, repair, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Style guide</dt><dd>${st.ready ? "from an earlier run — the story starts at version 1, so running again deletes it first" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4–5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>When the style guide changes, does every AI tool see the new version?</h1>
      <p class="lead">Kestrel Studio keeps its style guide as six Markdown files in one Library folder, imported into a project that every AI design tool searches.
      Version 2 ships and three people put their files back three different ways. The demo asks the guide the same three questions before and after, and checks every answer
      against <b>the file it quotes, downloaded as it is now</b> — then audits the index, finds every stale, orphaned and unindexed document, and repairs exactly those.
      This page drives the <code>memorylake</code> CLI for real. The studio and everyone in it are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the Library folder (every file in it) and the style guide project.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/style-guide-memory-for-design-teams" target="_blank" rel="noopener">memorylake.ai › style guide memory for design teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("Library folder + project");
    return `<h1>One Library folder for the guide, one project for the index</h1>
      <p class="lead">The Library holds the files people edit. The project is what AI tools search: <code>proj doc import</code> turns each file into a document.</p>
      <div class="grid"><div class="card"><h2 style="margin-top:0">Library</h2><div class="tree">${esc(su.folder)}/   ← one Markdown file per topic</div></div>
        <div class="card"><h2 style="margin-top:0">${esc(su.project)}</h2><div class="muted small">the index every AI design tool searches</div><div class="mono small faint">${esc(su.ids.project)}</div></div></div>`;
  }

  function v1() {
    const x = S.v1; if (!x) return empty("Version 1");
    return `<h1>Version 1 — six files into the Library, imported into the project</h1>
      <p class="lead"><code>lib upload</code> each file, then <code>proj doc import &lt;guide folder&gt; --recursive --wait</code> — ${x.seconds}s.</p>
      <div class="grid3"><div class="card"><div class="muted small">Library files</div><div class="bignum">${x.files}</div></div>
        <div class="card"><div class="muted small">documents in the project</div><div class="bignum">${x.docs}</div></div>
        <div class="card mine"><div class="muted small">status okay</div><div class="bignum ok">${x.okay}</div></div></div>
      <div class="card"><h2 style="margin-top:0">Documents</h2><table class="iso"><tr><th>document</th><th>doc id</th></tr>
        ${Object.entries(x.v1_docs).sort().map(([n, id]) => `<tr><td class="mono small">${esc(n)}</td><td class="mono small faint">${esc(short(id))}</td></tr>`).join("")}</table></div>`;
  }

  const VERDICT = {
    match: ["ok", "✓ matches the file"], stale: ["err", "✗ the file says something else"], gone: ["err", "✗ the file is gone (404)"],
    shadowed: ["warn", "✗ a newer copy is not indexed"], missing: ["err", "✗ not in the top 3"],
  };
  function answerCard(r) {
    const [cls, label] = VERDICT[r.verdict] || ["", r.verdict];
    return `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(r.question)}</h2><span class="pill ${cls}">${label}</span></div>
      <table class="iso" style="margin-top:10px">
        <tr><td class="small muted" style="width:120px">search says</td><td class="small">${r.rank ? `<span class="pill">#${r.rank}</span> <span class="mono small faint">${esc(r.file)} · ${esc(short(r.doc))}</span><div>“${esc(r.said || "(no sentence about it)")}”</div>` : `<span class="muted">${esc(r.file)} not in the top 3</span>`}</td></tr>
        <tr><td class="small muted">the file says</td><td class="small">${r.verdict === "gone" ? '<span class="pill err">404</span> the Library file behind this document is gone' : r.file_says ? `“${esc(r.file_says)}”` : "—"}</td></tr>
        ${r.copies?.length ? `<tr><td class="small muted">Library also has</td><td class="mono small">${r.copies.map(esc).join(", ")}</td></tr>` : ""}
      </table></div>`;
  }
  function answersView(label, title, lead) {
    const a = S.answers[label]; if (!a) return empty(title);
    return `<h1>${title}</h1><p class="lead">${lead}</p>
      <p><span class="pill ${a.ok === a.rows.length ? "ok" : "err"}">${a.ok} of ${a.rows.length} answers match their file</span> <span class="pill ${a.v2 === a.rows.length ? "ok" : ""}">${a.v2} of ${a.rows.length} quote version 2</span></p>
      ${a.rows.map(answerCard).join("")}`;
  }
  const ask1 = () => answersView("version 1", "Ask the guide — every answer checked against the file it quotes",
    "<code>search --types document</code> for each question, then <code>proj doc download</code> the document it quotes and compare the sentence.");
  const ask2 = () => answersView("after the release", "Ask again — the same three questions",
    "Version 2 is in the Library. The index answers anyway — from what it read on version 1 day.");

  function release() {
    const r = S.release; if (!r) return empty("Version 2 ships");
    return `<h1>Version 2 ships — three people, three ways to put a file back</h1>
      <p class="lead"><code>lib upload</code> without a flag uses the server default, <code>rename</code>. <code>overwrite</code> keeps the item and changes its bytes; <code>replace</code> deletes it and makes a new one; <code>deny</code> refuses.</p>
      <div class="card"><table class="iso"><tr><th>who</th><th>file</th><th>--on-conflict</th><th>what happened</th></tr>
        ${r.rows.map((x) => `<tr><td class="small">${esc(x.who)}</td><td class="mono small">${esc(x.file)}</td><td class="mono small">${esc(x.how)}</td>
          <td class="small">${x.refused ? '<span class="pill warn">409</span> ' : ""}${esc(x.result)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Afterwards</h2>
        <p class="small">Library folder: <span class="mono">${r.library.map(esc).join(", ")}</span></p>
        <p class="small">Project: <b>${r.docs} documents, all okay</b> — nothing on either listing says an answer is out of date.</p></div>`;
  }

  function obvious() {
    const o = S.obvious; if (!o) return empty("The two obvious fixes");
    return `<h1>The two obvious fixes — neither changes the answer</h1>
      <div class="grid"><div class="card"><div class="muted small">Fix 1 · <code>proj doc import</code> ${esc(o.file)} again</div>
          <div class="bignum">${esc(o.import_result)}</div><div class="small">duplicate_count ${esc(o.duplicates)} · ${o.same_doc ? "same document id" : "a new document"} — nothing is processed again</div></div>
        <div class="card"><div class="muted small">Fix 2 · <code>proj doc reload</code></div><div class="bignum err">refused</div><div class="small">${esc(o.reload)}</div></div></div>
      ${answerCard(o.after)}`;
  }

  const STATUS = { FRESH: "ok", STALE: "err", ORPHAN: "err", SHADOWED: "warn", COPY: "warn", "NOT INDEXED": "warn" };
  function auditCard(a) {
    if (!a) return "";
    return `<div class="card"><div class="row spread"><h2 style="margin:0">Audit — ${esc(a.label)}</h2>
        <span>${Object.entries(a.counts).map(([k, v]) => `<span class="pill ${STATUS[k] || ""}">${esc(k)} ${v}</span>`).join(" ")}</span></div>
      <p class="small muted">${a.files} Library files · ${a.docs} documents. A document is <b>stale</b> when its file changed after it was indexed, <b>orphaned</b> when its file is gone (download 404).</p>
      <table class="iso"><tr><th>status</th><th>name</th><th>indexed</th><th>file changed</th><th>why</th></tr>
        ${a.rows.map((r) => `<tr><td><span class="pill ${STATUS[r.status] || ""}">${esc(r.status)}</span></td><td class="mono small">${esc(r.name)}</td>
          <td class="mono small">${hms(r.indexed)}</td><td class="mono small">${hms(r.changed)}</td><td class="small">${esc(r.why)}</td></tr>`).join("")}</table></div>`;
  }
  function audit() {
    const a = S.audits["after the release"]; if (!a) return empty("Freshness audit");
    return `<h1>Freshness audit — every document against its file, every file against the index</h1>
      <p class="lead">Both listings, paged to the end (<code>lib list</code>, <code>proj doc list</code>), <code>proj doc download</code> for each document, and one rule per row.
      The three files nobody touched must come out <b>fresh</b> — an audit that flags everything is not an audit.</p>${auditCard(a)}`;
  }

  function repair() {
    const r = S.repair; if (!r) return empty("Repair");
    const fin = S.answers["after the repair"];
    return `<h1>Repair — re-import exactly what the audit found</h1>
      <p class="lead">${r.promoted.map((p) => `<code>${esc(p.copy)}</code> → <code>${esc(p.base)}</code> (the Library has no rename: index the copy, download its bytes, <code>--on-conflict overwrite</code> the original, delete the copy). `).join("")}
      Then <code>proj doc delete</code> ${r.dropped.length} stale/orphaned document(s) and <code>proj doc import</code> ${r.imported.length} file(s) — ${r.seconds}s.</p>
      ${auditCard(r.after)}
      <div class="card"><h2 style="margin-top:0">Files nobody touched keep their documents</h2>
        ${r.controls.map((c) => `<div class="memfact ${c.same ? "clean" : "personal"}"><span class="mk">${c.same ? "✓" : "✗"}</span><span class="body mono small">${esc(c.name)} &nbsp; ${esc(short(c.before))} → ${esc(short(c.after))}</span></div>`).join("")}</div>
      ${fin ? `<h2>Ask again</h2><p><span class="pill ${fin.ok === fin.rows.length ? "ok" : "err"}">${fin.ok} of ${fin.rows.length} answers match their file</span> <span class="pill ${fin.v2 === fin.rows.length ? "ok" : ""}">${fin.v2} of ${fin.rows.length} quote version 2</span></p>${fin.rows.map(answerCard).join("")}` : ""}`;
  }

  function askRows(r) {
    return `<h2>${r.rows.length} hits for “${esc(r.question)}”</h2>` + (r.rows.map((h) => `<div class="memfact ${h.verdict === "match" ? "clean" : "personal"}"><span class="mk">${h.verdict === "match" ? "✓" : "✗"}</span><span class="body">
      <span class="pill">#${h.rank}</span> <span class="mono small">${esc(h.name)}</span> <span class="small muted">${h.verdict === "match" ? "every quoted line is in the file" : h.verdict === "gone" ? "file gone (404)" : "the file has changed"}</span>
      <div class="small" style="margin-top:4px">“${esc(h.excerpt)}”</div></span></div>`).join("") || '<p class="muted">nothing</p>');
  }
  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the guide</h1>
      <p class="lead">Each hit is checked against its file, downloaded as it is now: every line the answer quotes must still be in it.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What color is the primary button?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["What color is the primary button?", "What corner radius do buttons use?", "How long do transitions last?", "Can product copy use exclamation marks?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Audit now</h2><button class="btn sm" data-act="audit" ${ready ? "" : "disabled"}>Audit</button></div>
        <div id="extra-result" style="margin-top:10px">${S.extra}</div></div>`;
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
    st.querySelector('[data-act="audit"]')?.addEventListener("click", async () => {
      const box = $("#extra-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> paging both lists, downloading every document…</span>`;
      const r = await api("/api/audit", {}); S.extra = r ? auditCard(r) : ""; if (document.body.contains(box)) box.innerHTML = S.extra;
    });
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
    S.ask.html = askRows(r);
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
  function resetView() { S.setup = null; S.v1 = null; S.answers = {}; S.release = null; S.obvious = null; S.audits = {}; S.repair = null; S.extra = ""; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's Library folder (every file in it) and the style guide project from your account?")) return;
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
    else if (k === "text") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "v1") S.v1 = d;
    else if (k === "answers") S.answers[d.label] = d;
    else if (k === "release") S.release = d;
    else if (k === "obvious") S.obvious = d;
    else if (k === "audit") S.audits[d.label] = d;
    else if (k === "repair") S.repair = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the Library folder (with every file in it) and the style guide project are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — every hit is checked against its file — or audit again.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "ask"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "v1", "answers", "release", "obvious", "audit", "repair", "ask", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.repair ? "repair" : "connect");
    subscribe();
  })();
})();

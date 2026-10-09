/* Codebase memory for engineering teams — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const side = (key) => key === "jun-park" ? "pm" : "analyst";
  const kindOf = (name) => name.endsWith(".pptx") ? "PowerPoint" : name.endsWith(".md") ? "Markdown" : "File";
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
  const repoTag = (r) => `<span class="repo r-${esc(r)}">${esc(r)}</span>`;

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "People & repos", sub: "one project per repository", view: "setup" },
    { n: 3, title: "Mirror docs/", sub: "Library folders + upload", view: "library" },
    { n: 4, title: "Recursive import", sub: "one command per repo", view: "imports" },
    { n: 5, title: "Reviews & incidents", sub: "threads become memory", view: "threads" },
    { n: 6, title: "Memory", sub: "what each repo remembers", view: "memory" },
    { n: 7, title: "Pre-commit check", sub: "blocked where it matters", view: "check" },
    { n: 8, title: "Day-one questions", sub: "across both repos", view: "answers" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "uploaded", "tree", "imported",
    "documents", "thread", "turn", "pinned", "cooked", "facts", "check", "answer", "brief", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, projects: {},
    uploaded: {},              // "repo/path" -> true
    imports: {},               // repo -> {expanded, success_count, duplicate_count, failure_count}
    documents: null,           // repo -> [{name, status}]
    threads: {},               // custom_id -> {id, stored, cooked, pinned}
    facts: null,               // repo -> [{fact, pinned}]
    checks: [], brief: null, answers: [],
    ask: { q: "", repos: null, html: "" },
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.uploaded = {}; S.imports = {}; S.documents = null; S.threads = {}; S.facts = null;
    S.checks = []; S.brief = null; S.answers = [];
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
        <span class="n">?</span><span class="t">Ask the memory<small>pick the repos to search</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  function renderStage() {
    const r = { connect, setup, library, imports, threads, memory, check, answers, ask }[S.view];
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
          <dt>Repo projects</dt><dd>${hasProjects() ? Object.entries(S.projects).map(([r, p]) => `${repoTag(r)} <span class="mono muted">${esc(p.id)}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd>
        </dl>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${hasProjects() ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes · everything it creates is prefixed <code>mlu-cdb-</code></span>
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
      <h1>Give engineering teams a codebase memory every AI tool can read</h1>
      <p class="lead">Two repositories, one memory each. Their <code>docs/</code> trees (ADRs, a runbook, last year's architecture-review deck) go in with one
      recursive import per repo; PR reviews and an incident review become dated facts; the gotchas are pinned. Then an AI assistant's proposed change is
      checked against each repo's memory before it is committed, and a new hire's questions are answered across both repos, every answer labelled with the repo it came from.
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
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/codebase-memory-for-engineering-teams" target="_blank" rel="noopener">memorylake.ai › codebase memory for engineering teams</a>
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
    return `
      <h1>Three engineers, and one project per repository</h1>
      <p class="lead">The page's promise is <b>one memory per repository</b>. In MemoryLake that is one <b>project</b> per repo: its documents, its review threads and the facts drawn from them stay inside it,
      so a search scoped to <code>--projects &lt;ledger-service&gt;</code> never sees checkout-web's rules — and a search over both sees everything.
      The engineers are <b>actors</b>, the speakers in the review threads.</p>
      <h2>Tidewell payments team</h2>
      <div class="grid">${Object.keys(S.data.people).map(personCard).join("")}</div>
      <h2>Repositories → projects</h2>
      ${Object.values(S.data.repos).map((r) => `<div class="card row spread"><div>${repoTag(r.key)} <b>${esc(r.name)}</b>
        <div class="muted small">custom id <code>${esc(r.custom_id)}</code>${S.projects[r.key] ? ` · <span class="mono">${esc(S.projects[r.key].id)}</span>` : ""}</div></div>
        <span class="pill ${S.projects[r.key] ? "ok" : ""}">${S.projects[r.key] ? "ready" : "pending"}</span></div>`).join("")}`;
  }

  function library() {
    if (!S.data) return "";
    return `
      <h1>Each repo's <code>docs/</code> folder, mirrored into the Library</h1>
      <p class="lead">The demo recreates the folder tree with <code>lib mkdir --parent</code> and uploads each file with <code>lib upload --parent … --on-conflict overwrite</code>
      (re-runs keep the same item ids). In a real setup this is a CI job on every merge to <code>main</code>. Formats as they are: Markdown ADRs and runbooks, and a PowerPoint deck from last year's architecture review.</p>
      <div class="card tree"><div class="mono"><b>${esc(S.data.library_root)}/</b></div>
      ${Object.entries(S.data.files).map(([repo, files]) => `<div class="tree-repo">${repoTag(repo)}<span class="mono faint">/</span></div>
        ${files.map((f) => { const up = S.uploaded[`${repo}/${f.path}`]; return `<details class="tree-file"><summary>
          <span class="kind k-${kindOf(f.path).toLowerCase()}">${kindOf(f.path)}</span>
          <a class="mono" href="/repos/${esc(repo)}/${esc(f.path)}" target="_blank" rel="noopener">${esc(f.path)}</a>
          <span class="faint small">${kb(f.size)}</span><span class="pill ${up ? "ok" : ""}" style="margin-left:auto">${up ? "uploaded" : "pending"}</span></summary>
          ${f.text ? `<pre>${esc(f.text)}</pre>` : `<p class="muted small">Binary file — MemoryLake parses the slides on import.</p>`}</details>`; }).join("")}`).join("")}
      </div>`;
  }

  function imports() {
    if (!S.data) return "";
    return `
      <h1>One recursive import per repo</h1>
      <p class="lead"><code>proj doc import --project &lt;repo&gt; &lt;folder id&gt; --recursive --wait</code> expands the folder into every file in its subtree, whatever the depth,
      and waits until each one is parsed. Re-running reports the files as already in the project instead of importing them twice.</p>
      ${Object.keys(S.data.repos).map((repo) => {
        const imp = S.imports[repo]; const docs = S.documents?.[repo] || [];
        return `<div class="card"><div class="row spread"><div>${repoTag(repo)} <span class="faint small mono">import … --recursive --wait</span></div>
          ${imp ? `<span class="pill ok">${imp.success_count} new · ${imp.duplicate_count} already there · ${imp.failure_count} failed</span>`
            : S.stepStatus[4] === "running" ? `<span class="pill busy"><span class="spinner"></span> parsing…</span>` : `<span class="pill">pending</span>`}</div>
          ${imp?.expanded ? `<div class="expanded mono">${esc(imp.expanded)}</div>` : ""}
          ${docs.length ? `<ul class="doclist">${docs.map((d) => `<li><span class="kind k-${kindOf(d.name).toLowerCase()}">${kindOf(d.name)}</span> <span class="mono">${esc(d.name)}</span> <span class="pill ${d.status === "okay" ? "ok" : ""}">${esc(d.status)}</span></li>`).join("")}</ul>` : ""}
        </div>`;
      }).join("")}`;
  }

  function threads() {
    if (!S.data) return "";
    return `
      <h1>PR reviews and an incident review become memory</h1>
      <p class="lead">Each thread is a group conversation in its repo's project, time-stamped to the day it happened — that is where the <i>(as of …)</i> on the facts comes from.
      Threads are stored one at a time and the demo waits for <code>cook-status</code> before the next. The gotcha each thread taught is pinned verbatim with <code>fact add</code>.</p>
      ${S.data.threads.map((c) => {
        const st = S.threads[c.custom_id] || {};
        const stored = st.stored || 0; const total = c.turns.length;
        const cook = st.cooked || (st.pinned && stored >= total) ? `<span class="pill ok">memory ready</span>`
          : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
          : stored ? `<span class="pill accent">${stored}/${total} messages stored</span>` : `<span class="pill">queued</span>`;
        return `<div class="card">
          <div class="call-head">${repoTag(c.repo)}<h3>${esc(c.name)}</h3><span class="muted small">${esc(c.source)} · ${fmtDate(c.date)}</span>
            <span style="margin-left:auto">${cook}</span></div>
          <div class="turns">${c.turns.map(([k, text], i) => `
            <div class="turn ${i < stored ? "stored" : ""}"><span class="avatar ${side(k)}">${initials(S.data.people[k].display)}</span>
              <div><div class="who">${esc(S.data.people[k].display)} · ${esc(S.data.people[k].role)}</div><div class="what">${esc(text)}</div></div>
              <span class="mark">${i < stored ? "✓ stored" : "msg " + (i + 1)}</span></div>`).join("")}</div>
          ${c.pinned?.length ? `<div class="notes">${c.pinned.map((n) => `<span class="note-chip ${st.pinned ? "" : "pending"}">📌 ${esc(n)}</span>`).join("")}</div>` : ""}
        </div>`;
      }).join("")}`;
  }

  function memory() {
    if (!S.facts) return `<h1>What does each repo remember?</h1><div class="empty">Run the demo first — the facts show up here once the threads are processed.</div>`;
    return `
      <h1>What does each repo remember?</h1>
      <p class="lead">Nobody tagged anything. The dated facts were written by MemoryLake from the threads; the 📌 ones are the pinned gotchas, stored verbatim. Each list is one project — one repo.</p>
      <div class="row" style="margin-bottom:12px"><button class="btn sm" data-act="refresh-facts">Refresh</button></div>
      <div class="cols">${Object.entries(S.facts).map(([repo, facts]) => `<div class="col">
        <div class="row spread">${repoTag(repo)}<span class="muted small">${facts.length} facts · ${facts.filter((f) => f.pinned).length} pinned</span></div>
        ${facts.map((f) => `<div class="fact ${f.pinned ? "pinned" : ""}"><span>${esc(f.fact)}</span><span class="tag">${f.pinned ? "pinned" : "extracted"}</span></div>`).join("")}
      </div>`).join("")}</div>`;
  }

  function check() {
    const list = S.checks;
    return `
      <h1>Pre-commit check: the AI assistant asks each repo's memory first</h1>
      <p class="lead">Jun's assistant proposes a change. Before it is committed, one <code>memorylake search --types fact</code> per repo asks that repo's memory about it.
      The repo that learned the lesson blocks it, with the reason; the other repo has never heard of the rule — one memory per repo means rules do not leak across.</p>
      ${list.length ? list.map((c) => `<div class="card"><h3>“${esc(c.change)}”</h3><div class="faint small mono">search "${esc(c.query)}" --types fact</div>
        <div class="verdicts">${c.verdicts.map((v) => `<div class="verdict ${v.blocked ? "blocked" : "clear"}">
          <div class="row spread">${repoTag(v.repo)}<b>${v.blocked ? "✋ blocked" : "✓ nothing against it"}</b></div>
          ${v.facts.map((f) => `<div class="small">${esc(f)}</div>`).join("") || `<div class="muted small">No fact in this repo's memory speaks to this change.</div>`}</div>`).join("")}</div></div>`).join("")
        : `<div class="empty">Run the demo first — the checks run once both repos' memory is ready.</div>`}`;
  }

  function docRow(d) {
    return `<div class="docres"><div class="row" style="gap:8px">${repoTag(d.repo)}<span class="kind k-${String(d.kind).toLowerCase()}">${esc(d.kind)}</span><b class="mono">${esc(d.name)}</b></div>
      <span class="muted small">${esc(d.summary || "")}</span></div>`;
  }

  function answerCard(a, withQuery) {
    return `<div class="card answer">${a.heading !== a.query ? `<h3>${esc(a.heading)}</h3>` : ""}
      ${withQuery ? `<div class="faint small mono">search "${esc(a.query)}" --projects ledger-service,checkout-web${a.types ? ` --types ${esc(a.types)}` : ""}</div>` : ""}
      ${a.facts.length ? `<ul>${a.facts.slice(0, 4).map((f) => `<li>${repoTag(f.repo)} ${esc(f.fact)}</li>`).join("")}</ul>` : ""}
      ${a.documents.slice(0, 3).map(docRow).join("")}
      ${!a.facts.length && !a.documents.length ? '<p class="muted">Nothing matched.</p>' : ""}</div>`;
  }

  function answers() {
    const list = S.brief?.answers || S.answers;
    if (!list.length) return `<h1>Day one: questions across both repos</h1><div class="empty">Run the demo first — five searches run at the end.</div>`;
    return `
      <div class="brief-head"><div><h1>Day one: Jun's questions, across both repos</h1>
        <p class="lead" style="margin:0">One search over both projects (<code>--projects a,b</code>). Search results do not say which project a hit came from, so the demo maps every fact and document id back to its repo.
        Last year's decision about Kafka lives only in a slide deck — and comes back as a PowerPoint hit.
        ${S.brief ? "" : '<span class="pill busy"><span class="spinner"></span> searching…</span>'}</p></div>
        ${S.brief ? `<button class="btn" data-act="download">Download brief .md</button>` : ""}</div>
      ${list.map((a) => answerCard(a, true)).join("")}`;
  }

  function ask() {
    const repos = S.data ? Object.keys(S.data.repos) : [];
    if (!S.ask.repos) S.ask.repos = [...repos];
    const sugg = ["can I use moment.js for dates", "how do I add a column to ledger_entries", "why do we use a transactional outbox",
      "where do payment calls from the browser go", "what happened in INC-2291"];
    const ready = hasProjects();
    return `
      <h1>Ask the codebase memory</h1>
      <p class="lead">The same <code>memorylake search</code> the steps use. Untick a repo to see one memory per repo at work: its rules simply are not there.</p>
      <div class="card"><div class="row" style="gap:16px;margin-bottom:10px">${repos.map((r) => `<label class="row" style="gap:6px"><input type="checkbox" class="repo-pick" value="${esc(r)}" ${S.ask.repos.includes(r) ? "checked" : ""}> ${repoTag(r)}</label>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. can I store fees as DOUBLE PRECISION?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
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
    stage.querySelector('[data-act="refresh-facts"]')?.addEventListener("click", async () => { const r = await api("/api/facts", {}); if (r) { S.facts = r.repos; renderStage(); } });
    stage.querySelector('[data-act="download"]')?.addEventListener("click", () => {
      const blob = new Blob([S.brief.markdown], { type: "text/markdown" }); const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "day-one-brief.md"; a.click(); URL.revokeObjectURL(a.href);
    });
    stage.querySelectorAll(".repo-pick").forEach((c) => c.addEventListener("change", () => {
      S.ask.repos = [...stage.querySelectorAll(".repo-pick:checked")].map((x) => x.value);
    }));
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
    const r = await api("/api/search", { query: q, repos: S.ask.repos, top_k: 5 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="faint small mono" style="margin-top:8px">searched: ${S.ask.repos.map(repoTag).join(" ")}</div>` + answerCard(r, false);
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
    if (!confirm("Delete both repo projects, the review threads, the demo actors and the mirrored docs folder from your account?")) return;
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
    else if (k === "project") S.projects[d.repo] = { id: d.id, name: d.name };
    else if (k === "uploaded") S.uploaded[d.path] = true;
    else if (k === "tree") Object.entries(d.repos || {}).forEach(([r, files]) => files.forEach((f) => (S.uploaded[`${r}/${f}`] = true)));
    else if (k === "imported") S.imports[d.repo] = d;
    else if (k === "documents") S.documents = d.repos;
    else if (k === "thread") S.threads[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false, pinned: (d.done || 0) >= d.turns };
    else if (k === "turn") (S.threads[d.custom_id] ||= {}).stored = d.index;
    else if (k === "pinned") (S.threads[d.custom_id] ||= {}).pinned = true;
    else if (k === "cooked") Object.values(S.threads).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "facts") S.facts = d.repos;
    else if (k === "check") S.checks.push(d);
    else if (k === "answer") { if (!S.brief && S.st.status === "running") S.answers.push(d); }
    else if (k === "brief") { S.brief = d; S.answers = d.answers; }
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.projects = {}; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. The checks and the day-one answers are ready, and you can ask the memory anything.", true);
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
    show(state.status === "running" ? S.view : state.has_brief ? "answers" : "connect");
    subscribe();
  })();
})();

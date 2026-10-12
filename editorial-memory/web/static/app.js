/* Editorial memory for publishing teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "Library tree + archive project", view: "setup" },
    { n: 3, title: "Archive", sub: "55 stories, tagged · count them", view: "archive" },
    { n: 4, title: "Index", sub: "import, then reconcile", view: "index" },
    { n: 5, title: "A late story", sub: "find what the index misses", view: "late" },
    { n: 6, title: "Sources", sub: "a retraction flags a folder", view: "flag" },
    { n: 7, title: "House style", sub: "rules stored as facts", view: "style" },
    { n: 8, title: "Draft check", sub: "coverage · sources · style", view: "check" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, upload: null, inventory: null, index: null, reconciles: [], late: null, flag: null, style: null, check: null,
    ask: { q: "", html: "" }, extra: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>the archive · reconcile now</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, archive, index, late, flag, style, check, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Archive</dt><dd>${st.ready ? "from an earlier run — Run again starts the story over on it (the 55 stories stay indexed), Reset &amp; run deletes it first" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 7 minutes the first time (most of it indexing 55 stories) · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>An archive bigger than one page — counted, indexed, and checked against every draft.</h1>
      <p class="lead">The Tidewater Ledger keeps every published story in one Library folder, <b>tagged as it is uploaded</b> with its date, desk and author, and indexed in an archive project.
      Every list call pages — and they page differently — so the demo reconciles the folder against the index page by page, finds the one story the index is missing,
      lets a standards editor retract a whole source folder with one tag, and checks a new writer's draft for prior coverage, retracted sources and house style.
      This page drives the <code>memorylake</code> CLI for real. The paper, the town and everyone in it are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the Library folder (every file and tag in it) and the archive project.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/editorial-memory-for-publishing-teams" target="_blank" rel="noopener">memorylake.ai › editorial memory for publishing teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("Library tree + archive project");
    const tree = `${su.folder}/            {"publication": …}\n├── archive/                       {"collection": "archive"}   ← every published story\n└── sources/                       {"collection": "sources"}\n${su.orgs.map((o, i) => `    ${i === su.orgs.length - 1 ? "└──" : "├──"} ${(o + "/").padEnd(27)}{"org": "${o}"}`).join("\n")}`;
    return `<h1>One folder for the archive, one per source organisation</h1>
      <p class="lead">Folders are created with their own tags (<code>lib mkdir --xattrs</code>). The archive project is the index writers search.</p>
      <div class="grid"><div class="card"><h2 style="margin-top:0">Library</h2><div class="tree">${esc(tree)}</div></div>
        <div class="card"><h2 style="margin-top:0">${esc(su.project)}</h2><div class="muted small">imports the archive folder; the house style is stored on it as facts</div><div class="mono small faint">${esc(su.ids.project)}</div></div></div>`;
  }

  function archive() {
    const u = S.upload, inv = S.inventory;
    if (!inv) return u ? `<h1>Uploading the archive</h1><p class="lead">Oldest first, each with <code>--xattrs '{"published":…,"desk":…,"author":…}'</code>.</p>
        <div class="card"><span class="pill busy"><span class="spinner"></span> uploaded ${u.done} of ${u.total}</span></div>` : empty("Archive — 55 stories, tagged");
    return `<h1>How many stories are in the archive folder?</h1>
      <p class="lead">Uploaded oldest first, each with its tags. The listing comes back newest first, 50 at a time at most.</p>
      <div class="grid3">
        <div class="card"><div class="muted small"><code>lib list &lt;archive&gt;</code> — one call</div><div class="bignum err">${inv.first}</div><div class="small">${inv.more ? "+ a continuation_token: there is more" : ""}</div></div>
        <div class="card"><div class="muted small"><code>--page-size 51</code></div><div class="bignum">400</div><div class="small">${esc(inv.refused)}</div></div>
        <div class="card mine"><div class="muted small">every page until the token is null</div><div class="bignum ok">${inv.total}</div><div class="small">${inv.calls} calls · ${inv.tagged} of ${inv.total} carry all three tags</div></div>
      </div>
      <div class="card"><h2 style="margin-top:0">The ${inv.beyond.length} the first page never shows — the oldest</h2>
        <table class="iso"><tr><th>published</th><th>desk</th><th>author</th><th>file</th></tr>
        ${inv.beyond.map((b) => `<tr><td class="mono small">${esc(b.published)}</td><td class="small">${esc(b.desk)}</td><td class="small">${esc(b.author)}</td><td class="mono small">${esc(b.name)}</td></tr>`).join("")}</table></div>`;
  }

  function recCard(r) {
    if (!r) return "";
    return `<div class="card"><h2 style="margin-top:0">Reconcile — ${esc(r.label)}</h2>
      <table class="iso"><tr><th></th><th>Library folder</th><th>index</th><th>“not indexed”</th><th></th></tr>
        <tr><td class="small">one call each, default page sizes</td><td>${r.naive_lib}</td><td>${r.naive_docs}</td><td><b>${r.naive_missing}</b></td>
          <td>${r.false_alarms ? `<span class="pill err">✗ ${r.false_alarms} false alarms</span>` : r.naive_missing ? "" : '<span class="pill">—</span>'}</td></tr>
        <tr><td class="small">every page</td><td>${r.lib}</td><td>${r.docs}</td><td><b>${r.missing.length}</b></td>
          <td>${!r.missing.length && !r.extra.length ? '<span class="pill ok">✓ in step</span>' : `<span class="pill warn">${r.missing.length} missing · ${r.extra.length} extra</span>`}</td></tr></table>
      ${r.missing.map((n) => `<div class="memfact personal"><span class="mk">!</span><span class="body mono small">not indexed: ${esc(n)}</span></div>`).join("")}
      ${r.extra.map((n) => `<div class="memfact"><span class="mk">?</span><span class="body mono small">in the index only: ${esc(n)}</span></div>`).join("")}</div>`;
  }
  function index() {
    const x = S.index; if (!x) return empty("Index — import, then reconcile");
    return `<h1>Index the folder, then reconcile it against the index</h1>
      <p class="lead"><code>proj doc import &lt;archive folder&gt; --recursive --wait</code>${x.imported ? ` — ${x.imported} stories in ${x.seconds}s` : " — already indexed by an earlier run"}.
      <code>lib list</code> returns 50 by default; <code>proj doc list</code> returns 20. Compare one call of each and ${x.reconcile.naive_missing} stories look unindexed.</p>
      <p><span class="pill ok">proj stats · okay ${x.stats.okay ?? "?"}</span> <span class="pill">running ${x.stats.running ?? "?"}</span> <span class="pill ${x.stats.error ? "err" : ""}">error ${x.stats.error ?? "?"}</span></p>
      ${recCard(x.reconcile)}
      <div class="card"><h2 style="margin-top:0"><code>proj update --description</code></h2><div class="small">“${esc(x.description)}”</div></div>`;
  }

  function late() {
    const l = S.late; if (!l) return empty("A late story");
    return `<h1>A late story — find exactly what the index is missing</h1>
      <p class="lead">The metro desk files <code>${esc(l.file)}</code> to the folder after the archive was indexed. The paged reconciliation names it; only that one is imported.</p>
      ${recCard(l.before)}${recCard(l.after)}
      <div class="card"><h2 style="margin-top:0">Project description, updated</h2><div class="small">“${esc(l.description)}”</div></div>`;
  }

  function flag() {
    const f = S.flag; if (!f) return empty("Sources — a retraction flags a folder");
    const fl = story().flag;
    return `<h1>A retraction flags a whole source folder</h1>
      <p class="lead">${esc(fl.date)}: ${esc(fl.by)} runs one call — <code>lib xattr set &lt;sources/${esc(fl.org)}&gt; --attrs '{"trust":"retracted",…}'</code>.
      The files inside do not inherit it: asked for the same keys, the server returns none for them. The demo walks up <code>parent_item_id</code> to find the nearest tag.</p>
      <div class="grid3">
        <div class="card"><div class="muted small">the folder</div><div class="bignum err">${esc(f.folder.trust || "—")}</div><div class="small">${esc(f.folder.flagged_on)} · ${esc(f.folder.flagged_by)}</div></div>
        <div class="card"><div class="muted small">files carrying the tag themselves</div><div class="bignum">${f.own} / ${f.kids}</div><div class="small"><code>lib list --xattr-keys trust,…</code></div></div>
        <div class="card mine"><div class="muted small">retracted by inheritance</div><div class="bignum err">${f.retracted.length} / ${f.kids}</div><div class="small">nearest tag up the parent chain</div></div>
      </div>
      <div class="card"><h2 style="margin-top:0">Every source file, effective trust</h2><table class="iso"><tr><th></th><th>org</th><th>file</th><th>effective trust</th></tr>
        ${f.rows.map((r) => `<tr><td>${r.trust === "retracted" ? '<span class="pill err">✗</span>' : '<span class="pill">·</span>'}</td><td class="small">${esc(r.org)}</td><td class="mono small">${esc(r.name)}</td>
          <td class="small">${r.trust ? `${esc(r.trust)}${r.inherited ? ` <span class="muted">(inherited from ${esc(r.where)}/)</span>` : ""}` : '<span class="faint">no tag on the file or its folders</span>'}</td></tr>`).join("")}</table>
        ${f.rows[0] ? `<p class="small muted">path walked for the first one: <span class="mono">${f.rows[0].chain.map(esc).join(" → ")}</span></p>` : ""}</div>`;
  }

  function style() {
    const s = S.style; if (!s) return empty("House style — rules stored as facts");
    return `<h1>House style — rules and editor corrections stored as facts</h1>
      <p class="lead"><code>fact add --project &lt;archive&gt; "rule 1" "rule 2" …</code>, then <code>fact update &lt;id&gt; --metadata '{"rule":…,"pattern":…,"fix":…}'</code>:
      the sentence is what a writer or an AI tool reads; the pattern in its metadata is what the draft check runs.</p>
      <div class="card"><table class="iso"><tr><th>fact</th><th>rule</th><th>pattern</th><th>text</th></tr>
        ${s.rules.map((r) => `<tr><td class="mono small faint">${esc(r.id.slice(0, 14))}…</td><td class="small">${esc(r.rule)}</td><td class="mono small">/${esc(r.pattern)}/</td><td class="small">${esc(r.text)}</td></tr>`).join("")}</table></div>`;
  }

  function draftHtml(text, c) {
    const bad = new Set(c.cites.filter((x) => x.status !== "ok").map((x) => x.name));
    const flagged = {}; c.style.forEach((f) => { (flagged[f.line] = flagged[f.line] || []).push(f.match); });
    return text.split("\n").map((ln, i) => {
      let h = esc(ln);
      (flagged[i + 1] || []).forEach((m) => { const e = esc(m); h = h.split(e).join(`<span class="hl">${e}</span>`); });
      h = h.replace(/\[source:\s*([^\]]+?)\s*\]/g, (all, n) => `<span class="${bad.has(n) ? "cite-bad" : "cite-ok"}">${all}</span>`);
      return `<span class="ln">${i + 1}</span>${h}`;
    }).join("\n");
  }
  const hitRow = (h) => `<div class="memfact ${h.page2 ? "new" : ""}"><span class="mk">${h.rank}</span><span class="body">
      <span class="mono small">${esc(h.published)}</span> <span class="small">${esc(h.author)} · ${esc(h.desk)}</span> <span class="mono small faint">${esc(h.name)}</span>
      ${h.page2 ? '<span class="pill accent">past page 1</span>' : ""}<div class="small" style="margin-top:4px">“${esc(h.excerpt)}”</div></span></div>`;
  const covCard = (cov) => `<div class="card"><div class="row spread"><h2 style="margin:0">Prior coverage — “${esc(cov.question)}”</h2>
      <span class="pill ${cov.page2 ? "accent" : ""}">${cov.page2} of ${cov.hits.length} from past page 1</span></div>
      <p class="small muted">Top ${cov.hits.length} archive hits (<code>search --types document</code>), oldest first; dates and authors come from the folder's tags, read with every page.</p>
      ${[...cov.hits].sort((a, b) => a.published.localeCompare(b.published)).map(hitRow).join("")}</div>`;
  function check() {
    const c = S.check; if (!c) return empty("Draft check");
    return `<h1>A new writer's draft — prior coverage, sources, house style</h1>
      <p class="lead">${esc(story().draft.author)}, first week on the metro desk, files <code>${esc(c.file)}</code>.
      <span class="pill err">${c.bad_sources.length} source(s) to replace</span> <span class="pill warn">${c.style.length} style fix(es)</span> <span class="pill">${c.coverage.hits.length} prior stories</span></p>
      <div class="card"><h2 style="margin-top:0">The draft</h2><div class="draft">${draftHtml(S.data?.draft || "", c)}</div></div>
      <div class="card"><h2 style="margin-top:0">Sources cited</h2>
        ${c.cites.map((x) => `<div class="memfact ${x.status === "retracted" ? "personal" : ""}"><span class="mk">${x.status === "retracted" ? "✗" : x.status === "ok" ? "✓" : "?"}</span><span class="body">
          <span class="mono small">${esc(x.name)}</span> ${x.org ? `<span class="pill">${esc(x.org)}</span>` : ""}
          ${x.status === "retracted" ? `<div class="leakwhy">↳ retracted — ${esc(x.tags.reason)} (${esc(x.tags.flagged_by)}, ${esc(x.tags.flagged_on)}; tag on ${esc(x.where)}/)</div>` : ""}</span></div>`).join("")}
        ${c.retraction ? `<p class="small">The archive on it — “${esc(c.retraction.question)}”: ${c.retraction.rank ? `<span class="pill ok">#${c.retraction.rank}</span> <span class="mono small">${esc(c.retraction.expected)}</span>` : `<span class="pill warn">not in the top 3</span>`}</p>` : ""}</div>
      <div class="card"><h2 style="margin-top:0">House style</h2>
        ${c.style.map((f) => `<div class="memfact personal"><span class="mk">✗</span><span class="body"><span class="small">line ${f.line} · <b>${esc(f.rule)}</b> · “${esc(f.match)}” → ${esc(f.fix)}</span><div class="trace mono">fact ${esc(f.fact)}</div></span></div>`).join("") || '<p class="muted">no problems</p>'}
        <p class="small muted">Same rules on a published story: <span class="pill ${c.control ? "warn" : "ok"}">${c.control} flag(s)</span></p></div>
      ${covCard(c.coverage)}`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the archive</h1>
      <p class="lead">“Have we written about this?” — the archive project answers; each hit is dated from the folder's tags.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What have we written about the ferry?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["What have we written about the ferry?", "Harbor Authority tide gauge data", "Calder High robotics team", "Brenner's Bakery on Harbor Street"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Reconcile now</h2><button class="btn sm" data-act="reconcile" ${ready ? "" : "disabled"}>Reconcile</button></div>
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
    st.querySelector('[data-act="reconcile"]')?.addEventListener("click", async () => {
      const box = $("#extra-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> paging both lists…</span>`;
      const r = await api("/api/reconcile", {}); S.extra = r ? recCard(r) : ""; if (document.body.contains(box)) box.innerHTML = S.extra;
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
    S.ask.html = `<h2>${r.hits.length} archive stories, oldest first</h2>${[...r.hits].sort((a, b) => a.published.localeCompare(b.published)).map(hitRow).join("") || '<p class="muted">nothing</p>'}`;
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
  function resetView() { S.setup = null; S.upload = null; S.inventory = null; S.index = null; S.late = null; S.flag = null; S.style = null; S.check = null; S.extra = ""; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's Library folder (every file and tag in it) and the archive project from your account?")) return;
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
    else if (k === "progress") { term("progress", ev.text.trim()); if (d.total) { S.upload = d; if (S.caughtUp && S.view === "archive" && !S.inventory) renderStage(); } }
    else if (k === "json") term("dim", ev.text);
    else if (k === "text") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "inventory") S.inventory = d;
    else if (k === "index") S.index = d;
    else if (k === "late") S.late = d;
    else if (k === "flag") S.flag = d;
    else if (k === "style") S.style = d;
    else if (k === "check") S.check = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the Library folder (with every file and tag) and the archive project are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — search the archive, or reconcile again.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "reconcile"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "inventory", "index", "reconcile", "late", "flag", "style", "check", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.check ? "check" : "connect");
    subscribe();
  })();
})();

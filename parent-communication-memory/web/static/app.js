/* Parent communication memory for schools — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "a folder per family, two projects", view: "setup" },
    { n: 3, title: "Capture", sub: "six communications, tagged", view: "capture" },
    { n: 4, title: "Route by tag", sub: "teachers vs counselor only", view: "route" },
    { n: 5, title: "Audit", sub: "before the parent meeting", view: "audit" },
    { n: 6, title: "Meeting prep", sub: "same question, two scopes", view: "prep" },
    { n: 7, title: "After the meeting", sub: "close follow-ups", view: "after" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, capture: null, route: null, audit: null, prep: null, after: null,
    ask: { q: "", scope: "staff", html: "" }, extra: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>as a teacher or the counselor</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, capture, route, audit, prep, after, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Family folder</dt><dd>${st.ready ? "from an earlier run — Run again starts the story over on it, Reset &amp; run deletes it first" : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>Every teacher's thread with a family — tagged, routed, audited.</h1>
      <p class="lead">Maplebrook Academy keeps one Library folder per family. Each email, phone call, hallway chat, portal message and counselor note goes in as a file,
      <b>tagged as it is uploaded</b> with who logged it, the channel, the date, how sensitive it is and whether a follow-up is open.
      The tags decide who can search it, drive the audit before Wednesday's parent meeting, and close the follow-ups afterwards.
      This page drives the <code>memorylake</code> CLI for real. All people and records are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the family folder (every file and tag in it) and both projects.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/parent-communication-memory-for-schools" target="_blank" rel="noopener">memorylake.ai › parent communication memory for schools</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("A folder per family, two projects");
    return `<h1>One Library folder per family, two projects</h1>
      <p class="lead">${esc(story().school)}: ${esc(su.family.student)} (${esc(su.family.grade)}), parents ${su.family.parents.map(esc).join(" and ")}.
      The files live once, in <code>${esc(su.folder)}/</code>; each project imports only the files its readers may search.</p>
      <div class="grid3">
        <div class="card"><h2 style="margin-top:0">Library folder</h2><div class="mono">${esc(su.folder)}/</div><div class="muted small">created with <code>lib mkdir --xattrs '{"family":"whitfield"}'</code></div><div class="mono small faint">${esc(su.ids.family)}</div></div>
        <div class="card"><h2 style="margin-top:0">${esc(su.projects.staff)}</h2><div class="muted small">searched by every teacher's tools</div><div class="mono small faint">${esc(su.ids.staff)}</div></div>
        <div class="card"><h2 style="margin-top:0">${esc(su.projects.counselor)}</h2><div class="muted small">searched only by the counselor's tools</div><div class="mono small faint">${esc(su.ids.counselor)}</div></div>
      </div>
      <div class="card"><h2 style="margin-top:0">Staff who should hear from the family</h2><table class="iso"><tr><th>name</th><th>role</th><th>searches</th></tr>
        ${su.staff.map((s) => `<tr><td>${esc(s.name)}</td><td>${esc(s.role)}</td><td class="small">${s.scope === "counselor" ? "teachers' + counselor projects" : "teachers' project"}</td></tr>`).join("")}</table></div>`;
  }

  const COLS = ["channel", "teacher", "logged_on", "sensitivity", "follow_up"];
  const tagTable = (rows, cols) => `<table class="iso"><tr><th>file</th>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>
    ${rows.map((r) => `<tr><td class="small mono">${esc(r.name)}</td>${cols.map((c) => `<td class="small">${r.tags[c] ? esc(r.tags[c]) : '<span class="faint">·</span>'}</td>`).join("")}</tr>`).join("")}</table>`;
  function capture() {
    const c = S.capture; if (!c) return empty("Capture — six communications, tagged");
    return `<h1>Six communications, tagged as they are uploaded</h1>
      <p class="lead"><code>lib upload &lt;file&gt; --parent &lt;family folder&gt; --xattrs '{…}'</code> — one call stores the file and its provenance.
      ${c.uploaded ? `${c.uploaded} uploaded this run.` : `All ${c.retagged} were already there; their tags were reset to the story's start.`}</p>
      <div class="card"><div class="row spread"><h2 style="margin:0"><code>lib list &lt;folder&gt;</code> — the default listing</h2><span class="pill ${c.visible ? "warn" : "err"}">custom tags shown: ${c.visible} of ${c.default.length} files</span></div>
        <table class="iso"><tr><th>file</th><th>what <code>x_attrs</code> holds</th></tr>
        ${c.default.map((d) => `<tr><td class="small mono">${esc(d.name)}</td><td class="small">${d.keys.length ? d.keys.map((k) => d.signed.includes(k) ? `<code>${esc(k)}</code> = a signed thumbnail URL (not shown)` : `<code>${esc(k)}</code>`).join(", ") : '<span class="faint">nothing yet</span>'}</td></tr>`).join("")}</table></div>
      <div class="card mine"><div class="row spread"><h2 style="margin:0"><code>lib list &lt;folder&gt; --xattr-keys ${esc((S.capture.keys || []).join(","))}</code></h2>
        <span><span class="pill ok">${c.complete} of ${c.projected.length} carry the tags they were logged with</span> <span class="pill ${c.signed_in_projection ? "err" : "ok"}">signed URLs: ${c.signed_in_projection}</span></span></div>
        <p class="small muted">Asking for the tags by name returns exactly those keys, and nothing else.</p>
        ${tagTable(c.projected, COLS)}</div>`;
  }

  function route() {
    const r = S.route; if (!r) return empty("Route by tag");
    const col = (k) => `<div class="card ${k === "counselor" ? "" : "mine"}"><h2 style="margin-top:0">${esc(story().projects[k].name)}</h2>
      <div class="small muted">sensitivity = ${k} · ${r.imported[k].length} document(s)</div>
      ${r.imported[k].map((n) => `<div class="memfact"><span class="mk">•</span><span class="body mono small">${esc(n)}</span></div>`).join("")}</div>`;
    return `<h1>Route by tag — <code>sensitivity</code> decides who can search it</h1>
      <p class="lead">The demo reads the folder with <code>--xattr-keys sensitivity</code> and imports each file into one project (<code>proj doc import --wait</code>).
      Teachers' tools search the teachers' project; the counselor's search both.</p>
      <div class="grid">${col("staff")}${col("counselor")}</div>
      ${r.removed?.length ? `<div class="card"><span class="pill warn">found misfiled</span> ${r.removed.map(esc).join(", ")} was in the teachers' project — removed with <code>proj doc delete</code></div>` : ""}
      <div class="card"><span class="pill ${r.leaks.length ? "err" : "ok"}">leak check: ${r.leaks.length} counselor-tagged file(s) in the teachers' project</span>
        <span class="small muted">— the teachers' document list, intersected with the folder's counselor-tagged files</span></div>`;
  }

  function auditCards(a) {
    return `<div class="card"><h2 style="margin-top:0">Who has talked to the family <span class="muted small">as of ${esc(a.as_of)} · window ${a.window} days</span></h2>
      <table class="iso"><tr><th></th><th>staff</th><th>role</th><th>logged</th><th>last</th><th>channels</th></tr>
      ${a.roster.map((p) => `<tr><td>${p.flag ? '<span class="pill err">✗</span>' : '<span class="pill ok">✓</span>'}</td><td>${esc(p.name)}</td><td class="small">${esc(p.role)}</td><td>${p.count}</td>
        <td class="small mono">${esc(p.last || "—")}</td><td class="small">${p.flag ? `<b>${esc(p.flag)}</b>` : esc(p.channels.join(", "))}</td></tr>`).join("")}</table></div>
      <div class="grid">
        <div class="card"><h2 style="margin-top:0">Open follow-ups · ${a.open.length}</h2>
          ${a.open.map((o) => `<div class="memfact personal"><span class="mk">!</span><span class="body"><span class="mono small">${esc(o.name)}</span><div class="trace">${esc(o.teacher)} · ${esc(o.channel)} · open ${o.age} days</div></span></div>`).join("") || '<p class="muted">none</p>'}</div>
        <div class="card"><h2 style="margin-top:0">Closed · ${a.closed.length}</h2>
          ${a.closed.map((c) => `<div class="memfact clean"><span class="mk">✓</span><span class="body"><span class="mono small">${esc(c.name)}</span><div class="trace">${esc(c.resolved_on)} — ${esc(c.outcome)}</div></span></div>`).join("") || '<p class="muted">none yet</p>'}</div>
      </div>`;
  }
  function audit() {
    const a = S.audit; if (!a) return empty("Before the parent meeting — audit");
    return `<h1>Before the parent meeting — a completeness audit from the tags</h1>
      <p class="lead">One call — <code>lib list &lt;folder&gt; --xattr-keys channel,teacher,logged_on,sensitivity,follow_up,resolved_on,outcome</code> — and the school knows
      who has no logged contact with the family and which promises are still open. No file is opened, nothing is searched.</p>${auditCards(a)}`;
  }

  const hitRow = (h) => `<div class="memfact ${h.sensitive.length ? "personal" : ""}"><span class="mk">${h.sensitive.length ? "!" : h.rank}</span><span class="body">
      <span class="mono small">${esc(h.name)}</span> <span class="pill">${esc(h.channel)} · ${esc(h.teacher)} · ${esc(h.logged_on)}</span> <span class="pill ${h.sensitivity === "counselor" ? "err" : ""}">${esc(h.sensitivity)}</span>
      <div class="small" style="margin-top:4px">“${esc(h.excerpt)}”</div>${h.sensitive.length ? `<div class="leakwhy">↳ mentions ${h.sensitive.map(esc).join(", ")}</div>` : ""}</span></div>`;
  function prep() {
    const p = S.prep; if (!p) return empty("Meeting prep");
    return `<h1>Meeting prep — the same question, two scopes</h1>
      <p class="lead">“${esc(p.question)}” — <code>search --types document --top-k 5</code>, once over the teachers' project and once over both. Every hit carries its tags, read back by file name.</p>
      <div class="grid">
        <div class="card mine"><div class="row spread"><h2 style="margin:0">${esc(story().prep.asker)} · teachers' project</h2><span class="pill ${p.staff_sensitive ? "err" : "ok"}">home situation: ${p.staff_sensitive}</span></div>${p.staff.map(hitRow).join("")}</div>
        <div class="card"><div class="row spread"><h2 style="margin:0">Ms. Ibarra · both projects</h2><span class="pill">home situation: ${p.counselor_sensitive.length}${p.counselor_sensitive.length ? ` (rank ${p.counselor_sensitive.join(", ")})` : ""}</span></div>${p.counselor.map(hitRow).join("")}</div>
      </div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Across teachers — ${esc(p.cross.asker)} (English) asks “${esc(p.cross.question)}”</h2><span class="pill ${p.cross.ok ? "ok" : "warn"}">${p.cross.ok ? "✓" : "✗"} top hit logged by ${esc(p.cross.hits[0]?.teacher || "—")}</span></div>
        ${p.cross.hits.map(hitRow).join("")}</div>`;
  }

  function after() {
    const a = S.after; if (!a) return empty("After the meeting");
    return `<h1>After the meeting — close follow-ups, audit again</h1>
      <p class="lead">For each promise kept at the meeting: <code>lib xattr delete &lt;file&gt; --keys follow_up</code>, then <code>lib xattr set &lt;file&gt; --attrs '{"resolved_on":…,"outcome":…}'</code>.
      The same one-call audit now shows what is still owed.</p>
      <p><span class="pill warn">open follow-ups: ${S.audit ? S.audit.open.length : "?"} → ${a.open.length}</span></p>${auditCards(a)}`;
  }

  function ask() {
    const ready = !!S.st.ready;
    const scopes = [["staff", "as a teacher"], ["counselor", "as the counselor"]];
    return `<h1>Ask the family's memory — as a teacher or as the counselor</h1>
      <p class="lead">A teacher's question searches the teachers' project; the counselor's searches both. Each hit shows the tags of the file it came from.</p>
      <div class="card"><div class="chips">${scopes.map(([k, l]) => `<button type="button" class="chip scope ${S.ask.scope === k ? "on" : ""}" data-scope="${k}">${l}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form" style="margin-top:10px"><input id="ask-q" placeholder="e.g. When is Leo away in November?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["When is Leo away in November?", "How is Leo doing at home?", "What name should we use for Leo?", "Who is helping Leo with math outside school?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Audit now</h2><button class="btn sm" data-act="audit" ${ready ? "" : "disabled"}>Run the audit</button></div>
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
      const box = $("#extra-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the tags…</span>`;
      const r = await api("/api/audit", {}); S.extra = r ? auditCards(r) : ""; if (document.body.contains(box)) box.innerHTML = S.extra;
    });
    st.querySelectorAll(".chip.scope").forEach((c) => c.addEventListener("click", () => { S.ask.scope = c.dataset.scope; S.ask.q = $("#ask-q").value; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { q, scope: S.ask.scope });
    if (!r) { box.innerHTML = ""; return; }
    const n = r.hits.filter((h) => h.sensitive.length).length;
    S.ask.html = `<h2>${r.scope === "counselor" ? "Teachers' + counselor projects" : "Teachers' project"} · ${r.hits.length} document(s)</h2>${r.hits.map(hitRow).join("") || '<p class="muted">nothing</p>'}
      <p class="muted small">${n ? `${n} hit(s) mention the home situation.` : "No hit mentions the home situation."}</p>`;
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
  function resetView() { S.setup = null; S.capture = null; S.route = null; S.audit = null; S.prep = null; S.after = null; S.extra = ""; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's family folder (every file and tag in it) and both projects from your account?")) return;
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
    else if (k === "capture") S.capture = d;
    else if (k === "route") S.route = d;
    else if (k === "audit") S.audit = d;
    else if (k === "prep") S.prep = d;
    else if (k === "after") S.after = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the family folder (with every file and tag) and both projects are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — as a teacher, then as the counselor.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "capture", "route", "audit", "prep", "after", "cleaned", "done", "error"];
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

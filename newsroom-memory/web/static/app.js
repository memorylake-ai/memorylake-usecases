/* Newsroom memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "records folder, one project", view: "setup" },
    { n: 3, title: "The records arrive", sub: "three scans, no text layer", view: "records" },
    { n: 4, title: "Find the line", sub: "record, page, line", view: "find" },
    { n: 5, title: "Interviews", sub: "claims, with who said them", view: "claims" },
    { n: 6, title: "Flags", sub: "contradictions, both sources", view: "flags" },
    { n: 7, title: "Before filing", sub: "quotes vs the scan", view: "quotes" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, records: null, find: null, claims: null, wait: null, flags: null, quotes: null,
    ask: { q: "", html: "" }, extra: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const q = (s) => `“${esc(s)}”`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>claims, records, quotes</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, records, find, claims, flags, quotes, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Desk project</dt><dd>${st.ready ? "from an earlier run — Run again reuses it, Reset &amp; run deletes it first" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2½ minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Scanned records you can quote, and claims checked against them.</h1>
      <p class="lead">Dana Ruiz at <i>The Marlow Ledger</i> is reporting on the Harbor Street Bridge. A public-records request comes back as <b>three scanned PDFs — no text layer</b>.
      MemoryLake reads them; every interview claim is pinned with who said it and when; and <b>the contradiction detector flags claims that conflict with the records or with each other</b>,
      with attribution on both sides. Before filing, every quote is checked against the page it cites.
      This page drives the <code>memorylake</code> CLI for real. All people, places and records are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the desk project (records, claims, flags) and the Library folder.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-journalists-and-newsrooms" target="_blank" rel="noopener">memorylake.ai › AI memory for journalists and newsrooms</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("A records folder and one project");
    return `<h1>A Library folder for the records request, one project for the story</h1>
      <p class="lead">${esc(su.newsroom)} · ${esc(su.reporter)} · the ${esc(su.story)} story. Request filed ${esc(su.request.filed)}, released ${esc(su.request.released)} by ${esc(su.request.office)}.</p>
      <div class="grid">
        <div class="card"><h2 style="margin-top:0">Library folder</h2><div class="mono">${esc(su.folder)}/</div><div class="muted small">what the records request returned, as files</div><div class="mono small faint">${esc(su.ids.folder)}</div></div>
        <div class="card mine"><h2 style="margin-top:0">${esc(su.project)}</h2><div class="muted small">records + interview claims, checked against each other</div><div class="mono small faint">${esc(su.ids.project)}</div></div>
      </div>`;
  }

  function records() {
    const r = S.records; if (!r) return empty("The records arrive");
    const files = Object.entries(r.local);
    return `<h1>Three scans with no text layer — read line by line</h1>
      <p class="lead">Inside each PDF there are only page images and <b>no fonts</b>: nothing to copy text from. After <code>proj doc import --wait</code>,
      <code>proj doc inspect</code> points at MemoryLake's OCR layout (a signed link — used, never shown): one element per block it read off each page.</p>
      <div class="card"><table class="iso"><tr><th>file</th><th>pages</th><th>page images</th><th>fonts</th><th></th></tr>
        ${files.map(([n, f]) => `<tr><td class="mono small">${esc(n)}</td><td>${f.pages}</td><td>${f.images}</td><td>${f.fonts}</td><td>${f.fonts ? "" : '<span class="pill warn">scan · no text layer</span>'}</td></tr>`).join("")}</table></div>
      <div class="grid3">${r.rows.map((row) => `<div class="card"><h2 style="margin-top:0" class="mono small">${esc(row.file)}</h2>
        ${row.pages.map((p) => `<div class="small"><b>page ${p.page}</b> · ${p.lines} lines · <span class="pill ${p.same === p.want ? "ok" : "warn"}">${p.same}/${p.want} word for word</span></div>
          <pre class="scan">${p.first.map(esc).join("\n")}</pre>`).join("")}</div>`).join("")}</div>
      <div class="card"><span class="pill ${r.verbatim === r.total ? "ok" : "warn"}">${r.verbatim} of ${r.total} typed lines came back word for word</span>
        <span class="small muted">— compared with the text the scans were printed from</span></div>`;
  }

  function find() {
    const f = S.find; if (!f) return empty("Find the line");
    const terms = story().find.show_terms;
    return `<h1>Ask a question — get the record, the page and the line</h1>
      <p class="lead">${esc(story().reporter)} asks ${q(f.question)} — <code>search --types document</code>. Each hit is a page of a scan, with the box it sits in (fractions of the page).</p>
      <div class="card">${f.hits.map((h) => h.chunks.map((c) => `<div class="memfact"><span class="mk">${h.rank}</span><span class="body">
        <span class="mono small">${esc(h.name)}</span> <span class="pill">page ${c.page}</span> <span class="small faint">box ${esc(JSON.stringify(c.box))}</span>
        ${c.text.split("\n").filter((ln) => terms.some((t) => ln.toLowerCase().includes(t))).map((ln) => `<div class="small mono ${ln === f.expect_line ? "hl" : ""}">| ${esc(ln)}</div>`).join("")}</span></div>`).join("")).join("")}</div>
      <div class="card"><span class="pill ${f.found ? "ok" : "err"}">${f.found ? `✓ ${q(f.found.line)} — ${esc(f.found.file)}, page ${f.found.page}` : "✗ not found"}</span></div>
      <div class="card"><h2 style="margin-top:0"><code>proj doc list --name ${esc(f.name_filter)}</code></h2><p class="small muted">a case-insensitive substring filter on the document name</p>
        ${f.named.map((n) => `<div class="mono small">${esc(n)}</div>`).join("") || '<p class="muted">none</p>'}</div>`;
  }

  const who = (m) => `${esc(m.source)} <span class="muted">(${esc(m.role)})</span> · ${esc(m.medium)} · <span class="mono">${esc(m.said_on)}</span>`;
  function claims() {
    const c = S.claims; if (!c) return empty("Interviews");
    return `<h1>Each claim pinned with who said it, when and how</h1>
      <p class="lead"><code>fact add --project …</code> stores the claim word for word; <code>fact update &lt;id&gt; --metadata '{"source":…,"said_on":…}'</code> attaches the attribution.
      ${c.written ? `${c.written} pinned this run, ${WRITE_GAP_S} s apart (back-to-back writes delay the detector by minutes).` : "All four were pinned in an earlier run."}</p>
      <div class="card">${c.claims.map((x) => `<div class="memfact"><span class="mk">•</span><span class="body"><div class="small">${who(x.meta)}</div><div>${q(x.text)}</div><div class="trace mono">${esc(x.fact_id || "")}</div></span></div>`).join("")}</div>`;
  }
  const WRITE_GAP_S = 15;

  function flagCard(v) {
    const claim = Object.fromEntries((S.claims?.claims || story().claims.map((c) => ({ key: c.key, text: c.text, meta: c }))).map((c) => [c.key, c]));
    return `<div class="card ${v.category === "m2d" ? "mine" : ""}"><div class="row spread"><h2 style="margin:0">${esc(v.name)}</h2>
        <span class="pill warn">${esc(v.category)} · ${v.category === "m2d" ? "claim vs record" : "claim vs claim"}</span></div>
      ${v.claims.map((k) => `<div class="memfact personal"><span class="mk">!</span><span class="body"><div class="small">said by ${who(claim[k]?.meta || {})}</div><div>${q(claim[k]?.text)}</div></span></div>`).join("")}
      ${v.records.map((r) => `<div class="memfact clean"><span class="mk">▤</span><span class="body"><div class="small">record <span class="mono">${esc(r.file)}</span>, <b>page ${r.page ?? "?"}</b> (released ${esc(story().records_request.released)})</div>
        ${r.line ? `<div class="mono small hl">“${esc(r.line)}”</div>` : ""}<div class="trace">${r.lines} lines in the excerpt; the page comes from matching it against the OCR layout</div></span></div>`).join("")}</div>`;
  }
  function flags() {
    const f = S.flags;
    if (!f) return S.wait ? `<h1>Waiting for the contradiction detector</h1><div class="card"><span class="pill busy"><span class="spinner"></span> reading <code>fact conflict list</code> every 5 s, up to ${S.wait.seconds} s</span></div>` : empty("Flags");
    const exp = f.expected;
    return `<h1>Contradictions, with both sources</h1>
      <p class="lead">MemoryLake checks each new fact against the project's documents (<b>m2d</b>) and its other facts (<b>m2m</b>). <code>fact conflict list</code> gives the facts and the record excerpt;
      the demo adds who said each claim (from its metadata) and which page the excerpt is on (from the OCR layout — the excerpt itself has no page number).</p>
      ${f.flags.sort((a, b) => (a.category !== "m2d") - (b.category !== "m2d")).map(flagCard).join("")}
      <div class="card"><h2 style="margin-top:0">Against what the reporting predicts</h2>
        ${exp.map((e) => `<div class="small">${f.flags.some((v) => matches(e, v)) ? "✓" : "✗"} <b>${esc(e[0])}</b> ${esc(e[1])} ⟂ ${esc(e[0] === "m2d" ? e[2].join(" or ") : e[2])}</div>`).join("")}
        ${f.quiet.map((k) => `<div class="small">${f.quiet_ok.includes(k) ? "✓" : "✗"} <b>no flag</b> ${esc(k)} (no record covers it)</div>`).join("")}
        <p class="small muted">${f.extra ? `${f.extra} flag(s) not in the plan — shown above. ` : ""}Excerpts traced to a page: ${f.paged}/${f.recs}.</p></div>`;
  }
  function matches(e, v) {
    if (e[0] === "m2d") return v.category === "m2d" && v.claims.includes(e[1]) && v.records.some((r) => e[2].includes(r.file));
    return v.category === "m2m" && v.claims.includes(e[1]) && v.claims.includes(e[2]);
  }

  const quoteRow = (r) => `<div class="memfact ${r.ok ? "clean" : "personal"}"><span class="mk">${r.ok ? "✓" : "✗"}</span><span class="body">
      <div class="mono small">“${esc(r.quote)}”</div><div class="small muted">${esc(r.file)}, page ${r.page}</div>
      ${r.ok ? "" : `<div class="leakwhy">${r.near ? `↳ page ${r.page} says “${esc(r.near)}”` : `↳ not on page ${r.page}`}</div>`}</span></div>`;
  function quotes() {
    const c = S.quotes; if (!c) return empty("Before filing");
    return `<h1>Before filing — every quote checked against the page it cites</h1>
      <p class="lead">Each quoted record line in the draft must appear, character for character, on the cited page of the OCR layout. One misquote is planted to show the check failing.</p>
      <div class="card">${c.rows.map(quoteRow).join("")}</div>
      <div class="card"><span class="pill ${c.good === c.rows.length ? "ok" : "warn"}">${c.good} of ${c.rows.length} quotes match the scan</span> <span class="small muted">— the misquote is caught before the story is filed</span></div>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    const recs = story()?.records || [];
    return `<h1>Ask the story's memory</h1>
      <p class="lead">One <code>search</code> returns claims (with who said them) and record pages. Or check a quote against a scan.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What did the city say the repair will cost?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["What did the city say the repair will cost?", "Who said the bridge passed inspection?", "When was the bridge inspected before 2026?", "How many vehicles cross the bridge?"].map((x) => `<button type="button" class="chip" data-q="${esc(x)}">${esc(x)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>
      <div class="card"><h2 style="margin-top:0">Check a quote</h2>
        <form class="ask-form" id="quote-form"><input id="quote-q" placeholder="Repair estimate: 2.4 million dollars." ${ready ? "" : "disabled"}>
          <select id="quote-file" ${ready ? "" : "disabled"}>${recs.map((r) => `<option>${esc(r.file)}</option>`).join("")}</select>
          <select id="quote-page" ${ready ? "" : "disabled"}><option>1</option><option>2</option></select><button class="btn primary" ${ready ? "" : "disabled"}>Check</button></form>
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
    st.querySelector("#quote-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const text = $("#quote-q").value.trim(); if (!text) return;
      const box = $("#extra-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the OCR layout…</span>`;
      const r = await api("/api/quote", { q: text, file: $("#quote-file").value, page: Number($("#quote-page").value) });
      S.extra = r ? quoteRow(r) : ""; if (document.body.contains(box)) box.innerHTML = S.extra;
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
    S.ask.html = `<h2>Claims · ${r.claims.length}</h2>${r.claims.map((c) => `<div class="memfact"><span class="mk">•</span><span class="body"><div>${esc(c.text)}</div><div class="trace">${esc(c.source)}</div></span></div>`).join("") || '<p class="muted">none</p>'}
      <h2>Records · ${r.records.length}</h2>${r.records.map((x) => `<div class="memfact clean"><span class="mk">▤</span><span class="body"><span class="mono small">${esc(x.file)}</span> <span class="pill">page ${x.page}</span><div class="small">“${esc(x.text)}”</div></span></div>`).join("") || '<p class="muted">none</p>'}`;
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
  function resetView() { S.setup = null; S.records = null; S.find = null; S.claims = null; S.wait = null; S.flags = null; S.quotes = null; S.extra = ""; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's desk project (records, claims, flags) and its Library folder from your account?")) return;
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
    else if (k === "records") S.records = d;
    else if (k === "find") S.find = d;
    else if (k === "claims") S.claims = d;
    else if (k === "detector_wait") S.wait = d;
    else if (k === "flags") S.flags = d;
    else if (k === "quotes") S.quotes = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the desk project (records, claims, flags) and the Library folder are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — a question about the story, or a quote to check.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "records", "find", "claims", "detector_wait", "flags", "quotes", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.quotes ? "quotes" : "connect");
    subscribe();
  })();
})();

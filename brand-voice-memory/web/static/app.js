/* Brand voice memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Brand memory", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "No memory", sub: "4 briefs × 3 tools, linted", view: "none" },
    { n: 4, title: "Day one", sub: "10 facts, --boundary", view: "day1" },
    { n: 5, title: "A quarter later", sub: "50 facts, same calls", view: "quarter" },
    { n: 6, title: "Voice card", sub: "the rules as one memory", view: "card" },
    { n: 7, title: "Scoreboard", sub: "and tonight's email", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: {}, drafts: {}, rounds: {}, insp: {}, score: null, stream: "", final: null, unavailable: null, summary: null,
    ask: { brief: "", tool: "social", boundary: true, html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const toolLabel = (k) => (S.data?.tools || []).find((t) => t.key === k)?.label || k;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>your own brief</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, none: () => stage("none"), day1: () => stage("day1"), quarter: () => stage("quarter"), card: () => stage("card"), score, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const lintMarks = (v) => v.length ? v.map((x) => `<span class="mark bad">✗ ${esc(x)}</span>`).join("") : `<span class="mark ok">✓ on-brand</span>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Brand memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-bvm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Every AI tool your team writes with, on brand — until the brand memory grows.</h1>
      <p class="lead"><b>Tallyleaf</b> (fictional) writes marketing copy with three AI tools on three APIs. The brand guide lives in MemoryLake, and every tool passes
      the same <b>boundary</b> to MemoryRouter, which attaches the memories most relevant to each brief. A brand linter checks every draft. Watch what happens
      when the memory grows from 10 facts to 50, and the fix. It drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 65 calls on the smallest models per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/brand-voice-memory-for-marketing-teams" target="_blank" rel="noopener">memorylake.ai › brand voice memory for marketing teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function factList(facts, mark) {
    const rules = new Set((S.data?.guide.rules || []).map((r) => r.text).concat([S.data?.card]));
    return `<div class="factlist">${facts.map((f) => `<div class="memfact ${rules.has(f) ? "rule" : ""} ${mark && mark(f) ? "new" : ""}"><span>${esc(f)}</span>${rules.has(f) ? '<span class="when">voice</span>' : ""}</div>`).join("")}</div>`;
  }
  function setup() {
    if (!S.data) return "";
    const facts = S.memory.day1 || [];
    return `<h1>One brand project, one boundary every writing tool passes</h1>
      <p class="lead">Day one: six voice rules, the brand's identity and audience, two product facts — stored verbatim with <code>fact add</code>. The <b>boundary</b>
      (<code>boundary create --projects …</code>) is the only thing each tool adds to its model call.</p>
      <div class="cols"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(S.world?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(S.world?.boundary || "pending")}</b></dd></dl>
        <h3>The three tools</h3>${(S.data.tools || []).map((t) => `<div class="cmdline">router ${esc(t.protocol)} "&lt;brief&gt;" --model ${esc(t.model)} --boundary ${esc(S.world?.boundary || "…")}</div>`).join("")}
        <h3>The linter</h3><p class="muted small">The brand guide as code: no “!”, no emoji, none of the banned words, talks to “you”, emails end with “- The Tallyleaf kitchen”.</p></div>
        <div class="card"><h3 style="margin-top:0">Brand memory on day one <span class="pill">${facts.length} facts</span></h3>${facts.length ? factList(facts) : '<div class="empty">pending</div>'}</div></div>`;
  }

  const STAGE_TEXT = {
    none: ["No memory — the same four briefs through three tools", "No boundary: each tool gets the brief and a one-line system prompt. This is what “AI tools revert to generic copy” looks like, checked by the linter."],
    day1: ["Day one — the same calls with <code>--boundary</code>", "The inspector asks Claude to quote back the memory block MemoryRouter put in front of it; each quoted line is matched to a stored fact. With 10 facts almost everything is attached."],
    quarter: ["One quarter later — the brand memory grew, the rules didn't change", "40 campaign notes, approved quotes, product facts and positioning lines joined the same project. Same boundary, same briefs. MemoryRouter attaches the few memories most relevant to each brief — and the rules are not about any topic."],
    card: ["The fix — one voice card that names every channel", "The six scattered rules are replaced by one fact that is about writing itself: “for every piece of writing (LinkedIn post, tweet, email, … )”. It ranks with any brief, and when it comes it brings every rule."],
  };
  function inspector(key) {
    const rows = S.insp[key] || [];
    if (!rows.length) return "";
    const rules = S.data.guide.rules;
    return `<div class="card"><h3 style="margin-top:0">What each model received <span class="muted small">— as ${esc(S.data.inspector.model)} quoted it back</span></h3>
      <table class="lint"><thead><tr><th>brief</th><th>attached</th>${key === "card" ? "<th>voice card</th>" : rules.map((r) => `<th>${esc(r.label)}</th>`).join("")}</tr></thead><tbody>
      ${rows.map((i) => `<tr><td>${esc(i.task_label)}</td><td>${i.attached.length} of ${i.total}</td>${key === "card"
        ? `<td>${i.card ? '<span class="mark ok">✓ attached</span>' : '<span class="mark bad">✗ not attached</span>'}</td>`
        : rules.map((r) => `<td>${i.rules[r.key] ? '<span class="mark ok">✓</span>' : '<span class="mark bad">✗</span>'}</td>`).join("")}</tr>
        <tr class="detail"><td colspan="${key === "card" ? 3 : rules.length + 2}"><details><summary>the ${i.attached.length} memories</summary>${factList(i.attached)}</details></td></tr>`).join("")}
      </tbody></table></div>`;
  }
  function draftGrid(key) {
    const ds = S.drafts[key] || [];
    if (!ds.length) return "";
    const tasks = S.data.tasks, tools = S.data.tools;
    const cell = (t, tl) => { const d = ds.find((x) => x.task === t.key && x.tool === tl.key); if (!d) return `<td class="muted">…</td>`;
      return `<td><details><summary>${lintMarks(d.violations)}</summary><div class="reply">${esc(d.reply)}</div></details></td>`; };
    const r = S.rounds[key];
    return `<div class="card"><h3 style="margin-top:0">Drafts, linted ${r ? `<span class="pill ${r.bad ? "err" : "ok"}">${r.bad} of ${r.total} off-brand · ${r.violations} violations</span>` : ""}</h3>
      <table class="lint"><thead><tr><th>brief</th>${tools.map((t) => `<th>${esc(t.label)}</th>`).join("")}</tr></thead>
      <tbody>${tasks.map((t) => `<tr><td>${esc(t.label)}</td>${tools.map((tl) => cell(t, tl)).join("")}</tr>`).join("")}</tbody></table>
      <p class="muted small">Click a cell to read the draft.</p></div>`;
  }
  function stage(key) {
    if (!S.drafts[key] && !S.insp[key]) return unavailable() || empty(STAGE_TEXT[key][0]);
    const extra = key === "quarter" && S.memory.quarter ? `<div class="card"><h3 style="margin-top:0">Brand memory now <span class="pill">${S.memory.quarter.length} facts</span></h3>
        <details><summary>show all</summary>${factList(S.memory.quarter, (f) => !(S.memory.day1 || []).includes(f))}</details></div>`
      : key === "card" ? `<div class="card"><h3 style="margin-top:0">The voice card</h3><div class="memfact rule"><span>${esc(S.data.card)}</span></div></div>` : "";
    return `<h1>${STAGE_TEXT[key][0]}</h1><p class="lead">${STAGE_TEXT[key][1]}</p>${extra}${inspector(key)}${draftGrid(key)}`;
  }

  function score() {
    if (!S.score) return unavailable() || empty("The scoreboard");
    const max = 12;
    return `<h1>The scoreboard</h1>
      <p class="lead">Drafts that break the brand guide, out of 12 (4 briefs × 3 tools), at each point of the story. Model wording varies run to run; the README reports totals over all runs.</p>
      <div class="card"><div class="bars">${S.score.rows.map((r) => `<div class="barrow"><span>${esc(r.label)}</span><div class="bar ${r.bad ? "alt" : ""}" style="width:${Math.max(1, Math.round(100 * (r.bad || 0) / max))}%"></div><span class="n">${r.bad ?? "—"} / ${r.total ?? "—"}</span></div>`).join("")}</div></div>
      <div class="card"><h3 style="margin-top:0">Tonight's launch email — <code>router responses --stream</code></h3><p class="muted small">“${esc(S.data.final_brief)}”</p>
        <div class="reply">${esc(S.final?.reply || S.stream)}${S.final ? "" : '<span class="spinner"></span>'}</div>
        ${S.final ? `<div class="marks">${lintMarks(S.final.violations)}</div>` : ""}</div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${S.summary.tokens.prompt} prompt + ${S.summary.tokens.completion} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    const tools = S.data?.tools || [];
    return `<h1>Ask — your own brief</h1>
      <p class="lead">Write any brief, pick a tool, with or without the brand memory. The draft is linted; <b>Inspect</b> shows what the model received for it.</p>
      <div class="card"><div class="userpick">${tools.map((t) => `<button type="button" class="chip ${S.ask.tool === t.key ? "on" : ""}" data-tool="${t.key}">${esc(t.label)}</button>`).join("")}
        <span style="width:16px"></span><button type="button" class="chip ${S.ask.boundary ? "on" : ""}" data-b="1">brand memory</button><button type="button" class="chip ${S.ask.boundary ? "" : "on"}" data-b="0">no memory</button></div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Write a blog intro about tip pooling reports." value="${esc(S.ask.brief)}" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Write</button><button type="button" class="btn" id="btn-inspect" ${ready ? "" : "disabled"}>Inspect</button></form>
        <div class="chips">${["Write a blog intro about tip pooling reports.", "Write an Instagram caption for card fee breakdowns.", "Write a push notification about weekly cash-flow forecasts.", "Write a landing page headline for Tallyleaf."].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    st.querySelectorAll(".userpick .chip[data-tool]").forEach((c) => c.addEventListener("click", () => { S.ask.brief = $("#ask-q")?.value || S.ask.brief; S.ask.tool = c.dataset.tool; renderStage(); }));
    st.querySelectorAll(".userpick .chip[data-b]").forEach((c) => c.addEventListener("click", () => { S.ask.brief = $("#ask-q")?.value || S.ask.brief; S.ask.boundary = c.dataset.b === "1"; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askBrief($("#ask-q").value.trim()); });
      $("#btn-inspect").addEventListener("click", () => inspectBrief($("#ask-q").value.trim()));
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askBrief(c.dataset.q); }));
    }
  }
  function setResult(html) { S.ask.html = html; const box = $("#ask-result"); if (box) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askBrief(q) {
    if (!q) return; S.ask.brief = q;
    $("#ask-result").innerHTML = `<span class="pill busy"><span class="spinner"></span> writing with ${esc(toolLabel(S.ask.tool))}…</span>`;
    const r = await api("/api/ask", { brief: q, tool: S.ask.tool, boundary: S.ask.boundary });
    if (!r) { $("#ask-result") && ($("#ask-result").innerHTML = ""); return; }
    setResult(`<div class="callhead"><h3 style="margin:0">${esc(r.tool_label)} · ${r.boundary ? "brand memory" : "no memory"}</h3><span class="tok">${r.prompt_tokens} prompt tokens</span></div>
      <div class="reply">${esc(r.reply)}</div><div class="marks">${lintMarks(r.violations)}</div>`);
  }
  async function inspectBrief(q) {
    if (!q) return; S.ask.brief = q;
    $("#ask-result").innerHTML = `<span class="pill busy"><span class="spinner"></span> asking ${esc(S.data.inspector.model)} what it received…</span>`;
    const i = await api("/api/inspect", { brief: q });
    if (!i) { $("#ask-result") && ($("#ask-result").innerHTML = ""); return; }
    const rules = S.data.guide.rules;
    const ruleMarks = i.card ? '<span class="mark ok">✓ voice card attached</span>'
      : Object.values(i.rules).some(Boolean) || !S.memory.card ? rules.map((r) => `<span class="mark ${i.rules[r.key] ? "ok" : "bad"}">${i.rules[r.key] ? "✓" : "✗"} ${esc(r.label)}</span>`).join("")
      : '<span class="mark bad">✗ voice card not attached</span>';
    setResult(`<h3>${i.attached.length} of ${i.total} memories attached</h3><div class="marks">${ruleMarks}</div>${factList(i.attached)}`);
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
  function resetView() { S.memory = {}; S.drafts = {}; S.rounds = {}; S.insp = {}; S.score = null; S.stream = ""; S.final = null; S.unavailable = null; S.summary = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's brand project (and every fact in it) and its boundary?")) return;
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
    else if (k === "memory") S.memory[d.stage] = d.facts;
    else if (k === "inspect") (S.insp[d.stage] ||= []).push(d);
    else if (k === "draft") (S.drafts[d.stage] ||= []).push(d);
    else if (k === "round") S.rounds[d.stage] = d;
    else if (k === "scoreboard") S.score = d;
    else if (k === "stream") { S.stream += d.chunk; if (S.caughtUp && S.view === "score") { const r = document.querySelector("#stage .reply"); if (r) { r.textContent = S.stream; return; } } }
    else if (k === "final") S.final = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the brand project, its facts and its boundary are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: your own brief, any tool, with or without the brand memory.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "stream"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "inspect", "draft", "round", "scoreboard", "stream", "final", "summary", "router_unavailable", "done", "error"];
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

/* Research figure memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, three boundaries", view: "setup" },
    { n: 3, title: "What the team keeps", sub: "two survey figures", view: "memory" },
    { n: 4, title: "Figures in the index", sub: "what is kept from each file", view: "index" },
    { n: 5, title: "PNG", sub: "values printed on every bar", view: "screenshots" },
    { n: 6, title: "PNG in a PDF", sub: "the same file, wrapped", view: "pdf" },
    { n: 7, title: "No labels, PDF", sub: "wrapped the same way", view: "unlabelled" },
    { n: 8, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "A", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["screenshots", "pdf", "unlabelled"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { wrong: ["✗", "bad"], partial: ["◐", "bad"], right: ["✓", "ok"], none: ["·", ""] };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "figure" ? "active" : ""}" data-view="figure"><span class="n">#</span><span class="t">Figure<small>one chart, three files</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, screenshots: () => arm("screenshots"), pdf: () => arm("pdf"), unlabelled: () => arm("unlabelled"), score, figure: figureTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const entry = (e) => `<span class="${e.found.length < e.total ? "bad" : ""}">${esc(e.short)} — ${e.found.length}/${e.total} values${e.found.length && e.found.length < e.total ? ` (${esc(e.found.join(", "))})` : ""}</span>`;
  const armLine = (t) => t ? `${t.right}/${t.answers} right · ${t.wrong} wrong · ${t.partial} incomplete · ${t.none} no figure` : "";
  const chkLine = (t) => t ? `checker: the asked figures were in what MemoryRouter attached ${t.checks.asked_values}/${t.checks.readable}${t.checks.declined ? ` (${t.checks.declined} declined to copy)` : ""}` : "";
  const figCard = (v) => `<div class="card ${v.found.length === v.total ? "" : "warnc"}"><h3 style="margin-top:0">figure ${esc(v.fig)} · ${esc(armShort(v.arm))} <span class="mono small muted">${esc(v.source_type)}</span> <span class="pill ${v.found.length === v.total ? "ok" : "err"}">${v.found.length} of ${v.total} values</span></h3>
      <p class="small"><b>document summary</b> ${esc(v.summary || "(none)")}</p>
      ${v.figure_items ? `<p class="small"><b>figure item, text read off the chart</b></p><pre class="mono small wrap figtext">${esc(v.figure_text || "(empty)")}</pre>` : ""}
      ${v.figure_summary && v.figure_summary !== v.summary ? `<p class="small"><b>figure item, summary</b> ${esc(v.figure_summary)}</p>` : ""}
      ${(v.page || []).map((t) => `<p class="small"><b>text on the page</b> <code>${esc(t)}</code></p>`).join("")}
      ${v.found.length && v.found.length < v.total ? `<p class="small muted">values in this text: ${esc(v.found.join(", "))}</p>` : ""}</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.pdf)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-rfm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Same chart, two file types. Only one of them gives MemoryRouter the numbers.</h1>
      <p class="lead">The energy research team at the Fernhill Institute (fictional) keeps its heat-pump survey results as bar charts with the value printed on every bar, and saves
      them into shared memory so every researcher's assistant can answer from them. The assistant runs on MemoryRouter: every question is an ordinary model call with
      <code>--boundary</code> added. This demo stores the same two charts three ways — as PNG screenshots, as the same PNG files wrapped in a one-page PDF, and unlabelled in a PDF —
      asks six questions through each boundary and grades every answer against the survey data. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 72 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-research-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for research teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, three boundaries</h1>
      <p class="lead">The same two survey figures, kept three ways, one project each, one boundary over each. The assistant never assembles memory itself; MemoryRouter picks what to
      attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armLabel(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The team's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What the team keeps") : empty("What the team keeps");
    const d = S.data || {};
    const figs = d.figures || [];
    const w = S.memory.wrapped || [];
    return `<h1>${esc(d.study)} — two result figures</h1>
      <p class="lead">Every bar has its value printed on it. The team saves the charts as PNG screenshots (project “${esc(armShort("screenshots"))}”). Project “${esc(armShort("pdf"))}” holds
      the very same PNG files, each wrapped in a one-page PDF by <code>wrap_png_as_pdf()</code> in demo.py — the image bytes go in unchanged, no redraw, no text layer. Project
      “${esc(armShort("unlabelled"))}” holds the charts without printed values, wrapped the same way. All go through <code>lib upload</code> + <code>proj doc import --wait</code>.</p>
      <div class="grid2">${figs.map((f) => `<div class="card"><h3 style="margin-top:0">figure ${esc(f.id)}</h3><img class="chartimg" src="/data/${esc(f.file)}.png" alt="${esc(f.title)}"></div>`).join("")}</div>
      <div class="card"><table class="lint plain"><thead><tr><th>PNG</th><th>bytes</th><th>wrapped PDF</th><th>bytes</th></tr></thead><tbody>
        ${w.map((m) => `<tr><td class="mono">${esc(m.png)}</td><td>${fmt(m.png_bytes)}</td><td class="mono">${esc(m.pdf)}</td><td>${fmt(m.pdf_bytes)}</td></tr>`).join("")}
      </tbody></table><p class="muted small">A PDF wrapper adds under a kilobyte: the PNG's compressed image data is the PDF's image stream.</p></div>
      <div class="card"><table class="lint plain"><thead><tr><th>figure A</th>${(figs[0]?.series || []).map((x) => `<th>${esc(x)} (%)</th>`).join("")}<th></th><th>figure B</th><th>${esc(figs[1]?.series?.[0] || "")} (%)</th></tr></thead><tbody>
        ${Array.from({ length: Math.max(Object.keys(figs[0]?.values || {}).length, Object.keys(figs[1]?.values || {}).length) }, (_, i) => {
          const a = Object.entries(figs[0]?.values || {})[i], b = Object.entries(figs[1]?.values || {})[i];
          return `<tr><td>${a ? esc(a[0]) : ""}</td>${(figs[0]?.series || []).map((_, k) => `<td>${a ? a[1][k] : ""}</td>`).join("")}<td></td><td>${b ? esc(b[0]) : ""}</td><td>${b ? b[1][0] : ""}</td></tr>`;
        }).join("")}
      </tbody></table><p class="muted small">The survey data — exactly what is printed on the bars. Every value is different, so a number in an answer names exactly one bar.</p></div>`;
  }

  function index() {
    const h = "The figures in the index — what MemoryLake keeps from each file";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const f = S.index.figures || [];
    const per = S.index.per || {};
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns, for each file, everything the index holds: the document summary, the figure item's text and summary, and the
      text on the page. This text is what MemoryRouter attaches for a figure. Values printed on the charts that are anywhere in it:
      ${ARMS.map((a) => `<b>${esc(armShort(a))}</b> ${per[a]?.found ?? "–"} of ${per[a]?.total ?? "–"}`).join(" · ")}.</p>
      ${ARMS.map((a) => `<h2>${esc(armLabel(a))}</h2><div class="grid2">${f.filter((v) => v.arm === a).map(figCard).join("")}</div>`).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armLabel(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const order = { wrong: 0, partial: 1, none: 2, right: 3 };
    return `<h1>${h}${t ? ` — ${t.right}/${t.answers} right` : ""}</h1>
      <p class="lead">Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what it was given, so you can see
      which files MemoryRouter attached and how many of the figure's values their text held. ✓ right · ✗ wrong · ◐ incomplete · · no figure.</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => order[a.verdict] - order[b.verdict])[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">survey data: <b>${esc(r.truth)}</b></div>
          <div class="small">checker: ${r.entries?.length ? r.entries.map(entry).join(" · ") : `<code>${esc(r.attached || "—")}</code>`}</div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict === "wrong" || pick.verdict === "partial" ? "wrong" : ""}"><span class="who">answer ${mark(pick.verdict)}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}<br>${chkLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {}, per = S.summary.index || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>values in the index</th><th>right</th><th>wrong</th><th>incomplete</th><th>no figure</th><th>asked figures attached</th></tr></thead><tbody>
        ${ARMS.map((k) => `<tr><td><b>${esc(armLabel(k))}</b></td><td>${per[k] ? `${per[k].found}/${per[k].total}` : "–"}</td>${t[k] ? `<td>${t[k].right}/${t[k].answers}</td><td>${t[k].wrong}</td><td>${t[k].partial}</td><td>${t[k].none}</td><td>${t[k].checks.asked_values}/${t[k].checks.readable}</td>` : "<td>–</td><td></td><td></td><td></td><td></td>"}</tr>`).join("")}
      </tbody></table></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant said instead of the right answer</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      What MemoryRouter attaches, and what a summary says, vary from run to run; the README reports totals over all runs.</p>`;
  }

  function figureTool() {
    const ready = !!S.world, figs = S.data?.figures || [];
    const last = S.lookups[S.lookups.length - 1];
    return `<h1>Figure</h1>
      <p class="lead">One survey figure as the index holds it in each of the three projects — PNG, PNG in a PDF, unlabelled in a PDF — next to the survey data. Read-only: searches, no model call.</p>
      <div class="card"><div class="row">${figs.map((f) => `<button class="chip ${S.pick === f.id ? "on" : ""}" data-pick="${esc(f.id)}">figure ${esc(f.id)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="figure" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
        ${last ? `<div class="card"><h3 style="margin-top:0">figure ${esc(last.figure)} — ${esc(last.title)}</h3><p class="small mono">${Object.entries(last.values).map(([c, v]) => `${esc(c)} ${v.join(" / ")}`).join(" · ")}</p></div>
          <div class="grid2">${(last.views || []).map(figCard).join("")}</div>` : ""}`;
  }

  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo());
    st.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => { S.pick = b.dataset.pick; renderStage(); }));
    st.querySelector('[data-act="figure"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/figure", { figure: S.pick });
      if (r) S.lookups.push(r);
      if (S.view === "figure") renderStage();
    });
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
    ["#btn-run", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.memory = null; S.index = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, the three projects and the 6 files?")) return;
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
    else if (k === "memory") S.memory = d;
    else if (k === "index") S.index = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, the three projects and the 6 files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Figure: one chart as the index holds it in each of the three projects.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "index", "asked", "tally", "summary", "router_unavailable", "figure", "done", "error"];
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
    show(state.status === "running" ? S.view : S.summary ? "score" : "connect");
    subscribe();
  })();
})();

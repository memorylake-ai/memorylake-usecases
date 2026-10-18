/* Edited page memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "First sync", sub: "meeting notes + decisions page", view: "first" },
    { n: 4, title: "The edit", sub: "the decisions page changes", view: "edit" },
    { n: 5, title: "Ask both models", sub: "ChatGPT and Claude, same memory", view: "answers" },
    { n: 6, title: "Score", sub: "per model, per way of writing", view: "score" },
  ];
  const fresh = () => ({ first: null, edit: null, answers: {}, highlight: null, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "How many accounts are in the pilot cohort?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const TOP = () => S.data?.topics || [];
  const MODELS = () => S.data?.models || ["gpt-4o-mini", "claude-haiku-4-5"];
  const MNAME = (m) => (S.data?.model_names || {})[m] || m;
  const LABEL = (a) => (S.data?.arms || {})[a] || a;
  const top = (id) => TOP().find((t) => t.id === id) || {};
  const DEC = () => S.data?.pages?.decisions || {};

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>A, B and C on both models</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, first: firstView, edit: editView, answers: answersView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the edited value"], stale: ["↺", "bad", "the older note's value"], unknown: ["?", "warn", "Unknown"], other: ["–", "warn", "neither value"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.text)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (160 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-epg")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The same synced memory, read by ChatGPT and Claude. The edited page loses to an older dated note.</h1>
      <p class="lead">${esc(d.org || "Larkfield Analytics")} (a fictional research team) syncs its workspace into MemoryLake, one fact per page block, so both models start from what the team wrote down.
      Meeting notes carry their date in the title; the living <b>${esc(DEC().title || "Pilot study decisions")}</b> page does not — the editor keeps “last edited” as a page property.
      On ${esc(DEC().v2_edited || "12 October 2026")} six lines of that page change, and the sync updates those blocks in place. Three ways to write the edit: <b>A</b> the page text only · <b>B</b> plus the page's last-edited date · <b>C</b> plus what each block changed from. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 160 calls per run, half on gpt-4o-mini, half on claude-haiku-4-5.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-beyond-notion-ai" target="_blank" rel="noopener">memorylake.ai › AI memory beyond Notion AI</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, one boundary each</h1>
      <p class="lead">Each way of writing the edited page goes into its own project, read through its own boundary — so both models read exactly one version of the team's memory at a time. All three projects carry the same plain name.</p>
      <div class="card"><dl class="kv">${ARMS.map((a, i) => `<dt>${esc(LABEL(a))}</dt><dd class="mono small">${esc(w?.projects?.[["text", "dated", "diffed"][i]] || "pending")}
        <span class="muted">· boundary ${esc(w?.boundaries?.[["text", "dated", "diffed"][i]] || "pending")}</span></dd>`).join("")}</dl></div>`;
  }

  function firstView() {
    if (!S.first) return running() ? waiting("First sync") : empty("First sync");
    const pages = S.data?.pages || {};
    const page = (key) => `<div class="card"><h3 style="margin-top:0">📄 ${esc(pages[key].title)} <span class="lab">${esc(pages[key].kind)}</span></h3>
      ${S.first.notes.filter((n) => n.page === key).map((n) => `<div class="copyrow small"><span class="k">${esc(top(n.id).label)}</span><span>${esc(n.fact)}</span></div>`).join("")}</div>`;
    return `<h1>The first sync — what both models will read</h1>
      <p class="lead">Each block becomes one fact (<code>fact add</code>), the same in all three projects. A meeting-notes page carries its date in its title, so its facts do too. The decisions page says what is decided now; its “last edited ${esc(DEC().v1_edited)}” lives in a page property, not in the text.</p>
      ${page("sync1")}${page("sync2")}
      <div class="card"><h3 style="margin-top:0">📄 ${esc(DEC().title)} <span class="lab">living page · v1</span></h3>
      ${S.first.blocks.map((b) => `<div class="copyrow small"><span class="k">${esc(top(b.id).label)}</span><span>${esc(b.fact)}</span></div>`).join("")}
      <p class="small muted" style="margin-bottom:0">The sync keeps block → fact id in <code>out/sync-state.json</code>, so the next sync can update each block in place.</p></div>`;
  }

  function editView() {
    if (!S.edit) return running() ? waiting("The edit") : empty("The edit");
    const ch = TOP().filter((t) => t.kind === "change"), p = S.edit.pair || {};
    return `<h1>${esc(DEC().v2_edited)}: six lines of the decisions page change</h1>
      <p class="lead">The sync writes each edited block into the fact it wrote last time — <code>fact update --text</code>, same fact id. The meeting notes are a different page and stay: they were true when they were written. Only the wording of the edited block differs between the three projects.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>block</th><th>v1 (September)</th><th>v2 (October)</th></tr></thead><tbody>
      ${ch.map((t) => `<tr><td class="small"><b>${esc(t.label)}</b></td><td class="small muted">${esc(t.v1)}</td><td class="small"><b>${esc(t.v2)}</b></td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">How each project writes the edited block</h3>
      ${ARMS.map((a) => `<div class="copyrow small"><span class="k">${esc(LABEL(a))}</span><span class="mono">${esc((S.edit.rows.find((r) => r.arm === a && r.id === "cohort") || {}).fact)}</span></div>`).join("")}
      <p class="small muted" style="margin-bottom:0">B writes the page's last-edited date into every block of the page (8 updates — it is a page property); A and C update the 6 blocks whose text changed. C diffs each block against the text it wrote last time.</p></div>
      <div class="card"><h3 style="margin-top:0"><code>fact list</code> — the pilot cohort, in each project</h3><table class="lint plain"><thead><tr><th></th><th>older note (meeting, 14 September)</th><th>edited page</th></tr></thead><tbody>
      ${ARMS.map((a) => `<tr><td class="small"><b>${a}</b></td><td class="small">${esc(p[a]?.note)}</td><td class="small">${esc(p[a]?.page)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function cellMarks(rows, id) { return rows.filter((r) => r.item === id).map(markOf).join(""); }
  function answersView() {
    const any = Object.keys(S.answers).length;
    if (!any) return unavailable() || (running() ? waiting("Ask both models") : empty("Ask both models"));
    const h = S.highlight;
    return `<h1>The same eight questions, through the same memory</h1>
      <p class="lead">Each question ${RUNS()} times per model and project (temperature 0), with the system prompt <code>${esc(S.data?.system)}</code>. ✓ the edited value · ↺ the older note's value · ∅ MemoryRouter attached nothing. The last two questions did not change.</p>
      ${MODELS().map((m) => `<div class="card"><h3 style="margin-top:0">${esc(MNAME(m))}</h3><table class="lint plain"><thead><tr><th></th>${TOP().map((t) => `<th class="small">${esc(t.label)}${t.kind === "control" ? '<br><span class="muted">control</span>' : ""}</th>`).join("")}<th class="num">edited value</th></tr></thead><tbody>
        ${ARMS.map((a) => { const x = S.answers[`${m}|${a}`]; if (!x) return `<tr><td class="small"><b>${esc(LABEL(a))}</b></td><td colspan="${TOP().length + 1}" class="muted small">${running() ? "asking…" : ""}</td></tr>`;
          return `<tr class="${x.tally.stale ? "hl" : ""}"><td class="small"><b>${esc(LABEL(a))}</b></td>${TOP().map((t) => `<td><div class="cell-ans">${cellMarks(x.rows, t.id)}</div></td>`).join("")}
          <td class="num"><b>${x.tally.ok}/${x.tally.n}</b>${x.tally.stale ? ` <span class="bad-ink">↺ ${x.tally.stale}</span>` : ""}</td></tr>`; }).join("")}</tbody></table></div>`).join("")}
      ${h ? `<div class="card"><h3 style="margin-top:0">One question, six readers — “${esc(top(h.item).ask)}”</h3><p class="small muted">edited page: <b>${esc(top(h.item).current)}</b> · older note: ${esc(top(h.item).stale || "(unchanged)")}. Where a model gave the older value at least once, that read is shown.</p>
        <div class="chat">${h.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : "wrong"}"><span class="who">${esc(MNAME(r.model))} · ${esc(LABEL(r.arm))} · ${markOf(r)} ${esc(r.empty ? "nothing attached" : MARKS[r.grade][2])}</span>${esc(r.reply)}</div>`).join("")}</div></div>` : ""}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const by = S.score.by, bi = S.score.by_item || {};
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>The six changed answers — ${MODELS().map((m) => `${esc(m)}: ${ARMS.map((a) => `${a} ${by[m][a].change_ok}/${by[m][a].change_n}`).join(" · ")}`).join("  |  ")}</h1>
      <p class="lead">The same memory went to both models. An undated edit loses to the older dated note on both; the last-edited date is how a reader can tell which is newer — whether it helps depends on the model; naming what the block changed from leaves nothing to infer.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>project</th>${MODELS().map((m) => `<th>${esc(MNAME(m))}</th>`).join("")}</tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>${MODELS().map((m) => { const x = by[m][a];
          return `<td><b>${x.change_ok}/${x.change_n}</b> <span class="muted">${pct(x.change_ok, x.change_n)}%</span>${x.change_stale ? ` <b class="bad-ink">↺ ${x.change_stale}</b>` : ""}${bar(x.change_ok, x.change_n, "g1")}<span class="small muted">controls ${x.control_ok}/${x.control_n}${x.empty ? ` · ∅ ${x.empty}` : ""}</span></td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${MODELS().map((m) => ARMS.map((a) => `<th class="num small">${esc(m.split("-")[0])} ${a}</th>`).join("")).join("")}</tr></thead><tbody>
        ${TOP().map((t) => `<tr><td class="small">${esc(t.label)} <span class="muted">= ${esc(t.current)}${t.kind === "control" ? " · control" : ""}</span></td>${MODELS().map((m) => ARMS.map((a) => `<td class="num small">${esc((bi[t.id]?.[m]?.[a] || {}).marks || "")}</td>`).join("")).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ the edited value · ↺ the older note's value · ? Unknown · – neither · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C, on both models</h1>
      <p class="lead">Any question to the research assistant, once through each project's memory on each model. Graded when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${TOP().map((t) => `<button class="chip" data-q="${esc(t.ask)}">${esc(t.label)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">You → the research assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(MNAME(r.model))} · ${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.current ? `<p class="small muted">edited page: ${esc(x.current)}</p>` : ""}`).join("")}</div></div>`;
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
    st.querySelector("#t-q")?.addEventListener("input", (e) => { S.tq = e.target.value; });
    st.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { S.tq = b.dataset.q; renderStage(); }));
    st.querySelector('[data-act="try"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Asking…";
      const r = await api("/api/ask", { question: S.tq });
      if (r) S.tries.push({ question: S.tq, rows: r.rows, current: r.current });
      if (S.view === "try") renderStage();
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
  function resetView() { Object.assign(S, fresh()); }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's three boundaries, every fact in its three projects, and the projects?")) return;
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
    else if (k === "first_sync") S.first = d;
    else if (k === "second_sync") S.edit = d;
    else if (k === "answers") S.answers[`${d.model}|${d.arm}`] = d;
    else if (k === "highlight") S.highlight = d;
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the three boundaries, every fact and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question, through A, B and C on both models.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "first_sync", "second_sync", "answers", "highlight",
    "score", "summary", "router_unavailable", "asked", "memory", "done", "error"];
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

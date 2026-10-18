/* First token memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Memory", sub: "8 facts about Priya", view: "setup" },
    { n: 3, title: "A · retrieve, then stream", sub: "MemoryRouter looks it up first", view: "A" },
    { n: 4, title: "B · 1 s deadline", sub: "miss it, stream without memory", view: "B" },
    { n: 5, title: "C · prefetch", sub: "load the profile when the chat opens", view: "C" },
    { n: 6, title: "Score", sub: "fast, and right?", view: "score" },
  ];
  const ARMS = ["A", "B", "C"];
  const fresh = () => ({ setup: null, arms: { A: {}, B: {}, C: {} }, prefetch: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const f2 = (x) => (x == null ? "—" : Number(x).toFixed(2));
  const MARK = { used: ["ok", "✓ used memory"], contradicts: ["err", "✗ contradicts memory"], generic: ["", "– generic, no memory"] };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const keep = {};
    document.querySelectorAll("pre.answer").forEach((p) => { keep[p.dataset.k] = p.scrollTop; });
    const r = { connect, setup: setupView, A: () => armView("A"), B: () => armView("B"), C: () => armView("C"), score: scoreView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
    document.querySelectorAll("pre.answer").forEach((p) => { p.scrollTop = p.dataset.live ? p.scrollHeight : (keep[p.dataset.k] || 0); });
  }

  function connect() {
    const st = S.st, d = D();
    const w = S.world || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Priya's actor</dt><dd>${w.actor ? `<span class="mono">${esc(w.actor)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${w.actor ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 70 seconds · 18 short model answers · everything it creates is prefixed <code>${esc(d.prefix || "mlu-ftk")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Stream the answer fast. Don't drop the memory it needs.</h1>
      <p class="lead">${esc(d.company || "Tallow Labs")} (fictional) puts a coding assistant, ${esc(d.assistant || "Quill")}, in Priya's editor. Answers stream, and the rule is a first token within ${f2(d.budget_secs || 2.5)} s.
      Six everyday questions go through three ways of getting Priya's memory into the answer: <b>A</b> let MemoryRouter look it up, then stream; <b>B</b> give <code>search</code> a ${esc(d.deadline_secs || 1)} s deadline and stream without memory when it misses;
      <b>C</b> load her whole profile once, when the chat opens. It drives the <code>memorylake</code> CLI for real, and times every first token.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH.</li>
          <li><b>Model calls:</b> 18 short gpt-4o-mini answers through MemoryRouter, billed to your MemoryLake balance.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-streaming-agent-responses" target="_blank" rel="noopener">memorylake.ai › Memory for streaming agent responses</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const s = S.setup;
    if (!s) return running() ? waiting("Memory") : empty("Memory");
    return `<h1>Memory: 8 facts about Priya, and a boundary for MemoryRouter</h1>
      <p class="lead">Each fact is pinned on Priya's actor (<code>fact add --actor</code>). The boundary (<code>boundary create --human-actor</code>) is what arm A hands MemoryRouter.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>key</th><th>fact</th></tr></thead><tbody>
        ${s.facts.map((f) => `<tr><td class="mono"><b>${esc(f.key)}</b></td><td>${esc(f.text)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function bar(first, budget) {
    const max = 6, w = Math.min(100, (first / max) * 100), b = (budget / max) * 100;
    return `<div class="ttft"><div class="fill ${first > budget ? "over" : ""}" style="width:${w}%"></div><div class="budget" style="left:${b}%"></div></div>`;
  }

  function qcard(arm, q) {
    const x = S.arms[arm][q.id];
    if (!x) return `<div class="card qcard"><h3>${esc(q.id)} · ${esc(q.short)}</h3><p class="ask">“${esc(q.ask)}”</p><span class="muted small">waiting</span></div>`;
    const budget = D().budget_secs || 2.5;
    const done = !!x.verdict;
    const m = MARK[x.verdict] || ["busy", "streaming…"];
    const late = x.late ? `<p class="late">${x.late.held ? `The search came back at <b>${f2(x.late.secs)} s</b>, ${esc(x.late.when || "")}, holding “${esc(x.late.fact)}” — discarded.` : `The search came back at ${f2(x.late.secs)} s without the fact.`}</p>` : "";
    return `<div class="card qcard"><h3>${esc(q.id)} · ${esc(q.short)}</h3><p class="ask">“${esc(q.ask)}”</p>
      <pre class="answer" data-k="${arm}${q.id}" ${done ? "" : 'data-live="1"'}>${esc(x.answer || "")}</pre>
      ${x.first != null ? bar(x.first, budget) : '<div class="ttft"></div>'}
      <div class="ttft-legend"><span>first token <b class="${x.first > budget ? "bad-ink" : "ok-ink"}">${f2(x.first)} s</b>${done ? ` · done ${f2(x.done)} s` : ""}</span><span>budget ${f2(budget)} s</span></div>
      <div style="margin-top:8px"><span class="pill ${m[0]}">${m[1]}</span>${done && x.verdict !== "used" ? ` <span class="muted small">memory says ${esc(q.expect_label)}</span>` : ""}</div>${late}</div>`;
  }

  function armView(arm) {
    const d = D(), st = d.arms || {};
    const any = Object.keys(S.arms[arm]).length || (arm === "C" && S.prefetch);
    const title = { A: "A · Retrieve, then stream", B: "B · A 1 s deadline on retrieval", C: "C · Prefetch the profile when the chat opens" }[arm];
    if (!any) return running() ? waiting(title) : empty(title);
    const pre = arm === "C" && S.prefetch ? `<div class="card prefetch">The chat opens: <code>fact list --actors</code> loads <b>${S.prefetch.facts.length} facts in ${f2(S.prefetch.secs)} s</b>, while Priya is still typing — paid once, before any question.</div>` : "";
    return `<h1>${title}</h1><p class="lead">${code(st[arm]?.how)}</p>${pre}
      <div class="qgrid">${(d.questions || []).map((q) => qcard(arm, q)).join("")}</div>`;
  }

  function scoreView() {
    const sc = S.score, st = D().arms || {};
    if (!sc) return running() ? waiting("Score") : empty("Score");
    const r = sc.rows, n = sc.n;
    return `<h1>Fast and right: only the prefetch is both</h1>
      <p class="lead">First token measured from the question to the first character on screen (CLI call included). “Used memory” = the answer names what Priya's memory says (pnpm, Vitest, Fly.io, Drizzle, Hono, Node 20).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>way</th><th>first token: median · max</th><th>within ${f2(sc.budget)} s</th><th>used memory</th><th>contradicted it</th></tr></thead><tbody>
      ${ARMS.map((a) => { const y = r[a]; return `<tr><td><b>${esc(st[a]?.short)}</b></td><td>${f2(y.median)} s · ${f2(y.max)} s</td><td class="${y.within < n ? "bad-ink" : ""}"><b>${y.within}/${n}</b></td><td class="${y.used < n ? "bad-ink" : ""}"><b>${y.used}/${n}</b></td><td class="${y.contradicts ? "bad-ink" : ""}">${y.contradicts}/${n}</td></tr>`; }).join("")}
      </tbody></table></div>
      <div class="card"><ul class="steps-list">
        <li><b>A</b> uses the memory every time it can — and makes Priya wait for MemoryRouter's lookup before the first token.</li>
        <li><b>B</b> looks fine on a latency dashboard. It missed its ${esc(sc.deadline)} s deadline on <b>${r.B.missed}/${n}</b> questions; the search came back holding the needed fact <b>${r.B.late_held}</b> times, and nothing raised an error. Its answers are the no-memory answers.</li>
        <li><b>C</b> paid ${f2(r.C.prefetch)} s once, when the chat opened. Every answer after that streams as fast as no memory at all — with it. It fits a per-user profile of tens of facts; a large store still needs a lookup per question.</li></ul></div>`;
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
    if (!confirm("Delete the demo's boundary, actor and her facts?")) return;
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
  const ent = (arm, qid) => (S.arms[arm][qid] = S.arms[arm][qid] || { answer: "" });
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) { resetView(); S.world = null; } term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = { ...(S.world || {}), ...d };
    else if (k === "setup") { S.setup = d; S.world = { ...(S.world || {}), actor: d.actor, boundary: d.boundary }; }
    else if (k === "asked") { if (S.arms[d.arm]) S.arms[d.arm][d.qid] = { answer: "" }; }
    else if (k === "first") { if (S.arms[d.arm]) ent(d.arm, d.qid).first = d.secs; }
    else if (k === "stream") { if (S.arms[d.arm]) ent(d.arm, d.qid).answer += d.chunk; }
    else if (k === "answered") { if (S.arms[d.arm]) S.arms[d.arm][d.qid] = { ...d }; }
    else if (k === "prefetch") S.prefetch = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's boundary, actor and facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The three ways are scored side by side under step 6.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "asked", "first", "stream", "answered", "prefetch", "score", "done", "error"];
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

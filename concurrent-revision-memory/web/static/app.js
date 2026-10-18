/* Concurrent revision memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "The team", sub: "one project, five actors", view: "setup" },
    { n: 3, title: "Kickoff", sub: "Monday · Planner agent", view: "kickoff" },
    { n: 4, title: "Three reports", sub: "Wednesday · same minute", view: "reports" },
    { n: 5, title: "What memory says", sub: "facts · conflicts", view: "memory" },
    { n: 6, title: "Audit", sub: "fact trace · who saw what", view: "audit" },
    { n: 7, title: "The lead decides", sub: "on the record · audit again", view: "decide" },
  ];
  const FIELDS = ["date", "owner"];
  const fresh = () => ({ kickoff: null, reported: [], cooked: {}, reports: null, memory: [], watch: [], audit: [], outcomes: null,
    verdict: {}, decision: null, result: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const hhmmss = (iso) => (iso || "").slice(11, 19);
  const person = (k) => D().people?.[k]?.display || k;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, kickoff: kickoffView, reports: reportsView, memory: memoryView, audit: auditView, decide: decideView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes (two rounds of extraction, then ${esc(d.window || 90)} s watching for conflicts) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-crv")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Give a multi-agent system a shared memory backend it can trust</h1>
      <p class="lead">${esc(d.company || "Corvane Labs")} (fictional) is launching Halyard 2.0. A Planner agent recorded the launch review:
      Thursday, October 22, owned by Ben Ito. Two days later three agents report changes to shared memory in the same minute —
      two of them change the date, to different days. What does memory keep, and who decided? It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH (<code>fact trace</code>, <code>fact conflict</code>).</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-backend-for-multi-agent-systems" target="_blank" rel="noopener">memorylake.ai › Memory backend for multi-agent systems</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = D(), w = S.world;
    if (!d.people) return empty("The team");
    return `<h1>One shared project, an engineering lead and four agents</h1>
      <p class="lead">Every conversation is between Mara and one agent, in the project “${esc(d.project_name)}”. Facts extracted from all of them land in the same shared memory.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>actor</th><th>role</th><th>id</th></tr></thead><tbody>
        ${Object.entries(d.people).map(([k, p]) => `<tr><td><b>${esc(p.display)}</b></td><td>${esc(p.role)}</td><td class="mono small">${esc(w?.actors?.[k] || "pending")}</td></tr>`).join("")}
      </tbody></table><p class="small muted" style="margin-bottom:0">project <span class="mono">${esc(w?.project || "pending")}</span></p></div>`;
  }

  function factList(facts) {
    return facts.map((f) => `<div class="memfact"><span class="pill">${esc((f.fields || []).join("/") || "other")}</span> ${esc(f.text)}</div>`).join("");
  }

  function kickoffView() {
    const d = D(), k = S.kickoff;
    if (!k) return running() ? waiting("Kickoff") : empty("Kickoff");
    return `<h1>Monday — the Planner agent records the launch review</h1>
      <p class="lead">One conversation, cooked into memory before anything else happens.</p>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">The conversation</h3>
        ${d.kickoff.turns.map(([, t]) => `<p class="small quote">${esc(t)}</p>`).join("")}</div>
        <div class="card"><h3 style="margin-top:0">What memory says</h3>${factList(k.facts)}</div></div>`;
  }

  function reportsView() {
    const d = D();
    if (!S.reported.length && !S.reports) return running() ? waiting("Three reports") : empty("Three reports");
    const conv = (key) => S.reported.find((r) => r.key === key)?.conv;
    const cooked = S.reports?.cooked || S.cooked;
    const order = Object.keys(cooked).sort((a, b) => cooked[a] - cooked[b]);
    return `<h1>Wednesday 09:01 — three agents report in the same minute</h1>
      <p class="lead">Each agent reports in its own conversation; nobody waits for anybody. Two of them change the <b>date</b>, to different days. One changes the <b>owner</b>.</p>
      <div class="grid3">${d.reports.map((r) => `<div class="card"><h3 style="margin-top:0">${esc(person(r.agent))}</h3>
        <p class="small quote">${esc(r.turns[r.turns.length - 1][1])}</p>
        <p class="small"><b>${esc(r.field)}</b> → ${esc(r.value)}</p>
        <p class="small muted" style="margin-bottom:0"><span class="mono">${esc(conv(r.key) || "…")}</span><br>${cooked[r.key] != null ? `processed after <b>${cooked[r.key]} s</b>${order.length === 3 ? ` · ${["first", "second", "last"][order.indexOf(r.key)]}` : ""}` : running() ? "processing…" : ""}</p></div>`).join("")}</div>`;
  }

  function nowTable(now) {
    return `<table class="lint plain"><tbody>${FIELDS.map((f) => `<tr><td><b>${f}</b></td><td>${esc((now[f] || []).join(" | ") || "(nothing)")}${(now[f] || []).length > 1 ? ' <span class="bad-ink">← two answers live at once</span>' : ""}</td></tr>`).join("")}</tbody></table>`;
  }
  function conflictCards(cs) {
    if (!cs.length) return "";
    return cs.map((c) => `<p class="small"><span class="pill busy">${esc(c.category)}</span> on the ${esc(c.about)} fact — “${esc(c.name)}”</p><p class="small quote">${esc(c.description)}</p>`).join("");
  }

  function memoryView() {
    const m = S.memory[0];
    if (!m) return running() ? waiting("What memory says now") : empty("What memory says now");
    const last = S.watch[S.watch.length - 1];
    const watching = !S.audit.length && running();
    return `<h1>Memory says: ${esc((m.now.date || []).join(" | "))} · ${esc((m.now.owner || []).join(" | "))}</h1>
      <p class="lead">Two agents set two different dates. Memory holds one of them. Which one depends on whose report was processed last — it changes from run to run.</p>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">Launch-review facts</h3>${factList(m.facts)}</div>
        <div class="card"><h3 style="margin-top:0">Answers</h3>${nowTable(m.now)}
          <h3>Conflicts raised on the project</h3>
          <div class="counts"><div class="count"><b class="${(m.conflicts || []).length ? "warn-ink" : ""}">${watching && last ? last.found : (m.conflicts || []).length}</b><span>${watching ? `watching · ${last ? last.waited : 0} s` : `after ${esc(D().window || 90)} s`}</span></div></div>
          ${conflictCards(m.conflicts || [])}
          ${!watching && !(m.conflicts || []).length ? '<p class="small muted" style="margin-bottom:0">None: nothing flagged that two agents disagreed about the date.</p>' : ""}</div></div>`;
  }

  function trail(a) {
    const byFact = {};
    a.entries.forEach((e) => { (byFact[e.fact] = byFact[e.fact] || []).push(e); });
    return Object.entries(byFact).map(([fid, es]) => `<div class="card"><p class="small muted" style="margin-top:0">fact <span class="mono">${esc(fid)}</span></p>
      <table class="lint plain"><thead><tr><th>time (UTC)</th><th>event</th><th>by</th><th>what changed</th></tr></thead><tbody>
      ${es.map((e) => `<tr class="${e.changes.some((c) => c[3] === "BLIND") ? "lost" : ""}"><td class="mono">${esc(hhmmss(e.at))}</td><td>${esc(e.event)}</td><td>${esc(e.by)}</td>
        <td>${e.event === "ADD" ? `<span class="small">${esc(e.text.split(" (as of")[0])}</span>` : e.changes.length ? e.changes.map((c) => `${c[3] === "BLIND" ? '<span class="mark bad">✗ BLIND</span>' : '<span class="mark ok">✓ INFORMED</span>'} <b>${esc(c[0])}</b>: ${c[1] === c[2] ? `${esc(c[2])} (kept)` : `${esc(c[1])} → ${esc(c[2])}`}`).join("<br>") : '<span class="muted small">no change to the date or the owner</span>'}</td></tr>`).join("")}
      </tbody></table></div>`).join("");
  }
  function verdictTable(v) {
    if (!v) return "";
    const how = (x) => ({ DECIDED: `<span class="mark ok">✓</span> changed by ${esc(x.by)}, who had seen the value it replaced`,
      "ARRIVAL ORDER": `<span class="mark bad">✗</span> ${esc(x.by)}'s report was processed last; nobody chose it`,
      UNRESOLVED: '<span class="mark bad">✗</span> two answers live at once', UNCHANGED: '<span class="muted">never revised</span>' }[x.state]);
    return `<table class="lint plain"><thead><tr><th>field</th><th>memory says now</th><th>how it got there</th></tr></thead><tbody>
      ${FIELDS.map((f) => { const x = v[f]; return `<tr><td><b>${f}</b></td><td>${esc(x.now.join(" | "))}</td><td>${how(x)}${x.lost.length ? `<br><span class="small bad-ink">overwritten without being seen: ${esc(x.lost.join(", "))} — now only in “previously …”</span>` : ""}</td></tr>`; }).join("")}
      </tbody></table>`;
  }

  function auditView() {
    const a = S.audit[0];
    if (!a) return running() ? waiting("Audit") : empty("Audit");
    const blind = a.changes.filter((c) => c.verdict === "BLIND");
    return `<h1>${blind.length ? `${esc(blind[0].by)} overwrote ${esc(blind[0].replaced_by)}'s ${esc(blind[0].old)} without ever seeing it` : "Every change was informed"}</h1>
      <p class="lead"><code>fact trace</code> says which conversation made each revision. A revision is <b>blind</b> when every message it came from was stored before the value it replaced existed in memory.</p>
      ${trail(a)}
      ${S.outcomes ? `<div class="card"><h3 style="margin-top:0">What each report did to memory</h3><table class="lint plain"><tbody>
        ${S.outcomes.map((o) => `<tr><td><b>${esc(o.agent)}</b></td><td>${esc(o.field)} → ${esc(o.value)}</td><td>${o.state === "KEPT" ? '<span class="mark ok">✓</span>' : '<span class="mark bad">✗</span>'} ${esc(o.how)}</td></tr>`).join("")}
      </tbody></table></div>` : ""}
      <div class="card"><h3 style="margin-top:0">Verdict</h3>${verdictTable(S.verdict.before)}</div>`;
  }

  function decideView() {
    const d = D(), x = d.decision;
    if (!S.decision && !S.verdict.after) return running() && S.verdict.before ? waiting("The lead decides") : empty("The lead decides");
    const a = S.audit[1];
    return `<h1>Thursday — Mara decides, on the record</h1>
      <p class="lead">${esc(person(x.by))}: the review is on <b>${esc(x.values.date)}</b> and <b>${esc(x.values.owner)}</b> owns it — ${esc(x.reason)}.
      The decision is written onto the facts themselves (<code>fact update</code>), so their trace now ends with someone who saw both reports.</p>
      ${S.decision ? `<div class="card"><h3 style="margin-top:0">Written</h3>${S.decision.written.map((w) => `<div class="memfact" style="display:block"><span class="mono small">${esc(w.fact)}</span><br>${esc(w.body)}</div>`).join("")}
        ${S.decision.removed.length ? `<p class="small muted">removed ${S.decision.removed.length} other fact(s) that still answered it</p>` : ""}</div>` : ""}
      ${a ? trail(a) : ""}
      ${S.verdict.after ? `<div class="card"><h3 style="margin-top:0">Verdict, after the decision</h3>${verdictTable(S.verdict.after)}</div>` : ""}`;
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
    if (!confirm("Delete the demo's project, conversations, facts and actors?")) return;
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
    else if (k === "progress") {
      term("progress", ev.text.trim());
      if (d.conv) S.reported.push(d);
      if (d.found != null) S.watch.push(d);
      if (S.caughtUp && (S.view === "reports" || S.view === "memory")) renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "kickoff") S.kickoff = d;
    else if (k === "cooked") { S.cooked[d.key] = d.after; if (S.caughtUp && S.view === "reports") renderStage(); }
    else if (k === "reports") S.reports = d;
    else if (k === "memory") S.memory.push(d);
    else if (k === "audit") S.audit.push(d);
    else if (k === "outcomes") S.outcomes = d.outcomes;
    else if (k === "verdict") S.verdict[d.stage] = d.verdict;
    else if (k === "decision") S.decision = d;
    else if (k === "result") S.result = d.report;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project, conversations, facts and actors are gone.", true); }
      else if (S.caughtUp) banner("Done. The Audit step shows who overwrote whom; step 7 shows the decision on the record.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text", "cooked"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "kickoff", "cooked", "reports", "memory", "audit",
    "outcomes", "verdict", "decision", "result", "done", "error"];
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
    show(state.status === "running" ? S.view : S.verdict.after ? "audit" : "connect");
    subscribe();
  })();
})();

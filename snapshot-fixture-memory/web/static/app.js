/* Snapshot fixture memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "The rules", sub: "9 rules, text + metadata", view: "setup" },
    { n: 3, title: "Pin + CI #1", sub: "three fixtures, agent v1", view: "ci1" },
    { n: 4, title: "Teammates' writes", sub: "5 changes, no code change", view: "week" },
    { n: 5, title: "CI #2", sub: "same code, same tests", view: "ci2" },
    { n: 6, title: "CI #3", sub: "agent v2 ships a bug", view: "ci3" },
    { n: 7, title: "Score", sub: "which fixture told the truth?", view: "score" },
  ];
  const STRATS = ["A", "B", "C"];
  const fresh = () => ({ setup: null, pinned: null, ci: {}, week: [], weekDone: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const mark = (ok) => `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup: setupView, ci1: () => ciView(1), week: weekView, ci2: () => ciView(2), ci3: () => ciView(3), score: scoreView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const w = S.world || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Project</dt><dd>${w.project ? `<span class="mono">${esc(w.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${w.project ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 25 seconds · everything it creates is prefixed <code>${esc(d.prefix || "mlu-snp")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Pin the memory your agent's tests read. Pinning the ids pins nothing.</h1>
      <p class="lead">${esc(d.company || "Tollquay Freight")} (fictional) runs a quote agent on project memory: one fact per shipping rule, a text people read and metadata the agent's code reads.
      Eight tests guard the agent, and they need memory, so the team pins a fixture — three ways side by side: <b>A</b> read live memory, <b>B</b> pin the fact ids, <b>C</b> pin the content (with a sha256 and a drift check).
      Then teammates edit the rules (no code change), and later a real bug ships. Which fixture tells the truth? It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH.</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-snapshotting-for-agent-testing" target="_blank" rel="noopener">memorylake.ai › Memory snapshotting for agent testing</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const s = S.setup;
    if (!s) return running() ? waiting("The rules") : empty("The rules");
    return `<h1>The rules: 9 facts in project memory</h1>
      <p class="lead">Each rule is one fact: a text, then <code>fact update --metadata</code> with what the agent's code reads (topic, who it applies to, status, the limit). One rule is a draft: written, not yet approved.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>key</th><th>topic</th><th>applies to</th><th>status</th><th>text</th></tr></thead><tbody>
        ${s.rules.map((r) => `<tr><td class="mono"><b>${esc(r.key)}</b></td><td>${esc(r.topic)}</td><td>${esc(r.applies_to)}</td><td>${r.status === "draft" ? '<span class="pill">draft</span>' : esc(r.status)}</td><td>${esc(r.text)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function ciTable(c) {
    const d = D(), st = d.strategies || {};
    const cell = (r) => `<td class="${r.ok ? "" : "bad-ink"}">${mark(r.ok)} ${esc(r.answer)}</td>`;
    return `<table class="lint plain"><thead><tr><th>test</th><th>expect</th>${STRATS.map((s) => `<th>${esc(st[s]?.short || s)}</th>`).join("")}</tr></thead><tbody>
      ${c.rows.map((r) => `<tr><td><span class="mono">${esc(r.id)}</span> <span class="small">${esc(r.ask)}</span></td><td>${esc(r.expect)}</td>${STRATS.map((s) => cell(r[s])).join("")}</tr>`).join("")}
      <tr><td><b>passed</b></td><td></td>${STRATS.map((s) => `<td><b class="${c.summary[s].passed === 8 ? "" : "bad-ink"}">${c.summary[s].passed}/8</b></td>`).join("")}</tr>
      </tbody></table>`;
  }

  function ciView(n) {
    const c = S.ci[n];
    const h = { 1: "Pin the fixtures, CI #1 — agent v1", 2: "CI #2 — the same agent v1, the same tests", 3: "CI #3 — agent v2 ships a bug" }[n];
    if (!c) return running() ? waiting(h) : empty(h);
    const d = D(), st = d.strategies || {};
    const pinned = n === 1 && S.pinned ? `<div class="card"><h3 style="margin-top:0">The pin</h3><table class="lint plain"><thead><tr><th>key</th><th>fact id (B keeps this)</th><th>sha256 of text + metadata (C keeps this, and the content)</th></tr></thead><tbody>
      ${S.pinned.facts.map((f) => `<tr><td class="mono"><b>${esc(f.key)}</b></td><td class="mono small">${esc(f.id)}</td><td class="mono small">${esc(f.sha)}…</td></tr>`).join("")}</tbody></table></div>` : "";
    const lead = { 1: `${STRATS.map((s) => `<b>${esc(st[s]?.short)}</b>: ${code(st[s]?.how)}`).join("<br>")}`,
      2: "Nobody touched the agent's code or its tests. Five teammates' writes landed in memory.",
      3: `${esc(d.regression?.commit || "")} — ${esc(d.regression?.bug || "")}.` }[n];
    const checks = `<div class="card"><h3 style="margin-top:0">Fixture checks</h3>
      <p><b>B</b>: <b class="${c.b_ok ? "" : "bad-ink"}">${c.b_resolved}/${c.b_total} pinned ids resolve ${c.b_ok ? "✓" : "✗"}</b>${n > 1 && c.summary.B.reds.length ? ` <span class="bad-ink">— while ${c.summary.B.reds.length} tests changed: <code>fact update</code> keeps the id</span>` : ""}</p>
      <p><b>C</b>: every record matches its sha256 ${c.c_ok ? "✓" : "✗"} · drift check (does not fail the build): ${c.drift.length ? `live memory has moved <b>${c.drift.length}</b> way(s) since the pin` : "live memory matches the pin"}</p>
      ${c.drift.length ? `<table class="lint plain"><tbody>${c.drift.map((r) => `<tr><td class="mono"><b>${esc(r.key)}</b></td><td class="${r.what.startsWith("metadata") ? "bad-ink" : ""}">${esc(r.what)}</td></tr>`).join("")}</tbody></table>` : ""}</div>`;
    const samples = (c.samples || []).length ? `<div class="card"><h3 style="margin-top:0">Same id, other content</h3>
      ${c.samples.map((x) => `<p><span class="mono">${esc(x.tid)}</span> under B read <b class="mono">${esc(x.key)}</b> by its pinned id <span class="mono small">${esc(x.id)}</span><br>
        <span class="muted small">at the pin</span> ${esc(x.pinned)}<br><span class="muted small">today</span> <span class="bad-ink">${esc(x.today)}</span></p>`).join("")}</div>` : "";
    const triage = c.triage ? `<div class="card"><h3 style="margin-top:0">Triage: run v1 on the same inputs, same moment</h3>
      <table class="lint plain"><thead><tr><th>fixture</th><th>red</th><th>the code broke</th><th>the code turned green</th><th>red without the code</th></tr></thead><tbody>
      ${STRATS.map((s) => { const t = c.triage[s]; return `<tr><td><b>${esc(st[s]?.short)}</b></td><td>${esc(c.summary[s].reds.join(", ") || "—")}</td><td><b>${esc(t.broke.join(", ") || "—")}</b></td><td class="${t.greened.length ? "bad-ink" : ""}">${esc(t.greened.join(", ") || "—")}</td><td class="${t.unexplained.length ? "bad-ink" : ""}">${esc(t.unexplained.join(", ") || "—")}</td></tr>`; }).join("")}
      </tbody></table></div>` : "";
    return `<h1>${h}</h1><p class="lead">${lead}</p>${pinned}<div class="card">${ciTable(c)}</div>${checks}${samples}${triage}`;
  }

  function weekView() {
    const w = S.week;
    if (!w.length) return running() ? waiting("Teammates' writes") : empty("Teammates' writes");
    const cls = (k) => (k === "metadata only" || k === "new rule" ? "bad-ink" : "");
    return `<h1>A week of teammates' writes: 5 changes, no code change</h1>
      <p class="lead">Ops moves two limits, pricing approves a draft (a <code>fact update</code> that changes only metadata) and adds a new rule, support rewords one. Ordinary work — and each <code>fact update</code> keeps the fact's id.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>#</th><th>rule</th><th>change</th><th>who</th><th>why</th></tr></thead><tbody>
        ${w.map((c, i) => `<tr><td class="num">${i + 1}</td><td class="mono"><b>${esc(c.key)}</b></td><td class="${cls(c.change)}">${esc(c.change)}</td><td>${esc(c.who)}</td><td>${esc(c.why)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function scoreView() {
    const sc = S.score, d = D(), st = d.strategies || {};
    if (!sc) return running() ? waiting("Score") : empty("Score");
    const r = sc.rows;
    return `<h1>Which fixture told the truth? C: 0 false reds, the bug caught 2/2</h1>
      <p class="lead">A test should change when the code changes, and only then. Regression tests here are the two boundary tests (T1 at exactly 30 kg, T5 at exactly 14:00); v2 refuses a value exactly at a limit.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>fixture</th><th>CI #2 red, code unchanged</th><th>fixture check says</th><th>CI #3 regression tests caught</th><th>CI #3 red the code did not cause</th></tr></thead><tbody>
      ${STRATS.map((s) => { const x = r[s]; return `<tr><td><b>${esc(st[s]?.short)}</b></td>
        <td class="${x.flipped.length ? "bad-ink" : ""}"><b>${x.flipped.length}/8</b> ${esc(x.flipped.join(", "))}</td>
        <td class="${s === "B" ? "bad-ink" : ""}">${esc(x.check)}</td>
        <td class="${x.caught.length < x.of ? "bad-ink" : ""}"><b>${x.caught.length}/${x.of}</b> ${esc(x.caught.join(", "))}</td>
        <td class="${x.unexplained.length ? "bad-ink" : ""}"><b>${x.unexplained.length}</b> ${esc(x.unexplained.join(", "))}</td></tr>`; }).join("")}
      </tbody></table></div>
      <div class="card"><ul class="steps-list">
        <li><b>B's check passed while 3 tests flipped.</b> <code>fact update</code> keeps the id, so a list of ids pins names, not content. B also never sees the new artwork rule (a new id) that A picks up.</li>
        <li><b>T1 stopped testing the boundary on live memory.</b> The limit moved to 25 kg, so a 30 kg parcel is refused by v1 and v2 alike: the bug is invisible there.</li>
        <li><b>T6 turned green under v2 on live memory.</b> The bug cancelled a memory change (Saturday cutoff 11 → 12); the suite reports it as fixed.</li>
        <li><b>C</b> red exactly T1 and T5, and its drift check listed the five ways memory moved — including the metadata-only approval. Re-pin on purpose, in a reviewed commit, not on a red build.</li></ul></div>`;
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
    if (!confirm("Delete the demo's memory project and its facts?")) return;
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
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) { resetView(); S.world = null; } term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") {
      term("progress", ev.text.trim());
      if (d.phase === "change") S.week.push(d);
      if (S.caughtUp && S.view === "week") renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = { ...(S.world || {}), ...d };
    else if (k === "setup") S.setup = d;
    else if (k === "pinned") S.pinned = d;
    else if (k === "ci") S.ci[d.n] = d;
    else if (k === "week") S.weekDone = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's project and its facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The three fixtures are scored side by side under step 7.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "pinned", "ci", "week", "score", "done", "error"];
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

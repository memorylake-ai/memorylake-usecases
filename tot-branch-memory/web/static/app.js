/* Branched memory for Tree-of-Thoughts agents — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Trunk + log", sub: "project · conversation · actors", view: "setup" },
    { n: 3, title: "The task", sub: "TEXT → trunk memory", view: "task" },
    { n: 4, title: "Three branches", sub: "a project each · THINKING", view: "branches" },
    { n: 5, title: "One trunk log", sub: "409 not the current head", view: "log" },
    { n: 6, title: "Reasoning ≠ memory", sub: "THINKING replayed vs fact list", view: "reasoning" },
    { n: 7, title: "Checkout", sub: "trunk vs trunk + branch", view: "checkout" },
    { n: 8, title: "Merge + rollback", sub: "fact add · proj delete", view: "merge" },
    { n: 9, title: "A week later", sub: "plan from the trunk", view: "next" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    setup: null, task: null, branches: {}, race: { r1: [], r2: [] }, raceStats: {}, log: null, reasoning: {}, checkout: null,
    merge: {}, plan: null, next: null, summary: null, curRun: "r1",
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const approach = (k) => story()?.approaches?.[k] || k;
  const branchesOf = (run) => (story()?.runs.find((r) => r.run === run)?.branches || []).map((b) => S.branches[`${run}/${b.key}`]).filter(Boolean);

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Plan a task<small>Pathfinder reads the trunk</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, task, branches, log, reasoning, checkout, merge, next, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const verdictPill = (v) => `<span class="pill ${v === "merged" ? "ok" : v === "pruned" ? "warn" : ""}">${esc(v)}</span>`;
  const mark = (ok) => `<span class="pill ${ok ? "ok" : "err"}">${ok ? "✓" : "✗"}</span>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Trunk</dt><dd>${S.st.ids?.trunk ? `<span class="mono muted">${esc(S.st.ids.trunk)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A Tree-of-Thoughts agent whose branches survive the run: explored in parallel, merged, rolled back, reused.</h1>
      <p class="lead"><b>Pathfinder</b>, Larkspur Outfitters' planning bot, opens three branches for one task. Each branch is its own <b>project</b>;
      its reasoning goes into <b>THINKING blocks</b>, which MemoryLake stores verbatim but does not turn into memory. The explorers share one <b>trunk log</b>
      that only accepts appends to its head (<b>409</b> otherwise). The winner and the lessons of the losers are <b>merged</b> into the trunk, the dead branches are
      <b>rolled back</b> with <code>proj delete</code>, and a week later the next task reads the trunk first and opens one branch instead of three.
      This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the conversations, the trunk and branch projects, Priya and Pathfinder.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-tree-of-thoughts-agents" target="_blank" rel="noopener">memorylake.ai › memory for Tree-of-Thoughts agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const s = S.setup; if (!s) return empty("Trunk + log");
    return `<h1>The trunk: a project for what is kept, a conversation every explorer writes to</h1>
      <p class="lead">The <b>trunk project</b> holds the tasks (extracted from Priya's messages), the merged results and the lessons of pruned branches.
      The <b>trunk log</b> is one conversation in it: every task, every branch opening and every branch result is appended there, by whoever gets there first.</p>
      <div class="grid">
        <div class="card"><div class="row spread"><b>${esc(s.trunk.name)}</b><span class="pill accent">trunk project</span></div>
          <div class="muted small">${esc(s.trunk.description)}</div><div class="mono small faint">${esc(s.ids.trunk)}</div></div>
        <div class="card"><div class="row spread"><b>Pathfinder trunk log</b><span class="pill">conversation</span></div>
          <div class="muted small">a linear log: an append must extend the current head</div><div class="mono small faint">${esc(s.ids.log)}</div></div>
        <div class="card"><div class="row spread"><b>${esc(s.human.name)}</b><span class="pill">actor</span></div>
          <div class="muted small">${esc(s.human.description)}</div><div class="mono small faint">${esc(s.ids.priya)}</div></div>
        <div class="card"><div class="row spread"><b>${esc(s.planner.name)}</b><span class="pill">actor</span></div>
          <div class="muted small">${esc(s.planner.description)}</div><div class="mono small faint">${esc(s.ids.pathfinder)}</div></div></div>`;
  }

  function task() {
    const t = S.task; if (!t) return empty("The task");
    return `<h1>The task — ${esc(t.service)} p95 below 300 ms</h1>
      <p class="lead">Priya writes it into the trunk log as a <b>TEXT</b> message, so MemoryLake extracts it into trunk memory (the constraints included).
      Pathfinder has three candidate approaches and will explore all of them at once.</p>
      <div class="card"><div class="row spread"><b>${esc(t.run)} · ${esc(t.date)} · ${esc(t.service)}</b><span class="pill ${t.new ? "ok" : ""}">${t.new ? "appended now" : "appended by an earlier run"}</span></div>
        <p>“${esc(t.task)}”</p></div>
      <h2>Candidate branches</h2><div class="grid">${t.candidates.map((c) => `<div class="card"><b>${esc(c.key)}</b><div class="muted small">${esc(c.approach)}</div></div>`).join("")}</div>`;
  }

  function branchCard(b) {
    return `<div class="card branch ${esc(b.verdict)}"><div class="row spread"><b>${esc(b.branch)}</b>${verdictPill(b.verdict)}</div>
      <div class="muted small">${esc(approach(b.approach))}</div>
      <div class="row" style="margin:8px 0"><span class="p95">${esc(b.p95_ms)} ms</span><span class="muted small">p95 · target 300 ms</span></div>
      ${(b.thoughts || []).map((t) => `<div class="thought"><b>THINKING</b> ${esc(t)}</div>`).join("")}
      <div class="memfact small"><span><b>pinned on the branch</b> ${esc(b.fact)}</span></div>
      <div class="mono small faint">${b.merged_earlier ? "merged into the trunk by an earlier run" : `project ${esc(b.project)} · ${esc(b.conv || "")}`}</div></div>`;
  }
  function raceList(run) {
    const ev = S.race[run] || [], st = S.raceStats[run];
    if (!st) return "";
    if (!ev.length && !st.appends) return `<div class="card small muted">The trunk log was written by an earlier run; no race this time.</div>`;
    return `<div class="card"><div class="row spread"><b>Trunk log writes</b><span class="pill ${st.conflicts ? "warn" : "ok"}">${st.appends} landed · ${st.conflicts} refused with 409 and retried</span></div>
      ${ev.map((e) => e.ok ? `<div class="race good">✓ ${esc(e.key)} landed on attempt ${esc(e.attempt)}</div>`
        : `<div class="race bad">409 ${esc(e.key)}: parent ${esc(e.stale || "(latest when sent)")} is not the current head → retry on ${esc(e.head)}</div>`).join("")}</div>`;
  }
  function branches() {
    const list = branchesOf("r1");
    if (!list.length) return empty("Three branches");
    return `<h1>Three branches at once — a project each, reasoning in THINKING blocks</h1>
      <p class="lead">Each explorer gets its own <b>project</b> (branch-local memory) and conversation. Its step-by-step reasoning goes into <b>THINKING</b> blocks; its
      measured result is pinned on the branch with <code>fact add</code> + <code>fact update --metadata</code>. All three write to the trunk log at the same moment, without a
      <code>--parent</code>: the CLI looks the head up and sends it as the parent, so the losers of each race get <b>409</b> and retry on the new head.</p>
      <div class="grid">${list.map(branchCard).join("")}</div>${raceList("r1")}`;
  }

  function log() {
    const l = S.log; if (!l) return empty("One trunk log");
    const s = l.stale;
    return `<h1>One trunk log, three writers — the server only accepts appends to the head</h1>
      <p class="lead"><code>conv msg list</code> in the order the server accepted it. Timestamps are what each writer claimed; <code>sequence_no</code> is the order that won.
      Then a writer with an old view of the log appends on top of message #1: refused.</p>
      <div class="row" style="gap:8px;margin-bottom:10px">${mark(l.contiguous)} sequence_no 1..${l.rows.length} with no gaps ${mark(l.unique)} every entry exactly once</div>
      <div class="card"><table class="tour"><thead><tr><th>#</th><th>claimed time</th><th>event</th><th>branch</th><th>blocks</th><th>text</th></tr></thead><tbody>
        ${l.rows.map((r) => `<tr><td class="mono"><b>${esc(r.seq)}</b></td><td class="mono small">${esc(String(r.ts || "").slice(0, 16).replace("T", " "))}</td><td class="small">${esc(r.event)}</td>
          <td class="small">${esc(r.branch || r.run)}</td><td class="mono small">${esc(r.kinds)}</td><td class="small">${esc(r.text)}</td></tr>`).join("")}</tbody></table>
        <div class="mono small faint" style="margin-top:6px">head (conv get → current_message_id): ${esc(l.head)}</div></div>
      ${s ? `<div class="card"><div class="row spread"><b>Append with <code>--parent ${esc(s.parent)}</code> (message #1)</b>${s.refused ? '<span class="pill ok">✓ refused · 409</span>' : '<span class="pill err">✗ accepted</span>'}</div>
        <p class="small mono">${esc(s.detail)}</p></div>` : ""}`;
  }

  function reasoningBlock(r) {
    if (!r) return "";
    return `<div class="row" style="gap:8px;margin-bottom:6px"><span class="pill ${r.verbatim === r.thoughts ? "ok" : "err"}">${r.verbatim}/${r.thoughts} THINKING blocks replayed verbatim</span>
        <span class="pill ${r.kept_out === r.details ? "ok" : "err"}">${r.kept_out}/${r.details} reasoning-only details kept out of memory</span></div>
      ${r.branches.map((b) => `<div class="card"><div class="row spread"><b>${esc(b.branch)}</b><span class="mono small faint">${esc(b.conv)}</span></div>
        ${b.thoughts.map((t, i) => `<div class="thought"><b>THINKING ${i + 1}</b> ${b.verbatim[i] ? "✓ replayed verbatim" : "✗ not found as sent"}<br>${esc(t)}</div>`).join("")}
        <div class="small muted">reasoning-only details, checked against every scope's <code>fact list</code>:</div>
        <div class="checks">${b.checks.map((c) => `<span class="pill ${c.leaked.length ? "err" : "ok"}">${c.leaked.length ? "✗" : "✓"} ${esc(c.detail)}</span>`).join("")}</div>
        ${b.checks.flatMap((c) => c.leaked.map((h) => `<div class="memfact small"><span>✗ “${esc(c.detail)}” is in ${esc(h.scope)} memory: ${esc(h.text)}</span></div>`)).join("")}
        <div class="small muted" style="margin-top:6px">what the branch remembers:</div>
        ${b.memory.map((f) => `<div class="memfact small"><span>${esc(f.text)}</span></div>`).join("") || '<div class="small faint">nothing</div>'}</div>`).join("")}`;
  }
  function reasoning() {
    const r = S.reasoning.r1; if (!r) return empty("Reasoning ≠ memory");
    return `<h1>Reasoning is stored, not remembered</h1>
      <p class="lead"><code>conv msg list</code> replays every THINKING block exactly as sent (compared by sha256). Each branch's reasoning carries details that appear nowhere else —
      node names, ratios, benchmark numbers. None of them is in any scope's memory: trunk, every branch, Priya, Pathfinder. Only the pinned result (and what the TEXT turns said) is.</p>
      ${reasoningBlock(r)}`;
  }

  function checkout() {
    const c = S.checkout; if (!c) return empty("Checkout");
    return `<h1>Checkout — trunk alone vs trunk + one branch</h1>
      <p class="lead">"Checking out" a branch is a search over the trunk and that branch's project: <code>search --projects &lt;trunk&gt;,&lt;branch&gt;</code>.
      The same query over the trunk alone returns ${c.trunk_hits} hit(s) and none of the branch results.</p>
      <div class="card small mono">search "${esc(c.query)}"</div>
      <div class="grid">${c.views.map((v) => `<div class="card"><div class="row spread"><b>trunk + ${esc(v.branch)}</b>${mark(v.own.length && !v.in_trunk_only)}</div>
        <div class="small muted">${v.hits} hit(s); ${v.own.length} from the branch · trunk alone: ${v.in_trunk_only}</div>
        ${v.own.map((h) => `<div class="memfact small"><span>${esc(h.text)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function mergeBlock(m) {
    if (!m) return "";
    return `<div class="card"><b>Merged into the trunk</b>
        ${m.merges.map((x) => `<div class="memfact small"><span>${verdictPill(x.kind)} ${esc(x.text)}</span><span class="when mono">${x.new ? "new" : "earlier"} · ${esc(x.id)}</span></div>`).join("")}</div>
      ${m.rolled.length ? `<div class="card"><b>Rolled back</b>${m.rolled.map((r) => `<div class="memfact small"><span><b>${esc(r.branch)}</b> ${r.deleted_now ? `<code>proj delete ${esc(r.project)}</code>` : "deleted by an earlier run"}
        → <code>fact list --projects ${esc(r.project)}</code>: ${r.gone ? "404, its memory is gone" : "still there"}</span><span class="when">${mark(r.gone)}</span></div>`).join("")}</div>` : ""}`;
  }
  function merge() {
    const m = S.merge.r1; if (!m) return empty("Merge + rollback");
    return `<h1>Merge the winner and the lessons; roll back the dead branches</h1>
      <p class="lead">Merging is a promotion: one trunk fact per branch (<code>fact add</code> + <code>fact update --metadata</code> with the branch, its project and its result fact) —
      the winner's result, and each pruned branch's <b>reason</b>, so it is not tried again. Rolling back a pruned branch is <code>proj delete</code> (its conversation first);
      its memory is gone. The winning branch is kept, so its reasoning can still be replayed.</p>
      ${mergeBlock(m)}
      <div class="card"><b>The trunk now</b>${m.trunk.map((f) => `<div class="memfact small"><span>${f.type === "extracted" ? '<span class="pill">extracted</span>' : verdictPill(f.type)} ${esc(f.text)}</span></div>`).join("")}</div>`;
  }

  function planCard(p, title) {
    if (!p) return "";
    return `<div class="card"><div class="row spread"><b>${title}</b><span class="pill">${p.hits} hit(s) · ${p.lessons.length} lesson(s)</span></div>
      ${p.steps.map((s) => `<div class="plan-step"><span>${s.action === "skip" ? '<span class="pill warn">skip</span>' : s.action === "first" ? '<span class="pill ok">try 1st</span>' : '<span class="pill">explore</span>'}</span>
        <span><b>${esc(s.approach)}</b> <span class="muted">${esc(approach(s.approach))}</span><br><span class="small">${s.action === "skip" ? `pruned in ${esc(s.branch)}: ${esc(s.why)}` : esc(s.why)}</span>
        ${s.fact ? `<span class="mono small faint"> · ${esc(s.fact)}</span>` : ""}</span></div>`).join("")}
      ${p.others ? `<div class="small faint">${p.others} other hit(s) left out: task notes, not lessons</div>` : ""}</div>`;
  }
  function next() {
    const n = S.next, p = S.plan;
    if (!p) return empty("A week later");
    const r2 = branchesOf("r2");
    return `<h1>A week later — the next task reads the trunk before branching</h1>
      <p class="lead">Product search is slow too. Before opening anything, Pathfinder searches the trunk: the pooling branch won, the cache and index branches were pruned —
      and the trunk says why. So it ${n ? `opens <b>${n.opened}</b> branch instead of ${n.r1_opened}, and skips <b>${n.skipped}</b>, each skip citing the fact` : "plans"}.</p>
      ${planCard(p, "Pathfinder's plan, from the trunk")}
      <div class="grid">${r2.map(branchCard).join("")}</div>${raceList("r2")}
      ${S.reasoning.r2 ? `<h2>Reasoning ≠ memory, again</h2>${reasoningBlock(S.reasoning.r2)}` : ""}
      ${S.merge.r2 ? `<h2>Merged</h2>${mergeBlock(S.merge.r2)}` : ""}`;
  }

  function ask() {
    const ready = !!S.st.ids?.trunk;
    return `<h1>Plan a task from the trunk</h1>
      <p class="lead">Describe a task. Pathfinder searches the trunk (<code>search --projects &lt;trunk&gt;</code>), keeps the merge and prune lessons, and orders the approaches:
      what won before goes first, what was pruned is skipped with the fact that says why.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. the cart page p95 is 700 ms" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
        <button class="btn primary" ${ready ? "" : "disabled"}>Plan</button></form>
        <div class="chips">${["The cart page p95 is 700 ms; it reads prices from the orders cluster.", "Order history API p95 is 480 ms on the same Postgres cluster.", "Speed up the price lookup in checkout."].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>
      <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div>`;
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
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askPlan($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askPlan(c.dataset.q); }));
    }
  }
  async function askPlan(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the trunk…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = planCard(r, "Pathfinder's plan");
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
  function resetView() {
    S.setup = null; S.task = null; S.branches = {}; S.race = { r1: [], r2: [] }; S.raceStats = {}; S.log = null; S.reasoning = {}; S.checkout = null;
    S.merge = {}; S.plan = null; S.next = null; S.summary = null; S.curRun = "r1";
  }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the trunk log and branch conversations, the trunk and branch projects, Priya and Pathfinder from your account?")) return;
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
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView(); if (d.index === 9) S.curRun = "r2";
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "race") { term(d.ok ? "note" : "error", ev.text.trim()); (S.race[S.curRun] ||= []).push(d); }
    else if (k === "text") { /* the CLI's own summary lines; the views show the same data */ }
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "existing") S.st.ids = d;
    else if (k === "setup") { S.setup = d; S.st.ids = d.ids; }
    else if (k === "task") S.task = d;
    else if (k === "branch") S.branches[d.branch] = d;
    else if (k === "race_stats") S.raceStats[d.run] = d;
    else if (k === "log") S.log = d;
    else if (k === "reasoning") S.reasoning[(d.runs || ["r1"])[0]] = d;
    else if (k === "checkout") S.checkout = d;
    else if (k === "merge") S.merge[d.run] = d;
    else if (k === "plan") S.plan = d;
    else if (k === "next") S.next = d;
    else if (k === "summary") S.summary = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the conversations, the trunk and branch projects, Priya and Pathfinder are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Open “A week later” for the plan read from the trunk, or try “Plan a task”.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "race"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "race", "connected", "existing", "setup", "task", "branch", "race_stats", "log",
    "reasoning", "checkout", "merge", "plan", "next", "summary", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => { try { onEvent(JSON.parse(e.data)); } catch { /* the browser's own `error` event has no data */ } }));
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
      const h = (e) => { let ev; try { ev = JSON.parse(e.data); } catch { return; } onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") { Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; }); }
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.next ? "next" : "connect");
    subscribe();
  })();
})();

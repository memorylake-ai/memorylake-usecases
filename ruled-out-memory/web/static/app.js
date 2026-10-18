/* Ruled-out memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "five projects, one per arm", view: "setup" },
    { n: 3, title: "Run 1", sub: "four ruled out, one lead", view: "run" },
    { n: 4, title: "What memory says", sub: "each suspect, each arm", view: "census" },
    { n: 5, title: "Run 2 plans", sub: "“pick the next 4 actions”", view: "plan" },
    { n: 6, title: "Ruled out?", sub: "“has it ruled out X?”", view: "suspects" },
    { n: 7, title: "Score", sub: "what the resume would do", view: "score" },
  ];
  const fresh = () => ({ runSeen: false, stored: null, census: null, plans: {}, sus: {}, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "cache", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const ALL = () => S.data?.all || ["none", "typed", "scratch", "stated", "records"];
  const MEM = () => S.data?.memarms || ["typed", "scratch", "stated", "records"];
  const ARM = (a) => (S.data?.arms || {})[a] || a;
  const SH = (a) => (S.data?.short || {})[a] || a;
  const SUS = () => S.data?.suspects || [];
  const RULED = () => new Set(SUS().filter((s) => s.checked).map((s) => s.tool));

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through 0 and A–E</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, run: runView, census, plan: planView, suspects: susView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], guess: ["◇", "warn", "Yes, but memory holds no such verdict (a guess)"], recheck: ["↻", "bad", "the resume checks it again"], skip: ["✗", "bad", "claims a check that never ran"], other: ["?", "warn", "unclear"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  const STATE = { ruled: "says ruled out", suspected: "still a suspect", reading: "a bare reading", mention: "mentioned", none: "nothing" };

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.scratch)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes (three conversations extracted, then ~130 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-rom")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A ReAct loop rules out four suspects — and memory keeps only the lead</h1>
      <p class="lead">${esc(d.org || "Tarnwick")} (a fictional retailer) has an investigation agent, ${esc(d.agent || "Sleuth")}, on incident ${esc(d.incident || "INC-2291")}: checkout p99 latency went from 300 ms to 4.1 s.
      In one run of five actions the ReAct loop checks four suspects and each observation rules one out; the fifth finds a lead. The budget is spent, the incident is still open, and the next run asks memory what the investigation already established.
      Five ways to store the same loop: A Thought as TEXT with tool blocks (the framework default) · B the scratchpad as TEXT · C the same with every verdict stated · D one typed step record per iteration (<code>fact add</code>) · E the same records plus one coverage record. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 130 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-react-style-agent-loops" target="_blank" rel="noopener">memorylake.ai › Memory for ReAct-style agent loops</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Five projects with the same name, one boundary each</h1>
      <p class="lead">The project name is context for extraction, so every arm's project is called “${esc(d.project_name)}”; the arm is in the custom_id. Each boundary covers one project — that is all the resumed run reads.</p>
      <div class="grid2"><div class="card"><dl class="kv">${MEM().map((a) => `<dt>${esc(ARM(a))}</dt><dd class="mono small">${esc(w?.projects?.[a] || "pending")}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">In the conversations</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}
        <p class="small muted">D has no conversation: the app writes its records.</p></div></div>`;
  }

  function runView() {
    const d = S.data;
    if (!d || (!S.runSeen && !S.world)) return running() ? waiting("Run 1") : empty("Run 1");
    const ex = (a) => (d.messages[a] || []).slice(0, 4).map((m) => `<div class="small mono" style="white-space:pre-wrap">${esc(m.kind)}  ${esc(m.shown)}</div>`).join("");
    return `<h1>Run 1: four suspects ruled out, one lead, budget spent</h1>
      <p class="lead">${esc(d.incident)} · ${esc(d.symptom)}. ${esc(d.agent)} has ${d.budget} read-only actions.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>#</th><th>Thought</th><th>Action → Observation</th><th>verdict</th></tr></thead><tbody>
        ${d.iterations.map((it) => `<tr class="${it.verdict === "lead" ? "hl" : ""}"><td class="small"><b>${it.n}</b></td><td class="small">${esc(it.thought)}</td>
          <td class="small"><span class="mono">${esc(it.tool)}</span><br><span class="mono muted">${esc(JSON.stringify(it.result))}</span></td><td class="small"><b>${esc(it.verdict)}</b></td></tr>`).join("")}
      </tbody></table><p class="small muted" style="margin-bottom:0">${esc(d.closing)}</p></div>
      <div class="grid2">
        <div class="card"><h3 style="margin-top:0">A · typed tool blocks</h3>${ex("typed")}</div>
        <div class="card"><h3 style="margin-top:0">B · scratchpad as text</h3>${ex("scratch")}</div>
        <div class="card"><h3 style="margin-top:0">C · verdicts stated</h3>${(d.messages.stated || []).slice(2, 4).map((m) => `<div class="small mono" style="white-space:pre-wrap">${esc(m.shown)}</div>`).join("")}</div>
        <div class="card"><h3 style="margin-top:0">D · step records (<code>fact add</code>)</h3>${d.records.map((t) => `<div class="small">${esc(t)}</div>`).join("")}</div></div>
      ${S.stored ? `<div class="card"><h3 style="margin-top:0">Extraction settled</h3>${["typed", "scratch", "stated"].map((a) => { const r = S.stored.arms[a] || {}; return `<div class="small">${esc(ARM(a))}: ${r.messages} messages · ${r.facts} fact(s) in the project after ${r.seconds} s</div>`; }).join("")}
        <div class="small">${esc(ARM("records"))}: ${S.stored.arms.records?.records} records</div></div>` : ""}`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What memory says") : empty("What memory says");
    const c = S.census.arms;
    const n = (a) => SUS().filter((s) => s.checked && c[a].suspects[s.key].state === "ruled").length;
    const checked = SUS().filter((s) => s.checked).length;
    const cell = (a, s) => { const r = c[a].suspects[s.key];
      const cls = s.checked ? (r.state === "ruled" ? "" : r.state === "suspected" ? "bad-ink" : "muted") : (r.state === "ruled" ? "bad-ink" : "muted");
      return `<div class="small ${cls}"><b>${esc(STATE[r.state])}</b>${r.fact ? `: ${esc(r.fact)}` : ""}</div>`; };
    return `<h1>Ruled out in memory: ${MEM().map((a) => `${SH(a)} ${n(a)} of ${checked}`).join(" · ")}</h1>
      <p class="lead">Every fact in each arm's project, read for the six suspects: does memory say it was ruled out, still list it as a suspect, hold only a bare reading, or nothing? The lead row shows what extraction did keep.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>suspect</th>${MEM().map((a) => `<th>${esc(ARM(a))}</th>`).join("")}</tr></thead><tbody>
      ${SUS().map((s) => `<tr><td class="small"><b>${esc(s.name)}</b><br><span class="muted">${s.checked ? "ruled out in run 1" : "never checked"}</span></td>${MEM().map((a) => `<td>${cell(a, s)}</td>`).join("")}</tr>`).join("")}
      <tr class="hl"><td class="small"><b>the lead</b><br><span class="muted">migration 0412</span></td>${MEM().map((a) => `<td class="small">${c[a].lead.length ? esc(c[a].lead[0]) : '<span class="muted">nothing</span>'}</td>`).join("")}</tr>
      <tr><td class="small">facts in the project</td>${MEM().map((a) => `<td class="small">${c[a].facts}</td>`).join("")}</tr>
      </tbody></table></div>`;
  }

  function planView() {
    const any = ALL().some((a) => (S.plans[a] || []).length);
    if (!any) return unavailable() || (running() ? waiting("Run 2 plans") : empty("Run 2 plans"));
    const ruled = RULED();
    const re = (a) => (S.plans[a] || []).filter((r) => r.verdict && !r.empty).reduce((k, r) => k + (r.rechecks || []).length, 0);
    return `<h1>Re-checks of ruled-out suspects in the next plan: ${ALL().filter((a) => (S.plans[a] || []).length).map((a) => `${SH(a)} ${re(a)}`).join(" · ")}</h1>
      <p class="lead">“${esc(S.data?.ask_plan)}” — ${RUNS()} answers per arm through its boundary (0 has none). ↻ marks a tool whose suspect run 1 already ruled out.</p>
      ${ALL().filter((a) => (S.plans[a] || []).length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(ARM(a))}</h3>
        ${(S.plans[a] || []).map((r) => `<div class="row small">${markOf(r)} ${r.empty ? '<span class="muted">nothing attached</span> ' : ""}${(r.tools || []).map((t) => `<span class="${ruled.has(t) ? "bad-ink" : ""} mono">${ruled.has(t) ? "↻" : ""}${esc(t)}</span>`).join(" · ")}</div>`).join("")}</div>`).join("")}`;
  }

  function susView() {
    const any = ALL().some((a) => (S.sus[a] || []).length);
    if (!any) return unavailable() || (running() ? waiting("Ruled out?") : empty("Ruled out?"));
    return `<h1>“Has the investigation already ruled out X?”</h1>
      <p class="lead">Four suspects run 1 ruled out (the right answer is Yes) and two it never checked (Yes would skip a real lead). Only the first word counts.</p>
      ${ALL().filter((a) => (S.sus[a] || []).length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(ARM(a))}</h3><table class="lint plain"><thead><tr><th>suspect</th><th>truth</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${SUS().map((s) => { const rs = S.sus[a].filter((r) => r.q === s.key); const bad = rs.find((r) => r.verdict && r.verdict !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(s.name)}</td><td class="small">${s.checked ? "ruled out" : "never checked"}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}</tbody></table></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score, A = sc.arms;
    const head = MEM().map((a) => `${SH(a)} ${A[a].rechecks}`).join(" · ");
    return `<h1>Re-checks planned: 0 ${A.none.rechecks} · ${head}</h1>
      <p class="lead">✓ Yes, and memory says it was ruled out · ◇ Yes, but memory holds no such verdict (a guess) · ↻ No / Not recorded on a suspect run 1 ruled out: the resume checks it again · ✗ Yes on a suspect never checked: the resume skips it · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call; left out).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th class="num">ruled out in memory</th><th class="num">plans</th><th class="num">re-checks planned</th><th class="num">knows it was ruled out</th><th class="num">guessed Yes</th><th class="num">claims an unchecked one</th><th class="num">∅</th></tr></thead><tbody>
        ${ALL().map((a) => `<tr class="${a === "scratch" ? "hl" : ""}"><td><b>${esc(ARM(a))}</b></td><td class="num">${a === "none" ? "–" : `${sc.memory[a]} of 4`}</td><td class="num">${A[a].plans}</td><td class="num"><b class="${A[a].rechecks ? "bad-ink" : ""}">${A[a].rechecks}</b></td>
          <td class="num">${A[a].known}/${A[a].known_of}</td><td class="num">${A[a].guessed || ""}</td><td class="num"><span class="${A[a].claimed ? "bad-ink" : ""}">${A[a].claimed}</span>/${A[a].claimed_of}</td><td class="num">${A[a].empty || ""}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Per suspect</h3><table class="lint plain"><thead><tr><th>suspect</th>${ALL().map((a) => `<th class="num">${SH(a)}</th>`).join("")}</tr></thead><tbody>
        ${SUS().map((s) => `<tr><td class="small">${esc(s.name)} <span class="muted">(${s.checked ? "ruled out" : "never checked"})</span></td>${ALL().map((a) => { const x = (sc.by_suspect[s.key] || {})[a] || {};
          return `<td class="num small">${Object.keys(MARKS).filter((v) => x[v]).map((v) => `<span class="${v === "ok" ? "" : "bad-ink"}">${MARKS[v][0]}${x[v]}</span>`).join(" ")}${x.empty ? ` <span class="muted">∅${x.empty}</span>` : ""}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through 0 and A–E</h1>
      <p class="lead">“plan” asks for the next 4 actions; a suspect asks whether the investigation already ruled it out; anything else is asked as typed.</p>
      <div class="card"><label for="t-q">plan, a suspect, or a question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips"><button class="chip" data-q="plan">plan</button>${SUS().map((s) => `<button class="chip" data-q="${s.key}">${s.key} · ${esc(s.name)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">run 2 asks</span>${esc(x.asked)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.verdict === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(ARM(r.arm))} · ${r.empty ? "∅ nothing attached" : r.verdict ? `${markOf(r)} ${esc(MARKS[r.verdict][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.truth ? `<p class="small muted">truth: ${esc(x.truth)}</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, truth: r.truth, asked: r.asked });
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
    if (!confirm("Delete the demo's boundaries, conversations, actors (their facts first) and the five projects?")) return;
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
    else if (k === "run1") S.runSeen = true;
    else if (k === "stored") S.stored = d;
    else if (k === "census") S.census = d;
    else if (k === "plan_rows") S.plans[d.arm] = d.rows;
    else if (k === "suspect_rows") S.sus[d.arm] = d.rows;
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the five projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: plan or any suspect through 0 and A–E.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "run1", "stored", "census", "plan_rows", "suspect_rows", "arm_done",
    "score", "summary", "router_unavailable", "asked", "memory_view", "done", "error"];
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

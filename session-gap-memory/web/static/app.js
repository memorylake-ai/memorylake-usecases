/* Session-gap memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const sec = (x) => (x === null || x === undefined ? "—" : `+${Number(x).toFixed(1)}s`);

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one memory per arm", view: "setup" },
    { n: 3, title: "The past", sub: "6 sessions → facts", view: "history" },
    { n: 4, title: "Today", sub: "22 steps × B, C, D", view: "session" },
    { n: 5, title: "The gap", sub: "said → out of window → in memory", view: "gap" },
    { n: 6, title: "Tally", sub: "who used today's value", view: "tally" },
  ];
  const fresh = () => ({ memory: null, lines: [], checks: [], gap: null, tally: null, unavailable: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Which port is my local Postgres on?", termCount: 0, lastEvent: 0, caughtUp: false, ingest: [],
  };
  const ARMS = () => S.data?.all || ["B", "C", "D"];
  const ARM = (a) => (S.data?.arms || {})[a] || a;
  const SHORT = (a) => (S.data?.short || {})[a] || a;
  const MARKS = { ok: ["ok", "✓", "right"], stale: ["stale", "⟲", "the value from before today's change"],
    unknown: ["empty", "∅", "says it is not on file"], made: ["bad", "✗", "a wrong or made-up value"] };
  const mark = (g) => g ? `<span class="mark ${MARKS[g][0]}" title="${MARKS[g][2]}">${MARKS[g][1]}</span>` : "";
  const conv = (id) => (S.data?.conventions || []).find((c) => c.id === id) || {};
  const changed = () => (S.data?.conventions || []).filter((c) => c.new).sort((a, b) => a.changed_at - b.changed_at);

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>a new session, B, C, D</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, history, session, gap, tally, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${ARMS().map((a) => esc(S.world.arms?.[a]?.project || "")).join(" · ")}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes: the past sessions extracted, then a 22-step session with 24 MemoryRouter calls · everything it creates is prefixed <code>${esc(d.prefix || "mlu-sgp")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Session memory ends where persistent memory has not started yet</h1>
      <p class="lead">${esc(d.person?.display || "Rhea Tanaka")} works on the ledger service at ${esc(d.company || "Brindlewood Payments")} (fictional) with ${esc(d.assistant?.display || "Pair")}, the team's coding agent.
      Six past sessions settled the conventions. This morning she changes three of them — the database port, the test command, the reviewer — then ${esc(d.assistant?.display || "Pair")} works through a 22-step task.
      Every request carries the last ${d.window_steps || 6} steps (session memory) and what MemoryRouter attaches from MemoryLake (persistent memory). The arms differ only in <i>when</i> today's session is written:
      <b>B</b> when the session closes · <b>C</b> step by step, as it happens · <b>D</b> like C, and a step leaves the window only once memory holds it.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 24 calls on gpt-4o-mini per run, under 0.01 USD.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/session-memory-vs-persistent-memory-for-ai" target="_blank" rel="noopener">memorylake.ai › Session memory vs persistent memory</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = S.data || {}, w = S.world;
    return `<h1>One memory per arm</h1>
      <p class="lead">Each arm gets its own project and a boundary over it — the scope its MemoryRouter calls read. The same six past sessions go into all three, so before today they know the same things. Today, C and D write into their own project, so what C writes never reaches B or D.</p>
      ${w ? `<div class="card"><dl class="kv"><dt>${esc(d.person?.display)}</dt><dd class="mono">${esc(w.rhea)}</dd><dt>${esc(d.assistant?.display)}</dt><dd class="mono">${esc(w.pair)}</dd>
        ${ARMS().map((a) => `<dt>${esc(SHORT(a))}</dt><dd class="mono">${esc(w.arms?.[a]?.project)} · ${esc(w.arms?.[a]?.boundary)}</dd>`).join("")}</dl></div>` : running() ? waiting("") : ""}`;
  }

  function history() {
    const d = S.data || {}, m = S.memory;
    return `<h1>The past: ${(d.sessions || []).length} sessions, August to September</h1>
      <p class="lead">Each session is a conversation in each arm's project; extraction turns it into facts. Below: is every agreed value in each memory? Checked before today starts.</p>
      ${S.ingest.length ? `<div class="card small mono">${S.ingest.map((x) => esc(x)).join("<br>")}</div>` : ""}
      ${m ? `<div class="card"><h3 style="margin-top:0">Facts: ${ARMS().map((a) => `${a} ${m.facts[a]}`).join(" · ")}</h3>
        <table class="lint plain"><thead><tr><th>agreed value</th><th>session</th>${ARMS().map((a) => `<th>${a}</th>`).join("")}<th>today</th></tr></thead><tbody>
        ${(d.conventions || []).map((c) => `<tr><td><b>${esc(c.label)}</b>: ${esc(c.old)}</td><td class="small">${esc(c.from)}</td>${ARMS().map((a) => `<td>${m.held[c.id]?.[a] ? mark("ok") : "·"}</td>`).join("")}<td class="small">${c.new ? `changes to <b>${esc(c.new)}</b> at step ${c.changed_at}` : "unchanged"}</td></tr>`).join("")}</tbody></table></div>`
        : running() ? waiting("") : ""}
      <div class="grid2">${(d.sessions || []).map((s) => `<div class="card"><h3 style="margin-top:0">${esc(s.id)} · ${esc(s.topic)} <span class="muted small">${esc(s.at)}</span></h3>
        <div class="chat">${s.turns.slice(0, 2).map((t) => `<div class="msg ${t.who === "rhea" ? "user" : "bot"}"><span class="who">${t.who === "rhea" ? esc(d.person?.display) : esc(d.assistant?.display)}</span>${esc(t.text.replace(/^[^:]+:\s*/, ""))}</div>`).join("")}</div></div>`).join("")}</div>`;
  }

  function memCell(r) {
    if (!r?.memory || !Object.keys(r.memory).length) return "";
    return `<div class="small">${Object.entries(r.memory).map(([k, v]) => `<span class="tag ${v === "new" ? "ok" : "old"}">memory: ${esc(conv(k)[v] || "")}</span>`).join(" ")}</div>`;
  }

  function session() {
    if (!S.lines.length && !S.checks.length) return unavailable() || (running() ? waiting("Today") : empty("Today"));
    const d = S.data || {}, n = (d.live || []).length;
    const done = Math.max(0, ...S.lines.map((l) => l.n), ...S.checks.map((c) => c.step));
    const right = (a) => S.checks.filter((t) => t.arms[a]?.grade === "ok").length;
    const all = [...S.lines.map((l) => ({ ...l, kind: "line" })), ...S.checks.map((c) => ({ ...c, n: c.step, kind: "check" }))].sort((a, b) => a.n - b.n);
    return `<h1>Step ${done} of ${n} · right so far: ${ARMS().map((a) => `${a} ${right(a)}/${S.checks.length}`).join(" · ")}</h1>
      <p class="lead">Steps 1–3 change three conventions. Each step leaves the ${d.window_steps || 6}-step window ${d.window_steps || 6} steps later. At each ▶ the same request goes to B, C and D; under each answer, what that arm's memory said about today's change at that moment.</p>
      <div class="card"><table class="lint plain"><tbody>
        ${all.map((x) => x.kind === "line"
          ? `<tr class="${x.n <= 3 ? "hl" : ""}"><td class="small mono">${x.n}</td><td class="small mono">${sec(x.t)}</td><td class="small" colspan="${ARMS().length}">${esc(x.said)}</td></tr>`
          : `<tr class="hl"><td class="small mono"><b>${x.n}</b></td><td class="small mono">${sec(x.t)}</td><td colspan="${ARMS().length}"><b>▶ ${esc(x.q)}</b> ${esc(x.said)}
              <div class="small muted">${(d.questions?.[x.q]?.needs || []).map((k) => { const c = conv(k); return c.new ? `${esc(c.label)}: ${esc(c.old)} → <b>${esc(c.new)}</b> (step ${c.changed_at}, ${x.left_window?.[k] ? "out of the window" : "still in the window"})` : `${esc(c.label)}: ${esc(c.old)} (unchanged)`; }).join(" · ")}</div>
              <div class="grid3">${ARMS().map((a) => { const r = x.arms[a]; return r ? `<div class="msg bot ${r.grade === "ok" ? "" : "wrong"}"><span class="who">${esc(SHORT(a))} ${mark(r.grade)} · window ${r.window} · ${fmt(r.prompt_tokens)} tok</span>${esc(r.reply)}${memCell(r)}</div>` : ""; }).join("")}</div></td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function gap() {
    const g = S.gap;
    if (!g) return running() ? waiting("The gap") : empty("The gap");
    const rows = changed();
    const open = rows.filter((c) => (g.gaps[c.id]?.gap ?? -1) > 0);
    const heldD = (g.extra || []).filter((x) => x[1] > 0);
    return `<h1>${open.length}/${rows.length} changes left the window before they reached memory</h1>
      <p class="lead">For each change: when Rhea said it, when it dropped out of the ${S.data?.window_steps || 6}-step window, and when the new value first showed up in C's and D's memory (<code>fact list --query</code>, polled every 2 s). A positive gap is time when no request could see the new value — and memory still held the old one.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>change</th><th class="num">said</th><th class="num">left window</th><th class="num">in C's memory</th><th class="num">in D's memory</th><th class="num">gap (C)</th></tr></thead><tbody>
        ${rows.map((c) => { const x = g.gaps[c.id] || {}; return `<tr class="${(x.gap ?? -1) > 0 ? "hl" : ""}"><td><b>${esc(c.label)}</b> → ${esc(c.new)}</td><td class="num mono">${sec(x.said)}</td><td class="num mono">${sec(x.left)}</td><td class="num mono">${sec(x.in_c)}</td><td class="num mono">${sec(x.in_d)}</td><td class="num mono"><b>${x.gap === null || x.gap === undefined ? "—" : (x.gap > 0 ? "+" : "") + x.gap.toFixed(1) + "s"}</b></td></tr>`; }).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">What D paid</h3>${heldD.length ? `<ul class="small">${heldD.map((x) => `<li>step ${x[0]}: kept ${x[1]} step(s) past the window, ${x[2] > 0 ? "+" : ""}${fmt(x[2])} prompt tokens compared with C</li>`).join("")}</ul>` : '<p class="small muted">D never had to keep a step past the window.</p>'}</div>
      <div class="card"><h3 style="margin-top:0">C's memory after the session</h3>${rows.map((c) => `<p class="small"><b>${esc(c.label)}</b>: ${(g.c_now?.[c.id] || []).map((v) => `<span class="tag ${v === "new" ? "ok" : "old"}">${esc(c[v])}</span>`).join(" ") || "no fact"}</p>`).join("")}</div>`;
  }

  function tally() {
    const T = S.tally?.tallies;
    if (!T) return running() ? waiting("Tally") : empty("Tally");
    const nq = S.checks.length;
    return `<h1>After a change left the window: ${ARMS().map((a) => `${a} ${T[a].after.ok}/${T[a].n_after}`).join(" · ")}</h1>
      <p class="lead">${nq} questions in all; ${T.B.n_after} of them needed a value changed today whose step had already left the window. ⟲ = answered with the value from before the change — the memory's old copy, not a guess.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th>all questions</th><th>after a change left the window</th><th class="num">prompt tokens</th></tr></thead><tbody>
        ${ARMS().map((a) => { const t = T[a]; return `<tr class="${a === "B" ? "hl" : ""}"><td><b>${esc(ARM(a))}</b></td>
          <td>${mark("ok")} ${t.grades.ok} ${mark("stale")} ${t.grades.stale} ${mark("unknown")} ${t.grades.unknown} ${mark("made")} ${t.grades.made}</td>
          <td>${mark("ok")} ${t.after.ok} ${mark("stale")} ${t.after.stale} ${mark("unknown")} ${t.after.unknown} ${mark("made")} ${t.after.made}</td><td class="num">${fmt(t.prompt)}</td></tr>`; }).join("")}</tbody></table>
      <p class="small muted" style="margin-bottom:0">✓ every value right · ⟲ the value from before today's change · ∅ says it is not on file · ✗ a wrong or made-up value.</p></div>`;
  }

  function tryView() {
    const ready = !!S.world;
    const sugg = ["Which port is my local Postgres on?", "How do I run the ledger tests?", "Who reviews ledger pull requests?", "When can I deploy to staging?", "What prefix do ledger flags use?"];
    return `<h1>Ask one — the first question of a new session</h1>
      <p class="lead">No window: only what each arm's memory holds now. After a run, C and D have had today's session written in; B has not (it would be written when the session closes).</p>
      <div class="card"><label for="t-q">question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${sugg.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div id="try-log">${S.tries.slice().reverse().map((x) => `<h3>“${esc(x.question)}”</h3><div class="grid3">${x.rows.map((r) => `<div class="msg bot"><span class="who">${esc(SHORT(r.arm))} · ${fmt(r.prompt_tokens)} tok</span>${esc(r.reply)}</div>`).join("")}</div>`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: r.question, rows: r.rows });
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
  function resetView() { Object.assign(S, fresh()); S.ingest = []; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, conversations, facts, actors and projects?")) return;
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
    else if (k === "progress") { term("progress", ev.text.trim()); S.ingest.push(ev.text.trim()); if (S.caughtUp && S.view === "history") renderStage(); }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "memory") S.memory = d;
    else if (k === "step_line") S.lines = S.lines.filter((l) => l.n !== d.n).concat([d]);
    else if (k === "checkpoint") S.checks = S.checks.filter((c) => c.step !== d.step).concat([d]);
    else if (k === "gap") S.gap = d;
    else if (k === "tally") S.tally = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, conversations, facts, actors and projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: a new session's first question to B, C and D.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "step_line", "checkpoint", "gap", "tally",
    "router_unavailable", "answered", "facts_done", "done", "error"];
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
    show(state.status === "running" ? S.view : S.tally ? "tally" : "connect");
    subscribe();
  })();
})();

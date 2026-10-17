/* Condition-lapse memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, one boundary each", view: "setup" },
    { n: 3, title: "Four meetings", sub: "eight decisions", view: "meetings" },
    { n: 4, title: "What the app writes", sub: "C: condition checks", view: "checks" },
    { n: 5, title: "What memory says", sub: "each decision, today", view: "census" },
    { n: 6, title: "Ask the assistant", sub: "A, B and C", view: "ask" },
    { n: 7, title: "Score", sub: "answers per question", view: "score" },
  ];
  const fresh = () => ({ meetingsSeen: false, checks: null, census: null, rows: { A: [], B: [], C: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Is the Lisbon office opening on November 2?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const ITEMS = () => S.data?.decisions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, meetings, checks: checksView, census, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], stands: ["✗", "bad", "the plan given as standing"], made: ["✎", "bad", "says the lapsed condition was met — no record says so"],
    early: ["≫", "warn", "Yes / No before the deadline"], other: ["?", "warn", "no Yes / No / Unknown"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.verdict), n = (v) => live.filter((r) => !r.empty && r.verdict === v).length;
    return { calls: live.length, empty: live.filter((r) => r.empty).length, ok: n("ok"), stands: n("stands"), made: n("made"), early: n("early"), other: n("other") };
  }
  // the denominator is the graded answers: ∅ calls are listed, not graded (same rule as the CLI)
  const tline = (t) => `<b>${t.ok}</b> right of ${t.calls - t.empty} · <b class="${t.stands ? "bad-ink" : ""}">${t.stands}</b> plan given as standing · <b class="${t.made ? "bad-ink" : ""}">${t.made}</b> condition said to be met · <b>${t.early}</b> decided early · <b>${t.other}</b> other${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const COND = /\b(unless|provided|only if|as long as|if)\b/gi;
  const hiCond = (t) => esc(t).replace(COND, (m) => `<span class="stamp">${m}</span>`);
  const KIND = { failed: "condition failed", met: "condition met", lapsed: "lapsed in silence", pending: "deadline ahead", plain: "no condition" };

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.notes)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes (four meetings extracted one after another, then ~80 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-clm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The condition nobody mentioned again</h1>
      <p class="lead">${esc(d.org || "Halcrest")} (a fictional company) decides things in its weekly operations meeting, and many decisions carry a condition: “we open Lisbon on November 2, <i>provided the lease is signed by October 1</i>”.
      MemoryLake keeps the condition with the decision. When a later meeting says how it came out (“Q3 churn closed at 4.1 percent”), the assistant connects the two.
      When nobody mentions it again, the deadline passes in silence — and the assistant answers that the plan stands, sometimes adding that the lease was signed, which no record says.
      Three arms: A the meetings as remembered · B the same memory and a careful system prompt · C the same facts plus a condition check per decision that the app computes in code. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 80 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-meeting-notes-and-decisions" target="_blank" rel="noopener">memorylake.ai › AI memory for meeting notes and decisions</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Two projects, one boundary each</h1>
      <p class="lead">“notes” gets the four meetings. “checked” gets every fact MemoryLake extracted into “notes”, copied word for word, plus the app's condition checks — so the only difference between A and C is the checks. A and B read the same boundary; B only changes the system prompt.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>notes (A, B)</dt><dd class="mono small">${esc(w?.projects?.notes || "pending")}</dd><dt>checked (C)</dt><dd class="mono small">${esc(w?.projects?.checked || "pending")}</dd></dl></div>
        <div class="card"><h3 style="margin-top:0">In the meetings</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}</div></div>`;
  }

  const who = (k) => ((S.data?.people || []).find((p) => p.key === k) || {}).display || k;
  function meetings() {
    const d = S.data;
    if (!d || (!S.meetingsSeen && !S.world)) return running() ? waiting("Four meetings") : empty("Four meetings");
    return `<h1>Four meetings, eight decisions — two conditions are never mentioned again</h1>
      <p class="lead">Each meeting is one GROUP conversation, every line as its speaker, at the meeting's time; the next one goes in only when the last one is extracted. The highlighted words are the conditions.</p>
      <div class="grid2">${d.meetings.map((m) => `<div class="card"><div class="row spread"><b>${esc(m.id)} · ${esc(m.name)}</b><span class="mono small muted">${esc(m.at.slice(0, 10))}</span></div>
        ${m.lines.map(([k, t]) => `<div class="small" style="margin-top:6px"><b>${esc(who(k))}</b>: ${hiCond(t)}</div>`).join("")}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">Where each decision stands today (${esc(d.today)})</h3><table class="lint plain"><thead><tr><th>decision</th><th>condition</th><th>what happened</th><th>right answer</th></tr></thead><tbody>
        ${ITEMS().map((x) => `<tr class="${x.kind === "lapsed" ? "hl" : ""}"><td class="small"><b>${esc(x.what)}</b></td><td class="small">${esc(x.condition || "—")}</td><td class="small">${esc(KIND[x.kind])}</td><td class="small">${esc(x.truth)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function checksView() {
    if (!S.checks) return running() ? waiting("What the app writes") : empty("What the app writes");
    const c = S.checks;
    return `<h1>C: ${c.copied} extracted facts copied word for word, plus ${c.checks.length} condition checks</h1>
      <p class="lead">The app keeps its own decision register (the decision, its condition, its deadline) and an event log from the systems that settle conditions. A check is code: a logged event decides it; no event and the deadline has passed → lapsed; deadline still ahead → open. No model reads or writes anything here.</p>
      <div class="card"><h3 style="margin-top:0">Event log the app reads</h3>${c.events.map((e) => `<div class="copyrow"><span class="k">${esc(e.on)}</span><span class="small">${esc(e.summary)} <span class="muted">— ${esc(e.source)}</span></span></div>`).join("")}
        <p class="small muted" style="margin-bottom:0">Nothing about the Lisbon lease or the migration test: nobody logged them, and nobody said a word about them after the meeting.</p></div>
      <div class="card"><h3 style="margin-top:0">Condition checks — <code>fact add</code> into “checked”</h3>${c.checks.map((x) => `<div class="copyrow"><span class="k"><span class="lab ${x.status === "LAPSED" || x.status === "FAILED" ? "lab-bad" : ""}">${esc(x.status)}</span></span><span class="small">${esc(x.text)}</span></div>`).join("")}</div>`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What memory says") : empty("What memory says");
    const it = S.census.items;
    const out = (x) => { const r = it[x.id];
      if (x.kind === "failed" || x.kind === "met") return r.settled.length ? '<span class="small">recorded</span>' : '<span class="small bad-ink">not remembered</span>';
      if (x.kind === "lapsed") return r.settled.length ? '<span class="small bad-ink">a fact says it happened</span>' : '<span class="small bad-ink">none — the deadline passed in silence</span>';
      if (x.kind === "pending") return '<span class="small muted">none yet</span>'; return '<span class="small muted">—</span>'; };
    const lost = ITEMS().filter((x) => !it[x.id].about.length).length;
    return `<h1>“notes”: ${S.census.facts} facts — conditions kept, two outcomes never recorded</h1>
      <p class="lead">Every fact in the “notes” project, read for the eight decisions. Extraction keeps the condition with the decision. What is missing for Lisbon and the ticketing tool is not the condition but any word about how it came out.${lost ? ` ${lost} decision(s) have no fact at all this run.` : ""}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>decision</th><th>what memory holds</th><th>outcome in memory</th></tr></thead><tbody>
      ${ITEMS().map((x) => `<tr class="${x.kind === "lapsed" ? "hl" : ""}"><td class="small"><b>${esc(x.what)}</b><br><span class="muted">${esc(KIND[x.kind])}</span></td><td>${it[x.id].about.slice(0, 3).map((f) => `<div class="small">· ${hiCond(f)}</div>`).join("") || '<span class="small muted">nothing about it</span>'}</td><td>${out(x)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The operations assistant — A: ${t.A.ok} of ${t.A.calls - t.A.empty} right · B: ${t.B.ok} of ${t.B.calls - t.B.empty} · C: ${t.C.ok} of ${t.C.calls - t.C.empty}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7, answer starting with Yes, No or Unknown, plus one call without a boundary as the ∅ baseline. B adds to the system prompt: <i>${esc(S.data?.careful || "")}</i></p>
      ${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>right answer</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${ITEMS().map((x) => { const rs = S.rows[a].filter((r) => r.item === x.id); const bad = rs.find((r) => r.verdict && r.verdict !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(x.ask)}</td><td class="small">${esc(x.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score, bi = sc.by_item || {};
    const cellc = (x) => x ? `${x.ok}/${x.n}${x.made ? ` <span class="bad-ink">✎${x.made}</span>` : ""}${x.empty ? ` <span class="muted">∅${x.empty}</span>` : ""}` : "";
    const g = (a) => `${sc.answers[a].ok}/${sc.answers[a].calls - sc.answers[a].empty}`;
    return `<h1>Right answers: A ${g("A")} · B ${g("B")} · C ${g("C")}</h1>
      <p class="lead">✓ right · ✗ the plan given as standing (or a standing plan called off) · ✎ “Yes”, and the reply says the lapsed condition was met — no record says so · ≫ Yes / No before the deadline · ? no Yes / No / Unknown · ∅ MemoryRouter attached nothing (listed, not graded).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th>answers (${ITEMS().length} × ${RUNS()})</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr class="${a === "C" ? "hl" : ""}"><td><b>${esc(LABEL(a))}</b></td><td>${tline(sc.answers[a])}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Answers right, per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${ITEMS().map((x) => `<tr class="${x.kind === "lapsed" ? "hl" : ""}"><td class="small">${esc(x.what)} <span class="muted">(${esc(KIND[x.kind])})</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[x.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">A condition is settled by something happening — or by its deadline passing while nothing happens. Memory can only hold the first kind: a meeting that never mentions the lease leaves nothing to remember.
        The app has the register and the calendar, so it writes the check (C); the model only reads it.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C</h1>
      <p class="lead">Any question to the operations assistant, once through each arm. Graded when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${ITEMS().map((x) => `<button class="chip" data-q="${esc(x.ask)}">${esc(x.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Priya → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.verdict === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.verdict ? `${markOf(r)} ${esc(MARKS[r.verdict][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.truth ? `<p class="small muted">right answer: ${esc(x.truth)}</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, truth: r.truth });
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
    if (!confirm("Delete the demo's boundaries, conversations, actors (their facts first) and the two projects?")) return;
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
    else if (k === "meetings") S.meetingsSeen = true;
    else if (k === "checks") S.checks = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the two projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the operations assistant, through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "meetings", "checks", "census", "ask_row", "arm_done",
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

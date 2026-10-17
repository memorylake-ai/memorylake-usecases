/* Plain-recap memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const COPIES = ["said", "recap", "logged"];
  const SHORT = { said: "A", recap: "B", logged: "C" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three copies, one boundary each", view: "setup" },
    { n: 3, title: "Four meetings", sub: "five weeks, five ways to say it", view: "calls" },
    { n: 4, title: "The lab log", sub: "C only · fact add", view: "log" },
    { n: 5, title: "What memory says", sub: "each item, today", view: "census" },
    { n: 6, title: "Ask the assistant", sub: "through each boundary", view: "ask" },
    { n: 7, title: "Score", sub: "answers per question", view: "score" },
  ];
  const fresh = () => ({ callsSeen: false, log: null, census: null, rows: { said: [], recap: [], logged: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Has anyone outside the lab reviewed our stats plan?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const ITEMS = () => S.data?.topics || [];
  const LABEL = (c) => (S.data?.copies || {})[c] || c;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, calls, log: logView, census, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "today's answer"], stale: ["✗", "bad", "the 09-01 “not yet”, given as today's answer"], part: ["◐", "warn", "half"],
    other: ["?", "warn", "something else"], made: ["✎", "bad", "made up access we do not have"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.verdict), n = (v) => live.filter((r) => !r.empty && r.verdict === v).length;
    return { calls: live.length, empty: live.filter((r) => r.empty).length, ok: n("ok"), stale: n("stale"), part: n("part"), other: n("other") + n("made") };
  }
  const tline = (t) => `<b>${t.ok}</b> right · <b class="${t.stale ? "bad-ink" : ""}">${t.stale}</b> stale “not yet” · <b>${t.other}</b> other${t.empty ? ` · <b>${t.empty}</b> ∅` : ""} <span class="muted">of ${t.calls}</span>`;
  const NOTYET = /\b(have not replicated|is not approved|has not arrived|is not posted|nobody outside the lab|no access)\b/gi;
  const hiNot = (t) => esc(t).replace(NOTYET, (m) => `<span class="stamp">${m}</span>`);
  const short = (q) => q.length > 64 ? q.slice(0, 62) + "…" : q;

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.said)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes (four meetings extracted one after another, then ~70 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-prc")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Saying it plainly in the meeting isn't enough — the event has to become a fact</h1>
      <p class="lead">${esc(d.org || "Halvern Lab")} (a fictional research group) is replicating a published effect. The kickoff meeting ends with the PI's “where we stand”: not replicated, ethics amendment not approved, sequencing data not arrived, preregistration not posted, nobody outside the lab has reviewed the stats plan, no access to the 2019 cohort.
      Five weeks later all but the last have stopped being true, and every change was mentioned in a later meeting — said plainly, second-hand, as a question and answer, taken for granted, implied.
      The 09-01 “not yet” goes away only if extraction turns the later mention into a fact; whether it does is not a matter of how plainly it was said. Three copies of the same four meetings: A as said · B the same meetings, each closed by one plain “Status as of today:” line · C as said, plus a lab log the app writes with <code>fact add</code>. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 70 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-knowledge-management" target="_blank" rel="noopener">memorylake.ai › AI memory for knowledge management</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Three copies of the same project memory, one boundary each</h1>
      <p class="lead">Each copy is its own project, so no copy's answers come from another's facts. The boundary covers the copy's project — what the research assistant reads through MemoryRouter.</p>
      <div class="grid2"><div class="card"><dl class="kv">${COPIES.map((c) => `<dt>${esc(LABEL(c))}</dt><dd class="mono small">${esc(w?.projects?.[c] || "pending")}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">In the meetings</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}</div></div>`;
  }

  const who = (k) => ((S.data?.people || []).find((p) => p.key === k) || {}).display || k;
  function calls() {
    const d = S.data;
    if (!d || (!S.callsSeen && !S.world)) return running() ? waiting("Four meetings") : empty("Four meetings");
    return `<h1>Four meetings over five weeks — the recap on day one, the changes after</h1>
      <p class="lead">Every copy gets the same four meetings: one GROUP conversation each, every line as its speaker, at the meeting's time. The next meeting goes in only when the last one is extracted, so a later meeting can revise what an earlier one left. In B each meeting ends with one more line from the PI — the plain status. The highlighted words are the “not yet”s.</p>
      <div class="grid2">${d.meetings.map((m) => `<div class="card"><div class="row spread"><b>${esc(m.id)} · ${esc(m.name)}</b><span class="mono small muted">${esc(m.at.slice(0, 10))}</span></div>
        ${m.lines.map(([k, t]) => `<div class="small" style="margin-top:6px"><b>${esc(who(k))}</b>: ${hiNot(t)}</div>`).join("")}
        ${m.recap ? `<div class="small recap" style="margin-top:6px"><span class="lab">B only</span> <b>${esc(who("pi"))}</b>: ${esc(m.recap)}</div>` : ""}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">Where each item stands today, and how the change was said</h3><table class="lint plain"><thead><tr><th>item</th><th>09-01 recap</th><th>today</th><th>said as</th></tr></thead><tbody>
        ${ITEMS().filter((t) => t.said).map((t) => `<tr><td class="small"><b>${esc(t.what)}</b></td><td class="small">“${esc(t.said)}”</td><td class="small">${esc(t.truth)}</td><td class="small">${t.closed ? `${esc(t.closed)} · ${esc(t.form)}` : `<span class="muted">${esc(t.form)}</span>`}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function logView() {
    if (!S.log) return running() ? waiting("The lab log") : empty("The lab log");
    return `<h1>C: ${S.log.entries.length} lab-log facts — the 09-01 “not yet”s stay beside them</h1>
      <p class="lead">C gets the lab log an app writes from the lab's tracker: one dated fact per event, <code>fact add</code>, topic and date in metadata. Nothing is deleted. A and B get nothing here — B's fix is in the meetings themselves.</p>
      <div class="card"><h3 style="margin-top:0">Lab log (C) — <code>fact add</code></h3>${S.log.entries.map((e) => `<div class="copyrow"><span class="k">${esc(e.topic)}</span><span class="small">${esc(e.body)}</span></div>`).join("")}</div>`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What memory says") : empty("What memory says");
    const c = S.census.copies, closed = ITEMS().filter((t) => t.closed);
    const stale = (k) => closed.filter((t) => c[k].items[t.id].not_yet.length).length;
    const cell = (k, t) => { const r = c[k].items[t.id];
      const ny = r.not_yet.slice(0, 2).map((f) => `<div class="small ${t.closed ? "bad-ink" : ""}">${t.closed ? "✗" : "✓"} ${esc(f)}</div>`).join("");
      const ev = r.event.slice(0, 1).map((f) => `<div class="small">· ${esc(f)}</div>`).join("");
      return ny + ev || '<span class="small muted">nothing about it</span>'; };
    return `<h1>Ended “not yet” still in memory — A: ${stale("said")} of ${closed.length} · B: ${stale("recap")} · C: ${stale("logged")}</h1>
      <p class="lead">Every fact in each copy's project, read for the six items. ✗ a “not yet” that has ended · ✓ the cohort “no access”, still true · · a fact about what happened.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>item · how the change was said</th>${COPIES.map((k) => `<th>${esc(LABEL(k))}</th>`).join("")}</tr></thead><tbody>
      ${ITEMS().filter((t) => t.said).map((t) => `<tr class="${t.closed && c.said.items[t.id].not_yet.length ? "hl" : ""}"><td class="small"><b>${esc(t.what)}</b><br>${esc(t.form)}<br><span class="muted">${esc(t.truth)}</span></td>${COPIES.map((k) => `<td>${cell(k, t)}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>`;
  }

  function askView() {
    const all = COPIES.flatMap((c) => S.rows[c]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(COPIES.map((c) => [c, tally(S.rows[c])]));
    return `<h1>The research assistant — A: ${t.said.ok} of ${t.said.calls} right · B: ${t.recap.ok} · C: ${t.logged.ok}</h1>
      <p class="lead">Each question through the copy's boundary, ${RUNS()} answers each at temperature 0.7, plus one call without a boundary as the ∅ baseline. The system prompt gives today's date.</p>
      ${COPIES.map((c) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(c))}</h3><table class="lint plain"><thead><tr><th>question</th><th>today</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${ITEMS().map((it) => { const rs = S.rows[c].filter((r) => r.item === it.id); const bad = rs.find((r) => r.verdict && r.verdict !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(it.ask)}</td><td class="small">${esc(it.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[c])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score, bi = sc.by_item || {};
    const cellc = (x) => x ? `${x.ok}/${x.n}${x.stale ? ` <span class="bad-ink">✗${x.stale}</span>` : ""}${x.empty ? ` <span class="muted">∅${x.empty}</span>` : ""}` : "";
    const n = ITEMS().filter((t) => t.closed).length, R = (k) => `${sc.answers[k].ok}/${sc.answers[k].calls - sc.answers[k].empty}`;
    return `<h1>Right answers: A ${R("said")} · B ${R("recap")} · C ${R("logged")}</h1>
      <p class="lead">✓ today's answer · ✗ the 09-01 “not yet” given as today's answer · ✎ cohort access made up · ? other · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>copy</th><th class="num">ended “not yet” in memory</th><th>assistant's answers (${ITEMS().length} × ${RUNS()})</th></tr></thead><tbody>
        ${COPIES.map((k) => `<tr class="${k === "recap" ? "hl" : ""}"><td><b>${esc(LABEL(k))}</b></td><td class="num">${sc.memory[k].stale}/${n}</td><td>${tline(sc.answers[k])}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Answers right, per question</h3><table class="lint plain"><thead><tr><th>question</th>${COPIES.map((c) => `<th class="num">${SHORT[c]}</th>`).join("")}</tr></thead><tbody>
        ${ITEMS().map((it) => `<tr><td class="small">${esc(short(it.ask))} <span class="muted">${it.id === "cohort" ? "(still true: “no access” is right)" : it.id === "grant" ? "(control)" : esc(it.form)}</span></td>${COPIES.map((c) => `<td class="num">${cellc((bi[it.id] || {})[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">A “not yet” carries the day it was said, never the day it stopped being true. Extraction rewrites it in place when a later meeting says the same thing plainly; second-hand, question-and-answer, taken-for-granted and implied mentions leave it standing.
        B adds one plain status line at the end of each meeting, and extraction does the rewriting. C adds the event as its own fact; the old “not yet” stays beside it.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C</h1>
      <p class="lead">Any question to the research assistant, once through each copy. Graded when it is one of the seven.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${ITEMS().map((it) => `<button class="chip" data-q="${esc(it.ask)}">${esc(it.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Noor → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.verdict === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(LABEL(r.copy))} · ${r.empty ? "∅ nothing attached" : r.verdict ? `${markOf(r)} ${esc(MARKS[r.verdict][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.truth ? `<p class="small muted">today: ${esc(x.truth)}</p>` : ""}`).join("")}</div></div>`;
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
    if (!confirm("Delete the demo's boundaries, conversations, actors (their facts first) and the three projects?")) return;
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
    else if (k === "meetings") S.callsSeen = true;
    else if (k === "lab_log") S.log = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows[d.copy].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the research assistant, through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "meetings", "lab_log", "census", "ask_row", "arm_done",
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

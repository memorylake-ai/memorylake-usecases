/* Bare reading memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "V", "H"];
  const COPY = { A: "said", V: "verdict", H: "harness" };
  const COPIES = ["said", "verdict", "harness"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "Two meetings", sub: "six gates, then the readings", view: "meetings" },
    { n: 4, title: "Harness readings", sub: "H: the numbers, from the source", view: "harness" },
    { n: 5, title: "What memory holds", sub: "gates vs readings", view: "census" },
    { n: 6, title: "Ask the assistant", sub: "three memories, same questions", view: "ask" },
    { n: 7, title: "Score", sub: "ship questions and figures", view: "score" },
  ];
  const fresh = () => ({ meetingsSeen: false, harness: null, census: null, rows: { A: [], V: [], H: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Does the new reranker ship?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const ITEMS = () => S.data?.items || [];
  const QS = () => S.data?.questions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, V and H</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, meetings, harness: harnessView, census, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right, and this memory holds the reading (or the decision)"], luck: ["◐", "warn", "right word, but this memory holds neither the reading nor the decision"],
    wrong: ["✗", "bad", "wrong"], unknown: ["?", "warn", "Unknown"], made: ["✎", "bad", "a figure memory does not hold"], other: ["–", "warn", "no verdict or figure"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const live = rows.filter((r) => r.grade && !r.empty), dec = live.filter((r) => r.kind === "decision"), fig = live.filter((r) => r.kind === "figure");
    const c = (xs, g) => xs.filter((r) => r.grade === g).length;
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, dec_n: dec.length, dec_ok: c(dec, "ok"), dec_luck: c(dec, "luck"),
      dec_wrong: c(dec, "wrong"), dec_unknown: c(dec, "unknown"), fig_n: fig.length, fig_ok: c(fig, "ok"), fig_made: c(fig, "made"), fig_unknown: c(fig, "unknown") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `ship questions: right from memory <b>${t.dec_ok}</b>/${t.dec_n} · right word, unsettled <b>${t.dec_luck}</b> · Unknown <b>${t.dec_unknown}</b> · wrong <b class="${t.dec_wrong ? "bad-ink" : ""}">${t.dec_wrong}</b>
    · figures right <b>${t.fig_ok}</b>/${t.fig_n} · invented <b class="${t.fig_made ? "bad-ink" : ""}">${t.fig_made}</b>${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const NUM = /(\b\d[\d,]*(?:\.\d+)?\s?(?:percent|cents?|milliseconds)?)/g;
  const hiNum = (t) => esc(t).replace(NUM, (m) => `<span class="stamp">${m}</span>`);
  const who = (k) => ((S.data?.people || []).find((p) => p.key === k) || {}).display || k;

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.said)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes (two meetings × two copies extracted, then ~80 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-brm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The gate is remembered. The number that decides it is not.</h1>
      <p class="lead">${esc(d.org || "Quillfold")} (a fictional company) runs an AI support agent. In September the team set six release gates — “we ship the new reranker <i>only if recall at 10 is at least 0.85</i>”. In October the eval sync read out the results the way people do: “Recall at 10 for the new reranker came in at 0.81.”
      MemoryLake's extraction keeps every gate and drops every one of those bare readings, so the release assistant can only see the gate. Three memories of the same meetings: <b>A</b> the readings as said · <b>V</b> each reading said with its verdict (the extraction control) · <b>H</b> A's facts plus the six readings written by the app straight from the eval harness. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 80 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/why-summarization-buffers-lose-critical-agent-context" target="_blank" rel="noopener">memorylake.ai › Why summarization buffers lose the details agents need</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Three projects, one boundary each</h1>
      <p class="lead">“said” and “verdict” each get the same two meetings, with their own four people; only the way the readings are said differs. “harness” gets every fact MemoryLake extracted into “said”, copied word for word, plus the eval harness's six readings.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>said (A)</dt><dd class="mono small">${esc(w?.projects?.said || "pending")}</dd><dt>verdict (V)</dt><dd class="mono small">${esc(w?.projects?.verdict || "pending")}</dd><dt>harness (H)</dt><dd class="mono small">${esc(w?.projects?.harness || "pending")}</dd></dl></div>
        <div class="card"><h3 style="margin-top:0">At both meetings</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}</div></div>`;
  }

  function meetings() {
    const d = S.data;
    if (!d || (!S.meetingsSeen && !S.world)) return running() ? waiting("Two meetings") : empty("Two meetings");
    return `<h1>Six gates in September, six readings in October — said two ways</h1>
      <p class="lead">Each meeting is one GROUP conversation, every line as its speaker at the meeting's time; the eval sync goes in once the gates meeting is extracted. “said” hears the readings bare; “verdict” hears the same numbers with the bar and the decision.</p>
      <div class="card"><div class="row spread"><b>${esc(d.gates_meeting.name)}</b><span class="mono small muted">${esc(d.gates_meeting.at.slice(0, 10))} · both copies</span></div>
        ${ITEMS().map((x) => `<div class="small" style="margin-top:6px"><b>${esc(who(x.speaker))}</b>: ${esc(x.gate)}</div>`).join("")}</div>
      <div class="card"><div class="row spread"><b>${esc(d.evals_meeting.name)}</b><span class="mono small muted">${esc(d.evals_meeting.at.slice(0, 10))}</span></div>
        <table class="lint plain"><thead><tr><th>“said” (A) hears</th><th>“verdict” (V) hears</th><th>right answer</th></tr></thead><tbody>
        ${ITEMS().map((x) => `<tr><td class="small"><b>${esc(who(x.speaker))}</b>: ${hiNum(x.bare)}</td><td class="small">${hiNum(x.verdict)}</td><td class="small">${esc(x.truth)}</td></tr>`).join("")}
        </tbody></table></div>`;
  }

  function harnessView() {
    if (!S.harness) return running() ? waiting("Harness readings") : empty("Harness readings");
    const h = S.harness;
    return `<h1>H: ${h.copied} extracted facts copied — then the six readings, from the harness</h1>
      <p class="lead">The numbers already exist before anyone reads them out: the eval harness produced them. The app writes each one with <code>fact add</code> — the metric and the number, with no bar and no verdict. Deciding is still the model's job: it has to compare each reading with the gate that memory kept.</p>
      <div class="card"><h3 style="margin-top:0">“harness” — <code>fact add</code>, one fact per reading</h3>${h.readings.map((r) => `<div class="copyrow"><span class="small">${hiNum(r)}</span></div>`).join("")}
        <p class="small muted" style="margin-bottom:0">Source: ${esc(h.source)}. A and H differ only by these six facts.</p></div>`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What memory holds") : empty("What memory holds");
    const c = S.census, n = ITEMS().length;
    const cell = (cp, x) => { const r = c[cp].items[x.id];
      const g = r.gate.length ? '<span class="lab">gate ✓</span>' : '<span class="lab lab-bad">gate –</span>';
      const rd = r.reading.length ? `<div class="small">${hiNum(r.reading[0])}</div>`
        : (r.decided || []).length ? `<div class="small warn-ink">the decision, without the number: ${esc(r.decided[0])}</div>`
        : r.on_actor.length ? `<div class="small warn-ink">only on a speaker: ${hiNum(r.on_actor[0])}</div>` : '<div class="small bad-ink">neither the number nor the decision</div>';
      return `<td>${g}${rd}</td>`; };
    return `<h1>Readings in memory: said ${c.said.readings}/${n} · verdict ${c.verdict.readings}/${n} · harness ${c.harness.readings}/${n}</h1>
      <p class="lead">Every fact in each project, read for the six gates. The gates are there. In “said”, the numbers that decide them usually are not: a reading said on its own — no bar, no verdict — rarely becomes a fact. Said with its verdict, the decision is kept, sometimes without the number. H's readings are the app's harness facts.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>gate</th>${COPIES.map((cp) => `<th>${cp} <span class="muted">(${c[cp].facts} facts)</span></th>`).join("")}</tr></thead><tbody>
      ${ITEMS().map((x) => `<tr class="${c.said.items[x.id].reading.length ? "" : "hl"}"><td class="small"><b>${esc(x.what)}</b><br><span class="muted">bar ${esc(x.bar)} · reading ${esc(x.value)} → ${esc(x.truth)}</span></td>${COPIES.map((cp) => cell(cp, x)).join("")}</tr>`).join("")}
      </tbody></table></div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The release assistant — ship questions right from memory: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].dec_ok}/${t[a].dec_n}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7, plus one call without a boundary as the ∅ baseline. ◐ marks a right Yes / No from a memory that holds neither the reading nor the decision — “No … recall is not confirmed to be at least 0.85”: right by luck.</p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>right answer</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows[a].filter((r) => r.item === q.id); const bad = rs.find((r) => r.grade && r.grade !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(q.ask)}</td><td class="small">${esc(q.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>Ship questions answered from memory: ${ARMS.map((a) => `${a} ${A[a].dec_ok}/${A[a].dec_n}`).join(" · ")}</h1>
      <p class="lead">${ITEMS().length * RUNS()} ship answers and ${(QS().length - ITEMS().length) * RUNS()} figure answers per memory. A right Yes / No counts only when that memory holds the reading or the decision; a right word from a memory with neither is ◐ — right by luck.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>ship: right, from memory</th><th class="num">right word, unsettled</th><th class="num">Unknown</th><th class="num">wrong</th><th>figures right</th><th class="num">invented</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].dec_ok}/${A[a].dec_n}</b> <span class="muted">${pct(A[a].dec_ok, A[a].dec_n)}%</span>${bar(A[a].dec_ok, A[a].dec_n, "g2")}</td>
          <td class="num">${A[a].dec_luck}</td><td class="num">${A[a].dec_unknown}</td><td class="num"><b class="${A[a].dec_wrong ? "bad-ink" : ""}">${A[a].dec_wrong}</b></td>
          <td><b>${A[a].fig_ok}/${A[a].fig_n}</b>${bar(A[a].fig_ok, A[a].fig_n, "g1")}</td><td class="num"><b class="${A[a].fig_made ? "bad-ink" : ""}">${A[a].fig_made}</b></td></tr>`).join("")}</tbody></table>
        ${ARMS.some((a) => A[a].empty) ? `<p class="small muted" style="margin-bottom:0">∅ (MemoryRouter attached nothing; listed, not graded): ${ARMS.map((a) => `${a} ${A[a].empty}`).join(" · ")}</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.what)} <span class="muted">→ ${esc(q.truth)}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ right, and this memory holds the reading (or the decision) · ◐ right word, memory holds neither · ✗ wrong · ? Unknown · ✎ a figure memory does not hold · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, V and H</h1>
      <p class="lead">Any question to the release assistant, once through each memory. Graded when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Tomas → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
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
    if (!confirm("Delete the demo's boundaries, conversations, eight actors (their facts first) and the three projects?")) return;
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
    else if (k === "harness") S.harness = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the release assistant, through A, V and H.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "meetings", "harness", "census", "ask_row", "arm_done", "example",
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

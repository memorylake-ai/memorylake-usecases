/* No-record memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C", "D"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "Four standups", sub: "eight decisions", view: "meetings" },
    { n: 4, title: "Branch the memory", sub: "C and D: condition checks", view: "checks" },
    { n: 5, title: "What memory says", sub: "each decision, today", view: "census" },
    { n: 6, title: "Ask the assistant", sub: "four strategies", view: "ask" },
    { n: 7, title: "A/B score", sub: "two graders, same replies", view: "score" },
  ];
  const fresh = () => ({ meetingsSeen: false, checks: null, census: null, rows: { A: [], B: [], C: [], D: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Is the Postgres 16 upgrade going ahead on November 3?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const ITEMS = () => S.data?.decisions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B, C and D</small></span></button>`;
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
  const MARKS = { ok: ["✓", "ok", "right and grounded"], stands: ["✗", "bad", "the plan given as standing"],
    made: ["✎", "bad", "states how a condition came out — memory holds no fact about it"],
    early: ["≫", "warn", "Yes / No before the deadline"], other: ["?", "warn", "no Yes / No / Unknown"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.g2][1]}" title="${esc(MARKS[r.g2][2])}${r.claim ? `: “${esc(r.claim)}”` : ""}">${MARKS[r.g2][0]}</span>`;
  function tally(rows) {
    const all = rows.filter((r) => r.verdict), live = all.filter((r) => !r.empty);
    return { calls: all.length, n: live.length, empty: all.length - live.length, g1: live.filter((r) => r.verdict === "ok").length,
      g2: live.filter((r) => r.g2 === "ok").length, right_made: live.filter((r) => r.verdict === "ok" && !r.grounded).length };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n) : 0;
  // the denominator is the graded answers: ∅ calls are listed, not graded (same rule as the CLI)
  const tline = (t) => `G1 verdict only <b>${t.g1}</b>/${t.n} · G2 grounded <b>${t.g2}</b>/${t.n} · <b class="${t.right_made ? "bad-ink" : ""}">${t.right_made}</b> right verdict with an invented reason${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const COND = /\b(unless|provided|only if|as long as|if)\b/gi;
  const hiCond = (t) => esc(t).replace(COND, (m) => `<span class="stamp">${m}</span>`);
  const KIND = { failed: "condition failed", met: "condition met", lapsed: "lapsed, never recorded", pending: "deadline ahead", plain: "no condition" };

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.notes)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes (four standups extracted one after another, then ~110 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-nrm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“No record that it happened” is not “it did not happen”</h1>
      <p class="lead">${esc(d.org || "Tarnwick")} (a fictional company) is A/B testing memory strategies for its release assistant. The platform team's standups are full of conditional decisions: “the Postgres 16 upgrade goes ahead on November 3, <i>as long as the staging rehearsal passes by October 9</i>”.
      Three of those conditions were never mentioned again. The right answer is “No — the deadline passed with no record that the rehearsal passed”. The assistant often says “No — the staging rehearsal did not pass”: the right verdict, with a reason nothing records.
      Four strategies (A the standups · B a careful system prompt · C the app's condition checks · D the same checks saying the outcome was never recorded either way), and two graders over the same replies: <b>G1</b> the verdict only, <b>G2</b> the verdict and no invented outcome. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 110 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ab-testing-agent-memory-strategies" target="_blank" rel="noopener">memorylake.ai › A/B testing agent memory strategies</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Three projects, one boundary each — two branches of the same memory</h1>
      <p class="lead">“notes” gets the four standups. “checked” and “noted” each get every fact MemoryLake extracted into “notes”, copied word for word, plus the app's condition checks — so A and C differ only by the checks, and C and D only by how three of them are worded. A and B read the same boundary; B only changes the system prompt.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>notes (A, B)</dt><dd class="mono small">${esc(w?.projects?.notes || "pending")}</dd><dt>checked (C)</dt><dd class="mono small">${esc(w?.projects?.checked || "pending")}</dd><dt>noted (D)</dt><dd class="mono small">${esc(w?.projects?.noted || "pending")}</dd></dl></div>
        <div class="card"><h3 style="margin-top:0">At the standups</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}</div></div>`;
  }

  const who = (k) => ((S.data?.people || []).find((p) => p.key === k) || {}).display || k;
  function meetings() {
    const d = S.data;
    if (!d || (!S.meetingsSeen && !S.world)) return running() ? waiting("Four standups") : empty("Four standups");
    return `<h1>Four standups, eight decisions — three conditions are never mentioned again</h1>
      <p class="lead">Each standup is one GROUP conversation, every line as its speaker, at the standup's time; the next one goes in only when the last one is extracted. The highlighted words are the conditions.</p>
      <div class="grid2">${d.meetings.map((m) => `<div class="card"><div class="row spread"><b>${esc(m.id)} · ${esc(m.name)}</b><span class="mono small muted">${esc(m.at.slice(0, 10))}</span></div>
        ${m.lines.map(([k, t]) => `<div class="small" style="margin-top:6px"><b>${esc(who(k))}</b>: ${hiCond(t)}</div>`).join("")}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">Where each decision stands today (${esc(d.today)})</h3><table class="lint plain"><thead><tr><th>decision</th><th>condition</th><th>what happened</th><th>right answer</th></tr></thead><tbody>
        ${ITEMS().map((x) => `<tr class="${x.kind === "lapsed" ? "hl" : ""}"><td class="small"><b>${esc(x.what)}</b></td><td class="small">${esc(x.condition || "—")}</td><td class="small">${esc(KIND[x.kind])}</td><td class="small">${esc(x.truth)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function checksView() {
    if (!S.checks) return running() ? waiting("Branch the memory") : empty("Branch the memory");
    const c = S.checks, lapsed = (x) => x.status === "LAPSED" || x.status === "NOT RECORDED";
    const row = (x) => `<div class="copyrow"><span class="k"><span class="lab ${lapsed(x) || x.status === "FAILED" ? "lab-bad" : ""}">${esc(x.status)}</span></span><span class="small">${esc(x.text)}</span></div>`;
    return `<h1>Two branches: ${c.copied} extracted facts copied into each, plus ${c.checks.length} condition checks</h1>
      <p class="lead">The app keeps its own decision register (the decision, its condition, its deadline) and an event log from the systems that settle conditions. A check is code: a logged event decides it; no event and the deadline has passed → lapsed; deadline still ahead → open. C and D get the same checks; only the three lapsed ones are worded differently.</p>
      <div class="card"><h3 style="margin-top:0">Event log the app reads</h3>${c.events.map((e) => `<div class="copyrow"><span class="k">${esc(e.on)}</span><span class="small">${esc(e.summary)} <span class="muted">— ${esc(e.source)}</span></span></div>`).join("")}
        <p class="small muted" style="margin-bottom:0">Nothing about the staging rehearsal, the client-app migration or the cost review: nobody logged them, and nobody mentioned them after the standup.</p></div>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">C · “checked” — <code>fact add</code></h3>${c.checks.map(row).join("")}</div>
        <div class="card"><h3 style="margin-top:0">D · “noted” — the lapsed checks, reworded</h3>${(c.noted || []).filter(lapsed).map(row).join("")}
          <p class="small muted" style="margin-bottom:0">MET / FAILED / OPEN checks are the same as in C.</p></div></div>`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What memory says") : empty("What memory says");
    const it = S.census.items;
    const out = (x) => { const r = it[x.id];
      if (x.kind === "failed" || x.kind === "met") return r.settled.length ? '<span class="small">recorded</span>' : '<span class="small bad-ink">not remembered</span>';
      if (x.kind === "lapsed") return r.settled.length ? '<span class="small bad-ink">a fact says it happened</span>' : '<span class="small bad-ink">none — the deadline passed in silence</span>';
      if (x.kind === "pending") return '<span class="small muted">none yet</span>'; return '<span class="small muted">—</span>'; };
    const lost = ITEMS().filter((x) => !it[x.id].about.length).length;
    return `<h1>“notes”: ${S.census.facts} facts — conditions kept, three outcomes never recorded</h1>
      <p class="lead">Every fact in the “notes” project, read for the eight decisions. What is missing for the Postgres upgrade, the legacy auth switch-off and the CI runners is any word about how the condition came out — in either direction. A reply that says “the rehearsal did not pass” is stating something memory does not hold.${lost ? ` ${lost} decision(s) have no fact at all this run.` : ""}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>decision</th><th>what memory holds</th><th>outcome in memory</th></tr></thead><tbody>
      ${ITEMS().map((x) => `<tr class="${x.kind === "lapsed" ? "hl" : ""}"><td class="small"><b>${esc(x.what)}</b><br><span class="muted">${esc(KIND[x.kind])}</span></td><td>${it[x.id].about.slice(0, 3).map((f) => `<div class="small">· ${hiCond(f)}</div>`).join("") || '<span class="small muted">nothing about it</span>'}</td><td>${out(x)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The release assistant — right verdicts with invented reasons: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].right_made}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7, answer starting with Yes, No or Unknown, plus one call without a boundary as the ∅ baseline. ✎ marks a reply that states how a condition came out when memory holds no fact about it — whatever its first word. B adds to the system prompt: <i>${esc(S.data?.careful || "")}</i></p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>right verdict</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${ITEMS().map((x) => { const rs = S.rows[a].filter((r) => r.item === x.id); const bad = rs.find((r) => r.verdict && r.g2 !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(x.ask)}</td><td class="small">${esc(x.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div>${bad && bad.claim ? `<div class="small bad-ink">G2: claims “${esc(bad.claim)}”${bad.verdict === "ok" ? " — G1 scores this right" : ""}</div>` : ""}</td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("A/B score") : empty("A/B score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const cellc = (x) => x ? (x.g1 !== x.g2 ? `${x.g1}<span class="bad-ink">→${x.g2}</span>/${x.n}` : `${x.g1}/${x.n}`) + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>Best by verdict only: ${esc(sc.rank.g1[0].join(" = "))} · best by grounded: ${esc(sc.rank.g2[0].join(" = "))}</h1>
      <p class="lead">The same ${ITEMS().length * RUNS()} replies per strategy, graded twice. <b>G1</b> counts the first word. <b>G2</b> also requires that the reply does not state how a condition came out when memory holds no fact about it (“the rehearsal did not pass”). Stating an outcome memory does hold (“the load test failed”) is fine.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>strategy</th><th>G1 verdict only</th><th>G2 grounded</th><th class="num">right verdict, invented reason</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr class="${sc.rank.g2[0].includes(a) ? "hl" : ""}"><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].g1}/${A[a].n}</b> <span class="muted">${pct(A[a].g1, A[a].n)}%</span>${bar(A[a].g1, A[a].n, "g1")}</td>
          <td><b>${A[a].g2}/${A[a].n}</b> <span class="muted">${pct(A[a].g2, A[a].n)}%</span>${bar(A[a].g2, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].right_made ? "bad-ink" : ""}">${A[a].right_made}</b></td></tr>`).join("")}</tbody></table>
        <p class="small" style="margin-bottom:0">Ranking by G1: <b>${sc.rank.g1.map((g) => g.join(" = ")).join(" > ")}</b> · by G2: <b>${sc.rank.g2.map((g) => g.join(" = ")).join(" > ")}</b></p></div>
      <div class="card"><h3 style="margin-top:0">Per question, G1 → G2</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${ITEMS().map((x) => `<tr class="${x.kind === "lapsed" ? "hl" : ""}"><td class="small">${esc(x.what)} <span class="muted">(${esc(KIND[x.kind])})</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[x.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">A condition whose deadline passed with no event logged has no outcome in memory in either direction. “No, the decision is not confirmed” is right; “No, the rehearsal did not pass” is a fact nobody recorded — and a verdict-only eval scores it as a win.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B, C and D</h1>
      <p class="lead">Any question to the release assistant, once through each strategy. Graded by both graders when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${ITEMS().map((x) => `<button class="chip" data-q="${esc(x.ask)}">${esc(x.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Ines → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.g2 === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.verdict ? `G1 ${r.verdict === "ok" ? "✓" : "✗"} · G2 ${markOf(r)} ${esc(MARKS[r.g2][2])}${r.claim ? ` (“${esc(r.claim)}”)` : ""}` : ""}</span>${esc(r.reply)}</div>`).join("")}
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
    else if (k === "meetings") S.meetingsSeen = true;
    else if (k === "checks") S.checks = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the release assistant, through A, B, C and D.", true);
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

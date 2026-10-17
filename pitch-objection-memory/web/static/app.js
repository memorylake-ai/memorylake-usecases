/* Pitch objection memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["0", "A", "B", "C"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "Captures", sub: "six pitches, eight captures", view: "captures" },
    { n: 4, title: "What memory holds", sub: "per pitch, and the SOC 2 facts", view: "census" },
    { n: 5, title: "C: pitch records", sub: "one per CRM opportunity", view: "records" },
    { n: 6, title: "Ask the assistant", sub: "no memory + three memories", view: "ask" },
    { n: 7, title: "Score", sub: "per question and per memory", view: "score" },
  ];
  const fresh = () => ({ capturesSeen: false, census: null, records: null, rows: { 0: [], A: [], B: [], C: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Which prospects asked us to pass a SOC 2 review? List each prospect once.", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 4;
  const PITCHES = () => S.data?.pitches || [];
  const QS = () => S.data?.questions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>no memory, A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, captures: capturesView, census: censusView, records: recordsView, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], dup: ["2×", "bad", "one objection counted as more than one prospect"],
    wrong: ["✗", "bad", "wrong"], unknown: ["?", "warn", "says it does not know"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const live = rows.filter((r) => r.grade && !r.empty), c = (g) => live.filter((r) => r.grade === g).length;
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c("ok"), dup: c("dup"), wrong: c("wrong"), unknown: c("unknown") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `right <b>${t.ok}</b>/${t.n} · 2× counted twice <b class="${t.dup ? "bad-ink" : ""}">${t.dup}</b> · ✗ <b>${t.wrong}</b> · ? <b>${t.unknown}</b>${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const SUBJ = /(\bQuorvia\b|\bProject Lantern\b|\bthe payments prospect\b|\bpayments prospect\b)/g;
  const hiSubj = (t) => esc(t).replace(SUBJ, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.captured)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes (14 captures read in parallel, then ~85 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-pom")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>One objection, captured in three tools, reads as a pattern.</h1>
      <p class="lead">${esc(d.org || "Fernhallow")} (a fictional agency) asks its pitch assistant, through MemoryRouter, which objections keep coming up. Six SaaS pitches this year: three prospects pushed back on pricing; only Quorvia raised a security objection — but that one objection was captured three times: the CRM debrief says “Quorvia”, the meeting notes “Project Lantern”, the Slack recap “the payments prospect”.
      Three memories: <b>A</b> every capture as it arrived · <b>B</b> each pitch captured once (the control) · <b>C</b> one record per pitch, written by the app after resolving each capture to its CRM opportunity — and <b>0</b>, the same questions with no memory. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 85 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/new-business-pitch-memory-for-agencies" target="_blank" rel="noopener">memorylake.ai › New business pitch memory for agencies</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, one boundary each</h1>
      <p class="lead">A gets all eight captures; B gets the six CRM debriefs — the same pitches and the same text, without the two repeats; C gets only the app's pitch records. All three carry the same plain name: a project's name is context for extraction.</p>
      <div class="card"><dl class="kv"><dt>A · every capture</dt><dd class="mono small">${esc(w?.projects?.captured || "pending")}</dd><dt>B · captured once</dt><dd class="mono small">${esc(w?.projects?.once || "pending")}</dd>
        <dt>C · pitch records</dt><dd class="mono small">${esc(w?.projects?.records || "pending")}</dd>
        ${(S.data?.people || []).map((p) => `<dt>${esc(p.display)} · ${esc(p.tool)}</dt><dd class="mono small">${esc(w?.actors?.[p.key] || "pending")}</dd>`).join("")}</dl></div>`;
  }

  function capturesView() {
    if (!S.data || (!S.capturesSeen && !S.world)) return running() ? waiting("Captures") : empty("Captures");
    const d = S.data;
    return `<h1>Eight captures of six pitches — Quorvia's objection is in three tools, under three names</h1>
      <p class="lead">Each capture is one message in its own GROUP conversation, written by the person who wrote it and stamped with the day. A gets all eight; B gets only the CRM debriefs.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>pitch</th><th>tool</th><th>by</th><th>calls the prospect</th><th>date</th><th>objection</th><th>in</th></tr></thead><tbody>
      ${d.captures.map((c) => `<tr class="${c.repeat ? "hl" : ""}"><td class="small"><b>${esc(c.pitch_name)}</b></td><td class="small">${esc(c.tool)}</td><td class="small">${esc(c.person)}</td>
        <td class="small"><b>${esc(c.called)}</b></td><td class="small mono">${esc(c.ts.slice(0, 10))}</td><td class="small">${esc(c.objection)}</td>
        <td class="small">${c.repeat ? '<span class="lab lab-bad">A only — same pitch, again</span>' : "A and B"}</td></tr>`).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Quorvia, three times</h3>${d.captures.filter((c) => c.pitch === "quorvia").map((c) => `<div class="copyrow small"><b>${esc(c.tool)}</b> · ${hiSubj(c.message)}</div>`).join("")}</div>`;
  }

  function censusView() {
    if (!S.census) return running() ? waiting("What memory holds") : empty("What memory holds");
    const cs = S.census, A = cs.captured, B = cs.once;
    const cell = (v) => v.kept ? `<span class="lab lab-ok">kept</span> <span class="muted">${v.facts} fact${v.facts === 1 ? "" : "s"}</span>`
      : v.facts ? `<span class="lab lab-bad">named, objection lost</span>` : `<span class="lab lab-bad">not in memory</span>`;
    return `<h1>A: the SOC 2 objection is in ${A.security_facts.length} facts under ${A.security_subjects.length} name(s) · B: ${B.security_facts.length} under ${B.security_subjects.length}</h1>
      <p class="lead"><code>fact list</code> on A and B. One row per pitch: is its objection in memory? Then every fact that carries the SOC 2 objection, and what it calls the prospect — the assistant has no way to know that <span class="stamp">Project Lantern</span> and <span class="stamp">the payments prospect</span> are <span class="stamp">Quorvia</span>.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>pitch</th><th>objection</th><th>A · every capture</th><th>B · captured once</th></tr></thead><tbody>
      ${PITCHES().map((p) => `<tr class="${!A.pitches[p.id].kept || !B.pitches[p.id].kept ? "hl" : ""}"><td class="small"><b>${esc(p.name)}</b></td><td class="small">${esc(p.objection)}</td>
        <td class="small">${cell(A.pitches[p.id])}</td><td class="small">${cell(B.pitches[p.id])}</td></tr>`).join("")}
      </tbody></table><p class="small" style="margin-bottom:0">A ${A.facts} facts, objection kept for ${A.kept}/6 pitches · B ${B.facts} facts, ${B.kept}/6</p></div>
      ${[["A", A], ["B", B]].map(([a, c]) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))} — the SOC 2 facts</h3>
        ${c.security_facts.map((s) => `<div class="copyrow small"><span class="lab ${s.subject === "Quorvia" ? "lab-ok" : "lab-bad"}">${esc(s.subject)}</span> ${hiSubj(s.text)}</div>`).join("") || '<p class="muted small">none</p>'}</div>`).join("")}`;
  }

  function recordsView() {
    if (!S.records) return running() ? waiting("C: pitch records") : empty("C: pitch records");
    const r = S.records;
    return `<h1>C: ${r.resolved.length} captures → ${r.records.length} pitch records, one per CRM opportunity</h1>
      <p class="lead">The app joins each capture by its link — the CRM opportunity, the calendar event attached to it, the deal card the Slack thread hangs off — never by the name it uses. Then one <code>fact add</code> writes one record per opportunity, listing every capture it came from.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>tool</th><th>calls it</th><th>link</th><th>opportunity</th></tr></thead><tbody>
      ${r.resolved.map((x) => `<tr class="${x.called !== x.name ? "hl" : ""}"><td class="small">${esc(x.tool)}</td><td class="small"><b>${esc(x.called)}</b></td><td class="small muted">${esc(x.link)}</td>
        <td class="small">→ <b>${esc(x.opp)} ${esc(x.name)}</b></td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Pitch records</h3>${r.records.map((t) => `<div class="copyrow small">${hiSubj(t)}</div>`).join("")}</div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The pitch assistant — right: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].n}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7 per memory, plus one call without a boundary as the ∅ baseline. 2× marks an answer that counts the repeats as more prospects, or lists the one-off SOC 2 objection as recurring.</p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>truth</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows[a].filter((r) => r.item === q.id); const bad = rs.find((r) => r.grade === "dup") || rs.find((r) => r.grade && r.grade !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(q.ask)}</td><td class="small">${esc(q.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.dup ? ` <span class="bad-ink">2×${x.dup}</span>` : "") + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>Right: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].n}`).join(" · ")} · counted twice: A ${A.A.dup} · B ${A.B.dup}</h1>
      <p class="lead">${QS().length * RUNS()} answers per memory. A and B differ only by the two repeat captures of Quorvia's objection, so 2× in A and not in B is what the repeats did. Q4 (pricing, three prospects) is the reverse check: C must not count less than the others.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>right</th><th class="num">2× counted twice</th><th class="num">✗</th><th class="num">?</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].dup ? "bad-ink" : ""}">${A[a].dup}</b></td><td class="num">${A[a].wrong}</td><td class="num">${A[a].unknown}</td></tr>`).join("")}</tbody></table>
        ${ARMS.some((a) => A[a].empty) ? `<p class="small muted" style="margin-bottom:0">∅ (MemoryRouter attached nothing; listed, not graded): ${ARMS.map((a) => `${a} ${A[a].empty}`).join(" · ")}</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.id)} ${esc(q.what)} <span class="muted">= ${esc(q.truth)}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ right · 2× one objection counted as more than one prospect, or a one-off listed as recurring · ✗ wrong · ? says it does not know · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — no memory, then A, B and C</h1>
      <p class="lead">Any question to the pitch assistant, once with no memory and once through each memory. Graded when it is one of the five.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">New business → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
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
    if (!confirm("Delete the demo's boundaries, conversations, the three people (their facts first) and the three projects?")) return;
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
    else if (k === "captures") S.capturesSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "records") S.records = d;
    else if (k === "memory_view") S.census = d.census;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, the three people (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the pitch assistant, with no memory and through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "captures", "census", "records", "ask_row", "arm_done", "example",
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

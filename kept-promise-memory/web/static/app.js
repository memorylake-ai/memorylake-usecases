/* Kept promise memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C", "D"];
  const COPY = { A: "spoken", B: "instructed", C: "confirmed", D: "written" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Four copies", sub: "of Noor's memory", view: "setup" },
    { n: 3, title: "Three sessions", sub: "findings and promises", view: "sessions" },
    { n: 4, title: "What each remembers", sub: "promises kept, notes kept", view: "census" },
    { n: 5, title: "Ask the assistant", sub: "what does Noor owe?", view: "ask" },
    { n: 6, title: "Score", sub: "kept vs answered", view: "score" },
  ];
  const fresh = () => ({ setup: null, sessionsSeen: false, census: null, rows: { A: [], B: [], C: [], D: [] }, example: null, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "When is the literature review due to Mara?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 2;
  const QS = () => S.data?.questions || [];
  const CS = () => S.data?.commitments || [];
  const NOTES = () => S.data?.notes || [];
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
    const r = { connect, setup, sessions, census, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], part: ["◐", "warn", "some of it"], unknown: ["?", "warn", "says it is not recorded"], made: ["✎", "bad", "something memory does not hold"], other: ["–", "warn", "no answer"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="MemoryRouter attached nothing">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const DATE = /\b(\d{1,2} (?:Oct|October)|(?:Oct|October) \d{1,2}|Friday \d{1,2} October|2026-10-\d\d)\b/g;
  const hiDate = (t) => esc(t).replace(DATE, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.spoken)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes (twelve sessions extracted, then ~80 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-kpm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Your second brain remembers what you found — not what you promised.</h1>
      <p class="lead">${esc(d.person?.display || "Noor Abadi")} (a fictional research analyst) works on a book chapter with her AI assistant: three short sessions, findings and decisions — and promises to colleagues, each with a date.
      The same sessions go into four copies of her memory: <b>A</b> as spoken · <b>B</b> + a <code>fact instruction</code> asking for every commitment · <b>C</b> the assistant replies “Commitment logged: …” · <b>D</b> the same reply, and the app writes each promise with <code>fact add</code>.
      Then the assistant is asked, through MemoryRouter, what Noor owes and when. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 80 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-second-brain-for-knowledge-workers" target="_blank" rel="noopener">memorylake.ai › AI second brain for knowledge workers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    const what = {
      A: "The sessions as spoken. The assistant answers “Noted.”",
      B: "The same sessions, plus a fact instruction on the project and on Noor's actor that asks for every commitment.",
      C: "The assistant confirms each promise in the chat: “Commitment logged: literature review to Mara, due Friday 23 October.”",
      D: "The same confirmation — and the app writes each promise as a fact with <code>fact add</code>, its parts in metadata.",
    };
    return `<h1>Four copies of Noor's memory</h1>
      <p class="lead">Each copy is a project named “${esc(d.project_name)}” with its own Noor and assistant actors; the arm is only in the custom_id and the boundary's name. Each boundary is the project plus Noor's actor — what her assistant reads through MemoryRouter.</p>
      <div class="grid2">${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><p class="small">${what[a]}</p>
        <div class="small muted mono">${esc(w?.projects?.[COPY[a]] || "pending")}</div></div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">B's fact instruction</h3><p class="small">“${esc(d.instruction)}”</p>
        ${S.setup ? `<p class="small muted" style="margin-bottom:0">read back with <code>fact instruction get</code>: “${esc(S.setup.instruction_read)}”</p>` : ""}</div>`;
  }

  function sessions() {
    const d = S.data;
    if (!d || (!S.sessionsSeen && !S.world)) return running() ? waiting("Three sessions") : empty("Three sessions");
    return `<h1>Three sessions with her assistant — promises included</h1>
      <p class="lead">Each session is one DIRECT conversation between Noor and her assistant, extracted before the next one starts. Highlighted: the five promises. In C and D the assistant confirms each one; in D the app also writes it.</p>
      ${d.sessions.map((s) => `<div class="card"><div class="row spread"><b>${esc(s.name)}</b><span class="mono small muted">${esc(s.at.slice(0, 10))}</span></div>
        <div class="chat">${s.turns.map((t) => t.who === "noor"
          ? `<div class="msg user ${t.commits ? "promise" : ""}"><span class="who">Noor${t.commits ? " · promise" : ""}</span>${hiDate(t.text)}</div>`
          : `<div class="msg bot"><span class="who">assistant${t.logged ? " · A, B" : ""}</span>${esc(t.text)}</div>${t.logged ? `<div class="msg bot router"><span class="who">assistant · C, D</span>${hiDate(t.logged)}</div>
             <div class="small muted" style="align-self:flex-start">D's app writes: ${t.logs.map((id) => `<code>${esc((CS().find((c) => c.id === id) || {}).record)}</code>`).join(" ")}</div>` : ""}`).join("")}</div></div>`).join("")}`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What each copy remembers") : empty("What each copy remembers");
    const c = S.census, n = CS().length;
    const pm = (v) => v.extracted.length ? (v.dated ? "✓" : "◐") : v.bot.length ? "·" : "✗";
    const cell = (cp, x) => { const v = c[cp].promises[x.id];
      const kept = v.extracted.length ? `<span class="${v.dated ? "" : "warn-ink"}">${pm(v)} ${hiDate(v.extracted[0])}</span>` : `<span class="bad-ink">✗ not in memory</span>`;
      if (v.record) return `<td class="small">✓ the app's record<div class="muted">extraction: ${v.extracted.length ? pm(v) : "✗"}</div></td>`;
      return `<td class="small">${kept}</td>`; };
    return `<h1>Promises in memory: ${ARMS.map((a) => a === "D" ? `D ${c[COPY[a]].records}/${n} (the app's records)` : `${a} ${c[COPY[a]].kept}/${n}`).join(" · ")}</h1>
      <p class="lead">Every fact the assistant can read in each copy (the project and Noor's actor), read for the five promises and five notes. D's promises are in memory because the app wrote them, whatever extraction did.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>promise</th>${ARMS.map((a) => `<th>${esc(LABEL(a))} <span class="muted">(${c[COPY[a]].facts} facts)</span></th>`).join("")}</tr></thead><tbody>
        ${CS().map((x) => `<tr class="${c.spoken.promises[x.id].extracted.length ? "" : "hl"}"><td class="small"><b>${esc(x.what)}</b><br><span class="muted">${esc(x.due_text)}</span></td>${ARMS.map((a) => cell(COPY[a], x)).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="card"><table class="lint plain"><thead><tr><th>note</th>${ARMS.map((a) => `<th>${a}</th>`).join("")}</tr></thead><tbody>
        ${NOTES().map((x) => `<tr><td class="small">${esc(x.what)}</td>${ARMS.map((a) => { const f = c[COPY[a]].notes[x.id];
          return f.length ? `<td class="small">✓ <span class="muted">${esc(f[0].slice(0, 70))}${f[0].length > 70 ? "…" : ""}</span></td>` : '<td class="small bad-ink">✗</td>'; }).join("")}</tr>`).join("")}
      </tbody></table><p class="small muted" style="margin-bottom:0">notes kept: ${ARMS.map((a) => `${a} ${c[COPY[a]].notes_kept}/${NOTES().length}`).join(" · ")}</p></div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const ex = S.example;
    return `<h1>Noor's assistant — what does she owe, and when?</h1>
      <p class="lead">Nine questions, ${RUNS()} times through each copy's boundary at temperature 0, plus one call without a boundary as the ∅ baseline (same prompt tokens = nothing attached). Two controls ask about her findings and preferences; the last asks about a promise she never made.</p>
      ${ex ? `<div class="card"><h3 style="margin-top:0">“${esc(ex.ask)}”</h3>
        ${ARMS.map((a) => `<div class="msg bot ${ex.rows[a].grade === "ok" && !ex.rows[a].empty ? "" : "wrong"}"><span class="who">${esc(LABEL(a))} · ${markOf(ex.rows[a])}</span>${hiDate(ex.rows[a].reply)}</div>`).join("")}</div>` : ""}
      <div class="card"><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th>${esc(LABEL(a))}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.what)}</td>${ARMS.map((a) => { const rs = S.rows[a].filter((r) => r.item === q.id);
          const pick = rs.find((r) => r.reply && !r.empty) || rs.find((r) => r.reply) || {};
          return `<td><div class="cell-ans">${rs.map(markOf).join(" ")}</div><div class="small muted">${esc((pick.reply || "").slice(0, 110))}${(pick.reply || "").length > 110 ? "…" : ""}</div></td>`; }).join("")}</tr>`).join("")}
      </tbody></table><p class="small muted" style="margin-bottom:0">✓ right · ◐ some of it · ? says it is not recorded · ✎ something memory does not hold · ∅ MemoryRouter attached nothing</p></div>`;
  }

  function score() {
    if (!S.score || !S.census) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers, c = S.census, n = CS().length;
    const kept = (a) => a === "D" ? c[COPY[a]].records : c[COPY[a]].kept;
    const bar = (k, m, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, m)}%"></span></div>`;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") : "";
    return `<h1>Promise questions answered: ${ARMS.map((a) => `${a} ${A[a].promise_ok}/${A[a].promise_n}`).join(" · ")}</h1>
      <p class="lead">What each copy kept decides what the assistant can say. Counts leave out ∅ answers (MemoryRouter attached nothing), which are listed separately. One run varies; the README adds up several.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>copy</th><th>promises in memory</th><th>promise answers right</th><th class="num">not recorded</th><th class="num">invented</th><th class="num">controls</th><th class="num">∅</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${kept(a)}/${n}</b> <span class="muted small">${a === "D" ? "app records" : "by extraction"}</span>${bar(kept(a), n, "g1")}</td>
          <td><b>${A[a].promise_ok}/${A[a].promise_n}</b> <span class="muted">${pct(A[a].promise_ok, A[a].promise_n)}%</span>${bar(A[a].promise_ok, A[a].promise_n, "g2")}</td>
          <td class="num">${A[a].promise_unknown}</td><td class="num"><b class="${A[a].promise_made + A[a].reverse_made ? "bad-ink" : ""}">${A[a].promise_made + A[a].reverse_made}</b></td>
          <td class="num">${A[a].control_ok}/${A[a].control_n}</td><td class="num">${A[a].empty}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.what)}</td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">right / answered with something attached · ∅ MemoryRouter attached nothing</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B, C and D</h1>
      <p class="lead">Any question to Noor's assistant, once through each copy's memory. Graded when it is one of the nine.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Noor → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${hiDate(r.reply)}</div>`).join("")}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows });
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
    if (!confirm("Delete the demo's boundaries, conversations, eight actors (their facts first) and the four projects?")) return;
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
    else if (k === "setup") S.setup = d;
    else if (k === "sessions") S.sessionsSeen = true;
    else if (k === "example") S.example = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the four projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to Noor's assistant, through A, B, C and D.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "sessions", "census", "ask_row", "arm_done", "example",
    "score", "summary", "router_unavailable", "answered", "memory_view", "done", "error"];
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

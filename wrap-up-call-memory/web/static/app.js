/* Wrap-up call memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "F"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, one boundary each", view: "setup" },
    { n: 3, title: "Six wrap-up calls", sub: "four figures each", view: "calls" },
    { n: 4, title: "Receipt audit", sub: "which figures each call left", view: "audit" },
    { n: 5, title: "Pinned lines", sub: "F: the gaps, from the calls", view: "pinned" },
    { n: 6, title: "Ask the assistant", sub: "two memories, twelve questions", view: "ask" },
    { n: 7, title: "Score", sub: "per question and per memory", view: "score" },
  ];
  const fresh = () => ({ callsSeen: false, audit: null, pinned: null, rows: { A: [], F: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "What was the cost per lead on the Halden Bikes spring commuter campaign?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const CALLS = () => S.data?.calls || [];
  const QS = () => S.data?.questions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A and F</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, calls, audit: auditView, pinned: pinnedView, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the figure said on the call"], swap: ["⇄", "bad", "another client's figure from memory"],
    unknown: ["?", "warn", "Unknown"], made: ["✎", "bad", "a figure nobody said"], other: ["–", "warn", "no figure and no Unknown"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const live = rows.filter((r) => r.grade && !r.empty), c = (xs, g) => xs.filter((r) => r.grade === g).length;
    const gap = live.filter((r) => r.gap !== "named"), held = live.filter((r) => r.gap === "named");
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c(live, "ok"), swap: c(live, "swap"), unknown: c(live, "unknown"),
      made: c(live, "made"), other: c(live, "other"), gap_n: gap.length, gap_ok: c(gap, "ok"), held_n: held.length, held_ok: c(held, "ok") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `right <b>${t.ok}</b>/${t.n} · ⇄ another client's figure <b class="${t.swap ? "bad-ink" : ""}">${t.swap}</b> · Unknown <b>${t.unknown}</b>
    · ✎ invented <b class="${t.made ? "bad-ink" : ""}">${t.made}</b> · on figures the audit pinned <b>${t.gap_ok}</b>/${t.gap_n}${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const NUM = /(\b\d[\d,]*(?:\.\d+)?\s?(?:percent|dollars|million)?)/g;
  const hiNum = (t) => esc(t).replace(NUM, (m) => `<span class="stamp">${m}</span>`);
  const STATE = { named: ["kept, names the client", "lab-ok"], unnamed: ["kept, no client", "lab-bad"], missing: ["not in memory", "lab-bad"] };
  const stateLab = (s) => s ? `<span class="lab ${STATE[s][1]}">${STATE[s][0]}</span>` : "";

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.as_said)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes (six calls extracted, then ~85 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-wcm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Some calls never make it into memory. The figures that do often lose the client.</h1>
      <p class="lead">${esc(d.org || "Brightwell Studio")} (a fictional agency) holds a wrap-up call at the end of every client campaign, and the new-business team writes case studies from what those calls left in memory. Six calls, four figures each, all said the same way: “Cost per lead came in at 18.40 dollars.”
      Most calls are extracted whole. Now and then one leaves nothing at all — and many kept figures come back without the client's name. Each conversation has a receipt, <code>conv fact-actions</code>: the app audits every call against it and pins the lines that are missing or unnamed. Two memories: <b>A</b> the calls as extracted · <b>F</b> A's facts plus the pinned lines. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 85 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/case-study-memory-for-agency-teams" target="_blank" rel="noopener">memorylake.ai › Case study memory for agency teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Two projects, one boundary each</h1>
      <p class="lead">“as_said” gets the six wrap-up calls. “audited” gets every fact MemoryLake extracted into “as_said”, copied word for word, plus the lines the audit pins — so the two differ only by those lines. Both projects carry the same plain name: a project's name is context for extraction.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>as_said (A)</dt><dd class="mono small">${esc(w?.projects?.as_said || "pending")}</dd><dt>audited (F)</dt><dd class="mono small">${esc(w?.projects?.audited || "pending")}</dd></dl></div>
        <div class="card"><h3 style="margin-top:0">On the calls</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}
          ${CALLS().map((c) => `<div class="small"><b>${esc(c.contact)}</b> · ${esc(c.contact_role)}</div>`).join("")}</div></div>`;
  }

  function calls() {
    if (!S.data || (!S.callsSeen && !S.world)) return running() ? waiting("Six wrap-up calls") : empty("Six wrap-up calls");
    return `<h1>Six wrap-up calls, four figures each — said the same way every time</h1>
      <p class="lead">Each call is one GROUP conversation in “as_said”, every line as its speaker at the call's time; the six are sent side by side, the way calls arrive at a busy studio. The highlighted lines are the figures a case study needs.</p>
      <div class="grid2">${CALLS().map((c) => `<div class="card"><div class="row spread"><b>${esc(c.client)}</b><span class="mono small muted">${esc(c.at.slice(0, 10))}</span></div>
        <div class="small muted">${esc(c.campaign)} wrap-up</div>
        ${c.lines.map((l, i) => `<div class="small" style="margin-top:4px">${c.metrics.some((m) => m.line === i) ? hiNum(`${l.who}: ${l.body}`) : esc(`${l.who}: ${l.body}`)}</div>`).join("")}</div>`).join("")}</div>`;
  }

  function auditView() {
    if (!S.audit) return running() ? waiting("Receipt audit") : empty("Receipt audit");
    const au = S.audit, nfig = 4 * CALLS().length;
    const kept = Object.values(au.calls).reduce((s, a) => s + a.kept, 0), named = Object.values(au.calls).reduce((s, a) => s + a.named, 0);
    return `<h1>${kept}/${nfig} figures are in memory · ${named}/${nfig} name the client</h1>
      <p class="lead"><code>conv fact-actions</code> lists the facts each call added to the project. For every figure said on the call: is it in one of those facts, and does that fact say whose campaign it was? A call whose receipt is empty left nothing — not the figures, not the dates, not what the client said.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>call</th><th class="num">facts</th>${[1, 2, 3, 4].map((i) => `<th>figure ${i}</th>`).join("")}</tr></thead><tbody>
      ${CALLS().map((c) => { const a = au.calls[c.id];
        return `<tr class="${a.named < 4 ? "hl" : ""}"><td class="small"><b>${esc(c.client)}</b>${a.facts === 0 ? '<br><span class="bad-ink">the whole call left nothing</span>' : ""}</td><td class="num">${a.facts}</td>
          ${c.metrics.map((m) => { const s = a.metrics[m.id]; return `<td class="small"><b>${esc(m.what)}</b> ${esc(m.value)}<br>${stateLab(s.state)}${s.state === "unnamed" ? `<div class="muted">“${esc(s.fact)}”</div>` : ""}</td>`; }).join("")}</tr>`; }).join("")}
      </tbody></table><p class="small" style="margin-bottom:0">${au.pins.length} line(s) to pin.</p></div>`;
  }

  function pinnedView() {
    if (!S.pinned) return running() ? waiting("Pinned lines") : empty("Pinned lines");
    const p = S.pinned;
    return `<h1>F: ${p.copied} extracted facts copied — then ${p.pins.length} pinned line(s)</h1>
      <p class="lead">For every figure the audit flagged, the app pins the sentence as it was said on the call — with the client, the campaign, the date and the speaker — using <code>fact add</code>. Nothing comes from a second source: the call itself is the record. A and F differ only by these lines.</p>
      <div class="card">${p.pins.length ? p.pins.map((x) => `<div class="copyrow"><span class="k">${stateLab(x.state)}</span><span class="small">${hiNum(x.text)}</span></div>`).join("")
        : '<p class="muted">Every figure was kept with its client this run — nothing to pin, so A and F hold the same facts.</p>'}</div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    const state = (q) => S.audit?.calls?.[q.call]?.metrics?.[q.metric]?.state;
    return `<h1>The case-study assistant — figures right: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].n}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7, plus one call without a boundary as the ∅ baseline. ⇄ marks a figure that belongs to another client or another metric — memory held it, just not for this question.</p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>said on the call</th><th>after the call</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows[a].filter((r) => r.item === q.id); const bad = rs.find((r) => r.grade && r.grade !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(q.ask)}</td><td class="small">${esc(q.truth)}</td><td>${stateLab(state(q))}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const state = (q) => S.audit?.calls?.[q.call]?.metrics?.[q.metric]?.state;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>Figures right: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].n}`).join(" · ")}</h1>
      <p class="lead">${QS().length * RUNS()} answers per memory. The split that matters: figures the audit had to pin (their call left them out, or left them without the client) and figures that were kept with the client — where F must do no worse than A.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>right</th><th class="num">⇄ other client</th><th class="num">Unknown</th><th class="num">✎ invented</th><th>pinned figures</th><th>kept with client</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].swap ? "bad-ink" : ""}">${A[a].swap}</b></td><td class="num">${A[a].unknown}</td><td class="num"><b class="${A[a].made ? "bad-ink" : ""}">${A[a].made}</b></td>
          <td><b>${A[a].gap_ok}/${A[a].gap_n}</b>${bar(A[a].gap_ok, A[a].gap_n, "g1")}</td><td><b>${A[a].held_ok}/${A[a].held_n}</b>${bar(A[a].held_ok, A[a].held_n, "g1")}</td></tr>`).join("")}</tbody></table>
        ${ARMS.some((a) => A[a].empty) ? `<p class="small muted" style="margin-bottom:0">∅ (MemoryRouter attached nothing; listed, not graded): ${ARMS.map((a) => `${a} ${A[a].empty}`).join(" · ")}</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th><th>after the call</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.what)} <span class="muted">= ${esc(q.truth)}</span></td><td>${stateLab(state(q))}</td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ the figure said on the call · ⇄ another client's (or metric's) figure from memory · ? Unknown · ✎ a figure nobody said · – neither · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A and F</h1>
      <p class="lead">Any question to the case-study assistant, once through each memory. Graded when it is one of the twelve.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">New business → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.truth ? `<p class="small muted">said on the call: ${esc(x.truth)}</p>` : ""}`).join("")}</div></div>`;
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
    if (!confirm("Delete the demo's boundaries, conversations, nine actors (their facts first) and the two projects?")) return;
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
    else if (k === "calls") S.callsSeen = true;
    else if (k === "audit") S.audit = d;
    else if (k === "pinned") S.pinned = d;
    else if (k === "memory_view") S.audit = d.audit;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the two projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the case-study assistant, through A and F.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "calls", "audit", "pinned", "ask_row", "arm_done", "example",
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

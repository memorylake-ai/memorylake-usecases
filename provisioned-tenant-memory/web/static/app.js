/* Provisioned tenant memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C"];
  const COPY = { A: "plain", B: "described", C: "instructed" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Provision", sub: "one tenant project, three ways", view: "provision" },
    { n: 3, title: "Three eval syncs", sub: "the same meetings into each", view: "meetings" },
    { n: 4, title: "What each remembers", sub: "readings kept, per meeting", view: "census" },
    { n: 5, title: "Ask the assistant", sub: "every reading, three tenants", view: "ask" },
    { n: 6, title: "Score", sub: "kept vs answered", view: "score" },
  ];
  const fresh = () => ({ provisioned: null, meetingsSeen: false, census: null, rows: { A: [], B: [], C: [] }, example: null, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "What recall at 10 did the new reranker get?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 2;
  const LINES = () => S.data?.lines || [];
  const READINGS = () => LINES().filter((x) => x.kind === "reading");
  const LABEL = (a) => (S.data?.arms || {})[a] || a;
  const who = (k) => ((S.data?.people || []).find((p) => p.key === k) || {}).display || k;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, provision, meetings, census, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the reading"], unknown: ["?", "warn", "Unknown"], made: ["✎", "bad", "a number memory does not hold"], other: ["–", "warn", "no number"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="MemoryRouter attached nothing">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  // the same counts as the CLI's tally(): every call is counted; ∅ is shown separately
  function tally(rows) {
    const graded = rows.filter((r) => r.grade), live = graded.filter((r) => !r.empty);
    const c = (g) => live.filter((r) => r.grade === g).length;
    return { calls: graded.length, n: live.length, empty: graded.length - live.length, ok: c("ok"), unknown: c("unknown"), made: c("made"), other: c("other") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const NUM = /(\b\d[\d,]*(?:\.\d+)?\s?(?:percent|cents?|milliseconds|%)?)/g;
  const hiNum = (t) => esc(t).replace(NUM, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.plain)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes (nine meetings extracted, then ~85 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-ptm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The tenant's context went into the description. Extraction never reads it.</h1>
      <p class="lead">${esc(d.saas || "Minutely")} (a fictional AI meeting-notes SaaS) gives every customer team its own MemoryLake project, created by its provisioning code: named after the account id, with what the customer typed at signup — “keep every metric reading we say” — stored somewhere.
      ${esc(d.org || "Quillfold")}'s three eval syncs go into three projects provisioned three ways: <b>A</b> the project only · <b>B</b> + the signup note as the project's description · <b>C</b> + the same note as the project's <code>fact instruction</code>. Whole meetings of bare readings vanish from A and B alike; C keeps them. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 85 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-infrastructure-for-ai-saas" target="_blank" rel="noopener">memorylake.ai › Memory infrastructure for AI SaaS</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function provision() {
    const w = S.world, d = S.data || {}, p = S.provisioned;
    const code = (a) => a === "A" ? `memorylake proj create --name ${esc(d.account)}`
      : a === "B" ? `memorylake proj create --name ${esc(d.account)} \\\n    --description "&lt;signup note&gt;"`
      : `memorylake proj create --name ${esc(d.account)}\nmemorylake fact instruction set --project &lt;id&gt; \\\n    --text "&lt;signup note&gt;"`;
    return `<h1>One tenant project, provisioned three ways</h1>
      <p class="lead">${esc(d.org)} signs up for ${esc(d.saas)} as account <code>${esc(d.account)}</code>. All three projects get that same name — what provisioning code names them; only the custom_id and the boundary say which is which. B and C store the same signup note in two different places.</p>
      <div class="card"><h3 style="margin-top:0">The signup note</h3><p class="small">“${esc(d.signup_note)}”</p></div>
      <div class="grid3">${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><pre class="code small">${code(a)}</pre>
        <div class="small muted mono">${esc(w?.projects?.[COPY[a]] || "pending")}</div></div>`).join("")}</div>
      ${p ? `<div class="card"><h3 style="margin-top:0">Read back</h3>
        <div class="small"><b>B</b> <code>proj get</code> → description: “${esc(p.description)}”</div>
        <div class="small" style="margin-top:6px"><b>C</b> <code>fact instruction get</code> → “${esc(p.instruction)}”</div>
        <p class="small muted" style="margin-bottom:0">Both are stored. Step 4 shows which one extraction reads.</p></div>` : ""}`;
  }

  function meetings() {
    const d = S.data;
    if (!d || (!S.meetingsSeen && !S.world)) return running() ? waiting("Three eval syncs") : empty("Three eval syncs");
    return `<h1>Three eval syncs — the same conversations into each tenant</h1>
      <p class="lead">Each meeting is one GROUP conversation, every line as its speaker at the meeting's time. Each sync reads out four metrics the way people do — “… came in at 0.81.” — and ends with one action item. The three tenant copies get identical conversations.</p>
      ${d.meetings.map((m) => `<div class="card"><div class="row spread"><b>${esc(m.name)}</b><span class="mono small muted">${esc(m.at.slice(0, 10))}</span></div>
        ${LINES().filter((x) => x.meeting === m.id).map((x) => `<div class="small" style="margin-top:6px"><span class="lab ${x.kind === "action" ? "lab-bad" : ""}">${x.kind}</span> <b>${esc(who(x.speaker))}</b>: ${hiNum(x.text)}</div>`).join("")}</div>`).join("")}`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What each tenant remembers") : empty("What each tenant remembers");
    const c = S.census, n = READINGS().length, d = S.data;
    const meet = (cp, m) => { const v = c[cp].meetings[m.id];
      return `<td class="${v.empty ? "bad-ink" : ""}">${v.empty ? "⌀ nothing kept" : `readings <b>${v.readings}</b>/${v.of}`} <span class="muted small">· action ${v.action ? "✓" : "✗"}</span></td>`; };
    const cell = (cp, x) => { const r = c[cp].lines[x.id];
      return r.project.length ? `<td class="small">${hiNum(r.project[0])}</td>` : r.actor.length ? `<td class="small warn-ink">only on a speaker: ${hiNum(r.actor[0])}</td>` : '<td class="small bad-ink">✗ not in memory</td>'; };
    return `<h1>Readings kept: A ${c.plain.readings}/${n} · B ${c.described.readings}/${n} · C ${c.instructed.readings}/${n}</h1>
      <p class="lead">Every fact in each tenant's project, read for the twelve readings and three action items. B stores the signup note as the project's description, which extraction does not read; C stores it as the fact instruction, which it does. A whole meeting can leave nothing behind — ⌀ — while its conversation is complete.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>meeting</th>${ARMS.map((a) => `<th>${esc(LABEL(a))} <span class="muted">(${c[COPY[a]].facts} facts)</span></th>`).join("")}</tr></thead><tbody>
        ${d.meetings.map((m) => `<tr><td class="small"><b>${esc(m.name)}</b></td>${ARMS.map((a) => meet(COPY[a], m)).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="card"><table class="lint plain"><thead><tr><th>line</th>${ARMS.map((a) => `<th>${a}</th>`).join("")}</tr></thead><tbody>
        ${LINES().map((x) => `<tr class="${c.plain.lines[x.id].project.length ? "" : "hl"}"><td class="small"><b>${esc(x.what)}</b>${x.value ? `<br><span class="muted">said: ${esc(x.value)}</span>` : ""}</td>${ARMS.map((a) => cell(COPY[a], x)).join("")}</tr>`).join("")}
      </tbody></table></div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    const ex = S.example;
    return `<h1>The meeting assistant — readings answered: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].calls}`).join(" · ")}</h1>
      <p class="lead">Each reading ${RUNS()} times through each tenant's boundary at temperature 0, plus one call without a boundary as the ∅ baseline (same prompt tokens = nothing attached).</p>
      ${ex ? `<div class="card"><h3 style="margin-top:0">“${esc(ex.ask)}” <span class="muted small">said: ${esc(ex.value)}</span></h3>
        ${ARMS.map((a) => `<div class="msg bot ${ex.rows[a].grade === "ok" && !ex.rows[a].empty ? "" : "wrong"}"><span class="who">${esc(LABEL(a))} · ${markOf(ex.rows[a])}</span>${esc(ex.rows[a].reply)}</div>`).join("")}</div>` : ""}
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>said</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${READINGS().map((x) => { const rs = S.rows[a].filter((r) => r.item === x.id); const bad = rs.find((r) => r.grade && (r.grade !== "ok" || r.empty));
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(x.ask)}</td><td class="small">${esc(x.value)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">right <b>${t[a].ok}</b>/${t[a].calls} · Unknown <b>${t[a].unknown}</b> · invented <b class="${t[a].made ? "bad-ink" : ""}">${t[a].made}</b> · ∅ <b>${t[a].empty}</b></p></div>`).join("")}`;
  }

  function score() {
    if (!S.score || !S.census) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers, c = S.census, n = READINGS().length;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + (x.held ? "" : ' <span class="small bad-ink">not kept</span>') : "";
    const bar = (k, m, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, m)}%"></span></div>`;
    return `<h1>Readings answered: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].calls}`).join(" · ")}</h1>
      <p class="lead">What each tenant's memory kept decides what the assistant can say. One run varies — a meeting kept or lost changes four readings at once — so the README adds up several runs.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>tenant</th><th>readings kept</th><th>right</th><th class="num">Unknown</th><th class="num">invented</th><th class="num">∅</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${c[COPY[a]].readings}/${n}</b>${bar(c[COPY[a]].readings, n, "g1")}</td>
          <td><b>${A[a].ok}/${A[a].calls}</b> <span class="muted">${pct(A[a].ok, A[a].calls)}%</span>${bar(A[a].ok, A[a].calls, "g2")}</td>
          <td class="num">${A[a].unknown}</td><td class="num"><b class="${A[a].made ? "bad-ink" : ""}">${A[a].made}</b></td><td class="num">${A[a].empty}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Per reading</h3><table class="lint plain"><thead><tr><th>reading</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${READINGS().map((x) => `<tr><td class="small">${esc(x.what)} <span class="muted">→ ${esc(x.value)}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[x.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ the reading · ? Unknown · ✎ a number memory does not hold · ∅ MemoryRouter attached nothing</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C</h1>
      <p class="lead">Any question to the meeting assistant, once through each tenant's memory. Graded when it is one of the twelve readings.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${READINGS().map((x) => `<button class="chip" data-q="${esc(x.ask)}">${esc(x.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Priya → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}${r.grade ? (r.held ? " · kept in this tenant" : " · not kept in this tenant") : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.value ? `<p class="small muted">said at the meeting: ${esc(x.value)}</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, value: r.value });
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
    if (!confirm("Delete the demo's boundaries, conversations, six actors (their facts first) and the three projects?")) return;
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
    else if (k === "provisioned") S.provisioned = d;
    else if (k === "meetings") S.meetingsSeen = true;
    else if (k === "example") S.example = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the meeting assistant, through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "provisioned", "meetings", "census", "ask_row", "arm_done", "example",
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

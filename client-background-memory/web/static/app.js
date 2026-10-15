/* Client background memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "playbooks, clients, boundaries", view: "setup" },
    { n: 3, title: "What Tallyfield knows", sub: "playbook + client background", view: "memory" },
    { n: 4, title: "Background is stored", sub: "fact list --actors", view: "background" },
    { n: 5, title: "12 questions", sub: "one boundary per client", view: "answers" },
    { n: 6, title: "What was attached", sub: "Claude copies it out", view: "attached" },
    { n: 7, title: "Background first", sub: "client facts in the prompt", view: "first" },
    { n: 8, title: "Scoreboard", sub: "three wirings side by side", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, background: null, answers: [], inspect: [], score: null,
    unavailable: null, summary: null, asked: [], askClient: "copperline", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ORDER = () => S.data?.order || ["week1", "month12", "first"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const client = (k) => (S.data?.clients || []).find((c) => c.key === k) || { name: k, facts: [] };
  const slotLabel = (s) => S.data?.slots?.[s]?.label || s;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>about any client, all three wirings</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, background, answers, attached, first, score, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (ok) => ok == null ? '<span class="mark">·</span>' : `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.playbooks?.month12?.boundary)}</span> + 6 more boundaries` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-cbg")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Does the assistant hear the client, or the playbook?</h1>
      <p class="lead">Tallyfield (fictional) onboards new clients with an assistant on MemoryRouter. It remembers two things: the team's shared onboarding playbook,
      and each client's background — what <i>this</i> client told us. One boundary per client over both is the obvious wiring. This demo asks the same 12 onboarding
      questions three ways and shows when the client's own words never reach the prompt. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 36 calls on gpt-4o-mini and 16 on claude-haiku-4-5 per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-client-onboarding-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for client onboarding teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two playbook projects, three client actors, one boundary per client</h1>
      <p class="lead">The assistant never assembles memory itself: every message is an ordinary model call with <code>--boundary</code> added, and MemoryRouter picks what to attach.
      Each client gets a boundary over <b>the playbook project + the client's actor</b> — once with the week-1 playbook, once with the month-12 one.</p>
      <div class="grid2">
        <div class="card"><h3 style="margin-top:0">Per client — playbook + client</h3><dl class="kv">
          ${(S.data?.clients || []).map((c) => `<dt>${esc(c.name)}</dt><dd class="mono small">${esc(w?.clients?.[c.key]?.actor || "pending")}<br>week 1 <b>${esc(w?.clients?.[c.key]?.week1 || "")}</b><br>month 12 <b>${esc(w?.clients?.[c.key]?.month12 || "")}</b></dd>`).join("")}</dl>
          <div class="cmdline">boundary create --projects &lt;playbook&gt; --human-actor &lt;client&gt;</div></div>
        <div class="card"><h3 style="margin-top:0">For the fix — playbook only</h3><dl class="kv">
          <dt>week 1</dt><dd class="mono small">${esc(w?.playbooks?.week1?.project || "pending")}</dd>
          <dt>month 12</dt><dd class="mono small">${esc(w?.playbooks?.month12?.project || "pending")}<br><b>${esc(w?.playbooks?.month12?.boundary || "")}</b></dd></dl>
          <div class="cmdline">boundary create --projects &lt;month-12 playbook&gt;</div></div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What Tallyfield knows") : empty("What Tallyfield knows");
    const pb = S.data.playbooks;
    return `<h1>The shared playbook, and each client's background</h1>
      <p class="lead">The playbook is the team's: the standard steps, and later a note for every client who followed them. The background is the client's: the few things
      where this client is different. Both are stored verbatim (<code>fact add</code>).</p>
      <div class="grid2">
        <div class="card"><h3 style="margin-top:0">Playbook · ${pb.week1.count} notes in week 1, ${pb.month12.count} in month 12</h3>
          ${pb.month12.sample.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}
          <div class="memfact small"><span class="muted">Onboarding 2025-11, Bluepeak Logistics: … · Check-in 2026-03, Cedar Line: … (one per client, per topic)</span></div></div>
        ${S.data.clients.map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.name)} <span class="muted small">${esc(c.where)} · ${c.facts.length} fact(s) on its actor</span></h3>
          ${c.facts.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function background() {
    if (!S.background) return running() ? waiting("Each client's background is stored") : empty("Each client's background is stored");
    return `<h1>Every exception is stored — on the client's own actor</h1>
      <p class="lead"><code>fact list --actors &lt;client&gt;</code> returns all of them, every time. Each client's boundary includes that actor. Whether a line reaches the
      prompt is up to MemoryRouter's ranking.</p>
      <div class="grid2">${S.data.clients.map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.name)}</h3>
        ${(S.background.clients?.[c.key] || []).map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  const qCard = (q, rows) => `<div class="card chat"><div class="msg user"><span class="who">${esc(client(q.client).name)} · ${esc(slotLabel(q.slot))}${q.exception ? "" : " · standard path"}</span>${esc(q.say)}</div>
    <p class="small muted" style="margin:4px 0">client's answer: <b>${esc(q.answer)}</b></p>
    ${rows.map((r) => `<div class="msg bot ${r.ok === false ? "wrong" : ""}"><span class="who">${esc(armLabel(r.arm))} ${markOf(r.ok)}${r.standard ? ' <b class="bad-ink">← the standard path</b>' : ""}</span>${esc(r.answer || r.reply || "…")}</div>`).join("")}</div>`;

  function answers() {
    const rows = S.answers.filter((r) => r.arm !== "first");
    const h = "12 questions, one boundary per client";
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const tot = (a) => { const g = rows.filter((r) => r.arm === a && r.exception && r.ok != null); return `${g.filter((r) => r.ok).length}/${g.length}`; };
    return `<h1>${h} — client exceptions honoured: week 1 ${tot("week1")} · month 12 ${tot("month12")}</h1>
      <p class="lead"><code>router chat --boundary</code> (gpt-4o-mini, temperature 0). Each reply starts with “Answer: …”; its first clause is graded against what the client told us.
      The misses cluster on the questions worded like the playbook, not like the client — at either playbook size.</p>
      <div class="grid2">${S.data.questions.map((q) => {
        const rs = ["week1", "month12"].map((a) => rows.find((r) => r.client === q.client && r.slot === q.slot && r.arm === a)).filter(Boolean);
        return rs.length ? qCard(q, rs) : "";
      }).join("")}</div>`;
  }

  function attached() {
    const h = "What MemoryRouter attached";
    if (!S.inspect.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const own = (a) => { const g = S.inspect.filter((c) => c.arm === a); return `${g.filter((c) => c.own).length}/${g.length}`; };
    return `<h1>${h} — the client's line: week 1 ${own("week1")} · month 12 ${own("month12")}</h1>
      <p class="lead">The 8 exception questions again, through <b>claude-haiku-4-5</b>, told in its system prompt to copy out every remembered entry first.
      Where the client's line is missing, the playbook's notes on the same topic took its place. (The checker is its own call, so what it got can differ a little from the answer's call.)</p>
      <div class="grid2">${S.data.questions.filter((q) => q.exception).map((q) => {
        const cs = ["week1", "month12"].map((a) => S.inspect.find((c) => c.client === q.client && c.slot === q.slot && c.arm === a)).filter(Boolean);
        if (!cs.length) return "";
        const mine = client(q.client).facts;
        return `<div class="card"><h3 style="margin-top:0">${esc(client(q.client).name)}: “${esc(q.say)}”</h3>
          ${cs.map((c) => `<p class="small" style="margin:10px 0 2px"><b>${esc(armLabel(c.arm))}</b> ${markOf(c.own)} <span class="muted">${c.lines.length} attached · ${c.playbook} playbook · ${c.client_lines} client</span></p>
            ${c.lines.map((l) => { const isClient = mine.includes(l);
              return `<div class="memfact small"><span><span class="lab ${isClient ? "lab-ok" : "lab-bad"}">${isClient ? "client" : "playbook"}</span> ${esc(l)}</span></div>`; }).join("")}`).join("")}</div>`;
      }).join("")}</div>`;
  }

  function first() {
    const rows = S.answers.filter((r) => r.arm === "first");
    const h = "Background first";
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const exc = rows.filter((r) => r.exception), std = rows.filter((r) => !r.exception);
    return `<h1>${h} — exceptions ${exc.filter((r) => r.ok).length}/${exc.length} · standard path ${std.filter((r) => r.ok).length}/${std.length}</h1>
      <p class="lead">What the use-case page calls <i>client identity loaded before the first message</i>: <code>fact list --actors &lt;client&gt;</code> (a handful of facts, all of them)
      goes into the system prompt, and the boundary covers the month-12 playbook only. The standard-path questions are the control.</p>
      <div class="grid2">${S.data.questions.map((q) => { const r = rows.find((x) => x.client === q.client && x.slot === q.slot); return r ? qCard(q, [r]) : ""; }).join("")}</div>`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Scoreboard") : empty("Scoreboard");
    const sc = S.score, cell = (q, a) => S.answers.find((r) => r.client === q.client && r.slot === q.slot && r.arm === a);
    return `<h1>Scoreboard — client exceptions: week 1 ${sc.exceptions.week1}/${sc.exception_questions} · month 12 ${sc.exceptions.month12}/${sc.exception_questions} · background first ${sc.exceptions.first}/${sc.exception_questions}</h1>
      <p class="lead">Same questions, same model, same memories; only the wiring changed between columns.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th>${ORDER().map((a) => `<th>${esc(armLabel(a))}</th>`).join("")}</tr></thead><tbody>
        ${S.data.questions.map((q) => `<tr><td>${esc(client(q.client).name)}: ${esc(slotLabel(q.slot))}${q.exception ? "" : ' <span class="muted">(standard)</span>'}</td>${ORDER().map((a) => `<td>${markOf(cell(q, a)?.ok)}</td>`).join("")}</tr>`).join("")}
        <tr class="hl"><td><b>right</b></td>${ORDER().map((a) => `<td>${sc.totals[a]}/${sc.questions}</td>`).join("")}</tr>
        <tr><td>client exceptions honoured</td>${ORDER().map((a) => `<td>${sc.exceptions[a]}/${sc.exception_questions}</td>`).join("")}</tr>
        <tr><td>told the standard path instead</td>${ORDER().map((a) => `<td>${sc.standard[a]}</td>`).join("")}</tr>
        <tr><td>client line attached (checker)</td>${["week1", "month12"].map((a) => `<td>${sc.attached?.[a] ?? "–"}/${sc.exception_questions}</td>`).join("")}<td>in the system prompt</td></tr></tbody></table></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    const clients = S.data?.clients || [];
    return `<h1>Ask about any client</h1>
      <p class="lead">One question through all three wirings, as the memory stands now.</p>
      <div class="card"><div class="chips" id="client-chips">${clients.map((c) => `<button type="button" class="chip ${S.askClient === c.key ? "on" : ""}" data-client="${esc(c.key)}">${esc(c.name)}</button>`).join("")}</div>
        <div class="chat" id="chat-log">${S.asked.map((x) => `<div class="msg user"><span class="who">${esc(client(x.client).name)}</span>${esc(x.say)}</div>
          ${x.rows.map((r) => `<div class="msg bot"><span class="who">${esc(armLabel(r.arm))}</span>${esc(r.reply)}</div>`).join("")}`).join("") || (ready ? '<div class="empty">No questions yet.</div>' : '<div class="empty">Run the demo first.</div>')}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. How should they send us their shipments?" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["How should they send us their shipments?", "What do we need from their IT team before go-live?", "Draft the kickoff email agenda."].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>`;
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
    st.querySelectorAll("#client-chips .chip").forEach((c) => c.addEventListener("click", () => { S.askClient = c.dataset.client; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); send($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => send(c.dataset.q)));
    }
  }
  async function send(say) {
    if (!say) return;
    const log = $("#chat-log"); if (log) log.insertAdjacentHTML("beforeend", `<div class="msg user"><span class="who">${esc(client(S.askClient).name)}</span>${esc(say)}</div><span class="pill busy" id="sending"><span class="spinner"></span> asking all three wirings…</span>`);
    const r = await api("/api/ask", { client: S.askClient, say });
    if (r) S.asked.push(r);
    if (S.view === "ask") renderStage();
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
  function resetView() { S.memory = null; S.background = null; S.answers = []; S.inspect = []; S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's two playbook projects, three client actors (and every fact on them) and seven boundaries?")) return;
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
    else if (k === "memory") S.memory = d;
    else if (k === "background") S.background = d;
    else if (k === "answer") S.answers.push(d);
    else if (k === "inspect") S.inspect.push(d);
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the playbook projects, the client actors with their facts, and the boundaries are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: one question about any client, through all three wirings.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "background", "answer", "inspect", "scoreboard", "summary", "router_unavailable", "done", "error"];
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

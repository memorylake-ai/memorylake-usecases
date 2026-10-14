/* Policy exception memory for HR teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project for the HR desk", view: "setup" },
    { n: 3, title: "Intake", sub: "handbook, decisions, register", view: "intake" },
    { n: 4, title: "Quick read", sub: "a past approval, approve again", view: "quick" },
    { n: 5, title: "Memory read", sub: "limit, decisions, register", view: "memory" },
    { n: 6, title: "Write it back", sub: "the qualifier onto the ticket", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", topic: "", value: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const topicOf = (t) => story()?.topics?.[t] || { label: t, unit: "" };
  const amount = (v, topic) => { const u = topicOf(topic).unit; return u === "dollars" ? `${v} dollars` : `${v} ${v === 1 ? u.replace(/s$/, "") : u}`; };
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill ${t === "register" ? "warn" : t === "handbook" ? "accent" : ""}">${esc(toolName(t))}</span>`;
  const verdict = (v) => `<span class="pill ${v === "approve" ? "ok" : v === "decline" ? "err" : ""}">${esc(v)}</span>`;
  function hitTable(hits, mark) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · source</th></tr>
      ${hits.map((h) => `<tr class="${h.record === mark ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.record === mark ? '<span class="pill err">precedent</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  // The handbook limit, every decision on the topic, the register joined in by ticket id.
  function precTable(pc, mark) {
    const pol = pc.policy;
    return `<table class="iso"><tr><th>record</th><th>who · when</th><th>amount</th><th>basis</th></tr>
      ${pol ? `<tr><td class="mono small">${esc(pol.record)}</td><td class="small">handbook</td><td class="small"><b>limit ${esc(amount(pol.limit, pc.topic))}</b></td><td class="small muted">more needs a written ${esc(pol.exceptions_by)} exception</td></tr>` : ""}
      ${pc.rows.map((r) => `<tr class="${r.record === mark ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.subject)} <span class="faint">· ${esc(r.on)}</span></td>
        <td class="small" style="white-space:nowrap">${esc(amount(r.value, pc.topic))}</td><td class="small">${r.exception ? `<span class="pill warn">${esc(r.basis)}</span>` : `<span class="pill">${esc(r.basis)}</span>`}
        ${r.record === mark ? ' <span class="pill err">the quick read\'s precedent</span>' : ""}</td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any request · any topic</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, memory, writeback, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays Omar's first desk shift on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about a minute and a half · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“We approved six weeks before.” It was a one-time exception.</h1>
      <p class="lead">Larkspur Freight runs its HR desk out of one MemoryLake project — the people handbook, every decision the desk made this year, and the CPO office's exception register.
      An assistant that remembers how HR handled a request before finds the ticket where Priya got six weeks in Lisbon, and approves Dana's five weeks in Porto. The ticket is true; it just isn't a precedent —
      the “one-time, not a precedent” line is in the exception register, a different record from the ticket. The memory read joins them and judges the request against the handbook limit.
      This page drives the <code>memorylake</code> CLI for real. The company and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-hr-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for HR teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for the HR desk");
    return `<h1>One project for the HR desk</h1>
      <p class="lead">“Candidate history and employee context carry forward automatically.” The handbook, the desk's decisions and the exception register live in one project;
      each record keeps its own words in the text and its topic, amount and outcome (or, for the register, the ticket it covers) in metadata.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const by = (k) => x.records.filter((r) => r.kind === k);
    return `<h1>Intake — ${x.records.length} records from three sources</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each.
      ${x.restored ? ` <span class="faint">(${x.restored} ticket(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">The exception register, as the CPO office keeps it</h2><table class="iso">
        ${by("exception").map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}<br><span class="faint">→ ${esc(r.ticket)}</span></td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">HR desk decisions · ${by("decision").length}</h2><table class="iso"><tr><th>ticket</th><th>topic</th><th>amount</th><th>as the desk closed it</th></tr>
        ${by("decision").map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(topicOf(r.topic).label)}</td><td class="small" style="white-space:nowrap">${esc(amount(r.value, r.topic))}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">People handbook · ${by("policy").length}</h2><table class="iso">
        ${by("policy").map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body)}<br><span class="pill accent">limit ${esc(amount(r.limit, r.topic))}</span></td></tr>`).join("")}</table></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — approved before, approve again</h1>
      <p class="lead">What an assistant does with “AI that remembers every interaction”: <code>search</code> the request, take the best-ranked past ticket of the same kind approved for as much or more, and approve again.
      A ticket that itself says it was one-time is skipped.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.ticket.id)} · ${esc(q.ticket.who)} · ${esc(amount(q.ticket.value, q.ticket.topic))}</h2>${verdict(q.advice)}</div>
        <p class="small">“${esc(q.ticket.text)}”</p>${hitTable(q.hits, q.precedent)}
        ${q.precedent ? `<p class="small" style="margin-bottom:0">${esc(q.precedent)}: <b>“${esc(q.precedent_body)}”</b></p>` : ""}
        <p class="small" style="margin-bottom:0">→ ${esc(q.said)}</p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    const excOf = (r) => { const row = r.prec.rows.find((p) => p.record === r.precedent); return row?.exception ? r.prec.exceptions.find((e) => e.record === row.exception) : null; };
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} approvals rest on a one-time exception</h1>
      <p class="lead">The handbook section on the topic, every decision on it and the exception register (<code>fact list</code>, every page), joined by ticket id. The request is judged against the handbook limit.</p>
      <div class="card"><table class="iso"><tr><th>request</th><th>quick read</th><th>handbook + register</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.ticket.id)}</span> ${esc(r.ticket.who)} · ${esc(amount(r.ticket.value, r.ticket.topic))}</td>
          <td class="small">${verdict(r.quick)} ${r.precedent ? `<span class="mono faint">${esc(r.precedent)}</span>` : ""}</td><td class="small">${verdict(r.memory.advice)}</td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => { const e = excOf(r); return `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.ticket.id)} · ${esc(r.ticket.who)} · ${esc(topicOf(r.ticket.topic).label)}</h2>${agreePill(r.agree)}</div>
        <p class="small">“${esc(r.ticket.text)}”</p>${precTable(r.prec, r.precedent)}
        ${e ? `<p class="small">${esc(r.precedent)}'s qualifier is in another record — <b>${esc(e.record)}</b>: “${esc(e.body)}”</p>` : ""}
        <p class="small" style="margin-bottom:0"><b>${esc(r.memory.said)}</b></p></div>`; }).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">Each exception's qualifier goes onto the ticket it covers (<code>fact update --text</code>; the old text goes to <code>metadata.previous</code>, the register id and scope too), and <code>fact trace</code> keeps both versions.
      Later the same day, three more requests go through the <b>same quick-read code</b>, asked in new words.</p>
      <div class="card mine"><h2 style="margin-top:0">Tickets</h2><table class="iso"><tr><th>ticket</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small">${esc(u.record)}<br><span class="faint">${esc(u.exception)}</span></td><td class="small muted">${esc(u.was)}</td><td class="small"><b>${esc(u.now)}</b></td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace ${esc(x.updated[0]?.record)}</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">Later requests, quick read again</h2><table class="iso"><tr><th>request</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.ticket.id)}</span> · ${esc(amount(a.ticket.value, a.ticket.topic))}<br>“${esc(a.ticket.text)}”</td>
          <td class="small">${verdict(a.quick)}<br>${esc(a.said)}${a.tries > 1 ? ` <span class="faint">· try ${a.tries}</span>` : ""}</td><td class="small">${verdict(a.memory)}<br>${esc(a.memory_said)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, topics = Object.entries(story()?.topics || {});
    return `<h1>Ask</h1>
      <p class="lead">Type any request, pick its topic and how much is asked for: the quick read (a past approval that covers it) and the memory read (handbook limit + every decision + the register) side by side.
      Or list every decision on a topic with its basis.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Can I work from Lisbon for 6 weeks next summer?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-topic" ${ready ? "" : "disabled"} style="max-width:190px">${topics.map(([k, t]) => `<option value="${esc(k)}" ${k === a.topic ? "selected" : ""}>${esc(t.label)}</option>`).join("")}</select>
          <input id="ask-value" type="number" min="1" placeholder="amount" value="${esc(a.value)}" ${ready ? "" : "disabled"} style="max-width:110px">
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-topic="${esc(e.topic)}" data-value="${esc(e.value)}">${esc(e.q)}</button>`).join("")}</div>
        <div class="chips">${topics.map(([k, t]) => `<button type="button" class="chip" data-prec="${esc(k)}">precedents: ${esc(t.label)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? a.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  // ---------------------------------------------------------------- behaviour
  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askRequest($("#ask-q").value.trim(), $("#ask-topic").value, $("#ask-value").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-topic").value = c.dataset.topic; $("#ask-value").value = c.dataset.value; askRequest(c.dataset.q, c.dataset.topic, c.dataset.value); }));
      st.querySelectorAll(".chips .chip[data-prec]").forEach((c) => c.addEventListener("click", () => askPrecedents(c.dataset.prec)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askRequest(q, topic, value) {
    if (!q) return; S.ask.q = q; S.ask.topic = topic; S.ask.value = value;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the desk's memory…</span>`;
    const r = await api("/api/ask", { q, topic, value: Number(value) });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.ticket.text)}” · ${esc(amount(r.ticket.value, r.ticket.topic))}</h2>${agreePill(r.agree)}</div>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${r.precedent ? `${esc(r.precedent)}: “${esc(r.precedent_body)}”<br>` : ""}→ <b>${esc(r.quick_said)}</b></p></div>
        <div><h3>Memory read</h3><p class="small">→ <b>${esc(r.memory.said)}</b></p></div></div>
      ${precTable(r.prec, r.precedent)}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.precedent)}</details>`);
  }
  async function askPrecedents(topic) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/precedents", { topic });
    if (!r) { box.innerHTML = ""; return; }
    const n = r.rows.filter((x) => x.exception).length;
    putResult(box, `<div class="row spread"><h2><code>fact list</code> · every decision on ${esc(topicOf(topic).label)}</h2><span class="pill">${r.rows.length} decision(s)</span></div>
      ${precTable(r, "")}
      ${r.exceptions.map((e) => `<p class="small"><span class="mono">${esc(e.record)}</span> → ${esc(e.ticket)}: “${esc(e.body)}”</p>`).join("")}
      <p class="small"><b>${n} of ${r.rows.length}</b> decision(s) were one-time exceptions — not precedents.</p>`);
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
    ["#btn-run", "#btn-reset", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.memory = null; S.writeback = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's HR desk project and every record on it from your account?")) return;
    banner(""); const r = await api("/api/cleanup", {}); if (r) { S.st = r; setConn(); }
  });

  // ---------------------------------------------------------------- terminal
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

  // ---------------------------------------------------------------- events
  const stepOf = (i) => STEPS.find((s) => s.n === i);
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; S.step = d.index || 0;
      if (d.index === 2) resetView();
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "intake") S.intake = d;
    else if (k === "quick") S.quick = d;
    else if (k === "memory") S.memory = d;
    else if (k === "writeback") S.writeback = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and every record on it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try a request of your own — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick", "memory", "writeback",
    "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    // "error" is also EventSource's own connection-error event, which has no data: skip those.
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
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.writeback ? "writeback" : "connect");
    subscribe();
  })();
})();

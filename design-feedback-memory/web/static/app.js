/* Design feedback memory across clients — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project for the studio", view: "setup" },
    { n: 3, title: "Intake", sub: "Figma, email, Loom, notes", view: "intake" },
    { n: 4, title: "Quick read", sub: "the client's note, followed", view: "quick" },
    { n: 5, title: "Memory read", sub: "rounds with a choice only", view: "memory" },
    { n: 6, title: "Write it back", sub: "the notes from the rounds", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, coverage: null, memory: null, writeback: null,
    ask: { q: "", client: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const clientName = (c) => story()?.clients?.[c] || c;
  const topicLabel = (t) => story()?.topics?.[t]?.label || t;
  const opt = (t, v) => story()?.topics?.[t]?.options?.[v] || v;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill ${t === "notes" ? "accent" : ""}">${esc(toolName(t))}</span>`;
  const advPill = (a) => `<span class="pill ${a === "default to it" ? "ok" : a === "show both" || a === "unconfirmed" ? "warn" : "err"}">${esc(a)}</span>`;
  const rolePill = (r) => `<span class="pill ${r === "for" ? "ok" : r === "against" ? "err" : ""}">${r === "echo" ? "echo · not counted" : esc(r)}</span>`;
  const comp = (t, shown) => (shown || []).map((s) => opt(t, s)).join(" + ") + ((shown || []).length > 1 ? "" : " only");
  function hitTable(hits, note) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · source</th></tr>
      ${hits.map((h) => `<tr class="${h.record === note ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.record === note ? '<span class="pill warn">the note</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  function roundTable(rows, topic) {
    return `<table class="iso"><tr><th>round</th><th>what the comp showed</th><th>the client</th><th></th></tr>
      ${rows.map((r) => `<tr class="${r.role === "echo" ? "" : "hl"}"><td class="small" style="white-space:nowrap"><span class="mono">${esc(r.round)}</span> <span class="faint">${esc(r.on)}</span><br>${toolPill(r.tool)}</td>
        <td class="small">${esc(comp(topic, r.shown))}<br><span class="faint">${esc(r.asset)}</span></td>
        <td class="small">${esc(r.why)}<br><span class="faint">“${esc(r.body)}”</span></td><td>${rolePill(r.role)}</td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any brief · any client</small></span></button>`;
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
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays this morning's briefs on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>“Approved in 6 of 7 rounds.” Five of them only had one option.</h1>
      <p class="lead">Quillon Studio keeps its client feedback in one MemoryLake project — every review round from Figma, email and Loom, and one studio note per client preference.
      The note says Harbor &amp; Pine wants tight type: approved with tight type in 6 of 7 rounds. That count is right. But once the note was written, the comps only showed tight type, and the client approved the only option on the table.
      The quick read follows the note. The memory read goes back to the rounds and counts only those where the client had a choice.
      This page drives the <code>memorylake</code> CLI for real. The studio, clients and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/design-feedback-memory-across-clients" target="_blank" rel="noopener">memorylake.ai › Design feedback memory across clients</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for the studio's client feedback");
    return `<h1>One project for the studio's client feedback</h1>
      <p class="lead">“Each piece of feedback becomes typed memory linked to client.” The review rounds and the studio notes live in one project; each record keeps the client's own words in the text and its fields (client, round, what the comp showed, how the client answered) in metadata.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const notes = x.records.filter((r) => r.kind === "note");
    const rounds = x.records.filter((r) => r.kind === "round");
    const clients = Object.keys(story()?.clients || {});
    return `<h1>Intake — ${x.records.length} records from three tools and the studio's notes</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each (client, point, round, date, what the comp showed, how the client answered; for a note, the preference, its count and its advice).
      ${x.restored ? ` <span class="faint">(${x.restored} note(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">The studio notes, as they stand</h2><table class="iso">
        ${notes.map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}<br><span class="faint">${esc(r.on)}</span></td><td class="small">${esc(r.body)}<br><span class="pill accent">${esc(opt(r.topic, r.value))}: ${esc(r.prefer)}</span> <span class="pill">counts ${esc((r.evidence || []).join(" of "))}</span></td></tr>`).join("")}</table></div>
      ${clients.map((c) => { const rs = rounds.filter((r) => r.client === c); return `<div class="card"><h2 style="margin-top:0">${esc(clientName(c))} · ${rs.length} rounds</h2><table class="iso"><tr><th>round</th><th>comp</th><th>the client wrote</th></tr>
        ${rs.map((r) => `<tr><td class="small" style="white-space:nowrap"><span class="mono">${esc(r.round)}</span> <span class="faint">${esc(r.on)}</span><br>${toolPill(r.tool)}</td><td class="small">${esc(comp(r.topic, r.shown))}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`; }).join("")}`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — default to the note</h1>
      <p class="lead">What every AI design tool does with feedback memory: <code>search</code> the brief, take the client's note on that point, default to it.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.ticket.id)} · ${esc(clientName(q.ticket.client))}, ${esc(topicLabel(q.ticket.topic))}</h2>${advPill(q.advice || "?")}</div>
        <p class="small">“${esc(q.ticket.text)}”</p>${hitTable(q.hits, q.note)}
        <p class="small" style="margin-bottom:0">${esc(q.note)}: <b>“${esc(q.note_body)}”</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} answers change</h1>
      <p class="lead">Every review round (<code>fact list</code>, every page), one question each: did the client have a choice? A round where the client asked for something, or picked from two options, is evidence.
      A round where the comp only had what the note says, after the note was written, is the note's own output being approved — an echo, not counted.
      (Default to it: two or more rounds for, none against. Show both: rounds go both ways. Unconfirmed: one round.)</p>
      <div class="card"><table class="iso"><tr><th>brief</th><th>the note says</th><th>the rounds say</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.ticket.id)}</span> ${esc(clientName(r.ticket.client))}, ${esc(topicLabel(r.ticket.topic))}</td>
          <td>${advPill(r.quick || "?")}</td><td>${advPill(r.memory.advice)}</td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.ticket.id)} · ${esc(r.memory.note)} · ${esc(clientName(r.ticket.client))}</h2>${agreePill(r.agree)}</div>
        <p class="small">“${esc(r.ticket.text)}”</p>${roundTable(r.rounds, r.ticket.topic)}
        <p class="small" style="margin-bottom:0">${advPill(r.memory.advice)} ${esc(r.memory.verdict.why)}</p></div>`).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">Each note the rounds disagree with is rewritten from the rounds (<code>fact update --text</code>; the old text, the old advice and the counts go to metadata), and <code>fact trace</code> keeps both versions.
      Later the same day, three more briefs go through the <b>same quick-read code</b>, asked in new words (<code>search</code> answers a repeated question from its cache).</p>
      <div class="card mine"><h2 style="margin-top:0">Studio notes</h2><table class="iso"><tr><th>id</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small" style="white-space:nowrap">${esc(u.record)}</td><td class="small muted">${esc(u.was)}<br>${advPill(u.was_prefer)}</td><td class="small"><b>${esc(u.now)}</b><br>${advPill(u.now_prefer)}</td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace ${esc(x.updated[0]?.record)}</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">Later briefs, quick read again</h2><table class="iso"><tr><th>brief</th><th>note</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.ticket.id)}</span> · ${esc(clientName(a.ticket.client))}<br>“${esc(a.ticket.text)}”</td><td class="mono small" style="white-space:nowrap">${esc(a.note)} <span class="faint">#${esc(a.note_rank)}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td>${advPill(a.quick || "?")}</td><td>${advPill(a.memory)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, clients = Object.entries(story()?.clients || {});
    return `<h1>Ask</h1>
      <p class="lead">Write any brief and pick the client: the quick read (the note's advice) and the memory read (rounds with a choice only) side by side.
      Or list every round on one client's note.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Tight or airy headlines for the menu boards?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-c" ${ready ? "" : "disabled"} style="max-width:220px">${clients.map(([k, v]) => `<option value="${esc(k)}" ${k === a.client ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-client="${esc(e.client)}">${esc(e.q)}</button>`).join("")}</div>
        <div class="chips">${clients.map(([k, v]) => `<button type="button" class="chip" data-rounds="${esc(k)}">every round: ${esc(v)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askBrief($("#ask-q").value.trim(), $("#ask-c").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-c").value = c.dataset.client; askBrief(c.dataset.q, c.dataset.client); }));
      st.querySelectorAll(".chips .chip[data-rounds]").forEach((c) => c.addEventListener("click", () => askRounds(c.dataset.rounds)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askBrief(q, client) {
    if (!q) return; S.ask.q = q; S.ask.client = client;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the rounds…</span>`;
    const r = await api("/api/ask", { q, client });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.ticket.text)}” · ${esc(clientName(client))}, ${esc(r.label)}</h2>${agreePill(r.agree)}</div>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${esc(r.note || "no note on this point in the top 8")}: “${esc(r.note_body)}”<br>→ ${advPill(r.quick || "?")}</p>
        ${r.before ? `<p class="small faint">Before a run rewrote ${esc(r.note)} it said: “${esc(r.before.text)}” → ${esc(r.before.advice)} (${r.before.advice === r.memory.advice ? "✓ same" : "✗ differs"})</p>` : ""}</div>
        <div><h3>Memory read</h3><p class="small">${advPill(r.memory.advice)} ${esc(r.memory.verdict.why)}</p></div></div>
      ${roundTable(r.rounds, r.ticket.topic)}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.note)}</details>`);
  }
  async function askRounds(client) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/rounds", { client });
    if (!r) { box.innerHTML = ""; return; }
    const n = r.note;
    putResult(box, `<div class="row spread"><h2><code>fact list</code> · every round on ${esc(n.record)}, ${esc(clientName(client))}</h2>${advPill(r.verdict.advice)}</div>
      <p class="small"><b>${esc(n.record)}${n.previous ? " now" : ""}:</b> “${esc(n.body)}”${n.previous ? `<br><span class="faint">was: “${esc(n.previous)}”</span>` : ""}</p>
      ${roundTable(r.rows, n.topic)}<p class="small">${advPill(r.verdict.advice)} ${esc(r.verdict.why)}</p>`);
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.coverage = null; S.memory = null; S.writeback = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's studio project and every record on it from your account?")) return;
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
    else if (k === "coverage") S.coverage = d;
    else if (k === "memory") S.memory = d;
    else if (k === "writeback") S.writeback = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and every record on it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try a brief of your own — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick", "coverage", "memory", "writeback",
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

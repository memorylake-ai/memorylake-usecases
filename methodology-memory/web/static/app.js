/* Methodology memory for consultants — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project for the practice", view: "setup" },
    { n: 3, title: "Intake", sub: "deck, case library, register", view: "intake" },
    { n: 4, title: "Quick read", sub: "the deck entry, followed", view: "quick" },
    { n: 5, title: "Memory read", sub: "the register, stalled included", view: "memory" },
    { n: 6, title: "Write it back", sub: "the deck from the register", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, coverage: null, memory: null, writeback: null,
    ask: { q: "", technique: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill ${t === "deck" ? "accent" : ""}">${esc(toolName(t))}</span>`;
  const pct = (k, n) => n ? Math.round((100 * k) / n) : 0;
  const tName = (t) => story()?.techniques?.[t] || t;
  const advPill = (a) => `<span class="pill ${a === "use it" ? "ok" : a === "optional" ? "warn" : "err"}">${esc(a)}</span>`;
  const outPill = (o) => `<span class="pill ${o === "implemented" ? "ok" : "err"}">${esc(o)}</span>`;
  function hitTable(hits, method) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · source</th></tr>
      ${hits.map((h) => `<tr class="${h.record === method ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.record === method ? '<span class="pill warn">the deck entry</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  const bar = (g) => g.n ? `<div class="rate"><span class="fill" style="width:${pct(g.k, g.n)}%"></span></div><span class="mono small">${g.k}/${g.n} · ${pct(g.k, g.n)}%</span>` : `<span class="pill err">none</span>`;
  function coverageTable(cov) {
    return `<table class="iso"><tr><th>90-day outcome</th><th>engagements</th><th>with a case study</th></tr>
      ${cov.rows.map((r) => `<tr class="${r.written ? "" : "hl"}"><td>${outPill(r.outcome)}</td><td class="mono small">${r.n}</td>
        <td style="min-width:170px">${bar({ k: r.written, n: r.n })}${r.written && r.without.length ? `<br><span class="small faint">none for ${esc(r.without.join(", "))}</span>` : ""}</td></tr>`).join("")}</table>`;
  }
  function techTable(tb) {
    const name = tName(tb.technique);
    return `<table class="iso"><tr><th>source</th><th>clients implemented</th><th>records</th></tr>
      <tr><td class="small"><b>case library</b>, with the ${esc(name)}<br><span class="pill warn">what the deck counted</span></td><td style="min-width:150px">${bar(tb.library)}</td><td class="mono small">${esc(tb.library.ids.join(", "))}</td></tr>
      <tr class="hl"><td class="small"><b>register</b>, with the ${esc(name)}</td><td>${bar(tb.with_)}</td><td class="mono small">${esc(tb.with_.ids.join(", "))}</td></tr>
      <tr class="hl"><td class="small"><b>register</b>, without it</td><td>${bar(tb.without)}</td><td class="mono small">${esc(tb.without.ids.join(", "))}</td></tr></table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Plan<small>any question · any technique</small></span></button>`;
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
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays Tomás's scoping session on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>“6 of 6 case studies.” Stalled engagements never get one.</h1>
      <p class="lead">Halvorsen &amp; Pike keeps its practice memory in one MemoryLake project — the methodology deck, the case library and the PMO's register of every engagement.
      The deck says to always open with the Diagnostic Sprint, because every case study where the firm ran it ended with the client implementing — 6 of 6. That count is right. But a case study is only written once the client agrees to be a reference, so a stalled engagement never becomes one.
      The quick read follows the deck. The memory read goes back to the register, stalled engagements included.
      This page drives the <code>memorylake</code> CLI for real. The firm, clients and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/methodology-memory-for-consultants" target="_blank" rel="noopener">memorylake.ai › Methodology memory for consultants</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for the firm's practice memory");
    return `<h1>One project for the firm's practice memory</h1>
      <p class="lead">“Each framework, technique, and pattern callable.” The deck, the case library and the engagement register live in one project; each record keeps its own words in the text and its fields (techniques used, 90-day outcome, case study) in metadata.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const by = (k) => x.records.filter((r) => r.kind === k);
    const eng = by("engagement").sort((a, b) => a.on.localeCompare(b.on));
    return `<h1>Intake — ${x.records.length} records from three sources</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each (techniques used, 90-day outcome, case study; for a deck entry, the technique, its evidence and its advice).
      ${x.restored ? ` <span class="faint">(${x.restored} deck entr${x.restored === 1 ? "y" : "ies"} an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">The methodology deck, as it stands</h2><table class="iso">
        ${by("method").map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body)}<br><span class="pill accent">${esc(tName(r.technique))}: ${esc(r.prefer)}</span></td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Case library · ${by("case").length} + policy</h2><table class="iso">
        ${[...by("policy"), ...by("case")].map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Engagement register · ${eng.length}</h2><table class="iso"><tr><th>engagement</th><th>techniques</th><th>90-day</th><th>case study</th></tr>
        ${eng.map((r) => `<tr><td class="small"><span class="mono">${esc(r.record)}</span> ${esc(r.client)} <span class="faint">· ${esc(r.on)}</span></td>
          <td class="small">${(r.used || []).map((c) => `<span class="pill">${esc(c)}</span>`).join(" ") || '<span class="faint">none</span>'}</td>
          <td>${outPill(r.outcome)}</td><td class="mono small">${esc(r.case)}</td></tr>`).join("")}</table></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — follow the deck</h1>
      <p class="lead">What every AI tool does with methodology memory: <code>search</code> the scoping question, take the deck entry on that technique, follow it.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.ticket.id)} · ${esc(tName(q.ticket.technique))}</h2>${advPill(q.advice || "?")}</div>
        <p class="small">“${esc(q.ticket.text)}”</p>${hitTable(q.hits, q.method)}
        <p class="small" style="margin-bottom:0">${esc(q.method)}: <b>“${esc(q.method_body)}”</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    const pol = S.intake?.records?.find((r) => r.kind === "policy");
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} answers change</h1>
      <p class="lead">Every engagement in the register (<code>fact list</code>, every page). First, what the case library covers; then each technique, engagements that used it against engagements that did not
      (use it if clients implemented ${esc(story()?.gap)} points more often with it, avoid it if ${esc(story()?.gap)} points less often, optional in between).</p>
      ${S.coverage ? `<div class="card mine"><h2 style="margin-top:0">The case library against the register</h2>${coverageTable(S.coverage)}
        ${pol ? `<p class="small">${esc(pol.record)}: “${esc(pol.body)}”</p>` : ""}<p class="small" style="margin-bottom:0"><b>A stalled engagement never gets a case study, so every technique in the library scores 100%.</b></p></div>` : ""}
      <div class="card"><table class="iso"><tr><th>question</th><th>the deck says</th><th>the register says</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.ticket.id)}</span> ${esc(tName(r.ticket.technique))}</td>
          <td>${advPill(r.quick || "?")}</td><td>${advPill(r.memory.advice)}</td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.ticket.id)} · ${esc(tName(r.ticket.technique))}</h2>${agreePill(r.agree)}</div>
        <p class="small">“${esc(r.ticket.text)}”</p>${techTable(r.table)}
        <p class="small" style="margin-bottom:0">${advPill(r.memory.advice)} ${esc(r.memory.verdict.why)}</p></div>`).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">Each deck entry the register disagrees with is rewritten from the register (<code>fact update --text</code>; the old text, the old advice and the counts go to metadata), and <code>fact trace</code> keeps both versions.
      Later the same day, three more questions go through the <b>same quick-read code</b>, asked in new words (<code>search</code> answers a repeated question from its cache).</p>
      <div class="card mine"><h2 style="margin-top:0">Deck entries</h2><table class="iso"><tr><th>id</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small" style="white-space:nowrap">${esc(u.record)}</td><td class="small muted">${esc(u.was)}<br>${advPill(u.was_prefer)}</td><td class="small"><b>${esc(u.now)}</b><br>${advPill(u.now_prefer)}</td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace ${esc(x.updated[0]?.record)}</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">Later questions, quick read again</h2><table class="iso"><tr><th>question</th><th>deck entry</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.ticket.id)}</span> · ${esc(tName(a.ticket.technique))}<br>“${esc(a.ticket.text)}”</td><td class="mono small" style="white-space:nowrap">${esc(a.method)} <span class="faint">#${esc(a.method_rank)}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td>${advPill(a.quick || "?")}</td><td>${advPill(a.memory)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, techs = Object.entries(story()?.techniques || {});
    return `<h1>Plan</h1>
      <p class="lead">Ask any scoping question and pick the technique it is about: the quick read (the deck entry's advice) and the memory read (the register, stalled engagements included) side by side.
      Or list every engagement against one technique.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Do we insist on the Diagnostic Sprint?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-t" ${ready ? "" : "disabled"} style="max-width:220px">${techs.map(([k, v]) => `<option value="${esc(k)}" ${k === a.technique ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Plan</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-technique="${esc(e.technique)}">${esc(e.q)}</button>`).join("")}</div>
        <div class="chips">${techs.map(([k, v]) => `<button type="button" class="chip" data-register="${esc(k)}">register by: ${esc(v)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askPlan($("#ask-q").value.trim(), $("#ask-t").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-t").value = c.dataset.technique; askPlan(c.dataset.q, c.dataset.technique); }));
      st.querySelectorAll(".chips .chip[data-register]").forEach((c) => c.addEventListener("click", () => askRegister(c.dataset.register)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askPlan(q, technique) {
    if (!q) return; S.ask.q = q; S.ask.technique = technique;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the register…</span>`;
    const r = await api("/api/plan", { q, technique });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.ticket.text)}” · ${esc(tName(technique))}</h2>${agreePill(r.agree)}</div>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${esc(r.method || "no deck entry on this technique in the top 8")}: “${esc(r.method_body)}”<br>→ ${advPill(r.quick || "?")}</p>
        ${r.before ? `<p class="small faint">Before a run rewrote ${esc(r.method)} it said: “${esc(r.before.text)}” → ${esc(r.before.advice)} (${r.before.advice === r.memory.advice ? "✓ same" : "✗ differs"})</p>` : ""}</div>
        <div><h3>Memory read</h3><p class="small">${advPill(r.memory.advice)} ${esc(r.memory.verdict.why)}</p></div></div>
      ${coverageTable(r.coverage)}${techTable(r.table)}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.method)}</details>`);
  }
  async function askRegister(technique) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/register", { technique });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2><code>fact list</code> · every engagement, with and without the ${esc(tName(technique))}</h2><span class="pill">${r.engagements.length} engagement(s)</span></div>
      ${coverageTable(r.coverage)}${techTable(r.table)}<p class="small">${advPill(r.verdict.advice)} ${esc(r.verdict.why)}</p>
      <details><summary class="small">the ${r.engagements.length} engagements</summary><table class="iso"><tr><th>engagement</th><th>techniques</th><th>90-day</th><th>case</th></tr>
      ${r.engagements.map((e) => `<tr class="${(e.used || []).includes(technique) ? "hl" : ""}"><td class="small"><span class="mono">${esc(e.record)}</span> ${esc(e.client)} <span class="faint">${esc(e.on)}</span></td><td class="small">${esc((e.used || []).join(", ") || "—")}</td><td>${outPill(e.outcome)}</td><td class="mono small">${esc(e.case)}</td></tr>`).join("")}</table></details>`);
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
    if (!confirm("Delete the demo's practice project and every record on it from your account?")) return;
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
      else if (S.caughtUp) banner("Done. Try a scoping question of your own — “Plan” in the sidebar.", true);
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

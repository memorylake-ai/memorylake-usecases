/* Source channel memory for recruiting teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project for hiring sources", view: "setup" },
    { n: 3, title: "Intake", sub: "hires, outcomes, channel notes", view: "intake" },
    { n: 4, title: "Quick read", sub: "the closest channel note", view: "quick" },
    { n: 5, title: "Memory read", sub: "same follow-up time", view: "memory" },
    { n: 6, title: "Write it back", sub: "the note from the follow-up", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", family: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill ${t === "notes" ? "accent" : ""}">${esc(toolName(t))}</span>`;
  const pct = (t) => t && t[1] ? Math.round((100 * t[0]) / t[1]) : 0;
  function hitTable(hits, note) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · source</th></tr>
      ${hits.map((h) => `<tr class="${h.record === note ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.record === note ? '<span class="pill warn">closest note</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  // One bar per cell: passed of the hires who have reached that day. 0/0 is "not readable", not 0%.
  function cell(t, strong) {
    if (!t[1]) return `<span class="pill warn">0/0 · not yet</span>`;
    return `<div class="rate ${strong ? "strong" : ""}"><span class="fill" style="width:${pct(t)}%"></span></div><span class="mono small">${t[0]}/${t[1]} · ${pct(t)}%</span>`;
  }
  function cohortTable(tb, quick, memory) {
    const hz = String(tb.horizon);
    return `<table class="iso"><tr><th>channel</th><th>still here</th>${tb.horizons.map((d) => `<th class="${String(d) === hz ? "seghead" : ""}">at ${d} days${String(d) === hz ? " · 6-month review" : ""}</th>`).join("")}</tr>
      ${tb.rows.map((r) => `<tr class="${r.channel === memory ? "hl" : ""}"><td class="small"><b>${esc(r.channel)}</b> <span class="faint">· ${r.hired} hired</span><br>${r.channel === quick ? `<span class="pill ${quick === memory ? "accent" : "err"}">the note</span> ` : ""}${r.channel === memory ? '<span class="pill ok">memory read</span> ' : ""}${r.unproven ? `<span class="pill warn">readable ${esc(r.readable_on)}</span>` : ""}</td>
        <td style="min-width:110px">${cell(r.still, false)}</td>${tb.horizons.map((d) => `<td style="min-width:110px">${cell(r.at[String(d)], String(d) === hz)}</td>`).join("")}</tr>`).join("")}</table>
      <p class="small faint" style="margin-bottom:0">Each “at N days” cell counts only the hires who have reached day N (or left before it): passed / reached.</p>`;
  }
  const whyList = (w) => w?.length ? `<ul class="small">${w.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Review<small>any sourcing question · cohorts</small></span></button>`;
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
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays Amara's first channel review on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>“All 5 are still here.” None of them has been here long enough to leave.</h1>
      <p class="lead">Corvid Pay keeps its hiring memory in one MemoryLake project — every hire from the ATS, what happened to them from the HRIS, and the channel notes the last recruiting lead left.
      His backend note is true word for word: all 5 Ferrous Guild hires are still here, LinkedIn kept 4 of 6. But the Guild hires started in June — 37 to 136 days ago — and LinkedIn's losses came on days 143 and 158.
      The quick read follows the note. The memory read counts every channel at the same follow-up time, over only the hires who got that far — and Ferrous Guild cannot be read until January.
      This page drives the <code>memorylake</code> CLI for real. The company and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/source-channel-memory-for-recruiting-teams" target="_blank" rel="noopener">memorylake.ai › Source channel memory for recruiting teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for hiring sources");
    return `<h1>One project for hiring sources</h1>
      <p class="lead">“Event memory of source outcomes · per-channel reflection memory.” Both live in one project; each hire keeps its channel, role family and start date in metadata, each outcome its exit day or review result, and every record its own words in the text.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const notes = x.records.filter((r) => r.kind === "note");
    const hires = x.records.filter((r) => r.kind === "hire").sort((a, b) => a.start.localeCompare(b.start));
    const outc = {}; x.records.filter((r) => r.kind === "exit" || r.kind === "review").forEach((r) => { (outc[r.hire] = outc[r.hire] || []).push(r); });
    const outPills = (h) => (outc[h.hire] || []).map((r) => r.kind === "exit" ? `<span class="pill err">left day ${esc(r.day)}</span>` : `<span class="pill ok">6-month review ${esc(r.result)}</span>`).join(" ") || '<span class="pill">no outcome yet</span>';
    return `<h1>Intake — ${x.records.length} records from three sources</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each (channel, role family, start date; exit day; review result; for a note, the family and the channel it prefers).
      ${x.restored ? ` <span class="faint">(${x.restored} note(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">Jonah's notes, as he left them</h2><table class="iso">
        ${notes.map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td class="small">${esc(r.body)}<br><span class="pill accent">${esc(r.family)} · prefers ${esc(r.prefer)}</span></td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Hires · ${hires.length} <span class="small faint">and what the HRIS says happened</span></h2><table class="iso"><tr><th>id</th><th>hire</th><th>family</th><th>channel</th><th>started</th><th>outcome</th></tr>
        ${hires.map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.name)} <span class="faint">· ${esc(r.role)}</span></td>
          <td><span class="pill ${r.family === "data" ? "accent" : ""}">${esc(r.family)}</span></td><td class="small">${esc(r.channel)}</td><td class="mono small">${esc(r.start)}</td>
          <td>${outPills(r)}</td></tr>`).join("")}</table></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — follow the closest channel note</h1>
      <p class="lead">What an assistant does with “per-channel reflection memory”: <code>search</code> the req, take the closest channel note for its role family, lead with the channel it names.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.ticket.id)} · ${esc(q.ticket.title)} · ${esc(q.ticket.family)}</h2><span class="pill accent">→ ${esc(q.advice || "?")}</span></div>
        <p class="small">“${esc(q.ticket.text)}”</p>${hitTable(q.hits, q.note)}
        <p class="small" style="margin-bottom:0">${esc(q.note)}: <b>“${esc(q.note_body)}”</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} reqs go the other way</h1>
      <p class="lead">Every hire and outcome (<code>fact list</code>, every page), each channel counted <b>at the same follow-up time</b> — over only the hires who have got that far. The call is made on the ${esc(story()?.horizon)}-day review, among channels with ${esc(story()?.min_reached)} or more hires past it.</p>
      <div class="card"><table class="iso"><tr><th>req</th><th>the note says</th><th>the follow-up says</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.ticket.id)}</span> ${esc(r.ticket.title)} · ${esc(r.ticket.family)}</td>
          <td class="small">${esc(r.quick)}</td><td class="small"><b>${esc(r.memory.advice)}</b></td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.ticket.id)} · ${esc(r.ticket.title)} · ${esc(r.ticket.family)}</h2>${agreePill(r.agree)}</div>
        <p class="small">“${esc(r.ticket.text)}”</p>${cohortTable(r.table, r.quick, r.memory.advice)}${whyList(r.why)}
        <p class="small" style="margin-bottom:0"><b>${esc(r.memory.said)}</b></p></div>`).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">The note that read an open follow-up window as zero losses is rewritten from the hires and their outcomes (<code>fact update --text</code>; the old text, the old advice and the counts go to metadata), and <code>fact trace</code> keeps both versions.
      Later the same day, three more reqs go through the <b>same quick-read code</b>, asked in new words (<code>search</code> answers a repeated question from its cache).</p>
      <div class="card mine"><h2 style="margin-top:0">Notes</h2><table class="iso"><tr><th>id</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small">${esc(u.record)}</td><td class="small muted">${esc(u.was)}<br><span class="pill err">prefers ${esc(u.was_prefer)}</span></td><td class="small"><b>${esc(u.now)}</b><br><span class="pill ok">prefers ${esc(u.now_prefer)}</span></td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace ${esc(x.updated[0]?.record)}</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">Later reqs, quick read again</h2><table class="iso"><tr><th>req</th><th>closest note</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.ticket.id)}</span> · ${esc(a.ticket.title)} · ${esc(a.ticket.family)}<br>“${esc(a.ticket.text)}”</td><td class="mono small" style="white-space:nowrap">${esc(a.note)} <span class="faint">#${esc(a.note_rank)}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td class="small">${esc(a.quick)}</td><td class="small">${esc(a.memory)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, fams = story()?.families || [];
    return `<h1>Review</h1>
      <p class="lead">Ask any sourcing question and pick the role family: the quick read (the closest channel note's advice) and the memory read (every channel at the same follow-up time) side by side.
      Or list a family's cohort: every hire, what happened to them, and each channel at 90 / 120 / 180 days.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Which channel for a senior backend engineer?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-fam" ${ready ? "" : "disabled"} style="max-width:160px">${fams.map((s) => `<option ${s === a.family ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Review</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-family="${esc(e.family)}">${esc(e.q)} · ${esc(e.family)}</button>`).join("")}</div>
        <div class="chips">${fams.map((f) => `<button type="button" class="chip" data-cohort="${esc(f)}">cohort: ${esc(f)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askReview($("#ask-q").value.trim(), $("#ask-fam").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-fam").value = c.dataset.family; askReview(c.dataset.q, c.dataset.family); }));
      st.querySelectorAll(".chips .chip[data-cohort]").forEach((c) => c.addEventListener("click", () => askCohort(c.dataset.cohort)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askReview(q, family) {
    if (!q) return; S.ask.q = q; S.ask.family = family;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the hires and outcomes…</span>`;
    const r = await api("/api/review", { q, family });
    if (!r) { box.innerHTML = ""; return; }
    const b = r.before;
    putResult(box, `<div class="row spread"><h2>“${esc(r.ticket.text)}” · ${esc(r.ticket.family)}</h2>${r.memory ? agreePill(r.agree) : ""}</div>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${esc(r.note || "no channel note in the top 8")}: “${esc(r.note_body)}”<br>→ <b>${esc(r.quick || "?")}</b></p></div>
        <div><h3>Memory read</h3><p class="small">${r.memory ? `<b>${esc(r.memory.said)}</b>` : "no channel note in the top 8"}</p></div></div>
      ${r.table ? cohortTable(r.table, r.quick, r.memory.advice) + whyList(r.why) : ""}
      ${b ? `<div class="card mine" style="margin-top:12px"><div class="row spread"><h3 style="margin:0">${esc(r.note)} before an earlier run rewrote it</h3>${agreePill(b.agree)}</div>
        <p class="small muted">“${esc(b.note_body)}”</p><p class="small">That text's quick read: <b>${esc(b.quick)}</b></p>${whyList(b.why)}</div>` : ""}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.note)}</details>`);
  }
  async function askCohort(family) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/cohort", { family });
    if (!r) { box.innerHTML = ""; return; }
    const tb = r.table;
    putResult(box, `<div class="row spread"><h2><code>fact list</code> · every ${esc(family)} hire, as of ${esc(story()?.today)}</h2><span class="pill">${tb.hires.length} hire(s)</span></div>
      ${cohortTable(tb, "", r.advice)}<p class="small"><b>${esc(r.said)}</b></p>
      <table class="iso"><tr><th>id</th><th>started</th><th>channel</th><th>role</th><th>what happened</th></tr>
      ${tb.hires.map((h) => `<tr><td class="mono small">${esc(h.hire)}</td><td class="mono small">${esc(h.start)}</td><td class="small">${esc(h.channel)}</td><td class="small">${esc(h.role)}</td>
        <td class="small">${h.left !== null && h.left < tb.horizon ? `<span class="pill err">${esc(h.status)}</span>` : h.review ? `<span class="pill ok">${esc(h.status)}</span>` : `<span class="pill warn">${esc(h.status)}</span>`}</td></tr>`).join("")}</table>`);
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
    if (!confirm("Delete the demo's hiring-sources project and every record on it from your account?")) return;
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
      else if (S.caughtUp) banner("Done. Try a sourcing question of your own — “Review” in the sidebar.", true);
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

/* Interview feedback memory for hiring teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects · four interviewers", view: "setup" },
    { n: 3, title: "Intake", sub: "40 scorecards · 7 reviews", view: "intake" },
    { n: 4, title: "The quick read", sub: "average the scores", view: "quick" },
    { n: 5, title: "Calibrate", sub: "each score against its interviewer", view: "calibrated" },
    { n: 6, title: "A review arrives today", sub: "the calibration, rewritten", view: "today" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, calibrated: null, today: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const who = (k) => story()?.interviewers?.find((p) => p.key === k) || { name: k };
  const firstName = (k) => who(k).name.split(" ")[0];
  const cand = (id) => story()?.candidates?.find((c) => c.id === id) || { name: id };
  const sc = (x) => String(+Number(x).toFixed(2));
  const sign = (x) => (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(2);
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const outPill = (o) => !o ? "" : `<span class="pill ${(story()?.good || []).includes(o) ? "ok" : "err"}">${esc(o)}</span>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the panel's memory<small>search · an interviewer's history</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, calibrated, today, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Scorecards</dt><dd>${st.ready ? "from an earlier run — Run again reads them back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1½ minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The average says Kai. Each score read against the person who gave it says Lena.</h1>
      <p class="lead">Harborline's panel has scored twelve Senior Backend candidates in the last year; eight were hired, seven have had their six-month review. Every scorecard and every review is one fact in MemoryLake.
      Today's debrief picks between two finalists. Kai averages 3.75, Lena 3.25 — but Kai's scores come from the panel's most generous interviewer, Lena's from its hardest.
      Read each score against that interviewer's own history, and check what their high scores turned into, and the order flips. The calibration is kept as memory on each interviewer, and rewritten — with history — when a review arrives.
      This page drives the <code>memorylake</code> CLI for real. The company, candidates and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes both projects, the four interviewers and every fact.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/interview-feedback-memory-for-hiring-teams" target="_blank" rel="noopener">memorylake.ai › Interview feedback memory for hiring teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("Two projects and four interviewers");
    const st = story();
    return `<h1>Two projects and four interviewers</h1>
      <p class="lead">Scorecards in one project, six-month reviews in another. Each interviewer is an actor: what the team learns about how they score is memory about that person, kept on them.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">Projects</h2>
        <p class="small"><b>${esc(st.scorecards_project.name)}</b><br><span class="mono faint">${esc(su.ids.scorecards)}</span></p>
        <p class="small" style="margin-bottom:0"><b>${esc(st.outcomes_project.name)}</b><br><span class="mono faint">${esc(su.ids.outcomes)}</span></p></div>
      <div class="card"><h2 style="margin-top:0">Interviewers (actors)</h2>
        ${su.interviewers.map((p) => `<p class="small" style="margin:6px 0"><b>${esc(p.name)}</b> · ${esc(p.title)}<br><span class="mono faint">${esc(su.ids.actors[p.key])}</span></p>`).join("")}</div></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const ppl = x.interviewers;
    const rows = x.candidates.map((c) => {
      const cells = ppl.map((p) => { const s = x.scorecards.find((q) => q.candidate === c.id && q.interviewer === p.key); return `<td style="text-align:center">${s ? sc(s.score) : '<span class="faint">·</span>'}</td>`; }).join("");
      const o = x.outcomes.find((q) => q.candidate === c.id);
      const tag = o ? outPill(o.outcome) : c.finalist ? '<span class="pill accent">finalist</span>' : c.hired ? '<span class="pill busy">review due today</span>' : '<span class="faint small">not hired</span>';
      return `<tr${c.finalist ? ' class="mine"' : ""}><td class="mono small">${esc(c.id)}</td><td>${esc(c.name)}</td><td class="small muted">${esc(c.onsite)}</td>${cells}<td>${tag}</td></tr>`;
    }).join("");
    const sample = x.scorecards.find((q) => q.candidate === "F2" && q.interviewer === "dana") || x.scorecards[0];
    return `<h1>Intake — every scorecard and every review, one fact each</h1>
      <p class="lead">One <code>fact add --project</code> for the ${x.scorecards.length} scorecards and one for the ${x.outcomes.length} reviews, word for word; then <code>fact update --metadata</code> on each — candidate, interviewer, score, date; candidate, outcome, review date.</p>
      <div class="card"><table class="iso"><tr><th></th><th>candidate</th><th>onsite</th>${ppl.map((p) => `<th style="text-align:center">${esc(p.name.split(" ")[0])}</th>`).join("")}<th>six-month review</th></tr>${rows}</table>
        <div class="memfact" style="margin-top:12px"><span class="mk">·</span><span class="body">${esc(sample.text)}<div class="trace">${esc(sample.id)} · ${esc(JSON.stringify({ candidate: sample.candidate, interviewer: sample.interviewer, score: sample.score, date: sample.date }))}</div></span></div></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — average each finalist's scores</h1>
      <p class="lead">What a summary tool does: <code>fact list --query "&lt;name&gt;"</code> pulls every scorecard that names the finalist, and the scores are averaged. Nothing is missed and the arithmetic is right.</p>
      <div class="cols">${x.rows.map((r) => `<div class="card ${r.id === x.winner ? "mine" : ""}"><div class="row spread"><h2 style="margin:0">${esc(r.name)}</h2><span class="pill ${r.id === x.winner ? "err" : ""}">average ${r.avg.toFixed(2)}${r.id === x.winner ? " → picked" : ""}</span></div>
        ${r.cards.map((c) => `<div class="memfact"><span class="mk">${sc(c.score)}</span><span class="body">${esc(c.text)}</span></div>`).join("")}</div>`).join("")}</div>
      <div class="card"><p style="margin:0">→ <b>“${esc(cand(x.winner).name.split(" ")[0])} is the stronger candidate.”</b> <span class="muted">The one thing this read assumes: that a 3.5 means the same thing whoever wrote it.</span></p></div>`;
  }

  function calTable(cal, prev) {
    return `<table class="iso"><tr><th>interviewer</th><th>scorecards</th><th>average</th><th>vs panel</th><th>hires they scored above their own average → six-month review</th></tr>
      ${story().interviewers.map((p) => { const r = cal.rows[p.key]; const q = prev?.[p.key];
        const moved = q && (q.good !== r.good || q.total !== r.total);
        return `<tr><td>${esc(p.name)}</td><td>${r.n}</td><td><b>${r.mean.toFixed(2)}</b></td><td class="${r.vs_panel >= 0 ? "" : ""}">${sign(r.vs_panel)}</td>
          <td><span class="pill ${r.good === r.total ? "ok" : "err"}">${moved ? `${q.good}/${q.total} → ` : ""}${r.good}/${r.total} met or exceeded</span>
          <div class="small faint" style="margin-top:4px">${r.detail.map(esc).join(" · ")}</div></td></tr>`; }).join("")}
      <tr><td class="muted">panel</td><td></td><td class="muted">${cal.panel.toFixed(2)}</td><td></td><td></td></tr></table>`;
  }
  function finTable(rows, best) {
    return `<table class="iso"><tr><th>finalist</th><th>average</th><th>each score against that interviewer's own average</th><th>vs own bar</th><th>backers' record</th></tr>
      ${rows.map((f) => `<tr${f.id === best ? ' class="mine"' : ""}><td><b>${esc(f.name)}</b></td><td>${f.raw.toFixed(2)}</td>
        <td class="small">${f.parts.map((p) => `${esc(firstName(p.interviewer))} ${sc(p.score)} <span class="${p.delta >= 0.5 ? "" : "muted"}">(${sign(p.delta)} vs ${p.mean.toFixed(2)})</span>`).join("<br>")}</td>
        <td><b>${sign(f.calibrated)}</b></td><td><span class="pill ${f.good === f.total ? "ok" : "err"}">${f.good}/${f.total}</span></td></tr>`).join("")}</table>`;
  }
  function calibrated() {
    const x = S.calibrated; if (!x) return empty("Calibrate");
    const raw = x.finalists.find((f) => f.id === x.raw_winner), best = x.finalists.find((f) => f.id === x.winner);
    return `<h1>Calibrate — each score against its interviewer's history</h1>
      <p class="lead">One <code>fact list --actors &lt;4 interviewers&gt; --projects &lt;scorecards&gt;,&lt;reviews&gt;</code> returns both scopes, every page. Then plain code over the metadata: each interviewer's own average, and what the six-month reviews said about the hires they scored above it.</p>
      <div class="card">${calTable(x.calibration)}</div>
      <div class="card"><h2 style="margin-top:0">The same four finalist scorecards, each read against the person who wrote it</h2>${finTable(x.finalists, x.winner)}
        <p style="margin-bottom:0">→ By average: <b>${esc(raw.name)}</b> (${raw.raw.toFixed(2)}). Against each interviewer's own bar: <b>${esc(best.name)}</b> (${sign(best.calibrated)}).
        <span class="muted small">${x.calibration.reviews} reviewed hires in all: a signal for the debrief, not a verdict.</span></p></div>
      <div class="card"><h2 style="margin-top:0">Written back as memory on each interviewer</h2>
        <p class="small muted"><code>fact add --actor</code> + <code>fact update --metadata</code> — the next debrief reads it instead of recomputing.</p>
        ${story().interviewers.map((p) => x.calibrations[p.key] ? `<div class="memfact"><span class="mk">${esc(firstName(p.key)[0])}</span><span class="body">${esc(x.calibrations[p.key])}</span></div>` : "").join("")}</div>`;
  }

  function traceHtml(trace) {
    return trace.map((t) => `<div class="memfact"><span class="mk">${t.event === "ADD" ? "+" : "✎"}</span><span class="body"><b>${esc(t.event)}</b> <span class="muted small">${esc(String(t.timestamp || "").slice(0, 19))}Z · ${esc(t.source)}</span>
      ${t.old ? `<div class="small faint">was: ${esc(t.old)}</div>` : ""}<div class="small">now: ${esc(t.new)}</div></span></div>`).join("");
  }
  function today() {
    const x = S.today; if (!x) return empty("A review arrives today");
    const o = story().outcomes.find((q) => q.reviewed >= x.day);
    const c = cand(o?.candidate);
    return `<h1>A six-month review arrives today — the calibration is rewritten, with history</h1>
      <p class="lead">${esc(c.name)} (${esc(o?.candidate)}) — hired after the ${esc(c.onsite)} onsite, scored high by Eli and Gus — was <b>${esc(o?.outcome)}</b>. The review is pinned; the calibration facts that changed are rewritten in place (<code>fact update --text</code>, same fact id, old values in <code>metadata.previous</code>).</p>
      <div class="card">${calTable(x.calibration, S.calibrated?.calibration?.rows)}
        <p class="small" style="margin-bottom:0">${x.changed.map((q) => `<span class="pill err">✎ ${esc(who(q.interviewer).name)} ${esc(q.before)} → ${esc(q.after)}</span>`).join(" ")}
        ${x.same.map((k) => `<span class="pill">= ${esc(who(k).name)}</span>`).join(" ")}${x.changed.length ? "" : ' <span class="muted">already rewritten by an earlier run</span>'}</p></div>
      <div class="card">${finTable(x.finalists, S.calibrated?.winner)}</div>
      <div class="card"><h2 style="margin-top:0"><code>fact trace</code> — Eli Brandt's calibration, every version</h2>${traceHtml(x.trace)}
        <p class="small muted" style="margin-bottom:0">One fact with history, not a number recomputed and thrown away: the next debrief can see what the team believed about Eli's scores before today, and which review changed it.</p></div>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the panel's memory</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 over both projects and the four interviewers, as it comes back. Or open one interviewer: their scorecards, their calibration, and its <code>fact trace</code>.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Who scored Kai Brennan, and what did they say?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Who scored Kai Brennan, and what did they say?", "Which hires were let go?", "How does Dana score?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="chips">${(story()?.interviewers || []).map((p) => `<button type="button" class="chip" data-who="${esc(p.key)}">interviewer: ${esc(p.name)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    st.querySelectorAll("[data-who]").forEach((b) => b.addEventListener("click", () => askWho(b.dataset.who)));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length} as returned</h2><table class="iso"><tr><th>rank</th><th>kind</th><th>hit</th></tr>
      ${r.hits.map((h) => `<tr><td class="mono small">#${h.rank}</td><td><span class="pill">${esc(h.kind || "?")}</span></td><td class="small">${esc(h.text)}</td></tr>`).join("")}</table>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askWho(key) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/interviewer", { interviewer: key });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<h2>${esc(r.name)} — ${r.cards.length} scorecards</h2><table class="iso"><tr><th>date</th><th>candidate</th><th>score</th><th>review</th></tr>
      ${r.cards.map((c) => `<tr><td class="small muted">${esc(c.date)}</td><td>${esc(c.candidate)} ${esc(c.name)}</td><td><b>${sc(c.score)}</b></td><td>${outPill(c.outcome)}</td></tr>`).join("")}</table>
      ${r.calibration ? `<h2>Calibration on the actor</h2><div class="memfact"><span class="mk">·</span><span class="body">${esc(r.calibration.text)}<div class="trace">${esc(r.calibration.id)}</div></span></div>
        <h2><code>fact trace</code></h2>${traceHtml(r.trace)}` : '<p class="muted">No calibration yet.</p>'}`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.calibrated = null; S.today = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's two projects, the four interviewers and every fact from your account?")) return;
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
    else if (k === "quick_read") S.quick = d;
    else if (k === "calibrated") S.calibrated = d;
    else if (k === "today") S.today = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — both projects, the four interviewers and every fact are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question of your own, or open an interviewer — “Ask the panel's memory” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick_read", "calibrated", "today",
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
    show(state.status === "running" ? S.view : S.today ? "today" : "connect");
    subscribe();
  })();
})();

/* Candidate pipeline memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  const initials = (n) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Team & candidates", sub: "one actor per person", view: "setup" },
    { n: 3, title: "Interviews", sub: "three stages, three interviewers", view: "stages" },
    { n: 4, title: "Debrief", sub: "every scorecard, one view", view: "debrief" },
    { n: 5, title: "Recruiter rotation", sub: "nothing to hand over", view: "handover" },
    { n: 6, title: "Withdrawal", sub: "unbind seals the memory", view: "withdraw" },
    { n: 7, title: "Re-application", sub: "bind brings it back", view: "reapply" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, people: {}, cands: {}, project: null,
    sessions: {}, cooked: {}, scorecards: {}, debrief: {}, pipelines: [], withdraw: null, reapply: null,
    ask: { cand: "priya", q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const candOf = (key) => (S.data?.candidates || []).find((c) => c.key === key);
  const person = (key) => S.data?.team.people[key] || { display: key, title: "" };
  const skey = (cand, stage) => `${cand}:${stage}`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask about a candidate<small>scoped search</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() { const r = { connect, setup, stages, debrief, handover, withdraw, reapply, ask }[S.view]; $("#stage").innerHTML = r ? r() : ""; bindStage(); }
  const empty = (title) => `<h1>${title}</h1><div class="empty">Run the demo first — this view fills in when the step runs.</div>`;
  const label = (a) => a.kind === "scorecard" ? `${a.stage} · ${a.who}` : `${a.stage} · said by candidate${a.revised ? " · revises an earlier answer" : ""}`;
  const answer = (a) => `<div class="memfact ${a.kind === "scorecard" ? "pinned" : ""}"><span><span class="src ${a.kind}">${esc(label(a))}</span> ${esc(a.text)}</span><span class="when">${esc(a.date || "")}</span></div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Pipeline project</dt><dd>${S.project ? `${esc(S.project.name)} <span class="mono muted">${esc(S.project.id)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.project ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>mlu-cpm-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Three interviewers, three private notes. One candidate memory.</h1>
      <p class="lead"><b>Halden Robotics</b> is hiring a Senior Controls Engineer. Two candidates go through a recruiter screen, a technical interview and a values
      interview. Every interview is stored as a conversation with the candidate and every scorecard is pinned to the candidate, so the debrief hears everyone,
      the next recruiter inherits the pipeline, and a candidate who withdraws can be sealed out of the workspace — and brought back intact. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the candidates and everything on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/candidate-pipeline-memory-for-recruiting-teams" target="_blank" rel="noopener">memorylake.ai › candidate pipeline memory for recruiting teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const team = S.data.team;
    return `<h1>The recruiting team and the candidates are all actors</h1>
      <p class="lead">An <b>actor</b> is who a memory belongs to. Each candidate is a <code>HUMAN</code> actor whose <code>custom_id</code> is the ATS candidate id, tagged
      <code>candidate</code>; binding it to the workspace is what lets this workspace read and write that memory. The interviewers are actors too, so every message is attributed.</p>
      <h2>Candidates</h2>
      <div class="grid">${S.data.candidates.map((c) => `<div class="card person"><span class="avatar user">${initials(c.display)}</span>
        <div><div class="name">${esc(c.display)}</div><div class="role">HUMAN · custom_id <code>${esc(S.data.prefix)}-${esc(c.candidate_id)}</code></div></div>
        <span class="status pill ${S.cands[c.key] ? "ok" : ""}">${S.cands[c.key] ? "bound" : "pending"}</span></div>`).join("")}</div>
      <h2>${esc(team.company)} recruiting team</h2>
      <div class="grid">${Object.entries(team.people).map(([k, p]) => `<div class="card person"><span class="avatar bot">${initials(p.display)}</span>
        <div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.title)}</div></div>
        <span class="status pill ${S.people[k] ? "ok" : ""}">${S.people[k] ? "bound" : "pending"}</span></div>`).join("")}</div>
      <h2>Pipeline project</h2>
      <div class="card row spread"><div><b>${esc(S.data.project.name)}</b><div class="muted small">custom id <code>${esc(S.data.project.custom_id)}</code>${S.project ? ` · <span class="mono">${esc(S.project.id)}</span>` : ""}</div></div>
        <span class="pill ${S.project ? "ok" : ""}">${S.project ? "ready" : "pending"}</span></div>`;
  }

  function stageCard(c, s) {
    const k = skey(c.key, s.stage); const st = S.sessions[k]; const cooked = st && S.cooked[st.id]; const card = S.scorecards[k];
    // A resumed run appends nothing to an existing stage, so there is no cook to wait for: it is already memory.
    const status = !st ? `<span class="pill">queued</span>` : cooked || card || !st.added ? `<span class="pill ok">memory ready</span>`
      : `<span class="pill busy"><span class="spinner"></span> extracting…</span>`;
    const iv = person(s.interviewer);
    return `<div class="card session"><div class="session-head"><h3>${esc(s.title)}</h3><span class="chan">${esc(iv.display)}</span><span class="muted small">${fmtDate(s.date)}</span><span style="margin-left:auto">${status}</span></div>
      <div class="bubbles">${s.turns.map(([who, text]) => `<div class="bubble ${who === c.key ? "user" : "bot"} ${st ? "stored" : ""}">${esc(text.replace(/^[^:]+:\s*/, ""))}</div>`).join("")}</div>
      <div class="scorecard ${card ? "on" : ""}"><b>Scorecard · ${esc(iv.display)}</b> ${esc(s.scorecard)}<span class="when">${card ? "pinned to the candidate" : "pinned after the interview"}</span></div></div>`;
  }
  function stages() {
    if (!S.data) return "";
    return `<h1>Every interview becomes memory on the candidate</h1>
      <p class="lead">One <code>DIRECT</code> conversation per stage between the candidate and that stage's interviewer. Stages of one candidate are processed in order
      (a later interview can revise an earlier answer); each interviewer's private scorecard is pinned with <code>fact add --actor &lt;candidate&gt;</code>, stamped with stage, author and date.</p>
      <div class="cols">${S.data.candidates.map((c) => `<div><div class="user-head"><span class="avatar user">${initials(c.display)}</span><h2>${esc(c.display)}</h2><span class="muted small">${esc(c.candidate_id)}</span></div>
        ${c.stages.map((s) => stageCard(c, s)).join("")}</div>`).join("")}</div>`;
  }

  function debrief() {
    if (!S.data) return "";
    if (!Object.keys(S.debrief).length) return empty("Debrief");
    return `<h1>Debrief — every scorecard and the candidate's own words, in one view</h1>
      <p class="lead">Without shared memory the decision goes to whoever was loudest in the room. Here every answer carries its source: a pinned scorecard
      (labelled <b>stage · interviewer</b>) or what the candidate said, dated by the conversation it came from. Search is scoped with <code>--actors &lt;candidate&gt;</code>.</p>
      <div class="cols">${S.data.candidates.map((c) => { const d = S.debrief[c.key]; if (!d) return "<div></div>"; return `<div>
        <div class="user-head"><span class="avatar user">${initials(c.display)}</span><h2>${esc(c.display)}</h2><span class="pill accent">${d.total} facts</span><span class="muted small">${d.pinned} scorecards</span></div>
        ${d.rows.map((r) => `<div class="card qa"><div class="q">${esc(r.question)} <span class="faint small">${r.stages.length ? r.stages.join(" + ") : ""}</span></div>
          ${r.answers.map(answer).join("") || '<div class="muted small">nothing in memory</div>'}
          ${r.left_out ? `<div class="faint small">${r.left_out} other hit(s) left out — they do not mention the topic</div>` : ""}</div>`).join("")}
        <div class="muted small">${d.covered}/${d.rows.length} questions answered · ${d.multi} draw on more than one stage</div></div>`; }).join("")}</div>`;
  }

  function pipelineCard(p) {
    const names = p.active.map((a) => a.display);
    return `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(p.label)}</h3><span class="pill ${p.active.length > 1 ? "accent" : ""}">${p.active.length} candidate(s) bound</span></div>
      <div class="faint small mono" style="margin:6px 0 10px">search "${esc(p.query)}" --actors ${p.active.map((a) => esc(a.id.slice(0, 12)) + "…").join(",")}</div>
      ${names.map((n) => `<div class="pipe"><b>${esc(n)}</b> <span class="muted small">${(p.hits[n] || []).length} hit(s)</span>
        ${(p.hits[n] || []).slice(0, 4).map(answer).join("")}</div>`).join("")}</div>`;
  }
  function handover() {
    const h = S.data?.team.handover; const p = S.pipelines[0];
    if (!p) return empty("Recruiter rotation");
    return `<h1>Recruiter rotation — the memory transfers because it never lived in the recruiter's notes</h1>
      <p class="lead">${fmtDate(h.date)}: <b>${esc(person(h.from).display)}</b> moves to another team and <b>${esc(person(h.to).display)}</b> takes over the requisition.
      There is no hand-over document. ${esc(person(h.to).display.split(" ")[0])} lists the candidates bound to the workspace (<code>actor list --tags candidate</code>, ACTIVE bindings only) and asks the whole pipeline one question.</p>
      ${pipelineCard(p)}`;
  }

  function withdraw() {
    const w = S.withdraw;
    if (!w) return empty("Withdrawal");
    const after = S.pipelines[1];
    return `<h1>${esc(w.display.split(" ")[0])} withdraws — <code>actor unbind</code> seals his memory in this workspace</h1>
      <p class="lead">${esc(w.reason)} Unbinding the actor is a pause, not an erasure: the workspace can no longer read, search or write his memory, but nothing is deleted.</p>
      <div class="cols"><div class="card"><h3 style="margin-top:0">After <code>actor unbind</code></h3>
        <div class="memfact"><span>Before: <b>${w.before.length}</b> facts on ${esc(w.display)}</span></div>
        ${w.checks.map((x) => `<div class="refusal ${x.refused ? "" : "bad"}"><span class="pill ${x.refused ? "err" : "ok"}">${x.refused ? `refused · HTTP ${esc(x.http || "?")}` : "still allowed"}</span> <b>${esc(x.what)}</b><div class="mono small">${esc(x.reason)}</div></div>`).join("")}
        <div class="memfact"><span>The actor is kept: <span class="mono">${esc(w.kept)}</span></span><span class="when">actor get ✓</span></div>
        <div class="memfact"><span>The raw transcripts are not sealed: ${w.transcripts} messages still listed — for an erasure request, delete the conversations and the actor (Clean up does both).</span></div></div>
        <div>${after ? pipelineCard(after) : ""}</div></div>`;
  }

  function reapply() {
    const r = S.reapply;
    if (!r) return empty("Re-application");
    return `<h1>${esc(r.display.split(" ")[0])} applies again — <code>actor bind</code> brings the same memory back</h1>
      <p class="lead">Two months later he applies for <b>${esc(r.opening)}</b>. Binding the same actor back restores exactly what was there, and the new conversation adds to it —
      the extractor even revises September's answer in place.</p>
      <div class="cols"><div class="card"><h3 style="margin-top:0">Facts on ${esc(r.display)}</h3>
        <div class="counts"><div><b>${r.before}</b><span>before withdrawal</span></div><div class="bad"><b>refused</b><span>while unbound</span></div><div><b>${r.after}</b><span>after re-binding</span></div></div>
        <div style="margin-top:10px">${r.identical ? '<span class="pill ok">identical fact ids — nothing lost</span>' : '<span class="pill err">fact ids differ</span>'}</div></div>
        <div class="card"><h3 style="margin-top:0">What compensation is he expecting? <span class="faint small">${r.stages.join(" + ")}</span></h3>${r.answers.map(answer).join("") || '<div class="muted small">nothing in memory</div>'}</div></div>`;
  }

  function ask() {
    const cands = S.data?.candidates || [];
    const ready = !!S.cands[S.ask.cand];
    return `<h1>Ask about a candidate</h1>
      <p class="lead">The same scoped search the debrief runs: <code>search "…" --actors &lt;candidate&gt; --types fact</code>. Every hit is labelled with the stage it came from.</p>
      <div class="card"><div class="userpick">${cands.map((c) => `<button type="button" class="chip ${S.ask.cand === c.key ? "on" : ""}" data-cand="${c.key}">${esc(c.display)}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. what did the hiring manager worry about" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${["what did the hiring manager worry about", "when can they start", "salary expectations", "safety certification experience", "do they mentor others"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelectorAll(".userpick .chip").forEach((c) => c.addEventListener("click", () => { S.ask.cand = c.dataset.cand; S.ask.html = ""; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { candidate: S.ask.cand, query: q, top_k: 8 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = r.refused ? `<div class="refusal"><span class="pill err">refused</span> <span class="mono small">${esc(r.refused)}</span></div>`
      : r.facts.length ? r.facts.map(answer).join("") : '<p class="muted">No facts matched.</p>';
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
  function resetView() { S.sessions = {}; S.cooked = {}; S.scorecards = {}; S.debrief = {}; S.pipelines = []; S.withdraw = null; S.reapply = null; }
  async function runDemo(reset) {
    banner(""); if (reset || !S.project) { resetView(); S.stepStatus = { 1: "done" }; }
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the interview conversations, the pipeline project, both candidates (and every fact on them) and the team actors from your account?")) return;
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
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView(); term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term(/refused/.test(ev.text) ? "error" : "note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "person") S.people[d.key] = d;
    else if (k === "candidate") S.cands[d.key] = d.id;
    else if (k === "project") S.project = { id: d.id, name: d.name };
    else if (k === "session") S.sessions[skey(d.candidate, d.stage)] = d;
    else if (k === "cooked") S.cooked[d.id] = true;
    else if (k === "scorecard") S.scorecards[skey(d.candidate, d.stage)] = d;
    else if (k === "debrief") S.debrief[d.candidate] = d;
    else if (k === "pipeline") S.pipelines.push(d);
    else if (k === "withdraw") S.withdraw = d;
    else if (k === "reapply") S.reapply = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.project = null; S.people = {}; S.cands = {}; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the candidates and everything on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask about a candidate” — every hit says which stage it came from.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "person", "candidate", "project", "session", "cooked", "scorecard",
    "debrief", "pipeline", "withdraw", "reapply", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  (async () => {
    renderSteps();
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data; if (state.project) S.project = state.project;
    Object.assign(S.cands, state.candidates || {});
    const target = state.last_event_id; setConn();
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const h = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.reapply ? "reapply" : "connect");
    subscribe();
  })();
})();

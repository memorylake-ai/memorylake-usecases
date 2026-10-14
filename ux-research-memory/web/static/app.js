/* UX research memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "same research, two projects", view: "setup" },
    { n: 3, title: "Three studies", sub: "participants, then findings", view: "record" },
    { n: 4, title: "Flagged", sub: "the conflict check", view: "flagged" },
    { n: 5, title: "Latest study wins", sub: "the team's usual way", view: "latest" },
    { n: 6, title: "Check who said it", sub: "participants behind each side", view: "check" },
    { n: 7, title: "Resolve", sub: "keep the reversal, scope the split", view: "apply" },
    { n: 8, title: "The Q4 question", sub: "both projects, same words", view: "question" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, record: null, flagged: null, latest: null, check: null, apply: null, question: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const projName = (a) => story()?.projects?.[a]?.name || a;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const segLabel = (s) => story()?.segments?.[s]?.label || s;
  const segs = (side) => Object.entries(side.segments || {}).map(([s, n]) => `${n} ${segLabel(s)}`).join(" + ");
  const VERD = { split: ["accent", "segment split — both stand, each for its own people"], reversal: ["ok", "reversal — the newer finding replaces the older"], unknown: ["warn", "cannot tell"] };
  const verdPill = (c) => `<span class="pill ${(VERD[c] || VERD.unknown)[0]}">${esc((VERD[c] || VERD.unknown)[1])}</span>`;
  function sidesTable(d) {
    return `<table class="iso"><tr><th>study</th><th>finding</th><th>participants</th></tr>
      ${d.sides.map((s) => `<tr class="${s.gone ? "hl" : ""}"><td class="small" style="white-space:nowrap"><b>${esc(s.round)}</b> <span class="muted">${esc(s.on)}</span><br><span class="faint">${esc(s.study)}</span></td>
        <td class="small">${esc(s.body)}${s.gone ? ` <span class="pill err">${esc(s.gone)}</span>` : ""}</td>
        <td class="small">${s.participants?.length ? `<span class="mono">${esc(s.participants[0])}–${esc(s.participants[s.participants.length - 1])}</span><br>${esc(segs(s))}` : ""}</td></tr>`).join("")}</table>`;
  }
  function hitsTable(r) {
    return `<table class="iso"><tr><th>rank</th><th>fact</th></tr>
      ${r.hits.map((h) => { const ev = r.evidence.some((e) => e.ref === h.ref); return `<tr class="${ev ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td>
        <td class="small">${esc(h.body)} <span class="pill">${esc(h.kind)} · ${esc(h.ref)}</span></td></tr>`; }).join("")}</table>
      ${r.cached?.length ? `<p class="small faint">${r.cached.length} hit(s) carry fact ids not on the project now — search answered from its cache</p>` : ""}`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any question, both projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, record, flagged, latest, check, apply, question, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Projects</dt><dd>${st.ready ? "from an earlier run — a run cleans them up and replays the research from the first study" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about three minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The conflict check flagged it. “Latest study wins” deleted the wrong finding.</h1>
      <p class="lead">Ledgerline ran three interview studies this year: two with self-serve signups who churned, one with enterprise trial admins. The synthesis wrote every finding as “New users …” — the sample fell off.
      MemoryLake's conflict check flags two pairs that cannot both be true as written. The team's habit is to keep the most recent study. That is right for one pair (the pricing page was redesigned in between)
      and wrong for the other: March's “CSV import is the main blocker” and September's “import is fine” came from different people. The same research runs in two projects — one resolves the usual way,
      one checks who was in the room first — and then both answer the Q4 roadmap question.
      This page drives the <code>memorylake</code> CLI for real. The company, researchers and participants are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes both projects and every fact on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-ux-researchers" target="_blank" rel="noopener">memorylake.ai › AI memory for UX researchers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("The same research, two projects");
    return `<h1>The same research, two projects</h1>
      <p class="lead">Both get the identical facts in the identical order. They differ only in how a flagged conflict gets resolved — so whatever differs at the end comes from that choice.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        ${Object.entries(su.projects).map(([a, id]) => `<tr><td><span class="pill ${a === "latest" ? "" : "accent"}">project</span></td><td><b>${esc(su.names[a])}</b></td><td class="mono small faint">${esc(id)}</td></tr>`).join("")}</table></div>`;
  }

  function record() {
    const x = S.record; if (!x) return empty("Three studies");
    return `<h1>Three studies — ${x.participants.length} participants, ${x.findings.length} findings</h1>
      <p class="lead">The participant roster and context notes go in first (one <code>fact add</code>), then each study's findings in the order they were written, 15 s apart.
      Every fact carries metadata — a finding names its study and participant ids, a participant names their segment. The finding <i>text</i> lost the sample: they all say “New users …”.</p>
      ${x.rounds.map((r) => { const ps = x.participants.filter((p) => p.round === r.id);
        return `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(r.id)} · ${esc(r.study)}</h2><span class="small muted">${esc(r.date)} · ${esc(r.lead)}</span></div>
        <p class="small">${ps.length} participants: <span class="pill ${ps[0]?.segment === "enterprise trial admin" ? "accent" : ""}">${esc(segLabel(ps[0]?.segment))}</span> <span class="mono faint">${esc(ps[0]?.pid)}–${esc(ps[ps.length - 1]?.pid)}</span></p>
        <table class="iso">${x.findings.filter((f) => f.round === r.id).map((f) => `<tr><td class="mono small" style="white-space:nowrap">${esc(f.key)}</td><td class="small">${esc(f.text)}</td></tr>`).join("")}</table></div>`; }).join("")}
      <div class="card"><h2 style="margin-top:0">Context</h2><table class="iso">${x.context.map((c) => `<tr><td class="small">${esc(c.text)}</td></tr>`).join("")}</table></div>`;
  }

  function flagged() {
    const x = S.flagged; if (!x) return empty("Flagged");
    const ds = x.conflicts.latest || [];
    return `<h1>Flagged — ${ds.length} conflict(s) in each project</h1>
      <p class="lead">MemoryLake checks each new fact against what the project already holds (<code>fact conflict list</code>). As written, these pairs cannot both be true — the check is right.
      What to do about each one is the team's call.</p>
      ${ds.map((d) => `<div class="card"><div class="row spread"><h2 style="margin:0">“${esc(d.name)}”</h2><span class="pill">${esc(d.category)}</span></div>${sidesTable(d)}</div>`).join("")}
      <p class="small muted">${Object.entries(x.conflicts).map(([a, cs]) => `${esc(projName(a))}: ${cs.length}`).join(" · ")}</p>`;
  }

  function latest() {
    const x = S.latest; if (!x) return empty("Latest study wins");
    return `<h1>Latest study wins — ${x.forgotten.length} findings forgotten</h1>
      <p class="lead">The team's habit: keep the finding from the most recent study (<code>fact conflict resolve --strategy keep_fact</code>). The other fact is forgotten — gone from <code>fact list</code> and from search.</p>
      <div class="card mine"><table class="iso"><tr><th>conflict</th><th>kept</th><th>forgot</th></tr>
        ${x.resolved.map((r) => `<tr><td class="small">“${esc(r.name)}”</td><td class="mono small">${esc(r.kept)}</td><td class="small"><span class="mono">${esc(r.forgot)}</span><br><span class="muted">${esc(r.forgot_body)}</span></td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Findings left: ${x.left.length} of ${x.left.length + x.forgotten.length}</h2><p class="small">${x.left.map((r) => `<span class="pill">${esc(r)}</span>`).join(" ")}</p></div>`;
  }

  function check() {
    const x = S.check; if (!x) return empty("Check who said it");
    return `<h1>Check who said it — ${x.rows.filter((r) => r.verdict.cls === "split").length} split, ${x.rows.filter((r) => r.verdict.cls === "reversal").length} reversal</h1>
      <p class="lead">Plain code, no model: each finding's metadata names its participants; each participant's roster fact names their segment. No participant group in common → the findings describe different people.
      Same group, later study → newer evidence about the same people.</p>
      ${x.rows.map((r) => `<div class="card ${r.verdict.cls === "split" ? "mine" : ""}"><div class="row spread"><h2 style="margin:0">“${esc(r.name)}”</h2>${verdPill(r.verdict.cls)}</div>${sidesTable(r)}
        <p class="small" style="margin-bottom:0">${esc(r.verdict.why)}</p></div>`).join("")}`;
  }

  function apply() {
    const x = S.apply; if (!x) return empty("Resolve");
    return `<h1>Resolve each one its own way — ${x.new_conflicts.length} new conflict(s)</h1>
      <p class="lead">The reversal: <code>keep_fact</code>, same as the other project. The split: <code>dismiss</code> (changes no fact), then both findings rewritten with their sample — delete the unscoped text, <code>fact add</code> the scoped one.
      The rewrite is a new write, so the conflict check runs on it: ${x.new_conflicts.length ? "it raised something — shown below" : "nothing new in " + esc(x.waited) + " s. The check itself agrees the scoped findings can both be true."}</p>
      ${x.actions.map((a) => `<div class="card ${a.cls === "split" ? "mine" : ""}"><div class="row spread"><h2 style="margin:0">“${esc(a.name)}”</h2>${verdPill(a.cls)}</div>
        ${a.cls === "split" ? `<table class="iso">${a.scoped.map((t) => `<tr><td class="small"><b>${esc(t)}</b></td></tr>`).join("")}</table>` : `<p class="small" style="margin-bottom:0">kept <span class="mono">${esc(a.kept)}</span>, forgot <span class="mono">${esc(a.forgot)}</span></p>`}</div>`).join("")}
      ${x.new_conflicts.map((c) => `<div class="card"><span class="pill warn">new</span> “${esc(c.name)}” ${c.refs.map(esc).join(" vs ")}</div>`).join("")}
      <p class="small muted">findings on the project: ${x.left.length}</p>`;
  }

  function question() {
    const x = S.question; if (!x) return empty("The Q4 question");
    const a = x.arms.latest, b = x.arms.provenance;
    return `<h1>The Q4 question — ${a.share}% vs ${b.share}% of new signups</h1>
      <p class="lead">Q4 planning: fund a redesign of the CSV column-mapping screen? Both projects get “${esc(x.question)}”, top 8 facts. Highlighted: findings about the import step.
      The percentage is who those findings speak for — the participants behind them, by segment, against the signup mix (74% self-serve, 26% enterprise).</p>
      <div class="cols">${["latest", "provenance"].map((arm) => { const r = x.arms[arm]; return `<div class="card ${arm === "provenance" ? "mine" : ""}"><div class="row spread"><h2 style="margin:0">${esc(projName(arm))}</h2>
        <span class="pill ${r.share < 100 ? "err" : "ok"}">${r.share}% of new signups</span></div>
        <p class="small">${r.evidence.length} finding(s) on the import step · speaks for ${esc(r.segments.join(" + ") || "nobody")}</p>${hitsTable(r)}</div>`; }).join("")}</div>
      <div class="card"><p class="small" style="margin:0">“Latest study wins” kept a finding that says <b>New users</b> and speaks for the 26%. The column-mapping screen has not changed since January — nothing made March's finding untrue; a different group of people was asked.</p></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask;
    return `<h1>Ask</h1>
      <p class="lead">Any question, to both projects with the same words — top 8 facts each, side by side. Or list both projects' conflicts and how each one reads.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What blocks new users during setup?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e)}">${esc(e)}</button>`).join("")}
          <button type="button" class="chip" data-conflicts="1">list the conflicts</button></div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askBoth($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askBoth(c.dataset.q); }));
      st.querySelectorAll(".chips .chip[data-conflicts]").forEach((c) => c.addEventListener("click", () => askConflicts()));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askBoth(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> asking both projects…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<h2>“${esc(r.question)}”</h2><div class="cols">${["latest", "provenance"].map((arm) => `<div><h3>${esc(projName(arm))}</h3>${hitsTable(r.arms[arm])}</div>`).join("")}</div>`);
  }
  async function askConflicts() {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/conflicts", {});
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, Object.entries(r).map(([arm, ds]) => `<h3>${esc(projName(arm))} · ${ds.length} conflict(s)</h3>
      ${ds.map((d) => `<div class="row spread"><b class="small">“${esc(d.name)}”</b><span class="pill">${esc(d.category)} · resolved: ${esc(d.resolved || "no")}</span></div>${sidesTable(d)}`).join("")}`).join(""));
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
  function resetView() { S.setup = null; S.record = null; S.flagged = null; S.latest = null; S.check = null; S.apply = null; S.question = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's two research projects and every fact on them from your account?")) return;
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
    else if (k === "record") S.record = d;
    else if (k === "flagged") S.flagged = d;
    else if (k === "latest") S.latest = d;
    else if (k === "check") S.check = d;
    else if (k === "apply") S.apply = d;
    else if (k === "question") S.question = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — both projects and every fact on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask both projects a question of your own — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "record", "flagged", "latest", "check", "apply", "question",
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
    show(state.status === "running" ? S.view : S.question ? "question" : "connect");
    subscribe();
  })();
})();

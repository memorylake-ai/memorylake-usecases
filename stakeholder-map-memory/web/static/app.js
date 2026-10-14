/* Stakeholder mapping memory for consultants — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "seats · 3 engagements · 5 people", view: "setup" },
    { n: 3, title: "Intake", sub: "notes anchored to a seat or a person", view: "intake" },
    { n: 4, title: "Inherit the last map", sub: "day one of engagement 3", view: "inherited" },
    { n: 5, title: "Resolve", sub: "seat → who sits there today", view: "resolved" },
    { n: 6, title: "Write E3's map", sub: "every line with its sources", view: "written" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, inherited: null, resolved: null, written: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const who = (k) => (k ? story()?.people?.find((p) => p.key === k) || { name: k } : { name: "(vacant)" });
  const seat = (k) => story()?.seats?.find((s) => s.key === k)?.title || k;
  const fn = (k) => story()?.functions?.find((f) => f.key === k) || { label: k, seat: "" };
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const stancePill = (s) => s ? `<span class="pill ${s === "skeptic" ? "err" : s === "unknown" ? "" : "ok"}">${esc(s)}</span>` : '<span class="pill busy">unknown</span>';

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the client's memory<small>search · a person · the map on any date</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, inherited, resolved, written, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Client memory</dt><dd>${st.ready ? "from an earlier run — Run again reads it back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>The last stakeholder map, carried over: three of its four lines are now wrong.</h1>
      <p class="lead">Ashgrove Partners is starting its third engagement with Calder Freight. The first two left notes in MemoryLake — who held which seat, what each person is like, who signed what — and engagement 2's stakeholder map.
      Every note is pinned word for word and tagged with what it is anchored to: <b>a seat</b> (“the VP Finance signs any SOW over 150,000 dollars”) or <b>a person</b> (“Dana asks for the downside case first”).
      Calder reorganised in between. Sign-off authority stayed with the seat; habits went with the people. Resolve each line through who sits in the seat today and the map changes — and the same method, asked about February, rebuilds the old map exactly.
      This page drives the <code>memorylake</code> CLI for real. The firms and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the four projects, the five people and every fact.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/stakeholder-mapping-memory-for-consultants" target="_blank" rel="noopener">memorylake.ai › Stakeholder mapping memory for consultants</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("The client's seats, three engagements, five people");
    const st = story();
    return `<h1>The client's seats, three engagements, five people</h1>
      <p class="lead">Seats — and what each seat can do — live on the client's projects. What a person is like lives on that person (an actor), so it can follow them when they change seats.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">Projects</h2>
        ${st.projects.map((p) => `<p class="small" style="margin:6px 0"><b>${esc(p.name)}</b><br><span class="mono faint">${esc(su.ids[p.key])}</span></p>`).join("")}</div>
      <div class="card"><h2 style="margin-top:0">People (actors)</h2>
        ${su.people.map((p) => `<p class="small" style="margin:6px 0"><b>${esc(p.name)}</b><br><span class="mono faint">${esc(su.ids.actors[p.key])}</span></p>`).join("")}</div></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const c = x.counts || {};
    const tl = (x.timeline || []).map((e) => `<tr class="${e.kind === "checkin" ? "mine" : ""}"><td class="mono small">${esc(e.date)}</td><td><span class="pill ${e.kind === "checkin" ? "err" : e.kind === "map" ? "accent" : ""}">${esc(e.kind)}</span></td><td class="small">${esc(e.what)}</td></tr>`).join("");
    const ten = story().tenures.map((t) => `<tr><td>${esc(seat(t.seat))}</td><td>${t.person ? esc(who(t.person).name) + (t.acting ? " <span class=\"muted\">(acting)</span>" : "") : '<span class="faint">vacant</span>'}</td><td class="mono small">${esc(t.from)} → ${esc(t.to || "now")}</td></tr>`).join("");
    return `<h1>Intake — two engagements of notes, each anchored to a seat or a person</h1>
      <p class="lead">One <code>fact add</code> per scope, word for word, then <code>fact update --metadata</code> on each: <code>kind</code>, <code>anchor</code> (seat | person), the seat or person, the engagement and the date. ${c.tenure || 0} seat tenures and ${c.checkin || 0} check-in notes on the seats project, ${c.observation || 0} observations, ${c.map || 0} lines of engagement 2's map.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">Timeline</h2><table class="iso">${tl}</table></div>
        <div class="card"><h2 style="margin-top:0">Seat tenures</h2><table class="iso"><tr><th>seat</th><th>held by</th><th>when</th></tr>${ten}</table></div></div>`;
  }

  function mapTable(rows, extra) {
    return `<table class="iso"><tr><th>function</th><th>who</th><th>seat</th><th>stance</th>${extra ? "<th></th>" : ""}</tr>
      ${rows.map((r, i) => `<tr${extra && !extra[i].same ? ' class="mine"' : ""}><td><b>${esc(fn(r.function).label)}</b></td><td>${esc(who(r.person).name)}${r.acting ? ' <span class="muted">(acting)</span>' : ""}${r.since ? `<div class="small faint">since ${esc(r.since)}</div>` : ""}</td>
        <td class="small">${esc(seat(fn(r.function).seat))}</td><td>${r.person ? stancePill(r.stance) : '<span class="faint small">no one to brief</span>'}</td>${extra ? `<td>${extra[i].same ? '<span class="pill ok">✓ same</span>' : '<span class="pill err">✗ changed</span>'}</td>` : ""}</tr>`).join("")}</table>`;
  }

  function inherited() {
    const x = S.inherited; if (!x) return empty("Inherit the last map");
    return `<h1>Day one of engagement 3 — inherit the last stakeholder map</h1>
      <p class="lead">What a new team does: open engagement 2's map (<code>fact list --projects &lt;E2&gt;</code>) and carry it over. And ask memory the day-one question across everything the firm knows about the client.</p>
      <div class="card"><h2 style="margin-top:0">Inherited from E2 (${esc(story().e2_map.date)}), as saved</h2>${mapTable(x.rows)}</div>
      <div class="card"><h2 style="margin-top:0"><code>search</code> “${esc(x.ask.q)}” — top ${x.ask.hits.length}</h2>
        ${x.ask.hits.map((h) => `<div class="memfact"><span class="mk">${h.rank}</span><span class="body">${esc(h.text)}</span></div>`).join("")}
        <p style="margin-bottom:0">→ The map sends the SOW to <b>${esc(who(x.rows[0]?.person).name)}</b>${x.named ? `, and the first person search names is <b>${esc(who(x.named).name)}</b>` : ""}. <span class="muted">Every fact behind both is true.</span></p></div>`;
  }

  function resolved() {
    const x = S.resolved; if (!x) return empty("Resolve");
    const moved = (x.moved || []).map((g) => `<div class="memfact"><span class="mk">→</span><span class="body"><b>${esc(who(g.person).name)}</b>: ${g.seats.length ? `now ${esc(g.seats.map(seat).join(", "))} since ${esc(g.since[0])} — carries with them: ${esc(g.stance || "no stance")}${g.style.length ? `, ${g.style.length} habit note(s)` : ""}` : "no seat at the client any more"}</span></div>`).join("");
    return `<h1>Resolve — each map line through the seat, then the person in it</h1>
      <p class="lead">One <code>fact list --projects &lt;seats, E1, E2&gt; --actors &lt;5 people&gt;</code> returns both scopes, every page. Plain code joins them: the line's seat → whoever holds it on the day (tenure facts) → that person's own stance and habits (facts on their actor).</p>
      <div class="card"><div class="row spread"><h2 style="margin:0">Asked about ${esc(x.check.day)} — the day E2's map was saved</h2><span class="pill ${x.check.agree === x.check.total ? "ok" : "err"}">${x.check.agree === x.check.total ? "✓" : "✗"} ${x.check.agree}/${x.check.total} lines match E2's saved map</span></div>
        <div style="margin-top:10px">${mapTable(x.check.rows)}</div><p class="small muted" style="margin-bottom:0">The method reproduces the old map; only the date differs.</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Asked about today</h2><span class="pill err">${x.still} of ${x.diff.length} inherited lines still hold</span></div>
        <div style="margin-top:10px">${mapTable(x.rows, x.diff)}</div>${moved}
        <p style="margin-bottom:0">→ Authority stayed with the seats; habits and stances went with the people.</p></div>`;
  }

  function written() {
    const x = S.written; if (!x) return empty("Write E3's map");
    return `<h1>Write E3's map back — every line with the facts it came from</h1>
      <p class="lead">One fact per line on the E3 project, with the ids of the facts it was resolved from in <code>metadata.basis</code>. Read back: <span class="pill ${x.cited === x.cited_total ? "ok" : "err"}">${x.cited === x.cited_total ? "✓" : "✗"} ${x.cited}/${x.cited_total} cited facts found</span></p>
      ${x.rows.map((r) => `<div class="card"><div class="memfact"><span class="mk">E3</span><span class="body"><b>${esc(r.text)}</b><div class="trace">${esc(r.id)}</div></span></div>
        ${r.sources.map((s) => `<div class="memfact"><span class="mk">←</span><span class="body small"><span class="pill">${esc(s.kind)}</span> ${esc(s.text || "(not found)")}<div class="trace">${esc(s.id)}</div></span></div>`).join("")}</div>`).join("")}
      ${x.gaps.length ? `<div class="card"><h2 style="margin-top:0">Gaps to close before the steering committee</h2>${x.gaps.map((g) => `<p class="small" style="margin:6px 0">· <b>${esc(fn(g).label)}</b>: ${g === "sow" ? "Priya Raman — no observations; meet before the SOW, and don't brief her with Dana's habits" : "the seat is vacant — ask who covers site data requests"}</p>`).join("")}</div>` : ""}`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the client's memory</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 over the seats, the first two engagements and the five people, as it comes back. Or open one person, or resolve the map for any date.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Who can veto the automation project?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Who can veto the automation project?", "How should we present numbers to finance?", "Who runs the sites?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="chips">${(story()?.people || []).map((p) => `<button type="button" class="chip" data-who="${esc(p.key)}">person: ${esc(p.name)}</button>`).join("")}</div>
        <div class="chips">${["2025-04-01", "2026-02-20", "2026-07-15", story()?.today].map((d) => `<button type="button" class="chip" data-day="${esc(d)}">map on ${esc(d)}</button>`).join("")}</div>
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
    st.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => askDay(b.dataset.day)));
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
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length} as returned</h2><table class="iso"><tr><th>rank</th><th>hit</th></tr>
      ${r.hits.map((h) => `<tr><td class="mono small">#${h.rank}</td><td class="small">${esc(h.text)}</td></tr>`).join("")}</table>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askWho(key) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/person", { person: key });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<h2>${esc(r.name)} — seats held</h2><table class="iso"><tr><th>seat</th><th>from</th><th>to</th></tr>
      ${r.tenures.map((t) => `<tr><td>${esc(seat(t.seat))}${t.acting ? ' <span class="muted">(acting)</span>' : ""}</td><td class="mono small">${esc(t.start)}</td><td class="mono small">${esc(t.end || "now")}</td></tr>`).join("")}</table>
      <h2>Observations on ${esc(r.name.split(" ")[0])} (${r.observations.length})</h2>
      ${r.observations.length ? r.observations.map((o) => `<div class="memfact"><span class="mk">${esc((o.trait || "·")[0])}</span><span class="body small">${esc(o.text)}</span></div>`).join("") : '<p class="muted">None — nobody at the firm has worked with them yet.</p>'}`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askDay(day) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> resolving…</span>`;
    const r = await api("/api/map", { day });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<h2>The map resolved for ${esc(r.day)}</h2>${mapTable(r.rows)}`;
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
  function resetView() { S.setup = null; S.intake = null; S.inherited = null; S.resolved = null; S.written = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's four projects, the five people and every fact from your account?")) return;
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
    else if (k === "inherited") S.inherited = d;
    else if (k === "resolved") S.resolved = d;
    else if (k === "written") S.written = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the four projects, the five people and every fact are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question of your own, open a person or pick a date — “Ask the client's memory” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "inherited", "resolved", "written",
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
    show(state.status === "running" ? S.view : S.written ? "written" : "connect");
    subscribe();
  })();
})();

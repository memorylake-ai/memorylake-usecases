/* Competitive intel memory for sales teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project for all the intel", view: "setup" },
    { n: 3, title: "Intake", sub: "14 records, one fact each", view: "intake" },
    { n: 4, title: "Three quick reads", sub: "search · vote · newest", view: "quick" },
    { n: 5, title: "Walk the lineage", sub: "every record to its source", view: "lineage" },
    { n: 6, title: "Write it back", sub: "mark copies · new card · who was told", view: "written" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, lineage: null, written: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const cname = (c) => story()?.claims?.[c] || "no answer";
  const kname = (k) => story()?.kinds?.[k] || k;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const claimPill = (c, status) => c === "sso-enterprise" ? `<span class="pill ${status ? "" : "err"}">Enterprise-only</span>`
    : c === "sso-business" ? `<span class="pill ok">Business</span>` : `<span class="pill">not SSO</span>`;
  const verdictPill = (c) => c === "sso-business" ? `<span class="pill ok">→ Business</span>` : c === "sso-enterprise" ? `<span class="pill err">→ Enterprise-only</span>` : `<span class="pill">→ —</span>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the intel<small>search + status · audit by date</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, lineage, written, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Intel</dt><dd>${st.ready ? "from an earlier run — Run again reads it back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">under a minute · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Seven records say Pendrel made SSO Enterprise-only. They all come from one newsletter — which corrected itself in August.</h1>
      <p class="lead">Lumora's sales team logs every piece of competitive intel into one MemoryLake project, word for word, with who logged it, when, the claim it makes, and what it was copied from.
      Ask search, count every record, or read the newest one, and the answer is “Enterprise-only”. Walk each record back to its source and the seven are one analyst item, withdrawn on August 25 — three of the copies were written after the correction was already in memory.
      This page drives the <code>memorylake</code> CLI for real. The companies, publications and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/competitive-intel-memory-for-sales-teams" target="_blank" rel="noopener">memorylake.ai › Competitive intel memory for sales teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for all the intel");
    return `<h1>One project for every piece of competitive intel</h1>
      <p class="lead">Analyst newsletters, Slack digests, battle cards, deal notes, pricing captures and loss debriefs all land in the same project, so one read sees all of it.</p>
      <div class="card"><h2 style="margin-top:0">${esc(su.name)}</h2><p class="small">${esc(su.description)}</p><div class="mono small faint">${esc(su.project)}</div></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    return `<h1>Intake — every record, word for word, with where it came from</h1>
      <p class="lead">One <code>fact add --project</code> for all fourteen, then <code>fact update --metadata</code> on each: kind, who logged it, when, the claim it makes, and <b>derived_from</b> — what the person logging it said they copied it from.</p>
      <div class="card"><table class="iso"><tr><th>id</th><th>logged</th><th>what · by</th><th>claim</th><th>from</th><th>record</th></tr>
        ${x.records.map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td class="small muted" style="white-space:nowrap">${esc(r.logged_on)}</td>
          <td class="small">${esc(kname(r.kind))}${r.account ? ` · ${esc(r.account)}` : ""}<br><span class="faint">${esc(r.logged_by)}</span></td>
          <td>${r.topic === x.topic ? claimPill(r.claim) : '<span class="faint small">—</span>'}</td>
          <td class="mono small">${r.derived_from?.length ? esc(r.derived_from.join(", ")) : r.corrects ? `<span class="pill warn">corrects ${esc(r.corrects)}</span>` : '<span class="faint">—</span>'}</td>
          <td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`;
  }

  function hitsTable(hits, today, withStatus) {
    return `<table class="iso"><tr><th>rank</th><th>score</th><th>claim</th><th>hit</th></tr>
      ${hits.map((h) => `<tr class="${withStatus && h.status ? "muted" : ""}"><td class="mono small">#${h.rank}</td><td class="mono small" style="white-space:nowrap">${Number(h.score || 0).toFixed(3)}</td>
        <td>${claimPill(h.claim, withStatus && h.status)}${withStatus && h.status ? `<br><span class="pill warn">${esc(h.status)}</span>` : ""}</td>
        <td class="small"><span class="mono">${esc(h.record || "?")}</span> ${esc(h.body)}${!withStatus && today && h.logged_on >= today ? ' <span class="faint">(logged today — not part of this morning’s read)</span>' : ""}</td></tr>`).join("")}</table>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("Three quick reads");
    const [srch, all, newest] = x.reads;
    return `<h1>Three quick reads of “${esc(x.question)}”</h1>
      <p class="lead">The same memory, read three ordinary ways.</p>
      <div class="card"><div class="row spread"><h2 style="margin:0">1 · Ask search, go with most hits</h2>${verdictPill(srch.winner)}</div>
        <p class="small muted">What an AI tool in a rep's prep flow would do. Enterprise-only ${srch.votes["sso-enterprise"]} · Business ${srch.votes["sso-business"]} among the top 8.</p>
        ${hitsTable(srch.hits, story().today, false)}</div>
      <div class="cols"><div class="card"><div class="row spread"><h2 style="margin:0">2 · Count every record</h2>${verdictPill(all.winner)}</div>
        <p class="small" style="margin-bottom:0"><code>fact list --projects</code>, every page: Enterprise-only <b>${all.votes["sso-enterprise"]}</b> · Business <b>${all.votes["sso-business"]}</b>.</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">3 · Trust the newest record</h2>${verdictPill(newest.winner)}</div>
        <p class="small" style="margin-bottom:0"><span class="mono">${esc(newest.record.record)}</span> ${esc(newest.record.logged_on)} · ${esc(kname(newest.record.kind))} · ${esc(newest.record.account || "")}<br>“${esc(newest.record.body)}”</p></div></div>`;
  }

  function claimTable(a) {
    return `<table class="iso"><tr><th>claim</th><th>records</th><th>sources</th><th>still standing</th></tr>
      ${Object.values(a.claims).map((c) => `<tr><td>${claimPill(c.claim)}</td><td><b>${c.records.length}</b></td><td><b>${c.roots.length}</b></td>
        <td><b>${c.live.length}</b> <span class="small muted">${esc(c.live.join(", "))}${c.retracted.length ? ` · withdrawn: ${esc(c.retracted.join(", "))}` : ""}</span></td></tr>`).join("")}</table>`;
  }
  function lineage() {
    const x = S.lineage; if (!x) return empty("Walk the lineage");
    const a = x.audit, c = x.control;
    const src = a.lineage[0];
    const after = a.lineage.filter((l) => l.after).map((l) => l.record);
    return `<h1>Walk the lineage — ${a.lineage.length} records, one source, withdrawn</h1>
      <p class="lead">The same <code>fact list</code>, read differently: follow <code>derived_from</code> up to the record that was copied from nothing, and check whether a correction in memory withdrew it. Plain code over the metadata — no model.</p>
      <div class="card"><h2 style="margin-top:0">Every SSO record, source first, copies under it</h2><div class="block">${esc(x.tree.map((l) => l.replace(/^    /, "")).join("\n"))}</div></div>
      <div class="cols"><div class="card mine"><div class="row spread"><h2 style="margin:0">As of today</h2>${verdictPill(a.verdict)}</div>${claimTable(a)}
        ${src ? `<p class="small" style="margin-bottom:0">${a.lineage.length} records rest on <b>${esc(src.root)}</b>, withdrawn by <b>${esc(src.by)}</b> on ${esc(src.since)}; <b>${after.length}</b> of them (${esc(after.join(", "))}) were written after that correction was already in memory.</p>` : ""}</div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Control — as of ${esc(c.day)}</h2>${verdictPill(c.verdict)}</div>${claimTable(c)}
        <p class="small muted" style="margin-bottom:0">Before the correction existed, the audit backs “Enterprise-only” — on one source, not ${c.claims["sso-enterprise"].records.length} votes. It takes no side; it folds copies back into their source.</p></div></div>`;
  }

  function written() {
    const x = S.written; if (!x) return empty("Write it back");
    const card = x.card;
    return `<h1>Write it back — mark the copies, issue a new card, find who was told</h1>
      <p class="lead">${x.marked.length ? `<b>${x.marked.length}</b> copies marked` : `All ${x.already.length} copies were marked by an earlier run`} with <code>fact update --metadata</code> (<code>status: retracted-upstream</code>, <code>retracted_by</code>, <code>root</code>) — the text stays word for word. Then a new battle card that cites what it rests on.</p>
      <div class="cols"><div class="card mine"><h2 style="margin-top:0">Battle card v5 <span class="mono small faint">${esc(card.record)}</span></h2><p class="small">“${esc(card.body)}”</p>
        <div class="small">basis: ${(card.basis || []).map((b) => `<span class="pill ok">${esc(b)}</span>`).join(" ")} · cited ${x.cited}/${(card.basis || []).length} back to records in memory · supersedes <span class="mono">${esc(card.supersedes)}</span></div></div>
      <div class="card"><h2 style="margin-top:0">Prospects told “Enterprise-only”</h2><table class="iso"><tr><th>account</th><th>told</th><th>now</th></tr>
        ${x.told.map((t) => `<tr><td>${esc(t.account)}</td><td class="small">${esc(t.on)} · ${esc(t.by)} <span class="mono faint">${esc(t.record)}</span></td>
          <td>${t.lost ? `<span class="pill err">lost ${esc(t.lost_on)}</span>` : story().rep.call.includes(t.account) ? `<span class="pill warn">call tomorrow</span>` : '<span class="pill">open</span>'}</td></tr>`).join("")}</table></div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">The same search, after the write-back</h2>${verdictPill(x.answer)}</div>
        <p class="small muted">Search does not read status: <b>${x.retracted_hits}</b> of the ${x.hits.length} hits are marked retracted-upstream and still come back. Read the metadata with them and the standing hits say Business ${x.standing_votes["sso-business"]} · Enterprise-only ${x.standing_votes["sso-enterprise"]}.</p>
        ${hitsTable(x.hits, null, true)}</div>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the intel</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 as it comes back, with each hit's claim and status read from its metadata. Or run the lineage audit as of any date.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Does Pendrel include SSO on its Business plan?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="chips">${["2026-08-24", "2026-09-01", "2026-10-14"].map((d) => `<button type="button" class="chip" data-day="${d}">audit as of ${d}</button>`).join("")}</div>
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
    st.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => askAudit(b.dataset.day)));
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
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length} as returned</h2>${hitsTable(r.hits, null, true)}
      <p class="small">Standing sources on SSO today: ${verdictPill(r.verdict)}${r.lineage.length ? ` · copies of a withdrawn source: <span class="mono">${esc(r.lineage.join(", "))}</span>` : ""}</p>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askAudit(day) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/audit", { day });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="row spread"><h2>Lineage audit as of ${esc(r.audit.day)}</h2>${verdictPill(r.audit.verdict)}</div>
      <div class="block">${esc(r.tree.map((l) => l.replace(/^    /, "")).join("\n"))}</div>${claimTable(r.audit)}`;
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.lineage = null; S.written = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's project and every record on it from your account?")) return;
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
    else if (k === "quick_reads") S.quick = d;
    else if (k === "provenance") S.lineage = d;
    else if (k === "written") S.written = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and every record on it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask the intel a question of your own — “Ask the intel” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick_reads", "provenance", "written",
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

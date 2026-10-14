/* Client matter handoff memory for law firms — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two matters + opposing counsel", view: "setup" },
    { n: 3, title: "Intake", sub: "matter records, counsel profile", view: "intake" },
    { n: 4, title: "Handoff read", sub: "matter + counsel's patterns", view: "handoff" },
    { n: 5, title: "The wall check", sub: "every fact on the shared actor", view: "wall" },
    { n: 6, title: "Move them back", sub: "specifics to their own matter", view: "moved" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, handoff: null, wall: null, moved: null,
    ask: { q: "", html: "", matter: "pellharbor", actor: true }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const mshort = (k) => (story()?.matters || []).find((m) => m.key === k)?.short || k;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const src = (h) => h.where === "holm" ? `<span class="pill accent">Holm actor</span>` : `<span class="pill">${esc(mshort(h.where))} project</span>`;
  const verdict = (w) => !w ? "" : w.verdict === "withheld" ? `<span class="pill err">✗ withheld · ${esc(mshort(w.origin))}</span>`
    : w.verdict === "unlabelled" ? `<span class="pill warn">? unlabelled</span>` : `<span class="pill ok">✓ ${esc(w.verdict)}</span>`;
  const label = (r) => r.scope ? `<span class="mono small">${esc(r.matter)}/${esc(r.scope)}</span>` : `<span class="pill warn">no label</span>`;
  const identPills = (xs) => (xs || []).length ? xs.map((i) => `<span class="pill err">${esc(i)}</span>`).join(" ") : `<span class="pill ok">none</span>`;
  function hitTable(hits, opts = {}) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>fact · where it lives · wall</th></tr>
      ${hits.map((h) => `<tr class="${h.wall?.verdict === "withheld" ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(opts.at ? h.at : "#" + h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${src(h)} ${h.where === "holm" || h.wall?.verdict === "withheld" ? verdict(h.wall) : ""}</td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>search a matter · ± Holm's actor</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, handoff, wall, moved, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays handoff day on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about a minute · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The matter wall holds. The opposing counsel walks through it.</h1>
      <p class="lead">Brannock &amp; Lise keeps one MemoryLake project per matter, and one actor per recurring opposing counsel — his negotiation patterns, reusable on the next matter.
      Garrick Holm was on the other side of Fenwick (settled, amount confidential) and is on the other side of Pell Harbor (mediation November 5). The associate who ran both is leaving.
      Searching Pell Harbor's project returns nothing from Fenwick. Loading Pell Harbor <i>plus</i> Holm's patterns — one search, the union of the two — brings back Fenwick's settlement amount, its client's authority and a gap in its evidence.
      A wall check on the shared actor finds them (one only by a scan, it had no label); moving them back to Fenwick fixes the source.
      This page drives the <code>memorylake</code> CLI for real. The firm, clients, parties and lawyers are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the two projects, the actor and every record on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/client-matter-handoff-memory-for-law-firms" target="_blank" rel="noopener">memorylake.ai › Client matter handoff memory for law firms</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project per matter, one actor for the opposing counsel");
    return `<h1>One project per matter, one actor for the opposing counsel</h1>
      <p class="lead">Each matter is its own memory scope — the wall. Garrick Holm gets an actor of his own, because he turns up again: what Mira learned about him on one matter should help on the next.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>status</th><th>id</th></tr>
        ${su.matters.map((m) => `<tr><td><span class="pill">project</span></td><td><b>${esc(m.name)}</b><br><span class="small faint">client ${esc(m.client)}</span></td><td class="small">${esc(m.status)}</td><td class="mono small faint">${esc(su.projects[m.key])}</td></tr>`).join("")}
        <tr><td><span class="pill accent">actor</span></td><td><b>${esc(su.counsel.name)}</b>, ${esc(su.counsel.firm)}<br><span class="small faint">opposing counsel in both matters</span></td><td class="small">shared</td><td class="mono small faint">${esc(su.actor)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const group = (w, title) => `<div class="card ${w === "holm" ? "mine" : ""}"><h2 style="margin-top:0">${title}</h2><table class="iso"><tr><th>id</th><th>date</th>${w === "holm" ? "<th>label</th>" : ""}<th>record</th></tr>
      ${x.records.filter((r) => r.where === w).map((r) => `<tr class="${w === "holm" && !r.scope ? "hl" : ""}"><td class="mono small">${esc(r.record)}</td><td class="small muted" style="white-space:nowrap">${esc(r.on)}</td>
        ${w === "holm" ? `<td>${label(r)}</td>` : ""}<td class="small">${r.privileged ? '<span class="pill warn">privileged</span> ' : ""}${esc(r.body)}</td></tr>`).join("")}</table></div>`;
    return `<h1>Intake — matter records on the matter, counsel patterns on the counsel</h1>
      <p class="lead">One <code>fact add</code> per scope, word for word, then <code>fact update --metadata</code>: where it came from (<code>matter</code>), whether it is a portable pattern or matter-specific (<code>scope</code>), privileged or not.
      Mira's notes on Holm include three Fenwick specifics — and one of them never got a label.${x.restored ? ` <span class="faint">(${x.restored} fact(s) an earlier run moved were put back where they were on handoff day.)</span>` : ""}</p>
      ${group("holm", "Garrick Holm — opposing-counsel actor")}
      <div class="cols">${(story()?.matters || []).map((m) => group(m.key, `${esc(m.name)} — project`)).join("")}</div>`;
  }

  function handoff() {
    const x = S.handoff; if (!x) return empty("Handoff read");
    return `<h1>Handoff read — ${x.leaks.length} of ${x.distinct} facts are Fenwick's</h1>
      <p class="lead">Control first: the question on the Pell Harbor project alone — nothing from Fenwick, the wall holds. Then the handoff read the page describes: load the matter <b>and</b> the opposing counsel's patterns,
      <code>search --projects &lt;Pell Harbor&gt; --actors &lt;Holm&gt;</code>. That is a union — everything on Holm comes along, whichever matter it was written on.</p>
      <div class="card"><div class="row spread"><h2 style="margin:0">Control · --projects Pell Harbor</h2><span class="pill ok">other matters' names: ${x.control.idents.length || "none"}</span></div>
        <p class="small">“${esc(x.control.q)}”</p>${hitTable(x.control.hits.slice(0, 4))}</div>
      ${x.asked.map((a) => `<div class="card mine"><div class="row spread"><h2 style="margin:0">--projects Pell Harbor --actors Holm</h2><span class="pill ${a.hits.some((h) => h.wall.verdict === "withheld") ? "err" : "ok"}">${a.hits.filter((h) => h.wall.verdict === "withheld").length} from Fenwick</span></div>
        <p class="small">“${esc(a.q)}”</p>${hitTable(a.hits)}</div>`).join("")}
      <div class="card"><h2 style="margin-top:0">Now in the Pell Harbor handoff</h2><p class="small" style="margin-bottom:0">${identPills(x.idents)}</p></div>`;
  }

  function wall() {
    const x = S.wall; if (!x) return empty("The wall check");
    const c = x.counts;
    return `<h1>The wall check — ${c.withheld} of ${x.rows.length} facts on Holm belong to another matter</h1>
      <p class="lead">Every fact on the shared actor — <code>fact list</code>, every page, not search's top 8 — read against the matter being handed over. The <b>label</b> says where a fact came from and whether it is a portable pattern;
      a <b>scan</b> looks for any other matter's names and figures. Either one withholds a fact. ${x.by_scan.length ? `The scan is the only thing that catches ${x.by_scan.map((r) => `<span class="mono">${esc(r)}</span>`).join(", ")} — nobody labelled it.` : ""}</p>
      <div><div class="card mine"><div class="row spread"><h2 style="margin:0">Holm's actor vs Pell Harbor</h2><span class="pill err">${c.withheld} withheld</span></div>
        <table class="iso"><tr><th>id</th><th>label</th><th>verdict</th><th>fact</th></tr>
        ${x.rows.map((r) => `<tr class="${r.wall.verdict === "withheld" ? "hl" : ""}"><td class="mono small">${esc(r.record)}</td><td style="white-space:nowrap">${label(r)}</td><td style="white-space:nowrap">${verdict(r.wall)}<br><span class="small faint">${esc(r.wall.reason)}</span></td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">The handoff, through the wall</h2><span class="pill ok">${x.brief.length} kept</span></div>
        <p class="small">The same two searches, withheld facts dropped. Holm's patterns stay.</p>${hitTable(x.brief, { at: true })}
        <p class="small" style="margin-bottom:0">Other matters' names and figures left: ${identPills(x.kept_idents)}</p></div></div>`;
  }

  function moved() {
    const x = S.moved; if (!x) return empty("Move them back");
    return `<h1>Move them back — ${x.moved.length} facts to their own matter</h1>
      <p class="lead">Filtering at read time protects this handoff; the source still leaks into the next one. So each withheld fact is pinned word for word on its own matter's project, then deleted from Holm's actor.
      Holm's actor keeps ${x.left.length} facts (${x.left.map(esc).join(", ")}). Asked in new words — <code>search</code> can answer a repeated question from its cache.</p>
      <div><div class="card mine"><div class="row spread"><h2 style="margin:0">Pell Harbor + Holm, after</h2><span class="pill ${x.idents.length ? "err" : "ok"}">other matters' names: ${x.idents.length || "none"}</span></div>
        <p class="small">“${esc(x.q)}”${x.tries > 1 ? ` <span class="faint">(index caught up on try ${x.tries})</span>` : ""}</p>${hitTable(x.hits)}
        <p class="small" style="margin-bottom:0">Holm's portable patterns in the top 8: <b>${x.patterns.length}</b> (${x.patterns.map(esc).join(", ")})</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Fenwick, still whole</h2><span class="pill ok">${x.found.length} of ${x.moved.length} moved facts found</span></div>
        <p class="small">“${esc(x.origin_q)}” · --projects Fenwick</p>${hitTable(x.origin_hits.slice(0, 5))}</div></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask;
    return `<h1>Ask</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 on one matter, with or without Holm's actor in scope; every hit labelled with where it lives, and judged by the wall. Or run the wall check again.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. How does Garrick Holm negotiate at mediation?" value="${esc(a.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["pellharbor", "fenwick"].map((m) => `<button type="button" class="chip ${a.matter === m ? "on" : ""}" data-matter="${m}">${esc(mshort(m))}</button>`).join("")}
          <button type="button" class="chip ${a.actor ? "on" : ""}" data-actor="1">${a.actor ? "+ Holm's actor" : "matter alone"}</button>
          <button type="button" class="chip" data-wall="1">Wall check now</button></div>
        <div class="chips">${(story()?.ask_examples || []).map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelectorAll("[data-matter]").forEach((b) => b.addEventListener("click", () => { S.ask.q = $("#ask-q").value; S.ask.matter = b.dataset.matter; renderStage(); }));
    st.querySelector("[data-actor]")?.addEventListener("click", () => { S.ask.q = $("#ask-q").value; S.ask.actor = !S.ask.actor; renderStage(); });
    st.querySelector("[data-wall]")?.addEventListener("click", askWall);
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { q, matter: S.ask.matter, with_actor: S.ask.actor });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="row spread"><h2>search “${esc(r.q)}” — --projects ${esc(mshort(r.matter))}${r.with_actor ? " --actors Holm" : ""}</h2>
      <span class="pill ${r.idents.length ? "err" : "ok"}">other matters' names: ${r.idents.length ? r.idents.map(esc).join(", ") : "none"}</span></div>${hitTable(r.hits)}`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askWall() {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/wall", {});
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="row spread"><h2>Holm's actor vs Pell Harbor, now</h2><span class="pill ${r.counts.withheld ? "err" : "ok"}">${r.counts.withheld} withheld of ${r.rows.length}</span></div>
      <table class="iso"><tr><th>id</th><th>label</th><th>verdict</th><th>fact</th></tr>${r.rows.map((x) => `<tr class="${x.wall.verdict === "withheld" ? "hl" : ""}"><td class="mono small">${esc(x.record)}</td><td>${label(x)}</td><td>${verdict(x.wall)}</td><td class="small">${esc(x.body)}</td></tr>`).join("")}</table>`;
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
  function resetView() { S.setup = null; S.intake = null; S.handoff = null; S.wall = null; S.moved = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's two matter projects, the opposing-counsel actor and every record on them from your account?")) return;
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
    else if (k === "handoff") S.handoff = d;
    else if (k === "wall") S.wall = d;
    else if (k === "moved") S.moved = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the two projects, the actor and every record on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question of your own, with or without Holm's actor in scope — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "handoff", "wall", "moved",
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
    show(state.status === "running" ? S.view : S.moved ? "moved" : "connect");
    subscribe();
  })();
})();

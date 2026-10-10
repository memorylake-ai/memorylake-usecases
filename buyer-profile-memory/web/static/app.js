/* Buyer profile memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => iso ? new Date(iso).toISOString().slice(0, 19).replace("T", " ") + "Z" : "";
  const money = (v) => Number(v).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Buyer + team", sub: "actor + project", view: "setup" },
    { n: 3, title: "Kickoff", sub: "Priya pins the profile", view: "kickoff" },
    { n: 4, title: "Showings", sub: "events on the team project", view: "showings" },
    { n: 5, title: "Call notes", sub: "fact conflict --actor", view: "notes" },
    { n: 6, title: "Takeover", sub: "screen · said vs responded", view: "takeover" },
    { n: 7, title: "Resolve", sub: "keep_fact + evidence", view: "resolve" },
    { n: 8, title: "Handoff record", sub: "fact conflict get", view: "handoff" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    setup: null, facts: {}, showings: null, detector: null, waiting: false, screens: {}, reflection: null, resolved: null, handoff: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask Lena's memory<small>profile + showings, one search</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, kickoff, showings, notes, takeover, resolve, handoff, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Lena's memory</dt><dd>${S.st.ids?.buyer ? `<span class="mono muted">${esc(S.st.ids.buyer)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>One buyer, three agents, one memory that notices when the notes disagree.</h1>
      <p class="lead"><b>Harborline Realty</b> keeps Lena Park's profile on her own <b>actor</b> and every showing on the <b>team project</b>.
      Priya ran the kickoff; Tom covered the showings and wrote two call notes that contradict it. MemoryLake's <b>contradiction detector</b> flags both on Lena's actor
      (<code>fact conflict list --actor</code>). Today <b>Maya</b> takes Lena over: her tour screen marks what depends on the dispute, the showings say what Lena actually responded to,
      and <code>keep_fact</code> settles it. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the team project and the buyer.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/buyer-profile-memory-for-real-estate-teams" target="_blank" rel="noopener">memorylake.ai › buyer profile memory for real estate teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const s = S.setup; if (!s || !story()) return empty("Buyer + team");
    const a = story().agents;
    return `<h1>One profile per buyer, one history for the team</h1>
      <p class="lead">Lena is an <b>actor</b> tagged <code>buyer</code>, <code>stage:touring</code>: her profile — the statements about what she wants — is pinned on it.
      The <b>team project</b> holds the showings, which every agent covering a buyer reads. The two scopes are searched together later with one <code>search --projects … --actors …</code>.</p>
      <div class="grid"><div class="card"><div class="row spread"><b>${esc(s.buyer.name)}</b><span class="pill accent">buyer actor</span></div>
          <div class="muted small">${esc(s.buyer.description)}</div><div class="mono small faint">${esc(s.ids.buyer)}</div></div>
        <div class="card"><div class="row spread"><b>${esc(s.project)}</b><span class="pill">team project</span></div>
          <div class="muted small">showing history, read by every agent</div><div class="mono small faint">${esc(s.ids.project)}</div></div></div>
      <h2>The agents</h2><div class="card">${Object.values(a).map((x) => `<div class="memfact"><span><b>${esc(x.name)}</b> — ${esc(x.role)}</span></div>`).join("")}</div>`;
  }

  const kindPill = (k) => `<span class="pill ${k === "preference" ? "" : k === "financing" ? "warn" : "accent"}">${esc(k)}</span>`;
  function factRow(f) {
    return `<div class="memfact ${f.forgotten ? "gone" : ""}"><span>${kindPill(f.fact_kind)} ${esc(f.fact)}<br><span class="small muted">${esc(f.agent)}, ${esc(f.said_on)}</span>
      ${f.id ? `<span class="mono small faint"> · ${esc(f.id)}</span>` : ""}</span><span class="when">${f.forgotten ? "forgotten by an earlier resolution" : '<span class="pill ok">pinned</span>'}</span></div>`;
  }
  function kickoff() {
    if (!story()) return "";
    const k = story().kickoff, got = Object.values(S.facts).filter((f) => f.source === "kickoff");
    return `<h1>Kickoff — Priya pins Lena's profile (${esc(k.date)})</h1>
      <p class="lead">Four statements, each pinned verbatim on Lena's actor with <code>fact add --actor</code>, then <code>fact update --metadata</code>
      with the rule, who said it and when. They go in ${S.data.gap}s apart: back-to-back writes are checked for contradictions minutes later, as a batch.</p>
      <div class="card">${got.length ? got.map(factRow).join("") : '<div class="empty">pending</div>'}</div>`;
  }

  const reactPill = (r) => `<span class="pill ${r === "loved" ? "ok" : r === "disliked" ? "err" : "warn"}">${esc(r)}</span>`;
  function showings() {
    const sh = S.showings; if (!sh) return empty("Showing history");
    return `<h1>Showing history — on the team project, not on Lena's profile</h1>
      <p class="lead">Each showing is an event: what was shown, by whom, and how Lena reacted. Events go on the team project. We measured why:
      with “she called this cul-de-sac house her favorite” sitting on her profile, the detector stayed quiet about the cul-de-sac contradiction in <b>0 of 7</b> runs; with the events kept apart, it raised it in <b>4 of 4</b>.</p>
      <div class="card"><table class="tour"><thead><tr><th>date</th><th>listing</th><th>street</th><th>price</th><th>reaction</th><th>agent</th></tr></thead><tbody>
      ${sh.map((s) => `<tr><td class="mono small">${esc(s.date)}</td><td><b>${esc(s.listing)}</b><div class="small muted">${esc(s.text.split(": ").slice(1).join(": "))}</div></td><td>${esc(s.street_type)}</td><td class="mono">${money(s.price)}</td><td>${reactPill(s.reaction)}</td><td class="small">${esc(s.agent)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function conflictCard(c) {
    const rs = c.resolve || {};
    return `<div class="card conflict"><div class="row spread"><div><span class="pill err">${esc(c.category)} · ${esc(c.conflict_type)}</span> <b>${esc(c.name)}</b></div>
        ${c.resolved ? `<span class="pill ok">resolved · ${esc(rs.strategy || "")}</span>` : '<span class="pill warn">open</span>'}</div>
      <div class="mono small faint">${esc(c.id)}</div><p class="small">${esc(c.description)}</p>
      ${c.facts.map((f) => `<div class="memfact small"><span>“${esc(f.text)}”<br><span class="muted">${f.agent ? `${esc(f.agent)}, ${esc(f.said_on)}` : "not from this story"}</span> <span class="mono faint">${esc(f.id)}</span></span></div>`).join("")}</div>`;
  }
  function notes() {
    if (!story()) return "";
    const n = Object.values(S.facts).filter((f) => f.source === "call notes"), d = S.detector;
    return `<h1>Call notes — Tom writes two notes; MemoryLake flags what contradicts</h1>
      <p class="lead">Tom covered Lena's showings and, after the second round (${esc(story().notes.date)}), pinned two notes on the same actor.
      MemoryLake checks every fact written to an actor against what that actor already holds, and raises a conflict when the two <i>cannot both be true</i>.</p>
      <div class="card">${n.length ? n.map(factRow).join("") : '<div class="empty">pending</div>'}</div>
      ${S.waiting ? `<p><span class="pill busy"><span class="spinner"></span> waiting for the detector on Lena's actor (up to ${S.data.detect_wait}s)…</span></p>` : ""}
      ${d ? `<h2><code>fact conflict list --actor</code> — ${d.conflicts.length} conflict(s) naming Tom's notes ${d.fresh ? `<span class="muted small">after ${d.waited}s</span>` : '<span class="muted small">raised by an earlier run</span>'}</h2>
        ${d.conflicts.map(conflictCard).join("")}
        ${d.missing.length ? `<div class="card"><b>✗ nothing raised</b> on ${d.missing.map(esc).join(", ")} within the wait — the detector sometimes answers minutes later, as a batch. <code>python3 demo.py conflicts</code> checks again.</div>` : ""}
        ${d.others.length ? `<h2>Other conflicts on Lena's actor <span class="muted small">not planned by the story — shown, not hidden</span></h2>${d.others.map(conflictCard).join("")}` : ""}` : ""}`;
  }

  const markPill = (m) => `<span class="pill ${m === "✓" ? "ok" : m === "✗" ? "err" : m === "?" ? "accent" : "warn"}">${esc(m)} ${esc(S.data?.verdict?.[m] || "")}</span>`;
  function screenTable(res) {
    return `<div class="card"><table class="tour"><thead><tr><th>listing</th><th>verdict</th><th>why</th></tr></thead><tbody>
      ${res.results.map((r) => { const rows = r.rows.filter((x) => x.mark !== "✓");
        return `<tr><td><b>${esc(r.listing)}</b></td><td>${markPill(r.verdict)}</td><td class="small">${rows.length ? rows.map((x) => `<div><b>${esc(x.mark)}</b> ${esc(x.value)} — ${x.facts.map((f) => `${esc(f.agent.split(" ")[0])} ${esc(f.said_on.slice(5))}: ${esc(f.label)} ${f.ok ? "✓" : "✗"}`).join(" <span class='faint'>vs</span> ")}${x.conflict && x.mark === "?" ? ` <span class="mono faint">${esc(x.conflict)}</span>` : ""}</div>`).join("") : `passes all ${r.rows.length} rules in her profile`}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }
  function takeover() {
    const b = S.screens.before; if (!b) return empty("Takeover");
    const refl = S.reflection || [];
    const sym = { contradicted: "≠", consistent: "=", untested: "·" };
    return `<h1>Takeover — Maya screens her tour against Lena's memory</h1>
      <p class="lead">Maya has never met Lena. Everything here is read from memory: the profile from Lena's actor (${b.profile.length} facts), the showings from the team project.
      Where two live facts on Lena's actor disagree, the screen does not guess — it says <b>ask first</b> and names the conflict.</p>
      ${screenTable(b)}
      <h2>Said vs responded <span class="muted small">kickoff profile × showing history</span></h2>
      <div class="card">${refl.map((r) => `<div class="memfact"><span><b>${sym[r.verdict]}</b> said “${esc(r.said)}” <span class="muted small">(${esc(r.agent)}, ${esc(r.said_on)})</span><br>
        <span class="small">${r.evidence.length ? r.evidence.map((x) => `${reactPill(x.reaction)} ${esc(x.listing)} <span class="muted">(${esc(x.value)}, ${esc(x.date)})</span>`).join(" &nbsp; ") : '<span class="muted">no showing tested it</span>'}</span></span>
        <span class="when">${esc(r.verdict)}</span></div>`).join("")}</div>`;
  }

  function resolve() {
    const r = S.resolved; if (!r) return empty("Resolve");
    return `<h1>Resolve — keep_fact, with the showings as evidence</h1>
      <p class="lead">For each conflict Maya keeps the note the showings back — <code>fact conflict resolve --actor … --strategy keep_fact --keep-fact-id …</code> — and the other fact is <b>forgotten</b>.</p>
      <div class="card">${r.resolved.length ? r.resolved.map((x) => `<div class="memfact"><span><span class="mono small">${esc(x.id)}</span> → keep “${esc(x.label)}”<br><span class="small muted">${esc(x.why)}</span></span>
        <span class="when">forgotten: ${x.forgotten.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ") || "nothing"}</span></div>`).join("") : '<p class="muted" style="margin:0">Nothing open to resolve in this pass (resolved by an earlier run).</p>'}</div>
      <h2>Lena's profile now <span class="muted small">${r.profile.length} facts · fact list --actors</span></h2>
      <div class="card">${r.profile.map((p) => `<div class="memfact"><span>${kindPill(p.kind)} ${esc(p.text)}<br><span class="small muted">${esc(p.agent)}, ${esc(p.said_on)}</span></span></div>`).join("")}</div>
      <h2>The same tour, screened again</h2>${S.screens.after ? screenTable(S.screens.after) : ""}`;
  }

  function handoff() {
    const h = S.handoff; if (!h) return empty("Handoff record");
    return `<h1>Handoff record — what changed on the profile, and why</h1>
      <p class="lead"><code>fact conflict get</code> keeps every resolution: the strategy, the fact kept, the fact forgotten, when — and the forgotten kickoff note word for word.
      With the profile, the showings and the before/after screen it becomes Lena's handoff record, written to <code>${esc(h.file)}</code>.</p>
      <div class="card"><table class="tour"><thead><tr><th>listing</th><th>before</th><th>after</th></tr></thead><tbody>
        ${h.before.map((x, i) => `<tr><td><b>${esc(x.listing)}</b></td><td>${markPill(x.verdict)}</td><td>${markPill(h.after[i].verdict)}</td></tr>`).join("")}</tbody></table></div>
      ${h.resolved.map((c) => { const rs = c.resolve || {}; return `<div class="card conflict"><div class="row spread"><b>${esc(c.name)}</b><span class="pill ok">resolved · ${esc(rs.strategy)}</span></div>
        <dl class="kv small"><dt>raised</dt><dd>${fmtTime(c.created_at)}</dd><dt>resolved</dt><dd>${fmtTime(rs.created_at)}</dd><dt>conflict</dt><dd class="mono">${esc(c.id)}</dd></dl>
        ${c.facts.map((f) => { const gone = (rs.forgotten_fact_ids || []).includes(f.id); return `<div class="memfact small ${gone ? "gone" : ""}"><span>“${esc(f.text)}” <span class="muted">(${esc(f.agent)}, ${esc(f.said_on)})</span></span><span class="when">${gone ? "forgotten" : "kept"}</span></div>`; }).join("")}</div>`; }).join("")}
      <h2>The record <span class="muted small">${esc(h.file)}</span></h2><pre class="instr log">${esc(h.markdown)}</pre>`;
  }

  function ask() {
    const ready = !!S.st.ids?.buyer;
    return `<h1>Ask Lena's memory</h1>
      <p class="lead">One search over her profile and the team's showings: <code>search --projects &lt;team&gt; --actors &lt;Lena&gt;</code> returns both. Each hit is labelled with where it lives.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. How does Lena feel about busy streets?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
        <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["How does Lena feel about cul-de-sacs?", "What is Lena's budget?", "How does Lena feel about busy streets?", "What did Lena love?", "Can the house need repairs?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askMemory($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askMemory(c.dataset.q); }));
    }
  }
  async function askMemory(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = r.hits.length ? r.hits.map((h) => `<div class="memfact"><span><span class="pill ${h.scope === "profile" ? "accent" : ""}">${esc(h.scope)}</span> ${esc(h.text)}</span><span class="when">#${esc(h.rank)}</span></div>`).join("")
      : '<div class="empty">No hits.</div>';
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
  function resetView() { S.setup = null; S.facts = {}; S.showings = null; S.detector = null; S.waiting = false; S.screens = {}; S.reflection = null; S.resolved = null; S.handoff = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the team project (showings) and the buyer actor (her profile and conflicts) from your account?")) return;
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
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "text") { /* the CLI's own summary lines; the views show the same data */ }
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "existing") S.st.ids = d;
    else if (k === "setup") { S.setup = d; S.st.ids = d.ids; }
    else if (k === "profile_fact") S.facts[`${d.source}:${d.key}`] = d;
    else if (k === "showings") S.showings = d.showings;
    else if (k === "detector_wait") S.waiting = true;
    else if (k === "detector") { S.detector = d; S.waiting = false; }
    else if (k === "screen") S.screens[d.phase] = d;
    else if (k === "reflection") S.reflection = d.items;
    else if (k === "resolved") S.resolved = d;
    else if (k === "handoff") S.handoff = d;
    else if (k === "done") {
      finishRunning("done"); S.waiting = false; term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the team project and the buyer are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Open Takeover and Resolve for the before/after, or try “Ask Lena's memory”.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); S.waiting = false; term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "setup", "profile_fact", "showings", "detector_wait",
    "detector", "screen", "reflection", "resolved", "handoff", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => { try { onEvent(JSON.parse(e.data)); } catch { /* the browser's own `error` event has no data */ } }));
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
      const h = (e) => { let ev; try { ev = JSON.parse(e.data); } catch { return; } onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") { Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; }); S.waiting = false; }
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.handoff ? "takeover" : "connect");
    subscribe();
  })();
})();

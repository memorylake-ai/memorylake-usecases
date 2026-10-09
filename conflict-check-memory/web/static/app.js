/* Conflict check memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => iso ? new Date(iso).toISOString().slice(0, 19).replace("T", " ") + "Z" : "";

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Firm history", sub: "matters, letters, parties", view: "history" },
    { n: 3, title: "Intake 1", sub: "Harbor & Pine v. Tallis", view: "i1" },
    { n: 4, title: "Intake 2", sub: "Penn v. Marlow", view: "i2" },
    { n: 5, title: "Intake 3", sub: "Dovetail v. Brightline", view: "i3" },
    { n: 6, title: "Resolve", sub: "keep_fact · trust_document", view: "resolve" },
    { n: 7, title: "Audit trail", sub: "fact conflict get", view: "audit" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, history: null, letters: null, matters: [], parties: [], intakes: {}, resolved: null, audit: null,
    waiting: null, check: { q: "", side: "adverse", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const firm = () => S.data?.firm;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "check" ? "active" : ""}" data-view="check"><span class="n">?</span><span class="t">Check a party<small>per-party memory + search</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, history, i1: () => intake("i1"), i2: () => intake("i2"), i3: () => intake("i3"), resolve, audit, check }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Firm history</dt><dd>${S.st.ids?.project ? `<span class="mono muted">${esc(S.st.ids.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Every prior relationship, surfaced at intake — not just the ones the senior partner remembers.</h1>
      <p class="lead"><b>Hollis &amp; Reyes LLP</b> keeps its firm history in MemoryLake: the matter index, the archived letters, one actor per party.
      Elena Hollis is on leave; <b>Theo Lindqvist</b>, who joined in September, runs three intakes. Each gets two checks: the <b>party check</b> (per-party memory + firm-history search,
      judged by the firm's rules) and MemoryLake's <b>contradiction detector</b> (<code>fact conflict</code>), which compares what intake records with everything the firm already knows.
      This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the firm history, the parties and the uploaded letters.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/conflict-check-memory-for-small-law-firms" target="_blank" rel="noopener">memorylake.ai › conflict check memory for small law firms</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  const roleClass = (r) => r === "client" ? "ok" : r === "adverse" ? "err" : "warn";
  function history() {
    if (!firm()) return "";
    const h = S.history, gap = S.data.gap;
    const matters = S.matters.length ? S.matters : [];
    return `<h1>The firm's history, in one memory</h1>
      <p class="lead">A project holds the <b>matter index</b> (one pinned fact per matter) and the <b>archived letters</b> (imported documents).
      Every party the firm has met is an <b>actor</b> tagged <code>party</code> + <code>client</code> / <code>adverse</code> / <code>witness</code>, with its relationship pinned on it.
      Matter records go in one every ${gap}s: back-to-back writes are checked for contradictions minutes later, as a batch; one at a time, each is checked as it lands.</p>
      <h2>Archived letters <span class="muted small">proj doc import</span></h2>
      <div class="card">${S.letters ? S.letters.map((d) => `<div class="memfact"><span>📄 ${esc(d.name)}</span><span class="when">${esc(d.status || "")}</span></div>`).join("") : '<div class="empty">pending</div>'}</div>
      <h2>Matter index <span class="muted small">${matters.length}/${firm().matters.length} pinned · fact add --project</span></h2>
      <div class="card">${firm().matters.map((m) => `<div class="memfact ${matters.includes(m) ? "" : "faded"}"><span>${esc(m)}</span><span class="when">${matters.includes(m) ? '<span class="pill ok">pinned</span>' : '<span class="pill">queued</span>'}</span></div>`).join("")}</div>
      <h2>Parties <span class="muted small">${S.parties.length}/${firm().parties.length} · actor create --tags party,…</span></h2>
      <div class="grid parties">${firm().parties.map((p) => { const a = S.parties.find((x) => x.name === p.name);
        return `<div class="card party ${a ? "" : "faded"}"><div class="row spread"><b>${esc(p.name)}</b><span class="pill ${roleClass(p.role)}">${esc(p.status)} ${esc(p.role)}</span></div>
          <div class="muted small">${esc(p.fact.replace(p.name + ": ", ""))}</div>${a ? `<div class="mono small faint">${esc(a.id)}</div>` : ""}</div>`; }).join("")}</div>
      ${h ? `<p><span class="pill ${h.open_conflicts ? "warn" : "ok"}">${h.open_conflicts} open conflict(s) in the firm history before today</span></p>` : ""}`;
  }

  const verdictPill = (v) => v === "conflict" ? '<span class="pill err">✗ conflict</span>' : v === "review" ? '<span class="pill warn">! review</span>' : '<span class="pill ok">✓ clear</span>';
  function partyCard(c) {
    return `<div class="card party-check ${c.verdict}"><div class="row spread"><div><b>${esc(c.name)}</b> <span class="muted small">${c.side === "client" ? "prospective client" : "adverse party"}</span></div>${verdictPill(c.verdict)}</div>
      <div class="rule">${esc(c.why)}</div>
      ${c.known ? `<div class="sub">Per-party memory <span class="mono faint">${esc(c.known.id)}</span> · tags <code>party,${esc(c.known.role)},${esc(c.known.status)}</code></div>${c.pinned.map((p) => `<div class="memfact small"><span>${esc(p)}</span></div>`).join("")}`
        : '<div class="sub">Per-party memory: <b>no actor</b> for this party</div>'}
      <div class="sub">Firm history: ${c.facts.length} matter record(s), ${c.documents.length} letter(s) name it${c.left_out ? ` <span class="faint">(${c.left_out} other hit(s) left out — they do not name this party)</span>` : ""}</div>
      ${c.facts.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}
      ${c.documents.map((d) => `<div class="memfact small doc"><span>📄 <b>${esc(d.name)}</b> — ${esc(d.summary)}</span></div>`).join("")}</div>`;
  }
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  // Highlight the sentences of an excerpt that name one of these parties (same idea as demo.py's sentences_naming).
  function markExcerpt(text, names) {
    const cores = (names || []).map((n) => n.replace(/\s+(LLC|Inc\.?|Co\.?)$/, "")).filter(Boolean);
    if (!cores.length) return esc(text);
    const hit = new RegExp(cores.map(reEsc).join("|"), "i");
    return text.split("\n").map((line) => line.split(/(?<=[a-z0-9][.!?])\s+/).map((s) => hit.test(s) && !/^#/.test(s) && !s.trim().endsWith(",") ? `<mark>${esc(s)}</mark>` : esc(s)).join(" ")).join("\n");
  }
  function conflictCard(c, names) {
    return `<div class="card conflict"><div class="row spread"><div><span class="pill ${c.category === "m2d" ? "accent" : "err"}">${esc(c.category)} · ${esc(c.conflict_type)}</span> <b>${esc(c.name)}</b></div><span class="mono small faint">${esc(c.id)}</span></div>
      <p class="small">${esc(c.description)}</p>
      ${c.facts.map((f) => `<div class="memfact small"><span><span class="mono faint">${esc(f.id)}</span><br>${esc(f.text)}</span></div>`).join("")}
      ${c.chunks.map((k) => `<blockquote class="excerpt"><div class="small faint">📄 ${esc(k.document)} — excerpt the server matched</div>${markExcerpt(k.text, names)}</blockquote>`).join("")}</div>`;
  }
  function intake(key) {
    const def = (firm()?.intakes || []).find((i) => i.key === key); if (!def) return "";
    const it = S.intakes[key] || {};
    const pc = it.party_check, det = it.detector;
    const waiting = S.waiting && S.waiting.key === key;
    return `<h1>${esc(def.title)}</h1>
      <p class="lead">${esc(def.matter)} · intake ${esc(def.date)}${def.form_note ? ` · <b>${esc(def.form_note)}</b>` : ""}</p>
      <h2>Check 1 — the party check ${pc ? verdictPill(pc.verdict) : ""}</h2>
      <p class="muted small">Is there an actor for this party (<code>actor get --by-custom-id</code>), what is pinned on it (<code>fact list --actors</code>), and what does the firm history say
      (<code>search --projects &lt;history&gt;</code>, facts and letters)? The firm's rules decide.</p>
      ${pc ? `<div class="grid">${pc.checks.map(partyCard).join("")}</div>` : '<div class="empty">pending</div>'}
      <h2>Check 2 — record the intake, let MemoryLake look for contradictions</h2>
      <div class="card"><div class="mono small faint">$ memorylake fact add --project &lt;firm history&gt; "…"</div><div class="record">${esc(def.record)}</div>
        ${det ? `<div class="mono small faint">${det.fact_id ? esc(det.fact_id) : "recorded by an earlier run"}</div>` : ""}</div>
      ${waiting ? `<p><span class="pill busy"><span class="spinner"></span> waiting for the detector (up to ${S.data.detect_wait}s)…</span></p>` : ""}
      ${det ? (det.conflicts.length ? `<p><b>The detector raised ${det.conflicts.length} conflict(s)</b> naming this intake — <code>fact conflict list --project …</code></p>${det.conflicts.map((c) => conflictCard(c, def.adverse.map((a) => a.name))).join("")}`
        : `<div class="card"><b>The detector raised nothing</b> naming this intake${det.fresh ? ` within ${S.data.detect_wait}s` : ""}.
          ${key === "i2" ? `<p class="muted small" style="margin-bottom:0">It looks for statements that <i>cannot both be true</i>. “Penn wants to sue Marlow” contradicts nothing — it is a conflict of interest,
          which is the firm's rule to apply. The party check applied it.</p>` : ""}</div>`) : ""}`;
  }

  function resolve() {
    const r = S.resolved; if (!r) return empty("Resolve");
    return `<h1>Resolve — the archived letter and the matter record win</h1>
      <p class="lead">Theo resolves each conflict with the strategy its category allows: fact vs document → <code>trust_document</code>; fact vs fact → <code>keep_fact --keep-fact-id &lt;the 2019 matter record&gt;</code>.
      Either way the intake form's claim is <b>forgotten</b>, and the corrected record goes in.</p>
      <div class="card">${r.resolved.length ? r.resolved.map((x) => `<div class="memfact"><span><span class="pill ${x.category === "m2d" ? "accent" : "err"}">${esc(x.category)}</span> <span class="mono small">${esc(x.id)}</span> → <code>${esc(x.strategy)}</code></span>
        <span class="when">forgotten: ${x.forgotten.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ") || "nothing"}</span></div>`).join("") : '<p class="muted" style="margin:0">Nothing to resolve in this pass (resolved by an earlier run).</p>'}</div>
      <h2>The firm history afterwards</h2>
      <div class="card checks">
        <div class="check ${r.claim_gone ? "ok" : "bad"}"><span class="mark">${r.claim_gone ? "✓" : "✗"}</span><span>The intake form's claim (“no prior relationship with Brightline Properties LLC”) ${r.claim_gone ? "is gone" : "is still there"}</span></div>
        <div class="check ok"><span class="mark">✓</span><span>The corrected record is there: <i>${esc(r.corrected)}</i></span></div>
        <div class="check ${r.corrected_conflicts.length ? "bad" : "ok"}"><span class="mark">${r.corrected_conflicts.length ? "✗" : "✓"}</span><span>The detector raised ${r.corrected_conflicts.length} conflict(s) on the corrected record</span></div>
        <p class="muted small" style="margin:8px 0 0">${r.facts} facts in the firm history now.</p></div>`;
  }

  function audit() {
    const a = S.audit; if (!a) return empty("Audit trail");
    return `<h1>Audit trail — what was checked, what was raised, how it was resolved</h1>
      <p class="lead"><code>fact conflict get</code> keeps every resolution: the strategy, the fact kept, the facts forgotten, when — and the snapshot of the forgotten claim's exact words.
      Together with the party checks it becomes the check's due-diligence record, written to <code>${esc(a.file)}</code>.</p>
      ${a.resolved.map((c) => { const rs = c.resolve || {}; return `<div class="card conflict"><div class="row spread"><div><span class="pill ${c.category === "m2d" ? "accent" : "err"}">${esc(c.category)}</span> <b>${esc(c.name)}</b></div><span class="pill ok">resolved · ${esc(rs.strategy)}</span></div>
        <dl class="kv small"><dt>raised</dt><dd>${fmtTime(c.created_at)}</dd><dt>resolved</dt><dd>${fmtTime(rs.created_at)}</dd>${rs.keep_fact_id ? `<dt>kept</dt><dd class="mono">${esc(rs.keep_fact_id)}</dd>` : ""}</dl>
        ${c.facts.map((f) => { const gone = (rs.forgotten_fact_ids || []).includes(f.id); return `<div class="memfact small ${gone ? "gone" : ""}"><span>${esc(f.text)}</span><span class="when">${gone ? "forgotten" : "kept"}</span></div>`; }).join("")}
        ${c.chunks.map((k) => `<div class="muted small">evidence: 📄 ${esc(k.document)}</div>`).join("")}</div>`; }).join("") || '<div class="empty">No resolved conflicts yet.</div>'}
      ${a.open.length ? `<h2>Still open</h2><div class="card">${a.open.map((c) => `<div class="memfact"><span><span class="pill">${esc(c.category)}</span> ${esc(c.name)}</span><span class="when mono">${esc(c.id)}</span></div>`).join("")}</div>` : ""}
      <h2>The log <span class="muted small">${esc(a.file)}</span></h2><pre class="instr log">${esc(a.markdown)}</pre>`;
  }

  function check() {
    const ready = !!S.st.ids?.project;
    return `<h1>Check a party</h1>
      <p class="lead">The same party check the intakes ran: per-party memory, a search of the firm history, the firm's rules. Try a former client, a witness, or a name the firm has never met.</p>
      <div class="card"><form class="ask-form" id="check-form"><input id="check-q" placeholder="e.g. Lena Brandt" value="${esc(S.check.q)}" ${ready ? "" : "disabled"}>
        <select id="check-side" ${ready ? "" : "disabled"}><option value="adverse" ${S.check.side === "adverse" ? "selected" : ""}>as adverse party</option><option value="client" ${S.check.side === "client" ? "selected" : ""}>as prospective client</option></select>
        <button class="btn primary" ${ready ? "" : "disabled"}>Check</button></form>
        <div class="chips">${[["Quarry Lane Builders", "adverse"], ["Carver Freight Inc.", "client"], ["Lena Brandt", "adverse"], ["Westbrook Holdings", "client"], ["Oakmere Bank", "adverse"]].map(([q, s]) => `<button type="button" class="chip" data-q="${esc(q)}" data-side="${s}">${esc(q)} <span class="faint">· ${s === "client" ? "as client" : "as adverse"}</span></button>`).join("")}</div>
        <div id="check-result" class="result">${ready ? S.check.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    const form = st.querySelector("#check-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); checkParty($("#check-q").value.trim(), $("#check-side").value); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#check-q").value = c.dataset.q; $("#check-side").value = c.dataset.side; checkParty(c.dataset.q, c.dataset.side); }));
    }
  }
  async function checkParty(q, side) {
    if (!q) return; S.check.q = q; S.check.side = side;
    const box = $("#check-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> checking…</span>`;
    const r = await api("/api/check", { name: q, side });
    if (!r) { box.innerHTML = ""; return; }
    S.check.html = partyCard(r);
    if (document.body.contains(box)) box.innerHTML = S.check.html; else if (S.view === "check") renderStage();
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
  function resetView() { S.history = null; S.letters = null; S.matters = []; S.parties = []; S.intakes = {}; S.resolved = null; S.audit = null; S.waiting = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the firm history (facts, letters, conflicts), the 11 party actors and the uploaded letters from your account?")) return;
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
  let currentIntake = null;
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
    else if (k === "letters") { S.letters = d.letters; S.st.ids = { project: d.project.id }; }
    else if (k === "matter") { if (!S.matters.includes(d.record)) S.matters.push(d.record); }
    else if (k === "party") { if (!S.parties.find((p) => p.id === d.id)) S.parties.push(d); }
    else if (k === "history") S.history = d;
    else if (k === "intake") { currentIntake = d.key; S.intakes[d.key] = { def: d }; }
    else if (k === "party_check") { (S.intakes[d.key] ||= {}).party_check = d; S.waiting = { key: d.key }; }
    else if (k === "detector") { (S.intakes[d.key] ||= {}).detector = d; S.waiting = null; }
    else if (k === "resolved") S.resolved = d;
    else if (k === "audit") S.audit = d;
    else if (k === "done") {
      finishRunning("done"); S.waiting = null; term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the firm history, the parties and the letters are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Open Intake 3 for the conflicts, or try “Check a party”.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); S.waiting = null; term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "letters", "matter", "party", "history", "intake",
    "party_check", "detector", "detector_wait", "resolved", "audit", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
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
      const h = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") { Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; }); S.waiting = null; }
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.audit ? "i3" : "connect");
    subscribe();
  })();
})();

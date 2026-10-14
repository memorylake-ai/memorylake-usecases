/* Referral network memory for small medical practices — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, three sources", view: "setup" },
    { n: 3, title: "Intake", sub: "directory, referral log, notes", view: "intake" },
    { n: 4, title: "Quick read", sub: "the closest office note", view: "quick" },
    { n: 5, title: "Memory read", sub: "whose days were they?", view: "memory" },
    { n: 6, title: "Write it back", sub: "notes from the referral log", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", plan: "", within: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill ${t === "notes" ? "accent" : ""}">${esc(toolName(t))}</span>`;
  const fmt = (x) => Number(x).toFixed(1);
  function hitTable(hits, note) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · source</th></tr>
      ${hits.map((h) => `<tr class="${h.record === note ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.record === note ? '<span class="pill warn">closest note</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  // One bar per provider: its own days, ours, the patient's — the raw average is the whole bar.
  function bar(p, max) {
    const w = (v) => `${Math.max(0, (v / max) * 100)}%`;
    return `<div class="split" title="${fmt(p.theirs)} theirs + ${fmt(p.ours)} ours + ${fmt(p.patient)} patient">
      <span class="seg theirs" style="width:${w(p.theirs)}"></span><span class="seg ours" style="width:${w(p.ours)}"></span><span class="seg patient" style="width:${w(p.patient)}"></span></div>`;
  }
  function providerTable(rows, quick, best) {
    const max = Math.max(...rows.map((r) => r.raw), 1);
    return `<table class="iso"><tr><th>provider</th><th>n</th><th>raw</th><th style="min-width:180px">theirs · <span class="k ours">ours</span> · <span class="k patient">patient</span></th><th>mi</th><th>network</th><th>returned for</th></tr>
      ${rows.map((r) => `<tr class="${r.name === best ? "hl" : ""}"><td class="small"><b>${esc(r.name)}</b><br>${r.name === best ? '<span class="pill ok">memory read</span> ' : ""}${r.name === quick && quick !== best ? '<span class="pill err">the note</span>' : ""}</td>
        <td class="mono small">${esc(r.n)}</td><td class="mono small" style="white-space:nowrap">${fmt(r.raw)}</td>
        <td>${bar(r, max)}<span class="small mono">${fmt(r.theirs)} + ${fmt(r.ours)} + ${fmt(r.patient)}</span></td>
        <td class="mono small">${esc(r.miles)}</td><td>${r.in_network ? '<span class="pill">yes</span>' : '<span class="pill warn">no</span>'}</td>
        <td class="small">${Object.entries(r.reasons || {}).map(([k, v]) => `${esc(k)} ×${v}`).join("<br>") || '<span class="faint">—</span>'}</td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Refer<small>any referral · any provider's log</small></span></button>`;
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
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays Rosa's first morning on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about two minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“Bayside is slow.” Most of those days were ours.</h1>
      <p class="lead">Larkspur Family Medicine keeps its referral network in one MemoryLake project — the provider directory, the EHR referral log, and the office notes the last office manager left.
      Her notes say which providers are slow and where to send patients instead. The averages behind them are right. But a referral that comes back for missing paperwork and sits in the fax queue for a week counts against the provider in that average.
      The quick read follows the closest note. The memory read splits every referral's days by who was holding it — the provider, the practice, the patient — and two of three notes turn out to blame the wrong party.
      This page drives the <code>memorylake</code> CLI for real. The practice, providers, staff and patients are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/referral-network-memory-for-medical-practices" target="_blank" rel="noopener">memorylake.ai › Referral network memory for medical practices</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for the referral network");
    return `<h1>One project for the referral network</h1>
      <p class="lead">“Per-provider fact memory · reflection memory for provider quality · event memory for referral outcomes.” All three live in one project; each record keeps its source, kind and provider in metadata, and its own words in the text.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const notes = x.records.filter((r) => r.kind === "note");
    const dir = x.records.filter((r) => r.kind === "provider");
    const refs = x.records.filter((r) => r.kind === "referral").sort((a, b) => a.record.localeCompare(b.record));
    return `<h1>Intake — ${x.records.length} records from three sources</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each (source, kind, provider, specialty; for a note, which provider it is about and which it prefers).
      ${x.restored ? ` <span class="faint">(${x.restored} note(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">Dana's notes, as she left them</h2><table class="iso">
        ${notes.map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td class="small">${esc(r.body)}<br><span class="pill">${esc(r.specialty)}</span> <span class="pill accent">prefers ${esc(r.prefer)}</span></td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Directory · ${dir.length}</h2><table class="iso">
        ${dir.map((r) => `<tr><td class="small"><b>${esc(r.provider)}</b></td><td class="small">${esc(r.body.replace(/^Provider profile: [^-]*- /, ""))}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">EHR referral log · ${refs.length}</h2><table class="iso">
        ${refs.map((r) => `<tr class="${/Returned/.test(r.body) ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body.replace(/^REF-\d+ /, ""))}</td></tr>`).join("")}</table></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — follow the closest office note</h1>
      <p class="lead">What an assistant does with “referral decisions retrieve relevant provider history”: <code>search</code> the referral, take the closest office note — the reflection memory — and follow its advice.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.ticket.id)} · ${esc(q.ticket.plan)}${q.ticket.within ? ` · within ${esc(q.ticket.within)} days` : ""}</h2><span class="pill accent">→ ${esc(q.provider || "?")}</span></div>
        <p class="small">“${esc(q.ticket.text)}”</p>${hitTable(q.hits, q.note)}
        <p class="small" style="margin-bottom:0">${esc(q.note)}: <b>“${esc(q.note_body)}”</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} notes blamed the wrong party</h1>
      <p class="lead">Every provider in the note's specialty (directory), every referral to each (<code>fact list --query "&lt;provider&gt;"</code>, exact substring, every page), each referral's days split by who was holding it:
      the provider (sent → returned, resent → seen), <span class="k ours">the practice</span> (returned → resent), <span class="k patient">the patient</span> (a rescheduled appointment).
      Pick: in network for the plan, the provider's own days, then the nearest.</p>
      <div class="card"><table class="iso"><tr><th>referral</th><th>the note says</th><th>the referral log says</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.ticket.id)}</span> ${esc(r.ticket.text)}</td>
          <td class="small">${esc(r.quick)}</td><td class="small"><b>${esc(r.memory.provider)}</b></td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => { const a = r.providers.find((p) => p.name === r.about);
        return `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.ticket.id)} · ${esc(r.ticket.plan)}${r.ticket.within ? ` · within ${esc(r.ticket.within)} days` : ""}</h2>${agreePill(r.agree)}</div>
        <p class="small">“${esc(r.ticket.text)}”</p>${providerTable(r.providers, r.quick, r.memory.provider)}
        ${a ? `<p class="small">${a.ours >= 1 ? `The note's “${esc(a.name)} is slow” is the raw column (${fmt(a.raw)} days); <b>${fmt(a.ours)} of them were ours</b> — ${a.returned} of ${a.n} referrals came back for paperwork and waited with us.` : `The note's “${esc(a.name)} is slow” holds: ${fmt(a.theirs)} of its ${fmt(a.raw)} days are its own.`}</p>` : ""}
        <p class="small" style="margin-bottom:0"><b>${esc(r.memory.said)}</b></p></div>`; }).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">Each note that blamed the wrong party is rewritten from the referral log (<code>fact update --text</code>; the old text, the old advice and the figures go to metadata), and <code>fact trace</code> keeps both versions.
      Later the same day, three more referrals go through the <b>same quick-read code</b>, asked in new words (<code>search</code> answers a repeated question from its cache).</p>
      <div class="card mine"><h2 style="margin-top:0">Notes</h2><table class="iso"><tr><th>id</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small">${esc(u.record)}</td><td class="small muted">${esc(u.was)}<br><span class="pill err">prefers ${esc(u.was_prefer)}</span></td><td class="small"><b>${esc(u.now)}</b><br><span class="pill ok">prefers ${esc(u.now_prefer)}</span></td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace ${esc(x.updated[0]?.record)}</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">Later referrals, quick read again</h2><table class="iso"><tr><th>referral</th><th>closest note</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.ticket.id)}</span> · ${esc(a.ticket.plan)}<br>“${esc(a.ticket.text)}”</td><td class="mono small" style="white-space:nowrap">${esc(a.note)} <span class="faint">#${esc(a.note_rank)}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td class="small">${esc(a.quick)}</td><td class="small">${esc(a.memory)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, plans = story()?.plans || [];
    const provs = (story()?.records || []).filter((r) => r.kind === "provider").map((r) => r.provider);
    return `<h1>Refer</h1>
      <p class="lead">Describe any referral, pick the plan and, if it matters, the deadline: the quick read (closest note's advice) and the memory read (every provider's days, split by who held them) side by side.
      Or open one provider's referral log, split referral by referral.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Knee MRI after a sports injury" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-plan" ${ready ? "" : "disabled"} style="max-width:190px">${plans.map((p) => `<option ${p === a.plan ? "selected" : ""}>${esc(p)}</option>`).join("")}</select>
          <input id="ask-within" placeholder="within days" value="${esc(a.within)}" ${ready ? "" : "disabled"} style="max-width:110px"><button class="btn primary" ${ready ? "" : "disabled"}>Refer</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-plan="${esc(e.plan)}" data-within="${esc(e.within || "")}">${esc(e.q)} · ${esc(e.plan)}${e.within ? ` · ${esc(e.within)} d` : ""}</button>`).join("")}</div>
        <div class="chips">${provs.map((p) => `<button type="button" class="chip" data-provider="${esc(p)}">log: ${esc(p)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askRefer($("#ask-q").value.trim(), $("#ask-plan").value, $("#ask-within").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-plan").value = c.dataset.plan; $("#ask-within").value = c.dataset.within; askRefer(c.dataset.q, c.dataset.plan, c.dataset.within); }));
      st.querySelectorAll(".chips .chip[data-provider]").forEach((c) => c.addEventListener("click", () => askProvider(c.dataset.provider)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askRefer(q, plan, within) {
    if (!q) return; S.ask.q = q; S.ask.plan = plan; S.ask.within = within;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the referral log…</span>`;
    const r = await api("/api/refer", { q, plan, within: Number(within || 0) });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.ticket.text)}” · ${esc(r.ticket.plan)}${r.ticket.within ? ` · within ${esc(r.ticket.within)} days` : ""}</h2>${r.memory ? agreePill(r.agree) : ""}</div>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${esc(r.note || "no note in the top 8")}: “${esc(r.note_body)}”<br>→ <b>${esc(r.quick || "?")}</b></p></div>
        <div><h3>Memory read</h3><p class="small">${r.memory ? `<b>${esc(r.memory.said)}</b>` : "no note in the top 8, so no specialty"}</p></div></div>
      ${r.providers.length ? providerTable(r.providers, r.quick, r.memory?.provider) : ""}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.note)}</details>`);
  }
  async function askProvider(name) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/provider", { name });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2><code>fact list --query "${esc(r.name)}"</code></h2><span class="pill">${r.n} referral(s)</span></div>
      <table class="iso"><tr><th>id</th><th>record</th><th>days</th><th>theirs</th><th>ours</th><th>patient</th></tr>
      ${r.refs.map((x) => `<tr class="${x.ours ? "hl" : ""}"><td class="mono small">${esc(x.record)}</td><td class="small">${esc(x.body)}</td><td class="mono small">${x.total}</td><td class="mono small">${x.theirs}</td><td class="mono small">${x.ours}</td><td class="mono small">${x.patient}</td></tr>`).join("")}
      <tr><td></td><td class="small"><b>average</b></td><td class="mono small"><b>${fmt(r.raw)}</b></td><td class="mono small"><b>${fmt(r.theirs)}</b></td><td class="mono small"><b>${fmt(r.ours)}</b></td><td class="mono small"><b>${fmt(r.patient)}</b></td></tr></table>`);
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
    if (!confirm("Delete the demo's referral project and every record on it from your account?")) return;
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
      else if (S.caughtUp) banner("Done. Try a referral of your own — “Refer” in the sidebar.", true);
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

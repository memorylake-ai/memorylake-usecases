/* Curriculum memory for small education businesses — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project for Module 3", view: "setup" },
    { n: 3, title: "Intake", sub: "change log, cohorts, retro notes", view: "intake" },
    { n: 4, title: "Quick read", sub: "the closest retro note", view: "quick" },
    { n: 5, title: "Memory read", sub: "where the bundle came apart", view: "memory" },
    { n: 6, title: "Write it back", sub: "the notes from the cohorts", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", change: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill ${t === "retro" ? "accent" : ""}">${esc(toolName(t))}</span>`;
  const pct = (k, n) => n ? Math.round((100 * k) / n) : 0;
  const chName = (c) => story()?.changes?.[c] || c;
  const prefers = (p) => typeof p === "object" && p ? Object.entries(p).map(([k, v]) => `${chName(k)}: ${v}`).join(" · ") : p;
  const advPill = (a) => `<span class="pill ${a === "keep" ? "ok" : a === "can go" ? "warn" : "err"}">${esc(a)}</span>`;
  function hitTable(hits, note) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · source</th></tr>
      ${hits.map((h) => `<tr class="${h.record === note ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.record === note ? '<span class="pill warn">closest note</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  // One bar per group: the metric pooled over the group's cohorts; a group no cohort fell into says so.
  function cell(g) {
    if (!g.cohorts.length) return `<span class="pill err">no cohort</span>`;
    return `<div class="rate"><span class="fill" style="width:${pct(g.k, g.n)}%"></span></div><span class="mono small">${g.k}/${g.n} · ${pct(g.k, g.n)}%</span>`;
  }
  function bundleTable(tb, credited) {
    const one = (g) => tb.bundle.length > 1 && credited && g.key.every((on, i) => on === (tb.bundle[i] === credited));
    return `<table class="iso"><tr><th>which of the bundle ran</th><th>${esc(tb.label)}</th><th>cohorts</th></tr>
      ${tb.groups.map((g, i) => `<tr class="${one(g) ? "hl" : ""}"><td class="small"><b>${esc(g.name)}</b>${one(g) ? '<br><span class="pill warn">the change the note credits, on its own</span>' : ""}
        ${(i === 0 || i === tb.groups.length - 1) && tb.bundle.length > 1 ? '<br><span class="pill accent">in the note\'s before/after</span>' : ""}</td>
        <td style="min-width:150px">${cell(g)}</td><td class="mono small">${esc(g.cohorts.join(", ") || "—")}</td></tr>`).join("")}</table>`;
  }
  function verdictList(vs) {
    return `<ul class="small" style="margin:8px 0 0 18px;padding:0">${Object.entries(vs).map(([c, v]) => `<li><b>${esc(chName(c))}</b> ${advPill(v.advice)} — ${esc(v.why)}</li>`).join("")}</ul>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Plan<small>any question · any change</small></span></button>`;
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
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays Kofi's first planning session on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>“The visualizer fixed recursion.” It shipped with the lab.</h1>
      <p class="lead">Larkspur Code School keeps its Module 3 curriculum in one MemoryLake project — the change log, every cohort's results, and the retro notes the last curriculum lead left.
      Her note says the call-stack visualizer took the recursion checkpoint from 60% to 84%, so the Tuesday lab can go if TA hours are cut. The before/after is right — but the lab and the visualizer shipped together.
      The quick read follows the closest note. The memory read looks for the cohorts where the bundle came apart — and says “can't tell” where it never did.
      This page drives the <code>memorylake</code> CLI for real. The school and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/curriculum-memory-for-small-education-businesses" target="_blank" rel="noopener">memorylake.ai › Curriculum memory for small education businesses</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for the Module 3 curriculum");
    return `<h1>One project for the Module 3 curriculum</h1>
      <p class="lead">“Curriculum version memory · reflection memory · event memory for cohort performance.” All three live in one project; each cohort keeps the changes it ran and its three readings in metadata, and its own words in the text.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const by = (k) => x.records.filter((r) => r.kind === k);
    const cohorts = by("cohort").sort((a, b) => a.on.localeCompare(b.on));
    return `<h1>Intake — ${x.records.length} records from three sources</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each (which changes were live and the three readings; for a note, the bundle it is about and the change it credits).
      ${x.restored ? ` <span class="faint">(${x.restored} note(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">Ruth's retro notes, as she left them</h2><table class="iso">
        ${by("note").map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td class="small">${esc(r.body)}<br><span class="pill accent">${esc(prefers(r.prefer))}</span></td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Change log · ${by("change").length}</h2><table class="iso">
        ${by("change").map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Cohort results · ${cohorts.length}</h2><table class="iso"><tr><th>cohort</th><th>changes live</th><th>checkpoint</th><th>on time</th><th>withdrew</th></tr>
        ${cohorts.map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)} <span class="faint">· ${esc(r.track)} · ${esc(r.on)}</span></td>
          <td class="small">${(r.live || []).map((c) => `<span class="pill">${esc(c)}</span>`).join(" ") || '<span class="faint">none</span>'}</td>
          <td class="mono small">${r.checkpoint[0]}/${r.checkpoint[1]}</td><td class="mono small">${r.ontime[0]}/${r.ontime[1]}</td><td class="mono small">${r.withdrew[0]}/${r.withdrew[1]}</td></tr>`).join("")}</table></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — follow the closest retro note</h1>
      <p class="lead">What an assistant does with “reflection memory”: <code>search</code> the planning question, take the closest retro note on that change, follow its advice.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.ticket.id)} · ${esc(chName(q.ticket.change))}</h2>${advPill(q.advice || "?")}</div>
        <p class="small">“${esc(q.ticket.text)}”</p>${hitTable(q.hits, q.note)}
        <p class="small" style="margin-bottom:0">${esc(q.note)}: <b>“${esc(q.note_body)}”</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} answers change</h1>
      <p class="lead">Every cohort (<code>fact list</code>, every page), grouped by which of the note's bundle it ran. A change gets credit only from cohorts that ran it <b>without</b> the rest
      (keep it if that moved the rate by the gap or more); if no cohort ever did, the answer is <b>can't tell</b>.</p>
      <div class="card"><table class="iso"><tr><th>question</th><th>the note says</th><th>the cohorts say</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.ticket.id)}</span> ${esc(chName(r.ticket.change))}</td>
          <td>${advPill(r.quick)}</td><td>${advPill(r.memory.advice)}</td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.ticket.id)} · ${esc(r.note)} is about ${esc(r.table.bundle.map(chName).join(" + "))}</h2>${agreePill(r.agree)}</div>
        <p class="small">“${esc(r.ticket.text)}”</p>${bundleTable(r.table, S.intake?.records?.find((n) => n.record === r.note)?.credits)}
        ${verdictList(r.memory.verdicts)}</div>`).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">Each note that credited one change of a bundle is rewritten from the cohorts (<code>fact update --text</code>; the old text, the old advice and the grouped counts go to metadata), and <code>fact trace</code> keeps both versions.
      Later the same day, three more questions go through the <b>same quick-read code</b>, asked in new words (<code>search</code> answers a repeated question from its cache).</p>
      <div class="card mine"><h2 style="margin-top:0">Notes</h2><table class="iso"><tr><th>id</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small">${esc(u.record)}</td><td class="small muted">${esc(u.was)}<br><span class="pill err">${esc(prefers(u.was_prefer))}</span></td><td class="small"><b>${esc(u.now)}</b><br><span class="pill ok">${esc(prefers(u.now_prefer))}</span></td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace ${esc(x.updated[0]?.record)}</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">Later questions, quick read again</h2><table class="iso"><tr><th>question</th><th>closest note</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.ticket.id)}</span> · ${esc(chName(a.ticket.change))}<br>“${esc(a.ticket.text)}”</td><td class="mono small" style="white-space:nowrap">${esc(a.note)} <span class="faint">#${esc(a.note_rank)}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td>${advPill(a.quick)}</td><td>${advPill(a.memory)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, changes = Object.entries(story()?.changes || {});
    return `<h1>Plan</h1>
      <p class="lead">Ask any planning question and pick the change it is about: the quick read (closest note's advice) and the memory read (every cohort, grouped by the bundle that change shipped in) side by side.
      Or list every cohort by one change's bundle.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Can the lab go if TA hours are cut?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-ch" ${ready ? "" : "disabled"} style="max-width:220px">${changes.map(([k, v]) => `<option value="${esc(k)}" ${k === a.change ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Plan</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-change="${esc(e.change)}">${esc(e.q)}</button>`).join("")}</div>
        <div class="chips">${changes.map(([k, v]) => `<button type="button" class="chip" data-cohorts="${esc(k)}">cohorts by: ${esc(v)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askPlan($("#ask-q").value.trim(), $("#ask-ch").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-ch").value = c.dataset.change; askPlan(c.dataset.q, c.dataset.change); }));
      st.querySelectorAll(".chips .chip[data-cohorts]").forEach((c) => c.addEventListener("click", () => askCohorts(c.dataset.cohorts)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askPlan(q, change) {
    if (!q) return; S.ask.q = q; S.ask.change = change;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the cohorts…</span>`;
    const r = await api("/api/plan", { q, change });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.ticket.text)}” · ${esc(chName(change))}</h2>${r.memory ? agreePill(r.agree) : ""}</div>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${esc(r.note || "no note on this change in the top 8")}: “${esc(r.note_body)}”<br>→ ${advPill(r.quick || "?")}</p>
        ${r.before ? `<p class="small faint">Before a run rewrote ${esc(r.note)} it said: “${esc(r.before.text)}” → ${esc(r.before.advice)}${r.memory ? ` (${r.before.advice === r.memory.advice ? "✓ same" : "✗ differs"})` : ""}</p>` : ""}</div>
        <div><h3>Memory read</h3><p class="small">${r.memory ? `${advPill(r.memory.advice)} ${esc(r.memory.said.replace(/^[^—]*— /, ""))}` : "no note in the top 8, so no bundle to group by"}</p></div></div>
      ${r.table ? bundleTable(r.table, r.before ? "" : r.credits) + verdictList(r.memory.verdicts) : ""}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.note)}</details>`);
  }
  async function askCohorts(change) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/cohorts", { change });
    if (!r) { box.innerHTML = ""; return; }
    const m = r.table.metric;
    putResult(box, `<div class="row spread"><h2><code>fact list</code> · every cohort by ${esc(r.table.bundle.map(chName).join(" + "))}</h2><span class="pill">${r.cohorts.length} cohort(s)</span></div>
      ${bundleTable(r.table, "")}${verdictList(r.verdicts)}
      <details><summary class="small">the ${r.cohorts.length} cohorts</summary><table class="iso"><tr><th>cohort</th><th>track</th><th>${esc(r.table.label)}</th><th>live</th></tr>
      ${r.cohorts.map((c) => `<tr><td class="mono small">${esc(c.record)} <span class="faint">${esc(c.on)}</span></td><td class="small">${esc(c.track)}</td><td class="mono small">${c[m][0]}/${c[m][1]}</td><td class="small">${esc((c.live || []).join(", ") || "—")}</td></tr>`).join("")}</table></details>`);
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
    if (!confirm("Delete the demo's curriculum project and every record on it from your account?")) return;
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
      else if (S.caughtUp) banner("Done. Try a planning question of your own — “Plan” in the sidebar.", true);
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

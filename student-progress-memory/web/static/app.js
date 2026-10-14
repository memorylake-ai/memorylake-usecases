/* Student progress memory for tutoring businesses — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "curriculum + one project per student", view: "setup" },
    { n: 3, title: "Intake", sub: "skills, test, session notes", view: "intake" },
    { n: 4, title: "The quick read", sub: "search, then read the hits", view: "quick" },
    { n: 5, title: "The coverage map", sub: "every skill vs every check", view: "coverage" },
    { n: 6, title: "Write it back", sub: "the check · the gaps as facts", view: "written" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, coverage: null, written: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const kname = (k) => story()?.kinds?.[k] || k;
  const sname = (id) => (story()?.skills || []).find((s) => s.id === id)?.name || id;
  const stname = (k) => story()?.status_names?.[k] || k;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const statusPill = (s) => s === "secure" ? `<span class="pill ok">✓ secure</span>` : s === "shaky" ? `<span class="pill err">✗ shaky</span>`
    : `<span class="pill warn">? ${esc(stname(s))}</span>`;
  const quickPill = (v) => v === "concern" ? `<span class="pill err">✗ concern noted</span>` : v === "mentioned" ? `<span class="pill ok">· mentioned, no concern</span>`
    : `<span class="pill ok">· nothing on record</span>`;
  const why = (r) => r.status === "never" ? `no assessment on record${r.gap ? ` <span class="mono">(gap ${esc(r.gap)})</span>` : ""}`
    : r.status === "practiced" ? `practiced in ${r.practiced.map((p) => `<span class="mono">${esc(p.record)}</span> ${esc(p.on)}`).join(", ")}; no check on record`
    : `${esc(r.last.result)} in <span class="mono">${esc(r.last.record)}</span> ${esc(r.last.on)}${r.status === "stale" ? ` — <b>${r.age} days ago</b>` : ""}`;
  const tags = (r) => [...(r.assessed || []).map((a) => { const [s, v] = a.split("="); return `<span class="pill ${v === "secure" ? "ok" : "err"}">${esc(s)} ${esc(v)}</span>`; }),
    ...(r.practiced || []).map((s) => `<span class="pill">${esc(s)} practiced</span>`)].join(" ");

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>search both students · map by date</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, coverage, written, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again reads them back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about a minute and a half · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Nothing on record is not the same as nothing wrong.</h1>
      <p class="lead">Larkspur Learning keeps every tutoring session note in MemoryLake — word for word, with metadata on which skills it checked and which it only practiced.
      Priya's tutor left; Mara takes over today, two weeks before a Unit 6 test. Searching Priya's memory turns up the two weak spots her old tutor wrote down and no concern about the test.
      The coverage map — every skill on the list against every check on record — shows none of the three test skills was verified: one practiced but never checked, one last checked 176 days ago, one never touched.
      This page drives the <code>memorylake</code> CLI for real. The business, students and tutors are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the three projects and every record on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/student-progress-memory-for-tutoring-businesses" target="_blank" rel="noopener">memorylake.ai › Student progress memory for tutoring businesses</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("A curriculum project and one project per student");
    return `<h1>A curriculum project, and one project per student</h1>
      <p class="lead">The skill list and the test syllabus live on their own project; each student's session notes on the student's — the way a tutor's assistant is scoped to one student.</p>
      <div class="card"><table class="iso"><tr><th>project</th><th>holds</th><th>id</th></tr>
        <tr><td class="mono"><b>curriculum</b></td><td>the grade 8 skill list (${story()?.skills?.length || 12} skills) + the Unit 6 test syllabus</td><td class="mono small faint">${esc(su.projects.curriculum)}</td></tr>
        ${su.students.map((s) => `<tr><td class="mono"><b>${esc(s.key)}</b></td><td>${esc(s.full)}, grade ${s.grade} — tutor ${esc(s.tutor)} since ${esc(s.since)}</td>
          <td class="mono small faint">${esc(su.projects[s.key])}</td></tr>`).join("")}</table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const recs = x.records.filter((r) => r.student);
    const t = (story()?.tests || [])[0];
    return `<h1>Intake — every note, word for word, with what it checked</h1>
      <p class="lead">One <code>fact add --project</code> per project, then <code>fact update --metadata</code> on each record: who wrote it, when, which skills it <b>assessed</b> (secure / shaky) and which it only <b>practiced</b>.
      ${t ? `The ${esc(t.name)} on ${esc(t.date)} covers ${t.skills.map((s) => `<b>${esc(sname(s))}</b>`).join(", ")}.` : ""}</p>
      <div class="card"><table class="iso"><tr><th>student</th><th>id</th><th>date</th><th>kind · by</th><th>metadata</th><th>note</th></tr>
        ${recs.map((r) => `<tr class="${r.record === "P-H" ? "hl" : ""}"><td class="mono small"><b>${esc(r.student)}</b></td><td class="mono small">${esc(r.record)}</td><td class="small muted" style="white-space:nowrap">${esc(r.on)}</td>
          <td class="small">${esc(kname(r.kind))}<br><span class="faint">${esc(r.by)}</span></td><td class="small">${tags(r)}</td>
          <td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`;
  }

  function quickTable(q) {
    return `<table class="iso"><tr><th>test skill</th><th>what the top 8 say</th><th>quick read</th></tr>
      ${q.per.map((p) => `<tr><td><span class="mono">${esc(p.skill)}</span> ${esc(sname(p.skill))}</td>
        <td class="small">${p.said.length ? `<span class="mono">${esc(p.said[0].record)}</span> “${esc(p.said[0].sentence)}”` : `<span class="faint">0 of ${p.n_hits} hits mention it</span>`}</td>
        <td>${quickPill(p.verdict)}</td></tr>`).join("")}</table>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    const n = x.flagged.length;
    return `<h1>Handover day, the quick read — ${n} of ${x.per.length} test skills flagged</h1>
      <p class="lead">What a tutor's AI assistant scoped to Priya does: <code>search</code> her project — what does she struggle with, how is she doing with each skill on the coming test — and report the concerns the hits mention. Every hit is a real note. Nothing it says is false.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">“${esc(x.q)}”</h2>
        ${x.hits.slice(0, 4).map((h) => `<div class="small" style="margin-bottom:6px">#${h.rank} <span class="mono">${esc(h.record)}</span> <span class="faint">${esc(h.on)}</span> ${esc(h.body)}</div>`).join("")}
        <p class="small" style="margin-bottom:0">Weak spots the hits name: ${x.weak.map((w) => `<span class="pill err">${esc(w.skill)} ${esc(sname(w.skill))}</span>`).join(" ") || "none"}</p></div>
      <div class="card mine"><div class="row spread"><h2 style="margin:0">${esc(x.test.name)} · ${esc(x.test.date)}</h2><span class="pill ${n ? "err" : "ok"}">${n} of ${x.per.length} flagged</span></div>
        ${quickTable(x)}
        <p class="small" style="margin-bottom:0">Next session plan: <b>${x.plan.map((s) => esc(sname(s))).join(", ") || "nothing specific"}</b></p></div></div>`;
  }

  function mapTable(cov, onlyTest) {
    return `<table class="iso"><tr><th>skill</th><th>status</th><th>evidence</th></tr>
      ${cov.rows.filter((r) => !onlyTest || r.on_test).map((r) => `<tr class="${r.on_test && !onlyTest ? "hl" : ""}"><td><span class="mono">${esc(r.skill)}</span> ${esc(r.name)}${r.on_test && !onlyTest ? ' <span class="pill">on the test</span>' : ""}</td>
        <td>${statusPill(r.status)}</td><td class="small">${why(r)}</td></tr>`).join("")}</table>`;
  }
  const covPill = (cov) => { const u = cov.unverified.length, n = cov.test.skills.length; return `<span class="pill ${u ? "err" : "ok"}">${u} of ${n} test skills unverified</span>`; };

  function coverage() {
    const x = S.coverage; if (!x) return empty("The coverage map");
    const c = x.cov, l = x.control;
    return `<h1>The coverage map — ${c.unverified.length} of ${c.test.skills.length} test skills unverified</h1>
      <p class="lead">Every skill on the curriculum project against every assessment on Priya's project: <code>fact list</code> (every page) and metadata, plain code. A skill with no check on record is not “fine” — it is unknown. No note says “not assessed”, so no search hit could.</p>
      <div class="card mine"><div class="row spread"><h2 style="margin:0">Priya, as of ${esc(c.day)}</h2>${covPill(c)}</div>${mapTable(c, false)}
        <p class="small" style="margin-bottom:0">The quick read flagged ${S.quick ? S.quick.flagged.length : "?"} of ${c.test.skills.length}. It could not see: ${x.missed.map((s) => `<span class="pill warn">${esc(s)} ${esc(sname(s))}</span>`).join(" ")}</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Control — Leo, same tutor, same test</h2><span class="pill ${l.agree ? "ok" : "err"}">${l.agree ? "the two readings agree" : "the readings disagree"}</span></div>
        <p class="small">Leo's notes do cover the test. Both readings find the same thing — the map is not crying wolf.</p>
        <div class="cols"><div><h2>Quick read</h2>${quickTable(l.quick)}</div><div><h2>Coverage map</h2>${mapTable(l.cov, true)}</div></div></div>`;
  }

  function written() {
    const x = S.written; if (!x) return empty("Write it back");
    return `<h1>Write it back — the check on record, and the gap as a fact</h1>
      <p class="lead">Mara's 15-minute check is pinned like every other note, the secure result too. Every skill still never assessed gets a fact that says so (${x.gaps.map((g) => `<span class="mono">${esc(g.record)}</span>`).join(", ") || "none"}), so the next search about it finds the gap instead of other skills' notes.</p>
      <div class="cols"><div class="card mine"><div class="row spread"><h2 style="margin:0">Test skills, after the check</h2>${covPill(x.cov)}</div>${mapTable(x.cov, true)}
        <p class="small" style="margin-bottom:0">Next session plan: <b>${x.plan.map((s) => `${esc(s)} ${esc(sname(s))}`).join(", ")}</b></p></div>
      <div class="card"><h2 style="margin-top:0">Written</h2>${x.check.map((r) => `<div class="small" style="margin-bottom:8px"><span class="mono">${esc(r.record)}</span> ${tags(r)}<br>${esc(r.body)}</div>`).join("")}
        ${x.gaps.map((g) => `<div class="small"><span class="mono">${esc(g.record)}</span> <span class="pill warn">coverage gap</span><br>${esc(g.body)}</div>`).join("")}</div></div>
      ${x.probes.map((p) => `<div class="card"><h2 style="margin-top:0">“${esc(p.q)}”</h2>
        ${p.hits.slice(0, 3).map((h) => `<div class="small ${h.record === p.want ? "" : "faint"}">#${h.rank} <span class="mono">${esc(h.record)}</span> ${esc(h.body)}</div>`).join("")}
        <p class="small" style="margin-bottom:0">${p.rank ? `<span class="mono">${esc(p.want)}</span> is hit <b>#${p.rank}</b>` : `<span class="mono">${esc(p.want)}</span> is not in the top 8`}${p.tries > 1 ? ` <span class="faint">(reached the search index on try ${p.tries})</span>` : ""}</p></div>`).join("")}`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 across both students' projects, each hit labelled with its student. Or draw the coverage map for either student as of any date.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. How is Priya doing with systems of equations?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="chips">${[["priya", "2026-06-12"], ["priya", "2026-10-13"], ["priya", "2026-10-14"], ["leo", "2026-10-14"]].map(([s, d]) => `<button type="button" class="chip" data-student="${s}" data-day="${d}">${s === "priya" ? "Priya" : "Leo"}'s map as of ${d}</button>`).join("")}</div>
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
    st.querySelectorAll("[data-day]").forEach((b) => b.addEventListener("click", () => askMap(b.dataset.student, b.dataset.day)));
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
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length} across both students</h2><table class="iso"><tr><th>rank</th><th>student</th><th>hit</th></tr>
      ${r.hits.map((h) => `<tr><td class="mono small">#${h.rank}</td><td class="mono"><b>${esc(h.student)}</b></td><td class="small"><span class="mono">${esc(h.record || "?")}</span> <span class="faint">${esc(h.on)}</span> ${esc(h.body)}${h.kind === "gap" ? ` <span class="pill warn">coverage gap</span>` : ""}</td></tr>`).join("")}</table>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askMap(student, day) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/coverage", { student, day });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="row spread"><h2>${esc(r.name)}'s coverage map, as of ${esc(r.day)}</h2>${covPill(r)}</div>${mapTable(r, false)}`;
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.coverage = null; S.written = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's three projects and every record on them from your account?")) return;
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
    else if (k === "coverage") S.coverage = d;
    else if (k === "written") S.written = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the three projects and every record on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question of your own, or draw a map as of another date — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick_read", "coverage", "written",
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

/* Treatment plan memory for solo practitioners — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "the practice · the patient", view: "setup" },
    { n: 3, title: "The chart", sub: "3 plan versions · precautions · visits", view: "intake" },
    { n: 4, title: "Handoff, the usual way", sub: "follow the current plan", view: "handoff" },
    { n: 5, title: "Audit", sub: "every precaution, every version", view: "audited" },
    { n: 6, title: "Write the handoff", sub: "with the entries it rests on", view: "written" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, handoff: null, audited: null, written: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const pre = (pid) => story()?.precautions?.find((p) => p.pid === pid) || { label: pid };
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const VERDICT = { carried: ["ok", "✓ carried"], dropped: ["err", "✗ dropped"], lifted: ["", "· lifted"], stale: ["err", "✗ stale value"] };

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the chart<small>search · plans · audit by date</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, handoff, audited, written, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Patient chart</dt><dd>${st.ready ? "from an earlier run — Run again reads it back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1 minute · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The current plan is not the whole plan: two precautions fell out in a rewrite, and nobody lifted them.</h1>
      <p class="lead">Mira runs a solo physio practice. Jonah's first plan carried three precautions: no loaded overhead work until the orthopaedic review, no dry needling because he takes an anticoagulant, a pain ceiling of 3/10.
      Plans v2 and v3 were drafted by an AI scribe from the previous plan and that day's visit. Each rewrite read fine; together they dropped two precautions — and v3 now schedules overhead pressing and dry needling.
      Mira goes on leave tomorrow and Theo covers, following the current plan. Follow every precaution through every version since it was imposed and the memory says what the plan does not.
      This page drives the <code>memorylake</code> CLI for real. The practice, the clinicians and the patient are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the practice project, the patient and every fact.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/treatment-plan-memory-for-solo-practitioners" target="_blank" rel="noopener">memorylake.ai › Treatment plan memory for solo practitioners</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("The practice and the patient");
    const st = story();
    return `<h1>The practice and the patient</h1>
      <p class="lead">Jonah's chart lives on Jonah — an actor — so every tool that serves him reads the same memory. The practice project holds the covering handoff.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">Project</h2><p class="small" style="margin:6px 0"><b>${esc(st.project.name)}</b><br><span class="mono faint">${esc(su.ids.practice)}</span></p></div>
      <div class="card"><h2 style="margin-top:0">Patient (actor)</h2><p class="small" style="margin:6px 0"><b>${esc(st.patient.name)}</b><br><span class="mono faint">${esc(su.ids.jonah)}</span></p></div></div>`;
  }

  function kindPill(e) {
    if (e.pid) return `<span class="pill ${e.action === "impose" ? "err" : e.action === "revise" ? "accent" : ""}">${esc(e.pid)} ${esc(e.action)}</span>`;
    return `<span class="pill ${e.kind === "plan" ? "accent" : ""}">${esc(e.kind)}</span>`;
  }
  function intake() {
    const x = S.intake; if (!x) return empty("The chart");
    const c = x.counts || {};
    const tl = (x.timeline || []).map((e) => `<tr class="${e.kind === "plan" ? "mine" : ""}"><td class="mono small nowrap">${esc(e.date)}</td><td class="nowrap">${kindPill(e)}</td><td class="small">${esc(e.what)}</td></tr>`).join("");
    return `<h1>The chart — three plan versions, precautions, visits, pinned word for word</h1>
      <p class="lead">One <code>fact add --actor</code> with every entry, then <code>fact update --metadata</code> on each: <code>kind</code>, <code>date</code>, for plans the <code>version</code> and the precautions it carries, for precaution events the <code>pid</code> and <code>action</code> (impose · revise · reaffirm · lift). ${c.plan || 0} plan versions, ${c.precaution || 0} precaution entries, ${c.visit || 0} visit notes, ${c.handoff || 0} handoff note.</p>
      <div class="card"><h2 style="margin-top:0">Timeline</h2><table class="iso">${tl}</table></div>`;
  }

  function handoff() {
    const x = S.handoff; if (!x) return empty("Handoff, the usual way");
    const p = x.plan;
    return `<h1>Handoff, the usual way — follow the current plan</h1>
      <p class="lead">What a covering clinician gets: the newest plan version. Theo's first session is planned straight from it.</p>
      <div class="cols"><div class="card"><div class="row spread"><h2 style="margin:0">Current plan: v${p.version} (${esc(p.date)})</h2><span class="pill">${p.drafted_by === "scribe" ? "drafted by the AI scribe, signed" : "written by Mira"}</span></div>
        <p class="small">${esc(p.text)}</p>
        <p class="small" style="margin-bottom:0">Precautions it carries: ${p.precautions.map((k) => `<span class="pill">${esc(k)} ${esc(pre(k).label)}${k === "P3" && p.pain_ceiling ? " " + esc(p.pain_ceiling) : ""}</span>`).join(" ") || "none"}</p></div>
      <div class="card"><h2 style="margin-top:0">Theo's first session (${esc(story().first_cover_visit)})</h2>
        ${x.session.map((i) => `<div class="memfact"><span class="mk">✓</span><span class="body">${esc(i)}</span></div>`).join("")}</div></div>
      <div class="card"><h2 style="margin-top:0"><code>search</code> “${esc(x.ask.q)}” — top ${x.ask.hits.length}</h2>
        ${x.ask.hits.map((h) => `<div class="memfact"><span class="mk">${h.rank}</span><span class="body small">${esc(h.text)}</span></div>`).join("")}
        <p style="margin-bottom:0">→ The chart answers when someone thinks to ask. The plan Theo was told to follow gives him no reason to.</p></div>`;
  }

  function auditTable(a, items) {
    const rows = a.rows.map((r) => `<tr class="${r.verdict === "dropped" ? "mine" : ""}"><td><b>${esc(r.pid)}</b> ${esc(r.label)}${r.value ? ` <span class="muted">(${esc(r.value)})</span>` : ""}</td><td class="mono small">${esc(r.imposed)}</td>
      <td class="mono small">${r.carried.map((c) => `v${c.version}${c.carried ? "✓" : "✗"}`).join(" ")}</td><td class="small">${r.lift ? "yes " + esc(r.lift.date) : "never"}</td>
      <td><span class="pill ${VERDICT[r.verdict][0]}">${VERDICT[r.verdict][1]}</span></td></tr>`).join("");
    const draft = items && a.draft.length ? `<h2>Next session from plan v${a.plan.version}, checked against what is in force</h2>
      ${a.draft.map((d) => `<div class="memfact"><span class="mk">${d.blocked_by.length ? "✗" : "✓"}</span><span class="body">${esc(d.item)} ${d.blocked_by.length ? `<span class="pill err">hold — breaks ${esc(d.blocked_by.join(", "))}</span>` : ""}</span></div>`).join("")}` : "";
    return `<p class="small muted">Plan of the day: v${a.plan.version} (${esc(a.plan.date)}) · carries ${esc(a.plan.precautions.join(", ") || "none")}</p>
      <table class="iso"><tr><th>precaution</th><th>imposed</th><th>versions since</th><th>lifted?</th><th>verdict</th></tr>${rows}</table>${draft}`;
  }
  function audited() {
    const x = S.audited; if (!x) return empty("Audit");
    const a = x.audit;
    const why = a.rows.map((r) => r.verdict === "dropped"
      ? `<div class="memfact"><span class="mk">✗</span><span class="body"><b>${esc(r.pid)} ${esc(r.label)}</b>: imposed ${esc(r.imposed)}, never lifted. Fell out in plan v${r.dropped_in.version} (${esc(r.dropped_in.date)}, ${r.dropped_in.drafted_by === "scribe" ? "the AI scribe's draft" : "a rewrite"})${r.reaffirmed.length ? ` — and was repeated after that: ${r.reaffirmed.map((e) => `<span class="small">“${esc(e.text)}”</span>`).join(" ")}` : ""}</span></div>`
      : r.revisions.length ? `<div class="memfact"><span class="mk">✓</span><span class="body"><b>${esc(r.pid)} ${esc(r.label)}</b>: changed on the record ${esc(r.revisions.at(-1).date)} → ${esc(r.revisions.at(-1).value)}; plan v${a.plan.version} carries ${esc(a.plan.pain_ceiling)}</span></div>` : "").join("");
    const kw = x.keyword.map((k) => `<tr><td class="mono">“${esc(k.word)}”</td><td>${k.n} entries <span class="small muted">(${esc(k.kinds.join(", "))})</span></td><td>${k.in_current ? '<span class="pill">yes</span>' : '<span class="pill err">no</span>'}</td></tr>`).join("");
    return `<h1>Audit — follow every precaution through every version since it was imposed</h1>
      <p class="lead">One <code>fact list --actors &lt;Jonah&gt;</code>, every page. Plain code, no model: for each precaution, its impose / revise / reaffirm / lift events and every plan written since. In force = imposed and never lifted; the plan of the day must carry it.</p>
      <div class="card"><div class="row spread"><h2 style="margin:0">Control — the same audit on ${esc(x.control.day)}, the day plan v1 was written</h2><span class="pill ${x.control.carried === x.control.total ? "ok" : "err"}">${x.control.carried === x.control.total ? "✓" : "✗"} ${x.control.carried}/${x.control.total} carried</span></div>
        <p class="small muted" style="margin-bottom:0">The audit does not flag a plan that is right. On ${esc(x.v2.day)}, the day v2 was signed, it would already have said: ${esc(x.v2.missing.map((p) => p + " dropped").join(", ") || "nothing missing")}.</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Today (${esc(a.day)})</h2><span class="pill err">${a.missing.length} of ${a.rows.length} dropped · ${a.blocked} of ${a.draft.length} session items on hold</span></div>
        <div style="margin-top:10px">${auditTable(a, true)}</div>${why}</div>
      <div class="card"><h2 style="margin-top:0">Why a keyword check would not catch it — <code>fact list --query</code></h2>
        <table class="iso"><tr><th>word</th><th>entries in the chart</th><th>in plan v${a.plan.version}?</th></tr>${kw}</table>
        <p style="margin-bottom:0">→ “Does the current plan mention overhead?” Yes — it says <i>add overhead dumbbell press</i>. A mention is not a precaution; only the precaution's own record says whether it was lifted.</p></div>`;
  }

  function written() {
    const x = S.written; if (!x) return empty("Write the handoff");
    return `<h1>Write the handoff back — with the chart entries it rests on</h1>
      <p class="lead">One fact on the practice project, with the id of every chart entry it rests on in <code>metadata.basis</code>. Read back: <span class="pill ${x.cited === x.cited_total ? "ok" : "err"}">${x.cited === x.cited_total ? "✓" : "✗"} ${x.cited}/${x.cited_total} cited entries found</span></p>
      <div class="card"><div class="memfact"><span class="mk">→</span><span class="body"><b>${esc(x.handoff)}</b><div class="trace">${esc(x.id)}</div></span></div>
        ${x.sources.map((s) => `<div class="memfact"><span class="mk">←</span><span class="body small">${esc(s.text || "(not found)")}<div class="trace">${esc(s.id)}</div></span></div>`).join("")}</div>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the chart</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 over Jonah's chart, as it comes back. Or open any plan version, or run the audit as of any date.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Can Jonah press overhead?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Can Jonah press overhead?", "Is dry needling safe for Jonah?", "What is Jonah's pain limit during exercise?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="chips">${[1, 2, 3].map((v) => `<button type="button" class="chip" data-plan="${v}">plan v${v}</button>`).join("")}</div>
        <div class="chips">${["2026-08-04", "2026-08-25", "2026-09-29", story()?.today].map((d) => `<button type="button" class="chip" data-day="${esc(d)}">audit on ${esc(d)}</button>`).join("")}</div>
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
    st.querySelectorAll("[data-plan]").forEach((b) => b.addEventListener("click", () => askPlan(+b.dataset.plan)));
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
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length} as returned</h2><table class="iso"><tr><th>rank</th><th>entry</th><th>hit</th></tr>
      ${r.hits.map((h) => `<tr><td class="mono small">#${h.rank}</td><td class="small nowrap">${esc(h.kind || "")}</td><td class="small">${esc(h.text)}</td></tr>`).join("")}</table>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askPlan(version) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/plan", { version });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<h2>Plan v${r.version} (${esc(r.date)}) — ${r.drafted_by === "scribe" ? "drafted by the AI scribe, signed" : "written by Mira"}</h2><p class="small">${esc(r.text)}</p>
      <p class="small">Carries: ${(r.precautions || []).map((k) => `<span class="pill">${esc(k)} ${esc(pre(k).label)}</span>`).join(" ") || "none"}</p>
      <p class="small" style="margin-bottom:0">Next session: ${(r.next || []).map((i) => esc(i.item)).join(" · ")}</p>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askDay(day) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> auditing…</span>`;
    const r = await api("/api/audit", { day });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<h2>Audit as of ${esc(r.day)}</h2>${auditTable(r, true)}`;
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
  function resetView() { S.setup = null; S.intake = null; S.handoff = null; S.audited = null; S.written = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's practice project, the patient and every fact from your account?")) return;
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
    else if (k === "audited") S.audited = d;
    else if (k === "written") S.written = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the practice project, the patient and every fact are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question of your own, open a plan version or audit any date — “Ask the chart” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "handoff", "audited", "written",
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

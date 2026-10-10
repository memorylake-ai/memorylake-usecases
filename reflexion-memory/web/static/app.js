/* Reflexion memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => iso ? new Date(iso).toISOString().slice(0, 19).replace("T", " ") + "Z" : "";

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Agent + twin", sub: "agent create · agent fork", view: "setup" },
    { n: 3, title: "Five runs", sub: "conversations + reflections", view: "runs" },
    { n: 4, title: "What Tern remembers", sub: "agent vs project scope", view: "memory" },
    { n: 5, title: "Lessons disagree", sub: "fact conflict --agent", view: "detector" },
    { n: 6, title: "Today's failures", sub: "Tern vs the twin", view: "today" },
    { n: 7, title: "Skill + version 2", sub: "skill create · agent version", view: "skill" },
    { n: 8, title: "Audit trail", sub: "how Tern changed", view: "audit" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    setup: null, runs: {}, refl: {}, memory: null, detector: null, waiting: false, resolved: null, today: null, skill: null, audit: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Plan a failure<small>Tern vs the twin, from memory</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, runs, memory, detector, today, skill, audit, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const causePill = (c) => `<span class="pill ${c === "environment" ? "accent" : c === "code" ? "warn" : ""}">cause: ${esc(c)}</span>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Tern</dt><dd>${S.st.ids?.agent ? `<span class="mono muted">${esc(S.st.ids.agent)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>An agent that stops repeating its mistakes, because its reflections are in its own memory.</h1>
      <p class="lead"><b>Tern</b>, Kestrel Labs' CI triage agent, got five failures wrong. After each one it wrote a <b>typed reflection</b>
      (cause, adjustment, expected outcome) into <b>its own agent memory</b> (<code>fact add --agent</code>). MemoryLake flags the two lessons that contradict each other;
      today's planning step retrieves the right lesson, while a <b>twin made with <code>agent fork</code></b> (same prompt, no memory) repeats the mistake;
      a pattern across reflections becomes a <b>skill</b> and <b>agent version 2</b>. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the agents (memory first), the skill, the project and Dana.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-reflexion-style-self-improving-agents" target="_blank" rel="noopener">memorylake.ai › memory for Reflexion-style self-improving agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const s = S.setup; if (!s) return empty("Agent + twin");
    return `<h1>Tern gets a memory of its own; the twin is a fork</h1>
      <p class="lead"><code>agent create</code> makes Tern (version ${esc(s.version)}, with a system prompt) and gives it <b>its own actor</b>: facts written with
      <code>--agent</code> are Tern's memory, and <code>search --actors &lt;Tern's actor&gt;</code> searches it. <code>agent fork</code> copies Tern's configuration
      into a twin with a new id and actor, but <b>never its memory</b>. The runs themselves are conversations in the CI project, with Dana on the other side.</p>
      <div class="grid">
        <div class="card"><div class="row spread"><b>${esc(s.agent.name)}</b><span class="pill accent">agent v${esc(s.version)}</span></div>
          <div class="muted small">${esc(s.agent.system_prompt)}</div><div class="mono small faint">${esc(s.ids.agent)} · actor ${esc(s.ids.agent_actor)}</div></div>
        <div class="card"><div class="row spread"><b>${esc(s.twin.name)}</b><span class="pill">agent fork</span></div>
          <div class="muted small">same prompt and configuration; memory not copied</div><div class="mono small faint">${esc(s.ids.twin)} · actor ${esc(s.ids.twin_actor)}</div></div>
        <div class="card"><div class="row spread"><b>${esc(s.project)}</b><span class="pill">project</span></div>
          <div class="muted small">the runs, as conversations: the incidents land here</div><div class="mono small faint">${esc(s.ids.project)}</div></div>
        <div class="card"><div class="row spread"><b>${esc(s.human.name)}</b><span class="pill">actor</span></div>
          <div class="muted small">${esc(s.human.description)}</div><div class="mono small faint">${esc(s.ids.human)}</div></div></div>`;
  }

  function runs() {
    if (!story()) return "";
    const rows = story().runs.map((r) => {
      const ev = S.runs[r.run], x = S.refl[r.run];
      return `<div class="card"><div class="row spread"><b>${esc(r.run)} · ${esc(r.date)}</b>${ev ? `<span class="pill ${ev.appended ? "ok" : ""}">${ev.appended ? `${ev.appended} messages appended` : "recorded earlier"}</span>` : '<span class="pill">pending</span>'}</div>
        <div class="memfact small"><span><b>failure</b> ${esc(r.task)}</span></div>
        <div class="memfact small"><span><b>Tern did</b> ${esc(r.action)}</span></div>
        <div class="memfact small"><span><b>Dana, later</b> ${esc(r.outcome)}</span></div>
        ${x ? `<div class="memfact ${x.forgotten ? "gone" : ""}"><span>${causePill(x.cause)} <b>reflection</b> ${esc(x.adjustment)}<br><span class="small muted">expected: ${esc(x.expected)}</span></span>
          <span class="when">${x.forgotten ? "forgotten by an earlier resolution" : `<span class="mono small faint">${esc(x.id)}</span>`}</span></div>` : ""}</div>`;
    }).join("");
    return `<h1>Five runs Tern got wrong, and the reflection it wrote after each</h1>
      <p class="lead">Each run is a <b>DIRECT conversation</b> in the CI project: Dana reports the failure, Tern says what it did, Dana follows up with what really happened.
      Then comes the Reflexion <b>write</b> step: the reflection goes into Tern's memory verbatim (<code>fact add --agent</code>), typed with
      <code>fact update --metadata</code> (cause, adjustment, expected outcome, signals), ${S.data?.gap}s apart.</p>${rows}`;
  }

  function memory() {
    const m = S.memory; if (!m) return empty("What Tern remembers");
    return `<h1>Lessons on the agent, incidents on the project</h1>
      <p class="lead">Two scopes, one set of conversations: <code>fact list --agents</code> holds what Tern learned; <code>fact list --projects</code> holds what happened.
      The twin's memory is empty.</p>
      <div class="grid"><div class="card"><div class="row spread"><b>Tern's memory</b><span class="pill accent">${m.reflections.length} reflections</span></div>
        ${m.reflections.map((r) => `<div class="memfact small"><span>${causePill(r.cause)} <b>${esc(r.run)}</b> ${esc(r.adjustment)}</span></div>`).join("")}
        ${m.extracted.length ? `<p class="small muted">Also on Tern, extracted from its own turns (not relied on):</p>${m.extracted.map((f) => `<div class="memfact small faded"><span>${esc(f.text)}</span></div>`).join("")}` : ""}</div>
      <div class="card"><div class="row spread"><b>CI project</b><span class="pill">${m.project.length} facts</span></div>
        ${m.project.map((f) => `<div class="memfact small"><span>${esc(f.text)}</span></div>`).join("")}</div></div>
      <div class="card"><div class="row spread"><b>The twin's memory</b><span class="pill ${m.twin ? "warn" : "ok"}">${m.twin} facts</span></div></div>`;
  }

  function planCard(p, title) {
    if (!p) return "";
    const mark = p.expect ? `<span class="pill ${p.ok ? "ok" : "err"}">${p.ok ? "✓" : "✗"} top lesson ${esc(p.top || "—")} · expected ${esc(p.expect)}</span>` : "";
    return `<div class="card"><div class="row spread"><b>${title}</b>${mark}</div>
      ${p.hits.length ? p.hits.map((h) => `<div class="memfact small"><span><b>#${esc(h.rank)} ${esc(h.run)}</b> ${causePill(h.cause)} ${esc(h.adjustment)}</span></div>`).join("") : '<div class="memfact small"><span class="muted">no lesson in memory matches this failure</span></div>'}
      ${p.other ? `<div class="small faint">${p.other} other hit(s) left out: none of their signals is in the task</div>` : ""}
      <div class="small" style="margin-top:8px"><b>plan</b><ol style="margin:4px 0 0 18px;padding:0">${p.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol></div>
      <div style="margin-top:6px"><span class="pill ${p.verdict === "ask" ? "warn" : p.verdict === "lessons" ? "ok" : "err"}">${p.verdict === "ask" ? "ask first — two lessons disagree" : p.verdict === "lessons" ? "planned from its lessons" : "default playbook"}</span></div></div>`;
  }
  function conflictCard(c) {
    const rs = c.resolve || {};
    return `<div class="card conflict"><div class="row spread"><div><span class="pill err">${esc(c.category)} · ${esc(c.conflict_type)}</span> <b>${esc(c.name)}</b></div>
        ${c.resolved ? `<span class="pill ok">resolved · ${esc(rs.strategy || "")}</span>` : '<span class="pill warn">open</span>'}</div>
      <div class="mono small faint">${esc(c.id)}</div><p class="small">${esc(c.description)}</p>
      ${c.facts.map((f) => `<div class="memfact small"><span>“${esc(f.text)}”<br><span class="muted">${f.run ? `${esc(f.run)} lesson` : "extracted from a run conversation"}</span> <span class="mono faint">${esc(f.id)}</span></span></div>`).join("")}</div>`;
  }
  function detector() {
    const d = S.detector;
    if (!d && !S.waiting) return empty("Lessons disagree");
    const r = S.resolved;
    return `<h1>Two lessons disagree — MemoryLake flags it on the agent</h1>
      <p class="lead">r-001 taught Tern to retry an ECONNRESET once; r-005 taught it never to. MemoryLake checks every fact written to an agent against what that agent
      already holds and raises a conflict when the two <i>cannot both be true</i> (<code>fact conflict list --agent</code>). While it is open, Tern's plan for an
      ECONNRESET says <b>ask first</b>; <code>keep_fact</code> with r-005 as the evidence settles it, and the old lesson is forgotten.</p>
      ${S.waiting ? `<p><span class="pill busy"><span class="spinner"></span> waiting for the detector on Tern's memory (up to ${S.data.detect_wait}s)…</span></p>` : ""}
      ${d ? `<h2><code>fact conflict list --agent</code> — ${d.conflicts.length} conflict(s) ${d.fresh ? `<span class="muted small">after ${d.waited}s</span>` : '<span class="muted small">raised by an earlier run</span>'}</h2>
        ${d.conflicts.map(conflictCard).join("") || '<div class="card"><b>✗ nothing raised</b> within the wait — the detector sometimes answers minutes later, as a batch.</div>'}
        ${d.others.length ? `<h2>Other conflicts <span class="muted small">not planned by the story — shown, not hidden</span></h2>${d.others.map(conflictCard).join("")}` : ""}
        <div class="grid">${planCard(d.before, d.fresh ? "Plan for an ECONNRESET, conflict open" : "Plan for an ECONNRESET (settled by an earlier run)")}
          ${r && r.resolved.length ? planCard(r.after, "Same failure, after keep_fact") : ""}</div>` : ""}
      ${r && r.resolved.length ? `<div class="card">${r.resolved.map((x) => `<div class="memfact"><span><span class="mono small">${esc(x.id)}</span> → keep r-005<br><span class="small muted">${esc(x.why)}</span></span>
        <span class="when">forgotten: ${x.forgotten.map((f) => `<span class="mono">${esc(f)}</span>`).join(", ")}</span></div>`).join("")}</div>` : ""}`;
  }

  function today() {
    const t = S.today; if (!t) return empty("Today's failures");
    return `<h1>Today's failures — Tern plans from its lessons, the twin from nothing</h1>
      <p class="lead">Both agents run the same planning step: <code>search "&lt;the failure&gt;" --actors &lt;its actor&gt;</code>, keep the hits whose signals appear in the failure,
      and plan around them. The twin has the same prompt and configuration as Tern had on day one, and an empty memory.</p>
      ${t.tasks.map((x) => `<h2>${esc(x.task)}</h2><div class="grid">${planCard(x.tern, "Tern")}${planCard(x.twin, "Twin (agent fork, no memory)")}</div>`).join("")}`;
  }

  function skill() {
    const k = S.skill; if (!k) return empty("Skill + version 2");
    if (!k.skill) return `<h1>No skill yet</h1><div class="card">Fewer than ${story().skill.min_reflections} live reflections share a cause.</div>`;
    return `<h1>A pattern across reflections becomes a skill, and agent version 2</h1>
      <p class="lead">${k.lessons.length} live reflections share the cause <b>${esc(story().skill.cause)}</b> (${k.lessons.map((l) => esc(l.run)).join(", ")}), so they are written up as a
      <code>SKILL.md</code>, published with <code>skill create</code> and checked by MemoryLake's security review. Once it is <b>safe</b>, <code>agent version create --from-version latest --config</code>
      gives Tern version ${esc(k.versions.at(-1)?.version)} with the skill attached. Tern's memory is the same before and after: memory is not configuration.</p>
      <div class="grid"><div class="card"><div class="row spread"><b>${esc(k.skill.name)}</b><span class="pill ${k.skill.status === "safe" ? "ok" : "err"}">security review: ${esc(k.skill.status)}</span></div>
          <div class="mono small faint">${esc(k.skill.id)} · v${esc(k.skill.version)}</div></div>
        <div class="card"><b>Tern's memory</b><div class="small">${k.count_before} fact(s) before the new version · ${k.count_after} after</div>
          <div class="small muted">twin: version ${esc(k.twin.version)}, ${k.twin.skills} skill(s), no memory</div></div></div>
      <h2>Versions <span class="muted small">agent version list</span></h2>
      <div class="card"><table class="tour"><thead><tr><th>version</th><th>created</th><th>skills</th></tr></thead><tbody>
        ${k.versions.map((v) => `<tr><td><b>v${esc(v.version)}</b></td><td class="mono small">${fmtTime(v.created_at)}</td><td class="small">${v.skills.map((s) => `<span class="mono">${esc(s.skill_id)}</span> v${esc(s.skill_version)}`).join(", ") || '<span class="muted">none</span>'}</td></tr>`).join("")}</tbody></table></div>
      <h2>SKILL.md <span class="muted small">written from the reflections</span></h2><pre class="instr log">${esc(k.markdown)}</pre>`;
  }

  function audit() {
    const a = S.audit; if (!a) return empty("Audit trail");
    return `<h1>Audit trail — how Tern's behavior changed, and why</h1>
      <p class="lead">Every lesson's history from <code>fact trace</code> (including the one that was forgotten), the conflict and its resolution from <code>fact conflict get</code>,
      each skill version with its review, and each agent version, in the order MemoryLake recorded them (server time). Written to <code>${esc(a.file || "")}</code>.</p>
      <div class="card"><table class="tour"><thead><tr><th>when</th><th>what</th><th>detail</th></tr></thead><tbody>
        ${a.events.map((e) => `<tr><td class="mono small">${fmtTime(e.at)}</td><td class="small"><b>${esc(e.what)}</b></td><td class="small">${esc(e.detail)}<div class="mono faint">${esc(e.ref)}</div></td></tr>`).join("")}</tbody></table></div>
      ${a.open.length ? `<div class="card">Still open: ${a.open.map((x) => `<span class="mono">${esc(x)}</span>`).join(", ")}</div>` : ""}`;
  }

  function ask() {
    const ready = !!S.st.ids?.agent;
    return `<h1>Plan a failure from memory</h1>
      <p class="lead">Describe a CI failure. Tern and the twin each run the planning step against their own memory (<code>search --actors &lt;its actor&gt;</code>), side by side.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. test_invoice_pdf timed out in CI" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
        <button class="btn primary" ${ready ? "" : "disabled"}>Plan</button></form>
        <div class="chips">${["test_invoice_pdf timed out in CI again.", "The billing-api deploy failed its smoke check.", "test_refund_webhook failed with HTTP 503 from the payments sandbox.", "test_ledger_sync failed with ECONNRESET against the staging database.", "test_signup_email failed with an assertion error."].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>
      <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div>`;
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
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> planning…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="grid">${planCard(r.tern, "Tern")}${planCard(r.twin, "Twin (agent fork, no memory)")}</div>`;
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
  function resetView() { S.setup = null; S.runs = {}; S.refl = {}; S.memory = null; S.detector = null; S.waiting = false; S.resolved = null; S.today = null; S.skill = null; S.audit = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete Tern and its twin (their memory first), the skill, the CI project and Dana from your account?")) return;
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
    else if (k === "run") S.runs[d.run] = d;
    else if (k === "reflection") S.refl[d.run] = d;
    else if (k === "memory") S.memory = d;
    else if (k === "detector_wait") S.waiting = true;
    else if (k === "detector") { S.detector = d; S.waiting = false; }
    else if (k === "resolved") S.resolved = d;
    else if (k === "today") S.today = d;
    else if (k === "skill") S.skill = d;
    else if (k === "audit") S.audit = d;
    else if (k === "done") {
      finishRunning("done"); S.waiting = false; term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the agents, their memory, the skill, the project and Dana are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Open “Today's failures” for Tern vs the twin, or try “Plan a failure”.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); S.waiting = false; term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "setup", "run", "reflection", "memory", "detector_wait",
    "detector", "resolved", "today", "skill", "audit", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.today ? "today" : "connect");
    subscribe();
  })();
})();

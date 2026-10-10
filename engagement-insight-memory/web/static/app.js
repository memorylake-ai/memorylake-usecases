/* Engagement insight memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const initials = (n) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, one rule", view: "setup" },
    { n: 3, title: "The debrief", sub: "filed in both projects", view: "debrief" },
    { n: 4, title: "Two memories", sub: "same words · leak scan", view: "memories" },
    { n: 5, title: "The shortcut", sub: "fact add skips the rule", view: "shortcut" },
    { n: 6, title: "Next engagement", sub: "searches the library", view: "next" },
    { n: 7, title: "Provenance", sub: "lesson → engagement code", view: "provenance" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, setup: null, debrief: {}, memories: null, shortcut: null, next: null, provenance: null,
    ask: { q: "", scope: "library", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const nameOf = (key) => (story()?.consultants || []).find((c) => c.key === key)?.display || key;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>library vs engagement</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, debrief, memories, shortcut, next, provenance, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const leakLine = (r) => r.leaks?.length ? `<div class="leakwhy">↳ ${esc(r.leaks.join(", "))}</div>` : "";
  function fact(r, expectLeaks) {
    const bad = r.leaks?.length;
    const cls = bad ? (expectLeaks ? "" : "personal") : (expectLeaks ? "" : "clean");
    const mk = bad ? (expectLeaks ? "•" : "✗") : (expectLeaks ? "•" : "✓");
    return `<div class="memfact ${cls}"><span class="mk">${mk}</span><span class="body">${esc(r.fact)}${leakLine(r)}</span></div>`;
  }
  const tally = (rows, label) => {
    if (!rows.length) return `${label}: no facts, so nothing to check`;
    const bad = rows.filter((r) => r.leaks?.length).length;
    return `${label}: ${rows.length} fact(s) · ${bad} name a client or carry a figure`;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Library</dt><dd>${S.st.ids?.library ? `<span class="mono muted">${esc(S.st.ids.library)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>Lessons that outlive the engagement — without the client in them.</h1>
      <p class="lead"><b>Calder Lane Advisory</b> closes an engagement with a grocer. The close-out debrief is filed twice, word for word: in the engagement's
      own project (built-in default, keeps the client) and in the firm's <b>insight library</b>, whose <b>fact instruction</b> says “describe clients by sector and size, never by name; no exact figures”.
      A leak scan checks every library fact, catches a client figure pinned in by hand, and the next engagement's team searches the library only. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes both projects, the debriefs and both consultants.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/engagement-insight-memory-for-consulting-firms" target="_blank" rel="noopener">memorylake.ai › engagement insight memory for consulting firms</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!story()) return "";
    const su = S.setup, eng = S.data.engagement, lib = S.data.library;
    const people = story().consultants.map((c) => `<div class="card person"><span class="avatar user">${initials(c.display)}</span><div><div class="name">${esc(c.display)}</div><div class="role">${esc(c.role)}</div></div>
        <span class="status pill ${su ? "ok" : ""}">${su ? "actor ready" : "pending"}</span></div>`).join("");
    return `<h1>Two consultants, two projects, one rule</h1>
      <p class="lead">The engagement project is for the engagement team and stays on MemoryLake's built-in default: it should remember the client.
      The insight library is for the whole firm. Only it gets a <b>project fact instruction</b> — <code>fact instruction set --project</code> — that tells extraction to keep the lesson and drop the client.</p>
      <div class="grid">${people}</div>
      <div class="cols" style="margin-top:16px">
        <div class="card"><h2 style="margin-top:0">${esc(eng.name)}</h2><div class="muted small">custom id <code>${esc(eng.custom_id)}</code>${su ? ` · <span class="mono">${esc(su.engagement.id)}</span>` : ""}</div>
          <div class="mono small faint" style="margin-top:10px">$ memorylake fact instruction get --project …</div>
          ${su ? `<pre class="instr">${esc(su.engagement_instruction || '{ "fact_instruction": "" }')}</pre><p class="muted small" style="margin:0">Empty → the built-in default, which records the client like anything else.</p>` : '<div class="empty">pending</div>'}</div>
        <div class="card mine"><h2 style="margin-top:0">${esc(lib.name)}</h2><div class="muted small">custom id <code>${esc(lib.custom_id)}</code>${su ? ` · <span class="mono">${esc(su.library.id)}</span>` : ""}</div>
          <div class="mono small faint" style="margin-top:10px">$ memorylake fact instruction set --project … --file data/library-instruction.md</div>
          <pre class="instr">${esc(su?.instruction || S.data.instruction)}</pre>${su ? '<p class="muted small" style="margin:0">Read back with <code>fact instruction get</code>: saved word for word.</p>' : ""}</div></div>`;
  }

  function transcript(where) {
    const d = S.debrief[where] || {}; const turns = story().debrief.parts.flatMap((p) => p.turns); const stored = d.stored || 0;
    const status = d.cooked ? `<span class="pill ok">memory ready</span>` : stored >= turns.length ? `<span class="pill busy"><span class="spinner"></span> extracting…</span>`
      : stored ? `<span class="pill accent">${stored}/${turns.length}</span>` : `<span class="pill">queued</span>`;
    return `<div class="card session"><div class="session-head"><span class="chan">${where === "engagement" ? esc(S.data.engagement.name) : esc(S.data.library.name)}</span>
        <span style="margin-left:auto">${status}</span></div>
      <div class="bubbles">${turns.map(([who, line], i) => `<div class="bubble ${who === "priya" ? "user" : "bot"} ${i < stored ? "stored" : ""}"><span class="speaker">${esc(nameOf(who))}</span>${esc(line.replace(/^[^:]+:\s*/, ""))}${i < stored ? '<span class="tick">✓</span>' : ""}</div>`).join("")}</div></div>`;
  }
  function debrief() {
    if (!story()) return "";
    const refiled = S.debrief["library-refiled"];
    return `<h1>The close-out debrief — the same words, filed in both projects</h1>
      <p class="lead">One <code>GROUP</code> conversation per project, Priya and Tom as actors, the engagement code in metadata (<code>engagement=${esc(story().debrief.engagement)}</code>) and never in the text.
      It arrives in two parts, each cooked before the next, so every lesson can be traced to the part it came from.</p>
      ${refiled ? `<div class="card"><span class="pill warn">filed again</span> <span class="muted small">The library recorded nothing from the first copy (2 of 29 test runs did this; the cause is not known), so the debrief was filed once more in a new conversation.</span></div>` : ""}
      <div class="cols">${transcript("engagement")}${transcript("library")}</div>`;
  }

  function memories() {
    const m = S.memories; if (!m) return empty("Same words, two memories");
    const code = story().debrief.engagement;
    return `<h1>Same words, two memories</h1>
      <p class="lead"><code>fact list --projects</code> on each. The engagement project kept the client, its people and its numbers — that is its job.
      The library kept the lessons in sector terms. The <b>leak scan</b> checks every library fact against every engagement's identifiers and flags any figure.</p>
      <div class="cols">
        <div class="card"><h2 style="margin-top:0">${esc(code)} · built-in default</h2>${m.engagement.map((r) => fact(r, true)).join("") || '<p class="muted">nothing</p>'}
          <p class="tally muted">${esc(tally(m.engagement, "engagement"))} <span class="small">← expected: this project is the client's</span></p></div>
        <div class="card mine"><h2 style="margin-top:0">Insight library · its own instruction</h2>${m.library.map((r) => fact(r, false)).join("") || '<p class="muted">nothing extracted</p>'}
          <p class="tally"><span class="pill ${m.lib_leaky ? "err" : m.library.length ? "ok" : "warn"}">${esc(tally(m.library, "library"))}</span></p>
          ${m.removed.length ? `<p class="muted small">removed ${m.removed.length} flagged fact(s), the same fix as step 5</p>` : ""}</div></div>
      <p class="muted small">Also on the consultants' own actors: ${m.people.length} fact(s) (role, working preferences) · ${m.people.filter((r) => r.leaks.length).length} name a client.</p>`;
  }

  function shortcut() {
    const s = S.shortcut; if (!s) return empty("The shortcut");
    return `<h1>The shortcut — a client figure pinned straight into the library</h1>
      <p class="lead">${esc(s.by)} starts the next grocer engagement, wants the number handy and pins it with <code>fact add</code>.
      An instruction steers what <em>extraction</em> records. It does not filter writes.</p>
      <div class="card"><div class="mono small faint">$ memorylake fact add --project &lt;library&gt; "${esc(s.note_text)}"</div>
        <div class="memfact personal" style="margin-top:10px"><span class="mk">✗</span><span class="body">${esc(s.note_text)}${s.flagged.length ? leakLine(s.flagged.find((f) => f.id === s.fact_id) || s.flagged[0]) : ""}</span></div>
        <ul class="small"><li>stored ${s.verbatim ? "<b>word for word</b>" : "rewritten"} (<code>fact get</code>)</li>
          <li>search for the new team's first question puts it at <b>${s.rank ? "rank " + s.rank : "no rank in the top 5"}</b></li>
          <li>the leak scan ${s.caught ? "<b>caught it</b>" : "missed it"} → <code>fact delete</code></li></ul>
        <p class="tally"><span class="pill ${s.after.some((r) => r.leaks.length) ? "err" : "ok"}">after: ${esc(tally(s.after, "library"))}</span></p></div>`;
  }

  function next() {
    const n = S.next; if (!n) return empty("The next engagement");
    return `<h1>${esc(n.engagement.code)} — the next team searches the library</h1>
      <p class="lead">${esc(n.engagement.client)} is a different ${esc(n.engagement.sector)}. Its team runs <code>search --projects &lt;library&gt;</code> and nothing else.
      Hits are filtered by topic; search always returns its closest facts, so the rest are counted, not hidden.</p>
      ${n.answers.map((a) => `<div class="card"><div class="question"><b>Q:</b> ${esc(a.q)}</div>
        ${a.hits.map((h) => fact(h, false)).join("") || '<p class="muted small">no lesson on this topic in the library</p>'}
        ${a.left_out ? `<p class="muted small" style="margin:4px 0 0">${a.left_out} other hit(s) left out</p>` : ""}</div>`).join("")}
      <p><span class="pill ${n.clean ? "ok" : "err"}">${n.answers.reduce((x, a) => x + a.hits.length, 0)} lesson(s) · ${n.clean ? "none names a client or carries a figure" : "client identifiers present"}</span></p>
      <div class="card"><h2 style="margin-top:0">What the library is not</h2><p class="small" style="margin:0">The same first question in ${esc(story().debrief.engagement)}'s own project:
        <b>${n.contrast.named} of ${n.contrast.hits}</b> hits name the client or carry a figure${n.contrast.example ? ` — e.g. “${esc(n.contrast.example)}”` : ""}.
        The new team's searches never include it, because it is not in their <code>--projects</code>.</p></div>`;
  }

  function provenance() {
    const p = S.provenance; if (!p) return empty("Provenance");
    return `<h1>Every lesson traced to an engagement code, not a client</h1>
      <p class="lead"><code>fact trace</code> gives each lesson's <code>source_entry_ids</code>; <code>conv msg list</code> turns them into debrief messages and speakers; the conversation's
      metadata gives the engagement code. Written to <code>out/insight-provenance.md</code> and <code>.json</code>.</p>
      <div class="card">${p.rows.map((r) => `<div class="memfact ${r.leaks.length ? "personal" : "clean"}"><span class="mk">${r.leaks.length ? "✗" : "✓"}</span><span class="body">${esc(r.fact)}
        <div class="trace">→ ${esc(r.engagement)} · ${r.messages.length ? `debrief msgs ${r.messages[0]}–${r.messages[r.messages.length - 1]}` : "added by hand"}${r.part ? ` (${esc(r.part)})` : ""} · ${esc(r.speakers.join(", "))} · ${esc(r.kinds.join("/"))}</div></span></div>`).join("") || '<p class="muted">no lessons</p>'}</div>
      <p class="muted small">Projects scope search; they are not an access control — every API key on a team can read every project (see security-review-memory).</p>`;
  }

  function ask() {
    const ready = !!S.st.ids?.library;
    const opt = (v, l) => `<button type="button" class="chip scope ${S.ask.scope === v ? "on" : ""}" data-scope="${v}">${l}</button>`;
    return `<h1>Ask</h1>
      <p class="lead">The same search, scoped to the library or to the closed engagement's project. Every hit goes through the leak scan.</p>
      <div class="card"><div class="chips" style="margin:0 0 10px">${opt("library", "Insight library")}${opt("engagement", `${esc(story()?.debrief.engagement || "")} project`)}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. where to look first when a grocer's margin falls" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${["where to look first when a grocer's margin falls", "getting cost savings approved", "presenting to a head of stores", "who signed off", "how much did margin fall"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelectorAll(".chip.scope").forEach((b) => b.addEventListener("click", () => {
      S.ask.scope = b.dataset.scope; st.querySelectorAll(".chip.scope").forEach((x) => x.classList.toggle("on", x === b)); if (S.ask.q) askQuery(S.ask.q);
    }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip:not(.scope)").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, scope: S.ask.scope, top_k: 6 });
    if (!r) { box.innerHTML = ""; return; }
    const bad = r.facts.filter((f) => f.leaks.length).length;
    S.ask.html = `${r.facts.length ? r.facts.map((f) => fact(f, r.scope === "engagement")).join("") : '<p class="muted">No facts matched.</p>'}
      <p class="muted small">Search always returns its closest facts, related or not. ${r.scope === "library" ? "Library" : "Engagement project"}: ${bad} of ${r.facts.length} name a client or carry a figure.</p>`;
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
  function resetView() { S.setup = null; S.debrief = {}; S.memories = null; S.shortcut = null; S.next = null; S.provenance = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the debriefs, both projects (their memory and the library's instruction) and both consultants from your account?")) return;
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
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "existing") S.st.ids = d;
    else if (k === "setup") { S.setup = d; S.st.ids = { engagement: d.engagement.id, library: d.library.id }; }
    else if (k === "debrief") S.debrief[d.where] = { id: d.id, stored: d.done || 0, cooked: false };
    else if (k === "turn") { const w = d.where === "library-refiled" ? "library" : d.where; (S.debrief[w] ||= {}).stored = d.where === "library-refiled" ? Math.max(S.debrief[w].stored || 0, d.index) : d.index; }
    else if (k === "cooked") Object.values(S.debrief).forEach((s) => { if (s.id === d.id) s.cooked = true; });
    else if (k === "two_memories") S.memories = d;
    else if (k === "shortcut") S.shortcut = d;
    else if (k === "next_engagement") S.next = d;
    else if (k === "provenance") S.provenance = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — both projects, the debriefs and both consultants are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — the same question against the library and against the engagement project.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "setup", "debrief", "turn", "cooked", "two_memories", "shortcut", "next_engagement", "provenance", "done", "error"];
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
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.provenance ? "memories" : "connect");
    subscribe();
  })();
})();

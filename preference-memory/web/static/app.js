/* Preference memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  const initials = (n) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "Priya, assistant, project", view: "setup" },
    { n: 3, title: "Session 1", sub: "Claude · built-in default", view: "s1" },
    { n: 4, title: "What it kept", sub: "session 1's receipt", view: "kept" },
    { n: 5, title: "Her instruction", sub: "what may be recorded", view: "instruction" },
    { n: 6, title: "Session 2", sub: "ChatGPT · her instruction", view: "s2" },
    { n: 7, title: "Every model", sub: "one block, three APIs", view: "everywhere" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, setup: null, sessions: {}, kept: null, instruction: null, s2: null, everywhere: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const labelOf = (key) => (story()?.excluded || []).find((c) => c.key === key)?.label || key;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask her memory<small>scoped search</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() { const r = { connect, setup, s1: () => session("s1"), kept, instruction, s2: () => session("s2"), everywhere, ask }[S.view]; $("#stage").innerHTML = r ? r() : ""; bindStage(); }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Priya</dt><dd>${S.st.ids?.person ? `<span class="mono muted">${esc(S.st.ids.person)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
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
    return `<h1>One memory of how you work — in every AI tool, and only what you allow.</h1>
      <p class="lead"><b>Priya Raman</b> works with an AI assistant in Claude one day and ChatGPT the next. Both sessions write to the same memory: her actor.
      On the built-in default it also keeps her family, diet and surgery, so she writes her own <b>fact instruction</b> for what it may record. The next session records her
      new work rules and none of her new personal news, and the profile goes byte for byte into a Claude, an OpenAI and a Gemini request. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes Priya, her memory and her instruction.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-that-remembers-your-preferences" target="_blank" rel="noopener">memorylake.ai › the AI that actually knows you</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!story()) return "";
    const p = story().person, a = story().assistant, su = S.setup;
    return `<h1>Priya, her assistant, and a project for her sessions</h1>
      <p class="lead">Priya is a <code>HUMAN</code> actor: her memory lives on her, not on a tool. The assistant is a second actor, and a project holds the sessions
      (every conversation needs one). Her <b>fact instruction</b> starts empty — that means MemoryLake's built-in default decides what gets recorded.</p>
      <div class="grid">
        <div class="card person"><span class="avatar user">${initials(p.display)}</span><div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.role)} · HUMAN</div></div>
          <span class="status pill ${su ? "ok" : ""}">${su ? "actor ready" : "pending"}</span></div>
        <div class="card person"><span class="avatar bot">AI</span><div><div class="name">${esc(a.display)}</div><div class="role">${esc(a.role)}</div></div>
          <span class="status pill ${su ? "ok" : ""}">${su ? "actor ready" : "pending"}</span></div></div>
      <h2>Sessions project</h2>
      <div class="card row spread"><div><b>${esc(S.data.project.name)}</b><div class="muted small">custom id <code>${esc(S.data.project.custom_id)}</code>${su ? ` · <span class="mono">${esc(su.project.id)}</span>` : ""}</div></div>
        <span class="pill ${su ? "ok" : ""}">${su ? "ready" : "pending"}</span></div>
      <h2>Her fact instruction today</h2>
      <div class="card"><div class="mono small faint">$ memorylake fact instruction get --actor ${esc(su?.person?.id || "…")}</div>
        ${su ? (su.instruction ? `<pre class="instr">${esc(su.instruction)}</pre><p class="muted small">Already set by an earlier run.</p>` : `<pre class="instr">{ "fact_instruction": "" }</pre><p class="muted small" style="margin:0">Empty → the built-in default.</p>`) : '<div class="empty">pending</div>'}</div>
      ${su?.draft ? `<h2>What the default records — in MemoryLake's words</h2><div class="card"><div class="mono small faint">$ memorylake fact instruction draft --actor ${esc(su.person.id)}   # nothing is saved</div><pre class="instr">${esc(su.draft)}</pre></div>` : ""}`;
  }

  function session(key) {
    const s = (story()?.sessions || []).find((x) => x.key === key); if (!s) return "";
    const st = S.sessions[key] || {}; const stored = st.stored || 0; const total = s.turns.length;
    const status = st.cooked ? `<span class="pill ok">memory ready</span>` : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting…</span>`
      : stored ? `<span class="pill accent">${stored}/${total}</span>` : `<span class="pill">queued</span>`;
    const lead = key === "s1"
      ? `One <code>DIRECT</code> conversation, the tool in metadata (<code>tool=${esc(s.tool)}</code>), Priya and the assistant as actors. The instruction is still the built-in default.`
      : `A new tool, the same Priya. Her instruction is now in force. She brings new work rules, changes her answer length, and shares two pieces of personal news.`;
    const extra = key === "s2" ? s2Receipt() : "";
    return `<h1>Session ${key === "s1" ? 1 : 2} in ${esc(s.tool)} — ${esc(s.title)}</h1><p class="lead">${lead}</p>
      <div class="card session"><div class="session-head"><span class="chan">${esc(s.tool)}</span><span class="muted small">${fmtDate(s.date)}</span><span style="margin-left:auto">${status}</span></div>
      <div class="bubbles">${s.turns.map(([who, line], i) => `<div class="bubble ${who === "user" ? "user" : "bot"} ${i < stored ? "stored" : ""}">${esc(line)}${i < stored ? '<span class="tick">✓</span>' : ""}</div>`).join("")}</div></div>${extra}`;
  }

  const scopeTag = (r) => r.scope === "Priya" ? "" : ' <span class="pill">on the project</span>';
  function kept() {
    const k = S.kept; if (!k) return empty("What the default kept");
    return `<h1>What the built-in default kept from session 1</h1>
      <p class="lead"><code>conv fact-actions</code> is a receipt: every fact this one conversation added or changed, on Priya and on the project.
      Alongside her working preferences, the default kept <b>${k.personal}</b> about her family, home, health or diet — it records “identity and background, current life situation”.</p>
      <div class="card">${k.rows.map((r) => `<div class="memfact ${r.category ? "personal" : ""}"><span><span class="ev">${esc(r.event)}</span> ${esc(r.fact)}${scopeTag(r)}</span>
        <span class="when">${r.category ? `<span class="pill err">personal · ${esc(labelOf(r.category))}</span>` : '<span class="pill">work</span>'}</span></div>`).join("")}</div>`;
  }

  function instruction() {
    const i = S.instruction; if (!i) return empty("Her instruction");
    return `<h1>She writes what her memory may record</h1>
      <p class="lead">The draft from step 2 describes the default: a person's identity, background and current situation. Priya rewrites it as hers and saves it with
      <code>fact instruction set --file</code> — on her actor and on the sessions project. An instruction steers what is recorded <em>from now on</em>, so she also removes what the default already kept.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">MemoryLake's draft (step 2)</h2><pre class="instr">${esc(i.draft)}</pre><div class="mono small faint">fact instruction draft --actor …</div></div>
        <div class="card mine"><h2 style="margin-top:0">Hers — <code>data/instruction.md</code></h2><pre class="instr">${esc(i.mine)}</pre><div class="mono small faint">fact instruction set --actor … --file data/instruction.md</div></div></div>
      <h2>Removed — what the default had kept</h2>
      <div class="card">${i.deleted.length ? i.deleted.map((d) => `<div class="memfact gone"><span>${esc(d.fact)}</span><span class="when">fact delete</span></div>`).join("") : '<p class="muted small" style="margin:0">Nothing removed in this pass (session 2 had already run).</p>'}</div>`;
  }

  function s2Receipt() {
    const r = S.s2; if (!r) return "";
    return `<h2>The receipt — <code>conv fact-actions</code></h2>
      <div class="card">${r.rows.map((x) => `<div class="memfact ${x.category ? "personal" : ""}"><span><span class="ev">${esc(x.event)}</span> ${esc(x.fact)}${scopeTag(x)}
        ${x.event === "UPDATE" && x.old ? `<div class="was">was: ${esc(x.old)}</div>` : ""}</span></div>`).join("") || '<p class="muted">nothing recorded</p>'}</div>
      <h2>She said it in this session, so was it recorded? <span class="pill ${r.ok === r.total ? "ok" : "warn"}">${r.ok} of ${r.total} as intended</span></h2>
      <div class="card checks">${r.checks.map((c) => `<div class="check ${c.ok ? "ok" : "bad"}"><span class="mark">${c.ok ? "✓" : "✗"}</span>
        <span><b>${esc(c.label)}</b> <span class="muted small">${c.kind === "excluded" ? (c.ok ? "excluded → 0 facts recorded" : `excluded → ${c.facts.length} recorded`) : (c.ok ? "recorded" : "not recorded")}</span>
        ${!c.ok && c.facts.length ? c.facts.map((f) => `<div class="was">${esc(f)}</div>`).join("") : ""}</span></div>`).join("")}</div>`;
  }

  function everywhere() {
    const e = S.everywhere; if (!e) return empty("Every model");
    return `<h1>One memory, the same bytes in every model</h1>
      <p class="lead">Her profile is read once — <code>fact list --actors &lt;Priya&gt;</code>, ${e.facts} facts — and becomes one block. That block goes into the system slot of a
      Claude, an OpenAI and a Gemini request body. Read back from the files on disk, the block hashes the same in all three.</p>
      <div class="block">${esc(e.block).replace(/(&lt;\/?background-memory[^&]*&gt;)/g, '<span class="tag">$1</span>')}</div>
      <div class="grid3">${e.payloads.map((p) => `<div class="card payload"><div class="name">${esc(p.label)}</div><div class="mono small">${esc(p.file)}</div>
        <div class="mono small sha">sha256 ${esc(p.sha.slice(0, 16))}…</div></div>`).join("")}</div>
      <p><span class="pill ${e.same ? "ok" : "err"}">${e.same ? "identical in all three ✓" : "the blocks differ"}</span>
      <span class="muted small">First message in each request: “${esc(e.ask)}”</span></p>`;
  }

  function ask() {
    const ready = !!S.st.ids?.person;
    return `<h1>Ask her memory</h1>
      <p class="lead">What any tool would run at the start of a turn: a search scoped to Priya's actor. Try asking about something she excluded.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. how should numbers be shown" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${["how long should answers be", "how should numbers be shown", "what is her top priority this quarter", "her family", "her health"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  function personalNote(facts) {
    // The same marker words demo.py uses to label a fact as family / health / diet.
    const hit = (t, m) => new RegExp(`(?<![A-Za-z])${m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![A-Za-z])`, "i").test(t || "");
    const cats = story()?.excluded || [];
    const n = facts.filter((f) => cats.some((c) => c.markers.some((m) => hit(f.fact, m)))).length;
    return n ? `<b>${n}</b> of these ${n === 1 ? "is" : "are"} about her family, home, health or diet.` : "None of these is about her family, home, health or diet.";
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, top_k: 6 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `${r.facts.length ? r.facts.map((f) => `<div class="memfact"><span>${esc(f.fact)}</span><span class="when">${(f.score ?? 0).toFixed(2)}</span></div>`).join("") : '<p class="muted">No facts matched.</p>'}
      <p class="muted small">Search always returns its closest facts, related or not. ${personalNote(r.facts)}</p>`;
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
  function resetView() { S.setup = null; S.sessions = {}; S.kept = null; S.instruction = null; S.s2 = null; S.everywhere = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo sessions, the project, Priya (her facts and her instruction) and the assistant from your account?")) return;
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
    else if (k === "setup") { S.setup = d; S.st.ids = { person: d.person.id, project: d.project.id }; }
    else if (k === "session") S.sessions[d.key] = { id: d.id, stored: d.done || 0, cooked: false };
    else if (k === "turn") (S.sessions[d.key] ||= {}).stored = d.index;
    else if (k === "cooked") Object.values(S.sessions).forEach((s) => { if (s.id === d.id) s.cooked = true; });
    else if (k === "default_profile") S.kept = d;
    else if (k === "instruction") S.instruction = d;
    else if (k === "session2") S.s2 = d;
    else if (k === "everywhere") S.everywhere = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — Priya, her memory and her instruction are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask her memory” — or open Session 2 for the receipt.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "setup", "session", "turn", "cooked", "default_profile", "instruction", "session2", "everywhere", "done", "error"];
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
    show(state.status === "running" ? S.view : S.everywhere ? "s2" : "connect");
    subscribe();
  })();
})();

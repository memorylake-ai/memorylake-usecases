/* ChatGPT API memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const initials = (n) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const first = (n) => n.split(/\s+/)[0];

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Learners", sub: "project + actor + boundary", view: "setup" },
    { n: 3, title: "Past sessions", sub: "every chat is stored", view: "sessions" },
    { n: 4, title: "Boundary memory", sub: "what a model may read", view: "memory" },
    { n: 5, title: "Same call ×3", sub: "no memory vs. a boundary", view: "calls" },
    { n: 6, title: "Token bill", sub: "pasted history vs. boundary", view: "bill" },
    { n: 7, title: "Switch models", sub: "same boundary, Claude", view: "models" },
    { n: 8, title: "Next session", sub: "piped in, known next call", view: "next" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, learners: {}, sessions: {}, memory: {}, calls: {}, bill: null, wb: null, unavailable: null, summary: null,
    ask: { who: "mina", model: "gpt-4o-mini", q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const L = (key) => (S.data?.learners || []).find((l) => l.key === key);

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>one router call</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() { const r = { connect, setup, sessions, memory, calls, bill, models, next, ask }[S.view]; $("#stage").innerHTML = r ? r() : ""; bindStage(); }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Learners</dt><dd>${Object.keys(S.learners).length ? Object.keys(S.learners).map((k) => esc(L(k)?.display)).join(", ") : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${Object.keys(S.learners).length ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>mlu-cgm-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Every ChatGPT API call starts blank. Add one argument and it doesn't.</h1>
      <p class="lead"><b>Lingofox</b> is a language tutor built on the chat API. Two learners have used it for a month. Each has a <b>boundary</b> — a saved scope over
      their own memory — and the app's call gains one flag: <code>--boundary</code>. MemoryRouter retrieves what matters for this message and puts it in front of the model.
      This page measures the reply, the prompt tokens, and what leaks (nothing should). It drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about ten calls on the smallest models per run. The memory steps cost nothing.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/cross-session-context-for-the-chatgpt-api" target="_blank" rel="noopener">memorylake.ai › cross-session context for the ChatGPT API</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    return `<h1>Per learner: a project, an actor, and a boundary over both</h1>
      <p class="lead">The sessions go to the learner's project; the learner is an actor (your own user id as <code>custom_id</code>). The <b>boundary</b> names exactly those two —
      it is the only memory a model called with it can read.</p>
      <div class="grid">${S.data.learners.map((l) => { const w = S.learners[l.key]; return `<div class="card"><div class="user-head"><span class="avatar user">${initials(l.display)}</span><h2>${esc(l.display)}</h2><span class="chan">${esc(l.language)}</span></div>
        <dl class="kv"><dt>Actor</dt><dd class="mono small">${esc(w?.actor || "pending")}</dd><dt>Project</dt><dd class="mono small">${esc(w?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small">${w ? `<b>${esc(w.boundary)}</b>` : "pending"}</dd></dl>
        <div class="cmdline">boundary create --projects ${esc(w?.project || "…")} --human-actor ${esc(w?.actor || "…")}</div></div>`; }).join("")}</div>`;
  }

  function sessionCard(l, s) {
    const st = S.sessions[s.custom_id] || {}; const stored = st.stored || 0; const total = s.turns.length;
    const status = st.cooked ? `<span class="pill ok">memory ready</span>` : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting…</span>`
      : stored ? `<span class="pill accent">${stored}/${total}</span>` : `<span class="pill">queued</span>`;
    return `<div class="card session"><div class="session-head"><h3>${esc(s.title)}</h3><span class="muted small">${esc(s.date.slice(0, 10))}</span><span style="margin-left:auto">${status}</span></div>
      <div class="bubbles">${s.turns.map(([who, text], i) => `<div class="bubble ${who === "user" ? "user" : "bot"} ${i < stored ? "stored" : ""}">${esc(text)}</div>`).join("")}</div></div>`;
  }
  function sessions() {
    if (!S.data) return "";
    return `<h1>Every past session is stored as a conversation</h1>
      <p class="lead">The app writes each turn as it happens (<code>conv msg append</code>); MemoryLake extracts facts in the background. The details the tutor must never lose
      are also pinned verbatim with <code>fact add</code>, because extraction wording varies from run to run.</p>
      <div class="cols">${S.data.learners.map((l) => `<div><div class="user-head"><span class="avatar user">${initials(l.display)}</span><h2>${esc(l.display)}</h2></div>
        ${l.sessions.map((s) => sessionCard(l, s)).join("")}</div>`).join("")}</div>`;
  }

  function memory() {
    if (!Object.keys(S.memory).length) return empty("What each boundary holds");
    return `<h1>What each boundary holds</h1>
      <p class="lead"><code>fact list --actors &lt;learner&gt;</code> and <code>--projects &lt;sessions&gt;</code>. Some facts come back in Japanese — the extractor's language is not
      controlled — which is why the essentials are pinned.</p>
      <div class="cols">${S.data.learners.map((l) => { const m = S.memory[l.key]; if (!m) return ""; return `<div class="card"><div class="user-head"><span class="avatar user">${initials(l.display)}</span><h2>${esc(l.display)}</h2>
        <span class="pill accent">${m.actor.length} on learner</span><span class="pill">${m.project.length} on project</span></div>
        <div class="factlist">${m.actor.map((f) => `<div class="memfact"><span>${esc(f)}</span><span class="when">learner</span></div>`).join("")}
        ${m.project.map((f) => `<div class="memfact"><span>${esc(f)}</span><span class="when">project</span></div>`).join("")}</div></div>`; }).join("")}</div>`;
  }

  function markList(list, cls) { return (list || []).map((r) => `<span class="mark ${r.ok ? "ok" : cls || "no"}">${r.ok ? "✓" : "✗"} ${esc(r.label)}</span>`).join(""); }
  function callCard(title, c, opts = {}) {
    if (!c) return "";
    const checks = Object.entries(c.checks || {}).map(([k, v]) => `<div class="marks"><span class="muted small">${esc(first(L(k)?.display || k))}:</span>${markList(v)}</div>`).join("");
    const leaks = c.leaks ? `<div class="marks"><span class="muted small">other learner's details:</span>${c.leaks.length ? c.leaks.map((w) => `<span class="mark bad">✗ ${esc(w)}</span>`).join("") : '<span class="mark ok">none — isolated ✓</span>'}</div>` : "";
    return `<div class="card"><div class="callhead"><h3 style="margin:0">${title}</h3><span class="tok">${c.prompt_tokens} prompt tokens</span><span class="muted small">${esc(c.model)}</span></div>
      ${opts.cmd ? `<div class="cmdline">${esc(opts.cmd)}</div>` : ""}<div class="reply">${esc(c.reply)}</div>${checks}${leaks}</div>`;
  }
  function calls() {
    if (!S.calls.none) return unavailable() || empty("Same API call, three ways");
    const q = S.data.today_prompt;
    return `<h1>Same API call, three ways</h1>
      <p class="lead">Message: <b>“${esc(q)}”</b>. The call is identical every time; only <code>--boundary</code> changes. Checks are keyword matches on the reply, printed as they come out.</p>
      <div class="cols">${callCard("No boundary", S.calls.none, { cmd: `router chat "${q}" --model gpt-4o-mini` })}
        ${S.data.learners.map((l) => callCard(`Boundary of ${esc(first(l.display))}`, S.calls[l.key], { cmd: `router chat "${q}" --model gpt-4o-mini --boundary ${S.learners[l.key]?.boundary || "…"}` })).join("")}</div>`;
  }
  function bar(name, n, max, cls) { return `<div class="barrow"><span>${esc(name)}</span><div class="bar ${cls || ""}" style="width:${Math.max(1, Math.round(100 * n / max))}%"></div><span class="n">${n}</span></div>`; }
  function bill() {
    if (!S.bill) return unavailable() || empty("The token bill");
    const rows = S.bill.rows; const max = Math.max(...rows.map((r) => r.prompt), 1); const base = S.calls.none?.prompt_tokens || 0;
    const pct = rows.length === 2 ? Math.floor(100 * rows[1].context / Math.max(rows[0].context, 1) + 0.5) : null;
    const m = first(S.data.learners[0].display);
    return `<h1>The token bill</h1>
      <p class="lead">The usual workaround is pasting the whole history into the system prompt. Same message, same learner (${esc(m)}): prompt tokens the provider billed.</p>
      <div class="card"><h3 style="margin-top:0">“${esc(S.data.today_prompt)}”</h3><div class="bars">
        ${bar("no memory", base, max, "base")}${bar("pasted history (3 sessions)", rows[0]?.prompt || 0, max, "alt")}${bar("boundary (MemoryRouter)", rows[1]?.prompt || 0, max)}</div>
        ${pct != null ? `<p class="muted small">The boundary's context was <b>${pct}%</b> of the pasted history's — with only three short sessions. The pasted history grows with every session.</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">“${esc(S.data.off_topic)}” — nothing in memory is relevant</h3><div class="bars">
        ${bar("no boundary", S.bill.off[0], Math.max(...S.bill.off, 1), "base")}${bar(`boundary of ${m}`, S.bill.off[1], Math.max(...S.bill.off, 1))}</div>
        <p class="muted small">${S.bill.off[1] === S.bill.off[0] ? "Same size: nothing was attached." : `The boundary still attached ${S.bill.off[1] - S.bill.off[0]} tokens of memory to an off-topic message.`}</p></div>
      ${callCard("Pasted history (no boundary)", S.calls.history, { cmd: "router chat \"…\" --model gpt-4o-mini --body out/history-mina.json" })}`;
  }
  function models() {
    if (!S.calls.claude) return unavailable() || empty("Switch models");
    return `<h1>Switch models — the memory stays</h1>
      <p class="lead">Same boundary, Anthropic Messages protocol: <code>router messages --model claude-haiku-4-5 --boundary …</code>. Nothing was migrated; the memory lives outside the model.</p>
      <div class="cols">${callCard("gpt-4o-mini (step 5)", S.calls[S.data.learners[0].key])}${callCard("claude-haiku-4-5", S.calls.claude)}</div>`;
  }
  function next() {
    if (!S.calls.after && !S.calls.before) return unavailable() || empty("The next session");
    const t = S.data.learners[0].today;
    return `<h1>The next session — piped in, known on the next call</h1>
      <p class="lead">Question: <b>“${esc(t.followup)}”</b> — asked before tonight's session is stored, and again after. The app changes nothing; it only writes the turns.</p>
      <div class="cols">${callCard("Before", S.calls.before ? { ...S.calls.before, checks: { mina: S.calls.before.checks } } : null)}
        <div class="card"><h3 style="margin-top:0">Tonight's session</h3><div class="bubbles">${t.turns.map(([w, x]) => `<div class="bubble ${w === "user" ? "user" : "bot"} stored">${esc(x)}</div>`).join("")}</div>
          ${S.wb ? `<h3>Extracted (mentions N3)</h3>${S.wb.facts.length ? S.wb.facts.map((f) => `<div class="memfact new"><span>${esc(f)}</span></div>`).join("") : '<div class="muted small">none this time</div>'}` : ""}</div>
        ${callCard("After", S.calls.after ? { ...S.calls.after, checks: { mina: S.calls.after.checks } } : null)}</div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${S.summary.tokens.prompt} prompt + ${S.summary.tokens.completion} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = Object.keys(S.learners).length > 0;
    const who = [["mina", "Mina's boundary"], ["tomas", "Tomás's boundary"], ["none", "No memory"]];
    return `<h1>Ask — one call through MemoryRouter</h1>
      <p class="lead">Pick whose boundary to attach (or none) and a model, then send any message. The reply, the prompt tokens, and which learner's details it mentions.</p>
      <div class="card"><div class="userpick">${who.map(([k, t]) => `<button type="button" class="chip ${S.ask.who === k ? "on" : ""}" data-who="${k}">${t}</button>`).join("")}
        <span style="width:16px"></span>${(S.data?.models || []).map((m) => `<button type="button" class="chip ${S.ask.model === m ? "on" : ""}" data-model="${m}">${m}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. quiz me on something I keep getting wrong" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Send</button></form>
        <div class="chips">${["Quiz me on something I keep getting wrong", "What exam am I preparing for, and when?", "Suggest a role-play for tonight", "Quick one: what is 17 times 23?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    st.querySelectorAll(".userpick .chip[data-who]").forEach((c) => c.addEventListener("click", () => { S.ask.who = c.dataset.who; renderStage(); }));
    st.querySelectorAll(".userpick .chip[data-model]").forEach((c) => c.addEventListener("click", () => { S.ask.model = c.dataset.model; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> calling ${esc(S.ask.model)}…</span>`;
    const r = await api("/api/ask", { who: S.ask.who, prompt: q, model: S.ask.model });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = callCard(r.who === "none" ? "No boundary" : `Boundary of ${esc(first(L(r.who)?.display || r.who))}`, r);
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
  function resetView() { S.sessions = {}; S.memory = {}; S.calls = {}; S.bill = null; S.wb = null; S.unavailable = null; S.summary = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo learners (and every fact on them), their sessions, projects and boundaries, and the tutor actor?")) return;
    banner(""); const r = await api("/api/cleanup", {}); if (r) { S.st = r; setConn(); }
  });

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

  const stepOf = (i) => STEPS.find((s) => s.n === i);
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView(); term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "learner") S.learners[d.key] = d;
    else if (k === "session") S.sessions[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false };
    else if (k === "turn") (S.sessions[d.custom_id] ||= {}).stored = d.index;
    else if (k === "cooked") Object.values(S.sessions).forEach((s) => { if (s.id === d.id) s.cooked = true; });
    else if (k === "memory") S.memory[d.learner] = d;
    else if (k === "call") S.calls[d.slot] = d;
    else if (k === "bill") S.bill = d;
    else if (k === "writeback") S.wb = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.learners = {}; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the learners, their memory and their boundaries are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: any message, any boundary, either model.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "turn"].includes(k)) { renderSteps(); renderStage(); }
    else if (S.caughtUp && k === "turn" && S.view === "sessions") renderStage();
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "tutor", "learner", "session", "turn", "cooked", "memory", "call", "bill", "writeback", "summary", "router_unavailable", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
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
    S.st = state; setConn();
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.calls.none ? "calls" : "connect");
    subscribe();
  })();
})();

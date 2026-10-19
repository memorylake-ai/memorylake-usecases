/* Commit-point memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const sec = (x) => (x === null || x === undefined ? "—" : `+${Number(x).toFixed(1)}s`);

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "Tern's memory", view: "setup" },
    { n: 3, title: "The past", sub: "3 sessions → facts", view: "history" },
    { n: 4, title: "Tonight", sub: "16 steps, 3 changes, 9 restarts", view: "night" },
    { n: 5, title: "Commit log", sub: "batches: read → written", view: "log" },
    { n: 6, title: "Tally", sub: "which commit point held", view: "tally" },
  ];
  const fresh = () => ({ memory: null, lines: [], restarts: [], controls: [], log: null, tally: null, unavailable: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Which schema is tonight's backfill writing to?", termCount: 0, lastEvent: 0, caughtUp: false, ingest: [],
  };
  const RULES = () => S.data?.all || ["B", "C", "D"];
  const RULE = (a) => (S.data?.rules || {})[a] || a;
  const SHORT = (a) => (S.data?.short || {})[a] || a;
  const SIGNAL = (a) => (S.data?.signal || {})[a] || "";
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const MARKS = { ok: ["ok", "✓", "right"], stale: ["stale", "⟲", "the value from before tonight's change"],
    unknown: ["empty", "∅", "says it is not on file"], made: ["bad", "✗", "a wrong or made-up value"] };
  const mark = (g) => g ? `<span class="mark ${MARKS[g][0]}" title="${MARKS[g][2]}">${MARKS[g][1]}</span>` : "";
  const setting = (id) => (S.data?.conventions || []).find((c) => c.id === id) || {};
  const changed = () => (S.data?.conventions || []).filter((c) => c.new).sort((a, b) => a.changed_at - b.changed_at);
  const stepName = (seq) => `${Math.floor((seq + 1) / 2)}${seq % 2 ? "m" : "t"}`;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>a restarted Tern, memory only</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, history, night, log, tally, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project || "")}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes: the past sessions extracted, then a 16-step run with 12 MemoryRouter calls · everything it creates is prefixed <code>${esc(d.prefix || "mlu-cmt")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>When is a step committed? Restart the agent and see</h1>
      <p class="lead">${esc(d.assistant?.display || "Tern")} runs the overnight backfills at ${esc(d.company || "Halvorsen Analytics")} (fictional) for ${esc(d.person?.display || "Mei Okafor")}.
      It writes every step into MemoryLake as it goes. A crash wipes whatever it kept in the process, so a step is only safe once memory has it — but which signal says so?
      Tonight Mei changes three settings mid-run, and the demo restarts ${esc(d.assistant?.display || "Tern")} at the moment each rule calls that step committed:
      <b>B</b> when the append returns · <b>C</b> when extraction has read it · <b>D</b> when memory has written it. The restarted agent has only its memory.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 12 calls on gpt-4o-mini per run, well under 0.01 USD.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-store-for-autonomous-agents" target="_blank" rel="noopener">memorylake.ai › Memory store for autonomous agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = S.data || {}, w = S.world;
    return `<h1>Tern's memory</h1>
      <p class="lead">One project holds Tern's memory; a boundary over it is what MemoryRouter attaches when the restarted agent asks. Every step of tonight's run is appended to a conversation in that project as it happens.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>rule</th><th>a step counts as committed when</th></tr></thead><tbody>
        ${RULES().map((a) => `<tr><td><b>${esc(RULE(a))}</b></td><td class="small">${code(SIGNAL(a))}</td></tr>`).join("")}</tbody></table></div>
      ${w ? `<div class="card"><dl class="kv"><dt>${esc(d.person?.display)}</dt><dd class="mono">${esc(w.mei)}</dd><dt>${esc(d.assistant?.display)}</dt><dd class="mono">${esc(w.tern)}</dd>
        <dt>Project</dt><dd class="mono">${esc(w.project)}</dd><dt>Boundary</dt><dd class="mono">${esc(w.boundary)}</dd></dl></div>` : running() ? waiting("") : ""}`;
  }

  function history() {
    const d = S.data || {}, m = S.memory;
    return `<h1>The past: ${(d.sessions || []).length} sessions in September</h1>
      <p class="lead">Each session is a conversation in Tern's project; extraction turns it into facts. Below: is every agreed setting in memory before tonight's run starts?</p>
      ${S.ingest.length ? `<div class="card small mono">${S.ingest.map((x) => esc(x)).join("<br>")}</div>` : ""}
      ${m ? `<div class="card"><h3 style="margin-top:0">${m.facts} facts</h3>
        <table class="lint plain"><thead><tr><th>agreed setting</th><th>session</th><th>in memory</th><th>tonight</th></tr></thead><tbody>
        ${(d.conventions || []).map((c) => `<tr><td><b>${esc(c.label)}</b>: ${esc(c.old)}</td><td class="small">${esc(c.from)}</td><td>${m.held[c.id] ? mark("ok") : "·"}</td><td class="small">${c.new ? `changes to <b>${esc(c.new)}</b> at step ${c.changed_at}` : "unchanged"}</td></tr>`).join("")}</tbody></table></div>`
        : running() ? waiting("") : ""}
      <div class="grid3">${(d.sessions || []).map((s) => `<div class="card"><h3 style="margin-top:0">${esc(s.id)} · ${esc(s.topic)} <span class="muted small">${esc(s.at)}</span></h3>
        <div class="chat">${s.turns.slice(0, 2).map((t) => `<div class="msg ${t.who === "mei" ? "user" : "bot"}"><span class="who">${t.who === "mei" ? esc(d.person?.display) : esc(d.assistant?.display)}</span>${esc(t.text.replace(/^[^:]+:\s*/, ""))}</div>`).join("")}</div></div>`).join("")}</div>`;
  }

  function changeCard(c) {
    const rs = S.restarts.filter((r) => r.change === c.id), said = S.lines.find((l) => l.n === c.changed_at);
    const ctl = S.controls.find((x) => x.change === c.id);
    return `<div class="card"><h3 style="margin-top:0">Step ${c.changed_at} · ${esc(c.label)}: ${esc(c.old)} → <b>${esc(c.new)}</b> <span class="muted small">said at ${sec(said?.t)}</span></h3>
      <div class="grid3">${RULES().map((a) => { const r = rs.find((x) => x.rule === a);
        if (!r) return `<div class="msg bot"><span class="who">${esc(SHORT(a))} · waiting for: ${code(SIGNAL(a))}</span>${running() ? '<span class="spinner"></span>' : "—"}</div>`;
        return `<div class="msg bot ${r.grade === "ok" ? "" : "wrong"}"><span class="who">${esc(SHORT(a))} ${mark(r.grade)} · restart at ${sec(r.safe)} (${said ? "+" + (r.safe - said.t).toFixed(1) + "s after it was said" : ""})</span>${esc(r.reply || "(MemoryRouter unavailable)")}</div>`; }).join("")}</div>
      ${ctl ? `<p class="small muted" style="margin-bottom:0">Control at B's restart — ${esc(setting(ctl.id).label)} (unchanged) ${mark(ctl.grade)} “${esc(ctl.reply)}”</p>` : ""}</div>`;
  }

  function night() {
    if (!S.lines.length) return unavailable() || (running() ? waiting("Tonight") : empty("Tonight"));
    const d = S.data || {}, n = (d.live || []).length;
    const done = Math.max(0, ...S.lines.map((l) => l.n));
    const right = (a) => S.restarts.filter((r) => r.rule === a && r.grade === "ok").length;
    const asked = (a) => S.restarts.filter((r) => r.rule === a).length;
    return `<h1>Step ${done} of ${n} · restarts right so far: ${RULES().map((a) => `${a} ${right(a)}/${asked(a)}`).join(" · ")}</h1>
      <p class="lead">Each change gets three restarts, one at each rule's commit moment. The restarted Tern asks MemoryRouter with nothing of its own — only what the boundary's memory holds at that second.</p>
      ${unavailable()}${changed().filter((c) => c.changed_at <= done).map(changeCard).join("")}
      <div class="card"><table class="lint plain"><tbody>
        ${S.lines.slice().sort((a, b) => a.n - b.n).map((x) => `<tr class="${x.change ? "hl" : ""}"><td class="small mono">${x.n}</td><td class="small mono">${sec(x.t)}</td><td class="small">${esc(x.said)}<br><span class="muted">${esc(x.agent)}</span></td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function log() {
    const L = S.log;
    if (!L) return running() ? waiting("Commit log") : empty("Commit log");
    const during = (L.status || []).filter((s) => s[0] <= L.t_last), after = (L.status || []).find((s) => s[1] && s[0] > L.t_last);
    const empties = L.batches.filter((b) => b.verdict === "nothing to write");
    const V = { written: ["ok", "committed: receipt"], "nothing to write": ["old", "committed: a later batch has its receipt"], open: ["", "not yet"] };
    return `<h1>${L.messages} messages, ${L.batches.length} extraction batches</h1>
      <p class="lead">Extraction reads messages in batches. <code>conv fact-actions --message</code> answers for the whole batch a message was read in — that is D's receipt. A batch of progress chatter writes nothing and never gets one, so D also releases a batch once a later batch has its receipt. (m = Mei's message of a step, t = Tern's.)</p>
      <div class="card"><table class="lint plain"><thead><tr><th>batch</th><th>steps</th><th>wrote</th><th>D says</th></tr></thead><tbody>
        ${L.batches.map((b, i) => `<tr class="${b.actions.length ? "hl" : ""}"><td class="mono">${i + 1}</td><td class="mono small">${stepName(b.seqs[0])}${b.seqs.length > 1 ? "–" + stepName(b.seqs[b.seqs.length - 1]) : ""} (${b.seqs.length})</td>
          <td class="small">${b.actions.length ? b.actions.map((a) => `<div><span class="tag ok">${esc(a.event)}</span> ${esc(a.fact)}</div>`).join("") : (b.read ? "—" : "not read")}</td>
          <td class="small"><span class="tag ${V[b.verdict][0]}">${V[b.verdict][1]}</span></td></tr>`).join("")}</tbody></table>
        ${empties.length ? `<p class="small muted" style="margin-bottom:0">${empties.length} batch(es), ${empties.reduce((n, b) => n + b.seqs.length, 0)} messages, wrote nothing: a rule waiting for each message's own receipt would keep them in the process forever.</p>` : ""}</div>
      ${L.status ? `<div class="card"><h3 style="margin-top:0">Why not <code>cook-status</code>?</h3><p class="small">Sampled every 3 s. While steps were arriving (up to ${sec(L.t_last)}), <code>cook_finished</code> was true in <b>${during.filter((s) => s[1]).length} of ${during.length}</b> samples${after ? `; true for good from ${sec(after[0])}, after the last step` : ""}. It is one flag for the whole conversation, true only when extraction has caught up with everything — as a commit point it holds every step until the run goes quiet.</p></div>` : ""}`;
  }

  function tally() {
    const T = S.tally?.tallies;
    if (!T) return running() ? waiting("Tally") : empty("Tally");
    const rows = changed(), ctl = S.tally.controls || [];
    const cell = (a, c) => { const r = S.restarts.find((x) => x.rule === a && x.change === c.id), said = S.lines.find((l) => l.n === c.changed_at);
      return r ? `${mark(r.grade)} <span class="mono small">+${(r.safe - (said?.t || 0)).toFixed(1)}s</span>` : "—"; };
    return `<h1>Restarted at its own commit moment: ${RULES().map((a) => `${a} ${T[a].ok}/${T[a].n}`).join(" · ")}</h1>
      <p class="lead">+N s = how long after Mei said it each rule called the step committed. ⟲ = the restarted agent used the value from before tonight's change — memory's old copy, said with confidence.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>commit point</th>${rows.map((c) => `<th>${esc(c.label)}</th>`).join("")}<th>right</th></tr></thead><tbody>
        ${RULES().map((a) => `<tr class="${a === "D" ? "" : "hl"}"><td><b>${esc(RULE(a))}</b><div class="small muted">${code(SIGNAL(a))}</div></td>${rows.map((c) => `<td>${cell(a, c)}</td>`).join("")}<td><b>${T[a].ok}/${T[a].n}</b></td></tr>`).join("")}</tbody></table>
      ${ctl.length ? `<p class="small muted" style="margin-bottom:0">Control: an unchanged setting asked at B's restarts, ${ctl.filter((c) => c.grade === "ok").length}/${ctl.length} right — the memory itself works; what fails is the commit point.</p>` : ""}</div>`;
  }

  function tryView() {
    const ready = !!S.world;
    const sugg = ["Which schema is tonight's backfill writing to?", "How many rows go in each batch tonight?", "Which channel do failure alerts go to?", "Which bucket do you stage files in?", "Between which hours may you write to the warehouse?"];
    return `<h1>Ask one — Tern, restarted</h1>
      <p class="lead">No notes of its own: only what its memory holds now. After a run, tonight's changes have been written, so it should know all three.</p>
      <div class="card"><label for="t-q">question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${sugg.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div id="try-log">${S.tries.slice().reverse().map((x) => `<h3>“${esc(x.question)}”</h3>${x.row ? `<div class="msg bot"><span class="who">Tern, restarted · ${fmt(x.row.prompt_tokens)} tok</span>${esc(x.row.reply)}</div>` : '<p class="muted small">MemoryRouter unavailable.</p>'}`).join("")}</div></div>`;
  }

  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo());
    st.querySelector("#t-q")?.addEventListener("input", (e) => { S.tq = e.target.value; });
    st.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { S.tq = b.dataset.q; renderStage(); }));
    st.querySelector('[data-act="try"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Asking…";
      const r = await api("/api/ask", { question: S.tq });
      if (r) S.tries.push({ question: r.question, row: r.row });
      if (S.view === "try") renderStage();
    });
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
    ["#btn-run", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { Object.assign(S, fresh()); S.ingest = []; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary, conversations, facts, actors and project?")) return;
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
    else if (k === "progress") { term("progress", ev.text.trim()); S.ingest.push(ev.text.trim()); if (S.caughtUp && S.view === "history") renderStage(); }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "memory") S.memory = d;
    else if (k === "step_line") S.lines = S.lines.filter((l) => l.n !== d.n).concat([d]);
    else if (k === "restart") S.restarts = S.restarts.filter((r) => !(r.change === d.change && r.rule === d.rule)).concat([d]);
    else if (k === "control") S.controls = S.controls.filter((r) => r.change !== d.change).concat([d]);
    else if (k === "log") S.log = d;
    else if (k === "tally") S.tally = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, conversations, facts, actors and project are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: a restarted Tern asks its memory.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "step_line", "restart", "control", "log", "tally",
    "router_unavailable", "answered", "facts_done", "done", "error"];
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
    show(state.status === "running" ? S.view : S.tally ? "tally" : "connect");
    subscribe();
  })();
})();

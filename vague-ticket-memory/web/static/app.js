/* Vague ticket memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "What the team knows", sub: "20 support policies", view: "memory" },
    { n: 4, title: "The inbox", sub: "what customers write", view: "inbox" },
    { n: 5, title: "Message only", sub: "as most integrations wire it", view: "plain" },
    { n: 6, title: "With the topic", sub: "picked in the help widget", view: "topic" },
    { n: 7, title: "Score + guard", sub: "✓ · ✗ · ∅", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, inbox: null, rows: { plain: [], topic: [] }, examples: { plain: [], topic: [] }, score: null,
    unavailable: null, summary: null, tries: [], tmsg: "Please wipe everything you have on us.", ttopic: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const INBOX = () => S.data?.inbox || [];
  const RUNS = () => S.data?.runs || 3;
  const msgOf = (id) => INBOX().find((m) => m.id === id);

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Try a message<small>how much gets attached</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, inbox, plain: () => arm("plain"), topic: () => arm("topic"), score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (r) => r.ok == null ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${r.ok ? "ok" : "bad"}">${r.ok ? "✓" : "✗"}</span>`;
  const att = (r) => `<span class="tok ${r.empty ? "bad-ink" : ""}">+${r.attached}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.ok != null);
    return { calls: live.length, right: live.filter((r) => !r.empty && r.ok).length, wrong: live.filter((r) => !r.empty && !r.ok).length, empty: live.filter((r) => r.empty).length };
  }
  const tline = (t) => `<b>${t.right}</b> ✓ · <b>${t.wrong}</b> ✗ · <b class="${t.empty ? "bad-ink" : ""}">${t.empty}</b> ∅ <span class="muted">of ${t.calls}</span>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-vtm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“Please wipe everything you have on us.” — which policy did the assistant use?</h1>
      <p class="lead">Brightdesk (fictional, 9 people) keeps its support policies in shared memory, and Tier 1 drafts replies through MemoryRouter: an ordinary model call with
      <code>--boundary</code> added. Customers rarely name the topic. For a message like that, MemoryRouter often attaches <b>nothing at all</b> — and the reply reads just as sure.
      The only trace is the prompt-token count. This demo drafts replies to 11 customer messages, three times each, first from the message alone, then with the topic the customer
      picked in the help widget. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 90 calls on gpt-4o-mini per run (about 12,000 prompt tokens).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-saas-founder-and-team-coordination" target="_blank" rel="noopener">memorylake.ai › AI memory for SaaS founder and team coordination</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>One project, one boundary — the shared memory every reply draws on</h1>
      <p class="lead">The assistant never looks policies up itself: every draft is an ordinary model call with <code>--boundary</code> added, and MemoryRouter decides
      which of the project's policies to attach, call by call. The reply does not say what it was given.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(w?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(w?.boundary || "pending")}</b></dd></dl>
        <div class="cmdline">boundary create --projects &lt;Brightdesk support policies&gt;</div></div>
        <div class="card"><h3 style="margin-top:0">The assistant's system prompt</h3><p class="small">${esc(S.data?.system || "")}</p></div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What the team knows") : empty("What the team knows");
    const m = S.memory;
    return `<h1>${m.count} support policies, stored word for word</h1>
      <p class="lead">One <code>fact add</code> per policy, all in the shared project. The inbox below needs nine of them.</p>
      <div class="card">${m.policies.map((p) => `<div class="memfact small"><span><span class="lab lab-ok">${esc(p.topic)}</span> ${esc(p.text)}</span></div>`).join("")}</div>`;
  }

  function inbox() {
    if (!S.inbox) return running() ? waiting("The inbox") : empty("The inbox");
    const row = (m) => `<tr><td><b>${esc(m.id)}</b></td><td>“${esc(m.message)}”<div class="small muted">${esc(m.sender)}</div></td><td class="small">${esc(m.topic)}</td><td class="small muted">${esc(m.right_label)}</td></tr>`;
    return `<h1>The inbox — what customers actually write</h1>
      <p class="lead">Nine messages that never name their topic, and two controls that say the same thing with the topic named. Before writing, each customer picked a topic in the help widget —
      most integrations drop that and forward only the message.</p>
      <div class="card"><table class="lint plain"><thead><tr><th></th><th>customer message</th><th>topic picked in the help widget</th><th>a right reply states</th></tr></thead><tbody>
        ${S.inbox.filter((m) => !m.control).map(row).join("")}
        <tr class="hl"><td colspan="4"><b>controls</b> — the same requests with the topic named</td></tr>
        ${S.inbox.filter((m) => m.control).map(row).join("")}</tbody></table></div>`;
  }

  function arm(name) {
    const h = name === "plain" ? "Draft replies from the message only" : "Draft replies with the widget topic";
    const rows = S.rows[name];
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const vague = tally(rows.filter((r) => !r.control)), ctrl = tally(rows.filter((r) => r.control));
    const lead = name === "plain"
      ? `The customer's message is the whole user turn. Each message: one call without <code>--boundary</code> (the baseline), then ${RUNS()} with it.
         <b>attached</b> = prompt tokens minus the baseline; <b>+0</b> means MemoryRouter attached nothing and the reply is the model on its own.`
      : `The same messages, with one line in front: <code>[Topic picked in the help widget: …]</code>. Nothing else changed — same boundary, same policies, same model.`;
    const byMsg = INBOX().map((m) => ({ m, rs: rows.filter((r) => r.m === m.id) })).filter((x) => x.rs.length);
    const ex = S.examples[name];
    return `<h1>${h} — vague ${vague.empty} ∅ of ${vague.calls}</h1>
      <p class="lead">${lead}</p>
      <div class="card"><table class="lint plain"><thead><tr><th></th><th>customer message</th><th>${RUNS()} drafts</th><th>attached tokens</th></tr></thead><tbody>
      ${byMsg.map(({ m, rs }) => `<tr class="${rs.some((r) => r.empty) ? "hl" : ""}"><td><b>${esc(m.id)}</b></td><td>“${esc(m.message)}”${name === "topic" ? `<div class="small muted">${esc(m.topic)}</div>` : ""}</td>
        <td>${rs.map(markOf).join(" ")}</td><td>${rs.map(att).join(" · ")}</td></tr>`).join("")}
      </tbody></table>
      <p class="small">vague ${tline(vague)} &nbsp;&nbsp; controls ${tline(ctrl)}</p></div>
      ${ex.length ? `<h2>${name === "plain" ? "Drafts as they would have been sent" : "The same messages, now with their topic"}</h2>
      <div class="grid2">${ex.map((r) => { const m = msgOf(r.m); return `<div class="card"><div class="msg cust"><span class="who">${esc(m.sender)}</span>“${esc(m.message)}”</div>
        <div class="msg bot ${r.ok ? "" : "wrong"}"><span class="who">draft ${markOf(r)} · attached ${att(r)} tokens</span>${esc(r.reply)}</div>
        <p class="small muted" style="margin-bottom:0">a right reply states: ${esc(m.right_label)}${r.why ? ` · <span class="bad-ink">${esc(r.why)}</span>` : ""}</p></div>`; }).join("")}</div>` : ""}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score;
    const line = (a, part) => tline(sc[a][part]);
    return `<h1>Message only: ${sc.plain.vague.empty} of ${sc.plain.vague.calls} drafts had nothing attached · with the topic: ${sc.topic.vague.empty}</h1>
      <p class="lead">✓ right · ✗ wrong · ∅ nothing attached (prompt tokens equal the no-memory baseline — the reply is the model on its own, graded apart).
      The controls name their topic and get policies attached either way.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>wiring</th><th>9 vague messages × ${RUNS()}</th><th>2 controls × ${RUNS()}</th></tr></thead><tbody>
        <tr class="hl"><td><b>${esc(S.data.arms.plain)}</b></td><td>${line("plain", "vague")}</td><td>${line("plain", "control")}</td></tr>
        <tr><td><b>${esc(S.data.arms.topic)}</b></td><td>${line("topic", "vague")}</td><td>${line("topic", "control")}</td></tr></tbody></table></div>
      <h2>The guard — hold a draft written with nothing attached</h2>
      <div class="card"><table class="lint plain"><thead><tr><th>wiring</th><th>held</th><th>sent</th></tr></thead><tbody>
        ${["plain", "topic"].map((a) => { const t = sc[a].all; return `<tr><td>${esc(S.data.arms[a])}</td><td><b>${t.empty}</b></td><td>${t.right + t.wrong} (${t.right} right, <span class="${t.wrong ? "bad-ink" : ""}">${t.wrong} wrong</span>)</td></tr>`; }).join("")}
      </tbody></table>
      <p class="small muted" style="margin-bottom:0">The guard catches every empty call: the prompt-token count gives it away. It cannot catch a draft written with the <i>wrong</i> policy attached
      (“I want it back” answered with the account-deletion grace period, or a reply in German because the Germany-only rule came along) — those have tokens attached like any other.
      The topic line is what fixes those.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    const topics = [...new Set(INBOX().map((m) => m.topic))];
    return `<h1>Try a message</h1>
      <p class="lead">Any customer message, through the demo's boundary: the baseline without memory, then ${RUNS()} calls with it. Leave the topic empty to send the message alone.</p>
      <div class="card"><label for="t-msg">Customer message</label><input id="t-msg" value="${esc(S.tmsg)}">
        <label for="t-topic">Topic picked in the help widget <span class="faint">(optional)</span></label>
        <select id="t-topic"><option value="">— none —</option>${topics.map((t) => `<option ${S.ttopic === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Send ${RUNS()} times</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((t) => `<div class="msg cust"><span class="who">${t.topic ? esc(t.topic) : "no topic"} · baseline ${t.base} prompt tokens</span>“${esc(t.message)}”</div>
          ${t.calls.map((c) => `<div class="msg bot"><span class="who">attached ${att(c)} tokens${c.empty ? ' · <b class="bad-ink">∅ nothing attached</b>' : ""}</span>${esc(c.reply)}</div>`).join("")}`).join("")}</div></div>`;
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
    st.querySelector("#t-msg")?.addEventListener("input", (e) => { S.tmsg = e.target.value; });
    st.querySelector("#t-topic")?.addEventListener("change", (e) => { S.ttopic = e.target.value; });
    st.querySelector('[data-act="try"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Calling…";
      const r = await api("/api/attach", { message: S.tmsg, topic: S.ttopic });
      if (r) S.tries.push({ message: S.tmsg, topic: S.ttopic, base: r.base, calls: r.calls });
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
  function resetView() { S.memory = null; S.inbox = null; S.rows = { plain: [], topic: [] }; S.examples = { plain: [], topic: [] }; S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary, its project and the 20 policies in it?")) return;
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
    else if (k === "world") S.world = d;
    else if (k === "memory") S.memory = d;
    else if (k === "inbox") S.inbox = d.messages;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "examples") S.examples[d.arm] = d.rows;
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the project and its policies are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try a message of your own: see how much MemoryRouter attaches, with and without a topic.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "inbox", "ask_row", "examples", "score", "summary", "router_unavailable", "attach", "done", "error"];
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
    show(state.status === "running" ? S.view : S.score ? "score" : "connect");
    subscribe();
  })();
})();

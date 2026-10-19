/* Response-chain memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "What Packbot remembers", sub: "12 customer notes", view: "memory" },
    { n: 4, title: "Turn 1", sub: "the packer names the order", view: "first" },
    { n: 5, title: "v1 · whole conversation", sub: "before the upgrade", view: "history" },
    { n: 6, title: "v2 · previous_response_id", sub: "after the upgrade", view: "chain" },
    { n: 7, title: "Whose notes", sub: "what MemoryRouter attached", view: "attached" },
    { n: 8, title: "Control and fix", sub: "still on previous_response_id", view: "fix" },
    { n: 9, title: "Scoreboard", sub: "same chats, same model", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, firsts: [], turns: { history: [], chain: [], chain_bare: [], chain_named: [] }, inspect: [],
    score: null, unavailable: null, summary: null, tries: [], tryCustomer: "Lena Vos", termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Try one order<small>v1 vs v2, live</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, first, history: () => arm("history"), chain: () => arm("chain"), attached, fix, score, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (ok) => ok == null ? '<span class="mark">·</span>' : `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;
  const first1 = (n) => String(n || "").split(" ")[0];

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1 minute · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-rcm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>After the SDK upgrade, the same follow-up gets another customer's gate code.</h1>
      <p class="lead">Packbot is a packing assistant that reads customer notes through <b>MemoryRouter</b>. v1 sent the whole conversation on every turn. v2 moved to the
      Responses API the way its docs suggest: <code>store: true</code>, then only the new message and <code>previous_response_id</code>. MemoryRouter can only search
      with what is in the request — and the new message doesn't say whose order it is. This demo drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 30 calls on gpt-4o-mini and 7 on claude-haiku-4-5 per run.</li>
          <li>Turn-1 replies are stored with the model provider (<code>store: true</code>); MemoryLake cannot delete them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/stable-memory-across-sdk-upgrades-for-agents" target="_blank" rel="noopener">memorylake.ai › Stable memory across SDK upgrades</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    return `<h1>One project for the customer notes, one boundary for Packbot</h1>
      <p class="lead">Packbot never assembles memory itself: every model call goes through MemoryRouter with <code>--boundary</code>, and MemoryRouter picks the notes
      that match the request.</p>
      <div class="cols"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(S.world?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(S.world?.boundary || "pending")}</b></dd></dl>
        <h3>Each Packbot turn</h3>
        <div class="cmdline">router responses "&lt;packer's message&gt;" --model ${esc(S.data.model)} --body &lt;request body&gt; --boundary ${esc(S.world?.boundary || "…")}</div></div>
        <div class="card"><h3 style="margin-top:0">${esc(S.data.company)}'s customers</h3><div class="factlist">${S.data.customers.map((c) => `<div class="memfact"><span><b>${esc(c.name)}</b> · order ${esc(c.order)}</span></div>`).join("")}</div></div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What Packbot remembers") : empty("What Packbot remembers");
    return `<h1>What Packbot remembers — ${S.memory.facts.length} notes</h1>
      <p class="lead">For each of ${S.memory.customers.length} customers: the drop-off gate code and what must not go in the box, stored with <code>fact add</code>.</p>
      <div class="card"><div class="factlist">${S.memory.facts.map((f) => `<div class="factline">${esc(f)}</div>`).join("")}</div></div>`;
  }

  function first() {
    if (!S.firsts.length) return unavailable() || (running() ? waiting("Turn 1") : empty("Turn 1"));
    return `<h1>Turn 1 — the packer names the order</h1>
      <p class="lead">Sent once per order with <code>store: true</code>, so the provider keeps it. Every version below continues from these same replies; only turn 2 differs.</p>
      <div class="card chat">${S.firsts.map((f) => `<div class="msg user"><span class="who">Packer</span>${esc(f.say)}</div>
        <div class="msg bot"><span class="who">Packbot <span class="faint mono">${esc((f.id || "").slice(0, 18))}…</span></span>${esc(f.reply)}</div>`).join("")}</div>
      <div class="card"><b>Turn 2, for every order:</b> “${esc(S.data?.turn2)}”</div>`;
  }

  function verdict(t) {
    const g = t.grade;
    if (!g) return "";
    if (g.wrong && g.wrong.length) return `<span class="bad-ink small"> ← ${g.wrong.map(esc).join(", ")}'s gate code</span>`;
    if (!g.said_code) return '<span class="bad-ink small"> ← no gate code</span>';
    return "";
  }
  function rows(list) {
    return list.map((t) => `<div class="msg bot ${t.grade && !t.grade.ok ? "wrong" : ""}"><span class="who">${markOf(t.grade ? t.grade.ok : null)} ${esc(t.customer)}${verdict(t)}</span>${esc(t.reply || "…")}
      <div class="faint small">input_tokens ${fmt(t.input_tokens)}</div></div>`).join("");
  }
  const rightOf = (list) => { const got = list.filter((t) => t.grade); return `${got.filter((t) => t.grade.ok).length}/${got.length}`; };

  function arm(key) {
    const a = S.data?.arms?.[key] || { label: key, how: "" };
    const ts = S.turns[key];
    if (!ts.length) return unavailable() || (running() ? waiting(a.label) : empty(a.label));
    return `<h1>${esc(a.label)} — ${rightOf(ts)} right</h1>
      <p class="lead">${code(a.how)}. Same model (${esc(S.data?.model)}), temperature 0.</p>
      <div class="card chat"><div class="msg user"><span class="who">Packer, turn 2</span>${esc(S.data?.turn2)}</div>${rows(ts)}</div>`;
  }

  function attached() {
    if (!S.inspect.length) return unavailable() || (running() ? waiting("Whose notes MemoryRouter attached") : empty("Whose notes MemoryRouter attached"));
    const who = (x) => [...new Set(x.whose)].map(first1).join(", ") || "(nothing copied)";
    const ch = S.inspect[0].chain;
    return `<h1>Whose notes MemoryRouter attached to turn 2</h1>
      <p class="lead"><b>${esc(S.data?.checker)}</b> gets the request body MemoryRouter saw, through the same boundary, told to copy out its memory before answering.
      v1's body holds the conversation. v2's holds only the new message — the same sentence for all six orders, so it gets the same notes every time.</p>
      <div class="card"><table class="lint"><thead><tr><th>order</th><th>v1 · attached</th><th>v2 · attached</th></tr></thead><tbody>
        ${S.inspect.map((r) => { const other = r.chain.whose.some((w) => w !== r.customer && w !== "?");
          return `<tr><td>${esc(r.customer)}</td><td>${esc(who(r.history))}</td><td class="${other ? "bad-ink" : ""}">${esc(who(r.chain))}${other ? " ← another customer" : ""}</td></tr>`; }).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">What v2 got, as Claude quoted it back</h3>${ch.lines.map((l) => `<div class="factline">${esc(l)}</div>`).join("") || '<span class="muted">(nothing copied)</span>'}</div>`;
  }

  function fix() {
    const b = S.turns.chain_bare, n = S.turns.chain_named;
    if (!b.length && !n.length) return unavailable() || (running() ? waiting("Control and fix") : empty("Control and fix"));
    const A = S.data?.arms || {};
    return `<h1>Control and fix — still on previous_response_id</h1>
      <div class="cols"><div class="card chat"><h3 style="margin-top:0">${esc(A.chain_bare?.label)} — ${rightOf(b)}</h3><p class="small muted">${code(A.chain_bare?.how)}. Turn 1's memory is already in the stored chain.</p>${rows(b)}</div>
        <div class="card chat"><h3 style="margin-top:0">${esc(A.chain_named?.label)} — ${rightOf(n)}</h3><p class="small muted">“${esc((S.data?.turn2_named || "").replace(/\{name\}/g, "<name>"))}”</p>${rows(n)}</div></div>`;
  }

  function score() {
    if (!S.score) return unavailable() || (running() ? waiting("Scoreboard") : empty("Scoreboard"));
    const tot = S.score.totals || {}, A = S.data?.arms || {}, keys = ["history", "chain", "chain_bare", "chain_named"];
    const n = S.score.rows.length;
    return `<h1>Scoreboard</h1><p class="lead">The same six chats, the same model; only how turn 2 is sent differs.</p>
      <div class="card"><table class="lint"><thead><tr><th>order</th>${keys.map((k) => `<th>${esc(A[k]?.short || k)}</th>`).join("")}</tr></thead><tbody>
        ${S.score.rows.map((r) => `<tr><td>${esc(r.customer)} <span class="faint mono small">${esc(r.order)}</span></td>${keys.map((k) => `<td>${markOf(r[k])}</td>`).join("")}</tr>`).join("")}
        <tr class="hl"><td><b>right</b></td>${keys.map((k) => `<td>${tot[k] ?? "–"}/${n}</td>`).join("")}</tr></tbody></table>
        <p class="muted small">v2 sends MemoryRouter a request that doesn't say whose order it is, and gets the same customer's notes for every order. Leaving the boundary off
        turn 2, or naming the customer in the new message, puts it right.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} input + ${fmt(S.summary.tokens.completion)} output tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    const opts = (S.data?.customers || []).map((c) => `<option ${c.name === S.tryCustomer ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    return `<h1>Try one order — v1 and v2, live</h1>
      <p class="lead">Pick a customer. Packbot gets turn 1 (stored), then turn 2 three ways: whole conversation, <code>previous_response_id</code>, and
      <code>previous_response_id</code> with the customer named.</p>
      <div class="card"><form class="ask-form" id="ask-form"><select id="ask-c" ${ready ? "" : "disabled"}>${opts}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Send both turns</button></form>
        <div class="chat" id="try-log">${S.tries.map((x) => `<div class="msg user"><span class="who">Packer</span>${esc(x.first.say)}</div>
          <div class="msg bot"><span class="who">Packbot</span>${esc(x.first.reply)}</div>
          <div class="msg user"><span class="who">Packer</span>${esc(S.data?.turn2)}</div>
          ${["history", "chain", "chain_named"].map((k) => `<div class="msg bot ${x[k].grade && !x[k].grade.ok ? "wrong" : ""}"><span class="who">${markOf(x[k].grade ? x[k].grade.ok : null)} ${esc(S.data?.arms?.[k]?.short)}${verdict(x[k])}</span>${esc(x[k].reply)}</div>`).join("")}`).join("")
          || (ready ? '<div class="empty">Nothing sent yet.</div>' : '<div class="empty">Run the demo first.</div>')}</div></div>`;
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
    st.querySelector("#ask-form")?.addEventListener("submit", (e) => { e.preventDefault(); tryOne($("#ask-c").value); });
  }
  async function tryOne(customer) {
    if (!customer) return;
    S.tryCustomer = customer;
    const log = $("#try-log"); if (log) log.insertAdjacentHTML("afterbegin", `<span class="pill busy" id="sending"><span class="spinner"></span> sending ${esc(customer)}'s order three ways…</span>`);
    const r = await api("/api/ask", { customer });
    if (r) S.tries.unshift(r);
    if (S.view === "ask") renderStage();
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
  function resetView() { S.memory = null; S.firsts = []; S.turns = { history: [], chain: [], chain_bare: [], chain_named: [] }; S.inspect = []; S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's memory project (and every note in it) and its boundary?")) return;
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
    else if (k === "first") S.firsts.push(d);
    else if (k === "turn") { if (S.turns[d.arm]) S.turns[d.arm].push(d); }
    else if (k === "inspect") S.inspect.push(d);
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the memory project, its notes and its boundary are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try one order: v1 and v2, live.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "first", "turn", "inspect", "scoreboard", "summary", "router_unavailable", "done", "error"];
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

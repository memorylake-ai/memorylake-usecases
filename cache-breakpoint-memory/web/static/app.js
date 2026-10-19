/* Cache-breakpoint memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const usd = (x, d = 6) => Number(x || 0).toFixed(d);

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "24 facts, a boundary, prices", view: "setup" },
    { n: 3, title: "Four requests", sub: "where the breakpoint sits", view: "layouts" },
    { n: 4, title: "Traffic", sub: "12 questions × A–D", view: "traffic" },
    { n: 5, title: "The bill", sub: "same answers, four prices", view: "bill" },
    { n: 6, title: "Too short", sub: "a breakpoint that does nothing", view: "short" },
  ];
  const fresh = () => ({ prices: null, layouts: null, qs: [], bill: null, short: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "4", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = () => S.data?.all || ["plain", "system", "user", "router"];
  const ARM = (a) => (S.data?.arms || {})[a] || a;
  const SH = (a) => (S.data?.short || {})[a] || a;
  const hit = (r) => (r.read || 0) > 0;
  const mark = (r) => r.empty ? '<span class="mark empty" title="MemoryRouter attached nothing">∅</span>'
    : r.ok === true ? '<span class="mark ok" title="right">✓</span>' : r.ok === false ? '<span class="mark bad" title="wrong">✗</span>' : '<span class="mark">·</span>';
  const wPill = (r) => r.written ? `<span class="pill err">${fmt(r.written)} written</span>` : '<span class="pill">0 written</span>';
  const rPill = (r) => r.read ? `<span class="pill ok">${fmt(r.read)} read</span>` : '<span class="pill">0 read</span>';

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>then the next question</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, layouts, traffic, bill, short, try: tryView }[S.view];
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
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (64 MemoryRouter calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-cbp")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A cached playbook, a memory block — and what the cache actually does with them</h1>
      <p class="lead">${esc(d.org || "Larkspan Home")} (a fictional retailer) runs a returns assistant, ${esc(d.assistant || "Wren")}, on ${esc(d.model || "claude-haiku-4-5")}. Every request carries the same returns playbook (${fmt(d.playbook_words || 5396)} words) with an Anthropic <code>cache_control</code> breakpoint, plus what ${esc(d.assistant || "Wren")} remembers about the customer.
      Twelve questions go through four requests: A no breakpoint · B breakpoint on the playbook, memory as a second system block · C breakpoint on the playbook, memory in the user turn · D breakpoint on the playbook, MemoryRouter attaching memory (<code>--boundary</code>).
      Every reply reports what was written to the cache, read from it, and sent uncached; the account's own <code>router models</code> list prices each part.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 64 calls on claude-haiku-4-5 per run, about 0.25 USD.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/persistent-context-for-claude-api-apps" target="_blank" rel="noopener">memorylake.ai › Persistent context for Claude API apps</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = S.data || {}, p = S.prices;
    return `<h1>Six customers, four facts each, one boundary</h1>
      <p class="lead">Every fact is pinned verbatim with <code>fact add</code> into one project, “${esc(d.project_name)}”. The boundary covers that project: it is what arm D's MemoryRouter calls read. A, B and C read the same project with <code>search</code>.</p>
      ${p ? `<div class="card"><h3 style="margin-top:0">Price list (<code>memorylake router models</code>)</h3><dl class="kv"><dt>${esc(p.model)} input</dt><dd>${p.input} USD / 1M tokens</dd>
        <dt>cache write (5 min)</dt><dd>${p.write} USD / 1M tokens <span class="muted">(${(p.write / p.input).toFixed(2)}× input)</span></dd>
        <dt>cache read</dt><dd>${p.read} USD / 1M tokens <span class="muted">(${(p.read / p.input).toFixed(2)}× input)</span></dd><dt>output</dt><dd>${p.output} USD / 1M tokens</dd></dl></div>` : ""}
      <div class="grid2">${(d.customers || []).map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.name)}</h3>
        ${c.facts.map((f) => `<div class="small">· ${esc(f)}</div>`).join("")}</div>`).join("")}</div>`;
  }

  function layouts() {
    const d = S.data || {};
    const blk = (cls, t) => `<span class="blk ${cls}">${esc(t)}</span>`;
    const bp = '<b class="bad-ink" title="cache_control breakpoint">◆</b>';
    const mem = blk("mem", `memory: top-${d.top_k || 5} facts`);
    const rows = { plain: ["system", blk("pb", "playbook"), mem, "· user", blk("q", "question")],
      system: ["system", blk("pb", "playbook") + bp, mem, "· user", blk("q", "question")],
      user: ["system", blk("pb", "playbook") + bp, "· user", mem, blk("q", "question")],
      router: ["system", blk("pb", "playbook") + bp, "· user", blk("q", "question"), blk("rt", "MemoryRouter attaches memory")] };
    return `<h1>Four ways to build ${esc(d.assistant || "Wren")}'s request</h1>
      <p class="lead">◆ marks <code>"cache_control": {"type": "ephemeral"}</code>: the cache breakpoint. B is the layout Anthropic's caching guide describes — stable block first, breakpoint on it, the changing part after it. A, B and C carry the same memory block from one <code>search</code> per question.</p>
      <div class="card">${ARMS().map((a) => `<div class="row" style="margin:8px 0;gap:6px;flex-wrap:wrap"><b style="width:300px">${esc(ARM(a))}</b>${rows[a].map((x) => x.startsWith("<") ? x : `<span class="muted small">${x}</span>`).join(" ")}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">The playbook (the cached part)</h3><pre class="small mono" style="white-space:pre-wrap;max-height:260px;overflow:auto">${esc(d.playbook || "")}</pre></div>`;
  }

  function traffic() {
    if (!S.qs.length) return unavailable() || (running() ? waiting("Traffic") : empty("Traffic"));
    const n = S.qs.length, tot = S.qs[0]?.total || 12;
    const cnt = (a, f) => S.qs.reduce((k, q) => k + q.rows.filter((r) => r.arm === a && f(r)).length, 0);
    return `<h1>Cache reads so far: ${ARMS().map((a) => `${SH(a)} ${cnt(a, hit)}/${n}`).join(" · ")}</h1>
      <p class="lead">${n} of ${tot} questions. Each arm's playbook starts with its own “Reference copy” line, so no arm can read what another arm wrote. B wrote the cache ${cnt("system", (r) => r.written > 0)} time(s) out of ${n}.</p>
      ${S.qs.slice().reverse().map((q) => `<div class="card"><h3 style="margin-top:0">Q${q.n} · “${esc(q.rows[0].question)}”</h3><table class="lint plain"><thead><tr><th>arm</th><th>written</th><th>read</th><th class="num">uncached</th><th class="num">USD per 1M calls</th><th></th><th>reply</th></tr></thead><tbody>
        ${q.rows.map((r) => `<tr class="${r.arm === "system" ? "hl" : ""}"><td class="small"><b>${esc(ARM(r.arm))}</b></td><td>${wPill(r)}</td><td>${rPill(r)}</td><td class="num small">${fmt(r.fresh)}</td><td class="num small">${fmt(Math.round(r.usd * 1e6))}</td><td>${mark(r)}</td><td class="small">${esc(r.reply)}</td></tr>`).join("")}</tbody></table></div>`).join("")}`;
  }

  function bill() {
    if (!S.bill) return unavailable() || (running() ? waiting("The bill") : empty("The bill"));
    const B = S.bill.bill, A = B.plain, b = B.system;
    const per = (x) => x.usd / Math.max(x.calls, 1) * 1e6;
    const ratio = (x) => A.usd ? (x.usd / A.usd).toFixed(2) + "×" : "–";
    return `<h1>Per million calls: ${ARMS().map((a) => `${SH(a)} ${fmt(Math.round(per(B[a])))} USD`).join(" · ")}</h1>
      <p class="lead">B carried the breakpoint and paid ${ratio(b)} what A paid without one: it wrote the cache ${b.writes} of ${b.calls} times and read it ${b.reads} times. C and D wrote the playbook once and read it ${B.user.reads} and ${B.router.reads} times.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th class="num">written</th><th class="num">read</th><th class="num">uncached</th><th class="num">writes</th><th class="num">reads</th><th class="num">right</th><th class="num">USD, these calls</th><th class="num">USD per 1M calls</th><th class="num">vs A</th></tr></thead><tbody>
        ${ARMS().map((a) => { const x = B[a]; return `<tr class="${a === "system" ? "hl" : ""}"><td><b>${esc(ARM(a))}</b></td><td class="num">${fmt(x.written)}</td><td class="num">${fmt(x.read)}</td><td class="num">${fmt(x.fresh)}</td>
          <td class="num">${x.writes}/${x.calls}</td><td class="num"><b class="${x.reads || a === "plain" ? "" : "bad-ink"}">${x.reads}/${x.calls}</b></td><td class="num">${x.right}/${x.calls - x.empty}${x.empty ? ` <span class="muted">∅${x.empty}</span>` : ""}</td><td class="num">${usd(x.usd)}</td><td class="num"><b>${fmt(Math.round(per(x)))}</b></td><td class="num">${ratio(x)}</td></tr>`; }).join("")}</tbody></table>
      <p class="small muted" style="margin-bottom:0">Prices: input ${S.bill.prices.input} · cache write ${S.bill.prices.write} · cache read ${S.bill.prices.read} · output ${S.bill.prices.output} USD per million tokens, from <code>router models</code>.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run (including 12 no-boundary baselines for ∅ and 4 for step 6): ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function short() {
    if (!S.short) return unavailable() || (running() ? waiting("Too short") : empty("Too short"));
    const d = S.data || {};
    const row = (name, words, calls) => calls.map((c, i) => `<tr class="${name === "trimmed" ? "hl" : ""}"><td><b>${name}</b> <span class="muted small">${fmt(words)} words</span></td><td class="num">${i + 1}</td><td>${wPill(c)}</td><td>${rPill(c)}</td><td class="num">${fmt(c.fresh)}</td></tr>`).join("");
    const t = S.short.trimmed || [], ignored = t.length && t.every((c) => !c.written && !c.read);
    return `<h1>${ignored ? "The trimmed playbook was never cached" : "The trimmed playbook was cached too"}</h1>
      <p class="lead">The same breakpoint on two playbooks, each sent twice in a row. ${ignored ? `The trimmed one (${fmt(t[0].fresh)} prompt tokens) is below the model's minimum cacheable length: <code>cache_control</code> is ignored and the reply says nothing about it — only the zeros do.` : ""}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>playbook</th><th class="num">call</th><th>written</th><th>read</th><th class="num">uncached</th></tr></thead><tbody>
        ${row("full", d.playbook_words, S.short.full || [])}${row("trimmed", d.playbook_short_words, t)}</tbody></table></div>`;
  }

  function tryView() {
    const ready = !!S.world, pool = S.data?.questions || [];
    return `<h1>Ask one — then the next question for the same customer</h1>
      <p class="lead">A question number, or any question that names a customer, through A–D with a fresh run id; then the customer's other question, the way traffic arrives. Round 2 shows the difference: C and D read the playbook that round 1 wrote; B writes its whole system prompt again, because its memory block changed.</p>
      <div class="card"><label for="t-q">question number (1–12) or question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${pool.map((q, i) => `<button class="chip" data-q="${i + 1}">${i + 1} · ${esc(q.text)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div id="try-log">${S.tries.slice().reverse().map((x) => `<h3>“${esc(x.rows[0]?.question || x.question)}”</h3><table class="lint plain"><thead><tr><th>round</th><th>arm</th><th>written</th><th>read</th><th class="num">uncached</th><th></th><th>reply</th></tr></thead><tbody>
          ${x.rows.map((r) => `<tr class="${r.arm === "system" ? "hl" : ""}"><td class="small">${r.round}</td><td class="small"><b>${esc(ARM(r.arm))}</b></td><td>${wPill(r)}</td><td>${rPill(r)}</td><td class="num small">${fmt(r.fresh)}</td><td>${mark(r)}</td><td class="small">${esc(r.reply)}</td></tr>`).join("")}</tbody></table>`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows });
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
  function resetView() { Object.assign(S, fresh()); }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary, its 24 facts and the project?")) return;
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
    else if (k === "prices") S.prices = d;
    else if (k === "layouts") S.layouts = d;
    else if (k === "question") S.qs = S.qs.filter((q) => q.n !== d.n).concat([d]);
    else if (k === "bill") S.bill = d;
    else if (k === "short") S.short = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the 24 facts and the project are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: a question through A–D, then the next one.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "prices", "layouts", "question", "bill", "short",
    "summary", "router_unavailable", "asked", "facts_view", "done", "error"];
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
    show(state.status === "running" ? S.view : S.bill ? "bill" : "connect");
    subscribe();
  })();
})();

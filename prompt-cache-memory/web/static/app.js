/* Prompt-cache memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const usd = (x, d = 6) => Number(x || 0).toFixed(d);

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "60 facts, a boundary, prices", view: "setup" },
    { n: 3, title: "Four prompts", sub: "where the memory sits", view: "layouts" },
    { n: 4, title: "Traffic", sub: "12 questions × A–D", view: "traffic" },
    { n: 5, title: "The bill", sub: "same memory, four prices", view: "bill" },
    { n: 6, title: "B next to C", sub: "same tokens, cut elsewhere", view: "pairs" },
  ];
  const fresh = () => ({ prices: null, layouts: null, qs: [], bill: null, pairs: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "4", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = () => S.data?.all || ["stuffed", "front", "back", "router"];
  const ARM = (a) => (S.data?.arms || {})[a] || a;
  const SH = (a) => (S.data?.short || {})[a] || a;
  const hit = (r) => (r.cached || 0) > 0;
  const mark = (r) => r.empty ? '<span class="mark empty" title="MemoryRouter attached nothing">∅</span>'
    : r.ok === true ? '<span class="mark ok" title="right">✓</span>' : r.ok === false ? '<span class="mark bad" title="wrong">✗</span>' : '<span class="mark">·</span>';
  const seenTxt = (m) => m == null ? '<span class="muted small">head new</span>' : `<span class="small">head sent ${m < 1 ? "<1" : Math.round(m)} min ago</span>`;
  const cachePill = (r) => r.cached == null ? '<span class="pill">n/a</span>'
    : hit(r) ? `<span class="pill ok">${fmt(r.cached)} cached</span>` : '<span class="pill err">0 cached</span>';

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A–D, twice</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, layouts, traffic, bill, pairs, try: tryView }[S.view];
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
          <span class="muted small">about 4 minutes (60 MemoryRouter calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-pcc")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Same memory, same answers — and a cache that never hits</h1>
      <p class="lead">${esc(d.org || "Tessavane Freight")} (a fictional carrier) runs a support assistant, ${esc(d.assistant || "Dock")}. Every request carries the same handling playbook (${fmt(d.playbook_words || 969)} words, shared by all customers) plus what ${esc(d.assistant || "Dock")} remembers about the customer asking.
      Twelve questions go through four ways of building the prompt: A the ticket history pasted in front · B <code>search</code> top-${d.top_k || 5} in front of the playbook · C the same facts after it · D the playbook alone, with MemoryRouter attaching memory (<code>--boundary</code>).
      The provider reports how much of each prompt it read from its cache (<code>cached_tokens</code>); the account's own <code>router models</code> list prices it.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 60 calls on gpt-4o-mini per run, under 0.02 USD.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/cost-optimized-agent-memory-at-scale" target="_blank" rel="noopener">memorylake.ai › Cost-optimized agent memory at scale</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = S.data || {}, p = S.prices;
    return `<h1>Six customers, ten facts each, one boundary</h1>
      <p class="lead">Every fact is pinned verbatim with <code>fact add</code> into one project, “${esc(d.project_name)}”. The boundary covers that project: it is what arm D's MemoryRouter calls read. B and C read the same project with <code>search</code>.</p>
      ${p ? `<div class="card"><h3 style="margin-top:0">Price list (<code>memorylake router models</code>)</h3><dl class="kv"><dt>${esc(p.model)} input</dt><dd>${p.input} USD / 1M tokens</dd>
        <dt>cache_read</dt><dd>${p.cache_read} USD / 1M tokens <span class="muted">(${Math.round(100 * p.cache_read / p.input)}% of input)</span></dd><dt>output</dt><dd>${p.output} USD / 1M tokens</dd></dl></div>` : ""}
      <div class="grid2">${(d.customers || []).map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.name)} <span class="muted small">${esc(c.company)}</span></h3>
        ${c.facts.slice(0, 4).map((f) => `<div class="small">· ${esc(f.replace(/^.*?\) /, ""))}</div>`).join("")}<div class="small muted">… ${c.facts.length - 4} more</div></div>`).join("")}</div>`;
  }

  function layouts() {
    const d = S.data || {};
    const blk = (cls, t) => `<span class="blk ${cls}">${esc(t)}</span>`;
    const rows = { stuffed: [blk("mem", "customer file: every past ticket"), blk("pb", "playbook")],
      front: [blk("mem", `top-${d.top_k || 5} facts for this question`), blk("pb", "playbook")],
      back: [blk("pb", "playbook"), blk("mem", `top-${d.top_k || 5} facts for this question`)],
      router: [blk("pb", "playbook"), blk("rt", "MemoryRouter attaches memory")] };
    return `<h1>Four ways to build ${esc(d.assistant || "Dock")}'s prompt</h1>
      <p class="lead">The playbook is the same bytes in every request. The memory part changes with every question. Providers bill the longest prefix a request shares with a recent one at the cache price (OpenAI: from 1,024 tokens up) — so what comes first decides whether anything can be shared.</p>
      <div class="card">${ARMS().map((a) => `<div class="row" style="margin:8px 0"><b style="width:280px">${esc(ARM(a))}</b>${rows[a].join(" ")}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">The playbook (the shared part)</h3><pre class="small mono" style="white-space:pre-wrap;max-height:260px;overflow:auto">${esc(d.playbook || "")}</pre></div>`;
  }

  function traffic() {
    if (!S.qs.length) return unavailable() || (running() ? waiting("Traffic") : empty("Traffic"));
    const n = S.qs.length, tot = S.qs[0]?.total || 12;
    const hits = (a) => S.qs.reduce((k, q) => k + q.rows.filter((r) => r.arm === a && hit(r)).length, 0);
    return `<h1>Cache hits so far: ${ARMS().map((a) => `${SH(a)} ${hits(a)}/${n}`).join(" · ")}</h1>
      <p class="lead">${n} of ${tot} questions, drawn from a pool of 60 (one per fact). A hit needs the start of the prompt (~1,024 tokens) to have been sent in the last minutes — the demo logs what each request started with. One <code>search</code> per question feeds both B and C, so their memory block is identical; the arm order rotates every question.</p>
      ${S.qs.slice().reverse().map((q) => `<div class="card"><h3 style="margin-top:0">Q${q.n} · “${esc(q.rows[0].question)}” <span class="muted small">#${q.rows[0].pool} of the pool</span></h3><table class="lint plain"><thead><tr><th>arm</th><th class="num">prompt</th><th>cache</th><th>start of the prompt</th><th></th><th>reply</th></tr></thead><tbody>
        ${q.rows.map((r) => `<tr class="${r.arm === "front" ? "hl" : ""}"><td class="small"><b>${esc(ARM(r.arm))}</b></td><td class="num small">${fmt(r.prompt_tokens)}</td><td>${cachePill(r)}</td><td>${seenTxt(r.seen)}</td><td>${mark(r)}</td><td class="small">${esc(r.reply)}</td></tr>`).join("")}</tbody></table></div>`).join("")}`;
  }

  function bill() {
    if (!S.bill) return unavailable() || (running() ? waiting("The bill") : empty("The bill"));
    const B = S.bill.bill, f = B.front, k = B.back;
    const per = (b) => b.usd / Math.max(b.calls, 1) * 1e6;
    return `<h1>Per million calls: ${ARMS().map((a) => `${SH(a)} ${fmt(Math.round(per(B[a])))} USD`).join(" · ")}</h1>
      <p class="lead">B and C sent ${fmt(f.prompt)} vs ${fmt(k.prompt)} prompt tokens and were right ${f.right} vs ${k.right} times. B paid every token at the full input price; C and D paid the playbook at the cache price.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th class="num">prompt tokens</th><th class="num">cached</th><th class="num">share</th><th class="num">cache hits</th><th class="num">right</th><th class="num">USD, these calls</th><th class="num">USD per 1M calls</th><th class="num">without any cache</th><th class="num">hits, head new</th><th class="num">hits, head sent before</th></tr></thead><tbody>
        ${ARMS().map((a) => { const b = B[a]; return `<tr class="${a === "front" ? "hl" : ""}"><td><b>${esc(ARM(a))}</b></td><td class="num">${fmt(b.prompt)}</td><td class="num">${fmt(b.cached)}</td><td class="num">${Math.round(100 * b.share)}%</td>
          <td class="num"><b class="${b.hits ? "" : "bad-ink"}">${b.hits}/${b.calls}</b></td><td class="num">${b.right}/${b.calls - b.empty}${b.empty ? ` <span class="muted">∅${b.empty}</span>` : ""}</td><td class="num">${usd(b.usd)}</td><td class="num"><b>${fmt(Math.round(per(b)))}</b></td><td class="num muted">${fmt(Math.round(b.usd_nocache / Math.max(b.calls, 1) * 1e6))}</td><td class="num">${b.new_hits}/${b.new}</td><td class="num">${b.seen_hits}/${b.calls - b.new}</td></tr>`; }).join("")}</tbody></table>
      <p class="small muted" style="margin-bottom:0">Prices: input ${S.bill.prices.input} · cache_read ${S.bill.prices.cache_read} · output ${S.bill.prices.output} USD per million tokens, from <code>router models</code>. “Without any cache” prices every prompt token at the input price.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run (including 12 no-boundary baselines for ∅): ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function pairs() {
    if (!S.pairs) return unavailable() || (running() ? waiting("B next to C") : empty("B next to C"));
    const P = S.pairs;
    return `<h1>Same tokens ${P.same_tokens}/${P.n} · same reply ${P.same_reply}/${P.n} · cache hits B ${P.b_hits}/${P.n}, C ${P.c_hits}/${P.n}</h1>
      <p class="lead">B and C carry the same playbook and the same retrieved facts; only the order differs. The answer cannot tell them apart. <code>usage.prompt_tokens_details.cached_tokens</code> can.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th class="num">B prompt</th><th class="num">B cached</th><th class="num">C prompt</th><th class="num">C cached</th><th>reply</th></tr></thead><tbody>
        ${P.pairs.map((x) => `<tr><td class="small">Q${x.q}</td><td class="num">${fmt(x.tokens[0])}</td><td class="num"><b class="${x.cached[0] ? "" : "bad-ink"}">${fmt(x.cached[0])}</b></td><td class="num">${fmt(x.tokens[1])}</td><td class="num"><b>${fmt(x.cached[1])}</b></td><td class="small">${x.same_reply ? "same, word for word" : "worded differently"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function tryView() {
    const ready = !!S.world, pool = S.data?.questions || [];
    const nums = S.qs.length ? S.qs.map((q) => q.rows[0].pool) : [1, 12, 23, 37, 44, 57];
    const qs = nums.map((k) => ({ k, text: pool[k - 1]?.text || "" }));
    return `<h1>Ask one — through A–D, twice in a row</h1>
      <p class="lead">A question number, or any question that names a customer. The second round sends the same question again: if <code>search</code> returns the same facts, B's prompt is byte-identical and can hit — the only way B shares a prefix. In real traffic the next question retrieves a different block.</p>
      <div class="card"><label for="t-q">question number (1–60) or question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${qs.map((q) => `<button class="chip" data-q="${q.k}">${q.k} · ${esc(q.text)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div id="try-log">${S.tries.slice().reverse().map((x) => `<h3>“${esc(x.rows[0]?.question || x.question)}”</h3><table class="lint plain"><thead><tr><th>round</th><th>arm</th><th class="num">prompt</th><th>cache</th><th>start of the prompt</th><th></th><th>reply</th></tr></thead><tbody>
          ${x.rows.map((r) => `<tr><td class="small">${r.round}</td><td class="small"><b>${esc(ARM(r.arm))}</b></td><td class="num small">${fmt(r.prompt_tokens)}</td><td>${cachePill(r)}</td><td>${seenTxt(r.seen)}</td><td>${mark(r)}</td><td class="small">${esc(r.reply)}</td></tr>`).join("")}</tbody></table>`).join("")}</div></div>`;
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
    if (!confirm("Delete the demo's boundary, its 60 facts and the project?")) return;
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
    else if (k === "pairs") S.pairs = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the 60 facts and the project are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question through A–D, twice.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "prices", "layouts", "question", "bill", "pairs",
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

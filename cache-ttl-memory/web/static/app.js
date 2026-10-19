/* Cache-TTL memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const usd = (x, d = 6) => Number(x || 0).toFixed(d);

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "yesterday's notes, prices", view: "setup" },
    { n: 3, title: "Four cache settings", sub: "none · 5 min · 1 h · ping", view: "layouts" },
    { n: 4, title: "Session 1", sub: "the morning, × A–D", view: "s1" },
    { n: 5, title: "The break", sub: "5½ minutes away", view: "brk" },
    { n: 6, title: "Session 2", sub: "back at the keyboard", view: "s2" },
    { n: 7, title: "The bill", sub: "same answers, four prices", view: "bill" },
  ];
  const fresh = () => ({ prices: null, layouts: null, reqs: [], memory: null, tick: null, ping: null, bill: null, even: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Can I store sessions in Redis for the auth refactor?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = () => S.data?.all || ["plain", "m5", "h1", "ping"];
  const ARM = (a) => (S.data?.arms || {})[a] || a;
  const SH = (a) => (S.data?.short || {})[a] || a;
  const clock = (s) => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  const mark = (r) => r.ok === true ? '<span class="mark ok" title="answered from memory">✓</span>' : r.ok === false ? '<span class="mark bad" title="missed">✗</span>' : '<span class="mark">·</span>';
  const wPill = (r) => r.written ? `<span class="pill err">${fmt(r.written)} written${r.w1h ? " (1 h)" : ""}</span>` : '<span class="pill">0 written</span>';
  const rPill = (r) => r.read ? `<span class="pill ok">${fmt(r.read)} read</span>` : '<span class="pill">0 read</span>';

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask Kit<small>with the team's memory</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, layouts, s1: () => session(1), brk, s2: () => session(2), bill, try: tryView }[S.view];
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
          <span class="muted small">about 7 minutes, 5½ of them a real break · everything it creates is prefixed <code>${esc(d.prefix || "mlu-ctm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The memory survives the coffee break. Does the prompt cache?</h1>
      <p class="lead">${esc(d.org || "Saltmarrow Pay")} (a fictional payments company) runs a coding agent, ${esc(d.assistant || "Kit")}, on ${esc(d.model || "claude-haiku-4-5")} over MemoryRouter. Every request carries the same repository guide (${fmt(d.guide_words || 6133)} words) with an Anthropic <code>cache_control</code> breakpoint, and MemoryRouter attaches what the team decided in past sessions (<code>--boundary</code>).
      ${esc(d.developer || "Noor")} works a short session, steps away for ${clock(d.gap || 330)}, and comes back. Every request goes through four cache settings: A none · B the default 5-minute TTL · C <code>"ttl": "1h"</code> · D 5 minutes plus one keep-alive ping during the break.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 33 calls on claude-haiku-4-5 per run, about 0.21 USD.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/persistent-memory-for-ai-coding-agents" target="_blank" rel="noopener">memorylake.ai › Persistent memory for AI coding agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = S.data || {}, p = S.prices;
    return `<h1>Yesterday's session notes, one boundary</h1>
      <p class="lead">${esc(d.assistant || "Kit")}'s decisions from yesterday are pinned verbatim with <code>fact add</code> into one project, “${esc(d.project_name)}”. A boundary covers the project; every request in every arm passes it to MemoryRouter.</p>
      ${p ? `<div class="card"><h3 style="margin-top:0">Price list (<code>memorylake router models</code>)</h3><dl class="kv"><dt>${esc(p.model)} input</dt><dd>${p.input} USD / 1M tokens</dd>
        <dt>cache write, 5-minute TTL</dt><dd>${p.write5} USD / 1M <span class="muted">(${(p.write5 / p.input).toFixed(2)}× input)</span></dd>
        <dt>cache write, 1-hour TTL</dt><dd>${p.write1h} USD / 1M <span class="muted">(${(p.write1h / p.input).toFixed(2)}× input)</span></dd>
        <dt>cache read</dt><dd>${p.read} USD / 1M <span class="muted">(${(p.read / p.input).toFixed(2)}× input)</span></dd><dt>output</dt><dd>${p.output} USD / 1M</dd></dl></div>` : ""}
      <div class="card"><h3 style="margin-top:0">Pinned before session 1</h3>${(d.facts_yesterday || []).map((f) => `<div class="small">· ${esc(f)}</div>`).join("")}</div>`;
  }

  function layouts() {
    const d = S.data || {};
    const blk = (cls, t) => `<span class="blk ${cls}">${esc(t)}</span>`;
    const rows = { plain: [blk("pb", "repo guide"), blk("rt", "memory (--boundary)"), blk("q", "question")],
      m5: [blk("pb", "repo guide") + '<b class="bad-ink">◆</b> <code class="small">{"type":"ephemeral"}</code>', blk("rt", "memory"), blk("q", "question")],
      h1: [blk("pb", "repo guide") + '<b class="ok-ink">◆</b> <code class="small">{"type":"ephemeral","ttl":"1h"}</code>', blk("rt", "memory"), blk("q", "question")],
      ping: [blk("pb", "repo guide") + '<b class="bad-ink">◆</b> <code class="small">{"type":"ephemeral"}</code>', blk("rt", "memory"), blk("q", "question"), `<span class="muted small">+ one 1-token call ${clock(d.ping_at || 240)} into the break</span>`] };
    return `<h1>Four ways to cache ${esc(d.assistant || "Kit")}'s repo guide</h1>
      <p class="lead">◆ marks the cache breakpoint. A cache entry lives 5 minutes by default, an hour with <code>"ttl": "1h"</code>; every read starts its clock again. MemoryRouter attaches the memory after the system prompt, so the cached guide is the same bytes on every request.</p>
      <div class="card">${ARMS().map((a) => `<div class="row" style="margin:8px 0;gap:6px;flex-wrap:wrap"><b style="width:300px">${esc(ARM(a))}</b>${rows[a].join(" ")}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">The repo guide (the cached part)</h3><pre class="small mono" style="white-space:pre-wrap;max-height:260px;overflow:auto">${esc(d.guide || "")}</pre></div>`;
  }

  function reqTable(q, first) {
    return `<div class="card"><h3 style="margin-top:0">S${q.session}·${q.n} · “${esc(q.rows[0].question)}”</h3><table class="lint plain"><thead><tr><th>arm</th><th>written</th><th>read</th><th class="num">uncached</th><th class="num">USD per 1M calls</th><th></th><th>reply</th></tr></thead><tbody>
      ${q.rows.map((r) => `<tr class="${first && r.arm === "m5" ? "hl" : ""}"><td class="small"><b>${esc(ARM(r.arm))}</b></td><td>${wPill(r)}</td><td>${rPill(r)}</td><td class="num small">${fmt(r.fresh)}</td><td class="num small">${fmt(Math.round(r.usd * 1e6))}</td><td>${mark(r)}</td><td class="small">${esc(r.reply)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function session(n) {
    const qs = S.reqs.filter((q) => q.session === n), d = S.data || {};
    const h = n === 1 ? "Session 1" : "Session 2";
    if (!qs.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const total = qs[0].total;
    if (n === 1) {
      const reads = (a) => qs.reduce((k, q) => k + q.rows.filter((r) => r.arm === a && r.read).length, 0);
      return `<h1>Session 1: B, C and D read the guide from the second request on</h1>
        <p class="lead">${qs.length} of ${total} requests. Within a session every cache setting works: cache reads ${ARMS().map((a) => `${SH(a)} ${reads(a)}/${qs.length}`).join(" · ")}. C's first write is billed at the 1-hour price; B's and D's at the 5-minute price. The last two requests are ${esc(d.developer || "Noor")}'s decisions for the session log.</p>
        ${qs.slice().reverse().map((q) => reqTable(q, false)).join("")}`;
    }
    const f = qs.find((q) => q.n === 1), row = (a) => f.rows.find((r) => r.arm === a);
    const b = row("m5"), c = row("h1"), dd = row("ping");
    const right = (a) => qs.reduce((k, q) => k + q.rows.filter((r) => r.arm === a && r.ok).length, 0);
    return `<h1>${b.read ? "B's cache survived the break this time" : `After the break, B wrote the guide again: ${fmt(b.written)} tokens`}</h1>
      <p class="lead">First request back: B ${b.read ? "read" : "<b>wrote</b>"} · C ${c.read ? "read" : "wrote"} · D ${dd.read ? "read" : "wrote"}. The memory was there in every arm — answers from this morning's decisions: ${ARMS().map((a) => `${SH(a)} ${right(a)}/${qs.length}`).join(" · ")}.</p>
      ${qs.slice().reverse().map((q) => reqTable(q, q.n === 1)).join("")}`;
  }

  function brk() {
    const d = S.data || {}, t = S.tick, gap = d.gap || 330;
    if (!S.memory && !t) return unavailable() || (running() ? waiting("The break") : empty("The break"));
    const el = t ? Math.min(t.elapsed, gap) : 0, pct = Math.round(el / gap * 100);
    return `<h1>${t && t.elapsed >= gap - 1 ? `Break over: ${clock(gap)} away` : `The break: ${clock(el)} of ${clock(gap)}`}</h1>
      <p class="lead">Session 1's decisions are now in memory. Then nothing happens for ${clock(gap)} — longer than the default 5-minute cache TTL — except D's one keep-alive call at ${clock(d.ping_at || 240)}.</p>
      <div class="card"><div class="bar"><span style="width:${pct}%"></span><i style="left:${Math.round(300 / gap * 100)}%" title="5-minute TTL"></i><i class="p" style="left:${Math.round((d.ping_at || 240) / gap * 100)}%" title="D's ping"></i></div>
        <div class="row spread small muted" style="margin-top:6px"><span>0:00</span><span>D pings at ${clock(d.ping_at || 240)} · 5-minute entries expire at 5:00</span><span>${clock(gap)}</span></div>
        ${S.ping ? `<p class="small" style="margin-bottom:0"><b>D's ping at ${clock(S.ping.offset)}</b>: ${rPill(S.ping)} ${wPill(S.ping)} · ${fmt(S.ping.fresh)} uncached · ${fmt(Math.round(S.ping.usd * 1e6))} USD per 1M pings — the read starts D's 5-minute clock again.</p>` : ""}</div>
      ${S.memory ? `<div class="card"><h3 style="margin-top:0">What ${esc(d.assistant || "Kit")} remembers now (<code>fact list</code>, ${S.memory.length} notes)</h3>${S.memory.map((f) => `<div class="small">· ${esc(f)}</div>`).join("")}</div>` : ""}`;
  }

  function bill() {
    if (!S.bill) return unavailable() || (running() ? waiting("The bill") : empty("The bill"));
    const B = S.bill.bill, A = B.plain;
    const ratio = (x) => A.usd ? (x.usd / A.usd).toFixed(2) + "×" : "–";
    const cheapest = ARMS().reduce((m, a) => B[a].usd < B[m].usd ? a : m, "plain");
    const first = (a) => { const f = B[a].s2_first; return a === "plain" || !f ? "—" : f.read ? `<span class="pill ok">read ${fmt(f.read)}</span>` : `<span class="pill err">wrote ${fmt(f.written)}</span>`; };
    const ev = S.even;
    return `<h1>Total for both sessions: ${ARMS().map((a) => `${SH(a)} ${ratio(B[a])}`).join(" · ")} of A</h1>
      <p class="lead">Same questions, same memory, the same answers. ${SH(cheapest)} was cheapest on this run. ${B.m5.s2_first && !B.m5.s2_first.read ? `B paid the write price twice — once per session — because its entry expired during the ${clock(S.bill.gap)} break.` : "B's entry was still there after the break on this run."}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th class="num">session 1</th><th class="num">break</th><th class="num">session 2</th><th class="num">total USD</th><th class="num">vs A</th><th>first call after the break</th><th class="num">answered</th></tr></thead><tbody>
        ${ARMS().map((a) => { const x = B[a]; return `<tr class="${a === "m5" ? "hl" : ""}"><td><b>${esc(ARM(a))}</b></td><td class="num">${usd(x.s1, 5)}</td><td class="num">${usd(x.break, 5)}</td><td class="num">${usd(x.s2, 5)}</td><td class="num"><b>${usd(x.usd, 5)}</b></td><td class="num">${ratio(x)}</td><td>${first(a)}</td><td class="num">${x.right}/${x.judged}</td></tr>`; }).join("")}</tbody></table>
      <p class="small muted" style="margin-bottom:0">Prices from <code>router models</code>: input ${S.bill.prices.input} · cache write 5 min ${S.bill.prices.write5} · 1 h ${S.bill.prices.write1h} · read ${S.bill.prices.read} · output ${S.bill.prices.output} USD per million tokens.</p></div>
      ${ev ? `<div class="card"><h3 style="margin-top:0">Other break lengths — computed from the prices, not measured</h3><table class="lint plain"><thead><tr><th>break</th><th class="num">A</th><th class="num">B 5 min</th><th class="num">C 1 h</th><th class="num">D ping</th><th>cheapest</th></tr></thead><tbody>
        ${ev.rows.map((r) => { const best = ARMS().reduce((m, k) => r[k] < r[m] ? k : m, "plain"); return `<tr><td>${r.minutes} min</td><td class="num">${usd(r.plain, 5)}</td><td class="num">${usd(r.m5, 5)}</td><td class="num">${usd(r.h1, 5)}</td><td class="num">${usd(r.ping, 5)} <span class="muted small">(${r.pings} ping${r.pings === 1 ? "" : "s"})</span></td><td><b>${SH(best)}</b></td></tr>`; }).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">USD for the guide (${fmt(ev.tokens)} tokens) over ${ev.n1} requests, the break, ${ev.n2} requests. Under 5 minutes the default is enough; for breaks under about ${ev.limit} minutes D's pings cost less than C's 1-hour write; longer than that, C; past an hour C's entry is gone too.</p></div>` : ""}
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world, pool = (S.data?.session2 || []).concat(S.data?.session1?.slice(0, 2) || []);
    return `<h1>Ask Kit — with the team's memory</h1>
      <p class="lead">One <code>router messages --boundary</code> call, the guide cached with <code>"ttl": "1h"</code>. The first ask writes the guide; ask again within the hour and it is read.</p>
      <div class="card"><label for="t-q">question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${pool.map((q) => `<button class="chip" data-q="${esc(q.text)}">${esc(q.text)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div id="try-log">${S.tries.slice().reverse().map((x) => `<h3>“${esc(x.question)}”</h3><p>${wPill(x.row)} ${rPill(x.row)} <span class="muted small">${fmt(x.row.fresh)} uncached</span></p><p class="small">${esc(x.row.reply)}</p>`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, row: r.row });
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
    if (!confirm("Delete the demo's boundary, its session notes and the project?")) return;
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
    else if (k === "progress") { term("progress", ev.text.trim()); if (d.total) S.tick = d; }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "prices") S.prices = d;
    else if (k === "layouts") S.layouts = d;
    else if (k === "request") S.reqs = S.reqs.filter((q) => !(q.n === d.n && q.session === d.session)).concat([d]);
    else if (k === "memory") S.memory = d.facts;
    else if (k === "ping") S.ping = d;
    else if (k === "bill") S.bill = d;
    else if (k === "breakeven") S.even = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the session notes and the project are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask Kit”: a question with the team's memory.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && (!["cmd", "progress", "json", "text"].includes(k) || (k === "progress" && S.view === "brk"))) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "prices", "layouts", "request", "memory", "ping", "bill", "breakeven",
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

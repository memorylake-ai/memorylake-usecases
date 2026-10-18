/* History-window memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const usd = (x, d = 4) => Number(x || 0).toFixed(d);

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "project, actors, boundary", view: "setup" },
    { n: 3, title: "Three months", sub: "10 past sessions → facts", view: "history" },
    { n: 4, title: "Three prompts", sub: "and the price list", view: "layouts" },
    { n: 5, title: "Today's session", sub: "16 turns × A, B, C", view: "session" },
    { n: 6, title: "The bill", sub: "tokens, cache, agreements", view: "bill" },
  ];
  const fresh = () => ({ prices: null, memory: null, turns: [], bill: null, unavailable: null, session: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Which day does Arnholt deliver, and to which door?", termCount: 0, lastEvent: 0, caughtUp: false, ingest: [],
  };
  const ARMS = () => S.data?.all || ["A", "B", "C"];
  const ARM = (a) => (S.data?.arms || {})[a] || a;
  const MARKS = { ok: ["ok", "✓", "right"], unknown: ["empty", "∅", "says it is not on file"], made: ["bad", "✗", "a wrong or made-up value"] };
  const mark = (g) => g ? `<span class="mark ${MARKS[g][0]}" title="${MARKS[g][2]}">${MARKS[g][1]}</span>` : "";
  const qOf = (id) => (S.data?.live || []).find((t) => t.id === id);
  const maxPrompt = () => Math.max(1, ...S.turns.flatMap((t) => ARMS().map((a) => t.arms[a]?.prompt_tokens || 0)));
  const split = (r, w) => {
    if (!r) return "";
    const max = w || maxPrompt(), c = 100 * (r.cached || 0) / max, u = 100 * (r.prompt_tokens - (r.cached || 0)) / max;
    return `<span class="split" title="${fmt(r.prompt_tokens)} prompt tokens, ${fmt(r.cached)} read from cache"><span class="c" style="width:${c}%"></span><span class="u" style="width:${u}%"></span></span>`;
  };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>a new turn through A, B, C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, history, layouts, session, bill, try: tryView }[S.view];
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
          <span class="muted small">${S.world ? "about 2 minutes (the memory is reused)" : "about 5 minutes: 10 sessions extracted, then 48 MemoryRouter calls"} · everything it creates is prefixed <code>${esc(d.prefix || "mlu-hwm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Keep only the last turns, and the agent forgets the deal — and the cache</h1>
      <p class="lead">${esc(d.person?.display || "Ines Varga")} buys for ${esc(d.org || "Tolland Hollow Brewing")} (fictional) with ${esc(d.assistant?.display || "Cask")}, an AI purchasing assistant, in one long thread. Ten sessions from July to September settle a hop contract, malt delivery days, a late-delivery credit, a glass quote, the CO2 account, label sign-off and the quarter's approval limit.
      Today's 16-turn session runs three ways: <b>A</b> the whole thread in every request · <b>B</b> only the last ${d.window_turns || 6} turns, the usual fix for A's bill · <b>C</b> the same ${d.window_turns || 6} turns plus MemoryRouter attaching what MemoryLake extracted (<code>--boundary</code>).
      Every request starts with the same ${fmt(d.playbook_words || 971)}-word playbook; the provider reports how much of each prompt it read from its cache (<code>cached_tokens</code>).</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 48 calls on gpt-4o-mini per run, about 0.02 USD.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/stop-token-bloat-from-stuffed-agent-history" target="_blank" rel="noopener">memorylake.ai › Stop token bloat from stuffed agent history</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = S.data || {}, w = S.world;
    return `<h1>One project, two actors, one boundary</h1>
      <p class="lead">The past sessions go into the project “${esc(d.project_name)}” as conversations between ${esc(d.person?.display)} and ${esc(d.assistant?.display)}. The boundary covers that project and ${esc(d.person?.display)}: it is what arm C's MemoryRouter calls read. A and B never touch MemoryLake — they are what an app without it would send.</p>
      ${w ? `<div class="card"><dl class="kv"><dt>project</dt><dd class="mono">${esc(w.project)}</dd><dt>${esc(d.person?.display)}</dt><dd class="mono">${esc(w.ines)}</dd>
        <dt>${esc(d.assistant?.display)}</dt><dd class="mono">${esc(w.cask)}</dd><dt>boundary</dt><dd class="mono">${esc(w.boundary)}</dd></dl>
        ${w.reused ? '<p class="small muted" style="margin-bottom:0">Reused from an earlier run (<code>python3 demo.py --reset</code> rebuilds it).</p>' : ""}</div>` : running() ? waiting("") : ""}`;
  }

  function history() {
    const d = S.data || {}, m = S.memory;
    const qs = (d.live || []).filter((t) => t.id);
    return `<h1>Three months of history: ${(d.sessions || []).length} sessions, ${fmt(d.past_messages)} messages</h1>
      <p class="lead">This is the thread A re-sends in every request (${fmt(d.past_words)} words). In MemoryLake each session is a conversation; extraction turns it into facts. Below: is every value today's session asks for among those facts? Checked before any answer.</p>
      ${S.ingest.length ? `<div class="card small mono">${S.ingest.map((x) => esc(x)).join("<br>")}</div>` : ""}
      ${m ? `<div class="card"><h3 style="margin-top:0">${m.facts} facts · ${Object.values(m.held).filter(Boolean).length}/${qs.length} of the values today's session needs</h3>
        <table class="lint plain"><thead><tr><th></th><th>value</th><th>agreed in</th><th>today</th></tr></thead><tbody>
        ${qs.map((q) => `<tr><td>${m.held[q.id] ? mark("ok") : mark("made")}</td><td><b>${esc(q.values)}</b></td><td class="small">${esc(q.from)} · ${esc((d.sessions || []).find((s) => s.id === q.from)?.topic)}</td><td class="small">${esc(q.text)}</td></tr>`).join("")}</tbody></table></div>`
        : running() ? waiting("") : ""}
      <div class="grid2">${(d.sessions || []).map((s) => `<div class="card"><h3 style="margin-top:0">${esc(s.id)} · ${esc(s.topic)} <span class="muted small">${esc(s.at)}</span></h3>
        <div class="chat">${s.turns.slice(0, 4).map((t) => `<div class="msg ${t.who === "ines" ? "user" : "bot"}"><span class="who">${t.who === "ines" ? esc(d.person?.display) : esc(d.assistant?.display)}</span>${esc(t.text)}</div>`).join("")}</div>
        <div class="small muted">… ${s.turns.length - 4} more messages</div></div>`).join("")}</div>`;
  }

  function layouts() {
    const d = S.data || {}, p = S.prices, n = d.window_turns || 6;
    const blk = (cls, t) => `<span class="blk ${cls}">${esc(t)}</span>`;
    const rows = { A: [blk("pb", "playbook"), blk("ses", "session line"), blk("mem", `all ${d.past_messages || 104} past messages`), blk("mem", "today so far"), blk("q", "new turn")],
      B: [blk("pb", "playbook"), blk("ses", "session line"), blk("mem", `last ${n} turns`), blk("q", "new turn")],
      C: [blk("pb", "playbook"), blk("ses", "session line"), blk("mem", `last ${n} turns`), blk("rt", "MemoryRouter attaches facts"), blk("q", "new turn")] };
    return `<h1>Three ways to build ${esc(d.assistant?.display || "Cask")}'s prompt</h1>
      <p class="lead">Providers bill the longest prefix a request shares with a recent one at the cache price (OpenAI: from 1,024 tokens up). A's thread only grows at its end, so each request starts with the whole previous one. B's window drops its oldest turn every time: after the playbook, nothing ever matches the request before. The session line (a new id each run) keeps one run from reusing another's prompts.</p>
      <div class="card">${ARMS().map((a) => `<div class="row" style="margin:8px 0"><b style="width:240px">${esc(ARM(a))}</b>${rows[a].join(" ")}</div>`).join("")}</div>
      ${p ? `<div class="card"><h3 style="margin-top:0">Price list (<code>memorylake router models</code>)</h3><dl class="kv"><dt>${esc(d.model)} input</dt><dd>${p.input} USD / 1M tokens</dd>
        <dt>cache_read</dt><dd>${p.cache_read} USD / 1M tokens <span class="muted">(${Math.round(100 * p.cache_read / p.input)}% of input)</span></dd><dt>output</dt><dd>${p.output} USD / 1M tokens</dd></dl></div>` : ""}
      <div class="card"><h3 style="margin-top:0">The playbook (the shared part)</h3><pre class="small mono" style="white-space:pre-wrap;max-height:260px;overflow:auto">${esc(d.playbook || "")}</pre></div>`;
  }

  function session() {
    if (!S.turns.length) return unavailable() || (running() ? waiting("Today's session") : empty("Today's session"));
    const max = maxPrompt(), qs = S.turns.filter((t) => t.q);
    const right = (a) => qs.filter((t) => t.arms[a]?.grade === "ok").length;
    return `<h1>Turn ${S.turns.length} of ${(S.data?.live || []).length} · earlier agreements right: ${ARMS().map((a) => `${a} ${right(a)}/${qs.length}`).join(" · ")}</h1>
      <p class="lead">Each bar is one request: <span class="key c"></span> read from the provider's cache · <span class="key u"></span> sent at the full input price. Rows marked ▶ ask for something agreed weeks ago.</p>
      <div class="card"><table class="lint plain turns-t"><thead><tr><th>turn</th>${ARMS().map((a) => `<th>${esc(ARM(a))}</th>`).join("")}</tr></thead><tbody>
        ${S.turns.map((t) => `<tr class="${t.q ? "hl" : ""}"><td class="small"><b>${t.turn}</b>${t.q ? ` ▶ ${esc(t.q)}` : ""}</td>${ARMS().map((a) => { const r = t.arms[a];
          return `<td>${r ? `${split(r, max)}<div class="small mono">${fmt(r.prompt_tokens)} · ${fmt(r.cached)} cached ${mark(r.grade)}</div>` : "—"}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
      ${qs.slice().reverse().map((t) => { const q = qOf(t.q) || {}; return `<div class="card"><h3 style="margin-top:0">Turn ${t.turn} ▶ ${esc(t.q)} · “${esc(t.said)}”</h3>
        <p class="small muted">agreed in ${esc(q.from)}: <b>${esc(q.values)}</b></p><div class="grid3">${ARMS().map((a) => { const r = t.arms[a]; return r ? `<div class="msg bot ${r.grade === "ok" ? "" : "wrong"}"><span class="who">${esc(ARM(a))} ${mark(r.grade)}</span>${esc(r.reply)}</div>` : ""; }).join("")}</div></div>`; }).join("")}`;
  }

  function bill() {
    if (!S.bill) return unavailable() || (running() ? waiting("The bill") : empty("The bill"));
    const T = S.bill.tallies, P = S.bill.prices, nq = (S.data?.live || []).filter((t) => t.id).length;
    const share = (t) => t.prompt ? Math.round(100 * t.cached / t.prompt) : 0;
    const fl = S.bill.flat || {};
    return `<h1>Per million sessions: ${ARMS().map((a) => `${a} ${fmt(Math.round(T[a].usd * 1e6))} USD`).join(" · ")}</h1>
      <p class="lead">B is the cheapest and got ${T.B.grades.ok}/${nq} of the earlier agreements right. A got ${T.A.grades.ok}/${nq}, C ${T.C.grades.ok}/${nq}. A sent ${(T.A.prompt / Math.max(T.C.prompt, 1)).toFixed(1)}× C's prompt tokens but paid ${(T.A.usd / Math.max(T.C.usd, 1e-9)).toFixed(1)}× — ${Math.round(100 * S.bill.a_share)}% of A's prompts from turn 2 on came from the cache.
      After the window slid, B's cache read sat at ${fmt(fl.B?.[0])} tokens on ${fl.B?.[1]}/${fl.B?.[2]} turns, C's at ${fmt(fl.C?.[0])} on ${fl.C?.[1]}/${fl.C?.[2]}: the playbook, nothing more.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th class="num">prompt tokens</th><th class="num">cached</th><th class="num">uncached</th><th class="num">first → last prompt</th><th class="num">USD this session</th><th class="num">USD per 1M sessions</th><th>earlier agreements</th></tr></thead><tbody>
        ${ARMS().map((a) => { const t = T[a]; return `<tr class="${a === "B" ? "hl" : ""}"><td><b>${esc(ARM(a))}</b></td><td class="num">${fmt(t.prompt)}</td><td class="num">${share(t)}%</td><td class="num">${fmt(t.uncached)}</td><td class="num">${fmt(t.first_prompt)} → ${fmt(t.last_prompt)}</td>
          <td class="num">${usd(t.usd)}</td><td class="num"><b>${fmt(Math.round(t.usd * 1e6))}</b></td><td>${mark("ok")} ${t.grades.ok} ${mark("unknown")} ${t.grades.unknown} ${mark("made")} ${t.grades.made}</td></tr>`; }).join("")}</tbody></table>
      <p class="small muted" style="margin-bottom:0">Prices: input ${P.input} · cache_read ${P.cache_read} · output ${P.output} USD per million tokens, from <code>router models</code>. ✓ every agreed value in the reply · ∅ says it is not on file · ✗ a wrong or made-up value.</p></div>`;
  }

  function tryView() {
    const ready = !!S.world;
    const sugg = ["Which day does Arnholt deliver, and to which door?", "What is our Ferrous Gas account for nitrogen?", "How many labels is Halyard's minimum print run?", "When does finance close Q4?", "What did Ostrava Glassworks quote?"];
    return `<h1>Ask one — a new turn at the end of the thread</h1>
      <p class="lead">The question goes to A (the whole thread), B (the last ${S.data?.window_turns || 6} turns of it) and C (the same turns plus MemoryRouter). Each call reports its prompt tokens and how many came from the cache.</p>
      <div class="card"><label for="t-q">question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${sugg.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div id="try-log">${S.tries.slice().reverse().map((x) => `<h3>“${esc(x.question)}”</h3><div class="grid3">${x.rows.map((r) => `<div class="msg bot"><span class="who">${esc(ARM(r.arm))} · ${fmt(r.prompt_tokens)} tok · ${fmt(r.cached)} cached</span>${esc(r.reply)}</div>`).join("")}</div>`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: r.question, rows: r.rows });
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
    else if (k === "prices") S.prices = d;
    else if (k === "turn") S.turns = S.turns.filter((t) => t.turn !== d.turn).concat([d]);
    else if (k === "session") S.session = d.session;
    else if (k === "bill") S.bill = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, conversations, facts, actors and project are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: a new turn through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "prices", "turn", "session", "bill",
    "router_unavailable", "answered", "done", "error"];
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

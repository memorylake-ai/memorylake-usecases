/* User-owned memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two pools, six own scopes", view: "setup" },
    { n: 3, title: "Sprout's memory", sub: "30 memories, three ways", view: "memory" },
    { n: 4, title: "Owner in metadata", sub: "search returns it", view: "search" },
    { n: 5, title: "Six users ask", sub: "through each store", view: "answers" },
    { n: 6, title: "What was attached", sub: "Claude copies it out", view: "attached" },
    { n: 7, title: "The user owns it", sub: "export · delete", view: "ownership" },
    { n: 8, title: "Scoreboard", sub: "three stores side by side", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, search: null, answers: [], inspect: [], exported: null, own: null, score: null,
    unavailable: null, summary: null, asked: [], askUser: "sam", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ORDER = () => S.data?.order || ["pooled", "labeled", "own"];
  const storeLabel = (st) => S.data?.stores?.[st]?.label || st;
  const user = (k) => (S.data?.users || []).find((u) => u.key === k) || { name: k, facts: [] };
  const whose = (f) => Object.entries(f || {}).map(([k, v]) => `${esc(user(k).name)}'s (${esc(v.join(", "))})`).join(", ");

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>as any user, all three stores</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, search, answers, attached, ownership, score, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (ok) => ok == null ? '<span class="mark">·</span>' : `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.stores?.pooled?.boundary)}</span> + 7 more boundaries` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-uom")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Whose memory is the coach reading?</h1>
      <p class="lead">Sprout (fictional) is a wellness app with an AI coach inside. Six users have told the coach about their allergies, injuries and schedules.
      The same 30 memories are stored three ways — one pool with the owner in metadata, the same pool with the owner's name in each fact, and one scope per user —
      and each user asks the coach one question through MemoryRouter. This demo drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 18 calls on gpt-4o-mini and 19 on claude-haiku-4-5 per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-agents-embedded-in-end-user-products" target="_blank" rel="noopener">memorylake.ai › Memory for agents embedded in end-user products</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two pooled projects, and one actor + one boundary per user</h1>
      <p class="lead">The coach never assembles memory itself: every message is an ordinary model call with <code>--boundary</code> added, and MemoryRouter picks what to attach.
      What differs is what the boundary covers.</p>
      <div class="grid2">
        <div class="card"><h3 style="margin-top:0">Pooled — one boundary for everyone</h3><dl class="kv">
          ${["pooled", "labeled"].map((st) => `<dt>${esc(st)}</dt><dd class="mono small">${esc(w?.stores?.[st]?.project || "pending")}<br><b>${esc(w?.stores?.[st]?.boundary || "")}</b></dd>`).join("")}</dl>
          <div class="cmdline">boundary create --projects &lt;the pool&gt;</div></div>
        <div class="card"><h3 style="margin-top:0">Own — one boundary per user</h3><dl class="kv">
          ${(S.data?.users || []).map((u) => `<dt>${esc(u.name)}</dt><dd class="mono small">${esc(w?.users?.[u.key]?.boundary || "pending")}</dd>`).join("")}</dl>
          <div class="cmdline">boundary create --human-actor &lt;the user's actor&gt;</div></div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("Sprout's memory") : empty("Sprout's memory");
    const users = S.data.users;
    return `<h1>${fmt(S.memory.total)} memories from ${S.memory.users} users — stored three ways</h1>
      <p class="lead"><b>pooled</b>: one project, the owner in <code>metadata.user_id</code> (how an app on a vector store with a metadata filter keeps them) ·
      <b>labeled</b>: the same pool, the owner's name written into each fact · <b>own</b>: each fact on its owner's actor.</p>
      <div class="grid2">${users.map((u) => `<div class="card"><h3 style="margin-top:0">${esc(u.name)}</h3>
        ${u.facts.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function search() {
    if (!S.search) return running() ? waiting("The owner is in the metadata") : empty("The owner is in the metadata");
    const keep = S.search.hits.filter((h) => h.user_id === "omar").length;
    return `<h1>The owner is in the metadata — <code>search</code> returns it</h1>
      <p class="lead"><code>search "${esc(S.search.query)}" --projects &lt;pool&gt;</code>: every hit carries <code>metadata.user_id</code>, so an app reading search results for Omar could keep only his
      (${keep} of ${S.search.hits.length} here). <b>MemoryRouter takes a boundary, not a filter</b>, and what it attaches is the fact text.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>#</th><th>fact</th><th>metadata.user_id</th></tr></thead><tbody>
        ${S.search.hits.map((h, i) => `<tr class="${h.user_id === "omar" ? "hl" : ""}"><td>${i + 1}</td><td>${esc(h.fact)}</td><td><code>${esc(h.user_id)}</code> <span class="muted">${esc(user(h.user_id).name)}</span></td></tr>`).join("")}</tbody></table></div>`;
  }

  function answers() {
    const rows = S.answers;
    const h = "Six users ask the coach";
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const tot = (st) => { const g = rows.filter((r) => r.store === st && r.ok != null); return `${g.filter((r) => r.ok).length}/${g.length}`; };
    return `<h1>${h} — pooled ${tot("pooled")} · labeled ${tot("labeled")} · own ${tot("own")}</h1>
      <p class="lead">Each question through each store: <code>router chat --boundary</code> (gpt-4o-mini, temperature 0); the system prompt names who is talking.
      ✗ marks a reply that uses another user's memory (or misses the asker's own); the arrow says whose.</p>
      <div class="grid2">${S.data.questions.map((q) => {
        const rs = ORDER().map((st) => rows.find((r) => r.user === q.user && r.store === st)).filter(Boolean);
        if (!rs.length) return "";
        return `<div class="card chat"><div class="msg user"><span class="who">${esc(user(q.user).name)}</span>${esc(q.say)}</div>
          ${rs.map((r) => `<div class="msg bot ${r.ok === false ? "wrong" : ""}"><span class="who">${esc(r.store)} ${markOf(r.ok)}${Object.keys(r.foreign || {}).length ? ` <b class="bad-ink">← ${whose(r.foreign)}</b>` : (r.ok === false ? ` <b class="bad-ink">← missed: ${esc(q.expect_label)}</b>` : "")}</span>${esc(r.reply || "…")}</div>`).join("")}</div>`;
      }).join("")}</div>`;
  }

  function attached() {
    const h = "What MemoryRouter attached";
    if (!S.inspect.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const other = (st) => S.inspect.filter((c) => c.store === st).reduce((a, c) => a + c.other, 0);
    return `<h1>${h} — someone else's lines: pooled ${other("pooled")} · labeled ${other("labeled")} · own ${other("own")}</h1>
      <p class="lead">The same 18 calls to <b>claude-haiku-4-5</b>, told in its system prompt to copy out every remembered entry first. In the labeled pool the owner's name is in every
      attached line, and the model still used them. (The checker is its own call, so what it got can differ a little from the answer's call.)</p>
      <div class="grid2">${S.data.questions.map((q) => {
        const cs = ORDER().map((st) => S.inspect.find((c) => c.user === q.user && c.store === st)).filter(Boolean);
        if (!cs.length) return "";
        return `<div class="card"><h3 style="margin-top:0">${esc(user(q.user).name)}: “${esc(q.say)}”</h3>
          ${cs.map((c) => `<p class="small" style="margin:10px 0 2px"><b>${esc(c.store)}</b> <span class="muted">${c.lines.length} attached · ${c.own} ${esc(user(q.user).name)}'s · ${c.other} someone else's</span></p>
            ${c.lines.map((l, i) => { const o = c.owners[i]; const mine = o === q.user;
              return `<div class="memfact small"><span><span class="lab ${mine ? "lab-ok" : "lab-bad"}">${esc(o ? user(o).name : "?")}</span> ${esc(l)}</span></div>`; }).join("")}`).join("")}</div>`;
      }).join("")}</div>`;
  }

  function ownership() {
    const h = "The user owns it";
    if (!S.own && !S.exported) return running() ? waiting(h) : empty(h);
    const ex = S.exported, o = S.own;
    return `<h1>${h} — export is one scope, delete is one scope</h1>
      <p class="lead">With a scope per user, “give me my data” is <code>fact list --actors &lt;me&gt;</code> and “forget me” is deleting the facts on my actor.
      In the pool, both mean reading every user's memory and filtering.</p>
      <div class="grid2">
        <div class="card"><h3 style="margin-top:0">Omar exports his memory</h3>
          ${ex ? `<p class="small muted">${ex.facts.length} facts → <code>${esc(ex.path)}</code></p>${ex.facts.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}` : '<span class="pill busy"><span class="spinner"></span> working…</span>'}</div>
        <div class="card"><h3 style="margin-top:0">Dana asks Sprout to forget her</h3>
          ${o ? `<dl class="kv"><dt>her actor</dt><dd>${o.dana_deleted} facts deleted · <code>fact list --actors</code> → 0</dd>
            <dt>her boundary</dt><dd>${o.dana_attached_after == null ? "–" : `${o.dana_attached_after.filter((l) => S.data.users.some((u) => u.facts.includes(l) || u.labeled.includes(l))).length} stored facts attached to her question`}</dd>
            ${Object.entries(o.pooled_scan || {}).map(([st, p]) => `<dt>${esc(st)}</dt><dd>read all ${p.read} facts to find hers: ${p.found} found — until then they are still attached to other users' questions</dd>`).join("")}</dl>` : '<span class="pill busy"><span class="spinner"></span> working…</span>'}</div></div>`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Scoreboard") : empty("Scoreboard");
    const sc = S.score, cell = (u, st) => S.answers.find((r) => r.user === u && r.store === st);
    return `<h1>Scoreboard — right: pooled ${sc.totals.pooled}/${sc.questions} · labeled ${sc.totals.labeled}/${sc.questions} · own ${sc.totals.own}/${sc.questions}</h1>
      <p class="lead">Same questions, same model, same 30 memories; only the scope changed between columns.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th>${ORDER().map((st) => `<th>${esc(st)}</th>`).join("")}</tr></thead><tbody>
        ${S.data.questions.map((q) => `<tr><td>${esc(user(q.user).name)}: ${esc(q.say)}</td>${ORDER().map((st) => `<td>${markOf(cell(q.user, st)?.ok)}</td>`).join("")}</tr>`).join("")}
        <tr class="hl"><td><b>right</b></td>${ORDER().map((st) => `<td>${sc.totals[st]}/${sc.questions}</td>`).join("")}</tr>
        <tr><td>answers that used someone else's memory</td>${ORDER().map((st) => `<td>${sc.used[st]}</td>`).join("")}</tr>
        <tr><td>someone else's lines attached (checker)</td>${ORDER().map((st) => `<td>${sc.other_lines[st]}</td>`).join("")}</tr></tbody></table></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    const users = S.data?.users || [];
    return `<h1>Ask the coach as any user</h1>
      <p class="lead">One question through all three stores, as the memory stands now (after a full run Dana's memory is deleted everywhere).</p>
      <div class="card"><div class="chips" id="user-chips">${users.map((u) => `<button type="button" class="chip ${S.askUser === u.key ? "on" : ""}" data-user="${esc(u.key)}">${esc(u.name)}</button>`).join("")}</div>
        <div class="chat" id="chat-log">${S.asked.map((x) => `<div class="msg user"><span class="who">${esc(user(x.user).name)}</span>${esc(x.say)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${Object.keys(r.foreign || {}).length ? "wrong" : ""}"><span class="who">${esc(r.store)}${Object.keys(r.foreign || {}).length ? ` <b class="bad-ink">← ${whose(r.foreign)}</b>` : ""}</span>${esc(r.reply)}</div>`).join("")}`).join("") || (ready ? '<div class="empty">No questions yet.</div>' : '<div class="empty">Run the demo first.</div>')}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Can I do overhead presses this week?" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Can I do overhead presses this week?", "Should I have a coffee before my run?", "What should I eat after my workout?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>`;
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
    st.querySelectorAll("#user-chips .chip").forEach((c) => c.addEventListener("click", () => { S.askUser = c.dataset.user; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); send($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => send(c.dataset.q)));
    }
  }
  async function send(say) {
    if (!say) return;
    const log = $("#chat-log"); if (log) log.insertAdjacentHTML("beforeend", `<div class="msg user"><span class="who">${esc(user(S.askUser).name)}</span>${esc(say)}</div><span class="pill busy" id="sending"><span class="spinner"></span> asking all three stores…</span>`);
    const r = await api("/api/ask", { user: S.askUser, say });
    if (r) S.asked.push(r);
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
  function resetView() { S.memory = null; S.search = null; S.answers = []; S.inspect = []; S.exported = null; S.own = null; S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's two projects, six user actors (and every fact on them) and eight boundaries?")) return;
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
    else if (k === "search") S.search = d;
    else if (k === "answer") S.answers.push(d);
    else if (k === "inspect") S.inspect.push(d);
    else if (k === "export") S.exported = d;
    else if (k === "ownership") S.own = d;
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the projects, the user actors with their facts, and the boundaries are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: one question as any user, through all three stores.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "search", "answer", "inspect", "export", "ownership", "scoreboard", "summary", "router_unavailable", "done", "error"];
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

/* Renewal pipeline memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "Account book", sub: "160 renewals + call notes", view: "book" },
    { n: 4, title: "Small answers", sub: "look-ups, one manager's list", view: "small" },
    { n: 5, title: "Renewal review", sub: "the whole team's list", view: "team" },
    { n: 6, title: "What was attached", sub: "Claude copies it out", view: "attached" },
    { n: 7, title: "Count first", sub: "fact list, then the model", view: "fix" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, answers: {}, inspect: {}, score: null, unavailable: null, summary: null, notes: [],
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>your own question</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, book, small, team, attached, fix, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (a) => a ? `<span class="mark ${a.ok ? "ok" : "bad"}">${esc(a.mark.split("  + ")[0])}</span>${(a.extra || []).map((n) => `<span class="mark bad">+ ${esc(n)}</span>`).join("")}` : '<span class="muted">…</span>';
  const win = () => S.data ? `${S.data.window[0]} to ${S.data.window[1]}` : "";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-rpm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A renewal list that looks complete, and is half of it.</h1>
      <p class="lead">A customer-success team keeps every account's renewal record in MemoryLake and asks its assistant through <b>MemoryRouter</b> with one
      <code>--boundary</code>. Look-ups are right. One manager's list is right. The whole team's list for the quarter is <b>40 accounts</b> — and the reply names about
      half of them, without saying so. This demo shows why, and the fix: count first with <code>fact list</code>. It drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 12 calls on gpt-4o-mini and 2 on claude-haiku-4-5 per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-sales-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for sales teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    return `<h1>One project for the account book, one boundary for the assistant</h1>
      <p class="lead">${esc(S.data.company)}: ${S.data.managers.length} account managers, ${S.data.accounts} accounts. Today is ${esc(S.data.today)};
      the renewal review covers <b>${esc(win())}</b>. The assistant never assembles memory itself: it adds <code>--boundary</code> to an ordinary model call.</p>
      <div class="cols"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(S.world?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(S.world?.boundary || "pending")}</b></dd></dl>
        <h3>Every question in this demo</h3>
        <div class="cmdline">router chat "&lt;question&gt;" --model gpt-4o-mini --system "…" --boundary ${esc(S.world?.boundary || "…")}</div></div>
        <div class="card"><h3 style="margin-top:0">Account managers</h3><div class="factlist">${S.data.managers.map((m) => `<div class="memfact"><span>${esc(m)}</span></div>`).join("")}</div></div></div>`;
  }

  function book() {
    if (!S.memory) return running() ? waiting("The account book") : empty("The account book");
    const m = S.memory;
    return `<h1>The account book — ${fmt(m.facts)} facts</h1>
      <p class="lead">${m.records} renewal records (owner, renewal date, ARR) and ${m.notes} call notes, stored verbatim with <code>fact add</code>.
      <b>${m.window} accounts</b> renew between ${esc(win())}.</p>
      <div class="cols"><div class="card"><h3 style="margin-top:0">Renewals in the window, by manager</h3>${Object.entries(m.per_manager).map(([k, v]) =>
        `<div class="barrow"><span>${esc(k)}</span><div class="bar" style="width:${Math.round(100 * v / 20)}%"></div><span class="n">${v}</span></div>`).join("")}
        <div class="barrow"><span><b>whole team</b></span><div class="bar alt" style="width:100%"></div><span class="n"><b>${m.window}</b></span></div></div>
        <div class="card"><h3 style="margin-top:0">A few of the facts</h3><div class="factlist">${S.data.sample.map((f) => `<div class="memfact ${f.startsWith("Renewal") ? "rule" : ""}"><span>${esc(f)}</span></div>`).join("")}</div></div></div>`;
  }

  function answerCard(a, title) {
    return `<div class="card"><div class="callhead"><h3 style="margin:0">${esc(title || a.label)}</h3><span class="tok">${a.memory_tokens != null ? fmt(a.memory_tokens) + " tokens of memory attached" : "list from fact list, in the request body"}</span></div>
      <div class="marks">${mark(a)}${a.hedged ? '<span class="mark ok">says it may be incomplete</span>' : ""}</div>
      <details ${a.qkind === "lookup" ? "open" : ""}><summary class="muted small">the reply</summary><div class="reply">${esc(a.reply)}</div></details>
      ${(a.missed || []).length ? `<p class="small"><b>Missing (${a.missed.length}):</b> ${a.missed.map((n) => `<span class="mark bad">${esc(n)}</span>`).join("")}</p>` : ""}</div>`;
  }

  function small() {
    const keys = (S.data?.questions || []).filter((q) => q.key !== "team").map((q) => q.key);
    const got = keys.map((k) => S.answers[`${k}:router`]).filter(Boolean);
    if (!got.length) return unavailable() || (running() ? waiting("Small answers through MemoryRouter") : empty("Small answers through MemoryRouter"));
    return `<h1>Small answers through MemoryRouter — right</h1>
      <p class="lead">Three look-ups and one manager's renewals (Lev Brandt, 8 accounts). Each answer fits in what MemoryRouter attaches to one request.</p>
      <div class="grid2">${got.map((a) => answerCard(a)).join("")}</div>`;
  }

  function team() {
    const a = S.answers["team:router"];
    if (!a) return unavailable() || (running() ? waiting("The renewal review") : empty("The renewal review"));
    const n = a.truth?.length || 40;
    return `<h1>The renewal review — ${esc(a.mark.split("  + ")[0])}</h1>
      <p class="lead">“Which accounts renew between ${esc(S.data?.window[0])} and ${esc(S.data?.window[1])}? List every one of them.” The same command as before. The reply is a clean list —
      every name in it is right, and ${a.hedged ? "it does say it may be incomplete" : "<b>nothing in it says it is partial</b>"}. It names ${a.right?.length ?? "?"} of the ${n} accounts.</p>
      <div class="cols">${answerCard(a, "Team's renewals · MemoryRouter")}
        <div class="card"><h3 style="margin-top:0">${n} accounts renew</h3><div class="chips">${(a.truth || []).map((t) => `<span class="mark ${(a.missed || []).includes(t) ? "bad" : "ok"}">${esc(t)}</span>`).join("")}</div>
        <p class="muted small">green: in the reply · red: missing from it</p>${S.notes.length ? `<p class="small">${esc(S.notes[S.notes.length - 1])}</p>` : ""}</div></div>`;
  }

  function attached() {
    const ins = ["lev", "team"].map((k) => S.inspect[k]).filter(Boolean);
    if (!ins.length) return unavailable() || (running() ? waiting("What MemoryRouter attached") : empty("What MemoryRouter attached"));
    return `<h1>What MemoryRouter attached</h1>
      <p class="lead">The same question through the same boundary to <b>claude-haiku-4-5</b>, told in its system prompt to copy out every remembered entry before answering.
      The user message is the question itself, so retrieval sees the same query. Each copied line is matched to a stored fact.</p>
      <div class="grid2">${ins.map((c) => `<div class="card"><div class="callhead"><h3 style="margin:0">${esc(c.label)}</h3><span class="tok">${c.attached} memories attached · answer has ${c.truth}</span></div>
        <div class="barrow"><span>attached</span><div class="bar" style="width:${Math.round(100 * c.attached / Math.max(c.truth, c.attached))}%"></div><span class="n">${c.attached}</span></div>
        <div class="barrow"><span>in the answer</span><div class="bar alt" style="width:${Math.round(100 * c.truth / Math.max(c.truth, c.attached))}%"></div><span class="n">${c.truth}</span></div>
        <p class="small">${c.kinds.window} renewal(s) that belong in the answer · ${c.kinds.other_record} other renewal(s) · ${c.kinds.note} call note(s)${c.kinds.unmatched ? ` · ${c.kinds.unmatched} unmatched` : ""}</p>
        <details><summary class="muted small">the attached lines</summary><div class="factlist">${(c.lines || []).map((l) => `<div class="memfact rule"><span>${esc(l)}</span></div>`).join("")}</div></details></div>`).join("")}</div>
      <div class="card"><p style="margin:0">Retrieval picks the right records. MemoryRouter attaches a bounded set by relevance — about ${S.data?.budget || 20} per request in most calls — never the whole book.
      When the answer has more entries than that, the reply can only list what was attached — and in some runs it lists fewer still.</p></div>`;
  }

  function fix() {
    const a = S.answers["team:listed"];
    if (!S.score && !a) return unavailable() || (running() ? waiting("Count first") : empty("Count first"));
    const rows = S.score?.rows || [];
    return `<h1>Count first: <code>fact list</code>, then the model</h1>
      <p class="lead"><code>fact list --query "Renewal record"</code> returns every match, no top-k. The app filters the window itself — ${S.score ? `<b>${S.score.window} accounts</b>, more than ${S.score.limit}` : "…"} —
      and hands the complete list to the same model in the request body (<code>router chat --body</code>). Same question, same model; only where the memory comes from differs.</p>
      ${a ? answerCard(a, "Team's renewals · listed via fact list") : ""}
      ${rows.length ? `<div class="card"><table class="lint"><thead><tr><th>question</th><th>MemoryRouter</th><th>count first, then list</th></tr></thead><tbody>
        ${rows.map((r) => `<tr class="${r.question === "team" ? "hl" : ""}"><td>${esc(r.label)}</td><td><span class="mark ${r.ok ? "ok" : "bad"}">${esc(r.router)}</span></td><td>${r.listed === "—" ? '<span class="muted">—</span>' : `<span class="mark ${a?.ok ? "ok" : "bad"}">${esc(r.listed)}</span>`}</td></tr>`).join("")}</tbody></table>
        <p class="muted small">Rule of thumb: for “list every …” questions, count with <code>fact list</code> first; if the count is more than MemoryRouter attaches, send the list yourself.</p></div>` : ""}
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    return `<h1>Ask — your own question</h1>
      <p class="lead">Ask the assistant anything about the accounts through MemoryRouter, against the account book as it is now. List questions are graded against the stored records.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Which accounts asked about a multi-year discount?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["When does Quarry Health renew?", "Which accounts asked about a multi-year discount?", "Which of Priya Nair's accounts renew between 2026-10-19 and 2026-12-31?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo());
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuestion(c.dataset.q); }));
    }
  }
  function setResult(html) { S.ask.html = html; const box = $("#ask-result"); if (box) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQuestion(q) {
    if (!q) return; S.ask.q = q;
    $("#ask-result").innerHTML = `<span class="pill busy"><span class="spinner"></span> asking…</span>`;
    const r = await api("/api/ask", { question: q });
    if (!r) { $("#ask-result") && ($("#ask-result").innerHTML = ""); return; }
    const x = r.result;
    setResult(`<div class="callhead"><h3 style="margin:0">MemoryRouter</h3><span class="tok">${fmt(x.memory_tokens)} tokens of memory attached · ${x.seconds.toFixed(1)}s</span></div>
      <div class="reply">${esc(x.reply)}</div>${x.mark ? `<div class="marks">${mark(x)}</div>` : ""}
      ${(x.missed || []).length ? `<p class="small"><b>Missing (${x.missed.length}):</b> ${x.missed.map((n) => `<span class="mark bad">${esc(n)}</span>`).join("")}</p>` : ""}`);
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
  function resetView() { S.memory = null; S.answers = {}; S.inspect = {}; S.score = null; S.unavailable = null; S.summary = null; S.notes = []; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's account-book project (and every fact in it) and its boundary?")) return;
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
    else if (k === "note") { term("note", ev.text); if (ev.text.includes("tokens of memory ≈")) S.notes.push(ev.text); }
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "memory") S.memory = d;
    else if (k === "answer") S.answers[`${d.question}:${d.mode}`] = d;
    else if (k === "inspect") S.inspect[d.question] = d;
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the account-book project, its facts and its boundary are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: your own question, against the account book.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "answer", "inspect", "scoreboard", "summary", "router_unavailable", "done", "error"];
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
    show(state.status === "running" ? S.view : S.score ? "fix" : "connect");
    subscribe();
  })();
})();

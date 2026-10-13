/* Memory telemetry for agent operations — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (i) => (i ? String(i).slice(0, 17) + "…" : "—");
  const hms = (t) => (t ? new Date(t * 1000).toISOString().slice(11, 19) : "—");
  const ops = (o) => Object.entries(o || {}).map(([k, n]) => `${n} ${k}`).join(", ") || "—";

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one memory, four jobs", view: "setup" },
    { n: 3, title: "intake-agent", sub: "runbooks into memory", view: "intake" },
    { n: 4, title: "scribe-agent", sub: "notes with a TTL", view: "scribe" },
    { n: 5, title: "answer-agent", sub: "pass 1", view: "pass1" },
    { n: 6, title: "retention-job", sub: "evict expired notes", view: "retention" },
    { n: 7, title: "answer-agent", sub: "pass 2", view: "pass2" },
    { n: 8, title: "Telemetry", sub: "metrics · alerts · usage", view: "telemetry" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, scribe: null, answers: {}, retention: null, telemetry: null,
    ask: { q: "", html: "" }, usage: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const windowLine = (w) => w ? `<p class="small muted">window <span class="mono">${hms(w.start)}–${hms(w.end)} UTC</span> · ${esc(ops(w.ops))}</p>` : "";

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask · usage<small>search · usage records now</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, scribe, pass1, retention, pass2, telemetry, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Support memory</dt><dd>${st.ready ? "from an earlier run — every run measures a fresh workload, so running again deletes it first" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Agent quality dropped. Is it the memory?</h1>
      <p class="lead">Bramblecrest Outfitters runs four jobs against one shared support memory. On the second pass the answer agent gets half its questions wrong,
      and nothing in the agents' own logs says why. The demo measures every memory operation as it runs — latency, retrieval hit ratio, facts held, evictions —
      evaluates three alert rules, traces each miss to the eviction that caused it, writes it all as Prometheus text, and reads back <b>MemoryLake's own usage
      records</b> for each agent's time window. This page drives the <code>memorylake</code> CLI for real. The company and everyone in it are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the support project (with its documents and facts) and the runbook folder.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-telemetry-for-agent-operations" target="_blank" rel="noopener">memorylake.ai › memory telemetry for agent operations</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One memory, four jobs");
    const a = story()?.agents || {};
    return `<h1>One shared support memory, four jobs that use it</h1>
      <p class="lead">One project holds the runbooks and the pinned notes. Each job runs in its own time window, so every operation — and later every usage record — belongs to one of them.</p>
      <div class="grid"><div class="card"><h2 style="margin-top:0">${esc(su.project)}</h2><div class="mono small faint">${esc(su.ids.project)}</div>
          <div class="small muted" style="margin-top:8px">Library: <span class="mono">${esc(su.folder)}/</span></div></div>
        <div class="card"><table class="iso">${Object.values(a).map((t) => { const [n, ...rest] = t.split(" "); return `<tr><td class="mono small">${esc(n)}</td><td class="small">${esc(rest.join(" "))}</td></tr>`; }).join("")}</table></div></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("intake-agent");
    return `<h1>intake-agent — the runbooks go into memory</h1>
      <p class="lead"><code>lib upload</code> three Markdown runbooks, <code>proj doc import --recursive --wait</code>, then <code>proj stats</code> for the document gauge.</p>
      ${windowLine(x.window)}
      <div class="grid3"><div class="card"><div class="muted small">documents</div><div class="bignum">${x.docs}</div></div>
        <div class="card mine"><div class="muted small">status okay</div><div class="bignum ok">${x.okay}</div></div>
        <div class="card"><div class="muted small">operations</div><div class="small" style="margin-top:8px">${esc(ops(x.window.ops))}</div></div></div>`;
  }

  function scribe() {
    const x = S.scribe; if (!x) return empty("scribe-agent");
    return `<h1>scribe-agent — notes from yesterday's tickets, each with a time-to-live</h1>
      <p class="lead"><code>fact add</code> six notes in one call, then <code>fact update --metadata</code> on each: kind, date noted, TTL. Everything that came in through a ticket
      is filed as <code>kind=ticket</code> with a 7-day TTL — including three notes that are really standing agreements and notices.</p>
      ${windowLine(x.window)}
      <div class="card"><table class="iso"><tr><th>fact</th><th>kind</th><th>noted</th><th>ttl</th><th>text</th></tr>
        ${x.facts.map((f) => `<tr><td class="mono small faint">${esc(short(f.id))}</td><td class="small">${esc(f.kind)}</td><td class="mono small">${esc(f.noted)}</td>
          <td class="small">${f.ttl_days ? `<span class="pill warn">${f.ttl_days}d</span>` : "—"}</td><td class="small">${esc(f.text)}</td></tr>`).join("")}</table></div>`;
  }

  function passView(label, title, lead) {
    const a = S.answers[label]; if (!a) return empty(title);
    return `<h1>${title}</h1><p class="lead">${lead}</p>${windowLine(a.window)}
      <p><span class="pill ${a.hits === a.total ? "ok" : "err"}">hit ratio ${a.hits}/${a.total} = ${a.ratio.toFixed(2)}</span></p>
      <div class="card"><table class="iso"><tr><th></th><th>question</th><th>latency</th><th>answer found in</th></tr>
        ${a.rows.map((r) => `<tr><td>${r.hit ? '<span class="pill ok">✓</span>' : '<span class="pill err">✗</span>'}</td><td class="small">${esc(r.question)}</td>
          <td class="mono small">${r.seconds.toFixed(2)}s</td><td class="small">${r.hit ? esc(r.source || "") : `<span class="muted">“${esc(r.marker)}” not in the top 5</span>`}</td></tr>`).join("")}</table></div>`;
  }
  const pass1 = () => passView("pass 1", "answer-agent — six customer questions, pass 1",
    "<code>search --projects &lt;support&gt; --top-k 5</code> for each question. A hit means the expected answer is in one of the five results (fact text or document passage).");
  const pass2 = () => passView("pass 2", "answer-agent — the same six questions, pass 2",
    "Nothing about the agent changed. Its memory did.");

  function retention() {
    const r = S.retention; if (!r) return empty("retention-job");
    return `<h1>retention-job — evict what has outlived its time-to-live</h1>
      <p class="lead"><code>fact list</code> every page, delete each fact whose <code>noted + ttl_days</code> is before today (<code>fact delete</code>, one id per call), list again.</p>
      ${windowLine(r.window)}
      <div class="grid3"><div class="card"><div class="muted small">facts before</div><div class="bignum">${r.before}</div></div>
        <div class="card"><div class="muted small">evicted</div><div class="bignum err">${r.evicted.length}</div></div>
        <div class="card"><div class="muted small">facts after</div><div class="bignum">${r.after}</div></div></div>
      <div class="card"><table class="iso"><tr><th>evicted</th><th>kind</th><th>noted</th><th>text</th></tr>
        ${r.evicted.map((e) => `<tr><td class="mono small faint">${esc(short(e.id))}</td><td class="small">${esc(e.metadata.kind)}</td><td class="mono small">${esc(e.metadata.noted)}</td><td class="small">${esc(e.text)}</td></tr>`).join("")}</table></div>`;
  }

  function usageTable(u, label) {
    if (!u) return "";
    const now = Date.now() / 1000;
    const head = u.newest ? `newest record in the account: <span class="mono">${hms(u.newest)} UTC</span>, ${Math.floor((now - u.newest) / 60)} min ago` : "the account has no usage records yet";
    return `<div class="card"><div class="row spread"><h2 style="margin:0">usage records — ${esc(label)}</h2><span class="small muted">${head}</span></div>
      <table class="iso" style="margin-top:10px"><tr><th>agent</th><th>window (UTC)</th><th>what it did</th><th>records</th><th>by operation (records · tokens)</th></tr>
      ${u.windows.map((w) => `<tr><td class="mono small">${esc(w.agent)}</td><td class="mono small">${hms(w.start)}–${hms(w.end)}</td><td class="small">${esc(ops(w.ops))}</td>
        <td class="mono small">${w.landed ? w.records : "—"}</td><td class="small">${w.landed ? (Object.entries(w.by_model).sort().map(([m, s]) => `<span class="pill">${esc(m)} ${s.records} · ${s.tokens}</span>`).join(" ") || "no billed operations") : '<span class="pill warn">not landed yet</span>'}</td></tr>`).join("")}</table></div>`;
  }

  function telemetry() {
    const t = S.telemetry; if (!t) return empty("Telemetry");
    const h = t.health;
    return `<h1>Telemetry — what the memory layer did, and what it cost</h1>
      <p class="lead">Latency is measured at the client (each CLI call, start-up included). Hit ratio, facts, evictions and documents come from the run itself.
      The usage records are MemoryLake's own: one per billed platform operation, read with <code>usage records --start-time --end-time</code> for each agent's window.</p>
      <div class="grid3"><div class="card"><div class="muted small">retrieval hit ratio</div><div class="bignum ${h.hit2 < h.hit1 ? "err" : "ok"}">${h.hit1.toFixed(2)} → ${h.hit2.toFixed(2)}</div></div>
        <div class="card"><div class="muted small">facts held</div><div class="bignum">${h.facts_before} → ${h.facts_after}</div><div class="small muted">${h.evictions} evicted</div></div>
        <div class="card"><div class="muted small">documents</div><div class="bignum">${h.docs}</div></div></div>
      <div class="card"><h2 style="margin-top:0">Alert rules</h2>
        ${t.alerts.map((a) => `<div class="memfact ${a.state === "FIRING" ? "personal" : "clean"}"><span class="mk">${a.state === "FIRING" ? "●" : "✓"}</span><span class="body">
          <span class="pill ${a.state === "FIRING" ? "err" : "ok"}">${a.state}</span> <b class="mono">${esc(a.alert)}</b> <span class="mono small">${esc(a.value)} (rule ${esc(a.threshold)})</span>
          <div class="small muted">${esc(a.summary)}</div></span></div>`).join("")}</div>
      <div class="card"><h2 style="margin-top:0">Why answer-agent got worse</h2>
        ${t.misses.map((m) => `<div class="memfact personal"><span class="mk">✗</span><span class="body"><div class="small">${esc(m.question)}</div>
          ${m.cause ? `<div class="small muted">← evicted <span class="mono">${esc(short(m.cause.id))}</span> (kind=${esc(m.cause.metadata.kind)}, ttl ${esc(m.cause.metadata.ttl_days)}d): “${esc(m.cause.text)}”</div>` : `<div class="small muted">no evicted fact held “${esc(m.marker)}”</div>`}</span></div>`).join("")}
        <p class="small muted" style="margin-bottom:0">${t.misses.filter((m) => m.cause).length} of ${t.misses.length} new misses trace to an eviction; ${h.evictions - t.misses.filter((m) => m.cause).length} of ${h.evictions} evictions cost no answer.</p></div>
      <div class="card"><h2 style="margin-top:0">Latency at the client</h2><table class="iso"><tr><th>operation</th><th>calls</th><th>p50</th><th>p95</th><th>max</th></tr>
        ${t.latency.map((r) => `<tr><td class="mono small">${esc(r.op)}</td><td class="mono small">${r.n}</td><td class="mono small">${r.p50.toFixed(2)}s</td><td class="mono small">${r.p95.toFixed(2)}s</td><td class="mono small">${r.max.toFixed(2)}s</td></tr>`).join("")}</table></div>
      ${usageTable(t.usage, "this run")}
      ${t.landed ? "" : '<p class="small muted">MemoryLake writes usage records in batches, often an hour or more after the work. The windows are saved in <code>out/runs.jsonl</code>; “Ask · usage” reads them again.</p>'}
      <p class="small muted">Written: <code>out/metrics.prom</code> (Prometheus text) and <code>out/alerts.json</code>.</p>`;
  }

  function askRows(r) {
    return `<h2>${r.rows.length} hits for “${esc(r.question)}” <span class="small muted">· ${r.seconds.toFixed(2)}s</span></h2>` + (r.rows.map((h) => `<div class="memfact clean"><span class="mk">#${h.rank}</span><span class="body">
      <span class="pill">${esc(h.kind)}</span> <span class="mono small">${esc(h.kind === "document" ? h.id : short(h.id))}</span>
      <div class="small" style="margin-top:4px">“${esc(h.text)}”</div></span></div>`).join("") || '<p class="muted">nothing</p>');
  }
  function usageView(r) {
    return `<p class="small">Team usage today: <b>${esc(r.summary?.requests ?? 0)}</b> billed requests, <b>${esc(r.summary?.used_tokens ?? 0)}</b> tokens · ${r.landed} of ${r.runs.length} saved run(s) fully landed.</p>`
      + r.runs.map((x) => usageTable(x.usage, `run ${x.run}`)).join("");
  }
  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the support memory · read usage records now</h1>
      <p class="lead">A search shows its hits and how long it took. “Read usage records” asks MemoryLake for the windows of your last five runs — run it again later and watch them land.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Do gift cards expire?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.questions || []).map((q) => `<button type="button" class="chip" data-q="${esc(q.ask)}">${esc(q.ask)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Usage records for your saved runs</h2><button class="btn sm" data-act="usage" ${S.st.connected ? "" : "disabled"}>Read usage records</button></div>
        <div id="usage-result" style="margin-top:10px">${S.usage}</div></div>`;
  }

  // ---------------------------------------------------------------- behaviour
  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    st.querySelector('[data-act="usage"]')?.addEventListener("click", async () => {
      const box = $("#usage-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading usage records…</span>`;
      const r = await api("/api/usage", {}); S.usage = r ? usageView(r) : ""; if (document.body.contains(box)) box.innerHTML = S.usage;
    });
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = askRows(r);
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
  function resetView() { S.setup = null; S.intake = null; S.scribe = null; S.answers = {}; S.retention = null; S.telemetry = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's support project (documents and facts with it) and the runbook folder from your account?")) return;
    banner(""); const r = await api("/api/cleanup", {}); if (r) { S.st = r; setConn(); }
  });

  // ---------------------------------------------------------------- terminal
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

  // ---------------------------------------------------------------- events
  const stepOf = (i) => STEPS.find((s) => s.n === i);
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; S.step = d.index || 0;
      if (d.index === 2) resetView();
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "text") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "intake") S.intake = d;
    else if (k === "scribe") S.scribe = d;
    else if (k === "answers") S.answers[d.label] = d;
    else if (k === "retention") S.retention = d;
    else if (k === "telemetry") S.telemetry = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the support project (documents and facts with it) and the runbook folder are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Open “Ask · usage” to search the memory or read the usage records again later.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "ask", "usage"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "scribe", "answers", "retention", "telemetry", "ask", "usage", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    // "error" is also EventSource's own connection-error event, which has no data: skip those.
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
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.telemetry ? "telemetry" : "connect");
    subscribe();
  })();
})();

/* Feature request memory for small SaaS teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project for every request", view: "setup" },
    { n: 3, title: "Intake", sub: "7 accounts · 17 requests", view: "intake" },
    { n: 4, title: "Four quick reads", sub: "recent · keyword · hits · mentions", view: "quick" },
    { n: 5, title: "Read it all, by account", sub: "who asks and what they pay", view: "byaccount" },
    { n: 6, title: "Two requests today", sub: "mentions vs accounts", view: "arrived" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, byaccount: null, arrived: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const tname = (k) => story()?.themes?.[k] || k;
  const money = (n) => "$" + Number(n || 0).toLocaleString("en-US");
  const quote = (t) => String(t || "").split("): ").slice(1).join("): ") || t;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const badge = (theme) => `<span class="pill">${esc(theme)}</span>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the requests<small>search's top 8 vs the whole theme</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, byaccount, arrived, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Requests</dt><dd>${st.ready ? "from an earlier run — Run again reads them back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1 minute · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Seventeen feature requests. Four quick reads say dark mode. All of them, by account, say single sign-on.</h1>
      <p class="lead">Driftline's product manager plans the Q4 roadmap next week. Every request since May is one fact in a MemoryLake project, word for word, with the account, channel, date and theme.
      Read the last two weeks, count a keyword, count search hits or count mentions, and dark mode wins — five of those requests come from one $4,800 account.
      Read every request, de-duplicated to accounts and weighted by what they pay, and single sign-on is five accounts and $120,400, with two renewals blocked on it.
      This page drives the <code>memorylake</code> CLI for real. The company, customers and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every request on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/feature-request-memory-for-small-saas-teams" target="_blank" rel="noopener">memorylake.ai › Feature request memory for small SaaS teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for every feature request");
    return `<h1>One project for every feature request</h1>
      <p class="lead">Tickets, sales-call notes, the Canny board and shared Slack channels all write into the same project, so one read sees every channel.</p>
      <div class="card"><h2 style="margin-top:0">${esc(su.name)}</h2><p class="small">${esc(su.description)}</p><div class="mono small faint">${esc(su.project)}</div></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const accts = Object.values(x.accounts).sort((a, b) => b.arr - a.arr);
    return `<h1>Intake — every account and every request, one fact each</h1>
      <p class="lead">One <code>fact add --project</code> for the accounts and one for the requests, word for word; then <code>fact update --metadata</code> on each: account, channel, date, urgency, and the <b>theme triage assigned</b> when the request came in.</p>
      <div class="cols"><div class="card"><h2 style="margin-top:0">${accts.length} accounts</h2><table class="iso"><tr><th>account</th><th>plan</th><th>ARR</th><th>renews</th></tr>
        ${accts.map((a) => `<tr><td>${esc(a.name)}</td><td class="small">${esc(a.plan)}</td><td>${a.status === "prospect" ? `<span class="pill warn">prospect</span> ${money(a.arr)}` : money(a.arr)}</td><td class="small muted">${esc(a.renews || "—")}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">${x.requests.length} requests <span class="muted small">received before today</span></h2><table class="iso"><tr><th>id</th><th>date</th><th>account · channel</th><th>request</th></tr>
        ${x.requests.map((r) => `<tr><td class="mono small">${esc(r.request)}</td><td class="small muted">${esc(r.received)}</td><td class="small">${esc(x.accounts[r.account]?.name)}<br><span class="faint">${esc(x.sources[r.source])}</span></td>
          <td class="small">${r.urgency === "blocker" ? '<span class="pill err">blocker</span> ' : ""}${esc(quote(r.text))}<br><span class="faint">${esc(r.theme)}</span></td></tr>`).join("")}</table></div></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("Four quick reads");
    const [rec, kw, tk, men] = x.reads;
    const recRows = Object.entries(rec.counts).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${esc(tname(k))} <b>${n}</b>`).join(" · ");
    const kwRows = Object.entries(kw.keyword).map(([k, v]) => `<tr><td class="mono small">--query "${esc(v.query)}"</td><td><b>${v.found.length}</b> <span class="muted small">of ${v.want.length} tagged ${esc(tname(k))}</span></td>
      <td class="small muted">${v.missed.length ? "missed " + v.missed.map(esc).join(", ") : "none missed"}</td></tr>`).join("");
    const hits = tk.hits.map((h) => `<tr><td class="mono small">#${h.rank}</td><td class="mono small" style="white-space:nowrap">${Number(h.score || 0).toFixed(3)}</td>
      <td>${h.theme === tk.theme ? '<span class="pill ok">✓ export</span>' : `<span class="pill err">✗ ${esc(h.theme || h.kind || "?")}</span>`}</td><td class="small">${esc(h.request ? quote(h.text) : h.text)}</td></tr>`).join("");
    return `<h1>Four quick reads — and every one of them says “build dark mode”</h1>
      <p class="lead">The data is the same in all four. What differs is how it is read.</p>
      <div class="card"><div class="row spread"><h2 style="margin:0">1 · What the team remembers: the last two weeks</h2><span class="pill err">→ ${esc(tname(rec.winner))}</span></div>
        <p class="small" style="margin-bottom:0">Since ${esc(x.recent_from)}: ${recRows}. Five of the six recent requests are dark mode — all from one account.</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">2 · A keyword count — <code>fact list --query</code></h2><span class="pill err">→ ${esc(tname(kw.winner))}</span></div>
        <table class="iso">${kwRows}</table><p class="small muted" style="margin-bottom:0">An exact, case-insensitive substring match: people write SAML, Okta, Google Workspace and Azure AD, not “SSO”.</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">3 · Counting search hits</h2><span class="pill err">→ ${tk.hits.length} “export requests”, ${tk.on_topic} real</span></div>
        <p class="small muted">An AI tool asks <code>search</code> and counts what comes back. Search returns top-k whether or not anything matches, and ${tk.reciprocal ? "the score is exactly <b>1/rank</b>" : "the score is not a relevance measure"} — there is no threshold to cut on.</p>
        <table class="iso"><tr><th>rank</th><th>score</th><th>theme</th><th>hit</th></tr>${hits}</table></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">4 · Counting mentions, all channels, all months</h2><span class="pill err">→ ${esc(tname(men.winner))}</span></div>
        <p class="small" style="margin-bottom:0">${esc(men.detail)} — the right data, the wrong unit: one account asking six times counts six.</p></div>`;
  }

  function table(rows, prev) {
    const max = Math.max(...rows.map((t) => t.arr + t.pipeline), 1);
    const d = (a, b, f) => (a !== undefined && a !== b ? `<span class="faint">${f(a)} →</span> <b>${f(b)}</b>` : f(b));
    return `<table class="iso"><tr><th>theme</th><th>mentions</th><th>accounts</th><th>customer ARR + pipeline</th><th>blockers</th></tr>
      ${rows.map((t) => { const p = prev?.find((q) => q.theme === t.theme);
        return `<tr><td>${esc(t.name)}<div class="faint small">${esc(t.account_names.join(", "))}</div></td><td>${d(p?.mentions, t.mentions, String)}</td><td>${d(p?.accounts.length, t.accounts.length, String)}</td>
        <td><div style="display:flex;align-items:center;gap:8px"><span style="display:inline-block;height:10px;border-radius:3px;background:var(--accent);width:${Math.round(110 * t.arr / max)}px"></span><span style="display:inline-block;height:10px;border-radius:3px;background:var(--warn-soft);border:1px solid var(--warn);width:${Math.round(110 * t.pipeline / max)}px;${t.pipeline ? "" : "display:none"}"></span>
          <span class="small">${d(p?.arr, t.arr, money)}${t.pipeline ? ` + ${money(t.pipeline)}` : ""}</span></div></td>
        <td>${t.blockers.length ? `<span class="pill err">${t.blockers.length}</span>` : "—"}</td></tr>`; }).join("")}</table>`;
  }
  function byaccount() {
    const x = S.byaccount; if (!x) return empty("Read it all, by account");
    const top = x.rows[0], dm = x.rows.find((t) => t.theme === "dark-mode");
    const reqs = S.intake?.requests || [];
    return `<h1>Read all of it, by account — who is asking and what they pay</h1>
      <p class="lead"><code>fact list --projects</code>, every page, not a top-k. Then per theme: distinct accounts, the ARR of the customers asking, prospect pipeline, blockers. Plain code over the metadata — the same memory gives the same table every time.</p>
      <div class="card">${table(x.rows)}</div>
      <div class="cols"><div class="card mine"><h2 style="margin-top:0">${esc(top.name)} <span class="pill ok">first</span></h2>
        <p class="small">${top.accounts.length} accounts · ${money(top.arr)} ARR + ${money(top.pipeline)} pipeline · ${top.blockers.length} blockers</p>
        ${top.blockers.map((id) => { const r = reqs.find((q) => q.request === id) || {}; const a = S.intake?.accounts?.[r.account] || {};
          return `<div class="memfact"><span class="mk">!</span><span class="body"><b>${esc(id)}</b> ${esc(a.name)} <span class="muted small">· ${esc(story().sources[r.source] || "")} · ${esc(r.received)}</span>
            <div>${esc(quote(r.text))}</div><div class="trace">${a.status === "customer" ? `renews ${esc(a.renews)} · ${money(a.arr)} ARR` : `prospect · ${money(a.arr)} pilot`}</div></span></div>`; }).join("")}</div>
      <div class="card"><h2 style="margin-top:0">${esc(dm.name)} <span class="pill">rank ${x.rows.indexOf(dm) + 1} of ${x.rows.length}</span></h2>
        <p class="small">${dm.mentions} mentions from ${dm.accounts.length} accounts (${esc(dm.account_names.join(", "))}) · ${money(dm.arr)} ARR. Real requests, worth keeping — and not the first thing to build.</p></div></div>`;
  }

  function arrived() {
    const x = S.arrived; if (!x) return empty("Two requests arrive today");
    const prev = S.byaccount?.rows;
    const today = story().today_requests;
    return `<h1>Two requests arrive today — mentions and accounts move differently</h1>
      <p class="lead">Triage first: <code>search --top-k 3</code> finds the nearest requests already in memory — a hint for whoever tags the theme, shown against the theme they chose. Then both are pinned and the table is read again.</p>
      <div class="cols">${today.map((r) => { const h = x.hints.find((q) => q.request === r.id);
        return `<div class="card"><h2 style="margin-top:0">${esc(r.id)} · ${esc(story().accounts.find((a) => a.key === r.account)?.name)}</h2><p>“${esc(r.text)}”</p>
          ${h ? `<div class="small">nearest in memory: ${h.hits.map((q) => `<span class="mono">${esc(q.request)}</span> ${badge(q.theme)}`).join(" ")}</div>
            <div style="margin-top:6px"><span class="pill ${h.agree ? "ok" : "err"}">${h.agree ? "✓" : "✗"} hint ${esc(h.hint)}</span> <span class="small muted">tagged ${esc(r.theme)}</span></div>` : `<div class="small muted">pinned by an earlier run · tagged ${esc(r.theme)}</div>`}</div>`; }).join("")}</div>
      <div class="card">${table(x.rows, prev)}</div>
      <div class="card">${x.moved.map((m) => `<p style="margin:4px 0"><b>${esc(m.name)}</b>: mentions ${m.mentions[0]} → <b>${m.mentions[1]}</b> · accounts ${m.accounts[0]} → <b>${m.accounts[1]}</b> · ARR ${money(m.arr[0])} → <b>${money(m.arr[1])}</b></p>`).join("")}
        <p class="small muted" style="margin-bottom:0">Counted by mentions, dark mode still leads. Counted by who is asking and what they pay, it was never close.</p></div>`;
  }

  function provHtml(p) {
    return `<h2>${esc(p.name)} — ${p.rows.length} request(s), every one back to its source</h2>
      ${p.rows.map((r) => `<div class="memfact"><span class="mk">${r.urgency === "blocker" ? "!" : "·"}</span><span class="body"><b>${esc(r.request)}</b> ${esc(r.account)} <span class="muted small">· ${esc(r.source)} · ${esc(r.received)}</span>
        <div>“${esc(r.quote)}”</div><div class="trace">${esc(r.fact)} · ${esc(r.status)} · ${money(r.arr)}${r.renews ? " · renews " + esc(r.renews) : ""}</div></span></div>`).join("")}`;
  }
  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the requests</h1>
      <p class="lead">Any question: <code>search</code>'s top 8 as it comes back (rank, score, theme), then the whole theme behind its first request hit, read with <code>fact list</code> — every request, back to its account, channel and date.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What do customers want for logging in?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).concat(["Which customers need data exports?"]).map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div class="chips">${Object.entries(story()?.themes || {}).map(([k, v]) => `<button type="button" class="chip" data-theme="${esc(k)}">theme: ${esc(v)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    st.querySelectorAll("[data-theme]").forEach((b) => b.addEventListener("click", () => askTheme(b.dataset.theme)));
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
    S.ask.html = `<h2>search “${esc(r.q)}” — top ${r.hits.length} as returned</h2><table class="iso"><tr><th>rank</th><th>score</th><th>theme</th><th>hit</th></tr>
      ${r.hits.map((h) => `<tr><td class="mono small">#${h.rank}</td><td class="mono small" style="white-space:nowrap">${Number(h.score || 0).toFixed(3)}</td><td>${badge(h.theme || h.kind || "?")}</td><td class="small">${esc(h.text)}</td></tr>`).join("")}</table>`
      + (r.full ? provHtml(r.full) : '<p class="muted">No request among the hits.</p>');
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function askTheme(theme) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/theme", { theme });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = provHtml(r);
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.byaccount = null; S.arrived = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's project and every request on it from your account?")) return;
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
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "intake") S.intake = d;
    else if (k === "quick_reads") S.quick = d;
    else if (k === "by_account") S.byaccount = d;
    else if (k === "arrived") S.arrived = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and every request on it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask the requests a question of your own — “Ask the requests” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick_reads", "by_account", "arrived",
    "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.arrived ? "arrived" : "connect");
    subscribe();
  })();
})();

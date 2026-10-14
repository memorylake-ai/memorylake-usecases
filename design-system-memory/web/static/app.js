/* Design system memory for design studios — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "studio system + one project per client", view: "setup" },
    { n: 3, title: "The system in memory", sub: "tokens, bans and why, per layer", view: "system" },
    { n: 4, title: "How an AI tool reads it", sub: "client alone, or studio + client", view: "reads" },
    { n: 5, title: "Layered", sub: "client wins · yesterday's drafts", view: "layered" },
    { n: 6, title: "The studio changes a token", sub: "who inherits it, who doesn't", view: "evolved" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, system: null, reads: null, layered: null, evolved: null,
    ask: { client: "orchard", q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const lab = (k) => story()?.layers?.[k]?.label || k;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const isColor = (v) => /^#[0-9a-f]{6}$/i.test(String(v || ""));
  const val = (v) => `${isColor(v) ? `<span class="swatch" style="background:${esc(v)}"></span>` : ""}<b>${esc(v)}</b>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask a design question<small>three ways to read memory</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, system, reads, layered, evolved, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Design system</dt><dd>${st.ready ? "from an earlier run — Run again reads it back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1–2 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>One studio system, a layer per client — and the client has to win.</h1>
      <p class="lead">Kiteline Studio keeps its design system in two layers: the <b>studio system</b> every client starts from, and <b>one project per client</b> holding only what that client changes.
      An AI tool that reads the client alone misses every rule the client didn't change; one that reads both gets two values for every token the client did change, with nothing saying which wins.
      Read layered, yesterday's AI drafts are reviewed line by line — and a change to the studio system reaches exactly the clients that inherit it.
      This page drives the <code>memorylake</code> CLI for real. The studio, clients and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the three projects and every token on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/design-system-memory-for-design-studios" target="_blank" rel="noopener">memorylake.ai › Design system memory for design studios</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("The studio system, and one project per client");
    return `<h1>The studio system, and one project per client</h1>
      <p class="lead">Each layer is its own project. The client projects hold <b>only</b> what the client changes — so the studio can change its system once and every client that doesn't override a token inherits it.</p>
      <div class="grid3">${Object.entries(su.layers).map(([k, l]) => `<div class="card"><h2 style="margin-top:0">${esc(l.name)}</h2>
        <div class="muted small">${l.client ? "only what this client changes" : "every client system starts from this"}</div><p class="small">${esc(l.description)}</p>
        <div class="mono small faint">${esc(su.ids[k])}</div></div>`).join("")}</div>`;
  }

  function layerCard(k, ents) {
    const toks = ents.filter((e) => e.layer === k && e.kind === "token").sort((a, b) => a.token.localeCompare(b.token));
    const bans = ents.filter((e) => e.layer === k && e.kind === "banned");
    return `<div class="card"><h2 style="margin-top:0">${esc(story().layers[k].name)} <span class="muted small">${toks.length} token(s) · ${bans.length} banned</span></h2>
      <table class="iso"><tr><th>token</th><th>value</th><th>why</th></tr>
      ${toks.map((e) => `<tr><td class="mono small">${esc(e.token)}</td><td>${val(e.value)} <span class="faint small">v${esc(e.version)}</span></td><td class="small muted">${esc(e.why)}<br><span class="faint">${esc(e.by)}, ${esc(e.decided)}</span></td></tr>`).join("")}
      ${bans.map((e) => `<tr><td class="small"><span class="pill err">banned</span></td><td class="mono small">${esc(e.pattern)}</td><td class="small muted">${esc(e.why)}<br><span class="faint">${esc(e.by)}, ${esc(e.decided)}</span></td></tr>`).join("")}</table></div>`;
  }
  function system() {
    const x = S.system; if (!x) return empty("The design system in memory");
    return `<h1>The design system in memory — tokens, banned patterns, and why</h1>
      <p class="lead">Each token is one fact (<code>fact add --project</code>) with metadata via <code>fact update --metadata</code>: token, value, layer, version, who decided it, when, and why.
      ${Object.entries(x.overrides).map(([k, v]) => `${esc(lab(k))} overrides ${v.length} of the studio's tokens`).join("; ")}.</p>
      <div class="cols">${layerCard("base", x.entries)}<div>${Object.keys(x.overrides).map((k) => layerCard(k, x.entries)).join("")}</div></div>`;
  }

  function readRow(r) {
    const c = lab(r.client);
    const only = r.hit_only.length ? `<span class="pill ok">✓</span> ${val(r.hit_only[0].value)} <span class="muted small">(${esc(lab(r.hit_only[0].layer))}, #${r.hit_only[0].rank})</span>`
      : `<span class="pill err">✗</span> no <code>${esc(r.token)}</code> in this scope <span class="muted small">(${r.only.length} hits, none about it) — the tool has to guess</span>`;
    const both = r.hit_both.length ? `<span class="pill ${r.ambiguous ? "warn" : "ok"}">${r.ambiguous ? "⚠ two answers" : "✓"}</span> `
      + r.hit_both.map((h) => `${val(h.value)} <span class="muted small">(${esc(lab(h.layer))}, #${h.rank})</span>`).join(" · ")
      + (r.ambiguous ? `<div class="small" style="margin-top:4px">a tool taking the first match uses <b>${esc(r.first.value)}</b> (${esc(lab(r.first.layer))})</div>` : "")
      : `<span class="pill err">✗</span> not in the top 8`;
    const lay = r.layered ? `<tr><td class="small">layered (client wins)</td><td><span class="pill ok">✓</span> ${val(r.layered.value)} <span class="muted small">(${esc(r.layered.source)})</span></td></tr>` : "";
    return `<div class="card"><h2 style="margin-top:0">“${esc(r.q)}” <span class="mono small muted">${esc(r.token || "")}</span></h2>
      <table class="iso"><tr><td class="small" style="width:220px">${esc(c)} only <span class="faint">(top 5)</span></td><td>${only}</td></tr>
      <tr><td class="small">studio + ${esc(c)} <span class="faint">(top 8)</span></td><td>${both}</td></tr>${lay}</table></div>`;
  }
  function reads() {
    const x = S.reads; if (!x) return empty("How an AI tool reads it");
    return `<h1>How an AI tool reads it — the client alone, or the studio and the client together</h1>
      <p class="lead">Before it generates, an AI design tool searches memory. Scoped to <b>${esc(lab(x.client))}</b> alone (<code>search --projects &lt;client&gt;</code>), or to the studio system and the client together
      (<code>search --projects &lt;studio&gt;,&lt;client&gt;</code>). Real <code>search</code> results, ranks as returned.</p>
      ${x.rows.map(readRow).join("")}
      <div class="card"><p style="margin:0"><span class="pill err">client alone</span> ${esc(lab(x.client))} holds ${x.overridden.length} tokens; the other ${x.missing.length}
        (<span class="mono small">${esc(x.missing.join(", "))}</span>) are not in its scope at all.</p>
        <p style="margin:8px 0 0"><span class="pill warn">studio + client</span> two values for each of those ${x.overridden.length}, and search ranks by relevance — it has no notion of which layer wins.</p></div>`;
  }

  function resolvedCard(res) {
    const c = lab(res.client);
    return `<div class="card"><h2 style="margin-top:0">${esc(c)} <span class="muted small">as of ${esc(res.day)}</span></h2>
      <table class="iso"><tr><th>token</th><th>value</th><th>from</th></tr>
      ${Object.values(res.tokens).map((t) => `<tr><td class="mono small">${esc(t.token)}</td><td>${val(t.value)}</td>
        <td class="small">${t.layer === "base" ? '<span class="muted">studio</span>' : `<span class="pill ok">${esc(c)}</span> <span class="faint">studio: ${esc(t.overrides)}</span>`} <span class="faint">v${esc(t.version)}</span></td></tr>`).join("")}</table>
      <p class="small muted" style="margin-bottom:0">banned: ${res.banned.map((b) => `<span class="mono">${esc(b.pattern)}</span> (${esc(lab(b.layer))})`).join(", ")}</p></div>`;
  }
  function reviewCard(rv) {
    return `<div class="card ${rv.bad ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(rv.id)} · ${esc(rv.title)}</h2>
      <span class="pill ${rv.bad ? "err" : "ok"}">${rv.bad ? `✗ ${rv.bad} finding(s)` : "✓ passes"}</span></div>
      <div class="small muted" style="margin:4px 0 8px">made by ${esc(rv.made_by)} · reviewed as of ${esc(rv.day)}</div>
      ${rv.findings.map((f) => `<div class="memfact ${f.ok ? "clean" : "nomatch"}"><span class="mk">${f.ok ? "✓" : f.ok === null ? "?" : "✗"}</span>
        <span class="body"><span class="mono small">${esc(f.item)}</span> ${val(f.used)}<div class="trace">${esc(f.why)}</div></span></div>`).join("")}</div>`;
  }
  function layered() {
    const x = S.layered; if (!x) return empty("Layered");
    return `<h1>Layered: the client wins, token by token</h1>
      <p class="lead">One <code>fact list --projects &lt;studio&gt;,&lt;client&gt;</code> — every page, not a top-k — then every token from the studio, replaced by the client's override where there is one.
      Yesterday (${esc(x.day)}) three AI tools produced drafts; each is reviewed against its client's layered system as of that day, every finding citing the layer, version and reason.</p>
      <div class="grid">${Object.values(x.systems).map(resolvedCard).join("")}</div>
      ${x.reviews.map(reviewCard).join("")}`;
  }

  function evolved() {
    const x = S.evolved; if (!x) return empty("The studio changes a token");
    const before = S.layered?.reviews || [];
    return `<h1>The studio changes one token today — who inherits it, who doesn't</h1>
      <p class="lead">${esc(story().people[x.update.by].name)} changes <code>${esc(x.token)}</code> in the studio system to <b>${esc(x.update.value)}</b> (“${esc(x.update.why)}”):
      one <code>fact update</code> on the studio project, nothing on any client project.</p>
      <div class="card"><table class="iso"><tr><th>client</th><th>${esc(story().drafts_made)}</th><th>${esc(story().today)}</th><th></th></tr>
        ${x.rows.map((r) => `<tr><td>${esc(lab(r.client))}</td><td>${val(r.old)}</td><td>${val(r.new)}</td><td>${r.moved ? '<span class="pill warn">changed — inherits the studio value</span>' : `<span class="pill">unchanged — ${esc(lab(r.client))} overrides it</span>`}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">The same three drafts</h2><table class="iso"><tr><th>draft</th><th>${esc(story().drafts_made)}</th><th>${esc(story().today)}</th></tr>
        ${x.reviews.map((rv) => { const was = before.find((b) => b.id === rv.id) || {}; const m = (n) => (n ? `<span class="pill err">✗ ${n}</span>` : '<span class="pill ok">✓</span>');
          return `<tr><td>${esc(rv.id)} · ${esc(rv.title)}</td><td>${m(was.bad)}</td><td>${m(rv.bad)}</td></tr>`; }).join("")}</table></div>
      ${x.reviews.filter((rv) => x.flips.some((f) => f.id === rv.id)).map(reviewCard).join("")}
      <div class="card"><h2 style="margin-top:0"><code>fact trace</code> — how the token changed <span class="mono small faint">${esc(x.fact)}</span></h2>
        ${x.history.map((h) => `<div class="memfact"><span class="mk">${esc(h.event === "ADD" ? "+" : "~")}</span><span class="body"><b>${esc(h.event)}</b> <span class="muted small">${esc(h.how)} · ${esc(String(h.at || "").slice(0, 19))}</span>
          <div>${esc(h.text)}</div>${h.old ? `<div class="trace">was: ${esc(h.old)}</div>` : ""}</span></div>`).join("")}</div>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    const cs = Object.entries(story()?.layers || {}).filter(([, l]) => l.client);
    return `<h1>Ask a design question</h1>
      <p class="lead">Any question, for either client: the client-only read, the studio + client read, and the layered answer, side by side. The token is taken from the first token hit in the studio + client search.</p>
      <div class="card"><div class="chips">${cs.map(([k, l]) => `<button type="button" class="chip ${S.ask.client === k ? "on" : ""}" data-client="${esc(k)}">${esc(l.label)}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. How big is body text?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["How big is body text?", "What should the card shadow look like?", "Which font do headings use?", "How much padding goes inside a dialog?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelectorAll("[data-client]").forEach((b) => b.addEventListener("click", () => { S.ask.client = b.dataset.client; S.ask.q = $("#ask-q")?.value || S.ask.q; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { client: S.ask.client, q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = r.token ? readRow(r) : '<p class="muted">No token in the studio + client search for this question.</p>';
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
  function resetView() { S.setup = null; S.system = null; S.reads = null; S.layered = null; S.evolved = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's three projects and every token on them from your account?")) return;
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
    else if (k === "system") S.system = d;
    else if (k === "ai_reads") S.reads = d;
    else if (k === "layered") S.layered = d;
    else if (k === "evolved") S.evolved = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the three projects and every token on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a design question of your own — “Ask a design question” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "system", "ai_reads", "layered", "evolved",
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
    show(state.status === "running" ? S.view : S.evolved ? "evolved" : "connect");
    subscribe();
  })();
})();

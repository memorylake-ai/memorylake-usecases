/* Per-tenant memory for multi-tenant agent platforms — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const short = (i) => (i ? String(i).slice(0, 14) + "…" : "—");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key and team", view: "connect" },
    { n: 2, title: "Provision", sub: "one workspace per tenant", view: "tenants" },
    { n: 3, title: "Write memory", sub: "rules + a conversation each", view: "memory" },
    { n: 4, title: "Leak matrix", sub: "same question, 3 tenants", view: "matrix" },
    { n: 5, title: "One person", sub: "two tenants, two memories", view: "person" },
    { n: 6, title: "Default-deny", sub: "naming another tenant", view: "deny" },
    { n: 7, title: "Offboard", sub: "workspace delete", view: "offboard" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, tenants: {}, sessions: {}, counts: null, matrix: null, person: null, deny: null, off: null, done: null,
    ask: { tenant: "harbor", q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const tname = (k) => story()?.tenants.find((t) => t.key === k)?.name || k;
  const pname = (k) => story()?.people.find((p) => p.key === k)?.display || k;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>inside one tenant</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, tenants, memory, matrix, person, deny, offboard, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Tenant workspaces</dt><dd>${st.ready?.length ? st.ready.map(tname).map(esc).join(", ") + " — from an earlier run; running again reuses them" : '<span class="muted">none yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready?.length ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3–6 minutes · creates three workspaces, custom ids <code>${esc(S.data?.prefix || "")}-…</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Can one tenant's memory ever show up in another tenant's agent?</h1>
      <p class="lead">Relaybench hosts one support agent, Relay, for three companies. Each tenant gets its own MemoryLake <b>workspace</b>.
      The demo asks the same question inside all three and scans every hit for every tenant's marker words, shows one customer of two tenants with two separate memories,
      has one tenant name another tenant's data, and offboards a tenant with <code>workspace delete</code> — then lists what that command leaves behind.
      This page drives the <code>memorylake</code> CLI for real. The platform, its tenants and their customers are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI must be v20261013 or later</b> (for <code>workspace delete</code>); this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the demo's workspaces, Library folders and actors; your own workspaces are never touched.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-sharding-for-multi-tenant-agent-platforms" target="_blank" rel="noopener">memorylake.ai › memory sharding for multi-tenant agent platforms</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function tenants() {
    const ts = Object.values(S.tenants); if (!ts.length) return empty("One workspace per tenant");
    return `<h1>One workspace per tenant</h1>
      <p class="lead"><code>workspace create</code>, a project, two policy documents imported from the tenant's own Library folder, and the actors bound to it.
      Actors are team-wide; <b>binding</b> one to a workspace lets it hold memory there — separately in each.</p>
      <div class="grid3">${ts.map((t) => `<div class="card"><h2 style="margin-top:0">${esc(t.name)}</h2>
        <dl class="kv"><dt>workspace</dt><dd class="mono small">${esc(t.ws)}</dd><dt>project</dt><dd class="mono small">${esc(short(t.project))}</dd>
        <dt>Library</dt><dd class="mono small">${esc(t.folder)}/</dd><dt>bound</dt><dd class="small">Relay${t.people.map((p) => ", " + esc(pname(p))).join("")}</dd></dl></div>`).join("")}</div>`;
  }

  function memory() {
    const ss = Object.values(S.sessions); if (!ss.length) return empty("Write memory");
    return `<h1>Pinned rules and a real support conversation, inside each tenant</h1>
      <p class="lead"><code>fact add --project</code> for two tenant rules, then a DIRECT conversation (customer ↔ Relay) that MemoryLake turns into memory.
      Nothing names a tenant: the <code>--workspace</code> flag is the whole routing decision.</p>
      <div class="grid3">${(story()?.tenants || []).map((t) => {
        const s = ss.filter((x) => x.tenant === t.key);
        return `<div class="card"><h2 style="margin-top:0">${esc(t.name)}</h2>
          ${t.facts.map((f) => `<div class="memfact"><span class="body small">${esc(f)}</span></div>`).join("")}
          ${s.map((x) => `<div class="small" style="margin-top:8px">💬 <b>${esc(pname(x.person))}</b> — ${esc(x.title)} <span class="faint">(${x.turns} turns)</span></div>`).join("")}
          ${S.counts ? `<div class="bignum" style="margin-top:10px">${S.counts[t.key] ?? "—"}</div><div class="muted small">facts in this tenant</div>` : ""}</div>`;
      }).join("")}</div>`;
  }

  function matrix() {
    const m = S.matrix; if (!m) return empty("Leak matrix");
    const head = `<tr><th>asked inside ↓ · hits with markers of →</th>${m.tenants.map((o) => `<th>${esc(o.name)}</th>`).join("")}</tr>`;
    const rows = m.tenants.map((t) => `<tr><td><b>${esc(t.name)}</b></td>${m.tenants.map((o) => {
      const n = m.matrix[t.key][o.key]; const good = o.key === t.key ? n > 0 : n === 0;
      return `<td><span class="pill ${good ? "ok" : "err"}" style="font-size:15px">${n} ${good ? "✓" : "✗"}</span></td>`; }).join("")}</tr>`).join("");
    return `<h1>The same question in every tenant — ${m.leaks} cross-tenant hits</h1>
      <p class="lead">“${esc(m.question)}” — asked inside each workspace (facts and document paragraphs). Every hit is scanned for every tenant's marker words.
      The diagonal is the positive control: the scan finds markers when they are there.</p>
      <div class="card"><table class="iso">${head}${rows}</table>
        <p style="margin-bottom:0"><span class="pill ${m.leaks === 0 ? "ok" : "err"}">cross-tenant hits: ${m.leaks}</span> <span class="pill ${m.own ? "ok" : "err"}">every tenant found its own answer: ${m.own ? "yes" : "no"}</span></p></div>
      <div class="grid3">${m.tenants.map((t) => `<div class="card"><h2 style="margin-top:0">${esc(t.name)} — top facts</h2>
        ${(m.samples[t.key] || []).map((f) => `<div class="memfact clean"><span class="body small">${esc(f)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function person() {
    const p = S.person; if (!p) return empty("One person, two tenants");
    const keys = Object.keys(p.views);
    return `<h1>${esc(p.person)}: one actor id, two separate memories</h1>
      <p class="lead"><code>fact list --actors ${esc(p.actor)}</code>, once inside each workspace. The two preferences even contradict each other — and neither tenant's agent ever sees the other one.</p>
      <div class="grid">${keys.map((k) => `<div class="card"><h2 style="margin-top:0">inside ${esc(tname(k))}</h2><div class="muted small">${p.views[k].length} fact(s)</div>
        ${p.views[k].map((f) => `<div class="memfact"><span class="body small">${esc(f)}</span></div>`).join("")}</div>`).join("")}</div>
      <p><span class="pill ${p.shared === 0 ? "ok" : "err"}">shared fact ids: ${p.shared}</span> <span class="pill ${p.foreign === 0 ? "ok" : "err"}">facts mentioning the other tenant: ${p.foreign}</span></p>`;
  }

  function deny() {
    const d = S.deny; if (!d) return empty("Default-deny");
    return `<h1>Default-deny — refused ${d.refused} of ${d.total}</h1>
      <p class="lead">Code running for Harbor Dental names Kestrel Freight's project id, or a person who is not Harbor's customer — a bug, or a forged request.
      A request that mixes an allowed project with a foreign one is refused whole: it never returns the allowed half.</p>
      <div class="card"><table class="iso"><tr><th>request (inside Harbor Dental)</th><th></th><th>server says</th></tr>
        ${d.results.map((r) => `<tr><td class="small">${esc(r.label)}</td><td><span class="pill ${r.refused ? "ok" : "err"}">${r.refused ? "refused ✓" : "allowed ✗"}</span></td><td class="mono small">${esc(r.message)}</td></tr>`).join("")}</table></div>`;
  }

  function offboard() {
    const o = S.off; if (!o) return empty("Offboard a tenant");
    const ok = o.checks.filter((c) => c.ok).length;
    return `<h1>${esc(o.tenant)} leaves — <code>workspace delete</code>, and what it leaves behind</h1>
      <p class="lead">No prompt: <code>--confirm</code> must repeat the workspace's exact name. Before: ${Object.entries(o.before).map(([k, v]) => `${esc(tname(k))} ${v}`).join(" · ")} facts.</p>
      <div class="grid"><div class="card"><div class="muted small">wrong name</div><div class="bignum err">refused</div><div class="small mono">${esc(o.refusal)}</div></div>
        <div class="card"><div class="muted small">exact name</div><div class="bignum ok">${o.took}s</div><div class="small">workspace deleted, with its project, documents, conversation and memories</div></div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Still there after <code>workspace delete</code></h2><span class="pill ${o.leftovers.length ? "err" : "ok"}">${o.leftovers.length} left behind</span></div>
        <p class="small muted">The help says its projects, documents and memories go with it. Memories do (404). These do not:</p>
        ${o.leftovers.map((l) => `<div class="memfact personal"><span class="mk">!</span><span class="body small"><span class="pill">${esc(l.kind)}</span> ${esc(l.what)}</span></div>`).join("") || '<p class="muted">nothing</p>'}
        <p class="small muted" style="margin-bottom:0">Projects and boundaries answer through the deleted workspace's id; the Library folder and the actor are team-level. The demo deletes each, then checks again.</p></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Checks</h2><span class="pill ${ok === o.checks.length ? "ok" : "err"}">${ok}/${o.checks.length} ✓</span></div>
        ${o.checks.map((c) => `<div class="memfact ${c.ok ? "clean" : "personal"}"><span class="mk">${c.ok ? "✓" : "✗"}</span><span class="body small">${esc(c.label)} <span class="faint mono">${esc(c.detail)}</span></span></div>`).join("")}</div>`;
  }

  function askRows(r) {
    const others = (story()?.tenants || []).filter((t) => t.key !== r.key);
    return `<h2>${r.rows.length} hits inside ${esc(r.tenant)} <span class="pill ${r.foreign ? "err" : "ok"}">${r.foreign} mention another tenant</span></h2>` + (r.rows.map((h) => {
      const own = h.markers[r.key] || []; const bad = others.flatMap((o) => (h.markers[o.key] || []).map((m) => `${o.name}: ${m}`));
      return `<div class="memfact ${bad.length ? "personal" : "clean"}"><span class="mk">${h.kind === "doc" ? "📄" : "•"}</span><span class="body small">${esc(h.text)}
        <div class="faint" style="margin-top:3px">${own.length ? "markers: " + own.map(esc).join(", ") : ""}${bad.length ? ` <b>other tenant: ${bad.map(esc).join(", ")}</b>` : ""}</div></span></div>`;
    }).join("") || '<p class="muted">nothing</p>');
  }
  function ask() {
    const ready = S.st.ready || [];
    const chips = (story()?.tenants || []).map((t) => `<button type="button" class="chip ${S.ask.tenant === t.key ? "on" : ""}" data-t="${t.key}" ${ready.includes(t.key) ? "" : "disabled"}>${esc(t.name)}</button>`).join("");
    const can = ready.includes(S.ask.tenant);
    return `<h1>Ask inside one tenant</h1>
      <p class="lead">Pick a tenant, ask anything. Every hit is scanned for every tenant's markers. A tenant that was offboarded has no workspace to ask.</p>
      <div class="card"><div class="chips" style="margin:0 0 10px">${chips}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. How does this customer want to be contacted?" value="${esc(S.ask.q)}" ${can ? "" : "disabled"}><button class="btn primary" ${can ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["How does this customer want to be contacted?", "What is the refund policy?", "Who handles complaints?", "Does this customer have any injury or sensitivity?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready.length ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  // ---------------------------------------------------------------- behaviour
  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    st.querySelectorAll(".chips .chip[data-t]").forEach((c) => c.addEventListener("click", () => { S.ask.tenant = c.dataset.t; S.ask.html = ""; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q || !(S.st.ready || []).includes(S.ask.tenant)) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching inside ${esc(tname(S.ask.tenant))}…</span>`;
    const r = await api("/api/ask", { tenant: S.ask.tenant, q });
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
    $("#conn-text").textContent = st.status === "running" ? `running ${st.job}…` : st.connected ? `${st.team?.name || "connected"}` : "Not connected";
    const busy = st.status === "running";
    ["#btn-run", "#btn-reset", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.tenants = {}; S.sessions = {}; S.counts = null; S.matrix = null; S.person = null; S.deny = null; S.off = null; S.done = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's three workspaces (with everything in them), its Library folders and its actors from your account?")) return;
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
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running";
      if (d.index === 2) resetView();
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") {
      // Two shapes: the server's state snapshot ({connected, team: {...}}) and demo.connect's own ({team, role}).
      if (S.caughtUp) S.st = "connected" in d ? d : Object.assign({}, S.st, { connected: true, team: { name: d.team, role: d.role } });
      S.stepStatus[1] = "done"; setConn();
    }
    else if (k === "tenant") S.tenants[d.key] = d;
    else if (k === "session") S.sessions[`${d.tenant}-${d.person}`] = d;
    else if (k === "memory") S.counts = d.counts;
    else if (k === "matrix") S.matrix = d;
    else if (k === "person") S.person = d;
    else if (k === "deny") S.deny = d;
    else if (k === "offboard") S.off = d;
    else if (k === "done") {
      if (d.job) {
        finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
        if (d.job === "cleanup") { S.st.ready = []; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's workspaces, Library folders and actors are gone from your account.", true); }
        else if (S.caughtUp) banner("Done. Try “Ask” inside Harbor Dental or Kestrel Freight — Nimbus Yoga was offboarded.", true);
        if (S.caughtUp) refreshState();
      } else S.done = d;   // the pipeline's own summary
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); if (S.view === "connect" || S.view === "ask") renderStage(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "tenant", "session", "memory", "matrix", "person", "deny", "offboard", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.off ? "offboard" : "connect");
    subscribe();
  })();
})();

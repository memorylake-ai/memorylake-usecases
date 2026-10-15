/* Eviction memory for long-running agents — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "memory, three active stores, archive", view: "setup" },
    { n: 3, title: "Six months", sub: "36 entries, cap 20", view: "intake" },
    { n: 4, title: "Evict", sub: "three policies, 16 out each", view: "evict" },
    { n: 5, title: "Three tasks", sub: "the same search, three memories", view: "tasks" },
    { n: 6, title: "Cold archive", sub: "moved out, not deleted", view: "archive" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, evict: null, tasks: [], score: null, archive: null,
    ask: { q: "", needs: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const POL = ["oldest", "lfu", "typed"];
  const polName = (k) => story()?.policies?.[k]?.label || k;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const TYPE_CLS = { decision: "accent", constraint: "err", preference: "", event: "warn", turn: "" };
  const typePill = (t) => `<span class="pill ${TYPE_CLS[t] || ""}">${esc(t)}</span>`;
  const okPill = (r) => r.ok ? `<span class="pill ok">✓ ${esc(r.needs)} at #${esc(r.rank)}</span>`
    : `<span class="pill err">✗ ${esc(r.needs)} ${r.held === false ? "evicted" : "not in the top 8"}</span>`;
  const rec = (id) => S.intake?.records?.find((r) => r.record === id);
  function hitTable(hits, needs, where) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>entry</th></tr>
      ${hits.map((h) => `<tr class="${h.record === needs ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${typePill(h.type)}${where && h.where === "archive" ? ' <span class="pill warn">archive</span>' : ""}${h.record === needs ? ' <span class="pill ok">needed</span>' : ""}</td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Check<small>any task · three memories</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, evict, tasks, archive, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${st.ready ? "from an earlier run — Run again reads it back and re-checks the tasks, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about two minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Drop the oldest, lose the founding rule. Drop the least used, lose the rare one.</h1>
      <p class="lead">Tern is the platform-operations agent at Kestrel Lane Freight. In six months it has remembered 36 things, and its active memory is capped at 20.
      Two naive eviction policies each throw away one entry a real task needs; typed retention keeps both, and moves what it evicts to a cold archive instead of deleting it.
      Each policy gets its own active-memory project; the same three tasks are searched against all three.
      This page drives the <code>memorylake</code> CLI for real. The company, agent and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the five projects and every entry on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-eviction-strategy-for-long-running-agents" target="_blank" rel="noopener">memorylake.ai › Memory eviction for long-running agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("Five projects");
    return `<h1>Tern's memory, one active memory per policy, a cold archive</h1>
      <p class="lead">The full memory as it stands at month six; then each eviction policy's active memory as its own project — the 20 entries it kept, word for word; and the cold archive typed retention moves its evictions to.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        ${Object.keys(su.projects).map((k) => `<tr><td><span class="pill ${k === "archive" ? "warn" : k === "memory" ? "accent" : ""}">${esc(k)}</span></td><td><b>${esc(su.names[k])}</b></td><td class="mono small faint">${esc(su.projects[k])}</td></tr>`).join("")}</table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Six months of memory");
    return `<h1>Six months of memory — ${x.records.length} entries, cap ${x.cap}</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each: type, the day it was written, how often Tern has retrieved it, when it last did. That is all an eviction job gets to read.</p>
      <div class="card"><table class="iso"><tr><th>id</th><th>type</th><th>written</th><th>uses</th><th>last used</th><th>entry</th></tr>
        ${x.records.map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td>${typePill(r.type)}</td><td class="mono small" style="white-space:nowrap">${esc(r.on)}</td><td class="mono small">${esc(r.uses)}</td><td class="mono small" style="white-space:nowrap">${esc(r.last_used)}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`;
  }

  function evict() {
    const x = S.evict; if (!x) return empty("Evict");
    const need = (story()?.tasks || []).filter((t) => !t.control).map((t) => t.needs);
    return `<h1>Evict — 16 out under each policy</h1>
      <p class="lead">Plain code over the metadata <code>fact list</code> returned. Each policy's 20 go into its own active-memory project; typed retention's 16 go to the cold archive with an eviction record.</p>
      ${POL.map((k) => { const p = x.plans[k]; const lost = p.evict.filter((e) => need.includes(e));
        return `<div class="card ${lost.length ? "mine" : ""}"><div class="row spread"><h2 style="margin:0">${esc(polName(k))}</h2><span class="pill">${p.keep.length} kept · ${x.counts[k]} in its project</span></div>
        <p class="small">${esc(story()?.policies?.[k]?.rule)}</p>
        <div class="chips">${p.evict.map((e) => `<span class="pill ${need.includes(e) ? "err" : ""}">${esc(e)}</span>`).join(" ")}</div>
        ${lost.map((e) => `<p class="small" style="margin-bottom:0"><b>✗ evicts ${esc(e)}</b> (${esc(rec(e)?.type)}): “${esc(rec(e)?.body)}” — ${esc(p.why[e])}</p>`).join("")}</div>`; }).join("")}
      <p class="small faint">cold archive: ${esc(x.counts.archive)} entries</p>`;
  }

  function tasks() {
    if (!S.tasks.length) return empty("The same three tasks");
    const sc = S.score;
    return `<h1>The same three tasks against each active memory</h1>
      <p class="lead">Each task text, <code>search --projects &lt;that policy's active memory&gt; --top-k 8</code>. The rules Tern sees are the decisions and constraints among the hits; the answer key is the entry the task needs.</p>
      <div class="card mine"><table class="iso"><tr><th>task</th>${POL.map((k) => `<th>${esc(polName(k))}</th>`).join("")}</tr>
        ${S.tasks.map((r) => `<tr><td class="small"><span class="mono">${esc(r.task.id)}</span> ${esc(r.task.text)}${r.task.control ? ' <span class="pill">control</span>' : ""}</td>${POL.map((k) => `<td>${okPill(r.results[k])}</td>`).join("")}</tr>`).join("")}
        ${sc ? `<tr><td class="small"><b>tasks with the rule in view</b></td>${POL.map((k) => `<td class="mono small"><b>${sc.score[k]}/${sc.n}</b></td>`).join("")}</tr>` : ""}</table></div>
      ${S.tasks.map((r) => `<div class="card"><h2 style="margin-top:0">${esc(r.task.id)} · “${esc(r.task.text)}”</h2>
        <p class="small">needs <b>${esc(r.task.needs)}</b>: “${esc(rec(r.task.needs)?.body)}”</p>
        <div class="cols3">${POL.map((k) => { const x = r.results[k]; return `<div><h3>${esc(polName(k))}</h3>${okPill(x)}
          <p class="small">rules seen: <span class="mono">${esc(x.rules.join(" ") || "—")}</span></p>${hitTable(x.hits.slice(0, 3), r.task.needs)}
          ${x.ok ? "" : `<p class="small"><b>Tern goes ahead:</b> ${esc(r.task.harm)}</p>`}</div>`; }).join("")}</div></div>`).join("")}`;
  }

  function archive() {
    const x = S.archive; if (!x) return empty("The cold archive");
    const d = x.deep, f = d.found;
    return `<h1>The cold archive — moved out, not deleted</h1>
      <p class="lead">“Old memory archived; available on demand, not in active retrieval.” A question about an old incident: the typed active memory alone, then active + archive in one <code>search --projects active,archive</code>.</p>
      <div class="card mine"><h2 style="margin-top:0">“${esc(d.text)}”</h2><div class="cols">
        <div><h3>Typed active memory only</h3><p class="small">${d.in_active ? `<span class="pill ok">${esc(d.needs)} found</span>` : `<span class="pill err">${esc(d.needs)} not here</span>`}</p>${hitTable(d.active.slice(0, 3), d.needs)}</div>
        <div><h3>Active + cold archive</h3><p class="small">${f ? `<span class="pill ok">${esc(f.record)} at #${esc(f.rank)} from the ${esc(f.where)}</span>` : `<span class="pill err">${esc(d.needs)} not in the top 8</span>`}</p>${hitTable(d.both.slice(0, 3), d.needs, true)}
          ${f?.evicted_on ? `<p class="small">eviction record: evicted ${esc(f.evicted_on)} by ${esc(f.policy)} — ${esc(f.reason)}</p>` : ""}</div></div></div>
      <div class="card"><h2 style="margin-top:0">Eviction log · ${x.log.length} entries, none deleted</h2><table class="iso"><tr><th>id</th><th>type</th><th>evicted</th><th>reason</th></tr>
        ${x.log.map((r) => `<tr><td class="mono small">${esc(r.record)}</td><td>${typePill(r.type)}</td><td class="mono small" style="white-space:nowrap">${esc(r.evicted_on)}</td><td class="small">${esc(r.reason)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask;
    const ids = (S.intake?.records || []).map((r) => r.record);
    return `<h1>Check</h1>
      <p class="lead">Any task against the three active memories: the top 8 each one returns and the rules among them. Pick the entry the task needs to mark it in each result.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Deploy the rates service on Friday at 16:00 UTC" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-n" ${ready ? "" : "disabled"} style="max-width:140px"><option value="">needs: —</option>${ids.map((i) => `<option ${i === a.needs ? "selected" : ""}>${esc(i)}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Check</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-needs="${esc(e.needs)}">${esc(e.q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? a.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askCheck($("#ask-q").value.trim(), $("#ask-n").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-n").value = c.dataset.needs; askCheck(c.dataset.q, c.dataset.needs); }));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askCheck(q, needs) {
    if (!q) return; S.ask.q = q; S.ask.needs = needs;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching three memories…</span>`;
    const r = await api("/api/check", { q, needs });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<h2>“${esc(r.text)}”${r.needs ? ` · needs ${esc(r.needs)}` : ""}</h2>
      <div class="cols3">${POL.map((k) => { const x = r.results[k]; return `<div><h3>${esc(r.labels[k])}</h3>${r.needs ? okPill(x) : ""}
        <p class="small">rules seen: <span class="mono">${esc(x.rules.join(" ") || "—")}</span></p>${hitTable(x.hits, r.needs)}
        ${x.cached?.length ? `<p class="small faint">search answered ${esc(x.cached.join(", "))} from its cache</p>` : ""}</div>`; }).join("")}</div>`);
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
  function resetView() { S.setup = null; S.intake = null; S.evict = null; S.tasks = []; S.score = null; S.archive = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's five projects and every entry on them from your account?")) return;
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
    else if (k === "evict") S.evict = d;
    else if (k === "task") S.tasks.push(d);
    else if (k === "scoreboard") S.score = d;
    else if (k === "archive") S.archive = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the five projects and every entry on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try a task of your own — “Check” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "evict", "task", "scoreboard", "archive",
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
    show(state.status === "running" ? S.view : S.archive ? "archive" : "connect");
    subscribe();
  })();
})();

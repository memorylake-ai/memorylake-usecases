/* Concurrent write memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Three ways to write", sub: "the same seat pool", view: "setup" },
    { n: 3, title: "Six agents at once", sub: "Monday 09:00", view: "race" },
    { n: 4, title: "What memory says", sub: "counts · fact trace", view: "memory" },
    { n: 5, title: "Conflict check", sub: "what MemoryLake flags", view: "conflicts" },
    { n: 6, title: "Score", sub: "seats in use, by memory", view: "score" },
  ];
  const ARMS = ["A", "B", "C"];
  const fresh = () => ({ agents: { A: [], B: [], C: [] }, raced: null, counts: null, watch: [], conflicts: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const truth = () => (D().in_use_before || 12) + (D().hires || []).length;
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const hhmmss = (iso) => (iso || "").slice(11, 23);

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, race: raceView, memory, conflicts: conflictView, score }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? ARMS.map((a) => `<span class="mono">${a} ${esc(S.world[a])}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3½ minutes (the race takes seconds; then 150 s watching for conflicts) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-cwr")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Run concurrent agents on shared memory without race conditions</h1>
      <p class="lead">${esc(d.company || "Kesswick Labs")} (fictional) has ${esc(d.seats || 20)} seats on ${esc(d.tool || "Tamsor")}, a design tool; ${esc(d.in_use_before || 12)} are in use.
      On Monday at 09:00 six people start, and six onboarding agents each give their person a seat and record it in shared memory — at the same moment.
      The same race runs three ways. It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH (<code>fact trace</code>, <code>fact conflict</code>).</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/concurrent-agent-memory-access-without-race-conditions" target="_blank" rel="noopener">memorylake.ai › Concurrent agent memory access</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = D(), w = S.world;
    if (!d.arms) return empty("Three ways to write");
    return `<h1>The same seat pool, three ways to write it</h1>
      <p class="lead">Three projects with the same name (“${esc(d.project_name)}”), one per way of writing. In each, six agents write at the same instant.</p>
      <div class="grid2">${ARMS.map((a) => { const x = d.arms[a]; return `<div class="card"><h3 style="margin-top:0">${esc(x.title)}</h3>
        <p class="small">${esc(x.how)}</p>
        <p class="small muted" style="margin-bottom:4px">starts with</p><div class="memfact">${x.seed.length ? esc(x.seed[0]) : '<span class="muted">nothing — the pool size lives in the app</span>'}</div>
        <p class="small muted" style="margin-bottom:4px">each agent writes</p><div class="memfact mono">${esc(x.write)}</div>
        <p class="small muted" style="margin-bottom:0">project <span class="mono">${esc(w?.[a] || "pending")}</span></p></div>`; }).join("")}</div>`;
  }

  function raceView() {
    const d = D();
    if (!d.hires) return empty("Six agents at once");
    const any = ARMS.some((a) => S.agents[a].length);
    if (!any && !S.raced) return running() ? waiting("Six agents at once") : empty("Six agents at once");
    const cell = (a, h) => { const r = S.agents[a].find((x) => x.agent === h.agent);
      if (!r) return '<td class="muted">·</td>';
      return a === "A" ? `<td>read <b>${r.read}</b> → wrote <b>${r.wrote}</b></td>` : `<td>added a record</td>`; };
    return `<h1>Monday 09:00 — six onboarding agents write at the same moment</h1>
      <p class="lead">Eighteen agent jobs, six per way of writing, released together. Every command is real and every write is accepted: no command fails.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>agent</th><th>new hire</th>${ARMS.map((a) => `<th>${esc(D().arms[a].short)}</th>`).join("")}</tr></thead><tbody>
        ${d.hires.map((h) => `<tr><td class="mono">${esc(h.agent)}</td><td>${esc(h.name)}</td>${ARMS.map((a) => cell(a, h)).join("")}</tr>`).join("")}
      </tbody></table>
      ${S.agents.A.length === d.hires.length ? `<p class="small" style="margin-bottom:0">A: all six read <b>${[...new Set(S.agents.A.map((r) => r.read))].join(", ")}</b> and wrote <b>${[...new Set(S.agents.A.map((r) => r.wrote))].join(", ")}</b>.</p>` : ""}</div>`;
  }

  function memory() {
    const c = S.counts, d = D();
    if (!c) return running() ? waiting("What memory says now") : empty("What memory says now");
    const t = c.truth, k = c.counts;
    const big = (a, n, extra) => `<div class="count"><b class="${n === t ? "" : "bad-ink"}">${n} of ${d.seats}</b><span>${esc(d.arms[a].short)}${extra || ""}</span></div>`;
    return `<h1>A says ${k.A.in_use} of ${d.seats} in use — six people got a seat, the truth is ${t}</h1>
      <p class="lead">${d.in_use_before} seats were in use before; six agents each assigned one. B and C count their records; A reads its counter.</p>
      <div class="card"><div class="counts">${big("A", k.A.in_use, ` · last assigned: ${esc(k.A.holders[0])}`)}${big("B", k.B.in_use, ` · ${k.B.records} records`)}${big("C", k.C.in_use, ` · ${k.C.records} records`)}</div></div>
      <div class="card"><h3 style="margin-top:0"><code>fact trace</code> on A's counter — ${c.trace.length} updates, oldest first</h3>
        <table class="lint plain"><thead><tr><th>time (UTC)</th><th>update</th><th>written by</th><th>replaced</th></tr></thead><tbody>
        ${c.trace.map((r) => `<tr class="${r.overwrote ? "lost" : ""}"><td class="mono">${esc(hhmmss(r.at))}</td><td class="num">${r.from} → ${r.to}</td><td>${esc(r.by)}</td>
          <td>${r.overwrote ? `<span class="bad-ink"><b>overwrote ${esc(r.overwrote)}'s seat</b></span>` : '<span class="muted">what its agent read</span>'}</td></tr>`).join("")}
        </tbody></table>
        <p class="small" style="margin-bottom:0"><b>${c.lost} of ${c.trace.length}</b> updates replaced a text their agent never read. Every command succeeded — <code>fact update</code> has no version or if-match check. The trace shows it afterwards; nothing stopped it.</p></div>`;
  }

  function conflictView() {
    const d = D();
    if (!S.watch.length && !S.conflicts) return running() ? waiting("Conflict check") : empty("Conflict check");
    const last = S.watch[S.watch.length - 1];
    const cf = S.conflicts;
    const raised = (a) => cf ? cf.raised[a] : last?.found?.[a] ?? 0;
    return `<h1>Conflict detection — ${cf ? ARMS.map((a) => `${a} ${cf.raised[a]}`).join(" · ") : "watching…"}</h1>
      <p class="lead">MemoryLake checks new facts for contradictions. The demo watches all three projects for 150 s after the last write.
      ${last ? `<span class="pill ${cf ? "ok" : "busy"}">${cf ? "" : '<span class="spinner"></span>'} ${last.waited}s after the last write</span>` : ""}</p>
      <div class="grid2">${ARMS.map((a) => { const cs = cf?.conflicts?.[a] || []; const rs = cf?.resolved?.[a] || [];
        return `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].short)}</h3>
          <div class="counts"><div class="count"><b class="${raised(a) ? "warn-ink" : ""}">${raised(a)}</b><span>conflicts</span></div></div>
          ${cs.map((c) => `<p class="small"><b>${esc(c.category)}</b> · “${esc(c.name)}” — ${c.facts} facts</p><p class="small quote">${esc(c.description)}</p>`).join("")}
          ${rs.map((r) => `<p class="small bad-ink">Resolved the latest-source way (keep the newest, forget the rest): <b>${r.forgotten} real assignment${r.forgotten === 1 ? "" : "s"} forgotten</b>${r.names.length ? ` — ${esc(r.names.join(", "))}` : ""}.</p>`).join("")}
          ${a === "A" && cf ? `<p class="small muted" style="margin-bottom:0">${raised("A") ? "" : "Nothing to flag: the lost updates all agree (“13 of 20”), and <code>fact update</code> does not run detection."}</p>` : ""}
          ${a !== "A" && cf && !cs.length ? `<p class="small muted" style="margin-bottom:0">No conflict this run.</p>` : ""}</div>`; }).join("")}</div>`;
  }

  function score() {
    const sc = S.score, d = D();
    if (!sc) return running() ? waiting("Score") : empty("Score");
    return `<h1>Seats in use: ${sc.rows.map((r) => `${r.arm} ${r.says}`).join(" · ")} — truth ${sc.truth}</h1>
      <p class="lead">${sc.rows.map((r) => r.arm === "A" ? `A lost ${r.lost} of ${d.hires.length} writes with no error${r.conflicts ? "" : " and no conflict"}.`
        : r.conflicts && r.lost ? `${r.arm} kept every record, was flagged as a contradiction, and resolving it the latest-source way forgot ${r.lost}.`
        : r.conflicts ? `${r.arm} was flagged as a contradiction; nothing forgotten.` : `${r.arm} kept all ${d.hires.length} records and raised no conflict this run.`).join(" ")}
        One run varies; the README adds up several.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th class="num">says</th><th>right?</th><th class="num">conflicts</th><th class="num">writes lost</th><th>holders it names from today</th></tr></thead><tbody>
        ${sc.rows.map((r) => `<tr><td><b>${esc(r.title)}</b></td><td class="num"><b class="${r.right ? "" : "bad-ink"}">${r.says}</b></td>
          <td>${r.right ? '<span class="mark ok">✓</span>' : '<span class="mark bad">✗</span>'}</td><td class="num">${r.conflicts}</td><td class="num">${r.lost}</td>
          <td class="small">${esc(r.holders.join(", "))}</td></tr>`).join("")}</tbody></table></div>`;
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
    if (!confirm("Delete the demo's three projects and their facts?")) return;
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
    else if (k === "progress") {
      term("progress", ev.text.trim());
      if (d.arm) S.agents[d.arm].push(d);
      if (d.found) S.watch.push(d);
      if (S.caughtUp && (S.view === "race" || S.view === "conflicts")) renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "race") S.raced = d;
    else if (k === "counts") S.counts = d;
    else if (k === "conflicts") S.conflicts = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the three projects and their facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The Score step has the three readings side by side.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "race", "counts", "conflicts", "score", "done", "error"];
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

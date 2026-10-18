/* DR history memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "The memory", sub: "seven facts, one project", view: "setup" },
    { n: 3, title: "A quarter of changes", sub: "six fact updates", view: "changes" },
    { n: 4, title: "The nightly backup", sub: "02:00 — three formats", view: "backup" },
    { n: 5, title: "The project is deleted", sub: "03:40 — history 404", view: "incident" },
    { n: 6, title: "Restore", sub: "04:05 — three new projects", view: "restore" },
    { n: 7, title: "The DR drill", sub: "current values", view: "drill" },
    { n: 8, title: "The incident review", sub: "before · how often · when", view: "review" },
  ];
  const ARMS = ["A", "B", "C"];
  const fresh = () => ({ setup: null, changes: [], changesDone: null, backup: null, incident: null, restore: { A: [], B: [], C: [] }, restored: null, drill: null, history: [], score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const hhmmss = (iso) => (iso || "").slice(11, 19);
  const label = (k) => (D().labels || {})[k] || k;
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const chain = (vals) => (vals && vals.length ? vals.map(esc).join(" → ") : '<span class="muted">(nothing)</span>');
  const delta = (a, b) => { const s = (Date.parse(b) - Date.parse(a)) / 1000; const v = Math.abs(s); return (s >= 0 ? "+" : "−") + (v >= 60 ? `${Math.floor(v / 60)}m${String(Math.floor(v % 60)).padStart(2, "0")}s` : `${v.toFixed(1)}s`); };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup: setupView, changes: changesView, backup: backupView, incident: incidentView, restore: restoreView, drill: drillView, review: reviewView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const w = S.world || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Restored copies</dt><dd>${w.A ? ARMS.map((a) => `<span class="mono">${a} ${esc(w[a])}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${w.A ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 75 seconds · everything it creates is prefixed <code>${esc(d.prefix || "mlu-drh")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Disaster recovery that keeps the history, not just the values</h1>
      <p class="lead">${esc(d.company || "Harrowgate Freight")} (fictional) runs a dispatch agent on project memory. Over a quarter the team changes four of its seven facts six times.
      At 03:40 a teardown script meant for staging deletes the production memory project — facts and <code>fact trace</code> history with it. At 04:05 the nightly backup is restored into a new project, three ways.
      All three pass the DR drill. Only one can still say what changed, and when. It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH (<code>fact trace</code> with full history).</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/disaster-recovery-for-agent-memory-infrastructure" target="_blank" rel="noopener">memorylake.ai › Disaster recovery for agent memory infrastructure</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const d = D(), s = S.setup;
    if (!s) return running() ? waiting("The memory") : empty("The memory");
    return `<h1>The dispatch agent's memory: seven facts</h1>
      <p class="lead">One project, “${esc(d.project_name)}” (<span class="mono">${esc(s.primary)}</span>), seven facts added with one <code>fact add</code>.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>fact</th><th>says</th><th>fact id</th></tr></thead><tbody>
        ${s.facts.map((f) => `<tr><td><b>${esc(f.label)}</b></td><td>${esc(f.value)}</td><td class="mono small">${esc(f.id)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function changesView() {
    const c = S.changes, t = S.changesDone;
    if (!c.length) return running() ? waiting("A quarter of changes") : empty("A quarter of changes");
    return `<h1>July → October: six changes to four facts</h1>
      <p class="lead">A quarter compressed into seconds: each change is a real <code>fact update</code>, recorded on the server's clock. The weekend on-call changes and changes back.</p>
      <div class="grid2"><div class="card">${c.map((p) => `<div class="memfact"><span class="mono">${esc(p.when)}</span><span><b>${esc(p.label)}</b>: ${esc(p.was)} → <b>${esc(p.value)}</b><br><span class="small muted">${esc(p.why)}</span></span></div>`).join("")}</div>
      ${t ? `<div class="card"><h3 style="margin-top:0"><code>fact trace</code> on ${esc(t.label)}</h3><table class="lint plain"><thead><tr><th>server time</th><th>event</th><th>value</th></tr></thead><tbody>
        ${t.trace.map((e) => `<tr><td class="mono">${esc(hhmmss(e.at))}</td><td>${esc(e.event)}</td><td>${esc(e.value)}</td></tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">Every version, with the time it was written. This is the history the incident review will need.</p></div>` : ""}</div>`;
  }

  function backupView() {
    const d = D(), b = S.backup;
    if (!b) return running() ? waiting("The nightly backup") : empty("The nightly backup");
    return `<h1>02:00 — the nightly backup, in three formats</h1>
      <p class="lead">${b.facts} facts, ${b.versions} versions in their history. Each restore gets its own format of the same backup.</p>
      <div class="grid2">${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].title)}</h3>
        <p class="small"><code>out/backup-${a.toLowerCase()}.json</code> · ${b.sizes[a]} bytes</p><p class="small" style="margin-bottom:0">${code(d.arms[a].backup)}</p></div>`).join("")}</div>`;
  }

  function incidentView() {
    const d = D(), i = S.incident;
    if (!i) return running() ? waiting("The project is deleted") : empty("The project is deleted");
    return `<h1>03:40 — the memory project is deleted</h1>
      <p class="lead">${esc(d.incident?.what || "")}. <code>proj delete</code> takes the facts and their <code>fact trace</code> history with it.</p>
      <div class="card"><pre class="mono small" style="margin:0;white-space:pre-wrap">$ memorylake proj delete ${esc(i.primary)}
$ memorylake fact trace … --project ${esc(i.primary)}
<span class="bad-ink">→ Project not found with id: ${esc(i.primary)} [NOT_FOUND]</span></pre>
      <p class="small" style="margin-bottom:0">The last write the project ever had was at <b class="mono">${esc(hhmmss(i.last_write))}</b> server time. From here on, the backup is all that is left.</p></div>`;
  }

  function restoreView() {
    const d = D(), r = S.restore;
    if (!r.A.length) return running() ? waiting("Restore") : empty("Restore");
    const w = S.world || {};
    return `<h1>04:05 — restore into a new project, three ways</h1>
      <div class="grid2">${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].title)}</h3><p class="small">${code(d.arms[a].how)}</p>
        ${r[a].map((x) => `<p class="small" style="margin:4px 0"><b>${x.versions ? `${x.versions} versions → ${x.facts} facts` : `${x.facts} facts`}</b> in ${x.calls} call${x.calls === 1 ? "" : "s"}</p>`).join("") || '<p class="small muted">…</p>'}
        <p class="small muted" style="margin-bottom:0">project <span class="mono">${esc(w[a] || "pending")}</span></p></div>`).join("")}</div>`;
  }

  function drillView() {
    const d = D(), dr = S.drill;
    if (!dr) return running() ? waiting("The DR drill") : empty("The DR drill");
    const all = ARMS.every((a) => dr.right[a] === dr.of);
    return `<h1>${all ? `The DR drill passes for all three: ${ARMS.map((a) => `${a} ${dr.right[a]}/${dr.of}`).join(" · ")}` : "The DR drill"}</h1>
      <p class="lead">Does each restored copy say what production said? This is the check most DR runbooks stop at.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>fact</th><th>should say</th>${ARMS.map((a) => `<th>${esc(d.arms[a].short)}</th>`).join("")}</tr></thead><tbody>
        ${d.facts.map((f) => `<tr><td><b>${esc(f.label)}</b></td><td>${esc(dr.now[f.key])}</td>${ARMS.map((a) => { const v = dr.mem[a][f.key] || []; const ok = v.length === 1 && v[0] === dr.now[f.key];
          return `<td class="${ok ? "" : "bad-ink"}">${v.map(esc).join(" + ") || "(nothing)"} <span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span></td>`; }).join("")}</tr>`).join("")}
      </tbody></table></div>`;
  }

  function reviewView() {
    const d = D(), h = S.history, sc = S.score;
    if (!h.length) return running() ? waiting("The incident review") : empty("The incident review");
    const mark = (ok) => `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;
    const blocks = h.map((x) => `<div class="card"><h3 style="margin-top:0">${esc(x.label)}</h3>
      <p class="small">production: <b>${chain(x.truth.versions)}</b> · last changed <b class="mono">${esc(hhmmss(x.truth.at))}</b></p>
      <table class="lint plain"><thead><tr><th>copy</th><th>versions in its trace</th><th>value before now</th><th>last changed</th></tr></thead><tbody>
      ${ARMS.filter((a) => x.arms[a]).map((a) => { const r = x.arms[a];
        return `<tr><td><b>${esc(d.arms[a].short)}</b></td><td>${chain(r.versions)}</td><td class="${r.ok_before ? "" : "bad-ink"}">${r.before ? esc(r.before) : "(none — never changed)"} ${mark(r.ok_before)}</td>
          <td class="${r.ok_when ? "" : "bad-ink"}"><span class="mono">${esc(hhmmss(r.at))}</span> <span class="small muted">(${esc(r.source)})</span> ${r.ok_when ? "" : `<span class="small">${esc(delta(x.truth.at, r.at))}</span>`} ${mark(r.ok_when)}</td></tr>`; }).join("")}
      </tbody></table></div>`).join("");
    const table = sc ? `<div class="card"><h3 style="margin-top:0">Score</h3><table class="lint plain"><thead><tr><th>copy</th><th class="num">current values</th><th class="num">value before now</th><th class="num">version count</th><th class="num">when it last changed</th></tr></thead><tbody>
      ${ARMS.map((a) => { const r = sc.rows[a]; const cell = (v, of) => `<b class="${v === of ? "" : "bad-ink"}">${v}/${of}</b>`;
        return `<tr><td><b>${esc(d.arms[a].title)}</b></td><td class="num">${cell(sc.current[a], sc.facts)}</td><td class="num">${cell(r.before, sc.of)}</td><td class="num">${cell(r.count, sc.of)}</td><td class="num">${cell(r.when, sc.of)}</td></tr>`; }).join("")}
      </tbody></table>
      <p class="small" style="margin-bottom:0">A restored values only: every fact has one version, dated at the restore. B replayed every version, but each one is dated at the restore — ${sc.rows.B.after_last}/${sc.of} last changes come after the last write production ever had (${esc(hhmmss(sc.last_write))}). C kept the original id and times in metadata: ${sc.rows.C.when}/${sc.of}.</p></div>` : "";
    return `<h1>${sc ? `What changed, and when: ${ARMS.map((a) => `${a} ${sc.rows[a].when}/${sc.of}`).join(" · ")}` : "The incident review"}</h1>
      <p class="lead">The questions an incident review asks of the restored memory: what was it before, how many times did it change, and when — answered from each copy's own <code>fact trace</code> (and C's metadata).</p>
      ${table}${blocks}`;
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
    if (!confirm("Delete the demo's memory projects and their facts?")) return;
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
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) { resetView(); S.world = null; } term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") {
      term("progress", ev.text.trim());
      if (d.phase === "change") S.changes.push(d);
      if (d.arm) S.restore[d.arm].push(d);
      if (S.caughtUp && ["changes", "restore"].includes(S.view)) renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = { ...(S.world || {}), ...d };
    else if (k === "setup") S.setup = d;
    else if (k === "changes") S.changesDone = d;
    else if (k === "backup") S.backup = d;
    else if (k === "incident") S.incident = d;
    else if (k === "restored") S.restored = d;
    else if (k === "drill") S.drill = d;
    else if (k === "history") S.history.push(d);
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's projects and their facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The incident review has the three restores side by side.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "changes", "backup", "incident", "restored", "drill", "history", "score", "done", "error"];
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
    show(state.status === "running" ? S.view : S.score ? "review" : "connect");
    subscribe();
  })();
})();

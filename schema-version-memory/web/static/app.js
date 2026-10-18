/* Schema version memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "v1 writes 8 accounts", sub: "text + metadata {plan}", view: "setup" },
    { n: 3, title: "Two upgrades", sub: "written by v1", view: "changes" },
    { n: 4, title: "v2 ships", sub: "plan → tier, three ways", view: "migrate" },
    { n: 5, title: "New accounts", sub: "written by v2", view: "signups" },
    { n: 6, title: "Four readers", sub: "who gets priority support?", view: "readers" },
    { n: 7, title: "The audit", sub: "when · from what", view: "audit" },
  ];
  const ARMS = ["A", "B", "C"];
  const READERS = ["R1", "R2", "R3", "R4"];
  const fresh = () => ({ setup: null, changes: [], changesDone: null, migrate: [], migrated: null, signups: null, readers: null, audit: {}, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const hhmmss = (iso) => (iso || "").slice(11, 19);
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const mark = (ok) => `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;
  const nm = (k) => (D().names || {})[k] || k;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup: setupView, changes: changesView, migrate: migrateView, signups: signupsView, readers: readersView, audit: auditView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const w = S.world || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Projects</dt><dd>${w.A ? ARMS.map((a) => `<span class="mono">${a} ${esc(w[a])}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${w.A ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 60 seconds · everything it creates is prefixed <code>${esc(d.prefix || "mlu-svm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Change a memory schema without breaking the readers — or the audit</h1>
      <p class="lead">${esc(d.company || "Tindlewood Analytics")} (fictional) runs an account assistant on project memory: one record per customer account, a text people read and metadata code reads.
      Today v2 renames <code>plan</code> to <code>tier</code>. The same v1 history sits in three projects and v2 ships three ways. Then four readers — two in code, two as text searches, one of each still on v1 — answer the same question,
      and an audit asks each migrated record when it changed shape and from what. It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH (<code>fact trace</code> with full history).</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/schema-evolution-for-versioned-agent-memory" target="_blank" rel="noopener">memorylake.ai › Schema evolution for versioned agent memory</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const s = S.setup;
    if (!s) return running() ? waiting("v1 writes 8 accounts") : empty("v1 writes 8 accounts");
    return `<h1>Spring: the v1 app writes 8 accounts</h1>
      <p class="lead">Each account is one fact: a text, then <code>fact update --metadata</code> with the fields code reads. The same history goes into all three projects.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>account</th><th>text</th><th>metadata (v1)</th></tr></thead><tbody>
        ${s.accounts.map((a) => `<tr><td><b>${esc(a.name)}</b></td><td>${esc(a.text)}</td><td class="mono small">${esc(JSON.stringify(a.meta))}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function changesView() {
    const c = S.changes, t = S.changesDone;
    if (!c.length) return running() ? waiting("Two upgrades") : empty("Two upgrades");
    return `<h1>June → August: two upgrades, written by v1</h1>
      <p class="lead">Text and metadata change in one <code>fact update</code>. The trace records the text versions on the server's clock.</p>
      <div class="grid2"><div class="card">${c.map((p) => `<div class="memfact"><span class="mono">${esc(p.when)}</span><span><b>${esc(p.name)}</b>: ${esc(p.was)} → <b>${esc(p.value)}</b><br><span class="small muted">${esc(p.why)}</span></span></div>`).join("")}</div>
      ${t ? `<div class="card"><h3 style="margin-top:0"><code>fact trace</code> on ${esc(t.name)}</h3><table class="lint plain"><thead><tr><th>server time</th><th>event</th><th>text</th></tr></thead><tbody>
        ${t.trace.map((e) => `<tr><td class="mono">${esc(hhmmss(e.at))}</td><td>${esc(e.event)}</td><td>${esc(e.text)}</td></tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">No entry for the metadata writes: a <code>fact update</code> that changes only metadata does not appear in the trace.</p></div>` : ""}</div>`;
  }

  function migrateView() {
    const d = D(), m = S.migrate;
    if (!m.length) return running() ? waiting("v2 ships") : empty("v2 ships");
    return `<h1>Today: v2 ships — <code>plan</code> becomes <code>tier</code>, three ways</h1>
      <p class="lead">v2 code reads <code>tier</code> and <code>billing</code>. A billing export job is still on v1 for the rest of the rolling deploy, and still reads <code>plan</code>.</p>
      <div class="grid2">${ARMS.map((a) => { const x = m.find((y) => y.arm === a); return `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].title)}</h3><p class="small">${code(d.arms[a].how)}</p>
        <p class="small" style="margin-bottom:0"><b>${x ? `${x.calls} migration call${x.calls === 1 ? "" : "s"}` : "…"}</b></p></div>`; }).join("")}</div>`;
  }

  function signupsView() {
    const s = S.signups;
    if (!s) return running() ? waiting("New accounts") : empty("New accounts");
    return `<h1>After the release: v2 signs 4 new accounts</h1>
      <p class="lead">A and B write them in the v2 shape only. C writes both keys, and the v1 phrase in the text, until every reader is on v2.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>account</th><th>tier</th><th class="num">seats</th><th>billing</th></tr></thead><tbody>
        ${s.accounts.map((a) => `<tr><td><b>${esc(a.name)}</b></td><td>${esc(a.tier)}</td><td class="num">${a.seats}</td><td>${esc(a.billing)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function readersView() {
    const d = D(), r = S.readers;
    if (!r) return running() ? waiting("Four readers") : empty("Four readers");
    const full = (a) => READERS.filter((k) => r.rows[a][k].found === r.rows[a][k].of && !r.rows[a][k].wrong.length).length;
    return `<h1>Who gets priority support? Readers that get it right: ${ARMS.map((a) => `${a} ${full(a)}/4`).join(" · ")}</h1>
      <p class="lead">The answer is ${r.truth.length} accounts: ${r.truth.map((k) => esc(nm(k))).join(", ")}. Each reader asks the same project memory its own way.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>reader</th><th>reads</th>${ARMS.map((a) => `<th>${esc(d.arms[a].short)}</th>`).join("")}</tr></thead><tbody>
        ${READERS.map((k) => { const rd = d.readers[k]; return `<tr><td><b>${k} ${esc(rd.name)}</b> <span class="small muted">(${esc(rd.code)})</span></td><td class="small">${code(rd.reads)}</td>
          ${ARMS.map((a) => { const x = r.rows[a][k]; const ok = x.found === x.of && !x.wrong.length;
            return `<td class="${ok ? "" : "bad-ink"}"><b>${x.found}/${x.of}</b>${x.unreadable ? ` <span class="small">· ${x.unreadable} unreadable</span>` : ""} ${mark(ok)}
              ${x.missed.length ? `<br><span class="small">missed ${x.missed.map((m) => esc(nm(m))).join(", ")}</span>` : ""}</td>`; }).join("")}</tr>`; }).join("")}
      </tbody></table>
      <p class="small" style="margin-bottom:0">A record a code reader cannot read (its key is missing) is counted as <i>unreadable</i>; the text readers just do not find it.</p></div>`;
  }

  function auditView() {
    const d = D(), au = S.audit, sc = S.score;
    if (!au.B) return running() ? waiting("The audit") : empty("The audit");
    const block = (a) => { const x = au[a]; if (!x) return ""; return `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].title)}</h3>
      <table class="lint plain"><thead><tr><th>account</th><th>last changed</th><th>newest trace entry</th><th>migration in trace</th><th>v1 fields read back</th></tr></thead><tbody>
      ${Object.values(x.detail).map((r) => `<tr><td><b>${esc(r.name)}</b></td><td class="mono">${esc(hhmmss(r.updated_at))}</td><td class="mono">${esc(r.newest.event)} ${esc(hhmmss(r.newest.at))}</td>
        <td class="${r.ok_trace ? "" : "bad-ink"}">${r.ok_trace ? `<span class="mono">${esc(hhmmss(r.migration_at))}</span>` : "none"} ${mark(r.ok_trace)}</td>
        <td class="${r.ok_fields ? "" : "bad-ink"}">${r.ok_fields ? `plan ${esc(r.recovered.plan)}, ${r.recovered.seats} seats` : "— (<code>plan</code> is gone)"} ${mark(r.ok_fields)}</td></tr>`).join("")}
      </tbody></table></div>`; };
    const table = sc ? `<div class="card"><h3 style="margin-top:0">Score</h3><table class="lint plain"><thead><tr><th>release</th>${READERS.map((k) => `<th class="num">${k} ${esc(d.readers[k].name)}</th>`).join("")}<th class="num">migration in trace</th><th class="num">v1 fields kept</th></tr></thead><tbody>
      ${ARMS.map((a) => { const cell = (v, of) => `<b class="${v === of ? "" : "bad-ink"}">${v}/${of}</b>`; const x = sc.readers[a], u = sc.audit[a];
        return `<tr><td><b>${esc(d.arms[a].title)}</b></td>${READERS.map((k) => `<td class="num">${cell(x[k].found, x[k].of)}</td>`).join("")}
          <td class="num">${u ? cell(u.trace, u.of) : '<span class="muted">n/a</span>'}</td><td class="num">${u ? cell(u.fields, u.of) : '<span class="muted">n/a</span>'}</td></tr>`; }).join("")}
      </tbody></table>
      <p class="small" style="margin-bottom:0">B changed ${sc.audit.B.moved}/${sc.audit.B.of} records (their <code>updated_at</code> moved), and <code>fact trace</code> shows ${sc.audit.B.trace} of those changes. C carries each change in its trace (${sc.audit.C.trace}/${sc.audit.C.of}) and the v1 fields in metadata (${sc.audit.C.fields}/${sc.audit.C.of}). A changed no v1 record, so there is nothing to audit.</p></div>` : "";
    return `<h1>${sc ? `The audit — migration in trace: ${["B", "C"].map((a) => `${a} ${sc.audit[a].trace}/${sc.audit[a].of}`).join(" · ")}` : "The audit"}</h1>
      <p class="lead">For each record the release migrated: does its <code>fact trace</code> show the schema change, and can its v1 fields still be read back?</p>
      ${table}${block("B")}${block("C")}`;
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
      if (d.arm) S.migrate.push(d);
      if (S.caughtUp && ["changes", "migrate"].includes(S.view)) renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = { ...(S.world || {}), ...d };
    else if (k === "setup") S.setup = d;
    else if (k === "changes") S.changesDone = d;
    else if (k === "migrated") S.migrated = d;
    else if (k === "signups") S.signups = d;
    else if (k === "readers") S.readers = d;
    else if (k === "audit") S.audit[d.arm] = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's projects and their facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The readers and the audit have the three releases side by side.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "changes", "migrated", "signups", "readers", "audit", "score", "done", "error"];
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
    show(state.status === "running" ? S.view : S.score ? "audit" : "connect");
    subscribe();
  })();
})();

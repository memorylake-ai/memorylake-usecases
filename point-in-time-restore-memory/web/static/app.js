/* Point-in-time restore memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Memory and its backup", sub: "02:00 — three copies", view: "backup" },
    { n: 3, title: "The day", sub: "09:10–11:40 · journaled", view: "day" },
    { n: 4, title: "The incident", sub: "14:52 — a bad agent run", view: "incident" },
    { n: 5, title: "Restore", sub: "three ways", view: "restore" },
    { n: 6, title: "What memory says", sub: "copies · fact trace", view: "memory" },
    { n: 7, title: "Score", sub: "which restore got 14:51 back", view: "score" },
  ];
  const ARMS = ["A", "B", "C"];
  const fresh = () => ({ backup: null, day: [], dayDone: null, inc: [], incident: null, restore: { A: [], B: [], C: [] }, restored: null, memory: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const hhmmss = (iso) => (iso || "").slice(11, 19);
  const label = (k) => (D().labels || {})[k] || k;
  const says = (vals) => (vals && vals.length ? vals.map(esc).join(" + ") : '<span class="muted">(nothing)</span>');
  const cellOk = (k, vals) => { const t = D().expected || {}; return k in t ? vals.length === 1 && vals[0] === t[k] : !vals.length; };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, backup: backupView, day: dayView, incident: incidentView, restore: restoreView, memory, score }[S.view];
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
          <span class="muted small">about 2 minutes · everything it creates is prefixed <code>${esc(d.prefix || "mlu-pit")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Back up and restore agent memory with point-in-time recovery</h1>
      <p class="lead">${esc(d.company || "Quellbrook")} (fictional) runs a support agent on project memory, backed up every night at 02:00.
      During the day the team changes three facts, adds two, and forgets a customer's phone number on an erasure request. At 14:52 an agent run follows instructions planted in a customer email and rewrites its own memory.
      At 15:20 the same memory — three identical copies — is restored three ways. It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH (<code>fact trace</code> on forgotten facts).</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-backup-and-restore-for-agent-systems" target="_blank" rel="noopener">memorylake.ai › Memory backup and restore for agent systems</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function backupView() {
    const d = D(), w = S.world;
    if (!d.arms) return empty("Memory and its backup");
    const b = S.backup?.facts;
    return `<h1>One agent memory, three copies — backed up at 02:00</h1>
      <p class="lead">Three projects with the same name (“${esc(d.project_name)}”) and the same eight facts, one per way of restoring. The nightly job exports every live fact with <code>fact list</code> — text and fact id — to <code>out/backup-&lt;copy&gt;.json</code>.</p>
      <div class="grid2">${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].title)}</h3><p class="small">${esc(d.arms[a].how.replace(/`/g, ""))}</p>
        <p class="small muted" style="margin-bottom:0">project <span class="mono">${esc(w?.[a] || "pending")}</span></p></div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">The 02:00 backup</h3><table class="lint plain"><thead><tr><th>fact</th><th>says</th><th>text</th><th>fact id (copy C)</th></tr></thead><tbody>
        ${d.facts.map((f) => { const x = b?.find((y) => y.key === f.key);
          return `<tr><td><b>${esc(f.label)}</b>${f.pii ? ' <span class="mark no">PII</span>' : ""}</td><td>${esc(f.value)}</td><td class="small">${esc(f.text)}</td><td class="mono small">${x ? esc(x.id.C) : "·"}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function dayView() {
    const d = D();
    if (!S.day.length) return running() ? waiting("The day") : empty("The day");
    return `<h1>09:10–11:40 — the day's changes, each one journaled</h1>
      <p class="lead">Every write goes to memory and to the agent's own write journal (<code>out/journal.json</code>) with the fact id it touched or created. The 10:05 deletion is an erasure request: that phone number must not come back.</p>
      <div class="card">${S.day.map((p) => `<div class="memfact"><span class="mono">${esc(p.at)}</span><span><b>${esc(label(p.key))}</b>: ${p.op === "update" ? `${esc(p.was)} → <b>${esc(p.value)}</b>` : p.op === "forget" ? '<span class="bad-ink"><b>forgotten</b></span>' : `new — <b>${esc(p.value)}</b>`}<br><span class="small muted">${esc(p.why)}</span></span></div>`).join("")}
      ${S.dayDone ? `<p class="small" style="margin-bottom:0">Restore point: <b class="mono">${esc(hhmmss(S.dayDone.restore_point))}</b> server time — the newest <code>fact trace</code> entry the day wrote, plus one second (story time 14:51). The journal has ${esc(S.dayDone.journal?.C)} entries per copy.</p>` : ""}</div>`;
  }

  function incidentView() {
    const d = D();
    if (!S.inc.length) return running() ? waiting("The incident") : empty("The incident");
    const t = d.expected || {}, now = S.incident?.now;
    return `<h1>14:52 — a bad agent run rewrites its own memory</h1>
      <p class="lead">${esc(d.incident?.what || "")}. Every write is a valid API call; nothing fails.</p>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">What the run wrote</h3>${S.inc.map((p) => `<div class="memfact"><span><b>${esc(label(p.key))}</b>: ${p.op === "update" ? `${esc(p.was)} → <b class="bad-ink">${esc(p.value)}</b>` : p.op === "forget" ? `<span class="bad-ink">forgotten</span> (${esc(p.was)})` : `new — <b class="bad-ink">${esc(p.value)}</b>`}</span></div>`).join("")}</div>
        ${now ? `<div class="card"><h3 style="margin-top:0">Before vs now (copy C)</h3><table class="lint plain"><thead><tr><th>fact</th><th>14:51</th><th>now</th></tr></thead><tbody>
          ${(d.rows || []).filter((k) => k in t || now[k]).map((k) => `<tr><td>${esc(label(k))}</td><td>${esc(t[k] || "(none)")}</td><td class="${cellOk(k, now[k] || []) ? "" : "bad-ink"}">${says(now[k])}</td></tr>`).join("")}
        </tbody></table></div>` : ""}</div>`;
  }

  function restoreView() {
    const d = D(), r = S.restore;
    if (!r.A.length && !r.B.length) return running() ? waiting("Restore") : empty("Restore");
    const rd = S.restored;
    const tbl = (a) => `<table class="lint plain"><thead><tr><th>fact</th><th>at the restore point</th><th>now</th><th>action</th></tr></thead><tbody>
      ${r[a].map((x) => `<tr><td>${esc(label(x.key))}</td><td>${esc(x.at_point)}</td><td>${esc(x.now)}</td><td>${x.action === "keep" || x.action === "stays forgotten" ? `<span class="muted">${esc(x.action)}</span>` : `<b>${esc(x.action.replace(/ as fact-.*/, " (new id)"))}</b>`}</td></tr>`).join("")}</tbody></table>`;
    return `<h1>15:20 — restore three ways</h1>
      <p class="lead">${ARMS.map((a) => `<b>${esc(d.arms[a].title)}</b>: ${esc(d.arms[a].how.replace(/`/g, ""))}`).join("<br>")}</p>
      <div class="card"><h3 style="margin-top:0">${esc(d.arms.A.title)}</h3>${r.A.map((x) => `<p class="small" style="margin:4px 0">${x.action === "wipe" ? `forgot all ${x.n} live facts` : `added the ${x.n} facts from <code>out/backup-a.json</code>`}</p>`).join("")}</div>
      ${["B", "C"].map((a) => `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].title)}</h3>
        ${rd ? `<p class="small">fact ids checked: <b>${rd[a].catalog}</b> — ${a === "B" ? `${rd.B.backup} in the backup + ${rd.B.listed} in <code>fact list</code>` : "every id in the write journal"}</p>` : ""}
        ${rd && rd[a].unseen.length ? rd[a].unseen.map((u) => `<p class="small bad-ink">not in the backup and not in <code>fact list</code>: <b>${esc(label(u.key))}</b> (added ${esc(u.at)}, forgotten in the incident) — never checked</p>`).join("") : ""}
        ${tbl(a)}</div>`).join("")}`;
  }

  function memory() {
    const m = S.memory, d = D();
    if (!m) return running() ? waiting("What memory says now") : empty("What memory says now");
    const t = m.expected, er = new Set(m.erased || []);
    const vals = (a, k) => (m.mem[a][k] || []).map((x) => x.value);
    const cell = (a, k) => { const v = vals(a, k), ok = cellOk(k, v);
      return `<td class="${ok ? "" : "bad-ink"}">${says(v)} ${ok ? '<span class="mark ok">✓</span>' : er.has(k) && v.length ? '<span class="mark bad">✗ resurrected</span>' : '<span class="mark bad">✗</span>'}</td>`; };
    const k0 = (m.erased || [])[0];
    return `<h1>${m.resurrected ? `A brought back ${esc(label(k0))} — forgotten at 10:05 on an erasure request` : "What each copy says now"}</h1>
      <p class="lead">Each copy should say what memory said at 14:51. A is back at last night, B lost one fact it never knew to look for, C has all of it.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>fact</th><th>should say</th>${ARMS.map((a) => `<th>${esc(d.arms[a].short)}</th>`).join("")}</tr></thead><tbody>
        ${(d.rows || []).map((k) => `<tr><td><b>${esc(label(k))}</b></td><td>${k in t ? esc(t[k]) : er.has(k) ? '<span class="muted">(deleted, ER-118)</span>' : '<span class="muted">(never)</span>'}</td>${ARMS.map((a) => cell(a, k)).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0"><code>fact trace</code> on the erased fact</h3>
        <p class="small">In C its id still has a history: <b>FORGET at ${esc(hhmmss(m.forget_at))}</b>, before the restore point ${esc(hhmmss(m.point))} — so the trace restore leaves it forgotten.
        ${m.resurrected ? "In A the backup put the same text back under a <b>new id</b> whose trace is a single ADD: nothing on it points to the deletion request." : ""}</p></div>`;
  }

  function score() {
    const sc = S.score, d = D();
    if (!sc) return running() ? waiting("Score") : empty("Score");
    const [a, b, c] = sc.rows;
    return `<h1>Facts right: ${sc.rows.map((r) => `${r.arm} ${r.right}/${r.of}`).join(" · ")}</h1>
      <p class="lead">A is back at last night: ${a.day - a.kept} of ${a.day} changes made today are gone, and an erased phone number is back. B missed ${b.lost.map(label).join(", ") || "nothing"} — added after the backup and forgotten in the incident, so neither the backup nor <code>fact list</code> had its id. C restored everything: the journal had every id, and <code>fact trace</code> reads forgotten facts too.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>copy</th><th class="num">facts right</th><th class="num">today's changes kept</th><th class="num">erasure honoured</th><th class="num">incident left</th></tr></thead><tbody>
        ${sc.rows.map((r) => `<tr><td><b>${esc(r.title)}</b></td><td class="num"><b class="${r.right === r.of ? "" : "bad-ink"}">${r.right}/${r.of}</b></td>
          <td class="num">${r.kept}/${r.day}</td><td class="num">${r.honoured ? "yes" : '<b class="bad-ink">no</b>'}</td><td class="num">${r.incident_left}</td></tr>`).join("")}</tbody></table></div>`;
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
    if (!confirm("Delete the demo's three memory copies and their facts?")) return;
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
      if (d.phase === "day") S.day.push(d);
      if (d.phase === "incident") S.inc.push(d);
      if (d.arm) S.restore[d.arm].push(d);
      if (S.caughtUp && ["day", "incident", "restore"].includes(S.view)) renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "backup") S.backup = d;
    else if (k === "day") S.dayDone = d;
    else if (k === "incident") S.incident = d;
    else if (k === "restored") S.restored = d;
    else if (k === "memory") S.memory = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the three memory copies and their facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The Score step has the three restores side by side.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "backup", "day", "incident", "restored", "memory", "score", "done", "error"];
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

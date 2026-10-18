/* Recurring failure memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Setup", sub: "run log (A) · failure records (B)", view: "setup" },
    { n: 3, title: "Six nights", sub: "one failure a night", view: "nights" },
    { n: 4, title: "Memory", sub: "what each one holds", view: "memory" },
    { n: 5, title: "Night 47", sub: "pre-call check", view: "precheck" },
    { n: 6, title: "Score", sub: "count · dates · escalation", view: "score" },
  ];
  const fresh = () => ({ setup: null, nights: [], memory: null, precheck: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const md = (d) => String(d || "").slice(5);
  const watchLabel = () => D().watch?.label || "export_invoices → HTTP 429";

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup: setupView, nights: nightsView, memory: memoryView, precheck: precheckView, score: scoreView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const w = S.world || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Project</dt><dd>${w.project || w.projects ? `<span class="mono">${esc(w.project || w.projects?.A)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${w.project || w.projects ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes (six nights, each waits for MemoryLake's extractor) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-rfm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The same tool failure, four times in a week. Memory keeps the lesson — does it keep the count?</h1>
      <p class="lead">${esc(d.company || "Copperleaf Billing")} (fictional) runs a nightly billing agent. Over six nights the Ledgerline API rate-limits <code>export_invoices</code> with HTTP 429 four times.
      Each failure is remembered two ways: <b>A</b> the night's run log goes to a conversation and MemoryLake's extractor turns it into memory;
      <b>B</b> the tool error handler writes one typed failure record (<code>fact add</code> + <code>fact update --metadata</code>).
      Before night 47 the agent checks memory and applies one rule: <i>${esc(d.rule?.text || "3 or more of the same failure in 7 days — open a systematic fix")}</i>.
      It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH.</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-agents-that-recover-from-tool-failures" target="_blank" rel="noopener">memorylake.ai › Memory for agents that recover from tool failures</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const s = S.setup, a = D().arms || {};
    if (!s) return running() ? waiting("Setup") : empty("Setup");
    return `<h1>Two memories for the same agent</h1>
      <div class="card"><table class="lint plain"><thead><tr><th></th><th>project</th><th>written by</th></tr></thead><tbody>
        <tr><td><b>${esc(a.A?.short)}</b></td><td>${esc(s.names.A)} <span class="mono muted small">${esc(s.projects.A)}</span></td><td>${code(a.A?.how)}</td></tr>
        <tr><td><b>${esc(a.B?.short)}</b></td><td>${esc(s.names.B)} <span class="mono muted small">${esc(s.projects.B)}</span></td><td>${code(a.B?.how)}</td></tr>
      </tbody></table></div>
      <p class="lead">The agent, <b>${esc(s.agent)}</b>, is an actor; its run log is a conversation on A's project (<span class="mono">${esc(s.conv)}</span>). B's project has no conversation: only the error handler writes to it.</p>`;
  }

  function nightsView() {
    const n = S.nights;
    if (!n.length) return running() ? waiting("Six nights") : empty("Six nights");
    const row = (t) => {
      const ex = t.extractor || {};
      const shape = ex.added || ex.rewrote ? `${ex.added} new · ${ex.rewrote} rewritten` : `<b class="bad-ink">no fact changed</b>`;
      const flag = (t.flag_b ? ' <span class="pill accent">B: escalate</span>' : "") + (t.flag_a ? ' <span class="pill">A: escalate</span>' : "");
      return `<tr class="${t.watched ? "" : "muted"}"><td class="mono"><b>${esc(t.run)}</b><br><span class="small">${esc(t.date)}</span></td>
        <td><span class="mono">${esc(t.tool)}</span> <span class="small muted">(${esc(t.api)})</span><br><span class="small">${esc(t.error)}${t.where ? ` at ${esc(t.where)}` : ""} · ${esc(t.recovery)}</span></td>
        <td class="small">${shape}<br><span class="muted">${esc(ex.secs)} s</span></td>
        <td class="mono">${t.watched ? `<b>${esc(t.happened)}</b>` : esc(t.happened)}</td><td class="mono ${t.a < t.happened ? "bad-ink" : ""}"><b>${esc(t.a)}</b></td><td class="mono"><b>${esc(t.b)}</b>${flag}</td></tr>`;
    };
    return `<h1>Six nights, one failure each</h1>
      <p class="lead">Each night: B's error handler writes a record (<code>fact add</code>), and the run log is appended to A's conversation (<code>conv msg append --timestamp … --wait</code>); the demo waits for the extractor before the next night.
      The last three columns count <b>${esc(watchLabel())}</b>: how often it has happened, how many times A's memory shows it (distinct dates written in the facts about it), how many records B has.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>night</th><th>failure · recovery</th><th>A: extractor</th><th>happened</th><th>A shows</th><th>B records</th></tr></thead><tbody>${n.map(row).join("")}</tbody></table></div>`;
  }

  function memoryView() {
    const m = S.memory;
    if (!m) return running() ? waiting("What each memory holds") : empty("What each memory holds");
    return `<h1>What each memory holds</h1>
      <div class="card"><h3 style="margin-top:0">A — the runs project: ${m.a.length} fact(s) from ${(D().runs || []).length} run logs</h3>
        <ul class="facts">${m.a.map((f) => `<li class="${f.about ? "" : "muted"}">${f.about ? "▸ " : ""}${esc(f.text)}</li>`).join("")}</ul></div>
      <div class="card"><h3 style="margin-top:0">B — the records project: ${m.b.length} record(s)</h3><table class="lint plain"><thead><tr><th>run</th><th>date</th><th>tool</th><th>error</th><th>recovery</th></tr></thead><tbody>
        ${m.b.map((r) => `<tr class="${r.tool === D().watch?.tool && String(r.error).startsWith("HTTP 429") ? "" : "muted"}"><td class="mono">${esc(r.run)}</td><td class="mono">${esc(r.date)}</td><td class="mono">${esc(r.tool)}</td><td>${esc(r.error)}</td><td class="small">${esc(r.recovery)}</td></tr>`).join("")}</tbody></table></div>
      <p class="muted small">▸ = about ${esc(watchLabel())}</p>`;
  }

  function precheckView() {
    const p = S.precheck, rule = D().rule || {};
    if (!p) return running() ? waiting("Night 47") : empty("Night 47");
    const a = p.A, b = p.B;
    return `<h1>Night 47: before calling export_invoices, the agent checks memory</h1>
      <p class="lead">Rule: <i>${esc(rule.text)}</i></p>
      <div class="card"><h3 style="margin-top:0">A run log — ${a.facts.length} fact(s) about it</h3>
        ${a.facts.map((f) => `<p class="small">${esc(f.text)}</p><p class="small muted"><code>fact trace</code>: ${f.history.slice().reverse().map((x) => `${esc(x.event)}·${esc(x.source)}${x.said ? ` (msg ${esc(md(x.said))})` : ""}`).join(" → ")}</p>`).join("")}
        <p>Dates written in those facts: <b>${esc(a.dates.map(md).join(", ") || "none")}</b> → <b class="${a.escalate ? "" : "bad-ink"}">${esc(a.count)} failure(s) on record — ${a.escalate ? "escalate" : `below ${esc(rule.threshold)}, retry with the remembered recovery`}</b></p>
        <p>Read through <code>fact trace</code>, each history entry dated by the message that caused it: <b>${esc(a.trace_count)} night(s)</b> (${esc(a.trace_dates.map(md).join(", ") || "none")}) — ${a.trace_escalate ? "escalate" : `below ${esc(rule.threshold)}`}</p></div>
      <div class="card"><h3 style="margin-top:0">B failure records — ${esc(b.count)} in the last ${esc(rule.window_days)} days</h3>
        <table class="lint plain"><thead><tr><th>run</th><th>date</th><th>recovery</th></tr></thead><tbody>${b.records.map((r) => `<tr><td class="mono">${esc(r.run)}</td><td class="mono">${esc(r.date)}</td><td class="small">${esc(r.recovery)}</td></tr>`).join("")}</tbody></table>
        <p><b>${esc(b.count)} failures → escalate</b>${b.first_flag ? `; the rule first held on night ${esc(b.first_flag.run)} (${esc(b.first_flag.date)})` : ""}. Last recovery that worked: ${esc(b.recovery)}</p></div>`;
  }

  function scoreView() {
    const sc = S.score;
    if (!sc) return running() ? waiting("Score") : empty("Score");
    const cols = ["A", "T", "B"], h = sc.heads || {};
    const sm = sc.summary;
    return `<h1>${esc(h.A)}: ${sm.A.right}/${sm.A.of} · ${esc(h.T)}: ${sm.T.right}/${sm.T.of} · ${esc(h.B)}: ${sm.B.right}/${sm.B.of}</h1>
      <p class="lead">Five things the pre-call check needs to know about <b>${esc(watchLabel())}</b>. “A + fact trace” is the most generous reading of the run-log memory: every history entry of the facts about it, dated by its source message.</p>
      <div class="card"><table class="lint plain"><thead><tr><th></th><th>truth</th>${cols.map((c) => `<th>${esc(h[c])}</th>`).join("")}</tr></thead><tbody>
      ${sc.rows.map((r) => `<tr><td>${esc(r.q)}</td><td><b>${esc(r.truth)}</b></td>${cols.map((c) => `<td class="${r["ok" + c] ? "" : "bad-ink"}">${r["ok" + c] ? "✓" : "✗"} <b>${esc(r[c])}</b></td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="card"><ul class="steps-list">
        <li><b>A</b>: ${esc(sc.expected.count)} failures left ${esc(sc.a_facts)} fact(s) about it and ${esc(sc.a_history)} history entr${sc.a_history === 1 ? "y" : "ies"}.${sc.a_facts < sc.expected.count ? " The extractor filed at least one repeat as the knowledge it already had — “the call can fail with HTTP 429 …” — not as another event." : " This time the extractor kept a fact per failure."}
          ${sc.silent_nights.length ? `On night(s) ${esc(sc.silent_nights.join(", "))} the failure changed no fact at all, so not even <code>fact trace</code> has it.` : ""}</li>
        <li><b>B</b>: one record per failure, filtered on its metadata (<code>fact list --query export_invoices</code>, then tool + error + date). The count is exact by construction, and the rule fires on night ${esc(sc.flag_night.B)}.</li>
        ${(() => { const r = sc.rows[sc.rows.length - 1], c = sc.rows[0];
          return `<li>Which recovery worked last: A ${r.okA ? "has it" : "does not name it"} this run; the count: A ${c.okA ? "got it right this run" : `shows ${esc(c.A)} of ${esc(c.truth)}`}.</li>`; })()}</ul></div>`;
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
    if (!confirm("Delete the demo's projects, conversation, actor and their facts?")) return;
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
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = { ...(S.world || {}), ...d };
    else if (k === "setup") { S.setup = d; S.world = { ...(S.world || {}), projects: d.projects }; }
    else if (k === "night") S.nights.push(d);
    else if (k === "memory") S.memory = d;
    else if (k === "precheck") S.precheck = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's projects, conversation, actor and facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The two memories are scored side by side under step 6.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "night", "memory", "precheck", "score", "done", "error"];
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

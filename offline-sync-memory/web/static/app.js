/* Offline sync memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Memory", sub: "4 orders × 3 copies", view: "setup" },
    { n: 3, title: "Snapshot", sub: "the kiosk's offline cache", view: "snapshot" },
    { n: 4, title: "Offline 70 s", sub: "requests at the kiosk, changes at HQ", view: "offline" },
    { n: 5, title: "Reconnect & sync", sub: "three ways", view: "sync" },
    { n: 6, title: "Score", sub: "whose change won?", view: "score" },
  ];
  const ARMS = ["A", "B", "C"];
  const fresh = () => ({ setup: null, snapshot: null, ticks: [], read: null, synced: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const hms = (iso) => String(iso || "").slice(11, 19);
  const shortDate = (s) => { const m = String(s || "").match(/^(\w{3})\w*day (\d+) October/); return m ? `${m[1]} ${m[2]} Oct` : s; };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup: setupView, snapshot: snapshotView, offline: offlineView, sync: syncView, score: scoreView }[S.view];
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
          <span class="muted small">about 3 minutes (70 s of it is the offline window, in real time) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-ofs")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>An edge agent goes offline, then syncs. Replaying its transcript lets an older request win.</h1>
      <p class="lead">${esc(d.company || "Fernhill Garden Centres")} (fictional) runs a kiosk assistant in ${esc(d.store || "Store 14")}; order memory is one fact per order.
      The kiosk loses its connection for 70 seconds: it answers from a local snapshot and writes customers' change requests down, while HQ — still online — changes some of the same orders.
      Then it reconnects and syncs three ways: <b>A</b> replay its conversation with each message's own <code>--timestamp</code> and let MemoryLake's extractor update the orders,
      <b>B</b> apply an outbox only where the order's <code>updated_at</code> is unchanged since the snapshot, <b>C</b> apply only where the order's last write (<code>fact trace</code>) is older than the request.
      It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH.</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-edge-deployed-ai-agents" target="_blank" rel="noopener">memorylake.ai › Memory for edge-deployed AI agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const s = S.setup;
    if (!s) return running() ? waiting("Memory") : empty("Memory");
    return `<h1>Memory: 4 orders, on three copies of the project</h1>
      <p class="lead">Each order is one fact (<code>fact add</code>), on three projects — one per sync strategy, so the three run side by side on the same story.
      A's kiosk conversation sits on A's orders project, where the extractor reads it; B and C sync the same transcript to a separate kiosk-log project, where it cannot touch an order.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>order</th><th>customer</th><th>item</th><th>date</th></tr></thead><tbody>
        ${s.orders.map((o) => `<tr><td class="mono"><b>${esc(o.order)}</b></td><td>${esc(o.customer)}</td><td>${esc(o.item)}</td><td>${esc(o.start)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function snapshotView() {
    const s = S.snapshot, a = D().arms || {};
    if (!s) return running() ? waiting("Snapshot") : empty("Snapshot");
    return `<h1>The kiosk caches every order, then the network drops</h1>
      <p class="lead">Snapshot taken at <span class="mono">${esc(hms(s.taken_at))}</span> UTC — the kiosk's whole memory for the next 70 seconds.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>order</th><th>cached date</th></tr></thead><tbody>
        ${Object.entries(s.orders).map(([o, v]) => `<tr><td class="mono"><b>${esc(o)}</b></td><td>${esc(v)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Three ways to sync on reconnect</h3><ul class="steps-list">${ARMS.map((x) => `<li><b>${esc(a[x]?.short)}</b>: ${code(a[x]?.how)}</li>`).join("")}</ul></div>`;
  }

  function offlineView() {
    if (!S.ticks.length) return running() ? waiting("Offline for 70 seconds") : empty("Offline for 70 seconds");
    const rd = S.read;
    const row = (t) => {
      const who = t.who === "hq" ? '<span class="pill accent">HQ · online</span>' : '<span class="pill">kiosk · offline</span>';
      const what = t.who === "kiosk" ? `${esc(t.says)}<br><span class="muted small">→ local outbox: order ${esc(t.order)} → <b>${esc(t.date)}</b></span>`
        : t.who === "hq" ? `${esc(t.why)}<br><span class="muted small">→ <code>fact update</code>: order ${esc(t.order)} → <b>${esc(t.date)}</b></span>`
        : `${esc(t.asks)}${rd ? `<br><span class="muted small">→ cloud unreachable: <span class="mono">${esc(rd.reason.slice(0, 60))}…</span></span><br><span class="small">→ answered from the snapshot (${esc(hms(rd.cached_at))}): <b>${esc(rd.answer)}</b></span>` : ""}`;
      return `<tr><td class="mono">+${esc(t.offset)} s<br><span class="muted small">${esc(hms(t.at))}</span></td><td>${who}</td><td>${what}</td></tr>`;
    };
    return `<h1>Offline for 70 seconds, in real time</h1>
      <p class="lead">The kiosk keeps working: it writes customers' requests to a local outbox (each with the time it heard it) and answers from its snapshot — the cloud really is unreachable (<code>--base-url</code> points nowhere).
      HQ, online, changes orders with <code>fact update</code>. Order 5521 is changed in both places: the kiosk heard Friday first, HQ set Saturday 30 seconds later. Order 5530 the other way round.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>when</th><th>where</th><th>what</th></tr></thead><tbody>${S.ticks.map(row).join("")}</tbody></table></div>`;
  }

  function syncView() {
    const y = S.synced;
    if (!y) return running() ? waiting("Reconnect and sync") : empty("Reconnect and sync");
    const b = y.B.rows, c = y.C.rows;
    return `<h1>Reconnect and sync — three ways</h1>
      <div class="card"><h3 style="margin-top:0">A: replay the transcript</h3><p>The three messages go to the orders project's conversation, each with the time the customer said it (<code>conv msg append --timestamp</code>).
      ${y.A.deferred ? "The extractor had not finished within the wait." : `The extractor touched <b>${esc(y.A.changed)}</b> fact(s) on the orders project, done ${esc(y.A.secs)} s after the first append.`} Its results are in the score.</p></div>
      <div class="card"><h3 style="margin-top:0">B: version check</h3><table class="lint plain"><thead><tr><th>order</th><th>request</th><th>updated_at: snapshot → now</th><th>outcome</th></tr></thead><tbody>
      ${b.map((r) => `<tr><td class="mono"><b>${esc(r.order)}</b></td><td>${esc(r.date)} <span class="muted small">${esc(hms(r.at))}</span></td><td class="mono">${esc(hms(r.seen))} → ${esc(hms(r.now))}</td>
        <td class="${r.held ? "bad-ink" : ""}">${r.held ? `held for review (keeps ${esc(r.current)})` : "applied"}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">C: request vs last write</h3><table class="lint plain"><thead><tr><th>order</th><th>request</th><th>last write (<code>fact trace</code>)</th><th>outcome</th></tr></thead><tbody>
      ${c.map((r) => `<tr><td class="mono"><b>${esc(r.order)}</b></td><td>${esc(r.date)} <span class="muted small">${esc(hms(r.at))}</span></td><td><span class="mono">${esc(hms(r.last_write.at))}</span> ${esc(r.last_write.event)} · ${esc(r.last_write.source)}</td>
        <td>${r.verdict === "apply" ? "request is newer: applied" : r.verdict === "superseded" ? `<b>HQ wrote later: kept, request logged as superseded</b>` : "too close to call: held"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function scoreView() {
    const sc = S.score, st = D().arms || {};
    if (!sc) return running() ? waiting("Score") : empty("Score");
    const orders = (D().orders || []);
    const sm = sc.summary;
    const cell = (a, o) => { const r = sc.rows[a].find((x) => x.order === o.order); return `<td class="${r.ok ? "" : "bad-ink"}">${r.ok ? "✓" : "✗"} <b>${esc(r.got)}</b>${r.held ? ' <span class="pill">held</span>' : ""}</td>`; };
    const ex = (sc.explain || []).map((e) => `<div class="card"><h3 style="margin-top:0">A, order ${esc(e.order)}: ${e.facts.length} fact(s) name it — the kiosk reads the newest</h3>
      <table class="lint plain"><thead><tr><th>date</th><th>last write</th><th>from a message said at</th><th></th></tr></thead><tbody>
      ${e.facts.map((f) => `<tr><td><b>${esc(f.date)}</b></td><td>${esc(f.event)} · <b>${esc(f.source)}</b> <span class="mono">${esc(hms(f.at))}</span></td><td class="mono">${esc((f.said || []).map(hms).join(", ") || "—")}</td><td class="muted small">${f.own ? "the order's own fact" : "a new fact"}</td></tr>`).join("")}</tbody></table>
      <p class="small">${e.request ? `The customer asked at <span class="mono">${esc(hms(e.request.at))}</span> for ${esc(e.request.date)}` : ""}${e.hq ? `; HQ changed it at <span class="mono">${esc(hms(e.hq.at))}</span> to ${esc(e.hq.date)}` : ""}.</p></div>`).join("");
    const fu = sc.follow_up;
    const shape = sm.A.extractor || {};
    return `<h1>Whose change won? A: ${sm.A.current}/4 · B: ${sm.B.current}/4 · C: ${sm.C.current}/4</h1>
      <p class="lead">What the kiosk reads for each order after the sync: the newest fact that names it. “Should be” is the latest change by the time it was made.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>order</th><th>customer</th><th>should be</th>${ARMS.map((a) => `<th>${esc(st[a]?.short)}</th>`).join("")}</tr></thead><tbody>
      ${orders.map((o) => `<tr><td class="mono"><b>${esc(o.order)}</b></td><td>${esc(o.customer)}</td><td><b>${esc(sc.truth[o.order])}</b></td>${ARMS.map((a) => cell(a, o)).join("")}</tr>`).join("")}
      </tbody></table></div>${ex}
      <div class="card"><ul class="steps-list">
        <li><b>A</b>: the extractor ${esc(shape.label || "")} (${esc(shape.rewrote)} rewritten, ${esc(shape.added)} added). It applies replayed messages as they arrive — a message's <code>--timestamp</code> does not make it older than a write that came first. Nothing flags it.</li>
        <li><b>B</b> never overwrites HQ — and holds ${sm.B.held.length} request(s) for a person, ${sm.B.false_holds.length} of them (${esc(sm.B.false_holds.join(", "))}) made <i>after</i> HQ's change: the customer's latest wish waits in a queue, the order still says the old date.</li>
        <li><b>C</b> compares the request's time with the order's last write and keeps the later one; the superseded request goes to the kiosk-log project, so the customer gets a call, not a silent change.</li>
        ${fu ? `<li>Offline answer: order ${esc(fu.order)} was told <b>${esc(fu.told)}</b> from the snapshot; memory now says <b>${esc(fu.now)}</b>${fu.stale ? " — the kiosk flags a call-back" : ""}.</li>` : ""}</ul></div>`;
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
    if (!confirm("Delete the demo's projects, conversations, actor and their facts?")) return;
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
    else if (k === "snapshot") S.snapshot = d;
    else if (k === "tick") S.ticks.push(d);
    else if (k === "offline_read") S.read = d;
    else if (k === "synced") S.synced = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's projects, conversations, actor and facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The three sync strategies are scored side by side under step 6.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "snapshot", "tick", "offline_read", "synced", "score", "done", "error"];
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

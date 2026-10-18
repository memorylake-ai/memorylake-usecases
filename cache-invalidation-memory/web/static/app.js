/* Cache invalidation memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Memory", sub: "6 policies + a billing channel", view: "setup" },
    { n: 3, title: "Warm the caches", sub: "4 questions, then a quiet minute", view: "warm" },
    { n: 4, title: "An API write", sub: "the admin tool edits a policy", view: "edit" },
    { n: 5, title: "A channel message", sub: "the extractor rewrites two", view: "channel" },
    { n: 6, title: "A policy retired", sub: "fact delete", view: "retire" },
    { n: 7, title: "Score", sub: "which cache served stale answers?", view: "score" },
  ];
  const ARMS = ["A", "B", "C"];
  const fresh = () => ({ setup: null, rounds: {}, edit: null, message: null, extracted: null, retire: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const ms = (iso) => String(iso || "").slice(11, 23);

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup: setupView, warm: warmView, edit: editView, channel: channelView, retire: retireView, score: scoreView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const w = S.world || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Project</dt><dd>${w.project ? `<span class="mono">${esc(w.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${w.project ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 90 seconds · everything it creates is prefixed <code>${esc(d.prefix || "mlu-civ")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Cache your agent's memory reads. The project version never sees the extractor's writes.</h1>
      <p class="lead">${esc(d.company || "Harlow Desk")} (fictional) runs a support agent on project memory; each answer is a <code>search</code> (about 3 s), so the team caches the answers —
      three ways side by side, against a direct search every time: <b>A</b> a 15-minute TTL, <b>B</b> check the project's version (<code>proj get</code> → <code>updated_at</code>) before each read,
      <b>C</b> check the facts' watermark (<code>fact list --page-size 1</code>: newest <code>updated_at</code> + <code>total</code>). Then the policies change — by an API call, by a message the extractor reads, by a delete.
      It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH.</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-caching-strategies-for-agent-apps" target="_blank" rel="noopener">memorylake.ai › Memory caching strategies for agent apps</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const s = S.setup;
    if (!s) return running() ? waiting("Memory") : empty("Memory");
    return `<h1>Memory: 6 support policies, and a billing channel on the same project</h1>
      <p class="lead">Each policy is one fact (<code>fact add</code>). The billing lead posts in a channel — a conversation on the same project, so MemoryLake's extractor reads it and updates the policies it mentions.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>key</th><th>policy</th></tr></thead><tbody>
        ${s.policies.map((p) => `<tr><td class="mono"><b>${esc(p.key)}</b></td><td>${esc(p.text)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function roundTable(r) {
    const st = D().arms || {};
    const cell = (x) => `<td class="${x.stale ? "bad-ink" : ""}">${x.stale ? "✗" : "✓"} <b>${esc(x.value)}</b> <span class="pill ${x.hit ? "accent" : ""}">${x.hit ? "hit" : "miss"}</span></td>`;
    return `<div class="card"><h3 style="margin-top:0">${esc(r.label)}</h3><table class="lint plain"><thead><tr><th>question</th><th>memory says</th>${ARMS.map((a) => `<th>${esc(st[a]?.short || a)}</th>`).join("")}</tr></thead><tbody>
      ${r.rows.map((x) => `<tr><td><span class="mono">${esc(x.id)}</span> <span class="small">${esc(x.ask)}</span></td><td><b>${esc(x.live_value)}</b></td>${ARMS.map((a) => cell(x[a])).join("")}</tr>`).join("")}
      <tr><td><b>current</b></td><td></td>${ARMS.map((a) => `<td><b class="${r.stale[a].length ? "bad-ink" : ""}">${4 - r.stale[a].length}/4</b></td>`).join("")}</tr></tbody></table></div>`;
  }
  const R = (n) => S.rounds[n];

  function warmView() {
    if (!R(0)) return running() ? waiting("Warm the caches") : empty("Warm the caches");
    const st = D().arms || {};
    return `<h1>Warm the caches, then a quiet minute</h1>
      <p class="lead">${ARMS.map((a) => `<b>${esc(st[a]?.short)}</b>: ${code(st[a]?.how)}`).join("<br>")}</p>${roundTable(R(0))}${R(1) ? roundTable(R(1)) : ""}`;
  }

  function editView() {
    const e = S.edit;
    if (!R(2)) return running() ? waiting("An API write") : empty("An API write");
    return `<h1>An API write: the admin tool changes a policy</h1>
      <p class="lead">${esc(e?.who)}: ${esc(e?.why)} — <code>fact update</code>, same fact id: <i>${esc(e?.body)}</i> The project's version moves with it, so B and C refetch; A keeps its entry for 15 minutes.</p>${roundTable(R(2))}`;
  }

  function channelView() {
    const m = S.message, x = S.extracted;
    if (!m) return running() ? waiting("A channel message") : empty("A channel message");
    const writes = x ? `<div class="card"><h3 style="margin-top:0">The extractor's writes (<code>fact trace</code>), ${esc(x.secs)} s after the message</h3>
      ${x.deferred ? `<p class="muted">The extractor wrote nothing within the wait. The rounds show what that means.</p>` : `<table class="lint plain"><thead><tr><th>event</th><th>source</th><th>at</th><th>policy</th><th>old → new</th></tr></thead><tbody>
      ${x.writes.map((w) => `<tr><td>${esc(w.event)}</td><td><b>${esc(w.source)}</b></td><td class="mono">${esc(ms(w.at))}</td><td class="mono">${esc(w.key)}</td><td class="small">${esc(w.old || "—")}<br>→ ${esc(w.new)}</td></tr>`).join("")}</tbody></table>`}
      <p>Project version: <span class="mono">${esc(ms(x.v_before))}</span> before the message → <span class="mono">${esc(ms(x.v_append))}</span> at the append → <b class="mono ${x.v_after === x.v_append ? "bad-ink" : ""}">${esc(ms(x.v_after))}</b> after the extractor
      ${x.v_after === x.v_append ? '<span class="bad-ink">— unchanged: the extractor\'s writes do not move it</span>' : ""}</p></div>` : "";
    const b = R(4) ? R(4).rows.filter((r) => r.B.stale) : [];
    const hl = b.length ? `<div class="card"><h3 style="margin-top:0">B: “version unchanged ✓”</h3>${b.map((r) => `<p><b>${esc(r.short)}</b> — served from cache: <span class="bad-ink">${esc(r.B.text)}</span><br><span class="muted small">memory says</span> ${esc(r.live)}</p>`).join("")}</div>` : "";
    return `<h1>A message in the billing channel: the extractor rewrites two policies</h1>
      <p class="lead"><i>${esc(m.body)}</i><br>The append moves the project's version. Tickets keep coming, so the agent answers right away — before the extractor has run. B refetches and files the old values under the new version.</p>
      ${R(3) ? roundTable(R(3)) : ""}${writes}${R(4) ? roundTable(R(4)) : ""}${hl}`;
  }

  function retireView() {
    const r = S.retire;
    if (!r) return running() ? waiting("A policy retired") : empty("A policy retired");
    return `<h1>Another API write: a policy is retired</h1>
      <p class="lead">${esc(r.who)}: ${esc(r.why)} (<code>fact delete</code>). The newest fact's <code>updated_at</code> does not move (<span class="mono">${esc(ms(r.before))}</span> → <span class="mono">${esc(ms(r.after))}</span>); <code>total</code> does (${esc(r.total_before)} → ${esc(r.total_after)}) — that is why C watches both. The project version moves too, so B is current again: healed by an unrelated write.</p>
      ${R(5) ? roundTable(R(5)) : waiting("")}`;
  }

  function scoreView() {
    const sc = S.score, st = D().arms || {};
    if (!sc) return running() ? waiting("Score") : empty("Score");
    const r = sc.rows, x = sc.extractor || {};
    return `<h1>Which cache served stale answers? C: none, at the same cost as B</h1>
      <p class="lead">Five rounds of four questions after the warm-up; “stale” = a different answer from a direct <code>search</code> at the same moment.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>cache</th><th>stale answers</th><th>cache hits</th><th>validator calls</th><th>s / read: all rounds</th><th>s / read: quiet minute</th></tr></thead><tbody>
      ${ARMS.map((a) => { const y = r[a]; return `<tr><td><b>${esc(st[a]?.short)}</b></td><td class="${y.stale ? "bad-ink" : ""}"><b>${y.stale}/${y.reads}</b></td><td>${y.hits}/${y.reads}</td><td>${y.validator_calls}</td><td>${y.avg_secs}</td><td>${y.quiet_secs}</td></tr>`; }).join("")}
      </tbody></table></div>
      <div class="card"><ul class="steps-list">
        <li><b>A</b> is fast and wrong for 15 minutes after every change, whoever made it.</li>
        <li><b>B</b> is right after every API write — and wrong after the extractor's: the version stopped at the message append (<span class="mono">${esc(ms(x.v_append))}</span>), the extractor wrote later, and the entry filled in between stayed “current”. B came right again only when an unrelated <code>fact delete</code> moved the version.</li>
        <li><b>C</b> watches the facts themselves: the extractor's rewrite moves the newest <code>updated_at</code>, a delete moves <code>total</code>. One call under a second, the same as B.</li></ul></div>`;
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
    if (!confirm("Delete the demo's project, conversation, actor and their facts?")) return;
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
    else if (k === "setup") S.setup = d;
    else if (k === "round") S.rounds[d.n] = d;
    else if (k === "edit") S.edit = d;
    else if (k === "message") S.message = d;
    else if (k === "extracted") S.extracted = d;
    else if (k === "retire") S.retire = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's project, conversation, actor and facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The three caches are scored side by side under step 7.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "round", "edit", "message", "extracted", "retire", "score", "done", "error"];
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

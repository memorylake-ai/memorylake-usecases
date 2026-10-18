/* Behavior diff memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "The policies", sub: "10 rules, text + metadata", view: "setup" },
    { n: 3, title: "Eval 1", sub: "six answers, with receipts", view: "eval1" },
    { n: 4, title: "A week of changes", sub: "11 ordinary writes", view: "week" },
    { n: 5, title: "Eval 2", sub: "the same six questions", view: "eval2" },
    { n: 6, title: "Behavior diff", sub: "which answers changed?", view: "behavior" },
    { n: 7, title: "Three memory diffs", sub: "which change caused it?", view: "diffs" },
  ];
  const METHODS = ["A", "B", "C"];
  const fresh = () => ({ setup: null, asked: { 1: [], 2: [] }, evals: {}, week: [], weekDone: null, behavior: null, attribution: [], score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const hhmmss = (iso) => (iso || "").slice(11, 19);
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const verdictOf = (x) => (x.exact && x.restores ? "ok" : x.hit ? "part" : "bad");
  const mark = (v) => `<span class="mark ${v === "ok" ? "ok" : "bad"}">${v === "ok" ? "✓" : v === "part" ? "◐" : "✗"}</span>`;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup: setupView, eval1: () => evalView(1), week: weekView, eval2: () => evalView(2), behavior: behaviorView, diffs: diffsView }[S.view];
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
          <span class="muted small">about 40 seconds · everything it creates is prefixed <code>${esc(d.prefix || "mlu-bdm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The agent's answers changed. Which memory change did it?</h1>
      <p class="lead">${esc(d.company || "Sallowfen Outfitters")} (fictional) runs a support agent on project memory: one fact per policy, a text people read and metadata the agent's code reads.
      Eval 1 asks six questions; then a week of ordinary memory changes; eval 2 asks them again and three answers differ. Three ways to find the cause: the time window in <code>fact trace</code>, a diff of two full snapshots,
      and a diff of the memory each answer read, checked by replaying the agent. It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH (<code>fact trace</code> with full history).</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-diff-for-agent-behavior-analysis" target="_blank" rel="noopener">memorylake.ai › Memory diff for agent behavior analysis</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setupView() {
    const s = S.setup;
    if (!s) return running() ? waiting("The policies") : empty("The policies");
    return `<h1>The policies: 10 rules in project memory</h1>
      <p class="lead">Each rule is one fact: a text, then <code>fact update --metadata</code> with what the agent's code reads (topic, status, which products, the limit). One rule is a draft: written, not yet approved.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>key</th><th>topic</th><th>status</th><th>text</th></tr></thead><tbody>
        ${s.rules.map((r) => `<tr><td class="mono"><b>${esc(r.key)}</b></td><td>${esc(r.topic)}</td><td>${r.status === "draft" ? '<span class="pill">draft</span>' : esc(r.status)}</td><td>${esc(r.text)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function evalView(n) {
    const a = S.asked[n];
    const h = n === 1 ? "Eval 1: six questions, each answer with a receipt" : "Eval 2: the same six questions";
    if (!a.length) return running() ? waiting(h) : empty(h);
    const e = S.evals[n];
    return `<h1>${h}</h1>
      <p class="lead">The agent is plain code: it reads the rules on the question's topic (that list is the answer's <b>receipt</b>), keeps the active ones, prefers the one for the product over the general one, and answers from the rule's limit.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>q</th><th>question</th><th>answer</th><th>rule applied</th><th>receipt: rules read</th></tr></thead><tbody>
        ${a.map((r) => `<tr><td class="mono">${esc(r.qid)}</td><td>${esc(r.ask)}</td><td><b>${esc(r.answer)}</b></td><td class="mono">${esc(r.applied || "—")}</td><td class="mono small">${esc(r.read.join(", ") || "—")}</td></tr>`).join("")}
      </tbody></table>${e ? `<p class="small muted" style="margin-bottom:0">Saved to <code>out/eval${n}.json</code> with the full snapshot. Newest write on the server clock: <span class="mono">${esc(hhmmss(e.at))}</span>.</p>` : ""}</div>`;
  }

  function weekView() {
    const w = S.week;
    if (!w.length) return running() ? waiting("A week of changes") : empty("A week of changes");
    const cls = (k) => (k === "metadata only" ? "bad-ink" : "");
    return `<h1>A week of memory changes: 11 ordinary writes</h1>
      <p class="lead">Copy edits, a policy change, two new rules, a sale that ended — and one approval: the outerwear rule's <code>status</code> goes from draft to active. That one is a <code>fact update</code> that changes only metadata, like the three review stamps.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>#</th><th>rule</th><th>change</th><th>why</th></tr></thead><tbody>
        ${w.map((c, i) => `<tr><td class="num">${i + 1}</td><td class="mono"><b>${esc(c.key)}</b></td><td class="${cls(c.change)}">${esc(c.change)}</td><td>${esc(c.why)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function behaviorView() {
    const b = S.behavior;
    if (!b) return running() ? waiting("Behavior diff") : empty("Behavior diff");
    return `<h1>Behavior diff: ${b.changed.length} of ${b.rows.length} answers changed</h1>
      <p class="lead">Same code, same questions. The difference is in memory — but which of the 11 changes did which?</p>
      <div class="card"><table class="lint plain"><thead><tr><th>q</th><th>question</th><th>eval 1</th><th>eval 2</th></tr></thead><tbody>
        ${b.rows.map((r) => { const ch = r.before !== r.after; return `<tr><td class="mono">${esc(r.id)}</td><td>${esc(r.ask)}</td><td>${esc(r.before)}</td><td class="${ch ? "bad-ink" : ""}"><b>${esc(r.after)}</b>${ch ? " ← changed" : ""}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function diffsView() {
    const d = D(), at = S.attribution, sc = S.score;
    if (!at.length) return running() ? waiting("Three memory diffs") : empty("Three memory diffs");
    const methods = d.methods || {};
    const table = sc ? `<div class="card"><h3 style="margin-top:0">Score — ${sc.rows.C.of} changed answers</h3><table class="lint plain"><thead><tr><th>method</th><th class="num">changes listed</th><th class="num">named the cause</th><th class="num">named exactly the cause</th><th class="num">replay gives back eval 1</th></tr></thead><tbody>
      ${METHODS.map((m) => { const x = sc.rows[m]; const cell = (v) => `<b class="${v === x.of ? "" : "bad-ink"}">${v}/${x.of}</b>`;
        return `<tr><td><b>${esc(methods[m].title)}</b></td><td class="num">${x.listed}</td><td class="num">${cell(x.hit)}</td><td class="num">${cell(x.exact)}</td><td class="num">${cell(x.restores)}</td></tr>`; }).join("")}
      </tbody></table>
      <p class="small" style="margin-bottom:0">${sc.hidden.length} of the ${sc.snapdiff.length} changes have no entry in <code>fact trace</code> (${sc.hidden.map(esc).join(", ")}): a <code>fact update</code> that changes only metadata moves <code>updated_at</code> and nothing else.</p></div>` : "";
    const block = (r) => `<div class="card"><h3 style="margin-top:0">${esc(r.qid)} · ${esc(r.before)} → ${esc(r.after)} <span class="muted small">${esc(r.ask)}</span></h3>
      <table class="lint plain"><thead><tr><th>method</th><th>names</th><th>replay with them put back</th><th></th></tr></thead><tbody>
      ${METHODS.map((m) => { const x = r.methods[m]; const v = verdictOf(x);
        return `<tr><td><b>${esc(methods[m].short)}</b></td><td class="mono ${v === "bad" ? "bad-ink" : ""}">${esc(x.named.join(", ") || "nothing")}</td><td>${esc(x.replay)}</td><td>${mark(v)}</td></tr>`; }).join("")}
      </tbody></table>
      ${r.tested.length ? `<p class="small" style="margin-bottom:0">C replays one fact at a time: ${r.tested.map((t) => `<span class="mono">${esc(t.key)}</span> ${t.restores ? `→ ${esc(r.before)} ✓` : "→ no change"}`).join(" · ")}</p>` : ""}
      ${r.methods.A.exact && !r.methods.A.restores ? `<p class="small muted" style="margin-bottom:0">A names the right fact, but the trace has its old text, not its old metadata, so the replay cannot put it back.</p>` : ""}</div>`;
    return `<h1>${sc ? `Which change caused it? Exactly right: ${METHODS.map((m) => `${m} ${sc.rows[m].exact}/${sc.rows[m].of}`).join(" · ")}` : "Three memory diffs"}</h1>
      <p class="lead">${METHODS.map((m) => `<b>${esc(methods[m].short)}</b>: ${code(methods[m].how)}`).join("<br>")}</p>
      ${table}${at.map(block).join("")}
      <p class="small muted">✓ named exactly the cause and the replay gives back the eval-1 answer · ◐ the cause is among the named, or named but the replay cannot put it back · ✗ the cause is not named</p>`;
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
    if (!confirm("Delete the demo's memory project and its facts?")) return;
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
      if (d.phase === "change") S.week.push(d);
      if (S.caughtUp && S.view === "week") renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = { ...(S.world || {}), ...d };
    else if (k === "setup") S.setup = d;
    else if (k === "asked") { S.asked[d.eval].push(d); if (S.caughtUp && S.view === `eval${d.eval}`) renderStage(); }
    else if (k === "eval") S.evals[d.n] = d;
    else if (k === "week") S.weekDone = d;
    else if (k === "behavior") S.behavior = d;
    else if (k === "attribution") S.attribution.push(d);
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo's project and its facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The three diffs are side by side under step 7.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text", "asked"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "setup", "asked", "eval", "week", "behavior", "attribution", "score", "done", "error"];
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
    show(state.status === "running" ? S.view : S.score ? "diffs" : "connect");
    subscribe();
  })();
})();

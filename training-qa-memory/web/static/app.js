/* Training Q&A memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "Training memory", sub: "procedures, policies, cohort Q&A", view: "memory" },
    { n: 4, title: "Policy change", sub: "five rules tightened", view: "change" },
    { n: 5, title: "Jun asks", sub: "after the policy update", view: "updated" },
    { n: 6, title: "What was attached", sub: "Claude copies it out", view: "attached" },
    { n: 7, title: "+ change note", sub: "the usual fix", view: "noted" },
    { n: 8, title: "Re-answer the Q&A", sub: "found by relies_on", view: "sweep" },
    { n: 9, title: "Ask again", sub: "and the scoreboard", view: "swept" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, policies: [], answers: { updated: [], noted: [], swept: [] }, inspect: [], reanswer: [], score: null,
    unavailable: null, summary: null, asked: [], termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>your own question</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, change, updated: () => answers("updated"), attached, noted: () => answers("noted"), sweep, swept: () => answers("swept") + score(), ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (ok) => ok == null ? '<span class="mark">·</span>' : `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  const pol = (key) => (S.data?.policies || []).find((p) => p.key === key) || {};

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-tqm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The policy changed. The answers it shaped did not.</h1>
      <p class="lead">Larkspur's support team keeps its training memory in MemoryLake: procedures, policies, and the questions last spring's new agents asked with the answers a senior agent gave.
      The team lead tightens five policies and updates the policy facts. A new agent asks the training assistant about the same situations — and gets last spring's answers.
      This demo drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 15 calls on gpt-4o-mini and 5 on claude-haiku-4-5 per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/support-agent-training-memory" target="_blank" rel="noopener">memorylake.ai › Support agent training memory</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    return `<h1>One project for the training memory, one boundary for the assistant</h1>
      <p class="lead">The training assistant never assembles memory itself: every question is an ordinary model call with <code>--boundary</code> added, and MemoryRouter picks what to attach.</p>
      <div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(S.world?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(S.world?.boundary || "pending")}</b></dd></dl>
        <h3>Each question</h3>
        <div class="cmdline">router chat "&lt;Jun's question&gt;" --model gpt-4o-mini --body &lt;system prompt&gt; --boundary ${esc(S.world?.boundary || "…")}</div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("The training memory") : empty("The training memory");
    const d = S.data, k = S.memory.kinds || {};
    const deps = d.qa.filter((q) => q.relies_on.length);
    return `<h1>The training memory — ${fmt(S.memory.facts)} facts</h1>
      <p class="lead">${k.procedure} procedures, ${k.policy} policies and ${k.qa} questions from the ${esc(d.cohort.name)}, answered by ${esc(d.cohort.senior)}. Each fact carries metadata;
      each Q&amp;A records the policies its answer relies on (<code>metadata.relies_on</code>).</p>
      <div class="cols"><div class="card"><h3 style="margin-top:0">Policies, as the spring cohort learned them</h3>
        ${d.policies.map((p) => `<div class="memfact rule small"><span>${esc(p.v1)}</span></div>`).join("")}</div>
        <div class="card"><h3 style="margin-top:0">Spring Q&amp;A that rely on a policy</h3>
        ${deps.map((q) => `<div class="memfact small"><span><b>${esc(q.asked_by)}:</b> “${esc(q.q)}”<br>${esc(q.a)} <span class="faint">relies_on: ${esc(q.relies_on.join(", "))}</span></span></div>`).join("")}
        <p class="muted small">${d.qa.length - deps.length} more Q&amp;A rely on no policy (SSO, data export, chargebacks …).</p></div></div>`;
  }

  function change() {
    if (!S.policies.length) return running() ? waiting("The policy change") : empty("The policy change");
    return `<h1>${esc(S.data.policy_change.on)} — five policies tightened</h1>
      <p class="lead">${esc(S.data.policy_change.by)} updates the five policy facts with <code>fact update --text … --metadata</code> (version 2, the old text kept in <code>previous</code>).
      Nobody goes back to the answers the spring cohort was given.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>Policy</th><th>was</th><th>now</th></tr></thead><tbody>
        ${S.policies.map((p) => `<tr><td>${esc(p.label)}</td><td class="bad-ink">${esc(p.old)}</td><td><b>${esc(p.new)}</b></td></tr>`).join("")}</tbody></table></div>`;
  }

  const TITLES = { updated: "Jun asks — the policies are updated, the Q&A is not", noted: "With a change note on each policy", swept: "Jun asks again — the Q&A re-answered" };
  function answers(state) {
    const rows = S.answers[state];
    if (!rows.length) return unavailable() || (running() ? waiting(TITLES[state]) : empty(TITLES[state]));
    const got = rows.filter((r) => r.ok != null), old = rows.filter((r) => r.follows === "old").length;
    const lead = { updated: "Five questions, each a new chat through <code>router chat --boundary</code> (gpt-4o-mini, temperature 0). The memory holds the new rule for every one of them.",
      noted: "Each changed policy now also says what it replaced and when: <i>“" + esc(pol("refund").noted || "") + "”</i>",
      swept: "The five Q&amp;A that relied on a changed policy were found by their <code>relies_on</code> metadata and re-answered under the new rules." }[state];
    return `<h1>${TITLES[state]} — ${got.filter((r) => r.ok).length}/${got.length} right</h1>
      <p class="lead">${lead}${old ? ` <b class="bad-ink">${old} answer${old > 1 ? "s" : ""} quote the rule the spring cohort was taught.</b>` : ""}</p>
      <div class="card chat">${rows.map((r) => `<div class="msg user"><span class="who">Jun</span>${esc(r.say)}</div>
        <div class="msg bot ${r.ok === false ? "wrong" : ""}"><span class="who">training assistant ${markOf(r.ok)}${r.follows === "old" ? ` <b class="bad-ink">· old rule (${esc(pol(r.key).old)})</b>` : ""}</span>${esc(r.reply || "…")}</div>`).join("")}</div>`;
  }

  const LABEL_CLASS = { "policy (current)": "lab-ok", "spring Q&A (old answer)": "lab-bad", "policy (OLD)": "lab-bad" };
  function attached() {
    if (!S.inspect.length) return unavailable() || (running() ? waiting("What MemoryRouter attached") : empty("What MemoryRouter attached"));
    const qa = S.inspect.filter((c) => c.old_qa).length, p = S.inspect.filter((c) => c.policy).length;
    return `<h1>What MemoryRouter attached — the old answer on ${qa} of ${S.inspect.length}, the current policy on ${p}</h1>
      <p class="lead">The same five questions through the same boundary to <b>claude-haiku-4-5</b>, told in its system prompt to copy out every remembered entry first.
      A worked answer to the same situation is a closer match to Jun's question than the rule it was derived from. (The checker is its own call, so what it got can differ a little from the answer's call.)</p>
      <div class="grid2">${S.inspect.map((c) => `<div class="card"><h3 style="margin-top:0">“${esc(c.say)}”</h3>
        ${c.lines.map((l, i) => `<div class="memfact small"><span>${c.labels[i] ? `<span class="lab ${LABEL_CLASS[c.labels[i]] || ""}">${esc(c.labels[i])}</span> ` : ""}${esc(l)}</span></div>`).join("")}
        ${c.policy ? "" : '<p class="small bad-ink">The current policy is not among them.</p>'}</div>`).join("")}</div>`;
  }

  function sweep() {
    if (!S.reanswer.length) return running() ? waiting("Re-answer the Q&A") : empty("Re-answer the Q&A");
    return `<h1>${S.reanswer.length} Q&amp;A relied on a changed policy — re-answered</h1>
      <p class="lead"><code>fact list</code> with metadata: every Q&amp;A whose <code>relies_on</code> names a policy at version 2 was answered under the old rule.
      Each is rewritten with <code>fact update --text … --metadata</code> (the old text kept in <code>previous</code>). Q&amp;A that rely on nothing that changed are left alone.</p>
      <div class="grid2">${S.reanswer.map((r) => `<div class="card"><h3 style="margin-top:0">“${esc(r.q)}”</h3><p class="faint small">relies_on: ${esc(r.relies_on.join(", "))}</p>
        <div class="memfact gone small"><span>${esc(r.was)}</span></div><div class="memfact new small"><span>${esc(r.now)}</span></div></div>`).join("")}</div>`;
  }

  function score() {
    if (!S.score) return "";
    const tot = S.score.totals || {};
    return `<h2 style="margin-top:24px">Scoreboard</h2>
      <div class="card"><table class="lint plain"><thead><tr><th>Jun asks</th><th>policies updated</th><th>+ change note</th><th>Q&amp;A re-answered</th></tr></thead><tbody>
        ${S.score.rows.map((r) => `<tr><td>${esc(r.say)}</td><td>${markOf(r.updated)}</td><td>${markOf(r.noted)}</td><td>${markOf(r.swept)}</td></tr>`).join("")}
        <tr class="hl"><td><b>right</b></td><td>${tot.updated ?? "–"}/5</td><td>${tot.noted ?? "–"}/5</td><td>${tot.swept ?? "–"}/5</td></tr></tbody></table>
        <p class="muted small">Same questions, same model, same boundary; only the memory changed between columns.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    return `<h1>Ask the training assistant</h1>
      <p class="lead">One question through MemoryRouter with the training boundary, as the memory stands now. After a full run the Q&amp;A are re-answered; try a situation the spring cohort did not ask about.</p>
      <div class="card"><div class="chat" id="chat-log">${S.asked.map((x) => `<div class="msg user"><span class="who">you</span>${esc(x.say)}</div>
          <div class="msg bot router"><span class="who">training assistant · ${esc(x.seconds)} s</span>${esc(x.reply)}</div>`).join("") || (ready ? '<div class="empty">No questions yet.</div>' : '<div class="empty">Run the demo first.</div>')}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Can I extend a trial by 2 days on my own?" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Can I extend a trial by 2 days on my own?", "A customer disputes a $150 charge. Escalate?", "Who handles a chargeback?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>`;
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
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); send($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => send(c.dataset.q)));
    }
  }
  async function send(say) {
    if (!say) return;
    const log = $("#chat-log"); if (log) log.insertAdjacentHTML("beforeend", `<div class="msg user"><span class="who">you</span>${esc(say)}</div><span class="pill busy" id="sending"><span class="spinner"></span> asking…</span>`);
    const r = await api("/api/ask", { say });
    if (r) S.asked.push(r);
    if (S.view === "ask") renderStage();
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
  function resetView() { S.memory = null; S.policies = []; S.answers = { updated: [], noted: [], swept: [] }; S.inspect = []; S.reanswer = []; S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's memory project (and every fact in it) and its boundary?")) return;
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
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "memory") S.memory = d;
    else if (k === "policy") S.policies.push(d);
    else if (k === "answer") S.answers[d.state].push(d);
    else if (k === "reanswer") S.reanswer.push(d);
    else if (k === "inspect") S.inspect.push(d);
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the memory project, its facts and its boundary are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: your own question to the training assistant.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "policy", "answer", "inspect", "reanswer", "scoreboard", "summary", "router_unavailable", "done", "error"];
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
    show(state.status === "running" ? S.view : S.score ? "swept" : "connect");
    subscribe();
  })();
})();

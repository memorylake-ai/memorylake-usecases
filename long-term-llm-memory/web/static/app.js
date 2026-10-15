/* Long-term memory for any LLM — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "Week 1", sub: "20 facts, 4 questions × 3 ways", view: "week1" },
    { n: 4, title: "Month 3", sub: "100 facts, same questions", view: "month3" },
    { n: 5, title: "Month 9", sub: "400 facts, same questions", view: "month9" },
    { n: 6, title: "Extra names", sub: "looked up in memory", view: "extras" },
    { n: 7, title: "Scoreboard", sub: "tokens and exact answers", view: "score" },
  ];
  const MODES = [["none", "no memory"], ["everything", "everything pasted"], ["router", "MemoryRouter"]];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: {}, answers: {}, rounds: {}, extras: [], score: null, other: null, unavailable: null, summary: null,
    ask: { q: "", mode: "all", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
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
    const r = { connect, setup, week1: () => round("week1"), month3: () => round("month3"), month9: () => round("month9"), extras, score, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const mark = (a) => {
    if (!a) return '<span class="muted">…</span>';
    const extra = (a.extra || []).map((n) => `<span class="mark bad">+ ${esc(n)}</span>`).join("");
    const head = a.mark.split("  + ")[0];
    return `<span class="mark ${a.ok ? "ok" : "bad"}">${esc(head)}</span>${extra}`;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-ltm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Long-term memory for any LLM, without pasting all of it into every prompt.</h1>
      <p class="lead">An assistant remembers one user, <b>Maya</b> (fictional): 20 facts in week 1, 100 by month 3, 400 by month 9. At each point the same four
      questions are asked three ways: with no memory, with the <b>whole memory pasted</b> into the system prompt, and through <b>MemoryRouter</b> with one
      <code>--boundary</code>. Every reply is graded against what is stored. It drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 40 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/long-term-memory-for-llms" target="_blank" rel="noopener">memorylake.ai › long-term memory for LLMs</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const cps = S.data.checkpoints;
    return `<h1>One memory project, one boundary the app passes to MemoryRouter</h1>
      <p class="lead">Every fact is stored verbatim with <code>fact add</code>. The app never assembles a memory prompt itself: it adds
      <code>--boundary</code> to an ordinary model call and MemoryRouter attaches what matters to that question.</p>
      <div class="cols"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(S.world?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(S.world?.boundary || "pending")}</b></dd></dl>
        <h3>The three ways of asking</h3>
        <div class="cmdline">router chat "&lt;question&gt;" --model gpt-4o-mini --system "…"</div>
        <div class="cmdline">router chat "&lt;question&gt;" --model gpt-4o-mini --body out/everything-&lt;point&gt;.json</div>
        <div class="cmdline">router chat "&lt;question&gt;" --model gpt-4o-mini --system "…" --boundary ${esc(S.world?.boundary || "…")}</div>
        <p class="muted small">The body file is a whole provider request whose system message lists every stored fact — the usual way to give a model memory.</p></div>
        <div class="card"><h3 style="margin-top:0">The memory grows</h3>${cps.map((c) => `<div class="barrow"><span>${esc(c.label)}</span><div class="bar" style="width:${Math.round(100 * c.facts / 400)}%"></div><span class="n">${S.memory[c.key] ?? "—"} / ${c.facts}</span></div>`).join("")}
        <h3>A few of the facts</h3><div class="factlist">${S.data.sample.map((f) => `<div class="memfact"><span>${esc(f)}</span></div>`).join("")}</div></div></div>`;
  }

  function round(key) {
    const cp = S.data?.checkpoints.find((c) => c.key === key);
    const a = S.answers[key];
    if (!cp) return "";
    if (!a && S.memory[key] === undefined) return unavailable() || empty(`${cp.label} — ${cp.facts} facts`);
    const r = S.rounds[key];
    const qs = S.data.questions;
    const rows = qs.map((q) => MODES.map(([m, label], i) => {
      const x = (a || {})[`${q.key}:${m}`];
      return `<tr class="${m === "router" ? "hl" : ""}"><td>${i === 0 ? esc(q.label) : ""}</td><td>${label}</td>
        <td class="num">${x ? (m === "none" ? "—" : fmt(x.memory_tokens) + " tok") : "…"}</td><td class="num">${x ? x.seconds.toFixed(1) + "s" : ""}</td>
        <td>${x ? `<details><summary>${mark(x)}</summary><div class="reply">${esc(x.reply)}</div></details>` : '<span class="muted">…</span>'}</td></tr>`;
    }).join("")).join("");
    return `<h1>${esc(cp.label)} — ${cp.facts} facts in memory</h1>
      <p class="lead">The same four questions, three ways. <b>Memory in prompt</b> = prompt tokens minus the same question with no memory.
      Lists are graded name by name against the contacts stored right now: right / missed / <b>extra</b> (a name that does not belong).</p>
      ${r ? `<div class="row"><span class="pill">pasted: ${fmt(r.everything_tokens)} tokens per question</span><span class="pill ok">MemoryRouter: ${r.router_tokens[0]}–${r.router_tokens[1]} tokens</span>
        <span class="pill ${r.exact.everything === 4 ? "ok" : "err"}">pasted exact ${r.exact.everything}/4</span><span class="pill ${r.exact.router === 4 ? "ok" : "err"}">router exact ${r.exact.router}/4</span></div>` : ""}
      <div class="card"><table class="lint"><thead><tr><th>question</th><th>asked with</th><th class="num">memory in prompt</th><th class="num">time</th><th>answer</th></tr></thead>
      <tbody>${rows}</tbody></table><p class="muted small">Click an answer to read the reply.</p></div>`;
  }

  function extras() {
    if (!S.score && !S.extras.length) return empty("Where the extra names came from");
    return `<h1>Where the extra names came from</h1>
      <p class="lead">Every name a reply put in a list it does not belong to, looked up in memory with <code>fact list --query "&lt;name&gt;"</code>.
      The memory holds the right date; the prompt that held every fact put the name next to the wrong month.</p>
      ${S.extras.length ? S.extras.map((x) => `<div class="card"><div class="callhead"><h3 style="margin:0">${esc(x.name)}</h3><span class="tok">${esc(x.checkpoint)} · ${esc(x.mode === "everything" ? "everything pasted" : x.mode)} · ${esc(x.question)}</span></div>
        <p>The reply said <span class="mark bad">${esc(x.said)}</span> — stored birthday <span class="mark ok">${esc(x.stored || "none")}</span></p>
        <div class="factlist">${[...x.facts].sort((a, b) => b.startsWith("Contact:") - a.startsWith("Contact:")).slice(0, 4).map((f) => `<div class="memfact ${f.startsWith("Contact:") ? "rule" : ""}"><span>${esc(f)}</span></div>`).join("")}</div></div>`).join("")
      : '<div class="card"><p class="muted">No reply named anyone who does not belong in its list this run. The README reports totals over every run.</p></div>'}`;
  }

  function score() {
    if (!S.score) return unavailable() || empty("The scoreboard");
    const rows = S.score.rows; const max = Math.max(...rows.map((r) => r.everything_tokens), 1);
    return `<h1>The scoreboard</h1>
      <p class="lead">Memory tokens per question at each point, pasted vs attached by MemoryRouter, and how many of the four answers were exact. Model wording varies run to run; the README reports totals over all runs.</p>
      <div class="card"><h3 style="margin-top:0">Memory in every prompt</h3><div class="bars">${rows.map((r) => `
        <div class="barrow"><span>${esc(r.label)} · pasted</span><div class="bar alt" style="width:${Math.max(1, Math.round(100 * r.everything_tokens / max))}%"></div><span class="n">${fmt(r.everything_tokens)}</span></div>
        <div class="barrow"><span>${esc(r.label)} · MemoryRouter</span><div class="bar" style="width:${Math.max(1, Math.round(100 * r.router_tokens[1] / max))}%"></div><span class="n">${r.router_tokens[0]}–${r.router_tokens[1]}</span></div>`).join("")}</div></div>
      <div class="card"><table class="lint"><thead><tr><th>memory</th><th class="num">exact · pasted</th><th class="num">exact · MemoryRouter</th><th>extra names · pasted</th><th>extra names · MemoryRouter</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td>${esc(r.label)} (${r.facts})</td><td class="num">${r.exact.everything}/4</td><td class="num">${r.exact.router}/4</td>
          <td>${r.extra.everything.map((n) => `<span class="mark bad">${esc(n)}</span>`).join("") || '<span class="mark ok">none</span>'}</td>
          <td>${r.extra.router.map((n) => `<span class="mark bad">${esc(n)}</span>`).join("") || '<span class="mark ok">none</span>'}</td></tr>`).join("")}</tbody></table></div>
      ${S.other ? `<div class="card"><h3 style="margin-top:0">Same boundary, another model and API — ${esc(S.other.model)}</h3><p class="muted small">${esc(S.other.question)}</p>
        <div class="reply">${esc(S.other.reply)}</div><div class="marks"><span class="mark ${S.other.ok ? "ok" : "bad"}">${esc(S.other.mark)}</span></div></div>` : ""}
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    const modes = [["all", "all three"], ...MODES];
    return `<h1>Ask — your own question</h1>
      <p class="lead">Ask anything about Maya against the memory as it is now (400 facts after a run), with no memory, with everything pasted, or through MemoryRouter.</p>
      <div class="card"><div class="userpick">${modes.map(([k, l]) => `<button type="button" class="chip ${S.ask.mode === k ? "on" : ""}" data-mode="${k}">${esc(l)}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Who are my climbing partners?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Who are my climbing partners?", "Which ingredients do I avoid at restaurants?", "List everyone whose birthday is in November.", "Which projects are due in March?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    st.querySelectorAll(".userpick .chip[data-mode]").forEach((c) => c.addEventListener("click", () => { S.ask.q = $("#ask-q")?.value || S.ask.q; S.ask.mode = c.dataset.mode; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuestion(c.dataset.q); }));
    }
  }
  function setResult(html) { S.ask.html = html; const box = $("#ask-result"); if (box) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQuestion(q) {
    if (!q) return; S.ask.q = q;
    $("#ask-result").innerHTML = `<span class="pill busy"><span class="spinner"></span> asking…</span>`;
    const r = await api("/api/ask", { question: q, mode: S.ask.mode });
    if (!r) { $("#ask-result") && ($("#ask-result").innerHTML = ""); return; }
    const graded = r.question.key !== "custom";
    setResult(`<p class="muted small">${r.memory} facts in memory</p>` + MODES.filter(([m]) => r.results[m] && !(S.ask.mode !== "all" && S.ask.mode !== "none" && m === "none")).map(([m, label]) => {
      const x = r.results[m];
      return `<div class="callhead"><h3 style="margin:0">${label}</h3><span class="tok">${m === "none" ? "no memory" : fmt(x.memory_tokens) + " memory tokens"} · ${x.seconds.toFixed(1)}s</span></div>
        <div class="reply">${esc(x.reply)}</div>${graded ? `<div class="marks">${mark(x)}</div>` : ""}`;
    }).join(""));
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
  function resetView() { S.memory = {}; S.answers = {}; S.rounds = {}; S.extras = []; S.score = null; S.other = null; S.unavailable = null; S.summary = null; }
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
    else if (k === "memory") S.memory[d.checkpoint] = d.facts;
    else if (k === "answer") (S.answers[d.checkpoint] ||= {})[`${d.question}:${d.mode}`] = d;
    else if (k === "round") S.rounds[d.checkpoint] = d;
    else if (k === "extra") S.extras.push(d);
    else if (k === "scoreboard") S.score = d;
    else if (k === "other_model") S.other = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the memory project, its facts and its boundary are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: your own question, against the 400-fact memory.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "answer", "round", "extra", "scoreboard", "other_model", "summary", "router_unavailable", "done", "error"];
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

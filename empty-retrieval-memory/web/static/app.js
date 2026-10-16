/* Empty retrieval memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "The team's memory", sub: "six runbooks", view: "memory" },
    { n: 4, title: "Can the index find it?", sub: "search --top-k 1", view: "found" },
    { n: 5, title: "What the model is handed", sub: "the checker's copy", view: "attached" },
    { n: 6, title: "Plain prompt", sub: "as most apps ship it", view: "plain" },
    { n: 7, title: "“Do not guess”", sub: "one more sentence", view: "noguess" },
    { n: 8, title: "+ The app sees ∅", sub: "attached tokens, search fallback", view: "observed" },
    { n: 9, title: "Per-retrieval metrics", sub: "what each call attached", view: "metrics" },
    { n: 10, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, found: null, attached: null, asked: [], tallies: {}, metrics: null, summary: null, unavailable: null,
    asks: [], pick: "Who do I wake up if a Sev1 is still burning after half an hour?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["plain", "noguess", "observed"];
  const armLabel = (a) => S.data?.arms?.[a]?.long || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { right: ["✓", "ok"], invented: ["✎", "bad"], missed: ["–", "warn"] };
  const ORDER = ["invented", "missed", "right"];
  const qOf = (id) => (S.data?.questions || []).find((x) => x.id === id) || {};

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "asktool" ? "active" : ""}" data-view="asktool"><span class="n">?</span><span class="t">Ask<small>one question, with its source</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, found, attached, plain: () => arm("plain"), noguess: () => arm("noguess"), observed: () => arm("observed"), metrics, score, asktool: askTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const fromSearch = (a) => (a.source || "").startsWith("search");
  const mark = (a) => { const m = MARK[a.verdict] || ["·", ""]; return `<span class="mark ${m[1]}">${m[0]}${a.empty ? "∅" : ""}${fromSearch(a) ? "↻" : ""}</span>`; };
  const armLine = (t) => {
    if (!t) return "";
    return `${t.right}/${t.answers} right · ${t.invented} invented${t.invented ? ` (${t.empty_invented} with nothing attached)` : ""} · “No record” ${t.no_record}×${t.no_record ? `, ${t.no_record_wrong} of them where a runbook has the answer` : ""}${t.fallback ? ` · ${t.fallback} answered from the search fallback` : ""}`;
  };
  const sourceTag = (a) => fromSearch(a) ? `MemoryRouter attached nothing → the app's search: ${esc(a.source.split(":")[1])}` : a.empty ? "MemoryRouter attached nothing (∅)" : `MemoryRouter attached ${fmt(a.attached)} tokens`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.runbooks)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-erm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The on-call assistant says “No record”, and you cannot tell whether that is true.</h1>
      <p class="lead">Larkspur Pay (fictional) keeps its platform runbooks in shared memory, and an on-call assistant answers from them through MemoryRouter: an ordinary model call
      with <code>--boundary</code> added. Some calls come back with nothing attached, and the reply does not say so. With a plain prompt the model then answers from nowhere.
      The usual fix — “If you do not know, reply 'No record.' Do not guess.” — stops most of that, but a retrieval miss and a question no runbook covers now get the same reply.
      This demo asks ten questions three ways, and in the third the app watches every call's attached tokens: on ∅ it searches the runbooks itself and says where the answer came from.
      It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 140 calls per run (gpt-4o-mini, plus 30 checker calls to claude-haiku-4-5).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-observability-for-production-agents" target="_blank" rel="noopener">memorylake.ai › Memory observability for production agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>One project, one boundary</h1>
      <p class="lead">The six runbooks go into one project, and a boundary covers it. Every answer is <code>router chat … --boundary</code>: MemoryRouter picks what to attach,
      call by call, and the reply never says what it picked.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>runbooks project</dt><dd class="mono small">${esc(w?.project?.runbooks || "pending")}</dd>
        <dt>boundary</dt><dd class="mono small"><b>${esc(w?.boundary?.runbooks || "pending")}</b></dd></dl></div>
        <div class="card"><h3 style="margin-top:0">The questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)} <span class="pill">${esc(q.kind)}</span></p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The team's memory") : empty("The team's memory");
    const rs = S.data?.runbooks || [];
    return `<h1>The team's memory — six runbooks</h1>
      <p class="lead">${esc(S.data?.company)} ${esc(S.data?.team)}: six short Markdown runbooks, uploaded to the Library and imported into the runbooks project.
      Eight of the ten questions are answered by one of them; two are not covered by any.</p>
      <div class="grid2">${rs.map((r) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(r.title)}</h3><span class="mono small muted">${esc(r.file)}</span></div>
        <p class="small" style="margin:6px 0 0">${esc(r.first)}</p></div>`).join("")}</div>`;
  }

  function found() {
    const h = "Can the index find the answer?";
    if (!S.found) return running() ? waiting(h) : empty(h);
    return `<h1>${h} — <code>search --top-k 1</code></h1>
      <p class="lead">Every question, as typed, through <code>search --types document --top-k 1</code> on the runbooks project. For the ${S.found.of} questions a runbook answers,
      the top page is that runbook <b>${S.found.right}</b> times. The answers are in memory and the index finds them.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>top 1</th><th></th></tr></thead><tbody>
        ${S.found.rows.map((r) => { const q = qOf(r.q); return `<tr><td><b>${esc(r.q)}</b> ${esc(q.say)}</td><td class="mono small">${esc(r.file || "nothing")}</td><td>${q.runbook ? (r.right ? '<span class="mark ok">the runbook with the answer</span>' : `<span class="mark bad">not it (${esc(q.runbook)}.md)</span>`) : '<span class="muted small">closest page; no runbook covers this</span>'}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function attached() {
    const h = "What MemoryRouter hands the model";
    if (!S.attached) return unavailable() || (running() ? waiting(h) : empty(h));
    return `<h1>${h}</h1>
      <p class="lead">For each question, ${esc(S.data?.check_model)} is asked through the same boundary to copy out everything above the question — its own call, so what it is handed
      can differ from the answer calls. ${S.attached.copies} copies: <b>nothing attached in ${S.attached.empty}</b>; for questions a runbook answers, that runbook was attached in
      <b>${S.attached.right} of ${S.attached.have}</b>.</p>
      ${(S.attached.checks || []).map((c) => { const q = qOf(c.q); return `<div class="card"><h3 style="margin-top:0">${esc(c.q)} <span class="small muted">${esc(q.say)}</span></h3>
        ${(c.copies || []).map((x, n) => `<p class="small muted" style="margin:8px 0 2px">copy ${n + 1}</p>` + (x.readable ? x.files.map((f) => `<div class="idx ${f === (c.runbook + ".md") ? "ok" : ""}"><span class="tag">${esc(f)}</span>${f === (c.runbook + ".md") ? " has the answer" : ""}</div>`).join("")
          : x.nothing ? '<div class="idx bad">nothing attached (∅)</div>'
          : `<div class="idx">wrote NONE${x.said ? `: “${esc(x.said)}”` : ""}</div>`)).join("")}</div>`; }).join("")}`;
  }

  function arm(name) {
    const h = `Ask the on-call assistant — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const sys = name === "observed" ? "" : `<div class="card"><h3 style="margin-top:0">System prompt, as the app sends it on every call</h3><pre class="mono small wrap">${esc(S.data?.system?.[name])}</pre></div>`;
    return `<h1>${h}${t ? ` — ${t.right}/${t.answers} right` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ✎ invented · – “No record” although a runbook has the answer · ∅ MemoryRouter attached nothing · ↻ answered after the app's own search.</p>
      ${sys}
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict))[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">runbook: ${r.runbook ? `<span class="mono">${esc(r.runbook)}.md</span>` : "none covers it"} · answer: ${esc(r.fact)}</div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map(mark).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick)}${name === "observed" ? ` · <span class="muted">${sourceTag(pick)}</span>` : ""}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}</div>` : ""}`;
  }

  function metrics() {
    const h = "Per-retrieval metrics";
    if (!S.metrics) return running() ? waiting(h) : empty(h);
    const m = S.metrics;
    return `<h1>${h} — what each MemoryRouter call attached</h1>
      <p class="lead">Every boundary call in steps 6–8. Attached tokens = the call's prompt tokens minus the same call without a boundary — nothing in the reply says this.
      <b>${m.empty} of ${m.calls}</b> calls attached nothing.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>calls</th><th>attached</th><th>∅</th><th>median tokens</th></tr></thead><tbody>
        ${m.rows.map((r) => `<tr><td><b>${esc(r.q)}</b> ${esc(qOf(r.q).say)}</td><td>${r.calls}</td><td>${r.hits}</td><td>${r.empty ? `<span class="mark bad">${r.empty}</span>` : 0}</td><td>${r.tokens || "—"}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    const ng = t.noguess;
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>setup</th><th>right</th><th>✎ invented (∅)</th><th>“No record”</th><th>– of them, a runbook has it</th><th>↻ search fallback</th></tr></thead><tbody>
        ${ARMS.map((a) => { const u = t[a];
          return `<tr><td style="white-space:nowrap"><b>${esc(armShort(a))}</b></td>${u ? `<td>${u.right}/${u.answers}</td><td>${u.invented} (${u.empty_invented})</td><td>${u.no_record}</td><td>${u.no_record_wrong}</td><td>${u.fallback}</td>` : "<td>–</td><td></td><td></td><td></td><td></td>"}</tr>`; }).join("")}
      </tbody></table>
      ${ng && ng.no_record ? `<p class="small" style="margin-bottom:0">With “Do not guess”, the assistant said “No record” <b>${ng.no_record}</b> times; <b>${ng.no_record_wrong}</b> of those were for a question a runbook answers. The reply reads the same either way.</p>` : ""}</div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant told the engineer instead of the runbook's answer</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function askTool() {
    const ready = !!S.world;
    const all = (S.data?.questions || []).map((q) => q.say);
    const last = S.asks[S.asks.length - 1];
    return `<h1>Ask</h1>
      <p class="lead">One question the observed way: a baseline call without a boundary, the boundary call and its attached tokens, and — if MemoryRouter attached nothing — the app's
      own <code>search --top-k 1</code> and a second call with that page. The answer says where it came from.</p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${all.map((a) => `<button class="chip ${S.pick === a ? "on" : ""}" data-pick="${esc(a)}">${esc(a)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><input id="ask-q" value="${esc(S.pick)}" style="flex:1"><button class="btn primary" data-act="ask" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last ? `<div class="card ${fromSearch(last) ? "warnc" : "hl"}"><h3 style="margin-top:0">“${esc(last.say)}”</h3>
        <div class="idx"><span class="tag">baseline</span>${fmt(last.baseline)} prompt tokens without a boundary</div>
        <div class="idx ${fromSearch(last) ? "bad" : "ok"}"><span class="tag">source</span>${fromSearch(last) ? `MemoryRouter attached nothing → the app searched: <b>${esc(last.source.split(":")[1])}</b>` : `MemoryRouter attached ${fmt(last.attached)} tokens`}</div>
        <div class="chat"><div class="msg bot"><span class="who">answer</span>${esc(last.reply)}</div></div></div>` : ""}`;
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
    st.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => { S.pick = b.dataset.pick; renderStage(); }));
    st.querySelector('[data-act="ask"]')?.addEventListener("click", async (e) => {
      S.pick = $("#ask-q").value.trim() || S.pick;
      e.target.disabled = true; e.target.textContent = "Asking…";
      const r = await api("/api/ask", { question: S.pick });
      if (r) S.asks.push(r);
      if (S.view === "asktool") renderStage();
    });
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
  function resetView() { S.memory = null; S.found = null; S.metrics = null; S.attached = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary, the runbooks project and its Library files?")) return;
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
    else if (k === "found") S.found = d;
    else if (k === "metrics") S.metrics = d;
    else if (k === "attached") S.attached = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the runbooks project and the Library files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: one question, with where its answer came from.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "found", "attached", "asked", "tally", "metrics", "summary", "router_unavailable", "one", "done", "error"];
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
    show(state.status === "running" ? S.view : S.summary ? "score" : "connect");
    subscribe();
  })();
})();

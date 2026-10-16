/* Rule application memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "The trial's memory", sub: "six protocol pages", view: "memory" },
    { n: 4, title: "Can the index find the rule?", sub: "search --top-k 1", view: "found" },
    { n: 5, title: "MemoryRouter attaches", sub: "router chat --boundary", view: "boundary" },
    { n: 6, title: "The right page, pasted in", sub: "retrieval taken out", view: "page" },
    { n: 7, title: "Quote the rule, then work it", sub: "the app checks the quote", view: "work" },
    { n: 8, title: "Scoreboard", sub: "all answers this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, found: null, asked: [], tallies: {}, summary: null, unavailable: null,
    asks: [], pick: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["boundary", "page", "work"];
  const KINDS = ["rule", "applied", "none"];
  const armLabel = (a) => S.data?.arms?.[a]?.long || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const kindLabel = (k) => S.data?.kinds?.[k] || k;
  const MARK = { right: ["✓", "ok"], wrong: ["✗", "bad"], invented: ["✎", "bad"], missed: ["–", "warn"] };
  const ORDER = ["wrong", "invented", "missed", "right"];
  const qOf = (id) => (S.data?.questions || []).find((x) => x.id === id) || {};

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "asktool" ? "active" : ""}" data-view="asktool"><span class="n">?</span><span class="t">Ask<small>one question, worked out</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, found, boundary: () => arm("boundary"), page: () => arm("page"), work: () => arm("work"), score, asktool: askTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (a) => { const m = MARK[a.verdict] || ["·", ""]; return `<span class="mark ${m[1]}">${m[0]}${a.empty ? "∅" : ""}</span>`; };
  const kindLine = (u) => {
    if (!u || !u.answers) return "—";
    let s = `${u.right}/${u.answers} ✓`;
    if (u.wrong) s += ` · ${u.wrong} ✗`;
    if (u.missed) s += ` · ${u.missed} –`;
    if (u.invented) s += ` · ${u.invented} ✎`;
    return s;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.protocol)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-ram")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The right protocol page is in front of the model. Applying it is where it goes wrong.</h1>
      <p class="lead">Corvane Therapeutics (fictional) keeps the protocol of its Phase II trial in shared memory, and a trial assistant answers the study team from it through MemoryRouter.
      Ask for a rule and it is right. Ask it to apply that rule to a participant — “93 kg this morning, what dose today?” — and it answers “180 mg” in the same confident line,
      with the page that says “never more than 160 mg” pasted into its prompt. This demo asks fifteen questions three ways: MemoryRouter attaching what it finds, the right page
      pasted in by the app, and the same page with the rule quoted word for word and worked one step per line. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 150 gpt-4o-mini calls per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-pharmaceutical-research" target="_blank" rel="noopener">memorylake.ai › AI memory for pharmaceutical research</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>One project, one boundary</h1>
      <p class="lead">The six protocol pages go into one project, and a boundary covers it for step 5 (<code>router chat … --boundary</code>). Steps 6 and 7 use no boundary:
      the app pastes the page <code>search</code> found into the prompt itself.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>protocol project</dt><dd class="mono small">${esc(w?.project?.protocol || "pending")}</dd>
        <dt>boundary</dt><dd class="mono small"><b>${esc(w?.boundary?.protocol || "pending")}</b></dd></dl></div>
        <div class="card"><h3 style="margin-top:0">The questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)} <span class="pill">${esc(kindLabel(q.kind))}</span></p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The trial's memory") : empty("The trial's memory");
    const ps = S.data?.pages || [];
    return `<h1>The trial's memory — six protocol pages</h1>
      <p class="lead">${esc(S.data?.company)}, protocol ${esc(S.data?.study)}: six short Markdown pages, uploaded to the Library and imported into the protocol project.
      Thirteen of the fifteen questions are answered by one of them; two are not covered at all.</p>
      <div class="grid2">${ps.map((p) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(p.title)}</h3><span class="mono small muted">${esc(p.file)}</span></div>
        <p class="small" style="margin:6px 0 0">${esc(p.first)}</p></div>`).join("")}</div>`;
  }

  function found() {
    const h = "Can the index find the rule?";
    if (!S.found) return running() ? waiting(h) : empty(h);
    return `<h1>${h} — <code>search --top-k 1</code></h1>
      <p class="lead">Every question, as typed, through <code>search --types document --top-k 1</code> on the protocol project. For the ${S.found.of} questions the protocol covers,
      the top page is the one with the rule <b>${S.found.right}</b> times. The app keeps each question's top page: steps 6 and 7 paste it into the prompt.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>top 1</th><th></th></tr></thead><tbody>
        ${S.found.rows.map((r) => { const q = qOf(r.q); return `<tr><td><b>${esc(r.q)}</b> ${esc(q.say)}</td><td class="mono small">${esc(r.file || "nothing")}</td><td>${q.page ? (r.right ? '<span class="mark ok">the page with the rule</span>' : `<span class="mark bad">not it (${esc(q.page)}.md)</span>`) : '<span class="muted small">closest page; not covered</span>'}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function arm(name) {
    const h = `Ask the trial assistant — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const key = name === "work" ? "work" : "answer";
    const sys = `<div class="card"><h3 style="margin-top:0">System prompt${name === "boundary" ? "" : `, then “${esc(S.data?.page_header)}” and the page step 4 found`}</h3><pre class="mono small wrap">${esc(S.data?.system?.[key])}</pre></div>`;
    const groups = ["applied", "rule", "none"].map((k) => {
      const rs = rows.filter((r) => r.qkind === k);
      if (!rs.length) return "";
      return `<h2>${esc(kindLabel(k))}${t ? ` <span class="small muted">${kindLine(t[k])}</span>` : ""}</h2>` + rs.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict))[0];
        const quotes = r.answers.filter((a) => a.quote === "verbatim").length;
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">protocol: ${esc(r.fact)}</div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map(mark).join(" ")}${name === "work" && r.page ? ` <span class="muted small">· quoted rule word for word on the page: ${quotes}/${r.answers.length}</span>` : ""}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""} ${name === "work" ? "pre" : ""}"><span class="who">answer ${mark(pick)}</span>${esc(name === "work" ? pick.reply : pick.clause)}</div></div>` : ""}</div>`;
      }).join("");
    }).join("");
    return `<h1>${h}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ✗ wrong · – “No record” although the protocol has it · ✎ answered what the protocol does not say${name === "boundary" ? " · ∅ MemoryRouter attached nothing" : ""}.</p>
      ${sys}${groups}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    const p = t.page, w = t.work;
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>setup</th>${KINDS.map((k) => `<th>${esc(kindLabel(k))}</th>`).join("")}</tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td style="white-space:nowrap"><b>${esc(armShort(a))}</b></td>${KINDS.map((k) => `<td>${t[a] ? kindLine(t[a][k]) : "–"}</td>`).join("")}</tr>`).join("")}
      </tbody></table>
      ${p ? `<p class="small">With the right page in the prompt, the rule itself: <b>${p.rule.right}/${p.rule.answers}</b>. The same rules applied to a participant: <b>${p.applied.right}/${p.applied.answers}</b>.</p>` : ""}
      ${w ? `<p class="small" style="margin-bottom:0">Quote the rule, then work it: applied <b>${w.applied.right}/${w.applied.answers}</b>; the quoted rule was on the page word for word in ${w.applied.quoted} of ${w.applied.answers}.</p>` : ""}</div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant told the study team instead of the protocol's answer</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function askTool() {
    const ready = !!S.world;
    const all = (S.data?.questions || []).filter((q) => q.kind === "applied").map((q) => q.say);
    if (!S.pick) S.pick = S.data?.example || all[0] || "";
    const last = S.asks[S.asks.length - 1];
    return `<h1>Ask</h1>
      <p class="lead">One question the third way: <code>search --top-k 1</code> finds the page, the model quotes the rule word for word and works it one step per line,
      and the app checks the quote against the page.</p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${all.map((a) => `<button class="chip ${S.pick === a ? "on" : ""}" data-pick="${esc(a)}">${esc(a)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><input id="ask-q" value="${esc(S.pick)}" style="flex:1"><button class="btn primary" data-act="ask" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last ? `<div class="card ${last.quote === "verbatim" ? "hl" : "warnc"}"><h3 style="margin-top:0">“${esc(last.say)}”</h3>
        <div class="idx"><span class="tag">page</span>${esc(last.file || "nothing")}</div>
        <div class="idx ${last.quote === "verbatim" ? "ok" : "bad"}"><span class="tag">quoted rule</span>${last.quote === "verbatim" ? "word for word on the page" : esc(last.quote)}</div>
        <div class="chat"><div class="msg bot pre"><span class="who">answer</span>${esc(last.reply)}</div></div></div>` : ""}`;
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
  function resetView() { S.memory = null; S.found = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary, the protocol project and its Library files?")) return;
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
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the protocol project and the Library files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: one question, worked out step by step.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const EVENTS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "found", "asked", "tally", "summary", "router_unavailable", "one", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    EVENTS.forEach((kind) => es.addEventListener(kind, (e) => { if (e.data) onEvent(JSON.parse(e.data)); }));
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
      EVENTS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
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

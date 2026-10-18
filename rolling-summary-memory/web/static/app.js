/* Rolling summary memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Two memories", sub: "summary buffer · MemoryLake", view: "setup" },
    { n: 3, title: "Twelve sessions", sub: "three weeks of platform work", view: "sessions" },
    { n: 4, title: "What each holds", sub: "summary size vs values kept", view: "holds" },
    { n: 5, title: "Ask Ferry", sub: "18 values from the first week", view: "ask" },
    { n: 6, title: "Score", sub: "right · not recorded · tokens", view: "score" },
  ];
  const fresh = () => ({ sessionsSeen: false, live: {}, trace: null, census: null, rows: [], example: null, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "When is the queue cutover?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const QS = () => S.data?.questions || [];
  const SES = () => S.data?.sessions || [];
  const NCH = () => S.data?.chains || 3;
  const day = (iso) => new Date(iso).toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" });

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through each summary and MemoryLake</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, sessions, holds, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], old: ["↺", "warn", "the value before it changed"], unknown: ["?", "warn", "says it is not recorded"], made: ["✎", "bad", "a value memory does not hold"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="MemoryRouter attached nothing">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const armName = (r) => r.arm === "A" ? `A${r.run} · summary chain ${r.run}` : "B · MemoryLake";

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 8 minutes (twelve sessions extracted one by one, then ~150 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-rsm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Stop summarizing agent history. Retrieve it instead.</h1>
      <p class="lead">${esc(d.person?.display || "Dana Okafor")} (a fictional platform engineer) works with Ferry, the team's AI assistant, over twelve short sessions in three weeks.
      The same sessions go into two memories: <b>A</b> a summary buffer — after every session the app asks the model to fold it into the running summary, “under ${esc(d.budget_words || 150)} words”, in ${NCH()} independent chains · <b>B</b> MemoryLake — each session a conversation, extracted into facts, read through MemoryRouter.
      Then Ferry is asked for 18 specific values from the first week. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Model calls are billed</b> to your MemoryLake balance: about 150 calls on gpt-4o-mini per run (the summaries are model calls too).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/stop-summarizing-agent-history-retrieve-it-instead" target="_blank" rel="noopener">memorylake.ai › Stop summarizing agent history</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Two memories for Ferry</h1>
      <p class="lead">The same twelve sessions, kept two ways. Both are read by the same model (${esc(d.model)}, temperature 0) with the same system prompt — which tells it to say “not recorded” rather than guess.</p>
      <div class="grid2">
        <div class="card"><h3 style="margin-top:0">A · summary buffer, ${NCH()} chains</h3>
          <p class="small">No MemoryLake objects: the app keeps the summary itself. After every session it calls the model (<code>router chat</code>, no boundary) with the current summary and the new session:</p>
          <p class="small quote">“${esc(d.summarizer)}”</p>
          <p class="small muted" style="margin-bottom:0">Three chains, as three instances of the same app would keep them. At question time the summary goes into the system prompt.</p></div>
        <div class="card"><h3 style="margin-top:0">B · MemoryLake</h3>
          <p class="small">A project “${esc(d.project_name)}”, Dana and Ferry as actors, each session a DIRECT conversation extracted before the next one starts (later sessions revise earlier values). A boundary — the project plus Dana — is what Ferry reads through <code>router chat --boundary</code>.</p>
          <dl class="kv small"><dt>project</dt><dd class="mono">${esc(w?.project || "pending")}</dd><dt>boundary</dt><dd class="mono">${esc(w?.boundary || "pending")}</dd></dl></div></div>
      <div class="card"><h3 style="margin-top:0">Ferry's system prompt</h3><p class="small quote" style="margin-bottom:0">“${esc(d.system)}”</p></div>`;
  }

  function sessions() {
    const d = S.data;
    if (!d || (!S.sessionsSeen && !S.world)) return running() ? waiting("Twelve sessions") : empty("Twelve sessions");
    const asked = new Set(QS().map((q) => q.session));
    return `<h1>Twelve sessions, three weeks</h1>
      <p class="lead">The first six carry the values Ferry is asked about later (highlighted). The last six are incident reviews, capacity, on-call and cost — dense with other numbers. Two of them change earlier values: the pool cap 40 → 64, the canary hold 20 → 30 minutes.</p>
      ${Object.keys(S.live).length && !S.trace ? `<div class="card"><h3 style="margin-top:0">Live — the summary chains fold in each session while MemoryLake extracts it</h3>${traceTable()}</div>` : ""}
      <div class="grid2">${d.sessions.map((s) => `<div class="card ${asked.has(s.id) ? "hl-card" : ""}"><div class="row spread"><b>${esc(s.topic)}</b><span class="mono small muted">${esc(day(s.at))}</span></div>
        <div class="chat">${s.turns.filter((t) => t.who === "dana").map((t) => `<div class="msg user ${/changed|raised|from now on/i.test(t.text) ? "promise" : ""}"><span class="who">Dana</span>${esc(t.text)}</div>`).join("")}</div>
        ${asked.has(s.id) ? '<div class="small muted">asked about later</div>' : ""}</div>`).join("")}</div>`;
  }

  function traceTable() {
    const nq = QS().length;
    const chains = S.trace ? S.trace.chains : null;
    const cell = (c, i, s) => {
      const x = chains ? chains[c].steps[i] : (S.live[c] || {})[s.id];
      if (!x) return '<td class="num muted">·</td>';
      const lost = x.lost ? x.lost.length : 0, drop = chains && x.dropped;
      return `<td class="num ${drop ? "drop" : ""}">${fmt(x.words)} w · <b>${x.kept}</b>/${nq}${drop ? ` <span class="bad-ink">↓${lost}</span>` : ""}</td>`;
    };
    const bWords = (S.data?.budget_words) || 150;
    return `<table class="lint plain"><thead><tr><th>after session</th>${[...Array(NCH()).keys()].map((c) => `<th class="num">chain ${c + 1}</th>`).join("")}<th class="num">B extracted</th></tr></thead><tbody>
      ${SES().map((s, i) => `<tr><td class="small">${esc(day(s.at))} · ${esc(s.topic)}</td>${[...Array(NCH()).keys()].map((c) => cell(c, i, s)).join("")}<td class="num">${(S.live[-1] || {})[s.id] || S.census ? "✓" : "·"}</td></tr>`).join("")}
    </tbody></table><p class="small muted" style="margin-bottom:0">words · values kept of ${nq} · budget ${bWords} words · <span class="bad-ink">↓N</span> one compaction dropped N values the summary held a moment before</p>`;
  }

  function holds() {
    if (!S.trace && !Object.keys(S.live).length) return running() ? waiting("What each memory holds") : empty("What each memory holds");
    const c = S.census, chains = S.trace?.chains;
    const sym = { ok: "✓", old: "↺", "": "✗" };
    const lostNotes = chains ? chains.flatMap((ch) => ch.steps.filter((x) => x.dropped).map((x) =>
      `<li>chain ${ch.chain}, folding in ${esc((SES().find((s) => s.id === x.session) || {}).topic)}: lost ${x.lost.map((k) => esc((QS().find((q) => q.id === k) || {}).what)).join(", ")}</li>`)) : [];
    const head = chains ? `Summaries end with ${chains.map((ch) => `${ch.steps[ch.steps.length - 1].kept}`).join(" · ")} of ${QS().length} values${c ? ` — MemoryLake holds ${c.kept}` : ""}` : "What each memory holds";
    return `<h1>${head}</h1>
      <p class="lead">Each summary chain, after every session: its length, and how many of the 18 values it still carries. A summary that obeys nobody's budget keeps growing; a compaction that does trim drops whole sessions at once.</p>
      <div class="card">${traceTable()}${lostNotes.length ? `<ul class="small" style="margin-bottom:0">${lostNotes.join("")}</ul>` : ""}</div>
      ${c && chains ? `<div class="card"><h3 style="margin-top:0">Per value — the final summaries and the facts (${c.facts} facts)</h3><table class="lint plain"><thead><tr><th>value</th>${chains.map((ch) => `<th class="num">A${ch.chain}</th>`).join("")}<th class="num">B</th></tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.what)}${q.old_re ? ' <span class="muted">· changed later</span>' : ""}</td>${chains.map((ch) => { const v = ch.steps[ch.steps.length - 1].holds[q.id];
          return `<td class="num ${v === "ok" ? "" : "bad-ink"}">${sym[v]}</td>`; }).join("")}<td class="num ${c.holds[q.id] === "ok" ? "" : "bad-ink"}">${sym[c.holds[q.id]]}</td></tr>`).join("")}
      </tbody></table><p class="small muted" style="margin-bottom:0">✓ holds the value · ↺ only the value before it changed · ✗ not there</p></div>` : ""}`;
  }

  function askView() {
    if (!S.rows.length) return unavailable() || (running() ? waiting("Ask Ferry") : empty("Ask Ferry"));
    const ex = S.example;
    const chains = [...Array(NCH()).keys()].map((c) => c + 1);
    return `<h1>Ask Ferry — 18 values from the first week</h1>
      <p class="lead">Each question once per summary chain (the summary in the system prompt) and ${S.data?.b_runs || 2}× through MemoryLake (<code>router chat --boundary</code>), temperature 0. ∅ = MemoryRouter attached nothing (same prompt tokens as a call without a boundary).</p>
      ${ex ? `<div class="card"><h3 style="margin-top:0">“${esc(ex.ask)}” <span class="muted small">— Dana said Thursday 22 October, 07:30 UTC: tomorrow</span></h3>
        ${ex.rows.filter((r) => r.arm === "A" || r.run === 1).map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : "wrong"}"><span class="who">${esc(armName(r))} · ${markOf(r)}</span>${esc(r.reply)}</div>`).join("")}</div>` : ""}
      <div class="card"><table class="lint plain"><thead><tr><th>question</th>${chains.map((c) => `<th>A${c}</th>`).join("")}<th>B</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows.filter((r) => r.item === q.id);
          const cellA = (c) => { const r = rs.find((x) => x.arm === "A" && x.run === c); return r ? `<td><div class="cell-ans">${markOf(r)}</div><div class="small muted">${esc((r.reply || "").slice(0, 80))}</div></td>` : "<td></td>"; };
          const bs = rs.filter((x) => x.arm === "B"), b0 = bs.find((x) => x.reply) || {};
          return `<tr><td class="small"><b>${esc(q.what)}</b><br><span class="muted">${esc(q.ask)}</span></td>${chains.map(cellA).join("")}<td><div class="cell-ans">${bs.map(markOf).join(" ")}</div><div class="small muted">${esc((b0.reply || "").slice(0, 80))}</div></td></tr>`; }).join("")}
      </tbody></table><p class="small muted" style="margin-bottom:0">✓ right · ↺ the value before it changed · ? says it is not recorded · ✎ a value memory does not hold · ∅ MemoryRouter attached nothing</p></div>`;
  }

  function score() {
    if (!S.score || !S.census) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, nq = QS().length, B = sc.arms.B, c = S.census;
    const bar = (k, m, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, m)}%"></span></div>`;
    const row = (label, kept, t, extra) => `<tr><td><b>${label}</b>${extra || ""}</td><td><b>${kept}/${nq}</b>${bar(kept, nq, "g1")}</td>
      <td><b>${t.ok}/${t.n}</b> <span class="muted">${pct(t.ok, t.n)}%</span>${bar(t.ok, t.n, "g2")}</td><td class="num">${t.old}</td><td class="num">${t.unknown}</td><td class="num">${t.made}</td><td class="num">${t.empty || 0}</td><td class="num">${fmt(t.prompt_tokens)}</td></tr>`;
    const Aok = sc.chains.reduce((s, x) => s + x.ok, 0), An = sc.chains.reduce((s, x) => s + x.n, 0);
    return `<h1>Right answers: summaries ${Aok}/${An} · MemoryLake ${B.ok}/${B.n}</h1>
      <p class="lead">This run: ${sc.dropped} of ${sc.chains.length} summary chains dropped 3+ values in a single compaction${sc.chains.every((x) => x.words > (S.data?.budget_words || 150)) ? `, and every summary ended past its ${esc(S.data?.budget_words)}-word budget` : ""}.
        ${(() => { const full = sc.chains.filter((x) => x.kept === nq); return full.length ? `The summar${full.length > 1 ? "ies" : "y"} that kept every value (${full.map((x) => "A" + x.chain).join(", ")}) cost ${full.map((x) => fmt(x.prompt_tokens)).join(", ")} prompt tokens per question, against ${fmt(B.prompt_tokens)} through MemoryLake.` : ""; })()}
        One run varies; the README adds up several.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>values held</th><th>answers right</th><th class="num">old value</th><th class="num">not recorded</th><th class="num">other</th><th class="num">∅</th><th class="num">prompt tokens / question</th></tr></thead><tbody>
        ${sc.chains.map((x) => row(`A${x.chain} summary`, x.kept, x, ` <span class="muted small">${fmt(x.words)} words${x.dropped.length ? ` · dropped at ${x.dropped.join(", ")}` : ""}</span>`)).join("")}
        ${row("B MemoryLake", c.kept, B, ` <span class="muted small">${c.facts} facts</span>`)}</tbody></table>
        <p class="small muted" style="margin-bottom:0">prompt tokens: per question, on average — A carries its whole summary every time, B what MemoryRouter attaches for that question</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run (summaries included): ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through each summary and through MemoryLake</h1>
      <p class="lead">Any question to Ferry, once through each summary chain from the last run and once through MemoryLake. Graded when it is one of the 18.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Dana → Ferry</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(armName(r))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}`).join("")}</div></div>`;
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
    st.querySelector("#t-q")?.addEventListener("input", (e) => { S.tq = e.target.value; });
    st.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { S.tq = b.dataset.q; renderStage(); }));
    st.querySelector('[data-act="try"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Asking…";
      const r = await api("/api/ask", { question: S.tq });
      if (r) S.tries.push({ question: S.tq, rows: r.rows });
      if (S.view === "try") renderStage();
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
  function resetView() { Object.assign(S, fresh()); }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary, twelve conversations, two actors (their facts first) and the project?")) return;
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
      if (d.chain !== undefined) { (S.live[d.chain] = S.live[d.chain] || {})[d.session] = d.chain < 0 ? true : { words: d.words, kept: d.kept }; }
      if (S.caughtUp && (S.view === "holds" || S.view === "sessions")) renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "sessions") S.sessionsSeen = true;
    else if (k === "trace") S.trace = d;
    else if (k === "example") S.example = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows.push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundary, conversations, actors (facts first) and the project are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to Ferry, through each summary and through MemoryLake.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "sessions", "trace", "census", "ask_row", "example",
    "score", "summary", "router_unavailable", "answered", "memory_view", "done", "error"];
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

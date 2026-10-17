/* Presupposed claim memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["0", "A", "B", "C"];
  const MEM = ["A", "B", "C"];
  const COPY = { A: "asked", B: "answers", C: "logged" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "Week 1", sub: "six threads, three claims", view: "week1" },
    { n: 4, title: "Finding log", sub: "C only: one fact per finding", view: "ledger" },
    { n: 5, title: "Weeks 2–3", sub: "questions that assume the claims", view: "week2" },
    { n: 6, title: "What memory holds", sub: "still a claim, or a fact?", view: "census" },
    { n: 7, title: "Ask the assistant", sub: "no memory + three memories", view: "ask" },
    { n: 8, title: "Score", sub: "per question and per memory", view: "score" },
  ];
  const fresh = () => ({ seen: {}, read: {}, ledgerSeen: false, census: null, rows: { 0: [], A: [], B: [], C: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Is Pellmont's Ohio plant bigger than Brisa Cycle's Leipzig plant?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const CLAIMS = () => S.data?.claims || [];
  const QS = () => S.data?.questions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;
  const CNAME = (a) => (S.data?.copy_names || {})[COPY[a]] || "";
  const cap = (k) => k ? k[0].toUpperCase() + k.slice(1) : "";

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>no memory, A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, week1: () => weekView(1), ledger: ledgerView, week2: () => weekView(2), census: censusView, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], taken: ["⚑", "bad", "states an unproven claim as fact"],
    wrong: ["✗", "bad", "wrong"], unknown: ["?", "warn", "says it does not know"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const live = rows.filter((r) => r.grade && !r.empty), c = (g) => live.filter((r) => r.grade === g).length;
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c("ok"), taken: c("taken"), wrong: c("wrong"), unknown: c("unknown") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `right <b>${t.ok}</b>/${t.n} · ⚑ <b class="${t.taken ? "bad-ink" : ""}">${t.taken}</b> · ✗ <b>${t.wrong}</b> · ? <b>${t.unknown}</b>${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const FIG = /(60,000 tonnes?|60,000|91 percent|410 million dollars|owns Lithra Recycling|18,000 tonnes?|18,000|64 percent)/g;
  const hiFig = (t) => esc(t).replace(FIG, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.asked)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 8 minutes (twelve research threads in each of three copies, the last six one at a time, then ~100 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-pcl")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“Now that Halden owns Lithra…” — a research question that assumes a rumour, saved as asked.</h1>
      <p class="lead">${esc(d.org || "Tarnwell Research")} (fictional): ${esc(d.analyst?.display || "Mira Okafor")} covers battery recycling and searches in Perplexity; every thread is saved to MemoryLake when it ends. In week 1 she finds three things that are not established — a rumoured deal, a company's own recovery figure, a plant expansion that is only a plan — and memory keeps them as claims. In the weeks after, her questions take them for granted, and the answers only deal with the other half of the question.
      Three memories of the same twelve threads: <b>A</b> every thread as asked and answered · <b>B</b> the answers only · <b>C</b> as A, plus the app's finding log (one fact per finding, with its status) — and <b>0</b>, the same questions with no memory. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 100 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/add-persistent-memory-to-perplexity" target="_blank" rel="noopener">memorylake.ai › Make your Perplexity research compound over time</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    const ppl = d.analyst ? [d.analyst, d.assistant] : [];
    return `<h1>Three projects, one boundary each</h1>
      <p class="lead">The same twelve threads go into three projects with the same plain name (a project's name is context for extraction; the arm lives only in the custom id). Each project has its own analyst and assistant actors, so the three memories share nothing.</p>
      <div class="card"><dl class="kv">${MEM.map((a) => `<dt>${a} · ${esc(CNAME(a))}</dt><dd class="mono small">${esc(w?.projects?.[COPY[a]] || "pending")}</dd>`).join("")}
        ${ppl.map((p) => `<dt>${esc(p.display)} · ${esc(p.role)}</dt><dd class="mono small">${MEM.map((a) => esc(w?.actors?.[`${COPY[a]}-${p.key}`] || "pending")).join("<br>")}</dd>`).join("")}</dl></div>`;
  }

  function weekView(week) {
    const title = week === 1 ? "Week 1 — six threads" : "Weeks 2 and 3 — six more threads";
    if (!S.data || (!S.seen[week] && !S.world)) return running() ? waiting(title) : empty(title);
    const d = S.data, rows = d.sessions.filter((s) => (s.week === 1) === (week === 1));
    const claimOf = (k) => (d.claims.find((c) => c.id === k) || {}).label;
    const lead = week === 1
      ? "Each Perplexity thread is saved when it ends: A and C save the question and the answer, B only the answer. Three of the six findings are not established, and the answers say so."
      : "Saved one at a time, each read before the next, as the weeks went. Every question takes a week-1 claim for granted; the answer deals with the other half of the question and never says the assumption is wrong.";
    const read = (s) => MEM.map((a) => (S.read[COPY[a]] || {})[s.id] ? `<span class="lab lab-ok">${a} read</span>` : "").join(" ");
    return `<h1>${title}</h1><p class="lead">${lead}</p>
      ${rows.map((s) => `<div class="card"><div class="row spread"><h3 style="margin:0">Thread ${esc(s.id.slice(1))} · ${esc(s.ts.slice(0, 10))}</h3>
        ${s.presupposes ? `<span class="lab lab-bad">assumes: ${esc(claimOf(s.presupposes))}</span>` : s.claim ? `<span class="lab">claim: ${esc(claimOf(s.claim))}</span>` : ""}</div>
        <div class="msg user" style="margin-top:10px"><span class="who">${esc(d.analyst.display)} <span class="muted">(not saved in B)</span></span>${hiFig(s.q)}</div>
        <div class="msg bot"><span class="who">${esc(d.assistant.display)}</span>${hiFig(s.a)}</div>
        ${week === 2 ? `<div class="small">${read(s)}</div>` : ""}</div>`).join("")}`;
  }

  function ledgerView() {
    if (!S.data || (!S.ledgerSeen && !S.world)) return running() ? waiting("The finding log") : empty("The finding log");
    return `<h1>C only — the finding log</h1>
      <p class="lead">What the page calls “store verified findings as Facts”: the app writes one fact per finding with the status the analyst gave it, word for word (<code>fact add</code>, no extraction). C also gets every thread as asked and answered, like A — so this step measures whether the log outweighs what the later questions leave behind.</p>
      <div class="card">${S.data.ledger.map((x) => `<div class="copyrow small">${hiFig(x.text)}</div>`).join("")}</div>`;
  }

  const CELL = {
    taken: () => `<span class="lab lab-bad">⚑ stated as fact</span>`,
    kept: () => `<span class="lab lab-ok">✓ kept as a claim</span>`,
    missing: () => `<span class="lab">not in memory</span>`,
  };
  const CELL18 = { current: '<span class="lab lab-ok">✓ current</span>', previously: '<span class="lab lab-bad">✗ only as “previously”</span>', missing: '<span class="lab">not in memory</span>' };
  function censusView() {
    if (!S.census) return running() ? waiting("What memory holds") : empty("What memory holds");
    const cs = S.census;
    return `<h1>Claims stated as fact: ${MEM.map((a) => `${a} ${cs[COPY[a]].taken}/3`).join(" · ")}</h1>
      <p class="lead"><code>fact list</code> on each project. A claim counts as stated when a fact gives the figure or the deal with nothing that keeps it a claim (plan, depends on, claims, report, talks…); text after “previously” is the old version the extraction kept.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>week-1 claim</th>${MEM.map((a) => `<th>${a} · ${esc(CNAME(a))}</th>`).join("")}</tr></thead><tbody>
      ${CLAIMS().map((c) => `<tr class="${MEM.some((a) => cs[COPY[a]].claims[c.id].status === "taken") ? "hl" : ""}"><td class="small"><b>${esc(c.label)}</b><br><span class="muted">${esc(c.status)}</span></td>
        ${MEM.map((a) => `<td class="small">${CELL[cs[COPY[a]].claims[c.id].status]()}</td>`).join("")}</tr>`).join("")}
      <tr><td class="small"><b>Pellmont Ohio: 18,000 tonnes today</b><br><span class="muted">the verified figure</span></td>${MEM.map((a) => `<td class="small">${CELL18[cs[COPY[a]].pellmont_18k.status]}</td>`).join("")}</tr>
      <tr><td class="small muted">facts</td>${MEM.map((a) => `<td class="small">${cs[COPY[a]].facts}</td>`).join("")}</tr>
      </tbody></table></div>
      ${MEM.filter((a) => cs[COPY[a]].taken).map((a) => `<div class="card"><h3 style="margin-top:0">${a}: the facts that state a claim as established, word for word</h3>
        ${Object.values(cs[COPY[a]].claims).flatMap((x) => x.taken).map((f) => `<div class="copyrow small">⚑ ${hiFig(f)}</div>`).join("")}</div>`).join("")}`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The research assistant — right: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].n}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7 per memory, plus one call without a boundary as the ∅ baseline. <b>⚑</b> marks an answer that states an unproven claim as fact. Q7 and Q8 are controls: findings nobody questioned.</p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>truth</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows[a].filter((r) => r.item === q.id); const bad = rs.find((r) => r.grade === "taken") || rs.find((r) => r.grade && r.grade !== "ok" && !r.empty) || rs.find((r) => r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(q.ask)}</td><td class="small">${esc(q.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.taken ? ` <span class="bad-ink">⚑${x.taken}</span>` : "") + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    const empties = ARMS.flatMap((a) => S.rows[a].filter((r) => r.empty && r.reply).map((r) => `${a} ${r.item}  ${r.reply.replace(/^\**answer:?\**\s*/i, "")}`));
    const counts = empties.reduce((m, x) => (m[x] = (m[x] || 0) + 1, m), {});
    return `<h1>Right: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].n}`).join(" · ")} · unproven claim stated as fact: A ${A.A.taken} · B ${A.B.taken} · C ${A.C.taken}</h1>
      <p class="lead">${QS().length * RUNS()} answers per memory. A and C hold the same twelve threads as asked and answered; B holds only the answers; C adds the finding log.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>right</th><th class="num">⚑</th><th class="num">✗</th><th class="num">?</th><th class="num">∅</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].taken ? "bad-ink" : ""}">${A[a].taken}</b></td><td class="num">${A[a].wrong}</td><td class="num">${A[a].unknown}</td><td class="num">${A[a].empty}</td></tr>`).join("")}</tbody></table>
        ${empties.length ? `<p class="small muted" style="margin-bottom:4px">∅ — MemoryRouter attached nothing, so the model answered on its own (listed, not graded):</p>${Object.entries(counts).map(([x, n]) => `<div class="copyrow small">${esc(x)}${n > 1 ? ` <span class="muted">×${n}</span>` : ""}</div>`).join("")}` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.id)} ${esc(q.what)} <span class="muted">= ${esc(q.truth)}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ right · ⚑ states an unproven claim as fact · ✗ wrong · ? says it does not know · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — no memory, then A, B and C</h1>
      <p class="lead">Any question to the research assistant, once with no memory and once through each memory. Graded when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Analyst → research assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.truth ? `<p class="small muted">truth: ${esc(x.truth)}</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, truth: r.truth });
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
    if (!confirm("Delete the demo's boundaries, conversations, its six actors (their facts first) and the three projects?")) return;
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
    else if (k === "progress") { term("progress", ev.text.trim()); if (d.copy && d.thread) { (S.read[d.copy] ||= {})[d.thread] = true; if (S.caughtUp && S.view === "week2") renderStage(); } }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "threads") S.seen[d.week] = true;
    else if (k === "ledger") S.ledgerSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "memory_view") S.census = d.census;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, the six actors (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the research assistant, with no memory and through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "threads", "ledger", "census", "ask_row", "arm_done", "example",
    "score", "summary", "router_unavailable", "asked", "memory_view", "done", "error"];
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

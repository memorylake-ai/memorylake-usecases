/* Stage handoff memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three pipelines, one memory each", view: "setup" },
    { n: 3, title: "Account records", sub: "what memory knows already", view: "records" },
    { n: 4, title: "The handoff", sub: "stage 1 writes, stage 2 reads", view: "handoff" },
    { n: 5, title: "Memory now", sub: "what made it in, asked again", view: "census" },
    { n: 6, title: "Score", sub: "per customer and per pipeline", view: "score" },
  ];
  const fresh = () => ({ records: null, hand: { A: [], B: [], C: [] }, armDone: {}, census: null, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "Which region should Orvane Labs' new environment be created in?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const CUST = () => S.data?.customers || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;
  const cust = (id) => CUST().find((c) => c.id === id) || {};

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, records: recordsView, handoff: handoffView, census: censusView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the new value"], stale: ["↺", "bad", "the value from before stage 1's write"],
    unknown: ["?", "warn", "Unknown"], other: ["–", "warn", "neither value"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  const ansLine = (t) => { const m = /answer\s*:\s*(.*)/i.exec(t || ""); return (m ? m[1] : t || "").split("\n")[0]; };
  // the same counts as the CLI's tally(): ∅ reads are listed, not graded
  function tally(rows) {
    const kind = Object.fromEntries(CUST().map((c) => [c.id, c.kind])), live = rows.filter((r) => r.grade && !r.empty);
    const c = (xs, g) => xs.filter((r) => r.grade === g).length, part = (p) => live.filter((r) => kind[r.item] === p);
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c(live, "ok"), stale: c(live, "stale"), unknown: c(live, "unknown"),
      other: c(live, "other"), change_n: part("change").length, change_ok: c(part("change"), "ok"), control_n: part("control").length, control_ok: c(part("control"), "ok") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.now)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (24 handoffs, ~110 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-qdp")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The next pipeline stage reads memory before the last one's write is in it.</h1>
      <p class="lead">${esc(d.org || "Ferrowind")} (a fictional SaaS company) runs an account-change pipeline on a queue. Stage 1 reads what a customer asked for and writes it into memory, then puts a light message on the <code>${esc(d.queue || "account.changed")}</code> queue — just the customer and the ticket. Stage 2 picks it up, asks memory through MemoryRouter what to do, and does it.
      The queue hands over in about a second; MemoryLake reads a conversation message in the background in twenty to fifty. Three pipelines: <b>A</b> enqueue at once · <b>B</b> wait for <code>cook_finished</code>, then enqueue · <b>C</b> also <code>fact add</code> the result and put the fact id in the message. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 110 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-queue-driven-agent-pipelines" target="_blank" rel="noopener">memorylake.ai › Memory for queue-driven agent pipelines</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three pipelines, one memory each</h1>
      <p class="lead">Each pipeline variant writes into its own project and reads through its own boundary, so one variant's writes can never answer another's question. All three projects carry the same plain name: a project's name is context for extraction.</p>
      <div class="card"><dl class="kv"><dt>A · enqueue at once</dt><dd class="mono small">${esc(w?.projects?.now || "pending")}</dd><dt>B · wait for cook_finished</dt><dd class="mono small">${esc(w?.projects?.cooked || "pending")}</dd>
        <dt>C · fact add + id</dt><dd class="mono small">${esc(w?.projects?.fact || "pending")}</dd><dt>stage 1 actor</dt><dd class="mono small">${esc(w?.actors?.intake || "pending")}</dd></dl></div>`;
  }

  function recordsView() {
    if (!S.records) return running() ? waiting("Account records") : empty("Account records");
    return `<h1>What memory knows already — ${S.records.length} account records from ${esc(S.data?.records_date)}</h1>
      <p class="lead">The same records go into all three projects with one <code>fact add</code> each. Six customers are about to change one of these values; two (the controls) will send a request about something else.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>customer</th><th>field</th><th>record</th></tr></thead><tbody>
      ${S.records.map((r) => `<tr><td class="small"><b>${esc(r.name)}</b>${cust(r.id).kind === "control" ? ' <span class="lab">control</span>' : ""}</td><td class="small">${esc(r.field)}</td><td class="small">${esc(r.record)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  const ARM_HOW = { A: "Stage 1 appends its note to the ticket's conversation and enqueues straight away.",
    B: "Stage 1 appends its note, then polls <code>conv consumed-messages --message</code> and <code>conv cook-status</code> until the conversation reports <code>cook_finished</code> — and only then enqueues.",
    C: "Stage 1 appends its note and also writes its result with <code>fact add</code> — dated, naming the value it replaces — then puts the fact id in the queue message and enqueues." };
  function timing(a, h) {
    if (a === "B") return h.cooked ? `cook_finished at <b>+${h.waited} s</b>` : `<span class="bad-ink">no cook_finished in ${S.data?.cook_limit || 300} s</span>`;
    return `${a === "C" ? "fact add" : "wrote"} +${h.written} s · read <b>+${h.read_at} s</b>`;
  }
  function handoffView() {
    const any = ARMS.some((a) => S.hand[a].length);
    if (!any) return unavailable() || (running() ? waiting("The handoff") : empty("The handoff"));
    const c0 = CUST()[0] || {};
    return `<h1>Stage 1 writes, the queue hands over, stage 2 reads</h1>
      <p class="lead">Stage 2 picks the message up ${S.data?.queue_s ?? 0.5} s after stage 1 enqueues it and reads memory ${RUNS()} times at the same moment (temperature 0.7). Times are seconds from the moment stage 1 starts writing. The queue message: <code>${esc(S.data?.sample_message)}</code></p>
      ${ARMS.map((a) => { const hs = S.hand[a]; const t = tally(hs.flatMap((h) => h.rows));
        return `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}${S.armDone[a] ? ` <span class="muted small">— the new value ${t.ok}/${t.n} · ↺ ${t.stale}</span>` : ""}</h3><p class="small muted">${ARM_HOW[a]}</p>
        ${hs.length ? `<table class="lint plain"><thead><tr><th>customer</th><th>stage 1 → stage 2</th><th>${RUNS()} reads</th><th>stage 2 read</th></tr></thead><tbody>
        ${CUST().map((c) => { const h = hs.find((x) => x.item === c.id); if (!h) return "";
          const bad = h.rows.find((r) => r.grade && r.grade !== "ok" && !r.empty) || h.rows.find((r) => r.reply) || {};
          return `<tr class="${h.rows.some((r) => r.grade && r.grade !== "ok" && !r.empty) ? "hl" : ""}"><td class="small"><b>${esc(c.name)}</b><br><span class="muted">${esc(c.field)}: ${c.kind === "control" ? "unchanged" : `${esc(c.stale)} → ${esc(c.current)}`}</span></td>
            <td class="small mono">${timing(a, h)}</td><td><div class="cell-ans">${h.rows.map(markOf).join(" ")}</div></td><td class="small">${esc(ansLine(bad.reply))}</td></tr>`; }).join("")}
        </tbody></table>` : `<div class="empty small">${running() ? '<span class="pill busy"><span class="spinner"></span> running…</span>' : "not run"}</div>`}</div>`; }).join("")}
      ${S.hand.C.length ? `<div class="card"><h3 style="margin-top:0">C · what stage 1 wrote</h3><div class="copyrow small mono">${esc(S.hand.C.find((h) => h.item === c0.id)?.message || "")}</div><div class="copyrow small">${esc(c0.result)}</div></div>` : ""}`;
  }

  function censusView() {
    if (!S.census) return running() ? waiting("Memory now") : empty("Memory now");
    const cs = S.census, late = cs.late || [], L = (a, id) => late.find((r) => r.arm === a && r.item === id);
    const copies = { A: "now", B: "cooked", C: "fact" }, ch = CUST().filter((c) => c.kind === "change");
    const lateOk = (a) => late.filter((r) => r.arm === a && r.grade === "ok" && !r.empty).length, lateN = (a) => late.filter((r) => r.arm === a && r.grade && !r.empty).length;
    const wrong = late.filter((r) => r.grade && r.grade !== "ok" && !r.empty);
    return `<h1>The new value is in memory: A ${cs.extracted.now}/${ch.length} · B ${cs.extracted.cooked}/${ch.length} · C ${cs.extracted.fact}/${ch.length}</h1>
      <p class="lead"><code>fact list</code> on each project after the handoffs — does a fact that names the customer state the new value? — and every question asked once more through each pipeline — what a later reader gets. In A and B the new value can only come from the extraction of stage 1's note; in C it is also in the fact stage 1 wrote. Asked again now: ${ARMS.map((a) => `${a} ${lateOk(a)}/${lateN(a)}`).join(" · ")}.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>customer</th><th>new value</th>${ARMS.map((a) => `<th>${a} · in memory</th>`).join("")}${ARMS.map((a) => `<th class="num">${a} now</th>`).join("")}</tr></thead><tbody>
      ${CUST().map((c) => `<tr class="${ARMS.some((a) => (c.kind === "change" && !cs[copies[a]].customers[c.id].new) || (L(a, c.id) && L(a, c.id).grade !== "ok" && !L(a, c.id).empty)) ? "hl" : ""}">
        <td class="small"><b>${esc(c.name)}</b><br><span class="muted">${esc(c.field)}${c.kind === "control" ? " · control" : ""}</span></td><td class="small">${esc(c.current)}</td>
        ${ARMS.map((a) => { const x = cs[copies[a]].customers[c.id]; return `<td class="small">${c.kind === "control" ? '<span class="muted">—</span>' : x.new ? '<span class="lab lab-ok">yes</span>' : `<span class="lab lab-bad">no</span><div class="muted">${esc(x.shown)}</div>`}</td>`; }).join("")}
        ${ARMS.map((a) => `<td class="num">${L(a, c.id) ? markOf(L(a, c.id)) : ""}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
      ${ARMS.some((a) => (cs[copies[a]].unnamed || []).length) ? `<div class="card"><h3 style="margin-top:0">Facts that name no customer</h3><p class="small muted">Extracted from stage 1's notes with the customer dropped. MemoryRouter can attach such a fact to any customer's question — right for the one it came from, wrong for the others.</p>
        ${ARMS.flatMap((a) => (cs[copies[a]].unnamed || []).map((f) => `<div class="copyrow small"><b>${a}</b> · ${esc(f)}</div>`)).join("")}</div>` : ""}
      ${wrong.length ? `<div class="card"><h3 style="margin-top:0">Asked again — the answers that were not the new value</h3>${wrong.map((r) => `<div class="copyrow small">${markOf(r)} <b>${r.arm} · ${esc(cust(r.item).name)}</b>: ${esc(ansLine(r.reply))}</div>`).join("")}</div>` : ""}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers, Lt = sc.latency || {};
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>The new value: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].n}`).join(" · ")}</h1>
      <p class="lead">${CUST().length * RUNS()} stage-2 reads per pipeline. What matters: the six customers whose value changed — and the two controls, where waiting or writing more must not make things worse — against how long stage 2 waited.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>pipeline</th><th>the new value</th><th class="num">↺ old</th><th class="num">– / ?</th><th>changed</th><th>control</th><th>stage 2 reads at</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].stale ? "bad-ink" : ""}">${A[a].stale}</b></td><td class="num">${A[a].other + A[a].unknown}</td>
          <td><b>${A[a].change_ok}/${A[a].change_n}</b>${bar(A[a].change_ok, A[a].change_n, "g1")}</td><td><b>${A[a].control_ok}/${A[a].control_n}</b></td>
          <td class="mono small">+${Lt[a]?.median} s <span class="muted">(max ${Lt[a]?.max})</span></td></tr>`).join("")}</tbody></table>
        ${ARMS.some((a) => A[a].empty) ? `<p class="small muted" style="margin-bottom:0">∅ (MemoryRouter attached nothing; listed, not graded): ${ARMS.map((a) => `${a} ${A[a].empty}`).join(" · ")}</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per customer</h3><table class="lint plain"><thead><tr><th>customer</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${CUST().map((c) => `<tr><td class="small">${esc(c.name)} · ${esc(c.field)} <span class="muted">= ${esc(c.current)}${c.kind === "control" ? " · control" : ""}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[c.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ the new value · ↺ the value from before stage 1's write · ? Unknown · – neither · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C</h1>
      <p class="lead">Any question to the fulfilment stage, once through each pipeline's memory. Graded when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${CUST().map((c) => `<button class="chip" data-q="${esc(c.ask)}">${esc(c.name)} · ${esc(c.field)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Stage 2 → memory</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.current ? `<p class="small muted">new value: ${esc(x.current)}</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, current: r.current });
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
    if (!confirm("Delete the demo's boundaries, conversations, the stage-1 actor (its facts first) and the three projects?")) return;
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
    else if (k === "records") S.records = d.rows;
    else if (k === "handoff") S.hand[d.arm].push(d);
    else if (k === "arm_done") S.armDone[d.arm] = d.tally;
    else if (k === "census") S.census = d;
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, the stage-1 actor (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the fulfilment stage, through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "records", "handoff", "arm_done", "census",
    "score", "summary", "router_unavailable", "asked", "done", "error"];
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

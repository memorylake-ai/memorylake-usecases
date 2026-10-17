/* Thread reply memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["0", "A", "B", "C"];
  const MEM = ["A", "B", "C"];
  const COPY = { A: "sent", B: "threads", C: "tagged" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "The channel", sub: "ten messages, saved as posted", view: "channel" },
    { n: 4, title: "What memory holds", sub: "whose value each fact says", view: "census" },
    { n: 5, title: "Ask the assistant", sub: "no memory + three memories", view: "ask" },
    { n: 6, title: "Score", sub: "per question and per memory", view: "score" },
  ];
  const fresh = () => ({ channelSeen: false, batches: {}, census: null, rows: { 0: [], A: [], B: [], C: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "When does the Leeds office move happen?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const VALUES = () => S.data?.values || [];
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
    const r = { connect, setup, channel: channelView, census: censusView, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], swap: ["⇄", "bad", "another move's value given as this one's"],
    wrong: ["✗", "bad", "wrong"], unknown: ["?", "warn", "says it does not know"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const live = rows.filter((r) => r.grade && !r.empty), c = (g) => live.filter((r) => r.grade === g).length;
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c("ok"), swap: c("swap"), wrong: c("wrong"), unknown: c("unknown") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `right <b>${t.ok}</b>/${t.n} · ⇄ <b class="${t.swap ? "bad-ink" : ""}">${t.swap}</b> · ✗ <b>${t.wrong}</b> · ? <b>${t.unknown}</b>${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const MOVE = /(\bLeeds\b|\bPorto\b|\bAustin\b)/g;
  const hiMove = (t) => esc(t).replace(MOVE, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.sent)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes (10 messages read one at a time in each of three copies, then ~100 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-trm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“It slips to 9 March” — a reply that only says “it”, saved the moment it is posted.</h1>
      <p class="lead">${esc(d.org || "Pellridge Freight")} (a fictional company): the workplace ops team plans three office moves in the Slack channel ${esc(d.channel || "#office-moves")}. Each move gets a thread that names it; the replies under it say “it”, “they”, “there”. The assistant saves every message the moment it is posted — and a reply read on its own is matched to whatever memory already holds: Porto's date ends up as the Leeds move's date.
      Three memories of the same ten messages: <b>A</b> the channel as sent, one conversation · <b>B</b> one conversation per Slack thread · <b>C</b> one conversation, each reply tagged with its thread's first message — and <b>0</b>, the same questions with no memory. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 100 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/keep-ai-context-across-conversations" target="_blank" rel="noopener">memorylake.ai › Keep AI context across conversations</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Three projects, one boundary each</h1>
      <p class="lead">The same ten messages go into three projects with the same plain name (a project's name is context for extraction; the arm lives only in the custom id). Each project has its own pair of people, so the three memories share nothing.</p>
      <div class="card"><dl class="kv">${MEM.map((a) => `<dt>${a} · ${esc(CNAME(a))}</dt><dd class="mono small">${esc(w?.projects?.[COPY[a]] || "pending")}</dd>`).join("")}
        ${(d.people || []).map((p) => `<dt>${esc(p.display)} · ${esc(p.role)}</dt><dd class="mono small">${MEM.map((a) => esc(w?.actors?.[`${COPY[a]}-${p.key}`] || "pending")).join("<br>")}</dd>`).join("")}</dl></div>`;
  }

  function channelView() {
    if (!S.data || (!S.channelSeen && !S.world)) return running() ? waiting("The channel") : empty("The channel");
    const d = S.data;
    const b = (c, id) => { const n = (S.batches[c] || {})[id]; return n ? `<span class="lab ${n === 1 ? "lab-bad" : "lab-ok"}" title="read in a batch of ${n}">${n}</span>` : '<span class="muted">…</span>'; };
    return `<h1>${esc(d.channel)} — three threads, ten messages, saved the moment each is posted</h1>
      <p class="lead">Each thread starts with a message that names the move; the replies say “it”, “they”, “there”. The Leeds update came a day later, under the Leeds thread — in the channel it sits right after the Austin messages. The number after each message is the size of the extraction batch it was read in: <b>1</b> means it was read on its own, as in a live chat.</p>
      ${d.moves.map((m) => `<div class="card"><h3 style="margin-top:0">Thread: ${esc(m.name)} office move</h3><table class="lint plain"><thead><tr><th>posted (UTC)</th><th>from</th><th>message</th>${MEM.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${d.messages.filter((x) => x.thread === m.id).map((x) => `<tr class="${x.root ? "" : "hl"}"><td class="small mono">${esc(x.ts.slice(5, 10))} ${esc(x.ts.slice(11, 16))}</td><td class="small">${esc(x.person)}</td>
          <td class="small">${x.root ? "" : "└ "}${hiMove(x.text)}</td>${MEM.map((a) => `<td class="num">${b(COPY[a], x.id)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`).join("")}
      <div class="card"><h3 style="margin-top:0">The same reply in A and in C</h3><div class="copyrow small"><b>A</b> · ${hiMove(d.messages[4].text)}</div>
        <div class="copyrow small"><b>C</b> · ${hiMove(d.messages[4].tagged)}</div><p class="small muted" style="margin-bottom:0">B sends the reply unchanged, into the Porto thread's own conversation.</p></div>`;
  }

  const CELL = {
    right: (v) => `<span class="lab lab-ok">✓ ${esc(v.move)}</span>`,
    other: (v) => `<span class="lab lab-bad">✗ under ${esc(v.under.map(cap).join("/"))}</span>`,
    both: (v) => `<span class="lab lab-ok">✓ ${esc(v.move)}</span> <span class="lab lab-bad">✗ ${esc(v.under.map(cap).join("/"))}</span>`,
    previously: () => `<span class="lab lab-bad">only as “previously”</span>`,
    unnamed: () => `<span class="lab">no move named</span>`,
    missing: () => `<span class="lab lab-bad">not in memory</span>`,
  };
  function censusView() {
    if (!S.census) return running() ? waiting("What memory holds") : empty("What memory holds");
    const cs = S.census, d = S.data || {};
    const bad = VALUES().filter((v) => ["other", "both", "previously"].includes(cs.sent.values[v.id].status));
    return `<h1>Stored under the right move: ${MEM.map((a) => `${a} ${cs[COPY[a]].right}/${cs[COPY[a]].total}`).join(" · ")}</h1>
      <p class="lead"><code>fact list</code> on each project. One row per value said in the channel: which move does the fact that holds it name? The move named first counts; text after “previously” is the old value the extraction kept.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>value · said in the thread of</th>${MEM.map((a) => `<th>${a} · ${esc(CNAME(a))}</th>`).join("")}</tr></thead><tbody>
      ${VALUES().map((v) => `<tr class="${MEM.some((a) => cs[COPY[a]].values[v.id].status !== "right") ? "hl" : ""}"><td class="small"><b>${esc(v.show)}</b> — ${esc((d.moves || []).find((m) => m.id === v.move)?.name)} ${esc(v.label)}</td>
        ${MEM.map((a) => `<td class="small">${CELL[cs[COPY[a]].values[v.id].status](cs[COPY[a]].values[v.id])}</td>`).join("")}</tr>`).join("")}
      <tr><td class="small muted">facts that name the move</td>${MEM.map((a) => `<td class="small">${Object.entries(cs[COPY[a]].naming || {}).map(([k, n]) => `${esc(cap(k))} <b class="${n ? "" : "bad-ink"}">${n}</b>`).join(" · ")}</td>`).join("")}</tr>
      </tbody></table></div>
      ${bad.length ? `<div class="card"><h3 style="margin-top:0">What A's facts say, word for word</h3>${[...new Set(bad.map((v) => cs.sent.values[v.id].shown))].map((f) => `<div class="copyrow small">${hiMove(f)}</div>`).join("")}</div>` : ""}`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The workplace assistant — right: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].n}`).join(" · ")}</h1>
      <p class="lead">A week later. Each question ${RUNS()} times at temperature 0.7 per memory, plus one call without a boundary as the ∅ baseline. <b>⇄</b> marks an answer that gives another move's value as this one's. Q8 asks something nobody said: the right answer is that it is not recorded.</p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>truth</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows[a].filter((r) => r.item === q.id); const bad = rs.find((r) => r.grade === "swap") || rs.find((r) => r.grade && r.grade !== "ok" && !r.empty) || rs.find((r) => r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(q.ask)}</td><td class="small">${esc(q.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.swap ? ` <span class="bad-ink">⇄${x.swap}</span>` : "") + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    const empties = ARMS.flatMap((a) => S.rows[a].filter((r) => r.empty && r.reply).map((r) => `${a} ${r.item}  ${r.reply.replace(/^\**answer:?\**\s*/i, "")}`));
    const counts = empties.reduce((m, x) => (m[x] = (m[x] || 0) + 1, m), {});
    return `<h1>Right: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].n}`).join(" · ")} · another move's value: A ${A.A.swap} · B ${A.B.swap} · C ${A.C.swap}</h1>
      <p class="lead">${QS().length * RUNS()} answers per memory. A, B and C hold the same ten messages, read the same way — one at a time, as posted. B only splits them by thread; C only tags each reply with its thread's first message.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>right</th><th class="num">⇄</th><th class="num">✗</th><th class="num">?</th><th class="num">∅</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].swap ? "bad-ink" : ""}">${A[a].swap}</b></td><td class="num">${A[a].wrong}</td><td class="num">${A[a].unknown}</td><td class="num">${A[a].empty}</td></tr>`).join("")}</tbody></table>
        ${empties.length ? `<p class="small muted" style="margin-bottom:4px">∅ — MemoryRouter attached nothing, so the model answered on its own (listed, not graded):</p>${Object.entries(counts).map(([x, n]) => `<div class="copyrow small">${esc(x)}${n > 1 ? ` <span class="muted">×${n}</span>` : ""}</div>`).join("")}` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.id)} ${esc(q.what)} <span class="muted">= ${esc(q.truth)}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ right (Q8: says it is not recorded) · ⇄ another move's value given as this one's · ✗ wrong · ? says it does not know · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — no memory, then A, B and C</h1>
      <p class="lead">Any question to the workplace assistant, once with no memory and once through each memory. Graded when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Facilities manager → workplace assistant</span>${esc(x.question)}</div>
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
    if (!confirm("Delete the demo's boundaries, conversations, its six people (their facts first) and the three projects?")) return;
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
    else if (k === "progress") { term("progress", ev.text.trim()); if (d.copy && d.msg_id) { (S.batches[d.copy] ||= {})[d.msg_id] = d.batch; if (S.caughtUp && S.view === "channel") renderStage(); } }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "channel") S.channelSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "memory_view") S.census = d.census;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, the six people (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the workplace assistant, with no memory and through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "channel", "census", "ask_row", "arm_done", "example",
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

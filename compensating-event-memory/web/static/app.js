/* Compensating event memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C", "D"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "four projects, one boundary each", view: "setup" },
    { n: 3, title: "Account events", sub: "six reversals that name an id", view: "events" },
    { n: 4, title: "What memory says", sub: "the latest fact per account", view: "census" },
    { n: 5, title: "C and D: app projection", sub: "event log + folded state", view: "state" },
    { n: 6, title: "Ask the assistant", sub: "four memories, eight questions", view: "ask" },
    { n: 7, title: "Score", sub: "per question and per memory", view: "score" },
    { n: 8, title: "Replay", sub: "C's event log, folded in code", view: "replay" },
  ];
  const fresh = () => ({ eventsSeen: false, census: null, state: null, rows: { A: [], B: [], C: [], D: [] }, score: null, replay: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "What is Tallow & Finch Bakery's service address right now?", rday: "2026-10-01", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const ACCTS = () => S.data?.accounts || [];
  const QS = () => S.data?.questions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;
  const day = (iso) => new Date(String(iso).slice(0, 10) + "T12:00:00Z").toLocaleDateString("en-GB", { timeZone: "UTC", weekday: "short", day: "2-digit", month: "short" });

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B, C and D</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, events: eventsView, census: censusView, state: stateView, ask: askView, score, replay: replayView, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the current value"], stale: ["↺", "bad", "the value the reversal undid (control: the value before the latest change)"],
    unknown: ["?", "warn", "Unknown"], other: ["–", "warn", "neither value"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  const KIND = () => Object.fromEntries(QS().map((q) => [q.id, q.kind]));
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const o = KIND(), live = rows.filter((r) => r.grade && !r.empty), c = (xs, g) => xs.filter((r) => r.grade === g).length;
    const part = (p) => live.filter((r) => o[r.item] === p);
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c(live, "ok"), stale: c(live, "stale"), unknown: c(live, "unknown"),
      other: c(live, "other"), reversed_n: part("reversed").length, reversed_ok: c(part("reversed"), "ok"), control_n: part("control").length, control_ok: c(part("control"), "ok") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `current <b>${t.ok}</b>/${t.n} · ↺ reversed value <b class="${t.stale ? "bad-ink" : ""}">${t.stale}</b> · Unknown <b>${t.unknown}</b>
    · reversed accounts <b>${t.reversed_ok}</b>/${t.reversed_n} · controls <b>${t.control_ok}</b>/${t.control_n}${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const STATE = { current: ["current", "lab-ok"], stale: ["STALE", "lab-bad"], tie: ["tie", "lab-bad"], missing: ["missing", "lab-bad"] };
  const stateLab = (s) => s ? `<span class="lab ${STATE[s][1]}">${STATE[s][0]}</span>` : "";
  const DATE = /(\((?:as of|on) [^)]*\)|\bpreviously\b|\bevt-\d+\b)/g;
  const hiDate = (t) => esc(t).replace(DATE, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.plain)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 7 minutes (48 event messages read one at a time, then ~104 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-cev")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A reversal that names an event id never reaches the value it undoes.</h1>
      <p class="lead">${esc(d.org || "Ferrowyn Energy")} (a fictional energy supplier) keeps each customer account as an event stream and writes every event into memory; its account assistant answers from that memory through MemoryRouter. A mistake is never edited away — a compensating event is appended: <code>EventReversed: reverses evt-1010</code>. It names the event by id and does not repeat the value.
      Extraction keeps the values but not the ids, so the reversal has nothing to attach to. Four memories: <b>A</b> the events as they are · <b>B</b> the same events with each reversal spelled out · <b>C</b> A's facts plus the event log and a state fact the app folds itself · <b>D</b> only that event log and those state facts. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 104 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-event-sourced-ai-agents" target="_blank" rel="noopener">memorylake.ai › Memory for event-sourced AI agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Four projects, one boundary each</h1>
      <p class="lead">A and B get the same event messages except the six reversals, which B spells out. C gets every fact MemoryLake extracted into A, copied word for word, plus the event log (one fact per event, id and day kept) and one state fact per account that the app folds from the events. D gets only that event log and those state facts. All four carry the same plain name: a project's name is context for extraction.</p>
      <div class="card"><dl class="kv"><dt>A · as they are</dt><dd class="mono small">${esc(w?.projects?.plain || "pending")}</dd><dt>B · spelled out</dt><dd class="mono small">${esc(w?.projects?.spelled || "pending")}</dd>
        <dt>C · A + app projection</dt><dd class="mono small">${esc(w?.projects?.state || "pending")}</dd><dt>D · app projection only</dt><dd class="mono small">${esc(w?.projects?.log || "pending")}</dd><dt>event-store actor</dt><dd class="mono small">${esc(w?.actors?.bus || "pending")}</dd></dl></div>`;
  }

  function eventsView() {
    if (!S.data || (!S.eventsSeen && !S.world)) return running() ? waiting("Account events") : empty("Account events");
    const d = S.data;
    return `<h1>Twenty-four events — six of them reversals that name the event they undo</h1>
      <p class="lead">Eight accounts, three events each, written in order (one GROUP conversation per account and memory, each event read before the next, stamped with its own time). Six accounts end with <code>EventReversed</code>; two controls end with an ordinary change. A and B differ only in the reversal's text:</p>
      <div class="card"><div class="small"><b>A</b></div><div class="mono small">${esc(d.sample.A)}</div><div class="small" style="margin-top:8px"><b>B</b></div><div class="mono small">${esc(d.sample.B)}</div></div>
      <div class="card"><table class="lint plain"><thead><tr><th>account</th><th>event</th><th>day</th><th>what happened</th></tr></thead><tbody>
      ${ACCTS().map((a) => a.events.map((e, i) => `<tr class="${e.reverses ? "hl" : ""}"><td class="small">${i === 0 ? `<b>${esc(a.name)}</b><br><span class="muted">${esc(a.field)} now: ${esc(a.now)}${a.kind === "control" ? " · control" : ""}</span>` : ""}</td>
        <td class="small mono">${esc(e.id)}</td><td class="small mono">${esc(day(e.at))}</td>
        <td class="small">${e.reverses ? `<span class="lab lab-bad">EventReversed</span> reverses <span class="mono">${esc(e.reverses)}</span> — ${esc(e.text)}` : `<span class="mono">${esc(e.type)}</span> → ${esc(e.value)}`}</td></tr>`).join("")).join("")}
      </tbody></table></div>`;
  }

  function censusView() {
    if (!S.census) return running() ? waiting("What memory says") : empty("What memory says");
    const cs = S.census, tA = cs.plain.tally, tB = cs.spelled.tally;
    const shown = (s) => (s.top?.[0] || s.facts?.[0] || "");
    return `<h1>A: the reversed value is still current for ${tA.stale} of 6 · B: ${tB.stale} of 6</h1>
      <p class="lead"><code>fact list</code> on each project, each fact attributed to its account by the conversation that wrote it (<code>conv fact-actions</code>). For each account, the latest-dated fact that states a value. A sentence about the reversal states a value only when it says what the value went back to.${tA.reversal_kept ? ` In A a fact about the reversal is in memory for ${tA.reversal_kept} of 6 accounts — next to the value it should have undone.` : ""}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>account</th><th>true now</th><th>A · as they are</th><th>B · spelled out</th></tr></thead><tbody>
      ${ACCTS().map((c) => { const a = cs.plain.accounts[c.id], b = cs.spelled.accounts[c.id];
        return `<tr class="${a.state !== "current" || b.state !== "current" ? "hl" : ""}"><td class="small"><b>${esc(c.name)}</b><br><span class="muted">${esc(c.field)}${c.kind === "control" ? " · control" : ""}</span></td>
          <td class="small">${esc(c.current)}</td>
          <td class="small">${stateLab(a.state)}${a.state !== "current" ? `<div class="muted">${hiDate(shown(a))}</div>${a.reversal?.[0] ? `<div class="muted">and: ${hiDate(a.reversal[0])}</div>` : ""}` : ""}</td>
          <td class="small">${stateLab(b.state)}${b.state !== "current" ? `<div class="muted">${hiDate(shown(b))}</div>` : ""}</td></tr>`; }).join("")}
      </tbody></table><p class="small" style="margin-bottom:0">A ${tA.facts} facts · B ${tB.facts} facts · controls current: A ${tA.control}/2 · B ${tB.control}/2</p></div>`;
  }

  function stateView() {
    if (!S.state) return running() ? waiting("C: app projection") : empty("C: app projection");
    const p = S.state;
    return `<h1>C: ${p.copied} facts copied from A, then ${p.logs.length} event-log facts and ${p.states.length} state facts the app writes · D: only the app's</h1>
      <p class="lead">The app folds each account's events in code: a reversal takes the event it names out of the stream. The result goes into memory as one state fact per account, next to the event log — every event with its id and day, so the log can be read back and replayed (step 8). C also holds every fact extracted into A, copied word for word; D holds nothing extracted.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>account</th><th>field</th><th>projected now</th><th></th></tr></thead><tbody>
      ${ACCTS().map((a) => { const r = a.events.find((e) => e.reverses); return `<tr><td class="small"><b>${esc(a.name)}</b></td><td class="small">${esc(a.field)}</td><td class="small">${esc(a.now)}</td>
        <td class="small muted">${r ? `<span class="mono">${esc(r.reverses)}</span> undone by <span class="mono">${esc(r.id)}</span>` : "control: latest change"}</td></tr>`; }).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0">State facts</h3>${p.states.map((t) => `<div class="factline small">${hiDate(t)}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">Event log — one fact per event</h3>${p.logs.map((t) => `<div class="factline small mono">${esc(t)}</div>`).join("")}</div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The account assistant — current value: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].n}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7, plus one call without a boundary as the ∅ baseline. ↺ marks the value the reversal undid.</p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>now</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows[a].filter((r) => r.item === q.id); const bad = rs.find((r) => r.grade && r.grade !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(q.ask)}</td><td class="small">${esc(q.current)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>Current value: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].n}`).join(" · ")}</h1>
      <p class="lead">${QS().length * RUNS()} answers per memory. The split that matters: the six accounts whose last event is a reversal; the two controls, where B, C and D must do no worse than A.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>current</th><th class="num">↺ reversed</th><th class="num">Unknown</th><th>reversed accounts</th><th>controls</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].stale ? "bad-ink" : ""}">${A[a].stale}</b></td><td class="num">${A[a].unknown}</td>
          <td><b>${A[a].reversed_ok}/${A[a].reversed_n}</b>${bar(A[a].reversed_ok, A[a].reversed_n, "g1")}</td><td><b>${A[a].control_ok}/${A[a].control_n}</b></td></tr>`).join("")}</tbody></table>
        ${ARMS.some((a) => A[a].empty) ? `<p class="small muted" style="margin-bottom:0">∅ (MemoryRouter attached nothing; listed, not graded): ${ARMS.map((a) => `${a} ${A[a].empty}`).join(" · ")}</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.what)} <span class="muted">= ${esc(q.current)}${q.kind === "control" ? " · control" : ""}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ the current value · ↺ the value the reversal undid (control: the value before the latest change) · ? Unknown · – neither · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function replayView() {
    const rp = S.replay, ready = !!S.world;
    const pick = `<div class="row" style="margin-top:4px"><label for="r-day" style="margin:0">Replay to</label><input id="r-day" type="date" value="${esc(S.rday)}" style="width:auto">
      <button class="btn primary" data-act="replay" ${ready && !running() ? "" : "disabled"}>Replay</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>`;
    if (!rp) return running() ? waiting("Replay") : `<h1>Replay</h1><div class="card">${pick}</div>`;
    return `<h1>Replay to ${esc(day(rp.at))}: ${rp.ok}/${rp.rows.length} accounts right on both days</h1>
      <p class="lead">C's event log read back from memory with <code>fact list</code> (every fact, not a top-k search) and folded in code — to a past day and to today. No model is involved; each cell is checked against the data file's own fold.</p>
      <div class="card">${pick}</div>
      <div class="card"><table class="lint plain"><thead><tr><th>account</th><th>field</th><th>on ${esc(day(rp.at))}</th><th>today</th><th></th></tr></thead><tbody>
      ${rp.rows.map((r) => `<tr class="${r.ok ? "" : "hl"}"><td class="small"><b>${esc(r.name)}</b></td><td class="small">${esc(r.field)}</td><td class="small">${esc(r.past)}</td><td class="small">${esc(r.now)}</td>
        <td class="small">${r.ok ? `<span class="mark ok">✓</span>${r.reversal ? ` <span class="muted">reversal <span class="mono">${esc(r.reversal)}</span></span>` : ""}` : '<span class="mark bad">✗</span>'}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B, C and D</h1>
      <p class="lead">Any question to the account assistant, once through each memory. Graded when it is one of the eight.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Support → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.current ? `<p class="small muted">now: ${esc(x.current)}</p>` : ""}`).join("")}</div></div>`;
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
    st.querySelector("#r-day")?.addEventListener("input", (e) => { S.rday = e.target.value; });
    st.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { S.tq = b.dataset.q; renderStage(); }));
    st.querySelector('[data-act="try"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Asking…";
      const r = await api("/api/ask", { question: S.tq });
      if (r) S.tries.push({ question: S.tq, rows: r.rows, current: r.current });
      if (S.view === "try") renderStage();
    });
    st.querySelector('[data-act="replay"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Replaying…";
      const r = await api("/api/replay", { at: S.rday });
      if (r) S.replay = r;
      if (S.view === "replay") renderStage();
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
    if (!confirm("Delete the demo's boundaries, conversations, the event-store actor (its facts first) and the four projects?")) return;
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
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2 && d.total === STEPS.length) resetView(); term("step", ev.text); if (S.follow && stepOf(d.index) && d.total === STEPS.length) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "events") S.eventsSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "state") S.state = d;
    else if (k === "memory_view") S.census = d.census;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "replay") S.replay = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, the event-store actor (facts first) and the four projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Replay” with another day, or “Ask one”: any question to the account assistant, through A, B, C and D.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "events", "census", "state", "ask_row", "arm_done", "example",
    "score", "replay", "summary", "router_unavailable", "asked", "memory_view", "done", "error"];
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

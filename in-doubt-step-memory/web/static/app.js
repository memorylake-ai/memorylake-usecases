/* In-doubt-step memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C"];
  const COPIES = ["journal", "wal"];
  const COPY_ARMS = { journal: "A · B", wal: "C" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two copies, one boundary each", view: "setup" },
    { n: 3, title: "The run", sub: "seven steps, a crash inside 4", view: "run" },
    { n: 4, title: "The run journals", sub: "completion vs write-ahead", view: "journal" },
    { n: 5, title: "What memory says", sub: "each step", view: "census" },
    { n: 6, title: "Resume check", sub: "“has step N been done?”", view: "ask" },
    { n: 7, title: "Reconcile", sub: "C only · by the key", view: "reconcile" },
    { n: 8, title: "Score", sub: "what the resume would do", view: "score" },
  ];
  const fresh = () => ({ runSeen: false, stored: null, journal: {}, census: null, rows: { A: [], B: [], C: [] }, after: [], reconciled: null, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "4", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const STEPS_ = () => S.data?.steps || [];
  const ARM = (a) => (S.data?.arms || {})[a] || a;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>a step through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, run: runView, journal: journalView, census, ask: askView, reconcile: reconcileView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], rerun: ["✗", "bad", "“No” on a finished step: the resume re-runs it"],
    skip: ["↷", "bad", "“Yes” on an unfinished step: the resume skips it"], hold: ["‖", "warn", "“Unknown” on a step memory settles: the resume stops to check for nothing"], other: ["?", "warn", "unclear"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.verdict), n = (v) => live.filter((r) => !r.empty && r.verdict === v).length;
    return { calls: live.length, empty: live.filter((r) => r.empty).length, ok: n("ok"), rerun: n("rerun"), skip: n("skip"), hold: n("hold"), other: n("other") };
  }
  const tline = (t) => `<b>${t.ok}</b> right · <b class="${t.rerun ? "bad-ink" : ""}">${t.rerun}</b> re-run a finished step · <b>${t.skip}</b> skip · <b>${t.hold}</b> stop to check${t.other ? ` · <b>${t.other}</b> unclear` : ""}${t.empty ? ` · <b>${t.empty}</b> ∅` : ""} <span class="muted">of ${t.calls - t.empty}</span>`;
  const stepOfN = (n) => STEPS_().find((s) => s.n === Number(n)) || {};
  const consequence = (s, v) => v === "rerun" ? s.rerun : v === "skip" ? s.skip : "";

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.journal)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (one run log extracted, then ~80 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-ids")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The journal says “no entry means not done” — the worker died between the payment and the entry</h1>
      <p class="lead">${esc(d.org || "Quillfen")} (a fictional startup) pays its contractors through ${esc(d.agent || "Payrook")}, a plan-and-execute agent. Run ${esc(d.run || "P-1016")} has seven steps.
      The app keeps a run journal in memory with <code>fact add</code>: an entry the moment each step completes, and one coverage fact — a step with no entry has not completed.
      Payrook sends step 4's bank transfers; the bank pays all 23; the worker dies before the reply comes back, so step 4's entry is never written. Asked whether step 4 is done, the model believes the journal.
      Three arms: A the completion journal, Yes/No · B the same memory, Yes/No/Unknown · C a write-ahead journal (a started entry with the idempotency key before each step, then the result). Then the app reconciles C's in-doubt step with the bank. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 80 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-startups-and-founding-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for startups and founding teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Two copies of the same run memory, one boundary each</h1>
      <p class="lead">Each copy is its own project, so no copy's answers come from the other's facts. A and B read the completion-journal copy (they differ only in the question); C reads the write-ahead-journal copy. Both hold the same run log.</p>
      <div class="grid2"><div class="card"><dl class="kv">${COPIES.map((c) => `<dt>${esc((d.copies || {})[c] || c)} <span class="muted">(${COPY_ARMS[c]})</span></dt><dd class="mono small">${esc(w?.projects?.[c] || "pending")}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">In the run log</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}</div></div>`;
  }

  function msgLine(m) {
    if (m.tool_use) return `<span class="lab">TOOL_USE</span> <b>${esc(m.tool_use.name)}</b><span class="mono small">(${esc(JSON.stringify(m.tool_use.arguments))})</span>`;
    if (m.tool_result) return `<span class="lab lab-bad">TOOL_RESULT</span> <span class="mono small">${esc(JSON.stringify(m.tool_result.result))}</span>`;
    const who = ((S.data?.people || []).find((p) => p.key === m.who) || {}).display || m.who;
    return `<b>${esc(who)}</b>: ${esc(m.text)}`;
  }
  function runView() {
    const d = S.data;
    if (!d || (!S.runSeen && !S.world)) return running() ? waiting("The run") : empty("The run");
    const t0 = new Date(d.messages && "2026-10-16T01:00:00Z");
    return `<h1>Run ${esc(d.run)}: steps 1–3 done, step 4's transfers paid — and the worker died before the reply</h1>
      <p class="lead">Both copies get the same run log: one GROUP conversation, each message as its sender at its time, the tool call as a TOOL_USE block. The bank settled all 23 transfers at 01:13; the TOOL_RESULT never arrived.</p>
      <div class="card">${d.messages.map((m, i) => `<div class="copyrow"><span class="k mono">${new Date(t0.getTime() + i * 180000).toISOString().slice(11, 16)}</span><span class="small">${msgLine(m)}</span></div>`).join("")}
        <div class="copyrow"><span class="k mono">${new Date(t0.getTime() + d.messages.length * 180000 - 120000).toISOString().slice(11, 16)}</span><span class="small muted">the bank settles all 23 transfers; the worker process dies before the reply arrives. Nothing more is written.</span></div></div>
      ${S.stored ? `<div class="card"><h3 style="margin-top:0">The run log as stored <span class="muted small">(conv msg list)</span></h3>${S.stored.results.map((r) => `<div class="small mono">${esc(r.kind)} ${esc(r.tool_call_id)} ${esc(r.tool_name || "")} ${esc(JSON.stringify(r.body))}</div>`).join("")}${S.stored.results.some((r) => r.kind === "TOOL_RESULT") ? "" : '<div class="small bad-ink">no TOOL_RESULT: the worker died before the bank\'s reply — the bank had already paid</div>'}</div>` : ""}
      <div class="card"><table class="lint plain"><thead><tr><th>step</th><th>truth</th><th>where the run log says so</th></tr></thead><tbody>
        ${STEPS_().map((s) => `<tr class="${s.crash ? "hl" : ""}"><td class="small"><b>${s.n}</b> ${esc(s.what)}</td><td class="small">${esc(s.truth)}</td><td class="small">${esc(s.how)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function journalView() {
    const j = S.journal || {};
    if (!j.journal && !j.wal) return running() ? waiting("The run journals") : empty("The run journals");
    const box = (c, title) => j[c] ? `<div class="card"><h3 style="margin-top:0">${title} <span class="muted small">(${COPY_ARMS[c]}) — <code>fact add</code></span></h3>
      ${j[c].entries.map((e) => { const m = e.match(/step (\d+) \([^)]*\) (started|completed|failed)/); return `<div class="copyrow"><span class="k">${m ? `step ${m[1]} · ${m[2]}` : ""}</span><span class="small">${esc(e)}</span></div>`; }).join("")}
      <div class="copyrow"><span class="k">coverage</span><span class="small"><b>${esc(j[c].coverage)}</b></span></div></div>` : "";
    return `<h1>Two journals, neither with a result for step 4</h1>
      <p class="lead">Both were written by the app as the run went on (replayed here). The completion journal writes when a step finishes — so step 4, whose reply never came, has no entry, and its coverage line says that means “not completed”. The write-ahead journal writes a started entry with the idempotency key before each step, so step 4 is visibly started-without-a-result.</p>
      <div class="grid2">${box("journal", "Completion journal")}${box("wal", "Write-ahead journal")}</div>`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What memory says") : empty("What memory says");
    const c = S.census.copies;
    const state = (r) => r.done.length ? "done" : r.failed.length ? "failed" : r.started.length ? "started" : "silent";
    const cell = (k, s) => { const r = c[k].steps[s.n], st = state(r);
      if (st === "silent") return `<span class="small muted">nothing about it</span>${s.crash ? ' <span class="small bad-ink">— yet the bank paid</span>' : ""}`;
      if (st === "started") return `<div class="small warn-ink">◐ ${esc(r.started[0])} <span class="muted">(no result after it)</span></div>`;
      return r.done.slice(0, 1).map((f) => `<div class="small ${s.done ? "" : "bad-ink"}">${s.done ? "·" : "✗"} ${esc(f)}</div>`).join("")
        + r.failed.slice(0, 1).map((f) => `<div class="small ${s.done ? "bad-ink" : ""}">${s.done ? "✗" : "·"} ${esc(f)}</div>`).join(""); };
    const s4 = (k) => state(c[k].steps[4]);
    return `<h1>Step 4: ${s4("journal") === "silent" ? "nothing" : esc(s4("journal"))} in the completion journal (A · B) · ${s4("wal") === "started" ? "started, no result" : esc(s4("wal"))} in the write-ahead journal (C)</h1>
      <p class="lead">Every fact in each copy's project, read for the seven steps. In the completion journal step 4 — paid — looks exactly like steps 5–7 — never reached, and the coverage line says what that means: not completed.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>step · truth</th>${COPIES.map((k) => `<th>${esc((S.data?.copies || {})[k] || k)} <span class="muted">(${COPY_ARMS[k]})</span></th>`).join("")}</tr></thead><tbody>
      ${STEPS_().map((s) => `<tr class="${s.crash ? "hl" : ""}"><td class="small"><b>${s.n}</b> ${esc(s.what)}<br><span class="muted">${esc(s.truth)} · ${esc(s.how)}</span></td>${COPIES.map((k) => `<td>${cell(k, s)}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Resume check") : empty("Resume check"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>Resume check — A: ${t.A.ok} of ${t.A.calls - t.A.empty} right · B: ${t.B.ok} · C: ${t.C.ok}</h1>
      <p class="lead">“Has step N already been done?” for each of the seven steps through the copy's boundary, ${RUNS()} answers each at temperature 0.7, plus one call without a boundary as the ∅ baseline. Only the reply's first word counts: Yes, No, Unknown. On step 4, Unknown is right: check the bank by the key before retrying.</p>
      ${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(ARM(a))}</h3><table class="lint plain"><thead><tr><th>step</th><th>truth</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${STEPS_().map((s) => { const rs = S.rows[a].filter((r) => r.step === s.n); const bad = rs.find((r) => r.verdict && r.verdict !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small"><b>${s.n}</b> ${esc(s.what)}</td><td class="small">${esc(s.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div>${bad && consequence(s, bad.verdict) ? `<div class="small bad-ink">→ the resume ${esc(consequence(s, bad.verdict))}</div>` : ""}</td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function reconcileView() {
    const rc = S.reconciled;
    if (!rc) return running() ? waiting("Reconcile") : empty("Reconcile");
    const d = S.data || {};
    return `<h1>C: step 4 is in doubt — the app asks the bank by its key, writes the answer, asks again</h1>
      <p class="lead">Only the write-ahead journal has something to reconcile with: the started entry carries the idempotency key the transfers were sent with. The completion journal has no key and no doubt — just “not completed”.</p>
      <div class="card"><h3 style="margin-top:0">Bank lookup</h3>${Object.entries(d.bank || {}).map(([k, v]) => `<div class="small mono">${esc(k)} → ${esc(JSON.stringify(v))}</div>`).join("")}
        ${rc.steps.length ? `<div class="copyrow" style="margin-top:10px"><span class="k">fact add</span><span class="small">${esc(d.reconciled)}</span></div>` : '<p class="small muted">nothing in doubt</p>'}</div>
      ${S.after.length ? `<div class="card"><h3 style="margin-top:0">Asked again through C</h3><div class="cell-ans">${S.after.map(markOf).join(" ")}</div><div class="small muted">${esc((S.after.find((r) => r.reply) || {}).reply || "")}</div></div>` : ""}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score, bs = sc.by_step || {}, st = sc.by_state || {};
    const cellc = (x) => x ? (Object.keys(MARKS).filter((v) => x[v]).map((v) => `<span class="${v === "ok" ? "" : v === "hold" ? "muted" : "bad-ink"}">${MARKS[v][0]}${x[v]}</span>`).join(" ") + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "")) || "–" : "–";
    const R = (a) => `${sc.answers[a].ok}/${sc.answers[a].calls - sc.answers[a].empty}`;
    const n4 = (a) => S.rows[a].filter((r) => r.step === 4 && r.verdict && !r.empty).length;
    const ROWS = [["done|done", "says done", "done"], ["silent|done", "nothing about it", "done"], ["started|done", "started, no result", "done"], ["silent|not done", "nothing about it", "not done"]];
    return `<h1>Right: A ${R("A")} · B ${R("B")} · C ${R("C")} — A pays the contractors twice ${sc.side_effects.A} of ${n4("A")} times</h1>
      <p class="lead">✓ right · ✗ “No” on a finished step: the resume re-runs it · ↷ “Yes” on an unfinished step: the resume skips it · ‖ “Unknown” on a step memory settles: the resume stops to check for nothing · ∅ MemoryRouter attached nothing (left out of the counts).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th>answers (${STEPS_().length} × ${RUNS()})</th><th>step 4</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr class="${a === "A" ? "hl" : ""}"><td><b>${esc(ARM(a))}</b></td><td>${tline(sc.answers[a])}</td><td class="small">${esc((sc.step4 || {})[a] || "")}</td></tr>`).join("")}
        ${(sc.step4 || {})["C after reconcile"] ? `<tr><td><b>C after the bank lookup is written</b></td><td></td><td class="small">${esc(sc.step4["C after reconcile"])}</td></tr>` : ""}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">By what memory held about the step</h3><table class="lint plain"><thead><tr><th>memory</th><th>step</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${ROWS.map(([k, m, t]) => `<tr class="${k === "silent|done" ? "hl" : ""}"><td class="small">${m}</td><td class="small">${t}</td>${ARMS.map((a) => `<td class="num">${cellc((st[a] || {})[k])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">In the completion journal “nothing about it” is the same memory for step 4 — paid — and steps 5–7 — never reached, and the coverage line turns both into “not completed”. Offering Unknown (B) does not help: memory does not look uncertain, it says “not completed”. The write-ahead journal makes the crash window visible as started-without-a-result, while 5–7 stay a plain No.</p></div>
      <div class="card"><h3 style="margin-top:0">Per step</h3><table class="lint plain"><thead><tr><th>step</th><th>truth</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${STEPS_().map((s) => `<tr><td class="small"><b>${s.n}</b> ${esc(s.what)}</td><td class="small">${esc(s.truth)}</td>${ARMS.map((a) => `<td class="num">${cellc((bs[s.n] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — a step, through A, B and C</h1>
      <p class="lead">Pick a step: the resume check asks “has step N already been done?” through each arm, graded. Or type any question.</p>
      <div class="card"><label for="t-q">Step number or question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${STEPS_().map((s) => `<button class="chip" data-q="${s.n}">${s.n} · ${esc(s.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">resume check</span>${x.step ? `Has step ${x.step} (${esc(stepOfN(x.step).what)}) already been done?` : esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.verdict === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(ARM(r.arm))} · ${r.empty ? "∅ nothing attached" : r.verdict ? `${markOf(r)} ${esc(MARKS[r.verdict][2])}` : ""}</span>${esc(r.reply)}${r.consequence ? `<div class="small bad-ink">→ the resume ${esc(r.consequence)}</div>` : ""}</div>`).join("")}
          ${x.truth ? `<p class="small muted">truth: step ${x.step} — ${esc(x.truth)}</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, truth: r.truth, step: r.step });
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
    if (!confirm("Delete the demo's boundaries, conversations, actors (their facts first) and the two projects?")) return;
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
    else if (k === "run_log") S.runSeen = true;
    else if (k === "stored") S.stored = d;
    else if (k === "journal") S.journal = { ...S.journal, [d.copy]: d };
    else if (k === "reconciled") S.reconciled = d;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") { if (d.phase === "reconciled") S.after.push(...d.rows); else S.rows[d.arm].push(...d.rows); }
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the two projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any step through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "run_log", "stored", "journal", "reconciled", "census", "ask_row", "arm_done",
    "example", "score", "summary", "router_unavailable", "asked", "memory_view", "done", "error"];
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

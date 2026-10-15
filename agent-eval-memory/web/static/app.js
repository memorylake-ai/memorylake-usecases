/* Agent eval memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "What it knows", sub: "60 support facts", view: "memory" },
    { n: 4, title: "Eval v1 vs v2", sub: "through MemoryRouter", view: "ab" },
    { n: 5, title: "A/A", sub: "v1 again, nothing changed", view: "aa" },
    { n: 6, title: "Pin the memory", sub: "one snapshot per question", view: "snapshot" },
    { n: 7, title: "Eval vs snapshot", sub: "memory held still", view: "pinned" },
    { n: 8, title: "Why it failed", sub: "attribution + scoreboard", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, rows: [], aa: null, snapshot: null, pinRows: [], pinned: null, attribution: null, score: null,
    unavailable: null, summary: null, replays: [], rq: "Q2", rprompt: "v2", rarm: "pinned", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const Q = () => S.data?.questions || [];
  const RUNS = () => S.data?.runs || 3;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "replay" ? "active" : ""}" data-view="replay"><span class="n">↻</span><span class="t">Replay<small>one question, one call</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, ab, aa, snapshot, pinned, score, replay }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (ok) => ok == null ? '<span class="mark">·</span>' : `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;
  const cellRuns = (rs) => rs.map((r) => `${markOf(r.ok)}<span class="tok">${r.prompt_tokens || "–"}</span>`).join(" ");
  const flipped = (rs) => new Set(rs.filter((r) => r.ok != null).map((r) => r.ok)).size > 1;
  const moved = (rs) => new Set(rs.filter((r) => r.prompt_tokens).map((r) => r.prompt_tokens)).size > 1;
  const routerRows = (qid, prompt) => S.rows.filter((r) => r.q === qid && r.prompt === prompt);
  const pinRows = (qid, prompt) => S.pinRows.filter((r) => r.q === qid && r.prompt === prompt);
  const why = (r) => r.ok === false ? [...(r.missing || []).map((m) => "missing: " + m), ...(r.wrong || []).map((w) => "wrong: " + w)].join("; ") : "";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-aev")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Did prompt v2 break the answer — or did the memory change?</h1>
      <p class="lead">Helio (fictional) runs a support assistant on MemoryRouter: 60 support facts, one boundary. The team grades it on 8 questions and wants to know whether
      prompt v2 beats v1. Through MemoryRouter, the memory attached to the same question can differ from one call to the next — and the reply never says which.
      This demo runs the eval as wired in production, runs it again with nothing changed, then pins one memory snapshot per question and runs both prompts against it.
      It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 80 calls on gpt-4o-mini per run (about 16,000 prompt tokens).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-aware-evaluation-for-agent-outputs" target="_blank" rel="noopener">memorylake.ai › Memory-aware evaluation for agent outputs</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>One project, one boundary — the assistant as it runs in production</h1>
      <p class="lead">The assistant never assembles memory itself: every message is an ordinary model call with <code>--boundary</code> added, and MemoryRouter picks
      which of the project's facts to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(w?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(w?.boundary || "pending")}</b></dd></dl>
        <div class="cmdline">boundary create --projects &lt;Helio support knowledge&gt;</div></div>
        <div class="card"><h3 style="margin-top:0">The two prompts under test</h3>
          ${["v1", "v2"].map((p) => `<p class="small"><b>${p}</b> — ${esc(S.data?.prompts?.[p] || "")}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What the assistant knows") : empty("What the assistant knows");
    const m = S.memory;
    return `<h1>${m.count} support facts, ${Object.keys(m.topics).length} topics</h1>
      <p class="lead">Stored verbatim with <code>fact add</code>: ${Object.entries(m.topics).map(([t, n]) => `${esc(t)} ${n}`).join(" · ")}.</p>
      <div class="card">${m.sample.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}
        <div class="memfact small"><span class="muted">… and ${m.count - m.sample.length} more</span></div></div>`;
  }

  function ab() {
    const h = "The eval as wired in production";
    if (!S.rows.some((r) => r.phase === "ab")) return unavailable() || (running() ? waiting(h) : empty(h));
    const n = Q().length, ok = (p) => S.rows.filter((r) => r.phase === "ab" && r.prompt === p && r.ok).length;
    const verdict = (kind) => Q().filter((q) => { const a = routerRows(q.id, "v1")[0], b = routerRows(q.id, "v2")[0];
      return a && b && (kind === "broke" ? a.ok && b.ok === false : a.ok === false && b.ok); }).map((q) => q.id);
    return `<h1>${h} — v1 ${ok("v1")}/${n} · v2 ${ok("v2")}/${n}</h1>
      <p class="lead"><code>router chat --boundary</code> (gpt-4o-mini, temperature 0), one call per question and prompt. Each answer is graded against the facts it must state.
      Read naively: v2 broke <b>${verdict("broke").join(", ") || "nothing"}</b> and fixed <b>${verdict("fixed").join(", ") || "nothing"}</b>. The small number after each mark is the call's prompt tokens.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>v1</th><th>v2</th><th></th></tr></thead><tbody>
      ${Q().map((q) => { const a = routerRows(q.id, "v1")[0], b = routerRows(q.id, "v2")[0]; if (!a && !b) return "";
        const flag = a && b && a.ok !== b.ok ? (a.ok ? '<b class="bad-ink">v2 broke it?</b>' : '<b>v2 fixed it?</b>') : "";
        return `<tr><td><b>${esc(q.id)}</b> ${esc(q.topic)}<div class="small muted">${esc(q.say)}</div></td><td>${a ? cellRuns([a]) : ""}</td><td>${b ? cellRuns([b]) : ""}</td><td>${flag}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function aa() {
    const h = "A/A — the same eval again, nothing changed";
    const have = Q().filter((q) => routerRows(q.id, "v1").length >= RUNS());
    if (!have.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const nm = have.filter((q) => moved(routerRows(q.id, "v1"))).length, nf = have.filter((q) => flipped(routerRows(q.id, "v1"))).length;
    return `<h1>A/A — ${nm}/${have.length} questions got different memory, ${nf}/${have.length} changed grade</h1>
      <p class="lead">Prompt v1, ${RUNS()} runs per question: same prompt, same 60 facts, same boundary. The reply does not say which memories MemoryRouter attached —
      the prompt-token count is the only trace. When the count moves, the memory moved.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>v1 × ${RUNS()} · prompt tokens</th><th></th></tr></thead><tbody>
      ${have.map((q) => { const rs = routerRows(q.id, "v1"), m = moved(rs), f = flipped(rs);
        return `<tr class="${f ? "hl" : ""}"><td><b>${esc(q.id)}</b> ${esc(q.topic)}</td><td>${cellRuns(rs)}</td>
          <td>${f && m ? '<b class="bad-ink">memory differed, grade flipped</b>' : f ? '<b class="bad-ink">grade flipped</b>' : m ? '<span class="muted">memory differed</span>' : ""}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function snapshot() {
    const h = "Pin the memory";
    if (!S.snapshot) return running() ? waiting(h) : empty(h);
    const kb = S.data.kb;
    return `<h1>${h} — one snapshot per question, ${S.snapshot.k} facts each</h1>
      <p class="lead"><code>search --types fact --top-k ${S.snapshot.k}</code> once per question; the fact ids and text are saved to <code>out/snapshots.json</code> (pinned ${esc(S.snapshot.pinned_at)}).
      Every prompt version gets the same snapshot in its system prompt, with no boundary — nothing is picked per call. <span class="lab lab-ok">needed</span> marks the facts the grader looks for.</p>
      <div class="grid2">${Q().map((q) => { const snap = S.snapshot.questions[q.id] || [], keys = new Set(snap.map((s) => s.key));
        const need = [...new Set(q.must.flatMap((m) => m.facts))], missing = need.filter((k) => !keys.has(k));
        return `<div class="card"><h3 style="margin-top:0">${esc(q.id)} ${esc(q.topic)} <span class="muted small">grader's facts ${need.length - missing.length}/${need.length} in it</span></h3>
          ${snap.map((s) => `<div class="memfact small"><span>${need.includes(s.key) ? '<span class="lab lab-ok">needed</span> ' : ""}<span class="mono faint">${esc(s.key)}</span> ${esc(s.text)}</span></div>`).join("")}
          ${missing.map((k) => `<div class="memfact small wrong"><span><span class="lab lab-bad">not retrieved</span> <span class="mono faint">${esc(k)}</span> ${esc(kb[k])}</span></div>`).join("")}</div>`; }).join("")}</div>`;
  }

  function pinned() {
    const h = "The eval against the snapshot";
    const have = Q().filter((q) => pinRows(q.id, "v1").length + pinRows(q.id, "v2").length > 0);
    if (!have.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const p = S.pinned;
    return `<h1>${h}${p ? ` — memory moved on ${p.moved}/${Q().length} · v1 flipped on ${p.flipped}/${Q().length}` : ""}</h1>
      <p class="lead">${RUNS()} × v1 and ${RUNS()} × v2 per question, all against that question's snapshot. The prompt tokens stop moving: the memory is identical, call after call.
      ${p ? `Questions where v1 and v2 disagree (majority of ${RUNS()}): <b>${esc(p.differ.join(", ") || "none")}</b> — with the memory fixed, that difference is the prompt.` : ""}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>v1 × ${RUNS()}</th><th>v2 × ${RUNS()}</th></tr></thead><tbody>
      ${have.map((q) => `<tr><td><b>${esc(q.id)}</b> ${esc(q.topic)}</td><td>${cellRuns(pinRows(q.id, "v1"))}</td><td>${cellRuns(pinRows(q.id, "v2"))}</td></tr>`).join("")}
      </tbody></table>${p && p.flipped ? '<p class="small muted">A flip with identical prompt tokens is the model\'s own sampling noise — now visible apart from the memory.</p>' : ""}</div>`;
  }

  function score() {
    if (!S.score || !S.attribution) return running() ? waiting("Why it failed") : empty("Why it failed");
    const sc = S.score, a = S.attribution, n = Q().length, kb = S.data.kb;
    const pinFails = Q().map((q) => ({ q, rows: a.rows.filter((r) => r.q === q.id && r.arm === "pinned") })).filter((x) => x.rows.length);
    const ru = a.rows.filter((r) => r.arm === "router");
    const cause = (q) => [...new Set(a.rows.filter((r) => r.q === q.id && r.arm === "pinned").map((r) => r.cause))].join(", ") || "–";
    return `<h1>Every snapshot failure has a cause — wrong answers explained: MemoryRouter 0/${a.counts.unknown} · snapshot ${a.counts.retrieval + a.counts.generation}/${a.counts.retrieval + a.counts.generation}</h1>
      <p class="lead">Against a snapshot, a wrong answer is one of two things: the fact the grader needs is <b>not in the snapshot</b> (retrieval failure) or it is there and
      <b>the answer left it out</b> (generation failure). Through MemoryRouter the call kept no record of what it was given, only a prompt-token count.</p>
      <div class="grid2">${pinFails.map(({ q, rows }) => `<div class="card"><h3 style="margin-top:0">${esc(q.id)} ${esc(q.topic)} <span class="pill ${rows[0].cause === "retrieval" ? "err" : "busy"}">${esc(rows[0].cause)} failure</span></h3>
          <p class="small muted">${rows.length} wrong answer(s) against the snapshot · ${esc(rows[0].why)}</p>
          ${rows[0].facts.map((k) => `<div class="memfact small"><span><span class="lab ${rows[0].cause === "retrieval" ? "lab-bad" : "lab-ok"}">${rows[0].cause === "retrieval" ? "not in the snapshot" : "in the snapshot"}</span> ${esc(kb[k])}</span></div>`).join("")}</div>`).join("")}
        ${ru.length ? `<div class="card"><h3 style="margin-top:0">Through MemoryRouter <span class="pill err">cause unknown</span></h3>
          <p class="small muted">${ru.length} wrong answer(s) in steps 4–5 (${esc([...new Set(ru.map((r) => r.q))].join(", "))}). The reply carries the answer and a prompt-token count — not the memories it was given.</p></div>` : ""}</div>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>Router v1 × ${RUNS()}</th><th>Router v2</th><th>snapshot v1 × ${RUNS()}</th><th>snapshot v2 × ${RUNS()}</th><th>cause</th></tr></thead><tbody>
        ${Q().map((q) => `<tr><td><b>${esc(q.id)}</b> ${esc(q.topic)}</td><td>${routerRows(q.id, "v1").map((r) => markOf(r.ok)).join(" ")}</td><td>${S.rows.filter((r) => r.q === q.id && r.prompt === "v2").map((r) => markOf(r.ok)).join(" ")}</td>
          <td>${pinRows(q.id, "v1").map((r) => markOf(r.ok)).join(" ")}</td><td>${pinRows(q.id, "v2").map((r) => markOf(r.ok)).join(" ")}</td><td>${esc(cause(q))}</td></tr>`).join("")}
        <tr class="hl"><td><b>same memory every call</b></td><td colspan="2">${n - sc.router_moved}/${n} questions</td><td colspan="2">${n - sc.pinned_moved}/${n} questions</td><td></td></tr>
        <tr><td>A/A: grade changed, nothing else did</td><td colspan="2">${sc.router_flipped}/${n}</td><td colspan="2">${sc.pinned_flipped}/${n}</td><td></td></tr>
        <tr><td>wrong answers with a known cause</td><td colspan="2">0/${a.counts.unknown}</td><td colspan="2">${a.counts.retrieval + a.counts.generation}/${a.counts.retrieval + a.counts.generation} (${a.counts.retrieval} retrieval, ${a.counts.generation} generation)</td><td></td></tr></tbody></table></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function replay() {
    const ready = !!S.world && !!S.snapshot;
    const chips = (name, vals, cur, label) => `<div class="chips">${vals.map((v) => `<button type="button" class="chip ${cur === v ? "on" : ""}" data-${name}="${esc(v)}">${esc(label ? label(v) : v)}</button>`).join("")}</div>`;
    const q = Q().find((x) => x.id === S.rq);
    return `<h1>Replay one question</h1>
      <p class="lead">One call, graded: against the question's pinned snapshot, or through MemoryRouter as in production. Replay the same thing a few times and watch the prompt tokens.</p>
      <div class="card">${chips("q", Q().map((x) => x.id), S.rq)}${chips("prompt", ["v1", "v2"], S.rprompt)}${chips("arm", ["pinned", "router"], S.rarm, (v) => v === "pinned" ? "against the snapshot" : "through MemoryRouter")}
        <p class="small">${q ? esc(q.say) : ""}</p>
        <div class="row"><button class="btn primary" data-act="replay" ${ready && !running() ? "" : "disabled"}>Replay</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="replay-log">${S.replays.slice().reverse().map((r) => `<div class="msg bot ${r.ok === false ? "wrong" : ""}"><span class="who">${esc(r.q)} · ${esc(r.prompt)} · ${r.arm === "pinned" ? "snapshot" : "MemoryRouter"} ${markOf(r.ok)} <span class="tok">${r.prompt_tokens} prompt tokens</span>${r.ok === false ? ` <span class="bad-ink small">${esc(why(r))}</span>` : ""}</span>${esc(r.reply)}</div>`).join("")}</div></div>`;
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
    st.querySelectorAll(".chip[data-q]").forEach((c) => c.addEventListener("click", () => { S.rq = c.dataset.q; renderStage(); }));
    st.querySelectorAll(".chip[data-prompt]").forEach((c) => c.addEventListener("click", () => { S.rprompt = c.dataset.prompt; renderStage(); }));
    st.querySelectorAll(".chip[data-arm]").forEach((c) => c.addEventListener("click", () => { S.rarm = c.dataset.arm; renderStage(); }));
    st.querySelector('[data-act="replay"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Calling…";
      const r = await api("/api/replay", { q: S.rq, prompt: S.rprompt, arm: S.rarm });
      if (r) S.replays.push(r);
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
  function resetView() { S.memory = null; S.rows = []; S.aa = null; S.snapshot = null; S.pinRows = []; S.pinned = null; S.attribution = null; S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary, its project and the 60 facts in it?")) return;
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
    else if (k === "router_row") S.rows.push(d);
    else if (k === "aa") S.aa = d;
    else if (k === "snapshot") S.snapshot = d;
    else if (k === "pinned_row") S.pinRows.push(d);
    else if (k === "pinned") S.pinned = d;
    else if (k === "attribution") S.attribution = d;
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the project and its facts are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Replay: one question, one call, against the snapshot or through MemoryRouter.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "router_row", "aa", "snapshot", "pinned_row", "pinned", "attribution", "scoreboard", "summary", "router_unavailable", "done", "error"];
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

/* Webhook order memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["A", "B", "C"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "Webhook deliveries", sub: "at least once, out of order", view: "deliveries" },
    { n: 4, title: "What memory says", sub: "the latest fact per account", view: "census" },
    { n: 5, title: "C: app state", sub: "applied by event time", view: "state" },
    { n: 6, title: "Ask the assistant", sub: "three memories, eleven questions", view: "ask" },
    { n: 7, title: "Score", sub: "per question and per memory", view: "score" },
  ];
  const fresh = () => ({ deliveriesSeen: false, census: null, state: null, rows: { A: [], B: [], C: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "How many seats does Quillmere have now?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const CUST = () => S.data?.customers || [];
  const QS = () => S.data?.questions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;
  const day = (iso) => { const d = new Date(iso); return d.toLocaleString("en-GB", { timeZone: "UTC", weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, deliveries: deliveriesView, census: censusView, state: stateView, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the current value"], stale: ["↺", "bad", "the value before the newer event — the late retry's"],
    unknown: ["?", "warn", "Unknown"], other: ["–", "warn", "neither value"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  const ORDER = () => Object.fromEntries(QS().map((q) => [q.id, q.order]));
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const o = ORDER(), live = rows.filter((r) => r.grade && !r.empty), c = (xs, g) => xs.filter((r) => r.grade === g).length;
    const part = (p) => live.filter((r) => o[r.item] === p);
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c(live, "ok"), stale: c(live, "stale"), unknown: c(live, "unknown"),
      other: c(live, "other"), late_n: part("late").length, late_ok: c(part("late"), "ok"), inorder_n: part("inorder").length, inorder_ok: c(part("inorder"), "ok"),
      history_n: part("history").length, history_ok: c(part("history"), "ok") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `current <b>${t.ok}</b>/${t.n} · ↺ stale <b class="${t.stale ? "bad-ink" : ""}">${t.stale}</b> · Unknown <b>${t.unknown}</b>
    · late-retry customers <b>${t.late_ok}</b>/${t.late_n} · in order <b>${t.inorder_ok}</b>/${t.inorder_n} · history <b>${t.history_ok}</b>/${t.history_n}${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const STATE = { current: ["current", "lab-ok"], stale: ["STALE", "lab-bad"], tie: ["tie", "lab-bad"], missing: ["missing", "lab-bad"] };
  const stateLab = (s) => s ? `<span class="lab ${STATE[s][1]}">${STATE[s][0]}</span>` : "";
  const DATE = /(\((?:as of|on) [^)]*\)|\bpreviously\b)/g;
  const hiDate = (t) => esc(t).replace(DATE, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.delivered)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes (40 webhook messages read one at a time, then ~110 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-wom")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A late webhook retry turns the account in memory back to how it was.</h1>
      <p class="lead">${esc(d.org || "Tallowby")} (a fictional SaaS company) writes its payment provider's webhooks into memory, and its billing assistant answers from that memory through MemoryRouter. Webhooks arrive at least once and not in order: after a day-long outage, eight customers' older events were retried 27 hours after the newer ones had arrived.
      Written the way most handlers write them — stamped with the time they arrived — the old value becomes the newest fact. Three memories: <b>A</b> stamped when each event arrived · <b>B</b> stamped when it was created · <b>C</b> B plus the app's own state facts, applied by event time. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 110 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-webhook-triggered-agents" target="_blank" rel="noopener">memorylake.ai › Memory for webhook-triggered agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, one boundary each</h1>
      <p class="lead">A and B get the same webhook messages; only the timestamp differs. C gets every fact MemoryLake extracted into B, copied word for word, plus one state fact per customer that the app writes — so B and C differ only by those facts. All three carry the same plain name: a project's name is context for extraction.</p>
      <div class="card"><dl class="kv"><dt>A · as arrived</dt><dd class="mono small">${esc(w?.projects?.delivered || "pending")}</dd><dt>B · as created</dt><dd class="mono small">${esc(w?.projects?.created || "pending")}</dd>
        <dt>C · + app state</dt><dd class="mono small">${esc(w?.projects?.state || "pending")}</dd><dt>webhook actor</dt><dd class="mono small">${esc(w?.actors?.bot || "pending")}</dd></dl></div>`;
  }

  function deliveriesView() {
    if (!S.data || (!S.deliveriesSeen && !S.world)) return running() ? waiting("Webhook deliveries") : empty("Webhook deliveries");
    const d = S.data, rows = CUST().flatMap((c) => d.deliveries.filter((x) => x.customer === c.id).sort((a, b) => a.created < b.created ? -1 : 1));
    return `<h1>Twenty events — eight of the older ones arrive after the newer ones</h1>
      <p class="lead">${esc(d.outage)} One GROUP conversation per customer; the events are written in the order they arrived, each read before the next. A stamps each message with the time it arrived, B with the time the event was created. The text is the same in both — the event, not its creation time, the way most handlers write it:</p>
      <div class="card mono small">${esc(d.sample)}</div>
      <div class="card"><table class="lint plain"><thead><tr><th>customer</th><th>event</th><th>value</th><th>created</th><th>arrived</th></tr></thead><tbody>
      ${rows.map((x) => `<tr class="${x.retry ? "hl" : ""}"><td class="small"><b>${esc(x.name)}</b></td><td class="small mono">${esc(x.type)}</td><td class="small">${esc(x.value)}</td>
        <td class="small mono">${esc(day(x.created))}</td><td class="small mono">${esc(day(x.delivered))}${x.retry ? ' <span class="lab lab-bad">retry, after the newer one</span>' : ""}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function censusView() {
    if (!S.census) return running() ? waiting("What memory says") : empty("What memory says");
    const cs = S.census, tA = cs.delivered.tally, tB = cs.created.tally;
    const shown = (s) => (s.rewritten?.[0] || s.top?.[0] || s.facts?.[0] || "");
    return `<h1>A: the latest fact is the old value for ${tA.stale} of 8 · B: ${tB.stale} of 8</h1>
      <p class="lead"><code>fact list</code> on each project. For each customer, the fact with the latest date — the <span class="stamp">(as of …)</span> MemoryLake writes from the message time — and the value it states. In A the retried event carries the day it arrived, so the old value is the newest thing memory knows${tA.rewritten ? `; for ${tA.rewritten} customer(s) the current fact itself was rewritten to the old value, with the new one as <span class="stamp">previously</span>` : ""}.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>customer</th><th>true now</th><th>A · as arrived</th><th>B · as created</th></tr></thead><tbody>
      ${CUST().map((c) => { const a = cs.delivered.customers[c.id], b = cs.created.customers[c.id];
        return `<tr class="${a.state !== "current" || b.state !== "current" ? "hl" : ""}"><td class="small"><b>${esc(c.name)}</b><br><span class="muted">${esc(c.field)}${c.order === "late" ? "" : " · arrived in order"}</span></td>
          <td class="small">${esc(c.current)}</td>
          <td class="small">${stateLab(a.state)}${a.state !== "current" ? `<div class="muted">${hiDate(shown(a))}</div>` : ""}</td>
          <td class="small">${stateLab(b.state)}${b.state !== "current" ? `<div class="muted">${hiDate(shown(b))}</div>` : ""}</td></tr>`; }).join("")}
      </tbody></table><p class="small" style="margin-bottom:0">A ${tA.facts} facts · B ${tB.facts} facts · in-order customers current: A ${tA.control}/2 · B ${tB.control}/2</p></div>`;
  }

  function stateView() {
    if (!S.state) return running() ? waiting("C: app state") : empty("C: app state");
    const p = S.state, ign = p.log.filter((x) => !x.applied).length;
    return `<h1>C: ${p.copied} facts copied from B — then ${p.pins.length} state facts and ${(p.history || []).length} history facts the app keeps</h1>
      <p class="lead">The handler processes deliveries in the order they arrive and keeps, per customer and field, the value of the most recently <i>created</i> event. An event created earlier than the one already applied is logged — ${ign} this time — and does not change the state — and is written down as history, because it did happen. One <code>fact add</code> call writes both.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arrived</th><th>customer</th><th>event</th><th>created</th><th></th></tr></thead><tbody>
      ${p.log.map((x) => `<tr class="${x.applied ? "" : "hl"}"><td class="small mono">${esc(day(x.delivered))}</td><td class="small"><b>${esc(x.name)}</b></td><td class="small mono">${esc(x.type)}</td>
        <td class="small mono">${esc(day(x.created))}</td><td class="small">${x.applied ? `applied → ${esc(x.value)}` : `<span class="lab lab-bad">not applied</span> older than ${esc(x.kept)}`}</td></tr>`).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0">State facts</h3>${p.pins.map((t) => `<div class="copyrow small">${esc(t)}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">History facts — one per event not applied</h3>${(p.history || []).map((t) => `<div class="copyrow small">${esc(t)}</div>`).join("")}</div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The billing assistant — current value: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].n}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7, plus one call without a boundary as the ∅ baseline. ↺ marks the value from before the newer event — the one the late retry carried.</p>
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
      <p class="lead">${QS().length * RUNS()} answers per memory. The split that matters: the eight customers whose older event arrived late; the two whose events arrived in order and the history question — where B and C must do no worse than A.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>current</th><th class="num">↺ stale</th><th class="num">Unknown</th><th>late-retry customers</th><th>in order</th><th>history</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].stale ? "bad-ink" : ""}">${A[a].stale}</b></td><td class="num">${A[a].unknown}</td>
          <td><b>${A[a].late_ok}/${A[a].late_n}</b>${bar(A[a].late_ok, A[a].late_n, "g1")}</td><td><b>${A[a].inorder_ok}/${A[a].inorder_n}</b></td><td><b>${A[a].history_ok}/${A[a].history_n}</b></td></tr>`).join("")}</tbody></table>
        ${ARMS.some((a) => A[a].empty) ? `<p class="small muted" style="margin-bottom:0">∅ (MemoryRouter attached nothing; listed, not graded): ${ARMS.map((a) => `${a} ${A[a].empty}`).join(" · ")}</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.what)} <span class="muted">= ${esc(q.current)}${q.order === "inorder" ? " · in order" : q.order === "history" ? " · history" : ""}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ the current value · ↺ the value before the newer event — the late retry's · ? Unknown · – neither · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C</h1>
      <p class="lead">Any question to the billing assistant, once through each memory. Graded when it is one of the eleven.</p>
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
    if (!confirm("Delete the demo's boundaries, conversations, the webhook actor (its facts first) and the three projects?")) return;
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
    else if (k === "deliveries") S.deliveriesSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "state") S.state = d;
    else if (k === "memory_view") S.census = d.census;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, the webhook actor (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the billing assistant, through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "deliveries", "census", "state", "ask_row", "arm_done", "example",
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

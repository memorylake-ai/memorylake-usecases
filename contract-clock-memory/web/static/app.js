/* Contract clock memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const COPIES = ["talk", "logged", "resolved"];
  const SHORT = { talk: "A", logged: "B", resolved: "C" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three copies, one boundary each", view: "setup" },
    { n: 3, title: "Four meetings", sub: "and what the app writes", view: "meetings" },
    { n: 4, title: "What memory holds", sub: "each contract clock", view: "census" },
    { n: 5, title: "The calendar", sub: "as of today", view: "board" },
    { n: 6, title: "Ask the assistant", sub: "through each boundary", view: "ask" },
    { n: 7, title: "Score", sub: "memory · calendar · answers", view: "score" },
  ];
  const fresh = () => ({ meetingsSeen: false, structured: null, census: null, board: null, rows: { talk: [], logged: [], resolved: [] }, examples: {}, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "When is the final payment to Tamsett due?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 2;
  const ITEMS = () => S.data?.clocks || [];
  const LABEL = (c) => (S.data?.copies || {})[c] || c;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, meetings, census, board, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the right due date"], off: ["✗", "bad", "another date"], calc: ["~", "warn", "only “N days from <date>”, never worked out"], nodate: ["?", "warn", "the clock, with no date"], none: ["·", "", "not remembered"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  const ansOf = (r) => `<span class="ans">${markOf(r)} ${esc(r.got || (r.empty ? "" : "no date"))}${r.off ? `<span class="off">${esc(r.off)}</span>` : ""}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.verdict), n = (v) => live.filter((r) => !r.empty && r.verdict === v).length;
    return { calls: live.length, empty: live.filter((r) => r.empty).length, ok: n("ok"), off: n("off"), nodate: n("nodate") + n("calc") };
  }
  const tline = (t) => `<b>${t.ok}</b> right · <b class="${t.off ? "bad-ink" : ""}">${t.off}</b> wrong date · <b>${t.nodate}</b> no date · <b>${t.empty}</b> ∅ <span class="muted">of ${t.calls}</span>`;
  const reEsc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const relHi = (t) => {
    const words = [...ITEMS().map((k) => k.said), "today", "this morning"].sort((a, b) => b.length - a.length);
    return esc(t).replace(new RegExp("\\b(" + words.map(reEsc).join("|") + ")\\b", "gi"), (m) => `<span class="stamp">${m}</span>`);
  };
  const day = (iso) => { if (!iso) return "–"; const d = new Date(iso + "T12:00:00Z"); return d.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" }) + " " + iso; };

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.talk)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (extraction, then ~40 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-ckm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The clock that starts on an event</h1>
      <p class="lead">${esc(d.org || "Fernbeck Transit")} (a fictional transit authority) is replacing its fare system. On the day the vendor contract was signed, the procurement lead read the contract's clocks into the meeting:
      the pilot runs 90 days from go-live, the final payment is due 45 days after the pilot ends, the warranty runs 12 months from acceptance… Go-live, the defect report and acceptance happen in later meetings.
      MemoryLake remembers every clock and every event — as separate facts, and nothing joins them: memory holds no due date for those clocks.
      Three copies of the same four meetings: A the meetings only · B plus the clocks and milestones as structured facts · C plus the due dates, worked out by the app in code. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 40 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-long-term-projects" target="_blank" rel="noopener">memorylake.ai › AI memory for long-term projects</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Three copies of the same project memory, one boundary each</h1>
      <p class="lead">Each copy is its own project, so no copy's answers come from another's facts. The boundary covers the copy's project — what the programme assistant reads through MemoryRouter.</p>
      <div class="grid2"><div class="card"><dl class="kv">${COPIES.map((c) => `<dt>${esc(LABEL(c))}</dt><dd class="mono small">${esc(w?.projects?.[c] || "pending")}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">${esc(d.org)} PMO</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)}</div>`).join("")}
          <p class="small muted" style="margin-bottom:0">Vendor: ${esc(d.vendor)}</p></div></div>`;
  }

  const who = (k) => ((S.data?.people || []).find((p) => p.key === k) || {}).display || k;
  function meetings() {
    const d = S.data;
    if (!d || (!S.meetingsSeen && !S.world)) return running() ? waiting("Four meetings") : empty("Four meetings");
    const st = S.structured?.texts || d.structured || {};
    return `<h1>Four meetings over four months — the clocks in month one, the events that start them later</h1>
      <p class="lead">Every copy gets the same four meetings: one GROUP conversation each, every line as its speaker, at the meeting's time. The highlighted words are the clocks and the events.</p>
      <div class="grid2">${d.meetings.map((m) => `<div class="card"><div class="row spread"><b>${esc(m.id)} · ${esc(m.name)}</b><span class="mono small muted">${esc(m.at.slice(0, 10))}</span></div>
        ${m.lines.map(([k, t]) => `<div class="small" style="margin-top:6px"><b>${esc(who(k))}</b>: ${relHi(t)}</div>`).join("")}</div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">The clocks, and when each one runs out</h3><table class="lint plain"><thead><tr><th>clock</th><th>as said</th><th>starts</th><th>due</th></tr></thead><tbody>
        ${ITEMS().map((k) => `<tr><td class="small"><b>${esc(k.what)}</b></td><td class="small">“${esc(k.said)}”</td><td class="small">${esc(k.start_what)} <span class="mono">${esc(k.start)}</span></td><td class="ans">${esc(day(k.want))}</td></tr>`).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0">What the app writes itself</h3>
        <div class="copyrow"><span class="k"><span class="lab lab-bad">A</span></span><span class="small muted">nothing — the meetings only</span></div>
        <div class="copyrow"><span class="k"><span class="lab">B</span> ${(st.logged || []).length} facts</span><span class="small">${(st.logged || []).map((t) => `<div>${esc(t)}</div>`).join("")}</span></div>
        <div class="copyrow"><span class="k"><span class="lab lab-ok">C</span> ${(st.resolved || []).length} facts</span><span class="small">the same ${(st.logged || []).length}, plus:${(st.resolved || []).filter((t) => t.startsWith("Due date")).map((t) => `<div><b>${esc(t)}</b></div>`).join("")}</span></div></div>`;
  }

  const cellOf = (r) => r.verdict === "none" ? '<span class="small muted">not remembered</span>'
    : `<div>${r.verdict === "calc" ? `<span class="ans"><span class="mark warn" title="${MARKS.calc[2]}">~</span> ${esc(r.anchor)} + ?</span>` : ansOf(r)}</div><div class="small muted">${esc(r.facts[0] || "")}</div>`;
  function census() {
    if (!S.census) return running() ? waiting("What memory holds") : empty("What memory holds");
    const c = S.census.copies, tr = S.census.truth;
    const ok = (k) => Object.values(c[k].items).filter((r) => r.verdict === "ok").length;
    const ms = S.data?.milestones || [];
    return `<h1>Due dates in memory — A: ${ok("talk")} of ${ITEMS().length} · B: ${ok("logged")} · C: ${ok("resolved")}</h1>
      <p class="lead">Every fact in each copy, read for the six contract clocks. ✓ the right due date · ? the clock is remembered, with no date · ~ only “N days from &lt;date&gt;” · · not remembered.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>clock · as said · due</th>${COPIES.map((k) => `<th>${esc(LABEL(k))}</th>`).join("")}</tr></thead><tbody>
      ${ITEMS().map((it) => `<tr class="${c.talk.items[it.id].verdict !== "ok" ? "hl" : ""}"><td class="small"><b>${esc(it.what)}</b><br>“${esc(it.said)}”<br>due <b>${esc(day(tr[it.id]))}</b></td>${COPIES.map((k) => `<td>${cellOf(c[k].items[it.id])}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0">The events that start the clocks, as copy A remembers them</h3>
        ${ms.map((m) => { const f = (c.talk.events || {})[m.key] || []; return `<div class="copyrow"><span class="k">${esc(m.short)}</span><span class="small">${f.length ? esc(f[0]) : '<span class="muted">not remembered</span>'}</span></div>`; }).join("")}
        ${(c.talk.stale || []).length ? `<p class="small" style="margin-bottom:0">And from the day the contract was signed, still in memory: <i>“${esc(c.talk.stale[0])}”</i></p>` : ""}</div>`;
  }

  function board() {
    if (!S.board) return running() ? waiting("The calendar") : empty("The calendar");
    const b = S.board, t = b.tally;
    const cell = (x) => x.verdict === "calc" ? `<span class="mark warn">~</span> ${esc(x.anchor)} + ?` : x.got ? `${markOf(x)} ${esc(x.got)} <span class="muted">${esc(x.span)}</span>` : `${markOf(x)} <span class="muted">no date</span>`;
    return `<h1>As of ${esc(day(b.today))}: on A's calendar ${t.talk.on} of ${b.rows.length} · B's ${t.logged.on} · C's ${t.resolved.on}</h1>
      <p class="lead">The contract calendar the app builds from memory today. A clock is on it only when memory gives its due date.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>clock</th><th>due</th>${COPIES.map((c) => `<th>${SHORT[c]} calendar</th>`).join("")}</tr></thead><tbody>
      ${b.rows.map((r) => `<tr class="${r.copies.talk.verdict !== "ok" ? "hl" : ""}"><td class="small"><b>${esc(r.what)}</b></td><td class="small">${esc(r.want)} ${r.passed ? '<span class="lab">passed</span>' : '<span class="lab lab-bad">open</span>'}</td>
        ${COPIES.map((c) => `<td class="small">${cell(r.copies[c])}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0">What falls due this month? — <code>fact list --query ${esc(b.month)}</code></h3>
        ${COPIES.map((c) => `<div class="copyrow"><span class="k"><span class="lab">${SHORT[c]}</span> ${(b.month_hits[c] || []).length} facts</span><span class="small">${(b.month_hits[c] || []).map((f) => `<div>${esc(f)}</div>`).join("") || '<span class="muted">none</span>'}</span></div>`).join("")}</div>`;
  }

  function askView() {
    const all = COPIES.flatMap((c) => S.rows[c]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(COPIES.map((c) => [c, tally(S.rows[c])]));
    return `<h1>The programme assistant — A: ${t.talk.ok} of ${t.talk.calls} right · B: ${t.logged.ok} · C: ${t.resolved.ok}</h1>
      <p class="lead">Each question through the copy's boundary, ${RUNS()} answers each at temperature 0.7, plus one call without a boundary as the ∅ baseline. The system prompt gives today's date.</p>
      ${COPIES.map((c) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(c))}</h3><table class="lint plain"><thead><tr><th>question</th><th>due</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${ITEMS().map((it) => { const rs = S.rows[c].filter((r) => r.item === it.id);
          return `<tr class="${rs.some((r) => r.verdict !== "ok" && !r.empty) ? "hl" : ""}"><td class="small">${esc(it.ask)}</td><td class="ans">${esc(it.want)}</td><td><div class="cell-ans">${rs.map(ansOf).join("")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(tally(S.rows[c]))}</p>
        ${(S.examples[c] || []).map((r) => { const it = ITEMS().find((i) => i.id === r.item) || {};
          return `<div class="msg user"><span class="who">Rhea → assistant</span>${esc(it.ask)}</div><div class="msg bot ${r.verdict === "ok" ? "" : "wrong"}"><span class="who">reply ${markOf(r)}</span>${esc(r.reply)}</div>`; }).join("")}</div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score, n = ITEMS().length;
    const bc = sc.by_clock || {};
    const cellc = (x) => x ? `${x.ok}/${x.n}${x.empty ? ` <span class="muted">∅${x.empty}</span>` : ""}` : "";
    return `<h1>Due dates in memory: A ${sc.memory.talk.ok}/${n} · B ${sc.memory.logged.ok}/${n} · C ${sc.memory.resolved.ok}/${n}</h1>
      <p class="lead">✓ the right due date · ✗ another date (how far off) · ~ only “N days from &lt;date&gt;” · ? the clock with no date · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>copy</th><th class="num">due dates in memory</th><th class="num">on the calendar</th><th>assistant's answers (${n} × ${RUNS()})</th></tr></thead><tbody>
        ${COPIES.map((k) => `<tr class="${k === "resolved" ? "hl" : ""}"><td><b>${esc(LABEL(k))}</b></td><td class="num">${sc.memory[k].ok}/${sc.memory[k].total}</td>
          <td class="num">${sc.calendar[k].on}/${n}</td><td>${tline(sc.answers[k])}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">Answers right, per clock</h3><table class="lint plain"><thead><tr><th>clock</th>${COPIES.map((c) => `<th class="num">${SHORT[c]}</th>`).join("")}</tr></thead><tbody>
        ${ITEMS().map((it) => `<tr><td class="small"><b>${esc(it.what)}</b> <span class="muted">“${esc(it.said)}”</span></td>${COPIES.map((c) => `<td class="num">${cellc((bc[it.id] || {})[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">A clock that starts on the day it is said (“16 weeks from today”) usually gets its date at extraction. A clock that starts on an event never does: the event is remembered later, as a separate fact.
        Even with the clocks and milestones written as clean structured facts (B), the model has to join them and do the arithmetic itself — and the chained clock (45 days after a 90-day pilot) goes wrong. C has the date written down.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C</h1>
      <p class="lead">Any question to the programme assistant, once through each copy. Graded when it is about one of the six contract clocks.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${ITEMS().map((it) => `<button class="chip" data-q="${esc(it.ask)}">${esc(it.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Rhea → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.verdict === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(LABEL(r.copy))} · ${r.empty ? "∅ nothing attached" : r.verdict ? `${markOf(r)} ${esc(r.got || "")} ${esc(r.off || "")}` : esc(r.got || "")}</span>${esc(r.reply)}</div>`).join("")}
          ${x.want ? `<p class="small muted">due: ${esc(x.want)}</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, want: r.want });
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
    if (!confirm("Delete the demo's boundaries, conversations, actors (their facts first) and the three projects?")) return;
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
    else if (k === "meetings") S.meetingsSeen = true;
    else if (k === "structured") S.structured = d;
    else if (k === "census") S.census = d;
    else if (k === "board") S.board = d;
    else if (k === "ask_row") S.rows[d.copy].push(...d.rows);
    else if (k === "examples") S.examples[d.copy] = d.rows;
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the programme assistant, through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "meetings", "structured", "census", "board", "ask_row", "examples", "arm_done",
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

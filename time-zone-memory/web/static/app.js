/* Time zone memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["raw", "zoned", "utc", "rawutc"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three copies of the channel", view: "setup" },
    { n: 3, title: "The channel", sub: "Berlin, Austin, Singapore", view: "channel" },
    { n: 4, title: "What got remembered", sub: "“4pm” — whose 4pm?", view: "census" },
    { n: 5, title: "A · As remembered", sub: "asked from London", view: "raw" },
    { n: 6, title: "B · Zone stamped", sub: "the app adds it on write", view: "zoned" },
    { n: 7, title: "C · UTC + app converts", sub: "the model copies, code converts", view: "utc" },
    { n: 8, title: "A′ · Reverse check", sub: "C's read path, A's memory", view: "rawutc" },
    { n: 9, title: "Score", sub: "+ a no-memory control", view: "score" },
  ];
  const fresh = () => ({ census: null, rows: Object.fromEntries(["none", ...ARMS].map((a) => [a, []])), examples: {}, score: null, unavailable: null, summary: null, channelSeen: false });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "When does the code freeze for 3.8 begin, in my time?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 3;
  const EVENTS = () => S.data?.events || [];
  const evOf = (id) => EVENTS().find((e) => e.id === id) || {};
  const personOf = (k) => (S.data?.people || []).find((p) => p.key === k) || {};

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const armViews = Object.fromEntries(ARMS.map((a) => [a, () => arm(a)]));
    const r = { connect, setup, channel, census, ...armViews, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], off: ["✗", "bad", "wrong"], none: ["?", "warn", "no time given"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  const ansOf = (r) => `<span class="ans">${markOf(r)} ${esc(r.got || "–")}${r.off ? `<span class="off">${esc(r.off)}</span>` : ""}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.verdict), n = (v) => live.filter((r) => !r.empty && r.verdict === v).length;
    return { calls: live.length, empty: live.filter((r) => r.empty).length, ok: n("ok"), off: n("off"), none: n("none"),
      day: live.filter((r) => !r.empty && /day/.test(r.off || "")).length };
  }
  const tline = (t) => `<b>${t.ok}</b> right · <b class="${t.off ? "bad-ink" : ""}">${t.off}</b> wrong${t.day ? ` (${t.day} a whole day off)` : ""} · <b>${t.none}</b> no time · <b>${t.empty}</b> ∅ <span class="muted">of ${t.calls}</span>`;
  const STAMP = /\(([A-Za-z ]+ time, UTC[+-]\d+(?::\d\d)?)(; = [^)]*)?\)|\(= [^)]*\)/g;
  const stamped = (t) => esc(t).replace(STAMP, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.raw)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes (extraction, then ~120 Router calls) · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-tzm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“4pm”, “6pm my time” — the channel is remembered, whose 4pm it was is not</h1>
      <p class="lead">${esc(S.data?.team || "Tessaline")} (fictional) lets MemoryLake remember its ${esc(S.data?.channel || "#release-sync")} channel. Lena works from Berlin, Marcus from Austin, Priya from Singapore,
      and they say times the way people do. Sam, in London, asks his assistant (MemoryRouter) what time those are for him. Extraction keeps “4pm” and drops whose 4pm it was.
      Three copies of the channel, written three ways by the app — as said · each sender's zone stamped · the zone and the UTC instant stamped, converted by the app —
      plus a reverse check and a no-memory control. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 120 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-remote-and-distributed-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for remote and distributed teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    const copies = d.copies || {};
    return `<h1>Three copies of the same channel, three teammates, one boundary each</h1>
      <p class="lead">Every arm reads its own copy, so no answer comes from what another arm's call retrieved. The boundaries cover the copy's project — what Sam's assistant reads through MemoryRouter.</p>
      <div class="grid2"><div class="card"><dl class="kv">${["raw", "zoned", "utc"].map((c) => `<dt>${esc(copies[c] || c)}</dt><dd class="mono small">${esc(w?.projects?.[c] || "pending")}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The team</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)} · ${esc(p.city)} <span class="mono muted">${esc(p.tz)}</span></div>`).join("")}
          <div class="small" style="margin-top:8px"><b>${esc(d.asker?.display)}</b> · ${esc(d.asker?.role)} · ${esc(d.asker?.city)} <span class="mono muted">${esc(d.asker?.tz)}</span> — asks the questions</div></div></div>`;
  }

  function channel() {
    const d = S.data;
    if (!d || (!S.channelSeen && !S.world)) return running() ? waiting("The channel") : empty("The channel");
    return `<h1>${esc(d.channel)} — when things happen, said the way people say it</h1>
      <p class="lead">One GROUP conversation per copy, each message appended as its sender. The app knows every sender's time zone from the chat profile;
      copy B gets it stamped next to each clock time, copy C gets the UTC instant too. The highlighted part is the only difference.</p>
      ${d.written.map((m) => `<div class="card"><div class="small"><b>${esc(m.display)}</b> <span class="muted">· ${esc(m.city)}</span></div>
        ${m.zoned === m.raw && m.utc === m.raw ? `<div class="copyrow"><span class="k">all three</span><span>${esc(m.raw)}</span></div>`
          : ["raw", "zoned", "utc"].map((c) => `<div class="copyrow"><span class="k">${c === "raw" ? "A" : c === "zoned" ? "B" : "C"} · ${esc(d.copies[c])}</span><span>${stamped(m[c])}</span></div>`).join("")}</div>`).join("")}`;
  }

  const ZW = { none: ["⚠ no time zone", "lab-bad"], vague: ["⚠ whose local time?", "lab-bad"], zone: ["zone kept", "lab-ok"], utc: ["UTC instant", "lab-ok"] };
  function census() {
    if (!S.census) return running() ? waiting("What got remembered") : empty("What got remembered");
    const c = S.census, d = S.data;
    const said = Object.fromEntries((d.written || []).filter((m) => m.event).map((m) => [m.event, m]));
    return `<h1>What got remembered — ${c.nozone.raw} of ${c.implicit} times with no zone you could convert from</h1>
      <p class="lead">The facts extraction wrote into each copy's project, for each scheduled item. In copy A the clock time survives and the zone does not —
      Marcus's “6pm <b>my time</b>” comes back as “6pm”. The two items said with a zone (SGT, UTC) keep it in every copy.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>item · who said it</th><th>A · as said</th><th>B · zone stamped</th><th>C · + UTC</th></tr></thead><tbody>
      ${EVENTS().map((ev) => { const m = said[ev.id] || {};
        const cell = (k) => { const hs = c.copies[k].events[ev.id] || []; return hs.length ? hs.slice(0, 2).map((h) => `<div class="small"><span class="lab ${ZW[h.zone][1]}">${ZW[h.zone][0]}</span>${esc(h.fact)}</div>`).join("") : '<span class="small muted">not remembered with a time</span>'; };
        return `<tr class="${ev.zone_in_text ? "" : "hl"}"><td class="small"><b>${esc(ev.what)}</b><br>${esc(m.display)} (${esc(m.city)}): “${esc(ev.said)}”</td><td>${cell("raw")}</td><td>${cell("zoned")}</td><td>${cell("utc")}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  const ARM_LEAD = {
    raw: "Each question through copy A's boundary, 3 answers each at temperature 0.7, plus one call without the boundary as the ∅ baseline. The system prompt says Sam is in London and asks for London time.",
    zoned: "Same questions, same prompt, copy B: every clock time in memory carries the sender's zone, so the model has what it needs to convert.",
    utc: "Copy C holds the UTC instant. The prompt asks the model for the moment in UTC (<code>Answer: YYYY-MM-DD HH:MM UTC</code>); the app converts it to London in code, with the real time-zone rules.",
    rawutc: "The reverse check: C's read path — model gives UTC, app converts — on copy A, where the zones were never remembered. Converting correctly cannot recover a zone the memory does not have.",
  };
  function arm(name) {
    const h = (S.data?.arms || {})[name] || name;
    const rows = S.rows[name];
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = tally(rows);
    const ex = S.examples[name] || [];
    return `<h1>${esc(h)} — ${t.ok} of ${t.calls} right${t.day ? `, ${t.day} a whole day off` : ""}</h1>
      <p class="lead">${ARM_LEAD[name]}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>right (London)</th><th>${RUNS()} answers</th></tr></thead><tbody>
      ${EVENTS().map((ev) => { const rs = rows.filter((r) => r.ev === ev.id);
        return `<tr class="${rs.some((r) => r.verdict === "off" && !r.empty) ? "hl" : ""}"><td class="small"><b>${esc(ev.what)}</b>${ev.zone_in_text ? ' <span class="lab">zone said</span>' : ""}<br><span class="muted">${esc(ev.ask)}</span></td>
          <td class="ans">${esc(ev.want)}</td><td><div class="cell-ans">${rs.map(ansOf).join("")}</div></td></tr>`; }).join("")}
      </tbody></table><p class="small">${tline(t)}</p></div>
      ${ex.length ? `<h2>Answers as Sam would read them</h2><div class="grid2">${ex.map((r) => { const ev = evOf(r.ev);
        return `<div class="card"><div class="msg user"><span class="who">Sam → assistant</span>${esc(ev.ask)}</div>
          <div class="msg bot ${r.verdict === "ok" ? "" : "wrong"}"><span class="who">reply ${markOf(r)}</span>${esc(r.reply)}</div>
          ${name === "utc" || name === "rawutc" ? `<p class="small" style="margin-bottom:0">← the app converts to London: <b>${esc(r.got || "–")}</b></p>` : ""}
          <p class="small muted" style="margin-bottom:0">right answer: ${esc(ev.want)} London</p></div>`; }).join("")}</div>` : ""}`;
  }

  const cell = (t) => `${t.ok}/${t.calls - t.empty}${t.empty ? ` <span class="muted">(∅ ${t.empty})</span>` : ""}`;
  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score, a = sc.arms, sp = sc.split;
    return `<h1>As remembered: ${a.raw.ok} of ${a.raw.calls} right · zone stamped: ${a.zoned.ok} · UTC + app converts: ${a.utc.ok}</h1>
      <p class="lead">✓ right day and time in London · ✗ wrong (by how much) · ? no time given · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call), graded apart.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>arm</th><th>7 questions × ${RUNS()}</th><th>said without a zone <span class="muted">(right / graded)</span></th><th>said with a zone</th></tr></thead><tbody>
        ${["none", ...ARMS].map((k) => `<tr class="${k === "utc" ? "hl" : ""}"><td><b>${esc(S.data.arms[k])}</b></td><td>${tline(a[k])}</td>
          <td class="num">${cell(sp[k].implicit)}</td><td class="num">${cell(sp[k].explicit)}</td></tr>`).join("")}</tbody></table>
      <p class="small muted" style="margin-bottom:0">Without a zone in memory the model reads “4pm” as Sam's 4pm. Stamping the sender's zone fixes the hours, and leaves the conversion to the model —
      which keeps the date when the London time crosses midnight. Stamping the instant and converting in code gets both. The same conversion on the copy that lost the zones does not help:
      the zone has to be written when the message arrives, because nothing later knows it.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A, B and C</h1>
      <p class="lead">Any question from Sam, once through each copy. Graded when it is about one of the seven scheduled items.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${EVENTS().map((ev) => `<button class="chip" data-q="${esc(ev.ask)}">${esc(ev.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Sam → assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.verdict === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(S.data.arms[r.arm])} · ${r.empty ? "∅ nothing attached" : r.verdict ? `${markOf(r)} ${esc(r.got || "")} ${esc(r.off || "")}` : esc(r.got || "")}</span>${esc(r.reply)}</div>`).join("")}
          ${x.want ? `<p class="small muted">right answer: ${esc(x.want)} London</p>` : ""}`).join("")}</div></div>`;
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
      if (r) S.tries.push({ question: S.tq, rows: r.rows, want: r.rows[0]?.want });
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
    if (!confirm("Delete the demo's boundaries, conversations, teammate actors (their facts first) and the three projects?")) return;
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
    else if (k === "channel") S.channelSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "examples") S.examples[d.arm] = d.rows;
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, teammate actors (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question from Sam, through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "channel", "census", "ask_row", "examples", "arm_done",
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

/* Migrated history memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const COPIES = ["asis", "stamped"];
  const SHORT = { asis: "A", stamped: "B" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two copies, one boundary each", view: "setup" },
    { n: 3, title: "Migrate", sub: "the export has no timestamps", view: "migrate" },
    { n: 4, title: "What got remembered", sub: "every relative date, resolved", view: "census" },
    { n: 5, title: "Deadline board", sub: "as of the migration", view: "board" },
    { n: 6, title: "Ask Relay", sub: "on the new framework", view: "ask" },
    { n: 7, title: "Score", sub: "+ what is left in B", view: "score" },
  ];
  const fresh = () => ({ exportSeen: false, census: null, board: null, rows: { asis: [], stamped: [] }, examples: {}, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "When does the TLS certificate on api.tallowmere.io expire?", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 2;
  const ITEMS = () => S.data?.items || [];
  const LABEL = (c) => (S.data?.copies || {})[c] || c;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>through A and B</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, migrate, census, board, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the date that was meant"], off: ["✗", "bad", "another date"], rel: ["~", "warn", "no date"], none: ["·", "", "not remembered"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  const ansOf = (r) => `<span class="ans">${markOf(r)} ${esc(r.got || (r.empty ? "" : "no date"))}${r.off ? `<span class="off">${esc(r.off)}</span>` : ""}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.verdict), n = (v) => live.filter((r) => !r.empty && r.verdict === v).length;
    return { calls: live.length, empty: live.filter((r) => r.empty).length, ok: n("ok"), off: n("off"), nodate: n("rel") };
  }
  const tline = (t) => `<b>${t.ok}</b> right · <b class="${t.off ? "bad-ink" : ""}">${t.off}</b> wrong date · <b>${t.nodate}</b> no date · <b>${t.empty}</b> ∅ <span class="muted">of ${t.calls}</span>`;
  const REL = /\b(two weeks from today|ten days from today|the day after tomorrow|tomorrow(?: morning)?|in (?:two weeks|ten days))\b/gi;
  const relHi = (t) => esc(t).replace(REL, (m) => `<span class="stamp">${m}</span>`);
  const day = (iso) => { if (!iso) return "–"; const d = new Date(iso + "T12:00:00Z"); return d.toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" }) + " " + iso; };

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.asis)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (extraction, then ~30 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-mhm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“Two weeks from today”, read on the day you migrated</h1>
      <p class="lead">${esc(d.company || "Tallowmere")} (fictional) moves Relay, its platform team's Slack assistant, from ${esc(d.old_stack || "LangChain + ConversationBufferMemory")} to ${esc(d.new_stack || "LangGraph + MemoryLake")}.
      The old memory exports cleanly — but a buffer never stored <i>when</i> a message was sent. Imported without a timestamp, MemoryLake's extraction resolves “tomorrow” and “two weeks from today”
      against the day of the migration. Two copies of the same history: A imported as exported · B with each thread's start time from the old app's request log. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 30 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-migration-when-upgrading-agent-frameworks" target="_blank" rel="noopener">memorylake.ai › Memory migration when upgrading agent frameworks</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Two copies of the same history, one boundary each</h1>
      <p class="lead">Each copy is its own project, so neither one's answers come from the other's facts. The boundary covers the copy's project — what Relay on the new framework reads through MemoryRouter.</p>
      <div class="grid2"><div class="card"><dl class="kv">${COPIES.map((c) => `<dt>${esc(LABEL(c))}</dt><dd class="mono small">${esc(w?.projects?.[c] || "pending")}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">${esc(d.channel)}</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)} · ${esc(p.city)} <span class="mono muted">${esc(p.tz)}</span></div>`).join("")}
          <div class="small"><b>${esc(d.assistant)}</b> · assistant</div></div></div>`;
  }

  function migrate() {
    const d = S.data;
    if (!d || (!S.exportSeen && !S.world)) return running() ? waiting("Migrate") : empty("Migrate");
    return `<h1>The export keeps every word and no timestamp</h1>
      <p class="lead"><code>messages_to_dict(memory.chat_memory.messages)</code> gives each thread's messages with <code>content</code>, <code>name</code>, <code>type</code> — and nothing about when.
      The old app's request log still has each thread's <code>started_at</code>. Copy A appends the messages as exported; copy B passes <code>--timestamp</code> from the log. The highlighted words only mean something next to the day they were said.</p>
      ${d.threads.map((t) => `<div class="card"><div class="row spread"><b class="mono">${esc(t.thread)}</b><span class="small muted">request log: <span class="mono">${esc(t.started_at)}</span></span></div>
        ${t.messages.map((m) => `<div class="copyrow"><span class="k">${esc(m.label)}</span><span>${relHi(m.body)}</span></div>`).join("")}
        <div class="small" style="margin-top:6px"><span class="lab lab-bad">A</span> appended with no timestamp · <span class="lab lab-ok">B</span> <code>--timestamp ${esc(t.started_at)}</code> (+1 min per message)</div></div>`).join("")}`;
  }

  function census() {
    if (!S.census) return running() ? waiting("What got remembered") : empty("What got remembered");
    const c = S.census.copies, tr = S.census.truth;
    const ok = (k) => Object.values(c[k].items).filter((r) => r.verdict === "ok").length;
    return `<h1>What got remembered — A: ${ok("asis")} of ${ITEMS().length} dates right · B: ${ok("stamped")}</h1>
      <p class="lead">Extraction writes each relative date as a calendar date. In copy A that date is counted from the day of the import; the words that said “two weeks from today” are gone,
      so nothing in memory shows that anything is off. Facts still worded relative to when they were said — A: ${c.asis.relative.length} · B: ${c.stamped.relative.length}.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>item · said · meant</th><th>A · imported as exported</th><th>B · timestamps from the log</th></tr></thead><tbody>
      ${ITEMS().map((it) => { const cell = (k) => { const r = c[k].items[it.id];
          if (r.verdict === "none") return '<span class="small muted">not remembered</span>';
          return `<div>${ansOf(r)}</div><div class="small muted">${esc(r.facts[0] || "")}</div>`; };
        return `<tr class="${c.asis.items[it.id].verdict !== "ok" ? "hl" : ""}"><td class="small"><b>${esc(it.what)}</b><br>“${esc(it.said)}”, ${esc(day(it.said_on))}<br>meant <b>${esc(day(tr[it.id]))}</b></td><td>${cell("asis")}</td><td>${cell("stamped")}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function board() {
    if (!S.board) return running() ? waiting("Deadline board") : empty("Deadline board");
    const b = S.board, k = b.counts;
    const pill = (s) => s === "past" ? '<span class="lab">past</span>' : s === "upcoming" ? '<span class="lab lab-bad">upcoming</span>' : `<span class="lab">${esc(s)}</span>`;
    return `<h1>As of ${esc(day(b.today))}: ${k.truth.past} already past · copy A says ${k.asis.upcoming} still upcoming</h1>
      <p class="lead">The board the app builds from memory on the day of the migration. Every item was in the past by then; copy A moved each one to after the migration —
      the expired certificate looks like it has two weeks left.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>item</th><th>meant</th><th>A remembers</th><th>B remembers</th></tr></thead><tbody>
      ${b.rows.map((r) => `<tr class="${r.copies.asis.status !== r.status ? "hl" : ""}"><td class="small"><b>${esc(r.what)}</b></td><td class="small">${esc(r.want)} ${pill(r.status)}</td>
        ${COPIES.map((c) => `<td class="small">${markOf(r.copies[c])} ${esc(r.copies[c].got || "–")} ${pill(r.copies[c].status)}</td>`).join("")}</tr>`).join("")}
      </tbody></table>
      <p class="small" style="margin-bottom:0">${["truth", ...COPIES].map((c) => `<b>${c === "truth" ? "meant" : esc(LABEL(c))}</b>: past ${k[c].past} · upcoming ${k[c].upcoming}`).join(" &nbsp;|&nbsp; ")}</p></div>`;
  }

  function askView() {
    const all = [...S.rows.asis, ...S.rows.stamped];
    if (!all.length) return unavailable() || (running() ? waiting("Ask Relay") : empty("Ask Relay"));
    const ta = tally(S.rows.asis), tb = tally(S.rows.stamped);
    return `<h1>Relay on the new framework — A: ${ta.ok} of ${ta.calls} right · B: ${tb.ok} of ${tb.calls}</h1>
      <p class="lead">Each question through the copy's boundary, ${RUNS()} answers each at temperature 0.7, plus one call without a boundary as the ∅ baseline. The system prompt gives today's date.</p>
      ${COPIES.map((c) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(c))}</h3><table class="lint plain"><thead><tr><th>question</th><th>meant</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${ITEMS().map((it) => { const rs = S.rows[c].filter((r) => r.item === it.id);
          return `<tr class="${rs.some((r) => r.verdict === "off" && !r.empty) ? "hl" : ""}"><td class="small">${esc(it.ask)}</td><td class="ans">${esc(it.want)}</td><td><div class="cell-ans">${rs.map(ansOf).join("")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(tally(S.rows[c]))}</p>
        ${(S.examples[c] || []).map((r) => { const it = ITEMS().find((i) => i.id === r.item) || {};
          return `<div class="msg user"><span class="who">Ines → Relay</span>${esc(it.ask)}</div><div class="msg bot ${r.verdict === "ok" ? "" : "wrong"}"><span class="who">reply ${markOf(r)}</span>${esc(r.reply)}</div>`; }).join("")}</div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score, c = S.census?.copies;
    return `<h1>Imported as exported: ${sc.memory.asis.ok} of ${sc.memory.asis.total} dates right · timestamps from the log: ${sc.memory.stamped.ok}</h1>
      <p class="lead">✓ the date that was meant · ✗ another date (how far off) · ~ remembered with no date · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>copy</th><th>remembered dates</th><th>Relay's answers (${ITEMS().length} × ${RUNS()})</th></tr></thead><tbody>
        ${COPIES.map((k) => `<tr class="${k === "stamped" ? "hl" : ""}"><td><b>${esc(LABEL(k))}</b></td><td class="num">${sc.memory[k].ok}/${sc.memory[k].total}</td><td>${tline(sc.answers[k])}</td></tr>`).join("")}</tbody></table></div>
      ${sc.evening_threads.length && c ? `<div class="card"><h3 style="margin-top:0">What is left in B: threads started in the evening</h3>
        ${ITEMS().filter((it) => sc.evening_threads.includes(it.thread)).map((it) => { const r = c.stamped.items[it.id];
          return `<div class="small">${esc(it.what)} — meant ${esc(it.want)} · B remembers ${ansOf(r)}${r.as_of ? ` <span class="muted">(as of ${esc(r.as_of)})</span>` : ""}</div>`; }).join("")}
        <p class="small muted" style="margin-bottom:0">Extraction dates a message by the calendar day in UTC+8, so a message sent after 16:00 UTC is read as said on the next day.
        The timestamp is right; the day it is read against is not the sender's. Daytime threads come out right. The fix that matters is the first one: carry the timestamp across the migration —
        a buffer export drops it, and nothing after the import can tell which dates moved.</p></div>` : ""}
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A and B</h1>
      <p class="lead">Any question to Relay, once through each copy. Graded when it is about one of the six items.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${ITEMS().map((it) => `<button class="chip" data-q="${esc(it.ask)}">${esc(it.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Ines → Relay</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.verdict === "ok" && !r.empty ? "" : r.verdict ? "wrong" : ""}"><span class="who">${esc(LABEL(r.copy))} · ${r.empty ? "∅ nothing attached" : r.verdict ? `${markOf(r)} ${esc(r.got || "")} ${esc(r.off || "")}` : esc(r.got || "")}</span>${esc(r.reply)}</div>`).join("")}
          ${x.want ? `<p class="small muted">meant: ${esc(x.want)}</p>` : ""}`).join("")}</div></div>`;
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
    else if (k === "export") S.exportSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "board") S.board = d;
    else if (k === "ask_row") S.rows[d.copy].push(...d.rows);
    else if (k === "examples") S.examples[d.copy] = d.rows;
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, actors (facts first) and the two projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to Relay, through A and B.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "export", "census", "board", "ask_row", "examples", "arm_done",
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

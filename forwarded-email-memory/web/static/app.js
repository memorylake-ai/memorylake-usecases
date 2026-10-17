/* Forwarded email memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const COPIES = ["asis", "split"];
  const SHORT = { asis: "A", split: "B" };
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two copies, one boundary each", view: "setup" },
    { n: 3, title: "Three forwards", sub: "as received · split", view: "forwards" },
    { n: 4, title: "What got remembered", sub: "every deadline in the forwards", view: "census" },
    { n: 5, title: "The docket", sub: "as of today", view: "board" },
    { n: 6, title: "Ask the assistant", sub: "through each boundary", view: "ask" },
    { n: 7, title: "Score", sub: "memory · docket · answers", view: "score" },
  ];
  const fresh = () => ({ inboxSeen: false, census: null, board: null, rows: { asis: [], split: [] }, examples: {}, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "When is our response to the Japanese Office Action in QR-2291-JP due?", termCount: 0, lastEvent: 0, caughtUp: false,
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
    const r = { connect, setup, forwards, census, board, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "the date that was meant"], off: ["✗", "bad", "another date"], calc: ["~", "warn", "only “N days from <date>”, never resolved"], nodate: ["?", "warn", "no date"], none: ["·", "", "not remembered"] };
  const markOf = (r) => !r.verdict ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.verdict][1]}" title="${MARKS[r.verdict][2]}">${MARKS[r.verdict][0]}</span>`;
  const ansOf = (r) => `<span class="ans">${markOf(r)} ${esc(r.got || (r.empty ? "" : "no date"))}${r.off ? `<span class="off">${esc(r.off)}</span>` : ""}</span>`;
  function tally(rows) {
    const live = rows.filter((r) => r.verdict), n = (v) => live.filter((r) => !r.empty && r.verdict === v).length;
    return { calls: live.length, empty: live.filter((r) => r.empty).length, ok: n("ok"), off: n("off"), nodate: n("nodate") + n("calc") };
  }
  const tline = (t) => `<b>${t.ok}</b> right · <b class="${t.off ? "bad-ink" : ""}">${t.off}</b> wrong date · <b>${t.nodate}</b> no date · <b>${t.empty}</b> ∅ <span class="muted">of ${t.calls}</span>`;
  const REL = /\b(60 days from today|four months from today|three weeks from today|the day after tomorrow|within 14 days|today)\b/gi;
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
          <span class="muted small">about 2½ minutes (extraction, then ~30 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-fem")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The deadline inside the forward</h1>
      <p class="lead">${esc(d.firm || "Quillmere & Rook")} (a fictional patent firm) dockets from email, and most deadlines arrive forwarded: a foreign associate reports an Office Action,
      a client asks for an opinion before a board meeting, and the docketing paralegal forwards it into ${esc(d.channel || "#docketing")}, which MemoryLake remembers.
      Stored as received — one message, her note on top, the original below — extraction often remembers <i>the forward</i> instead of what it says, and the deadlines inside come out incomplete.
      Two copies of the same three forwards: A stored as received · B split by the app (the original as its own message, from its author, at its <code>Date:</code>). It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 30 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-patent-and-ip-research" target="_blank" rel="noopener">memorylake.ai › AI memory for patent and IP research</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world, d = S.data || {};
    return `<h1>Two copies of the same channel, one boundary each</h1>
      <p class="lead">Each copy is its own project, so neither one's answers come from the other's facts. The boundary covers the copy's project — what the docketing assistant reads through MemoryRouter.</p>
      <div class="grid2"><div class="card"><dl class="kv">${COPIES.map((c) => `<dt>${esc(LABEL(c))}</dt><dd class="mono small">${esc(w?.projects?.[c] || "pending")}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">${esc(d.channel)}</h3>${(d.people || []).map((p) => `<div class="small"><b>${esc(p.display)}</b> · ${esc(p.role)} · ${esc(p.city)} <span class="mono muted">${esc(p.tz)}</span></div>`).join("")}</div></div>`;
  }

  const when = (iso) => { const t = new Date(iso); return t.toISOString().slice(0, 16).replace("T", " ") + " UTC"; };
  function forwards() {
    const d = S.data;
    if (!d || (!S.inboxSeen && !S.world)) return running() ? waiting("Three forwards") : empty("Three forwards");
    return `<h1>Three forwarded emails, stored two ways</h1>
      <p class="lead">Ana clears her inbox backlog on Wednesday and forwards three emails into ${esc(d.channel)}. Copy A appends each forward as received: one message from Ana, at the time she forwarded it.
      Copy B splits it first — <code>From:</code>, <code>Date:</code>, body — and appends the original from its author at its <code>Date:</code>, then Ana's note. The highlighted words only mean something next to the day they were written.</p>
      ${d.forwards.map((f) => `<div class="card"><div class="row spread"><b>${esc(f.subject)}</b><span class="small muted">sent <span class="mono">${esc(when(f.sent))}</span> · forwarded <span class="mono">${esc(when(f.forwarded_at))}</span></span></div>
        <div class="copyrow"><span class="k"><span class="lab lab-bad">A</span> 1 message</span><span class="small"><b>Ana Lindqvist</b> <span class="mono muted">--timestamp ${esc(f.copies.asis[0].ts)}</span><pre class="mail">${relHi(f.text)}</pre></span></div>
        <div class="copyrow"><span class="k"><span class="lab lab-ok">B</span> ${f.copies.split.length} messages</span><span class="small">${f.copies.split.map((m) => `<div><b>${esc(m.text.split(":")[0])}</b> <span class="mono muted">--timestamp ${esc(m.ts)}</span><div>${relHi(m.text.slice(m.text.indexOf(":") + 1))}</div></div>`).join("")}</span></div></div>`).join("")}`;
  }

  const cellOf = (r) => r.verdict === "none" ? '<span class="small muted">not remembered</span>'
    : `<div>${r.verdict === "calc" ? `<span class="ans"><span class="mark warn" title="${MARKS.calc[2]}">~</span> ${esc(r.anchor)} + ?</span>` : ansOf(r)}</div><div class="small muted">${esc(r.facts[0] || "")}</div>`;
  function census() {
    if (!S.census) return running() ? waiting("What got remembered") : empty("What got remembered");
    const c = S.census.copies, tr = S.census.truth;
    const ok = (k) => Object.values(c[k].items).filter((r) => r.verdict === "ok").length;
    return `<h1>What got remembered — A: ${ok("asis")} of ${ITEMS().length} deadlines with the right date · B: ${ok("split")}</h1>
      <p class="lead">Every fact in each copy, read for the six deadlines. ✓ the date that was meant · ~ only “N days from &lt;date&gt;”, never resolved · · not remembered.
      Facts about the forwarding itself — A: ${c.asis.about_forward.length} · B: ${c.split.about_forward.length}.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>deadline · written · meant</th><th>A · stored as received</th><th>B · split by the app</th></tr></thead><tbody>
      ${ITEMS().map((it) => `<tr class="${c.asis.items[it.id].verdict !== "ok" ? "hl" : ""}"><td class="small"><b>${esc(it.what)}</b><br>“${esc(it.said)}”, ${esc(day(it.said_on))}<br>meant <b>${esc(day(tr[it.id]))}</b></td><td>${cellOf(c.asis.items[it.id])}</td><td>${cellOf(c.split.items[it.id])}</td></tr>`).join("")}
      </tbody></table>
      ${COPIES.filter((k) => c[k].about_forward.length).map((k) => `<p class="small"><b>${esc(LABEL(k))}</b> also remembers the forwarding itself:</p>${c[k].about_forward.slice(0, 4).map((f) => `<div class="small muted">“${esc(f)}”</div>`).join("")}`).join("")}</div>`;
  }

  function board() {
    if (!S.board) return running() ? waiting("The docket") : empty("The docket");
    const b = S.board, t = b.tally;
    const pill = (s) => s === "past" ? '<span class="lab">past</span>' : s === "upcoming" ? '<span class="lab lab-bad">open</span>' : s === "missing" ? '<span class="lab">not on the docket</span>' : `<span class="lab">${esc(s)}</span>`;
    const cell = (x) => x.verdict === "calc" ? `<span class="mark warn">~</span> ${esc(x.anchor)} + ? ${pill("missing")}` : `${markOf(x)} ${esc(x.got || "")} ${pill(x.status)}`;
    return `<h1>As of ${esc(day(b.today))}: ${b.open} deadlines still open · on A's docket ${t.asis.on} · on B's ${t.split.on}</h1>
      <p class="lead">The docket the app builds from memory today. A deadline is on it only when memory gives its date; a period from some date (~) is left for the reader to work out.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>deadline</th><th>meant</th><th>A docket</th><th>B docket</th></tr></thead><tbody>
      ${b.rows.map((r) => `<tr class="${r.status === "upcoming" && r.copies.asis.verdict !== "ok" ? "hl" : ""}"><td class="small"><b>${esc(r.what)}</b></td><td class="small">${esc(r.want)} ${pill(r.status)}</td>
        ${COPIES.map((c) => `<td class="small">${cell(r.copies[c])}</td>`).join("")}</tr>`).join("")}
      </tbody></table>
      <p class="small" style="margin-bottom:0">${COPIES.map((c) => `<b>${esc(LABEL(c))}</b>: open deadlines on the docket ${t[c].on}/${b.open}${t[c].wrong ? ` · wrong date ${t[c].wrong}` : ""} · missing ${t[c].missing}`).join(" &nbsp;|&nbsp; ")}</p></div>`;
  }

  function askView() {
    const all = [...S.rows.asis, ...S.rows.split];
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const ta = tally(S.rows.asis), tb = tally(S.rows.split);
    return `<h1>The docketing assistant — A: ${ta.ok} of ${ta.calls} right · B: ${tb.ok} of ${tb.calls}</h1>
      <p class="lead">Each question through the copy's boundary, ${RUNS()} answers each at temperature 0.7, plus one call without a boundary as the ∅ baseline. The system prompt gives today's date.</p>
      ${COPIES.map((c) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(c))}</h3><table class="lint plain"><thead><tr><th>question</th><th>meant</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${ITEMS().map((it) => { const rs = S.rows[c].filter((r) => r.item === it.id);
          return `<tr class="${rs.some((r) => r.verdict !== "ok" && !r.empty) ? "hl" : ""}"><td class="small">${esc(it.ask)}</td><td class="ans">${esc(it.want)}</td><td><div class="cell-ans">${rs.map(ansOf).join("")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(tally(S.rows[c]))}</p>
        ${(S.examples[c] || []).map((r) => { const it = ITEMS().find((i) => i.id === r.item) || {};
          return `<div class="msg user"><span class="who">Ruth → assistant</span>${esc(it.ask)}</div><div class="msg bot ${r.verdict === "ok" ? "" : "wrong"}"><span class="who">reply ${markOf(r)}</span>${esc(r.reply)}</div>`; }).join("")}</div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("The score") : empty("The score");
    const sc = S.score;
    return `<h1>Stored as received: ${sc.memory.asis.ok} of ${sc.memory.asis.total} deadlines remembered right · split by the app: ${sc.memory.split.ok}</h1>
      <p class="lead">✓ the date that was meant · ✗ another date (how far off) · ~ only “N days from &lt;date&gt;”, never resolved · ? remembered with no date · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call).</p>
      <div class="card"><table class="lint plain"><thead><tr><th>copy</th><th>remembered deadlines</th><th>open deadlines on the docket</th><th>assistant's answers (${ITEMS().length} × ${RUNS()})</th></tr></thead><tbody>
        ${COPIES.map((k) => `<tr class="${k === "split" ? "hl" : ""}"><td><b>${esc(LABEL(k))}</b></td><td class="num">${sc.memory[k].ok}/${sc.memory[k].total} <span class="small muted">(~ ${sc.memory[k].calc} · not remembered ${sc.memory[k].none})</span></td>
          <td class="num">${sc.docket[k].on}/${S.board?.open ?? 3}</td><td>${tline(sc.answers[k])}</td></tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">Stored as received, whole deadlines go missing — most often the second one in an email — and sometimes all that is left of an email is “it was received for docketing”.
        Split, far fewer go missing. Splitting does not make extraction do date arithmetic: a period (“60 days”, “four months”, “within 14 days”) can stay “N days from &lt;date&gt;” in either copy.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — through A and B</h1>
      <p class="lead">Any question to the docketing assistant, once through each copy. Graded when it is about one of the six deadlines.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${ITEMS().map((it) => `<button class="chip" data-q="${esc(it.ask)}">${esc(it.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Ruth → assistant</span>${esc(x.question)}</div>
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
    else if (k === "inbox") S.inboxSeen = true;
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
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the docketing assistant, through A and B.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "inbox", "census", "board", "ask_row", "examples", "arm_done",
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

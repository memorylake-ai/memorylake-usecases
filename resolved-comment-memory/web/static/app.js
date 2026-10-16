/* Resolved comment memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, two boundaries", view: "setup" },
    { n: 3, title: "The brief library", sub: "three Word files", view: "memory" },
    { n: 4, title: "Comments in the index", sub: "open or resolved?", view: "index" },
    { n: 5, title: "As saved", sub: "every thread, open or resolved", view: "saved" },
    { n: 6, title: "Resolved dropped", sub: "open threads only", view: "dropped" },
    { n: 7, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "op", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["saved", "dropped"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { stale: ["✗", "bad"], mixed: ["◐", "bad"], other: ["·", ""], right: ["✓", "ok"], empty: ["∅", ""] };
  const OUTCOME = { rejected: "turned down; the line was left as it is", applied: "accepted; the line already says so", open: "still open" };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "lookup" ? "active" : ""}" data-view="lookup"><span class="n">#</span><span class="t">Brief<small>one brief, both projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, saved: () => arm("saved"), dropped: () => arm("dropped"), score, lookup: lookupTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const graded = (g) => g.answers - g.empty;
  const armLine = (t) => t ? `lines with a resolved suggestion: ${t.resolved.right}/${graded(t.resolved)} right · ${t.resolved.stale} gave the turned-down suggestion · ${t.resolved.mixed} mixed it in${t.resolved.empty ? ` · ${t.resolved.empty} ∅` : ""} · open comment ${t.open.right}/${graded(t.open)} · uncommented brief ${t.control.right}/${graded(t.control)}` : "";
  const chkLine = (t) => t ? `checker: the brief attached ${t.checks.attached}/${t.checks.need}, with the resolved comment ${t.checks.held}/${t.checks.attached}${t.checks.declined ? ` · ${t.checks.declined} declined to copy` : ""}` : "";

  // A brief as Word shows it in Review: lines on the page, comment balloons beside them; resolved ones greyed out.
  function docCard(x) {
    const by = {};
    (x.threads || []).forEach((c) => { (by[c.on] = by[c.on] || []).push(c); });
    return `<div class="doc"><p class="t">${esc(x.title)}</p>${x.lines.map((l) => `<div class="cl ${by[l.label] ? "has" : ""}"><p><b>${esc(l.label)}:</b> ${esc(l.text)}</p>
      ${(by[l.label] || []).map((c) => `<div class="balloon ${c.state}"><b>${esc(c.mark)}</b> ${esc(c.author)}: ${esc(c.text)}
        <span class="st">${c.state === "resolved" ? "✓ Resolved" : "Open"}</span></div>`).join("")}</div>`).join("")}</div>`;
  }
  function briefIndexCard(x, views) {
    const by = Object.fromEntries(views.filter((v) => v.brief === x.id).map((v) => [v.arm, v]));
    if (!(x.threads || []).length) return `<div class="card"><h3 style="margin-top:0">${esc(x.client)}</h3><p class="small muted">No comments: the index holds the same text in both projects.</p></div>`;
    return `<div class="card warnc"><h3 style="margin-top:0">${esc(x.client)}</h3>
      ${x.threads.map((c, k) => `<p class="small" style="margin:12px 0 2px"><b>${esc(c.mark)}</b> <span class="pill ${c.state === "resolved" ? "" : "ok"}">${esc(c.state)} in the file</span> on <b>${esc(c.on)}</b> <span class="muted">— “${esc(c.text)}” (${esc(OUTCOME[c.outcome] || c.outcome)})</span></p>
        ${ARMS.map((a) => { const y = by[a]?.comments?.[k]; const bad = y && y.held && c.state === "resolved";
          return `<div class="idx ${bad ? "bad" : y && y.held ? "ok" : ""}"><span class="tag">${esc(armShort(a))}</span>${y && y.held ? esc(y.line) : "(not in the index text)"}</div>`; }).join("")}`).join("")}
      ${by.saved?.found ? `<p class="small" style="margin-top:12px">Words like “resolved” anywhere in the as-saved index text of this brief: <b>${by.saved.state_words}</b>. A resolved comment reads exactly like an open one.</p>` : ""}</div>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.saved)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-rcm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A resolved comment reaches the index. That it was resolved does not.</h1>
      <p class="lead">Halfmoon Studio (fictional) keeps its past client briefs in shared memory, so a brief assistant can answer “when does Fernlight launch?” before the next brief is
      written. The briefs are Word files, and the review happened in Word <b>comments</b>: “Move the launch to Tuesday, November 3.” The team said no and clicked <b>Resolve</b> — in Word
      the thread greys out and folds away. In the file the comment is still there, with one flag on it. The assistant runs on MemoryRouter: every question is an ordinary model call
      with <code>--boundary</code> added. This demo stores the briefs as saved, and the same briefs with resolved threads removed, shows each comment in the index, and asks eight
      questions through each boundary. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 64 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/creative-brief-memory-across-agency-client-work" target="_blank" rel="noopener">memorylake.ai › Creative brief memory across agency client work</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two projects, two boundaries</h1>
      <p class="lead">The same three briefs twice: as saved, and with every resolved comment thread removed before upload (open threads kept). One project each, one boundary over
      each. The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armShort(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The designers' questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    // The files are local: show them as soon as step 3 starts, not only once the import has finished.
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The brief library") : empty("The brief library");
    const xs = S.data?.briefs || [];
    const res = xs.reduce((n, x) => n + x.threads.filter((c) => c.state === "resolved").length, 0), opn = xs.reduce((n, x) => n + x.threads.filter((c) => c.state === "open").length, 0);
    return `<h1>The brief library — three Word files</h1>
      <p class="lead">Two briefs carry review comment threads; ${res} of them are marked resolved (<code>w15:done="1"</code> in <code>word/commentsExtended.xml</code>), ${opn} are still open.
      Most resolved ones were suggestions the team turned down without writing a reply. Before upload, the demo's <code>drop_resolved()</code> makes the second copy: the resolved threads
      removed, the open ones and the brief text unchanged.</p>
      ${xs.map((x) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(x.file)}.docx</h3>
        <span class="pill ${x.threads.length ? "err" : "ok"}">${x.threads.length ? `${x.threads.length} comment threads · ${x.threads.filter((c) => c.state === "resolved").length} resolved` : "no comments"}</span></div>
        <div style="margin-top:10px">${docCard(x)}</div></div>`).join("")}`;
  }

  function index() {
    const h = "The comments in the index — open or resolved?";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const per = S.index.per || {};
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns the text the index holds for each brief; this is the text MemoryRouter attaches. Every comment comes out as
      <code>Commented [initials+n]: text</code>. ${ARMS.map((a) => `<b>${esc(armShort(a))}</b>: resolved comments in the text ${per[a]?.resolved_held ?? "–"}/${per[a]?.resolved ?? "–"}, open ${per[a]?.open_held ?? "–"}/${per[a]?.open ?? "–"}`).join(" · ")};
      words like “resolved” in the as-saved text: ${per.saved?.state_words ?? "–"}.</p>
      ${(S.data?.briefs || []).map((x) => briefIndexCard(x, S.index.briefs || [])).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const order = { stale: 0, mixed: 1, other: 2, empty: 3, right: 4 };
    return `<h1>${h}${t ? ` — ${t.resolved.right}/${graded(t.resolved)} right on lines with a resolved suggestion` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what it
      was given. ✓ the brief's own line · ✗ the turned-down suggestion · ◐ both · · neither · ∅ nothing attached.</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => order[a.verdict] - order[b.verdict])[0];
        const tag = r.group === "resolved" ? `<span class="pill err">a resolved suggestion on this line</span>` : r.group === "open" ? `<span class="pill ok">an open comment on this line</span>` : `<span class="pill">uncommented brief</span>`;
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small" style="white-space:pre-line">${esc(r.truth)}</div>
          <div class="row small" style="margin-top:4px">${tag}</div>
          <div class="small">checker: <code>${esc(r.attached || "—")}</code></div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick.verdict)}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}<br>${chkLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {}, per = S.summary.index || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>index: resolved comments in the text</th><th>lines with a resolved suggestion: right</th><th>gave the turned-down suggestion</th><th>mixed it in</th><th>open comment: right</th><th>uncommented brief</th><th>checker: resolved comment attached</th></tr></thead><tbody>
        ${ARMS.map((k) => `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td><td>${per[k] ? `${per[k].resolved_held}/${per[k].resolved}` : "–"}</td>${t[k] ? `<td>${t[k].resolved.right}/${graded(t[k].resolved)}</td><td>${t[k].resolved.stale}</td><td>${t[k].resolved.mixed}</td><td>${t[k].open.right}/${graded(t[k].open)}</td><td>${t[k].control.right}/${graded(t[k].control)}</td><td>${t[k].checks.held}/${t[k].checks.attached}</td>` : "<td>–</td><td></td><td></td><td></td><td></td><td></td>"}</tr>`).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">∅ answers (MemoryRouter attached nothing) are not counted either way. The open-comment question checks that dropping resolved threads keeps the live ones.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant said instead of the brief's own line</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function lookupTool() {
    const ready = !!S.world, xs = S.data?.briefs || [];
    const last = S.lookups[S.lookups.length - 1];
    const x = last && xs.find((y) => y.id === last.brief);
    return `<h1>Brief</h1>
      <p class="lead">One client brief: its comment threads, and the line each one has in the index text of both projects. Read-only: searches, no model call.</p>
      <div class="card"><div class="row">${xs.map((y) => `<button class="chip ${S.pick === y.id ? "on" : ""}" data-pick="${esc(y.id)}">${esc(y.client)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="lookup" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last && x ? briefIndexCard(x, last.views || []) : ""}`;
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
    st.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => { S.pick = b.dataset.pick; renderStage(); }));
    st.querySelector('[data-act="lookup"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/brief", { brief: S.pick });
      if (r) S.lookups.push(r);
      if (S.view === "lookup") renderStage();
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
  function resetView() { S.memory = null; S.index = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, the two projects and the 6 files?")) return;
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
    else if (k === "index") S.index = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, the two projects and the 6 files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Brief: one client brief's comment threads and their lines in both projects' index.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "index", "asked", "tally", "summary", "router_unavailable", "lookup", "done", "error"];
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
    show(state.status === "running" ? S.view : S.summary ? "score" : "connect");
    subscribe();
  })();
})();

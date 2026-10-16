/* Letter comment memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, two boundaries", view: "setup" },
    { n: 3, title: "The letter library", sub: "three Word files", view: "memory" },
    { n: 4, title: "Comments in the index", sub: "next to which clause?", view: "index" },
    { n: 5, title: "As saved", sub: "comments in the comment pane", view: "saved" },
    { n: 6, title: "Comments anchored", sub: "written in after their clause", view: "anchored" },
    { n: 7, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "hd", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["saved", "anchored"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { under: ["✗", "bad"], other: ["✗", "bad"], mixed: ["◐", "bad"], right: ["✓", "ok"], none: ["·", ""] };
  const short = (name) => String(name || "").replace((S.data?.prefix || "mlu-lcm") + "-", "");
  const letterOf = (id) => (S.data?.letters || []).find((x) => x.id === id);
  const clauseName = (x, n) => n ? `clause ${n} (${x?.clauses?.[n - 1]?.title || "?"})` : "no clause";

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "lookup" ? "active" : ""}" data-view="lookup"><span class="n">#</span><span class="t">Letter<small>one letter, both projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, saved: () => arm("saved"), anchored: () => arm("anchored"), score, lookup: lookupTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const armLine = (t) => t ? `displaced comments: ${t.displaced.right}/${t.displaced.answers} right · ${t.displaced.under} named the clause above the comment · in-place comments ${t["in place"].right}/${t["in place"].answers} · uncommented letter ${t.control.right}/${t.control.answers}` : "";
  const chkLine = (t) => t ? `checker: the letter attached ${t.checks.attached}/${t.checks.need}, with the comment ${t.checks.held}/${t.checks.attached}, after its own clause ${t.checks.right_place}/${t.checks.held}${t.checks.declined ? ` · ${t.checks.declined} declined to copy` : ""}` : "";

  // A letter as Word shows it in Review: clauses on the page, the partner's comments as balloons beside the clause they point at.
  function docCard(x) {
    const by = Object.fromEntries((x.comments || []).map((c) => [c.on, c]));
    return `<div class="doc"><p class="t">${esc(x.title)}</p>${x.clauses.map((c) => `<div class="cl ${by[c.n] ? "has" : ""}"><p><b>${c.n}. ${esc(c.title)}.</b> ${esc(c.text)}</p>
      ${by[c.n] ? `<div class="balloon"><b>${esc(by[c.n].mark)}</b> ${esc(by[c.n].author)}: ${esc(by[c.n].text)}</div>` : ""}</div>`).join("")}</div>`;
  }
  const where = (x, c) => !c || !c.held ? "(not in the index text)" : `sits after ${clauseName(x, c.after)}`;
  const placeTag = (c) => !c || !c.held ? ["–", ""] : c.after === c.on ? ["✓", "ok"] : ["✗", "bad"];
  function letterIndexCard(x, views) {
    const by = Object.fromEntries(views.filter((v) => v.letter === x.id).map((v) => [v.arm, v]));
    if (!(x.comments || []).length) return `<div class="card"><h3 style="margin-top:0">${esc(x.client)}</h3><p class="small muted">No comments: the index holds the same text in both projects.</p></div>`;
    return `<div class="card warnc"><h3 style="margin-top:0">${esc(x.client)}</h3>
      ${x.comments.map((c, k) => `<p class="small" style="margin:12px 0 2px"><b>${esc(c.mark)}</b> on <b>${esc(clauseName(x, c.on))}</b> <span class="muted">— “${esc(c.text)}”</span></p>
        ${ARMS.map((a) => { const y = by[a]?.comments?.[k]; const t = placeTag(y);
          return `<div class="idx ${t[1]}"><span class="tag">${t[0]} ${esc(armShort(a))}</span>${esc(where(x, y))}${y && y.held && y.after !== y.on ? ` <span class="bad-ink">← another clause</span>` : ""}</div>`; }).join("")}`).join("")}
      ${by.saved?.comments?.find((c) => c.held && c.after !== c.on) ? `<p class="small" style="margin-top:12px"><b>The as-saved index text reads</b> <code>${esc(by.saved.comments.find((c) => c.held && c.after !== c.on).line)}</code> — no clause number, no anchor.</p>` : ""}</div>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.saved)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-lcm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The partner's comments reach the index. Which clause each one is on does not.</h1>
      <p class="lead">Larkspur &amp; Vance CPAs (fictional) keeps its past engagement letters in shared memory, so an engagement-letter assistant can answer “which clause did this client refuse last year?”
      before the next letter goes out. The letters are Word files, and the partner's notes are Word <b>comments</b> — “Halloway refused this outright in the 2025 renewal”. The comment
      doesn't name the clause; in Word the balloon points at it. The assistant runs on MemoryRouter: every question is an ordinary model call with <code>--boundary</code> added. This demo
      stores the letters as saved, and the same letters with each comment written in after its clause, shows where each comment sits in the index, and asks ten questions through each
      boundary. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 80 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/engagement-letter-memory-for-professional-services" target="_blank" rel="noopener">memorylake.ai › Engagement letter memory for professional services</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two projects, two boundaries</h1>
      <p class="lead">The same three letters twice: as saved, and with each comment written into the letter right after the clause it is on. One project each, one boundary over each.
      The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armShort(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The partner's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    // The files are local: show them as soon as step 3 starts, not only once the import has finished.
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The letter library") : empty("The letter library");
    const xs = S.data?.letters || [];
    return `<h1>The letter library — three Word files</h1>
      <p class="lead">Two letters carry R. Vance's review comments. In Word each balloon points at its clause, so the comment never says which clause it means. Before upload, the
      demo's <code>anchor_comments()</code> makes the second copy: each comment written into the letter right after its clause, naming it — “[Partner comment on clause 5 (Limitation of
      liability), R. Vance: …]”. The letter text is otherwise unchanged.</p>
      ${xs.map((x) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(x.file)}.docx</h3>
        <span class="pill ${x.comments.length ? "err" : "ok"}">${x.comments.length ? `${x.comments.length} comments · ${esc(x.comments[0].author)}` : "no comments"}</span></div>
        <div style="margin-top:10px">${docCard(x)}</div></div>`).join("")}`;
  }

  function index() {
    const h = "The comments in the index — next to which clause?";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const per = S.index.per || {};
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns the text the index holds for each letter; this is the text MemoryRouter attaches. Each comment comes out as a line of its
      own; the clause it sits after: ${ARMS.map((a) => `<b>${esc(armShort(a))}</b> its own clause ${per[a]?.in_place ?? "–"}/${per[a]?.comments ?? "–"}, another clause ${per[a]?.displaced ?? "–"}`).join(" · ")}.</p>
      ${(S.data?.letters || []).map((x) => letterIndexCard(x, S.index.letters || [])).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const order = { under: 0, other: 1, mixed: 2, none: 3, right: 4 };
    const right = t ? t.displaced.right + t["in place"].right + t.control.right : 0, all = t ? t.displaced.answers + t["in place"].answers + t.control.answers : 0;
    return `<h1>${h}${t ? ` — ${right}/${all} right` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what it
      was given. ✓ the clause the comment is on · ✗ another clause (usually the one the comment sits after in the index) · ◐ several · · none.</p>
      ${rows.map((r) => {
        const x = letterOf(r.letter);
        const pick = [...r.answers].sort((a, b) => order[a.verdict] - order[b.verdict])[0];
        const tag = r.group === "displaced" ? `<span class="pill err">as saved: the comment sits after ${esc(clauseName(x, r.under))}</span>` : r.group === "in place" ? `<span class="pill ok">as saved: the comment sits after its own clause</span>` : `<span class="pill">uncommented letter</span>`;
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">the letter: <b>${esc(r.truth)}</b></div>
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
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>index: comments after another clause</th><th>displaced comments: right</th><th>named the clause above</th><th>in-place comments: right</th><th>uncommented letter</th><th>checker: comment after its own clause</th></tr></thead><tbody>
        ${ARMS.map((k) => `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td><td>${per[k] ? `${per[k].displaced}/${per[k].comments}` : "–"}</td>${t[k] ? `<td>${t[k].displaced.right}/${t[k].displaced.answers}</td><td>${t[k].displaced.under}</td><td>${t[k]["in place"].right}/${t[k]["in place"].answers}</td><td>${t[k].control.right}/${t[k].control.answers}</td><td>${t[k].checks.right_place}/${t[k].checks.held}</td>` : "<td>–</td><td></td><td></td><td></td><td></td>"}</tr>`).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">“Displaced” and “in place” are measured in this run's as-saved index (step 4); the comments anchored copy has every comment after its own clause, so the same questions are asked there for comparison.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant named instead of the clause the comment is on</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function lookupTool() {
    const ready = !!S.world, xs = S.data?.letters || [];
    const last = S.lookups[S.lookups.length - 1];
    const x = last && xs.find((y) => y.id === last.letter);
    return `<h1>Letter</h1>
      <p class="lead">One engagement letter: its comments, and where each one sits in the index text of both projects. Read-only: searches, no model call.</p>
      <div class="card"><div class="row">${xs.map((y) => `<button class="chip ${S.pick === y.id ? "on" : ""}" data-pick="${esc(y.id)}">${esc(y.client)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="lookup" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last && x ? letterIndexCard(x, last.views || []) : ""}`;
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
      const r = await api("/api/letter", { letter: S.pick });
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
      else if (S.caughtUp) banner("Done. Try Letter: one engagement letter's comments and where each sits in both projects' index.", true);
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

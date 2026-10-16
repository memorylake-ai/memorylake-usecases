/* Deck chart memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, three boundaries", view: "setup" },
    { n: 3, title: "What the team keeps", sub: "two review decks", view: "memory" },
    { n: 4, title: "Decks in the index", sub: "what is kept from each file", view: "index" },
    { n: 5, title: "Deck as saved", sub: "native charts, labelled bars", view: "as-saved" },
    { n: 6, title: "Numbers in notes", sub: "typed into the speaker notes", view: "notes" },
    { n: 7, title: "Deck + chart data", sub: "read out of the .pptx", view: "with-data" },
    { n: 8, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "A", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["as-saved", "notes", "with-data"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { wrong: ["✗", "bad"], partial: ["◐", "bad"], right: ["✓", "ok"], none: ["·", ""] };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "figure" ? "active" : ""}" data-view="figure"><span class="n">#</span><span class="t">Deck<small>one chart, three projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, "as-saved": () => arm("as-saved"), notes: () => arm("notes"), "with-data": () => arm("with-data"), score, figure: figureTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const entry = (e) => `<span class="${e.found.length < e.total ? "bad" : ""}">${esc(e.short)} — ${e.found.length}/${e.total} values${e.found.length && e.found.length < e.total ? ` (${esc(e.found.join(", "))})` : ""}</span>`;
  const armLine = (t) => t ? `${t.right}/${t.answers} right · ${t.wrong} wrong · ${t.partial} incomplete · ${t.none} no figure` : "";
  const chkLine = (t) => t ? `checker: the asked figures were in what MemoryRouter attached ${t.checks.asked_values}/${t.checks.readable}${t.checks.declined ? ` (${t.checks.declined} declined to copy)` : ""}` : "";
  const figCard = (v) => `<div class="card ${v.found.length === v.total ? "" : "warnc"}"><h3 style="margin-top:0">chart ${esc(v.fig)} · ${esc(v.kind)} <span class="mono small muted">${esc(v.source_type)}</span> <span class="pill ${v.found.length === v.total ? "ok" : "err"}">${v.found.length} of ${v.total} values</span></h3>
      <p class="small"><b>document summary</b> ${esc(v.summary || "(none)")}</p>
      ${v.figure_items ? `<p class="small"><b>chart (figure item), text read off it</b></p><pre class="mono small wrap figtext">${esc(v.figure_text || "(empty)")}</pre>` : ""}
      ${v.figure_summary && v.figure_summary !== v.summary ? `<p class="small"><b>chart (figure item), summary</b> ${esc(v.figure_summary)}</p>` : ""}
      ${(v.estimates || []).length ? `<p class="small bad"><b>${v.estimates.length} values estimated from the bar heights</b> (written ~), none printed on the chart — e.g. ${esc(v.estimates.slice().sort((x, y) => Math.abs(y.est - y.actual) - Math.abs(x.est - x.actual))[0].cat)} ~${v.estimates.slice().sort((x, y) => Math.abs(y.est - y.actual) - Math.abs(x.est - x.actual))[0].est} (deck: ${v.estimates.slice().sort((x, y) => Math.abs(y.est - y.actual) - Math.abs(x.est - x.actual))[0].actual})</p>` : ""}
      ${(v.page || []).map(([k, t]) => k === "table" ? `<p class="small"><b>table rows</b></p><pre class="mono small wrap figtext">${esc(t)}</pre>` : `<p class="small"><b>text on the slide</b> <code>${esc(t)}</code></p>`).join("")}
      ${v.notes_in_index === null || v.notes_in_index === undefined ? "" : `<p class="small ${v.notes_in_index ? "" : "bad"}"><b>speaker notes</b> (in the file, with every value): ${v.notes_in_index ? "in the index" : "not anywhere in the index"}</p>`}
      ${v.found.length && v.found.length < v.total ? `<p class="small muted">values in this text: ${esc(v.found.join(", "))}</p>` : ""}</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.["with-data"])}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-dcm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The numbers are in the deck. The index does not read them.</h1>
      <p class="lead">The growth team at Larkhaven Outdoor (fictional) keeps its monthly results in review decks — native PowerPoint charts, a data label on every bar — and saves
      the decks into shared memory so everyone's assistant can answer from them. The assistant runs on MemoryRouter: every question is an ordinary model call with
      <code>--boundary</code> added. This demo stores the same two decks three ways — as saved, with every number typed into the speaker notes, and with each chart's own data
      read out of the .pptx into a CSV — asks six questions through each boundary and grades every answer against the deck data. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 72 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-marketing-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for marketing teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, three boundaries</h1>
      <p class="lead">The same two review decks, kept three ways, one project each, one boundary over each. The assistant never assembles memory itself; MemoryRouter picks what to
      attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armLabel(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The team's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What the team keeps") : empty("What the team keeps");
    const d = S.data || {};
    const figs = d.figures || [];
    const decks = S.memory.decks || [], made = S.memory.made || [];
    return `<h1>${esc(d.review)} — two decks</h1>
      <p class="lead">Each deck is one slide: a title and a native chart, a data label on every bar. The chart's numbers are stored in the .pptx itself — <code>read_deck()</code> in
      demo.py (standard library zipfile + XML) reads them back out of the chart XML. Project “${esc(armShort("notes"))}” holds the same decks with every number also typed into the
      speaker notes; project “${esc(armShort("with-data"))}” holds the decks as saved plus the CSV that <code>chart_csv()</code> writes from each chart's own data.
      All go through <code>lib upload</code> + <code>proj doc import --wait</code>.</p>
      <div class="grid2">${figs.map((f) => `<div class="card"><h3 style="margin-top:0">chart ${esc(f.id)} · <span class="mono small">${esc(f.file)}.pptx</span></h3><img class="chartimg" src="/data/${esc(f.file)}-slide.png" alt="${esc(f.title)}"></div>`).join("")}</div>
      <div class="card"><table class="lint plain"><thead><tr><th>file</th><th>what read_deck() finds in it</th></tr></thead><tbody>
        ${decks.map((x) => `<tr><td class="mono">${esc(x.name)}</td><td>${x.values} chart values${x.notes ? ` · speaker notes with ${x.notes_values} values: <span class="small muted">“${esc(x.notes.slice(0, 90))}…”</span>` : ""}</td></tr>`).join("")}
        ${made.map((m) => `<tr><td class="mono">${esc(m.csv)}</td><td>written by chart_csv() from ${esc(m.deck)}: ${m.rows} rows, ${m.values} values, ${fmt(m.bytes)} bytes</td></tr>`).join("")}
      </tbody></table><p class="muted small">Every value is different, so a number in an answer names exactly one bar.</p></div>`;
  }

  function index() {
    const h = "The decks in the index — what MemoryLake keeps from each file";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const f = S.index.figures || [];
    const per = S.index.per || {};
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns, for each file, everything the index holds: the document summary, the chart's figure item (its text and summary),
      the text on the slide, table rows. This is the text MemoryRouter can attach. Chart values (all of them in the .pptx files) that are anywhere in it:
      ${ARMS.map((a) => `<b>${esc(armShort(a))}</b> ${per[a]?.found ?? "–"} of ${per[a]?.total ?? "–"}${per[a]?.notes ? ` (speaker notes in the index: ${per[a].notes_in_index} of ${per[a].notes})` : ""}`).join(" · ")}.</p>
      ${ARMS.map((a) => `<h2>${esc(armLabel(a))}</h2><div class="grid2">${f.filter((v) => v.arm === a).map(figCard).join("")}</div>`).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armLabel(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const order = { wrong: 0, partial: 1, none: 2, right: 3 };
    return `<h1>${h}${t ? ` — ${t.right}/${t.answers} right` : ""}</h1>
      <p class="lead">Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what it was given, so you can see
      which files MemoryRouter attached and how many of the chart's values their text held. ✓ right · ✗ wrong · ◐ incomplete · · no figure.</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => order[a.verdict] - order[b.verdict])[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">deck data: <b>${esc(r.truth)}</b></div>
          <div class="small">checker: ${r.entries?.length ? r.entries.map(entry).join(" · ") : `<code>${esc(r.attached || "—")}</code>`}</div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict === "wrong" || pick.verdict === "partial" ? "wrong" : ""}"><span class="who">answer ${mark(pick.verdict)}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}<br>${chkLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {}, per = S.summary.index || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>values in the index</th><th>right</th><th>wrong</th><th>incomplete</th><th>no figure</th><th>asked figures attached</th></tr></thead><tbody>
        ${ARMS.map((k) => `<tr><td><b>${esc(armLabel(k))}</b></td><td>${per[k] ? `${per[k].found}/${per[k].total}` : "–"}</td>${t[k] ? `<td>${t[k].right}/${t[k].answers}</td><td>${t[k].wrong}</td><td>${t[k].partial}</td><td>${t[k].none}</td><td>${t[k].checks.asked_values}/${t[k].checks.readable}</td>` : "<td>–</td><td></td><td></td><td></td><td></td>"}</tr>`).join("")}
      </tbody></table></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant said instead of the right answer</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      What MemoryRouter attaches, and what a summary says, vary from run to run; the README reports totals over all runs.</p>`;
  }

  function figureTool() {
    const ready = !!S.world, figs = S.data?.figures || [];
    const last = S.lookups[S.lookups.length - 1];
    return `<h1>Deck</h1>
      <p class="lead">One chart as the index holds it in each of the three projects — deck as saved, numbers in the notes, deck + chart data — next to the data inside the deck. Read-only: searches, no model call.</p>
      <div class="card"><div class="row">${figs.map((f) => `<button class="chip ${S.pick === f.id ? "on" : ""}" data-pick="${esc(f.id)}">chart ${esc(f.id)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="figure" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
        ${last ? `<div class="card"><h3 style="margin-top:0">chart ${esc(last.figure)} — ${esc(last.title)}</h3><p class="small mono">${Object.entries(last.values).map(([c, v]) => `${esc(c)} ${v.join(" / ")}`).join(" · ")}</p></div>
          <div class="grid2">${(last.views || []).map(figCard).join("")}</div>` : ""}`;
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
    st.querySelector('[data-act="figure"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/deck", { deck: S.pick });
      if (r) S.lookups.push(r);
      if (S.view === "figure") renderStage();
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
    if (!confirm("Delete the demo's boundaries, the three projects and the 6 files?")) return;
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
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, the three projects and the 6 files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Deck: one chart as the index holds it in each of the three projects.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "index", "asked", "tally", "summary", "router_unavailable", "figure", "done", "error"];
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

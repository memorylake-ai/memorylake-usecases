/* Project roster memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, two boundaries", view: "setup" },
    { n: 3, title: "The PMO's memory", sub: "six logs, the roster", view: "memory" },
    { n: 4, title: "Is Beacon in memory?", sub: "roster lookup vs search", view: "lookup" },
    { n: 5, title: "What the model is handed", sub: "the checker's copy", view: "attached" },
    { n: 6, title: "Decision logs only", sub: "one boundary over the logs", view: "logs" },
    { n: 7, title: "+ Roster document", sub: "the roster in the boundary", view: "doc" },
    { n: 8, title: "+ Roster in the prompt", sub: "read with fact list", view: "pinned" },
    { n: 9, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, lookup: null, attached: null, asked: [], tallies: {}, summary: null, unavailable: null,
    projects: [], pick: "Beacon", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["logs", "doc", "pinned"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { right: ["✓", "ok"], borrowed: ["⇄", "bad"], invented: ["✎", "bad"], other: ["✗", "bad"] };
  const ORDER = ["borrowed", "invented", "other", "right"];

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "project" ? "active" : ""}" data-view="project"><span class="n">#</span><span class="t">Project<small>on the roster? what search returns</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, lookup, attached, logs: () => arm("logs"), doc: () => arm("doc"), pinned: () => arm("pinned"), score, project: projectTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (a) => { const m = MARK[a.verdict] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}${a.empty ? "∅" : ""}</span>`; };
  const armLine = (t) => {
    if (!t) return "";
    const u = t.untracked, c = t.control;
    return `never tracked: ${u.right}/${u.answers} “not tracked” · ${u.borrowed} another project's value · ${u.invented} invented${u.empty ? ` (∅ ${u.empty})` : ""} · controls ${c.right}/${c.answers}`;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.logs)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-prr")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Asked about a project it never recorded, the assistant answers anyway.</h1>
      <p class="lead">Corvane Freight (fictional) keeps its PMO's decision logs in shared memory — six projects, one log each — and a PMO assistant answers from them:
      “what go-live date did we set for Project Atlas?”. It runs on MemoryRouter: every question is an ordinary model call with <code>--boundary</code> added.
      Then someone asks about Project Beacon, a Finance project the PMO never tracked. MemoryRouter usually attaches nothing, and the model invents a date, a vendor, a budget.
      This demo asks eight questions through three setups: the logs alone; the logs plus a roster document in the same boundary; and the roster kept as facts, read by the app
      with <code>fact list</code> and put in the system prompt. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 100 calls per run (gpt-4o-mini, plus 12 checker calls to claude-haiku-4-5).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-project-management-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for project management teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    const rows = [["logs", "the six decision logs"], ["doc", "the six logs + the roster document"], ["roster", "the roster as six facts (no boundary)"]];
    return `<h1>Three projects, two boundaries</h1>
      <p class="lead">One project holds the six decision logs; a second holds the same logs plus the PMO roster as a document; a third holds the roster as facts, one per tracked
      project. The first two get a boundary each. The third arm asks through the logs boundary, and the app reads the roster facts with <code>fact list</code> and puts them
      in the system prompt. The assistant never assembles memory itself: MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${rows.map(([k, t]) => `<dt>${esc(t)}</dt><dd class="mono small">${esc(w?.project?.[k] || "pending")}${w?.boundary?.[k] ? `<br><b>${esc(w.boundary[k])}</b>` : ""}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)} <span class="pill ${q.group === "control" ? "" : "err"}">${q.group === "untracked" ? "never tracked" : "control"}</span></p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The PMO's memory") : empty("The PMO's memory");
    const logs = S.data?.logs || [];
    return `<h1>The PMO's memory — six decision logs, and the roster</h1>
      <p class="lead">${esc(S.data?.company)} PMO, logs kept by ${esc(S.data?.lead)}: one Markdown file per project, uploaded to the Library and imported into both document projects.
      The roster says the PMO tracks exactly these six — once as a document, once as six facts.</p>
      <div class="grid2">${logs.map((p) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(p.name)}</h3><span class="mono small muted">${esc(p.file)}</span></div>
        <p class="small muted" style="margin:4px 0">${esc(p.about)} · owner ${esc(p.owner)} · sponsor ${esc(p.sponsor)}</p>
        ${p.decisions.map((b) => `<p class="small" style="margin:4px 0">· ${esc(b)}</p>`).join("")}</div>`).join("")}</div>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">${esc(S.data?.roster_file)}.md</h3><pre class="mono small wrap">${esc(S.data?.roster)}</pre></div>
        <div class="card"><h3 style="margin-top:0">The roster as facts</h3>${(S.memory?.facts || S.data?.facts || []).map((f) => `<p class="small mono" style="margin:4px 0">· ${esc(f)}</p>`).join("")}</div></div>`;
  }

  function lookup() {
    const h = "Is Project Beacon in memory?";
    if (!S.lookup) return running() ? waiting(h) : empty(h);
    return `<h1>${h} — the roster lookup vs search</h1>
      <p class="lead">For every project in the questions: <code>fact list --query &lt;name&gt;</code> on the roster (an exact substring match), and <code>search</code> with the question itself
      over the decision logs. Search ranks by topic and always returns its top hits — for projects that were never recorded too. The roster lookup is exact.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>project</th><th>roster facts</th><th>search with the question → top 3</th></tr></thead><tbody>
        ${S.lookup.rows.map((r) => `<tr><td><b>${esc(r.name)}</b>${r.tracked ? "" : ' <span class="pill err">never tracked</span>'}</td><td>${r.roster}</td><td class="mono small">${esc(r.search.join(", ") || "nothing")}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function attached() {
    const h = "What MemoryRouter hands the model";
    if (!S.attached) return unavailable() || (running() ? waiting(h) : empty(h));
    const qs = Object.fromEntries((S.data?.questions || []).map((q) => [q.id, q]));
    const who = (e) => e.roster ? "the PMO roster" : e.project ? `${e.project}'s log` : "";
    return `<h1>${h} — asked about a project it never recorded</h1>
      <p class="lead">For each question about a never-tracked project, ${esc(S.data?.check_model)} is asked through the roster-document boundary to copy out everything above the question.
      It is its own call, so what it is handed can differ from the answer calls. Labels as MemoryRouter writes them: <code>(file)</code>.
      ${S.attached.copies} copies: nothing attached in <b>${S.attached.empty}</b>; the roster attached in <b>${S.attached.roster}</b>.</p>
      ${(S.attached.checks || []).map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.q)} <span class="small muted">${esc(qs[c.q]?.say)}</span></h3>
        ${(c.copies || []).map((x, k) => `<p class="small muted" style="margin:8px 0 2px">copy ${k + 1}</p>` + (x.readable ? (x.entries.length ? x.entries.map((e) => `<div class="idx ${e.roster ? "ok" : ""}"><span class="tag">${esc(e.file || "no file label")}</span> ${esc(who(e))}</div>`).join("") : '<p class="muted small">no entry</p>')
          : x.nothing ? '<div class="idx bad">nothing attached (∅)</div>'
          : `<div class="idx">wrote NONE${x.said ? `: “${esc(x.said)}”` : ""}</div>`)).join("")}</div>`).join("")}`;
  }

  function arm(name) {
    const h = `Ask the PMO assistant — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const tag = { untracked: ["never tracked", "err"], control: ["control", ""] };
    return `<h1>${h}${t ? ` — ${t.untracked.right}/${t.untracked.answers} “not tracked”` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ⇄ another project's value · ✎ invented · ∅ nothing attached (the model on its own — still what the PMO reads).</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict))[0];
        const lent = pick ? Object.entries(pick.values || {}) : [];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">${esc(r.truth)}</div>
          <div class="row small" style="margin-top:4px"><span class="pill ${tag[r.group][1]}">${tag[r.group][0]}</span></div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map(mark).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick)}</span>${esc(pick.reply)}</div></div>` : ""}
          ${pick && pick.verdict === "borrowed" && lent.length ? `<p class="small">⇄ ${lent.map(([p, v]) => `“${esc(v.join("”, “"))}” is from <b>${esc(p)}</b>'s log`).join("; ")}</p>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>setup</th><th>never tracked: “not tracked”</th><th>another project's value</th><th>invented</th><th>∅</th><th>controls</th></tr></thead><tbody>
        ${ARMS.map((k) => { const u = t[k]?.untracked;
          return `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td>${u ? `<td>${u.right}/${u.answers}</td><td>${u.borrowed}</td><td>${u.invented}</td><td>${u.empty}</td><td>${t[k].control.right}/${t[k].control.answers}</td>` : "<td>–</td><td></td><td></td><td></td><td></td>"}</tr>`; }).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">Every answer is graded, the ∅ ones too (MemoryRouter attached nothing, the model answered on its own): it is still what the PMO reads.
      With the roster in the prompt, ∅ is harmless — the model has the list either way.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant said instead of “not tracked”</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function projectTool() {
    const ready = !!S.world;
    const all = [...(S.data?.untracked || []).map((u) => u.name), ...(S.data?.logs || []).map((p) => p.name)].map((n) => n.split(" ").pop());
    const last = S.projects[S.projects.length - 1];
    return `<h1>Project</h1>
      <p class="lead">One project name: is it on the roster (<code>fact list --query</code>, exact), and what does <code>search</code> return for it. Read-only: no model call.
      This is the check an app can make before asking any model.</p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${all.map((a) => `<button class="chip ${S.pick === a ? "on" : ""}" data-pick="${esc(a)}">${esc(a)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="project" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last ? `<div class="card ${last.facts.length ? "hl" : "warnc"}"><h3 style="margin-top:0">${esc(last.name)}</h3>
        <p class="small">${last.facts.length ? `On the roster (${last.facts.length} fact):` : "Not on the roster: <code>fact list --query</code> returns 0 facts. The app can answer “not tracked by the PMO” without asking a model."}</p>
        ${last.facts.map((f) => `<div class="idx ok">${esc(f)}</div>`).join("")}
        <div class="idx"><span class="tag">search</span>${esc(last.search.join(", ") || "nothing")}</div></div>` : ""}`;
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
    st.querySelector('[data-act="project"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/project", { name: S.pick });
      if (r) S.projects.push(r);
      if (S.view === "project") renderStage();
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
  function resetView() { S.memory = null; S.lookup = null; S.attached = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, the three projects, the roster facts and its Library files?")) return;
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
    else if (k === "lookup") S.lookup = d;
    else if (k === "attached") S.attached = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, the three projects, the roster facts and the Library files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Project: is a name on the roster, and what does search return for it.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "lookup", "attached", "asked", "tally", "summary", "router_unavailable", "project", "done", "error"];
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

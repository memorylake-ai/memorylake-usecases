/* Client alias memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, one boundary", view: "setup" },
    { n: 3, title: "The agency's memory", sub: "six briefs, the alias registry", view: "memory" },
    { n: 4, title: "Who is Project Kestrel?", sub: "registry lookup vs search", view: "lookup" },
    { n: 5, title: "What the model is handed", sub: "the checker's copy", view: "attached" },
    { n: 6, title: "Asked as written", sub: "the alias, as typed", view: "alias" },
    { n: 7, title: "+ Registry in the prompt", sub: "read with fact list", view: "registry" },
    { n: 8, title: "Alias rewritten", sub: "fact list --query, in the app", view: "rewrite" },
    { n: 9, title: "+ Attach check", sub: "no brief attached, no answer", view: "checked" },
    { n: 10, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, lookup: null, attached: null, asked: [], tallies: {}, summary: null, unavailable: null,
    looked: [], pick: "Project Kestrel", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["alias", "registry", "rewrite", "checked"];
  const armLabel = (a) => S.data?.arms?.[a]?.long || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { right: ["✓", "ok"], borrowed: ["⇄", "bad"], invented: ["✎", "bad"], declined: ["–", "warn"], held: ["◌", ""] };
  const ORDER = ["borrowed", "invented", "declined", "held", "right"];
  const byKey = () => Object.fromEntries((S.data?.clients || []).map((c) => [c.key, c]));
  const clientOfQ = (qid) => { const q = (S.data?.questions || []).find((x) => x.id === qid); return q ? byKey()[q.client] : null; };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "aliastool" ? "active" : ""}" data-view="aliastool"><span class="n">#</span><span class="t">Alias<small>which client? what does search say?</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, lookup, attached, alias: () => arm("alias"), registry: () => arm("registry"), rewrite: () => arm("rewrite"), checked: () => arm("checked"), score, aliastool: aliasTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (a) => { const m = MARK[a.verdict] || ["·", ""]; return `<span class="mark ${m[1]}">${m[0]}${a.empty && a.verdict !== "held" ? "∅" : ""}</span>`; };
  const armLine = (t) => {
    if (!t) return "";
    return `${t.right}/${t.answers} right · ${t.borrowed} another client's tagline · ${t.invented} invented${t.empty_invented ? ` (${t.empty_invented} with nothing attached)` : ""} · ${t.declined} “not specified”${t.held ? ` · ${t.held} held back` : ""}${t.retried ? ` · ${t.retried} needed a retry` : ""}`;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.briefs)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-cam")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>An account manager asks about “Project Kestrel”, and the assistant answers for another client.</h1>
      <p class="lead">Copperline Studio (fictional) keeps one brief per client in shared memory — six clients, each with an approved tagline — and an account assistant answers
      from them. It runs on MemoryRouter: every question is an ordinary model call with <code>--boundary</code> added. But the account managers don't say “Brightwater Foods”; they say
      “Project Kestrel”, a codename that appears in no brief. MemoryRouter retrieves on the words it is given, attaches some other client's brief, and the model answers with that
      client's tagline. This demo asks six alias questions four ways: as typed; with the alias registry (facts in MemoryLake) in the system prompt; with the alias rewritten to the
      client's name by the app; and rewritten, with an answer shown only when a brief was attached. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 110 calls per run (gpt-4o-mini, plus 18 checker calls to claude-haiku-4-5).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/client-context-memory-for-agencies" target="_blank" rel="noopener">memorylake.ai › Client context memory for agencies</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    const rows = [["briefs", "the six client briefs (documents)"], ["aliases", "the alias registry: one fact per alias (no boundary)"]];
    const k = byKey();
    return `<h1>Two projects, one boundary</h1>
      <p class="lead">One project holds the six client briefs and gets a boundary. A second holds the alias registry — what the account managers call each client, one fact each — and
      no boundary: the app reads it itself, with <code>fact list</code>. The assistant never assembles memory on its own: MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${rows.map(([key, t]) => `<dt>${esc(t)}</dt><dd class="mono small">${esc(w?.project?.[key] || "pending")}${w?.boundary?.[key] ? `<br><b>${esc(w.boundary[key])}</b>` : ""}</dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)} <span class="pill">${esc(q.kind)} · ${esc(k[q.client]?.name)}</span></p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The agency's memory") : empty("The agency's memory");
    const cs = S.data?.clients || [];
    return `<h1>The agency's memory — six client briefs, and the alias registry</h1>
      <p class="lead">${esc(S.data?.agency)}: one Markdown brief per client, uploaded to the Library and imported into the briefs project. Each brief names the client — never the alias.
      The registry says what the account managers call each one, one fact each.</p>
      <div class="grid2">${cs.map((c) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(c.name)}</h3><span class="mono small muted">${esc(c.file)}</span></div>
        <p class="small muted" style="margin:4px 0">${esc(c.product)} · contact ${esc(c.contact)} · launch ${esc(c.launch)}</p>
        <p class="small" style="margin:4px 0">approved tagline <b>“${esc(c.tagline)}”</b> · never “${esc(c.banned)}”</p>
        <p class="small" style="margin:4px 0"><span class="pill">${esc(c.kind)}</span> the account managers say <b>${esc(c.alias)}</b></p></div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">The alias registry as facts</h3>${(S.memory?.facts || S.data?.facts || []).map((f) => `<p class="small mono" style="margin:4px 0">· ${esc(f)}</p>`).join("")}</div>`;
  }

  function lookup() {
    const h = "Who is “Project Kestrel”?";
    if (!S.lookup) return running() ? waiting(h) : empty(h);
    return `<h1>${h} — the registry lookup vs search</h1>
      <p class="lead">For every alias in the questions: <code>fact list --query &lt;alias&gt;</code> on the registry (an exact text match, one fact naming the client), and <code>search</code>
      with the question as typed over the briefs. The alias is in no brief, so where the right brief ranks is luck: for the codenames it is often not in the top three.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>alias</th><th>registry says</th><th>search with the question → top 3</th><th>its brief</th></tr></thead><tbody>
        ${S.lookup.rows.map((r) => `<tr><td><b>${esc(r.alias)}</b> <span class="pill">${esc(r.kind)}</span></td><td>${esc(r.client || "not in the registry")}</td><td class="mono small">${esc(r.search.join(", ") || "nothing")}</td><td>${r.rank ? `#${r.rank}` : '<span class="mark bad">not in top 3</span>'}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function attached() {
    const h = "What MemoryRouter hands the model";
    if (!S.attached) return unavailable() || (running() ? waiting(h) : empty(h));
    const k = byKey();
    return `<h1>${h} — asked by alias</h1>
      <p class="lead">For each alias, ${esc(S.data?.check_model)} is asked through the briefs boundary to copy out everything above the question.
      It is its own call, so what it is handed can differ from the answer calls. Labels as MemoryRouter writes them: <code>(file)</code>.
      ${S.attached.copies} copies: the right client's brief in <b>${S.attached.right}</b>; only another client's brief in <b>${S.attached.other}</b>; nothing attached in <b>${S.attached.empty}</b>.</p>
      ${(S.attached.checks || []).map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.q)} <span class="small muted">${esc(c.alias)} = ${esc(k[c.client]?.name)}</span></h3>
        ${(c.copies || []).map((x, n) => `<p class="small muted" style="margin:8px 0 2px">copy ${n + 1}</p>` + (x.readable ? (x.entries.length ? x.entries.map((e) => `<div class="idx ${e.key === c.client ? "ok" : e.key ? "bad" : ""}"><span class="tag">${esc(e.file || "no file label")}</span> ${e.client ? `the brief for <b>${esc(e.client)}</b>${e.key === c.client ? " — the right client" : " — another client"}` : ""}</div>`).join("") : '<p class="muted small">no entry</p>')
          : x.nothing ? '<div class="idx">nothing attached (∅)</div>'
          : `<div class="idx">wrote NONE${x.said ? `: “${esc(x.said)}”` : ""}</div>`)).join("")}</div>`).join("")}`;
  }

  function arm(name) {
    const h = `Ask the account assistant — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const k = byKey();
    const reg = name === "registry" ? `<div class="card"><h3 style="margin-top:0">System prompt, as the app sends it on every call</h3><pre class="mono small wrap">${esc(S.data?.system)}

${esc(S.data?.registry_header)}
${(S.memory?.facts || S.data?.facts || []).slice().sort().map((f) => "- " + esc(f)).join("\n")}
${esc(S.data?.registry_footer)}</pre></div>` : "";
    return `<h1>${h}${t ? ` — ${t.right}/${t.answers} right` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ⇄ another client's tagline · ✎ invented · – “not specified” · ◌ held back by the app · ∅ nothing attached (the model on its own — still what the account manager reads).</p>
      ${reg}
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict))[0];
        const c = clientOfQ(r.q);
        const lent = pick && pick.verdict === "borrowed" ? (pick.hits || []).filter((x) => x !== c?.key) : [];
        const retried = r.answers.filter((a) => (a.tries || []).length > 1).length;
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          ${r.say !== r.typed ? `<div class="small muted">typed: “${esc(r.typed)}” → the app swapped in the client's name</div>` : ""}
          <div class="small">truth: ${esc(r.truth)}</div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map(mark).join(" ")}${retried ? ` <span class="pill">retried ${retried}×</span>` : ""}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">${pick.verdict === "held" ? "the app" : "answer"} ${mark(pick)}</span>${esc(pick.reply)}</div></div>` : ""}
          ${lent.length ? `<p class="small">⇄ ${lent.map((x) => `“${esc(k[x]?.tagline)}” is the tagline of <b>${esc(k[x]?.name)}</b>`).join("; ")} — not ${esc(c?.name)}</p>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>setup</th><th>right</th><th>⇄ another client's</th><th>✎ invented (∅)</th><th>– not specified</th><th>◌ held back</th><th>retried</th></tr></thead><tbody>
        ${ARMS.map((a) => { const u = t[a];
          return `<tr><td style="white-space:nowrap"><b>${esc(armShort(a))}</b></td>${u ? `<td>${u.right}/${u.answers}</td><td>${u.borrowed}</td><td>${u.invented} (${u.empty_invented})</td><td>${u.declined}</td><td>${u.held}</td><td>${u.retried}</td>` : "<td>–</td><td></td><td></td><td></td><td></td><td></td>"}</tr>`; }).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">Every answer is graded, the ∅ ones too (MemoryRouter attached nothing, the model answered on its own): it is still what the account manager reads.
      The registry in the system prompt tells the model who “Project Kestrel” is; it does not change what MemoryRouter retrieves.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant said instead of the right tagline</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function aliasTool() {
    const ready = !!S.world;
    const all = [...(S.data?.clients || []).map((c) => c.alias), "Project Osprey"];
    const last = S.looked[S.looked.length - 1];
    return `<h1>Alias</h1>
      <p class="lead">One alias, looked up exactly in the registry with <code>fact list --query</code> — the client it stands for, or nothing. And what <code>search</code> returns for
      a question that uses it. Read-only: no model call. This is the lookup the app makes before it asks any model.</p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${all.map((a) => `<button class="chip ${S.pick === a ? "on" : ""}" data-pick="${esc(a)}">${esc(a)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="lookup" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last ? `<div class="card ${last.client ? "hl" : "warnc"}"><h3 style="margin-top:0">“${esc(last.alias)}”</h3>
        <p class="small">${last.client ? `In the registry: <b>${esc(last.client)}</b>. The app asks about ${esc(last.client)}, not about “${esc(last.alias)}”.` : "Not in the registry: <code>fact list --query</code> found no fact for it. The app can say it does not know the client instead of letting retrieval guess."}</p>
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
    st.querySelector('[data-act="lookup"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/alias", { alias: S.pick });
      if (r) S.looked.push(r);
      if (S.view === "aliastool") renderStage();
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
    if (!confirm("Delete the demo's boundary, the two projects, the alias facts and its Library files?")) return;
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
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary, the two projects, the alias facts and the Library files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Alias: which client an alias stands for, and what search returns for it.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "lookup", "attached", "asked", "tally", "summary", "router_unavailable", "alias", "done", "error"];
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

/* Applied twice — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const plain = (v) => (v === null || v === undefined ? "null" : typeof v === "number" ? fmt(v) : String(v));
  const money = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, two boundaries", view: "setup" },
    { n: 3, title: "Workflow memory", sub: "5 tools × 3 versions", view: "workflows" },
    { n: 4, title: "Records and policies", sub: "8 records, 4 policies", view: "records" },
    { n: 5, title: "What memory gives", sub: "the answer key, by code", view: "key" },
    { n: 6, title: "Bare tools", sub: "records + policies attached", view: "bare" },
    { n: 7, title: "Bare tools, no policies", sub: "the control", view: "nopolicy" },
    { n: 8, title: "Described tools", sub: "“TOTAL, never subtract”", view: "described" },
    { n: 9, title: "Tools take the record id", sub: "the tool reads memory", view: "record" },
    { n: 10, title: "A second model", sub: "claude-haiku-4-5", view: "claude" },
    { n: 11, title: "Scoreboard", sub: "all answers this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, workflows: null, records: null, key: null, asked: [], tallies: {}, summary: null, unavailable: null,
    asks: [], pick: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["bare", "nopolicy", "described", "record", "claude_bare", "claude_record"];
  const KINDS = ["recorded", "whatif"];
  const arm = (a) => S.data?.arms?.[a] || {};
  const kindLabel = (k) => S.data?.kinds?.[k] || k;
  const MARK = { right: ["✓", "ok"], twice: ["⇣", "bad"], wrong: ["✗", "bad"], missed: ["–", "warn"] };
  const ST = { ok: ["✓", "ok"], twice: ["⇣", "bad"], delta: ["Δ", "warn"], wrong: ["✗", "bad"] };
  const ORDER = ["twice", "wrong", "missed", "right"];

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "asktool" ? "active" : ""}" data-view="asktool"><span class="n">?</span><span class="t">Ask<small>one question, record-id tools</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, workflows, records, key, bare: () => armView(["bare"]), nopolicy: () => armView(["nopolicy"]),
      described: () => armView(["described"]), record: () => armView(["record"]), claude: () => armView(["claude_bare", "claude_record"]),
      score, asktool: askTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (a) => { const m = MARK[a.verdict] || ["·", ""]; return `<span class="mark ${m[1]}">${m[0]}${a.empty ? "∅" : ""}</span>`; };
  const kindLine = (u) => {
    if (!u || !u.answers) return "—";
    let s = `${u.right}/${u.answers} ✓`;
    if (u.twice) s += ` · ${u.twice} ⇣`;
    if (u.wrong) s += ` · ${u.wrong} ✗`;
    if (u.missed) s += ` · ${u.missed} –`;
    return s;
  };
  const callsLine = (t) => { const n = KINDS.reduce((s, k) => s + (t?.[k]?.calls || 0), 0); const w = KINDS.reduce((s, k) => s + (t?.[k]?.calls_twice || 0), 0); return `${w}/${n}`; };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.records)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-atm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The rule is in memory and in the tool, and the model applies it before the tool does.</h1>
      <p class="lead">Quillhaven Bookkeeping (fictional) tuned five calculator tools in its custom GPT and stores them once in MemoryLake as workflow memory, so any model can call them.
      The records (timesheets, bookings, a mileage log, an invoice, a claim) and the policies (40 hours, a 50,000 dollar quota, 30 commuting miles, a 10-day grace period) are in memory too — and each tool applies its own threshold.
      A model that has just read the policy takes the threshold off first; the tool takes it off again; the answer is exact to the cent and wrong.
      This demo asks ten questions through MemoryRouter four ways with gpt-4o-mini and two with claude-haiku-4-5, and checks every number the model passes against the record. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 400 calls per run (gpt-4o-mini and claude-haiku-4-5).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/cross-tool-prompt-and-workflow-memory-for-small-teams" target="_blank" rel="noopener">memorylake.ai › Cross-tool prompt and workflow memory for small teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, two boundaries</h1>
      <p class="lead">The workflow tools go into their own project — the app reads them with <code>fact list</code> and hands them to the model; they are never attached as context.
      Records and policies are separate projects so one boundary can leave the policies out: that is the control in step 7.</p>
      <div class="grid2"><div class="card"><dl class="kv">
        <dt>workflows</dt><dd class="mono small">${esc(w?.workflows || "pending")}</dd>
        <dt>records</dt><dd class="mono small">${esc(w?.records || "pending")}</dd>
        <dt>policies</dt><dd class="mono small">${esc(w?.policies || "pending")}</dd>
        <dt>boundary: records + policies</dt><dd class="mono small"><b>${esc(w?.b_full || "pending")}</b></dd>
        <dt>boundary: records only</dt><dd class="mono small"><b>${esc(w?.b_records || "pending")}</b></dd></dl></div>
        <div class="card"><h3 style="margin-top:0">The questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)} <span class="pill">${esc(kindLabel(q.kind))}</span></p>`).join("")}</div></div>`;
  }

  function workflows() {
    const h = "Workflow memory — five tools, three versions";
    const ts = S.workflows?.tools;
    if (!ts) return running() ? waiting(h) : empty(h);
    const by = {};
    ts.forEach((t) => { (by[t.name] = by[t.name] || {})[t.schema] = t; });
    return `<h1>${h}</h1>
      <p class="lead">Each version of each tool is one fact; the tool definition (JSON schema) is its metadata. The <b>bare</b> version is what the team's custom GPT had;
      <b>described</b> spells out the unit of every parameter; <b>record id</b> takes the record and reads the value from memory itself.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>tool</th><th>bare</th><th>described</th><th>record id</th></tr></thead><tbody>
        ${Object.entries(by).map(([n, v]) => `<tr><td class="mono small"><b>${esc(n)}</b></td>${["bare", "described", "record"].map((s) => `<td class="small">${v[s] ? Object.entries(v[s].params).map(([k, d]) => `<code>${esc(k)}</code> ${esc(d)}`).join("<br>") : ""}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>`;
  }

  function records() {
    const h = "Records and policies";
    const r = S.records;
    if (!r) return running() ? waiting(h) : empty(h);
    return `<h1>${h}</h1>
      <p class="lead">One fact per record, with its typed fields as metadata; one fact per policy. MemoryRouter attaches both to the model in every arm except step 7.</p>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">Records</h3><table class="lint plain"><tbody>
        ${r.records.map((x) => `<tr><td class="mono small"><b>${esc(x.id)}</b></td><td class="small">${esc(x.text.replace(/^[^:]*\): /, ""))}</td></tr>`).join("")}</tbody></table></div>
        <div class="card"><h3 style="margin-top:0">Policies</h3>${r.policies.map((x) => `<p class="small">${esc(x.text)}</p>`).join("")}</div></div>`;
  }

  function key() {
    const h = "What memory gives";
    const k = S.key;
    if (!k) return running() ? waiting(h) : empty(h);
    const qs = Object.fromEntries((S.data?.questions || []).map((q) => [q.id, q]));
    return `<h1>${h} — the answer key, worked out by code</h1>
      <p class="lead">The recorded values, read back with <code>fact list</code>, put through the same five tools. Each tool takes its threshold off itself:
      ${Object.entries(k.thresholds).map(([f, v]) => `<code>${esc(f)} − ${esc(plain(v))}</code>`).join(" · ")}.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>tool call with the recorded values</th><th>amount</th></tr></thead><tbody>
        ${k.key.map((e) => `<tr><td><b>${esc(e.q)}</b> ${esc(qs[e.q]?.say)}</td><td class="mono small">${esc(qs[e.q]?.tool)}(${Object.entries(e.inputs).filter(([f]) => f !== "deductible").map(([f, v]) => `${esc(f)} ${esc(plain(v))}`).join(", ")})</td><td class="mono small" style="white-space:nowrap">${money(e.value)}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function callBox(c) {
    let h = `<div class="mono small">${esc(c.text)}</div>`;
    h += (c.lines || []).map((l) => `<div class="small">${esc(l.replace(/^\s*│\s*/, ""))}</div>`).join("");
    return `<div class="idx">${h}</div>`;
  }

  function armView(names) {
    const first = arm(names[0]);
    const h = names.length > 1 ? "The same workflow memory, a second model" : `Ask the bookkeeping assistant — ${first.short || names[0]}`;
    const rows = S.asked.filter((r) => names.includes(r.arm));
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const blocks = names.map((name) => {
      const rs = rows.filter((r) => r.arm === name);
      if (!rs.length) return "";
      const t = S.tallies[name];
      const a = arm(name);
      return `<div class="card"><h2 style="margin-top:0">${esc(a.short)}</h2><p class="small muted">${esc(a.long)} · ${esc(a.model)}, each question ${a.runs}×</p>
        ${t ? `<p class="small">${KINDS.map((k) => `${esc(kindLabel(k))}: <b>${kindLine(t[k])}</b>`).join(" · ")} · tool calls with the threshold already off: <b>${callsLine(t)}</b></p>` : ""}</div>`
        + rs.map((r) => {
          const pick = [...r.answers].sort((x, y) => ORDER.indexOf(x.verdict) - ORDER.indexOf(y.verdict))[0];
          return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
            <div class="small">the record gives: <b>${esc(r.key)}</b> <span class="muted">(${esc(r.record)})</span></div>
            <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map(mark).join(" ")}</div>
            ${pick ? `${(pick.calls || []).slice(0, 2).map(callBox).join("")}<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick)}</span>${esc(pick.clause)}</div></div>` : ""}</div>`;
        }).join("");
    }).join("");
    return `<h1>${h}</h1>
      <p class="lead">✓ right · ⇣ wrong, and a tool call carried a value with the threshold already taken off · ✗ wrong · – no amount (or still calling tools after 4 turns) · ∅ MemoryRouter attached nothing.</p>
      ${blocks}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    const b = t.bare, n = t.nopolicy;
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>setup</th>${KINDS.map((k) => `<th>${esc(kindLabel(k))}</th>`).join("")}<th>tool calls, threshold already off</th></tr></thead><tbody>
        ${ARMS.map((x) => `<tr><td style="white-space:nowrap"><b>${esc(arm(x).short)}</b></td>${KINDS.map((k) => `<td>${t[x] ? kindLine(t[x][k]) : "–"}</td>`).join("")}<td>${t[x] ? callsLine(t[x]) : "–"}</td></tr>`).join("")}
      </tbody></table>
      ${b && n ? `<p class="small" style="margin-bottom:0">Same bare tools, policies taken out of the attached memory: from a record <b>${b.recorded.right}/${b.recorded.answers} → ${n.recorded.right}/${n.recorded.answers}</b> (applied twice ${b.recorded.twice} → ${n.recorded.twice}).</p>` : ""}</div>
      ${(S.summary.twice || []).length ? `<div class="card"><h3 style="margin-top:0">Inputs the model passed with the threshold already taken off</h3><pre class="mono small wrap">${esc(S.summary.twice.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant answered instead of what the records give</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function askTool() {
    const ready = !!S.world;
    const all = (S.data?.questions || []).map((q) => q.say);
    if (!S.pick) S.pick = S.data?.example || all[0] || "";
    const last = S.asks[S.asks.length - 1];
    return `<h1>Ask</h1>
      <p class="lead">One question the step-9 way: the model names the record, and the tool reads the recorded value from typed memory — nothing for the model to take off first.</p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${all.map((x) => `<button class="chip ${S.pick === x ? "on" : ""}" data-pick="${esc(x)}">${esc(x)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><input id="ask-q" value="${esc(S.pick)}" style="flex:1"><button class="btn primary" data-act="ask" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last ? `<div class="card hl"><h3 style="margin-top:0">“${esc(last.say)}”</h3>
        ${(last.calls || []).map(callBox).join("") || '<div class="idx muted small">no tool call</div>'}
        <div class="chat"><div class="msg bot"><span class="who">answer</span>${esc(last.reply || "(no reply)")}</div></div></div>` : ""}`;
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
    st.querySelector('[data-act="ask"]')?.addEventListener("click", async (e) => {
      S.pick = $("#ask-q").value.trim() || S.pick;
      e.target.disabled = true; e.target.textContent = "Asking…";
      const r = await api("/api/ask", { question: S.pick });
      if (r) S.asks.push(r);
      if (S.view === "asktool") renderStage();
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
  function resetView() { S.workflows = null; S.records = null; S.key = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's two boundaries and three projects with their facts?")) return;
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
    else if (k === "workflows") S.workflows = d;
    else if (k === "records") S.records = d;
    else if (k === "key") S.key = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the two boundaries and three projects with their facts are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: one question, record-id tools.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const EVENTS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "workflows", "records", "key", "asked", "tally", "summary", "router_unavailable", "one", "workflows_view", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    EVENTS.forEach((kind) => es.addEventListener(kind, (e) => { if (e.data) onEvent(JSON.parse(e.data)); }));
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
      EVENTS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
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

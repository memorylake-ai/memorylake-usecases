/* Tool input memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const plain = (v) => (v === null || v === undefined ? "null" : typeof v === "number" && Number.isInteger(v) ? fmt(v) : String(v));
  const money = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "The agent's memory", sub: "13 tool results", view: "memory" },
    { n: 4, title: "Typed memory", sub: "latest result per field", view: "typed" },
    { n: 5, title: "The model does the arithmetic", sub: "router chat --boundary", view: "direct" },
    { n: 6, title: "A calculator, inputs copied", sub: "compute_charge(…every input…)", view: "copied" },
    { n: 7, title: "The tool reads typed memory", sub: "compute_charge(account, quantity)", view: "typedarm" },
    { n: 8, title: "Scoreboard", sub: "all answers this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, typed: null, asked: [], tallies: {}, summary: null, unavailable: null,
    asks: [], pick: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["direct", "copied", "typed"];
  const KINDS = ["lookup", "computed", "missing"];
  const armLabel = (a) => S.data?.arms?.[a]?.long || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const kindLabel = (k) => S.data?.kinds?.[k] || k;
  const MARK = { right: ["✓", "ok"], wrong: ["✗", "bad"], invented: ["✎", "bad"], missed: ["–", "warn"] };
  const ST = { ok: ["✓", "ok"], wrong: ["✗", "bad"], invented: ["✎", "bad"], dropped: ["∅", "warn"] };
  const ORDER = ["wrong", "invented", "missed", "right"];

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "asktool" ? "active" : ""}" data-view="asktool"><span class="n">?</span><span class="t">Ask<small>one question, the typed tool</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, typed, direct: () => arm("direct"), copied: () => arm("copied"), typedarm: () => arm("typed"), score, asktool: askTool }[S.view];
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
    if (u.wrong) s += ` · ${u.wrong} ✗`;
    if (u.missed) s += ` · ${u.missed} –`;
    if (u.invented) s += ` · ${u.invented} ✎`;
    return s;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3–4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-tim")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Hand the arithmetic to a tool, and the model starts making up the tool's inputs.</h1>
      <p class="lead">Vellumtide (fictional) runs a billing assistant: a tool-calling agent whose earlier calls — get_account, get_usage, get_contract, get_rate_card — are remembered in MemoryLake, each with its typed fields.
      Ask it for a charge and it gets the arithmetic wrong. Give it a calculator tool and the arithmetic is right — but for the one customer whose usage was never fetched, the model fills <code>api_calls</code> in itself,
      and the calculator turns the guess into an exact-looking bill. This demo asks fourteen questions three ways, every call through MemoryRouter: the model doing the arithmetic, a calculator fed inputs the model copies,
      and a calculator that reads its inputs from typed memory itself. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 250 gpt-4o-mini calls per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-patterns-for-tool-calling-agents" target="_blank" rel="noopener">memorylake.ai › Memory patterns for tool-calling agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>One project, one boundary</h1>
      <p class="lead">The remembered tool results go into one project as facts, and a boundary covers it. Every model call in steps 5–7 goes through <code>router chat --body … --boundary</code>;
      the three setups differ only in what the model is asked to do with the memory.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>tool-results project</dt><dd class="mono small">${esc(w?.project || "pending")}</dd>
        <dt>boundary</dt><dd class="mono small"><b>${esc(w?.boundary || "pending")}</b></dd></dl></div>
        <div class="card"><h3 style="margin-top:0">The questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)} <span class="pill">${esc(kindLabel(q.kind))}</span></p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The agent's memory") : empty("The agent's memory");
    const rs = S.memory?.results || S.data?.results || [];
    return `<h1>The agent's memory — ${rs.length} tool results</h1>
      <p class="lead">Each earlier tool call is one fact (<code>fact add</code>), and its typed fields go on as metadata (<code>fact update --metadata</code>): <code>{tool, account, returned, fields}</code>.
      Halden Freight's seats changed between two <code>get_account</code> calls; Pellow &amp; Finch never had <code>get_usage</code> called.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>returned</th><th>tool</th><th>account</th><th>fields (metadata)</th></tr></thead><tbody>
        ${rs.map((r) => `<tr><td class="mono small">${esc(r.returned)}</td><td class="mono small">${esc(r.tool)}</td><td class="small">${esc(r.name || "")} <span class="mono muted">${esc(r.account || "")}</span></td>
          <td class="mono small">${Object.entries(r.fields).map(([k, v]) => `${esc(k)}: ${esc(plain(v))}`).join(" · ")}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function typed() {
    const h = "Typed memory";
    if (!S.typed) return running() ? waiting(h) : empty(h);
    const t = S.typed;
    const cell = (row, k) => {
      const f = row.fields[k];
      if (!f) return `<span class="mark bad">— no ${k === "api_calls" ? "get_usage" : k === "discount_pct" ? "get_contract" : "get_account"} result</span>`;
      const was = (f.replaced || []).filter((x) => x !== f.value);
      return `${esc(plain(f.value))}${k === "discount_pct" ? "%" : ""}<div class="muted small">${esc(f.tool)} ${esc(f.returned)}${was.length ? ` · replaced ${esc(was.map(plain).join(", "))}` : ""}</div>`;
    };
    const ex = Object.fromEntries((t.expected || []).map((e) => [e.q, e.value]));
    return `<h1>${h} — the latest result per account and field</h1>
      <p class="lead">Read back with <code>fact list</code> and the metadata alone, no parsing of text. This is what the step-7 tool reads; the amounts below are worked out by code from it and are the answer key for steps 5–7.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>account</th><th>tier</th><th>seats</th><th>API calls (Oct)</th><th>discount</th></tr></thead><tbody>
        ${t.rows.map((r) => `<tr><td><b>${esc(r.name)}</b> <span class="mono muted small">${esc(r.account)}</span></td>${["tier", "seats", "api_calls", "discount_pct"].map((k) => `<td>${cell(r, k)}</td>`).join("")}</tr>`).join("")}
      </tbody></table>
      <p class="small muted" style="margin-bottom:0">rate card: Growth ${esc(t.rates.growth_seat)} dollars/seat incl. ${fmt(t.rates.growth_included_calls)} calls · Scale ${esc(t.rates.scale_seat)} dollars/seat incl. ${fmt(t.rates.scale_included_calls)} calls · ${esc(t.rates.overage_per_1000_calls)} dollars per 1,000 calls above</p></div>
      <div class="card"><h3 style="margin-top:0">Answer key</h3><table class="lint plain"><tbody>
        ${(S.data?.questions || []).map((q) => { const v = ex[q.id]; return `<tr><td><b>${esc(q.id)}</b> ${esc(q.say)}</td><td class="mono small" style="white-space:nowrap">${v === null || v === undefined ? '<span class="mark warn">No record</span>' : q.kind === "lookup" ? esc(plain(v)) : money(v) + " dollars"}</td></tr>`; }).join("")}
      </tbody></table></div>`;
  }

  function callBox(c) {
    let h = `<div class="mono small">${esc(c.text)}</div>`;
    if (c.audit) h += `<div class="small">inputs vs memory: ${c.audit.map((x) => { const m = ST[x.status]; return `${esc(x.field)} ${esc(plain(x.given))} <span class="mark ${m[1]}">${m[0]}</span>${x.status === "ok" ? "" : ` <span class="muted">(memory: ${x.status === "invented" ? esc(x.source) : esc(plain(x.memory)) + ", " + esc(x.source)})</span>`}`; }).join(" · ")}</div>`;
    if (c.read) h += `<div class="small">read from memory: ${c.read.map((r) => `${esc(r.field)} ${esc(plain(r.value))} <span class="muted">(${esc(r.tool)} ${esc(r.returned)})</span>`).join(" · ") || "nothing"}</div>`;
    const r = c.result || {};
    h += `<div class="small">→ ${"amount_dollars" in r ? `<b>${money(r.amount_dollars)} dollars</b>` : `<span class="mark warn">${esc(r.error)}</span>`}</div>`;
    return `<div class="idx">${h}</div>`;
  }

  function arm(name) {
    const h = `Ask the billing assistant — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const tool = S.data?.tools?.[name]?.[0]?.function;
    const sys = `<div class="card"><h3 style="margin-top:0">System prompt</h3><pre class="mono small wrap">${esc(S.data?.system?.[name])}</pre>
      ${tool ? `<p class="small" style="margin-bottom:0">tool offered: <code>${esc(tool.name)}(${esc(Object.keys(tool.parameters.properties).join(", "))})</code> — ${esc(tool.description)}</p>` : ""}</div>`;
    const groups = ["computed", "missing", "lookup"].map((k) => {
      const rs = rows.filter((r) => r.qkind === k);
      if (!rs.length) return "";
      return `<h2>${esc(kindLabel(k))}${t ? ` <span class="small muted">${kindLine(t[k])}</span>` : ""}</h2>` + rs.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict) || (b.made_up || []).length - (a.made_up || []).length)[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">memory gives: <b>${esc(r.key)}</b> <span class="muted">(${esc(r.fact)})</span></div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map(mark).join(" ")}</div>
          ${pick ? `${(pick.calls || []).slice(0, 2).map(callBox).join("")}<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick)}</span>${esc(pick.clause)}</div></div>` : ""}</div>`;
      }).join("");
    }).join("");
    return `<h1>${h}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ✗ wrong · – no amount although memory has one · ✎ a value where memory has none · ∅ MemoryRouter attached nothing.</p>
      ${sys}${groups}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    const a = t.direct, c = t.copied, ty = t.typed;
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>setup</th>${KINDS.map((k) => `<th>${esc(kindLabel(k))}</th>`).join("")}</tr></thead><tbody>
        ${ARMS.map((x) => `<tr><td style="white-space:nowrap"><b>${esc(armShort(x))}</b></td>${KINDS.map((k) => `<td>${t[x] ? kindLine(t[x][k]) : "–"}</td>`).join("")}</tr>`).join("")}
      </tbody></table>
      ${a && c ? `<p class="small">Moving the arithmetic into a tool: charges <b>${a.computed.right}/${a.computed.answers} → ${c.computed.right}/${c.computed.answers}</b>. The account with no usage on record: <b>${a.missing.right}/${a.missing.answers} → ${c.missing.right}/${c.missing.answers}</b>.</p>` : ""}
      ${ty ? `<p class="small" style="margin-bottom:0">The tool reading typed memory itself: charges <b>${ty.computed.right}/${ty.computed.answers}</b>, no usage on record <b>${ty.missing.right}/${ty.missing.answers}</b>.</p>` : ""}</div>
      ${(S.summary.made_up || []).length ? `<div class="card"><h3 style="margin-top:0">Inputs the model put into compute_charge that memory does not have (step 6)</h3><pre class="mono small wrap">${esc(S.summary.made_up.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant answered instead of what memory gives</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function askTool() {
    const ready = !!S.world;
    const all = (S.data?.questions || []).filter((q) => q.kind !== "lookup").map((q) => q.say);
    if (!S.pick) S.pick = S.data?.example || all[0] || "";
    const last = S.asks[S.asks.length - 1];
    return `<h1>Ask</h1>
      <p class="lead">One question the step-7 way: the model calls <code>compute_charge</code> with the account and the quantity, and the tool reads every input from typed memory — the latest result per field, with where it came from.</p>
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
  function resetView() { S.memory = null; S.typed = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundary and the tool-results project with its facts?")) return;
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
    else if (k === "typed") S.typed = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundary and the tool-results project with its facts are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Ask: one question, the typed tool.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const EVENTS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "typed", "asked", "tally", "summary", "router_unavailable", "one", "bill", "done", "error"];
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

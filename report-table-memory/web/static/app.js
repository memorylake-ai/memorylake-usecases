/* Report table memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, two boundaries", view: "setup" },
    { n: 3, title: "What the desk keeps", sub: "6 IR reports, 6 model exports", view: "memory" },
    { n: 4, title: "The tables in the index", sub: "how many ways each row reads", view: "index" },
    { n: 5, title: "IR reports (PDF)", sub: "through MemoryRouter", view: "pdf" },
    { n: 6, title: "Model exports (.xlsx)", sub: "through MemoryRouter", view: "xlsx" },
    { n: 7, title: "Control", sub: "only the separator changes", view: "control" },
    { n: 8, title: "Number check", sub: "before an answer goes out", view: "guard" },
    { n: 9, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, control: [], guard: null, summary: null, unavailable: null,
    lookups: [], pick: "Vossberg", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = { pdf: "IR reports (PDF)", xlsx: "model exports (.xlsx)" };
  const MARK = { wrong: ["✗", "bad"], right: ["✓", "ok"], none: ["·", ""] };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "rows" ? "active" : ""}" data-view="rows"><span class="n">#</span><span class="t">Rows<small>any company's table, any time</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, pdf: () => arm("pdf"), xlsx: () => arm("xlsx"), control, guard, score, rows }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const num = (v) => Number.isInteger(v) ? Number(v).toLocaleString("en-US") : String(+Number(v).toFixed(3));
  const ways = (r) => (r.readings || []).map((rd) => rd.map(num).join(" | ")).join("  or  ") || "?";
  const sideLine = (t) => t ? `two-way rows ${t.two.right}/${t.two.answers} right · ${t.two.wrong} wrong &nbsp;·&nbsp; one-way rows ${t.one.right}/${t.one.answers} right · ${t.one.wrong} wrong` : "";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.pdf)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 7 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-rtm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>In a German report, 4,5 means four and a half. In the text MemoryRouter attaches, the comma is also the column separator.</h1>
      <p class="lead">Kestrel Ridge Capital (fictional) covers European industrials. The desk keeps each company's quarterly IR report (PDF) in MemoryLake, and the research
      assistant runs on MemoryRouter: every question is an ordinary model call with <code>--boundary</code> added. MemoryRouter attaches a PDF table as comma-separated text
      with nothing quoted, so <b>Lieferzeit (Tage) | 12 | 4,5</b> arrives as <code>Lieferzeit (Tage),12,4,5</code> — which splits two ways. This demo counts the ways each row
      splits, asks eight PM questions through the PDFs and through .xlsx exports of the same tables, and shows that only the separator matters.
      It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 112 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/financial-research-memory-for-hedge-funds" target="_blank" rel="noopener">memorylake.ai › Financial research memory for hedge funds</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two projects, two boundaries</h1>
      <p class="lead">The IR reports go in one project, as published. The same KPI tables, keyed into the desk's models and exported as .xlsx, go in another.
      One boundary over each. The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>IR reports (PDF)</dt><dd class="mono small">${esc(w?.pdf || "pending")}</dd>
        <dt>Model exports (.xlsx)</dt><dd class="mono small">${esc(w?.xlsx || "pending")}</dd>
        <dt>Boundary: PDF</dt><dd class="mono small"><b>${esc(w?.boundary?.pdf || "pending")}</b></dd>
        <dt>Boundary: .xlsx</dt><dd class="mono small"><b>${esc(w?.boundary?.xlsx || "pending")}</b></dd></dl></div>
        <div class="card"><h3 style="margin-top:0">The PM's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What the desk keeps") : empty("What the desk keeps");
    const cs = S.data?.companies || [];
    return `<h1>${S.memory.pdf} IR reports and ${S.memory.xlsx} model exports</h1>
      <p class="lead">One KPI table per company, the same numbers in both: the PDF prints them the way the company does, the .xlsx holds them as numbers.
      Both go through <code>lib upload</code> + <code>proj doc import --wait</code>.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>company</th><th></th><th>the PDF prints</th><th>a row, as printed</th></tr></thead><tbody>
        ${cs.map((c) => { const r = c.rows[1]; return `<tr><td><b>${esc(c.company)}</b></td><td>${esc(c.country)}</td><td class="mono">${c.convention === "de" ? "412,6 · 2.140" : "154.2 · 2,310"}</td>
          <td class="mono small">${esc(r.label)} | ${r.cells.map(esc).join(" | ")}</td></tr>`; }).join("")}</tbody></table></div>`;
  }

  function index() {
    const h = "The tables in the index";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const two = S.index.rows.filter((r) => (r.readings || []).length > 1).length;
    return `<h1>${h} — ${two} of ${S.index.rows.length} rows read two ways</h1>
      <p class="lead"><code>search --types document</code> returns each table as one entry: the text MemoryRouter attaches. From the PDF it is comma-separated values with nothing
      quoted; from the .xlsx, one <code>column: value</code> line per row. Every PDF row is split every way the company's number format allows — before anything is asked.</p>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">Vossberg, from the PDF</h3><pre class="mono small">${esc(S.index.pdf_chunk)}</pre></div>
        <div class="card"><h3 style="margin-top:0">Vossberg, from the .xlsx</h3><pre class="mono small">${esc(S.index.xlsx_chunk || "(not returned)")}</pre></div></div>
      <div class="card"><table class="lint plain"><thead><tr><th></th><th>company</th><th>row, as indexed</th><th>printed</th><th>readings</th></tr></thead><tbody>
        ${S.index.rows.map((r) => { const n = (r.readings || []).length; return `<tr class="${n > 1 ? "warn" : ""}"><td>${n > 1 ? "⚠" : ""}</td><td>${esc(r.company)}</td>
          <td class="mono small">${esc(r.line || "(not returned)")}</td><td class="mono small">${r.printed.map(esc).join(" | ")}</td><td class="small">${n} · ${esc(ways(r))}</td></tr>`; }).join("")}</tbody></table></div>`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${ARMS[name]}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    return `<h1>${h}${t ? ` — two-way rows: ${t.two.wrong}/${t.two.answers} wrong` : ""}</h1>
      <p class="lead">Each question is answered ${S.data?.runs || 4}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what it was given,
      so you can see the row as attached. ✓ both quarters right · ✗ a figure from the other reading (or another row) · · no figure.</p>
      ${rows.map((r) => {
        const show = r.answers.find((a) => a.verdict === "wrong") || r.answers[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">PDF row in the index <code>${esc(r.line || "(not returned)")}</code> · ${r.ways} reading${r.ways === 1 ? "" : "s"} · printed <code>${r.printed.map(esc).join(" | ")}</code></div>
          <div class="small" style="margin-top:4px">checker, the row as attached: <code>${esc(r.attached || "—")}</code></div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${show ? `<div class="chat"><div class="msg bot ${show.verdict === "wrong" ? "wrong" : ""}"><span class="who">answer ${mark(show.verdict)}</span>${esc(show.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${sideLine(t)} · checker found the row attached ${t.checks.attached}/${t.checks.readable}</div>` : ""}`;
  }

  function control() {
    const h = "Control — only the separator changes";
    if (!S.control.length) return unavailable() || (running() ? waiting(h) : empty(h));
    return `<h1>${h}</h1>
      <p class="lead">No boundary: <code>router chat --body</code> with MemoryRouter's own preamble and one entry holding the company's table — once exactly as the index wrote it,
      once with the PDF's own cell boundaries ( | ). Same questions, same model, same numbers.</p>
      ${S.control.map((c) => { const wrong = c.answers.filter((a) => a.verdict === "wrong").length, ex = c.answers.find((a) => a.verdict === "wrong") || c.answers[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(c.label)} <span class="pill ${wrong ? "err" : "ok"}">${wrong}/${c.answers.length} wrong</span></h3>
          <div class="row">${(S.data?.questions || []).map((q) => `<span><b>${esc(q.id)}</b> ${c.answers.filter((a) => a.q === q.id).map((a) => mark(a.verdict)).join(" ")}</span>`).join("&nbsp;&nbsp;&nbsp;")}</div>
          ${ex ? `<div class="chat"><div class="msg bot ${ex.verdict === "wrong" ? "wrong" : ""}"><span class="who">${esc(ex.q)} ${mark(ex.verdict)}</span>${esc(ex.reply)}</div></div>` : ""}</div>`; }).join("")}`;
  }

  function guard() {
    const h = "Number check — before an answer goes out";
    if (!S.guard) return running() ? waiting(h) : empty(h);
    const g = S.guard, pdf = S.asked.filter((r) => r.arm === "pdf"), xl = S.asked.filter((r) => r.arm === "xlsx");
    return `<h1>${h}</h1>
      <p class="lead">Deterministic, no model: look up the row the answer is about, as the index holds it, and split it every way the company's number format allows.
      One reading → send. Two → hold, and answer from the model export. It judges the row, not the answer, so right answers on two-way rows are held too.</p>
      <div class="card"><table class="lint plain"><thead><tr><th></th><th>question</th><th>row</th><th>readings</th><th>PDF answers</th><th>.xlsx answers</th></tr></thead><tbody>
        ${pdf.map((r) => { const x = xl.find((y) => y.q === r.q); return `<tr class="${r.ways > 1 ? "warn" : ""}"><td><b>${r.ways > 1 ? "hold" : "send"}</b></td><td>${esc(r.q)} ${esc(r.company)}</td>
          <td class="mono small">${esc(r.label)}</td><td>${r.ways}</td><td>${r.answers.map((a) => mark(a.verdict)).join(" ")}</td><td>${x ? x.answers.map((a) => mark(a.verdict)).join(" ") : "–"}</td></tr>`; }).join("")}</tbody></table>
        <p class="small">held ${g.held} answers on rows that read two ways (${g.wrong_held} of them wrong) · sent ${g.sent} on rows that read one way (${g.wrong_sent} of them wrong)</p></div>`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {}, g = S.summary.guard || {};
    const ctl = (v) => { const c = S.control.find((x) => x.variant === v); if (!c) return ""; const two = c.answers.filter((a) => a.ways > 1), one = c.answers.filter((a) => a.ways === 1);
      return `<tr><td><b>${esc(c.label)}</b> <span class="muted small">no boundary</span></td><td>${two.filter((a) => a.verdict === "wrong").length}/${two.length} wrong</td><td>${one.filter((a) => a.verdict === "wrong").length}/${one.length} wrong</td><td class="muted small">table handed over directly</td></tr>`; };
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>rows that read two ways</th><th>rows that read one way</th><th>checker: row attached</th></tr></thead><tbody>
        ${Object.keys(ARMS).filter((k) => t[k]).map((k) => `<tr><td><b>${esc(ARMS[k])}</b></td><td>${t[k].two.wrong}/${t[k].two.answers} wrong</td><td>${t[k].one.wrong}/${t[k].one.answers} wrong</td><td>${t[k].checks.attached}/${t[k].checks.readable}</td></tr>`).join("")}
        ${ctl("csv")}${ctl("cells")}
      </tbody></table>
      ${g.held !== undefined ? `<p class="small">Number check: held ${g.held} (${g.wrong_held} wrong), sent ${g.sent} (${g.wrong_sent} wrong).</p>` : ""}</div>
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      What MemoryRouter attaches varies from call to call; the README reports totals over all runs.</p>`;
  }

  function rows() {
    const ready = !!S.world, cs = S.data?.companies || [];
    const last = S.lookups[S.lookups.length - 1];
    return `<h1>Rows</h1>
      <p class="lead">Any company's table as the index holds it, PDF and .xlsx, with the readings of every PDF row. Read-only: two searches, no model call.</p>
      <div class="card"><div class="row">${cs.map((c) => `<button class="chip ${S.pick === c.short ? "on" : ""}" data-pick="${esc(c.short)}">${esc(c.short)} · ${esc(c.country)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="rows" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        ${last ? `<h3>${esc(last.company)}</h3><table class="lint plain"><thead><tr><th></th><th>row, as indexed</th><th>printed</th><th>readings</th></tr></thead><tbody>
          ${last.rows.map((r) => { const n = (r.readings || []).length; return `<tr class="${n > 1 ? "warn" : ""}"><td>${n > 1 ? "⚠" : ""}</td><td class="mono small">${esc(r.line || r.label)}</td><td class="mono small">${r.printed.map(esc).join(" | ")}</td><td class="small">${n} · ${esc(ways(r))}</td></tr>`; }).join("")}</tbody></table>
          <h3>from the .xlsx</h3><pre class="mono small">${esc(last.xlsx || "(not returned)")}</pre>` : ""}</div>`;
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
    st.querySelector('[data-act="rows"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/rows", { company: S.pick });
      if (r) S.lookups.push(r);
      if (S.view === "rows") renderStage();
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
  function resetView() { S.memory = null; S.index = null; S.asked = []; S.tallies = {}; S.control = []; S.guard = null; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, both projects and the 12 documents?")) return;
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
    else if (k === "control") S.control.push(d);
    else if (k === "guard") S.guard = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, both projects and the 12 documents are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Rows: any company's table as the index holds it.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "index", "asked", "tally", "control", "guard", "summary", "router_unavailable", "rows", "done", "error"];
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

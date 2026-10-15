/* Stock sheet memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, two boundaries", view: "setup" },
    { n: 3, title: "What the team keeps", sub: "the Monday stock count", view: "memory" },
    { n: 4, title: "The count in the index", sub: "one table per sheet", view: "index" },
    { n: 5, title: "As kept", sub: "warehouse = sheet name", view: "tabs" },
    { n: 6, title: "Site column", sub: "warehouse on every row", view: "sited" },
    { n: 7, title: "Wrong numbers", sub: "whose count, which label", view: "prov" },
    { n: 8, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, prov: null, summary: null, unavailable: null,
    lookups: [], pick: "Bristol", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = { tabs: "the workbook as kept", sited: "Site column on every row" };
  const MARK = { other: ["✗", "bad"], mixed: ["±", "bad"], right: ["✓", "ok"], none: ["·", ""] };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "sheet" ? "active" : ""}" data-view="sheet"><span class="n">#</span><span class="t">Sheet<small>any warehouse, any time</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, tabs: () => arm("tabs"), sited: () => arm("sited"), prov, score, sheet }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const entry = (e) => e.rows === e.label ? `<span>${esc(e.rows)}'s rows as “${esc(e.label)}”</span>`
    : `<span class="bad">${esc(e.rows || "?")}'s rows labelled “${esc(e.label)}” ⚠</span>`;
  const armLine = (t) => t ? `${t.right}/${t.answers} right · ${t.other} another warehouse's count${t.mixed ? ` · ${t.mixed} gave both` : ""} · ${t.none} no figure` : "";
  const chkLine = (t) => t ? `checker: ${t.checks.mislabelled} of ${t.checks.entries} attached tables under another warehouse's label · asked warehouse's rows attached ${t.checks.asked_attached}/${t.checks.readable}${t.checks.declined ? ` (${t.checks.declined} declined to copy)` : ""}` : "";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.tabs)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-stk")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>One workbook, a sheet per warehouse. MemoryRouter puts one sheet name on every table it attaches from it.</h1>
      <p class="lead">Harrow &amp; Pike Distribution (fictional) counts stock in Leeds, Bristol, Glasgow and Cardiff every Monday — one .xlsx, one sheet per warehouse,
      the same columns on every sheet. The operations team keeps it in MemoryLake and asks a stock assistant that runs on MemoryRouter: every question is an ordinary model
      call with <code>--boundary</code> added. The index knows each table's sheet; the label on the attached text does not, and the rows never say which warehouse.
      This demo asks eight weekly questions through the workbook as kept and through the same numbers with a Site column, and shows whose count every wrong answer gave.
      It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 80 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-recurring-operations-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for recurring operations teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two projects, two boundaries</h1>
      <p class="lead">The stock count as the team keeps it goes in one project. The same numbers with a Site column on every row go in another. One boundary over each.
      The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>Count as kept</dt><dd class="mono small">${esc(w?.tabs || "pending")}</dd>
        <dt>Count with Site column</dt><dd class="mono small">${esc(w?.sited || "pending")}</dd>
        <dt>Boundary: as kept</dt><dd class="mono small"><b>${esc(w?.boundary?.tabs || "pending")}</b></dd>
        <dt>Boundary: Site column</dt><dd class="mono small"><b>${esc(w?.boundary?.sited || "pending")}</b></dd></dl></div>
        <div class="card"><h3 style="margin-top:0">This week's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What the team keeps") : empty("What the team keeps");
    const d = S.data || {};
    return `<h1>${esc(d.file)} — counted ${esc(d.counted)}</h1>
      <p class="lead">One sheet per warehouse, named after it; every sheet has the same columns and the same six items. The second workbook holds the same numbers with
      <b>Site</b> as the first column. Both go through <code>lib upload</code> + <code>proj doc import --wait</code>.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>SKU</th><th>Item</th>${(d.sites || []).map((s) => `<th>${esc(s.site)}</th>`).join("")}<th>Reorder point</th></tr></thead><tbody>
        ${(d.items || []).map((i) => `<tr><td class="mono">${esc(i.sku)}</td><td>${esc(i.item)}</td>${d.sites.map((s) => `<td>${fmt(s.on_hand[i.sku])}</td>`).join("")}<td class="muted">${fmt(i.reorder)}</td></tr>`).join("")}
      </tbody></table><p class="muted small">Every count is different, so a number in an answer names exactly one warehouse.</p></div>`;
  }

  function index() {
    const h = "The count in the index — one table per sheet";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const t = S.index.tables || [], doc = t[0]?.doc_sheet;
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns one table item per sheet, and each item carries its own <code>sheet_name</code>. The search hit for the file carries one
      more, file-level <code>sheet_name</code> — here <b>“${esc(doc)}”</b>. MemoryRouter attaches each table as an entry labelled <code>(file / sheet)</code> with one sheet name per file,
      often not the table's own (steps 5–6 show it); the rows never say which warehouse.</p>
      <div class="grid2">${t.map((x) => `<div class="card"><h3 style="margin-top:0">table item · sheet_name “${esc(x.sheet)}”</h3><pre class="mono small">${esc(x.rows.join("\n"))}</pre></div>`).join("")}</div>
      <div class="card hl"><h3 style="margin-top:0">How MemoryRouter labels them — for example</h3><pre class="mono small">${esc(S.index.sample)}</pre>
        ${S.index.sited?.length ? `<h3>The same sheet with a Site column</h3><pre class="mono small">${esc(S.index.sited[0].rows.slice(0, 3).join("\n"))}</pre>` : ""}</div>`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${ARMS[name]}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    return `<h1>${h}${t ? ` — ${t.right}/${t.answers} right` : ""}</h1>
      <p class="lead">Each question is answered ${S.data?.runs || 4}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what it was given, so you can see
      every attached table's label next to the warehouse its numbers belong to. ✓ the asked warehouse's count · ✗ another warehouse's count · · no figure.</p>
      ${rows.map((r) => {
        const pick = r.answers.find((a) => a.verdict === "other" || a.verdict === "mixed") || r.answers[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span> <span class="pill">count ${fmt(r.truth)}</span></h3>
          <div class="small">checker: ${r.entries?.length ? r.entries.map(entry).join(" · ") : `<code>${esc(r.attached || "—")}</code>`}</div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict === "other" || pick.verdict === "mixed" ? "wrong" : ""}"><span class="who">answer ${mark(pick.verdict)}${pick.from_sites?.length ? ` · ${esc(pick.from_sites.join(", "))}'s count` : ""}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}<br>${chkLine(t)}</div>` : ""}`;
  }

  function prov() {
    const h = "Where the wrong numbers came from";
    if (!S.prov) return running() ? waiting(h) : empty(h);
    const p = S.prov, tabs = S.asked.filter((r) => r.arm === "tabs"), Q = Object.fromEntries((S.data?.questions || []).map((q) => [q.id, q]));
    const rows = tabs.flatMap((r) => r.answers.filter((a) => a.verdict === "other" || a.verdict === "mixed").map((a) => ({ r, a })));
    const count = (r, site) => S.data?.sites.find((s) => s.site === site)?.on_hand[r.sku];
    return `<h1>${h} — ${p.wrong} answer${p.wrong === 1 ? "" : "s"} gave another warehouse's count</h1>
      <p class="lead">Every count in the data is different, so each wrong number names its warehouse. Next to it: how the checker saw that warehouse's table attached for the same
      question — a separate call, so a hint, not proof.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>question</th><th>asked</th><th>answered</th><th>whose count</th><th>checker saw</th></tr></thead><tbody>
        ${rows.map(({ r, a }) => { const src = a.from_sites[0], hit = (r.entries || []).filter((e) => e.rows === src);
          const seen = !r.entries?.length ? "—" : hit.length ? hit.map((e) => `${esc(src)}'s rows labelled “${esc(e.label)}”`).join(", ") : `not ${esc(src)}'s rows`;
          return `<tr class="warn"><td>${esc(r.q)} <span class="small muted">${esc(Q[r.q]?.sku)}</span></td><td>${esc(r.site)}</td><td>${fmt(count(r, src))}</td><td><b>${esc(src)}</b></td><td class="small">${seen}</td></tr>`; }).join("")}
      </tbody></table>
      <p class="small">${(p.pairs || []).map((x) => `${esc(x.asked)} → ${esc(x.answered)}: ${x.n}`).join(" &nbsp; ")}</p></div>`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>right</th><th>another warehouse's count</th><th>no figure</th><th>checker: tables under another label</th></tr></thead><tbody>
        ${Object.keys(ARMS).filter((k) => t[k]).map((k) => `<tr><td><b>${esc(ARMS[k])}</b></td><td>${t[k].right}/${t[k].answers}</td><td>${t[k].other + t[k].mixed}</td><td>${t[k].none}</td><td>${t[k].checks.mislabelled}/${t[k].checks.entries}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      What MemoryRouter attaches varies from call to call; the README reports totals over all runs.</p>`;
  }

  function sheet() {
    const ready = !!S.world, ss = S.data?.sites || [];
    const last = S.lookups[S.lookups.length - 1];
    return `<h1>Sheet</h1>
      <p class="lead">Any warehouse's sheet as the index holds it — in the workbook as kept and with the Site column. Read-only: two searches, no model call.</p>
      <div class="card"><div class="row">${ss.map((s) => `<button class="chip ${S.pick === s.site ? "on" : ""}" data-pick="${esc(s.site)}">${esc(s.site)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="sheet" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        ${last ? `<h3>${esc(last.site)} — as kept</h3><pre class="mono small">${esc(last.tabs.join("\n") || "(not returned)")}</pre>
          <h3>${esc(last.site)} — with the Site column</h3><pre class="mono small">${esc(last.sited.join("\n") || "(not returned)")}</pre>` : ""}</div>`;
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
    st.querySelector('[data-act="sheet"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/sheet", { site: S.pick });
      if (r) S.lookups.push(r);
      if (S.view === "sheet") renderStage();
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
  function resetView() { S.memory = null; S.index = null; S.asked = []; S.tallies = {}; S.prov = null; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, both projects and the 2 workbooks?")) return;
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
    else if (k === "provenance") S.prov = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, both projects and the 2 workbooks are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Sheet: any warehouse's table as the index holds it.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "index", "asked", "tally", "provenance", "summary", "router_unavailable", "sheet", "done", "error"];
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

/* Comps note memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, three boundaries", view: "setup" },
    { n: 3, title: "The comps workbook", sub: "notes, a hidden row, a hidden sheet", view: "memory" },
    { n: 4, title: "The workbook in the index", sub: "notes out, hidden parts in", view: "index" },
    { n: 5, title: "What the model is handed", sub: "the checker's copy", view: "attached" },
    { n: 6, title: "As saved", sub: "the workbook as kept", view: "saved" },
    { n: 7, title: "Noted", sub: "notes as a column", view: "noted" },
    { n: 8, title: "Usable", sub: "only the comps to use", view: "usable" },
    { n: 9, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, attached: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "5 Mill Pond Road", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["saved", "noted", "usable"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { used: ["✗", "bad"], flagged: ["◐", "bad"], other: ["·", ""], right: ["✓", "ok"], empty: ["∅", ""] };
  const ORDER = ["used", "flagged", "other", "empty", "right"];

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "lookup" ? "active" : ""}" data-view="lookup"><span class="n">#</span><span class="t">Comp<small>one address, three projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, attached, saved: () => arm("saved"), noted: () => arm("noted"), usable: () => arm("usable"), score, lookup: lookupTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const graded = (g) => g.answers - g.empty;
  const armLine = (t) => t ? `ruled-out rows: ${t["ruled-out"].right}/${graded(t["ruled-out"])} right · ${t["ruled-out"].used} used as a comp · ${t["ruled-out"].flagged} with a caveat${t["ruled-out"].other ? ` · ${t["ruled-out"].other} other` : ""}${t["ruled-out"].empty ? ` · ${t["ruled-out"].empty} ∅` : ""} · controls ${t.control.right}/${graded(t.control)}` : "";
  const money = (v) => typeof v === "number" && v > 10000 ? fmt(v) : esc(v);

  // The Comps sheet as Excel shows it: a red triangle on noted cells, the hidden row greyed out, the hidden sheet tab.
  function sheetCard(w) {
    const cols = w.columns || [];
    return `<div class="xl"><table class="lint plain"><thead><tr><th></th>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
      ${w.rows.map((r) => `<tr class="${r.hidden ? "hid" : ""}"><td class="rn">${r.row}</td>${r.cells.map((c, i) => `<td class="${i === 2 && r.note ? "noted" : ""}">${money(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>
      <div class="tabs">${(w.sheets || []).map(([n, h]) => `<span class="tab ${h ? "hidden" : "on"}">${esc(n)}${h ? " (hidden)" : ""}</span>`).join("")}</div></div>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.saved)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-cnm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The senior agent's notes don't reach the index. The rows she hid do.</h1>
      <p class="lead">Harrow &amp; Vale Realty (fictional) keeps its comparable sales in an Excel workbook that the senior agent, Priya Nair, maintains, and puts it in shared memory so a newer
      agent's listing assistant can answer “what's the lowest comp we can use for 12 Fenwick Street?”. Priya's judgement lives where Excel users put it: the reason a sale is out is a
      <b>cell note</b> on its price, a sale that fell through is a <b>hidden row</b>, contracts that have not closed sit on a <b>hidden sheet</b>. The assistant runs on MemoryRouter:
      every question is an ordinary model call with <code>--boundary</code> added. This demo stores the workbook three ways — as saved, with the notes written into a column, with only the
      usable comps — shows each in the index, copies out what MemoryRouter hands the model, and asks eight questions through each boundary. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 86 calls per run (gpt-4o-mini, plus 6 checker calls to claude-haiku-4-5).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/local-market-memory-for-real-estate-agents" target="_blank" rel="noopener">memorylake.ai › Local market memory for real estate agents</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, three boundaries</h1>
      <p class="lead">The same workbook three times: as Priya keeps it; with every cell note written into an “${esc(S.data?.note_column)}” column and the hidden row and sheet left out
      (<code>note_workbook()</code>); and with only the comps she would use (<code>usable_workbook()</code>). One project each, one boundary over each.
      The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armShort(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The agent's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}${q.control ? ' <span class="pill">control</span>' : ""}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The comps workbook") : empty("The comps workbook");
    const w = S.data?.workbook; if (!w) return empty("The comps workbook");
    const hid = (S.data.comps || []).find((c) => c.hidden);
    return `<h1>The comps workbook — what the senior agent sees in Excel</h1>
      <p class="lead">${w.rows.length} closed sales on sheet “${esc(w.sheet)}”. Three carry a cell note on the price (the red triangle; hover to read), one row is hidden, and the
      “${esc(S.data.pending_sheet)}” sheet is hidden. All of it read from the .xlsx itself (<code>read_workbook()</code>, standard library).</p>
      <div class="card"><div class="row spread"><h3 style="margin:0">${esc(w.file)}</h3><span class="pill err">${w.notes.length} notes · 1 hidden row · 1 hidden sheet</span></div>
        <div style="margin-top:10px">${sheetCard(w)}</div></div>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">Cell notes</h3>${w.notes.map((n) => `<p class="small"><span class="mono">${esc(n.ref)}</span> “${esc(n.text)}”</p>`).join("")}</div>
        <div class="card"><h3 style="margin-top:0">Hidden</h3>
          ${hid ? `<p class="small"><b>row:</b> ${esc(hid.address)} — ${esc(hid.why)}</p>` : ""}
          ${(w.hidden_sheets || []).map((s) => `<p class="small"><b>sheet “${esc(s.name)}”:</b> ${s.rows.map((r) => `${esc(r[0])} — under contract ${esc(r[1])} at ${fmt(r[2])}`).join("; ")}</p>`).join("")}</div></div>
      <div class="card small">Before upload: <b>noted</b> = ${(S.data.arms.noted.rows || []).length} rows with an “${esc(S.data.note_column)}” column · <b>usable</b> = ${(S.data.arms.usable.rows || []).length} rows: ${esc((S.data.arms.usable.rows || []).join(", "))}.</div>`;
  }

  function index() {
    const h = "The workbook in the index — notes out, hidden parts in";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const by = Object.fromEntries((S.index.arms || []).map((v) => [v.arm, v]));
    const s = by.saved || {};
    const tag = (r) => (s.hidden_row || []).some((a) => r.includes(a)) ? '<span class="pill err">hidden row</span> ' : (s.pending || []).some((a) => r.includes(a)) ? '<span class="pill err">hidden sheet</span> ' : "";
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns each sheet as a table item, one line per row; this is the text MemoryRouter attaches. In the as-saved copy:
      words from the ${(S.data?.workbook?.notes || []).length} cell notes: <b>${s.note_words ?? "–"}</b> · the hidden row: <b>${(s.hidden_row || []).length ? "in" : "out"}</b> ·
      the hidden sheet: <b>${(s.pending || []).length ? "in" : "out"}</b>.</p>
      <div class="card warnc"><h3 style="margin-top:0">as saved</h3>${(s.tables || []).map((t) => `<p class="small" style="margin:10px 0 4px"><b>table item</b> · sheet_name “${esc(t.sheet)}” · ${t.rows.length} rows</p>
        ${t.rows.map((r) => `<div class="idx ${tag(r) ? "bad" : ""}">${tag(r)}${esc(r)}</div>`).join("")}`).join("")}
        ${s.summary ? `<p class="small" style="margin-top:12px"><b>The index's own summary:</b> “${esc(s.summary)}”</p>` : ""}</div>
      ${["noted", "usable"].map((a) => { const v = by[a] || {}; const sample = (v.tables || []).flatMap((t) => t.rows).find((r) => r.includes(S.data?.note_column || "Agent note") && r.includes("Quarry"));
        return `<div class="card"><h3 style="margin-top:0">${esc(armShort(a))}</h3><p class="small">${v.rows ?? "–"} rows · hidden row ${(v.hidden_row || []).length} · hidden sheet ${(v.pending || []).length} · rows with a note ${(v.noted || []).length}</p>
          ${sample ? `<div class="idx">${esc(sample)}</div>` : ""}</div>`; }).join("")}`;
  }

  function attached() {
    const h = "What MemoryRouter hands the model — as saved";
    if (!S.attached) return unavailable() || (running() ? waiting(h) : empty(h));
    const qs = Object.fromEntries((S.data?.questions || []).map((q) => [q.id, q]));
    return `<h1>${h}</h1>
      <p class="lead">For each question about a ruled-out row, ${esc(S.data?.check_model)} is asked through the as-saved boundary to copy out everything above the question.
      It is its own call, so what it is handed can differ from the answer calls. Labels as MemoryRouter writes them: <code>(file / sheet)</code>.
      ${(S.attached.pending_labels || []).length ? `The hidden sheet's contracts arrived labelled ${S.attached.pending_labels.map((x) => `“${esc(x)}”`).join(", ")}.` : ""}</p>
      ${(S.attached.checks || []).map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.q)} <span class="small muted">${esc(qs[c.q]?.say)}</span></h3>
        ${c.readable ? (c.entries.length ? c.entries.map((e) => `<div class="idx ${e.pending.length || e.hidden_row.length ? "bad" : ""}"><span class="tag">${e.file ? `${esc(e.file)}${e.label ? " / " + esc(e.label) : ""}` : "no file label"}</span>
          ${e.rows} rows${e.noted.length ? ` · ${e.noted.length} noted in Excel` : ""}${e.hidden_row.length ? ` · <b>hidden row</b> (${esc(e.hidden_row.join(", "))})` : ""}${e.pending.length ? ` · <b>hidden-sheet contracts</b> (${esc(e.pending.join(", "))})` : ""} · note words <b>${e.note_words}</b></div>`).join("") : '<p class="muted small">no workbook entry</p>')
          : `<p class="muted small">${c.declined ? "declined to copy (wrote NONE)" : "unreadable"}</p>`}</div>`).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    return `<h1>${h}${t ? ` — ${t["ruled-out"].right}/${graded(t["ruled-out"])} right on ruled-out rows` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ✗ a ruled-out row used as a comp · ◐ given with a caveat · · other · ∅ nothing attached (the model on its own; not counted).</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict))[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">${esc(r.truth)}</div>
          <div class="row small" style="margin-top:4px"><span class="pill ${r.group === "control" ? "" : "err"}">${r.group === "control" ? "control: no note, not hidden" : "about a ruled-out row"}</span></div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick.verdict)}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {}; const by = Object.fromEntries((S.summary.index || []).map((v) => [v.arm, v]));
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>index: note words · hidden row · hidden sheet</th><th>ruled-out rows: right</th><th>used as a comp</th><th>with a caveat</th><th>other</th><th>∅</th><th>controls</th></tr></thead><tbody>
        ${ARMS.map((k) => { const v = by[k] || {}; const y = t[k]?.["ruled-out"];
          return `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td><td>${v.note_words ?? "–"} · ${(v.hidden_row || []).length} · ${(v.pending || []).length}</td>${y ? `<td>${y.right}/${graded(y)}</td><td>${y.used}</td><td>${y.flagged}</td><td>${y.other}</td><td>${y.empty}</td><td>${t[k].control.right}/${graded(t[k].control)}</td>` : "<td>–</td><td></td><td></td><td></td><td></td><td></td>"}</tr>`; }).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">∅ answers (MemoryRouter attached nothing) are not counted either way — but they are what the agent would read: with the row gone, the model answers on its own.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant handed the agent instead</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function lookupTool() {
    const ready = !!S.world;
    const all = [...(S.data?.comps || []).map((c) => c.address), ...(S.data?.pending || []).map((p) => p.address)];
    const last = S.lookups[S.lookups.length - 1];
    const how = (o) => o.comp ? (o.comp.hidden ? `hidden row — ${esc(o.comp.why)}` : o.comp.note ? `cell note: “${esc(o.comp.note)}”` : "no note, not hidden")
      : `hidden sheet “${esc(S.data?.pending_sheet)}”, under contract ${esc(o.pending?.contract)} at ${fmt(o.pending?.price)}`;
    return `<h1>Comp</h1>
      <p class="lead">One address: what the workbook holds for it, and its row in the index text of all three projects. Read-only: searches, no model call.</p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${all.map((a) => `<button class="chip ${S.pick === a ? "on" : ""}" data-pick="${esc(a)}">${esc(a)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="lookup" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last ? `<div class="card warnc"><h3 style="margin-top:0">${esc(last.address)}</h3><p class="small">In the workbook: ${how(last)}</p>
        ${(last.views || []).map((v) => `<div class="idx ${v.row && (last.pending || last.comp?.hidden || last.comp?.note) && v.arm === "saved" ? "bad" : ""}"><span class="tag">${esc(armShort(v.arm))}</span>${v.row ? `sheet_name “${esc(v.sheet)}”: ${esc(v.row)}` : "not in the index text"}</div>`).join("")}</div>` : ""}`;
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
      const r = await api("/api/comp", { address: S.pick });
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
  function resetView() { S.memory = null; S.index = null; S.attached = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, the three projects and the 3 files?")) return;
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
    else if (k === "attached") S.attached = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, the three projects and the 3 files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Comp: one address in the workbook and in the three projects' index.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "index", "attached", "asked", "tally", "summary", "router_unavailable", "lookup", "done", "error"];
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

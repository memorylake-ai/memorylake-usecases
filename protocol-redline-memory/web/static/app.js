/* Protocol redline memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, two boundaries", view: "setup" },
    { n: 3, title: "The study library", sub: "two amendments marked by hand", view: "memory" },
    { n: 4, title: "Edits in the index", sub: "which value is left?", view: "index" },
    { n: 5, title: "As saved", sub: "struck-through text and all", view: "saved" },
    { n: 6, title: "Strike removed", sub: "apply_redline() before upload", view: "applied" },
    { n: 7, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "klv", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["saved", "applied"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { stale: ["✗", "bad"], mixed: ["◐", "bad"], other: ["·", ""], right: ["✓", "ok"], empty: ["∅", ""] };
  const KEPT = { both: ["both values", "warn"], "old only": ["deleted value only", "err"], "new only": ["new value only", "ok"], neither: ["neither", "err"], "line missing": ["line not found", ""] };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "lookup" ? "active" : ""}" data-view="lookup"><span class="n">#</span><span class="t">Document<small>one file, both projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, saved: () => arm("saved"), applied: () => arm("applied"), score, lookup: lookupTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const graded = (g) => g.answers - g.empty;
  const armLine = (t) => t ? `edited lines: ${t.edited.right}/${graded(t.edited)} right · ${t.edited.stale} gave the deleted value · ${t.edited.mixed} gave both${t.edited.empty ? ` · ${t.edited.empty} ∅` : ""} · controls ${t.control.right}/${graded(t.control)}` : "";
  const kept = (k) => { const m = KEPT[k] || [k, ""]; return `<span class="pill ${m[1]}">${esc(m[0])}</span>`; };

  // A document as Word shows it: the old words struck through, the new ones bold and underlined.
  function docCard(x) {
    const part = (p) => typeof p === "string" ? esc(p) : p[0] === "del" ? `<s class="del">${esc(p[1])}</s>` : `<span class="ins">${esc(p[1])}</span>`;
    return `<div class="doc"><p class="t">${esc(x.title)}</p><p class="small muted">${esc(S.data?.sponsor)} · ${esc(x.study)} · ${esc(x.saved)}</p>
      ${x.lines.map((l) => `<div class="cl"><p>${l.label === "Legend" ? `<i>${l.parts.map(part).join("")}</i>` : `<b>${esc(l.label)}:</b> ${l.parts.map(part).join("")}`}</p></div>`).join("")}</div>`;
  }
  function docIndexCard(x, views) {
    const by = Object.fromEntries(views.filter((v) => v.doc === x.id).map((v) => [v.arm, v]));
    if (!(x.marks || []).length) return `<div class="card"><h3 style="margin-top:0">${esc(x.title)}</h3><p class="small muted">No markup: the index holds the same text in both projects.</p></div>`;
    const s = by.saved;
    return `<div class="card warnc"><h3 style="margin-top:0">${esc(x.title)}</h3>
      ${x.marks.map((m, k) => `<p class="small" style="margin:12px 0 2px"><b>${esc(m.label)}</b> <span class="muted">struck</span> <s class="del">${esc(m.old)}</s> <span class="muted">→ new</span> <span class="ins">${esc(m.new)}</span></p>
        ${ARMS.map((a) => { const y = by[a]?.edits?.[k];
          return `<div class="idx ${y && (y.kept === "old only" || y.kept === "both") ? "bad" : y && y.kept === "new only" ? "ok" : ""}"><span class="tag">${esc(armShort(a))}</span>${y ? kept(y.kept) + " " + esc(y.line || "(line not found)") : "(not returned by search yet)"}</div>`; }).join("")}`).join("")}
      ${s?.found && s.summary ? `<p class="small" style="margin-top:12px"><b>The index's own summary</b> of the as-saved file: “${esc(s.summary)}”</p>` : ""}</div>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.saved)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-prl")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>An amendment marked up by hand. The strikethrough does not reach the index.</h1>
      <p class="lead">Northfield Clinical Research Unit (fictional) keeps its study documents in shared memory, so a study assistant can answer “what's the minimum eGFR at screening?”
      while a coordinator pre-screens a patient. The sponsor sends each protocol amendment as a Word file marked up <b>by hand</b>: deleted text <s class="del">struck through</s>,
      new text <span class="ins">bold and underlined</span> — not Word's Track Changes. The assistant runs on MemoryRouter: every question is an ordinary model call with
      <code>--boundary</code> added. This demo stores the documents as saved, and the same documents with the struck-through text removed, shows each edited line in the index,
      and asks nine questions through each boundary. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 63 calls per run (gpt-4o-mini).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-healthcare-researchers" target="_blank" rel="noopener">memorylake.ai › AI memory for healthcare researchers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two projects, two boundaries</h1>
      <p class="lead">The same three documents twice: as the sponsor sent them, and with the struck-through words removed before upload (<code>apply_redline()</code>). One project each,
      one boundary over each. The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armShort(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The coordinator's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The study library") : empty("The study library");
    const xs = S.data?.docs || [];
    const n = xs.reduce((k, x) => k + x.marks.length, 0);
    return `<h1>The study library — three Word files</h1>
      <p class="lead">Two protocol amendments carry ${n} edits marked by hand: the old words in a struck-through run (<code>&lt;w:strike/&gt;</code>), the new ones in a bold, underlined run.
      There is no <code>&lt;w:del&gt;</code> or <code>&lt;w:ins&gt;</code> in either file${S.memory ? ` (Track Changes revisions: <b>${S.memory.tracked}</b>)` : ""}: accepting tracked changes — the fix for Word's own revision marks — leaves every line as it is.
      Before upload, the demo's <code>apply_redline()</code> makes the second copy: struck-through runs removed, the new words kept.</p>
      ${xs.map((x) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(x.file)}.docx</h3>
        <span class="pill ${x.marks.length ? "err" : "ok"}">${x.marks.length ? `${x.marks.length} edits marked by hand` : "no markup"}</span></div>
        <div style="margin-top:10px">${docCard(x)}</div></div>`).join("")}`;
  }

  function index() {
    const h = "The edits in the index — which value is left?";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const per = S.index.per || {};
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns the text the index holds for each file; this is the text MemoryRouter attaches. The words come through without their
      font: nothing marks the struck-through value as deleted. ${ARMS.map((a) => `<b>${esc(armShort(a))}</b>: both values ${per[a]?.both ?? "–"} · deleted value only ${per[a]?.["old only"] ?? "–"} · new value only ${per[a]?.["new only"] ?? "–"}`).join(" · ")}.</p>
      ${(S.data?.docs || []).map((x) => docIndexCard(x, S.index.docs || [])).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const order = { stale: 0, mixed: 1, other: 2, empty: 3, right: 4 };
    return `<h1>${h}${t ? ` — ${t.edited.right}/${graded(t.edited)} right on edited lines` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ the amended value · ✗ the deleted value · ◐ both · · neither · ∅ nothing attached.</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => order[a.verdict] - order[b.verdict])[0];
        const tag = r.group === "edited" ? `<span class="pill err">an edited line</span>` : `<span class="pill">control: no markup on this line</span>`;
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small" style="white-space:pre-line">${esc(r.truth)}</div>
          <div class="row small" style="margin-top:4px">${tag}</div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick.verdict)}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {}, per = S.summary.index || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>index: edited lines with both values</th><th>deleted value only</th><th>edited lines: right</th><th>gave the deleted value</th><th>gave both</th><th>controls</th></tr></thead><tbody>
        ${ARMS.map((k) => `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td><td>${per[k]?.both ?? "–"}</td><td>${per[k]?.["old only"] ?? "–"}</td>${t[k] ? `<td>${t[k].edited.right}/${graded(t[k].edited)}</td><td>${t[k].edited.stale}</td><td>${t[k].edited.mixed}</td><td>${t[k].control.right}/${graded(t[k].control)}</td>` : "<td>–</td><td></td><td></td><td></td>"}</tr>`).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">∅ answers (MemoryRouter attached nothing) are not counted either way. The two control questions are about lines without markup.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant said instead of the amended value</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function lookupTool() {
    const ready = !!S.world, xs = S.data?.docs || [];
    const last = S.lookups[S.lookups.length - 1];
    const x = last && xs.find((y) => y.id === last.doc);
    return `<h1>Document</h1>
      <p class="lead">One study document: its hand-marked edits, and each edited line in the index text of both projects. Read-only: searches, no model call.</p>
      <div class="card"><div class="row">${xs.map((y) => `<button class="chip ${S.pick === y.id ? "on" : ""}" data-pick="${esc(y.id)}">${esc(y.title.split(",")[0])}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="lookup" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last && x ? docIndexCard(x, last.views || []) : ""}`;
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
      const r = await api("/api/doc", { doc: S.pick });
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
      else if (S.caughtUp) banner("Done. Try Document: one file's hand-marked edits and their lines in both projects' index.", true);
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

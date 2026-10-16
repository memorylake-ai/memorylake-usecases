/* Footnote memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, two boundaries", view: "setup" },
    { n: 3, title: "The platform docs", sub: "two Word files, eight footnotes", view: "memory" },
    { n: 4, title: "Footnotes in the index", sub: "next to which sentence?", view: "index" },
    { n: 5, title: "As saved", sub: "footnotes at the foot of the page", view: "saved" },
    { n: 6, title: "Footnotes inlined", sub: "written in after their sentence", view: "inlined" },
    { n: 7, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "limits", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["saved", "inlined"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { body: ["✗", "bad"], mixed: ["◐", "bad"], right: ["✓", "ok"], none: ["·", ""] };
  const SUP = (n) => String(n).replace(/\d/g, (c) => "⁰¹²³⁴⁵⁶⁷⁸⁹"[c]);
  const docOf = (id) => (S.data?.docs || []).find((x) => x.id === id);
  const secName = (x, n) => n ? `§${n} ${x?.sections?.[n - 1]?.title || "?"}` : "no section";

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "lookup" ? "active" : ""}" data-view="lookup"><span class="n">#</span><span class="t">Document<small>one document, both projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, saved: () => arm("saved"), inlined: () => arm("inlined"), score, lookup: lookupTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const armLine = (t) => t ? `footnote questions: ${t.footnote.right}/${t.footnote.answers} right · ${t.footnote.body} gave the section's own value · ${t.footnote.mixed + t.footnote.none} other · sections without a footnote ${t.control.right}/${t.control.answers}${t.footnote.empty + t.control.empty ? ` · ${t.footnote.empty + t.control.empty} call(s) had nothing attached (not counted)` : ""}` : "";
  const chkLine = (t) => t ? `checker on footnote questions: the sentence attached ${t.checks.rule}/${t.checks.need} · without its footnote ${t.checks.rule_only} · footnote at the foot of the page ${t.checks.foot - t.checks.inline} · footnote right after its sentence ${t.checks.inline}${t.checks.declined ? ` · ${t.checks.declined} declined to copy` : ""}` : "";

  // A document as Word shows it: sections on the page, a superscript on each qualified sentence, the footnotes at the foot of the page.
  function docCard(x) {
    const by = Object.fromEntries((x.footnotes || []).map((f) => [f.on, f]));
    const body = x.sections.map((c) => {
      const f = by[c.n];
      let text = esc(c.text);
      if (f && f.sentence && c.text.includes(f.sentence)) text = text.replace(esc(f.sentence), `<span class="has">${esc(f.sentence)}<sup>${SUP(f.n)}</sup></span>`);
      return `<p class="sec">${c.n}. ${esc(c.title)}</p><p>${text}</p>`;
    }).join("");
    return `<div class="doc"><p class="t">${esc(x.title)}</p>${body}
      <div class="notes">${(x.footnotes || []).map((f) => `<p><sup>${SUP(f.n)}</sup> ${esc(f.text)}</p>`).join("")}</div></div>`;
  }
  const where = (x, y) => {
    if (!y || !y.held) return "(the footnote's words are not in the index text search returned)";
    if (y.together) return y.how === "inline" ? "in the same chunk as its sentence, right after it" : "in the same chunk as its sentence, at the foot of the page";
    return `in another chunk — at the foot of the page${y.after ? `, after the text of ${secName(x, y.after)}` : ""}`;
  };
  const placeTag = (y) => !y || !y.held ? ["–", ""] : y.together ? ["✓", "ok"] : ["✗", "bad"];
  function docIndexCard(x, views) {
    const by = Object.fromEntries(views.filter((v) => v.doc === x.id).map((v) => [v.arm, v]));
    const firstApart = by.saved?.footnotes?.find((y) => y.held && !y.together);
    return `<div class="card warnc"><h3 style="margin-top:0">${esc(x.file)}.docx <span class="small muted">${ARMS.map((a) => `${esc(armShort(a))}: ${by[a]?.chunks ?? "–"} chunks`).join(" · ")}</span></h3>
      ${(x.footnotes || []).map((f, k) => `<p class="small" style="margin:12px 0 2px"><b>${SUP(f.n)}</b> on <b>${esc(secName(x, f.on))}</b> <span class="muted">— “${esc(f.text)}”</span></p>
        ${ARMS.map((a) => { const y = by[a]?.footnotes?.[k]; const t = placeTag(y);
          return `<div class="idx ${t[1]}"><span class="tag">${t[0]} ${esc(armShort(a))}</span>${esc(where(x, y))}${y && y.held && !y.together ? ` <span class="bad-ink">← apart from its sentence, which keeps only “$ ^{${f.n}} $”</span>` : ""}</div>`; }).join("")}`).join("")}
      ${firstApart ? `<p class="small" style="margin-top:12px"><b>The footnote's chunk reads</b> <code>${esc(firstApart.line)}</code> — no section, no subject.</p>` : ""}</div>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.saved)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 6 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-ftn")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The footnotes reach the index. Which sentence each one qualifies does not.</h1>
      <p class="lead">Kestrel (fictional) keeps its platform team's Word documents — the service limits and the release policy — in shared memory, so a platform assistant can answer an
      engineer's “what's the export rate limit for our EU-West key?”. The exceptions are Word <b>footnotes</b>, written the way people write them: “¹ 200 for keys on the EU-West cluster”.
      The footnote doesn't say what it is 200 <i>of</i>; on the page the superscript points at the sentence. The index keeps a bare <code>$ ^{1} $</code> on the sentence and puts the
      footnote's words at the foot of the page — in another chunk. The assistant runs on MemoryRouter: every question is an ordinary model call with <code>--boundary</code> added. This demo
      stores the documents as saved, and the same documents with each footnote written in after its sentence, shows where each footnote sits in the index, and asks ten questions
      through each boundary. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 80 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-software-engineers" target="_blank" rel="noopener">memorylake.ai › AI memory for software engineers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two projects, two boundaries</h1>
      <p class="lead">The same two documents twice: as saved, and with each footnote written in right after the sentence it qualifies. One project each, one boundary over each.
      The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armShort(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The engineers' questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    // The files are local: show them as soon as step 3 starts, not only once the import has finished.
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The platform docs") : empty("The platform docs");
    const xs = S.data?.docs || [];
    return `<h1>The platform docs — two Word files, eight footnotes</h1>
      <p class="lead">In Word each footnote sits under a superscript on the sentence it qualifies, so the footnote never repeats what it is about. Before upload, the demo's
      <code>inline_footnotes()</code> makes the second copy: each footnote written in right after its sentence, naming its section — “[Footnote 1 to section 2 (Batch export rate): 200 for keys
      on the EU-West cluster, …]”. The documents are otherwise unchanged.</p>
      ${xs.map((x) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(x.file)}.docx</h3>
        <span class="pill err">${x.footnotes.length} footnotes</span></div>
        <div style="margin-top:10px">${docCard(x)}</div>
        <p class="small muted" style="margin-bottom:0">footnotes inlined copy: ${x.inline.map((l) => `<code>${esc(l)}</code>`).join(" ")}</p></div>`).join("")}`;
  }

  function index() {
    const h = "The footnotes in the index — next to which sentence?";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const per = S.index.per || {};
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns the chunks the index holds for each document; MemoryRouter attaches chunks. Footnotes in the same chunk as their
      sentence: ${ARMS.map((a) => `<b>${esc(armShort(a))}</b> ${per[a]?.together ?? "–"}/${per[a]?.footnotes ?? "–"}, in another chunk ${per[a]?.apart ?? "–"}`).join(" · ")}.</p>
      ${(S.data?.docs || []).map((x) => docIndexCard(x, S.index.docs || [])).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const order = { body: 0, mixed: 1, none: 2, right: 3 };
    const right = t ? t.footnote.right : 0, all = t ? t.footnote.answers : 0;
    return `<h1>${h}${t ? ` — footnote questions ${right}/${all} right` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what it
      was given. ✓ the footnote's value (or, without a footnote, the section's) · ✗ the section's own value · ◐ both · · neither · ∅ nothing was attached to that call.</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => (a.empty - b.empty) || (order[a.verdict] - order[b.verdict]))[0];
        const tag = r.qkind === "footnote" ? `<span class="pill err">answered by a footnote</span>` : `<span class="pill">no footnote</span>`;
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">the document: <b>${esc(r.truth)}</b></div>
          <div class="row small" style="margin-top:4px">${tag}</div>
          <div class="small">checker: <code>${esc(r.attached || "—")}</code></div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map((a) => (a.empty ? '<span class="mark">∅</span>' : "") + mark(a.verdict)).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick.verdict)}${pick.empty ? " (nothing attached)" : ""}</span>${esc(pick.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}<br>${chkLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {}, per = S.summary.index || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>index: footnote apart from its sentence</th><th>footnote Qs: right</th><th>gave the section's value</th><th>no footnote: right</th><th>∅ nothing attached</th><th>checker: sentence, no footnote</th></tr></thead><tbody>
        ${ARMS.map((k) => `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td><td>${per[k] ? `${per[k].apart}/${per[k].footnotes}` : "–"}</td>${t[k] ? `<td>${t[k].footnote.right}/${t[k].footnote.answers}</td><td>${t[k].footnote.body}</td><td>${t[k].control.right}/${t[k].control.answers}</td><td>${t[k].footnote.empty + t[k].control.empty}</td><td>${t[k].checks.rule_only}/${t[k].checks.rule}</td>` : "<td>–</td><td></td><td></td><td></td><td></td>"}</tr>`).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">Answers from calls that had nothing attached (the prompt was only the system prompt and the question) are counted apart, in both boundaries.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant said instead of the footnote's value</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function lookupTool() {
    const ready = !!S.world, xs = S.data?.docs || [];
    const last = S.lookups[S.lookups.length - 1];
    const x = last && xs.find((y) => y.id === last.doc);
    return `<h1>Document</h1>
      <p class="lead">One platform document: its footnotes, and where each one sits in the index of both projects. Read-only: searches, no model call.</p>
      <div class="card"><div class="row">${xs.map((y) => `<button class="chip ${S.pick === y.id ? "on" : ""}" data-pick="${esc(y.id)}">${esc(y.file)}</button>`).join(" ")}</div>
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
    if (!confirm("Delete the demo's boundaries, the two projects and the 4 files?")) return;
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
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, the two projects and the 4 files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Document: one platform document's footnotes and where each sits in both projects' index.", true);
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

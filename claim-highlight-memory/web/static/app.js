/* Claim highlight memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, three boundaries", view: "setup" },
    { n: 3, title: "The fact sheets", sub: "yellow = not cleared", view: "memory" },
    { n: 4, title: "Highlights in the index", sub: "what is left of the colour?", view: "index" },
    { n: 5, title: "As saved", sub: "highlights and all", view: "saved" },
    { n: 6, title: "Tagged", sub: "status written into the text", view: "tagged" },
    { n: 7, title: "Withheld", sub: "unverified numbers taken out", view: "withheld" },
    { n: 8, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, index: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "lumen", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["saved", "tagged", "withheld"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { published: ["✗", "bad"], flagged: ["◐", "bad"], over: ["⊘", "bad"], short: ["○", ""], other: ["·", ""], right: ["✓", "ok"], empty: ["∅", ""] };
  const STATE = { plain: ["reads as plain text", "err"], tagged: ["tagged in the text", "warn"], withheld: ["claim taken out", "ok"], missing: ["not in the line", ""], "line missing": ["line not found", ""] };
  const ORDER = ["published", "flagged", "over", "short", "other", "empty", "right"];

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "lookup" ? "active" : ""}" data-view="lookup"><span class="n">#</span><span class="t">Fact sheet<small>one file, three projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, index, saved: () => arm("saved"), tagged: () => arm("tagged"), withheld: () => arm("withheld"), score, lookup: lookupTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const graded = (g) => g.answers - g.empty;
  const armLine = (t) => t ? `yellow claims: ${t.yellow.right}/${graded(t.yellow)} right · ${t.yellow.published} published as fact · ${t.yellow.flagged} quoted with a caveat${t.yellow.short ? ` · ${t.yellow.short} held back the verified figure too` : ""}${t.yellow.other ? ` · ${t.yellow.other} other` : ""}${t.yellow.empty ? ` · ${t.yellow.empty} ∅` : ""} · controls ${t.control.right}/${graded(t.control)}${t.control.over ? ` (⊘ ${t.control.over})` : ""}` : "";
  const stateTag = (k) => { const m = STATE[k] || [k, ""]; return `<span class="pill ${m[1]}">${esc(m[0])}</span>`; };

  // A fact sheet as Word shows it: yellow and green highlighter on the claims.
  function docCard(x) {
    const part = (p) => typeof p === "string" ? esc(p) : `<mark class="hl-${esc(p[0])}">${esc(p[1])}</mark>`;
    return `<div class="doc"><p class="t">${esc(x.title)}</p><p class="small muted">${esc(S.data?.company)} · ${esc(x.product)} · saved ${esc(x.saved)}</p>
      <div class="cl"><p><i>${esc(S.data?.legend)}</i></p></div>
      ${x.lines.map((l) => `<div class="cl"><p><b>${esc(l.label)}:</b> ${l.parts.map(part).join("")}</p></div>`).join("")}</div>`;
  }
  function docIndexCard(x, views) {
    const by = Object.fromEntries(views.filter((v) => v.doc === x.id).map((v) => [v.arm, v]));
    const s = by.saved;
    return `<div class="card warnc"><h3 style="margin-top:0">${esc(x.title)}</h3>
      ${(x.marks || []).map((m, k) => `<p class="small" style="margin:12px 0 2px"><b>${esc(m.label)}</b> <mark class="hl-${esc(m.colour)}">${esc(m.claim)}</mark> <span class="muted">${m.colour === "yellow" ? "not cleared" : "cleared by legal"}</span></p>
        ${ARMS.map((a) => { const y = by[a]?.marks?.[k];
          const cls = !y || m.colour !== "yellow" ? "" : y.state === "plain" ? "bad" : y.state === "withheld" ? "ok" : "";
          return `<div class="idx ${cls}"><span class="tag">${esc(armShort(a))}</span>${y ? (m.colour === "yellow" ? stateTag(y.state) + " " : "") + esc(y.line || "(line not found)") : "(not returned by search yet)"}</div>`; }).join("")}`).join("")}
      ${s?.found ? `<p class="small" style="margin-top:12px">Words in the as-saved index text that mark a colour (legend aside): <b>${s.colour_words}</b></p>` : ""}
      ${s?.found && s.summary ? `<p class="small"><b>The index's own summary</b> of the as-saved sheet: “${esc(s.summary)}”${(s.in_summary || []).length ? ` <span class="pill err">states ${s.in_summary.length} yellow claim(s) as fact</span>` : ""}</p>` : ""}</div>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.saved)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-chl")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Yellow means “not cleared to publish”. The highlighter does not reach the index.</h1>
      <p class="lead">Kestrel Outdoor (fictional) keeps its launch fact sheets in shared memory, so the content team's assistant can answer “what brightness can I state in the launch post?”
      while a writer drafts. Product marketing marks each claim with the highlighter: <mark class="hl-yellow">yellow</mark> = still waiting for test-lab or legal sign-off,
      <mark class="hl-green">green</mark> = wording cleared by legal. The assistant runs on MemoryRouter: every question is an ordinary model call with <code>--boundary</code> added.
      This demo stores the sheets three ways — as saved, with each yellow claim tagged in the text, with each yellow claim taken out — shows each highlighted line in the index,
      and asks eleven questions through each boundary. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 110 calls per run (gpt-4o-mini).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-content-writers" target="_blank" rel="noopener">memorylake.ai › AI memory for content writers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, three boundaries</h1>
      <p class="lead">The same two fact sheets three times: as product marketing saved them, with each yellow claim written as “[UNVERIFIED - not cleared to publish: …]” (<code>tag_highlights()</code>),
      and with each yellow claim replaced by “Not cleared to publish yet” (<code>withhold_highlights()</code>). One project each, one boundary over each.
      The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armShort(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The writer's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The fact sheets") : empty("The fact sheets");
    const xs = S.data?.docs || [];
    const y = xs.reduce((k, x) => k + x.marks.filter((m) => m.colour === "yellow").length, 0);
    const g = xs.reduce((k, x) => k + x.marks.filter((m) => m.colour === "green").length, 0);
    return `<h1>The fact sheets — two Word files</h1>
      <p class="lead">${y} claims highlighted yellow (not cleared), ${g} green (cleared by legal). The colour is a run property, <code>&lt;w:highlight w:val="yellow"/&gt;</code>, on otherwise ordinary text;
      the legend at the top of each sheet says what the colours mean. Before upload the demo makes the two other copies and checks every line against the data file.</p>
      ${xs.map((x) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(x.file)}.docx</h3>
        <span class="pill err">${x.marks.filter((m) => m.colour === "yellow").length} not cleared</span></div>
        <div style="margin-top:10px">${docCard(x)}</div></div>`).join("")}`;
  }

  function index() {
    const h = "The highlights in the index — what is left of the colour?";
    if (!S.index) return running() ? waiting(h) : empty(h);
    const per = S.index.per || {};
    return `<h1>${h}</h1>
      <p class="lead"><code>search --types document</code> returns the text the index holds for each file; this is the text MemoryRouter attaches. The words come through without their
      colour: in the as-saved sheets a yellow claim reads exactly like a verified one. ${ARMS.map((a) => `<b>${esc(armShort(a))}</b>: plain ${per[a]?.plain ?? "–"} · tagged ${per[a]?.tagged ?? "–"} · taken out ${per[a]?.withheld ?? "–"}`).join(" · ")}.</p>
      ${(S.data?.docs || []).map((x) => docIndexCard(x, S.index.docs || [])).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const KIND = { unverified: ["yellow: the line is only the claim", "err"], partial: ["yellow claim beside a verified figure", "err"], cleared: ["green: cleared by legal", "ok"], plain: ["control: no highlight", ""] };
    return `<h1>${h}${t ? ` — ${t.yellow.right}/${graded(t.yellow)} right on yellow claims` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ✗ an unverified claim published as fact · ◐ quoted with a caveat · ○ held back the verified figure too · ⊘ cleared claim called unverified · ∅ nothing attached.</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict))[0];
        const k = KIND[r.qkind] || [r.qkind, ""];
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small" style="white-space:pre-line">${esc(r.truth)}</div>
          <div class="row small" style="margin-top:4px"><span class="pill ${k[1]}">${esc(k[0])}</span></div>
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
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>index: yellow claims as plain text</th><th>yellow claims: right</th><th>published as fact</th><th>with a caveat</th><th>verified figure held back</th><th>controls</th></tr></thead><tbody>
        ${ARMS.map((k) => `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td><td>${per[k]?.plain ?? "–"}</td>${t[k] ? `<td>${t[k].yellow.right}/${graded(t[k].yellow)}</td><td>${t[k].yellow.published}</td><td>${t[k].yellow.flagged}</td><td>${t[k].yellow.short}</td><td>${t[k].control.right}/${graded(t[k].control)}</td>` : "<td>–</td><td></td><td></td><td></td><td></td>"}</tr>`).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">∅ answers (MemoryRouter attached nothing) are not counted either way. The controls are the two green claims and two lines without a highlight.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant handed the writer instead</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function lookupTool() {
    const ready = !!S.world, xs = S.data?.docs || [];
    const last = S.lookups[S.lookups.length - 1];
    const x = last && xs.find((y) => y.id === last.doc);
    return `<h1>Fact sheet</h1>
      <p class="lead">One fact sheet: its highlights, and each highlighted line in the index text of all three projects. Read-only: searches, no model call.</p>
      <div class="card"><div class="row">${xs.map((y) => `<button class="chip ${S.pick === y.id ? "on" : ""}" data-pick="${esc(y.id)}">${esc(y.product)}</button>`).join(" ")}</div>
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
      else if (S.caughtUp) banner("Done. Try Fact sheet: one file's highlights and their lines in the three projects' index.", true);
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

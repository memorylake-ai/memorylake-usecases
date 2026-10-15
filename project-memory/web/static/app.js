/* Project memory for product managers — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one roadmap project, four people", view: "setup" },
    { n: 3, title: "Decision log", sub: "who may decide, four decisions", view: "decisions" },
    { n: 4, title: "The channel", sub: "four messages, one at a time", view: "channel" },
    { n: 5, title: "What it remembers", sub: "pinned, rewritten, extracted", view: "memory" },
    { n: 6, title: "Quick read", sub: "trust the top search hit", view: "quick" },
    { n: 7, title: "Who said it?", sub: "trace → message → speaker", view: "provenance" },
    { n: 8, title: "Write it back", sub: "restore, label, re-ask", view: "written" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, decisions: null, messages: [], memory: null, quick: null, provenance: null, compare: null,
    written: null, after: null, ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const mark = (ok) => (ok ? '<span class="pill ok">✓</span>' : '<span class="pill err">✗</span>');

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the roadmap<small>top hit vs the decision</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, decisions, channel, memory, quick, provenance, written, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Roadmap memory</dt><dd>${st.ready ? "from an earlier run — Run again replays today, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3–4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Whoever speaks in the roadmap channel can rewrite the roadmap.</h1>
      <p class="lead">Fieldbook's product manager keeps the roadmap in one MemoryLake project: who may decide, and four decisions with their owners.
      The team's roadmap channel goes into the same project. When the VP of Sales says offline mode is back, extraction can rewrite the PM's decision record in place — and the
      project's top answer becomes his sentence. <code>fact trace</code> still knows whose message it was. This page drives the <code>memorylake</code> CLI for real. All companies and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the roadmap project, the channel conversation and the four people.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/project-memory-for-product-managers" target="_blank" rel="noopener">memorylake.ai › Project memory for product managers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  const initials = (n) => String(n || "").split(" ").map((w) => w[0]).slice(0, 2).join("");
  function setup() {
    const su = S.setup; if (!su) return empty("One roadmap project, four people");
    return `<h1>One roadmap project, four people</h1>
      <p class="lead">Decisions, decision rights and the roadmap channel all land on one project — the one every tool asks about the roadmap.
      Each person is an actor, so every channel message carries who sent it. Two of them decide; two of them ask.</p>
      <div class="grid">
        <div class="card"><h2 style="margin-top:0">${esc(su.project)}</h2><div class="muted small">decision rights, decision log, #fieldbook-roadmap</div><div class="mono small faint">${esc(su.ids.roadmap)}</div></div>
        ${su.people.map((p) => `<div class="card person"><span class="avatar ${p.decides ? "" : "user"}">${esc(initials(p.name))}</span><div><div class="name">${esc(p.name)}</div><div class="role">${esc(p.role)}</div><div class="mono small faint">${esc(su.ids[p.key])}</div></div><span class="status pill ${p.decides ? "ok" : ""}">${p.decides ? "decides" : "asks"}</span></div>`).join("")}
      </div>`;
  }

  function decisions() {
    const x = S.decisions; if (!x) return empty("The decision log");
    return `<h1>The decision log, pinned with its owners</h1>
      <p class="lead">One <code>fact add --project</code> for the rights and all four decisions, then <code>fact update --metadata</code> on each: who decided, their role, the date, the item.
      Each entry also names its decider in a fixed format — that matters in step 7.</p>
      <div class="card"><p style="margin-top:0"><b>Decision rights.</b> ${esc(x.rights)}</p></div>
      <div class="card"><table class="iso"><tr><th>id</th><th>decided</th><th>by</th><th>item</th><th>decision</th></tr>
        ${x.items.map((d) => `<tr><td><b>${esc(d.id)}</b></td><td>${esc(d.on)}</td><td>${esc(d.by)}<div class="small muted">${esc(d.role)}</div></td><td>${esc(d.item)}</td>
          <td class="small">${esc(d.text.split(": ").slice(1).join(": "))}</td></tr>`).join("")}</table></div>`;
  }

  function channel() {
    const ms = S.messages; if (!ms.length) return empty("#fieldbook-roadmap");
    const total = story()?.channel?.messages?.length || 4;
    return `<h1>${esc(story()?.channel?.name || "#fieldbook-roadmap")} — one message at a time</h1>
      <p class="lead">One GROUP conversation on the roadmap project. Each message is appended with its sender's actor and read into memory before the next one arrives
      (<code>conv consumed-messages</code> confirms a batch of 1), so every fact it produces traces back to exactly one message.</p>
      <div class="card"><div class="bubbles">${ms.map((m) => `<div class="bubble ${m.decides ? "bot" : "user"}"><b>${esc(m.who)}</b> <span class="small muted">${esc(m.role)} · ${esc(String(m.at).slice(0, 10))}</span><br>${esc(m.line)}
        <div class="small muted">${m.seconds == null ? "read by an earlier run" : `read into memory in ${esc(m.seconds)}s · batch of ${esc(m.batch)}`}${m.decides ? " · can decide" : " · cannot decide"}</div></div>`).join("")}
        ${ms.length < total && S.st.status === "running" ? '<p><span class="pill busy"><span class="spinner"></span> reading the next message into memory…</span></p>' : ""}</div></div>`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("What the project remembers");
    const cls = (k) => (k.includes("rewritten") ? "personal" : k.startsWith("pinned") ? "clean" : "");
    return `<h1>What the project remembers now</h1>
      <p class="lead">${x.rewritten.length ? `<b>${x.rewritten.length} pinned decision(s) were rewritten in place</b> by channel messages: same fact id, new words — and the pinned metadata (who decided, when) is gone.
        The PM never changed them.` : "No pinned decision was rewritten in place this time; the channel's claims are new facts of their own."}
        Nothing extracted says who said it, and nothing landed on the people's own actors.</p>
      <div class="card">${x.facts.map((f) => `<div class="memfact ${cls(f.kind)}"><span class="mk">${f.kind.includes("rewritten") ? "↻" : f.kind.startsWith("pinned") ? "📌" : "+"}</span>
        <span class="body"><span class="pill ${f.kind.includes("rewritten") ? "warn" : ""}">${esc(f.kind)}</span> ${esc(f.text)}</span></div>`).join("")}</div>
      <p class="tally"><span class="pill">on actors: ${Object.entries(x.actors).map(([n, c]) => `${esc(n.split(" ")[0])} ${c}`).join(" · ")}</span>
        <span class="pill">contradictions raised: ${x.conflicts.length}</span></p>`;
  }

  function quickRows(rows) {
    return rows.map((r) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(r.question)}</h2>${mark(r.ok)}</div>
      ${r.text ? `<div class="trace">top hit #${esc(r.rank)}: “${esc(r.text)}”</div><p class="small" style="margin-bottom:0">reads as <b>${esc(r.said)}</b></p>` : '<p class="muted">no hit about it in the top 8</p>'}</div>`).join("");
  }
  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read: ask the project, trust the top hit</h1>
      <p class="lead">What any tool wired to this project would do: <code>search --projects &lt;roadmap&gt; --types fact</code>, take the first hit about the item.
      Relevance ranking does not know who may decide.</p>
      ${quickRows(x.rows)}
      <p class="tally"><span class="pill ${x.ok === x.rows.length ? "ok" : "warn"}">top hit right: ${x.ok}/${x.rows.length}</span></p>`;
  }

  function provenance() {
    const x = S.provenance; if (!x) return empty("Who said it?");
    const items = story()?.items || [];
    const c = S.compare;
    return `<h1>Who said it? fact trace → message → speaker → rights</h1>
      <p class="lead">For every roadmap fact: <code>fact trace</code> gives the entry that wrote its current words. A COOK entry names its source message
      (<code>conv msg list</code> → sender); a MANUAL entry in the decision-log format names its decider. Only someone in the decision rights can decide.
      When a claim overwrote a decision, the decision's own words are still earlier in the same trace.</p>
      ${items.map((it) => { const rows = x.rows.filter((r) => r.item === it.key); if (!rows.length) return "";
        return `<div class="card"><h2 style="margin-top:0">${esc(it.name)}</h2>${rows.map((r) => `<div class="memfact ${r.authorized ? "clean" : r.annotated ? "" : "personal"}">
          <span class="mk">${r.authorized ? "✓" : r.annotated ? "·" : "✗"}</span><span class="body">“${esc(r.text)}”
          <div class="trace">${esc(r.chain)} → <b>${esc(r.verdict)}</b></div>
          ${r.overwrote ? `<div class="trace">same fact, earlier in its trace: “${esc(r.standing_text)}”</div>` : ""}</span></div>`).join("")}</div>`; }).join("")}
      ${c ? `<div class="card"><table class="iso"><tr><th>question</th><th>top search hit</th><th>decided, by who may decide</th></tr>
        ${c.rows.map((r) => `<tr><td>${esc(r.question)}</td><td>${mark(r.quick_ok)} ${esc(r.quick || "—")}</td>
          <td>${mark(r.memory_ok)} ${esc(r.memory || "—")} <span class="small muted">${r.by ? `${esc(r.by)}, ${esc(r.at)}` : ""}${r.restored ? " · from the fact's own trace" : ""}</span></td></tr>`).join("")}</table>
        <p class="tally"><span class="pill warn">top hit: ${c.quick}/${c.rows.length}</span> <span class="pill ok">by who may decide: ${c.memory}/${c.rows.length}</span></p></div>` : ""}`;
  }

  function written() {
    const x = S.written; if (!x) return empty("Write it back");
    const a = S.after;
    return `<h1>Write it back</h1>
      <p class="lead">A decision a claim overwrote gets its own words back on the same fact id (<code>fact update --text --metadata</code>), and the claim is kept as a fact of its own that says
      who said it and what stands. A claim that never touched a decision is rewritten in place the same way. Then the same quick read, asked in new words.</p>
      ${x.restored.map((d) => `<div class="card"><h2 style="margin-top:0">${esc(d.key)} restored <span class="mono small faint">${esc(d.id)}</span></h2>
        <div class="trace">was: “${esc(d.before)}”</div><div class="trace">now: “${esc(d.after)}”</div><div class="trace">new claim: “${esc(d.claim)}”</div></div>`).join("")}
      ${x.annotated.map((d) => `<div class="card"><h2 style="margin-top:0">claim labelled <span class="mono small faint">${esc(d.id)}</span></h2>
        <div class="trace">was: “${esc(d.before)}”</div><div class="trace">now: “${esc(d.after)}”</div></div>`).join("")}
      ${a ? `${quickRows(a.rows)}<p class="tally"><span class="pill ${a.ok === a.rows.length ? "ok" : "warn"}">top hit right after the write-back: ${a.ok}/${a.rows.length}</span></p>`
        : '<p><span class="pill busy"><span class="spinner"></span> asking again…</span></p>'}`;
  }

  function askCard(r) {
    const q = r.quick, m = r.memory;
    return `<div class="card"><h2 style="margin-top:0">${esc(r.question)} <span class="muted small">— ${esc(r.name)}</span></h2>
      <p>${mark(q.ok)} <b>top search hit</b> ${q.text ? `#${esc(q.rank)}: “${esc(q.text)}” → <b>${esc(q.said)}</b>` : "— nothing about it in the top 8"}</p>
      <p>${mark(m.ok)} <b>decided, by who may decide</b>: ${m.said ? `<b>${esc(m.said)}</b> <span class="small muted">(${esc(m.by)}, ${esc(m.at)})</span>` : "no decision on record"}</p>
      ${r.rows.map((x) => `<div class="trace">“${esc(x.text)}”<br>${esc(x.chain)} → <b>${esc(x.verdict)}</b></div>`).join("")}</div>`;
  }
  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask the roadmap</h1>
      <p class="lead">Any question that names a roadmap item (offline mode, dark mode, the tablet layout, SSO): the top search hit, and the same item read by who may decide —
      with every fact about it traced to its author.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Is offline mode coming back?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Is offline mode coming back?", "Is dark mode shipping in November?", "When does the tablet layout ship?", "Is SSO still planned for Q4?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  // ---------------------------------------------------------------- behaviour
  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> asking…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = askCard(r);
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
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
    ["#btn-run", "#btn-reset", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.setup = null; S.decisions = null; S.messages = []; S.memory = null; S.quick = null; S.provenance = null; S.compare = null; S.written = null; S.after = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's roadmap project, channel conversation and four people from your account?")) return;
    banner(""); const r = await api("/api/cleanup", {}); if (r) { S.st = r; setConn(); }
  });

  // ---------------------------------------------------------------- terminal
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

  // ---------------------------------------------------------------- events
  const stepOf = (i) => STEPS.find((s) => s.n === i);
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; S.step = d.index || 0;
      if (d.index === 2) resetView();
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    // While catching up on page load, /api/state (fetched first) is newer than an old "connected" event.
    else if (k === "connected") { if (S.caughtUp) S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "setup") S.setup = d;
    else if (k === "decisions") S.decisions = d;
    else if (k === "message") S.messages.push(d);
    else if (k === "memory") S.memory = d;
    else if (k === "quick") S.quick = d;
    else if (k === "provenance") S.provenance = d;
    else if (k === "compare") S.compare = d;
    else if (k === "written") S.written = d;
    else if (k === "after") S.after = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the roadmap project, the channel conversation and the four people are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a roadmap question of your own — “Ask the roadmap” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "decisions", "message", "memory",
    "quick", "provenance", "compare", "written", "after", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    // "error" is also EventSource's own connection-error event, which has no data: skip those.
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
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.written ? "written" : "connect");
    subscribe();
  })();
})();

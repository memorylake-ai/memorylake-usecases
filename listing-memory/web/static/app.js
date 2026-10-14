/* Listing memory for real estate teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project per listing", view: "setup" },
    { n: 3, title: "Intake", sub: "five tools, four versions", view: "intake" },
    { n: 4, title: "Quick read", sub: "against today's listing", view: "quick" },
    { n: 5, title: "Memory read", sub: "against the listing that day", view: "memory" },
    { n: 6, title: "Write it back", sub: "which listing it was about", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", topic: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const topicLabel = (t) => t === "now" ? "What buyers object to now" : (story()?.topics?.[t]?.label || t);
  const objLabel = (t) => (story()?.topics?.[t]?.label || t).toLowerCase();
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill">${esc(toolName(t))}</span>`;
  const ansPill = (a, bad) => `<span class="pill ${bad ? "err" : ""}">${esc(a?.short ?? "—")}</span>`;
  const objs = (list) => (list || []).length ? list.map((o) => `<span class="pill">${esc(objLabel(o))}</span>`).join(" ") : '<span class="faint">—</span>';
  const counts = (c) => { const e = Object.entries(c || {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])); return e.length ? e.map(([k, v]) => `${esc(objLabel(k))} ${v}`).join(" · ") : "nothing"; };
  const fields = () => Object.keys(story()?.fields || { price: 1, roof: 1, hoa: 1 });
  const verLabel = (shown) => fields().map((f) => shown?.[f] ?? "?").join(" · ");
  function hitTable(hits, fb) {
    const set = new Set(fb || []);
    return `<table class="iso"><tr><th>rank</th><th>record</th><th>day</th><th>text · source</th></tr>
      ${hits.map((h) => `<tr class="${set.has(h.record) ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small faint" style="white-space:nowrap">${esc(h.on)}</td><td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${set.has(h.record) ? '<span class="pill accent">feedback · counted</span>' : ""}</td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any question · versions</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, memory, writeback, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays Jordan's first morning on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about a minute and a half · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>“Too expensive.” At which price?</h1>
      <p class="lead">Larkspur Realty keeps one MemoryLake project per listing. For 14 Alder Court it holds everything since August — MLS changes, the seller's disclosure, the HOA's letters,
      every buyer's agent's showing feedback, a withdrawn offer. The feedback is true, but it never says which listing it saw: <b>$749,000 with the 1998 roof</b>, or <b>$699,000 with a new roof
      and a $14,500 HOA assessment</b>. Counted against today's listing, it says “cut the price again”. Joined to the listing on the day of each showing, it says something else.
      This page drives the <code>memorylake</code> CLI for real. The team, the house and the people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the project and every record on it.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/listing-memory-for-real-estate-teams" target="_blank" rel="noopener">memorylake.ai › Listing memory for real estate teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One project for 14 Alder Court");
    return `<h1>One project for 14 Alder Court</h1>
      <p class="lead">“Per-listing memory — seller context, pricing history, prior offers.” Every tool's record about the listing goes into one project; each keeps its own words in the text,
      and the tool, the author and <b>the day</b> in metadata — plus, for feedback, what the buyers objected to.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const tools = Object.keys(story()?.tools || {});
    return `<h1>Intake — ${x.records.length} records from ${tools.length} tools, and four versions of one house</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each.
      The MLS, the roofer and the HOA say when the listing changed; the showing feedback does not say which version the buyers saw.
      ${x.restored ? ` <span class="faint">(${x.restored} record(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">The listing's versions</h2><table class="iso"><tr><th></th><th>since</th><th>listing</th><th>what changed</th><th>feedback while it stood</th></tr>
        ${x.versions.map((v) => `<tr><td class="mono small">${esc(v.v)}</td><td class="small" style="white-space:nowrap"><b>${esc(v.since)}</b></td><td class="small"><b>${esc(v.label)}</b></td>
          <td class="small">${v.changes.map((c) => `${esc(c.record)} · ${esc(c.field)} → ${esc(c.label)}`).join("<br>")}</td><td class="small mono">${esc(v.feedback.join(", "))}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Every record</h2><table class="iso"><tr><th>record</th><th>day</th><th>by</th><th>text</th></tr>
        ${x.records.map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}<br>${toolPill(r.tool)}</td><td class="small faint" style="white-space:nowrap">${esc(r.on)}</td><td class="small" style="white-space:nowrap">${esc(r.by)}<br><span class="faint">${esc(r.role)}</span></td><td class="small">${esc(r.body)}${r.objections ? `<br>${objs(r.objections)}` : ""}</td></tr>`).join("")}</table></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — counted against today's listing</h1>
      <p class="lead">What “any agent covering a showing retrieves full listing context” does the quick way: <code>search</code> the question, take the showing feedback in the hits,
      count what it objects to — as if every buyer saw the listing as it is today: <b>${esc(x.now)}</b>.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.question.id)} · ${esc(q.question.text)}</h2>${ansPill(q.ans)}</div>
        ${hitTable(q.hits, q.feedback)}
        <p class="small">${q.feedback.length} piece(s) of feedback in the hits, all counted against today's listing: ${counts(q.ans.counts)}</p>
        <p class="small" style="margin-bottom:0">→ <b>${esc(q.ans.said)}</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.agree);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} answers change with which listing the feedback is read against</h1>
      <p class="lead">Every piece of feedback (<code>fact list</code>, every page), each joined to the listing as it was <b>on the day of that showing</b> — the MLS, permit and HOA records on file that day. The <b>same counting code</b>.
      Today's listing: <b>${esc(verLabel(x.now))}</b>.</p>
      <div class="card"><table class="iso"><tr><th>question</th><th>quick read (today)</th><th>memory read (that day)</th><th class="faint">every record, today's listing</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.question.id)}</span> ${esc(r.question.text)}</td>
          <td class="small">${ansPill(r.quick, !r.agree)}</td><td class="small">${ansPill(r.memory)}</td><td class="small faint">${esc(r.control.short)}</td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table>
        <p class="small faint" style="margin-bottom:0">The grey column reads every record without <code>search</code>, against today's listing: it gives the quick read's answer each time — the join, not the retrieval, makes the difference.</p></div>
      <div class="card mine"><h2 style="margin-top:0">Every piece of feedback, joined to its day</h2><table class="iso"><tr><th>record</th><th>shown</th><th>listing that day</th><th>objected to</th></tr>
        ${x.joined.map((j) => `<tr class="${j.differs.length ? "" : "hl"}"><td class="mono small">${esc(j.record)}</td><td class="small" style="white-space:nowrap">${esc(j.on)}</td>
          <td class="small">${esc(verLabel(j.shown))}${j.differs.length ? ` <span class="faint">· not today's ${esc(j.differs.join(", "))}</span>` : ' <span class="pill ok">today\'s listing</span>'}</td><td class="small">${objs(j.objections)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => `<div class="card ${r.agree ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.question.id)} · ${esc(r.question.text)}</h2>${agreePill(r.agree)}</div>
        <table class="iso"><tr><td class="small" style="white-space:nowrap">quick read</td><td class="small">${esc(r.quick.said)}</td></tr>
          <tr><td class="small" style="white-space:nowrap"><b>memory read</b></td><td class="small"><b>${esc(r.memory.said)}</b><br><span class="faint">counted ${r.kept.length} about ${esc(r.memory.scope)}: ${counts(r.memory.counts)}${r.left.length ? ` · left out ${r.left.length} given at another version` : ""}</span></td></tr></table></div>`).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    const c = x.census || {};
    return `<h1>Write it back — the quick read agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">Each piece of feedback now says which listing it was about (<code>fact update --text</code> and <code>metadata.shown_at</code>; the old text goes to <code>metadata.previous</code>), and <code>fact trace</code> keeps both versions.
      Then the same questions in other words go through the <b>same quick-read code</b>.</p>
      <div class="card"><h2 style="margin-top:0"><code>fact list --query '${esc(c.query)}'</code> <span class="small faint">exact substring, every record</span></h2><table class="iso">
        <tr><td class="small">before</td><td class="small"><b>${(c.before || []).length}</b></td><td class="mono small">${esc((c.before || []).join(", "))}</td></tr>
        <tr class="hl"><td class="small">after</td><td class="small"><b>${(c.after || []).length}</b></td><td class="mono small">${esc((c.after || []).join(", "))}</td></tr></table>
        <p class="small faint" style="margin-bottom:0">Before, no piece of feedback named the price it was given at — a question about today's price could not find the feedback about it.</p></div>
      <div class="card mine"><h2 style="margin-top:0">Feedback</h2><table class="iso"><tr><th>record</th><th>was</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small" style="white-space:nowrap">${esc(u.record)}<br><span class="faint">${esc(u.on)}</span></td><td class="small muted">${esc(u.was)}</td><td class="small">${esc(u.was)} <b>${esc(u.now.slice(u.was.length).trim())}</b></td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace ${esc(x.updated[0]?.record)}</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}
      <div class="card"><h2 style="margin-top:0">The same questions, other words, quick read again</h2><table class="iso"><tr><th>question</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.question.id)}</span> “${esc(a.question.text)}”<br><span class="faint">${a.feedback.length} feedback in the hits · ${a.n} about today's listing</span></td>
          <td class="small">${esc(a.quick)}${a.tries > 1 ? ` <span class="faint">· try ${a.tries}</span>` : ""}</td><td class="small">${esc(a.memory)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, topics = Object.keys(S.data?.kinds || {});
    return `<h1>Ask</h1>
      <p class="lead">Type any question and pick its topic: the quick read (feedback counted against today's listing) and the memory read (against the listing on the day of each showing) side by side.
      Or list every piece of feedback with the listing it saw.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Would a lower price bring offers?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-topic" ${ready ? "" : "disabled"} style="max-width:220px">${topics.map((k) => `<option value="${esc(k)}" ${k === a.topic ? "selected" : ""}>${esc(topicLabel(k))}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-topic="${esc(e.topic)}">${esc(e.q)}</button>`).join("")}
          <button type="button" class="chip" data-versions="1">versions: every piece of feedback</button></div>
        <div id="ask-result" class="result">${ready ? a.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("#ask-q").value.trim(), $("#ask-topic").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-topic").value = c.dataset.topic; askQuestion(c.dataset.q, c.dataset.topic); }));
      st.querySelector(".chips .chip[data-versions]")?.addEventListener("click", () => askVersions());
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQuestion(q, topic) {
    if (!q) return; S.ask.q = q; S.ask.topic = topic;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the listing's memory…</span>`;
    const r = await api("/api/ask", { q, topic });
    if (!r) { box.innerHTML = ""; return; }
    const b = r.before;
    putResult(box, `<div class="row spread"><h2>“${esc(r.question.text)}” · ${esc(topicLabel(topic))}</h2>${agreePill(r.agree)}</div>
      <p class="small faint">today's listing: ${esc(r.now)}</p>
      <div class="cols"><div><h3>Quick read</h3><p class="small">${r.feedback.length} piece(s) of feedback in the hits: ${counts(r.quick.counts)}<br>→ <b>${esc(r.quick.said)}</b></p></div>
        <div><h3>Memory read</h3><p class="small">counted ${r.memory.n} about ${esc(r.memory.scope)}: ${counts(r.memory.counts)}<br>→ <b>${esc(r.memory.said)}</b></p></div></div>
      ${b ? `<p class="small">Before step 6 stamped each piece of feedback with its listing, every record read against today's listing gave: <b>${esc(b.said)}</b>.</p>` : ""}
      <details><summary class="small">search's top ${r.hits.length}</summary>${hitTable(r.hits, r.feedback)}</details>`);
  }
  async function askVersions() {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/versions", {});
    if (!r) { box.innerHTML = ""; return; }
    const n = r.rows.filter((x) => x.differs.length).length;
    putResult(box, `<div class="row spread"><h2><code>fact list</code> · every piece of feedback</h2><span class="pill">${r.rows.length} record(s)</span></div>
      <table class="iso"><tr><th>record</th><th>shown</th><th>listing that day</th><th>differs from today</th><th>objected to</th></tr>
      ${r.rows.map((x) => `<tr class="${x.differs.length ? "" : "hl"}"><td class="mono small">${esc(x.record)}${x.how === "stated" ? ' <span class="pill accent">stated</span>' : ""}</td><td class="small" style="white-space:nowrap">${esc(x.on)}<br><span class="faint">${esc(x.by)}</span></td>
        <td class="small">${esc(x.shown)}</td><td class="small">${esc(x.differs.join(", ") || "—")}</td><td class="small">${objs(x.objections)}</td></tr>`).join("")}</table>
      <p class="small">Today's listing: <b>${esc(r.now)}</b>. <b>${n} of ${r.rows.length}</b> pieces of feedback were about a listing that no longer exists.</p>`);
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.memory = null; S.writeback = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's 14 Alder Court project and every record on it from your account?")) return;
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
    else if (k === "intake") S.intake = d;
    else if (k === "quick") S.quick = d;
    else if (k === "memory") S.memory = d;
    else if (k === "writeback") S.writeback = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and every record on it are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask a question of your own, or list every piece of feedback with the listing it saw — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick", "memory", "writeback",
    "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.writeback ? "writeback" : "connect");
    subscribe();
  })();
})();

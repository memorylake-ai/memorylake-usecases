/* Tax season memory for accounting teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one season file", view: "setup" },
    { n: 3, title: "Intake", sub: "directory, returns, emails, notes", view: "intake" },
    { n: 4, title: "Quick read", sub: "the closest record with the name", view: "quick" },
    { n: 5, title: "Memory read", sub: "whose record is it?", view: "memory" },
    { n: 6, title: "File it", sub: "each client's own memory", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", client: "C-2290", topic: "carryover", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const toolName = (t) => story()?.tools?.[t] || t;
  const who = (c) => { const x = story()?.clients?.[c]; return x ? `${x.name} (${c})` : c; };
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const okPill = (a) => a ? `<span class="pill ok">✓ same record</span>` : `<span class="pill err">✗ differs</span>`;
  const toolPill = (t) => `<span class="pill">${esc(toolName(t))}</span>`;
  const clientPill = (c, mine) => !c ? `<span class="pill warn">not confirmed</span>` : `<span class="pill ${mine === undefined ? "" : mine ? "ok" : "err"}">${esc(c)}</span>`;
  const via = (r) => r.channel || (r.ssn ? `SSN ending ${r.ssn}` : "") || (r.binder ? `binder ${r.binder}` : "");
  function hitTable(hits, used, owners) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record · source</th></tr>
      ${hits.map((h) => `<tr class="${h.record === used ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br>${toolPill(h.tool)} ${h.client ? clientPill(h.client) : ""} ${owners && owners[h.record] !== undefined && !h.client ? `<span class="faint">really ${esc(owners[h.record] || "nobody's")}</span>` : ""} ${h.record === used ? '<span class="pill accent">used</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  function verdictPill(v) { return v === "used" ? `<span class="pill ok">used</span>` : v === "set aside" ? `<span class="pill warn">set aside</span>` : `<span class="pill err">not this client</span>`; }
  function topicTable(rows, used) {
    return `<table class="iso"><tr><th>record</th><th>says</th><th>whose · how</th><th></th></tr>
      ${rows.map((x) => `<tr class="${x.record === used ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(x.record)}<br><span class="faint">${esc(toolName(x.tool))}</span></td>
        <td class="small">${esc(x.value)}</td><td class="small">${x.owner ? `<b>${esc(x.owner)}</b>` : '<b>?</b>'} <span class="faint">· ${esc(x.via)}</span></td>
        <td>${verdictPill(x.verdict)}${x.record === used ? ' <span class="pill accent">quick read</span>' : ""}</td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any question · a client's records</small></span></button>`;
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
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays Priya's morning on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about two minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Two clients called Dan Okafor. The search hands the father's records to the son's return.</h1>
      <p class="lead">Lindqvist &amp; Oduya CPAs keeps its 2025 tax season in one MemoryLake project — the client directory, last year's return exports, client emails and voicemails, workpaper notes and team chat.
      On October 15 Priya picks up two returns: Daniel Okafor Sr. and his son Daniel Okafor Jr. Both sign their emails “Dan Okafor”.
      Every record is true. Whose it is lives only on what it came in by — the sender address, the calling number, the binder, the SSN. The quick read goes by the name; the memory read resolves each record's channel against the client directory, and files it under the right client.
      This page drives the <code>memorylake</code> CLI for real. The firm and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the season file, both client files and every record on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-accounting-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for accounting teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("One season file");
    return `<h1>One season file</h1>
      <p class="lead">“Client context that persists across engagements.” Every record of the season goes into one project, word for word; each keeps in metadata what it carried when it came in — a client id on exports, the sender address on an email, the calling number on a voicemail, the binder on a workpaper, the SSN on an IRS notice.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr></table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const plain = x.records.filter((r) => !r.client && /okafor/i.test(r.body)).sort((a, b) => (a.on + a.record).localeCompare(b.on + b.record));
    const rest = x.records.filter((r) => !plain.includes(r));
    return `<h1>Intake — ${x.records.length} records</h1>
      <p class="lead">One <code>fact add</code> for all of them, word for word, then <code>fact update --metadata</code> on each with what the record itself carries.
      ${x.restored ? ` <span class="faint">(${x.restored} record(s) an earlier run tagged with a client were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">${plain.length} records that only say “Okafor”</h2><p class="small muted">Whose they are is only in what they came in by.</p><table class="iso"><tr><th>id</th><th>came in by</th><th>record</th></tr>
        ${plain.map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}<br><span class="faint">${esc(toolName(r.tool))}</span></td><td class="mono small">${esc(via(r) || "—")}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Everything else · ${rest.length}</h2><table class="iso"><tr><th>id</th><th>carries</th><th>record</th></tr>
        ${rest.map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}<br><span class="faint">${esc(toolName(r.tool))}</span></td><td class="small">${r.client ? clientPill(r.client) : esc(via(r) || "—")}</td><td class="small">${esc(r.body)}</td></tr>`).join("")}</table></div>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    return `<h1>The quick read — the closest record with the client's name</h1>
      <p class="lead">What an assistant does with “client context that loads automatically”: <code>search</code> the question, take the first hit on that topic that mentions the client and is not tagged with another client.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.q.id)} · ${esc(q.q.title)} · return ${esc(who(q.q.client))}</h2><span class="pill accent">→ ${esc(q.value || "?")}</span></div>
        <p class="small">“${esc(q.q.text)}”</p>${hitTable(q.hits, q.used)}
        <p class="small" style="margin-bottom:0">${esc(q.used)}: <b>“${esc(q.used_body)}”</b></p>
        ${q.cached?.length ? `<p class="small faint">${q.cached.length} hit(s) carry fact ids not in <code>fact list</code> now — search answered from its cache</p>` : ""}</div>`).join("")}`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const wrong = x.rows.filter((r) => !r.ok);
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} answers come from the other Okafor</h1>
      <p class="lead">Every record that says “Okafor” (<code>fact list --query</code>, an exact substring, every page), each resolved by what it carries: a client id, a workpaper binder, or an address, number or SSN looked up in the client directory (<code>fact list --query</code>) — one directory entry, or nobody. Then the other way round: those clients' addresses, numbers and SSNs looked up in every record's text.</p>
      <div class="card"><table class="iso"><tr><th>item</th><th>the quick read says</th><th>the client's own records say</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.ok ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.q.id)}</span> ${esc(r.q.title)}<br><span class="faint">return ${esc(who(r.q.client))}</span></td>
          <td class="small">${esc(r.quick)}<br><span class="faint">${esc(r.used)} · ${esc(r.used_owner ? who(r.used_owner) : "not confirmed")}</span></td><td class="small"><b>${esc(r.value)}</b></td><td>${okPill(r.ok)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Whose record is it?</h2><table class="iso"><tr><th>record</th><th>whose</th><th>how</th></tr>
        ${x.resolved.map((v) => `<tr><td class="mono small">${esc(v.record)}</td><td>${clientPill(v.owner)}</td><td class="small">${esc(v.via)}</td></tr>`).join("")}</table></div>
      ${x.rows.map((r) => `<div class="card ${r.ok ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.q.id)} · ${esc(r.q.title)} · return ${esc(who(r.q.client))}</h2>${okPill(r.ok)}</div>
        <p class="small">“${esc(r.q.text)}”</p>${topicTable(r.records, r.used)}
        <p class="small" style="margin-bottom:0">Memory read: <b>${esc(r.value)}</b>${!r.ok && r.q.stakes ? `<br><span class="faint">On the return as the quick read has it: ${esc(r.q.stakes)}</span>` : ""}</p></div>`).join("")}`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("File it under the client");
    return `<h1>File it under the client — ${x.agree} of ${x.after.length} from the right person</h1>
      <p class="lead">Each resolved record goes into its client's own memory — one actor per client, <code>fact add --actor</code> with the full name and client id in the text, where it came from and how it was resolved in metadata. The season file keeps the original and gets the client tag.
      Later the same day, three more questions go through the <b>same quick read</b>, now scoped to the client's own memory (<code>search --actors</code>), asked in new words.</p>
      <div class="grid">${Object.entries(x.files).map(([c, fs]) => `<div class="card mine"><h2 style="margin-top:0">${esc(who(c))}</h2><p class="small faint mono">${esc(x.actors[c])}</p><table class="iso">
        ${fs.sort((a, b) => (a.on + a.record).localeCompare(b.on + b.record)).map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.record)}</td><td class="small">${esc(r.body)}<br><span class="faint">${esc(r.resolved_via)}</span></td></tr>`).join("")}</table></div>`).join("")}</div>
      <div class="card"><h2 style="margin-top:0">Filed nowhere · ${x.unresolved.length}</h2><table class="iso">
        ${x.unresolved.map((u) => `<tr><td class="mono small">${esc(u.record)}</td><td class="small">${esc(u.body)}<br><span class="pill warn">${esc(u.via)}</span></td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Later questions, quick read in the client's own memory</h2><table class="iso"><tr><th>item</th><th>used</th><th>says</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono" style="white-space:nowrap">${esc(a.q.id)}</span> · ${esc(who(a.q.client))}<br>“${esc(a.q.text)}”<br><span class="faint">${a.hits} hits, ${a.others.length} from anyone else</span></td><td class="mono small" style="white-space:nowrap">${esc(a.used)} <span class="faint">#${esc(a.rank)}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td class="small">${esc(a.value)}</td><td>${okPill(a.ok)}</td></tr>`).join("")}</table></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, topics = ["carryover", "estimates", "state", "notice", "business", "income"];
    return `<h1>Ask</h1>
      <p class="lead">Ask anything about one of the two returns: the quick read over the season file as it came in, the memory read (whose record is it?), and — once a run has filed the records — the quick read in that client's own memory. Or list every record that is one client's.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Did Dan Okafor carry a capital loss into 2025?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-client" ${ready ? "" : "disabled"} style="max-width:220px">${["C-2290", "C-1187"].map((c) => `<option value="${c}" ${c === a.client ? "selected" : ""}>${esc(who(c))}</option>`).join("")}</select>
          <select id="ask-topic" ${ready ? "" : "disabled"} style="max-width:140px">${topics.map((t) => `<option ${t === a.topic ? "selected" : ""}>${t}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-client="${esc(e.client)}" data-topic="${esc(e.topic)}">${esc(e.q)} · ${esc(e.client)}</button>`).join("")}</div>
        <div class="chips">${["C-2290", "C-1187"].map((c) => `<button type="button" class="chip" data-who="${c}">records: ${esc(who(c))}</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askQ($("#ask-q").value.trim(), $("#ask-client").value, $("#ask-topic").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-client").value = c.dataset.client; $("#ask-topic").value = c.dataset.topic; askQ(c.dataset.q, c.dataset.client, c.dataset.topic); }));
      st.querySelectorAll(".chips .chip[data-who]").forEach((c) => c.addEventListener("click", () => askClient(c.dataset.who)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQ(q, client, topic) {
    if (!q) return; S.ask.q = q; S.ask.client = client; S.ask.topic = topic;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the season file…</span>`;
    const r = await api("/api/ask", { q, client, topic });
    if (!r) { box.innerHTML = ""; return; }
    const f = r.filed;
    putResult(box, `<div class="row spread"><h2>“${esc(r.q.text)}” · return ${esc(who(r.q.client))} · ${esc(r.q.topic)}</h2>${okPill(r.ok)}</div>
      <div class="cols"><div><h3>Quick read · season file as it came in</h3><p class="small">${r.used ? `${esc(r.used)}: “${esc(r.used_body)}”<br>→ <b>${esc(r.quick)}</b> <span class="faint">(${esc(r.used_owner ? who(r.used_owner) : "not confirmed")})</span>` : "no record on this topic in the top 8"}</p></div>
        <div><h3>Memory read</h3><p class="small"><b>${esc(r.memory.value)}</b></p>
        ${f ? `<h3>Quick read · ${esc(r.q.client)}'s own memory</h3><p class="small"><b>${esc(f.value || "—")}</b> <span class="faint">(${esc(f.used || "no record on this topic")})</span> ${f.ok ? '<span class="pill ok">✓</span>' : '<span class="pill err">✗</span>'}</p>` : `<p class="small faint">${esc(r.q.client)} has no client file yet — a full run files the records in step 6.</p>`}</div></div>
      ${topicTable(r.memory.rows, r.used)}
      <details><summary class="small">search's top ${r.hits.length} in the season file</summary>${hitTable(r.hits, r.used)}</details>`);
  }
  async function askClient(client) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> resolving…</span>`;
    const r = await api("/api/client", { client });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>${esc(r.name)} (${esc(r.client)}) · ${r.records.length} records resolved</h2><span class="pill">${r.actor ? `client file: ${r.filed} records` : "no client file yet"}</span></div>
      <table class="iso"><tr><th>id</th><th>on</th><th>topic</th><th>how</th><th>record</th></tr>
      ${r.records.sort((a, b) => (a.on + a.record).localeCompare(b.on + b.record)).map((x) => `<tr><td class="mono small" style="white-space:nowrap">${esc(x.record)}</td><td class="mono small" style="white-space:nowrap">${esc(x.on)}</td><td class="small">${esc(x.topic)}</td><td class="small">${esc(x.via)}</td><td class="small">${esc(x.body)}</td></tr>`).join("")}</table>
      <p class="small">Same name, someone else's: <b>${esc(r.others.map(who).join(", ") || "none")}</b></p>
      <p class="small">Not confirmed — filed nowhere: ${r.unresolved.map((u) => `<span class="mono">${esc(u.record)}</span> <span class="faint">(${esc(u.via)})</span>`).join("; ") || "none"}</p>`);
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
    if (!confirm("Delete the demo's season file, both client files and every record on them from your account?")) return;
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
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the season file, both client files and every record on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try a question of your own — “Ask” in the sidebar.", true);
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

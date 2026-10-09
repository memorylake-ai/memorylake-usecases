/* Meeting memory across tools — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const avatar = (key) => `<span class="avatar p-${key}">${initials(S.data.people[key].display)}</span>`;
  const tool = (name) => name ? `<span class="tool t-${String(name).toLowerCase()}">${esc(name)}</span>` : "";
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
  // Highlight the two cutover dates wherever they appear.
  const hl = (s) => esc(s).replace(/\b(October 1[4]|October 21|2026-10-14|2026-10-21)\b/g, "<mark>$1</mark>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "People & project", sub: "tagged by team", view: "setup" },
    { n: 3, title: "Meetings", sub: "Otter, Granola, Slack", view: "meetings" },
    { n: 4, title: "Exported notes", sub: "Notion, Fathom · .docx", view: "sources" },
    { n: 5, title: "Decision chain", sub: "what changed, and when", view: "chain" },
    { n: 6, title: "Who owes what", sub: "action items by owner", view: "actions" },
    { n: 7, title: "One query", sub: "every tool at once", view: "answers" },
    { n: 8, title: "Forget a meeting", sub: "delete it for good", view: "forget" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "uploaded", "imported", "documents",
    "meeting", "turn", "pinned", "cooked", "closed", "facts", "actions", "answer", "brief", "forgot", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, project: null,
    files: {},                 // name -> {status}
    meetings: {},              // custom_id -> {id, stored, cooked, pinned}
    closed: {},                // action item id -> done text
    facts: null, chainKind: "none",
    actions: null,
    brief: null, answers: [],
    forgot: null,
    ask: { q: "", html: "" },
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.files = {}; S.meetings = {}; S.closed = {}; S.facts = null; S.chainKind = "none";
    S.actions = null; S.brief = null; S.answers = []; S.forgot = null;
  }

  // ------------------------------------------------------------------ rendering: steps
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `
      <button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span>
        <span class="t">${s.title}<small>${s.sub}</small></span>
      </button>`).join("") + `
      <button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask">
        <span class="n">?</span><span class="t">Ask the memory<small>free-form search</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }


  // ------------------------------------------------------------------ rendering: stage
  function renderStage() {
    const stage = $("#stage");
    const r = { connect, setup, meetings, sources, chain, actions, answers, forget, ask }[S.view];
    stage.innerHTML = r ? r() : "";
    bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card">
        <div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px">
          <dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Project</dt><dd>${S.project ? `${esc(S.project.name)} <span class="mono muted">${esc(S.project.id)}</span>` : '<span class="muted">not created yet</span>'}</dd>
        </dl>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.project ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>mlu-mmt-</code></span>
        </div>
      </div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required>
          <button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url">
          <option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option>
          <option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option>
        </select>
        <label for="workspace">Workspace id <span class="faint">(optional — defaults to your default workspace)</span></label>
        <input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px">
          <button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code> next to the demo.</span>
        </div>
      </form>`;
    return `
      <h1>Give teams one meeting memory that works across every tool</h1>
      <p class="lead">A warehouse cutover is discussed in an Otter-recorded ops sync, a Granola check-in, a Slack thread, a Notion runbook and a Fathom call summary.
      All of it goes into one MemoryLake project. Then: what was decided and what it replaced, who owes what (with where each item came from),
      one search across every tool — and a confidential call deleted for good.
      This page drives the <code>memorylake</code> CLI for real — watch the terminal at the bottom.</p>
      <div class="connect">
        ${form}
        <div class="card">
          <h2 style="margin-top:0">Before you start</h2>
          <ol class="steps-list">
            <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one and copy it. A free personal account is enough.</li>
            <li><b>The CLI is already here</b> — this server found it on PATH, otherwise it would not have started.</li>
            <li><b>Paste the key and connect.</b> Then run the demo; re-running is safe, and <b>Clean up</b> removes everything it created.</li>
          </ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/meeting-memory-across-tools" target="_blank" rel="noopener">memorylake.ai › meeting memory across tools</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p>
        </div>
      </div>`;
  }

  function personCard(key) {
    const p = S.data.people[key]; const a = S.actors[key];
    const onTeam = p.tags.split(",").includes(S.data.team_tag);
    return `<div class="card person ${onTeam ? "" : "out"}">${avatar(key)}
      <div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.role)}</div>
        <div style="margin-top:4px">${p.tags.split(",").map((t) => `<span class="tagchip ${t === S.data.team_tag ? "" : "off"}">${esc(t)}</span>`).join("")}</div></div>
      <span class="status pill ${a ? "ok" : ""}">${a ? "actor ready" : "pending"}</span></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const pr = S.data.project;
    return `
      <h1>Four people, tagged by team, and one project for the cutover</h1>
      <p class="lead">An <b>actor</b> is who a memory is attributed to. Each one gets <b>tags</b> (<code>actor create --tags riverside,team-ops</code>),
      so later <code>actor list --tags riverside</code> finds everyone on this project — Dev from finance is deliberately not tagged.
      The <b>project</b> holds the meetings, the exported notes and the facts drawn from them.</p>
      <h2>Halden Freight</h2>
      <div class="grid">${Object.keys(S.data.people).map(personCard).join("")}</div>
      <h2>Project</h2>
      <div class="card row spread"><div><b>${esc(pr.name)}</b><div class="muted small">custom id <code>${esc(pr.custom_id)}</code>${S.project ? ` · <span class="mono">${esc(S.project.id)}</span>` : ""}</div></div>
        <span class="pill ${S.project ? "ok" : ""}">${S.project ? (S.project.fresh ? "created" : "exists") : "pending"}</span></div>`;
  }

  function meetings() {
    if (!S.data) return "";
    return `
      <h1>Three meetings, three tools, one memory</h1>
      <p class="lead">Each meeting is a <code>GROUP</code> conversation with <code>--metadata source=otter|granola|slack</code>; every message is sent by the actor who said it,
      time-stamped to the meeting. Meetings go in one at a time and each finishes processing before the next — in order, the later meeting revises the earlier decision instead of losing it.
      Action items are pinned to their owners with <code>fact add --actor</code>, stating where they came from.</p>
      ${S.data.meetings.map((m) => {
        const st = S.meetings[m.custom_id] || {};
        const stored = st.stored || 0; const total = m.turns.length;
        const cook = st.cooked ? `<span class="pill ok">memory ready</span>`
          : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
          : stored ? `<span class="pill accent">${stored}/${total} messages stored</span>` : `<span class="pill">queued</span>`;
        const items = (m.action_items || []);
        const closes = (m.closes || []);
        return `<div class="card">
          <div class="call-head">${tool(m.tool)}<h3>${esc(m.name)}</h3><span class="muted small">${esc(m.kind)} · ${fmtDate(m.date)}</span>
            <span style="margin-left:auto">${cook}</span></div>
          <div class="turns">${m.turns.map(([k, text], i) => `
            <div class="turn ${i < stored ? "stored" : ""}">${avatar(k)}
              <div><div class="who">${esc(S.data.people[k].display)}</div><div class="what">${hl(text)}</div></div>
              <span class="mark">${i < stored ? "✓ stored" : "msg " + (i + 1)}</span></div>`).join("")}</div>
          ${items.length || closes.length ? `<div class="notes">
            ${items.map((it) => `<span class="note-chip ${st.pinned ? "" : "pending"}" title="${st.pinned ? "pinned to the owner with fact add --actor" : "pinned after the meeting"}">📌 ${esc(it.id)} → ${esc(S.data.people[it.owner].display)}: ${esc(it.task)} · due ${esc(it.due)}</span>`).join("")}
            ${closes.map((c) => `<span class="note-chip ${S.closed[c.id] ? "" : "pending"}" title="the open item is replaced with a done one after processing">✓ closes ${esc(c.id)}: ${esc(c.how)}</span>`).join("")}
          </div>` : ""}
        </div>`;
      }).join("")}`;
  }

  function sources() {
    if (!S.data) return "";
    const pill = (st) => st === "deleted" ? `<span class="pill">deleted in step 8</span>` : st === "okay" ? `<span class="pill ok">parsed & indexed</span>`
      : st === "imported" ? `<span class="pill ok">imported</span>`
      : st === "uploaded" ? `<span class="pill busy"><span class="spinner"></span> uploaded, parsing…</span>` : `<span class="pill">pending</span>`;
    return `
      <h1>Notes exported from Notion and Fathom</h1>
      <p class="lead">Not every meeting tool has an API you want to wire up; an export works too. Both files are Word documents:
      <code>lib upload</code>, then <code>proj doc import --wait</code> parses them into the same project as the meetings.
      The Fathom call summary is confidential — it gets deleted in the last step.</p>
      ${S.data.sources.map((f) => { const st = S.forgot?.gone && S.forgot.name === f.name ? "deleted" : S.files[f.name]?.status; return `<div class="card doc">
        <div class="row spread"><div class="row" style="gap:10px"><span class="kind k-word">Word</span>${tool(f.tool)}
          <a class="mono" href="/sources/${encodeURIComponent(f.name)}" target="_blank" rel="noopener">${esc(f.name)}</a><span class="faint small">${kb(f.size)}</span></div>
          ${pill(st)}</div>
        ${f.text ? `<pre>${hl(f.text)}</pre>` : ""}</div>`; }).join("")}`;
  }

  function chain() {
    if (!S.facts) return `<h1>The decision chain</h1><div class="empty">Run the demo first — the facts show up here once the meetings are processed.</div>`;
    const hi = S.facts.filter((f) => f.chain);
    const groups = {};
    S.facts.filter((f) => !f.chain).forEach((f) => { (groups[f.date || "undated"] ||= []).push(f); });
    const keys = Object.keys(groups).sort();
    const head = { merged: "Two meetings in two tools, chained — the new cutover date and the one it replaced, with both dates",
      dated: "Both cutover dates are in memory, each dated to the meeting that set it",
      latest: "This run, extraction kept only the latest cutover date — the earlier one is not in memory" }[S.chainKind];
    const card = hi.length ? `<div class="card revision"><div class="rev-head">${esc(head)}</div>
        ${hi.map((f) => `<div class="rev-fact">${hl(f.fact)}</div>`).join("")}
        ${S.chainKind === "latest" ? "" : `<div class="muted small" style="margin-top:8px">The September 22 decision (Otter) was not overwritten by the September 29 one (Granola): “what was the decision before today?” is still answerable.</div>`}</div>` : "";
    return `
      <h1>What was decided, and what it replaced</h1>
      <p class="lead">Nobody tagged anything: these facts were written by MemoryLake from the three meetings, each dated to the meeting it came from (<code>fact list --projects</code>).</p>
      <div class="counts"><div class="count"><b>${S.facts.length}</b><span>facts from the meetings</span></div>
        <div class="count" style="margin-left:auto"><button class="btn sm" data-act="refresh-facts">Refresh</button></div></div>
      ${card}
      <div class="timeline">${keys.map((d) => `<div class="tl-date">${d === "undated" ? "Undated" : fmtDate(d)}</div>
        ${groups[d].map((f) => `<div class="fact"><span>${hl(f.fact.replace(/\s*\(as of [0-9-]+\)\.?\s*$/, ""))}</span><span class="tag">extracted</span></div>`).join("")}`).join("")}</div>`;
  }

  function actions() {
    const a = S.actions;
    if (!a) return `<h1>Who owes what</h1><div class="empty">Run the demo first — this step lists everyone tagged <code>${esc(S.data?.team_tag || "")}</code> and their action items.</div>`;
    return `
      <h1>Who owes what — with where it came from</h1>
      <p class="lead"><code>actor list --tags ${esc(S.data.team_tag)}</code> returns the project team (${a.people.length} people${a.left_out.length ? `; ${esc(a.left_out.join(", "))} is not tagged, so not listed` : ""}),
      then <code>fact list --actors &lt;id&gt;</code> per person. Each item names its owner, its due date, the meeting and tool it was raised in — and, once closed, where.</p>
      <div class="counts"><div class="count"><b>${a.open}</b><span>open</span></div><div class="count"><b>${a.done}</b><span>done</span></div></div>
      ${a.people.map((p) => `<div class="card">
        <div class="row" style="gap:10px">${avatar(p.key)}<b>${esc(p.display)}</b>${p.tags.map((t) => `<span class="tagchip">${esc(t)}</span>`).join("")}</div>
        <div style="margin-top:8px">${p.items.length ? p.items.map((it) => `<div class="item"><span class="status ${it.status}">${it.status}</span><span>${hl(it.fact)}</span></div>`).join("") : '<p class="muted">No action items.</p>'}</div>
        ${p.other.length ? `<div class="muted small" style="margin-top:6px">Also remembered about ${esc(p.display.split(" ")[0])} (attribution is decided by the server): ${p.other.map(esc).join(" · ")}</div>` : ""}
      </div>`).join("")}`;
  }

  function docRow(d) {
    return `<div class="docres"><div class="row" style="gap:8px"><span class="kind k-${String(d.kind).toLowerCase()}">${esc(d.kind)}</span>${tool(d.tool)}<b class="mono">${esc(d.name)}</b></div>
      <span class="muted small">${esc(d.summary || "")}</span></div>`;
  }

  function answers() {
    const list = S.brief?.answers || S.answers;
    if (!list.length) return `<h1>One query, every tool</h1><div class="empty">Run the demo first — three searches run once the memory is ready.</div>`;
    return `
      <div class="brief-head"><div><h1>One query, every tool</h1>
        <p class="lead" style="margin:0">One <code>memorylake search</code> per question returns facts from the Otter, Granola and Slack meetings <em>and</em> the Notion or Fathom export that answers it.
        ${S.brief ? "" : '<span class="pill busy"><span class="spinner"></span> searching…</span>'}</p></div>
        ${S.brief ? `<button class="btn" data-act="download">Download brief .md</button>` : ""}</div>
      ${list.map((a) => `<div class="card answer"><h3>${esc(a.heading)}</h3>
        <div class="faint small mono">search "${esc(a.query)}"</div>
        ${a.facts.length ? `<ul>${a.facts.slice(0, 4).map((f) => `<li>${hl(f.fact)}</li>`).join("")}</ul>` : '<p class="muted">No facts matched.</p>'}
        ${a.documents.slice(0, 2).map(docRow).join("")}</div>`).join("")}`;
  }

  function forget() {
    const f = S.forgot;
    const list = (names, gone) => names.length ? names.map((n) => `<div class="docres"><b class="mono ${gone && n === f.name ? "gone" : ""}">${esc(n)}</b></div>`).join("") : '<p class="muted">No documents matched.</p>';
    return `
      <h1>Forget a meeting — for good</h1>
      <p class="lead">The Fathom summary of the vendor pricing call should never have been shared. <code>proj doc delete</code> removes the document,
      its indexed content and every memory derived from it — no confirmation prompt, no undo. The same search, before and after:</p>
      ${f ? `<div class="card"><div class="faint small mono">search "${esc(f.query)}" --types document</div>
        <div class="forget" style="margin-top:10px">
          <div><h3>Before</h3>${list(f.before, false)}</div>
          <div><h3>After <code>proj doc delete</code></h3>${list(f.after, false)}</div></div>
        <div class="row" style="margin-top:12px"><span class="pill ${f.gone ? "ok" : ""}">${f.gone ? `${esc(f.name)}: found before, gone after` : "not removed — see the terminal"}</span></div></div>
        <p class="muted small">Deleting a <em>conversation</em> (<code>conv delete</code>) removes its messages, but the facts already extracted from it stay in the project — see the README.</p>`
        : `<div class="empty">Run the demo first — this is the last step.</div>`}`;
  }

  function ask() {
    const sugg = [...(S.data?.questions || []).map((q) => q.query),
      "who owns the label printer firmware", "what is the rollback plan", "when do the handhelds arrive", "how many pickers need training"];
    return `
      <h1>Ask the meeting memory</h1>
      <p class="lead">The same <code>memorylake search</code> the answers come from, scoped to the cutover project. Try your own question.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. what did we decide last week?" value="${esc(S.ask.q)}" ${S.project ? "" : "disabled"}><button class="btn primary" ${S.project ? "" : "disabled"}>Search</button></form>
        <div class="chips">${sugg.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${S.project ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
  }

  // ------------------------------------------------------------------ stage behaviour
  function bindStage() {
    const stage = $("#stage");
    stage.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); }
      else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    stage.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    stage.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    stage.querySelector('[data-act="refresh-facts"]')?.addEventListener("click", async () => { const r = await api("/api/facts", {}); if (r) { S.facts = r.facts; renderStage(); } });
    stage.querySelector('[data-act="download"]')?.addEventListener("click", () => {
      const blob = new Blob([S.brief.markdown], { type: "text/markdown" }); const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "meeting-brief.md"; a.click(); URL.revokeObjectURL(a.href);
    });
    const form = stage.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      stage.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }

  async function askQuery(q) {
    if (!q) return;
    S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, top_k: 5 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `
      ${r.facts.length ? `<h2 style="margin-top:8px">Facts</h2>${r.facts.map((f) => `<div class="fact"><span>${hl(f.fact)}</span><span class="tag" title="score">${(f.score ?? 0).toFixed(2)}</span></div>`).join("")}` : '<p class="muted">No facts matched.</p>'}
      ${r.documents.length ? `<h2>Documents</h2>${r.documents.map(docRow).join("")}` : ""}`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }

  // ------------------------------------------------------------------ api + top bar
  async function api(path, body) {
    try {
      const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      const j = await res.json();
      if (!res.ok || j.error) { banner(j.error || `HTTP ${res.status}`); return null; }
      return j;
    } catch (e) { banner(String(e)); return null; }
  }

  function banner(msg, ok) {
    const b = $("#banner"); if (!msg) { b.hidden = true; return; }
    b.hidden = false; b.className = "banner" + (ok ? " ok" : ""); b.textContent = msg;
  }

  function setConn() {
    const st = S.st; const c = $("#conn");
    c.className = "conn " + (st.status === "running" ? "busy" : st.connected ? "on" : "");
    $("#conn-text").textContent = st.status === "running" ? `running ${st.job}…` : st.connected ? `${st.team?.name || "connected"} · ${st.workspace?.name || st.workspace?.id || ""}` : "Not connected";
    const busy = st.status === "running";
    $("#btn-run").disabled = !st.connected || busy;
    $("#btn-reset").disabled = !st.connected || busy;
    $("#btn-cleanup").disabled = !st.connected || busy;
  }

  async function runDemo(reset) {
    banner("");
    if (reset || !S.project) { resetRun(); S.stepStatus = { 1: "done" }; }
    S.follow = true;
    const r = await api("/api/run", { reset });
    if (r) { S.st = r; setConn(); }
  }

  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo project, its meetings and documents, the four demo people (with their action items) and the uploaded files from your account?")) return;
    banner("");
    const r = await api("/api/cleanup", {});
    if (r) { S.st = r; setConn(); }
  });

  // ------------------------------------------------------------------ terminal
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

  // ------------------------------------------------------------------ event stream
  function stepOf(index) { return STEPS.find((s) => s.n === index); }

  function onEvent(ev) {
    S.lastEvent = ev.id;
    const k = ev.kind, d = ev.data || {};
    if (k === "step") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.index) S.stepStatus[d.index] = "running";
      if (d.index === 2) resetRun();
      term("step", ev.text);
      if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    } else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "actor") S.actors[d.key] = d;
    else if (k === "project") S.project = { id: d.id, name: d.name, fresh: !!d.fresh };
    else if (k === "uploaded") S.files[d.name] = { status: "uploaded" };
    else if (k === "imported") Object.keys(S.files).forEach((n) => (S.files[n].status = "imported"));
    else if (k === "documents") (d.documents || []).forEach((x) => (S.files[x.name] = { status: x.status }));
    else if (k === "meeting") S.meetings[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false, pinned: (d.done || 0) > 0 };
    else if (k === "turn") (S.meetings[d.custom_id] ||= {}).stored = d.index;
    else if (k === "pinned") (S.meetings[d.custom_id] ||= {}).pinned = true;
    else if (k === "cooked") Object.values(S.meetings).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "closed") S.closed[d.id] = d.fact;
    else if (k === "facts") { S.facts = d.facts; S.chainKind = d.chain_kind || "none"; }
    else if (k === "actions") S.actions = d;
    else if (k === "answer") { if (!S.brief) S.answers.push(d); }
    else if (k === "brief") { S.brief = d; S.answers = d.answers; }
    else if (k === "forgot") S.forgot = d;
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.project = null; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Every step is ready to revisit, and you can ask the memory anything.", true);
      term("dim", `── ${d.job} finished`);
      S.st.status = "done"; setConn();
      if (S.caughtUp) refreshState();
    } else if (k === "error") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "error"; });
      term("error", ev.text); if (S.caughtUp) banner(ev.text);
      S.st.status = "error"; setConn(); refreshState();
    }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }

  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }

  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    ALL_KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  // ------------------------------------------------------------------ boot
  (async () => {
    renderSteps();
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data; if (state.project) S.project = state.project;
    const target = state.last_event_id;
    setConn();
    // Replay the whole event log silently, then render once.
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const handler = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      ALL_KINDS.forEach((kind) => es.addEventListener(kind, handler));
      es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : state.has_brief ? "answers" : "connect");
    subscribe();
  })();
})();

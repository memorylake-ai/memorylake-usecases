/* Customer onboarding memory that survives every handoff — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const side = (key) => S.data?.people[key]?.side === "customer" ? "analyst" : "pm";
  // "sales · 08-27" / "handoff note · kickoff" / "tool finding" → a coloured source tag
  const srcKey = (s) => s.startsWith("handoff") ? "note" : s.startsWith("tool") ? "tool" : s.startsWith("sales") ? "sales"
    : s.startsWith("kickoff") ? "kickoff" : s.startsWith("implementation") ? "impl" : "conv";
  const srcTag = (s) => `<span class="owner o-${srcKey(s)}">${esc(s)}</span>`;
  const stageTag = (s) => `<span class="owner o-${s === "implementation" ? "impl" : esc(s)}">${esc(s)}</span>`;
  const evTag = (e) => `<span class="ev ev-${esc(e)}">${esc(e)}</span>`;

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Customer & people", sub: "one project, six actors", view: "setup" },
    { n: 3, title: "Three stages", sub: "sales → kickoff → implementation", view: "stages" },
    { n: 4, title: "Discovery checklist", sub: "asked of memory first", view: "checklist" },
    { n: 5, title: "Tool findings", sub: "replayed, paired, promoted", view: "blockers" },
    { n: 6, title: "Timeline", sub: "from message metadata", view: "timeline" },
    { n: 7, title: "Training brief", sub: "for tomorrow's session", view: "brief" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "session", "turn", "cooked",
    "handoff", "memory", "question", "discovery", "probe", "calls", "promoted", "blockers", "timeline", "brief", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, projects: {},
    sessions: {},              // custom_id -> {id, stored, cooked, pinned}
    memory: null,              // {facts:[{fact, kind}], actor_facts:[…]}
    questions: [], discovery: null,
    probes: { before: {}, after: {} }, calls: null, promoted: null, blockers: null,
    timeline: null, brief: null,
    ask: { q: "", html: "" },
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.sessions = {}; S.memory = null; S.questions = []; S.discovery = null;
    S.probes = { before: {}, after: {} }; S.calls = null; S.promoted = null; S.blockers = null; S.timeline = null; S.brief = null;
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
        <span class="n">?</span><span class="t">Ask Corvane's memory<small>any question, before a call</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  function renderStage() {
    const r = { connect, setup, stages, checklist, blockers, timeline, brief, ask }[S.view];
    $("#stage").innerHTML = r ? r() : "";
    bindStage();
  }

  const hasProjects = () => Object.keys(S.projects).length > 0;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card">
        <div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px">
          <dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Project</dt><dd>${hasProjects() ? Object.values(S.projects).map((p) => `${esc(p.name)} <span class="mono muted">${esc(p.id)}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd>
        </dl>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${hasProjects() ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>mlu-com-</code></span>
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
      <h1>Customer onboarding memory that survives every handoff</h1>
      <p class="lead">Corvane HVAC Services bought Tallyfield in August. Since then they have talked to an account executive, a customer success manager and an AI onboarding assistant that ran three setup checks.
      Tomorrow a trainer meets them for the first time. Every stage lives in one customer memory: she asks it before she asks the customer, and sees the two problems the assistant's tools found that nobody passed on.
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
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/customer-onboarding-memory-for-saas-teams" target="_blank" rel="noopener">memorylake.ai › Customer onboarding memory for SaaS teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p>
        </div>
      </div>`;
  }

  function personCard(p, a) {
    return `<div class="card person"><span class="avatar ${p.side === "customer" ? "analyst" : "pm"}">${initials(p.display)}</span>
      <div><div class="name">${esc(p.display)} ${p.type === "ASSISTANT" ? '<span class="pill">ASSISTANT</span>' : ""}</div><div class="role">${esc(p.role)}</div></div>
      <span class="status pill ${a ? "ok" : ""}">${a ? "actor ready" : "pending"}</span></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const people = Object.values(S.data.people);
    const p = S.data.project, live = S.projects[S.data.customer.key];
    return `
      <h1>One project per customer, everyone who talked to them as an actor</h1>
      <p class="lead">The customer's whole onboarding — every conversation, every handoff note, every promoted tool finding — lives in one <b>project</b>.
      The people on both sides, and the onboarding assistant, are <b>actors</b>: some facts MemoryLake extracts land on them, so step 4 searches the project <i>and</i> Corvane's people.</p>
      <div class="card row spread"><div><b>${esc(p.name)}</b>
        <div class="muted small">${esc(p.description)} · custom id <code>${esc(p.custom_id)}</code>${live ? ` · <span class="mono">${esc(live.id)}</span>` : ""}</div></div>
        <span class="pill ${live ? "ok" : ""}">${live ? "ready" : "pending"}</span></div>
      <h2>Corvane HVAC Services</h2>
      <div class="grid">${people.filter((x) => x.side === "customer").map((x) => personCard(x, S.actors[x.key])).join("")}</div>
      <h2>Tallyfield</h2>
      <div class="grid">${people.filter((x) => x.side !== "customer").map((x) => personCard(x, S.actors[x.key])).join("")}
        <div class="card person"><span class="avatar pm">${initials(S.data.trainer.display)}</span><div><div class="name">${esc(S.data.trainer.display)}</div><div class="role">${esc(S.data.trainer.role)} — meets Corvane tomorrow</div></div><span class="status pill accent">reads only</span></div></div>`;
  }

  function toolBlock(turn) {
    if (turn.tool_use) {
      const tu = turn.tool_use;
      return `<div class="toolblk use"><span class="tb-kind">TOOL_USE</span><span class="mono">${esc(tu.name)}(${esc(Object.entries(tu.arguments).map(([k, v]) => `${k}=${v}`).join(", "))})</span><span class="mono faint">${esc(tu.id)}</span></div>`;
    }
    const tr = turn.tool_result, r = tr.result;
    return `<div class="toolblk res ${r.status === "ok" ? "ok" : "bad"}"><span class="tb-kind">TOOL_RESULT</span><span class="mono faint">${esc(tr.id)}</span>
      <div class="tb-body"><b>${esc(r.status)}</b> — ${esc(r.finding)}${r.fix ? `<div class="faint small">fix: ${esc(r.fix)}</div>` : ""}</div></div>`;
  }

  function sessionCard(c) {
    const st = S.sessions[c.custom_id] || {};
    const stored = st.stored || 0; const total = c.turns.length;
    const cook = st.cooked ? `<span class="pill ok">memory ready</span>`
      : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
      : stored ? `<span class="pill accent">${stored}/${total} messages stored</span>` : `<span class="pill">queued</span>`;
    return `<div class="card">
      <div class="call-head">${stageTag(c.stage)}<h3>${esc(c.stage_label)}</h3><span class="muted small">${fmtDate(c.date)} · ${esc(c.source)}</span>
        <span style="margin-left:auto">${cook}</span></div>
      <div class="meta"><span class="pill">customer=corvane</span><span class="pill">stage=${esc(c.stage)}</span><span class="pill">source=${esc(c.source)}</span></div>
      <div class="turns">${c.turns.map((t, i) => {
        const p = S.data.people[t.speaker];
        return `<div class="turn ${i < stored ? "stored" : ""}"><span class="avatar ${side(t.speaker)}">${initials(p.display)}</span>
          <div><div class="who">${esc(p.display)} · ${esc(p.role)}</div>
          ${t.text ? `<div class="what">${esc(t.text)}</div>` : ""}
          ${t.tool_use || t.tool_result ? toolBlock(t) : ""}
          ${t.event ? `<div class="msgmeta">${evTag(t.event)}<span class="mono faint">--metadata event=${esc(t.event)}${t.topic ? ` topic=${esc(t.topic)}` : ""}</span></div>` : ""}</div>
          <span class="mark">${i < stored ? "✓ stored" : "msg " + (i + 1)}</span></div>`;
      }).join("")}</div>
      ${c.handoff.length ? `<div class="notes">${c.handoff.map((n) => `<span class="note-chip ${st.pinned ? "" : "pending"}">📌 ${esc(n)}</span>`).join("")}</div>
      <div class="muted small" style="margin-top:6px">↑ ${esc(S.data.people[c.owner].display)}'s handoff note, pinned with <code>fact add --project</code> — about half of what was said</div>` : ""}
    </div>`;
  }

  function factKind(k) { return k === "pinned" ? "pinned" : k === "promoted" ? "promoted" : "extracted"; }

  function stages() {
    if (!S.data) return "";
    const m = S.memory;
    return `
      <h1>Sales → kickoff → implementation: one memory</h1>
      <p class="lead">Each stage is a conversation in the customer's project, appended one stage at a time (the last message uses <code>--wait</code>, so the CLI polls until the facts are extracted).
      Messages that matter carry their own <code>--metadata event=…</code> — step 6 builds the timeline from it. In the implementation chat the assistant's tool calls go in as
      <b>TOOL_USE</b> / <b>TOOL_RESULT</b> blocks (<code>--content-json</code>), the way an agent framework hands them over.</p>
      ${S.data.sessions.map(sessionCard).join("")}
      <h2>What Corvane's memory holds</h2>
      ${m ? `<div class="row" style="margin-bottom:8px"><button class="btn sm" data-act="refresh-memory">Refresh</button>
          <span class="muted small">${m.facts.length} project facts · ${m.actor_facts.length} on the people's actors</span></div>
        <div class="card">${m.facts.map((f) => `<div class="fact ${factKind(f.kind)}"><span>${esc(f.fact)}</span><span class="tag">${factKind(f.kind)}</span></div>`).join("")}
        ${m.actor_facts.length ? `<div class="muted small" style="margin:12px 0 4px">held by the people's actors</div>${m.actor_facts.map((f) => `<div class="fact"><span>${esc(f)}</span><span class="tag">actor</span></div>`).join("")}` : ""}</div>`
        : `<div class="empty">Shows up once all three stages are processed.</div>`}`;
  }

  function hitList(hits) {
    return hits.length ? hits.map((h) => `<div class="hit">${srcTag(h.source)}<span>${esc(h.fact)}</span></div>`).join("") : '<p class="muted" style="margin:6px 0">Not in memory — ask the customer.</p>';
  }

  function checklist() {
    if (!S.data) return "";
    const cl = S.data.checklist, d = S.discovery;
    const byQ = Object.fromEntries(S.questions.map((q) => [q.q, q]));
    return `
      <h1>Training tomorrow: ask memory before you ask the customer</h1>
      <p class="lead">${esc(S.data.trainer.display)} has never spoken to Corvane. Her discovery checklist runs as one <code>memorylake search</code> per question, scoped to the customer's project plus Corvane's people.
      Each answer is tagged with where it came from — a handoff note, or a fact MemoryLake extracted from the sales or kickoff conversation (the <i>as of</i> date tells which).</p>
      ${d ? `<div class="counts card"><div class="count"><b>${d.answered} / ${d.total}</b><span>answered from memory</span></div>
        <div class="count"><b>${d.total - d.answered}</b><span>to re-ask</span></div>
        <div class="count"><b>${d.by_notes} / ${d.total}</b><span>covered by the handoff notes alone</span></div></div>` : ""}
      ${cl.questions.map((q) => { const r = byQ[q.q]; return `<div class="card qa"><div class="row spread"><h3 style="margin:0">${r ? (r.answered ? "✓" : "✗") : "·"} ${esc(q.q)}</h3>
        ${r ? `<span class="pill ${r.in_notes ? "" : "accent"}">${r.in_notes ? "in a handoff note" : "only in the conversations"}</span>` : `<span class="pill">pending</span>`}</div>
        ${r ? hitList(r.hits) : ""}</div>`; }).join("")}`;
  }

  function probeCard(p, when) {
    const r = S.probes[when][p.topic];
    if (!r) return `<div class="card"><b>${esc(p.title)}</b> <span class="pill">pending</span></div>`;
    return `<div class="card probe ${r.hits.length ? "found" : "missing"}"><div class="row spread"><b>${esc(p.title)}</b>
      <span class="pill ${r.hits.length ? "ok" : "err"}">${r.hits.length} hit(s) about the cause</span></div>
      ${r.hits.map((h) => `<div class="hit"><span class="owner o-tool">tool finding</span><span>${esc(h.fact)}</span></div>`).join("")}
      ${r.others.length ? `<div class="dropped">other hits, none about the cause: “${esc(r.others[0])}”</div>` : ""}</div>`;
  }

  function blockers() {
    if (!S.data) return "";
    const probes = S.data.probes;
    return `
      <h1>What the assistant's tools found — and nobody passed on</h1>
      <p class="lead">On Sep 22 the onboarding assistant checked Corvane's setup with three tool calls. Tool results are <b>stored</b> with the conversation but <b>not extracted</b> into facts,
      so a search finds only the symptoms. The demo replays the implementation chat (<code>conv list</code> by metadata, <code>conv msg list</code>), pairs each TOOL_USE with its TOOL_RESULT by
      <code>tool_call_id</code>, and promotes the ones that did not pass with <code>fact add</code>. Then the same searches again.</p>
      <h2>Before</h2><div class="cols">${probes.map((p) => probeCard(p, "before")).join("")}</div>
      <h2>Replayed tool calls</h2>
      ${S.calls ? `<div class="card">${S.calls.calls.map((c) => { const r = c.result || {}; return `<div class="call ${r.status === "ok" ? "ok" : "bad"}">
          <div class="mono"><span class="faint">${esc((c.when || "").slice(11, 16))}</span> <b>${esc(c.name)}</b>(${esc(Object.entries(c.arguments).map(([k, v]) => `${k}=${v}`).join(", "))}) <span class="faint">${esc(c.id)}</span></div>
          <div class="call-res">${r.status === "ok" ? "✓" : "✗"} <b>${esc(r.status)}</b> — ${esc(r.finding)}${r.fix ? `<div class="faint small">fix: ${esc(r.fix)}</div>` : ""}</div></div>`; }).join("")}</div>`
        : `<div class="empty">Run the demo first.</div>`}
      ${S.promoted ? `<h2>Promoted into Corvane's memory</h2><div class="card">${S.promoted.map((t) => `<div class="fact promoted"><span>${esc(t)}</span><span class="tag">promoted</span></div>`).join("")}</div>` : ""}
      <h2>After</h2><div class="cols">${probes.map((p) => probeCard(p, "after")).join("")}</div>
      ${S.blockers ? `<div class="isolation ok">${S.blockers.results.map((r) => `${esc(r.title)}: ${r.before} → ${r.after}`).join(" · ")} hit(s) about the cause</div>` : ""}`;
  }

  function timeline() {
    const rows = S.timeline;
    if (!rows) return `<h1>Onboarding timeline</h1><div class="empty">Run the demo first.</div>`;
    const days = {};
    rows.forEach((r) => (days[r.when.slice(0, 10)] ||= []).push(r));
    return `
      <h1>The onboarding timeline, from message metadata</h1>
      <p class="lead">${rows.length} events across all three conversations. Nothing here is a search: the demo lists each conversation's messages and keeps the ones whose own
      metadata says <code>event=…</code> — the tool results included — sorted by the message timestamp.</p>
      <div class="timeline">${Object.entries(days).map(([day, rs]) => `<div class="tl-date">${fmtDate(day)} ${stageTag(rs[0].stage)}</div>
        ${rs.map((r) => `<div class="tl-row">${evTag(r.event)}<div><div class="what">${esc(r.text)}</div><div class="faint small">${esc(r.who)}${r.topic ? ` · ${esc(r.topic)}` : ""}</div></div></div>`).join("")}`).join("")}</div>`;
  }

  function mdToHtml(md) {
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])_(.+?)_(?=$|[\s.,;:)])/g, "$1<i>$2</i>").replace(/`(.+?)`/g, "<code>$1</code>");
    const out = []; let list = false, table = false;
    md.split("\n").forEach((ln) => {
      if (ln.startsWith("|")) {
        if (/^\|[-| ]+\|$/.test(ln)) return;
        const cells = ln.split("|").slice(1, -1).map((c) => inline(c.trim()));
        if (!table) { out.push('<table class="tl-table"><tr>' + cells.map((c) => `<th>${c}</th>`).join("") + "</tr>"); table = true; }
        else out.push("<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>");
        return;
      }
      if (table) { out.push("</table>"); table = false; }
      if (ln.startsWith("- ")) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(ln.slice(2))}</li>`); return; }
      if (list) { out.push("</ul>"); list = false; }
      if (ln.startsWith("# ")) out.push(`<h2>${inline(ln.slice(2))}</h2>`);
      else if (ln.startsWith("## ")) out.push(`<h3>${inline(ln.slice(3))}</h3>`);
      else if (ln.trim()) out.push(`<p>${inline(ln)}</p>`);
    });
    if (list) out.push("</ul>"); if (table) out.push("</table>");
    return out.join("\n");
  }

  function brief() {
    const b = S.brief;
    if (!b) return `<h1>Training brief</h1><div class="empty">Run the demo first — the brief is written at the end.</div>`;
    return `
      <div class="brief-head"><div><h1>Training prep — Corvane HVAC Services</h1>
        <p class="lead" style="margin:0">What not to re-ask, the open blockers from the assistant's tool calls, and the timeline — all read back from Corvane's memory.</p></div>
        <button class="btn" data-act="download">Download brief .md</button></div>
      <div class="card brief">${mdToHtml(b.markdown)}</div>`;
  }

  function ask() {
    const sugg = ["go-live deadline", "how do they sign in", "technician import", "who is the champion", "how many dispatchers"];
    const ready = hasProjects();
    return `
      <h1>Ask Corvane's memory</h1>
      <p class="lead">The same scope as the checklist: the customer's project plus Corvane's people. Every hit is tagged with the stage it came from. No topic filter here — you see the raw top hits.</p>
      <div class="card"><form class="ask-form" id="ask-form">
          <input id="ask-q" placeholder="e.g. go-live deadline" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${sugg.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    stage.querySelector('[data-act="refresh-memory"]')?.addEventListener("click", async () => { const r = await api("/api/memory", {}); if (r) { S.memory = r; renderStage(); } });
    stage.querySelector('[data-act="download"]')?.addEventListener("click", () => {
      const blob = new Blob([S.brief.markdown], { type: "text/markdown" }); const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = "training-brief-corvane.md"; a.click(); URL.revokeObjectURL(a.href);
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
    S.ask.html = `<div class="faint small" style="margin:8px 0">searched: Corvane's project + Corvane's people · top ${r.hits.length}</div>` + hitList(r.hits);
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
    if (reset || !hasProjects()) { resetRun(); S.stepStatus = { 1: "done" }; }
    S.follow = true;
    const r = await api("/api/run", { reset });
    if (r) { S.st = r; setConn(); }
  }

  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the Corvane project (with its facts), the three conversations and the demo actors from your account?")) return;
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
    else if (k === "project") S.projects[d.key] = { id: d.id, name: d.name };
    else if (k === "session") S.sessions[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false, pinned: (d.done || 0) >= d.turns };
    else if (k === "turn") (S.sessions[d.custom_id] ||= {}).stored = d.index;
    else if (k === "cooked") Object.values(S.sessions).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "handoff") (S.sessions[d.custom_id] ||= {}).pinned = true;
    else if (k === "memory") S.memory = d;
    else if (k === "question") { if (S.questions.some((q) => q.q === d.q)) S.questions = []; S.questions.push(d); }
    else if (k === "discovery") S.discovery = d;
    else if (k === "probe") { if (d.when === "before" && S.probes.after[d.topic]) { S.probes = { before: {}, after: {} }; S.calls = S.promoted = S.blockers = null; } S.probes[d.when][d.topic] = d; }
    else if (k === "calls") S.calls = d;
    else if (k === "promoted") S.promoted = d.facts;
    else if (k === "blockers") S.blockers = d;
    else if (k === "timeline") S.timeline = d.rows;
    else if (k === "brief") S.brief = d;
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.projects = {}; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. The training brief is ready, and you can ask Corvane's memory anything.", true);
      term("dim", `── ${d.job} finished`);
      S.st.status = "done"; setConn();
      if (S.caughtUp) refreshState();
    } else if (k === "error") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "error"; });
      term("error", ev.text); if (S.caughtUp) banner(ev.text);
      S.st.status = "error"; setConn(); refreshState();
    }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "turn"].includes(k)) { if (S.view !== "ask") { renderSteps(); renderStage(); } else renderSteps(); }
    else if (S.caughtUp && k === "turn" && S.view === "stages") renderStage();
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
    S.st = state; S.data = data; if (state.projects) S.projects = { ...state.projects };
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
    show(state.status === "running" ? S.view : state.has_brief ? "brief" : "connect");
    subscribe();
  })();
})();

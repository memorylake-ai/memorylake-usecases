/* Reproduce an AI agent's bug by replaying its memory — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const fmtWhen = (iso) => iso ? iso.slice(0, 16).replace("T", " ") : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const short = (id) => String(id || "").replace("fact-", "").slice(0, 8);
  const evTag = (e) => `<span class="ev ev-${esc(e)}">${esc(e)}</span>`;

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Account & actors", sub: "one project, customer + agent", view: "setup" },
    { n: 3, title: "Four support chats", sub: "the tool call recorded", view: "chats" },
    { n: 4, title: "After the complaint", sub: "memory fixed by hand", view: "fixes" },
    { n: 5, title: "Reproduce today", sub: "same tool call, new answer", view: "today" },
    { n: 6, title: "Pin to the call", sub: "fact trace, dated by message", view: "pinned" },
    { n: 7, title: "Diff & audit", sub: "then vs now, every source", view: "diff" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "session", "turn", "cooked",
    "recorded", "actions", "memory", "fixes", "today", "pinned", "diff", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, projects: {},
    sessions: {},              // custom_id -> {id, stored, cooked, said:{index:text}, counts}
    recorded: null, memory: null, fixes: null, today: null, pinned: null, diff: null,
    ask: { q: "", html: "" },
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.sessions = {}; S.recorded = null; S.memory = null; S.fixes = null; resetReplay();
  }
  function resetReplay() { S.today = null; S.pinned = null; S.diff = null; }

  // ------------------------------------------------------------------ rendering: steps
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `
      <button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span>
        <span class="t">${s.title}<small>${s.sub}</small></span>
      </button>`).join("") + `
      <button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask">
        <span class="n">?</span><span class="t">Ask the agent's memory<small>today, same scope as its tool</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  function renderStage() {
    const r = { connect, setup, chats, fixes, today, pinned, diff, ask }[S.view];
    $("#stage").innerHTML = r ? r() : "";
    bindStage();
  }

  const hasProjects = () => Object.keys(S.projects).length > 0;
  const notYet = (what) => `<div class="empty">${what || "Run the demo first."}</div>`;

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
          ${hasProjects() ? `<button class="btn" data-act="replay" ${st.status === "running" ? "disabled" : ""}>Replay only (steps 5–7)</button>` : ""}
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>mlu-abr-</code></span>
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
      <h1>Reproduce an AI agent's bug by replaying its memory</h1>
      <p class="lead">On Oct 3 Quill, a support agent, shipped a replacement printer to an office that had closed the day before. Today an engineer picks up the ticket.
      Asking the agent again proves nothing: its memory has changed since. This demo stores the agent's tool call as it happened, then uses MemoryLake's fact history
      (<code>fact trace</code>, <code>conv fact-actions</code>) to put the memory back the way it was at that exact message — and checks the result against what the agent recorded.
      This page drives the <code>memorylake</code> CLI for real — watch the terminal at the bottom.</p>
      <div class="connect">
        ${form}
        <div class="card">
          <h2 style="margin-top:0">Before you start</h2>
          <ol class="steps-list">
            <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one and copy it. A free personal account is enough.</li>
            <li><b>The CLI is already here</b> — this server found it on PATH. It must be <b>v20261009 or newer</b> (the fact-history commands); connecting checks that.</li>
            <li><b>Paste the key and connect.</b> Then run the demo; re-running is safe, and <b>Clean up</b> removes everything it created.</li>
          </ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/reproducing-agent-bugs-through-memory-replay" target="_blank" rel="noopener">memorylake.ai › Reproducing agent bugs through memory replay</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p>
        </div>
      </div>`;
  }

  function personCard(p, a) {
    return `<div class="card person"><span class="avatar ${p.side === "customer" ? "analyst" : "pm"}">${initials(p.display)}</span>
      <div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.role)}</div></div>
      <span class="status pill ${a ? "ok" : ""}">${a ? "actor ready" : "pending"}</span></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const p = S.data.project, live = S.projects[S.data.account.key];
    return `
      <h1>One project for the account, the customer and the agent as actors</h1>
      <p class="lead">Quill keeps one memory per customer account: a <b>project</b>. Lena and Quill are <b>actors</b> in the conversations; MemoryLake puts some of what it extracts on Lena's actor,
      so the agent's <code>memory_search</code> tool — and the replay — read the project <i>and</i> Lena.</p>
      <div class="card row spread"><div><b>${esc(p.name)}</b>
        <div class="muted small">${esc(p.description)} · custom id <code>${esc(p.custom_id)}</code>${live ? ` · <span class="mono">${esc(live.id)}</span>` : ""}</div></div>
        <span class="pill ${live ? "ok" : ""}">${live ? "ready" : "pending"}</span></div>
      <div class="grid" style="margin-top:14px">${Object.values(S.data.people).map((x) => personCard(x, S.actors[x.key])).join("")}
        <div class="card person"><span class="avatar pm">${initials(S.data.engineer.display)}</span><div><div class="name">${esc(S.data.engineer.display)}</div><div class="role">${esc(S.data.engineer.role)} — picks up the ticket today</div></div><span class="status pill accent">reads only</span></div></div>`;
  }

  function factRows(facts, tagFn) {
    return facts.map((f) => { const [cls, tag] = tagFn ? tagFn(f) : ["", f.where || ""];
      return `<div class="fact ${cls}"><span class="fid">${short(f.id)}</span><span>${esc(f.fact)}</span>${tag ? `<span class="tag">${esc(tag)}</span>` : ""}</div>`; }).join("");
  }

  function sessionCard(c) {
    const st = S.sessions[c.custom_id] || {};
    const stored = st.stored || 0; const total = c.turns.length;
    const cook = st.cooked ? `<span class="pill ok">memory ready</span>`
      : stored >= total ? `<span class="pill busy"><span class="spinner"></span> updating memory…</span>`
      : stored ? `<span class="pill accent">${stored}/${total} messages stored</span>` : `<span class="pill">queued</span>`;
    const counts = st.counts ? Object.entries(st.counts).map(([e, n]) => `${evTag(e)} ×${n}`).join(" ") || '<span class="muted small">no change</span>' : "";
    return `<div class="card">
      <div class="call-head"><h3>${esc(c.label)}</h3><span class="muted small">${fmtDate(c.date)}</span><span style="margin-left:auto">${cook}</span></div>
      <div class="turns">${c.turns.map((t, i) => {
        const p = S.data.people[t.speaker];
        let body = "";
        if (t.tool_use) body = `<div class="toolblk use"><span class="tb-kind">TOOL_USE</span><span class="mono">${esc(t.tool_use.name)}("${esc(t.tool_use.arguments.query)}")</span><span class="mono faint">${esc(t.tool_use.id)}</span></div>`;
        else if (t.tool_result) body = `<div class="toolblk res ok"><span class="tb-kind">TOOL_RESULT</span><span class="mono faint">${esc(t.tool_result.id)}</span>
          <div class="tb-body">${S.recorded ? `${S.recorded.length} fact(s), stored verbatim:${factRows(S.recorded, () => ["", ""])}` : '<span class="muted">the agent\'s search runs here, against the memory as it is at this point</span>'}</div></div>`;
        else body = `<div class="what">${esc((st.said || {})[i + 1] || t.text.replace("{address}", "the address on file"))}</div>`;
        return `<div class="turn ${i < stored ? "stored" : ""}"><span class="avatar ${p.side === "customer" ? "analyst" : "pm"}">${initials(p.display)}</span>
          <div><div class="who">${esc(p.display)} · ${esc(p.role)}</div>${body}</div>
          <span class="mark">${i < stored ? "✓ stored" : "msg " + (i + 1)}</span></div>`;
      }).join("")}</div>
      ${counts ? `<div class="msgmeta" style="margin-top:10px"><span class="muted small">conv fact-actions:</span> ${counts}</div>` : ""}
    </div>`;
  }

  function chats() {
    if (!S.data) return "";
    const m = S.memory;
    return `
      <h1>Four support chats, one at a time</h1>
      <p class="lead">Each chat is a conversation in the account's project, appended one at a time (the last message uses <code>--wait</code>) so each one sees the memory the one before left behind.
      On Oct 3 the agent's tool call goes in as <b>TOOL_USE</b> / <b>TOOL_RESULT</b> blocks: the search really runs at that point, and its result — fact ids and text — is stored verbatim.
      After each chat, <code>conv fact-actions</code> says what it did to the memory.</p>
      ${S.data.sessions.map(sessionCard).join("")}
      <h2>What the account's memory holds now</h2>
      ${m ? `<div class="row" style="margin-bottom:8px"><button class="btn sm" data-act="refresh-memory">Refresh</button><span class="muted small">${m.facts.length} live facts · project + Lena</span></div>
        <div class="card">${factRows(m.facts)}</div>` : notYet("Shows up once all four chats are processed.")}`;
  }

  function fixes() {
    const f = S.fixes;
    return `
      <h1>Today, after the complaint: memory corrected by hand</h1>
      <p class="lead">The support lead removes every fact that still tells the agent to ship to Lyon (<code>fact delete</code>), and finance's switch to monthly billing goes in with <code>fact update</code>.
      Both are <b>MANUAL</b> changes: they show up in each fact's history next to the ones extracted from conversations.</p>
      ${f ? `<h2>Forgotten</h2><div class="card">${f.forgot.length ? factRows(f.forgot, () => ["gone", "fact delete"]) : '<span class="muted">nothing left to forget</span>'}</div>
        <h2>Rewritten</h2><div class="card">${f.updated.length ? f.updated.map((u) => `<div class="drow"><div class="dh"><span class="fid">${short(u.id)}</span>${evTag("UPDATE")} ${evTag("MANUAL")}</div>
          <div class="was">${esc(u.old)}</div><div class="now">${esc(u.new)}</div></div>`).join("") : '<span class="muted">nothing to rewrite</span>'}</div>` : notYet()}`;
  }

  function addrBoxes(thenLbl, thenVal, nowLbl, nowVal) {
    return `<div class="addr"><div class="box then"><div class="lbl">${thenLbl}</div><div class="val">${esc(thenVal || "—")}</div></div>
      <div class="box now"><div class="lbl">${nowLbl}</div><div class="val">${esc(nowVal || "—")}</div></div></div>`;
  }

  function today() {
    const t = S.today;
    if (!t) return `<h1>Reproduce it today</h1>${notYet()}`;
    const seen = new Set((S.recorded || []).map((f) => f.id));
    return `
      <h1>Reproduce it today: the agent's own tool call, re-run</h1>
      <p class="lead">The engineer reads the TOOL_USE block back from the Oct 3 conversation (<code>conv msg list</code>) and runs the very same search against today's memory.</p>
      ${addrBoxes("Oct 3 — recorded in the TOOL_RESULT, the agent shipped to", t.then_address, "today — the same call would ship to", t.now_address)}
      <div class="counts card"><div class="count"><b>${t.unchanged}</b><span>of the ${t.total} facts it saw unchanged</span></div>
        <div class="count"><b>${t.changed.length}</b><span>rewritten since</span></div><div class="count"><b>${t.gone.length}</b><span>forgotten since</span></div></div>
      <div class="isolation ${t.then_address !== t.now_address ? "bad" : "ok"}">${t.then_address !== t.now_address ? "Asking the agent again does not reproduce the bug: its memory has moved on." : "Today's answer is the same as then."}</div>
      <div class="cols" style="margin-top:14px">
        <div class="col"><h2>What it saw on Oct 3</h2><div class="card">${factRows(S.recorded || [], (f) => t.gone.includes(f.id) ? ["gone", "forgotten since"] : t.changed.includes(f.id) ? ["changed", "rewritten since"] : ["", ""])}</div></div>
        <div class="col"><h2>What the same call returns today</h2><div class="card">${factRows(t.today, (f) => seen.has(f.id) ? ["", "also then"] : ["match", "new"])}</div></div>
      </div>`;
  }

  function pinned() {
    const p = S.pinned;
    if (!p) return `<h1>Pin memory to the call</h1>${notYet()}`;
    const used = p.used;
    return `
      <h1>Pin memory to the moment of the call</h1>
      <p class="lead">Every fact the account ever had (forgotten ones included — each conversation reports the facts it touched) goes through <code>fact trace</code>: its full history of ADD / UPDATE / FORGET.
      Each history entry is dated by the <b>messages it was extracted from</b> (<code>source_entry_ids</code> → <code>conv msg list</code>), not by when the server processed it.
      Keeping each fact's last change at or before <b>${esc(fmtWhen(p.at))}</b> — the TOOL_USE message — gives the memory the agent searched.</p>
      ${addrBoxes(`address on file at ${esc(fmtWhen(p.at))} (replayed)`, p.address, "replayed facts identical to what the agent recorded", `${p.identical} / ${p.checks.length}`)}
      <h2>Replayed vs recorded</h2>
      <div class="card">${p.checks.map((c) => `<div class="fact ${c.same ? "match" : "mismatch"}"><span class="fid">${short(c.id)}</span><span>${esc(c.replayed || "(not in the replayed memory)")}</span><span class="tag">${c.same ? "✓ identical" : "✗ differs"}</span></div>`).join("")}</div>
      ${used ? `<h2>The fact the agent shipped by — <span class="mono">${short(used.id)}</span>, full history</h2>
        <div class="card trace">${used.entries.map((e) => `<div class="te"><span class="when">${esc(fmtWhen(e.at))}</span>
          <div><div class="dh">${evTag(e.event)} ${e.source_kind === "MANUAL" ? evTag("MANUAL") : `<span class="muted small">${esc(e.source)}</span>`} <span class="mono faint small">${esc(e.source_event_id || "")}</span></div>
          <div>${esc(e.new_fact || "(forgotten)")}</div></div></div>`).join("")}
          <div class="te"><span class="when">now</span><div><b>${esc(used.expired ? "forgotten" : used.current)}</b></div></div></div>` : ""}
      <h2>The whole memory at ${esc(fmtWhen(p.at))}: ${p.then.length} facts</h2>
      <div class="card">${factRows([...p.then].sort((a, b) => a.since.localeCompare(b.since)), (f) => ["", f.source])}</div>`;
  }

  function diff() {
    const d = S.diff;
    if (!d) return `<h1>Memory diff</h1>${notYet()}`;
    return `
      <div class="brief-head"><div><h1>Memory diff: then vs now, and the audit record</h1>
        <p class="lead" style="margin:0">Every change after the tool call, oldest first, each with its source: a conversation and message (with the <code>cookrun-…</code> that extracted it), or a manual API call.</p></div>
        <button class="btn" data-act="download">Download ${esc(d.path.split("/").pop())}</button></div>
      ${d.rows.map((r) => `<div class="drow"><div class="dh">${evTag(r.event)}${r.kind === "MANUAL" ? evTag("MANUAL") : ""}<span class="fid">${short(r.id)}</span>
          <span class="mono small">${esc(fmtWhen(r.at))}${r.dated_by === "message" ? "" : " (API call time)"}</span><span class="muted small">← ${esc(r.source)}</span>
          ${r.cookrun ? `<span class="mono faint small">${esc(r.cookrun)}</span>` : ""}</div>
        ${r.event === "UPDATE" ? `<div class="was">${esc(r.old)}</div><div class="now">${esc(r.new)}</div>` : r.event === "ADD" ? `<div class="now">${esc(r.new)}</div>` : `<div class="was">${esc(r.old || "")}</div>`}</div>`).join("")}
      <div class="verdict">${esc(d.verdict)}</div>
      <p class="muted small">Audit record written to <code>${esc(d.path)}</code>. The replay is read-only: nothing was written back to MemoryLake.</p>`;
  }

  function ask() {
    const sugg = ["where to ship replacement hardware", "is the Lyon office still open", "billing plan", "printer error E-41"];
    const ready = hasProjects();
    return `
      <h1>Ask the agent's memory</h1>
      <p class="lead">The scope the agent's <code>memory_search</code> tool uses — the account's project plus Lena — as it is <b>today</b>. Raw top hits, no filtering.</p>
      <div class="card"><form class="ask-form" id="ask-form">
          <input id="ask-q" placeholder="e.g. where to ship replacement hardware" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${sugg.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : notYet()}</div></div>`;
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
    stage.querySelector('[data-act="replay"]')?.addEventListener("click", () => runReplay());
    stage.querySelector('[data-act="refresh-memory"]')?.addEventListener("click", async () => { const r = await api("/api/memory", {}); if (r) { S.memory = r; renderStage(); } });
    stage.querySelector('[data-act="download"]')?.addEventListener("click", () => {
      const blob = new Blob([JSON.stringify(S.diff.record, null, 2) + "\n"], { type: "application/json" }); const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = S.diff.path.split("/").pop(); a.click(); URL.revokeObjectURL(a.href);
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
    const r = await api("/api/search", { query: q, top_k: 6 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="faint small" style="margin:8px 0">searched: the account's project + Lena · top ${r.hits.length}</div>` + factRows(r.hits, () => ["", ""]);
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

  async function runReplay() {
    banner(""); resetReplay(); S.follow = true;
    const r = await api("/api/replay", {});
    if (r) { S.st = r; setConn(); }
  }

  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the Brightmoor project (with its facts), the four conversations and the demo actors from your account?")) return;
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
      if (d.index === 5) resetReplay();
      term("step", ev.text);
      if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    } else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "actor") S.actors[d.key] = d;
    else if (k === "project") S.projects[d.key] = { id: d.id, name: d.name };
    else if (k === "session") S.sessions[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false, said: {} };
    else if (k === "turn") { const s = (S.sessions[d.custom_id] ||= { said: {} }); s.stored = d.index; if (d.said) (s.said ||= {})[d.index] = d.said; }
    else if (k === "cooked") Object.values(S.sessions).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "recorded") S.recorded = d.facts;
    else if (k === "actions") (S.sessions[d.custom_id] ||= {}).counts = d.counts;
    else if (k === "memory") S.memory = d;
    else if (k === "fixes") S.fixes = d;
    else if (k === "today") S.today = d;
    else if (k === "pinned") { S.pinned = d; if (!S.recorded) S.recorded = d.checks.map((c) => ({ id: c.id, fact: c.recorded })); }
    else if (k === "diff") S.diff = d;
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.projects = {}; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. The replay is written down; ask the agent's memory anything.", true);
      term("dim", `── ${d.job} finished`);
      S.st.status = "done"; setConn();
      if (S.caughtUp) refreshState();
    } else if (k === "error") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "error"; });
      term("error", ev.text); if (S.caughtUp) banner(ev.text);
      S.st.status = "error"; setConn(); refreshState();
    }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "turn"].includes(k)) { if (S.view !== "ask") { renderSteps(); renderStage(); } else renderSteps(); }
    else if (S.caughtUp && k === "turn" && S.view === "chats") renderStage();
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
    show(state.status === "running" ? S.view : state.has_brief ? "diff" : "connect");
    subscribe();
  })();
})();

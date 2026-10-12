/* Escalation pattern memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const initials = (n) => n.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two agents, one instruction", view: "setup" },
    { n: 3, title: "The handoff", sub: "same words, both agents", view: "handoff" },
    { n: 4, title: "Three memories", sub: "Relay · default · project", view: "memories" },
    { n: 5, title: "Provenance", sub: "lesson → message → speaker", view: "provenance" },
    { n: 6, title: "Maya leaves", sub: "actor sealed", view: "departure" },
    { n: 7, title: "First escalation", sub: "Tomás asks Relay", view: "escalation" },
    { n: 8, title: "The outcome", sub: "a new lesson", view: "outcome" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true,
    stepStatus: {}, setup: null, convs: {}, memories: null, provenance: null, departure: null, escalation: null, outcome: null,
    ask: { q: "", scope: "relay", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const nameOf = (key) => key === "relay" ? "Relay" : (story()?.people || []).find((p) => p.key === key)?.display || key;
  const labelOf = (key) => [...(story()?.patterns || []), story()?.outcome.pattern].find((p) => p && p.key === key)?.label || key;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>Relay vs default agent</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, handoff, memories, provenance, departure, escalation, outcome, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const leakLine = (r) => r.leaks?.length ? `<div class="leakwhy">↳ ${esc(r.leaks.join(", "))}</div>` : "";
  function fact(r, judge) {
    const bad = r.leaks?.length;
    const cls = judge ? (bad ? "personal" : "clean") : "";
    const mk = judge ? (bad ? "✗" : "✓") : "•";
    return `<div class="memfact ${cls}"><span class="mk">${mk}</span><span class="body">${esc(r.fact)}${leakLine(r)}</span></div>`;
  }
  const tally = (rows, label) => {
    if (!rows.length) return `${label}: no facts`;
    const bad = rows.filter((r) => r.leaks?.length).length;
    return `${label}: ${rows.length} fact(s) · ${bad} name a customer or carry a figure`;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Relay</dt><dd>${S.st.ids?.relay ? `<span class="mono muted">${esc(S.st.ids.relay)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Escalation know-how that stays when the senior agent leaves.</h1>
      <p class="lead"><b>Maya</b>, Quayside's senior support agent, talks her escalation notes through with <b>Relay</b>, the team's escalation agent.
      Relay has a <b>fact instruction of its own</b> — <code>fact instruction set --agent</code> — that records escalation paths, context packages and framing, even when a teammate says them,
      and never a customer, a contact, an id or a figure. A second agent on the built-in default hears the same words. Maya then leaves; Tomás, three weeks in, routes his first escalation from Relay's memory.
      This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the agents' memories, the agents, the project, the conversations and both people.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/escalation-pattern-memory-for-support-teams" target="_blank" rel="noopener">memorylake.ai › escalation pattern memory for support teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!story()) return "";
    const su = S.setup;
    const people = story().people.map((p) => `<div class="card person"><span class="avatar user">${initials(p.display)}</span><div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.role)}</div></div>
        <span class="status pill ${su ? "ok" : ""}">${su ? "actor ready" : "pending"}</span></div>`).join("");
    const agent = (key) => {
      const a = story().agents.find((x) => x.key === key); const live = su?.agents.find((x) => x.key === key);
      return `<div class="card ${a.instruction ? "mine" : ""}"><h2 style="margin-top:0">${esc(a.name)}</h2>
        <div class="muted small">custom id <code>${esc(S.data.prefix)}-${esc(a.custom_id)}</code>${live ? ` · <span class="mono">${esc(live.id)}</span> · actor <span class="mono">${esc(live.actor_id)}</span>` : ""}</div>
        <div class="mono small faint" style="margin-top:10px">$ memorylake fact instruction ${a.instruction ? "set --agent … --file data/relay-instruction.md" : "get --agent …"}</div>
        ${a.instruction ? `<pre class="instr">${esc(su?.instruction || S.data.instruction)}</pre>${su ? '<p class="muted small" style="margin:0">Read back with <code>fact instruction get --agent</code>: saved word for word.</p>' : ""}`
          : `<pre class="instr">{ "fact_instruction": "" }</pre><p class="muted small" style="margin:0">Empty → MemoryLake's built-in default. Here only for comparison.</p>`}</div>`;
    };
    return `<h1>Two people, one project, two agents — one instruction</h1>
      <p class="lead">Every agent comes with an actor of its own: its memory is listed with <code>fact list --agents</code> and searched with <code>search --actors &lt;its actor&gt;</code>.
      Only Relay gets a fact instruction. It steers what extraction records into <em>Relay's</em> memory, and nothing else.</p>
      <div class="grid">${people}</div>
      <div class="cols" style="margin-top:16px">${agent("relay")}${agent("default")}</div>`;
  }

  function transcript(where) {
    const c = S.convs[where] || {}; const turns = where === "outcome" ? story().outcome.turns : story().handoff.turns; const stored = c.stored || 0;
    const status = c.cooked ? `<span class="pill ok">memory ready</span>` : stored >= turns.length ? `<span class="pill busy"><span class="spinner"></span> extracting…</span>`
      : stored ? `<span class="pill accent">${stored}/${turns.length}</span>` : `<span class="pill">queued</span>`;
    const title = where === "outcome" ? "Tomás ↔ Relay" : where === "default" ? "Maya ↔ Relay (built-in default)" : where === "relay-again" ? "Maya ↔ Relay (told again)" : "Maya ↔ Relay";
    return `<div class="card session"><div class="session-head"><span class="chan">${esc(title)}</span><span style="margin-left:auto">${status}</span></div>
      <div class="bubbles">${turns.map(([who, line], i) => `<div class="bubble ${who === "relay" ? "bot" : "user"} ${i < stored ? "stored" : ""}"><span class="speaker">${esc(nameOf(who))}</span>${esc(line.replace(/^[^:]+:\s*/, ""))}${i < stored ? '<span class="tick">✓</span>' : ""}</div>`).join("")}</div></div>`;
  }
  function handoff() {
    if (!story()) return "";
    return `<h1>Maya's handoff — the same words, told to both agents</h1>
      <p class="lead">One <code>DIRECT</code> conversation per agent, between Maya's actor and the agent's own actor, in the support project. Her notes name customers, a contact,
      ticket and account numbers and a contract value — the way real notes do.</p>
      ${S.convs["relay-again"] ? `<div class="card"><span class="pill warn">told again</span> <span class="muted small">Relay recorded nothing from the first telling, so the handoff was told once more in a new conversation.</span></div>` : ""}
      <div class="cols">${transcript("relay")}${transcript("default")}</div>`;
  }

  function memories() {
    const m = S.memories; if (!m) return empty("Same words, three memories");
    const cov = m.coverage, pats = story().patterns;
    const row = (p) => `<tr><td>${esc(p.label)}</td>${["relay", "default", "project"].map((k) => `<td style="text-align:center">${cov[k].includes(p.key) ? "✓" : "·"}</td>`).join("")}</tr>`;
    return `<h1>Same words, three memories</h1>
      <p class="lead">Relay kept Maya's lessons and dropped the customers. The default agent, on the same words, kept little or nothing of what Maya said.
      The project the conversations live in kept the lessons <em>and</em> the customers. The <b>leak scan</b> checks every fact against the customers' identifiers and flags any figure.</p>
      <div class="grid3">
        <div class="card mine"><h2 style="margin-top:0">Relay · its instruction</h2>${m.relay.map((r) => fact(r, true)).join("") || '<p class="muted">nothing extracted</p>'}
          <p class="tally"><span class="pill ${m.leaky.relay ? "err" : m.relay.length ? "ok" : "warn"}">${esc(tally(m.relay, "Relay"))}</span></p>
          ${m.removed.length ? `<p class="muted small">removed ${m.removed.length} flagged fact(s): the instruction steers extraction, it does not guarantee it</p>` : ""}</div>
        <div class="card"><h2 style="margin-top:0">Default agent</h2>${m.default.map((r) => fact(r, false)).join("") || '<p class="muted">nothing</p>'}
          <p class="tally muted">${esc(tally(m.default, "default"))}</p></div>
        <div class="card"><h2 style="margin-top:0">Support project</h2>${m.project.map((r) => fact(r, false)).join("") || '<p class="muted">nothing</p>'}
          <p class="tally muted">${esc(tally(m.project, "project"))}</p></div></div>
      <div class="card"><h2 style="margin-top:0">Maya's ${cov.total} lessons, found in each memory</h2>
        <table class="iso"><thead><tr><th></th><th>Relay</th><th>default</th><th>project</th></tr></thead><tbody>${pats.map(row).join("")}
        <tr><th>total</th><th>${cov.relay.length}/${cov.total}</th><th>${cov.default.length}/${cov.total}</th><th>${cov.project.length}/${cov.total}</th></tr></tbody></table>
        <p class="muted small" style="margin-bottom:0">${m.maya ? `Also on Maya's own actor: ${m.maya.length} fact(s) (her role, that she is leaving).` : "Maya's own actor was sealed by an earlier run."}</p></div>`;
  }

  function provenance() {
    const p = S.provenance; if (!p) return empty("Provenance");
    return `<h1>Every lesson on Relay, traced to the message it came from</h1>
      <p class="lead"><code>fact trace --agent</code> gives each lesson's <code>source_entry_ids</code>; <code>conv msg list</code> turns them into messages and speakers
      (<code>actor_id</code> for people, <code>agent_id</code> for Relay). A source is the whole batch extraction read, so Relay's own questions show up beside Maya's answers.
      Written to <code>out/relay-provenance.md</code> and <code>.json</code>.</p>
      <div class="card">${p.rows.map((r) => `<div class="memfact clean"><span class="mk">✓</span><span class="body">${esc(r.fact)}
        <div class="trace">→ ${r.messages.length ? `${esc(r.conv.join("/"))} msg ${esc(r.messages.join(", "))}` : "added by hand"} · ${esc(r.speakers.join(", "))} · ${esc(r.kinds.join("/"))}</div></span></div>`).join("") || '<p class="muted">no lessons</p>'}</div>
      <p class="muted small">${p.rows.length} lesson(s) on Relay · ${p.from_maya} trace to a batch with Maya's messages in it.</p>`;
  }

  function departure() {
    const d = S.departure; if (!d) return empty("Maya leaves");
    const line = (r) => r.rc ? `HTTP ${r.status} ${esc(r.message)}` : "still answers";
    return `<h1>Maya leaves — her actor is sealed, Relay's memory stays</h1>
      <p class="lead"><code>actor unbind</code> seals her: not deleted, and reversible with <code>actor bind</code>. Her own memory can no longer be read or searched in this workspace.
      The lessons she taught live on Relay, so they are where they were.</p>
      <div class="cols">
        <div class="card"><h2 style="margin-top:0">Maya's own memory</h2>
          <div class="mono small faint">$ memorylake fact list --actors &lt;maya&gt;</div><p><span class="pill ${d.fact_list.rc ? "err" : "warn"}">${line(d.fact_list)}</span></p>
          <div class="mono small faint">$ memorylake search … --actors &lt;relay&gt;,&lt;maya&gt;</div><p><span class="pill ${d.search.rc ? "err" : "warn"}">${line(d.search)}</span></p></div>
        <div class="card mine"><h2 style="margin-top:0">Relay's memory</h2>
          <p><span class="pill ${d.after === d.before ? "ok" : "warn"}">${d.before} lesson(s) before · ${d.after} after${d.after === d.before ? " · unchanged" : ""}</span></p>
          <div class="mono small faint">$ memorylake search "where does a mobile app crash go" --actors &lt;relay&gt;</div>
          ${d.top.map((t, i) => `<div class="memfact"><span class="mk">${i + 1}</span><span class="body">${esc(t)}</span></div>`).join("")}</div></div>
      <p class="muted small">Deleting her actor would not remove her facts either: they stay readable and can no longer be deleted. That is why the demo's cleanup deletes facts first.</p>`;
  }

  function escalation() {
    const e = S.escalation; if (!e) return empty("Tomás's first escalation");
    return `<h1>Tomás's first escalation — routed from Relay's memory</h1>
      <div class="card"><div class="question"><b>Ticket:</b> ${esc(e.ticket)}</div></div>
      <p class="lead">Three questions, each a <code>search --actors &lt;Relay's actor&gt;</code>. Hits are filtered by topic (search always returns its closest facts) and checked for the lesson Maya taught.</p>
      ${e.answers.map((a, i) => `<div class="card"><div class="question"><b>Q:</b> ${esc(a.q)}</div>
        ${a.hits.map((h) => `<div class="memfact ${h.lessons.some((k) => a.expect.includes(k)) ? "clean" : ""}"><span class="mk">${h.lessons.some((k) => a.expect.includes(k)) ? "✓" : "·"}</span><span class="body">${esc(h.fact)}</span></div>`).join("") || '<p class="muted small">no lesson on this in Relay\'s memory</p>'}
        <p class="small" style="margin:6px 0 0">expected: <b>${esc(labelOf(a.expect[0]))}</b> → <span class="pill ${a.ok ? "ok" : "err"}">${a.ok ? "found" : "not found"}</span>
          <span class="muted"> · default agent: ${e.contrast[i].ok ? "found" : "not found"}</span>${a.left_out ? `<span class="muted"> · ${a.left_out} other hit(s) left out</span>` : ""}</p></div>`).join("")}
      <p><span class="pill ${e.ok === e.answers.length ? "ok" : "warn"}">Relay's memory answered ${e.ok} of ${e.answers.length}</span>
        <span class="pill">default agent: ${e.contrast.filter((c) => c.ok).length} of ${e.contrast.length}</span></p>`;
  }

  function outcome() {
    const o = S.outcome;
    if (!o) return S.convs.outcome ? `<h1>The outcome</h1>${transcript("outcome")}` : empty("The outcome");
    return `<h1>The outcome — Tomás's lesson joins Relay's memory, the customer does not</h1>
      <p class="lead">A <code>DIRECT</code> conversation between Tomás and Relay. The same instruction applies to whoever is teaching: the lesson is kept, Harbor Lane Plumbing and its contact are not.</p>
      <div class="cols">${transcript("outcome")}
        <div class="card mine"><h2 style="margin-top:0">${o.appended ? "What changed on Relay" : "Relay's lesson from the outcome"}</h2>
          ${o.new.map((r) => `${fact(r, true)}${r.change === "updated" ? '<p class="muted small" style="margin:0 0 6px 26px">revised a lesson Relay already had (same fact id)</p>' : ""}`).join("") || '<p class="muted">nothing changed this time</p>'}
          <p class="small">expected: <b>${esc(labelOf(story().outcome.pattern.key))}</b> → <span class="pill ${o.got ? "ok" : "warn"}">${o.got ? "on Relay" : "not recorded this time"}</span></p>
          ${o.removed.length ? `<p class="muted small">removed ${o.removed.length} flagged fact(s)</p>` : ""}
          <p class="muted small" style="margin-bottom:0">The support project, for contrast: ${o.project_leaky} fact(s) name a customer or carry a figure.</p></div></div>`;
  }

  function ask() {
    const ready = !!S.st.ids?.relay;
    const opt = (v, l) => `<button type="button" class="chip scope ${S.ask.scope === v ? "on" : ""}" data-scope="${v}">${l}</button>`;
    return `<h1>Ask</h1>
      <p class="lead">The same search against Relay's memory or the default agent's: <code>search --actors &lt;the agent's actor&gt;</code>. Every hit goes through the leak scan and the lesson check.</p>
      <div class="card"><div class="chips" style="margin:0 0 10px">${opt("relay", "Relay")}${opt("default", "Default agent")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. a data export is missing rows, who gets it" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Search</button></form>
        <div class="chips">${["a data export is missing rows, who gets it", "customer was double charged", "what do I attach for a photo upload failure", "how do I get the data team to pick it up fast", "which customer pays the most"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelectorAll(".chip.scope").forEach((b) => b.addEventListener("click", () => {
      S.ask.scope = b.dataset.scope; st.querySelectorAll(".chip.scope").forEach((x) => x.classList.toggle("on", x === b)); if (S.ask.q) askQuery(S.ask.q);
    }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip:not(.scope)").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, scope: S.ask.scope, top_k: 5 });
    if (!r) { box.innerHTML = ""; return; }
    const bad = r.facts.filter((f) => f.leaks.length).length;
    S.ask.html = `${r.facts.length ? r.facts.map((f) => `${fact(f, true)}${f.lessons.length ? `<div class="trace" style="margin:-4px 0 8px 26px">lesson: ${esc(f.lessons.map((l) => l.label).join(" · "))}</div>` : ""}`).join("") : '<p class="muted">No facts in this memory.</p>'}
      <p class="muted small">Search always returns its closest facts, related or not. ${r.scope === "relay" ? "Relay" : "Default agent"}: ${bad} of ${r.facts.length} name a customer or carry a figure.</p>`;
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
  function resetView() { S.setup = null; S.convs = {}; S.memories = null; S.provenance = null; S.departure = null; S.escalation = null; S.outcome = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete both agents' memories, both agents, the support project, the conversations and both people from your account?")) return;
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
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView(); term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "existing") S.st.ids = d;
    else if (k === "setup") {
      S.setup = d;
      S.st.ids = { project: d.project.id, ...Object.fromEntries(d.agents.map((a) => [a.key, a.id])), ...Object.fromEntries(d.agents.map((a) => [a.key + "_actor", a.actor_id])) };
    }
    else if (k === "handoff") S.convs[d.where] = { id: d.id, stored: d.done || 0, cooked: false };
    else if (k === "outcome_conv") S.convs.outcome = { id: d.id, stored: d.done || 0, cooked: false };
    else if (k === "turn") (S.convs[d.where] ||= {}).stored = d.index;
    else if (k === "cooked") Object.values(S.convs).forEach((s) => { if (s.id === d.id) s.cooked = true; });
    else if (k === "memories") S.memories = d;
    else if (k === "provenance") S.provenance = d;
    else if (k === "departure") S.departure = d;
    else if (k === "escalation") S.escalation = d;
    else if (k === "outcome") S.outcome = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the agents' memories, the agents, the project, the conversations and both people are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — the same question against Relay's memory and against the default agent's.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "setup", "handoff", "outcome_conv", "turn", "cooked",
    "memories", "provenance", "departure", "escalation", "outcome", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
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
      const h = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.memories ? "memories" : "connect");
    subscribe();
  })();
})();

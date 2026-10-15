/* Known issues memory for support teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, one actor per customer", view: "setup" },
    { n: 3, title: "Known issues", sub: "as they stood in September", view: "issues" },
    { n: 4, title: "What we told them", sub: "four tickets, pinned advice", view: "customers" },
    { n: 5, title: "Withdrawn", sub: "KI-204 updated, trace kept", view: "withdrawn" },
    { n: 6, title: "Two follow-ups", sub: "three ways to reply", view: "followups" },
    { n: 7, title: "Recall", sub: "who else, and the corrections", view: "recalled" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, issues: null, turns: [], customers: null, withdrawn: null, followups: null, recalled: null,
    ask: { customer: "northfield", q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const cust = (k) => (story()?.customers || []).find((c) => c.key === k) || {};
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const fromWorkaround = (t) => { const i = String(t || "").indexOf("Workaround"); return i >= 0 ? t.slice(i) : t; };

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Try a ticket<small>any text, any customer</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, issues, customers, withdrawn, followups, recalled, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Support memory</dt><dd>${st.ready ? "from an earlier run — Run again reads it back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2–3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>A withdrawn workaround is still in every customer you gave it to.</h1>
      <p class="lead">Pebbleline's support team keeps its <b>known issues</b> in one project and <b>what it told each customer</b> on that customer's own actor.
      Three customers were told KI-204's workaround: sign out of the iPad app and sign back in. Today engineering withdraws it — signing out discards notes that have not synced.
      The known issue is fixed in one place; the old advice is still in three customers' memory, and two of them write in today. This page drives the <code>memorylake</code> CLI for real. All companies and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes both projects, the ticket conversations, the four customers and both agents.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/known-issues-memory-for-support-teams" target="_blank" rel="noopener">memorylake.ai › Known issues memory for support teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  const initials = (n) => String(n || "").split(" ").map((w) => w[0]).slice(0, 2).join("");
  function setup() {
    const su = S.setup; if (!su) return empty("Two projects, one actor per customer");
    return `<h1>Two projects, one actor per customer</h1>
      <p class="lead">Known issues are team memory: one project every agent reads. Each customer is an actor of their own, so what we told them stays with them.
      Past ticket conversations go in a separate project — conversations are turned into memory on the project they live in, and the known-issues project should hold only issues.</p>
      <div class="grid">
        <div class="card"><h2 style="margin-top:0">${esc(su.projects.ki)}</h2><div class="muted small">team-wide: every known issue, pinned</div><div class="mono small faint">${esc(su.ids.ki)}</div></div>
        <div class="card"><h2 style="margin-top:0">${esc(su.projects.tickets)}</h2><div class="muted small">past ticket conversations</div><div class="mono small faint">${esc(su.ids.tickets)}</div></div>
        ${su.customers.map((c) => `<div class="card person"><span class="avatar user">${esc(initials(c.name))}</span><div><div class="name">${esc(c.name)}</div><div class="role">${esc(c.description)}</div><div class="mono small faint">${esc(su.ids[c.key])}</div></div><span class="status pill ok">customer memory</span></div>`).join("")}
        ${su.agents.map((a) => `<div class="card person"><span class="avatar">${esc(initials(a.name))}</span><div><div class="name">${esc(a.name)}</div><div class="role">${esc(a.description)}</div><div class="mono small faint">${esc(su.ids[a.key])}</div></div><span class="status pill">support agent</span></div>`).join("")}
      </div>`;
  }

  function kiTable(items) {
    const rows = items.slice().sort((a, b) => a.ki.localeCompare(b.ki));
    return `<div class="card"><table class="iso"><tr><th>issue</th><th>v</th><th>applies to</th><th>current workaround</th><th>status</th></tr>
      ${rows.map((k) => `<tr><td><b>${esc(k.ki)}</b></td><td>v${esc(k.version)}</td><td class="small">${esc(k.applies_when)}</td>
        <td class="small">${esc(k.workaround)}${(k.withdrawn || []).map((w) => `<div class="leakwhy">v${esc(w.version)} withdrawn ${esc(w.on)}: “${esc(w.workaround)}” — ${esc(w.reason)}</div>`).join("")}</td>
        <td class="small muted">${esc(k.status)}<br><span class="mono faint">${esc(k.id)}</span></td></tr>`).join("")}</table></div>`;
  }
  function issues() {
    const x = S.issues; if (!x) return empty("Known issues");
    return `<h1>Known issues, as they stood in September</h1>
      <p class="lead">One <code>fact add --project</code> for all four, then <code>fact update --metadata</code> on each: symptom, where it applies, the current workaround and its version.
      KI-204 is the one that matters: notes written on the iPad offline never reach the desktop, and its workaround is <b>sign out of the iPad app and sign back in</b>.</p>
      ${kiTable(x.items)}`;
  }

  function customers() {
    if (!S.turns.length && !S.customers) return empty("What we told them");
    const keys = (story()?.customers || []).map((c) => c.key);
    const told = S.customers?.told || [];
    return `<h1>What each customer was told</h1>
      <p class="lead">Each ticket is a DIRECT conversation (customer ↔ Dana) that MemoryLake turns into memory on the customer's actor. What support said is also pinned there word for word,
      with the ticket, the date, the known issue and the workaround version — extraction varies run to run, so nothing later depends on it.</p>
      ${S.customers ? `<div class="card"><table class="iso"><tr><th>customer</th><th>ticket</th><th>date</th><th>issue</th><th>what we told them</th></tr>
        ${told.map((r) => `<tr><td>${esc(r.name)}</td><td>${esc(r.ticket)}</td><td>${esc(r.date)}</td><td><b>${esc(r.ki)}</b> v${esc(r.version)}</td>
          <td class="small">${esc(r.advised)}${r.not_ki ? ` <span class="pill">not ${esc(r.not_ki)}</span>` : ""}</td></tr>`).join("")}</table>
        <p class="muted small" style="margin-bottom:0">+ ${Object.values(S.customers.extracted || {}).reduce((a, b) => a + b, 0)} fact(s) extracted from the four conversations (shown, not relied on)</p></div>` : ""}
      <div class="grid">${keys.map((k) => {
        const c = cust(k), turns = S.turns.filter((t) => t.customer === k);
        return `<div class="card"><h2 style="margin-top:0">${esc(c.name)} <span class="muted small">${esc(c.ticket?.id)}, ${esc(c.ticket?.date)}</span></h2>
          ${turns.length ? `<div class="bubbles">${turns.map((t) => `<div class="bubble ${t.who === "customer" ? "user" : "bot"}">${esc(t.line)}</div>`).join("")}</div>`
            : S.customers ? '<p class="muted small">filed by an earlier run</p>' : '<p><span class="pill busy"><span class="spinner"></span> turning the ticket into memory…</span></p>'}</div>`;
      }).join("")}</div>`;
  }

  function withdrawn() {
    const x = S.withdrawn; if (!x) return empty("Withdrawn");
    const names = x.still_told.map((k) => cust(k).name);
    return `<h1>Today: KI-204's workaround is withdrawn</h1>
      <p class="lead">${esc(story().today)}. Signing out discards notes that have not synced yet. The known issue is updated in place — new text and metadata in one <code>fact update</code> —
      so the change and its reason land in the fact's <code>fact trace</code>.</p>
      ${kiTable([x.ki])}
      <div class="card"><h2 style="margin-top:0">fact trace KI-204 <span class="muted small">newest first</span></h2>
        ${x.trace.map((e) => `<div class="memfact ${e.event === "UPDATE" ? "personal" : ""}"><span class="mk">${e.event === "UPDATE" ? "↻" : "+"}</span><span class="body"><b>${esc(e.event)}</b> <span class="mono small">${esc(e.how)} · ${esc(e.at)}</span>
          ${e.old ? `<div class="trace">was: “…${esc(fromWorkaround(e.old))}”</div>` : ""}<div class="trace">${e.old ? "now" : "text"}: “…${esc(fromWorkaround(e.new))}”</div></span></div>`).join("")}</div>
      <p class="tally"><span class="pill warn">still in ${names.length} customers' memory, word for word</span> <span class="small">${esc(names.join(", "))}</span></p>`;
  }

  function readingsCard(r) {
    const a = r.customer_only, b = r.ki_only, j = r.joined;
    const verdict = (x) => x.judge.ok ? `<span class="pill ok">✓ passes all ${x.judge.checks.length} checks</span>`
      : x.judge.checks.filter((c) => !c.ok).map((c) => `<span class="pill err">✗ ${esc(c.fail)}</span>`).join(" ");
    const box = (tag, title, how, x, extra) => `<div class="card ${x.judge.ok ? "mine" : ""}"><h2 style="margin-top:0">(${tag}) ${title}</h2>
      <div class="small muted">${how}</div>${extra || ""}<div class="bubble bot">${esc(x.reply)}</div><p class="tally">${verdict(x)}</p></div>`;
    return `<div class="bubble user"><b>${esc(r.ticket || "")} ${esc(r.name)}:</b> ${esc(r.text)}</div>
      <div class="grid">
        ${box("a", "Customer history only", `<code>search --actors ${esc(r.customer)}</code>, follow the closest advice${a.rank ? ` (#${a.rank} of top 5)` : ""}`, a)}
        ${box("b", "Known issues only", `<code>search --projects &lt;known issues&gt;</code>, top hit ${esc(b.top || "—")}`, b)}
        ${box("c", "Both, joined", "the customer's advice records × each issue as it stands now × <code>fact trace</code>", j,
          j.open.map((o) => `<div class="trace">${esc(o.ticket)} ${esc(o.date)} ${esc(o.ki)}: told v${esc(o.told_version)}, now v${esc(o.now_version)}${o.withdrawn ? ` — <b>v${esc(o.told_version)} withdrawn</b>` : " — still current"}${o.corrected ? " · correction on record" : ""}</div>`).join("")
          + (j.evidence ? `<div class="trace">fact trace: ${esc(j.evidence.event)} at ${esc(j.evidence.at)} · the step they were given is the old text word for word: <b>${j.evidence.verbatim ? "yes" : "no"}</b></div>` : ""))}
      </div>${(a.cached || b.cached) ? `<p class="muted small">${a.cached + b.cached} hit(s) left out: no longer in memory (search answered from its cache)</p>` : ""}`;
  }
  function followups() {
    const x = S.followups; if (!x) return empty("Two follow-ups");
    const m = (y) => y.judge.ok ? "✓" : '<span class="pill err">✗</span>';
    return `<h1>Two customers write in — three ways to reply</h1>
      <p class="lead">Leo Park started this week. Each reply is judged by the same three checks: it does not repeat the withdrawn step, it retracts what the customer was told,
      and it takes the right route — recovery for notes already lost, otherwise the current workaround.</p>
      ${x.rows.map((r) => `<div class="card">${readingsCard(r)}</div>`).join("")}
      <div class="card"><table class="iso"><tr><th>ticket</th><th>customer</th><th>(a) history only</th><th>(b) issues only</th><th>(c) joined</th></tr>
        ${x.rows.map((r) => `<tr><td>${esc(r.ticket)}</td><td>${esc(r.name)}</td><td>${m(r.customer_only)}</td><td>${m(r.ki_only)}</td><td>${m(r.joined)}</td></tr>`).join("")}</table></div>`;
  }

  function recalled() {
    const x = S.recalled; if (!x) return empty("Recall");
    const b = x.before, cb = x.come_back;
    const name = (k) => cust(k).name;
    return `<h1>Who else was told? Then the corrections go into memory</h1>
      <p class="lead">One <code>fact list --actors &lt;all four customers&gt; --query ${esc(b.query)}</code> — every match, nothing ranked away — and each fact's metadata decides.
      Oakline Dental's record mentions KI-204, but they were told KI-207's workaround.</p>
      <div class="card"><table class="iso"><tr><th>customer</th><th>kind</th><th>verdict</th></tr>
        ${b.rows.slice().sort((p, q) => p.name.localeCompare(q.name)).map((r) => `<tr><td>${esc(r.name)}</td><td class="small mono">${esc(r.kind)}</td>
          <td class="small">${r.verdict === "told the withdrawn step" ? `<span class="pill err">${esc(r.verdict)}</span>` : esc(r.verdict)}</td></tr>`).join("")}</table>
        <p class="tally"><span class="pill warn">given the withdrawn step: ${esc(b.told.map(name).join(", ") || "nobody")}</span></p></div>
      ${x.records.map((r) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(r.name)}</h2><span class="mono small">${esc(r.id)}</span></div><p>“${esc(r.text)}”</p></div>`).join("")}
      <p class="tally"><span class="pill ${x.left.length ? "warn" : "ok"}">re-read: ${x.after.told.length} given the withdrawn step, ${x.after.corrected.length} corrected, ${x.left.length} left to tell</span></p>
      <div class="card"><h2 style="margin-top:0">“${esc(cb.question)}” <span class="muted small">— memory of ${esc(cb.name)}, top 3</span></h2>
        ${cb.hits.map((h, i) => `<div class="memfact ${h.record ? "clean" : ""}"><span class="mk">${h.record ? "✓" : "·"}</span><span class="body">${i + 1}. ${esc(h.text)}</span></div>`).join("")}
        <p class="tally"><span class="pill ${cb.rank ? "ok" : "warn"}">${cb.rank ? `the correction is #${cb.rank}` : "the correction is not in the top 3"}</span></p></div>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    const cs = story()?.customers || [];
    return `<h1>Try a ticket</h1>
      <p class="lead">Any ticket text, for any customer: the three readings side by side, each judged by the same checks. After a run, the corrections are in memory too —
      so the history-only reading may now pass for a customer who has been corrected.</p>
      <div class="card"><div class="chips">${cs.map((c) => `<button type="button" class="chip ${S.ask.customer === c.key ? "on" : ""}" data-cust="${esc(c.key)}">${esc(c.name)}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Notes from the iPad are missing on the Mac again" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Check</button></form>
        <div class="chips">${["Notes from the iPad are not on the desktop again. Should we sign out like you said?", "Two notes vanished after I signed out on the iPad", "Tablet notes take hours to reach the desktop"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    st.querySelectorAll("[data-cust]").forEach((b) => b.addEventListener("click", () => { S.ask.customer = b.dataset.cust; S.ask.q = $("#ask-q")?.value || S.ask.q; renderStage(); }));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> checking…</span>`;
    const r = await api("/api/ask", { customer: S.ask.customer, q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = readingsCard(r);
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
  function resetView() { S.setup = null; S.issues = null; S.turns = []; S.customers = null; S.withdrawn = null; S.followups = null; S.recalled = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's projects, ticket conversations, customers and agents from your account?")) return;
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
    else if (k === "known_issues") S.issues = d;
    else if (k === "turn") S.turns.push(d);
    else if (k === "customers") S.customers = d;
    else if (k === "withdrawn") S.withdrawn = d;
    else if (k === "follow_ups") S.followups = d;
    else if (k === "recalled") S.recalled = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the projects, ticket conversations, customers and agents are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try a ticket of your own — “Try a ticket” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "turn"].includes(k)) { renderSteps(); renderStage(); }
    else if (S.caughtUp && k === "turn" && S.view === "customers") renderStage();
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "known_issues", "turn", "customers",
    "withdrawn", "follow_ups", "recalled", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.recalled ? "recalled" : "connect");
    subscribe();
  })();
})();

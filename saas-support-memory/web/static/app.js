/* AI memory for small SaaS support teams — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, one actor per customer", view: "setup" },
    { n: 3, title: "Known issues", sub: "conditions, workaround, breaks", view: "issues" },
    { n: 4, title: "Customer memory", sub: "September's tickets", view: "customers" },
    { n: 5, title: "By symptom only", sub: "same symptom, same reply", view: "symptom" },
    { n: 6, title: "With customer memory", sub: "every condition, cited", view: "checked" },
    { n: 7, title: "Into memory", sub: "said, held back, found again", view: "remembered" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, issues: null, turns: [], customers: null, symptom: null, checked: null, remembered: null,
    ask: { customer: "copperleaf", q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const cust = (k) => (story()?.customers || []).find((c) => c.key === k) || {};
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const WORDS = { multi: "more than one currency", unset: "no default currency" };
  const condText = (aw) => Object.entries(aw || {}).map(([a, v]) => `${a.replace("_", " ")}: ${WORDS[v] || v}`).join(" and ");

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Try a ticket<small>any text, either customer</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, issues, customers, symptom, checked, remembered, ask }[S.view];
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
    return `<h1>Same symptom, two customers, two different right answers.</h1>
      <p class="lead">Tallyfold's four-person support team keeps two kinds of memory: the team's <b>known issues</b> (each with the conditions it applies under and what its workaround breaks)
      and <b>one memory per customer</b>, learned from past tickets. Today two customers report blank totals on their PDF invoices. By symptom alone both get the same reply —
      and for one of them it is wrong, and harmful. This page drives the <code>memorylake</code> CLI for real. All companies and people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes both projects, the ticket conversations, both customers and both agents.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-small-saas-support-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for small SaaS support teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  const initials = (n) => String(n || "").split(" ").map((w) => w[0]).slice(0, 2).join("");
  function setup() {
    const su = S.setup; if (!su) return empty("Two projects, one actor per customer");
    return `<h1>Two projects, one actor per customer</h1>
      <p class="lead">Known issues are team memory: one project every agent reads. Each customer is an actor of their own, so what they told us stays theirs.
      Past ticket conversations go in a separate project — conversations are turned into memory on the project they live in, and the known-issues project should hold only issues.</p>
      <div class="grid">
        <div class="card"><h2 style="margin-top:0">${esc(su.projects.ki)}</h2><div class="muted small">team-wide: every known issue, pinned</div><div class="mono small faint">${esc(su.ids.ki)}</div></div>
        <div class="card"><h2 style="margin-top:0">${esc(su.projects.tickets)}</h2><div class="muted small">past ticket conversations</div><div class="mono small faint">${esc(su.ids.tickets)}</div></div>
        ${su.customers.map((c) => `<div class="card person"><span class="avatar user">${esc(initials(c.name))}</span><div><div class="name">${esc(c.name)}</div><div class="role">${esc(c.description)}</div><div class="mono small faint">${esc(su.ids[c.key])}</div></div><span class="status pill ok">customer memory</span></div>`).join("")}
        ${su.agents.map((a) => `<div class="card person"><span class="avatar">${esc(initials(a.name))}</span><div><div class="name">${esc(a.name)}</div><div class="role">${esc(a.description)}</div><div class="mono small faint">${esc(su.ids[a.key])}</div></div><span class="status pill">support agent</span></div>`).join("")}
      </div>`;
  }

  function issues() {
    const x = S.issues; if (!x) return empty("Known issues");
    const rows = x.items.slice().sort((a, b) => a.ki.localeCompare(b.ki));
    return `<h1>Known issues — when each applies, and what its workaround breaks</h1>
      <p class="lead">One <code>fact add --project</code> for all four, then <code>fact update --metadata</code> on each: symptom, the conditions it applies under, the workaround, what it breaks, status.
      KI-109 is the one that matters later: the Legacy renderer — KI-101's workaround — drops VAT lines on multi-currency invoices.</p>
      <div class="card"><table class="iso"><tr><th>issue</th><th>symptom</th><th>applies when</th><th>workaround / note</th><th>status</th></tr>
        ${rows.map((k) => `<tr><td><b>${esc(k.ki)}</b></td><td class="small mono">${esc(k.symptom)}</td><td class="small">${esc(condText(k.applies_when))}</td>
          <td class="small">${k.breaks ? `<span class="pill err">breaks “${esc(k.breaks)}”</span> ` : ""}${esc(k.workaround)}</td><td class="small muted">${esc(k.status)}<br>${esc(k.reported)} · ${esc(k.verified_by)}</td></tr>`).join("")}</table></div>
      ${rows.map((k) => `<div class="memfact"><span class="mk">•</span><span class="body">${esc(k.text)}<div class="trace mono">${esc(k.id)}</div></span></div>`).join("")}`;
  }

  const ENV = ["browser", "currencies", "default_currency", "integrations", "contact"];
  function customers() {
    if (!S.turns.length && !S.customers) return empty("Customer memory");
    const keys = (story()?.customers || []).map((c) => c.key);
    return `<h1>Customer memory — what each customer told us in September</h1>
      <p class="lead">Each September ticket is a DIRECT conversation (customer ↔ Dana) that MemoryLake turns into memory. What an agent must be able to rely on is pinned on the customer's actor
      with the ticket and date it came from; the extracted facts are shown, not relied on — extraction varies run to run.</p>
      <div class="grid">${keys.map((k) => {
        const c = cust(k), p = S.customers?.profiles.find((x) => x.key === k);
        const turns = S.turns.filter((t) => t.customer === k);
        return `<div class="card"><h2 style="margin-top:0">${esc(c.name)} <span class="muted small">${esc(c.ticket?.id)}, ${esc(c.ticket?.date)}</span></h2>
          ${turns.length ? `<div class="bubbles">${turns.map((t) => `<div class="bubble ${t.who === "customer" ? "user" : "bot"}">${esc(t.line)}</div>`).join("")}</div>` : '<p class="muted small">filed by an earlier run</p>'}
          ${p ? `<h2>Pinned on ${esc(c.short)}'s actor</h2><table class="iso">${ENV.filter((a) => p.env[a]).map((a) => `<tr><td class="small mono">${a}</td><td><b>${esc(p.env[a].value)}</b></td><td class="small muted">${esc(p.env[a].ticket)}, ${esc(p.env[a].date)}</td></tr>`).join("")}</table>
            <h2>Also remembered (extracted): ${p.extracted.length}</h2>${p.extracted.map((e) => `<div class="memfact"><span class="mk">·</span><span class="body">${esc(e.text)}</span></div>`).join("")}`
            : '<p><span class="pill busy"><span class="spinner"></span> turning the ticket into memory…</span></p>'}</div>`;
      }).join("")}</div>
      ${S.customers ? `<p class="muted small">${S.customers.tickets_project} more fact(s) landed on the ticket-history project; the known-issues project holds only the pinned issues.</p>` : ""}`;
  }

  function symptom() {
    const x = S.symptom; if (!x) return empty("By symptom only");
    return `<h1>Today's tickets, by symptom only</h1>
      <p class="lead">${esc(story().today)}. Leo Park started this week. The way a tool that sees one ticket at a time would answer: the ticket text against the known issues
      (<code>search --projects &lt;known issues&gt; --top-k 3</code>), nothing else.</p>
      <div class="grid">${x.rows.map((r) => `<div class="card"><h2 style="margin-top:0">${esc(r.ticket)} · ${esc(r.name)}</h2>
        <div class="bubble user">${esc(r.text)}</div>
        ${r.hits.map((h, i) => `<div class="memfact ${i === 0 ? "new" : ""}"><span class="mk">${i + 1}</span><span class="body"><b>${esc(h.ki || "—")}</b> ${esc(h.text)}</span></div>`).join("")}
        <p class="tally"><span class="pill">reply: ${esc(r.top || "none")}</span> <span class="small">${esc(r.reply || "")}</span></p></div>`).join("")}</div>
      <p class="tally"><span class="pill ${x.same ? "warn" : ""}">${x.same ? `both tickets get the same reply: ${esc(x.rows[0].top)}` : "the two tickets get different top hits"}</span>
        <span class="muted small">the lookup knows the symptom, not the customer</span></p>`;
  }

  function evalCard(ev) {
    const cls = { "✓": "clean", "⚠": "personal", "✗": "nomatch", "?": "" }[ev.verdict];
    const head = { "✓": "applies", "⚠": "applies, but its workaround is forbidden here", "✗": "does not apply", "?": "unknown — ask the customer" }[ev.verdict];
    return `<div class="memfact ${cls}"><span class="mk">${ev.verdict}</span><span class="body"><b>${esc(ev.ki)}</b> ${head}
      ${ev.conditions.map((c) => `<div class="trace">${c.ok ? "✓" : c.ok === null ? "?" : "✗"} needs ${esc(c.attr.replace("_", " "))}: ${esc(WORDS[c.want] || c.want)} · customer: <b>${esc(c.value || "nothing on record")}</b>${c.source ? ` (${esc(c.source)})` : ""}</div>`).join("")}
      ${ev.blockers.map((b) => `<div class="leakwhy">⚠ its workaround is forbidden by ${esc(b.ki)}: “${esc(b.text)}”</div>`).join("")}</span></div>`;
  }
  function checkCard(r, withSearch) {
    const n = r.naive || {};
    return `<div class="card ${r.pick ? "mine" : ""}"><h2 style="margin-top:0">${esc(r.name)}</h2>
      ${withSearch ? `<div class="small muted">one <code>search</code> over known issues ∪ ${esc(r.name)}'s memory (top 8):</div>
        <div class="hits">${r.search.map((h, i) => `<div class="hitrow" title="${esc(h.text)}">${i + 1}. <span class="pill">${esc(h.label)}</span> ${esc(h.text)}</div>`).join("")}</div>` : ""}
      <h2>Every known issue tagged <code>${esc(r.symptom)}</code>, checked</h2>${r.evals.map(evalCard).join("")}
      <p class="tally">${r.pick ? `<span class="pill ok">reply: ${esc(r.pick)}</span> <span class="small">${esc(r.reply)}${r.contact ? ` <span class="muted">(as ${esc(r.contact)})</span>` : ""}</span>` : '<span class="pill warn">no known issue fits: escalate as new</span>'}</p>
      ${n.top ? `<p class="small" style="margin-bottom:0">${n.right ? `✓ the symptom-only reply (${esc(n.top)}) happens to be right here` : `<span class="pill err">✗ the symptom-only reply (${esc(n.top)}) was wrong here</span>`}</p>` : ""}</div>`;
  }
  function checked() {
    const x = S.checked; if (!x) return empty("With customer memory");
    return `<h1>The same tickets, with customer memory</h1>
      <p class="lead">Every known issue with the ticket's symptom (from <code>fact list</code>, not the top-k) is checked against what the customer told us — each condition cites the ticket and date it came from.
      A workaround that another known issue forbids on this kind of account is held back.</p>
      <div class="grid">${x.rows.map((r) => checkCard(r, true)).join("")}</div>
      <div class="card"><table class="iso"><tr><th>ticket</th><th>customer</th><th>symptom only</th><th>with customer memory</th></tr>
        ${x.rows.map((r) => `<tr><td>${esc(cust(r.customer).today?.id)}</td><td>${esc(r.name)}</td><td>${esc(r.naive.top || "—")} ${r.naive.right ? "✓" : '<span class="pill err">✗</span>'}</td><td><b>${esc(r.pick || "escalate")}</b> ✓</td></tr>`).join("")}</table></div>`;
  }

  function remembered() {
    const x = S.remembered; if (!x) return empty("Into memory");
    const cb = x.come_back;
    return `<h1>The reply goes into memory</h1>
      <p class="lead">What was matched, what was said and what was held back — and why — goes onto the customer's actor (<code>fact add --actor</code> + <code>--metadata</code>).
      Next week, whoever opens the customer's next ticket finds it with one <code>search</code>.</p>
      ${x.records.map((r) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(r.name)}</h2><span class="mono small">${esc(r.id)}</span></div>
        <p>“${esc(r.text)}”</p><div class="trace">matched ${esc(r.meta.matched || "nothing")} · withheld ${esc((r.meta.withheld || []).join(", ") || "nothing")} · ${esc(r.meta.agent)}, ${esc(r.meta.date)}${r.new ? "" : " · recorded by an earlier run"}</div></div>`).join("")}
      <div class="card"><h2 style="margin-top:0">“${esc(cb.question)}” <span class="muted small">— ${esc(cb.name)}'s memory, top 3</span></h2>
        ${cb.hits.map((h, i) => `<div class="memfact ${h.record ? "clean" : ""}"><span class="mk">${h.record ? "✓" : "·"}</span><span class="body">${i + 1}. ${esc(h.text)}</span></div>`).join("")}
        <p class="tally"><span class="pill ${cb.rank ? "ok" : "warn"}">${cb.rank ? `today's record is #${cb.rank}` : "today's record is not in the top 3"}</span></p></div>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    const cs = story()?.customers || [];
    return `<h1>Try a ticket</h1>
      <p class="lead">Any ticket text, for either customer: the symptom-only answer and the memory-checked one, side by side. The symptom is taken from the closest known issue.</p>
      <div class="card"><div class="chips">${cs.map((c) => `<button type="button" class="chip ${S.ask.customer === c.key ? "on" : ""}" data-cust="${esc(c.key)}">${esc(c.name)}</button>`).join("")}</div>
        <form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. The VAT lines are missing from my PDF invoices" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Check</button></form>
        <div class="chips">${["The VAT lines are missing from my PDF invoices", "Our PDF invoices show blank totals", "QuickBooks has two copies of some customers"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    const n = r.naive;
    S.ask.html = `<div class="grid">
      <div><h2>By symptom only · top 3</h2>${n.hits.map((h, i) => `<div class="memfact ${i === 0 ? "new" : ""}"><span class="mk">${i + 1}</span><span class="body"><b>${esc(h.ki || "—")}</b> ${esc(h.text)}</span></div>`).join("") || '<p class="muted">nothing</p>'}</div>
      <div><h2>With ${esc(r.name)}'s memory</h2>${r.symptom ? checkCard(r, false) : '<p class="muted">No known issue is close to this text: escalate it as a new issue.</p>'}</div></div>`;
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
  function resetView() { S.setup = null; S.issues = null; S.turns = []; S.customers = null; S.symptom = null; S.checked = null; S.remembered = null; S.ask.html = ""; }
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
    else if (k === "symptom_only") S.symptom = d;
    else if (k === "checked") S.checked = d;
    else if (k === "remembered") S.remembered = d;
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
    "symptom_only", "checked", "remembered", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.remembered ? "remembered" : "connect");
    subscribe();
  })();
})();

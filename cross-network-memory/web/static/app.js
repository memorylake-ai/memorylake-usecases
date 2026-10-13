/* Shared memory for agents on different networks — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "your personal team", view: "connect" },
    { n: 2, title: "Sandbox team", sub: "team create --issue-api-key", view: "sandbox" },
    { n: 3, title: "Memories & agents", sub: "member create · scope set", view: "setup" },
    { n: 4, title: "Keys pinned", sub: "api-key create --allowed-ip", view: "keys" },
    { n: 5, title: "Dispatch writes", sub: "fact add, Harborline's key", view: "dispatch" },
    { n: 6, title: "Partner joins", sub: "api-key update --allowed-ip", view: "partner" },
    { n: 7, title: "Suspend & resume", sub: "api-key update --status", view: "suspend" },
    { n: 8, title: "Network changes", sub: "allowlist drops this machine", view: "egress" },
    { n: 9, title: "Audit & ledger", sub: "audit-log list · every call", view: "audit" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    sandbox: null, setup: null, keys: null, dispatch: null, partner: null, suspend: null, egress: null, audit: null,
    calls: [], report: null, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const C = () => S.data?.contract;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "report" ? "active" : ""}" data-view="report"><span class="n">≡</span><span class="t">Contract review<small>out/contract-review.md</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, sandbox, setup, keys, dispatch, partner, suspend, egress, audit, report }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const when = (ts) => ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + "Z" : "—";
  const refusal = (status, code, message) => `<div class="refusal">HTTP ${esc(status)}${code ? " " + esc(code) : ""} · ${esc(message)}</div>`;
  const nets = (a) => (a && a.length ? a.map((x) => `<span class="pill mono">${esc(x)}</span>`).join(" ") : '<span class="pill">anywhere</span>');
  const by = (w) => `<span class="pill ${w === "kestrel-broker" ? "accent" : ""}">${esc(w)}</span>`;
  const hits = (hs) => (hs || []).map((h, i) => `<div class="memfact small"><span class="ev">#${i + 1}</span><span>${by(h.writer)} ${esc(h.text)}</span></div>`).join("");

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Your team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Sandbox team</dt><dd>${st.sandbox ? `<span class="mono">${esc(st.sandbox)}</span> <span class="muted small">from the last run — Clean up deletes it</span>` : '<span class="muted">none — the run creates one</span>'}</dd>
          <dt>Last review</dt><dd>${st.has_report ? '<span class="muted">out/contract-review.md</span>' : '<span class="muted">none yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.has_report ? "Run again" : "Run the demo"}</button>
          <span class="muted small">under a minute · no LLM calls, nothing to wait for</span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key <span class="faint">(of your personal team)</span></label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    const c = C();
    return `<h1>Two companies' agents, one shared memory — every key pinned to its agent's network.</h1>
      <p class="lead"><b>${esc(c?.company.name || "Harborline Freight")}</b>'s dispatch agent and <b>${esc(c?.company.partner || "Kestrel Customs Brokerage")}</b>'s
      broker agent (both fictional) share a <b>shipment contract</b> memory; Harborline keeps a second memory to itself. Each agent gets its own key, limited to the memory the
      contract names <i>and</i> to its network (<code>--allowed-ip</code>). This page builds that in a <b>sandbox org team</b> on your account, uses each key from this machine,
      suspends one, moves one, and reads the audit log. Clean up deletes the sandbox team.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">The contract</h2>${contractTable()}
          <p class="muted small" style="margin-bottom:0">${esc(c?.partner_network.why || "")}</p>
          <p class="muted small" style="margin-bottom:0">An account may own one org team besides its personal team; if yours already has one, the run stops at step 2 and says so.
          Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-agents-that-talk-to-each-other-across-networks" target="_blank" rel="noopener">memorylake.ai › memory for agents that talk across networks</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }
  function contractTable() {
    const c = C(); if (!c) return "";
    return `<table class="mx"><thead><tr><th></th>${c.projects.map((x) => `<th>${esc(x.name)}</th>`).join("")}<th>network</th></tr></thead><tbody>
      ${c.agents.map((a) => `<tr><th>${esc(a.slug)}<div class="muted small" style="font-weight:400">${esc(a.org)}</div></th>${c.projects.map((x) => {
        const l = a.grants[x.slug]; return `<td class="${l ? "" : "none"}">${l ? `<span class="pill ${l === "write" ? "accent" : ""}">${esc(l)}</span>` : '<span class="faint">—</span>'}</td>`; }).join("")}
        <td class="small">${esc(a.network)}</td></tr>`).join("")}</tbody></table>`;
  }

  function sandbox() {
    const s = S.sandbox; if (!s) return empty("Sandbox team");
    return `<h1>A sandbox org team — the only kind of team that can have members</h1>
      <p class="lead">Your personal team cannot have members. <code>team create --issue-api-key</code> makes an org team owned by you and prints its owner key once;
      the demo stores it in its own CLI profile and does everything else in that team. Your own team is not touched.</p>
      <div class="grid">
        <div class="card"><b>Your team</b><div class="big" style="margin-top:6px">${esc(s.personal.name)}</div><div class="muted small">${esc(s.personal.type)}</div></div>
        <div class="card"><b>Sandbox team</b><div class="big" style="margin-top:6px">${esc(s.team.name)}</div><div class="muted small">${esc(s.team.type)} · you are ${esc(s.team.role)} · <span class="mono">${esc(s.team.id)}</span></div></div>
        <div class="card"><b>Owner key</b><div class="big" style="margin-top:6px">${esc(s.owner_key.name)}</div><div class="muted small">id ${esc(s.owner_key.id)} · prefix <span class="mono">${esc(s.owner_key.prefix)}</span> · in <code>.memorylake-demo/org/</code> only</div></div></div>`;
  }

  function setup() {
    const s = S.setup; if (!s) return empty("Memories & agents");
    return `<h1>Two memories, two agents — the contract says who reaches what</h1>
      <p class="lead">Two projects: the <b>shipment contract</b> both companies share, and <b>Harborline internal</b>. Each agent is a virtual member
      (<code>member create</code>, no login, acts only through API keys) limited by <code>member scope set</code> to the memory the contract names.</p>
      <div class="flow">${s.projects.map((p) => `<div class="card"><div class="row spread"><b>${esc(p.name)}</b><span class="pill ${p.shared ? "accent" : ""}">${p.shared ? "shared" : "Harborline only"}</span></div>
        <div class="muted small mono">${esc(p.id)}</div></div>`).join("")}</div>
      <div class="flow">${s.agents.map((a) => `<div class="card"><b>${esc(a.agent)}</b> <span class="muted small">${esc(a.org)}</span>
        <dl class="kv" style="margin-top:8px"><dt>member</dt><dd class="mono small">${esc(a.principal_id)}</dd>
        <dt>reaches</dt><dd class="small">${Object.entries(a.grants).map(([p, l]) => `${esc(s.projects.find((x) => x.slug === p)?.name || p)} (${esc(l)})`).join(", ")}</dd>
        <dt>network</dt><dd class="small">${esc(a.network)}</dd></dl></div>`).join("")}</div>`;
  }

  function keys() {
    const k = S.keys; if (!k) return empty("Keys pinned");
    return `<h1>Each key pinned to its agent's network</h1>
      <p class="lead"><code>api-key create --member … --allowed-ip ${esc(k.partner.cidr)}</code>: Kestrel's key only works from Kestrel's network. Used from this machine,
      <code>auth login</code> is refused before the key touches any memory — and the refusal names the address the server saw. That is this machine's IP, so
      Harborline's dispatch key is pinned to exactly it.</p>
      <div class="flow">
        <div class="card"><b>kestrel-broker, from this machine</b>${refusal(k.partner.status, k.partner.code, k.partner.message)}</div>
        <div class="card"><b>This machine, as the server sees it</b><div class="big" style="margin-top:6px">${esc(k.egress)}</div><div class="muted small">read from the refusal — no IP-echo service needed</div></div></div>
      <div class="card"><table class="tour"><thead><tr><th>key</th><th>id</th><th>prefix</th><th>allowed from</th></tr></thead><tbody>
        ${Object.entries(k.keys).map(([s, v]) => `<tr><td>${esc(v.name)}</td><td class="mono small">${esc(v.id)}</td><td class="mono small">${esc(v.prefix)}</td><td>${nets(v.allowed)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function dispatch() {
    const d = S.dispatch; if (!d) return empty("Dispatch writes");
    const c = C();
    return `<h1>Harborline's dispatch agent writes the shipments</h1>
      <p class="lead">With its own key, from this machine: four shipments into the shared contract, three notes that stay inside Harborline.</p>
      <div class="flow">${Object.entries(d.written).map(([slug, facts]) => `<div class="card"><div class="row spread"><b>${esc(c?.projects.find((p) => p.slug === slug)?.name || slug)}</b><span class="pill">${facts.length} facts</span></div>
        ${facts.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function partner() {
    const p = S.partner; if (!p) return empty("Partner joins");
    return `<h1>Kestrel's agent joins the shared memory — and only the shared memory</h1>
      <p class="lead">For this demo this machine also plays Kestrel's network: the contract adds it to the broker key's allowlist
      (<code>api-key update --allowed-ip</code> ${nets(p.allowed)}). The key that was refused at step 4 now logs in — the key did not change, the contract did.</p>
      <div class="tally"><span class="pill ${p.matched === p.total ? "ok" : "err"}">${p.matched}/${p.total} calls as the contract says</span>
        ${p.rank ? `<span class="pill accent">Kestrel's hold is #${esc(p.rank)} in Harborline's answer</span>` : ""}</div>
      <div class="flow">
        <div class="card"><b>kestrel-broker asks</b> <span class="muted small">${esc(p.questions.partner)}</span>${hits(p.partner_hits)}
          <div style="margin-top:8px"><b class="small">and records</b>${p.partner_wrote.map((f) => `<div class="memfact small new"><span>${esc(f)}</span></div>`).join("")}</div></div>
        <div class="card"><b>harborline-dispatch asks</b> <span class="muted small">${esc(p.questions.dispatch)}</span>${hits(p.dispatch_hits)}</div></div>
      <div class="card"><b>Every call, with the agent's own key</b><table class="tour"><thead><tr><th>agent</th><th>op</th><th>memory</th><th>answer</th></tr></thead><tbody>
        ${p.cells.map((x) => `<tr><td>${esc(x.agent)}</td><td>${esc(x.op)}</td><td>${esc(x.project)}</td><td><span class="op ${x.allowed ? "yes" : "no"} ${x.match ? "" : "diff"}">${x.allowed ? "✓" : "✗ " + esc(x.status)}</span> <span class="muted small">${esc(x.detail)}</span></td></tr>`).join("")}</tbody></table>
        ${refusal(p.refusal.status, p.refusal.code, p.refusal.message)}</div>`;
  }

  function suspend() {
    const s = S.suspend; if (!s) return empty("Suspend & resume");
    return `<h1>A key may have leaked: suspend it, keep its memory, resume the same key</h1>
      <p class="lead">${esc(s.reason)}. <code>api-key update --status disabled</code> — not <code>revoke</code>, not <code>rotate</code>: nothing has to be re-issued to the partner.</p>
      <div class="flow">
        <div class="card"><b>${esc(s.off.after_s)} s after <code>--status disabled</code></b>${refusal(s.off.status, s.off.code, s.off.message)}<div class="muted small" style="margin-top:6px">api-key get: status “${esc(s.key_status)}”</div></div>
        <div class="card"><b>What it wrote</b><div class="big" style="margin-top:6px">${esc(s.kept)}/${esc(s.wrote)}</div><div class="muted small">of kestrel-broker's facts read by harborline-dispatch while the key is suspended</div></div>
        <div class="card"><b>${esc(s.on.after_s)} s after <code>--status enabled</code></b><div class="big" style="margin-top:6px">${s.on.rc === 0 ? "rc 0" : "refused"}</div><div class="muted small">the same key, never re-issued · ${esc(s.on.facts)} facts</div><div class="muted small" style="margin-top:6px">${esc(s.resolved)}</div></div></div>`;
  }

  function egress() {
    const e = S.egress; if (!e) return empty("Network changes");
    return `<h1>Kestrel's network changes: the contract drops this machine</h1>
      <p class="lead">${esc(e.reason)}. One <code>api-key update --allowed-ip</code>; the next call from here is refused and names this machine. Harborline's own key, from the same machine, still works.</p>
      <div class="flow">
        <div class="card"><b>kestrel-broker, from this machine</b>${refusal(e.partner.status, e.partner.code, e.partner.message)}</div>
        <div class="card"><b>harborline-dispatch, same machine</b><div class="big" style="margin-top:6px">${e.dispatch_rc === 0 ? "rc 0" : "refused"}</div></div></div>
      <div class="card"><b>The contract as the server holds it</b> <span class="muted small">api-key list</span><table class="tour"><thead><tr><th>key</th><th>agent</th><th>status</th><th>allowed from</th></tr></thead><tbody>
        ${e.keys.map((k) => `<tr><td>${esc(k.name)}</td><td class="small">${esc(k.owner)}</td><td><span class="pill ${k.status === "enabled" ? "ok" : "err"}">${esc(k.status)}</span></td><td>${nets(k.allowed)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function audit() {
    const a = S.audit; if (!a) return empty("Audit & ledger");
    return `<h1>Audit log for contract changes, access ledger for every call</h1>
      <p class="lead"><code>audit-log list</code> in the sandbox team: every key created or updated, every scope set, and which key did it.
      <code>service_account:update</code> says a key changed, not what changed (read that with <code>api-key get</code>). Refused calls are not in the log
      (<code>--result failed</code>: ${esc(a.audit_failed)}), so the demo keeps its own ledger of every agent call: <code>out/access-ledger.jsonl</code>.</p>
      <div class="tally"><span class="pill">${a.rows.length} audit events</span><span class="pill">${esc(a.ledger_calls)} agent calls</span>
        <span class="pill err">${esc(a.ledger_refused)} refused</span>${Object.entries(a.refused_by).map(([k, v]) => `<span class="pill">${esc(k)} ${esc(v)}</span>`).join("")}</div>
      <div class="flow" style="grid-template-columns: minmax(0, 3fr) minmax(0, 2fr)">
        <div class="card"><b>Audit log</b><table class="tour"><thead><tr><th>time (UTC)</th><th>action</th><th>key</th><th>target</th></tr></thead><tbody>
          ${a.rows.map((r) => `<tr><td class="mono small">${esc(when(r.at))}</td><td class="small">${esc(r.action)}</td><td class="mono small">${esc(r.key)}</td><td class="small">${esc(r.target)}</td></tr>`).join("")}</tbody></table></div>
        <div class="card"><b>Refused calls</b> <span class="muted small">from the demo's ledger</span>
          ${a.refused.map((x) => `<div class="memfact small"><span class="ev">${esc(when(x.at).slice(11))}</span><span><b>${esc(x.agent)}</b> ${esc(x.op)} ${esc(x.project || "")}<br><span class="refusal">HTTP ${esc(x.status)} · ${esc(x.why)}${x.ip ? " · from " + esc(x.ip) : ""}</span></span></div>`).join("")}</div></div>
      <div class="row"><button class="btn" data-act="audit" ${S.st.status === "running" ? "disabled" : ""}>Read the audit log again</button></div>`;
  }

  function report() {
    const r = S.report;
    if (!r) return `<h1>Contract review</h1><div class="empty">${S.st.has_report ? '<button class="btn" data-act="loadreport">Load the last review</button>' : "Run the demo first."}</div>`;
    const p = r.partner_check, s = r.suspend, e = r.egress_change;
    return `<h1>${esc(r.company)} × ${esc(r.partner)} — agent memory contract review</h1>
      <p class="lead">Generated ${esc(r.generated_at)} in sandbox team <span class="mono">${esc(r.sandbox.team_id)}</span>. The same review is in
      <code>out/contract-review.md</code> (<a href="/api/report.md" target="_blank" rel="noopener">open</a>) and <code>.json</code>; every agent call is in <code>out/access-ledger.jsonl</code>.</p>
      <div class="tally"><span class="pill ${p.matched === p.total ? "ok" : "err"}">${p.matched}/${p.total} calls as the contract says</span>
        <span class="pill">pinned key from outside: HTTP ${esc(r.keys.partner.status)}</span>
        <span class="pill">suspended: refused after ${esc(s.off.after_s)} s · resumed after ${esc(s.on.after_s)} s</span>
        <span class="pill">network change: HTTP ${esc(e.partner.status)}</span><span class="pill">${r.audit.rows.length} audit events · ${esc(r.audit.audit_failed)} refused calls in it</span></div>
      <div class="card"><table class="tour"><thead><tr><th>key</th><th>agent</th><th>status</th><th>allowed from</th></tr></thead><tbody>
        ${e.keys.map((k) => `<tr><td>${esc(k.name)}</td><td class="small">${esc(k.owner)}</td><td>${esc(k.status)}</td><td>${nets(k.allowed)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  // ---------------------------------------------------------------- behaviour
  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    st.querySelector('[data-act="audit"]')?.addEventListener("click", async () => { banner(""); S.follow = false; const r = await api("/api/audit", {}); if (r) { S.st = r; setConn(); } });
    st.querySelector('[data-act="loadreport"]')?.addEventListener("click", loadReport);
  }
  async function loadReport() {
    try { const r = await fetch("/api/report"); const j = await r.json(); if (!r.ok || j.error) { banner(j.error || `HTTP ${r.status}`); return; } S.report = j; if (S.view === "report") renderStage(); } catch (e) { banner(String(e)); }
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
    $("#conn-text").textContent = st.status === "running" ? `running ${st.job}…` : st.connected ? `${st.team?.name || "connected"}${st.sandbox ? " · sandbox " + st.sandbox.slice(0, 8) : ""}` : "Not connected";
    const busy = st.status === "running";
    ["#btn-run", "#btn-reset", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() {
    Object.assign(S, { sandbox: null, setup: null, keys: null, dispatch: null, partner: null, suspend: null, egress: null, audit: null, calls: [], report: null });
  }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the sandbox team the demo created (its projects, members and keys)? Your own team is not touched.")) return;
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
  const VIEWS = ["sandbox", "setup", "keys", "dispatch", "partner", "suspend", "egress", "audit"];
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    let rerender = true;
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView();
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
      if (S.follow && d.title === "Audit") show("audit");
    }
    else if (k === "cmd") { term("cmd", ev.text); rerender = false; }
    else if (k === "note") { term("note", ev.text); rerender = false; }
    else if (k === "progress") { term("progress", ev.text.trim()); rerender = false; }
    else if (k === "json") { term("dim", ev.text); rerender = false; }
    else if (k === "text") rerender = false;
    else if (k === "call") { S.calls.push(d); rerender = false; }
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "intro") { /* the contract is in /api/data */ }
    else if (VIEWS.includes(k)) S[k] = d;
    else if (k === "report") S.report = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the sandbox team, its members and keys are deleted. Your own team was not touched.", true); }
      else if (d.job === "run" && S.caughtUp) { banner("Done. The sandbox team is still there: read its audit log again, or Clean up to delete it.", true); loadReport(); }
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && rerender) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); if (S.view === "connect") renderStage(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "intro", "call", ...VIEWS, "report", "summary", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => { if (!e.data) return; try { onEvent(JSON.parse(e.data)); } catch { /* ignore */ } }));
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
      const h = (e) => { if (!e.data) return; let ev; try { ev = JSON.parse(e.data); } catch { return; } onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") { Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; }); }
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.partner ? "partner" : "connect");
    subscribe();
  })();
})();

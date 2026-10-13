/* Role-based access to AI memory, enforced by the server — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "your personal team", view: "connect" },
    { n: 2, title: "Sandbox team", sub: "team create --issue-api-key", view: "sandbox" },
    { n: 3, title: "Memory", sub: "rule book · claims · HR", view: "memory" },
    { n: 4, title: "Members & policy", sub: "member scope set · access set-level", view: "policy" },
    { n: 5, title: "27 calls", sub: "each assistant, its own key", view: "matrix" },
    { n: 6, title: "Who reaches HR", sub: "access show", view: "who" },
    { n: 7, title: "A grant changes", sub: "access grant vs scope set", view: "change" },
    { n: 8, title: "A key leaks", sub: "member remove → 401", view: "remove" },
    { n: 9, title: "Audit log", sub: "audit-log list", view: "audit" },
  ];
  const OPS = ["read", "search", "write"];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    sandbox: null, memory: null, policy: null, cells: [], matrix: null, who: null, change: null, remove: null, audit: null,
    report: null, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const P = () => S.data?.policy;
  const pname = (slug) => P()?.projects.find((p) => p.slug === slug)?.name || slug;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "report" ? "active" : ""}" data-view="report"><span class="n">≡</span><span class="t">Access review<small>out/access-review.md</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, sandbox, memory, policy, matrix, who, change, remove, audit, report }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const mark = (ok) => `<span class="pill ${ok ? "ok" : "err"}">${ok ? "✓" : "✗"}</span>`;
  const when = (ts) => ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + "Z" : "—";
  const refusal = (status, message) => `<div class="refusal">HTTP ${esc(status)} · ${esc(message)}</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Your team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Sandbox team</dt><dd>${st.sandbox ? `<span class="mono">${esc(st.sandbox)}</span> <span class="muted small">from the last run — Clean up deletes it</span>` : '<span class="muted">none — the run creates one</span>'}</dd>
          <dt>Last review</dt><dd>${st.has_report ? '<span class="muted">out/access-review.md</span>' : '<span class="muted">none yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.has_report ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1½ minutes · no LLM calls, nothing to wait for</span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key <span class="faint">(of your personal team)</span></label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    const p = P();
    return `<h1>Three AI assistants, one memory — and the server decides who may read or change which part.</h1>
      <p class="lead"><b>${esc(p?.company.name || "Corvane Mutual")}</b> (fictional) gives a claims assistant, an underwriting bot and the auditor's reviewer
      access to its memory: the underwriting rule book, the open claim files and the HR investigations file. The access policy says who may read and who may write
      what. This page builds it in a <b>sandbox org team</b> on your account — one virtual member and one API key per assistant — then has every assistant try
      everything with its own key, changes a grant, removes a member whose key leaked, and reads the audit log. Clean up deletes the sandbox team.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">The access policy</h2>${policyTable(p?.grants)}
          <p class="muted small" style="margin-bottom:0">An account may own one org team besides its personal team; if yours already has one, the run stops at step 2 and says so.
          Use case page: <a href="https://www.memorylake.ai/en/usecase/enterprise-ai-memory-deployment" target="_blank" rel="noopener">memorylake.ai › enterprise AI memory deployment</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }
  function policyTable(grants) {
    const p = P(); if (!p || !grants) return "";
    return `<table class="mx"><thead><tr><th></th>${p.projects.map((x) => `<th>${esc(x.name)}</th>`).join("")}</tr></thead><tbody>
      ${p.members.map((m) => `<tr><th>${esc(m.slug)}<div class="muted small" style="font-weight:400">${esc(m.purpose)}</div></th>${p.projects.map((x) => {
        const l = grants[m.slug]?.[x.slug]; return `<td class="${l ? "" : "none"}">${l ? `<span class="pill ${l === "write" ? "accent" : ""}">${esc(l)}</span>` : '<span class="faint">—</span>'}</td>`; }).join("")}</tr>`).join("")}</tbody></table>`;
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

  function memory() {
    const m = S.memory; if (!m) return empty("Memory");
    return `<h1>Corvane's memory — three projects</h1>
      <p class="lead">Written with the sandbox owner's key. None of the assistants has a key yet.</p>
      <div class="flow">${m.projects.map((p) => `<div class="card"><div class="row spread"><b>${esc(p.name)}</b><span class="pill">${p.facts.length} facts</span></div>
        <div class="muted small">${esc(p.description)}</div>${p.facts.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function policy() {
    const p = S.policy; if (!p) return empty("Members & policy");
    return `<h1>One member per assistant, limited by the policy, each with its own key</h1>
      <p class="lead"><code>member create</code> makes a virtual member (no login, acts only through API keys). A <code>tenant_member</code> reaches every project by default,
      so <code>member scope set</code> limits it to the projects the policy names — each with the actions of its level — and <code>access set-level</code> records the level.
      Then <code>api-key create --member</code> issues its key, which goes into its own CLI profile.</p>
      <div class="card">${policyTable(Object.fromEntries(p.members.map((r) => [r.member, r.grants])))}</div>
      <div class="flow">${p.members.map((r) => `<div class="card"><b>${esc(r.member)}</b><div class="muted small">${esc(r.purpose)}</div>
        <dl class="kv" style="margin-top:8px"><dt>member</dt><dd class="mono small">${esc(r.principal_id)}</dd><dt>key</dt><dd class="mono small">${esc(p.keys[r.member]?.name)} · ${esc(p.keys[r.member]?.prefix)}</dd>
        <dt>reason</dt><dd class="small">${esc(r.reason)}</dd></dl></div>`).join("")}</div>`;
  }

  function matrix() {
    const p = P(); if (!S.cells.length && !S.matrix) return empty("27 calls");
    const cell = (m, x, op) => S.cells.find((c) => c.member === m && c.project === x && c.op === op);
    const mm = S.matrix;
    return `<h1>Every assistant tries everything — with its own key</h1>
      <p class="lead">For each assistant × project: <code>fact list</code> (read), <code>search</code>, <code>fact add</code> (write). The server answers; the page only compares the answer with the policy.</p>
      ${mm ? `<div class="tally"><span class="pill ${mm.matched === mm.total ? "ok" : "err"}">${mm.matched}/${mm.total} as the policy says</span>${mm.refusals.map((r) => `<span class="pill">HTTP ${esc(r.status)} ${esc(r.code)}</span>`).join("")}</div>` : `<div class="tally"><span class="pill busy"><span class="spinner"></span> ${S.cells.length}/27 calls</span></div>`}
      <div class="card"><table class="mx"><thead><tr><th></th>${p.projects.map((x) => `<th>${esc(x.name)}</th>`).join("")}</tr></thead><tbody>
      ${p.members.map((m) => `<tr><th>${esc(m.slug)}</th>${p.projects.map((x) => {
        const lvl = p.grants[m.slug]?.[x.slug] || "none";
        return `<td class="${lvl === "none" ? "none" : ""}"><div class="lvl">policy: ${esc(lvl)}</div><div class="ops">${OPS.map((op) => {
          const c = cell(m.slug, x.slug, op);
          if (!c) return `<span class="op pending">${op}</span>`;
          return `<span class="op ${c.allowed ? "yes" : "no"} ${c.match ? "" : "diff"}" title="${esc(c.allowed ? c.detail : `HTTP ${c.status} ${c.message}`)}">${c.allowed ? "✓" : "✗"} ${op}</span>`; }).join("")}</div></td>`; }).join("")}</tr>`).join("")}
      </tbody></table></div>
      ${(() => { const r = S.cells.find((c) => !c.allowed); return r ? `<div class="card"><b>What a refusal looks like</b> <span class="muted small">${esc(r.member)} → ${esc(r.op)} ${esc(pname(r.project))}</span>${refusal(r.status, r.message)}</div>` : ""; })()}`;
  }

  function who() {
    const w = S.who; if (!w) return empty("Who reaches HR");
    return `<h1>Who can reach the HR file — and why</h1>
      <p class="lead"><code>access show project ${esc(w.project.id)}</code> lists every member that reaches the project, at what level, and where that comes from; and the members that cannot.</p>
      <div class="flow" style="grid-template-columns: minmax(0, 2fr) minmax(0, 1fr)"><div class="card"><b>Can reach</b><table class="tour"><thead><tr><th>member</th><th>level</th><th>role</th><th>via</th></tr></thead><tbody>
        ${w.allowed.map((a) => `<tr><td>${esc(a.name)}</td><td><span class="pill">${esc(a.level)}</span></td><td class="small">${esc(a.role)}</td><td class="small">${esc(a.sources.join(", "))}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><b>Cannot</b>${w.denied.length ? w.denied.map((d) => `<div class="memfact small"><span>${esc(d.name)} <span class="muted">(${esc(d.role)})</span></span></div>`).join("") : '<div class="muted small">—</div>'}
        <div class="row" style="margin-top:10px">${mark(w.ok)}<span class="small">${w.ok ? "only the auditor's reviewer, as the policy says" : "not what the policy says"}</span></div></div></div>`;
  }

  function change() {
    const c = S.change; if (!c) return empty("A grant changes");
    const lv = (e) => `<b>${esc(e.level)}</b> <span class="muted small">from ${esc(e.sources.map((s) => s.type + (s.levels ? ` (${s.levels.join("/")})` : "") + (s.type === "scope_grant" && s.actions?.[0] === "*" ? " — every action the role allows" : "")).join(", "))}</span>`;
    return `<h1>A grant changes — and the quick way grants more than the ticket asked</h1>
      <p class="lead">${esc(c.reason)}. The same key is used before and after; nothing is restarted.</p>
      <div class="flow">
        <div class="card"><b>Before</b>${refusal(c.before.status, c.before.message)}</div>
        <div class="card"><b>Quick: <code>access grant</code> + <code>set-level ${esc(c.level)}</code></b>
          <div class="small" style="margin-top:6px">search: ${c.after.rc === 0 ? "✓ " + esc(c.after.detail) : "✗"}</div>
          <div class="small">write: ${c.quick.write_rc === 0 ? '<span class="pill err">✓ allowed — not asked for</span>' : "✗ refused"}</div>
          <div class="small" style="margin-top:6px">access show: ${lv(c.quick.effective)}</div></div>
        <div class="card"><b>Exact: <code>member scope set</code> with the level's actions</b>
          <div class="small" style="margin-top:6px">search: ${c.exact.search_rc === 0 ? "✓" : "✗"}</div>
          <div class="small">write: ${c.exact.write_rc === 0 ? "✓ still allowed" : '<span class="pill ok">✗ refused</span>'}</div>
          ${c.exact.write_rc ? refusal(c.exact.write_status, c.exact.write_message) : ""}
          <div class="small" style="margin-top:6px">access show: ${lv(c.exact.effective)}</div></div></div>
      ${c.over_granted ? `<div class="card note"><b>Why</b> Levels only add. <code>access grant</code> put the project in the member's scope with every action its role allows — and a <code>tenant_member</code> may add memory — so the effective level was <b>write</b> while <code>set-level</code> said read. A scope entry that carries the level's actions is exact.</div>` : ""}`;
  }

  function remove() {
    const r = S.remove; if (!r) return empty("A key leaks");
    return `<h1>A key leaks: remove the member, the key is refused</h1>
      <p class="lead">${esc(r.reason)}. <code>member remove</code> disables every key the member has in this team.</p>
      <div class="flow">
        <div class="card"><b>Before</b><div class="big" style="margin-top:6px">${esc(r.before)} facts</div><div class="muted small">claim files read with its key</div></div>
        <div class="card"><b>${esc(r.refused.after_s)} s after <code>member remove</code></b>${r.refused.rc ? refusal(r.refused.status, r.refused.message) : '<div class="refusal">still accepted</div>'}</div>
        <div class="card"><b>What it wrote</b><div class="big" style="margin-top:6px">${esc(r.kept)}</div><div class="muted small">fact(s) with written_by=${esc(r.member)} stay in the claim files</div>
          <div class="small" style="margin-top:6px">roster: ${esc(r.roster.join(", "))}</div></div></div>`;
  }

  function audit() {
    const a = S.audit; if (!a) return empty("Audit log");
    return `<h1>The audit log: every access change, who made it, when</h1>
      <p class="lead"><code>audit-log list</code> in the sandbox team. Each event names the API key that made it and its target.
      ${a.has_reason ? "" : "The <code>--reason</code> given to scope and level changes is not in the event."} It records access changes, not memory reads or writes (${esc(a.data_events)} such events).</p>
      <div class="card"><table class="tour"><thead><tr><th>time (UTC)</th><th>action</th><th>by</th><th>key</th><th>target</th></tr></thead><tbody>
        ${a.rows.map((r) => `<tr><td class="mono small">${esc(when(r.at))}</td><td>${esc(r.action)}</td><td class="small">${esc(r.by)}</td><td class="mono small">${esc(r.key)}</td><td class="small">${esc(r.target)}</td></tr>`).join("")}</tbody></table></div>
      <div class="row"><button class="btn" data-act="audit" ${S.st.status === "running" ? "disabled" : ""}>Read the audit log again</button></div>`;
  }

  function report() {
    const r = S.report;
    if (!r) return `<h1>Access review</h1><div class="empty">${S.st.has_report ? '<button class="btn" data-act="loadreport">Load the last review</button>' : "Run the demo first."}</div>`;
    const m = r.matrix;
    return `<h1>${esc(r.company)} — AI memory access review</h1>
      <p class="lead">Generated ${esc(r.generated_at)} in sandbox team <span class="mono">${esc(r.sandbox.team_id)}</span>. The same review is in
      <code>out/access-review.md</code> (<a href="/api/report.md" target="_blank" rel="noopener">open</a>) and <code>.json</code>.</p>
      <div class="tally"><span class="pill ${m.matched === m.total ? "ok" : "err"}">${m.matched}/${m.total} calls as the policy says</span>
        <span class="pill">quick grant → ${esc(r.change.quick.effective.level)}, exact → ${esc(r.change.exact.effective.level)}</span>
        <span class="pill">removed key refused after ${esc(r.remove.refused.after_s)} s</span><span class="pill">${r.audit.rows.length} audit events</span></div>
      <div class="card"><table class="tour"><thead><tr><th>assistant</th><th>project</th><th>level</th>${OPS.map((o) => `<th>${o}</th>`).join("")}</tr></thead><tbody>
        ${P().members.map((mm) => P().projects.map((x) => { const cs = m.cells.filter((c) => c.member === mm.slug && c.project === x.slug);
          return `<tr><td>${esc(mm.slug)}</td><td>${esc(x.name)}</td><td>${esc(cs[0]?.level)}</td>${OPS.map((o) => { const c = cs.find((y) => y.op === o);
            return `<td>${c ? `<span class="op ${c.allowed ? "yes" : "no"} ${c.match ? "" : "diff"}">${c.allowed ? "✓" : "✗ " + esc(c.status)}</span>` : ""}</td>`; }).join("")}</tr>`; }).join("")).join("")}
      </tbody></table></div>`;
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
    Object.assign(S, { sandbox: null, memory: null, policy: null, cells: [], matrix: null, who: null, change: null, remove: null, audit: null, report: null });
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
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "intro") { /* the policy is in /api/data */ }
    else if (k === "cell") { S.cells.push(d); rerender = S.view === "matrix"; }
    else if (["sandbox", "memory", "policy", "matrix", "who", "change", "remove", "audit"].includes(k)) S[k] = d;
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
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "intro", "sandbox", "memory", "policy", "cell", "matrix", "who",
    "change", "remove", "audit", "report", "summary", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.matrix ? "matrix" : "connect");
    subscribe();
  })();
})();

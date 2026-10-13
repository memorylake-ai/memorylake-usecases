/* A team second brain with role-gated access — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "your personal team", view: "connect" },
    { n: 2, title: "Sandbox team", sub: "team create --issue-api-key", view: "sandbox" },
    { n: 3, title: "A custom role", sub: "role create · permission rule create", view: "role" },
    { n: 4, title: "Access groups", sub: "access-group create · explain", view: "groups" },
    { n: 5, title: "The second brain", sub: "each researcher, own key", view: "knowledge" },
    { n: 6, title: "27 calls", sub: "read · search · write", view: "matrix" },
    { n: 7, title: "A tidy-up leaks", sub: "role scope without actions", view: "leak" },
    { n: 8, title: "Mira leaves", sub: "member remove → 401", view: "departure" },
    { n: 9, title: "Audit log", sub: "audit-log list", view: "audit" },
  ];
  const OPS = ["read", "search", "write"];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    sandbox: null, role: null, groups: null, knowledge: null, cells: [], matrix: null, leak: null, departure: null, audit: null,
    report: null, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const T = () => S.data?.team;
  const pname = (slug) => T()?.projects.find((p) => p.slug === slug)?.name || slug;
  const mname = (slug) => T()?.members.find((m) => m.slug === slug)?.name || slug;
  const gname = (slug) => T()?.groups.find((g) => g.slug === slug)?.name || slug;
  function level(m, project) {
    const t = T(); if (!t) return "none";
    if (m.groups.some((g) => t.groups.find((x) => x.slug === g)?.project === project)) return "write";
    return t.role.scope.includes(project) ? "read" : "none";
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "report" ? "active" : ""}" data-view="report"><span class="n">≡</span><span class="t">Review<small>out/second-brain-review.md</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, sandbox, role, groups, knowledge, matrix, leak, departure, audit, report }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const mark = (ok) => `<span class="pill ${ok ? "ok" : "err"}">${ok ? "✓" : "✗"}</span>`;
  const when = (ts) => ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + "Z" : "—";
  const refusal = (status, message) => `<div class="refusal">HTTP ${esc(status)} · ${esc(message)}</div>`;

  function modelTable() {
    const t = T(); if (!t) return "";
    return `<table class="mx"><thead><tr><th></th>${t.projects.map((x) => `<th>${esc(x.name)}</th>`).join("")}</tr></thead><tbody>
      ${t.members.map((m) => `<tr><th>${esc(m.name)}<div class="muted small" style="font-weight:400">${esc(m.title)}</div></th>${t.projects.map((x) => {
        const l = level(m, x.slug); return `<td class="${l === "none" ? "none" : ""}">${l !== "none" ? `<span class="pill ${l === "write" ? "accent" : ""}">${esc(l)}</span>` : '<span class="faint">—</span>'}</td>`; }).join("")}</tr>`).join("")}</tbody></table>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Your team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Sandbox team</dt><dd>${st.sandbox ? `<span class="mono">${esc(st.sandbox)}</span> <span class="muted small">from the last run — Clean up deletes it</span>` : '<span class="muted">none — the run creates one</span>'}</dd>
          <dt>Last review</dt><dd>${st.has_report ? '<span class="muted">out/second-brain-review.md</span>' : '<span class="muted">none yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.has_report ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1½ minutes · no LLM calls</span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key <span class="faint">(of your personal team)</span></label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    const t = T();
    return `<h1>One memory for the whole lab — and access decided by role, not by who happens to ask.</h1>
      <p class="lead"><b>${esc(t?.company.name || "Thornfield Field Ecology Lab")}</b> (fictional) keeps its methods, field notes and funder reports in one MemoryLake memory.
      Mira, its most senior researcher, leaves at the end of the month; Theo joined this week. This page builds the lab's access model in a <b>sandbox org team</b> on your account —
      a custom role from permission rules, three access groups, one virtual member and one API key per researcher — then has everyone try everything with their own key,
      shows how a tidy-up of the role lets a group's write spill over, and removes Mira: her key stops, her knowledge stays. Clean up deletes the sandbox team.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">The access model</h2>${modelTable()}
          <p class="muted small" style="margin-bottom:0">Role “${esc(t?.role.label)}” reads ${esc((t?.role.scope || []).map(pname).join(" and "))}; each group adds write on one project.
          An account may own one org team besides its personal team; if yours already has one, the run stops at step 2 and says so.
          Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-second-brain-for-teams" target="_blank" rel="noopener">memorylake.ai › a second brain for your team</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
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

  function role() {
    const r = S.role; if (!r) return empty("A custom role");
    return `<h1>A custom role from permission rules: “${esc(r.label)}”</h1>
      <p class="lead"><code>role create</code> makes a role with no permissions at all. <code>permission rule create</code> adds what it may do — one rule per action —
      and <code>role scope set</code> limits it to two of the three projects, with a read-only action list on each. <span class="mono">${esc(r.reason)}</span></p>
      <div class="flow">
        <div class="card"><b>Role key</b><div class="big mono" style="margin-top:6px">${esc(r.key)}</div><div class="muted small">what <code>member create --role</code> takes</div></div>
        <div class="card"><b>Permissions</b><div style="margin-top:6px">${r.permissions.map((p) => `<span class="pill">${esc(p)}</span>`).join(" ")}</div>
          <div class="muted small" style="margin-top:6px"><code>search</code> also needs <code>workspace:memory_search</code> — the project actions alone are refused with “Search Workspace Memories”.</div></div>
        <div class="card"><b>Scope (${esc(r.scope_mode)})</b>${r.reach.map((x) => `<div class="memfact small"><span><b>${esc(pname(x.project))}</b> — ${x.actions.length} actions, read-only</span></div>`).join("")}
          <div class="memfact small"><span class="faint">${esc(T().projects.filter((p) => !r.reach.some((x) => x.project === p.slug)).map((p) => p.name).join(", "))} — not in scope</span></div></div></div>`;
  }

  function groups() {
    const g = S.groups; if (!g) return empty("Access groups");
    return `<h1>Access groups add the rest: field crew, method stewards, grant team</h1>
      <p class="lead">Each group carries write permissions and, through its scope, one project. <code>access-group member add</code> puts researchers in it.
      <code>access-group explain</code> and <code>member explain</code> say where every permission comes from.</p>
      <div class="flow">${g.groups.map((x) => `<div class="card"><div class="row spread"><b>${esc(x.name)}</b><span class="pill accent">write</span></div>
        <div class="muted small">opens ${esc(pname(x.project))}</div><div class="small" style="margin-top:6px">${x.members.map((m) => `<span class="pill">${esc(mname(m))}</span>`).join(" ")}</div></div>`).join("")}</div>
      <div class="flow" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr)">
        <div class="card"><b>member explain — Mira</b><table class="tour"><thead><tr><th>permission</th><th>from</th></tr></thead><tbody>
          ${g.mira.permissions.map((p) => `<tr><td class="mono small">${esc(p.permission)}</td><td class="small">${esc(p.sources.map((x) => x === "role" ? "role" : gname(x)).join(", "))}</td></tr>`).join("")}</tbody></table>
          <div class="small" style="margin-top:8px">${g.mira.reach.map((r) => `<div>${esc(pname(r.project))} <span class="muted">via ${esc(r.sources.map((x) => x === "role" ? "role" : gname(x)).join(", "))}</span></div>`).join("")}</div></div>
        <div class="card"><b>The access model</b>${modelTable()}
          <div class="small" style="margin-top:8px">${g.members.map((m) => `<div><b>${esc(m.name)}</b> <span class="mono muted">${esc(m.key.name)} · ${esc(m.key.prefix)}</span></div>`).join("")}</div></div></div>`;
  }

  function knowledge() {
    const k = S.knowledge; if (!k) return empty("The second brain");
    return `<h1>The second brain fills up — each researcher writes with their own key</h1>
      <p class="lead">Mira writes the methods and decisions she carries in her head; Theo the week's field notes; Asha the funder report terms.
      The server records no writer on a fact, so the demo keeps the ids each one wrote.</p>
      <div class="flow">${k.items.map((i) => `<div class="card"><div class="row spread"><b>${esc(pname(i.project))}</b><span class="pill">${i.facts.length} facts</span></div>
        <div class="muted small">written by ${esc(mname(i.member))}</div>${i.facts.map((f) => `<div class="memfact small"><span>${esc(f.text)}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function matrix() {
    const t = T(); if (!S.cells.length && !S.matrix) return empty("27 calls");
    const cell = (m, x, op) => S.cells.find((c) => c.member === m && c.project === x && c.op === op);
    const mm = S.matrix;
    return `<h1>Every researcher tries everything — with their own key</h1>
      <p class="lead">For each researcher × project: <code>fact list</code> (read), <code>search</code>, <code>fact add</code> (write). The server answers; the page only compares the answer with the access model.</p>
      ${mm ? `<div class="tally"><span class="pill ${mm.matched === mm.total ? "ok" : "err"}">${mm.matched}/${mm.total} as the model says</span>${mm.refusals.map((r) => `<span class="pill">HTTP ${esc(r.status)} ${esc(r.code)}</span>`).join("")}</div>` : `<div class="tally"><span class="pill busy"><span class="spinner"></span> ${S.cells.length}/27 calls</span></div>`}
      <div class="card"><table class="mx"><thead><tr><th></th>${t.projects.map((x) => `<th>${esc(x.name)}</th>`).join("")}</tr></thead><tbody>
      ${t.members.map((m) => `<tr><th>${esc(m.name)}</th>${t.projects.map((x) => {
        const lvl = level(m, x.slug);
        return `<td class="${lvl === "none" ? "none" : ""}"><div class="lvl">model: ${esc(lvl)}</div><div class="ops">${OPS.map((op) => {
          const c = cell(m.slug, x.slug, op);
          if (!c) return `<span class="op pending">${op}</span>`;
          return `<span class="op ${c.allowed ? "yes" : "no"} ${c.match ? "" : "diff"}" title="${esc(c.allowed ? c.detail : `HTTP ${c.status} ${c.message}`)}">${c.allowed ? "✓" : "✗"} ${op}</span>`; }).join("")}</div></td>`; }).join("")}</tr>`).join("")}
      </tbody></table></div>
      ${(() => { const r = S.cells.find((c) => !c.allowed && c.member === "theo"); return r ? `<div class="card"><b>What a refusal looks like</b> <span class="muted small">${esc(mname(r.member))} → ${esc(r.op)} ${esc(pname(r.project))}</span>${refusal(r.status, r.message)}</div>` : ""; })()}`;
  }

  function leak() {
    const l = S.leak; if (!l) return empty("A tidy-up leaks");
    const src = (xs) => xs.map((x) => x === "role" ? "role" : gname(x)).join(", ") || "—";
    return `<h1>A tidy-up of the role scope lets a group's write land on the methods</h1>
      <p class="lead">${esc(l.ticket)}. The same two projects stay in the role's scope, only the per-project action lists go.
      Theo is in the Field crew, whose write permission is meant for the field notes.</p>
      <div class="flow">
        <div class="card"><b>Without action lists</b><div class="small" style="margin-top:6px">Theo → write ${esc(pname(l.project))}:</div>
          ${l.leaked ? `<div><span class="pill err">✓ allowed — nobody gave him this</span></div><div class="muted small" style="margin-top:4px">${esc(l.leak.detail)}</div>` : refusal(l.leak.status, l.leak.message)}
          <div class="small" style="margin-top:8px"><code>member explain</code>: <span class="mono">project:mem_add</span> from <b>${esc(src(l.mem_add_sources))}</b>; ${esc(pname(l.project))} reached via <b>${esc(src(l.reach_sources))}</b></div></div>
        <div class="card"><b>Action lists back</b><div class="small" style="margin-top:6px">Theo → write ${esc(pname(l.project))}:</div>
          ${l.fixed.rc ? `<span class="pill ok">✗ refused</span>${refusal(l.fixed.status, l.fixed.message)}` : '<span class="pill err">still allowed</span>'}
          <div class="small" style="margin-top:6px">field notes: ${l.field_notes_rc === 0 ? "✓ still allowed" : "✗"}</div></div></div>
      <div class="card note"><b>Why</b> A scope instance without <code>actions</code> gets every action the member holds — from the role <i>or any access group</i>. The Field crew's
      <code>mem_add</code> was scoped to the field notes, but once the role's instances carried no action list, it applied to every project the role reaches. Keep an action list on every instance.</div>`;
  }

  function departure() {
    const d = S.departure; if (!d) return empty("Mira leaves");
    return `<h1>Mira leaves: her key stops, her knowledge stays</h1>
      <p class="lead">${esc(d.ticket)}. <code>member remove</code> disables her key in this team and takes her out of every group. What she wrote is the lab's, not hers.</p>
      <div class="flow">
        <div class="card"><b>Before</b><div class="big" style="margin-top:6px">${esc(d.before)} facts</div><div class="muted small">${esc(pname("methods"))}, read with her key</div></div>
        <div class="card"><b>${esc(d.refused.after_s)} s after <code>member remove</code></b>${d.refused.rc ? refusal(d.refused.status, d.refused.message) : '<div class="refusal">still accepted</div>'}
          <div class="small" style="margin-top:6px">${Object.entries(d.groups_after).map(([g, v]) => `<div>${esc(gname(g))}: ${esc(v.members)} member(s)${v.still_listed ? " — still lists her" : ""}</div>`).join("")}</div></div>
        <div class="card"><b>What she wrote</b><div class="big" style="margin-top:6px">${esc(d.kept)}</div><div class="muted small">of ${esc(d.facts)} facts in ${esc(pname("methods"))}</div></div></div>
      <div class="card"><b>Theo asks the second brain</b> <span class="muted small">search ${esc(pname("methods"))}, rank 1</span>
        ${d.answers.map((a) => `<div class="memfact"><span>${mark(a.found)} <b>${esc(a.q)}</b><div class="small" style="margin-top:4px">${esc(a.text)}</div>
          ${a.writer ? `<div class="muted small">written by ${esc(mname(a.writer))}${a.writer === d.member ? " — who has left" : ""}</div>` : ""}</span></div>`).join("")}</div>`;
  }

  function audit() {
    const a = S.audit; if (!a) return empty("Audit log");
    return `<h1>The audit log: role, group and member changes</h1>
      <p class="lead"><code>audit-log list</code> in the sandbox team. The custom role is <code>custom_role:create</code>, every permission rule a <code>policy:create</code>,
      a role scope change <code>member:set_scope</code>; group membership events name the group, not who joined it.</p>
      <div class="card"><table class="tour"><thead><tr><th>time (UTC)</th><th>action</th><th>by</th><th>target</th></tr></thead><tbody>
        ${a.rows.map((r) => `<tr><td class="mono small">${esc(when(r.at))}</td><td>${esc(r.action)}</td><td class="small">${esc(r.by)}</td><td class="small">${esc(r.target)}</td></tr>`).join("")}</tbody></table></div>
      <div class="row"><button class="btn" data-act="audit" ${S.st.status === "running" ? "disabled" : ""}>Read the audit log again</button></div>`;
  }

  function report() {
    const r = S.report;
    if (!r) return `<h1>Review</h1><div class="empty">${S.st.has_report ? '<button class="btn" data-act="loadreport">Load the last review</button>' : "Run the demo first."}</div>`;
    const m = r.matrix;
    return `<h1>${esc(r.company)} — second brain access review</h1>
      <p class="lead">Generated ${esc(r.generated_at)} in sandbox team <span class="mono">${esc(r.sandbox.team_id)}</span>. The same review is in
      <code>out/second-brain-review.md</code> (<a href="/api/report.md" target="_blank" rel="noopener">open</a>) and <code>.json</code>.</p>
      <div class="tally"><span class="pill ${m.matched === m.total ? "ok" : "err"}">${m.matched}/${m.total} calls as the model says</span>
        <span class="pill">tidy-up: ${r.leak.leaked ? "leaked write" : "no leak"} → ${r.leak.ok ? "closed" : "open"}</span>
        <span class="pill">Mira's key refused after ${esc(r.departure.refused.after_s)} s</span><span class="pill">${r.departure.answers.filter((a) => a.found).length}/${r.departure.answers.length} answers</span>
        <span class="pill">${r.audit.rows.length} audit events</span></div>
      <div class="card"><table class="tour"><thead><tr><th>researcher</th><th>project</th><th>model</th>${OPS.map((o) => `<th>${o}</th>`).join("")}</tr></thead><tbody>
        ${T().members.map((mm) => T().projects.map((x) => { const cs = m.cells.filter((c) => c.member === mm.slug && c.project === x.slug);
          return `<tr><td>${esc(mm.name)}</td><td>${esc(x.name)}</td><td>${esc(cs[0]?.level)}</td>${OPS.map((o) => { const c = cs.find((y) => y.op === o);
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
    Object.assign(S, { sandbox: null, role: null, groups: null, knowledge: null, cells: [], matrix: null, leak: null, departure: null, audit: null, report: null });
  }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the sandbox team the demo created (its projects, role, groups, members and keys)? Your own team is not touched.")) return;
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
    else if (k === "intro") { /* the access model is in /api/data */ }
    else if (k === "cell") { S.cells.push(d); rerender = S.view === "matrix"; }
    else if (["sandbox", "role", "groups", "knowledge", "matrix", "leak", "departure", "audit"].includes(k)) S[k] = d;
    else if (k === "report") S.report = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the sandbox team, its role, groups, members and keys are deleted. Your own team was not touched.", true); }
      else if (d.job === "run" && S.caughtUp) { banner("Done. The sandbox team is still there: read its audit log again, or Clean up to delete it.", true); loadReport(); }
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && rerender) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); if (S.view === "connect") renderStage(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "intro", "sandbox", "role", "groups", "knowledge", "cell", "matrix",
    "leak", "departure", "audit", "report", "summary", "cleaned", "done", "error"];
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

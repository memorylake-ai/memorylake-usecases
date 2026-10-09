/* Onboarding memory for HR teams — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const kindOf = (name) => name.endsWith(".pdf") ? "PDF" : name.endsWith(".md") ? "Markdown" : "File";
  const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
  const STATUS = { current: ["✓", "current", "in force"], stale: ["✗", "stale", "superseded"], scheduled: ["…", "scheduled", "not in force yet"], later: ["·", "scheduled", "issued later"] };
  const vtag = (status, label) => `<span class="vtag v-${STATUS[status][1]}">${STATUS[status][0]} ${esc(label || STATUS[status][2])}</span>`;
  const who = (key) => S.data?.hires[key] || S.data?.people[key] || { display: key, role: "" };

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "New hires & scopes", sub: "actors with stage tags, two projects", view: "setup" },
    { n: 3, title: "Handbook & memos", sub: "Library + recursive import", view: "library" },
    { n: 4, title: "Policy versions", sub: "every version pinned, stamped", view: "policies" },
    { n: 5, title: "Pre-boarding chat", sub: "memory about Priya", view: "chat" },
    { n: 6, title: "Current answers", sub: "old + new come back; stamps decide", view: "answers" },
    { n: 7, title: "Audit", sub: "what was in force on a date", view: "audit" },
    { n: 8, title: "Day one", sub: "actor update · plan per role", view: "dayone" },
    { n: 9, title: "Onboarding brief", sub: "Priya's, with sources", view: "brief" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "uploaded", "tree", "imported",
    "documents", "policies", "session", "turn", "cooked", "memory", "answer", "audit", "stage", "plans", "brief", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, projects: {},
    uploaded: {}, imports: {}, documents: null, pinned: false,
    sessions: {}, memory: null,
    answers: {},               // question -> answer event
    audit: null, stages: {}, plans: null, brief: null,
    ask: { q: "", hire: "priya-nair", asOf: "", html: "" },
    auditDate: "",
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.uploaded = {}; S.imports = {}; S.documents = null; S.pinned = false; S.sessions = {}; S.memory = null;
    S.answers = {}; S.audit = null; S.stages = {}; S.plans = null; S.brief = null;
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
        <span class="n">?</span><span class="t">Ask the policies<small>as a new hire, on any date</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  function renderStage() {
    const r = { connect, setup, library, policies, chat, answers, audit, dayone, brief, ask }[S.view];
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
          <dt>Projects</dt><dd>${hasProjects() ? Object.values(S.projects).map((p) => `${esc(p.name)} <span class="mono muted">${esc(p.id)}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd>
        </dl>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${hasProjects() ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>mlu-ohr-</code></span>
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
      <h1>Onboarding memory that answers with the policy in force</h1>
      <p class="lead">Fernhill Robotics changed its parental leave from 16 to 20 weeks in July, its travel meal allowance twice, and has signed off a 25-day vacation allowance that only starts in January.
      The January handbook still says the old numbers. Two people start on October 12. Every policy version is kept as a fact stamped with its effective date, sign-off and source —
      so a new hire's question gets the version in force, stale ones are marked, an audit can ask what applied on any date, and the engineer and the intern get different first-day plans.
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
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/onboarding-memory-for-hr-teams" target="_blank" rel="noopener">memorylake.ai › Onboarding memory for HR teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p>
        </div>
      </div>`;
  }

  function personCard(p, a, extra) {
    const isAI = p.type === "ASSISTANT";
    const tags = a?.tags || [];
    return `<div class="card person"><span class="avatar ${isAI ? "pm" : "analyst"}">${initials(p.display)}</span>
      <div><div class="name">${esc(p.display)} ${isAI ? '<span class="pill">ASSISTANT</span>' : ""}</div><div class="role">${esc(p.role)}</div>
      ${tags.length ? `<div class="meta">${tags.map((t) => `<span class="pill ${t.startsWith("stage:") ? "accent" : ""}">${esc(t)}</span>`).join("")}</div>` : ""}
      ${extra || ""}</div>
      <span class="status pill ${a ? "ok" : ""}">${a ? "actor ready" : "pending"}</span></div>`;
  }

  const metaLine = (m) => m && Object.keys(m).length ? `<div class="mono faint small" style="margin-top:4px">metadata ${esc(JSON.stringify(m))}</div>` : "";

  function setup() {
    if (!S.data) return "";
    return `
      <h1>Two new hires, the onboarding assistant, two projects</h1>
      <p class="lead">Each new hire is an <b>actor</b>: the onboarding stage is a <b>tag</b> (<code>stage:pre-boarding</code>), so <code>actor list --tags</code> finds who starts today;
      the role, start date and manager are <b>metadata</b>. Policies live in one project; the pre-boarding chat in another, so facts extracted from the chat never land among the signed-off policy text.</p>
      <h2>New hires</h2>
      <div class="grid">${Object.values(S.data.hires).map((h) => personCard(h, S.actors[h.key], metaLine(S.actors[h.key]?.metadata || h.metadata))).join("")}</div>
      <h2>Assistant</h2>
      <div class="grid">${Object.values(S.data.people).map((p) => personCard(p, S.actors[p.key])).join("")}</div>
      <h2>Projects</h2>
      ${Object.values(S.data.projects).map((r) => `<div class="card row spread"><div><b>${esc(r.name)}</b>
        <div class="muted small">${esc(r.description)} · custom id <code>${esc(r.custom_id)}</code>${S.projects[r.key] ? ` · <span class="mono">${esc(S.projects[r.key].id)}</span>` : ""}</div></div>
        <span class="pill ${S.projects[r.key] ? "ok" : ""}">${S.projects[r.key] ? "ready" : "pending"}</span></div>`).join("")}`;
  }

  function library() {
    if (!S.data) return "";
    const imp = S.imports.policies; const docs = S.documents || [];
    return `
      <h1>The January handbook and both policy memos</h1>
      <p class="lead">The files are mirrored into the Library under <code>${esc(S.data.library_root)}/</code>, then one <code>proj doc import &lt;folder&gt; --recursive --wait</code> brings all three into the policies project.
      The handbook is the stale one: it still says 16 weeks of parental leave and USD 50 for meals. It stays — people will keep finding it — and step 6 shows what happens when they do.</p>
      <div class="card tree"><div class="mono"><b>${esc(S.data.library_root)}/</b></div>
        ${S.data.files.map((f) => { const up = S.uploaded[f.path]; return `<details class="tree-file"><summary>
          <span class="kind k-${kindOf(f.path).toLowerCase()}">${kindOf(f.path)}</span>
          <a class="mono" href="/library/${esc(f.path)}" target="_blank" rel="noopener">${esc(f.path)}</a>
          <span class="faint small">${kb(f.size)}</span><span class="pill ${up ? "ok" : ""}" style="margin-left:auto">${up ? "uploaded" : "pending"}</span></summary>
          ${f.text ? `<pre>${esc(f.text)}</pre>` : `<p class="muted small">PDF — MemoryLake parses it on import.</p>`}</details>`; }).join("")}
      </div>
      <div class="card"><div class="row spread"><div><b>${esc(S.data.projects.policies.name)}</b> <span class="faint small mono">import handbook/ --recursive --wait</span></div>
        ${imp ? `<span class="pill ok">${imp.success_count} new · ${imp.duplicate_count} already there · ${imp.failure_count} failed</span>`
          : S.stepStatus[3] === "running" ? `<span class="pill busy"><span class="spinner"></span> parsing…</span>` : `<span class="pill">pending</span>`}</div>
        ${imp?.expanded ? `<div class="expanded mono">${esc(imp.expanded)}</div>` : ""}
        ${docs.length ? `<ul class="doclist">${docs.map((d) => `<li><span class="kind k-${kindOf(d.name).toLowerCase()}">${kindOf(d.name)}</span> <span class="mono">${esc(d.name)}</span> <span class="pill ${d.status === "okay" ? "ok" : ""}">${esc(d.status)}</span></li>`).join("")}</ul>` : ""}
      </div>`;
  }

  function policies() {
    if (!S.data) return "";
    const today = S.data.today;
    return `
      <h1>Every version of every policy, pinned and stamped</h1>
      <p class="lead">One <code>fact add --project</code> per batch. Facts are immutable: a new version is a new fact, the old one stays — that is the audit trail.
      Each fact starts with a stamp (<code>POL-LEAVE v2 · … · effective 2026-07-01 · signed off 2026-06-12 by Dana Whitfield · source policy-update-memo-2026-06.md §1</code>) that the assistant reads back to pick a version.</p>
      ${S.data.policies.map((p) => `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(p.title)} <span class="mono faint small">${esc(p.id)}</span></h3>
        <span class="pill ${S.pinned ? "ok" : ""}">${S.pinned ? `${p.versions.length} pinned` : "pending"}</span></div>
        <div class="vline">${p.versions.map((v, i) => { const next = p.versions[i + 1];
          const st = v.effective > today ? "scheduled" : next && next.effective <= today ? "stale" : "current";
          return `<div class="ver v-${st}"><div class="ver-head"><b>v${v.v}</b> <span class="mono small">effective ${esc(v.effective)}</span> ${vtag(st, st === "current" ? "in force today" : st === "stale" ? `superseded ${next.effective}` : "not in force yet")}</div>
            <div>${esc(v.body)}</div><div class="faint small">signed off ${esc(v.signed)} by ${esc(v.signer)} · ${esc(v.source)} ${esc(v.section)}</div></div>`; }).join("")}</div></div>`).join("")}
      <h2>Onboarding flows, per role</h2>
      <div class="cols">${["engineer", "intern"].map((r) => `<div class="col"><b>${r}</b>${S.data.flows.filter((f) => f.role === r).map((f) => `<div class="fact ${S.pinned ? "pinned" : ""}"><span><b>${esc(f.stage)}</b> — ${esc(f.body)}</span><span class="tag">${S.pinned ? "pinned" : "pending"}</span></div>`).join("")}</div>`).join("")}</div>`;
  }

  function chat() {
    if (!S.data) return "";
    const c = S.data.sessions[0]; const st = S.sessions[c.custom_id] || {};
    const stored = st.stored || 0; const total = c.turns.length;
    const cook = st.cooked || (stored >= total && S.memory) ? `<span class="pill ok">memory ready</span>`
      : stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
      : stored ? `<span class="pill accent">${stored}/${total} messages stored</span>` : `<span class="pill">queued</span>`;
    const priya = S.memory?.find((m) => m.title.startsWith("Priya"));
    const policyScope = S.memory?.find((m) => m.title.startsWith("Fernhill people"));
    return `
      <h1>Priya's pre-boarding chat becomes memory about her</h1>
      <p class="lead">A DIRECT conversation between Priya and the onboarding assistant in the chats project, time-stamped to October 5. MemoryLake extracts what she said into facts owned by <b>her</b> actor —
      so step 6 can show what it knows about her next to an answer, and the brief can list it.</p>
      <div class="card"><div class="call-head"><h3>${esc(c.name)}</h3><span class="muted small">${fmtDate(c.date)}</span><span style="margin-left:auto">${cook}</span></div>
        <div class="turns">${c.turns.map(([k, text], i) => `<div class="turn ${i < stored ? "stored" : ""}"><span class="avatar ${k === "assistant" ? "pm" : "analyst"}">${initials(who(k).display)}</span>
          <div><div class="who">${esc(who(k).display)}</div><div class="what">${esc(text)}</div></div><span class="mark">${i < stored ? "✓ stored" : "msg " + (i + 1)}</span></div>`).join("")}</div></div>
      <h2>What each scope remembers</h2>
      ${S.memory ? `<div class="cols">
        <div class="col"><div class="row spread"><b>${esc(priya?.title || "Priya Nair")}</b><span class="muted small">${priya?.facts.length || 0} facts</span></div>
          ${(priya?.facts || []).map((f) => `<div class="fact"><span>${esc(f.fact)}</span><span class="tag">extracted</span></div>`).join("") || '<p class="muted">nothing yet</p>'}</div>
        <div class="col"><div class="row spread"><b>${esc(policyScope?.title || "")}</b><span class="muted small">${policyScope?.facts.length || 0} facts</span></div>
          <p class="small">${policyScope ? `${policyScope.facts.filter((f) => f.pinned).length} of ${policyScope.facts.length} are the pinned versions and flows, word for word — the chat added nothing here.` : ""}</p></div></div>`
        : `<div class="empty">Shows up once the chat is processed.</div>`}`;
  }

  function verRow(r) {
    const label = r.status === "current" ? `in force · signed off ${r.signed} by ${r.signer}` : r.status === "stale" ? (r.by ? `superseded by v${r.by.v} on ${r.by.effective}` : "superseded") : `signed off ${r.signed}, not in force yet`;
    return `<div class="ver v-${r.status}"><div class="ver-head"><b>v${r.v}</b> <span class="mono small">effective ${esc(r.effective)}</span> ${vtag(r.status, label)}</div>
      <div>${esc(r.body)}</div>${r.status === "current" ? `<div class="faint small">source ${esc(r.source)} ${esc(r.section)} ${r.source_ok ? "· ✓ in the policies project" : ""}</div>` : ""}</div>`;
  }

  function answerCard(a) {
    return `<div class="card"><div class="row spread"><h3 style="margin:0">${esc(a.question)}</h3><span class="muted small">${esc(who(a.hire).display)} · as of ${esc(a.as_of)}</span></div>
      ${a.policy ? `<div class="faint small" style="margin:6px 0">search returned ${a.rows.length} version(s) of <span class="mono">${esc(a.policy)}</span> ${esc(a.title || "")}${a.docs.length ? ` and ${a.docs.length} source document(s)` : ""}${a.later ? ` · ${a.later} signed off after ${esc(a.as_of)}, not shown` : ""}</div>` : ""}
      <div class="vline">${a.rows.map(verRow).join("")}</div>
      ${a.docs.map((d) => `<div class="docrow">${vtag(d.status, d.status === "current" ? "source of the version in force" : d.status === "stale" ? "stale document" : d.status === "later" ? `issued after ${a.as_of}` : "source of a version not in force yet")} <span class="mono">${esc(d.name)}</span>
        <span class="faint small">cites v${d.versions.join(", v")}</span></div>`).join("")}
      ${a.about.length ? `<div class="about"><b>About ${esc(who(a.hire).display.split(" ")[0])}:</b>${a.about.map((m) => `<div>${esc(m.fact)}</div>`).join("")}</div>` : ""}
      ${a.left_out ? `<div class="faint small">${a.left_out} other hit(s) not about this policy, left out</div>` : ""}
      ${a.answer ? `<div class="action ok">→ ${esc(a.answer.body)} <span class="mono small">[${esc(a.policy)} v${a.answer.v}, effective ${esc(a.answer.effective)}]</span></div>` : `<div class="action escalate">No version in force was retrieved.</div>`}</div>`;
  }

  function answers() {
    const list = (S.data?.questions || []).map((q) => S.answers[q.q]).filter(Boolean);
    if (!list.length) return `<h1>Current answers</h1><div class="empty">Run the demo first — Priya's questions run once the chat is processed.</div>`;
    const stale = list.reduce((n, a) => n + a.rows.filter((r) => r.status === "stale").length + a.docs.filter((d) => d.status === "stale").length, 0);
    return `
      <h1>Priya asks — old and new versions come back; the stamps pick the one in force</h1>
      <p class="lead">One <code>memorylake search</code> per question, scoped <code>--projects &lt;policies&gt; --actors &lt;Priya&gt;</code>. MemoryLake keeps every version, so the 16-week and the 20-week leave both come back, and so does the January handbook.
      The assistant reads each fact's stamp: the latest version whose effective date has passed is the answer; older ones, and documents that only back them, are marked stale and not used.</p>
      ${list.map(answerCard).join("")}
      <div class="isolation ok">✓ ${list.filter((a) => a.answer).length} of ${list.length} answered with the version in force · ${stale} stale version(s) or document(s) came back and were marked, not used</div>`;
  }

  function auditTable(t) {
    return `<div class="card"><h3 style="margin-top:0">In force on ${esc(t.date)}</h3><table class="audit"><thead><tr><th>Policy</th><th>Version</th><th>Rule</th><th>Signed off · source</th></tr></thead><tbody>
      ${t.rows.map((r) => `<tr><td><b>${esc(r.title)}</b><div class="mono faint small">${esc(r.policy)}</div></td><td>${r.v ? `v${r.v}<div class="faint small">since ${esc(r.effective)}</div>` : "—"}</td>
        <td>${esc(r.body || "none yet")}${r.scheduled.length ? `<div class="faint small">… scheduled: ${r.scheduled.map((s) => `v${s.v} from ${esc(s.effective)}`).join("; ")}</div>` : ""}</td>
        <td class="small">${r.v ? `${esc(r.signed)} · ${esc(r.signer)}<div class="mono faint">${esc(r.source)}</div>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function audit() {
    const a = S.audit;
    return `
      <h1>Audit — what policy was in force on a given day?</h1>
      <p class="lead"><code>fact list --projects &lt;policies&gt;</code> returns every version; for each policy the version in force is the latest one whose effective date is on or before the day asked about.
      A version signed off after that day is not shown as "scheduled" — it did not exist yet.</p>
      <div class="card"><form class="ask-form" id="audit-form"><input id="audit-date" type="date" value="${esc(S.auditDate || S.data?.audit_date || "")}" ${hasProjects() ? "" : "disabled"}>
        <button class="btn primary" ${hasProjects() ? "" : "disabled"}>Audit this date</button></form>
        <div class="chips">${["2025-12-01", "2026-03-15", "2026-05-01", "2026-08-01", "2027-02-01"].map((d) => `<button type="button" class="chip" data-d="${d}">${d}</button>`).join("")}</div></div>
      ${a ? a.tables.map(auditTable).join("") + (a.changed?.length ? `<div class="isolation ok">Changed between ${esc(a.tables[0].date)} and ${esc(a.tables[a.tables.length - 1].date)}: ${a.changed.map(esc).join(", ")}</div>` : "")
        : `<div class="empty">Run the demo first.</div>`}`;
  }

  function dayone() {
    if (!S.data) return "";
    const p = S.plans;
    return `
      <h1>Day one — one question, a plan per role</h1>
      <p class="lead"><code>actor update --tags new-hire,stage:day-1 --metadata {…}</code> moves both hires on. Both flags <b>replace</b> what is stored, so the demo always sends the full set.
      Then <code>actor list --tags stage:day-1</code> finds who starts today, and each of them asks the same question; the plan is the flow whose role matches their <code>metadata.role</code>.</p>
      <div class="grid">${Object.values(S.data.hires).map((h) => { const st = S.stages[h.key]; const a = S.actors[h.key];
        return personCard(h, a && { ...a, tags: [] }, `<div class="meta">${(st ? ["new-hire", "stage:day-1"] : a?.tags || ["new-hire", "stage:pre-boarding"]).map((t) => `<span class="pill ${t.startsWith("stage:") ? "accent" : ""}">${esc(t)}</span>`).join("")}
          ${st ? '<span class="pill ok">updated</span>' : ""}</div>`); }).join("")}</div>
      ${p ? `<p class="small">stage:day-1 now: <b>${p.starting.map(esc).join(", ")}</b></p>
        <div class="cols">${p.plans.map((x) => `<div class="col"><div class="row spread"><b>${esc(x.display)}</b><span class="pill">metadata.role=${esc(x.role)}</span></div>
          <div class="faint small">Q: ${esc(S.data.first_day_q)}</div>
          ${x.plan ? `<ol class="plan">${x.plan.split(/;\s*/).map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : '<p class="muted">no plan came back</p>'}
          ${x.left_out.length ? `<div class="faint small">also retrieved, left out: ${x.left_out.map(esc).join(", ")}</div>` : ""}</div>`).join("")}</div>
        <div class="isolation ${p.verdict.startsWith("✓") ? "ok" : "bad"}">${esc(p.verdict)}</div>` : `<div class="empty">Runs after the audit.</div>`}`;
  }

  function mdToHtml(md) {
    return md.split("\n").map((l) => l.startsWith("# ") ? `<h1>${esc(l.slice(2))}</h1>` : l.startsWith("## ") ? `<h2>${esc(l.slice(3))}</h2>`
      : l.startsWith("- ") ? `<li>${inline(l.slice(2))}</li>` : l.trim() ? `<p>${inline(l)}</p>` : "").join("");
  }
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])_(.+?)_(?=$|[^*])/g, "$1<i>$2</i>");

  function brief() {
    const b = S.brief;
    if (!b) return `<h1>Onboarding brief</h1><div class="empty">Run the demo first — the brief is written at the end.</div>`;
    return `<div class="brief-head"><div><h1 style="margin:0">Priya's onboarding brief</h1>
        <p class="lead" style="margin:4px 0 0">Every answer is the version in force today, with its sign-off and source; stale versions are named so nobody quotes them back. Saved as <code>out/${esc(b.filename)}</code>.</p></div>
        <button class="btn" data-act="download">Download .md</button></div>
      <div class="card brief">${mdToHtml(b.markdown)}</div>`;
  }

  function ask() {
    const hires = S.data ? Object.values(S.data.hires) : [];
    const sugg = ["parental leave", "meal allowance on a trip", "vacation days", "home office equipment", "receipts for meals"];
    const ready = hasProjects();
    return `
      <h1>Ask the policies</h1>
      <p class="lead">The same search as step 6, as the new hire you pick — and on the date you pick. Leave the date empty for today; set it to 2026-03-15 and the January answers come back as the ones in force.</p>
      <div class="card"><form class="ask-form" id="ask-form">
          <select id="ask-hire">${hires.map((h) => `<option value="${esc(h.key)}" ${S.ask.hire === h.key ? "selected" : ""}>${esc(h.display)}</option>`).join("")}</select>
          <input id="ask-date" type="date" value="${esc(S.ask.asOf)}" title="answer as of (empty = today)" ${ready ? "" : "disabled"}>
          <input id="ask-q" placeholder="e.g. how long is parental leave" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
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
    stage.querySelector('[data-act="download"]')?.addEventListener("click", () => {
      const blob = new Blob([S.brief.markdown], { type: "text/markdown" }); const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = S.brief.filename; a.click(); URL.revokeObjectURL(a.href);
    });
    const af = stage.querySelector("#audit-form");
    if (af) {
      af.addEventListener("submit", (e) => { e.preventDefault(); runAudit($("#audit-date").value); });
      stage.querySelectorAll(".chip[data-d]").forEach((c) => c.addEventListener("click", () => { $("#audit-date").value = c.dataset.d; runAudit(c.dataset.d); }));
    }
    stage.querySelector("#ask-hire")?.addEventListener("change", (e) => { S.ask.hire = e.target.value; });
    stage.querySelector("#ask-date")?.addEventListener("change", (e) => { S.ask.asOf = e.target.value; });
    const form = stage.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      stage.querySelectorAll(".chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }

  async function runAudit(d) {
    if (!d) return;
    S.auditDate = d;
    const today = S.data.today;
    const r = await api("/api/audit", { dates: d === today ? [d] : [d, today] });
    if (r) { S.audit = { tables: r.tables, changed: r.tables.length > 1 ? r.tables[0].rows.filter((x, i) => x.v !== r.tables[1].rows[i].v).map((x) => x.policy) : [] }; if (S.view === "audit") renderStage(); }
  }

  async function askQuery(q) {
    if (!q) return;
    S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, hire: S.ask.hire, as_of: S.ask.asOf });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = r.policy ? answerCard(r) : `<p class="muted">No policy matched that question.</p>`;
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
    if (!confirm("Delete both demo projects, the pre-boarding chat, the demo actors (with what was remembered about the new hires) and the Library folder from your account?")) return;
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
      if (d.index === 2 && d.total === STEPS.length) resetRun();
      term("step", ev.text);
      if (S.follow && d.total === STEPS.length && stepOf(d.index)) show(stepOf(d.index).view);
    } else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "actor") S.actors[d.key] = d;
    else if (k === "project") S.projects[d.key] = { id: d.id, name: d.name };
    else if (k === "uploaded") S.uploaded[d.path] = true;
    else if (k === "tree") (d.files || []).forEach((f) => (S.uploaded[f] = true));
    else if (k === "imported") S.imports[d.project] = d;
    else if (k === "documents") S.documents = d.docs;
    else if (k === "policies") S.pinned = true;
    else if (k === "session") S.sessions[d.custom_id] = { id: d.id, stored: d.done || 0, cooked: false };
    else if (k === "turn") (S.sessions[d.custom_id] ||= {}).stored = d.index;
    else if (k === "cooked") Object.values(S.sessions).forEach((c) => { if (c.id === d.id) c.cooked = true; });
    else if (k === "memory") S.memory = d.scopes;
    else if (k === "answer") { if ((S.data?.questions || []).some((q) => q.q === d.question) && d.as_of === S.data.today) S.answers[d.question] = d; }
    else if (k === "audit") S.audit = d;
    else if (k === "stage") { S.stages[d.key] = d.stage; if (S.actors[d.key]) S.actors[d.key].tags = ["new-hire", `stage:${d.stage}`]; }
    else if (k === "plans") S.plans = d;
    else if (k === "brief") S.brief = d;
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.projects = {}; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Priya's brief is ready; audit any date or ask as either new hire.", true);
      term("dim", `── ${d.job} finished`);
      S.st.status = "done"; setConn();
      if (S.caughtUp) refreshState();
    } else if (k === "error") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "error"; });
      term("error", ev.text); if (S.caughtUp) banner(ev.text);
      S.st.status = "error"; setConn(); refreshState();
    }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "uploaded", "turn"].includes(k)) {
      renderSteps(); if (!(S.view === "ask" && k === "answer") && !(S.view === "audit" && k === "audit")) renderStage();
    } else if (S.caughtUp && (k === "uploaded" || k === "turn") && S.view !== "ask") renderStage();
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

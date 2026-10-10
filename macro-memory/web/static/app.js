/* Macro memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (iso) => iso ? new Date(iso).toISOString().slice(0, 19).replace("T", " ") + "Z" : "";
  const body = (t) => String(t || "").split("): ").slice(1).join("): ") || String(t || "");   // drop "Macro 'X' (Tool): "

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Support KB", sub: "policy v3 · proj doc import", view: "kb" },
    { n: 3, title: "Import macros", sub: "Zendesk + Intercom · fact add", view: "macros" },
    { n: 4, title: "Detector report", sub: "m2d · self · m2m", view: "detector" },
    { n: 5, title: "Triage", sub: "four resolve strategies", view: "triage" },
    { n: 6, title: "Policy v4", sub: "the detector stays quiet", view: "swap" },
    { n: 7, title: "Re-verify", sub: "re-file dependent macros", view: "reverify" },
    { n: 8, title: "Support AI", sub: "by name · by question", view: "ai" },
    { n: 9, title: "Audit", sub: "fact conflict get", view: "audit" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    policy: null, macros: {}, detector: null, settled: [], open: null, swap: null, reverify: null, answers: null, audit: null,
    waiting: null, ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const pb = () => S.data?.playbook;
  const macroDef = (slug) => (S.data?.macros || []).find((m) => m.slug === slug);
  const title = (slug) => macroDef(slug)?.title || slug;
  const toolTag = (tool) => `<span class="tooltag ${esc(String(tool).toLowerCase())}">${esc(tool)}</span>`;
  const SECTION = { refunds: "Refunds", shipping: "Shipping", phone: "Phone support", damaged: "Damaged items" };
  // The policy section a macro depends on, cut out of the conflict's document excerpt (same as demo.py's excerpt()).
  function excerpt(text, slug) {
    for (const t of macroDef(slug)?.depends_on || []) {
      const h = SECTION[t]; if (!h) continue;
      const m = new RegExp(`##\\s*${h}\\s*\\n([\\s\\S]+?)(?:\\n\\s*##|$)`).exec(text || "");
      if (m) return `${h}: ${m[1].trim()}`;
    }
    return String(text || "").replace(/#/g, "").trim();
  }
  const catPill = (c) => `<span class="pill ${c === "m2d" ? "accent" : c === "self" ? "warn" : "err"}">${esc(c)}</span>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the KB<small>which macro would the assistant use?</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, kb, macros, detector, triage, swap, reverify, ai, audit, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">${S.st.status === "running" ? "Working on it — this view fills in when the step finishes." : "Run the demo first — this view fills in as it runs."}</div>`;
  const waitPill = (what) => S.waiting === what ? `<p><span class="pill busy"><span class="spinner"></span> ${esc(S.waitText || "waiting…")}</span></p>` : "";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Support KB</dt><dd>${S.st.ids?.project ? `<span class="mono muted">${esc(S.st.ids.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5–6 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Saved replies that stay true to the policy — and get flagged the moment they don't.</h1>
      <p class="lead"><b>Larkspur Gear</b> (fictional) has 7 support macros in two help desks: 4 in Zendesk, 3 in Intercom. Support lead <b>Priya Raman</b> moves them into one MemoryLake project,
      next to the support policy. Each macro becomes one fact; MemoryLake's <b>contradiction detector</b> (<code>fact conflict</code>) checks every write against the policy (<code>m2d</code>),
      against itself (<code>self</code>) and against the other macros (<code>m2m</code>). Priya settles each conflict — then the policy changes, and you see what re-flags the macros that depend on it.
      This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the support KB and the uploaded policies.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/macro-and-template-memory-for-support-teams" target="_blank" rel="noopener">memorylake.ai › macro and template memory for support teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function kb() {
    if (!S.data) return "";
    const p = S.policy;
    return `<h1>The support policy — the source of truth for every macro</h1>
      <p class="lead">A project is the support knowledge base. The current policy (v3) goes in as a document: <code>lib upload</code> → <code>proj doc import --wait</code>.
      Every macro written to this project is checked against it.</p>
      <div class="card"><div class="row spread"><b>📄 ${esc(p?.document?.name || "policy-v3.md")}</b>${p ? `<span class="mono small faint">${esc(p.document.id || "")}</span>` : '<span class="pill">pending</span>'}</div>
        <pre class="policy" style="margin-top:10px">${esc(S.data.policy.current)}</pre></div>`;
  }

  function macroCard(m, extra) {
    const got = S.macros[m.slug];
    return `<div class="card macro ${extra?.cls || ""} ${got ? "" : "faded"}"><div class="row spread"><b>${esc(m.title)}</b><span>${toolTag(m.tool)} <span class="faint small">#${esc(m.source_id)}</span></span></div>
      <div class="macro-text">${esc(m.body)}</div>
      <div class="row spread small" style="margin-top:8px"><span class="muted">depends on: ${m.depends_on.map((d) => `<code>${esc(d)}</code>`).join(" ")} · used ${esc(m.uses)}× in 30 days</span>
        <span class="mono faint">${got ? esc(got.fact_id) : "queued"}</span></div>${extra?.html || ""}</div>`;
  }
  function macros() {
    if (!S.data) return "";
    const n = Object.keys(S.macros).length;
    return `<h1>Import macros — one fact each, from both help desks</h1>
      <p class="lead">Each macro is stored as one fact (<code>fact add --project</code>) named after the macro, with its source tool, id and the policy sections it depends on in the fact's
      metadata (<code>fact update --metadata</code>). They go in one every ${S.data.gap}s: back-to-back writes are checked for contradictions minutes later, as a batch; one at a time, each is checked as it lands.</p>
      <p><span class="pill ${n === 7 ? "ok" : "busy"}">${n}/7 imported</span></p>
      <div class="grid">${S.data.macros.map((m) => macroCard(m)).join("")}</div>`;
  }

  function conflictCard(c, slug) {
    const others = c.macros.filter((s) => s !== slug);
    return `<div class="card conflict"><div class="row spread"><div>${catPill(c.category)} <b>${esc(c.name)}</b></div><span class="mono small faint">${esc(c.id)}</span></div>
      <p class="small" style="margin-bottom:4px">${esc(c.description)}</p>
      ${others.length ? `<div class="small muted">with: ${others.map((s) => `<b>${esc(title(s))}</b>`).join(", ")}</div>` : ""}
      ${c.chunks.map((k) => `<blockquote class="excerpt"><div class="small faint">📄 ${esc(k.document)} — the policy excerpt the server matched</div>${esc(excerpt(k.text, slug))}</blockquote>`).join("")}</div>`;
  }
  function detector() {
    const d = S.detector;
    if (!d) return S.waiting === "detector" ? `<h1>Detector report</h1>${waitPill("detector")}` : empty("Detector report");
    const plan = pb().macros;
    return `<h1>Detector report — ${d.conflicts.length} conflict(s) on 7 macros</h1>
      <p class="lead"><code>fact conflict list --project …</code>: the server compared each macro with the policy and with the other macros as it was written.
      Names and descriptions are written by the server's model and change from run to run; categories and fact ids are what the demo checks.</p>
      <p><span class="pill ${d.planned === d.want ? "ok" : "warn"}">${d.planned}/${d.want} planned (macro, category) pairs raised</span>
        ${d.extra.length ? `<span class="pill warn">${d.extra.length} not in the plan — shown, left for review</span>` : ""}</p>
      <div class="grid">${S.data.macros.map((m) => {
        const mine = d.conflicts.filter((c) => c.macros.includes(m.slug));
        const exp = plan[m.slug]?.expect || [];
        const got = [...new Set(mine.map((c) => c.category))].sort();
        const asPlanned = JSON.stringify(got) === JSON.stringify([...exp].sort());
        return macroCard(m, { cls: mine.length ? "flagged" : "clean", html: `<div class="row" style="margin-top:8px">${mine.length ? got.map(catPill).join(" ") : '<span class="pill ok">✓ clean</span>'}
            <span class="small ${asPlanned ? "muted" : "warn"}">${asPlanned ? "as planned" : `planned: ${exp.join(", ") || "clean"}`}</span></div>
          ${mine.map((c) => conflictCard(c, m.slug)).join("")}` });
      }).join("")}</div>`;
  }

  function settledCard(x) {
    const head = `<div class="row spread"><div>${catPill(x.category)} → <code>${esc(x.strategy)}</code> <b>${esc(title(x.macro))}</b></div><span class="pill ${x.ok ? "ok" : "err"}">${x.ok ? "✓" : "✗"}</span></div>
      <p class="small muted" style="margin:6px 0 0">${esc(x.why)}</p>`;
    if (x.strategy === "edit_fact") return `<div class="card">${head}
      <div class="label">before</div><div class="macro-text old">${esc(body(x.before))}</div>
      <div class="label">after — replaced in place, same fact id <span class="mono">${esc(x.fact_id)}</span></div><div class="macro-text">${esc(body(x.after))}</div></div>`;
    if (x.strategy === "trust_document") return `<div class="card">${head}
      ${(x.chunks || []).map((k) => `<blockquote class="excerpt"><div class="small faint">📄 ${esc(k.document)}</div>${esc(excerpt(k.text, x.macro))}</blockquote>`).join("")}
      <div class="label">version 1 — forgotten <span class="mono">${esc((x.forgotten || []).join(", "))}</span></div><div class="macro-text old">${esc(body(x.before))}</div>
      <div class="label">version 2 — published <span class="mono">${esc(x.fact_id)}</span>, detector: ${x.ok ? "no conflicts" : "raised a conflict"}</div><div class="macro-text">${esc(body(x.after))}</div></div>`;
    if (x.strategy === "trust_fact") return `<div class="card">${head}
      ${(x.chunks || []).map((k) => `<blockquote class="excerpt"><div class="small faint">📄 ${esc(k.document)} — behind: the new hours go live with v4</div>${esc(excerpt(k.text, x.macro))}</blockquote>`).join("")}
      <div class="label">what Priya approved</div><div class="macro-text">${esc(body(x.before))}</div>
      ${x.rewritten && x.rewritten !== x.before ? `<div class="label">what <code>trust_fact</code> left — the server rewrote the macro to record the decision</div><div class="macro-text server">${esc(body(x.rewritten))}</div>
        <p class="small">A macro is pasted into replies word for word, so the approved wording goes back with <code>fact update --text</code>.</p>` : ""}
      <div class="label"><code>fact trace</code> — every version of this macro</div>
      <div class="trace">${(x.trace || []).map((e) => `<div><b>${esc(e.event)}</b> ${fmtTime(e.timestamp)} · ${esc(e.source_kind)} — ${esc(body(e.new_fact))}</div>`).join("")}</div>
      <p class="small" style="margin-bottom:0">${x.ok ? "✓ the macro reads exactly as approved" : "✗ the macro text differs from the approved wording"}</p></div>`;
    return `<div class="card">${head}
      <p class="small">${x.stale ? `<span class="pill warn">stale</span> found with <code>fact conflict list --stale true</code> — one of its macros changed after it was raised.` : "Not marked stale yet."}
      <code>dismiss</code> closes it and changes no fact: the Trailhead Plus macro stays exactly as it is.</p>
      ${(x.facts || []).map((f) => `<div class="memfact small"><span>${esc(f.text)}</span></div>`).join("")}</div>`;
  }
  function triage() {
    if (!S.settled.length && !S.open) return empty("Triage");
    return `<h1>Triage — each conflict, settled the way its category allows</h1>
      <p class="lead"><code>fact conflict resolve --strategy …</code>: <b>self</b> → <code>edit_fact</code> (replace the text in place) ·
      <b>m2d</b> → <code>trust_document</code> (forget the macro, publish version 2) or <code>trust_fact</code> (the macro is right, the policy is behind) ·
      <b>m2m</b> → <code>keep_fact</code> or <code>dismiss</code>.</p>
      ${S.settled.map(settledCard).join("")}
      ${S.open ? `<h2>Open after triage: ${S.open.length}</h2>${S.open.map((c) => `<div class="memfact"><span>${catPill(c.category)} ${esc(c.name)}</span><span class="when mono">${esc(c.id)}</span></div>`).join("")}` : ""}`;
  }

  function swap() {
    const s = S.swap;
    if (!s) return S.waiting === "swap" ? `<h1>Policy v4 replaces v3</h1>${waitPill("swap")}` : empty("Policy v4");
    const changed = pb().policy.changed_sections;
    return `<h1>Policy v4 replaces v3 — and the detector stays quiet</h1>
      <p class="lead">v3 leaves the KB (<code>proj doc delete</code>), v4 (effective ${esc(pb().policy.next_effective)}) goes in. Free shipping now starts at 50 dollars; phone hours are longer.</p>
      <div class="grid3"><div class="card"><div class="big">${s.new_conflicts.length}</div><div class="muted small">new conflicts in the ${s.watched}s after v4 landed</div></div>
        <div class="card" style="grid-column: span 2"><b>Why:</b> the detector checks what is written, when it is written. Macros already in the KB are not re-checked against a new document —
        so “free over 75 dollars” sits there, unflagged. (A <code>fact update</code> does not re-run it either.)</div></div>
      <h2>Macros that depend on a changed section <span class="muted small">${changed.map((c) => `<code>${esc(c)}</code>`).join(" ")}</span></h2>
      <div class="grid">${S.data.macros.filter((m) => m.depends_on.some((d) => changed.includes(d))).map((m) => macroCard(m)).join("")}</div>
      <h2>📄 ${esc(s.added)}</h2><pre class="policy">${esc(S.data.policy.next)}</pre>`;
  }

  function reverify() {
    const r = S.reverify;
    if (!r) return S.waiting === "reverify" ? `<h1>Re-verify</h1>${waitPill("reverify")}` : empty("Re-verify");
    return `<h1>Re-verify — re-file what depends on the change, and the detector answers</h1>
      <p class="lead">Each dependent macro is written again (<code>fact add</code> the same text, then <code>fact delete</code> the old copy), so the detector checks it against v4.</p>
      <div class="grid">${r.macros.map((m) => { const mine = r.conflicts.filter((c) => c.macros.includes(m.slug)); const def = macroDef(m.slug);
        return macroCard(def, { cls: mine.length ? "flagged" : "clean", html: `<div class="row" style="margin-top:8px">${mine.length ? mine.map((c) => catPill(c.category)).join(" ") : '<span class="pill ok">✓ clean against v4</span>'}</div>${mine.map((c) => conflictCard(c, m.slug)).join("")}` }); }).join("")}</div>
      ${r.fixed.map((f) => `<h2>${esc(title(f.macro))} — <code>trust_document</code>, version ${f.version}</h2><div class="card"><p class="small muted" style="margin-top:0">${esc(f.why)}</p>
        <div class="macro-text old">${esc(body(f.before))}</div><div class="macro-text">${esc(body(f.after))}</div>
        <p class="small" style="margin-bottom:0">${f.ok ? "✓ the detector raised no conflict on the new version" : "✗ the detector raised a conflict on the new version"}</p></div>`).join("")}`;
  }

  const metaLine = (m) => m ? `v${esc(m.version)} · ${toolTag(m.tool)} #${esc(m.source_id)} · checked against <code>${esc(m.checked_against)}</code>${m.approved_exception ? ` · <span class="faint">${esc(m.approved_exception)}</span>` : ""}` : "";
  function ai() {
    const a = S.answers; if (!a) return empty("Support AI");
    return `<h1>Support AI — the current macro, every time</h1>
      <p class="lead">At reply time an assistant either asks for a macro by name (<code>fact list</code>, read by <code>metadata.macro</code>) or searches the KB with the customer's question
      (<code>search --types fact</code>) and takes the top macro hit. Forgotten and deleted versions never come back.</p>
      ${a.map((x) => `<div class="card"><div class="row spread"><b>“${esc(x.q)}”</b><span class="pill ${x.ok ? "ok" : "err"}">${x.ok ? "✓" : "✗"} ${esc(title(x.slug))} · rank ${esc(x.rank)}</span></div>
        <div class="macro-text">${esc(body(x.text))}</div></div>`).join("")}
      <p class="muted small">${a.filter((x) => x.ok).length}/${a.length} answered with the expected macro.</p>`;
  }

  function audit() {
    const a = S.audit; if (!a) return empty("Audit");
    return `<h1>Audit — every conflict, how it was settled</h1>
      <p class="lead"><code>fact conflict get</code> keeps the strategy, when, what was forgotten — and a snapshot of the macro's exact words when it was flagged. Written to <code>${esc(a.file)}</code>.</p>
      ${a.resolved.map((c) => { const rs = c.resolve || {}; return `<div class="card"><div class="row spread"><div>${catPill(c.category)} <b>${esc(c.name)}</b> <span class="muted small">${c.macros.map((s) => esc(title(s))).join(" · ")}</span></div><span class="pill ok">${esc(rs.strategy)}</span></div>
        <dl class="kv small" style="margin-top:8px"><dt>raised</dt><dd>${fmtTime(c.created_at)}</dd><dt>resolved</dt><dd>${fmtTime(rs.created_at)}</dd></dl>
        ${c.facts.map((f) => { const gone = (rs.forgotten_fact_ids || []).includes(f.id); return `<div class="memfact small ${gone ? "gone" : ""}"><span>${esc(f.text)}</span><span class="when">${gone ? "forgotten" : "snapshot"}</span></div>`; }).join("")}</div>`; }).join("") || '<div class="empty">No resolved conflicts yet.</div>'}
      ${a.open.length ? `<h2>Still open</h2>${a.open.map((c) => `<div class="memfact"><span>${catPill(c.category)} ${esc(c.name)}</span><span class="when mono">${esc(c.id)}</span></div>`).join("")}` : ""}
      <h2>The log <span class="muted small">${esc(a.file)}</span></h2><pre class="instr log">${esc(a.markdown)}</pre>`;
  }

  function ask() {
    const ready = !!S.st.ids?.project;
    const qs = ["A customer's order came to 60 dollars. Do they pay for shipping?", "Can someone call me on Saturday?", "My tent arrived with a torn seam", "I never got the password email", "Refund 20 days after delivery?"];
    return `<h1>Ask the KB</h1>
      <p class="lead">The lookup a support assistant makes at reply time: <code>search --projects &lt;KB&gt; --types fact</code>, keep the macro hits, use the top one.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="a customer question" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
        <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${qs.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
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
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askKB($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askKB(c.dataset.q); }));
    }
  }
  async function askKB(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = r.fact ? `<div class="card"><div class="row spread"><b>${esc(r.fact.metadata?.title)}</b><span class="pill accent">rank ${esc(r.rank)} of ${esc(r.hits)} fact hits</span></div>
        <div class="macro-text">${esc(body(r.fact.text))}</div><div class="small muted" style="margin-top:8px">${metaLine(r.fact.metadata)}</div></div>`
      : `<div class="empty">No macro among the ${esc(r.hits)} hits.</div>`;
    if (document.body.contains(box)) box.innerHTML = S.ask.html; else if (S.view === "ask") renderStage();
  }
  async function api(path, b) {
    try { const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) });
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
  function resetView() { S.policy = null; S.macros = {}; S.detector = null; S.settled = []; S.open = null; S.swap = null; S.reverify = null; S.answers = null; S.audit = null; S.waiting = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the support KB (macros, policy documents, conflicts) and the uploaded policy files from your account?")) return;
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
  const WAIT_FOR = { 4: "detector", 6: "swap", 7: "reverify" };
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView();
      S.waiting = WAIT_FOR[d.index] || null; S.waitText = "working…";
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") { term("progress", ev.text.trim()); S.waitText = ev.text.trim().replace(/^…\s*/, ""); if (S.caughtUp && S.waiting && S.view === { detector: "detector", swap: "swap", reverify: "reverify" }[S.waiting]) renderStage(); }
    else if (k === "json") term("dim", ev.text);
    else if (k === "text") { /* the CLI's own summary lines; the views show the same data */ }
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "existing") S.st.ids = d;
    else if (k === "policy") { S.policy = d; S.st.ids = { project: d.project.id }; }
    else if (k === "macro") S.macros[d.slug] = d;
    else if (k === "detector") { S.detector = d; S.waiting = null; }
    else if (k === "settled") S.settled.push(d);
    else if (k === "triage") S.open = d.open;
    else if (k === "swap") { S.swap = d; S.waiting = null; }
    else if (k === "reverify") { S.reverify = d; S.waiting = null; }
    else if (k === "answers") S.answers = d.answers;
    else if (k === "audit") S.audit = d;
    else if (k === "done") {
      finishRunning("done"); S.waiting = null; term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the support KB and the uploaded policies are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Open Triage for the four strategies, or try “Ask the KB”.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); S.waiting = null; term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "policy", "macro", "detector", "settled", "triage",
    "swap", "reverify", "answers", "audit", "cleaned", "done", "error"];
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
    if (state.status !== "running") { Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; }); S.waiting = null; }
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.audit ? "triage" : "connect");
    subscribe();
  })();
})();

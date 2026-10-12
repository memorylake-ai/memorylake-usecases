/* Patient context memory for clinicians — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one actor per patient", view: "setup" },
    { n: 3, title: "Chart history", sub: "signed, sourced, paged", view: "chart" },
    { n: 4, title: "Last week", sub: "urgent care, a new allergy", view: "lastweek" },
    { n: 5, title: "Reconcile", sub: "the stale allergy line", view: "reconcile" },
    { n: 6, title: "Order check", sub: "today's draft vs his chart", view: "check" },
    { n: 7, title: "Exact vs semantic", sub: "--query and search", view: "exact" },
    { n: 8, title: "Decision record", sub: "what the scribe saw", view: "record" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, chart: null, uc: null, waiting: false, detector: null, turns: [], dictation: null,
    reconciled: null, check: null, exact: null, record: null,
    ask: { q: "", html: "" }, extra: "", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>exact and semantic</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, chart, lastweek, reconcile, check, exact, record, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Walter's chart</dt><dd>${st.ready ? "from an earlier run — Run again reads it back, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3–4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Last week's allergy, in front of today's order.</h1>
      <p class="lead">Brightwater Family Practice keeps one memory per patient. Walter Brandt's chart has said <b>no known drug allergies</b> since 2018.
      On 2026-10-05 an urgent care clinic gave him cefalexin and his lips swelled. Today the AI scribe drafts an antibiotic for Dr. Okafor —
      and checks it against his <b>whole</b> chart with <code>fact list --query</code> before it goes out. This page drives the <code>memorylake</code> CLI for real.
      All people and records are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the dictated note, the clinic project, Walter's chart and the urgent care physician.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/patient-context-memory-for-clinicians" target="_blank" rel="noopener">memorylake.ai › patient context memory for clinicians</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("A clinic project, one actor per patient");
    const w = story().clinicians.whitaker;
    return `<h1>A clinic project, one actor per patient</h1>
      <p class="lead">Walter's chart lives on his own actor (<code>actor create --tags patient</code>), so every clinician and tool reads the same memory for him and nobody else's.</p>
      <div class="grid">
        <div class="card person"><span class="avatar user">WB</span><div><div class="name">${esc(su.patient.name)}</div><div class="role">${esc(su.patient.summary)}</div><div class="mono small faint">${esc(su.ids.patient)}</div></div><span class="status pill ok">his chart</span></div>
        <div class="card person"><span class="avatar">SW</span><div><div class="name">${esc(w.name)}</div><div class="role">${esc(w.role)}</div><div class="mono small faint">${esc(su.ids.whitaker)}</div></div><span class="status pill">speaks in a dictated note</span></div>
        <div class="card"><h2 style="margin-top:0">${esc(su.project)}</h2><div class="muted small">clinic-wide: dictated notes, order-check records</div><div class="mono small faint">${esc(su.ids.project)}</div></div>
      </div>`;
  }

  const entryRow = (i) => `<tr><td class="small">${esc(i.kind)}</td><td>${esc(i.text)}</td><td class="small muted">${esc(i.clinician || "extracted")}</td><td class="small mono">${esc(i.date || "")}</td></tr>`;
  function chart() {
    const c = S.chart; if (!c) return empty("Chart history");
    const cur = c.items.filter((i) => i.entry === "current"), ev = c.items.filter((i) => i.entry === "event").sort((a, b) => (a.date || "").localeCompare(b.date || ""));
    return `<h1>His chart, signed and sourced — read back page by page</h1>
      <p class="lead">Events (visits, labs) carry their date in the text. Current status (allergy list, problems, medications) does not — its date, clinician and source are in <code>metadata</code>.
      That split matters in step 4: the contradiction detector treats dated statements as history.</p>
      <div class="card"><h2 style="margin-top:0"><code>fact list --actors … --page-size 5</code></h2>
        <div class="chips">${c.pages.map((p) => `<span class="chip">page ${p.n}: ${p.count} ${p.token ? "→ token" : "· token null"}</span>`).join("")}</div>
        ${c.pages.length > 1 && c.pages[c.pages.length - 1].count === 0 ? '<p class="muted small" style="margin-bottom:0">The last full page still came with a token; the page after it is empty. Read until the token is null.</p>' : ""}</div>
      <div class="card"><h2 style="margin-top:0">Current status</h2><table class="iso"><tr><th>kind</th><th>entry</th><th>signed by</th><th>date</th></tr>${cur.map(entryRow).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Timeline</h2><table class="iso"><tr><th>kind</th><th>entry</th><th>signed by</th><th>date</th></tr>${ev.map(entryRow).join("")}</table></div>`;
  }

  function conflictCard(c, note) {
    return `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(c.name)}</h2><span class="pill ${c.resolved ? "ok" : "warn"}">${c.category} · ${c.resolved ? "resolved" : "open"}</span></div>
      <div class="mono small faint">${esc(c.id)}</div><p class="small">${esc(c.description)}</p>
      ${c.facts.map((f) => `<div class="memfact"><span class="mk">•</span><span class="body">${esc(f.text)}<div class="trace">${esc(f.clinician ? `${f.clinician}, ${f.date}` : "")}</div></span></div>`).join("")}
      ${note ? `<p class="muted small" style="margin-bottom:0">${note}</p>` : ""}</div>`;
  }
  function lastweek() {
    const u = S.uc; if (!u) return empty("Last week — urgent care");
    const d = S.detector;
    const det = d ? (d.conflicts.length ? d.conflicts.map((c) => conflictCard(c, d.fresh ? `raised ${d.waited}s after the allergy was written` : "raised in an earlier run")).join("")
        + d.others.map((c) => conflictCard(c, "also raised — not planned by the story, shown, not hidden")).join("")
      : `<div class="card"><span class="pill err">nothing raised${d.fresh ? ` within ${d.waited}s` : ""}</span> <span class="small muted">the detector sometimes answers minutes later; <code>python3 demo.py conflicts</code></span></div>`)
      : S.waiting ? `<div class="card"><span class="pill busy"><span class="spinner"></span> waiting for the contradiction detector (up to ${S.data?.detect_wait || 150}s)…</span></div>` : "";
    const turns = S.turns.length ? `<div class="card"><h2 style="margin-top:0">Dictated note <span class="muted small">(DIRECT conversation, clinic project)</span></h2>
      <div class="bubbles">${S.turns.map((t) => `<div class="bubble ${t.who === "walter" ? "user" : "bot"}">${esc(t.line)}</div>`).join("")}</div>
      ${S.dictation ? `<h2>Extracted: ${S.dictation.extracted.length} fact(s)</h2>${S.dictation.extracted.map((e) => `<div class="memfact ${e.mentions ? "new" : ""}"><span class="mk">${e.mentions ? "•" : "·"}</span><span class="body">${esc(e.text)}<div class="trace">${esc(e.scope)}</div></span></div>`).join("")}
        <p class="muted small" style="margin-bottom:0">Shown, not relied on: extraction varies run to run. The order check works from the signed entries.</p>` : '<p><span class="pill busy"><span class="spinner"></span> turning the note into memory…</span></p>'}</div>` : "";
    return `<h1>Last week — urgent care records a new allergy</h1>
      <p class="lead">${esc(story().urgent_care.place)}, ${esc(story().urgent_care.date)}. The visit and triage notes are dated events; the allergy goes on his current allergy list.
      MemoryLake checks every fact written to his actor against what the actor already holds.</p>
      <div class="grid3">${u.entries.map((e) => `<div class="card"><span class="pill ${e.kind === "allergy" ? "err" : ""}">${esc(e.kind)}</span><p>${esc(e.text)}</p><div class="trace">${esc(e.clinician)}, ${esc(e.date)}</div></div>`).join("")}</div>
      <h2>The contradiction detector</h2>${det}${turns}`;
  }

  function reconcile() {
    const r = S.reconciled; if (!r) return empty("Reconcile");
    const text = {};
    [...(S.detector?.conflicts || []), ...(S.detector?.others || [])].forEach((c) => c.facts.forEach((f) => (text[f.id] = f.text)));
    return `<h1>Reconcile — retire the stale line, keep every record</h1>
      <p class="lead"><code>fact conflict resolve --strategy keep_fact</code> keeps one fact and forgets <b>every other fact in the conflict</b>.
      On the two-fact conflict that retires exactly the stale “no known drug allergies” line. On the conflict that also names the visit and triage notes, keeping any one would forget a record that supports the allergy — so it is dismissed instead (dismiss changes no fact).</p>
      ${r.resolved.map((x) => `<div class="card mine"><div class="row spread"><h2 style="margin:0">keep_fact</h2><span class="mono small">${esc(x.id)}</span></div>
        <div class="memfact clean"><span class="mk">✓</span><span class="body">kept: ${esc(text[x.kept] || x.kept)}</span></div>
        ${x.forgotten.map((f) => `<div class="memfact gone"><span class="mk">✗</span><span class="body">forgotten: ${esc(text[f] || f)}</span></div>`).join("")}</div>`).join("")}
      ${r.dismissed.map((x) => `<div class="card"><div class="row spread"><h2 style="margin:0">dismissed, not keep_fact</h2><span class="mono small">${esc(x.id)}</span></div>
        <p class="small">${x.would_forget.length ? "keep_fact here would also have forgotten:" : "the NKDA line it names was already retired by the two-fact conflict."}</p>
        ${x.would_forget.map((f) => `<div class="memfact personal"><span class="mk">!</span><span class="body">${esc(text[f] || f)}<div class="leakwhy">↳ a record that supports the allergy</div></span></div>`).join("")}</div>`).join("")}
      ${r.left_open.length ? `<div class="card"><span class="pill warn">${r.left_open.length} left open</span></div>` : ""}
      ${r.trail.map((t) => `<div class="card"><h2 style="margin-top:0">${t.conflict ? "Forgotten, not erased" : "Rewritten, not erased"}</h2>
        <p>“${esc(t.text)}”</p><div class="trace">${t.conflict ? `kept word for word by <code>fact conflict get ${esc(t.conflict)}</code> · ` : ""}<code>fact trace</code>: ${t.history.slice().reverse().map((h) => `${esc(h.action)} (${esc(h.source)})`).join(" → ")}</div></div>`).join("")}
      <div class="card"><h2 style="margin-top:0">His allergy list now</h2>${r.allergies.map((a) => `<div class="memfact new"><span class="mk">•</span><span class="body">${esc(a.text)}<div class="trace">${esc(a.clinician)}, ${esc(a.date)} · ${esc(a.source)}</div></span></div>`).join("")}</div>`;
  }

  function checkCard(r, cutoff) {
    const seen = cutoff ? r.hits.filter((h) => h.date && h.date <= cutoff) : r.hits;
    const ev = seen.filter((h) => h.evidence);
    const v = ev.length ? "✗" : seen.length ? "!" : "✓";
    const cls = { "✗": "err", "!": "warn", "✓": "ok" }[v];
    return `<div class="card ${v === "✗" ? "" : "mine"}"><div class="row spread"><h2 style="margin:0">${esc(r.order.drug)} <span class="muted small">${esc(r.order.dose)}</span></h2><span class="pill ${cls}">${v} ${esc((S.data?.verdict || {})[v] || "")}</span></div>
      <div class="small muted" style="margin:6px 0">${esc(r.order.class)} · looked for ${r.terms.map((t) => `<code>${esc(t)}</code>`).join(" ")}</div>
      ${seen.map((h) => `<div class="memfact ${h.evidence ? "personal" : ""}"><span class="mk">${h.evidence ? "✗" : "·"}</span><span class="body">${esc(h.text)}
        <div class="trace">${esc(h.clinician ? `${h.clinician}, ${h.date}` : "extracted from a dictated note")} · matched “${h.terms.map(esc).join("”, “")}”</div></span></div>`).join("") || '<p class="muted small">no entry names this drug class</p>'}</div>`;
  }
  function check() {
    const c = S.check; if (!c) return empty("Today's order check");
    const p = c.provenance;
    return `<h1>Today's order check — the draft against his whole chart</h1>
      <p class="lead">${esc(story().today)}, ${esc(story().clinicians.okafor.name)}: ${esc(story().visit.reason)}. Each drafted order is checked with
      <code>fact list --actors &lt;Walter&gt; --projects &lt;clinic&gt; --query &lt;name&gt;</code> for every name of its drug class — exact, every page, nothing ranked away.</p>
      <div class="grid">${c.results.map((r) => checkCard(r)).join("")}</div>
      ${p ? `<div class="card"><h2 style="margin-top:0">Why it is blocked — <code>fact get</code></h2><p>“${esc(p.text)}”</p>
        <dl class="kv"><dt>signed by</dt><dd>${esc(p.clinician)}</dd><dt>date</dt><dd>${esc(p.date)}</dd><dt>source</dt><dd>${esc(p.source)}</dd><dt>fact</dt><dd class="mono small">${esc(p.id)}</dd></dl></div>` : ""}
      <h2>Control — his chart as it stood on ${esc(c.cutoff)}</h2>
      <p class="lead">The same results, keeping only entries dated on or before ${esc(c.cutoff)}: what a scribe would have seen before urgent care.</p>
      <div class="grid">${c.results.map((r) => checkCard(r, c.cutoff)).join("")}</div>`;
  }

  function exact() {
    const x = S.exact; if (!x) return empty("Exact vs semantic");
    return `<h1>Exact vs semantic, on the same chart</h1>
      <p class="lead"><code>fact list --query</code> is a case-insensitive substring match: it finds exactly the entries containing the word, and nothing else.
      So the check has to name every member of the class — the triage nurse wrote “Keflex”, not “cephalosporin”.</p>
      <div class="card"><table class="iso"><tr><th>name</th><th>entries found</th><th></th></tr>
        ${x.terms.map((t) => `<tr><td><code>${esc(t.term)}</code></td><td>${t.ids.length}</td><td class="small">${esc([...new Set(t.labels)].join(", "))}</td></tr>`).join("")}</table>
        ${x.only.map((o) => `<p class="small">→ “${esc(o.text)}” is found only by <code>${esc(o.term)}</code></p>`).join("")}
        <p class="tally"><span class="pill warn">the class name alone: ${x.class_only} of ${x.evidence} reaction records</span> <span class="pill ok">all names: ${x.evidence} of ${x.evidence}</span></p></div>
      <div class="card"><h2 style="margin-top:0"><code>search</code> — “${esc(x.question)}” (top 5)</h2>
        ${x.search.map((h, i) => `<div class="memfact ${h.evidence ? "clean" : ""}"><span class="mk">${h.evidence ? "✓" : "·"}</span><span class="body">${i + 1}. ${esc(h.text)}</span></div>`).join("")}
        <p class="tally"><span class="pill ${x.search_found === x.evidence ? "ok" : "warn"}">${x.search_found} of ${x.evidence} reaction records in the top 5</span></p>
        <p class="muted small" style="margin-bottom:0">Search finds things by meaning — no name list needed — but returns its top-k. The order check uses the exact list; search answers the questions nobody wrote a list for.</p></div>`;
  }

  function recordCards(x) {
    return x.records.map((rec) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(rec.meta.verdict)} ${esc(rec.meta.order)}</h2><span class="mono small">${esc(rec.id)}</span></div>
      <p class="small">“${esc(rec.text)}”</p><div class="trace">checked ${esc(rec.meta.checked_at)} · saw ${(rec.meta.seen || []).length} entr${(rec.meta.seen || []).length === 1 ? "y" : "ies"}</div>
      ${x.rows.filter((r) => r.record === rec.id).map((r) => `<div class="memfact ${r.state === "unchanged" ? "clean" : "personal"}"><span class="mk">${r.state === "unchanged" ? "✓" : "✗"}</span><span class="body">${esc(r.state)} · ${esc(r.text || r.id)}</span></div>`).join("")}</div>`).join("")
      + `<p><span class="pill ${x.unchanged === x.total ? "ok" : "warn"}">${x.unchanged} of ${x.total} entries unchanged since the decision</span></p>`;
  }
  function record() {
    const x = S.record; if (!x) return empty("Decision record");
    return `<h1>Decision record — what the scribe saw</h1>
      <p class="lead">Each decision is pinned on the clinic project with the id and version (<code>updated_at</code>) of every entry it saw, then re-read with <code>fact get</code>.
      Asked later “what did the AI see at decision time?”, this is the answer — and it says whether any of it has changed since.</p>
      ${recordCards(x)}<p class="muted small">Written to <code>${esc(x.file)}</code>.</p>`;
  }

  function ask() {
    const ready = !!S.st.ready;
    return `<h1>Ask his chart — exact and semantic, side by side</h1>
      <p class="lead">The same text goes to <code>fact list --query</code> (every entry containing it) and to <code>search</code> (the 5 closest by meaning).</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Keflex" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}><button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Keflex", "cephalosporin", "eGFR", "allerg", "Has he reacted badly to an antibiotic?"].map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${ready ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>
      <div class="card"><div class="row spread"><h2 style="margin:0">Check an order now</h2><span>${(story()?.visit.orders || []).map((o) => `<button class="btn sm" data-drug="${esc(o.drug)}" ${ready ? "" : "disabled"}>${esc(o.drug)}</button>`).join(" ")}
        <button class="btn sm" data-act="records" ${ready ? "" : "disabled"}>Re-verify records</button></span></div>
        <div id="extra-result" style="margin-top:10px">${S.extra}</div></div>`;
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
    st.querySelectorAll("[data-drug]").forEach((b) => b.addEventListener("click", async () => {
      const box = $("#extra-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> checking ${esc(b.dataset.drug)}…</span>`;
      const r = await api("/api/check", { drug: b.dataset.drug }); S.extra = r ? checkCard(r) : ""; if (document.body.contains(box)) box.innerHTML = S.extra;
    }));
    st.querySelector('[data-act="records"]')?.addEventListener("click", async () => {
      const box = $("#extra-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> re-reading every entry…</span>`;
      const r = await api("/api/records", {}); S.extra = r ? recordCards(r) : ""; if (document.body.contains(box)) box.innerHTML = S.extra;
    });
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }
  async function askQuery(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> asking…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `<div class="grid">
      <div><h2><code>fact list --query</code> · ${r.exact.length}</h2>${r.exact.map((h) => `<div class="memfact"><span class="mk">•</span><span class="body">${esc(h.text)}<div class="trace">${esc(h.clinician || "extracted")}${h.date ? ", " + esc(h.date) : ""}</div></span></div>`).join("") || '<p class="muted">no entry contains it</p>'}</div>
      <div><h2><code>search</code> · top 5</h2>${r.semantic.map((h, i) => `<div class="memfact ${h.in_exact ? "clean" : ""}"><span class="mk">${h.in_exact ? "=" : h.same_text ? "~" : "+"}</span><span class="body">${i + 1}. ${esc(h.text)}${h.same_text ? `<div class="leakwhy">↳ same text as an exact hit, different id (${esc(h.id)})</div>` : ""}</span></div>`).join("") || '<p class="muted">nothing</p>'}
        <p class="muted small">= also in the exact list · + found by meaning only</p></div></div>`;
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
  function resetView() { S.setup = null; S.chart = null; S.uc = null; S.waiting = false; S.detector = null; S.turns = []; S.dictation = null; S.reconciled = null; S.check = null; S.exact = null; S.record = null; S.extra = ""; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's dictated note, clinic project, Walter's chart and the urgent care physician from your account?")) return;
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
    else if (k === "chart") S.chart = d;
    else if (k === "urgent_care") S.uc = d;
    else if (k === "detector_wait") S.waiting = true;
    else if (k === "detector") { S.detector = d; S.waiting = false; }
    else if (k === "turn") S.turns.push(d);
    else if (k === "dictation") S.dictation = d;
    else if (k === "reconciled") S.reconciled = d;
    else if (k === "order_check") S.check = d;
    else if (k === "exact_vs_semantic") S.exact = d;
    else if (k === "record") S.record = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the dictated note, clinic project, Walter's chart and the urgent care physician are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask” — exact and semantic side by side, or check an order again.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text", "turn"].includes(k)) { renderSteps(); renderStage(); }
    else if (S.caughtUp && k === "turn" && S.view === "lastweek") renderStage();
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "chart", "urgent_care", "detector_wait", "detector",
    "turn", "dictation", "reconciled", "order_check", "exact_vs_semantic", "record", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.record ? "record" : "connect");
    subscribe();
  })();
})();

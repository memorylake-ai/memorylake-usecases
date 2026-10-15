/* Customer feedback memory across tickets — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "feedback + taxonomy", view: "setup" },
    { n: 3, title: "Intake", sub: "24 tickets, 6 languages", view: "intake" },
    { n: 4, title: "Quick read", sub: "English words only", view: "quick" },
    { n: 5, title: "Memory read", sub: "every language + search", view: "memory" },
    { n: 6, title: "Write it back", sub: "Portuguese words, English tags", view: "writeback" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, quick: null, memory: null, writeback: null,
    ask: { q: "", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const langName = (l) => story()?.languages?.[l] || l;
  const themeList = () => S.intake?.themes || (story()?.glossary || []).map((g) => ({ theme: g.theme, label: g.label }));
  const themeLabel = (t) => themeList().find((x) => x.theme === t)?.label || t;
  const tickets = () => Object.fromEntries((S.intake?.tickets || []).map((t) => [t.record, t]));
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const agreePill = (a) => a ? `<span class="pill ok">✓ same</span>` : `<span class="pill err">✗ differs</span>`;
  const langPill = (l) => `<span class="pill">${esc(l)}</span>`;
  const ansPill = (a, bad) => `<span class="pill ${bad ? "err" : ""}">${esc(a?.short ?? "—")}</span>`;
  const isRequest = (t) => (story()?.glossary || []).find((g) => g.theme === t)?.kind !== "signal";
  function bars(read) {
    const tk = tickets();
    const rows = themeList().filter((t) => isRequest(t.theme)).map((t) => ({ ...t, r: read[t.theme] || { hits: [], candidates: [] } }))
      .sort((a, b) => b.r.hits.length - a.r.hits.length || a.label.localeCompare(b.label));
    const max = Math.max(1, ...rows.map((x) => x.r.hits.length + (x.r.candidates || []).length));
    return `<table class="iso">${rows.map((x) => {
      const langs = [...new Set(x.r.hits.map((i) => tk[i]?.lang).filter(Boolean))].sort();
      const w = (n) => Math.round((n / max) * 100);
      return `<tr><td class="small" style="white-space:nowrap"><b>${esc(x.label)}</b></td><td class="mono small">${x.r.hits.length}${x.r.candidates?.length ? ` <span class="faint">+${x.r.candidates.length}?</span>` : ""}</td>
        <td style="width:45%"><div style="display:flex;height:12px;border-radius:3px;overflow:hidden;background:var(--line,#e5e7eb)"><div style="width:${w(x.r.hits.length)}%;background:var(--accent,#4f46e5)"></div><div style="width:${w((x.r.candidates || []).length)}%;background:var(--warn)"></div></div></td>
        <td class="small">${langs.map(langPill).join(" ")}</td></tr>`; }).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any language · themes</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, quick, memory, writeback, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays the review morning on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">under two minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Customers write in their language. The dashboard counts in English.</h1>
      <p class="lead">Tallyhook sells in six countries and answers every ticket in the customer's language. Each ticket's feedback is kept word for word in a MemoryLake project —
      German, Japanese, Portuguese as written. The feedback dashboard counts each theme with its <b>English</b> word: every match exact, every record true, and the
      <b>invoice PDF export</b> that seven customers asked for in six languages shows up once. The memory read uses every language's words, and <code>search</code> — which ranks by meaning,
      across languages — for the language the taxonomy forgot. This page drives the <code>memorylake</code> CLI for real. The company, the customers and the people are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes both projects and every record on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/customer-feedback-memory-across-tickets" target="_blank" rel="noopener">memorylake.ai › Customer feedback memory across tickets</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("Two projects");
    const p = story()?.projects || {};
    return `<h1>Two projects: the feedback, and the words that sort it</h1>
      <p class="lead">“Ticket content becomes typed feedback memory.” One project holds every ticket's feedback word for word in the customer's language, with the account,
      country, language, plan and ARR in metadata. The other holds the taxonomy: each theme with the words that mark it, per language — the thing every team's tools read to sort feedback.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        ${["feedback", "taxonomy"].map((k) => `<tr><td><span class="pill">project</span></td><td><b>${esc(p[k]?.name)}</b></td><td class="mono small faint">${esc(su.projects?.[k])}</td></tr>`).join("")}</table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const langs = Object.keys(x.languages || {});
    const by = Object.fromEntries(langs.map((l) => [l, x.tickets.filter((t) => t.lang === l)]));
    return `<h1>Intake — ${x.tickets.length} tickets in ${langs.filter((l) => by[l].length).length} languages, word for word</h1>
      <p class="lead">One <code>fact add</code> for all of them, then <code>fact update --metadata</code> on each. The taxonomy was written ${esc(story()?.glossary_by?.on)};
      it has words for ${langs.length - (x.uncovered || []).length} languages — <b>none for ${esc((x.uncovered || []).map(langName).join(", ") || "—")}</b> (Brazil launched in September).
      ${x.restored ? ` <span class="faint">(${x.restored} record(s) an earlier run rewrote were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card mine"><h2 style="margin-top:0">The taxonomy</h2><table class="iso"><tr><th>theme</th>${langs.map((l) => `<th>${esc(l)}</th>`).join("")}</tr>
        ${x.themes.map((t) => `<tr><td class="small"><b>${esc(t.label)}</b></td>${langs.map((l) => `<td class="small">${t.terms?.[l] ? esc(t.terms[l].join(", ")) : '<span class="pill err">none</span>'}</td>`).join("")}</tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Every ticket</h2><table class="iso"><tr><th>ticket</th><th>account</th><th>feedback, as written</th></tr>
        ${x.tickets.map((t) => `<tr><td class="mono small" style="white-space:nowrap">${esc(t.record)}<br>${langPill(t.lang)}</td><td class="small" style="white-space:nowrap">${esc(t.account)}<br><span class="faint">${esc(t.country)} · ${esc(t.plan)} · $${Number(t.arr).toLocaleString("en-US")}</span></td><td class="small">${esc(t.body)}</td></tr>`).join("")}</table></div>`;
  }

  function answersTable(qs, answers) {
    return `<table class="iso">${qs.map((q) => `<tr><td class="small"><span class="mono">${esc(q.id)}</span> ${esc(q.text)}</td><td class="small"><b>${esc(answers[q.id]?.said)}</b></td></tr>`).join("")}</table>`;
  }

  function quick() {
    const x = S.quick; if (!x) return empty("The quick read");
    const tk = tickets();
    return `<h1>The quick read — the dashboard counts with English words</h1>
      <p class="lead">${esc(story()?.product_lead?.name)} (product) and ${esc(story()?.sales_lead?.name)} (customer success) open the feedback dashboard. Each theme is counted with
      the taxonomy's English word: <code>fact list --query</code>, an exact, case-insensitive substring over every record. Nothing here is wrong — every count is exact.</p>
      <div class="card"><h2 style="margin-top:0">Requests, by tickets that contain the English word</h2>${bars(x.read)}</div>
      <div class="card"><h2 style="margin-top:0">The questions</h2>${answersTable(x.questions, x.answers)}</div>
      <div class="card"><h2 style="margin-top:0">What each word matched</h2><table class="iso">
        ${themeList().map((t) => { const r = x.read[t.theme] || { words: {} }; return Object.entries(r.words || {}).map(([w, ids]) =>
          `<tr><td class="small">${esc(t.label)}</td><td class="mono small">--query '${esc(w)}'</td><td class="small">${ids.map((i) => `${esc(i)} ${langPill(tk[i]?.lang)}`).join(" · ") || '<span class="faint">—</span>'}</td></tr>`).join(""); }).join("")}</table></div>`;
  }

  function memory() {
    const x = S.memory; if (!x) return empty("The memory read");
    const tk = tickets(), langs = x.languages || [];
    const wrong = x.rows.filter((r) => !r.agree);
    const knn = Object.values(x.read)[0]?.knn || [];
    return `<h1>The memory read — ${wrong.length} of ${x.rows.length} answers change with the language of the words</h1>
      <p class="lead">The taxonomy as it is in memory (<code>fact list</code>), <b>every word in every language</b> (<code>fact list --query</code> each), then <code>search</code> for the language it has no words for:
      each Portuguese ticket's own text is the query, and the three nearest tickets the words did match vote on its themes. The <b>same counting code</b> as the dashboard.</p>
      <div class="card"><table class="iso"><tr><th>question</th><th>quick read (English words)</th><th>memory read (every language)</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.agree ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.question.id)}</span> ${esc(r.question.text)}</td>
          <td class="small">${esc(r.quick.said)}</td><td class="small"><b>${esc(r.memory.said)}</b></td><td>${agreePill(r.agree)}</td></tr>`).join("")}</table></div>
      <div class="card mine"><h2 style="margin-top:0">Themes × languages</h2><table class="iso"><tr><th>theme</th>${langs.map((l) => `<th>${esc(l)}</th>`).join("")}<th>all</th><th class="faint">English words</th></tr>
        ${themeList().map((t) => { const r = x.read[t.theme] || { hits: [], candidates: [] };
          return `<tr><td class="small"><b>${esc(t.label)}</b></td>${langs.map((l) => { const n = r.hits.filter((i) => tk[i]?.lang === l).length, c = r.candidates.filter((i) => tk[i]?.lang === l).length;
            return `<td class="mono small">${n || '<span class="faint">·</span>'}${c ? ' <span class="pill accent">?</span>' : ""}</td>`; }).join("")}
            <td class="mono small"><b>${r.hits.length}</b></td><td class="mono small faint">${(S.quick?.read?.[t.theme]?.hits || []).length}</td></tr>`; }).join("")}</table>
        <p class="small faint" style="margin-bottom:0">? = proposed by search for a ticket in a language the taxonomy has no words for — a person who reads that language confirms it in step 6.</p></div>
      <div class="card"><h2 style="margin-top:0">Search by example</h2>
        ${knn.map((k) => `<div style="margin-bottom:12px"><p class="small" style="margin:0 0 4px"><span class="mono">${esc(k.record)}</span> ${langPill(k.lang)} <b>${esc(k.account)}</b> — “${esc(k.body)}”</p>
          <table class="iso">${k.near.map((h) => `<tr><td class="mono small">#${esc(h.rank)}</td><td class="mono small">${esc(h.record)} ${langPill(h.lang)}</td><td class="small">${esc(h.account)}</td><td class="small">${h.themes.map((t) => `<span class="pill">${esc(themeLabel(t))}</span>`).join(" ")}</td></tr>`).join("")}</table>
          <p class="small" style="margin:4px 0 0">→ ${k.proposed.length ? "proposes " + k.proposed.map((t) => `<span class="pill accent">${esc(themeLabel(t))} ${k.votes[t]}/3</span>`).join(" ") : '<span class="faint">proposes nothing</span>'}
          ${k.matched.length ? ` <span class="faint">· already matched: ${esc(k.matched.map(themeLabel).join(", "))}</span>` : ""}</p></div>`).join("")}</div>`;
  }

  function writeback() {
    const x = S.writeback; if (!x) return empty("Write it back");
    const c = x.census || {}, tk = tickets(), rv = story()?.reviewer || {};
    const prop = new Set(x.proposed || []);
    return `<h1>Write it back — the dashboard agrees on ${x.agree} of ${x.after.length}</h1>
      <p class="lead">${esc(rv.name)} (${esc(rv.role)}) confirms what search proposed. The taxonomy gets its Portuguese words (<code>fact update</code>, old text in <code>metadata.previous</code>),
      and every non-English ticket gets its themes appended in English — the customer's words stay first. Then the <b>same dashboard code</b>, English words only.</p>
      <div class="card"><h2 style="margin-top:0">The review</h2><table class="iso">
        ${Object.entries(x.confirmed || {}).flatMap(([rec, ts]) => ts.map((t) => `<tr><td class="mono small">${esc(rec)} ${langPill(tk[rec]?.lang)}</td><td class="small">${esc(tk[rec]?.account)}</td><td class="small">${esc(themeLabel(t))}</td><td><span class="pill ok">✓ confirmed</span> <span class="small faint">${prop.has(rec + ":" + t) ? "search proposed it" : "not proposed by search"}</span></td></tr>`)).join("")}
        ${[...prop].filter((p) => { const [rec, t] = p.split(":"); return !(x.confirmed?.[rec] || []).includes(t); }).map((p) => { const [rec, t] = p.split(":");
          return `<tr><td class="mono small">${esc(rec)} ${langPill(tk[rec]?.lang)}</td><td class="small">${esc(tk[rec]?.account)}</td><td class="small">${esc(themeLabel(t))}</td><td><span class="pill err">✗ rejected</span> <span class="small faint">search proposed it</span></td></tr>`; }).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0"><code>fact list --query '${esc(c.query)}'</code> <span class="small faint">exact substring, every record</span></h2><table class="iso">
        <tr><td class="small">before</td><td class="small"><b>${(c.before || []).length}</b></td><td class="mono small">${esc((c.before || []).join(", "))}</td></tr>
        <tr class="hl"><td class="small">after</td><td class="small"><b>${(c.after || []).length}</b></td><td class="mono small">${esc((c.after || []).join(", "))}</td></tr></table></div>
      <div class="card"><h2 style="margin-top:0">The dashboard again</h2>${bars(Object.fromEntries(themeList().map((t) => [t.theme, { hits: (x.after_hits || {})[t.theme] || [], candidates: [] }])))}
        <table class="iso"><tr><th>question</th><th>quick read</th><th>memory read</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono">${esc(a.question.id)}</span> ${esc(a.question.text)}</td><td class="small">${esc(a.quick.said)}</td><td class="small">${esc(a.memory.said)}</td><td>${agreePill(a.agree)}</td></tr>`).join("")}</table></div>
      <div class="card mine"><h2 style="margin-top:0">Tickets</h2><table class="iso"><tr><th>ticket</th><th>now</th></tr>
        ${x.updated.map((u) => `<tr><td class="mono small" style="white-space:nowrap">${esc(u.record)} ${langPill(tk[u.record]?.lang)}</td><td class="small">${esc(u.was)} <b>${esc(u.now.slice(u.was.length).trim())}</b></td></tr>`).join("")}</table></div>
      ${x.trace?.length ? `<div class="card"><h2 style="margin-top:0"><code>fact trace</code> <span class="small faint">newest first</span></h2><table class="iso">
        ${x.trace.map((e) => `<tr><td><span class="pill ${e.event === "ADD" ? "" : "accent"}">${esc(e.event)}</span></td><td class="small muted" style="white-space:nowrap">${esc(e.how)} · ${esc(e.at)}</td><td class="small">${esc(e.new)}</td></tr>`).join("")}</table></div>` : ""}`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask;
    return `<h1>Ask</h1>
      <p class="lead">Ask in any language: <code>search</code> ranks the feedback by meaning, across languages, and each hit says whether the dashboard's English words would ever count it.
      Or see every theme per language.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Who wants invoices as PDF?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e)}">${esc(e)}</button>`).join("")}
          <button type="button" class="chip" data-themes="1">themes: per language</button></div>
        <div id="ask-result" class="result">${ready ? a.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuestion($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; askQuestion(c.dataset.q); }));
      st.querySelector(".chips .chip[data-themes]")?.addEventListener("click", () => askThemes());
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQuestion(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching the feedback…</span>`;
    const r = await api("/api/ask", { q });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>“${esc(r.question)}”</h2><span class="pill">top ${r.hits.length} · ${esc(r.langs.join(", "))}</span></div>
      <table class="iso"><tr><th>rank</th><th>ticket</th><th>feedback, as written</th><th>English dashboard</th></tr>
      ${r.hits.map((h) => `<tr class="${h.english.length ? "" : "hl"}"><td class="mono small">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)} ${langPill(h.lang)}<br><span class="faint">${esc(h.account)}</span></td>
        <td class="small">${esc(h.previous || h.body)}</td><td class="small">${h.english.length ? `has <code>${esc(h.english.join(", "))}</code>` : '<span class="pill err">no English word</span>'}</td></tr>`).join("")}</table>
      <p class="small">Search ranks by meaning across languages; <b>${r.english} of ${r.hits.length}</b> hits contain one of the dashboard's English words in the customer's own words.</p>`);
  }
  async function askThemes() {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading…</span>`;
    const r = await api("/api/themes", {});
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2><code>fact list --query</code> · every theme, per language</h2><span class="pill">${r.tagged} tagged in English</span></div>
      <table class="iso"><tr><th>theme</th>${r.langs.map((l) => `<th>${esc(l)}</th>`).join("")}<th>every language</th><th>English words</th><th>words in</th></tr>
      ${r.rows.map((x) => `<tr class="${x.memory !== x.quick ? "hl" : ""}"><td class="small"><b>${esc(x.label)}</b></td>${r.langs.map((l) => `<td class="mono small">${x.per[l] || '<span class="faint">·</span>'}</td>`).join("")}
        <td class="mono small"><b>${x.memory}</b></td><td class="mono small">${x.quick}</td><td class="small">${esc(x.covered.join(" "))}${x.revised ? ` <span class="faint">· revised ${esc(x.revised)}</span>` : ""}</td></tr>`).join("")}</table>`);
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
  function resetView() { S.setup = null; S.intake = null; S.quick = null; S.memory = null; S.writeback = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete both demo projects and every record on them from your account?")) return;
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
    else if (k === "intake") S.intake = d;
    else if (k === "quick") S.quick = d;
    else if (k === "memory") S.memory = d;
    else if (k === "writeback") S.writeback = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — both projects and every record on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Ask in any language, or see every theme per language — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "quick", "memory", "writeback",
    "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.writeback ? "writeback" : "connect");
    subscribe();
  })();
})();

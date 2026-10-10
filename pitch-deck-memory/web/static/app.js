/* Pitch deck memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, status: {},
    library: null, slots: {}, lessons: null, approved: null, verified: null, exported: null,
    ask: { q: "", res: null }, termCount: 0, lastEvent: 0, caughtUp: false, slides: {},
  };
  const story = () => S.data?.story || {};
  const label = (name) => S.data?.labels?.[name] || name;
  const fileOf = (name) => S.data?.names?.[name] || name;
  const STEPS = [
    { view: "connect", title: "Connect", sub: "API key, team, workspace" },
    { view: "library", title: "Pitch library", sub: "3 decks + 3 debriefs" },
    { view: "slots", title: "Slide finder", sub: "a cited slide per slot" },
    { view: "lessons", title: "Debriefs", sub: "what lost, what won" },
    { view: "approved", title: "Approved content", sub: "which old slide is stale" },
    { view: "verify", title: "Verify", sub: "against the .pptx / .docx" },
    { view: "export", title: "Export", sub: "outline + citations" },
  ];
  const viewOfStep = (i) => STEPS[i - 1]?.view;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s, i) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.status[s.view] || ""}" data-view="${s.view}">
        <span class="n">${S.status[s.view] === "done" ? "✓" : i + 1}</span><span class="t">${esc(s.title)}<small>${esc(s.sub)}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the library<small>citable slides for any question</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, library, slots, lessons, approved, verify, export: exportView, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage(); hydrateSlides();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  function connect() {
    const st = S.st, p = story().prospect || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Pitch library</dt><dd>${S.st.ids?.project ? `<span class="mono muted">${esc(S.st.ids.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3–4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The next pitch deck, built from the slides that already worked.</h1>
      <p class="lead"><b>${esc(story().agency)}</b> is pitching <b>${esc(p.client)}</b> (${esc(p.vertical)}). Its past decks (.pptx) and pitch debriefs (.docx) are in MemoryLake.
      For every slot of the new deck, MemoryLake returns <b>the slide that answers it — deck and slide number, in the slide's own words</b>; the debriefs reorder the outline;
      refreshed figures pinned as approved content get <b>the old slide flagged</b>; every citation is checked against the original file. This page drives the <code>memorylake</code> CLI.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the project and the six uploaded files.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/pitch-deck-memory-for-marketing-agencies" target="_blank" rel="noopener">memorylake.ai › pitch deck memory for marketing agencies</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  const outcomePill = (o) => o === "won" ? '<span class="pill ok">won</span>' : o === "lost" ? '<span class="pill warn">lost</span>' : "";
  function library() {
    const l = S.library; if (!l) return empty("Pitch library");
    return `<h1>The pitch library — three decks and their debriefs</h1>
      <p class="lead">Each file is uploaded with what the team knows about that pitch as Library attributes (<code>lib upload --xattrs</code>): client, category, date, won or lost,
      and the <b>sha256 of the exact bytes</b>; <code>proj doc import --wait</code> makes them searchable. A deck comes back from <code>search</code> as <b>one passage per slide</b>, with
      <code>range</code> = <code>P&lt;slide&gt;[…]</code> and a box on the slide.</p>
      <div class="card"><table class="tbl"><thead><tr><th>File</th><th>Client</th><th>Category</th><th>Pitched</th><th>Result</th><th>Slides</th><th>sha256 (pinned)</th></tr></thead><tbody>
      ${l.files.map((f) => { const a = f.attrs || {}; return `<tr><td>${f.kind === "deck" ? "🖼" : "📄"} <b>${esc(f.name)}</b></td><td>${esc(a.client)}</td><td>${esc(a.vertical)}</td><td>${esc(a.pitched)}</td>
        <td>${outcomePill(a.outcome)}</td><td>${f.slides || "—"}</td><td class="mono small">${esc(String(a.sha256 || "").slice(0, 16))}…</td></tr>`; }).join("")}</tbody></table></div>
      <p class="muted small">Project <span class="mono">${esc(l.project.id)}</span></p>`;
  }

  // ---------------------------------------------------------------- a slide, drawn from the file, with MemoryLake's box
  function slideBlock(name, page, box, small) {
    if (!name || !page) return "";
    return `<div class="slide ${small ? "small" : ""}" data-file="${esc(fileOf(name))}" data-page="${page}" data-box="${esc((box || []).join(","))}"><div class="page-loading">slide ${page}</div></div>`;
  }
  function wrapText(t, n) { const out = []; let cur = ""; for (const w of String(t).split(/\s+/)) { if ((cur + " " + w).trim().length > n) { out.push(cur.trim()); cur = w; } else cur += " " + w; } if (cur.trim()) out.push(cur.trim()); return out; }
  async function hydrateSlides() {
    for (const el of document.querySelectorAll(".slide[data-file]")) {
      const key = `${el.dataset.file}#${el.dataset.page}`;
      try {
        if (!S.slides[key]) S.slides[key] = await (await fetch(`/api/slide?file=${encodeURIComponent(el.dataset.file)}&page=${el.dataset.page}`)).json();
        const L = S.slides[key]; if (L.error) continue;
        const W = 1000, H = L.deck ? 750 : 1294, b = el.dataset.box ? el.dataset.box.split(",").map(Number) : [];
        let y, ink = "";
        if (L.deck) {
          const [title, ...body] = L.paragraphs;
          const tl = wrapText(title, 34); y = 105 - (tl.length - 1) * 26;
          ink += tl.map((t, i) => `<text x="${W / 2}" y="${y + i * 54}" font-size="46" text-anchor="middle" font-weight="600">${esc(t)}</text>`).join("");
          y = 250; body.forEach((p) => wrapText(p, 52).forEach((t, i) => { ink += `<text x="80" y="${y}" font-size="32">${i ? "" : "• "}${esc(t)}</text>`; y += 44; }));
        } else {
          y = 140; L.paragraphs.forEach((p, j) => { wrapText(p, 62).forEach((t) => { ink += `<text x="140" y="${y}" font-size="${j ? 24 : 40}" font-weight="${j ? 400 : 600}">${esc(t)}</text>`; y += j ? 34 : 56; }); y += 14; });
        }
        const rect = b.length === 4 ? `<rect class="hit" x="${b[0] * W}" y="${b[1] * H}" width="${(b[2] - b[0]) * W}" height="${(b[3] - b[1]) * H}" rx="6"/>` : "";
        el.innerHTML = `<svg viewBox="0 0 ${W} ${L.deck ? H : Math.min(H, y + 60)}" role="img" aria-label="${L.deck ? "slide" : "page"} ${el.dataset.page} with the cited box">${rect}<g class="ink">${ink}</g></svg>
          <div class="page-cap">${L.deck ? `slide ${L.page} of ${L.count}` : `page ${L.page}`} · text from the file, box from MemoryLake</div>`;
      } catch { /* leave the placeholder */ }
    }
  }

  function slots() {
    const ss = story().slots || []; if (!Object.keys(S.slots).length) return empty("Slide finder");
    const ok = Object.values(S.slots).filter((x) => x.chosen?.ok).length;
    return `<h1>Slide finder — a cited slide for every slot</h1>
      <p class="lead">One <code>search --types document</code> per slot. From each deck, the first slide of that kind (in MemoryLake's order for the query) is a candidate.
      <b>Case study and credentials</b> come from a pitch in the prospect's category; <b>process, pricing and team</b> from the newest pitch the agency won.</p>
      <div class="card big"><b>${ok} of ${ss.length}</b> slots got the expected slide.</div>
      ${ss.map((s) => { const r = S.slots[s.key]; if (!r) return `<h2>${esc(s.label)}</h2><div class="empty">pending</div>`; const c = r.chosen;
        return `<h2>${esc(s.label)} <span class="muted small">“${esc(s.query)}”</span></h2>
        <div class="card cite">${c ? slideBlock(c.document, c.page, c.box) : ""}<div class="cite-body">
          ${c ? `<div class="row spread"><b>${esc(label(c.document))}, slide ${c.page}</b>${c.ok ? '<span class="pill ok">✓ expected</span>' : '<span class="pill bad">✗ not the expected slide</span>'}</div>
          <div class="muted small">${esc(c.title)}</div><blockquote class="excerpt">${esc(c.body)}</blockquote>` : '<div class="check bad">no slide found</div>'}
          <div class="checks small">${r.candidates.map((x) => `<div class="check ${c && x.document === c.document ? "ok" : ""}"><span class="mark">${c && x.document === c.document ? "→" : "·"}</span>
            <span>${esc(label(x.document))} · slide ${x.page} · ${esc(x.attrs.vertical)} ${outcomePill(x.attrs.outcome)}${x.first_in_deck ? "" : ` <span class="faint">#${x.position} in its deck</span>`}</span></div>`).join("")}</div>
        </div></div>`; }).join("")}`;
  }

  function lessons() {
    const l = S.lessons; if (!l) return empty("Debriefs");
    return `<h1>Debriefs — what lost and what won reorders the outline</h1>
      <p class="lead">The outline starts from the last pitch in the same category (${esc(l.start.join(" → "))}). Each debrief is searched like the decks; the sentence that says what to change
      is quoted with its file and page, and moves one slide.</p>
      ${l.lessons.map((x) => `<div class="card cite">${slideBlock(x.debrief, x.page, x.box, true)}<div class="cite-body">
        <div class="row spread"><b>${esc(label(x.debrief))}</b>${outcomePill(x.outcome)}</div><div class="muted small">page ${x.page}</div>
        <blockquote class="excerpt">${esc(x.quote)}</blockquote>
        <div class="small">${x.changed ? `→ moved <b>${esc(l.labels[x.move])}</b>: ${esc(x.order.map((k) => l.labels[k]).join(" → "))}` : `✓ ${esc(l.labels[x.move])} already in place`}</div></div></div>`).join("")}
      ${l.lessons.length < l.wanted ? `<div class="card check bad">${l.wanted - l.lessons.length} lesson(s) not found in what search returned — not applied.</div>` : ""}
      <h2>Outline for ${esc(story().prospect?.client)}</h2><div class="card answer">Title → ${esc(l.order.map((k) => l.labels[k]).join(" → "))}</div>`;
  }

  function approved() {
    const a = S.approved; if (!a) return empty("Approved content");
    return `<h1>Approved content — which old slide is out of date</h1>
      <p class="lead">Refreshed figures and approved lines are pinned as project facts (<code>fact add</code>, ${15} s apart). MemoryLake's contradiction detector checks every new fact against the
      documents in the project: a conflict (<code>fact conflict list</code>) carries the slide's own text, which is matched back to a slide number in the .pptx.</p>
      ${a.rows.map((r) => `<div class="card"><div class="row spread"><b>${esc(r.text)}</b>${r.conflicts.length ? '<span class="pill warn">⚠ conflict</span>' : '<span class="pill ok">clean</span>'}</div>
        ${r.conflicts.map((v) => `<div class="cite" style="margin-top:10px">${v.slide ? slideBlock(v.document, v.slide, [], true) : ""}<div class="cite-body">
          <div class="small"><b>${esc(v.category)}</b> vs ${esc(label(v.document))}${v.slide ? `, slide ${v.slide}` : ""} — ${esc(v.description)}</div>
          <blockquote class="excerpt">${esc(v.excerpt)}</blockquote></div></div>`).join("")}
        <div class="small ${r.ok ? "" : "muted"}">${r.ok ? "✓ as expected" : "✗ not as expected (the detector may have deferred it)"}</div></div>`).join("")}
      ${Object.entries(a.flagged).map(([k, f]) => `<div class="card check warn"><span class="mark">⚠</span><span>The outline reuses that slide for <b>${esc(k.replace("_", " "))}</b>: keep it, replace its figures with “${esc(f.text)}”</span></div>`).join("")}`;
  }

  function verify() {
    const v = S.verified; if (!v) return empty("Verify");
    return `<h1>Verify — each citation against the original file</h1>
      <p class="lead">Not against the index that produced it: <code>proj doc download</code> fetches each cited file, its <b>sha256</b> must equal the one pinned at upload,
      and the quote must be on <b>that slide</b> of the .pptx (read from <code>ppt/slides/*.xml</code> in presentation order) or in the .docx.</p>
      <div class="card big"><b>${v.ok} of ${v.total}</b> citation(s) check out.</div>
      <div class="card checks">${v.results.map((r) => `<div class="check ${r.ok ? "ok" : "bad"}"><span class="mark">${r.ok ? "✓" : "✗"}</span><span><b>[${r.n}]</b>
        ${esc(label(r.document))} ${esc(r.unit)} ${r.page} — sha256 <span class="mono">${esc(String(r.sha256 || "").slice(0, 12))}…</span> ${r.sha256_ok ? "matches" : "<b>differs</b>"} · ${r.found ? "quote found there" : "<b>quote not there</b>"}</span></div>`).join("")}</div>
      <h2>Control — the same quotes on the neighbouring slide</h2><p class="muted small">If the check could not fail, these would pass too.</p>
      <div class="card big"><b>${v.caught} of ${v.control.length}</b> rejected.</div>`;
  }

  function exportView() {
    const e = S.exported; if (!e) return empty("Export");
    return `<h1>Export — the outline with its citations</h1>
      <p class="lead"><code>${esc(e.file)}</code> is the outline the team starts from; <code>${esc(e.json)}</code> holds every citation (file, document id, slide or page, box, quote, sha256, verification).
      <code>python3 demo.py verify ${esc(e.json.replace(/^.*?out\//, "out/"))}</code> downloads the originals and checks every one again.</p>
      <pre class="instr log">${esc(e.markdown)}</pre>`;
  }

  function ask() {
    const ready = !!S.st.ids?.project, r = S.ask.res;
    return `<h1>Ask the pitch library</h1>
      <p class="lead">Any question: the slides and debrief pages MemoryLake would offer as citations, in its order.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Which case study had the lowest cost per acquisition?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
        <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["Which case study had the lowest cost per acquisition?", "What retainer have we quoted?", "Why did we lose a pitch?", "Which pitch named the team on the last slide?"]
          .map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${!ready ? '<div class="empty">Run the demo first.</div>' : r ? askResult(r) : ""}</div></div>`;
  }
  function askResult(r) {
    if (!r.passages.length) return '<div class="empty">No passages.</div>';
    return r.passages.slice(0, 10).map((c) => `<div class="card cite">${slideBlock(c.document, c.page, c.box, true)}<div class="cite-body">
      <div class="row spread"><b>#${c.rank}.${c.position} ${esc(c.label)}</b>${outcomePill(c.outcome)}</div>
      <div class="muted small">${c.source_type === "ppt_file" ? "slide" : "page"} ${c.page} · ${esc(c.vertical)}</div>
      <blockquote class="excerpt">${esc(c.source_type === "ppt_file" ? c.title + " — " + c.body : c.text)}</blockquote></div></div>`).join("")
      + (r.passages.length > 10 ? `<p class="muted small">${r.passages.length - 10} more passage(s) not shown.</p>` : "");
  }

  // ---------------------------------------------------------------- behaviour
  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.status.connect = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo(false));
    const form = st.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askLib($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askLib(c.dataset.q); }));
    }
  }
  async function askLib(q) {
    if (!q) return; S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/ask", { question: q });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.res = r;
    if (S.view === "ask") renderStage();
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
  function resetView() { S.library = null; S.slots = {}; S.lessons = null; S.approved = null; S.verified = null; S.exported = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.status = { connect: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the pitch library project (documents, approved-content facts, conflicts) and the six uploaded files from your account?")) return;
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
  function running(view) {
    Object.keys(S.status).forEach((v) => { if (S.status[v] === "running") S.status[v] = "done"; });
    if (view) S.status[view] = "running";
    if (S.follow && view) S.view = view;
  }
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    if (k === "step") { if (d.index === 2) resetView(); running(viewOfStep(d.index)); term("step", ev.text); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "text") { /* the CLI's own summary lines; the views show the same data */ }
    else if (k === "connected") { S.st = d; S.status.connect = "done"; setConn(); }
    else if (k === "existing") S.st.ids = d;
    else if (k === "library") { S.library = d; S.st.ids = { project: d.project.id }; }
    else if (k === "slot") S.slots[d.slot.key] = d;
    else if (k === "lessons") S.lessons = d;
    else if (k === "approved") S.approved = d;
    else if (k === "verified") S.verified = d;
    else if (k === "exported") S.exported = d;
    else if (k === "done") {
      running(null); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.status = { connect: "done" }; if (S.caughtUp) banner("Cleanup finished — the project and the six uploaded files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. The outline is in Export; every citation was checked against the original files.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { running(null); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "library", "slot", "lessons",
    "approved", "verified", "exported", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => { if (e.data) onEvent(JSON.parse(e.data)); }));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  (async () => {
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data; renderSteps();
    const target = state.last_event_id; setConn();
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const h = (e) => { if (!e.data) return; const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") running(null);
    if (state.connected) S.status.connect = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.exported ? "slots" : "connect");
    subscribe();
  })();
})();

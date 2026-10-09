/* Verifiable source memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const fmtTime = (iso) => iso ? String(iso).slice(0, 16).replace("T", " ") + "Z" : "";

  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, status: {},
    sources: null, member: null, answers: {}, verified: null, controls: null, exported: null,
    ask: { q: "", res: null }, termCount: 0, lastEvent: 0, caughtUp: false, layouts: {},
  };
  const questions = () => S.data?.story?.questions || [];
  function steps() {
    return [
      { view: "connect", title: "Connect", sub: "API key, team, workspace" },
      { view: "sources", title: "Sources", sub: "documents + provenance" },
      { view: "member", title: "Member memory", sub: "Dana's chat, staff note" },
      ...questions().map((q, i) => ({ view: `q:${q.key}`, title: `Answer ${i + 1}`, sub: q.question.length > 34 ? q.question.slice(0, 33) + "…" : q.question })),
      { view: "verify", title: "Verify", sub: "against the original files" },
      { view: "export", title: "Export", sub: "citations as JSON" },
    ];
  }
  // demo.py step index → the view that shows it
  const viewOfStep = (i) => ({ 1: "connect", 2: "sources", 3: "member", 4: questions()[0] && `q:${questions()[0].key}`, 5: "verify", 6: "export" }[i]);

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = steps().map((s, i) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.status[s.view] || ""}" data-view="${s.view}">
        <span class="n">${S.status[s.view] === "done" ? "✓" : i + 1}</span><span class="t">${esc(s.title)}<small>${esc(s.sub)}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask the sources<small>citable passages for any question</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const v = S.view;
    const r = v.startsWith("q:") ? () => answerView(v.slice(2)) : { connect, sources, member, verify, exportView, ask }[v === "export" ? "exportView" : v];
    $("#stage").innerHTML = r ? r() : ""; bindStage(); hydratePages();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Sources project</dt><dd>${S.st.ids?.project ? `<span class="mono muted">${esc(S.st.ids.project)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 2–3 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Every statement the agent makes, traced to a page you can check.</h1>
      <p class="lead"><b>Larch Valley Credit Union</b> is reviewing its member-support agent before launch. Compliance's rule: every statement cites a source a person can check by hand.
      MemoryLake returns each passage with its <b>document, page and box on the page</b>, the Library keeps each file's <b>version and sha256</b>, and member facts carry <b>how they came to exist</b>.
      The agent's drafts are scripted (LLM calls are not free); every check on them is real. This page drives the <code>memorylake</code> CLI.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes both projects, the chat, the two actors and the uploaded PDFs.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-patterns-for-agents-that-need-verifiable-sources" target="_blank" rel="noopener">memorylake.ai › agents that cite verifiable sources</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  const forcePill = (f) => f === "current" ? '<span class="pill ok">current</span>' : `<span class="pill warn">${esc(f)}</span>`;
  function sources() {
    const s = S.sources; if (!s) return empty("Sources");
    return `<h1>The published documents, with their provenance</h1>
      <p class="lead">Each PDF is uploaded with its provenance as Library attributes (<code>lib upload --xattrs</code>): title, version, effective date, the version it replaces, publisher —
      and the <b>sha256 of the exact bytes</b>. Citations read it back with <code>lib get</code>, so they carry what the server holds. On ${esc(s.today)}, one fee schedule is in force and one is superseded.</p>
      <div class="card"><table class="tbl"><thead><tr><th>Document</th><th>Version</th><th>Effective</th><th>On ${esc(s.today)}</th><th>Pages</th><th>sha256 (pinned)</th></tr></thead><tbody>
      ${s.sources.map((d) => { const p = d.provenance || {}; return `<tr><td>📄 <b>${esc(p.title || d.name)}</b><div class="mono small faint">${esc(d.name)}</div></td><td>${esc(p.version)}</td><td>${esc(p.effective)}${p.superseded ? `<div class="small faint">superseded ${esc(p.superseded)}</div>` : ""}</td>
        <td>${forcePill(d.in_force)}</td><td>${d.pages}</td><td class="mono small">${esc(String(p.sha256 || "").slice(0, 16))}…</td></tr>`; }).join("")}</tbody></table></div>
      <p class="muted small">Project <span class="mono">${esc(s.project.id)}</span> · imported with <code>proj doc import --wait</code>. Each search hit comes back with <code>items[].highlight.chunks[]</code>: the passage text and a <code>range</code> like <code>P2[2:0.12,0.17,0.88,0.28]</code> — page 2, box from (0.12, 0.17) to (0.88, 0.28) of the page.</p>`;
  }

  function member() {
    const m = S.member; if (!m) return empty("Member memory");
    return `<h1>Dana's memory — what she said, and what staff stated</h1>
      <p class="lead">Dana Whitlock is an actor. Her chat of ${esc(m.chat_date.slice(0, 10))} is a conversation (in its own project, so nothing extracted from it mixes with the published documents);
      MemoryLake reads it and keeps facts. Member Services' note is pinned on her word for word with <code>fact add --actor</code>. <code>fact trace</code> tells the two apart.</p>
      <h2>The chat <span class="muted small">conv msg list</span></h2>
      <div class="card chat">${m.messages.map((x, i) => `<div class="bubble ${i % 2 ? "agent" : "member"}"><div class="who">${i % 2 ? "Support agent" : "Dana"} <span class="faint">${fmtTime(x.at)}</span></div>${esc(x.text)}</div>`).join("")}</div>
      <h2>Her memory now <span class="muted small">fact list --actors · --projects &lt;chats&gt;</span></h2>
      <div class="card">${m.facts.map((f) => `<div class="memfact small ${f.text === m.staff_note ? "new" : ""}"><span>${esc(f.text)}</span><span class="when">${f.text === m.staff_note ? '<span class="pill accent">stated by staff</span>' : '<span class="pill">extracted</span>'}</span></div>`).join("")}</div>`;
  }

  // ---------------------------------------------------------------- the page with the box
  function pageBlock(file, page, box, small) {
    if (!file || !page) return "";
    return `<div class="page ${small ? "small" : ""}" data-file="${esc(file)}" data-page="${page}" data-box="${esc((box || []).join(","))}"><div class="page-loading">p.${page}</div></div>`;
  }
  async function hydratePages() {
    for (const el of document.querySelectorAll(".page[data-file]")) {
      const key = `${el.dataset.file}#${el.dataset.page}`;
      try {
        if (!S.layouts[key]) S.layouts[key] = await (await fetch(`/api/page?file=${encodeURIComponent(el.dataset.file)}&page=${el.dataset.page}`)).json();
        const L = S.layouts[key]; if (L.error) continue;
        const W = L.width, H = L.height, b = el.dataset.box ? el.dataset.box.split(",").map(Number) : [];
        const text = L.lines.map((l) => `<text x="${l.x}" y="${H - l.y}" font-size="${l.size}" font-weight="${l.font === "F2" ? 700 : 400}" font-style="${l.font === "F3" ? "italic" : "normal"}">${esc(l.text)}</text>`).join("");
        const rules = L.rules.map((r) => `<line x1="${r[0]}" y1="${H - r[1]}" x2="${r[2]}" y2="${H - r[3]}"/>`).join("");
        const rect = b.length === 4 ? `<rect class="hit" x="${b[0] * W}" y="${b[1] * H}" width="${(b[2] - b[0]) * W}" height="${(b[3] - b[1]) * H}" rx="4"/>` : "";
        el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="page ${el.dataset.page} with the cited box">${rect}<g class="ink">${text}</g><g class="rules">${rules}</g></svg><div class="page-cap">p.${el.dataset.page} of ${L.pages}</div>`;
      } catch { /* leave the placeholder */ }
    }
  }

  const markNeedle = (text, needle) => needle ? esc(text).replace(new RegExp(reEsc(esc(needle)), "g"), (m) => `<mark>${m}</mark>`) : esc(text);
  function citationCard(c, needle) {
    if (c.type === "fact") {
      const src = (c.messages || [])[0];
      return `<div class="card cite"><div class="cite-n">[${c.n}]</div><div class="cite-body">
        <div class="row spread"><b>Dana's memory</b><span class="pill ${c.method === "stated" ? "accent" : ""}">${c.method === "stated" ? "stated by staff" : "extracted by MemoryLake"}</span></div>
        <blockquote class="excerpt">${markNeedle(c.text, needle)}</blockquote>
        <div class="muted small">${esc(c.detail)} · <span class="mono">${esc(c.fact_id)}</span> on her ${c.scope === "actor" ? "profile" : "chats project"}</div>
        ${src ? `<div class="muted small">source message ${fmtTime(src.at)} (${esc(src.who)}): “${esc(src.text)}”</div>` : ""}</div></div>`;
    }
    const b = c.box || [];
    return `<div class="card cite"><div class="cite-n">[${c.n}]</div>${pageBlock(fileOf(c.document), c.page, c.box)}<div class="cite-body">
      <div class="row spread"><b>${esc(c.title || c.document)}</b>${forcePill(c.in_force)}</div>
      <div class="muted small">${esc(c.document)} · v${esc(c.version)} (effective ${esc(c.effective)}) · <b>page ${c.page}</b>${b.length === 4 ? ` · box x ${b[0].toFixed(2)}–${b[2].toFixed(2)}, y ${b[1].toFixed(2)}–${b[3].toFixed(2)}` : ""} · ${esc(c.kind)}</div>
      <blockquote class="excerpt">${c.kind === "table" ? `<div class="small faint">${esc((c.cells && c.cells.length ? "row of the table" : ""))}</div>` : ""}${markNeedle(c.excerpt, needle)}</blockquote>
      <div class="mono small faint">sha256 ${esc(String(c.sha256 || "").slice(0, 24))}… · ${esc(c.chunk_id)}</div></div></div>`;
  }
  const fileOf = (name) => S.data?.files?.[name] || name;

  function answerView(key) {
    const q = questions().find((x) => x.key === key); if (!q) return "";
    const a = S.answers[key];
    const head = `<h1>“${esc(q.question)}”</h1><p class="lead">${esc(q.asker)} asks. MemoryLake is searched with <code>search --projects &lt;sources&gt; --types document</code>${q.claims.some((c) => c.source === "member") ? " and her own memory" : ""};
      each claim in the agent's draft is matched to a passage or fact that states it.</p>`;
    if (!a) return head + '<div class="empty">pending — this answer fills in as the demo runs.</div>';
    const needles = Object.fromEntries(a.checked.flatMap((r) => [[r.claim, r.needle], ...(r.replaced_by ? [[r.replaced_by.claim, r.replaced_by.needle]] : [])]));
    const mark = { supported: ["ok", "✓"], stale: ["warn", "⚠"], unsupported: ["bad", "✗"] };
    return head + `<h2>The draft, checked claim by claim <span class="muted small">${a.passages} passage(s) retrieved</span></h2>
      <div class="card checks">${a.checked.map((r) => { const [cls, m] = mark[r.status];
        const why = r.status === "supported" ? "" : r.status === "stale"
          ? `<div class="small muted">Only a superseded version says this: ${r.also.map((x) => `${esc(x.document)} v${esc(x.version)} p.${x.page} (superseded ${esc(x.superseded)})`).join(", ")}.</div>
             ${r.replaced_by ? `<div class="small">→ replaced with the current version: <b>${esc(r.replaced_by.claim)}</b></div>` : ""}`
          : `<div class="small muted">No passage or fact states this → dropped from the answer.</div>`;
        return `<div class="check ${cls === "bad" ? "bad" : cls === "warn" ? "warn" : "ok"}"><span class="mark">${m}</span><span>${esc(r.claim)}${why}</span></div>`; }).join("")}</div>
      <h2>The answer as sent</h2>
      <div class="card answer">${a.final.map((f) => `${esc(f.claim)} <sup>[${f.n}]</sup>`).join(" ")}</div>
      <h2>Citations</h2>${a.citations.map((c) => citationCard(c, needles[c.claim])).join("")}`;
  }

  function verify() {
    const v = S.verified; if (!v) return empty("Verify");
    const cites = Object.values(S.answers).flatMap((a) => a.citations);
    const byN = Object.fromEntries(cites.map((c) => [c.n, c]));
    return `<h1>Verify — each citation against the original file</h1>
      <p class="lead">Not against the index that produced it: <code>proj doc download</code> fetches each cited document, its <b>sha256</b> must equal the one pinned at ingest,
      and the excerpt must be in <b>that page's text layer</b> (a table row: every cell). A cited fact must still be in Dana's memory, word for word.</p>
      <div class="card big"><b>${v.ok} of ${v.total}</b> citation(s) check out.</div>
      <div class="card checks">${v.results.map((r) => { const c = byN[r.n] || {};
        return `<div class="check ${r.ok ? "ok" : "bad"}"><span class="mark">${r.ok ? "✓" : "✗"}</span><span><b>[${r.n}]</b> ${r.type === "document"
          ? `${esc(c.document)} p.${r.page} — sha256 <span class="mono">${esc(r.sha256.slice(0, 12))}…</span> ${r.sha256_ok ? "matches" : "<b>differs</b>"} · ${r.found ? "excerpt found on page " + r.page : "<b>excerpt not on page " + r.page + "</b>"}`
          : `fact <span class="mono">${esc(c.fact_id)}</span> — ${r.unchanged ? "still in her memory, word for word" : r.exists ? "<b>changed</b>" : "<b>gone</b>"}`}</span></div>`; }).join("")}</div>
      ${S.controls ? `<h2>Control — the claims that were not sent, checked the same way</h2><p class="muted small">All four originals downloaded; if the check above were always green, these would be too.</p>
      <div class="card checks">${S.controls.map((c) => `<div class="check ${c.current.length ? "ok" : "bad"}"><span class="mark">${c.current.length ? "!" : "✗"}</span><span>“${esc(c.needle)}” — ${c.current.length
        ? "IS on " + c.current.map(([n, p]) => `${esc(n)} p.${p}`).join(", ")
        : "on no page of a current document" + (c.superseded.length ? "; only on " + c.superseded.map(([n, p]) => `${esc(n)} p.${p} (superseded)`).join(", ") : "")}</span></div>`).join("")}</div>` : ""}`;
  }

  function exportView() {
    const e = S.exported; if (!e) return empty("Export");
    return `<h1>Export — citations compliance can re-check later</h1>
      <p class="lead">One JSON record per citation: the source link (document id, Library item), version, page, box, the excerpt, the sha256, and how it verified —
      plus a natural-language rendering. <code>python3 demo.py verify ${esc(e.file.replace(/^.*?out\//, "out/"))}</code> downloads the originals and checks every one again.</p>
      <div class="card">${e.citations.map((c) => `<p class="rendered">${esc(c.rendered)}</p>`).join("")}</div>
      <h2>${esc(e.file)}</h2><pre class="instr log">${esc(JSON.stringify(e.citations, null, 2))}</pre>`;
  }

  function ask() {
    const ready = !!S.st.ids?.project, r = S.ask.res;
    return `<h1>Ask the sources</h1>
      <p class="lead">Any question: the passages MemoryLake would offer as citations, each with its document, version, page and box.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. What does a stop payment order cost?" value="${esc(S.ask.q)}" ${ready ? "" : "disabled"}>
        <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${["What does a stop payment order cost?", "When are deposits after 5 pm treated as received?", "Do wires of 10,000 dollars need a call-back?", "What is the Premier Checking monthly fee?"]
          .map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${!ready ? '<div class="empty">Run the demo first.</div>' : r ? askResult(r) : ""}</div></div>`;
  }
  function askResult(r) {
    if (!r.chunks.length) return '<div class="empty">No passages.</div>';
    return r.chunks.map((c) => `<div class="card cite">${pageBlock(c.file, c.page, c.box, true)}<div class="cite-body">
      <div class="row spread"><b>#${c.rank} ${esc(c.title || c.document)}</b>${forcePill(c.in_force)}</div>
      <div class="muted small">v${esc(c.version)} · ${esc(c.kind)} · page ${c.page}${c.box.length === 4 ? ` · box x ${c.box[0].toFixed(2)}–${c.box[2].toFixed(2)}, y ${c.box[1].toFixed(2)}–${c.box[3].toFixed(2)}` : ""}</div>
      <blockquote class="excerpt">${esc(c.text)}</blockquote></div></div>`).join("");
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askSources($("#ask-q").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askSources(c.dataset.q); }));
    }
  }
  async function askSources(q) {
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
  function resetView() { S.sources = null; S.member = null; S.answers = {}; S.verified = null; S.controls = null; S.exported = null; }
  async function runDemo(reset) {
    banner(""); resetView(); S.status = { connect: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete both projects (documents, facts), Dana's chat, the two actors and the uploaded PDFs from your account?")) return;
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
    else if (k === "sources") { S.sources = d; S.st.ids = { project: d.project.id }; }
    else if (k === "member") S.member = d;
    else if (k === "answer") {
      S.answers[d.key] = d; S.status[`q:${d.key}`] = "done";
      const qs = questions(), i = qs.findIndex((q) => q.key === d.key);
      if (qs[i + 1]) running(`q:${qs[i + 1].key}`);
    }
    else if (k === "verified") S.verified = d;
    else if (k === "controls") S.controls = d.controls;
    else if (k === "exported") S.exported = d;
    else if (k === "done") {
      running(null); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.status = { connect: "done" }; if (S.caughtUp) banner("Cleanup finished — the projects, the chat, the actors and the uploaded PDFs are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Answer 3 has a stale and an unsupported claim; Verify checks every citation against the originals.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { running(null); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "sources", "member", "answer",
    "verified", "controls", "exported", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => onEvent(JSON.parse(e.data))));
    es.onerror = () => { es.close(); setTimeout(subscribe, 1500); };
  }

  (async () => {
    const [state, data] = await Promise.all([fetch("/api/state").then((r) => r.json()), fetch("/api/data").then((r) => r.json())]);
    S.st = state; S.data = data; renderSteps();
    const target = state.last_event_id; setConn();
    await new Promise((resolve) => {
      if (!target) return resolve();
      const es = new EventSource("/api/stream?since=0");
      const h = (e) => { const ev = JSON.parse(e.data); onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") running(null);
    if (state.connected) S.status.connect = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.exported ? `q:${questions()[2]?.key}` : "connect");
    subscribe();
  })();
})();

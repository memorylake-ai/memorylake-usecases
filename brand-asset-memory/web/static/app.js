/* Brand asset memory for design teams — web companion. Vanilla JS, no build step.
   The page is a view over the event stream the demo emits (see demo.py `emit`). */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "";
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const side = (key) => key === "dana" ? "analyst" : "pm";
  const img = (name) => `/assets/${encodeURIComponent(name)}`;
  const bg = (name) => `style="background-image:url('${img(name)}')"`;
  // Highlight hex codes wherever they appear.
  const hl = (s) => esc(s).replace(/(#[0-9A-Fa-f]{6})\b/g, "<mark>$1</mark>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "People & project", sub: "one project for the brand", view: "setup" },
    { n: 3, title: "DAM export", sub: "six images, opaque names", view: "assets" },
    { n: 4, title: "What it saw", sub: "a description per picture", view: "seen" },
    { n: 5, title: "Brand review", sub: "images in chat, rules pinned", view: "review" },
    { n: 6, title: "Designer asks", sub: "the right picture, by content", view: "answers" },
    { n: 7, title: "Retire", sub: "the old wordmark stops coming back", view: "retire" },
  ];
  const ALL_KINDS = ["step", "cmd", "note", "progress", "json", "connected", "actor", "project", "uploaded", "imported", "documents",
    "seen", "review", "turn", "cooked", "pinned", "replay", "facts", "answer", "brief", "retired", "cleaned", "done", "error", "text"];

  const S = {
    data: null, st: { connected: false, status: "idle" },
    view: "connect", follow: true,
    stepStatus: {},
    actors: {}, project: null,
    files: {},                 // name -> {status}
    seen: null,                // [{name, kind, summary, text, questions}]
    review: { stored: 0, cooked: false, pinned: false },
    replay: null, facts: null,
    answers: [], brief: null,
    retired: null,
    ask: { q: "", html: "" },
    termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function resetRun() {
    S.files = {}; S.seen = null; S.review = { stored: 0, cooked: false, pinned: false };
    S.replay = null; S.facts = null; S.answers = []; S.brief = null; S.retired = null;
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
        <span class="n">?</span><span class="t">Ask the memory<small>describe a picture, get the file</small></span>
      </button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }

  function show(view) { S.view = view; renderSteps(); renderStage(); }

  // ------------------------------------------------------------------ rendering: stage
  function renderStage() {
    const stage = $("#stage");
    const r = { connect, setup, assets, seen, review, answers, retire, ask }[S.view];
    stage.innerHTML = r ? r() : "";
    bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card">
        <div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px">
          <dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Project</dt><dd>${S.project ? `${esc(S.project.name)} <span class="mono muted">${esc(S.project.id)}</span>` : '<span class="muted">not created yet</span>'}</dd>
        </dl>
        <div class="row" style="margin-top:16px">
          <button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.project ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes · everything it creates is prefixed <code>mlu-bam-</code></span>
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
      <h1>Give design teams brand asset memory the AI actually sees</h1>
      <p class="lead">Fernway Coffee Roasters' brand comes out of the DAM as <code>IMG_2031.png</code>, <code>IMG_2052.png</code>, … —
      names that say nothing. Imported into one MemoryLake project, each image is <em>looked at</em>: described, its text read, the questions it answers listed.
      A new designer then asks in plain words and gets the right picture back, picked by what is in it.
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
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/brand-asset-memory-for-design-teams" target="_blank" rel="noopener">memorylake.ai › brand asset memory for design teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p>
        </div>
      </div>`;
  }

  function personCard(key) {
    const p = S.data.people[key]; const a = S.actors[key];
    return `<div class="card person"><span class="avatar ${side(key)}">${initials(p.display)}</span>
      <div><div class="name">${esc(p.display)}</div><div class="role">${esc(p.role)}</div></div>
      <span class="status pill ${a ? "ok" : ""}">${a ? "actor ready" : "pending"}</span></div>`;
  }

  function setup() {
    if (!S.data) return "";
    const pr = S.data.project;
    return `
      <h1>The brand lead, the design assistant, one project for the brand</h1>
      <p class="lead">An <b>actor</b> is who a memory is attributed to. The <b>project</b> holds the brand: its images as documents, the brand review
      and the rules — so <code>search --projects</code> is the brand memory any tool can read.
      ${esc(S.data.designer.display)} (${esc(S.data.designer.role)}) only asks questions, so he needs no actor.</p>
      <h2>Fernway Coffee Roasters</h2>
      <div class="grid">${["dana", "assistant"].map(personCard).join("")}</div>
      <h2>Brand project</h2>
      <div class="card row spread"><div><b>${esc(pr.name)}</b><div class="muted small">custom id <code>${esc(pr.custom_id)}</code>${S.project ? ` · <span class="mono">${esc(S.project.id)}</span>` : ""}</div></div>
        <span class="pill ${S.project ? "ok" : ""}">${S.project ? (S.project.fresh ? "created" : "exists") : "pending"}</span></div>`;
  }

  function assets() {
    if (!S.data) return "";
    const pill = (st) => st === "okay" ? `<span class="pill ok">processed</span>`
      : st === "imported" ? `<span class="pill ok">imported</span>`
      : st === "uploaded" ? `<span class="pill busy"><span class="spinner"></span> processing…</span>` : `<span class="pill">pending</span>`;
    return `
      <h1>The DAM export: six images with names that say nothing</h1>
      <p class="lead">Each image goes into the Library with <code>lib upload</code>, then <code>proj doc import --wait</code> processes it inside the project
      (about 50 seconds for all six). You can see what is in them; a search over file names cannot.</p>
      <div class="assets">${S.data.assets.map((f) => `<div class="asset"><div class="img" ${bg(f.name)}></div>
        <div class="cap"><span class="mono">${esc(f.name)}</span>${pill(S.files[f.name]?.status)}</div></div>`).join("")}</div>`;
  }

  function seen() {
    if (!S.seen) return `<h1>What MemoryLake saw</h1><div class="empty">Run the demo first — the descriptions show up once the images are processed.</div>`;
    return `
      <h1>What MemoryLake saw in each picture</h1>
      <p class="lead">One <code>search --types document</code> brings every image back with what MemoryLake made of it: a description of the picture,
      the text it read on it, and the questions it expects the picture to answer. Nobody tagged or captioned anything.</p>
      ${S.seen.map((h) => `<div class="card seen"><div class="img" ${bg(h.name)}></div>
        <div><div class="row" style="gap:8px"><b class="mono">${esc(h.name)}</b><span class="pill">${esc(h.kind)}</span></div>
          <dl><dt>saw</dt><dd>${esc(h.summary)}</dd>
            ${h.text ? `<dt>read on it</dt><dd><span class="ocr">${hl(h.text)}</span></dd>` : ""}
            ${h.questions?.length ? `<dt>answers</dt><dd><ul>${h.questions.slice(0, 3).map((q) => `<li>${hl(q)}</li>`).join("")}</ul></dd>` : ""}</dl></div></div>`).join("")}`;
  }

  function review() {
    if (!S.data) return "";
    const r = S.data.review; const st = S.review; const total = r.turns.length;
    const cook = st.cooked ? `<span class="pill ok">memory ready</span>`
      : st.stored >= total ? `<span class="pill busy"><span class="spinner"></span> extracting facts…</span>`
      : st.stored ? `<span class="pill accent">${st.stored}/${total} messages stored</span>` : `<span class="pill">queued</span>`;
    const facts = S.facts ? `
      <h2>What the review left in memory</h2>
      <div class="counts"><div class="count"><b>${S.facts.length}</b><span>facts</span></div>
        <div class="count"><b>${S.facts.filter((f) => f.pinned).length}</b><span>rules pinned verbatim</span></div>
        <div class="count"><b>${S.facts.filter((f) => !f.pinned).length}</b><span>written by MemoryLake from the words</span></div></div>
      ${S.facts.slice().sort((a, b) => b.pinned - a.pinned).map((f) => `<div class="fact ${f.pinned ? "pinned" : ""}"><span>${hl(f.fact)}</span><span class="tag">${f.pinned ? "pinned" : "learned"}</span></div>`).join("")}
      <p class="muted small">The pictures in the chat are stored with the messages and come back on replay, but facts are only written from the words.
      What a picture shows is read when the file is imported as a document (steps 3–4).</p>` : "";
    const replay = S.replay ? `<div class="card"><b>Read back with <code>conv msg list</code></b> — ${S.replay.messages} messages; each IMAGE block's uri resolved with <code>lib get</code>:
      <div class="row" style="gap:10px;flex-wrap:wrap;margin-top:8px">${S.replay.images.map((i) => `<span class="chat-img"><span class="th" ${bg(i.name)}></span><span class="mono">${esc(i.name)}</span><span class="faint">${esc(i.mime)}</span></span>`).join("")}</div></div>` : "";
    return `
      <h1>The brand review: images sent in the chat, rules pinned</h1>
      <p class="lead">Dana signs off the autumn bag and retires the old wordmark, sending each picture in the chat as an <code>IMAGE</code> content block
      that points at the Library file. Her rules — hex codes, accent-only red, clear space — are pinned verbatim with <code>fact add</code>.</p>
      <div class="card">
        <div class="call-head"><h3>${esc(r.name)}</h3><span class="muted small">${fmtDate(r.date)}</span><span style="margin-left:auto">${cook}</span></div>
        <div class="turns">${r.turns.map(([k, text, image], i) => `
          <div class="turn ${i < st.stored ? "stored" : ""}"><span class="avatar ${side(k)}">${initials(S.data.people[k].display)}</span>
            <div><div class="who">${esc(S.data.people[k].display)} · ${esc(S.data.people[k].role)}</div><div class="what">${hl(text)}</div>
              ${image ? `<span class="chat-img"><span class="th" ${bg(image)}></span><span>IMAGE · <span class="mono">${esc(image)}</span></span></span>` : ""}</div>
            <span class="mark">${i < st.stored ? "✓ stored" : "msg " + (i + 1)}</span></div>`).join("")}</div>
        <div class="notes">${r.pinned.map((n) => `<span class="note-chip ${st.pinned ? "" : "pending"}">📌 ${hl(n)}</span>`).join("")}</div>
      </div>
      ${replay}${facts}`;
  }

  function answerCard(a) {
    const top = a.hits[0];
    return `<div class="card answer"><h3>${esc(a.heading)}</h3>
      <div class="faint small mono">search "${esc(a.query)}" --types document</div>
      <div class="answer-grid">
        ${top ? `<div class="hero ${a.ok ? "" : "miss"}" ${bg(top.name)}></div>` : `<div class="empty">nothing matched</div>`}
        <div><div class="ranks">${a.hits.slice(0, 3).map((h, i) => `<div class="rankrow"><span class="r">#${i + 1}</span><span class="th" ${bg(h.name)}></span>
            <span class="s"><b class="mono">${esc(h.name)}</b> ${esc(h.summary)}</span></div>`).join("")}</div>
          ${a.rules?.length ? `<ul class="rules">${a.rules.map((r) => `<li>${hl(r)}</li>`).join("")}</ul>` : ""}
          ${a.left_out ? `<div class="faint small">${a.left_out} other fact hit(s) left out: not about colour</div>` : ""}
          <div class="verdict ${a.ok ? "ok" : "miss"}">${a.ok ? `✓ rank 1 is ${esc(a.top)} — the file the brand lead would have sent` : `✗ rank 1 is ${esc(a.top)}; the brand lead would have sent ${esc(a.expect)}`}</div>
          ${a.download ? `<div class="muted small">↓ <a href="/out/assets/${encodeURIComponent(top.name)}" target="_blank" rel="noopener">${esc(a.download.path)}</a> · ${a.download.size.toLocaleString()} bytes${a.download.identical ? " · byte-for-byte the exported file" : ""}</div>` : ""}
        </div></div></div>`;
  }

  function answers() {
    if (!S.answers.length) return `<h1>${esc(S.data?.designer.display || "The designer")} asks</h1><div class="empty">Run the demo first — five searches run once the memory is ready.</div>`;
    const ok = S.answers.filter((a) => a.ok).length;
    return `
      <div class="brief-head"><div><h1>${esc(S.data.designer.display)} starts on winter packaging — and asks the memory</h1>
        <p class="lead" style="margin:0">Plain questions, no file names. Each answer is the top image hit — chosen on what is in the picture — and its original file, downloaded from memory.
        ${S.brief ? `<b>${S.brief.ok}/${S.brief.total}</b> with the expected image at rank 1.` : `<span class="pill busy"><span class="spinner"></span> ${ok}/${S.answers.length} so far…</span>`}</p></div>
        ${S.brief ? `<a class="btn" href="/out/brand-brief.md" download="brand-brief.md">Download brief .md</a>` : ""}</div>
      ${S.answers.map(answerCard).join("")}`;
  }

  function retire() {
    const r = S.retired; const q = S.data?.retire;
    return `
      <h1>Retire the 2019 wordmark — and it stops coming back</h1>
      <p class="lead"><code>proj doc delete</code> removes the document and everything MemoryLake derived from it (the Library file stays).
      The same question, asked before and after, shows the difference any tool reading this memory will see.</p>
      ${q ? `<div class="faint small mono">search "${esc(q.query)}" --types document</div>` : ""}
      ${r ? `<div class="beforeafter" style="margin-top:12px">
        <div class="card"><div class="row spread"><b>Before</b><span class="pill accent">${r.before ? `rank ${r.before}` : "not returned"}</span></div>
          <div class="seen" style="grid-template-columns:1fr;margin-top:10px"><div class="img" ${bg(r.file)}></div></div><div class="mono small">${esc(r.file)}</div></div>
        <div class="card"><div class="row spread"><b>After</b><span class="pill ${r.after ? "err" : "ok"}">${r.after ? `still rank ${r.after}` : "not returned"}</span></div>
          <div class="seen" style="grid-template-columns:1fr;margin-top:10px"><div class="img gone" ${bg(r.file)}></div></div>
          <div class="muted small">${r.top_after ? `top hit now: <span class="mono">${esc(r.top_after)}</span>` : ""}</div></div></div>` : `<div class="empty">Run the demo first — this is the last step.</div>`}`;
  }

  function ask() {
    const sugg = [...(S.data?.questions || []).map((q) => q.query), "a bag design with an orange blend name", "something with a red cross on it", "what text is on the Instagram graphic"];
    return `
      <h1>Ask the brand memory</h1>
      <p class="lead">The same <code>memorylake search</code> the designer's answers come from. Describe a picture the way you would to a colleague.
      Ranking is sensitive to wording — try a few.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. the one with three crossed-out logos" value="${esc(S.ask.q)}" ${S.project ? "" : "disabled"}><button class="btn primary" ${S.project ? "" : "disabled"}>Search</button></form>
        <div class="chips">${sugg.map((q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
        <div id="ask-result" class="result">${S.project ? S.ask.html : '<div class="empty">Run the demo first.</div>'}</div></div>`;
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
    const form = stage.querySelector("#ask-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); askQuery($("#ask-q").value.trim()); });
      stage.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { $("#ask-q").value = c.dataset.q; askQuery(c.dataset.q); }));
    }
  }

  async function askQuery(q) {
    if (!q) return;
    S.ask.q = q;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> searching…</span>`;
    const r = await api("/api/search", { query: q, top_k: 3 });
    if (!r) { box.innerHTML = ""; return; }
    S.ask.html = `
      <h2 style="margin-top:8px">Images</h2>
      ${r.images.length ? r.images.map((h, i) => `<div class="card seen" style="margin-top:8px"><div class="img" ${bg(h.name)}></div>
        <div><b class="mono">#${i + 1} ${esc(h.name)}</b><dl><dt>saw</dt><dd>${esc(h.summary)}</dd>${h.text ? `<dt>read on it</dt><dd><span class="ocr">${hl(h.text)}</span></dd>` : ""}</dl></div></div>`).join("") : '<p class="muted">No image matched.</p>'}
      ${r.facts.length ? `<h2>Facts</h2>${r.facts.map((f) => `<div class="fact"><span>${hl(f.fact)}</span><span class="tag" title="score">${(f.score ?? 0).toFixed(2)}</span></div>`).join("")}` : ""}`;
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
    if (reset || !S.project) { resetRun(); S.stepStatus = { 1: "done" }; }
    S.follow = true;
    const r = await api("/api/run", { reset });
    if (r) { S.st = r; setConn(); }
  }

  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo project, its review and images, the demo actors and the uploaded files from your account?")) return;
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
      if (d.index === 2) resetRun();
      term("step", ev.text);
      if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    } else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "actor") S.actors[d.key] = d;
    else if (k === "project") S.project = { id: d.id, name: d.name, fresh: !!d.fresh };
    else if (k === "uploaded") S.files[d.name] = { status: "uploaded" };
    else if (k === "imported") Object.keys(S.files).forEach((n) => (S.files[n].status = "imported"));
    else if (k === "documents") (d.documents || []).forEach((x) => (S.files[x.name] = { status: x.status }));
    else if (k === "seen") S.seen = d.assets;
    else if (k === "review") S.review = { id: d.id, stored: d.done || 0, cooked: (d.done || 0) >= d.turns, pinned: (d.done || 0) > 0 };
    else if (k === "turn") S.review.stored = d.index;
    else if (k === "cooked") S.review.cooked = true;
    else if (k === "pinned") S.review.pinned = true;
    else if (k === "replay") S.replay = d;
    else if (k === "facts") S.facts = d.facts;
    else if (k === "answer") { if (S.brief) { S.brief = null; S.answers = []; } S.answers.push(d); }
    else if (k === "brief") S.brief = d;
    else if (k === "retired") S.retired = d;
    else if (k === "done") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
      if (d.job === "cleanup") { S.project = null; S.actors = {}; resetRun(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the demo data is gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Open the designer's answers, or describe a picture in Ask the memory.", true);
      term("dim", `── ${d.job} finished`);
      S.st.status = "done"; setConn();
      if (S.caughtUp) refreshState();
    } else if (k === "error") {
      Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "error"; });
      term("error", ev.text); if (S.caughtUp) banner(ev.text);
      S.st.status = "error"; setConn(); refreshState();
    }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
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
    S.st = state; S.data = data; if (state.project) S.project = state.project;
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
    show(state.status === "running" ? S.view : state.has_brief ? "answers" : "connect");
    subscribe();
  })();
})();

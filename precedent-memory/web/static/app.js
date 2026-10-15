/* Precedent memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "two projects, two boundaries", view: "setup" },
    { n: 3, title: "What the firm knows", sub: "16 memos, 10 citator notes", view: "memory" },
    { n: 4, title: "The Tarrow memo", sub: "page by page", view: "pages" },
    { n: 5, title: "Memos only", sub: "through MemoryRouter", view: "memos" },
    { n: 6, title: "Memos + citator", sub: "through MemoryRouter", view: "citator" },
    { n: 7, title: "Control", sub: "pages handed over directly", view: "control" },
    { n: 8, title: "Citator check", sub: "every cited case", view: "check" },
    { n: 9, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, pages: null, asked: [], tallies: {}, control: [], check: null, summary: null, unavailable: null,
    cites: [], citeText: "Tarrow Freight v. Delling holds that a non-compete is void when the signed agreement only arrives on the first day.",
    termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = { memos: "memos only", citator: "memos + citator" };
  const MARK = { bad: ["✗", "bad"], flagged: ["✓", "ok"], none: ["·", ""] };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "cite" ? "active" : ""}" data-view="cite"><span class="n">§</span><span class="t">Cite check<small>any text, any time</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, pages, memos: () => arm("memos"), citator: () => arm("citator"), control, check, score, cite }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (v) => { const m = MARK[v] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}</span>`; };
  const attached = (c) => {
    if (!c.readable) return '<span class="lab">unreadable</span>';
    const p = (c.order && c.order.length ? c.order : c.pages) || [];
    const name = { 1: "p1 holding", 2: "p2", 3: "p3", 4: "p4 addendum" };
    return (p.length ? p.map((n) => `<span class="lab ${n === 4 ? "lab-ok" : n === 1 ? "lab-bad" : ""}">${name[n]}</span>`).join(" → ") : '<span class="lab">no Tarrow page</span>')
      + (c.citator ? ' <span class="lab lab-ok">citator: REVERSED</span>' : "");
  };
  const tallyText = (t) => t ? `${t.bad}/${t.answers} answers cite Tarrow as good law · ${t.flagged} say it was reversed · ${t.none} do not use it` : "";

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.memos)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-prm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The memo says “reversed” on page 4. Did the assistant get page 4?</h1>
      <p class="lead">Lind &amp; Okafor (fictional) keeps one PDF memo per precedent. The research assistant runs on MemoryRouter: the memos sit in a MemoryLake project,
      and every question is an ordinary model call with <code>--boundary</code> added. MemoryRouter attaches documents <b>one page at a time</b>, picked by how well each page
      matches the question. The Tarrow Freight v. Delling memo states its holding on page 1 — and on page 4, an addendum: reversed on appeal. This demo asks three associate
      questions through MemoryRouter, shows which Tarrow pages were attached, and adds the fix: a citator. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 48 calls per run (gpt-4o-mini answers, claude-haiku-4-5 checker).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/case-memory-for-legal-teams" target="_blank" rel="noopener">memorylake.ai › Case memory for legal teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Two projects, two boundaries</h1>
      <p class="lead">The memos go in one project, the citator — one status note per case — in another. Two boundaries over them: the assistant as most firms would wire it
      (memos only), and the same assistant with the citator added. The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv"><dt>Memos</dt><dd class="mono small">${esc(w?.memos || "pending")}</dd>
        <dt>Citator</dt><dd class="mono small">${esc(w?.citator || "pending")}</dd>
        <dt>Boundary: memos only</dt><dd class="mono small"><b>${esc(w?.boundary?.memos || "pending")}</b></dd>
        <dt>Boundary: + citator</dt><dd class="mono small"><b>${esc(w?.boundary?.citator || "pending")}</b></dd></dl></div>
        <div class="card"><h3 style="margin-top:0">The questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)}</p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory) return running() ? waiting("What the firm knows") : empty("What the firm knows");
    const memos = S.data?.memos || [], cit = S.data?.citator || [];
    return `<h1>${S.memory.memos} precedent memos and ${S.memory.citator} citator notes</h1>
      <p class="lead">The memos are PDFs (<code>lib upload</code> + <code>proj doc import --wait</code>); the citator notes are stored verbatim with <code>fact add</code>, one per
      non-compete case, each naming its case.</p>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">Memos</h3>${memos.map((m) => `<div class="memfact small"><span><b>${esc(m.case)}</b>
        <span class="muted">${esc(m.court)}, ${esc(m.decided)} · ${m.pages} pages</span></span></div>`).join("")}</div>
        <div class="card"><h3 style="margin-top:0">Citator</h3>${cit.map((c) => `<div class="memfact small ${c.status === "REVERSED" ? "wrong" : ""}"><span>
          ${c.status === "REVERSED" ? '<span class="lab lab-bad">REVERSED</span> ' : ""}${esc(c.text)}</span></div>`).join("")}</div></div>`;
  }

  function pages() {
    const h = "The Tarrow memo, page by page";
    if (!S.pages) return running() ? waiting(h) : empty(h);
    return `<h1>${h} — the reversal is in memory, on page ${S.pages.pages[S.pages.pages.length - 1]?.page}</h1>
      <p class="lead"><code>search --types document</code> returns the memo one page per entry (page number from <code>range</code>). MemoryRouter attaches documents at this
      same unit: each page is its own entry, picked by how well it matches the question.</p>
      <div class="card">${S.pages.pages.map((p) => `<div class="memfact small ${/Reversed/.test(p.text) ? "" : ""}"><span><span class="lab ${/Reversed/.test(p.text) ? "lab-ok" : ""}">page ${p.page}</span>
        ${esc(p.text.replace(/\s+/g, " ").slice(0, 330))}</span></div>`).join("")}</div>`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${ARMS[name]}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const lead = name === "memos"
      ? `Boundary over the memos only. Each question is answered ${S.data?.runs || 4}× by ${esc(S.data?.model)}; a checker call (${esc(S.data?.check_model)}) copies out what
         it was given, ${S.data?.checks || 2}× per question. <span class="lab lab-bad">p1 holding</span> without <span class="lab lab-ok">p4 addendum</span> is the gap.`
      : `The same questions, the boundary now covering the citator too. The note for Tarrow names the case, so it matches the questions the holding page matches.`;
    return `<h1>${h}${t ? ` — ${t.bad}/${t.answers} cite Tarrow as good law` : ""}</h1>
      <p class="lead">${lead} ✗ cites Tarrow as good law · ✓ says it was reversed · · does not use Tarrow.</p>
      ${rows.map((r) => {
        const q = (S.data?.questions || []).find((x) => x.id === r.q);
        const bad = r.answers.find((a) => a.verdict === "bad"), good = r.answers.find((a) => a.verdict === "flagged");
        const show = name === "memos" ? (bad || good || r.answers[0]) : (good || r.answers[0]);
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(q?.say)}</span></h3>
          <div class="row"><span>answers</span> ${r.answers.map((a) => mark(a.verdict)).join(" ")}</div>
          ${r.checks.map((c, i) => `<div class="small" style="margin-top:6px">checker ${i + 1}: ${attached(c)}</div>`).join("")}
          ${show ? `<div class="chat"><div class="msg bot ${show.verdict === "bad" ? "wrong" : ""}"><span class="who">answer ${mark(show.verdict)}</span>${esc(show.reply)}</div></div>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${esc(tallyText(t))} · checker: holding page ${t.holding}/${t.checks}, addendum ${t.addendum}/${t.checks} (before the holding page ${t.addendum_first}), citator note ${t.citator}/${t.checks}</div>` : ""}`;
  }

  function control() {
    const h = "Control — the pages handed over directly";
    if (!S.control.length) return unavailable() || (running() ? waiting(h) : empty(h));
    return `<h1>${h}</h1>
      <p class="lead">No boundary: <code>router chat --body</code> with the Tarrow pages in the system prompt, laid out the way MemoryRouter lays out an attached page.
      Same questions, same model. Only which pages are there, and in what order, changes.</p>
      ${S.control.map((c) => { const bad = c.answers.filter((a) => a.verdict === "bad").length, ex = c.answers.find((a) => a.verdict === (c.variant === "p1" ? "bad" : "flagged")) || c.answers[0];
        return `<div class="card"><h3 style="margin-top:0">${esc(c.label)} <span class="pill ${bad ? "err" : "ok"}">${bad}/${c.answers.length} cite Tarrow as good law</span></h3>
          <div class="row">${(S.data?.questions || []).map((q) => `<span><b>${esc(q.id)}</b> ${c.answers.filter((a) => a.q === q.id).map((a) => mark(a.verdict)).join(" ")}</span>`).join("&nbsp;&nbsp;&nbsp;")}</div>
          ${ex ? `<div class="chat"><div class="msg bot ${ex.verdict === "bad" ? "wrong" : ""}"><span class="who">${esc(ex.q)} ${mark(ex.verdict)}</span>${esc(ex.reply)}</div></div>` : ""}</div>`; }).join("")}`;
  }

  function check() {
    const h = "Citator check — after the model";
    if (!S.check) return running() ? waiting(h) : empty(h);
    const row = (k) => { const c = S.check[k]; return c ? `<tr><td><b>${esc(ARMS[k])}</b></td><td>${c.answers}</td><td>${c.bad}</td><td><b>${c.held}</b></td><td>${c.bad <= c.held ? "yes" : "no"}</td></tr>` : ""; };
    return `<h1>${h}</h1>
      <p class="lead">Whatever MemoryRouter attached, the app can check the answer: every case it names is looked up with
      <code>fact list --projects &lt;citator&gt; --query "&lt;case&gt;"</code> — an exact substring match, not a ranking — and an answer that leans on a REVERSED case without
      saying so is held back.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>answers</th><th>✗ (cite Tarrow as good law)</th><th>held back by the check</th><th>every ✗ held</th></tr></thead>
        <tbody>${row("memos")}${row("citator")}</tbody></table></div>`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>answers ✗ / ✓ / ·</th><th>checker: holding page</th><th>addendum</th><th>citator note</th><th>holding without addendum or note</th><th>addendum before holding</th></tr></thead><tbody>
        ${Object.keys(ARMS).filter((k) => t[k]).map((k) => `<tr><td><b>${esc(ARMS[k])}</b></td><td>${t[k].bad} / ${t[k].flagged} / ${t[k].none} of ${t[k].answers}</td>
          <td>${t[k].holding}/${t[k].checks}</td><td>${t[k].addendum}/${t[k].checks}</td><td>${t[k].citator}/${t[k].checks}</td><td>${t[k].holding_alone}/${t[k].checks}</td><td>${t[k].addendum_first}/${t[k].checks}</td></tr>`).join("")}
        ${S.control.map((c) => `<tr><td><b>${esc(c.label)}</b> <span class="muted small">no boundary</span></td><td>${c.answers.filter((a) => a.verdict === "bad").length} / ${c.answers.filter((a) => a.verdict === "flagged").length} / ${c.answers.filter((a) => a.verdict === "none").length} of ${c.answers.length}</td><td colspan="5" class="muted small">pages handed over directly, in this order</td></tr>`).join("")}
      </tbody></table>
      ${S.check ? `<p class="small">Citator check after the model: held back ${S.check.memos?.held ?? 0} of ${S.check.memos?.answers ?? 0} (memos only) and ${S.check.citator?.held ?? 0} of ${S.check.citator?.answers ?? 0} (memos + citator).</p>` : ""}</div>
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      What MemoryRouter attaches varies from call to call; the README reports totals over all runs.</p>`;
  }

  function cite() {
    const ready = !!S.world;
    return `<h1>Cite check</h1>
      <p class="lead">Paste a draft paragraph or an answer from anywhere. Every case from the memo library it names is looked up in the citator.</p>
      <div class="card"><textarea id="cite-text" rows="4">${esc(S.citeText)}</textarea>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="cite" ${ready && !running() ? "" : "disabled"}>Check</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat">${S.cites.slice().reverse().map((c) => `<div class="msg bot ${c.held ? "wrong" : ""}"><span class="who">${c.cases.length ? c.cases.map((k) => `${c.status[k] === "REVERSED" ? "✗" : "✓"} ${esc(k)}: ${esc(c.status[k])}`).join(" · ") : "no case from the library"}</span>
          ${c.reversed.length ? (c.held ? "<b>Hold:</b> the text leans on a reversed case and does not say so. " : "The text names the reversal. ") + esc(c.note) : "Nothing to hold."}</div>`).join("")}</div></div>`;
  }

  function bindStage() {
    const st = $("#stage");
    st.querySelector("#connect-form")?.addEventListener("submit", async (e) => {
      e.preventDefault(); const btn = $("#btn-connect"); btn.disabled = true; btn.textContent = "Connecting…";
      const res = await api("/api/connect", { api_key: $("#api-key").value.trim(), base_url: $("#base-url").value, workspace: $("#workspace").value.trim() });
      if (res) { S.st = res; S.stepStatus[1] = "done"; setConn(); renderSteps(); renderStage(); } else { btn.disabled = false; btn.textContent = "Connect"; }
    });
    st.querySelector('[data-act="eye"]')?.addEventListener("click", (e) => { const i = $("#api-key"); i.type = i.type === "password" ? "text" : "password"; e.target.textContent = i.type === "password" ? "show" : "hide"; });
    st.querySelector('[data-act="run"]')?.addEventListener("click", () => runDemo());
    st.querySelector("#cite-text")?.addEventListener("input", (e) => { S.citeText = e.target.value; });
    st.querySelector('[data-act="cite"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Checking…";
      const r = await api("/api/cite", { text: S.citeText });
      if (r) S.cites.push(r);
      if (S.view === "cite") renderStage();
    });
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
    ["#btn-run", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.memory = null; S.pages = null; S.asked = []; S.tallies = {}; S.control = []; S.check = null; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, both projects, the memos and the citator notes?")) return;
    banner(""); const r = await api("/api/cleanup", {}); if (r) { S.st = r; setConn(); }
  });

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

  const stepOf = (i) => STEPS.find((s) => s.n === i);
  function onEvent(ev) {
    S.lastEvent = ev.id; const k = ev.kind, d = ev.data || {};
    const finishRunning = (to) => Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = to; });
    if (k === "step") { finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView(); term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "memory") S.memory = d;
    else if (k === "pages") S.pages = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "control") S.control.push(d);
    else if (k === "citator_check") S.check = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, both projects, the memos and the citator notes are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Cite check: paste any paragraph that names a case.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "pages", "asked", "tally", "control", "citator_check", "summary", "router_unavailable", "cite", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
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
    S.st = state; setConn();
    S.caughtUp = true;
    if (state.status !== "running") Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; });
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.summary ? "score" : "connect");
    subscribe();
  })();
})();

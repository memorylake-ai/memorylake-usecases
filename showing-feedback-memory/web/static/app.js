/* Showing feedback memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const ARMS = ["0", "A", "B", "C"];
  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, one boundary each", view: "setup" },
    { n: 3, title: "Texts", sub: "eight showings, six texts without a house", view: "texts" },
    { n: 4, title: "What memory holds", sub: "per house, and facts with no house", view: "census" },
    { n: 5, title: "C: showing records", sub: "one per showing, from the schedule", view: "records" },
    { n: 6, title: "Ask the assistant", sub: "no memory + three memories", view: "ask" },
    { n: 7, title: "Score", sub: "per question and per memory", view: "score" },
  ];
  const fresh = () => ({ textsSeen: false, census: null, records: null, rows: { 0: [], A: [], B: [], C: [] }, score: null, unavailable: null, summary: null });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    tries: [], tq: "What concerns have buyers raised about 14 Alder Lane after showings? List each concern once.", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const RUNS = () => S.data?.runs || 4;
  const LISTINGS = () => S.data?.listings || [];
  const QS = () => S.data?.questions || [];
  const LABEL = (a) => (S.data?.arms || {})[a] || a;

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">↻</span><span class="t">Ask one<small>no memory, A, B and C</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, texts: textsView, census: censusView, records: recordsView, ask: askView, score, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const MARKS = { ok: ["✓", "ok", "right"], mix: ["mix", "bad", "another house's feedback mixed in"],
    wrong: ["✗", "bad", "wrong"], unknown: ["?", "warn", "says it does not know"] };
  const markOf = (r) => !r.grade ? '<span class="mark">·</span>' : r.empty ? '<span class="mark empty" title="nothing attached">∅</span>'
    : `<span class="mark ${MARKS[r.grade][1]}" title="${esc(MARKS[r.grade][2])}">${MARKS[r.grade][0]}</span>`;
  // the same counts as the CLI's tally(): ∅ calls are listed, not graded
  function tally(rows) {
    const live = rows.filter((r) => r.grade && !r.empty), c = (g) => live.filter((r) => r.grade === g).length;
    return { n: live.length, empty: rows.filter((r) => r.grade && r.empty).length, ok: c("ok"), mix: c("mix"), wrong: c("wrong"), unknown: c("unknown") };
  }
  const pct = (k, n) => n ? Math.floor((100 * k) / n + 0.5) : 0;
  const tline = (t) => `right <b>${t.ok}</b>/${t.n} · mix <b class="${t.mix ? "bad-ink" : ""}">${t.mix}</b> · ✗ <b>${t.wrong}</b> · ? <b>${t.unknown}</b>${t.empty ? ` · <b>${t.empty}</b> ∅` : ""}`;
  const ADDR = /(\b14 Alder Lane\b|\b22 Birch Court\b|\b9 Cedar Row\b)/g;
  const hiAddr = (t) => esc(t).replace(ADDR, (m) => `<span class="stamp">${m}</span>`);

  function connect() {
    const st = S.st, d = S.data || {};
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.projects?.texted)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (16 texts read in parallel, then ~70 Router calls) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-sfm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Feedback texts that don't say which house get pinned on every house.</h1>
      <p class="lead">${esc(d.org || "Wrenfield Homes")} (a fictional brokerage): ${esc(d.agent || "Rhea Calloway")} has three listings on the market. Four buyer's agents showed them eight times and texted feedback afterwards — two texts name the house, six don't (“Thanks for letting us in today. My buyers felt it was overpriced for the size …”). The showing schedule knows which house each one was about; the text doesn't say.
      Three memories: <b>A</b> every text as it arrived · <b>B</b> the same texts with the address the app looked up in the schedule written in front · <b>C</b> one record per showing, written by the app with <code>fact add</code> — and <b>0</b>, the same questions with no memory. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: about 70 calls on gpt-4o-mini per run.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/showing-feedback-memory-for-real-estate-teams" target="_blank" rel="noopener">memorylake.ai › Showing feedback memory for real estate teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, one boundary each</h1>
      <p class="lead">A gets the eight texts as sent; B the same eight texts, each with the address the app looked up written in front; C gets only the app's showing records. All three carry the same plain name: a project's name is context for extraction.</p>
      <div class="card"><dl class="kv"><dt>A · as texted</dt><dd class="mono small">${esc(w?.projects?.texted || "pending")}</dd><dt>B · address stamped</dt><dd class="mono small">${esc(w?.projects?.stamped || "pending")}</dd>
        <dt>C · showing records</dt><dd class="mono small">${esc(w?.projects?.records || "pending")}</dd>
        ${(S.data?.people || []).map((p) => `<dt>${esc(p.display)} · ${esc(p.brokerage)}</dt><dd class="mono small">${esc(w?.actors?.[p.key] || "pending")}</dd>`).join("")}</dl></div>`;
  }

  function textsView() {
    if (!S.data || (!S.textsSeen && !S.world)) return running() ? waiting("Texts") : empty("Texts");
    const d = S.data, unnamed = d.texts.filter((t) => !t.named);
    return `<h1>Eight showings, eight texts — ${unnamed.length} don't say which house</h1>
      <p class="lead">Each text is one message in its own GROUP conversation, written by the buyer's agent and stamped with the time it was sent. The app matches a text to the last showing by the same buyer's agent before it — that is where the address comes from in B and C.</p>
      <div class="card"><h3 style="margin-top:0">The showing schedule</h3><table class="lint plain"><thead><tr><th>showing</th><th>when (UTC)</th><th>house</th><th>buyer's agent</th></tr></thead><tbody>
      ${d.showings.map((s) => `<tr><td class="small mono">${esc(s.id)}</td><td class="small mono">${esc(s.start.slice(0, 10))} ${esc(s.start.slice(11, 16))}–${esc(s.end.slice(11, 16))}</td>
        <td class="small"><b>${esc(s.address)}</b></td><td class="small">${esc(s.person)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 style="margin-top:0">The texts</h3><table class="lint plain"><thead><tr><th>sent (UTC)</th><th>from</th><th>text</th><th>names the house?</th><th>the schedule says</th></tr></thead><tbody>
      ${d.texts.map((t) => `<tr class="${t.named ? "" : "hl"}"><td class="small mono">${esc(t.ts.slice(0, 10))} ${esc(t.ts.slice(11, 16))}</td><td class="small">${esc(t.person)}</td>
        <td class="small">${hiAddr(t.text)}</td><td class="small">${t.named ? '<span class="lab lab-ok">yes</span>' : '<span class="lab lab-bad">no</span>'}</td>
        <td class="small">${esc(t.showing)} → <b>${esc(t.address)}</b></td></tr>`).join("")}</tbody></table></div>
      ${unnamed.length ? `<div class="card"><h3 style="margin-top:0">The same text in A and in B</h3><div class="copyrow small"><b>A</b> · ${hiAddr(unnamed[0].message)}</div>
        <div class="copyrow small"><b>B</b> · ${hiAddr(unnamed[0].stamped)}</div></div>` : ""}`;
  }

  function censusView() {
    if (!S.census) return running() ? waiting("What memory holds") : empty("What memory holds");
    const cs = S.census, A = cs.texted, B = cs.stamped;
    const cell = (v) => !v.facts ? `<span class="lab lab-bad">not in memory</span>`
      : v.addressed ? `<span class="lab lab-ok">names the house</span> <span class="muted">${v.facts} fact${v.facts === 1 ? "" : "s"}</span>`
      : `<span class="lab lab-bad">no address</span> <span class="muted">${v.facts} fact${v.facts === 1 ? "" : "s"}</span>`;
    return `<h1>A: ${A.no_address.length} of ${A.facts} facts name no house · B: ${B.no_address.length} of ${B.facts}</h1>
      <p class="lead"><code>fact list</code> on A and B. One row per concern a buyer raised: is it in memory, and does the fact say which house? A fact with no house is true of some house — the assistant has no way to tell which.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>house</th><th>concern</th><th>A · as texted</th><th>B · address stamped</th></tr></thead><tbody>
      ${LISTINGS().flatMap((x) => Object.entries(x.concerns).map(([k, label]) => { const a = A.concerns[`${x.id}.${k}`], b = B.concerns[`${x.id}.${k}`];
        return `<tr class="${!a.addressed || !b.addressed ? "hl" : ""}"><td class="small"><b>${esc(x.address)}</b></td><td class="small">${esc(label)}</td><td class="small">${cell(a)}</td><td class="small">${cell(b)}</td></tr>`; })).join("")}
      </tbody></table><p class="small" style="margin-bottom:0">A: ${A.kept}/${A.total} concerns in memory, ${A.kept_addressed}/${A.total} with the house named · B: ${B.kept}/${B.total}, ${B.kept_addressed}/${B.total}</p></div>
      ${[["A", A], ["B", B]].map(([a, c]) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))} — facts that name no house</h3>
        ${c.no_address.map((f) => `<div class="copyrow small">${esc(f)}</div>`).join("") || '<p class="muted small">none</p>'}</div>`).join("")}`;
  }

  function recordsView() {
    if (!S.records) return running() ? waiting("C: showing records") : empty("C: showing records");
    const r = S.records;
    return `<h1>C: ${r.records.length} texts → ${r.records.length} showing records, each with its house</h1>
      <p class="lead">The app takes the house from the showing schedule, not from the text: the last showing by the same buyer's agent before the text was sent. One <code>fact add</code> writes one record per showing, the text quoted verbatim — nothing is left to extraction.</p>
      <div class="card"><h3 style="margin-top:0">Showing records</h3>${r.records.map((t) => `<div class="copyrow small">${hiAddr(t)}</div>`).join("")}</div>`;
  }

  function askView() {
    const all = ARMS.flatMap((a) => S.rows[a]);
    if (!all.length) return unavailable() || (running() ? waiting("Ask the assistant") : empty("Ask the assistant"));
    const t = Object.fromEntries(ARMS.map((a) => [a, tally(S.rows[a])]));
    return `<h1>The listing assistant — right: ${ARMS.filter((a) => S.rows[a].length).map((a) => `${a} ${t[a].ok}/${t[a].n}`).join(" · ")}</h1>
      <p class="lead">Each question ${RUNS()} times at temperature 0.7 per memory, plus one call without a boundary as the ∅ baseline. <b>mix</b> marks an answer that puts another house's feedback on this one (or names another house as the overpriced one).</p>
      ${ARMS.filter((a) => S.rows[a].length).map((a) => `<div class="card"><h3 style="margin-top:0">${esc(LABEL(a))}</h3><table class="lint plain"><thead><tr><th>question</th><th>truth</th><th>${RUNS()} answers</th></tr></thead><tbody>
        ${QS().map((q) => { const rs = S.rows[a].filter((r) => r.item === q.id); const bad = rs.find((r) => r.grade === "mix") || rs.find((r) => r.grade && r.grade !== "ok" && !r.empty);
          return `<tr class="${bad ? "hl" : ""}"><td class="small">${esc(q.ask)}</td><td class="small">${esc(q.truth)}</td><td><div class="cell-ans">${rs.map(markOf).join(" ")}</div>
            <div class="small muted">${esc((bad || rs.find((r) => r.reply) || {}).reply || "")}</div></td></tr>`; }).join("")}
        </tbody></table><p class="small">${tline(t[a])}</p></div>`).join("")}`;
  }

  function score() {
    if (!S.score) return running() ? waiting("Score") : empty("Score");
    const sc = S.score, bi = sc.by_item || {}, A = sc.answers;
    const cellc = (x) => x ? `${x.ok}/${x.n}` + (x.mix ? ` <span class="bad-ink">mix ${x.mix}</span>` : "") + (x.empty ? ` <span class="muted">∅${x.empty}</span>` : "") + ` <span class="small muted">${esc(x.marks)}</span>` : "";
    const bar = (k, n, cls) => `<div class="sbar"><span class="${cls}" style="width:${pct(k, n)}%"></span></div>`;
    return `<h1>Right: ${ARMS.map((a) => `${a} ${A[a].ok}/${A[a].n}`).join(" · ")} · another house mixed in: A ${A.A.mix} · B ${A.B.mix} · C ${A.C.mix}</h1>
      <p class="lead">${QS().length * RUNS()} answers per memory. A and B hold the same texts; B only has the address the app looked up written in front of each, so mix in A and not in B is what the missing address did. B still loses whatever extraction drops — C keeps every text verbatim.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>memory</th><th>right</th><th class="num">mix</th><th class="num">✗</th><th class="num">?</th></tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(LABEL(a))}</b></td>
          <td><b>${A[a].ok}/${A[a].n}</b> <span class="muted">${pct(A[a].ok, A[a].n)}%</span>${bar(A[a].ok, A[a].n, "g2")}</td>
          <td class="num"><b class="${A[a].mix ? "bad-ink" : ""}">${A[a].mix}</b></td><td class="num">${A[a].wrong}</td><td class="num">${A[a].unknown}</td></tr>`).join("")}</tbody></table>
        ${ARMS.some((a) => A[a].empty) ? `<p class="small muted" style="margin-bottom:0">∅ (MemoryRouter attached nothing; listed, not graded): ${ARMS.map((a) => `${a} ${A[a].empty}`).join(" · ")}</p>` : ""}</div>
      <div class="card"><h3 style="margin-top:0">Per question</h3><table class="lint plain"><thead><tr><th>question</th>${ARMS.map((a) => `<th class="num">${a}</th>`).join("")}</tr></thead><tbody>
        ${QS().map((q) => `<tr><td class="small">${esc(q.id)} ${esc(q.what)} <span class="muted">= ${esc(q.truth)}</span></td>${ARMS.map((a) => `<td class="num">${cellc((bi[q.id] || {})[a])}</td>`).join("")}</tr>`).join("")}</tbody></table>
        <p class="small muted" style="margin-bottom:0">✓ right · mix another house's feedback in the answer · ✗ wrong · ? says it does not know · ∅ nothing attached</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function tryView() {
    const ready = !!S.world;
    return `<h1>Ask one — no memory, then A, B and C</h1>
      <p class="lead">Any question to the listing assistant, once with no memory and once through each memory. Graded when it is one of the four.</p>
      <div class="card"><label for="t-q">Question</label><input id="t-q" value="${esc(S.tq)}">
        <div class="chips">${QS().map((q) => `<button class="chip" data-q="${esc(q.ask)}">${esc(q.what)}</button>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn primary" data-act="try" ${ready && !running() ? "" : "disabled"}>Ask</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div>
        <div class="chat" id="try-log">${S.tries.slice().reverse().map((x) => `<div class="msg user"><span class="who">Rhea → listing assistant</span>${esc(x.question)}</div>
          ${x.rows.map((r) => `<div class="msg bot ${r.grade === "ok" && !r.empty ? "" : r.grade ? "wrong" : ""}"><span class="who">${esc(LABEL(r.arm))} · ${r.empty ? "∅ nothing attached" : r.grade ? `${markOf(r)} ${esc(MARKS[r.grade][2])}` : ""}</span>${esc(r.reply)}</div>`).join("")}
          ${x.truth ? `<p class="small muted">truth: ${esc(x.truth)}</p>` : ""}`).join("")}</div></div>`;
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
    st.querySelector("#t-q")?.addEventListener("input", (e) => { S.tq = e.target.value; });
    st.querySelectorAll("[data-q]").forEach((b) => b.addEventListener("click", () => { S.tq = b.dataset.q; renderStage(); }));
    st.querySelector('[data-act="try"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Asking…";
      const r = await api("/api/ask", { question: S.tq });
      if (r) S.tries.push({ question: S.tq, rows: r.rows, truth: r.truth });
      if (S.view === "try") renderStage();
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
  function resetView() { Object.assign(S, fresh()); }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, conversations, the four buyer's agents (their facts first) and the three projects?")) return;
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
    else if (k === "texts") S.textsSeen = true;
    else if (k === "census") S.census = d;
    else if (k === "records") S.records = d;
    else if (k === "memory_view") S.census = d.census;
    else if (k === "ask_row") S.rows[d.arm].push(...d.rows);
    else if (k === "score") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — boundaries, conversations, the four buyer's agents (facts first) and the three projects are gone.", true); }
      else if (S.caughtUp) banner("Done. Try “Ask one”: any question to the listing assistant, with no memory and through A, B and C.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "texts", "resolved", "census", "records", "ask_row", "arm_done", "example",
    "score", "summary", "router_unavailable", "asked", "memory_view", "done", "error"];
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
    show(state.status === "running" ? S.view : S.score ? "score" : "connect");
    subscribe();
  })();
})();

/* Consent withdrawal memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "three projects, three boundaries", view: "setup" },
    { n: 3, title: "The interview notes", sub: "eight participants", view: "memory" },
    { n: 4, title: "Consent withdrawn", sub: "delete, then check the index", view: "withdrawn" },
    { n: 5, title: "What is left", sub: "nothing / register / notices", view: "left" },
    { n: 6, title: "What the model is handed", sub: "the checker's copy", view: "attached" },
    { n: 7, title: "Notes deleted", sub: "nothing left behind", view: "deleted" },
    { n: 8, title: "+ Consent register", sub: "one line per withdrawal", view: "register" },
    { n: 9, title: "+ Withdrawal notices", sub: "a notice per deleted file", view: "tombstone" },
    { n: 10, title: "Scoreboard", sub: "all calls this run", view: "score" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, memory: null, withdrawn: null, left: null, attached: null, asked: [], tallies: {}, summary: null, unavailable: null,
    lookups: [], pick: "Dana Reyes", termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const ARMS = ["deleted", "register", "tombstone"];
  const armLabel = (a) => S.data?.arms?.[a]?.label || a;
  const armShort = (a) => S.data?.arms?.[a]?.short || a;
  const MARK = { right: ["✓", "ok"], borrowed: ["⇄", "bad"], invented: ["✎", "bad"], used: ["✗", "bad"], leaked: ["!", "bad"], other: ["·", ""] };
  const ORDER = ["leaked", "used", "borrowed", "invented", "other", "right"];
  const person = (id) => (S.data?.notes || []).find((p) => p.id === id);

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "lookup" ? "active" : ""}" data-view="lookup"><span class="n">#</span><span class="t">Who<small>one name, three projects</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, withdrawn, left, attached, deleted: () => arm("deleted"), register: () => arm("register"), tombstone: () => arm("tombstone"), score, lookup: lookupTool }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const mark = (a) => { const m = MARK[a.verdict] || ["–", ""]; return `<span class="mark ${m[1]}">${m[0]}${a.empty ? "∅" : ""}</span>`; };
  const armLine = (t) => {
    if (!t) return "";
    const w = t.withdrawn, u = t.unlisted, c = t.control;
    return `withdrawn: ${w.right}/${w.answers} right · ${w.borrowed} someone else's words · ${w.invented} invented · ${w.used} “yes, quote them”${w.leaked ? ` · ${w.leaked} their deleted words` : ""}${w.empty ? ` (∅ ${w.empty})` : ""} · never interviewed ${u.right}/${u.answers} · controls ${c.right}/${c.answers}`;
  };

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.project?.deleted)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-cwm")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Their notes are deleted. The assistant still answers for them.</h1>
      <p class="lead">Tallyfox (fictional) runs customer interviews and keeps the notes in shared memory, so a research assistant can answer “what did Dana Reyes say about the
      onboarding checklist?”. Two of the eight participants withdraw consent, and the team does what it should: deletes their notes in every project and checks that the index
      holds none of their words. Then someone asks the assistant about them. It runs on MemoryRouter — every question is an ordinary model call with <code>--boundary</code> added —
      and MemoryRouter attaches by topic: other people's notes on the same topic, or nothing at all. This demo deletes the notes in three projects, leaves nothing / a consent register /
      a notice per person in their place, and asks eight questions through each boundary. It drives the <code>memorylake</code> CLI and MemoryRouter for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 89 calls per run (gpt-4o-mini, plus 9 checker calls to claude-haiku-4-5).</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/ai-memory-for-customer-research-teams" target="_blank" rel="noopener">memorylake.ai › AI memory for customer research teams</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const w = S.world;
    return `<h1>Three projects, three boundaries</h1>
      <p class="lead">The same study three times. In each, the two withdrawn participants' notes are deleted; what differs is what is left in their place:
      nothing; the team's consent register (who withdrew and when, do not quote — none of what they said); or a short notice under each deleted file's own name.
      One project each, one boundary over each. The assistant never assembles memory itself; MemoryRouter picks what to attach, call by call.</p>
      <div class="grid2"><div class="card"><dl class="kv">${ARMS.map((a) => `<dt>${esc(armShort(a))}</dt><dd class="mono small">${esc(w?.project?.[a] || "pending")}<br><b>${esc(w?.boundary?.[a] || "pending")}</b></dd>`).join("")}</dl></div>
        <div class="card"><h3 style="margin-top:0">The researcher's questions</h3>
          ${(S.data?.questions || []).map((q) => `<p class="small"><b>${esc(q.id)}</b> ${esc(q.say)} <span class="pill ${q.group === "control" ? "" : "err"}">${q.group === "withdrawn" ? "withdrew" : q.group === "unlisted" ? "never interviewed" : "control"}</span></p>`).join("")}</div></div>`;
  }

  function memory() {
    if (!S.memory && !S.stepStatus[3]) return running() ? waiting("The interview notes") : empty("The interview notes");
    const notes = S.data?.notes || [];
    return `<h1>The interview notes — eight participants</h1>
      <p class="lead">${esc(S.data?.study)}, notes kept by ${esc(S.data?.lead)}: one Markdown file per interview, uploaded once to the Library and imported into all three projects.</p>
      <div class="grid2">${notes.map((p) => `<div class="card ${p.withdrawn ? "warnc" : ""}"><div class="row spread"><h3 style="margin:0">${esc(p.id)} ${esc(p.name)}</h3>
          ${p.withdrawn ? `<span class="pill err">withdraws ${esc(p.withdrawn)}</span>` : ""}</div>
        <p class="small muted" style="margin:4px 0">${esc(p.org)} · <span class="mono">${esc(p.file)}</span></p>
        ${p.bullets.map((b) => `<p class="small" style="margin:4px 0">· ${esc(b)}</p>`).join("")}</div>`).join("")}</div>`;
  }

  function withdrawn() {
    const h = "Two participants withdraw consent";
    if (!S.withdrawn) return running() ? waiting(h) : empty(h);
    const w = S.withdrawn;
    return `<h1>${h} — their notes deleted everywhere</h1>
      <p class="lead">${w.people.map((p) => `<b>${esc(p.id)} ${esc(p.name)}</b> (${esc(p.org)}) withdrew on ${esc(p.withdrawn)}`).join("; ")}.
      In all three projects: <code>proj doc delete</code> (the indexed text and every memory derived from it go with it), then <code>lib delete</code> of the originals.
      Then <code>search --types document</code> in each project for their names and their own phrases.</p>
      <div class="card ${w.clean ? "hl" : "warnc"}"><table class="lint plain"><thead><tr><th>project</th><th>documents</th>${w.people.map((p) => `<th>${esc(p.name)}: words left in the index</th>`).join("")}</tr></thead><tbody>
        ${ARMS.map((a) => `<tr><td><b>${esc(armShort(a))}</b></td><td>${(w.docs?.[a] || []).length}</td>${w.people.map((p) => { const x = w.left?.[a]?.[p.id] || {};
          return `<td>${(x.phrases || []).length ? esc(x.phrases.join(", ")) : "0"}${(x.named || []).length ? ` · named in ${esc(x.named.join(", "))}` : ""}</td>`; }).join("")}</tr>`).join("")}</tbody></table>
        <p class="small" style="margin-bottom:0">${w.clean ? "The deletion is complete: none of their words, and no chunk naming them, in any project." : "Some of their text is still returned (see above)."}</p></div>`;
  }

  function left() {
    const h = "What is left in their place";
    if (!S.left) return running() ? waiting(h) : empty(h);
    return `<h1>${h}</h1>
      <p class="lead">Nothing they said is kept in any project. Two of the projects keep a record that the deletion happened: the consent register, or a notice under each deleted
      file's own name — MemoryRouter labels an entry with its file name, so the notice arrives labelled like the notes it replaces.</p>
      ${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(armShort(a))}</h3>${(S.left.arms?.[a] || []).length ? S.left.arms[a].map((f) =>
        `<p class="small mono" style="margin:6px 0 2px">${esc(f.file)}</p><pre class="mono small wrap">${esc(f.text)}</pre>`).join("") : '<p class="small muted">nothing — the notes are gone and so is any trace of them</p>'}</div>`).join("")}`;
  }

  function attached() {
    const h = "What MemoryRouter hands the model — notes deleted";
    if (!S.attached) return unavailable() || (running() ? waiting(h) : empty(h));
    const qs = Object.fromEntries((S.data?.questions || []).map((q) => [q.id, q]));
    const who = (e) => e.register ? "the consent register" : e.notice ? "withdrawal notice" : e.participant ? `${person(e.participant)?.name || e.participant}'s notes` : "";
    return `<h1>${h}</h1>
      <p class="lead">For each question about someone with no notes, ${esc(S.data?.check_model)} is asked through the notes-deleted boundary to copy out everything above the question.
      It is its own call, so what it is handed can differ from the answer calls. Labels as MemoryRouter writes them: <code>(file)</code>.</p>
      ${(S.attached.checks || []).map((c) => `<div class="card"><h3 style="margin-top:0">${esc(c.q)} <span class="small muted">${esc(qs[c.q]?.say)}</span></h3>
        ${(c.copies || []).map((x, k) => `<p class="small muted" style="margin:8px 0 2px">copy ${k + 1}</p>` + (x.readable ? (x.entries.length ? x.entries.map((e) => `<div class="idx ${e.participant ? "bad" : ""}"><span class="tag">${esc(e.file || "no file label")}</span> ${esc(who(e))}</div>`).join("") : '<p class="muted small">no entry</p>')
          : x.nothing ? '<p class="small">nothing attached (∅)</p>'
          : `<div class="idx ${x.named.length ? "bad" : ""}">wrote NONE${x.named.length ? `, then said the context holds ${esc(x.named.map((i) => (person(i)?.name || i) + "'s notes").join(", "))}` : ""}${x.said ? `: “${esc(x.said)}”` : ""}</div>`)).join("")}</div>`).join("")}`;
  }

  function arm(name) {
    const h = `Ask through MemoryRouter — ${armShort(name)}`;
    const rows = S.asked.filter((r) => r.arm === name);
    if (!rows.length) return unavailable() || (running() ? waiting(h) : empty(h));
    const t = S.tallies[name];
    const tag = { withdrawn: ["withdrew consent", "err"], unlisted: ["never interviewed", "err"], control: ["control", ""] };
    return `<h1>${h}${t ? ` — ${t.withdrawn.right}/${t.withdrawn.answers} right about the two who withdrew` : ""}</h1>
      <p class="lead">${esc(armLabel(name))}. Each question is answered ${S.data?.runs || 3}× by ${esc(S.data?.model)}.
      ✓ right · ⇄ another participant's words put on them · ✎ invented · ✗ “yes, you can quote them” · · other · ∅ nothing attached (the model on its own — still what the researcher reads).</p>
      ${rows.map((r) => {
        const pick = [...r.answers].sort((a, b) => ORDER.indexOf(a.verdict) - ORDER.indexOf(b.verdict))[0];
        const borrowed = pick && Object.keys(pick.phrases || {}).filter((id) => id !== (S.data?.questions || []).find((q) => q.id === r.q)?.about);
        return `<div class="card"><h3 style="margin-top:0">${esc(r.q)} <span class="small muted">${esc(r.say)}</span></h3>
          <div class="small">${esc(r.truth)}</div>
          <div class="row small" style="margin-top:4px"><span class="pill ${tag[r.group][1]}">${tag[r.group][0]}</span></div>
          <div class="row" style="margin-top:6px"><span>answers</span> ${r.answers.map(mark).join(" ")}</div>
          ${pick ? `<div class="chat"><div class="msg bot ${pick.verdict !== "right" ? "wrong" : ""}"><span class="who">answer ${mark(pick)}</span>${esc(pick.reply)}</div></div>` : ""}
          ${pick && pick.verdict === "borrowed" && borrowed.length ? `<p class="small">⇄ these words are from ${borrowed.map((id) => `<b>${esc(person(id)?.name || id)}</b>'s notes (“${esc((pick.phrases[id] || []).join("”, “"))}”)`).join(", ")}</p>` : ""}</div>`;
      }).join("")}
      ${t ? `<div class="card hl small">${armLine(t)}</div>` : ""}`;
  }

  function score() {
    const h = "Scoreboard";
    if (!S.summary) return running() ? waiting(h) : empty(h);
    const t = S.summary.tallies || {};
    return `<h1>${h}</h1>
      <div class="card"><table class="lint plain"><thead><tr><th>boundary</th><th>withdrew: right</th><th>someone else's words</th><th>invented</th><th>“yes, quote them”</th><th>∅</th><th>never interviewed: right</th><th>controls</th></tr></thead><tbody>
        ${ARMS.map((k) => { const w = t[k]?.withdrawn;
          return `<tr><td style="white-space:nowrap"><b>${esc(armShort(k))}</b></td>${w ? `<td>${w.right}/${w.answers}</td><td>${w.borrowed}</td><td>${w.invented}</td><td>${w.used}</td><td>${w.empty}</td><td>${t[k].unlisted.right}/${t[k].unlisted.answers}</td><td>${t[k].control.right}/${t[k].control.answers}</td>` : "<td>–</td><td></td><td></td><td></td><td></td><td></td><td></td>"}</tr>`; }).join("")}
      </tbody></table>
      <p class="muted small" style="margin-bottom:0">Every answer is graded, the ∅ ones too (MemoryRouter attached nothing, the model answered on its own): it is still what the researcher reads.
      The deletion itself was complete in all three projects${S.summary.clean ? " (search found none of their words)" : ""}.</p></div>
      ${(S.summary.instead || []).length ? `<div class="card"><h3 style="margin-top:0">What the assistant told the researcher instead</h3><pre class="mono small wrap">${esc(S.summary.instead.map((x) => x.trim()).join("\n"))}</pre></div>` : ""}
      <p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} prompt + ${fmt(S.summary.tokens.completion)} reply tokens, billed to your MemoryLake balance.
      Model replies vary from run to run; the README reports totals over all runs.</p>`;
  }

  function lookupTool() {
    const ready = !!S.world;
    const all = [...(S.data?.notes || []).map((p) => p.name), S.data?.unlisted?.name].filter(Boolean);
    const last = S.lookups[S.lookups.length - 1];
    return `<h1>Who</h1>
      <p class="lead">One name: what the study holds for it, and what each project's index returns when you search for it. Read-only: searches, no model call.</p>
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:6px">${all.map((a) => `<button class="chip ${S.pick === a ? "on" : ""}" data-pick="${esc(a)}">${esc(a)}</button>`).join(" ")}</div>
        <div class="row" style="margin-top:10px"><button class="btn primary" data-act="lookup" ${ready && !running() ? "" : "disabled"}>Look up</button>${ready ? "" : '<span class="muted small">Run the demo first.</span>'}</div></div>
      ${last ? `<div class="card warnc"><h3 style="margin-top:0">${esc(last.name)}</h3><p class="small">In the study: ${last.participant ? `${esc(last.participant.id)}, ${esc(last.participant.org)}; ${last.participant.withdrawn ? `withdrew consent ${esc(last.participant.withdrawn)}, notes deleted ${esc(last.participant.deleted)}` : "consented to quotes"}` : esc(S.data?.unlisted?.why)}</p>
        ${(last.views || []).map((v) => `<div class="idx"><span class="tag">${esc(armShort(v.arm))}</span>top hits: ${esc(v.files.slice(0, 4).join(", ") || "none")} · chunks naming ${esc(last.name)}: <b>${v.named.length ? esc(v.named.map((n) => n.file).join(", ")) : "0"}</b></div>`).join("")}</div>` : ""}`;
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
    st.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => { S.pick = b.dataset.pick; renderStage(); }));
    st.querySelector('[data-act="lookup"]')?.addEventListener("click", async (e) => {
      e.target.disabled = true; e.target.textContent = "Looking up…";
      const r = await api("/api/who", { name: S.pick });
      if (r) S.lookups.push(r);
      if (S.view === "lookup") renderStage();
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
  function resetView() { S.memory = null; S.withdrawn = null; S.left = null; S.attached = null; S.asked = []; S.tallies = {}; S.summary = null; S.unavailable = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's boundaries, the three projects and its Library files?")) return;
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
    else if (k === "withdrawn") S.withdrawn = d;
    else if (k === "left") S.left = d;
    else if (k === "attached") S.attached = d;
    else if (k === "asked") S.asked.push(d);
    else if (k === "tally") S.tallies[d.arm] = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the boundaries, the three projects and the Library files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try Who: one name in the study and in the three projects' index.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "withdrawn", "left", "attached", "asked", "tally", "summary", "router_unavailable", "lookup", "done", "error"];
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

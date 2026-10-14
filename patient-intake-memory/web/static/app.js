/* Patient intake memory for small clinics — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "a registry, one chart per patient", view: "setup" },
    { n: 3, title: "Intake", sub: "registrations and chart records", view: "intake" },
    { n: 4, title: "Check-in read", sub: "today's chart, then her names", view: "checkin" },
    { n: 5, title: "Identity read", sub: "who is this patient?", view: "identity" },
    { n: 6, title: "Merge", sub: "one chart, then ask again", view: "merge" },
  ];
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, step: 0,
    stepStatus: {}, setup: null, intake: null, checkin: null, identity: null, merge: null,
    ask: { q: "", topic: "draw", html: "" }, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const story = () => S.data?.story;
  const srcName = (t) => story()?.sources?.[t] || t;
  const topicName = (t) => story()?.topics?.[t] || t;
  const pname = (p) => story()?.patients?.[p]?.name || p;
  const chart = (p) => `${pname(p)} (${p})`;
  const ci = () => story()?.checkin || "P-1088";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const okPill = (a, yes = "✓", no = "✗") => a ? `<span class="pill ok">${yes}</span>` : `<span class="pill err">${no}</span>`;
  const yn = (b) => b ? '<span class="pill ok">✓</span>' : '<span class="faint">·</span>';
  function hitTable(hits, used) {
    return `<table class="iso"><tr><th>rank</th><th>id</th><th>record</th></tr>
      ${hits.map((h) => `<tr class="${h.record === used ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">#${esc(h.rank)}</td><td class="mono small" style="white-space:nowrap">${esc(h.record)}</td>
        <td class="small">${esc(h.body)}<br><span class="pill">${esc(srcName(h.source))}</span> <span class="pill">${esc(topicName(h.topic))}</span> ${h.record === used ? '<span class="pill accent">used</span>' : ""}</td></tr>`).join("")}</table>`;
  }
  function recTable(rows, opts = {}) {
    return `<table class="iso"><tr><th>chart</th><th>on</th><th>says</th>${opts.extra ? "<th></th>" : ""}</tr>
      ${rows.map((x) => `<tr class="${opts.hl && opts.hl(x) ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(x.patient)}<br><span class="faint">${esc(pname(x.patient))}</span></td>
        <td class="mono small" style="white-space:nowrap">${esc(x.on)}</td><td class="small"><b>${esc(x.value)}</b><br><span class="faint">${esc(x.body)}</span></td>${opts.extra ? `<td>${opts.extra(x)}</td>` : ""}</tr>`).join("")}</table>`;
  }
  function idTable(idr) {
    return `<p class="small">${esc(chart(idr.patient))}'s registration: born <b class="mono">${esc(idr.ids.dob)}</b> · mobile <b class="mono">${esc(idr.ids.phone)}</b> · email <b class="mono">${esc(idr.ids.email)}</b></p>
      <table class="iso"><tr><th>chart</th><th>born</th><th>mobile</th><th>email</th><th>name</th><th>verdict</th></tr>
      ${idr.rows.map((r) => `<tr class="${r.same ? "hl" : ""}"><td class="mono small" style="white-space:nowrap">${esc(r.record)}<br><span class="faint">${esc(r.name)}</span></td>
        <td>${yn(r.dob)}</td><td>${yn(r.phone)}</td><td>${yn(r.email)}</td><td>${yn(r.by_name)}</td>
        <td class="small">${r.same ? '<span class="pill ok">same person</span>' : '<span class="pill err">someone else</span>'}<br><span class="faint">${esc(r.why)}</span></td></tr>`).join("")}</table>`;
  }

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Ask<small>any question · who is a patient</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, intake, checkin, identity, merge, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Records</dt><dd>${st.ready ? "from an earlier run — Run again replays the morning of the visit on them, Reset &amp; run starts over" : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.ready ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about two minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>One patient, two names. Today's chart says “allergies: none”.</h1>
      <p class="lead">Cedar Row Family Clinic keeps one MemoryLake memory per patient chart and a front desk registry. On October 15 Lena Okoro checks in for a physical with fasting labs, and the front desk registers her as a new patient.
      She is not new: in 2024 and 2025 she came as Lena Marsh, and that chart says she faints when seated for a blood draw, reacts to latex gloves, and works nights.
      The check-in read sees only today's chart; reading by name pulls in another Lena Marsh. The identity read matches her date of birth and mobile, and the duplicate chart is merged back.
      This page drives the <code>memorylake</code> CLI for real. The clinic and patients are fictional.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. <b>Clean up</b> deletes the registry, every chart and every record on them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/patient-intake-memory-for-small-clinics" target="_blank" rel="noopener">memorylake.ai › Patient intake memory for small clinics</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const su = S.setup; if (!su) return empty("A registry and one chart per patient");
    return `<h1>A registry and one chart per patient</h1>
      <p class="lead">“Intake memory with clinic-specific patterns.” The registry is a project — one fact per registration. Each chart is an actor, so a question can be scoped to one patient's chart with <code>search --actors</code>.</p>
      <div class="card"><table class="iso"><tr><th></th><th>name</th><th>id</th></tr>
        <tr><td><span class="pill">project</span></td><td><b>${esc(su.name)}</b></td><td class="mono small faint">${esc(su.project)}</td></tr>
        ${Object.entries(su.charts).map(([p, a]) => `<tr><td><span class="pill">chart</span></td><td><b>${esc(chart(p))}</b></td><td class="mono small faint">${esc(a)}</td></tr>`).join("")}</table></div>`;
  }

  function intake() {
    const x = S.intake; if (!x) return empty("Intake");
    const reg = [...x.registry].sort((a, b) => a.registered.localeCompare(b.registered));
    const today = x.charts[ci()] || [];
    return `<h1>Intake — ${reg.length} registrations, ${Object.values(x.charts).reduce((n, c) => n + c.length, 0)} chart records</h1>
      <p class="lead">Every registration and every chart record pinned word for word (<code>fact add</code>), then <code>fact update --metadata</code> with its fields.
      ${x.restored ? ` <span class="faint">(${x.restored} change(s) an earlier run made by merging were put back, so this run starts from the same morning.)</span>` : ""}</p>
      <div class="card"><h2 style="margin-top:0">The registry</h2><table class="iso"><tr><th>chart</th><th>name</th><th>born</th><th>mobile</th><th>email</th><th>registered</th></tr>
        ${reg.map((r) => `<tr class="${r.record === ci() ? "hl" : ""}"><td class="mono small">${esc(r.record)}</td><td class="small"><b>${esc(r.name)}</b></td><td class="mono small">${esc(r.dob)}</td><td class="mono small">${esc(r.phone)}</td><td class="mono small">${esc(r.email)}</td><td class="mono small">${esc(r.registered)}</td></tr>`).join("")}</table></div>
      <div class="card mine"><h2 style="margin-top:0">Today: ${esc(chart(ci()))} checks in</h2><p class="small muted">An annual physical with fasting labs. Her chart, as the front desk made it this morning:</p>
        ${recTable(today)}</div>
      <div class="grid">${Object.entries(x.charts).filter(([p]) => p !== ci()).map(([p, rs]) => `<div class="card"><h2 style="margin-top:0">${esc(chart(p))}</h2><table class="iso">
        ${rs.map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.on)}</td><td class="small"><b>${esc(r.value)}</b> <span class="faint">· ${esc(topicName(r.topic))}</span></td></tr>`).join("")}</table></div>`).join("")}</div>`;
  }

  function checkin() {
    const x = S.checkin; if (!x) return empty("The check-in read");
    const names = Object.entries(x.names);
    return `<h1>The check-in read — today's chart has nothing about her</h1>
      <p class="lead">What “intake memory that loads automatically” does at check-in: <code>search</code> the question in the chart she was checked in under (${esc(ci())}) and take the first hit on that topic.</p>
      ${x.reads.map((q) => `<div class="card"><div class="row spread"><h2 style="margin:0">${esc(q.q.id)} · ${esc(q.q.title)}</h2><span class="pill ${q.used ? "accent" : "err"}">→ ${esc(q.used ? (q.hits.find((h) => h.record === q.used)?.value || q.used) : "nothing on file")}</span></div>
        <p class="small">“${esc(q.q.text)}”</p>${hitTable(q.hits, q.used)}</div>`).join("")}
      <div class="card mine"><h2 style="margin-top:0">The obvious repair: read every chart under her names</h2>
        <p class="small">Her form gives a previous surname. The registry (<code>fact list --query</code>, an exact substring) finds: ${names.map(([n, ps]) => `“${esc(n)}” → <span class="mono">${esc(ps.join(", "))}</span>`).join(" · ")}</p>
        ${x.named.map((n) => `<h3>${esc(n.q.id)} · ${esc(topicName(n.q.topic))} — ${n.rows.length} record(s) from <b>${n.people.length} different patients</b></h3>${recTable(n.rows)}`).join("")}</div>`;
  }

  function identity() {
    const x = S.identity; if (!x) return empty("The identity read");
    const bad = x.rows.filter((r) => !r.ok).length;
    return `<h1>The identity read — the check-in read missed her own records on ${bad} of ${x.rows.length}</h1>
      <p class="lead">Each identifier on today's registration looked up in the registry (<code>fact list --query</code>, every page). Rule: <b>${esc(story()?.rule || "")}</b>. Each identifier alone matches someone else.</p>
      <div class="card">${idTable(x.identity)}</div>
      <div class="card"><table class="iso"><tr><th>item</th><th>the check-in read (today's chart)</th><th>her records</th><th></th></tr>
        ${x.rows.map((r) => `<tr class="${r.ok ? "" : "hl"}"><td class="small"><span class="mono">${esc(r.q.id)}</span> ${esc(r.q.title)}</td>
          <td class="small">${esc(r.used ? r.used_value : "nothing on file")}</td><td class="small">${r.records.map((v) => `<b>${esc(v.value)}</b> <span class="faint">${esc(v.patient)} ${esc(v.on)}</span>`).join("<br>")}</td>
          <td>${okPill(r.ok, "✓", `✗ missed ${r.missed.length}`)}</td></tr>`).join("")}</table></div>`;
  }

  function merge() {
    const x = S.merge; if (!x) return empty("Merge the charts");
    if (!x.survivor) return `<h1>Merge the charts</h1><div class="empty">No other registration met the rule; nothing was merged.</div>`;
    return `<h1>Merge the charts — ${x.agree} of ${x.after.length} answered from her own records</h1>
      <p class="lead">The duplicate chart ${esc(ci())} goes into the original ${esc(x.survivor)}: its records are added word for word (<code>fact add --actor</code>, <code>merged_from</code> in metadata), the chart is renamed, the registry entry gets the new name, and ${esc(ci())} is deleted — its facts first.
      Later the same day the <b>same check-in read</b> runs on the merged chart, in new words.</p>
      <div class="card mine"><h2 style="margin-top:0">${esc(x.name)}</h2><p class="small faint">${x.deleted} fact(s) deleted from ${esc(ci())}, then the actor</p><table class="iso">
        ${x.chart.sort((a, b) => (a.on + a.record).localeCompare(b.on + b.record)).map((r) => `<tr><td class="mono small" style="white-space:nowrap">${esc(r.on)}</td><td class="small"><b>${esc(r.value)}</b> <span class="faint">· ${esc(topicName(r.topic))}</span>${r.merged_from ? ` <span class="pill accent">from ${esc(r.merged_from)}</span>` : ""}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Later questions, check-in read on the merged chart</h2><table class="iso"><tr><th>item</th><th>on-topic hits</th><th></th></tr>
        ${x.after.map((a) => `<tr><td class="small"><span class="mono">${esc(a.q.id)}</span> “${esc(a.q.text)}”<br><span class="faint">${a.found} of her ${a.want} record(s) in the top ${a.hits}${a.tries > 1 ? ` · try ${a.tries}` : ""}</span></td>
          <td class="small">${a.on_topic.map((h) => `#${esc(h.rank)} <b>${esc(h.value)}</b> <span class="faint">${esc(h.on)}</span>`).join("<br>")}</td><td>${okPill(a.ok)}</td></tr>`).join("")}</table></div>
      <div class="card"><h2 style="margin-top:0">Everyone else's chart</h2><p class="small">${Object.entries(x.untouched).map(([p, [b, a]]) => `${esc(chart(p))}: ${b} → ${a} ${b === a ? '<span class="pill ok">unchanged</span>' : '<span class="pill err">changed</span>'}`).join("<br>")}</p></div>`;
  }

  function ask() {
    const ready = !!S.st.ready, a = S.ask, topics = Object.keys(story()?.topics || {});
    return `<h1>Ask</h1>
      <p class="lead">Ask anything before her visit: the check-in read (today's chart — or, once merged, what it held this morning), the name read, the identity read, and the check-in read on the merged chart. Or check which registrations are one patient.</p>
      <div class="card"><form class="ask-form" id="ask-form"><input id="ask-q" placeholder="e.g. Does she need nitrile gloves?" value="${esc(a.q)}" ${ready ? "" : "disabled"}>
          <select id="ask-topic" ${ready ? "" : "disabled"} style="max-width:200px">${topics.map((t) => `<option value="${t}" ${t === a.topic ? "selected" : ""}>${esc(topicName(t))}</option>`).join("")}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Ask</button></form>
        <div class="chips">${(story()?.ask_examples || []).map((e) => `<button type="button" class="chip" data-q="${esc(e.q)}" data-topic="${esc(e.topic)}">${esc(e.q)}</button>`).join("")}</div>
        <div class="chips">${Object.keys(story()?.patients || {}).map((p) => `<button type="button" class="chip" data-who="${p}">who is ${esc(p)}?</button>`).join("")}</div>
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
      form.addEventListener("submit", (e) => { e.preventDefault(); askQ($("#ask-q").value.trim(), $("#ask-topic").value); });
      st.querySelectorAll(".chips .chip[data-q]").forEach((c) => c.addEventListener("click", () => {
        $("#ask-q").value = c.dataset.q; $("#ask-topic").value = c.dataset.topic; askQ(c.dataset.q, c.dataset.topic); }));
      st.querySelectorAll(".chips .chip[data-who]").forEach((c) => c.addEventListener("click", () => askPatient(c.dataset.who)));
    }
  }
  function putResult(box, html) { S.ask.html = html; if (document.body.contains(box)) box.innerHTML = html; else if (S.view === "ask") renderStage(); }
  async function askQ(q, topic) {
    if (!q) return; S.ask.q = q; S.ask.topic = topic;
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> reading the charts…</span>`;
    const r = await api("/api/ask", { q, topic });
    if (!r) { box.innerHTML = ""; return; }
    const t = topicName(r.q.topic);
    const c = r.checkin, w = r.was, m = r.merged;
    const first = c ? (c.used ? c.hits.find((h) => h.record === c.used)?.value : "nothing on file") : w ? (w.records[0]?.value || "nothing on file") : "—";
    putResult(box, `<div class="row spread"><h2>“${esc(r.q.text)}” · ${esc(t)}</h2></div>
      <div class="cols"><div><h3>Check-in read · ${esc(ci())}${!c && w ? ` <span class="faint">(as it was this morning; merged into ${esc(w.survivor)})</span>` : ""}</h3><p class="small"><b>${esc(first)}</b></p>
        <h3>Name read · ${r.named.pids.length} charts, ${r.named.people.length} patients</h3>${recTable(r.named.rows)}</div>
        <div><h3>Her own records <span class="faint">(same person: ${esc(r.identity.same.join(", ") || "none")})</span></h3>${recTable(r.mine)}
        ${m ? `<h3>Check-in read on the merged chart ${esc(m.survivor)}</h3><p class="small">${m.on_topic.map((h) => `#${esc(h.rank)} <b>${esc(h.value)}</b> <span class="faint">${esc(h.on)}</span>`).join("<br>") || "no record on this topic"}</p>` : `<p class="small faint">The charts are not merged yet — a full run merges them in step 6.</p>`}</div></div>
      <details><summary class="small">identity read</summary>${idTable(r.identity)}</details>`);
  }
  async function askPatient(pid) {
    const box = $("#ask-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> looking up identifiers…</span>`;
    const r = await api("/api/patient", { patient: pid });
    if (!r) { box.innerHTML = ""; return; }
    putResult(box, `<div class="row spread"><h2>Which registrations are ${esc(pid)}?</h2><span class="pill">${r.actor ? `${esc(r.name)} · ${r.records} records` : r.merged_into ? `merged into ${esc(r.merged_into)}` : "no chart"}</span></div>
      ${idTable(r.identity)}<p class="small">Same person: <b>${esc(r.identity.same.join(", ") || "nobody else")}</b>${r.aliases.length ? ` · also registered as ${esc(r.aliases.join(", "))}` : ""}</p>`);
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
  function resetView() { S.setup = null; S.intake = null; S.checkin = null; S.identity = null; S.merge = null; S.ask.html = ""; }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's registry, every patient chart and every record on them from your account?")) return;
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
    else if (k === "checkin") S.checkin = d;
    else if (k === "identity") S.identity = d;
    else if (k === "merge") S.merge = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ready = false; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the registry, every chart and every record on them are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try a question of your own — “Ask” in the sidebar.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "setup", "intake", "checkin", "identity", "merge",
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
    show(state.status === "running" ? S.view : S.merge ? "merge" : "connect");
    subscribe();
  })();
})();

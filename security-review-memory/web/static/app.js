/* Answering an enterprise security review with live evidence — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Q1 Credentials", sub: "api-key create · shown once", view: "keys" },
    { n: 3, title: "Tenant memory", sub: "written with the ingest key", view: "tenant" },
    { n: 4, title: "Q2 Rotation", sub: "api-key rotate", view: "rotate" },
    { n: 5, title: "Q3 Least privilege", sub: "what one key reaches", view: "scope" },
    { n: 6, title: "Q4 Revocation", sub: "api-key revoke · 409 on itself", view: "revoke" },
    { n: 7, title: "Q5 Temporary access", sub: "--expires-at", view: "expire" },
    { n: 8, title: "Q6 Audit trail", sub: "fact trace", view: "audit" },
    { n: 9, title: "Q7 Offboarding", sub: "proj delete → 404", view: "offboard" },
  ];
  const QVIEW = { Q1: "keys", Q2: "rotate", Q3: "scope", Q4: "revoke", Q5: "expire", Q6: "audit", Q7: "offboard" };
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    keys: null, tenant: null, rotate: null, scope: null, revoke: null, expire: null, left: null, audit: null, offboard: null,
    answers: {}, pack: null, termCount: 0, lastEvent: 0, caughtUp: false,
  };
  const review = () => S.data?.review;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "pack" ? "active" : ""}" data-view="pack"><span class="n">≡</span><span class="t">Evidence pack<small>all seven answers</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, keys, tenant, rotate, scope, revoke, expire, audit, offboard, pack }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;
  const mark = (ok) => `<span class="pill ${ok ? "ok" : "err"}">${ok ? "✓" : "✗"}</span>`;
  const VERDICT = { yes: ["ok", "✓ Yes"], no: ["err", "✗ No"], partial: ["warn", "◐ Partly"] };
  const verdictPill = (v) => `<span class="pill ${VERDICT[v]?.[0] || ""}">${VERDICT[v]?.[1] || esc(v)}</span>`;
  const when = (ts) => ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 19) + "Z" : "—";

  function answerCard(qid) {
    const a = S.answers[qid];
    const q = review()?.questions.find((x) => x.id === qid);
    if (!a) return q ? `<div class="card qcard"><div class="row spread"><b>${esc(q.id)} · ${esc(q.topic)}</b><span class="pill busy"><span class="spinner"></span> checking</span></div><div class="q">“${esc(q.text)}”</div></div>` : "";
    return `<div class="card qcard ${esc(a.verdict)}"><div class="row spread"><b>${esc(a.id)} · ${esc(a.topic)}</b>${verdictPill(a.verdict)}</div>
      <div class="q">“${esc(a.question)}”</div><div>${code(a.answer)}</div>
      ${a.note ? `<div class="note"><b>Note</b> ${code(a.note)}</div>` : ""}
      <ul class="evl">${a.evidence.map((e) => `<li><span class="m ${e.ok === null || e.ok === undefined ? "na" : e.ok ? "ok" : "bad"}">${e.ok === null || e.ok === undefined ? "·" : e.ok ? "✓" : "✗"}</span>
        <span><code>${esc(e.cmd)}</code> → ${esc(e.result)}</span></li>`).join("")}</ul></div>`;
  }

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Last pack</dt><dd>${st.has_pack ? '<span class="muted">out/security-review-halden.md</span>' : '<span class="muted">none yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${st.has_pack ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 3 minutes (2½ of them waiting for a key to expire) · keys it creates are named <code>${esc(S.data?.prefix || "")}-…</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    const qs = review()?.questions || [];
    return `<h1>Answer an enterprise security review about your agent's memory — with checks, not claims.</h1>
      <p class="lead"><b>Quillstack</b> sells an agent that drafts procurement memos and keeps each customer's memory in MemoryLake. <b>Halden Mutual</b>, a bank,
      sent seven questions. This page answers each one by running the check against <b>your own MemoryLake team</b>: it issues three integration keys,
      writes Halden's memory with one of them, rotates it, checks what it can reach, revokes another, lets the third expire, reads the audit trail and
      deletes Halden's memory. Every answer — the no's included — goes into an evidence pack. This page drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">The questionnaire</h2><ol class="steps-list">${qs.map((q) => `<li><b>${esc(q.topic)}.</b> ${esc(q.text)}</li>`).join("")}</ol>
          <p class="muted small" style="margin-bottom:0">The demo only creates, rotates and revokes keys named <code>${esc(S.data?.prefix || "")}-…</code>, never the key you connect with, and shows keys as <code>sk-</code> + 4 characters.
          Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-compliance-for-agent-saas-selling-to-enterprise" target="_blank" rel="noopener">memorylake.ai › memory compliance for agent SaaS</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function keys() {
    const k = S.keys; if (!k) return empty("Q1 Credentials");
    const ks = Object.values(k.keys);
    return `<h1>One key per integration — the secret is shown once</h1>
      <p class="lead"><code>api-key create --name</code> for each integration. The secret is in that reply only; the demo logs each one into its own isolated CLI profile
      and never keeps it anywhere else. A retry with the same <code>--idempotency-key</code> returns the same key without the secret, and <code>api-key list</code> shows prefixes.</p>
      <div class="grid">${ks.map((x) => `<div class="card"><div class="row spread"><b>${esc(x.name)}</b><span class="pill ${x.fate === "rotated" ? "accent" : x.fate === "revoked" ? "err" : "warn"}">${esc(x.fate)}</span></div>
        <div class="muted small">${esc(x.purpose)}</div>
        <dl class="kv" style="margin-top:8px"><dt>id</dt><dd class="mono">${esc(x.id)}</dd><dt>secret</dt><dd><span class="secret">${esc(x.shown)}</span> <span class="muted small">${esc(x.length)} chars, shown once</span></dd>
          <dt>prefix</dt><dd class="mono">${esc(x.prefix)}</dd>${x.expires_at ? `<dt>expires</dt><dd class="mono">${esc(when(x.expires_at))}</dd>` : ""}</dl></div>`).join("")}</div>
      <div class="card"><div class="row spread"><b>Retry with the same idempotency key</b>${mark(k.replay.same && !k.replay.secret)}</div>
        <div class="small">id ${esc(k.replay.id)} (${k.replay.same ? "the same key" : "a new key"}) · ${k.replay.secret ? "the secret was printed again" : "the secret field is empty"}</div></div>
      <div class="card"><b>api-key list</b><table class="tour"><thead><tr><th>id</th><th>name</th><th>prefix</th><th>status</th><th>expires_at</th></tr></thead><tbody>
        ${k.listed.map((x) => `<tr><td class="mono">${esc(x.id)}</td><td>${esc(x.name)}</td><td class="mono">${esc(x.key_prefix)}</td><td>${esc(x.status)}</td><td class="mono small">${esc(when(x.expires_at))}</td></tr>`).join("")}</tbody></table></div>
      ${answerCard("Q1")}`;
  }

  function tenant() {
    const t = S.tenant; if (!t) return empty("Tenant memory");
    return `<h1>Halden Mutual's memory, written through the ingest worker's key</h1>
      <p class="lead">Everything here ran as <code>${esc(PREFIX())}-ingest-worker</code>: the project, Ines and Quill, a five-message setup call (read into memory by MemoryLake),
      and two pinned settings stamped with <code>metadata.written_by</code>. A second customer, Orrin Freight, has its own project — written with your key.</p>
      <div class="card"><div class="row spread"><b>Halden Mutual — tenant memory</b><span class="mono small faint">${esc(t.ids.halden)}</span></div>
        ${t.facts.map((f) => `<div class="memfact small"><span>${f.pinned ? '<span class="pill accent">pinned</span>' : '<span class="pill">extracted</span>'} ${esc(f.text)}</span></div>`).join("")}</div>
      <div class="card"><div class="row spread"><b>${esc(t.orrin.name)}</b><span class="mono small faint">${esc(t.orrin.id)}</span></div>
        <div class="muted small">another customer of Quillstack, on the same MemoryLake team</div></div>`;
  }
  const PREFIX = () => S.data?.prefix || "mlu-sec";

  function refusal(r) {
    if (!r) return "";
    return r.refused ? `<div class="refusal">HTTP ${esc(r.status)} · ${esc(r.message)}</div><div class="muted small">refused ${esc(r.after_s)} s after the change</div>`
      : `<div class="refusal">still accepted after ${esc(r.after_s)} s</div>`;
  }

  function rotate() {
    const r = S.rotate; if (!r) return empty("Q2 Rotation");
    return `<h1>Rotate: new secret, same key, same memory</h1>
      <p class="lead"><code>api-key rotate ${esc(r.id)}</code> keeps the key id and mints a new secret. The old secret is refused on the next call; the new one reads the same facts,
      because memory belongs to the team, not to the key.</p>
      <div class="grid">
        <div class="card"><b>Old secret</b> <span class="mono muted">${esc(r.old_prefix)}</span>${refusal(r.refused)}</div>
        <div class="card"><b>New secret</b> <span class="secret">${esc(r.shown)}</span> <span class="mono muted">${esc(r.new_prefix)}</span>
          <div class="row" style="margin-top:8px"><span class="big">${esc(r.before)} → ${esc(r.after)}</span><span class="muted small">facts before / after</span>${mark(r.same)}</div>
          <div class="muted small">${r.same ? "the same fact ids" : "different fact ids"}</div></div></div>
      ${answerCard("Q2")}`;
  }

  function scope() {
    const s = S.scope; if (!s) return empty("Q3 Least privilege");
    return `<h1>What one integration key can reach</h1>
      <p class="lead">The ingest worker only needs Halden's project. Asked with its own key:</p>
      <div class="grid">
        <div class="card"><b>team get</b><div class="big" style="margin-top:6px">${esc(s.role)}</div><div class="muted small">caller_role of the ingest key</div></div>
        <div class="card"><b>Orrin Freight's memory</b>${s.read_orrin.length ? s.read_orrin.map((f) => `<div class="memfact small"><span>${esc(f)}</span></div>`).join("") + '<div class="muted small">readable with the ingest key</div>' : '<div class="muted small">refused</div>'}</div>
        <div class="card"><b>api-key list</b><div class="big" style="margin-top:6px">${s.keys_listed ?? "refused"}</div><div class="muted small">${s.keys_listed !== null ? "keys of the team, listed with the ingest key" : ""}</div></div></div>
      ${answerCard("Q3")}`;
  }

  function revoke() {
    const r = S.revoke; if (!r) return empty("Q4 Revocation");
    return `<h1>Retire the support console's key</h1>
      <p class="lead">It still works, and writes one last note. It cannot revoke itself; the owner revokes it. Then its secret is refused, the key is gone — and the note stays.</p>
      <div class="grid">
        <div class="card"><b>Revoke itself</b><div class="refusal">HTTP ${esc(r.self_revoke.status)} · ${esc(r.self_revoke.message)}</div></div>
        <div class="card"><b>After the owner revokes it</b>${refusal(r.refused)}<div class="small" style="margin-top:6px"><code>api-key get ${esc(r.id)}</code> → HTTP ${esc(r.get.status)} ${esc(r.get.message)}</div></div>
        <div class="card"><b>What it wrote</b><div class="row" style="margin-top:6px">${mark(r.note_kept)}<span class="small">${r.note_kept ? "still in Halden's memory" : "gone"}</span></div><div class="mono small faint">${esc(r.note_id)}</div></div></div>
      ${answerCard("Q4")}`;
  }

  function expire() {
    const e = S.expire;
    const k = S.keys?.keys?.["contractor-export"];
    if (!e && !k) return empty("Q5 Temporary access");
    const waiting = !e && S.left !== null;
    return `<h1>The contractor's key expires on its own</h1>
      <p class="lead">Created with <code>--expires-at</code>. Nobody revokes it: the demo waits for the time and calls again.</p>
      <div class="grid">
        <div class="card"><b>expires_at</b><div class="big" style="margin-top:6px">${esc(when(k?.expires_at || e?.expires_at))}</div>
          ${waiting ? `<span class="pill busy"><span class="spinner"></span> ${esc(S.left)} s to go</span>` : ""}</div>
        ${e ? `<div class="card"><b>After expiry</b>${refusal(e.refused)}</div>
        <div class="card"><b>api-key get</b><div class="big" style="margin-top:6px">${esc(e.status_after)}</div><div class="muted small">the status field does not change at expiry</div></div>` : ""}</div>
      ${answerCard("Q5")}`;
  }

  function audit() {
    const a = S.audit; if (!a) return empty("Q6 Audit trail");
    return `<h1>What changed, when, from where — and by which key?</h1>
      <p class="lead">Halden signed amendment 1 (60 days, not 90); the owner updated the setting in place. <code>fact trace</code> shows every version. An extracted fact points at the messages it was read from.</p>
      <div class="card"><b>fact trace ${esc(a.fact_id)}</b><table class="tour"><thead><tr><th>event</th><th>source</th><th>server time</th><th>text</th></tr></thead><tbody>
        ${a.trace.map((r) => `<tr><td>${esc(r.event)}</td><td>${esc(r.source)}</td><td class="mono small">${esc(String(r.ts || "").slice(0, 19).replace("T", " "))}</td><td class="small">${esc(r.text)}</td></tr>`).join("")}</tbody></table>
        <div class="small muted" style="margin-top:6px">entry fields: <span class="mono">${esc(a.fields.join(", "))}</span> — ${a.who_fields.length ? "credential: " + esc(a.who_fields.join(", ")) : "none names the key"} ·
          <code>metadata.written_by</code> = ${esc(a.written_by)} (self-reported)</div></div>
      ${a.cook ? `<div class="card"><b>Extracted:</b> ${esc(a.cook.text)}<div class="small muted">${esc(a.cook.event)} · COOK · read from ${a.cook.sources.length} message(s)</div>
        ${a.cook.sources.map((s) => `<div class="memfact small"><span class="ev">#${esc(s.seq)} ${esc(String(s.at || "").slice(0, 16).replace("T", " "))}</span><span>${esc(s.text)}</span></div>`).join("")}</div>` : ""}
      ${answerCard("Q6")}`;
  }

  function offboard() {
    const o = S.offboard; if (!o) return empty("Q7 Offboarding");
    return `<h1>Halden leaves: delete its memory and show it is gone</h1>
      <p class="lead">The conversation, Ines and Quill, and the project (${esc(o.facts_before)} facts) are deleted; then the keys that served Halden are revoked.</p>
      <div class="grid">
        <div class="card"><b>fact list --projects</b><div class="refusal">HTTP ${esc(o.fact_list.status)} · ${esc(o.fact_list.message)}</div></div>
        <div class="card"><b>search --projects</b><div class="refusal">HTTP ${esc(o.search.status)} · ${esc(o.search.message)}</div></div>
        <div class="card"><b>demo keys left</b><div class="big" style="margin-top:6px">${esc(o.keys_left)}</div></div></div>
      ${answerCard("Q7")}`;
  }

  function pack() {
    const p = S.pack;
    if (!p) return `<h1>Evidence pack</h1><div class="empty">${S.st.has_pack ? '<button class="btn" data-act="loadpack">Load the last pack</button>' : "Run the demo first."}</div>`;
    const c = p.counts;
    return `<h1>${esc(p.vendor)} — security review answers for ${esc(p.buyer)}</h1>
      <p class="lead">Generated ${esc(p.generated_at)} against <span class="mono">${esc(p.base_url)}</span>, team “${esc(p.team)}”. The same pack is in
      <code>out/security-review-halden.md</code> (<a href="/api/pack.md" target="_blank" rel="noopener">open</a>) and <code>.json</code>.</p>
      <div class="tally"><span class="pill ok">${c.yes} yes</span><span class="pill warn">${c.partial} partly</span><span class="pill err">${c.no} no</span></div>
      ${p.answers.map((a) => { S.answers[a.id] = a; return answerCard(a.id); }).join("")}`;
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
    st.querySelector('[data-act="loadpack"]')?.addEventListener("click", loadPack);
  }
  async function loadPack() {
    try { const r = await fetch("/api/pack"); const j = await r.json(); if (!r.ok || j.error) { banner(j.error || `HTTP ${r.status}`); return; } S.pack = j; if (S.view === "pack") renderStage(); } catch (e) { banner(String(e)); }
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
  function resetView() {
    Object.assign(S, { keys: null, tenant: null, rotate: null, scope: null, revoke: null, expire: null, left: null, audit: null, offboard: null, answers: {}, pack: null });
  }
  async function runDemo(reset) {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm(`Delete the demo's projects, conversation and people, and revoke every key named ${PREFIX()}-… on your team?`)) return;
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
    let rerender = true;
    if (k === "step") {
      finishRunning("done"); if (d.index) S.stepStatus[d.index] = "running"; if (d.index === 2) resetView();
      term("step", ev.text); if (S.follow && stepOf(d.index)) show(stepOf(d.index).view);
    }
    else if (k === "cmd") { term("cmd", ev.text); rerender = false; }
    else if (k === "note") { term("note", ev.text); rerender = false; }
    else if (k === "progress") { term("progress", ev.text.trim()); S.left = d.left ?? null; rerender = S.view === "expire"; }
    else if (k === "json") { term("dim", ev.text); rerender = false; }
    else if (k === "text") rerender = false;
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "review") { /* the questionnaire is in /api/data */ }
    else if (k === "answer") S.answers[d.id] = d;
    else if (["keys", "tenant", "rotate", "scope", "revoke", "expire", "audit", "offboard"].includes(k)) S[k] = d;
    else if (k === "pack") S.pack = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner(`Cleanup finished — the demo's projects, people and every ${PREFIX()}-… key are gone from your account.`, true); }
      else if (S.caughtUp) { banner("Done. Open “Evidence pack” for all seven answers and their evidence.", true); if (S.follow) S.view = "pack"; }
      if (S.caughtUp) refreshState();
    } else if (k === "error") { finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && rerender) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "review", "answer", "keys", "tenant", "rotate", "scope", "revoke",
    "expire", "audit", "offboard", "pack", "summary", "cleaned", "done", "error"];
  function subscribe() {
    const es = new EventSource(`/api/stream?since=${S.lastEvent}`);
    KINDS.forEach((kind) => es.addEventListener(kind, (e) => { try { onEvent(JSON.parse(e.data)); } catch { /* the browser's own `error` event has no data */ } }));
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
      const h = (e) => { let ev; try { ev = JSON.parse(e.data); } catch { return; } onEvent(ev); if (ev.id >= target) { es.close(); resolve(); } };
      KINDS.forEach((kind) => es.addEventListener(kind, h)); es.onerror = () => { es.close(); resolve(); };
    });
    S.caughtUp = true;
    if (state.status !== "running") { Object.keys(S.stepStatus).forEach((n) => { if (S.stepStatus[n] === "running") S.stepStatus[n] = "done"; }); }
    if (state.connected) S.stepStatus[1] = "done";
    S.follow = state.status === "running";
    show(state.status === "running" ? S.view : S.pack ? "pack" : "connect");
    subscribe();
  })();
})();

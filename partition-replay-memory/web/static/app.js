/* Partition replay memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "One runbook, three copies", sub: "09:00 — both regions read it", view: "setup" },
    { n: 3, title: "The partition", sub: "09:04–09:40", view: "partition" },
    { n: 4, title: "Reconnect and replay", sub: "three ways", view: "replay" },
    { n: 5, title: "What memory says", sub: "runbooks · fact trace", view: "memory" },
    { n: 6, title: "Conflict check", sub: "what MemoryLake flags", view: "conflicts" },
    { n: 7, title: "Score", sub: "which copy ended up right", view: "score" },
  ];
  const ARMS = ["A", "B", "C"];
  const fresh = () => ({ snap: null, part: [], outbox: null, replay: { A: [], B: [], C: [] }, replayed: null, memory: null, watch: [], conflicts: null, score: null });
  const S = { data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {}, world: null, ...fresh(),
    termCount: 0, lastEvent: 0, caughtUp: false };
  const D = () => S.data || {};
  const running = () => S.st.status === "running";
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const hhmmss = (iso) => (iso || "").slice(11, 19);
  const label = (k) => (D().fields || []).find((f) => f.key === k)?.label || k;
  const touched = () => (D().fields || []).map((f) => f.key).filter((k) => (D().outbox || []).some((e) => e.field === k) || (D().online_writes || []).some((w) => w.field === k));

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("");
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, partition: partitionView, replay: replayView, memory, conflicts: conflictView, score }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }

  function connect() {
    const st = S.st, d = D();
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? ARMS.map((a) => `<span class="mono">${a} ${esc(S.world[a])}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 4 minutes (the replay takes a minute; then 150 s watching for conflicts) · everything it creates is prefixed <code>${esc(d.prefix || "mlu-prp")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Run distributed agent fleets on memory that survives network partitions</h1>
      <p class="lead">${esc(d.company || "Tessane Freight")} (fictional) runs dispatch agents in us-east and eu-west on one shared runbook memory.
      At 09:04 eu-west loses its link to MemoryLake for 36 minutes, keeps working, and journals its changes in a local outbox; us-east changes two of the same settings.
      At 09:40 eu-west replays its outbox — three ways, each in its own copy of the runbook. It drives the <code>memorylake</code> CLI for real; no model calls.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> on your PATH (<code>fact trace</code>, <code>fact conflict</code>).</li>
          <li><b>Nothing is billed:</b> the demo makes no model calls.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-for-distributed-agent-deployments" target="_blank" rel="noopener">memorylake.ai › Memory for distributed agent deployments</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    const d = D(), w = S.world;
    if (!d.arms) return empty("One runbook, three copies");
    const snap = S.snap?.fields;
    return `<h1>One runbook, three copies — both regions read it at 09:00</h1>
      <p class="lead">Three projects with the same name (“${esc(d.project_name)}”) and the same six facts, one per way of replaying. eu-west keeps what it read — each fact's id, text and <code>updated_at</code> — because it is about to lose its link.</p>
      <div class="grid2">${ARMS.map((a) => `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].title)}</h3><p class="small">${esc(d.arms[a].how.replace(/`/g, ""))}</p>
        <p class="small muted" style="margin-bottom:0">project <span class="mono">${esc(w?.[a] || "pending")}</span></p></div>`).join("")}</div>
      <div class="card"><h3 style="margin-top:0">The runbook at 09:00</h3><table class="lint plain"><thead><tr><th>field</th><th>says</th><th>fact</th><th>updated_at (copy A)</th></tr></thead><tbody>
        ${d.fields.map((f) => `<tr><td><b>${esc(f.label)}</b></td><td>${esc(f.value)}</td><td class="small">${esc(f.base)}</td><td class="mono">${snap ? esc(hhmmss(snap.find((x) => x.key === f.key)?.updated_at?.A)) : "·"}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  function partitionView() {
    const d = D();
    if (!d.outbox) return empty("The partition");
    if (!S.part.length) return running() ? waiting("The partition") : empty("The partition");
    const on = S.part.filter((p) => p.region === d.regions.online), off = S.part.filter((p) => p.region === d.regions.offline);
    const row = (p) => `<div class="memfact"><span class="mono">${esc(p.at)}</span><span><b>${esc(label(p.field))}</b>: ${p.read ? `${esc(p.read)} → ` : "→ "}<b>${esc(p.value)}</b><br><span class="small muted">${esc(p.why)}</span></span></div>`;
    return `<h1>09:04–09:40 — eu-west is cut off; both regions keep changing the runbook</h1>
      <p class="lead">us-east writes straight to memory. eu-west cannot reach it, so its agents journal each change in a local outbox, with the <code>updated_at</code> they read at 09:00. Neither sees the other's changes.</p>
      <div class="grid2"><div class="card"><h3 style="margin-top:0">us-east · connected — writes to memory</h3>${on.map(row).join("") || '<span class="muted">·</span>'}</div>
        <div class="card"><h3 style="margin-top:0">eu-west · cut off — journals to its outbox</h3>${off.map(row).join("") || '<span class="muted">·</span>'}
        ${S.outbox ? `<p class="small" style="margin-bottom:0">Two of eu-west's four changes touch settings us-east changed meanwhile: the outbox still says 50 req/s and 300 s were current.</p>` : ""}</div></div>`;
  }

  function replayView() {
    const d = D();
    if (!S.outbox) return empty("Reconnect and replay");
    const cell = (a, k) => { const r = S.replay[a].find((x) => x.field === k);
      if (!r) return '<td class="muted">·</td>';
      if (r.outcome === "held") return `<td><span class="mark no">held</span> <span class="small">now ${esc(r.now)} (${esc(r.by)}, ${esc(r.by_at)})</span></td>`;
      if (r.outcome === "raced") return `<td><span class="mark bad">written over someone</span></td>`;
      return `<td>${r.outcome === "added" ? "added" : "written"}</td>`; };
    return `<h1>09:40 — the link is back; eu-west replays its outbox three ways</h1>
      <p class="lead">${ARMS.map((a) => `<b>${esc(d.arms[a].title)}</b>: ${esc(d.arms[a].how.replace(/`/g, ""))}`).join("<br>")}</p>
      <div class="card"><table class="lint plain"><thead><tr><th>outbox entry</th>${ARMS.map((a) => `<th>${esc(d.arms[a].short)}</th>`).join("")}</tr></thead><tbody>
        ${S.outbox.map((e) => `<tr><td><b>${esc(e.label)}</b> ${esc(e.read_value)} → ${esc(e.value)} <span class="small muted">(${esc(e.at)})</span></td>${ARMS.map((a) => cell(a, e.field)).join("")}</tr>`).join("")}
      </tbody></table>${S.replayed ? '<p class="small" style="margin-bottom:0">No command failed in any copy.</p>' : ""}</div>`;
  }

  function memory() {
    const m = S.memory, d = D();
    if (!m) return running() ? waiting("What memory says now") : empty("What memory says now");
    const t = m.target;
    const cell = (a, k) => { const e = m.books[a][k] || []; const ok = e.length === 1 && e[0].value === t[k].value;
      return `<td class="${ok ? "" : "bad-ink"}">${e.map((x) => esc(x.value)).join(" + ") || "(nothing)"} ${ok ? '<span class="mark ok">✓</span>' : '<span class="mark bad">✗</span>'}</td>`; };
    const k0 = (D().online_writes || [])[0]?.field || "rate", aSays = (m.books.A[k0] || []).map((x) => x.value).join(" + ");
    return `<h1>${aSays === t[k0].value ? `A says ${esc(aSays)}` : `A says ${esc(aSays)} — ${esc(t[k0].who)} changed it to ${esc(t[k0].value)} at ${esc(t[k0].at)}, and nothing flagged it`}</h1>
      <p class="lead">The runbook should hold the latest decision per field. A's replay wrote eu-west's older decisions over us-east's newer ones; B now holds two values per field; C kept us-east's and held eu-west's for review.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>field</th><th>should say</th>${ARMS.map((a) => `<th>${esc(d.arms[a].short)}</th>`).join("")}</tr></thead><tbody>
        ${touched().map((k) => `<tr><td><b>${esc(label(k))}</b></td><td>${esc(t[k].value)} <span class="small muted">${esc(t[k].who)} ${esc(t[k].at)}</span></td>${ARMS.map((a) => cell(a, k)).join("")}</tr>`).join("")}
      </tbody></table></div>
      <div class="card"><h3 style="margin-top:0"><code>fact trace</code> on A — what each replayed update replaced</h3>
        <table class="lint plain"><thead><tr><th>field</th><th>eu-west read</th><th>wrote</th><th>replaced</th></tr></thead><tbody>
        ${m.trace.map((r) => `<tr class="${r.blind ? "lost" : ""}"><td>${esc(r.label)}</td><td>${esc(r.read)}</td><td>${esc(r.wrote)}</td>
          <td>${r.blind ? `<span class="bad-ink"><b>${esc(r.replaced)}</b> — ${esc(r.replaced_by)}'s ${esc(r.replaced_at)} change, never seen by eu-west</span>` : `<span class="muted">${esc(r.replaced)} — what it read</span>`}</td></tr>`).join("")}
        </tbody></table>
        <p class="small" style="margin-bottom:0"><b>${m.blind} of ${m.trace.length}</b> replayed updates overwrote a change made during the partition. Every command succeeded — <code>fact update</code> takes no version or if-match. The trace shows it afterwards; nothing stopped it.</p></div>`;
  }

  function conflictView() {
    const d = D();
    if (!S.watch.length && !S.conflicts) return running() ? waiting("Conflict check") : empty("Conflict check");
    const last = S.watch[S.watch.length - 1];
    const cf = S.conflicts;
    const raised = (a) => cf ? cf.raised[a] : last?.found?.[a] ?? 0;
    return `<h1>Conflict detection — ${cf ? ARMS.map((a) => `${a} ${cf.raised[a]}`).join(" · ") : "watching…"}</h1>
      <p class="lead">MemoryLake checks new facts for contradictions. The demo watches all three copies for ${esc(d.window || 150)} s after B's last write, then resolves B's the obvious automatic way: keep the newest fact.
      ${last ? `<span class="pill ${cf ? "ok" : "busy"}">${cf ? "" : '<span class="spinner"></span>'} ${last.waited}s after the last write</span>` : ""}</p>
      <div class="grid2">${ARMS.map((a) => { const cs = cf?.conflicts?.[a] || [];
        return `<div class="card"><h3 style="margin-top:0">${esc(d.arms[a].short)}</h3>
          <div class="counts"><div class="count"><b class="${raised(a) ? "warn-ink" : ""}">${raised(a)}</b><span>conflicts</span></div></div>
          ${cs.map((c) => `<p class="small"><b>${esc(c.fields.map(label).join(", "))}</b> — ${c.real ? '<span class="warn-ink">both regions changed it</span>' : '<span class="muted">only eu-west changed it: nothing to review</span>'}</p><p class="small quote">“${esc(c.description)}”</p>`).join("")}
          ${a !== "B" && cf && !cs.length ? `<p class="small muted" style="margin-bottom:0">${a === "A" ? "Nothing to flag: <code>fact update</code> never runs detection, so A's overwrites are invisible to it." : "Nothing to flag: C never wrote over a change it had not read."}</p>` : ""}</div>`; }).join("")}</div>
      ${cf?.resolved?.length ? `<div class="card"><h3 style="margin-top:0">B resolved the latest-source way — keep the newest fact</h3><table class="lint plain"><thead><tr><th>field</th><th>kept</th><th>forgot</th><th>right?</th></tr></thead><tbody>
        ${cf.resolved.map((r) => `<tr><td>${esc(label(r.field))}</td><td>${esc(r.kept)} <span class="small muted">${esc(r.kept_by)}</span></td><td>${esc(r.forgot.join(", "))}</td><td>${r.right ? '<span class="mark ok">✓</span>' : '<span class="mark bad">✗ the newest to arrive, not the latest decision</span>'}</td></tr>`).join("")}
      </tbody></table></div>` : ""}`;
  }

  function score() {
    const sc = S.score, d = D();
    if (!sc) return running() ? waiting("Score") : empty("Score");
    const [a, b, c] = sc.rows;
    return `<h1>Fields right: ${sc.rows.map((r) => `${r.arm} ${r.right}/${r.of}`).join(" · ")}</h1>
      <p class="lead">A lost ${a.online - a.kept} of ${a.online} us-east changes with no error and no conflict. B surfaced ${b.review} conflicts — ${b.review_real} real, ${b.review_false} only stale — and keeping the newest put eu-west's older decisions back. C wrote ${c.landed} of ${c.clean} clean changes, kept us-east's ${c.kept}, and held ${c.review} for a person.</p>
      <div class="card"><table class="lint plain"><thead><tr><th>copy</th><th class="num">fields right</th><th class="num">us-east changes kept</th><th class="num">eu-west changes in</th><th class="num">for review (real / not)</th></tr></thead><tbody>
        ${sc.rows.map((r) => `<tr><td><b>${esc(r.title)}</b></td><td class="num"><b class="${r.right === r.of ? "" : "bad-ink"}">${r.right}/${r.of}</b></td>
          <td class="num">${r.kept}/${r.online}</td><td class="num">${r.landed}/${r.clean}</td><td class="num">${r.review} (${r.review_real} / ${r.review_false})</td></tr>`).join("")}</tbody></table></div>
      ${(sc.replay?.C || []).some((r) => r.outcome === "held") ? `<div class="card"><h3 style="margin-top:0">Held for review in C</h3>${sc.replay.C.filter((r) => r.outcome === "held").map((h) => `<div class="memfact"><span><b>${esc(label(h.field))}</b>: eu-west at ${esc(h.at)} wanted ${esc(h.read)} → <b>${esc(h.proposed)}</b> (${esc(h.why)}); ${esc(h.by)} changed it to <b>${esc(h.now)}</b> at ${esc(h.by_at)}${h.by_at > h.at ? " — the later decision" : ""}</span></div>`).join("")}</div>` : ""}`;
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
    if (!confirm("Delete the demo's three runbook copies and their facts?")) return;
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
    else if (k === "progress") {
      term("progress", ev.text.trim());
      if (d.region) S.part.push(d);
      if (d.arm) S.replay[d.arm].push(d);
      if (d.found) S.watch.push(d);
      if (S.caughtUp && ["partition", "replay", "conflicts"].includes(S.view)) renderStage();
    }
    else if (k === "json" || k === "text") term("dim", ev.text);
    else if (k === "connected") { S.st = d; S.stepStatus[1] = "done"; setConn(); }
    else if (k === "world") S.world = d;
    else if (k === "snapshot") S.snap = d;
    else if (k === "outbox") S.outbox = d.entries;
    else if (k === "replayed") S.replayed = d;
    else if (k === "memory") S.memory = d;
    else if (k === "conflicts") S.conflicts = d;
    else if (k === "score") S.score = d;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the three runbook copies and their facts are gone.", true); }
      else if (S.caughtUp) banner("Done. The Score step has the three copies side by side; Review lists what C held.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "snapshot", "outbox", "replayed", "memory", "conflicts", "score", "done", "error"];
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

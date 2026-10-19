/* Frozen-chain memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => Number(n || 0).toLocaleString("en-US");
  const code = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");

  const STEPS = [
    { n: 1, title: "Connect", sub: "API key, team, workspace", view: "connect" },
    { n: 2, title: "Set up", sub: "one project, one boundary", view: "setup" },
    { n: 3, title: "What Dispatchbot remembers", sub: "6 gate codes", view: "memory" },
    { n: 4, title: "Turn 1", sub: "stored in the chain", view: "first" },
    { n: 5, title: "The codes change", sub: "fact update · fact trace", view: "update" },
    { n: 6, title: "Send the conversation", sub: "whole history in the request", view: "history" },
    { n: 7, title: "Chain only", sub: "previous_response_id, no boundary", view: "bare" },
    { n: 8, title: "Chain + memory", sub: "and the fix that keeps the chain", view: "chain" },
    { n: 9, title: "Scoreboard", sub: "same chats, same model", view: "score" },
  ];
  const ARMKEYS = ["history", "bare", "chain", "newest"];
  const blankTurns = () => ({ history: [], bare: [], chain: [], newest: [] });
  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, stepStatus: {},
    world: null, before: null, after: null, trace: null, fresh: null, firsts: [], turns: blankTurns(),
    score: null, unavailable: null, summary: null, tries: [], tryRecipient: "Ruth Abara", tryAsk: "confirm", termCount: 0, lastEvent: 0, caughtUp: false,
  };

  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.stepStatus[s.n] || ""}" data-view="${s.view}">
        <span class="n">${S.stepStatus[s.n] === "done" ? "✓" : s.n}</span><span class="t">${s.title}<small>${s.sub}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "ask" ? "active" : ""}" data-view="ask"><span class="n">?</span><span class="t">Try one parcel<small>four ways, live</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, setup, memory, first, update, history: () => arm("history"), bare: () => arm("bare"), chain, score, ask }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first.</div>`;
  const waiting = (h) => `<h1>${h}</h1><div class="empty"><span class="pill busy"><span class="spinner"></span> working…</span></div>`;
  const unavailable = () => S.unavailable ? `<div class="card"><span class="pill err">MemoryRouter unavailable</span><p class="muted small">${esc(S.unavailable)}</p></div>` : "";
  const running = () => S.st.status === "running";
  const markOf = (ok) => ok == null ? '<span class="mark">·</span>' : `<span class="mark ${ok ? "ok" : "bad"}">${ok ? "✓" : "✗"}</span>`;
  const recOf = (name) => (S.data?.recipients || []).find((r) => r.name === name) || {};
  const askText = (k) => String(S.data?.turn2?.[k] || "").replace(/\{name\}/g, "<recipient>").replace(/\{parcel\}/g, "<parcel>");

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Memory</dt><dd>${S.world ? `<span class="mono">${esc(S.world.boundary)}</span>` : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${running() ? "disabled" : ""}>${S.world ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 1½ minutes · everything it creates is prefixed <code>${esc(S.data?.prefix || "mlu-fzc")}-</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>The memory was updated. The conversation still has the old gate code.</h1>
      <p class="lead">Dispatchbot reads delivery notes through <b>MemoryRouter</b> and keeps its conversations on the Responses API: <code>store: true</code>, then the
      new message plus <code>previous_response_id</code>. Between two turns every recipient phones in a new gate code and the app runs <code>fact update</code>.
      The memory is current — but the note attached on turn 1 was stored in the chain with the reply. This demo drives the <code>memorylake</code> CLI for real.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one.</li>
          <li><b>CLI v20261015 or later</b> — <code>memorylake router</code> is new in that release.</li>
          <li><b>Router calls are billed</b> to your MemoryLake balance: 55 calls on gpt-4o-mini per run.</li>
          <li>Turn-1 replies are stored with the model provider (<code>store: true</code>); MemoryLake cannot delete them.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/memory-api-for-llm-developers" target="_blank" rel="noopener">memorylake.ai › Memory API for LLM developers</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function setup() {
    if (!S.data) return "";
    return `<h1>One project for the delivery notes, one boundary for Dispatchbot</h1>
      <p class="lead">Dispatchbot never assembles memory itself: model calls go through MemoryRouter with <code>--boundary</code>, and MemoryRouter attaches the notes
      that match the request.</p>
      <div class="cols"><div class="card"><dl class="kv"><dt>Project</dt><dd class="mono small">${esc(S.world?.project || "pending")}</dd>
        <dt>Boundary</dt><dd class="mono small"><b>${esc(S.world?.boundary || "pending")}</b></dd></dl>
        <h3>Each Dispatchbot turn</h3>
        <div class="cmdline">router responses "&lt;dispatcher's message&gt;" --model ${esc(S.data.model)} --body &lt;request body&gt; --boundary ${esc(S.world?.boundary || "…")}</div></div>
        <div class="card"><h3 style="margin-top:0">${esc(S.data.company)}'s recipients</h3><div class="factlist">${S.data.recipients.map((r) => `<div class="factline"><b>${esc(r.name)}</b> · parcel ${esc(r.parcel)}</div>`).join("")}</div></div></div>`;
  }

  function memory() {
    if (!S.before) return running() ? waiting("What Dispatchbot remembers") : empty("What Dispatchbot remembers");
    return `<h1>What Dispatchbot remembers — ${S.before.facts.length} gate codes</h1>
      <p class="lead">One note per recipient, stored with <code>fact add</code>.</p>
      <div class="card"><div class="factlist">${S.before.facts.map((f) => `<div class="factline">${esc(f)}</div>`).join("")}</div></div>`;
  }

  function first() {
    if (!S.firsts.length) return unavailable() || (running() ? waiting("Turn 1") : empty("Turn 1"));
    return `<h1>Turn 1 — the dispatcher names the parcel</h1>
      <p class="lead">Sent with <code>store: true</code> and the boundary. MemoryRouter attaches the gate code; the provider stores the request — note included — and the reply
      in the chain. Every version of turn 2 continues from these same responses.</p>
      <div class="card chat">${S.firsts.map((f) => `<div class="msg user"><span class="who">Dispatcher</span>${esc(f.say)}</div>
        <div class="msg bot"><span class="who">${markOf(f.said_old)} Dispatchbot <span class="faint mono">${esc((f.id || "").slice(0, 18))}…</span></span>${esc(f.reply)}</div>`).join("")}</div>`;
  }

  function update() {
    if (!S.after) return unavailable() || (running() ? waiting("The codes change") : empty("The codes change"));
    const recs = S.data?.recipients || [];
    const tr = (S.trace?.trace || []).map((e) => `<div class="factline"><b>${esc(e.event)}</b> <span class="faint">${esc(e.source_kind)} · ${esc((e.timestamp || "").slice(11, 19))}Z</span><br>${e.old_fact ? `<span class="bad-ink">${esc(e.old_fact)}</span> → ` : ""}${esc(e.new_fact)}</div>`).join("");
    const fr = S.fresh ? `<div class="msg bot ${S.fresh.grade?.ok ? "" : "wrong"}"><span class="who">${markOf(S.fresh.grade?.ok)} a new conversation, same boundary</span>${esc(S.fresh.reply)}</div>` : "";
    return `<h1>Every recipient phones in a new gate code</h1>
      <p class="lead">The app records each call with <code>fact update</code>. <code>fact list</code> now shows the new codes; <code>fact trace</code> keeps the old one in the
      fact's history. A new conversation through the same boundary answers the new code.</p>
      <div class="cols"><div class="card"><h3 style="margin-top:0">fact list</h3><div class="factlist">${S.after.facts.map((f) => `<div class="factline">${esc(f)}</div>`).join("")}</div>
        <p class="small muted">${recs.map((r) => `${esc(r.name.split(" ")[0])} ${esc(r.old)} → <b>${esc(r.new)}</b>`).join(" · ")}</p></div>
        <div class="card"><h3 style="margin-top:0">fact trace · ${esc(S.trace?.recipient || "")}</h3>${tr}<div class="chat" style="margin-top:12px">${fr}</div></div></div>`;
  }

  function verdict(t) {
    const g = t.grade, r = recOf(t.recipient);
    if (!g) return "";
    if (g.verdict === "old") return `<span class="bad-ink small"> ← the old code (${esc(r.old)})</span>`;
    if (g.verdict === "both") return '<span class="bad-ink small"> ← both codes</span>';
    if (g.verdict === "none") return '<span class="bad-ink small"> ← no gate code</span>';
    return "";
  }
  function rows(list) {
    return list.map((t) => `<div class="msg bot ${t.grade && !t.grade.ok ? "wrong" : ""}"><span class="who">${markOf(t.grade ? t.grade.ok : null)} ${esc(t.recipient)}${verdict(t)}</span>${esc(t.reply || "…")}
      <div class="faint small">input_tokens ${fmt(t.input_tokens)}</div></div>`).join("");
  }
  const newOf = (list) => { const got = list.filter((t) => t.grade); return `${got.filter((t) => t.grade.ok).length}/${got.length}`; };
  function byAsk(key) {
    const ts = S.turns[key];
    return (S.data?.asks || ["confirm", "driver"]).map((k) => { const part = ts.filter((t) => t.ask === k);
      return `<div class="card chat"><div class="msg user"><span class="who">Dispatcher, turn 2 — ${newOf(part)} new code</span>${esc(askText(k))}</div>${rows(part)}</div>`; }).join("");
  }

  function arm(key) {
    const a = S.data?.arms?.[key] || { label: key, how: "" };
    const ts = S.turns[key];
    if (!ts.length) return unavailable() || (running() ? waiting(a.label) : empty(a.label));
    const why = key === "bare" ? "<p class=\"lead\"><b>This is the way to keep MemoryRouter from attaching someone else's notes on a follow-up</b> — and the chain answers with the note it stored on turn 1.</p>"
      : "<p class=\"lead\">Dispatchbot's own old answer is in the request too, but MemoryRouter attaches the current note on this turn and nothing older is frozen in.</p>";
    return `<h1>${esc(a.label)} — ${newOf(ts)} new code</h1>
      <p class="muted">${code(a.how)}. Same model (${esc(S.data?.model)}), temperature 0.</p>${why}<div class="cols">${byAsk(key)}</div>`;
  }

  function chain() {
    const c = S.turns.chain, n = S.turns.newest, A = S.data?.arms || {};
    if (!c.length) return unavailable() || (running() ? waiting(A.chain?.label || "Chain + memory") : empty(A.chain?.label || "Chain + memory"));
    return `<h1>${esc(A.chain?.label)} — ${newOf(c)} new code</h1>
      <p class="lead">The chain holds the old note, the boundary attaches the new one: both are in front of the model. Which one wins depends on how the dispatcher asks.</p>
      <div class="cols">${byAsk("chain")}</div>
      ${n.length ? `<h2>${esc(A.newest?.label)} — ${newOf(n)} new code</h2><p class="muted">${code(A.newest?.how)}.</p><div class="cols">${byAsk("newest")}</div>` : ""}`;
  }

  function score() {
    if (!S.score) return unavailable() || (running() ? waiting("Scoreboard") : empty("Scoreboard"));
    const tot = S.score.totals || {}, old = S.score.old || {}, A = S.data?.arms || {}, asks = S.data?.asks || ["confirm", "driver"];
    const n = S.score.rows.length;
    return `<h1>Scoreboard</h1><p class="lead">The same six chats, the same model; only how turn 2 is sent differs. Every gate code changed between the turns.</p>
      <div class="card"><table class="lint"><thead><tr><th rowspan="2">parcel</th>${ARMKEYS.map((k) => `<th colspan="2">${esc(A[k]?.short || k)}</th>`).join("")}</tr>
        <tr>${ARMKEYS.map(() => asks.map((a) => `<th class="small">${esc(a)}</th>`).join("")).join("")}</tr></thead><tbody>
        ${S.score.rows.map((r) => `<tr><td>${esc(r.recipient)} <span class="faint mono small">${esc(r.parcel)}</span></td>${ARMKEYS.map((k) => asks.map((a) => `<td>${markOf(r[k + ":" + a])}</td>`).join("")).join("")}</tr>`).join("")}
        <tr class="hl"><td><b>new code</b></td>${ARMKEYS.map((k) => asks.map((a) => `<td>${tot[k + ":" + a] ?? "–"}/${n}</td>`).join("")).join("")}</tr>
        <tr><td><b>old code</b></td>${ARMKEYS.map((k) => `<td colspan="2">${old[k] ?? "–"}/${2 * n}</td>`).join("")}</tr></tbody></table>
        <p class="muted small">“confirm” = “${esc(askText("confirm"))}” · “driver” = “${esc(askText("driver"))}”. The memory held the new codes for every one of these calls.</p></div>
      ${S.summary ? `<p class="muted small">${S.summary.tokens.calls} MemoryRouter calls this run: ${fmt(S.summary.tokens.prompt)} input + ${fmt(S.summary.tokens.completion)} output tokens, billed to your MemoryLake balance.</p>` : ""}`;
  }

  function ask() {
    const ready = !!S.world;
    const opts = (S.data?.recipients || []).map((r) => `<option ${r.name === S.tryRecipient ? "selected" : ""}>${esc(r.name)}</option>`).join("");
    const aopts = (S.data?.asks || []).map((a) => `<option value="${esc(a)}" ${a === S.tryAsk ? "selected" : ""}>${esc(askText(a))}</option>`).join("");
    return `<h1>Try one parcel — four ways, live</h1>
      <p class="lead">Pick a recipient and how turn 2 asks. The demo puts that recipient's note back to the old code, sends turn 1 (stored), changes the code with
      <code>fact update</code>, then sends turn 2 the four ways. The note is left on the new code.</p>
      <div class="card"><form class="ask-form" id="ask-form"><select id="ask-c" ${ready ? "" : "disabled"}>${opts}</select><select id="ask-a" ${ready ? "" : "disabled"}>${aopts}</select>
          <button class="btn primary" ${ready ? "" : "disabled"}>Send</button></form>
        <div class="chat" id="try-log">${S.tries.map((x) => { const r = recOf(x.recipient);
          return `<div class="msg user"><span class="who">Dispatcher</span>${esc(x.first.say)}</div>
          <div class="msg bot"><span class="who">Dispatchbot</span>${esc(x.first.reply)}</div>
          <div class="msg user"><span class="who">${esc(x.recipient)} phoned</span>gate code ${esc(r.old)} → <b>${esc(r.new)}</b> (fact update)</div>
          <div class="msg user"><span class="who">Dispatcher</span>${esc(String(S.data?.turn2?.[x.ask] || "").replace(/\{name\}/g, x.recipient).replace(/\{parcel\}/g, r.parcel))}</div>
          ${ARMKEYS.map((k) => `<div class="msg bot ${x[k].grade && !x[k].grade.ok ? "wrong" : ""}"><span class="who">${markOf(x[k].grade ? x[k].grade.ok : null)} ${esc(S.data?.arms?.[k]?.short)}${verdict({ ...x[k], recipient: x.recipient })}</span>${esc(x[k].reply)}</div>`).join("")}`; }).join("")
          || (ready ? '<div class="empty">Nothing sent yet.</div>' : '<div class="empty">Run the demo first.</div>')}</div></div>`;
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
    st.querySelector("#ask-form")?.addEventListener("submit", (e) => { e.preventDefault(); tryOne($("#ask-c").value, $("#ask-a").value); });
  }
  async function tryOne(recipient, askKey) {
    if (!recipient) return;
    S.tryRecipient = recipient; S.tryAsk = askKey || "confirm";
    const log = $("#try-log"); if (log) log.insertAdjacentHTML("afterbegin", `<span class="pill busy" id="sending"><span class="spinner"></span> ${esc(recipient)}: turn 1, the code change, then turn 2 four ways (about 40 s)…</span>`);
    const r = await api("/api/ask", { recipient, ask: S.tryAsk });
    if (r) S.tries.unshift(r);
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
    ["#btn-run", "#btn-cleanup"].forEach((id) => ($(id).disabled = !st.connected || busy));
  }
  function resetView() { S.before = null; S.after = null; S.trace = null; S.fresh = null; S.firsts = []; S.turns = blankTurns(); S.score = null; S.unavailable = null; S.summary = null; }
  async function runDemo() {
    banner(""); resetView(); S.stepStatus = { 1: "done" };
    S.follow = true; const r = await api("/api/run", {}); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo());
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the demo's memory project (and every note in it) and its boundary?")) return;
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
    else if (k === "memory") { if (d.phase === "after") S.after = d; else S.before = d; }
    else if (k === "trace") S.trace = d;
    else if (k === "fresh") S.fresh = d;
    else if (k === "first") S.firsts.push(d);
    else if (k === "turn") { if (S.turns[d.arm]) S.turns[d.arm].push(d); }
    else if (k === "scoreboard") S.score = d;
    else if (k === "summary") S.summary = d;
    else if (k === "router_unavailable") S.unavailable = d.reason;
    else if (k === "done") {
      finishRunning("done"); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.world = null; resetView(); S.stepStatus = { 1: "done" }; if (S.caughtUp) banner("Cleanup finished — the memory project, its notes and its boundary are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Try one parcel: four ways, live.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { if (!d.job) return; finishRunning("error"); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "world", "memory", "trace", "fresh", "first", "turn", "scoreboard", "summary", "router_unavailable", "done", "error"];
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

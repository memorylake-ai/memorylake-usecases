/* Bookkeeping memory — web companion. Vanilla JS, a view over the demo's event stream. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const S = {
    data: null, st: { connected: false, status: "idle" }, view: "connect", follow: true, status: {},
    clients: null, threads: {}, rules: {}, files: null, first: {}, resent: null, verified: null, whys: {},
    try: { client: "fernhill", payee: "", memo: "", amount: "", res: null }, termCount: 0, lastEvent: 0, caughtUp: false, sheets: {},
  };
  const story = () => S.data?.story || {};
  const clientsOf = () => story().clients || [];
  const nameOf = (k) => clientsOf().find((c) => c.key === k)?.name || k;
  const prefix = () => (S.data?.prefix || "mlu-bkm") + "-";
  const fileOf = (n) => String(n || "").startsWith(prefix()) ? String(n).slice(prefix().length) : String(n || "");
  const money = (x) => Number(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const STEPS = [
    { view: "connect", title: "Connect", sub: "API key, team, workspace" },
    { view: "clients", title: "Clients", sub: "one project per client" },
    { view: "threads", title: "Onboarding threads", sub: "each client's rules" },
    { view: "files", title: "Ledger exports", sub: "stats · inspect · one fails" },
    { view: "categorize", title: "Categorize", sub: "September bank feed" },
    { view: "resent", title: "Re-sent export", sub: "overwrite + reload" },
    { view: "verify", title: "Verify", sub: "cited rows vs the .xlsx" },
    { view: "why", title: "Why?", sub: "rule → message → batch" },
  ];
  const viewOfStep = (i) => STEPS[i - 1]?.view;

  // ---------------------------------------------------------------- steps + stage
  function renderSteps() {
    const el = $("#steps");
    el.innerHTML = STEPS.map((s, i) => `<button class="step ${S.view === s.view ? "active" : ""} ${S.status[s.view] || ""}" data-view="${s.view}">
        <span class="n">${S.status[s.view] === "done" ? "✓" : i + 1}</span><span class="t">${esc(s.title)}<small>${esc(s.sub)}</small></span></button>`).join("")
      + `<button class="step tool ${S.view === "try" ? "active" : ""}" data-view="try"><span class="n">?</span><span class="t">Categorize a line<small>any payee, either client</small></span></button>`;
    el.querySelectorAll(".step").forEach((b) => b.addEventListener("click", () => { S.follow = false; show(b.dataset.view); }));
  }
  function show(view) { S.view = view; renderSteps(); renderStage(); }
  function renderStage() {
    const r = { connect, clients, threads, files, categorize, resent, verify, why, try: tryView }[S.view];
    $("#stage").innerHTML = r ? r() : ""; bindStage(); hydrateSheets();
  }
  const empty = (h) => `<h1>${h}</h1><div class="empty">Run the demo first — this view fills in as it runs.</div>`;

  function connect() {
    const st = S.st;
    const form = st.connected ? `
      <div class="card"><div class="row spread"><h2 style="margin:0">Connected</h2><span class="pill ok">● live</span></div>
        <dl class="kv" style="margin-top:12px"><dt>Team</dt><dd>${esc(st.team?.name)} <span class="muted">(${esc(st.team?.role)})</span></dd>
          <dt>Workspace</dt><dd>${esc(st.workspace?.name)} <span class="mono muted">${esc(st.workspace?.id)}</span></dd>
          <dt>Client projects</dt><dd>${S.st.ids?.projects ? Object.entries(S.st.ids.projects).map(([k, v]) => `${esc(nameOf(k))} <span class="mono muted">${esc(v)}</span>`).join("<br>") : '<span class="muted">not created yet</span>'}</dd></dl>
        <div class="row" style="margin-top:16px"><button class="btn primary" data-act="run" ${st.status === "running" ? "disabled" : ""}>${S.st.ids ? "Run again" : "Run the demo"}</button>
          <span class="muted small">about 5 minutes · everything it creates is prefixed <code>${esc(prefix())}</code></span></div></div>` : `
      <form class="card" id="connect-form">
        <label for="api-key">MemoryLake API key</label>
        <div class="field"><input id="api-key" type="password" placeholder="sk-…" autocomplete="off" required><button type="button" class="eye" data-act="eye">show</button></div>
        <label for="base-url">Endpoint</label>
        <select id="base-url"><option value="https://app.memorylake.ai/openapi/memorylake">Global · app.memorylake.ai</option><option value="https://app.memorylake.cn/openapi/memorylake">China · app.memorylake.cn</option></select>
        <label for="workspace">Workspace id <span class="faint">(optional)</span></label><input id="workspace" placeholder="ws-…">
        <div class="row" style="margin-top:16px"><button class="btn primary" type="submit" id="btn-connect">Connect</button>
          <span class="muted small">The key stays in this local process and in <code>.memorylake-demo/</code>.</span></div></form>`;
    return `<h1>Every client's bookkeeping quirks, remembered — and every answer cited to a ledger row.</h1>
      <p class="lead"><b>${esc(story().firm)}</b> keeps the books for <b>${clientsOf().map((c) => esc(c.name)).join("</b> and <b>")}</b>, each in its own MemoryLake project:
      the owner's onboarding thread and past ledger exports (.xlsx). September's bank feed is categorized line by line — <b>the client's rule</b>, else <b>the last time that payee was booked</b>
      (cited as <code>file · Sheet!A15:E15</code>), else <b>ask the client</b>. One export arrives corrupted and is fixed in place; every cited row is checked against the original workbook;
      and “why is it booked that way?” goes back to the message, and the batch MemoryLake read it in. This page drives the <code>memorylake</code> CLI.</p>
      <div class="connect">${form}
        <div class="card"><h2 style="margin-top:0">Before you start</h2><ol class="steps-list">
          <li><b>Get an API key.</b> Sign up at <a href="https://app.memorylake.ai" target="_blank" rel="noopener">app.memorylake.ai</a>, open <b>API Keys</b>, create one. A free personal account is enough.</li>
          <li><b>The CLI is already here</b> — this server found it on PATH.</li>
          <li><b>Paste the key and connect,</b> then run. Re-running is safe; <b>Clean up</b> deletes the two projects, the threads, three actors and the uploaded files.</li></ol>
          <p class="muted small" style="margin-bottom:0">Use case page: <a href="https://www.memorylake.ai/en/usecase/bookkeeping-memory-for-small-accounting-firms" target="_blank" rel="noopener">memorylake.ai › bookkeeping memory for small accounting firms</a>
          · Source: <a href="https://github.com/memorylake-ai/memorylake-usecases" target="_blank" rel="noopener">memorylake-usecases</a></p></div></div>`;
  }

  function clients() {
    const c = S.clients; if (!c) return empty("Clients");
    return `<h1>One project per client</h1>
      <p class="lead">${esc(c.bookkeeper)} works both clients. Each client is a <b>project</b>: its rules, its thread and its files stay in it, and every lookup below is
      <code>--projects &lt;that client&gt;</code> — so the same vendor can be booked two different ways without either client's memory leaking into the other's.</p>
      <div class="grid2">${c.clients.map((x) => `<div class="card"><h2 style="margin-top:0">${esc(x.name)}</h2><div class="muted">${esc(x.what)} · owner ${esc(x.owner)}</div>
        <div class="mono small muted" style="margin-top:8px">${esc(x.project)}</div></div>`).join("")}</div>`;
  }

  function threads() {
    if (!Object.keys(S.threads).length) return empty("Onboarding threads");
    return `<h1>Onboarding threads — each client's rules, in their words</h1>
      <p class="lead">Each owner's thread is stored as a conversation in the client's project (<code>conv msg append</code>, dated). MemoryLake extracts facts from it;
      the rules themselves are <b>pinned word for word</b> (<code>fact add</code>) with metadata — vendor, line, category, thread, message — because a rule is applied verbatim.</p>
      ${clientsOf().map((c) => { const t = S.threads[c.key], r = S.rules[c.key]; if (!t) return "";
        return `<h2>${esc(c.name)}</h2><div class="grid2">
          <div class="card"><div class="turns">${t.turns.map((x) => `<div class="turn stored"><span class="mono small muted">${x.n}</span><div><div class="who">${esc(x.who)}</div><div class="what">${esc(x.text)}</div></div><span></span></div>`).join("")}</div></div>
          <div class="card">${r ? `<h3 style="margin-top:0">Rules on file</h3>${r.rules.map((x) => `<div class="fact pinned"><span class="tag">rule</span> <b>${esc(x.vendor)} ${esc(x.line)}</b> → ${esc(x.category)}${x.treatment ? ` <span class="muted">(${esc(x.treatment)})</span>` : ""}
              <div class="small muted">${esc(x.text)}</div><div class="mono small faint">${esc(x.id)} · message ${x.turn}</div></div>`).join("")}
            <h3>Also extracted by MemoryLake (${r.extracted.length})</h3>${r.extracted.map((x) => `<div class="fact small">${esc(x)}</div>`).join("") || '<div class="muted small">none this time</div>'}` : '<div class="empty">reading the thread…</div>'}</div></div>`; }).join("")}`;
  }

  // ---------------------------------------------------------------- a workbook drawn from the committed file, with ranges marked
  function cellRange(r) { const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(r || ""); if (!m) return null; const col = (s) => [...s].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0) - 1; return { c0: col(m[1]), r0: +m[2], c1: col(m[3]), r1: +m[4] }; }
  function sheetBlock(file, marks, opts) {
    return `<div class="sheet" data-file="${esc(file)}" data-marks="${esc(JSON.stringify(marks || []))}" data-max="${(opts && opts.max) || 0}"><div class="muted small">loading ${esc(file)}…</div></div>`;
  }
  async function hydrateSheets() {
    for (const el of document.querySelectorAll(".sheet[data-file]")) {
      const f = el.dataset.file;
      try {
        if (!S.sheets[f]) S.sheets[f] = await (await fetch(`/api/sheet?file=${encodeURIComponent(f)}`)).json();
        const sh = S.sheets[f]; if (sh.error) { el.innerHTML = `<div class="muted small">${esc(sh.error)}</div>`; continue; }
        const marks = JSON.parse(el.dataset.marks || "[]").map((m) => ({ ...m, rg: cellRange(m.range) })).filter((m) => m.rg);
        const max = +el.dataset.max || sh.rows.length;
        const focus = marks.find((m) => m.focus);
        let from = 1, to = sh.rows.length;
        if (max && sh.rows.length > max && focus) { from = Math.max(1, focus.rg.r0 - Math.floor(max / 2)); to = Math.min(sh.rows.length, from + max - 1); }
        const cls = (r, c) => marks.filter((m) => r >= m.rg.r0 && r <= m.rg.r1 && c >= m.rg.c0 && c <= m.rg.c1).map((m) => "m-" + m.kind).join(" ");
        const width = Math.max(...sh.rows.map((r) => r.length), 1);
        const head = `<tr><th></th>${Array.from({ length: width }, (_, c) => `<th>${String.fromCharCode(65 + c)}</th>`).join("")}</tr>`;
        const body = sh.rows.slice(from - 1, to).map((row, i) => { const r = from + i;
          return `<tr><th>${r}</th>${Array.from({ length: width }, (_, c) => `<td class="${cls(r, c)}">${esc(row[c] ?? "")}</td>`).join("")}</tr>`; }).join("");
        el.innerHTML = `<div class="sheet-cap"><b>${esc(f)}</b> · sheet <b>${esc(sh.sheet)}</b>${from > 1 || to < sh.rows.length ? ` · rows ${from}–${to} of ${sh.rows.length}` : ""}</div>
          <div class="sheet-scroll"><table class="grid">${head}${body}</table></div>
          ${marks.length ? `<div class="sheet-legend small">${[...new Set(marks.map((m) => m.kind))].map((k) => `<span class="sw m-${k}"></span>${esc({ table: "table MemoryLake found", title: "its title row", cite: "cited row", over: "row the rule overrides" }[k] || k)}`).join(" ")}</div>` : ""}`;
      } catch { /* leave the placeholder */ }
    }
  }

  function statsBar(st) {
    return ["pending", "running", "okay", "error"].map((k) => `<span class="pill ${k === "okay" && st[k] ? "ok" : k === "error" && st[k] ? "err" : ""}">${k} ${st[k] || 0}</span>`).join(" ");
  }
  function files() {
    const f = S.files; if (!f) return empty("Ledger exports");
    return `<h1>Ledger exports — what each project holds, and how MemoryLake read it</h1>
      <p class="lead">Each client's exports are uploaded and imported (<code>proj doc import --wait</code>). <code>proj stats</code> counts the documents by status;
      <code>proj doc get</code> names a failure; <code>proj doc inspect</code> shows <b>how each workbook was parsed</b> — the tables it found on each sheet, their cell ranges, a title row if there is one,
      and every column's type. Marrow &amp; Pine's September export arrived as a broken download.</p>
      ${clientsOf().map((c) => { const x = f[c.key]; if (!x) return "";
        const tables = Object.entries(x.tables || {});
        return `<h2>${esc(c.name)} <span class="small">${statsBar(x.stats || {})}</span></h2>
          ${(x.failed || []).map((d) => `<div class="card check bad"><div class="row spread"><b>✗ ${esc(fileOf(d.name))}</b><span class="pill err">status error</span></div>
            <div class="mono small">${esc(d.id)} · ${esc(d.code)}</div><div class="small">${esc(d.msg)}</div>
            <div class="small muted">Not in the <code>inspect</code> output: the server leaves out documents in status <code>error</code> and the CLI names them on stderr.</div></div>`).join("")}
          ${tables.map(([n, ts]) => `<div class="card"><div class="row spread"><b>✓ ${esc(fileOf(n))}</b><span class="pill ok">${ts.length} table(s) found</span></div>
            <table class="tbl small" style="margin:8px 0"><thead><tr><th>Range</th><th>Title</th><th>Rows</th><th>Columns (type)</th></tr></thead><tbody>
            ${ts.map((t) => `<tr><td class="mono">${esc(t.sheet)}!${esc(t.range)}</td><td>${t.title ? `“${esc(t.title)}”` : '<span class="muted">—</span>'}</td><td>${t.rows}</td>
              <td>${t.columns.map((col) => `${esc(col.name)} <span class="muted">${esc(col.type)}</span>`).join(", ")}</td></tr>`).join("")}</tbody></table>
            ${sheetBlock(fileOf(n), ts.flatMap((t) => [{ range: t.data_range && t.header_range ? `${t.header_range.split(":")[0]}:${t.data_range.split(":")[1]}` : t.range, kind: "table" }]
              .concat(t.title_range ? [{ range: t.title_range, kind: "title" }] : [])))}</div>`).join("")}`; }).join("")}`;
  }

  function sourceCell(r) {
    if (r.source === "rule") return `<span class="pill accent">rule</span> <span class="small">“${esc(r.rule.text)}”</span>
      ${(r.disagree || []).map((d) => `<div class="small warn-ink">! ${esc(fileOf(d.document))} · ${esc(d.sheet)}!${esc(d.cells)} booked it to <b>${esc(d.values.Category)}</b> — the rule wins</div>`).join("")}`;
    if (r.source === "precedent") { const p = r.precedent; return `<span class="pill ok">precedent</span> <span class="mono small">${esc(fileOf(p.document))} · ${esc(p.sheet)}!${esc(p.cells)}</span>
      <div class="small muted">${esc(p.values.Date)} · ${esc(p.values.Memo)} · ${esc(p.values.Amount)}${r.others ? ` · +${r.others} earlier` : ""}</div>`; }
    return `<span class="pill busy">ask the client</span> <span class="small muted">no rule, no booking of this payee in the ${r.looked} file(s) returned</span>`;
  }
  function feedTable(rows) {
    return `<table class="tbl"><thead><tr><th></th><th>Date</th><th>Payee · memo</th><th style="text-align:right">Amount</th><th>Category</th><th>Because</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${r.ok ? '<span class="ok-ink">✓</span>' : '<span class="err-ink">✗</span>'}</td><td class="mono small">${esc(r.line.date)}</td>
        <td><b>${esc(r.line.payee)}</b><div class="small muted">${esc(r.line.memo)}</div></td><td class="mono" style="text-align:right">${money(r.line.amount)}</td>
        <td><b>${esc(r.category || "—")}</b>${r.rule?.treatment ? `<div class="small muted">${esc(r.rule.treatment)}</div>` : ""}${r.ok ? "" : `<div class="small err-ink">expected ${esc(r.expect[0] || "ask")}</div>`}</td>
        <td>${sourceCell(r)}</td></tr>`).join("")}</tbody></table>`;
  }
  function categorize() {
    if (!Object.keys(S.first).length) return empty("Categorize");
    const all = Object.values(S.first).flat(), ok = all.filter((r) => r.ok).length;
    const pay = clientsOf().map((c) => (S.first[c.key] || []).find((r) => r.line.payee === "Paylane" && /fee/i.test(r.line.memo))).filter(Boolean);
    return `<h1>Categorize — the ${esc(story().month)} bank feed, client by client</h1>
      <p class="lead">Per line: a <b>client rule</b> on file (vendor + kind of line) wins; otherwise the latest booking of that payee in the client's own ledgers,
      from <code>search --types document --projects &lt;client&gt;</code> — an .xlsx hit is a table, one line per row, with the cells it covers, so the precedent is cited to its row;
      otherwise <b>ask the client</b>.</p>
      <div class="card big"><b>${ok} of ${all.length}</b> lines as expected.${pay.length === 2 ? ` The same Paylane fee line: <b>${esc(pay[0].category)}</b> for ${esc(nameOf(clientsOf()[0].key))}, <b>${esc(pay[1].category)}</b> for ${esc(nameOf(clientsOf()[1].key))}.` : ""}</div>
      ${clientsOf().map((c) => S.first[c.key] ? `<h2>${esc(c.name)}</h2><div class="card">${feedTable(S.first[c.key])}</div>` : "").join("")}
      ${(S.first.marrow || []).some((r) => r.disagree?.length) ? `<h2>The precedent the rule overrides</h2><div class="card">${sheetBlock("marrow-ledger-2026-q3.xlsx",
        S.first.marrow.flatMap((r) => (r.disagree || []).map((d) => ({ range: d.cells, kind: "over", focus: true }))), { max: 14 })}</div>` : ""}`;
  }

  function resent() {
    const r = S.resent; if (!r) return empty("Re-sent export");
    if (r.skipped) return `<h1>Re-sent export</h1><div class="card">The September export was already re-sent and reloaded in an earlier run (<span class="mono">${esc(r.document)}</span>).
      <span class="muted">“Reset &amp; run” replays the broken export.</span></div>`;
    return `<h1>Re-sent export — overwrite the file, reload the document</h1>
      <p class="lead">${esc(nameOf(r.client))} sends <b>${esc(r.file)}</b> again. It is uploaded under the same name with <code>lib upload --on-conflict overwrite</code> (same Library item),
      then <code>proj doc reload</code> processes <b>the same document</b> again from the new bytes — only a document in status <code>error</code> can be reloaded.</p>
      <div class="card"><div class="row"><span class="mono">${esc(r.document)}</span> <span class="pill err">error</span>${r.timeline.map(([t, s]) => ` → <span class="pill ${s === "okay" ? "ok" : s === "error" ? "err" : "busy"}">${esc(s)}</span> <span class="small muted">${t}s</span>`).join("")}</div>
        <div style="margin-top:10px">${statsBar(r.stats || {})}</div></div>
      ${(() => { const a = S.filesAfter?.[r.client]; const ts = a && Object.entries(a.tables || {}).find(([n]) => fileOf(n) === r.file);
        return ts ? `<div class="card"><b>proj doc inspect</b> now returns it: ${ts[1].map((t) => `<span class="mono">${esc(t.sheet)}!${esc(t.range)}</span> · ${t.rows} row(s) · ${t.columns.map((c) => `${esc(c.name)} <span class="muted">${esc(c.type)}</span>`).join(", ")}`).join("<br>")}</div>` : ""; })()}
      <h2>The lines that had no answer, again</h2><div class="card">${feedTable(r.rows || [])}</div>
      <div class="card">${sheetBlock(r.file, (r.rows || []).filter((x) => x.precedent).map((x) => ({ range: x.precedent.cells, kind: "cite" })))}</div>`;
  }

  function verify() {
    const v = S.verified; if (!v) return empty("Verify");
    return `<h1>Verify — every cited row against the original workbook</h1>
      <p class="lead">Not against the index that produced it: <code>proj doc download</code> fetches each cited workbook, its <b>sha256</b> must equal the committed export's,
      and every <code>Column: value</code> of the cited line must equal the cell under that header <b>in that row</b> of the sheet XML (read with the standard library; numbers compared as numbers).</p>
      <div class="card big"><b>${v.ok} of ${v.total}</b> cited row(s) check out. Control: the same cells against the next row down — <b>${v.caught} of ${v.control.length}</b> rejected.</div>
      <div class="card checks">${v.results.map((r) => `<div class="check ${r.ok ? "ok" : "bad"}"><span class="mark">${r.ok ? "✓" : "✗"}</span><span><b>[${r.n}]</b>
        <span class="mono">${esc(fileOf(r.document))} · ${esc(r.cells)}</span> <span class="muted small">(${esc(r.use)})</span> — sha256 <span class="mono">${esc(String(r.sha256 || "").slice(0, 12))}…</span>
        ${r.sha256_ok ? "matches" : "<b>differs</b>"} · ${r.cells_ok ? "cells match" : `<b>${esc((r.mismatch || []).join("; "))}</b>`}</span></div>`).join("")}</div>
      ${[...new Set(v.results.map((r) => fileOf(r.document)))].map((f) => `<div class="card">${sheetBlock(f, v.results.filter((r) => fileOf(r.document) === f).map((r) => ({ range: r.cells, kind: "cite" })))}</div>`).join("")}`;
  }

  function why() {
    if (!Object.keys(S.whys).length) return empty("Why?");
    return `<h1>Why is it booked that way? — rule → message → the batch MemoryLake read</h1>
      <p class="lead">The pinned rule carries the thread and the message it was agreed in. <code>conv consumed-messages</code> returns <b>the batch of messages MemoryLake read together</b>
      when it read that message — the context its memory of this rule came from. The thread arrived in two sittings, so the batch is the first sitting only.
      The fact MemoryLake extracted itself, traced with <code>fact trace</code>, comes from the same messages.</p>
      ${clientsOf().map((c) => { const w = S.whys[c.key]; if (!w) return "";
        const inBatch = new Set(w.batch.map((b) => b.seq));
        const turns = (S.threads[c.key]?.turns || []);
        return `<h2>${esc(c.name)}</h2><div class="card"><div class="excerpt">${esc(w.rule.text)}</div>
          <div class="small muted" style="margin:8px 0">rule <span class="mono">${esc(w.rule.id)}</span> · agreed in message ${w.rule.turn} · batch of ${w.batch.length} of the thread's ${w.thread}</div>
          <div class="turns">${turns.map((t) => { const b = w.batch.find((x) => x.seq === t.n);
            return `<div class="turn ${inBatch.has(t.n) ? "stored" : ""}"><span class="mono small">${b?.mark === "▶" ? '<span class="mk agreed" title="the message the rule was agreed in">▶</span>' : b?.mark === "★" ? '<span class="mk reason" title="the reason given for it">★</span>' : t.n}</span>
              <div><div class="who">${esc(t.who)} ${b ? `<span class="mono faint">${esc(b.custom_id || "")}</span>` : ""}</div><div class="what">${esc(t.text)}</div></div>
              <span class="mark">${inBatch.has(t.n) ? "in the batch" : "later batch"}</span></div>`; }).join("")}</div>
          <div class="small muted" style="margin-top:6px"><span class="mk agreed">▶</span> the message the rule was agreed in · <span class="mk reason">★</span> the reason given for it</div>
          ${w.extracted ? `<h3>MemoryLake's own extracted fact</h3><div class="fact">${esc(w.extracted.text)}</div>
            <div class="small muted">fact trace: ${esc(w.extracted.event)} · ${esc(w.extracted.source_kind)} · from ${w.extracted.sources} message(s)${w.extracted.same_batch ? " — <b>the same batch</b>" : ""}</div>`
            : '<div class="small muted" style="margin-top:8px">Extraction did not write a fact naming this vendor and category this time; the pinned rule is the record.</div>'}</div>`; }).join("")}`;
  }

  function tryView() {
    const ready = !!S.st.ids?.projects, t = S.try;
    return `<h1>Categorize a line</h1>
      <p class="lead">Any payee, either client: the same lookup the demo runs — the client's rules on file, then the client's own ledgers — against what MemoryLake holds now.</p>
      <div class="card"><form class="ask-form" id="try-form">
        <select id="try-client" ${ready ? "" : "disabled"}>${clientsOf().map((c) => `<option value="${esc(c.key)}" ${t.client === c.key ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>
        <input id="try-payee" placeholder="Payee, e.g. Paylane" value="${esc(t.payee)}" ${ready ? "" : "disabled"}>
        <input id="try-memo" placeholder="Memo, e.g. Card processing fees Sep" value="${esc(t.memo)}" ${ready ? "" : "disabled"}>
        <input id="try-amount" placeholder="Amount" value="${esc(t.amount)}" style="max-width:120px" ${ready ? "" : "disabled"}>
        <button class="btn primary" ${ready ? "" : "disabled"}>Categorize</button></form>
        <div class="chips">${[["fernhill", "Paylane", "Card processing fees Sep", "-240"], ["marrow", "Paylane", "Card processing fees Sep", "-92"], ["fernhill", "Copperleaf Office", "Toner", "-55"], ["marrow", "Hollis Dairy", "Butter", "-120"], ["marrow", "Northwind Hosting", "Servers", "-1480"]]
          .map(([c, p, m, a]) => `<button type="button" class="chip" data-c="${c}" data-p="${esc(p)}" data-m="${esc(m)}" data-a="${a}">${esc(nameOf(c))}: ${esc(p)}</button>`).join("")}</div>
        <div id="try-result" class="result">${!ready ? '<div class="empty">Run the demo first.</div>' : t.res ? tryResult(t.res) : ""}</div></div>`;
  }
  function tryResult(r) {
    return `<div class="card"><div class="row spread"><b>${esc(r.name)} · ${esc(r.line.payee)} · ${esc(r.line.memo)}</b><b>${esc(r.category || "ask the client")}</b></div>
      <div style="margin-top:8px">${sourceCell(r)}</div></div>
      ${r.precedent ? sheetBlock(fileOf(r.precedent.document), [{ range: r.precedent.cells, kind: "cite", focus: true }], { max: 12 }) : ""}`;
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
    const form = st.querySelector("#try-form");
    if (form) {
      form.addEventListener("submit", (e) => { e.preventDefault(); tryLine($("#try-client").value, $("#try-payee").value.trim(), $("#try-memo").value.trim(), $("#try-amount").value.trim()); });
      st.querySelectorAll(".chips .chip").forEach((c) => c.addEventListener("click", () => {
        $("#try-client").value = c.dataset.c; $("#try-payee").value = c.dataset.p; $("#try-memo").value = c.dataset.m; $("#try-amount").value = c.dataset.a;
        tryLine(c.dataset.c, c.dataset.p, c.dataset.m, c.dataset.a); }));
    }
  }
  async function tryLine(client, payee, memo, amount) {
    if (!payee) return; Object.assign(S.try, { client, payee, memo, amount });
    const box = $("#try-result"); box.innerHTML = `<span class="pill busy"><span class="spinner"></span> looking it up…</span>`;
    const r = await api("/api/categorize", { client, payee, memo, amount });
    if (!r) { box.innerHTML = ""; return; }
    S.try.res = r;
    if (S.view === "try") renderStage();
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
  function resetView() { S.filesAfter = null; S.clients = null; S.threads = {}; S.rules = {}; S.files = null; S.first = {}; S.resent = null; S.verified = null; S.whys = {}; }
  async function runDemo(reset) {
    banner(""); resetView(); S.status = { connect: "done" };
    S.follow = true; const r = await api("/api/run", { reset }); if (r) { S.st = r; setConn(); }
  }
  $("#btn-run").addEventListener("click", () => runDemo(false));
  $("#btn-reset").addEventListener("click", () => runDemo(true));
  $("#btn-cleanup").addEventListener("click", async () => {
    if (!confirm("Delete the two client projects, their threads, the three demo actors and the uploaded ledger files from your account?")) return;
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
    if (k === "step") { if (d.index === 2) resetView(); running(d.total === 2 ? null : viewOfStep(d.index)); term("step", ev.text); }
    else if (k === "cmd") term("cmd", ev.text);
    else if (k === "note") term("note", ev.text);
    else if (k === "progress") term("progress", ev.text.trim());
    else if (k === "json") term("dim", ev.text);
    else if (k === "text") { /* the CLI's own summary lines; the views show the same data */ }
    else if (k === "connected") { S.st = d; S.status.connect = "done"; setConn(); }
    else if (k === "existing") S.st.ids = d;
    else if (k === "clients") { S.clients = d; S.st.ids = { projects: Object.fromEntries(d.clients.map((c) => [c.key, c.project])) }; }
    else if (k === "thread") S.threads[d.client] = d;
    else if (k === "rules") S.rules[d.client] = d;
    else if (k === "files") { if (d.phase === "after") S.filesAfter = d.clients; else { if (!S.files) S.files = {}; Object.assign(S.files, d.clients); } }
    else if (k === "categorized") { if (d.phase === "first") S.first[d.client] = d.rows; else if (S.resent) S.resent.rows = d.rows; }
    else if (k === "resent") S.resent = d;
    else if (k === "verified") S.verified = d;
    else if (k === "why") S.whys[d.client] = d;
    else if (k === "done") {
      running(null); term("dim", `── ${d.job} finished`); S.st.status = "done"; setConn();
      if (d.job === "cleanup") { S.st.ids = null; resetView(); S.status = { connect: "done" }; if (S.caughtUp) banner("Cleanup finished — the projects, threads, actors and uploaded files are gone from your account.", true); }
      else if (S.caughtUp) banner("Done. Every cited row was checked against the original workbook; “Why?” shows where each rule came from.", true);
      if (S.caughtUp) refreshState();
    } else if (k === "error") { running(null); term("error", ev.text); if (S.caughtUp) banner(ev.text); S.st.status = "error"; setConn(); refreshState(); }
    if (S.caughtUp && !["cmd", "note", "progress", "json", "text"].includes(k)) { renderSteps(); renderStage(); }
  }
  async function refreshState() { try { S.st = await (await fetch("/api/state")).json(); setConn(); } catch { /* ignore */ } }
  const KINDS = ["step", "cmd", "note", "progress", "json", "text", "connected", "existing", "clients", "thread", "rules", "files",
    "categorized", "resent", "verified", "why", "cleaned", "done", "error"];
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
    show(state.status === "running" ? S.view : S.verified ? "categorize" : "connect");
    subscribe();
  })();
})();

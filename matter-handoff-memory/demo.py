#!/usr/bin/env python3
"""
Client matter handoff memory for law firms — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Brannock & Lise LLP (fictional) keeps one MemoryLake project per matter, and
one actor per recurring opposing counsel — "reflection memory for opposing
counsel patterns".  Garrick Holm (Tessaly Partners) was on the other side of
Fenwick Bakeries v. Corlett Freight Lines (settled 2026-09-22, amount
confidential) and is on the other side of Pell Harbor Marine v. Ardent Cold
Storage (mediation 2026-11-05).  Mira Castell ran both; she leaves on
October 16 and Jonah Reyes takes Pell Harbor over today, October 14.

The matter wall holds: searching the Pell Harbor project alone returns nothing
from Fenwick.  But the handoff read loads the matter *and* the opposing
counsel's patterns — `search --projects <matter> --actors <Holm>`, which is the
union of the two — and Mira wrote some Fenwick specifics on Holm's actor: the
confidential settlement amount, Fenwick's settlement authority, a gap in
Fenwick's evidence.  They come back in Pell Harbor's handoff.

Step 5 checks every fact on the shared actor against the matter being read —
its label (where it came from, portable pattern or matter-specific) and a scan
for any other matter's names and figures (the only thing that catches the note
nobody labelled).  Step 6 moves each withheld fact word for word to its own
matter's project and deletes it from the actor: Pell Harbor's handoff keeps
Holm's patterns and loses Fenwick's secrets; Fenwick still has them.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-mho"  # memorylake-usecases / matter-handoff-memory


# --------------------------------------------------------------------------- output sink

class DemoError(Exception):
    pass


def _print_sink(kind: str, text: str, data: dict) -> None:
    if kind == "step":
        print(f"\n{'=' * 78}\n  {text}\n{'=' * 78}")
    elif kind == "cmd":
        print("$ " + text)
    elif kind == "note":
        print(f"  · {text}")
    elif kind in ("text", "json", "progress"):
        print(text)


EMIT = _print_sink


def emit(kind: str, text: str = "", **data) -> None:
    EMIT(kind, text, data)


def banner(step: int, total: int, title: str) -> None:
    emit("step", f"Step {step}/{total}  {title}", index=step, total=total, title=title)


def note(msg: str) -> None:
    emit("note", msg)


def die(msg: str) -> None:
    raise DemoError(msg)


# --------------------------------------------------------------------------- CLI wrapper

class CLIError(RuntimeError):
    def __init__(self, cmd: list[str], rc: int, stdout: str, stderr: str):
        super().__init__(stderr.strip() or stdout.strip() or f"exit {rc}")
        self.cmd, self.rc, self.stdout, self.stderr = cmd, rc, stdout, stderr

    def has(self, *needles: str) -> bool:
        blob = (self.stderr + self.stdout).lower()
        return any(n.lower() in blob for n in needles)


class CLI:
    """Runs `memorylake …`, echoes the command, parses the JSON reply, retries read-only calls."""

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search", "trace"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "internal_error",
                 "http 502", "http 503", "http 504")

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False):
        self.binary, self.env, self.show_json = binary, env, show_json
        self.workspace: str | None = None
        self.secret: str | None = None
        self.base_url = DEFAULT_BASE_URL

    def _echo(self, args: list[str]) -> None:
        shown = [a.replace(self.secret, "sk-…") for a in args] if self.secret else list(args)
        emit("cmd", shlex.join(["memorylake", *shown]))

    def raw(self, *args: str, scoped: bool = False, echo: bool = True) -> tuple[int, str, str]:
        argv = [str(a) for a in args]
        if scoped and self.workspace:
            argv += ["--workspace", self.workspace]
        if echo:
            self._echo(argv)
        proc = subprocess.run([self.binary, *argv], env=self.env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def run(self, *args: str, scoped: bool = False, echo: bool = True):
        # Read-only calls retry on a dropped connection, and so does `fact update` (it sets the same text/metadata again).
        retryable = any(str(a) in self.READ_ONLY for a in args[:3]) or tuple(map(str, args[:2])) == ("fact", "update")
        attempt = 0
        while True:
            rc, out, err = self.raw(*args, scoped=scoped, echo=echo and attempt == 0)
            if rc == 0:
                break
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup; retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            return out


# --------------------------------------------------------------------------- step 1: connect

def find_binary() -> str:
    binary = os.environ.get("MEMORYLAKE_BIN") or shutil.which("memorylake")
    if not binary:
        die("the `memorylake` CLI is not on PATH.\n"
            "  install: curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh\n"
            "  or set MEMORYLAKE_BIN=/path/to/memorylake")
    return binary


def connect(show_json: bool = False, api_key: str | None = None, base_url: str | None = None,
            workspace: str | None = None) -> CLI:
    binary = find_binary()
    env = dict(os.environ)
    api_key = (api_key or env.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)
    cli.base_url = base_url
    if api_key:
        STATE_DIR.mkdir(exist_ok=True)
        env["MEMORYLAKE_CONFIG_DIR"] = str(STATE_DIR)
        cli.secret = api_key
        note(f"API key given — logging into an isolated profile under {STATE_DIR.name}/")
        cli.run("auth", "login", "--api-key", api_key, "--base-url", base_url, "--profile", PROFILE)
    else:
        note("no API key given — using your existing `memorylake auth login` session")
        rc, out, _ = cli.raw("auth", "status")
        if rc != 0 or "Logged in: yes" not in out:
            die("not logged in. Either export MEMORYLAKE_API_KEY=sk-… or run `memorylake auth login` first.")
    team = cli.run("team", "get")
    note(f"connected to team “{team.get('name')}” as {team.get('caller_role')}")
    cli.team = team  # type: ignore[attr-defined]
    ws = (workspace or env.get("MEMORYLAKE_WORKSPACE", "")).strip()
    ws_name = ""
    if not ws:
        rc, out, _ = cli.raw("ws", "current")
        m = re.search(r"\b(ws-[0-9a-f]+)\b", out) if rc == 0 else None
        ws = m.group(1) if m else ""
    if not ws:
        # `ws list` is newest first: its first item is whichever workspace was created last. Take the team's
        # default workspace, so `run` and `cleanup` land in the same one even after you add another.
        items = (cli.run("ws", "list", "--page-size", "100") or {}).get("items") or []
        items.sort(key=lambda w: (w.get("custom_id") != "_sys_default_workspace", w.get("created_at") or ""))
        if not items:
            die("this account has no workspace; create one with `memorylake ws create --name … --custom-id …`")
        ws, ws_name = items[0]["id"], items[0]["name"]
        note(f"using workspace “{ws_name}” ({ws})")
    else:
        note(f"using workspace {ws}")
    cli.workspace = ws
    cli.workspace_name = ws_name  # type: ignore[attr-defined]
    return cli



# --------------------------------------------------------------------------- data

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def matters(story: dict) -> dict[str, dict]:
    return {m["key"]: m for m in story["matters"]}


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null, so absent fields are simply left out.
    m = dict(key=f"mho:{r['id']}", record=r["id"], where=r["where"], kind=r["kind"], by=r["by"], on=r["on"])
    for k in ("matter", "scope"):
        if r.get(k):
            m[k] = r[k]
    if r.get("privileged"):
        m["privileged"] = True
    return m


# --------------------------------------------------------------------------- reading memory

def pages(cli: CLI, args: list[str], size: int = 50, echo: bool = True) -> list[dict]:
    """All pages of a list command: loop until the continuation token is null."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def scope_args(ids: dict, where: str) -> list[str]:
    return ["--actor", ids["actor"]] if where == "holm" else ["--project", ids["projects"][where]]


def list_facts(cli: CLI, ids: dict, where: str, echo: bool = True) -> list[dict]:
    flag = ["--actors", ids["actor"]] if where == "holm" else ["--projects", ids["projects"][where]]
    return pages(cli, ["fact", "list", *flag], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (F1, H6, …), metadata flattened in, plus the fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            out[m["record"]] = dict(m, fid=f["id"], body=f.get("fact") or "")
    return out


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_actor(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("actor", "get", cid(key), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


# --------------------------------------------------------------------------- the wall check (plain code, no model)

def scan(story: dict, text: str, current: str) -> list[tuple[str, str]]:
    """Identifiers of *other* matters that appear in `text`: (matter key, identifier)."""
    out = []
    for m in story["matters"]:
        if m["key"] == current:
            continue
        for ident in m["identifiers"]:
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(ident) + r"(?![A-Za-z0-9])", text or ""):
                out.append((m["key"], ident))
    return out


def wall(story: dict, r: dict, current: str) -> dict:
    """May this fact about a shared party be read in `current`?  Its label says where it came from and whether it is
    a portable pattern; the scan looks for another matter's names and figures in the text. Either one can withhold."""
    found = scan(story, r.get("body") or "", current)
    scope = r.get("scope") or ""
    if scope == "matter" and r.get("matter") != current:
        verdict, reason = "withheld", f"label: {r.get('matter')}-specific"
    elif found:
        verdict = "withheld"
        reason = ("no scope label" if not scope else f"label says {scope}") + "; scan: " + ", ".join(i for _, i in found)
    elif scope == "pattern":
        verdict, reason = "portable", "label: portable pattern"
    elif r.get("matter") == current:
        verdict, reason = "this matter", f"label: {current}-specific"
    else:
        verdict, reason = "unlabelled", "no scope label, no other matter's identifiers"
    origin = r.get("matter") or (found[0][0] if found else "")
    return dict(verdict=verdict, reason=reason, origin=origin, idents=[i for _, i in found])


WMARK = {"portable": "✓", "this matter": "✓", "withheld": "✗", "unlabelled": "?"}


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, ids: dict, current: str, with_actor: bool, top_k: int = 8, echo: bool = True) -> list[dict]:
    args = ["search", q, "--projects", ids["projects"][current]]
    if with_actor:
        args += ["--actors", ids["actor"]]
    res = cli.run(*args, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", where=m.get("where") or "?"))
    return out


def label_hits(story: dict, hits: list[dict], current: str) -> list[dict]:
    for h in hits:
        h["wall"] = wall(story, h, current) if h["where"] == "holm" else dict(
            verdict="this matter" if h["where"] == current else "withheld", reason=f"{h['where']} project",
            origin=h["where"], idents=[i for _, i in scan(story, h["body"], current)])
        # Every hit is checked by the scan too: a matter-project fact that names another matter is worth seeing.
        h["idents"] = [i for _, i in scan(story, h["body"], current)]
    return hits


def other_idents(hits: list[dict]) -> list[str]:
    seen = []
    for h in hits:
        for i in h.get("idents") or []:
            if i not in seen:
                seen.append(i)
    return seen


def hit_line(story: dict, h: dict, rank: str = "") -> str:
    src = "Holm actor" if h["where"] == "holm" else matters(story).get(h["where"], {}).get("short", h["where"]) + " project"
    flag = f"  ✗ {h['wall']['origin']}" if h["wall"]["verdict"] == "withheld" else ""
    return f"      {rank or '#' + str(h['rank']):<5} {h['record']:<3} {src:<20} {clip(h['body'], 76)}{flag}"


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "where", "kind", "by", "on", "matter", "scope", "privileged", "fid", "body",
                                  "rank", "at", "wall", "idents", "moved_from", "moved_on")}


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    pids, made = {}, []
    for m in story["matters"]:
        proj = find_project(cli, m["key"], echo=False)
        if proj is None:
            proj = cli.run("proj", "create", "--name", f"Matter: {m['name']}", "--custom-id", cid(m["key"]),
                           "--description", m["description"], scoped=True, echo=not made)
            made.append(m["key"])
        pids[m["key"]] = proj["id"]
    if len(made) > 1:
        note(f"… and {len(made) - 1} more `proj create` the same way ({', '.join(made[1:])})")
    c = story["counsel"]
    actor = find_actor(cli, c["key"], echo=False)
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(c["key"]), "--display-name", f"{c['name']} ({c['firm']})",
                        "--description", c["description"], "--tags", "opposing-counsel")
        made.append(c["key"])
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    if len(made) < len(pids) + 1:
        note(f"{len(pids) + 1 - len(made)} object(s) already exist from an earlier run (pass --reset to start over)")
    ms = matters(story)
    lines = [""]
    for k, pid in pids.items():
        lines.append(f"    project  {ms[k]['name']:<44} {ms[k]['status']:<22} {pid}")
    lines.append(f"    actor    {c['name'] + ', ' + c['firm'] + ' — opposing counsel in both':<67} {actor['id']}")
    emit("text", "\n".join(lines))
    ids = dict(projects=pids, actor=actor["id"])
    emit("setup", "", projects=pids, actor=actor["id"], matters=story["matters"], counsel=c)
    return ids


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, ids: dict, where: str, items: list[tuple[str, dict]], echo: bool = True,
        live: dict | None = None) -> dict[str, dict]:
    """Pin the entries the scope does not hold yet (matched by metadata key) — one `fact add` for all of them,
    then `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new}."""
    if live is None:
        live = {}
        for f in list_facts(cli, ids, where, echo=False):
            k = (f.get("metadata") or {}).get("key")
            if k:
                live[k] = f
    out, missing = {}, []
    for text, meta in items:
        f = live.get(meta["key"])
        if f is None:
            missing.append((text, meta))
        else:
            out[meta["key"]] = dict(id=f["id"], new=False)
    if missing:
        res = cli.run("fact", "add", *scope_args(ids, where), *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, *scope_args(ids, where), "--metadata", json.dumps(meta), scoped=True,
                    echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
    return out


def restore_handoff_day(cli: CLI, ids: dict, story: dict) -> int:
    """Every run starts on handoff day: facts an earlier run moved back to their own matter are deleted there
    (step 3 pins them on the actor again)."""
    moved = []
    for m in story["matters"]:
        for f in list_facts(cli, ids, m["key"], echo=False):
            if (f.get("metadata") or {}).get("moved_from"):
                moved.append((m["key"], f["id"]))
    for i, (k, fid) in enumerate(moved):
        cli.run("fact", "delete", "--project", ids["projects"][k], fid, scoped=True, echo=i == 0)
    if moved:
        note(f"an earlier run moved {len(moved)} fact(s) off the actor; they go back to where they were on handoff day")
    return len(moved)


def intake(cli: CLI, ids: dict, story: dict) -> dict:
    restored = restore_handoff_day(cli, ids, story)
    note("each matter's records go on its project; Mira's notes about Garrick Holm go on his actor — where a "
         "'reflection memory for opposing counsel patterns' belongs. One fact each, word for word, then "
         "`fact update --metadata`: where it came from (matter), whether it is a portable pattern or matter-specific "
         "(scope), privileged or not. One of her notes on Holm never got a scope label")
    new, first = 0, True
    for where in [m["key"] for m in story["matters"]] + ["holm"]:
        recs = [r for r in story["records"] if r["where"] == where]
        got = pin(cli, ids, where, [(r["text"], record_meta(r)) for r in recs], echo=first or where == "holm")
        first = False
        new += sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    else:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new} new fact(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = {}
    for where in [m["key"] for m in story["matters"]] + ["holm"]:
        mem.update(entries(list_facts(cli, ids, where)))
    ms = matters(story)
    lines = []
    for where in [m["key"] for m in story["matters"]] + ["holm"]:
        rs = sorted((r for r in mem.values() if r["where"] == where), key=lambda r: r["record"])
        head = f"{ms[where]['name']} — project" if where in ms else f"{story['counsel']['name']} — opposing-counsel actor"
        lines.append(f"\n  {head}, {len(rs)} records:")
        for r in rs:
            lab = ""
            if where == "holm":
                lab = (f"{r.get('matter', '?')}/{r['scope']}" if r.get("scope") else "— no label —")
            priv = " privileged" if r.get("privileged") else ""
            lines.append(f"    {r['record']:<3} {r['on']}  {lab:<22}{priv:<11} {clip(r['body'], 70)}")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["where"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the handoff read

def handoff_read(cli: CLI, ids: dict, story: dict, mem: dict) -> dict:
    cur = story["current"]
    ms = matters(story)
    q = story["questions"]["handoff"]
    note(f"control first: the same question scoped to the {ms[cur]['short']} project alone — the matter wall")
    ctl = label_hits(story, search(cli, q, ids, cur, with_actor=False), cur)
    lines = [f"\n  “{q}” · --projects {ms[cur]['short']} · top {len(ctl)}:"] + [hit_line(story, h) for h in ctl[:4]]
    if len(ctl) > 4:
        lines.append(f"      … {len(ctl) - 4} more")
    lines.append(f"  → other matters' names and figures in these hits: {', '.join(other_idents(ctl)) or 'none'}")
    emit("text", "\n".join(lines))
    note(f"now the handoff read, the way the page describes it: {story['taking_over']['name']}'s assistant loads the matter "
         "and the opposing counsel's patterns — `search --projects <matter> --actors <Holm>` (the union of the two)")
    qs = [q, story["questions"]["numbers"]]
    asked, seen = [], {}
    for i, qq in enumerate(qs):
        hits = label_hits(story, search(cli, qq, ids, cur, with_actor=True), cur)
        asked.append(dict(q=qq, hits=hits))
        for h in hits:
            seen.setdefault(h["record"], h)
    lines = []
    for a in asked:
        lines.append(f"\n  “{a['q']}” · --projects {ms[cur]['short']} --actors Holm · top {len(a['hits'])}:")
        lines += [hit_line(story, h) for h in a["hits"]]
    live = {r["fid"] for r in mem.values()}
    cached = sorted({h["record"] for a in asked for h in a["hits"] if h["fid"] not in live})
    if cached:
        # `search` caches by query text: a repeat of the same question can come back with an earlier run's fact ids.
        note(f"{len(cached)} hit(s) carry fact ids from an earlier run ({', '.join(cached)}) — search answered from its cache")
    leaks = [h for h in seen.values() if h["wall"]["verdict"] == "withheld"]
    leaks.sort(key=lambda h: h["record"])
    idents = other_idents(list(seen.values()))
    lines.append(f"\n  → {len(seen)} distinct facts across the two questions; {len(leaks)} of them are "
                 f"{', '.join(sorted({ms[h['wall']['origin']]['short'] for h in leaks})) or 'another matter'}'s material:")
    for h in leaks:
        lines.append(f"      {h['record']}  {'privileged ' if h.get('privileged') else ''}{clip(h['body'], 96)}")
    lines.append(f"  → names and figures from another matter now in the {ms[cur]['short']} handoff: {', '.join(idents) or 'none'}")
    emit("text", "\n".join(lines))
    out = dict(control=dict(q=q, hits=ctl, idents=other_idents(ctl)), asked=asked, cached=cached,
               leaks=[public(h) for h in leaks], idents=idents, distinct=len(seen))
    emit("handoff", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: the wall check

def wall_check(cli: CLI, ids: dict, story: dict, hr: dict) -> dict:
    cur = story["current"]
    ms = matters(story)
    note(f"the wall check: every fact on Holm's actor — `fact list` (every page), not search's top 8 — read against "
         f"the {ms[cur]['short']} matter: its label (where it came from, portable or matter-specific) and a scan for any "
         "other matter's names and figures. Either one withholds a fact; the scan is what catches a note nobody labelled")
    facts = entries(list_facts(cli, ids, "holm"))
    rows = []
    for r in sorted(facts.values(), key=lambda r: r["record"]):
        rows.append(dict(public(r), wall=wall(story, r, cur)))
    lines = [f"\n    {'id':<3} {'label':<22} {'verdict':<14} reason"]
    for r in rows:
        lab = f"{r.get('matter')}/{r.get('scope')}" if r.get("scope") else "— no label —"
        w = r["wall"]
        lines.append(f"    {r['record']:<3} {lab:<22} {WMARK[w['verdict']] + ' ' + w['verdict']:<14} {w['reason']}")
    counts = {k: sum(1 for r in rows if r["wall"]["verdict"] == k) for k in ("portable", "this matter", "withheld", "unlabelled")}
    by_scan = [r["record"] for r in rows if r["wall"]["verdict"] == "withheld" and not r.get("scope")]
    lines.append(f"\n  → {len(rows)} facts on Holm: {counts['portable']} portable patterns · {counts['this matter']} "
                 f"{ms[cur]['short']}-specific · {counts['withheld']} withheld (another matter's)"
                 + (f" · {counts['unlabelled']} unlabelled" if counts["unlabelled"] else ""))
    if by_scan:
        lines.append(f"  → caught only by the scan (no label): {', '.join(by_scan)}")
    held = {r["record"] for r in rows if r["wall"]["verdict"] == "withheld"}
    if not hr["asked"]:
        emit("text", "\n".join(lines))
        return dict(rows=rows, counts=counts, by_scan=by_scan, brief=[], kept_idents=[])
    brief = []
    for qi, a in enumerate(hr["asked"], 1):
        for h in a["hits"]:
            if h["record"] not in held and h["record"] not in {b["record"] for b in brief}:
                brief.append(dict(h, at=f"q{qi}#{h['rank']}"))
    lines.append(f"\n  the {ms[cur]['short']} handoff through the wall — the same two searches (q1, q2), withheld facts "
                 f"dropped ({len(brief)} kept):")
    lines += [hit_line(story, h, h["at"]) for h in brief]
    kept_idents = other_idents(brief)
    lines.append(f"  → other matters' names and figures left: {', '.join(kept_idents) or 'none'}")
    emit("text", "\n".join(lines))
    out = dict(rows=rows, counts=counts, by_scan=by_scan, brief=[public(h) for h in brief], kept_idents=kept_idents)
    emit("wall", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: move them back

def move_back(cli: CLI, ids: dict, story: dict, wc: dict) -> dict:
    cur = story["current"]
    ms = matters(story)
    note("the fix is at the source: a matter's specifics belong to that matter. Each withheld fact is pinned word for "
         "word on its own matter's project (metadata says where it came from), then deleted from Holm's actor — "
         "nothing is lost, it just stops travelling with him")
    moves = [r for r in wc["rows"] if r["wall"]["verdict"] == "withheld"]
    done = []
    for i, r in enumerate(moves):
        target = r["wall"]["origin"]
        meta = dict(key=f"mho:{r['record']}:moved", record=r["record"], where=target, kind=r["kind"], by=r["by"], on=r["on"],
                    matter=target, scope="matter", moved_from="holm", moved_on=story["today"])
        if r.get("privileged") or r["wall"]["idents"]:
            meta["privileged"] = True
        got = pin(cli, ids, target, [(r["body"], meta)], echo=i == 0)
        cli.run("fact", "delete", "--actor", ids["actor"], r["fid"], scoped=True, echo=i == 0)
        done.append(dict(record=r["record"], to=target, fid=got[meta["key"]]["id"], body=r["body"]))
    if len(moves) > 1:
        note(f"… and the same for the other {len(moves) - 1}")
    left = entries(list_facts(cli, ids, "holm"))
    lines = [f"\n  moved: " + ", ".join(f"{d['record']} → {ms[d['to']]['short']} project" for d in done),
             f"  Holm's actor now: {len(left)} facts — " + ", ".join(sorted(left))]
    emit("text", "\n".join(lines))
    # Not this morning's wording: `search` can answer a repeated question from a cache made before the move.
    q = story["questions"]["after"]
    note("the handoff read again, new wording (search answers a repeated question from its cache), until the "
         "deleted facts have left the search index")
    gone = set(d["record"] for d in done)
    t1, tries = time.time(), 0
    while True:
        tries += 1
        hits = label_hits(story, search(cli, q, ids, cur, with_actor=True, echo=tries == 1), cur)
        stale = [h for h in hits if h["record"] in gone and h["where"] == "holm"]
        if not stale or tries >= 8:
            break
        time.sleep(4)
    if tries > 1:
        note(f"the search index caught up on try {tries} ({int(time.time() - t1)} s)" if not stale
             else f"{len(stale)} deleted fact(s) still returned after {tries} tries ({int(time.time() - t1)} s)")
    patterns = [h for h in hits if h["wall"]["verdict"] == "portable"]
    lines = [f"\n  “{q}” · --projects {ms[cur]['short']} --actors Holm · top {len(hits)}:"] + [hit_line(story, h) for h in hits]
    idents = other_idents(hits)
    lines.append(f"  → other matters' names and figures: {', '.join(idents) or 'none'} · Holm's portable patterns in the "
                 f"top {len(hits)}: {len(patterns)} ({', '.join(h['record'] for h in patterns)})")
    origin = moves[0]["wall"]["origin"] if moves else "fenwick"
    qa = f"What did {ms[origin]['short']} settle for, and what was the client's authority?"
    note(f"and nothing was lost: the {ms[origin]['short']} matter still answers for itself")
    ah = label_hits(story, search(cli, qa, ids, origin, with_actor=False), origin)
    lines += [f"\n  “{qa}” · --projects {ms[origin]['short']} · top {min(4, len(ah))} of {len(ah)}:"] + [hit_line(story, h) for h in ah[:4]]
    found = [h["record"] for h in ah if h["record"] in gone]
    lines.append(f"  → the moved facts found on the {ms[origin]['short']} project: {', '.join(found) or 'none yet'}")
    emit("text", "\n".join(lines))
    out = dict(moved=done, left=sorted(left), q=q, hits=[public(h) for h in hits], idents=idents, tries=tries,
               patterns=[h["record"] for h in patterns], origin_q=qa, origin_hits=[public(h) for h in ah], found=found)
    emit("moved", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, wall

def ask(cli: CLI, ids: dict, q: str, matter: str = "", with_actor: bool = True, echo: bool = True) -> dict:
    """Any question: search's top 8 on one matter (default: the one being handed over), with or without Holm's actor,
    each hit labelled with where it lives and judged by the wall."""
    story = load_story()
    matter = matter or story["current"]
    hits = label_hits(story, search(cli, q, ids, matter, with_actor=with_actor, echo=echo), matter)
    return dict(q=q, matter=matter, with_actor=with_actor, hits=[public(h) for h in hits], idents=other_idents(hits))


def need_ids(cli: CLI) -> dict:
    story = load_story()
    pids = {}
    for m in story["matters"]:
        proj = find_project(cli, m["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        pids[m["key"]] = proj["id"]
    actor = find_actor(cli, story["counsel"]["key"], echo=False)
    if actor is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(projects=pids, actor=actor["id"])


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    def facts() -> list:
        return pages(cli, ["fact", "list", "--actors", actor_id], echo=False)

    try:
        found = facts()
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        # The binding takes a moment to show: `fact list` can still answer 404 right after it.
        for wait in (1, 2, 4, 8, 0):
            try:
                found = facts()
                break
            except CLIError as e:
                if not e.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    for f in found:
        cli.run("fact", "delete", "--actor", actor_id, f["id"], scoped=True, echo=False)
    left = facts()
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id)
    if found:
        note(f"deleted {len(found)} fact(s) on {actor_id} before the actor")
    return len(found)


def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    gone = 0
    for m in story["matters"]:
        proj = find_project(cli, m["key"], echo=not quiet and gone == 0)
        if not proj:
            continue
        for f in pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False):
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on {m['key']} after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        gone += 1
    actor = find_actor(cli, story["counsel"]["key"], echo=not quiet)
    if actor:
        delete_actor(cli, actor["id"])
        gone += 1
    note(f"deleted {gone} object(s) (projects + the opposing-counsel actor) and every fact on them")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one project per matter, one actor for the opposing counsel")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: two matters and the opposing counsel's profile")
    mem = intake(cli, ids, story)
    banner(4, TOTAL, "Handoff day — the matter, plus the opposing counsel's patterns")
    hr = handoff_read(cli, ids, story, mem)
    banner(5, TOTAL, "The wall check — every fact on the shared actor, against this matter")
    wc = wall_check(cli, ids, story, hr)
    banner(6, TOTAL, "Move them back — matter specifics to their own matter, patterns stay with the person")
    mb = move_back(cli, ids, story, wc)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   control_idents=hr["control"]["idents"], handoff_leaks=[h["record"] for h in hr["leaks"]],
                   handoff_idents=hr["idents"], cached=hr["cached"], handoff_ranks={a["q"][:24]: [h["record"] for h in a["hits"]] for a in hr["asked"]},
                   wall_counts=wc["counts"], by_scan=wc["by_scan"], brief_idents=wc["kept_idents"],
                   moved=[d["record"] for d in mb["moved"]], after_idents=mb["idents"], after_patterns=mb["patterns"],
                   after_tries=mb["tries"], origin_found=mb["found"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "wall", "ask", "cleanup"],
                    help="run = full demo (default); wall = the wall check of Holm's actor against Pell Harbor; "
                         "ask \"QUESTION\" [--matter fenwick|pellharbor] [--no-actor] = search's top 8 on one matter, "
                         "with Holm's actor unless --no-actor; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask)")
    ap.add_argument("--matter", default="", help="ask: the matter to search (default pellharbor)")
    ap.add_argument("--no-actor", action="store_true", help="ask: search the matter project alone")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one argument, e.g. ask \"How does Garrick Holm negotiate at mediation?\"")
    if a.matter and a.matter not in matters(story):
        ap.error(f"--matter is one of {', '.join(matters(story))}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py wall`, `python3 demo.py ask \"How does Garrick Holm negotiate at mediation?\"` or "
              "`python3 demo.py ask \"What did Fenwick settle for?\" --matter fenwick`; `python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "wall":
        banner(2, 2, "The wall check — Holm's actor against the Pell Harbor matter")
        wall_check(cli, ids, story, dict(asked=[]))
    elif a.command == "ask":
        ms = matters(story)
        banner(2, 2, f"Ask — top 8 on {ms[a.matter or story['current']]['short']}" + (" alone" if a.no_actor else " + Holm's actor"))
        r = ask(cli, ids, a.args[0], a.matter, with_actor=not a.no_actor)
        lines = [f"\n  “{r['q']}” — top {len(r['hits'])}:"] + [hit_line(story, h) for h in r["hits"]]
        lines.append(f"  → other matters' names and figures: {', '.join(r['idents']) or 'none'}")
        emit("text", "\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        # The failing command can be `auth login --api-key …`: never print the key.
        shown = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-…", shlex.join(e.cmd)) if e.cmd else "(memorylake)"
        print(f"\ncommand failed: {shown}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

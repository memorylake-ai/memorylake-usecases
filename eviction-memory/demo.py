#!/usr/bin/env python3
"""
Eviction memory for long-running agents — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tern (fictional) is the platform-operations agent at Kestrel Lane Freight. It
has been running since 2026-04-14 and has remembered 36 things: founding
decisions, rare constraints, preferences, incident and deploy events, chat
turns. Its active memory is capped at 20, so 16 have to go.

The page names the two naive policies and what each one loses. Drop-oldest
keeps the 20 newest entries and drops the founding ledger rule (ledger_entries
is append-only). Drop-least-used keeps the 20 most-retrieved entries and drops
the quarter-end close freeze, which was needed exactly once. Typed retention
keeps every decision, constraint and preference, keeps an event while it is in
use, and moves the rest to a cold archive project with an eviction record --
nothing is deleted.

Each policy gets its own active-memory project holding the 20 it kept. Three
real tasks are then checked against all three (`search`, top 8, the same
question): schedule a migration on 29 December (needs the freeze), fix a
ledger row with an UPDATE (needs the ledger rule), rotate the staging Redis
password (a recent event every policy keeps -- the control). Drop-oldest
misses the ledger rule, drop-least-used misses the freeze, typed retention
misses neither. Last, a question about an old incident: the typed active
memory does not have it, active + archive does, with its eviction record.

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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-evm"  # memorylake-usecases / eviction-memory

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
        if shown[:2] == ["fact", "add"] and len(shown) > 8:
            # One `fact add` carries up to 36 entries: echo the first two and a count (display only).
            texts = [a for a in shown[4:] if not a.startswith("--")][:-1] if "--workspace" in shown else shown[4:]
            if len(texts) > 2:
                shown = shown[:6] + [f"… {len(texts) - 2} more entries"] + shown[4 + len(texts):]
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


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null.
    return dict(key=f"evm:{r['id']}", record=r["id"], type=r["type"], on=r["on"], uses=r["uses"], last_used=r["last"])


POLICIES = ("oldest", "lfu", "typed")


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


def list_facts(cli: CLI, pid: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned entries by record id (D-01, C-01, E-12 …), metadata flattened in, plus fact id and text."""
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


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "type", "on", "uses", "last_used", "fid", "body", "rank", "policy",
                                  "evicted_on", "reason", "memory_fact")}


# --------------------------------------------------------------------------- the three policies (plain code, no model)

def days_before(today: str, d: str) -> int:
    return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d")).days


def plan_policies(mem: dict, story: dict) -> dict:
    """Which entries each policy keeps, and why each of the others goes. Reads the metadata `fact list` returned —
    the same numbers any eviction job would have: type, written on, use count, last used."""
    cap, today, window = story["cap"], story["today"], story["event_window_days"]
    rows = list(mem.values())
    plans = {}
    keep = sorted(rows, key=lambda r: (r["on"], r["record"]), reverse=True)[:cap]
    cut = min(r["on"] for r in keep)
    plans["oldest"] = dict(keep=[r["record"] for r in keep], why={
        r["record"]: f"written {r['on']}, before the newest {cap} (cut-off {cut})" for r in rows if r not in keep})
    keep = sorted(rows, key=lambda r: (r["uses"], r["last_used"], r["on"]), reverse=True)[:cap]
    floor = min(r["uses"] for r in keep)
    plans["lfu"] = dict(keep=[r["record"] for r in keep], why={
        r["record"]: f"used {r['uses']}× (the {cap} kept were used ≥ {floor}×)" for r in rows if r not in keep})
    keep, why = [], {}
    for r in sorted(rows, key=lambda r: r["record"]):
        if r["type"] in ("decision", "constraint", "preference"):
            keep.append(r)
        elif r["type"] == "event" and days_before(today, r["last_used"]) <= window:
            keep.append(r)
        elif r["type"] == "event":
            why[r["record"]] = f"event, last used {r['last_used']} ({days_before(today, r['last_used'])} days ago, window {window})"
        else:
            why[r["record"]] = f"chat turn from {r['on']} — turns are archived"
    plans["typed"] = dict(keep=[r["record"] for r in keep], why=why)
    if len(plans["typed"]["keep"]) > cap:
        die(f"typed retention keeps {len(plans['typed']['keep'])} entries, over the cap of {cap}")
    for p in plans.values():
        p["evict"] = sorted(p["why"])
    return plans


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    pids, lines, made = {}, [], 0
    for k, p in story["projects"].items():
        proj = find_project(cli, p["key"], echo=False)
        if proj is None:
            proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]), "--description",
                           p["description"], scoped=True, echo=made == 0)
            made += 1
        pids[k] = proj["id"]
        lines.append(f"    {p['name']:<44} {proj['id']}")
    if made > 1:
        note(f"… and {made - 1} more `proj create` the same way")
    if made < len(pids):
        note(f"{len(pids) - made} project(s) exist from an earlier run (pass --reset to start over)")
    emit("text", "\n" + "\n".join(lines))
    emit("setup", "", projects=pids, names={k: p["name"] for k, p in story["projects"].items()})
    return pids


# --------------------------------------------------------------------------- writing

def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Make the project hold exactly these entries (matched by metadata key): one `fact add` for the missing ones,
    then `fact update --metadata` on each (fact add takes no metadata); entries the project should not hold are
    deleted. Returns key → {id, new}."""
    live = {}
    for f in list_facts(cli, pid, echo=False):
        k = (f.get("metadata") or {}).get("key")
        if k and k not in live:
            live[k] = f
        else:
            cli.run("fact", "delete", "--project", pid, f["id"], scoped=True, echo=False)
    want = {meta["key"] for _, meta in items}
    for k, f in live.items():
        if k not in want:
            cli.run("fact", "delete", "--project", pid, f["id"], scoped=True, echo=False)
    out, missing = {}, []
    for text, meta in items:
        f = live.get(meta["key"])
        if f is None or f.get("fact") != text:
            if f is not None:
                cli.run("fact", "delete", "--project", pid, f["id"], scoped=True, echo=False)
            missing.append((text, meta))
        else:
            if (f.get("metadata") or {}) != meta:
                cli.run("fact", "update", f["id"], "--project", pid, "--metadata", json.dumps(meta), scoped=True,
                        echo=False)
            out[meta["key"]] = dict(id=f["id"], new=False)
    if missing:
        res = cli.run("fact", "add", "--project", pid, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for text, _ in missing:
            if not got.get(text):
                die(f"`fact add` did not return “{clip(text, 60)}”")
        # One `fact update --metadata` per new fact, four at a time (each touches a different fact).
        jobs = [(got[text], meta) for text, meta in missing]
        cli.run("fact", "update", jobs[0][0], "--project", pid, "--metadata", json.dumps(jobs[0][1]), scoped=True, echo=echo)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda j: cli.run("fact", "update", j[0], "--project", pid, "--metadata", json.dumps(j[1]),
                                            scoped=True, echo=False), jobs[1:]))
        for fid, meta in jobs:
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way")
    return out


# --------------------------------------------------------------------------- step 3: intake

def type_counts(rows) -> str:
    c: dict[str, int] = {}
    for r in rows:
        c[r["type"]] = c.get(r["type"], 0) + 1
    return " · ".join(f"{c[t]} {t}{'s' if c[t] > 1 else ''}" for t in ("decision", "constraint", "preference", "event", "turn")
                      if c.get(t))


def intake(cli: CLI, pids: dict, story: dict) -> dict:
    note(f"{story['agent']['name']}'s memory since {story['agent']['since']}: one fact per entry, word for word, then "
         "`fact update --metadata` (type, written on, use count, last used — what an eviction job reads)")
    got = pin(cli, pids["memory"], [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the entries were pinned by an earlier run; reading them back")
    mem = entries(list_facts(cli, pids["memory"]))
    lines = [f"\n  {len(mem)} entries · cap {story['cap']} · {type_counts(mem.values())}",
             f"    {'id':<5} {'type':<11} {'written':<11} {'uses':>4}  {'last used':<11} text"]
    for r in sorted(mem.values(), key=lambda r: r["on"]):
        lines.append(f"    {r['record']:<5} {r['type']:<11} {r['on']:<11} {r['uses']:>4}  {r['last_used']:<11} {clip(r['body'], 60)}")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: r["on"])], cap=story["cap"])
    return mem


# --------------------------------------------------------------------------- step 4: evict

def archive_meta(r: dict, story: dict, why: str, from_pid: str) -> dict:
    return dict(record_meta(dict(id=r["record"], type=r["type"], on=r["on"], uses=r["uses"], last=r["last_used"])),
                evicted_on=story["today"], policy=story["policies"]["typed"]["label"], reason=why,
                from_project=from_pid, memory_fact=r["fid"])


def evict(cli: CLI, pids: dict, story: dict, mem: dict) -> dict:
    plans = plan_policies(mem, story)
    critical = {t["needs"]: t for t in story["tasks"] if not t.get("control")}
    lines = []
    for k in POLICIES:
        pol, p = story["policies"][k], plans[k]
        lines.append(f"\n  {pol['label']} — {pol['rule']}")
        lines.append(f"    keeps {len(p['keep'])} · evicts {len(p['evict'])}: {' '.join(p['evict'])}")
        for rec in p["evict"]:
            if rec in critical:
                lines.append(f"    ✗ evicts {rec} ({mem[rec]['type']}): “{clip(mem[rec]['body'], 70)}” — {p['why'][rec]}")
    emit("text", "\n".join(lines))
    note("each policy's active memory is its own project holding the entries it kept (the same text and metadata)")
    for i, k in enumerate(POLICIES):
        keep = [mem[rec] for rec in plans[k]["keep"]]
        pin(cli, pids[k], [(r["body"], dict(record_meta(dict(id=r["record"], type=r["type"], on=r["on"], uses=r["uses"],
                                                                  last=r["last_used"])), policy=story["policies"][k]["label"]))
                           for r in keep], echo=i == 0)
    note("typed retention moves what it evicts to the cold archive instead of deleting it — each entry with an eviction "
         "record: evicted_on, policy, reason, the project it left, the original fact id")
    arch = [mem[rec] for rec in plans["typed"]["evict"]]
    pin(cli, pids["archive"], [(r["body"], archive_meta(r, story, plans["typed"]["why"][r["record"]], pids["typed"]))
                               for r in arch])
    counts = {k: len(list_facts(cli, pids[k], echo=k == "oldest")) for k in (*POLICIES, "archive")}
    emit("text", "\n  active memory now:  " + " · ".join(f"{story['policies'][k]['label']} {counts[k]}" for k in POLICIES)
         + f"   ·   cold archive {counts['archive']}   (memory before eviction: {len(mem)})")
    emit("evict", "", plans={k: dict(keep=plans[k]["keep"], evict=plans[k]["evict"], why=plans[k]["why"]) for k in POLICIES},
         counts=counts)
    return plans


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, pids: list[str], top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", ",".join(pids), "--types", "fact", "--top-k", str(top_k), scoped=True,
                  echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", type=m.get("type") or "?"))
    return out


def wait_indexed(cli: CLI, pids: dict, story: dict) -> None:
    """`fact list` has new facts at once; `search` gets them a few seconds later. Wait until every active project
    answers with a full top 8 of its own facts before step 5 reads through search."""
    t0 = time.time()
    for k in POLICIES:
        ids = {f["id"] for f in list_facts(cli, pids[k], echo=False)}
        while True:
            hits = search(cli, f"{story['agent']['name']} memory", [pids[k]], echo=False)
            if len([h for h in hits if h["fid"] in ids]) >= 8 or time.time() - t0 > 90:
                break
            time.sleep(5)
    if time.time() - t0 > 3:
        note(f"waited {int(time.time() - t0)} s for the search index to catch up")


def hit_line(h: dict, mark: str = "") -> str:
    return f"        #{h['rank']:<3} {h['record']:<5} {h['type']:<11} {clip(h['body'], 66)}{mark}"


# --------------------------------------------------------------------------- step 5: the same task, three memories

RULE_TYPES = ("decision", "constraint")


def check_one(cli: CLI, pid: str, text: str, needs: str | None, kept: list[str] | None, echo: bool = True) -> dict:
    """What the agent sees for one task in one active memory: the top 8, the rules among them (decisions and
    constraints), and whether the entry the task needs is there."""
    hits = search(cli, text, [pid], echo=echo)
    live = {f["id"] for f in list_facts(cli, pid, echo=False)}
    cached = [h["record"] for h in hits if h["fid"] not in live]
    found = next((h for h in hits if h["record"] == needs), None) if needs else None
    held = needs in kept if (needs and kept is not None) else None
    return dict(hits=[public(h) for h in hits], rules=[h["record"] for h in hits if h["type"] in RULE_TYPES],
                needs=needs, ok=bool(found), rank=found["rank"] if found else None, held=held, cached=cached)


def outcome_text(r: dict) -> str:
    if r["ok"]:
        return f"✓ {r['needs']} at #{r['rank']}"
    if r["held"] is False:
        return f"✗ {r['needs']} evicted"
    return f"✗ {r['needs']} not in the top 8"


def check_tasks(cli: CLI, pids: dict, story: dict, mem: dict, plans: dict) -> list[dict]:
    rows = []
    for t in story["tasks"]:
        need = mem[t["needs"]]
        lines = [f"\n  {t['id']}  “{t['text']}”",
                 f"      needs {t['needs']} ({need['type']}): “{clip(need['body'], 90)}”"
                 + ("   (control: every policy kept it)" if t.get("control") else "")]
        res = {}
        for k in POLICIES:
            r = check_one(cli, pids[k], t["text"], t["needs"], plans[k]["keep"], echo=k == "oldest" and not rows)
            res[k] = r
            lines.append(f"    {story['policies'][k]['label']:<17} {outcome_text(r):<26} rules seen: {' '.join(r['rules']) or '—'}")
            if r["cached"]:
                lines.append(f"      · search answered {', '.join(r['cached'])} from its cache (not in this project's fact list)")
        for k in POLICIES:
            if not res[k]["ok"]:
                lines.append(f"    → under {story['policies'][k]['label']}, {story['agent']['name']} goes ahead: {t['harm']}")
        emit("text", "\n".join(lines))
        if not rows:
            note("the other searches the same way: the task text, `--projects <that policy's active memory>`, top 8")
        row = dict(task=t, results=res)
        emit("task", "", task=t, results=res)
        rows.append(row)
    score = {k: sum(1 for r in rows if r["results"][k]["ok"]) for k in POLICIES}
    lines = ["\n  " + f"{'':<6}" + "".join(f"{story['policies'][k]['label']:<20}" for k in POLICIES)]
    for r in rows:
        lines.append(f"  {r['task']['id']:<6}" + "".join(f"{outcome_text(r['results'][k]):<20}" for k in POLICIES))
    lines.append(f"  {'':<6}" + "".join(f"{str(score[k]) + '/' + str(len(rows)) + ' tasks':<20}" for k in POLICIES))
    emit("text", "\n".join(lines))
    emit("scoreboard", "", score=score, n=len(rows))
    return rows


# --------------------------------------------------------------------------- step 6: the archive

def deep_query(cli: CLI, pids: dict, story: dict, text: str | None = None, needs: str | None = None,
               echo: bool = True) -> dict:
    """The typed active memory alone, then active + cold archive: what was moved out is still there on demand."""
    text = text or story["deep"]["text"]
    needs = needs if text != story["deep"]["text"] or needs else story["deep"]["needs"]
    active = search(cli, text, [pids["typed"]], echo=echo)
    both = search(cli, text, [pids["typed"], pids["archive"]], echo=echo)
    arch_ids = {f["id"] for f in list_facts(cli, pids["archive"], echo=False)}
    for h in both:
        h["where"] = "archive" if h["fid"] in arch_ids else "active"
    found = next((h for h in both if h["record"] == needs), None) if needs else None
    return dict(text=text, needs=needs, active=[public(h) for h in active],
                both=[dict(public(h), where=h["where"]) for h in both],
                in_active=any(h["record"] == needs for h in active) if needs else None,
                found=dict(public(found), where=found["where"]) if found else None)


def eviction_log(cli: CLI, pids: dict, echo: bool = True) -> list[dict]:
    facts = entries(list_facts(cli, pids["archive"], echo=echo))
    return [public(r) for r in sorted(facts.values(), key=lambda r: r["record"])]


def archive_step(cli: CLI, pids: dict, story: dict) -> dict:
    d = deep_query(cli, pids, story)
    lines = [f"\n  “{d['text']}”", "    typed active memory only:"]
    lines += [hit_line(h) for h in d["active"][:3]]
    lines.append(f"      → {d['needs']} {'is' if d['in_active'] else 'is not'} in the active memory")
    lines.append("    active + cold archive (`--projects active,archive`):")
    lines += [hit_line(h, "   ← archive" if h["where"] == "archive" else "") for h in d["both"][:3]]
    f = d["found"]
    if f:
        lines.append(f"      → {f['record']} at #{f['rank']} from the {f['where']}"
                     + (f": evicted {f['evicted_on']} by {f['policy']} — {f['reason']}" if f.get("evicted_on") else ""))
    else:
        lines.append(f"      → {d['needs']} not in the top 8")
    log = eviction_log(cli, pids)
    by_reason: dict[str, list[str]] = {}
    for r in log:
        by_reason.setdefault("chat turn" if r["type"] == "turn" else "event outside the window", []).append(r["record"])
    lines.append(f"\n  eviction log — {len(log)} entries in the cold archive, none deleted:")
    for k, v in by_reason.items():
        lines.append(f"    {k:<26} {len(v):>2}   {' '.join(v)}")
    emit("text", "\n".join(lines))
    emit("archive", "", deep=d, log=log)
    return dict(deep=d, log=log)


# --------------------------------------------------------------------------- read-only commands

def need_projects(cli: CLI) -> dict:
    story = load_story()
    pids = {}
    for k, p in story["projects"].items():
        proj = find_project(cli, p["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        pids[k] = proj["id"]
    return pids


def check_text(cli: CLI, pids: dict, text: str, needs: str | None = None, echo: bool = True) -> dict:
    """Any task against the three active memories: the top 8 each one returns and the rules among them."""
    story = load_story()
    kept = {k: [r for r in entries(list_facts(cli, pids[k], echo=False))] for k in POLICIES}
    res = {k: check_one(cli, pids[k], text, needs, kept[k], echo=echo and k == "oldest") for k in POLICIES}
    return dict(text=text, needs=needs, results=res, labels={k: story["policies"][k]["label"] for k in POLICIES})


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    n_proj = n_facts = 0
    for k, p in story["projects"].items():
        proj = find_project(cli, p["key"], echo=not quiet and n_proj == 0)
        if not proj:
            continue
        facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False),
                          facts))
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on {p['name']} after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet and n_proj == 0)
        n_proj += 1
        n_facts += len(facts)
    note(f"deleted {n_proj} project(s) and their {n_facts} facts" if n_proj else "nothing to delete")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, f"Set up — {story['agent']['name']}'s memory, one active memory per policy, a cold archive")
    pids = setup(cli, story, reset)
    cli.pids = pids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, f"Six months of memory — {len(story['records'])} entries, cap {story['cap']}")
    mem = intake(cli, pids, story)
    banner(4, TOTAL, "Evict — drop-oldest, drop-least-used, typed retention")
    plans = evict(cli, pids, story, mem)
    wait_indexed(cli, pids, story)
    banner(5, TOTAL, "The same three tasks against each active memory")
    rows = check_tasks(cli, pids, story, mem, plans)
    banner(6, TOTAL, "The cold archive — moved out, not deleted")
    arch = archive_step(cli, pids, story)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   evict={k: plans[k]["evict"] for k in POLICIES},
                   tasks={r["task"]["id"]: {k: [r["results"][k]["ok"], r["results"][k]["rank"], r["results"][k]["rules"]]
                                            for k in POLICIES} for r in rows},
                   cached=sorted({c for r in rows for k in POLICIES for c in r["results"][k]["cached"]}),
                   deep=[arch["deep"]["in_active"], (arch["deep"]["found"] or {}).get("rank"),
                         (arch["deep"]["found"] or {}).get("where")], archived=len(arch["log"]))
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "check", "evictions", "cleanup"],
                    help="run = full demo (default); check \"TASK\" [--needs D-01] = any task against the three active "
                         "memories; evictions = the cold archive's eviction log; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the task text (check)")
    ap.add_argument("--needs", help="check: the entry the task needs (e.g. C-01), to mark it in each result")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "check" and len(a.args) != 1:
        ap.error("check needs one argument, e.g. check \"Deploy the rates service on Friday at 16:00 UTC\" --needs D-05")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py check \"Deploy the rates service on Friday at 16:00 UTC\" --needs D-05`, "
              "`python3 demo.py evictions`; `python3 demo.py cleanup` removes the demo data.")
        return
    pids = need_projects(cli)
    if a.command == "check":
        banner(2, 2, "Check — one task against the three active memories")
        r = check_text(cli, pids, a.args[0], a.needs)
        lines = [f"\n  “{r['text']}”" + (f" · needs {r['needs']}" if r["needs"] else "")]
        for k in POLICIES:
            x = r["results"][k]
            head = outcome_text(x) if r["needs"] else f"{len(x['hits'])} hits"
            lines.append(f"    {r['labels'][k]:<17} {head:<26} rules seen: {' '.join(x['rules']) or '—'}")
            lines += [hit_line(dict(h, rank=h["rank"], type=h["type"] or "?"),
                               "   ← needed" if h["record"] == r["needs"] else "") for h in x["hits"][:3]]
            if x["cached"]:
                lines.append(f"      · search answered {', '.join(x['cached'])} from its cache")
        emit("text", "\n".join(lines))
    elif a.command == "evictions":
        banner(2, 2, "Evictions — the cold archive's eviction log")
        log = eviction_log(cli, pids)
        lines = [f"\n  {len(log)} entries moved out of {story['projects']['typed']['name']}, none deleted:",
                 f"    {'id':<5} {'type':<9} {'evicted':<11} {'policy':<16} reason"]
        for r in log:
            lines.append(f"    {r['record']:<5} {r['type']:<9} {r['evicted_on'] or '?':<11} {r['policy'] or '?':<16} {r['reason']}")
        lines.append("\n  each entry also records the project it left and the original fact id (metadata.from_project, "
                     "metadata.memory_fact)")
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

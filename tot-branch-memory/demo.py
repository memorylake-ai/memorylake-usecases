#!/usr/bin/env python3
"""
Branched memory for a Tree-of-Thoughts agent — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Outfitters (fictional) has a planning bot, Pathfinder, that works the
Tree-of-Thoughts way: for a task it opens several branches, explores them in
parallel, keeps the one that works and prunes the rest. On 2026-10-01 the task
is "checkout p95 is 610 ms, get it below 300 ms". Pathfinder opens three
branches: a price cache, a bigger connection pool, a new index.

- Every branch is its own **project** (branch-local memory) with its own
  conversation. The step-by-step reasoning goes into **THINKING** blocks:
  MemoryLake stores them and replays them verbatim, but does not turn them
  into memory. The measured result is pinned on the branch as one fact.
- The three explorers write to one shared **trunk log** at the same time. A
  conversation is a linear log: an append whose parent is no longer the head
  is refused with 409 "not the current head", and the writer re-reads
  `current_message_id` and retries.
- "Checking out" a branch is a search over trunk + that branch. Merging
  promotes the winner's result, and the losers' "why pruned" lessons, to the
  trunk. Rolling back a dead branch is `proj delete`: its memory is gone.
- A week later the task is "search p95 is 540 ms". Pathfinder reads the trunk
  first, skips the pruned branches (citing the fact that says why) and opens
  one branch instead of three.

The planner's choices are scripted (no LLM call): what the demo shows is the
memory around it. Everything here is a plain CLI command, echoed as it runs.
The companion web app (web/server.py) drives the same functions and streams
the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-tot"  # memorylake-usecases / tot-branch-memory

COOK_WAIT = 300      # upper bound for the conversations to be processed (usually 30–90 s)
APPEND_TRIES = 12    # a trunk-log append retried this many times on 409 before giving up


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
    elif kind in ("text", "json", "progress", "race"):
        print(text)


EMIT = _print_sink
_EMIT_LOCK = threading.Lock()   # the explorers run in threads; keep each line whole


def emit(kind: str, text: str = "", **data) -> None:
    with _EMIT_LOCK:
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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504")

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
        retryable = any(str(a) in self.READ_ONLY for a in args[:3])
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
        items = (cli.run("ws", "list", "--page-size", "1") or {}).get("items") or []
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
    return json.loads((DATA / "tot.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def run_of(story: dict, run_id: str) -> dict:
    return next(r for r in story["runs"] if r["run"] == run_id)


def branch_slug(run: dict, b: dict) -> str:
    return f"br-{run['run']}-{b['key']}"


def branch_name(run: dict, b: dict) -> str:
    return f"{run['run']}/{b['key']}"


def all_branches(story: dict) -> list[tuple[dict, dict]]:
    return [(r, b) for r in story["runs"] for b in r["branches"]]


def speaker(person: dict, text: str) -> str:
    return f"{person['name']} ({person['role']}): {text}"


# --------------------------------------------------------------------------- lookups

def _pages(cli: CLI, args: list[str], echo: bool = True, scoped: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        page = cli.run(*args, "--page-size", "100", *(["--continuation-token", token] if token else []),
                       scoped=scoped, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def _get(cli: CLI, *args: str, scoped: bool = False, echo: bool = True):
    try:
        return cli.run(*args, scoped=scoped, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_project(cli: CLI, slug: str, echo: bool = True):
    return _get(cli, "proj", "get", cid(slug), "--by-custom-id", scoped=True, echo=echo)


def find_actor(cli: CLI, slug: str, echo: bool = True):
    return _get(cli, "actor", "get", cid(slug), "--by-custom-id", echo=echo)


def find_conv(cli: CLI, slug: str, echo: bool = True):
    return _get(cli, "conv", "get", cid(slug), "--by-custom-id", scoped=True, echo=echo)


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "list", flag, scope_id], echo)


def list_messages(cli: CLI, conv_id: str, echo: bool = True) -> list[dict]:
    msgs = _pages(cli, ["conv", "msg", "list", conv_id], echo, scoped=False)
    return sorted(msgs, key=lambda m: m.get("sequence_no") or 0)


def blocks(m: dict, kind: str) -> list[str]:
    return [b.get("text") or "" for b in m.get("content") or [] if b.get("block_type") == kind]


def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`, but the server still honours the type.
    # Pass it whenever the installed CLI still accepts it.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    if not cli.has_actor_type and actor_type != "HUMAN":  # type: ignore[attr-defined]
        note(f"this CLI cannot set an actor type, so this {actor_type} actor is created untyped (server default HUMAN)")
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- step 2: setup

def ensure_project(cli: CLI, slug: str, name: str, description: str) -> tuple[str, bool]:
    proj = find_project(cli, slug)
    if proj is not None:
        return proj["id"], False
    proj = cli.run("proj", "create", "--name", name, "--custom-id", cid(slug), "--description", description, scoped=True)
    return proj["id"], True


def ensure_actor(cli: CLI, person: dict, actor_type: str = "HUMAN") -> str:
    actor = find_actor(cli, person["slug"])
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(person["slug"]), "--display-name", person["name"],
                        "--description", person["description"], *actor_type_args(cli, actor_type))
        note(f"created {person['name']} → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"]


def ensure_trunk_conv(cli: CLI, ids: dict) -> str:
    conv = find_conv(cli, "trunk-log")
    if conv is None:
        conv = cli.run("conv", "create", "--project", ids["trunk"], "--name", "Pathfinder trunk log", "--kind", "DIRECT",
                       "--actors", f"{ids['priya']},{ids['pathfinder']}", "--custom-id", cid("trunk-log"), scoped=True)
        note(f"created the trunk log conversation → {conv['id']}")
    return conv["id"]


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything this demo created, then starting over")
        cleanup(cli, quiet=True)
    t = story["trunk"]
    trunk, created = ensure_project(cli, t["slug"], t["name"], t["description"])
    note(f"{'created' if created else 'found'} the trunk project “{t['name']}” → {trunk}"
         + ("" if created else " (pass --reset to start over)"))
    ids = dict(trunk=trunk, priya=ensure_actor(cli, story["human"]),
               pathfinder=ensure_actor(cli, story["planner"], "ASSISTANT"))
    ids["log"] = ensure_trunk_conv(cli, ids)
    emit("text", f"\n  trunk project  {trunk} (tasks, merged results, pruned-branch lessons)"
                 f"\n  trunk log      {ids['log']} (one conversation every explorer writes to)"
                 f"\n  Priya Nair     {ids['priya']} (staff engineer)"
                 f"\n  Pathfinder     {ids['pathfinder']} (the Tree-of-Thoughts planner)")
    emit("setup", "", ids=ids, trunk=t, human=story["human"], planner=story["planner"])
    return ids


# --------------------------------------------------------------------------- the trunk log: compare-and-swap on the head

def log_entries(cli: CLI, ids: dict, echo: bool = True) -> list[dict]:
    return list_messages(cli, ids["log"], echo)


def entry_key(m: dict) -> str | None:
    return (m.get("metadata") or {}).get("entry")


class RaceStats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.appends = 0
        self.conflicts = 0
        self.events: list[dict] = []

    def add(self, ok: bool, ev: dict | None = None) -> None:
        with self.lock:
            if ok:
                self.appends += 1
            else:
                self.conflicts += 1
            if ev:
                self.events.append(ev)


def append_to_log(cli: CLI, ids: dict, who: str, key: str, content: list[dict], ts: str, meta: dict,
                  stats: RaceStats | None = None) -> dict:
    """Append to the shared trunk log. Without --parent the CLI looks the conversation's latest message up and
    sends it as the parent; the server only accepts an append that extends the current head. A writer that lost
    the race gets 409, re-reads `current_message_id` and retries on top of it."""
    parent = None
    for attempt in range(1, APPEND_TRIES + 1):
        args = ["conv", "msg", "append", ids["log"], "--actor", ids[who], "--custom-id", key, "--timestamp", ts,
                "--content-json", json.dumps(content), "--metadata", f"entry={key}"]
        for k, v in meta.items():
            args += ["--metadata", f"{k}={v}"]
        if parent:
            args += ["--parent", parent]
        try:
            msg = cli.run(*args, scoped=True, echo=attempt == 1)
            if stats:
                stats.add(True)
            if attempt > 1:
                emit("race", f"  ✓ {key} landed on attempt {attempt}", key=key, attempt=attempt, ok=True)
            return msg
        except CLIError as e:
            if not e.has("not the current head", "409"):
                raise
            head = (cli.run("conv", "get", ids["log"], scoped=True, echo=False) or {}).get("current_message_id")
            m = re.search(r"'(conv-entry-[0-9a-f]+)' is not the current head", e.stderr + e.stdout)
            ev = dict(key=key, attempt=attempt, ok=False, stale=m.group(1) if m else parent, head=head)
            if stats:
                stats.add(False, ev)
            emit("race", f"  409 {key}: parent {ev['stale'] or '(latest when sent)'} is not the current head "
                         f"→ head is now {head}, retrying on top of it", **ev)
            parent = head
            time.sleep(random.uniform(0.1, 0.6))
    die(f"{key}: still not the head after {APPEND_TRIES} tries")


# --------------------------------------------------------------------------- step 3: the task

def append_task(cli: CLI, ids: dict, story: dict, run: dict) -> bool:
    key = f"{run['run']}-task"
    if any(entry_key(m) == key for m in log_entries(cli, ids, echo=False)):
        note(f"{run['run']}'s task was written to the trunk log by an earlier run")
        return False
    append_to_log(cli, ids, "priya", key, [{"block_type": "TEXT", "text": speaker(story["human"], run["task"])}],
                  f"{run['date']}T09:00:00Z", dict(event="task", run=run["run"]))
    return True


def task_step(cli: CLI, ids: dict, story: dict) -> dict:
    r1 = story["runs"][0]
    note("Priya writes the task into the trunk log: a TEXT message, so MemoryLake extracts it into trunk memory")
    new = append_task(cli, ids, story, r1)
    emit("text", f"\n  {r1['run']} · {r1['date']} · {r1['service']}\n  “{r1['task']}”"
                 f"\n\n  candidate branches: " + ", ".join(f"{k} ({story['approaches'][k]})" for k in r1["candidates"]))
    emit("task", "", run=r1["run"], date=r1["date"], service=r1["service"], task=r1["task"], new=new,
         candidates=[dict(key=k, approach=story["approaches"][k]) for k in r1["candidates"]])
    return dict(new=new)


# --------------------------------------------------------------------------- step 4: expand branches in parallel

def merged_on_trunk(cli: CLI, ids: dict, echo: bool = False) -> dict[str, dict]:
    """Trunk facts written by a merge (winner or pruned lesson), keyed by branch name (r1/pool)."""
    out = {}
    for f in list_facts(cli, "--projects", ids["trunk"], echo):
        m = f.get("metadata") or {}
        if m.get("type") in ("merged", "pruned") and m.get("branch"):
            out[m["branch"]] = f
    return out


def result_text(story: dict, run: dict, b: dict) -> str:
    return (f"Branch result ({branch_name(run, b)}, {run['date']}, {run['service']}): {story['approaches'][b['approach']]} — "
            f"p95 {b['p95_ms']} ms against a {run['target_ms']} ms target. Verdict: {b['verdict']}, because {b['reason']}.")


def branch_messages(story: dict, run: dict, b: dict) -> list[tuple[str, list[dict]]]:
    first, second = b["thoughts"]
    return [(f"{run['date']}T09:{10 + 10 * run['branches'].index(b):02d}:00Z",
             [{"block_type": "THINKING", "text": first},
              {"block_type": "TEXT", "text": speaker(story["planner"], f"Exploring branch {b['key']}: {story['approaches'][b['approach']]}.")}]),
            (f"{run['date']}T11:{10 + 10 * run['branches'].index(b):02d}:00Z",
             [{"block_type": "THINKING", "text": second},
              {"block_type": "TEXT", "text": speaker(story["planner"], f"Branch {b['key']} measured: p95 {b['p95_ms']} ms. Verdict: {b['verdict']}.")}])]


def explore(cli: CLI, ids: dict, story: dict, run: dict, b: dict, stats: RaceStats, gate: threading.Barrier | None,
            existing_log: set[str]) -> dict:
    """One explorer: open the branch in the trunk log, give it a project and a conversation, think in THINKING
    blocks, pin the measured result on the branch, report back to the trunk log."""
    name, slug = branch_name(run, b), branch_slug(run, b)
    out = dict(branch=name, key=b["key"], run=run["run"], approach=b["approach"])

    def sync():
        if gate is not None:
            try:
                gate.wait(timeout=120)
            except threading.BrokenBarrierError:
                pass

    sync()  # all explorers open their branch in the trunk log at the same moment
    if f"{run['run']}-{b['key']}-open" not in existing_log:
        append_to_log(cli, ids, "pathfinder", f"{run['run']}-{b['key']}-open",
                      [{"block_type": "THINKING", "text": f"Pathfinder opens branch {name}: {story['approaches'][b['approach']]}."}],
                      f"{run['date']}T09:05:00Z", dict(event="open", run=run["run"], branch=name), stats)
    pid, created = ensure_project(cli, slug, f"Larkspur — branch {name}",
                                  f"Branch-local memory for {name} ({story['approaches'][b['approach']]}). Demo data.")
    out["project"] = pid
    conv = find_conv(cli, f"conv-{run['run']}-{b['key']}", echo=False)
    if conv is None:
        conv = cli.run("conv", "create", "--project", pid, "--name", f"Branch {name}", "--kind", "DIRECT",
                       "--actors", f"{ids['priya']},{ids['pathfinder']}", "--custom-id", cid(f"conv-{run['run']}-{b['key']}"),
                       "--metadata", f"branch={name}", scoped=True)
    out["conv"] = conv["id"]
    have = len(list_messages(cli, conv["id"], echo=False))
    appended = 0
    for i, (ts, content) in enumerate(branch_messages(story, run, b)[have:], start=have):
        cli.run("conv", "msg", "append", conv["id"], "--actor", ids["pathfinder"], "--custom-id", f"{b['key']}-m{i + 1}",
                "--timestamp", ts, "--content-json", json.dumps(content), scoped=True, echo=appended == 0)
        appended += 1
    out["appended"] = appended
    text = result_text(story, run, b)
    pinned = next((f for f in list_facts(cli, "--projects", pid, echo=False) if f.get("fact") == text), None)
    if pinned is None:
        res = cli.run("fact", "add", "--project", pid, text, scoped=True)
        fid = ((res or {}).get("facts") or [{}])[0].get("id")
        cli.run("fact", "update", fid, "--project", pid, "--metadata", json.dumps(dict(
            type="branch-result", run=run["run"], branch=name, approach=b["approach"], p95_ms=str(b["p95_ms"]),
            target_ms=str(run["target_ms"]), verdict=b["verdict"], reason=b["reason"])), scoped=True)
    else:
        fid = pinned["id"]
    out["result_fact"] = fid
    sync()  # and report back at the same moment, too
    if f"{run['run']}-{b['key']}-result" not in existing_log:
        append_to_log(cli, ids, "pathfinder", f"{run['run']}-{b['key']}-result",
                      [{"block_type": "THINKING", "text": f"Branch {name}: p95 {b['p95_ms']} ms (target {run['target_ms']} ms) → {b['verdict']}."}],
                      f"{run['date']}T11:45:00Z", dict(event="result", run=run["run"], branch=name, p95_ms=b["p95_ms"],
                                                      verdict=b["verdict"]), stats)
    emit("branch", "", **out, p95_ms=b["p95_ms"], verdict=b["verdict"], reason=b["reason"], fact=text,
         thoughts=b["thoughts"], created=created)
    return out


def wait_cooked(cli: CLI, convs: dict[str, str]) -> int:
    pending = dict(convs)
    if not pending:
        return 0
    note(f"waiting for MemoryLake to process {len(pending)} conversation(s) (they cook in parallel)")
    t0, first = time.time(), True
    while pending and time.time() - t0 < COOK_WAIT:
        for conv_id in list(pending):
            try:
                st = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first) or {}
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                st = {}
            first = False
            if st.get("cook_finished"):
                pending.pop(conv_id)
        if pending:
            time.sleep(5)
    waited = int(time.time() - t0)
    if pending:
        note(f"still processing after {waited}s: {', '.join(pending.values())} — the demo goes on; facts arrive later")
    else:
        note(f"processed in {waited}s")
    return waited


def expand(cli: CLI, ids: dict, story: dict, run: dict) -> dict:
    merged = merged_on_trunk(cli, ids)
    todo = [b for b in run["branches"] if branch_name(run, b) not in merged]
    done = [b for b in run["branches"] if branch_name(run, b) in merged]
    for b in done:
        f = merged[branch_name(run, b)]
        note(f"branch {branch_name(run, b)} was merged into the trunk by an earlier run ({(f.get('metadata') or {}).get('type')}) "
             f"→ {f['id']}")
        emit("branch", "", branch=branch_name(run, b), key=b["key"], run=run["run"], approach=b["approach"], p95_ms=b["p95_ms"],
             verdict=b["verdict"], reason=b["reason"], fact=result_text(story, run, b), thoughts=b["thoughts"],
             merged_earlier=True, project=(f.get("metadata") or {}).get("from_project"))
    stats = RaceStats()
    existing = {entry_key(m) for m in log_entries(cli, ids, echo=False)}
    results: list[dict] = []
    if todo:
        if len(todo) > 1:
            note(f"{len(todo)} explorers start at once; each one writes to the shared trunk log without a --parent "
                 "(the CLI looks the head up and sends it as the parent), so they race for the head")
        else:
            note("one explorer: it writes to the trunk log with nobody to race against")
        gate = threading.Barrier(len(todo)) if len(todo) > 1 else None
        errors: list[BaseException] = []

        def worker(b):
            try:
                results.append(explore(cli, ids, story, run, b, stats, gate, existing))
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
                if gate is not None:
                    gate.abort()

        threads = [threading.Thread(target=worker, args=(b,), name=b["key"]) for b in todo]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
    convs = {r["conv"]: r["branch"] for r in results if r.get("appended")}
    if any(r.get("appended") for r in results) or todo:
        convs[ids["log"]] = "trunk log"
    waited = wait_cooked(cli, convs)
    emit("race_stats", "", appends=stats.appends, conflicts=stats.conflicts, events=stats.events, run=run["run"])
    order = {b["key"]: i for i, b in enumerate(run["branches"])}
    results.sort(key=lambda r: order[r["key"]])
    lines = [f"\n  {run['run']}: {len(run['branches'])} branch(es)"]
    for b in run["branches"]:
        r = next((x for x in results if x["key"] == b["key"]), None)
        where = f"project {r['project']} · result {r['result_fact']}" if r else "merged into the trunk earlier"
        lines.append(f"    {branch_name(run, b):<16} p95 {b['p95_ms']:>4} ms  {b['verdict']:<7} {where}")
    if todo:
        lines.append(f"\n  trunk log: {stats.appends} append(s) landed, {stats.conflicts} refused with 409 and retried")
    else:
        lines.append("\n  every branch was explored and merged by an earlier run; the trunk log (`python3 demo.py log`) is its record")
    emit("text", "\n".join(lines))
    return dict(results=results, appends=stats.appends, conflicts=stats.conflicts, cook_seconds=waited, fresh=bool(todo))


# --------------------------------------------------------------------------- step 5: one linear log

def show_log(cli: CLI, ids: dict, story: dict, check_stale: bool = True) -> dict:
    msgs = log_entries(cli, ids)
    seqs = [m.get("sequence_no") for m in msgs]
    keys = [entry_key(m) for m in msgs]
    contiguous = seqs == list(range(1, len(msgs) + 1))
    unique = len(set(keys)) == len(keys)
    rows = []
    lines = [f"\n  The trunk log (`conv msg list {ids['log']}`), in the order the server accepted it:"]
    for m in msgs:
        md = m.get("metadata") or {}
        kinds = "+".join(b.get("block_type") for b in m.get("content") or [])
        text = (blocks(m, "THINKING") or blocks(m, "TEXT") or [""])[0]
        rows.append(dict(seq=m.get("sequence_no"), id=m.get("id"), ts=m.get("timestamp"), event=md.get("event"),
                         branch=md.get("branch"), run=md.get("run"), kinds=kinds, text=text, key=md.get("entry")))
        lines.append(f"    #{m.get('sequence_no'):<3} {str(m.get('timestamp'))[:16]}  {md.get('event', ''):<6} "
                     f"{md.get('branch') or md.get('run') or '':<14} {kinds:<13} {clip(text, 58)}")
    lines.append(f"\n  {'✓' if contiguous else '✗'} sequence_no 1..{len(msgs)} with no gaps   "
                 f"{'✓' if unique else '✗'} every entry exactly once ({len(set(keys))} keys)")
    out = dict(rows=rows, contiguous=contiguous, unique=unique, head=None, stale=None)
    head = (cli.run("conv", "get", ids["log"], scoped=True) or {}).get("current_message_id")
    out["head"] = head
    lines.append(f"  head (`conv get` → current_message_id): {head}")
    if check_stale and len(msgs) > 1:
        stale = msgs[0]["id"]
        note(f"now a writer with an old view of the log: append on top of #1 ({stale}), which is no longer the head")
        rc, so, se = cli.raw("conv", "msg", "append", ids["log"], "--actor", ids["pathfinder"], "--custom-id",
                             f"stale-{int(time.time())}", "--parent", stale, "--content-json",
                             json.dumps([{"block_type": "THINKING", "text": "A write based on a stale view of the log."}]))
        refused = rc != 0 and "not the current head" in (se + so)
        detail = re.search(r'"detail":"([^"]+)"', se + so)
        out["stale"] = dict(parent=stale, refused=refused, rc=rc, detail=detail.group(1) if detail else clip(se or so, 200))
        lines.append(f"  {'✓ refused' if refused else '✗ accepted'} (rc={rc}): {clip(out['stale']['detail'], 150)}")
        if not refused and rc == 0:
            lines.append("    (the server accepted a stale parent; this demo expected 409)")
    emit("text", "\n".join(lines))
    emit("log", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: reasoning is stored, not remembered

def secret_hits(secret: str, texts: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    alts = [a.lower() for a in secret.split("|")]
    return [t for t in texts if any(a in t[2].lower() for a in alts)]


def all_memory(cli: CLI, ids: dict, story: dict, echo: bool = True) -> list[tuple[str, str, str]]:
    """(scope, fact id, text) for every scope the demo writes to."""
    out = [("trunk", f.get("id"), f.get("fact") or "") for f in list_facts(cli, "--projects", ids["trunk"], echo)]
    for run, b in all_branches(story):
        p = find_project(cli, branch_slug(run, b), echo=False)
        if p:
            out += [(branch_name(run, b), f.get("id"), f.get("fact") or "") for f in list_facts(cli, "--projects", p["id"], echo)]
    for who in ("priya", "pathfinder"):
        out += [(who, f.get("id"), f.get("fact") or "") for f in list_facts(cli, "--actors", ids[who], echo)]
    return out


def reasoning_check(cli: CLI, ids: dict, story: dict, runs: list[str]) -> dict:
    memory = all_memory(cli, ids, story)
    lines, branches = [], []
    n_ok = n_all = v_ok = v_all = 0
    for run, b in all_branches(story):
        if run["run"] not in runs:
            continue
        conv = find_conv(cli, f"conv-{run['run']}-{b['key']}", echo=False)
        if conv is None:
            lines.append(f"\n  {branch_name(run, b)}: rolled back — its conversation and project were deleted, so there is nothing to replay")
            continue
        msgs = list_messages(cli, conv["id"])
        stored = [t for m in msgs for t in blocks(m, "THINKING")]
        verbatim = [any(sha(s) == sha(t) for s in stored) for t in b["thoughts"]]
        v_ok += sum(verbatim)
        v_all += len(verbatim)
        branch_facts = [x for x in memory if x[0] == branch_name(run, b)]
        checks = []
        for sec in b["secrets"]:
            hits = secret_hits(sec, memory)
            checks.append(dict(detail=sec.split("|")[0], leaked=[dict(scope=h[0], id=h[1], text=h[2]) for h in hits]))
            n_all += 1
            n_ok += not hits
        branches.append(dict(branch=branch_name(run, b), conv=conv["id"], thoughts=b["thoughts"], stored=len(stored),
                             verbatim=verbatim, checks=checks, memory=[dict(id=x[1], text=x[2]) for x in branch_facts]))
        lines.append(f"\n  {branch_name(run, b)}  ({conv['id']})")
        for i, t in enumerate(b["thoughts"]):
            lines.append(f"    THINKING {i + 1}  {'✓ replayed verbatim (sha256 ' + sha(t)[:12] + ')' if verbatim[i] else '✗ not found as sent'}")
            lines.append(f"               “{clip(t, 96)}”")
        lines.append("    reasoning-only details, checked against every scope's `fact list`:")
        lines.append("      " + "   ".join(f"{'✓' if not c['leaked'] else '✗'} {c['detail']}" for c in checks))
        for c in checks:
            for h in c["leaked"]:
                lines.append(f"      ✗ “{c['detail']}” is in {h['scope']} memory: {clip(h['text'], 90)} ({h['id']})")
        lines.append(f"    what the branch remembers ({len(branch_facts)} fact(s)):")
        lines += [f"      · {clip(x[2], 104)}" for x in branch_facts]
    lines.insert(0, f"\n  {v_ok}/{v_all} THINKING blocks replayed verbatim · {n_ok}/{n_all} reasoning-only details "
                    f"kept out of memory (scopes: trunk, every branch, Priya, Pathfinder)")
    emit("text", "\n".join(lines))
    out = dict(branches=branches, kept_out=n_ok, details=n_all, verbatim=v_ok, thoughts=v_all, runs=runs)
    emit("reasoning", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: checkout = trunk + one branch

def search_facts(cli: CLI, query: str, projects: list[str], echo: bool = True) -> list[dict]:
    res = cli.run("search", query, "--projects", ",".join(projects), "--types", "fact", "--top-k", "20",
                  scoped=True, echo=echo) or {}
    return res.get("facts") or []


def checkout(cli: CLI, ids: dict, story: dict, run: dict) -> dict:
    q = f"what did each branch measure for {run['service']} p95 latency"
    base = search_facts(cli, q, [ids["trunk"]])
    views = []
    lines = [f"\n  `search \"{q}\" --projects <trunk>` → {len(base)} hit(s), "
             f"{sum(1 for h in base if (h.get('content') or h.get('fact') or '').startswith('Branch result'))} branch result(s)"]
    for b in run["branches"]:
        p = find_project(cli, branch_slug(run, b), echo=False)
        if p is None:
            continue
        mine = {f["id"] for f in list_facts(cli, "--projects", p["id"], echo=False)}
        hits = search_facts(cli, q, [ids["trunk"], p["id"]])
        own = [h for h in hits if h.get("id") in mine]
        in_base = [h for h in base if h.get("id") in mine]
        views.append(dict(branch=branch_name(run, b), project=p["id"], hits=len(hits), own=[dict(id=h.get("id"),
                     text=h.get("content") or h.get("fact")) for h in own], in_trunk_only=len(in_base)))
        lines.append(f"  `--projects <trunk>,<{branch_name(run, b)}>` → {len(hits)} hit(s); {len(own)} from the branch "
                     f"(trunk alone: {len(in_base)})  {'✓' if own and not in_base else '✗'}")
        for h in own[:2]:
            lines.append(f"      · {clip(h.get('content') or h.get('fact') or '', 104)}")
    emit("text", "\n".join(lines))
    out = dict(query=q, trunk_hits=len(base), views=views)
    emit("checkout", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: merge + rollback

def merge_text(story: dict, run: dict, b: dict) -> str:
    a = story["approaches"][b["approach"]]
    if b["verdict"] == "merged":
        return (f"Merged from branch {branch_name(run, b)} ({run['date']}, {run['service']}): {a} — "
                f"{run['service']} p95 {b['p95_ms']} ms against a {run['target_ms']} ms target; {b['reason']}.")
    return (f"Pruned branch {branch_name(run, b)} ({run['date']}, {run['service']}): {a} — do not retry: {b['reason']}.")


def merge_branch(cli: CLI, ids: dict, story: dict, run: dict, b: dict, merged: dict) -> dict:
    name = branch_name(run, b)
    if name in merged:
        f = merged[name]
        return dict(branch=name, id=f["id"], text=f.get("fact"), kind=(f.get("metadata") or {}).get("type"), new=False,
                    project=(f.get("metadata") or {}).get("from_project"))
    p = find_project(cli, branch_slug(run, b), echo=False)
    if p is None:
        die(f"branch {name} has neither a project nor a trunk merge; run with --reset")
    result = next((f for f in list_facts(cli, "--projects", p["id"], echo=False)
                   if (f.get("metadata") or {}).get("type") == "branch-result"), None)
    text = merge_text(story, run, b)
    res = cli.run("fact", "add", "--project", ids["trunk"], text, scoped=True)
    fid = ((res or {}).get("facts") or [{}])[0].get("id")
    kind = "merged" if b["verdict"] == "merged" else "pruned"
    cli.run("fact", "update", fid, "--project", ids["trunk"], "--metadata", json.dumps(dict(
        type=kind, run=run["run"], branch=name, approach=b["approach"], service=run["service"], p95_ms=str(b["p95_ms"]),
        reason=b["reason"], from_project=p["id"], from_fact=(result or {}).get("id") or "")), scoped=True)
    return dict(branch=name, id=fid, text=text, kind=kind, new=True, project=p["id"], from_fact=(result or {}).get("id"))


def rollback(cli: CLI, run: dict, b: dict, project_id: str | None) -> dict:
    name = branch_name(run, b)
    conv = find_conv(cli, f"conv-{run['run']}-{b['key']}", echo=False)
    if conv:
        cli.run("conv", "delete", conv["id"], scoped=True)
    p = find_project(cli, branch_slug(run, b), echo=False)
    deleted = False
    if p:
        cli.run("proj", "delete", p["id"], scoped=True)
        project_id, deleted = p["id"], True
    gone = None
    if project_id:
        rc, so, se = cli.raw("fact", "list", "--projects", project_id, "--workspace", cli.workspace or "")
        gone = rc != 0 and ("not found" in (se + so).lower() or "404" in (se + so))
    return dict(branch=name, project=project_id, deleted_now=deleted, gone=gone)


def merge_step(cli: CLI, ids: dict, story: dict, run: dict) -> dict:
    merged = merged_on_trunk(cli, ids, echo=True)
    note("merge = promote to the trunk: the winner's result, and each pruned branch's reason, as one trunk fact each "
         "(`fact add` + `fact update --metadata` with the branch, its project and its result fact)")
    merges = [merge_branch(cli, ids, story, run, b, merged) for b in run["branches"]]
    note("rollback = `proj delete` on each pruned branch (its conversation first: `proj delete` does not delete conversations)")
    rolled = [rollback(cli, run, b, m["project"]) for b, m in zip(run["branches"], merges) if m["kind"] == "pruned"]
    lines = ["\n  Merged into the trunk:"]
    for m in merges:
        lines.append(f"    {'+' if m['new'] else '='} {m['kind']:<6} {m['id']}  {clip(m['text'], 92)}")
    lines.append("\n  Rolled back:" + ("" if rolled else " nothing — no branch was pruned"))
    for r in rolled:
        lines.append(f"    {r['branch']:<10} {'proj delete ' + str(r['project']) if r['deleted_now'] else 'deleted by an earlier run'}"
                     f"  →  fact list --projects {r['project']}: {'✓ 404 (its memory is gone)' if r['gone'] else '✗ still there' if r['gone'] is False else '?'}")
    trunk = list_facts(cli, "--projects", ids["trunk"])
    lines.append(f"\n  The trunk now ({len(trunk)} fact(s)):")
    for f in trunk:
        t = (f.get("metadata") or {}).get("type") or "extracted"
        lines.append(f"    {t:<9} {clip(f.get('fact') or '', 100)}")
    emit("text", "\n".join(lines))
    out = dict(run=run["run"], merges=merges, rolled=rolled,
               trunk=[dict(id=f.get("id"), text=f.get("fact"), type=(f.get("metadata") or {}).get("type") or "extracted") for f in trunk])
    emit("merge", "", **out)
    return out


# --------------------------------------------------------------------------- step 9: the next task reads the trunk first

def plan(cli: CLI, ids: dict, story: dict, task: str, candidates: list[str] | None = None, echo: bool = True,
         before: str | None = None) -> dict:
    """Pathfinder's planning step: search the trunk for the task, keep the merge/prune lessons, and order the
    candidate approaches — merged ones first, pruned ones skipped (with the fact that says why). `before` leaves out
    the lessons of that run itself, so a re-run plans from what the trunk held before the run started."""
    candidates = candidates or list(story["approaches"])
    hits = search_facts(cli, task, [ids["trunk"]], echo)
    meta = {f["id"]: f for f in list_facts(cli, "--projects", ids["trunk"], echo=False)}
    lessons = [meta[h["id"]] for h in hits if h.get("id") in meta and (meta[h["id"]].get("metadata") or {}).get("type") in ("merged", "pruned")
               and not (before and (meta[h["id"]].get("metadata") or {}).get("run") == before)]
    others = len(hits) - len(lessons)
    steps = []
    for a in candidates:
        mine = [f for f in lessons if (f.get("metadata") or {}).get("approach") == a]
        pruned = [f for f in mine if f["metadata"]["type"] == "pruned"]
        won = [f for f in mine if f["metadata"]["type"] == "merged"]
        if pruned:
            f = pruned[0]
            steps.append(dict(approach=a, action="skip", fact=f["id"], why=f["metadata"].get("reason"), branch=f["metadata"].get("branch")))
        elif won:
            f = won[0]
            steps.append(dict(approach=a, action="first", fact=f["id"], why=f"won {f['metadata'].get('branch')} at "
                                                                            f"{f['metadata'].get('p95_ms')} ms", branch=f["metadata"].get("branch")))
        else:
            steps.append(dict(approach=a, action="explore", fact=None, why="nothing in the trunk about it", branch=None))
    order = {"first": 0, "explore": 1, "skip": 2}
    steps.sort(key=lambda s: order[s["action"]])
    return dict(task=task, hits=len(hits), lessons=[dict(id=f["id"], text=f.get("fact"), type=f["metadata"]["type"],
                                                         approach=f["metadata"].get("approach")) for f in lessons],
                others=others, steps=steps)


def show_plan(story: dict, p: dict, title: str) -> None:
    lines = [f"\n  {title}", f"  trunk search: {p['hits']} hit(s), {len(p['lessons'])} merge/prune lesson(s)"
             + (f", {p['others']} other hit(s) left out (task notes, not lessons)" if p["others"] else "")]
    for s in p["steps"]:
        a = f"{s['approach']} ({story['approaches'].get(s['approach'], s['approach'])})"
        if s["action"] == "skip":
            lines.append(f"    skip     {clip(a, 60)}\n             pruned in {s['branch']} ({s['fact']}): {clip(s['why'], 90)}")
        elif s["action"] == "first":
            lines.append(f"    try 1st  {clip(a, 60)}\n             {s['why']} ({s['fact']})")
        else:
            lines.append(f"    explore  {clip(a, 60)}  — {s['why']}")
    emit("text", "\n".join(lines))


def next_task(cli: CLI, ids: dict, story: dict) -> dict:
    r1, r2 = story["runs"][0], story["runs"][1]
    note("a week later Priya writes a new task into the same trunk log")
    append_task(cli, ids, story, r2)
    emit("text", f"\n  {r2['run']} · {r2['date']} · {r2['service']}\n  “{r2['task']}”")
    p = plan(cli, ids, story, r2["task"], r2["candidates"], before=r2["run"])
    show_plan(story, p, "Pathfinder's plan, read from the trunk before opening anything:")
    emit("plan", "", run=r2["run"], **p)
    ex = expand(cli, ids, story, r2)
    rs = reasoning_check(cli, ids, story, [r2["run"]])
    mg = merge_step(cli, ids, story, r2)
    skipped = [s for s in p["steps"] if s["action"] == "skip"]
    opened = len(r2["branches"])
    emit("text", f"\n  {r1['run']} opened {len(r1['branches'])} branch(es); {r2['run']} opened {opened} and skipped "
                 f"{len(skipped)}, each skip citing the trunk fact that says why "
                 f"({', '.join(s['fact'] for s in skipped) or 'none'})")
    out = dict(plan=p, opened=opened, skipped=len(skipped), r1_opened=len(r1["branches"]), kept_out=rs["kept_out"],
               details=rs["details"], merges=mg["merges"])
    emit("next", "", **out)
    return out


# --------------------------------------------------------------------------- read-only views

def tree(cli: CLI, ids: dict, story: dict) -> dict:
    merged = merged_on_trunk(cli, ids, echo=True)
    rows = []
    lines = [f"\n  trunk {ids['trunk']}"]
    for run in story["runs"]:
        lines.append(f"  ├─ {run['run']} · {run['date']} · {run['service']}")
        for b in run["branches"]:
            name = branch_name(run, b)
            p = find_project(cli, branch_slug(run, b), echo=False)
            m = merged.get(name)
            state = (m.get("metadata") or {}).get("type") if m else "open" if p else "—"
            state = "rolled back" if state == "pruned" and not p else state
            rows.append(dict(branch=name, state=state, project=p["id"] if p else None, trunk_fact=m["id"] if m else None))
            lines.append(f"  │   ├─ {name:<16} {state:<12} {p['id'] if p else '(no project)'}"
                         + (f"  trunk fact {m['id']}" if m else ""))
    emit("text", "\n".join(lines))
    emit("tree", "", rows=rows)
    return dict(rows=rows)


def thoughts(cli: CLI, story: dict, which: str) -> None:
    for run, b in all_branches(story):
        if which in (b["key"], branch_name(run, b)):
            conv = find_conv(cli, f"conv-{run['run']}-{b['key']}")
            if conv is None:
                emit("text", f"\n  {branch_name(run, b)} has no conversation (rolled back, or not run yet)")
                return
            for m in list_messages(cli, conv["id"]):
                for blk in m.get("content") or []:
                    emit("text", f"\n  #{m.get('sequence_no')} {blk.get('block_type')}\n    {blk.get('text')}")
            return
    die(f"no branch called {which}; try one of: " + ", ".join(b["key"] for _, b in all_branches(story)))


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    slugs = ["trunk-log"] + [f"conv-{r['run']}-{b['key']}" for r, b in all_branches(story)]
    n = 0
    for s in slugs:
        conv = find_conv(cli, s, echo=not quiet and s == "trunk-log")
        if conv:
            cli.run("conv", "delete", conv["id"], scoped=True)
            n += 1
    note(f"deleted {n} conversation(s) (`proj delete` does not delete conversations)")
    n = 0
    for s in [story["trunk"]["slug"]] + [branch_slug(r, b) for r, b in all_branches(story)]:
        p = find_project(cli, s, echo=not quiet and s == story["trunk"]["slug"])
        if p:
            cli.run("proj", "delete", p["id"], scoped=True)
            n += 1
    note(f"deleted {n} project(s) (their facts go with them)")
    for person in (story["human"], story["planner"]):
        a = find_actor(cli, person["slug"], echo=not quiet)
        if a:
            cli.run("actor", "delete", a["id"])
            note(f"deleted {person['name']}")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    r1 = story["runs"][0]
    t0 = time.time()
    banner(2, TOTAL, "Trunk project, trunk log, Priya and Pathfinder")
    ids = setup(cli, story, reset)
    banner(3, TOTAL, f"The task — {r1['service']} p95 below {r1['target_ms']} ms")
    task_step(cli, ids, story)
    banner(4, TOTAL, "Three branches at once — a project each, reasoning in THINKING blocks")
    ex = expand(cli, ids, story, r1)
    banner(5, TOTAL, "One trunk log, three writers — the server only accepts appends to the head")
    lg = show_log(cli, ids, story)
    banner(6, TOTAL, "Reasoning is stored, not remembered — THINKING replayed vs fact list")
    rs = reasoning_check(cli, ids, story, [r1["run"]])
    banner(7, TOTAL, "Checkout — trunk alone vs trunk + one branch")
    co = checkout(cli, ids, story, r1)
    banner(8, TOTAL, "Merge the winner and the lessons; roll back the dead branches")
    mg = merge_step(cli, ids, story, r1)
    banner(9, TOTAL, "A week later — the next task reads the trunk before branching")
    nx = next_task(cli, ids, story)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   fresh=ex["fresh"], cook_seconds=ex["cook_seconds"], appends=ex["appends"], conflicts_409=ex["conflicts"],
                   log_contiguous=lg["contiguous"], log_unique=lg["unique"], stale_refused=(lg["stale"] or {}).get("refused"),
                   thinking_verbatim=f"{rs['verbatim']}/{rs['thoughts']}", kept_out_r1=f"{rs['kept_out']}/{rs['details']}",
                   kept_out_r2=f"{nx['kept_out']}/{nx['details']}",
                   checkout_ok=all(v["own"] and not v["in_trunk_only"] for v in co["views"]) if co["views"] else None,
                   rolled_back_404=sum(1 for r in mg["rolled"] if r["gone"]), rolled=len(mg["rolled"]),
                   r2_opened=nx["opened"], r2_skipped=nx["skipped"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    emit("summary", "", **summary)
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    trunk = find_project(cli, story["trunk"]["slug"])
    priya, pf = find_actor(cli, story["human"]["slug"]), find_actor(cli, story["planner"]["slug"])
    log = find_conv(cli, "trunk-log")
    if not (trunk and priya and pf and log):
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(trunk=trunk["id"], priya=priya["id"], pathfinder=pf["id"], log=log["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "tree", "log", "thoughts", "plan", "cleanup"],
                    help="run = full demo (default); tree = branches and their state; log = the trunk log; "
                         "thoughts BRANCH = replay a branch's THINKING blocks; plan \"TASK\" = Pathfinder's plan from the "
                         "trunk; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="the branch for `thoughts` (e.g. pool), or the task for `plan`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command in ("thoughts", "plan") and not args.arg:
        ap.error("thoughts needs a branch (e.g. pool); plan needs a task, e.g. "
                 "python3 demo.py plan \"cart page p95 is 700 ms\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py tree`, `python3 demo.py log`, `python3 demo.py thoughts pool` or "
              "`python3 demo.py plan \"…\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    story, ids = load_story(), need_ids(cli)
    if args.command == "tree":
        banner(2, 2, "Branches and their state")
        tree(cli, ids, story)
    elif args.command == "log":
        banner(2, 2, "The trunk log")
        show_log(cli, ids, story, check_stale=False)
    elif args.command == "thoughts":
        banner(2, 2, f"Replay the THINKING blocks of {args.arg}")
        thoughts(cli, story, args.arg)
    elif args.command == "plan":
        banner(2, 2, "Plan a task from the trunk")
        show_plan(story, plan(cli, ids, story, args.arg), "Pathfinder's plan:")


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

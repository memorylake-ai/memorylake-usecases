#!/usr/bin/env python3
"""
Project memory for product managers — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Fieldbook (fictional) is a field-service scheduling app.  Its product manager,
Priya Raman, keeps the roadmap in one MemoryLake project: who may decide, and
four decisions with their dates and owners.  The team's #fieldbook-roadmap
channel goes into the same project, one message at a time, and MemoryLake
turns each message into memory.

Two of the four messages come from people who ask for things rather than
decide them: the support lead says dark mode ships next month, and the VP of
Sales says offline mode is back and that a customer was told September.
Extraction writes those down as plain project facts ("Offline mode is planned
to ship in September") with nothing that says who said them.  Ask the project
"Is offline mode on the roadmap?" and the VP's sentence is the first hit.

Memory still knows who said it.  `fact trace` shows each fact was extracted
(COOK) from one channel message; `conv msg list` says whose message that was;
the pinned decision rights say whether that person can decide.  Read that way
the roadmap is right for all four items, including a date change that came
from the channel and *is* a decision, because the engineering lead made it.
The claims are then rewritten in place to say what they are.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-pjm"  # memorylake-usecases / project-memory

PROJECTS = dict(
    roadmap=dict(key="roadmap", name="Fieldbook roadmap",
                 description="Decision rights, the decision log and the #fieldbook-roadmap channel. Demo data."),
)


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

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False):
        try:
            return self.run(*args, scoped=scoped)
        except CLIError as e:
            if e.has(*missing):
                return None
            raise


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


# --------------------------------------------------------------------------- reading scopes

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


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", flag, scope_id], echo=echo)


def find(cli: CLI, kind: str, key: str, echo: bool = True):
    try:
        return cli.run(kind, "get", cid(key), "--by-custom-id", scoped=kind in ("proj", "conv"), echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def bind_actor(cli: CLI, actor_id: str) -> None:
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, key: str, name: str, description: str, tags: str) -> str:
    actor = find(cli, "actor", key)
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", name,
                        "--description", description, "--tags", tags)
        note(f"created {name} → {actor['id']}")
    else:
        note(f"{name} already exists → {actor['id']}")
    bind_actor(cli, actor["id"])
    return actor["id"]



# --------------------------------------------------------------------------- conversations

def list_messages(cli: CLI, conv_id: str) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"] + (["--continuation-token", token] if token else [])
        page = cli.run(*args, echo=False) or {}
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return msgs


def wait_for_cook(cli: CLI, conv_ids: list[str], timeout: int = 600) -> int:
    t0 = last = time.time()
    left, first = list(conv_ids), True
    while left and time.time() - t0 < timeout:
        for cid_ in list(left):
            try:
                status = cli.run("conv", "cook-status", cid_, scoped=True, echo=first) or {}
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                status = {}
            first = False
            if status.get("cook_finished"):
                left.remove(cid_)
        if not left:
            break
        if time.time() - last >= 30:
            emit("progress", f"  … still turning {len(left)} ticket conversation(s) into memory ({int(time.time() - t0)}s)")
            last = time.time()
        time.sleep(5)
    if left:
        note(f"gave up after {timeout}s; {len(left)} conversation(s) still processing server-side")
        return -1
    return int(time.time() - t0)


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    def facts() -> list:
        out, token = [], None
        while True:
            args = ["fact", "list", "--actors", actor_id, "--page-size", "50"]
            if token:
                args += ["--continuation-token", token]
            page = cli.run(*args, scoped=True, echo=False) or {}
            out.extend(page.get("items") or [])
            token = page.get("continuation_token")
            if not token:
                return out

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



# --------------------------------------------------------------------------- reading the story

def person(story: dict, key: str) -> dict:
    return story["people"][key]


def item_of(story: dict, key: str) -> dict:
    return next(i for i in story["items"] if i["key"] == key)


def about(item: dict, text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in item["words"])


def stance(item: dict, text: str) -> str | None:
    """Which state of the item a sentence asserts: the state whose marker appears first in the text.
    The first one, not any one — a revised fact carries the old value further on ("… previously …")."""
    t = (text or "").lower()
    best = None
    for state, markers in item["states"].items():
        for m in markers:
            i = t.find(m)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, state)
    return best[1] if best else None


def item_for_text(story: dict, text: str) -> dict | None:
    return next((i for i in story["items"] if about(i, text)), None)


def ts(s: str) -> str:
    """A sortable UTC timestamp from either a decision date (2026-03-12) or a message time."""
    s = (s or "").replace("+00:00", "Z")
    return s if "T" in s else f"{s}T00:00:00Z"


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = {}
    p = PROJECTS["roadmap"]
    proj = find(cli, "proj", p["key"])
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                       "--description", p["description"], scoped=True)
        note(f"created “{p['name']}” → {proj['id']}")
    else:
        note(f"“{p['name']}” already exists → {proj['id']} (pass --reset to start over)")
    ids["roadmap"] = proj["id"]
    for k, a in story["people"].items():
        ids[k] = ensure_actor(cli, a["key"], a["name"], a["description"], "fieldbook")
    holders = set(story["rights"]["holders"])
    lines = [f"\n  Roadmap project        {ids['roadmap']}  (decisions, decision rights and the channel all land here)"]
    lines += [f"  {a['name']:<22} {ids[k]}  ({a['role']}{', decides' if k in holders else ', asks'})"
              for k, a in story["people"].items()]
    emit("text", "\n".join(lines))
    emit("setup", "", ids=ids, project=p["name"],
         people=[dict(key=k, name=a["name"], role=a["role"], decides=k in holders) for k, a in story["people"].items()])
    return ids


# --------------------------------------------------------------------------- step 3: the decision log

def decision_meta(story: dict, d: dict) -> dict:
    who = person(story, d["by"])
    return dict(kind="decision", key=d["id"], item=d["item"], by=d["by"], decided_by=who["name"], role=who["role"], on=d["on"])


def rights_meta(story: dict) -> dict:
    return dict(kind="rights", key="rights", holders=[person(story, k)["name"] for k in story["rights"]["holders"]])


DECISION_LINE = re.compile(r"^Roadmap decision (D-\d+) \((\d{4}-\d{2}-\d{2}), ([^,]+), ([^)]+)\):")


def decision_line(story: dict, text: str) -> dict | None:
    """A decision-log entry names its decider in a fixed format; read it from the words, because the
    metadata does not survive an extraction that rewrites the fact."""
    m = DECISION_LINE.match(text or "")
    if not m:
        return None
    who = next((k for k, p in story["people"].items() if p["name"] == m.group(3)), None)
    return dict(key=m.group(1), on=m.group(2), who=who)


def trace(cli: CLI, ids: dict, fact_id: str, echo: bool = False) -> list[dict]:
    return (cli.run("fact", "trace", fact_id, "--project", ids["roadmap"], scoped=True, echo=echo) or {}).get("trace") or []


def locate_pinned(cli: CLI, ids: dict, story: dict, facts: list[dict]) -> dict[str, dict]:
    """Decision key → the fact that holds it now. By metadata first; a fact a conversation rewrote has lost
    its metadata, so the rest are found by their trace: the pinned words are still its first MANUAL entry."""
    found = {f["meta"]["key"]: dict(f, rewritten=False) for f in facts
             if f["meta"].get("kind") in ("decision", "rights") and f["meta"].get("key")}
    want = {d["id"] for d in story["decisions"]} - set(found)
    for f in facts:
        if not want:
            break
        if f["meta"].get("kind") in ("decision", "rights", "claim"):
            continue
        for e in trace(cli, ids, f["id"]):
            d = decision_line(story, e.get("new_fact")) if e.get("source_kind") == "MANUAL" else None
            if d and d["key"] in want:
                found[d["key"]] = dict(f, rewritten=True)
                want.discard(d["key"])
                break
    return found


def pin_keyed(cli: CLI, ids: dict, story: dict, items: list[tuple[str, dict]]) -> dict[str, dict]:
    """Pin what the project does not hold yet, matched by `metadata.key` or, for a decision a conversation
    rewrote, by its trace (never by text). One `fact add` for all of them, then `fact update --metadata` on each."""
    scope_id = ids["roadmap"]
    live = locate_pinned(cli, ids, story, roadmap_facts(cli, ids, echo=False))
    missing = [(t, m) for t, m in items if m["key"] not in live]
    out = {m["key"]: dict(id=live[m["key"]]["id"], new=False) for t, m in items if m["key"] in live}
    if missing:
        res = cli.run("fact", "add", "--project", scope_id, *[t for t, _ in missing], scoped=True) or {}
        facts = res.get("facts") or []
        if [f.get("fact") for f in facts] != [t for t, _ in missing]:
            die("`fact add` did not return the pinned texts in order")
        for i, ((t, m), f) in enumerate(zip(missing, facts)):
            cli.run("fact", "update", f["id"], "--project", scope_id, "--metadata", json.dumps(m), scoped=True, echo=i == 0)
            out[m["key"]] = dict(id=f["id"], new=True)
        if len(missing) > 1:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way (kind, who decided, when, which item)")
    else:
        note("the decision log was pinned by an earlier run; reading it back")
    return out


def pin_decisions(cli: CLI, ids: dict, story: dict) -> None:
    note("the decision rights and four roadmap decisions go onto the project in one `fact add`; "
         "each then gets metadata: who decided, their role, the date, the item")
    pin_keyed(cli, ids, story, [(story["rights"]["text"], rights_meta(story))] +
              [(d["text"], decision_meta(story, d)) for d in story["decisions"]])
    lines = [f"\n  {story['rights']['text']}", "", f"  {'id':<5} {'decided':<11} {'by':<30} decision"]
    for d in story["decisions"]:
        who = person(story, d["by"])
        lines.append(f"  {d['id']:<5} {d['on']:<11} {who['name'] + ', ' + who['role']:<30} {clip(d['text'].split(': ', 1)[1], 82)}")
    emit("text", "\n".join(lines))
    emit("decisions", "", rights=story["rights"]["text"],
         items=[dict(id=d["id"], on=d["on"], by=person(story, d["by"])["name"], role=person(story, d["by"])["role"],
                     item=item_of(story, d["item"])["name"], text=d["text"]) for d in story["decisions"]])


# --------------------------------------------------------------------------- step 4: the channel, one message at a time

def wait_until_read(cli: CLI, conv_id: str, msg_id: str, timeout: int = 300) -> tuple[int, int]:
    """Wait until this message has been read into memory; returns (seconds, size of the batch it was read in).
    `cook-status` alone can still say "finished" from the previous message; `consumed-messages` is empty
    until this message itself has been read."""
    t0, last, first = time.time(), time.time(), True
    while time.time() - t0 < timeout:
        try:
            batch = cli.run("conv", "consumed-messages", conv_id, "--message", msg_id, scoped=True, echo=first) or []
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first) or {}
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            batch, status = [], {}
        first = False
        got = batch if isinstance(batch, list) else (batch.get("items") or [])
        if got and status.get("cook_finished"):
            return int(time.time() - t0), len(got)
        if time.time() - last >= 30:
            emit("progress", f"  … still turning the message into memory ({int(time.time() - t0)}s)")
            last = time.time()
        time.sleep(4)
    note(f"gave up after {timeout}s; the message is still being processed server-side")
    return -1, 0


def channel(cli: CLI, ids: dict, story: dict) -> dict:
    ch = story["channel"]
    conv = find(cli, "conv", ch["key"], echo=False)
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", cid(ch["key"]), "--project", ids["roadmap"],
                       "--actors", ",".join(ids[k] for k in story["people"]), "--kind", "GROUP", "--name", ch["name"],
                       scoped=True)
    done = len(list_messages(cli, conv["id"]))
    if done:
        note(f"{done} of {len(ch['messages'])} messages are already in the channel from an earlier run")
    note("each message is appended, then read into memory before the next one arrives — so every extracted fact "
         "traces back to exactly one message")
    holders = set(story["rights"]["holders"])
    appended, batches = 0, []
    for i, m in enumerate(ch["messages"], start=1):
        p = person(story, m["who"])
        if i <= done:
            emit("message", "", index=i, who=p["name"], role=p["role"], at=m["at"], line=m["text"], decides=m["who"] in holders,
                 seconds=None, batch=None)
            continue
        res = cli.run("conv", "msg", "append", conv["id"], "--actor", ids[m["who"]], "--custom-id", f"msg-{i:02d}",
                      "--timestamp", m["at"], "--text", m["text"], scoped=True, echo=appended == 0) or {}
        mid = res.get("id") or res.get("message_id") or ""
        secs, size = wait_until_read(cli, conv["id"], mid) if mid else (-1, 0)
        appended += 1
        batches.append(size)
        emit("text", f"  {m['at'][:10]}  {p['name']} ({p['role']}): “{m['text']}”\n"
                     f"              read into memory in {secs}s, batch of {size} message{'s' if size != 1 else ''}")
        emit("message", "", index=i, who=p["name"], role=p["role"], at=m["at"], line=m["text"], decides=m["who"] in holders,
             seconds=secs, batch=size)
    return dict(conv=conv["id"], appended=appended, batches=batches)


# --------------------------------------------------------------------------- step 5: what the project remembers

def roadmap_facts(cli: CLI, ids: dict, echo: bool = True) -> list[dict]:
    return [dict(id=f["id"], text=f.get("fact") or "", meta=f.get("metadata") or {})
            for f in list_facts(cli, "--projects", ids["roadmap"], echo=echo)]


def kind_of(f: dict, located: dict | None = None) -> str:
    k = f["meta"].get("kind")
    if located:
        key = next((key for key, x in located.items() if x["id"] == f["id"] and x.get("rewritten")), None)
        if key:
            return f"pinned {key}, rewritten"
    return {"decision": f"pinned {f['meta'].get('key')}", "rights": "pinned rights", "claim": "annotated claim"}.get(k, "extracted")


ANNOTATED = "Not a roadmap decision."


def replay_today(cli: CLI, ids: dict) -> int:
    """Every run replays the same day: what an earlier run's write-back changed goes back to the words
    extraction wrote, so this run reads the channel's effect afresh."""
    n = 0
    for f in roadmap_facts(cli, ids, echo=False):
        m = f["meta"]
        if m.get("kind") == "claim" and m.get("split"):
            cli.run("fact", "delete", "--project", ids["roadmap"], f["id"], scoped=True, echo=n == 0)
        elif m.get("kind") == "claim" and m.get("previous"):
            cli.run("fact", "update", f["id"], "--project", ids["roadmap"], "--text", m["previous"],
                    "--metadata", json.dumps({"kind": "extracted"}), scoped=True, echo=n == 0)
        elif m.get("kind") == "decision" and m.get("overwritten_by"):
            cli.run("fact", "update", f["id"], "--project", ids["roadmap"], "--text", m["overwritten_by"],
                    "--metadata", json.dumps({"kind": "extracted"}), scoped=True, echo=n == 0)
        else:
            continue
        n += 1
    if n:
        note(f"replaying today: {n} fact(s) an earlier run wrote back are returned to the words extraction wrote")
    return n


def show_memory(cli: CLI, ids: dict, story: dict) -> dict:
    facts = roadmap_facts(cli, ids)
    located = locate_pinned(cli, ids, story, facts)
    lines = [f"\n  {'kind':<24} fact"]
    rank = lambda f: (0 if kind_of(f) == "pinned rights" else 1 if kind_of(f, located).startswith("pinned") else 2, f["text"])
    for f in sorted(facts, key=rank):
        lines.append(f"  {kind_of(f, located):<24} {clip(f['text'], 104)}")
    extracted = [f for f in facts if kind_of(f, located) == "extracted"]
    rewritten = sorted(k for k, x in located.items() if x.get("rewritten"))
    on_actors = {}
    for k, a in story["people"].items():
        on_actors[a["name"]] = len(list_facts(cli, "--actors", ids[k], echo=k == "priya"))
    conflicts = (cli.run("fact", "conflict", "list", "--project", ids["roadmap"], scoped=True) or {}).get("items") or []
    named = [f for f in extracted if any(a["name"].split()[0] in f["text"] for a in story["people"].values())]
    lines.append(f"\n  {len(extracted)} new fact(s) extracted from the channel"
                 + ("; none of them names who said it." if not named else f"; {len(named)} name a speaker."))
    if rewritten:
        lines.append(f"  {len(rewritten)} pinned decision(s) rewritten in place by the channel: {', '.join(rewritten)} — "
                     "same fact id, new words, and the pinned metadata (who decided, when) is gone.")
    else:
        lines.append("  No pinned decision was rewritten in place this time.")
    lines.append("  On the people's own actors: " + ", ".join(f"{n} {c}" for n, c in on_actors.items()))
    lines.append(f"  Contradictions the detector has raised on the project so far: {len(conflicts)}"
                 + ("" if not conflicts else " — " + "; ".join(clip(c.get("name") or "", 60) for c in conflicts)))
    emit("text", "\n".join(lines))
    emit("memory", "", facts=[dict(kind=kind_of(f, located), text=f["text"], id=f["id"]) for f in facts],
         actors=on_actors, conflicts=[c.get("name") for c in conflicts], rewritten=rewritten)
    return dict(extracted=len(extracted), rewritten=rewritten, on_actors=on_actors, conflicts=len(conflicts))


# --------------------------------------------------------------------------- step 6: the quick read

def quick(cli: CLI, ids: dict, story: dict, it: dict, question: str, echo: bool = True) -> dict:
    """What a tool that trusts the project's top search hit would say: the first hit about the item that asserts a state."""
    res = cli.run("search", question, "--projects", ids["roadmap"], "--types", "fact", "--top-k", "8", scoped=True, echo=echo) or {}
    hits = res.get("facts") or []
    live = {f["id"] for f in roadmap_facts(cli, ids, echo=False)}
    cached = [h["id"] for h in hits if h.get("id") not in live]
    if cached:
        note(f"search answered from its cache ({len(cached)} hit(s) no longer in the project)")
    rel = [(n, h) for n, h in enumerate(hits, start=1) if about(it, h.get("fact")) and stance(it, h.get("fact"))]
    rank, top = rel[0] if rel else (None, None)
    said = stance(it, top.get("fact")) if top else None
    return dict(item=it["key"], name=it["name"], question=question, rank=rank, text=top.get("fact") if top else None,
                id=top.get("id") if top else None, said=said, ok=said == it["truth"], cached=len(cached))


def show_quick(rows: list[dict]) -> None:
    lines = []
    for r in rows:
        mark = "✓" if r["ok"] else "✗"
        lines.append(f"\n  {mark} {r['question']}")
        if r["text"]:
            lines.append(f"      top hit #{r['rank']}: “{clip(r['text'], 110)}”  → reads as: {r['said']}")
        else:
            lines.append("      no hit about it in the top 8")
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- step 7: who said it

def speakers(cli: CLI, conv_id: str) -> dict[str, dict]:
    return {m["id"]: dict(actor=m.get("actor_id"), at=m.get("timestamp"), seq=m.get("sequence_no"))
            for m in list_messages(cli, conv_id)}


def author(story: dict, ids: dict, e: dict, msgs: dict) -> dict:
    """Who wrote one trace entry: a COOK entry's source message(s) → their speaker; a MANUAL entry in the
    decision-log format → its decider; the demo's own annotation; or unknown."""
    by_actor = {ids[k]: k for k in story["people"]}
    if e.get("source_kind") == "COOK":
        entries = e.get("source_entry_ids") or []
        found = [msgs[x] for x in entries if x in msgs]
        who = {by_actor.get(x["actor"]) for x in found}
        at = max((x["at"] for x in found if x.get("at")), default=None)
        if len(who) != 1 or None in who:
            return dict(how="COOK", who=None, at=at, entries=len(entries), conv=e.get("conversation_id"), ambiguous=True)
        return dict(how="COOK", who=who.pop(), at=at, entries=len(entries), conv=e.get("conversation_id"),
                    seq=[x.get("seq") for x in found], ambiguous=False)
    d = decision_line(story, e.get("new_fact"))
    if d:
        return dict(how="MANUAL pinned", who=d["who"], at=d["on"], key=d["key"], ambiguous=False)
    if (e.get("new_fact") or "").startswith(ANNOTATED):
        return dict(how="MANUAL annotated", who=None, at=None, ambiguous=False, annotated=True)
    return dict(how="MANUAL", who=None, at=None, ambiguous=True, unknown=True)


def origin(cli: CLI, ids: dict, story: dict, f: dict, msgs: dict, echo: bool) -> dict:
    """Whose words a roadmap fact holds now, whether that person can decide — and if not, the last version
    in its own history that someone who can decide wrote."""
    events = trace(cli, ids, f["id"], echo=echo)            # newest first
    holders = set(story["rights"]["holders"])
    authors = [author(story, ids, e, msgs) for e in events]
    i = 0
    if authors and authors[0].get("unknown"):              # a MANUAL rewrite to words a conversation wrote earlier
        i = next((j for j, (e, a) in enumerate(zip(events, authors))
                  if a["how"] == "COOK" and e.get("new_fact") == f["text"]), 0)
    o = dict(authors[i]) if authors else dict(how="?", who=None, at=None, ambiguous=True)
    if o.get("annotated"):
        o["who"], o["at"] = f["meta"].get("speaker_key"), f["meta"].get("said_on")
    o["event"] = events[i].get("event") if events else None
    o["authorized"] = bool(o.get("who")) and o["who"] in holders and not o.get("annotated")
    if o.get("who"):
        p = person(story, o["who"])
        o["name"], o["role"] = p["name"], p["role"]
    o["standing_text"] = o["standing"] = None
    if not o["authorized"] and not o.get("annotated"):
        j = next((j for j in range(i + 1, len(authors)) if authors[j].get("who") in holders), None)
        if j is not None:
            s = dict(authors[j], name=person(story, authors[j]["who"])["name"], role=person(story, authors[j]["who"])["role"])
            o["standing_text"], o["standing"] = events[j].get("new_fact"), s
    o["history"] = [dict(event=e.get("event"), how=a["how"], who=person(story, a["who"])["name"] if a.get("who") else None,
                         text=e.get("new_fact")) for e, a in zip(events, authors)]
    return o


def provenance(cli: CLI, ids: dict, story: dict, conv_id: str, echo: bool = True) -> dict:
    facts = roadmap_facts(cli, ids, echo=echo)
    msgs = speakers(cli, conv_id)
    if echo:
        emit("cmd", shlex.join(["memorylake", "conv", "msg", "list", conv_id, "--page-size", "50"]))
    rows, first = [], True
    for f in facts:
        it = item_for_text(story, f["text"])
        if it is None or f["meta"].get("kind") == "rights":
            continue
        if stance(it, f["text"]) is None:
            continue
        o = origin(cli, ids, story, f, msgs, echo=echo and first)
        first = False
        rows.append(dict(id=f["id"], text=f["text"], item=it["key"], stance=stance(it, f["text"]), meta=f["meta"], **o))
    answers = {}
    for it in story["items"]:
        mine = [r for r in rows if r["item"] == it["key"]]
        dec = [r for r in mine if r["authorized"]]
        dec += [dict(r, text=r["standing_text"], stance=stance(it, r["standing_text"]), at=r["standing"]["at"],
                     how=r["standing"]["how"], who=r["standing"]["who"], name=r["standing"]["name"], role=r["standing"]["role"],
                     authorized=True, restored=True)
                for r in mine if r.get("standing_text") and r["standing"]["how"] == "MANUAL pinned"]
        dec = [d for d in dec if d.get("stance")]
        dec.sort(key=lambda r: ts(r.get("at") or ""))
        answers[it["key"]] = dec[-1] if dec else None
    claims = [r for r in rows if not r["authorized"] and not r.get("annotated")]
    return dict(rows=rows, answers=answers, claims=claims, ambiguous=sum(1 for r in rows if r.get("ambiguous")))


def chain(r: dict) -> str:
    if r["how"] == "COOK":
        if r.get("ambiguous"):
            return f"← COOK {r.get('event')} {r.get('conv')} ← {r.get('entries')} messages from different people: ambiguous"
        when = (r.get("at") or "")[:16].replace("T", " ")
        return (f"← COOK {r.get('event')} ← message #{(r.get('seq') or ['?'])[0]} ({when}Z, batch of {r.get('entries')}) "
                f"← {r['name']}, {r['role']}")
    if r["how"] == "MANUAL pinned":
        return f"← MANUAL (pinned {r.get('key')}, {r.get('at')}) ← {r['name']}, {r['role']}"
    if r.get("annotated"):
        return f"← MANUAL (annotated claim) ← said by {r.get('name')}, {r.get('role')}"
    return f"← {r['how']}"


def verdict(r: dict) -> str:
    if r.get("annotated"):
        return "annotated"
    if r["authorized"]:
        return "DECISION"
    if r.get("standing") and r["standing"]["how"] == "MANUAL pinned":
        return f"claim — overwrote {r['standing'].get('key')} ({r['standing']['name']})"
    return "claim — no decision right"


def show_provenance(pv: dict, story: dict) -> None:
    lines = []
    for it in story["items"]:
        mine = [r for r in pv["rows"] if r["item"] == it["key"]]
        if not mine:
            continue
        lines.append(f"\n  {it['name']}")
        for r in sorted(mine, key=lambda r: ts(r.get("at") or "")):
            lines.append(f"    “{clip(r['text'], 96)}”")
            lines.append(f"        {chain(r)}  →  {verdict(r)}")
            if r.get("standing") and r["standing"]["how"] == "MANUAL pinned":
                lines.append(f"        same fact, earlier in its trace: “{clip(r['standing_text'], 92)}”")
            elif len(r.get("history") or []) > 1:
                def step(h: dict) -> str:
                    if h["how"] == "MANUAL pinned":
                        return f"{h['event']} MANUAL ({'pinned by ' + h['who'] if h['event'] == 'ADD' else 'decision restored'})"
                    if h["how"] == "COOK":
                        return f"{h['event']} COOK ({h['who'] or 'several speakers'})"
                    return f"{h['event']} MANUAL ({'annotated' if h['how'] == 'MANUAL annotated' else 'replayed'})"
                steps = [step(h) for h in reversed(r["history"])]
                lines.append(f"        its trace: {' → '.join(steps)}")
    emit("text", "\n".join(lines))
    emit("provenance", "", rows=[dict(item=r["item"], text=r["text"], how=r["how"], event=r.get("event"), who=r.get("name"),
                                      role=r.get("role"), at=r.get("at"), authorized=r["authorized"],
                                      annotated=bool(r.get("annotated")), ambiguous=bool(r.get("ambiguous")),
                                      entries=r.get("entries"), verdict=verdict(r), chain=chain(r),
                                      overwrote=(r.get("standing") or {}).get("key") if (r.get("standing") or {}).get("how") == "MANUAL pinned" else None,
                                      standing_text=r.get("standing_text"))
                                 for r in pv["rows"]])


def compare(story: dict, q: list[dict], pv: dict) -> list[dict]:
    out = []
    for r in q:
        it = item_of(story, r["item"])
        a = pv["answers"].get(r["item"])
        said = a["stance"] if a else None
        out.append(dict(item=r["item"], name=it["name"], question=r["question"], quick=r["said"], quick_ok=r["ok"],
                        memory=said, memory_ok=said == it["truth"], by=a.get("name") if a else None,
                        at=(a.get("at") or "")[:10] if a else None, restored=bool(a and a.get("restored"))))
    return out


def show_compare(rows: list[dict]) -> None:
    lines = [f"\n  {'question':<46} {'top search hit':<18} {'decided, by who may decide':<40}"]
    for r in rows:
        q = f"{'✓' if r['quick_ok'] else '✗'} {r['quick'] or '—'}"
        m = f"{'✓' if r['memory_ok'] else '✗'} {r['memory'] or '—'}" + (f"  ({r['by']}, {r['at']})" if r["by"] else "")
        m += "  ← from the fact's own trace" if r["restored"] else ""
        lines.append(f"  {clip(r['question'], 46):<46} {q:<18} {m}")
    qn, mn = sum(r["quick_ok"] for r in rows), sum(r["memory_ok"] for r in rows)
    lines.append(f"\n  top search hit: {qn}/{len(rows)} right · read by who may decide: {mn}/{len(rows)} right")
    emit("text", "\n".join(lines))
    emit("compare", "", rows=rows, quick=qn, memory=mn)


# --------------------------------------------------------------------------- step 8: write it back

def annotation(story: dict, r: dict, standing: dict | None) -> str:
    when = (r.get("at") or "")[:10]
    head = ANNOTATED
    if standing:
        head += f" Standing decision ({standing['name']}, {(standing.get('at') or '')[:10]}): {standing['text'].rstrip('.')}."
    return f"{head} {r['name']} ({r['role']}) said in {story['channel']['name']} on {when}: {r['text']}"


def claim_meta(r: dict, standing: dict | None, split: bool) -> dict:
    return dict(kind="claim", key=f"claim-{r['id']}", item=r["item"], speaker=r["name"], speaker_key=r["who"],
                role=r["role"], said_on=r.get("at"), conversation=r.get("conv"), decision_right=False,
                standing=(standing or {}).get("id"), previous=r["text"], split=split)


def write_back(cli: CLI, ids: dict, story: dict, pv: dict) -> dict:
    restored, annotated, n = [], [], 0
    for r in pv["claims"]:
        if r.get("ambiguous") or not r.get("who"):
            continue
        standing = pv["answers"].get(r["item"])
        if r.get("standing") and r["standing"]["how"] == "MANUAL pinned":
            # A claim that overwrote a pinned decision: the decision's words go back on its own fact id,
            # and the claim is kept as a fact of its own that says who said it.
            d = next(d for d in story["decisions"] if d["id"] == r["standing"]["key"])
            cli.run("fact", "update", r["id"], "--project", ids["roadmap"], "--text", r["standing_text"],
                    "--metadata", json.dumps(dict(decision_meta(story, d), overwritten_by=r["text"])), scoped=True, echo=n == 0)
            n += 1
            standing = dict(r["standing"], text=r["standing_text"], id=r["id"])
            text = annotation(story, r, standing)
            res = cli.run("fact", "add", "--project", ids["roadmap"], text, scoped=True, echo=n == 1) or {}
            new = (res.get("facts") or [{}])[0].get("id")
            if new:
                cli.run("fact", "update", new, "--project", ids["roadmap"], "--metadata", json.dumps(claim_meta(r, standing, True)),
                        scoped=True, echo=False)
            restored.append(dict(id=r["id"], key=d["id"], item=r["item"], before=r["text"], after=r["standing_text"],
                                 claim_id=new, claim=text, speaker=r["name"]))
        else:
            text = annotation(story, r, standing)
            cli.run("fact", "update", r["id"], "--project", ids["roadmap"], "--text", text,
                    "--metadata", json.dumps(claim_meta(r, standing, False)), scoped=True, echo=n == 0)
            n += 1
            annotated.append(dict(id=r["id"], item=r["item"], before=r["text"], after=text, speaker=r["name"]))
    lines = []
    if restored:
        lines.append(f"\n  {len(restored)} pinned decision(s) put back on their own fact id; the claim that overwrote each is kept "
                     "as its own fact:")
        for d in restored:
            lines.append(f"    {d['key']} was: “{clip(d['before'], 96)}”")
            lines.append(f"    {d['key']} now: “{clip(d['after'], 96)}”")
            lines.append(f"    new claim: “{clip(d['claim'], 140)}”")
    if annotated:
        lines.append(f"\n  {len(annotated)} claim(s) rewritten in place — the standing decision first, then who said what:")
        for d in annotated:
            lines.append(f"    was: “{clip(d['before'], 100)}”")
            lines.append(f"    now: “{clip(d['after'], 150)}”")
    if not lines:
        lines.append("\n  nothing to write back: every roadmap fact is a decision")
    emit("text", "\n".join(lines))
    emit("written", "", restored=restored, annotated=annotated)
    return dict(restored=restored, annotated=annotated)


def trace_lines(cli: CLI, ids: dict, fact_id: str) -> list[str]:
    tr = cli.run("fact", "trace", fact_id, "--project", ids["roadmap"], scoped=True) or {}
    out = []
    for e in tr.get("trace") or []:
        out.append(f"    {e.get('event', ''):<7} {e.get('source_kind', ''):<7} {(e.get('timestamp') or '')[:19]}Z  “{clip(e.get('new_fact'), 90)}”")
    return out


def after(cli: CLI, ids: dict, story: dict, wb: dict) -> dict:
    shown = (wb["restored"] or wb["annotated"])[:1]
    for d in shown:
        lines = [f"\n  fact trace {d['id']} (newest first):"] + trace_lines(cli, ids, d["id"])
        emit("text", "\n".join(lines))
    time.sleep(10)   # a fresh write reaches `search` a few seconds after `fact list`
    note("the same quick read, asked in new words (search caches answers per question text)")
    rows = [quick(cli, ids, story, item_of(story, q["item"]), q["again"], echo=n == 0) for n, q in enumerate(story["questions"])]
    show_quick(rows)
    n = sum(r["ok"] for r in rows)
    emit("text", f"\n  top search hit after the write-back: {n}/{len(rows)} right")
    emit("after", "", rows=rows, ok=n)
    return dict(rows=rows, ok=n)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    conv = find(cli, "conv", story["channel"]["key"], echo=not quiet)
    if conv:
        cli.run("conv", "delete", conv["id"], scoped=True)   # `proj delete` does not delete conversations
    proj = find(cli, "proj", PROJECTS["roadmap"]["key"], echo=not quiet)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
    for k in story["people"]:
        actor = find(cli, "actor", k, echo=not quiet)
        if actor:
            delete_actor(cli, actor["id"])
    note("deleted the channel conversation, the roadmap project and the four people")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one roadmap project, four people")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "The decision log — who may decide, and four decisions, pinned with their owners")
    pin_decisions(cli, ids, story)
    banner(4, TOTAL, f"{story['channel']['name']} — four messages, each read into memory as it arrives")
    ch = channel(cli, ids, story)
    ids["conv"] = ch["conv"]
    banner(5, TOTAL, "What the project remembers now")
    replayed = replay_today(cli, ids)
    mem = show_memory(cli, ids, story)
    banner(6, TOTAL, "The quick read — ask the project, trust the top hit")
    q = [quick(cli, ids, story, item_of(story, x["item"]), x["ask"], echo=n == 0) for n, x in enumerate(story["questions"])]
    show_quick(q)
    emit("quick", "", rows=q, ok=sum(r["ok"] for r in q))
    banner(7, TOTAL, "Who said it? fact trace → the source message → its speaker → the decision rights")
    pv = provenance(cli, ids, story, ch["conv"])
    show_provenance(pv, story)
    cmp_rows = compare(story, q, pv)
    show_compare(cmp_rows)
    banner(8, TOTAL, "Write it back — decisions restored on their own fact id, each claim labelled with who said it")
    wb = write_back(cli, ids, story, pv)
    af = after(cli, ids, story, wb)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   appended=ch["appended"], batches=ch["batches"], replayed=replayed, extracted=mem["extracted"], rewritten=mem["rewritten"],
                   on_actors=mem["on_actors"], conflicts=mem["conflicts"],
                   quick={r["item"]: dict(ok=r["ok"], rank=r["rank"], said=r["said"], cached=r["cached"]) for r in q},
                   memory={r["item"]: r["memory_ok"] for r in cmp_rows}, claims=len(pv["claims"]),
                   ambiguous=pv["ambiguous"], restored=[d["key"] for d in wb["restored"]],
                   annotated=len(wb["annotated"]), after=af["ok"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    proj = find(cli, "proj", PROJECTS["roadmap"]["key"], echo=False)
    conv = find(cli, "conv", story["channel"]["key"], echo=False)
    if proj is None or conv is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    ids = dict(roadmap=proj["id"], conv=conv["id"])
    for k in story["people"]:
        a = find(cli, "actor", k, echo=False)
        if a is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[k] = a["id"]
    return ids


def ask(cli: CLI, ids: dict, question: str, echo: bool = True) -> dict:
    """Any roadmap question: the top search hit, and the same item read by who may decide."""
    story = load_story()
    it = item_for_text(story, question)
    if it is None:
        die("the question names none of the roadmap items: " + ", ".join(i["name"] for i in story["items"]))
    q = quick(cli, ids, story, it, question, echo=echo)
    pv = provenance(cli, ids, story, ids["conv"], echo=False)
    rows = [r for r in pv["rows"] if r["item"] == it["key"]]
    a = pv["answers"].get(it["key"])
    return dict(question=question, item=it["key"], name=it["name"], truth=it["truth"], quick=q,
                memory=dict(said=a["stance"] if a else None, ok=bool(a) and a["stance"] == it["truth"],
                            by=a.get("name") if a else None, at=(a.get("at") or "")[:10] if a else None,
                            text=a.get("text") if a else None),
                rows=[dict(text=r["text"], chain=chain(r), verdict=verdict(r))
                      for r in sorted(rows, key=lambda r: ts(r.get("at") or ""))])


def show_ask(r: dict) -> None:
    q, m = r["quick"], r["memory"]
    lines = [f"\n  {r['question']}  ({r['name']})", ""]
    lines.append(f"  {'✓' if q['ok'] else '✗'} top search hit #{q['rank']}: “{clip(q['text'], 100)}” → {q['said']}"
                 if q["text"] else "  ✗ no hit about it in the top 8")
    lines.append(f"  {'✓' if m['ok'] else '✗'} decided, by who may decide: {m['said']} ({m['by']}, {m['at']})"
                 if m["said"] else "  ✗ no decision on record")
    lines.append("\n  every roadmap fact about it, oldest first:")
    for x in r["rows"]:
        lines.append(f"    “{clip(x['text'], 96)}”\n        {x['chain']}  →  {x['verdict']}")
    emit("text", "\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "provenance", "ask", "cleanup"],
                    help="run = full demo (default); provenance = every roadmap fact traced to who said it; "
                         "ask \"QUESTION\" = the top search hit vs the decision, for any roadmap question; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for `ask`: the question, e.g. \"Is offline mode on the roadmap?\"")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one question, e.g. ask \"Is offline mode on the roadmap?\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py provenance` or `python3 demo.py ask \"Is dark mode shipping in November?\"`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    story = load_story()
    if a.command == "provenance":
        banner(2, 2, "Provenance — every roadmap fact, traced to the person whose words it is")
        show_provenance(provenance(cli, ids, story, ids["conv"]), story)
    elif a.command == "ask":
        banner(2, 2, "Ask — the top search hit, and the decision")
        show_ask(ask(cli, ids, a.args[0]))


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

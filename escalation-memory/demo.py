#!/usr/bin/env python3
"""
Escalation pattern memory for support teams — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Quayside's senior support agent, Maya, is leaving.  Before she goes she talks
her escalation notes through with Relay, the team's escalation agent: which
team gets which kind of issue, what each team needs attached, how to frame an
escalation.  Her notes are full of customers, contacts, ticket numbers and a
contract value.

Relay has a fact instruction of its own (`fact instruction set --agent`):
record escalation paths, context packages and framing, even when a teammate
says them; never a customer, a contact, an id or a figure.  A second agent on
the built-in default hears the same words, for comparison.

Same words, three memories: Relay keeps Maya's lessons with no customer in
them; the default agent keeps little or nothing of what Maya said; the project
the conversation lives in keeps the customers.  Every lesson on Relay traces
back to the message it came from.  Maya's actor is then sealed (`actor
unbind`) and her own memory can no longer be read — Relay's lessons are where
they were.  Tomás, three weeks in, takes the next mobile crash alone and gets
the route, the package and the framing from Relay's memory.  His outcome adds
one more lesson, again without the customer.

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

PREFIX = "mlu-esc"  # memorylake-usecases / escalation-memory

PROJECT = dict(
    custom_id=f"{PREFIX}-support",
    name="Quayside support — escalations",
    description="Where Quayside's support conversations live. Demo data.",
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


# --------------------------------------------------------------------------- data, the leak scan, the lesson check

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def load_instruction() -> str:
    return (DATA / "relay-instruction.md").read_text(encoding="utf-8")


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def person(story: dict, key: str) -> dict:
    return next(p for p in story["people"] if p["key"] == key)


def agent_def(story: dict, key: str) -> dict:
    return next(a for a in story["agents"] if a["key"] == key)


# Dates the server adds to a fact ("(as of 2026-10-05)", "on 2026-10-05") are not customer figures.
_SERVER_DATES = re.compile(r"\b(?:as of|on)\s+\d{4}-\d{2}-\d{2}\b|\(\s*\d{4}-\d{2}-\d{2}\s*\)")


def leaks(text: str, story: dict) -> list[str]:
    """What in this fact points at a customer: any customer identifier (whole words), or any figure.

    A list-based scan only catches what is on the list; the figure rule is the safety net for
    contract values, ids and counts that are not on it.
    """
    found = []
    for c in story["customers"]:
        for word in c["identifiers"]:
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text or "", re.I):
                found.append(word)
    rest = _SERVER_DATES.sub("", text or "")
    for c in story["customers"]:
        for word in c["identifiers"]:
            rest = re.sub(re.escape(word), "", rest, flags=re.I)
    # A digit glued to letters is a name, not a figure ("S3", "iOS17").
    found += [f"figure “{m}”" for m in re.findall(r"(?<![A-Za-z0-9])\$?\d+(?:[.,]\d+)*%?(?![A-Za-z])", rest)]
    return found


def all_patterns(story: dict) -> list[dict]:
    return story["patterns"] + [story["outcome"]["pattern"]]


def lessons_in(text: str, story: dict) -> list[str]:
    """Which of the expected escalation lessons this fact states (every keyword group must match)."""
    t = (text or "").lower()
    return [p["key"] for p in all_patterns(story) if all(any(w in t for w in group) for group in p["all"])]


def label(story: dict, key: str) -> str:
    return next(p["label"] for p in all_patterns(story) if p["key"] == key)


def rows_of(facts: list[dict], story: dict) -> list[dict]:
    return [dict(id=f["id"], fact=f.get("fact") or "", leaks=leaks(f.get("fact") or "", story),
                 lessons=lessons_in(f.get("fact") or "", story)) for f in facts]


def scan_line(rows: list[dict], where: str) -> str:
    bad = [r for r in rows if r["leaks"]]
    if not rows:
        return f"{where}: no facts, so nothing to check"
    return f"{where}: {len(rows)} fact(s) · {len(bad)} name a customer or carry a figure"


def coverage(rows: list[dict], story: dict) -> list[str]:
    have = {k for r in rows for k in r["lessons"]}
    return [p["key"] for p in story["patterns"] if p["key"] in have]


# --------------------------------------------------------------------------- step 2: setup

def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`; pass it only when the installed CLI still accepts it.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def find_actor(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("actor", "get", cid(key), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def bind_actor(cli: CLI, actor_id: str) -> bool:
    """Bind to the workspace; returns True when this call bound it (False: it already was)."""
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
        return True
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        return False


def is_bound(cli: CLI, actor_id: str) -> bool:
    # A sealed (unbound) actor answers 404 to `fact list`: that is the check, and it is step 6's point.
    rc, out, err = cli.raw("fact", "list", "--actors", actor_id, "--page-size", "1", scoped=True, echo=False)
    if rc == 0:
        return True
    if "404" in err or "not bound" in err.lower() or "not found" in err.lower():
        return False
    raise CLIError(["fact", "list", "--actors", actor_id], rc, out, err)


def ensure_person(cli: CLI, p: dict, rebind: bool) -> str:
    actor = find_actor(cli, p["custom_id"])
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(p["custom_id"]), "--display-name", p["display"],
                        *actor_type_args(cli, "HUMAN"), "--tags", "demo,support", "--description", p["role"])
        note(f"created actor {p['display']} → {actor['id']}")
        bind_actor(cli, actor["id"])
    else:
        note(f"actor {p['display']} already exists → {actor['id']}")
        if rebind:
            bind_actor(cli, actor["id"])
    return actor["id"]


def find_agent(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("agent", "get", cid(key), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def ensure_agent(cli: CLI, a: dict) -> dict:
    agent = find_agent(cli, a["custom_id"])
    if agent is None:
        agent = cli.run("agent", "create", "--name", a["name"], "--custom-id", cid(a["custom_id"]),
                        "--description", a["description"])
        note(f"created the agent {a['name']} → {agent['id']}, with its own actor {agent['actor_id']} "
             f"(its memory is read with `fact list --agents` and searched with `search --actors {agent['actor_id']}`)")
    else:
        note(f"agent {a['name']} already exists → {agent['id']}")
    try:
        cli.run("agent", "bind", agent["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return agent


def find_project(cli: CLI, echo: bool = True):
    try:
        return cli.run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def ensure_project(cli: CLI) -> dict:
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']}")
    return proj


def get_instruction(cli: CLI, agent_id: str, echo: bool = True) -> str:
    res = cli.run("fact", "instruction", "get", "--agent", agent_id, scoped=True, echo=echo) or {}
    return res.get("fact_instruction") or ""


def handoff_done(cli: CLI, story: dict) -> bool:
    conv = find_conv(cli, "handoff-relay", echo=False)
    return bool(conv) and len(list_messages(cli, conv["id"], echo=False)) >= len(story["handoff"]["turns"])


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting the conversations, the agents' memories, the agents, the project and both people, then starting over")
        cleanup(cli, quiet=True)
    ids = {"project": ensure_project(cli)["id"]}
    for p in story["people"]:
        # Maya is sealed in step 6. A re-run that finds her sealed leaves her that way: she has left.
        ids[p["key"]] = ensure_person(cli, p, rebind=p["key"] != "maya")
    for a in story["agents"]:
        ag = ensure_agent(cli, a)
        ids[a["key"]], ids[a["key"] + "_actor"] = ag["id"], ag["actor_id"]

    mine = load_instruction()
    before = get_instruction(cli, ids["relay"])
    note("Relay's instruction: " + ("set by an earlier run" if before else "empty → MemoryLake's built-in default"))
    if before.strip() != mine.strip():
        cli.run("fact", "instruction", "set", "--agent", ids["relay"],
                "--file", os.path.relpath(DATA / "relay-instruction.md"), scoped=True)
    saved = get_instruction(cli, ids["relay"])
    if saved.strip() != mine.strip():
        die("Relay's saved instruction differs from data/relay-instruction.md")
    other = get_instruction(cli, ids["default"])
    if other:
        die("the comparison agent has an instruction; run with --reset")
    emit("text", "\n  Relay's fact instruction, read back with `fact instruction get --agent`:\n\n"
         + "\n".join("    " + l for l in saved.strip().splitlines())
         + f"\n\n  {agent_def(story, 'default')['name']}: `fact instruction get --agent` → \"\" (built-in default)")
    emit("setup", "", project=dict(id=ids["project"], **PROJECT),
         people=[dict(p, id=ids[p["key"]]) for p in story["people"]],
         agents=[dict(a, id=ids[a["key"]], actor_id=ids[a["key"] + "_actor"]) for a in story["agents"]],
         instruction=saved)
    return ids


# --------------------------------------------------------------------------- step 3: the handoff, told to both agents

def find_conv(cli: CLI, name: str, echo: bool = True):
    try:
        return cli.run("conv", "get", cid(name), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def list_messages(cli: CLI, conv_id: str, echo: bool = True) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None) or {}
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return sorted(msgs, key=lambda m: m.get("sequence_no") or 0)


def ensure_conv(cli: CLI, name: str, title: str, project: str, actors: list[str], meta: list[str]) -> dict:
    conv = find_conv(cli, name)
    if conv is None:
        args = ["conv", "create", "--custom-id", cid(name), "--project", project, "--actors", ",".join(actors),
                "--kind", "DIRECT", "--name", title]
        for m in meta:
            args += ["--metadata", m]
        conv = cli.run(*args, scoped=True)
    return conv


def append_turns(cli: CLI, conv: dict, turns: list, speaker_ids: dict, date: str, done: int, where: str) -> int:
    start = datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(timezone.utc)
    n = 0
    for i, (who, words) in enumerate(turns, start=1):
        if i <= done:
            continue
        ts = (start + timedelta(minutes=2 * (i - 1))).strftime("%Y-%m-%dT%H:%M:%SZ")
        cli.run("conv", "msg", "append", conv["id"], "--actor", speaker_ids[who], "--custom-id", f"msg-{i:02d}",
                "--timestamp", ts, "--text", words, scoped=True, echo=n == 0)
        n += 1
        emit("turn", "", where=where, index=i, who=who, line=words)
    if n > 1:
        note(f"… and {n - 1} more message(s) appended the same way")
    return n


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> int:
    t0 = last = time.time()
    pending, first = list(conv_ids), True
    while pending and time.time() - t0 < timeout:
        for c in list(pending):
            try:
                status = cli.run("conv", "cook-status", c, scoped=True, echo=first) or {}
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                status = {}
            if status.get("cook_finished"):
                pending.remove(c)
                emit("cooked", "", id=c, seconds=int(time.time() - t0))
        first = False
        if not pending:
            break
        if time.time() - last >= 30:
            emit("progress", f"  … still turning the conversation into memory ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
            last = time.time()
        time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} conversation(s) still processing server-side")
    else:
        note(f"memory ready in {int(time.time() - t0)}s")
    return int(time.time() - t0)


def handoff(cli: CLI, ids: dict, story: dict) -> dict:
    h = story["handoff"]
    total = len(h["turns"])
    convs, appended = {}, 0
    for key in ("relay", "default"):
        a = agent_def(story, key)
        speaker = {"maya": ids["maya"], "relay": ids[key + "_actor"]}
        # A DIRECT conversation between Maya and the agent's own actor, in the support project.
        conv = ensure_conv(cli, f"handoff-{key}", f"{h['title']} → {a['name']}", ids["project"],
                           [ids["maya"], ids[key + "_actor"]], [f"handoff_from={person(story, 'maya')['display']}", f"agent={key}"])
        done = len(list_messages(cli, conv["id"]))
        emit("handoff", "", where=key, id=conv["id"], agent=a["name"], done=done, total=total)
        if done < total and not is_bound(cli, ids["maya"]):
            die("Maya is sealed but her handoff is incomplete; run with --reset")
        if done:
            note(f"the conversation with {a['name']} already holds {done} of {total} message(s)")
        appended += append_turns(cli, conv, h["turns"], speaker, h["date"], done, key)
        convs[key] = conv["id"]
    waited = wait_for_memory(cli, list(convs.values())) if appended else 0
    if not appended:
        note("the handoff was told by an earlier run; reading the memories as they are")
    if find_conv(cli, "handoff-relay-again", echo=False) is None and not list_facts(cli, "--agents", ids["relay"], echo=False) \
            and is_bound(cli, ids["maya"]):
        # Seen in testing elsewhere (a whole extraction that records nothing). Say so and tell it once more.
        note("Relay recorded nothing from this handoff — telling it once more, in a new conversation")
        conv = ensure_conv(cli, "handoff-relay-again", f"{h['title']} → Relay (again)", ids["project"],
                           [ids["maya"], ids["relay_actor"]], ["agent=relay", "retold=yes"])
        append_turns(cli, conv, h["turns"], {"maya": ids["maya"], "relay": ids["relay_actor"]}, h["date"], 0, "relay-again")
        wait_for_memory(cli, [conv["id"]])
        convs["relay-again"] = conv["id"]
    return dict(convs=convs, waited=waited, appended=appended)


# --------------------------------------------------------------------------- step 4: same words, three memories

def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", flag, scope_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            break
    return facts


def three_memories(cli: CLI, ids: dict, story: dict) -> dict:
    relay = rows_of(list_facts(cli, "--agents", ids["relay"]), story)
    default = rows_of(list_facts(cli, "--agents", ids["default"]), story)
    project = rows_of(list_facts(cli, "--projects", ids["project"]), story)
    maya_bound = is_bound(cli, ids["maya"])
    maya = rows_of(list_facts(cli, "--actors", ids["maya"]), story) if maya_bound else None
    n = len(story["patterns"])

    def block(title: str, rows: list[dict], judge: bool) -> list[str]:
        out = [f"\n  {title}"]
        for r in rows:
            mark = ("✗" if r["leaks"] else "✓") if judge else "•"
            out.append(f"   {mark} {r['fact']}")
            if r["leaks"]:
                out.append(f"       ↳ {', '.join(r['leaks'])}")
        if not rows:
            out.append("   (nothing)")
        return out

    rc, dc, pc = coverage(relay, story), coverage(default, story), coverage(project, story)
    lines = block(f"Relay — the agent's own memory, under its instruction ({len(relay)} fact(s)):", relay, True)
    lines += block(f"{agent_def(story, 'default')['name']} — same words, built-in default ({len(default)} fact(s)):", default, False)
    lines += block(f"The support project the conversations live in ({len(project)} fact(s)):", project, False)
    lines.append("")
    lines.append(f"  Maya's {n} lessons found in each memory:")
    for p in story["patterns"]:
        lines.append(f"   {p['label']:<50} Relay {'✓' if p['key'] in rc else '·'}   default {'✓' if p['key'] in dc else '·'}   project {'✓' if p['key'] in pc else '·'}")
    lines.append(f"   {'':<50} {len(rc)}/{n}     {len(dc)}/{n}       {len(pc)}/{n}")
    lines.append("")
    lines.append("  " + scan_line(relay, "Relay  "))
    lines.append("  " + scan_line(default, "default"))
    lines.append("  " + scan_line(project, "project") + "   ← the project keeps the customers")
    if maya is not None:
        lines.append(f"  also on Maya's own actor: {len(maya)} fact(s) (her role, that she is leaving)")
    else:
        lines.append("  Maya's own actor: sealed by an earlier run (step 6), so its memory cannot be read")
    removed = []
    for r in relay:
        if r["leaks"]:
            # The instruction is a prompt to the extractor, not a filter. Whatever slips through is removed.
            cli.run("fact", "delete", r["id"], "--agent", ids["relay"], scoped=True)
            removed.append(r)
    if removed:
        lines.append(f"  removed {len(removed)} Relay fact(s) the scan flagged — the instruction steers extraction, it does not guarantee it")
    emit("text", "\n".join(lines))
    res = dict(relay=relay, default=default, project=project, maya=maya, removed=removed,
               coverage=dict(relay=rc, default=dc, project=pc, total=n),
               leaky=dict(relay=sum(bool(r["leaks"]) for r in relay), default=sum(bool(r["leaks"]) for r in default),
                          project=sum(bool(r["leaks"]) for r in project)))
    emit("memories", "", **res)
    return res


# --------------------------------------------------------------------------- step 5: provenance

def provenance(cli: CLI, ids: dict, story: dict, write: bool = True) -> dict:
    names = {ids["maya"]: person(story, "maya")["display"], ids["tomas"]: person(story, "tomas")["display"]}
    by_id = {}
    for key in ("handoff-relay", "handoff-relay-again", "outcome"):
        conv = find_conv(cli, key, echo=False)
        if conv:
            for i, m in enumerate(list_messages(cli, conv["id"]), start=1):
                by_id[m["id"]] = dict(m, n=i, conv_name=key, conv=conv["id"])
    rows = []
    for f in list_facts(cli, "--agents", ids["relay"], echo=False):
        t = cli.run("fact", "trace", f["id"], "--agent", ids["relay"], scoped=True) or {}
        entries = t.get("trace") or []
        src_ids = sorted({s for e in entries for s in (e.get("source_entry_ids") or [])})
        src = sorted((by_id[s] for s in src_ids if s in by_id), key=lambda m: (m["conv_name"], m["n"]))
        kinds = sorted({e.get("source_kind") or "?" for e in entries})

        def who(m: dict) -> str:
            if m.get("agent_id"):
                return "Relay"
            return names.get(m.get("actor_id") or "", m.get("actor_id") or "?")

        speakers = sorted({who(m) for m in src})
        rows.append(dict(id=f["id"], fact=f["fact"], kinds=kinds, conv=sorted({m["conv_name"] for m in src}),
                         messages=[m["n"] for m in src], speakers=speakers, at=(src[-1].get("timestamp") or "") if src else "",
                         lessons=lessons_in(f["fact"], story)))
    lines = []
    for r in rows:
        where = f"{'/'.join(r['conv'])} msg {', '.join(map(str, r['messages']))}" if r["messages"] else "no message (added by hand)"
        lines.append(f"   {r['fact']}\n     → {where} · {', '.join(r['speakers']) or '?'} · {'/'.join(r['kinds'])}")
    batch = [r for r in rows if r["messages"]]
    from_maya = [r for r in batch if "Maya Lindqvist" in r["speakers"]]
    lines.append(f"\n  {len(rows)} lesson(s) on Relay; {len(from_maya)} trace to a batch with Maya's messages in it.")
    lines.append("  A source is the whole batch extraction read (`source_entry_ids`), so Relay's own questions show up beside")
    lines.append("  her answers; the lessons themselves are in her messages.")
    emit("text", "\n".join(lines) if rows else "\n  Relay holds no lessons to trace.")
    if write:
        OUT.mkdir(exist_ok=True)
        md = ["# Relay — where each escalation lesson came from", ""]
        for r in rows:
            md.append(f"- {r['fact']}  \n  `{r['id']}` · {'/'.join(r['conv'])} messages {r['messages']} · {', '.join(r['speakers'])} · {'/'.join(r['kinds'])}")
        (OUT / "relay-provenance.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        (OUT / "relay-provenance.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        note(f"written to {os.path.relpath(OUT / 'relay-provenance.md')} and .json")
    res = dict(rows=rows, from_maya=len(from_maya))
    emit("provenance", "", **res)
    return res


# --------------------------------------------------------------------------- step 6: Maya leaves

def attempt(cli: CLI, *args: str) -> dict:
    """Run a command that is expected to fail; return its status line instead of raising."""
    rc, out, err = cli.raw(*args, scoped=True)
    m = re.search(r"HTTP (\d{3})[^\n]*", err)
    msg = re.search(r'"message":"([^"]+)"', err)
    last = (err.strip().splitlines() or [""])[-1]
    return dict(rc=rc, status=int(m.group(1)) if m else None, message=(msg.group(1) if msg else last) if rc else "")


def departure(cli: CLI, ids: dict, story: dict) -> dict:
    maya = person(story, "maya")
    before = len(list_facts(cli, "--agents", ids["relay"], echo=False))
    if is_bound(cli, ids["maya"]):
        note(f"{maya['display']}'s last day: her actor is sealed — unbound from the workspace, not deleted")
        cli.run("actor", "unbind", "--actor", ids["maya"], scoped=True)
        earlier = False
    else:
        note(f"{maya['display']} was sealed by an earlier run")
        earlier = True
    fl = attempt(cli, "fact", "list", "--actors", ids["maya"])
    se = attempt(cli, "search", "escalation", "--actors", f"{ids['relay_actor']},{ids['maya']}")
    after = list_facts(cli, "--agents", ids["relay"])
    hits = cli.run("search", "where does a mobile app crash go", "--actors", ids["relay_actor"], "--types", "fact",
                   "--top-k", "3", scoped=True) or {}
    top = [h.get("fact") or "" for h in hits.get("facts") or []]

    def line(r: dict) -> str:
        return f"HTTP {r['status']} {r['message']}" if r["rc"] else "still answers"

    lines = [f"\n  Maya's own memory:  fact list --actors → {line(fl)}",
             f"                     a search that names her → {line(se)}",
             f"  Relay's memory:     {before} lesson(s) before · {len(after)} after"
             + ("  ✓ unchanged" if len(after) == before else ""),
             "  search --actors <Relay> “where does a mobile app crash go”:"]
    lines += [f"   {i}. {t}" for i, t in enumerate(top, 1)] or ["   (nothing)"]
    lines.append("  Sealing is reversible (`actor bind` restores her memory as it was); deleting her actor would not remove her")
    lines.append("  facts either — they stay readable and can no longer be deleted — so the demo's cleanup deletes facts first.")
    emit("text", "\n".join(lines))
    res = dict(earlier=earlier, fact_list=fl, search=se, before=before, after=len(after), top=top)
    emit("departure", "", **res)
    return res


# --------------------------------------------------------------------------- step 7: Tomás's first escalation

def search_agent(cli: CLI, actor_id: str, q: str, top_k: int = 5, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--actors", actor_id, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    return [dict(id=f.get("id"), fact=f.get("fact") or "") for f in res.get("facts") or []]


def on_topic(text: str, topic: list[str]) -> bool:
    t = (text or "").lower()
    return any(w.lower() in t for w in topic)


def ask(cli: CLI, ids: dict, story: dict, key: str, item: dict, echo: bool = True) -> dict:
    hits = search_agent(cli, ids[key + "_actor"], item["q"], 5, echo=echo)
    shown = [dict(h, lessons=lessons_in(h["fact"], story), leaks=leaks(h["fact"], story))
             for h in hits if on_topic(h["fact"], item["topic"])]
    found = sorted({k for h in shown for k in h["lessons"] if k in item["expect"]})
    return dict(q=item["q"], expect=item["expect"], hits=shown, left_out=len(hits) - len(shown), found=found,
                ok=item["expect"][0] in found)


def escalation(cli: CLI, ids: dict, story: dict) -> dict:
    e = story["escalation"]
    tomas = person(story, e["by"])
    note(f"{tomas['display']} takes the ticket alone: {e['ticket']}")
    answers, lines = [], []
    for item in e["questions"]:
        a = ask(cli, ids, story, "relay", item)
        lines.append(f"\n  Q: {item['q']}")
        for h in a["hits"]:
            lines.append(f"   {'✓' if any(k in item['expect'] for k in h['lessons']) else '·'} {h['fact']}")
        if not a["hits"]:
            lines.append("   (no lesson on this in Relay's memory)")
        if a["left_out"]:
            lines.append(f"   ({a['left_out']} other hit(s) left out: search always returns its closest facts, related or not)")
        lines.append(f"   expected: {label(story, item['expect'][0])} → {'✓ found' if a['ok'] else '✗ not found'}")
        answers.append(a)
    # The same questions against the comparison agent's memory — quietly, one line each.
    contrast = [ask(cli, ids, story, "default", item, echo=False) for item in e["questions"]]
    ok = sum(a["ok"] for a in answers)
    lines.append(f"\n  Relay's memory answered {ok} of {len(answers)} · the built-in-default agent's memory: "
                 f"{sum(c['ok'] for c in contrast)} of {len(contrast)}")
    named = [h for a in answers for h in a["hits"] if h["leaks"]]
    lines.append(f"  {sum(len(a['hits']) for a in answers)} lesson(s) shown · {len(named)} name a customer or carry a figure")
    emit("text", "\n".join(lines))
    res = dict(ticket=e["ticket"], by=tomas["display"], answers=answers, ok=ok,
               contrast=[dict(q=c["q"], ok=c["ok"], hits=len(c["hits"])) for c in contrast])
    emit("escalation", "", **res)
    return res


# --------------------------------------------------------------------------- step 8: the outcome

def outcome(cli: CLI, ids: dict, story: dict) -> dict:
    o = story["outcome"]
    before = {f["id"]: f.get("fact") or "" for f in list_facts(cli, "--agents", ids["relay"], echo=False)}
    conv = ensure_conv(cli, "outcome", o["title"], ids["project"], [ids["tomas"], ids["relay_actor"]], ["agent=relay", "by=tomas"])
    done = len(list_messages(cli, conv["id"]))
    emit("outcome_conv", "", id=conv["id"], done=done, total=len(o["turns"]))
    n = append_turns(cli, conv, o["turns"], {"tomas": ids["tomas"], "relay": ids["relay_actor"]}, o["date"], done, "outcome")
    if n:
        wait_for_memory(cli, [conv["id"]])
    else:
        note("the outcome was told by an earlier run")
    relay = rows_of(list_facts(cli, "--agents", ids["relay"]), story)
    # The outcome can add a lesson or revise one Relay already had (same fact id, new text): show both.
    new = [dict(r, change="updated" if r["id"] in before else "added") for r in relay
           if r["id"] not in before or before[r["id"]] != r["fact"]] if n \
        else [dict(r, change="earlier run") for r in relay if o["pattern"]["key"] in r["lessons"]]
    lines = [f"\n  What Tomás's outcome changed on Relay ({len(new)}):" if n else "\n  Relay's lesson from the outcome (recorded by an earlier run):"]
    for r in new:
        lines.append(f"   {'✗' if r['leaks'] else '✓'} [{r['change']}] {r['fact']}" + (f"\n       ↳ {', '.join(r['leaks'])}" if r["leaks"] else ""))
        if r["change"] == "updated":
            lines.append(f"       was: {before[r['id']]}")
    if not new:
        lines.append("   (nothing changed — extraction varies; the lessons Relay already had still apply)")
    got = any(o["pattern"]["key"] in r["lessons"] for r in relay)
    lines.append(f"\n  expected: {o['pattern']['label']} → {'✓ on Relay' if got else '✗ not recorded this time'}")
    removed = []
    for r in relay:
        if r["leaks"]:
            cli.run("fact", "delete", r["id"], "--agent", ids["relay"], scoped=True)
            removed.append(r)
    after = rows_of(list_facts(cli, "--agents", ids["relay"], echo=False), story) if removed else relay
    lines.append("  " + scan_line(relay, "Relay") + (f" → removed {len(removed)}" if removed else ""))
    for r in removed:
        lines.append(f"   ✗ removed: {r['fact']}\n       ↳ {', '.join(r['leaks'])}")
    project_new = [r for r in rows_of(list_facts(cli, "--projects", ids["project"], echo=False), story) if r["leaks"]]
    lines.append(f"  the support project, for contrast: {len(project_new)} fact(s) name a customer or carry a figure")
    emit("text", "\n".join(lines))
    res = dict(conv=conv["id"], appended=n, new=new, got=got, removed=removed, relay_total=len(after),
               project_leaky=len(project_new))
    emit("outcome", "", **res)
    return res


# --------------------------------------------------------------------------- cleanup

def wipe_facts(cli: CLI, flag: str, scope_id: str, quiet: bool) -> int:
    """Delete every fact in one scope, one call per id; returns how many were deleted."""
    single = {"--agents": "--agent", "--actors": "--actor"}[flag]
    facts = list_facts(cli, flag, scope_id, echo=not quiet)
    for f in facts:
        cli.run("fact", "delete", f["id"], single, scope_id, scoped=True, echo=False)
    left = list_facts(cli, flag, scope_id, echo=False)
    if left:
        die(f"{len(left)} fact(s) are still on {scope_id}; not deleting it (its memory would become undeletable)")
    return len(facts)


def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find_project(cli, echo=not quiet)
    for name in ("handoff-relay", "handoff-default", "handoff-relay-again", "outcome"):
        conv = find_conv(cli, name, echo=False) if proj else None
        if conv:
            # `proj delete` does not delete conversations; remove them explicitly.
            cli.run("conv", "delete", conv["id"], scoped=True)
    if proj:
        note("deleted the conversations")
    for a in story["agents"]:
        agent = find_agent(cli, a["custom_id"], echo=not quiet)
        if agent:
            # A deleted agent's memory stays readable but can no longer be deleted: facts first.
            n = wipe_facts(cli, "--agents", agent["id"], quiet)
            cli.run("agent", "delete", agent["id"])
            note(f"deleted {n} fact(s) from {a['name']}'s memory{' (its instruction goes with the agent)' if a['instruction'] else ''}, then the agent")
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the support project and its memory")
    for p in story["people"]:
        actor = find_actor(cli, p["custom_id"], echo=not quiet)
        if actor:
            # Same rule for people: a deleted actor's facts stay readable and undeletable. A sealed
            # actor's facts cannot be listed, so bind it back first.
            bind_actor(cli, actor["id"])
            n = wipe_facts(cli, "--actors", actor["id"], quiet)
            cli.run("actor", "delete", actor["id"])
            note(f"deleted {n} fact(s) on {p['display']}, then the actor")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    banner(2, TOTAL, "Set up — Maya, Tomás, the support project, Relay with its own instruction, a default agent to compare")
    ids = setup(cli, story, reset)

    banner(3, TOTAL, "Maya's handoff — the same words, told to both agents")
    handoff(cli, ids, story)

    banner(4, TOTAL, "Same words, three memories — Relay, the default agent, the project")
    three_memories(cli, ids, story)

    banner(5, TOTAL, "Provenance — every lesson on Relay traced to the message it came from")
    provenance(cli, ids, story)

    banner(6, TOTAL, "Maya leaves — her actor is sealed; Relay's memory stays")
    departure(cli, ids, story)

    banner(7, TOTAL, "Tomás's first escalation — routed from Relay's memory")
    result = escalation(cli, ids, story)

    banner(8, TOTAL, "The outcome — Tomás's lesson joins Relay's memory, the customer does not")
    outcome(cli, ids, story)
    return result


def lookup_ids(cli: CLI, story: dict) -> dict:
    ids = {}
    proj = find_project(cli)
    if proj:
        ids["project"] = proj["id"]
    for p in story["people"]:
        a = find_actor(cli, p["custom_id"])
        if a:
            ids[p["key"]] = a["id"]
    for a in story["agents"]:
        ag = find_agent(cli, a["custom_id"])
        if ag:
            ids[a["key"]], ids[a["key"] + "_actor"] = ag["id"], ag["actor_id"]
    if "relay" not in ids:
        die("Relay does not exist yet; run `python3 demo.py` first")
    return ids


def show_memory(cli: CLI) -> None:
    story = load_story()
    ids = lookup_ids(cli, story)
    instr = get_instruction(cli, ids["relay"])
    emit("text", "\n  Relay's instruction:\n\n" + "\n".join("    " + l for l in (instr.strip() or "(empty: built-in default)").splitlines()))
    rows = rows_of(list_facts(cli, "--agents", ids["relay"]), story)
    emit("text", "\n  Relay's memory:\n" + "\n".join(f"   {'✗' if r['leaks'] else '✓'} {r['fact']}" for r in rows)
         + "\n\n  " + scan_line(rows, "Relay"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "audit", "cleanup"],
                    help="run = full demo (default); memory = Relay's instruction, lessons and leak scan; "
                         "audit = provenance of every lesson; cleanup = delete everything")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = {"cleanup": 2, "memory": 2, "audit": 2}.get(args.command, TOTAL)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "memory":
        banner(2, 2, "Relay's memory and its leak scan")
        show_memory(cli)
        return
    if args.command == "audit":
        banner(2, 2, "Provenance — every lesson on Relay traced to its message")
        provenance(cli, lookup_ids(cli, load_story()), load_story())
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py memory` shows Relay's memory again; `python3 demo.py audit` traces every lesson;"
          " `python3 demo.py cleanup` removes the demo data.")


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

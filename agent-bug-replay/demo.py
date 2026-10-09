#!/usr/bin/env python3
"""
Reproduce an AI agent's bug by replaying its memory — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Quillstone makes lab label printers.  Its support agent, Quill, keeps one memory
per customer account in MemoryLake.  Lena Fischer (Brightmoor Labs) talked to
Quill four times:

  * 2026-09-20  the QL-40 jams with E-41; "ship any hardware to our Lyon office";
  * 2026-10-01  "we are moving out of Lyon; keep shipping there until the end of October";
  * 2026-10-03  the printer jams again.  Quill calls its `memory_search` tool,
                then ships a replacement to the address it found;
  * 2026-10-05  "the replacement went to Lyon, but the office closed on October 2".

A support engineer picks up the ticket today.  Asking the agent again does not
reproduce anything: its memory has moved on.  The demo:

  * stores the agent's tool call and the exact facts it got back as TOOL_USE /
    TOOL_RESULT blocks, the way an agent framework hands them over;
  * corrects the memory by hand after the complaint (`fact delete`, `fact update`);
  * re-runs the agent's own search today — and gets a different answer;
  * pins memory to the moment of the tool call: `fact trace` gives every fact's
    full history (ADD / UPDATE / FORGET, from which conversation and message, or
    by hand), and each history entry is dated by the messages it came from;
  * checks the rebuilt memory against what the agent recorded at the time, fact
    by fact, and shows the diff between then and now with a source for every line;
  * writes the replay down as an audit record (out/replay-….json).  Nothing is
    written back to the memory.

Everything the script does is a plain CLI command, echoed as it runs, so you
can copy any line into your own shell.  The companion web app (web/server.py)
drives the same functions and streams the same events to a browser.

Requirements: Python 3.9+, the memorylake CLI (v20261009 or newer) on PATH, and a MemoryLake API key.
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
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-abr"  # memorylake-usecases / agent-bug-replay; keeps demo ids apart from yours

ACCOUNT = dict(key="brightmoor", display="Brightmoor Labs")
PROJECT = dict(custom_id=f"{PREFIX}-brightmoor", name="Brightmoor Labs — support account",
               description="Quill's memory of the Brightmoor Labs account. Demo data.")

# The customer and the support agent.  The agent is an ASSISTANT actor when the CLI can still say so.
PEOPLE = {
    "lena": dict(display="Lena Fischer", type="HUMAN", side="customer", tags="customer:brightmoor",
                 role="Operations lead, Brightmoor Labs"),
    "quill": dict(display="Quill", type="ASSISTANT", side="vendor", tags="quillstone,ai",
                  role="Quillstone support agent (AI)"),
}
ENGINEER = dict(display="Sam Okafor", role="Support engineer, Quillstone")

# The two shipping addresses in the story.  "Which address would the agent use" = the first street
# named in the first fact (in search order) that names one.
STREETS = [("22 Quai Perrache", "22 Quai Perrache, 69002 Lyon"),
           ("9 Rue Felix Viallet", "9 Rue Felix Viallet, 38000 Grenoble")]
OLD_STREET, NEW_STREET = STREETS[0][0], STREETS[1][0]
# --------------------------------------------------------------------------- output sink
#
# Everything the demo says goes through `emit`.  The CLI runner prints; the web app
# replaces EMIT with a function that streams the same events to the browser.

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
    # "call", "turn", "facts", "brief" carry structured data for the web app; the text
    # versions of the same information are emitted alongside them.


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
    """Thin wrapper: runs `memorylake …`, echoes the command, parses the JSON reply."""

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False):
        self.binary = binary
        self.env = env
        self.show_json = show_json
        self.workspace: str | None = None
        self.secret: str | None = None

    def _echo(self, args: list[str]) -> None:
        shown = list(args)
        if self.secret:
            shown = [a.replace(self.secret, "sk-…") for a in shown]
        emit("cmd", shlex.join(["memorylake", *shown]))

    def raw(self, *args: str, scoped: bool = False, echo: bool = True) -> tuple[int, str, str]:
        argv = [str(a) for a in args]
        if scoped and self.workspace:
            argv += ["--workspace", self.workspace]
        if echo:
            self._echo(argv)
        proc = subprocess.run([self.binary, *argv], env=self.env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    # Read-only subcommands are safe to repeat when the network hiccups.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504")

    def run(self, *args: str, scoped: bool = False, echo: bool = True):
        """Run and return the parsed JSON (or raw text when the reply is not JSON).

        A read-only command that fails on a transient network error (a dropped TLS handshake,
        a reset connection, a gateway timeout) is retried a few times with a short backoff
        instead of aborting the whole demo. Writes are never repeated here."""
        retryable = any(str(a) in self.READ_ONLY for a in args[:3])
        attempt = 0
        while True:
            rc, out, err = self.raw(*args, scoped=scoped, echo=echo and attempt == 0)
            if rc == 0:
                break
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup ({err.strip().splitlines()[-1] if err.strip() else 'connect'}); retry {attempt}/4 in {2 * attempt}s")
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
        """Like run(), but returns None when the server says the thing does not exist."""
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
    """Log in (isolated profile when a key is given) and pick a workspace."""
    binary = find_binary()
    env = dict(os.environ)
    api_key = (api_key or env.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)

    if api_key:
        # Keep the demo's credentials in its own config dir so your ~/.memorylake is untouched.
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

    # Workspace: explicit → env var → the one remembered by `ws use` → the first one listed.
    ws = (workspace or env.get("MEMORYLAKE_WORKSPACE", "")).strip()
    ws_name = ""
    if not ws:
        rc, out, _ = cli.raw("ws", "current")
        m = re.search(r"\b(ws-[0-9a-f]+)\b", out) if rc == 0 else None
        ws = m.group(1) if m else ""
    if not ws:
        listing = cli.run("ws", "list", "--page-size", "1")
        items = listing.get("items") or []
        if not items:
            die("this account has no workspace; create one with `memorylake ws create --name … --custom-id …`")
        ws, ws_name = items[0]["id"], items[0]["name"]
        note(f"using workspace “{ws_name}” ({ws})")
    else:
        note(f"using workspace {ws}")
    cli.workspace = ws
    cli.workspace_name = ws_name  # type: ignore[attr-defined]
    return cli



def require_history_commands(cli: CLI) -> None:
    """`fact trace`, `conv fact-actions` and `fact update` arrived in CLI v20261009."""
    for cmd in (("fact", "trace"), ("conv", "fact-actions"), ("fact", "update")):
        rc, _, _ = cli.raw(*cmd, "--help", echo=False)
        if rc != 0:
            die(f"this demo needs `memorylake {' '.join(cmd)}`, which your CLI does not have.\n"
                "  upgrade: curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh")


# --------------------------------------------------------------------------- step 2: setup

def bind(cli: CLI, actor_id: str) -> None:
    # Actors are account-wide; they must be bound to the workspace to take part in it.
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")


def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`, but the server still honours the type: an ASSISTANT
    # actor keeps (almost) no facts, a HUMAN one picks up facts from the conversations it is in.
    # Pass the type whenever the installed CLI still accepts it.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    if not cli.has_actor_type and actor_type != "HUMAN":  # type: ignore[attr-defined]
        note(f"this CLI cannot set an actor type, so this {actor_type} actor is created untyped (server default HUMAN)")
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def ensure_actor(cli: CLI, key: str, spec: dict) -> str:
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", spec["display"],
                        *actor_type_args(cli, spec["type"]), "--tags", spec["tags"], "--description", spec["role"])
        note(f"created actor {spec['display']} → {actor['id']}")
    else:
        note(f"actor {spec['display']} already exists → {actor['id']}")
    bind(cli, actor["id"])
    emit("actor", "", key=key, id=actor["id"], display=spec["display"], role=spec["role"], type=spec["type"],
         side=spec["side"])
    return actor["id"]


def find_actor_ids(cli: CLI) -> dict[str, str]:
    found = {k: cli.try_run("actor", "get", f"{PREFIX}-{k}", "--by-custom-id") for k in PEOPLE}
    missing = [k for k, a in found.items() if a is None]
    if missing:
        die(f"the demo actor(s) {', '.join(missing)} do not exist yet; run `python3 demo.py` first")
    return {k: a["id"] for k, a in found.items()}


def load_sessions() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "sessions").glob("*.json"))]


def bug_session() -> dict:
    return next(s for s in load_sessions() if any("tool_use" in t for t in s["turns"]))


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def project_id_or_die(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        die("the demo project does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def delete_actors(cli: CLI) -> None:
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])


def ensure_project(cli: CLI, reset: bool) -> str:
    proj = find_project(cli)
    if reset and proj is not None:
        note("--reset: deleting the conversations, the account project and the demo actors")
        delete_conversations(cli)
        cli.run("proj", "delete", proj["id"], scoped=True)
        delete_actors(cli)
        proj = None
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    emit("project", "", key=ACCOUNT["key"], id=proj["id"], name=proj["name"])
    return proj["id"]


# --------------------------------------------------------------------------- memory helpers

class Scope:
    """The account's memory is two scopes: facts land on the project and on Lena's actor."""

    def __init__(self, project_id: str, lena_id: str):
        self.project_id, self.lena_id = project_id, lena_id

    def args(self, where: str) -> list[str]:
        return ["--project", self.project_id] if where == "project" else ["--actor", self.lena_id]

    def search_args(self) -> list[str]:
        return ["--projects", self.project_id, "--actors", self.lena_id]

    WHERE = ("project", "lena")


def list_facts(cli: CLI, scope: Scope, echo: bool = True) -> dict[str, dict]:
    """Every live fact of the account → {id: {fact, where}}."""
    out = {}
    for where, flag, ids in (("project", "--projects", scope.project_id), ("lena", "--actors", scope.lena_id)):
        token = None
        while True:
            args = ["fact", "list", flag, ids, "--page-size", "50"]
            if token:
                args += ["--continuation-token", token]
            page = cli.run(*args, scoped=True, echo=echo and token is None)
            for f in page.get("items") or []:
                out[f["id"]] = dict(id=f["id"], fact=f.get("fact") or "", where=where)
            token = page.get("continuation_token")
            if not token:
                break
    return out


def list_messages(cli: CLI, conv_id: str, echo: bool = True) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None)
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return sorted(msgs, key=lambda m: m.get("sequence_no") or 0)


def fact_actions(cli: CLI, conv_id: str, scope: Scope, where: str, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        args = ["conv", "fact-actions", conv_id, *scope.args(where), "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def wait_for_memory(cli: CLI, conv_id: str, timeout: int = 600) -> bool:
    """Used on resume only, when messages were stored by an earlier run that stopped before the cook finished."""
    t0 = time.time()
    first = True
    while time.time() - t0 < timeout:
        try:
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first)
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            status = {}
        first = False
        if status.get("cook_finished"):
            return True
        time.sleep(5)
    note(f"gave up after {timeout}s; still processing server-side")
    return False


def street_in(text: str) -> str | None:
    """The first of the story's streets named in a fact (the current value comes before any 'previously …')."""
    found = [(text.find(s), full) for s, full in STREETS if s in text]
    return min(found)[1] if found else None


def address_on_file(facts: list[dict]) -> tuple[str | None, dict | None]:
    """What the agent would ship to: the first fact, in search order, that names a street."""
    for f in facts:
        a = street_in(f.get("fact") or "")
        if a:
            return a, f
    return None, None


def short(fid: str) -> str:
    return fid.replace("fact-", "")[:8]


# --------------------------------------------------------------------------- step 3: the four conversations

def speaker_line(key: str, text: str) -> str:
    p = PEOPLE[key]
    return f"{p['display']} ({p['role']}): {text}"


def agent_search(cli: CLI, scope: Scope, query: str, top_k: int = 6) -> list[dict]:
    """The agent's `memory_search` tool: one MemoryLake search over the account's memory."""
    res = cli.run("search", query, *scope.search_args(), "--types", "fact", "--top-k", str(top_k), scoped=True)
    return [dict(id=f.get("id"), fact=f.get("fact") or "") for f in (res or {}).get("facts") or []]


def recorded_call(msgs: list[dict]) -> dict | None:
    """The TOOL_USE block and its TOOL_RESULT (paired by tool_call_id), as stored in the conversation."""
    call = None
    for m in msgs:
        for b in m.get("content") or []:
            if b.get("block_type") == "TOOL_USE":
                call = dict(id=b["tool_call_id"], name=b.get("tool_name"), arguments=b.get("arguments") or {},
                            at=m.get("timestamp") or "", message_id=m.get("id"))
            elif b.get("block_type") == "TOOL_RESULT" and call and b.get("tool_call_id") == call["id"]:
                call["facts"] = (b.get("result") or {}).get("facts") or []
    return call


def ingest_sessions(cli: CLI, scope: Scope, actors: dict[str, str]) -> None:
    recorded: list[dict] = []
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        done, msgs = 0, []
        if conv is not None and scope.project_id not in (conv.get("rw_project_ids") or []):
            note(f"“{session['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        emit("text", f"\n  ▸ {session['date'][:10]} · {session['label']}")
        if conv is None:
            conv = cli.run("conv", "create", "--custom-id", session["custom_id"], "--project", scope.project_id,
                           "--actors", ",".join(actors.values()), "--kind", "DIRECT", "--name", session["name"],
                           "--metadata", f"account={ACCOUNT['key']}", scoped=True)
        else:
            msgs = list_messages(cli, conv["id"])
            done = len(msgs)
            note(f"conversation exists with {done} message(s) → {conv['id']}")
            prev = recorded_call(msgs)
            if prev and "facts" in prev:
                recorded = prev["facts"]
        emit("session", "", custom_id=session["custom_id"], id=conv["id"], label=session["label"],
             name=session["name"], date=session["date"], turns=len(session["turns"]), done=done)
        start = datetime.fromisoformat(session["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        total = len(session["turns"])
        for i, turn in enumerate(session["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            args = ["conv", "msg", "append", conv["id"], "--actor", actors[turn["speaker"]],
                    "--custom-id", f"turn-{i:02d}", "--timestamp", ts]
            shown = turn.get("text", "")
            if "tool_use" in turn:
                tu = turn["tool_use"]
                # Field names matter: `tool_call_id` + `tool_name` are required, the input is `arguments`.
                blocks = [{"block_type": "TOOL_USE", "tool_call_id": tu["id"], "tool_name": tu["name"],
                           "arguments": tu["arguments"]}]
                emit("text", f"   Quill calls {tu['name']}({json.dumps(tu['arguments']['query'])})  [{tu['id']}]")
            elif "tool_result" in turn:
                # The agent's tool really runs here, against the memory as it is at this point of the story.
                tu = next(t["tool_use"] for t in session["turns"] if "tool_use" in t)
                recorded = agent_search(cli, scope, tu["arguments"]["query"])
                blocks = [{"block_type": "TOOL_RESULT", "tool_call_id": turn["tool_result"]["id"],
                           "result": {"facts": recorded}}]
                emit("text", "\n".join([f"   the tool returned {len(recorded)} fact(s); stored verbatim in the TOOL_RESULT:"]
                                       + [f"     {short(f['id'])}  {f['fact']}" for f in recorded]))
                emit("recorded", "", facts=recorded, call_id=turn["tool_result"]["id"])
            else:
                blocks = None
                if "{address}" in shown:
                    shown = shown.replace("{address}", address_on_file(recorded)[0] or "the address on file")
            if blocks is not None:
                args += ["--content-json", json.dumps(blocks, ensure_ascii=False)]
            else:
                args += ["--text", speaker_line(turn["speaker"], shown)]
            if parent:
                args += ["--parent", parent]
            last = i == total
            if last:
                # One conversation at a time: the next one must see this one's memory, as it did in real life.
                note("last message: --wait keeps polling until MemoryLake has updated the memory")
                args += ["--wait", "--timeout", "600"]
            t0 = time.time()
            parent = cli.run(*args, scoped=True)["id"]
            if last:
                note(f"memory ready in {int(time.time() - t0)}s")
                emit("cooked", "", id=conv["id"], seconds=int(time.time() - t0))
            emit("turn", "", custom_id=session["custom_id"], index=i, speaker=turn["speaker"], said=shown,
                 block="tool_use" if "tool_use" in turn else "tool_result" if "tool_result" in turn else "text")
            if blocks is None and "tool_use" not in turn and turn["speaker"] == "quill" and "{address}" in turn.get("text", ""):
                emit("text", f"   Quill: {shown}")
        if done >= total:
            wait_for_memory(cli, conv["id"])
            emit("cooked", "", id=conv["id"], seconds=0)
        # What this conversation did to the account's memory, straight from the server.
        counts = {}
        for where in Scope.WHERE:
            for a in fact_actions(cli, conv["id"], scope, where):
                counts[a["event"]] = counts.get(a["event"], 0) + 1
        summary = ", ".join(f"{n} {ev}" for ev, n in sorted(counts.items())) or "no change"
        emit("text", f"   memory changes from this conversation (conv fact-actions): {summary}")
        emit("actions", "", custom_id=session["custom_id"], counts=counts)


def show_memory(cli: CLI, scope: Scope) -> dict[str, dict]:
    facts = list_facts(cli, scope)
    lines = [f"\n  {ACCOUNT['display']}: {len(facts)} live fact(s)  (project + Lena's actor)"]
    lines += [f"   {short(f['id'])}  [{f['where']:<7}] {f['fact']}" for f in facts.values()]
    emit("text", "\n".join(lines))
    emit("memory", "", facts=list(facts.values()))
    return facts


# --------------------------------------------------------------------------- step 4: after the complaint

def fix_memory(cli: CLI, scope: Scope) -> dict:
    """Today, after the complaint: the support lead corrects the account's memory by hand."""
    facts = list_facts(cli, scope)
    # Forget the shipping instructions that still point at Lyon; keep what is true (e.g. "Lyon closed").
    forget = [f for f in facts.values() if street_in(f["fact"]) == STREETS[0][1]
              and re.search(r"\bship|\bsent\b|deliver", f["fact"], re.I) and not re.search(r"\bclosed\b", f["fact"], re.I)]
    billing = [f for f in facts.values() if re.search(r"billed annually|annual billing", f["fact"], re.I)]
    emit("text", f"\n  The Lyon address must never be used again: {len(forget)} fact(s) still say to ship there.")
    for f in forget:
        cli.run("fact", "delete", *scope.args(f["where"]), f["id"], scoped=True)
        emit("text", f"   ✗ forgot {short(f['id'])}  {f['fact']}")
    if not forget:
        note("nothing left to forget (an earlier run did it, or no fact kept the old street on its own)")
    updated = []
    emit("text", "\n  Finance moved Brightmoor to monthly billing last week; fixing that by hand too.")
    for f in billing:
        new = re.sub(r"billed annually", "billed monthly", f["fact"], flags=re.I)
        new = re.sub(r"annual billing", "monthly billing", new, flags=re.I)
        cli.run("fact", "update", f["id"], *scope.args(f["where"]), "--text", new, scoped=True)
        updated.append(dict(id=f["id"], old=f["fact"], new=new))
        emit("text", f"   ✎ {short(f['id'])}  {f['fact']}\n        → {new}")
    if not billing:
        note("no fact says annual billing any more (an earlier run fixed it, or it was not extracted)")
    out = dict(forgot=forget, updated=updated)
    emit("fixes", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: reproduce today

def find_bug_call(cli: CLI) -> tuple[dict, dict]:
    session = bug_session()
    conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
    if conv is None:
        die("the 2026-10-03 conversation does not exist yet; run `python3 demo.py` first")
    call = recorded_call(list_messages(cli, conv["id"]))
    if not call or "facts" not in call:
        die("the 2026-10-03 conversation has no recorded tool call yet; run `python3 demo.py` first")
    return conv, call


def reproduce_today(cli: CLI, scope: Scope, call: dict) -> dict:
    then_addr, _ = address_on_file(call["facts"])
    emit("text", f"\n  The ticket: on {call['at'][:10]} at {call['at'][11:16]} UTC Quill called "
                 f"{call['name']}({json.dumps(call['arguments'].get('query'))}) and shipped to {then_addr or '?'}.")
    emit("text", "  Re-running the agent's exact tool call against today's memory:")
    today = agent_search(cli, scope, call["arguments"].get("query", ""))
    now_addr, now_fact = address_on_file(today)
    live = list_facts(cli, scope, echo=False)
    lines = [f"     {short(f['id'])}  {f['fact']}" for f in today]
    seen = {f["id"]: f["fact"] for f in call["facts"]}
    changed = [i for i, t in seen.items() if i in live and live[i]["fact"] != t]
    gone = [i for i in seen if i not in live]
    lines += ["",
              f"   then (recorded in the TOOL_RESULT): ships to {then_addr or '— no address —'}",
              f"   today (same call):                  ships to {now_addr or '— no address —'}",
              f"   of the {len(seen)} fact(s) the agent saw: {len(seen) - len(changed) - len(gone)} unchanged, "
              f"{len(changed)} rewritten, {len(gone)} forgotten",
              "   → asking the agent again does not reproduce the bug: its memory has moved on."
              if then_addr != now_addr else "   → today's answer is the same as then."]
    emit("text", "\n".join(lines))
    out = dict(query=call["arguments"].get("query"), today=today, then_address=then_addr, now_address=now_addr,
               unchanged=len(seen) - len(changed) - len(gone), changed=changed, gone=gone, total=len(seen),
               now_fact=now_fact)
    emit("today", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: pin memory to the call

def all_fact_ids(cli: CLI, scope: Scope) -> dict[str, str]:
    """Every fact the account's memory ever had → where it lives.  Forgotten facts are not listed by
    `fact list`, but every conversation reports the facts it touched (`conv fact-actions`)."""
    ids = {}
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        if conv is None:
            continue
        for where in Scope.WHERE:
            for a in fact_actions(cli, conv["id"], scope, where, echo=False):
                ids.setdefault(a["fact_id"], where)
    for f in list_facts(cli, scope, echo=False).values():
        ids.setdefault(f["id"], f["where"])
    return ids


def message_index(cli: CLI) -> dict[str, dict]:
    """message id → {at, session label, n}: the story time of every message, read back from the server."""
    index = {}
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        if conv is None:
            continue
        for n, m in enumerate(list_messages(cli, conv["id"], echo=False), start=1):
            index[m["id"]] = dict(at=m.get("timestamp") or "", label=session["label"], n=n,
                                  date=session["date"][:10])
    return index


def dated(entry: dict, msgs: dict[str, dict]) -> dict:
    """When did this change happen in the story?  A COOK entry is as new as the newest message it was
    drawn from (its own `timestamp` is the server's processing time).  A MANUAL one has no message: its
    timestamp is when someone called the API."""
    src = [msgs[i] for i in entry.get("source_entry_ids") or [] if i in msgs]
    if entry.get("source_kind") == "COOK" and src:
        newest = max(src, key=lambda m: m["at"])
        return dict(entry, at=newest["at"], source=f"{newest['label']} ({newest['date']}), msg {newest['n']}",
                    dated_by="message")
    return dict(entry, at=entry.get("timestamp") or "", source=f"{entry.get('source_kind', '?')} via API",
                dated_by="server")


def trace_all(cli: CLI, scope: Scope) -> dict[str, dict]:
    ids = all_fact_ids(cli, scope)
    msgs = message_index(cli)
    note(f"{len(ids)} fact(s) ever existed in this account's memory; tracing each one")
    traces = {}
    for fid, where in ids.items():
        t = cli.run("fact", "trace", fid, *scope.args(where), scoped=True)
        entries = sorted((dated(e, msgs) for e in t.get("trace") or []), key=lambda e: e["at"])
        traces[fid] = dict(id=fid, where=where, current=(t.get("fact") or {}).get("fact"),
                           expired=bool((t.get("fact") or {}).get("expired")), entries=entries)
    return traces


def state_at(traces: dict[str, dict], when: str) -> dict[str, dict]:
    """The account's memory as it was at `when`: each fact's last change at or before that moment."""
    state = {}
    for fid, t in traces.items():
        last = None
        for e in t["entries"]:
            if e["at"] <= when:
                last = e
        if last and last["event"] != "FORGET":
            state[fid] = dict(id=fid, fact=last.get("new_fact") or "", where=t["where"], since=last["at"],
                              source=last["source"])
    return state


def pin_to_call(cli: CLI, scope: Scope, call: dict) -> dict:
    T = call["at"]
    traces = trace_all(cli, scope)
    then = state_at(traces, T)
    now = {fid: t for fid, t in traces.items() if not t["expired"]}
    lines = [f"\n  Memory pinned to {T} (the TOOL_USE message): {len(then)} fact(s)"]
    for f in sorted(then.values(), key=lambda f: f["since"]):
        t = traces[f["id"]]
        tag = "forgotten since" if t["expired"] else "rewritten since" if t["current"] != f["fact"] else ""
        lines.append(f"   {short(f['id'])}  {f['fact']}" + (f"   ← {tag}" if tag else ""))
    emit("text", "\n".join(lines))

    # The check that makes this a reproduction, not a guess: every fact the agent saw at the time,
    # as recorded in its TOOL_RESULT, must come back from the replay word for word.
    checks = []
    for f in call["facts"]:
        got = then.get(f["id"], {}).get("fact")
        checks.append(dict(id=f["id"], recorded=f["fact"], replayed=got, same=got == f["fact"]))
    ok = sum(c["same"] for c in checks)
    addr, used = address_on_file([dict(id=c["id"], fact=c["replayed"] or "") for c in checks])
    lines = [f"\n  Replayed vs recorded — the {len(checks)} fact(s) in the agent's TOOL_RESULT:"]
    lines += [f"   {'✓' if c['same'] else '✗'} {short(c['id'])}  {c['replayed'] or '(not in the replayed memory)'}"
              for c in checks]
    lines += [f"\n   {ok}/{len(checks)} identical → the replay reproduces what the agent saw",
              f"   address on file at {T[:16].replace('T', ' ')}: {addr or '—'}"]
    emit("text", "\n".join(lines))

    if used:
        t = traces[used["id"]]
        lines = [f"\n  The fact the agent shipped by, {short(used['id'])} — full history (fact trace), oldest first:"]
        for e in t["entries"]:
            lines.append(f"   {e['at'][:16].replace('T', ' ')}  {e['event']:<6} {e.get('source_kind', ''):<6} "
                         f"{e['source']}" + (f"  {e['source_event_id']}" if e.get("source_event_id") else ""))
            lines.append(f"        {e.get('new_fact') or '(forgotten)'}")
        lines.append(f"   now: {'forgotten' if t['expired'] else t['current']}")
        emit("text", "\n".join(lines))
    out = dict(at=T, then=list(then.values()), checks=checks, identical=ok, address=addr,
               used=used and traces[used["id"]], live=len(now), ever=len(traces))
    emit("pinned", "", **out)
    return dict(out, traces=traces)


# --------------------------------------------------------------------------- step 7: diff + audit

def memory_diff(traces: dict[str, dict], since: str) -> list[dict]:
    rows = []
    for fid, t in traces.items():
        for e in t["entries"]:
            if e["at"] > since:
                rows.append(dict(at=e["at"], event=e["event"], kind=e.get("source_kind"), id=fid, where=t["where"],
                                 old=e.get("old_fact"), new=e.get("new_fact"), source=e["source"],
                                 cookrun=e.get("source_event_id"), conversation=e.get("conversation_id"),
                                 messages=e.get("source_entry_ids") or [], dated_by=e["dated_by"]))
    return sorted(rows, key=lambda r: r["at"])


def diff_and_audit(call: dict, today: dict, pinned: dict) -> Path:
    T = call["at"]
    rows = memory_diff(pinned["traces"], T)
    lines = [f"\n  What changed in the account's memory after {T[:16].replace('T', ' ')} — {len(rows)} change(s):"]
    for r in rows:
        mark = {"ADD": "+", "UPDATE": "~", "FORGET": "-"}.get(r["event"], "?")
        when = r["at"][:16].replace("T", " ") + ("" if r["dated_by"] == "message" else " (API call time)")
        lines.append(f"\n   {mark} {r['event']:<6} {short(r['id'])}  [{r['where']}]  {when}  ← {r['source']}"
                     + (f" · {r['cookrun']}" if r["cookrun"] else ""))
        if r["event"] == "UPDATE":
            lines += [f"        was: {r['old']}", f"        now: {r['new']}"]
        elif r["event"] == "ADD":
            lines.append(f"        {r['new']}")
        else:
            lines.append(f"        {r['old'] or pinned['traces'][r['id']]['entries'][0].get('new_fact')}")
    emit("text", "\n".join(lines))

    first_new = next((r for r in rows if NEW_STREET in (r["new"] or "")), None)
    if pinned["address"] and OLD_STREET in pinned["address"] and first_new:
        verdict = (f"Not an agent bug: at the moment of the call the memory said {pinned['address']}, and the agent "
                   f"shipped exactly there. The Grenoble address first reached memory on {first_new['at'][:10]} "
                   f"({first_new['source']}). Fix the process, not the agent: confirm the address before shipping "
                   f"when a move is pending.")
    else:
        verdict = (f"Replayed address {pinned['address'] or 'none'}, shipped to {today['then_address'] or 'unknown'}: "
                   f"see the diff above.")
    emit("text", f"\n  Verdict: {verdict}")

    OUT.mkdir(exist_ok=True)
    path = OUT / f"replay-{call['id']}.json"
    record = {
        "ticket": "Replacement QL-40 shipped to a closed office",
        "replayed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pinned_to": {"time": T, "message_id": call["message_id"], "tool_call_id": call["id"],
                      "tool": call["name"], "arguments": call["arguments"]},
        "recorded_vs_replayed": pinned["checks"],
        "identical": f"{pinned['identical']}/{len(pinned['checks'])}",
        "address_then": pinned["address"], "address_today": today["now_address"],
        "memory_then": pinned["then"],
        "changes_since": rows,
        "verdict": verdict,
        "note": "Read-only replay. Nothing was written back to MemoryLake.",
    }
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    emit("text", f"\n   audit record: {len(rows)} change(s), {pinned['identical']}/{len(pinned['checks'])} replayed facts "
                 f"identical, written to out/{path.name} (nothing written back to memory)")
    emit("diff", "", rows=rows, verdict=verdict, path=f"out/{path.name}", record=record)
    return path


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversations(cli)
    note(f"deleted {n} conversation(s)")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note(f"deleted project {PROJECT['name']} (its facts went with it)")
    delete_actors(cli)
    note("deleted the demo actors (and any facts held by them)")


# --------------------------------------------------------------------------- the whole pipeline

TOTAL = 7


def replay_steps(cli: CLI, scope: Scope, first: int, total: int) -> Path:
    banner(first, total, "Reproduce it today — the agent's own tool call, re-run")
    _, call = find_bug_call(cli)
    today = reproduce_today(cli, scope, call)
    banner(first + 1, total, "Pin memory to the moment of the call — fact trace, dated by message")
    pinned = pin_to_call(cli, scope, call)
    banner(first + 2, total, "Memory diff, then vs now, and the audit record")
    return diff_and_audit(call, today, pinned)


def run_pipeline(cli: CLI, reset: bool = False) -> Path:
    """Steps 2–7. Step 1, connecting, is the caller's job."""
    banner(2, TOTAL, "Set up — one project for the account, the customer and the agent as actors")
    project_id = ensure_project(cli, reset)
    actors = {key: ensure_actor(cli, key, spec) for key, spec in PEOPLE.items()}
    scope = Scope(project_id, actors["lena"])

    banner(3, TOTAL, "Four support chats, one at a time — the agent's tool call recorded as it happened")
    ingest_sessions(cli, scope, actors)
    show_memory(cli, scope)

    banner(4, TOTAL, "Today, after the complaint — memory corrected by hand")
    fix_memory(cli, scope)
    return replay_steps(cli, scope, 5, TOTAL)


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "replay", "facts", "cleanup"],
                    help="run = full demo (default); replay = only reproduce, pin and diff (steps 5–7) on the "
                         "existing data; facts = what the account's memory holds now; cleanup = delete "
                         "everything the demo created")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo data before ingesting")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()

    # Line-buffer stdout so the commands show up as they run even when piped (`| tee run.log`).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    total = {"run": TOTAL, "replay": 4}.get(args.command, 2)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    require_history_commands(cli)

    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command != "run":
        scope = Scope(project_id_or_die(cli), find_actor_ids(cli)["lena"])
        if args.command == "facts":
            banner(2, 2, "What the account's memory holds now")
            show_memory(cli, scope)
        else:
            path = replay_steps(cli, scope, 2, 4)
            print(f"\nReplay written to {path}")
        return

    path = run_pipeline(cli, reset=args.reset)
    print(f"\nDone. The replay is in {path}. Re-run `python3 demo.py replay` any time, "
          "or `python3 demo.py cleanup` to remove the demo data.")


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

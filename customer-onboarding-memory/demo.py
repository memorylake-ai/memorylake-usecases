#!/usr/bin/env python3
"""
Customer onboarding memory that survives every handoff — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tallyfield sells field-service scheduling software.  Corvane HVAC Services
(240 technicians, 14 branches) bought it in August.  The customer has since
talked to three different people at Tallyfield, and one AI assistant:

  * the sales discovery call with Lena Ortiz (AE), 2026-08-27;
  * the kickoff call with Theo Park (CSM), 2026-09-08;
  * an implementation chat between Marcus Bell (Corvane's IT admin) and
    Tallyfield's onboarding assistant, 2026-09-22, in which the assistant
    called three tools to check Corvane's setup.

Each stage left a handoff note that kept about half of what was said.  It is
now 2026-10-13, and Ines Calder runs dispatcher training tomorrow.  The demo:

  * keeps one MemoryLake project per customer.  Every stage is a conversation
    in it, tagged with the stage, and every message that matters carries its
    own metadata (event=deadline|decision|blocker|win|…);
  * stores the assistant's tool calls as TOOL_USE / TOOL_RESULT blocks, which
    is how an agent framework would hand them over;
  * runs Ines's discovery checklist against the customer's memory, so she
    does not re-ask what sales and kickoff already asked;
  * shows what MemoryLake does *not* extract: the tool results never become facts.
    So the demo replays the implementation chat, pairs each call with its result,
    and promotes the failures into the customer's memory. Ines's search finds
    them before and after;
  * builds the onboarding timeline from the message metadata, across all
    three conversations, and writes a training-prep brief.

Everything the script does is a plain CLI command, echoed as it runs, so you
can copy any line into your own shell.  The companion web app (web/server.py)
drives the same functions and streams the same events to a browser.

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
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-com"  # memorylake-usecases / customer-onboarding-memory; keeps demo ids apart from yours

CUSTOMER = dict(key="corvane", display="Corvane HVAC Services")
PROJECT = dict(custom_id=f"{PREFIX}-corvane", name="Corvane HVAC Services — onboarding",
               description="Everything Tallyfield knows about Corvane, from sales to activation. Demo data.")

# Who takes part in the conversations.  Customer-side people and Tallyfield's team are both actors;
# the onboarding assistant is an ASSISTANT actor.
PEOPLE = {
    "dana-whitlock": dict(display="Dana Whitlock", type="HUMAN", side="customer",
                          tags="customer:corvane,champion", role="Director of Operations, Corvane HVAC"),
    "marcus-bell": dict(display="Marcus Bell", type="HUMAN", side="customer",
                        tags="customer:corvane,admin", role="IT admin, Corvane HVAC"),
    "lena-ortiz": dict(display="Lena Ortiz", type="HUMAN", side="vendor",
                       tags="tallyfield,sales", role="Account executive, Tallyfield"),
    "theo-park": dict(display="Theo Park", type="HUMAN", side="vendor",
                      tags="tallyfield,cs", role="Customer success manager, Tallyfield"),
    "assistant": dict(display="Tallyfield onboarding assistant", type="ASSISTANT", side="vendor",
                      tags="tallyfield,ai", role="AI onboarding assistant (calls setup-check tools)"),
}
TRAINER = dict(display="Ines Calder", role="Trainer, Tallyfield")

# Promoted tool findings start with this, so a re-run can find and retract them (step 5 starts clean).
PROMOTED = "Open blocker from implementation"
STAGE_ORDER = ["sales", "kickoff", "implementation"]
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


def load_checklist() -> dict:
    return json.loads((DATA / "checklist.json").read_text(encoding="utf-8"))


def handoff_notes() -> set[str]:
    return {n for s in load_sessions() for n in s["handoff"]}


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def project_id_or_die(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        die("the demo project does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def delete_actors(cli: CLI) -> None:
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])


def ensure_project(cli: CLI, reset: bool) -> str:
    proj = find_project(cli)
    if reset and proj is not None:
        note("--reset: deleting the conversations, the customer project and the demo actors")
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
    emit("project", "", key=CUSTOMER["key"], id=proj["id"], name=proj["name"])
    return proj["id"]


# --------------------------------------------------------------------------- facts & messages

def list_facts(cli: CLI, scope: str, ids: str, echo: bool = True) -> list[dict]:
    """scope is "projects" or "actors"."""
    facts, token = [], None
    while True:
        args = ["fact", "list", f"--{scope}", ids, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


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


# --------------------------------------------------------------------------- step 3: the three stages

def speaker_line(key: str, text: str) -> str:
    p = PEOPLE[key]
    return f"{p['display']} ({p['role']}): {text}"


def turn_blocks(turn: dict) -> list[dict] | None:
    """Content blocks for the turns `--text` cannot express: a tool call, a tool result."""
    if "tool_use" in turn:
        tu = turn["tool_use"]
        blocks = [{"block_type": "TEXT", "text": turn["text"]}] if turn.get("text") else []
        # Field names matter: `tool_call_id` + `tool_name` are required, the input is `arguments`.
        blocks.append({"block_type": "TOOL_USE", "tool_call_id": tu["id"], "tool_name": tu["name"],
                       "arguments": tu["arguments"]})
        return blocks
    if "tool_result" in turn:
        tr = turn["tool_result"]
        return [{"block_type": "TOOL_RESULT", "tool_call_id": tr["id"], "result": tr["result"]}]
    return None


def turn_metadata(session: dict, turn: dict) -> list[str]:
    meta = {"stage": session["stage"], "source": session["source"]}
    if turn.get("event"):
        meta["event"] = turn["event"]
    if turn.get("topic"):
        meta["topic"] = turn["topic"]
    args = []
    for k, v in meta.items():
        args += ["--metadata", f"{k}={v}"]
    return args


def ingest_sessions(cli: CLI, project_id: str, actors: dict[str, str]) -> None:
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        done = 0
        if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
            # Left over from a deleted project (deleting a project does not delete its conversations).
            note(f"“{session['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        emit("text", f"\n  ▸ {session['stage_label']} · {session['date'][:10]} · {session['source']}")
        if conv is None:
            conv = cli.run("conv", "create", "--custom-id", session["custom_id"], "--project", project_id,
                           "--actors", ",".join(actors[k] for k in session["participants"]), "--kind", "DIRECT",
                           "--name", session["name"], "--metadata", f"customer={CUSTOMER['key']}",
                           "--metadata", f"stage={session['stage']}", "--metadata", f"source={session['source']}",
                           scoped=True)
        else:
            done = len(list_messages(cli, conv["id"]))
            note(f"conversation exists with {done} message(s) → {conv['id']}")
        emit("session", "", custom_id=session["custom_id"], id=conv["id"], stage=session["stage"],
             name=session["name"], date=session["date"], turns=len(session["turns"]), done=done)
        start = datetime.fromisoformat(session["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        total = len(session["turns"])
        for i, turn in enumerate(session["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            args = ["conv", "msg", "append", conv["id"], "--actor", actors[turn["speaker"]],
                    "--custom-id", f"turn-{i:02d}", "--timestamp", ts, *turn_metadata(session, turn)]
            blocks = turn_blocks(turn)
            if blocks is not None:
                args += ["--content-json", json.dumps(blocks, ensure_ascii=False)]
            else:
                args += ["--text", speaker_line(turn["speaker"], turn["text"])]
            if parent:
                args += ["--parent", parent]
            last = i == total
            if last:
                # The last append waits for the whole conversation's memory: the CLI polls cook-status.
                # One stage at a time: conversations cooking in parallel finish in any order.
                note("last message: --wait keeps polling until MemoryLake has extracted the facts")
                args += ["--wait", "--timeout", "600"]
            t0 = time.time()
            parent = cli.run(*args, scoped=True)["id"]
            if last:
                note(f"memory ready in {int(time.time() - t0)}s")
                emit("cooked", "", id=conv["id"], seconds=int(time.time() - t0))
            emit("turn", "", custom_id=session["custom_id"], index=i, speaker=turn["speaker"],
                 block="tool_use" if "tool_use" in turn else "tool_result" if "tool_result" in turn else "text")
        if done >= total:
            wait_for_memory(cli, conv["id"])
            emit("cooked", "", id=conv["id"], seconds=0)
        # Pin the stage owner's handoff note only when this run appended the conversation: the note's
        # text is the thing a re-run would look up, and the extractor may rewrite pinned facts in place.
        if session["handoff"] and done < total:
            cli.run("fact", "add", "--project", project_id, *session["handoff"], scoped=True)
            note(f"handoff note from {PEOPLE[session['owner']]['display']}: {len(session['handoff'])} fact(s) pinned")
        elif session["handoff"]:
            note("handoff note pinned on an earlier run")
        if session["handoff"]:
            emit("text", "\n".join(f"   📌 {n}" for n in session["handoff"]))
        emit("handoff", "", custom_id=session["custom_id"], notes=session["handoff"])


def retract_promoted(cli: CLI, project_id: str) -> int:
    """A re-run starts step 5 clean: take back the tool findings an earlier run promoted."""
    stale = [f for f in list_facts(cli, "projects", project_id, echo=False) if (f.get("fact") or "").startswith(PROMOTED)]
    for f in stale:
        cli.run("fact", "delete", "--project", project_id, f["id"], scoped=True)
    if stale:
        note(f"took back {len(stale)} finding(s) promoted by an earlier run, so step 5 starts from the same place")
    return len(stale)


def show_memory(cli: CLI, project_id: str, actors: dict[str, str]) -> None:
    notes = handoff_notes()
    facts = sorted(list_facts(cli, "projects", project_id), key=lambda f: f.get("created_at") or "")
    actor_facts = list_facts(cli, "actors", ",".join(actors.values()))
    def kind(f: dict) -> str:
        t = f.get("fact") or ""
        return "pinned" if t in notes else "promoted" if t.startswith(PROMOTED) else "extracted"
    lines = [f"\n  {PROJECT['name']} (project): {len(facts)} fact(s)"]
    lines += [f"   {'📌' if kind(f) == 'pinned' else '⬆' if kind(f) == 'promoted' else '-'} {f.get('fact')}" for f in facts]
    if actor_facts:
        lines.append(f"\n  held by the people's actors: {len(actor_facts)} fact(s)")
        lines += [f"   - {f.get('fact')}" for f in actor_facts]
    emit("text", "\n".join(lines))
    emit("memory", "", facts=[dict(fact=f.get("fact"), kind=kind(f)) for f in facts],
         actor_facts=[f.get("fact") for f in actor_facts])


# --------------------------------------------------------------------------- step 4: no re-discovery

AS_OF = re.compile(r"\(as of (\d{4}-\d{2}-\d{2})\)")


def stage_dates() -> dict[str, str]:
    """as-of date → stage.  The extractor stamps the date in its own time zone, which can be the next
    calendar day for an evening (UTC) conversation, so the day after counts too."""
    dates = {}
    for s in load_sessions():
        day = datetime.fromisoformat(s["date"][:10])
        dates.setdefault((day + timedelta(days=1)).strftime("%Y-%m-%d"), s["stage"])
        dates[s["date"][:10]] = s["stage"]
    return dates


def label(fact: str, notes: set[str], dates: dict[str, str]) -> str:
    if fact in notes:
        return "handoff note · sales" if fact.startswith("Handoff") else "handoff note · kickoff"
    if fact.startswith(PROMOTED):
        return "tool finding"
    m = AS_OF.search(fact) or re.search(r"\(on (\d{4}-\d{2}-\d{2})\)", fact)
    if m and m.group(1) in dates:
        stage = dates[m.group(1)]
        when = next(x["date"][5:10] for x in load_sessions() if x["stage"] == stage)
        return f"{stage} · {when}"
    return "conversation"


def search(cli: CLI, query: str, project_id: str, actor_ids: list[str], top_k: int = 5) -> list[dict]:
    # Union of the customer project and the customer's people: extracted facts can land on either.
    res = cli.run("search", query, "--projects", project_id, "--actors", ",".join(actor_ids),
                  "--types", "fact", "--top-k", str(top_k), scoped=True)
    return [dict(id=f.get("id"), fact=f.get("fact") or "") for f in (res or {}).get("facts") or []]


def customer_actor_ids(actors: dict[str, str]) -> list[str]:
    return [actors[k] for k, p in PEOPLE.items() if p["side"] == "customer"]


def discovery_check(cli: CLI, project_id: str, actors: dict[str, str]) -> dict:
    checklist = load_checklist()
    notes, dates = handoff_notes(), stage_dates()
    emit("text", f"\n  {TRAINER['display']} ({TRAINER['role']}) prepares “{checklist['session']}”. "
                 f"Her discovery checklist, asked of Corvane's memory first:")
    rows = []
    for q in checklist["questions"]:
        found = search(cli, q["query"], project_id, customer_actor_ids(actors))
        hits = [dict(h, source=label(h["fact"], notes, dates)) for h in found if re.search(q["guard"], h["fact"], re.I)]
        in_notes = any(re.search(q["guard"], n, re.I) for n in notes)
        lines = [f"\n  {'✓' if hits else '✗'} {q['q']}"]
        lines += [f"      [{h['source']}] {h['fact']}" for h in hits[:2]] or ["      not in memory — ask the customer"]
        if len(found) > len(hits):
            lines.append(f"      ({len(found) - len(hits)} other hit(s) not about this question, left out)")
        emit("text", "\n".join(lines))
        row = dict(q=q["q"], hits=hits[:2], answered=bool(hits), in_notes=in_notes)
        rows.append(row)
        emit("question", "", **row)
    answered = sum(r["answered"] for r in rows)
    by_notes = sum(r["in_notes"] for r in rows)
    verdict = (f"{answered} of {len(rows)} discovery answers already in memory: {len(rows) - answered} to re-ask. "
               f"The handoff notes alone covered {by_notes} of {len(rows)}.")
    emit("text", f"\n  {verdict}")
    summary = dict(answered=answered, total=len(rows), by_notes=by_notes, verdict=verdict, rows=rows)
    emit("discovery", "", **summary)
    return summary


# --------------------------------------------------------------------------- step 5: tool findings

BLOCKER_PROBES = [
    dict(topic="sso", title="Okta sign-in", query="Okta single sign-on problem", guard=r"SAML|NameID|email attribute"),
    dict(topic="import", title="Technician import", query="technician roster import problem",
         guard=r"rejected|column F|EPA 608"),
]


def find_conversations(cli: CLI, **want: str) -> list[dict]:
    """`conv list` has no server-side filter; pick the customer's conversations by their metadata."""
    convs, token = [], None
    while True:
        args = ["conv", "list", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=token is None)
        convs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            break
    mine = [c for c in convs if all((c.get("metadata") or {}).get(k) == v for k, v in want.items())]
    note(f"{len(convs)} conversation(s) in the workspace; {len(mine)} with metadata "
         + ", ".join(f"{k}={v}" for k, v in want.items()))
    return mine


def probe_blockers(cli: CLI, project_id: str, actors: dict[str, str], when: str) -> dict[str, list[dict]]:
    out = {}
    for p in BLOCKER_PROBES:
        found = search(cli, p["query"], project_id, customer_actor_ids(actors))
        hits = [h for h in found if re.search(p["guard"], h["fact"], re.I)]
        others = [h for h in found if h not in hits]
        lines = [f"   {p['title']}: {len(hits)} hit(s) about the cause"]
        lines += [f"      ✓ {h['fact']}" for h in hits]
        if others:
            lines.append(f"      ({len(others)} other hit(s), none about the cause, e.g. “{others[0]['fact']}”)")
        emit("text", "\n".join(lines))
        out[p["topic"]] = hits
        emit("probe", "", when=when, topic=p["topic"], title=p["title"], hits=hits,
             others=[h["fact"] for h in others[:2]])
    return out


def tool_calls(msgs: list[dict], names: dict[str, str]) -> list[dict]:
    """Pair every TOOL_USE block with the TOOL_RESULT that carries the same tool_call_id."""
    calls, order = {}, []
    for m in msgs:
        meta = m.get("metadata") or {}
        for b in m.get("content") or []:
            if b.get("block_type") == "TOOL_USE":
                calls[b["tool_call_id"]] = dict(id=b["tool_call_id"], name=b.get("tool_name"),
                                                arguments=b.get("arguments") or {}, when=m.get("timestamp") or "",
                                                who=names.get(m.get("actor_id"), m.get("actor_id")))
                order.append(b["tool_call_id"])
            elif b.get("block_type") == "TOOL_RESULT":
                c = calls.setdefault(b["tool_call_id"], dict(id=b["tool_call_id"], name="?", arguments={}))
                c.update(result=b.get("result") or {}, event=meta.get("event"), topic=meta.get("topic"))
    return [calls[i] for i in order]


def promoted_text(call: dict) -> str:
    r = call["result"]
    text = f"{PROMOTED} ({call['when'][:10]}, tool {call['name']}, call {call['id']}): {r.get('finding')}."
    if r.get("fix"):
        text += f" Fix: {r['fix']}."
    return text


def blockers_step(cli: CLI, project_id: str, actors: dict[str, str]) -> list[dict]:
    retract_promoted(cli, project_id)
    emit("text", "\n  Before: does Corvane's memory know what the assistant found on 2026-09-22?")
    before = probe_blockers(cli, project_id, actors, "before")

    convs = find_conversations(cli, customer=CUSTOMER["key"], stage="implementation")
    if not convs:
        die("no implementation conversation for this customer; run `python3 demo.py` first")
    conv = convs[0]
    names = {aid: PEOPLE[k]["display"] for k, aid in actors.items()}
    calls = tool_calls(list_messages(cli, conv["id"]), names)
    lines = [f"\n  Replaying “{conv.get('name')}”: {len(calls)} tool call(s), each paired with its result by tool_call_id"]
    for c in calls:
        args = ", ".join(f"{k}={v}" for k, v in c["arguments"].items())
        r = c.get("result") or {}
        mark = "✓" if r.get("status") == "ok" else "✗"
        lines += [f"\n   {c['when'][11:16]}  {c['name']}({args})   [{c['id']}]",
                  f"          {mark} {r.get('status', 'no result')}: {r.get('finding', '')}"]
        if r.get("fix"):
            lines.append(f"            fix: {r['fix']}")
    emit("text", "\n".join(lines))
    emit("calls", "", conversation=conv.get("name"), calls=calls)

    failing = [c for c in calls if (c.get("result") or {}).get("status") not in (None, "ok")]
    texts = [promoted_text(c) for c in failing]
    if texts:
        emit("text", f"\n  Tool results are stored but never extracted. Promoting the {len(texts)} that did not "
                     f"pass into the customer's memory:")
        cli.run("fact", "add", "--project", project_id, *texts, scoped=True)
        emit("text", "\n".join(f"   ⬆ {t}" for t in texts))
    emit("promoted", "", facts=texts)

    emit("text", "\n  After: the same searches")
    after = probe_blockers(cli, project_id, actors, "after")
    results = []
    for p in BLOCKER_PROBES:
        b, a = len(before[p["topic"]]), len(after[p["topic"]])
        results.append(dict(topic=p["topic"], title=p["title"], before=b, after=a))
    emit("text", "\n" + "\n".join(f"   {r['title']}: {r['before']} → {r['after']} hit(s) about the cause" for r in results))
    emit("blockers", "", results=results, promoted=texts)
    return [dict(c, promoted=promoted_text(c)) for c in failing]


# --------------------------------------------------------------------------- step 6: timeline

def message_summary(m: dict) -> str:
    for b in m.get("content") or []:
        if b.get("block_type") == "TOOL_RESULT":
            r = b.get("result") or {}
            return f"[tool result] {r.get('finding', '')}"
    text = " ".join(b.get("text") or "" for b in m.get("content") or [] if b.get("block_type") == "TEXT")
    return text.split("): ", 1)[1] if "): " in text else text


def timeline_step(cli: CLI, actors: dict[str, str]) -> list[dict]:
    names = {aid: PEOPLE[k]["display"] for k, aid in actors.items()}
    convs = find_conversations(cli, customer=CUSTOMER["key"])
    rows = []
    for conv in convs:
        for m in list_messages(cli, conv["id"]):
            meta = m.get("metadata") or {}
            if meta.get("event"):
                rows.append(dict(when=m.get("timestamp") or "", stage=meta.get("stage", "?"), event=meta["event"],
                                 topic=meta.get("topic", ""), who=names.get(m.get("actor_id"), "?"),
                                 text=message_summary(m)))
    rows.sort(key=lambda r: r["when"])
    lines = [f"\n  {len(rows)} event(s) from {len(convs)} conversation(s), picked by message metadata `event`:\n"]
    for r in rows:
        lines.append(f"   {r['when'][:10]}  {r['stage']:<14} {r['event']:<11} {r['text']}")
    emit("text", "\n".join(lines))
    emit("timeline", "", rows=rows)
    return rows


# --------------------------------------------------------------------------- step 7: training brief

def render_brief(discovery: dict, blockers: list[dict], rows: list[dict]) -> str:
    cl = load_checklist()
    lines = [f"# Training prep — {CUSTOMER['display']}, {cl['session']}", "",
             f"_For {TRAINER['display']}. Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from "
             f"the customer's MemoryLake project: three conversations (sales, kickoff, implementation), the handoff "
             f"notes, and the tool findings promoted from the implementation chat._", "",
             "## Do not re-ask", ""]
    for r in discovery["rows"]:
        if r["answered"]:
            h = r["hits"][0]
            lines.append(f"- **{r['q']}** {h['fact']} _({h['source']})_")
        else:
            lines.append(f"- **{r['q']}** not in memory — ask")
    lines += ["", "## Open blockers (from the assistant's tool calls)", ""]
    lines += [f"- **{b['name']}** ({b['when'][:10]}, call `{b['id']}`): {b['result'].get('finding')}. "
              f"Fix: {b['result'].get('fix')}." for b in blockers] or ["- none"]
    lines += ["", "## Timeline", "", "| date | stage | event | what |", "|---|---|---|---|"]
    lines += [f"| {r['when'][:10]} | {r['stage']} | {r['event']} | {r['text'].replace('|', '/')} |" for r in rows]
    risks = [r for r in rows if r["event"] == "risk"]
    if risks:
        lines += ["", "## Before tomorrow", ""]
        lines += [f"- {r['who']}: {r['text']}" for r in risks]
    return "\n".join(lines) + "\n"


def brief_step(discovery: dict, blockers: list[dict], rows: list[dict]) -> str:
    brief = render_brief(discovery, blockers, rows)
    OUT.mkdir(exist_ok=True)
    path = OUT / "training-brief-corvane.md"
    path.write_text(brief, encoding="utf-8")
    emit("text", f"\n   do not re-ask: {discovery['answered']} answer(s)   open blockers: {len(blockers)}   "
                 f"timeline: {len(rows)} event(s)")
    note(f"brief written to {path}")
    emit("brief", "", markdown=brief)
    return brief


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


def run_pipeline(cli: CLI, reset: bool = False, first_step: int = 2) -> str:
    """Steps 2–7. Step 1, connecting, is the caller's job."""
    banner(first_step, TOTAL, "Set up — one project per customer, everyone who talked to them as an actor")
    project_id = ensure_project(cli, reset)
    actors = {key: ensure_actor(cli, key, spec) for key, spec in PEOPLE.items()}

    banner(3, TOTAL, "Sales → kickoff → implementation: each stage a conversation, tool calls included")
    ingest_sessions(cli, project_id, actors)
    retract_promoted(cli, project_id)
    show_memory(cli, project_id, actors)

    banner(4, TOTAL, "Training tomorrow — the trainer's discovery checklist, asked of memory first")
    discovery = discovery_check(cli, project_id, actors)

    banner(5, TOTAL, "What the assistant's tools found — replayed, paired, promoted into memory")
    blockers = blockers_step(cli, project_id, actors)

    banner(6, TOTAL, "The onboarding timeline, from message metadata across all three conversations")
    rows = timeline_step(cli, actors)

    banner(7, TOTAL, "Training-prep brief")
    return brief_step(discovery, blockers, rows)


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "check", "blockers", "timeline", "brief", "facts", "cleanup"],
                    help="run = full demo (default); check = only the discovery checklist; blockers = only "
                         "replay and promote the tool findings; timeline = only the event timeline; brief = "
                         "check + blockers + timeline + the brief; facts = what the customer's memory holds; "
                         "cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo data before ingesting")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()

    # Line-buffer stdout so the commands show up as they run even when piped (`| tee run.log`).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    total = {"run": TOTAL, "brief": 5}.get(args.command, 2)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)

    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command != "run":
        project_id = project_id_or_die(cli)
        actors = find_actor_ids(cli)
        if args.command == "check":
            banner(2, 2, "The trainer's discovery checklist, asked of memory first")
            discovery_check(cli, project_id, actors)
        elif args.command == "blockers":
            banner(2, 2, "What the assistant's tools found — replayed, paired, promoted")
            blockers_step(cli, project_id, actors)
        elif args.command == "timeline":
            banner(2, 2, "The onboarding timeline, from message metadata")
            timeline_step(cli, actors)
        elif args.command == "facts":
            banner(2, 2, "What Corvane's memory holds")
            show_memory(cli, project_id, actors)
        else:
            banner(2, 5, "The trainer's discovery checklist")
            discovery = discovery_check(cli, project_id, actors)
            banner(3, 5, "Tool findings — replayed, paired, promoted")
            blockers = blockers_step(cli, project_id, actors)
            banner(4, 5, "The onboarding timeline")
            rows = timeline_step(cli, actors)
            banner(5, 5, "Training-prep brief")
            brief_step(discovery, blockers, rows)
        return

    run_pipeline(cli, reset=args.reset)
    print("\nDone. Re-run `python3 demo.py check`, `blockers`, `timeline` or `brief` any time, "
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

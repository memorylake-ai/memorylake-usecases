#!/usr/bin/env python3
"""
Sales call memory for revenue teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
An SDR runs a discovery call with Acme Corp, hands the deal to an AE, who runs
two more calls and an email thread, then hands the live pilot to a CSM.  Every
call is written to MemoryLake as a conversation; MemoryLake extracts the
durable facts (people, blockers, pricing, dates) in the background.  Notes and
emails are uploaded as documents.  At the end, the CSM gets a pre-kickoff brief
in seconds by searching the account memory — no re-discovery.

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

PREFIX = "mlu-scm"  # memorylake-usecases / sales-call-memory; keeps demo ids apart from yours

PEOPLE = {
    # our side: Northwind, the vendor
    "sdr-jordan": dict(display="Jordan Park", tags="rep,sdr", role="SDR, Northwind"),
    "ae-sam": dict(display="Sam Okafor", tags="rep,ae", role="Account Executive, Northwind"),
    # the prospect: Acme Corp
    "dana-li": dict(display="Dana Li", tags="acme,prospect", role="VP Operations, Acme Corp"),
    "priya-raman": dict(display="Priya Raman", tags="acme,prospect", role="Security Lead, Acme Corp"),
    "mark-chen": dict(display="Mark Chen", tags="acme,prospect", role="CFO, Acme Corp"),
}
PROJECT = dict(
    custom_id=f"{PREFIX}-acme-corp",
    name="Acme Corp — account memory",
    description="Every call, note and email about the Acme Corp opportunity. Demo data.",
)
AGENT = dict(
    custom_id=f"{PREFIX}-deal-assistant",
    name="Deal assistant (demo)",
    system_prompt=(
        "You are a sales assistant. Answer only from the account memory you retrieve. "
        "Be concise, use bullet points, and quote dates when the memory has them."
    ),
)

# The hand-off brief: one search per question.
BRIEF = [
    ("Who is who, and who is the main contact now?",
     "who is the main contact at Acme now and who else is involved in the decision"),
    ("Blockers and hard requirements",
     "Acme security requirements and blockers"),
    ("Pricing, budget and billing",
     "Acme pricing, budget approval and billing terms"),
    ("Pilot timeline and success criteria",
     "Acme pilot kickoff date and success criteria"),
    ("Competition",
     "which competitors is Acme evaluating and what did they think of them"),
    ("Working preferences",
     "how does Dana prefer to be contacted"),
]


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

    def run(self, *args: str, scoped: bool = False, echo: bool = True):
        """Run and return the parsed JSON (or raw text when the reply is not JSON)."""
        rc, out, err = self.raw(*args, scoped=scoped, echo=echo)
        if rc != 0:
            raise CLIError([self.binary, *map(str, args)], rc, out, err)
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

def ensure_actor(cli: CLI, key: str) -> str:
    p = PEOPLE[key]
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", p["display"],
                        "--type", "HUMAN", "--tags", p["tags"], "--description", p["role"])
        note(f"created actor {p['display']} → {actor['id']}")
    else:
        note(f"actor {p['display']} already exists → {actor['id']}")
    # Actors are account-wide; they must be bound to the workspace to take part in it.
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")
    emit("actor", "", key=key, id=actor["id"], display=p["display"], role=p["role"])
    return actor["id"]


def load_calls() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "calls").glob("*.json"))]


def load_notes() -> list[dict]:
    return [dict(name=p.name, text=p.read_text(encoding="utf-8")) for p in sorted((DATA / "notes").glob("*.md"))]


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for call in load_calls():
        conv = cli.try_run("conv", "get", call["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def ensure_project(cli: CLI, reset: bool) -> tuple[str, bool]:
    """Returns (project_id, fresh)."""
    proj = find_project(cli)
    if proj is not None and reset:
        note(f"--reset: deleting the demo conversations and project {proj['id']} (documents go with it)")
        delete_conversations(cli)
        cli.run("proj", "delete", proj["id"], scoped=True)
        proj = None
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
        emit("project", "", id=proj["id"], name=proj["name"], fresh=True)
        return proj["id"], True
    note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    emit("project", "", id=proj["id"], name=proj["name"], fresh=False)
    return proj["id"], False


# --------------------------------------------------------------------------- step 3: calls → conversations

def ingest_calls(cli: CLI, project_id: str, actors: dict[str, str]) -> list[str]:
    conv_ids: list[str] = []
    # Facts already in the project, so a re-run does not pin the same rep note twice.
    known_facts = {f.get("fact") for f in list_facts(cli, project_id)}
    for call in load_calls():
        conv = cli.try_run("conv", "get", call["custom_id"], "--by-custom-id", scoped=True)
        done = 0
        if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
            # Left over from an earlier project of the same name (deleting a project does not delete
            # the conversations that pointed at it). Start that call over.
            note(f"conversation “{call['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        if conv is None:
            participants = ",".join(actors[k] for k in call["participants"])
            conv = cli.run("conv", "create", "--custom-id", call["custom_id"], "--project", project_id,
                           "--actors", participants, "--kind", "GROUP", "--name", call["name"],
                           "--metadata", f"call_date={call['date'][:10]}", scoped=True)
        else:
            # Resume: messages already stored are skipped (custom ids make appends idempotent anyway).
            done = len(cli.run("conv", "msg", "list", conv["id"], "--page-size", "50").get("items") or [])
            note(f"conversation “{call['name']}” exists with {done} message(s) → {conv['id']}")
        conv_ids.append(conv["id"])
        emit("call", "", custom_id=call["custom_id"], id=conv["id"], name=call["name"], date=call["date"],
             turns=len(call["turns"]), done=done)
        start = datetime.fromisoformat(call["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        for i, (speaker, text) in enumerate(call["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # --timestamp dates the memory to when it was said, not when it was uploaded.
            # --parent names the message this one follows; without it the CLI looks the latest one up.
            # Speaker labels in the text (as a Gong/Otter export has them) let the extractor tell
            # "we" (Northwind) from "you" (Acme) when it writes the facts.
            line = f"{PEOPLE[speaker]['display']} ({PEOPLE[speaker]['role']}): {text}"
            args = ["conv", "msg", "append", conv["id"], "--actor", actors[speaker],
                    "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", line]
            if parent:
                args += ["--parent", parent]
            msg = cli.run(*args, scoped=True)
            parent = msg["id"]
            emit("turn", "", custom_id=call["custom_id"], index=i, speaker=speaker)
        if done < len(call["turns"]):
            note(f"{len(call['turns']) - done} turn(s) appended to “{call['name']}”")
        missing = [n for n in call.get("rep_notes") or [] if n not in known_facts]
        if missing:
            # What the rep typed into the CRM after the call. Facts are stored verbatim and are
            # searchable immediately, with no extraction step in between.
            cli.run("fact", "add", "--project", project_id, *missing, scoped=True)
            note(f"{len(missing)} rep note(s) pinned as facts")
        if call.get("rep_notes"):
            emit("pinned", "", custom_id=call["custom_id"], notes=call["rep_notes"])
    return conv_ids


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> None:
    """Messages are stored instantly; the facts are extracted in the background. Poll until done."""
    pending = set(conv_ids)
    t0 = last_report = time.time()
    first = True
    while pending and time.time() - t0 < timeout:
        for cid in sorted(pending):
            status = cli.run("conv", "cook-status", cid, scoped=True, echo=first)
            if status.get("cook_finished"):
                pending.discard(cid)
                emit("cooked", "", id=cid)
        first = False
        if pending:
            if time.time() - last_report >= 30:
                emit("progress", f"  … {len(pending)} conversation(s) still cooking ({int(time.time() - t0)}s)",
                     pending=len(pending), elapsed=int(time.time() - t0))
                last_report = time.time()
            time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} conversation(s) still processing server-side")
    else:
        note(f"memory ready for all conversations in {int(time.time() - t0)}s")


# --------------------------------------------------------------------------- step 4: notes → documents

def ingest_notes(cli: CLI, project_id: str) -> None:
    item_ids = []
    for path in sorted((DATA / "notes").glob("*.md")):
        # `overwrite` keeps the same Library item id on re-runs instead of creating note_1, note_2, …
        item = cli.run("lib", "upload", str(path), "--on-conflict", "overwrite")
        item_ids.append(item["item_id"])
        note(f"uploaded {item['name']}")
        emit("uploaded", "", name=item["name"], item_id=item["item_id"])
    result = cli.run("proj", "doc", "import", "--project", project_id, *item_ids, "--wait", scoped=True)
    note(f"imported: {result.get('success_count', 0)} new, {result.get('duplicate_count', 0)} already in project, "
         f"{result.get('failure_count', 0)} failed")
    emit("imported", "", **{k: result.get(k, 0) for k in ("success_count", "duplicate_count", "failure_count")})


# --------------------------------------------------------------------------- step 5: what did MemoryLake remember?

def list_facts(cli: CLI, project_id: str) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", "--projects", project_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=token is None)
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


def fact_date(f: dict) -> str:
    m = re.search(r"\(as of (\d{4}-\d{2}-\d{2})\)", f.get("fact", ""))
    return m.group(1) if m else (f.get("created_at") or "")[:10]


def show_facts(facts: list[dict]) -> None:
    extracted = [f for f in facts if "(as of " in f.get("fact", "")]
    lines = [f"\n  {len(facts)} facts in the account memory: {len(extracted)} extracted from the calls, "
             f"{len(facts) - len(extracted)} pinned by the reps.\n"]
    for f in sorted(facts, key=fact_date):
        flag = "  (expired)" if f.get("expired") else ""
        lines.append(f"   - {f['fact']}{flag}")
    emit("text", "\n".join(lines))
    emit("facts", "", facts=[dict(id=f.get("id"), fact=f.get("fact"), expired=bool(f.get("expired")),
                                   date=fact_date(f), pinned="(as of " not in f.get("fact", ""))
                              for f in sorted(facts, key=fact_date)])


# --------------------------------------------------------------------------- step 6: the hand-off brief

def search(cli: CLI, project_id: str, query: str, top_k: int = 5) -> dict:
    res = cli.run("search", query, "--projects", project_id, "--top-k", str(top_k), scoped=True)
    return dict(
        query=query,
        facts=[dict(id=f.get("id"), fact=f.get("fact"), score=f.get("score")) for f in res.get("facts") or []],
        documents=[dict(id=d.get("document_id"), name=d.get("document_name") or d.get("file_name"),
                        summary=d.get("document_summary")) for d in res.get("documents") or []],
    )


def brief_sections(cli: CLI, project_id: str, top_k: int) -> list[dict]:
    sections = []
    for heading, query in BRIEF:
        res = search(cli, project_id, query, top_k)
        sections.append(dict(heading=heading, **res))
        emit("brief_section", "", heading=heading, **res)
    return sections


def render_brief(sections: list[dict]) -> str:
    lines = ["# Acme Corp — pre-kickoff brief for the CSM",
             "",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from MemoryLake account memory "
             f"(project `{PROJECT['custom_id']}`). Every bullet is a retrieved memory, not a summary._",
             ""]
    for s in sections:
        lines.append(f"## {s['heading']}")
        if not s["facts"]:
            lines.append("_No facts matched._")
        for f in s["facts"]:
            lines.append(f"- {f['fact']}")
        if s["documents"]:
            names = ", ".join(sorted({d["name"] or "?" for d in s["documents"]}))
            lines.append(f"\n_Source documents: {names}_")
        lines.append("")
    return "\n".join(lines)


def build_brief(cli: CLI, project_id: str, top_k: int) -> str:
    sections = brief_sections(cli, project_id, top_k)
    brief = render_brief(sections)
    OUT.mkdir(exist_ok=True)
    (OUT / "acme-handoff-brief.md").write_text(brief, encoding="utf-8")
    emit("text", "\n" + brief)
    note(f"written to {OUT / 'acme-handoff-brief.md'}")
    emit("brief", "", markdown=brief, sections=sections)
    return brief


# --------------------------------------------------------------------------- optional: ask an agent

def ask_agent(cli: CLI, project_id: str) -> None:
    agent = cli.try_run("agent", "get", AGENT["custom_id"], "--by-custom-id")
    if agent is None:
        agent = cli.run("agent", "create", "--name", AGENT["name"], "--custom-id", AGENT["custom_id"],
                        "--system-prompt", AGENT["system_prompt"])
        note(f"created agent → {agent['id']}")
    try:
        cli.run("agent", "bind", agent["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    question = ("Write a 6-bullet pre-kickoff brief for the Acme Corp pilot: who to talk to, "
                "blockers, pricing and billing, timeline, competition, and open action items.")
    try:
        rc, out, err = cli.raw("agent", "send", agent["id"], "--project", project_id, "--text", question, scoped=True)
        if rc != 0:
            raise CLIError([], rc, out, err)
        emit("text", "\n" + out.strip() + "\n")
        note(err.strip().splitlines()[-1] if err.strip() else "done")
    except CLIError as e:
        if e.has("402", "QUOTA_EXCEEDED"):
            note("the agent needs model quota (HTTP 402 QUOTA_EXCEEDED). Free personal accounts do not have any; "
                 "the search-based brief above needs none. Skipping.")
        else:
            raise


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversations(cli)
    note(f"deleted {n} conversation(s)")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted project (its documents and facts went with it)")
    agent = cli.try_run("agent", "get", AGENT["custom_id"], "--by-custom-id")
    if agent:
        cli.run("agent", "delete", agent["id"])
        note("deleted agent")
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted demo actors")
    # Library files are not owned by the project; remove the ones we uploaded, by name.
    names = {p.name for p in (DATA / "notes").glob("*.md")}
    token, removed = None, 0
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=token is None)
        for item in page.get("items") or []:
            if item.get("name") in names:
                cli.run("lib", "delete", item.get("item_id") or item.get("id"))
                removed += 1
        token = page.get("continuation_token")
        if not token:
            break
    note(f"deleted {removed} uploaded note(s) from the Library")


# --------------------------------------------------------------------------- the whole pipeline

def run_pipeline(cli: CLI, reset: bool = False, with_agent: bool = False, top_k: int = 5,
                 first_step: int = 2) -> str:
    """Steps 2–6 (and the optional 7). Step 1, connecting, is the caller's job."""
    total = 6 + (1 if with_agent else 0)

    banner(first_step, total, "Set up — the people on the deal, and one project per account")
    actors = {key: ensure_actor(cli, key) for key in PEOPLE}
    project_id, _fresh = ensure_project(cli, reset)

    banner(3, total, "Ingest the calls — each call is a conversation; MemoryLake extracts the facts")
    conv_ids = ingest_calls(cli, project_id, actors)
    wait_for_memory(cli, conv_ids)

    banner(4, total, "Ingest the notes — hand-off doc and email thread become searchable documents")
    ingest_notes(cli, project_id)

    banner(5, total, "What did MemoryLake remember?")
    show_facts(list_facts(cli, project_id))

    banner(6, total, "The hand-off — the CSM's pre-kickoff brief, straight from account memory")
    brief = build_brief(cli, project_id, top_k)

    if with_agent:
        banner(7, total, "Optional — ask a MemoryLake agent, which reads the same memory")
        ask_agent(cli, project_id)
    return brief


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "brief", "facts", "cleanup"],
                    help="run = full demo (default); brief = only re-generate the brief; "
                         "facts = only list extracted facts; cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo project before ingesting")
    ap.add_argument("--with-agent", action="store_true",
                    help="also ask a MemoryLake agent for a brief (needs model quota on your account)")
    ap.add_argument("--top-k", type=int, default=5, help="facts per question in the brief (default 5)")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()

    # Line-buffer stdout so the commands show up as they run even when piped (`| tee run.log`).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    total = {"cleanup": 2, "brief": 2, "facts": 2}.get(args.command, 6 + (1 if args.with_agent else 0))
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)

    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return

    if args.command in ("brief", "facts"):
        proj = find_project(cli)
        if proj is None:
            die("the demo project does not exist yet; run `python3 demo.py` first")
        if args.command == "facts":
            banner(2, 2, "What does MemoryLake remember about the account?")
            show_facts(list_facts(cli, proj["id"]))
        else:
            banner(2, 2, "The hand-off brief, straight from account memory")
            build_brief(cli, proj["id"], args.top_k)
        return

    run_pipeline(cli, reset=args.reset, with_agent=args.with_agent, top_k=args.top_k)
    print("\nDone. Re-run `python3 demo.py brief` any time, or `python3 demo.py cleanup` to remove the demo data.")


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        print(f"\ncommand failed: {shlex.join(e.cmd) if e.cmd else '(memorylake)'}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

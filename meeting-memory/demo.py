#!/usr/bin/env python3
"""
Meeting memory across tools — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Halden Freight's operations team is moving its Riverside warehouse to a new
system.  The meetings about it live in four tools: the weekly ops sync was
recorded in Otter, the check-in a week later in Granola, the follow-up happened
in a Slack thread, and the go-live runbook sits in Notion.  A confidential call
with the scanner vendor was summarised in Fathom.

Everything goes into one MemoryLake project: the three meetings as group
conversations (each message sent by the person who said it), the two exports as
.docx documents, and every action item pinned to its owner with where it came
from.  Then:

  - "When is the cutover, and did it change?" comes back as one fact that
    carries both dates, from two meetings in two tools;
  - the people tagged `riverside` each have their action items, with source and
    status — the one closed in Slack shows as done;
  - one search returns the Notion runbook and the meeting decisions together;
  - the confidential vendor call is deleted, and the same search no longer
    finds it.

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
SOURCES = DATA / "sources"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-mmt"  # memorylake-usecases / meeting-memory-across-tools; keeps demo ids apart from yours

# Tags are how you find people later: `actor list --tags riverside` is "everyone on this project".
PEOPLE = {
    "ana-ruiz": dict(display="Ana Ruiz", tags="riverside,team-ops", role="Operations lead, Halden Freight"),
    "ben-okafor": dict(display="Ben Okafor", tags="riverside,team-eng", role="Systems engineer, Halden Freight"),
    "cara-lind": dict(display="Cara Lind", tags="riverside,team-ops", role="Warehouse floor manager, Halden Freight"),
    # Not on the project: the tag filter should leave him out.
    "dev-mehta": dict(display="Dev Mehta", tags="team-finance", role="Finance partner, Halden Freight"),
}
TEAM_TAG = "riverside"
PROJECT = dict(
    custom_id=f"{PREFIX}-riverside-cutover",
    name="Riverside cutover — meeting memory",
    description="Meetings, notes and action items for the Riverside warehouse cutover, from every tool. Demo data.",
)

# Asked of the whole memory once everything is in. One search each.
QUESTIONS = [
    ("When is the Riverside cutover, and did it change?",
     "When is the Riverside cutover and why did the date change?"),
    ("What are the go-live risks?",
     "What are the go-live risks for the Riverside cutover?"),
    ("What did we agree with the scanner vendor?",
     "scanner vendor pricing unit price discount penalty"),
]
# The confidential call that gets deleted in the last step, and the question that used to find it.
FORGET_FILE = "scanner-vendor-pricing-call.docx"
FORGET_QUERY = QUESTIONS[2][1]

# Dates the cutover had: the decision chain is any fact that mentions them.
CUTOVER = re.compile(r"\b(October 1[4]|October 21|Oct\.? 1[4]|Oct\.? 21|2026-10-14|2026-10-21)\b")


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

def ensure_actor(cli: CLI, key: str) -> str:
    p = PEOPLE[key]
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", p["display"],
                        "--type", "HUMAN", "--tags", p["tags"], "--description", p["role"])
        note(f"created actor {p['display']} (tags {p['tags']}) → {actor['id']}")
    else:
        note(f"actor {p['display']} already exists → {actor['id']}")
    # Actors are account-wide; they must be bound to the workspace to take part in it.
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")
    emit("actor", "", key=key, id=actor["id"], display=p["display"], role=p["role"], tags=p["tags"].split(","))
    return actor["id"]


def load_meetings() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "meetings").glob("*.json"))]


def source_files() -> list[Path]:
    return sorted(p for p in SOURCES.iterdir() if p.suffix == ".docx")


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for meeting in load_meetings():
        conv = cli.try_run("conv", "get", meeting["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def reset_demo(cli: CLI) -> None:
    """--reset: start over. Action items live on the people, not the project, so they go too."""
    note("--reset: deleting the demo meetings, project and people (their action items go with them)")
    delete_conversations(cli)
    proj = find_project(cli)
    if proj is not None:
        cli.run("proj", "delete", proj["id"], scoped=True)
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor is not None:
            cli.run("actor", "delete", actor["id"])


def ensure_project(cli: CLI) -> tuple[str, bool]:
    """Returns (project_id, fresh)."""
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
        emit("project", "", id=proj["id"], name=proj["name"], fresh=True)
        return proj["id"], True
    note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    emit("project", "", id=proj["id"], name=proj["name"], fresh=False)
    return proj["id"], False


# --------------------------------------------------------------------------- action items

def meeting_label(m: dict) -> str:
    return f"{m['name']} ({m['tool']}, {m['date'][:10]})"


def action_items() -> dict[str, dict]:
    """Every action item, by id, with the meeting that raised it and the one that closed it (if any)."""
    items: dict[str, dict] = {}
    for m in load_meetings():
        for it in m.get("action_items") or []:
            items[it["id"]] = dict(it, raised_in=m)
    for m in load_meetings():
        for c in m.get("closes") or []:
            items[c["id"]].update(closed_in=m, how=c["how"])
    return items


def open_text(it: dict) -> str:
    owner = PEOPLE[it["owner"]]["display"]
    return (f"Action item {it['id']} (open) — owner {owner}: {it['task']}, due {it['due']}. "
            f"Source: {meeting_label(it['raised_in'])}.")


def done_text(it: dict) -> str:
    owner = PEOPLE[it["owner"]]["display"]
    closed = it["closed_in"]
    return (f"Action item {it['id']} (done {closed['date'][:10]}) — owner {owner}: {it['task']}. "
            f"Raised in {meeting_label(it['raised_in'])}; closed in {meeting_label(closed)}: {it['how']}.")


def pinned_texts() -> set[str]:
    texts = set()
    for it in action_items().values():
        texts.add(open_text(it))
        if it.get("closed_in"):
            texts.add(done_text(it))
    return texts


ITEM_ID = re.compile(r"\bAction item (RIV-\d+)\b")


# --------------------------------------------------------------------------- step 3: meetings → conversations

def ingest_meetings(cli: CLI, project_id: str, actors: dict[str, str]) -> list[str]:
    conv_ids: list[str] = []
    items = action_items()
    for meeting in load_meetings():
        conv = cli.try_run("conv", "get", meeting["custom_id"], "--by-custom-id", scoped=True)
        done = 0
        if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
            # Left over from an earlier project of the same name (deleting a project does not delete
            # the conversations that pointed at it). Start that meeting over.
            note(f"conversation “{meeting['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        if conv is None:
            participants = ",".join(actors[k] for k in meeting["participants"])
            # GROUP: several people talk. Each message is sent by the actor who said it.
            conv = cli.run("conv", "create", "--custom-id", meeting["custom_id"], "--project", project_id,
                           "--actors", participants, "--kind", "GROUP", "--name", meeting["name"],
                           "--metadata", f"source={meeting['tool'].lower()}",
                           "--metadata", f"meeting_date={meeting['date'][:10]}", scoped=True)
        else:
            done = len(cli.run("conv", "msg", "list", conv["id"], "--page-size", "50").get("items") or [])
            note(f"conversation “{meeting['name']}” exists with {done} message(s) → {conv['id']}")
        conv_ids.append(conv["id"])
        emit("meeting", "", custom_id=meeting["custom_id"], id=conv["id"], name=meeting["name"], tool=meeting["tool"],
             date=meeting["date"], turns=len(meeting["turns"]), done=done)
        start = datetime.fromisoformat(meeting["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        for i, (speaker, text) in enumerate(meeting["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            # --timestamp dates each memory to the meeting, so a later meeting can supersede it.
            line = f"{PEOPLE[speaker]['display']} ({PEOPLE[speaker]['role']}): {text}"
            args = ["conv", "msg", "append", conv["id"], "--actor", actors[speaker],
                    "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", line]
            if parent:
                args += ["--parent", parent]
            msg = cli.run(*args, scoped=True)
            parent = msg["id"]
            emit("turn", "", custom_id=meeting["custom_id"], index=i, speaker=speaker)
        appended = done < len(meeting["turns"])
        if appended:
            note(f"{len(meeting['turns']) - done} message(s) from {meeting['tool']} appended to “{meeting['name']}”")
        # Pin this meeting's action items to their owners, in the same run that stores the meeting.
        # Extraction does not reliably turn "I'll do X by Friday" into a fact, so the item is stated
        # verbatim, with where it came from. On a re-run they are already there (and may have been
        # reworded by a later meeting), so they are not pinned again.
        new_items = [items[it["id"]] for it in meeting.get("action_items") or []]
        if appended:
            for it in new_items:
                cli.run("fact", "add", "--actor", actors[it["owner"]], open_text(it), scoped=True)
            if new_items:
                note(f"{len(new_items)} action item(s) pinned to their owners")
        if new_items:
            emit("pinned", "", custom_id=meeting["custom_id"],
                 items=[dict(id=it["id"], owner=it["owner"], text=open_text(it)) for it in new_items])
        if appended:
            # Let this meeting finish before the next one goes in, as it would days apart in real life.
            # Processed out of order, a later meeting's date can be folded into an earlier one and the
            # earlier decision lost; in order, the later meeting revises the earlier fact and keeps it.
            wait_for_memory(cli, [conv["id"]])
    return conv_ids


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> None:
    """Messages are stored instantly; the facts are extracted in the background. Poll until done."""
    pending = set(conv_ids)
    t0 = last_report = time.time()
    first = True
    while pending and time.time() - t0 < timeout:
        for cid in sorted(pending):
            try:
                status = cli.run("conv", "cook-status", cid, scoped=True, echo=first)
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                note("cook-status probe failed on the network; will try again on the next poll")
                continue
            if status.get("cook_finished"):
                pending.discard(cid)
                emit("cooked", "", id=cid)
        first = False
        if pending:
            if time.time() - last_report >= 30:
                emit("progress", f"  … {len(pending)} meeting(s) still being processed ({int(time.time() - t0)}s)",
                     pending=len(pending), elapsed=int(time.time() - t0))
                last_report = time.time()
            time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} meeting(s) still processing server-side")
    else:
        note(f"memory ready ({len(conv_ids)} meeting{'s' if len(conv_ids) > 1 else ''}) in {int(time.time() - t0)}s")


def list_facts(cli: CLI, scope: str, scope_id: str, echo: bool = True) -> list[dict]:
    """scope is "projects" or "actors"."""
    facts, token = [], None
    while True:
        args = ["fact", "list", f"--{scope}", scope_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


def close_items(cli: CLI, actors: dict[str, str]) -> None:
    """A later meeting closed an item: replace the owner's open item with a done one.

    Idempotent: whatever the owner holds for that item id that is not the done text is
    removed (the open text, or a version a later meeting reworded), and the done text is
    added once."""
    for it in action_items().values():
        if not it.get("closed_in"):
            continue
        owner = actors[it["owner"]]
        target = done_text(it)
        held = [f for f in list_facts(cli, "actors", owner, echo=False)
                if (ITEM_ID.search(f.get("fact", "")) or [None, None])[1] == it["id"]]
        stale = [f["id"] for f in held if f.get("fact") != target]
        if stale:
            cli.run("fact", "delete", "--actor", owner, *stale, scoped=True)
        if not any(f.get("fact") == target for f in held):
            cli.run("fact", "add", "--actor", owner, target, scoped=True)
            note(f"{it['id']} closed in {it['closed_in']['tool']}: {PEOPLE[it['owner']]['display']}'s open item "
                 f"replaced with a done one")
        emit("closed", "", id=it["id"], owner=it["owner"], fact=target)


# --------------------------------------------------------------------------- step 4: exports from other tools → documents

FILE_KIND = {"msword_file": "Word", "pdf_file": "PDF", "excel_file": "Excel", "txt_file": "Text"}
SOURCE_TOOL = {"riverside-go-live-runbook.docx": "Notion", "scanner-vendor-pricing-call.docx": "Fathom"}


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    docs, token = [], None
    while True:
        args = ["proj", "doc", "list", "--project", project_id]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        docs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return docs


def show_documents(docs: list[dict]) -> None:
    lines = [f"\n  {len(docs)} exported notes in the meeting memory:\n"]
    for d in sorted(docs, key=lambda d: d.get("name") or ""):
        tool = SOURCE_TOOL.get(d.get("name") or "", "")
        lines.append(f"   - {d.get('name'):<36} {('from ' + tool) if tool else '':<12} {d.get('status')}")
    emit("text", "\n".join(lines))
    emit("documents", "", documents=[dict(id=d.get("id"), name=d.get("name"), status=d.get("status"),
                                          tool=SOURCE_TOOL.get(d.get("name") or ""))
                                     for d in sorted(docs, key=lambda d: d.get("name") or "")])


def ingest_sources(cli: CLI, project_id: str) -> None:
    item_ids = []
    for path in source_files():
        # `overwrite` keeps the same Library item id on re-runs instead of creating name_1, name_2, …
        item = cli.run("lib", "upload", str(path), "--on-conflict", "overwrite")
        item_ids.append(item["item_id"])
        note(f"uploaded {item['name']} (exported from {SOURCE_TOOL.get(item['name'], 'another tool')})")
        emit("uploaded", "", name=item["name"], item_id=item["item_id"])
    t0 = time.time()
    result = cli.run("proj", "doc", "import", "--project", project_id, *item_ids, "--wait", scoped=True)
    note(f"imported: {result.get('success_count', 0)} new, {result.get('duplicate_count', 0)} already in project, "
         f"{result.get('failure_count', 0)} failed ({int(time.time() - t0)}s)")
    emit("imported", "", **{k: result.get(k, 0) for k in ("success_count", "duplicate_count", "failure_count")})
    show_documents(list_documents(cli, project_id))


# --------------------------------------------------------------------------- step 5: the decision chain

def fact_date(f: dict) -> str:
    """The latest date the fact mentions (a revised fact carries the old date too)."""
    dates = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", f.get("fact", ""))
    return max(dates) if dates else (f.get("created_at") or "")[:10]


def decision_chain(facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """The cutover date as the memory tells it.

    Returns (merged, dated): facts where MemoryLake folded the old date into the new one
    ("October 21 … previously October 14 … as of 2026-09-22"), and dated facts that carry
    either date. Extraction varies run to run, so the demo shows whichever it got."""
    candidates = [f for f in facts if CUTOVER.search(f.get("fact", "")) and "cutover" in f.get("fact", "").lower()]
    merged = [f for f in candidates if "previously" in f["fact"].lower()]
    dated = [f for f in candidates if f not in merged and "as of " in f["fact"]]
    return merged, sorted(dated, key=fact_date)


def show_decisions(facts: list[dict]) -> None:
    lines = [f"\n  {len(facts)} facts written by MemoryLake from the three meetings (nobody tagged anything):\n"]
    for f in sorted(facts, key=fact_date):
        lines.append(f"   - {f['fact']}")
    merged, dated = decision_chain(facts)
    both = {d for f in dated for d in ("14", "21") if re.search(rf"October {d}|10-{d}", f["fact"])} == {"14", "21"}
    if merged:
        lines.append("\n  ▶ Two meetings in two tools, chained: the new cutover date, and the one it replaced, with both dates:")
        lines += [f"     {f['fact']}" for f in merged]
    elif dated:
        lines.append("\n  ▶ Both cutover dates are in memory, each dated to the meeting that set it:" if both else
                     "\n  ▶ This run, extraction kept only the latest cutover date (the earlier one is not in memory):")
        lines += [f"     {f['fact']}" for f in dated]
    emit("text", "\n".join(lines))
    highlight = merged or dated
    emit("facts", "", facts=[dict(id=f.get("id"), fact=f.get("fact"), date=fact_date(f), chain=f in highlight)
                              for f in sorted(facts, key=fact_date)],
         chain_kind="merged" if merged else ("dated" if dated and both else ("latest" if dated else "none")))


# --------------------------------------------------------------------------- step 6: who owes what

def team_actors(cli: CLI) -> list[dict]:
    """Everyone tagged for this project — found by tag, not by a list kept somewhere else."""
    page = cli.run("actor", "list", "--tags", TEAM_TAG, "--page-size", "50")
    by_custom = {f"{PREFIX}-{k}": k for k in PEOPLE}
    return [dict(a, key=by_custom[a.get("custom_id")]) for a in page.get("items") or []
            if a.get("custom_id") in by_custom]


def show_actions(cli: CLI) -> list[dict]:
    team = team_actors(cli)
    left_out = [PEOPLE[k]["display"] for k in PEOPLE if k not in {a["key"] for a in team}]
    lines = [f"\n  `actor list --tags {TEAM_TAG}` → {len(team)} people"
             + (f" ({', '.join(left_out)} is not tagged {TEAM_TAG}, so not listed)" if left_out else "")]
    pinned = pinned_texts()
    people = []
    for a in sorted(team, key=lambda a: a["display_name"]):
        facts = list_facts(cli, "actors", a["id"])
        items, other = [], []
        for f in facts:
            m = ITEM_ID.search(f.get("fact", ""))
            if m:
                text = f["fact"]
                status = "done" if re.search(r"\(done\b", text) else "open"
                items.append(dict(id=m.group(1), status=status, fact=text, pinned=text in pinned))
            else:
                other.append(f["fact"])
        items.sort(key=lambda x: x["id"])
        lines.append(f"\n  {a['display_name']} · {', '.join(a.get('tags') or [])}")
        for it in items:
            lines.append(f"   [{it['status']:>4}] {it['fact']}")
        if not items:
            lines.append("   (no action items)")
        for o in other:
            lines.append(f"   also remembered: {o}")
        people.append(dict(key=a["key"], id=a["id"], display=a["display_name"], tags=a.get("tags") or [],
                           items=items, other=other))
    n_open = sum(it["status"] == "open" for p in people for it in p["items"])
    n_done = sum(it["status"] == "done" for p in people for it in p["items"])
    lines.append(f"\n  ▶ {n_open} open, {n_done} done — each with its owner, the meeting and tool it came from, "
                 "and where it was closed.")
    emit("text", "\n".join(lines))
    emit("actions", "", people=people, left_out=left_out, open=n_open, done=n_done)
    return people


# --------------------------------------------------------------------------- step 7: one query, every tool

def search(cli: CLI, project_id: str, query: str, top_k: int = 5, types: str | None = None) -> dict:
    args = ["search", query, "--projects", project_id, "--top-k", str(top_k)]
    if types:
        args += ["--types", types]
    res = cli.run(*args, scoped=True)
    return dict(
        query=query,
        facts=[dict(id=f.get("id"), fact=f.get("fact"), score=f.get("score")) for f in res.get("facts") or []],
        documents=[dict(id=d.get("document_id"), name=d.get("document_name") or d.get("file_name"),
                        kind=FILE_KIND.get(d.get("source_type") or "", d.get("source_type") or "file"),
                        tool=SOURCE_TOOL.get(d.get("document_name") or d.get("file_name") or ""),
                        summary=d.get("document_summary"))
                   for d in res.get("documents") or []],
    )


def where(d: dict) -> str:
    return f"{d['kind']}, exported from {d['tool']}" if d.get("tool") else d["kind"]


def show_answer(heading: str, res: dict, docs_shown: int = 2) -> None:
    lines = [f"\n  Q: {heading}"]
    for f in res["facts"][:4]:
        lines.append(f"     fact  {f['fact']}")
    for d in res["documents"][:docs_shown]:
        summary = (d.get("summary") or "").replace("\n", " ").strip()
        if len(summary) > 150:
            summary = summary[:147].rstrip() + "…"
        lines.append(f"     doc   {d['name']}  ({where(d)})")
        if summary:
            lines.append(f"           {summary}")
    if not res["facts"] and not res["documents"]:
        lines.append("     (nothing matched)")
    emit("text", "\n".join(lines))


def ask_questions(cli: CLI, project_id: str, top_k: int) -> list[dict]:
    answers = []
    for heading, query in QUESTIONS:
        res = search(cli, project_id, query, top_k)
        show_answer(heading, res)
        answers.append(dict(heading=heading, **res))
        emit("answer", "", heading=heading, **res)
    return answers


def render_brief(answers: list[dict], people: list[dict] | None) -> str:
    lines = ["# Riverside cutover — meeting brief",
             "",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from MemoryLake meeting memory "
             f"(project `{PROJECT['custom_id']}`). Every line is a retrieved memory, not a summary._",
             ""]
    for a in answers:
        lines.append(f"## {a['heading']}")
        if not a["facts"]:
            lines.append("_No facts matched._")
        for f in a["facts"]:
            lines.append(f"- {f['fact']}")
        if a["documents"]:
            lines.append("")
            lines.append("Sources: " + "; ".join(f"`{d['name']}` ({where(d)})" for d in a["documents"]))
        lines.append("")
    if people:
        lines.append("## Action items")
        for p in people:
            for it in p["items"]:
                lines.append(f"- **{it['status']}** · {it['fact']}")
        lines.append("")
    return "\n".join(lines)


def build_brief(cli: CLI, project_id: str, top_k: int, people: list[dict] | None = None) -> str:
    answers = ask_questions(cli, project_id, top_k)
    brief = render_brief(answers, people)
    OUT.mkdir(exist_ok=True)
    (OUT / "meeting-brief.md").write_text(brief, encoding="utf-8")
    note(f"brief written to {OUT / 'meeting-brief.md'}")
    emit("brief", "", markdown=brief, answers=answers)
    return brief


# --------------------------------------------------------------------------- step 8: forget a meeting

def forget_meeting(cli: CLI, project_id: str) -> None:
    before = search(cli, project_id, FORGET_QUERY, 5, types="document")
    hit = [d for d in before["documents"] if d["name"] == FORGET_FILE]
    note(f"before: {len(before['documents'])} document hit(s) — "
         + (", ".join(d["name"] for d in before["documents"]) or "none"))
    doc = next((d for d in list_documents(cli, project_id, echo=False) if d.get("name") == FORGET_FILE), None)
    if doc is None:
        note(f"{FORGET_FILE} is not in the project; nothing to delete")
    else:
        # Removes the indexed content and every memory derived from it. No confirmation prompt.
        cli.run("proj", "doc", "delete", "--project", project_id, doc["id"], scoped=True)
    after = search(cli, project_id, FORGET_QUERY, 5, types="document")
    still = [d for d in after["documents"] if d["name"] == FORGET_FILE]
    note(f"after:  {len(after['documents'])} document hit(s) — "
         + (", ".join(d["name"] for d in after["documents"]) or "none"))
    gone = bool(hit) and not still
    emit("text", f"\n  ▶ {FORGET_FILE}: "
         + ("found before, gone after — what was only in that call can no longer be retrieved."
            if gone else ("still found." if still else "was not found before the delete either.")))
    emit("forgot", "", name=FORGET_FILE, query=FORGET_QUERY, before=[d["name"] for d in before["documents"]],
         after=[d["name"] for d in after["documents"]], gone=gone)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversations(cli)
    note(f"deleted {n} meeting conversation(s)")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted project (its documents and facts went with it)")
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted demo actors (their action items went with them)")
    # Library files are not owned by the project; remove the ones we uploaded, by name.
    names = {p.name for p in source_files()}
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
    note(f"deleted {removed} uploaded file(s) from the Library")


# --------------------------------------------------------------------------- the whole pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False, top_k: int = 5, first_step: int = 2) -> str:
    """Steps 2–8. Step 1, connecting, is the caller's job."""
    banner(first_step, TOTAL, "Set up — four people, tagged by team, and one project for the cutover")
    if reset:
        reset_demo(cli)
    actors = {key: ensure_actor(cli, key) for key in PEOPLE}
    project_id, _fresh = ensure_project(cli)

    banner(3, TOTAL, "Meetings from three tools — Otter, Granola, Slack — into one memory")
    conv_ids = ingest_meetings(cli, project_id, actors)
    wait_for_memory(cli, conv_ids)
    close_items(cli, actors)

    banner(4, TOTAL, "Notes exported from Notion and Fathom — .docx documents")
    ingest_sources(cli, project_id)

    banner(5, TOTAL, "The decision chain — what was decided, and what it replaced")
    show_decisions(list_facts(cli, "projects", project_id))

    banner(6, TOTAL, f"Who owes what — everyone tagged `{TEAM_TAG}`, and their action items")
    people = show_actions(cli)

    banner(7, TOTAL, "One query, every tool — the meeting brief")
    brief = build_brief(cli, project_id, top_k, people)

    banner(8, TOTAL, "Forget a meeting — delete the confidential vendor call for good")
    forget_meeting(cli, project_id)
    return brief


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "brief", "actions", "facts", "cleanup"],
                    help="run = full demo (default); brief = only ask the questions again; "
                         "actions = only list everyone's action items; facts = only list the meeting facts; "
                         "cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true",
                    help="delete and recreate the demo project, meetings and people before ingesting")
    ap.add_argument("--top-k", type=int, default=5, help="results per source type for each question (default 5)")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()

    # Line-buffer stdout so the commands show up as they run even when piped (`| tee run.log`).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    total = 2 if args.command != "run" else TOTAL
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)

    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return

    if args.command in ("brief", "actions", "facts"):
        proj = find_project(cli)
        if proj is None:
            die("the demo project does not exist yet; run `python3 demo.py` first")
        if args.command == "facts":
            banner(2, 2, "What does MemoryLake remember from the meetings?")
            show_decisions(list_facts(cli, "projects", proj["id"]))
        elif args.command == "actions":
            banner(2, 2, f"Who owes what — everyone tagged `{TEAM_TAG}`")
            show_actions(cli)
        else:
            banner(2, 2, "The meeting brief, answered from memory")
            build_brief(cli, proj["id"], args.top_k)
        return

    run_pipeline(cli, reset=args.reset, top_k=args.top_k)
    print("\nDone. Try `python3 demo.py actions` or `python3 demo.py brief`, "
          "or `python3 demo.py cleanup` to remove the demo data.")


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

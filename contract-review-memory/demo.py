#!/usr/bin/env python3
"""
Contract review that remembers your positions — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Harbrook Logistics' legal team reviews supplier contracts against a playbook
of standard positions.  Every deal also leaves precedents behind: in 2025
Elena Voss accepted an 18-month liability cap from Northwind Freight (their
cargo insurance stood behind it) and a 24-month data-breach super-cap from
Halden Analytics.  That knowledge lived in Elena's head and in two review
sessions with her AI assistant.

Now Northwind's renewal redline lands on the desk of Sam Okafor, who joined
in September.  The demo gives the team:

  * a curated playbook project — the standard positions, pinned as facts, and
    the playbook PDF;
  * one actor per counterparty, holding the exceptions accepted from it;
  * a reviews project — every review session is a conversation, with the
    redline under review attached to its first message.

Then Sam's review starts with memory:

  * one search scoped to the playbook project AND the Northwind actor loads
    our standard positions plus Northwind's precedents — Halden's precedents
    stay out (and come back when the scope says Halden);
  * last year's Northwind review session is found by its metadata and replayed
    message by message; the redline attached to it is fetched back from
    MemoryLake and diffed against the new one, clause by clause.

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
LIBRARY_DIR = DATA / "library"
INCOMING = DATA / "incoming" / "northwind-msa-redline-v3-2026-09.md"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-crm"  # memorylake-usecases / contract-review-memory; keeps demo ids apart from yours
LIBRARY_ROOT = f"{PREFIX}-contracts"  # Library folder that mirrors data/library/

# The people (and the AI assistant) who take part in review sessions.
PEOPLE = {
    "elena-voss": dict(display="Elena Voss", type="HUMAN", tags="legal,reviewer",
                       role="Senior counsel, Harbrook Logistics"),
    "assistant": dict(display="Harbrook review assistant", type="ASSISTANT", tags="legal,ai",
                      role="AI contract-review assistant"),
}
# One actor per counterparty: the exceptions accepted from it are stored as that actor's facts.
COUNTERPARTIES = {
    "northwind-freight": dict(display="Northwind Freight", tags="counterparty,carrier",
                              role="Counterparty — freight carrier, MSA NWF-MSA-2025"),
    "halden-analytics": dict(display="Halden Analytics", tags="counterparty,saas-vendor",
                             role="Counterparty — SaaS analytics vendor, HAL-SAAS-2025"),
}
PROJECTS = {
    "playbook": dict(custom_id=f"{PREFIX}-playbook", name="Harbrook contract playbook", library="playbook",
                     description="Standard contract positions, curated by Legal. Demo data."),
    "reviews": dict(custom_id=f"{PREFIX}-reviews", name="Harbrook contract reviews", library="redlines",
                    description="Review sessions (one conversation each) and the redlines they reviewed. Demo data."),
}

# The renewal under review, and the clause topics Sam checks before reading it. A search always
# returns its top-k, relevant or not; `guard` keeps only the hits that are about the clause.
RENEWAL = dict(counterparty="northwind-freight", contract="NWF-MSA-2025")
TOPICS = [
    dict(clause="9.2", title="Limitation of liability", query="limitation of liability cap in months of fees",
         guard=r"liabilit|\bcap\b|super-cap"),
    dict(clause="5.1", title="Payment terms", query="payment terms net days early-payment discount",
         guard=r"payment|\bnet \d+"),
    dict(clause="2.1", title="Term and renewal", query="automatic renewal term and notice",
         guard=r"renew"),
]

FILE_KIND = {"pdf_file": "PDF", "excel_file": "Excel", "txt_file": "Text", "markdown_file": "Markdown",
             "ppt_file": "PowerPoint", "msword_file": "Word"}
MIME = {".md": "text/markdown", ".pdf": "application/pdf"}

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


def ensure_actor(cli: CLI, key: str, spec: dict, actor_type: str) -> str:
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", spec["display"],
                        "--type", actor_type, "--tags", spec["tags"], "--description", spec["role"])
        note(f"created actor {spec['display']} → {actor['id']}")
    else:
        note(f"actor {spec['display']} already exists → {actor['id']}")
    bind(cli, actor["id"])
    emit("actor", "", key=key, id=actor["id"], display=spec["display"], role=spec["role"], type=actor_type,
         counterparty=key in COUNTERPARTIES)
    return actor["id"]


def find_actor_ids(cli: CLI, keys) -> dict[str, str]:
    found = {k: cli.try_run("actor", "get", f"{PREFIX}-{k}", "--by-custom-id") for k in keys}
    missing = [k for k, a in found.items() if a is None]
    if missing:
        die(f"the demo actor(s) {', '.join(missing)} do not exist yet; run `python3 demo.py` first")
    return {k: a["id"] for k, a in found.items()}


def load_sessions() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "sessions").glob("*.json"))]


def load_positions() -> list[str]:
    return json.loads((DATA / "positions.json").read_text(encoding="utf-8"))["positions"]


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def find_project(cli: CLI, key: str):
    return cli.try_run("proj", "get", PROJECTS[key]["custom_id"], "--by-custom-id", scoped=True)


def find_projects(cli: CLI) -> dict[str, str]:
    found = {key: find_project(cli, key) for key in PROJECTS}
    missing = [k for k, p in found.items() if p is None]
    if missing:
        die(f"the demo project(s) {', '.join(missing)} do not exist yet; run `python3 demo.py` first")
    return {key: p["id"] for key, p in found.items()}


def ensure_projects(cli: CLI, reset: bool) -> dict[str, str]:
    if reset:
        existing = [p for p in (find_project(cli, k) for k in PROJECTS) if p is not None]
        if existing:
            note("--reset: deleting the review sessions, both projects, the counterparties' facts and the Library folder")
            delete_conversations(cli)
            for proj in existing:
                cli.run("proj", "delete", proj["id"], scoped=True)
            delete_library_root(cli)
            for key in COUNTERPARTIES:  # their facts are actor-scoped; deleting the actor removes them
                actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
                if actor:
                    cli.run("actor", "delete", actor["id"])
    ids = {}
    for key, spec in PROJECTS.items():
        proj = find_project(cli, key)
        if proj is None:
            proj = cli.run("proj", "create", "--name", spec["name"], "--custom-id", spec["custom_id"],
                           "--description", spec["description"], scoped=True)
            note(f"created project “{proj['name']}” → {proj['id']}")
        else:
            note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
        ids[key] = proj["id"]
        emit("project", "", key=key, id=proj["id"], name=proj["name"])
    return ids


# --------------------------------------------------------------------------- step 3: Library + import

def lib_children(cli: CLI, parent: str, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        args = ["lib", "list", parent, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None)
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def item_id(item: dict) -> str:
    return item.get("item_id") or item.get("id")


def ensure_folder(cli: CLI, parent: str, name: str) -> str:
    for item in lib_children(cli, parent, echo=False):
        if item.get("name") == name:
            return item_id(item)
    return item_id(cli.run("lib", "mkdir", name, "--parent", parent, "--on-conflict", "deny"))


def find_library_root(cli: CLI, echo: bool = True) -> str | None:
    for item in lib_children(cli, "MY_SPACE", echo=echo):
        if item.get("name") == LIBRARY_ROOT:
            return item_id(item)
    return None


def delete_library_root(cli: CLI) -> bool:
    root = find_library_root(cli)
    if root is None:
        return False
    cli.run("lib", "delete", root)  # deleting a folder removes everything inside it
    return True


def library_files() -> list[Path]:
    return sorted(p for p in LIBRARY_DIR.rglob("*") if p.is_file() and not p.name.startswith("."))


def mirror_library(cli: CLI) -> tuple[dict[str, str], dict[str, dict]]:
    """Recreate data/library/... as Library folders and upload every file.

    Returns ({top-level folder: id}, {relative path: upload reply with uri and item_id})."""
    root = find_library_root(cli, echo=False)
    if root is None:
        root = item_id(cli.run("lib", "mkdir", LIBRARY_ROOT, "--on-conflict", "deny"))
        note(f"created Library folder {LIBRARY_ROOT}/ → {root}")
    else:
        note(f"Library folder {LIBRARY_ROOT}/ already exists → {root}")
    folders: dict[tuple, str] = {(): root}
    uploaded: dict[str, dict] = {}
    for path in library_files():
        rel = path.relative_to(LIBRARY_DIR)
        parts = rel.parts[:-1]
        for i in range(1, len(parts) + 1):
            if parts[:i] not in folders:
                folders[parts[:i]] = ensure_folder(cli, folders[parts[:i - 1]], parts[i - 1])
        # `overwrite` keeps the same Library item id on re-runs, so attachments keep pointing at it.
        uploaded[rel.as_posix()] = cli.run("lib", "upload", str(path), "--parent", folders[parts],
                                           "--on-conflict", "overwrite")
        emit("uploaded", "", path=rel.as_posix())
    lines = [f"\n  {LIBRARY_ROOT}/"] + [f"   {p}" for p in uploaded]
    emit("text", "\n".join(lines))
    emit("tree", "", root=LIBRARY_ROOT, files=list(uploaded))
    return {k[0]: v for k, v in folders.items() if len(k) == 1}, uploaded


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


def import_library(cli: CLI, projects: dict[str, str], folders: dict[str, str]) -> None:
    for key, project_id in projects.items():
        t0 = time.time()
        folder = folders[PROJECTS[key]["library"]]
        rc, out, err = cli.raw("proj", "doc", "import", "--project", project_id, folder, "--recursive",
                               "--wait", scoped=True)
        if rc != 0:
            raise CLIError(["proj", "doc", "import"], rc, out, err)
        result = json.loads(out) if out.strip() else {}
        # The "Importing N file(s) resolved from 1 argument(s)" line goes to stderr; stdout is the JSON.
        expanded = next((ln.strip() for ln in err.splitlines() if ln.strip().startswith("Importing")), "")
        if expanded:
            emit("text", f"  {expanded}")
        note(f"{PROJECTS[key]['name']}: {result.get('success_count', 0)} new, "
             f"{result.get('duplicate_count', 0)} already in the project, {result.get('failure_count', 0)} failed "
             f"({int(time.time() - t0)}s)")
        emit("imported", "", project=key, expanded=expanded,
             **{k: result.get(k, 0) for k in ("success_count", "duplicate_count", "failure_count")})
    lines, payload = [], {}
    for key, project_id in projects.items():
        docs = sorted(list_documents(cli, project_id), key=lambda d: d.get("name") or "")
        payload[key] = [dict(id=d.get("id"), name=d.get("name"), status=d.get("status")) for d in docs]
        lines.append(f"\n  {PROJECTS[key]['name']}: {len(docs)} document(s)")
        lines += [f"   - {d.get('name'):<44} {d.get('status')}" for d in docs]
    emit("text", "\n".join(lines))
    emit("documents", "", projects=payload)


# --------------------------------------------------------------------------- facts

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


def pin(cli: CLI, scope_flag: str, scope_id: str, texts: list[str], label: str) -> None:
    # Nothing else writes to these scopes (no conversation involves the playbook project or a
    # counterparty actor), so an exact-text match is a safe "already pinned" test on re-runs.
    scope = "projects" if scope_flag == "--project" else "actors"
    have = {f.get("fact") for f in list_facts(cli, scope, scope_id, echo=False)}
    missing = [t for t in texts if t not in have]
    if missing:
        cli.run("fact", "add", scope_flag, scope_id, *missing, scoped=True)
    note(f"{label}: {len(missing)} pinned now, {len(texts) - len(missing)} already there")


def pin_positions(cli: CLI, projects: dict[str, str]) -> None:
    positions = load_positions()
    pin(cli, "--project", projects["playbook"], positions, "standard positions → Harbrook contract playbook")
    emit("text", "\n".join(f"   📌 {p}" for p in positions))
    emit("positions", "", positions=positions)


# --------------------------------------------------------------------------- step 5: review sessions

def wait_for_memory(cli: CLI, conv_id: str, timeout: int = 600) -> bool:
    """Messages are stored instantly; the facts are extracted in the background. Poll until done."""
    t0 = last_report = time.time()
    first = True
    while time.time() - t0 < timeout:
        try:
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first)
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            note("cook-status probe failed on the network; will try again on the next poll")
            status = {}
        first = False
        if status.get("cook_finished"):
            note(f"memory ready in {int(time.time() - t0)}s")
            emit("cooked", "", id=conv_id, seconds=int(time.time() - t0))
            return True
        if time.time() - last_report >= 30:
            emit("progress", f"  … still being processed ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
            last_report = time.time()
        time.sleep(5)
    note(f"gave up after {timeout}s; still processing server-side")
    return False


def speaker_line(key: str, text: str) -> str:
    p = PEOPLE[key]
    return f"{p['display']} ({p['role']}): {text}"


def ingest_sessions(cli: CLI, projects: dict[str, str], actors: dict[str, str], counterparties: dict[str, str],
                    uploaded: dict[str, dict]) -> None:
    project_id = projects["reviews"]
    for session in load_sessions():
        cp = COUNTERPARTIES[session["counterparty"]]
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        done = 0
        if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
            # Left over from a deleted project (deleting a project does not delete its conversations).
            note(f"session “{session['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        if conv is None:
            # Metadata is how the next reviewer finds this session again (step 7).
            conv = cli.run("conv", "create", "--custom-id", session["custom_id"], "--project", project_id,
                           "--actors", f"{actors[session['reviewer']]},{actors['assistant']}", "--kind", "DIRECT",
                           "--name", session["name"], "--metadata", "kind=contract-review",
                           "--metadata", f"counterparty={session['counterparty']}",
                           "--metadata", f"contract={session['contract']}", scoped=True)
        else:
            done = len(cli.run("conv", "msg", "list", conv["id"], "--page-size", "50").get("items") or [])
            note(f"session “{session['name']}” exists with {done} message(s) → {conv['id']}")
        emit("session", "", custom_id=session["custom_id"], id=conv["id"], name=session["name"],
             counterparty=session["counterparty"], contract=session["contract"], date=session["date"],
             turns=len(session["turns"]), done=done, attachment=Path(session["attachment"]).name)
        start = datetime.fromisoformat(session["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        attachment = uploaded[Path(session["attachment"]).relative_to("library").as_posix()]
        for i, (speaker, text) in enumerate(session["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            args = ["conv", "msg", "append", conv["id"], "--actor", actors[speaker],
                    "--custom-id", f"turn-{i:02d}", "--timestamp", ts]
            if i == 1:
                # The redline under review travels with the first message as a FILE block pointing at
                # the Library item. `mime_type` is required (the API answers 500 without it).
                name = Path(session["attachment"]).name
                blocks = [{"block_type": "TEXT", "text": speaker_line(speaker, text)},
                          {"block_type": "FILE", "uri": attachment["uri"], "name": name,
                           "mime_type": MIME.get(Path(name).suffix, "application/octet-stream")}]
                args += ["--content-json", json.dumps(blocks, ensure_ascii=False)]
            else:
                args += ["--text", speaker_line(speaker, text)]
            if parent:
                args += ["--parent", parent]
            parent = cli.run(*args, scoped=True)["id"]
            emit("turn", "", custom_id=session["custom_id"], index=i, speaker=speaker,
                 attachment=Path(session["attachment"]).name if i == 1 else None)
        if done < len(session["turns"]):
            note(f"{len(session['turns']) - done} message(s) appended; waiting for MemoryLake to extract the facts")
            # One session at a time: sessions cooking in parallel finish in any order.
            wait_for_memory(cli, conv["id"])
        pin(cli, "--actor", counterparties[session["counterparty"]], session["precedents"],
            f"accepted exceptions → {cp['display']}")
        emit("precedents", "", counterparty=session["counterparty"], notes=session["precedents"])


def owner_labels(cli: CLI, projects: dict[str, str], counterparties: dict[str, str],
                 echo: bool = False) -> dict[str, str]:
    """Search hits carry no owner; map fact ids back to the scope that holds them."""
    labels = {f["id"]: "Harbrook standard" for f in list_facts(cli, "projects", projects["playbook"], echo=echo)}
    for key, actor_id in counterparties.items():
        labels.update({f["id"]: f"{COUNTERPARTIES[key]['display']} precedent"
                       for f in list_facts(cli, "actors", actor_id, echo=echo)})
    labels.update({f["id"]: "review sessions" for f in list_facts(cli, "projects", projects["reviews"], echo=echo)})
    return labels


def show_memory(cli: CLI, projects: dict[str, str], counterparties: dict[str, str]) -> None:
    scopes = [("Harbrook contract playbook (project)", "projects", projects["playbook"])]
    scopes += [(f"{COUNTERPARTIES[k]['display']} (counterparty actor)", "actors", a) for k, a in counterparties.items()]
    scopes += [("Harbrook contract reviews (project — extracted from the sessions)", "projects", projects["reviews"])]
    pinned = set(load_positions()) | {n for s in load_sessions() for n in s["precedents"]}
    lines, payload = [], []
    for title, scope, sid in scopes:
        facts = sorted(list_facts(cli, scope, sid), key=lambda f: f.get("created_at") or "")
        lines.append(f"\n  {title}: {len(facts)} fact(s)")
        lines += [f"   {'📌' if f.get('fact') in pinned else '-'} {f.get('fact')}" for f in facts]
        payload.append(dict(title=title, facts=[dict(fact=f.get("fact"), pinned=f.get("fact") in pinned)
                                                for f in facts]))
    emit("text", "\n".join(lines))
    emit("memory", "", scopes=payload)


# --------------------------------------------------------------------------- step 6: context, scoped

def search(cli: CLI, query: str, projects: list[str], actors: list[str], top_k: int = 4,
           types: str | None = "fact") -> dict:
    args = ["search", query, "--projects", ",".join(projects), "--actors", ",".join(actors), "--top-k", str(top_k)]
    if types:
        args += ["--types", types]
    return cli.run(*args, scoped=True)


def load_context(cli: CLI, projects: dict[str, str], counterparties: dict[str, str], counterparty: str,
                 labels: dict[str, str], topics=TOPICS, top_k: int = 4) -> list[dict]:
    """Our standard positions + this counterparty's precedents, one search per clause topic."""
    name = COUNTERPARTIES[counterparty]["display"]
    others = [COUNTERPARTIES[k]["display"] for k in COUNTERPARTIES if k != counterparty]
    results = []
    for topic in topics:
        res = search(cli, topic["query"], [projects["playbook"]], [counterparties[counterparty]], top_k=top_k)
        found = [dict(id=f.get("id"), fact=f.get("fact"), owner=labels.get(f.get("id"), "?"))
                 for f in res.get("facts") or []]
        hits = [h for h in found if re.search(topic["guard"], h["fact"] or "", re.I)]
        lines = [f"\n  {topic['clause']} {topic['title']} — context for {name}"]
        lines += [f"     [{h['owner']}] {h['fact']}" for h in hits] or ["     (nothing matched)"]
        if len(found) > len(hits):
            lines.append(f"     ({len(found) - len(hits)} other hit(s) not about this clause, left out)")
        emit("text", "\n".join(lines))
        leaked = [h for h in hits if any(h["owner"].startswith(o) for o in others)]
        results.append(dict(topic=topic, counterparty=counterparty, hits=hits, leaked=len(leaked)))
        emit("context", "", clause=topic["clause"], title=topic["title"], counterparty=counterparty,
             counterparty_name=name, hits=hits, leaked=len(leaked))
    return results


def possessive(name: str) -> str:
    return name + ("'" if name.endswith("s") else "'s")


def context_step(cli: CLI, projects: dict[str, str], counterparties: dict[str, str]) -> list[dict]:
    labels = owner_labels(cli, projects, counterparties)
    ctx = load_context(cli, projects, counterparties, RENEWAL["counterparty"], labels)
    other = next(k for k in COUNTERPARTIES if k != RENEWAL["counterparty"])
    emit("text", f"\n  Same liability question, scoped to {COUNTERPARTIES[other]['display']} instead:")
    contrast = load_context(cli, projects, counterparties, other, labels, topics=TOPICS[:1])
    leaked = sum(c["leaked"] for c in ctx + contrast)
    me, them = COUNTERPARTIES[RENEWAL["counterparty"]]["display"], COUNTERPARTIES[other]["display"]
    verdict = (f"✓ no {them} precedent in {me}'s context, and no {me} precedent in {possessive(them)}" if not leaked
               else f"✗ {leaked} hit(s) came from the other counterparty")
    emit("text", f"\n  {verdict}")
    emit("isolation", "", leaked=leaked, verdict=verdict)
    return ctx


# --------------------------------------------------------------------------- step 7: replay the last review

def find_sessions(cli: CLI, counterparty: str) -> list[dict]:
    """`conv list` has no server-side filter; pick review sessions by their metadata."""
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
    mine = [c for c in convs if (c.get("metadata") or {}).get("kind") == "contract-review"
            and (c.get("metadata") or {}).get("counterparty") == counterparty]
    note(f"{len(convs)} conversation(s) in the workspace; {len(mine)} review session(s) with "
         f"metadata counterparty={counterparty}")
    return mine


def list_messages(cli: CLI, conv_id: str) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=token is None)
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return sorted(msgs, key=lambda m: m.get("sequence_no") or 0)


def clauses(text: str) -> dict[str, str]:
    """`- **9.2 Limitation of liability.** text` → {"9.2": "Limitation of liability. text"}."""
    found = {}
    for m in re.finditer(r"^- \*\*(\d+\.\d+) ([^*]+)\*\*\s*(.+)$", text, re.M):
        found[m.group(1)] = f"{m.group(2).strip()} {m.group(3).strip()}"
    return found


def replay_step(cli: CLI, projects: dict[str, str], actor_names: dict[str, str]) -> list[dict]:
    sessions = find_sessions(cli, RENEWAL["counterparty"])
    if not sessions:
        die("no review session for this counterparty; run `python3 demo.py` first")
    conv = sessions[0]
    meta = conv.get("metadata") or {}
    emit("text", f"\n  ▸ {conv.get('name')}   {conv['id']}\n    metadata: "
                 + ", ".join(f"{k}={v}" for k, v in sorted(meta.items())))
    attachments, transcript = [], []
    msgs = list_messages(cli, conv["id"])
    lines = []
    for m in msgs:
        when = (m.get("timestamp") or "")[:16].replace("T", " ")
        who = actor_names.get(m.get("actor_id"), m.get("actor_id"))
        for block in m.get("content") or []:
            if block.get("block_type") == "TEXT":
                body = block.get("text") or ""
                body = body.split("): ", 1)[1] if "): " in body else body  # drop the speaker label
                lines.append(f"   {when}  {who}: {body}")
                transcript.append(dict(when=when, who=who, text=body))
            elif block.get("block_type") == "FILE":
                lines.append(f"   {'':16}  📎 {block.get('mime_type')}  {block.get('uri')}")
                attachments.append(block)
                transcript.append(dict(when=when, who=who, file=block.get("uri"), mime=block.get("mime_type")))
    emit("text", "\n".join(lines))
    emit("replay", "", id=conv["id"], name=conv.get("name"), metadata=meta, messages=transcript)
    if not attachments:
        die("the session has no attachment")

    # The FILE block points at the Library item; the item tells us the file name, and the
    # document imported from it into the reviews project gives us the original bytes back.
    uri = attachments[0]["uri"]
    item = cli.run("lib", "get", uri.rsplit("/", 1)[-1])
    name = item.get("name")
    doc = next((d for d in list_documents(cli, projects["reviews"]) if d.get("name") == name), None)
    if doc is None:
        die(f"{name} is not imported into the reviews project")
    OUT.mkdir(exist_ok=True)
    target = OUT / name
    cli.run("proj", "doc", "download", "--project", projects["reviews"], doc["id"], "--output", str(target),
            "--force", scoped=True)
    note(f"attachment fetched back from MemoryLake → out/{name} ({target.stat().st_size} bytes)")

    old, new = clauses(target.read_text(encoding="utf-8")), clauses(INCOMING.read_text(encoding="utf-8"))
    changes = [dict(clause=c, old=old.get(c), new=new.get(c)) for c in sorted(set(old) | set(new),
               key=lambda c: [int(x) for x in c.split(".")]) if old.get(c) != new.get(c)]
    lines = [f"\n  {name}  →  {INCOMING.name}: {len(changes)} of {len(new)} clauses changed"]
    for ch in changes:
        lines += [f"\n   {ch['clause']}", f"     - {ch['old'] or '(absent)'}", f"     + {ch['new'] or '(removed)'}"]
    emit("text", "\n".join(lines))
    emit("diff", "", old=name, new=INCOMING.name, total=len(new), changes=changes)
    return changes


# --------------------------------------------------------------------------- step 8: renewal brief

def render_brief(changes: list[dict], context: list[dict], session_name: str) -> str:
    name = COUNTERPARTIES[RENEWAL["counterparty"]]["display"]
    by_clause = {c["topic"]["clause"]: c for c in context}
    lines = [f"# Renewal brief — {name}, {RENEWAL['contract']} (redline v3)", "",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Changes are the clause-level "
             f"diff between the redline attached to “{session_name}” (fetched back from MemoryLake) and the new "
             f"redline. Context lines are retrieved memories: our standard positions and {name}'s precedents._", ""]
    for ch in changes:
        ctx = by_clause.get(ch["clause"])
        lines += [f"## {ch['clause']} — changed", "", f"- 2025 text: {ch['old'] or '(absent)'}",
                  f"- 2026 redline: {ch['new'] or '(removed)'}", ""]
        if ctx is None:
            lines += ["_No topic search configured for this clause._", ""]
            continue
        precedent = [h for h in ctx["hits"] if h["owner"].endswith("precedent")]
        lines += [f"- **{h['owner']}** — {h['fact']}" for h in ctx["hits"]]
        lines += ["", ("**Action:** the counterparty moved away from the text it signed last time — escalate to the "
                       "General Counsel with the precedent attached (playbook §6)." if precedent else
                       "**Action:** no precedent with this counterparty — review against the standard position."), ""]
    return "\n".join(lines)


def brief_step(cli: CLI, projects: dict[str, str], counterparties: dict[str, str], changes: list[dict],
               context: list[dict]) -> str:
    session = next(s for s in load_sessions() if s["counterparty"] == RENEWAL["counterparty"])
    brief = render_brief(changes, context, session["name"])
    OUT.mkdir(exist_ok=True)
    (OUT / "renewal-brief.md").write_text(brief, encoding="utf-8")
    summary = []
    for ch in changes:
        ctx = next((c for c in context if c["topic"]["clause"] == ch["clause"]), None)
        has_precedent = bool(ctx and any(h["owner"].endswith("precedent") for h in ctx["hits"]))
        summary.append(dict(clause=ch["clause"], escalate=has_precedent))
        emit("text", f"   {ch['clause']}  {'escalate — departs from the 2025 precedent' if has_precedent else 'check against the standard position'}")
    note(f"brief written to {OUT / 'renewal-brief.md'}")
    emit("brief", "", markdown=brief, summary=summary)
    return brief


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversations(cli)
    note(f"deleted {n} conversation(s)")
    for key in PROJECTS:
        proj = find_project(cli, key)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
            note(f"deleted project {PROJECTS[key]['name']} (its documents and facts went with it)")
    for key in [*PEOPLE, *COUNTERPARTIES]:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted demo actors (the counterparties' precedents went with them)")
    if delete_library_root(cli):
        note(f"deleted Library folder {LIBRARY_ROOT}/ and everything in it")


# --------------------------------------------------------------------------- the whole pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False, first_step: int = 2) -> str:
    """Steps 2–8. Step 1, connecting, is the caller's job."""
    banner(first_step, TOTAL, "Set up — a reviewer, her AI assistant, two counterparties, two projects")
    projects = ensure_projects(cli, reset)
    actors = {key: ensure_actor(cli, key, spec, spec["type"]) for key, spec in PEOPLE.items()}
    counterparties = {key: ensure_actor(cli, key, spec, "HUMAN") for key, spec in COUNTERPARTIES.items()}

    banner(3, TOTAL, "The playbook PDF and last year's redlines go into the Library and the projects")
    folders, uploaded = mirror_library(cli)
    import_library(cli, projects, folders)

    banner(4, TOTAL, "Pin the playbook's standard positions")
    pin_positions(cli, projects)

    banner(5, TOTAL, "Last year's review sessions become memory — the redline attached to each")
    ingest_sessions(cli, projects, actors, counterparties, uploaded)
    show_memory(cli, projects, counterparties)

    banner(6, TOTAL, "Northwind's renewal lands — load our positions + Northwind's precedents, nobody else's")
    context = context_step(cli, projects, counterparties)

    banner(7, TOTAL, "Reopen last year's review — replay it, fetch its redline, diff against the new one")
    names = {aid: PEOPLE[k]["display"] for k, aid in actors.items()}
    changes = replay_step(cli, projects, names)

    banner(8, TOTAL, "Renewal brief — every changed clause with the position and precedent behind it")
    return brief_step(cli, projects, counterparties, changes, context)


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "context", "replay", "brief", "facts", "cleanup"],
                    help="run = full demo (default); context = only the scoped searches; replay = only replay "
                         "last year's session and diff the redlines; brief = context + replay + the brief; "
                         "facts = list what each scope remembers; cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo data before ingesting")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()

    # Line-buffer stdout so the commands show up as they run even when piped (`| tee run.log`).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    total = {"run": TOTAL, "brief": 4}.get(args.command, 2)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)

    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command != "run":
        projects = find_projects(cli)
        counterparties = find_actor_ids(cli, COUNTERPARTIES)
        names = {aid: PEOPLE[k]["display"] for k, aid in find_actor_ids(cli, PEOPLE).items()}
        if args.command == "context":
            banner(2, 2, "Our positions + Northwind's precedents, nobody else's")
            context_step(cli, projects, counterparties)
        elif args.command == "replay":
            banner(2, 2, "Replay last year's Northwind review and diff the redlines")
            replay_step(cli, projects, names)
        elif args.command == "facts":
            banner(2, 2, "What each scope remembers")
            show_memory(cli, projects, counterparties)
        else:
            banner(2, 4, "Our positions + Northwind's precedents, nobody else's")
            context = context_step(cli, projects, counterparties)
            banner(3, 4, "Replay last year's Northwind review and diff the redlines")
            changes = replay_step(cli, projects, names)
            banner(4, 4, "Renewal brief")
            brief_step(cli, projects, counterparties, changes, context)
        return

    run_pipeline(cli, reset=args.reset)
    print("\nDone. Re-run `python3 demo.py context`, `replay` or `brief` any time, "
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

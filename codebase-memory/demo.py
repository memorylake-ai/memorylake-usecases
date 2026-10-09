#!/usr/bin/env python3
"""
Codebase memory for engineering teams — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tidewell's payments team owns two repositories: `ledger-service` (the money
ledger) and `checkout-web` (the payment pages).  Their knowledge lives where
engineering knowledge always lives — ADRs and runbooks in `docs/`, a deck from
last year's architecture review, PR review threads, an incident review in
Slack.  Jun Park joined two weeks ago and his AI assistant keeps suggesting the
patterns the team already learned to avoid.

The demo gives each repository its own MemoryLake project — one memory per
repo.  Each repo's `docs/` folder is mirrored into the Library and imported
with a single `--recursive` command; the PR reviews and the incident review
become conversations (MemoryLake extracts the dated facts); the hard-won
gotchas are pinned as facts.  Then:

  * a pre-commit check asks each repo's memory about a proposed change — the
    repo that banned the pattern blocks it, the other repo has never heard of it;
  * Jun's day-one questions are asked across both repos at once, and every
    answer is labelled with the repo it came from — including last year's
    architecture decision, found inside a PowerPoint deck.

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
REPOS_DIR = DATA / "repos"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cdb"  # memorylake-usecases / codebase-memory; keeps demo ids apart from yours
LIBRARY_ROOT = f"{PREFIX}-docs"  # Library folder that mirrors each repo's docs/ tree

PEOPLE = {
    "priya-raman": dict(display="Priya Raman", tags="engineering,ledger", role="Staff engineer, owns ledger-service"),
    "tomas-ortega": dict(display="Tomás Ortega", tags="engineering,checkout", role="Frontend lead, owns checkout-web"),
    "jun-park": dict(display="Jun Park", tags="engineering,new-hire", role="Backend engineer, joined 2026-09"),
}
# One memory per repository: each repo is its own project.
REPOS = {
    "ledger-service": dict(custom_id=f"{PREFIX}-ledger-service", name="ledger-service — codebase memory",
                           description="ADRs, runbooks, review decks, PR reviews and incident reviews for ledger-service. Demo data."),
    "checkout-web": dict(custom_id=f"{PREFIX}-checkout-web", name="checkout-web — codebase memory",
                         description="ADRs, conventions and PR reviews for checkout-web. Demo data."),
}

# What Jun's AI assistant wants to do. Each proposal is checked against every repo's memory.
# A retrieved fact blocks the change when it is about the pattern (`guard`) and forbids or warns
# against it (`BLOCKS`): "moment.js was added to package.json" is about moment.js but forbids nothing.
PROPOSALS = [
    dict(change="Add moment.js to format the payment date on the receipt",
         query="add the moment.js library to format dates",
         guard=r"moment\.?js"),
    dict(change="Add a NOT NULL column to ledger_entries in one migration",
         query="add a NOT NULL column to the ledger_entries table in a migration",
         guard=r"NOT NULL|expand-contract"),
]
BLOCKS = r"\b(do not|don't|never|must not|cannot|can't|removed|deprecated|take [\w.]+ out|locks?|write lock|expand-contract)\b"

# Jun's day-one questions: one search each, across both repos at once. Some answers live only in
# documents (last year's decision is in a slide deck, the on-call steps in a runbook), so those
# questions search documents only; the browser question is about rules, so it searches facts.
QUESTIONS = [
    ("Why doesn't the ledger use Kafka?",
     "why was Kafka rejected as the ledger source of truth", "document"),
    ("How do we store and show money amounts?",
     "store money amounts as integer minor units and format amounts", None),
    ("What must a client do when it retries a payment?",
     "retry a payment request idempotency key", None),
    ("Can the payment page call ledger-service directly?",
     "browser payment page calling ledger-service directly", "fact"),
    ("The outbox relay alert fired — what is safe to do?",
     "outbox relay lag alert on-call what to do", "document"),
]

FILE_KIND = {"pdf_file": "PDF", "excel_file": "Excel", "txt_file": "Text", "markdown_file": "Markdown",
             "ppt_file": "PowerPoint", "msword_file": "Word"}

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


def load_threads() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "threads").glob("*.json"))]


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for thread in load_threads():
        conv = cli.try_run("conv", "get", thread["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def find_project(cli: CLI, repo: str):
    return cli.try_run("proj", "get", REPOS[repo]["custom_id"], "--by-custom-id", scoped=True)


def find_projects(cli: CLI) -> dict[str, str]:
    found = {repo: find_project(cli, repo) for repo in REPOS}
    missing = [r for r, p in found.items() if p is None]
    if missing:
        die(f"the demo project for {', '.join(missing)} does not exist yet; run `python3 demo.py` first")
    return {repo: p["id"] for repo, p in found.items()}


def ensure_projects(cli: CLI, reset: bool) -> dict[str, str]:
    """One project per repository. Returns {repo: project_id}."""
    if reset:
        existing = [p for p in (find_project(cli, r) for r in REPOS) if p is not None]
        if existing:
            note("--reset: deleting the demo conversations, both repo projects and the mirrored docs folder")
            delete_conversations(cli)
            for proj in existing:
                cli.run("proj", "delete", proj["id"], scoped=True)
            delete_library_root(cli)
    ids = {}
    for repo, spec in REPOS.items():
        proj = find_project(cli, repo)
        if proj is None:
            proj = cli.run("proj", "create", "--name", spec["name"], "--custom-id", spec["custom_id"],
                           "--description", spec["description"], scoped=True)
            note(f"created project “{proj['name']}” → {proj['id']}")
        else:
            note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
        ids[repo] = proj["id"]
        emit("project", "", repo=repo, id=proj["id"], name=proj["name"])
    return ids


# --------------------------------------------------------------------------- step 3: mirror docs/ into the Library

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
    folder = cli.run("lib", "mkdir", name, "--parent", parent, "--on-conflict", "deny")
    return item_id(folder)


def find_library_root(cli: CLI, echo: bool = True) -> str | None:
    for item in lib_children(cli, "MY_SPACE", echo=echo):
        if item.get("name") == LIBRARY_ROOT:
            return item_id(item)
    return None


def delete_library_root(cli: CLI) -> bool:
    root = find_library_root(cli)
    if root is None:
        return False
    # Deleting a folder removes everything inside it.
    cli.run("lib", "delete", root)
    return True


def repo_files(repo: str) -> list[Path]:
    base = REPOS_DIR / repo
    return sorted(p for p in base.rglob("*") if p.is_file() and not p.name.startswith("."))


def mirror_docs(cli: CLI) -> dict[str, str]:
    """Recreate data/repos/<repo>/docs/... as Library folders and upload every file.

    Returns {repo: library folder id}. A real setup would run this from CI on every merge to main."""
    root = find_library_root(cli, echo=False)
    if root is None:
        root = item_id(cli.run("lib", "mkdir", LIBRARY_ROOT, "--on-conflict", "deny"))
        note(f"created Library folder {LIBRARY_ROOT}/ → {root}")
    else:
        note(f"Library folder {LIBRARY_ROOT}/ already exists → {root}")
    repo_folders: dict[str, str] = {}
    for repo in REPOS:
        folders = {(): ensure_folder(cli, root, repo)}
        repo_folders[repo] = folders[()]
        for path in repo_files(repo):
            rel = path.relative_to(REPOS_DIR / repo)
            parts = rel.parts[:-1]
            for i in range(1, len(parts) + 1):
                if parts[:i] not in folders:
                    folders[parts[:i]] = ensure_folder(cli, folders[parts[:i - 1]], parts[i - 1])
            # `overwrite` keeps the same Library item id on re-runs instead of creating name_1, name_2, …
            cli.run("lib", "upload", str(path), "--parent", folders[parts], "--on-conflict", "overwrite")
            emit("uploaded", "", repo=repo, path=f"{repo}/{rel.as_posix()}")
        note(f"{repo}: {len(repo_files(repo))} file(s) in {LIBRARY_ROOT}/{repo}/")
    show_tree()
    return repo_folders


def show_tree() -> None:
    lines = [f"\n  {LIBRARY_ROOT}/"]
    tree = {}
    for repo in REPOS:
        files = [p.relative_to(REPOS_DIR / repo).as_posix() for p in repo_files(repo)]
        tree[repo] = files
        lines.append(f"   {repo}/")
        lines += [f"     {f}" for f in files]
    emit("text", "\n".join(lines))
    emit("tree", "", root=LIBRARY_ROOT, repos=tree)


# --------------------------------------------------------------------------- step 4: one recursive import per repo

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


def import_docs(cli: CLI, projects: dict[str, str], folders: dict[str, str]) -> None:
    for repo, project_id in projects.items():
        t0 = time.time()
        # One folder id + --recursive: the CLI expands the whole subtree, whatever its depth.
        rc, out, err = cli.raw("proj", "doc", "import", "--project", project_id, folders[repo], "--recursive",
                               "--wait", scoped=True)
        if rc != 0:
            raise CLIError(["proj", "doc", "import"], rc, out, err)
        result = json.loads(out) if out.strip() else {}
        expanded = next((ln.strip() for ln in err.splitlines() if ln.strip().startswith("Importing")), "")
        if expanded:
            emit("text", f"  {expanded}")
        note(f"{repo}: {result.get('success_count', 0)} new, {result.get('duplicate_count', 0)} already in the project, "
             f"{result.get('failure_count', 0)} failed ({int(time.time() - t0)}s)")
        emit("imported", "", repo=repo, expanded=expanded,
             **{k: result.get(k, 0) for k in ("success_count", "duplicate_count", "failure_count")})
    show_documents(cli, projects)


def show_documents(cli: CLI, projects: dict[str, str]) -> None:
    lines, payload = [], {}
    for repo, project_id in projects.items():
        docs = sorted(list_documents(cli, project_id), key=lambda d: d.get("name") or "")
        payload[repo] = [dict(id=d.get("id"), name=d.get("name"), status=d.get("status")) for d in docs]
        lines.append(f"\n  {repo}: {len(docs)} document(s)")
        lines += [f"   - {d.get('name'):<48} {d.get('status')}" for d in docs]
    emit("text", "\n".join(lines))
    emit("documents", "", repos=payload)


# --------------------------------------------------------------------------- step 5: reviews → conversations

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


def ingest_threads(cli: CLI, projects: dict[str, str], actors: dict[str, str]) -> None:
    known = {repo: {f.get("fact") for f in list_facts(cli, pid, echo=False)} for repo, pid in projects.items()}
    for thread in load_threads():
        project_id = projects[thread["repo"]]
        conv = cli.try_run("conv", "get", thread["custom_id"], "--by-custom-id", scoped=True)
        done = 0
        if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
            # Left over from a deleted project (deleting a project does not delete its conversations).
            note(f"conversation “{thread['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        if conv is None:
            participants = ",".join(actors[k] for k in thread["participants"])
            conv = cli.run("conv", "create", "--custom-id", thread["custom_id"], "--project", project_id,
                           "--actors", participants, "--kind", "GROUP", "--name", thread["name"],
                           "--metadata", f"source={thread['source']}", scoped=True)
        else:
            done = len(cli.run("conv", "msg", "list", conv["id"], "--page-size", "50").get("items") or [])
            note(f"conversation “{thread['name']}” exists with {done} message(s) → {conv['id']}")
        emit("thread", "", custom_id=thread["custom_id"], id=conv["id"], repo=thread["repo"], name=thread["name"],
             source=thread["source"], date=thread["date"], turns=len(thread["turns"]), done=done)
        start = datetime.fromisoformat(thread["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        for i, (speaker, text) in enumerate(thread["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            line = f"{PEOPLE[speaker]['display']} ({PEOPLE[speaker]['role']}): {text}"
            args = ["conv", "msg", "append", conv["id"], "--actor", actors[speaker],
                    "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", line]
            if parent:
                args += ["--parent", parent]
            parent = cli.run(*args, scoped=True)["id"]
            emit("turn", "", custom_id=thread["custom_id"], index=i, speaker=speaker)
        appended = done < len(thread["turns"])
        if appended:
            note(f"{len(thread['turns']) - done} message(s) appended; waiting for MemoryLake to extract the facts")
            # One thread at a time: threads cooking in parallel finish in any order.
            wait_for_memory(cli, conv["id"])
        # Pin the gotchas in the same run that stores the thread (extraction may reword a pinned fact
        # in place later, so matching on text alone would pin it twice on a re-run).
        missing = [n for n in thread.get("pinned") or [] if n not in known[thread["repo"]]] if appended else []
        if missing:
            cli.run("fact", "add", "--project", project_id, *missing, scoped=True)
            note(f"{len(missing)} gotcha(s) pinned to {thread['repo']} as facts")
        emit("pinned", "", custom_id=thread["custom_id"], repo=thread["repo"], notes=thread.get("pinned") or [])


# --------------------------------------------------------------------------- step 6: what each repo remembers

def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", "--projects", project_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None)
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


def pinned_notes() -> set[str]:
    return {n for t in load_threads() for n in t.get("pinned") or []}


def show_facts(cli: CLI, projects: dict[str, str]) -> dict[str, list[dict]]:
    pinned = pinned_notes()
    by_repo, lines = {}, []
    for repo, project_id in projects.items():
        facts = sorted(list_facts(cli, project_id), key=lambda f: f.get("created_at") or "")
        by_repo[repo] = facts
        n_pinned = sum(f.get("fact") in pinned for f in facts)
        lines.append(f"\n  {repo}: {len(facts)} facts — {len(facts) - n_pinned} extracted by MemoryLake from the "
                     f"threads, {n_pinned} pinned verbatim")
        lines += [f"   {'📌' if f.get('fact') in pinned else '-'} {f.get('fact')}" for f in facts]
    emit("text", "\n".join(lines))
    emit("facts", "", repos={repo: [dict(id=f.get("id"), fact=f.get("fact"), pinned=f.get("fact") in pinned)
                                    for f in facts] for repo, facts in by_repo.items()})
    return by_repo


# --------------------------------------------------------------------------- step 7: pre-commit check, per repo

def search(cli: CLI, project_ids: list[str], query: str, top_k: int = 5, types: str | None = None) -> dict:
    args = ["search", query, "--projects", ",".join(project_ids), "--top-k", str(top_k)]
    if types:
        args += ["--types", types]
    return cli.run(*args, scoped=True)


def precommit_check(cli: CLI, projects: dict[str, str]) -> list[dict]:
    results = []
    for prop in PROPOSALS:
        lines = [f"\n  Proposed change: “{prop['change']}”"]
        verdicts = []
        for repo, project_id in projects.items():
            res = search(cli, [project_id], prop["query"], top_k=3, types="fact")
            hits = [f.get("fact") for f in res.get("facts") or []
                    if re.search(prop["guard"], f.get("fact") or "", re.I) and re.search(BLOCKS, f.get("fact") or "", re.I)]
            if hits:
                lines.append(f"     {repo:<15} ✋ blocked — this repo's memory says:")
                lines += [f"                     {h}" for h in hits[:2]]
            else:
                lines.append(f"     {repo:<15} ✓ nothing in this repo's memory speaks against it")
            verdicts.append(dict(repo=repo, blocked=bool(hits), facts=hits[:2]))
        emit("text", "\n".join(lines))
        emit("check", "", change=prop["change"], query=prop["query"], verdicts=verdicts)
        results.append(dict(change=prop["change"], verdicts=verdicts))
    return results


# --------------------------------------------------------------------------- step 8: day-one questions, across repos

def owner_maps(cli: CLI, projects: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Search results do not say which project a hit came from; map ids back to repos."""
    facts, docs = {}, {}
    for repo, project_id in projects.items():
        facts.update({f["id"]: repo for f in list_facts(cli, project_id, echo=False)})
        docs.update({d["id"]: repo for d in list_documents(cli, project_id, echo=False)})
    return facts, docs


def ask(cli: CLI, projects: dict[str, str], heading: str, query: str, top_k: int,
        maps: tuple[dict[str, str], dict[str, str]], types: str | None = None) -> dict:
    fact_repo, doc_repo = maps
    res = search(cli, list(projects.values()), query, top_k=top_k, types=types)
    answer = dict(
        heading=heading, query=query, types=types,
        facts=[dict(id=f.get("id"), fact=f.get("fact"), repo=fact_repo.get(f.get("id"), "?"))
               for f in res.get("facts") or []],
        documents=[dict(id=d.get("document_id"), name=d.get("document_name") or d.get("file_name"),
                        repo=doc_repo.get(d.get("document_id"), "?"),
                        kind=FILE_KIND.get(d.get("source_type") or "", d.get("source_type") or "file"),
                        summary=d.get("document_summary"))
                   for d in res.get("documents") or []],
    )
    lines = [f"\n  Q: {heading}"]
    for f in answer["facts"][:3]:
        lines.append(f"     [{f['repo']}] fact  {f['fact']}")
    for d in answer["documents"][:2]:
        lines.append(f"     [{d['repo']}] doc   {d['name']}  ({d['kind']})")
        summary = (d.get("summary") or "").replace("\n", " ").strip()
        if summary:
            lines.append(f"                     {summary[:147].rstrip() + '…' if len(summary) > 150 else summary}")
    if not answer["facts"] and not answer["documents"]:
        lines.append("     (nothing matched)")
    emit("text", "\n".join(lines))
    emit("answer", "", **answer)
    return answer


def render_brief(answers: list[dict]) -> str:
    lines = ["# Day-one brief — ledger-service + checkout-web", "",
             f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by searching both repos' "
             "MemoryLake projects at once. Every line is a retrieved memory, labelled with the repo it lives in._", ""]
    for a in answers:
        lines.append(f"## {a['heading']}")
        if not a["facts"] and not a["documents"]:
            lines.append("_Nothing matched._")
        lines += [f"- **{f['repo']}** — {f['fact']}" for f in a["facts"]]
        if a["documents"]:
            lines += ["", "Sources: " + "; ".join(f"`{d['repo']}/{d['name']}` ({d['kind']})" for d in a["documents"])]
        lines.append("")
    return "\n".join(lines)


def build_brief(cli: CLI, projects: dict[str, str], top_k: int) -> str:
    maps = owner_maps(cli, projects)
    answers = [ask(cli, projects, heading, query, top_k, maps, types) for heading, query, types in QUESTIONS]
    brief = render_brief(answers)
    OUT.mkdir(exist_ok=True)
    (OUT / "day-one-brief.md").write_text(brief, encoding="utf-8")
    note(f"brief written to {OUT / 'day-one-brief.md'}")
    emit("brief", "", markdown=brief, answers=answers)
    return brief


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = delete_conversations(cli)
    note(f"deleted {n} conversation(s)")
    for repo in REPOS:
        proj = find_project(cli, repo)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
            note(f"deleted project {repo} (its documents and facts went with it)")
    for key in PEOPLE:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted demo actors")
    # Library files are not owned by a project; the mirrored folder goes in one call.
    if delete_library_root(cli):
        note(f"deleted Library folder {LIBRARY_ROOT}/ and everything in it")


# --------------------------------------------------------------------------- the whole pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False, top_k: int = 4, first_step: int = 2) -> str:
    """Steps 2–8. Step 1, connecting, is the caller's job."""
    banner(first_step, TOTAL, "Set up — three engineers, and one project per repository")
    actors = {key: ensure_actor(cli, key) for key in PEOPLE}
    projects = ensure_projects(cli, reset)

    banner(3, TOTAL, "Mirror each repo's docs/ folder into the Library — ADRs, runbooks, a review deck")
    folders = mirror_docs(cli)

    banner(4, TOTAL, "One recursive import per repo — the whole docs/ tree in one command")
    import_docs(cli, projects, folders)

    banner(5, TOTAL, "PR reviews and an incident review become memory")
    ingest_threads(cli, projects, actors)

    banner(6, TOTAL, "What does each repo remember?")
    show_facts(cli, projects)

    banner(7, TOTAL, "Pre-commit check — the AI assistant asks each repo's memory first")
    precommit_check(cli, projects)

    banner(8, TOTAL, "Day one — Jun's questions, asked across both repos at once")
    return build_brief(cli, projects, top_k)


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "check", "brief", "facts", "cleanup"],
                    help="run = full demo (default); check = only the pre-commit checks; brief = only the "
                         "day-one questions; facts = only list each repo's facts; "
                         "cleanup = delete everything the demo created")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo projects before ingesting")
    ap.add_argument("--top-k", type=int, default=4, help="results per source type for each question (default 4)")
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
    if args.command in ("check", "brief", "facts"):
        projects = find_projects(cli)
        if args.command == "check":
            banner(2, 2, "Pre-commit check — each proposed change against each repo's memory")
            precommit_check(cli, projects)
        elif args.command == "facts":
            banner(2, 2, "What does each repo remember?")
            show_facts(cli, projects)
        else:
            banner(2, 2, "Day-one questions, answered across both repos")
            build_brief(cli, projects, args.top_k)
        return

    run_pipeline(cli, reset=args.reset, top_k=args.top_k)
    print("\nDone. Re-run `python3 demo.py check` or `python3 demo.py brief` any time, "
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

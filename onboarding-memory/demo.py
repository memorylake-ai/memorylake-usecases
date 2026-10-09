#!/usr/bin/env python3
"""
Onboarding memory for HR teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Fernhill Robotics (about 400 people) keeps its people policies in an employee
handbook from January 2026 and in two policy-update memos since.  Policies
change: parental leave went from 16 to 20 weeks on 2026-07-01, the travel
meal allowance changed twice, and a 25-day vacation allowance is signed off
but only starts on 2027-01-01.  The old handbook is still around, and it is
still what most people find first.

On 2026-10-12 two people start: Priya Nair, a senior software engineer, and
Tom Alvarez, an engineering intern.  The demo gives People Ops:

  * a policies project — the handbook and both memos, plus EVERY version of
    every policy pinned as a fact stamped with its id, version, effective
    date, sign-off and source document (nothing is ever overwritten);
  * one actor per new hire, tagged with their onboarding stage and carrying
    their role in metadata; Priya's pre-boarding chat with the onboarding
    assistant becomes memory about her.

Then:

  * Priya asks about parental leave.  The search brings back BOTH the
    16-week and the 20-week version, and the January handbook.  The version
    stamps let the assistant answer with the policy in force today and mark
    the rest stale — with the source and sign-off behind the answer;
  * an audit asks "what was in force on 2026-03-15?" and gets every policy's
    version for that day from the same facts;
  * on day one both hires move to `stage:day-1` (`actor update`), and the
    same question — "what happens on my first day?" — gives the engineer and
    the intern different plans, picked by the role in their metadata.

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
LIBRARY_DIR = DATA / "library"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"  # isolated CLI config, used only with an explicit API key
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ohr"  # memorylake-usecases / onboarding-memory (HR); keeps demo ids apart from yours
LIBRARY_ROOT = f"{PREFIX}-handbook"  # Library folder that mirrors data/library/

# The onboarding assistant takes part in the pre-boarding chat.
PEOPLE = {
    "assistant": dict(display="Fernhill onboarding assistant", type="ASSISTANT", tags="people-ops,ai",
                      role="AI onboarding assistant, Fernhill People team"),
}
# One actor per new hire. The stage lives in the tags (so `actor list --tags` finds who starts today);
# the role and the start date live in metadata.
HIRES = {
    "priya-nair": dict(display="Priya Nair", role="Senior software engineer, robot-fleet team",
                       metadata=dict(role="engineer", start_date="2026-10-12", team="robot-fleet",
                                     manager="Jonas Berg", office="Denver")),
    "tom-alvarez": dict(display="Tom Alvarez", role="Engineering intern, autumn cohort",
                        metadata=dict(role="intern", start_date="2026-10-12", team="perception",
                                      manager="Lena Okoro", office="Boston lab")),
}
STAGES = ["pre-boarding", "day-1", "week-1"]
PROJECTS = {
    "policies": dict(custom_id=f"{PREFIX}-policies", name="Fernhill people policies", library="handbook",
                     description="Handbook, policy memos, and every version of every policy. Demo data."),
    "chats": dict(custom_id=f"{PREFIX}-chats", name="Fernhill onboarding chats", library=None,
                  description="Pre-boarding chats between new hires and the onboarding assistant. Demo data."),
}

# What Priya asks before her first day. `guard` decides which of her own facts are about the question.
QUESTIONS = [
    dict(policy="POL-LEAVE", q="How long is parental leave?", guard=r"leave|child|baby|parent|expect"),
    dict(policy="POL-MEAL", q="What is the daily meal allowance when I travel?", guard=r"travel|trip|boston|meal"),
    dict(policy="POL-PTO", q="How many vacation days do I get?", guard=r"vacation|holiday|time off|pto"),
]
FIRST_DAY_Q = "What happens on my first day?"
AUDIT_DATE = "2026-03-15"  # "what policy was active in March?"

# `POL-LEAVE v2 · Parental leave · effective 2026-07-01 · signed off 2026-06-12 by Dana Whitfield ·
#  source policy-update-memo-2026-06.md §1 — 20 weeks …`
STAMP = re.compile(r"^(?P<policy>POL-[A-Z]+) v(?P<v>\d+) · (?P<title>[^·]+?) · effective (?P<effective>\d{4}-\d{2}-\d{2})"
                   r" · signed off (?P<signed>\d{4}-\d{2}-\d{2}) by (?P<signer>[^·]+?) · source (?P<source>\S+)"
                   r" (?P<section>§[\d.]+) — (?P<body>.+)$")
FLOW = re.compile(r"^FLOW (?P<role>[\w-]+) · (?P<stage>[\w-]+) — (?P<body>.+)$")

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

def bind(cli: CLI, actor_id: str) -> None:
    # Actors are account-wide; they must be bound to the workspace to take part in it.
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")


def stage_tags(stage: str) -> str:
    return f"new-hire,stage:{stage}"


def hire_metadata(key: str, stage: str) -> str:
    # `actor update --metadata` REPLACES the stored object, so always send every key.
    return json.dumps(dict(HIRES[key]["metadata"], stage=stage), separators=(",", ":"))


def ensure_actor(cli: CLI, key: str, display: str, role: str, actor_type: str, tags: str,
                 metadata: str | None = None) -> str:
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        args = ["actor", "create", "--custom-id", custom_id, "--display-name", display, "--type", actor_type,
                "--tags", tags, "--description", role]
        if metadata:
            args += ["--metadata", metadata]
        actor = cli.run(*args)
        note(f"created actor {display} → {actor['id']}")
    elif key in HIRES and sorted(actor.get("tags") or []) != sorted(tags.split(",")):
        # A previous run moved this hire on to day 1; the story starts before day 1 again.
        actor = cli.run("actor", "update", actor["id"], "--tags", tags, "--metadata", metadata)
        note(f"actor {display} already exists → {actor['id']}; tags set back to {tags}")
    else:
        note(f"actor {display} already exists → {actor['id']}")
    bind(cli, actor["id"])
    emit("actor", "", key=key, id=actor["id"], display=display, role=role, type=actor_type,
         tags=actor.get("tags") or tags.split(","), metadata=actor.get("metadata") or {}, hire=key in HIRES)
    return actor["id"]


def ensure_people(cli: CLI) -> tuple[dict[str, str], dict[str, str]]:
    people = {k: ensure_actor(cli, k, p["display"], p["role"], p["type"], p["tags"]) for k, p in PEOPLE.items()}
    hires = {k: ensure_actor(cli, k, h["display"], h["role"], "HUMAN", stage_tags("pre-boarding"),
                             hire_metadata(k, "pre-boarding")) for k, h in HIRES.items()}
    return people, hires


def find_actor_ids(cli: CLI, keys) -> dict[str, str]:
    found = {k: cli.try_run("actor", "get", f"{PREFIX}-{k}", "--by-custom-id") for k in keys}
    missing = [k for k, a in found.items() if a is None]
    if missing:
        die(f"the demo actor(s) {', '.join(missing)} do not exist yet; run `python3 demo.py` first")
    return {k: a["id"] for k, a in found.items()}


def load_sessions() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "sessions").glob("*.json"))]


def load_policies() -> dict:
    return json.loads((DATA / "policies.json").read_text(encoding="utf-8"))


def pinned_texts() -> list[str]:
    spec = load_policies()
    return [v for p in spec["policies"] for v in p["versions"]] + spec["flows"]


def delete_conversations(cli: CLI) -> int:
    """Conversations are workspace-scoped: deleting a project does not remove them, so do it by hand."""
    removed = 0
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        if conv is not None:
            cli.run("conv", "delete", conv["id"], scoped=True)
            removed += 1
    return removed


def delete_hires(cli: CLI) -> None:
    for key in HIRES:  # what was remembered about them is actor-scoped; deleting the actor removes it
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])


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
            note("--reset: deleting the chat, both projects, the new hires (with their memory) and the Library folder")
            delete_conversations(cli)
            for proj in existing:
                cli.run("proj", "delete", proj["id"], scoped=True)
            delete_library_root(cli)
            delete_hires(cli)
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


def mirror_library(cli: CLI) -> dict[str, str]:
    """Recreate data/library/... as Library folders and upload every file. Returns {top-level folder: id}."""
    root = find_library_root(cli, echo=False)
    if root is None:
        root = item_id(cli.run("lib", "mkdir", LIBRARY_ROOT, "--on-conflict", "deny"))
        note(f"created Library folder {LIBRARY_ROOT}/ → {root}")
    else:
        note(f"Library folder {LIBRARY_ROOT}/ already exists → {root}")
    folders: dict[tuple, str] = {(): root}
    uploaded = []
    for path in library_files():
        rel = path.relative_to(LIBRARY_DIR)
        parts = rel.parts[:-1]
        for i in range(1, len(parts) + 1):
            if parts[:i] not in folders:
                folders[parts[:i]] = ensure_folder(cli, folders[parts[:i - 1]], parts[i - 1])
        cli.run("lib", "upload", str(path), "--parent", folders[parts], "--on-conflict", "overwrite")
        uploaded.append(rel.as_posix())
        emit("uploaded", "", path=rel.as_posix())
    emit("text", "\n".join([f"\n  {LIBRARY_ROOT}/"] + [f"   {p}" for p in uploaded]))
    emit("tree", "", root=LIBRARY_ROOT, files=uploaded)
    return {k[0]: v for k, v in folders.items() if len(k) == 1}


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
        if not PROJECTS[key]["library"]:
            continue
        t0 = time.time()
        rc, out, err = cli.raw("proj", "doc", "import", "--project", project_id, folders[PROJECTS[key]["library"]],
                               "--recursive", "--wait", scoped=True)
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
        docs = sorted(list_documents(cli, project_id), key=lambda d: d.get("name") or "")
        emit("text", "\n".join([f"\n  {PROJECTS[key]['name']}: {len(docs)} document(s)"]
                               + [f"   - {d.get('name'):<40} {d.get('status')}" for d in docs]))
        emit("documents", "", project=key, docs=[dict(id=d.get("id"), name=d.get("name"), status=d.get("status"))
                                                 for d in docs])


# --------------------------------------------------------------------------- step 4: policy versions

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
    # No conversation touches the policies project, so an exact-text match is a safe
    # "already pinned" test on re-runs.
    scope = "projects" if scope_flag == "--project" else "actors"
    have = {f.get("fact") for f in list_facts(cli, scope, scope_id, echo=False)}
    missing = [t for t in texts if t not in have]
    if missing:
        cli.run("fact", "add", scope_flag, scope_id, *missing, scoped=True)
    note(f"{label}: {len(missing)} pinned now, {len(texts) - len(missing)} already there")


def stamp(text: str) -> dict | None:
    m = STAMP.match(text or "")
    return dict(m.groupdict(), v=int(m.group("v"))) if m else None


def resolve(versions: list[dict], as_of: str) -> dict:
    """Split one policy's versions into the one in force on `as_of`, older ones, and scheduled ones."""
    ordered = sorted(versions, key=lambda s: (s["effective"], s["v"]))
    live = [s for s in ordered if s["effective"] <= as_of]
    current = live[-1] if live else None
    nxt = {a["v"]: b for a, b in zip(ordered, ordered[1:])}  # who replaced whom
    # A version signed off after `as_of` did not exist yet on that day, so it is not "scheduled" either.
    return dict(current=current, superseded=[dict(s, by=nxt.get(s["v"])) for s in live[:-1]],
                scheduled=[s for s in ordered if s["effective"] > as_of and s["signed"] <= as_of])


def catalog(cli: CLI, project_id: str, echo: bool = True) -> dict[str, list[dict]]:
    """Every policy version stored in the project, by policy id, parsed from its stamp."""
    by_policy: dict[str, list[dict]] = {}
    for f in list_facts(cli, "projects", project_id, echo=echo):
        s = stamp(f.get("fact"))
        if s:
            by_policy.setdefault(s["policy"], []).append(dict(s, id=f.get("id"), text=f.get("fact")))
    return by_policy


def pin_policies(cli: CLI, projects: dict[str, str]) -> None:
    spec = load_policies()
    pin(cli, "--project", projects["policies"], [v for p in spec["policies"] for v in p["versions"]],
        "policy versions → Fernhill people policies")
    pin(cli, "--project", projects["policies"], spec["flows"], "role-specific onboarding flows → Fernhill people policies")
    lines, timeline = [], []
    for p in spec["policies"]:
        versions = [stamp(v) for v in p["versions"]]
        lines.append(f"\n  {p['id']}  {p['title']}")
        lines += [f"   📌 v{s['v']}  effective {s['effective']}  signed off {s['signed']} by {s['signer']}  "
                  f"({s['source']} {s['section']})\n        {s['body']}" for s in versions]
        timeline.append(dict(policy=p["id"], title=p["title"], versions=versions))
    flows = [FLOW.match(f).groupdict() for f in spec["flows"]]
    lines.append("\n  Onboarding flows")
    lines += [f"   📌 {f['role']:<9} {f['stage']:<7} {f['body']}" for f in flows]
    emit("text", "\n".join(lines))
    emit("policies", "", timeline=timeline, flows=flows)


# --------------------------------------------------------------------------- step 5: pre-boarding chat

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


def display_of(key: str) -> tuple[str, str]:
    p = PEOPLE.get(key) or HIRES[key]
    return p["display"], p["role"]


def ingest_chat(cli: CLI, projects: dict[str, str], people: dict[str, str], hires: dict[str, str]) -> None:
    # The chat lives in its own project: facts extracted from a conversation land on the project it
    # belongs to, and the policies project must hold nothing but the pinned, verbatim versions.
    project_id = projects["chats"]
    ids = {**people, **hires}
    for session in load_sessions():
        conv = cli.try_run("conv", "get", session["custom_id"], "--by-custom-id", scoped=True)
        done = 0
        if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
            # Left over from a deleted project (deleting a project does not delete its conversations).
            note(f"chat “{session['name']}” belongs to a deleted project; recreating it")
            cli.run("conv", "delete", conv["id"], scoped=True)
            conv = None
        if conv is None:
            conv = cli.run("conv", "create", "--custom-id", session["custom_id"], "--project", project_id,
                           "--actors", f"{hires[session['hire']]},{people['assistant']}", "--kind", "DIRECT",
                           "--name", session["name"], "--metadata", "kind=pre-boarding",
                           "--metadata", f"hire={session['hire']}", scoped=True)
        else:
            done = len(cli.run("conv", "msg", "list", conv["id"], "--page-size", "50").get("items") or [])
            note(f"chat “{session['name']}” exists with {done} message(s) → {conv['id']}")
        emit("session", "", custom_id=session["custom_id"], id=conv["id"], name=session["name"],
             hire=session["hire"], date=session["date"], turns=len(session["turns"]), done=done)
        start = datetime.fromisoformat(session["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
        parent = conv.get("current_message_id")
        for i, (speaker, text) in enumerate(session["turns"], start=1):
            if i <= done:
                continue
            ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            name, role = display_of(speaker)
            args = ["conv", "msg", "append", conv["id"], "--actor", ids[speaker], "--custom-id", f"turn-{i:02d}",
                    "--timestamp", ts, "--text", f"{name} ({role}): {text}"]
            if parent:
                args += ["--parent", parent]
            parent = cli.run(*args, scoped=True)["id"]
            emit("turn", "", custom_id=session["custom_id"], index=i, speaker=speaker)
        if done < len(session["turns"]):
            note(f"{len(session['turns']) - done} message(s) appended; waiting for MemoryLake to extract the facts")
            wait_for_memory(cli, conv["id"])


def show_memory(cli: CLI, projects: dict[str, str], hires: dict[str, str]) -> None:
    pinned = set(pinned_texts())
    scopes = [("Fernhill people policies (project)", "projects", projects["policies"])]
    scopes += [(f"{HIRES[k]['display']} (new-hire actor)", "actors", a) for k, a in hires.items()]
    scopes += [("Fernhill onboarding chats (project — extracted from the chat)", "projects", projects["chats"])]
    lines, payload = [], []
    for title, scope, sid in scopes:
        facts = sorted(list_facts(cli, scope, sid), key=lambda f: f.get("created_at") or "")
        lines.append(f"\n  {title}: {len(facts)} fact(s)")
        if scope == "projects" and sid == projects["policies"]:
            exact = sum(f.get("fact") in pinned for f in facts)
            lines.append(f"   📌 {exact} of {len(pinned)} pinned versions and flows, stored verbatim")
        else:
            lines += [f"   - {f.get('fact')}" for f in facts]
        payload.append(dict(title=title, scope=scope, facts=[dict(fact=f.get("fact"), pinned=f.get("fact") in pinned)
                                                             for f in facts]))
    emit("text", "\n".join(lines))
    emit("memory", "", scopes=payload)


# --------------------------------------------------------------------------- step 6: the current answer

def today() -> str:
    return date.today().isoformat()


def search(cli: CLI, query: str, projects: list[str], actors: list[str], top_k: int = 6,
           types: str | None = None) -> dict:
    args = ["search", query, "--projects", ",".join(projects), "--actors", ",".join(actors), "--top-k", str(top_k)]
    if types:
        args += ["--types", types]
    return cli.run(*args, scoped=True)


def answer(cli: CLI, projects: dict[str, str], hire: str, hire_id: str, question: dict, known: dict,
           about: dict[str, str], doc_names: set[str], as_of: str | None = None) -> dict:
    """One question from a new hire: retrieve, then judge every version and document that came back."""
    as_of = as_of or today()
    res = search(cli, question["q"], [projects["policies"]], [hire_id])
    facts, docs = res.get("facts") or [], res.get("documents") or []
    retrieved = [dict(s, id=f.get("id")) for f in facts if (s := stamp(f.get("fact"))) and s["policy"] == question["policy"]]
    mine = [dict(id=f.get("id"), fact=f.get("fact")) for f in facts
            if f.get("id") in about and re.search(question["guard"], f.get("fact") or "", re.I)]
    verdict = resolve(retrieved, as_of)
    allv = resolve(known.get(question["policy"], []), as_of)
    cur = verdict["current"]
    rows = []
    if cur:
        rows.append(dict(status="current", **cur, source_ok=cur["source"] in doc_names))
    # "superseded by" comes from the full history, not just from what this search happened to return.
    by = {s["v"]: s["by"] for s in allv["superseded"]}
    rows += [dict(status="stale", **dict(s, by=by.get(s["v"]) or s["by"])) for s in verdict["superseded"]]
    rows += [dict(status="scheduled", **s) for s in verdict["scheduled"]]
    # A document is only as current as the policy versions that cite it.
    cites = {}
    for s in known.get(question["policy"], []):
        cites.setdefault(s["source"], []).append(s)
    doc_rows = []
    for d in docs:
        name = d.get("document_name") or d.get("file_name") or d.get("name") or ""
        if name not in cites:
            continue
        vs = cites[name]
        live = allv["current"] and any(v["v"] == allv["current"]["v"] for v in vs)
        status = ("current" if live else "later" if all(v["signed"] > as_of for v in vs)
                  else "scheduled" if all(v["effective"] > as_of for v in vs) else "stale")
        doc_rows.append(dict(name=name, versions=[v["v"] for v in vs], status=status))
    left_out = len(facts) + len(docs) - len(retrieved) - len(mine) - len(doc_rows)
    later = [s for s in retrieved if s["signed"] > as_of]  # did not exist yet on `as_of`

    title = next(p["title"] for p in load_policies()["policies"] if p["id"] == question["policy"])
    lines = [f"\n  Q: {question['q']}   (asked by {HIRES[hire]['display']}, answered as of {as_of})",
             f"     retrieved {len(retrieved)} version(s) of {question['policy']} {title}"
             + (f" and {len(doc_rows)} source document(s)" if doc_rows else "")
             + (f" ({len(later)} signed off after {as_of}, not shown)" if later else "")]
    for r in rows:
        head = {"current": "✓ CURRENT  ", "stale": "✗ STALE    ", "scheduled": "… SCHEDULED"}[r["status"]]
        extra = (f"signed off {r['signed']} by {r['signer']}" if r["status"] == "current"
                 else f"superseded by v{r['by']['v']} on {r['by']['effective']}" if r["status"] == "stale" and r.get("by")
                 else f"signed off {r['signed']}, not in force yet" if r["status"] == "scheduled" else "")
        lines.append(f"     {head} v{r['v']} · effective {r['effective']} · {extra}")
        lines.append(f"                 {r['body']}")
        if r["status"] == "current":
            lines.append(f"                 source: {r['source']} {r['section']}"
                         + ("  (✓ in the policies project)" if r["source_ok"] else "  (✗ not in the project)"))
    for d in doc_rows:
        mark = {"current": "✓ SOURCE   ", "stale": "✗ STALE DOC", "scheduled": "… SOURCE   ", "later": "· LATER DOC"}[d["status"]]
        why = ("cites the version in force" if d["status"] == "current"
               else f"only cites v{', v'.join(map(str, d['versions']))}, no longer in force" if d["status"] == "stale"
               else f"issued after {as_of}" if d["status"] == "later"
               else "cites a version not yet in force")
        lines.append(f"     {mark} {d['name']} — {why}")
    if mine:
        lines.append(f"     about {HIRES[hire]['display'].split()[0]}:")
        lines += [f"       - {m['fact']}" for m in mine]
    if not cur:
        lines.append("     ✗ no version in force was retrieved")
    elif allv["current"] and allv["current"]["v"] != cur["v"]:
        lines.append(f"     ⚠ the newest version in the project is v{allv['current']['v']}; it was not in the top hits")
    if left_out > 0:
        lines.append(f"     ({left_out} other hit(s) not about {title.lower()}, left out)")
    if cur:
        lines.append(f"  → {cur['body']}  [{question['policy']} v{cur['v']}, effective {cur['effective']}]")
    emit("text", "\n".join(lines))
    result = dict(question=question["q"], policy=question["policy"], title=title, hire=hire, as_of=as_of,
                  rows=rows, docs=doc_rows, about=mine, left_out=max(left_out, 0), later=len(later),
                  answer=cur and dict(body=cur["body"], v=cur["v"], effective=cur["effective"], signer=cur["signer"],
                                      signed=cur["signed"], source=f"{cur['source']} {cur['section']}"))
    emit("answer", "", **result)
    return result


def answers_step(cli: CLI, projects: dict[str, str], hires: dict[str, str], hire: str = "priya-nair",
                 questions=QUESTIONS) -> list[dict]:
    known = catalog(cli, projects["policies"], echo=False)
    about = {f["id"]: f.get("fact") for f in list_facts(cli, "actors", hires[hire], echo=False)}
    doc_names = {d.get("name") for d in list_documents(cli, projects["policies"], echo=False)}
    out = [answer(cli, projects, hire, hires[hire], q, known, about, doc_names) for q in questions]
    stale = sum(1 for a in out for r in a["rows"] + a["docs"] if r["status"] == "stale")
    emit("text", f"\n  ✓ {sum(1 for a in out if a['answer'])} of {len(out)} questions answered with the version in force; "
                 f"{stale} stale version(s) or document(s) came back from search and were marked, not used")
    return out


# --------------------------------------------------------------------------- step 7: audit

def audit(cli: CLI, projects: dict[str, str], dates: list[str], show: bool = True) -> list[dict]:
    known = catalog(cli, projects["policies"], echo=show)
    titles = {p["id"]: p["title"] for p in load_policies()["policies"]}
    tables = []
    for d in dates:
        rows = []
        for pid in titles:
            r = resolve(known.get(pid, []), d)
            cur = r["current"]
            rows.append(dict(policy=pid, title=titles[pid], v=cur and cur["v"], effective=cur and cur["effective"],
                             body=cur and cur["body"], signed=cur and cur["signed"], signer=cur and cur["signer"],
                             source=cur and f"{cur['source']} {cur['section']}",
                             scheduled=[dict(v=s["v"], effective=s["effective"]) for s in r["scheduled"]]))
        lines = [f"\n  In force on {d}:"]
        for r in rows:
            nxt = "; ".join(f"v{s['v']} from {s['effective']}" for s in r["scheduled"])
            lines.append(f"   {r['policy']:<10} " + (f"v{r['v']}  (effective {r['effective']}, signed off {r['signed']} by "
                                                   f"{r['signer']}, {r['source']})" if r["v"] else "— none yet"))
            if r["body"]:
                lines.append(f"   {'':<10} {r['body']}")
            if nxt:
                lines.append(f"   {'':<10} … scheduled: {nxt}")
        if show:
            emit("text", "\n".join(lines))
        tables.append(dict(date=d, rows=rows))
    changed = [r1["policy"] for r1, r2 in zip(tables[0]["rows"], tables[-1]["rows"]) if r1["v"] != r2["v"]] if len(tables) > 1 else []
    if not show:
        return tables
    if changed:
        emit("text", f"\n  Changed between {tables[0]['date']} and {tables[-1]['date']}: {', '.join(changed)}")
    emit("audit", "", tables=tables, changed=changed, versions=sum(len(v) for v in known.values()))
    return tables


# --------------------------------------------------------------------------- step 8: day one

def actors_with(cli: CLI, tag: str) -> list[dict]:
    return cli.run("actor", "list", "--tags", tag).get("items") or []


def first_day_step(cli: CLI, projects: dict[str, str], hires: dict[str, str], promote: bool = True) -> list[dict]:
    if promote:
        before = actors_with(cli, "stage:day-1")
        note(f"{len(before)} actor(s) tagged stage:day-1 before today")
        for key, actor_id in hires.items():
            # --tags and --metadata REPLACE what is stored: send the full set every time.
            cli.run("actor", "update", actor_id, "--tags", stage_tags("day-1"), "--metadata", hire_metadata(key, "day-1"))
            emit("stage", "", key=key, id=actor_id, stage="day-1")
    starting = actors_with(cli, "stage:day-1")
    order = list(hires.values())
    mine = sorted((a for a in starting if a.get("id") in order), key=lambda a: order.index(a["id"]))
    note(f"{len(starting)} actor(s) tagged stage:day-1 now: " + ", ".join(a.get("display_name") for a in starting))
    plans = []
    for a in mine:
        key = next(k for k, i in hires.items() if i == a["id"])
        role = (a.get("metadata") or {}).get("role")
        res = search(cli, FIRST_DAY_Q, [projects["policies"]], [a["id"]], top_k=8, types="fact")
        flows = [m.groupdict() for f in res.get("facts") or [] if (m := FLOW.match(f.get("fact") or ""))]
        plan = next((f for f in flows if f["role"] == role and f["stage"] == "day-1"), None)
        others = [f"{f['role']}/{f['stage']}" for f in flows if f is not plan]
        emit("text", f"\n  {a.get('display_name')}  · metadata.role={role} · tags {', '.join(a.get('tags') or [])}\n"
                     f"   Q: {FIRST_DAY_Q}\n"
                     + (f"   → {plan['body']}  [FLOW {role} · day-1]" if plan else "   ✗ no day-1 flow for this role came back")
                     + (f"\n   (also retrieved and left out: {', '.join(others)})" if others else ""))
        plans.append(dict(key=key, display=a.get("display_name"), role=role, tags=a.get("tags") or [],
                          metadata=a.get("metadata") or {}, plan=plan and plan["body"], left_out=others))
    distinct = len({p["plan"] for p in plans if p["plan"]})
    verdict = (f"✓ same question, {distinct} different first-day plans — picked by each hire's metadata.role"
               if distinct == len(plans) and plans else "✗ the plans did not come back per role")
    emit("text", f"\n  {verdict}")
    emit("plans", "", plans=plans, starting=[a.get("display_name") for a in starting], verdict=verdict)
    return plans


# --------------------------------------------------------------------------- step 9: onboarding brief

def render_brief(hire: str, answers: list[dict], plans: list[dict], about: list[str], audit_today: dict) -> str:
    h = HIRES[hire]
    plan = next((p for p in plans if p["key"] == hire), None)
    lines = [f"# Onboarding brief — {h['display']}", "",
             f"_{h['role']} · starts {h['metadata']['start_date']} · {h['metadata']['office']} · manager "
             f"{h['metadata']['manager']}. Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} from "
             f"MemoryLake: policy versions in force on {audit_today['date']}, with the sign-off and source behind each._", "",
             "## Your questions", ""]
    for a in answers:
        lines.append(f"**{a['question']}**")
        if a["answer"]:
            x = a["answer"]
            lines.append(f"{x['body'].rstrip('.')} — {a['policy']} v{x['v']}, effective {x['effective']}, signed off {x['signed']} "
                         f"by {x['signer']} ({x['source']}).")
        stale = [r for r in a["rows"] if r["status"] == "stale"] + [d for d in a["docs"] if d["status"] == "stale"]
        if stale:
            lines.append("Not this: " + "; ".join(f"v{r['v']} ({r['body'].rstrip('.')})" if "body" in r else f"{r['name']} (outdated)"
                                                for r in stale) + ".")
        sched = [r for r in a["rows"] if r["status"] == "scheduled"]
        if sched:
            lines.append("Coming: " + "; ".join(f"v{r['v']} from {r['effective']} — {r['body'].rstrip('.')}" for r in sched) + ".")
        lines.append("")
    lines += ["## Policies in force today", ""]
    lines += [f"- **{r['title']}** ({r['policy']} v{r['v']}): {r['body']}" for r in audit_today["rows"] if r["v"]]
    lines += ["", "## Your first day", "", (plan or {}).get("plan") or "_No plan found._", "",
              "## What the onboarding assistant remembers about you", ""]
    lines += [f"- {f}" for f in about] or ["_Nothing yet._"]
    return "\n".join(lines) + "\n"


def brief_step(cli: CLI, projects: dict[str, str], hires: dict[str, str], answers: list[dict], plans: list[dict],
               hire: str = "priya-nair") -> str:
    about = [f.get("fact") for f in list_facts(cli, "actors", hires[hire])]
    audit_today = audit(cli, projects, [today()], show=False)[0]
    brief = render_brief(hire, answers, plans, about, audit_today)
    OUT.mkdir(exist_ok=True)
    target = OUT / f"onboarding-brief-{hire}.md"
    target.write_text(brief, encoding="utf-8")
    note(f"brief written to {target}")
    emit("brief", "", markdown=brief, hire=hire, filename=target.name)
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
    for key in [*PEOPLE, *HIRES]:
        actor = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted demo actors (what was remembered about the new hires went with them)")
    if delete_library_root(cli):
        note(f"deleted Library folder {LIBRARY_ROOT}/ and everything in it")


# --------------------------------------------------------------------------- the whole pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False, first_step: int = 2) -> str:
    """Steps 2–9. Step 1, connecting, is the caller's job."""
    banner(first_step, TOTAL, "Set up — two new hires, the onboarding assistant, two projects")
    projects = ensure_projects(cli, reset)
    people, hires = ensure_people(cli)

    banner(3, TOTAL, "The January handbook and both policy memos go into the policies project")
    folders = mirror_library(cli)
    import_library(cli, projects, folders)

    banner(4, TOTAL, "Pin every version of every policy — stamped, never overwritten — and the onboarding flows")
    pin_policies(cli, projects)

    banner(5, TOTAL, "Priya's pre-boarding chat becomes memory about her")
    ingest_chat(cli, projects, people, hires)
    show_memory(cli, projects, hires)

    banner(6, TOTAL, "Priya asks — search returns old and new versions; the stamps pick the one in force")
    answers = answers_step(cli, projects, hires)

    banner(7, TOTAL, f"Audit — what was in force on {AUDIT_DATE}, and what is today?")
    audit(cli, projects, [AUDIT_DATE, today()])

    banner(8, TOTAL, "Day one — `actor update` moves both hires on; one question, a plan per role")
    plans = first_day_step(cli, projects, hires)

    banner(9, TOTAL, "Priya's onboarding brief — current answers with their source and sign-off")
    return brief_step(cli, projects, hires, answers, plans)


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "audit", "brief", "facts", "cleanup"],
                    help="run = full demo (default); ask = only Priya's questions; audit = which version was in "
                         "force on a date (see --as-of); brief = questions + first-day plans + the brief; "
                         "facts = list what each scope remembers; cleanup = delete everything the demo created")
    ap.add_argument("--as-of", action="append", metavar="YYYY-MM-DD",
                    help=f"audit: date(s) to report on (repeatable; default {AUDIT_DATE} and today)")
    ap.add_argument("--reset", action="store_true", help="delete and recreate the demo data before ingesting")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    for d in args.as_of or []:
        try:
            date.fromisoformat(d)
        except ValueError:
            ap.error(f"--as-of wants YYYY-MM-DD, got {d!r}")

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
        hires = find_actor_ids(cli, HIRES)
        if args.command == "ask":
            banner(2, 2, "Priya asks — the version in force, stale ones marked")
            answers_step(cli, projects, hires)
        elif args.command == "audit":
            dates = args.as_of or [AUDIT_DATE, today()]
            banner(2, 2, f"Audit — what was in force on {', '.join(dates)}")
            audit(cli, projects, dates)
        elif args.command == "facts":
            banner(2, 2, "What each scope remembers")
            show_memory(cli, projects, hires)
        else:
            banner(2, 4, "Priya asks — the version in force, stale ones marked")
            answers = answers_step(cli, projects, hires)
            banner(3, 4, "First-day plans, per role")
            plans = first_day_step(cli, projects, hires, promote=False)
            banner(4, 4, "Priya's onboarding brief")
            brief_step(cli, projects, hires, answers, plans)
        return

    run_pipeline(cli, reset=args.reset)
    print("\nDone. Re-run `python3 demo.py ask`, `audit --as-of 2026-05-01` or `brief` any time, "
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

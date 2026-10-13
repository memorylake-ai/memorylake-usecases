#!/usr/bin/env python3
"""
Per-tenant memory for a multi-tenant agent platform — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
"Relaybench" hosts one support agent, Relay, for three customer companies:
Harbor Dental, Kestrel Freight and Nimbus Yoga.  Each tenant gets its own
MemoryLake workspace: its own project, its own documents, its own conversations,
its own memories.  The demo asks the same question in all three and counts every
tenant's marker words in every hit (a 3 x 3 leak matrix), shows one person who is
a customer of two tenants and has two separate memories under one actor id, has
tenant A name tenant B's project (refused), and finally offboards a tenant with
`workspace delete` — then lists, honestly, what that command does not delete.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI (v20261013 or later, for
`workspace delete`) on PATH, and a MemoryLake API key.
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
DOCS = DATA / "docs"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-tnt"  # memorylake-usecases / tenant memory


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


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


def say(msg: str) -> None:
    emit("text", msg)


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

    def server_message(self) -> str:
        """The server's one-line reason (`… [CODE]`), or the CLI's own refusal."""
        for line in self.stderr.splitlines():
            line = line.strip()
            if re.search(r"\[[A-Z_]+\]$", line):
                return line
        lines = [l.strip() for l in self.stderr.splitlines() if l.strip()]
        return re.sub(r"^Error:\s*", "", lines[0]) if lines else str(self)


class CLI:
    """Runs `memorylake …`, echoes the command, parses the JSON reply, retries read-only calls."""

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable",
                 "http 502", "http 503", "http 504", "internal_error")
    # Status codes are matched with their "HTTP " prefix: a bare "502" also matches ids such as ws-…13502644….

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False):
        self.binary, self.env, self.show_json = binary, env, show_json
        self.secret: str | None = None
        self.base_url = DEFAULT_BASE_URL

    def _echo(self, args: list[str]) -> None:
        shown = [a.replace(self.secret, "sk-…") for a in args] if self.secret else list(args)
        emit("cmd", shlex.join(["memorylake", *shown]))

    def raw(self, *args: str, ws: str | None = None, echo: bool = True) -> tuple[int, str, str]:
        argv = [str(a) for a in args] + (["--workspace", ws] if ws else [])
        if echo:
            self._echo(argv)
        proc = subprocess.run([self.binary, *argv], env=self.env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def run(self, *args: str, ws: str | None = None, echo: bool = True):
        retryable = any(str(a) in self.READ_ONLY for a in args[:3]) or tuple(map(str, args[:2])) == ("fact", "update")
        attempt = 0
        while True:
            rc, out, err = self.raw(*args, ws=ws, echo=echo and attempt == 0)
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

    def try_run(self, *args: str, ws: str | None = None, echo: bool = True, missing=("404", "not found")):
        try:
            return self.run(*args, ws=ws, echo=echo)
        except CLIError as e:
            if e.has(*missing):
                return None
            raise


def create(cli: CLI, args: list, refind, ws: str | None = None):
    """A create call that is not idempotent: on a dropped connection, look for what it may have created before retrying."""
    for attempt in range(5):
        try:
            return cli.run(*args, ws=ws, echo=attempt == 0)
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            note(f"network hiccup; checking whether it went through (retry {attempt + 1}/4)")
            time.sleep(2 * (attempt + 1))
            found = refind()
            if found:
                return found


def pages(cli: CLI, args: list[str], size: int, ws: str | None = None, echo: bool = True) -> list[dict]:
    """All pages of a list command; stops only when the token is null."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, ws=ws, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


# --------------------------------------------------------------------------- step 1: connect

def find_binary() -> str:
    binary = os.environ.get("MEMORYLAKE_BIN") or shutil.which("memorylake")
    if not binary:
        die("the `memorylake` CLI is not on PATH.\n"
            "  install: curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh\n"
            "  or set MEMORYLAKE_BIN=/path/to/memorylake")
    return binary


def connect(show_json: bool = False, api_key: str | None = None, base_url: str | None = None) -> CLI:
    binary = find_binary()
    env = dict(os.environ)
    env.pop("MEMORYLAKE_API_KEY", None)
    api_key = (api_key or os.environ.get("MEMORYLAKE_API_KEY", "")).strip()
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
    rc, out, _ = cli.raw("workspace", "delete", "--help", echo=False)
    if rc != 0:
        die("this demo needs `memorylake workspace delete` (CLI v20261013 or later); reinstall the CLI:\n"
            "  curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh")
    team = cli.run("team", "get")
    cli.team = team  # type: ignore[attr-defined]
    note(f"connected to team “{team.get('name')}” as {team.get('caller_role')}")
    emit("connected", "", team=team.get("name"), role=team.get("caller_role"))
    # The demo never touches your existing workspaces: it creates one per tenant and works only inside those.
    return cli


# --------------------------------------------------------------------------- data

def load_story() -> dict:
    return json.loads((DATA / "tenants.json").read_text(encoding="utf-8"))


def ws_name(t: dict) -> str:
    # `workspace delete --confirm` must repeat this exactly.
    return f"{t['name']} (Relaybench demo)"


def folder_name(t: dict) -> str:
    return cid(t["key"])


def tenants_of(story: dict, person: str) -> list[dict]:
    return [t for t in story["tenants"] if person in t["people"]]


def markers_in(text: str, tenant: dict) -> list[str]:
    low = text.lower()
    return [m for m in tenant["markers"] if m in low]


# --------------------------------------------------------------------------- finding things again

def find_workspace(cli: CLI, t: dict, echo: bool = False):
    return cli.try_run("workspace", "get", cid(t["key"]), "--by-custom-id", echo=echo)


def demo_workspaces(cli: CLI, echo: bool = True) -> list[dict]:
    return [w for w in pages(cli, ["workspace", "list"], 50, echo=echo) if (w.get("custom_id") or "").startswith(PREFIX + "-")]


def find_project(cli: CLI, t: dict, ws: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(t["key"], "kb"), "--by-custom-id", ws=ws, echo=echo)


def boundaries_in(cli: CLI, ws: str, echo: bool = False) -> list[dict]:
    return pages(cli, ["boundary", "list"], 100, ws=ws, echo=echo)


def projects_in(cli: CLI, ws: str, echo: bool = False) -> list[dict]:
    return pages(cli, ["proj", "list"], 100, ws=ws, echo=echo)


def find_folder(cli: CLI, name: str, echo: bool = False):
    return next((i for i in pages(cli, ["lib", "list", "MY_SPACE"], 50, echo=echo)
                 if i.get("type") in ("directory", "folder") and i["name"] == name), None)


def files_in(cli: CLI, folder: str, echo: bool = False) -> list[dict]:
    return [i for i in pages(cli, ["lib", "list", folder], 50, echo=echo) if i.get("type") == "file"]


def find_actor(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id", echo=echo)


# --------------------------------------------------------------------------- step 2: provision the tenants

def ensure_actor(cli: CLI, key: str, display: str, description: str) -> str:
    actor = find_actor(cli, key, echo=True)
    if actor is None:
        actor = create(cli, ["actor", "create", "--custom-id", cid(key), "--display-name", display,
                             "--description", description, "--tags", "demo"], lambda: find_actor(cli, key))
        note(f"created actor {display} → {actor['id']}")
    return actor["id"]


def bind(cli: CLI, actor_id: str, ws: str) -> None:
    try:
        cli.run("actor", "bind", "--actor", actor_id, ws=ws)
    except CLIError as e:
        if not e.has("409", "already"):
            raise


def provision(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created first")
        cleanup(cli, story, quiet=True)
    for ws_id in (json.loads(OFFBOARDED.read_text()) if OFFBOARDED.exists() else []):
        # A run that stopped half-way through offboarding leaves the tenant's boundary holding its custom id.
        if clear_workspace(cli, ws_id, quiet=True):
            note(f"removed what an earlier offboarding left under {ws_id}")
    ids: dict = {"tenants": {}, "actors": {}}
    ids["actors"][story["bot"]["key"]] = ensure_actor(cli, story["bot"]["key"], story["bot"]["display"], story["bot"]["description"])
    for p in story["people"]:
        ids["actors"][p["key"]] = ensure_actor(cli, p["key"], p["display"], p["description"])
    say("\n  Actors are team-wide identities. Binding one to a workspace lets it hold memory there —")
    say("  a separate memory per workspace.")
    for t in story["tenants"]:
        say(f"\n  Tenant {t['name']} — {t['about']}")
        ws = find_workspace(cli, t, echo=True)
        if ws is None:
            ws = create(cli, ["workspace", "create", "--name", ws_name(t), "--custom-id", cid(t["key"]),
                              "--description", f"Relaybench tenant: {t['name']}. Demo data."],
                        lambda: find_workspace(cli, t))
            note(f"created workspace “{ws['name']}” → {ws['id']}")
        else:
            note(f"workspace “{ws['name']}” already exists → {ws['id']}")
        w = ws["id"]
        for key in [story["bot"]["key"], *t["people"].keys()]:
            bind(cli, ids["actors"][key], w)
        proj = find_project(cli, t, w)
        if proj is None:
            proj = create(cli, ["proj", "create", "--name", f"{t['name']} support KB", "--custom-id", cid(t["key"], "kb"),
                                "--description", "What Relay may know when it answers for this tenant. Demo data."],
                          lambda: find_project(cli, t, w), ws=w)
            note(f"created project “{proj['name']}” → {proj['id']}")
        else:
            note(f"project “{proj['name']}” already exists → {proj['id']}")
        folder = find_folder(cli, folder_name(t), echo=True)
        if folder is None:
            folder = create(cli, ["lib", "mkdir", folder_name(t), "--parent", "MY_SPACE", "--on-conflict", "deny"],
                            lambda: find_folder(cli, folder_name(t)))
        have = {f["name"]: f for f in files_in(cli, folder["item_id"])}
        items = []
        for path in sorted((DOCS / t["key"]).glob("*.md")):
            f = have.get(path.name)
            if f is None:
                f = cli.run("lib", "upload", str(path.relative_to(HERE)), "--parent", folder["item_id"], "--on-conflict", "deny")
            items.append(f["item_id"])
        docs = pages(cli, ["proj", "doc", "list", "--project", proj["id"]], 100, ws=w)
        if len(docs) < len(items):
            res = cli.run("proj", "doc", "import", "--project", proj["id"], "--wait", *items, ws=w) or {}
            bad = [d for d in res.get("details") or [] if d.get("result") not in ("success", "duplicate")]
            if bad:
                die(f"{len(bad)} document(s) failed to import into {t['name']}")
            note(f"imported {len(items)} policy documents into {t['name']}")
        else:
            note(f"{len(docs)} policy documents already imported")
        # The tenant's boundary: the saved scope (its project) its tools and the Memory Router connect with.
        bnd = next((b for b in boundaries_in(cli, w) if b.get("custom_id") == cid(t["key"], "relay")), None)
        if bnd is None:
            bnd = create(cli, ["boundary", "create", "--name", f"Relay for {t['name']}", "--custom-id", cid(t["key"], "relay"),
                               "--projects", proj["id"]],
                         lambda: next((b for b in boundaries_in(cli, w) if b.get("custom_id") == cid(t["key"], "relay")), None), ws=w)
            note(f"created boundary “{bnd['name']}” → {bnd['id']}")
        ids["tenants"][t["key"]] = dict(ws=w, ws_name=ws["name"], project=proj["id"], folder=folder["item_id"], boundary=bnd["id"])
        emit("tenant", "", key=t["key"], name=t["name"], ws=w, project=proj["id"], folder=folder_name(t),
             boundary=bnd["id"], people=list(t["people"].keys()))
    return ids


# --------------------------------------------------------------------------- step 3: write memory, per tenant

def list_facts(cli: CLI, ws: str, projects: list[str] = (), actors: list[str] = (), echo: bool = False) -> list[dict]:
    args = ["fact", "list"]
    if projects:
        args += ["--projects", ",".join(projects)]
    if actors:
        args += ["--actors", ",".join(actors)]
    return pages(cli, args, 200, ws=ws, echo=echo)


def pinned(text: str, facts: list[dict]) -> bool:
    """Was this text pinned already?  The extractor may rewrite a pinned fact in place (adding "(as of …)"),
    so compare on the text without its final full stop, as a prefix."""
    stem = text.rstrip(". ")
    return any((f.get("fact") or "").startswith(stem) for f in facts)


def write_memory(cli: CLI, story: dict, ids: dict) -> None:
    bot = ids["actors"][story["bot"]["key"]]
    conv_ids: list[tuple[str, str]] = []
    for t in story["tenants"]:
        tid = ids["tenants"][t["key"]]
        w, proj = tid["ws"], tid["project"]
        say(f"\n  {t['name']}: two tenant rules pinned to the tenant's project")
        have = list_facts(cli, w, projects=[proj])
        missing = [x for x in t["facts"] if not pinned(x, have)]
        if missing:
            cli.run("fact", "add", "--project", proj, *missing, ws=w)
        for pkey, p in t["people"].items():
            actor = ids["actors"][pkey]
            s = p["session"]
            ccid = cid(t["key"], pkey, "session")
            conv = cli.try_run("conv", "get", ccid, "--by-custom-id", ws=w)
            done = 0
            if conv is None:
                conv = create(cli, ["conv", "create", "--custom-id", ccid, "--project", proj, "--actors", f"{actor},{bot}",
                                    "--kind", "DIRECT", "--name", f"{t['name']} — {s['title']}",
                                    "--metadata", f"tenant={t['key']}"],
                              lambda: cli.try_run("conv", "get", ccid, "--by-custom-id", ws=w, echo=False), ws=w)
            else:
                done = len(pages(cli, ["conv", "msg", "list", conv["id"]], 50, echo=False))
            if done < len(s["turns"]):
                # Pin the person's preference only when this run writes their session: the extractor rewrites a
                # pinned fact in place (even mid-sentence), so it cannot be found again by its text on a rerun.
                cli.run("fact", "add", "--actor", actor, p["pin"], ws=w)
            start = datetime.fromisoformat(s["date"].replace("Z", "+00:00"))
            parent = conv.get("current_message_id")
            for i, (who, text) in enumerate(s["turns"], start=1):
                if i <= done:
                    continue
                stamp = (start + timedelta(seconds=45 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
                a = ["conv", "msg", "append", conv["id"], "--actor", actor if who == "user" else bot,
                     "--custom-id", f"turn-{i:02d}", "--timestamp", stamp, "--text", text]
                if parent:
                    a += ["--parent", parent]
                msg = cli.run(*a, ws=w, echo=i == done + 1)
                parent = msg["id"]
            if done < len(s["turns"]):
                note(f"{len(s['turns']) - done} message(s) appended to “{s['title']}” ({p['pin'].split(' wants')[0]} ↔ Relay)")
            else:
                note(f"session “{s['title']}” already complete")
            conv_ids.append((w, conv["id"]))
            emit("session", "", tenant=t["key"], person=pkey, title=s["title"], id=conv["id"], turns=len(s["turns"]))
    wait_for_memory(cli, conv_ids)
    say("\n  Memory per tenant (the tenant's project + Relay + its customers, inside its own workspace):")
    counts = {}
    for t in story["tenants"]:
        w, projects, actors = tenant_scope(story, ids, t)
        counts[t["key"]] = len(list_facts(cli, w, projects=projects, actors=actors, echo=True))
        say(f"    {t['name']}: {counts[t['key']]} fact(s)")
    emit("memory", "", counts=counts)


def wait_for_memory(cli: CLI, convs: list[tuple[str, str]], timeout: int = 600) -> None:
    pending = set(convs)
    t0 = last = time.time()
    first = True
    while pending and time.time() - t0 < timeout:
        for w, c in sorted(pending):
            try:
                st = cli.run("conv", "cook-status", c, ws=w, echo=first)
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                continue
            if st.get("cook_finished"):
                pending.discard((w, c))
        first = False
        if pending:
            if time.time() - last >= 30:
                emit("progress", f"  … {len(pending)} conversation(s) still being turned into memory ({int(time.time() - t0)}s)")
                last = time.time()
            time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} conversation(s) still processing server-side")
    else:
        note(f"memory ready in all three tenants after {int(time.time() - t0)}s")


# --------------------------------------------------------------------------- step 4: the leak matrix

def tenant_scope(story: dict, ids: dict, t: dict) -> tuple[str, list[str], list[str]]:
    tid = ids["tenants"][t["key"]]
    actors = [ids["actors"][story["bot"]["key"]]] + [ids["actors"][k] for k in t["people"]]
    return tid["ws"], [tid["project"]], actors


def hit_texts(res: dict) -> list[tuple[str, str]]:
    """(kind, text) for every fact and every document hit, document paragraphs included."""
    out = [("fact", f.get("fact") or "") for f in res.get("facts") or []]
    for d in res.get("documents") or []:
        chunks = [c.get("text") or "" for it in d.get("items") or [] for c in ((it.get("highlight") or {}).get("chunks") or [])]
        out.append(("doc", " ".join([d.get("name") or d.get("document_name") or "", d.get("document_summary") or "", *chunks])))
    return out


def ask_tenant(cli: CLI, story: dict, ids: dict, t: dict, question: str, echo: bool = True) -> list[tuple[str, str]]:
    w, projects, actors = tenant_scope(story, ids, t)
    base = ["search", question, "--projects", ",".join(projects), "--actors", ",".join(actors)]
    facts = cli.run(*base, "--types", "fact", "--top-k", "10", ws=w, echo=echo) or {}
    docs = cli.run(*base, "--types", "document", "--top-k", "5", ws=w, echo=echo) or {}
    return hit_texts(facts) + hit_texts(docs)


def leak_matrix(cli: CLI, story: dict, ids: dict) -> dict:
    q = story["question"]
    say(f"\n  The same question, asked inside each tenant's workspace:\n    “{q}”")
    say("  Every hit (facts and document paragraphs) is scanned for every tenant's marker words.")
    tenants = story["tenants"]
    matrix: dict[str, dict[str, int]] = {}
    samples: dict[str, list[str]] = {}
    for t in tenants:
        hits = ask_tenant(cli, story, ids, t, q)
        matrix[t["key"]] = {o["key"]: sum(1 for _, text in hits if markers_in(text, o)) for o in tenants}
        samples[t["key"]] = [text for kind, text in hits if kind == "fact"][:3]
        note(f"{t['name']}: {len(hits)} hit(s)")
    width = max(len(t["name"]) for t in tenants)
    lines = ["", "  hits containing markers of →".ljust(width + 17) + "".join(o["name"].rjust(18) for o in tenants)]
    for t in tenants:
        row = f"  asked inside {t['name']}".ljust(width + 17)
        for o in tenants:
            n = matrix[t["key"]][o["key"]]
            cell = f"{n}" + (" ✓" if (o is t and n > 0) or (o is not t and n == 0) else " ✗")
            row += cell.rjust(18)
        lines.append(row)
    say("\n".join(lines))
    leaks = sum(matrix[t["key"]][o["key"]] for t in tenants for o in tenants if o is not t)
    own = all(matrix[t["key"]][t["key"]] > 0 for t in tenants)
    say(f"\n  cross-tenant hits: {leaks}   ·   every tenant found its own answer: {'yes' if own else 'NO'}")
    for t in tenants:
        say(f"\n  {t['name']} — top facts:")
        for s in samples[t["key"]]:
            say(f"    - {s}")
    emit("matrix", "", tenants=[dict(key=t["key"], name=t["name"]) for t in tenants], matrix=matrix,
         leaks=leaks, own=own, samples=samples, question=q)
    return dict(matrix=matrix, leaks=leaks, own=own)


def tenant_ids(cli: CLI, story: dict, key: str) -> dict:
    """Look a tenant's workspace, project and actors up again (for `matrix` and the web app's Ask)."""
    t = next(x for x in story["tenants"] if x["key"] == key)
    ws = find_workspace(cli, t)
    if ws is None:
        die(f"{t['name']} has no workspace (offboarded, or the demo has not run yet)")
    proj = find_project(cli, t, ws["id"])
    if proj is None:
        die(f"{t['name']}'s project is missing; run the demo again")
    ids: dict = {"tenants": {key: dict(ws=ws["id"], ws_name=ws["name"], project=proj["id"], folder="")}, "actors": {}}
    for k in [story["bot"]["key"], *t["people"].keys()]:
        a = find_actor(cli, k)
        if a:
            ids["actors"][k] = a["id"]
    return dict(t=t, ids=ids)


def ask_free(cli: CLI, key: str, question: str) -> dict:
    """One tenant, any question: every hit with the tenant markers it contains."""
    story = load_story()
    found = tenant_ids(cli, story, key)
    t, ids = found["t"], found["ids"]
    w, projects = ids["tenants"][key]["ws"], [ids["tenants"][key]["project"]]
    actors = list(ids["actors"].values())
    base = ["search", question, "--projects", ",".join(projects), "--actors", ",".join(actors)]
    hits = hit_texts(cli.run(*base, "--types", "fact", "--top-k", "8", ws=w) or {}) + \
        hit_texts(cli.run(*base, "--types", "document", "--top-k", "3", ws=w) or {})
    rows = [dict(kind=kind, text=text[:400], markers={o["key"]: markers_in(text, o) for o in story["tenants"]}) for kind, text in hits]
    foreign = sum(1 for r in rows for k, v in r["markers"].items() if k != key and v)
    return dict(tenant=t["name"], key=key, question=question, rows=rows, foreign=foreign)


# --------------------------------------------------------------------------- step 5: one person, two tenants

def same_person(cli: CLI, story: dict, ids: dict) -> dict:
    person = next(p for p in story["people"] if len(tenants_of(story, p["key"])) > 1)
    actor = ids["actors"][person["key"]]
    say(f"\n  {person['display']} is a customer of two tenants. One actor id: {actor}")
    views = {}
    for t in tenants_of(story, person["key"]):
        w = ids["tenants"][t["key"]]["ws"]
        facts = list_facts(cli, w, actors=[actor], echo=True)
        views[t["key"]] = facts
        say(f"\n  inside {t['name']}'s workspace — {len(facts)} fact(s) on {person['display']}:")
        for f in facts:
            say(f"    - {f['fact']}")
    keys = list(views)
    a, b = (set(f["id"] for f in views[k]) for k in keys)
    foreign = {k: [f["fact"] for f in views[k] for o in story["tenants"] if o["key"] != k and markers_in(f["fact"], o)] for k in keys}
    say(f"\n  shared fact ids between the two views: {len(a & b)}   ·   "
        f"facts mentioning the other tenant: {sum(len(v) for v in foreign.values())}")
    emit("person", "", person=person["display"], actor=actor,
         views={k: [f["fact"] for f in v] for k, v in views.items()}, shared=len(a & b),
         foreign=sum(len(v) for v in foreign.values()))
    return dict(shared=len(a & b), foreign=foreign)


# --------------------------------------------------------------------------- step 6: default-deny

def attempt(cli: CLI, label: str, *args: str, ws: str) -> dict:
    try:
        res = cli.run(*args, ws=ws)
        n = len((res or {}).get("facts") or (res or {}).get("items") or [])
        note(f"{label}: ALLOWED ({n} result(s)) ✗")
        return dict(label=label, refused=False, message=f"{n} result(s)")
    except CLIError as e:
        msg = e.server_message()
        note(f"{label}: refused ✓  {msg}")
        return dict(label=label, refused=True, message=msg)


def default_deny(cli: CLI, story: dict, ids: dict) -> list[dict]:
    a, b = story["tenants"][0], story["tenants"][1]
    wa, pa = ids["tenants"][a["key"]]["ws"], ids["tenants"][a["key"]]["project"]
    pb = ids["tenants"][b["key"]]["project"]
    outsider = next(p for p in story["people"] if a["key"] not in [t["key"] for t in tenants_of(story, p["key"])])
    say(f"\n  Code running for {a['name']} names {b['name']}'s project id — a bug, or a forged request:")
    results = [
        attempt(cli, f"search {b['name']}'s project", "search", "refund policy", "--projects", pb, ws=wa),
        attempt(cli, f"search {a['name']} + {b['name']} together", "search", "refund policy", "--projects", f"{pa},{pb}", ws=wa),
        attempt(cli, f"list {b['name']}'s facts", "fact", "list", "--projects", pb, ws=wa),
    ]
    say(f"\n  …and names a person who is not a customer of {a['name']} ({outsider['display']}):")
    results.append(attempt(cli, f"search {outsider['display']}'s memory", "search", "contact preference",
                           "--actors", ids["actors"][outsider["key"]], ws=wa))
    refused = sum(r["refused"] for r in results)
    say(f"\n  refused: {refused}/{len(results)} — a mixed request is refused whole, it never returns the allowed half")
    emit("deny", "", results=results, refused=refused, total=len(results))
    return results


# --------------------------------------------------------------------------- step 7: offboard a tenant

def snapshot(cli: CLI, story: dict, ids: dict, t: dict) -> set[str]:
    w, projects, actors = tenant_scope(story, ids, t)
    return {f["id"] for f in list_facts(cli, w, projects=projects, actors=actors)}


OFFBOARDED = STATE_DIR / "offboarded.json"


def remember_offboarded(ws_id: str) -> None:
    """Keep the id of every workspace the demo deletes: what the delete leaves behind is reachable only through it."""
    STATE_DIR.mkdir(exist_ok=True)
    seen = json.loads(OFFBOARDED.read_text()) if OFFBOARDED.exists() else []
    if ws_id not in seen:
        OFFBOARDED.write_text(json.dumps(seen + [ws_id]))


def remains(cli: CLI, ws_id: str, echo: bool = True) -> dict:
    """What still answers through a workspace id after `workspace delete`."""
    try:
        projects = projects_in(cli, ws_id, echo=echo)
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        projects = []
    try:
        bnds = boundaries_in(cli, ws_id, echo=echo)
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        bnds = []
    return dict(projects=projects, boundaries=bnds)


def offboard(cli: CLI, story: dict, ids: dict) -> dict:
    gone = next(t for t in story["tenants"] if t["key"] == story["offboard"])
    stay = [t for t in story["tenants"] if t is not gone]
    tid = ids["tenants"][gone["key"]]
    say(f"\n  {gone['name']} cancels its Relaybench contract. Everything Relay knew for them must go.")
    before = {t["key"]: snapshot(cli, story, ids, t) for t in story["tenants"]}
    docs = pages(cli, ["proj", "doc", "list", "--project", tid["project"]], 100, ws=tid["ws"], echo=False)
    say(f"  {gone['name']} holds {len(before[gone['key']])} fact(s), {len(docs)} document(s) and a boundary; "
        + ", ".join(f"{t['name']} {len(before[t['key']])}" for t in stay) + " fact(s)")

    say("\n  `workspace delete` asks for the workspace's name, typed out, instead of a prompt:")
    try:
        cli.run("workspace", "delete", tid["ws"], "--confirm", gone["name"])
        die("the CLI deleted a workspace with the wrong name")
    except CLIError as e:
        refusal = e.server_message()
        note(f"refused ✓  {refusal}")
    remember_offboarded(tid["ws"])
    t0 = time.time()
    cli.run("workspace", "delete", tid["ws"], "--confirm", tid["ws_name"])
    took = round(time.time() - t0, 1)
    note(f"deleted in {took}s — the help says “its projects, documents and memories go with it”")

    checks = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append(dict(label=label, ok=ok, detail=detail))
        note(f"{label}: {'✓' if ok else '✗'} {detail}".rstrip())

    say("\n  Memory:")
    try:
        cli.run("workspace", "get", tid["ws"])
        check("the workspace is gone", False, "workspace get still answers")
    except CLIError as e:
        check("the workspace is gone", e.has("404", "not found"), e.server_message())
    for label, args in ((f"{gone['name']}'s facts are unreachable", ["fact", "list", "--projects", tid["project"]]),
                        (f"{gone['name']}'s search is unreachable", ["search", "refund policy", "--projects", tid["project"]])):
        try:
            cli.run(*args, ws=tid["ws"])
            check(label, False, "it still answers")
        except CLIError as e:
            check(label, e.has("404", "not found"), e.server_message())
    for t in stay:
        after = snapshot(cli, story, ids, t)
        check(f"{t['name']} untouched", after == before[t["key"]], f"{len(after)} fact(s), same ids as before")

    say(f"\n  Still there — reachable through the deleted workspace's id:")
    left = remains(cli, tid["ws"])
    leftovers = []
    for p in left["projects"]:
        pdocs = pages(cli, ["proj", "doc", "list", "--project", p["id"]], 100, ws=tid["ws"])
        first = ""
        if pdocs:
            rc, out, _ = cli.raw("proj", "doc", "download", "--project", p["id"], pdocs[0]["id"], "--output", "-", ws=tid["ws"])
            first = next((l.strip() for l in out.splitlines() if l.strip()), "") if rc == 0 else ""
        what = (f"project “{p['name']}” with {len(pdocs)} document(s)"
                + (f"; `proj doc download` still returns {pdocs[0]['name']}: “{first[:70]}”" if first else ""))
        leftovers.append(dict(kind="project", what=what))
        note(f"still there: {what}")
    for b in left["boundaries"]:
        what = f"boundary “{b['name']}” ({b['id']}), and its custom id stays taken"
        leftovers.append(dict(kind="boundary", what=what))
        note(f"still there: {what}")
    say(f"\n  Still there — team-level, outside any workspace:")
    folder = find_folder(cli, folder_name(gone), echo=True)
    if folder:
        names = [f["name"] for f in files_in(cli, folder["item_id"])]
        leftovers.append(dict(kind="library", what=f"Library folder {folder_name(gone)}/ with {len(names)} file(s): {', '.join(names)}"))
        note(f"still there: Library folder {folder_name(gone)}/ — {', '.join(names)}")
    only_here = [p for p in story["people"] if [x["key"] for x in tenants_of(story, p["key"])] == [gone["key"]]]
    for p in only_here:
        a = find_actor(cli, p["key"], echo=True)
        if a:
            leftovers.append(dict(kind="actor", what=f"actor {p['display']} ({a['id']}), bound to no workspace now"))
            note(f"still there: actor {p['display']} → {a['id']}")

    say("\n  Offboarding is finished by deleting each of them, then checking again:")
    for b in left["boundaries"]:
        cli.run("boundary", "delete", b["id"])
    for p in left["projects"]:
        cli.run("proj", "delete", p["id"], ws=tid["ws"])
    if folder:
        cli.run("lib", "delete", folder["item_id"])
    for p in only_here:
        a = find_actor(cli, p["key"])
        if a:
            # Their memories lived only in the deleted workspace, so there are no facts left to delete first.
            cli.run("actor", "delete", a["id"])
    again = remains(cli, tid["ws"])
    check("no project left under the deleted workspace", not again["projects"], f"{len(again['projects'])} left")
    check("no boundary left under the deleted workspace", not again["boundaries"], f"{len(again['boundaries'])} left")
    check("Library folder removed", find_folder(cli, folder_name(gone), echo=True) is None)
    for p in only_here:
        check(f"actor {p['display']} removed", find_actor(cli, p["key"], echo=True) is None)
    ok = all(c["ok"] for c in checks)
    say(f"\n  offboarding checks: {sum(c['ok'] for c in checks)}/{len(checks)} ✓   ·   "
        f"left behind by `workspace delete` alone: {len(leftovers)}")
    emit("offboard", "", tenant=gone["name"], refusal=refusal, took=took, checks=checks, leftovers=leftovers,
         before={k: len(v) for k, v in before.items()})
    return dict(ok=ok, checks=checks, leftovers=leftovers, took=took, refusal=refusal)


# --------------------------------------------------------------------------- cleanup

def clear_workspace(cli: CLI, ws_id: str, quiet: bool = False) -> int:
    """Delete the boundaries and projects under a workspace id; `workspace delete` leaves both behind."""
    left = remains(cli, ws_id, echo=not quiet)
    for b in left["boundaries"]:
        cli.run("boundary", "delete", b["id"])
    for p in left["projects"]:
        cli.run("proj", "delete", p["id"], ws=ws_id)
    return len(left["boundaries"]) + len(left["projects"])


def cleanup(cli: CLI, story: dict, quiet: bool = False) -> None:
    for w in demo_workspaces(cli, echo=not quiet):
        n = clear_workspace(cli, w["id"], quiet)
        cli.run("workspace", "delete", w["id"], "--confirm", w["name"])
        note(f"deleted workspace “{w['name']}” ({n} project(s) and boundaries first)")
    for ws_id in (json.loads(OFFBOARDED.read_text()) if OFFBOARDED.exists() else []):
        n = clear_workspace(cli, ws_id, quiet)
        if n:
            note(f"removed {n} project(s)/boundaries left under the offboarded workspace {ws_id}")
    if OFFBOARDED.exists():
        OFFBOARDED.unlink()
    for t in story["tenants"]:
        folder = find_folder(cli, folder_name(t), echo=not quiet)
        if folder:
            cli.run("lib", "delete", folder["item_id"])
            note(f"deleted Library folder {folder_name(t)}/")
    for key in [story["bot"]["key"], *[p["key"] for p in story["people"]]]:
        a = find_actor(cli, key, echo=not quiet)
        if a:
            # The demo binds its actors only to its own workspaces, deleted above with their memories.
            cli.run("actor", "delete", a["id"])
            note(f"deleted actor {a.get('display_name')}")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    banner(2, TOTAL, "Provision — one workspace per tenant, with its own project and documents")
    ids = provision(cli, story, reset)
    banner(3, TOTAL, "Write memory — pinned rules and a support conversation in each tenant")
    write_memory(cli, story, ids)
    banner(4, TOTAL, "Isolation — the same question in every tenant, every hit scanned for every tenant's markers")
    m = leak_matrix(cli, story, ids)
    banner(5, TOTAL, "One person, two tenants — one actor id, two separate memories")
    p = same_person(cli, story, ids)
    banner(6, TOTAL, "Default-deny — a tenant naming another tenant's data is refused")
    d = default_deny(cli, story, ids)
    banner(7, TOTAL, "Offboard a tenant — `workspace delete`, and what it leaves behind")
    o = offboard(cli, story, ids)
    result = dict(leaks=m["leaks"], own=m["own"], shared_ids=p["shared"], denied=sum(r["refused"] for r in d),
                  deny_total=len(d), offboard_ok=o["ok"], leftovers=[x["what"] for x in o["leftovers"]])
    OUT.mkdir(exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(dict(result, matrix=m["matrix"], deny=d, offboard=o), indent=2), encoding="utf-8")
    say(f"\n  Summary: cross-tenant hits {m['leaks']} · same-person shared ids {p['shared']} · "
        f"refused {result['denied']}/{result['deny_total']} · offboarding {'✓' if o['ok'] else '✗'}")
    note(f"result written to {OUT / 'result.json'}")
    emit("done", "", **result)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "matrix", "cleanup"],
                    help="run = full demo (default); matrix = only re-ask the question in each tenant; cleanup = delete everything")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = {"cleanup": 2, "matrix": 2}.get(args.command, TOTAL)
    banner(1, total, "Connect — API key and team")
    cli = connect(args.show_json)
    story = load_story()
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove every workspace, folder and actor the demo created")
        cleanup(cli, story)
        return
    if args.command == "matrix":
        banner(2, 2, "Isolation — the same question in every tenant")
        ids: dict = {"tenants": {}, "actors": {}}
        for key in [story["bot"]["key"], *[p["key"] for p in story["people"]]]:
            a = find_actor(cli, key)
            ids["actors"][key] = a["id"] if a else ""
        tenants = []
        for t in story["tenants"]:
            ws = find_workspace(cli, t)
            if ws is None:
                note(f"{t['name']} has no workspace (offboarded, or the demo has not run); skipping it")
                continue
            proj = find_project(cli, t, ws["id"])
            ids["tenants"][t["key"]] = dict(ws=ws["id"], ws_name=ws["name"], project=proj["id"], folder="")
            tenants.append(t)
        if not tenants:
            die("no tenant workspaces yet; run `python3 demo.py` first")
        leak_matrix(cli, dict(story, tenants=tenants), ids)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py` again re-provisions the offboarded tenant and replays it; "
          "`python3 demo.py cleanup` removes the demo data.")


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

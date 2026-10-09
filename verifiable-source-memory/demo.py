#!/usr/bin/env python3
"""
Verifiable source memory for agents — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larch Valley Credit Union (fictional) is reviewing its member-support agent before
launch. Compliance has one rule: every statement the agent makes must cite a source
that a person can check by hand.

The credit union's published documents (two versions of the fee schedule, the funds
availability policy, the wire transfer procedures) go into MemoryLake with their
provenance — version, effective date, the version they replace, and the file's
sha256 — on the Library item. A member, Dana Whitlock, has one chat on record and
one note from Member Services.

Four member questions come in. The agent's drafted claims are scripted here (LLM
steps are not available on a free account); MemoryLake supplies the evidence:

  - a document claim is cited to the passage that states it: the document, its
    version, the page number, the box on the page, and the paragraph, verbatim;
  - a member claim is cited to the fact that states it, with how that fact came to
    exist (extracted from the member's own message, quoted, or stated by staff);
  - a claim that only a superseded version supports is replaced, and a claim that
    nothing supports is dropped before the answer goes out.

Then every citation is checked against the original file: download it, compare its
sha256 with the one pinned at ingest, and find the excerpt in that page's text layer.
The citations are exported as JSON that `python3 demo.py verify <file>` re-checks later.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
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
ORIGINALS = OUT / "originals"  # the downloaded originals the citations are checked against
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-vsm"  # memorylake-usecases / verifiable-source-memory

SOURCES_PROJECT = dict(
    custom_id=f"{PREFIX}-sources",
    name="Larch Valley CU — published member documents",
    description="Fee schedules, policies and procedures the member-support agent may cite. Demo data.",
)
CHATS_PROJECT = dict(
    custom_id=f"{PREFIX}-member-chats",
    name="Larch Valley CU — member chats",
    description="Member conversations with the support agent. Demo data.",
)

# Our own provenance keys on a Library item. The server adds its own `x_*` keys (one is an internal
# storage path): those are never printed.
PROVENANCE_KEYS = ("title", "doc_type", "version", "effective", "superseded", "supersedes", "publisher", "sha256")

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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
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


# --------------------------------------------------------------------------- data

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def load_provenance() -> dict:
    return json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def lib_name(file: str) -> str:
    """The Library file name. Copies of the demo with another PREFIX get their own names, so that
    `--on-conflict overwrite` and cleanup-by-name never touch another copy's files."""
    return file if PREFIX == "mlu-vsm" else f"{PREFIX}-{file}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def norm(s: str) -> str:
    return " ".join((s or "").split())


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def status_on(prov: dict, day: str) -> str:
    """Was this version the one in force on `day`?  (Dates are ISO strings, so they compare as text.)"""
    if prov.get("effective") and day < prov["effective"]:
        return "not yet effective"
    if prov.get("superseded") and day >= prov["superseded"]:
        return "superseded"
    return "current"


# --------------------------------------------------------------------------- PDF text layer

def pdf_pages_text(data: bytes) -> list[str]:
    """Each page's text layer, read from the PDF bytes themselves (no PDF library).

    It understands the plain, uncompressed PDFs that data/make_sources.py writes: one `(…) Tj`
    per line of text. That is enough to check a citation against the file without trusting
    the index that produced it."""
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj", data, re.S)}
    root = int(re.search(rb"/Root (\d+) 0 R", data).group(1))
    pages = int(re.search(rb"/Pages (\d+) 0 R", objs[root]).group(1))
    kids = [int(k) for k in re.findall(rb"(\d+) 0 R", re.search(rb"/Kids \[(.*?)\]", objs[pages]).group(1))]
    out = []
    for k in kids:
        content = int(re.search(rb"/Contents (\d+) 0 R", objs[k]).group(1))
        stream = re.search(rb"stream\n(.*)\nendstream", objs[content], re.S).group(1)
        shown = re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", stream)
        out.append(" ".join(re.sub(rb"\\(.)", rb"\1", s).decode("latin-1") for s in shown))
    return out


def pdf_page_layout(data: bytes, page: int) -> dict:
    """Where each line of text sits on one page (PDF points, origin bottom-left) — the web app draws the
    page from this and lays MemoryLake's box over it."""
    objs = {int(m.group(1)): m.group(2) for m in re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj", data, re.S)}
    root = int(re.search(rb"/Root (\d+) 0 R", data).group(1))
    pages = int(re.search(rb"/Pages (\d+) 0 R", objs[root]).group(1))
    kids = [int(k) for k in re.findall(rb"(\d+) 0 R", re.search(rb"/Kids \[(.*?)\]", objs[pages]).group(1))]
    obj = objs[kids[page - 1]]
    w, h = (float(x) for x in re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", obj).groups())
    content = int(re.search(rb"/Contents (\d+) 0 R", obj).group(1))
    stream = re.search(rb"stream\n(.*)\nendstream", objs[content], re.S).group(1)
    lines = [dict(font=m.group(1).decode(), size=float(m.group(2)), x=float(m.group(3)), y=float(m.group(4)),
                  text=re.sub(rb"\\(.)", rb"\1", m.group(5)).decode("latin-1"))
             for m in re.finditer(rb"/(F\d) ([\d.]+) Tf ([\d.]+) ([\d.]+) Td \(((?:\\.|[^\\)])*)\) Tj", stream)]
    rules = [[float(v) for v in m.groups()] for m in re.finditer(rb"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", stream)]
    return dict(width=w, height=h, pages=len(kids), lines=lines, rules=rules)


# --------------------------------------------------------------------------- lookups

def find_project(cli: CLI, spec: dict):
    return cli.try_run("proj", "get", spec["custom_id"], "--by-custom-id", scoped=True)


def ensure_project(cli: CLI, spec: dict) -> str:
    proj = find_project(cli, spec)
    if proj is None:
        proj = cli.run("proj", "create", "--name", spec["name"], "--custom-id", spec["custom_id"],
                       "--description", spec["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    return proj["id"]


def find_actor(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("actor", "get", cid(key), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`, but the server still honours the type.
    # Pass it whenever the installed CLI still accepts it.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def ensure_actor(cli: CLI, key: str, display: str, actor_type: str, description: str) -> str:
    actor = find_actor(cli, key)
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", display,
                        *actor_type_args(cli, actor_type), "--description", description)
        note(f"created {display} → {actor['id']}")
    else:
        note(f"{display} already exists → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"]


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    page = cli.run("proj", "doc", "list", "--project", project_id, scoped=True, echo=echo) or {}
    return page.get("items") or []


def list_library(cli: CLI, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", flag, scope_id, "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


def fact_text(f: dict) -> str:
    return f.get("fact") or f.get("content") or ""


# --------------------------------------------------------------------------- step 2: sources

def ingest_sources(cli: CLI, reset: bool) -> dict:
    if reset:
        note("--reset: deleting both projects, the conversation, the actors and the uploaded files, then starting over")
        cleanup(cli, quiet=True)
    prov = load_provenance()
    project_id = ensure_project(cli, SOURCES_PROJECT)
    have = {d.get("name") for d in list_documents(cli, project_id)}
    todo = [f for f in prov if lib_name(f) not in have]
    if todo:
        ids = []
        for f in todo:
            data = (SOURCES / f).read_bytes()
            # Provenance travels with the file: version, dates, publisher, and the sha256 of these exact bytes.
            attrs = dict(prov[f], sha256=sha256_of(data))
            item = cli.run("lib", "upload", os.path.relpath(SOURCES / f), "--name", lib_name(f),
                           "--on-conflict", "overwrite", "--xattrs", json.dumps(attrs, separators=(",", ":")))
            ids.append(item["item_id"])
            note(f"uploaded {item['name']} with its provenance")
        t0 = time.time()
        res = cli.run("proj", "doc", "import", "--project", project_id, *ids, "--wait", scoped=True) or {}
        note(f"imported {res.get('success_count', len(ids))} document(s) ({int(time.time() - t0)}s)")
    else:
        note("all four documents are already in the project")

    # Read the provenance back from the Library: the citation carries what the server holds, not our copy.
    docs = {d["name"]: d for d in list_documents(cli, project_id, echo=False)}
    lib = {i["name"]: i for i in list_library(cli, echo=False)}
    story = load_story()
    sources: dict[str, dict] = {}
    rows = []
    for f in prov:
        name = lib_name(f)
        item = cli.run("lib", "get", lib[name]["item_id"]) if name in lib else {}
        attrs = {k: v for k, v in (item.get("x_attrs") or {}).items() if k in PROVENANCE_KEYS}
        want = dict(prov[f], sha256=sha256_of((SOURCES / f).read_bytes()))
        if item and attrs != want:
            # Provenance changed in data/provenance.json since the upload: update it in place.
            cli.run("lib", "xattr", "set", item["item_id"], "--attrs", json.dumps(want, separators=(",", ":")))
            item = cli.run("lib", "get", item["item_id"])
            attrs = {k: v for k, v in (item.get("x_attrs") or {}).items() if k in PROVENANCE_KEYS}
        doc = docs.get(name) or {}
        pages = len(pdf_pages_text((SOURCES / f).read_bytes()))
        s = dict(file=f, name=name, document_id=doc.get("id"), status=doc.get("status"),
                 library_item_id=item.get("item_id"), provenance=attrs, pages=pages,
                 in_force=status_on(attrs, story["today"]))
        sources[name] = s
        rows.append(s)
    lines = [f"\n  {'document':<40} {'version':<8} {'effective':<11} {'on ' + story['today']:<14} {'pages':<5} sha256 (pinned)"]
    for s in rows:
        p = s["provenance"]
        lines.append(f"  {s['name']:<40} {p.get('version', '?'):<8} {p.get('effective', '?'):<11} "
                     f"{s['in_force']:<14} {s['pages']:<5} {str(p.get('sha256', '?'))[:12]}…")
    lines.append("\n  The provenance is the Library item's x_attrs (`lib get`); the sha256 pins the exact bytes imported.")
    emit("text", "\n".join(lines))
    emit("sources", "", project=dict(id=project_id, name=SOURCES_PROJECT["name"]), today=story["today"], sources=rows)
    return dict(project=project_id, sources=sources)


# --------------------------------------------------------------------------- step 3: member memory

def find_conv(cli: CLI, story: dict, echo: bool = True):
    try:
        return cli.run("conv", "get", cid(story["chat"]["custom_id"]), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def list_messages(cli: CLI, conv_id: str, echo: bool = True) -> list[dict]:
    page = cli.run("conv", "msg", "list", conv_id, "--page-size", "50", echo=echo) or {}
    return sorted(page.get("items") or [], key=lambda m: int(m.get("sequence_no") or 0))


def message_text(m: dict) -> str:
    parts = []
    for b in m.get("content") or []:
        if isinstance(b, dict) and b.get("text"):
            parts.append(b["text"])
    return " ".join(parts) or m.get("text") or ""


def wait_for_memory(cli: CLI, conv_id: str, timeout: int = 600) -> None:
    t0 = last = time.time()
    first = True
    while time.time() - t0 < timeout:
        try:
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first) or {}
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            status = {}
        first = False
        if status.get("cook_finished"):
            note(f"memory ready in {int(time.time() - t0)}s")
            return
        if time.time() - last >= 30:
            emit("progress", f"  … still turning the chat into memory ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
            last = time.time()
        time.sleep(5)
    note(f"gave up after {timeout}s; the chat is still processing server-side")


def setup_member(cli: CLI) -> dict:
    story = load_story()
    m, a = story["member"], story["assistant"]
    ids = dict(member=ensure_actor(cli, m["custom_id"], m["display"], "HUMAN", m["description"]),
               assistant=ensure_actor(cli, a["custom_id"], a["display"], "ASSISTANT", a["description"]))
    ids["chats"] = ensure_project(cli, CHATS_PROJECT)
    chat = story["chat"]
    conv = find_conv(cli, story)
    done = len(list_messages(cli, conv["id"], echo=False)) if conv else 0
    if conv is None:
        # The chats live in their own project, so nothing extracted from them lands among the published documents.
        conv = cli.run("conv", "create", "--custom-id", cid(chat["custom_id"]), "--project", ids["chats"],
                       "--actors", f"{ids['member']},{ids['assistant']}", "--kind", "DIRECT",
                       "--name", chat["title"], scoped=True)
    else:
        note(f"the chat exists with {done} of {len(chat['turns'])} message(s) → {conv['id']}")
    ids["conv"] = conv["id"]
    start = datetime.fromisoformat(chat["date"].replace("Z", "+00:00"))
    parent = conv.get("current_message_id")
    appended = 0
    for i, (who, words) in enumerate(chat["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(seconds=50 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv["id"], "--actor", ids["member"] if who == "user" else ids["assistant"],
                "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", words]
        if parent:
            args += ["--parent", parent]
        parent = cli.run(*args, scoped=True)["id"]
        appended += 1
    if appended:
        wait_for_memory(cli, conv["id"])
    # Member Services' note is pinned word for word on Dana: a fact a person stated, not one extracted.
    if not any(fact_text(f) == story["staff_note"] for f in list_facts(cli, "--actors", ids["member"], echo=False)):
        cli.run("fact", "add", "--actor", ids["member"], story["staff_note"], scoped=True)
        note("pinned the Member Services note on Dana")
    else:
        note("the Member Services note is already pinned on Dana")
    msgs = list_messages(cli, conv["id"])
    facts = member_facts(cli, ids, echo=False)
    lines = [f"\n  Chat {chat['date'][:10]} ({len(msgs)} messages) → {conv['id']}"]
    lines += [f"    {'Dana' if i % 2 else 'agent'}: {clip(message_text(x), 120)}" for i, x in enumerate(msgs, start=1)]
    lines.append(f"\n  Dana's memory now holds {len(facts)} fact(s) (her actor + the chats project):")
    lines += [f"    · {clip(fact_text(f), 140)}" for f in facts.values()]
    emit("text", "\n".join(lines))
    emit("member", "", member=dict(id=ids["member"], **m), conv=conv["id"], chat_date=chat["date"],
         messages=[dict(id=x["id"], at=x.get("timestamp"), text=message_text(x)) for x in msgs],
         facts=[dict(id=k, text=fact_text(f)) for k, f in facts.items()], staff_note=story["staff_note"])
    return ids


def member_facts(cli: CLI, ids: dict, echo: bool = True) -> dict[str, dict]:
    """Everything remembered about Dana. A chat in a project leaves facts on the person and on the project
    (which one gets what varies from run to run), so both are read."""
    out = {}
    for flag, scope_id, where in (("--actors", ids["member"], "actor"), ("--projects", ids["chats"], "project")):
        for f in list_facts(cli, flag, scope_id, echo=echo):
            out[f["id"]] = dict(f, where=where)
    return out


# --------------------------------------------------------------------------- step 4: answers with citations

def parse_range(r: str) -> tuple[int | None, list[float]]:
    m = re.match(r"P(\d+)\[\d+:([-0-9.,]+)\]", r or "")
    if not m:
        return None, []
    return int(m.group(1)), [round(float(x), 3) for x in m.group(2).split(",")[:4]]


def doc_chunks(cli: CLI, project_id: str, query: str, echo: bool = True) -> list[dict]:
    res = cli.run("search", query, "--projects", project_id, "--types", "document", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    chunks = []
    for d in res.get("documents") or []:
        for it in d.get("items") or []:
            for c in (it.get("highlight") or {}).get("chunks") or []:
                page, box = parse_range(c.get("range") or it.get("table_region_info") or "")
                chunks.append(dict(document_id=d.get("document_id"), document=d.get("document_name") or d.get("file_name"),
                                   rank=d.get("rank"), kind=it.get("type"), page=page, box=box,
                                   chunk_id=c.get("id"), text=c.get("text") or ""))
    return chunks


def table_cells(row: list[str]) -> list[str]:
    """A table passage comes back as CSV without quoting, so "5,525 dollars" arrives as two cells.
    Glue a cell back on when it starts with exactly three digits after a cell that ends in a digit."""
    out: list[str] = []
    for cell in row:
        if out and re.search(r"\d$", out[-1]) and re.match(r"\d{3}\b", cell):
            out[-1] += "," + cell
        else:
            out.append(cell)
    return out


def excerpt_for(chunk: dict, needle: str) -> dict | None:
    """The part of a passage that states the claim: the sentence (paragraph) or the row (table)."""
    if needle not in norm(chunk["text"]):
        return None
    if chunk["kind"] == "table":
        rows = [table_cells(r) for r in csv.reader(io.StringIO(chunk["text"]))]
        header = rows[0] if rows else []
        for row in rows[1:]:
            if needle in ", ".join(row) or needle in ",".join(row):
                return dict(kind="row", text=" | ".join(row), cells=row, header=header)
        return None
    for line in chunk["text"].splitlines():
        for sent in re.split(r"(?<=[a-z0-9][.!?])\s+(?=[A-Z])", line.strip()):
            if needle in norm(sent):
                return dict(kind="sentence", text=norm(sent))
    return dict(kind="sentence", text=norm(chunk["text"]))


def cite_document(claim: dict, chunks: list[dict], sources: dict, today: str) -> dict:
    hits = []
    for c in chunks:
        ex = excerpt_for(c, claim["needle"])
        src = sources.get(c["document"])
        if ex and src:
            hits.append(dict(c, excerpt=ex, source=src, in_force=status_on(src["provenance"], today)))
    current = [h for h in hits if h["in_force"] == "current"]
    if current:
        best = min(current, key=lambda h: (h["rank"] or 99))
        return dict(status="supported", citation=doc_citation(best), also=[doc_citation(h) for h in hits if h is not best])
    if hits:
        return dict(status="stale", citation=None, also=[doc_citation(h) for h in hits])
    return dict(status="unsupported", citation=None, also=[])


def doc_citation(h: dict) -> dict:
    s, p = h["source"], h["source"]["provenance"]
    return dict(type="document", document_id=h["document_id"], document=h["document"], title=p.get("title"),
                library_item_id=s["library_item_id"], version=p.get("version"), effective=p.get("effective"),
                superseded=p.get("superseded"), in_force=h["in_force"], publisher=p.get("publisher"),
                sha256=p.get("sha256"), page=h["page"], box=h["box"], chunk_id=h["chunk_id"], kind=h["kind"],
                excerpt=h["excerpt"]["text"], cells=h["excerpt"].get("cells"), passage=h["text"], rank=h["rank"])


def trace_fact(cli: CLI, f: dict, ids: dict, msgs: dict[str, dict]) -> dict:
    """How did this fact come to exist?  `fact trace` says: extracted (COOK, with the source messages)
    or stated through the API (MANUAL)."""
    scope = ["--actor", ids["member"]] if f["where"] == "actor" else ["--project", ids["chats"]]
    t = cli.run("fact", "trace", f["id"], *scope, scoped=True) or {}
    entries = t.get("trace") or []
    first = entries[0] if entries else {}
    src = [msgs[i] for i in first.get("source_entry_ids") or [] if i in msgs]
    if first.get("source_kind") == "COOK" and src:
        newest = max(src, key=lambda m: m["at"])
        # The batch holds every message cooked together; quote the one the fact's words come from.
        words = set(re.findall(r"[a-z]{4,}", fact_text(f).lower()))
        said = sorted(src, key=lambda m: -len(words & set(re.findall(r"[a-z]{4,}", m["text"].lower()))))[:1]
        return dict(method="extracted", detail=f"extracted from the chat of {newest['at'][:10]} (MemoryLake read it)",
                    messages=[dict(id=m["id"], at=m["at"], who=m["who"], text=m["text"]) for m in said],
                    changes=len(entries))
    when = (first.get("timestamp") or "")[:19]
    return dict(method="stated", detail=f"stated through the API by staff (`fact add`), recorded {when}Z",
                messages=[], changes=len(entries))


def cite_member(cli: CLI, claim: dict, ids: dict, story: dict) -> dict:
    facts = member_facts(cli, ids, echo=False)
    # The agent's own retrieval: Dana's facts and the chats project (a union), ranked by the claim.
    hits = (cli.run("search", claim["text"], "--projects", ids["chats"], "--actors", ids["member"], "--types", "fact",
                    "--top-k", "5", scoped=True) or {}).get("facts") or []
    msgs = {m["id"]: dict(id=m["id"], at=m.get("timestamp") or "", text=message_text(m),
                          who="member" if m.get("actor_id") == ids["member"] or m.get("role") == "user" else "agent")
            for m in list_messages(cli, ids["conv"], echo=False)}
    exact = [facts[h["id"]] for h in hits if h.get("id") in facts and claim["needle"] in fact_text(facts[h["id"]])]
    if not exact:
        exact = [f for f in facts.values() if claim["needle"] in fact_text(f)]
        if exact:
            note("search did not rank the stating fact in its top 5; found it in the full list (`fact list`)")
    keys = story["member_keywords"]
    related = [f for f in facts.values() if f not in exact and all(k in fact_text(f).lower() for k in keys)]
    if not exact:
        return dict(status="unsupported", citation=None, also=[])
    best = exact[0]
    cit = dict(type="fact", fact_id=best["id"], scope=best["where"], text=fact_text(best), **trace_fact(cli, best, ids, msgs))
    also = [dict(type="fact", fact_id=f["id"], scope=f["where"], text=fact_text(f), **trace_fact(cli, f, ids, msgs))
            for f in related[:2]]
    return dict(status="supported", citation=cit, also=also)


def show_citation(n: int, c: dict) -> list[str]:
    if c["type"] == "document":
        box = c["box"]
        where = f"p.{c['page']}" + (f" · box x {box[0]:.2f}–{box[2]:.2f}, y {box[1]:.2f}–{box[3]:.2f}" if len(box) == 4 else "")
        head = f"    [{n}] {c['document']} · v{c['version']} (effective {c['effective']}) · {where}"
        what = "row" if c["kind"] == "table" else "says"
        return [head, f"        {what}: “{clip(c['excerpt'], 150)}”"]
    lines = [f"    [{n}] Dana's memory · fact {c['fact_id']} (on her {'profile' if c['scope'] == 'actor' else 'chats project'})",
             f"        “{clip(c['text'], 150)}”", f"        {c['detail']}"]
    lines += [f"        source message {m['at'][:16].replace('T', ' ')}Z ({m['who']}): “{clip(m['text'], 110)}”" for m in c["messages"][:1]]
    return lines


def answer(cli: CLI, ids: dict, q: dict, story: dict, cites: list[dict]) -> dict:
    today = story["today"]
    emit("text", f"\n  {q['asker']} asks: “{q['question']}”")
    chunks = doc_chunks(cli, ids["project"], q["search"])
    found = sorted({(c["document"], c["page"]) for c in chunks})
    note(f"{len(chunks)} passage(s) retrieved from {len({c['document'] for c in chunks})} document(s), each with a page and a box")
    emit("text", "\n  Draft from the agent (scripted), checked claim by claim:")
    checked, final = [], []
    for claim in q["claims"]:
        if claim.get("source") == "member":
            res = cite_member(cli, claim, ids, story)
        else:
            res = cite_document(claim, chunks, ids["sources"], today)
        row = dict(claim=claim["text"], needle=claim["needle"], status=res["status"], citation=res["citation"], also=res["also"])
        if res["status"] == "supported":
            emit("text", f"    ✓ {claim['text']}")
            final.append(row)
        elif res["status"] == "stale":
            olds = ", ".join(f"{a['document']} v{a['version']} ({a['in_force']} since {a['superseded']})" for a in res["also"])
            emit("text", f"    ⚠ {claim['text']}\n        only a superseded version says this: {olds}")
            fix = claim.get("replace_with")
            if fix:
                res2 = cite_document(fix, chunks, ids["sources"], today)
                row["replaced_by"] = dict(claim=fix["text"], needle=fix["needle"], status=res2["status"], citation=res2["citation"])
                if res2["status"] == "supported":
                    emit("text", f"      → replaced with the current version: ✓ {fix['text']}")
                    final.append(dict(claim=fix["text"], needle=fix["needle"], status="supported",
                                      citation=res2["citation"], also=res2["also"], replaces=claim["text"]))
                else:
                    emit("text", f"      → the replacement has no current source either; dropped")
        else:
            emit("text", f"    ✗ {claim['text']}\n        no passage or fact states this → dropped from the answer")
        checked.append(row)
    lines = ["\n  Answer as sent, every sentence cited:"]
    refs = []
    for row in final:
        cites.append(dict(n=len(cites) + 1, question=q["key"], claim=row["claim"], **row["citation"]))
        refs.append(cites[-1])
        lines.append(f"    {row['claim']} [{cites[-1]['n']}]")
    lines.append("")
    for c in refs:
        lines += show_citation(c["n"], c)
    for row in final:
        for a in row["also"]:
            if a["type"] == "fact":
                lines.append(f"        also remembered: “{clip(a['text'], 110)}”\n          {a['detail']}")
                lines += [f"          source message {m['at'][:16].replace('T', ' ')}Z ({m['who']}): “{clip(m['text'], 100)}”"
                          for m in a["messages"][:1]]
            elif a.get("in_force") != "current":
                lines.append(f"        not cited: {a['document']} v{a['version']} p.{a['page']} says “{clip(a['excerpt'], 70)}” ({a['in_force']})")
    emit("text", "\n".join(lines))
    emit("answer", "", key=q["key"], question=q["question"], asker=q["asker"], checked=checked,
         final=[dict(claim=c["claim"], n=c["n"]) for c in refs], citations=refs, passages=len(chunks),
         pages=[dict(document=d, page=p) for d, p in found])
    return dict(key=q["key"], checked=checked, cited=[c["n"] for c in refs])


# --------------------------------------------------------------------------- step 5: verify against the originals

def verify_citations(cli: CLI, project_id: str, cites: list[dict], member_ids: dict | None = None) -> list[dict]:
    """Check each citation against the original file, not against the index that produced it:
    download the document, compare its sha256 with the one pinned at ingest, and look for the
    excerpt in that page's text layer (a table row: every cell)."""
    results = []
    files: dict[str, bytes] = {}
    ORIGINALS.mkdir(parents=True, exist_ok=True)
    for c in cites:
        if c["type"] == "document" and c["document_id"] not in files:
            path = ORIGINALS / f"{c['document_id']}.pdf"
            cli.run("proj", "doc", "download", c["document_id"], "--project", project_id, "-o", os.path.relpath(path),
                    "--force", scoped=True)
            files[c["document_id"]] = path.read_bytes()
    facts_now = {}
    if member_ids and any(c["type"] == "fact" for c in cites):
        facts_now = member_facts(cli, member_ids)
    lines = []
    for c in cites:
        if c["type"] == "document":
            data = files[c["document_id"]]
            digest = sha256_of(data)
            pages = pdf_pages_text(data)
            page_text = norm(pages[c["page"] - 1]) if c["page"] and c["page"] <= len(pages) else ""
            if c["kind"] == "table":
                pieces = [norm(p) for cell in c["cells"] or [] for p in cell.split(",") if norm(p)]
                found = bool(pieces) and all(p in page_text for p in pieces)
            else:
                found = norm(c["excerpt"]) in page_text
            ok_hash = digest == c["sha256"]
            on_pages = [i for i, t in enumerate(pages, start=1) if norm(c["excerpt"]) in norm(t)] if c["kind"] != "table" else []
            r = dict(n=c["n"], type="document", sha256_ok=ok_hash, sha256=digest, page=c["page"], found=found,
                     pages=len(pages), ok=ok_hash and found)
            lines.append(f"  [{c['n']}] {c['document']} p.{c['page']}: "
                         f"{'✓' if ok_hash else '✗'} sha256 {digest[:12]}… {'matches the pinned one' if ok_hash else 'DIFFERS from ' + str(c['sha256'])[:12]} · "
                         f"{'✓' if found else '✗'} {'every cell of the row' if c['kind'] == 'table' else 'the excerpt'} "
                         f"{'is' if found else 'is NOT'} in page {c['page']}'s text"
                         + ("" if found or not on_pages else f" (found on page {on_pages[0]})"))
        else:
            now = facts_now.get(c["fact_id"])
            same = bool(now) and fact_text(now) == c["text"]
            r = dict(n=c["n"], type="fact", exists=bool(now), unchanged=same, ok=same)
            lines.append(f"  [{c['n']}] fact {c['fact_id']}: "
                         + ("✓ still in Dana's memory, word for word" if same else
                            "✗ changed since it was cited" if now else "✗ no longer in Dana's memory"))
        results.append(r)
    ok = sum(r["ok"] for r in results)
    lines.append(f"\n  {ok} of {len(results)} citation(s) check out against the original files and current memory.")
    emit("text", "\n".join(lines))
    emit("verified", "", results=results, ok=ok, total=len(results))
    return results


def control_checks(cli: CLI, ids: dict, checked: list[dict]) -> list[dict]:
    """The same check, run on the claims that were not sent. It must come out ✗ — otherwise the
    check above proves nothing."""
    dropped = [r for r in checked if r["status"] != "supported"]
    if not dropped:
        return []
    pages: dict[str, list[str]] = {}
    ORIGINALS.mkdir(parents=True, exist_ok=True)
    for name, src in ids["sources"].items():
        path = ORIGINALS / f"{src['document_id']}.pdf"
        cli.run("proj", "doc", "download", src["document_id"], "--project", ids["project"], "-o", os.path.relpath(path),
                "--force", scoped=True, echo=False)
        pages[name] = [norm(t) for t in pdf_pages_text(path.read_bytes())]
    note(f"control: downloaded all {len(pages)} originals and looked for the claims that were not sent")
    out, lines = [], []
    for r in dropped:
        where = [(name, i) for name, ps in pages.items() for i, t in enumerate(ps, start=1) if r["needle"] in t]
        cur = [(n, i) for n, i in where if ids["sources"][n]["in_force"] == "current"]
        old = [(n, i) for n, i in where if ids["sources"][n]["in_force"] != "current"]
        text = f"  ✗ “{r['needle']}” is on no page of a current document"
        if old:
            text += " — only on " + ", ".join(f"{n} p.{i} ({ids['sources'][n]['in_force']})" for n, i in old)
        if cur:
            text = f"  ! “{r['needle']}” IS on " + ", ".join(f"{n} p.{i}" for n, i in cur) + " — the retrieval missed it"
        lines.append(text)
        out.append(dict(needle=r["needle"], current=cur, superseded=old))
    emit("text", "\n  Control — the claims that were not sent, checked the same way:\n" + "\n".join(lines))
    emit("controls", "", controls=out)
    return out


# --------------------------------------------------------------------------- step 6: export

def render(c: dict, org: str) -> str:
    if c["type"] == "document":
        return (f"[{c['n']}] {org}, {c.get('title') or c['document']}, version {c['version']} (effective {c['effective']}), "
                f"p. {c['page']}: \"{c['excerpt']}\"")
    src = c["messages"][0] if c["messages"] else None
    tail = f" Source message, {src['at'][:10]}: \"{src['text']}\"" if src else ""
    return f"[{c['n']}] Member memory, fact {c['fact_id']} ({c['method']}): \"{c['text']}\".{tail}"


def export(ids: dict, story: dict, cites: list[dict], results: list[dict]) -> Path:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"citations-{story['today']}.json"
    by_n = {r["n"]: r for r in results}
    doc = dict(org=story["org"], agent=story["agent"], as_of=story["today"],
               generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               sources_project=ids["project"], member_actor=ids.get("member"), chats_project=ids.get("chats"),
               citations=[dict({k: v for k, v in c.items() if k != "passage"}, verified=by_n.get(c["n"]),
                               rendered=render(c, story["org"])) for c in cites])
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["\n  Natural-language citations:"] + [f"  {render(c, story['org'])}" for c in cites]
    lines.append(f"\n  Exported {len(cites)} citation(s) to {os.path.relpath(path)} "
                 f"(source link, version, page, box, excerpt, sha256, verification).")
    lines.append(f"  Re-check them any time: python3 demo.py verify {os.path.relpath(path, HERE)}")
    emit("text", "\n".join(lines))
    emit("exported", "", file=os.path.relpath(path), citations=doc["citations"])
    return path


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    conv = find_conv(cli, story, echo=not quiet)
    if conv:
        # `proj delete` does not delete conversations; remove it explicitly.
        cli.run("conv", "delete", conv["id"], scoped=True)
        note("deleted Dana's chat")
    for spec in (SOURCES_PROJECT, CHATS_PROJECT):
        proj = find_project(cli, spec)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
            note(f"deleted project “{spec['name']}”")
    for key in (story["member"]["custom_id"], story["assistant"]["custom_id"]):
        actor = find_actor(cli, key, echo=not quiet)
        if actor:
            cli.run("actor", "delete", actor["id"])
            note(f"deleted {actor.get('display_name') or key} and every fact on it")
    names = {lib_name(f) for f in load_provenance()}
    removed = 0
    for item in list_library(cli, echo=not quiet):
        if item.get("name") in names:
            cli.run("lib", "delete", item.get("item_id") or item.get("id"))
            removed += 1
    note(f"deleted {removed} uploaded document(s) from the Library")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Sources — the published documents, with their provenance")
    ids = ingest_sources(cli, reset)
    banner(3, TOTAL, "Member memory — Dana's chat and a note from Member Services")
    ids.update(setup_member(cli))
    banner(4, TOTAL, "Answers — every claim cited, or replaced, or dropped")
    cites: list[dict] = []
    answers = [answer(cli, ids, q, story, cites) for q in story["questions"]]
    banner(5, TOTAL, "Verify — each citation against the original file")
    results = verify_citations(cli, ids["project"], cites, ids)
    controls = control_checks(cli, ids, [c for a in answers for c in a["checked"]])
    banner(6, TOTAL, "Export — citations compliance can re-check later")
    path = export(ids, story, cites, results)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   claims={a["key"]: [c["status"] for c in a["checked"]] for a in answers},
                   citations=len(cites), verified=sum(r["ok"] for r in results),
                   controls_found_current=sum(bool(c["current"]) for c in controls),
                   pages={str(c["n"]): c.get("page") for c in cites if c["type"] == "document"},
                   methods=[c.get("method") for c in cites if c["type"] == "fact"], export=path.name)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")
    return summary


def ids_for_reads(cli: CLI) -> dict:
    proj = find_project(cli, SOURCES_PROJECT)
    if proj is None:
        die("the sources project does not exist yet; run `python3 demo.py` first")
    ids = dict(project=proj["id"])
    member = find_actor(cli, load_story()["member"]["custom_id"], echo=False)
    chats = find_project(cli, CHATS_PROJECT)
    if member and chats:
        ids.update(member=member["id"], chats=chats["id"])
    return ids


def verify_file(cli: CLI, file: str) -> None:
    doc = json.loads(Path(file).read_text(encoding="utf-8"))
    ids = ids_for_reads(cli)
    note(f"{len(doc['citations'])} citation(s) exported {doc['generated_at']} for answers as of {doc['as_of']}")
    verify_citations(cli, doc["sources_project"], doc["citations"], ids if "member" in ids else None)


def ask(cli: CLI, question: str) -> None:
    ids = ids_for_reads(cli)
    story = load_story()
    prov = load_provenance()
    sources = {lib_name(f): dict(provenance=dict(p), library_item_id=None) for f, p in prov.items()}
    chunks = doc_chunks(cli, ids["project"], question)
    lines = [f"\n  {len(chunks)} citable passage(s) for “{question}”:"]
    for c in chunks:
        p = (sources.get(c["document"]) or {}).get("provenance") or {}
        box = c["box"]
        lines.append(f"\n  #{c['rank']} {c['document']} v{p.get('version', '?')} ({status_on(p, story['today'])}) · "
                     f"{c['kind']} · p.{c['page']}" + (f" · box x {box[0]:.2f}–{box[2]:.2f}, y {box[1]:.2f}–{box[3]:.2f}" if len(box) == 4 else ""))
        lines += [f"     {clip(l, 130)}" for l in c["text"].splitlines()[:4] if l.strip()]
    emit("text", "\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "verify", "cleanup"],
                    help="run = full demo (default); ask QUESTION = citable passages for any question; "
                         "verify FILE = re-check an exported citations file; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="the question for `ask`, the file for `verify`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command in ("ask", "verify") and not args.arg:
        ap.error(f"{args.command} needs an argument, e.g. python3 demo.py ask \"What is the stop payment fee?\" "
                 f"or python3 demo.py verify out/citations-2026-10-08.json")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "ask":
        banner(2, 2, "Citable passages")
        ask(cli, args.arg)
        return
    if args.command == "verify":
        banner(2, 2, f"Verify — {args.arg}")
        verify_file(cli, args.arg)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py ask \"…\"` shows citable passages for any question; "
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

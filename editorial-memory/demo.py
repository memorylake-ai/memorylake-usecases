#!/usr/bin/env python3
"""
Editorial memory for publishing teams — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
The Tidewater Ledger (fictional) keeps every published story in one Library
folder, tagged when it is uploaded with its publication date, desk and author,
and imported into an archive project so writers can ask "what have we already
written about this?".  The archive is bigger than one page:

* Every list call pages, and the three list calls the demo uses page
  differently — `lib list` returns 50 items by default (and refuses more),
  `proj doc list` returns 20 by default (up to 100).  Reconciling "what is in
  the Library" against "what is in the index" with one call each reports
  dozens of stories missing that are not missing.  Paging every list to the end
  finds the real gap: the one story that arrived after the archive was indexed.
* Sources live in one sub-folder per organisation.  When an organisation
  withdraws its data, the standards editor tags the whole folder
  `trust: retracted`.  Files do not inherit folder tags (the server returns none
  for them), so the demo resolves a file's effective tag by walking up its
  `parent_item_id` chain.
* A new writer's draft is checked against the archive (prior coverage), the
  sources (effective trust) and the house style (rules stored as facts with a
  pattern in their metadata).

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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ARCHIVE = DATA / "archive"
SOURCES = DATA / "sources"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-edm"  # memorylake-usecases / editorial-memory
ROOT_FOLDER = f"{PREFIX}-tidewater-ledger"
ARCHIVE_TAGS = ["published", "desk", "author"]
TRUST_KEYS = ["trust", "flagged_on", "flagged_by", "reason"]
CITE = re.compile(r"\[source:\s*([^\]]+?)\s*\]")
CITE_SPAN = re.compile(r"\s*\[source:[^\]]*\]")

PRESIGNED = re.compile(r"https://[^\"\s]*X-Amz-[^\"\s]*")
HIDDEN = "<signed URL, not printed>"
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


def say(msg: str = "") -> None:
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


class CLI:
    """Runs `memorylake …`, echoes the command, parses the JSON reply, retries read-only calls."""

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search"}
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
        # Read-only calls retry on a dropped connection, and so do `lib xattr` and `fact update` (they set the same values again).
        retryable = any(str(a) in self.READ_ONLY for a in args[:3]) or tuple(map(str, args[:2])) in (("lib", "xattr"), ("fact", "update"))
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
            # Library items carry pre-signed thumbnail URLs (with an access key id and a signature): never print them.
            emit("json", PRESIGNED.sub(HIDDEN, out.rstrip()))
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            return out


def create(cli: CLI, args: list, refind):
    """A create call that is not idempotent: on a dropped connection, look for what it may have created before retrying."""
    for attempt in range(5):
        try:
            return cli.run(*args, echo=attempt == 0)
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            note(f"network hiccup; checking whether it went through (retry {attempt + 1}/4)")
            time.sleep(2 * (attempt + 1))
            found = refind()
            if found:
                return found


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


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def attrs_json(d: dict) -> str:
    return json.dumps(d, separators=(",", ":"), ensure_ascii=False)


def shown_path(p: Path) -> str:
    """The path relative to where you ran the demo (so the echoed command is the one you would type)."""
    rel = os.path.relpath(p)
    return str(p) if rel.startswith("..") else rel


def mine(xattrs: dict | None, keys: list[str]) -> dict:
    """Only our own tag keys — the server adds `x_*` keys, one of them a signed URL."""
    return {k: v for k, v in (xattrs or {}).items() if k in keys}


# --------------------------------------------------------------------------- reading

def pages(cli: CLI, args: list[str], size: int, echo: bool = True) -> tuple[list[dict], int]:
    """All pages of a list command; stops only when the token is null. Returns (items, calls)."""
    items, token, calls = [], None, 0
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, echo=echo and token is None) or {}
        calls += 1
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items, calls


def one_call(cli: CLI, args: list[str], echo: bool = True) -> tuple[list[dict], bool]:
    """What a script that calls the list once, with the server's default page size, sees."""
    page = cli.run(*args, echo=echo) or {}
    return page.get("items") or [], bool(page.get("continuation_token"))


def list_folder(cli: CLI, folder: str, keys: list[str] | None = None, echo: bool = True) -> list[dict]:
    args = ["lib", "list", folder] + (["--xattr-keys", ",".join(keys)] if keys else [])
    return pages(cli, args, 50, echo=echo)[0]


def files_in(cli: CLI, folder: str, keys: list[str] | None = None, echo: bool = True) -> list[dict]:
    return [i for i in list_folder(cli, folder, keys, echo=echo) if i.get("type") == "file"]


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["proj", "doc", "list", "--project", project_id, "--workspace", cli.workspace], 100, echo=echo)[0]


def find_project(cli: CLI, echo: bool = True):
    try:
        return cli.run("proj", "get", cid("archive"), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_folder(cli: CLI, parent: str, name: str, echo: bool = True):
    return next((i for i in list_folder(cli, parent, echo=echo) if i.get("type") in ("directory", "folder") and i["name"] == name), None)


# --------------------------------------------------------------------------- step 2: setup

def ensure_folder(cli: CLI, parent: str, name: str, attrs: dict) -> str:
    folder = find_folder(cli, parent, name, echo=False)
    if folder is None:
        folder = create(cli, ["lib", "mkdir", name, "--parent", parent, "--on-conflict", "deny", "--xattrs", attrs_json(attrs)],
                        lambda: find_folder(cli, parent, name, echo=False))
        note(f"created folder {name}/ → {folder['item_id']}")
    else:
        note(f"folder {name}/ already exists")
    return folder["item_id"]


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created first")
        cleanup(cli, quiet=True)
    root = ensure_folder(cli, "MY_SPACE", ROOT_FOLDER, {"publication": story["publication"]})
    ids = dict(root=root,
               archive=ensure_folder(cli, root, "archive", story["folder_tags"]["archive"]),
               sources=ensure_folder(cli, root, "sources", story["folder_tags"]["sources"]))
    ids["orgs"] = {org: ensure_folder(cli, ids["sources"], org, {"org": org})
                   for org in dict.fromkeys(s["org"] for s in story["sources"])}
    proj = find_project(cli)
    if proj is None:
        spec = story["project"]
        proj = create(cli, ["proj", "create", "--name", spec["name"], "--custom-id", cid("archive"),
                            "--description", spec["description"], "--workspace", cli.workspace],
                      lambda: find_project(cli, echo=False))
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']}")
    ids["project"] = proj["id"]
    say(f"\n  {story['publication']} ({story['town']})")
    say(f"    {ROOT_FOLDER}/archive/        every published story, tagged {', '.join(ARCHIVE_TAGS)}")
    say(f"    {ROOT_FOLDER}/sources/<org>/  one folder per source organisation: {', '.join(ids['orgs'])}")
    say(f"    project                       {story['project']['name']}  (the archive index writers search)")
    emit("setup", "", ids=ids, folder=ROOT_FOLDER, orgs=list(ids["orgs"]), project=story["project"]["name"])
    return ids


# --------------------------------------------------------------------------- step 3: archive

def upload(cli: CLI, folder: str, path: Path, tags: dict, echo: bool = True) -> dict:
    """`lib upload` is not idempotent: on a dropped connection, look for the file before trying again."""
    args = ["lib", "upload", shown_path(path), "--parent", folder, "--xattrs", attrs_json(tags)]
    for attempt in range(5):
        try:
            return cli.run(*args, echo=echo and attempt == 0)
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            note(f"network hiccup; checking whether {path.name} went through (retry {attempt + 1}/4)")
            time.sleep(2 * (attempt + 1))
            found = next((i for i in files_in(cli, folder, echo=False) if i["name"] == path.name), None)
            if found:
                return found
    return {}


def upload_archive(cli: CLI, ids: dict, story: dict) -> dict:
    late = story["late"]
    have = {i["name"]: i for i in files_in(cli, ids["archive"])}
    if late in have:
        # A second run starts the story over: the late story has not arrived yet.
        for d in list_documents(cli, ids["project"], echo=False):
            if d["name"] == late:
                cli.run("proj", "doc", "delete", "--project", ids["project"], "--workspace", cli.workspace, d["id"])
        cli.run("lib", "delete", have.pop(late)["item_id"])
        note(f"{late} came in at step 5 of an earlier run — removed so the story starts over")
    todo = [s for s in story["archive"] if s["file"] != late and s["file"] not in have]
    t0 = time.time()
    # Oldest first, in batches: the listing is newest first, so the oldest stories are the ones past the first page.
    # The first batch is exactly the stories that will not fit on page 1, so those are the oldest ones.
    first = max(1, len(have) + len(todo) - 50)
    batches = [todo[:first]] + [todo[i:i + 8] for i in range(first, len(todo), 8)]
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for batch in (b for b in batches if b):
            list(pool.map(lambda s: upload(cli, ids["archive"], ARCHIVE / s["file"], s["tags"],
                                           echo=s is todo[0]), batch))
            done += len(batch)
            if done == len(batch) and len(todo) > 1:
                note(f"… the same command for the other {len(todo) - 1} stories, oldest first, 8 at a time (echo off)")
            emit("progress", f"    uploaded {done}/{len(todo)}", done=done, total=len(todo))
    if todo:
        note(f"{len(todo)} stories uploaded with their tags in {int(time.time() - t0)}s; {len(have)} were already there")
    else:
        note(f"all {len(have)} stories already in the folder")
    return inventory(cli, ids, story)


def inventory(cli: CLI, ids: dict, story: dict) -> dict:
    say("\n  How many stories are in the archive folder?")
    first, more = one_call(cli, ["lib", "list", ids["archive"], "--xattr-keys", "published"])
    first_files = [i for i in first if i.get("type") == "file"]
    say(f"    one call, default page size:  {len(first_files)} files"
        + ("  + a continuation_token (there is more)" if more else ""))
    refused = ""
    try:
        cli.run("lib", "list", ids["archive"], "--page-size", "51")
    except CLIError as e:
        m = re.search(r'"message"\s*:\s*"([^"]+)"', e.stderr + e.stdout) or re.search(r"(page_size[^\n\[]+)", e.stderr + e.stdout)
        refused = m.group(1).strip() if m else str(e).splitlines()[-1]
        say(f"    asking for 51 per page:       refused — {refused}")
    rows, calls = pages(cli, ["lib", "list", ids["archive"], "--xattr-keys", ",".join(ARCHIVE_TAGS)], 50)
    rows = [dict(name=i["name"], item_id=i["item_id"], tags=mine(i.get("x_attrs"), ARCHIVE_TAGS)) for i in rows if i.get("type") == "file"]
    say(f"    every page until the token is null: {len(rows)} files in {calls} calls")
    seen = {i["name"] for i in first_files}
    beyond = sorted((r for r in rows if r["name"] not in seen), key=lambda r: r["tags"].get("published", ""))
    if beyond:
        say(f"\n  The {len(beyond)} the first page never shows (the listing is newest first, so these are the oldest):")
        for r in beyond:
            say(f"    {r['tags'].get('published', '?')}  {r['tags'].get('desk', '?'):<12} {r['name']}")
    tagged = sum(1 for r in rows if all(r["tags"].get(k) for k in ARCHIVE_TAGS))
    say(f"\n  {tagged} of {len(rows)} stories carry all three tags ({', '.join(ARCHIVE_TAGS)})")
    out = dict(first=len(first_files), more=more, refused=refused, total=len(rows), calls=calls,
               beyond=[dict(name=r["name"], **r["tags"]) for r in beyond], tagged=tagged)
    emit("inventory", "", **out)
    return out


# --------------------------------------------------------------------------- step 4/5: index + reconcile

def import_items(cli: CLI, ids: dict, rows: list[dict], whole_folder: bool) -> int:
    t0 = time.time()
    if whole_folder:
        args = ["proj", "doc", "import", "--project", ids["project"], "--workspace", cli.workspace, "--wait",
                ids["archive"], "--recursive"]
    else:
        args = ["proj", "doc", "import", "--project", ids["project"], "--workspace", cli.workspace, "--wait",
                *[r["item_id"] for r in rows]]
    # Re-importing a file the project already holds is reported as a duplicate, so a retry is safe.
    res = create(cli, args, lambda: None) or {}
    failed = [d for d in res.get("details") or [] if d.get("result") not in ("success", "duplicate")]
    if failed:
        die(f"{len(failed)} file(s) failed to import: {', '.join(str(d.get('drive_item_id')) for d in failed)}")
    secs = int(time.time() - t0)
    note(f"imported {res.get('success_count', len(rows))} · duplicates {res.get('duplicate_count', 0)}"
         f" · failed {res.get('failure_count', 0)} in {secs}s")
    return secs


def reconcile(cli: CLI, ids: dict, label: str, echo: bool = True) -> dict:
    say(f"\n  Reconcile the Library folder against the index ({label})")
    lib1, _ = one_call(cli, ["lib", "list", ids["archive"]], echo=echo)
    doc1, _ = one_call(cli, ["proj", "doc", "list", "--project", ids["project"], "--workspace", cli.workspace], echo=echo)
    lib1_names = {i["name"] for i in lib1 if i.get("type") == "file"}
    doc1_names = {d["name"] for d in doc1}
    naive = sorted(lib1_names - doc1_names)
    libs = files_in(cli, ids["archive"], echo=echo)
    docs = list_documents(cli, ids["project"], echo=echo)
    lib_names, doc_names = {i["name"] for i in libs}, {d["name"] for d in docs}
    missing = sorted(lib_names - doc_names)
    extra = sorted(doc_names - lib_names)
    say(f"    one call each (default page sizes):  Library {len(lib1_names)} · index {len(doc1)}"
        f"  → “{len(naive)} not indexed”" + (f"  ✗ false alarm — {len([n for n in naive if n in doc_names])} of them are indexed"
                                              if naive and set(naive) - set(missing) else ""))
    say(f"    every page:                           Library {len(lib_names)} · index {len(doc_names)}"
        f"  → not indexed {len(missing)} · indexed but not in the folder {len(extra)}"
        + ("  ✓" if not missing and not extra else ""))
    for n in missing:
        say(f"      not indexed: {n}")
    for n in extra:
        say(f"      in the index only: {n}")
    out = dict(label=label, naive_lib=len(lib1_names), naive_docs=len(doc1), naive_missing=len(naive),
               false_alarms=len([n for n in naive if n in doc_names]), lib=len(lib_names), docs=len(doc_names),
               missing=missing, extra=extra)
    emit("reconcile", "", **out)
    return out


def describe_index(cli: CLI, ids: dict, story: dict) -> str:
    tags = {r["name"]: mine(r.get("x_attrs"), ARCHIVE_TAGS) for r in files_in(cli, ids["archive"], ["published"], echo=False)}
    indexed = [d["name"] for d in list_documents(cli, ids["project"], echo=False)]
    latest = max((tags.get(n, {}).get("published", "") for n in indexed), default="")
    desc = f"{story['project']['description']} Indexed: {len(indexed)} stories, latest published {latest}."
    cli.run("proj", "update", ids["project"], "--description", desc, scoped=True)
    got = (cli.run("proj", "get", ids["project"], scoped=True, echo=False) or {}).get("description", "")
    say(f"    project description now: “{got}”")
    return got


def index(cli: CLI, ids: dict, story: dict) -> dict:
    libs = files_in(cli, ids["archive"])
    have = {d["name"] for d in list_documents(cli, ids["project"])}
    todo = [dict(name=i["name"], item_id=i["item_id"]) for i in libs if i["name"] not in have]
    secs = 0
    if todo:
        note(f"importing {len(todo)} stories into the archive project (about 4 minutes for the whole folder)")
        secs = import_items(cli, ids, todo, whole_folder=not have)
    else:
        note(f"the archive project already holds all {len(have)} stories")
    stats = cli.run("proj", "stats", ids["project"], scoped=True) or {}
    st = stats.get("document_status") or {}
    say(f"    proj stats: {stats.get('document_count')} documents · okay {st.get('okay')} · running {st.get('running')}"
        f" · error {st.get('error')}")
    rec = reconcile(cli, ids, "after indexing")
    desc = describe_index(cli, ids, story)
    out = dict(imported=len(todo), seconds=secs, stats=st, reconcile=rec, description=desc)
    emit("index", "", **out)
    return out


def late_arrival(cli: CLI, ids: dict, story: dict) -> dict:
    late = next(s for s in story["archive"] if s["file"] == story["late"])
    say(f"\n  {late['tags']['published']}: the {late['tags']['desk']} desk files “{late['file']}” to the archive folder")
    item = upload(cli, ids["archive"], ARCHIVE / late["file"], late["tags"])
    before = reconcile(cli, ids, "the next morning")
    rows = [dict(name=n, item_id=i["item_id"]) for i in files_in(cli, ids["archive"], echo=False) for n in before["missing"] if i["name"] == n]
    if rows:
        import_items(cli, ids, rows, whole_folder=False)
    after = reconcile(cli, ids, "after importing just the missing one", echo=False)
    desc = describe_index(cli, ids, story)
    out = dict(file=late["file"], item_id=(item or {}).get("item_id"), before=before, after=after, description=desc)
    emit("late", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: sources + a retraction

def effective(cli: CLI, item_id: str, root: str, cache: dict, echo: bool = False) -> dict:
    """Walk up parent_item_id until a folder (or the file itself) carries a trust tag."""
    chain, cur = [], item_id
    for _ in range(8):
        it = cache.get(cur) or cli.run("lib", "get", cur, echo=echo)
        cache[cur] = it
        own = mine(it.get("x_attrs"), TRUST_KEYS)
        chain.append(it["name"] + ("/" if it.get("type") in ("directory", "folder") else ""))
        if own.get("trust"):
            return dict(trust=own["trust"], where=it["name"], inherited=cur != item_id, tags=own, chain=chain)
        parent = it.get("parent_item_id")
        if cur == root or not parent or parent == cur:
            break
        cur = parent
    return dict(trust=None, where=None, inherited=False, tags={}, chain=chain)


def all_sources(cli: CLI, ids: dict, echo: bool = False) -> list[dict]:
    return [dict(org=org, name=i["name"], item_id=i["item_id"], tags=mine(i.get("x_attrs"), TRUST_KEYS))
            for org, fid in ids["orgs"].items() for i in files_in(cli, fid, TRUST_KEYS, echo=echo)]


def sources_and_flag(cli: CLI, ids: dict, story: dict) -> dict:
    flag = story["flag"]
    org_folder = ids["orgs"][flag["org"]]
    uploaded = 0
    for org, fid in ids["orgs"].items():
        have = {i["name"] for i in files_in(cli, fid, echo=False)}
        for s in (s for s in story["sources"] if s["org"] == org and s["file"] not in have):
            upload(cli, fid, SOURCES / org / s["file"], {"org": org, "kind": "source"}, echo=uploaded < 1)
            uploaded += 1
    note(f"{uploaded} source file(s) uploaded into sources/<org>/" if uploaded else "source files already uploaded")
    current = mine((cli.run("lib", "get", org_folder, echo=False) or {}).get("x_attrs"), TRUST_KEYS)
    if current:
        # A second run starts the story over: the folder is not flagged yet.
        cli.run("lib", "xattr", "delete", org_folder, "--keys", ",".join(current))
        note("the folder was flagged by an earlier run — tag removed so the story starts over")

    say(f"\n  {flag['date']}: {flag['by']} flags the whole sources/{flag['org']}/ folder — one call:")
    cli.run("lib", "xattr", "set", org_folder, "--attrs", attrs_json(flag["attrs"]))
    folder_tags = mine((cli.run("lib", "get", org_folder) or {}).get("x_attrs"), TRUST_KEYS)
    say(f"    the folder:  trust = {folder_tags.get('trust')} · flagged_on = {folder_tags.get('flagged_on')}"
        f" · flagged_by = {folder_tags.get('flagged_by')}")

    say(f"\n  The files inside it, asking for the same keys (--xattr-keys {','.join(TRUST_KEYS)}):")
    kids = files_in(cli, org_folder, TRUST_KEYS)
    own = 0
    for i in kids:
        t = mine(i.get("x_attrs"), TRUST_KEYS)
        own += bool(t.get("trust"))
        say(f"    {i['name']:<44} trust = {t.get('trust') or '(none)'}")
    say(f"    files carrying the tag themselves: {own} of {len(kids)}"
        + ("  — folder tags are not inherited; the server returns none for the files" if own == 0 else ""))

    say("\n  Effective trust = the nearest tag walking up parent_item_id (lib get, one level at a time):")
    cache: dict = {}
    rows = []
    for n, s in enumerate(all_sources(cli, ids)):
        e = effective(cli, s["item_id"], ids["root"], cache, echo=n == 0)
        rows.append(dict(org=s["org"], name=s["name"], **e))
    for r in rows:
        mark = "✗" if r["trust"] == "retracted" else "·"
        how = f"{r['trust']} (inherited from {r['where']}/)" if r["inherited"] else (r["trust"] or "no trust tag on the file or its folders")
        say(f"    {mark} {r['org'] + '/' + r['name']:<62} {how}")
    say(f"    path walked for the first one: {' → '.join(rows[0]['chain'])}" if rows else "")
    retracted = [r["name"] for r in rows if r["trust"] == "retracted"]
    say(f"\n  retracted by inheritance: {len(retracted)} of {len(kids)} files in sources/{flag['org']}/"
        + ("  ✓" if len(retracted) == len(kids) and own == 0 else ""))
    out = dict(uploaded=uploaded, folder=folder_tags, kids=len(kids), own=own, rows=rows, retracted=retracted)
    emit("flag", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: house style as facts

def style_facts(cli: CLI, ids: dict, echo: bool = True) -> list[dict]:
    facts, _ = pages(cli, ["fact", "list", "--projects", ids["project"], "--workspace", cli.workspace], 50, echo=echo)
    return [f for f in facts if (f.get("metadata") or {}).get("rule")]


def house_style(cli: CLI, ids: dict, story: dict) -> dict:
    have = {(f.get("metadata") or {}).get("rule"): f for f in style_facts(cli, ids)}
    missing = [r for r in story["style"] if r["rule"] not in have]
    if missing:
        res = cli.run("fact", "add", "--project", ids["project"], *[r["text"] for r in missing], scoped=True) or {}
        for r, f in zip(missing, res.get("facts") or []):
            meta = dict(kind="house-style", rule=r["rule"], pattern=r["pattern"], fix=r["fix"])
            cli.run("fact", "update", f["id"], "--project", ids["project"], "--metadata", attrs_json(meta), scoped=True,
                    echo=r is missing[0])
        note(f"{len(missing)} rule(s) stored as facts on the archive project, each with its check pattern in metadata")
    else:
        note(f"all {len(story['style'])} rules already stored")
    rules = style_facts(cli, ids)
    say("")
    for f in rules:
        m = f.get("metadata") or {}
        say(f"    {f['id'][:18]}…  {m.get('rule'):<26} /{m.get('pattern')}/")
        say(f"        “{clip(f.get('fact') or f.get('text') or '', 110)}”")
    out = dict(rules=[dict(id=f["id"], text=f.get("fact") or f.get("text"), **(f.get("metadata") or {})) for f in rules])
    emit("style", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: check a draft

def search_archive(cli: CLI, ids: dict, question: str, top_k: int, echo: bool = True) -> list[dict]:
    res = cli.run("search", question, "--projects", ids["project"], "--types", "document", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    return res.get("documents") or []


def coverage(cli: CLI, ids: dict, question: str, top_k: int = 6, echo: bool = True) -> dict:
    rows = files_in(cli, ids["archive"], ARCHIVE_TAGS, echo=echo)
    tags = {i["name"]: mine(i.get("x_attrs"), ARCHIVE_TAGS) for i in rows}
    first, _ = one_call(cli, ["lib", "list", ids["archive"], "--xattr-keys", ",".join(ARCHIVE_TAGS)], echo=False)
    first_names = {i["name"] for i in first}
    hits = []
    for h in search_archive(cli, ids, question, top_k, echo=echo):
        name = h.get("document_name") or h.get("file_name") or ""
        t = tags.get(name, {})
        chunks = [c.get("text") or "" for it in h.get("items") or [] for c in (it.get("highlight") or {}).get("chunks") or []]
        body = [ln for c in chunks for ln in c.splitlines() if ln.strip() and not ln.startswith(("#", "The Tidewater Ledger,"))]
        hits.append(dict(rank=h.get("rank"), name=name, published=t.get("published", "?"), desk=t.get("desk", "?"),
                         author=t.get("author", "?"), page2=name not in first_names,
                         excerpt=clip(" ".join(body), 150) or clip(h.get("document_summary"), 150)))
    return dict(question=question, hits=hits, page2=sum(1 for h in hits if h["page2"]))


def show_coverage(c: dict) -> None:
    say(f"\n  Prior coverage — “{c['question']}” (top {len(c['hits'])}, shown oldest first, dates from the tags):")
    for h in sorted(c["hits"], key=lambda h: h["published"]):
        say(f"    {h['published']}  #{h['rank']}  {h['author']:<16} {h['name']}" + ("   ← past page 1" if h["page2"] else ""))
        say(f"                “{h['excerpt']}”")
    if c["page2"]:
        say(f"    {c['page2']} of these {'comes' if c['page2'] == 1 else 'come'} from past the first page of the listing"
            " — a one-call tag lookup would have no date for it" + ("" if c["page2"] == 1 else "m"))


def style_check(text: str, rules: list[dict]) -> list[dict]:
    flags = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        body = CITE_SPAN.sub("", line)
        for r in rules:
            m = re.search(r["pattern"], body)
            if m:
                flags.append(dict(line=n, rule=r["rule"], fact=r["id"], fix=r["fix"], match=m.group(0)))
    return flags


def check(cli: CLI, ids: dict, story: dict, path: Path | None = None, echo: bool = True) -> dict:
    d = story["draft"]
    path = path or DATA / d["file"]
    text = path.read_text(encoding="utf-8")
    say(f"\n  Draft: {shown_path(path)} — {d['author']}, {d['topic']}")

    cov = coverage(cli, ids, d["question"], echo=echo)
    show_coverage(cov)

    say("\n  Sources cited in the draft:")
    srcs = {s["name"]: s for s in all_sources(cli, ids, echo=False)}
    cache: dict = {}
    cites = []
    for name in dict.fromkeys(CITE.findall(text)):
        s = srcs.get(name)
        if not s:
            cites.append(dict(name=name, status="unknown"))
            say(f"    ? {name:<48} not in the sources folders")
            continue
        e = effective(cli, s["item_id"], ids["root"], cache)
        bad = e["trust"] == "retracted"
        cites.append(dict(name=name, org=s["org"], status="retracted" if bad else "ok", **e))
        say(f"    {'✗' if bad else '✓'} {name:<48} " + (f"retracted — {e['tags'].get('reason')} ({e['tags'].get('flagged_by')}, "
                                                        f"{e['tags'].get('flagged_on')}; tag on {e['where']}/)" if bad else f"{s['org']}, no retraction"))
    retraction = None
    if any(c["status"] == "retracted" for c in cites):
        q = story["flag"]["archive_query"]
        hits = coverage(cli, ids, q, top_k=3, echo=echo)["hits"]
        exp = story["flag"]["archive_story"]
        hit = next((h for h in hits if h["name"] == exp), None)
        retraction = dict(question=q, expected=exp, rank=hit["rank"] if hit else None, hits=hits)
        say(f"    the archive on it (“{q}”): " + (f"#{hit['rank']} {hit['published']} {exp}  ✓" if hit else f"✗ {exp} not in the top 3"))

    rules = [dict(id=f["id"], **(f.get("metadata") or {})) for f in style_facts(cli, ids, echo=echo)]
    flags = style_check(text, rules)
    say(f"\n  House style ({len(rules)} rules from the archive project's facts):")
    lines = text.splitlines()
    for f in flags:
        say(f"    ✗ line {f['line']:<3} {f['rule']:<26} “{f['match']}” → {f['fix']}")
        say(f"               {clip(CITE_SPAN.sub('', lines[f['line'] - 1]), 100)}")
    if not flags:
        say("    no house-style problems")
    published = ARCHIVE / story["late"]
    control = style_check(published.read_text(encoding="utf-8"), rules)
    say(f"    same rules on a published story ({published.name}): {len(control)} flag(s)"
        + ("  ✓ (the checks are not just noisy)" if not control else ""))

    bad_sources = [c["name"] for c in cites if c["status"] != "ok"]
    say(f"\n  Verdict: {len(bad_sources)} source(s) to replace · {len(flags)} style fix(es) · "
        f"{len(cov['hits'])} prior stories to read first")
    out = dict(file=path.name, coverage=cov, cites=cites, retraction=retraction, style=flags, control=len(control),
               bad_sources=bad_sources)
    emit("check", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    proj = find_project(cli, echo=not quiet)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)   # the index and the house-style facts on it
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=not quiet)
    if root:
        cli.run("lib", "delete", root["item_id"])   # the archive, the sources and every tag on them
    note("deleted the archive project and the publication's Library folder (with every file and tag in it)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one Library folder for the archive, one per source organisation, an archive project")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Archive — 55 published stories, tagged as they are uploaded; count them")
    inv = upload_archive(cli, ids, story)
    banner(4, TOTAL, "Index — import the folder into the archive project, then reconcile")
    idx = index(cli, ids, story)
    banner(5, TOTAL, "A late story — find exactly what the index is missing")
    late = late_arrival(cli, ids, story)
    banner(6, TOTAL, "Sources — a retraction flags a whole folder")
    fl = sources_and_flag(cli, ids, story)
    banner(7, TOTAL, "House style — rules and editor corrections stored as facts")
    st = house_style(cli, ids, story)
    banner(8, TOTAL, "A new writer's draft — prior coverage, sources, house style")
    ck = check(cli, ids, story)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   first_page=inv["first"], total=inv["total"], beyond=len(inv["beyond"]),
                   naive_missing=idx["reconcile"]["naive_missing"], false_alarms=idx["reconcile"]["false_alarms"],
                   real_missing=len(idx["reconcile"]["missing"]), import_s=idx["seconds"],
                   late_found=late["before"]["missing"], late_after=len(late["after"]["missing"]),
                   kids_own=fl["own"], retracted=len(fl["retracted"]), rules=len(st["rules"]),
                   coverage_page2=ck["coverage"]["page2"], retraction_rank=(ck["retraction"] or {}).get("rank"),
                   bad_sources=len(ck["bad_sources"]), style=len(ck["style"]), control=ck["control"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=False)
    if not root:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    archive = find_folder(cli, root["item_id"], "archive", echo=False)
    sources = find_folder(cli, root["item_id"], "sources", echo=False)
    proj = find_project(cli, echo=False)
    if not (archive and sources and proj):
        die("the demo data is incomplete; run `python3 demo.py` (or `python3 demo.py --reset`)")
    orgs = {}
    for org in dict.fromkeys(s["org"] for s in story["sources"]):
        f = find_folder(cli, sources["item_id"], org, echo=False)
        if f:
            orgs[org] = f["item_id"]
    return dict(root=root["item_id"], archive=archive["item_id"], sources=sources["item_id"], orgs=orgs, project=proj["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "reconcile", "trust", "check", "ask", "cleanup"],
                    help="run = full demo (default); reconcile = Library folder vs index, now; trust = every source and its "
                         "effective trust; check [FILE] = check a draft (default data/draft.md); ask \"TEXT\" = search the "
                         "archive; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="question for `ask`, or a draft file for `check`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns (signed URLs are masked)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "ask" and not args.arg:
        ap.error("ask needs a question, e.g. python3 demo.py ask \"What have we written about the ferry?\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py reconcile`, `python3 demo.py check`, "
              "`python3 demo.py ask \"What have we written about the ferry?\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    story = load_story()
    ids = need_ids(cli)
    if args.command == "reconcile":
        banner(2, 2, "Reconcile — the archive folder against the index")
        reconcile(cli, ids, "now")
    elif args.command == "trust":
        banner(2, 2, "Every source file and its effective trust")
        cache: dict = {}
        for s in all_sources(cli, ids):
            e = effective(cli, s["item_id"], ids["root"], cache)
            how = f"{e['trust']} (inherited from {e['where']}/)" if e["inherited"] else (e["trust"] or "(none)")
            say(f"    {s['org'] + '/' + s['name']:<62} {how}")
    elif args.command == "check":
        banner(2, 2, "Check a draft against the archive, the sources and the house style")
        check(cli, ids, story, Path(args.arg) if args.arg else None)
    elif args.command == "ask":
        banner(2, 2, "Ask the archive")
        show_coverage(coverage(cli, ids, args.arg, top_k=8))


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

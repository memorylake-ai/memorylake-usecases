#!/usr/bin/env python3
"""
Style guide memory for design teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Kestrel Studio (fictional) keeps its style guide, Kestrel UI, as six Markdown
files in one Library folder, imported into a project that every AI design tool
searches before it generates anything.  Version 2 ships, and three people put
their updated files back into the Library three different ways:

* `lib upload` with no flag — the server default is `rename`, so the new
  buttons.md lands next to the old one as buttons_1.md and is never indexed.
* `--on-conflict overwrite` — same Library item, new bytes.  Downloading the
  document returns version 2; searching it still returns version 1.
* `--on-conflict replace` — a new Library item; the project's document now
  points at a file that no longer exists (download: 404) and search keeps
  serving it.

The two obvious fixes do nothing (importing the file again is a "duplicate";
`proj doc reload` only takes failed documents).  A freshness audit finds every
stale, orphaned and unindexed document from the two listings, and the repair
re-imports exactly those — after which every answer matches the file it quotes.

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
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
V1 = DATA / "v1"
V2 = DATA / "v2"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-sgm"  # memorylake-usecases / style-guide-memory
ROOT_FOLDER = f"{PREFIX}-kestrel-style-guide"
GUIDE = "guide"
COPY_NAME = re.compile(r"^(?P<base>.+)_(?P<n>\d+)(?P<ext>\.[A-Za-z0-9]+)$")

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
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504",
                 "internal_error")   # a 500 right after creating a folder (listing it at once) has been seen once

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


def short(i: str | None) -> str:
    """inode-5823f2112b7e… / doc-c03badf1f08f… — enough to tell two ids apart on screen."""
    if not i:
        return "—"
    tail = i.split(":")[-1]
    return tail[:18] + "…"


def shown_path(p: Path) -> str:
    """The path relative to where you ran the demo (so the echoed command is the one you would type)."""
    rel = os.path.relpath(p)
    return str(p) if rel.startswith("..") else rel


def ts(s: str | None) -> datetime | None:
    if not s:
        return None
    # The server trims trailing zeros (".95953"); Python 3.9 only parses 3 or 6 fraction digits.
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def hhmmss(s: str | None) -> str:
    t = ts(s)
    return t.strftime("%H:%M:%S") if t else "—"


# --------------------------------------------------------------------------- reading

def pages(cli: CLI, args: list[str], size: int, echo: bool = True) -> list[dict]:
    """All pages of a list command; stops only when the token is null."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def files_in(cli: CLI, folder: str, echo: bool = True) -> list[dict]:
    return [i for i in pages(cli, ["lib", "list", folder], 50, echo=echo) if i.get("type") == "file"]


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    docs = pages(cli, ["proj", "doc", "list", "--project", project_id, "--workspace", cli.workspace], 100, echo=echo)
    for d in docs:
        if not d.get("created_at"):
            d.update(cli.run("proj", "doc", "get", d["id"], "--project", project_id, scoped=True, echo=False) or {})
    return docs


def find_project(cli: CLI, echo: bool = True):
    try:
        return cli.run("proj", "get", cid("guide"), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_folder(cli: CLI, parent: str, name: str, echo: bool = True):
    return next((i for i in pages(cli, ["lib", "list", parent], 50, echo=echo)
                 if i.get("type") in ("directory", "folder") and i["name"] == name), None)


def download(cli: CLI, project_id: str, doc_id: str, echo: bool = True) -> str | None:
    """The document's original file as the Library holds it now; None when that file is gone (404)."""
    args = ["proj", "doc", "download", "--project", project_id, "--workspace", cli.workspace, doc_id, "--output", "-"]
    for attempt in range(5):
        rc, out, err = cli.raw(*args, echo=echo and attempt == 0)
        if rc == 0:
            return out
        e = CLIError([cli.binary, *args], rc, out, err)
        if e.has("404", "not found"):
            return None
        if attempt == 4 or not e.has(*CLI.TRANSIENT):
            raise e
        time.sleep(2 * (attempt + 1))
    return None


def sentence(text: str | None, anchor: str) -> str | None:
    """The sentence that starts at `anchor` (the rule a question asks about), from a chunk or a file."""
    if not text:
        return None
    i = text.lower().find(anchor.lower())
    if i < 0:
        return None
    rest = text[i:].split("\n", 1)[0]
    j = rest.find(". ")
    return rest[: j + 1] if j >= 0 else rest.strip()


def chunk_text(hit: dict) -> str:
    return "\n".join(c.get("text") or "" for it in hit.get("items") or []
                     for c in (it.get("highlight") or {}).get("chunks") or [])


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    root, proj = find_folder(cli, "MY_SPACE", ROOT_FOLDER), find_project(cli)
    if root or proj:
        # The story moves the Library from version 1 to version 2 for good, so a second run starts over.
        note("--reset: deleting everything the demo created first" if reset else
             "found the data of an earlier run — this story starts at version 1, so it is removed first")
        cleanup(cli, quiet=True)
    elif reset:
        note("--reset: nothing to delete")
    root = create(cli, ["lib", "mkdir", ROOT_FOLDER, "--parent", "MY_SPACE", "--on-conflict", "deny"],
                  lambda: find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=False))
    guide = create(cli, ["lib", "mkdir", GUIDE, "--parent", root["item_id"], "--on-conflict", "deny"],
                   lambda: find_folder(cli, root["item_id"], GUIDE, echo=False))
    spec = story["project"]
    proj = create(cli, ["proj", "create", "--name", spec["name"], "--custom-id", cid("guide"),
                        "--description", spec["description"], "--workspace", cli.workspace],
                  lambda: find_project(cli, echo=False))
    ids = dict(root=root["item_id"], guide=guide["item_id"], project=proj["id"])
    say(f"\n  {story['studio']} — {story['system']}")
    say(f"    Library  {ROOT_FOLDER}/{GUIDE}/   the style guide, one Markdown file per topic")
    say(f"    project  {spec['name']}   the index every AI design tool searches → {proj['id']}")
    emit("setup", "", ids=ids, folder=f"{ROOT_FOLDER}/{GUIDE}", project=spec["name"])
    return ids


# --------------------------------------------------------------------------- uploads

def upload(cli: CLI, folder: str, path: Path, how: str | None = None, echo: bool = True) -> dict:
    """`lib upload`, with or without --on-conflict.  On a dropped connection, look before trying again."""
    args = ["lib", "upload", shown_path(path), "--parent", folder] + (["--on-conflict", how] if how else [])
    t0 = datetime.now(timezone.utc)
    for attempt in range(5):
        try:
            return cli.run(*args, echo=echo and attempt == 0)
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            note(f"network hiccup; checking whether {path.name} went through (retry {attempt + 1}/4)")
            time.sleep(2 * (attempt + 1))
            if how in ("overwrite", "replace", "deny"):
                continue   # sending it again ends in the same place
            stem, ext = path.stem, path.suffix
            found = [i for i in files_in(cli, folder, echo=False)
                     if (i["name"] == path.name or re.fullmatch(re.escape(stem) + r"_\d+" + re.escape(ext), i["name"]))
                     and (ts(i.get("created_at")) or t0) >= t0]
            if found:
                return found[0]
    return {}


def import_items(cli: CLI, project_id: str, item_ids: list[str], recursive: bool = False) -> dict:
    t0 = time.time()
    args = ["proj", "doc", "import", "--project", project_id, "--workspace", cli.workspace, "--wait", *item_ids]
    if recursive:
        args.append("--recursive")
    # Importing a file the project already holds is reported as a duplicate, so a retry is safe.
    res = create(cli, args, lambda: None) or {}
    failed = [d for d in res.get("details") or [] if d.get("result") not in ("success", "duplicate")]
    if failed:
        die(f"{len(failed)} file(s) failed to import: {', '.join(str(d.get('drive_item_id')) for d in failed)}")
    res["seconds"] = int(time.time() - t0)
    note(f"imported {res.get('success_count', 0)} · duplicates {res.get('duplicate_count', 0)}"
         f" · failed {res.get('failure_count', 0)} in {res['seconds']}s")
    return res


# --------------------------------------------------------------------------- step 3: version 1

def publish_v1(cli: CLI, ids: dict, story: dict) -> dict:
    say(f"\n  {story['v1_date']}: version 1 goes into the Library and the project")
    for n, name in enumerate(story["v1"]):
        upload(cli, ids["guide"], V1 / name, echo=n == 0)
        if n == 0 and len(story["v1"]) > 1:
            note(f"… the same command for the other {len(story['v1']) - 1} files (echo off)")
    imp = import_items(cli, ids["project"], [ids["guide"]], recursive=True)
    libs = {i["name"]: i for i in files_in(cli, ids["guide"])}
    docs = list_documents(cli, ids["project"])
    say("")
    say(f"    {'document':<18} {'doc id':<22} {'status':<8} Library item")
    for d in sorted(docs, key=lambda d: d["name"]):
        say(f"    {d['name']:<18} {short(d['id']):<22} {d.get('status', '?'):<8} {short((libs.get(d['name']) or {}).get('item_id'))}")
    v1_docs = {d["name"]: d["id"] for d in docs}
    out = dict(files=len(libs), docs=len(docs), okay=sum(d.get("status") == "okay" for d in docs),
               seconds=imp["seconds"], v1_docs=v1_docs)
    say(f"\n    {out['okay']} of {len(story['v1'])} documents okay")
    emit("v1", "", **out)
    return out


# --------------------------------------------------------------------------- steps 4/6/9: ask the guide

def answer(cli: CLI, ids: dict, q: dict, libs: list[dict] | None = None, echo: bool = True) -> dict:
    """Search the guide, then read the file the answer quotes — does the answer match the file as it is now?"""
    res = cli.run("search", q["ask"], "--projects", ids["project"], "--types", "document", "--top-k", "3",
                  scoped=True, echo=echo) or {}
    hits = res.get("documents") or []
    hit = next((h for h in hits if (h.get("document_name") or h.get("file_name")) == q["file"]), None)
    row = dict(question=q["ask"], file=q["file"], rank=None, doc=None, said=None, file_says=None,
               copies=[], verdict="missing", hits=[h.get("document_name") for h in hits])
    if hit:
        row.update(rank=hit.get("rank"), doc=hit.get("document_id"), said=sentence(chunk_text(hit), q["anchor"]))
        text = download(cli, ids["project"], row["doc"], echo=echo)
        if text is None:
            row["verdict"] = "gone"
        else:
            row["file_says"] = sentence(text, q["anchor"])
            row["verdict"] = "match" if row["said"] and row["said"] == row["file_says"] else "stale"
    stem, ext = os.path.splitext(q["file"])
    libs = libs if libs is not None else files_in(cli, ids["guide"], echo=False)
    row["copies"] = [i["name"] for i in libs if re.fullmatch(re.escape(stem) + r"_\d+" + re.escape(ext), i["name"])]
    if row["verdict"] == "match" and row["copies"]:
        row["verdict"] = "shadowed"
    row["version"] = next((v for v, val in (("v2", q["v2"]), ("v1", q["v1"])) if row["said"] and val in row["said"]), None)
    return row


VERDICT = {
    "match": "✓ the answer matches the file it quotes",
    "stale": "✗ the file says something else — the index still holds an older version",
    "gone": "✗ the file this answer quotes no longer exists (download: 404) — and search still serves it",
    "shadowed": "✗ matches its file — but a newer copy of that file sits next to it, never indexed",
    "missing": "✗ the expected document is not in the top 3",
}


def show_answer(r: dict) -> None:
    say(f"\n  Q: {r['question']}")
    if r["rank"] is None:
        say(f"    search      {r['file']} not in the top 3 ({', '.join(r['hits']) or 'no hits'})")
    else:
        say(f"    search      #{r['rank']} {r['file']} ({short(r['doc'])})  “{r['said'] or '(no sentence about it)'}”")
        say(f"    the file    " + ("404 — the Library file behind this document is gone" if r["verdict"] == "gone"
                                   else f"“{r['file_says'] or '(no sentence about it)'}”"))
    if r["copies"]:
        say(f"    Library     also holds {', '.join(r['copies'])}")
    say(f"    {VERDICT[r['verdict']]}")


def ask_all(cli: CLI, ids: dict, story: dict, label: str, echo: bool = True) -> dict:
    libs = files_in(cli, ids["guide"], echo=False)
    rows = []
    for n, q in enumerate(story["questions"]):
        r = answer(cli, ids, q, libs, echo=echo and n == 0)
        show_answer(r)
        rows.append(r)
    ok = sum(r["verdict"] == "match" for r in rows)
    v2 = sum(r["version"] == "v2" for r in rows)
    say(f"\n  {label}: {ok} of {len(rows)} answers match their file · {v2} of {len(rows)} quote version 2")
    out = dict(label=label, rows=rows, ok=ok, v2=v2)
    emit("answers", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: version 2 ships

def release_v2(cli: CLI, ids: dict, story: dict) -> dict:
    rows = []
    say(f"\n  {story['v2_date']}: version 2 ships")
    for r in story["release"]:
        say(f"\n  {r['who']} — {r['what']}")
        before = {i["name"]: i for i in files_in(cli, ids["guide"], echo=False)}
        old = before.get(r["file"])
        refused = None
        item = {}
        try:
            item = upload(cli, ids["guide"], V2 / r["file"], how=r["how"])
        except CLIError as e:
            if not e.has("409", "conflict"):
                raise
            m = re.search(r"\[([A-Z_]+)\]", e.stderr + e.stdout)
            refused = m.group(1) if m else "409"
        after = {i["name"]: i for i in files_in(cli, ids["guide"], echo=False)}
        if refused:
            what = f"refused — 409 {refused}; nothing changed, the script stops and asks a person"
        elif not old:
            what = f"new file {item.get('name')} → {short(item.get('item_id'))}"
        elif item.get("name") != r["file"]:
            what = f"stored as {item.get('name')} (new item {short(item.get('item_id'))}); {r['file']} is untouched"
        elif item.get("item_id") == old["item_id"]:
            now = after.get(r["file"], {})
            what = (f"same item {short(old['item_id'])}; size {old.get('size')} → {now.get('size')} bytes; "
                    f"updated_at {hhmmss(old.get('updated_at'))} → {hhmmss(now.get('updated_at'))}")
        else:
            gone = old["item_id"] not in {i["item_id"] for i in after.values()}
            what = (f"new item {short(item.get('item_id'))}; the old item {short(old['item_id'])} is "
                    + ("gone" if gone else "still there"))
        say(f"    → {what}")
        rows.append(dict(file=r["file"], who=r["who"], how=r["how"] or "default (rename)", result=what,
                         refused=refused, name=item.get("name"), item=item.get("item_id"),
                         old_item=(old or {}).get("item_id")))
    libs = files_in(cli, ids["guide"])
    docs = list_documents(cli, ids["project"])
    say(f"\n    the Library folder now: {', '.join(sorted(i['name'] for i in libs))}")
    say(f"    the project still lists {len(docs)} documents, all "
        + ("okay" if all(d.get("status") == "okay" for d in docs) else "(see statuses)")
        + " — nothing on either listing says an answer is out of date")
    out = dict(rows=rows, library=sorted(i["name"] for i in libs), docs=len(docs))
    emit("release", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: the obvious fixes

def obvious_fixes(cli: CLI, ids: dict, story: dict) -> dict:
    q = next(q for q in story["questions"] if q["file"] == story["obvious"])
    libs = {i["name"]: i for i in files_in(cli, ids["guide"], echo=False)}
    doc = next(d for d in list_documents(cli, ids["project"], echo=False) if d["name"] == q["file"])
    say(f"\n  Fix 1 — import {q['file']} into the project again:")
    res = cli.run("proj", "doc", "import", "--project", ids["project"], "--workspace", cli.workspace,
                  libs[q["file"]]["item_id"]) or {}
    d = (res.get("details") or [{}])[0]
    say(f"    result: {d.get('result')} · duplicate_count {res.get('duplicate_count')} · same document "
        f"{short(d.get('document_id'))} — nothing is processed again")
    say(f"\n  Fix 2 — reload the document:")
    reload_msg = None
    try:
        cli.run("proj", "doc", "reload", "--project", ids["project"], "--workspace", cli.workspace, doc["id"])
        reload_msg = "accepted"
    except CLIError as e:
        m = re.search(r'"message"\s*:\s*"([^"]+)"', e.stderr + e.stdout)
        code = re.search(r"\[([A-Z_]+)\]", e.stderr + e.stdout)
        reload_msg = f"{m.group(1) if m else str(e).splitlines()[0]} [{code.group(1) if code else e.rc}]"
    say(f"    refused: {reload_msg}")
    r = answer(cli, ids, q)
    show_answer(r)
    out = dict(file=q["file"], import_result=d.get("result"), duplicates=res.get("duplicate_count"),
               same_doc=d.get("document_id") == doc["id"], reload=reload_msg, after=r)
    emit("obvious", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: the freshness audit

def audit(cli: CLI, ids: dict, label: str, echo: bool = True) -> dict:
    """Every document against the Library file it was made from, and every Library file against the index."""
    libs = files_in(cli, ids["guide"], echo=echo)
    docs = list_documents(cli, ids["project"], echo=echo)
    by_name = {i["name"]: i for i in libs}
    rows = []
    for n, d in enumerate(sorted(docs, key=lambda d: d["name"])):
        text = download(cli, ids["project"], d["id"], echo=echo and n == 0)
        f = by_name.get(d["name"])
        row = dict(kind="document", name=d["name"], doc=d["id"], indexed=d.get("created_at"),
                   changed=(f or {}).get("updated_at"), item=(f or {}).get("item_id"))
        if text is None:
            row.update(status="ORPHAN", why="its Library file was replaced or deleted (download: 404); search still serves it",
                       changed=None, item=None)
        elif f and ts(f.get("updated_at")) and ts(d.get("created_at")) and ts(f["updated_at"]) > ts(d["created_at"]):
            row.update(status="STALE", why="the file changed after it was indexed; search serves the old text")
        else:
            row.update(status="FRESH", why="indexed after the file's last change")
        rows.append(row)
    status = {r["name"]: r["status"] for r in rows}
    for f in sorted(libs, key=lambda i: i["name"]):
        if f["name"] in status and status[f["name"]] != "ORPHAN":
            continue
        m = COPY_NAME.match(f["name"])
        base = m.group("base") + m.group("ext") if m else None
        row = dict(kind="file", name=f["name"], doc=None, indexed=None, changed=f.get("updated_at"), item=f["item_id"])
        if f["name"] in status:
            row.update(status="NOT INDEXED", why=f"replaced the orphaned {f['name']}; never imported")
        elif base and base in by_name:
            row.update(status="COPY", base=base, why=f"a newer {base}, stored under another name (the default upload renames)")
            for r in rows:
                if r["name"] == base and r["status"] == "FRESH":
                    r.update(status="SHADOWED", why=f"current for its file, but {f['name']} is newer and not indexed")
        else:
            row.update(status="NOT INDEXED", why="in the Library, never imported")
        rows.append(row)
    say(f"\n  Freshness audit ({label}) — {len(libs)} Library files · {len(docs)} documents")
    say(f"    {'':2}{'status':<12} {'name':<16} {'indexed':<9} {'changed':<9} why")
    for r in rows:
        mark = "✓" if r["status"] == "FRESH" else "✗"
        say(f"    {mark} {r['status']:<12} {r['name']:<16} {hhmmss(r['indexed']):<9} {hhmmss(r['changed']):<9} {r['why']}")
    counts: dict = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    say("    " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    out = dict(label=label, rows=rows, counts=counts, files=len(libs), docs=len(docs),
               clean=all(r["status"] == "FRESH" for r in rows) and len(libs) == len(docs))
    emit("audit", "", **out)
    return out


# --------------------------------------------------------------------------- step 9: repair

def repair(cli: CLI, ids: dict, story: dict, found: dict, v1_docs: dict) -> dict:
    promoted = []
    for c in (r for r in found["rows"] if r["status"] == "COPY"):
        # The Library has no rename or copy: index the copy, read its bytes back, put them under the right name.
        say(f"\n  Promote {c['name']} → {c['base']} (the Library has no rename, so its bytes go back under the right name)")
        res = import_items(cli, ids["project"], [c["item"]])
        doc = (res.get("details") or [{}])[0].get("document_id")
        text = download(cli, ids["project"], doc) or ""
        OUT.mkdir(exist_ok=True)
        tmp = OUT / c["base"]
        tmp.write_text(text, encoding="utf-8")
        upload(cli, ids["guide"], tmp, how="overwrite")
        cli.run("proj", "doc", "delete", "--project", ids["project"], "--workspace", cli.workspace, doc)
        cli.run("lib", "delete", c["item"])
        promoted.append(dict(copy=c["name"], base=c["base"]))
    now = audit(cli, ids, "after promoting copies", echo=False) if promoted else found
    drop = [r for r in now["rows"] if r["status"] in ("STALE", "ORPHAN")]
    say(f"\n  Remove the {len(drop)} stale/orphaned document(s) from the index, then import the current files:")
    for n, r in enumerate(drop):
        cli.run("proj", "doc", "delete", "--project", ids["project"], "--workspace", cli.workspace, r["doc"], echo=True)
    libs = {i["name"]: i for i in files_in(cli, ids["guide"], echo=False)}
    todo = sorted({r["name"] for r in drop} | {r["name"] for r in now["rows"] if r["status"] == "NOT INDEXED"})
    imp = import_items(cli, ids["project"], [libs[n]["item_id"] for n in todo if n in libs]) if todo else {}
    after = audit(cli, ids, "after the repair")
    docs = {d["name"]: d["id"] for d in list_documents(cli, ids["project"], echo=False)}
    controls = [dict(name=n, before=v1_docs.get(n), after=docs.get(n), same=v1_docs.get(n) == docs.get(n))
                for n in story["unchanged"]]
    say("\n  Files nobody touched keep their documents (the audit did not flag them, the repair did not touch them):")
    for c in controls:
        say(f"    {'✓' if c['same'] else '✗'} {c['name']:<16} {short(c['before'])} → {short(c['after'])}")
    out = dict(promoted=promoted, dropped=[r["name"] for r in drop], imported=todo,
               seconds=imp.get("seconds", 0), after=after, controls=controls)
    emit("repair", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    proj = find_project(cli, echo=not quiet)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)   # the index (documents only; this demo stores no facts)
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=not quiet)
    if root:
        cli.run("lib", "delete", root["item_id"])   # the style guide folder and every file in it
    note("deleted the style guide project and the Library folder (with every file in it)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — a Library folder for the style guide, a project for the index")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Version 1 — six files into the Library, imported into the project")
    v1 = publish_v1(cli, ids, story)
    banner(4, TOTAL, "Ask the guide — every answer checked against the file it quotes")
    a1 = ask_all(cli, ids, story, "version 1")
    banner(5, TOTAL, "Version 2 ships — three people, three ways to put a file back")
    rel = release_v2(cli, ids, story)
    banner(6, TOTAL, "Ask again — the same three questions")
    a2 = ask_all(cli, ids, story, "after the release")
    banner(7, TOTAL, "The two obvious fixes")
    ob = obvious_fixes(cli, ids, story)
    banner(8, TOTAL, "Freshness audit — every document against its file, every file against the index")
    au = audit(cli, ids, "after the release")
    banner(9, TOTAL, "Repair — re-import exactly what the audit found, then ask again")
    rp = repair(cli, ids, story, au, v1["v1_docs"])
    a3 = ask_all(cli, ids, story, "after the repair")
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   v1_import_s=v1["seconds"], v1_ok=a1["ok"], v1_v2=a1["v2"],
                   release=[r["result"][:40] for r in rel["rows"]],
                   after_release={r["file"]: r["verdict"] for r in a2["rows"]}, after_release_ok=a2["ok"],
                   obvious=dict(import_result=ob["import_result"], reload=ob["reload"], after=ob["after"]["verdict"]),
                   audit=au["counts"], repaired_clean=rp["after"]["clean"], repair_s=rp["seconds"],
                   controls_same=sum(c["same"] for c in rp["controls"]), final_ok=a3["ok"], final_v2=a3["v2"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=False)
    guide = find_folder(cli, root["item_id"], GUIDE, echo=False) if root else None
    proj = find_project(cli, echo=False)
    if not (root and guide and proj):
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(root=root["item_id"], guide=guide["item_id"], project=proj["id"])


def ask_free(cli: CLI, ids: dict, text: str, echo: bool = True) -> dict:
    """Any question: each hit, and whether every line it quotes is still in the file it came from."""
    res = cli.run("search", text, "--projects", ids["project"], "--types", "document", "--top-k", "3",
                  scoped=True, echo=echo) or {}
    rows = []
    say(f"\n  Q: {text}")
    for n, h in enumerate(res.get("documents") or []):
        lines = [ln.strip() for ln in chunk_text(h).splitlines() if ln.strip() and not ln.startswith("#")]
        body = download(cli, ids["project"], h.get("document_id"), echo=echo and n == 0)
        if body is None:
            verdict = "gone"
        else:
            have = {ln.strip() for ln in body.splitlines()}
            verdict = "match" if all(ln in have for ln in lines) else "stale"
        rows.append(dict(rank=h.get("rank"), name=h.get("document_name"), doc=h.get("document_id"),
                         excerpt=clip(" ".join(lines), 140), verdict=verdict))
        mark = {"match": "✓ current", "stale": "✗ the file has changed", "gone": "✗ file gone (404)"}[verdict]
        say(f"    #{h.get('rank')} {h.get('document_name'):<16} {mark}")
        say(f"        “{clip(' '.join(lines), 140)}”")
    out = dict(question=text, rows=rows)
    emit("ask", "", **out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "audit", "ask", "cleanup"],
                    help="run = full demo (default); audit = freshness audit now (reads only); "
                         "ask \"TEXT\" = search the guide and check each hit against its file; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="question for `ask`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns (signed URLs are masked)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "ask" and not args.arg:
        ap.error("ask needs a question, e.g. python3 demo.py ask \"What color is the primary button?\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py audit`, `python3 demo.py ask \"What color is the primary button?\"`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if args.command == "audit":
        banner(2, 2, "Freshness audit — every document against its file, every file against the index")
        audit(cli, ids, "now")
    elif args.command == "ask":
        banner(2, 2, "Ask the guide — and check each hit against its file")
        ask_free(cli, ids, args.arg)


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

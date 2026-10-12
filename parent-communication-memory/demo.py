#!/usr/bin/env python3
"""
Parent communication memory for schools — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Maplebrook Academy (fictional) keeps one Library folder per family.  Every
email, phone call, hallway conversation, parent-portal message and counselor
note about the Whitfields goes into that folder as a file, tagged when it is
uploaded with who logged it, on which channel, on what date, how sensitive it
is and whether someone still owes the family an answer (`lib upload --xattrs`).

The tags do the work:

* `sensitivity` decides which project a file is imported into — the teachers'
  project, or the counselor-only project.  A teacher's tools search the first;
  the counselor's search both.  A leak check compares the two.
* Before Wednesday's parent meeting, an audit reads the tags back with
  `lib list --xattr-keys …` — the default listing does not include them (it
  carries a signed thumbnail URL instead) — and reports which teacher has no
  logged contact with the family and which follow-ups are still open.
* After the meeting, follow-ups are closed by deleting the `follow_up` tag
  (`lib xattr delete`) and recording the outcome, and the audit is re-run.

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
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FILES = DATA / "whitfield"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-pcs"  # memorylake-usecases / parent-communication-memory (schools)
ROOT_FOLDER = f"{PREFIX}-maplebrook"   # one Library folder per school, one sub-folder per family

PRESIGNED = re.compile(r"https://[^\"\s]*X-Amz-[^\"\s]*")
HIDDEN = "<signed URL, not printed>"
HEADER = re.compile(r"^\s*(#|(From|To|Date|Logged by|With):)")   # the header lines of a logged communication


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
        # Read-only calls retry on a dropped connection, and so does `lib xattr` (it sets/removes the same keys again).
        retryable = any(str(a) in self.READ_ONLY for a in args[:3]) or tuple(map(str, args[:2])) == ("lib", "xattr")
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
            # Library listings carry pre-signed thumbnail URLs (with an access key id and a signature): never print them.
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


def days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def tag_keys(story: dict) -> list[str]:
    """Every custom tag key the story uses, in a stable order."""
    keys: list[str] = []
    for f in story["files"]:
        for k in f["tags"]:
            if k not in keys:
                keys.append(k)
    return keys


# --------------------------------------------------------------------------- reading

def pages(cli: CLI, args: list[str], size: int = 50, echo: bool = True) -> list[dict]:
    """All pages of a list command (each page is one call); stops only when the token is null."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_folder(cli: CLI, folder: str, keys: list[str] | None = None, echo: bool = True) -> list[dict]:
    args = ["lib", "list", folder]
    if keys:
        args += ["--xattr-keys", ",".join(keys)]
    return pages(cli, args, echo=echo)


def find(cli: CLI, kind: str, key: str, echo: bool = True):
    try:
        return cli.run(kind, "get", cid(key), "--by-custom-id", scoped=kind in ("proj", "conv"), echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_folder(cli: CLI, parent: str, name: str, echo: bool = True):
    return next((i for i in list_folder(cli, parent, echo=echo) if i.get("type") in ("directory", "folder") and i["name"] == name), None)


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["proj", "doc", "list", "--project", project_id, "--workspace", cli.workspace], echo=echo)


# --------------------------------------------------------------------------- step 2: setup

def ensure_folder(cli: CLI, parent: str, name: str, attrs: dict) -> str:
    folder = find_folder(cli, parent, name)
    if folder is None:
        folder = create(cli, ["lib", "mkdir", name, "--parent", parent, "--on-conflict", "deny", "--xattrs", attrs_json(attrs)],
                        lambda: find_folder(cli, parent, name, echo=False))
        note(f"created folder {name} → {folder['item_id']}")
    else:
        note(f"folder {name} already exists → {folder['item_id']}")
    return folder["item_id"]


def ensure_project(cli: CLI, key: str, spec: dict) -> str:
    proj = find(cli, "proj", key)
    if proj is None:
        proj = create(cli, ["proj", "create", "--name", spec["name"], "--custom-id", cid(key),
                            "--description", spec["description"], "--workspace", cli.workspace],
                      lambda: find(cli, "proj", key, echo=False))
        note(f"created project “{spec['name']}” → {proj['id']}")
    else:
        note(f"project “{spec['name']}” already exists → {proj['id']}")
    return proj["id"]


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created first")
        cleanup(cli, quiet=True)
    fam = story["family"]
    root = ensure_folder(cli, "MY_SPACE", ROOT_FOLDER, {"school": story["school"]})
    family = ensure_folder(cli, root, fam["key"], {"school": story["school"], "family": fam["key"]})
    ids = dict(root=root, family=family,
               staff=ensure_project(cli, "staff", story["projects"]["staff"]),
               counselor=ensure_project(cli, "counselor", story["projects"]["counselor"]))
    say(f"\n  {story['school']} · {fam['name']} ({fam['student']}, {fam['grade']})")
    say(f"    Library folder  {ROOT_FOLDER}/{fam['key']}/   one folder per family; every file in it is tagged")
    say(f"    teachers        {story['projects']['staff']['name']}")
    say(f"    counselor only  {story['projects']['counselor']['name']}")
    emit("setup", "", ids=ids, folder=f"{ROOT_FOLDER}/{fam['key']}", family=fam, staff=story["staff"],
         projects={k: v["name"] for k, v in story["projects"].items()})
    return ids


# --------------------------------------------------------------------------- step 3: capture

def capture(cli: CLI, ids: dict, story: dict) -> dict:
    keys = tag_keys(story)
    existing = {i["name"]: i for i in list_folder(cli, ids["family"]) if i.get("type") == "file"}
    uploaded, retagged = 0, 0
    for f in story["files"]:
        item = existing.get(f["file"])
        if item is None:
            item = create(cli, ["lib", "upload", shown_path(FILES / f["file"]), "--parent", ids["family"], "--xattrs", attrs_json(f["tags"])],
                          lambda: next((i for i in list_folder(cli, ids["family"], echo=False) if i["name"] == f["file"]), None))
            uploaded += 1
        else:
            # A second run starts the story over: tags back to how they were logged (open follow-ups, no outcome).
            cli.run("lib", "xattr", "set", item["item_id"], "--attrs", attrs_json(f["tags"]))
            stale = [k for k in ("follow_up", "resolved_on", "outcome") if k not in f["tags"]]
            cli.run("lib", "xattr", "delete", item["item_id"], "--keys", ",".join(stale))
            retagged += 1
        existing[f["file"]] = item
    note(f"{uploaded} file(s) uploaded with their tags, {retagged} already there (tags reset to the story's start)")

    say(f"\n  The default listing — what does `x_attrs` hold?")
    default = []
    for i in list_folder(cli, ids["family"]):
        if i.get("type") != "file":
            continue
        xa = i.get("x_attrs") or {}
        mine = [k for k in xa if k in keys]
        default.append(dict(name=i["name"], keys=sorted(xa), custom=mine,
                            signed=[k for k, v in xa.items() if isinstance(v, str) and PRESIGNED.search(v)]))
        shown = ", ".join(f"{k} = {HIDDEN}" if k in default[-1]["signed"] else k for k in sorted(xa)) or "(none)"
        say(f"    {i['name']:<34} {shown}")
    visible = sum(1 for d in default if d["custom"])
    say(f"\n  custom tags in the default listing: {visible} of {len(default)} files"
        + ("  ✓ (they are stored, just not returned unless asked for)" if visible == 0 else ""))

    say(f"\n  The same folder, asking for the tags by name (--xattr-keys {','.join(keys)}):")
    projected = []
    for i in list_folder(cli, ids["family"], keys):
        if i.get("type") != "file":
            continue
        tags = i.get("x_attrs") or {}
        projected.append(dict(name=i["name"], item_id=i["item_id"], tags=tags))
    projected.sort(key=lambda r: r["tags"].get("logged_on", ""))
    show_tag_table(projected, keys)
    complete = sum(1 for r in projected if all(r["tags"].get(k) == v for f in story["files"] if f["file"] == r["name"]
                                               for k, v in f["tags"].items()))
    signed_in_projection = sum(1 for r in projected for v in r["tags"].values() if PRESIGNED.search(str(v)))
    say(f"\n  {complete} of {len(story['files'])} files carry exactly the tags they were logged with"
        f" · signed URLs in the projection: {signed_in_projection}")
    emit("capture", "", default=default, projected=projected, keys=keys, uploaded=uploaded, retagged=retagged,
         visible=visible, complete=complete, signed_in_projection=signed_in_projection)
    return dict(default=default, projected=projected, visible=visible, complete=complete)


def show_tag_table(rows: list[dict], keys: list[str]) -> None:
    cols = [k for k in keys if k != "family"]
    widths = {k: max([len(k)] + [len(str(r["tags"].get(k, "") or "")) for r in rows]) for k in cols}
    say("    " + "  ".join(k.ljust(widths[k]) for k in cols))
    for r in rows:
        say("    " + "  ".join(str(r["tags"].get(k, "") or "·").ljust(widths[k]) for k in cols))


# --------------------------------------------------------------------------- step 4: route by tag

def route(cli: CLI, ids: dict, story: dict, projected: list[dict]) -> dict:
    target = {"staff": ids["staff"], "counselor": ids["counselor"]}
    plan: dict[str, list[dict]] = {"staff": [], "counselor": []}
    untagged = []
    for r in projected:
        s = r["tags"].get("sensitivity")
        (plan[s] if s in plan else untagged).append(r)
    for r in untagged:
        note(f"{r['name']} has no usable sensitivity tag — not imported anywhere until someone tags it")
    imported = {}
    for scope, rows in plan.items():
        have = {d["name"] for d in list_documents(cli, target[scope])}
        todo = [r for r in rows if r["name"] not in have]
        if todo:
            t0 = time.time()
            # Re-importing a file the project already holds is reported as a duplicate, so a retry is safe.
            res = create(cli, ["proj", "doc", "import", "--project", target[scope], "--workspace", cli.workspace, "--wait",
                               *[r["item_id"] for r in todo]], lambda: None)
            failed = [d for d in (res or {}).get("details") or [] if d.get("result") not in ("success", "duplicate")]
            if failed:
                die(f"{len(failed)} file(s) failed to import into the {scope} project")
            note(f"{len(todo)} file(s) imported into the {scope} project in {int(time.time() - t0)}s")
        else:
            note(f"the {scope} project already holds its {len(rows)} file(s)")
        imported[scope] = sorted(d["name"] for d in list_documents(cli, target[scope]))
    counselor_files = {r["name"] for r in plan["counselor"]}
    leaks = sorted(set(imported["staff"]) & counselor_files)
    removed = []
    if leaks:
        # Someone imported a counselor-tagged file into the teachers' project by hand: take it out again.
        docs = [d for d in list_documents(cli, target["staff"]) if d["name"] in counselor_files]
        cli.run("proj", "doc", "delete", "--project", target["staff"], "--workspace", cli.workspace, *[d["id"] for d in docs])
        removed = leaks
        note(f"removed {len(docs)} counselor-tagged document(s) from the teachers' project: {', '.join(leaks)}")
        imported["staff"] = sorted(d["name"] for d in list_documents(cli, target["staff"]))
        leaks = sorted(set(imported["staff"]) & counselor_files)
    stray = sorted(set(imported["counselor"]) - counselor_files)
    for scope in ("staff", "counselor"):
        say(f"\n  {story['projects'][scope]['name']} — {len(imported[scope])} document(s)")
        for n in imported[scope]:
            say(f"    {n}")
    if removed:
        say(f"\n  leak check found {len(removed)} misfiled: {', '.join(removed)} — removed from the teachers' project")
    say(f"\n  leak check: counselor-tagged files in the teachers' project: {len(leaks)}"
        + ("  ✓" if not leaks else "  ✗ " + ", ".join(leaks)))
    if stray:
        say(f"  (the counselor project also holds {len(stray)} file(s) not tagged counselor: {', '.join(stray)})")
    emit("route", "", plan={k: [r["name"] for r in v] for k, v in plan.items()}, imported=imported,
         leaks=leaks, removed=removed, untagged=[r["name"] for r in untagged])
    return dict(imported=imported, leaks=leaks, removed=removed)


# --------------------------------------------------------------------------- step 5: audit

def audit(cli: CLI, ids: dict, story: dict, as_of: str | None = None, echo: bool = True) -> dict:
    as_of = as_of or story["today"]
    keys = story["audit_keys"]
    rows = [dict(name=i["name"], item_id=i["item_id"], tags=i.get("x_attrs") or {})
            for i in list_folder(cli, ids["family"], keys, echo=echo) if i.get("type") == "file"]
    rows.sort(key=lambda r: r["tags"].get("logged_on", ""))
    window = story["audit_window_days"]
    roster = []
    for s in story["staff"]:
        mine = [r for r in rows if r["tags"].get("teacher") == s["name"]]
        last = max((r["tags"].get("logged_on", "") for r in mine), default="")
        age = days(last, as_of) if last else None
        flag = "no logged contact" if not mine else (f"last contact {age} days ago" if age > window else "")
        roster.append(dict(name=s["name"], role=s["role"], count=len(mine), last=last, age=age,
                           channels=sorted({r["tags"].get("channel", "?") for r in mine}), flag=flag))
    open_rows = [dict(name=r["name"], teacher=r["tags"].get("teacher", "?"), channel=r["tags"].get("channel", "?"),
                      logged_on=r["tags"].get("logged_on", ""), age=days(r["tags"]["logged_on"], as_of) if r["tags"].get("logged_on") else None)
                 for r in rows if r["tags"].get("follow_up") == "open"]
    closed_rows = [dict(name=r["name"], teacher=r["tags"].get("teacher", "?"), resolved_on=r["tags"]["resolved_on"],
                        outcome=r["tags"].get("outcome", ""))
                   for r in rows if r["tags"].get("resolved_on")]
    untagged = [r["name"] for r in rows if not r["tags"].get("teacher") or not r["tags"].get("logged_on")]
    say(f"\n  Who has talked to the {story['family']['name']} (as of {as_of}, window {window} days):")
    for p in roster:
        mark = "✗" if p["flag"] else "✓"
        say(f"    {mark} {p['name']:<14} {p['role']:<27} {p['count']} logged"
            + (f" · last {p['last']} · {', '.join(p['channels'])}" if p["count"] else "")
            + (f"  ← {p['flag']}" if p["flag"] else ""))
    say(f"\n  Open follow-ups: {len(open_rows)}")
    for o in open_rows:
        say(f"    • {o['name']:<34} {o['teacher']:<12} {o['channel']:<8} open {o['age']} days")
    if closed_rows:
        say(f"  Closed: {len(closed_rows)}")
        for c in closed_rows:
            say(f"    ✓ {c['name']:<34} {c['resolved_on']} — {c['outcome']}")
    if untagged:
        say(f"  Files missing teacher/date tags (not counted): {', '.join(untagged)}")
    out = dict(as_of=as_of, window=window, files=len(rows), roster=roster, open=open_rows, closed=closed_rows,
               untagged=untagged, gaps=[p["name"] for p in roster if p["flag"]])
    return out


# --------------------------------------------------------------------------- step 6: meeting prep

def search_scope(cli: CLI, ids: dict, scope: str, question: str, top_k: int = 5, echo: bool = True) -> list[dict]:
    projects = ids["staff"] if scope == "staff" else f"{ids['staff']},{ids['counselor']}"
    res = cli.run("search", question, "--projects", projects, "--types", "document", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    return res.get("documents") or []


def hit_view(h: dict, tags_by_name: dict[str, dict], terms: list[str]) -> dict:
    chunks = [c.get("text") or "" for it in h.get("items") or [] for c in (it.get("highlight") or {}).get("chunks") or []]
    body = [ln for c in chunks for ln in c.splitlines() if ln.strip() and not HEADER.match(ln)]
    blob = " ".join(chunks + [h.get("document_summary") or ""]).lower()
    name = h.get("document_name") or h.get("file_name") or ""
    tags = tags_by_name.get(name, {})
    return dict(rank=h.get("rank"), name=name, excerpt=clip(" ".join(body), 220) or clip(h.get("document_summary"), 220),
                teacher=tags.get("teacher", "?"), channel=tags.get("channel", "?"), logged_on=tags.get("logged_on", "?"),
                sensitivity=tags.get("sensitivity", "?"), sensitive=[t for t in terms if t.lower() in blob])


def show_hits(hits: list[dict], header: str) -> None:
    say(header)
    for h in hits:
        mark = "!" if h["sensitive"] else "·"
        say(f"    {mark} {h['rank']}. {h['name']}  [{h['channel']} · {h['teacher']} · {h['logged_on']} · {h['sensitivity']}]")
        say(f"         “{h['excerpt']}”")
    if not hits:
        say("    (no documents)")


def prep(cli: CLI, ids: dict, story: dict) -> dict:
    p = story["prep"]
    tags_by_name = {r["name"]: r["tags"] for r in (dict(name=i["name"], tags=i.get("x_attrs") or {})
                                                   for i in list_folder(cli, ids["family"], tag_keys(story)))}
    terms = p["sensitive_terms"]
    out = {}
    for scope, who in (("staff", p["asker"]), ("counselor", "Ms. Ibarra")):
        say(f"\n  {who} ({'teachers’ project' if scope == 'staff' else 'teachers’ + counselor projects'}) asks: “{p['question']}”")
        hits = [hit_view(h, tags_by_name, terms) for h in search_scope(cli, ids, scope, p["question"])]
        show_hits(hits, "")
        out[scope] = hits
    staff_sensitive = sum(1 for h in out["staff"] if h["sensitive"])
    coun_sensitive = [h for h in out["counselor"] if h["sensitive"]]
    say(f"\n  hits mentioning the home situation (“{'”, “'.join(terms)}”): teachers {staff_sensitive}"
        f" · counselor {len(coun_sensitive)}" + (f" (rank {', '.join(str(h['rank']) for h in coun_sensitive)})" if coun_sensitive else ""))
    say("  " + ("✓ the counselor's note stays out of the teachers' scope" if staff_sensitive == 0 else "✗ sensitive text reached the teachers' scope"))

    x = p["cross_teacher"]
    say(f"\n  {x['asker']} (English) asks the teachers' project: “{x['question']}”")
    cross = [hit_view(h, tags_by_name, terms) for h in search_scope(cli, ids, "staff", x["question"], top_k=3)]
    show_hits(cross, "")
    top = cross[0]["teacher"] if cross else None
    say(f"  top hit logged by {top or '—'}: " + ("✓ another teacher's thread, found from hers" if top == x["expect_teacher"]
                                                else f"✗ expected {x['expect_teacher']}"))
    emit("prep", "", question=p["question"], staff=out["staff"], counselor=out["counselor"], terms=terms,
         staff_sensitive=staff_sensitive, counselor_sensitive=[h["rank"] for h in coun_sensitive],
         cross=dict(question=x["question"], asker=x["asker"], hits=cross, expect=x["expect_teacher"], ok=top == x["expect_teacher"]))
    return dict(staff_sensitive=staff_sensitive, counselor_sensitive=len(coun_sensitive),
                counselor_rank=coun_sensitive[0]["rank"] if coun_sensitive else None, cross_ok=top == x["expect_teacher"])


# --------------------------------------------------------------------------- step 7: after the meeting

def after_meeting(cli: CLI, ids: dict, story: dict) -> dict:
    am = story["after_meeting"]
    items = {i["name"]: i for i in list_folder(cli, ids["family"]) if i.get("type") == "file"}
    for c in am["closed"]:
        item = items.get(c["file"])
        if item is None:
            die(f"{c['file']} is not in the family folder")
        cli.run("lib", "xattr", "delete", item["item_id"], "--keys", "follow_up")
        cli.run("lib", "xattr", "set", item["item_id"], "--attrs", attrs_json({"resolved_on": am["date"], "outcome": c["outcome"]}))
    note(f"closed {len(am['closed'])} follow-up(s): removed the follow_up tag, recorded resolved_on + outcome")
    after = audit(cli, ids, story, as_of=am["date"])
    return after


# --------------------------------------------------------------------------- ask

def ask(cli: CLI, ids: dict, text: str, scope: str = "staff", echo: bool = True) -> dict:
    story = load_story()
    tags_by_name = {i["name"]: i.get("x_attrs") or {} for i in list_folder(cli, ids["family"], tag_keys(story), echo=echo)}
    hits = [hit_view(h, tags_by_name, story["prep"]["sensitive_terms"]) for h in search_scope(cli, ids, scope, text, echo=echo)]
    return dict(question=text, scope=scope, hits=hits)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for key in ("staff", "counselor"):
        proj = find(cli, "proj", key, echo=not quiet)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=not quiet)
    if root:
        cli.run("lib", "delete", root["item_id"])   # deletes the family folder and every file in it
    note("deleted the two family projects and the school's Library folder (with every file and tag in it)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one Library folder per family, a teachers' project and a counselor-only project")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Capture — six communications, tagged as they are uploaded")
    cap = capture(cli, ids, story)
    banner(4, TOTAL, "Route by tag — sensitivity decides which project a file is searchable from")
    rt = route(cli, ids, story, cap["projected"])
    banner(5, TOTAL, f"Before the parent meeting ({story['meeting']}) — a completeness audit from the tags")
    before = audit(cli, ids, story)
    emit("audit", "", **before)
    banner(6, TOTAL, "Meeting prep — the same question from a teacher and from the counselor")
    pr = prep(cli, ids, story)
    banner(7, TOTAL, f"After the meeting ({story['after_meeting']['date']}) — close follow-ups, audit again")
    after = after_meeting(cli, ids, story)
    emit("after", "", **after)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   default_visible=f"{cap['visible']}/{len(cap['default'])}", tagged=f"{cap['complete']}/{len(story['files'])}",
                   imported={k: len(v) for k, v in rt["imported"].items()}, leaks=len(rt["leaks"]), removed=len(rt["removed"]),
                   gaps=before["gaps"], open_before=len(before["open"]), open_after=len(after["open"]),
                   staff_sensitive=pr["staff_sensitive"], counselor_sensitive=pr["counselor_sensitive"],
                   counselor_rank=pr["counselor_rank"], cross_ok=pr["cross_ok"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    root = find_folder(cli, "MY_SPACE", ROOT_FOLDER, echo=False)
    family = root and find_folder(cli, root["item_id"], story["family"]["key"], echo=False)
    staff, coun = find(cli, "proj", "staff", echo=False), find(cli, "proj", "counselor", echo=False)
    if not (family and staff and coun):
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(root=root["item_id"], family=family["item_id"], staff=staff["id"], counselor=coun["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "audit", "ask", "tags", "cleanup"],
                    help="run = full demo (default); audit = the completeness audit, now; ask \"TEXT\" = search as a "
                         "teacher (or --as counselor); tags = every file and its tags; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="question for `ask`")
    ap.add_argument("--as", dest="scope", choices=["staff", "counselor"], default="staff",
                    help="whose scope `ask` searches: staff = the teachers' project, counselor = both projects")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns (signed URLs are masked)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "ask" and not args.arg:
        ap.error("ask needs a question, e.g. python3 demo.py ask \"When is Leo away in November?\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py audit`, `python3 demo.py ask \"When is Leo away in November?\"` "
              "or `python3 demo.py ask \"How is Leo doing at home?\" --as counselor`; `python3 demo.py cleanup` removes the demo data.")
        return
    story = load_story()
    ids = need_ids(cli)
    if args.command == "audit":
        banner(2, 2, f"Completeness audit — the {story['family']['name']}, from the tags")
        audit(cli, ids, story)
    elif args.command == "tags":
        banner(2, 2, "Every file in the family folder, with its tags")
        keys = tag_keys(story) + [k for k in ("resolved_on", "outcome") if k not in tag_keys(story)]
        rows = [dict(name=i["name"], tags=i.get("x_attrs") or {}) for i in list_folder(cli, ids["family"], keys) if i.get("type") == "file"]
        rows.sort(key=lambda r: r["tags"].get("logged_on", ""))
        show_tag_table(rows, [k for k in keys if k != "outcome"])
    elif args.command == "ask":
        who = "the counselor (teachers' + counselor projects)" if args.scope == "counselor" else "a teacher (teachers' project)"
        banner(2, 2, f"Ask as {who}")
        r = ask(cli, ids, args.arg, args.scope)
        show_hits(r["hits"], f"\n  “{args.arg}”")


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

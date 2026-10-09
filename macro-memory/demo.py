#!/usr/bin/env python3
"""
Macro and template memory for a support team — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Gear (fictional) sells outdoor gear. Its support team has saved replies
(macros) in two help desks: 4 in Zendesk, 3 in Intercom. Support lead Priya Raman
moves them into one MemoryLake project, next to the support policy that is
supposed to be their source of truth. Each macro becomes one fact, with its name,
source tool and the policy sections it depends on in the fact's metadata.

MemoryLake's contradiction detector (`fact conflict`) checks every write:

  - m2d   a macro contradicts the policy document (it quotes the policy back);
  - self  a macro contradicts itself;
  - m2m   two macros — here, one from each tool — contradict each other.

Priya settles each with the strategy its category allows: edit_fact (rewrite
the broken macro in place), trust_document (forget the stale macro, publish
version 2), trust_fact (the macro is right, the policy is behind — the server
rewrites the macro to record that, so the approved wording is put back) and
dismiss (the m2m raised against wording that version 2 replaced, now `stale`).

Then the policy changes (v4: free shipping from 50 dollars, longer phone hours).
The detector checks what is written, when it is written — the new policy alone
flags nothing. Re-filing the macros that depend on the changed sections does:
the shipping macro is flagged against v4, fixed, and re-checked.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import csv
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
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-mtm"  # memorylake-usecases / macro-memory

PROJECT = dict(
    custom_id=f"{PREFIX}-support-kb",
    name="Larkspur Gear — support macros",
    description="Larkspur Gear's support policy and every saved reply (macro) from Zendesk and Intercom. Demo data.",
)

# The detector runs right after a fact is written to a quiet project. Back-to-back writes are
# checked later, as one batch, minutes later (measured: 7.5–8.5 min). So every macro write
# waits until the previous one is GAP seconds old.
GAP = 15
DETECT_WAIT = 60     # how long to wait for the detector after the last write (it usually answers in 10–20 s)
QUIET_WATCH = 45     # how long step 6 watches for conflicts after the new policy lands
REVERIFY_WAIT = 150  # step 7 re-files three macros in a row; its checks have landed up to ~110 s later


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


# --------------------------------------------------------------------------- data

def load_playbook() -> dict:
    return json.loads((DATA / "playbook.json").read_text(encoding="utf-8"))


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def load_macros() -> list[dict]:
    """The two help-desk exports, normalised: one dict per macro."""
    out = []
    zd = json.loads((DATA / "zendesk-macros.json").read_text(encoding="utf-8"))
    for m in zd["macros"]:
        out.append(dict(slug=slugify(m["title"]), title=m["title"], tool="Zendesk", source_id=str(m["id"]),
                        body=m["body"], depends_on=list(m.get("tags") or []), uses=m.get("usage_30d")))
    with (DATA / "intercom-macros.csv").open(encoding="utf-8", newline="") as fh:
        for m in csv.DictReader(fh):
            out.append(dict(slug=slugify(m["name"]), title=m["name"], tool="Intercom", source_id=m["macro_id"],
                            body=m["text"], depends_on=[t for t in m["topics"].split(";") if t],
                            uses=int(m["uses_last_30_days"])))
    return out


def macro_text(m: dict, body: str | None = None) -> str:
    """How a macro is stored: one fact, named, so the detector and search both see what it is."""
    return f"Macro '{m['title']}' ({m['tool']}): {body or m['body']}"


def lib_name(file: str) -> str:
    # Library names are global to the account; the prefix keeps the demo's files apart from yours.
    return f"{PREFIX}-{file}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def tail(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[-n:]


def wrap(s: str, indent: int, width: int = 96) -> str:
    """Word-wrap for the terminal; continuation lines line up under the first."""
    words, lines, cur = " ".join((s or "").split()).split(" "), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return ("\n" + " " * indent).join(lines)


SECTION = {"refunds": "Refunds", "shipping": "Shipping", "phone": "Phone support", "damaged": "Damaged items"}


def excerpt(chunk_text: str, depends_on: list[str]) -> str:
    """The policy section a macro depends on, taken from the conflict's document excerpt."""
    text = chunk_text or ""
    for topic in depends_on:
        head = SECTION.get(topic)
        m = re.search(rf"##\s*{re.escape(head)}\s*\n(.+?)(?:\n\s*##|\Z)", text, re.S) if head else None
        if m:
            return f"{head}: " + " ".join(m.group(1).split())
    return clip(text.replace("#", ""), 220)


# --------------------------------------------------------------------------- pacing

class Pace:
    """Keeps writes to the support KB GAP seconds apart, so the detector checks each one as it lands."""

    def __init__(self) -> None:
        self.last = 0.0

    def wait(self) -> None:
        left = GAP - (time.time() - self.last)
        if left > 0:
            time.sleep(left)

    def wrote(self) -> None:
        self.last = time.time()


PACE = Pace()


# --------------------------------------------------------------------------- MemoryLake reads

def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def _pages(cli: CLI, args: list[str], key: str = "items", echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        items.extend(page.get(key) or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "list", "--projects", project_id, "--page-size", "100"], echo=echo)


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    page = cli.run("proj", "doc", "list", "--project", project_id, scoped=True, echo=echo) or {}
    return page.get("items") or []


def list_conflicts(cli: CLI, project_id: str, *extra: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "conflict", "list", "--project", project_id, "--page-size", "100", *extra], echo=echo)


def live_macros(cli: CLI, project_id: str, echo: bool = True) -> dict[str, dict]:
    """slug → the macro's current fact (the one not forgotten), read from `fact list`."""
    out: dict[str, dict] = {}
    for f in list_facts(cli, project_id, echo=echo):
        meta = f.get("metadata") or {}
        if meta.get("macro") and not f.get("expired"):
            cur = out.get(meta["macro"])
            if cur is None or int(meta.get("version", 1)) >= int((cur.get("metadata") or {}).get("version", 1)):
                out[meta["macro"]] = f
    return out


def macro_of(fact_id: str, c: dict, by_id: dict[str, str]) -> str:
    """The macro a fact belongs to: from its metadata, or — once it is forgotten or deleted — from its snapshot text."""
    if fact_id in by_id:
        return by_id[fact_id]
    snap = next((s.get("fact_text") or "" for s in c.get("fact_snapshots") or [] if s.get("fact_id") == fact_id), "")
    m = re.match(r"Macro '([^']+)'", snap)
    return slugify(m.group(1)) if m else "?"


def view(c: dict, by_id: dict[str, str]) -> dict:
    """A conflict, with each fact id mapped back to the macro it belongs to."""
    return dict(id=c["id"], name=c.get("name"), description=c.get("description"), category=c.get("category"),
                conflict_type=c.get("conflict_type"), resolved=bool(c.get("resolved")), stale=bool(c.get("stale")),
                fact_ids=c.get("fact_ids") or [], macros=[macro_of(i, c, by_id) for i in c.get("fact_ids") or []],
                facts=[dict(id=s.get("fact_id"), text=s.get("fact_text")) for s in c.get("fact_snapshots") or []],
                chunks=[dict(document=k.get("document_name"), text=(k.get("text") or "").strip())
                        for k in c.get("file_chunks") or []],
                created_at=c.get("created_at"), resolve=c.get("resolve"))


def id_map(cli: CLI, project_id: str) -> dict[str, str]:
    """fact id → macro slug, for every macro fact ever written (forgotten ones included)."""
    return {f["id"]: (f.get("metadata") or {}).get("macro", "?") for f in list_facts(cli, project_id, echo=False)}


# --------------------------------------------------------------------------- step 2: support KB + policy

def ensure_project(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    return proj["id"]


def import_policy(cli: CLI, project_id: str, file: str) -> dict:
    """Upload one policy version to the Library and import it into the support KB."""
    OUT.mkdir(exist_ok=True)
    staged = OUT / lib_name(file)
    shutil.copyfile(DATA / file, staged)
    item = cli.run("lib", "upload", os.path.relpath(staged), "--on-conflict", "overwrite")
    t0 = time.time()
    cli.run("proj", "doc", "import", "--project", project_id, item["item_id"], "--wait", scoped=True)
    note(f"imported {item['name']} into the support KB ({int(time.time() - t0)}s)")
    return next(d for d in list_documents(cli, project_id, echo=False) if d.get("name") == lib_name(file))


def setup_kb(cli: CLI, pb: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting the support KB and the uploaded policies, then starting over")
        cleanup(cli, quiet=True)
    project_id = ensure_project(cli)
    docs = {d.get("name"): d for d in list_documents(cli, project_id)}
    if lib_name(pb["policy"]["next"]) in docs:
        note(f"{lib_name(pb['policy']['next'])} is already the policy of record (step 6 ran before)")
        policy = docs[lib_name(pb["policy"]["next"])]
    elif lib_name(pb["policy"]["current"]) in docs:
        note(f"{lib_name(pb['policy']['current'])} is already in the support KB")
        policy = docs[lib_name(pb["policy"]["current"])]
    else:
        policy = import_policy(cli, project_id, pb["policy"]["current"])
    text = (DATA / pb["policy"]["current"]).read_text(encoding="utf-8")
    emit("policy", "", project=dict(id=project_id, name=PROJECT["name"]),
         document=dict(id=policy.get("id"), name=policy.get("name")), policy_text=text)
    return dict(project=project_id, policy=policy.get("name"))


# --------------------------------------------------------------------------- step 3: macros

def add_macro(cli: CLI, project_id: str, m: dict, body: str, version: int, against: str, **extra) -> dict:
    """One macro version = one fact. Metadata carries the name, the source tool and what it depends on."""
    PACE.wait()
    res = cli.run("fact", "add", "--project", project_id, macro_text(m, body), scoped=True) or {}
    PACE.wrote()
    fact = (res.get("facts") or [{}])[0]
    meta = dict(macro=m["slug"], title=m["title"], tool=m["tool"], source_id=m["source_id"],
                depends_on=m["depends_on"], version=version, checked_against=against, **extra)
    cli.run("fact", "update", fact["id"], "--project", project_id, "--metadata", json.dumps(meta), scoped=True)
    return dict(fact, metadata=meta)


def import_macros(cli: CLI, ids: dict, pb: dict) -> dict[str, dict]:
    project_id = ids["project"]
    macros = load_macros()
    have = live_macros(cli, project_id)
    todo = [m for m in macros if m["slug"] not in have]
    if todo:
        note(f"{len(todo)} macro(s) to import ({sum(m['tool'] == 'Zendesk' for m in todo)} from Zendesk, "
             f"{sum(m['tool'] == 'Intercom' for m in todo)} from Intercom), one every {GAP}s — back-to-back writes "
             "are checked for contradictions minutes later, as a batch; one at a time, each is checked as it lands")
    else:
        note("all 7 macros are already in the support KB")
    todo_slugs = {m["slug"] for m in todo}
    for m in macros:
        if m["slug"] in todo_slugs:
            have[m["slug"]] = add_macro(cli, project_id, m, m["body"], 1, ids["policy"])
        f = have[m["slug"]]
        emit("macro", "", slug=m["slug"], title=m["title"], tool=m["tool"], source_id=m["source_id"],
             depends_on=m["depends_on"], uses=m["uses"], fact_id=f["id"], fact_text=f.get("fact"),
             version=(f.get("metadata") or {}).get("version", 1))
    rows = [f"  {m['tool']:<9} {m['title']:<30} → {have[m['slug']]['id']}  depends on: {', '.join(m['depends_on'])}"
            for m in macros]
    emit("text", "\n  Macro library (one fact per macro; name, tool and dependencies in its metadata):\n" + "\n".join(rows))
    ids["last_write"] = PACE.last
    return have


# --------------------------------------------------------------------------- step 4: detector report

def wait_for(cli: CLI, project_id: str, want: list[tuple[str, str]], since_fact_ids: set[str] | None = None,
             timeout: int = DETECT_WAIT) -> list[dict]:
    """Poll `fact conflict list` until every (macro, category) in `want` has a conflict, or time runs out."""
    t0 = time.time()
    first = True
    while True:
        by_id = id_map(cli, project_id)
        cur = [view(c, by_id) for c in list_conflicts(cli, project_id, echo=first)]
        first = False
        if since_fact_ids is not None:
            cur = [c for c in cur if set(c["fact_ids"]) & since_fact_ids]
        got = {(s, c["category"]) for c in cur for s in c["macros"]}
        if all(w in got for w in want) or time.time() - t0 > timeout:
            return cur
        emit("progress", f"  … waiting for the detector ({int(time.time() - t0)}s)", waited=int(time.time() - t0))
        time.sleep(5)


def detector_report(cli: CLI, ids: dict, pb: dict, have: dict[str, dict]) -> list[dict]:
    project_id = ids["project"]
    plan = pb["macros"]
    want = [(slug, cat) for slug, p in plan.items() for cat in p.get("expect") or []]
    on_v4 = ids["policy"] == lib_name(pb["policy"]["next"])
    expect = {slug: set(p.get("expect") or []) | ({p["after_v4"]} if on_v4 and p.get("after_v4") else set())
              for slug, p in plan.items()}
    if on_v4:
        note("this support KB already ran the whole story — showing every conflict the detector raised, resolved ones included")
    left = DETECT_WAIT - (time.time() - (ids.get("last_write") or 0))
    conflicts = wait_for(cli, project_id, want, timeout=max(int(left), 10))
    macros = load_macros()
    lines = []
    for m in macros:
        mine = [c for c in conflicts if m["slug"] in c["macros"]]
        exp = expect.get(m["slug"], set())
        got = {c["category"] for c in mine}
        mark = "✓ clean" if not mine else "✗ " + ", ".join(sorted(got))
        ok = "as planned" if got == exp else f"planned: {', '.join(sorted(exp)) or 'clean'}"
        lines.append(f"\n  {mark:<12} {m['title']} ({m['tool']})   [{ok}]")
        for c in mine:
            others = [s for s in c["macros"] if s != m["slug"]]
            lines.append(f"      {c['category']} · {c['name']}   {c['id']}" + ("   (resolved)" if c["resolved"] else ""))
            lines.append(f"        {clip(c['description'], 150)}")
            if others:
                lines.append(f"        with: {', '.join(others)}")
            for k in c["chunks"]:
                lines.append(f"        policy ({k['document']}): \"{clip(excerpt(k['text'], m['depends_on']), 150)}\"")
    planned = sum(1 for slug, cat in want if any(slug in c["macros"] and c["category"] == cat for c in conflicts))
    extra = [c for c in conflicts if not all(c["category"] in expect.get(s, set()) for s in c["macros"])]
    lines.append(f"\n  Detector: {len(conflicts)} conflict(s) on 7 macros — {planned}/{len(want)} planned (macro, category) pairs raised"
                 + (f", {len(extra)} not in the plan (shown above, left open for review)" if extra else ""))
    if planned < len(want):
        lines.append(f"  (not raised within {DETECT_WAIT}s — the detector may have deferred them; `python3 demo.py conflicts` later)")
    emit("text", "\n".join(lines))
    emit("detector", "", conflicts=conflicts, planned=planned, want=len(want), extra=[c["id"] for c in extra])
    return conflicts


# --------------------------------------------------------------------------- step 5: triage

def open_conflict(cli: CLI, project_id: str, slug: str, category: str, echo: bool = False) -> dict | None:
    by_id = id_map(cli, project_id)
    for c in list_conflicts(cli, project_id, "--resolved", "false", "--category", category, echo=echo):
        v = view(c, by_id)
        if slug in v["macros"]:
            return v
    return None


def resolve(cli: CLI, project_id: str, conflict_id: str, *args: str) -> dict:
    return cli.run("fact", "conflict", "resolve", conflict_id, "--project", project_id, *args, scoped=True) or {}


def triage(cli: CLI, ids: dict, pb: dict) -> list[dict]:
    project_id = ids["project"]
    plan = pb["macros"]
    macros = {m["slug"]: m for m in load_macros()}
    done: list[dict] = []

    def settled(item: dict) -> None:
        done.append(item)
        emit("settled", "", **item)

    # 1. self → edit_fact: the macro contradicts itself; replace its text in place.
    slug = "password-reset"
    c = open_conflict(cli, project_id, slug, "self", echo=True)
    emit("text", f"\n  [self → edit_fact] {macros[slug]['title']}: {plan[slug]['why']}")
    if c:
        new = macro_text(macros[slug], plan[slug]["fixed"])
        r = resolve(cli, project_id, c["id"], "--strategy", "edit_fact", "--edit", f"{c['fact_ids'][0]}={new}")
        f = next(x for x in list_facts(cli, project_id, echo=False) if x["id"] == c["fact_ids"][0])
        emit("text", f"    updated {', '.join(r.get('updated_fact_ids') or [])} in place (same fact id)\n"
                     f"    before: {wrap(c['facts'][0]['text'].split('): ', 1)[-1], 12)}\n"
                     f"    after:  {wrap(f['fact'].split('): ', 1)[-1], 12)}\n"
                     f"    {'✓' if f['fact'] == new else '✗'} the text is exactly the replacement we sent")
        settled(dict(macro=slug, conflict=c["id"], category="self", name=c["name"], strategy="edit_fact", ok=f["fact"] == new,
                     why=plan[slug]["why"], before=c["facts"][0]["text"], after=f["fact"], fact_id=f["id"]))
    else:
        note("no open `self` conflict on this macro (resolved by an earlier run, or not raised)")

    # 2. m2d → trust_document: the macro is stale; forget it and publish version 2.
    slug = "refund-request"
    emit("text", f"\n  [m2d → trust_document] {macros[slug]['title']}: {plan[slug]['why']}")
    c = open_conflict(cli, project_id, slug, "m2d", echo=True)
    if c:
        r = resolve(cli, project_id, c["id"], "--strategy", "trust_document")
        emit("text", f"    forgotten: {', '.join(r.get('forgotten_fact_ids') or []) or 'none'} (version 1)")
        v2 = add_macro(cli, project_id, macros[slug], plan[slug]["v2"], 2, ids["policy"], replaces=c["fact_ids"][0])
        emit("text", f"    published version 2 → {v2['id']}: {clip(v2['fact'], 120)}")
        new = wait_for(cli, project_id, [], since_fact_ids={v2["id"]}, timeout=25)
        emit("text", f"    {'✓' if not new else '✗'} the detector raised {len(new)} conflict(s) on version 2")
        settled(dict(macro=slug, conflict=c["id"], category="m2d", name=c["name"], strategy="trust_document", ok=not new,
                     why=plan[slug]["why"], before=c["facts"][0]["text"], forgotten=r.get("forgotten_fact_ids") or [],
                     after=v2["fact"], fact_id=v2["id"], chunks=c["chunks"]))
    else:
        note("no open `m2d` conflict on this macro (resolved by an earlier run, or not raised)")

    # 3. m2d → trust_fact: the macro is right and the policy document is behind.
    slug = "call-me-back"
    emit("text", f"\n  [m2d → trust_fact] {macros[slug]['title']}: {plan[slug]['why']}")
    c = open_conflict(cli, project_id, slug, "m2d", echo=True)
    if c:
        before = c["facts"][0]["text"]
        resolve(cli, project_id, c["id"], "--strategy", "trust_fact")
        g = cli.run("fact", "conflict", "get", c["id"], "--project", project_id, scoped=True) or {}
        rewritten = next((u.get("new_fact_text") for u in (g.get("resolve") or {}).get("updated_facts") or []), None)
        if rewritten and rewritten != before:
            emit("text", f"    the fact is kept — but the server rewrote it to record the decision:\n"
                         f"      before: {wrap(before.split('): ', 1)[-1], 14)}\n"
                         f"      after:  {wrap(rewritten.split('): ', 1)[-1], 14)}\n"
                         f"    A macro is pasted into replies word for word, so put the approved wording back:")
            meta = dict(next(x for x in list_facts(cli, project_id, echo=False) if x["id"] == c["fact_ids"][0]).get("metadata") or {})
            meta["approved_exception"] = "extended phone hours approved 2026-10-06, ahead of policy v4"
            cli.run("fact", "update", c["fact_ids"][0], "--project", project_id, "--text", before,
                    "--metadata", json.dumps(meta), scoped=True)
        else:
            emit("text", "    the fact is kept, text unchanged")
        tr = cli.run("fact", "trace", c["fact_ids"][0], "--project", project_id, scoped=True) or {}
        events = list(reversed(tr.get("trace") or []))
        hist = [f"      {e.get('event'):<6} {str(e.get('timestamp'))[:19]}Z  {e.get('source_kind')}: …{tail(e.get('new_fact'), 80)}"
                for e in events]
        final = (tr.get("fact") or {}).get("fact")
        emit("text", "    `fact trace` — every version of this macro:\n" + "\n".join(hist)
             + f"\n    {'✓' if final == before else '✗'} the macro reads exactly as approved")
        settled(dict(macro=slug, conflict=c["id"], category="m2d", name=c["name"], strategy="trust_fact", ok=final == before,
                     why=plan[slug]["why"], before=before, rewritten=rewritten, after=final, fact_id=c["fact_ids"][0],
                     trace=events, chunks=c["chunks"]))
    else:
        note("no open `m2d` conflict on this macro (resolved by an earlier run, or not raised)")

    # 4. The same macro raised two conflicts. Fix the root cause once (m2d → trust_document, version 2);
    #    the m2m that shared the old wording is then stale, and is closed with dismiss (which changes no fact).
    slug, other = "damaged-item", plan["damaged-item"]["with"]
    emit("text", f"\n  [m2d → trust_document] {macros[slug]['title']}: {plan[slug]['why']}")
    c = open_conflict(cli, project_id, slug, "m2d", echo=True)
    if c:
        r = resolve(cli, project_id, c["id"], "--strategy", "trust_document")
        emit("text", f"    forgotten: {', '.join(r.get('forgotten_fact_ids') or []) or 'none'} (version 1)")
        v2 = add_macro(cli, project_id, macros[slug], plan[slug]["v2"], 2, ids["policy"], replaces=c["fact_ids"][0])
        emit("text", f"    published version 2 → {v2['id']}: {clip(v2['fact'], 120)}")
        new = wait_for(cli, project_id, [], since_fact_ids={v2["id"]}, timeout=25)
        emit("text", f"    {'✓' if not new else '✗'} the detector raised {len(new)} conflict(s) on version 2")
        settled(dict(macro=slug, conflict=c["id"], category="m2d", name=c["name"], strategy="trust_document", ok=not new,
                     why=plan[slug]["why"], before=c["facts"][0]["text"], forgotten=r.get("forgotten_fact_ids") or [],
                     after=v2["fact"], fact_id=v2["id"], chunks=c["chunks"]))
    else:
        note("no open `m2d` conflict on this macro (resolved by an earlier run, or not raised)")
    emit("text", f"\n  [m2m → dismiss] {macros[slug]['title']} vs {macros[other]['title']}: raised against the old wording")
    m2m, t0, first = None, time.time(), True
    while True:
        by_id = id_map(cli, project_id)
        # Wait for the server to mark it stale, then pick it up from the stale list itself.
        cur = [view(x, by_id) for x in list_conflicts(cli, project_id, "--resolved", "false", "--stale", "true",
                                                         "--category", "m2m", echo=first)]
        first = False
        m2m = next((x for x in cur if slug in x["macros"] and other in x["macros"]), None)
        if m2m or time.time() - t0 > 30:
            break
        emit("progress", f"  … waiting for the server to mark it stale ({int(time.time() - t0)}s)", waited=int(time.time() - t0))
        time.sleep(5)
    if m2m is None:
        by_id = id_map(cli, project_id)
        m2m = next((x for x in (view(y, by_id) for y in list_conflicts(cli, project_id, "--resolved", "false", "--category", "m2m"))
                    if slug in x["macros"] and other in x["macros"]), None)
    if m2m:
        emit("text", f"    {m2m['id']}  stale: {str(m2m['stale']).lower()}"
                     + (" — a macro in it changed after it was raised (`fact conflict list --stale true` finds these)"
                        if m2m["stale"] else " — the server has not marked it stale yet"))
        r = resolve(cli, project_id, m2m["id"], "--strategy", "dismiss")
        changed = len(r.get("updated_fact_ids") or []) + len(r.get("forgotten_fact_ids") or [])
        emit("text", f"    dismissed — facts changed: {changed}; the Trailhead Plus macro stays exactly as it is")
        settled(dict(macro=slug, conflict=m2m["id"], category="m2m", name=m2m["name"], strategy="dismiss", ok=changed == 0,
                     why="raised against the old wording, which version 2 replaced", stale=m2m["stale"],
                     facts=m2m["facts"], with_macro=other))
    else:
        note("no open `m2m` conflict between the two damaged-item macros (resolved earlier, or not raised)")

    by_id = id_map(cli, project_id)
    still = [view(c, by_id) for c in list_conflicts(cli, project_id, "--resolved", "false")]
    stale = [c for c in still if c["stale"]]
    emit("text", f"\n  Open after triage: {len(still)}"
         + "".join(f"\n    {c['id']}  [{c['category']}{' · stale' if c['stale'] else ''}] {c['name']} ({', '.join(c['macros'])})" for c in still)
         + ("\n  `stale` = a macro in it changed after the detector raised it (here: by the fixes above) — re-read before acting."
            if stale else ""))
    emit("triage", "", resolved=done, open=still)
    return done


# --------------------------------------------------------------------------- steps 6–7: the policy changes

def swap_policy(cli: CLI, ids: dict, pb: dict) -> dict:
    project_id = ids["project"]
    cur, nxt = lib_name(pb["policy"]["current"]), lib_name(pb["policy"]["next"])
    docs = {d.get("name"): d for d in list_documents(cli, project_id)}
    if nxt in docs:
        note(f"{nxt} is already the policy of record")
        ids["policy"] = nxt
        emit("swap", "", removed=cur, added=nxt, new_conflicts=[], watched=0, already=True)
        return dict(new_conflicts=[], already=True)
    if cur in docs:
        cli.run("proj", "doc", "delete", "--project", project_id, docs[cur]["id"], scoped=True)
        note(f"removed {cur} from the support KB (v4 replaces it)")
    import_policy(cli, project_id, pb["policy"]["next"])
    ids["policy"] = nxt
    before = {c["id"] for c in list_conflicts(cli, project_id, echo=False)}
    t0 = time.time()
    new: list[dict] = []
    first = True
    while time.time() - t0 < QUIET_WATCH:
        time.sleep(5)
        by_id = id_map(cli, project_id)
        new = [view(c, by_id) for c in list_conflicts(cli, project_id, echo=first) if c["id"] not in before]
        first = False
        if new:
            break
        emit("progress", f"  … watching for new conflicts ({int(time.time() - t0)}s)", waited=int(time.time() - t0))
    stale_by_text = [m for m in load_macros() if set(m["depends_on"]) & set(pb["policy"]["changed_sections"])]
    emit("text", f"\n  New conflicts in {int(time.time() - t0)}s after the new policy landed: {len(new)}"
         + ("\n  The detector checks what is written, when it is written. Macros already in the KB are not"
            "\n  re-checked against a new document — so the old shipping threshold sits there, unflagged."
            if not new else "")
         + f"\n  Macros that depend on a changed section ({', '.join(pb['policy']['changed_sections'])}): "
         + ", ".join(m["title"] for m in stale_by_text))
    emit("swap", "", removed=cur, added=nxt, new_conflicts=new, watched=int(time.time() - t0), already=False)
    return dict(new_conflicts=new, already=False)


def reverify(cli: CLI, ids: dict, pb: dict) -> dict:
    """Re-file every macro that depends on a changed section, so the detector checks it against v4."""
    project_id = ids["project"]
    changed = set(pb["policy"]["changed_sections"])
    macros = [m for m in load_macros() if set(m["depends_on"]) & changed]
    have = live_macros(cli, project_id)
    refiled: dict[str, dict] = {}
    did_refile = False
    for m in macros:
        f = have.get(m["slug"])
        meta = (f or {}).get("metadata") or {}
        if f and meta.get("checked_against") == ids["policy"]:
            note(f"{m['title']} was already re-checked against {ids['policy']}")
            refiled[m["slug"]] = f
            continue
        body = f["fact"].split("): ", 1)[1] if f else m["body"]
        extra = {k: v for k, v in meta.items() if k not in ("macro", "title", "tool", "source_id", "depends_on", "version", "checked_against")}
        new = add_macro(cli, project_id, m, body, int(meta.get("version", 1)), ids["policy"], refiled_from=f["id"] if f else None, **extra)
        did_refile = True
        if f:
            cli.run("fact", "delete", f["id"], "--project", project_id, scoped=True)
        refiled[m["slug"]] = new
        emit("text", f"  re-filed {m['title']} v{new['metadata']['version']} → {new['id']}" + (f" (replaces {f['id']})" if f else ""))
    want = [(s, "m2d") for s, p in pb["macros"].items() if p.get("after_v4") == "m2d"]
    if did_refile:
        new_ids = {f["id"] for f in refiled.values()}
        conflicts = wait_for(cli, project_id, want, since_fact_ids=new_ids, timeout=REVERIFY_WAIT)
    else:
        # Re-verified by an earlier run: show what the detector raised against the new policy then.
        by_id = id_map(cli, project_id)
        conflicts = [v for v in (view(c, by_id) for c in list_conflicts(cli, project_id))
                     if any(k["document"] == ids["policy"] for k in v["chunks"]) and set(v["macros"]) & {m["slug"] for m in macros}]
    lines = []
    for m in macros:
        mine = [c for c in conflicts if m["slug"] in c["macros"]]
        exp = pb["macros"].get(m["slug"], {}).get("after_v4")
        lines.append(f"  {'✓ clean' if not mine else '✗ ' + ', '.join(sorted({c['category'] for c in mine})):<12} {m['title']} ({m['tool']})"
                     f"   [{'as planned' if ({c['category'] for c in mine} == ({exp} if exp else set())) else 'planned: ' + (exp or 'clean')}]")
        for c in mine:
            lines.append(f"      {c['category']} · {c['name']}   {c['id']}")
            lines.append(f"        {clip(c['description'], 150)}")
            for k in c["chunks"]:
                lines.append(f"        policy ({k['document']}): \"{clip(excerpt(k['text'], m['depends_on']), 150)}\"")
    missing = [s for s, _ in want if not any(s in c["macros"] for c in conflicts)]
    if missing:
        lines.append(f"\n  (not raised within {REVERIFY_WAIT}s: {', '.join(missing)} — the detector may have deferred it to a batch"
                     " that runs minutes later; `python3 demo.py conflicts` shows it once it lands)")
    emit("text", "\n" + "\n".join(lines))

    # The flagged macro is stale: trust the policy, publish the next version, check it.
    fixed = []
    for slug, p in pb["macros"].items():
        if p.get("after_v4") != "m2d":
            continue
        c = next((c for c in conflicts if slug in c["macros"] and c["category"] == "m2d" and not c["resolved"]), None)
        if not c:
            continue
        m = next(x for x in load_macros() if x["slug"] == slug)
        emit("text", f"\n  [m2d → trust_document] {m['title']}: {p['why']}")
        resolve(cli, project_id, c["id"], "--strategy", "trust_document")
        old = refiled[slug]
        v = int(old["metadata"]["version"]) + 1
        nv = add_macro(cli, project_id, m, p["v2"], v, ids["policy"], replaces=old["id"])
        emit("text", f"    published version {v} → {nv['id']}: {clip(nv['fact'], 120)}")
        again = wait_for(cli, project_id, [], since_fact_ids={nv["id"]}, timeout=25)
        emit("text", f"    {'✓' if not again else '✗'} the detector raised {len(again)} conflict(s) on version {v}")
        fixed.append(dict(macro=slug, conflict=c["id"], version=v, ok=not again, before=old["fact"], after=nv["fact"], why=p["why"]))
    emit("reverify", "", macros=[dict(slug=m["slug"], title=m["title"], fact_id=refiled[m["slug"]]["id"]) for m in macros],
         conflicts=conflicts, fixed=fixed)
    return dict(conflicts=conflicts, fixed=fixed)


# --------------------------------------------------------------------------- step 8: the support AI uses macros

def macro_card(f: dict) -> str:
    meta = f.get("metadata") or {}
    return (f"  {meta.get('title')}  v{meta.get('version')} · {meta.get('tool')} #{meta.get('source_id')} · checked against "
            f"{meta.get('checked_against')}{' · ' + meta['approved_exception'] if meta.get('approved_exception') else ''}\n"
            f"    {f.get('fact', '').split('): ', 1)[-1]}")


def get_macro(cli: CLI, project_id: str, slug: str, echo: bool = True) -> dict | None:
    return live_macros(cli, project_id, echo=echo).get(slug)


def ask(cli: CLI, project_id: str, q: str, expect: str | None = None, echo: bool = True) -> dict:
    """What a support assistant does at reply time: search the KB, keep the macro hits, take the top one."""
    res = cli.run("search", q, "--projects", project_id, "--types", "fact", "--top-k", "5", scoped=True, echo=echo) or {}
    live = {f["id"]: f for f in live_macros(cli, project_id, echo=False).values()}
    hits = [h for h in res.get("facts") or []]
    ranked = [(i + 1, live.get(h.get("id"))) for i, h in enumerate(hits)]
    macros = [(r, f) for r, f in ranked if f]
    top = macros[0][1] if macros else None
    slug = (top.get("metadata") or {}).get("macro") if top else None
    retired = [h for h in hits if h.get("id") not in live]
    return dict(q=q, expect=expect, top=top, slug=slug, rank=macros[0][0] if macros else None,
                ok=(slug == expect) if expect else None, hits=len(hits), retired=len(retired))


def support_ai(cli: CLI, ids: dict, pb: dict) -> list[dict]:
    project_id = ids["project"]
    emit("text", "\n  By name — the assistant knows which macro it wants:")
    f = get_macro(cli, project_id, "shipping-cost")
    emit("text", macro_card(f) if f else "  (shipping-cost not found)")
    emit("text", "\n  By question — the assistant searches and uses the top macro hit:")
    out = []
    for item in pb["questions"]:
        r = ask(cli, project_id, item["q"], item["macro"])
        out.append(r)
        mark = "✓" if r["ok"] else "✗"
        got = (r["top"].get("metadata") or {}).get("title") if r["top"] else "no macro"
        emit("text", f"  {mark} “{item['q']}”\n    → {got} (rank {r['rank']} of {r['hits']} fact hits"
                     f"{', retired versions in hits: ' + str(r['retired']) if r['retired'] else ''})"
                     + (f"\n{macro_card(r['top'])}" if r["top"] else ""))
    n = sum(1 for r in out if r["ok"])
    emit("text", f"\n  {n}/{len(out)} questions answered with the expected macro; forgotten versions never come back"
                 " (trust_document / delete take them out of search)")
    emit("answers", "", answers=[dict(q=r["q"], expect=r["expect"], slug=r["slug"], ok=r["ok"], rank=r["rank"],
                                     text=(r["top"] or {}).get("fact")) for r in out])
    return out


# --------------------------------------------------------------------------- step 9: audit

def audit(cli: CLI, ids: dict, pb: dict) -> Path:
    project_id = ids["project"]
    by_id = id_map(cli, project_id)
    resolved = [view(cli.run("fact", "conflict", "get", c["id"], "--project", project_id, scoped=True), by_id)
                for c in list_conflicts(cli, project_id, "--resolved", "true")]
    still = [view(c, by_id) for c in list_conflicts(cli, project_id, "--resolved", "false", echo=False)]
    live = live_macros(cli, project_id, echo=False)
    lines = [f"# Macro audit — {pb['company']}", "",
             f"Run by {pb['lead']['name']} ({pb['lead']['role']}). Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
             f"from MemoryLake project `{project_id}`. Policy of record: `{ids['policy']}`.", "",
             "## Macros in use", ""]
    for m in load_macros():
        f = live.get(m["slug"])
        if f:
            meta = f.get("metadata") or {}
            lines.append(f"- **{m['title']}** v{meta.get('version')} ({m['tool']} #{m['source_id']}, fact `{f['id']}`, "
                         f"checked against `{meta.get('checked_against')}`): {f['fact'].split('): ', 1)[-1]}")
    lines += ["", "## Resolved conflicts (`fact conflict get`)", ""]
    for c in resolved:
        rs = c["resolve"] or {}
        lines += [f"### {c['name']} — `{c['id']}`", "",
                  f"- {c['category']} · {c['conflict_type']} · macros: {', '.join(c['macros'])}",
                  f"- raised {c['created_at']} · resolved {rs.get('created_at')} · strategy `{rs.get('strategy')}`",
                  f"- forgotten: {', '.join('`%s`' % i for i in rs.get('forgotten_fact_ids') or []) or 'none'}",
                  f"- why: {c['description']}"]
        lines += [f"- snapshot `{f['id']}`: {f['text']}" for f in c["facts"]]
        lines += [f"- server rewrite `{u.get('fact_id')}`: {u.get('new_fact_text')}" for u in rs.get("updated_facts") or []
                  if rs.get("strategy") == "trust_fact"]
        lines += [f"- policy excerpt ({k['document']}): {' / '.join(l.strip() for l in k['text'].splitlines() if l.strip())}" for k in c["chunks"]]
        lines.append("")
    lines += ["## Still open", ""] + ([f"- `{c['id']}` {c['category']}{' (stale)' if c['stale'] else ''}: {c['name']} — {', '.join(c['macros'])}"
                                       for c in still] or ["- none"])
    OUT.mkdir(exist_ok=True)
    path = OUT / f"macro-audit-{pb['today']}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    show = [f"\n  {len(resolved)} resolved conflict(s), {len(still)} open:"]
    for c in resolved:
        rs = c["resolve"] or {}
        show.append(f"    {rs.get('strategy', '?'):<15} [{c['category']}] {clip(c['name'], 55):<55} {', '.join(c['macros'])}")
    for c in still:
        show.append(f"    {'OPEN' + (' · stale' if c['stale'] else ''):<15} [{c['category']}] {clip(c['name'], 55):<55} {', '.join(c['macros'])}")
    show.append(f"\n  Every resolution keeps the macro's words as they were when the conflict was raised (`fact_snapshots`).")
    show.append(f"  Audit written to {os.path.relpath(path)}")
    emit("text", "\n".join(show))
    emit("audit", "", resolved=resolved, open=still, file=os.path.relpath(path), markdown="\n".join(lines))
    return path


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    pb = load_playbook()
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the support KB project (its macros, policy documents and conflicts go with it)")
    names = {lib_name(pb["policy"]["current"]), lib_name(pb["policy"]["next"])}
    removed, token = 0, None
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=token is None and not quiet) or {}
        for item in page.get("items") or []:
            if item.get("name") in names:
                cli.run("lib", "delete", item.get("item_id") or item.get("id"))
                removed += 1
        token = page.get("continuation_token")
        if not token:
            break
    note(f"deleted {removed} uploaded policy file(s) from the Library")
    for n in names:
        (OUT / n).unlink(missing_ok=True)
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    pb = load_playbook()
    t0 = time.time()
    banner(2, TOTAL, "Support KB — the current policy (v3)")
    ids = setup_kb(cli, pb, reset)
    banner(3, TOTAL, "Import macros — 4 from Zendesk, 3 from Intercom, one fact each")
    have = import_macros(cli, ids, pb)
    banner(4, TOTAL, "Detector report — which macros contradict the policy, themselves, or each other")
    found = detector_report(cli, ids, pb, have)
    banner(5, TOTAL, "Triage — settle each conflict: edit_fact, trust_document, trust_fact, dismiss")
    done = triage(cli, ids, pb)
    banner(6, TOTAL, "Policy v4 replaces v3 — and the detector stays quiet")
    swap = swap_policy(cli, ids, pb)
    banner(7, TOTAL, "Re-verify — re-file the macros that depend on what changed")
    rv = reverify(cli, ids, pb)
    banner(8, TOTAL, "Support AI — macros by name and by question")
    answers = support_ai(cli, ids, pb)
    banner(9, TOTAL, "Audit — every conflict, how it was settled, what the macros say now")
    path = audit(cli, ids, pb)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   detected=sorted(f"{','.join(c['macros'])}:{c['category']}" for c in found),
                   triage={d["strategy"]: d["ok"] for d in done},
                   quiet_after_v4=len(swap["new_conflicts"]) == 0,
                   reverify=sorted(f"{','.join(c['macros'])}:{c['category']}" for c in rv["conflicts"]),
                   answers=sum(1 for a in answers if a["ok"]), audit=path.name)
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")
    return summary


def show_conflict_list(cli: CLI) -> None:
    proj = find_project(cli)
    if proj is None:
        die("the support KB does not exist yet; run `python3 demo.py` first")
    by_id = id_map(cli, proj["id"])
    items = [view(c, by_id) for c in list_conflicts(cli, proj["id"])]
    if not items:
        emit("text", "\n  No conflicts in the support KB.")
    for c in items:
        rs = c["resolve"] or {}
        state = ("resolved (" + str(rs.get("strategy") or "…") + ")") if c["resolved"] else ("OPEN · stale" if c["stale"] else "OPEN")
        emit("text", f"\n  {c['id']}  [{c['category']}] {state}  {c['name']}  ({', '.join(c['macros'])})"
                     + "".join(f"\n    fact {f['id']}: {clip(f['text'], 110)}" for f in c["facts"])
                     + "".join(f"\n    document {k['document']}" for k in c["chunks"]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "conflicts", "macro", "ask", "cleanup"],
                    help="run = full demo (default); conflicts = every conflict in the support KB; "
                         "macro NAME = one macro by name (e.g. shipping-cost); ask \"QUESTION\" = the macro a support "
                         "assistant would use; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="macro name for `macro`, question for `ask`")
    ap.add_argument("--reset", action="store_true", help="delete the support KB first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command in ("macro", "ask") and not args.arg:
        ap.error(f"{args.command} needs an argument, e.g. python3 demo.py macro shipping-cost / "
                 "python3 demo.py ask \"Do I pay shipping on a 60 dollar order?\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command != "run":
        proj = find_project(cli)
        if proj is None:
            die("the support KB does not exist yet; run `python3 demo.py` first")
        if args.command == "conflicts":
            banner(2, 2, "Every conflict in the support KB")
            show_conflict_list(cli)
        elif args.command == "macro":
            banner(2, 2, f"Macro — {args.arg}")
            f = get_macro(cli, proj["id"], slugify(args.arg))
            emit("text", macro_card(f) if f else f"  no macro named {slugify(args.arg)}; names: "
                 + ", ".join(m["slug"] for m in load_macros()))
        else:
            banner(2, 2, f"Ask — {args.arg}")
            r = ask(cli, proj["id"], args.arg)
            emit("text", (f"  → {(r['top'].get('metadata') or {}).get('title')} (rank {r['rank']} of {r['hits']} fact hits)\n"
                          + macro_card(r["top"])) if r["top"] else "  no macro among the hits")
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py conflicts` lists every conflict; `python3 demo.py cleanup` removes the demo data.")


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

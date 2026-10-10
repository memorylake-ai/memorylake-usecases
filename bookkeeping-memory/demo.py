#!/usr/bin/env python3
"""
Bookkeeping memory for small accounting firms — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Ledgerline Bookkeeping (a fictional three-person firm) keeps the books for two clients,
each in its own MemoryLake project: Fernhill Supply, an online store, and Marrow & Pine,
a café. Each project holds the onboarding thread with the owner and the client's past
ledger exports (.xlsx).

  - The clients' rules are pinned from the threads. The same Paylane card-fee line is
    Cost of revenue for Fernhill (investors read gross margin with processing in it)
    and Bank and merchant fees for Marrow & Pine.
  - September's bank feed is categorized line by line: a client rule first, otherwise
    the last time that payee was booked, cited as file · sheet!cells (the ledger row
    MemoryLake returned), otherwise "ask the client".
  - Marrow & Pine's September export arrives corrupted: `proj stats` counts the error,
    `proj doc get` names it, `proj doc inspect` leaves it out. The client re-sends it;
    `lib upload --on-conflict overwrite` + `proj doc reload` fixes the same document,
    and the line that needed it gets a precedent.
  - Every cited row is checked against the original workbook, downloaded back.
  - "Why is this booked that way?": the rule, the message it came from, and the batch
    of messages MemoryLake read it in (`conv consumed-messages`), with the owner's reason.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SOURCES = DATA / "sources"
OUT = HERE / "out"
ORIGINALS = OUT / "originals"  # the downloaded originals the citations are checked against
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-bkm"  # memorylake-usecases / bookkeeping-memory
RELOAD_WAIT = 300   # seconds to wait for a reloaded document

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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search", "stats", "inspect",
                 "consumed-messages", "trace", "download"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out", "close_notify",
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

    def run(self, *args: str, scoped: bool = False, echo: bool = True, stderr: list | None = None):
        retryable = any(str(a) in self.READ_ONLY for a in args[:4])
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
        if stderr is not None:
            stderr.append(err)
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


def client_by_key(key: str) -> dict:
    for c in load_story()["clients"]:
        if c["key"] == key:
            return c
    die(f"no client “{key}” (choose from: {', '.join(c['key'] for c in load_story()['clients'])})")


def lib_name(file: str) -> str:
    """The Library file name: prefixed, so copies of the demo with another PREFIX never overwrite
    (`--on-conflict overwrite`) or delete (cleanup by name) each other's files."""
    return f"{PREFIX}-{file}"


def file_of(name: str) -> str:
    return name[len(PREFIX) + 1:] if name and name.startswith(PREFIX + "-") else name


def all_files() -> list[str]:
    return [e["file"] for c in load_story()["clients"] for e in c["exports"]]


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def money(x: float) -> str:
    return f"{x:,.2f}"


def fact_text(f: dict) -> str:
    return f.get("fact") or f.get("content") or ""


# --------------------------------------------------------------------------- the workbook itself (stdlib)

def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_rows(data: bytes) -> tuple[str, dict[int, dict[int, str]]]:
    """(sheet name, {row: {column index: text}}) of the first worksheet, read from the bytes (no library)."""
    z = zipfile.ZipFile(BytesIO(data))
    wb = z.read("xl/workbook.xml").decode("utf-8")
    sheet = html.unescape(re.search(r'<sheet [^>]*name="([^"]+)"', wb).group(1))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S):
            shared.append(html.unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", si))))
    xml = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    rows: dict[int, dict[int, str]] = {}
    for m in re.finditer(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', xml, re.S):
        letters, r, attrs, body = m.group(1), int(m.group(2)), m.group(3), m.group(4) or ""
        kind = (re.search(r't="([^"]+)"', attrs) or [None, "n"])[1]
        if kind == "inlineStr":
            val = html.unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", body)))
        else:
            v = re.search(r"<v>([^<]*)</v>", body)
            val = html.unescape(v.group(1)) if v else ""
            if kind == "s" and val:
                val = shared[int(val)]
        rows.setdefault(r, {})[_col_index(letters)] = val
    return sheet, rows


def same_value(a: str, b: str) -> bool:
    """Cells compare as numbers when both are numbers (MemoryLake prints -430.00 as -430), else as text."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return (a or "").strip() == (b or "").strip()


# --------------------------------------------------------------------------- lookups

def find_project(cli: CLI, client: dict):
    return cli.try_run("proj", "get", f"{PREFIX}-{client['key']}", "--by-custom-id", scoped=True)


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


def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        args = ["fact", "list", "--projects", project_id, "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_messages(cli: CLI, conv_id: str, echo: bool = True) -> list[dict]:
    page = cli.run("conv", "msg", "list", conv_id, "--page-size", "50", echo=echo) or {}
    return sorted(page.get("items") or [], key=lambda m: m.get("sequence_no") or 0)


def message_text(m: dict) -> str:
    return next((b.get("text") for b in m.get("content") or [] if b.get("block_type") == "TEXT"), "") or ""


# --------------------------------------------------------------------------- step 2: clients

def ensure_actor(cli: CLI, key: str, person: dict) -> str:
    custom_id = f"{PREFIX}-{key}"
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", person["display"],
                        "--description", person["role"])
        note(f"created actor {person['display']} → {actor['id']}")
    else:
        note(f"actor {person['display']} already exists → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"]


def ensure_project(cli: CLI, client: dict) -> str:
    proj = find_project(cli, client)
    if proj is None:
        proj = cli.run("proj", "create", "--name", f"{client['name']} — books", "--custom-id", f"{PREFIX}-{client['key']}",
                       "--description", f"Ledgerline Bookkeeping: {client['name']} ({client['what']}). Demo data.", scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    return proj["id"]


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    actors = {story["bookkeeper"]["key"]: ensure_actor(cli, story["bookkeeper"]["key"], story["bookkeeper"])}
    projects = {}
    for c in story["clients"]:
        actors[c["owner"]["key"]] = ensure_actor(cli, c["owner"]["key"], c["owner"])
        projects[c["key"]] = ensure_project(cli, c)
    lines = [f"\n  {'client':<22} {'owner':<16} project (one per client: its rules, threads and files stay apart)"]
    for c in story["clients"]:
        lines.append(f"  {c['name']:<22} {c['owner']['display']:<16} {projects[c['key']]}")
    emit("text", "\n".join(lines))
    emit("clients", "", clients=[dict(key=c["key"], name=c["name"], what=c["what"], owner=c["owner"]["display"],
                                     project=projects[c["key"]]) for c in story["clients"]],
         bookkeeper=story["bookkeeper"]["display"])
    return dict(actors=actors, projects=projects)


# --------------------------------------------------------------------------- step 3: onboarding threads → client rules

def speaker_line(people: dict, key: str, text: str) -> str:
    p = people[key]
    return f"{p['display']} ({p['role']}): {text}"


def wait_for_memory(cli: CLI, conv_id: str, timeout: int = 600) -> bool:
    t0, first = time.time(), True
    while time.time() - t0 < timeout:
        try:
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first) or {}
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            status = {}
        first = False
        if status.get("cook_finished"):
            return True
        emit("progress", f"  … MemoryLake is reading the thread ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
        time.sleep(6)
    note(f"gave up after {timeout}s; still processing server-side")
    return False


def ensure_thread(cli: CLI, story: dict, client: dict, ids: dict) -> tuple[str, int]:
    """The owner's onboarding thread, stored as a GROUP conversation in the client's project."""
    t = client["thread"]
    custom_id = f"{PREFIX}-{client['key']}-onboarding"
    people = {story["bookkeeper"]["key"]: story["bookkeeper"], client["owner"]["key"]: client["owner"]}
    conv = cli.try_run("conv", "get", custom_id, "--by-custom-id", scoped=True)
    done = 0
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", custom_id, "--project", ids["projects"][client["key"]],
                       "--actors", ",".join(ids["actors"][k] for k in people), "--kind", "GROUP", "--name", t["name"],
                       "--metadata", f"client={client['key']}", scoped=True)
    else:
        done = len(list_messages(cli, conv["id"]))
        note(f"thread “{t['name']}” exists with {done} message(s) → {conv['id']}")
    start = datetime.fromisoformat(t["date"].replace("Z", "+00:00"))
    parent = conv.get("current_message_id")
    appended = 0
    for i, (who, text) in enumerate(t["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(minutes=7 * (i - 1))).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv["id"], "--actor", ids["actors"][who], "--custom-id", f"turn-{i:02d}",
                "--timestamp", ts, "--text", speaker_line(people, who, text)]
        if parent:
            args += ["--parent", parent]
        msg = cli.run(*args, scoped=True, echo=i == done + 1 or i == len(t["turns"]) or i - 1 in t.get("read_after", []))
        parent = msg["id"]
        appended += 1
        if i in t.get("read_after", []) and i < len(t["turns"]):
            # The thread arrives in two sittings; MemoryLake reads each one as it comes, so the second sitting
            # is a separate extraction batch (see `conv consumed-messages` in the last step).
            note(f"messages 1–{i} stored; waiting for MemoryLake to read them before the rest of the thread arrives")
            wait_for_memory(cli, conv["id"])
    if appended:
        note(f"{appended} message(s) appended to “{t['name']}”")
    return conv["id"], appended


def rule_meta(client: dict, rule: dict, conv_id: str) -> dict:
    return {"kind": "rule", "client": client["key"], "vendor": rule["vendor"], "line": rule["line"],
            "match": rule["match"], "category": rule["category"], "treatment": rule.get("treatment", ""),
            "thread": conv_id, "turn": rule["turn"], "reason_turn": rule["reason_turn"]}


def threads_and_rules(cli: CLI, story: dict, ids: dict) -> dict:
    convs, rules = {}, {}
    for c in story["clients"]:
        emit("text", f"\n  {c['name']} — {len(c['thread']['turns'])} messages, {c['thread']['date'][:10]}")
        conv_id, appended = ensure_thread(cli, story, c, ids)
        convs[c["key"]] = conv_id
        if appended or not cli.run("conv", "cook-status", conv_id, scoped=True, echo=False).get("cook_finished"):
            t0 = time.time()
            wait_for_memory(cli, conv_id)
            note(f"thread read into memory ({int(time.time() - t0)}s)")
        emit("thread", "", client=c["key"], conv=conv_id, name=c["thread"]["name"],
             turns=[dict(n=i, who=(c["owner"] if w == c["owner"]["key"] else story["bookkeeper"])["display"], text=t)
                    for i, (w, t) in enumerate(c["thread"]["turns"], start=1)])
    lines = []
    for c in story["clients"]:
        pid = ids["projects"][c["key"]]
        facts = list_facts(cli, pid)
        live = {fact_text(f): f for f in facts}
        mine = []
        for r in c["rules"]:
            meta = rule_meta(c, r, convs[c["key"]])
            f = live.get(r["text"])
            if f is None:
                # The rule as agreed, pinned word for word: extraction rewords and sometimes merges what it reads,
                # and a rule is something the firm applies verbatim.
                res = cli.run("fact", "add", "--project", pid, r["text"], scoped=True) or {}
                fid = ((res.get("facts") or [{}])[0]).get("id")
                cli.run("fact", "update", fid, "--project", pid, "--metadata", json.dumps(meta), scoped=True)
                new = True
            else:
                fid, new = f["id"], False
                if (f.get("metadata") or {}).get("category") != meta["category"] or (f.get("metadata") or {}).get("thread") != meta["thread"]:
                    cli.run("fact", "update", fid, "--project", pid, "--metadata", json.dumps(meta), scoped=True)
            mine.append(dict(r, id=fid, new=new))
        extracted = [fact_text(f) for f in facts if fact_text(f) not in {r["text"] for r in c["rules"]}]
        rules[c["key"]] = mine
        lines.append(f"\n  {c['name']}: {len(mine)} rule(s) on file")
        for r in mine:
            lines.append(f"    {'+' if r['new'] else '='} {(r['vendor'] + ' ' + r['line']):<32} → {r['category']}"
                         + (f" ({r['treatment']})" if r.get("treatment") else "") + f"   [{r['id']}]")
        lines.append(f"    also extracted from the thread by MemoryLake: {len(extracted)} fact(s)")
        lines += [f"      · {clip(t, 110)}" for t in extracted[:4]]
        emit("rules", "", client=c["key"], rules=mine, extracted=extracted)
    emit("text", "\n".join(lines) + "\n\n  + pinned now   = already on file (pinned verbatim, with metadata vendor / line / category / thread / turn)")
    return dict(convs=convs, rules=rules)


# --------------------------------------------------------------------------- step 4: ledger exports

def upload_and_import(cli: CLI, project_id: str, files: list[tuple[str, Path]]) -> list[str]:
    """(library name, local path) → uploaded and imported. A file that fails to parse ends in status `error`;
    `--wait` then exits non-zero, which is part of the story, not a crash."""
    ids = []
    for name, path in files:
        item = cli.run("lib", "upload", os.path.relpath(path), "--name", name, "--on-conflict", "overwrite")
        ids.append(item["item_id"])
        note(f"uploaded {name} ({path.stat().st_size:,} bytes)")
    t0 = time.time()
    try:
        res = cli.run("proj", "doc", "import", "--project", project_id, *ids, "--wait", scoped=True) or {}
        note(f"imported {res.get('success_count', len(ids))} file(s) ({int(time.time() - t0)}s)")
    except CLIError as e:
        if not e.has("finished with status `error`"):
            raise
        failed = re.findall(r"doc-[0-9a-f]+", e.stderr)
        note(f"import finished in {int(time.time() - t0)}s; {len(failed)} document(s) ended in status `error`: {', '.join(failed)}")
    return ids


def ledger_files(cli: CLI, story: dict, ids: dict) -> dict:
    recovered_before = {}
    for c in story["clients"]:
        pid = ids["projects"][c["key"]]
        have = {d["name"]: d for d in list_documents(cli, pid)}
        todo = []
        for e in c["exports"]:
            name = lib_name(e["file"])
            if name in have:
                if e.get("broken_first") and have[name].get("status") == "okay":
                    recovered_before[name] = have[name]["id"]
                continue
            # The first September export Marrow & Pine sent was a broken download; it goes in under the real name.
            src = SOURCES / (e.get("broken_first") or e["file"])
            todo.append((name, src))
        if todo:
            emit("text", f"\n  {c['name']}: {len(todo)} export(s) arrive")
            upload_and_import(cli, pid, todo)
        else:
            note(f"{c['name']}: all exports are already in the project")
    report = files_report(cli, story, ids)
    for name, doc_id in recovered_before.items():
        note(f"{file_of(name)} ({doc_id}) was re-sent and reloaded in an earlier run; `--reset` replays the broken export")
    report["recovered_before"] = recovered_before
    return report


def subtables(item: dict) -> list[dict]:
    out = []
    for ws in item.get("worksheets") or []:
        for st in ws.get("subtables") or []:
            for t in st.get("inner_tables") or []:
                cols = [dict(name=c.get("name"), type=c.get("data_type"), count=c.get("count"), nulls=c.get("null_count"),
                             distinct=c.get("approx_ndv"), examples=(c.get("display_examples") or [])[:3])
                        for c in t.get("columns") or [] if c.get("name") != "UNNAMED_COLUMN"]
                out.append(dict(sheet=ws.get("name"), title=st.get("title"), range=st.get("range"), title_range=st.get("title_range"),
                                header_range=t.get("header_range"), data_range=t.get("data_range") or t.get("range"),
                                rows=t.get("num_rows"), columns=cols))
    return out


def files_report(cli: CLI, story: dict, ids: dict, only: str | None = None, echo: bool = True, phase: str = "import") -> dict:
    """What each project holds, and how MemoryLake read each workbook:
    `proj stats` (counts by status), `proj doc get` for the failed ones, `proj doc inspect` for the parsed tables."""
    out, lines = {}, []
    for c in story["clients"]:
        if only and c["key"] != only:
            continue
        pid = ids["projects"][c["key"]]
        stats = cli.run("proj", "stats", pid, scoped=True, echo=echo) or {}
        st = stats.get("document_status") or {}
        docs = list_documents(cli, pid, echo=False)
        lines.append(f"\n  {c['name']}: {stats.get('document_count', len(docs))} document(s) · "
                     + " · ".join(f"{k} {st.get(k, 0)}" for k in ("pending", "running", "okay", "error")))
        failed = []
        for d in docs:
            if d.get("status") == "error":
                full = cli.run("proj", "doc", "get", "--project", pid, d["id"], scoped=True, echo=echo) or {}
                err = full.get("error") or {}
                failed.append(dict(id=d["id"], name=d["name"], code=err.get("code"), msg=err.get("msg")))
                lines.append(f"    ✗ {file_of(d['name'])}  {d['id']}  status error · {err.get('code')} — {err.get('msg')}")
        tables, omitted = {}, []
        if docs:
            err_out: list[str] = []
            res = cli.run("proj", "doc", "inspect", "--project", pid, *[d["id"] for d in docs], scoped=True,
                          echo=echo, stderr=err_out) or {}
            omitted = re.findall(r"doc-[0-9a-f]+", "".join(err_out))
            # Pre-signed storage links (persist_path, *_s3_url) are in this reply; nothing of them is printed.
            for item in res.get("items") or []:
                tables[item["name"]] = subtables(item)
                lines.append(f"    ✓ {file_of(item['name'])}  {item['document_id']}  {item.get('source_type')} · "
                             f"{len(tables[item['name']])} table(s) found:")
                for t in tables[item["name"]]:
                    title = f"“{t['title']}”" if t["title"] else "(no title row)"
                    lines.append(f"        {t['sheet']}!{t['range']:<9} {title:<22} {t['rows']} row(s) · "
                                 + ", ".join(f"{col['name']}:{col['type']}" for col in t["columns"]))
            if omitted:
                lines.append(f"    inspect left out {', '.join(omitted)} — the server omits documents in status `error`")
        out[c["key"]] = dict(project=pid, stats=st, count=stats.get("document_count", len(docs)), failed=failed,
                             tables=tables, omitted=omitted, documents=[dict(id=d["id"], name=d["name"], status=d.get("status")) for d in docs])
    emit("text", "\n".join(lines))
    emit("files", "", clients=out, phase=phase)
    return out


# --------------------------------------------------------------------------- step 5: categorize the bank feed

def parse_range(r: str) -> tuple[str, int, str, int] | None:
    m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)$", r or "")
    return (m.group(1), int(m.group(2)), m.group(3), int(m.group(4))) if m else None


def split_row(line: str, names: list[str]) -> dict[str, str]:
    """`Date: 2026-08-19, Payee: Teal Freight, Memo: Pallet shipping, …` → {column: value}. Split only where a known
    column name follows, so a value with a comma in it (“Delivery payout, net”) stays whole."""
    if not names:
        return {}
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    parts = re.split(rf"(?:^|, )({alt}): ", line)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def ledger_rows(cli: CLI, project_id: str, query: str, echo: bool = True) -> tuple[list[dict], list[str]]:
    """Table rows from `search --types document`. An xlsx hit's `table` item carries a whole table as one chunk,
    one `Column: value` line per row, and the cells it covers (`range`, e.g. A2:E19): line i is row start+i."""
    res = cli.run("search", query, "--projects", project_id, "--types", "document", "--top-k", "5",
                  scoped=True, echo=echo) or {}
    rows, problems = [], []
    for d in res.get("documents") or []:
        for it in d.get("items") or []:
            if it.get("type") != "table":
                continue
            hl = it.get("highlight") or {}
            names = [col.get("name") for t in (hl.get("inner_tables") or it.get("inner_tables") or [])
                     for col in t.get("columns") or [] if col.get("name") and col.get("name") != "excel_row_number"]
            for ch in hl.get("chunks") or []:
                rg = parse_range(ch.get("range") or "")
                lines = (ch.get("text") or "").split("\n")
                if not rg or rg[3] - rg[1] + 1 != len(lines):
                    problems.append(f"{d.get('document_name')} {ch.get('range')}: {len(lines)} line(s) for that range — not cited")
                    continue
                for i, line in enumerate(lines):
                    r = rg[1] + i
                    rows.append(dict(document=d.get("document_name"), document_id=d.get("document_id"), sheet=d.get("sheet_name"),
                                     rank=d.get("rank"), row=r, cells=f"{rg[0]}{r}:{rg[2]}{r}", text=line,
                                     values=split_row(line, names)))
    return rows, problems


def cite(row: dict) -> str:
    return f"{file_of(row['document'])} · {row['sheet']}!{row['cells']}"


def rule_for(rules: list[dict], line: dict) -> dict | None:
    for r in rules:
        if r["vendor"].lower() == line["payee"].lower() and r["match"].lower() in line["memo"].lower():
            return r
    return None


def propose(cli: CLI, client: dict, project_id: str, rules: list[dict], line: dict, echo: bool = True) -> dict:
    """Rule first; otherwise the latest booking of this payee in the client's own ledgers; otherwise ask."""
    rows, problems = ledger_rows(cli, project_id, f"{line['payee']} {line['memo']}", echo=echo)
    same = [r for r in rows if (r["values"].get("Payee") or "").lower() == line["payee"].lower() and r["values"].get("Category")]
    rule = rule_for(rules, line)
    if rule:
        kind = (rule["match"] or "").lower()
        alike = [r for r in same if kind in (r["values"].get("Memo") or "").lower()]
        alike.sort(key=lambda r: r["values"].get("Date") or "", reverse=True)
        disagree = [r for r in alike if r["values"].get("Category") != rule["category"]]
        return dict(category=rule["category"], source="rule", rule=dict(id=rule["id"], text=rule["text"], treatment=rule.get("treatment")),
                    precedent=None, disagree=disagree[:2], agree=len(alike) - len(disagree), problems=problems)
    same.sort(key=lambda r: (r["values"].get("Date") or "", r["row"]), reverse=True)
    if same:
        p = same[0]
        return dict(category=p["values"]["Category"], source="precedent", rule=None, precedent=p,
                    others=len(same) - 1, disagree=[], problems=problems)
    return dict(category=None, source="ask", rule=None, precedent=None, disagree=[], problems=problems,
                looked=len({r["document"] for r in rows}))


def categorize(cli: CLI, story: dict, ids: dict, rules: dict, phase: str = "first", only: str | None = None,
               lines_filter=None) -> dict:
    out = {}
    for c in story["clients"]:
        if only and c["key"] != only:
            continue
        pid = ids["projects"][c["key"]]
        rows, text = [], [f"\n  {c['name']} — {story['month']} bank feed"]
        for line in c["feed"]:
            if lines_filter and not lines_filter(c, line):
                continue
            p = propose(cli, c, pid, rules[c["key"]], line)
            want_cat, want_src = line["expect"]
            if phase == "first" and line.get("needs_resent") and ids.get("broken_now"):
                want_cat, want_src = None, "ask"   # its only precedent is in the export that failed to parse
            ok = p["source"] == want_src and p["category"] == want_cat
            head = f"  {'✓' if ok else '✗'} {line['date']}  {line['payee']:<19} {clip(line['memo'], 28):<28} {money(line['amount']):>10}"
            if p["source"] == "rule":
                text.append(f"{head}  → {p['category']}" + (f" ({p['rule']['treatment']})" if p["rule"].get("treatment") else ""))
                text.append(f"      client rule {p['rule']['id']}: “{clip(p['rule']['text'], 92)}”")
                for d in p["disagree"]:
                    text.append(f"      ! precedent disagrees: {cite(d)} booked it to {d['values'].get('Category')} — the rule wins")
            elif p["source"] == "precedent":
                pr = p["precedent"]
                text.append(f"{head}  → {p['category']}")
                text.append(f"      precedent {cite(pr)}: {pr['values'].get('Date')} {pr['values'].get('Memo')} "
                            f"{pr['values'].get('Amount')}" + (f"  (+{p['others']} earlier)" if p["others"] else ""))
            else:
                text.append(f"{head}  → ask the client")
                text.append(f"      no rule, and no booking of {line['payee']} in the {p['looked']} file(s) search returned")
            if not ok:
                text.append(f"      expected {want_cat or 'ask the client'} ({want_src})")
            for pb in p["problems"]:
                text.append(f"      · {pb}")
            rows.append(dict(line=line, ok=ok, expect=[want_cat, want_src], **p))
        good = sum(r["ok"] for r in rows)
        text.append(f"\n  {good}/{len(rows)} as expected · "
                    + " · ".join(f"{k} {sum(1 for r in rows if r['source'] == k)}" for k in ("rule", "precedent", "ask")))
        emit("text", "\n".join(text))
        out[c["key"]] = rows
        emit("categorized", "", client=c["key"], name=c["name"], phase=phase, rows=rows)
    return out


# --------------------------------------------------------------------------- step 6: the re-sent export

def resend(cli: CLI, story: dict, ids: dict, rules: dict, files: dict) -> dict | None:
    for c in story["clients"]:
        for e in c["exports"]:
            if not e.get("broken_first"):
                continue
            pid = ids["projects"][c["key"]]
            name = lib_name(e["file"])
            if name in (files.get("recovered_before") or {}):
                note(f"{e['file']} was already re-sent and reloaded in an earlier run — nothing to do "
                     f"(`python3 demo.py --reset` replays the broken export)")
                emit("resent", "", client=c["key"], skipped=True, document=(files["recovered_before"])[name])
                return None
            doc = next((d for d in list_documents(cli, pid, echo=False) if d["name"] == name), None)
            if not doc or doc.get("status") != "error":
                note(f"{e['file']} is not in status `error` ({(doc or {}).get('status')}); nothing to reload")
                return None
            emit("text", f"\n  {c['owner']['display']} re-sends {e['file']}. Same name, so the Library item is overwritten in place;\n"
                         f"  the project's document {doc['id']} is then processed again from the new bytes.")
            item = cli.run("lib", "upload", os.path.relpath(SOURCES / e["file"]), "--name", name, "--on-conflict", "overwrite")
            note(f"uploaded {name} ({(SOURCES / e['file']).stat().st_size:,} bytes) → same Library item {item['item_id'].split(':')[-1]}")
            cli.run("proj", "doc", "reload", "--project", pid, doc["id"], scoped=True)
            t0, first, status, timeline = time.time(), True, "pending", []
            while time.time() - t0 < RELOAD_WAIT:
                d = cli.run("proj", "doc", "get", "--project", pid, doc["id"], scoped=True, echo=first) or {}
                first = False
                status = d.get("status")
                if not timeline or timeline[-1][1] != status:
                    timeline.append((int(time.time() - t0), status))
                if status in ("okay", "error"):
                    break
                emit("progress", f"  … {doc['id']} {status} ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
                time.sleep(5)
            note(f"{doc['id']}: error → " + " → ".join(f"{s} ({t}s)" for t, s in timeline))
            ids["broken_now"] = status != "okay"
            after = files_report(cli, story, ids, only=c["key"], phase="after")
            emit("text", f"\n  Re-categorizing the {c['name']} lines that had no answer:")
            asked = {r["line"]["payee"] for r in files.get("first_pass", {}).get(c["key"], []) if r["source"] == "ask"}
            redo = categorize(cli, story, ids, rules, phase="after", only=c["key"],
                              lines_filter=lambda cl, ln: ln["payee"] in asked)
            emit("resent", "", client=c["key"], document=doc["id"], file=e["file"], status=status, timeline=timeline,
                 stats=after[c["key"]]["stats"], rows=redo.get(c["key"], []))
            return dict(document=doc["id"], status=status, timeline=timeline, rows=redo.get(c["key"], []))
    return None


# --------------------------------------------------------------------------- step 7: verify against the originals

def citations_of(first: dict, after: dict | None) -> list[dict]:
    """Every ledger row the categorization pointed at: the precedents it used and the ones it overrode."""
    cites, seen = [], set()
    rows = [r for v in first.values() for r in v]
    if after:
        rows += after.get("rows") or []
    for r in rows:
        refs = ([("precedent", r["precedent"])] if r.get("precedent") else []) + [("overridden", d) for d in r.get("disagree") or []]
        for use, p in refs:
            key = (p["document"], p["cells"])
            if key in seen:
                continue
            seen.add(key)
            cites.append(dict(n=len(cites) + 1, use=use, line=f"{r['line']['payee']} {r['line']['date']}", document=p["document"],
                              document_id=p["document_id"], sheet=p["sheet"], cells=p["cells"], row=p["row"], text=p["text"],
                              values=p["values"], sha256=sha256_of((SOURCES / file_of(p["document"])).read_bytes())))
    return cites


def originals(cli: CLI, project_of: dict, names: list[str], echo: bool = True) -> dict[str, bytes]:
    ORIGINALS.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in dict.fromkeys(names):
        doc_id, pid = project_of.get(name, (None, None))
        if not doc_id:
            continue
        path = ORIGINALS / name
        cli.run("proj", "doc", "download", doc_id, "--project", pid, "-o", os.path.relpath(path), "--force",
                scoped=True, echo=echo)
        out[name] = path.read_bytes()
    return out


def check_row(c: dict, files: dict[str, bytes], row: int | None = None) -> dict:
    data = files.get(c["document"])
    row = row or c["row"]
    if data is None:
        return dict(ok=False, sha256_ok=False, sheet_ok=False, cells_ok=False, row=row, mismatch=["file not downloaded"])
    digest = sha256_of(data)
    sheet, rows = xlsx_rows(data)
    header = rows.get(1, {})
    col = {v: k for k, v in header.items()}
    have = rows.get(row, {})
    mismatch = [f"{k}: cited {v!r}, file {have.get(col.get(k, -1), '')!r}" for k, v in c["values"].items()
                if k not in col or not same_value(v, have.get(col[k], ""))]
    ok_hash = digest == c["sha256"]
    return dict(ok=ok_hash and sheet == c["sheet"] and not mismatch and bool(c["values"]), sha256_ok=ok_hash, sha256=digest,
                sheet_ok=sheet == c["sheet"], cells_ok=not mismatch and bool(c["values"]), row=row, mismatch=mismatch)


def verify(cli: CLI, ids: dict, cites: list[dict], echo: bool = True) -> list[dict]:
    """Check each cited row against the original workbook, not against the index that produced it:
    download it, compare the sha256 of the committed export, and read that row's cells out of the sheet XML."""
    project_of = {}
    for pid in ids["projects"].values():
        for d in list_documents(cli, pid, echo=False):
            project_of[d["name"]] = (d["id"], pid)
    files = originals(cli, project_of, [c["document"] for c in cites], echo=echo)
    results, lines = [], []
    for c in cites:
        r = check_row(c, files)
        results.append(dict(n=c["n"], document=c["document"], cells=c["cells"], use=c["use"], **r))
        lines.append(f"  [{c['n']}] {cite(c):<52} {'✓' if r['sha256_ok'] else '✗'} sha256 · "
                     f"{'✓' if r['cells_ok'] else '✗'} {len(c['values'])} cells match row {c['row']}"
                     + ("" if r["cells_ok"] else f" — {'; '.join(r['mismatch'][:2])}"))
    ok = sum(r["ok"] for r in results)
    lines.append(f"\n  {ok} of {len(results)} cited row(s) check out against the original workbooks.")
    # Control: the same check against the neighbouring row must fail, or it proves nothing.
    ctl = []
    for c in cites:
        r = check_row(c, files, row=c["row"] + 1 if c["row"] > 2 else c["row"] + 1)
        ctl.append(dict(n=c["n"], row=r["row"], passed=r["cells_ok"]))
    caught = sum(not x["passed"] for x in ctl)
    lines.append(f"  Control: the same cells checked against the next row down → {caught}/{len(ctl)} rejected "
                 f"({'the check can fail' if caught == len(ctl) else 'SOME PASSED ON THE WRONG ROW'}).")
    emit("text", "\n".join(lines))
    emit("verified", "", results=results, ok=ok, total=len(results), control=ctl, caught=caught)
    OUT.mkdir(exist_ok=True)
    (OUT / "citations.json").write_text(json.dumps(dict(firm=load_story()["firm"], generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                        citations=cites), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return results


# --------------------------------------------------------------------------- step 8: why is it booked that way?

def why(cli: CLI, story: dict, ids: dict, client: dict, rule: dict, conv_id: str) -> dict:
    """Rule → the message it was agreed in → the batch MemoryLake read that message in (`conv consumed-messages`)."""
    msgs = list_messages(cli, conv_id)
    by_seq = {m.get("sequence_no"): m for m in msgs}
    by_id = {m["id"]: m for m in msgs}
    target = by_seq.get(rule["turn"])
    if not target:
        die(f"message {rule['turn']} of the {client['name']} thread is missing; run the demo again")
    batch = cli.run("conv", "consumed-messages", conv_id, "--message", target["id"], scoped=True) or []
    batch = batch.get("items") if isinstance(batch, dict) else batch
    shown = []
    lines = [f"\n  {client['name']}: “{clip(rule['text'], 120)}”",
             f"    pinned rule {rule['id']}, from message {rule['turn']} of “{client['thread']['name']}”",
             f"    MemoryLake read that message in a batch of {len(batch)} (conv consumed-messages):"]
    for b in batch:
        m = by_id.get(b.get("message_id")) or {}
        seq = b.get("sequence_no")
        mark = "▶" if seq == rule["turn"] else "★" if seq == rule["reason_turn"] else " "
        t = message_text(m)
        shown.append(dict(seq=seq, custom_id=b.get("custom_id"), id=b.get("message_id"), text=t,
                          at=m.get("timestamp"), mark=mark.strip()))
        lines.append(f"    {mark} {seq:>2} {b.get('custom_id') or '':<8} {clip(t, 104)}")
    lines.append("      ▶ the message the rule was agreed in   ★ the reason given for it")
    rest = [m.get("sequence_no") for m in msgs if m["id"] not in {b.get("message_id") for b in batch}]
    if rest:
        lines.append(f"    messages {rest[0]}–{rest[-1]} of the thread are not in it: MemoryLake read them in a later batch")
    # What extraction made of the same batch: a fact naming this vendor and category, with its history.
    extracted = None
    for f in list_facts(cli, ids["projects"][client["key"]], echo=False):
        t = fact_text(f)
        if t == rule["text"] or (f.get("metadata") or {}).get("kind") == "rule":
            continue
        if rule["vendor"].lower() in t.lower() and rule["category"].lower() in t.lower():
            tr = cli.run("fact", "trace", f["id"], "--project", ids["projects"][client["key"]], scoped=True) or {}
            first = (tr.get("trace") or [{}])[0]
            src = first.get("source_entry_ids") or []
            extracted = dict(id=f["id"], text=t, event=first.get("event"), source_kind=first.get("source_kind"),
                             sources=len(src), same_batch=sorted(src) == sorted(b.get("message_id") for b in batch))
            lines.append(f"    MemoryLake also extracted: “{clip(t, 110)}”")
            lines.append(f"      fact trace: {first.get('event')} · {first.get('source_kind')} · from {len(src)} message(s)"
                         + (" — the same batch" if extracted["same_batch"] else ""))
            break
    if extracted is None:
        lines.append("    (extraction did not write a fact naming this vendor and category this time; the pinned rule is the record)")
    emit("text", "\n".join(lines))
    out = dict(client=client["key"], name=client["name"], rule=rule, message=target["id"], batch=shown, extracted=extracted,
               thread=len(msgs), rest=rest)
    emit("why", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    for c in story["clients"]:
        conv = cli.try_run("conv", "get", f"{PREFIX}-{c['key']}-onboarding", "--by-custom-id", scoped=True)
        if conv:
            # Deleting a project does not delete its conversations.
            cli.run("conv", "delete", conv["id"], scoped=True)
            note(f"deleted the {c['name']} onboarding thread")
        proj = find_project(cli, c)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
            note(f"deleted project “{proj['name']}” (its documents and facts)")
    people = [story["bookkeeper"]["key"]] + [c["owner"]["key"] for c in story["clients"]]
    for key in people:
        a = cli.try_run("actor", "get", f"{PREFIX}-{key}", "--by-custom-id")
        if a:
            cli.run("actor", "delete", a["id"])
    note(f"deleted {len(people)} actor(s)")
    names = {lib_name(f) for f in all_files()}
    removed = 0
    for item in list_library(cli, echo=not quiet):
        if item.get("name") in names:
            cli.run("lib", "delete", item.get("item_id") or item.get("id"))
            removed += 1
    note(f"deleted {removed} uploaded file(s) from the Library")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Clients — one project per client")
    ids = setup(cli, story, reset)
    banner(3, TOTAL, "Onboarding threads — each client's rules, in their words")
    mem = threads_and_rules(cli, story, ids)
    banner(4, TOTAL, "Ledger exports — what each project holds, and how MemoryLake read it")
    files = ledger_files(cli, story, ids)
    ids["broken_now"] = any(f["failed"] for f in files.values() if isinstance(f, dict) and "failed" in f)
    banner(5, TOTAL, f"Categorize — the {story['month']} bank feed, client by client")
    first = categorize(cli, story, ids, mem["rules"])
    files["first_pass"] = first
    banner(6, TOTAL, "Re-sent export — overwrite the file, reload the document")
    after = resend(cli, story, ids, mem["rules"], files)
    banner(7, TOTAL, "Verify — every cited row against the original workbook")
    cites = citations_of(first, after)
    results = verify(cli, ids, cites)
    banner(8, TOTAL, "Why is it booked that way? — rule → message → the batch MemoryLake read")
    whys = []
    for c in story["clients"]:
        r = next(x for x in mem["rules"][c["key"]] if x["vendor"] == "Paylane" and x["line"] == "fee")
        whys.append(why(cli, story, ids, c, r, mem["convs"][c["key"]]))
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   first={k: [r["ok"] for r in v] for k, v in first.items()},
                   first_ok=sum(r["ok"] for v in first.values() for r in v), lines=sum(len(v) for v in first.values()),
                   resent=after and dict(status=after["status"], ok=[r["ok"] for r in after["rows"]]),
                   inspect={k: {file_of(n): [(t["range"], t["rows"]) for t in ts] for n, ts in v["tables"].items()}
                            for k, v in files.items() if isinstance(v, dict) and "tables" in v},
                   failed={k: [f["code"] for f in v["failed"]] for k, v in files.items() if isinstance(v, dict) and "failed" in v},
                   citations=len(cites), verified=sum(r["ok"] for r in results),
                   batches=[len(w["batch"]) for w in whys], extracted=[bool(w["extracted"]) for w in whys])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")
    return summary


def ids_for_reads(cli: CLI) -> dict:
    story = load_story()
    projects = {}
    for c in story["clients"]:
        proj = find_project(cli, c)
        if proj is None:
            die("the client projects do not exist yet; run `python3 demo.py` first")
        projects[c["key"]] = proj["id"]
    return dict(projects=projects)


def rules_on_file(cli: CLI, story: dict, ids: dict) -> dict:
    """The pinned rules, read back from MemoryLake (text + metadata), not from story.json."""
    out = {}
    for c in story["clients"]:
        rules = []
        for f in list_facts(cli, ids["projects"][c["key"]]):
            m = f.get("metadata") or {}
            if m.get("kind") == "rule":
                rules.append(dict(id=f["id"], text=fact_text(f), vendor=m.get("vendor"), line=m.get("line"), match=m.get("match") or "",
                                  category=m.get("category"), treatment=m.get("treatment") or None, turn=m.get("turn"),
                                  reason_turn=m.get("reason_turn"), thread=m.get("thread")))
        out[c["key"]] = rules
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "categorize", "files", "why", "verify", "cleanup"],
                    help="run = full demo (default); categorize [CLIENT] = categorize the bank feed from current memory; "
                         "files = what each project holds (stats + inspect); why [CLIENT] = where the Paylane rule came from; "
                         "verify = re-check out/citations.json against the originals; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="client key for categorize / why (fernhill or marrow)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    story = load_story()
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. `python3 demo.py categorize` re-runs the bank feed against current memory; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    ids = ids_for_reads(cli)
    if args.command == "files":
        banner(2, 2, "Files — what each project holds, and how MemoryLake read it")
        files_report(cli, story, ids)
    elif args.command == "categorize":
        banner(2, 2, "Categorize — the bank feed, from what MemoryLake holds now")
        if args.arg:
            client_by_key(args.arg)
        categorize(cli, story, ids, rules_on_file(cli, story, ids), phase="now", only=args.arg)
    elif args.command == "why":
        banner(2, 2, "Why is Paylane booked that way?")
        rules = rules_on_file(cli, story, ids)
        for c in story["clients"]:
            if args.arg and c["key"] != client_by_key(args.arg)["key"]:
                continue
            r = next((x for x in rules[c["key"]] if x["vendor"] == "Paylane" and x["line"] == "fee"), None)
            if r is None:
                die(f"no Paylane rule on file for {c['name']}")
            why(cli, story, ids, c, r, r["thread"])
    elif args.command == "verify":
        banner(2, 2, "Verify — out/citations.json against the original workbooks")
        path = OUT / "citations.json"
        if not path.exists():
            die("out/citations.json does not exist yet; run `python3 demo.py` first")
        doc = json.loads(path.read_text(encoding="utf-8"))
        note(f"{len(doc['citations'])} cited row(s), exported {doc['generated_at']}")
        verify(cli, ids, doc["citations"])


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

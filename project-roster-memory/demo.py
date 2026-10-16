#!/usr/bin/env python3
"""project-roster-memory — asked about a project it never recorded, the PMO assistant answers anyway.

Corvane Freight (a fictional freight company) keeps its PMO's decision logs in shared memory: six projects, one log
each — budget, vendor, go-live date, top risk, status. A PMO assistant (an ordinary model call through MemoryRouter
with `--boundary`) answers "what go-live date did we set for Project Atlas?" from them, and gets it right.

Then someone asks about Project Beacon, a Finance project the PMO has never tracked. "go-live date" is in all six
logs, but the unfamiliar name is enough: MemoryRouter usually attaches nothing at all, and the model answers on its
own with a confident, specific date — or it attaches Atlas's log and the model hands Atlas's date to Beacon. Search
always returns its top hits; nothing in the reply says the project was never there.

The demo measures two places to put "these are the only projects we track":
  · a roster document in the same boundary — it helps only when MemoryRouter decides to attach it, and an unfamiliar
    name is exactly what makes it attach nothing;
  · the roster as facts in MemoryLake, read by the app with `fact list` and put in the system prompt on every call.
And it shows the deterministic check the app can make before any model call: `fact list --query "Beacon"` on the
roster returns 0, `--query "Atlas"` returns 1.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 100 calls per run, roughly 50,000 tokens).
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-prr"  # memorylake-usecases / project-roster-memory


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

    # Router calls are retried too: a dropped connection before the reply costs nothing to repeat. So are uploads:
    # the demo always uploads with --on-conflict overwrite, so a second attempt replaces the same file.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "chat", "messages", "responses", "balance",
                 "upload"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "internal_error",
                 "http 502", "http 503", "http 504")

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False):
        self.binary, self.env, self.show_json = binary, env, show_json
        self.workspace: str | None = None
        self.secret: str | None = None
        self.base_url = DEFAULT_BASE_URL

    def _echo(self, args: list[str]) -> None:
        shown = [a.replace(self.secret, "sk-…") for a in args] if self.secret else list(args)
        # A multi-line argument (the system prompt with the roster) is shown as its first line; it is printed in full
        # once, above the call.
        shown = [a.split("\n")[0] + f" […{a.count(chr(10))} more lines]" if "\n" in a else a for a in shown]
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
                parsed = self._parse(out)
                # A reply that should be JSON and isn't (seen once on `lib upload`, exit 0): retry what is safe to retry.
                if parsed is not None or not out.strip() or not retryable or attempt >= 4 or args[0] == "router":
                    break
                attempt += 1
                note(f"unexpected reply ({' '.join(out.split())[:80]}); retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup; retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        parsed = self._parse(out)
        return parsed if parsed is not None else (out if out.strip() else None)

    @staticmethod
    def _parse(out: str):
        """The JSON reply, also when something was printed before it; None when there is no JSON."""
        for start in (0, out.find("{"), out.find("[")):
            if start < 0:
                continue
            try:
                return json.loads(out[start:])
            except json.JSONDecodeError:
                continue
        return None

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False, echo: bool = True):
        try:
            return self.run(*args, scoped=scoped, echo=echo)
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
    env.pop("MEMORYLAKE_API_KEY", None)
    api_key = (api_key or os.environ.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)
    cli.base_url = base_url
    rc, out, _ = cli.raw("router", "--help", echo=False)
    if rc != 0:
        die("this memorylake CLI has no `router` command; MemoryRouter arrived in v20261015.\n"
            "  upgrade: curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh")
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
        # `ws list` is newest first: take the team's default workspace, so `run` and `cleanup` land in the same one.
        items = (cli.run("ws", "list", "--page-size", "100") or {}).get("items") or []
        items.sort(key=lambda w: (w.get("custom_id") != "_sys_default_workspace", w.get("created_at") or ""))
        if not items:
            die("this account has no workspace; create one with `memorylake ws create --name … --custom-id …`")
        ws, ws_name = items[0]["id"], items[0]["name"]
        note(f"using workspace “{ws_name}” ({ws})")
    else:
        note(f"using workspace {ws}")
    cli.workspace = ws
    cli.workspace_name = ws_name  # type: ignore[attr-defined]
    cli.router_ok = True  # type: ignore[attr-defined]
    cli.tokens = {"prompt": 0, "completion": 0, "calls": 0}  # type: ignore[attr-defined]
    return cli





MODEL = "gpt-4o-mini"        # answers: router chat (OpenAI Chat Completions)
CHECK_MODEL = "claude-haiku-4-5"  # checker: router messages, copies out what it was given
RUNS = 3                     # answers per question per arm
CHECKS = 2                   # checker calls per question
PROJECTS = ("logs", "doc", "roster")   # MemoryLake projects the demo creates
ARMS = ("logs", "doc", "pinned")       # the three ways of asking
BOUNDARY = {"logs": "logs", "doc": "doc", "pinned": "logs"}  # which boundary each arm asks through


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "pmo.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(key: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{key}.md"


def log_path(key: str) -> Path:
    return DATA / "logs" / f"{key}.md"


def roster_path(d: dict) -> Path:
    return DATA / f"{d['roster_file']}.md"


def roster_fact(p: dict) -> str:
    return f"{p['name']} is a PMO-tracked project: {p['about']}. PMO owner: {p['owner']}."


def roster_block(d: dict, facts: list[str]) -> str:
    """The roster as the app puts it in the system prompt: the facts it read from memory, one per line."""
    return "\n".join([d["roster_header"], *[f"- {f}" for f in facts], d["roster_footer"]])


def system_for(d: dict, arm: str, roster: list[str] | None) -> str:
    return d["system"] + ("\n\n" + roster_block(d, roster or []) if arm == "pinned" else "")


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").replace("$", "").lower().split())


def bare(name: str) -> str:
    """'Project Beacon' → 'Beacon': the word an app would look up."""
    return name.split()[-1]


def logs_view(d: dict) -> list[dict]:
    out = []
    for p in d["projects"]:
        text = log_path(p["key"]).read_text(encoding="utf-8")
        decisions = [l_[2:] for l_ in text.splitlines() if l_.startswith("- ")]
        out.append(dict(key=p["key"], name=p["name"], about=p["about"], owner=p["owner"], sponsor=p["sponsor"],
                        file=f"{p['key']}.md", decisions=decisions, values=p["values"]))
    return out


# --------------------------------------------------------------------------- reading replies

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)
# The reply says the project is not on record: not tracked, not listed, no log, nothing found.
DECLINE = re.compile(r"not tracked|not (?:listed|on (?:the|our) (?:list|roster))|not (?:one of|part of|among)|"
                     r"no (?:record|information|decision log|log|details|data|mention|entry|project)\b|"
                     r"not (?:been )?(?:available|found|mentioned|recorded|included|specified|provided|documented|in (?:the|our))|"
                     r"(?:don't|do not|doesn't|does not) (?:have|appear|contain|include|mention|exist)|"
                     r"there (?:is|are) no\b|unable to|cannot (?:find|provide|determine|confirm)|can't (?:find|provide)|"
                     r"no such project|unknown project", re.I)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply)
    return " ".join((m.group(1) if m else reply.strip().split("\n")[0]).split())


def value_hits(d: dict, text: str) -> dict:
    """Project name → the values from its decision log that appear in the text (budget, vendor, date, people)."""
    t, out = norm(text), {}
    for p in d["projects"]:
        got = [v for v in p["values"] if norm(v) in t]
        if got:
            out[p["name"]] = got
    return out


CLAUSE = re.compile(r"(?<=[a-z0-9)])\.\s+|;\s*|\n+")


def borrowed_values(d: dict, text: str) -> dict:
    """Values from a project's log given in a clause that does not name that project: handed to someone else."""
    out: dict = {}
    for clause in CLAUSE.split(text):
        for name, vals in value_hits(d, clause).items():
            if bare(name).lower() not in clause.lower():
                out.setdefault(name, []).extend(v for v in vals if v not in out.get(name, []))
    return out


def grade(d: dict, q: dict, reply: str, tokens: int, empty: int) -> dict:
    """Every reply is graded, the ∅ ones too: with nothing attached it is still what the PMO reads.
      untracked: right = says it is not tracked / not on record ·
        borrowed = a value from another project's log, in a sentence that does not name that project ·
        invented = neither (a value from nowhere)
      control: right = the logged value · other"""
    whole = " ".join(reply.split())
    a = answer_line(reply)
    nothing = bool(tokens and empty and tokens <= empty)
    hits = value_hits(d, whole)
    out = dict(empty=nothing, clause=a, values=hits)
    if q["group"] == "untracked":
        lent = borrowed_values(d, reply)
        out["values"] = lent
        if lent:
            v = "borrowed"
        elif DECLINE.search(a) or DECLINE.search(whole):
            v = "right"
        else:
            v = "invented"
    else:
        v = "right" if any(norm(o) in norm(whole) for o in q["ok"]) else "other"
    out["verdict"] = v
    return out


MARK = {"right": "✓", "borrowed": "⇄", "invented": "✎", "other": "✗", None: "–"}
ORDER = ("borrowed", "invented", "other", "right", None)
VERDICTS = ("right", "borrowed", "invented", "other")
GROUPS = ("untracked", "control")


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("∅" if a.get("empty") else "")


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, name: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(name), "--by-custom-id", scoped=True, echo=echo)


def demo_boundaries(cli: CLI, echo: bool = False) -> list[dict]:
    # `boundary get --by-custom-id` is not available: list and match the custom_id prefix.
    items, token = [], None
    while True:
        args = ["boundary", "list", "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        items += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            break
    return [b for b in items if (b.get("custom_id") or "").startswith(PREFIX + "-")]


def demo_files(cli: CLI, echo: bool = False) -> list[dict]:
    items, token = [], None
    while True:
        args = ["lib", "list", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None) or {}
        items += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            break
    return [i for i in items if (i.get("name") or "").startswith(PREFIX + "-")]


PROJECT_NAME = {"logs": "PMO decision logs", "doc": "PMO decision logs + roster document", "roster": "PMO project roster"}


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or any(find_project(cli, p) for p in PROJECTS) or demo_files(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for name in PROJECTS:
        p = cli.run("proj", "create", "--name", f"{d['company']} {PROJECT_NAME[name]}", "--custom-id", cid(name),
                    "--description", f"{PROJECT_NAME[name]}. Demo data.", scoped=True)
        world["project"][name] = p["id"]
    for name in ("logs", "doc"):
        b = cli.run("boundary", "create", "--name", f"PMO assistant: {PROJECT_NAME[name]}", "--custom-id", cid(name),
                    "--projects", world["project"][name], scoped=True)
        world["boundary"][name] = b["id"]
    note("three projects: the six decision logs; the same logs plus the roster as a document; the roster as facts. "
         "A boundary over each of the first two. Every answer is `router chat … --boundary`, and MemoryRouter picks "
         "what to attach")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    world: dict = dict(project={}, boundary={})
    for name in PROJECTS:
        p = find_project(cli, name)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["project"][name] = p["id"]
    for name in ("logs", "doc"):
        if cid(name) not in bs:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["boundary"][name] = bs[cid(name)]
    return world


# --------------------------------------------------------------------------- step 3: the decision logs and the roster

def project_docs(cli: CLI, project: str) -> list[dict]:
    page_ = cli.run("proj", "doc", "list", "--project", project, "--page-size", "100", scoped=True, echo=False) or {}
    return page_.get("items") or []


def wait_docs(cli: CLI, project: str, names: set[str]) -> list[dict]:
    for attempt in range(72):
        docs = project_docs(cli, project)
        bad = [x for x in docs if x.get("status") == "error" and x.get("name") in names]
        if bad:
            die(f"{len(bad)} document(s) failed to process: " + ", ".join(x.get("name", "?") for x in bad))
        if names <= {x.get("name") for x in docs if x.get("status") == "okay"}:
            return docs
        time.sleep(5)
    die("the documents are still processing after 6 minutes; run again later")


def upload(cli: CLI, key: str, path: Path, echo: bool = False) -> str:
    return cli.run("lib", "upload", os.path.relpath(path), "--name", lib_name(key), "--on-conflict", "overwrite",
                   echo=echo)["item_id"]


def import_into(cli: CLI, project: str, items: list[str], names: set[str], echo: bool = False) -> None:
    try:
        cli.run("proj", "doc", "import", "--project", project, *items, "--wait", scoped=True, echo=echo)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the documents already submitted")
    wait_docs(cli, project, names)


def fact_text(x: dict) -> str:
    return x.get("fact") or x.get("text") or ""


def roster_facts(cli: CLI, project: str, query: str | None = None, echo: bool = False) -> list[dict]:
    args = ["fact", "list", "--projects", project, "--page-size", "200"] + (["--query", query] if query else [])
    return (cli.run(*args, scoped=True, echo=echo) or {}).get("items") or []


def store(cli: CLI, world: dict) -> dict:
    d = load_data()
    for p in d["projects"]:
        text = log_path(p["key"]).read_text(encoding="utf-8")
        if p["name"] not in text or any(ord(ch) > 127 for ch in text) or any(v not in text for v in p["values"]):
            die(f"data/logs/{p['key']}.md does not match pmo.json (or is not plain ASCII)")
    roster_md = roster_path(d).read_text(encoding="utf-8")
    if any(p["name"] not in roster_md for p in d["projects"]) or any(u["name"] in roster_md for u in d["untracked"]):
        die("data/pmo-project-roster.md does not match pmo.json")
    view = logs_view(d)
    out = [f"\n  {d['company']} PMO — {len(view)} projects, one decision log each, kept by {d['lead']}:"]
    for v in view:
        out.append(f"    {v['name']:<15} {v['about']:<42} {v['file']}")
        out.append(f"         budget {v['values'][0]} dollars · vendor {v['values'][1]} · go-live {v['values'][2]} · "
                   f"sponsor {v['sponsor']}")
    emit("text", "\n".join(out))
    t0 = time.time()
    items = {p["key"]: upload(cli, p["key"], log_path(p["key"]), echo=k == 0) for k, p in enumerate(d["projects"])}
    roster_item = upload(cli, d["roster_file"], roster_path(d), echo=True)
    note(f"{len(items)} decision logs and the roster uploaded to the Library; both document projects import the six "
         "logs, and the second also the roster")
    logs_names = {lib_name(p["key"]) for p in d["projects"]}
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(import_into, cli, world["project"]["logs"], list(items.values()), logs_names, True),
                pool.submit(import_into, cli, world["project"]["doc"], [*items.values(), roster_item],
                            logs_names | {lib_name(d["roster_file"])}, False)]
        for j in jobs:
            j.result()
    note(f"both projects indexed in {int(time.time() - t0)} s")
    emit("text", "\n  The roster, also kept as facts — one per tracked project, stored verbatim:")
    added = cli.run("fact", "add", "--project", world["project"]["roster"], *[roster_fact(p) for p in d["projects"]],
                    scoped=True) or {}
    got = added.get("facts") or []
    for f in got:
        emit("text", f"    · {f.get('fact')}")
    if len(got) != len(d["projects"]):
        die(f"expected {len(d['projects'])} roster facts, the server stored {len(got)}")
    emit("memory", "", logs=view, roster=roster_md, facts=[f.get("fact") for f in got], items=len(items) + 1)
    return dict(items=items, roster_item=roster_item)


# --------------------------------------------------------------------------- step 4: is it in memory?

def index_files(cli: CLI, project: str, query: str, echo: bool = False) -> list[str]:
    """The documents `search --types document` returns for the query, in rank order."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "3",
                  scoped=True, echo=echo) or {}
    return [(doc.get("document_name") or "")[len(PREFIX) + 1:] for doc in res.get("documents") or []]


def lookup(cli: CLI, world: dict) -> dict:
    """For every project name in the questions: does the roster hold it (exact substring, `fact list --query`), and
    what does `search` return for the question itself."""
    d = load_data()
    names = [u["name"] for u in d["untracked"]] + [q["about"] for q in d["questions"] if q["group"] == "control"]
    rows = []
    for k, name in enumerate(names):
        facts = roster_facts(cli, world["project"]["roster"], bare(name), echo=k == 0)
        q = next(q for q in d["questions"] if q["about"] == name)
        files = index_files(cli, world["project"]["logs"], q["say"], echo=k == 0)
        tracked = any(p["name"] == name for p in d["projects"])
        rows.append(dict(name=name, tracked=tracked, roster=len(facts), say=q["say"], search=files))
        emit("text", f"  {name:<16} roster facts matching “{bare(name)}”: {len(facts)}   ·   search “{q['say']}” → "
                     + (", ".join(files) or "nothing"))
    note("search ranks by topic and always returns its top hits — for Beacon, Juniper, Kestrel and Orion too. The "
         "roster lookup is exact: 0 for every project the PMO never tracked, 1 for each one it does")
    emit("lookup", "", rows=rows)
    return dict(rows=rows)


# --------------------------------------------------------------------------- MemoryRouter

def router_call(cli: CLI, args: list[str], echo: bool) -> dict | None:
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("model replies below are skipped; the memory steps still run. Top up in the console to see the replies.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    return res


def count(cli: CLI, prompt: int, reply: int) -> None:
    with LOCK:
        cli.tokens["prompt"] += prompt  # type: ignore[attr-defined]
        cli.tokens["completion"] += reply  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]


def chat(cli: CLI, q: dict, boundary: str | None, system: str, echo: bool = False) -> tuple[str, int] | None:
    args = ["chat", q["say"], "--model", MODEL, "--max-tokens", "150", "--temperature", "0", "--system", system]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return None
    u = res.get("usage") or {}
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return (res["choices"][0]["message"]["content"] or "").strip(), u.get("prompt_tokens", 0)


def baselines(cli: CLI, system: str) -> dict:
    """Each question once without a boundary, with the same system prompt: those prompt tokens are the system prompt
    and the question alone. A boundary call with the same count had nothing attached (∅)."""
    d = load_data()
    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(lambda kq: chat(cli, kq[1], None, system, kq[0] == 0), enumerate(d["questions"])))
    return {q["id"]: (g[1] if g else 0) for q, g in zip(d["questions"], got)}


def answer(cli: CLI, q: dict, boundary: str, system: str, empty: int, echo: bool = False) -> dict:
    got = chat(cli, q, boundary, system, echo)
    if not got:
        return dict(q=q["id"], reply="", prompt_tokens=0, verdict=None, empty=False, clause="", values={})
    text, tokens = got
    return dict(q=q["id"], reply=text, prompt_tokens=tokens, **grade(load_data(), q, text, tokens, empty))


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


# --------------------------------------------------------------------------- step 5: what MemoryRouter attaches

LABEL = re.compile(r"\(([^()\n]*?\.md)\)")


def read_attached(d: dict, text: str, tokens: int, empty: int = 0) -> dict:
    """What the checker copied out: each attached entry's file label (a project's log, or the roster). With no more
    prompt tokens than the same call without a boundary, nothing was attached (∅)."""
    nothing = bool(tokens and empty and tokens <= empty)
    parts = text.split("```")
    block = parts[1] if len(parts) >= 3 else text
    if nothing or block.strip() == "NONE" or len(parts) < 3:
        after = " ".join((parts[-1] if len(parts) >= 3 else "").split())
        return dict(readable=False, declined=not nothing and block.strip() == "NONE", nothing=nothing, entries=[],
                    said=after[:220], raw=text[:4000], tokens=tokens)
    marks = [m for m in LABEL.finditer(block) if m.group(1).startswith(PREFIX + "-")]
    spans = [(m.group(1)[len(PREFIX) + 1:-3], m.end(), marks[k + 1].start() if k + 1 < len(marks) else len(block))
             for k, m in enumerate(marks)]
    names = {p["key"]: p["name"] for p in d["projects"]}
    entries = []
    if not spans and block.strip():
        # Copied without the file labels: attribute by what is in the text — a log's heading or its vendor, the
        # roster's heading or its first line.
        for p in d["projects"]:
            if f"{p['name']} - decision log" in block or p["values"][1] in block:
                entries.append(dict(file=None, key=p["key"], roster=False, project=p["name"]))
        if "PMO project roster" in block or "tracks exactly six projects" in block:
            entries.append(dict(file=None, key="roster", roster=True, project=None))
        if not entries:
            entries.append(dict(file=None, key=None, roster=False, project=None))
    for key, start, end in spans:
        roster = key == d["roster_file"]
        entries.append(dict(file=f"{key}.md", key="roster" if roster else key, roster=roster, project=names.get(key)))
    return dict(readable=True, declined=False, nothing=False, entries=entries, said="", raw=text[:4000], tokens=tokens)


def check(cli: CLI, q: dict, boundary: str | None, empty: int = 0, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given. Its own call: what it is given
    can differ from what the answer calls were given. Without a boundary, its prompt tokens are the baseline."""
    d = load_data()
    args = ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
            "--system", d["checker"]]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return dict(readable=False, declined=False, nothing=False, entries=[], said="", raw="", tokens=0)
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(d, text, u.get("input_tokens", 0), empty)


def entry_line(e: dict) -> str:
    if e["roster"]:
        return f"({e['file'] or 'no file label'}) — the PMO roster"
    if not e["file"]:
        return "(copied without its file label)" + (f" — {e['project']}'s log" if e["project"] else "")
    return f"({e['file']}) — {e['project']}'s log" if e["project"] else f"({e['file']})"


def check_line(c: dict) -> str:
    if c["readable"]:
        return ", ".join(entry_line(e) for e in c["entries"]) or "(no entry)"
    if c["nothing"]:
        return "nothing attached (∅)"
    return f"wrote NONE: “{c['said'][:150]}”" if c["said"] else "wrote NONE"


def show_attached(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    qs = [q for q in d["questions"] if q["group"] == "untracked"]
    note(f"for each question about a project the PMO never tracked, {CHECK_MODEL} is asked {CHECKS}× through the "
         "roster-document boundary to copy out everything above the question (its own call: the attachment can "
         "differ from the answer calls'), and once without a boundary for its ∅ baseline")
    with ThreadPoolExecutor(4) as pool:
        base = list(pool.map(lambda q: check(cli, q, None)["tokens"], qs))
    first = check(cli, qs[0], world["boundary"]["doc"], base[0], echo=True)
    jobs = [(n, k) for n in range(len(qs)) for k in range(CHECKS) if (n, k) != (0, 0)]
    with ThreadPoolExecutor(6) as pool:
        got = dict(zip(jobs, pool.map(lambda nk: check(cli, qs[nk[0]], world["boundary"]["doc"], base[nk[0]]), jobs)))
    got[(0, 0)] = first
    out, raws = [], []
    for n, q in enumerate(qs):
        copies = [got[(n, k)] for k in range(CHECKS)]
        emit("text", f"\n  {q['id']}  {q['say']}\n" + "\n".join(f"      {k + 1}. {check_line(c)}" for k, c in enumerate(copies)))
        out.append(dict(q=q["id"], copies=[{k_: c[k_] for k_ in ("readable", "declined", "nothing", "entries", "said",
                                                                 "tokens")} for c in copies]))
        raws.append(dict(q=q["id"], raw=[c["raw"] for c in copies]))
    copies = [x for c in out for x in c["copies"]]
    n_empty = sum(x["nothing"] for x in copies)
    n_roster = sum(any(e["roster"] for e in x["entries"]) for x in copies)
    n_logs = sum(any(not e["roster"] for e in x["entries"]) for x in copies)
    note(f"{len(copies)} copies: nothing attached in {n_empty}; the roster attached in {n_roster}; another project's "
         f"log attached in {n_logs}")
    emit("attached", "", checks=out, empty=n_empty, roster=n_roster, logs=n_logs, copies=len(copies))
    OUT.mkdir(exist_ok=True)
    (OUT / "checker.json").write_text(json.dumps(raws, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- steps 6–8: ask

def ask_all(cli: CLI, world: dict, arm: str, system: str, base: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"][BOUNDARY[arm]]
    qs = d["questions"]
    first = answer(cli, qs[0], boundary, system, base.get(qs[0]["id"], 0), echo=True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(answer, cli, q, boundary, system, base.get(q["id"], 0))
                for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        emit("text", f"\n  {q['id']}  {q['say']}\n      truth: {q['truth']}")
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        emit("text", f"      answers {' '.join(shown(a) for a in answers)}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], group=q["group"], arm=arm, answers=answers))
        emit("asked", "", q=q["id"], group=q["group"], arm=arm, say=q["say"], truth=q["truth"],
             answers=[dict(verdict=a["verdict"], empty=a["empty"], reply=a["reply"], tokens=a["prompt_tokens"],
                           values=a["values"]) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
        out[g]["answers"] = len(ans)
        out[g]["empty"] = sum(bool(a["empty"]) for a in ans)
        out[g]["wrong_empty"] = sum(bool(a["empty"]) and a["verdict"] != "right" for a in ans)
    return out


def tally_line(t: dict) -> str:
    u, c = t["untracked"], t["control"]
    return (f"never tracked: {u['right']}/{u['answers']} “not tracked” · {u['borrowed']} another project's value · "
            f"{u['invented']} invented" + (f" (∅ {u['empty']})" if u["empty"] else "")
            + f"  ·  controls: {c['right']}/{c['answers']}")


def instead(rows: list[dict]) -> list[str]:
    """What the assistant told the PMO instead of 'not tracked': one line per distinct reply, worst first."""
    order = ["borrowed", "invented", "other"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = " ".join(a["reply"].split())
            if text[:150] in seen:
                continue
            seen.add(text[:150])
            found.append((order.index(a["verdict"]), f"    {shown(a)} {r['q']} [{r['arm']}] “{text[:150]}”"))
    return [line_ for _, line_ in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 9
LOCK = threading.Lock()
ARM_STEP = {"logs": 6, "doc": 7, "pinned": 8}


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — the decision logs, the logs + a roster document, the roster as facts")
    world = setup(cli)

    banner(3, TOTAL, "The PMO's memory — six decision logs, and the roster of tracked projects")
    store(cli, world)

    banner(4, TOTAL, "Is Project Beacon in memory? — the roster lookup vs search")
    looked = lookup(cli, world)

    banner(5, TOTAL, "What MemoryRouter hands the model — asked about a project it never recorded")
    attached = show_attached(cli, world) if cli.router_ok else []  # type: ignore[attr-defined]

    rows: list[dict] = []
    tallies: dict = {}
    base: dict = {}
    roster: list[str] = []
    for arm in ARMS:
        banner(ARM_STEP[arm], TOTAL, f"Ask the PMO assistant — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        if arm == "pinned":
            roster = sorted(fact_text(x) for x in roster_facts(cli, world["project"]["roster"], echo=True))
            emit("text", "\n  system prompt, as the app sends it on every call:\n"
                 + "\n".join("    │ " + x for x in system_for(d, arm, roster).splitlines()))
        system = system_for(d, arm, roster)
        key = "pinned" if arm == "pinned" else "plain"
        if key not in base:
            note("first, each question once without a boundary and with this system prompt: those prompt tokens are "
                 "the prompt alone, so a boundary call with the same count had nothing attached (∅)")
            base[key] = baselines(cli, system)
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ⇄ another "
             "project's value · ✎ invented · ✗ wrong · ∅ nothing attached")
        got = ask_all(cli, world, arm, system, base[key])
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(9, TOTAL, "Scoreboard")
    for arm in ARMS:
        if arm in tallies:
            emit("text", f"  {d['arms'][arm]['short']:<24} {tally_line(tallies[arm])}")
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant told the PMO instead of “not tracked” (⇄ another project's value · "
                     "✎ invented · ∅ nothing attached):\n"
             + "\n".join(said[:12]) + (f"\n    … {len(said) - 12} more in out/report.json" if len(said) > 12 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, lookup=looked, attached=attached, baselines=base, roster=roster,
                   tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         instead=said[:12])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_offline() -> dict:
    """Offline: the six decision logs, the roster document and the roster facts."""
    d = load_data()
    view = logs_view(d)
    for v in view:
        emit("text", f"\n  {v['name']} — {v['about']} — {v['file']} (owner {v['owner']}, sponsor {v['sponsor']})\n"
             + "\n".join(f"      - {x}" for x in v["decisions"]))
    roster_md = roster_path(d).read_text(encoding="utf-8")
    emit("text", f"\n  {d['roster_file']}.md\n" + "\n".join("    │ " + x for x in roster_md.strip().splitlines() if x.strip()))
    emit("text", "\n  the roster as facts:\n" + "\n".join(f"    · {roster_fact(p)}" for p in d["projects"]))
    out = dict(logs=view, roster=roster_md, facts=[roster_fact(p) for p in d["projects"]])
    emit("pmo", "", **out)
    return out


def show_project(cli: CLI, name: str) -> dict:
    """One project name: is it on the roster (`fact list --query`), and what does search return for it."""
    d = load_data()
    world = load_world(cli)
    word = bare(name.strip()).strip(" ?.")
    if not word:
        die("give a project name, e.g. Beacon or \"Project Atlas\"")
    facts = roster_facts(cli, world["project"]["roster"], word, echo=True)
    files = index_files(cli, world["project"]["logs"], f"Project {word[:1].upper() + word[1:]}", echo=True)
    if facts:
        emit("text", f"\n  “{word}” is on the roster ({len(facts)} fact):\n" + "\n".join(f"    · {fact_text(x)}" for x in facts))
    else:
        emit("text", f"\n  “{word}” is not on the roster: `fact list --query` returns 0 facts. The app can answer "
                     "“not tracked by the PMO” without asking a model.")
    emit("text", f"  search for “Project {word}” {'returns' if facts else 'still returns'}: " + (", ".join(files) or "nothing"))
    out = dict(name=word, facts=[fact_text(x) for x in facts], search=files)
    emit("project", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_docs = n_proj = n_facts = 0
    for name in PROJECTS:
        proj = find_project(cli, name, echo=not quiet)
        if not proj:
            continue
        for k, f in enumerate(roster_facts(cli, proj["id"], echo=False)):
            cli.run("fact", "delete", f["id"], "--project", proj["id"], scoped=True, echo=not quiet and k == 0)
            n_facts += 1
        n_docs += len(project_docs(cli, proj["id"]))
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_proj += 1
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundaries, {n_facts} roster fact(s), {n_proj} project(s) ({n_docs} document(s)) and "
             f"{len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "project", "pmo", "cleanup"],
                    help="run = full demo (default); project <name> = is it on the roster, and what search returns; "
                         "pmo = the six decision logs and the roster (offline); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for project: a project name, e.g. Beacon or \"Project Atlas\"")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "pmo":
        banner(1, 1, "PMO — the six decision logs and the roster (offline)")
        show_offline()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "project":
        if not args.args:
            die("usage: python3 demo.py project <name>")
        banner(2, 2, f"Project — “{' '.join(args.args)}”: on the roster? what does search return?")
        show_project(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py project Beacon`; `python3 demo.py cleanup` removes the demo data.")


if __name__ == "__main__":
    try:
        main()
    except DemoError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        sys.exit(1)
    except CLIError as e:
        # The failing command can be `auth login --api-key …`: never print the key.
        shown_cmd = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-…", shlex.join(e.cmd)) if e.cmd else "(memorylake)"
        print(f"\ncommand failed: {shown_cmd}\n{e}", file=sys.stderr)
        sys.exit(e.rc or 1)
    except KeyboardInterrupt:
        sys.exit(130)

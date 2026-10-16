#!/usr/bin/env python3
"""docket-number-memory — two digits swapped in an application number, and the assistant answers for the neighbour.

Halberd & Quill LLP (a fictional IP firm) keeps its prior art search memos in shared memory: six US applications,
one memo each — client, who searched, when, the closest reference, the conclusion. A prior-art assistant (an
ordinary model call through MemoryRouter with `--boundary`) answers "what was the closest reference in our search for
17/482,907?" from them, and gets it right.

Then an attorney asks about 17/428,907 — two digits swapped; the firm has never searched it. The number is close
enough that MemoryRouter attaches the memo for 17/482,907, and the model answers with that memo's reference, date
and searcher, repeating the number it was asked about. Nothing in the reply says the memo is for another
application. Search ranks the neighbour first too.

The demo measures two places to put "these are the applications we have searched":
  · the docket as facts in MemoryLake, read by the app with `fact list` and put in the system prompt — it stops most
    near misses, but the model can also "correct" 17/428,907 to 17/482,907 and answer for that;
  · an exact check in the app before any model call: normalise the number, `fact list --query "17/428,907"` on the
    docket — 0 facts means no model call, just "no search on file" and the nearest docket number to check against.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 90 calls per run, roughly 35,000 tokens).
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

PREFIX = "mlu-dnm"  # memorylake-usecases / docket-number-memory


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
        # A multi-line argument (the system prompt with the docket) is shown as its first line; it is printed in full
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
PROJECTS = ("memos", "docket")          # MemoryLake projects the demo creates
ARMS = ("memos", "pinned", "gate")      # the three ways of asking


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "firm.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(key: str) -> str:
    """The Library file name, which is also the label MemoryRouter puts on every entry it attaches."""
    return f"{PREFIX}-{key}.md"


def memo_path(key: str) -> Path:
    return DATA / "memos" / f"{key}.md"


def docket_fact(m: dict) -> str:
    return f"US Application No. {m['number']} - prior art search on file: {m['title']} (client {m['client']})."


def docket_block(d: dict, facts: list[str]) -> str:
    """The docket as the app puts it in the system prompt: the facts it read from memory, one per line."""
    return "\n".join([d["docket_header"], *[f"- {f}" for f in facts], d["docket_footer"]])


def system_for(d: dict, arm: str, docket: list[str] | None) -> str:
    return d["system"] + ("\n\n" + docket_block(d, docket or []) if arm == "pinned" else "")


NUMBER = re.compile(r"(?<![\d/])(\d{2})\s*/?\s*(\d{3})\s*,?\s*(\d{3})(?![\d,])")


def canonical(text: str) -> str | None:
    """The first US application number in the text, written the way the docket writes it: 17/482,907.
    `fact list --query` matches text exactly, so 17/482907 or 17482907 must be rewritten before the lookup."""
    m = NUMBER.search(text)
    return f"{m.group(1)}/{m.group(2)},{m.group(3)}" if m else None


def digits(number: str) -> str:
    return re.sub(r"\D", "", number)


def distance(a: str, b: str) -> tuple[int, str]:
    """Edit distance between two digit strings, counting a swap of two adjacent digits as one edit, and a word for it."""
    a, b = digits(a), digits(b)
    if len(a) == len(b):
        diff = [k for k in range(len(a)) if a[k] != b[k]]
        if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
            return 1, "two adjacent digits swapped"
        if len(diff) == 2 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
            return 2, "two digits swapped"
        if len(diff) == 1:
            return 1, "one digit different"
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1], f"{prev[-1]} digits different"


def nearest(number: str, docket: list[str]) -> tuple[str, str] | None:
    """The docket number closest to the given one, if it is within two edits."""
    best = sorted((distance(number, n) + (n,) for n in docket), key=lambda x: x[0])
    return (best[0][2], best[0][1]) if best and best[0][0] <= 2 else None


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").lower().split())


def memos_view(d: dict) -> list[dict]:
    out = []
    for m in d["memos"]:
        out.append(dict(key=m["key"], number=m["number"], title=m["title"], client=m["client"], searcher=m["searcher"],
                        date=m["date"], ref=m["ref"], file=f"{m['key']}.md",
                        conclusion=re.search(r"^Conclusion: (.+)$", memo_path(m["key"]).read_text(encoding="utf-8"),
                                             re.M).group(1)))
    return out


# --------------------------------------------------------------------------- reading replies

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)
# The reply says there is no search on file for this number.
DECLINE = re.compile(r"no (?:prior art )?search (?:is |was )?(?:on file|found|recorded|available)|not on file|"
                     r"no (?:record|information|memo|data|details|entry|results?)\b|"
                     r"not (?:been )?(?:available|found|mentioned|recorded|included|specified|provided|documented|listed)|"
                     r"(?:don't|do not|doesn't|does not) (?:have|appear|contain|include|mention|exist)|"
                     r"there (?:is|are|was) no\b|unable to|cannot (?:find|provide|determine|confirm)|can't (?:find|provide)", re.I)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply)
    return " ".join((m.group(1) if m else reply.strip().split("\n")[0]).split())


def value_hits(d: dict, text: str) -> dict:
    """Application number → the values from its memo that appear in the text (reference, inventor, date, searcher)."""
    t, out = norm(text), {}
    for m in d["memos"]:
        got = [v for v in m["values"] if norm(v) in t]
        if got:
            out[m["number"]] = got
    return out


def grade(d: dict, q: dict, reply: str, tokens: int, empty: int) -> dict:
    """Every reply is graded, the ∅ ones too: with nothing attached it is still what the attorney reads.
      nearmiss: right = the answer line says no search is on file (or, with no value from any memo, the reply does) ·
        borrowed = a value from a memo (an answer that belongs to another application) ·
        invented = neither (a value from nowhere)
        renumbered = the reply writes the docket number instead of the one asked about
      control: right = the memo's value · other"""
    whole = " ".join(reply.split())
    a = answer_line(reply)
    nothing = bool(tokens and empty and tokens <= empty)
    hits = value_hits(d, whole)
    out = dict(empty=nothing, clause=a, values=hits, renumbered=False)
    if q["group"] == "nearmiss":
        out["renumbered"] = q["near"] in whole and q["number"] not in whole
        if DECLINE.search(a):
            v = "right"  # declines first; a "did you mean" naming the neighbour afterwards is not an answer for it
        elif hits:
            v = "borrowed"
        elif DECLINE.search(whole):
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
GROUPS = ("nearmiss", "control")


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("↻" if a.get("renumbered") else "") + ("∅" if a.get("empty") else "")


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


PROJECT_NAME = {"memos": "prior art search memos", "docket": "prior art search docket"}


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or any(find_project(cli, p) for p in PROJECTS) or demo_files(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    for name in PROJECTS:
        p = cli.run("proj", "create", "--name", f"{d['firm']} {PROJECT_NAME[name]}", "--custom-id", cid(name),
                    "--description", f"{PROJECT_NAME[name].capitalize()}. Demo data.", scoped=True)
        world["project"][name] = p["id"]
    b = cli.run("boundary", "create", "--name", "Prior-art assistant: search memos", "--custom-id", cid("memos"),
                "--projects", world["project"]["memos"], scoped=True)
    world["boundary"]["memos"] = b["id"]
    note("two projects: the six search memos (documents), and the docket of application numbers with a search on file "
         "(facts). One boundary, over the memos. Every answer is `router chat … --boundary`, and MemoryRouter picks "
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
    if cid("memos") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    world["boundary"]["memos"] = bs[cid("memos")]
    return world


# --------------------------------------------------------------------------- step 3: the memos and the docket

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


def docket_facts(cli: CLI, project: str, query: str | None = None, echo: bool = False) -> list[dict]:
    args = ["fact", "list", "--projects", project, "--page-size", "200"] + (["--query", query] if query else [])
    return (cli.run(*args, scoped=True, echo=echo) or {}).get("items") or []


def store(cli: CLI, world: dict) -> dict:
    d = load_data()
    for m in d["memos"]:
        text = memo_path(m["key"]).read_text(encoding="utf-8")
        if f"No. {m['number']}" not in text or any(ord(ch) > 127 for ch in text) or any(
                v not in text for v in (m["ref"], m["searcher"], m["date"], m["title"])):
            die(f"data/memos/{m['key']}.md does not match firm.json (or is not plain ASCII)")
    view = memos_view(d)
    out = [f"\n  {d['firm']} {d['team']} — {len(view)} prior art search memos, one per US application:"]
    for v in view:
        out.append(f"    {v['number']:<11} {v['title']:<46} {v['file']}")
        out.append(f"                closest reference {v['ref']} · searched {v['date']} by {v['searcher']}")
    emit("text", "\n".join(out))
    t0 = time.time()
    items = {m["key"]: upload(cli, m["key"], memo_path(m["key"]), echo=k == 0) for k, m in enumerate(d["memos"])}
    note(f"{len(items)} memos uploaded to the Library and imported into the memos project")
    import_into(cli, world["project"]["memos"], list(items.values()), {lib_name(m["key"]) for m in d["memos"]}, True)
    note(f"indexed in {int(time.time() - t0)} s")
    emit("text", "\n  The docket — one fact per application with a search on file, stored verbatim:")
    added = cli.run("fact", "add", "--project", world["project"]["docket"], *[docket_fact(m) for m in d["memos"]],
                    scoped=True) or {}
    got = added.get("facts") or []
    for f in got:
        emit("text", f"    · {f.get('fact')}")
    if len(got) != len(d["memos"]):
        die(f"expected {len(d['memos'])} docket facts, the server stored {len(got)}")
    emit("memory", "", memos=view, facts=[f.get("fact") for f in got], items=len(items))
    return dict(items=items)


# --------------------------------------------------------------------------- step 4: is it on file?

def index_files(cli: CLI, project: str, query: str, echo: bool = False) -> list[str]:
    """The documents `search --types document` returns for the query, in rank order."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "3",
                  scoped=True, echo=echo) or {}
    return [(doc.get("document_name") or "")[len(PREFIX) + 1:] for doc in res.get("documents") or []]


def lookup(cli: CLI, world: dict) -> dict:
    """For every number in the questions: is it on the docket (`fact list --query`, exact), and what does `search`
    return for the question itself. Then the same lookup with the number written without its comma."""
    d = load_data()
    rows = []
    for k, q in enumerate(d["questions"]):
        facts = docket_facts(cli, world["project"]["docket"], q["number"], echo=k == 0)
        files = index_files(cli, world["project"]["memos"], q["say"], echo=k == 0)
        rows.append(dict(q=q["id"], number=q["number"], group=q["group"], docket=len(facts), search=files))
        emit("text", f"  {q['number']:<11} docket facts matching: {len(facts)}   ·   search “{q['say'][:44]}…” → "
                     + (", ".join(files) or "nothing"))
    note("search ranks by similarity and always returns its top hits: for every near miss, the first one is the memo "
         "of the application two digits away. The docket lookup is exact: 0 for each near miss, 1 for each number "
         "on file")
    raw = digits(d["questions"][4]["number"])
    loose = f"{raw[:2]}/{raw[2:]}"
    facts = docket_facts(cli, world["project"]["docket"], loose, echo=True)
    emit("text", f"  {loose:<11} docket facts matching: {len(facts)}   (the same number as {d['questions'][4]['number']}, "
                 "written without its comma)")
    note("`--query` matches the text as written: the app normalises the number to NN/NNN,NNN before it looks it up")
    emit("lookup", "", rows=rows, loose=dict(number=loose, docket=len(facts)))
    return dict(rows=rows, loose=dict(number=loose, docket=len(facts)))


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



# --------------------------------------------------------------------------- step 5: what MemoryRouter attaches

LABEL = re.compile(r"\(([^()\n]*?\.md)\)")


def read_attached(d: dict, text: str, tokens: int, empty: int = 0) -> dict:
    """What the checker copied out: each attached entry's file label (a memo, named by its application number). With
    no more prompt tokens than the same call without a boundary, nothing was attached (∅)."""
    nothing = bool(tokens and empty and tokens <= empty)
    parts = text.split("```")
    block = parts[1] if len(parts) >= 3 else text
    if nothing or block.strip() == "NONE" or len(parts) < 3:
        after = " ".join((parts[-1] if len(parts) >= 3 else "").split())
        return dict(readable=False, declined=not nothing and block.strip() == "NONE", nothing=nothing, entries=[],
                    said=after[:220], raw=text[:4000], tokens=tokens)
    marks = [m for m in LABEL.finditer(block) if m.group(1).startswith(PREFIX + "-")]
    numbers = {m["key"]: m["number"] for m in d["memos"]}
    entries = []
    seen = set()
    for m in marks:
        key = m.group(1)[len(PREFIX) + 1:-3]
        if key not in seen:
            seen.add(key)
            entries.append(dict(file=f"{key}.md", number=numbers.get(key)))
    if not entries and block.strip():
        # Copied without the file labels: attribute by the memo heading in the text.
        for m in d["memos"]:
            if f"No. {m['number']}" in block:
                entries.append(dict(file=None, number=m["number"]))
        if not entries:
            entries.append(dict(file=None, number=None))
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


def entry_line(e: dict, q: dict) -> str:
    tag = "  ← the near number" if e["number"] and e["number"] == q.get("near") else ""
    if not e["file"]:
        return "(copied without its file label)" + (f" — the memo for {e['number']}{tag}" if e["number"] else "")
    return f"({e['file']}) — the memo for {e['number']}{tag}" if e["number"] else f"({e['file']})"


def check_line(c: dict, q: dict) -> str:
    if c["readable"]:
        return ", ".join(entry_line(e, q) for e in c["entries"]) or "(no entry)"
    if c["nothing"]:
        return "nothing attached (∅)"
    return f"wrote NONE: “{c['said'][:150]}”" if c["said"] else "wrote NONE"


def show_attached(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    qs = [q for q in d["questions"] if q["group"] == "nearmiss"]
    note(f"for each near-miss number, {CHECK_MODEL} is asked {CHECKS}× through the memos boundary to copy out "
         "everything above the question (its own call: the attachment can differ from the answer calls'), and once "
         "without a boundary for its ∅ baseline")
    with ThreadPoolExecutor(4) as pool:
        base = list(pool.map(lambda q: check(cli, q, None)["tokens"], qs))
    first = check(cli, qs[0], world["boundary"]["memos"], base[0], echo=True)
    jobs = [(n, k) for n in range(len(qs)) for k in range(CHECKS) if (n, k) != (0, 0)]
    with ThreadPoolExecutor(6) as pool:
        got = dict(zip(jobs, pool.map(lambda nk: check(cli, qs[nk[0]], world["boundary"]["memos"], base[nk[0]]), jobs)))
    got[(0, 0)] = first
    out, raws = [], []
    for n, q in enumerate(qs):
        copies = [got[(n, k)] for k in range(CHECKS)]
        emit("text", f"\n  {q['id']}  {q['say']}   (on file: {q['near']})\n"
             + "\n".join(f"      {k + 1}. {check_line(c, q)}" for k, c in enumerate(copies)))
        out.append(dict(q=q["id"], number=q["number"], near=q["near"],
                        copies=[{k_: c[k_] for k_ in ("readable", "declined", "nothing", "entries", "said", "tokens")}
                                for c in copies]))
        raws.append(dict(q=q["id"], raw=[c["raw"] for c in copies]))
    copies = [(c["near"], x) for c in out for x in c["copies"]]
    n_empty = sum(x["nothing"] for _, x in copies)
    n_near = sum(any(e["number"] == near for e in x["entries"]) for near, x in copies)
    n_first = sum(bool(x["entries"]) and x["entries"][0]["number"] == near for near, x in copies)
    note(f"{len(copies)} copies: the memo of the number two digits away attached in {n_near} (listed first in "
         f"{n_first}); nothing attached in {n_empty}")
    emit("attached", "", checks=out, empty=n_empty, near=n_near, first=n_first, copies=len(copies))
    OUT.mkdir(exist_ok=True)
    (OUT / "checker.json").write_text(json.dumps(raws, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- steps 6–8: ask

def gate(cli: CLI, world: dict, q: dict, docket: list[str], base: int, echo: bool = False) -> dict:
    """The app's own check before any model call: normalise the number, look it up exactly on the docket. No match:
    no model call, the reply says so and names the nearest docket number. A match: the question goes to the memos
    boundary as in step 6."""
    d = load_data()
    number = canonical(q["say"])
    facts = docket_facts(cli, world["project"]["docket"], number, echo=echo) if number else []
    if number and not facts:
        near = nearest(number, docket)
        reply = (f"Answer: No prior art search is on file for US application {number}."
                 + (f" The nearest docket number is {near[0]} ({near[1]}): check the number before relying on that "
                    f"memo." if near else ""))
        return dict(q=q["id"], reply=reply, prompt_tokens=0, verdict="right" if q["group"] == "nearmiss" else "other",
                    empty=False, clause=answer_line(reply), values={}, renumbered=False, model=False,
                    lookup=dict(number=number, docket=0, nearest=near))
    a = answer(cli, q, world["boundary"]["memos"], d["system"], base, echo=echo)
    a.update(model=True, lookup=dict(number=number, docket=len(facts), nearest=None))
    return a


def ask_all(cli: CLI, world: dict, arm: str, system: str, base: dict, docket: list[str]) -> list[dict]:
    d = load_data()
    boundary = world["boundary"]["memos"]
    qs = d["questions"]
    ask = (lambda q, echo=False: gate(cli, world, q, docket, base.get(q["id"], 0), echo)) if arm == "gate" else \
        (lambda q, echo=False: answer(cli, q, boundary, system, base.get(q["id"], 0), echo))
    first = ask(qs[0], True)
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    with ThreadPoolExecutor(6) as pool:
        futs = {(q["id"], k): pool.submit(ask, q) for n, q in enumerate(qs) for k in range(RUNS) if (n, k) != (0, 0)}
        got = {key: f.result() for key, f in futs.items()}
    if not cli.router_ok:  # type: ignore[attr-defined]
        return []
    rows = []
    for n, q in enumerate(qs):
        emit("text", f"\n  {q['id']}  {q['say']}\n      truth: {q['truth']}")
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        how = "" if arm != "gate" else ("   [docket: 0 — no model call]" if not pick.get("model") else "   [docket: 1]")
        emit("text", f"      answers {' '.join(shown(a) for a in answers)}{how}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:170] + "”")
        rows.append(dict(q=q["id"], group=q["group"], arm=arm, answers=answers))
        emit("asked", "", q=q["id"], group=q["group"], arm=arm, say=q["say"], truth=q["truth"],
             answers=[dict(verdict=a["verdict"], empty=a["empty"], renumbered=a.get("renumbered", False),
                           reply=a["reply"], tokens=a["prompt_tokens"], values=a["values"],
                           model=a.get("model", True)) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    out: dict = {}
    for g in GROUPS:
        ans = [a for r in rows if r["group"] == g for a in r["answers"] if a["verdict"]]
        out[g] = {k: sum(a["verdict"] == k for a in ans) for k in VERDICTS}
        out[g]["answers"] = len(ans)
        out[g]["empty"] = sum(bool(a["empty"]) for a in ans)
        out[g]["renumbered"] = sum(bool(a.get("renumbered")) for a in ans)
        out[g]["calls"] = sum(a.get("model", True) for a in ans)
    return out


def tally_line(t: dict) -> str:
    u, c = t["nearmiss"], t["control"]
    return (f"near misses: {u['right']}/{u['answers']} “not on file” · {u['borrowed']} another application's answer"
            + (f" ({u['renumbered']} rewrote the number)" if u["renumbered"] else "")
            + f" · {u['invented']} invented" + (f" (∅ {u['empty']})" if u["empty"] else "")
            + f"  ·  controls: {c['right']}/{c['answers']}")


def instead(rows: list[dict]) -> list[str]:
    """What the assistant told the attorney instead of 'no search on file': one line per distinct reply, worst first."""
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
ARM_STEP = {"memos": 6, "pinned": 7, "gate": 8}


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — the search memos, and the docket of numbers with a search on file")
    world = setup(cli)

    banner(3, TOTAL, "The IP group's memory — six prior art search memos, and the docket")
    store(cli, world)

    banner(4, TOTAL, "Is 17/428,907 on file? — the docket lookup vs search")
    looked = lookup(cli, world)

    banner(5, TOTAL, "What MemoryRouter hands the model — asked about a number two digits away")
    attached = show_attached(cli, world) if cli.router_ok else []  # type: ignore[attr-defined]

    rows: list[dict] = []
    tallies: dict = {}
    base: dict = {}
    docket: list[str] = []
    for arm in ARMS:
        banner(ARM_STEP[arm], TOTAL, f"Ask the prior-art assistant — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        if arm in ("pinned", "gate") and not docket:
            facts = docket_facts(cli, world["project"]["docket"], echo=True)
            docket = sorted(fact_text(x) for x in facts)
        if arm == "pinned":
            emit("text", "\n  system prompt, as the app sends it on every call:\n"
                 + "\n".join("    │ " + x for x in system_for(d, arm, docket).splitlines()))
        if arm == "gate":
            note("before any model call the app reads the number out of the question, writes it as NN/NNN,NNN and "
                 "runs `fact list --query <number>` on the docket. 0 facts: no model call — the app replies itself "
                 "and names the nearest docket number. 1 fact: the question goes to the memos boundary as in step 6")
        system = system_for(d, arm, docket)
        key = "pinned" if arm == "pinned" else "plain"
        if key not in base:
            note("first, each question once without a boundary and with this system prompt: those prompt tokens are "
                 "the prompt alone, so a boundary call with the same count had nothing attached (∅)")
            base[key] = baselines(cli, system)
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ⇄ another "
             "application's answer (↻ with the number rewritten) · ✎ invented · ✗ wrong · ∅ nothing attached")
        numbers = [canonical(f) for f in docket]
        got = ask_all(cli, world, arm, system, base[key], [n for n in numbers if n])
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}"
                     + (f"  ·  model calls {tallies[arm]['nearmiss']['calls'] + tallies[arm]['control']['calls']}"
                        f"/{tallies[arm]['nearmiss']['answers'] + tallies[arm]['control']['answers']}" if arm == "gate" else ""))
        emit("tally", "", arm=arm, **tallies[arm])

    banner(9, TOTAL, "Scoreboard")
    for arm in ARMS:
        if arm in tallies:
            emit("text", f"  {d['arms'][arm]['short']:<24} {tally_line(tallies[arm])}")
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant told the attorney instead of “no search on file” (⇄ another application's "
                     "answer · ↻ number rewritten · ✎ invented · ∅ nothing attached):\n"
             + "\n".join(said[:12]) + (f"\n    … {len(said) - 12} more in out/report.json" if len(said) > 12 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, lookup=looked, attached=attached, baselines=base, docket=docket,
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
    """Offline: the six search memos and the docket facts."""
    d = load_data()
    view = memos_view(d)
    for v in view:
        emit("text", f"\n  {v['number']} — {v['title']} — {v['file']} (client {v['client']})\n"
                     f"      searched {v['date']} by {v['searcher']} · closest reference {v['ref']}\n"
                     f"      {v['conclusion']}")
    emit("text", "\n  the docket as facts:\n" + "\n".join(f"    · {docket_fact(m)}" for m in d["memos"]))
    out = dict(memos=view, facts=[docket_fact(m) for m in d["memos"]])
    emit("firm", "", **out)
    return out


def show_number(cli: CLI, text: str) -> dict:
    """One application number: normalised, looked up exactly on the docket; if absent, the nearest docket number.
    And what search returns for it."""
    world = load_world(cli)
    number = canonical(text)
    if not number:
        die("give a US application number, e.g. 17/428,907 or 17428907")
    if number != text.strip():
        note(f"“{text.strip()}” written as the docket writes numbers: {number}")
    facts = docket_facts(cli, world["project"]["docket"], number, echo=True)
    files = index_files(cli, world["project"]["memos"], f"US Application No. {number}", echo=True)
    near = None
    if facts:
        emit("text", f"\n  {number} is on the docket ({len(facts)} fact):\n" + "\n".join(f"    · {fact_text(x)}" for x in facts))
    else:
        docket = [n for n in (canonical(fact_text(x)) for x in docket_facts(cli, world["project"]["docket"])) if n]
        near = nearest(number, docket)
        emit("text", f"\n  {number} is not on the docket: `fact list --query` returns 0 facts. The app can answer "
                     "“no prior art search on file” without asking a model."
             + (f"\n  nearest docket number: {near[0]} ({near[1]})" if near else ""))
    emit("text", f"  search for “US Application No. {number}” {'returns' if facts else 'still returns'}: "
         + (", ".join(files) or "nothing"))
    out = dict(number=number, facts=[fact_text(x) for x in facts], search=files, nearest=near)
    emit("number", "", **out)
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
        for k, f in enumerate(docket_facts(cli, proj["id"], echo=False)):
            cli.run("fact", "delete", f["id"], "--project", proj["id"], scoped=True, echo=not quiet and k == 0)
            n_facts += 1
        n_docs += len(project_docs(cli, proj["id"]))
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_proj += 1
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundary, {n_facts} docket fact(s), {n_proj} project(s) ({n_docs} document(s)) and "
             f"{len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "docket", "memos", "cleanup"],
                    help="run = full demo (default); docket <number> = is it on file, the nearest docket number, "
                         "and what search returns; memos = the six memos and the docket (offline); "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for docket: a US application number, e.g. 17/428,907")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "memos":
        banner(1, 1, "Memos — the six prior art search memos and the docket (offline)")
        show_offline()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "docket":
        if not args.args:
            die("usage: python3 demo.py docket <number>")
        banner(2, 2, f"Docket — “{' '.join(args.args)}”: on file? nearest number? what does search return?")
        show_number(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py docket 17/428,907`; `python3 demo.py cleanup` removes the demo data.")


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

#!/usr/bin/env python3
"""empty-retrieval-memory — the on-call assistant says "No record", and you cannot tell whether that is true.

Larkspur Pay (a fictional payments company) keeps its platform runbooks in shared memory: six short documents —
rollback sign-off, Postgres failover, partner rate limits, Sev1 escalation, certificate rotation, the holiday change
freeze. An on-call assistant (an ordinary model call through MemoryRouter with `--boundary`) answers from them.

Some calls come back with nothing attached: MemoryRouter found no page for the question as it was typed, and the
reply does not say so. With a plain system prompt, the model then answers anyway, from nowhere ("We keep payment
webhook logs for 90 days"). The usual fix is one more sentence in the prompt: "If you do not know, reply 'No record.'
Do not guess." It works: nothing is invented any more. But now a retrieval miss and a question no runbook covers get
the same reply, "No record", and the engineer on call cannot tell them apart.

The demo asks ten questions three ways:
  · the plain system prompt;
  · the "Do not guess" prompt;
  · the "Do not guess" prompt, and the app watches each call: prompt tokens no higher than the same call without a
    boundary mean MemoryRouter attached nothing (∅). Then the app runs `search --types document --top-k 1` itself,
    asks again with that page, and labels where the answer came from.

Model replies can vary from run to run; the README reports totals over all runs.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 140 calls per run, roughly 30,000 tokens).
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

PREFIX = "mlu-erm"  # memorylake-usecases / empty-retrieval-memory


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
        # A multi-line argument (a system prompt with a runbook page) is shown as its first line; it is printed in full
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
PROJECTS = ("runbooks",)                     # the MemoryLake project the demo creates
ARMS = ("plain", "noguess", "observed")     # the three ways of asking


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "oncall.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def lib_name(key: str) -> str:
    """The Library file name: prefixed, so cleanup can find it; MemoryRouter labels attached pages with it."""
    return f"{PREFIX}-{key}.md"


def runbook_path(key: str) -> Path:
    return DATA / "runbooks" / f"{key}.md"


def runbook_key(name: str) -> str | None:
    """`mlu-erm-rollback.md` → `rollback`."""
    name = name or ""
    if name.startswith(PREFIX + "-") and name.endswith(".md"):
        return name[len(PREFIX) + 1:-3]
    return None


def norm(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("–", "-").replace("—", "-").replace("“", '"').replace("”", '"')
                    .lower().split())


# --------------------------------------------------------------------------- reading replies

ANSWER = re.compile(r"^\s*\**answer\**\s*:\s*(.+)$", re.I | re.M)
# The reply says the runbooks do not have it.
NO_RECORD = re.compile(r"no record|no (?:information|runbook|data|details|entry|mention)\b|"
                       r"not (?:been )?(?:available|found|mentioned|recorded|included|specified|provided|documented|"
                       r"listed|given|stated|covered)|"
                       r"(?:don't|do not|doesn't|does not) (?:have|appear|contain|include|mention|specify|cover)|"
                       r"there (?:is|are|was) no\b|unable to|cannot (?:find|provide|determine|confirm)|"
                       r"can't (?:find|provide)", re.I)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply)
    return " ".join((m.group(1) if m else reply.strip().split("\n")[0]).split())


def grade(q: dict, reply: str, tokens: int, empty: int) -> dict:
    """Every reply is graded, the ∅ ones too: with nothing attached it is still what the engineer on call reads.
      right = the runbook's answer, or “No record” where no runbook covers it ·
      missed = “No record” although a runbook has the answer · invented = anything else"""
    whole = norm(reply)
    nothing = bool(tokens and empty and tokens <= empty)
    said_none = bool(NO_RECORD.search(answer_line(reply)))
    if q["runbook"] is None:
        v = "right" if said_none or NO_RECORD.search(whole) else "invented"
    elif any(t in whole for t in q["truth"]) and not said_none:
        v = "right"
    elif said_none:
        v = "missed"
    else:
        v = "invented"
    return dict(empty=nothing, clause=answer_line(reply), verdict=v, said_none=said_none)


MARK = {"right": "✓", "missed": "–", "invented": "✎", None: "·"}
ORDER = ("invented", "missed", "right", None)
VERDICTS = ("right", "missed", "invented")


def shown(a: dict) -> str:
    return MARK[a["verdict"]] + ("∅" if a.get("empty") else "") + ("↻" if a.get("source", "").startswith("search") else "")


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


def setup(cli: CLI) -> dict:
    if demo_boundaries(cli) or any(find_project(cli, p) for p in PROJECTS) or demo_files(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    d = load_data()
    world: dict = dict(project={}, boundary={})
    p = cli.run("proj", "create", "--name", f"{d['company']} runbooks", "--custom-id", cid("runbooks"),
                "--description", "Platform on-call runbooks. Demo data.", scoped=True)
    world["project"]["runbooks"] = p["id"]
    b = cli.run("boundary", "create", "--name", "On-call assistant: runbooks", "--custom-id", cid("runbooks"),
                "--projects", world["project"]["runbooks"], scoped=True)
    world["boundary"]["runbooks"] = b["id"]
    note("one project for the six runbooks (documents) and one boundary over it. Every answer is "
         "`router chat … --boundary`: MemoryRouter picks what to attach, and the reply never says what it picked")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    bs = {x["custom_id"]: x["id"] for x in demo_boundaries(cli)}
    p = find_project(cli, "runbooks")
    if not p or cid("runbooks") not in bs:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(project={"runbooks": p["id"]}, boundary={"runbooks": bs[cid("runbooks")]})


# --------------------------------------------------------------------------- step 3: the runbooks

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


def upload(cli: CLI, key: str, echo: bool = False) -> str:
    return cli.run("lib", "upload", os.path.relpath(runbook_path(key)), "--name", lib_name(key),
                   "--on-conflict", "overwrite", echo=echo)["item_id"]


def runbooks_view(d: dict) -> list[dict]:
    out = []
    for r in d["runbooks"]:
        text = runbook_path(r["key"]).read_text(encoding="utf-8")
        body = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#") and not ln.startswith("Owner:")]
        out.append(dict(key=r["key"], title=r["title"], file=f"{r['key']}.md", first=body[0] if body else ""))
    return out


def store(cli: CLI, world: dict) -> dict:
    d = load_data()
    for r in d["runbooks"]:
        text = runbook_path(r["key"]).read_text(encoding="utf-8")
        if r["title"] not in text or any(ord(ch) > 127 for ch in text):
            die(f"data/runbooks/{r['key']}.md does not match oncall.json (or is not plain ASCII)")
    for q in d["questions"]:
        if q["runbook"] and not any(t in norm(runbook_path(q["runbook"]).read_text(encoding="utf-8")) for t in q["truth"]):
            die(f"{q['id']}: its answer is not in data/runbooks/{q['runbook']}.md")
    view = runbooks_view(d)
    out = [f"\n  {d['company']} {d['team']} — {len(view)} runbooks:"]
    for v in view:
        out.append(f"    {v['file']:<16} {v['title']}")
        out.append(f"        {v['first'][:104]}")
    emit("text", "\n".join(out))
    t0 = time.time()
    items = {r["key"]: upload(cli, r["key"], echo=k == 0) for k, r in enumerate(d["runbooks"])}
    note(f"{len(items)} runbooks uploaded to the Library; importing them into the runbooks project")
    project = world["project"]["runbooks"]
    try:
        cli.run("proj", "doc", "import", "--project", project, *items.values(), "--wait", scoped=True)
    except CLIError as e:
        if not e.has(*CLI.TRANSIENT):
            raise
        note("the import call hit a network error; waiting for the documents already submitted")
    wait_docs(cli, project, {lib_name(r["key"]) for r in d["runbooks"]})
    note(f"indexed in {int(time.time() - t0)} s")
    emit("memory", "", runbooks=view, items=len(items))
    return dict(items=items)


# --------------------------------------------------------------------------- step 4: can the index find them?

def search_page(cli: CLI, project: str, query: str, echo: bool = False) -> dict:
    """The top document `search --types document --top-k 1` returns for the question: its file and page text."""
    res = cli.run("search", query, "--projects", project, "--types", "document", "--top-k", "1",
                  scoped=True, echo=echo) or {}
    docs = res.get("documents") or []
    if not docs:
        return dict(key=None, file=None, text="")
    doc = docs[0]
    text = "\n".join(c.get("text") or "" for it in doc.get("items") or []
                     for c in (it.get("highlight") or {}).get("chunks") or [])
    key = runbook_key(doc.get("document_name") or "")
    return dict(key=key, file=f"{key}.md" if key else doc.get("document_name"), text=text.strip())


def find_all(cli: CLI, world: dict) -> dict:
    d = load_data()
    rows = []
    for k, q in enumerate(d["questions"]):
        hit = search_page(cli, world["project"]["runbooks"], q["say"], echo=k == 0)
        if q["runbook"]:
            tag = "← the runbook with the answer" if hit["key"] == q["runbook"] else f"← not it (answer is in {q['runbook']}.md)"
        else:
            tag = "← closest page; no runbook covers this"
        emit("text", f"  {q['id']:<4} {q['say'][:66]:<66}  top 1: {hit['file'] or 'nothing':<15} {tag}")
        rows.append(dict(q=q["id"], file=hit["file"], key=hit["key"], right=hit["key"] == q["runbook"]))
    n = sum(r["right"] for r, q in zip(rows, d["questions"]) if q["runbook"])
    m = sum(1 for q in d["questions"] if q["runbook"])
    note(f"search finds the right runbook for {n} of the {m} questions a runbook answers. The answers are in memory")
    emit("found", "", rows=rows, right=n, of=m)
    return dict(rows=rows, right=n, of=m)


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


def chat(cli: CLI, say: str, boundary: str | None, system: str, echo: bool = False) -> tuple[str, int] | None:
    args = ["chat", say, "--model", MODEL, "--max-tokens", "120", "--temperature", "0", "--system", system]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return None
    u = res.get("usage") or {}
    count(cli, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return (res["choices"][0]["message"]["content"] or "").strip(), u.get("prompt_tokens", 0)


def baselines(cli: CLI, system: str) -> dict:
    """Each question once without a boundary, with the same system prompt: those prompt tokens are the system prompt
    and the question alone. A boundary call with the same count had nothing attached (∅)."""
    qs = load_data()["questions"]
    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(lambda nq: chat(cli, nq[1]["say"], None, system, nq[0] == 0), enumerate(qs)))
    return {q["id"]: (g[1] if g else 0) for q, g in zip(qs, got)}


def answer(cli: CLI, q: dict, boundary: str, system: str, empty: int, echo: bool = False) -> dict:
    got = chat(cli, q["say"], boundary, system, echo)
    if not got:
        return dict(q=q["id"], reply="", prompt_tokens=0, attached=0, verdict=None, empty=False, clause="",
                    source="", said_none=False)
    text, tokens = got
    g = grade(q, text, tokens, empty)
    return dict(q=q["id"], reply=text, prompt_tokens=tokens, attached=max(tokens - empty, 0), **g,
                source="none" if g["empty"] else "router")


def observed(cli: CLI, world: dict, q: dict, empty: int, echo: bool = False) -> dict:
    """The “Do not guess” prompt, and the app looks at what the call cost: prompt tokens no higher than the same call
    without a boundary mean MemoryRouter attached nothing. Then the app searches the runbooks itself
    (`search --types document --top-k 1`) and asks again with that page in the system prompt, without a boundary."""
    d = load_data()
    a = answer(cli, q, world["boundary"]["runbooks"], d["system"]["noguess"], empty, echo=echo)
    if a["verdict"] is None or not a["empty"]:
        return a
    hit = search_page(cli, world["project"]["runbooks"], q["say"], echo=echo)
    if not hit["text"]:
        a.update(source="none")
        return a
    system = f"{d['system']['noguess']}\n\n{d['fallback_header']}\n({hit['file']})\n{hit['text']}"
    got = chat(cli, q["say"], None, system, echo)
    if not got:
        return a
    text, tokens = got
    g = grade(q, text, tokens, 0)
    out = dict(q=q["id"], reply=text, prompt_tokens=tokens, attached=0, **g)
    out.update(empty=True, source=f"search:{hit['file']}", first=a["reply"])
    return out


def worst(answers: list[dict]) -> dict:
    return sorted(answers, key=lambda a: ORDER.index(a["verdict"]))[0]


# --------------------------------------------------------------------------- step 5: what MemoryRouter attaches

LABEL = re.compile(r"\(([^()\n]*?\.md)\)")


def read_attached(text: str, tokens: int, empty: int = 0) -> dict:
    """What the checker copied out: each attached entry's file label (a runbook). With no more prompt tokens than the
    same call without a boundary, nothing was attached (∅)."""
    nothing = bool(tokens and empty and tokens <= empty)
    parts = text.split("```")
    block = parts[1] if len(parts) >= 3 else text
    if nothing or block.strip() == "NONE" or len(parts) < 3:
        after = " ".join((parts[-1] if len(parts) >= 3 else "").split())
        return dict(readable=False, nothing=nothing, files=[], said=after[:220], raw=text[:4000], tokens=tokens)
    files = []
    for m in LABEL.finditer(block):
        key = runbook_key(m.group(1))
        if key and f"{key}.md" not in files:
            files.append(f"{key}.md")
    if not files and block.strip():
        # Copied without the file labels: attribute by the runbook heading in the text.
        d = load_data()
        files = [f"{r['key']}.md" for r in d["runbooks"] if r["title"] in block] or ["(unlabelled text)"]
    return dict(readable=True, nothing=False, files=files, said="", raw=text[:4000], tokens=tokens)


def check(cli: CLI, q: dict, boundary: str | None, empty: int = 0, echo: bool = False) -> dict:
    """Ask Claude, through the same boundary, to copy out everything it was given. Its own call: what it is given
    can differ from what the answer calls were given. Without a boundary, its prompt tokens are the baseline."""
    d = load_data()
    args = ["messages", q["say"], "--model", CHECK_MODEL, "--max-tokens", "2500", "--temperature", "0",
            "--system", d["checker"]]
    res = router_call(cli, args + (["--boundary", boundary] if boundary else []), echo)
    if not res:
        return dict(readable=False, nothing=False, files=[], said="", raw="", tokens=0)
    u = res.get("usage") or {}
    count(cli, u.get("input_tokens", 0), u.get("output_tokens", 0))
    text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
    return read_attached(text, u.get("input_tokens", 0), empty)


def check_line(c: dict, q: dict) -> str:
    if c["readable"]:
        want = f"{q['runbook']}.md" if q["runbook"] else None
        return ", ".join(f + ("  ← has the answer" if f == want else "") for f in c["files"])
    if c["nothing"]:
        return "nothing attached (∅)"
    return f"wrote NONE: “{c['said'][:150]}”" if c["said"] else "wrote NONE"


def show_attached(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    qs = d["questions"]
    boundary = world["boundary"]["runbooks"]
    note(f"for each question, {CHECK_MODEL} is asked {CHECKS}× through the runbooks boundary to copy out everything "
         "above the question (its own call: the attachment can differ from the answer calls'), and once without a "
         "boundary for its ∅ baseline")
    with ThreadPoolExecutor(4) as pool:
        base = list(pool.map(lambda q: check(cli, q, None)["tokens"], qs))
    first = check(cli, qs[0], boundary, base[0], echo=True)
    jobs = [(n, k) for n in range(len(qs)) for k in range(CHECKS) if (n, k) != (0, 0)]
    with ThreadPoolExecutor(6) as pool:
        got = dict(zip(jobs, pool.map(lambda nk: check(cli, qs[nk[0]], boundary, base[nk[0]]), jobs)))
    got[(0, 0)] = first
    out, raws = [], []
    for n, q in enumerate(qs):
        copies = [got[(n, k)] for k in range(CHECKS)]
        emit("text", f"\n  {q['id']}  {q['say']}\n"
             + "\n".join(f"      {k + 1}. {check_line(c, q)}" for k, c in enumerate(copies)))
        out.append(dict(q=q["id"], runbook=q["runbook"],
                        copies=[{k_: c[k_] for k_ in ("readable", "nothing", "files", "said", "tokens")} for c in copies]))
        raws.append(dict(q=q["id"], raw=[c["raw"] for c in copies]))
    flat = [(c["runbook"], x) for c in out for x in c["copies"]]
    n_empty = sum(x["nothing"] for _, x in flat)
    n_right = sum(bool(rb) and f"{rb}.md" in x["files"] for rb, x in flat)
    n_have = sum(bool(rb) for rb, _ in flat)
    note(f"{len(flat)} copies: nothing attached in {n_empty}; for the {n_have} copies of questions a runbook answers, "
         f"that runbook was attached in {n_right}")
    emit("attached", "", checks=out, empty=n_empty, right=n_right, have=n_have, copies=len(flat))
    OUT.mkdir(exist_ok=True)
    (OUT / "checker.json").write_text(json.dumps(raws, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- steps 6–8: ask

def ask_all(cli: CLI, world: dict, arm: str, base: dict) -> list[dict]:
    d = load_data()
    boundary = world["boundary"]["runbooks"]
    qs = d["questions"]
    system = d["system"]["plain" if arm == "plain" else "noguess"]
    if arm == "observed":
        ask = lambda q, echo=False: observed(cli, world, q, base.get(q["id"], 0), echo)
    else:
        ask = lambda q, echo=False: answer(cli, q, boundary, system, base.get(q["id"], 0), echo)
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
        emit("text", f"\n  {q['id']}  {q['say']}\n      runbook: {q['runbook'] + '.md' if q['runbook'] else '—'}"
                     f"  ·  answer: {q['fact']}")
        answers = [first if (n, k) == (0, 0) else got[(q["id"], k)] for k in range(RUNS)]
        pick = worst(answers)
        extra = ""
        if arm == "observed":
            src = sorted({a["source"] for a in answers if a["source"].startswith("search")})
            if src:
                extra = f"   [∅ → {', '.join(s.split(':', 1)[1] for s in src)} via search]"
        emit("text", f"      answers {' '.join(shown(a) for a in answers)}{extra}   "
                     + "e.g. “" + " ".join(pick["reply"].split())[:160] + "”")
        rows.append(dict(q=q["id"], qtype=q["kind"], arm=arm, answers=answers))
        emit("asked", "", q=q["id"], arm=arm, say=q["say"], qtype=q["kind"], runbook=q["runbook"], fact=q["fact"],
             answers=[dict(verdict=a["verdict"], empty=a["empty"], reply=a["reply"], tokens=a["prompt_tokens"],
                           attached=a.get("attached", 0), source=a.get("source", "")) for a in answers])
    return rows


def tally(rows: list[dict]) -> dict:
    d = load_data()
    has = {q["id"]: bool(q["runbook"]) for q in d["questions"]}
    ans = [(r["q"], a) for r in rows for a in r["answers"] if a["verdict"]]
    out = {k: sum(a["verdict"] == k for _, a in ans) for k in VERDICTS}
    out["answers"] = len(ans)
    out["empty"] = sum(bool(a["empty"]) for _, a in ans)
    out["empty_invented"] = sum(bool(a["empty"]) and a["verdict"] == "invented" for _, a in ans)
    out["no_record"] = sum(a["said_none"] for _, a in ans)
    out["no_record_wrong"] = sum(a["said_none"] and has[q] for q, a in ans)
    out["fallback"] = sum(a.get("source", "").startswith("search") for _, a in ans)
    return out


def tally_line(t: dict) -> str:
    return (f"{t['right']}/{t['answers']} right · {t['invented']} invented"
            + (f" ({t['empty_invented']} with nothing attached)" if t["invented"] else "")
            + f" · “No record” {t['no_record']}×"
            + (f", {t['no_record_wrong']} of them where a runbook has the answer" if t["no_record"] else "")
            + (f" · {t['fallback']} answered from the search fallback" if t["fallback"] else ""))


def metrics(rows: list[dict]) -> list[dict]:
    """Per question, over every MemoryRouter call with the boundary: how often something was attached, and how many
    tokens. The reply never says this; `usage.prompt_tokens` minus the no-boundary baseline is the only trace."""
    d = load_data()
    out = []
    for q in d["questions"]:
        calls = [a for r in rows if r["q"] == q["id"] for a in r["answers"] if a["verdict"]]
        hit = [a.get("attached", 0) for a in calls if not a["empty"]]
        nothing = sum(bool(a["empty"]) for a in calls)
        out.append(dict(q=q["id"], calls=len(calls), hits=len(hit), empty=nothing,
                        tokens=sorted(hit)[len(hit) // 2] if hit else 0))
    return out


def instead(rows: list[dict]) -> list[str]:
    """What the assistant told the engineer instead of the runbook's answer: one line per distinct reply."""
    order = ["invented", "missed"]
    seen, found = set(), []
    for r in rows:
        for a in r["answers"]:
            if a["verdict"] not in order:
                continue
            text = " ".join(a["reply"].split())
            if (r["arm"], text[:150]) in seen:
                continue
            seen.add((r["arm"], text[:150]))
            found.append((order.index(a["verdict"]), f"    {shown(a)} {r['q']} [{r['arm']}] “{text[:140]}”"))
    return [line_ for _, line_ in sorted(found, key=lambda x: x[0])]


# --------------------------------------------------------------------------- pipeline

TOTAL = 10
LOCK = threading.Lock()
ARM_STEP = {"plain": 6, "noguess": 7, "observed": 8}


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    banner(2, TOTAL, "Set up — a project for the runbooks, and a boundary over it")
    world = setup(cli)

    banner(3, TOTAL, f"The team's memory — {len(d['runbooks'])} runbooks")
    store(cli, world)

    banner(4, TOTAL, "Can the index find the answer? — `search --top-k 1` for every question")
    found = find_all(cli, world)

    banner(5, TOTAL, "What MemoryRouter hands the model — the checker copies it out")
    attached = show_attached(cli, world) if cli.router_ok else []  # type: ignore[attr-defined]

    rows: list[dict] = []
    tallies: dict = {}
    base: dict = {}
    for arm in ARMS:
        banner(ARM_STEP[arm], TOTAL, f"Ask the on-call assistant — {d['arms'][arm]['short']}")
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        key = "plain" if arm == "plain" else "noguess"
        if arm != "observed":
            emit("text", "\n  system prompt, as the app sends it on every call:\n"
                 + "\n".join("    │ " + x for x in d["system"][key].splitlines()))
        if arm == "observed":
            note("the same “Do not guess” prompt. After each call the app compares prompt tokens with the same call "
                 "without a boundary: equal means MemoryRouter attached nothing (∅). Then the app runs `search "
                 "--types document --top-k 1` itself and asks again, without a boundary, with that page in the "
                 "system prompt — and labels where the answer came from")
        if key not in base:
            note("first, each question once without a boundary and with this system prompt: those prompt tokens are "
                 "the prompt alone, so a boundary call with the same count had nothing attached (∅)")
            base[key] = baselines(cli, d["system"][key])
        note(f"`router chat --boundary` ({MODEL}, temperature 0), each question {RUNS}×. ✓ right · ✎ invented · "
             "– “No record” although a runbook has it · ∅ nothing attached · ↻ answered from the app's search")
        got = ask_all(cli, world, arm, base[key])
        rows += got
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        tallies[arm] = tally(got)
        emit("text", f"\n    {d['arms'][arm]['short']}: {tally_line(tallies[arm])}")
        emit("tally", "", arm=arm, **tallies[arm])

    banner(9, TOTAL, "Per-retrieval metrics — what each MemoryRouter call attached")
    met = metrics(rows)
    if met:
        note("every boundary call in steps 6–8. Attached tokens = the call's prompt tokens minus the same call "
             "without a boundary; nothing in the reply says this")
        emit("text", "  question                                                              calls  attached  ∅   median tokens")
        qs = {q["id"]: q for q in d["questions"]}
        for m in met:
            emit("text", f"  {m['q']:<4} {qs[m['q']]['say'][:62]:<62}  {m['calls']:>4}  {m['hits']:>8}  {m['empty']:>2}  "
                         f"{m['tokens'] or '—':>6}")
        tot = sum(m["calls"] for m in met)
        nil = sum(m["empty"] for m in met)
        emit("text", f"  {'all':<67}  {tot:>4}  {tot - nil:>8}  {nil:>2}")
        emit("metrics", "", rows=met, calls=tot, empty=nil)

    banner(10, TOTAL, "Scoreboard")
    for arm in ARMS:
        if arm in tallies:
            emit("text", f"  {d['arms'][arm]['short']:<28} {tally_line(tallies[arm])}")
    t = tallies.get("noguess")
    if t and t["no_record"]:
        emit("text", f"\n  With “Do not guess”, the assistant said “No record” {t['no_record']} times; "
                     f"{t['no_record_wrong']} of those were for a question a runbook answers. "
                     "The reply reads the same either way.")
    said = instead(rows)
    if said:
        emit("text", "\n  what the assistant told the engineer instead of the runbook's answer (✎ invented · – “No "
                     "record” · ∅ nothing attached · ↻ after the app's search):\n"
             + "\n".join(said[:14]) + (f"\n    … {len(said) - 14} more in out/report.json" if len(said) > 14 else ""))
    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rows=rows, tallies=tallies, found=found, attached=attached, baselines=base, metrics=met,
                   tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"], tallies=tallies,  # type: ignore[attr-defined]
         instead=said[:14])
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def show_offline() -> dict:
    """Offline: the six runbooks and the ten questions."""
    d = load_data()
    view = runbooks_view(d)
    for v in view:
        emit("text", f"\n  {v['file']} — {v['title']}\n      {v['first']}")
    emit("text", "\n  the questions:\n" + "\n".join(
        f"    {q['id']:<4} {q['say']}\n         → {q['fact']}" + (f"  ({q['runbook']}.md)" if q["runbook"] else "")
        for q in d["questions"]))
    out = dict(runbooks=view, questions=d["questions"])
    emit("runbooks", "", **out)
    return out


def ask_one(cli: CLI, text: str) -> dict:
    """One question the observed way: the baseline, the boundary call and its attached tokens, the fallback if ∅."""
    d = load_data()
    world = load_world(cli)
    say = " ".join(text.split())
    if not say:
        die("give a question, e.g. \"Who do I wake up if a Sev1 is still burning after half an hour?\"")
    q = dict(id="Q", say=say, runbook=None, truth=[], kind="asked")
    cli.router_ok = True  # type: ignore[attr-defined]
    got = chat(cli, say, None, d["system"]["noguess"], echo=True)
    if not got:
        return {}
    empty = got[1]
    note(f"baseline: {empty} prompt tokens without a boundary")
    a = observed(cli, world, q, empty, echo=True)
    if a.get("source", "").startswith("search"):
        emit("text", f"\n  MemoryRouter attached nothing (prompt tokens {empty} = baseline). The app searched the runbooks "
                     f"and asked again with {a['source'].split(':', 1)[1]}:")
    elif a.get("prompt_tokens"):
        emit("text", f"\n  MemoryRouter attached {a['attached']} tokens of runbook text:")
    emit("text", "  " + " ".join(a.get("reply", "").split()))
    out = dict(say=say, baseline=empty, prompt_tokens=a.get("prompt_tokens"), attached=a.get("attached"),
               source=a.get("source"), reply=a.get("reply"))
    emit("one", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
    n_docs = n_proj = 0
    for name in PROJECTS:
        proj = find_project(cli, name, echo=not quiet)
        if not proj:
            continue
        n_docs += len(project_docs(cli, proj["id"]))
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        n_proj += 1
    files = demo_files(cli, echo=not quiet)
    for k, f in enumerate(files):
        cli.run("lib", "delete", f["item_id"], echo=not quiet and k == 0)
    if not quiet:
        note(f"deleted the boundary, {n_proj} project(s) ({n_docs} document(s)) and {len(files)} Library file(s)")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "runbooks", "cleanup"],
                    help="run = full demo (default); ask \"<question>\" = one question the observed way (attached "
                         "tokens, search fallback, where the answer came from); runbooks = the six runbooks and the "
                         "ten questions (offline); cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: a question")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "runbooks":
        banner(1, 1, "Runbooks — the six runbooks and the ten questions (offline)")
        show_offline()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "ask":
        if not args.args:
            die("usage: python3 demo.py ask \"Who do I wake up if a Sev1 is still burning after half an hour?\"")
        banner(2, 2, f"Ask — “{' '.join(args.args)}”: what was attached, and where the answer came from")
        ask_one(cli, " ".join(args.args))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py ask \"Can I ship a hotfix the Friday after Thanksgiving?\"`; "
          "`python3 demo.py cleanup` removes the demo data.")


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

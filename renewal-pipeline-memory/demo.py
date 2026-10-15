#!/usr/bin/env python3
"""
Renewal pipeline memory: when the answer is a list longer than what MemoryRouter
attaches — a MemoryLake demo driven entirely by the `memorylake` CLI
(https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tidewater Software (fictional) has four account managers and 160 customer
accounts.  Every account's renewal record (owner, renewal date, ARR) and 60
recent call notes are stored as facts in one MemoryLake project, with one
*boundary* over it that the team's assistant passes to MemoryRouter.

On Monday 19 October the team prepares its renewal review: which accounts renew
between 19 October and 31 December?  The answer is 40 accounts.

What it shows
-------------
  · Look-ups through MemoryRouter are right ("When does Juniper Health renew?"),
    and so is one manager's list (12 accounts): the memory MemoryRouter attaches
    to a request holds the whole answer.
  · The whole team's list comes back with about half the accounts.  Nothing in
    the reply says it is partial, and nothing it names is wrong.
  · Why: MemoryRouter attaches at most 20 memories to one request.  A checker
    (Claude, asked to copy out everything above the request) shows 20 attached
    lines, every one of them a renewal in the window: retrieval picked the right
    records, there were just more right records than places.
  · The fix: count first.  `fact list --query` reads every renewal record (no
    top-k), the app filters the window itself, and with 40 > 20 it hands the
    complete list to the model in the request body (`router chat --body`).

Model wording varies run to run; every reply is graded and printed, and the
README reports totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (about 12
calls on gpt-4o-mini and 2 on claude-haiku-4-5 per run).
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
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-rpm"  # memorylake-usecases / renewal-pipeline-memory

SYSTEM = ("You are the customer-success assistant at Tidewater Software. "
          "Use what you remember about the accounts. Answer briefly.")
MODEL = dict(protocol="chat", model="gpt-4o-mini", label="gpt-4o-mini (OpenAI Chat Completions)")
CHECKER = dict(protocol="messages", model="claude-haiku-4-5", label="claude-haiku-4-5 (Anthropic Messages)")
ATTACH_BUDGET = 20  # about what MemoryRouter attaches to one request (measured 18-20 in most calls; see README)


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

    # Router calls are retried too: a dropped connection before the reply costs nothing to repeat.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "chat", "messages", "responses", "balance"}
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




# --------------------------------------------------------------------------- data

def load_book() -> dict:
    return json.loads((DATA / "accounts.json").read_text(encoding="utf-8"))


def load_questions() -> list[dict]:
    return json.loads((DATA / "questions.json").read_text(encoding="utf-8"))["questions"]


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def record_text(a: dict) -> str:
    return (f"Renewal record: {a['account']} (account manager {a['manager']}) - "
            f"contract renews on {a['renews_on']}, ARR {a['arr']} dollars.")


RECORD = re.compile(r"^Renewal record: (.+?) \(account manager (.+?)\) - contract renews on (\d{4}-\d{2}-\d{2}), "
                    r"ARR (\d+) dollars\.$")


def parse_records(texts: list[str]) -> list[dict]:
    out = []
    for t in texts:
        m = RECORD.match(t.strip())
        if m:
            out.append(dict(account=m.group(1), manager=m.group(2), renews_on=m.group(3), arr=int(m.group(4)), text=t))
    return out


def in_window(recs: list[dict], manager: str | None = None) -> list[dict]:
    lo, hi = load_book()["window"]
    return sorted((r for r in recs if lo <= r["renews_on"] <= hi and (manager is None or r["manager"] == manager)),
                  key=lambda r: (r["renews_on"], r["account"]))


# --------------------------------------------------------------------------- grading (deterministic)

def date_forms(iso: str) -> list[str]:
    """2026-12-10 → the ways a model writes it: 2026-12-10, December 10, 2026, Dec 10, 2026, 10 December 2026."""
    d = time.strptime(iso, "%Y-%m-%d")
    month, mon, day, year = time.strftime("%B", d), time.strftime("%b", d), d.tm_mday, d.tm_year
    return [iso, f"{month} {day}, {year}", f"{mon} {day}, {year}", f"{mon}. {day}, {year}", f"{day} {month} {year}"]


def grade(q: dict, reply: str, recs: list[dict]) -> dict:
    """Grade one reply against what is stored. Lists: right / missed / extra account names."""
    if q.get("kind") == "lookup":
        ok = any(v in reply for v in date_forms(q["expect"]))
        return dict(ok=ok, mark="✓" if ok else "✗")
    if q.get("kind") != "list":
        return dict(ok=None, mark="")  # your own question: nothing stored says what the right answer is
    truth = [r["account"] for r in in_window(recs, q.get("manager"))]
    named = [r["account"] for r in recs if re.search(r"\b" + re.escape(r["account"]) + r"\b", reply)]
    right = [n for n in named if n in truth]
    missed = [n for n in truth if n not in named]
    extra = [n for n in named if n not in truth]
    ok = not missed and not extra
    mark = f"{'✓' if ok else '✗'} {len(right)}/{len(truth)}" + (f"  + {len(extra)} wrong" if extra else "")
    return dict(ok=ok, mark=mark, truth=truth, right=right, missed=missed, extra=extra)


HEDGE = re.compile(r"\b(partial|incomplete|not (?:be )?(?:a )?complete|may not include|might not include|"
                   r"may be missing|might be missing|not all|only (?:the )?(?:ones|accounts|records) (?:i|that i))\b", re.I)


def hedged(reply: str) -> bool:
    """Does the reply itself say the list may be incomplete?"""
    return bool(HEDGE.search(reply))


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("book"), "--by-custom-id", scoped=True, echo=echo)


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


def list_facts(cli: CLI, project: str, query: str | None = None, echo: bool = True) -> list[dict]:
    """Every fact (or every fact whose text contains `query`), page by page until the token is null."""
    out, token = [], None
    while True:
        args = ["fact", "list", "--projects", project, "--page-size", "200"]
        if query:
            args += ["--query", query]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def setup(cli: CLI) -> dict:
    old = find_project(cli, echo=True)
    if old is not None:
        n = len(list_facts(cli, old["id"], echo=False))
        note(f"an earlier run left an account book of {n} facts; starting over with a fresh project "
             "(a new scope, so no answer can come from an earlier run's retrieval)")
        cleanup(cli, quiet=True)
    proj = cli.run("proj", "create", "--name", "Tidewater account book", "--custom-id", cid("book"),
                   "--description", "Renewal records and call notes of a fictional CS team. Demo data.", scoped=True)
    note(f"created project “{proj['name']}” → {proj['id']}")
    # The one integration point: the assistant passes this boundary to MemoryRouter and nothing else.
    b = cli.run("boundary", "create", "--name", "Tidewater CS assistant", "--custom-id", cid("boundary"),
                "--projects", proj["id"], scoped=True)
    note(f"created boundary {b['id']} over the project")
    world = dict(project=proj["id"], boundary=b["id"])
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    proj = find_project(cli)
    b = {x["custom_id"]: x for x in demo_boundaries(cli)}.get(cid("boundary"))
    if not (proj and b):
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], boundary=b["id"])


# --------------------------------------------------------------------------- step 3: the account book

def store(cli: CLI, world: dict) -> list[dict]:
    book = load_book()
    want = [record_text(a) for a in book["accounts"]] + book["notes"]
    batches = [want[i:i + 50] for i in range(0, len(want), 50)]
    shown = ["fact", "add", "--project", world["project"], *[textwrap.shorten(t, 50) for t in want[:2]]]
    emit("cmd", shlex.join(["memorylake", *shown, "--workspace", cli.workspace]) + f"  … {len(want) - 2} more")

    def add(batch: list[str]) -> bool:
        try:
            cli.run("fact", "add", "--project", world["project"], *batch, scoped=True, echo=False)
            return True
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
            return False

    with ThreadPoolExecutor(4) as pool:
        ok = list(pool.map(add, batches))
    if not all(ok):
        # `fact add` is not idempotent, so a dropped call is not retried blindly: add only what is still missing.
        have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
        missing = [t for t in want if t not in have]
        note(f"{ok.count(False)} `fact add` call(s) hit a network error; adding the {len(missing)} fact(s) still missing")
        for i in range(0, len(missing), 50):
            cli.run("fact", "add", "--project", world["project"], *missing[i:i + 50], scoped=True, echo=False)
    facts = [f["fact"] for f in list_facts(cli, world["project"])]
    if sorted(facts) != sorted(want):
        die(f"the project holds {len(facts)} facts, expected {len(want)}")
    recs = parse_records(facts)
    win = in_window(recs)
    note(f"stored {len(facts)} facts in {len(batches)} `fact add` call(s): {len(recs)} renewal records, "
         f"{len(facts) - len(recs)} call notes")
    emit("text", "\n  A few of them:\n" + "\n".join(f"   - {t}" for t in want[:3] + book["notes"][:2]))
    lo, hi = book["window"]
    per = {m: len(in_window(recs, m)) for m in book["managers"]}
    emit("text", f"\n  Renewals between {lo} and {hi}: {len(win)} accounts — "
         + ", ".join(f"{m} {n}" for m, n in per.items()))
    emit("memory", "", facts=len(facts), records=len(recs), notes=len(facts) - len(recs), window=len(win),
         per_manager=per)
    time.sleep(10)  # a new fact is in `fact list` at once; retrieval takes a few seconds to see it
    return recs


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, boundary: str | None = None, body: Path | None = None, tool: dict = MODEL,
           max_tokens: int = 900, system: str | None = SYSTEM, echo: bool = True) -> dict | None:
    """One call through MemoryRouter. Returns {reply, prompt_tokens, seconds} or None if unavailable."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["router", tool["protocol"], prompt, "--model", tool["model"], "--max-tokens", str(max_tokens),
            "--temperature", "0"]
    if body:
        args += ["--body", str(body.relative_to(HERE))]  # the system message, with the list, is in the body
    elif system:
        args += ["--system", system]
    if boundary:
        args += ["--boundary", boundary]
    t0 = time.time()
    try:
        res = cli.run(*args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("router steps below are skipped; top up in the console to see them.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    secs = time.time() - t0
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    if tool["protocol"] == "chat":
        text = res["choices"][0]["message"]["content"] or ""
        pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    else:
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
        pt = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        ct = u.get("output_tokens", 0)
    cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
    cli.tokens["completion"] += ct  # type: ignore[attr-defined]
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=text.strip(), prompt_tokens=pt, completion_tokens=ct, seconds=round(secs, 1))


def show_reply(reply: str, limit: int = 8) -> None:
    lines = [ln for ln in reply.splitlines() if ln.strip()]
    shown = lines[:limit] + ([f"… {len(lines) - limit} more line(s)"] if len(lines) > limit else [])
    emit("text", "\n".join("      │ " + textwrap.shorten(ln, 100) for ln in shown))


def ask_router(cli: CLI, world: dict, q: dict, recs: list[dict], echo: bool = True, baseline: bool = True) -> dict | None:
    """Ask through MemoryRouter with the boundary, and (for the memory size) once without it."""
    r = router(cli, q["prompt"], boundary=world["boundary"], echo=echo)
    if r is None:
        return None
    r.update(grade(q, r["reply"], recs))
    r["hedged"] = hedged(r["reply"])
    if baseline:
        b = router(cli, q["prompt"], echo=False, max_tokens=1)  # same request without memory: only its prompt size
        r["memory_tokens"] = r["prompt_tokens"] - b["prompt_tokens"] if b else None
    return r


def records_attached(results: dict, memory_tokens: int | None) -> dict | None:
    """How many records a request's memory holds, from its size: the look-ups attach one record each, Lev's
    list (graded complete) attaches his 8; the difference gives the size of one record and of the fixed preamble."""
    looks = [results[k]["memory_tokens"] for k in ("quarry", "alder", "delta") if results.get(k, {}).get("ok")]
    lev = results.get("lev")
    if not (looks and lev and lev.get("ok") and memory_tokens):
        return None
    one = sum(looks) / len(looks)
    per = (lev["memory_tokens"] - one) / (len(lev["truth"]) - 1)
    pre = one - per
    return dict(records=round((memory_tokens - pre) / per), per_record=round(per, 1), preamble=round(pre))


# --------------------------------------------------------------------------- step 6: what MemoryRouter attached

CHECK_SYSTEM = ("Before answering, copy out verbatim, inside triple backticks, every remembered entry you were given "
                "(each line that starts with '- '). Then answer the question.")


def inspect(cli: CLI, world: dict, q: dict, recs: list[dict], stored: list[str]) -> dict | None:
    """Ask Claude the same question through the same boundary, told (in the system prompt) to copy out first
    every memory MemoryRouter attached. The user message is the question itself, so retrieval sees the same query."""
    r = router(cli, q["prompt"], boundary=world["boundary"], tool=CHECKER, max_tokens=4000, system=CHECK_SYSTEM)
    if r is None:
        return None
    block = r["reply"].split("```")[1] if r["reply"].count("```") >= 2 else r["reply"]
    lines = [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")]
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()[:48]
    known = {norm(t): t for t in stored}
    win = {x["account"] for x in in_window(recs, q.get("manager"))}
    kinds = dict(window=0, other_record=0, note=0, unmatched=0)
    rows = []
    for ln in lines:
        t = known.get(norm(ln))
        if t is None:
            kind = "unmatched"
        else:
            rec = parse_records([t])
            kind = ("window" if rec[0]["account"] in win else "other_record") if rec else "note"
        kinds[kind] += 1
        rows.append(dict(line=ln, kind=kind))
    return dict(question=q["key"], label=q["label"], attached=len(lines), kinds=kinds, rows=rows,
                truth=len(win), input_tokens=r["prompt_tokens"])


# --------------------------------------------------------------------------- step 7: count first, then list

def body_with(recs: list[dict], name: str) -> Path:
    """The fix: the complete list, read with `fact list`, goes into the request body."""
    OUT.mkdir(exist_ok=True)
    path = OUT / f"renewals-{name}.json"
    lo, hi = load_book()["window"]
    system = (SYSTEM + f"\n\nThe complete list of renewal records between {lo} and {hi}, read from memory "
              f"({len(recs)} records):\n" + "\n".join("- " + r["text"] for r in recs))
    path.write_text(json.dumps({"model": MODEL["model"], "messages": [{"role": "system", "content": system}]},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def count_first(cli: CLI, world: dict) -> list[dict]:
    hits = list_facts(cli, world["project"], query="Renewal record")
    recs = parse_records([h["fact"] for h in hits])
    return recs


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    book = load_book()
    qs = {q["key"]: q for q in load_questions()}
    lo, hi = book["window"]
    banner(2, TOTAL, "Set up — one memory project and one boundary the assistant passes to MemoryRouter")
    world = setup(cli)
    emit("text", f"\n  {book['company']}: {len(book['managers'])} account managers, {len(book['accounts'])} accounts.\n"
         f"  Today is {book['today']}; the renewal review covers {lo} to {hi}.")

    banner(3, TOTAL, "The account book — every renewal record and recent call note, stored as facts")
    recs = store(cli, world)
    stored = [r["text"] for r in recs] + book["notes"]

    banner(4, TOTAL, "Ask through MemoryRouter — three look-ups and one manager's renewals")
    results: dict[str, dict] = {}
    small = [qs[k] for k in ("quarry", "alder", "delta", "lev")]
    q0 = small[0]
    cli._echo(["router", "chat", q0["prompt"], "--model", MODEL["model"], "--max-tokens", "900", "--temperature", "0",
               "--system", SYSTEM, "--boundary", world["boundary"]])
    note(f"{len(small)} questions, the same command shape; each is also sent once without the boundary to "
         "measure how much memory was attached")
    def one(q: dict) -> dict | None:
        r = ask_router(cli, world, q, recs, echo=False)
        if r:  # the browser shows each answer as it arrives
            emit("answer", "", question=q["key"], label=q["label"], qkind=q["kind"], mode="router", reply=r["reply"],
                 mark=r["mark"], ok=r["ok"], memory_tokens=r["memory_tokens"], missed=r.get("missed") or [], right=r.get("right") or [],
                 extra=r.get("extra") or [], truth=r.get("truth"), hedged=r["hedged"])
        return r

    with ThreadPoolExecutor(4) as pool:
        got = list(pool.map(one, small))
    if all(got):
        for q, r in zip(small, got):
            results[q["key"]] = r
            emit("text", f"\n    {q['label']}: {r['mark']}  ({r['memory_tokens']} tokens of memory attached)")
            show_reply(r["reply"], limit=4 if q["kind"] == "lookup" else 14)
        note("every answer fits in what MemoryRouter attaches to one request")
        time.sleep(3)  # leave the answers on screen a moment before the next step

    banner(5, TOTAL, "The renewal review — the whole team's list, through MemoryRouter")
    team = qs["team"]
    r = ask_router(cli, world, team, recs) if cli.router_ok else None  # type: ignore[attr-defined]
    if r:
        results["team"] = r
        show_reply(r["reply"], limit=24)
        emit("text", f"\n    {team['label']}: {r['mark']}  ({r['memory_tokens']} tokens of memory attached)")
        if r["missed"]:
            emit("text", "    missing: " + ", ".join(r["missed"]))
        est = records_attached(results, r["memory_tokens"])
        if est:
            r["records_estimate"] = est
            note(f"{r['memory_tokens']} tokens of memory ≈ {est['records']} renewal records (one record ≈ "
                 f"{est['per_record']} tokens, measured on the look-ups and Lev's list above)")
        note("the reply says the list may be incomplete" if r["hedged"] else
             "nothing in the reply says the list is partial" + ("" if r["extra"] else ", and nothing it names is wrong"))
        emit("answer", "", question="team", label=team["label"], qkind="list", mode="router", reply=r["reply"],
             mark=r["mark"], ok=r["ok"], memory_tokens=r["memory_tokens"], missed=r["missed"], right=r["right"], extra=r["extra"],
             truth=r["truth"], hedged=r["hedged"])

    banner(6, TOTAL, "What MemoryRouter attached — Claude copies out what sits above the request")
    checks = []
    for key in ("lev", "team"):
        if not cli.router_ok:  # type: ignore[attr-defined]
            break
        c = inspect(cli, world, qs[key], recs, stored)
        if c is None:
            break
        checks.append(c)
        k = c["kinds"]
        emit("text", f"\n    {c['label']}: {c['attached']} memories attached — {k['window']} renewal(s) in the answer, "
             f"{k['other_record']} other renewal record(s), {k['note']} call note(s)"
             + (f", {k['unmatched']} line(s) not matching a stored fact" if k["unmatched"] else "")
             + f"; the answer has {c['truth']} account(s)")
        for row in c["rows"][:3]:
            emit("text", f"      - {textwrap.shorten(row['line'], 96)}")
        if c["attached"] > 3:
            emit("text", f"      … {c['attached'] - 3} more")
        emit("inspect", "", **{k2: v for k2, v in c.items() if k2 != "rows"}, lines=[x["line"] for x in c["rows"]])
    if len(checks) == 2:
        lev, tm = checks
        note(f"every attached line is a record that belongs in the answer: retrieval picks the right records. "
             f"Lev's answer ({lev['truth']}) fits in what is attached; the team's ({tm['truth']}) does not — this request "
             f"attached {tm['attached']}. MemoryRouter attaches a bounded set by relevance (about {ATTACH_BUDGET} in most "
             "calls; the README has the counts over every run), never the whole book")
    emit("text", "")

    banner(7, TOTAL, "Count first — list with `fact list`, hand the complete list to the model")
    full = count_first(cli, world)
    win = in_window(full)
    note(f"`fact list --query` returns every match, no top-k: {len(full)} renewal records; {len(win)} renew between "
         f"{lo} and {hi} (filtered by the app, not the model)")
    route = "list path" if len(win) > ATTACH_BUDGET else "MemoryRouter"
    note(f"{len(win)} > {ATTACH_BUDGET}: more than MemoryRouter attaches to one request → {route}" if len(win) > ATTACH_BUDGET
         else f"{len(win)} ≤ {ATTACH_BUDGET}: fits in one request's memory → {route}")
    fixed = None
    if cli.router_ok:  # type: ignore[attr-defined]
        body = body_with(win, "team")
        fixed = router(cli, team["prompt"], body=body)  # the same question, the same model: only the memory differs
        if fixed:
            fixed.update(grade(team, fixed["reply"], recs))
            note(f"{body.relative_to(HERE)} holds the {len(win)} records in its system message")
            show_reply(fixed["reply"], limit=10)
            emit("text", f"\n    {team['label']}, listed via `fact list`: {fixed['mark']}")
            emit("answer", "", question="team", label=team["label"], qkind="list", mode="listed", reply=fixed["reply"],
                 mark=fixed["mark"], ok=fixed["ok"], missed=fixed["missed"], right=fixed["right"], extra=fixed["extra"], truth=fixed["truth"])

    rows = []
    for key in ("quarry", "alder", "delta", "lev", "team"):
        x = results.get(key)
        if x:
            rows.append(dict(question=key, label=qs[key]["label"], router=x["mark"], ok=x["ok"],
                             listed=fixed["mark"] if key == "team" and fixed else "—"))
    lines = [f"\n    {'question':<20}{'answer size':>12}{'MemoryRouter':>16}{'count first, then list':>26}"]
    for row in rows:
        q = qs[row["question"]]
        size = "1" if q["kind"] == "lookup" else str(len(in_window(recs, q.get("manager"))))
        lines.append(f"    {row['label']:<20}{size:>12}{row['router']:>16}{row['listed']:>26}")
    emit("text", "\n".join(lines))
    emit("scoreboard", "", rows=rows, window=len(win), limit=ATTACH_BUDGET)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(results=results, checks=checks, fixed=fixed, window=len(win), tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every reply and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def find_question(text: str) -> dict:
    for q in load_questions():
        if text.strip().lower() in (q["key"], q["prompt"].lower()):
            return q
    return dict(key="custom", kind="custom", label="Your question", prompt=text)


def ask(cli: CLI, text: str) -> dict:
    world = load_world(cli)
    recs = parse_records([f["fact"] for f in list_facts(cli, world["project"], query="Renewal record", echo=False)])
    q = find_question(text)
    r = ask_router(cli, world, q, recs)
    if not r:
        return {}
    emit("text", f"\n    MemoryRouter · {r['memory_tokens']} tokens of memory attached · {r['seconds']:.1f}s"
         + (f" · {r['mark']}" if r["mark"] else ""))
    show_reply(r["reply"], limit=40)
    if r.get("missed"):
        emit("text", "    missing: " + ", ".join(r["missed"]))
    return dict(question=q, result=r)


def renewals(cli: CLI, manager: str | None = None) -> list[dict]:
    world = load_world(cli)
    recs = count_first(cli, world)
    win = in_window(recs, manager)
    lo, hi = load_book()["window"]
    emit("text", f"\n  {len(recs)} renewal records in memory; {len(win)} renew between {lo} and {hi}"
         + (f" for {manager}" if manager else "") + ":")
    for m in sorted({r["manager"] for r in win}):
        mine = [r for r in win if r["manager"] == m]
        emit("text", f"\n   {m} — {len(mine)} account(s), ARR {sum(r['arr'] for r in mine):,} dollars")
        for r in mine:
            emit("text", f"     {r['renews_on']}  {r['account']:<20} ARR {r['arr']:>7,}")
    if len(win) > ATTACH_BUDGET:
        note(f"{len(win)} accounts: more than MemoryRouter attaches to one request (about {ATTACH_BUDGET})")
    return win


# --------------------------------------------------------------------------- cleanup

def delete_fact(cli: CLI, fact_id: str, project: str) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, "--project", project, scoped=True, echo=False)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"], echo=not quiet)
        if not quiet:
            note(f"deleted boundary {b['name']}")
    proj = find_project(cli, echo=not quiet)
    if proj:
        facts = list_facts(cli, proj["id"], echo=False)
        left = facts
        for attempt in range(4):  # a list right after many deletes can still show a few: delete those and look again
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_fact(cli, f["id"], proj["id"]), left))
            time.sleep(2 * attempt)
            left = list_facts(cli, proj["id"], echo=False)
            if not left:
                break
        if left:
            die(f"{len(left)} fact(s) are still in the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        if not quiet:
            note(f"deleted {len(facts)} fact(s) and the project")
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "renewals", "cleanup"],
                    help="run = full demo (default); ask = one question through MemoryRouter; "
                         "renewals = the review list, read with `fact list`; cleanup = delete everything")
    ap.add_argument("question", nargs="?", help="for ask: quarry, alder, delta, lev, team, or your own question in quotes")
    ap.add_argument("--manager", help="for renewals: one account manager, e.g. \"Hana Okafor\"")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "renewals":
        banner(2, 2, "The renewal review, read with `fact list` (every record, no top-k)")
        renewals(cli, args.manager)
        return
    if args.command == "ask":
        if not args.question:
            die('usage: python3 demo.py ask quarry|alder|delta|lev|team|"your own question"')
        banner(2, 2, "One question through MemoryRouter, against the account book as it is now")
        ask(cli, args.question)
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py renewals` or `python3 demo.py ask team`; '
          '`python3 demo.py cleanup` removes the demo data.')


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

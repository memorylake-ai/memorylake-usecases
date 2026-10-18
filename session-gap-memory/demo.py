#!/usr/bin/env python3
"""session-gap-memory — a value changed today can leave the context window before MemoryLake has written it, and for
those seconds the memory answers with the old one.

Rhea Tanaka (backend engineer at Brindlewood Payments, a fictional company) works with Pair, the team's coding agent,
on the ledger service. Six past sessions settled the conventions: the test command, the local database port, branch
names, the reviewer, the staging window, the flag prefix. This morning she changes three of them in her first three
messages, then Pair works through a 22-step migration task and has to use those values along the way. Every request
carries only the last 6 steps (the window) plus what MemoryRouter attaches from MemoryLake (`--boundary`). Three
ways to keep the two in step:

  B  today's session is written to memory when the session closes (the common "save on close")
  C  each step is written to memory as it happens (`conv msg append`, off the hot path)
  D  written like C, and a step leaves the window only once `conv cook-status` says memory holds it

Each arm has its own project, so what C writes never reaches B or D. Extraction takes tens of seconds; an agent's
steps take one or two. The demo prints, for every question, which value each arm's memory held at that moment, and
for every change when it was said, when it left the window and when it reached memory.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Model calls go
through MemoryRouter and are billed to your MemoryLake balance (24 calls on gpt-4o-mini per run, under 0.01 USD).
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
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-sgp"  # memorylake-usecases / session-gap-memory




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

    # Router calls are retried too: a dropped connection before the reply costs nothing to repeat. So is `fact update`:
    # setting the same metadata twice is the same as once.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "chat", "messages", "responses", "balance",
                 "update"}
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
        # A multi-line argument is shown as its first line; it is printed in full
        # once, above the call.
        shown = [a.split("\n")[0] + f" […{a.count(chr(10))} more lines]" if "\n" in a else a for a in shown]
        # `fact add` with many texts: the first two, then a count (display only; every text is sent).
        if shown[:2] == ["fact", "add"] and len(shown) > 12:
            # keep the flags after the texts (--project, --workspace): they say where the facts go
            cut = next((i for i in range(2, len(shown)) if shown[i].startswith("--")), len(shown))
            tail = shown[cut:]
            body = shown[:cut]
            shown = body[:6] + [f"… {len(body) - 6} more texts"] + tail
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


# --------------------------------------------------------------------------- objects

def find_project(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)


def find_actor(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id", echo=echo)


def paged(cli: CLI, args: list[str], echo: bool = False) -> list[dict]:
    out, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or [] if isinstance(page, dict) else page
        token = page.get("continuation_token") if isinstance(page, dict) else None
        if not token:
            return out


def demo_boundaries(cli: CLI, echo: bool = False) -> list[dict]:
    # `boundary get --by-custom-id` is not available: list and match the custom_id prefix.
    return [b for b in paged(cli, ["boundary", "list", "--page-size", "100"], echo)
            if (b.get("custom_id") or "").startswith(PREFIX + "-")]


def actor_facts(cli: CLI, actor: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--actors", actor, "--page-size", "200"], echo)


def project_facts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--projects", project, "--page-size", "200"], echo)


def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`, but the server still honours the type. Pass it when accepted.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def make_actor(cli: CLI, key: str, display: str, description: str, echo: bool, actor_type: str = "HUMAN") -> str:
    a = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", display, "--description", description,
                "--tags", "demo", *actor_type_args(cli, actor_type), echo=echo)
    cli.run("actor", "bind", "--actor", a["id"], scoped=True, echo=echo)
    return a["id"]


# --------------------------------------------------------------------------- conversations

def append(cli: CLI, conv: str, actor: str, custom_id: str, ts: str, text: str, echo: bool) -> None:
    for attempt in range(5):
        try:
            cli.run("conv", "msg", "append", conv, "--actor", actor, "--custom-id", custom_id, "--timestamp", ts,
                    "--text", text, scoped=True, echo=echo and attempt == 0)
            return
        except CLIError as e:
            # a resend after a dropped connection: the custom_id is already there, so the message is too
            if e.has("custom_id_conflict", "already exists") and attempt:
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))





# --------------------------------------------------------------------------- waiting for extraction

def cooked(cli: CLI, conv: str, echo: bool, limit: int = 300) -> bool:
    t0, first = time.time(), echo
    while time.time() - t0 < limit:
        try:
            st = cli.run("conv", "cook-status", conv, scoped=True, echo=first) or {}
            first = False
            if st.get("cook_finished"):
                return True
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
        time.sleep(5)
    return False

def delete_fact(cli: CLI, fact_id: str, scope: list[str]) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, *scope, scoped=True, echo=False)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and `fact delete` then
    fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes first, the list must come back empty,
    then the actor. A sealed (unbound) actor answers 404 to `fact list`, so it is bound back first."""
    try:
        found = actor_facts(cli, actor_id)
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        for wait in (1, 2, 4, 8, 0):  # the binding takes a moment to show
            try:
                found = actor_facts(cli, actor_id)
                break
            except CLIError as e2:
                if not e2.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    left = found
    for attempt in range(4):
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: delete_fact(cli, f["id"], ["--actor", actor_id]), left))
        left = actor_facts(cli, actor_id)
        if not left:
            break
        time.sleep(2 * (attempt + 1))
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id, echo=False)
    return len(found)


# --------------------------------------------------------------------------- settings

MODEL = "gpt-4o-mini"
TEMPERATURE = "0"   # the most repeatable setting: what changes between the arms is what the prompt holds
MAX_TOKENS = 140    # an answer is one to three short sentences
TOTAL = 6
INGEST_WORKERS = 6  # past sessions extracted at the same time (6 sessions × 3 arms)
ARMS = ("B", "C", "D")
WRITERS = ("C", "D")   # the arms that write today's session into MemoryLake while it happens
ARM_LABEL = {"B": "B  window + memory, saved at session end",
             "C": "C  window + memory, written as it goes",
             "D": "D  C + keep a step until memory holds it"}
ARM_SHORT = {"B": "B  saved at end", "C": "C  written as it goes", "D": "D  kept until held"}
MARK = {"ok": "✓", "stale": "⟲", "unknown": "∅", "made": "✗"}
GRADE_NAME = {"ok": "right", "stale": "the value from before today's change", "unknown": "says it is not on file",
              "made": "a wrong or made-up value"}
POLL_SECONDS = 2
SETTLE_LIMIT = 180  # after the last step: how long to keep watching for today's changes to reach memory


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "session.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


ACTOR_KEYS = ("rhea", "pair")


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def at(s: dict) -> datetime:
    return datetime.fromisoformat(s["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def conventions(d: dict) -> dict:
    return {c["id"]: c for c in d["conventions"]}


def changed(d: dict) -> list[dict]:
    """Today's three changes, in the order Rhea makes them."""
    return sorted((c for c in d["conventions"] if c.get("new")), key=lambda c: c["changed_at"])


def checkpoints(d: dict) -> list[tuple[int, dict]]:
    return [(n, s) for n, s in enumerate(d["live"], 1) if s.get("q")]


# --------------------------------------------------------------------------- grading

UNKNOWN_RE = (r"not on file|don't have|do not have|don't know|do not know|no record|not recorded|isn't recorded|not (been )?(mentioned|specified|"
              r"available|provided|in (my|the) (notes|records))|no information|not sure|unknown|check with")


def required(d: dict, qid: str) -> list[str]:
    """What a right answer must contain: today's value for a changed convention, the agreed one otherwise."""
    q, conv = d["questions"][qid], conventions(d)
    if q.get("re"):
        return list(q["re"])
    out = []
    for cid_ in q["needs"]:
        c = conv[cid_]
        out += c.get("new_re") or c["old_re"]
    return out


def grade(d: dict, qid: str, reply: str) -> str:
    """ok: every value right; stale: a value from before today's change; unknown: says it does not have it; else made."""
    conv = d["questions"][qid]["needs"]
    if all(re.search(r, reply, re.I) for r in required(d, qid)):
        return "ok"
    for c in (conventions(d)[x] for x in conv):
        if c.get("new") and any(re.search(r, reply, re.I) for r in c["old_re"]) \
                and not all(re.search(r, reply, re.I) for r in c["new_re"]):
            return "stale"
    return "unknown" if re.search(UNKNOWN_RE, reply, re.I) else "made"


def check_grader(d: dict) -> None:
    """The grading rules on known replies, before any call is made: a wrong rule would grade every run wrong."""
    cases = [("q1", "Your local Postgres is on port 6543.", "ok"),
             ("q1", "Your local Postgres is running on port 5433.", "stale"),
             ("q1", "It's on 6543 now (it used to be 5433).", "ok"),
             ("q1", "I don't have the port on file.", "unknown"),
             ("q2", "The branch is `feat/LED-418`.", "ok"),
             ("q2", "Call it `led-418-rename`.", "made"),
             ("q4", "I'm running `just ledger-test`.", "ok"),
             ("q4", "Running `make test-ledger` now.", "stale"),
             ("q5", "The next staging slot is Tuesday at 14:00 UTC.", "ok"),
             ("q6", "I'll request the review from Priya Nair.", "ok"),
             ("q6", "Requesting a review from Tomasz Ilic.", "stale"),
             ("q7", "Add `ledger.cents_display` to `flags.yaml`.", "ok"),
             ("q8", "Tests: `just ledger-test`. Database: port 6543. Reviewer: Priya Nair.", "ok"),
             ("q8", "Tests: `just ledger-test`. Database: port 5433. Reviewer: Priya Nair.", "stale"),
             ("q8", "Tests: `make test-ledger`. Database: port 5433. Reviewer: Tomasz Ilic.", "stale")]
    for qid, reply, want in cases:
        got = grade(d, qid, reply)
        if got != want:
            die(f"grader self-check failed on {qid}: “{reply}” graded {got}, expected {want}")


def value_in(c: dict, text: str) -> str | None:
    """Which value a fact states for a changed convention: the first one in the text. A revised fact carries the
    old value along in "previously …", so "contains the old value" would misread it."""
    hits = [(m.start(), v) for v, rs in (("new", c["new_re"]), ("old", c["old_re"])) for r in rs
            for m in [re.search(r, text, re.I)] if m]
    return min(hits)[1] if hits else None


# --------------------------------------------------------------------------- MemoryRouter

LOCK = threading.Lock()


def prices(cli: CLI, echo: bool = True) -> dict:
    """USD per million tokens for MODEL, from the account's own price list: input, cache_read, output."""
    res = cli.run("router", "models", echo=echo) or {}
    items = (res.get("data") or {}).get("items") or res.get("items") or []
    m = next((i for i in items if i.get("name") == MODEL), None)
    if not m:
        die(f"{MODEL} is not in `memorylake router models` on this account")
    p = m.get("price") or {}

    def usd(node) -> float | None:
        return node.get("usd_per_million") if isinstance(node, dict) else None

    out = dict(input=usd(p.get("input")), output=usd(p.get("output")),
               cache_read=usd((p.get("details") or {}).get("cache_read")))
    if out["input"] is None or out["output"] is None:
        die(f"no input/output price for {MODEL} in `router models`")
    if out["cache_read"] is None:
        out["cache_read"] = out["input"]
    return out


def cost(p: dict, prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * p["input"] + cached * p["cache_read"] + completion * p["output"]) / 1e6


def router(cli: CLI, arm: str, messages: list[dict], prompt: str, boundary_id: str | None = None,
           echo: bool = False) -> dict | None:
    """One `router chat --body` call: the system prompt and the window go in the body, the new step is the PROMPT."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{arm}.json"   # one file per arm: the arms run at the same time
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": MODEL, "messages": messages}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    args = ["chat", "--body", str(path.relative_to(HERE)) if echo else str(path), "--model", MODEL,
            "--max-tokens", str(MAX_TOKENS), "--temperature", TEMPERATURE]
    if boundary_id:
        args += ["--boundary", boundary_id]
    args.append(prompt)
    t0 = time.time()
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("model calls below are skipped; the memory steps still ran. Top up in the console to see the answers.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    details = u.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    with LOCK:
        cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
        cli.tokens["completion"] += ct  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=(res["choices"][0]["message"]["content"] or "").strip(), prompt_tokens=pt,
                cached=cached or 0, completion_tokens=ct, seconds=round(time.time() - t0, 1))


# --------------------------------------------------------------------------- step 2: set up

def demo_convs(cli: CLI) -> list[dict]:
    return [c for c in paged(cli, ["conv", "list", "--page-size", "100"])
            if (c.get("custom_id") or "").startswith(PREFIX + "-")]


def arm_state(cli: CLI, d: dict, convs: list[dict], bounds: list[dict], arm: str, echo: bool = False) -> dict | None:
    """One arm's memory if all of it is there: project, boundary, the six past sessions; plus whether a run already
    wrote a session of today into it."""
    a = arm.lower()
    p = find_project(cli, f"proj-{a}", echo=echo)
    b = next((x for x in bounds if x.get("custom_id") == cid(f"bnd-{a}")), None)
    past = [c for c in convs if (c.get("custom_id") or "").startswith(cid(a, "s"))]
    today = [c for c in convs if (c.get("custom_id") or "").startswith(cid(a, "today"))]
    if not p or not b:
        return None
    return dict(project=p["id"], boundary=b["id"], past=len(past), today=len(today),
                complete=len(past) == len(d["sessions"]))


def load_world(cli: CLI, quiet: bool = False) -> dict | None:
    d = load_data()
    world = {}
    for k in ACTOR_KEYS:
        a = find_actor(cli, k)
        if not a:
            return None
        world[k] = a["id"]
    convs, bounds = demo_convs(cli), demo_boundaries(cli)
    world["arms"] = {}
    for arm in ARMS:
        st = arm_state(cli, d, convs, bounds, arm, echo=not quiet and arm == "B")
        if not st:
            return None
        world["arms"][arm] = st
    return world


def remove_arm(cli: CLI, arm: str, convs: list[dict], bounds: list[dict]) -> int:
    """Delete one arm's boundary, conversations, project facts and project; returns how many facts went."""
    a = arm.lower()
    for b in bounds:
        if b.get("custom_id") == cid(f"bnd-{a}"):
            cli.run("boundary", "delete", b["id"], echo=False)
    for c in convs:
        if (c.get("custom_id") or "").startswith(cid(a) + "-"):
            cli.run("conv", "delete", c["id"], scoped=True, echo=False)
    p = find_project(cli, f"proj-{a}")
    n = 0
    if p:
        left = project_facts(cli, p["id"])
        for attempt in range(4):
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_fact(cli, f["id"], ["--project", p["id"]]), left))
            n += len(left)
            left = project_facts(cli, p["id"])
            if not left:
                break
            time.sleep(2 * (attempt + 1))
        if left:
            die(f"{len(left)} fact(s) are still in {p['id']} after deleting; run cleanup again")
        cli.run("proj", "delete", p["id"], scoped=True, echo=False)
    return n


def make_arm(cli: CLI, d: dict, world: dict, arm: str, echo: bool) -> dict:
    a = arm.lower()
    proj = cli.run("proj", "create", "--name", f"{d['project_name']} ({arm})", "--custom-id", cid(f"proj-{a}"),
                   scoped=True, echo=echo)["id"]
    bnd = cli.run("boundary", "create", "--name", f"Pair's memory ({arm})", "--custom-id", cid(f"bnd-{a}"),
                  "--projects", proj, scoped=True, echo=echo)["id"]
    return dict(project=proj, boundary=bnd, past=0, today=0, complete=False)


def setup(cli: CLI, reset: bool) -> tuple[dict, list[str]]:
    """(world, arms to fill): an arm is reused when its six past sessions are there and no run has written a session
    of today into it; C and D always have after a run, so they are rebuilt; B is reused unless --reset."""
    d = load_data()
    emit("text", f"\n    {d['person']['display']} — {d['person']['role']}.\n    {d['assistant']['display']} — {d['assistant']['role']}.")
    world = None if reset else load_world(cli)
    convs, bounds = demo_convs(cli), demo_boundaries(cli)
    if world is None:
        if convs or bounds or any(find_project(cli, f"proj-{a.lower()}") for a in ARMS) \
                or any(find_actor(cli, k) for k in ACTOR_KEYS):
            note("--reset: removing the earlier demo memory" if reset else "removing what an earlier run left behind")
            cleanup(cli, quiet=True)
            convs, bounds = [], []
        world = {"rhea": make_actor(cli, "rhea", d["person"]["display"], f"{d['person']['role']}. Demo data.", echo=True),
                 "pair": make_actor(cli, "pair", d["assistant"]["display"], f"{d['assistant']['role']}. Demo data.",
                                    echo=False, actor_type="ASSISTANT"),
                 "arms": {}}
    fill = []
    for arm in ARMS:
        st = world["arms"].get(arm)
        if st and st["complete"] and not st["today"]:
            note(f"{arm}: reusing the memory an earlier run built (six past sessions, nothing from today)")
            continue
        if st:
            why = "a run already wrote today's session into it" if st["today"] else "it is incomplete"
            note(f"{arm}: rebuilding its memory — {why}")
            remove_arm(cli, arm, convs, bounds)
        world["arms"][arm] = make_arm(cli, d, world, arm, echo=not fill)
        fill.append(arm)
    note("three projects with a boundary each — one memory per arm, so what C writes today never reaches B or D")
    emit("world", "", **world)
    return world, fill


# --------------------------------------------------------------------------- step 3: the past sessions

def arm_facts(cli: CLI, world: dict, arm: str, echo: bool = False) -> list[dict]:
    return project_facts(cli, world["arms"][arm]["project"], echo)


def ingest(cli: CLI, world: dict, arms: list[str]) -> None:
    """Each past session becomes a DIRECT conversation in each arm's project, then extraction runs."""
    d = load_data()
    who = {"rhea": world["rhea"], "pair": world["pair"]}
    t0 = time.time()
    jobs = [(arm, s) for s in d["sessions"] for arm in arms]

    def one(n: int, job: tuple[str, dict]) -> str | None:
        arm, s = job
        proj = world["arms"][arm]["project"]
        conv = cli.run("conv", "create", "--project", proj, "--actors", f"{who['rhea']},{who['pair']}",
                       "--kind", "DIRECT", "--name", f"{s['topic']} ({s['at'][:10]})",
                       "--custom-id", cid(arm.lower(), s["id"]), scoped=True, echo=n == 0)["id"]
        for i, t in enumerate(s["turns"]):
            append(cli, conv, who[t["who"]], f"m{i:02d}", utc(at(s) + timedelta(minutes=i)), t["text"],
                   echo=n == 0 and i == 0)
        done = cooked(cli, conv, echo=n == 0, limit=420)
        emit("progress", f"    {arm}  {s['id']}  {at(s):%d %b}  {s['topic']:<22} {len(s['turns'])} messages · extracted · "
                         f"{int(time.time() - t0)} s", session=s["id"], arm=arm)
        return None if done else f"{arm}/{s['id']}"

    one(0, jobs[0])   # the first one alone, so its commands print in order
    with ThreadPoolExecutor(INGEST_WORKERS) as pool:
        late = [x for x in pool.map(lambda a: one(*a), list(enumerate(jobs))[1:]) if x]
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — counted as they stand")
    time.sleep(5)   # cook_finished can come a moment before the facts are listed


def show_memory(cli: CLI, world: dict, d: dict, echo: bool = True) -> dict:
    """Is each agreed value in each arm's memory? Checked before today's session starts."""
    held = {}
    texts = {}
    for arm in ARMS:
        texts[arm] = [f.get("fact") or "" for f in arm_facts(cli, world, arm, echo=echo and arm == "B")]
    emit("text", f"\n    Facts extracted from the six past sessions: "
                 + " · ".join(f"{arm} {len(texts[arm])}" for arm in ARMS))
    emit("text", f"    {'':4}{'agreed value':<44}" + "".join(f"{arm:>4}" for arm in ARMS))
    for c in d["conventions"]:
        row = {}
        for arm in ARMS:
            row[arm] = any(all(re.search(r, t, re.I) for r in c["old_re"]) for t in texts[arm])
        held[c["id"]] = row
        emit("text", f"    {'':4}{c['label'] + ': ' + c['old']:<44}" + "".join(f"{'✓' if row[a] else '·':>4}" for a in ARMS))
    n = sum(all(r.values()) for r in held.values())
    miss = [f"{c['label']} ({', '.join(a for a in ARMS if not held[c['id']][a])})" for c in d["conventions"]
            if not all(held[c["id"]].values())]
    note(f"{n}/{len(held)} agreed values are in all three memories"
         + (" — what each arm knows before today is the same" if not miss else
            f"; not found as written in English: {'; '.join(miss)} (extraction sometimes writes a fact in another "
            "language, and this check only reads English)"))
    emit("memory", "", held=held, facts={a: len(texts[a]) for a in ARMS})
    return held


# --------------------------------------------------------------------------- step 4: today's session

class Writer:
    """Writes one arm's copy of today's session into its conversation, in order, off the hot path — the way an app
    would: the step goes on while the append is in flight. `done` counts messages whose append has returned."""

    def __init__(self, cli: CLI, conv: str, who: dict, echo: bool):
        self.cli, self.conv, self.who, self.echo = cli, conv, who, echo
        self.q: list = []
        self.cv = threading.Condition()
        self.done = 0
        self.sent = 0
        self.closed = False
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def put(self, who: str, text: str) -> None:
        with self.cv:
            self.q.append((self.sent, who, text))
            self.sent += 1
            self.cv.notify()

    def _loop(self) -> None:
        while True:
            with self.cv:
                while not self.q and not self.closed:
                    self.cv.wait()
                if not self.q:
                    return
                i, who, text = self.q.pop(0)
            append(self.cli, self.conv, self.who[who], f"t{i:03d}", utc(datetime.now(timezone.utc)), text,
                   echo=self.echo and i == 0)
            with self.cv:
                self.done = i + 1

    def close(self) -> None:
        with self.cv:
            self.closed = True
            self.cv.notify()
        self.t.join()


class Watcher:
    """Polls one arm's project for today's new values (`fact list --query`) and records when each first shows up."""

    def __init__(self, cli: CLI, project: str, d: dict, t0: float):
        self.cli, self.project, self.t0 = cli, project, t0
        self.todo = {c["id"]: c for c in changed(d)}
        self.seen: dict[str, float] = {}
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self) -> None:
        while not self.stop.is_set() and len(self.seen) < len(self.todo):
            for k, c in self.todo.items():
                if k in self.seen:
                    continue
                try:
                    items = (self.cli.run("fact", "list", "--projects", self.project, "--query", c["new_re"][0],
                                          "--page-size", "20", scoped=True, echo=False) or {}).get("items") or []
                except CLIError:
                    continue
                if any(value_in(c, f.get("fact") or "") == "new" for f in items):
                    self.seen[k] = round(time.time() - self.t0, 1)
            self.stop.wait(POLL_SECONDS)


def session_id() -> str:
    return "LS-" + os.urandom(3).hex().upper()


def system_prompt(d: dict, sid: str) -> str:
    return f"{d['playbook']}\n\nToday is {d['today']}. Session {sid} with {d['person']['display']}."


def window(thread: list[dict], steps: int) -> list[dict]:
    return thread[-2 * steps:]


def held_now(cli: CLI, world: dict, arm: str, c: dict) -> str | None:
    """What the arm's memory says about a changed convention at this moment: "new", "old" or None."""
    items = (cli.run("fact", "list", "--projects", world["arms"][arm]["project"], "--query", c["new_re"][0], "--page-size", "20",
                     scoped=True, echo=False) or {}).get("items") or []
    if any(value_in(c, f.get("fact") or "") == "new" for f in items):
        return "new"
    return "old"


def d_window(cli: CLI, conv: str, writer: Writer, state: dict, thread: list[dict], steps: int) -> list[dict]:
    """D's rule: a step leaves the window only once MemoryLake holds it. `cook-status` finished means every message
    appended before the call has been written into memory, so the watermark moves up to there; everything after
    the watermark stays in the window, however old."""
    sent_before = writer.done
    st = cli.run("conv", "cook-status", conv, scoped=True, echo=False) or {}
    if st.get("cook_finished"):
        state["mark"] = sent_before
    start = min(len(thread) - 2 * steps, state["mark"])
    return thread[max(start, 0):]


def run_session(cli: CLI, world: dict, d: dict, p: dict) -> dict:
    sid = session_id()
    system = system_prompt(d, sid)
    who = {"rhea": world["rhea"], "pair": world["pair"]}
    convs = {}
    for arm in WRITERS:
        convs[arm] = cli.run("conv", "create", "--project", world["arms"][arm]["project"], "--actors",
                             f"{who['rhea']},{who['pair']}", "--kind", "DIRECT", "--name", f"Today {sid}",
                             "--custom-id", cid(arm.lower(), "today", sid.lower()), scoped=True, echo=arm == "C")["id"]
    t0 = time.time()
    writers = {arm: Writer(cli, convs[arm], who, echo=arm == "C") for arm in WRITERS}
    watchers = {arm: Watcher(cli, world["arms"][arm]["project"], d, t0) for arm in WRITERS}
    threads = {a: [] for a in ARMS}
    dstate = {"mark": 0}
    steps, rows = [], []
    win = d["window_steps"]
    emit("text", f"\n    Session {sid} — {d['today']}. {len(d['live'])} steps; B, C and D see each step at the same moment.")
    for n, step in enumerate(d["live"], 1):
        t_step = round(time.time() - t0, 1)
        steps.append(t_step)
        qid = step.get("q")
        if not qid:
            for a in ARMS:
                threads[a] += [{"role": "user", "content": step["user"]}, {"role": "assistant", "content": step["agent"]}]
            for arm in WRITERS:
                writers[arm].put("rhea", step["user"])
                writers[arm].put("pair", step["agent"])
            emit("text", f"  step {n:>2}  +{t_step:>5.1f}s  {step['user'][:92]}{'…' if len(step['user']) > 92 else ''}")
            emit("step_line", "", n=n, t=t_step, said=step["user"])
            time.sleep(d["step_pause"])
            continue
        needs = [conventions(d)[x] for x in d["questions"][qid]["needs"]]
        today = [c for c in needs if c.get("new")]
        first = not rows

        def body(arm: str) -> list[dict]:
            if arm == "D":
                return [{"role": "system", "content": system}] + d_window(cli, convs["D"], writers["D"], dstate,
                                                                        threads["D"], win)
            return [{"role": "system", "content": system}] + window(threads[arm], win)

        def call(arm: str):
            msgs = body(arm)
            r = router(cli, arm, msgs, step["user"], world["arms"][arm]["boundary"], echo=first)
            if r:
                r["window"] = (len(msgs) - 1) // 2
            return r

        def memory(arm: str):
            return {c["id"]: held_now(cli, world, arm, c) for c in today}

        if first:   # the first checkpoint one arm at a time, so the commands print in order
            emit("text", "")
            res = {a: call(a) for a in ARMS}
            mem = {a: memory(a) for a in ARMS}
        else:
            with ThreadPoolExecutor(6) as pool:
                futs = {a: pool.submit(call, a) for a in ARMS}
                mfuts = {a: pool.submit(memory, a) for a in ARMS}
                res = {a: f.result() for a, f in futs.items()}
                mem = {a: f.result() for a, f in mfuts.items()}
        row = dict(step=n, t=t_step, q=qid, said=step["user"], arms={},
                   left_window={c["id"]: n > c["changed_at"] + win for c in today})
        for a in ARMS:
            r = res[a]
            if r:
                r["usd"] = cost(p, r["prompt_tokens"], r["cached"], r["completion_tokens"])
                r["grade"] = grade(d, qid, r["reply"])
                r["memory"] = mem[a]
            row["arms"][a] = r
            threads[a] += [{"role": "user", "content": step["user"]},
                           {"role": "assistant", "content": r["reply"] if r else "(no reply)"}]
        for arm in WRITERS:
            writers[arm].put("rhea", step["user"])
            writers[arm].put("pair", row["arms"][arm]["reply"] if row["arms"][arm] else "(no reply)")
        rows.append(row)
        show_checkpoint(row, d, steps)
        emit("checkpoint", "", **row)
    t_end = round(time.time() - t0, 1)
    note(f"session over at +{t_end:.0f} s; B's copy of it would be written now, when the session closes — after every "
         "question above")
    for w in writers.values():
        w.close()
    return dict(sid=sid, t0=t0, t_end=t_end, steps=steps, rows=rows, convs=convs, watchers=watchers, dstate=dstate)


def show_checkpoint(row: dict, d: dict, steps: list[float]) -> None:
    conv = conventions(d)
    q = d["questions"][row["q"]]
    needs = [conv[x] for x in q["needs"]]
    emit("text", f"\n  step {row['step']:>2}  +{row['t']:>5.1f}s  ▶ {row['q']}  {row['said']}")
    for c in needs:
        if c.get("new"):
            gone = row["left_window"][c["id"]]
            said = steps[c["changed_at"] - 1]
            emit("text", f"           {c['label']}: {c['old']} → {c['new']}, changed at step {c['changed_at']} (+{said:.1f}s); "
                         + ("that step has left the 6-step window" if gone else "still inside the 6-step window"))
        else:
            emit("text", f"           {c['label']}: {c['old']} (agreed in {c['from']}, unchanged)")
    for arm in ARMS:
        r = row["arms"][arm]
        if not r:
            continue
        m = r.get("memory") or {}
        mem = ""
        if m:
            mem = "  memory: " + ", ".join(f"{conv[k]['label'].split()[-1]} {'NEW' if v == 'new' else 'old'}"
                                            for k, v in m.items())
        emit("text", f"    {MARK[r['grade']]} {ARM_SHORT[arm]:<22} window {r['window']:>2} steps · {r['prompt_tokens']:>5,} tok{mem}")
        emit("text", wrap(r["reply"], "        | ", 104))


# --------------------------------------------------------------------------- step 5: the gap

def settle(sess: dict, d: dict) -> None:
    """Keep watching after the last step until every change has reached C's and D's memory (or the limit)."""
    t_lim = time.time() + SETTLE_LIMIT
    need = len(changed(d))
    while time.time() < t_lim and any(len(w.seen) < need for w in sess["watchers"].values()):
        time.sleep(POLL_SECONDS)
    for w in sess["watchers"].values():
        w.stop.set()
        w.t.join()


def show_gap(cli: CLI, world: dict, d: dict, sess: dict) -> dict:
    win = d["window_steps"]
    steps = sess["steps"]
    emit("text", f"\n    Each change: when Rhea said it, when it left the {win}-step window, when it reached memory.")
    emit("text", f"    {'':4}{'change':<40}{'said':>8}{'left window':>13}{'in C memory':>13}{'in D memory':>13}{'gap (C)':>10}")
    gaps = {}
    for c in changed(d):
        said = steps[c["changed_at"] - 1]
        k = c["changed_at"] + win   # the first step whose window no longer has it
        left = steps[k] if k < len(steps) else None
        mc, md = sess["watchers"]["C"].seen.get(c["id"]), sess["watchers"]["D"].seen.get(c["id"])
        gap = round(mc - left, 1) if (mc is not None and left is not None) else None
        gaps[c["id"]] = dict(said=said, left=left, in_c=mc, in_d=md, gap=gap)
        f = lambda x: "—" if x is None else f"+{x:.1f}s"
        emit("text", f"    {'':4}{c['label'] + ' → ' + c['new']:<40}{f(said):>8}{f(left):>13}{f(mc):>13}{f(md):>13}"
                     f"{('—' if gap is None else f'{gap:+.1f}s'):>10}")
    open_gaps = [g for g in gaps.values() if g["gap"] is not None and g["gap"] > 0]
    if open_gaps:
        note(f"{len(open_gaps)}/{len(gaps)} changes left the window before they reached memory — for up to "
             f"{max(g['gap'] for g in open_gaps):.0f} s the only copy of the new value was nowhere a request could see it, "
             "and memory still answered with the old one")
    # D's price: how many steps past the window it carried, at which checkpoints
    extra = [(r["step"], r["arms"]["D"]["window"] - min(win, r["arms"]["C"]["window"]),
              r["arms"]["D"]["prompt_tokens"] - r["arms"]["C"]["prompt_tokens"])
             for r in sess["rows"] if r["arms"].get("D") and r["arms"].get("C")]
    held = [x for x in extra if x[1] > 0]
    if held:
        emit("text", "\n    D kept steps past the window while `cook-status` had not reported its conversation finished (it is one\n"
                     "    flag for the whole conversation, and new steps keep arriving):")
        for step, n, tok in held:
            emit("text", f"      step {step:>2}: {n} step(s) past the window, {tok:+,} prompt tokens compared with C")
    else:
        note("D never had to keep a step past the window: memory already held everything that left it")
    # What C's memory says now: did its own wrong answers write the old value back?
    facts = arm_facts(cli, world, "C")
    emit("text", "\n    What C's memory says now, after the session (the first value stated in each fact):")
    now = {}
    for c in changed(d):
        hits = [(f.get("fact") or "") for f in facts if value_in(c, f.get("fact") or "")]
        vals = [value_in(c, t) for t in hits]
        now[c["id"]] = vals
        emit("text", f"      {c['label']:<22} " + ("no fact" if not hits else f"{len(hits)} fact(s)"))
        for t in hits:
            emit("text", wrap(t, f"        {c[value_in(c, t)]:<17} “", 104) + "”")
    emit("gap", "", gaps=gaps, extra=extra, c_now=now, mark=sess["dstate"]["mark"])
    return dict(gaps=gaps, extra=extra, c_now=now)


# --------------------------------------------------------------------------- step 6: tally

def tally(rows: list[dict], arm: str, d: dict) -> dict:
    rs = [r["arms"][arm] for r in rows if r["arms"].get(arm)]
    after = [r["arms"][arm] for r in rows if r["arms"].get(arm) and any(r["left_window"].values())]
    t = dict(calls=len(rs), prompt=sum(r["prompt_tokens"] for r in rs), usd=sum(r["usd"] for r in rs),
             grades={g: sum(1 for r in rs if r.get("grade") == g) for g in MARK},
             after={g: sum(1 for r in after if r.get("grade") == g) for g in MARK}, n_after=len(after))
    return t


def show_tally(rows: list[dict], d: dict) -> dict:
    nq = len(rows)
    tallies = {a: tally(rows, a, d) for a in ARMS}
    n_after = tallies["B"]["n_after"]
    emit("text", f"\n    {'':42}{'all ' + str(nq) + ' questions':>16}{'after a change left the window':>34}{'prompt tok':>12}")
    for a in ARMS:
        t = tallies[a]
        g, ga = t["grades"], t["after"]
        emit("text", f"    {ARM_LABEL[a]:<42}{g['ok']:>10} ✓ {g['stale']} ⟲{'':>2}"
                     f"{ga['ok']:>22} ✓ {ga['stale']} ⟲ {ga['unknown']} ∅ {ga['made']} ✗{t['prompt']:>10,}")
    emit("text", "")
    note(f"⟲ = answered with the value from before today's change; {n_after} of the {nq} questions needed a value changed "
         "today whose step had already left the window")
    out = {a: t for a, t in tallies.items()}
    emit("tally", "", tallies=out)
    return out


# --------------------------------------------------------------------------- the pipeline

def archive_report() -> None:
    """Move the last run's report to out/runs/, so every run's numbers are kept (a rerun or a cleanup moves it)."""
    f = OUT / "report.json"
    if f.exists():
        (OUT / "runs").mkdir(parents=True, exist_ok=True)
        f.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_grader(d)
    archive_report()
    banner(2, TOTAL, "Set up — Rhea, Pair, and one memory per arm")
    world, fill = setup(cli, reset)
    banner(3, TOTAL, "The past — six sessions, August to September, into each memory")
    for s in d["sessions"]:
        emit("text", f"      {s['id']}  {at(s):%a %d %b}  {s['topic']}")
    if fill:
        emit("text", f"\n    Each session goes into {', '.join(fill)} as a conversation; extraction turns it into facts.")
        ingest(cli, world, fill)
    held = show_memory(cli, world, d)
    banner(4, TOTAL, f"Today — {len(d['live'])} steps, three changes at the start, {len(checkpoints(d))} questions")
    p = prices(cli, echo=False)
    emit("text", wrap("Every request carries the playbook, the last 6 steps and what MemoryRouter attaches from that arm's "
                      "memory (`--boundary`). B writes today's session to memory when the session closes. C writes each "
                      "step as it happens. D writes like C, and a step leaves its window only once `cook-status` says "
                      "memory holds it.", "    ", 108))
    sess = run_session(cli, world, d, p)
    banner(5, TOTAL, "The gap — said, out of the window, in memory")
    note("waiting until every change has reached C's and D's memory (up to "
         f"{SETTLE_LIMIT} s after the last step)")
    settle(sess, d)
    gap = show_gap(cli, world, d, sess)
    banner(6, TOTAL, "Tally")
    tallies = show_tally(sess["rows"], d)
    report = dict(at=utc(datetime.now(timezone.utc)), model=MODEL, prices=p, world=world, held=held, sid=sess["sid"],
                  steps=sess["steps"], rows=sess["rows"], gap=gap, tallies=tallies, t_end=sess["t_end"])
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    note("every request, reply, window size and timing is in out/report.json")
    return report


# --------------------------------------------------------------------------- read-only commands

def ask(cli: CLI, question: str) -> list[dict]:
    """A new session's first question to each arm: no window, only what its memory holds now."""
    d = load_data()
    world = load_world(cli, quiet=True)
    if not world:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    p = prices(cli, echo=False)
    system = system_prompt(d, session_id())
    out = []
    for arm in ARMS:
        r = router(cli, arm, [{"role": "system", "content": system}], question, world["arms"][arm]["boundary"],
                   echo=arm == "B")
        if not r:
            continue
        r["usd"] = cost(p, r["prompt_tokens"], r["cached"], r["completion_tokens"])
        emit("text", f"\n  {ARM_LABEL[arm]}   {r['prompt_tokens']:,} tok")
        emit("text", wrap(r["reply"], "    | ", 104))
        out.append(dict(arm=arm, **r))
    emit("answered", "", question=question, rows=out)
    return out


def facts(cli: CLI) -> None:
    world = load_world(cli, quiet=True)
    if not world:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    d = load_data()
    for arm in ARMS:
        fs = arm_facts(cli, world, arm, echo=True)
        emit("text", f"\n    {ARM_LABEL[arm]} — {len(fs)} facts; the conventions changed today:")
        for c in changed(d):
            hits = [f.get("fact") or "" for f in fs if value_in(c, f.get("fact") or "")]
            for t in hits:
                emit("text", wrap(t, f"      {c[value_in(c, t)]:<17} ", 104))
            if not hits:
                emit("text", f"      —    no fact about the {c['label']}")
    emit("facts_done", "")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    bounds = demo_boundaries(cli, echo=not quiet)
    convs = demo_convs(cli)
    nfacts = sum(remove_arm(cli, arm, convs, bounds) for arm in ARMS)
    # anything left with the prefix (a run stopped half-way)
    for b in demo_boundaries(cli):
        cli.run("boundary", "delete", b["id"], echo=False)
    for c in demo_convs(cli):
        cli.run("conv", "delete", c["id"], scoped=True, echo=False)
    found = [(k, find_actor(cli, k, echo=not quiet and k == ACTOR_KEYS[0])) for k in ACTOR_KEYS]
    for _, a in found:
        if a:
            nfacts += delete_actor(cli, a["id"])
    if not quiet:
        n_act = sum(1 for _, a in found if a)
        note(f"deleted {len(bounds)} boundaries, {len(convs)} conversations, {nfacts} facts, {n_act} actors (facts "
             "first: a deleted actor's facts could never be removed) and the arms' projects"
             if (bounds or convs or n_act or nfacts) else "nothing to delete")
    archive_report()   # kept, not deleted: every run's numbers survive a cleanup
    for f in OUT.glob("body-*.json"):
        f.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "facts", "ask", "cleanup"],
                    help="run = full demo (default); facts = what each arm's memory says about today's changes; "
                         "ask \"<question>\" = a new session's first question to B, C and D; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: the question")
    ap.add_argument("--reset", action="store_true", help="rebuild the memory instead of reusing an earlier run's")
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
    if args.command == "facts":
        banner(2, 2, "What each arm's memory says now")
        facts(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die('usage: python3 demo.py ask "<question>"   (e.g. "Which port is my local Postgres on?")')
        banner(2, 2, "A new session's first question, to B, C and D")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py facts` or `python3 demo.py ask \"Which port is my local Postgres on?\"`; "
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

#!/usr/bin/env python3
"""stage-handoff-memory — the next pipeline stage reads memory before the last one's write is in it.

Ferrowind (a fictional SaaS company) runs an account-change pipeline on a queue. Stage 1 (intake) reads what a customer
asked for and writes it into memory; then it puts a light message on the queue — just the customer and the ticket.
Stage 2 (fulfilment) picks the message up, asks memory through MemoryRouter what to do, and does it: creates the
environment in a region, sends the invoice to a contact, provisions seats.

The queue hands the message over in about a second. MemoryLake reads a conversation message in the background, in
twenty to forty seconds. So stage 2 reads the memory from *before* stage 1's write — and answers with the old region,
the old contact, the old seat count, as confidently as when it is right. Three pipelines, the same eight customers:
A stage 1 appends its note and enqueues at once · B stage 1 waits until the conversation reports `cook_finished`,
then enqueues · C stage 1 also writes its result with `fact add` (a dated fact that names the value it replaces) and
puts the fact id in the queue message, then enqueues at once.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 90 calls on gpt-4o-mini per run).
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

MAX_TOKENS = 80      # replies are one line
PREFIX = "mlu-qdp"  # memorylake-usecases / stage-handoff-memory (queue-driven pipelines)


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
            tail = shown[-2:] if shown[-2] == "--workspace" else []
            body = shown[:len(shown) - len(tail)]
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


def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, k) for k in actor_keys(d)))


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


# --------------------------------------------------------------------------- MemoryRouter

LOCK = threading.Lock()


def router(cli: CLI, prompt: str, system: str, boundary_id: str | None = None, max_tokens: int = MAX_TOKENS,
           echo: bool = False) -> dict | None:
    """One `router chat` call. Without a boundary it is the plain model call — the baseline for ∅."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["chat", prompt, "--model", MODEL, "--max-tokens", str(max_tokens), "--temperature", TEMPERATURE, "--system", system]
    if boundary_id:
        args += ["--boundary", boundary_id]
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("model replies below are skipped; the memory steps still ran. Top up in the console to see the replies.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    with LOCK:
        cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
        cli.tokens["completion"] += ct  # type: ignore[attr-defined]
        cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=(res["choices"][0]["message"]["content"] or "").strip(), prompt_tokens=pt)


BASE: dict = {}


def baseline(cli: CLI, prompt: str, system: str, echo: bool = False) -> int:
    """Prompt tokens of the same call without a boundary: a boundary call with exactly this many had nothing attached."""
    k = (prompt, system)
    if k not in BASE:
        r = router(cli, prompt, system, max_tokens=1, echo=echo)
        BASE[k] = r["prompt_tokens"] if r else 0
    return BASE[k]


# --------------------------------------------------------------------------- writing a message

def append(cli: CLI, conv: str, actor: str, custom_id: str, ts: str, text: str, echo: bool) -> str | None:
    for attempt in range(5):
        try:
            res = cli.run("conv", "msg", "append", conv, "--actor", actor, "--custom-id", custom_id, "--timestamp", ts,
                          "--text", text, scoped=True, echo=echo and attempt == 0)
            return (res or {}).get("id") if isinstance(res, dict) else None
        except CLIError as e:
            # a resend after a dropped connection: the custom_id is already there, so the message is too
            if e.has("custom_id_conflict", "already exists") and attempt:
                return None
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))






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
TEMPERATURE = "0.7"   # an assistant's usual setting: the same question gets a slightly different reply each time
RUNS = 3              # stage-2 reads per customer and pipeline, all at the same moment
QUEUE_S = 0.5         # how long the queue takes to hand a message to stage 2
COOK_LIMIT = 300      # B: the longest stage 1 waits for cook_finished
COPIES = ("now", "cooked", "fact")   # the three projects
COPY_NAME = {"now": "enqueue at once", "cooked": "enqueue after cook_finished", "fact": "fact add + fact id in the message"}
ARMS = ("A", "B", "C")
ARM_LABEL = {"A": "A  enqueue at once", "B": "B  wait for cook_finished", "C": "C  fact add, id in the message"}
ARM_COPY = {"A": "now", "B": "cooked", "C": "fact"}
MARK = {"ok": "✓", "stale": "↺", "unknown": "?", "other": "–"}
GRADE_NAME = {"ok": "the new value", "stale": "the value from before stage 1's write", "unknown": "Unknown",
              "other": "neither value"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "accounts.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    return [d["bot"]["key"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=indent)


def customer(d: dict, key: str) -> dict:
    return next(c for c in d["customers"] if c["id"] == key)


def message_ts(d: dict, n: int) -> str:
    return f"{d['today']}T09:{n:02d}:00Z"


def queue_message(d: dict, c: dict, fact_id: str | None = None) -> str:
    """What goes on the queue: light — the customer and the ticket; C adds the id of the fact stage 1 wrote."""
    m = {"queue": d["queue"], "customer": c["name"], "ticket": c["ticket"]}
    if fact_id:
        m["fact_id"] = fact_id
    return json.dumps(m)


# --------------------------------------------------------------------------- reading a fact or a reply

PREVIOUSLY = re.compile(r"\bpreviously\b|\bformerly\b|\breplaces?\b|\binstead of\b|\bno longer\b|\bnot by\b|\bnot sent by\b", re.I)
# "switching from Okta to Entra ID": the value after "from" is the old one; drop the phrase, keep the rest.
FROM_OLD = re.compile(r"\bfrom (?:Okta|us-east|the US region|US dollars|USD|email|the Team plan with 25 seats|dana\.roe\S*)", re.I)
LEAD_UNKNOWN = re.compile(r"^\W*(?:answer:\s*)?unknown\b", re.I)
NOT_KNOWN = re.compile(r"\bunknown\b|\bno (?:record|information|data|details?)\b|\bdon'?t have\b|\bdo not have\b|"
                       r"\bnot (?:sure|available|specified|recorded|known)\b", re.I)


def asserted(values: dict, text: str) -> str | None:
    """The value a sentence states as the case: the part before any 'previously / replaces / no longer …';
    of the values it names, the one named first."""
    t = PREVIOUSLY.split(text or "")[0]
    t = FROM_OLD.sub(" ", t)
    t = re.sub(r"\d{4}-\d\d-\d\d", " ", t)  # a date is not a seat count
    best = None
    for v, rx in values.items():
        m = re.search(rx, t, re.I | re.A)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), v)
    return best[1] if best else None


def answer_line(reply: str) -> str:
    m = re.search(r"answer\s*:\s*(.*)", reply or "", re.I)
    return m.group(1) if m else (reply or "")


def grade(c: dict, reply: str) -> str:
    """One grade per reply, read from its 'Answer:' line: ✓ the new value · ↺ the old one · ? Unknown · – neither."""
    line = answer_line(reply)
    if LEAD_UNKNOWN.match(line):
        return "unknown"
    v = asserted(c["values"], line)
    if v == c["current"]:
        return "ok"
    if c["stale"] and v == c["stale"]:
        return "stale"
    return "unknown" if NOT_KNOWN.search(line) else "other"


GRADE_CASES = [  # (customer, reply, grade) — checked offline before every run
    ("orvane", "Answer: eu-central. Orvane Labs asked to move to the EU region.", "ok"),
    ("orvane", "Answer: us-east. Orvane Labs' workspace is hosted in the US region.", "stale"),
    ("orvane", "Answer: eu-central, replacing us-east.", "ok"),
    ("pemberlin", "Answer: li.wen@pemberlin.example. Dana Roe has left.", "ok"),
    ("pemberlin", "Answer: dana.roe@pemberlin.example.", "stale"),
    ("quassett", "Answer: 60 seats. They upgraded from 25.", "ok"),
    ("quassett", "Answer: 25. Quassett Health is on the Team plan.", "stale"),
    ("tirlow", "Answer: In their shared Slack channel #ferrowind-incidents, not by email.", "ok"),
    ("tirlow", "Answer: By email to ops@tirlow.example.", "stale"),
    ("wrenfield", "Answer: EUR. Their billing entity moved to Dublin.", "ok"),
    ("wrenfield", "Answer: USD. Wrenfield Analytics is invoiced in US dollars.", "stale"),
    ("halvex", "Answer: Microsoft Entra ID. They are switching from Okta.", "ok"),
    ("halvex", "Answer: Okta.", "stale"),
    ("brismoor", "Answer: us-west.", "ok"),
    ("calderine", "Answer: 10 seats.", "ok"),
    ("halvex", "Ticket T-4106 intake note: Halvex Studio is switching SSO from Okta to Microsoft Entra ID.", "ok"),
    ("orvane", "Answer: eu-central. Moving from us-east for data residency.", "ok"),
    ("calderine", "Answer: Unknown (as of 2026-10-18).", "unknown"),
    ("quassett", "Answer: 25 seats as of 2026-09-02.", "stale"),
    ("calderine", "Answer: Unknown. No seat count on record.", "unknown"),
]


def check_grader(d: dict) -> None:
    bad = [(k, r, g, grade(customer(d, k), r)) for k, r, g in GRADE_CASES if grade(customer(d, k), r) != g]
    if bad:
        die("grader self-check failed: " + "; ".join(f"{k} {r!r} → {got} (want {g})" for k, r, g, got in bad))


def holds(c: dict, facts: list[str]) -> dict:
    """What the facts in one project say about one customer's field: does any fact state the new value?"""
    mine = [f for f in facts if re.search(re.escape(c["name"].split()[0]), f, re.I)]
    new = [f for f in mine if asserted(c["values"], f) == c["current"]]
    old = [f for f in mine if c["stale"] and asserted(c["values"], f) == c["stale"]]
    return dict(new=bool(new), old=bool(old), shown=(new or old or mine or [""])[0], n=len(mine))


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for all three, and no pipeline letter in it: a project's name is context for extraction.
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} customer accounts",
                                       "--custom-id", cid(c), "--description", f"{d['org']} customer accounts. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects with the same name — one per pipeline variant")
    b = d["bot"]
    world["actors"][b["key"]] = make_actor(cli, b["key"], b["display"], f"{b['role']}. Demo data.", echo=True,
                                           actor_type="ASSISTANT")
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"Fulfilment stage ({COPY_NAME[c]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what stage 2 reads through MemoryRouter")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    d = load_data()
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
    for c in COPIES:
        p = find_project(cli, c)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["projects"][c] = p["id"]
    for k in actor_keys(d):
        a = find_actor(cli, k)
        if a:
            world["actors"][k] = a["id"]
    world["boundaries"] = {b["custom_id"][len(PREFIX) + 1:]: b["id"] for b in demo_boundaries(cli)}
    return world


# --------------------------------------------------------------------------- step 3: the account records

def records(cli: CLI, world: dict) -> None:
    d = load_data()
    texts = [c["record"] for c in d["customers"]]
    emit("text", f"\n    What {d['org']} already knows: the account records imported on {d['records_date']}, one per customer.")
    for n, copy in enumerate(COPIES):
        res = cli.run("fact", "add", *texts, "--project", world["projects"][copy], scoped=True, echo=n == 0)
        if len((res or {}).get("facts") or []) != len(texts):
            die(f"`fact add` returned {len((res or {}).get('facts') or [])} facts for {len(texts)} texts")
    for c in d["customers"]:
        emit("text", wrap(c["record"], "      · ", 118))
    note(f"the same {len(texts)} records in all three projects")
    emit("records", "", rows=[dict(id=c["id"], name=c["name"], field=c["field"], record=c["record"]) for c in d["customers"]])
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact add`


# --------------------------------------------------------------------------- step 4: the handoff

def wait_read(cli: CLI, conv: str, msg: str | None, echo: bool, limit: int = COOK_LIMIT) -> bool:
    """A message is read when it shows up in its extraction batch and the conversation reports cook_finished."""
    t0, first = time.time(), echo
    while time.time() - t0 < limit:
        try:
            batch = cli.run("conv", "consumed-messages", conv, "--message", msg, scoped=True, echo=first) if msg else [1]
            items = batch.get("items") if isinstance(batch, dict) else batch
            if items:
                st = cli.run("conv", "cook-status", conv, scoped=True, echo=first) or {}
                if st.get("cook_finished"):
                    return True
            first = False
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
        time.sleep(2)
    return False


def stage2(cli: CLI, world: dict, arm: str, c: dict, echo: bool) -> list[dict]:
    """Stage 2 picks up the message and reads memory: RUNS replies at the same moment."""
    system = system_prompt(load_data())

    def one(i):
        r = router(cli, c["ask"], system, world["boundaries"][ARM_COPY[arm]], echo=echo and i == 0)
        if not r:
            return dict(item=c["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="")
        return dict(item=c["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"],
                    empty=r["prompt_tokens"] <= BASE[(c["ask"], system)], grade=grade(c, r["reply"]))
    with ThreadPoolExecutor(RUNS) as pool:
        return list(pool.map(one, range(RUNS)))


def handoff(cli: CLI, world: dict, arm: str, c: dict, n: int, echo: bool) -> dict:
    """Stage 1 writes, enqueues; stage 2 reads. Times are seconds from the moment stage 1 starts writing."""
    d = load_data()
    copy = ARM_COPY[arm]
    bot = world["actors"][d["bot"]["key"]]
    conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", bot, "--kind", "GROUP",
                   "--name", f"Intake {c['ticket']}", "--custom-id", cid("conv", copy, c["id"]), scoped=True, echo=echo)["id"]
    t0 = time.time()
    mid = append(cli, conv, bot, f"{c['ticket']}-note", message_ts(d, n), c["note"], echo=echo)
    out = dict(item=c["id"], arm=arm, conv=conv, written=round(time.time() - t0, 1), fact_id=None, waited=None, cooked=None)
    if arm == "B":
        out["cooked"] = wait_read(cli, conv, mid, echo=echo)
        out["waited"] = round(time.time() - t0, 1)
    if arm == "C":
        res = cli.run("fact", "add", c["result"], "--project", world["projects"][copy], scoped=True, echo=echo)
        out["fact_id"] = ((res or {}).get("facts") or [{}])[0].get("id")
        out["written"] = round(time.time() - t0, 1)
    out["message"] = queue_message(d, c, out["fact_id"])
    time.sleep(QUEUE_S)
    out["read_at"] = round(time.time() - t0, 1)
    out["rows"] = stage2(cli, world, arm, c, echo)
    if arm == "A":
        # When did the write become readable? Not part of the handoff: stage 2 has already acted.
        st = cli.run("conv", "cook-status", conv, scoped=True, echo=False) or {}
        out["cooked_at_read"] = bool(st.get("cook_finished"))
    return out


def show_handoff(arm: str, h: dict, c: dict) -> None:
    marks = " ".join(mark(r) for r in h["rows"])
    first = next((r["reply"] for r in h["rows"] if r.get("grade") and r["grade"] != "ok" and not r.get("empty")), None) \
        or next((r["reply"] for r in h["rows"] if r.get("reply")), "")
    first = " ".join(answer_line(first).split())
    if arm == "A":
        how = f"wrote +{h['written']:>4}s · read +{h['read_at']:>4}s"
    elif arm == "B":
        how = f"cook_finished +{h['waited']:>5}s" if h["cooked"] else f"no cook_finished in {COOK_LIMIT}s"
    else:
        how = f"fact add +{h['written']:>4}s · read +{h['read_at']:>4}s"
    emit("text", f"    {c['name']:<20} {how:<34} {marks:<7} {first[:58]}")
    emit("handoff", "", **dict(h, current=c["current"], stale=c["stale"]))


def run_handoffs(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    c0 = d["customers"][0]
    emit("text", f"\n    Stage 1 writes this into memory (one message in the ticket's conversation):")
    emit("text", wrap(c0["note"], "      ", 110))
    emit("text", f"    and puts this on the {d['queue']} queue:  {queue_message(d, c0)}")
    emit("text", f"    Stage 2 picks it up {QUEUE_S} s later and asks memory, through MemoryRouter, before it acts:")
    emit("text", f"      “{c0['ask']}”   ({RUNS} reads at the same moment, temperature {TEMPERATURE})")
    for n, c in enumerate(d["customers"]):
        baseline(cli, c["ask"], system, echo=n == 0)
    note("each question once without --boundary first: a read with exactly that many prompt tokens had nothing attached (∅)")
    out = []
    for arm in ARMS:
        emit("text", f"\n  {ARM_LABEL[arm]}" + {"A": " — stage 1 appends its note and enqueues",
                                                 "B": f" — stage 1 waits until the conversation reports cook_finished (up to {COOK_LIMIT} s), then enqueues",
                                                 "C": " — stage 1 also writes its result with `fact add`, puts the fact id in the message, enqueues"}[arm])
        t0 = time.time()
        jobs = list(enumerate(d["customers"]))
        with ThreadPoolExecutor(len(jobs)) as pool:
            res = list(pool.map(lambda j: handoff(cli, world, arm, j[1], j[0], echo=j[0] == 0), jobs))
        for (n, c), h in zip(jobs, res):
            show_handoff(arm, h, c)
        if arm == "C":
            emit("text", f"    queue message: {res[0]['message']}")
            emit("text", wrap(d['customers'][0]['result'], "    fact: ", 110))
        t = tally([r for h in res for r in h["rows"]])
        emit("text", f"    → {t['ok']}/{t['n']} the new value · ↺ {t['stale']} the old one" + (f" · ∅ {t['empty']}" if t["empty"] else "")
             + f"   ({int(time.time() - t0)} s for all eight)")
        emit("arm_done", "", arm=arm, tally=t)
        out += res
    return out


# --------------------------------------------------------------------------- step 5: what memory holds now

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, copy in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][copy], echo=echo and n == 0)]
        names = [c["name"].split()[0] for c in d["customers"]]
        unnamed = [f for f in facts if not any(re.search(re.escape(n), f, re.I) for n in names)]
        out[copy] = dict(all=facts, customers={c["id"]: holds(c, facts) for c in d["customers"]}, unnamed=unnamed)
    return out


def late_reads(cli: CLI, world: dict) -> list[dict]:
    """Every question again through every pipeline, once extraction has had its time: what a later reader gets.
    For A it is what stage 2 would have read had it waited; for B and C it shows what the extraction of stage 1's
    note added since — including facts that land on the wrong customer."""
    d = load_data()
    system = system_prompt(d)

    def one(job):
        a, c = job
        r = router(cli, c["ask"], system, world["boundaries"][ARM_COPY[a]])
        if not r:
            return dict(item=c["id"], arm=a, reply="", grade=None, empty=False)
        return dict(item=c["id"], arm=a, reply=r["reply"], empty=r["prompt_tokens"] <= BASE[(c["ask"], system)],
                    grade=grade(c, r["reply"]))
    with ThreadPoolExecutor(8) as pool:
        return list(pool.map(one, [(a, c) for a in ARMS for c in d["customers"]]))


def show_census(d: dict, cs: dict, hand: list[dict], late: list[dict] | None) -> None:
    ch = [c for c in d["customers"] if c["kind"] == "change"]
    emit("text", f"\n    In memory now — does a fact naming the customer state the new value? (fact list) · Asked again now (one read each)")
    emit("text", f"    {'customer':<20}{'field':<19}{'new value':<12}{'A':<5}{'B':<5}{'C':<9}{'A':<4}{'B':<4}{'C':<4}")
    lmap = {(r["arm"], r["item"]): r for r in late or []}
    for c in d["customers"]:
        cells = "".join(f"{('yes' if cs[copy]['customers'][c['id']]['new'] else 'NO') if c['kind'] == 'change' else '':<5}" for copy in COPIES)
        tail = "".join(f"{mark(lmap[(a, c['id'])]) if (a, c['id']) in lmap else '':<4}" for a in ARMS)
        tag = "  (control)" if c["kind"] == "control" else ""
        emit("text", f"    {c['name']:<20}{c['field']:<19}{c['current']:<12}{cells}    {tail}{tag}")
    for copy, a in zip(COPIES, ARMS):
        for f in cs[copy].get("unnamed") or []:
            emit("text", wrap(f"{a} · a fact that names no customer: {f}", "      · ", 118))
    for r in late or []:
        if r.get("grade") not in (None, "ok") and not r.get("empty"):
            emit("text", wrap(f"asked again · {r['arm']} · {customer(d, r['item'])['name']}: {answer_line(r['reply'])}", "      · ", 118))
    bh = {h["item"]: h for h in hand if h["arm"] == "B"}
    gaps = [c for c in ch if not cs["cooked"]["customers"][c["id"]]["new"]]
    for c in gaps:
        h = bh.get(c["id"], {})
        emit("text", wrap(f"B · {c['name']}: cook_finished {'came at +' + str(h.get('waited')) + ' s' if h.get('cooked') else 'never came'}, "
                          f"and no fact states the new value. Closest fact: {cs['cooked']['customers'][c['id']]['shown'] or '(none)'}",
                          "      · ", 118))
    t = {copy: sum(1 for c in ch if cs[copy]["customers"][c["id"]]["new"]) for copy in COPIES}
    emit("text", f"\n    the new value is in memory for A {t['now']}/{len(ch)} · B {t['cooked']}/{len(ch)} · C {t['fact']}/{len(ch)} "
                 f"(A and B: extracted from the note; C: the fact stage 1 wrote)"
                 + ("".join(f"\n    asked again now, {a}: {sum(1 for r in late if r['arm'] == a and r.get('grade') == 'ok' and not r.get('empty'))}"
                            f"/{sum(1 for r in late if r['arm'] == a and r.get('grade') and not r.get('empty'))} the new / unchanged value"
                            for a in ARMS) if late else ""))
    emit("census", "", **{k: dict(customers=v["customers"], all=v["all"], unnamed=v.get("unnamed") or []) for k, v in cs.items()},
         late=late or [], extracted=t)


# --------------------------------------------------------------------------- step 6: score

def system_prompt(d: dict) -> str:
    return d["system"].format(org=d["org"], today=date.fromisoformat(d["today"]).strftime("%A %Y-%m-%d"))


def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK[r["grade"]] if r.get("grade") else "·"


def tally(rows: list[dict]) -> dict:
    d = load_data()
    kind = {c["id"]: c["kind"] for c in d["customers"]}
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    t = dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
             empty=sum(1 for r in rows if r.get("grade") and r.get("empty")))
    for g in MARK:
        t[g] = sum(1 for r in live if r["grade"] == g)
    for part in ("change", "control"):
        sub = [r for r in live if kind[r["item"]] == part]
        t[part + "_n"], t[part + "_ok"] = len(sub), sum(1 for r in sub if r["grade"] == "ok")
    return t


def score(hand: list[dict]) -> dict:
    d = load_data()
    rows = [r for h in hand for r in h["rows"]]
    out: dict = {"answers": {}, "by_item": {}, "latency": {}}
    emit("text", f"    per customer, the new value / graded ({RUNS} reads per pipeline; ∅ = nothing attached, not graded):")
    emit("text", f"      {'':<50}" + "  ".join(f"{a:<11}" for a in ARMS))
    for c in d["customers"]:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == c["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            marks="".join(mark(r) for r in mine))
        out["by_item"][c["id"]] = cells
        cell = lambda x: f"{x['ok']}/{x['n']} {x['marks']}"  # noqa: E731
        tag = " (control)" if c["kind"] == "control" else ""
        emit("text", f"      {(c['name'] + ' · ' + c['field'] + ' = ' + c['current'] + tag)[:49]:<50}" + "  ".join(f"{cell(cells[a]):<11}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
        hs = [h for h in hand if h["arm"] == a]
        lat = sorted(h["read_at"] for h in hs)
        out["latency"][a] = dict(median=lat[len(lat) // 2], max=lat[-1],
                                 no_cook=sum(1 for h in hs if a == "B" and not h["cooked"]))
    t, L = out["answers"], out["latency"]
    emit("text", f"    {'':<34}{'new value':>11}{'↺ old':>8}{'?':>4}{'–':>4}{'changed':>11}{'control':>10}{'stage 2 reads at':>20}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<34}{x['ok']:>7}/{x['n']:<3}{x['stale']:>8}{x['unknown']:>4}{x['other']:>4}"
                     f"{x['change_ok']:>7}/{x['change_n']:<3}{x['control_ok']:>6}/{x['control_n']:<3}"
                     f"{'+' + str(L[a]['median']) + ' s (max ' + str(L[a]['max']) + ')':>20}"
                     + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    emit("text", "\n    ✓ the new value · ↺ the value from before stage 1's write · ? Unknown · – neither · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_grader(d)
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s account memory, three pipelines")
    world = setup(cli)

    banner(3, TOTAL, "What memory knows already — last month's account records")
    records(cli, world)

    banner(4, TOTAL, "Stage 1 writes, the queue hands over, stage 2 reads — eight customers, three pipelines")
    hand = run_handoffs(cli, world)

    banner(5, TOTAL, "What memory holds now")
    time.sleep(20)
    cs = census(cli, world)
    late = late_reads(cli, world)
    show_census(d, cs, hand, late)

    banner(6, TOTAL, "The score — per customer and per pipeline")
    sc = score(hand)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census={k: dict(customers=v["customers"], all=v["all"], unnamed=v.get("unnamed") or []) for k, v in cs.items()}, late=late,
                   handoffs=hand, score=sc, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok, today=d["today"])  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, read and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """Re-read the three projects: does a fact state each customer's new value — and what stage 2 reads now."""
    d = load_data()
    world = load_world(cli)
    BASE.clear()
    for c in d["customers"]:
        baseline(cli, c["ask"], system_prompt(d))
    cs = census(cli, world)
    rep = OUT / "report.json"
    hand = json.loads(rep.read_text(encoding="utf-8"))["handoffs"] if rep.exists() else []
    show_census(d, cs, hand, late_reads(cli, world))
    return cs


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each pipeline's memory; graded when it is one of the eight."""
    d = load_data()
    world = load_world(cli)
    c = next((c for c in d["customers"] if c["ask"].lower() == question.strip().lower()), None)
    rows = []
    system = system_prompt(d)
    for n, a in enumerate(ARMS):
        base = baseline(cli, question, system, echo=n == 0)
        r = router(cli, question, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = grade(c, r["reply"]) if c else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, grade=g))
    if c:
        emit("text", f"\n  new: {c['current']} · before stage 1's write: {c['stale'] or '(unchanged)'}")
    emit("asked", "", question=question, item=c and c["id"], rows=rows, current=c and c["current"])
    return rows


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = [c for c in paged(cli, ["conv", "list", "--page-size", "100"])
             if (c.get("custom_id") or "").startswith(PREFIX + "-")]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda c: cli.run("conv", "delete", c["id"], scoped=True, echo=False), convs))
    nfacts = 0
    found = [(k, find_actor(cli, k, echo=not quiet and k == actor_keys(d)[0])) for k in actor_keys(d)]
    for _, a in found:
        if a:
            nfacts += delete_actor(cli, a["id"])
    nproj = 0
    for c in COPIES:
        p = find_project(cli, c)
        if not p:
            continue
        left = project_facts(cli, p["id"])
        for attempt in range(4):
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_fact(cli, f["id"], ["--project", p["id"]]), left))
            nfacts += len(left)
            left = project_facts(cli, p["id"])
            if not left:
                break
            time.sleep(2 * (attempt + 1))
        if left:
            die(f"{len(left)} fact(s) are still in {p['id']} after deleting; run cleanup again")
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet and nproj == 0)
        nproj += 1
    if not quiet:
        n_act = sum(1 for _, a in found if a)
        note(f"deleted {len(bs)} boundaries, {len(convs)} conversations, {nfacts} facts, {n_act} actor (facts first: a "
             f"deleted actor's facts could never be removed) and {nproj} projects" if (bs or convs or n_act or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


EXAMPLE = "Which region should Orvane Labs' new environment be created in?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = re-read whether each pipeline's memory holds the new values; "
                         "ask \"<question>\" = one question through A, B and C; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: the question")
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
    if args.command == "memory":
        banner(2, 2, "What each pipeline's memory holds now")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the fulfilment stage, through A, B and C")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py memory`, or `python3 demo.py ask \"{EXAMPLE}\"`; "
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

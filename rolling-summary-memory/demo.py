#!/usr/bin/env python3
"""rolling-summary-memory — a summary buffer either outgrows its budget or drops whole sessions in one compaction.

Dana Okafor (a fictional platform engineer at Kestrel Freight) works with Ferry, the team's AI assistant, over twelve
short sessions in three weeks: database ports, cache settings, release rules, webhook retries, security dates, a queue
cutover — then incident reviews, capacity, on-call and cost, dense with other numbers. Two later sessions change
earlier values (the pool cap 40 → 64, the canary hold 20 → 30 minutes). The same twelve sessions go into two memories:

  A  a summary buffer — after every session the app asks the model to fold the session into the running summary,
     "under 150 words". Three independent chains, as three instances of the same app would keep them.
  B  MemoryLake — each session is a conversation; extraction turns it into facts; MemoryRouter attaches what fits.

Then Ferry is asked for 18 specific values from the first six sessions. A summary chain either keeps growing far past
its budget, or one compaction drops a block of sessions — and Ferry then says, truthfully, that it was never recorded.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Model calls go
through MemoryRouter and are billed to your MemoryLake balance (about 150 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-rsm"  # memorylake-usecases / rolling-summary-memory


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


# --------------------------------------------------------------------------- settings

MODEL = "gpt-4o-mini"
TEMPERATURE = "0"     # the most repeatable setting: what changes between answers is what memory holds
MAX_TOKENS = 120      # an answer is one short sentence
SUMMARY_TOKENS = 900  # room for a summary well past its budget: the cap is not what cuts it
B_RUNS = 2            # answers per question through MemoryLake (each summary chain answers once)
DROP = 3              # a compaction that loses this many values the summary held a moment ago
MARK = {"ok": "✓", "old": "↺", "unknown": "?", "made": "✎"}
GRADE_NAME = {"ok": "right", "old": "the value before it changed", "unknown": "says it is not recorded",
              "made": "a value memory does not hold"}


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


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "sessions.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


ACTOR_KEYS = ("dana", "ferry")


def leftovers(cli: CLI) -> bool:
    return bool(demo_boundaries(cli) or find_project(cli, "notes") or any(find_actor(cli, k) for k in ACTOR_KEYS))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def at(s: dict) -> datetime:
    return datetime.fromisoformat(s["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def session_text(s: dict, d: dict) -> str:
    names = {"dana": d["person"]["display"].split()[0], "ferry": d["assistant"]["display"]}
    return "\n".join(f"{names[t['who']]}: {t['text']}" for t in s["turns"])


# --------------------------------------------------------------------------- what a text holds, and grading

def holds(q: dict, text: str) -> str:
    """Does a summary, a fact list or a reply carry this value? ok · old (only the value before it changed) · ''."""
    if re.search(q["re"], text, re.I):
        return "ok"
    if q.get("old_re") and re.search(q["old_re"], text, re.I):
        return "old"
    return ""


def holds_then(q: dict, text: str, upto: list[str]) -> str:
    """As holds(), on the sessions so far: before a value changes, its earlier value is the right one."""
    if q.get("revised_in") and q["revised_in"] not in upto:
        return "ok" if re.search(q["old_re"], text, re.I) else ""
    return holds(q, text)


UNKNOWN_RE = (r"not recorded|no record|isn't recorded|is not in (what|my)|don't have|do not have|not (been )?(mentioned|specified|"
              r"available|provided)|no information|not sure|unknown")


def grade(q: dict, reply: str) -> str:
    h = holds(q, reply)
    if h:
        return h
    return "unknown" if re.search(UNKNOWN_RE, reply, re.I) else "made"


def check_grader(d: dict) -> None:
    """The grading rules on known replies, before any call is made: a wrong rule would grade every run wrong."""
    qs = {q["id"]: q for q in d["questions"]}
    cases = [("q01", "The staging Postgres for the billing service listens on port 6543.", "ok"),
             ("q01", "It typically listens on port 5432.", "made"),
             ("q03", "The billing service connection pool cap was raised to 64 connections per pod.", "ok"),
             ("q03", "The billing service connection pool is capped at 40 connections per pod.", "old"),
             ("q08", "Canaries are held for 20 minutes before promotion.", "old"),
             ("q08", "Canaries are now held for 30 minutes (previously 20 minutes).", "ok"),
             ("q10", "Billing webhooks retry 7 times before giving up.", "ok"),
             ("q10", "Billing webhooks retry three times.", "made"),
             ("q10", "It is not recorded.", "unknown"),
             ("q13", "The TLS certificate renews on November 14.", "ok"),
             ("q14", "They are revoked on 31 October.", "ok"),
             ("q17", "The queue cutover is on Thursday 22 October at 07:30 UTC.", "ok"),
             ("q17", "The queue cutover date has not been specified in the notes.", "unknown"),
             ("q06", "The rate-card Redis cluster has 6 shards.", "ok"),
             ("q06", "Shard 4 is the hot one.", "made")]
    for qid, reply, want in cases:
        got = grade(qs[qid], reply)
        if got != want:
            die(f"grader self-check failed on {qid}: “{reply}” graded {got}, expected {want}")


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {}
    emit("text", f"\n    {d['person']['display']} — {d['person']['role']}. {d['assistant']['display']} — {d['assistant']['role']}.")
    world["project"] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("notes"), scoped=True)["id"]
    world["dana"] = make_actor(cli, "dana", d["person"]["display"], f"{d['person']['role']}. Demo data.", echo=True)
    world["ferry"] = make_actor(cli, "ferry", d["assistant"]["display"], f"{d['assistant']['role']}. Demo data.", echo=False,
                                actor_type="ASSISTANT")
    world["boundary"] = cli.run("boundary", "create", "--name", "Ferry's memory", "--custom-id", cid("ferry"),
                                "--projects", world["project"], "--human-actor", world["dana"], scoped=True)["id"]
    note("B: one project, Dana and Ferry as actors, and a boundary (project + Dana) — what Ferry reads through MemoryRouter")
    emit("text", f"\n    A: no setup — the app keeps the summary itself. {d['chains']} chains, each told:")
    emit("text", wrap(f"“{d['summarizer']}”", "      ", 108))
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    p = find_project(cli, "notes")
    if not p:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    world = {"project": p["id"]}
    for k in ACTOR_KEYS:
        a = find_actor(cli, k)
        if a:
            world[k] = a["id"]
    world["boundary"] = next((b["id"] for b in demo_boundaries(cli)), None)
    return world


# --------------------------------------------------------------------------- step 3: twelve sessions, two memories

def show_sessions(d: dict) -> None:
    asked = {q["session"] for q in d["questions"]}
    for s in d["sessions"]:
        tag = "  ← asked about later" if s["id"] in asked else ""
        emit("text", f"\n    {at(s):%a %d %b}  {s['topic']}{tag}")
        for t in s["turns"]:
            if t["who"] == "dana":
                emit("text", wrap(f"Dana  {t['text']}", "      ", 112))
    emit("sessions", "", sessions=d["sessions"], questions=d["questions"])


def summary_chain(cli: CLI, d: dict, n: int, t0: float) -> dict:
    """One app instance: after every session, fold it into the running summary."""
    summary, steps = "", []
    for i, s in enumerate(d["sessions"]):
        prompt = (f"Current summary:\n{summary or '(empty)'}\n\nNew session ({s['at'][:10]}):\n{session_text(s, d)}\n\n"
                  "New summary:")
        r = router(cli, prompt, d["summarizer"], max_tokens=SUMMARY_TOKENS, echo=n == 0 and i == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance): the summary buffer needs a model")
        before = steps[-1]["holds"] if steps else {}
        summary = r["reply"]
        words = len(summary.split())
        upto = [x["id"] for x in d["sessions"][:i + 1]]
        kept = {q["id"]: holds_then(q, summary, upto) for q in d["questions"]}
        lost = [k for k, v in before.items() if v == "ok" and kept[k] != "ok"]
        step = dict(session=s["id"], words=words, kept=sum(1 for v in kept.values() if v == "ok"), holds=kept,
                    lost=lost, dropped=len(lost) >= DROP, summary=summary)
        steps.append(step)
        emit("progress", f"    A chain {n + 1}: {s['id']:>3} folded in · {words} words · "
                         f"{step['kept']}/{len(d['questions'])} values · {int(time.time() - t0)} s",
                         chain=n, session=s["id"], words=words, kept=step["kept"])
    return dict(chain=n + 1, steps=steps, summary=summary, dropped=[x["session"] for x in steps if x["dropped"]])


def ingest_memorylake(cli: CLI, world: dict, d: dict, t0: float) -> list[str]:
    """Each session a DIRECT conversation, extracted before the next starts: later sessions revise earlier facts."""
    who = {"dana": world["dana"], "ferry": world["ferry"]}
    late = []
    for n, s in enumerate(d["sessions"]):
        conv = cli.run("conv", "create", "--project", world["project"], "--actors", f"{who['dana']},{who['ferry']}",
                       "--kind", "DIRECT", "--name", f"{s['topic']} ({s['at'][:10]})", "--custom-id", cid("conv", s["id"]),
                       scoped=True, echo=n == 0)["id"]
        for i, t in enumerate(s["turns"]):
            append(cli, conv, who[t["who"]], f"m{i:02d}", utc(at(s) + timedelta(minutes=i)), t["text"], echo=n == 0 and i == 0)
        if not cooked(cli, conv, echo=n == 0, limit=420):
            late.append(s["id"])
        emit("progress", f"    B MemoryLake: {s['id']:>3} extracted · {int(time.time() - t0)} s", chain=-1, session=s["id"])
    return late


def ingest(cli: CLI, world: dict) -> list[dict]:
    d = load_data()
    show_sessions(d)
    emit("text", f"\n    The twelve sessions go into MemoryLake one by one, while {d['chains']} summary chains fold them in.")
    t0 = time.time()
    with ThreadPoolExecutor(d["chains"] + 1) as pool:
        fb = pool.submit(ingest_memorylake, cli, world, d, t0)
        fa = [pool.submit(summary_chain, cli, d, n, t0) for n in range(d["chains"])]
        chains = [f.result() for f in fa]
        late = fb.result()
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — counted as they stand")
    time.sleep(5)   # cook_finished can come a moment before the facts are listed
    OUT.mkdir(exist_ok=True)
    (OUT / "summaries.json").write_text(json.dumps(chains, ensure_ascii=False, indent=1), encoding="utf-8")
    note(f"both memories built in {int(time.time() - t0)} s; the summaries are in out/summaries.json")
    return chains


def load_chains() -> list[dict]:
    f = OUT / "summaries.json"
    if not f.exists():
        die("no summaries yet; run `python3 demo.py` first")
    return json.loads(f.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- step 4: what each memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Every fact Ferry can read (the project and Dana's actor), and which of the 18 values they carry."""
    d = load_data()
    seen: dict = {}
    for f in project_facts(cli, world["project"], echo=echo) + (actor_facts(cli, world["dana"]) if world.get("dana") else []):
        seen[f["id"]] = f.get("fact") or ""
    facts = list(seen.values())
    blob = "\n".join(facts)
    hold = {q["id"]: holds(q, blob) for q in d["questions"]}
    return dict(facts=len(facts), all=facts, holds=hold, kept=sum(1 for v in hold.values() if v == "ok"))


def show_trace(chains: list[dict]) -> None:
    d = load_data()
    nq = len(d["questions"])
    emit("text", f"\n    A — the summary after each session (words · values kept of {nq}); budget {d['budget_words']} words")
    emit("text", f"      {'':<34}" + "".join(f"{'chain ' + str(c['chain']):<18}" for c in chains))
    for i, s in enumerate(d["sessions"]):
        cells = []
        for c in chains:
            x = c["steps"][i]
            cells.append(f"{x['words']:>4} w · {x['kept']:>2}/{nq}" + (f" ↓{len(x['lost'])}" if x["dropped"] else ""))
        emit("text", f"      {at(s):%d %b} {s['topic'][:26]:<27}" + "".join(f"{v:<18}" for v in cells))
    emit("text", f"      ↓N one compaction dropped N values the summary held a moment before (counted from {DROP})")
    what = {q["id"]: q["what"] for q in d["questions"]}
    for c in chains:
        if c["dropped"]:
            for i, x in enumerate(c["steps"]):
                if not x["dropped"]:
                    continue
                before = c["steps"][i - 1]
                emit("text", wrap(f"chain {c['chain']}, folding in {x['session']}: {before['words']} → {x['words']} words, "
                                  f"{before['kept']} → {x['kept']} values — lost: {', '.join(what[k] for k in x['lost'])}", "    ", 112))
        else:
            emit("text", f"    chain {c['chain']}: never dropped {DROP}+ values at once — {c['steps'][-1]['words']} words at the end, "
                         f"{c['steps'][-1]['words'] / d['budget_words']:.1f}× its budget")
    emit("trace", "", chains=[{k: v for k, v in c.items() if k != "summary"} for c in chains])


def show_holdings(chains: list[dict], cen: dict) -> None:
    d = load_data()
    sym = {"ok": "✓", "old": "↺", "": "✗"}
    emit("text", f"\n    What Ferry has to go on, per value — the final summaries (A) and the facts (B, {cen['facts']} facts):")
    emit("text", f"      {'':<24}" + "".join(f"{'A' + str(c['chain']):<5}" for c in chains) + "B")
    last = None
    for q in d["questions"]:
        s = next(x for x in d["sessions"] if x["id"] == q["session"])
        head = f"{at(s):%d %b}" if s["id"] != last else ""
        last = s["id"]
        row = "".join(f"{sym[c['steps'][-1]['holds'][q['id']]]:<5}" for c in chains) + sym[cen["holds"][q["id"]]]
        emit("text", f"      {head:<7}{q['what'][:16]:<17}{row}" + ("   changed later" if q.get("old_re") else ""))
    emit("text", "      ✓ holds the value · ↺ only the value before it changed · ✗ not there")
    ex = [f for f in cen["all"] if holds(next(q for q in d["questions"] if q["id"] == "q17"), f) == "ok"]
    if ex:
        emit("text", wrap(f"B, the fact for the cutover: “{ex[0]}”", "    ", 112))
    emit("census", "", **cen)


# --------------------------------------------------------------------------- step 5: ask Ferry

def ask_all(cli: CLI, world: dict, chains: list[dict]) -> list[dict]:
    d = load_data()
    qs = d["questions"]
    note(f"each question once per summary chain (the summary in the system prompt), {B_RUNS}× through MemoryLake "
         "(`router chat --boundary`), and once with neither (the ∅ baseline); 6 at a time")
    for n, q in enumerate(qs):
        baseline(cli, q["ask"], d["system"], echo=n == 0)
    jobs = [(q, "A", c["chain"]) for q in qs for c in chains] + [(q, "B", i + 1) for q in qs for i in range(B_RUNS)]

    def one(job):
        q, arm, k = job
        if arm == "A":
            system = d["system"] + "\n\nWhat you remember from earlier sessions:\n" + chains[k - 1]["summary"]
            r = router(cli, q["ask"], system, echo=job is jobs[0])
            empty = False
        else:
            r = router(cli, q["ask"], d["system"], world["boundary"], echo=job[1] == "B" and job[2] == 1 and q is qs[0])
            empty = bool(r) and r["prompt_tokens"] <= BASE[(q["ask"], d["system"])]
        if not r:
            return dict(item=q["id"], arm=arm, run=k, grade=None, reply="")
        return dict(item=q["id"], arm=arm, run=k, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    grade=grade(q, r["reply"]))
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    emit("text", f"\n      {'':<20}{'A1 A2 A3':<10}{'B  B':<7}")
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        a = " ".join(mark(r) for r in mine if r["arm"] == "A")
        b = " ".join(mark(r) for r in mine if r["arm"] == "B")
        bad = next((r for r in mine if r["arm"] == "A" and r.get("grade") not in (None, "ok")), None)
        good = next((r for r in mine if r["arm"] == "B" and r.get("reply")), None)
        say = f"A{bad['run']}: {bad['reply']}" if bad else (f"B: {good['reply']}" if good else "")
        say = " ".join(say.split())
        emit("text", f"      {q['what'][:19]:<20}{a:<10}{b:<7}" + (say[:78] + "…" if len(say) > 79 else say))
        emit("ask_row", "", item=q["id"], rows=mine)
    return rows


def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK.get(r.get("grade") or "", "·")


EXAMPLE = "q17"


def show_example(rows: list[dict], chains: list[dict]) -> None:
    """The cutover question — the one that matters tomorrow — answer by answer."""
    d = load_data()
    q = next(x for x in d["questions"] if x["id"] == EXAMPLE)
    emit("text", f"\n    “{q['ask']}”   (Dana said: Thursday 22 October at 07:30 UTC — tomorrow)")
    for r in rows:
        if r["item"] == EXAMPLE and r.get("reply") and (r["arm"] == "A" or r["run"] == 1):
            emit("text", wrap(r["reply"], f"    {r['arm']}{r['run'] if r['arm'] == 'A' else ' '} {mark(r)} | ", 108))
    emit("example", "", ask=q["ask"], rows=[r for r in rows if r["item"] == EXAMPLE])


def tally(rows: list[dict]) -> dict:
    g = [r for r in rows if r.get("grade")]
    c = lambda x: sum(1 for r in g if r["grade"] == x and not r.get("empty"))  # noqa: E731
    pt = [r["prompt_tokens"] for r in g if r.get("prompt_tokens")]
    return dict(n=len(g), ok=c("ok"), old=c("old"), unknown=c("unknown"), made=c("made"),
                empty=sum(1 for r in g if r.get("empty")), prompt_tokens=round(sum(pt) / len(pt)) if pt else 0)


def score(chains: list[dict], cen: dict, rows: list[dict]) -> dict:
    d = load_data()
    nq = len(d["questions"])
    out: dict = {"arms": {}, "chains": []}
    emit("text", f"    {'':<34}{'kept':>6}{'right':>9}{'old value':>11}{'not recorded':>14}{'other':>7}{'∅':>4}{'prompt tokens':>15}")
    for c in chains:
        t = tally([r for r in rows if r["arm"] == "A" and r["run"] == c["chain"]])
        last = c["steps"][-1]
        label = f"A{c['chain']} summary · {last['words']} words"
        emit("text", f"    {label:<34}{last['kept']:>3}/{nq:<2}{t['ok']:>6}/{t['n']:<2}{t['old']:>9}{t['unknown']:>14}"
                     f"{t['made']:>7}{'':>4}{t['prompt_tokens']:>15,}")
        out["chains"].append(dict(chain=c["chain"], words=last["words"], kept=last["kept"], dropped=c["dropped"], **t))
    for arm in ("A", "B"):
        out["arms"][arm] = tally([r for r in rows if r["arm"] == arm])
    t = out["arms"]["B"]
    emit("text", f"    {'B MemoryLake · ' + str(cen['facts']) + ' facts':<34}{cen['kept']:>3}/{nq:<2}{t['ok']:>6}/{t['n']:<2}{t['old']:>9}"
                 f"{t['unknown']:>14}{t['made']:>7}{t['empty']:>4}{t['prompt_tokens']:>15,}")
    dropped = sum(1 for c in chains if c["dropped"])
    emit("text", f"\n    this run: {dropped} of {len(chains)} summary chains dropped {DROP}+ values in a single compaction; "
                 f"every chain ended above its {d['budget_words']}-word budget" if all(c["steps"][-1]["words"] > d["budget_words"] for c in chains)
                 else f"\n    this run: {dropped} of {len(chains)} summary chains dropped {DROP}+ values in a single compaction")
    emit("text", "    ✓ right · ↺ the value before it changed · ? says it is not recorded · ✎ a value memory does not hold · "
                 "∅ MemoryRouter attached nothing")
    emit("text", "    prompt tokens: per question, on average — A carries its whole summary every time, B what MemoryRouter attaches")
    out["dropped"] = dropped
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    check_grader(d)
    BASE.clear()
    banner(2, TOTAL, "Two memories for Ferry — a summary buffer and MemoryLake")
    world = setup(cli)

    banner(3, TOTAL, "Twelve sessions, three weeks")
    chains = ingest(cli, world)

    banner(4, TOTAL, "What each memory holds")
    show_trace(chains)
    cen = census(cli, world)
    show_holdings(chains, cen)

    banner(5, TOTAL, "Ask Ferry — 18 values from the first six sessions")
    rows = ask_all(cli, world, chains)
    show_example(rows, chains)

    banner(6, TOTAL, "The score")
    sc = score(chains, cen, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(chains=chains, census=cen, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every summary, fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def summaries() -> list[dict]:
    """The three summary chains: size and values after each session, and each final summary."""
    chains = load_chains()
    show_trace(chains)
    for c in chains:
        emit("text", f"\n    chain {c['chain']}, final summary ({c['steps'][-1]['words']} words):")
        emit("text", wrap(c["summary"], "      | ", 110))
    return chains


def memory(cli: CLI) -> dict:
    world = load_world(cli)
    cen = census(cli, world)
    show_holdings(load_chains(), cen)
    emit("text", f"\n    The {cen['facts']} facts Ferry can read:")
    for f in cen["all"]:
        emit("text", wrap(f, "      · ", 110))
    emit("memory_view", "", census=cen)
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each summary chain and once through MemoryLake; graded when it is one of the 18."""
    d = load_data()
    world = load_world(cli)
    chains = load_chains()
    q = next((x for x in d["questions"] if x["ask"].lower() == question.strip().lower()), None)
    rows = []
    for c in chains:
        r = router(cli, question, d["system"] + "\n\nWhat you remember from earlier sessions:\n" + c["summary"], echo=c is chains[0])
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        rows.append(dict(arm="A", run=c["chain"], reply=r["reply"], grade=grade(q, r["reply"]) if q else None))
    base = baseline(cli, question, d["system"])
    r = router(cli, question, d["system"], world["boundary"], echo=True)
    if not r:
        die("MemoryRouter is not available on this account (no balance)")
    rows.append(dict(arm="B", run=1, reply=r["reply"], grade=grade(q, r["reply"]) if q else None,
                     empty=r["prompt_tokens"] <= base))
    for x in rows:
        head = "∅ nothing attached" if x.get("empty") else (f"{MARK[x['grade']]} {GRADE_NAME[x['grade']]}" if x["grade"] else "")
        name = f"A{x['run']} summary chain {x['run']}" if x["arm"] == "A" else "B MemoryLake"
        emit("text", f"\n  {name}: {head}")
        emit("text", wrap(x["reply"], "    | ", 100))
    emit("answered", "", question=question, item=q and q["id"], rows=rows)
    return rows


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = [c for c in paged(cli, ["conv", "list", "--page-size", "100"])
             if (c.get("custom_id") or "").startswith(PREFIX + "-")]
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=False)
    nfacts = 0
    found = [(k, find_actor(cli, k, echo=not quiet and k == ACTOR_KEYS[0])) for k in ACTOR_KEYS]
    for _, a in found:
        if a:
            nfacts += delete_actor(cli, a["id"])
    p = find_project(cli, "notes")
    if p:
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
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet)
    if not quiet:
        n_act = sum(1 for _, a in found if a)
        note(f"deleted {len(bs)} boundary, {len(convs)} conversations, {nfacts} facts, {n_act} actors (facts first: a "
             f"deleted actor's facts could never be removed) and {1 if p else 0} project"
             if (bs or convs or n_act or p) else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "summaries"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "summaries", "memory", "ask", "cleanup"],
                    help="run = full demo (default); summaries = the three summary chains, step by step; memory = what "
                         "each memory holds, and MemoryLake's facts; ask \"<question>\" = one question through A1–A3 and B; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: the question")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "summaries":
        banner(1, 1, "The summary buffer — three chains, session by session")
        summaries()
        return
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "memory":
        banner(2, 2, "What each memory holds")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die('usage: python3 demo.py ask "<question>"   (e.g. "When is the queue cutover?")')
        banner(2, 2, "One question to Ferry, through each summary and through MemoryLake")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py summaries`, `python3 demo.py memory`, or "
          "`python3 demo.py ask \"When is the queue cutover?\"`; `python3 demo.py cleanup` removes the demo data.")


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

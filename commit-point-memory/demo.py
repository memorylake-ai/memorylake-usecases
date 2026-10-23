#!/usr/bin/env python3
"""commit-point-memory — when can a long-running agent drop its own copy of a step and rely on MemoryLake instead?
Restart it at the moment your rule says "safe", and see what it remembers.

Tern is Halvorsen Analytics' autonomous backfill agent (a fictional company). Three past sessions with Mei Okafor, a
data engineer, settled its run settings: target schema, batch size, alert channel, staging bucket, write window.
Tonight, during a 16-step backfill, Mei changes three of them. Tern writes every step into MemoryLake as it goes
(`conv msg append`); extraction turns the steps into facts a few seconds later. A crash wipes whatever Tern kept in
the process, so the question is when a step counts as committed. Three rules:

  B  committed once `conv msg append` returns (the server has the message)
  C  committed once `conv consumed-messages --message` lists it (extraction has read it)
  D  committed once `conv fact-actions --message` shows what its batch wrote into memory

For every change the demo restarts Tern at the exact moment each rule declares the step safe — the riskiest moment
a crash can come for that rule — and asks the restarted agent, which has only its memory (MemoryRouter
`--boundary`), what the setting is now. Then it prints the commit log: every extraction batch, when it was read,
what it wrote, and why `cook-status` is no use as a commit point while steps keep arriving.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Model calls go
through MemoryRouter and are billed to your MemoryLake balance (12 calls on gpt-4o-mini per run, well under 0.01 USD).
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

PREFIX = "mlu-cmt"  # memorylake-usecases / commit-point-memory




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

def append(cli: CLI, conv: str, actor: str, custom_id: str, ts: str, text: str, echo: bool) -> str | None:
    """Append one message; returns its id (None when a resend found it already there)."""
    for attempt in range(5):
        try:
            r = cli.run("conv", "msg", "append", conv, "--actor", actor, "--custom-id", custom_id, "--timestamp", ts,
                        "--text", text, scoped=True, echo=echo and attempt == 0)
            return r.get("id") if isinstance(r, dict) else None
        except CLIError as e:
            # a resend after a dropped connection: the custom_id is already there, so the message is too
            if e.has("custom_id_conflict", "already exists") and attempt:
                return None
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
TEMPERATURE = "0"   # the most repeatable setting: what changes between the rules is only when the restart happens
MAX_TOKENS = 100    # an answer is one or two short sentences
TOTAL = 6
RULES = ("B", "C", "D")
RULE_LABEL = {"B": "B  committed once the append returns",
              "C": "C  committed once extraction has read it",
              "D": "D  committed once memory has written it"}
RULE_SHORT = {"B": "B  appended", "C": "C  read", "D": "D  written"}
RULE_SIGNAL = {"B": "`conv msg append` returned", "C": "`conv consumed-messages --message` lists it",
               "D": "`conv fact-actions --message` shows what its batch wrote"}
MARK = {"ok": "✓", "stale": "⟲", "unknown": "∅", "made": "✗"}
GRADE_NAME = {"ok": "right", "stale": "the value from before tonight's change", "unknown": "says it is not on file",
              "made": "a wrong or made-up value"}
POLL_SECONDS = 0.5   # how often a change's watcher asks "read yet? written yet?"
WATCH_LIMIT = 240    # after the change: how long to wait for extraction to read it and write it
STATUS_SECONDS = 3   # how often `cook-status` is sampled during the run
ACTOR_KEYS = ("mei", "tern")


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "run.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def at(s: dict) -> datetime:
    return datetime.fromisoformat(s["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def settings(d: dict) -> dict:
    return {c["id"]: c for c in d["conventions"]}


def changed(d: dict) -> list[dict]:
    """Tonight's three changes, in the order Mei makes them."""
    return sorted((c for c in d["conventions"] if c.get("new")), key=lambda c: c["changed_at"])


# --------------------------------------------------------------------------- grading

UNKNOWN_RE = (r"not on file|don't have|do not have|don't know|do not know|no record|not recorded|isn't recorded|"
              r"not (been )?(mentioned|specified|available|provided|in (my|the) (notes|records|memory))|no information|"
              r"not sure|unknown|check with")


def grade(c: dict, reply: str) -> str:
    """ok: tonight's value (the agreed one for a setting that did not change); stale: the value from before tonight's
    change and not the new one; unknown: says it does not have it; else made."""
    want = c.get("new_re") or c["old_re"]
    if all(re.search(r, reply, re.I) for r in want):
        return "ok"
    if c.get("new") and any(re.search(r, reply, re.I) for r in c["old_re"]):
        return "stale"
    return "unknown" if re.search(UNKNOWN_RE, reply, re.I) else "made"


def value_in(c: dict, text: str) -> str | None:
    """Which value a fact states for a changed setting: "new" if it names tonight's value, else "old" if it names the
    earlier one, else None. A revised fact carries the earlier value along in "previously …", so that tail is not
    read, and the order of the values is not used either ("not analytics_v3 but analytics_v4")."""
    head = re.split(r"\bpreviously\b", text, maxsplit=1, flags=re.I)[0]
    if any(re.search(r, head, re.I) for r in c["new_re"]):
        return "new"
    if any(re.search(r, head, re.I) for r in c["old_re"]):
        return "old"
    return None


def check_grader(d: dict) -> None:
    """The grading rules on known replies, before any call is made: a wrong rule would grade every run wrong."""
    s = settings(d)
    cases = [("schema", "Tonight's backfill writes to the analytics_v4 schema.", "ok"),
             ("schema", "It writes to analytics_v3.", "stale"),
             ("schema", "analytics_v4 (not analytics_v3, which is frozen).", "ok"),
             ("schema", "The target schema is not on file.", "unknown"),
             ("batch", "Each batch is 2,000 rows.", "ok"),
             ("batch", "Batches of 2000 rows.", "ok"),
             ("batch", "Each batch is 5,000 rows.", "stale"),
             ("batch", "Each batch is 12,000 rows.", "made"),
             ("alerts", "Failure alerts go to #backfill-oncall.", "ok"),
             ("alerts", "They go to #pipeline-alerts.", "stale"),
             ("bucket", "I stage files in s3://hv-stage-a.", "ok"),
             ("bucket", "I stage files in s3://hv-stage-b.", "made"),
             ("window", "Only between 01:00 and 05:00 UTC.", "ok"),
             ("window", "Between 1:00 and 5:00 UTC.", "ok"),
             ("window", "Any time after 22:00.", "made")]
    for k, reply, want in cases:
        got = grade(s[k], reply)
        if got != want:
            die(f"grader self-check failed on {k}: “{reply}” graded {got}, expected {want}")
    facts = [("schema", "Tonight's backfill writes to analytics_v4, not analytics_v3. (as of 2026-10-19)", "new"),
             ("schema", "Backfills write to the analytics_v3 schema. (as of 2026-09-07)", "old"),
             ("schema", "Backfills write to analytics_v4 (as of 2026-10-19, previously analytics_v3 as of 2026-09-07).", "new"),
             ("batch", "Batches are 5,000 rows (as of 2026-10-19, previously 2,000 rows).", "old"),
             ("alerts", "#pipeline-alerts is muted; alerts go to #backfill-oncall.", "new")]
    for k, text, want in facts:
        got = value_in(s[k], text)
        if got != want:
            die(f"fact-reading self-check failed on {k}: “{text}” read as {got}, expected {want}")


# --------------------------------------------------------------------------- MemoryRouter

LOCK = threading.Lock()
BODY = OUT / "body.json"


def prices(cli: CLI, echo: bool = True) -> dict:
    """USD per million tokens for MODEL, from the account's own price list: input, cache_read, output."""
    res = cli.run("router", "models", echo=echo) or {}
    # CLI v20261023+ prints the model list itself; earlier versions wrapped it in {"data": {"items": [...]}}
    items = res if isinstance(res, list) else (res.get("data") or {}).get("items") or res.get("items") or []
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


def system_prompt(d: dict) -> str:
    """The playbook plus what a restarted agent still knows from its job queue: the date and which run it resumes.
    Without them a fact like "the September backfill uses 2,000 rows (as of 2026-10-19)" does not read as tonight's."""
    return f"{d['playbook']}\n\nToday is {d['today']}. You are resuming tonight's run: {d['run_name']}."


def write_body(d: dict) -> None:
    """The restarted agent's request body: the system prompt and nothing else — whatever it kept in the process is gone.
    Written once, atomically, before any call: the calls run at the same time and all read it."""
    OUT.mkdir(exist_ok=True)
    tmp = BODY.with_suffix(".tmp")
    tmp.write_text(json.dumps({"model": MODEL, "messages": [{"role": "system", "content": system_prompt(d)}]},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, BODY)


def router(cli: CLI, prompt: str, boundary_id: str, echo: bool = False) -> dict | None:
    """One `router chat --body` call: the body holds the playbook, MemoryRouter attaches what the boundary's memory
    holds, the question is the PROMPT."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["chat", "--body", str(BODY.relative_to(HERE)) if echo else str(BODY), "--model", MODEL,
            "--max-tokens", str(MAX_TOKENS), "--temperature", TEMPERATURE, "--boundary", boundary_id, prompt]
    t0 = time.time()
    try:
        res = cli.run("router", *args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("restart questions below are skipped; the commit moments and the log still run. Top up in the console "
                 "to see the answers.")
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


def load_world(cli: CLI, quiet: bool = False) -> dict | None:
    """The demo's memory if all of it is there: both actors, the project, its boundary, the past sessions; plus how
    many runs have written a night into it."""
    d = load_data()
    world = {}
    for k in ACTOR_KEYS:
        a = find_actor(cli, k, echo=not quiet and k == ACTOR_KEYS[0])
        if not a:
            return None
        world[k] = a["id"]
    p = find_project(cli, "proj")
    b = next((x for x in demo_boundaries(cli) if x.get("custom_id") == cid("bnd")), None)
    if not p or not b:
        return None
    convs = demo_convs(cli)
    past = [c for c in convs if (c.get("custom_id") or "").startswith(cid("s"))]
    nights = sorted((c for c in convs if (c.get("custom_id") or "").startswith(cid("night"))),
                    key=lambda c: c.get("created_at") or "")
    world.update(project=p["id"], boundary=b["id"], past=len(past), complete=len(past) == len(d["sessions"]),
                 nights=[c["id"] for c in nights])
    return world


def setup(cli: CLI, reset: bool) -> tuple[dict, bool]:
    """(world, whether the past sessions need extracting). The memory is reused only when it holds the past sessions
    and no night yet: after a run it holds tonight's new values, and a restart would find them whatever the rule."""
    d = load_data()
    emit("text", f"\n    {d['person']['display']} — {d['person']['role']}.\n    {d['assistant']['display']} — {d['assistant']['role']}.")
    world = None if reset else load_world(cli)
    if world and world["complete"] and not world["nights"]:
        note("reusing the memory an earlier run built (the past sessions, nothing from a night)")
        emit("world", "", **world)
        return world, False
    if world or demo_convs(cli) or find_project(cli, "proj") or any(find_actor(cli, k) for k in ACTOR_KEYS):
        why = ("--reset" if reset else "an earlier run already wrote a night into it — tonight's values would be there"
               if world and world["nights"] else "an earlier run stopped half-way")
        note(f"rebuilding the memory: {why}")
        cleanup(cli, quiet=True)
    world = {"mei": make_actor(cli, "mei", d["person"]["display"], f"{d['person']['role']}. Demo data.", echo=True),
             "tern": make_actor(cli, "tern", d["assistant"]["display"], f"{d['assistant']['role']}. Demo data.",
                                echo=False, actor_type="ASSISTANT")}
    world["project"] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("proj"),
                               scoped=True)["id"]
    world["boundary"] = cli.run("boundary", "create", "--name", f"{d['assistant']['display']}'s memory",
                                "--custom-id", cid("bnd"), "--projects", world["project"], scoped=True)["id"]
    world.update(past=0, complete=False, nights=[])
    note("one project for Tern's memory and a boundary over it: what MemoryRouter attaches when the restarted agent asks")
    emit("world", "", **world)
    return world, True


# --------------------------------------------------------------------------- step 3: the past sessions

def ingest(cli: CLI, world: dict) -> None:
    """Each past session becomes a DIRECT conversation in the project; extraction turns it into facts."""
    d = load_data()
    who = {"mei": world["mei"], "tern": world["tern"]}
    t0 = time.time()

    def one(n: int, s: dict) -> str | None:
        conv = cli.run("conv", "create", "--project", world["project"], "--actors", f"{who['mei']},{who['tern']}",
                       "--kind", "DIRECT", "--name", f"{s['topic']} ({s['at'][:10]})", "--custom-id", cid(s["id"]),
                       scoped=True, echo=n == 0)["id"]
        for i, t in enumerate(s["turns"]):
            append(cli, conv, who[t["who"]], f"m{i:02d}", utc(at(s) + timedelta(minutes=i)), t["text"],
                   echo=n == 0 and i == 0)
        done = cooked(cli, conv, echo=n == 0, limit=420)
        emit("progress", f"    {s['id']}  {at(s):%d %b}  {s['topic']:<16} {len(s['turns'])} messages · extracted · "
                         f"{int(time.time() - t0)} s", session=s["id"])
        return None if done else s["id"]

    late = [one(0, d["sessions"][0])]   # the first one alone, so its commands print in order
    with ThreadPoolExecutor(3) as pool:
        late += list(pool.map(lambda a: one(*a), list(enumerate(d["sessions"]))[1:]))
    late = [x for x in late if x]
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — counted as they stand")
    time.sleep(5)   # cook_finished can come a moment before the facts are listed


def show_memory(cli: CLI, world: dict, d: dict, echo: bool = True) -> dict:
    """Is each agreed setting in Tern's memory? Checked before tonight's run starts."""
    texts = [f.get("fact") or "" for f in project_facts(cli, world["project"], echo=echo)]
    emit("text", f"\n    Facts extracted from the past sessions: {len(texts)}")
    held = {}
    for c in d["conventions"]:
        hits = [t for t in texts if all(re.search(r, t, re.I) for r in c["old_re"])]
        held[c["id"]] = bool(hits)
        emit("text", f"    {'✓' if hits else '·'}  {c['label'] + ': ' + c['old']:<36}"
                     + (f"“{hits[0][:70]}{'…' if len(hits[0]) > 70 else ''}”" if hits else "not found as written in English"))
    n = sum(held.values())
    note(f"{n}/{len(held)} agreed settings are in Tern's memory"
         + ("" if n == len(held) else " (extraction sometimes writes a fact in another language, and this check reads "
                                      "English only)"))
    emit("memory", "", held=held, facts=len(texts))
    return held


# --------------------------------------------------------------------------- step 4: tonight's run

def night_id() -> str:
    return "N-" + os.urandom(3).hex().upper()


def items_of(res) -> list:
    if isinstance(res, list):
        return res
    return (res or {}).get("items") or [] if isinstance(res, dict) else []


class StatusSampler:
    """Samples `cook-status` every few seconds through the run: is the conversation's single "finished" flag ever a
    usable commit point while steps keep arriving?"""

    def __init__(self, cli: CLI, conv: str, t0: float):
        self.cli, self.conv, self.t0 = cli, conv, t0
        self.samples: list[tuple[float, bool]] = []
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                st = self.cli.run("conv", "cook-status", self.conv, scoped=True, echo=False) or {}
                self.samples.append((round(time.time() - self.t0, 1), bool(st.get("cook_finished"))))
            except CLIError:
                pass
            self.stop.wait(STATUS_SECONDS)


class ChangeWatch:
    """Follows one change from the moment Mei says it: restarts Tern when B, C and D each call the step committed,
    and records what the restarted agent answers."""

    def __init__(self, cli: CLI, world: dict, d: dict, conv: str, c: dict, step: int, msg_id: str | None,
                 t_said: float, t_appended: float, t0: float, echo: bool, pool: ThreadPoolExecutor):
        self.cli, self.world, self.d, self.conv, self.c = cli, world, d, conv, c
        self.step, self.msg, self.t0, self.echo, self.pool = step, msg_id, t0, echo, pool
        self.control = settings(d)[d["controls"][c["id"]]]
        self.res = dict(id=c["id"], step=step, said=t_said, rules={}, batch=None, actions=[], control=None)
        self.asks = []
        self.res["rules"]["B"] = dict(safe=t_appended)
        self.asks.append(pool.submit(self._restart, "B", True))
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def now(self) -> float:
        return round(time.time() - self.t0, 1)

    def _restart(self, rule: str, with_control: bool = False) -> None:
        """Crash and restart at this rule's commit moment: the agent asks its memory for the setting."""
        first = self.echo
        r = router(self.cli, self.c["question"], self.world["boundary"], echo=first)
        slot = self.res["rules"][rule]
        if r:
            r["grade"] = grade(self.c, r["reply"])
            slot.update(r)
        with LOCK:
            show_restart(self.c, rule, slot, self.res["said"])
            emit("restart", "", change=self.c["id"], rule=rule, **slot)
        if with_control:
            rc = router(self.cli, self.control["question"], self.world["boundary"], echo=False)
            if rc:
                rc["grade"] = grade(self.control, rc["reply"])
                self.res["control"] = dict(id=self.control["id"], **rc)
                with LOCK:
                    emit("text", f"             control, same restart: {self.control['label']} (unchanged) "
                                 f"{MARK[rc['grade']]}  “{rc['reply'][:80]}”")
                    emit("control", "", change=self.c["id"], **self.res["control"])

    def _loop(self) -> None:
        if not self.msg:
            note(f"no message id for the {self.c['label']} change (the append was resent); not watched")
            return
        t_end = time.time() + WATCH_LIMIT
        first_c = first_d = self.echo
        while time.time() < t_end and "D" not in self.res["rules"]:
            try:
                if "C" not in self.res["rules"]:
                    batch = items_of(self.cli.run("conv", "consumed-messages", self.conv, "--message", self.msg,
                                                  scoped=True, echo=first_c))
                    first_c = False
                    if batch:
                        self.res["rules"]["C"] = dict(safe=self.now())
                        self.res["batch"] = sorted(m.get("sequence_no") or 0 for m in batch)
                        self.asks.append(self.pool.submit(self._restart, "C"))
                acts = items_of(self.cli.run("conv", "fact-actions", self.conv, "--project", self.world["project"],
                                             "--message", self.msg, scoped=True, echo=first_d))
                first_d = False
                if acts:
                    self.res["rules"]["D"] = dict(safe=self.now())
                    if "C" not in self.res["rules"]:   # read and written between two polls
                        self.res["rules"]["C"] = dict(safe=self.res["rules"]["D"]["safe"])
                        self.asks.append(self.pool.submit(self._restart, "C"))
                    self.res["actions"] = [dict(event=a.get("event"), fact=a.get("new_fact") or a.get("old_fact") or "")
                                           for a in acts]
                    self.asks.append(self.pool.submit(self._restart, "D"))
                    break
            except CLIError as e:
                if not e.has(*CLI.TRANSIENT):
                    raise
            time.sleep(POLL_SECONDS)
        if "D" not in self.res["rules"]:
            with LOCK:
                note(f"{self.c['label']}: extraction had not written it {WATCH_LIMIT} s after it was said")

    def join(self) -> dict:
        self.t.join()
        for f in list(self.asks):
            f.result()
        return self.res


def show_restart(c: dict, rule: str, slot: dict, said: float) -> None:
    after = slot["safe"] - said
    head = f"      ↻ {RULE_SHORT[rule]:<12} +{slot['safe']:>5.1f}s ({after:+5.1f}s after it was said)  restart"
    if "reply" not in slot:
        emit("text", head + " — MemoryRouter unavailable, not asked")
        return
    emit("text", f"{head} → {MARK[slot['grade']]} {c['label']}")
    emit("text", wrap(slot["reply"], "          | ", 104))


def run_night(cli: CLI, world: dict, d: dict) -> dict:
    nid = night_id()
    who = {"mei": world["mei"], "tern": world["tern"]}
    conv = cli.run("conv", "create", "--project", world["project"], "--actors", f"{who['mei']},{who['tern']}",
                   "--kind", "DIRECT", "--name", f"Backfill {nid}", "--custom-id", cid("night", nid.lower()),
                   scoped=True)["id"]
    write_body(d)
    t0 = time.time()
    sampler = StatusSampler(cli, conv, t0)
    pool = ThreadPoolExecutor(8)
    watches, steps = [], []
    emit("text", f"\n    Backfill {nid} — {d['today']}. {len(d['live'])} steps; each step is written as it happens.")
    first_change = True
    for n, step in enumerate(d["live"], 1):
        t_said = round(time.time() - t0, 1)
        mid = append(cli, conv, who["mei"], f"m{2 * n - 2:03d}", utc(datetime.now(timezone.utc)), step["user"],
                     echo=n == 1)
        append(cli, conv, who["tern"], f"m{2 * n - 1:03d}", utc(datetime.now(timezone.utc)), step["agent"], echo=False)
        t_app = round(time.time() - t0, 1)
        steps.append(dict(n=n, t=t_said, said=step["user"], agent=step["agent"], change=step.get("change")))
        with LOCK:
            emit("text", f"  step {n:>2}  +{t_said:>5.1f}s  {step['user'][:92]}{'…' if len(step['user']) > 92 else ''}")
            emit("step_line", "", n=n, t=t_said, said=step["user"], agent=step["agent"], change=step.get("change"))
        if step.get("change"):
            c = settings(d)[step["change"]]
            with LOCK:
                emit("text", f"           ▶ {c['label']}: {c['old']} → {c['new']}. Restarting Tern at each rule's commit moment:")
            watches.append(ChangeWatch(cli, world, d, conv, c, n, mid, t_said, t_app, t0, echo=first_change, pool=pool))
            first_change = False
        time.sleep(d["step_pause"])
    t_last = round(time.time() - t0, 1)
    with LOCK:
        note(f"last step appended at +{t_last:.0f} s; waiting for extraction to write the changes "
             f"(up to {WATCH_LIMIT} s after each)")
    changes = [w.join() for w in watches]
    pool.shutdown(wait=True)
    # a little longer, so the samples show when the single flag finally turns
    t_lim = time.time() + 60
    while time.time() < t_lim and not any(f for _, f in sampler.samples if _ > t_last):
        time.sleep(1)
    sampler.stop.set()
    sampler.t.join()
    return dict(nid=nid, conv=conv, t0=t0, t_last=t_last, steps=steps, changes=changes, status=sampler.samples)


# --------------------------------------------------------------------------- step 5: the commit log

def commit_log(cli: CLI, world: dict, conv: str, echo: bool = True) -> dict:
    """Every extraction batch of the night: which messages it read, what it wrote. A batch with nothing to write
    never gets a receipt, so D's rule also releases a batch once a later batch has its receipt."""
    msgs = sorted(items_of(cli.run("conv", "msg", "list", conv, "--page-size", "100", echo=echo)),
                  key=lambda m: m.get("sequence_no") or 0)
    batches, seen = [], set()
    first = echo
    for m in msgs:
        seq = m.get("sequence_no")
        if seq in seen:
            continue
        read = items_of(cli.run("conv", "consumed-messages", conv, "--message", m["id"], scoped=True, echo=first))
        acts = items_of(cli.run("conv", "fact-actions", conv, "--project", world["project"], "--message", m["id"],
                                scoped=True, echo=first))
        first = False
        if not read:
            batches.append(dict(seqs=[seq], read=False, actions=[]))
            seen.add(seq)
            continue
        seqs = sorted(x.get("sequence_no") or 0 for x in read)
        seen.update(seqs)
        batches.append(dict(seqs=seqs, read=True, actions=[dict(event=a.get("event"),
                                                                fact=a.get("new_fact") or a.get("old_fact") or "")
                                                           for a in acts]))
    last_written = max((i for i, b in enumerate(batches) if b["actions"]), default=-1)
    for i, b in enumerate(batches):
        if b["actions"]:
            b["verdict"] = "written"
        elif b["read"] and i < last_written:
            b["verdict"] = "nothing to write"   # a later batch has its receipt: this one is past
        else:
            b["verdict"] = "open"
    return dict(messages=len(msgs), batches=batches)


def step_of(seq: int) -> str:
    """Message sequence number → "step n, who" (two messages per step: Mei's, then Tern's)."""
    n, who = (seq + 1) // 2, "Mei" if seq % 2 else "Tern"
    return f"{n}{'m' if who == 'Mei' else 't'}"


def show_log(log: dict, sess: dict | None, d: dict) -> None:
    emit("text", f"\n    {log['messages']} messages, read in {len(log['batches'])} extraction batches "
                 "(m = Mei's message of a step, t = Tern's):")
    emit("text", f"    {'':4}{'batch':<7}{'steps':<26}{'wrote':<13}{'D says'}")
    for i, b in enumerate(log["batches"], 1):
        span = f"{step_of(b['seqs'][0])}–{step_of(b['seqs'][-1])}" if len(b["seqs"]) > 1 else step_of(b["seqs"][0])
        wrote = f"{len(b['actions'])} change(s)" if b["actions"] else ("—" if b["read"] else "not read")
        verdict = {"written": "committed: receipt", "nothing to write": "committed: a later batch has its receipt",
                   "open": "not yet: no receipt, nothing later either"}[b["verdict"]]
        emit("text", f"    {'':4}{i:<7}{span + ' (' + str(len(b['seqs'])) + ' msgs)':<26}{wrote:<13}{verdict}")
        for a in b["actions"][:3]:
            emit("text", wrap(a["fact"], f"{'':11}{a['event'] or '':<7}“", 104) + "”")
    empty = [b for b in log["batches"] if b["verdict"] == "nothing to write"]
    if empty:
        n = sum(len(b["seqs"]) for b in empty)
        note(f"{len(empty)} batch(es), {n} messages of progress chatter, wrote nothing and so never got a receipt: a rule "
             "that waits for every message's own receipt would keep them in the process forever")
    if sess:
        s = sess["status"]
        during = [f for t, f in s if t <= sess["t_last"]]
        after = next((t for t, f in s if f and t > sess["t_last"]), None)
        emit("text", f"\n    `cook-status` sampled every {STATUS_SECONDS} s: while steps were arriving (up to +{sess['t_last']:.0f} s) "
                     f"cook_finished was true in {sum(during)} of {len(during)} samples"
                     + (f"; true for good from +{after:.0f} s, after the last step" if after else
                        "; still false when sampling stopped"))
        note("one flag for the whole conversation: it is true only in the moments extraction has caught up with "
             "everything, so as a commit point it holds every step until the run goes quiet")
    emit("log", "", **log, status=(sess or {}).get("status"), t_last=(sess or {}).get("t_last"))


# --------------------------------------------------------------------------- step 6: tally

def show_tally(sess: dict, d: dict) -> dict:
    s = settings(d)
    rows = sess["changes"]
    out = {}
    emit("text", f"\n    {'':4}{'restart at the commit moment of':<44}"
                 + "".join(f"{s[r['id']]['label']:>18}" for r in rows) + f"{'right':>9}")
    for rule in RULES:
        cells, ok, delays = [], 0, []
        for r in rows:
            slot = r["rules"].get(rule)
            if not slot:
                cells.append("—")
                continue
            delays.append(slot["safe"] - r["said"])
            g = slot.get("grade")
            ok += g == "ok"
            cells.append(f"{MARK.get(g, '·')} +{slot['safe'] - r['said']:.1f}s")
        out[rule] = dict(ok=ok, n=len(rows), grades=[(r["rules"].get(rule) or {}).get("grade") for r in rows],
                         delays=[round(x, 1) for x in delays])
        emit("text", f"    {'':4}{RULE_LABEL[rule]:<44}" + "".join(f"{x:>18}" for x in cells) + f"{ok:>6}/{len(rows)}")
    ctl = [r["control"] for r in rows if r.get("control")]
    emit("text", "")
    note("+N s = how long after Mei said it each rule called the step committed; ⟲ = the restarted agent answered "
         "with the value from before tonight's change")
    if ctl:
        note(f"control: an unchanged setting asked at B's restarts, {sum(c['grade'] == 'ok' for c in ctl)}/{len(ctl)} "
             "right — the memory itself works; what fails is the commit point")
    emit("tally", "", tallies=out, controls=ctl)
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
    banner(2, TOTAL, "Set up — Mei, Tern, and Tern's memory")
    world, fill = setup(cli, reset)
    banner(3, TOTAL, f"The past — {len(d['sessions'])} sessions in September settle the run settings")
    for s in d["sessions"]:
        emit("text", f"      {s['id']}  {at(s):%a %d %b}  {s['topic']}")
    if fill:
        emit("text", "\n    Each session goes into Tern's project as a conversation; extraction turns it into facts.")
        ingest(cli, world)
    held = show_memory(cli, world, d)
    banner(4, TOTAL, f"Tonight — {len(d['live'])} steps, three changes, a restart at every commit moment")
    p = prices(cli, echo=False)
    emit("text", wrap("Tern appends every step to the conversation as it happens. When Mei changes a setting, the demo "
                      "restarts Tern at the moment each rule calls that step committed: B when the append returns, C "
                      "when `consumed-messages` lists it, D when `fact-actions --message` shows what its batch wrote. The "
                      "restarted agent has only its memory (MemoryRouter `--boundary`).", "    ", 108))
    sess = run_night(cli, world, d)
    for r in sess["changes"]:
        for slot in r["rules"].values():
            if "prompt_tokens" in slot:
                slot["usd"] = cost(p, slot["prompt_tokens"], slot["cached"], slot["completion_tokens"])
    banner(5, TOTAL, "The commit log — every extraction batch, what it read and wrote")
    log = commit_log(cli, world, sess["conv"])
    show_log(log, sess, d)
    banner(6, TOTAL, "Tally")
    tallies = show_tally(sess, d)
    report = dict(at=utc(datetime.now(timezone.utc)), model=MODEL, prices=p, world=world, held=held, nid=sess["nid"],
                  steps=sess["steps"], changes=sess["changes"], status=sess["status"], t_last=sess["t_last"], log=log,
                  tallies=tallies, tokens=cli.tokens)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    note("every restart, reply, timing and batch is in out/report.json")
    return report


# --------------------------------------------------------------------------- read-only commands

def ask(cli: CLI, question: str) -> dict | None:
    """A restarted agent's question: no notes of its own, only what its memory holds now."""
    d = load_data()
    world = load_world(cli, quiet=True)
    if not world:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    write_body(d)
    r = router(cli, question, world["boundary"], echo=True)
    if r:
        emit("text", f"\n  Tern, restarted   {r['prompt_tokens']:,} tok")
        emit("text", wrap(r["reply"], "    | ", 104))
    emit("answered", "", question=question, row=r)
    return r


def log_cmd(cli: CLI) -> None:
    world = load_world(cli, quiet=True)
    if not world or not world["nights"]:
        die("no night has run yet; run `python3 demo.py` first")
    show_log(commit_log(cli, world, world["nights"][-1]), None, load_data())


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    bounds = demo_boundaries(cli, echo=not quiet)
    for b in bounds:
        cli.run("boundary", "delete", b["id"], echo=False)
    convs = demo_convs(cli)
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=False)
    nfacts = 0
    p = find_project(cli, "proj")
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
        cli.run("proj", "delete", p["id"], scoped=True, echo=False)
    found = [(k, find_actor(cli, k, echo=not quiet and k == ACTOR_KEYS[0])) for k in ACTOR_KEYS]
    for _, a in found:
        if a:
            nfacts += delete_actor(cli, a["id"])
    if not quiet:
        n_act = sum(1 for _, a in found if a)
        note(f"deleted {len(bounds)} boundary, {len(convs)} conversations, {nfacts} facts, {n_act} actors (facts first: "
             "a deleted actor's facts could never be removed) and the project"
             if (bounds or convs or n_act or nfacts or p) else "nothing to delete")
    archive_report()   # kept, not deleted: every run's numbers survive a cleanup
    if BODY.exists():
        BODY.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "log", "ask", "cleanup"],
                    help="run = full demo (default); log = the last night's commit log; ask \"<question>\" = a "
                         "restarted Tern's question to its memory; cleanup = delete everything")
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
    if args.command == "log":
        banner(2, 2, "The last night's commit log")
        log_cmd(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die('usage: python3 demo.py ask "<question>"   (e.g. "Which schema does the backfill write to?")')
        banner(2, 2, "A restarted Tern asks its memory")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py log` or `python3 demo.py ask \"Which schema does the backfill write to?\"`; "
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

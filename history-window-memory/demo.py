#!/usr/bin/env python3
"""history-window-memory — the usual cure for a bloated prompt, "keep only the last few turns", forgets what was agreed
months ago and stops the provider's prompt cache from ever covering the conversation.

Ines Varga (Head of Purchasing at Tolland Hollow Brewing, a fictional craft brewery) works with Cask, the brewery's
purchasing assistant, in one long thread: ten sessions from July to September settle a hop contract, malt delivery
days, a late-delivery credit, a glass quote, the CO2 account, label sign-off and the quarter's approval limit. Today,
19 October, she plans the holiday orders in a 16-turn session, and seven of those turns depend on something agreed
weeks ago. The same session runs three ways:

  A  the whole thread in every request (append-only: every turn re-sends three months of history)
  B  the last 6 turns only — the common fix for A's bill
  C  the last 6 turns, plus MemoryRouter attaching what MemoryLake extracted from the ten past sessions

Every request starts with the same 1,100-token playbook, so the provider can serve that part from its prompt cache.
A's history only ever grows at the end, so each turn re-reads the previous turn's prompt from cache. B's window drops
its oldest turn every time, so from the moment it slides nothing after the playbook is ever cached — and the old
agreements are simply not there. Usage and prices come from MemoryRouter: `usage.prompt_tokens_details.cached_tokens`
and the account's own `router models` price list.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Model calls go
through MemoryRouter and are billed to your MemoryLake balance (48 calls on gpt-4o-mini per run, about 0.02 USD).
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

PREFIX = "mlu-hwm"  # memorylake-usecases / history-window-memory




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
MAX_TOKENS = 120    # an answer is one to three short sentences
TOTAL = 6
INGEST_WORKERS = 3  # past sessions extracted at the same time
ARMS = ("A", "B", "C")
ARM_LABEL = {"A": "A  whole thread", "B": "B  last 6 turns", "C": "C  last 6 turns + memory"}
MARK = {"ok": "✓", "unknown": "∅", "made": "✗"}
GRADE_NAME = {"ok": "right", "unknown": "says it is not on file", "made": "a wrong or made-up value"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "thread.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


ACTOR_KEYS = ("ines", "cask")


def leftovers(cli: CLI) -> bool:
    return bool(demo_boundaries(cli) or find_project(cli, "thread") or any(find_actor(cli, k) for k in ACTOR_KEYS))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def at(s: dict) -> datetime:
    return datetime.fromisoformat(s["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def past_messages(d: dict) -> list[dict]:
    """The ten past sessions as chat messages, the way the app keeps the thread: each session opens with its date."""
    msgs = []
    for s in d["sessions"]:
        for i, t in enumerate(s["turns"]):
            text = t["text"]
            if i == 0:
                text = f"[{at(s):%a %-d %b %Y}] {text}"
            msgs.append({"role": "user" if t["who"] == "ines" else "assistant", "content": text})
    return msgs


def questions(d: dict) -> list[dict]:
    return [t for t in d["live"] if t.get("id")]


# --------------------------------------------------------------------------- grading

UNKNOWN_RE = (r"not on file|don't have|do not have|don't know|do not know|no record|not recorded|isn't recorded|not (been )?(mentioned|specified|"
              r"available|provided|in (my|the) (notes|records))|no information|not sure|unknown|check with")


def grade(q: dict, reply: str) -> str:
    """ok when every value of the agreement is in the reply; unknown when it says it does not have it; else made."""
    if all(re.search(r, reply, re.I) for r in q["re"]):
        return "ok"
    return "unknown" if re.search(UNKNOWN_RE, reply, re.I) else "made"


def check_grader(d: dict) -> None:
    """The grading rules on known replies, before any call is made: a wrong rule would grade every run wrong."""
    qs = {q["id"]: q for q in questions(d)}
    cases = [("q1", "That drop is contract lot VH-2207, Citra at 8.40 USD per pound.", "ok"),
             ("q1", "It is lot VH-2207.", "made"),
             ("q1", "I don't have the lot number or price on file.", "unknown"),
             ("q2", "Yes: more than 3 days late earns a 6% credit on that drop's invoice.", "ok"),
             ("q2", "You may be owed a 5% credit.", "made"),
             ("q3", "Arnholt delivers on Tuesdays to dock door 4.", "ok"),
             ("q3", "It should arrive on Tuesday; I don't have the dock door on file.", "unknown"),
             ("q4", "Corvane's minimum run is 22 pallets with a 5-week lead time.", "ok"),
             ("q5", "Emergency delivery is a flat 180 USD; quote account FG-5531.", "ok"),
             ("q5", "Quote your Ferrous Gas account; the fee is usually around 150 USD.", "made"),
             ("q6", "Dara Osei signs off label proofs, with a 48-hour turnaround.", "ok"),
             ("q7", "It is above your 5,000 USD limit, so Tomas Reyne (CFO) has to approve it.", "ok"),
             ("q7", "You will need CFO approval.", "made"),
             ("q7", "I don't have the current approval limit on file; check with finance.", "unknown"),
             ("q7", "I do not know the current approval limit set by finance for this quarter.", "unknown")]
    for qid, reply, want in cases:
        got = grade(qs[qid], reply)
        if got != want:
            die(f"grader self-check failed on {qid}: “{reply}” graded {got}, expected {want}")


# --------------------------------------------------------------------------- MemoryRouter

LOCK = threading.Lock()


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
        out["cache_read"] = out["input"]  # no cache discount listed: cached tokens cost the same
        note(f"{MODEL} lists no cache_read price; cached tokens are priced as input")
    return out


def cost(p: dict, prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * p["input"] + cached * p["cache_read"] + completion * p["output"]) / 1e6


def router(cli: CLI, arm: str, messages: list[dict], prompt: str, boundary_id: str | None = None,
           echo: bool = False) -> dict | None:
    """One `router chat --body` call: the system prompt and the history go in the body, the new turn is the PROMPT."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    OUT.mkdir(exist_ok=True)
    path = OUT / f"body-{arm}.json"   # one file per arm: the three arms run at the same time
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
            note("model calls below are skipped; the memory steps still ran. Top up in the console to see the bill.")
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
                cached=cached or 0, cached_reported=cached is not None, completion_tokens=ct,
                seconds=round(time.time() - t0, 1))


# --------------------------------------------------------------------------- step 2: set up

def demo_convs(cli: CLI) -> list[dict]:
    return [c for c in paged(cli, ["conv", "list", "--page-size", "100"])
            if (c.get("custom_id") or "").startswith(PREFIX + "-")]


def load_world(cli: CLI, quiet: bool = False) -> dict | None:
    """The memory an earlier run built, if all of it is there: project, both actors, boundary, ten conversations."""
    p = find_project(cli, "thread", echo=not quiet)
    if not p:
        return None
    world = {"project": p["id"]}
    for k in ACTOR_KEYS:
        a = find_actor(cli, k)
        if not a:
            return None
        world[k] = a["id"]
    world["boundary"] = next((b["id"] for b in demo_boundaries(cli)), None)
    world["convs"] = len(demo_convs(cli))
    return world if world["boundary"] and world["convs"] == len(load_data()["sessions"]) else None


def setup(cli: CLI, reset: bool) -> tuple[dict, bool]:
    """(world, reused): an earlier run's memory is reused unless --reset; anything half-built is removed first."""
    d = load_data()
    if not reset:
        world = load_world(cli)
        if world:
            n = len(memory_facts(cli, world))
            if n:
                note(f"found the memory an earlier run built ({world['convs']} conversations, {n} facts); reusing it "
                     "(`--reset` rebuilds it)")
                emit("world", "", reused=True, **world)
                return world, True
    if leftovers(cli):
        note("removing what an earlier run left behind" if not reset else "--reset: removing the earlier demo memory")
        cleanup(cli, quiet=True)
    world: dict = {}
    emit("text", f"\n    {d['person']['display']} — {d['person']['role']}.\n    {d['assistant']['display']} — {d['assistant']['role']}.")
    world["project"] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("thread"), scoped=True)["id"]
    world["ines"] = make_actor(cli, "ines", d["person"]["display"], f"{d['person']['role']}. Demo data.", echo=True)
    world["cask"] = make_actor(cli, "cask", d["assistant"]["display"], f"{d['assistant']['role']}. Demo data.", echo=False,
                               actor_type="ASSISTANT")
    world["boundary"] = cli.run("boundary", "create", "--name", "Cask's memory", "--custom-id", cid("cask"),
                                "--projects", world["project"], "--human-actor", world["ines"], scoped=True)["id"]
    note("one project, Ines and Cask as actors, and a boundary (project + Ines): what arm C reads through MemoryRouter")
    emit("world", "", reused=False, **world)
    return world, False


# --------------------------------------------------------------------------- step 3: three months of history

def memory_facts(cli: CLI, world: dict, echo: bool = False) -> list[dict]:
    seen, out = set(), []
    for f in project_facts(cli, world["project"], echo) + actor_facts(cli, world["ines"]):
        if f["id"] not in seen:
            seen.add(f["id"])
            out.append(f)
    return out


def ingest(cli: CLI, world: dict) -> None:
    """Each past session a DIRECT conversation between Ines and Cask."""
    d = load_data()
    who = {"ines": world["ines"], "cask": world["cask"]}
    t0 = time.time()

    def one(n: int, s: dict) -> str | None:
        conv = cli.run("conv", "create", "--project", world["project"], "--actors", f"{who['ines']},{who['cask']}",
                       "--kind", "DIRECT", "--name", f"{s['topic']} ({s['at'][:10]})", "--custom-id", cid("conv", s["id"]),
                       scoped=True, echo=n == 0)["id"]
        for i, t in enumerate(s["turns"]):
            append(cli, conv, who[t["who"]], f"m{i:02d}", utc(at(s) + timedelta(minutes=i)), t["text"], echo=n == 0 and i == 0)
        done = cooked(cli, conv, echo=n == 0, limit=420)
        emit("progress", f"    {s['id']}  {at(s):%d %b}  {s['topic']:<26} {len(s['turns'])} messages · extracted · "
                         f"{int(time.time() - t0)} s", session=s["id"])
        return None if done else s["id"]

    # The sessions do not revise one another, so they are extracted side by side; whether every value made it into
    # the facts is checked right after, value by value.
    one(0, d["sessions"][0])   # the first one alone, so its commands print in order
    with ThreadPoolExecutor(INGEST_WORKERS) as pool:
        late = [x for x in pool.map(lambda a: one(*a), list(enumerate(d["sessions"]))[1:]) if x]
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — counted as they stand")
    time.sleep(5)   # cook_finished can come a moment before the facts are listed


def show_history(d: dict) -> None:
    msgs = past_messages(d)
    words = sum(len(m["content"].split()) for m in msgs)
    emit("text", f"\n    The thread so far: {len(d['sessions'])} sessions, {len(msgs)} messages, {words:,} words.")
    for s in d["sessions"]:
        emit("text", f"      {s['id']}  {at(s):%a %d %b}  {s['topic']}")


def covering(q: dict, texts: list[str]) -> list[str] | None:
    """The fewest facts that between them carry every value of q (most values first), or None if one is missing."""
    left, out = list(q["re"]), []
    while left:
        best = max(texts, key=lambda t: sum(1 for r in left if re.search(r, t, re.I)), default="")
        got = [r for r in left if re.search(r, best, re.I)]
        if not got:
            return None
        out.append(best)
        left = [r for r in left if r not in got]
    return out


def show_memory(cli: CLI, world: dict, d: dict, echo: bool = True) -> dict:
    """Is each value today's session needs among the facts MemoryLake extracted? Checked here, before any answer."""
    facts = memory_facts(cli, world, echo=echo)
    texts = [f.get("fact") or f.get("text") or "" for f in facts]
    held = {}
    emit("text", f"\n    MemoryLake extracted {len(facts)} facts from the ten sessions. The values today's session asks for:")
    for q in questions(d):
        cover = covering(q, texts)
        ok = cover is not None
        held[q["id"]] = ok
        emit("text", f"      {'✓' if ok else '·'} {q['id']}  {q['values']:<40} {'in the facts' if ok else 'NOT in the facts'}")
        for t in cover or []:
            emit("text", wrap(t, "           “", 100) + "”")
    n = sum(held.values())
    note(f"{n}/{len(held)} of the values are in the extracted facts"
         + ("" if n == len(held) else " — a question whose value is missing can only be answered from the thread itself"))
    emit("memory", "", facts=len(facts), held=held)
    return held


# --------------------------------------------------------------------------- step 5: today's session, three ways

def session_id() -> str:
    return "TH-" + os.urandom(3).hex().upper()


def system_prompt(d: dict, sid: str) -> str:
    # The session line goes after the playbook: the playbook stays a prefix every request shares, and a new session
    # never starts with a prompt an earlier run already sent.
    return f"{d['playbook']}\n\nToday is {d['today']}. Session {sid} with {d['person']['display']}."


def window(thread: list[dict], turns: int) -> list[dict]:
    return thread[-2 * turns:]


def request(arm: str, system: str, thread: list[dict], d: dict) -> list[dict]:
    body = thread if arm == "A" else window(thread, d["window_turns"])
    return [{"role": "system", "content": system}] + body


def fmt_tok(r: dict | None) -> str:
    return "—" if not r else f"{r['prompt_tokens']:>6,} tok · {r['cached']:>6,} cached"


def run_session(cli: CLI, world: dict, d: dict, p: dict) -> list[dict]:
    sid = session_id()
    system = system_prompt(d, sid)
    past = past_messages(d)
    threads = {a: list(past) for a in ARMS}
    rows = []
    emit("text", f"\n    Session {sid} — {d['today']}. 16 turns; each turn goes to A, B and C at the same time.")
    emit("text", f"    {'':12}{ARM_LABEL['A']:<28}{ARM_LABEL['B']:<28}{ARM_LABEL['C']}")
    for n, turn in enumerate(d["live"], 1):
        q = turn if turn.get("id") else None
        first = n == 1

        def call(arm: str):
            return router(cli, arm, request(arm, system, threads[arm], d), turn["text"],
                          world["boundary"] if arm == "C" else None, echo=first)

        if first:   # the first turn one arm at a time, so the three commands print in order
            emit("text", "")
            res = {a: call(a) for a in ARMS}
        else:
            with ThreadPoolExecutor(3) as pool:
                futs = {a: pool.submit(call, a) for a in ARMS}
                res = {a: f.result() for a, f in futs.items()}
        row = dict(turn=n, said=turn["text"], q=q and q["id"], arms={})
        for a in ARMS:
            r = res[a]
            if r:
                r["usd"] = cost(p, r["prompt_tokens"], r["cached"], r["completion_tokens"])
                if q:
                    r["grade"] = grade(q, r["reply"])
            row["arms"][a] = r
            threads[a] += [{"role": "user", "content": turn["text"]},
                           {"role": "assistant", "content": r["reply"] if r else "(no reply)"}]
        rows.append(row)
        show_turn(row, q)
        emit("turn", "", **row)
    emit("session", "", session=sid)
    return rows


def show_turn(row: dict, q: dict | None) -> None:
    a = row["arms"]
    if not q:
        emit("text", f"\n  turn {row['turn']:>2}  Ines: {row['said'][:84]}{'…' if len(row['said']) > 84 else ''}")
        emit("text", f"           A {fmt_tok(a['A'])}   B {fmt_tok(a['B'])}   C {fmt_tok(a['C'])}")
        return
    emit("text", f"\n  turn {row['turn']:>2}  ▶ {q['id']}  Ines: {row['said']}")
    emit("text", f"           agreed in {q['from']}: {q['values']}")
    for arm in ARMS:
        r = a[arm]
        if not r:
            continue
        emit("text", f"    {MARK[r['grade']]} {ARM_LABEL[arm]:<27} {fmt_tok(r)}")
        emit("text", wrap(r["reply"], "        | ", 104))


# --------------------------------------------------------------------------- step 6: the bill

def tally(rows: list[dict], arm: str) -> dict:
    rs = [r["arms"][arm] for r in rows if r["arms"].get(arm)]
    t = dict(calls=len(rs), prompt=sum(r["prompt_tokens"] for r in rs), cached=sum(r["cached"] for r in rs),
             completion=sum(r["completion_tokens"] for r in rs), usd=sum(r["usd"] for r in rs),
             last_prompt=rs[-1]["prompt_tokens"] if rs else 0, first_prompt=rs[0]["prompt_tokens"] if rs else 0,
             grades={g: sum(1 for r in rs if r.get("grade") == g) for g in MARK})
    t["uncached"] = t["prompt"] - t["cached"]
    return t


def flat_cache(rows: list[dict], arm: str, d: dict) -> tuple[int, int, int]:
    """(value, turns, of): once the window has slid, how often the cached count sat at one and the same value."""
    after = [r["arms"][arm]["cached"] for r in rows if r["arms"].get(arm) and r["turn"] > d["window_turns"]]
    if not after:
        return 0, 0, 0
    v = max(set(after), key=after.count)
    return v, after.count(v), len(after)


def show_bill(rows: list[dict], p: dict, d: dict) -> dict:
    tallies = {a: tally(rows, a) for a in ARMS}
    nq = len(questions(d))
    emit("text", f"\n    {MODEL}: input {p['input']} · cache read {p['cache_read']} · output {p['output']} USD per million tokens")
    emit("text", f"\n    {'':28}{'prompt tok':>11}{'cached':>9}{'uncached':>10}{'per session':>13}{'per 1M sessions':>17}"
                 f"{'agreements':>12}")
    for a in ARMS:
        t = tallies[a]
        share = t["cached"] / t["prompt"] if t["prompt"] else 0
        emit("text", f"    {ARM_LABEL[a]:<28}{t['prompt']:>11,}{share:>8.0%} {t['uncached']:>10,}"
                     f"{t['usd']:>13.4f}{t['usd'] * 1e6:>17,.0f}{t['grades']['ok']:>10}/{nq}")
    emit("text", "")
    for a in ARMS:
        t = tallies[a]
        g = t["grades"]
        emit("text", f"    {ARM_LABEL[a]:<28}✓ {g['ok']} right · ∅ {g['unknown']} not on file · ✗ {g['made']} wrong   "
                     f"prompt {t['first_prompt']:,} → {t['last_prompt']:,} tokens")
    vb, nb, ofb = flat_cache(rows, "B", d)
    vc, nc, ofc = flat_cache(rows, "C", d)
    a_last = [r["arms"]["A"] for r in rows if r["arms"].get("A")][1:]
    a_share = (sum(r["cached"] for r in a_last) / sum(r["prompt_tokens"] for r in a_last)) if a_last else 0
    emit("text", "")
    note(f"A: from turn 2 on, {a_share:.0%} of every prompt came from the cache — the thread only grows at the end, so "
         "each request starts with the one before it")
    note(f"B: after turn {d['window_turns']} the cached count sat at {vb:,} on {nb}/{ofb} turns — the playbook; the window "
         "drops its oldest turn every time, so nothing after the playbook is ever re-read from cache")
    note(f"C: the same window, the same floor ({vc:,} on {nc}/{ofc} turns); MemoryRouter's facts come after the playbook, "
         "so they do not move it")
    ta, tb, tc = tallies["A"], tallies["B"], tallies["C"]
    if tc["usd"] and tb["usd"]:
        note(f"per session A costs {ta['usd'] / tc['usd']:.1f}× C and C costs {tc['usd'] / tb['usd']:.2f}× B — "
             f"B is the cheapest and answers {tb['grades']['ok']}/{nq} of the questions about earlier agreements")
    out = {a: {k: v for k, v in t.items()} for a, t in tallies.items()}
    emit("bill", "", tallies=out, prices=p, flat={"B": [vb, nb, ofb], "C": [vc, nc, ofc]}, a_share=a_share)
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
    banner(2, TOTAL, "Set up — a project, Ines and Cask, and the boundary arm C reads")
    world, reused = setup(cli, reset)
    banner(3, TOTAL, "Three months of history — ten sessions, July to September")
    show_history(d)
    if not reused:
        emit("text", "\n    Each session goes into MemoryLake as a conversation; extraction turns it into facts.")
        ingest(cli, world)
    held = show_memory(cli, world, d)
    banner(4, TOTAL, "Prices — the account's own MemoryRouter price list")
    p = prices(cli)
    emit("text", f"    {MODEL}: input {p['input']} · cache read {p['cache_read']} · output {p['output']} USD per million tokens")
    emit("prices", "", **p)
    banner(5, TOTAL, f"Today's session — 16 turns, three ways to build each prompt")
    emit("text", wrap("A sends the whole thread every turn. B keeps the last 6 turns (the usual fix). C keeps the same "
                      "6 turns and lets MemoryRouter attach what MemoryLake holds (`--boundary`). All three start with "
                      "the same playbook.", "    ", 108))
    rows = run_session(cli, world, d, p)
    banner(6, TOTAL, "The bill — tokens, cache, dollars, and the earlier agreements")
    tallies = show_bill(rows, p, d)
    report = dict(at=utc(datetime.now(timezone.utc)), model=MODEL, prices=p, world=world, reused=reused, held=held,
                  rows=rows, tallies=tallies)
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    note("every request, reply and usage line is in out/report.json")
    return report


# --------------------------------------------------------------------------- read-only commands

def ask(cli: CLI, question: str) -> list[dict]:
    """One new turn, at the end of the thread, through A, B and C."""
    d = load_data()
    world = load_world(cli, quiet=True)
    if not world:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    p = prices(cli, echo=False)
    system = system_prompt(d, session_id())
    past = past_messages(d)
    out = []
    for arm in ARMS:
        r = router(cli, arm, request(arm, system, past, d), question, world["boundary"] if arm == "C" else None,
                   echo=arm == "C")
        if not r:
            continue
        r["usd"] = cost(p, r["prompt_tokens"], r["cached"], r["completion_tokens"])
        emit("text", f"\n  {ARM_LABEL[arm]}   {fmt_tok(r)}   {r['usd']:.6f} USD")
        emit("text", wrap(r["reply"], "    | ", 104))
        out.append(dict(arm=arm, **r))
    emit("answered", "", question=question, rows=out)
    return out


def facts(cli: CLI) -> None:
    world = load_world(cli, quiet=True)
    if not world:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    d = load_data()
    fs = memory_facts(cli, world, echo=True)
    emit("text", f"\n    {len(fs)} facts MemoryLake extracted from the ten past sessions:")
    for f in sorted(fs, key=lambda f: f.get("created_at") or ""):
        emit("text", wrap(f.get("fact") or f.get("text") or "", "      - ", 104))
    show_memory(cli, world, d, echo=False)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = demo_convs(cli)
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=False)
    nfacts = 0
    found = [(k, find_actor(cli, k, echo=not quiet and k == ACTOR_KEYS[0])) for k in ACTOR_KEYS]
    for _, a in found:
        if a:
            nfacts += delete_actor(cli, a["id"])
    p = find_project(cli, "thread")
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
    archive_report()   # kept, not deleted: every run's numbers survive a cleanup
    for f in OUT.glob("body-*.json"):
        f.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "facts", "ask", "cleanup"],
                    help="run = full demo (default); facts = what MemoryLake extracted from the ten past sessions; "
                         "ask \"<question>\" = one new turn through A, B and C; cleanup = delete everything")
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
        banner(2, 2, "What MemoryLake holds from the ten past sessions")
        facts(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die('usage: python3 demo.py ask "<question>"   (e.g. "Which day does Arnholt deliver?")')
        banner(2, 2, "One new turn, through A, B and C")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py facts` or `python3 demo.py ask \"Which day does Arnholt deliver, and to which "
          "door?\"`; `python3 demo.py cleanup` removes the demo data.")


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

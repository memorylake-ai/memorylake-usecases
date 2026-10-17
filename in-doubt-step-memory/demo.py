#!/usr/bin/env python3
"""in-doubt-step-memory — the run journal says "no entry means not done"; the worker died between the payment and the entry.

Quillfen (a fictional 9-person startup) pays its contractors through Payrook, a plan-and-execute agent. Run P-1016 has
seven steps: freeze the payout sheet, pull 23 timesheets, compute the payouts, send the bank transfers, email remittance
advice, post to the books, unfreeze the sheet. The app keeps a run journal in memory with `fact add` — one entry the
moment each step completes, and one coverage fact: "a step with no journal entry has not completed". Payrook sends the
bank transfers (the bank pays all 23), and the worker dies before the reply comes back, so step 4's entry is never
written. Asked "has step 4 been done?", the model believes the journal: No. The resume pays 23 contractors twice.

Three arms over two copies of the same run log: A the completion journal, Yes/No · B the same memory, Yes/No/Unknown
("answer Unknown if memory cannot settle it") · C a write-ahead journal: a "started" entry with the idempotency key
before each step, a result entry after, and coverage that says "started without a result is in doubt". Then the app
reconciles C's in-doubt step with the bank by its key and writes the result. The resume check is MemoryRouter: an
ordinary model call with `--boundary`.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router calls
are billed to your MemoryLake balance (about 80 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-ids"  # memorylake-usecases / in-doubt-step-memory


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



MODEL = "gpt-4o-mini"
TEMPERATURE = "0.7"   # an assistant's usual setting: the same question gets a slightly different reply each time
RUNS = 3              # answers per question and arm
MAX_TOKENS = 80
COPIES = ("journal", "wal")
COPY_LABEL = {"journal": "completion journal", "wal": "write-ahead journal"}
ARMS = ("A", "B", "C")
ARM_COPY = {"A": "journal", "B": "journal", "C": "wal"}
COPY_ARMS = {"journal": "A·B", "wal": "C"}
ARM_LABEL = {"A": "A  completion journal · Yes/No", "B": "B  completion journal · Yes/No/Unknown",
             "C": "C  write-ahead journal · Yes/No/Unknown"}
MARK = {"ok": "✓", "rerun": "✗", "skip": "↷", "hold": "‖", "other": "?"}
GRADE_NAME = {"ok": "right", "rerun": "re-runs a finished step", "skip": "skips an unfinished step",
              "hold": "stops to check a step memory could settle (Unknown)", "other": "unclear"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "run.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    sub = " " * (len(indent) - 2) + "| " if indent.endswith("| ") else " " * len(indent)
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=sub)


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def label(p: dict) -> str:
    return f"{p['display']} ({p['role']})"


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def started(d: dict) -> datetime:
    return datetime.fromisoformat(d["started"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def step(d: dict, n: int) -> dict:
    return next(s for s in d["steps"] if s["n"] == n)


def truth(s: dict) -> str:
    return "done" if s["done"] else "not done"


# A fact says a step did not happen when it reports a failure or a "not yet" about it.
FAILED = re.compile(r"\bfail|\bnot (?:yet )?(?:been )?(?:done|completed|run|sent|posted)|\bleft for\b|\bpending\b|"
                    r"\bblocked\b|\bunable\b|could not|未|失败", re.I)


def is_journal(fact: str) -> bool:
    return fact.startswith("Run journal ")


def topics_of(d: dict, fact: str) -> list[int]:
    return [s["n"] for s in d["steps"] if re.search(s["topic"], fact, re.I)]


def classify(d: dict, facts: list[str]) -> dict:
    """Per step: the facts that say it was done, failed, or only started; and the plan facts (one fact naming three or
    more steps is the plan, not news about any of them)."""
    per = {s["n"]: dict(done=[], failed=[], started=[]) for s in d["steps"]}
    plan, coverage = [], []
    for f in facts:
        if f.startswith("Run journal") and "coverage" in f[:40]:
            coverage.append(f)
            continue
        if is_journal(f):
            m = re.match(r"Run journal \S+: step (\d+) \([^)]*\) (started|completed|failed)\b", f)
            if m:
                per[int(m.group(1))][{"started": "started", "completed": "done", "failed": "failed"}[m.group(2)]].append(f)
                continue
        ns = topics_of(d, f)
        if len(ns) >= 3:
            plan.append(f)
            continue
        bad = bool(FAILED.search(f))
        for n in ns:
            per[n]["failed" if bad else "done"].append(f)
    return dict(steps=per, plan=plan, coverage=coverage)


def state_of(r: dict) -> str:
    """What memory holds about one step: done, failed, started with no result (in doubt), or nothing."""
    return "done" if r["done"] else "failed" if r["failed"] else "started" if r["started"] else "silent"


STATE = {"done": "· says done", "failed": "· says failed", "started": "· started, no result",
         "silent": "· nothing about it"}


# What the reply's first word says: Yes / No / Unknown.
FIRST = re.compile(r"^\W*(unknown|yes|no)\b", re.I)


def answer_of(reply: str) -> str:
    m = FIRST.search(reply or "")
    return m.group(1).lower() if m else "other"


def grade(s: dict, reply: str) -> str:
    """✓ right · ✗ "No" on a finished step: the resume re-runs it · ↷ "Yes" on an unfinished step: the resume skips it
    · ‖ "Unknown" on a step memory settles: the resume stops to check for nothing · ? anything else.
    The crashed step (the bank paid, no reply came back) is right as "Unknown" — check by the key — or "Yes"."""
    a = answer_of(reply)
    if a == "other":
        return "other"
    if a == "unknown":
        return "ok" if s.get("crash") else "hold"
    return "ok" if (a == "yes") == s["done"] else "rerun" if s["done"] else "skip"


def consequence(s: dict, verdict: str) -> str:
    return s.get("rerun", "") if verdict == "rerun" else s.get("skip", "") if verdict == "skip" else ""


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


def actor_keys(d: dict) -> list[str]:
    return [p["key"] for p in d["people"]]



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



# --------------------------------------------------------------------------- step 2: set up

def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, k) for k in actor_keys(d)))


def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    for n, c in enumerate(COPIES):
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['agent']} run memory ({COPY_LABEL[c]})",
                                       "--custom-id", cid(c), "--description",
                                       f"{d['org']} payout agent memory for run {d['run']}, {COPY_LABEL[c]}. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("two copies of the same run memory: " + " · ".join(COPY_LABEL[c] for c in COPIES))
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}. Demo data.", echo=n == 0,
                                               actor_type=p.get("type", "HUMAN"))
    note(f"{len(d['people'])} actors: " + " · ".join(label(p) for p in d["people"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Resume check ({COPY_LABEL[c]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per copy — what the resume check reads through MemoryRouter (A and B share the completion-journal copy)")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    d = load_data()
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
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



# --------------------------------------------------------------------------- step 3: the run

def append(cli: CLI, conv: str, actor: str, custom_id: str, ts: str, body: list[str], echo: bool) -> None:
    for attempt in range(5):
        try:
            cli.run("conv", "msg", "append", conv, "--actor", actor, "--custom-id", custom_id, "--timestamp", ts,
                    *body, scoped=True, echo=echo and attempt == 0)
            return
        except CLIError as e:
            # a resend after a dropped connection: the custom_id is already there, so the message is too
            if e.has("custom_id_conflict", "already exists") and attempt:
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def message_body(d: dict, m: dict) -> list[str]:
    """TEXT messages carry the speaker label; tool calls and results go in as TOOL_USE / TOOL_RESULT content blocks
    (`tool_call_id` + `tool_name` + `arguments`; `tool_call_id` + `result`)."""
    if "tool_use" in m:
        u = m["tool_use"]
        return ["--content-json", json.dumps([{"block_type": "TOOL_USE", "tool_call_id": u["id"], "tool_name": u["name"],
                                               "arguments": u["arguments"]}])]
    if "tool_result" in m:
        r = m["tool_result"]
        return ["--content-json", json.dumps([{"block_type": "TOOL_RESULT", "tool_call_id": r["id"], "result": r["result"]}])]
    return ["--text", f"{label(person(d, m['who']))}: {m['text']}"]


def shown(d: dict, m: dict) -> str:
    who = person(d, m["who"])["display"]
    if "tool_use" in m:
        return f"{who} → tool {m['tool_use']['name']}({json.dumps(m['tool_use']['arguments'])})"
    if "tool_result" in m:
        return f"tool result → {json.dumps(m['tool_result']['result'])}"
    return f"{who}: {m['text']}"


def show_run(d: dict) -> None:
    t0 = started(d)
    for i, m in enumerate(d["messages"]):
        kind = "TOOL_USE   " if "tool_use" in m else "TOOL_RESULT" if "tool_result" in m else "TEXT       "
        emit("text", wrap(shown(d, m), f"    {t0 + timedelta(minutes=3 * i):%H:%M}  {kind}  ", 118))
    emit("text", f"    {t0 + timedelta(minutes=3 * len(d['messages']) - 2):%H:%M}  — the bank settles all 23 transfers; the worker "
                 "process dies before the reply arrives. Nothing more is written.")
    emit("text", "\n    where each step stands, and where the run log says so:")
    for s in d["steps"]:
        emit("text", f"      {s['n']}  {s['what']:<45} {truth(s):<9} {s['how']}")
    emit("run_log", "", messages=[dict(m, shown=shown(d, m), at=utc(t0 + timedelta(minutes=3 * i)))
                                  for i, m in enumerate(d["messages"])],
         steps=[dict(n=s["n"], what=s["what"], truth=truth(s), how=s["how"]) for s in d["steps"]])


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


def stored_results(cli: CLI, conv: str, echo: bool) -> list[dict]:
    """The tool blocks as the conversation stores them: step 4's TOOL_USE is there, its TOOL_RESULT never arrived."""
    # `conv msg list` takes no --workspace
    page = cli.run("conv", "msg", "list", conv, "--page-size", "100", echo=echo) or {}
    msgs = page.get("items") or [] if isinstance(page, dict) else page
    out = []
    for m in sorted(msgs, key=lambda m: m.get("sequence_no") or 0):
        for b in m.get("content") or []:
            kind = (b.get("block_type") or "").upper()
            if kind in ("TOOL_USE", "TOOL_RESULT"):
                out.append(dict(kind=kind, tool_call_id=b.get("tool_call_id"), tool_name=b.get("tool_name"),
                                body=b.get("arguments") if kind == "TOOL_USE" else b.get("result")))
    return out


def ingest(cli: CLI, world: dict) -> dict:
    """Both copies get the same run log: one GROUP conversation, every message at its time, tool calls as tool blocks."""
    d = load_data()
    show_run(d)
    emit("text", "\n    Both copies get the same run log: one GROUP conversation, each message as its sender at its time; the tool "
                 "call as a TOOL_USE block, the way an agent framework logs it.")
    convs: dict = {}
    late: list = []
    t0 = time.time()

    def copy(c):
        first = c == COPIES[0]
        conv = cli.run("conv", "create", "--project", world["projects"][c], "--actors", ",".join(world["actors"].values()),
                       "--kind", "GROUP", "--name", f"{d['agent']} · run {d['run']} ({COPY_LABEL[c]})",
                       "--custom-id", cid("conv", c), scoped=True, echo=first)
        for i, m in enumerate(d["messages"]):
            append(cli, conv["id"], world["actors"][m["who"]], f"m{i:02d}", utc(started(d) + timedelta(minutes=3 * i)),
                   message_body(d, m), echo=first and (i == 0 or "tool_use" in m))
        convs[c] = conv["id"]
        if not cooked(cli, conv["id"], echo=first):
            late.append(COPY_LABEL[c])
        emit("progress", f"    {COPY_LABEL[c]}: extracted · {int(time.time() - t0)} s", copy=c)
    with ThreadPoolExecutor(len(COPIES)) as pool:
        list(pool.map(copy, COPIES))
    if late:
        note(f"still extracting after 300 s: {', '.join(late)} — read as it stands")
    note(f"{len(COPIES)} GROUP conversations of {len(d['messages'])} messages, extracted in {int(time.time() - t0)} s")
    results = stored_results(cli, convs[COPIES[0]], echo=True)
    emit("text", "\n    the run log as stored (conv msg list) — the tool call, and no result after it:")
    for r in results:
        emit("text", f"      {r['kind']:<11} {r['tool_call_id']}  {r.get('tool_name') or ''} {json.dumps(r['body'])}")
    if not any(r["kind"] == "TOOL_RESULT" for r in results):
        emit("text", "      (no TOOL_RESULT: the worker died before the bank's reply — the bank had already paid)")
    emit("stored", "", results=results)
    world["convs"] = convs
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`
    return convs


# --------------------------------------------------------------------------- step 4: what the app writes

def add_facts(cli: CLI, project: str, texts: list[str], metas: list[dict], echo: bool = True) -> list[str]:
    """`fact add` keeps the text as given; step and status go into metadata with `fact update`."""
    res = cli.run("fact", "add", *texts, "--project", project, scoped=True, echo=echo)
    facts = (res or {}).get("facts") or []
    if len(facts) != len(texts):
        die(f"`fact add` returned {len(facts)} facts for {len(texts)} texts")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda fm: cli.run("fact", "update", fm[0]["id"], "--project", project, "--metadata",
                                         json.dumps(fm[1]), scoped=True, echo=echo and fm[0] is facts[0]),
                      zip(facts, metas)))
    return [f["id"] for f in facts]


def journal_texts(d: dict, c: str) -> tuple[list[str], list[dict]]:
    """What each app wrote while the run went on. Completion journal: an entry when a step completes, so step 4 — the
    worker died before the bank's reply — has none. Write-ahead journal: a started entry (with the idempotency key)
    before the step, then the result; step 4 has only its started entry."""
    texts, metas = [], []
    for s in d["steps"]:
        if c == "wal" and s.get("started"):
            texts.append(s["started"])
            metas.append(dict(source="run_journal", run=d["run"], step=s["n"], status="started"))
        if s.get("journal"):
            texts.append(s["journal"])
            metas.append(dict(source="run_journal", run=d["run"], step=s["n"], status="completed"))
    texts.append(d["coverage"][c])
    metas.append(dict(source="run_journal", run=d["run"], kind="coverage"))
    return texts, metas


def write_journal(cli: CLI, world: dict) -> dict:
    d = load_data()
    out = {}
    for n, c in enumerate(COPIES):
        texts, metas = journal_texts(d, c)
        out[c] = add_facts(cli, world["projects"][c], texts, metas, echo=n == 0)
        emit("text", f"\n    {COPY_ARMS[c]}  {COPY_LABEL[c]} ({len(texts) - 1} entries + coverage):")
        for t in texts:
            emit("text", wrap(t, "      ", 116))
        emit("journal", "", copy=c, entries=texts[:-1], coverage=texts[-1])
    note("both journals were written by the app as the run went on (replayed here with `fact add`); neither has a result "
         "for step 4 — the worker died between the bank paying and the reply")
    time.sleep(10)
    return out


# --------------------------------------------------------------------------- step 5: what memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, c in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][c], echo=echo and n == 0)]
        cl = classify(d, facts)
        out[c] = dict(facts=len(facts), steps={str(k): v for k, v in cl["steps"].items()}, plan=cl["plan"],
                      coverage=cl["coverage"], all=facts)
    return out


def steps_in(cen: dict, c: str, state: str) -> list[int]:
    return [int(k) for k, r in cen[c]["steps"].items() if state_of(r) == state]


def show_census(cen: dict) -> None:
    d = load_data()
    for s in d["steps"]:
        emit("text", f"\n    {s['n']}  {s['what']} — {truth(s)}   [{s['how']}]")
        for c in COPIES:
            r = cen[c]["steps"][str(s["n"])]
            st = state_of(r)
            if st == "silent":
                lines = ["· nothing about it" + ("   ← yet the bank paid" if s.get("crash") else "")]
            elif st == "started":
                lines = ["◐ " + r["started"][0] + "   (no result after it)"]
            else:
                lines = [("· " if s["done"] else "✗ ") + f for f in r["done"][:1]] + \
                        [("✗ " if s["done"] else "· ") + f for f in r["failed"][:1]]
            for ln in lines:
                emit("text", wrap(ln, f"      {COPY_ARMS[c]:<4} ", 122))
    emit("text", "")
    for c in COPIES:
        sil, doubt = steps_in(cen, c, "silent"), steps_in(cen, c, "started")
        done_sil = [n for n in sil if step(d, n)["done"]]
        emit("text", f"    {COPY_LABEL[c]:<20} {cen[c]['facts']:>2} facts · nothing about step(s) {', '.join(map(str, sil)) or '—'}"
                     + (f"  (of which done: {', '.join(map(str, done_sil))})" if done_sil else "")
                     + (f" · in doubt: {', '.join(map(str, doubt))}" if doubt else "")
                     + (" · coverage: “" + ("no entry means not completed" if c == "journal" else "started without a result is in doubt") + "”"
                        if cen[c]["coverage"] else ""))
    if any(cen[c]["plan"] for c in COPIES):
        emit("text", "    (a fact naming three or more steps is the plan, not news about any step: "
                     + ", ".join(f"{COPY_LABEL[c]} {len(cen[c]['plan'])}" for c in COPIES) + ")")
    emit("census", "", copies={c: cen[c] for c in COPIES})


# --------------------------------------------------------------------------- step 6: the resume check

def system_prompt(d: dict, arm: str) -> str:
    return d["system" if arm == "A" else "system_u"].format(today=today(d).strftime("%A %Y-%m-%d"))


def question(d: dict, arm: str, s: dict) -> str:
    return d["ask" if arm == "A" else "ask_u"].format(n=s["n"], what=s["what"])


def ask_rows(cli: CLI, world: dict, arm: str, echo_first: bool = True, steps: list[dict] | None = None,
             phase: str = "resume") -> list[dict]:
    d = load_data()
    system = system_prompt(d, arm)
    steps = steps or d["steps"]
    jobs = [(s, i) for s in steps for i in range(RUNS)]
    for n, s in enumerate(steps):
        baseline(cli, question(d, arm, s), system, echo=echo_first and n == 0)

    def one(job):
        s, i = job
        q = question(d, arm, s)
        r = router(cli, q, system, world["boundaries"][ARM_COPY[arm]], echo=echo_first and job is jobs[0])
        if not r:
            return dict(step=s["n"], arm=arm, run=i + 1, verdict=None, empty=False, reply="", phase=phase)
        v = grade(s, r["reply"])
        return dict(step=s["n"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], phase=phase,
                    empty=r["prompt_tokens"] <= BASE[(q, system)], verdict=v, answer=answer_of(r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for s in steps:
        mine = [r for r in rows if r["step"] == s["n"]]
        marks = " ".join("∅" if r.get("empty") else MARK.get(r["verdict"], "–") for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {s['n']} {s['what'][:30]:<30} {truth(s):<8} {marks:<6} " + (first[:70] + "…" if len(first) > 71 else first))
        emit("ask_row", "", arm=arm, step=s["n"], rows=mine, truth=truth(s), phase=phase)
    if phase == "resume":
        show_example(rows, arm)
    return rows


def show_example(rows: list[dict], arm: str) -> None:
    """One reply in full: step 4 (the step the bank paid), a re-run if there is one."""
    d = load_data()
    mine = [r for r in rows if r["step"] == 4 and r.get("reply") and not r.get("empty")]
    r = next((r for r in mine if r["verdict"] == "rerun"), None) or next(iter(mine), None)
    if not r:
        return
    s = step(d, r["step"])
    emit("text", f"\n    resume check: “{question(d, arm, s)}”   (truth: the bank paid; no reply was recorded)")
    emit("text", wrap(r["reply"], f"    {MARK[r['verdict']]} | ", 104))
    if consequence(s, r["verdict"]):
        emit("text", f"    → the resume {consequence(s, r['verdict'])}")
    emit("example", "", arm=arm, row=r, ask=question(d, arm, s), truth=truth(s), consequence=consequence(s, r["verdict"]))


# --------------------------------------------------------------------------- step 7: reconcile what is in doubt (C)

KEY = re.compile(r"idempotency key ([A-Za-z0-9_-]+)")


def reconcile(cli: CLI, world: dict, cen: dict) -> dict:
    """C: for each step the write-ahead journal holds as started-without-result, look its idempotency key up at the
    bank, write the result into the journal, and ask the resume check again."""
    d = load_data()
    doubt = steps_in(cen, "wal", "started")
    if not doubt:
        note("the write-ahead journal has no step in doubt; nothing to reconcile")
        emit("reconciled", "", steps=[])
        return dict(steps=[], rows=[])
    done = []
    for n in doubt:
        entry = cen["wal"]["steps"][str(n)]["started"][0]
        m = KEY.search(entry)
        key = m.group(1) if m else None
        rec = d["bank"].get(key) if key else None
        emit("text", f"    step {n}: started entry has idempotency key {key or '—'}")
        if not rec:
            emit("text", "      the bank has no record of that key: the step did not happen — the resume can run it")
            continue
        emit("text", f"      bank lookup {key} → {json.dumps(rec)}")
        done.append(n)
    if done:
        texts = [d["reconciled"]]
        add_facts(cli, world["projects"]["wal"], texts,
                  [dict(source="run_journal", run=d["run"], step=4, status="completed", reconciled_by=KEY.search(texts[0]).group(1))])
        emit("text", wrap(texts[0], "      ", 116))
        note("the app writes the bank's answer into the write-ahead journal; asked again, C reads it")
    emit("reconciled", "", steps=done)
    time.sleep(10)
    rows = ask_rows(cli, world, "C", echo_first=False, steps=[step(d, n) for n in done], phase="reconciled")
    return dict(steps=done, rows=rows)


# --------------------------------------------------------------------------- step 8: the score

def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("verdict")]
    k = {v: sum(1 for r in live if not r.get("empty") and r["verdict"] == v) for v in MARK}
    return dict(k, calls=len(live), empty=sum(1 for r in live if r.get("empty")))


def answer_mix(rows: list[dict]) -> str:
    live = [r for r in rows if r.get("verdict") and not r.get("empty")]
    names = {"yes": "Yes", "no": "No", "unknown": "Unknown", "other": "?"}
    return " ".join(f"{names[a]} {sum(1 for r in live if r['answer'] == a)}" for a in names
                    if any(r["answer"] == a for r in live)) or "–"


def score(cen: dict, rows: list[dict], after: list[dict]) -> dict:
    d = load_data()
    out: dict = {"answers": {}, "by_step": {}, "by_state": {}, "side_effects": {}, "step4": {}}
    emit("text", f"    what the resume would do, per step ({RUNS} answers per arm):")
    emit("text", f"      {'':<47}{'truth':<10}" + "".join(f"{a:<10}" for a in ARMS))
    cell = lambda x: (" ".join(f"{MARK[v]}{x[v]}" for v in MARK if x[v]) + (f" ∅{x['empty']}" if x["empty"] else "")).strip() or "–"  # noqa: E731
    for s in d["steps"]:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["step"] == s["n"] and r.get("verdict")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = {v: sum(1 for r in live if r["verdict"] == v) for v in MARK}
            cells[a].update(n=len(live), empty=len(mine) - len(live))
        out["by_step"][str(s["n"])] = cells
        emit("text", f"      {s['n']} {s['what']:<45}{truth(s):<10}" + "".join(f"{cell(cells[a]):<10}" for a in ARMS))
    for a in ARMS:
        c = ARM_COPY[a]
        for s in d["steps"]:
            st = state_of(cen[c]["steps"][str(s["n"])])
            key = f"{st}|{'done' if s['done'] else 'not done'}"
            for r in rows:
                if r["arm"] == a and r["step"] == s["n"] and r.get("verdict") and not r.get("empty"):
                    b = out["by_state"].setdefault(a, {}).setdefault(key, {v: 0 for v in MARK})
                    b[r["verdict"]] += 1
    emit("text", "\n    the answers, by what memory held about the step:")
    for key in ("done|done", "silent|done", "started|done", "silent|not done"):
        st, tr = key.split("|")
        cells = []
        for a in ARMS:
            b = out["by_state"].get(a, {}).get(key)
            cells.append((" ".join(f"{MARK[v]}{b[v]}" for v in MARK if b[v]) if b else "–"))
        emit("text", f"      memory {STATE[st][2:]:<20} · step {tr:<9}" + "".join(f"{x:<12}" for x in cells))
    emit("text", "\n    step 4 — the bank paid, no reply was recorded — answers per arm:")
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["step"] == 4]
        out["step4"][a] = answer_mix(mine)
        emit("text", f"      {ARM_LABEL[a]:<42} {out['step4'][a]}")
    if after:
        out["step4"]["C after reconcile"] = answer_mix(after)
        emit("text", f"      {'C  after the bank lookup is written':<42} {out['step4']['C after reconcile']}")
    emit("text", "")
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a]
        t = tally(mine)
        side = [r for r in mine if r.get("verdict") == "rerun" and not r.get("empty") and step(d, r["step"]).get("crash")]
        out["answers"][a] = t
        out["side_effects"][a] = len(side)
        emit("text", f"    {ARM_LABEL[a]:<42} right {t['ok']:>2}/{t['calls'] - t['empty']} · pays twice {len(side)}"
                     f" · re-runs a finished step {t['rerun']:>2} · skips {t['skip']} · stops to check {t['hold']:>2}"
                     + (f" · other {t['other']}" if t["other"] else "") + (f" · ∅ {t['empty']}" if t["empty"] else ""))
    worst = max(ARMS, key=lambda a: out["side_effects"][a])
    if out["side_effects"][worst]:
        n4 = sum(1 for r in rows if r["arm"] == worst and r["step"] == 4 and r.get("verdict") and not r.get("empty"))
        emit("text", f"\n    In {worst}, {out['side_effects'][worst]} of {n4} answers about step 4 said “not done”: the resume "
                     f"{step(d, 4)['rerun']}.")
    emit("text", "\n    ✓ right · ✗ “No” on a finished step: the resume re-runs it · ↷ “Yes” on an unfinished step: the resume "
                 "skips it · ‖ “Unknown” on a step memory settles: the resume stops to check for nothing · ? unclear · "
                 "∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call; left out of the counts). "
                 "On step 4, “Unknown” is right: check the bank by the key before retrying.")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['agent']}'s run memory, two copies")
    world = setup(cli)

    banner(3, TOTAL, f"Payout run {d['run']} — seven steps, a crash inside step 4")
    ingest(cli, world)

    banner(4, TOTAL, "The run journal the app wrote — completion-only vs write-ahead")
    journal = write_journal(cli, world)

    banner(5, TOTAL, "What memory says about each step")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(6, TOTAL, "The resume check — “has step N already been done?”")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a in ("A", "B"))
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine

    banner(7, TOTAL, "C only — reconcile the step in doubt with the bank, by its key")
    rec = reconcile(cli, world, cen)

    banner(8, TOTAL, "The score — what the resume would do")
    sc = score(cen, rows, rec["rows"])

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census={c: cen[c] for c in COPIES}, journal=journal, answers=rows, reconciled=rec, score=sc, tokens=tok,
                   seconds=int(time.time() - t0), router=cli.router_ok, today=today(d).isoformat())  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """What each copy remembers about each step."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("memory_view", "", copies={c: cen[c] for c in COPIES})
    return cen


def ask(cli: CLI, what: str) -> list[dict]:
    """A step number asks the resume check about that step, through A, B and C, graded; any other text is asked as is."""
    d = load_data()
    world = load_world(cli)
    s = step(d, int(what)) if what.strip().isdigit() and 1 <= int(what) <= len(d["steps"]) else None
    rows = []
    for n, a in enumerate(ARMS):
        system = system_prompt(d, a)
        q = question(d, a, s) if s else what.strip()
        base = baseline(cli, q, system, echo=n == 0)
        r = router(cli, q, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = grade(s, r["reply"]) if s else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        if s and consequence(s, g):
            emit("text", f"    → the resume {consequence(s, g)}")
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, verdict=g, consequence=s and consequence(s, g)))
    if s:
        emit("text", f"\n  truth: step {s['n']} ({s['what']}) — {truth(s)}")
    emit("asked", "", question=what, step=s and s["n"], rows=rows, truth=s and truth(s))
    return rows


# --------------------------------------------------------------------------- cleanup

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


def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = [c for c in paged(cli, ["conv", "list", "--page-size", "100"])
             if (c.get("custom_id") or "").startswith(PREFIX + "-")]
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=False)
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
        note(f"deleted {len(bs)} boundaries, {len(convs)} conversations, {nfacts} facts, {n_act} actors (facts first: a "
             f"deleted actor's facts could never be removed) and {nproj} projects" if (bs or convs or n_act or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")



EXAMPLE = "4"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = what each copy remembers about each step; "
                         "ask <step number | \"question\"> = the resume check through A, B and C; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: a step number (1-7) or a question")
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
        banner(2, 2, "What each copy remembers about each step")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask <step number | "question">   (e.g. python3 demo.py ask {EXAMPLE})')
        banner(2, 2, "The resume check, through A, B and C")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py memory`, or `python3 demo.py ask {EXAMPLE}`; "
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

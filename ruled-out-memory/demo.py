#!/usr/bin/env python3
"""ruled-out-memory — a ReAct investigation rules out four suspects, runs out of steps, and memory keeps the lead
but not what was ruled out.

Tarnwick (a fictional retailer) has an investigation agent, Sleuth, on incident INC-2291: checkout API p99 latency went
from 300 ms to 4.1 s. In one run of five actions the ReAct loop checks four suspects — a cold Redis cache, the
payments-gateway connection pool, the fraud-scorer sidecar, DNS — and each observation rules one out; the fifth action
finds a lead (a migration rebuilt the orders-table indexes at 02:05). The budget is spent; the incident is still open.
The next run asks memory what the investigation already established.

Five ways to store the same loop, one project each: A the framework default (Thought as TEXT, Action and Observation
as TOOL_USE / TOOL_RESULT blocks) · B the scratchpad as TEXT ("Thought / Action / Observation" per iteration) · C the
same, with each next Thought stating the verdict ("… so X is ruled out") · D one typed step record per iteration
written by the app (`fact add`: hypothesis, action, observation, verdict) · E the same records plus one coverage
record (these five were checked, nothing else was). Extraction keeps what was found and drops
what was ruled out; the resumed run, through MemoryRouter, plans to check the dead ends again.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router calls
are billed to your MemoryLake balance (about 130 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-rom"  # memorylake-usecases / ruled-out-memory


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
        # Everything from the first option on is kept, so the scope (`--project P`) stays visible.
        if shown[:2] == ["fact", "add"]:
            i = next((k for k in range(2, len(shown)) if shown[k].startswith("--")), len(shown))
            if i - 2 > 2:
                shown = shown[:4] + [f"… {i - 4} more texts"] + shown[i:]
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
TEMPERATURE = "0.7"   # an agent's usual setting: the same question gets a slightly different reply each time
RUNS = 3              # answers per question and arm
MAX_TOKENS = 90
ARMS = ("typed", "scratch", "stated", "records", "covered")   # the five memories, one project each
ALL = ("none",) + ARMS                                 # plus the same questions with no memory at all
CONVERSED = ("typed", "scratch", "stated")             # the arms whose memory comes from extraction
SHORT = {"none": "0", "typed": "A", "scratch": "B", "stated": "C", "records": "D", "covered": "E"}
ARM_LABEL = {"none": "0  no memory", "typed": "A  typed tool blocks", "scratch": "B  scratchpad as text",
             "stated": "C  text + stated verdicts", "records": "D  step records (fact add)",
             "covered": "E  step records + coverage"}
MARK = {"ok": "✓", "guess": "◇", "recheck": "↻", "skip": "✗", "other": "?"}
GRADE_NAME = {"ok": "right", "guess": "right word, but memory holds no such verdict", "recheck": "the resume checks it again",
              "skip": "claims a check that never ran", "other": "unclear"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "incident.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    sub = " " * (len(indent) - 2) + "| " if indent.endswith("| ") else " " * len(indent)
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=sub)


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def label(p: dict) -> str:
    return f"{p['display']} ({p['role']})"


def started(d: dict) -> datetime:
    return datetime.fromisoformat(d["started"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def suspect(d: dict, key: str) -> dict:
    return next(s for s in d["suspects"] if s["key"] == key)


def ruled_tools(d: dict) -> set:
    return {s["tool"] for s in d["suspects"] if s["checked"]}


def call(it: dict) -> str:
    return f"{it['tool']}({', '.join(f'{k}={v}' for k, v in it['arguments'].items())})"


def messages(d: dict, arm: str) -> list[dict]:
    """The run's messages as arm `arm` stores them: who, the CLI body, and how it reads on screen."""
    eng, ag = person(d, "eng"), person(d, "agent")
    out = [dict(who="eng", kind="TEXT", text=f"{label(eng)}: {d['opening']}")]
    its = d["iterations"]
    for i, it in enumerate(its):
        prev = f"Thought: {its[i - 1]['conclusion']} " if arm == "stated" and i else ""
        thought = f"{prev}Thought: {it['thought']}"
        if arm == "typed":
            out.append(dict(who="agent", kind="TEXT", text=f"{label(ag)}: {thought}"))
            out.append(dict(who="agent", kind="TOOL_USE", blocks=[{"block_type": "TOOL_USE", "tool_call_id": f"call-{it['n']}",
                                                                 "tool_name": it["tool"], "arguments": it["arguments"]}]))
            out.append(dict(who="agent", kind="TOOL_RESULT", blocks=[{"block_type": "TOOL_RESULT", "tool_call_id": f"call-{it['n']}",
                                                                    "result": it["result"]}]))
        else:
            out.append(dict(who="agent", kind="TEXT", text=f"{label(ag)}: {thought}\nAction: {it['tool']}("
                            f"{json.dumps(it['arguments'])})\nObservation: {json.dumps(it['result'])}"))
    last = f"Thought: {its[-1]['conclusion']} " if arm == "stated" else ""
    out.append(dict(who="agent", kind="TEXT", text=f"{label(ag)}: {last}{d['closing']}"))
    return out


def body_args(m: dict) -> list[str]:
    return ["--content-json", json.dumps(m["blocks"])] if "blocks" in m else ["--text", m["text"]]


def shown(m: dict) -> str:
    if m["kind"] == "TEXT":
        return m["text"].replace("\n", "  ")
    b = m["blocks"][0]
    if m["kind"] == "TOOL_USE":
        return f"→ {b['tool_name']}({json.dumps(b['arguments'])})"
    return f"← {json.dumps(b['result'])}"


def record_text(d: dict, it: dict) -> str:
    at = started(d) + timedelta(minutes=3 * it["n"])
    return (f"{d['incident']} step {it['n']} ({at:%Y-%m-%d %H:%M} UTC): hypothesis - {it['short']}; action - {call(it)}; "
            f"observation - {it['observation']}; verdict - {it['verdict']}.")


# --------------------------------------------------------------------------- objects

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


# --------------------------------------------------------------------------- step 2: set up

def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, a) for a in ARMS)
                or any(find_actor(cli, k) for k in actor_keys(d)))


def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    # The project name is context for extraction, so every arm's project has the same name; the arm is in the custom_id.
    for n, a in enumerate(ARMS):
        world["projects"][a] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid(a),
                                       "--description", f"{d['org']} checkout incident memory. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note(f"{len(ARMS)} projects, all named “{d['project_name']}”: " + " · ".join(ARM_LABEL[a] for a in ARMS))
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}. Demo data.", echo=n == 0)
    note(f"{len(d['people'])} actors: " + " · ".join(label(p) for p in d["people"]))
    for n, a in enumerate(ARMS):
        b = cli.run("boundary", "create", "--name", f"{d['incident']} resume ({ARM_LABEL[a][3:]})",
                    "--custom-id", cid(a), "--projects", world["projects"][a], scoped=True, echo=n == 0)
        world["boundaries"][a] = b["id"]
    note("one boundary per arm — what the resumed run reads through MemoryRouter")
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    d = load_data()
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    for a in ARMS:
        p = find_project(cli, a)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["projects"][a] = p["id"]
    for k in actor_keys(d):
        a = find_actor(cli, k)
        if a:
            world["actors"][k] = a["id"]
    world["boundaries"] = {b["custom_id"][len(PREFIX) + 1:]: b["id"] for b in demo_boundaries(cli)}
    return world


# --------------------------------------------------------------------------- step 3: run 1

def show_run(d: dict) -> None:
    emit("text", wrap(f"{d['incident']} · {d['symptom']}. {d['agent']} has {d['budget']} read-only actions.", "    ", 118))
    for it in d["iterations"]:
        emit("text", f"\n    iteration {it['n']}")
        emit("text", wrap(f"Thought      {it['thought']}", "      ", 118))
        emit("text", wrap(f"Action       {call(it)}", "      ", 118))
        emit("text", wrap(f"Observation  {json.dumps(it['result'])}", "      ", 118))
        emit("text", f"      → {it['short']}: {it['verdict'].upper()}")
    emit("text", f"\n    {d['closing']}")
    emit("run1", "", iterations=d["iterations"], closing=d["closing"], opening=d["opening"])


def watermark(cli: CLI, project: str) -> tuple:
    page = cli.run("fact", "list", "--projects", project, "--page-size", "1", scoped=True, echo=False) or {}
    items = page.get("items") or []
    return (page.get("total"), items[0].get("updated_at") if items else None)


def settle(cli: CLI, conv: str, project: str, echo: bool, limit: int = 300) -> dict:
    """Wait until extraction is done: cook finished three polls in a row, the project's facts unchanged over them, and
    at least 60 s since the last message. `--wait` and a single "finished" have both reported done before any write."""
    t0, first, streak, last = time.time(), echo, 0, None
    while time.time() - t0 < limit:
        try:
            st = cli.run("conv", "cook-status", conv, scoped=True, echo=first) or {}
            first = False
            wm = watermark(cli, project)
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(5)
            continue
        streak = streak + 1 if st.get("cook_finished") and wm == last else 0
        last = wm
        if streak >= 3 and time.time() - t0 >= 60:
            return dict(done=True, facts=wm[0] or 0, seconds=int(time.time() - t0))
        time.sleep(5)
    return dict(done=False, facts=(last or (0,))[0] or 0, seconds=int(time.time() - t0))


def write_conversation(cli: CLI, world: dict, arm: str, echo: bool) -> dict:
    d = load_data()
    conv = cli.run("conv", "create", "--custom-id", cid("conv", arm), "--project", world["projects"][arm],
                   "--actors", ",".join(world["actors"][k] for k in actor_keys(d)),
                   "--name", f"{d['incident']} checkout latency", scoped=True, echo=echo)
    msgs = messages(d, arm)
    for i, m in enumerate(msgs):
        append(cli, conv["id"], world["actors"][m["who"]], f"m{i:02d}", utc(started(d) + timedelta(minutes=2 * i)),
               body_args(m), echo=echo and i in (0, 1))
    res = settle(cli, conv["id"], world["projects"][arm], echo=echo)
    return dict(conv=conv["id"], messages=len(msgs), **res)


def write_records(cli: CLI, world: dict, arm: str, echo: bool) -> list[str]:
    """D: the app commits one typed record per iteration — hypothesis, action, observation, verdict.
    E: the same records, closed by one coverage record: what was checked, and that nothing else was."""
    d = load_data()
    p = world["projects"][arm]
    texts = [record_text(d, it) for it in d["iterations"]] + ([d["coverage"]] if arm == "covered" else [])
    res = cli.run("fact", "add", *texts, "--project", p, scoped=True, echo=echo)
    facts = (res or {}).get("facts") or []
    if len(facts) != len(texts):
        die(f"`fact add` returned {len(facts)} facts for {len(texts)} texts")
    metas = [dict(source="react_step", incident=d["incident"], step=it["n"], hypothesis=it["suspect"], tool=it["tool"],
                  verdict=it["verdict"]) for it in d["iterations"]]
    if arm == "covered":
        metas.append(dict(source="react_coverage", incident=d["incident"], run=1, checked=[it["tool"] for it in d["iterations"]]))
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda fm: cli.run("fact", "update", fm[0]["id"], "--project", p, "--metadata", json.dumps(fm[1]),
                                         scoped=True, echo=echo and fm[0] is facts[0]), zip(facts, metas)))
    return texts


def run_one(cli: CLI, world: dict) -> dict:
    d = load_data()
    show_run(d)
    emit("text", "\n    The same loop, stored five ways:")
    for a in ARMS:
        ex = next((m for m in messages(d, a) if m["kind"] == "TEXT" and "Thought" in m["text"]), None) if a != "records" else None
        what = {"typed": "Thought as TEXT, Action / Observation as TOOL_USE / TOOL_RESULT blocks (the framework default)",
                "scratch": "one TEXT message per iteration: Thought / Action / Observation",
                "stated": "as B, and each next Thought first states the verdict (“… so X is ruled out”)",
                "records": "no conversation: one `fact add` record per iteration, written by the app",
                "covered": "as D, plus one coverage record: these five were checked, nothing else was"}[a]
        emit("text", wrap(f"{SHORT[a]}  {what}", "      ", 118))
    out: dict = {"arms": {}}
    t0 = time.time()

    def conv_arm(a):
        r = write_conversation(cli, world, a, echo=a == "typed")
        out["arms"][a] = r
        emit("progress", f"    {SHORT[a]}: {r['messages']} messages, extraction settled after {r['seconds']} s · "
                         f"{r['facts']} fact(s) in the project" + ("" if r["done"] else " (still changing at 300 s)"), arm=a)
    with ThreadPoolExecutor(len(CONVERSED)) as pool:
        futs = [pool.submit(conv_arm, a) for a in CONVERSED]
        texts = write_records(cli, world, "records", echo=True)
        for t in texts:
            emit("text", wrap(t, "      D  ", 118))
        out["arms"]["records"] = dict(records=len(texts))
        etexts = write_records(cli, world, "covered", echo=False)
        emit("text", wrap(etexts[-1], "      E  ", 118))
        out["arms"]["covered"] = dict(records=len(etexts))
        for f in futs:
            f.result()
    note(f"run 1 stored five ways in {int(time.time() - t0)} s")
    emit("stored", "", **out)
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`
    return out


# --------------------------------------------------------------------------- step 4: what memory holds

RULED = re.compile(r"ruled out|rules out|\bnot (?:the|a) (?:cause|culprit|issue|problem)|isn't the cause|not responsible|"
                   r"\bexcluded|eliminated|\bhealthy\b|\bnormal\b|\bis fine\b|no (?:issue|problem)|排除|不是原因|正常", re.I)
SUSPECTED = re.compile(r"\blikely\b|probably|suspect|could be|\bmight\b|may be|maybe|hypothes|可能|怀疑", re.I)
STATE = {"ruled": "says ruled out", "suspected": "still a suspect", "reading": "a bare reading", "mention": "mentioned",
         "none": "nothing"}


def classify(d: dict, facts: list[str]) -> dict:
    """Per suspect, the strongest thing memory says about it: ruled out > still suspected > a bare reading > a mention."""
    rank = ["ruled", "suspected", "reading", "mention"]
    out = {}
    for s in d["suspects"]:
        best = None
        for f in facts:
            if not re.search(s["topic"], f, re.I):
                continue
            st = ("ruled" if RULED.search(f) else "suspected" if SUSPECTED.search(f)
                  else "reading" if re.search(r"\d", re.sub(r"\(?(?:as of|on) \d{4}-\d\d-\d\d\)?|\d{4}-\d\d-\d\d", "", f)) else "mention")
            if best is None or rank.index(st) < rank.index(best[0]):
                best = (st, f)
        out[s["key"]] = dict(state=best[0] if best else "none", fact=best[1] if best else "")
    lead = [f for f in facts if re.search(d["lead_topic"], f, re.I)]
    return dict(suspects=out, lead=lead)


def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, a in enumerate(ARMS):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][a], echo=echo and n == 0)]
        # A step record names one suspect; show it before the coverage line that names all four.
        ordered = sorted(facts, key=lambda f: " coverage (" in f[:40])
        out[a] = dict(classify(d, ordered), facts=len(facts), all=facts)
    side = []
    for k, aid in world["actors"].items():
        side += [f.get("fact") or "" for f in actor_facts(cli, aid)]
    out["_actors"] = dict(facts=len(side), all=side)
    return out


def show_census(cen: dict) -> None:
    d = load_data()
    for s in d["suspects"]:
        emit("text", f"\n    {s['name']} — {'ruled out in run 1' if s['checked'] else 'never checked'}")
        for a in ARMS:
            r = cen[a]["suspects"][s["key"]]
            mark = ("✓ " if r["state"] == "ruled" else "✗ " if r["state"] == "suspected" else "· ") if s["checked"] else \
                   ("✗ " if r["state"] == "ruled" else "· ")
            body = f"{STATE[r['state']]}: {r['fact']}" if r["fact"] else STATE[r["state"]]
            emit("text", wrap(mark + body, f"      {SHORT[a]}  ", 122))
    emit("text", "\n    the lead (migration 0412 rebuilt the orders-table indexes):")
    for a in ARMS:
        emit("text", wrap(("✓ " + cen[a]["lead"][0]) if cen[a]["lead"] else "· nothing", f"      {SHORT[a]}  ", 122))
    emit("text", "")
    checked = [s["key"] for s in d["suspects"] if s["checked"]]
    for a in ARMS:
        k = sum(1 for x in checked if cen[a]["suspects"][x]["state"] == "ruled")
        sus = sum(1 for x in checked if cen[a]["suspects"][x]["state"] == "suspected")
        emit("text", f"    {ARM_LABEL[a]:<30} {cen[a]['facts']:>2} facts · ruled out {k} of {len(checked)}"
                     + (f" · {sus} still a suspect" if sus else "") + f" · lead {'kept' if cen[a]['lead'] else 'missing'}")
    emit("text", f"    (the two actors hold {cen['_actors']['facts']} fact(s) of their own; the boundaries read the projects)")
    emit("census", "", arms={a: cen[a] for a in ARMS}, actors=cen["_actors"])


# --------------------------------------------------------------------------- steps 5-6: the resumed run

def system_prompt(d: dict) -> str:
    now = datetime.fromisoformat(d["resume_at"].replace("Z", "+00:00"))
    tools = "; ".join(f"{t['name']} ({t['about']})" for t in d["tools"])
    return d["system"].format(now=f"{now:%A %Y-%m-%d, %H:%M} UTC", tools=tools)


def question(d: dict, q: str) -> str:
    return d["ask_plan"] if q == "plan" else d["ask_suspect"].format(name=suspect(d, q)["name"])


def plan_tools(d: dict, reply: str) -> list[str]:
    names = [t["name"] for t in d["tools"]]
    found = re.findall(r"\b(" + "|".join(names) + r")\b", reply or "")
    out = []
    for f in found:
        if f not in out:
            out.append(f)
    return out[:4]


FIRST = re.compile(r"^\W*(not recorded|yes|no)\b", re.I)


def answer_of(reply: str) -> str:
    m = FIRST.search(reply or "")
    return {"yes": "yes", "no": "no", "not recorded": "norec"}[m.group(1).lower()] if m else "other"


def grade(d: dict, q: str, reply: str, held: bool = False) -> dict:
    """held: this arm's memory says the suspect was ruled out. A Yes without that is a guess that happens to be true."""
    if q == "plan":
        tools = plan_tools(d, reply)
        re_ = [t for t in tools if t in ruled_tools(d)]
        return dict(verdict="other" if not tools else "recheck" if re_ else "ok", tools=tools, rechecks=re_)
    s, a = suspect(d, q), answer_of(reply)
    if a == "other":
        return dict(verdict="other", answer=a)
    if s["checked"]:
        return dict(verdict=("ok" if held else "guess") if a == "yes" else "recheck", answer=a)
    return dict(verdict="skip" if a == "yes" else "ok", answer=a)


def held_by(cen: dict | None, arm: str, q: str) -> bool:
    return bool(cen) and arm in cen and q in cen[arm]["suspects"] and cen[arm]["suspects"][q]["state"] == "ruled"


def ask_arm(cli: CLI, world: dict, arm: str, qs: list[str], cen: dict, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    jobs = [(q, i) for q in qs for i in range(RUNS)]

    def one(job):
        q, i = job
        text = question(d, q)
        bid = None if arm == "none" else world["boundaries"][arm]
        r = router(cli, text, system, bid, echo=echo_first and job is jobs[0])
        if not r:
            return dict(q=q, arm=arm, run=i + 1, verdict=None, empty=False, reply="")
        if arm == "none":
            with LOCK:
                BASE[(text, system)] = min(BASE.get((text, system), 10 ** 9), r["prompt_tokens"])
        g = grade(d, q, r["reply"], held_by(cen, arm, q))
        return dict(q=q, arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], **g)
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    if arm != "none":
        for r in rows:
            r["empty"] = bool(r.get("verdict")) and r["prompt_tokens"] <= BASE.get((question(d, r["q"]), system), 0)
    else:
        for r in rows:
            r["empty"] = False
    return rows


def show_plans(rows: list[dict], arm: str) -> None:
    d = load_data()
    ruled = ruled_tools(d)
    for r in rows:
        if not r.get("verdict"):
            continue
        if r["empty"]:
            emit("text", f"      ∅  (MemoryRouter attached nothing) " + ", ".join(r["tools"]))
            continue
        tools = ", ".join((f"↻{t}" if t in ruled else t) for t in r["tools"]) or " ".join(r["reply"].split())[:80]
        emit("text", f"      {MARK[r['verdict']]}  {tools}   ({len(r['rechecks'])} re-check{'s' if len(r['rechecks']) != 1 else ''})")
    emit("plan_rows", "", arm=arm, rows=rows)


def show_suspects(rows: list[dict], arm: str) -> None:
    d = load_data()
    for s in d["suspects"]:
        mine = [r for r in rows if r["q"] == s["key"] and r.get("verdict")]
        marks = " ".join("∅" if r["empty"] else MARK[r["verdict"]] for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r["verdict"] != "ok" and not r["empty"]),
                              next((r["reply"] for r in mine), "")).split())
        truth = "ruled out" if s["checked"] else "never checked"
        emit("text", f"      {s['name'][:44]:<44} {truth:<13} {marks:<6} " + (first[:64] + "…" if len(first) > 65 else first))
    emit("suspect_rows", "", arm=arm, rows=rows)


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("verdict")]
    plans = [r for r in live if r["q"] == "plan" and not r["empty"]]
    sus = [r for r in live if r["q"] != "plan" and not r["empty"]]
    d = load_data()
    checked = {s["key"] for s in d["suspects"] if s["checked"]}
    return dict(plans=len(plans), rechecks=sum(len(r["rechecks"]) for r in plans),
                clean_plans=sum(1 for r in plans if r["verdict"] == "ok"),
                known=sum(1 for r in sus if r["q"] in checked and r["verdict"] == "ok"),
                known_of=sum(1 for r in sus if r["q"] in checked),
                guessed=sum(1 for r in sus if r["q"] in checked and r["verdict"] == "guess"),
                claimed=sum(1 for r in sus if r["q"] not in checked and r["verdict"] == "skip"),
                claimed_of=sum(1 for r in sus if r["q"] not in checked),
                other=sum(1 for r in live if r["verdict"] == "other" and not r["empty"]),
                empty=sum(1 for r in live if r["empty"]), calls=len(live))


def score(cen: dict, rows: list[dict]) -> dict:
    d = load_data()
    out: dict = {"arms": {}, "by_suspect": {}}
    emit("text", f"    what the resumed run would do ({RUNS} answers per question and arm; ∅ left out):\n")
    emit("text", f"      {'':<32}{'plans':>6}{'re-checks':>11}{'knows ruled out':>17}{'guessed Yes':>13}{'claims an unchecked one':>25}")
    for a in ALL:
        t = tally([r for r in rows if r["arm"] == a])
        out["arms"][a] = t
        emit("text", f"      {ARM_LABEL[a]:<32}{t['plans']:>6}{t['rechecks']:>11}{t['known']:>11}/{t['known_of']:<5}"
                     f"{t['guessed']:>9}    {t['claimed']:>15}/{t['claimed_of']:<5}" + (f"  ∅ {t['empty']}" if t["empty"] else "")
                     + (f"  ? {t['other']}" if t["other"] else ""))
    emit("text", "\n      per suspect — answers to “has the investigation already ruled out X?”:")
    emit("text", f"      {'':<46}" + "".join(f"{SHORT[a]:<9}" for a in ALL))
    for s in d["suspects"]:
        cells = {}
        for a in ALL:
            mine = [r for r in rows if r["arm"] == a and r["q"] == s["key"] and r.get("verdict")]
            live = [r for r in mine if not r["empty"]]
            cells[a] = {v: sum(1 for r in live if r["verdict"] == v) for v in MARK}
            cells[a]["empty"] = len(mine) - len(live)
        out["by_suspect"][s["key"]] = cells
        cell = lambda x: (" ".join(f"{MARK[v]}{x[v]}" for v in MARK if x[v]) + (f" ∅{x['empty']}" if x["empty"] else "")).strip() or "–"  # noqa: E731
        emit("text", f"      {s['name'][:44]:<46}" + "".join(f"{cell(cells[a]):<9}" for a in ALL))
    checked = [s["key"] for s in d["suspects"] if s["checked"]]
    out["memory"] = {a: sum(1 for x in checked if cen[a]["suspects"][x]["state"] == "ruled") for a in ARMS}
    emit("text", "\n    ✓ Yes, and memory says it was ruled out · ◇ Yes, but memory holds no such verdict (a guess) · ↻ No / Not recorded on a suspect run 1 ruled out: the resume checks it again · ✗ Yes on a "
                 "suspect never checked: the resume skips it · ∅ MemoryRouter attached nothing (prompt tokens equal the "
                 "no-memory call)")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — five projects, one per way of storing the loop")
    world = setup(cli)

    banner(3, TOTAL, f"Run 1 — {d['incident']}: four suspects ruled out, one lead, budget spent")
    stored = run_one(cli, world)

    banner(4, TOTAL, "What memory says about each suspect")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(5, TOTAL, "Run 2 resumes — “pick the next 4 actions”")
    note(f"the same system prompt and question for every arm; 0 has no --boundary (its prompt tokens are the ∅ baseline); "
         f"{RUNS} answers each, 6 at a time")
    for a in ALL:
        emit("text", f"\n    {ARM_LABEL[a]}")
        mine = ask_arm(cli, world, a, ["plan"], cen, echo_first=a in ("none", "typed"))
        show_plans(mine, a)
        rows += mine

    banner(6, TOTAL, "Run 2 asks memory — “has the investigation already ruled out X?”")
    keys = [s["key"] for s in d["suspects"]]
    for a in ALL:
        emit("text", f"\n    {ARM_LABEL[a]}")
        mine = ask_arm(cli, world, a, keys, cen, echo_first=a == "typed")
        show_suspects(mine, a)
        rows += mine
        emit("arm_done", "", arm=a, tally=tally([r for r in rows if r["arm"] == a]))

    banner(7, TOTAL, "The score — what the resumed run would do")
    sc = score(cen, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cen, stored=stored, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """What each arm remembers about each suspect."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("memory_view", "", arms={a: cen[a] for a in ARMS})
    return cen


def ask(cli: CLI, what: str) -> list[dict]:
    """`plan`, a suspect key (cache, pool, sidecar, dns, queries, locks) or any question, through 0 and A–E."""
    d = load_data()
    world = load_world(cli)
    keys = [s["key"] for s in d["suspects"]]
    q = what.strip().lower() if what.strip().lower() in keys + ["plan"] else None
    text = question(d, q) if q else what.strip()
    system = system_prompt(d)
    cen = census(cli, world, echo=False) if q and q != "plan" else None
    emit("text", f"  “{text}”")
    rows = []
    base = None
    for n, a in enumerate(ALL):
        r = router(cli, text, system, None if a == "none" else world["boundaries"].get(a), echo=n < 2)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        if a == "none":
            base = r["prompt_tokens"]
        empty = a != "none" and r["prompt_tokens"] <= (base or 0)
        g = grade(d, q, r["reply"], held_by(cen, a, q)) if q else {}
        head = "∅ nothing attached" if empty else (f"{MARK[g['verdict']]} {GRADE_NAME[g['verdict']]}" if g.get("verdict") else "")
        if q == "plan" and g.get("rechecks"):
            head += f" ({', '.join(g['rechecks'])})"
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, **g))
    if q and q != "plan":
        s = suspect(d, q)
        emit("text", f"\n  truth: {s['name']} — {'ruled out in run 1' if s['checked'] else 'never checked'}")
    emit("asked", "", question=what, q=q, rows=rows)
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
    # `proj delete` does not reliably delete conversations: they go explicitly, found by custom_id.
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
    for a in ARMS:
        p = find_project(cli, a)
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


EXAMPLE = "cache"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = what each arm remembers about each suspect; "
                         "ask <plan | suspect | \"question\"> = the resumed run's question through 0 and A–E; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: plan, a suspect (cache, pool, sidecar, dns, queries, locks) or a question")
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
        banner(2, 2, "What each arm remembers about each suspect")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask <plan | suspect | "question">   (e.g. python3 demo.py ask {EXAMPLE})')
        banner(2, 2, "The resumed run's question, through 0 and A–E")
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

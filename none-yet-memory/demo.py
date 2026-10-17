#!/usr/bin/env python3
"""none-yet-memory — the "not yet" that never expires.

Tallyfern (a fictional software vendor) is selling dock scheduling to Brackwell Freight (a fictional haulier). At the end
of the first discovery call, the account executive read a recap into the call: no budget yet, nobody has agreed to
champion it, no other vendor in the picture, none of their decision makers has seen a demo, nobody from procurement met.
Over the next five weeks most of that stopped being true: finance approved a budget, the VP Operations offered to sponsor
it, a competitor appeared, the CFO and the VP sat through a demo. MemoryLake rewrites a "not yet" when a later call says
the same thing plainly ("we are also talking to Quaystride"); when the change comes as a thank-you ("thanks for joining
the demo today, Priya and Dev") the old "none of them has seen a demo" stays, dated the first day, as the current answer.

Three copies of the same four calls: A the calls only · B plus the deal log the app writes from its CRM activity (one
dated fact per event) · C plus the "not yet" facts each event has ended, retired by the app (the one that is still true
stays). The sales assistant (MemoryRouter: an ordinary model call with `--boundary`) then answers six questions.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 60 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-nym"  # memorylake-usecases / none-yet-memory


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
RUNS = 3              # answers per question and copy
MAX_TOKENS = 90
COPIES = ("talk", "logged", "retired")
COPY_LABEL = {"talk": "A  calls only", "logged": "B  + deal log", "retired": "C  + not-yets retired"}
COPY_SHORT = {"talk": "A", "logged": "B", "retired": "C"}
MARK = {"ok": "✓", "stale": "✗", "part": "◐", "other": "?", "made": "✎"}
GRADE_NAME = {"ok": "right", "stale": "stale “not yet”", "part": "half", "other": "other", "made": "made up"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "deal.json").read_text(encoding="utf-8"))


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


def call_at(c: dict) -> datetime:
    return datetime.fromisoformat(c["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def topic(d: dict, tid: str) -> dict:
    return next(t for t in d["topics"] if t["id"] == tid)


# What a fact says now: the text before a revision's "(as of …, previously …)" tail.
TAIL = re.compile(r"\((?:as of|on) \d{4}-\d\d-\d\d|\bpreviously\b|（截至|此前", re.I)
ASOF = re.compile(r"(?:as of|on|截至)\s*(\d{4}-\d\d-\d\d)")


def body_of(fact: str) -> str:
    m = TAIL.search(fact)
    return fact[:m.start()] if m else fact


def asof_of(fact: str) -> date | None:
    m = ASOF.search(fact)
    return date.fromisoformat(m.group(1)) if m else None


def is_log(fact: str) -> bool:
    return fact.startswith("Deal log ")


def says_not_yet(t: dict, fact: str) -> bool:
    return not is_log(fact) and bool(re.search(t["neg"], body_of(fact), re.I))


def says_event(t: dict, fact: str) -> bool:
    return bool(re.search(t["pos"], body_of(fact), re.I)) and not says_not_yet(t, fact)


def grade(t: dict, reply: str) -> str:
    """✓ the current answer · ✗ the stale "not yet" · ◐ half of a two-name answer · ? anything else · ✎ (the still-true
    question) somebody made up."""
    r = " ".join(reply.split())
    if re.search(t["ok"], r, re.I):
        return "ok"
    if t.get("part") and re.search(t["part"], r):
        return "part"
    if re.search(t["stale"], r, re.I):
        return "stale"
    return "made" if t["id"] == "procurement" else "other"


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


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    for n, c in enumerate(COPIES):
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['customer']} account ({COPY_LABEL[c][3:]})",
                                       "--custom-id", cid(c), "--description",
                                       f"{d['org']} account memory for {d['customer']} ({d['deal']}), {COPY_LABEL[c][3:]}. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three copies of the same account memory: " + " · ".join(COPY_LABEL[c] for c in COPIES))
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}. Demo data.", echo=n == 0)
    note(f"{len(d['people'])} people as actors: " + " · ".join(label(p) for p in d["people"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Sales assistant ({COPY_LABEL[c][3:]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per copy — what the sales assistant reads through MemoryRouter")
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


# --------------------------------------------------------------------------- step 3: the meetings

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





# --------------------------------------------------------------------------- step 3: the calls

def show_calls(d: dict) -> None:
    for c in d["calls"]:
        emit("text", f"\n    {c['id']}  {call_at(c):%a %d %b %Y}  “{c['name']}”")
        for who, line in c["lines"]:
            emit("text", wrap(f"{person(d, who)['display']}: {line}", "        ", 108))
    emit("text", "\n    where each item stands today, and the call that changed it:")
    for t in d["topics"]:
        if t["said"]:
            emit("text", f"      · {t['what']:<16} 09-01 “{t['said']}” → today: {t['truth']}")
    emit("calls", "", calls=d["calls"], topics=[dict(id=t["id"], what=t["what"], said=t["said"], truth=t["truth"])
                                                 for t in d["topics"]])


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


def ingest(cli: CLI, world: dict) -> dict:
    """Each copy gets the four calls one after another: a call is appended only when the one before it is extracted, so
    a later call can revise what an earlier one left in memory (appended at once, they are extracted in any order)."""
    d = load_data()
    show_calls(d)
    emit("text", "\n    Every copy gets the same four calls: one GROUP conversation each, every line as its speaker, at the "
                 "call's time; the next call goes in when the last one is extracted.")
    convs: dict = {}
    late: list = []
    t0 = time.time()

    def copy(c):
        first = c == COPIES[0]
        for n, call in enumerate(d["calls"]):
            conv = cli.run("conv", "create", "--project", world["projects"][c], "--actors", ",".join(world["actors"].values()),
                           "--kind", "GROUP", "--name", f"{d['customer']} · {call['name']} ({COPY_LABEL[c][3:]})",
                           "--custom-id", cid("conv", c, call["id"]), scoped=True, echo=first and n == 0)
            t = call_at(call)
            for i, (who, line) in enumerate(call["lines"]):
                append(cli, conv["id"], world["actors"][who], f"m{i:02d}", utc(t + timedelta(minutes=i)),
                       f"{label(person(d, who))}: {line}", echo=first and n == 0 and i == 0)
            convs[(c, call["id"])] = conv["id"]
            if not cooked(cli, conv["id"], echo=first and n == 0):
                late.append(f"{COPY_SHORT[c]} {call['id']}")
            emit("progress", f"    {COPY_SHORT[c]}: {call['id']} extracted · {int(time.time() - t0)} s", copy=c, call=call["id"],
                 done=sum(1 for k in convs if k[0] == c), total=len(d["calls"]))
    with ThreadPoolExecutor(len(COPIES)) as pool:
        list(pool.map(copy, COPIES))
    if late:
        note(f"still extracting after 300 s: {', '.join(late)} — graded as they stand")
    note(f"{len(d['calls'])} calls × {len(COPIES)} copies = {len(convs)} GROUP conversations, extracted in "
         f"{int(time.time() - t0)} s")
    world["convs"] = {f"{k[0]}:{k[1]}": v for k, v in convs.items()}
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`
    return convs


# --------------------------------------------------------------------------- step 4: what the app writes (B, C)

def write_log(cli: CLI, world: dict) -> dict:
    """B and C: the deal log, one dated fact per event, as the app records it from its CRM activity."""
    d = load_data()
    logged = [t for t in d["topics"] if t.get("log")]
    out: dict = {}
    for n, c in enumerate(c for c in COPIES if c != "talk"):
        res = cli.run("fact", "add", *[t["log"]["text"] for t in logged], "--project", world["projects"][c], scoped=True,
                      echo=n == 0)
        facts = (res or {}).get("facts") or []
        if len(facts) != len(logged):
            die(f"`fact add` returned {len(facts)} facts for {len(logged)} texts")
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda ft: cli.run("fact", "update", ft[0]["id"], "--project", world["projects"][c], "--metadata",
                                             json.dumps(dict(source="deal_log", topic=ft[1]["id"], on=ft[1]["log"]["on"])),
                                             scoped=True, echo=n == 0 and ft[0] is facts[0]),
                          zip(facts, logged)))
        out[c] = [f["id"] for f in facts]
    for t in logged:
        emit("text", wrap(t["log"]["text"], "      ", 110))
    note(f"B and C: {len(logged)} deal-log facts each (`fact add` keeps the text as given; topic and date go into metadata)")
    emit("deal_log", "", entries=[dict(topic=t["id"], on=t["log"]["on"], body=t["log"]["text"]) for t in logged])
    return out


def retire(cli: CLI, world: dict) -> list[dict]:
    """C: every "not yet" that a logged event has ended goes. A fact is retired when it says "not yet" about the topic,
    is dated before the event, and is not itself a revision that already says what changed. The still-true one
    (procurement: no event) stays."""
    d = load_data()
    p = world["projects"]["retired"]
    gone = []
    for f in project_facts(cli, p, echo=True):
        text = f.get("fact") or ""
        for t in d["topics"]:
            if not t.get("log") or not says_not_yet(t, text):
                continue
            when = asof_of(text)
            if when and when >= date.fromisoformat(t["log"]["on"]):
                continue
            cli.run("fact", "delete", f["id"], "--project", p, scoped=True, echo=not gone)
            gone.append(dict(id=f["id"], topic=t["id"], fact=text, ended=t["log"]["on"]))
            break
    for g in gone:
        emit("text", wrap(f"retired ({g['topic']}, ended {g['ended']}): {g['fact']}", "      ", 110))
    kept = [f.get("fact") or "" for f in project_facts(cli, p) if says_not_yet(topic(d, "procurement"), f.get("fact") or "")]
    note(f"C: retired {len(gone)} “not yet” fact(s) that a logged event ended; "
         + (f"kept the one that is still true: “{kept[0]}”" if kept else "no procurement fact was extracted in C this run"))
    emit("retired", "", gone=gone, kept=kept)
    time.sleep(10)
    return gone


# --------------------------------------------------------------------------- step 5: what memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per copy and topic: the facts that still say "not yet", and the facts that record what happened."""
    d = load_data()
    out: dict = {}
    for n, c in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][c], echo=echo and n == 0)]
        per = {}
        for t in d["topics"]:
            if t["id"] == "yard":
                hits = [f for f in facts if re.search(t["ok"], f, re.I)]
                per[t["id"]] = dict(not_yet=[], event=hits[:2] or [f for f in facts if re.search(r"yard|堆场", f, re.I)][:1])
                continue
            per[t["id"]] = dict(not_yet=[f for f in facts if says_not_yet(t, f)],
                                event=[f for f in facts if t["id"] != "procurement" and says_event(t, f)])
        out[c] = dict(facts=len(facts), items=per)
    # The champion's own words also land on Priya's actor, which the assistant's boundary does not read.
    pr = world["actors"].get("ops")
    out["priya_actor"] = [f.get("fact") or "" for f in actor_facts(cli, pr)] if pr else []
    return out


def show_census(cen: dict) -> None:
    d = load_data()
    for t in d["topics"]:
        if t["id"] == "yard":
            continue
        emit("text", f"\n    {t['what']} — 09-01 “{t['said']}” → today: {t['truth']}")
        for c in COPIES:
            r = cen[c]["items"][t["id"]]
            shown = 0
            for f in r["not_yet"][:2]:
                emit("text", wrap(("✗ still says " if t["id"] != "procurement" else "✓ still true ") + f, f"      {COPY_SHORT[c]}  ", 118))
                shown += 1
            for f in r["event"][:2 if c == "talk" else 1]:
                emit("text", wrap("· " + f, f"      {COPY_SHORT[c]}  ", 118))
                shown += 1
            if not shown:
                emit("text", f"      {COPY_SHORT[c]}  · nothing about it")
    y = topic(d, "yard")
    emit("text", f"\n    {y['what']} — {y['truth']}")
    for c in COPIES:
        hit = cen[c]["items"]["yard"]["event"]
        emit("text", wrap(("· " + hit[0]) if hit else "· not remembered", f"      {COPY_SHORT[c]}  ", 118))
    pa = [f for f in cen.get("priya_actor", []) if re.search(r"sponsor|champion|steering", f, re.I)]
    if pa:
        emit("text", "\n    on Priya Raman's actor (outside the account project the assistant reads):")
        for f in pa[:2]:
            emit("text", wrap(f, "      · ", 112))
    emit("text", "")
    for c in COPIES:
        it = cen[c]["items"]
        stale = sum(1 for t in d["topics"] if t.get("log") and it[t["id"]]["not_yet"])
        missing = sum(1 for t in d["topics"] if t.get("log") and not it[t["id"]]["event"])
        emit("text", f"    {COPY_LABEL[c]:<26} {cen[c]['facts']:>2} facts · ended “not yet” still in memory {stale}/4 · "
                     f"event not recorded {missing}/4 · procurement “not yet” {'kept' if it['procurement']['not_yet'] else 'absent'}")
    emit("census", "", copies={c: cen[c] for c in COPIES}, priya_actor=cen.get("priya_actor", []))


# --------------------------------------------------------------------------- step 6: ask the sales assistant

def system_prompt(d: dict) -> str:
    return d["system"].format(today=today(d).strftime("%A %Y-%m-%d"))


def ask_rows(cli: CLI, world: dict, c: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    jobs = [(t, i) for t in d["topics"] for i in range(RUNS)]
    for n, t in enumerate(d["topics"]):
        baseline(cli, t["ask"], system, echo=echo_first and n == 0)

    def one(job):
        t, i = job
        r = router(cli, t["ask"], system, world["boundaries"][c], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=t["id"], copy=c, run=i + 1, verdict=None, empty=False, reply="")
        empty = r["prompt_tokens"] <= BASE[(t["ask"], system)]
        return dict(item=t["id"], copy=c, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    verdict=grade(t, r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for t in d["topics"]:
        mine = [r for r in rows if r["item"] == t["id"]]
        marks = " ".join("∅" if r.get("empty") else MARK.get(r["verdict"], "–") for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {t['what']:<20} {marks:<8} " + (first[:83] + "…" if len(first) > 84 else first))
        emit("ask_row", "", copy=c, item=t["id"], rows=mine, truth=t["truth"])
    show_example(rows, c)
    return rows


def show_example(rows: list[dict], c: str) -> None:
    """One reply in full: the first stale "not yet" in this copy, else the demo question."""
    d = load_data()
    r = next((r for r in rows if r.get("verdict") == "stale" and not r.get("empty")), None) or \
        next((r for r in rows if r["item"] == "demo" and r.get("reply") and not r.get("empty")), None)
    if not r:
        return
    t = topic(d, r["item"])
    emit("text", f"\n    Mara: “{t['ask']}”   (today: {t['truth']})")
    emit("text", wrap(r["reply"], f"    {MARK[r['verdict']]} | ", 104))
    emit("example", "", copy=c, row=r, ask=t["ask"], truth=t["truth"])


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("verdict")]
    k = {v: sum(1 for r in live if not r.get("empty") and r["verdict"] == v) for v in MARK}
    return dict(k, calls=len(live), empty=sum(1 for r in live if r.get("empty")))


def score(cen: dict, rows: list[dict]) -> dict:
    d = load_data()
    out: dict = {"answers": {}, "by_item": {}, "memory": {}}
    emit("text", f"    answers right, per question ({RUNS} per copy):")
    emit("text", f"      {'':<30} " + "  ".join(f"{COPY_SHORT[c]:<9}" for c in COPIES))
    for t in d["topics"]:
        cells = {}
        for c in COPIES:
            mine = [r for r in rows if r["copy"] == c and r["item"] == t["id"] and r.get("verdict")]
            live = [r for r in mine if not r.get("empty")]
            cells[c] = dict(ok=sum(1 for r in live if r["verdict"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            stale=sum(1 for r in live if r["verdict"] == "stale"))
        out["by_item"][t["id"]] = cells
        cell = lambda x: f"{x['ok']}/{x['n']}" + (f" ✗{x['stale']}" if x["stale"] else "") + (f" ∅{x['empty']}" if x["empty"] else "")  # noqa: E731
        tag = {"procurement": "  (still true: “nobody yet” is right)", "yard": "  (control)"}.get(t["id"], "")
        emit("text", f"      {(t['ask'][:28] + '…') if len(t['ask']) > 29 else t['ask']:<30} "
                     + "  ".join(f"{cell(cells[c]):<9}" for c in COPIES) + tag)
    emit("text", "")
    for c in COPIES:
        t = tally([r for r in rows if r["copy"] == c])
        it = cen[c]["items"]
        stale = sum(1 for x in d["topics"] if x.get("log") and it[x["id"]]["not_yet"])
        out["answers"][c] = t
        out["memory"][c] = dict(stale=stale)
        emit("text", f"    {COPY_LABEL[c]:<26} right {t['ok']:>2}/{t['calls'] - t['empty']} · stale “not yet” {t['stale']:>2} · "
                     f"half {t['part']} · other {t['other'] + t['made']}" + (f" · ∅ {t['empty']}" if t["empty"] else "")
                     + f"   (ended “not yet” in memory: {stale}/4)")
    emit("text", "\n    ✓ today's answer · ✗ the 09-01 “not yet” given as today's answer · ◐ one of the two people · ✎ someone "
                 "named who was never met · ? other · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call)")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — the {d['customer']} account, three copies")
    world = setup(cli)

    banner(3, TOTAL, "Four calls, five weeks")
    ingest(cli, world)

    banner(4, TOTAL, "What the app writes itself — B: the deal log · C: and retires what it ended")
    write_log(cli, world)
    gone = retire(cli, world)

    banner(5, TOTAL, "What memory says about each item, today")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the sales assistant — through each copy's boundary")
    for c in COPIES:
        emit("text", f"\n  {COPY_LABEL[c]}")
        mine = ask_rows(cli, world, c, echo_first=c == COPIES[0])
        emit("arm_done", "", copy=c, tally=tally(mine))
        rows += mine

    banner(7, TOTAL, "The score")
    sc = score(cen, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census={c: cen[c] for c in COPIES}, priya_actor=cen.get("priya_actor"), retired=gone, answers=rows,
                   score=sc, tokens=tok, seconds=int(time.time() - t0), router=cli.router_ok,  # type: ignore[attr-defined]
                   today=today(d).isoformat())
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """What each copy remembers about each item, today."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("memory_view", "", copies={c: cen[c] for c in COPIES})
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each copy; graded when it is one of the six."""
    d = load_data()
    world = load_world(cli)
    t = next((t for t in d["topics"] if t["ask"].lower() == question.strip().lower()), None)
    system = system_prompt(d)
    rows = []
    base = baseline(cli, question, system, echo=True)
    for n, c in enumerate(COPIES):
        r = router(cli, question, system, world["boundaries"].get(c), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = grade(t, r["reply"]) if t else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {COPY_LABEL[c]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(copy=c, reply=r["reply"], empty=empty, verdict=g))
    if t:
        emit("text", f"\n  today's answer: {t['truth']}")
    emit("asked", "", question=question, item=t and t["id"], rows=rows, truth=t and t["truth"])
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
    for p in OUT.glob("*.json") if OUT.exists() else []:
        p.unlink()



EXAMPLE = "Which of Brackwell Freight's decision makers have seen a demo?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = what each copy remembers about each item; "
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
        banner(2, 2, "What each copy remembers about each item")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "{EXAMPLE}")')
        banner(2, 2, "One question to the sales assistant, through A, B and C")
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


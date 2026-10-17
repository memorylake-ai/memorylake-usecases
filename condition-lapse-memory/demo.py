#!/usr/bin/env python3
"""condition-lapse-memory — the condition nobody mentioned again.

Halcrest (a fictional company) runs a weekly operations meeting. Many of its decisions carry a condition: "we open
Lisbon on November 2, provided the lease is signed by October 1"; "the January price increase goes ahead only if Q3
churn stays under 3 percent". MemoryLake keeps the condition with the decision. When a later meeting says how the
condition came out ("Q3 churn closed at 4.1 percent"), the operations assistant connects the two. When nobody ever
mentions it again (the lease, the migration test), the deadline passes in silence, and the assistant answers that the
plan stands, sometimes adding that the condition was met, which no record says.

Three arms over the same four meetings: A the meetings as MemoryLake extracted them · B the same memory, with a system
prompt that tells the model how to treat conditions · C the same extracted facts, plus one "condition check" fact per
conditional decision that the app computes in code from its own decision register and event log (met / failed /
lapsed / still open). The assistant (MemoryRouter: an ordinary model call with `--boundary`) answers eight questions.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 80 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-clm"  # memorylake-usecases / condition-lapse-memory


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
MAX_TOKENS = 90
COPIES = ("notes", "checked")   # the two projects
COPY_NAME = {"notes": "meeting notes", "checked": "meeting notes + condition checks"}
ARMS = ("A", "B", "C")
ARM_LABEL = {"A": "A  meetings as remembered", "B": "B  + careful system prompt", "C": "C  + app condition checks"}
ARM_COPY = {"A": "notes", "B": "notes", "C": "checked"}
MARK = {"ok": "✓", "stands": "✗", "made": "✎", "early": "≫", "other": "?"}
GRADE_NAME = {"ok": "right", "stands": "the plan given as standing", "made": "says the condition was met (no record of it)",
              "early": "decided before the deadline", "other": "other"}


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
    return json.loads((DATA / "meetings.json").read_text(encoding="utf-8"))


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


def meeting_at(m: dict) -> datetime:
    return datetime.fromisoformat(m["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decision(d: dict, did: str) -> dict:
    return next(x for x in d["decisions"] if x["id"] == did)


TRUTH_WORD = {"Y": "Yes", "N": "No", "U": "Unknown", "NU": "No or Unknown"}
LEAD = re.compile(r"^\W*(yes|no|unknown)\b", re.I)


def lead_word(reply: str) -> str | None:
    m = LEAD.match(reply or "")
    return m.group(1)[0].upper() if m else None


def grade(x: dict, reply: str) -> str:
    """✓ the first word is in the truth set · ✎ "Yes" on a lapsed condition, and the reply says the condition was met
    (memory has no record of it) · ✗ "Yes" on a lapsed / failed condition, or "No" on a standing plan · ≫ "Yes" or "No"
    on a condition whose deadline is still ahead · ? no Yes / No / Unknown up front."""
    w = lead_word(reply)
    r = " ".join(reply.split())
    if not w:
        return "other"
    if w in x["truth"]:
        return "ok"
    if x["kind"] == "pending":
        return "early"
    if w == "Y" and x.get("made") and re.search(x["made"], r, re.I) and not re.search(r"\bnot (been )?signed|has not|hasn't|wasn't|no record", r, re.I):
        return "made"
    return "stands"


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    for n, c in enumerate(COPIES):
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} ops meetings ({COPY_NAME[c]})",
                                       "--custom-id", cid(c), "--description",
                                       f"{d['org']} weekly operations meetings: {COPY_NAME[c]}. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("two projects: “notes” gets the meetings; “checked” gets the same facts word for word, plus the app's condition checks")
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}. Demo data.", echo=n == 0)
    note(f"{len(d['people'])} people as actors: " + " · ".join(label(p) for p in d["people"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Ops assistant ({COPY_NAME[c]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per project — what the operations assistant reads through MemoryRouter (A and B share “notes”)")
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


# --------------------------------------------------------------------------- step 3: the meetings

def show_meetings(d: dict) -> None:
    for m in d["meetings"]:
        emit("text", f"\n    {m['id']}  {meeting_at(m):%a %d %b %Y}  “{m['name']}”")
        for who, line in m["lines"]:
            emit("text", wrap(f"{person(d, who)['display']}: {line}", "        ", 108))
    emit("text", f"\n    where each decision stands today ({today(d):%d %b}):")
    for x in d["decisions"]:
        emit("text", f"      · {x['what']:<26} {x['kind']:<8} → {TRUTH_WORD[x['truth']]}")
    emit("meetings", "", meetings=d["meetings"], decisions=[dict(id=x["id"], what=x["what"], kind=x["kind"],
                                                                  condition=x["condition"], deadline=x["deadline"],
                                                                  truth=TRUTH_WORD[x["truth"]]) for x in d["decisions"]])


def ingest(cli: CLI, world: dict) -> None:
    """The four meetings go into "notes" one after another: a meeting is appended only when the one before it is
    extracted, so a later meeting can revise what an earlier one left in memory."""
    d = load_data()
    show_meetings(d)
    emit("text", "\n    Each meeting is one GROUP conversation in “notes”, every line as its speaker, at the meeting's time; "
                 "the next one goes in when the last one is extracted.")
    t0 = time.time()
    late = []
    for n, m in enumerate(d["meetings"]):
        conv = cli.run("conv", "create", "--project", world["projects"]["notes"], "--actors", ",".join(world["actors"].values()),
                       "--kind", "GROUP", "--name", f"{d['org']} · {m['name']}", "--custom-id", cid("conv", m["id"]),
                       scoped=True, echo=n == 0)
        t = meeting_at(m)
        for i, (who, line) in enumerate(m["lines"]):
            append(cli, conv["id"], world["actors"][who], f"m{i:02d}", utc(t + timedelta(minutes=i)),
                   f"{label(person(d, who))}: {line}", echo=n == 0 and i == 0)
        if not cooked(cli, conv["id"], echo=n == 0):
            late.append(m["id"])
        emit("progress", f"    {m['id']} extracted · {int(time.time() - t0)} s", meeting=m["id"], done=n + 1, total=len(d["meetings"]))
    if late:
        note(f"still extracting after 300 s: {', '.join(late)} — graded as they stand")
    note(f"{len(d['meetings'])} meetings extracted in {int(time.time() - t0)} s")


# --------------------------------------------------------------------------- step 4: what the app writes (C)

def check_conditions(d: dict) -> list[dict]:
    """The app's condition check, in code: each conditional decision in its register against its own event log.
    A logged event decides it (met / failed, on that day); no event and the deadline has passed → lapsed; no event and
    the deadline is ahead → still open. Nothing here reads memory or asks a model."""
    events = {e["id"]: e for e in d["events"]}
    out = []
    for x in d["decisions"]:
        if not x["condition"]:
            continue
        e = events.get(x["event"]) if x["event"] else None
        decided = next(m for m in d["meetings"] if m["id"] == x["said"])["at"][:10]
        head = f"Condition check {d['today']} · {x['decision']} (decided {decided}, {x['condition']}):"
        if e and e["result"] == "met":
            status, tail = "MET", f"condition MET on {e['on']} ({e['summary']}). The decision stands."
        elif e:
            status, tail = "FAILED", f"condition FAILED on {e['on']} ({e['summary']}). The decision no longer stands."
        elif x["deadline"] and date.fromisoformat(x["deadline"]) < today(d):
            status, tail = "LAPSED", (f"condition LAPSED: the {date.fromisoformat(x['deadline']):%B} {date.fromisoformat(x['deadline']).day} "
                                      "deadline passed and the app has no record that it was met. The decision is not confirmed.")
        else:
            status, tail = "OPEN", (f"condition still OPEN: the deadline is {x['deadline']} and nothing is recorded yet. "
                                    "Whether it goes ahead is not decided.")
        out.append(dict(decision=x["id"], status=status, text=f"{head} {tail}"))
    return out


def write_checked(cli: CLI, world: dict) -> dict:
    """C: every fact extracted into "notes", copied word for word into "checked", then one condition-check fact per
    conditional decision. The only difference between A and C is the checks."""
    d = load_data()
    src = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["notes"], echo=True)]
    src = [s for s in src if s]
    p = world["projects"]["checked"]
    if src:
        res = cli.run("fact", "add", *src, "--project", p, scoped=True)
        if len((res or {}).get("facts") or []) != len(src):
            die(f"`fact add` returned {len((res or {}).get('facts') or [])} facts for {len(src)} texts")
    note(f"C: the {len(src)} facts MemoryLake extracted into “notes”, copied word for word")
    checks = check_conditions(d)
    res = cli.run("fact", "add", *[c["text"] for c in checks], "--project", p, scoped=True)
    for c in checks:
        emit("text", wrap(f"[{c['status']}] {c['text']}", "      ", 112))
    note(f"C: {len(checks)} condition checks, computed by the app from its decision register and event log "
         f"({', '.join(e['source'] for e in d['events'])}) — not by a model")
    emit("checks", "", copied=len(src), checks=checks, events=d["events"])
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`
    return dict(copied=len(src), checks=checks)


# --------------------------------------------------------------------------- step 5: what memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per decision: the facts in "notes" that are about it, and whether any of them records how the condition came out."""
    d = load_data()
    facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["notes"], echo=echo)]
    per = {}
    for x in d["decisions"]:
        about = [f for f in facts if re.search(x["topic"], f, re.I)]
        settled = [f for f in about if re.search(x["outcome"], f, re.I)]
        per[x["id"]] = dict(about=about, settled=settled,
                            kept_condition=any(re.search(r"\b(unless|provided|only if|if|as long as)\b|除非|前提|如果", f, re.I) for f in about))
    return dict(facts=len(facts), all=facts, items=per)


def show_census(cen: dict) -> None:
    d = load_data()
    for x in d["decisions"]:
        r = cen["items"][x["id"]]
        cond = f" — {x['condition']}" if x["condition"] else ""
        emit("text", f"\n    {x['what']}{cond}   [{x['kind']} → {TRUTH_WORD[x['truth']]}]")
        for f in r["about"][:3]:
            emit("text", wrap(f, "      · ", 116))
        if not r["about"]:
            emit("text", "      · nothing about it in memory")
        if x["kind"] in ("failed", "met"):
            emit("text", "      → outcome in memory: " + ("yes" if r["settled"] else "NO — the meeting that settled it was not remembered"))
        elif x["kind"] == "lapsed":
            emit("text", "      → outcome in memory: none (nobody mentioned it again; the deadline passed "
                         + ("— yet a fact says it happened" if r["settled"] else "in silence") + ")")
        elif x["kind"] == "pending":
            emit("text", "      → outcome in memory: none yet (deadline still ahead)")
    lost = [x["what"] for x in d["decisions"] if not cen["items"][x["id"]]["about"]]
    emit("text", f"\n    “notes”: {cen['facts']} facts · decisions with no fact at all: {len(lost)}/{len(d['decisions'])}"
                 + (f" ({', '.join(lost)})" if lost else "")
                 + f" · conditions kept with the decision: {sum(1 for x in d['decisions'] if x['condition'] and cen['items'][x['id']]['kept_condition'])}"
                 f"/{sum(1 for x in d['decisions'] if x['condition'])}")
    emit("census", "", **cen)


# --------------------------------------------------------------------------- step 6: ask the operations assistant

def system_prompt(d: dict, arm: str) -> str:
    s = d["system"].format(today=today(d).strftime("%A %Y-%m-%d"))
    return s + " " + d["careful"] if arm == "B" else s


def ask_rows(cli: CLI, world: dict, arm: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d, arm)
    jobs = [(x, i) for x in d["decisions"] for i in range(RUNS)]
    for n, x in enumerate(d["decisions"]):
        baseline(cli, x["ask"], system, echo=echo_first and n == 0)
    if arm == "B":
        emit("text", wrap("system prompt adds: " + d["careful"], "    ", 112))

    def one(job):
        x, i = job
        r = router(cli, x["ask"], system, world["boundaries"][ARM_COPY[arm]], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=x["id"], arm=arm, run=i + 1, verdict=None, empty=False, reply="")
        empty = r["prompt_tokens"] <= BASE[(x["ask"], system)]
        return dict(item=x["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    verdict=grade(x, r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for x in d["decisions"]:
        mine = [r for r in rows if r["item"] == x["id"]]
        marks = " ".join("∅" if r.get("empty") else MARK.get(r["verdict"], "–") for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {x['what']:<26} {marks:<8} " + (first[:78] + "…" if len(first) > 79 else first))
        emit("ask_row", "", arm=arm, item=x["id"], rows=mine, truth=TRUTH_WORD[x["truth"]])
    show_example(rows, arm)
    return rows


def show_example(rows: list[dict], arm: str) -> None:
    """One reply in full: the first ✎ in this arm, else the first ✗, else the Lisbon question."""
    d = load_data()
    live = [r for r in rows if r.get("reply") and not r.get("empty")]
    r = next((r for r in live if r["verdict"] == "made"), None) or next((r for r in live if r["verdict"] == "stands"), None) \
        or next((r for r in live if r["item"] == "lisbon"), None)
    if not r:
        return
    x = decision(d, r["item"])
    emit("text", f"\n    “{x['ask']}”   (today: {TRUTH_WORD[x['truth']]}{' — ' + x['condition'] if x['condition'] else ''})")
    emit("text", wrap(r["reply"], f"    {MARK[r['verdict']]} | ", 104))
    emit("example", "", arm=arm, row=r, ask=x["ask"], truth=TRUTH_WORD[x["truth"]], condition=x["condition"])


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("verdict")]
    k = {v: sum(1 for r in live if not r.get("empty") and r["verdict"] == v) for v in MARK}
    return dict(k, calls=len(live), empty=sum(1 for r in live if r.get("empty")))


def score(cen: dict, rows: list[dict]) -> dict:
    d = load_data()
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    answers right, per question ({RUNS} per arm; ∅ = MemoryRouter attached nothing, not graded):")
    emit("text", f"      {'':<28} " + "  ".join(f"{a:<10}" for a in ARMS))
    for x in d["decisions"]:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == x["id"] and r.get("verdict")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["verdict"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            made=sum(1 for r in live if r["verdict"] == "made"))
        out["by_item"][x["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']}" + (f" ✎{c['made']}" if c["made"] else "") + (f" ∅{c['empty']}" if c["empty"] else "")  # noqa: E731
        tag = {"lapsed": "  (lapsed in silence)", "pending": "  (deadline ahead)", "plain": "  (no condition)", "met": "  (condition met)"}.get(x["kind"], "")
        emit("text", f"      {x['what']:<28} " + "  ".join(f"{cell(cells[a]):<10}" for a in ARMS) + tag)
    emit("text", "")
    for a in ARMS:
        t = tally([r for r in rows if r["arm"] == a])
        out["answers"][a] = t
        emit("text", f"    {ARM_LABEL[a]:<28} right {t['ok']:>2}/{t['calls'] - t['empty']} · plan given as standing {t['stands']:>2} · "
                     f"condition said to be met {t['made']} · decided early {t['early']} · other {t['other']}"
                     + (f" · ∅ {t['empty']}" if t["empty"] else ""))
    emit("text", "\n    ✓ right · ✗ the plan given as standing (or a standing plan called off) · ✎ “Yes” and the reply says the "
                 "lapsed condition was met — no record says so · ≫ Yes / No before the deadline · ? no Yes / No / Unknown · "
                 "∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call)")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s meeting memory, two projects")
    world = setup(cli)

    banner(3, TOTAL, "Four operations meetings, eight decisions")
    ingest(cli, world)

    banner(4, TOTAL, "What the app writes itself — C: the same facts, plus a condition check per decision")
    chk = write_checked(cli, world)

    banner(5, TOTAL, "What memory says about each decision, today")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the operations assistant — A, B and C")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == ARMS[0])
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine

    banner(7, TOTAL, "The score")
    sc = score(cen, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cen, checks=chk, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok, today=today(d).isoformat())  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every fact, answer and grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """What "notes" remembers about each decision, and the condition checks in "checked"."""
    d = load_data()
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    checks = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["checked"])
              if (f.get("fact") or "").startswith("Condition check ")]
    emit("text", "\n    condition checks in “checked”:")
    for c in checks:
        emit("text", wrap(c, "      · ", 116))
    emit("memory_view", "", census=cen, checks=checks)
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each arm; graded when it is one of the eight."""
    d = load_data()
    world = load_world(cli)
    x = next((x for x in d["decisions"] if x["ask"].lower() == question.strip().lower()), None)
    rows = []
    for n, a in enumerate(ARMS):
        system = system_prompt(d, a)
        base = baseline(cli, question, system, echo=n == 0)
        r = router(cli, question, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = grade(x, r["reply"]) if x else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, verdict=g))
    if x:
        emit("text", f"\n  today's answer: {TRUTH_WORD[x['truth']]}" + (f" ({x['condition']}; {x['kind']})" if x["condition"] else ""))
    emit("asked", "", question=question, item=x and x["id"], rows=rows, truth=x and TRUTH_WORD[x["truth"]])
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


EXAMPLE = "Is the Lisbon office opening on November 2?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = what memory holds about each decision; "
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
        banner(2, 2, "What memory holds about each decision")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "{EXAMPLE}")')
        banner(2, 2, "One question to the operations assistant, through A, B and C")
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

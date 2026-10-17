#!/usr/bin/env python3
"""wrap-up-call-memory — some client calls never make it into memory at all; the figures that do often lose the client.

Brightwell Studio (a fictional agency) holds a wrap-up call at the end of every client campaign, and the case
studies the new-business team writes come from what those calls left in memory. Six calls, four figures each, said
the same way every time ("Cost per lead came in at 18.40 dollars."). MemoryLake extracts most calls whole — and,
now and then, a whole call leaves nothing behind, with nothing to show for it. Of the figures that are kept, many
come back without the client's name ("Return on ad spend was 5.1"), so a question about one client can pull up
another client's number.

The app can check this itself: `conv fact-actions` is each conversation's receipt. For every call, the audit compares
the figures said on the call with the facts the receipt lists, and pins the lines that are missing or unnamed — the
sentence as it was said, with the client, the campaign, the date and the speaker. Two memories, read through
MemoryRouter: A the calls as extracted · F A's facts copied word for word, plus the pinned lines. Twelve figure
questions, three answers each.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 85 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-wcm"  # memorylake-usecases / wrap-up-call-memory


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


# --------------------------------------------------------------------------- settings

MODEL = "gpt-4o-mini"
TEMPERATURE = "0.7"   # an assistant's usual setting: the same question gets a slightly different reply each time
RUNS = 3              # answers per question and arm
MAX_TOKENS = 90
COPIES = ("as_said", "audited")   # the two projects
COPY_NAME = {"as_said": "the calls as extracted", "audited": "as extracted + the audit's pinned lines"}
ARMS = ("A", "F")
ARM_LABEL = {"A": "A  as extracted", "F": "F  + the audit's pinned lines"}
ARM_COPY = {"A": "as_said", "F": "audited"}
ARM_OF = {c: a for a, c in ARM_COPY.items()}
MARK = {"ok": "✓", "swap": "⇄", "unknown": "?", "made": "✎", "other": "–"}
GRADE_NAME = {"ok": "the figure said on the call", "swap": "another client's figure from memory", "unknown": "Unknown",
              "made": "a figure nobody said", "other": "no figure and no Unknown"}


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
    return json.loads((DATA / "calls.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    """The studio's three people, and one client contact per call."""
    return [p["key"] for p in d["people"]] + [c["id"] for c in d["calls"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    sub = " " * (len(indent) - 2) + "| " if indent.endswith("| ") else " " * len(indent)
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=sub)


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def speaker(d: dict, call: dict, who: str) -> dict:
    if who == "client":
        return dict(key=call["id"], display=call["contact"], role=call["contact_role"])
    return person(d, who)


def label(p: dict) -> str:
    return f"{p['display']} ({p['role']})"


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def at(m: dict) -> datetime:
    return datetime.fromisoformat(m["at"].replace("Z", "+00:00"))


def utc(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def call_of(d: dict, call_id: str) -> dict:
    return next(c for c in d["calls"] if c["id"] == call_id)


def metric_of(call: dict, mid: str) -> dict:
    return next(m for m in call["metrics"] if m["id"] == mid)


def questions(d: dict) -> list[dict]:
    """Twelve figure questions: two per client, in the words a case-study writer would use."""
    out = []
    for c in d["calls"]:
        for mid in c["ask"]:
            m = metric_of(c, mid)
            out.append(dict(id=f"{c['id']}-{mid}", call=c["id"], metric=mid, what=f"{c['client']} · {m['what']}",
                            ask=f"What was the {m['what']} on the {c['client']} {c['campaign']}?", truth=m["value"]))
    return out


def pin_text(d: dict, call: dict, m: dict) -> str:
    """The line the audit pins: the sentence as it was said, with whose campaign, which call and who said it."""
    who, line = call["lines"][m["line"]]
    return f"{call['client']} {call['campaign']} wrap-up call ({at(call):%d %b %Y}), {speaker(d, call, who)['display']}: {line}"


# --------------------------------------------------------------------------- grading

MONTHS = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
DATES = re.compile(rf"\b{MONTHS}\.? \d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}(?:st|nd|rd|th)? {MONTHS}\b|\b20\d\d-\d\d-\d\d\b|\b20\d\d\b")
LEAD_UNKNOWN = re.compile(r"^\W*unknown\b", re.I)
NOT_KNOWN = re.compile(r"\bunknown\b|\bnot (?:been )?(?:remembered|recorded|reported|available|mentioned|specified|known|provided)\b|"
                       r"\bno (?:record|information|data|figure|details?)\b|\bdon'?t have\b|\bdo not have\b|\bnot sure\b", re.I)
NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")


def strip_dates(text: str) -> str:
    return DATES.sub(" ", text or "")


def borrowed(d: dict, q: dict, reply: str) -> list[str]:
    """Other clients' (or other metrics') figures in a reply: what memory held for someone else."""
    r = strip_dates(reply)
    out = []
    for c in d["calls"]:
        for m in c["metrics"]:
            if (c["id"], m["id"]) != (q["call"], q["metric"]) and re.search(m["re"], r, re.I):
                out.append(f"{c['client']} {m['what']} {m['value']}")
    return out


def grade(d: dict, q: dict, reply: str) -> str:
    """One grade per reply: ✓ the figure · ? Unknown · ⇄ another client's figure · ✎ a figure nobody said · – none of these."""
    m = metric_of(call_of(d, q["call"]), q["metric"])
    r = strip_dates(reply)
    if re.search(m["re"], r, re.I):
        return "ok"
    if LEAD_UNKNOWN.match(reply or ""):
        return "unknown"
    if borrowed(d, q, reply):
        return "swap"
    if NOT_KNOWN.search(reply or ""):
        return "unknown"
    if NUMBER.search(r):
        return "made"
    return "other"


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for both, and no arm letter in it: a project's name is context for extraction (with
        # " · A" appended, fewer of the calls were kept), so it must not differ between them or carry anything else.
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} client work",
                                       "--custom-id", cid(c), "--description", f"{d['org']} client work. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("two projects: “as_said” gets the six wrap-up calls; “audited” gets what “as_said” extracted, plus the lines "
         "the audit pins")
    for n, k in enumerate(actor_keys(d)):
        p = person(d, k) if k in [x["key"] for x in d["people"]] else speaker(d, call_of(d, k), "client")
        world["actors"][k] = make_actor(cli, k, p["display"], f"{p['role']}. Demo data.", echo=n == 0)
    note(f"{len(d['people'])} studio people and {len(d['calls'])} client contacts: "
         + " · ".join(p["display"] for p in d["people"]) + " · " + " · ".join(c["contact"] for c in d["calls"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Case-study assistant ({COPY_NAME[c]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per project — what the case-study assistant reads through MemoryRouter")
    emit("world", "", **world)
    return world


def demo_convs(cli: CLI) -> dict:
    return {c["custom_id"][len(cid("conv")) + 1:]: c["id"] for c in paged(cli, ["conv", "list", "--page-size", "100"])
            if (c.get("custom_id") or "").startswith(cid("conv") + "-")}


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
    world["convs"] = demo_convs(cli)
    return world


# --------------------------------------------------------------------------- step 3: the calls

def show_calls(d: dict) -> None:
    for c in d["calls"]:
        emit("text", f"\n    {at(c):%a %d %b %Y}  {c['client']} — {c['campaign']} wrap-up")
        for who, line in c["lines"]:
            emit("text", wrap(f"{speaker(d, c, who)['display']}: {line}", "        ", 112))
    emit("calls", "", calls=[dict(id=c["id"], client=c["client"], campaign=c["campaign"], at=c["at"],
                                  lines=[dict(who=speaker(d, c, w)["display"], body=l) for w, l in c["lines"]],
                                  metrics=[dict(id=m["id"], what=m["what"], value=m["value"], line=m["line"]) for m in c["metrics"]])
                             for c in d["calls"]])


def ingest_call(cli: CLI, world: dict, call: dict, echo: bool, t0: float) -> tuple[str, bool]:
    d = load_data()
    staff = [world["actors"][p["key"]] for p in d["people"]]
    conv = cli.run("conv", "create", "--project", world["projects"]["as_said"],
                   "--actors", ",".join(staff + [world["actors"][call["id"]]]), "--kind", "GROUP",
                   "--name", f"{call['client']} · {call['campaign']} wrap-up", "--custom-id", cid("conv", call["id"]),
                   scoped=True, echo=echo)
    for i, (who, line) in enumerate(call["lines"]):
        sp = speaker(d, call, who)
        append(cli, conv["id"], world["actors"][sp["key"]], f"m{i:02d}", utc(at(call) + timedelta(minutes=i)),
               f"{label(sp)}: {line}", echo=echo and i == 0)
    ok = cooked(cli, conv["id"], echo=echo, limit=420)
    emit("progress", f"    {call['client']}: extracted · {int(time.time() - t0)} s", call=call["id"])
    return conv["id"], ok


def ingest(cli: CLI, world: dict) -> None:
    d = load_data()
    show_calls(d)
    emit("text", "\n    Each call is one GROUP conversation in “as_said”, every line as its speaker at the call's time. "
                 "The six are sent the way calls arrive at a busy studio: side by side.")
    t0 = time.time()
    with ThreadPoolExecutor(6) as pool:
        res = list(pool.map(lambda c: ingest_call(cli, world, c, c is d["calls"][0], t0), d["calls"]))
    world["convs"] = {c["id"]: r[0] for c, r in zip(d["calls"], res)}
    late = [c["client"] for c, r in zip(d["calls"], res) if not r[1]]
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — audited as they stand")
    note(f"all six calls extracted in {int(time.time() - t0)} s")
    time.sleep(5)


# --------------------------------------------------------------------------- step 4: the audit

def receipt(cli: CLI, conv: str, project: str, echo: bool = False) -> list[dict]:
    """`conv fact-actions`: the facts this one conversation added or updated in the project, newest first."""
    items = paged(cli, ["conv", "fact-actions", conv, "--project", project, "--page-size", "100"], echo)
    out = []
    for it in items:
        act = (it.get("action") or it.get("event") or "").upper()
        text = it.get("fact") if isinstance(it.get("fact"), str) else (it.get("fact") or {}).get("fact") if isinstance(it.get("fact"), dict) else None
        text = text or it.get("new_fact") or it.get("text") or ""
        out.append(dict(action=act, fact=text, id=it.get("fact_id") or it.get("id")))
    return out


def audit(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per call: what its receipt says it left in memory, and for each figure said on the call — is it in one of those
    facts, and does that fact say whose campaign it was? Missing or unnamed → the line the app pins."""
    d = load_data()
    proj = world["projects"]["as_said"]
    out: dict = {"calls": {}, "pins": []}
    # MemoryRouter reads the project; a figure that landed on a person instead is reported, and still pinned.
    on_people = [f.get("fact") or "" for k in actor_keys(d) if world["actors"].get(k) for f in actor_facts(cli, world["actors"][k])]
    for n, c in enumerate(d["calls"]):
        conv = world["convs"].get(c["id"])
        rec = receipt(cli, conv, proj, echo=echo and n == 0) if conv else []
        facts = [r["fact"] for r in rec if r["action"] in ("ADD", "UPDATE", "") and r["fact"]]
        ms = {}
        for m in c["metrics"]:
            hits = [f for f in facts if re.search(m["re"], strip_dates(f), re.I)]
            named = [f for f in hits if re.search(c["name_re"], f)]
            state = "named" if named else "unnamed" if hits else "missing"
            person_hits = [f for f in on_people if re.search(m["re"], strip_dates(f), re.I)] if not hits else []
            ms[m["id"]] = dict(state=state, fact=(named or hits or [""])[0], on_person=(person_hits or [""])[0])
            if state != "named":
                out["pins"].append(dict(call=c["id"], metric=m["id"], state=state, text=pin_text(d, c, m)))
        out["calls"][c["id"]] = dict(facts=len(facts), all=facts, metrics=ms,
                                     kept=sum(1 for v in ms.values() if v["state"] != "missing"),
                                     named=sum(1 for v in ms.values() if v["state"] == "named"))
    return out


def show_audit(d: dict, au: dict) -> None:
    emit("text", f"\n    {'call':<22}{'facts':>6}   figures kept   … naming the client")
    for c in d["calls"]:
        a = au["calls"][c["id"]]
        emit("text", f"    {c['client']:<22}{a['facts']:>6}   {a['kept']}/4{'':<10}{a['named']}/4"
                     + ("   ← the whole call left nothing" if a["facts"] == 0 else ""))
        for m in c["metrics"]:
            s = a["metrics"][m["id"]]
            if s["state"] == "unnamed":
                emit("text", wrap(f"{m['what']}: kept without the client — “{s['fact']}”", "          · ", 116))
            elif s.get("on_person"):
                emit("text", wrap(f"{m['what']}: only on a person, not in the project — “{s['on_person']}”", "          · ", 116))
    tot = sum(a["kept"] for a in au["calls"].values()), sum(a["named"] for a in au["calls"].values())
    nfig = 4 * len(d["calls"])
    emit("text", f"\n    {tot[0]}/{nfig} figures said on the calls are in memory; {tot[1]}/{nfig} in a fact that names the client. "
                 f"{len(au['pins'])} line(s) to pin.")
    emit("audit", "", **au)


def write_audited(cli: CLI, world: dict, au: dict) -> dict:
    """F: every fact in “as_said”, copied word for word, plus the pinned lines. A and F differ only by those lines."""
    src = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["as_said"], echo=True)]
    src = [s for s in src if s]
    p = world["projects"]["audited"]
    if src:
        res = cli.run("fact", "add", *src, "--project", p, scoped=True, echo=True)
        if len((res or {}).get("facts") or []) != len(src):
            die(f"`fact add` returned {len((res or {}).get('facts') or [])} facts for {len(src)} texts")
    pins = [x["text"] for x in au["pins"]]
    if pins:
        cli.run("fact", "add", *pins, "--project", p, scoped=True, echo=True)
    note(f"“audited”: the {len(src)} facts MemoryLake extracted into “as_said”, copied word for word, then {len(pins)} pinned line(s)")
    for x in au["pins"]:
        emit("text", wrap(f"[{x['state']}] {x['text']}", "      · ", 118))
    emit("pinned", "", copied=len(src), pins=au["pins"])
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact add`
    return dict(copied=len(src), pins=len(pins))


# --------------------------------------------------------------------------- step 5: ask

def system_prompt(d: dict) -> str:
    return d["system"].format(today=today(d).strftime("%A %Y-%m-%d"))


def gap(au: dict | None, q: dict) -> str:
    """Where this question's figure stood after the calls: named / unnamed / missing."""
    return au["calls"][q["call"]]["metrics"][q["metric"]]["state"] if au else ""


def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK[r["grade"]] if r.get("grade") else "·"


def ask_rows(cli: CLI, world: dict, au: dict, arm: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    qs = questions(d)
    jobs = [(q, i) for q in qs for i in range(RUNS)]
    for n, q in enumerate(qs):
        baseline(cli, q["ask"], system, echo=echo_first and n == 0)

    def one(job):
        q, i = job
        r = router(cli, q["ask"], system, world["boundaries"][ARM_COPY[arm]], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=q["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="", gap=gap(au, q))
        empty = r["prompt_tokens"] <= BASE[(q["ask"], system)]
        return dict(item=q["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    grade=grade(d, q, r["reply"]), gap=gap(au, q), borrowed=borrowed(d, q, r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        marks = " ".join(mark(r) for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {q['what']:<44} {marks:<8} " + (first[:66] + "…" if len(first) > 67 else first))
        emit("ask_row", "", arm=arm, item=q["id"], rows=mine, truth=q["truth"], gap=gap(au, q))
    return rows


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    t = dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
             empty=sum(1 for r in rows if r.get("grade") and r.get("empty")))
    for g in MARK:
        t[g] = sum(1 for r in live if r["grade"] == g)
    for part, keep in (("gap", lambda r: r["gap"] != "named"), ("held", lambda r: r["gap"] == "named")):
        sub = [r for r in live if keep(r)]
        t[part + "_n"], t[part + "_ok"] = len(sub), sum(1 for r in sub if r["grade"] == "ok")
    return t


def show_example(rows: list[dict]) -> None:
    """The same question through A and F, for a figure the audit had to pin (its call left it out, or left it unnamed)."""
    d = load_data()
    qs = questions(d)
    pick = next((q for q in qs for r in rows if r["arm"] == "A" and r["item"] == q["id"] and r["gap"] == "missing"
                 and r.get("grade") and r["grade"] != "ok"), None) \
        or next((q for q in qs for r in rows if r["arm"] == "A" and r["item"] == q["id"] and r["gap"] != "named"
                 and r.get("grade") and r["grade"] != "ok"), None)
    if not pick:
        note("no question whose figure the audit had to pin went wrong through A this run — nothing to set side by side")
        return
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["item"] == pick["id"] and r.get("reply")]
        r = next((r for r in mine if a == "F" or r["grade"] != "ok"), None) or (mine[0] if mine else None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{pick['ask']}”  (said on the call: {pick['truth']}; the figure was "
                         f"{r['gap']} after the call)")
            emit("text", wrap(r["reply"], f"    {mark(r)} | ", 104))
            emit("example", "", arm=a, row=r, ask=pick["ask"], truth=pick["truth"])


def score(au: dict, rows: list[dict]) -> dict:
    d = load_data()
    qs = questions(d)
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, right / graded ({RUNS} answers per arm; ∅ = nothing attached, not graded):")
    emit("text", f"      {'':<60} {'after the call':<15}" + "  ".join(f"{a:<12}" for a in ARMS))
    for q in qs:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            marks="".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']} {c['marks']}"  # noqa: E731
        emit("text", f"      {q['what'] + ' = ' + q['truth']:<60} {gap(au, q):<15}" + "  ".join(f"{cell(cells[a]):<12}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
    t = out["answers"]
    emit("text", f"    {'':<34}{'right':>9}{'⇄ other client':>16}{'Unknown':>9}{'✎':>4}"
                 f"{'figures the audit pinned':>27}{'figures kept with the client':>31}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<34}{x['ok']:>5}/{x['n']:<3}{x['swap']:>16}{x['unknown']:>9}{x['made']:>4}"
                     f"{x['gap_ok']:>22}/{x['gap_n']:<4}{x['held_ok']:>26}/{x['held_n']:<4}" + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    emit("text", "\n    ✓ the figure said on the call · ⇄ another client's (or another metric's) figure from memory · ? Unknown · "
                 "✎ a figure nobody said · – none of these · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s client memory, two projects")
    world = setup(cli)

    banner(3, TOTAL, "Six client wrap-up calls, four figures each")
    ingest(cli, world)

    banner(4, TOTAL, "Audit each call's receipt — which figures it left, and whose they are")
    au = audit(cli, world)
    show_audit(d, au)

    banner(5, TOTAL, "F — the same memory, plus the lines the audit pins")
    pw = write_audited(cli, world, au)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the case-study assistant — two memories, the same twelve questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, au, a, echo_first=a == ARMS[0])
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(7, TOTAL, "The score — per question and per memory")
    sc = score(au, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(audit=au, pinned=pw, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
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
    """Re-read each call's receipt: which figures it left in memory, and whether they name the client."""
    d = load_data()
    world = load_world(cli)
    au = audit(cli, world)
    show_audit(d, au)
    emit("memory_view", "", audit=au)
    return au


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each memory; graded when it is one of the twelve."""
    d = load_data()
    world = load_world(cli)
    q = next((q for q in questions(d) if q["ask"].lower() == question.strip().lower()), None)
    rows = []
    system = system_prompt(d)
    for n, a in enumerate(ARMS):
        base = baseline(cli, question, system, echo=n == 0)
        r = router(cli, question, system, world["boundaries"].get(ARM_COPY[a]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = grade(d, q, r["reply"]) if q else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, grade=g))
    if q:
        emit("text", f"\n  said on the call: {q['truth']}")
    emit("asked", "", question=question, item=q and q["id"], rows=rows, truth=q and q["truth"])
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


EXAMPLE = "What was the cost per lead on the Halden Bikes spring commuter campaign?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = re-read each call's receipt: which figures it left, and "
                         "whose; ask \"<question>\" = one question through A and F; cleanup = delete everything")
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
        banner(2, 2, "Each call's receipt: which figures it left in memory, and whose")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the case-study assistant, through A and F")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py memory`, or `python3 demo.py ask '{EXAMPLE}'`; "
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

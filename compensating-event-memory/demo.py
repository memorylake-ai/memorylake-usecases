#!/usr/bin/env python3
"""compensating-event-memory — a reversal that names an event id never reaches the value it undoes.

Ferrowyn Energy (a fictional energy supplier) keeps each customer account as an event stream and writes every event
into memory as it is appended; its account service assistant answers from that memory through MemoryRouter. In an
event-sourced system a mistake is never edited away: a compensating event is appended instead — "EventReversed:
reverses evt-1010 (the request came from an unverified caller)". It names the event it undoes by id; it does not
repeat the value.

MemoryLake's extraction turns each event into a fact, but the facts do not keep the event ids. So the reversal has
nothing to attach to: it is dropped, or kept as a fact of its own ("evt-1018 reversed evt-1010 …") next to the fact it
should have undone, and the account's current value in memory stays the one that was reversed. Four memories, the
same eight accounts, read through MemoryRouter: A the events as they are · B the same events, but the app spells out
each reversal (what it undoes and what the value is now) · C A's facts, copied word for word, plus the event log
(one fact per event, id and date included) and one state fact per account that the app folds from the events
itself · D only that event log and those state facts, nothing extracted. Last, the app replays the event log to any
date — the state on 25 September, and today — without a model.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 104 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-cev"  # memorylake-usecases / compensating-event-memory


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
COPIES = ("plain", "spelled", "state", "log")   # the four projects
COPY_NAME = {"plain": "the events as they are",
             "spelled": "reversals spelled out",
             "state": "A's facts + the event log + the app's state facts",
             "log": "the event log + the app's state facts, nothing extracted"}
ARMS = ("A", "B", "C", "D")
ARM_LABEL = {"A": "A  the events as they are", "B": "B  reversals spelled out", "C": "C  A + event log + app state",
             "D": "D  event log + app state only"}
ARM_COPY = {"A": "plain", "B": "spelled", "C": "state", "D": "log"}
ARM_OF = {c: a for a, c in ARM_COPY.items()}
MARK = {"ok": "✓", "stale": "↺", "unknown": "?", "other": "–"}
GRADE_NAME = {"ok": "the current value", "stale": "the value that was reversed (or replaced)",
              "unknown": "Unknown", "other": "neither value"}
STATE_MARK = {"current": "current", "stale": "STALE", "tie": "tie", "missing": "missing"}
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


# --------------------------------------------------------------------------- step 3: writing the events

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
# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "events.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    return [d["bot"]["key"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=indent)


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def day(e: dict) -> str:
    return e["at"][:10]


def short_day(s: str) -> str:
    return date.fromisoformat(s[:10]).strftime("%a %d %b")


def account(d: dict, key: str) -> dict:
    return next(a for a in d["accounts"] if a["id"] == key)


def all_events(d: dict) -> list[dict]:
    """Every event of every account, in the order they were appended to the event store."""
    return sorted(({**e, "account": a["id"]} for a in d["accounts"] for e in a["events"]), key=lambda e: (e["at"], e["id"]))


def fold(events: list[dict], upto: str | None = None) -> tuple[str | None, dict | None, list[dict]]:
    """The projection: replay the events up to a day; a reversal takes the event it names out of the stream.
    Returns (value, the event that set it, the reversals applied)."""
    seen = [e for e in sorted(events, key=lambda e: (e["at"], e["id"])) if upto is None or day(e) <= upto]
    undone = {e["reverses"] for e in seen if e.get("reverses")}
    value, setter = None, None
    for e in seen:
        if "value" in e and e["id"] not in undone:
            value, setter = e["value"], e
    return value, setter, [e for e in seen if e.get("reverses")]


def reason(e: dict) -> str:
    return e["text"][0].upper() + e["text"][1:]


def message_text(copy: str, a: dict, e: dict) -> str:
    """What the event store writes. A reversal names the event it undoes — the usual compensating event. In B the app
    looks that event up in its own store and writes what it undid and what the value is now."""
    head = f"Event {e['id']} on account {a['name']}: {e['type']}"
    if not e.get("reverses"):
        return f"{head}. {e['text']}"
    if copy != "spelled":
        return f"{head}: reverses {e['reverses']}. {reason(e)}"
    orig = next(x for x in a["events"] if x["id"] == e["reverses"])
    after, _, _ = fold(a["events"], day(e))
    return (f"{head}: reverses {e['reverses']} ({orig['type']}: {orig['text']}) {reason(e)} "
            f"That change is undone: {a['name']}'s {a['field']} is {after} again.")


def log_text(a: dict, e: dict) -> str:
    """One event-log fact in C: the account, the id, the day and the event, in a form the app can parse back."""
    if e.get("reverses"):
        return f"Event log · {a['name']} · {e['id']} · {day(e)} · EventReversed · reverses {e['reverses']}: {e['text']}"
    return f"Event log · {a['name']} · {e['id']} · {day(e)} · {e['type']} · {a['field']} = {e['value']}: {e['text']}"


LOG_RE = re.compile(r"^Event log · (?P<name>.+?) · (?P<id>evt-\d+) · (?P<day>\d{4}-\d\d-\d\d) · (?P<type>\w+) · "
                    r"(?:reverses (?P<rev>evt-\d+)|(?P<field>[^=]+?) = (?P<value>.+?)): ")


def parse_log(texts: list[str]) -> dict[str, list[dict]]:
    """C's event-log facts back into events, per account name. Anything else in C (A's facts, state facts) is skipped."""
    out: dict[str, list[dict]] = {}
    for t in texts:
        m = LOG_RE.match(t or "")
        if not m:
            continue
        e = dict(id=m["id"], at=m["day"] + "T09:00:00Z", type=m["type"])
        if m["rev"]:
            e["reverses"] = m["rev"]
        else:
            e["value"] = m["value"]
        out.setdefault(m["name"], []).append(e)
    return out


def state_text(d: dict, a: dict) -> str:
    value, setter, revs = fold(a["events"])
    evs = a["events"]
    note_ = " ".join(
        f"{r['reverses']} ({next(x for x in evs if x['id'] == r['reverses'])['type']} to "
        f"{next(x for x in evs if x['id'] == r['reverses'])['value']}) was reversed by {r['id']} on {day(r)}, so it no "
        f"longer counts." for r in revs) or f"The latest change is {setter['id']} on {day(setter)}."
    return (f"Account state for {a['name']}, {a['field']}: {value}. Folded by the app from the account's event stream "
            f"({evs[0]['id']} … {evs[-1]['id']}, up to {day(evs[-1])}). {note_}")


def questions(d: dict) -> list[dict]:
    return [dict(id=a["id"], what=f"{a['name']} · {a['field']}", ask=a["ask"], values=a["values"], current=a["current"],
                 stale=a["stale"], kind=a["kind"]) for a in d["accounts"]]


# --------------------------------------------------------------------------- reading a fact or a reply

AS_OF = re.compile(r"\((?:as of|on) (\d{4}-\d\d-\d\d)|\bon (\d{4}-\d\d-\d\d)\b|\bas of (\d{4}-\d\d-\d\d)\b")
PAREN_DATES = re.compile(r"\((?:as of|on) [^)]*\)")
FROM_TO = re.compile(r"\bfrom\b.{0,80}?\bto\b", re.I)
PREVIOUSLY = re.compile(r"\bpreviously\b|\bformerly\b|\bprevious\b", re.I)
REVERSAL = re.compile(r"\brevers|\brevert|\bundo|\bundone|\bvoid\b|\brolled back\b|\brescind", re.I)
# A sentence that rejects a value does not state it: "The bank rejected mandate DD-5530", "the meter is not eligible
# for the Flat tariff", "billing@… was a typo". (Extraction often keeps a reversal's reason as a fact of its own.)
NEGATED = re.compile(r"\bnot\b|n't\b|\bno longer\b|\brejected\b|\btypo\b|\bwithout\b|\binvalid\b|\bbounced\b|"
                     r"\bdeclined\b|\bineligible\b|\bin error\b|\bwrong\b", re.I)
RESTORED = r"(?:back to|reverted to|restored to|returns? to|returned to|is now|remains|stays|is again|again (?:is|on))"
LEAD_UNKNOWN = re.compile(r"^\W*(?:answer:\s*)?unknown\b", re.I)
NOT_KNOWN = re.compile(r"\bunknown\b|\bno (?:record|information|data|details?)\b|\bdon'?t have\b|\bdo not have\b|"
                       r"\bnot (?:sure|available|specified|recorded|known)\b", re.I)


def name_re(a: dict) -> str:
    return re.escape(a["name"].split()[0])


def fact_date(text: str) -> str | None:
    m = AS_OF.search(text or "")
    return next((g for g in m.groups() if g), None) if m else None


def asserted(values: dict, text: str) -> str | None:
    """The value a sentence states as the case now.

    A sentence about a reversal ("evt-1018 reversed evt-1010", "the change to 3 Mill Yard was reversed") states a
    value only when it says what the value went back to ("… is 14 Quarry Lane again", "reverted to Standard");
    otherwise none. A sentence that rejects a value (NEGATED) states none. Any other sentence: the part before 'previously …', without 'from X to' and the (as of …) notes;
    of the values it names, the one named first."""
    t = PREVIOUSLY.split(text or "")[0]
    t = PAREN_DATES.sub(" ", t)
    if REVERSAL.search(t):
        for v, rx in values.items():
            if re.search(rf"{RESTORED}\s+(?:the\s+)?(?:{rx})", t, re.I | re.A) or \
               re.search(rf"(?:{rx})[^.;]{{0,40}}\bagain\b", t, re.I | re.A):
                return v
        return None
    if NEGATED.search(t):
        return None
    t = FROM_TO.sub(" ", t)
    best = None
    for v, rx in values.items():
        m = re.search(rx, t, re.I | re.A)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), v)
    return best[1] if best else None


def answer_line(reply: str) -> str:
    m = re.search(r"answer\s*:\s*(.*)", reply or "", re.I)
    return m.group(1) if m else (reply or "")


def grade(q: dict, reply: str) -> str:
    """One grade per reply, read from its 'Answer:' line: ✓ the current value · ↺ the reversed (or replaced) one ·
    ? Unknown · – neither."""
    line = answer_line(reply)
    if LEAD_UNKNOWN.match(line):
        return "unknown"
    t = FROM_TO.sub(" ", PAREN_DATES.sub(" ", line))
    best = None
    for v, rx in q["values"].items():
        m = re.search(rx, t, re.I | re.A)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), v)
    v = best[1] if best else None
    if v == q["current"]:
        return "ok"
    if v == q["stale"]:
        return "stale"
    return "unknown" if NOT_KNOWN.search(line) else "other"


def census_one(a: dict, facts: list[str]) -> dict:
    """What one account's facts say its value is now: the value stated by the latest-dated fact that states one."""
    rev = next((e for e in a["events"] if e.get("reverses")), None)
    mentions = [f for f in facts if rev and (rev["id"] in f or rev["reverses"] in f or REVERSAL.search(f))]
    dated = [(fact_date(f) or "", asserted(a["values"], f), f) for f in facts]
    dated = [x for x in dated if x[1]]
    # Did the change the reversal undoes reach memory at all? Any fact naming its value, read or not as current —
    # except a sentence that rejects it ("the meter is not eligible for the Flat tariff" is not the change).
    changed = any(re.search(a["values"][a["stale"]], f, re.I | re.A) and not NEGATED.search(f) for f in facts)
    if not dated:
        return dict(state="missing", facts=facts, top=[], reversal=mentions, changed=changed)
    latest = max(x[0] for x in dated)
    top = [x for x in dated if x[0] == latest]
    vals = {x[1] for x in top}
    state = "tie" if len(vals) > 1 else ("current" if vals == {a["current"]} else "stale" if vals == {a["stale"]} else "missing")
    return dict(state=state, date=latest, top=[x[2] for x in top], facts=facts, reversal=mentions, changed=changed)


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for all four, and no arm letter in it: a project's name is context for extraction.
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} account events",
                                       "--custom-id", cid(c), "--description", f"{d['org']} customer account events. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("four projects with the same name: A and B get the event messages, C gets A's facts + the event log + the app's state, "
         "D only the event log + the app's state")
    b = d["bot"]
    world["actors"][b["key"]] = make_actor(cli, b["key"], b["display"], f"{b['role']}. Demo data.", echo=True,
                                           actor_type="ASSISTANT")
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"Account assistant ({COPY_NAME[c]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what the account assistant reads through MemoryRouter")
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


# --------------------------------------------------------------------------- step 3: the events

def show_events(d: dict) -> None:
    emit("text", f"\n    {'account':<23}{'event':<10}{'day':<12}{'what happened':<70}")
    rows = []
    for a in d["accounts"]:
        for n, e in enumerate(a["events"]):
            what = (f"{e['type']}: reverses {e['reverses']} — {e['text']}" if e.get("reverses")
                    else f"{e['type']} → {e['value']}")
            emit("text", f"    {a['name'] if n == 0 else '':<23}{e['id']:<10}{short_day(e['at']):<12}{what[:86]}")
            rows.append(dict(account=a["id"], name=a["name"], field=a["field"], kind=a["kind"], **e))
        value, _, _ = fold(a["events"])
        emit("text", f"    {'':<23}{'':<10}{'':<12}= {a['field']} now: {value}"
                     + ("   (control: no reversal)" if a["kind"] == "control" else ""))
    emit("events", "", rows=rows)


def wait_read(cli: CLI, conv: str, msg: str | None, echo: bool, limit: int = 300) -> bool:
    """A message is read when it shows up in its extraction batch and the conversation reports cook_finished."""
    t0, first = time.time(), echo
    while time.time() - t0 < limit:
        try:
            batch = cli.run("conv", "consumed-messages", conv, "--message", msg, scoped=True, echo=first) if msg else [1]
            first = False
            items = batch.get("items") if isinstance(batch, dict) else batch
            if items:
                st = cli.run("conv", "cook-status", conv, scoped=True, echo=False) or {}
                if st.get("cook_finished"):
                    return True
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
        time.sleep(4)
    return False


def ingest_one(cli: CLI, world: dict, copy: str, a: dict, echo: bool, t0: float) -> tuple[str, bool]:
    d = load_data()
    bot = world["actors"][d["bot"]["key"]]
    conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", bot, "--kind", "GROUP",
                   "--name", f"{a['name']} account events", "--custom-id", cid("conv", copy, a["id"]), scoped=True, echo=echo)
    ok = True
    for i, e in enumerate(a["events"]):
        mid = append(cli, conv["id"], bot, e["id"], e["at"], message_text(copy, a, e), echo=echo and i == 0)
        # One event at a time, in order: the next one is written after this one has been read.
        ok = wait_read(cli, conv["id"], mid, echo=echo and i == 0) and ok
    emit("progress", f"    {ARM_OF[copy]} · {a['name']}: {len(a['events'])} events read · {int(time.time() - t0)} s",
         copy=copy, account=a["id"])
    return conv["id"], ok


def ingest(cli: CLI, world: dict) -> None:
    d = load_data()
    show_events(d)
    emit("text", "\n    One GROUP conversation per account and memory, the events in order, each read before the next is "
                 "written,\n    stamped with the event's own time. A and B get the same messages except the six reversals:")
    a = account(d, "tallow")
    rev = next(e for e in a["events"] if e.get("reverses"))
    emit("text", wrap("A: " + message_text("plain", a, rev), "      ", 112))
    emit("text", wrap("B: " + message_text("spelled", a, rev), "      ", 112))
    t0 = time.time()
    jobs = [(copy, a) for copy in ("plain", "spelled") for a in d["accounts"]]
    with ThreadPoolExecutor(8) as pool:
        res = list(pool.map(lambda j: ingest_one(cli, world, j[0], j[1], j is jobs[0], t0), jobs))
    world["convs"] = {f"{j[0]}-{j[1]['id']}": r[0] for j, r in zip(jobs, res)}
    late = [f"{ARM_OF[j[0]]}/{j[1]['name']}" for j, r in zip(jobs, res) if not r[1]]
    if late:
        note(f"still extracting after 300 s: {', '.join(late)} — read as they stand")
    n = sum(len(a["events"]) for a in d["accounts"])
    note(f"{len(jobs)} conversations, {2 * n} messages, all read in {int(time.time() - t0)} s")
    time.sleep(8)


# --------------------------------------------------------------------------- step 4: what memory says now

def conv_fact_ids(cli: CLI, conv: str, project: str, echo: bool = False) -> set[str]:
    """The facts one conversation added or updated in the project (its receipt)."""
    items = paged(cli, ["conv", "fact-actions", conv, "--project", project, "--page-size", "100"], echo)
    return {x.get("fact_id") for x in items if x.get("fact_id")}


def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per account, the live facts its conversation wrote (from the conversation's fact-actions), plus any fact that
    names the account. Extracted facts often leave the account's name out ("The service address was changed …")."""
    d = load_data()
    out: dict = {}
    for n, copy in enumerate(("plain", "spelled")):
        live = {f["id"]: f.get("fact") or "" for f in project_facts(cli, world["projects"][copy], echo=echo and n == 0)}
        accounts = {}
        for i, a in enumerate(d["accounts"]):
            conv = world["convs"].get(f"{copy}-{a['id']}")
            ids = conv_fact_ids(cli, conv, world["projects"][copy], echo=echo and n == 0 and i == 0) if conv else set()
            mine = [t for k, t in live.items() if k in ids or re.search(name_re(a), t, re.I)]
            accounts[a["id"]] = census_one(a, mine)
        out[copy] = dict(all=list(live.values()), accounts=accounts)
    return out


def show_census(d: dict, cs: dict) -> None:
    emit("text", f"\n    {'account':<23}{'field':<19}{'true now':<28}{'A · as they are':<18}{'B · spelled out':<18}")
    for a in d["accounts"]:
        x, y = cs["plain"]["accounts"][a["id"]], cs["spelled"]["accounts"][a["id"]]
        tag = "" if a["kind"] == "reversed" else "   (control)"
        emit("text", f"    {a['name']:<23}{a['field']:<19}{a['current']:<28}{STATE_MARK[x['state']]:<18}{STATE_MARK[y['state']]:<18}{tag}")
    for copy, arm in (("plain", "A"), ("spelled", "B")):
        bad = [(a, cs[copy]["accounts"][a["id"]]) for a in d["accounts"] if cs[copy]["accounts"][a["id"]]["state"] != "current"]
        if not bad:
            continue
        emit("text", f"\n    {arm}: accounts whose latest fact does not state the current value")
        for a, s in bad:
            shown = (s["top"] or [f"(no fact states {a['name']}'s {a['field']})"])[0]
            emit("text", wrap(f"{a['name']} [{STATE_MARK[s['state']]}]: {shown}", "      · ", 118))
            for r in s["reversal"][:1]:
                emit("text", wrap(f"and, in the same memory: {r}", "          ", 118))
    for copy in ("plain", "spelled"):
        cc = cs[copy]["accounts"]
        rv = [a for a in d["accounts"] if a["kind"] == "reversed"]
        cs[copy]["tally"] = dict(
            current=sum(1 for a in rv if cc[a["id"]]["state"] == "current"),
            stale=sum(1 for a in rv if cc[a["id"]]["state"] == "stale"),
            tie=sum(1 for a in rv if cc[a["id"]]["state"] == "tie"),
            missing=sum(1 for a in rv if cc[a["id"]]["state"] == "missing"),
            reversal_kept=sum(1 for a in rv if cc[a["id"]]["reversal"]),
            change_lost=sum(1 for a in rv if not cc[a["id"]]["changed"]),
            control=sum(1 for a in d["accounts"] if a["kind"] == "control" and cc[a["id"]]["state"] == "current"),
            facts=len(cs[copy]["all"]))
    for copy, arm in (("plain", "A"), ("spelled", "B")):
        t = cs[copy]["tally"]
        emit("text", f"\n    {arm}: of the 6 accounts with a reversal — current {t['current']} · stale (the reversed value) "
                     f"{t['stale']} · tie {t['tie']} · missing {t['missing']}; a fact about the reversal is in memory for "
                     f"{t['reversal_kept']}; the change it undoes never reached memory for {t['change_lost']}"
                     f"{' (' + ', '.join(a['name'] for a in d['accounts'] if a['kind'] == 'reversed' and not cs[copy]['accounts'][a['id']]['changed']) + ')' if t['change_lost'] else ''}. "
                     f"Controls current: {t['control']}/2. ({t['facts']} facts)")
    emit("census", "", **{k: dict(accounts=v["accounts"], tally=v["tally"], all=v["all"]) for k, v in cs.items()})


# --------------------------------------------------------------------------- step 5: C — the app projects the state

def write_state(cli: CLI, world: dict) -> dict:
    d = load_data()
    emit("text", "\n    The app's projection, folded in code from each account's events (a reversal takes the event it "
                 "names out of the stream):")
    for a in d["accounts"]:
        value, setter, revs = fold(a["events"])
        how = (f"{revs[0]['reverses']} undone by {revs[0]['id']}" if revs else f"latest change {setter['id']}")
        emit("text", f"      {a['name']:<23}{a['field']:<19}{value:<28}{how}")
    src = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["plain"], echo=True)]
    src = [s for s in src if s]
    p = world["projects"]["state"]
    if src:
        res = cli.run("fact", "add", *src, "--project", p, scoped=True, echo=True)
        if len((res or {}).get("facts") or []) != len(src):
            die(f"`fact add` returned {len((res or {}).get('facts') or [])} facts for {len(src)} texts")
    logs = [log_text(a, e) for a in d["accounts"] for e in a["events"]]
    states = [state_text(d, a) for a in d["accounts"]]
    cli.run("fact", "add", *logs, "--project", p, scoped=True, echo=True)
    cli.run("fact", "add", *states, "--project", p, scoped=True, echo=True)
    note(f"C: the {len(src)} facts MemoryLake extracted into A, copied word for word, then {len(logs)} event-log facts "
         f"(id and day kept) and {len(states)} state facts that the app wrote")
    q = world["projects"]["log"]
    cli.run("fact", "add", *logs, "--project", q, scoped=True, echo=True)
    cli.run("fact", "add", *states, "--project", q, scoped=True, echo=False)
    note(f"D: the same {len(logs)} event-log facts and {len(states)} state facts, and nothing extracted")
    for t in (logs[2], states[0]):
        emit("text", wrap(t, "      · ", 118))
    emit("state", "", copied=len(src), logs=logs, states=states)
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact add`
    return dict(copied=len(src), logs=len(logs), states=len(states))


# --------------------------------------------------------------------------- step 6: ask

def system_prompt(d: dict) -> str:
    return d["system"].format(org=d["org"], today=today(d).strftime("%A %Y-%m-%d"))


def mark(r: dict) -> str:
    if r.get("empty"):
        return "∅"
    return MARK[r["grade"]] if r.get("grade") else "·"


def ask_rows(cli: CLI, world: dict, arm: str, echo_first: bool = True) -> list[dict]:
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
            return dict(item=q["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="")
        empty = r["prompt_tokens"] <= BASE[(q["ask"], system)]
        return dict(item=q["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    grade=grade(q, r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        marks = " ".join(mark(r) for r in mine)
        first = " ".join(next((r["reply"] for r in mine if r.get("reply")), "").split())
        emit("text", f"    {q['what'][:40]:<41} {marks:<8} " + (first[:70] + "…" if len(first) > 71 else first))
        emit("ask_row", "", arm=arm, item=q["id"], rows=mine, current=q["current"], stale=q["stale"])
    return rows


def tally(rows: list[dict], qs: list[dict] | None = None) -> dict:
    qs = qs or questions(load_data())
    kind = {q["id"]: q["kind"] for q in qs}
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    t = dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
             empty=sum(1 for r in rows if r.get("grade") and r.get("empty")))
    for g in MARK:
        t[g] = sum(1 for r in live if r["grade"] == g)
    for part in ("reversed", "control"):
        sub = [r for r in live if kind[r["item"]] == part]
        t[part + "_n"], t[part + "_ok"] = len(sub), sum(1 for r in sub if r["grade"] == "ok")
        t[part + "_stale"] = sum(1 for r in sub if r["grade"] == "stale")
    return t


def show_example(rows: list[dict]) -> None:
    """The same question through the four memories, for an account whose reversal A answered past."""
    d = load_data()
    qs = questions(d)
    pick = next((q for q in qs if q["kind"] == "reversed" for r in rows
                 if r["arm"] == "A" and r["item"] == q["id"] and r.get("grade") == "stale"), None)
    if not pick:
        note("no reversed account was answered with its reversed value through A this run — nothing to set side by side")
        return
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["item"] == pick["id"] and r.get("reply")]
        r = next((r for r in mine if (a == "A") == (r["grade"] == "stale")), None) or (mine[0] if mine else None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{pick['ask']}”  (now: {pick['current']}; reversed: {pick['stale']})")
            emit("text", wrap(r["reply"], f"    {mark(r)} | ", 104))
            emit("example", "", arm=a, row=r, ask=pick["ask"], current=pick["current"])


def score(rows: list[dict]) -> dict:
    d = load_data()
    qs = questions(d)
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, current value / graded ({RUNS} answers per memory; ∅ = nothing attached, not graded):")
    emit("text", f"      {'':<56}" + "  ".join(f"{a:<11}" for a in ARMS))
    for q in qs:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), n=len(live), empty=len(mine) - len(live),
                            marks="".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']} {c['marks']}"  # noqa: E731
        tag = " (control)" if q["kind"] == "control" else ""
        emit("text", f"      {(q['what'] + ' = ' + q['current'] + tag)[:55]:<56}" + "  ".join(f"{cell(cells[a]):<11}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a], qs)
    t = out["answers"]
    emit("text", f"    {'':<34}{'current':>9}{'↺ reversed':>12}{'?':>4}{'–':>4}{'reversed accounts':>21}{'controls':>11}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<34}{x['ok']:>5}/{x['n']:<3}{x['stale']:>12}{x['unknown']:>4}{x['other']:>4}"
                     f"{x['reversed_ok']:>16}/{x['reversed_n']:<4}{x['control_ok']:>7}/{x['control_n']:<3}"
                     + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    emit("text", "\n    ✓ the current value · ↺ the value the reversal undid (for a control: the value before the latest change) · "
                 "? Unknown · – neither · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: replay

def replay(cli: CLI, world: dict, at: str, echo: bool = True) -> dict:
    """Read C's event log back from memory (`fact list` returns every fact, not a top-k) and fold it in code — to a
    past day and to today. No model involved; each cell is checked against the data file's own fold."""
    d = load_data()
    texts = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["state"], echo=echo)]
    logs = parse_log(texts)
    n = sum(len(v) for v in logs.values())
    emit("text", f"\n    {n} event-log facts read back from C (of {len(texts)} facts there); folded per account:")
    emit("text", f"\n    {'account':<23}{'field':<19}{'on ' + short_day(at):<30}{'today':<30}")
    rows, ok = [], 0
    for a in d["accounts"]:
        evs = logs.get(a["name"], [])
        past, _, _ = fold(evs, at)
        now, _, revs = fold(evs)
        want_past, _, _ = fold(a["events"], at)
        want_now, _, _ = fold(a["events"])
        good = past == want_past and now == want_now and len(evs) == len(a["events"])
        ok += good
        emit("text", f"    {a['name']:<23}{a['field']:<19}{str(past):<30}{str(now):<30}{'✓' if good else '✗ expected ' + str(want_past) + ' / ' + str(want_now)}"
                     + (f"   ({revs[0]['reverses']} reversed by {revs[0]['id']})" if revs and good else ""))
        rows.append(dict(account=a["id"], name=a["name"], field=a["field"], past=past, now=now, ok=good,
                         events=len(evs), reversal=revs[0]["id"] if revs else None))
    note(f"{ok}/{len(d['accounts'])} accounts replay to the right value on both days — from the event log in memory, folded in code")
    emit("replay", "", at=at, rows=rows, ok=ok, n=len(d["accounts"]), facts=len(texts), logs=n)
    return dict(at=at, rows=rows, ok=ok)


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s account memory, four projects")
    world = setup(cli)

    banner(3, TOTAL, "Twenty-four account events, six of them reversals that name the event they undo")
    ingest(cli, world)

    banner(4, TOTAL, "What memory now says about each account")
    cs = census(cli, world)
    show_census(d, cs)

    banner(5, TOTAL, "C and D — the app folds each account's events itself")
    st = write_state(cli, world)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the account assistant — four memories, the same eight questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == ARMS[0])
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(7, TOTAL, "The score — per question and per memory")
    sc = score(rows)

    banner(8, TOTAL, f"Replay — C's event log, folded to {short_day(d['replay_at'])} and to today")
    rp = replay(cli, world, d["replay_at"])

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census={k: dict(accounts=v["accounts"], tally=v["tally"], all=v["all"]) for k, v in cs.items()},
                   state=st, answers=rows, score=sc, replay=rp, tokens=tok, seconds=int(time.time() - t0),
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
    """Re-read A's and B's facts: which value the latest fact about each account states."""
    d = load_data()
    world = load_world(cli)
    cs = census(cli, world)
    show_census(d, cs)
    emit("memory_view", "", census={k: dict(accounts=v["accounts"], tally=v["tally"]) for k, v in cs.items()})
    return cs


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each memory; graded when it is one of the eight."""
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
        g = grade(q, r["reply"]) if q else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, grade=g))
    if q:
        emit("text", f"\n  now: {q['current']} · reversed: {q['stale']}")
    emit("asked", "", question=question, item=q and q["id"], rows=rows, current=q and q["current"])
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


EXAMPLE = "What is Tallow & Finch Bakery's service address right now?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "replay", "cleanup"],
                    help="run = full demo (default); memory = re-read what A's and B's facts say about each account; "
                         "ask \"<question>\" = one question through A, B, C and D; replay [YYYY-MM-DD] = fold C's event "
                         "log to that day and to today; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="for ask: the question; for replay: the day")
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
        banner(2, 2, "What A's and B's facts say about each account now")
        memory(cli)
        return
    if args.command == "replay":
        at = args.args[0] if args.args else load_data()["replay_at"]
        try:
            date.fromisoformat(at)
        except ValueError:
            die("usage: python3 demo.py replay [YYYY-MM-DD]")
        banner(2, 2, f"Replay — C's event log, folded to {short_day(at)} and to today")
        replay(cli, load_world(cli), at)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the account assistant, through A, B, C and D")
        ask(cli, args.args[0])
        return
    run_pipeline(cli, reset=args.reset)
    print(f"\nDone. Try `python3 demo.py memory`, `python3 demo.py replay 2026-10-01`, or "
          f"`python3 demo.py ask '{EXAMPLE}'`; `python3 demo.py cleanup` removes the demo data.")


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

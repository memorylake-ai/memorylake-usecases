#!/usr/bin/env python3
"""thread-reply-memory — "It slips to 9 March": a reply that only says "it", saved as it is sent.

Pellridge Freight (a fictional company): the workplace operations team plans three office moves in the Slack channel
#office-moves. Each move gets a thread that names it ("Next up is the Porto office move."); the replies under it say
"it", "they", "there" ("It goes ahead on 2 February, the landlord confirmed the lease break."). The team's assistant
saves the channel as it goes: every message is appended to MemoryLake the moment it is posted.

Read one at a time, a reply is matched to whatever memory already holds — and Porto's date is merged into the Leeds
move's date fact, which now says Leeds goes ahead on 2 February ("previously 9 March"). A week later the assistant
tells the facilities manager the wrong date for Leeds, with no doubt in its voice.
Three memories of the same ten messages, the same eight questions: A the channel as sent, one conversation · B one
conversation per Slack thread · C one conversation, each reply tagged with its thread's first message — plus the same
questions with no memory at all, the baseline for what the model says on its own.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 100 calls on gpt-4o-mini per run).
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-trm"  # memorylake-usecases / thread-reply-memory


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
MAX_TOKENS = 120
COPIES = ("sent", "threads", "tagged")   # the three projects
COPY_NAME = {"sent": "the channel as sent, one conversation",
             "threads": "one conversation per Slack thread",
             "tagged": "each reply tagged with its thread"}
ARMS = ("0", "A", "B", "C")
ARM_LABEL = {"0": "0  no memory", "A": "A  as sent, one conversation", "B": "B  one conversation per thread",
             "C": "C  each reply tagged with its thread"}
ARM_COPY = {"A": "sent", "B": "threads", "C": "tagged"}
MARK = {"ok": "✓", "swap": "⇄", "wrong": "✗", "unknown": "?"}
GRADE_NAME = {"ok": "right", "swap": "another move's value", "wrong": "wrong", "unknown": "says it does not know"}

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


# --------------------------------------------------------------------------- writing a text

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

def load_data() -> dict:
    return json.loads((DATA / "channel.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    # one pair of people per project: facts can land on the speakers, and the three memories must not share any
    return [f"{c}-{p['key']}" for c in COPIES for p in d["people"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    sub = indent.replace("·", " ") if "·" in indent else indent
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=sub)


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


def move(d: dict, key: str) -> dict:
    return next(m for m in d["moves"] if m["id"] == key)


def value(d: dict, key: str) -> dict:
    return next(v for v in d["values"] if v["id"] == key)


def questions(d: dict) -> list[dict]:
    return d["questions"]


def roots(d: dict) -> dict:
    """The first message of each thread: the one that names the move."""
    out: dict = {}
    for m in d["messages"]:
        out.setdefault(m["thread"], m)
    return out


def tagged_text(d: dict, m: dict) -> str:
    """C: a reply carries its thread's first message, as the chat app has it (thread_ts → parent)."""
    r = roots(d)[m["thread"]]
    return m["text"] if r["id"] == m["id"] else f"[in thread: {r['text']}] {m['text']}"


def hm(ts: str) -> str:
    return ts[11:16]


# --------------------------------------------------------------------------- reading values out of text
# Every rule here has offline cases in selftest(); run `python3 demo.py selftest` after changing one.

DAY = r"(?<![\d]){d}(?:st|nd|rd|th)?\s+(?:of\s+)?{m}\b|\b{m}\s+{d}(?:st|nd|rd|th)?(?![\d])"
VALUE_RE = {
    "leeds.date": DAY.format(d="9", m="March") + r"|2026-03-09|9 de marzo|9\. März|3月9日",
    "porto.date": DAY.format(d="2", m="February") + r"|2026-02-02|2 de febrero|2\. Februar|2月2日",
    "austin.date": DAY.format(d="23", m="April") + r"|2026-04-23|23 de abril|23\. April|4月23日",
    "leeds.server": DAY.format(d="30", m="April") + r"|2026-04-30|30 de abril|30\. April|4月30日",
    "leeds.budget": r"(?<![\d.,])180[,. ]?000(?![\d])|(?<![\d.,])180\s?k\b|18\s?万",
    "austin.budget": r"(?<![\d.,])95[,. ]?000(?![\d])|(?<![\d.,])95\s?k\b|9\.5\s?万",
    "porto.floor": r"\b(?:third|3rd)[ -]floor\b|\bfloor (?:three|3)\b|tercer piso|tercera planta|dritte\w* (?:Stock|Etage)|三楼|三层",
}
MOVE_RE = {"leeds": r"\bLeeds\b|利兹", "porto": r"\bPorto\b|波尔图", "austin": r"\bAustin\b|奥斯汀"}
# The part of a fact (or an answer) after this is the old value the extraction kept for the record.
PREVIOUSLY = r"\bpreviously\b|\bformerly\b|\boriginally\b|\binitially\b|\bantes\b|\bvorher\b|\bzuvor\b|此前|之前|原定"
UNKNOWN = (r"\b(?:don't have|do not have|no record|not recorded|not (?:been )?(?:stated|mentioned|specified|provided|"
           r"available|confirmed|given|known)|unable to|cannot (?:determine|confirm|say)|can't (?:determine|say)|"
           r"no information|no (?:specific )?(?:details?|mention|floor|date|budget)|unknown|ha(?:s|ve)(?:n't| not) been (?:decided|"
           r"specified|mentioned|confirmed|shared|set|announced)|(?:yet|still) to be (?:confirmed|decided|set|announced|determined)|"
           r"isn't (?:specified|mentioned|recorded))\b|\[(?:insert|specific)")


def has_value(key: str, text: str) -> bool:
    return bool(re.search(VALUE_RE[key], text or "", re.I | re.A)) or bool(re.search(VALUE_RE[key], text or "", re.I))


def moves_named(text: str) -> list[str]:
    """Moves named in text, in order of first appearance."""
    hits = []
    for k, rx in MOVE_RE.items():
        m = re.search(rx, text or "", re.I)
        if m:
            hits.append((m.start(), k))
    return [k for _, k in sorted(hits)]


def main_part(text: str) -> str:
    m = re.search(PREVIOUSLY, text or "", re.I)
    return text[:m.start()] if m else (text or "")


def answer_body(reply: str) -> str:
    s = " ".join((reply or "").split())
    return re.sub(r"^\**answer:?\**\s*", "", s, flags=re.I)


def clauses(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;:])\s+|,(?!\d)\s*(?:and\s+|but\s+|while\s+|whereas\s+)?|\s+(?:but|while|whereas)\s+|\s*\(|\)",
                     text, flags=re.I)
    return [p for p in parts if p and p.strip()]


def kind_values(d: dict, kind: str, not_move: str) -> list[str]:
    return [v["id"] for v in d["values"] if v["kind"] == kind and v["move"] != not_move]


def grade(d: dict, q: dict, reply: str) -> str:
    """ok · swap (another move's value, as this move's) · wrong · unknown."""
    t = answer_body(reply)
    own_move = value(d, q["value"])["move"] if q["value"] else q["move"]
    kind = value(d, q["value"])["kind"] if q["value"] else q["kind"]
    foreign = kind_values(d, kind, own_move)
    has_own = has_foreign = False
    for c in clauses(t):
        if re.search(PREVIOUSLY + r"|\bwas (?:initially |originally )?(?:scheduled|planned|set)\b|\binstead of\b|\brather than\b|"
                     r"\bnot the\b", c, re.I):
            continue  # the old value, or the one ruled out
        named = moves_named(c)
        if named and own_move not in named:
            continue  # said about another move
        has_foreign |= any(has_value(k, c) for k in foreign)
        if q["value"]:
            has_own |= has_value(q["value"], c)
    if has_foreign:
        return "swap"
    if has_own:
        return "ok"
    unknown = bool(re.search(UNKNOWN, t, re.I))
    if not q["value"]:
        if unknown:
            return "ok"
        return "wrong"
    return "unknown" if unknown else "wrong"


CASES = [
    ("Q1", "Answer: The Leeds office move happens on 9 March, because the fit-out crew is booked until then.", "ok"),
    ("Q1", "Answer: The Leeds office move goes ahead on 2 February, previously scheduled for 9 March.", "swap"),
    ("Q1", "Answer: The Leeds office move is scheduled for February 2.", "swap"),
    ("Q1", "Answer: Leeds moves on March 9th; the Porto move is on 2 February.", "ok"),
    ("Q1", "Answer: The Leeds office move is set for 9 March (it was originally 2 February).", "ok"),
    ("Q1", "Answer: I don't have a date for the Leeds office move.", "unknown"),
    ("Q1", "Answer: The Leeds office move is planned for 15 January.", "wrong"),
    ("Q1", "Answer: The Leeds office move was delayed to 9 March, but now it goes ahead on 2 February.", "swap"),
    ("Q2", "Answer: The Porto office move goes ahead on 2 February, after the landlord confirmed the lease break.", "ok"),
    ("Q2", "Answer: The date of the Porto office move has not been specified.", "unknown"),
    ("Q2", "Answer: The Porto office move happens on 9 March.", "swap"),
    ("Q2", "Answer: The Porto office move is scheduled for a date yet to be confirmed, but it is the next project.", "unknown"),
    ("Q2", "Answer: The Porto office move is scheduled to take place soon. Specific dates have not been shared yet.", "unknown"),
    ("Q2", "Answer: The Porto move is on 23 April.", "swap"),
    ("Q3", "Answer: The Porto office is moving to the third floor, not the ground floor.", "ok"),
    ("Q3", "Answer: The Porto team wants the 3rd floor.", "ok"),
    ("Q3", "Answer: Which floor Porto moves to has not been mentioned.", "unknown"),
    ("Q4", "Answer: The earliest date for the Austin office move is 23 April, after the badge reader upgrade.", "ok"),
    ("Q4", "Answer: The Austin office move can happen on 2 February at the earliest.", "swap"),
    ("Q5", "Answer: The budget for the Austin office move is 95,000 dollars.", "ok"),
    ("Q5", "Answer: The Austin move is capped at $95k.", "ok"),
    ("Q5", "Answer: The budget for the Austin office move is capped at 180,000 pounds.", "swap"),
    ("Q6", "Answer: The Leeds office move budget is capped at 180,000 pounds.", "ok"),
    ("Q6", "Answer: The Leeds budget is £180k; Austin's is 95,000 dollars.", "ok"),
    ("Q6", "Answer: The Leeds office move has a budget of 95,000 dollars.", "swap"),
    ("Q7", "Answer: The Leeds office keeps its old server room until 30 April.", "ok"),
    ("Q7", "Answer: Until April 30th.", "ok"),
    ("Q7", "Answer: The Leeds office keeps the server room until 23 April.", "swap"),
    ("Q7", "Answer: There is no information about the Leeds server room.", "unknown"),
    ("Q8", "Answer: The Leeds office is moving to the third floor, not the ground floor.", "swap"),
    ("Q8", "Answer: The floor for the Leeds office move has not been specified.", "ok"),
    ("Q8", "Answer: I don't have information on which floor the Leeds office is moving to; the Porto team wants the third floor.", "ok"),
    ("Q8", "Answer: The Leeds office is moving to the fifth floor.", "wrong"),
    ("Q8", "Answer: The Leeds office is moving to the ground floor.", "wrong"),
]

CENSUS_CASES = [
    ("porto.date", "The Leeds office move goes ahead on 2 February (as of 2026-10-12, previously scheduled for 9 March as of 2026-10-12).", "leeds"),
    ("leeds.date", "The Leeds office move goes ahead on 2 February (as of 2026-10-12, previously scheduled for 9 March as of 2026-10-12).", "previously"),
    ("porto.date", "The Porto office move goes ahead on 2 February (as of 2026-10-12).", "porto"),
    ("leeds.date", "The fit-out crew is booked until 9 March. (as of 2026-10-12)", "none"),
    ("austin.budget", "The budget for the Porto office move is capped at 95,000 dollars (as of 2026-10-12).", "porto"),
    ("porto.floor", "For the Leeds office move, the desired location is the third floor rather than the ground floor.", "leeds"),
    ("leeds.budget", "Fenwick: Das Budget für den Umzug nach Leeds beträgt 180.000 Pfund.", "leeds"),
]


def fact_reading(key: str, fact: str) -> str | None:
    """Whose value a fact makes it: the move it names first, before any "previously". None when it has no such value."""
    if not has_value(key, fact):
        return None
    main = main_part(fact)
    if not has_value(key, main):
        return "previously"
    named = moves_named(main)
    return named[0] if named else "none"


def arm_grade(d: dict, arm: str, q: dict, reply: str) -> str:
    """With no memory there is no other move's value to mix up: one there is invented, so it is ✗."""
    g = grade(d, q, reply)
    return "wrong" if arm == "0" and g == "swap" else g


def selftest() -> int:
    d = load_data()
    qs = {q["id"]: q for q in questions(d)}
    bad = [(qid, s, want, grade(d, qs[qid], s)) for qid, s, want in CASES if grade(d, qs[qid], s) != want]
    for qid, s, want, got in bad:
        print(f"FAIL {qid} want {want} got {got}: {s}")
    cbad = [(k, f, want, fact_reading(k, f)) for k, f, want in CENSUS_CASES if fact_reading(k, f) != want]
    for k, f, want, got in cbad:
        print(f"FAIL census {k} want {want} got {got}: {f}")
    tags = [tagged_text(d, m) for m in d["messages"]]
    tbad = [] if (tags[0] == d["messages"][0]["text"] and tags[4].startswith("[in thread: Next up is the Porto office move.] ")
                  and tags[9].startswith("[in thread: Kicking off planning for the Leeds office move today.] ")) else ["tag"]
    print(f"grader cases: {len(CASES) - len(bad)}/{len(CASES)} pass · census cases: {len(CENSUS_CASES) - len(cbad)}/"
          f"{len(CENSUS_CASES)} · thread tags: {'ok' if not tbad else 'FAIL'}")
    return 1 if bad or cbad or tbad else 0


# --------------------------------------------------------------------------- census: whose value each fact holds

def census_copy(d: dict, facts: list[str]) -> dict:
    out: dict = {"facts": len(facts), "values": {}}
    for v in d["values"]:
        readings = [(fact_reading(v["id"], f), f) for f in facts]
        readings = [(r, f) for r, f in readings if r]
        own = [f for r, f in readings if r == v["move"]]
        other = [(r, f) for r, f in readings if r in MOVE_RE and r != v["move"]]
        prev = [f for r, f in readings if r == "previously"]
        none = [f for r, f in readings if r == "none"]
        if own and not other:
            status = "right"
        elif other and not own:
            status = "other"
        elif own and other:
            status = "both"
        elif prev:
            status = "previously"
        elif none:
            status = "unnamed"
        else:
            status = "missing"
        shown = (other[0][1] if other else "") or (own[0] if own else "") or (prev[0] if prev else "") or (none[0] if none else "")
        out["values"][v["id"]] = dict(status=status, under=sorted({r for r, _ in other}), shown=shown,
                                      move=move(d, v["move"])["name"], label=v["label"], show=v["show"])
    out["naming"] = {m["id"]: sum(1 for f in facts if m["id"] in moves_named(f)) for m in d["moves"]}
    out["right"] = sum(1 for x in out["values"].values() if x["status"] == "right")
    out["total"] = len(out["values"])
    return out


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for all three, and no arm letter in it: a project's name is context for extraction.
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} workplace ops",
                                       "--custom-id", cid(c), "--description", f"The {d['channel']} channel. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects with the same name: A gets the channel as sent, B one conversation per thread, C each reply "
         "tagged with its thread")
    n = 0
    for c in COPIES:
        for p in d["people"]:
            world["actors"][f"{c}-{p['key']}"] = make_actor(cli, f"{c}-{p['key']}", p["display"],
                                                            f"{p['role'].capitalize()} at {d['org']}. Demo data.", echo=n == 0)
            n += 1
    note(f"{', '.join(p['display'] + ' (' + p['role'] + ')' for p in d['people'])} — one pair per project, so the three "
         "memories share nothing")
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"Workplace assistant ({COPY_NAME[c]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what the workplace assistant reads through MemoryRouter")
    emit("world", "", **world)
    return world


def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, k) for k in actor_keys(d)))


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


# --------------------------------------------------------------------------- step 3: the channel

def show_channel(d: dict) -> None:
    emit("text", f"\n    {d['channel']} — three threads, each started by a message that names the move:")
    for th in d["moves"]:
        emit("text", "")
        for m in [m for m in d["messages"] if m["thread"] == th["id"]]:
            lead = "    " if roots(d)[th["id"]]["id"] == m["id"] else "      └ "
            emit("text", f"    {m['ts'][5:10]} {hm(m['ts'])}  {lead}{person(d, m['by'])['display']:<12} {m['text']}")
    emit("channel", "", rows=[dict(m, person=person(d, m["by"])["display"], root=roots(d)[m["thread"]]["id"] == m["id"],
                                   tagged=tagged_text(d, m)) for m in d["messages"]])
    emit("text", "\n    Posted in this order (the Leeds update came the next day, under the Leeds thread). The assistant "
                 "saves every message the moment it is posted:")
    emit("text", "      A  one conversation for the channel, messages as sent")
    emit("text", "      B  one conversation per Slack thread, messages as sent")
    emit("text", "      C  one conversation for the channel, each reply tagged with its thread's first message, e.g.")
    emit("text", wrap(tagged_text(d, d["messages"][4]), "         C | ", 116))


def read_batch(cli: CLI, conv: str, msg: str | None, echo: bool, limit: int = 420) -> int:
    """Wait until a message is read: it shows up in its extraction batch and the conversation reports cook_finished.
    Returns the batch size (0 if still unread at the limit)."""
    t0, first = time.time(), echo
    while time.time() - t0 < limit:
        try:
            batch = cli.run("conv", "consumed-messages", conv, "--message", msg, scoped=True, echo=first) if msg else [1]
            first = False
            items = batch.get("items") if isinstance(batch, dict) else batch
            if items:
                st = cli.run("conv", "cook-status", conv, scoped=True, echo=False) or {}
                if st.get("cook_finished"):
                    return len(items)
        except CLIError as e:
            if not e.has(*CLI.TRANSIENT):
                raise
        time.sleep(4)
    return 0


def ingest_copy(cli: CLI, world: dict, copy: str, echo: bool, t0: float) -> dict:
    """One copy of the channel, message by message: append, wait until it is read, then the next one — live."""
    d = load_data()
    convs: dict = {}
    batches = {}
    for i, m in enumerate(d["messages"]):
        key = m["thread"] if copy == "threads" else "channel"
        if key not in convs:
            n = [t["id"] for t in d["moves"]].index(key) + 1 if copy == "threads" else 0
            name = f"{d['channel']} thread {n}" if n else d["channel"]
            actors = ",".join(world["actors"][f"{copy}-{p['key']}"] for p in d["people"])
            convs[key] = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", actors, "--kind", "GROUP",
                                 "--name", name, "--custom-id", cid("conv", copy, key), scoped=True, echo=echo and i == 0)["id"]
        body = tagged_text(d, m) if copy == "tagged" else m["text"]
        mid = append(cli, convs[key], world["actors"][f"{copy}-{m['by']}"], cid("msg", copy, m["id"]), m["ts"], body,
                     echo=echo and i == 0)
        batches[m["id"]] = read_batch(cli, convs[key], mid, echo=echo and i == 0)
        emit("progress", f"    {copy:<8} {m['id']:<4} {person(d, m['by'])['display']:<12} read in a batch of "
                         f"{batches[m['id']] or '?'} · {int(time.time() - t0)} s", copy=copy, msg_id=m["id"],
             batch=batches[m["id"]])
    return dict(convs=convs, batches=batches)


def ingest(cli: CLI, world: dict) -> dict:
    t0 = time.time()
    with ThreadPoolExecutor(3) as pool:
        out = dict(zip(COPIES, pool.map(lambda c: ingest_copy(cli, world, c, c == COPIES[0], t0), COPIES)))
    world["convs"] = {c: v["convs"] for c, v in out.items()}
    late = [f"{c}/{k}" for c, v in out.items() for k, b in v["batches"].items() if not b]
    if late:
        note(f"still extracting after 420 s: {', '.join(late)} — read as they stand")
    sizes = sorted({b for v in out.values() for b in v["batches"].values()})
    note(f"30 messages in 5 conversations, all read in {int(time.time() - t0)} s; batch sizes seen: {sizes} "
         "(1 = each message was read on its own, as in a live chat)")
    time.sleep(8)
    return {c: v["batches"] for c, v in out.items()}


# --------------------------------------------------------------------------- step 4: what memory holds

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, copy in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][copy], echo=echo and n == 0)]
        out[copy] = dict(all=facts, **census_copy(d, facts))
    return out


CELL = {"right": "✓ {move}", "other": "✗ under {under}", "both": "✓ {move} + ✗ {under}", "previously": "✗ only as 'previously'",
        "unnamed": "· no move named", "missing": "· not in memory"}


def census_cell(v: dict) -> str:
    return CELL[v["status"]].format(move=v["move"], under="/".join(x.title() for x in v["under"]))


def show_census(d: dict, cs: dict) -> None:
    emit("text", f"\n    {'value (said in the thread of)':<36}" + "".join(f"{a + '  ' + COPY_NAME[ARM_COPY[a]][:22]:<30}" for a in "ABC"))
    for v in d["values"]:
        emit("text", f"    {v['show'] + ' — ' + move(d, v['move'])['name'] + ' ' + v['label']:<36}"
                     + "".join(f"{census_cell(cs[ARM_COPY[a]]['values'][v['id']]):<30}" for a in "ABC"))
    emit("text", f"    {'facts that name the move':<36}" + "".join(
        f"{', '.join(move(d, k)['name'] + ' ' + str(n) for k, n in cs[ARM_COPY[a]]['naming'].items()):<30}" for a in "ABC"))
    for a in "ABC":
        c = cs[ARM_COPY[a]]
        emit("text", f"    {a}: {c['facts']} facts; {c['right']}/{c['total']} values stored under the right move")
    wrong = [(v, cs["sent"]["values"][v["id"]]) for v in d["values"] if cs["sent"]["values"][v["id"]]["status"] in ("other", "both", "previously")]
    if wrong:
        emit("text", "\n    What A's facts say, word for word:")
        for f in dict.fromkeys(x["shown"] for _, x in wrong):
            emit("text", wrap(f, "      · ", 118))
    emit("census", "", **cs)


# --------------------------------------------------------------------------- step 5: ask

def system_prompt(d: dict) -> str:
    return d["system"].format(org=d["org"], channel=d["channel"], today=today(d).strftime("%A %Y-%m-%d"))


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
        bd = world["boundaries"][ARM_COPY[arm]] if arm != "0" else None
        r = router(cli, q["ask"], system, bd, echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=q["id"], arm=arm, run=i + 1, grade=None, empty=False, reply="")
        empty = arm != "0" and r["prompt_tokens"] <= BASE[(q["ask"], system)]
        return dict(item=q["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty,
                    grade=arm_grade(d, arm, q, r["reply"]))
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers per memory; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    for q in qs:
        mine = [r for r in rows if r["item"] == q["id"]]
        marks = " ".join(mark(r) for r in mine)
        pick = next((r for r in mine if r.get("grade") == "swap"), None) or next((r for r in mine if r.get("reply")), None)
        first = answer_body(pick["reply"]) if pick else ""
        emit("text", f"    {q['what']:<30} {marks:<8} " + (first[:76] + "…" if len(first) > 77 else first))
        emit("ask_row", "", arm=arm, item=q["id"], rows=mine, truth=q["truth"])
    return rows


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    t = dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
             empty=sum(1 for r in rows if r.get("grade") and r.get("empty")))
    for g in MARK:
        t[g] = sum(1 for r in live if r["grade"] == g)
    return t


def show_example(rows: list[dict]) -> None:
    """One question through every memory: the A reply that gives another move's value, set beside B and C."""
    d = load_data()
    q = next((x for x in questions(d) if any(r["arm"] == "A" and r["item"] == x["id"] and r.get("grade") == "swap" for r in rows)), None)
    if not q:
        note("no answer through A gave another move's value this run — nothing to set side by side")
        return
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("reply")]
        r = next((r for r in mine if (a == "A") == (r["grade"] == "swap")), None) or (mine[0] if mine else None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{q['ask']}”  (truth: {q['truth']})")
            emit("text", wrap(r["reply"], f"    {mark(r):>3} | ", 104))
            emit("example", "", arm=a, row=r, ask=q["ask"], truth=q["truth"])


def score(rows: list[dict]) -> dict:
    d = load_data()
    qs = questions(d)
    out: dict = {"answers": {}, "by_item": {}}
    emit("text", f"    per question, right / graded ({RUNS} answers per memory; ∅ = MemoryRouter attached nothing, not graded):")
    emit("text", f"      {'':<40}" + "".join(f"{a:<13}" for a in ARMS))
    for q in qs:
        cells = {}
        for a in ARMS:
            mine = [r for r in rows if r["arm"] == a and r["item"] == q["id"] and r.get("grade")]
            live = [r for r in mine if not r.get("empty")]
            cells[a] = dict(ok=sum(1 for r in live if r["grade"] == "ok"), swap=sum(1 for r in live if r["grade"] == "swap"),
                            n=len(live), empty=len(mine) - len(live), marks=" ".join(mark(r) for r in mine))
        out["by_item"][q["id"]] = cells
        cell = lambda c: f"{c['ok']}/{c['n']}" + (f" ⇄{c['swap']}" if c["swap"] else "") + (f" ∅{c['empty']}" if c["empty"] else "")  # noqa: E731
        emit("text", f"      {(q['id'] + ' ' + q['what'])[:39]:<40}" + "".join(f"{cell(cells[a]):<13}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a])
    t = out["answers"]
    emit("text", f"    {'':<40}{'right':>9}{'⇄':>4}{'✗':>4}{'?':>4}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<40}{x['ok']:>5}/{x['n']:<3}{x['swap']:>4}{x['wrong']:>4}{x['unknown']:>4}"
                     + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    empties = [(r["arm"], r["item"], answer_body(r["reply"])) for r in rows if r.get("empty") and r.get("reply")]
    if empties:
        emit("text", f"\n    ∅ — MemoryRouter attached nothing, so the model answered on its own ({len(empties)} answers, not graded):")
        for (a, item, body), n in Counter(empties).items():
            emit("text", wrap(f"{a} {item}  {body}" + (f"  (×{n})" if n > 1 else ""), "      · ", 116))
    emit("text", "\n    ✓ right (Q8: says it is not recorded) · ⇄ another move's value given as this one's · ✗ wrong · "
                 "? says it does not know · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s workplace memory, three projects")
    world = setup(cli)

    banner(3, TOTAL, f"{d['channel']} — ten messages, saved the moment each is posted")
    show_channel(d)
    batches = ingest(cli, world)

    banner(4, TOTAL, "What memory now holds — whose date, floor and budget each fact says")
    cs = census(cli, world)
    show_census(d, cs)

    rows: list[dict] = []
    banner(5, TOTAL, "A week later: ask the workplace assistant — no memory and three memories, the same eight questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == "A")
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(6, TOTAL, "The score — per question and per memory")
    sc = score(rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cs, batches=batches, answers=rows, score=sc, tokens=tok,
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
    """Re-read the three memories: which move each date, floor and budget is stored under."""
    d = load_data()
    world = load_world(cli)
    cs = census(cli, world)
    show_census(d, cs)
    emit("memory_view", "", census=cs)
    return cs


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once with no memory and once through each memory; graded when it is one of the eight."""
    d = load_data()
    world = load_world(cli)
    q = next((q for q in questions(d) if q["ask"].lower() == question.strip().lower()), None)
    rows = []
    system = system_prompt(d)
    for n, a in enumerate(ARMS):
        base = baseline(cli, question, system, echo=n == 0)
        bd = world["boundaries"].get(ARM_COPY[a]) if a != "0" else None
        r = router(cli, question, system, bd, echo=n == 1)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = a != "0" and r["prompt_tokens"] <= base
        g = arm_grade(d, a, q, r["reply"]) if q else None
        head = "∅ nothing attached" if empty else (f"{MARK[g]} {GRADE_NAME[g]}" if g else "")
        emit("text", f"\n  {ARM_LABEL[a]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=a, reply=r["reply"], empty=empty, grade=g))
    if q:
        emit("text", f"\n  truth: {q['truth']}")
    emit("asked", "", question=question, item=q and q["id"], rows=rows, truth=q and q["truth"])
    return rows


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    bs = demo_boundaries(cli, echo=not quiet)
    for b in bs:
        cli.run("boundary", "delete", b["id"], echo=False)
    # Conversations go explicitly, found by custom_id (do not rely on `proj delete` taking them along).
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
        note(f"deleted {len(bs)} boundaries, {len(convs)} conversations, {nfacts} facts, {n_act} actors (facts first: a "
             f"deleted actor's facts could never be removed) and {nproj} projects" if (bs or convs or n_act or nproj) else "nothing to delete")
    # The report is kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    rep = OUT / "report.json"
    if rep.exists():
        (OUT / "runs").mkdir(exist_ok=True)
        rep.rename(OUT / "runs" / f"report-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")


EXAMPLE = "When does the Leeds office move happen?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup", "selftest"],
                    help="run = full demo (default); memory = re-read which move each value is stored under in A, B "
                         "and C; ask \"<question>\" = one question with no memory and through A, B and C; "
                         "cleanup = delete everything; selftest = the grader's offline cases (no account needed)")
    ap.add_argument("args", nargs="*", help="for ask: the question")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "selftest":
        sys.exit(selftest())
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "memory":
        banner(2, 2, "Which move each date, floor and budget is stored under")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the workplace assistant: no memory, then A, B and C")
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

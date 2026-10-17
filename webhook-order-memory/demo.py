#!/usr/bin/env python3
"""webhook-order-memory — a late webhook retry turns the account in memory back to how it was.

Tallowby (a fictional SaaS company) writes its payment provider's webhooks into memory, and its billing support
assistant answers from that memory through MemoryRouter. Webhooks are delivered at least once and not in order: when
Tallowby's endpoint was down for a day, the provider retried what it could not deliver, so for eight customers the
newer event arrived first and the older one 27 hours later. Each event is written the way most handlers write it —
one message, stamped with the time it arrived.

MemoryLake reads the late, older event as the newest news: the fact about the account is rewritten in place to the
old value ("seat count is set to 40 seats (as of 2026-10-17, previously 25 seats as of 2026-10-16)"), and the assistant
answers with it. Three memories, the same ten customers, read through MemoryRouter: A messages stamped with the time
they arrived · B the same messages stamped with the time the event was created · C B's facts, copied word for word,
plus one fact per customer that the app keeps itself: the value from the most recently *created* event, so an older
event that arrives later never changes it — and, for each such event, a history fact saying it happened.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 110 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-wom"  # memorylake-usecases / webhook-order-memory


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
COPIES = ("delivered", "created", "state")   # the three projects
COPY_NAME = {"delivered": "stamped with the time each event arrived",
             "created": "stamped with the time each event was created",
             "state": "event-time facts + the app's state and history facts"}
ARMS = ("A", "B", "C")
ARM_LABEL = {"A": "A  stamped when it arrived", "B": "B  stamped when it was created", "C": "C  B + the app's state facts"}
ARM_COPY = {"A": "delivered", "B": "created", "C": "state"}
ARM_OF = {c: a for a, c in ARM_COPY.items()}
MARK = {"ok": "✓", "stale": "↺", "unknown": "?", "other": "–"}
GRADE_NAME = {"ok": "the current value", "stale": "the value before the newer event (the late retry's)",
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


# --------------------------------------------------------------------------- step 3: the deliveries

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
    return json.loads((DATA / "events.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def actor_keys(d: dict) -> list[str]:
    return [d["bot"]["key"]]


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join((text or "").split()), width=width, initial_indent=indent, subsequent_indent=indent)


def today(d: dict) -> date:
    return date.fromisoformat(d["today"])


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def utc(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%SZ")


def short(x: datetime) -> str:
    return x.strftime("%a %d %b %H:%M")


def customer(d: dict, key: str) -> dict:
    return next(c for c in d["customers"] if c["id"] == key)


def deliveries(d: dict) -> list[dict]:
    """When each event reached Tallowby. In order: a minute after it was created. Late: the newer event arrived on
    time; the older one failed while the endpoint was down and the provider's retry got through 27 hours after the
    newer one was created."""
    out = []
    for c in d["customers"]:
        old, new = c["events"]
        if c["order"] == "late":
            pairs = [(new, ts(new["created"]) + timedelta(minutes=1), False),
                     (old, ts(new["created"]) + timedelta(hours=d["retry_hours"]), True)]
        else:
            pairs = [(e, ts(e["created"]) + timedelta(minutes=1), False) for e in (old, new)]
        for e, at, retry in pairs:
            out.append(dict(customer=c["id"], name=c["name"], event=e["id"], type=e["type"], created=e["created"],
                            delivered=utc(at), retry=retry, value=e["value"], body=e["text"]))
    return sorted(out, key=lambda x: x["delivered"])


def message_text(x: dict) -> str:
    """What a typical webhook handler writes: the event type, its id and what happened — not when it was created."""
    return f"Webhook {x['type']} {x['event']}: {x['body']}"


def questions(d: dict) -> list[dict]:
    qs = [dict(id=c["id"], what=f"{c['name']} · {c['field']}", ask=c["ask"], values=c["values"], current=c["current"],
               stale=c["stale"], order=c["order"]) for c in d["customers"]]
    h = d["history"]
    qs.append(dict(id=h["id"], what=f"{customer(d, h['customer'])['name']} · canceled this month?", ask=h["ask"],
                   values=h["values"], current=h["current"], stale="no", order="history"))
    return qs


# --------------------------------------------------------------------------- reading a fact or a reply

AS_OF = re.compile(r"\((?:as of|on) (\d{4}-\d\d-\d\d)|\bon (\d{4}-\d\d-\d\d)\b|\bas of (\d{4}-\d\d-\d\d)\b")
PAREN_DATES = re.compile(r"\((?:as of|on) [^)]*\)")
FROM_PHRASE = re.compile(r"\bfrom (?:the )?[\w@.$,'-]+(?: plan| seats?| card| ending \d+)?", re.I)
PREVIOUSLY = re.compile(r"\bpreviously\b|\bpreviously,|\bformerly\b", re.I)
LEAD_UNKNOWN = re.compile(r"^\W*(?:answer:\s*)?unknown\b", re.I)
NOT_KNOWN = re.compile(r"\bunknown\b|\bno (?:record|information|data|details?)\b|\bdon'?t have\b|\bdo not have\b|"
                       r"\bnot (?:sure|available|specified|recorded|known)\b", re.I)
NAME_TOKEN = {"ostrel": r"Ostrel|INV-2207", "fenwick": r"Fenwick|fenwicklark"}


def name_re(c: dict) -> str:
    return NAME_TOKEN.get(c["id"], re.escape(c["name"].split()[0]))


def fact_date(text: str) -> str | None:
    m = AS_OF.search(text or "")
    return next((g for g in m.groups() if g), None) if m else None


def asserted(values: dict, text: str) -> str | None:
    """The value a sentence states as the case: the part before any 'previously …', without 'from X' phrases and
    without the (as of …) date notes; of the values it names, the one named first."""
    t = PREVIOUSLY.split(text or "")[0]
    t = PAREN_DATES.sub(" ", t)
    t = FROM_PHRASE.sub(" ", t)
    best = None
    for v, rx in values.items():
        # ASCII word boundaries: a fact extracted in Japanese puts a kana right before "finance@" or "Team".
        m = re.search(rx, t, re.I | re.A)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), v)
    return best[1] if best else None


def answer_line(reply: str) -> str:
    m = re.search(r"answer\s*:\s*(.*)", reply or "", re.I)
    return m.group(1) if m else (reply or "")


def grade(q: dict, reply: str) -> str:
    """One grade per reply, read from its 'Answer:' line: ✓ the current value · ↺ the stale one · ? Unknown · – neither."""
    line = answer_line(reply)
    if LEAD_UNKNOWN.match(line):
        return "unknown"
    v = asserted(q["values"], line)
    if v == q["current"]:
        return "ok"
    if v == q["stale"]:
        return "stale"
    return "unknown" if NOT_KNOWN.search(line) else "other"


def census_one(c: dict, facts: list[str]) -> dict:
    """What the facts about one customer say the current value is: the value stated by the latest-dated fact."""
    mine = [f for f in facts if re.search(name_re(c), f, re.I)]
    dated = [(fact_date(f) or "", asserted(c["values"], f), f) for f in mine]
    dated = [x for x in dated if x[1]]
    rewritten = [f for f in mine if PREVIOUSLY.search(f) and asserted(c["values"], f) == c["stale"]
                 and re.search(c["values"][c["current"]], PREVIOUSLY.split(f, 1)[1], re.I | re.A)]
    if not dated:
        return dict(state="missing", facts=mine, top=[], rewritten=rewritten, has_current=False, has_stale=False)
    latest = max(x[0] for x in dated)
    top = [x for x in dated if x[0] == latest]
    vals = {x[1] for x in top}
    state = "tie" if len(vals) > 1 else ("current" if vals == {c["current"]} else "stale" if vals == {c["stale"]} else "missing")
    return dict(state=state, date=latest, top=[x[2] for x in top], facts=mine, rewritten=rewritten,
                has_current=any(x[1] == c["current"] for x in dated), has_stale=any(x[1] == c["stale"] for x in dated))


# --------------------------------------------------------------------------- step 2: set up

def setup(cli: CLI) -> dict:
    d = load_data()
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}, "convs": {}}
    for n, c in enumerate(COPIES):
        # The same plain name for all three, and no arm letter in it: a project's name is context for extraction.
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} billing events",
                                       "--custom-id", cid(c), "--description", f"{d['org']} billing events. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three projects with the same name: A and B get the webhook messages, C gets B's facts plus the app's state and history facts")
    b = d["bot"]
    world["actors"][b["key"]] = make_actor(cli, b["key"], b["display"], f"{b['role']}. Demo data.", echo=True,
                                           actor_type="ASSISTANT")
    for n, c in enumerate(COPIES):
        bd = cli.run("boundary", "create", "--name", f"Billing assistant ({COPY_NAME[c]})",
                     "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = bd["id"]
    note("one boundary per project — what the billing assistant reads through MemoryRouter")
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


# --------------------------------------------------------------------------- step 3: the deliveries

def show_deliveries(d: dict) -> None:
    emit("text", f"\n    {d['outage']}")
    emit("text", f"\n    {'customer':<15}{'event (what happened)':<62}{'created':<17}{'arrived':<17}")
    rows = []
    for c in d["customers"]:
        for x in sorted([x for x in deliveries(d) if x["customer"] == c["id"]], key=lambda x: x["created"]):
            late = "  ← retry, after the newer one" if x["retry"] else ""
            emit("text", f"    {c['name'] if x is not None else '':<15}{(x['type'] + ' → ' + x['value'])[:60]:<62}"
                         f"{short(ts(x['created'])):<17}{short(ts(x['delivered'])):<17}{late}")
            rows.append(x)
    emit("deliveries", "", rows=rows)


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


def ingest_one(cli: CLI, world: dict, copy: str, c: dict, echo: bool, t0: float) -> tuple[str, bool]:
    d = load_data()
    bot = world["actors"][d["bot"]["key"]]
    conv = cli.run("conv", "create", "--project", world["projects"][copy], "--actors", bot, "--kind", "GROUP",
                   "--name", f"{c['name']} billing events", "--custom-id", cid("conv", copy, c["id"]), scoped=True, echo=echo)
    ok = True
    for i, x in enumerate(x for x in deliveries(d) if x["customer"] == c["id"]):
        stamp = x["delivered"] if copy == "delivered" else x["created"]
        mid = append(cli, conv["id"], bot, f"{x['event']}", stamp, message_text(x), echo=echo and i == 0)
        # One event at a time: the next delivery is written after this one has been read, as a handler would.
        ok = wait_read(cli, conv["id"], mid, echo=echo and i == 0) and ok
    emit("progress", f"    {copy:<9} {c['name']}: both events read · {int(time.time() - t0)} s", copy=copy, customer=c["id"])
    return conv["id"], ok


def ingest(cli: CLI, world: dict) -> None:
    d = load_data()
    show_deliveries(d)
    emit("text", "\n    One GROUP conversation per customer, the events in the order they arrived, each read before the "
                 "next is written.\n    A stamps each message with the time it arrived; B with the time the event was "
                 "created. Same text in both.")
    emit("text", "    " + message_text(next(x for x in deliveries(d) if x["retry"])))
    t0 = time.time()
    jobs = [(copy, c) for copy in ("delivered", "created") for c in d["customers"]]
    with ThreadPoolExecutor(10) as pool:
        res = list(pool.map(lambda j: ingest_one(cli, world, j[0], j[1], j is jobs[0], t0), jobs))
    world["convs"] = {f"{j[0]}-{j[1]['id']}": r[0] for j, r in zip(jobs, res)}
    late = [f"{j[0]}/{j[1]['name']}" for j, r in zip(jobs, res) if not r[1]]
    if late:
        note(f"still extracting after 300 s: {', '.join(late)} — read as they stand")
    note(f"20 conversations, 40 messages, all read in {int(time.time() - t0)} s")
    time.sleep(8)


# --------------------------------------------------------------------------- step 4: what memory says now

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, copy in enumerate(("delivered", "created")):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][copy], echo=echo and n == 0)]
        out[copy] = dict(all=facts, customers={c["id"]: census_one(c, facts) for c in d["customers"]})
    return out


def show_census(d: dict, cs: dict) -> None:
    emit("text", f"\n    {'customer':<15}{'field':<22}{'true now':<30}{'A · as arrived':<16}{'B · as created':<16}")
    for c in d["customers"]:
        a, b = cs["delivered"]["customers"][c["id"]], cs["created"]["customers"][c["id"]]
        tag = "" if c["order"] == "late" else "   (arrived in order)"
        emit("text", f"    {c['name']:<15}{c['field']:<22}{c['current']:<30}{STATE_MARK[a['state']]:<16}{STATE_MARK[b['state']]:<16}{tag}")
    for copy, arm in (("delivered", "A"), ("created", "B")):
        stale = [(c, cs[copy]["customers"][c["id"]]) for c in d["customers"] if cs[copy]["customers"][c["id"]]["state"] != "current"]
        if not stale:
            continue
        emit("text", f"\n    {arm}: the latest fact about each customer not shown as current")
        for c, s in stale:
            shown = (s["rewritten"] or s["top"] or s["facts"] or ["(no fact names this customer's " + c["field"] + ")"])[0]
            emit("text", wrap(f"{c['name']} [{STATE_MARK[s['state']]}]: {shown}", "      · ", 118))
    for copy, arm in (("delivered", "A"), ("created", "B")):
        cc = cs[copy]["customers"]
        late = [c for c in d["customers"] if c["order"] == "late"]
        cs[copy]["tally"] = dict(
            current=sum(1 for c in late if cc[c["id"]]["state"] == "current"),
            stale=sum(1 for c in late if cc[c["id"]]["state"] == "stale"),
            tie=sum(1 for c in late if cc[c["id"]]["state"] == "tie"),
            missing=sum(1 for c in late if cc[c["id"]]["state"] == "missing"),
            rewritten=sum(1 for c in late if cc[c["id"]]["rewritten"]),
            older_kept=sum(1 for c in late if cc[c["id"]]["has_stale"]),
            control=sum(1 for c in d["customers"] if c["order"] != "late" and cc[c["id"]]["state"] == "current"),
            facts=len(cs[copy]["all"]))
    for copy, arm in (("delivered", "A"), ("created", "B")):
        t = cs[copy]["tally"]
        emit("text", f"\n    {arm}: of the 8 customers whose older event arrived late — current {t['current']} · stale "
                     f"{t['stale']} · tie {t['tie']} · missing {t['missing']}; a fact rewritten to the old value "
                     f"(with the new one as 'previously') for {t['rewritten']}; the older event is in memory at all for "
                     f"{t['older_kept']}. In-order customers current: {t['control']}/2. ({t['facts']} facts)")
    emit("census", "", **{k: dict(customers=v["customers"], tally=v["tally"], all=v["all"]) for k, v in cs.items()})


# --------------------------------------------------------------------------- step 5: C — the app keeps the state

def app_state(d: dict) -> tuple[dict, list[dict]]:
    """The deliveries in the order they arrived; per customer and field, keep the value of the most recently created
    event. An event created earlier than the one already applied is logged and not applied."""
    state: dict = {}
    log = []
    for x in deliveries(d):
        cur = state.get(x["customer"])
        if cur and x["created"] < cur["created"]:
            log.append(dict(x, applied=False, kept=cur["event"]))
            continue
        state[x["customer"]] = x
        log.append(dict(x, applied=True))
    return state, log


def state_text(d: dict, c: dict, x: dict) -> str:
    return (f"Billing state for {c['name']}, {c['field']}: {x['value']}. Set by {x['type']} {x['event']}, created "
            f"{ts(x['created']):%Y-%m-%d %H:%M} UTC, the most recently created event for this field. Events created "
            f"earlier still happened and stay in the history; they do not change the current value, even when they "
            f"arrived later.")


def history_text(d: dict, c: dict, x: dict) -> str:
    return (f"Billing history for {c['name']}, {c['field']}: {x['value']}, from {x['type']} {x['event']}, created "
            f"{ts(x['created']):%Y-%m-%d %H:%M} UTC. It happened, but it arrived on {ts(x['delivered']):%Y-%m-%d} after a "
            f"newer event had been applied, so it is history, not the current value.")


def write_state(cli: CLI, world: dict) -> dict:
    d = load_data()
    state, log = app_state(d)
    emit("text", "\n    The handler's own log, in arrival order:")
    for x in log:
        if x["applied"]:
            emit("text", f"      {short(ts(x['delivered']))}  {x['name']:<14} {x['type']:<31} created {short(ts(x['created']))}  applied → {x['value']}")
        else:
            emit("text", f"      {short(ts(x['delivered']))}  {x['name']:<14} {x['type']:<31} created {short(ts(x['created']))}  "
                         f"NOT applied: older than {x['kept']}")
    src = [f.get("fact") or "" for f in project_facts(cli, world["projects"]["created"], echo=True)]
    src = [s for s in src if s]
    p = world["projects"]["state"]
    if src:
        res = cli.run("fact", "add", *src, "--project", p, scoped=True, echo=True)
        if len((res or {}).get("facts") or []) != len(src):
            die(f"`fact add` returned {len((res or {}).get('facts') or [])} facts for {len(src)} texts")
    pins = [state_text(d, c, state[c["id"]]) for c in d["customers"]]
    hist = [history_text(d, customer(d, x["customer"]), x) for x in log if not x["applied"]]
    cli.run("fact", "add", *pins, *hist, "--project", p, scoped=True, echo=True)
    note(f"C: the {len(src)} facts MemoryLake extracted into B, copied word for word, then {len(pins)} state facts and "
         f"{len(hist)} history facts (one per event it did not apply) that the app wrote")
    for t in pins[:1] + hist[:1]:
        emit("text", wrap(t, "      · ", 118))
    emit("text", f"      · … {len(pins) - 1} more state facts, {len(hist) - 1} more history facts")
    emit("state", "", log=log, copied=len(src), pins=pins, history=hist)
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact add`
    return dict(copied=len(src), pins=len(pins), history=len(hist), ignored=sum(1 for x in log if not x["applied"]))


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
        emit("text", f"    {q['what']:<40} {marks:<8} " + (first[:70] + "…" if len(first) > 71 else first))
        emit("ask_row", "", arm=arm, item=q["id"], rows=mine, current=q["current"], stale=q["stale"])
    return rows


def tally(rows: list[dict], qs: list[dict] | None = None) -> dict:
    qs = qs or questions(load_data())
    order = {q["id"]: q["order"] for q in qs}
    live = [r for r in rows if r.get("grade") and not r.get("empty")]
    t = dict(calls=len([r for r in rows if r.get("grade")]), n=len(live),
             empty=sum(1 for r in rows if r.get("grade") and r.get("empty")))
    for g in MARK:
        t[g] = sum(1 for r in live if r["grade"] == g)
    for part in ("late", "inorder", "history"):
        sub = [r for r in live if order[r["item"]] == part]
        t[part + "_n"], t[part + "_ok"] = len(sub), sum(1 for r in sub if r["grade"] == "ok")
        t[part + "_stale"] = sum(1 for r in sub if r["grade"] == "stale")
    return t


def show_example(rows: list[dict]) -> None:
    """The same question through the three memories, for a customer whose older event arrived late and A went stale."""
    d = load_data()
    qs = questions(d)
    pick = next((q for q in qs if q["order"] == "late" for r in rows
                 if r["arm"] == "A" and r["item"] == q["id"] and r.get("grade") == "stale"), None)
    if not pick:
        note("no late-retry question went stale through A this run — nothing to set side by side")
        return
    for a in ARMS:
        mine = [r for r in rows if r["arm"] == a and r["item"] == pick["id"] and r.get("reply")]
        r = next((r for r in mine if (a == "A") == (r["grade"] == "stale")), None) or (mine[0] if mine else None)
        if r:
            emit("text", f"\n    {ARM_LABEL[a]} · “{pick['ask']}”  (now: {pick['current']}; before the newer event: {pick['stale']})")
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
        tag = {"late": "", "inorder": " (in order)", "history": " (history)"}[q["order"]]
        emit("text", f"      {(q['what'] + ' = ' + q['current'] + tag)[:55]:<56}" + "  ".join(f"{cell(cells[a]):<11}" for a in ARMS))
    emit("text", "")
    for a in ARMS:
        out["answers"][a] = tally([r for r in rows if r["arm"] == a], qs)
    t = out["answers"]
    emit("text", f"    {'':<32}{'current':>9}{'↺ stale':>9}{'?':>4}{'–':>4}{'late-retry customers':>24}{'in order':>11}{'history':>10}")
    for a in ARMS:
        x = t[a]
        emit("text", f"    {ARM_LABEL[a]:<32}{x['ok']:>5}/{x['n']:<3}{x['stale']:>9}{x['unknown']:>4}{x['other']:>4}"
                     f"{x['late_ok']:>19}/{x['late_n']:<4}{x['inorder_ok']:>7}/{x['inorder_n']:<3}{x['history_ok']:>7}/{x['history_n']:<3}"
                     + (f"   ∅ {x['empty']}" if x["empty"] else ""))
    emit("text", "\n    ✓ the current value · ↺ the value before the newer event — what the late retry carried · ? Unknown · "
                 "– neither · ∅ MemoryRouter attached nothing")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s billing memory, three projects")
    world = setup(cli)

    banner(3, TOTAL, "Twenty webhook events, delivered at least once and out of order")
    ingest(cli, world)

    banner(4, TOTAL, "What memory now says about each account")
    cs = census(cli, world)
    show_census(d, cs)

    banner(5, TOTAL, "C — the app keeps each account's state by event time")
    st = write_state(cli, world)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the billing assistant — three memories, the same eleven questions")
    for a in ARMS:
        emit("text", f"\n  {ARM_LABEL[a]}")
        mine = ask_rows(cli, world, a, echo_first=a == ARMS[0])
        emit("arm_done", "", arm=a, tally=tally(mine))
        rows += mine
    show_example(rows)

    banner(7, TOTAL, "The score — per question and per memory")
    sc = score(rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census={k: dict(customers=v["customers"], tally=v["tally"], all=v["all"]) for k, v in cs.items()},
                   state=st, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
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
    """Re-read A's and B's facts: which value the latest fact about each customer states."""
    d = load_data()
    world = load_world(cli)
    cs = census(cli, world)
    show_census(d, cs)
    emit("memory_view", "", census={k: dict(customers=v["customers"], tally=v["tally"]) for k, v in cs.items()})
    return cs


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each memory; graded when it is one of the eleven."""
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
        emit("text", f"\n  now: {q['current']} · before the newer event: {q['stale']}")
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


EXAMPLE = "How many seats does Quillmere have now?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = re-read what A's and B's facts say about each account; "
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
        banner(2, 2, "What A's and B's facts say about each account now")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f"usage: python3 demo.py ask '<question>'   (e.g. '{EXAMPLE}')")
        banner(2, 2, "One question to the billing assistant, through A, B and C")
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

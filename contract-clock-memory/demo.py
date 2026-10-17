#!/usr/bin/env python3
"""contract-clock-memory — the clock that starts on an event.

Fernbeck Transit (a fictional transit authority) is replacing its fare system. On the day the vendor contract was
signed, the procurement lead read the contract's clocks into the meeting: the pilot runs 90 days from go-live, the
final payment is due 45 days after the pilot ends, the source code goes into escrow within 60 days of go-live, a defect
must be fixed within 30 days of being reported, the warranty runs 12 months from acceptance. Go-live, the defect report
and acceptance happened in later meetings, weeks and months apart. MemoryLake remembers every clock and every event —
as separate facts, and nothing ever joins them: memory holds no due date for any of those clocks.

Three copies of the same four meetings: A the meetings only · B plus the clocks and milestones written as structured
facts · C plus the due dates, worked out by the app in code when each milestone is written. The app then reads every
clock, builds the contract calendar as of today, and asks the programme assistant (MemoryRouter: an ordinary model
call with `--boundary`) for each date.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 40 calls on gpt-4o-mini per run).
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
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ckm"  # memorylake-usecases / contract-clock-memory


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
        if shown[:2] == ["fact", "add"] and len(shown) > 8:
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
RUNS = 2              # answers per question and copy
MAX_TOKENS = 80
COPIES = ("talk", "logged", "resolved")
COPY_LABEL = {"talk": "A  meetings only", "logged": "B  + clocks and milestones", "resolved": "C  + due dates (app)"}
COPY_SHORT = {"talk": "A", "logged": "B", "resolved": "C"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "project.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(str(text).split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent))


def person(d: dict, key: str) -> dict | None:
    return next((p for p in d["people"] if p["key"] == key), None)


def label(p: dict) -> str:
    return f"{p['display']} ({p['role']})"


def add_months(x: date, n: int) -> date:
    y, m = divmod(x.month - 1 + n, 12)
    return date(x.year + y, m + 1, x.day)


def clock_of(d: dict, cid_: str) -> dict:
    return next(c for c in d["clocks"] if c["id"] == cid_)


def milestone(d: dict, key: str) -> dict:
    return next(m for m in d["milestones"] if m["key"] == key)


def anchor_of(d: dict, clock: dict) -> tuple[date, str]:
    """The day a clock starts, and what starts it: a milestone, or (for "@pilot") the day another clock runs out."""
    if clock["from"].startswith("@"):
        prev = clock_of(d, clock["from"][1:])
        return due(d, prev), prev["what"].lower()
    m = milestone(d, clock["from"])
    return date.fromisoformat(m["on"]), m["what"]


def due(d: dict, clock: dict) -> date:
    """The due date, worked out in code: the start day plus the clock's length. This is all the app adds in copy C."""
    start, _ = anchor_of(d, clock)
    if clock.get("months"):
        return add_months(start, clock["months"])
    return start + timedelta(days=clock.get("days", 0) + 7 * clock.get("weeks", 0))


def length_of(clock: dict) -> str:
    for k in ("days", "weeks", "months"):
        if clock.get(k):
            return f"{clock[k]} {k}"
    return ""


def today(d: dict) -> date:
    return datetime.now(ZoneInfo(d["home_tz"])).date()


def show_day(x: date | None) -> str:
    return x.strftime("%a %Y-%m-%d") if x else "–"


def meeting_at(m: dict) -> datetime:
    return datetime.fromisoformat(m["at"].replace("Z", "+00:00"))


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                      "october", "november", "december"], 1)}
_MON = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
MDY = re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)
DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+(20\d\d)\b", re.I)
# The extraction's own stamp at the end of a fact: "(as of 2026-06-10)" or "(on 2026-08-03)". It says when the fact
# was written down, not what it says.
STAMP = re.compile(r"\s*\(?\s*(?:as of|on)\s+(20\d\d-\d\d-\d\d)\s*\)?\s*\.?\s*$", re.I)
SPAN = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|sixteen|thirty|forty-five|sixty|ninety)"
UNIT = r"(?:calendar\s+)?(?:days?|weeks?|months?)"
# "90 days from 2026-08-03": the start is there, the due date is left to whoever reads it.
ANCHOR = re.compile(SPAN + r"(?:\s+|-)" + UNIT + r"\s+(?:from|after|of)\s+(?:the\s+)?$", re.I)
# A period whose start is an event, not a date: "90 days from go-live", "12 months from acceptance".
DURATION = re.compile(r"\b" + SPAN + r"(?:\s+|-)" + UNIT + r"\b", re.I)
# Extraction sometimes writes the fact in Chinese ("Tamsett 在 2026-06-10 起算 16 周后交付…"): the period, and a date that
# is only where the period starts.
DURATION_ZH = re.compile(r"\d+\s*(?:天|周|个月|個月)")
ANCHOR_AFTER = re.compile(r"^\s*(?:\)\s*)?(?:起算|起|开始)")


def dates_in(text: str) -> list[tuple[int, date, bool]]:
    """Every calendar date written in `text`: (position, date, is it only the start of "N days from <date>")."""
    out = []
    for rx, f in ((ISO, lambda m: (int(m[1]), int(m[2]), int(m[3]))),
                  (MDY, lambda m: (int(m[3]), MONTHS[m[1].lower()], int(m[2]))),
                  (DMY, lambda m: (int(m[3]), MONTHS[m[2].lower()], int(m[1])))):
        for m in rx.finditer(text):
            try:
                out.append((m.start(), date(*f(m)), bool(ANCHOR.search(text[max(0, m.start() - 40):m.start()])
                                                         or ANCHOR_AFTER.search(text[m.end():m.end() + 6]))))
            except ValueError:
                pass
    return sorted(out, key=lambda x: x[0])


def body_of(fact: str) -> str:
    return STAMP.sub("", fact).strip()


def stamp_of(fact: str) -> str:
    m = STAMP.search(fact)
    return m.group(1) if m else ""


def about(text: str, clock: dict) -> bool:
    body = body_of(text)
    return bool(re.search(clock["match"], body, re.I)) and not (clock.get("not") and re.search(clock["not"], body, re.I))


def read_fact(text: str, clock: dict) -> tuple[date | None, date | None, bool]:
    """(the due date this fact gives, the start date of an unresolved "N days from <date>", is there a bare period).
    The date that follows the clock's keyword is the one that belongs to it; "the pilot runs 90 days from go-live" has
    a period and no date — the clock is remembered, its due date is not."""
    body = body_of(text)
    k = re.search(clock["match"], body, re.I)
    ds = dates_in(body)
    real = [x for x in ds if not x[2]]
    anchors = [x for x in ds if x[2]]
    period = bool(DURATION.search(body) or DURATION_ZH.search(body))
    after = [x for x in real if k and x[0] >= k.start()]
    # "the pilot runs for 90 days starting from go-live on 2026-08-03": the date after the period is its start.
    # "16 weeks after 2026-06-10 (expected 2026-09-30)": the period already has its start; the next date is the answer.
    if after and period:
        gap = DURATION.search(body, k.start() if k else 0, after[0][0])
        started = gap and any(x[2] and gap.end() <= x[0] < after[0][0] for x in ds)
        if gap and not started and re.search(r"\b(?:from|after|of|since|starting)\b", body[gap.end():after[0][0]], re.I):
            return None, after[0][1], True
    if after:
        return after[0][1], None, period
    if anchors:
        return None, anchors[0][1], True
    return (real[0][1] if real else None), None, period


DATE_LINE = re.compile(r"\bDate:\s*(.+)", re.I)


def answer_date(reply: str) -> date | None:
    """The date after "Date:" in a reply; the first date anywhere when the model left the label out."""
    m = DATE_LINE.search(reply)
    if m:
        ds = dates_in(m.group(1))
        if ds:
            return ds[0][1]
        if re.search(r"unknown|not (?:given|specified|stated|available)|none|n/a", m.group(1), re.I):
            return None
    ds = dates_in(reply)
    return ds[0][1] if ds else None


MARK = {"ok": "✓", "off": "✗", "calc": "~", "nodate": "?", "none": "·"}
MEANING = {"ok": "the right due date", "off": "another date", "calc": "only “N days from <date>”, never worked out",
           "nodate": "the clock, with no date", "none": "not remembered"}


def verdict(want: date, got: date | None, matched: bool, anchor: date | None = None) -> dict:
    if not matched:
        return dict(verdict="none", got="", off="", anchor="")
    if not got:
        return dict(verdict="calc" if anchor else "nodate", got="", off="", anchor=anchor.isoformat() if anchor else "")
    delta = (got - want).days
    return dict(verdict="ok" if delta == 0 else "off", got=got.isoformat(), anchor="",
                off="" if delta == 0 else f"{delta:+d} day{'s' if abs(delta) != 1 else ''}")


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
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['org']} {d['project']} ({COPY_LABEL[c][3:]})",
                                       "--custom-id", cid(c), "--description",
                                       f"PMO memory of the {d['project']}, {COPY_LABEL[c][3:]}. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("three copies of the same project memory: " + " · ".join(COPY_LABEL[c] for c in COPIES))
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}, {d['org']} PMO. Demo data.", echo=n == 0)
    note(f"{len(d['people'])} people as actors: " + " · ".join(label(p) for p in d["people"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Programme assistant ({COPY_LABEL[c][3:]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per copy — what the programme assistant reads through MemoryRouter")
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



def utc(x: datetime) -> str:
    return x.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


CLOCKY = re.compile(r"\b(?:\d+ (?:days?|weeks?|months?)|today|this morning)\b", re.I)


def show_meetings(d: dict) -> None:
    for m in d["meetings"]:
        emit("text", f"\n    {m['id']}  {meeting_at(m):%a %d %b %Y}  “{m['name']}”")
        for who, text in m["lines"]:
            emit("text", wrap(f"{person(d, who)['display']}: {text}", "        ", 108))
    emit("text", "\n    the clocks, and the day each one runs out (worked out here, from the milestones):")
    for c in d["clocks"]:
        start, what = anchor_of(d, c)
        emit("text", f"      · {c['what']:<32} “{c['said']}” — {what} {start.isoformat()} → due {show_day(due(d, c))}")
    emit("meetings", "", meetings=d["meetings"], clocks=[dict(c, due=due(d, c).isoformat(), start=anchor_of(d, c)[0].isoformat(),
                                                             start_what=anchor_of(d, c)[1]) for c in d["clocks"]])


def ingest(cli: CLI, world: dict) -> dict:
    d = load_data()
    show_meetings(d)
    emit("text", "\n    Every copy gets the same four meetings: one GROUP conversation each, every line as its speaker, at the "
                 "meeting's time.")
    convs: dict = {}

    def one(job):
        n, c, m = job
        conv = cli.run("conv", "create", "--project", world["projects"][c], "--actors", ",".join(world["actors"].values()),
                       "--kind", "GROUP", "--name", f"{d['project']} · {m['name']} ({COPY_LABEL[c][3:]})",
                       "--custom-id", cid("conv", c, m["id"]), scoped=True, echo=n == 0)
        t0 = meeting_at(m)
        for i, (who, text) in enumerate(m["lines"]):
            p = person(d, who)
            append(cli, conv["id"], world["actors"][who], f"m{i:02d}", utc(t0 + timedelta(minutes=i)),
                   f"{label(p)}: {text}", echo=n == 0 and i == 0)
        return (c, m["id"]), conv["id"]
    jobs = [(n, c, m) for n, (c, m) in enumerate((c, m) for c in COPIES for m in d["meetings"])]
    with ThreadPoolExecutor(6) as pool:
        convs.update(dict(pool.map(one, jobs)))
    note(f"{len(d['meetings'])} meetings × {len(COPIES)} copies = {len(convs)} GROUP conversations, each line as its speaker, "
         "labelled `Name (role): …`; MemoryLake now extracts facts in the background")
    world["convs"] = convs
    wait_cooked(cli, world)
    return convs


def structured_texts(d: dict, c: str) -> list[tuple[str, dict]]:
    """What the app itself writes, besides the meetings. B: the contract's clocks (stable parameters) and the milestones
    (events, with their dates) — each on its own, as the use case page lays out. C: the same, plus every due date,
    worked out in code from the clock and the milestone that starts it."""
    if c == "talk":
        return []
    out = [(f"Contract clock ({d['vendor']} contract schedule): {k['clock']}.",
            dict(kind="clock", clock=k["id"], length=length_of(k), starts=k["from"])) for k in d["clocks"]]
    out += [(f"Milestone: {m['what']} on {m['on']}.", dict(kind="milestone", milestone=m["key"], on=m["on"]))
            for m in d["milestones"]]
    if c == "resolved":
        for k in d["clocks"]:
            start, _ = anchor_of(d, k)
            frm = (f"after the {k['from'][1:]} ends" if k["from"].startswith("@")
                   else f"from {milestone(d, k['from'])['short']}")
            out.append((f"Due date: {k['due'].format(due=due(d, k).isoformat())} ({length_of(k)} {frm} on {start.isoformat()}).",
                        dict(kind="due", clock=k["id"], due=due(d, k).isoformat(), start=start.isoformat(), length=length_of(k))))
    return out


def write_structured(cli: CLI, world: dict) -> dict:
    d = load_data()
    written: dict = {}
    for n, c in enumerate(COPIES):
        texts = structured_texts(d, c)
        if not texts:
            continue
        res = cli.run("fact", "add", *[t for t, _ in texts], "--project", world["projects"][c], scoped=True, echo=True)
        facts = (res or {}).get("facts") or []
        if len(facts) != len(texts):
            die(f"`fact add` returned {len(facts)} facts for {len(texts)} texts")
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda fm: cli.run("fact", "update", fm[0]["id"], "--project", world["projects"][c], "--metadata",
                                             json.dumps(fm[1]), scoped=True, echo=fm[0] is facts[0] and n == 1),
                          zip(facts, [m for _, m in texts])))
        kinds = {}
        for _, m in texts:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        written[c] = kinds
        note(f"{COPY_LABEL[c]}: " + " · ".join(f"{v} {k}{'s' if v != 1 else ''}" for k, v in kinds.items())
             + " (`fact add` keeps the text as given; the kind goes into metadata)")
        for t, m in texts:
            if m["kind"] == "due":
                emit("text", wrap(t, "      ", 108))
    emit("structured", "", written=written, texts={c: [t for t, _ in structured_texts(d, c)] for c in COPIES})
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`
    return written


def wait_cooked(cli: CLI, world: dict, limit: int = 600) -> None:
    """Every conversation reports cook_finished, and every copy has facts to list (the first can come before the second)."""
    t0, pending, first = time.time(), dict(world["convs"]), True
    while time.time() - t0 < limit:
        for k, conv in list(pending.items()):
            try:
                st = cli.run("conv", "cook-status", conv, scoped=True, echo=first) or {}
                first = False
                if st.get("cook_finished"):
                    pending.pop(k)
            except CLIError as e:
                if not e.has(*CLI.TRANSIENT):
                    raise
        empty = [c for c in COPIES if not project_facts(cli, world["projects"][c])] if not pending else []
        done = len(world["convs"]) - len(pending)
        emit("progress", f"    extracted {done}/{len(world['convs'])} conversations · {int(time.time() - t0)} s", done=done,
             total=len(world["convs"]))
        if not pending and not empty:
            break
        time.sleep(8)
    else:
        left = [f"{COPY_SHORT[c]} {f}" for c, f in sorted(pending)] or [f"copy {COPY_SHORT[c]} has no facts" for c in COPIES
                                                                       if not project_facts(cli, world["projects"][c])]
        note(f"still extracting after {limit} s: {', '.join(left)} — those items are graded as they stand")
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`


# --------------------------------------------------------------------------- step 4: what memory holds for each clock

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per copy and clock: the facts about it, the due date each one gives, and the verdict against the real one."""
    d = load_data()
    out: dict = {}
    for n, c in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][c], echo=echo and n == 0)]
        per: dict = {}
        for k in d["clocks"]:
            hits = [f for f in facts if about(f, k)]
            read = [(f, *read_fact(f, k)) for f in hits]
            # the fact that is graded comes first: one with a date, else one with an unresolved start, else any
            read.sort(key=lambda x: (x[1] is None, x[2] is None))
            got, anchor = (read[0][1], read[0][2]) if read else (None, None)
            per[k["id"]] = dict(facts=[x[0] for x in read][:3], stamp=stamp_of(read[0][0]) if read else "",
                                **verdict(due(d, k), got, bool(hits), anchor))
        events = {m["key"]: [f for f in facts if re.search(EVENT_RX[m["key"]], body_of(f), re.I)
                             and not any(about(f, k) for k in d["clocks"])][:2] for m in d["milestones"]}
        stale = [f for f in facts if STALE.search(f)]
        out[c] = dict(facts=len(facts), items=per, events=events, stale=stale)
    return out


# The milestones as the meetings put them: "We went live this morning", "signed the acceptance certificate today".
EVENT_RX = {"contract": r"contract.*signed|signed.*contract|签署.*合同|合同.*签署", "golive": r"went live|go-live|live on|上线",
            "defect": r"defect.*report|report.*defect|报告.*缺陷|缺陷.*报告", "acceptance": r"acceptance certificate|验收证书"}
# What was true on the day the contract was signed, and is still remembered as if it were true today.
STALE = re.compile(r"(?:none|no) (?:of (?:the|those) )?(?:contract\s+)?(?:schedule |timeline )?clocks? (?:have|has) started"
                   r"|clocks? (?:have|has) (?:not|n't) (?:yet )?started|时钟尚未启动", re.I)


def counts(items: dict) -> dict:
    k = {v: 0 for v in MARK}
    for r in items.values():
        k[r["verdict"]] += 1
    return k


def show_census(cen: dict) -> None:
    d = load_data()
    for k in d["clocks"]:
        start, what = anchor_of(d, k)
        emit("text", f"\n    {k['what']} — “{k['said']}” ({what} {start.isoformat()}) → due {show_day(due(d, k))}")
        for c in COPIES:
            r = cen[c]["items"][k["id"]]
            if r["verdict"] == "none":
                emit("text", f"      {COPY_SHORT[c]}  · not remembered")
                continue
            head = (f"~ {r['anchor']} + ?" if r["verdict"] == "calc" else
                    f"{MARK[r['verdict']]} {r['got'] or 'no date'}{(' (' + r['off'] + ')') if r['off'] else ''}")
            emit("text", wrap(f"{head:<22} {r['facts'][0]}", f"      {COPY_SHORT[c]}  ", 116))
    emit("text", "\n    the milestones that start the clocks, as copy A remembers them (separate facts):")
    for m in d["milestones"]:
        got = cen["talk"]["events"].get(m["key"]) or []
        emit("text", wrap(f"{m['short']:<18} " + (got[0] if got else "· not remembered"), "      ", 116))
    if cen["talk"]["stale"]:
        emit("text", "\n    and from the day the contract was signed, still in memory:")
        for f in cen["talk"]["stale"][:2]:
            emit("text", wrap(f, "      “", 110) + "”")
    emit("text", "")
    for c in COPIES:
        k = counts(cen[c]["items"])
        emit("text", f"    {COPY_LABEL[c]:<28} clocks with their due date {k['ok']}/{len(d['clocks'])} · clock with no date "
                     f"{k['nodate']} · start date only {k['calc']} · wrong date {k['off']} · not remembered {k['none']}")
    emit("census", "", copies=cen, truth={k["id"]: due(d, k).isoformat() for k in d["clocks"]})


# --------------------------------------------------------------------------- step 5: the contract calendar as of today

def board(cli: CLI, world: dict, cen: dict, echo: bool = True) -> dict:
    d = load_data()
    now = today(d)

    def span(x: date | None) -> str:
        if not x:
            return ""
        n = (x - now).days
        return f"in {n} d" if n > 0 else f"{-n} d ago" if n < 0 else "today"
    rows = []
    tally = {c: {"on": 0, "wrong": 0, "missing": 0} for c in COPIES}
    emit("text", f"    today is {show_day(now)}; a clock is on the calendar only when memory gives its due date\n")
    emit("text", f"    {'':<32} {'due':<22} " + "  ".join(f"{COPY_SHORT[c] + ' calendar':<24}" for c in COPIES))
    for k in sorted(d["clocks"], key=lambda k: due(d, k)):
        want = due(d, k)
        row = dict(item=k["id"], what=k["what"], want=want.isoformat(), passed=want < now, copies={})
        cells = []
        for c in COPIES:
            r = cen[c]["items"][k["id"]]
            got = date.fromisoformat(r["got"]) if r["got"] else None
            tally[c]["on" if r["verdict"] == "ok" else "wrong" if got else "missing"] += 1
            shown = r["got"] or (f"{r['anchor']} + ?" if r["verdict"] == "calc" else "no date")
            cells.append(f"{MARK[r['verdict']]} {shown:<11} {span(got):<9}")
            row["copies"][c] = dict(got=r["got"], verdict=r["verdict"], anchor=r["anchor"], span=span(got))
        rows.append(row)
        emit("text", f"    {k['what'][:32]:<32} {want.isoformat()} {span(want):<11} " + "  ".join(f"{x:<24}" for x in cells))
    passed = sum(1 for r in rows if r["passed"])
    emit("text", "")
    for c in COPIES:
        t = tally[c]
        emit("text", f"    {COPY_LABEL[c]:<28} on the calendar {t['on']}/{len(rows)}"
                     + (f" · wrong date {t['wrong']}" if t["wrong"] else "") + f" · no due date {t['missing']}")
    # The same question as a date search: which facts mention this month? `--query` is an exact substring match.
    month = now.strftime("%Y-%m")
    emit("text", f"\n    what falls due this month? — every fact whose text contains “{month}”:")
    hits: dict = {}
    for n, c in enumerate(COPIES):
        got = paged(cli, ["fact", "list", "--projects", world["projects"][c], "--query", month, "--page-size", "200"],
                    echo=echo and n == 0)
        hits[c] = [f.get("fact") or "" for f in got]
        emit("text", f"      {COPY_LABEL[c]:<28} {len(hits[c])} fact(s)")
        for f in hits[c][:4]:
            emit("text", wrap(f, "          ", 112))
    emit("board", "", today=now.isoformat(), rows=rows, passed=passed, tally=tally, month=month, month_hits=hits)
    return dict(today=now.isoformat(), rows=rows, passed=passed, tally=tally, month=month, month_hits=hits)


# --------------------------------------------------------------------------- step 6: ask the programme assistant

def system_prompt(d: dict) -> str:
    return d["system"].format(today=today(d).strftime("%A %Y-%m-%d"))


def ask_rows(cli: CLI, world: dict, c: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    jobs = [(k, i) for k in d["clocks"] for i in range(RUNS)]
    for n, k in enumerate(d["clocks"]):
        baseline(cli, k["ask"], system, echo=echo_first and n == 0)

    def one(job):
        k, i = job
        r = router(cli, k["ask"], system, world["boundaries"][c], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=k["id"], copy=c, run=i + 1, verdict=None, empty=False, reply="")
        base = BASE[(k["ask"], system)]
        empty = r["prompt_tokens"] <= base
        g = verdict(due(d, k), answer_date(r["reply"]), True)
        return dict(item=k["id"], copy=c, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty, **g)
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    emit("text", f"    {'':<32} {'due':<12} answers")
    for k in d["clocks"]:
        mine = [r for r in rows if r["item"] == k["id"]]
        got = "  ".join("∅" if r.get("empty") else f"{MARK.get(r['verdict'], '?')} {r['got'] or 'no date'}"
                        + (f" ({r['off']})" if r.get("off") else "") for r in mine)
        emit("text", f"    {k['what'][:32]:<32} {due(d, k).isoformat():<12} {got}")
        emit("ask_row", "", copy=c, item=k["id"], rows=mine, want=due(d, k).isoformat())
    return rows


def show_examples(rows: list[dict], c: str) -> None:
    d = load_data()
    picked = [r for r in rows if r.get("verdict") in ("off", "nodate") and not r.get("empty")][:1] or \
             [r for r in rows if r.get("verdict") == "ok" and not r.get("empty")][:1]
    for r in picked:
        k = clock_of(d, r["item"])
        emit("text", f"\n    “{k['ask']}”  (due: {show_day(due(d, k))})")
        emit("text", wrap(r["reply"], "      | ", 100))
    emit("examples", "", copy=c, rows=picked)


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("verdict")]
    n = lambda v: sum(1 for r in live if not r.get("empty") and r["verdict"] == v)  # noqa: E731
    return dict(calls=len(live), empty=sum(1 for r in live if r.get("empty")), ok=n("ok"), off=n("off"),
                nodate=n("nodate") + n("calc"))


def arm_line(t: dict, lab: str) -> str:
    return (f"    {lab:<28} right {t['ok']:>2} · wrong date {t['off']:>2} · no date {t['nodate']:>2} · ∅ {t['empty']:>2}"
            f"   of {t['calls']}")


# --------------------------------------------------------------------------- step 7: score

def score(cen: dict, brd: dict, rows: list[dict]) -> dict:
    d = load_data()
    n = len(d["clocks"])
    out: dict = {"memory": {}, "answers": {}, "calendar": {}, "by_clock": {}}
    emit("text", f"    due dates in memory ({n} clocks), clocks on the calendar, and the assistant's answers "
                 f"({n} questions × {RUNS}):")
    for c in COPIES:
        k = counts(cen[c]["items"])
        t = tally([r for r in rows if r["copy"] == c])
        out["memory"][c] = dict(k, total=n)
        out["answers"][c] = t
        out["calendar"][c] = brd["tally"][c]
        emit("text", f"    {COPY_LABEL[c]:<28} memory {k['ok']}/{n} · calendar {brd['tally'][c]['on']}/{n} · "
                     f"answers right {t['ok']}/{t['calls'] - t['empty']}" + (f" (∅ {t['empty']})" if t["empty"] else ""))
    emit("text", "\n    answers right, per clock:")
    emit("text", f"      {'':<32} " + "  ".join(f"{COPY_SHORT[c]:<7}" for c in COPIES))
    for k in d["clocks"]:
        cells = {}
        for c in COPIES:
            mine = [r for r in rows if r["copy"] == c and r["item"] == k["id"] and r.get("verdict")]
            live = [r for r in mine if not r.get("empty")]
            cells[c] = dict(ok=sum(1 for r in live if r["verdict"] == "ok"), n=len(live), empty=len(mine) - len(live))
        out["by_clock"][k["id"]] = cells
        cell = lambda x: f"{x['ok']}/{x['n']}" + (f" ∅{x['empty']}" if x["empty"] else "")  # noqa: E731
        emit("text", f"      {k['what'][:32]:<32} " + "  ".join(f"{cell(cells[c]):<7}" for c in COPIES))
    emit("text", "\n    ✓ the right due date · ✗ another date (how far off) · ~ only “N days from <date>” · ? the clock with no "
                 "date · · not remembered · ∅ MemoryRouter attached nothing (prompt tokens equal the no-memory call)")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['org']}'s {d['project']}, three copies")
    world = setup(cli)

    banner(3, TOTAL, "Four meetings, four months — and what the app writes itself (B, C)")
    ingest(cli, world)
    write_structured(cli, world)

    banner(4, TOTAL, "What memory holds for each contract clock")
    cen = census(cli, world)
    show_census(cen)

    banner(5, TOTAL, "The contract calendar, as of today")
    brd = board(cli, world, cen)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the programme assistant — through each copy's boundary")
    for c in COPIES:
        emit("text", f"\n  {COPY_LABEL[c]}")
        mine = ask_rows(cli, world, c, echo_first=c == COPIES[0])
        if any(r.get("verdict") for r in mine):
            emit("text", "\n" + arm_line(tally(mine), COPY_LABEL[c]))
            show_examples(mine, c)
        emit("arm_done", "", copy=c, tally=tally(mine))
        rows += mine

    banner(7, TOTAL, "The score")
    sc = score(cen, brd, rows)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cen, board=brd, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
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
    """Each contract clock as each copy remembers it, and the calendar as of today."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("text", "")
    board(cli, world, cen)
    emit("memory_view", "", copies=cen)
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through each copy; graded when it is about one of the six clocks."""
    d = load_data()
    world = load_world(cli)
    k = next((k for k in d["clocks"] if about(question, k)), None)
    system = system_prompt(d)
    rows = []
    base = baseline(cli, question, system, echo=True)
    for n, c in enumerate(COPIES):
        r = router(cli, question, system, world["boundaries"].get(c), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = verdict(due(d, k), answer_date(r["reply"]), True) if k else dict(verdict=None, got=str(answer_date(r["reply"]) or ""), off="", anchor="")
        head = "∅ nothing attached" if empty else (f"{MARK[g['verdict']]} {g['got'] or 'no date'} {g['off']}".strip() if g["verdict"] else g["got"])
        emit("text", f"\n  {COPY_LABEL[c]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(copy=c, reply=r["reply"], empty=empty, want=k and due(d, k).isoformat(), **g))
    if k:
        start, what = anchor_of(d, k)
        emit("text", f"\n  due: {show_day(due(d, k))} (“{k['said']}”; {what} {start.isoformat()})")
    emit("asked", "", question=question, item=k and k["id"], rows=rows, want=k and due(d, k).isoformat())
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



EXAMPLE = "When is the final payment to Tamsett due?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = each clock as each copy remembers it, and the calendar; "
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
        banner(2, 2, "Each contract clock, as each copy remembers it")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "{EXAMPLE}")')
        banner(2, 2, "One question to the programme assistant, through A, B and C")
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


#!/usr/bin/env python3
"""time-zone-memory — "4pm", "6pm my time": the team channel is remembered, the time zone is not.

Tessaline (a fictional software company) lets MemoryLake remember its #release-sync channel: one GROUP
conversation, MemoryLake extracts facts into the channel's project. Lena works from Berlin, Marcus from Austin,
Priya from Singapore; each says when something happens the way people do — "4pm on Wednesday", "6pm my time".
Sam, in London, asks his assistant (MemoryRouter: an ordinary model call with `--boundary`) what time those are for
him. Extraction keeps "4pm" and drops whose 4pm it was, so the answers come back in London time, hours off.

Three copies of the same channel, written three ways by the app: A as said · B with each sender's zone stamped
next to every clock time (the chat tool knows it) · C with the zone and the UTC instant; C's questions ask the model
for the UTC instant and the app converts it to London in code. A′ is C's read path on A's memory — the reverse
check. A no-memory control closes the run.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 120 calls on gpt-4o-mini per run).
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

PREFIX = "mlu-tzm"  # memorylake-usecases / time-zone-memory


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
RUNS = 3              # answers per question and arm
MAX_TOKENS = 100
ARMS = ("none", "raw", "zoned", "utc", "rawutc")
ARM_LABEL = {"none": "0  no memory (control)", "raw": "A  as remembered", "zoned": "B  zone stamped on write",
             "utc": "C  UTC stamped, app converts", "rawutc": "A′ as remembered, app converts"}
# Which copy of the channel each arm reads, and how it asks.
COPY = {"none": None, "raw": "raw", "zoned": "zoned", "utc": "utc", "rawutc": "raw"}
READ = {"none": "local", "raw": "local", "zoned": "local", "utc": "utc", "rawutc": "utc"}
COPIES = ("raw", "zoned", "utc")
COPY_LABEL = {"raw": "as said", "zoned": "sender's zone stamped", "utc": "zone + UTC instant stamped"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "channel.json").read_text(encoding="utf-8"))


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(str(text).split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent))


def person(d: dict, key: str) -> dict:
    return next(p for p in d["people"] if p["key"] == key)


# --------------------------------------------------------------------------- time: what the app knows and the model has to work out

MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                      "October", "November", "December"], 1)}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
YEAR = 2026
# A clock time as people type it: "4pm", "10am", "9:30am", "15:00"; an explicit zone right after it is kept as said.
CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s?(am|pm)\b(\s+my time)?|\b(\d{2}):(\d{2})\b", re.I)
EXPLICIT = re.compile(r"^\s+(UTC|SGT)\b")
ZONE_ABBR = {"UTC": "UTC", "SGT": "Asia/Singapore"}
DATE = re.compile(r"\b(?:(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?(\d{1,2})\s+(January|February|March|April|May|"
                  r"June|July|August|September|October|November|December)\b")


def offset(dt: datetime) -> str:
    """UTC+2, UTC-5, UTC+5:30 — the offset in force at that moment."""
    m = int(dt.utcoffset().total_seconds() // 60)
    sign, m = ("+" if m >= 0 else "-"), abs(m)
    return f"UTC{sign}{m // 60}" + (f":{m % 60:02d}" if m % 60 else "")


def utc_words(dt: datetime) -> str:
    u = dt.astimezone(timezone.utc)
    return f"{u:%H:%M} UTC {WEEKDAYS[u.weekday()]} {u.day} {u:%B}"


def stamp(text: str, sender: dict, with_utc: bool) -> str:
    """The app's write-side step: it knows each sender's time zone (the chat tool's profile), so it writes the zone —
    and, for copy C, the UTC instant — next to every clock time before the message goes to MemoryLake."""
    dm = DATE.search(text)
    if not dm:
        return text
    day = date(YEAR, MONTHS[dm.group(3)], int(dm.group(2)))

    def one(m: re.Match) -> str:
        if m.group(5):  # 24-hour "15:00"
            h, mi, ampm, mine = int(m.group(5)), int(m.group(6)), "", ""
        else:
            h, mi, ampm, mine = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower(), m.group(4) or ""
            h = h % 12 + (12 if ampm == "pm" else 0)
        said = m.group(0)[: len(m.group(0)) - len(mine)] if mine else m.group(0)
        ex = EXPLICIT.match(text[m.end():])
        if ex:  # the speaker already gave a zone: B keeps it as said; C adds the instant
            return m.group(0)  # C's instant for these is added after the abbreviation, below
        dt = datetime(day.year, day.month, day.day, h, mi, tzinfo=ZoneInfo(sender["tz"]))
        zone = f"{sender['city']} time, {offset(dt)}"
        return f"{said} ({zone}; = {utc_words(dt)})" if with_utc else f"{said} ({zone})"
    out = CLOCK.sub(one, text)
    if with_utc:  # explicit zones the speaker typed ("10am SGT"): add the instant after the abbreviation
        for abbr, tzname in ZONE_ABBR.items():
            if abbr == "UTC":
                continue
            for m in list(re.finditer(rf"\b(\d{{1,2}})(?::(\d{{2}}))?\s?(am|pm)\s+{abbr}\b", out, re.I))[::-1]:
                h = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
                dt = datetime(day.year, day.month, day.day, h, int(m.group(2) or 0), tzinfo=ZoneInfo(tzname))
                out = out[:m.end()] + f" (= {utc_words(dt)})" + out[m.end():]
    return out


def truth(ev: dict, tz: str) -> datetime:
    """The real moment, in the asker's zone."""
    local = datetime.strptime(ev["local"], "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(ev["tz"]))
    return local.astimezone(ZoneInfo(tz))


def show_time(dt: datetime) -> str:
    return f"{WEEKDAYS[dt.weekday()][:3]} {dt.day} {dt:%b} {dt:%H:%M}"


ANSWER = re.compile(r"Answer:\s*(.*)", re.I)
HM = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?=\W|$)", re.I)


def answer_line(reply: str) -> str:
    m = ANSWER.search(reply or "")
    return (m.group(1) if m else (reply or "").strip().split("\n")[0]).strip()


# Extraction does not control the language of facts (a copy sometimes comes back in German), and the model answers
# in the facts' language: "Montag 26. Oktober, 15:00". Read the common European names as their English ones.
FOREIGN = {"montag": "Monday", "dienstag": "Tuesday", "mittwoch": "Wednesday", "donnerstag": "Thursday", "freitag": "Friday",
           "samstag": "Saturday", "sonntag": "Sunday", "oktober": "October", "okt": "October", "lundi": "Monday",
           "mardi": "Tuesday", "mercredi": "Wednesday", "jeudi": "Thursday", "vendredi": "Friday", "samedi": "Saturday",
           "octobre": "October", "lunes": "Monday", "martes": "Tuesday", "miércoles": "Wednesday", "jueves": "Thursday",
           "viernes": "Friday", "sábado": "Saturday", "octubre": "October", "uhr": ""}


def english(line: str) -> str:
    line = re.sub(r"\b(\d{1,2})\.(?=\s)", r"\1", line)          # "26. Oktober" → "26 Oktober"
    line = re.sub(r"\b(\d{1,2})\s+de\s+(?=[a-z])", r"\1 ", line, flags=re.I)  # "26 de octubre"
    return re.sub(r"[^\W\d_]+", lambda m: FOREIGN.get(m.group(0).lower(), m.group(0)), line)


def parse_local(line: str, tz: str):
    """'<weekday> <date>, <HH:MM> London time' (in whatever shape the model wrote it) → an aware datetime, or None."""
    line = english(line)
    dm = DATE.search(line) or re.search(r"\b(?:(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
                                        r"(?:October|November)\s+(\d{1,2})\b", line)
    if dm and dm.re is DATE:
        day = date(YEAR, MONTHS[dm.group(3)], int(dm.group(2)))
        rest = line[dm.end():]
    elif dm:
        day = date(YEAR, MONTHS["November" if "November" in dm.group(0) else "October"], int(dm.group(2)))
        rest = line[dm.end():]
    else:
        wd = re.search(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", line)
        if not wd:
            return None
        start = date(YEAR, 10, 19)  # "today" in the story: the week the questions are about
        day = start + timedelta(days=(WEEKDAYS.index(wd.group(1)) - start.weekday()) % 7)
        rest = line[wd.end():]
    if re.search(r"\bmidnight\b", rest, re.I):
        h, mi = 0, 0
    elif re.search(r"\bnoon\b", rest, re.I):
        h, mi = 12, 0
    else:
        m = next((m for m in HM.finditer(rest) if m.group(2) or m.group(3)), None)
        if not m:
            return None
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
        if ap:
            h = h % 12 + (12 if ap == "pm" else 0)
        if h > 23 or mi > 59:
            return None
    return datetime(day.year, day.month, day.day, h, mi, tzinfo=ZoneInfo(tz))


def parse_utc(line: str):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})", line)
    if not m:
        return None
    y, mo, dd, h, mi = map(int, m.groups())
    if h > 23 or mi > 59:
        return None
    return datetime(y, mo, dd, h, mi, tzinfo=timezone.utc)


def grade(ev: dict, reply: str, read: str, tz: str) -> dict:
    """✓ right day and time · ✗ wrong (by how much) · ? no time given. For the UTC read path the app converts."""
    line = answer_line(reply)
    want = truth(ev, tz)
    if read == "utc":
        u = parse_utc(line)
        got = u.astimezone(ZoneInfo(tz)) if u else None
    else:
        got = parse_local(line, tz)
    if got is None:
        return dict(verdict="none", line=line, got="", off="")
    if got == want:
        return dict(verdict="ok", line=line, got=show_time(got), off="")
    hours = (got - want).total_seconds() / 3600
    if hours % 24 == 0:
        off = f"{'+' if hours > 0 else '−'}{abs(int(hours // 24))} day"
    else:
        off = f"{'+' if hours > 0 else '−'}{abs(hours):g} h"
    return dict(verdict="off", line=line, got=show_time(got), off=off)


MARK = {"ok": "✓", "off": "✗", "none": "?"}


def mark(r: dict) -> str:
    if not r.get("verdict"):
        return "·"
    return "∅" if r.get("empty") else MARK[r["verdict"]]


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("verdict")]
    n = lambda v: sum(1 for r in live if not r.get("empty") and r["verdict"] == v)  # noqa: E731
    return dict(calls=len(live), empty=sum(1 for r in live if r.get("empty")), ok=n("ok"), off=n("off"), none=n("none"),
                day=sum(1 for r in live if not r.get("empty") and "day" in (r.get("off") or "")))


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


def actor_facts(cli: CLI, actor: str, query: str | None = None, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--actors", actor, "--page-size", "200"] + (["--query", query] if query else []), echo)


def project_facts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--projects", project, "--page-size", "200"], echo)


def leftovers(cli: CLI) -> bool:
    d = load_data()
    return bool(demo_boundaries(cli) or any(find_project(cli, c) for c in COPIES)
                or any(find_actor(cli, p["key"]) for p in d["people"]))


def make_actor(cli: CLI, key: str, display: str, description: str, echo: bool) -> str:
    a = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", display, "--description", description,
                "--tags", "demo", echo=echo)
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
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['team']} {d['channel']} ({COPY_LABEL[c]})",
                                       "--custom-id", cid(c), "--description",
                                       f"The team channel, written to memory {COPY_LABEL[c]}. Demo data.", scoped=True, echo=n == 0)["id"]
    note(f"three copies of the same channel, one per way of writing it: {', '.join(COPY_LABEL[c] for c in COPIES)} "
         "(each arm reads its own copy, so no answer comes from another arm's cached retrieval)")
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}, {p['city']}. Demo data.", echo=n == 0)
    note(f"{len(d['people'])} teammates as actors: " + " · ".join(f"{p['display']} ({p['city']}, {p['tz']})" for p in d["people"]))
    for n, c in enumerate(COPIES):
        boundary(cli, world, c, f"{d['asker']['display']}'s assistant: {d['channel']} ({COPY_LABEL[c]})",
                 "--projects", world["projects"][c], echo=n == 0)
    note(f"one boundary per copy — what {d['asker']['display']}'s assistant reads through MemoryRouter")
    emit("world", "", **world)
    return world


def boundary(cli: CLI, world: dict, key: str, name: str, *args: str, echo: bool = False) -> str:
    if key not in world["boundaries"]:
        b = cli.run("boundary", "create", "--name", name, "--custom-id", cid(key), *args, scoped=True, echo=echo)
        world["boundaries"][key] = b["id"]
    return world["boundaries"][key]


def load_world(cli: CLI) -> dict:
    d = load_data()
    world: dict = {"projects": {}, "actors": {}, "boundaries": {}}
    for c in COPIES:
        p = find_project(cli, c)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world["projects"][c] = p["id"]
    for p in d["people"]:
        a = find_actor(cli, p["key"])
        if a:
            world["actors"][p["key"]] = a["id"]
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



# --------------------------------------------------------------------------- step 3: the channel

def written(d: dict) -> list[dict]:
    """Every message in the three forms the copies store."""
    rows = []
    for m in d["messages"]:
        p = person(d, m["who"])
        label = f"{p['display']} ({p['role']})"
        rows.append(dict(who=m["who"], event=m.get("event"), display=p["display"], city=p["city"],
                         raw=m["text"], zoned=stamp(m["text"], p, False), utc=stamp(m["text"], p, True), label=label))
    return rows


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


def channel(cli: CLI, world: dict) -> dict:
    d = load_data()
    rows = written(d)
    emit("text", f"\n    {d['channel']} · Monday {d['date']}")
    for r in rows:
        emit("text", wrap(f"{r['label']}: {r['raw']}", "      "))
    emit("text", "\n    what the app writes into each copy (it knows every sender's time zone from the chat profile):")
    for r in rows:
        if r["zoned"] == r["raw"] and r["utc"] == r["raw"]:
            continue
        emit("text", f"      {r['display']}")
        for c in COPIES:
            emit("text", wrap(r[c], f"        {c:<6} | ", 108))
    emit("channel", "", messages=rows, channel=d["channel"], date=d["date"])
    convs: dict = {}

    def one(n_c):
        n, c = n_c
        conv = cli.run("conv", "create", "--project", world["projects"][c], "--actors", ",".join(world["actors"].values()),
                       "--kind", "GROUP", "--name", f"{d['channel']} ({COPY_LABEL[c]})", "--custom-id", cid("conv", c),
                       scoped=True, echo=n == 0)
        for i, r in enumerate(rows):
            append(cli, conv["id"], world["actors"][r["who"]], f"m{i:02d}", f"{d['date']}T09:{i:02d}:00Z",
                   f"{r['label']}: {r[c]}", echo=n == 0 and i == 1)
        return c, conv["id"]
    with ThreadPoolExecutor(3) as pool:
        convs.update(dict(pool.map(one, enumerate(COPIES))))
    note(f"{len(rows)} messages appended to one GROUP conversation per copy, each as its sender, labelled `Name (role): …`; "
         "MemoryLake now extracts facts in the background")
    world["convs"] = convs
    wait_cooked(cli, world)
    return convs


def wait_cooked(cli: CLI, world: dict, limit: int = 600) -> None:
    t0, pending, first = time.time(), dict(world["convs"]), True
    while pending and time.time() - t0 < limit:
        for c, conv in list(pending.items()):
            try:
                st = cli.run("conv", "cook-status", conv, scoped=True, echo=first) or {}
                first = False
                if st.get("cook_finished") and project_facts(cli, world["projects"][c]):
                    pending.pop(c)
            except CLIError as e:
                if not e.has(*CLI.TRANSIENT):
                    raise
        done = len(world["convs"]) - len(pending)
        emit("progress", f"    extracted {done}/{len(world['convs'])} copies · {int(time.time() - t0)} s", done=done,
             total=len(world["convs"]))
        if pending:
            time.sleep(8)
    if pending:
        note(f"still extracting after {limit} s: {', '.join(sorted(pending))} — deferred; those questions are graded as they come")
    time.sleep(10)  # new facts reach retrieval a few seconds after `fact list`


# --------------------------------------------------------------------------- step 4: what got remembered

# Which event a fact is about. Order matters: the rollback fact mentions the migration.
EVENT_KEYS = [("rollback", r"rollback"), ("freeze", r"freeze"), ("demo", r"Barrowgate"), ("standup", r"stand-?up"),
              ("gonogo", r"no-?go"), ("office", r"office hours"), ("migration", r"migration")]
HAS_CLOCK = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b", re.I)
HAS_UTC = re.compile(r"\b\d{1,2}:\d{2}\s?UTC\b|\bUTC\s?\d{1,2}:\d{2}\b", re.I)
HAS_ZONE = re.compile(r"\b(?:UTC|GMT|SGT|CEST|CET|CDT|CST|BST)\b|Berlin|Austin|Singapore|London", re.I)
# "in the release manager's local time": a zone is implied, and the fact does not say which one.
VAGUE = re.compile(r"local time|my time|their time|time zone", re.I)


def event_of(text: str) -> str | None:
    if not HAS_CLOCK.search(text):
        return None
    return next((k for k, rx in EVENT_KEYS if re.search(rx, text, re.I)), None)


def zone_of(text: str) -> str:
    return ("utc" if HAS_UTC.search(text) else "zone" if HAS_ZONE.search(text) else "vague" if VAGUE.search(text)
            else "none")


def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    d = load_data()
    out: dict = {}
    for n, c in enumerate(COPIES):
        facts = project_facts(cli, world["projects"][c], echo=echo and n == 0)
        per: dict = {}
        for f in facts:
            ev = event_of(f.get("fact") or "")
            if ev:
                per.setdefault(ev, []).append(dict(fact=f["fact"], zone=zone_of(f["fact"])))
        out[c] = dict(facts=len(facts), events=per)
    return out


ZONE_WORD = {"none": "⚠ no time zone", "vague": "⚠ whose local time?", "zone": "zone kept", "utc": "UTC instant"}


def show_census(cen: dict) -> None:
    d = load_data()
    msgs = {m.get("event"): m for m in d["messages"]}
    for ev in d["events"]:
        said = msgs[ev["id"]]
        p = person(d, said["who"])
        emit("text", f"\n    {ev['what']} — {p['display']} ({p['city']}) said “{ev['said']}”")
        for c in COPIES:
            hits = cen[c]["events"].get(ev["id"]) or []
            if not hits:
                emit("text", f"      {c:<6} (not remembered with a time)")
            for h in hits[:2]:
                emit("text", wrap(f"[{ZONE_WORD[h['zone']]}] {h['fact']}", f"      {c:<6} ", 108))
    rows = {c: sum(1 for ev in d["events"] if not ev["zone_in_text"]
                   and any(h["zone"] in ("none", "vague") for h in cen[c]["events"].get(ev["id"]) or [])) for c in COPIES}
    implicit = sum(1 for ev in d["events"] if not ev["zone_in_text"])
    emit("text", "\n    of the " + str(implicit) + " times said without a zone, remembered with no zone you could convert from: "
         + " · ".join(f"{c} {rows[c]}" for c in COPIES))
    emit("census", "", copies=cen, nozone=rows, implicit=implicit)


# --------------------------------------------------------------------------- steps 5–8: ask

def ask_rows(cli: CLI, world: dict, arm: str, echo_first: bool = True) -> list[dict]:
    """Every question × RUNS through the arm's boundary (none for the control), 6 calls at a time."""
    d = load_data()
    tz = d["asker"]["tz"]
    system = d["system_utc"] if READ[arm] == "utc" else d["system_local"]
    b = world["boundaries"].get(COPY[arm]) if COPY[arm] else None
    jobs = [(ev, i) for ev in d["events"] for i in range(RUNS)]
    for n, ev in enumerate(d["events"]):
        baseline(cli, ev["ask"], system, echo=echo_first and n == 0)

    def one(job):
        ev, i = job
        r = router(cli, ev["ask"], system, b, echo=echo_first and job is jobs[0])
        if not r:
            return dict(ev=ev["id"], arm=arm, run=i + 1, verdict=None, empty=False, reply="")
        g = grade(ev, r["reply"], READ[arm], tz)
        base = BASE[(ev["ask"], system)]
        return dict(ev=ev["id"], arm=arm, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"],
                    attached=r["prompt_tokens"] - base, empty=bool(b) and r["prompt_tokens"] <= base, **g)
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers with it; "
             f"{len(jobs)} answers, 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    emit("text", f"    {'':<28} {'right answer':<18} answers")
    for ev in d["events"]:
        mine = [r for r in rows if r["ev"] == ev["id"]]
        got = "  ".join(f"{mark(r)} {r['got'] or '–'}{(' ' + r['off']) if r.get('off') else ''}" for r in mine)
        emit("text", f"    {ev['what'][:28]:<28} {show_time(truth(ev, tz)):<18} {got}")
        emit("ask_row", "", arm=arm, ev=ev["id"], rows=mine, want=show_time(truth(ev, tz)))
    return rows


def arm_line(rows: list[dict], label: str) -> str:
    t = tally(rows)
    return (f"    {label:<32} right {t['ok']:>2} · wrong {t['off']:>2} (a whole day off: {t['day']}) · no time {t['none']:>2}"
            f" · ∅ {t['empty']:>2}   of {t['calls']}")


def show_examples(rows: list[dict], arm: str) -> None:
    d = load_data()
    picked = []
    for v in ("off", "none", "ok"):
        for r in rows:
            if r.get("verdict") == v and not r.get("empty") and r["ev"] not in {p["ev"] for p in picked} and len(picked) < 2:
                picked.append(r)
    if not picked:
        return
    emit("text", "")
    for r in picked:
        ev = next(e for e in d["events"] if e["id"] == r["ev"])
        emit("text", f"    “{ev['ask']}”  (right: {show_time(truth(ev, d['asker']['tz']))} London)")
        emit("text", wrap(r["reply"], "      | ", 100))
        if READ[arm] == "utc" and r.get("got"):
            emit("text", f"      ← the app converts the UTC instant to London: {r['got']}")
    emit("examples", "", arm=arm, rows=picked)


def run_arm(cli: CLI, world: dict, arm: str) -> list[dict]:
    rows = ask_rows(cli, world, arm)
    if any(r.get("verdict") for r in rows):
        emit("text", "\n" + arm_line(rows, ARM_LABEL[arm]))
        show_examples(rows, arm)
    emit("arm_done", "", arm=arm, tally=tally(rows))
    return rows


def score(rows: list[dict]) -> dict:
    d = load_data()
    out: dict = {"arms": {}, "split": {}}
    lines = []
    implicit = {ev["id"] for ev in d["events"] if not ev["zone_in_text"]}
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        out["arms"][arm] = tally(mine)
        out["split"][arm] = dict(implicit=tally([r for r in mine if r["ev"] in implicit]),
                                 explicit=tally([r for r in mine if r["ev"] not in implicit]))
        lines.append(arm_line(mine, ARM_LABEL[arm]))
    emit("text", f"\n    {len(d['events'])} questions × {RUNS} answers, per arm:\n" + "\n".join(lines))
    emit("text", "\n    split by how the time was said:")
    for arm in ARMS:
        s = out["split"][arm]
        cell = lambda t: f"{t['ok']:>2}/{t['calls'] - t['empty']:<3}" + (f"(∅ {t['empty']})" if t["empty"] else "     ")  # noqa: E731
        emit("text", f"    {ARM_LABEL[arm]:<32} said without a zone {cell(s['implicit'])} · said with a zone {cell(s['explicit'])}")
    emit("text", "\n    ✓ right day and time in London · ✗ wrong (by how much) · ? no time given · ∅ MemoryRouter attached nothing "
         "(prompt tokens equal the no-memory call), graded apart")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 9


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['team']} {d['channel']}: three copies, three teammates, one boundary each")
    world = setup(cli)

    banner(3, TOTAL, "The channel — Berlin, Austin and Singapore say when things happen")
    channel(cli, world)

    banner(4, TOTAL, "What got remembered — “4pm”, “6pm my time”, with no time zone")
    cen = census(cli, world)
    show_census(cen)

    rows: list[dict] = []
    banner(5, TOTAL, f"A · Ask from London, as remembered")
    rows += run_arm(cli, world, "raw")

    banner(6, TOTAL, "B · The app stamped each sender's zone on write")
    rows += run_arm(cli, world, "zoned")

    banner(7, TOTAL, "C · The app stamped the UTC instant; the model copies it, the app converts")
    rows += run_arm(cli, world, "utc")

    banner(8, TOTAL, "A′ · The same read path on the memory as remembered")
    emit("text", "    the reverse check: the app-side conversion alone, on the copy that lost the zones")
    rows += run_arm(cli, world, "rawutc")

    banner(9, TOTAL, "The score — and a control with no memory at all")
    emit("text", "    the same 7 questions with no boundary")
    rows = ask_rows(cli, world, "none", echo_first=False) + rows
    sc = score(rows) if any(r.get("verdict") for r in rows) else {}

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(census=cen, answers=rows, score=sc, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']:,} prompt + {tok['completion']:,} reply "
                 f"tokens, billed to your MemoryLake balance.\n"
                 f"  Every answer and its grade: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok, seconds=summary["seconds"])  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def memory(cli: CLI) -> dict:
    """Each scheduled item, as each copy remembers it."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("memory_view", "", copies=cen)
    return cen


def ask(cli: CLI, text: str) -> list[dict]:
    """Any question from Sam, once through A, B and C; graded when it is about one of the seven items."""
    d = load_data()
    world = load_world(cli)
    ev = next((e for e in d["events"] if re.search(dict(EVENT_KEYS)[e["id"]], text, re.I)), None)
    rows = []
    for n, arm in enumerate(("raw", "zoned", "utc")):
        system = d["system_utc"] if READ[arm] == "utc" else d["system_local"]
        base = baseline(cli, text, system, echo=n == 0)
        r = router(cli, text, system, world["boundaries"].get(COPY[arm]), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        g = grade(ev, r["reply"], READ[arm], d["asker"]["tz"]) if ev else dict(verdict=None, line=answer_line(r["reply"]), got="", off="")
        if READ[arm] == "utc" and not ev:
            u = parse_utc(g["line"])
            g["got"] = show_time(u.astimezone(ZoneInfo(d["asker"]["tz"]))) if u else ""
        empty = r["prompt_tokens"] <= base
        head = "∅ nothing attached" if empty else (f"{MARK[g['verdict']]} {g['got']} {g['off']}".strip() if g["verdict"] else g["got"] or "")
        emit("text", f"\n  {ARM_LABEL[arm]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(arm=arm, reply=r["reply"], empty=empty, want=ev and show_time(truth(ev, d["asker"]["tz"])), **g))
    if ev:
        emit("text", f"\n  right answer: {show_time(truth(ev, d['asker']['tz']))} London")
    emit("asked", "", question=text, ev=ev and ev["id"], rows=rows, want=ev and show_time(truth(ev, d["asker"]["tz"])))
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
    found = [(p["key"], find_actor(cli, p["key"], echo=not quiet and p is d["people"][0])) for p in d["people"]]
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


EXAMPLE = "When does the code freeze for 3.8 begin, in my time?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = each scheduled item as each copy remembers it; "
                         "ask \"<question>\" = one question from Sam through A, B and C; cleanup = delete everything")
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
        banner(2, 2, "Each scheduled item, as each copy remembers it")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "{EXAMPLE}")')
        banner(2, 2, "One question from London, through A, B and C")
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

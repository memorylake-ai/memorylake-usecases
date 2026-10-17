#!/usr/bin/env python3
"""forwarded-email-memory — the deadline inside the forward.

Quillmere & Rook (a fictional patent firm) dockets from email. Most deadlines arrive forwarded: a foreign associate
reports an Office Action, a client asks for an opinion before a board meeting, and the docketing paralegal forwards
the email into the firm's #docketing channel, which MemoryLake remembers. Stored as received — one message, the
paralegal's note on top and the original email below — MemoryLake's extraction often remembers the forward instead
of what it says ("A forwarded email titled … was received for docket calendaring"), and the deadlines inside come out
incomplete. The app can split the forward before it stores it: the original email becomes its own message, from its
author, at the time in its Date: header.

Two copies of the same three forwards: A stored as received · B split by the app. The app then reads every
remembered deadline, builds the docket as of today, and asks the docketing assistant (MemoryRouter: an ordinary model
call with `--boundary`) for each date.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key. Router
calls are billed to your MemoryLake balance (about 30 calls on gpt-4o-mini per run).
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
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-fem"  # memorylake-usecases / forwarded-email-memory


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
COPIES = ("asis", "split")
COPY_LABEL = {"asis": "A  stored as received", "split": "B  split by the app"}
COPY_SHORT = {"asis": "A", "split": "B"}


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    d = json.loads((DATA / "firm.json").read_text(encoding="utf-8"))
    d["inbox"] = json.loads((DATA / "forwards.json").read_text(encoding="utf-8"))
    return d


def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(str(text).split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent))


def person(d: dict, key: str) -> dict | None:
    return next((p for p in d["people"] if p["key"] == key), None)


def label(p: dict) -> str:
    return f"{p['display']} ({p['role']})"


FWD_MARK = re.compile(r"^-{3,}\s*Forwarded message\s*-{3,}\s*$", re.M)
HEADER = re.compile(r"^(From|Date|Subject|To|Cc):\s*(.*)$")


def split_forward(text: str) -> dict:
    """A forwarded email as a mail client writes it: the forwarder's note, the marker line, the original's headers,
    a blank line, the original body. Returns the parts; raises if the text is not a forward."""
    m = FWD_MARK.search(text)
    if not m:
        die("not a forwarded email: no '---------- Forwarded message ---------' line")
    note_text, rest = text[:m.start()].strip(), text[m.end():].lstrip("\n")
    head, _, body = rest.partition("\n\n")
    hdr = {}
    for line in head.splitlines():
        h = HEADER.match(line.strip())
        if h:
            hdr[h.group(1).lower()] = h.group(2).strip()
    if "from" not in hdr or "date" not in hdr:
        die("forwarded email without From: or Date: header")
    name, addr = parseaddr(hdr["from"])
    sent = parsedate_to_datetime(hdr["date"])
    return dict(note=note_text, name=name, addr=addr, sent=sent, subject=hdr.get("subject", ""), body=body.strip())


def sender_of(d: dict, fwd: dict) -> dict:
    addr = split_forward(fwd["text"])["addr"].lower()
    p = next((p for p in d["people"] if p["email"].lower() == addr), None)
    if not p:
        die(f"no person in data/firm.json has the address {addr}")
    return p


def forward_of(d: dict, item: dict) -> dict:
    return next(f for f in d["inbox"]["forwards"] if f["id"] == item["email"])


def said_on(d: dict, item: dict) -> date:
    """The sender's own calendar day: the Date: header in the sender's time zone."""
    fwd = forward_of(d, item)
    return split_forward(fwd["text"])["sent"].astimezone(ZoneInfo(sender_of(d, fwd)["tz"])).date()


def add_months(x: date, n: int) -> date:
    y, m = divmod(x.month - 1 + n, 12)
    return date(x.year + y, m + 1, x.day)


def truth(d: dict, item: dict) -> date:
    """The day the sender meant: their local date plus the offset they wrote."""
    base = said_on(d, item)
    return add_months(base, item["months"]) if item.get("months") else base + timedelta(days=item["days"])


def today(d: dict) -> date:
    return datetime.now(ZoneInfo(d["home_tz"])).date()


def show_day(x: date | None) -> str:
    return x.strftime("%a %Y-%m-%d") if x else "–"


def forwarded_at(fwd: dict) -> datetime:
    return datetime.fromisoformat(fwd["forwarded_at"].replace("Z", "+00:00"))


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september",
                                      "october", "november", "december"], 1)}
_MON = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")
MDY = re.compile(_MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)
DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r",?\s+(20\d\d)\b", re.I)
# The extraction's own stamp at the end of a fact: "(as of 2026-10-14)" or "(on 2026-10-14)". It says when the fact
# was written down, not what it says.
STAMP = re.compile(r"\s*\(?\s*(?:as of|on)\s+(20\d\d-\d\d-\d\d)\s*\)?\s*\.?\s*$", re.I)
# "60 days from 2026-09-21": the anchor is right, but the deadline itself is left to whoever reads it.
SPAN = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fourteen|twenty-one|thirty|sixty)"
UNIT = r"(?:calendar\s+)?(?:days?|weeks?|months?)"
# Extraction sometimes writes the fact in another language ("trois semaines après le 2026-10-08"): the prepositions
# after which a date is only an anchor, in the languages it has drifted into so far.
ANCHOR = re.compile(r"(?:" + SPAN + r"(?:\s+|-)" + UNIT + r"(?:\s+(?:deadline|period))?\s+(?:from|after|of)|\bday\s+after"
                    r"|\bapr[eè]s\s+le|\bnach\s+dem|\bab\s+dem|\bdesde\s+el|\bdespu[eé]s\s+del)\s+(?:the\s+)?$", re.I)
# A period with no date of its own ("four months from that date", "within 14 days", "the day after …"): the deadline
# hangs on some other date in the sentence.
DURATION = re.compile(r"\b" + SPAN + r"(?:\s+|-)" + UNIT + r"\b|\bday after\b", re.I)
# A quoted email subject names the thread, not a deadline in it.
QUOTED = re.compile(r"[“\"][^”\"]{3,120}[”\"]")
# Words that only mean something next to the day they were said.
RELATIVE = re.compile(r"\b(?:tomorrow|today|yesterday|day after|from (?:today|now)|next (?:week|month)|within \d+ days|"
                      r"in (?:a|one|two|three|ten|\d+) (?:days?|weeks?)|(?:two|three|four) (?:weeks|months) from)\b", re.I)
# A fact about the forward itself rather than what the email says.
ABOUT_FORWARD = re.compile(r"\bforward|\bemail (?:report|thread)|\bemail titled|for (?:the )?docket|\bcalendar(?:ing|ed)?\b", re.I)


def dates_in(text: str) -> list[tuple[int, date, bool]]:
    """Every calendar date written in `text`: (position, date, is it only the anchor of "N days from <date>")."""
    out = []
    for rx, f in ((ISO, lambda m: (int(m[1]), int(m[2]), int(m[3]))),
                  (MDY, lambda m: (int(m[3]), MONTHS[m[1].lower()], int(m[2]))),
                  (DMY, lambda m: (int(m[3]), MONTHS[m[2].lower()], int(m[1])))):
        for m in rx.finditer(text):
            try:
                out.append((m.start(), date(*f(m)), bool(ANCHOR.search(text[max(0, m.start() - 40):m.start()]))))
            except ValueError:
                pass
    return sorted(out, key=lambda x: x[0])


def body_of(fact: str) -> str:
    return STAMP.sub("", fact).strip()


def stamp_of(fact: str) -> str:
    m = STAMP.search(fact)
    return m.group(1) if m else ""


def about(text: str, rx: str) -> bool:
    """Is this fact about the item? Keywords inside a quoted email subject do not count."""
    return bool(re.search(rx, QUOTED.sub("", body_of(text)), re.I))


def read_fact(text: str, rx: str) -> tuple[date | None, date | None]:
    """(the date this fact gives for the item, the anchor of an unresolved "N days from <date>").
    With several items in one fact, the date that follows the item's keyword is the one that belongs to it; when the
    only dates come before the keyword and a bare period follows it ("…on 2026-09-29, giving four months from that
    date to approve…"), the deadline was never resolved and that earlier date is its anchor."""
    body = body_of(text)
    k = re.search(rx, QUOTED.sub(lambda m: " " * len(m.group(0)), body), re.I)
    ds = dates_in(body)
    real = [x for x in ds if not x[2]]
    anchors = [x for x in ds if x[2]]
    after = [x for x in real if k and x[0] >= k.start()]
    if after:
        return after[0][1], None
    if anchors:
        return None, anchors[0][1]
    if real and DURATION.search(body):
        return None, real[-1][1]
    return (real[0][1], None) if real else (None, None)


DATE_LINE = re.compile(r"\bDate:\s*(.+)", re.I)


def answer_date(reply: str) -> date | None:
    """The date after "Date:" in a reply; the first date anywhere when the model left the label out."""
    m = DATE_LINE.search(reply)
    if m:
        ds = dates_in(m.group(1))
        if ds:
            return ds[0][1]
        if re.search(r"unknown|not (?:given|specified|stated)|none", m.group(1), re.I):
            return None
    ds = dates_in(reply)
    return ds[0][1] if ds else None


MARK = {"ok": "✓", "off": "✗", "calc": "~", "nodate": "?", "none": "·"}
MEANING = {"ok": "the date that was meant", "off": "another date", "calc": "only “N days from <date>”, never resolved",
           "nodate": "remembered with no date", "none": "not remembered"}


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
        world["projects"][c] = cli.run("proj", "create", "--name", f"{d['firm']} docket ({COPY_LABEL[c][3:]})",
                                       "--custom-id", cid(c), "--description",
                                       f"The {d['channel']} channel, forwards {COPY_LABEL[c][3:]}. Demo data.",
                                       scoped=True, echo=n == 0)["id"]
    note("two copies of the same docketing channel, one per way of storing a forward: "
         + " · ".join(COPY_LABEL[c] for c in COPIES))
    for n, p in enumerate(d["people"]):
        world["actors"][p["key"]] = make_actor(cli, p["key"], p["display"], f"{p['role']}, {p['city']}. Demo data.", echo=n == 0)
    note(f"{len(d['people'])} people as actors: " + " · ".join(label(p) for p in d["people"]))
    for n, c in enumerate(COPIES):
        b = cli.run("boundary", "create", "--name", f"Docketing assistant ({COPY_LABEL[c][3:]})",
                    "--custom-id", cid(c), "--projects", world["projects"][c], scoped=True, echo=n == 0)
        world["boundaries"][c] = b["id"]
    note("one boundary per copy — what the docketing assistant reads through MemoryRouter")
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


# --------------------------------------------------------------------------- step 3: the forwards

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


def messages_for(d: dict, fwd: dict, c: str) -> list[dict]:
    """What gets appended for one forward. A: the forward as received, from the forwarder, at the time it was forwarded.
    B: the original as its own message, from its author, at its Date: — then the forwarder's note."""
    by = person(d, d["inbox"]["forwarded_by"])
    if c == "asis":
        return [dict(actor=by["key"], ts=utc(forwarded_at(fwd)), text=f"{label(by)}: {fwd['text']}")]
    f = split_forward(fwd["text"])
    au = sender_of(d, fwd)
    local = f["sent"].astimezone(ZoneInfo(au["tz"]))
    return [dict(actor=au["key"], ts=utc(f["sent"]), text=f"{label(au)}: {f['body']}"),
            dict(actor=by["key"], ts=utc(forwarded_at(fwd)),
                 text=f"{label(by)}: {f['note']} (Forwarded: “{f['subject']}”, {au['display']}'s email of "
                      f"{local:%a %d %b %Y}.)")]


def show_inbox(d: dict) -> None:
    by = person(d, d["inbox"]["forwarded_by"])
    for fwd in d["inbox"]["forwards"]:
        f = split_forward(fwd["text"])
        au = sender_of(d, fwd)
        local = f["sent"].astimezone(ZoneInfo(au["tz"]))
        fl = forwarded_at(fwd).astimezone(ZoneInfo(d["home_tz"]))
        emit("text", f"\n    {fwd['id']}  “{f['subject']}”")
        emit("text", f"        sent      {local:%a %d %b %Y, %H:%M} {au['city']} — {label(au)}")
        emit("text", f"        forwarded {fl:%a %d %b %Y, %H:%M} {d['people'][0]['city']} — {by['display']}: “{f['note']}”")
        for it in [i for i in d["items"] if i["email"] == fwd["id"]]:
            emit("text", f"        · {it['what']}: “{it['said']}” → meant {show_day(truth(d, it))}")
    emit("inbox", "", forwards=[dict(id=fwd["id"], text=fwd["text"], **{k: (v.isoformat() if isinstance(v, datetime) else v)
                                                                        for k, v in split_forward(fwd["text"]).items()},
                                     forwarded_at=fwd["forwarded_at"], copies={c: messages_for(d, fwd, c) for c in COPIES})
                                for fwd in d["inbox"]["forwards"]])


def ingest(cli: CLI, world: dict) -> dict:
    d = load_data()
    show_inbox(d)
    emit("text", "\n    A appends each forward as received: one message from Ana, at the time she forwarded it.")
    emit("text", "    B splits it first (From:, Date:, body): the original from its author at its Date:, then Ana's note.")
    convs: dict = {}

    def one(job):
        n, c, fwd = job
        conv = cli.run("conv", "create", "--project", world["projects"][c], "--actors", ",".join(world["actors"].values()),
                       "--kind", "GROUP", "--name", f"{d['channel']} · {split_forward(fwd['text'])['subject']} ({COPY_LABEL[c][3:]})",
                       "--custom-id", cid("conv", c, fwd["id"]), scoped=True, echo=n in (0, 3))
        for i, m in enumerate(messages_for(d, fwd, c)):
            append(cli, conv["id"], world["actors"][m["actor"]], f"m{i:02d}", m["ts"], m["text"], echo=n in (0, 3) and i == 0)
        return (c, fwd["id"]), conv["id"]
    jobs = [(n, c, f) for n, (c, f) in enumerate((c, f) for c in COPIES for f in d["inbox"]["forwards"])]
    with ThreadPoolExecutor(6) as pool:
        convs.update(dict(pool.map(one, jobs)))
    note(f"{len(d['inbox']['forwards'])} forwards × {len(COPIES)} copies = {len(convs)} GROUP conversations, each message as "
         "its sender, labelled `Name (role): …`; MemoryLake now extracts facts in the background")
    world["convs"] = convs
    wait_cooked(cli, world)
    return convs


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


# --------------------------------------------------------------------------- step 4: what got remembered

def census(cli: CLI, world: dict, echo: bool = True) -> dict:
    """Per copy and item: the facts about it, the date each one gives, and the verdict against what was meant."""
    d = load_data()
    out: dict = {}
    for n, c in enumerate(COPIES):
        facts = [f.get("fact") or "" for f in project_facts(cli, world["projects"][c], echo=echo and n == 0)]
        per: dict = {}
        for it in d["items"]:
            hits = [f for f in facts if about(f, it["match"])]
            read = [(f, *read_fact(f, it["match"])) for f in hits]
            # the fact that is graded comes first: one with a date, else one with an unresolved anchor, else any
            read.sort(key=lambda x: (x[1] is None, x[2] is None))
            got, anchor = (read[0][1], read[0][2]) if read else (None, None)
            per[it["id"]] = dict(facts=[x[0] for x in read][:3], stamp=stamp_of(read[0][0]) if read else "",
                                 **verdict(truth(d, it), got, bool(hits), anchor))
        itemless = [f for f in facts if not any(about(f, it["match"]) for it in d["items"])]
        on_forward = [f for f in itemless if ABOUT_FORWARD.search(f)]
        out[c] = dict(facts=len(facts), items=per, about_forward=on_forward,
                      relative=[f for f in facts if RELATIVE.search(body_of(f))])
    return out


def counts(items: dict) -> dict:
    k = {v: 0 for v in MARK}
    for r in items.values():
        k[r["verdict"]] += 1
    return k


def show_census(cen: dict) -> None:
    d = load_data()
    for fwd in d["inbox"]["forwards"]:
        f = split_forward(fwd["text"])
        emit("text", f"\n  {fwd['id']}  “{f['subject']}”")
        for it in [i for i in d["items"] if i["email"] == fwd["id"]]:
            emit("text", f"\n    {it['what']} — “{it['said']}”, {show_day(said_on(d, it))} → meant {show_day(truth(d, it))}")
            for c in COPIES:
                r = cen[c]["items"][it["id"]]
                if r["verdict"] == "none":
                    emit("text", f"      {COPY_SHORT[c]}  · not remembered")
                    continue
                head = (f"~ {r['anchor']} + ?" if r["verdict"] == "calc" else
                        f"{MARK[r['verdict']]} {r['got'] or 'no date'}{(' (' + r['off'] + ')') if r['off'] else ''}")
                emit("text", wrap(f"{head:<22} {r['facts'][0]}", f"      {COPY_SHORT[c]}  ", 112))
    for c in COPIES:
        if cen[c]["about_forward"]:
            emit("text", f"\n    {COPY_SHORT[c]} also remembers the forwarding itself:")
            for f in cen[c]["about_forward"][:4]:
                emit("text", wrap(f, "      “", 110) + "”")
    emit("text", "")
    for c in COPIES:
        k = counts(cen[c]["items"])
        emit("text", f"    {COPY_LABEL[c]:<26} right date {k['ok']}/{len(d['items'])} · never resolved {k['calc']} · "
                     f"no date {k['nodate']} · wrong {k['off']} · not remembered {k['none']} · facts about the forward "
                     f"itself {len(cen[c]['about_forward'])}")
    emit("census", "", copies=cen, truth={it["id"]: truth(d, it).isoformat() for it in d["items"]})


# --------------------------------------------------------------------------- step 5: the docket as of today

def board(cen: dict) -> dict:
    d = load_data()
    now = today(d)

    def status(x: date | None) -> str:
        if not x:
            return "missing"
        n = (x - now).days
        return "past" if n < 0 else "today" if n == 0 else "upcoming"
    rows = []
    tally = {c: {"on": 0, "wrong": 0, "missing": 0} for c in COPIES}
    emit("text", f"    today is {show_day(now)}; a deadline is on the docket only when memory gives its date "
                 "(~ = a period from some date, left for the reader to work out)\n")
    emit("text", f"    {'':<46} {'meant':<20} " + "  ".join(f"{COPY_SHORT[c] + ' docket':<29}" for c in COPIES))
    for it in sorted(d["items"], key=lambda i: truth(d, i)):
        want = truth(d, it)
        row = dict(item=it["id"], what=it["what"], want=want.isoformat(), status=status(want), copies={})
        cells = []
        for c in COPIES:
            r = cen[c]["items"][it["id"]]
            got = date.fromisoformat(r["got"]) if r["got"] else None
            s = status(got)
            if row["status"] in ("upcoming", "today"):
                tally[c]["on" if r["verdict"] == "ok" else "wrong" if got else "missing"] += 1
            when = "" if not got else f"in {(got - now).days} d" if s == "upcoming" else f"{(now - got).days} d ago" if s == "past" else "today"
            shown = r["got"] or (f"{r['anchor']} + ?" if r["verdict"] == "calc" else "not on the docket")
            cells.append(f"{MARK[r['verdict']]} {shown:<17} {when:<9}")
            row["copies"][c] = dict(got=r["got"], status=s, verdict=r["verdict"], anchor=r["anchor"])
        rows.append(row)
        span = f"in {(want - now).days} d" if want > now else f"{(now - want).days} d ago" if want < now else "today"
        emit("text", f"    {it['what'][:46]:<46} {want.isoformat()} {span:<9} " + "  ".join(f"{x:<29}" for x in cells))
    open_ = sum(1 for r in rows if r["status"] in ("upcoming", "today"))
    emit("text", "")
    for c in COPIES:
        t = tally[c]
        emit("text", f"    {COPY_LABEL[c]:<26} open deadlines on the docket {t['on']}/{open_}"
                     + (f" · wrong date {t['wrong']}" if t["wrong"] else "") + f" · missing {t['missing']}")
    emit("board", "", today=now.isoformat(), rows=rows, open=open_, tally=tally)
    return dict(today=now.isoformat(), rows=rows, open=open_, tally=tally)


# --------------------------------------------------------------------------- step 6: ask the docketing assistant

def system_prompt(d: dict) -> str:
    return d["system"].format(today=today(d).strftime("%A %Y-%m-%d"))


def ask_rows(cli: CLI, world: dict, c: str, echo_first: bool = True) -> list[dict]:
    d = load_data()
    system = system_prompt(d)
    jobs = [(it, i) for it in d["items"] for i in range(RUNS)]
    for n, it in enumerate(d["items"]):
        baseline(cli, it["ask"], system, echo=echo_first and n == 0)

    def one(job):
        it, i = job
        r = router(cli, it["ask"], system, world["boundaries"][c], echo=echo_first and job is jobs[0])
        if not r:
            return dict(item=it["id"], copy=c, run=i + 1, verdict=None, empty=False, reply="")
        base = BASE[(it["ask"], system)]
        empty = r["prompt_tokens"] <= base
        g = verdict(truth(d, it), answer_date(r["reply"]), True)
        return dict(item=it["id"], copy=c, run=i + 1, reply=r["reply"], prompt_tokens=r["prompt_tokens"], empty=empty, **g)
    if echo_first:
        note(f"each question once without --boundary (the ∅ baseline), then {RUNS} answers through the boundary; 6 at a time")
    with ThreadPoolExecutor(6) as pool:
        rows = list(pool.map(one, jobs))
    emit("text", f"    {'':<46} {'meant':<12} answers")
    for it in d["items"]:
        mine = [r for r in rows if r["item"] == it["id"]]
        got = "  ".join("∅" if r.get("empty") else f"{MARK.get(r['verdict'], '?')} {r['got'] or 'no date'}"
                        + (f" ({r['off']})" if r.get("off") else "") for r in mine)
        emit("text", f"    {it['what'][:46]:<46} {truth(d, it).isoformat():<12} {got}")
        emit("ask_row", "", copy=c, item=it["id"], rows=mine, want=truth(d, it).isoformat())
    return rows


def show_examples(rows: list[dict], c: str) -> None:
    d = load_data()
    picked = [r for r in rows if r.get("verdict") in ("off", "nodate") and not r.get("empty")][:1] or \
             [r for r in rows if r.get("verdict") == "ok" and not r.get("empty")][:1]
    for r in picked:
        it = next(i for i in d["items"] if i["id"] == r["item"])
        emit("text", f"\n    “{it['ask']}”  (meant: {show_day(truth(d, it))})")
        emit("text", wrap(r["reply"], "      | ", 100))
    emit("examples", "", copy=c, rows=picked)


def tally(rows: list[dict]) -> dict:
    live = [r for r in rows if r.get("verdict")]
    n = lambda v: sum(1 for r in live if not r.get("empty") and r["verdict"] == v)  # noqa: E731
    return dict(calls=len(live), empty=sum(1 for r in live if r.get("empty")), ok=n("ok"), off=n("off"),
                nodate=n("nodate") + n("calc"))


def arm_line(t: dict, lab: str) -> str:
    return (f"    {lab:<26} right {t['ok']:>2} · wrong date {t['off']:>2} · no date {t['nodate']:>2} · ∅ {t['empty']:>2}"
            f"   of {t['calls']}")


# --------------------------------------------------------------------------- step 7: score

def score(cen: dict, brd: dict, rows: list[dict]) -> dict:
    d = load_data()
    out: dict = {"memory": {}, "answers": {}, "docket": {}}
    emit("text", f"    remembered deadlines ({len(d['items'])} items), open deadlines on the docket ({brd['open']}), "
                 f"and the assistant's answers ({len(d['items'])} questions × {RUNS}):")
    for c in COPIES:
        k = counts(cen[c]["items"])
        t = tally([r for r in rows if r["copy"] == c])
        out["memory"][c] = dict(k, total=len(d["items"]), about_forward=len(cen[c]["about_forward"]))
        out["answers"][c] = t
        out["docket"][c] = brd["tally"][c]
        emit("text", f"    {COPY_LABEL[c]:<26} memory {k['ok']}/{len(d['items'])} · docket {brd['tally'][c]['on']}/{brd['open']} · "
                     f"answers right {t['ok']}/{t['calls'] - t['empty']}" + (f" (∅ {t['empty']})" if t["empty"] else ""))
    emit("text", "\n    ✓ the date that was meant · ✗ another date (how far off) · ~ only “N days from <date>”, never resolved · "
                 "? remembered with no date · · not remembered · ∅ MemoryRouter attached nothing (prompt tokens equal the "
                 "no-memory call)")
    emit("score", "", **out)
    return out


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    d = load_data()
    BASE.clear()
    banner(2, TOTAL, f"Set up — {d['firm']}'s {d['channel']} channel, two copies")
    world = setup(cli)

    banner(3, TOTAL, "Three forwarded emails — stored as received (A), split by the app (B)")
    ingest(cli, world)

    banner(4, TOTAL, "What got remembered — every deadline in the forwards")
    cen = census(cli, world)
    show_census(cen)

    banner(5, TOTAL, "The docket, as of today")
    brd = board(cen)

    rows: list[dict] = []
    banner(6, TOTAL, "Ask the docketing assistant — through each copy's boundary")
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
    """Each deadline as each copy remembers it, and the docket as of today."""
    world = load_world(cli)
    cen = census(cli, world)
    show_census(cen)
    emit("text", "")
    board(cen)
    emit("memory_view", "", copies=cen)
    return cen


def ask(cli: CLI, question: str) -> list[dict]:
    """Any question, once through A and once through B; graded when it is about one of the six deadlines."""
    d = load_data()
    world = load_world(cli)
    it = next((i for i in d["items"] if re.search(i["match"], question, re.I)), None)
    system = system_prompt(d)
    rows = []
    base = baseline(cli, question, system, echo=True)
    for n, c in enumerate(COPIES):
        r = router(cli, question, system, world["boundaries"].get(c), echo=n == 0)
        if not r:
            die("MemoryRouter is not available on this account (no balance)")
        empty = r["prompt_tokens"] <= base
        g = verdict(truth(d, it), answer_date(r["reply"]), True) if it else dict(verdict=None, got=str(answer_date(r["reply"]) or ""), off="", anchor="")
        head = "∅ nothing attached" if empty else (f"{MARK[g['verdict']]} {g['got'] or 'no date'} {g['off']}".strip() if g["verdict"] else g["got"])
        emit("text", f"\n  {COPY_LABEL[c]}: {head}")
        emit("text", wrap(r["reply"], "    | ", 100))
        rows.append(dict(copy=c, reply=r["reply"], empty=empty, want=it and truth(d, it).isoformat(), **g))
    if it:
        emit("text", f"\n  meant: {show_day(truth(d, it))} (“{it['said']}”, written {show_day(said_on(d, it))})")
    emit("asked", "", question=question, item=it and it["id"], rows=rows, want=it and truth(d, it).isoformat())
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


EXAMPLE = "When is our response to the Japanese Office Action in QR-2291-JP due?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "memory", "ask", "cleanup"],
                    help="run = full demo (default); memory = each deadline as each copy remembers it, and the docket; "
                         "ask \"<question>\" = one question through A and B; cleanup = delete everything")
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
        banner(2, 2, "Each deadline, as each copy remembers it")
        memory(cli)
        return
    if args.command == "ask":
        if len(args.args) != 1:
            die(f'usage: python3 demo.py ask "<question>"   (e.g. "{EXAMPLE}")')
        banner(2, 2, "One question to the docketing assistant, through A and B")
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

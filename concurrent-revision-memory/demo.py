#!/usr/bin/env python3
"""concurrent-revision-memory — three agents revise a shared plan in the same minute: what memory keeps, and who decided.

Corvane Labs (fictional) is launching Halyard 2.0. A Planner agent recorded the launch review: Thursday, October 22,
owned by Ben Ito. Two days later three agents report to shared memory in the same minute, each in its own conversation:

  Scheduler agent  the review moves to Friday, October 23 (ops freeze)
  QA-gate agent    the review moves to Monday, October 26 (security sign-off)
  Staffing agent   Ana Ruiz now owns the review (Ben is on leave)

MemoryLake extracts facts from all three conversations at once. The owner, which one agent changed, is revised
cleanly. The date, which two agents changed without seeing each other, ends up as whichever extraction finished
last; the other value survives only inside "previously …", and no conflict is raised. `fact trace` says which
conversation made each revision, and the message times say whether its author could have seen the value it
replaced. The demo flags each blind overwrite, then the engineering lead decides, and the audit runs again.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key.
No model calls: nothing is billed to your MemoryLake balance.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-crv"  # memorylake-usecases / concurrent-revision-memory


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

    # `conv msg append` is retried too: the same --custom-id returns the message created the first time.
    READ_ONLY = {"get", "list", "trace", "status", "current", "me", "search", "cook-status", "append"}
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
            # keep the flags after the texts (--project, --workspace): they say where the facts go
            cut = next((i for i in range(2, len(shown)) if shown[i].startswith("--")), len(shown))
            tail = shown[cut:]
            body = shown[:cut]
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
    rc, out, _ = cli.raw("fact", "conflict", "--help", echo=False)
    if rc != 0:
        die("this memorylake CLI has no `fact conflict` command; it needs v20261015 or later.\n"
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
    return cli


# --------------------------------------------------------------------------- settings

TOTAL = 7             # steps in a full run
COOK_TIMEOUT = 600    # seconds to wait for extraction
POLL = 4
WINDOW = 90           # seconds to watch for conflicts after the last extraction (detection lands 10–20 s after a write)


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("halyard"), "--by-custom-id", scoped=True, echo=echo)


def paged(cli: CLI, args: list[str], echo: bool = False, scoped: bool = True) -> list[dict]:
    out, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=scoped, echo=echo and token is None) or {}
        out += page.get("items") or [] if isinstance(page, dict) else page
        token = page.get("continuation_token") if isinstance(page, dict) else None
        if not token:
            return out


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


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def conversations(d: dict) -> list[dict]:
    return [d["kickoff"], *d["reports"]]


# What a fact says now: the text before the first "(as of" — a revised fact carries its old values in "previously …".
def head(text: str) -> str:
    return re.split(r"\s*\(as of\b", text or "", maxsplit=1)[0].strip()


DATE_RE = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b|\bOctober \d+")
OWNER_RE = re.compile(r"\bown(s|ed|er)\b|\bresponsible\b", re.I)


def fields_of(text: str) -> set[str]:
    """Which part of the plan a fact is about, read from what it says now (not from its history)."""
    h = head(text)
    out = set()
    if DATE_RE.search(h):
        out.add("date")
    if OWNER_RE.search(h):
        out.add("owner")
    return out


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
DAY_RE = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+(?:on\s+)?"
                    r"(?:(October \d+)|(\d{4})-(\d{2})-(\d{2}))")


def value_of(text: str, field: str) -> str:
    """The value a fact gives now, in one spelling: "Friday, October 23" (extraction also writes 2026-10-23)."""
    h = head(text)
    if field == "date":
        m = DAY_RE.search(h)
        if m:
            day = m.group(2) or f"{MONTHS[int(m.group(4)) - 1]} {int(m.group(5))}"
            return f"{m.group(1)}, {day}"
        m = DATE_RE.search(h)
        return m.group(0) if m else h
    m = re.search(r"\b(Ana Ruiz|Ben Ito)\b", h)
    return m.group(1) if m else h


def short(iso: str) -> str:
    return (iso or "")[11:19]


def check_parsers() -> None:
    """The readers on known texts, before any call is made: a wrong rule would misread every run."""
    cases = [
        ("The Halyard launch review is scheduled for Monday, October 26. (as of 2026-10-14, previously Friday, "
         "October 23, 2026 as of 2026-10-14, previously on Thursday, October 22, 2026. as of 2026-10-12)",
         {"date"}, "Monday, October 26"),
        ("Ana Ruiz owns the Halyard launch review (as of 2026-10-14, previously Ben Ito as of 2026-10-12).",
         {"owner"}, "Ana Ruiz"),
        ("The Halyard launch review is owned by Ana Ruiz (as of 2026-10-14, previously Ben Ito owns the Halyard "
         "launch review as of 2026-10-12).", {"owner"}, "Ana Ruiz"),
        ("The Halyard launch review is on Thursday, October 22, and Ben Ito owns it. (as of 2026-10-12)",
         {"date", "owner"}, None),
    ]
    for text, fields, value in cases:
        got = fields_of(text)
        if got != fields:
            die(f"parser self-check failed on “{text[:60]}…”: fields {got}, expected {fields}")
        if value and value_of(text, next(iter(fields))) != value:
            die(f"parser self-check failed on “{text[:60]}…”: value {value_of(text, next(iter(fields)))}")
    if value_of("The Halyard launch review is on Friday, 2026-10-23 (as of 2026-10-14, previously Monday, October 26 "
                "as of 2026-10-14).", "date") != "Friday, October 23":
        die("parser self-check failed on an ISO date")
    x = load_data()["decision"]
    for kind, fields in (("date", {"date"}), ("owner", {"owner"}), ("both", {"date", "owner"})):
        t = x["text"][kind].format(**x["values"]) + x["suffix"]
        if fields_of(t) != fields or any(value_of(t, f) != x["values"][f] for f in fields):
            die(f"parser self-check failed on the decision text “{t}”")
    if fields_of("Ops freeze runs through Thursday.") != {"date"} or head("x (as of 2026-10-14)") != "x":
        die("parser self-check failed on the head / field rules")


# --------------------------------------------------------------------------- step 2: setup

def find_actor(cli: CLI, key: str):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id", echo=False)


def leftovers(cli: CLI, d: dict) -> bool:
    return bool(find_project(cli)) or any(find_actor(cli, k) for k in d["people"])


def setup(cli: CLI, d: dict) -> dict:
    if leftovers(cli, d):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {"actors": {}}
    world["project"] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("halyard"),
                               scoped=True, echo=True)["id"]
    for key, p in d["people"].items():
        a = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", p["display"],
                    "--description", p["role"], echo=key == "mara")
        cli.run("actor", "bind", "--actor", a["id"], scoped=True, echo=key == "mara")
        world["actors"][key] = a["id"]
    note(f"one shared project and {len(d['people'])} actors: " + ", ".join(p["display"] for p in d["people"].values()))
    emit("world", "", project=world["project"], actors=world["actors"],
         people={k: p["display"] for k, p in d["people"].items()})
    return world


def load_world(cli: CLI, d: dict) -> dict:
    p = find_project(cli)
    if not p:
        die("the demo memory does not exist yet; run `python3 demo.py` first")
    world = {"project": p["id"], "actors": {}}
    for key in d["people"]:
        a = find_actor(cli, key)
        if a:
            world["actors"][key] = a["id"]
    return world


# --------------------------------------------------------------------------- conversations

def open_conversation(cli: CLI, d: dict, world: dict, conv: dict, echo: bool = True) -> str:
    members = ",".join(world["actors"][k] for k in ("mara", conv["agent"]))
    c = cli.run("conv", "create", "--custom-id", cid(conv["key"]), "--project", world["project"], "--actors", members,
                "--kind", "GROUP", "--name", conv["name"], "--metadata", f"agent={d['people'][conv['agent']]['display']}",
                scoped=True, echo=echo)
    return c["id"]


def append_turns(cli: CLI, world: dict, conv_id: str, conv: dict, date: str, echo: bool = True) -> None:
    start = datetime.fromisoformat(date.replace("Z", "+00:00"))
    parent = None
    for i, (who, text) in enumerate(conv["turns"], start=1):
        ts = (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv_id, "--actor", world["actors"][who], "--custom-id", f"turn-{i:02d}",
                "--timestamp", ts, "--text", text]
        if parent:
            args += ["--parent", parent]
        parent = cli.run(*args, scoped=True, echo=echo)["id"]


def wait_for_memory(cli: CLI, conv_ids: dict, label: str) -> dict:
    """Messages are stored at once; facts are extracted in the background. Poll until each conversation is done."""
    pending = dict(conv_ids)
    done: dict = {}
    t0 = last = time.time()
    first = True
    while pending and time.time() - t0 < COOK_TIMEOUT:
        for key, c in sorted(pending.items()):
            try:
                st = cli.run("conv", "cook-status", c, scoped=True, echo=first)
            except CLIError as e:
                if not e.has(*CLI.TRANSIENT):
                    raise
                continue
            first = False
            if st.get("cook_finished"):
                done[key] = round(time.time() - t0)
                pending.pop(key)
                emit("cooked", "", key=key, after=done[key])
        if pending:
            if time.time() - last >= 20:
                emit("progress", f"    … {len(pending)} {label} still being processed ({int(time.time() - t0)} s)")
                last = time.time()
            time.sleep(POLL)
    if pending:
        die(f"extraction did not finish in {COOK_TIMEOUT} s for: {', '.join(pending)}; run the demo again")
    return done


def review_facts(cli: CLI, d: dict, world: dict, echo: bool = False) -> list[dict]:
    """Every live fact about the launch review, from the project and from each actor (extraction picks the scope)."""
    seen: dict = {}
    scopes = [("project", ["--projects", world["project"]])] + \
             [(k, ["--actors", a]) for k, a in world["actors"].items()]
    for name, flag in scopes:
        for f in paged(cli, ["fact", "list", *flag, "--page-size", "200"], echo=echo and name == "project"):
            if d["subject"] in (f.get("fact") or "").lower() and f["id"] not in seen:
                f["_scope"] = name
                seen[f["id"]] = f
    return list(seen.values())


def show_facts(facts: list[dict], indent: str = "    ") -> None:
    for f in sorted(facts, key=lambda f: f.get("created_at") or ""):
        tag = "/".join(sorted(fields_of(f["fact"]))) or "other"
        emit("text", wrap(f"[{tag}] {f['fact']}", indent, 104))


# --------------------------------------------------------------------------- step 3: kickoff

def kickoff(cli: CLI, d: dict, world: dict) -> dict:
    k = d["kickoff"]
    for who, text in k["turns"]:
        emit("text", wrap(text, "    ", 100))
    c = open_conversation(cli, d, world, k)
    append_turns(cli, world, c, k, k["date"])
    world.setdefault("convs", {})[k["key"]] = c
    after = wait_for_memory(cli, {k["key"]: c}, "conversation")
    facts = review_facts(cli, d, world, echo=True)
    emit("text", "\n  What the team's memory says about the launch review:")
    show_facts(facts)
    note(f"memory ready in {after[k['key']]} s — {len(facts)} fact(s) about the launch review")
    emit("kickoff", "", facts=[dict(id=f["id"], text=f["fact"], fields=sorted(fields_of(f["fact"]))) for f in facts])
    return {"conv": c, "facts": facts}


# --------------------------------------------------------------------------- step 4: three reports, one minute

def reports(cli: CLI, d: dict, world: dict) -> dict:
    """Three agents report in the same minute, each in its own conversation; nobody waits for anybody."""
    for r in d["reports"]:
        emit("text", f"    {d['people'][r['agent']]['display']:<16} {r['field']} → {r['value']}")
    gate = threading.Barrier(len(d["reports"]))
    ids: dict = {}
    lock = threading.Lock()

    def report(r):
        gate.wait()
        c = open_conversation(cli, d, world, r)
        append_turns(cli, world, c, r, d["reports_date"])
        with lock:
            ids[r["key"]] = c
        emit("progress", f"    {d['people'][r['agent']]['display']} reported → {c}", key=r["key"], conv=c)

    with ThreadPoolExecutor(len(d["reports"])) as pool:
        list(pool.map(report, d["reports"]))
    world.setdefault("convs", {}).update(ids)
    after = wait_for_memory(cli, ids, "reports")
    order = sorted(after, key=lambda k: after[k])
    note("all three reports processed: " + " · ".join(f"{d['people'][k]['display']} {after[k]} s" for k in order))
    emit("reports", "", convs=ids, cooked=after)
    return {"convs": ids, "cooked": after}


# --------------------------------------------------------------------------- step 5: what memory says now

def conflicts(cli: CLI, world: dict, echo: bool = True) -> list[dict]:
    return paged(cli, ["fact", "conflict", "list", "--project", world["project"]], echo=echo)


def watch_conflicts(cli: CLI, world: dict, since: float, echo: bool = True) -> list[dict]:
    """Detection runs in the background after a write: watch the whole window before saying "none"."""
    first = True
    while True:
        cs = conflicts(cli, world, echo=echo and first)
        first = False
        waited = int(time.time() - since)
        if waited >= WINDOW:
            return cs
        emit("progress", f"    {waited:>3} s after the last report was processed — conflicts: {len(cs)}", waited=waited,
             found=len(cs))
        time.sleep(min(15, max(1, WINDOW - waited)))


def show_conflicts(cs: list[dict], facts: list[dict]) -> list[dict]:
    names = {f["id"]: "/".join(sorted(fields_of(f["fact"]))) or "other" for f in facts}
    out = []
    for c in cs:
        about = ", ".join(names.get(i, "non-review") for i in c.get("fact_ids") or [])
        emit("text", f"    conflict · {c.get('category')} · {c.get('conflict_type')} · on the {about} fact"
                     f"{' · resolved' if c.get('resolve') else ''}")
        # The name and description are written by a model, in whatever language it picks.
        emit("text", wrap(f"“{c.get('name')}” — {c.get('description')}", "        ", 100))
        out.append(dict(id=c["id"], category=c.get("category"), about=about, name=c.get("name"),
                        description=c.get("description"), created_at=c.get("created_at")))
    return out


def memory_now(cli: CLI, d: dict, world: dict, echo: bool = True, since: float | None = None) -> dict:
    facts = review_facts(cli, d, world, echo=echo)
    emit("text", "\n  What the team's memory says about the launch review now:")
    show_facts(facts)
    now: dict = {}
    for field in FIELDS:
        live = [f for f in facts if field in fields_of(f["fact"])]
        now[field] = sorted({value_of(f["fact"], field) for f in live})
    emit("text", "")
    for field in FIELDS:
        vals = now[field]
        emit("text", f"    {field:<6} {' | '.join(vals) if vals else '(nothing)'}"
                     + ("   ← two answers live at once" if len(vals) > 1 else ""))
    if since is None:
        cs = conflicts(cli, world, echo=echo)
    else:
        note(f"watching the project for conflicts until {WINDOW} s after the last report was processed")
        cs = watch_conflicts(cli, world, since, echo=echo)
    emit("text", f"\n    conflicts raised on the project: {len(cs)}")
    shown = show_conflicts(cs, facts)
    emit("memory", "", facts=[dict(id=f["id"], text=f["fact"], fields=sorted(fields_of(f["fact"])), scope=f["_scope"])
                              for f in facts], now=now, conflicts=shown)
    return {"facts": facts, "now": now, "conflicts": shown}


# --------------------------------------------------------------------------- step 6: audit with fact trace

def message_times(cli: CLI, conv_id: str, cache: dict) -> dict:
    """entry id → when the server stored it (created_at), for one conversation."""
    if conv_id not in cache:
        items = paged(cli, ["conv", "msg", "list", conv_id, "--page-size", "50"], echo=False, scoped=False) if conv_id else []
        cache[conv_id] = {m["id"]: m.get("created_at") or "" for m in items}
    return cache[conv_id]


FIELDS = ("date", "owner")


def audit(cli: CLI, d: dict, world: dict, facts: list[dict], echo: bool = True) -> dict:
    """For every change to the date or the owner: who made it, and could they have seen the value it replaced?

    Walk each fact's trace oldest first and follow each field's value. A change replaces the value set at the last
    change of that field. If every message the change was extracted from was stored before that value existed, its
    author never saw it: a blind overwrite, decided by which extraction finished last.
    """
    by_conv = {c: k for k, c in (world.get("convs") or {}).items()}
    agent_of = {x["key"]: d["people"][x["agent"]]["display"] for x in conversations(d)}
    cache: dict = {}
    changes, entries_out = [], []
    for f in sorted(facts, key=lambda f: f.get("created_at") or ""):
        scope = ["--project", world["project"]] if f["_scope"] == "project" else ["--actor", world["actors"][f["_scope"]]]
        tr = cli.run("fact", "trace", f["id"], *scope, scoped=True, echo=echo)
        entries = sorted(tr.get("trace") or [], key=lambda e: e.get("timestamp") or "")
        state: dict = {}  # field -> (value, set_at, set_by)
        emit("text", f"\n  fact {f['id']}  ({'/'.join(sorted(fields_of(f['fact']))) or 'other'})")
        for e in entries:
            key = by_conv.get(e.get("conversation_id") or "", "")
            manual = e.get("source_kind") == "MANUAL"
            who = f"{d['people'][d['decision']['by']]['display']} (fact update)" if manual else \
                agent_of.get(key, e.get("source_kind") or "?")
            text = e.get("new_fact") or ""
            at = e.get("timestamp") or ""
            spoke = ""
            if e.get("event") == "UPDATE":
                if manual:
                    spoke = at  # a direct write: its author is the caller, at the moment of the call
                else:
                    times = message_times(cli, e.get("conversation_id") or "", cache)
                    spoke = max((times.get(i, "") for i in e.get("source_entry_ids") or []), default="")
            emit("text", f"    {short(at)}  {e.get('event', ''):<7} {who:<16} “{head(text)}”")
            mine = []
            for field in FIELDS:
                if field not in fields_of(text):
                    continue
                v = value_of(text, field)
                old = state.get(field)
                # A direct write that keeps the value is still a decision about it: it is recorded as one.
                if e.get("event") == "UPDATE" and old and (v != old[0] or manual):
                    blind = bool(spoke) and spoke < old[1]
                    c = dict(fact=f["id"], field=field, old=old[0], new=v, by=who, conv=key, at=at, spoke=spoke,
                             replaced_by=old[2], replaced_at=old[1], verdict="BLIND" if blind else "INFORMED")
                    changes.append(c)
                    mine.append(c)
                    mark = "✗ BLIND   " if blind else "✓ INFORMED"
                    move = f"{old[0]} → {v}" if v != old[0] else f"{v} (kept)"
                    emit("text", f"              {mark} {field}: {move}   ({old[2]}'s value arrived "
                                 f"{short(old[1])}; {who} spoke {short(spoke)})")
                if not old or v != old[0] or manual:
                    state[field] = (v, at, who)
            if e.get("event") == "UPDATE" and not mine:
                emit("text", "              (no change to the date or the owner)")
            entries_out.append(dict(fact=f["id"], event=e.get("event"), at=at, by=who, conv=key, text=text, spoke=spoke,
                                    changes=[(c["field"], c["old"], c["new"], c["verdict"]) for c in mine]))
    blind = [c for c in changes if c["verdict"] == "BLIND"]
    for c in blind:
        note(f"{c['by']} changed the {c['field']} {c['old']} → {c['new']} without having seen {c['old']}: "
             f"{c['by']} spoke at {short(c['spoke'])}, {c['replaced_by']}'s {c['old']} arrived at {short(c['replaced_at'])}")
    if not blind:
        note("every change was made by an author who could have seen the value it replaced")
    emit("audit", "", entries=entries_out, changes=changes, blind=len(blind))
    return {"entries": entries_out, "changes": changes, "blind": blind}


def report_outcomes(d: dict, now: dict, aud: dict) -> list[dict]:
    """What each agent's report did to memory: applied and kept, applied then overwritten, or never applied."""
    out = []
    for r in d["reports"]:
        who = d["people"][r["agent"]]["display"]
        field, want = r["field"], r["value"]
        mine = [e for e in aud["entries"] if e["conv"] == r["key"]]
        hit = [e for e in mine if field in fields_of(e["text"]) and value_of(e["text"], field) == want]
        later = [c for c in aud["changes"] if c["field"] == field and c["old"] == want and hit and c["at"] > hit[0]["at"]]
        if hit and want in now[field] and len(now[field]) > 1:
            state, how = "UNRESOLVED", f"applied {short(hit[0]['at'])}, but {' | '.join(v for v in now[field] if v != want)} is still live too"
        elif hit and now[field] == [want]:
            state, how = "KEPT", f"applied {short(hit[0]['at'])} — memory says this now"
        elif hit and later:
            c = later[0]
            state = "OVERWRITTEN"
            how = f"applied {short(hit[0]['at'])}, then overwritten {'blind ' if c['verdict'] == 'BLIND' else ''}by {c['by']}"
        elif mine:
            left = sorted({value_of(e["text"], field) for e in mine if field in fields_of(e["text"])})
            state, how = "NOT APPLIED", f"its update left {' | '.join(left) or 'something else'}"
        else:
            state, how = "NOT APPLIED", "no fact in memory was written from this report"
        out.append(dict(agent=who, field=field, value=want, state=state, how=how))
    emit("text", "\n    report                                          what it did to memory")
    for o in out:
        mark = {"KEPT": "✓", "OVERWRITTEN": "✗", "NOT APPLIED": "✗", "UNRESOLVED": "✗"}[o["state"]]
        emit("text", f"    {o['agent']:<16} {o['field']} → {o['value']:<20}  {mark} {o['how']}")
    emit("outcomes", "", outcomes=out)
    return out


def verdict(d: dict, now: dict, aud: dict) -> dict:
    """Per field: is what memory says now the result of a decision, or of arrival order?"""
    out = {}
    for field in FIELDS:
        ch = sorted([c for c in aud["changes"] if c["field"] == field], key=lambda c: c["at"] or "")
        cur = ch[-1] if ch else None
        vals = now[field]
        if len(vals) > 1:
            state = "UNRESOLVED"
        elif cur and cur["verdict"] == "BLIND":
            state = "ARRIVAL ORDER"
        elif cur:
            state = "DECIDED"
        else:
            state = "UNCHANGED"
        lost = sorted({c["old"] for c in ch if c["verdict"] == "BLIND" and c["old"] != c["new"]} - set(vals))
        out[field] = dict(now=vals, state=state, by=cur["by"] if cur else None, lost=lost)
    return out


def print_verdict(v: dict) -> None:
    emit("text", "\n    field   memory says now          how it got there")
    for field, x in v.items():
        how = {"DECIDED": f"✓ changed by {x['by']}, who had seen the value it replaced",
               "ARRIVAL ORDER": f"✗ {x['by']}'s report was processed last; nobody chose it",
               "UNRESOLVED": "✗ two answers live at once",
               "UNCHANGED": "· never revised"}[x["state"]]
        emit("text", f"    {field:<7} {' | '.join(x['now']) or '(nothing)':<24} {how}")
        if x["lost"]:
            emit("text", f"            overwritten without being seen: {', '.join(x['lost'])} (now only in “previously …”)")


# --------------------------------------------------------------------------- step 7: the lead decides

def decide(cli: CLI, d: dict, world: dict, mem: dict) -> dict:
    """The lead reads the audit and decides. The decision goes onto the facts themselves, so their trace shows who chose.

    Said in a conversation instead, a decision that agrees with what memory already holds changes nothing: extraction
    leaves the fact as it is, and its trace still ends with the blind overwrite.
    """
    x = d["decision"]
    vals = x["values"]
    emit("text", wrap(f"{d['people'][x['by']]['display']}: the review is on {vals['date']} and {vals['owner']} owns it — "
                      f"{x['reason']}.", "    ", 100))
    holding = {field: [f for f in mem["facts"] if field in fields_of(f["fact"])] for field in FIELDS}
    if not any(holding.values()):
        die("memory holds nothing about the launch review to decide on; run the demo again")
    # The newest fact for each field gets the decision; any other fact that still answers it is removed.
    chosen = {}
    for field, fs in holding.items():
        if fs:
            f = max(fs, key=lambda f: f.get("updated_at") or f.get("created_at") or "")
            chosen[f["id"]] = f
    written = []
    for f in chosen.values():
        fl = fields_of(f["fact"])
        kind = "both" if fl >= {"date", "owner"} else next(iter(fl))
        text = x["text"][kind].format(**vals) + x["suffix"]
        scope = ["--project", world["project"]] if f["_scope"] == "project" else ["--actor", world["actors"][f["_scope"]]]
        cli.run("fact", "update", f["id"], "--text", text, *scope, scoped=True, echo=True)
        written.append(dict(fact=f["id"], body=text))
    gone = []
    for f in {f["id"]: f for fs in holding.values() for f in fs}.values():
        if f["id"] not in chosen:
            scope = ["--project", world["project"]] if f["_scope"] == "project" else ["--actor", world["actors"][f["_scope"]]]
            cli.run("fact", "delete", f["id"], *scope, scoped=True, echo=True)
            gone.append(f["id"])
    note(f"decision written onto {len(written)} fact(s)" + (f"; {len(gone)} other fact(s) that still answered it removed"
                                                            if gone else ""))
    emit("decision", "", written=written, removed=gone)
    return {"written": written, "removed": gone}


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_parsers()
    banner(2, TOTAL, "Setup — one shared project, an engineering lead and four agents")
    world = setup(cli, d)
    banner(3, TOTAL, "Monday — the Planner agent records the launch review")
    kick = kickoff(cli, d, world)
    banner(4, TOTAL, "Wednesday 09:01 — three agents report in the same minute")
    rep = reports(cli, d, world)
    cooked_at = time.time()
    banner(5, TOTAL, "What memory says now — and what got flagged")
    mem = memory_now(cli, d, world, since=cooked_at)
    banner(6, TOTAL, "Audit — fact trace: who revised what, and had they seen it?")
    aud = audit(cli, d, world, mem["facts"])
    outcomes = report_outcomes(d, mem["now"], aud)
    before = verdict(d, mem["now"], aud)
    print_verdict(before)
    emit("verdict", "", stage="before", verdict=before)
    banner(7, TOTAL, "Thursday — the engineering lead decides, on the record; audit again")
    decide(cli, d, world, mem)
    mem2 = memory_now(cli, d, world, echo=False)
    aud2 = audit(cli, d, world, mem2["facts"], echo=False)
    after = verdict(d, mem2["now"], aud2)
    print_verdict(after)
    emit("verdict", "", stage="after", verdict=after)
    date_was = before["date"]
    note(f"before the decision the date was {date_was['state'].lower()} ({' | '.join(date_was['now'])}); "
         f"after it: {after['date']['state'].lower()} ({' | '.join(after['date']['now'])}), "
         f"conflicts on the project: {len(mem['conflicts'])} after the reports, {len(mem2['conflicts'])} after the decision")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, kickoff=[f["fact"] for f in kick["facts"]],
                  cooked=rep["cooked"], memory=mem["now"], conflicts=mem["conflicts"], audit=aud["changes"],
                  outcomes=outcomes,
                  verdict=before, after=dict(memory=mem2["now"], conflicts=mem2["conflicts"], audit=aud2["changes"],
                                             verdict=after))
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (OUT / "world.json").write_text(json.dumps(world, indent=2), encoding="utf-8")
    emit("result", "", report=report)
    return report


# --------------------------------------------------------------------------- read-only subcommands

def _world_with_convs(cli: CLI, d: dict) -> dict:
    world = load_world(cli, d)
    world["convs"] = {}
    for x in conversations(d):
        c = cli.try_run("conv", "get", cid(x["key"]), "--by-custom-id", scoped=True, echo=False)
        if c:
            world["convs"][x["key"]] = c["id"]
    return world


def show_audit(cli: CLI) -> None:
    d = load_data()
    world = _world_with_convs(cli, d)
    mem = memory_now(cli, d, world, echo=False)
    aud = audit(cli, d, world, mem["facts"])
    report_outcomes(d, mem["now"], aud)
    print_verdict(verdict(d, mem["now"], aud))


def show_facts_cmd(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli, d)
    memory_now(cli, d, world)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    nfacts = nconv = 0
    p = find_project(cli, echo=not quiet)
    actors = {k: a for k in d["people"] if (a := find_actor(cli, k))}
    # Conversations are workspace-scoped: deleting the project does not remove them.
    for x in conversations(d):
        c = cli.try_run("conv", "get", cid(x["key"]), "--by-custom-id", scoped=True, echo=False)
        if c:
            cli.run("conv", "delete", c["id"], scoped=True, echo=not quiet and nconv == 0)
            nconv += 1
    # Facts first: a deleted actor's facts stay readable and can no longer be deleted.
    scopes = ([["--projects", p["id"]]] if p else []) + [["--actors", a["id"]] for a in actors.values()]
    for flag in scopes:
        dscope = ["--project" if flag[0] == "--projects" else "--actor", flag[1]]
        for attempt in range(4):
            left = paged(cli, ["fact", "list", *flag, "--page-size", "200"])
            if not left:
                break
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_fact(cli, f["id"], dscope), left))
            nfacts += len(left)
            time.sleep(2 * attempt)
        if paged(cli, ["fact", "list", *flag, "--page-size", "200"]):
            die(f"facts are still in {flag[1]} after deleting; run cleanup again")
    if p:
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet)
    for k, a in actors.items():
        cli.run("actor", "delete", a["id"], echo=not quiet and k == "mara")
    if not quiet:
        note(f"deleted {nfacts} facts, {nconv} conversations, {1 if p else 0} project and {len(actors)} actors"
             if (p or actors or nconv) else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "world"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "audit", "facts", "cleanup"],
                    help="run = full demo (default); audit = who revised each launch-review fact, and whether they "
                         "had seen what they replaced; facts = what memory says now; cleanup = delete everything")
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
    if args.command == "audit":
        banner(2, 2, "Audit — fact trace on every launch-review fact")
        show_audit(cli)
        return
    if args.command == "facts":
        banner(2, 2, "What memory says about the launch review")
        show_facts_cmd(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py audit` or `python3 demo.py facts`; "
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

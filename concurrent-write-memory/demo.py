#!/usr/bin/env python3
"""concurrent-write-memory — six agents write to shared memory at once: which writes survive, and what gets flagged.

Kesswick Labs (fictional) has 20 seats on Tamsor, a design tool; 12 are in use. On Monday at 09:00 six people start,
and six onboarding agents — one per new hire — each give their person a seat and record it in shared memory, all at
the same moment. The same race runs three ways, each in its own project:

  A  one counter fact: each agent reads it (`fact get`), adds one, writes it back (`fact update`)
  B  one record per assignment (`fact add`), the seat as subject: "Tamsor seat assigned to …"
  C  one record per assignment (`fact add`), the person as subject: "<name> holds one of the 20 Tamsor seats …"

Then: what each memory says is in use (the truth is 18), what `fact trace` shows about the counter, and what
MemoryLake's conflict detection flags. A lost update is not a contradiction — six writes of "13 of 20" agree.

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
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cwr"  # memorylake-usecases / concurrent-write-memory


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

    # `fact update` is retried too: setting the same text twice is the same as once.
    READ_ONLY = {"get", "list", "trace", "status", "current", "me", "search", "update"}
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

TOTAL = 6             # steps in a full run
WINDOW = 150          # seconds to watch for conflicts after the last write (detection lands in 10–110 s)
POLL = 15
ARMS = ("A", "B", "C")
DETECTOR = "latest-source"  # what the page's "configurable resolution strategies" suggests: keep the newest write


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, arm: str, echo: bool = False):
    return cli.try_run("proj", "get", cid("pool", arm.lower()), "--by-custom-id", scoped=True, echo=echo)


def paged(cli: CLI, args: list[str], echo: bool = False) -> list[dict]:
    out, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or [] if isinstance(page, dict) else page
        token = page.get("continuation_token") if isinstance(page, dict) else None
        if not token:
            return out


def project_facts(cli: CLI, project: str, query: str | None = None, echo: bool = False) -> list[dict]:
    args = ["fact", "list", "--projects", project, "--page-size", "200"]
    if query:
        args += ["--query", query]
    return paged(cli, args, echo)


def conflicts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "conflict", "list", "--project", project], echo)


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


COUNT_RE = re.compile(r"(\d+) of (\d+) in use")
WHO_RE = re.compile(r"last assigned: (.+?) by (onboard-agent-\d+)")


def counter_value(text: str) -> int | None:
    m = COUNT_RE.search(text or "")
    return int(m.group(1)) if m else None


def counter_who(text: str) -> str:
    m = WHO_RE.search(text or "")
    return m.group(1) if m else "the starting count"


def hhmmss(iso: str) -> str:
    return (iso or "")[11:23]


def check_parsers() -> None:
    """The parsers on known texts, before any call is made: a wrong rule would misread every run."""
    d = load_data()
    a = d["arms"]["A"]
    cases = [(a["seed"][0], 12, "the starting count"),
             (a["write"].format(n=13, name="Nadia Kerr", agent="onboard-agent-1"), 13, "Nadia Kerr"),
             ("Kesswick Labs Tamsor seats: 20 of 20 in use (last assigned: Lena Moretti by onboard-agent-3).", 20, "Lena Moretti")]
    for text, n, who in cases:
        if counter_value(text) != n or counter_who(text) != who:
            die(f"parser self-check failed on “{text}”: {counter_value(text)}, {counter_who(text)}")
    for arm in ("B", "C"):
        x = d["arms"][arm]
        rec = x["write"].format(name="Nadia Kerr", email="nadia.kerr@kesswick.test", agent="onboard-agent-1")
        if x["query"] not in rec or any(x["query"] in t for t in x["seed"]):
            die(f"query self-check failed on arm {arm}: the query must match a record and not the seed fact")
    if d["arms"]["B"]["query"] in d["arms"]["C"]["write"] or d["arms"]["C"]["query"] in d["arms"]["B"]["write"]:
        die("query self-check failed: B and C queries overlap")


# --------------------------------------------------------------------------- step 2: setup

def leftovers(cli: CLI) -> bool:
    return any(find_project(cli, arm) for arm in ARMS)


def setup(cli: CLI, d: dict) -> dict:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {}
    for arm in ARMS:
        x = d["arms"][arm]
        emit("text", f"\n  {x['title']}")
        emit("text", wrap(x["how"], "    ", 100))
        # Same project name for all three: only the custom_id says which arm it is.
        world[arm] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("pool", arm.lower()),
                             scoped=True, echo=True)["id"]
        world[arm + "_seed"] = []
        if x["seed"]:
            r = cli.run("fact", "add", *x["seed"], "--project", world[arm], scoped=True, echo=True)
            world[arm + "_seed"] = [f["id"] for f in (r or {}).get("facts") or []]
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    world = {}
    for arm in ARMS:
        p = find_project(cli, arm)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world[arm] = p["id"]
    return world


# --------------------------------------------------------------------------- step 3: six agents at once

def race(cli: CLI, d: dict, world: dict) -> dict:
    """Eighteen agent jobs — six per arm — released at the same instant. Every command is real and echoed."""
    hires = d["hires"]
    jobs = [(arm, h) for arm in ARMS for h in hires]
    gate = threading.Barrier(len(jobs))
    log: dict = {arm: [] for arm in ARMS}
    lock = threading.Lock()
    a_fact = world["A_seed"][0]

    def agent(job):
        arm, h = job
        x = d["arms"][arm]
        p = world[arm]
        gate.wait()
        t0 = datetime.now(timezone.utc).isoformat()
        if arm == "A":
            got = cli.run("fact", "get", a_fact, "--project", p, scoped=True, echo=True)
            seen = got["fact"]
            n = counter_value(seen)
            text = x["write"].format(n=n + 1, name=h["name"], agent=h["agent"])
            cli.run("fact", "update", a_fact, "--text", text, "--project", p, scoped=True, echo=True)
            row = dict(agent=h["agent"], name=h["name"], read=n, read_text=seen, wrote=n + 1, text=text, at=t0)
            line = f"    A  {h['agent']}  read {n} → wrote {n + 1}  ({h['name']})"
        else:
            text = x["write"].format(name=h["name"], email=h["email"], agent=h["agent"])
            r = cli.run("fact", "add", text, "--project", p, scoped=True, echo=True)
            fid = ((r or {}).get("facts") or [{}])[0].get("id")
            row = dict(agent=h["agent"], name=h["name"], text=text, fact=fid, at=t0)
            line = f"    {arm}  {h['agent']}  added a record for {h['name']}"
        with lock:
            log[arm].append(row)
        emit("progress", line, arm=arm, agent=h["agent"], who=h["name"], read=row.get("read"), wrote=row.get("wrote"))
        return row

    with ThreadPoolExecutor(len(jobs)) as pool:
        list(pool.map(agent, jobs))
    last = datetime.now(timezone.utc).isoformat()
    accepted = {arm: len(log[arm]) for arm in ARMS}
    note(f"every write was accepted: A {accepted['A']} updates · B {accepted['B']} adds · C {accepted['C']} adds — "
         "no command failed")
    emit("race", "", log=log, finished=last)
    return {"log": log, "finished": last}


# --------------------------------------------------------------------------- step 4: what memory says now

def read_counts(cli: CLI, d: dict, world: dict, echo: bool = True) -> dict:
    before = d["in_use_before"]
    out = {}
    a = cli.run("fact", "get", world["A_seed"][0], "--project", world["A"], scoped=True, echo=echo)
    out["A"] = dict(in_use=counter_value(a["fact"]), text=a["fact"], holders=[counter_who(a["fact"])])
    for arm in ("B", "C"):
        recs = project_facts(cli, world[arm], d["arms"][arm]["query"], echo=echo)
        names = sorted(h["name"] for h in d["hires"] if any(h["email"] in r["fact"] for r in recs))
        out[arm] = dict(in_use=before + len(recs), records=len(recs), holders=names)
    return out


def trace_counter(cli: CLI, world: dict, echo: bool = True) -> list[dict]:
    """The counter's history, oldest first: each UPDATE with the text it replaced."""
    tr = cli.run("fact", "trace", world["A_seed"][0], "--project", world["A"], scoped=True, echo=echo)
    rows = []
    for e in sorted(tr.get("trace") or [], key=lambda e: e.get("timestamp") or ""):
        if e.get("event") != "UPDATE":
            continue
        rows.append(dict(at=e.get("timestamp"), old=e.get("old_fact"), new=e.get("new_fact"),
                         source=e.get("source_kind")))
    return rows


def judge_trace(rows: list[dict], log: list[dict] | None) -> list[dict]:
    """An update is clean when the text it replaced is the text its agent read; otherwise it overwrote someone."""
    read_by = {r["text"]: r["read_text"] for r in (log or [])}
    for r in rows:
        r["by"] = counter_who(r["new"])
        r["overwrote"] = counter_who(r["old"]) if r["old"] != read_by.get(r["new"], r["old"]) else None
        r["from"], r["to"] = counter_value(r["old"]), counter_value(r["new"])
    return rows


def what_memory_says(cli: CLI, d: dict, world: dict, raced: dict) -> dict:
    truth = d["in_use_before"] + len(d["hires"])
    counts = read_counts(cli, d, world)
    rows = judge_trace(trace_counter(cli, world), raced["log"]["A"] if raced else None)
    emit("text", f"\n  Six people got a seat; {d['in_use_before']} were in use before: the truth is {truth} of {d['seats']}.")
    emit("text", f"\n  A  the counter says {counts['A']['in_use']} of {d['seats']} in use"
                 f"  — last assigned: {counts['A']['holders'][0]}")
    for arm in ("B", "C"):
        c = counts[arm]
        emit("text", f"  {arm}  {c['records']} assignment records → {d['in_use_before']} + {c['records']} = {c['in_use']} in use")
    lost = [r for r in rows if r["overwrote"]]
    emit("text", f"\n  fact trace on A's counter — {len(rows)} updates, oldest first:")
    emit("text", "    time (UTC)     update                 written by          replaced")
    for r in rows:
        tag = f"OVERWROTE {r['overwrote']}'s seat" if r["overwrote"] else "ok (replaced what it read)"
        emit("text", f"    {hhmmss(r['at']):<14} {str(r['from']):>2} → {str(r['to']):<2} of {d['seats']}"
                     f"           {r['by']:<19} {tag}")
    note(f"{len(lost)} of {len(rows)} updates replaced a text their agent never read — {len(lost)} assignment"
         f"{'s are' if len(lost) != 1 else ' is'} gone from the counter, and every command succeeded: `fact update` "
         "has no version or if-match check")
    emit("counts", "", counts=counts, truth=truth, trace=rows, lost=len(lost))
    return {"counts": counts, "trace": rows, "truth": truth}


# --------------------------------------------------------------------------- step 5: conflict detection

def watch_conflicts(cli: CLI, world: dict, since: float) -> dict:
    found: dict = {arm: [] for arm in ARMS}
    seen: dict = {}
    first = True
    while True:
        waited = time.time() - since
        for arm in ARMS:
            found[arm] = conflicts(cli, world[arm], echo=first)
        first = False
        for arm in ARMS:
            if found[arm] and arm not in seen:
                seen[arm] = int(waited)
        line = " · ".join(f"{arm} {len(found[arm])}" for arm in ARMS)
        emit("progress", f"    {int(waited):>3}s after the last write — conflicts: {line}", waited=int(waited),
             found={arm: len(found[arm]) for arm in ARMS})
        if waited >= WINDOW:
            break
        time.sleep(min(POLL, max(1, WINDOW - waited)))
    found["seen"] = seen
    return found


def conflict_step(cli: CLI, d: dict, world: dict, since: float) -> dict:
    note(f"watching all three projects for {WINDOW} s after the last write (detection usually lands in 10–110 s)")
    found = watch_conflicts(cli, world, since)
    result = {"raised": {arm: len(found[arm]) for arm in ARMS}, "conflicts": {}, "resolved": {},
              "first_seen_s": found["seen"]}
    for arm in ARMS:
        for c in found[arm]:
            emit("text", f"\n  {arm}: {c.get('category')} · {c.get('conflict_type')} — “{c.get('name')}”")
            emit("text", wrap(c.get("description") or "", "      “", 100) + "”")
            emit("text", f"      {len(c.get('fact_ids') or [])} facts in it")
        result["conflicts"][arm] = [dict(id=c["id"], name=c.get("name"), description=c.get("description"),
                                         category=c.get("category"), facts=len(c.get("fact_ids") or []),
                                         fact_ids=c.get("fact_ids") or []) for c in found[arm]]
    if not any(found.values()):
        note("no conflict in any of the three: nothing in A contradicts (the lost updates all say “13 of 20”), "
             "and the records in B and C can all be true at once")
    elif not found["A"]:
        note("A has none: a lost update is not a contradiction, and `fact update` does not run detection")
    # What the page's "latest-source" strategy would do: keep the newest fact, forget the rest.
    for arm in ("B", "C"):
        for c in found[arm]:
            if c.get("category") != "m2m":
                continue
            facts = {f["id"]: f for f in project_facts(cli, world[arm])}
            ids = [i for i in c.get("fact_ids") or [] if i in facts]
            if not ids:
                continue
            keep = max(ids, key=lambda i: facts[i].get("created_at") or "")
            emit("text", f"\n  {arm}: resolving it the {DETECTOR} way — keep the newest write, forget the rest:")
            r = cli.run("fact", "conflict", "resolve", c["id"], "--project", world[arm], "--strategy", "keep_fact",
                        "--keep-fact-id", keep, scoped=True, echo=True) or {}
            gone = ((r.get("resolve") or {}).get("forgotten_fact_ids") or r.get("forgotten_fact_ids") or [])
            if not gone:
                g = cli.run("fact", "conflict", "get", c["id"], "--project", world[arm], scoped=True, echo=False) or {}
                gone = (g.get("resolve") or {}).get("forgotten_fact_ids") or []
            names = sorted(h["name"] for h in d["hires"] for i in gone if i in facts and h["email"] in facts[i]["fact"])
            note(f"forgotten: {len(gone)} fact(s){' — ' + ', '.join(names) if names else ''}")
            result["resolved"].setdefault(arm, []).append(dict(conflict=c["id"], kept=keep, forgotten=len(gone),
                                                               names=names))
    emit("conflicts", "", **result)
    return result


# --------------------------------------------------------------------------- step 6: score

def score(cli: CLI, d: dict, world: dict, said: dict, conf: dict) -> dict:
    truth = said["truth"]
    final = read_counts(cli, d, world, echo=True)
    emit("text", f"\n  Seats in use after six onboarding agents — the truth is {truth} of {d['seats']}:\n")
    emit("text", "    memory                                    says   right?  conflicts  writes lost")
    rows = []
    for arm in ARMS:
        x = d["arms"][arm]
        n = final[arm]["in_use"]
        lost = (len([r for r in said["trace"] if r["overwrote"]]) if arm == "A"
                else sum(r["forgotten"] for r in conf["resolved"].get(arm, [])))
        rows.append(dict(arm=arm, title=x["title"], says=n, right=n == truth, conflicts=conf["raised"][arm], lost=lost,
                         holders=final[arm]["holders"]))
        emit("text", f"    {x['short']:<41} {n:>4}     {'✓' if n == truth else '✗'}     {conf['raised'][arm]:>5}"
                     f"      {lost:>5}")
    for r in rows:
        if r["arm"] == "A":
            msg = (f"A: {r['lost']} of {len(d['hires'])} updates overwrote another agent's, with no error and "
                   + (f"{r['conflicts']} conflict{'s' if r['conflicts'] != 1 else ''}" if r["conflicts"] else "no conflict"))
        elif r["conflicts"] and r["lost"]:
            msg = (f"{r['arm']}: every record was written, then flagged as a contradiction; resolving it "
                   f"{DETECTOR} forgot {r['lost']} real assignment{'s' if r['lost'] != 1 else ''}")
        elif r["conflicts"]:
            msg = f"{r['arm']}: flagged as a contradiction, nothing forgotten"
        else:
            msg = f"{r['arm']}: all {len(d['hires'])} records kept, no conflict raised this run"
        note(msg)
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, truth=truth, rows=rows,
                  trace=said["trace"], conflicts=conf, counts_before_resolve=said["counts"], counts_final=final)
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    emit("score", "", **report)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_parsers()
    banner(2, TOTAL, "Setup — the same seat pool, three ways to write it")
    world = setup(cli, d)
    banner(3, TOTAL, "Monday 09:00 — six onboarding agents write at the same moment")
    raced = race(cli, d, world)
    last_write = time.time()
    banner(4, TOTAL, "What memory says now — and what fact trace shows")
    said = what_memory_says(cli, d, world, raced)
    banner(5, TOTAL, "Conflict detection — what MemoryLake flags")
    conf = conflict_step(cli, d, world, last_write)
    banner(6, TOTAL, "Score — seats in use, by memory")
    rep = score(cli, d, world, said, conf)
    (OUT / "race.json").write_text(json.dumps(raced, indent=2), encoding="utf-8")
    return rep


# --------------------------------------------------------------------------- read-only subcommands

def show_trace(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli)
    world["A_seed"] = [_counter_id(cli, world)]
    raced = json.loads((OUT / "race.json").read_text(encoding="utf-8")) if (OUT / "race.json").exists() else None
    what_memory_says(cli, d, world, raced)


def _counter_id(cli: CLI, world: dict) -> str:
    facts = project_facts(cli, world["A"])
    if not facts:
        die("the counter fact is gone; run `python3 demo.py` again")
    return facts[0]["id"]


def show_conflicts(cli: CLI) -> None:
    world = load_world(cli)
    for arm in ARMS:
        cs = paged(cli, ["fact", "conflict", "list", "--project", world[arm], "--resolved", "false"], echo=True)
        done = paged(cli, ["fact", "conflict", "list", "--project", world[arm], "--resolved", "true"], echo=False)
        emit("text", f"  {arm}: {len(cs)} open, {len(done)} resolved")
        for c in cs + done:
            r = c.get("resolve") or {}
            emit("text", f"    “{c.get('name')}” · {len(c.get('fact_ids') or [])} facts"
                         + (f" · resolved {r.get('strategy')}, forgot {len(r.get('forgotten_fact_ids') or [])}" if r else ""))


def show_count(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli)
    world["A_seed"] = [_counter_id(cli, world)]
    counts = read_counts(cli, d, world)
    truth = d["in_use_before"] + len(d["hires"])
    for arm in ARMS:
        c = counts[arm]
        emit("text", f"  {d['arms'][arm]['title']}: {c['in_use']} of {d['seats']} in use"
                     f" {'✓' if c['in_use'] == truth else '✗ (truth ' + str(truth) + ')'}")
        emit("text", "      holders named today: " + (", ".join(c["holders"]) or "none"))


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    nfacts, nproj = 0, 0
    for arm in ARMS:
        p = find_project(cli, arm, echo=not quiet and arm == "A")
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
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet)
        nproj += 1
    if not quiet:
        note(f"deleted {nfacts} facts and {nproj} projects (their conflicts go with them)" if nproj else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "race"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "trace", "conflicts", "count", "cleanup"],
                    help="run = full demo (default); trace = the counter's update history; conflicts = what each "
                         "project flagged; count = seats in use, by memory; cleanup = delete everything")
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
    if args.command == "trace":
        banner(2, 2, "The counter's history — fact trace")
        show_trace(cli)
        return
    if args.command == "conflicts":
        banner(2, 2, "What conflict detection flagged")
        show_conflicts(cli)
        return
    if args.command == "count":
        banner(2, 2, "Seats in use, by memory")
        show_count(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py trace`, `python3 demo.py conflicts` or `python3 demo.py count`; "
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

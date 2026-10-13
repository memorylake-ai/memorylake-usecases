#!/usr/bin/env python3
"""
Memory telemetry for agent operations — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Bramblecrest Outfitters (fictional) runs four jobs against one shared support
memory: intake-agent imports the runbooks, scribe-agent pins what yesterday's
tickets settled, answer-agent answers customer questions, and a retention job
evicts notes whose time-to-live has passed.  On the second pass answer-agent
gets half its questions wrong, and nothing in the agents' own logs says why.

The demo measures every memory operation as it runs — latency per operation,
retrieval hit ratio, facts and documents held, evictions — evaluates three
alert rules, traces each miss to the eviction that caused it, and writes it all
as Prometheus text (out/metrics.prom).  Each agent runs in its own time window;
the team's own consumption records (`memorylake usage records`, one record per
billed platform operation) are read back for those windows, so the cost of
each agent comes from MemoryLake's side, not from the demo's guess.  Usage
records arrive in batches, often an hour or more after the work: the demo says
when a window has not landed yet, and `python3 demo.py telemetry` reads every
window you have run so far.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RUNBOOKS = DATA / "runbooks"
OUT = HERE / "out"
RUNS = OUT / "runs.jsonl"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-tel"  # memorylake-usecases / memory-telemetry
ROOT_FOLDER = f"{PREFIX}-bramblecrest-runbooks"
AGENTS = ("intake-agent", "scribe-agent", "answer-agent", "retention-job")

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


def say(msg: str = "") -> None:
    emit("text", msg)


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
    """Runs `memorylake …`, echoes the command, times it, parses the JSON reply, retries read-only calls."""

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "stats", "records", "summary"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable",
                 "http 502", "http 503", "http 504", "internal_error")
    # Status codes are matched with their "HTTP " prefix: a bare "502" also matches ids such as ws-…13502644….
    RATE_LIMITED = ("http 429", "rate limit exceeded")   # `usage records` allows about 20 calls a minute

    def __init__(self, binary: str, env: dict[str, str], show_json: bool = False):
        self.binary, self.env, self.show_json = binary, env, show_json
        self.workspace: str | None = None
        self.secret: str | None = None
        self.base_url = DEFAULT_BASE_URL
        self.timings: list[tuple[str, float]] = []   # (operation, seconds) for every timed call
        self.ops: Counter = Counter()                # operations of the agent window that is open

    def _echo(self, args: list[str]) -> None:
        shown = [a.replace(self.secret, "sk-…") for a in args] if self.secret else list(args)
        emit("cmd", shlex.join(["memorylake", *shown]))

    def raw(self, *args: str, scoped: bool = False, echo: bool = True) -> tuple[int, str, str]:
        argv = [str(a) for a in args]
        if scoped and self.workspace:
            argv += ["--workspace", self.workspace]
        if echo:
            self._echo(argv)
        proc = subprocess.run([self.binary, *argv], env=self.env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def run(self, *args: str, scoped: bool = False, echo: bool = True, op: str | None = None):
        """`op` names the call for the latency table (e.g. "search"); only the successful attempt is timed."""
        retryable = any(str(a) in self.READ_ONLY for a in args[:3]) or tuple(map(str, args[:2])) == ("fact", "update")
        attempt = 0
        while True:
            t0 = time.perf_counter()
            rc, out, err = self.raw(*args, scoped=scoped, echo=echo and attempt == 0)
            took = time.perf_counter() - t0
            if rc == 0:
                break
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 6 and e.has(*self.RATE_LIMITED):
                attempt += 1
                note(f"rate limited; waiting 20s (retry {attempt}/6)")
                time.sleep(20)
                continue
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup; retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if op:
            self.timings.append((op, took))
            self.ops[op] += 1
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            return out


def create(cli: CLI, args: list, refind, op: str | None = None, echo: bool = True):
    """A create call that is not idempotent: on a dropped connection, look for what it may have created before retrying."""
    for attempt in range(5):
        try:
            return cli.run(*args, echo=echo and attempt == 0, op=op)
        except CLIError as e:
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            note(f"network hiccup; checking whether it went through (retry {attempt + 1}/4)")
            time.sleep(2 * (attempt + 1))
            found = refind()
            if found:
                return found


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
    api_key = (api_key or env.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)
    cli.base_url = base_url
    if api_key:
        STATE_DIR.mkdir(exist_ok=True)
        env["MEMORYLAKE_CONFIG_DIR"] = str(STATE_DIR)
        env.pop("MEMORYLAKE_API_KEY", None)
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
        # `ws list` is newest first: its first item is whichever workspace was created last. Take the team's
        # default workspace, so `run` and `cleanup` land in the same one even after you add another.
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


# --------------------------------------------------------------------------- data + small helpers

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def short(i: str | None) -> str:
    if not i:
        return "—"
    return i[:17] + "…"


def shown_path(p: Path) -> str:
    """The path relative to where you ran the demo (so the echoed command is the one you would type)."""
    rel = os.path.relpath(p)
    return str(p) if rel.startswith("..") else rel


def hm(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%S")


def quantile(xs: list[float], q: float) -> float:
    """Nearest-rank quantile (what most dashboards show for small samples)."""
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def pages(cli: CLI, args: list[str], size: int, echo: bool = True, op: str | None = None) -> list[dict]:
    """All pages of a list command; stops only when the token is null (a full last page still returns one)."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, echo=echo and token is None, op=op) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def find_project(cli: CLI, echo: bool = True):
    try:
        return cli.run("proj", "get", cid("support"), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_folder(cli: CLI, echo: bool = True):
    return next((i for i in pages(cli, ["lib", "list", "MY_SPACE"], 50, echo=echo)
                 if i.get("type") in ("directory", "folder") and i["name"] == ROOT_FOLDER), None)


# --------------------------------------------------------------------------- agent windows

class Window:
    """One agent's turn: wall-clock start and end (Unix seconds) and the memory operations it made."""

    def __init__(self, cli: CLI, agent: str, what: str):
        self.cli, self.agent, self.what = cli, agent, what

    def __enter__(self):
        self.cli.ops = Counter()
        # Windows abut and never overlap: [start, end) in whole seconds, the next one opens where this one closed.
        last = getattr(self.cli, "last_end", 0)
        while time.time() < last:
            time.sleep(0.2)
        self.start = max(math.floor(time.time()), last)
        say(f"\n  [{self.agent}] {self.what}   window opens {hm(self.start)} UTC")
        return self

    def __exit__(self, *exc):
        self.end = math.floor(time.time()) + 1   # exclusive: covers the second the last call returned in
        self.cli.last_end = self.end
        self.ops = dict(self.cli.ops)
        if not exc[0]:
            say(f"  [{self.agent}] window closes {hm(self.end)} UTC — "
                + ", ".join(f"{n} {op}" for op, n in self.ops.items()))
        return False

    def record(self) -> dict:
        return dict(agent=self.agent, start=self.start, end=self.end, ops=self.ops)


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    folder, proj = find_folder(cli), find_project(cli)
    if folder or proj:
        # Every run is a fresh workload (that is what the telemetry measures), so leftovers go first.
        note("--reset: deleting everything the demo created first" if reset else
             "found the data of an earlier run — every run measures a fresh workload, so it is removed first")
        cleanup(cli, quiet=True)
    elif reset:
        note("--reset: nothing to delete")
    folder = create(cli, ["lib", "mkdir", ROOT_FOLDER, "--parent", "MY_SPACE", "--on-conflict", "deny"],
                    lambda: find_folder(cli, echo=False))
    spec = story["project"]
    proj = create(cli, ["proj", "create", "--name", spec["name"], "--custom-id", cid("support"),
                        "--description", spec["description"], "--workspace", cli.workspace],
                  lambda: find_project(cli, echo=False))
    ids = dict(folder=folder["item_id"], project=proj["id"])
    say(f"\n  {story['company']} — one shared memory, four jobs writing to and reading from it")
    say(f"    project  {spec['name']} → {proj['id']}")
    say(f"    Library  {ROOT_FOLDER}/   the runbooks intake-agent imports")
    for a in AGENTS:
        key = {"intake-agent": "intake", "scribe-agent": "scribe", "answer-agent": "answer", "retention-job": "janitor"}[a]
        say(f"    {a:<14} {story['agents'][key].split(' ', 1)[1]}")
    emit("setup", "", ids=ids, project=spec["name"], folder=ROOT_FOLDER)
    return ids


# --------------------------------------------------------------------------- step 3: intake-agent

def intake(cli: CLI, ids: dict, story: dict) -> dict:
    with Window(cli, "intake-agent", "imports the support runbooks") as w:
        for n, name in enumerate(story["runbooks"]):
            create(cli, ["lib", "upload", shown_path(RUNBOOKS / name), "--parent", ids["folder"], "--on-conflict", "overwrite"],
                   lambda: None, op="lib upload", echo=n == 0)   # overwrite: sending it again is safe
            if n == 0 and len(story["runbooks"]) > 1:
                note(f"… the same command for {', '.join(story['runbooks'][1:])}")
        t0 = time.time()
        res = cli.run("proj", "doc", "import", "--project", ids["project"], "--workspace", cli.workspace,
                      "--wait", ids["folder"], "--recursive", op="doc import") or {}
        stats = cli.run("proj", "stats", ids["project"], scoped=True, op="proj stats") or {}
    okay = (stats.get("document_status") or {}).get("okay", 0)
    say(f"    {res.get('success_count', 0)} imported in {int(time.time() - t0)}s · {okay} of {stats.get('document_count', 0)} documents okay")
    out = dict(window=w.record(), docs=stats.get("document_count", 0), okay=okay)
    emit("intake", "", **out)
    return out


# --------------------------------------------------------------------------- step 4: scribe-agent

def scribe(cli: CLI, ids: dict, story: dict) -> dict:
    notes = story["notes"]
    with Window(cli, "scribe-agent", f"pins {len(notes)} notes from yesterday's tickets") as w:
        res = cli.run("fact", "add", "--project", ids["project"], *[n["text"] for n in notes], scoped=True, op="fact add") or {}
        facts = res.get("facts") or []
        if len(facts) != len(notes):
            die(f"fact add returned {len(facts)} facts for {len(notes)} notes")
        for n, (f, spec) in enumerate(zip(facts, notes)):
            meta = {"kind": spec["kind"], "noted": spec["noted"], "ttl_days": spec["ttl_days"], "by": "scribe-agent"}
            cli.run("fact", "update", f["id"], "--project", ids["project"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0, op="fact update")
            if n == 0:
                note(f"… and the same for the other {len(facts) - 1} (each note's kind, date and time-to-live)")
    say("")
    say(f"    {'fact':<20} {'kind':<7} {'noted':<11} {'ttl':<5} text")
    for f, spec in zip(facts, notes):
        ttl = f"{spec['ttl_days']}d" if spec["ttl_days"] else "—"
        say(f"    {short(f['id']):<20} {spec['kind']:<7} {spec['noted']:<11} {ttl:<5} {clip(spec['text'], 58)}")
    labelled = [s for s in notes if s["ttl_days"] and s["really"] != "a one-off ticket"]
    say(f"\n    scribe-agent files everything that came in through a ticket as kind=ticket with a 7-day TTL —"
        f"\n    including {len(labelled)} notes that are really standing agreements and notices")
    rows = [dict(id=f["id"], text=s["text"], kind=s["kind"], noted=s["noted"], ttl_days=s["ttl_days"]) for f, s in zip(facts, notes)]
    out = dict(window=w.record(), facts=rows)
    emit("scribe", "", **out)
    return out


# --------------------------------------------------------------------------- steps 5/7: answer-agent

def hit_text(res: dict) -> str:
    parts = [f.get("fact") or "" for f in res.get("facts") or []]
    for d in res.get("documents") or []:
        for it in d.get("items") or []:
            for c in (it.get("highlight") or {}).get("chunks") or []:
                parts.append(c.get("text") or "")
    return "\n".join(parts)


def ask(cli: CLI, ids: dict, q: dict, echo: bool = True) -> dict:
    t0 = time.perf_counter()
    res = cli.run("search", q["ask"], "--projects", ids["project"], "--top-k", "5", scoped=True, echo=echo, op="search") or {}
    took = time.perf_counter() - t0
    blob = hit_text(res)
    hit = q["marker"].lower() in blob.lower()
    src = None
    if hit:
        f = next((f for f in res.get("facts") or [] if q["marker"].lower() in (f.get("fact") or "").lower()), None)
        d = next((d for d in res.get("documents") or [] if q["marker"].lower() in hit_text({"documents": [d]}).lower()), None)
        src = f"fact {short(f['id'])}" if f else (f"doc {d.get('document_name')}" if d else None)
    return dict(question=q["ask"], marker=q["marker"], hit=hit, source=src, seconds=round(took, 2),
                n_facts=len(res.get("facts") or []), n_docs=len(res.get("documents") or []))


def answer_pass(cli: CLI, ids: dict, story: dict, label: str) -> dict:
    qs = story["questions"]
    with Window(cli, "answer-agent", f"answers {len(qs)} customer questions ({label})") as w:
        rows = []
        for n, q in enumerate(qs):
            r = ask(cli, ids, q, echo=n == 0)
            if n == 0 and len(qs) > 1:
                note(f"… the same search for the other {len(qs) - 1} questions (echo off)")
            rows.append(r)
    say("")
    for r in rows:
        mark = "✓" if r["hit"] else "✗"
        where = r["source"] or f"“{r['marker']}” not in the top 5"
        say(f"    {mark} {r['seconds']:>5.2f}s  {clip(r['question'], 64):<64} {where}")
    hits = sum(r["hit"] for r in rows)
    ratio = hits / len(rows)
    say(f"\n    hit ratio {hits}/{len(rows)} = {ratio:.2f}")
    out = dict(window=w.record(), label=label, rows=rows, hits=hits, total=len(rows), ratio=ratio)
    emit("answers", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: retention job

def expired(meta: dict, today: date) -> bool:
    ttl, noted = (meta or {}).get("ttl_days"), (meta or {}).get("noted")
    if not ttl or not noted:
        return False
    return date.fromisoformat(noted) + timedelta(days=int(ttl)) < today


def retention(cli: CLI, ids: dict) -> dict:
    today = datetime.now(timezone.utc).date()
    with Window(cli, "retention-job", "evicts notes whose time-to-live has passed") as w:
        facts = pages(cli, ["fact", "list", "--projects", ids["project"], "--workspace", cli.workspace], 200, op="fact list")
        gone = [f for f in facts if expired(f.get("metadata") or {}, today)]
        for n, f in enumerate(gone):
            cli.run("fact", "delete", f["id"], "--project", ids["project"], scoped=True, echo=n == 0, op="fact delete")
            if n == 0 and len(gone) > 1:
                note(f"… the same for the other {len(gone) - 1} (one id per call)")
        left = pages(cli, ["fact", "list", "--projects", ids["project"], "--workspace", cli.workspace], 200, op="fact list")
    say(f"\n    rule: delete when noted + ttl_days < {today.isoformat()}")
    for f in gone:
        m = f.get("metadata") or {}
        say(f"    evicted {short(f['id']):<20} {m.get('kind'):<7} noted {m.get('noted')} ttl {m.get('ttl_days')}d  {clip(f['fact'], 50)}")
    say(f"\n    facts in the project: {len(facts)} → {len(left)}  ({len(gone)} evicted)")
    out = dict(window=w.record(), before=len(facts), after=len(left),
               evicted=[dict(id=f["id"], text=f["fact"], metadata=f.get("metadata") or {}) for f in gone])
    emit("retention", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: telemetry

def latency_table(timings: list[tuple[str, float]]) -> list[dict]:
    by = defaultdict(list)
    for op, s in timings:
        by[op].append(s)
    return [dict(op=op, n=len(xs), p50=round(quantile(xs, .5), 2), p95=round(quantile(xs, .95), 2),
                 max=round(max(xs), 2), sum=round(sum(xs), 3)) for op, xs in sorted(by.items())]


def evaluate_alerts(story: dict, a1: dict, a2: dict, ret: dict, lat: list[dict]) -> list[dict]:
    rules = story["alerts"]
    drop = (ret["before"] - ret["after"]) / ret["before"] if ret["before"] else 0.0
    p95 = next((r["p95"] for r in lat if r["op"] == "search"), 0.0)
    return [
        dict(alert="MemoryHitRatioLow", value=round(a2["ratio"], 2), threshold=f"< {rules['hit_ratio_min']}",
             state="FIRING" if a2["ratio"] < rules["hit_ratio_min"] else "ok",
             summary=f"answer-agent hit ratio {a1['ratio']:.2f} → {a2['ratio']:.2f}"),
        dict(alert="MemoryFactsDropped", value=round(drop, 2), threshold=f"> {rules['fact_drop_max']}",
             state="FIRING" if drop > rules["fact_drop_max"] else "ok",
             summary=f"facts {ret['before']} → {ret['after']} in one retention window"),
        dict(alert="SearchLatencyHigh", value=p95, threshold=f"> {rules['search_p95_max_s']}s",
             state="FIRING" if p95 > rules["search_p95_max_s"] else "ok",
             summary=f"search p95 {p95:.2f}s at the client (includes CLI start-up)"),
    ]


def trace_misses(a1: dict, a2: dict, ret: dict) -> list[dict]:
    """Every question that went from hit to miss, and the evicted fact that held its answer."""
    rows = []
    for r1, r2 in zip(a1["rows"], a2["rows"]):
        if r1["hit"] and not r2["hit"]:
            cause = next((e for e in ret["evicted"] if r2["marker"].lower() in e["text"].lower()), None)
            rows.append(dict(question=r2["question"], marker=r2["marker"], cause=cause))
    return rows


def prom_escape(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def write_prometheus(run_id: str, lat: list[dict], a1: dict, a2: dict, ret: dict, intake_out: dict,
                     alerts: list[dict], usage: dict | None) -> Path:
    L = []
    def metric(name: str, kind: str, help_: str, samples: list[tuple[dict, float]]) -> None:
        L.append(f"# HELP {name} {help_}")
        L.append(f"# TYPE {name} {kind}")
        for labels, v in samples:
            lab = ",".join(f'{k}="{prom_escape(x)}"' for k, x in {"run": run_id, **labels}.items())
            L.append(f"{name}{{{lab}}} {v:g}")
    q = []
    for r in lat:
        q += [({"op": r["op"], "quantile": "0.5"}, r["p50"]), ({"op": r["op"], "quantile": "0.95"}, r["p95"])]
    metric("memorylake_demo_op_latency_seconds", "summary",
           "Wall-clock seconds per memory operation, measured at the client (CLI start-up included).", q)
    for r in lat:   # a summary family also carries _sum and _count, under the same HELP / TYPE
        lab = f'run="{prom_escape(run_id)}",op="{prom_escape(r["op"])}"'
        L.append(f"memorylake_demo_op_latency_seconds_sum{{{lab}}} {r['sum']:g}")
        L.append(f"memorylake_demo_op_latency_seconds_count{{{lab}}} {r['n']:g}")
    metric("memorylake_demo_retrieval_hit_ratio", "gauge", "Questions whose expected answer was in the top 5.",
           [({"agent": "answer-agent", "pass": a1["label"]}, a1["ratio"]), ({"agent": "answer-agent", "pass": a2["label"]}, a2["ratio"])])
    metric("memorylake_demo_project_facts", "gauge", "Facts held by the support project.",
           [({"when": "before retention"}, ret["before"]), ({"when": "after retention"}, ret["after"])])
    metric("memorylake_demo_project_documents", "gauge", "Documents in the support project (proj stats).",
           [({}, intake_out["docs"])])
    metric("memorylake_demo_evictions_total", "counter", "Facts deleted by the retention job.",
           [({"reason": "ttl"}, len(ret["evicted"]))])
    metric("memorylake_demo_alert_firing", "gauge", "1 when the alert rule fires.",
           [({"alert": a["alert"]}, 1 if a["state"] == "FIRING" else 0) for a in alerts])
    if usage:
        rec, tok = [], []
        for w in usage["windows"]:
            for m, s in w["by_model"].items():
                rec.append(({"agent": w["agent"], "model": m}, s["records"]))
                tok.append(({"agent": w["agent"], "model": m}, s["tokens"]))
        metric("memorylake_usage_records", "gauge", "Usage records MemoryLake billed in the agent's window (usage records).", rec)
        metric("memorylake_usage_tokens", "gauge", "Tokens MemoryLake billed in the agent's window (usage records).", tok)
    OUT.mkdir(exist_ok=True)
    path = OUT / "metrics.prom"
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    return path


def post_webhook(alerts: list[dict]) -> str | None:
    """Optional: POST firing alerts as JSON to ALERT_WEBHOOK_URL (any receiver that accepts JSON)."""
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    firing = [a for a in alerts if a["state"] == "FIRING"]
    if not url or not firing:
        return None
    body = json.dumps({"source": "memorylake-usecases/memory-telemetry", "alerts": firing}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return f"POST {len(firing)} firing alert(s) → HTTP {resp.status}"
    except Exception as e:  # the demo goes on; the alert file is still written
        return f"POST to ALERT_WEBHOOK_URL failed: {e}"


# --------------------------------------------------------------------------- server-side: usage records

def latest_record(cli: CLI, echo: bool = True) -> int | None:
    page = cli.run("usage", "records", "--page-size", "1", echo=echo) or {}
    items = page.get("items") or []
    return int(items[0]["created_at"]) if items else None


def usage_window(cli: CLI, w: dict, echo: bool = True) -> dict:
    recs = pages(cli, ["usage", "records", "--start-time", str(w["start"]), "--end-time", str(w["end"])], 100, echo=echo)
    by = {}
    for r in recs:
        s = by.setdefault(r.get("model") or "?", {"records": 0, "tokens": 0})
        s["records"] += 1
        s["tokens"] += int(r.get("used_tokens") or 0)
    members = sorted({(r.get("member") or {}).get("display_name") or "?" for r in recs})
    return dict(agent=w["agent"], start=w["start"], end=w["end"], ops=w.get("ops") or {},
                records=len(recs), by_model=by, members=members)


def read_usage(cli: CLI, windows: list[dict], echo: bool = True, newest: int | None = None) -> dict:
    """Usage records for each window, and whether the record stream has caught up with it."""
    if newest is None:
        newest = latest_record(cli, echo=echo)
    out = []
    for n, w in enumerate(windows):
        landed = newest is not None and newest >= w["end"]
        u = usage_window(cli, w, echo=echo and n == 0) if (landed or newest is None or newest >= w["start"]) else \
            dict(agent=w["agent"], start=w["start"], end=w["end"], ops=w.get("ops") or {}, records=0, by_model={}, members=[])
        # A stall in the record stream is flushed later, stamped with the flush time rather than the time of the work
        # (2026-10-13: nothing from 03:56 to 06:13 UTC, then 791 records stamped 06:13:47–06:14:04). So a job that did
        # work but has no record in its window is not "no billed operations": its records may sit in a later burst.
        u["unattributed"] = landed and not u["records"] and bool(w.get("ops"))
        u["landed"] = landed and not u["unattributed"]
        out.append(u)
    return dict(newest=newest, windows=out)


def show_usage(u: dict, label: str) -> None:
    now = time.time()
    newest = u["newest"]
    if newest:
        say(f"\n  usage records — {label}   (newest record in the account: {hm(newest)} UTC, {int((now - newest) // 60)} min ago)")
    else:
        say(f"\n  usage records — {label}   (the account has no usage records yet)")
    say(f"    {'agent':<21} {'window (UTC)':<17} {'what the agent did':<26} {'records':>7}  by operation (records · tokens)")
    for w in u["windows"]:
        did = ", ".join(f"{n} {op}" for op, n in w["ops"].items()) or "—"
        if w["landed"]:
            by = " · ".join(f"{m} {s['records']}·{s['tokens']}" for m, s in sorted(w["by_model"].items())) or "no billed operations"
            say(f"    {w['agent']:<21} {hm(w['start'])}–{hm(w['end'])} {clip(did, 26):<26} {w['records']:>7}  {by}")
        elif w.get("unattributed"):
            say(f"    {w['agent']:<21} {hm(w['start'])}–{hm(w['end'])} {clip(did, 26):<26} {0:>7}  none stamped in this window "
                f"(records after a stall carry the flush time)")
        else:
            say(f"    {w['agent']:<21} {hm(w['start'])}–{hm(w['end'])} {clip(did, 26):<26} {'—':>7}  not landed yet")


def telemetry(cli: CLI, story: dict, run_id: str, intake_out: dict, scribe_out: dict, a1: dict,
              ret: dict, a2: dict) -> dict:
    lat = latency_table(cli.timings)
    say("\n  latency at the client (each CLI call, start-up included)")
    say(f"    {'operation':<13} {'calls':>5} {'p50':>7} {'p95':>7} {'max':>7}")
    for r in lat:
        say(f"    {r['op']:<13} {r['n']:>5} {r['p50']:>6.2f}s {r['p95']:>6.2f}s {r['max']:>6.2f}s")
    say("\n  memory health")
    say(f"    retrieval hit ratio   {a1['ratio']:.2f} → {a2['ratio']:.2f}   (answer-agent, {a1['total']} questions, top 5)")
    say(f"    facts held            {ret['before']} → {ret['after']}")
    say(f"    evictions             {len(ret['evicted'])}   (retention-job, ttl)")
    say(f"    documents             {intake_out['docs']}   ({intake_out['okay']} okay)")
    alerts = evaluate_alerts(story, a1, a2, ret, lat)
    say("\n  alert rules")
    for a in alerts:
        say(f"    {'🔴 FIRING' if a['state'] == 'FIRING' else '🟢 ok    '}  {a['alert']:<20} {a['value']:>6} (rule {a['threshold']})  {a['summary']}")
    misses = trace_misses(a1, a2, ret)
    say("\n  why answer-agent got worse — each new miss, matched to what the retention job evicted")
    for m in misses:
        c = m["cause"]
        if c:
            md = c["metadata"]
            say(f"    ✗ {clip(m['question'], 60)}")
            say(f"        ← evicted {short(c['id'])} (kind={md.get('kind')}, ttl {md.get('ttl_days')}d): “{clip(c['text'], 80)}”")
        else:
            say(f"    ✗ {clip(m['question'], 60)}   (no evicted fact held “{m['marker']}”)")
    traced = sum(1 for m in misses if m["cause"])
    say(f"\n    {traced} of {len(misses)} new misses trace to an eviction; "
        f"{len(ret['evicted']) - traced} of {len(ret['evicted'])} evictions cost no answer")
    windows = [intake_out["window"], scribe_out["window"], a1["window"], ret["window"], a2["window"]]
    for w, tag in zip(windows, ("", "", " (pass 1)", "", " (pass 2)")):
        w["agent_label"] = w["agent"] + tag
    usage = read_usage(cli, windows)
    for w, src in zip(usage["windows"], windows):
        w["agent"] = src["agent_label"]
    show_usage(usage, "this run")
    landed = all(w["landed"] for w in usage["windows"])
    if not landed:
        say("\n    MemoryLake writes usage records in batches, often an hour or more after the work."
            "\n    The windows are saved in out/runs.jsonl; `python3 demo.py telemetry` reads them again later.")
    path = write_prometheus(run_id, lat, a1, a2, ret, intake_out, alerts, usage if landed else None)
    (OUT / "alerts.json").write_text(json.dumps(alerts, indent=2) + "\n", encoding="utf-8")
    say(f"\n  wrote {shown_path(path)} ({sum(1 for l in path.read_text().splitlines() if not l.startswith('#'))} samples)"
        f" and {shown_path(OUT / 'alerts.json')}")
    hooked = post_webhook(alerts)
    if hooked:
        note(hooked)
    out = dict(latency=lat, alerts=alerts, misses=misses, usage=usage, landed=landed,
               health=dict(hit1=a1["ratio"], hit2=a2["ratio"], facts_before=ret["before"], facts_after=ret["after"],
                           evictions=len(ret["evicted"]), docs=intake_out["docs"]))
    emit("telemetry", "", **out)
    return out


# --------------------------------------------------------------------------- `telemetry`: every saved run

def saved_runs() -> list[dict]:
    if not RUNS.exists():
        return []
    rows = []
    for line in RUNS.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("windows"):
            rows.append(r)
    return rows


def telemetry_all(cli: CLI, last: int = 5) -> dict:
    """Read usage records for the windows of the last few runs, and the team's usage summary."""
    runs = saved_runs()[-last:]
    if not runs:
        die("no saved runs yet; run `python3 demo.py` first (its agent windows go to out/runs.jsonl)")
    summary = cli.run("usage", "summary") or {}
    today = summary.get("today") or {}
    say(f"\n  team usage today ({summary.get('timezone', '?')}): {today.get('requests', 0)} billed requests, "
        f"{today.get('used_tokens', 0)} tokens")
    result = []
    newest = latest_record(cli)   # once: `usage records` allows about 20 calls a minute
    for n, r in enumerate(runs):
        u = read_usage(cli, r["windows"], echo=n == 0, newest=newest)
        show_usage(u, f"run {r['run']} ({r['when']})")
        result.append(dict(run=r["run"], when=r["when"], usage=u))
    landed = [r for r in result if all(w["landed"] for w in r["usage"]["windows"])]
    if landed:
        say("\n  per agent, all landed runs together")
        agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
        for r in landed:
            for w in r["usage"]["windows"]:
                for m, s in w["by_model"].items():
                    agg[w["agent"]][m][0] += s["records"]
                    agg[w["agent"]][m][1] += s["tokens"]
        for agent, models in agg.items():
            say(f"    {agent:<21} " + " · ".join(f"{m} {c} records / {t} tokens" for m, (c, t) in sorted(models.items())))
    else:
        say("\n    none of these runs has landed in its own windows yet — usage records arrive in batches, and a batch "
            "that arrives after a stall is stamped with its arrival time; try again later")
    out = dict(runs=result, summary=today, newest=newest, landed=len(landed))
    emit("usage", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    proj = find_project(cli, echo=not quiet)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)   # the project, its documents and its facts
    folder = find_folder(cli, echo=not quiet)
    if folder:
        cli.run("lib", "delete", folder["item_id"])   # the runbook folder and every file in it
    note("deleted the support project (documents and facts with it) and the runbook folder — "
         "out/runs.jsonl stays, so `telemetry` can still read the windows")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    banner(2, TOTAL, "Set up — one shared support memory, four jobs that use it")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    cli.timings = []
    banner(3, TOTAL, "intake-agent — the runbooks go into memory")
    i = intake(cli, ids, story)
    banner(4, TOTAL, "scribe-agent — notes from yesterday's tickets, each with a time-to-live")
    s = scribe(cli, ids, story)
    banner(5, TOTAL, "answer-agent — six customer questions, pass 1")
    a1 = answer_pass(cli, ids, story, "pass 1")
    banner(6, TOTAL, "retention-job — evict what has outlived its time-to-live")
    r = retention(cli, ids)
    banner(7, TOTAL, "answer-agent — the same six questions, pass 2")
    a2 = answer_pass(cli, ids, story, "pass 2")
    banner(8, TOTAL, "Telemetry — latency, hit ratio, growth, evictions, alerts, and MemoryLake's own usage records")
    t = telemetry(cli, story, run_id, i, s, a1, r, a2)
    summary = dict(run=run_id, when=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   seconds=int(time.time() - t0), docs=i["docs"], facts=len(s["facts"]),
                   hit1=f"{a1['hits']}/{a1['total']}", hit2=f"{a2['hits']}/{a2['total']}",
                   facts_before=r["before"], facts_after=r["after"], evicted=len(r["evicted"]),
                   misses_traced=sum(1 for m in t["misses"] if m["cause"]), misses=len(t["misses"]),
                   firing=[a["alert"] for a in t["alerts"] if a["state"] == "FIRING"],
                   latency={x["op"]: [x["p50"], x["p95"]] for x in t["latency"]},
                   usage_landed=t["landed"],
                   windows=[{k: w[k] for k in ("agent", "start", "end", "ops")} for w in t["usage"]["windows"]])
    OUT.mkdir(exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    folder, proj = find_folder(cli, echo=False), find_project(cli, echo=False)
    if not (folder and proj):
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(folder=folder["item_id"], project=proj["id"])


def ask_free(cli: CLI, ids: dict, text: str, echo: bool = True) -> dict:
    """Any question: the top 5 hits, how long the search took, and the latency of every ask in this session."""
    t0 = time.perf_counter()
    res = cli.run("search", text, "--projects", ids["project"], "--top-k", "5", scoped=True, echo=echo, op="search") or {}
    took = time.perf_counter() - t0
    rows = []
    for f in res.get("facts") or []:
        rows.append(dict(rank=f.get("rank"), kind="fact", id=f.get("id"), text=clip(f.get("fact") or "", 140)))
    for d in res.get("documents") or []:
        rows.append(dict(rank=d.get("rank"), kind="document", id=d.get("document_name"),
                         text=clip(hit_text({"documents": [d]}).replace("#", ""), 140)))
    rows.sort(key=lambda r: r["rank"] or 99)
    say(f"\n  Q: {text}   ({took:.2f}s)")
    for r in rows:
        say(f"    #{r['rank']} {r['kind']:<8} {r['id'] if r['kind'] == 'document' else short(r['id'])}  “{r['text']}”")
    if not rows:
        say("    (no hits)")
    out = dict(question=text, seconds=round(took, 2), rows=rows)
    emit("ask", "", **out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "telemetry", "metrics", "ask", "cleanup"],
                    help="run = full demo (default); telemetry = read MemoryLake's usage records for the windows of "
                         "your last runs (reads only); metrics = print out/metrics.prom; ask \"TEXT\" = search the "
                         "support memory; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="question for `ask`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "metrics":
        p = OUT / "metrics.prom"
        if not p.exists():
            die("no out/metrics.prom yet; run `python3 demo.py` first")
        print(p.read_text(encoding="utf-8"), end="")
        return
    if args.command == "ask" and not args.arg:
        ap.error("ask needs a question, e.g. python3 demo.py ask \"Do gift cards expire?\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py telemetry` (now and again in an hour), `python3 demo.py metrics`, "
              "`python3 demo.py ask \"Do gift cards expire?\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    if args.command == "telemetry":
        banner(2, 2, "Usage records — what MemoryLake billed in each agent's window")
        telemetry_all(cli)
        return
    ids = need_ids(cli)
    banner(2, 2, "Ask the support memory")
    ask_free(cli, ids, args.arg)


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

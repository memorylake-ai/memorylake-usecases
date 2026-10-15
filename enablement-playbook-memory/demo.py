#!/usr/bin/env python3
"""
Sales playbook memory for enablement teams — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Fernhollow Software (fictional) keeps its sales playbook in one MemoryLake
project: every AE's quarterly scorecard from the CRM (12 reps, 2025 Q1 to 2026
Q3, one fact each), the enablement programs that ran, and the plays written
from them -- the reflection memory every rep's AI tool reads before it plans.

The quick read is what those tools do with it: `search` the question, take the
play on that program, act on it. Play 7 says weekly call coaching lifted the
five reps it was given to from 14% to 25% (+11 points): coach the five lowest
every quarter. Play 9 says carrying a new-hire shadower dropped five closers
from 31% to 20%: keep shadowers off the top five. Play 8 says the objection
workshop lifted the West pod from 21% to 28% while East held: roll it out.

The numbers are right. Two of them are regression to the mean: the coached
five were the five lowest win rates of the quarter before, and the five lowest
of any quarter rise the next one with no coaching at all -- low quarters are
partly bad luck. The memory read goes back to the scorecards (`fact list`,
every page), works out from the numbers how each program's reps were picked,
and replays that same rule in the quarters when no program ran. The five
lowest rose +8 to +12 every time; the coached five rose +11 -> not shown. The
five highest fell -7 to -12 every time; the shadowed five fell -11 -> not
shown. The West pod, picked by territory rather than by results, moved -2 to 0
in earlier quarters and +7 with the workshop -> credit it (the control).

Step 6 rewrites the two plays from the scorecards (`fact update --text`, the
old text kept in metadata.previous, the change visible in `fact trace`), and
the same quick-read code then agrees with the scorecards on later questions.

Everything here is a plain CLI command, echoed as it runs.  The companion web
app (web/server.py) drives the same functions and streams the same events.

Requirements: Python 3.9+, the memorylake CLI on PATH, and a MemoryLake API key.
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

PREFIX = "mlu-epm"  # memorylake-usecases / enablement-playbook-memory

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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search", "trace"}
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
        if shown[:2] == ["fact", "add"] and len(shown) > 8:
            # One `fact add` carries up to 30 entries: echo the first two and a count (display only).
            texts = [a for a in shown[4:] if not a.startswith("--")][:-1] if "--workspace" in shown else shown[4:]
            if len(texts) > 2:
                shown = shown[:6] + [f"… {len(texts) - 2} more entries"] + shown[4 + len(texts):]
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
        # Read-only calls retry on a dropped connection, and so does `fact update` (it sets the same text/metadata again).
        retryable = any(str(a) in self.READ_ONLY for a in args[:3]) or tuple(map(str, args[:2])) == ("fact", "update")
        attempt = 0
        while True:
            rc, out, err = self.raw(*args, scoped=scoped, echo=echo and attempt == 0)
            if rc == 0:
                break
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup; retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            return out


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


# --------------------------------------------------------------------------- data

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def pct(won: int, opps: int) -> int:
    # Half up, like the web page's Math.round (Python's round() is banker's rounding: round(62.5) == 62).
    return int(100 * won / opps + 0.5) if opps else 0


def qlabel(q: str) -> str:
    return f"{q[:4]} {q[4:]}"


def signed(x) -> str:
    if isinstance(x, float):
        return f"{x:+.1f}"
    return f"{x:+d}"


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null. Empty lists come back as [].
    m = dict(key=f"epm:{r['id']}", record=r["id"], kind=r["kind"], by=r["by"], on=r["on"])
    if r["kind"] == "scorecard":
        m.update(rep=r["rep"], pod=r["pod"], quarter=r["quarter"], won=r["won"], opps=r["opps"])
    elif r["kind"] == "program":
        m.update(program=r["program"], quarter=r["quarter"], members=r["members"])
    elif r["kind"] == "play":
        m.update(program=r["program"], title=r["title"], direction=r["direction"], claim=r["claim"],
                 advice=r["advice"], stance="act on it")
    return m


# --------------------------------------------------------------------------- reading memory

def pages(cli: CLI, args: list[str], size: int = 50, echo: bool = True) -> list[dict]:
    """All pages of a list command: loop until the continuation token is null."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, pid: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (SC-2025Q1-AR, P-COACH, PLAY-7 …), metadata flattened in, plus fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            out[m["record"]] = dict(m, fid=f["id"], body=f.get("fact") or "")
    return out


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "kind", "by", "on", "rep", "pod", "quarter", "won", "opps", "program",
                                  "members", "title", "direction", "claim", "advice", "stance", "fid", "body", "rank",
                                  "previous", "previous_stance")}


def kind(mem: dict, k: str) -> list[dict]:
    return sorted((r for r in mem.values() if r.get("kind") == k), key=lambda r: (r["on"], r["record"]))


# --------------------------------------------------------------------------- the scorecards (plain code, no model)

def scores(mem: dict) -> dict[tuple[str, str], tuple[int, int]]:
    return {(r["rep"], r["quarter"]): (int(r["won"]), int(r["opps"])) for r in kind(mem, "scorecard")}


def group_rate(sc: dict, reps: list[str], q: str) -> int:
    won = sum(sc[(r, q)][0] for r in reps if (r, q) in sc)
    opps = sum(sc[(r, q)][1] for r in reps if (r, q) in sc)
    return pct(won, opps)


def ranked(sc: dict, story: dict, q: str) -> list[str]:
    """Reps by win rate in quarter q, lowest first."""
    return sorted(story["reps"], key=lambda r: sc[(r, q)][0] / sc[(r, q)][1])


def infer_rule(sc: dict, story: dict, members: list[str], before: str) -> dict:
    """How were this program's reps picked? Worked out from the numbers, not from a label: are they exactly the k
    lowest (or highest) win rates of the quarter before, or a pod (a group fixed by territory)?"""
    k, ms = len(members), set(members)
    rate = lambda r: sc[(r, before)][0] / sc[(r, before)][1]
    inside = [rate(r) for r in members]
    outside = [rate(r) for r in story["reps"] if r not in ms]
    if outside and max(inside) < min(outside):
        return dict(type="lowest", k=k, label=f"the {WORD[k]} lowest win rates of the quarter before",
                    short=f"{WORD[k]} lowest")
    if outside and min(inside) > max(outside):
        return dict(type="highest", k=k, label=f"the {WORD[k]} highest win rates of the quarter before",
                    short=f"{WORD[k]} highest")
    for pod, reps in story["pods"].items():
        if set(reps) == ms:
            return dict(type="pod", pod=pod, label=f"the {pod} pod (fixed by territory, not by results)",
                        short=f"{pod} pod")
    return dict(type="named", members=members, label="a named group", short="same reps")


WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}


def apply_rule(rule: dict, sc: dict, story: dict, q: str) -> list[str]:
    if rule["type"] == "lowest":
        return ranked(sc, story, q)[: rule["k"]]
    if rule["type"] == "highest":
        return ranked(sc, story, q)[-rule["k"]:][::-1]
    if rule["type"] == "pod":
        return list(story["pods"][rule["pod"]])
    return list(rule["members"])


def replay(story: dict, mem: dict, play: dict) -> dict:
    """The play's evidence is one before/after on one group. Replay the same selection rule in every earlier pair of
    quarters: same rule, same before/after, no program. Those changes are what the group does on its own."""
    sc = scores(mem)
    prog = mem[play["program"]]
    qs = story["quarters"]
    pq = prog["quarter"]
    before = qs[qs.index(pq) - 1]
    rule = infer_rule(sc, story, prog["members"], before)
    ran = {}  # quarter -> {rep: program}
    for p in kind(mem, "program"):
        for r in p["members"]:
            ran.setdefault(p["quarter"], {})[r] = p["program"]
    rows = []
    for a, b in zip(qs, qs[1:]):
        if qs.index(b) > qs.index(pq):
            break
        grp = apply_rule(rule, sc, story, a)
        x, y = group_rate(sc, grp, a), group_rate(sc, grp, b)
        busy = sorted({ran[b][r] for r in grp if r in ran.get(b, {})})
        if b == pq:
            role, why = "program", f"← {prog['program']}"
        elif busy:
            role, why = "excluded", f"not a comparison: {', '.join(busy)} ran in {qlabel(b)}"
        else:
            role, why = "baseline", "no program"
        rows.append(dict(pair=f"{qlabel(a)} → {qlabel(b)[5:]}" if a[:4] == b[:4] else f"{qlabel(a)} → {qlabel(b)}",
                         a=a, b=b, group=grp, x=x, y=y, change=y - x, role=role, why=why))
    other = None
    if rule["type"] == "pod":
        rest = [r for r in story["reps"] if r not in prog["members"]]
        name = next(p for p, reps in story["pods"].items() if set(reps) == set(rest))
        x, y = group_rate(sc, rest, before), group_rate(sc, rest, pq)
        other = dict(label=f"{name} pod, same quarter, no {prog['program']}", x=x, y=y, change=y - x)
    return dict(play=play["record"], program=prog["record"], name=prog["program"], quarter=pq, before=before,
                members=prog["members"], rule=rule, rows=rows, other=other)


def verdict(rp: dict, direction: str) -> dict:
    """`credit it` when the program's group moved further, in the play's direction, than the same rule's group did in
    every quarter without a program; `not shown` when the move is inside that range."""
    base = [r["change"] for r in rp["rows"] if r["role"] == "baseline"]
    prog = next(r["change"] for r in rp["rows"] if r["role"] == "program")
    if not base:
        return dict(advice="can't tell", base=[], prog=prog, mean=0.0, beyond=0.0,
                    why="no quarter without a program to compare with")
    mean = sum(base) / len(base)
    beyond = round(prog - mean, 1)
    lo, hi = min(base), max(base)
    credit = prog > hi if direction == "up" else prog < lo
    rng = f"{signed(lo)} to {signed(hi)}" if lo != hi else signed(lo)
    if credit:
        why = (f"without a program this group moved {rng}; with it {signed(prog)}, past every one of those "
               f"({signed(beyond)} beyond their average)")
    else:
        why = (f"without a program this group moved {rng}; with it {signed(prog)}, inside that range "
               f"({signed(beyond)} beyond their average)")
    return dict(advice="act on it" if credit else "not shown", base=base, prog=prog, mean=round(mean, 1),
                beyond=beyond, lo=lo, hi=hi, why=why)


def rule_text(rp: dict) -> str:
    """The rule, with the quarter it was read from when it depends on one."""
    if rp["rule"]["type"] in ("lowest", "highest"):
        return f"{rp['rule']['label']} ({qlabel(rp['before'])})"
    return rp["rule"]["label"]


def replay_lines(story: dict, rp: dict) -> list[str]:
    lines = []
    for r in rp["rows"]:
        names = " ".join(r["group"]) if rp["rule"]["type"] != "pod" else f"{len(r['group'])} reps"
        if r["role"] == "excluded":
            lines.append(f"      {r['pair']:<17} {rp['rule']['short']:<13} {names:<15} {r['why']}")
            continue
        tag = r["why"] if r["role"] == "program" else ""
        lines.append(f"      {r['pair']:<17} {rp['rule']['short']:<13} {names:<15} {r['x']:>3}% → {r['y']:>3}%  "
                     f"{signed(r['change']):>4}  {tag}")
    if rp["other"]:
        o = rp["other"]
        lines.append(f"      · {o['label']}: {o['x']}% → {o['y']}%  {signed(o['change'])}")
    return lines


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, pid: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", kind=m.get("kind") or "?"))
    return out


def hit_line(h: dict, mark: str = "") -> str:
    return f"      #{h['rank']:<3} {h['record']:<15} {clip(h['body'], 84)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, t: dict, echo: bool = True) -> dict:
    """What every rep's AI tool does with playbook memory: search the question, take the play on that program, act
    on it. The play is read from `fact list` by fact id (search can answer from a cache of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    hit = next((h for h in hits if by_fid.get(h["fid"], h).get("record") == t["play"]), None)
    if hit is None:
        return dict(ticket=t, hits=hits, play=None, advice="", said="the play is not in the top 8", cached=cached)
    cur = by_fid.get(hit["fid"], hit)
    return dict(ticket=t, hits=hits, play=cur["record"], play_rank=hit["rank"], play_body=cur["body"],
                advice=cur.get("stance") or "", action=cur.get("advice") or "", cached=cached,
                said=f"the play says {cur.get('stance')}")


# --------------------------------------------------------------------------- read 2: the scorecards

def memory_read(cli: CLI, pid: str, play_id: str, echo: bool = True) -> dict:
    """Every scorecard (`fact list`, every page); the program's selection rule worked out from them and replayed."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    play = mem.get(play_id)
    if play is None:
        die(f"no play {play_id} in the project")
    rp = replay(story, mem, play)
    v = verdict(rp, play["direction"])
    return dict(play=play_id, replay=rp, verdict=v, advice=v["advice"], said=f"{v['advice']} — {v['why']}")


def agrees(quick: str, memory: str) -> bool:
    return bool(quick) and quick == memory


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> str:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    p = story["project"]
    proj = find_project(cli, p["key"], echo=False)
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]), "--description",
                       p["description"], scoped=True)
    else:
        note("the project exists from an earlier run (pass --reset to start over)")
    emit("text", f"\n    project  {p['name']:<40} {proj['id']}")
    emit("setup", "", project=proj["id"], name=p["name"])
    return proj["id"]


# --------------------------------------------------------------------------- step 3: intake

BATCH = 30


def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key) — `fact add` takes many texts at once
    (30 per call here), then one `fact update --metadata` per new fact, four at a time. Returns key → {id, new}."""
    live = {}
    for f in list_facts(cli, pid, echo=False):
        k = (f.get("metadata") or {}).get("key")
        if k:
            live[k] = f
    out, missing = {}, []
    for text, meta in items:
        f = live.get(meta["key"])
        if f is None:
            missing.append((text, meta))
        else:
            out[meta["key"]] = dict(id=f["id"], new=False)
    jobs = []
    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        res = cli.run("fact", "add", "--project", pid, *[t for t, _ in chunk], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for text, meta in chunk:
            if not got.get(text):
                die(f"`fact add` did not return “{clip(text, 60)}”")
            jobs.append((got[text], meta))
    if jobs:
        cli.run("fact", "update", jobs[0][0], "--project", pid, "--metadata", json.dumps(jobs[0][1]), scoped=True,
                echo=echo)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda j: cli.run("fact", "update", j[0], "--project", pid, "--metadata", json.dumps(j[1]),
                                            scoped=True, echo=False), jobs[1:]))
        for fid, meta in jobs:
            out[meta["key"]] = dict(id=fid, new=True)
        if len(jobs) > 1 and echo:
            note(f"… and {len(jobs) - 1} more `fact update --metadata` the same way (four at a time)")
    return out


# Every key the write-back adds or changes; restore takes them out and puts `stance` back.
REWRITE_KEYS = ("previous", "previous_stance", "rewritten_on", "rewritten_by", "basis")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of Q4 planning: plays an earlier run rewrote are put back word for word from
    metadata.previous, with the stance they had."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous"):
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            meta["stance"] = m.get("previous_stance") or meta.get("stance")
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} play(s); put back, so this run starts from the same morning")
    return n


def grid_lines(story: dict, sc: dict) -> list[str]:
    qs = story["quarters"]
    lines = [f"    {'rep':<16} {'pod':<5} " + " ".join(f"{q[2:4]}{q[4:]:>3}" for q in qs)]
    for pod in ("West", "East"):
        for r in story["pods"][pod]:
            lines.append(f"    {story['reps'][r]:<16} {pod:<5} " + " ".join(f"{pct(*sc[(r, q)]):>5}%"[1:] for q in qs))
    lines.append(f"    {'team':<16} {'':<5} " + " ".join(f"{group_rate(sc, list(story['reps']), q):>5}%"[1:] for q in qs))
    return lines


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("CRM scorecards, enablement programs and the playbook, one project. One fact per record, word for word, then "
         "`fact update --metadata` (rep, pod, quarter, won, opportunities; for a program: who was in it; for a play: "
         "the program, the claimed before/after and the advice)")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    sc = scores(mem)
    lines = [f"\n  {len(kind(mem, 'scorecard'))} scorecards, win rate by quarter:"] + grid_lines(story, sc)
    lines.append(f"\n  {len(kind(mem, 'program'))} programs:")
    for r in kind(mem, "program"):
        lines.append(f"    {r['record']:<11} {clip(r['body'], 150)}")
    lines.append(f"\n  {len(kind(mem, 'play'))} plays, as they stand:")
    for r in sorted(kind(mem, "play"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<7} {r['body']}")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["kind"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} questions from Q4 planning. The quick read: `search` the question, take the play "
         "on that program, act on it")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']}: “{t['text']}”", f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(h, "   ← the play" if h["record"] == q["play"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["play"]:
            lines.append(f"  → {q['play']}: “{clip(q['play_body'], 190)}”")
            lines.append(f"  → quick read: {q['advice']} ({q['action']})")
        else:
            lines.append("  → quick read: ? (the play is not in the top 8)")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], play=q["play"], play_body=q.get("play_body", ""),
                                  advice=q["advice"], action=q.get("action", ""), said=q["said"],
                                  hits=[public(h) for h in q["hits"]], cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, mem: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every scorecard (`fact list`, every page). For each play, work out from the numbers how "
         "its program's reps were picked, then replay that same rule in the quarters when no program ran — that is "
         "what the group does on its own")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        mr = memory_read(cli, pid, t["play"], echo=i == 0)
        rp, v = mr["replay"], mr["verdict"]
        p = mem[t["play"]]
        c = p.get("claim") or [0, 0]
        verb = "credits" if p["direction"] == "up" else "blames"
        lines = [f"\n  {t['id']} · {t['play']} ({p['on']}) {verb} {p.get('title') or rp['name']} for {c[0]}% → {c[1]}% "
                 f"({signed(c[1] - c[0])} points) in {qlabel(rp['quarter'])}",
                 f"    who was in it: {' '.join(rp['members'])} = {rule_text(rp)}, worked out from the scorecards"]
        lines += replay_lines(story, rp)
        lines.append(f"  · {v['advice']} — {v['why']}")
        lines.append(f"  → quick read:  {q['advice'] or '?'}")
        lines.append(f"  → memory read: {mr['advice']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, play=q["play"], quick=q["advice"], memory=mr, agree=agrees(q["advice"], mr["advice"])))
    lines = [f"\n  {'q':<3} {'play':<8} {'program':<10} {'picked by':<14} {'the play says':<14} {'the scorecards say':<19}",
             "  " + "-" * 82]
    for r in rows:
        rp = r["memory"]["replay"]
        lines.append(f"  {r['ticket']['id']:<3} {r['ticket']['play']:<8} {rp['name']:<10} {rp['rule']['short']:<14} "
                     f"{r['quick'] or '?':<14} {r['memory']['advice']:<19} {'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)}: groups picked for an extreme quarter drift back on their own; the "
                 "plays credited (or blamed) the program for it" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], play=r["play"], quick=r["quick"], agree=r["agree"],
                                  memory={k: r["memory"][k] for k in ("play", "advice", "said", "verdict", "replay")})
                             for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def rewritten_play(p: dict, mr: dict, story: dict) -> str:
    """The play's conclusion and the replay it rests on."""
    rp, v = mr["replay"], mr["verdict"]
    num = p["record"].split("-")[-1]
    head = (f"Play {num}, {p.get('title') or rp['name']} (rewritten by {story['lead']['name']} from the scorecards, {story['today']}): ")
    c = p["claim"]
    prog = next(r for r in rp["rows"] if r["role"] == "program")
    rng = f"{signed(v['lo'])} to {signed(v['hi'])}"
    if v["advice"] == "not shown":
        ext = "low" if rp["rule"]["type"] == "lowest" else "high"
        drift = "better" if ext == "low" else "lower"
        say = (f"not shown to change win rates. Its reps were {rp['rule']['label']}, and went {c[0]}% to {c[1]}% "
               f"({signed(prog['change'])} points); the same pick in each quarter without any program moved {rng}. "
               f"A {ext} quarter is followed by a {drift} one anyway. To test it, pick half of the candidates at random "
               f"and compare the two halves.")
    else:
        say = (f"{signed(prog['change'])} points with it; the same group moved {rng} in quarters without it.")
    return head + say


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next AI tool will read it: each play the scorecards disagree with is rewritten from "
         "the replay (`fact update --text`; the old text, the old stance and the replay go to metadata)")
    updates = []
    for r in rows:
        if r["agree"] or not r["play"]:
            continue
        m = mem[r["play"]]
        text = rewritten_play(m, r["memory"], story)
        if text == m["body"]:
            continue
        v = r["memory"]["verdict"]
        meta = {k: m[k] for k in ("key", "record", "kind", "by", "on", "program", "title", "direction", "claim", "advice")}
        meta.update(stance=v["advice"], previous=m["body"], previous_stance=m["stance"], rewritten_on=today,
                    rewritten_by=story["lead"]["name"], basis=dict(without=v["base"], with_program=v["prog"],
                                                                  beyond=v["beyond"],
                                                                  rule=r["memory"]["replay"]["rule"]["label"]))
        updates.append((m, text, meta))
    for i, (m, text, meta) in enumerate(updates):
        cli.run("fact", "update", m["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more play(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for m, _, _ in updates:
        lines.append(f"\n    {m['record']} was “{clip(m['body'], 190)}”")
        lines.append(f"    {'':<{len(m['record'])}} now “{mem2[m['record']]['body']}”")
    emit("text", "\n".join(lines))
    trace = []
    if updates:
        m = updates[0][0]
        res = cli.run("fact", "trace", m["fid"], "--project", pid, scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {m['record']} (newest first):"]
        for e in trace:
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(e['new'], 90)}")
        emit("text", "\n".join(tl))
    # Later the same day, new wording (search answers a repeated question from its cache).
    note("later the same day, the same three topics in other words — the quick read again, unchanged code. Search "
         "gets a rewritten fact a few seconds after `fact list` does; each read is retried until its play is the "
         "current text")
    time.sleep(6)
    after = []
    for t in story["followups"]:
        t1, tries = time.time(), 0
        while True:
            tries += 1
            q = quick_read(cli, pid, mem2, t, echo=tries == 1)
            if not q["cached"] or tries >= 8:
                break
            time.sleep(4)
        mr = memory_read(cli, pid, t["play"], echo=False)
        after.append(dict(ticket=t, play=q["play"], play_rank=q.get("play_rank"), quick=q["advice"],
                          memory=mr["advice"], agree=agrees(q["advice"], mr["advice"]),
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']}: “{a['ticket']['text']}”")
        lines.append(f"    play {a['play'] or '—'} (#{a['play_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick'] or '?'} · memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the scorecards on {agree} of {len(after)}: the plays it follows "
                 "are the ones the replay rewrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=m["record"], was=m["body"], now=mem2[m["record"]]["body"],
                             was_stance=m["stance"], now_stance=mem2[m["record"]].get("stance")) for m, _, _ in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, replay

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def ask_text(cli: CLI, pid: str, text: str, play_id: str, echo: bool = True) -> dict:
    """Any question about one play's program: the quick read and the memory read side by side. If the play was
    rewritten, also what it said before (metadata.previous), so the two reads can still be compared after a run."""
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", play=play_id, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, play_id, echo=echo)
    m = mem.get(play_id)
    before = None
    if m and m.get("previous"):
        before = dict(text=m["previous"], advice=m.get("previous_stance") or "")
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], play=q["play"], play_body=q.get("play_body", ""),
                quick=q["advice"], action=q.get("action", ""), before=before,
                memory={k: mr[k] for k in ("play", "advice", "said", "verdict", "replay")},
                agree=agrees(q["advice"], mr["advice"]))


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find_project(cli, story["project"]["key"], echo=not quiet)
    if not proj:
        note("nothing to delete")
        emit("cleaned", "")
        return
    facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda f: cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False),
                      facts))
    left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
    if left:
        die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
    cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
    note(f"deleted the project and its {len(facts)} facts")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one project for the sales playbook")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: scorecards, programs and plays")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the play on each question, and its advice")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — each program's pick, replayed in quarters without it")
    rows = memory_review(cli, pid, story, mem, quick)
    banner(6, TOTAL, "Write it back — the plays from the scorecards, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   play={q["ticket"]["id"]: [q["play"], q.get("play_rank")] for q in quick},
                   quick={r["ticket"]["id"]: r["quick"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["advice"] for r in rows},
                   replay={r["ticket"]["id"]: [r["memory"]["verdict"]["base"], r["memory"]["verdict"]["prog"]]
                           for r in rows},
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["play"], a["play_rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


PLAYS = ["7", "8", "9"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "replay", "cleanup"],
                    help="run = full demo (default); ask \"QUESTION\" --play 7|8|9 = the quick read and the memory "
                         "read of any question on that play's program; replay 7|8|9 = that program's pick replayed "
                         "in every quarter; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask) or the play number (replay)")
    ap.add_argument("--play", default="7", choices=PLAYS, help="ask: the play the question is about")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("ask", "replay") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. ask \"Should we coach the bottom five in Q4?\" --play 7, "
                 "or replay 9")
    if a.command == "replay" and a.args[0] not in PLAYS:
        ap.error(f"replay takes one of: {', '.join(PLAYS)}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py ask \"Should we coach the bottom five in Q4?\" --play 7`, "
              "`python3 demo.py replay 9`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — the quick read and the memory read")
        r = ask_text(cli, pid, a.args[0], f"PLAY-{a.play}")
        lines = [f"\n  “{r['ticket']['text']}” · PLAY-{a.play} — top {len(r['hits'])}:"]
        lines += [hit_line(h, "   ← the play" if h["record"] == r["play"] else "") for h in r["hits"]]
        if r["play"]:
            lines.append(f"  → {r['play']}: “{clip(r['play_body'], 190)}”")
        lines.append(f"  → quick read:  {r['quick'] or '?'}")
        if r["before"]:
            lines.append(f"  · {r['play']} was rewritten by a run; before that it said “{clip(r['before']['text'], 120)}” "
                         f"→ the quick read would have said: {r['before']['advice']}")
        rp = r["memory"]["replay"]
        lines.append(f"  who was in {rp['name']}: {' '.join(rp['members'])} = {rule_text(rp)}; the same pick by quarter:")
        lines += replay_lines(story, rp)
        lines.append(f"  · {r['memory']['said']}")
        lines.append(f"  → memory read: {r['memory']['advice']}  {'✓ same' if r['agree'] else '✗ differs'}"
                     + (f" (before the rewrite: {'✓ same' if r['before']['advice'] == r['memory']['advice'] else '✗ differs'})"
                        if r["before"] else ""))
        emit("text", "\n".join(lines))
    elif a.command == "replay":
        mr = memory_read(cli, pid, f"PLAY-{a.args[0]}")
        mem = entries(list_facts(cli, pid, echo=False))
        p, rp, v = mem[f"PLAY-{a.args[0]}"], mr["replay"], mr["verdict"]
        banner(2, 2, f"Replay — {rp['name']}'s pick in every quarter")
        lines = [f"\n  {p['record']} ({p['on']}): “{p['body']}”" if not p.get("previous") else
                 f"\n  {p['record']} now: “{p['body']}”\n  {'':<{len(p['record'])}} was: “{p['previous']}”",
                 f"\n  who was in {rp['name']} ({qlabel(rp['quarter'])}): {' '.join(rp['members'])} = {rule_text(rp)}",
                 "  the same pick, quarter by quarter:"]
        lines += replay_lines(story, rp)
        lines.append(f"  · {v['advice']} — {v['why']}")
        emit("text", "\n".join(lines))


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

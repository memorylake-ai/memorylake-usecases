#!/usr/bin/env python3
"""
UX research memory — a MemoryLake demo driven entirely by the `memorylake` CLI
(https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Ledgerline (fictional invoicing SaaS for small businesses) runs three interview
studies in 2026: March and June with self-serve signups who churned in their
first week, September with enterprise trial admins.  Each study's synthesis is
stored as findings that say "New users ...": the sample fell off on the way.
Every finding keeps its participant ids in metadata, and every participant is a
roster fact with a segment.

MemoryLake's conflict check flags two pairs of findings that cannot both be
true as written.  The team's habit is "keep the most recent study".  The demo
runs the same research in two projects:

  latest study wins   keep_fact the newer finding, both times.  Right for the
                      pricing page (same kind of participant, the page was
                      redesigned in between), wrong for CSV import: March's
                      "import is the main blocker" came from churned self-serve
                      signups, September's "import is fine" from enterprise
                      admins.  The finding about 74% of new signups is gone.

  check who said it   read the participants behind each side (plain code, no
                      model): disjoint segments → a segment split, dismiss and
                      rewrite both with their sample; same segment → a
                      reversal, keep the newer one.  After the rewrite the
                      conflict check raises nothing new: it agrees the scoped
                      findings can both be true.

Then the Q4 roadmap question goes to both projects with the same words.

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

PREFIX = "mlu-uxr"  # memorylake-usecases / ux-research-memory


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
    return json.loads((DATA / "research.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"

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


def list_facts(cli: CLI, pid: str, query: str = "", echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid] + (["--query", query] if query else []), echo=echo)


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise




# --------------------------------------------------------------------------- the research record

ARMS = ("latest", "provenance")
ARM_LABEL = {"latest": "latest study wins", "provenance": "check who said it"}
MONTHS = "January February March April May June July August September October November December".split()


def month_of(day: str) -> str:
    y, m, _ = day.split("-")
    return f"{MONTHS[int(m) - 1]} {y}"


def seed_items(story: dict) -> list[tuple[str, dict]]:
    """Participant roster + context notes: written first, in one call."""
    items = []
    for p in story["participants"]:
        items.append((p["text"], dict(key=f"uxr:{p['pid']}", kind="participant", pid=p["pid"], round=p["round"],
                                      segment=p["segment"])))
    for c in story["context"]:
        meta = dict(key=f"uxr:{c['key']}", kind="context", ref=c["key"])
        if c.get("on"):
            meta["on"] = c["on"]
        items.append((c["text"], meta))
    return items


def finding_items(story: dict, rnd: str) -> list[tuple[str, dict]]:
    r = story["rounds"][rnd]
    return [(f["text"], dict(key=f"uxr:{f['key']}", kind="finding", ref=f["key"], round=rnd, study=r["study"],
                             on=r["date"], by=r["lead"], participants=f["participants"]))
            for f in story["findings"] if f["round"] == rnd]


def read_memory(cli: CLI, pid: str, echo: bool = True) -> dict[str, dict]:
    """Every fact on the project by metadata key, metadata flattened in, plus the fact id and text."""
    out = {}
    for f in list_facts(cli, pid, echo=echo):
        m = f.get("metadata") or {}
        if m.get("key"):
            out[m["key"]] = dict(m, fid=f["id"], body=f.get("fact") or "")
    return out


def by_fid(mem: dict[str, dict]) -> dict[str, dict]:
    return {r["fid"]: r for r in mem.values()}


def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, str]:
    """One `fact add` for all the texts, then `fact update --metadata` on each (fact add takes no metadata).
    Returns key → fact id."""
    res = cli.run("fact", "add", "--project", pid, *[t for t, _ in items], scoped=True, echo=echo) or {}
    facts = res.get("facts") or []
    if len(facts) != len(items):
        die(f"`fact add` returned {len(facts)} fact(s) for {len(items)} text(s)")
    out = {meta["key"]: f["id"] for (_, meta), f in zip(items, facts)}
    jobs = [(f["id"], meta) for (_, meta), f in zip(items, facts)]
    cli.run("fact", "update", jobs[0][0], "--project", pid, "--metadata", json.dumps(jobs[0][1]), scoped=True, echo=echo)
    with ThreadPoolExecutor(max_workers=6) as pool:  # the rest at once; each call is independent and retried
        list(pool.map(lambda j: cli.run("fact", "update", j[0], "--project", pid, "--metadata", json.dumps(j[1]),
                                        scoped=True, echo=False), jobs[1:]))
    if len(items) > 1 and echo:
        note(f"… and {len(items) - 1} more `fact update --metadata` the same way")
    return out


# --------------------------------------------------------------------------- conflicts

def conflicts(cli: CLI, pid: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "conflict", "list", "--project", pid], size=100, echo=echo)


def pair_of(c: dict, fid_key: dict[str, str]) -> tuple[str, ...]:
    """The conflict as finding refs (R1-import, R3-import), oldest round first; unknown ids as `?…`."""
    return tuple(sorted(fid_key.get(i, "?" + i[-6:]) for i in c.get("fact_ids") or []))


def expected_pairs(story: dict) -> list[tuple[str, ...]]:
    """Two findings with the same topic suffix from different rounds are the pairs a reader would call contradictory."""
    by_topic = {}
    for f in story["findings"]:
        by_topic.setdefault(f["key"].split("-", 1)[1], []).append(f["key"])
    return sorted(tuple(sorted(v)) for v in by_topic.values() if len(v) == 2)


def wait_flagged(cli: CLI, pids: dict[str, str], fid_keys: dict[str, dict[str, str]], want: list[tuple[str, ...]],
                 last_write: float, cap: int = 150) -> dict[str, list[dict]]:
    """Poll each project's conflict list until every expected pair is there and the last write is ≥ 30 s old
    (a write made just now may still be under check). Gives up after `cap` seconds and says so."""
    t0, first = time.time(), True
    while True:
        got = {arm: conflicts(cli, pid, echo=first) for arm, pid in pids.items()}
        first = False
        have = {arm: {pair_of(c, fid_keys[arm]) for c in cs} for arm, cs in got.items()}
        done = all(set(want) <= h for h in have.values())
        if done and time.time() - last_write >= 30:
            note(f"both projects flagged after {int(time.time() - t0)} s (last write {int(time.time() - last_write)} s ago)")
            return got
        if time.time() - t0 > cap:
            note(f"not all expected conflicts within {cap} s — the check can run deferred; "
                 "`python3 demo.py conflicts` reads them later")
            return got
        emit("progress", f"    … waiting for the conflict check ({int(time.time() - t0)} s)", seconds=int(time.time() - t0))
        time.sleep(10)


def describe(story: dict, mem: dict[str, dict], c: dict) -> dict:
    """A conflict, read the way a researcher reads it: the two findings, their study, and who was in the room."""
    fids = by_fid(mem)
    sides = []
    for fid in c.get("fact_ids") or []:
        f = fids.get(fid)
        if not f:
            # Gone from the project (forgotten by keep_fact, or deleted when it was rewritten). The conflict keeps a
            # snapshot of its text; the story's original finding with that exact text gives its round.
            snap = next((x.get("fact_text") or "" for x in c.get("fact_snapshots") or [] if x.get("fact_id") == fid), "")
            orig = next((x for x in story["findings"] if x["text"] == snap), None)
            r = story["rounds"].get(orig["round"]) if orig else None
            gone = "forgotten" if fid in ((c.get("resolve") or {}).get("forgotten_fact_ids") or []) else "deleted"
            sides.append(dict(fid=fid, ref=orig["key"] if orig else "?", body=snap or "(no snapshot)",
                              round=orig["round"] if orig else "", study=r["study"] if r else "", on=r["date"] if r else "",
                              participants=orig["participants"] if orig else [], segments={}, gone=gone,
                              kind="finding" if orig else ""))
            f = sides[-1]
        segs = {}
        for p in f.get("participants") or []:
            sg = (mem.get(f"uxr:{p}") or {}).get("segment") or "unknown"
            segs[sg] = segs.get(sg, 0) + 1
        if f.get("gone"):
            f["segments"] = segs
            continue
        # The check sometimes pairs a finding with a roster or context fact; those carry no round / study / date.
        sides.append(dict(fid=fid, ref=f.get("ref") or f.get("key", "?"), body=f["body"], round=f.get("round", ""),
                          study=f.get("study", ""), on=f.get("on", ""), participants=f.get("participants") or [],
                          segments=segs, kind=f.get("kind", "")))
    sides.sort(key=lambda s: s["on"])
    return dict(id=c["id"], name=c.get("name") or "", category=c.get("category") or "", sides=sides,
                resolved=(c.get("resolve") or {}).get("strategy") or "",
                findings=all(s["kind"] == "finding" for s in sides))


def verdict(story: dict, mem: dict[str, dict], d: dict) -> dict:
    """Plain code, no model. Same kind of participant on both sides → the later study is newer evidence about the same
    people (a reversal; keep the newer finding, and say what changed in between). Disjoint participant segments → the
    two findings describe different people (a segment split; both stay, each scoped to who said it)."""
    if len(d["sides"]) != 2 or not all(s["segments"] for s in d["sides"]):
        return dict(cls="unknown", why="cannot trace both findings to participants")
    a, b = d["sides"]
    sa, sb = set(a["segments"]), set(b["segments"])
    if sa.isdisjoint(sb):
        return dict(cls="split", why=f"{fmt_segs(story, a)} vs {fmt_segs(story, b)} — no participant group in common")
    changes = [c for c in mem.values() if c.get("kind") == "context" and c.get("on") and a["on"] < c["on"] < b["on"]]
    why = f"both {fmt_segs(story, a)} / {fmt_segs(story, b)}; {b['round']} came later"
    if changes:
        why += f", after the {changes[0]['on']} change: “{clip(changes[0]['body'].split(': ', 1)[-1], 70)}”"
    return dict(cls="reversal", why=why, change=changes[0]["body"] if changes else "")


def fmt_segs(story: dict, side: dict) -> str:
    return " + ".join(f"{n} {story['segments'].get(s, {}).get('label', s)}" for s, n in side["segments"].items())


def scoped_text(story: dict, side: dict) -> str:
    """The finding with its sample put back: who, how many, which study."""
    (seg, n), = side["segments"].items()
    who = story["segments"][seg]["label"]
    rest = re.sub(r"^Finding:\s*New users\s+", "", side["body"])
    return f"Finding ({who}, n={n}, {side['study']}, {month_of(side['on'])}): these users {rest}"


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict[str, str]:
    """Conflict resolution is one-way (a forgotten fact cannot be pinned back without raising the same conflict
    again), so every run starts from a fresh pair of projects: leftovers from an earlier run are cleaned up first."""
    left = [k for k in ARMS if find_project(cli, k, echo=False)]
    if left or reset:
        note("found this demo's projects from an earlier run — cleaning up and starting over (a resolved conflict "
             "cannot be un-resolved, so every run replays the research from the first study)")
        cleanup(cli, quiet=True)
    pids = {}
    for arm in ARMS:
        p = story["projects"][arm]
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(arm), "--description",
                       p["description"], scoped=True, echo=arm == ARMS[0])
        pids[arm] = proj["id"]
        emit("text", f"    project  {p['name']:<44} {proj['id']}")
    note("same research, two projects: one resolves conflicts the team's usual way, the other checks first")
    emit("setup", "", projects=pids, names={a: story["projects"][a]["name"] for a in ARMS})
    return pids


# --------------------------------------------------------------------------- step 3: record the research

def record(cli: CLI, pids: dict[str, str], story: dict) -> tuple[dict[str, dict[str, str]], float]:
    note(f"{len(story['participants'])} participants across three studies and {len(story['context'])} context notes "
         "first; then each study's findings in the order they were written, as the synthesis wrote them — "
         "“New users …”, the sample dropped")
    fid_keys: dict[str, dict[str, str]] = {a: {} for a in ARMS}
    for arm in ARMS:
        got = pin(cli, pids[arm], seed_items(story), echo=arm == ARMS[0])
        fid_keys[arm].update({v: k for k, v in got.items()})
    lines = []
    last = time.time()
    for i, rnd in enumerate(story["rounds"]):
        if i:
            note("15 s between studies, so each study's findings are checked against what was already there")
            time.sleep(15)
        r = story["rounds"][rnd]
        for arm in ARMS:
            got = pin(cli, pids[arm], finding_items(story, rnd), echo=arm == ARMS[0] and i == 0)
            fid_keys[arm].update({v: k.split(":", 1)[1] for k, v in got.items()})
        last = time.time()
        n = sum(1 for p in story["participants"] if p["round"] == rnd)
        seg = story["segments"][next(p["segment"] for p in story["participants"] if p["round"] == rnd)]["label"]
        lines.append(f"\n  {rnd} {r['date']}  {r['study']} — {r['lead']}, {n} {seg}")
        for f in story["findings"]:
            if f["round"] == rnd:
                lines.append(f"      {f['key']:<13} {clip(f['text'], 96)}")
    for arm in ARMS:
        fid_keys[arm] = {fid: k.replace("uxr:", "") for fid, k in fid_keys[arm].items()}
    emit("text", "\n".join(lines) + f"\n\n  each project: {len(story['participants']) + len(story['context']) + len(story['findings'])} facts "
         f"({len(story['participants'])} participants · {len(story['context'])} context · {len(story['findings'])} findings), "
         "every one with metadata: round, study, participants, segment")
    emit("record", "", rounds=[dict(id=k, **v) for k, v in story["rounds"].items()],
         findings=story["findings"], participants=story["participants"], context=story["context"])
    return fid_keys, last


# --------------------------------------------------------------------------- step 4: flagged

def flagged(cli: CLI, pids: dict[str, str], story: dict, fid_keys: dict, last: float) -> dict[str, list[dict]]:
    want = expected_pairs(story)
    got = wait_flagged(cli, pids, fid_keys, want, last)
    out, lines = {}, []
    for arm in ARMS:
        mem = read_memory(cli, pids[arm], echo=False)
        out[arm] = [describe(story, mem, c) for c in got[arm]]
        pairs = [pair_of(c, fid_keys[arm]) for c in got[arm]]
        extra = [p for p in pairs if p not in want]
        lines.append(f"\n  {story['projects'][arm]['name']}: {len(pairs)} conflict(s)"
                     + (f" — {len(extra)} not expected: {extra}" if extra else ""))
    for d in out[ARMS[0]]:
        lines.append(f"\n  {d['category']} “{d['name']}”")
        for s in d["sides"]:
            lines.append(f"      {s['round']} {s['on']}  {clip(s['body'], 100)}")
    lines.append("\n  → the check is doing its job: these pairs cannot both be true as written. "
                 "What to do about each is the team's call")
    emit("text", "\n".join(lines))
    emit("flagged", "", conflicts=out, expected=[list(p) for p in want])
    return out


# --------------------------------------------------------------------------- step 5: latest study wins

def latest_wins(cli: CLI, pid: str, story: dict, found: list[dict]) -> dict:
    note("the team's habit (it is in memory too): when two findings disagree, keep the one from the most recent study")
    done = []
    for i, d in enumerate(found):
        if d["resolved"] or len(d["sides"]) != 2 or not d["findings"]:  # never resolve away a roster/context fact
            continue
        old, new = d["sides"]
        cli.run("fact", "conflict", "resolve", d["id"], "--project", pid, "--strategy", "keep_fact",
                "--keep-fact-id", new["fid"], scoped=True, echo=True)
        done.append(dict(name=d["name"], kept=new["ref"], forgot=old["ref"], forgot_body=old["body"]))
    mem = read_memory(cli, pid)
    findings = sorted((r for r in mem.values() if r.get("kind") == "finding"), key=lambda r: (r["on"], r["ref"]))
    lines = []
    for x in done:
        lines.append(f"\n  “{x['name']}” — kept {x['kept']}, forgot {x['forgot']}")
    gone = [f["key"] for f in story["findings"] if f"uxr:{f['key']}" not in mem]
    lines.append(f"\n  findings left on the project: {len(findings)} of {len(story['findings'])}"
                 f"  (forgotten: {', '.join(gone) or 'none'})")
    for f in findings:
        lines.append(f"      {f['ref']:<13} {clip(f['body'], 96)}")
    emit("text", "\n".join(lines))
    out = dict(resolved=done, left=[f["ref"] for f in findings], forgotten=gone)
    emit("latest", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: check who said it

def check(cli: CLI, pid: str, story: dict) -> list[dict]:
    note("before resolving: each finding's metadata names the participants behind it; their roster facts name "
         "the segment. Read both sides, then compare who was in the room")
    mem = read_memory(cli, pid)
    found = [describe(story, mem, c) for c in conflicts(cli, pid)]
    rows, lines = [], []
    for d in found:
        v = verdict(story, mem, d)
        rows.append(dict(d, verdict=v))
        lines.append(f"\n  “{d['name']}”")
        for s in d["sides"]:
            lines.append(f"      {s['round']} {s['study']:<34} {', '.join(s['participants'][:3])}…"
                         f"{s['participants'][-1]}  → {fmt_segs(story, s)}")
        tag = {"split": "SEGMENT SPLIT — different people; both findings stand, each for its own segment",
               "reversal": "REVERSAL — same kind of participant, later study; the newer finding replaces the older",
               "unknown": "cannot tell"}[v["cls"]]
        lines.append(f"      → {tag}\n        ({v['why']})")
    emit("text", "\n".join(lines))
    emit("check", "", rows=rows)
    return rows


# --------------------------------------------------------------------------- step 7: resolve each one its own way

def apply(cli: CLI, pid: str, story: dict, rows: list[dict]) -> dict:
    acts, last = [], time.time()
    resolved_ids = []
    for r in rows:
        v, (old, new) = r["verdict"], r["sides"]
        if v["cls"] == "reversal":
            cli.run("fact", "conflict", "resolve", r["id"], "--project", pid, "--strategy", "keep_fact",
                    "--keep-fact-id", new["fid"], scoped=True)
            acts.append(dict(name=r["name"], cls="reversal", kept=new["ref"], forgot=old["ref"]))
        elif v["cls"] == "split":
            cli.run("fact", "conflict", "resolve", r["id"], "--project", pid, "--strategy", "dismiss", scoped=True)
            note("dismiss changes no fact; the two findings are then rewritten with their sample, so the record says "
                 "who each one is about (delete the unscoped text, add the scoped one)")
            texts = [scoped_text(story, s) for s in (old, new)]
            for i, s_ in enumerate((old, new)):  # one id per call (several ids in one delete has returned 500)
                cli.run("fact", "delete", "--project", pid, s_["fid"], scoped=True, echo=i == 0)
            res = cli.run("fact", "add", "--project", pid, *texts, scoped=True) or {}
            for s, f, t in zip((old, new), res.get("facts") or [], texts):
                mem_meta = dict(key=f"uxr:{s['ref']}", kind="finding", ref=s["ref"], round=s["round"], study=s["study"],
                                on=s["on"], participants=s["participants"], scoped=True,
                                previous=s["body"], segment=next(iter(s["segments"])))
                cli.run("fact", "update", f["id"], "--project", pid, "--metadata", json.dumps(mem_meta), scoped=True,
                        echo=False)
            acts.append(dict(name=r["name"], cls="split", scoped=texts, refs=[old["ref"], new["ref"]]))
            last = time.time()
        resolved_ids.append(r["id"])
    # The scoped rewrite is new writing, so the conflict check runs on it. Wait it out before calling it clean.
    note("the rewrite is a new write, so the conflict check runs on it too — waiting 60 s for anything new")
    t0, new_c = time.time(), []
    while True:
        new_c = [c for c in conflicts(cli, pid, echo=time.time() - t0 < 1) if c["id"] not in resolved_ids]
        if new_c or time.time() - last >= 60:
            break
        emit("progress", f"    … {int(time.time() - last)} s since the rewrite, no new conflict", seconds=int(time.time() - last))
        time.sleep(10)
    mem = read_memory(cli, pid, echo=False)
    lines = []
    for a in acts:
        if a["cls"] == "reversal":
            lines.append(f"\n  “{a['name']}” — reversal: kept {a['kept']}, forgot {a['forgot']}")
        else:
            lines.append(f"\n  “{a['name']}” — split: dismissed, both rewritten with their sample")
            for t in a["scoped"]:
                lines.append(f"      {clip(t, 110)}")
    descr = [describe(story, mem, c) for c in new_c]
    lines.append(f"\n  new conflicts after the rewrite: {len(new_c)}"
                 + (" — the check itself agrees the scoped findings can both be true" if not new_c else
                    " — " + "; ".join(f"“{d['name']}”" for d in descr)))
    findings = sorted((r for r in mem.values() if r.get("kind") == "finding"), key=lambda r: (r["on"], r["ref"]))
    lines.append(f"  findings on the project: {len(findings)} of {len(story['findings'])}")
    emit("text", "\n".join(lines))
    out = dict(actions=acts, new_conflicts=[dict(name=d["name"], refs=[s["ref"] for s in d["sides"]]) for d in descr],
               left=[f["ref"] for f in findings], waited=int(time.time() - last))
    emit("apply", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: the Q4 question

def on_topic(story: dict, body: str) -> bool:
    return any(t.lower() in body.lower() for t in story["topic"])


def ask(cli: CLI, pids: dict[str, str], story: dict, question: str, echo: bool = True) -> dict:
    """The same question to both projects. The text is read from `fact list` by fact id (search can answer from a
    cache of older text); a hit whose id is not on the project any more is reported, not hidden."""
    out = {}
    for arm in ARMS:
        mem = read_memory(cli, pids[arm], echo=False)
        fids = by_fid(mem)
        res = cli.run("search", question, "--projects", pids[arm], "--types", "fact", "--top-k", "8", scoped=True,
                      echo=echo) or {}
        hits, cached = [], []
        for i, f in enumerate(res.get("facts") or [], 1):
            cur = fids.get(f.get("id"))
            if cur is None:
                cached.append(clip(f.get("fact") or "", 60))
                continue
            hits.append(dict(rank=f.get("rank") or i, ref=cur.get("ref") or cur.get("pid") or "?", kind=cur.get("kind"),
                             body=cur["body"], segment=cur.get("segment") or ""))
        evidence = [h for h in hits if h["kind"] == "finding" and on_topic(story, h["body"])]
        covered = set()
        for h in evidence:
            src = mem.get(f"uxr:{h['ref']}") or {}
            for p in src.get("participants") or []:
                covered.add((mem.get(f"uxr:{p}") or {}).get("segment"))
        share = sum(v["share"] for k, v in story["segments"].items() if k in covered)
        out[arm] = dict(hits=hits, evidence=evidence, cached=cached, share=share,
                        segments=sorted(story["segments"][s]["label"] for s in covered if s in story["segments"]))
    return out


def question(cli: CLI, pids: dict[str, str], story: dict) -> dict:
    q = story["question"]
    note(f"Q4 planning asks the roadmap question (“{next(c['text'] for c in story['context'] if c['key'] == 'ctx-q4').split(': ', 1)[1]}”) — "
         "both projects get the same question, top 8 facts each")
    time.sleep(5)  # new facts reach search a few seconds after `fact list`
    res = ask(cli, pids, story, q)
    lines = [f"\n  “{q}”"]
    for arm in ARMS:
        r = res[arm]
        lines.append(f"\n  {story['projects'][arm]['name']}: {len(r['evidence'])} finding(s) on the import step in the top 8")
        for h in r["evidence"]:
            lines.append(f"      #{h['rank']:<3} {h['ref']:<11} {clip(h['body'], 98)}")
        lines.append(f"      → the participants behind it: {' + '.join(r['segments']) or 'nobody'} = {r['share']}% of new signups")
        if r["cached"]:
            lines.append(f"      · {len(r['cached'])} hit(s) carry fact ids not on the project now — search answered from its cache")
    a, b = res["latest"], res["provenance"]
    lines.append(f"\n  → same research, same question: {a['share']}% vs {b['share']}% of new signups covered. "
                 "“Latest study wins” threw away the finding about the 74% — the column-mapping screen "
                 "has not changed since January, so nothing ever made it untrue")
    emit("text", "\n".join(lines))
    emit("question", "", question=q, arms=res)
    return res


# --------------------------------------------------------------------------- read-only: conflicts, ask

def need_projects(cli: CLI) -> dict[str, str]:
    out = {}
    for arm in ARMS:
        p = find_project(cli, arm, echo=False)
        if p is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        out[arm] = p["id"]
    return out


def show_conflicts(cli: CLI, pids: dict[str, str], story: dict) -> dict:
    out, lines = {}, []
    for arm in ARMS:
        mem = read_memory(cli, pids[arm], echo=False)
        ds = [describe(story, mem, c) for c in conflicts(cli, pids[arm])]
        out[arm] = ds
        lines.append(f"\n  {story['projects'][arm]['name']}: {len(ds)} conflict(s)")
        for d in ds:
            v = verdict(story, mem, d)
            lines.append(f"    {d['category']} “{d['name']}”  resolved: {d['resolved'] or 'no'}  · reads as: {v['cls']}")
            for s in d["sides"]:
                lines.append(f"        {s['round'] or '  '} {clip(s['body'], 92)}" + (f"  [{s['gone']}]" if s.get("gone") else ""))
    emit("text", "\n".join(lines))
    return out


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    n = 0
    for arm in ARMS:
        proj = find_project(cli, arm, echo=not quiet and arm == ARMS[0])
        if not proj:
            continue
        facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        with ThreadPoolExecutor(max_workers=6) as pool:  # one id per call (several ids in one delete has returned 500)
            list(pool.map(lambda f: cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False),
                          facts))
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on {proj['name']} after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        note(f"deleted “{proj['name']}” and its {len(facts)} facts")
        n += 1
    if not n:
        note("nothing to delete")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — the same research in two projects")
    pids = setup(cli, story, reset)
    cli.pids = pids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Record three studies — participants, context, then each study's findings")
    fid_keys, last = record(cli, pids, story)
    banner(4, TOTAL, "Flagged — the conflict check finds the findings that disagree")
    found = flagged(cli, pids, story, fid_keys, last)
    banner(5, TOTAL, "Latest study wins — the team's usual way, in the first project")
    lw = latest_wins(cli, pids["latest"], story, found["latest"])
    banner(6, TOTAL, "Check who said it — participants behind each finding, in the second project")
    rows = check(cli, pids["provenance"], story)
    banner(7, TOTAL, "Resolve each one its own way — keep the reversal, scope the split")
    ap_ = apply(cli, pids["provenance"], story, rows)
    banner(8, TOTAL, "The Q4 question — both projects, same words")
    qa = question(cli, pids, story)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   flagged={a: [[s["ref"] for s in d["sides"]] for d in found[a]] for a in ARMS},
                   verdicts={"-".join(s["ref"] for s in r["sides"]): r["verdict"]["cls"] for r in rows},
                   latest_forgot=lw["forgotten"], new_conflicts=ap_["new_conflicts"],
                   evidence={a: [h["ref"] for h in qa[a]["evidence"]] for a in ARMS},
                   share={a: qa[a]["share"] for a in ARMS}, cached={a: len(qa[a]["cached"]) for a in ARMS})
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "conflicts", "ask", "cleanup"],
                    help="run = full demo (default); conflicts = both projects' conflicts and how each reads; "
                         "ask \"QUESTION\" = the same question to both projects; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one argument, e.g. ask \"What blocks new users during setup?\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py conflicts`, `python3 demo.py ask \"What blocks new users during setup?\"`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    pids = need_projects(cli)
    if a.command == "conflicts":
        banner(2, 2, "Conflicts — both projects")
        show_conflicts(cli, pids, story)
    elif a.command == "ask":
        banner(2, 2, "Ask — the same question to both projects")
        res = ask(cli, pids, story, a.args[0])
        lines = [f"\n  “{a.args[0]}”"]
        for arm in ARMS:
            r = res[arm]
            lines.append(f"\n  {story['projects'][arm]['name']}:")
            for h in r["hits"]:
                lines.append(f"      #{h['rank']:<3} {h['ref']:<11} {clip(h['body'], 98)}")
            if r["cached"]:
                lines.append(f"      · {len(r['cached'])} hit(s) not on the project now — search answered from its cache")
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

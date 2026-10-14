#!/usr/bin/env python3
"""
Source channel memory for recruiting teams — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Corvid Pay (fictional) hires backend and data engineers through three
channels: a LinkedIn Recruiter seat, employee referrals, and — since June —
Ferrous Guild, a community job board.  Its hiring memory lives in one
MemoryLake project: every hire from the ATS (channel, role, start date), what
happened to them from the HRIS (6-month review, exit day), and the channel
notes Jonah Reyes left when he handed recruiting to Amara Osei.

The quick read is what the page calls "per-channel reflection memory": `search`
the req, take the closest channel note, lead with the channel it names.
Jonah's backend note says Ferrous Guild is the best source by a distance —
5 hires since June, all 5 still here — while LinkedIn kept 4 of 6.

Every word of it is true.  The conclusion is not.  The memory read takes every
hire and outcome (`fact list`, every page) and counts each channel at the same
follow-up time, over only the hires who have got that far: Ferrous Guild's hires
have been here 37 to 136 days, and LinkedIn's two losses came on days 143 and
158.  At 120 days LinkedIn had kept 6 of 6 too.  On the 6-month review —
the measure the team actually uses — referrals pass 5 of 5, LinkedIn 4 of 6,
and Ferrous Guild 0 of 0: not readable until its third hire gets there on
2027-01-09.  The data-roles note is the control: every hire in it is past the
review, and the two reads agree.

Step 6 rewrites the note that read an open follow-up window as zero losses
(`fact update --text`, the old text kept in metadata.previous, the change
visible in `fact trace`), and the same quick-read code then agrees with the
follow-up on the day's later reqs.

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-scr"  # memorylake-usecases / source-channel-memory


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
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null.
    m = dict(key=f"scr:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], by=r["by"], on=r["on"])
    if r["kind"] == "hire":
        m.update(hire=r["hire"], name=r["name"], family=r["family"], role=r["role"], channel=r["channel"], start=r["start"])
    elif r["kind"] == "exit":
        m.update(hire=r["hire"], day=r["day"])
    elif r["kind"] == "review":
        m.update(hire=r["hire"], result=r["result"])
    else:
        m.update(family=r["family"], prefer=r["prefer"])
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
    """The pinned records by record id (H-119, X-104, NOTE-BE …), metadata flattened in, plus fact id and text."""
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
    return {k: r.get(k) for k in ("record", "tool", "kind", "by", "on", "hire", "name", "family", "role", "channel", "start",
                                  "day", "result", "prefer", "fid", "body", "rank", "previous")}


# --------------------------------------------------------------------------- follow-up time (plain code, no model)

def days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def plus(a: str, n: int) -> str:
    return (date.fromisoformat(a) + timedelta(days=n)).isoformat()


def follow(mem: dict, family: str, story: dict) -> list[dict]:
    """Every hire in the role family with what the HRIS says happened to them, as of the story's today: the day they
    left (if they did), whether they passed the 6-month review, and how long they have been here."""
    today, hz = story["today"], story["horizon"]
    exits = {r["hire"]: r for r in mem.values() if r.get("kind") == "exit"}
    reviews = {r["hire"]: r for r in mem.values() if r.get("kind") == "review"}
    out = []
    for h in sorted((r for r in mem.values() if r.get("kind") == "hire" and r.get("family") == family),
                    key=lambda r: r["start"]):
        x, rv = exits.get(h["hire"]), reviews.get(h["hire"])
        left = int(x["day"]) if x else None
        tenure = left if left is not None else days(h["start"], today)
        if left is not None and left < hz:
            status = f"left on day {left}"
        elif rv:
            status = f"6-month review {rv['result']}" + (f", left on day {left}" if left is not None else "")
        else:
            status = f"day {tenure}, before the 6-month review"
        out.append(dict(hire=h["hire"], name=h["name"], role=h["role"], channel=h["channel"], start=h["start"],
                        left=left, review=rv["result"] if rv else "", tenure=tenure, status=status))
    return out


def outcome_at(f: dict, d: int, story: dict):
    """True / False once the hire has reached day `d` (or left before it), None while they have not got there yet.
    At the review horizon the outcome is the 6-month review itself."""
    if f["left"] is not None and f["left"] < d:
        return False
    if days(f["start"], story["today"]) < d:
        return None
    if d >= story["horizon"]:
        return f["review"] == "passed"
    return True


def cohort_table(mem: dict, family: str, story: dict) -> dict:
    """Per channel: hired, still here (the quick tally), and — for each follow-up horizon — how many hires have
    reached it and how many of those were still here / passed. A channel whose hires have not reached the review
    horizon (fewer than min_reached of them) cannot be read yet."""
    fs = follow(mem, family, story)
    hz, k = story["horizon"], story["min_reached"]
    rows = []
    for ch in story["channels"]:
        hs = [f for f in fs if f["channel"] == ch]
        if not hs:
            continue
        at = {}
        for d in story["horizons"]:
            got = [outcome_at(f, d, story) for f in hs]
            at[str(d)] = [sum(1 for g in got if g), sum(1 for g in got if g is not None)]
        here = [f for f in hs if f["left"] is None]
        starts = sorted(f["start"] for f in hs if outcome_at(f, hz, story) is None)
        rows.append(dict(channel=ch, hired=len(hs), still=[len(here), len(hs)], at=at,
                         tenure=[min((f["tenure"] for f in here), default=0), max((f["tenure"] for f in here), default=0)],
                         unproven=at[str(hz)][1] < k,
                         first_on=plus(starts[0], hz) if starts else "",
                         readable_on=plus(starts[k - 1 - at[str(hz)][1]], hz) if at[str(hz)][1] < k and len(starts) >= k - at[str(hz)][1] else ""))
    losses = [dict(hire=f["hire"], channel=f["channel"], day=f["left"]) for f in fs if f["left"] is not None]
    return dict(family=family, horizons=story["horizons"], horizon=hz, rows=rows, losses=sorted(losses, key=lambda x: x["day"]),
                hires=fs)


def rate(t) -> float:
    return t[0] / t[1] if t[1] else -1.0


def pct(t) -> str:
    return f"{t[0]}/{t[1]} {round(100 * t[0] / t[1]):>3}%" if t[1] else f"{t[0]}/{t[1]}   —  "


def pc(t) -> str:
    return f"{t[0]}/{t[1]}" if t[1] else "0/0"


def row_of(tb: dict, ch: str) -> dict:
    return next(r for r in tb["rows"] if r["channel"] == ch)


def decide(tb: dict) -> tuple[str, str]:
    """The channel to lead with: the best 6-month-review pass rate among channels with enough hires past the
    review; channels without that many cannot be read yet."""
    hz = str(tb["horizon"])
    proven = [r for r in tb["rows"] if not r["unproven"]]
    if not proven:
        return "", "no channel has enough hires past the 6-month review yet"
    best = max(proven, key=lambda r: (rate(r["at"][hz]), r["at"][hz][1]))
    others = [r for r in proven if r is not best]
    said = f"{best['channel']} ({pc(best['at'][hz])} passed the 6-month review"
    said += "".join(f" vs {r['channel']} {pc(r['at'][hz])}" for r in others) + ")"
    for r in tb["rows"]:
        if r["unproven"]:
            said += f"; {r['channel']} not readable until {r['readable_on'] or 'more hires reach day ' + hz}"
    return best["channel"], said


def why_lines(tb: dict, quick: str) -> list[str]:
    """Why the quick tally and the follow-up disagree (or that they do not)."""
    hz = tb["horizon"]
    r = row_of(tb, quick) if quick and any(x["channel"] == quick for x in tb["rows"]) else None
    if r is None or not r["unproven"]:
        unp = [x for x in tb["rows"] if x["unproven"]]
        if not unp:
            return [f"  · the note holds: every hire in this comparison is past day {hz} (or left before it), so “still "
                    f"here” and the 6-month review count the same people"]
        return [f"  · the note leads with a channel read on the 6-month review; {x['channel']} cannot be read yet — "
                f"{x['at'][str(hz)][1]} of {x['hired']} hires past day {hz}, the {ordinal(load_story()['min_reached'])} "
                f"gets there on {x['readable_on']}" for x in unp]
    lo, hi = r["tenure"]
    early = [x for x in tb["losses"] if x["channel"] != quick and x["day"] < hz]
    same = max([d for d in tb["horizons"] if d <= hi], default=None)
    lines = [f"  · the note counts who is still here. {quick}'s hires have been here {lo} to {hi} days; the losses "
             f"elsewhere came on day{'s' if len(early) > 1 else ''} {and_list([str(x['day']) for x in early])}" if early else
             f"  · the note counts who is still here. {quick}'s hires have been here {lo} to {hi} days"]
    if same:
        others = [x for x in tb["rows"] if x["channel"] != quick]
        lines.append(f"  · at {same} days — the furthest any {quick} hire has got — "
                     + ", ".join(f"{x['channel']} kept {pc(x['at'][str(same)])}" for x in others)
                     + f": the same {round(100 * rate(r['at'][str(same)]))}% {quick} has now")
    lines.append(f"  · {quick}: {r['at'][str(hz)][1]} of {r['hired']} hires past day {hz}; the first gets there on "
                 f"{r['first_on']}, the {ordinal(load_story()['min_reached'])} on {r['readable_on']}")
    return lines


def ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


def table_lines(tb: dict, quick: str = "", memory: str = "") -> list[str]:
    hs = tb["horizons"]
    lines = [f"      {'channel':<20} {'hired':>5}  {'still here':>11}  " + "  ".join(
        f"{('at ' + str(d) + ' days'):>12}" for d in hs) + "   (passed / reached)"]
    for r in tb["rows"]:
        mark = ""
        if r["channel"] == quick:
            mark += "  ← the note"
        if r["channel"] == memory:
            mark += "  ← memory read"
        lines.append(f"      {r['channel']:<20} {r['hired']:>5}  {pct(r['still']):>11}  "
                     + "  ".join(f"{pct(r['at'][str(d)]):>12}" for d in hs) + mark)
    return lines


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, pid: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", tool=m.get("tool") or "?", kind=m.get("kind") or "?"))
    return out


def hit_line(story: dict, h: dict, mark: str = "") -> str:
    tool = story["tools"].get(h["tool"], h["tool"])
    return f"      #{h['rank']:<3} {h['record']:<9} {tool:<13} {clip(h['body'], 80)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, t: dict, echo: bool = True) -> dict:
    """The page's "per-channel reflection memory", done the quick way: search the req, take the closest channel note
    for the req's role family, lead with the channel it names. The note is read from `fact list` by fact id (search can
    answer from a cache of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    note_hit = next((h for h in hits if h["kind"] == "note" and h.get("family") == t["family"]), None)
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    if note_hit is None:
        return dict(ticket=t, hits=hits, note=None, advice="", said=f"no {t['family']} channel note in the top 8",
                    cached=cached)
    cur = by_fid.get(note_hit["fid"], note_hit)
    return dict(ticket=t, hits=hits, note=cur["record"], note_rank=note_hit["rank"], note_body=cur["body"],
                advice=cur.get("prefer") or "", cached=cached, said=f"the note says {cur.get('prefer')}")


# --------------------------------------------------------------------------- read 2: at the same follow-up time

def memory_read(cli: CLI, pid: str, family: str, echo: bool = True) -> dict:
    """Every hire and outcome (`fact list`, every page), each channel counted at the same follow-up horizons, the call
    made on the 6-month review among channels whose hires have reached it."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    tb = cohort_table(mem, family, story)
    pick, said = decide(tb)
    return dict(family=family, table=tb, advice=pick, said=said, unproven=[r["channel"] for r in tb["rows"] if r["unproven"]])


def agrees(quick: str, memory: str) -> bool:
    return bool(quick) and quick == memory


def mem_public(mr: dict) -> dict:
    return {k: mr[k] for k in ("family", "advice", "said", "unproven")}


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

def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key) — one `fact add` for all of them,
    then `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new}."""
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
    if missing:
        res = cli.run("fact", "add", "--project", pid, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, "--project", pid, "--metadata", json.dumps(meta), scoped=True,
                    echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way")
    return out


REWRITE_KEYS = ("previous", "previous_prefer", "rewritten_on", "rewritten_by", "basis", "readable_on")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of Amara's first channel review: notes an earlier run rewrote are put back word
    for word from metadata.previous."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous"):
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            meta["prefer"] = m.get("previous_prefer") or meta.get("prefer")
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} note(s); put back, so this run starts from the same morning")
    return n


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("three sources, one project: every hire from the ATS, what happened to them from the HRIS, and the channel notes "
         "Jonah Reyes left. One fact per record, word for word, then `fact update --metadata` (channel, role family, "
         "start date; exit day; review result)")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    hires = [r for r in mem.values() if r["kind"] == "hire"]
    lines = [f"    {story['tools']['ats']:<14} {len(hires):>2}   "
             + " · ".join(f"{fam} {sum(1 for h in hires if h['family'] == fam)}" for fam in story["families"]),
             f"    {story['tools']['hris']:<14} {sum(1 for r in mem.values() if r['kind'] in ('exit', 'review')):>2}   "
             f"6-month reviews {sum(1 for r in mem.values() if r['kind'] == 'review')} · "
             f"exits {sum(1 for r in mem.values() if r['kind'] == 'exit')}",
             f"    {story['tools']['notes']:<14} {sum(1 for r in mem.values() if r['kind'] == 'note'):>2}",
             "\n  Jonah's notes, as he left them:"]
    for r in sorted((r for r in mem.values() if r["kind"] == "note"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<9} {r['body']}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} open reqs on Amara's desk. The quick read: `search` the req, take the closest channel "
         "note for its role family, lead with the channel it names")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']} ({t['family']}): “{t['text']}”", f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← closest note" if h["record"] == q["note"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["note"]:
            lines.append(f"  → {q['note']}: “{clip(q['note_body'], 170)}”")
        lines.append(f"  → quick read: lead with {q['advice'] or '?'}")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], note=q["note"], note_body=q.get("note_body", ""), advice=q["advice"],
                                  said=q["said"], hits=[public(h) for h in q["hits"]], cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, quick: list[dict]) -> list[dict]:
    note(f"the memory read: every hire and outcome (`fact list`, every page), each channel counted at the same follow-up "
         f"time — {and_list([str(d) for d in story['horizons']])} days — only over the hires who have got that far; the "
         f"call is made on the {story['horizon']}-day review, among channels with {story['min_reached']}+ hires past it")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        if not q["note"]:
            continue
        mr = memory_read(cli, pid, t["family"], echo=i == 0)
        tb = mr["table"]
        lines = [f"\n  {t['id']} {t['title']} · {t['family']}"]
        lines += table_lines(tb, q["advice"], mr["advice"])
        lines += why_lines(tb, q["advice"])
        lines.append(f"  → quick read:  {q['advice']}")
        lines.append(f"  → memory read: {mr['said']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, note=q["note"], quick=q["advice"], memory=mr, agree=agrees(q["advice"], mr["advice"])))
    lines = [f"\n  {'req':<8} {'role':<42} {'the note says':<19} {'the follow-up says':<19}", "  " + "-" * 96]
    for r in rows:
        lines.append(f"  {r['ticket']['id']:<8} {r['ticket']['title']:<42} {r['quick']:<19} {r['memory']['advice']:<19} "
                     f"{'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)} reqs: the note leads with a channel whose “zero exits” is a follow-up "
                 "window that has not closed" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], note=r["note"], quick=r["quick"], agree=r["agree"],
                                  memory=mem_public(r["memory"]), table=r["memory"]["table"],
                                  why=[x.strip()[2:] for x in why_lines(r["memory"]["table"], r["quick"])]) for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def rewritten_note(n: dict, mr: dict, story: dict) -> str:
    """The note's conclusion only; the counts (they change with every hire and review) go to metadata."""
    tb = mr["table"]
    pick = mr["advice"]
    bits = []
    for r in tb["rows"]:
        if r["unproven"]:
            bits.append(f"{r['channel']} is not readable yet - {'none' if not r['at'][str(tb['horizon'])][1] else 'too few'} "
                        f"of its hires {'has' if not r['at'][str(tb['horizon'])][1] else 'have'} reached the 6-month review, "
                        f"and the losses elsewhere came at a tenure no {r['channel']} hire has reached. Keep "
                        f"{r['channel']} running alongside and re-read it once {story['min_reached']} of its hires pass "
                        f"day {tb['horizon']} ({r['readable_on']}).")
    proven = [r["channel"] for r in tb["rows"] if not r["unproven"] and r["channel"] != pick]
    if proven and any(r["unproven"] for r in tb["rows"]):
        bits.append(f"Do not drop {and_list(proven)} on the strength of a channel that cannot be read yet.")
    return (f"Channel retrospective, {n['family']} roles (rewritten by {story['manager']['name']} from the ATS and HRIS "
            f"records): lead {n['family']} reqs with {pick}. " + " ".join(bits)).strip()


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next req will read it: the note that read an open follow-up window as zero losses is "
         "rewritten from the hires and their outcomes (`fact update --text`; the old text, the old advice and the counts "
         "go to metadata)")
    updates, seen = [], set()
    for r in rows:
        if r["agree"] or r["note"] in seen:
            continue
        seen.add(r["note"])
        n = mem[r["note"]]
        text = rewritten_note(n, r["memory"], story)
        if text == n["body"]:
            continue
        tb = r["memory"]["table"]
        meta = {k: n[k] for k in ("key", "record", "tool", "kind", "by", "on", "family")}
        basis = {row["channel"]: dict(hired=row["hired"], still=row["still"], **row["at"]) for row in tb["rows"]}
        meta.update(prefer=r["memory"]["advice"], previous=n["body"], previous_prefer=n["prefer"], rewritten_on=today,
                    rewritten_by=story["manager"]["name"], basis=basis,
                    readable_on={row["channel"]: row["readable_on"] for row in tb["rows"] if row["unproven"]})
        updates.append((n, text, meta))
    for i, (n, text, meta) in enumerate(updates):
        cli.run("fact", "update", n["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more note(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for n, _, _ in updates:
        lines.append(f"\n    {n['record']} was “{clip(n['body'], 170)}”")
        lines.append(f"    {'':<{len(n['record'])}} now “{mem2[n['record']]['body']}”")
    emit("text", "\n".join(lines))
    trace = []
    if updates:
        n = updates[0][0]
        res = cli.run("fact", "trace", n["fid"], "--project", pid, scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {n['record']} (newest first):"]
        for e in trace:
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(e['new'], 90)}")
        emit("text", "\n".join(tl))
    # Later the same day, new wording (search answers a repeated question from its cache).
    note("later the same day, three more reqs of the same kinds — the quick read again, unchanged code. Search gets a "
         "rewritten fact a few seconds after `fact list` does; each read is retried until its note is the current text")
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
        mr = memory_read(cli, pid, t["family"], echo=False) if q["note"] else None
        after.append(dict(ticket=t, note=q["note"], note_rank=q.get("note_rank"), quick=q["advice"],
                          memory=mr["advice"] if mr else "", agree=bool(mr) and agrees(q["advice"], mr["advice"]),
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']} {a['ticket']['title']} · {a['ticket']['family']}: “{a['ticket']['text']}”")
        lines.append(f"    closest note {a['note']} (#{a['note_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick']} · memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the follow-up on {agree} of {len(after)}: the note it follows "
                 "is the one the hire records rewrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=n["record"], was=n["body"], now=mem2[n["record"]]["body"],
                             was_prefer=n["prefer"], now_prefer=mem2[n["record"]].get("prefer")) for n, _, _ in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: review, cohort

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def review_text(cli: CLI, pid: str, text: str, family: str, echo: bool = True) -> dict:
    """Any sourcing question: the quick read and the memory read side by side. After a run has rewritten the note,
    the quick read follows the new text — so the note's original advice (metadata.previous) is read too, and checked
    the same way, to keep the comparison visible."""
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", title="", family=family, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, family, echo=echo) if q["note"] else None
    before = None
    if q["note"] and mem[q["note"]].get("previous"):
        n = mem[q["note"]]
        before = dict(note_body=n["previous"], quick=n.get("previous_prefer") or "",
                      agree=bool(mr) and agrees(n.get("previous_prefer") or "", mr["advice"]),
                      why=[x.strip()[2:] for x in why_lines(mr["table"], n.get("previous_prefer") or "")] if mr else [])
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], note=q["note"], note_body=q.get("note_body", ""),
                quick=q["advice"], memory=mem_public(mr) if mr else None, table=mr["table"] if mr else None,
                why=[x.strip()[2:] for x in why_lines(mr["table"], q["advice"])] if mr else [],
                agree=bool(mr) and agrees(q["advice"], mr["advice"]), before=before)


def cohort_text(cli: CLI, pid: str, family: str, echo: bool = True) -> dict:
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    tb = cohort_table(mem, family, story)
    pick, said = decide(tb)
    return dict(table=tb, advice=pick, said=said)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find_project(cli, story["project"]["key"], echo=not quiet)
    if not proj:
        note("nothing to delete")
        emit("cleaned", "")
        return
    facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
    for f in facts:
        cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
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
    banner(2, TOTAL, "Set up — one project for hiring sources")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: hires, outcomes, channel notes")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the closest channel note, and its advice")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — every channel at the same follow-up time")
    rows = memory_review(cli, pid, story, quick)
    banner(6, TOTAL, "Write it back — the note from the follow-up, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   note={q["ticket"]["id"]: [q["note"], q.get("note_rank")] for q in quick},
                   quick={r["ticket"]["id"]: r["quick"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["advice"] for r in rows},
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["note"], a["note_rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def print_review(story: dict, r: dict, family: str) -> None:
    lines = [f"\n  “{r['ticket']['text']}” · {family} — top {len(r['hits'])}:"]
    lines += [hit_line(story, h, "   ← closest note" if h["record"] == r["note"] else "") for h in r["hits"]]
    if r["note"]:
        lines.append(f"  → {r['note']}: “{clip(r['note_body'], 170)}”")
    lines.append(f"  → quick read:  {r['quick'] or '?'}")
    if r["memory"]:
        lines += table_lines(r["table"], r["quick"], r["memory"]["advice"])
        lines += [f"  · {w}" for w in r["why"]]
        lines.append(f"  → memory read: {r['memory']['said']}  {'✓ same' if r['agree'] else '✗ differs'}")
    b = r.get("before")
    if b:
        lines.append(f"\n  {r['note']} before an earlier run rewrote it: “{clip(b['note_body'], 170)}”")
        lines.append(f"  → that text's quick read: {b['quick']}  {'✓ same' if b['agree'] else '✗ differs'} from the memory read")
        lines += [f"  · {w}" for w in b["why"]]
    emit("text", "\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "review", "cohort", "cleanup"],
                    help="run = full demo (default); review \"SOURCING QUESTION\" --family backend|data = the quick read "
                         "and the memory read of any sourcing question; cohort backend|data = every hire in that role "
                         "family with what happened to them, and each channel at the same follow-up times; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the sourcing question (review) or the role family (cohort)")
    ap.add_argument("--family", default="backend", choices=["backend", "data"], help="review: the req's role family")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("review", "cohort") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. review \"Which channel for a senior backend engineer?\" --family "
                 "backend, or cohort backend")
    if a.command == "cohort" and a.args[0] not in story["families"]:
        ap.error(f"cohort takes one of: {', '.join(story['families'])}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py cohort backend`, `python3 demo.py review \"Is Ferrous Guild worth a paid "
              "sponsorship for backend hiring?\" --family backend`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "review":
        banner(2, 2, "Review — the quick read and the memory read")
        print_review(story, review_text(cli, pid, a.args[0], a.family), a.family)
    elif a.command == "cohort":
        banner(2, 2, f"Cohort — every {a.args[0]} hire, and each channel at the same follow-up time")
        c = cohort_text(cli, pid, a.args[0])
        tb = c["table"]
        lines = [f"\n  {len(tb['hires'])} {a.args[0]} hire(s), as of {story['today']}:",
                 f"    {'id':<6} {'started':<10} {'channel':<20} {'role':<25} what happened"]
        for h in tb["hires"]:
            lines.append(f"    {h['hire']:<6} {h['start']:<10} {h['channel']:<20} {h['role']:<25} {h['status']}")
        lines.append("")
        lines += table_lines(tb, "", c["advice"])
        for r in tb["rows"]:
            if r["unproven"]:
                lines.append(f"  · {r['channel']}: {r['at'][str(tb['horizon'])][1]} of {r['hired']} hires past day "
                             f"{tb['horizon']}; readable on {r['readable_on']}")
        lines.append(f"  → {c['said']}")
        emit("text", "\n".join(lines))


def and_list(xs: list[str]) -> str:
    if len(xs) < 2:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


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

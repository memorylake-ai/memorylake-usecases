#!/usr/bin/env python3
"""
Interview feedback memory for hiring teams — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Harborline (fictional) is hiring a Senior Backend Engineer.  Four people
interview: Dana scores hard, Eli scores generously, Farah and Gus sit in
between.  Over the last year they scored twelve candidates; eight were hired
and seven of those have had their six-month review.  Every scorecard and every
review outcome is one fact in MemoryLake, pinned word for word, with the
candidate, interviewer, score and date as metadata.

Today the debrief picks between two finalists.  Average the scores and it is
Kai (3.75, from Eli and Gus) over Lena (3.25, from Dana and Farah).  But a 3.5
from Dana is one of the highest scores she has given; a 4 from Eli is what he
gives most people.  Read each score against that interviewer's own history and
Lena is further above her interviewers' bar (+0.70) than Kai is above his
(+0.28).  Then check the history against what happened: when Dana or Farah
scored a hire above their own average, every one of those hires met or exceeded
expectations at six months; when Eli did, half did not.  The calibration is
written back as memory on each interviewer (a fact on their actor), and when a
review arrives today — a hire Eli and Gus scored high was let go — it is
rewritten, and `fact trace` shows both versions.

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
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ifm"  # memorylake-usecases / interview-feedback-memory


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


def sc(x: float) -> str:
    """A score as people write it: 3, 3.5."""
    return f"{x:g}"


def people(story: dict) -> dict[str, dict]:
    return {i["key"]: i for i in story["interviewers"]}


def candidates(story: dict) -> dict[str, dict]:
    return {c["id"]: c for c in story["candidates"]}


def first(name: str) -> str:
    return name.split()[0]


def card_text(story: dict, c: dict) -> str:
    who, cand = people(story)[c["interviewer"]], candidates(story)[c["candidate"]]
    return (f"Scorecard: {who['name']} on {cand['name']} ({c['candidate']}, {story['role']}, onsite {c['date']}): "
            f"{sc(c['score'])} of 4. {c['note'][0].upper() + c['note'][1:]}.")


def card_meta(c: dict) -> dict:
    return dict(kind="scorecard", key=f"scorecard:{c['candidate']}:{c['interviewer']}", candidate=c["candidate"],
                interviewer=c["interviewer"], score=c["score"], date=c["date"])


def outcome_text(story: dict, o: dict) -> str:
    cand = candidates(story)[o["candidate"]]
    return (f"Six-month review: {cand['name']} ({o['candidate']}), hired after the {cand['onsite']} onsite, "
            f"{o['outcome']} (reviewed {o['reviewed']}).")


def outcome_meta(o: dict) -> dict:
    return dict(kind="outcome", key=f"outcome:{o['candidate']}", candidate=o["candidate"], outcome=o["outcome"],
                reviewed=o["reviewed"])


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


def list_facts(cli: CLI, projects: list[str] = (), actors: list[str] = (), query: str | None = None,
               echo: bool = True) -> list[dict]:
    args = ["fact", "list"]
    if actors:
        args += ["--actors", ",".join(actors)]
    if projects:
        args += ["--projects", ",".join(projects)]
    if query:
        args += ["--query", query]
    return pages(cli, args, echo=echo)


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_actor(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("actor", "get", cid(key), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def entries(facts: list[dict]) -> dict:
    """The pinned facts by kind, metadata flattened in: scorecards and outcomes by key, calibrations by interviewer."""
    out = dict(scorecards={}, outcomes={}, calibrations={})
    for f in facts:
        m = f.get("metadata") or {}
        row = dict(m, id=f["id"], text=f.get("fact"))
        if m.get("kind") == "scorecard":
            out["scorecards"][m["key"]] = row
        elif m.get("kind") == "outcome":
            out["outcomes"][m["candidate"]] = row
        elif m.get("kind") == "calibration":
            out["calibrations"][m["interviewer"]] = row
    return out


def read_all(cli: CLI, ids: dict, echo: bool = True) -> dict:
    """Everything in one call: `fact list --actors` (the interviewers) and `--projects` (scorecards, outcomes)
    together return both scopes' facts, every page."""
    return entries(list_facts(cli, projects=[ids["scorecards"], ids["outcomes"]],
                              actors=[ids["actors"][k] for k in sorted(ids["actors"])], echo=echo))


# --------------------------------------------------------------------------- the arithmetic (plain code, no model)

def calibrate(story: dict, mem: dict, before: str) -> dict:
    """Each interviewer's average over past candidates, and — for hires they scored above that average —
    what the six-month reviews said. Only reviews dated before `before` count."""
    cands = candidates(story)
    past = [c for c in mem["scorecards"].values() if not cands[c["candidate"]]["finalist"]]
    outs = {k: o for k, o in mem["outcomes"].items() if o["reviewed"] < before}
    panel = sum(c["score"] for c in past) / len(past)
    rows = {}
    for p in story["interviewers"]:
        mine = [c for c in past if c["interviewer"] == p["key"]]
        mean = sum(c["score"] for c in mine) / len(mine)
        above = sorted((c for c in mine if c["score"] > mean and c["candidate"] in outs), key=lambda c: c["candidate"])
        tally = {}
        for c in above:
            o = outs[c["candidate"]]["outcome"]
            tally[o] = tally.get(o, 0) + 1
        good = sum(n for o, n in tally.items() if o in story["good"])
        rows[p["key"]] = dict(interviewer=p["key"], name=p["name"], n=len(mine), mean=round(mean, 2),
                              vs_panel=round(mean - panel, 2), above=[c["candidate"] for c in above], tally=tally,
                              good=good, total=len(above),
                              detail=[f"{c['candidate']} {sc(c['score'])} → {outs[c['candidate']]['outcome']}" for c in above])
    return dict(panel=round(panel, 2), rows=rows, reviews=len(outs), before=before)


def tally_text(story: dict, t: dict) -> str:
    order = ["exceeds", "meets", "below", "let go"]
    return " · ".join(f"{o} {t[o]}" for o in order if t.get(o)) or "none yet"


def finalists(story: dict, mem: dict, cal: dict) -> list[dict]:
    out = []
    for cand in (c for c in story["candidates"] if c["finalist"]):
        cards = sorted((c for c in mem["scorecards"].values() if c["candidate"] == cand["id"]),
                       key=lambda c: c["interviewer"])
        raw = sum(c["score"] for c in cards) / len(cards)
        parts = [dict(interviewer=c["interviewer"], score=c["score"], mean=cal["rows"][c["interviewer"]]["mean"],
                      delta=round(c["score"] - cal["rows"][c["interviewer"]]["mean"], 2)) for c in cards]
        good = sum(cal["rows"][c["interviewer"]]["good"] for c in cards)
        total = sum(cal["rows"][c["interviewer"]]["total"] for c in cards)
        out.append(dict(id=cand["id"], name=cand["name"], raw=round(raw, 2), parts=parts,
                        calibrated=round(sum(p["delta"] for p in parts) / len(parts), 2), good=good, total=total))
    return out


def calibration_text(story: dict, r: dict, day: str, reviews: int) -> str:
    return (f"Calibration for {r['name']} as of {day} ({r['n']} scorecards): average "
            f"{r['mean']:.2f} of 4, {r['vs_panel']:+.2f} against the panel. Hires {first(r['name'])} scored above "
            f"that average: {r['good']} of {r['total']} met or exceeded expectations at six months "
            f"({'; '.join(r['detail']) or 'none reviewed yet'}).")


def calibration_meta(r: dict, day: str, reviews: int) -> dict:
    return dict(kind="calibration", key=f"calibration:{r['interviewer']}", interviewer=r["interviewer"], as_of=day,
                reviews=reviews, n=r["n"], mean=r["mean"], vs_panel=r["vs_panel"], above_good=r["good"],
                above_total=r["total"], above=r["above"])


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = dict(actors={})
    for key in ("scorecards_project", "outcomes_project"):
        p = story[key]
        proj = find_project(cli, p["key"])
        if proj is None:
            proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                           "--description", p["description"], scoped=True)
            note(f"created “{p['name']}” → {proj['id']}")
        else:
            note(f"“{p['name']}” already exists → {proj['id']} (pass --reset to start over)")
        ids[p["key"]] = proj["id"]
    for p in story["interviewers"]:
        a = find_actor(cli, p["key"])
        if a is None:
            a = cli.run("actor", "create", "--custom-id", cid(p["key"]), "--display-name", p["name"],
                        "--description", f"{p['title']}, Harborline interview panel (demo: interview-feedback-memory)")
            note(f"created interviewer {p['name']} → {a['id']}")
        else:
            note(f"interviewer {p['name']} already exists → {a['id']}")
        try:
            cli.run("actor", "bind", "--actor", a["id"], scoped=True, echo=p is story["interviewers"][0])
        except CLIError as e:
            if not e.has("already", "409", "conflict"):
                raise
        ids["actors"][p["key"]] = a["id"]
    note("each interviewer is an actor: their calibration — how they score, and what their scores turned out to "
         "mean — is memory about that person, kept on them")
    emit("setup", "", ids=ids, interviewers=story["interviewers"])
    return ids


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, scope: tuple[str, str], live: dict, items: list[tuple[str, dict]], what: str,
        echo: bool = True) -> list[str]:
    """Pin the entries the scope does not hold yet (matched by metadata key) — one `fact add` for all of
    them, then `fact update --metadata` on each (fact add takes no metadata). Returns the new fact ids."""
    missing = [(t, m) for t, m in items if m["key"] not in live]
    new = []
    if missing:
        res = cli.run("fact", "add", scope[0], scope[1], *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, scope[0], scope[1], "--metadata", json.dumps(meta), scoped=True,
                    echo=echo and i == 0)
            new.append(fid)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way ({what})")
    return new


def live_keys(facts: list[dict]) -> dict:
    return {(f.get("metadata") or {}).get("key"): f for f in facts if (f.get("metadata") or {}).get("key")}


def intake(cli: CLI, ids: dict, story: dict) -> dict:
    day = story["today"]
    note("one fact per scorecard (candidate, interviewer, score of 4, date) and one per six-month review, word for "
         "word, each with metadata — the reviews dated today arrive in step 6")
    live = live_keys(list_facts(cli, projects=[ids["scorecards"]], echo=False))
    n1 = pin(cli, ("--project", ids["scorecards"]), live, [(card_text(story, c), card_meta(c)) for c in story["scorecards"]],
             "candidate, interviewer, score, date")
    live = live_keys(list_facts(cli, projects=[ids["outcomes"]], echo=False))
    n2 = pin(cli, ("--project", ids["outcomes"]), live,
             [(outcome_text(story, o), outcome_meta(o)) for o in story["outcomes"] if o["reviewed"] < day],
             "candidate, outcome, reviewed")
    if not n1 and not n2:
        note("the scorecards and reviews were pinned by an earlier run; reading them back")
    mem = read_all(cli, ids)
    cands, ppl = candidates(story), people(story)
    lines = [f"\n  {len(mem['scorecards'])} scorecards · {sum(1 for o in mem['outcomes'].values() if o['reviewed'] < day)} "
             f"six-month reviews (before today, {day})\n",
             f"    {'':<4} {'candidate':<17} {'onsite':<11} " + "".join(f"{first(p['name']):>7}" for p in story["interviewers"])
             + "   outcome"]
    for c in story["candidates"]:
        row = {s["interviewer"]: s["score"] for s in mem["scorecards"].values() if s["candidate"] == c["id"]}
        o = mem["outcomes"].get(c["id"])
        o = o["outcome"] if o and o["reviewed"] < day else ("finalist" if c["finalist"] else
                                                             "hired, review due" if c["hired"] else "not hired")
        if c["finalist"] and c is next(x for x in story["candidates"] if x["finalist"]):
            lines.append("")
        lines.append(f"    {c['id']:<4} {c['name']:<17} {c['onsite']:<11} "
                     + "".join(f"{sc(row[k]) if k in row else '·':>7}" for k in ppl) + f"   {o}")
    emit("text", "\n".join(lines))
    emit("intake", "", scorecards=list(mem["scorecards"].values()),
         outcomes=[o for o in mem["outcomes"].values() if o["reviewed"] < day], candidates=story["candidates"],
         interviewers=story["interviewers"])
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_read(cli: CLI, ids: dict, story: dict) -> dict:
    note("what a summary tool does: pull each finalist's scorecards (`fact list --query <name>`, an exact substring "
         "match) and average the scores")
    rows = []
    for cand in (c for c in story["candidates"] if c["finalist"]):
        hits = [entries([f])["scorecards"] for f in list_facts(cli, projects=[ids["scorecards"]], query=cand["name"])]
        cards = sorted((v for h in hits for v in h.values()), key=lambda c: c["interviewer"])
        avg = sum(c["score"] for c in cards) / len(cards)
        rows.append(dict(id=cand["id"], name=cand["name"], avg=round(avg, 2),
                         cards=[dict(interviewer=c["interviewer"], score=c["score"], text=c["text"]) for c in cards]))
        emit("text", "\n".join([f"\n    {cand['name']} — {len(cards)} scorecards"]
                               + [f"      {sc(c['score']):>3}  {clip(c['text'], 108)}" for c in cards]
                               + [f"      average {avg:.2f}"]))
    win = max(rows, key=lambda r: r["avg"])
    other = min(rows, key=lambda r: r["avg"])
    emit("text", f"\n  → “{first(win['name'])} is the stronger candidate”: {win['avg']:.2f} vs {other['avg']:.2f} — "
                 f"every scorecard read, the arithmetic right, and the scores treated as one scale")
    out = dict(rows=rows, winner=win["id"])
    emit("quick_read", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: calibrate

def show_calibration(story: dict, cal: dict, prev: dict | None = None) -> None:
    lines = [f"\n    {'interviewer':<14} {'scorecards':>10} {'average':>8} {'vs panel':>9}   hires they scored above "
             f"their own average → six-month review"]
    for p in story["interviewers"]:
        r = cal["rows"][p["key"]]
        q = (prev or {}).get(p["key"])
        rec = f"{r['good']}/{r['total']} met or exceeded"
        if q and (q["good"], q["total"]) != (r["good"], r["total"]):
            rec = f"{q['good']}/{q['total']} → {rec}"
        lines.append(f"    {p['name']:<14} {r['n']:>10} {r['mean']:>8.2f} {r['vs_panel']:>+9.2f}   {rec:<28} "
                     f"({'; '.join(r['detail']) or '-'})")
    lines.append(f"    {'panel':<14} {'':>10} {cal['panel']:>8.2f}")
    emit("text", "\n".join(lines))


def show_finalists(story: dict, rows: list[dict]) -> None:
    ppl = people(story)
    lines = [f"\n    {'finalist':<14} {'average':>7}   each score against that interviewer's own average"
             f"{'':<14} {'vs own bar':>10}   backers' record"]
    for f in rows:
        parts = ", ".join(f"{first(ppl[p['interviewer']]['name'])} {sc(p['score'])} ({p['delta']:+.2f} vs {p['mean']:.2f})"
                          for p in f["parts"])
        lines.append(f"    {f['name']:<14} {f['raw']:>7.2f}   {parts:<63} {f['calibrated']:>+10.2f}   "
                     f"{f['good']}/{f['total']}")
    emit("text", "\n".join(lines))


def calibrate_step(cli: CLI, ids: dict, story: dict, quick: dict) -> dict:
    day = story["today"]
    note("read everything once — both projects and all four interviewers in one `fact list` — then plain code over the "
         "metadata: each interviewer's own average, and what happened to the hires they scored above it")
    mem = read_all(cli, ids)
    cal = calibrate(story, mem, day)
    show_calibration(story, cal)
    rows = finalists(story, mem, cal)
    emit("text", "\n  the same four finalist scorecards, each read against the interviewer who wrote it:")
    show_finalists(story, rows)
    best = max(rows, key=lambda r: r["calibrated"])
    raw = next(r for r in rows if r["id"] == quick["winner"])
    emit("text", f"\n  → by average: {first(raw['name'])} ({raw['raw']:.2f}). Against each interviewer's own bar: "
                 f"{first(best['name'])} ({best['calibrated']:+.2f}). {first(best['name'])}'s interviewers' high scores "
                 f"held up {best['good']}/{best['total']}; {first(raw['name'])}'s, "
                 f"{raw['good']}/{raw['total']}.\n    ({cal['reviews']} reviewed hires in all: a signal for the debrief, not a verdict.)")

    note("write the calibration back as memory on each interviewer (`fact add --actor`, then `--metadata`)")
    written, kept = [], []
    for p in story["interviewers"]:
        r, aid = cal["rows"][p["key"]], ids["actors"][p["key"]]
        have = mem["calibrations"].get(p["key"])
        if have:
            kept.append(p["key"])
            continue
        new = pin(cli, ("--actor", aid), {}, [(calibration_text(story, r, day, cal["reviews"]),
                                              calibration_meta(r, day, cal["reviews"]))], "calibration",
                  echo=not written)
        written.append(dict(interviewer=p["key"], id=new[0]))
    if written:
        note(f"wrote {len(written)} calibration fact(s), one per interviewer")
    if kept:
        note(f"{len(kept)} interviewer(s) already carry a calibration from an earlier run (step 6 shows today's version)")
    mem = read_all(cli, ids, echo=False)
    eli = mem["calibrations"].get("eli")
    if eli:
        emit("text", f"\n    on {people(story)['eli']['name']}: “{eli['text']}”")
    out = dict(calibration=cal, finalists=rows, winner=best["id"], raw_winner=raw["id"], written=written,
               calibrations={k: v["text"] for k, v in mem["calibrations"].items()})
    emit("calibrated", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: a review arrives today

def tail(text: str | None) -> str:
    """The part of a calibration fact that changes: from "Hires … scored above" on."""
    t = text or ""
    i = t.find("Hires ")
    return t[i:] if i >= 0 else t


def show_trace(cli: CLI, ids: dict, story: dict, who: str, fact_id: str, echo: bool = True) -> list[dict]:
    tr = cli.run("fact", "trace", fact_id, "--actor", ids["actors"][who], scoped=True, echo=echo) or {}
    events = list(reversed(tr.get("trace") or []))
    lines = [f"\n    `fact trace` — every version of {people(story)[who]['name']}'s calibration, oldest first:"]
    for e in events:
        lines.append(f"      {str(e.get('event')):<6} {str(e.get('timestamp'))[:19]}Z  {e.get('source_kind')}")
        if e.get("old_fact"):
            lines.append(f"        was: …{clip(tail(e.get('old_fact')), 220)}")
        lines.append(f"        now: …{clip(tail(e.get('new_fact')), 220)}")
    emit("text", "\n".join(lines))
    return [dict(event=e.get("event"), timestamp=e.get("timestamp"), source=e.get("source_kind"),
                 old=e.get("old_fact"), new=e.get("new_fact")) for e in events]


def today_step(cli: CLI, ids: dict, story: dict, before: dict) -> dict:
    day = story["today"]
    todays = [o for o in story["outcomes"] if o["reviewed"] >= day]
    mem = read_all(cli, ids, echo=False)
    for o in todays:
        cand = candidates(story)[o["candidate"]]
        cards = sorted((c for c in mem["scorecards"].values() if c["candidate"] == o["candidate"]), key=lambda c: c["interviewer"])
        emit("text", f"\n  {cand['name']} ({o['candidate']}), hired after the {cand['onsite']} onsite — six-month review "
                     f"today: {o['outcome']}\n    scored by " + ", ".join(
                         f"{people(story)[c['interviewer']]['name']} {sc(c['score'])}" for c in cards))
    new = pin(cli, ("--project", ids["outcomes"]), live_keys(list_facts(cli, projects=[ids["outcomes"]], echo=False)),
              [(outcome_text(story, o), outcome_meta(o)) for o in todays], "candidate, outcome, reviewed")
    if not new:
        note("today's review was pinned by an earlier run; reading it back")
    mem = read_all(cli, ids)
    cal = calibrate(story, mem, "9999-12-31")
    show_calibration(story, cal, before["calibration"]["rows"])

    note("rewrite the calibration facts that changed (`fact update --text`, the old values kept in metadata.previous); "
         "the others stay as they are")
    changed, same = [], []
    for p in story["interviewers"]:
        r, aid = cal["rows"][p["key"]], ids["actors"][p["key"]]
        have = mem["calibrations"].get(p["key"])
        text = calibration_text(story, r, day, cal["reviews"])
        if not have:
            die(f"{p['name']} has no calibration fact; run `python3 demo.py --reset`")
        if have["text"] == text:
            same.append(p["key"])
            continue
        prev = {k: have.get(k) for k in ("as_of", "reviews", "mean", "above_good", "above_total", "above")}
        meta = dict(calibration_meta(r, day, cal["reviews"]), previous=prev)
        cli.run("fact", "update", have["id"], "--actor", aid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=not changed)
        changed.append(dict(interviewer=p["key"], id=have["id"], before=f"{prev['above_good']}/{prev['above_total']}",
                            after=f"{r['good']}/{r['total']}"))
    emit("text", "\n" + "\n".join(
        [f"    ✎ {people(story)[c['interviewer']]['name']}: {c['before']} → {c['after']} met or exceeded "
         f"(fact {c['id']}, same id)" for c in changed]
        + [f"    = {people(story)[k]['name']}: unchanged" for k in same]))
    if not changed:
        note("the calibrations already reflect today's review (rewritten by an earlier run)")

    rows = finalists(story, mem, cal)
    show_finalists(story, rows)
    mem = read_all(cli, ids, echo=False)
    eli = mem["calibrations"]["eli"]
    trace = show_trace(cli, ids, story, "eli", eli["id"])
    ok = len(trace) >= 2 and trace[0]["event"] == "ADD" and trace[-1]["event"] == "UPDATE"
    emit("text", f"\n    {'✓' if ok else '✗'} the calibration is one fact with history ({' → '.join(t['event'] or '?' for t in trace)}), "
                 f"not a number recomputed and thrown away")
    out = dict(day=day, calibration=cal, finalists=rows, changed=changed, same=same, trace=trace,
               pinned=[o["candidate"] for o in todays] if new else [])
    emit("today", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: interviewer and ask

def interviewer(cli: CLI, ids: dict, story: dict, who: str, echo: bool = True) -> dict:
    mem = read_all(cli, ids, echo=echo)
    p = people(story)[who]
    cal = mem["calibrations"].get(who)
    cands = candidates(story)
    cards = sorted((c for c in mem["scorecards"].values() if c["interviewer"] == who), key=lambda c: c["date"])
    rows = [dict(candidate=c["candidate"], name=cands[c["candidate"]]["name"], score=c["score"], date=c["date"],
                 outcome=(mem["outcomes"].get(c["candidate"]) or {}).get("outcome"), text=c["text"]) for c in cards]
    trace = show_trace(cli, ids, story, who, cal["id"], echo=echo) if cal else []
    return dict(interviewer=who, name=p["name"], calibration=cal, cards=rows, trace=trace)


def show_interviewer(r: dict) -> None:
    lines = [f"\n  {r['name']}'s scorecards ({len(r['cards'])}):"]
    for c in r["cards"]:
        lines.append(f"    {c['date']}  {c['candidate']} {c['name']:<17} {sc(c['score']):>3}  {c['outcome'] or ''}")
    if r["calibration"]:
        lines.append(f"\n  calibration on the actor: “{r['calibration']['text']}”")
    emit("text", "\n".join(lines))


def ask(cli: CLI, ids: dict, q: str, echo: bool = True) -> dict:
    """Any question over both projects and the interviewers: search's top 8, each hit labelled by kind."""
    mem = read_all(cli, ids, echo=False)
    by_id = {v["id"]: v for d in mem.values() for v in d.values()}
    res = cli.run("search", q, "--projects", f"{ids['scorecards']},{ids['outcomes']}",
                  "--actors", ",".join(ids["actors"][k] for k in sorted(ids["actors"])), "--types", "fact",
                  "--top-k", "8", scoped=True, echo=echo) or {}
    hits = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = by_id.get(f.get("id")) or {}
        hits.append(dict(rank=f.get("rank") or i, id=f.get("id"), kind=m.get("kind"), text=f.get("fact")))
    return dict(q=q, hits=hits)


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    def facts() -> list:
        return pages(cli, ["fact", "list", "--actors", actor_id], echo=False)

    try:
        found = facts()
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        # The binding takes a moment to show: `fact list` can still answer 404 right after it.
        for wait in (1, 2, 4, 8, 0):
            try:
                found = facts()
                break
            except CLIError as e:
                if not e.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    for f in found:
        cli.run("fact", "delete", "--actor", actor_id, f["id"], scoped=True, echo=False)
    left = facts()
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id)
    return len(found)


def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    n = 0
    for p in story["interviewers"]:
        a = find_actor(cli, p["key"], echo=False)
        if a:
            n += delete_actor(cli, a["id"])
    note(f"deleted the four interviewer actors and their {n} calibration fact(s) (facts first, then the actor)")
    for key in ("scorecards_project", "outcomes_project"):
        proj = find_project(cli, story[key]["key"], echo=not quiet)
        if proj:
            for f in list_facts(cli, projects=[proj["id"]], echo=False):
                cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
            left = list_facts(cli, projects=[proj["id"]], echo=False)
            if left:
                die(f"{len(left)} fact(s) still on {proj['id']} after deleting; run cleanup again")
            cli.run("proj", "delete", proj["id"], scoped=True)
    note("deleted the scorecard and outcome projects and every fact on them")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — a project for scorecards, one for hire outcomes, an actor per interviewer")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Intake — 40 scorecards and 7 six-month reviews, one fact each")
    intake(cli, ids, story)
    banner(4, TOTAL, "The quick read — average each finalist's scores")
    quick = quick_read(cli, ids, story)
    banner(5, TOTAL, "Calibrate — each score against its interviewer's history and what their hires became")
    cal = calibrate_step(cli, ids, story, quick)
    banner(6, TOTAL, f"A six-month review arrives today ({story['today']}) — the calibration is rewritten, with history")
    today = today_step(cli, ids, story, cal)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   average={r["id"]: r["avg"] for r in quick["rows"]}, average_winner=quick["winner"],
                   calibrated={r["id"]: r["calibrated"] for r in cal["finalists"]}, calibrated_winner=cal["winner"],
                   record={r["id"]: f"{r['good']}/{r['total']}" for r in today["finalists"]},
                   changed=[c["interviewer"] for c in today["changed"]],
                   trace=[t["event"] for t in today["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    ids = dict(actors={})
    for key in ("scorecards_project", "outcomes_project"):
        proj = find_project(cli, story[key]["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[story[key]["key"]] = proj["id"]
    for p in story["interviewers"]:
        a = find_actor(cli, p["key"], echo=False)
        if a is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids["actors"][p["key"]] = a["id"]
    return ids


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "finalists", "interviewer", "ask", "cleanup"],
                    help="run = full demo (default); finalists = both readings of today's finalists; interviewer NAME = "
                         "one interviewer's scorecards, calibration and its history; ask \"QUESTION\" = search's top 8 "
                         "over scorecards, reviews and calibrations; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the interviewer (dana, eli, farah, gus) or the question")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("interviewer", "ask") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. interviewer eli  /  ask \"Who scored Kai?\"")
    if a.command == "interviewer" and a.args[0].lower() not in people(story):
        ap.error(f"unknown interviewer {a.args[0]!r}; this demo has: {', '.join(people(story))}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py interviewer eli`, `python3 demo.py finalists` or "
              "`python3 demo.py ask \"Who scored Kai Brennan?\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "finalists":
        banner(2, 2, "Today's finalists — the average, and each score against its interviewer")
        mem = read_all(cli, ids)
        cal = calibrate(story, mem, "9999-12-31")
        show_calibration(story, cal)
        show_finalists(story, finalists(story, mem, cal))
    elif a.command == "interviewer":
        who = a.args[0].lower()
        banner(2, 2, f"{people(story)[who]['name']} — scorecards, calibration, and how it changed")
        show_interviewer(interviewer(cli, ids, story, who))
    elif a.command == "ask":
        banner(2, 2, "Ask — search's top 8 over scorecards, reviews and calibrations")
        r = ask(cli, ids, a.args[0])
        lines = [f"\n  “{r['q']}” — search top {len(r['hits'])}:"]
        for h in r["hits"]:
            lines.append(f"    {h['rank']:>2}  {(h['kind'] or '?'):<11} {clip(h['text'], 110)}")
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

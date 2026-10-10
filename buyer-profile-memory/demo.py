#!/usr/bin/env python3
"""
Buyer profile memory for a real estate team — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Harborline Realty (fictional) keeps one memory per buyer. Lena Park's profile
(dealbreakers, budget, financing, preferences) is pinned on her own actor;
every showing, with how she reacted, goes into the team project, which every
agent covering her reads.

Priya Shah ran the kickoff on 2026-09-02: no cul-de-sac, 800,000 dollars as a
hard ceiling, FHA loan, quiet street. Tom Becker covered four showings while
Priya was away, and after the second round wrote two call notes on Lena's
profile: a cul-de-sac is fine, the budget is 850,000.

Those notes contradict the kickoff. MemoryLake checks every fact written to an
actor against what that actor already holds, and raises a conflict when the two
cannot both be true (`fact conflict list --actor`). Today Maya Ortiz takes Lena
over. Her tour screen marks the two listings that depend on the disputed rules
as "ask first". The showing history shows what Lena responded to, as opposed to
what she said. Maya resolves both conflicts with `keep_fact`, citing the showings,
and the screen changes. The resolved conflicts keep the forgotten kickoff notes
word for word, as the record of what changed and why.

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

PREFIX = "mlu-bpm"  # memorylake-usecases / buyer-profile-memory

PROJECT = dict(
    custom_id=f"{PREFIX}-team",
    name="Harborline Realty — buyers",
    description="Showing history every agent on the team reads. Demo data.",
)

# The detector checks a write right after it lands, if the scope has been quiet. Back-to-back writes
# are checked minutes later, as one batch (measured: 6–8 min). So every write waits until the
# previous one is GAP seconds old.
GAP = 15
DETECT_WAIT = 150    # upper bound for the detector to answer the call notes (usually 15–30 s)
SETTLE = 30          # and nothing is called "not raised" until the last write is this old


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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
    TRANSIENT = ("could not connect", "tls handshake", "connection reset", "timed out",
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504")

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
        retryable = any(str(a) in self.READ_ONLY for a in args[:3])
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

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False):
        try:
            return self.run(*args, scoped=scoped)
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
        items = (cli.run("ws", "list", "--page-size", "1") or {}).get("items") or []
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
    return json.loads((DATA / "buyer.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def money(v) -> str:
    return f"{int(v):,}"


def agent_name(story: dict, key: str) -> str:
    return story["agents"][key]["name"]


def profile_entries(story: dict) -> list[dict]:
    """Every profile statement in the story, with who said it and when (kickoff first, then call notes)."""
    out = []
    for source, block in (("kickoff", story["kickoff"]), ("call notes", story["notes"])):
        for f in block["facts"]:
            out.append(dict(f, agent=agent_name(story, block["agent"]), said_on=block["date"], source=source))
    return out


RULE_KEYS = ("forbid", "allow", "max", "prefer")


def profile_meta(e: dict) -> dict:
    meta = dict(kind=e["kind"], field=e["field"], label=e["label"], agent=e["agent"], said_on=e["said_on"], source=e["source"])
    meta.update(e["rule"])
    return meta


def showing_meta(s: dict, story: dict) -> dict:
    return dict(event="showing", listing=s["listing"], date=s["date"], agent=agent_name(story, s["agent"]),
                street_type=s["street_type"], price=s["price"], traffic=s["traffic"], condition=s["condition"],
                reaction=s["reaction"])



# --------------------------------------------------------------------------- pacing

class Pace:
    """Keeps writes GAP seconds apart, so the detector checks each one as it lands."""

    def __init__(self) -> None:
        self.last = 0.0

    def wait(self) -> None:
        left = GAP - (time.time() - self.last)
        if left > 0:
            time.sleep(left)

    def wrote(self) -> None:
        self.last = time.time()


PACE = Pace()


# --------------------------------------------------------------------------- scopes

def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def find_buyer(cli: CLI, story: dict, echo: bool = True):
    try:
        return cli.run("actor", "get", cid(story["buyer"]["slug"]), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def _pages(cli: CLI, args: list[str], echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        page = cli.run(*args, "--page-size", "100", *(["--continuation-token", token] if token else []),
                       scoped=True, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "list", flag, scope_id], echo)


def list_conflicts(cli: CLI, buyer_id: str, *extra: str, echo: bool = True) -> list[dict]:
    return _pages(cli, ["fact", "conflict", "list", "--actor", buyer_id, *extra], echo)


def ensure_project(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created the team project “{proj['name']}” → {proj['id']}")
    else:
        note(f"team project already exists → {proj['id']} (pass --reset to start over)")
    return proj["id"]


def ensure_buyer(cli: CLI, story: dict) -> str:
    b = story["buyer"]
    actor = find_buyer(cli, story)
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(b["slug"]), "--display-name", b["name"],
                        "--description", b["description"], "--tags", "buyer,stage:touring")
        note(f"created buyer {b['name']} → {actor['id']}")
    else:
        note(f"buyer {b['name']} already exists → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"]


def forgotten_texts(cli: CLI, buyer_id: str) -> set[str]:
    """Texts of facts an earlier run's resolution forgot: they must not be pinned again on a re-run."""
    out = set()
    for c in list_conflicts(cli, buyer_id, "--resolved", "true", echo=False):
        gone = set((c.get("resolve") or {}).get("forgotten_fact_ids") or [])
        if not gone:
            full = cli.run("fact", "conflict", "get", c["id"], "--actor", buyer_id, scoped=True, echo=False) or {}
            gone = set((full.get("resolve") or {}).get("forgotten_fact_ids") or [])
        out |= {s.get("fact_text") for s in c.get("fact_snapshots") or [] if s.get("fact_id") in gone}
    return out


def pin(cli: CLI, flag: str, scope_id: str, text: str, meta: dict) -> str:
    """`fact add` (verbatim) + `fact update --metadata` (fact add takes no metadata). Writes are paced."""
    PACE.wait()
    res = cli.run("fact", "add", flag, scope_id, text, scoped=True)
    PACE.wrote()
    fid = ((res or {}).get("facts") or [{}])[0].get("id")
    cli.run("fact", "update", fid, flag, scope_id, "--metadata", json.dumps(meta), scoped=True)
    return fid


def ensure_facts(cli: CLI, flag: str, scope_id: str, entries: list[tuple[str, dict]], skip: set[str] = frozenset()) -> dict[str, dict]:
    """Pin each (text, metadata) unless it is there already (or was forgotten by an earlier resolution)."""
    live = {f.get("fact"): f for f in list_facts(cli, flag.replace("--actor", "--actors").replace("--project", "--projects"), scope_id)}
    out = {}
    for text, meta in entries:
        f = live.get(text)
        if f is None and text in skip:
            out[text] = dict(id=None, forgotten=True)
            continue
        if f is None:
            out[text] = dict(id=pin(cli, flag, scope_id, text, meta), new=True)
        else:
            if any((f.get("metadata") or {}).get(k) != v for k, v in meta.items()):
                cli.run("fact", "update", f["id"], flag, scope_id, "--metadata", json.dumps(meta), scoped=True)
            out[text] = dict(id=f["id"], new=False)
    return out


def profile_view(f: dict) -> dict:
    m = f.get("metadata") or {}
    return dict(id=f.get("id"), text=f.get("fact"), kind=m.get("kind"), field=m.get("field"), label=m.get("label"),
                agent=m.get("agent"), said_on=m.get("said_on"), source=m.get("source"),
                rule={k: m[k] for k in RULE_KEYS if k in m})


def read_profile(cli: CLI, buyer_id: str, echo: bool = True) -> list[dict]:
    return [profile_view(f) for f in list_facts(cli, "--actors", buyer_id, echo) if (f.get("metadata") or {}).get("field")]


def read_showings(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    out = []
    for f in list_facts(cli, "--projects", project_id, echo):
        m = f.get("metadata") or {}
        if m.get("event") == "showing":
            out.append(dict(m, id=f.get("id"), text=f.get("fact")))
    return sorted(out, key=lambda s: (s["date"], s["listing"]))


def show_profile(profile: list[dict], header: str) -> None:
    lines = [header]
    for p in sorted(profile, key=lambda p: (p["field"] or "", p["said_on"] or "")):
        lines.append(f"    {p['kind']:<11} {clip(p['text'], 92)}")
        lines.append(f"    {'':<11} {p['agent']}, {p['said_on']} · {p['id']}")
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- the tour screen

def rule_ok(rule: dict, listing: dict, field: str) -> bool:
    v = listing.get(field)
    if "forbid" in rule:
        return rule["forbid"] not in str(v)
    if "max" in rule:
        return int(v) <= int(rule["max"])
    if "prefer" in rule:
        return v == rule["prefer"]
    return True  # "allow"


def field_value(listing: dict, field: str) -> str:
    v = listing.get(field)
    return f"{money(v)} dollars" if field == "price" else f"{v} traffic" if field == "traffic" else str(v)


def screen(profile: list[dict], listing: dict, conflict_of: dict[str, str]) -> dict:
    """Check one listing against every rule in the buyer's memory. Two live facts on one field = disputed."""
    by_field: dict[str, list[dict]] = {}
    for p in profile:
        by_field.setdefault(p["field"], []).append(p)
    rows = []
    for field, facts in by_field.items():
        checks = [(p, rule_ok(p["rule"], listing, field)) for p in facts]
        hard = facts[0]["kind"] != "preference"
        if len(facts) > 1 and len({json.dumps(p["rule"], sort_keys=True) for p in facts}) > 1:
            if all(ok for _, ok in checks):
                mark = "✓"
            elif not any(ok for _, ok in checks):
                mark = "✗" if hard else "!"
            else:
                mark = "?"
        else:
            ok = all(ok for _, ok in checks)
            mark = "✓" if ok else ("✗" if hard else "!")
        rows.append(dict(field=field, value=field_value(listing, field), mark=mark,
                         conflict=next((conflict_of[p["id"]] for p, _ in checks if p["id"] in conflict_of), None),
                         facts=[dict(id=p["id"], label=p["label"], agent=p["agent"], said_on=p["said_on"], ok=ok) for p, ok in checks]))
    marks = [r["mark"] for r in rows]
    verdict = "✗" if "✗" in marks else "?" if "?" in marks else "!" if "!" in marks else "✓"
    return dict(key=listing["key"], listing=listing["listing"], verdict=verdict, rows=rows)


VERDICT = {"✓": "show it", "✗": "skip", "?": "ask Lena first — her memory disagrees", "!": "show, but flag it"}


def show_screen(results: list[dict], header: str) -> None:
    lines = [header]
    for r in results:
        lines.append(f"\n  {r['verdict']}  {r['listing']:<22} → {VERDICT[r['verdict']]}")
        if r["verdict"] == "✓":
            lines.append(f"       ✓ passes all {len(r['rows'])} rules in her profile")
        for row in r["rows"]:
            if row["mark"] == "✓":
                continue
            said = "  vs  ".join(f"{f['agent'].split()[0]} {f['said_on'][5:]}: {f['label']} {'✓' if f['ok'] else '✗'}" for f in row["facts"])
            lines.append(f"       {row['mark']} {row['value']:<32} {said}" + (f"  ({row['conflict']})" if row["conflict"] and row["mark"] == "?" else ""))
    emit("text", "\n".join(lines))


def run_screen(cli: CLI, ids: dict, story: dict, phase: str) -> list[dict]:
    profile = read_profile(cli, ids["buyer"])
    conflict_of = {}
    for c in list_conflicts(cli, ids["buyer"], "--resolved", "false", echo=False):
        for f in c.get("fact_ids") or []:
            conflict_of[f] = c["id"]
    results = [screen(profile, l, conflict_of) for l in story["tour"]]
    show_screen(results, f"\n  Maya's tour for {story['today']}, screened against Lena's memory ({len(profile)} profile facts):")
    emit("screen", "", phase=phase, results=results, profile=profile)
    return results


# --------------------------------------------------------------------------- steps 2–4: profile + showings

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting the team project and the buyer, then starting over")
        cleanup(cli, quiet=True)
    project_id = ensure_project(cli)
    buyer_id = ensure_buyer(cli, story)
    buyers = cli.run("actor", "list", "--tags", "buyer", "--page-size", "100") or {}
    n = len([a for a in buyers.get("items") or [] if (a.get("custom_id") or "").startswith(PREFIX + "-")])
    emit("text", f"\n  Team project {project_id} (showings, read by every agent)"
                 f"\n  Buyer actor  {buyer_id} (her profile) — `actor list --tags buyer` finds {n} buyer(s) from this demo")
    ids = dict(project=project_id, buyer=buyer_id)
    emit("setup", "", ids=ids, buyer=story["buyer"], project=PROJECT["name"])
    return ids


def kickoff(cli: CLI, ids: dict, story: dict) -> None:
    k = story["kickoff"]
    entries = [e for e in profile_entries(story) if e["source"] == "kickoff"]
    skip = forgotten_texts(cli, ids["buyer"])
    note(f"{agent_name(story, k['agent'])}'s kickoff notes ({k['date']}) go onto Lena's own actor, one fact each, "
         f"{GAP}s apart; metadata carries the rule, who said it and when")
    got = ensure_facts(cli, "--actor", ids["buyer"], [(e["text"], profile_meta(e)) for e in entries], skip)
    for e in entries:
        st = got[e["text"]]
        emit("profile_fact", "", key=e["key"], source="kickoff", id=st.get("id"), fact=e["text"], label=e["label"],
             fact_kind=e["kind"], agent=e["agent"], said_on=e["said_on"], forgotten=bool(st.get("forgotten")))
    gone = [e for e in entries if got[e["text"]].get("forgotten")]
    if gone:
        note(f"{len(gone)} kickoff note(s) were forgotten by an earlier run's resolution; not pinned again")
    show_profile(read_profile(cli, ids["buyer"]), f"\n  Lena's profile (`fact list --actors {ids['buyer']}`):")


def showings(cli: CLI, ids: dict, story: dict) -> list[dict]:
    note("showings are events, so they go onto the team project, not onto Lena's profile — "
         "with “she called this cul-de-sac house her favorite” on the profile, the detector stayed quiet about "
         "the cul-de-sac contradiction (0/7 runs; 4/4 without it)")
    ensure_facts(cli, "--project", ids["project"], [(s["text"], showing_meta(s, story)) for s in story["showings"]])
    shown = read_showings(cli, ids["project"])
    lines = [f"\n  Showing history on the team project ({len(shown)} showings):"]
    for s in shown:
        lines.append(f"    {s['date']}  {s['listing']:<18} {s['street_type']:<15} {money(s['price']):>9}  {s['reaction']:<9} ({s['agent']})")
    emit("text", "\n".join(lines))
    emit("showings", "", showings=shown)
    return shown


# --------------------------------------------------------------------------- step 5: call notes + detector

def conflict_view(c: dict, story: dict) -> dict:
    who = {e["text"]: e for e in profile_entries(story)}
    facts = []
    for s in c.get("fact_snapshots") or []:
        e = who.get(s.get("fact_text")) or {}
        facts.append(dict(id=s.get("fact_id"), text=s.get("fact_text"), agent=e.get("agent"), said_on=e.get("said_on"),
                          source=e.get("source"), key=e.get("key"), label=e.get("label")))
    return dict(id=c["id"], name=c.get("name"), description=c.get("description"), category=c.get("category"),
                conflict_type=c.get("conflict_type"), resolved=c.get("resolved"), stale=c.get("stale"),
                fact_ids=c.get("fact_ids") or [], facts=facts, created_at=c.get("created_at"), resolve=c.get("resolve"))


def show_conflicts(conflicts: list[dict], header: str) -> None:
    lines = [header]
    for c in conflicts:
        state = f"resolved ({(c['resolve'] or {}).get('strategy', '…')})" if c["resolved"] else "OPEN"
        lines.append(f"\n  [{c['category']} · {c['conflict_type']}] {c['name']}   {c['id']}  {state}")
        lines.append(f"    {clip(c['description'], 240)}")
        for f in c["facts"]:
            who = f"{f['agent']}, {f['said_on']}" if f["agent"] else "not from this story"
            lines.append(f"    {f['id']}  ({who})")
            lines.append(f"      “{clip(f['text'], 110)}”")
    emit("text", "\n".join(lines))


def call_notes(cli: CLI, ids: dict, story: dict) -> dict:
    n = story["notes"]
    entries = [e for e in profile_entries(story) if e["source"] == "call notes"]
    note(f"{agent_name(story, n['agent'])} writes his call notes ({n['date']}) onto the same actor")
    got = ensure_facts(cli, "--actor", ids["buyer"], [(e["text"], profile_meta(e)) for e in entries])
    for e in entries:
        emit("profile_fact", "", key=e["key"], source="call notes", id=got[e["text"]]["id"], fact=e["text"], label=e["label"],
             fact_kind=e["kind"], agent=e["agent"], said_on=e["said_on"], forgotten=False)
    note_ids = {got[e["text"]]["id"] for e in entries}
    fresh = any(got[e["text"]].get("new") for e in entries)

    def naming(cs):
        return [conflict_view(c, story) for c in cs if note_ids & set(c.get("fact_ids") or [])]

    t0 = time.time()
    if fresh:
        note(f"waiting for MemoryLake's contradiction detector on Lena's actor (up to {DETECT_WAIT}s; "
             f"nothing counts as “not raised” until the last write is {SETTLE}s old)")
        emit("detector_wait", "", seconds=DETECT_WAIT)
        first, found = True, []
        while time.time() - t0 < DETECT_WAIT:
            time.sleep(5)
            found = naming(list_conflicts(cli, ids["buyer"], echo=first))
            first = False
            covered = {f for c in found for f in c["fact_ids"]} & note_ids
            if covered == note_ids and time.time() - PACE.last >= SETTLE:
                break
    else:
        note("the call notes were written by an earlier run; reading what the detector raised then")
        found = naming(list_conflicts(cli, ids["buyer"]))
    waited = int(time.time() - t0)
    others = [conflict_view(c, story) for c in list_conflicts(cli, ids["buyer"], echo=False)
              if not (note_ids & set(c.get("fact_ids") or []))]
    if found:
        show_conflicts(found, f"\n  `fact conflict list --actor {ids['buyer']}` — {len(found)} conflict(s) naming Tom's notes"
                              + (f" (after {waited}s)" if fresh else "") + ":")
    covered = {f for c in found for f in c["fact_ids"]} & note_ids
    missing = [e for e in entries if got[e["text"]]["id"] not in covered]
    for e in missing:
        emit("text", f"\n  ✗ nothing raised on “{clip(e['text'], 70)}”" + (f" within {waited}s" if fresh else "")
                     + " — the detector sometimes answers minutes later, as a batch; check with `python3 demo.py conflicts`")
    if others:
        show_conflicts(others, f"\n  {len(others)} other conflict(s) on Lena's actor (not planned by the story — shown, not hidden):")
    out = dict(conflicts=found, others=others, waited=waited, fresh=fresh, missing=[e["key"] for e in missing])
    emit("detector", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: takeover

def reflection(story: dict, showings_: list[dict]) -> list[dict]:
    """What Lena said at kickoff vs what she responded to in the showings (the team project)."""
    out = []
    for e in [e for e in profile_entries(story) if e["source"] == "kickoff"]:
        broke = [s for s in showings_ if not rule_ok(e["rule"], s, e["field"])]
        loved = [s for s in broke if s["reaction"] == "loved"]
        disliked = [s for s in broke if s["reaction"] == "disliked"]
        verdict = "contradicted" if loved else "consistent" if disliked else "untested"
        out.append(dict(key=e["key"], said=e["label"], agent=e["agent"], said_on=e["said_on"], verdict=verdict,
                        evidence=[dict(id=s["id"], listing=s["listing"], date=s["date"], reaction=s["reaction"],
                                       value=field_value(s, e["field"])) for s in (loved or disliked)]))
    return out


def takeover(cli: CLI, ids: dict, story: dict) -> dict:
    maya = story["agents"]["maya"]["name"]
    note(f"{maya} takes Lena over today ({story['today']}). Everything below is read from memory: "
         "the profile from Lena's actor, the showings from the team project")
    if not list_conflicts(cli, ids["buyer"], "--resolved", "false", echo=False) and \
            list_conflicts(cli, ids["buyer"], "--resolved", "true", echo=False):
        note("an earlier run already resolved Lena's conflicts, so this screen shows the profile as it is now "
             "(--reset replays the whole story)")
    before = run_screen(cli, ids, story, "before")
    shown = read_showings(cli, ids["project"])
    refl = reflection(story, shown)
    lines = [f"\n  Said vs responded — the kickoff profile against {len(shown)} showings:"]
    for r in refl:
        mark = {"contradicted": "≠", "consistent": "=", "untested": "·"}[r["verdict"]]
        ev = "; ".join(f"{x['reaction']} {x['listing']} ({x['value']}, {x['date'][5:]})" for x in r["evidence"]) or "no showing tested it"
        lines.append(f"    {mark} said “{r['said']}” ({r['agent']}, {r['said_on'][5:]})  →  {ev}")
    emit("text", "\n".join(lines))
    emit("reflection", "", items=refl)
    return dict(before=before, reflection=refl)


# --------------------------------------------------------------------------- step 7: resolve

def resolve(cli: CLI, ids: dict, story: dict) -> dict:
    maya = story["agents"]["maya"]["name"]
    shown = {s["listing"]: s for s in read_showings(cli, ids["project"], echo=False)}
    by_key = {s["key"]: s for s in story["showings"]}
    note_texts = {e["text"]: e for e in profile_entries(story) if e["source"] == "call notes"}
    open_ = [conflict_view(c, story) for c in list_conflicts(cli, ids["buyer"], "--resolved", "false")]
    done = []
    if not open_:
        note("no open conflict on Lena's actor (resolved by an earlier run, or the detector has not answered yet)")
    for c in open_:
        keep = next((f for f in c["facts"] if f["text"] in note_texts), None)
        if c["category"] != "m2m" or keep is None:
            note(f"{c['id']} ({c['category']}) is not one the story plans for; left open for Maya to read")
            continue
        e = note_texts[keep["text"]]
        evidence = [shown[by_key[k]["listing"]] for k in e.get("evidence", []) if by_key[k]["listing"] in shown]
        why = "; ".join(f"{s['reaction']} {s['listing']} ({field_value(s, e['field'])}, {s['date']}, {s['id']})" for s in evidence)
        emit("text", f"\n  {c['id']}: keep {keep['agent']}'s “{e['label']}” — the showings back it: {why}")
        r = cli.run("fact", "conflict", "resolve", c["id"], "--actor", ids["buyer"], "--strategy", "keep_fact",
                    "--keep-fact-id", keep["id"], scoped=True) or {}
        note(f"resolved with {r.get('strategy')}: forgotten {', '.join(r.get('forgotten_fact_ids') or []) or 'nothing'}")
        done.append(dict(id=c["id"], name=c["name"], strategy=r.get("strategy"), kept=keep["id"], label=e["label"],
                         forgotten=r.get("forgotten_fact_ids") or [], evidence=[s["id"] for s in evidence], why=why))
    time.sleep(2 if done else 0)
    profile = read_profile(cli, ids["buyer"])
    show_profile(profile, f"\n  Lena's profile after {maya}'s resolution ({len(profile)} facts):")
    after = run_screen(cli, ids, story, "after")
    out = dict(resolved=done, profile=profile, after=after)
    emit("resolved", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: handoff record

def handoff(cli: CLI, ids: dict, story: dict, before: list[dict], after: list[dict]) -> Path:
    resolved = [conflict_view(cli.run("fact", "conflict", "get", c["id"], "--actor", ids["buyer"], scoped=True), story)
                for c in list_conflicts(cli, ids["buyer"], "--resolved", "true")]
    still_open = [conflict_view(c, story) for c in list_conflicts(cli, ids["buyer"], "--resolved", "false", echo=False)]
    profile = read_profile(cli, ids["buyer"], echo=False)
    shown = read_showings(cli, ids["project"], echo=False)
    b = story["buyer"]
    lines = [f"# Buyer profile — {b['name']}", "",
             f"{story['team']} · handed to {story['agents']['maya']['name']} on {story['today']}. Generated "
             f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from MemoryLake (buyer actor `{ids['buyer']}`, team project `{ids['project']}`).",
             "", "## Profile (current)", ""]
    lines += [f"- **{p['kind']}** — {p['text']} _({p['agent']}, {p['said_on']}; `{p['id']}`)_" for p in profile]
    lines += ["", "## Showing history", ""]
    lines += [f"- {s['date']} · {s['listing']} · {s['street_type']} · {money(s['price'])} · **{s['reaction']}** ({s['agent']}) `{s['id']}`" for s in shown]
    lines += ["", "## What changed, and why (`fact conflict get`)", ""]
    for c in resolved:
        rs = c["resolve"] or {}
        lines += [f"### {c['name']} — `{c['id']}`", "",
                  f"- {c['category']} · {c['conflict_type']} · raised {c['created_at']} · resolved {rs.get('created_at')} · `{rs.get('strategy')}`"]
        for f in c["facts"]:
            gone = f["id"] in (rs.get("forgotten_fact_ids") or [])
            lines.append(f"- {'forgotten' if gone else 'kept'}: “{f['text']}” ({f['agent']}, {f['said_on']}; `{f['id']}`)")
        lines += [f"- detector: {c['description']}", ""]
    lines += ["## Still open", ""] + ([f"- `{c['id']}` {c['name']}" for c in still_open] or ["- none"])
    lines += ["", "## Tour screen", "", "| listing | before | after |", "|---|---|---|"]
    for x, y in zip(before, after):
        lines.append(f"| {x['listing']} | {x['verdict']} {VERDICT[x['verdict']]} | {y['verdict']} {VERDICT[y['verdict']]} |")
    OUT.mkdir(exist_ok=True)
    path = OUT / f"buyer-profile-{b['slug']}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    show = [f"\n  {len(resolved)} resolved conflict(s) on Lena's actor, {len(still_open)} open:"]
    for c in resolved:
        rs = c["resolve"] or {}
        show.append(f"\n  {c['id']}  {c['name']}")
        show.append(f"    raised {str(c['created_at'])[:19]}Z · resolved {str(rs.get('created_at'))[:19]}Z · {rs.get('strategy')}")
        for f in c["facts"]:
            gone = f["id"] in (rs.get("forgotten_fact_ids") or [])
            show.append(f"    {'forgotten' if gone else 'kept     '} “{clip(f['text'], 84)}” ({f['agent']}, {f['said_on']})")
    show.append("\n  The forgotten kickoff notes are gone from the profile, but each resolved conflict keeps them word for word.")
    show.append("\n  Tour screen, before → after:")
    for x, y in zip(before, after):
        show.append(f"    {x['listing']:<22} {x['verdict']} → {y['verdict']}  {VERDICT[y['verdict']]}")
    show.append(f"\n  Handoff record written to {os.path.relpath(path)}")
    emit("text", "\n".join(show))
    emit("handoff", "", resolved=resolved, open=still_open, file=os.path.relpath(path), markdown="\n".join(lines),
         before=before, after=after)
    return path


# --------------------------------------------------------------------------- ask

def ask(cli: CLI, ids: dict, question: str, echo: bool = True) -> dict:
    """One search over Lena's profile and the team's showings: `--projects P --actors A` returns both."""
    res = cli.run("search", question, "--projects", ids["project"], "--actors", ids["buyer"], "--types", "fact",
                  "--top-k", "6", scoped=True, echo=echo) or {}
    profile = {f.get("id") for f in list_facts(cli, "--actors", ids["buyer"], echo=False)}
    hits = [dict(id=f.get("id"), text=f.get("fact") or f.get("content"), rank=f.get("rank"),
                 scope="profile" if f.get("id") in profile else "showings") for f in res.get("facts") or []]
    return dict(question=question, hits=hits)


def show_ask(r: dict) -> None:
    emit("text", f"\n  “{r['question']}”\n" + "\n".join(f"    {h['rank']}. [{h['scope']}] {clip(h['text'], 110)}" for h in r["hits"]))


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the team project (the showings go with it)")
    actor = find_buyer(cli, story, echo=not quiet)
    if actor:
        cli.run("actor", "delete", actor["id"])
        note("deleted the buyer actor (her profile facts and conflicts go with it)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Team project + buyer actor — one shared history, one profile per buyer")
    ids = setup(cli, story, reset)
    banner(3, TOTAL, f"Kickoff — {agent_name(story, 'priya')} pins Lena's profile ({story['kickoff']['date']})")
    kickoff(cli, ids, story)
    banner(4, TOTAL, "Showing history — what Lena saw and how she reacted, on the team project")
    showings(cli, ids, story)
    banner(5, TOTAL, f"Call notes — {agent_name(story, 'tom')} writes two notes; MemoryLake flags what contradicts")
    det = call_notes(cli, ids, story)
    banner(6, TOTAL, f"Takeover — {agent_name(story, 'maya')} screens her tour against Lena's memory")
    tk = takeover(cli, ids, story)
    banner(7, TOTAL, "Resolve — keep_fact, with the showings as evidence")
    res = resolve(cli, ids, story)
    banner(8, TOTAL, "Handoff record — what changed on the profile, and why")
    path = handoff(cli, ids, story, tk["before"], res["after"])
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   fresh=det["fresh"], waited=det["waited"],
                   detected=sorted(f.get("key") or "?" for c in det["conflicts"] for f in c["facts"] if f.get("source") == "call notes"),
                   missing=det["missing"], others=len(det["others"]), resolved=len(res["resolved"]),
                   before={r["key"]: r["verdict"] for r in tk["before"]}, after={r["key"]: r["verdict"] for r in res["after"]},
                   record=path.name)
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    proj, buyer = find_project(cli), find_buyer(cli, story)
    if proj is None or buyer is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], buyer=buyer["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "profile", "conflicts", "screen", "ask", "cleanup"],
                    help="run = full demo (default); profile = Lena's profile + showings; conflicts = every conflict on "
                         "her actor; screen = screen the tour now; ask \"QUESTION\" = search profile + showings; cleanup = delete everything")
    ap.add_argument("question", nargs="?", help="question for `ask`")
    ap.add_argument("--reset", action="store_true", help="delete the team project and the buyer first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "ask" and not args.question:
        ap.error("ask needs a question, e.g. python3 demo.py ask \"How does Lena feel about busy streets?\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py conflicts`, `python3 demo.py screen`, or `python3 demo.py ask \"…\"`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    story, ids = load_story(), need_ids(cli)
    if args.command == "profile":
        banner(2, 2, "Lena's profile and showing history")
        show_profile(read_profile(cli, ids["buyer"]), "\n  Profile (Lena's actor):")
        emit("text", "\n  Showing history (team project):")
        for s in read_showings(cli, ids["project"]):
            emit("text", f"    {s['date']}  {s['listing']:<18} {s['street_type']:<15} {money(s['price']):>9}  {s['reaction']}")
    elif args.command == "conflicts":
        banner(2, 2, "Every conflict on Lena's actor")
        items = [conflict_view(c, story) for c in list_conflicts(cli, ids["buyer"])]
        show_conflicts(items, f"\n  {len(items)} conflict(s):") if items else emit("text", "\n  No conflicts on Lena's actor.")
    elif args.command == "screen":
        banner(2, 2, "Screen the tour against Lena's memory, now")
        run_screen(cli, ids, story, "now")
    elif args.command == "ask":
        banner(2, 2, "Ask Lena's memory — profile + showings in one search")
        show_ask(ask(cli, ids, args.question))


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

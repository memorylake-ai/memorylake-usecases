#!/usr/bin/env python3
"""
Stakeholder mapping memory for consultants — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Ashgrove Partners (fictional) is starting its third engagement with the same
client, Calder Freight.  The first two left a trail in MemoryLake: who held
which seat, what each person is like to work with, who signed what, and the
stakeholder map from engagement 2.  Every note is one fact, pinned word for
word, tagged with what it is anchored to: a seat ("the VP Finance signs any SOW
over 150,000 dollars") or a person ("Dana asks for the downside case first").

The obvious move is to inherit the last map.  It says the SOW is signed by Dana,
the COO Marcus is the skeptic, Ines runs the sites, Tom owns the WMS change
board.  Every line of it was true in February, and every fact behind it is
still true today — but Calder reorganised in between.  Marcus left, Ines is
acting COO, Priya joined as VP Finance and Dana moved to a new VP Strategy seat.
Sign-off authority belongs to the seat, so it went to Priya; Dana's habits
belong to Dana, so they went with her.  Resolve each map line through the seat
tenures and three of the four lines change: the SOW signer is someone nobody
has met, and the seat that holds the veto is now held by the person who backed
automation last time.

The same resolver, asked about 2026-02-20, rebuilds engagement 2's map exactly —
so the difference is the date, not the method.  The new map is written back for
engagement 3, each line carrying the ids of the facts it came from.

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

PREFIX = "mlu-smm"  # memorylake-usecases / stakeholder-map-memory


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


def people(story: dict) -> dict[str, dict]:
    return {p["key"]: p for p in story["people"]}


def seats(story: dict) -> dict[str, dict]:
    return {s["key"]: s for s in story["seats"]}


def functions(story: dict) -> dict[str, dict]:
    return {f["key"]: f for f in story["functions"]}


def engagements(story: dict) -> dict[str, dict]:
    return {e["key"]: e for e in story["engagements"]}


def name(story: dict, person: str | None) -> str:
    return people(story)[person]["name"] if person else "(vacant)"


def first(n: str) -> str:
    return n.split()[0]


def tenure_text(story: dict, t: dict) -> str:
    seat, client = seats(story)[t["seat"]]["title"], story["client"]
    who = name(story, t["person"]) + (" (acting)" if t.get("acting") else "") if t["person"] else "vacant"
    span = f"from {t['from']} to {t['to']}" if t["to"] else f"from {t['from']}, current at the last check"
    return f"Seat tenure at {client}: {seat} - {who}, {span} (source: {t['source']})."


def tenure_meta(t: dict) -> dict:
    return dict(kind="tenure", key=f"tenure:{t['seat']}:{t['from']}", seat=t["seat"], person=t["person"],
                acting=bool(t.get("acting")), start=t["from"], end=t["to"], source=t["source"])


def checkin_meta(c: dict) -> dict:
    return dict(kind="checkin", key=f"checkin:{c['date']}", date=c["date"])


def obs_text(story: dict, o: dict) -> str:
    return f"{engagements(story)[o['engagement']]['name']}, {o['date']}: {o['text']}"


def obs_meta(o: dict) -> dict:
    m = dict(kind="observation", key=f"obs:{o['id']}", anchor=o["anchor"], engagement=o["engagement"], date=o["date"])
    for k in ("seat", "function", "person", "trait", "stance"):
        if o.get(k):
            m[k] = o[k]
    return m


def map_text(story: dict, eng: str, day: str, row: dict) -> str:
    fn = functions(story)[row["function"]]
    seat = seats(story)[fn["seat"]]["title"]
    stance = row.get("stance") or ("no one to brief" if not row["person"] else "stance unknown (no observations yet)")
    who = name(story, row["person"]) if row["person"] else "vacant"
    since = f" since {row['since']}" if row.get("since") else ""
    acting = " (acting)" if row.get("acting") else ""
    if not row["person"]:
        return (f"Stakeholder map, {engagements(story)[eng]['name']} ({day}): {fn['label']} - {seat} vacant{since}; "
                f"{stance}.")
    return (f"Stakeholder map, {engagements(story)[eng]['name']} ({day}): {fn['label']} - {who}{acting}, "
            f"{seat}{since}; {stance}.")


def map_meta(eng: str, day: str, row: dict, basis: list[str] = ()) -> dict:
    m = dict(kind="map", key=f"map:{eng}:{row['function']}", engagement=eng, as_of=day, function=row["function"],
             person=row["person"], stance=row.get("stance"))
    if basis:
        m["basis"] = list(basis)
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
    """The pinned facts by kind, metadata flattened in, each keyed by its metadata key."""
    out = dict(tenure={}, checkin={}, observation={}, map={})
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("kind") in out:
            out[m["kind"]][m["key"]] = dict(m, id=f["id"], text=f.get("fact"))
    return out


def read_all(cli: CLI, ids: dict, echo: bool = True) -> dict:
    """Everything in one call: `fact list --projects` (seats, three engagements) and `--actors` (five people)
    together return both scopes' facts, every page."""
    return entries(list_facts(cli, projects=[ids[k] for k in ("seats", "e1", "e2", "e3")],
                              actors=[ids["actors"][k] for k in sorted(ids["actors"])], echo=echo))


def live_keys(facts: list[dict]) -> dict:
    return {(f.get("metadata") or {}).get("key"): f for f in facts if (f.get("metadata") or {}).get("key")}


# --------------------------------------------------------------------------- the resolver (plain code, no model)

def holder(mem: dict, seat: str, day: str) -> dict | None:
    """The tenure fact for whoever held `seat` on `day` (a vacancy is a tenure with no person)."""
    for t in mem["tenure"].values():
        if t["seat"] == seat and t["start"] <= day and (not t.get("end") or day <= t["end"]):
            return t
    return None


def seats_held(mem: dict, person: str, day: str) -> list[dict]:
    return sorted((t for t in mem["tenure"].values() if t.get("person") == person and t["start"] <= day
                   and (not t.get("end") or day <= t["end"])), key=lambda t: t["seat"])


def about(mem: dict, person: str, day: str, trait: str | None = None) -> list[dict]:
    """Observations anchored to a person, up to `day`, oldest first."""
    return sorted((o for o in mem["observation"].values() if o.get("anchor") == "person" and o.get("person") == person
                   and o["date"] <= day and (trait is None or o.get("trait") == trait)), key=lambda o: o["date"])


def stance(mem: dict, person: str | None, day: str) -> dict | None:
    s = about(mem, person, day, "stance") if person else []
    return s[-1] if s else None


def resolve(story: dict, mem: dict, day: str) -> list[dict]:
    """One row per map function: the seat's authority (seat-anchored observations), whoever sits in the
    seat on `day` (tenure facts), and that person's own stance and habits (person-anchored observations)."""
    rows = []
    for fn in story["functions"]:
        t = holder(mem, fn["seat"], day)
        person = t.get("person") if t else None
        st = stance(mem, person, day)
        authority = sorted((o for o in mem["observation"].values() if o.get("anchor") == "seat"
                            and o.get("function") == fn["key"] and o["date"] <= day), key=lambda o: o["date"])
        style = about(mem, person, day, "style") if person else []
        basis = [x["id"] for x in ([t] if t else []) + authority + ([st] if st else []) + style]
        rows.append(dict(function=fn["key"], seat=fn["seat"], person=person, acting=bool(t and t.get("acting")),
                         since=t["start"] if t else None, stance=st["stance"] if st else None,
                         observations=len(about(mem, person, day)) if person else 0,
                         authority=[a["text"] for a in authority], style=[s["text"] for s in style],
                         tenure=t["text"] if t else None, basis=basis))
    return rows


def inherited(mem: dict, eng: str) -> list[dict]:
    rows = [m for m in mem["map"].values() if m["engagement"] == eng]
    order = {k: i for i, k in enumerate(["sow", "steer", "sites", "wms"])}
    return sorted(rows, key=lambda m: order.get(m["function"], 9))


def moved(story: dict, mem: dict, old: list[dict], new: list[dict], day: str) -> list[dict]:
    """People on the old map who no longer hold the seat that put them there: where they are now."""
    now = {r["person"] for r in new}
    out = []
    for r in old:
        p = r.get("person")
        if not p or p in now:
            continue
        held = seats_held(mem, p, day)
        st = stance(mem, p, day)
        out.append(dict(person=p, seats=[t["seat"] for t in held], since=[t["start"] for t in held],
                        stance=st["stance"] if st else None, style=[s["text"] for s in about(mem, p, day, "style")],
                        basis=[t["id"] for t in held] + ([st["id"]] if st else [])))
    return out


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = dict(actors={})
    for p in story["projects"]:
        proj = find_project(cli, p["key"])
        if proj is None:
            proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                           "--description", p["description"], scoped=True)
            note(f"created “{p['name']}” → {proj['id']}")
        else:
            note(f"“{p['name']}” already exists → {proj['id']} (pass --reset to start over)")
        ids[p["key"]] = proj["id"]
    for p in story["people"]:
        a = find_actor(cli, p["key"])
        if a is None:
            a = cli.run("actor", "create", "--custom-id", cid(p["key"]), "--display-name", p["name"],
                        "--description", f"{p['name']}, {story['client']} (demo: stakeholder-map-memory)")
            note(f"created {p['name']} → {a['id']}")
        else:
            note(f"{p['name']} already exists → {a['id']}")
        try:
            cli.run("actor", "bind", "--actor", a["id"], scoped=True, echo=p is story["people"][0])
        except CLIError as e:
            if not e.has("already", "409", "conflict"):
                raise
        ids["actors"][p["key"]] = a["id"]
    note("seats and what they can do live on the client's projects; what a person is like lives on that person "
         "(an actor) — so it can follow them when they change seats")
    emit("setup", "", ids=ids, people=story["people"])
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


def intake(cli: CLI, ids: dict, story: dict) -> dict:
    note("one fact per note, word for word, each tagged with what it is anchored to: a seat (authority, who held "
         "it when) or a person (stance, habits)")
    total = 0
    live = live_keys(list_facts(cli, projects=[ids["seats"]], echo=False))
    total += len(pin(cli, ("--project", ids["seats"]), live,
                     [(tenure_text(story, t), tenure_meta(t)) for t in story["tenures"]]
                     + [(c["text"], checkin_meta(c)) for c in story["checkins"]], "seat, person, from, to, source"))
    for eng in ("e1", "e2"):
        live = live_keys(list_facts(cli, projects=[ids[eng]], echo=False))
        items = [(obs_text(story, o), obs_meta(o)) for o in story["observations"]
                 if o["anchor"] == "seat" and o["engagement"] == eng]
        if eng == "e2":
            m = story["e2_map"]
            items += [(map_text(story, "e2", m["date"], dict(r, seat=functions(story)[r["function"]]["seat"])),
                       map_meta("e2", m["date"], r)) for r in m["rows"]]
        total += len(pin(cli, ("--project", ids[eng]), live, items, "anchor, seat, engagement, date",
                         echo=eng == "e1"))
    first_person = True
    for p in story["people"]:
        obs = [(obs_text(story, o), obs_meta(o)) for o in story["observations"] if o.get("person") == p["key"]]
        if not obs:
            continue
        live = live_keys(list_facts(cli, actors=[ids["actors"][p["key"]]], echo=False))
        total += len(pin(cli, ("--actor", ids["actors"][p["key"]]), live, obs, "anchor, person, trait, date",
                         echo=first_person))
        first_person = False
    if not total:
        note("everything was pinned by an earlier run; reading it back")
    mem = read_all(cli, ids)
    show_timeline(story, mem)
    emit("intake", "", counts={k: len(v) for k, v in mem.items()}, timeline=timeline(story, mem))
    return mem


def timeline(story: dict, mem: dict) -> list[dict]:
    ev = [dict(date=e["start"], what=f"{e['name']} starts", kind="engagement") for e in story["engagements"]]
    ev += [dict(date=e["end"], what=f"{e['name']} ends", kind="engagement") for e in story["engagements"] if e["end"]]
    ev += [dict(date=c["date"], what=c["text"], kind="checkin") for c in mem["checkin"].values()]
    m = story["e2_map"]
    ev.append(dict(date=m["date"], what=f"E2 stakeholder map saved ({len(inherited(mem, 'e2'))} lines)", kind="map"))
    return sorted(ev, key=lambda e: e["date"])


def show_timeline(story: dict, mem: dict) -> None:
    n_seat = sum(1 for o in mem["observation"].values() if o["anchor"] == "seat")
    n_person = sum(1 for o in mem["observation"].values() if o["anchor"] == "person")
    lines = [f"\n  {len(mem['tenure'])} seat tenures · {len(mem['checkin'])} check-in notes · {n_seat} seat-anchored and "
             f"{n_person} person-anchored observations · {len(mem['map'])} map lines\n"]
    for e in timeline(story, mem):
        lines.append(f"    {e['date']}  {clip(e['what'], 120)}")
    per = {p["key"]: len(about(mem, p["key"], "9999")) for p in story["people"]}
    lines.append("\n    on each person: " + " · ".join(f"{first(name(story, k))} {n}" for k, n in per.items()))
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- step 4: inherit the last map

def show_map(story: dict, rows: list[dict], title: str) -> None:
    lines = [f"\n    {title}", f"    {'function':<27} {'who':<24} {'seat':<20} stance"]
    for r in rows:
        fn = functions(story)[r["function"]]
        who = name(story, r["person"]) + (" (acting)" if r.get("acting") else "") if r["person"] else "(vacant)"
        lines.append(f"    {fn['label']:<27} {who:<24} {seats(story)[fn['seat']]['title']:<20} "
                     f"{r.get('stance') or 'unknown'}")
    emit("text", "\n".join(lines))


def inherit_step(cli: CLI, ids: dict, story: dict) -> dict:
    note("what a new team does on day one: open the last engagement's stakeholder map and carry it over")
    mem = entries(list_facts(cli, projects=[ids["e2"]]))
    old = inherited(mem, "e2")
    show_map(story, old, f"inherited from E2 ({story['e2_map']['date']}), as saved:")
    q = story["ask_question"]
    note("and ask memory the day-one question across everything the firm knows about the client")
    hits = ask(cli, ids, q, top_k=3)["hits"]
    lines = [f"\n    “{q}” — search top {len(hits)}:"]
    for h in hits:
        lines.append(f"      {h['rank']:>2}  {clip(h['text'], 120)}")
    named = next((p for h in hits for p in story["people"] if p["name"] in (h["text"] or "")), None)
    lines.append(f"\n  → the inherited map sends the SOW to {name(story, old[0]['person'])}; "
                 + (f"the first person search names is {named['name']}" if named else "search names no one")
                 + " — every fact behind both is true")
    emit("text", "\n".join(lines))
    out = dict(rows=old, ask=dict(q=q, hits=hits), named=named["key"] if named else None)
    emit("inherited", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: resolve through the seats

def diff(story: dict, old: list[dict], new: list[dict]) -> list[dict]:
    by = {r["function"]: r for r in old}
    out = []
    for n in new:
        o = by.get(n["function"]) or {}
        same = o.get("person") == n["person"] and o.get("stance") == n["stance"]
        out.append(dict(function=n["function"], same=same, was=dict(person=o.get("person"), stance=o.get("stance")),
                        now=dict(person=n["person"], stance=n["stance"], since=n["since"], acting=n["acting"],
                                 observations=n["observations"])))
    return out


def why(story: dict, mem: dict, d: dict, day: str) -> str:
    was, now = d["was"], d["now"]
    if d["same"]:
        return "same person, same stance"
    if not now["person"]:
        return f"{name(story, was['person'])} left the seat; vacant since {now['since']}"
    if was["person"] != now["person"]:
        bits = [f"{name(story, now['person'])} holds the seat since {now['since']}"]
        if not now["observations"]:
            bits.append("no observations of them yet")
        elif now["stance"] != was["stance"]:
            bits.append(f"their own stance is {now['stance']}, not {was['stance']}")
        return "; ".join(bits)
    return f"stance changed: {was['stance']} → {now['stance']}"


def resolve_step(cli: CLI, ids: dict, story: dict, inh: dict) -> dict:
    day, then = story["today"], story["e2_map"]["date"]
    note("read everything once — seats, three engagements, five people in one `fact list` — then join in plain "
         "code: each map line's seat → who holds it on the day, and that person's own observations")
    mem = read_all(cli, ids)

    check = resolve(story, mem, then)
    old = inh["rows"]
    agree = [d["same"] for d in diff(story, old, check)]
    show_map(story, check, f"the resolver asked about {then} (the day E2's map was saved):")
    emit("text", f"\n    {'✓' if all(agree) else '✗'} {sum(agree)}/{len(agree)} lines match the map E2 saved that day "
                 f"— the method reproduces the old map; only the date differs")

    rows = resolve(story, mem, day)
    show_map(story, rows, f"the resolver asked about today ({day}):")
    dd = diff(story, old, rows)
    lines = ["\n    line by line against the inherited map:"]
    for d in dd:
        fn = functions(story)[d["function"]]
        lines.append(f"      {'✓' if d['same'] else '✗'} {fn['label']:<27} {name(story, d['was']['person']):<14} → "
                     f"{name(story, d['now']['person']):<14} {why(story, mem, d, day)}")
    gone = moved(story, mem, old, rows, day)
    for g in gone:
        where = ", ".join(seats(story)[s]["title"] for s in g["seats"]) or "no seat at the client"
        since = f" since {g['since'][0]}" if g["since"] else ""
        carry = f"; carries with them: {g['stance'] or 'no stance'}" + (f", {len(g['style'])} habit note(s)"
                                                                          if g["style"] else "")
        lines.append(f"      → {name(story, g['person'])}: now {where}{since}{carry if g['seats'] else ''}")
    still = sum(1 for d in dd if d["same"])
    lines.append(f"\n  → {still} of {len(dd)} inherited lines still hold. Authority stayed with the seats; habits "
                 f"and stances went with the people.")
    emit("text", "\n".join(lines))
    out = dict(check=dict(day=then, rows=check, agree=sum(agree), total=len(agree)), rows=rows, diff=dd,
               moved=gone, still=still)
    emit("resolved", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: write the E3 map, with its sources

def write_step(cli: CLI, ids: dict, story: dict, res: dict) -> dict:
    day = story["today"]
    note("write E3's map back as memory on the E3 project, one fact per line, each with the ids of the facts it "
         "was resolved from in metadata.basis")
    live = live_keys(list_facts(cli, projects=[ids["e3"]], echo=False))
    items, changed = [], []
    for r in res["rows"]:
        text, meta = map_text(story, "e3", day, r), map_meta("e3", day, r, r["basis"])
        have = live.get(meta["key"])
        if have and have.get("fact") != text:
            cli.run("fact", "update", have["id"], "--project", ids["e3"], "--text", text, "--metadata",
                    json.dumps(meta), scoped=True, echo=not changed)
            changed.append(meta["key"])
        items.append((text, meta))
    new = pin(cli, ("--project", ids["e3"]), live, items, "function, person, stance, basis")
    if not new and not changed:
        note("E3's map was written by an earlier run; reading it back")
    mem = read_all(cli, ids)
    by_id = {v["id"]: v for d in mem.values() for v in d.values()}
    rows = inherited(mem, "e3")
    lines = [f"\n  E3 stakeholder map ({len(rows)} lines on the E3 project), each with where it came from:"]
    out_rows = []
    for m in rows:
        lines.append(f"\n    {m['text']}")
        src = []
        for b in m.get("basis") or []:
            f = by_id.get(b)
            kind = f"{f['kind']}·{f.get('anchor')}" if f and f.get("anchor") else (f["kind"] if f else "missing")
            lines.append(f"      ← {b}  {kind:<18} {clip(f['text'] if f else '(not found)', 96)}")
            src.append(dict(id=b, kind=kind, text=f["text"] if f else None))
        out_rows.append(dict(id=m["id"], text=m["text"], function=m["function"], person=m.get("person"), sources=src))
    resolved = sum(1 for r in out_rows for s in r["sources"] if s["text"])
    total = sum(len(r["sources"]) for r in out_rows)
    gaps = [r for r in res["rows"] if r["person"] and not r["observations"]] + [r for r in res["rows"] if not r["person"]]
    lines.append(f"\n    {'✓' if resolved == total else '✗'} {resolved}/{total} cited facts read back from memory")
    if gaps:
        lines.append("\n  gaps to close before the steering committee:")
        for g in gaps:
            fn = functions(story)[g["function"]]
            lines.append(f"    · {fn['label']}: " + (f"{name(story, g['person'])} — no observations; meet before the SOW, "
                                                     f"don't brief with someone else's habits" if g["person"]
                                                     else f"{seats(story)[g['seat']]['title']} is vacant — ask who "
                                                          f"covers site data requests"))
    emit("text", "\n".join(lines))
    out = dict(rows=out_rows, written=len(new), rewritten=changed, cited=resolved, cited_total=total,
               gaps=[g["function"] for g in gaps])
    emit("written", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: map, person, ask

def person_view(cli: CLI, ids: dict, story: dict, who: str, echo: bool = True) -> dict:
    mem = read_all(cli, ids, echo=echo)
    ten = sorted((t for t in mem["tenure"].values() if t.get("person") == who), key=lambda t: t["start"])
    obs = about(mem, who, "9999")
    return dict(person=who, name=name(story, who), tenures=[dict(seat=t["seat"], start=t["start"], end=t.get("end"),
                                                                acting=t.get("acting")) for t in ten],
                observations=[dict(date=o["date"], trait=o.get("trait"), stance=o.get("stance"), text=o["text"])
                              for o in obs])


def show_person(story: dict, r: dict) -> None:
    lines = [f"\n  {r['name']} — seats held:"]
    for t in r["tenures"]:
        lines.append(f"    {t['start']} → {t['end'] or 'now':<10}  {seats(story)[t['seat']]['title']}"
                     + (" (acting)" if t["acting"] else ""))
    lines.append(f"\n  observations on {first(r['name'])} ({len(r['observations'])}):")
    for o in r["observations"]:
        lines.append(f"    {o['date']}  {o['trait']:<6} {clip(o['text'], 110)}")
    if not r["observations"]:
        lines.append("    (none — nobody at the firm has worked with them yet)")
    emit("text", "\n".join(lines))


def ask(cli: CLI, ids: dict, q: str, top_k: int = 8, echo: bool = True) -> dict:
    """Any question over the client's seats, the first two engagements and the five people: search's top k."""
    res = cli.run("search", q, "--projects", ",".join(ids[k] for k in ("seats", "e1", "e2")),
                  "--actors", ",".join(ids["actors"][k] for k in sorted(ids["actors"])), "--types", "fact",
                  "--top-k", str(top_k), scoped=True, echo=echo) or {}
    hits = [dict(rank=f.get("rank") or i, id=f.get("id"), text=f.get("fact"))
            for i, f in enumerate(res.get("facts") or [], 1)]
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
    for p in story["people"]:
        a = find_actor(cli, p["key"], echo=False)
        if a:
            n += delete_actor(cli, a["id"])
    note(f"deleted the five people (actors) and their {n} observation fact(s) (facts first, then the actor)")
    for p in story["projects"]:
        proj = find_project(cli, p["key"], echo=not quiet)
        if proj:
            for f in list_facts(cli, projects=[proj["id"]], echo=False):
                cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
            left = list_facts(cli, projects=[proj["id"]], echo=False)
            if left:
                die(f"{len(left)} fact(s) still on {proj['id']} after deleting; run cleanup again")
            cli.run("proj", "delete", proj["id"], scoped=True)
    note("deleted the seats project and the three engagement projects, and every fact on them")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — the client's seats, three engagements, an actor per person")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Intake — two engagements of notes, each anchored to a seat or to a person")
    intake(cli, ids, story)
    banner(4, TOTAL, "Day one of engagement 3 — inherit the last stakeholder map")
    inh = inherit_step(cli, ids, story)
    banner(5, TOTAL, "Resolve — each map line through who holds the seat today, and what that person is like")
    res = resolve_step(cli, ids, story, inh)
    banner(6, TOTAL, "Write E3's map back — every line with the facts it came from")
    wr = write_step(cli, ids, story, res)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   inherited={r["function"]: r["person"] for r in inh["rows"]}, search_named=inh["named"],
                   search_top=[h["text"] for h in inh["ask"]["hits"]],
                   check=f"{res['check']['agree']}/{res['check']['total']}",
                   today={r["function"]: r["person"] for r in res["rows"]}, still=res["still"],
                   cited=f"{wr['cited']}/{wr['cited_total']}", written=wr["written"], gaps=wr["gaps"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    ids = dict(actors={})
    for p in story["projects"]:
        proj = find_project(cli, p["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[p["key"]] = proj["id"]
    for p in story["people"]:
        a = find_actor(cli, p["key"], echo=False)
        if a is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids["actors"][p["key"]] = a["id"]
    return ids


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "map", "person", "ask", "cleanup"],
                    help="run = full demo (default); map [YYYY-MM-DD] = the stakeholder map resolved for a date "
                         "(default today); person NAME = seats a person held and every observation of them; "
                         "ask \"QUESTION\" = search's top 8 over the client's memory; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the date, the person (dana, marcus, ines, tom, priya) or the question")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("person", "ask") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. person dana  /  ask \"Who signs the SOW?\"")
    if a.command == "person" and a.args[0].lower() not in people(story):
        ap.error(f"unknown person {a.args[0]!r}; this demo has: {', '.join(people(story))}")
    if a.command == "map" and a.args and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.args[0]):
        ap.error("map takes a date as YYYY-MM-DD, e.g. map 2026-07-15")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py map 2026-07-15`, `python3 demo.py person dana` or "
              "`python3 demo.py ask \"Who can veto the automation project?\"`; `python3 demo.py cleanup` removes the "
              "demo data.")
        return
    ids = need_ids(cli)
    if a.command == "map":
        day = a.args[0] if a.args else story["today"]
        banner(2, 2, f"The stakeholder map as of {day} — each line through the seats and the people")
        mem = read_all(cli, ids)
        show_map(story, resolve(story, mem, day), f"resolved for {day}:")
    elif a.command == "person":
        who = a.args[0].lower()
        banner(2, 2, f"{name(story, who)} — seats held, and what we know about them")
        show_person(story, person_view(cli, ids, story, who))
    elif a.command == "ask":
        banner(2, 2, "Ask — search's top 8 over the client's seats, engagements and people")
        r = ask(cli, ids, a.args[0])
        lines = [f"\n  “{r['q']}” — search top {len(r['hits'])}:"]
        for h in r["hits"]:
            lines.append(f"    {h['rank']:>2}  {clip(h['text'], 120)}")
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

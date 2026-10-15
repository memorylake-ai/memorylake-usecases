#!/usr/bin/env python3
"""
Game world memory — a MemoryLake demo driven entirely by the `memorylake` CLI
(https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Lanternfish Games (a fictional studio) ships Ashford, an RPG whose NPCs speak
from memory.  The game writes into MemoryLake at three scopes, as the use case
page describes: world canon in one project (lore, rules, the quest table), the
player's own event log on one actor, and what each NPC has seen or been told on
that NPC's actor.  Every record is pinned word for word (`fact add`), with its
scope, session and source in metadata.

In session 2 the player, Rook, told Mira the innkeeper that they were the King's
envoy, to get a night at the Gilded Goose for free.  Mira believed it, and her
actor says so: "Rook is the King's envoy".  That is a true record of what Mira
believes, and false about the world.

Session 4 starts.  Four callers need context about Rook: Captain Doran at the
north gate, Wren on the ridge, Mira at the inn, and the quest system.  The quick
read is one `search` across every scope — "what does the world know about
Rook?" — and the same reply code for everyone.  Doran has never heard the envoy
story, but his context holds it, and the world's denial too.  The memory read
uses the same reply code with one change: each caller reads only its own
scope (an NPC its actor; the quest system the canon and the player's log).
The quest system is the control: it is entitled to the whole truth, and both
reads agree.

Step 6 plays the gossip: Doran drinks at the Goose after his watch and tells
Mira the King has sent no envoy.  Written to Mira's actor, it contradicts what
she believes, and MemoryLake's contradiction check raises a conflict on her
actor (`fact conflict list --actor`).  Resolving it with `keep_fact` forgets the
belief, and the conflict keeps its text (`fact_snapshots`) — so Mira's next line
remembers being lied to.

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

PREFIX = "mlu-gwm"  # memorylake-usecases / game-world-memory


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


def records(story: dict) -> dict[str, dict]:
    """Every record by key (W1, E2, M1 …), the gossip included."""
    out = {r["key"]: r for r in story["records"]}
    out[story["gossip"]["key"]] = story["gossip"]
    return out


def scope_name(story: dict, scope: str) -> str:
    if scope == "world":
        return "world canon"
    if scope == story["player"]["key"]:
        return f"{story['player']['name']}'s event log"
    return next(n["name"] for n in story["npcs"] if n["key"] == scope)


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null.
    m = dict(key=f"gwm:{r['key']}", record=r["key"], scope=r["scope"], session=r["session"], source=r["source"],
             asserts=r["asserts"])
    if r.get("quest"):
        m["quest"] = r["quest"]
    return m


# --------------------------------------------------------------------------- scopes

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


def find_project(cli: CLI, story: dict, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(story["world"]["key"]), "--by-custom-id", scoped=True, echo=echo)
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


def ensure_actor(cli: CLI, key: str, name: str, description: str, tags: str) -> tuple[str, bool]:
    actor = find_actor(cli, key, echo=False)
    new = actor is None
    if new:
        actor = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", name, "--description", description,
                        "--tags", tags)
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True, echo=new)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"], new


def flag(ids: dict, scope: str) -> tuple[str, str]:
    """The CLI flag and id for a scope: the world is a project, everyone else an actor."""
    return ("--project", ids["world"]) if scope == "world" else ("--actor", ids[scope])


def list_scope(cli: CLI, ids: dict, scope: str, echo: bool = True) -> list[dict]:
    f, i = flag(ids, scope)
    return pages(cli, ["fact", "list", f + "s", i], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """Pinned records by record key, metadata flattened in, plus fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            out[m["record"]] = dict(m, fid=f["id"], body=f.get("fact") or "")
    return out


def all_entries(cli: CLI, ids: dict, story: dict, echo: bool = False) -> dict[str, dict]:
    out = {}
    for scope in scopes(story):
        out.update(entries(list_scope(cli, ids, scope, echo=echo)))
    return out


def scopes(story: dict) -> list[str]:
    return ["world", story["player"]["key"], *[n["key"] for n in story["npcs"]]]


# --------------------------------------------------------------------------- step 2: the cast

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    w = story["world"]
    proj = find_project(cli, story, echo=False)
    if proj is None:
        proj = cli.run("proj", "create", "--name", w["name"], "--custom-id", cid(w["key"]), "--description",
                       w["description"], scoped=True)
    else:
        note("the world and the cast exist from an earlier run (pass --reset to start over)")
    ids = dict(world=proj["id"])
    rows = [dict(scope="world", kind="project", name=w["name"], id=proj["id"], holds="lore, rules, the quest table")]
    p = story["player"]
    ids[p["key"]], _ = ensure_actor(cli, p["key"], p["name"], p["description"], "player")
    rows.append(dict(scope=p["key"], kind="actor", name=f"{p['name']} ({p['role']})", id=ids[p["key"]],
                     holds="the game's event log of what the player did"))
    for n in story["npcs"]:
        ids[n["key"]], _ = ensure_actor(cli, n["key"], n["name"], n["description"], "npc")
        rows.append(dict(scope=n["key"], kind="actor", name=f"{n['name']} ({n['role']})", id=ids[n["key"]],
                         holds="what this NPC has seen or been told"))
    emit("text", "\n" + "\n".join(f"    {r['kind']:<8} {r['name']:<44} {r['id']}" for r in rows))
    emit("setup", "", rows=rows)
    return ids


# --------------------------------------------------------------------------- step 3: sessions 1-3

def pin(cli: CLI, ids: dict, scope: str, items: list[dict], live: dict[str, dict], echo: bool = True) -> list[str]:
    """Pin the records a scope does not hold yet (matched by metadata.record): one `fact add` for all of them, then
    `fact update --metadata` on each (fact add takes no metadata). Returns the keys pinned now."""
    missing = [r for r in items if r["key"] not in live]
    if not missing:
        return []
    f, i = flag(ids, scope)
    res = cli.run("fact", "add", f, i, *[r["text"] for r in missing], scoped=True, echo=echo) or {}
    got = {x.get("fact"): x.get("id") for x in res.get("facts") or []}
    for n, r in enumerate(missing):
        fid = got.get(r["text"])
        if not fid:
            die(f"`fact add` did not return “{clip(r['text'], 60)}”")
        cli.run("fact", "update", fid, f, i, "--metadata", json.dumps(record_meta(r)), scoped=True, echo=echo and n == 0)
        live[r["key"]] = dict(record_meta(r), fid=fid, body=r["text"])
    return [r["key"] for r in missing]


def replay_session_four(cli: CLI, ids: dict, story: dict, live: dict[str, dict]) -> bool:
    """Every run starts the morning of session 4: the gossip an earlier run wrote to Mira is taken back (the belief it
    made her forget is pinned again below)."""
    g = story["gossip"]
    e = live.get(g["key"])
    if not e:
        return False
    f, i = flag(ids, g["scope"])
    cli.run("fact", "delete", f, i, e["fid"], scoped=True)
    del live[g["key"]]
    note("an earlier run played the gossip; taken back, so this run starts from the same morning")
    return True


def sessions(cli: CLI, ids: dict, story: dict) -> dict:
    note("the game writes what happened to the scope it belongs to: canon to the world project, what Rook did to "
         "Rook's actor, what each NPC saw or was told to that NPC's actor — word for word, then `fact update --metadata`")
    mira = story["gossip"]["scope"]
    live_mira = entries(list_scope(cli, ids, mira, echo=False))
    replayed = replay_session_four(cli, ids, story, live_mira)
    pinned, held = [], {}
    for scope in scopes(story):
        live = live_mira if scope == mira else entries(list_scope(cli, ids, scope, echo=False))
        items = [r for r in story["records"] if r["scope"] == scope]
        pinned += pin(cli, ids, scope, items, live, echo=not pinned)
        held[scope] = [live[r["key"]] for r in items if r["key"] in live]
    if len(pinned) > 1:
        note(f"pinned {len(pinned)} record(s) this run; the rest were already there")
    # Read back what each scope holds, the way the readers will.
    lines, view = [], []
    for scope in scopes(story):
        got = entries(list_scope(cli, ids, scope, echo=scope == "world"))
        rows = [got[k] for k in sorted(got, key=lambda k: (got[k]["session"], k))]
        lines.append(f"\n  {scope_name(story, scope)} — {len(rows)} fact(s)")
        for r in rows:
            tag = f"   [{', '.join(r['asserts'])}]" if r.get("asserts") else ""
            lines.append(f"    {r['record']:<3} s{r['session']}  {clip(r['body'], 86)}{tag}")
        view.append(dict(scope=scope, name=scope_name(story, scope), rows=[public(r) for r in rows]))
    lines.append("\n  · only Mira's actor says Rook is the King's envoy; the canon and Doran say the King sent no one; "
                 "Rook's log says it was a lie")
    emit("text", "\n".join(lines))
    emit("sessions", "", scopes=view, pinned=pinned, replayed=replayed)
    return dict(pinned=pinned, replayed=replayed)


PUBLIC = ("record", "scope", "session", "source", "asserts", "quest", "fid", "body", "rank")


def public(r: dict | None) -> dict | None:
    return None if r is None else {k: r.get(k) for k in PUBLIC}


# --------------------------------------------------------------------------- the reply code (shared by both reads)

def caller(story: dict, key: str) -> dict:
    return next(c for c in story["callers"] if c["key"] == key)


def own_scopes(story: dict, c: dict) -> list[str]:
    """What a caller is entitled to read: an NPC its own actor; the quest system the canon and the player's log."""
    return ["world", story["player"]["key"]] if c["kind"] == "quest" else [c["key"]]


def reply(story: dict, c: dict, recs: list[dict], forgotten: list[dict] = ()) -> dict:
    """The one function both reads use: what this caller says (or does) given the records in its context.
    `forgotten` are records a resolved conflict made this caller forget (read back from its snapshots)."""
    tags = set()
    for r in recs:
        tags |= set(r.get("asserts") or [])
    gone = set()
    for r in forgotten:
        gone |= set(r.get("asserts") or [])
    L = story["lines"]
    if c["kind"] == "quest":
        if "lie" in tags:
            key, said = "liar", "Rook is not the King's envoy (Rook lied to Mira) → offer “The Liar at the Goose”"
        elif "envoy" in tags and "no-envoy" not in tags:
            key, said = "writ", "Rook is the King's envoy → offer “The Envoy's Writ” (Doran hands over the writ)"
        else:
            key, said = "none", "nothing on file about Rook's title → no title quest"
        return dict(key=key, said=said, short=key, tags=sorted(tags))
    if "envoy" in gone and "no-envoy" in tags:
        stance = "was_told"
    elif "envoy" in tags and "no-envoy" in tags:
        stance = "calls_out"
    elif "envoy" in tags:
        stance = "defers"
    else:
        stance = ""
    personal = [t for t in story["personal"] if t in tags]
    parts = [L[stance]] if stance else []
    for t in personal:
        parts.append(L["room_after"] if t == "room" and stance == "was_told" else L[t])
    if not parts:
        parts.append(L["stranger"])
    npc = next(n for n in story["npcs"] if n["key"] == c["key"])
    said = " ".join(parts + [npc["sign_off"]])
    short = (stance or "plain") + (" + " + ", ".join(personal) if personal else "")
    return dict(key=f"{stance}|{','.join(personal)}", said=said, short=short, tags=sorted(tags), stance=stance,
                personal=personal)


def leaks(story: dict, c: dict, recs: list[dict]) -> list[dict]:
    """Records in a caller's context that came from a scope it cannot know, and that change what it says."""
    mine = set(own_scopes(story, c))
    return [r for r in recs if r["scope"] not in mine and r.get("asserts")]


# --------------------------------------------------------------------------- step 4: the quick read

def search(cli: CLI, story: dict, ids: dict, q: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    actors = ",".join(ids[s] for s in scopes(story) if s != "world")
    res = cli.run("search", q, "--projects", ids["world"], "--actors", actors, "--types", "fact", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    out = []
    for h in res.get("facts") or []:
        m = h.get("metadata") or {}
        out.append(dict(m, fid=h.get("id"), body=h.get("fact") or "", rank=h.get("rank")))
    return out


def hit_line(story: dict, h: dict, c: dict) -> str:
    mine = h.get("scope") in own_scopes(story, c)
    where = scope_name(story, h["scope"]) if h.get("scope") else "?"
    tag = "" if mine or not h.get("asserts") else "   ← not theirs to know"
    return f"    #{h.get('rank')} {h.get('record', '?'):<3} {where:<20} {clip(h['body'], 66)}{tag}"


def quick_read(cli: CLI, story: dict, ids: dict, c: dict, live_ids: set[str], q: str | None = None,
               echo: bool = True) -> dict:
    hits = search(cli, story, ids, q or c["query"], echo=echo)
    recs = [h for h in hits if h.get("record")]
    ans = reply(story, c, recs)
    cached = [h["fid"] for h in hits if h.get("fid") not in live_ids]
    return dict(caller=c, query=q or c["query"], hits=[public(h) for h in hits], ans=ans,
                leaks=[public(r) for r in leaks(story, c, recs)], cached=cached)


def quick_review(cli: CLI, story: dict, ids: dict) -> list[dict]:
    note("the quick read: one `search` across every scope — “what does the world know about Rook?” — then the same "
         "reply code for every caller")
    live = all_entries(cli, ids, story)
    live_ids = {e["fid"] for e in live.values()}
    out = []
    for i, c in enumerate(story["callers"]):
        r = quick_read(cli, story, ids, c, live_ids, echo=True)
        lines = [f"\n  {c['label']} — {c['scene']}"]
        lines += [hit_line(story, h, c) for h in r["hits"]]
        if r["leaks"]:
            lines.append(f"  · {len(r['leaks'])} of the hits come from a scope {c['label'].split(' at ')[0].split(' on ')[0]} "
                         f"cannot know: {', '.join(x['record'] for x in r['leaks'])}")
        if r["cached"]:
            lines.append(f"  · {len(r['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        lines.append(f"  → {r['ans']['said']}")
        emit("text", "\n".join(lines))
        out.append(r)
        if i == 0 and len(story["callers"]) > 1:
            note("… the same `search`, across the same scopes, for the other callers")
    emit("quick", "", reads=out)
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_read(cli: CLI, story: dict, ids: dict, c: dict, forgotten: list[dict] = (), echo: bool = True) -> dict:
    recs = []
    for s in own_scopes(story, c):
        recs += list(entries(list_scope(cli, ids, s, echo=echo)).values())
    recs.sort(key=lambda r: (r["session"], r["record"]))
    return dict(caller=c, scopes=own_scopes(story, c), recs=[public(r) for r in recs],
                ans=reply(story, c, recs, forgotten))


def memory_review(cli: CLI, story: dict, ids: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: the same reply code, but each caller reads only its own scope — an NPC its actor "
         "(`fact list --actors`), the quest system the canon and Rook's log")
    rows = []
    for q in quick:
        c = q["caller"]
        m = memory_read(cli, story, ids, c)
        agree = q["ans"]["key"] == m["ans"]["key"]
        rows.append(dict(caller=c, quick=q["ans"], memory=m["ans"], agree=agree, recs=m["recs"], scopes=m["scopes"],
                         leaks=q["leaks"]))
    lines = [f"\n  {'caller':<32} {'quick read (every scope)':<26} {'memory read (own scope)':<26}", "  " + "-" * 90]
    for r in rows:
        lines.append(f"  {r['caller']['label']:<32} {r['quick']['short']:<26} {r['memory']['short']:<26} "
                     f"{'✓ same' if r['agree'] else '✗ differs'}")
    for r in rows:
        c = r["caller"]
        lines.append(f"\n  {c['label']} reads {' + '.join(scope_name(story, s) for s in r['scopes'])} "
                     f"({len(r['recs'])} fact(s))")
        lines.append(f"    quick read:  {r['quick']['said']}")
        lines.append(f"    memory read: {r['memory']['said']}")
    n = sum(1 for r in rows if not r["agree"])
    lines.append(f"\n  · {n} of {len(rows)} callers say something different. Every ✗ is someone else's memory read as "
                 "the world's: Mira's belief, Doran's orders, Rook's own log. The quest system is entitled to the truth "
                 "of the world, and its two reads agree.")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=rows)
    return rows


# --------------------------------------------------------------------------- step 6: the gossip

def list_conflicts(cli: CLI, actor_id: str, *extra: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "conflict", "list", "--actor", actor_id, *extra], size=50, echo=echo)


def gossip(cli: CLI, story: dict, ids: dict) -> dict:
    g = story["gossip"]
    mira = g["scope"]
    allrec = records(story)
    emit("text", f"\n  {g['event']}")
    live = entries(list_scope(cli, ids, mira, echo=False))
    pinned = pin(cli, ids, mira, [g], live)
    gid = live[g["key"]]["fid"]
    belief = live.get("M1")
    t0 = time.time()
    note("written to Mira's actor; MemoryLake checks each new fact against what the actor already holds")
    found, first = None, True
    while time.time() - t0 < 150:
        for c in list_conflicts(cli, ids[mira], "--resolved", "false", echo=first):
            if gid in (c.get("fact_ids") or []):
                found = c
        first = False
        if found:
            break
        time.sleep(5)
    waited = int(time.time() - t0)
    if not found:
        note(f"no conflict naming the gossip after {waited}s — the check sometimes answers minutes later, as a batch; "
             "see `python3 demo.py conflicts`")
        out = dict(conflict=None, waited=waited, pinned=pinned)
        emit("gossip", "", **out)
        return out
    snap = {s.get("fact_id"): s.get("fact_text") for s in found.get("fact_snapshots") or []}
    by_fid = {e["fid"]: e for e in live.values()}
    named = [dict(fid=f, record=(by_fid.get(f) or {}).get("record", "?"), text=snap.get(f) or (by_fid.get(f) or {}).get("body"))
             for f in found.get("fact_ids") or []]
    lines = [f"\n  `fact conflict list --actor` after {waited}s: [{found.get('category')} · {found.get('conflict_type')}] "
             f"“{found.get('name')}”  {found['id']}"]
    lines += [f"    {n['record']:<3} {clip(n['text'], 90)}" for n in named]
    extra = [n for n in named if n["fid"] not in (gid, (belief or {}).get("fid"))]
    if extra:
        lines.append(f"  · the conflict also names {', '.join(n['record'] for n in extra)}: keep_fact forgets every fact in it "
                     "but the one kept, so those are pinned again after the resolution")
    emit("text", "\n".join(lines))
    r = cli.run("fact", "conflict", "resolve", found["id"], "--actor", ids[mira], "--strategy", "keep_fact",
                "--keep-fact-id", gid, scoped=True) or {}
    gone_ids = r.get("forgotten_fact_ids") or []
    note(f"resolved with keep_fact: kept what Doran said, forgot {', '.join(by_fid.get(f, {}).get('record', f) for f in gone_ids) or 'nothing'}")
    time.sleep(2)
    full = cli.run("fact", "conflict", "get", found["id"], "--actor", ids[mira], scoped=True) or {}
    snaps = {s.get("fact_id"): s.get("fact_text") for s in full.get("fact_snapshots") or []}
    # Mira's own records only: the gossip has the same words as the canon's W1.
    text_to_key = {r_["text"]: k for k, r_ in allrec.items() if r_["scope"] == mira}
    forgotten = []
    for f in (full.get("resolve") or {}).get("forgotten_fact_ids") or gone_ids:
        k = text_to_key.get(snaps.get(f) or "")
        if k:
            forgotten.append(dict(allrec[k], record=k, fid=f, body=snaps.get(f)))
    # Anything else the resolution forgot is not part of the story: put it back.
    now = entries(list_scope(cli, ids, mira, echo=False))
    repinned = pin(cli, ids, mira, [allrec[x["record"]] for x in forgotten if x["record"] != "M1"], now, echo=True)
    after = entries(list_scope(cli, ids, mira))
    lines = [f"\n  Mira's actor now ({len(after)} fact(s)): " + ", ".join(sorted(after)),
             f"  `fact conflict get` still holds what she was told: "
             + "; ".join(f"{x['record']} “{clip(x['body'], 70)}”" for x in forgotten)]
    emit("text", "\n".join(lines))
    reads = []
    for c in story["callers"]:
        fg = [dict(x, asserts=x["asserts"]) for x in forgotten] if c["key"] == mira else []
        reads.append(memory_read(cli, story, ids, c, fg, echo=False))
    lines = [f"\n  Session 4, after the gossip — memory read, own scope:"]
    for m in reads:
        lines.append(f"    {m['caller']['label']:<32} {m['ans']['said']}")
    emit("text", "\n".join(lines))
    out = dict(conflict=dict(id=found["id"], name=found.get("name"), category=found.get("category"),
                             conflict_type=found.get("conflict_type"), facts=named),
               waited=waited, kept=gid, forgotten=[public(x) for x in forgotten], repinned=repinned,
               mira=[public(x) for x in after.values()], after=[dict(caller=m["caller"], ans=m["ans"]) for m in reads],
               pinned=pinned)
    emit("gossip", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: npc, ask, conflicts

def need_ids(cli: CLI) -> dict:
    story = load_story()
    proj = find_project(cli, story, echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    ids = dict(world=proj["id"])
    for s in scopes(story)[1:]:
        a = find_actor(cli, s, echo=False)
        if a is None:
            die(f"actor {cid(s)} is missing; run `python3 demo.py --reset`")
        ids[s] = a["id"]
    return ids


def forgotten_for(cli: CLI, story: dict, ids: dict, key: str) -> list[dict]:
    """What a resolved conflict made this NPC forget, read back from the conflict's snapshots."""
    if key != story["gossip"]["scope"]:
        return []
    allrec = records(story)
    text_to_key = {r["text"]: k for k, r in allrec.items() if r["scope"] == key}
    live = entries(list_scope(cli, ids, key, echo=False))
    if story["gossip"]["key"] not in live:
        return []
    gid = live[story["gossip"]["key"]]["fid"]
    out = []
    for c in list_conflicts(cli, ids[key], "--resolved", "true", echo=False):
        if gid not in (c.get("fact_ids") or []):
            continue
        full = cli.run("fact", "conflict", "get", c["id"], "--actor", ids[key], scoped=True) or {}
        snaps = {s.get("fact_id"): s.get("fact_text") for s in full.get("fact_snapshots") or []}
        for f in (full.get("resolve") or {}).get("forgotten_fact_ids") or []:
            k = text_to_key.get(snaps.get(f) or "")
            if k and k not in live:
                out.append(dict(allrec[k], record=k, fid=f, body=snaps.get(f)))
    return out


def npc_view(cli: CLI, ids: dict, key: str) -> dict:
    story = load_story()
    c = caller(story, key)
    fg = forgotten_for(cli, story, ids, key)
    m = memory_read(cli, story, ids, c, fg)
    return dict(caller=c, recs=m["recs"], forgotten=[public(x) for x in fg], ans=m["ans"])


def ask_text(cli: CLI, ids: dict, text: str, key: str, echo: bool = True) -> dict:
    """Any question from any caller: the quick read (search, every scope) and the memory read (own scope)."""
    story = load_story()
    c = caller(story, key)
    live = all_entries(cli, ids, story)
    q = quick_read(cli, story, ids, c, {e["fid"] for e in live.values()}, q=text, echo=echo)
    fg = forgotten_for(cli, story, ids, key)
    m = memory_read(cli, story, ids, c, fg, echo=False)
    return dict(caller=c, query=text, hits=q["hits"], leaks=q["leaks"], cached=q["cached"], quick=q["ans"],
                memory=m["ans"], agree=q["ans"]["key"] == m["ans"]["key"])


def conflicts_view(cli: CLI, ids: dict) -> list[dict]:
    story = load_story()
    mira = story["gossip"]["scope"]
    out = []
    for c in list_conflicts(cli, ids[mira]):
        snaps = c.get("fact_snapshots") or []
        out.append(dict(id=c["id"], name=c.get("name"), category=c.get("category"), conflict_type=c.get("conflict_type"),
                        resolved=c.get("resolved"), stale=c.get("stale"), created_at=c.get("created_at"),
                        facts=[clip(s.get("fact_text") or "", 90) for s in snaps]))
    return out


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    def facts() -> list:
        out, token = [], None
        while True:
            args = ["fact", "list", "--actors", actor_id, "--page-size", "50"]
            if token:
                args += ["--continuation-token", token]
            page = cli.run(*args, scoped=True, echo=False) or {}
            out.extend(page.get("items") or [])
            token = page.get("continuation_token")
            if not token:
                return out

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
    if found:
        note(f"deleted {len(found)} fact(s) on {actor_id} before the actor")
    return len(found)


def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    n = 0
    for s in scopes(story)[1:]:
        a = find_actor(cli, s, echo=not quiet)
        if a:
            delete_actor(cli, a["id"])
            n += 1
    proj = find_project(cli, story, echo=not quiet)
    if proj:
        facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        for f in facts:
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        note(f"deleted the world project and its {len(facts)} facts")
    if not proj and not n:
        note("nothing to delete")
    elif n:
        note(f"deleted {n} actor(s) (their facts first)")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, f"The cast — the world, the player and {len(story['npcs'])} NPCs, each its own scope")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Sessions 1–3 — what happened, written to the scope it belongs to")
    ses = sessions(cli, ids, story)
    banner(4, TOTAL, "The quick read — search every scope for what the world knows about Rook")
    quick = quick_review(cli, story, ids)
    banner(5, TOTAL, "The memory read — each caller reads only the scope it is entitled to")
    rows = memory_review(cli, story, ids, quick)
    banner(6, TOTAL, "Gossip — Doran tells Mira the King sent no one; a conflict on her actor")
    g = gossip(cli, story, ids)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   pinned=len(ses["pinned"]), replayed=ses["replayed"],
                   hits={r["caller"]["key"]: [h["record"] for h in r["hits"]] for r in quick},
                   leaks={r["caller"]["key"]: [x["record"] for x in r["leaks"]] for r in quick},
                   quick={x["caller"]["key"]: x["quick"]["short"] for x in rows},
                   memory={x["caller"]["key"]: x["memory"]["short"] for x in rows},
                   agree=[x["caller"]["key"] for x in rows if x["agree"]],
                   cached=[c for r in quick for c in r["cached"]],
                   conflict=bool(g.get("conflict")), waited=g.get("waited"),
                   conflict_facts=[f["record"] for f in (g.get("conflict") or {}).get("facts", [])],
                   forgotten=[x["record"] for x in g.get("forgotten") or []],
                   after={a["caller"]["key"]: a["ans"]["short"] for a in g.get("after") or []})
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    story = load_story()
    keys = [c["key"] for c in story["callers"]]
    npcs = [n["key"] for n in story["npcs"]]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "npc", "ask", "conflicts", "cleanup"],
                    help="run = full demo (default); npc NAME = what that NPC says now, and from which facts; "
                         "ask \"QUESTION\" --as CALLER = the quick read and the memory read of any question; "
                         "conflicts = the conflicts on Mira's actor; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the NPC (npc) or the question (ask)")
    ap.add_argument("--as", dest="as_", default="doran", choices=keys, help="ask: who is asking")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one question, e.g. ask \"Who is the traveller at the gate?\" --as doran")
    if a.command == "npc" and (len(a.args) != 1 or a.args[0] not in npcs):
        ap.error(f"npc needs one of: {', '.join(npcs)}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py npc mira`, `python3 demo.py ask \"Who is the traveller at the gate?\" --as doran`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "npc":
        banner(2, 2, f"NPC — what {a.args[0]} says now, and from which facts")
        v = npc_view(cli, ids, a.args[0])
        lines = [f"\n  {v['caller']['label']} reads {len(v['recs'])} fact(s) on their own actor:"]
        lines += [f"    {r['record']:<3} s{r['session']}  {clip(r['body'], 86)}" for r in v["recs"]]
        if v["forgotten"]:
            lines.append("  and remembers what a resolved conflict made them forget (`fact conflict get`, fact_snapshots):")
            lines += [f"    {r['record']:<3} s{r['session']}  {clip(r['body'], 86)}   (forgotten)" for r in v["forgotten"]]
        lines.append(f"\n  → {v['ans']['said']}")
        emit("text", "\n".join(lines))
    elif a.command == "ask":
        banner(2, 2, "Ask — the quick read and the memory read")
        r = ask_text(cli, ids, a.args[0], a.as_)
        c = r["caller"]
        lines = [f"\n  “{r['query']}” · asked as {c['label']} — top {len(r['hits'])} across every scope:"]
        lines += [hit_line(story, h, c) for h in r["hits"]]
        lines.append(f"  → quick read:  {r['quick']['said']}")
        lines.append(f"  → memory read: {r['memory']['said']}  {'✓ same' if r['agree'] else '✗ differs'}")
        emit("text", "\n".join(lines))
    elif a.command == "conflicts":
        banner(2, 2, "Conflicts — on Mira's actor")
        cs = conflicts_view(cli, ids)
        lines = [f"\n  {len(cs)} conflict(s):"] if cs else ["\n  no conflicts on Mira's actor"]
        for c in cs:
            state = "resolved" if c["resolved"] else "open"
            lines.append(f"\n  [{c['category']} · {c['conflict_type']}] “{c['name']}”  {c['id']}  {state}"
                         f"{' · stale' if c['stale'] else ''}")
            lines += [f"    {t}" for t in c["facts"]]
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

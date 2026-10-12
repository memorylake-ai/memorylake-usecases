#!/usr/bin/env python3
"""
Memory scopes for MCP tools — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Fernhill Robotics' platform team connects three AI tools to MemoryLake: Priya's
editor assistant, Tom's editor assistant and a CI review bot.  Each tool gets
one "memory store identifier" — a boundary: a saved scope of projects, one
person and one agent.  MCP clients pick a boundary on the OAuth consent
screen; Memory Router takes it as `?boundary_id=`.

The same question goes through all three boundaries and comes back with three
different answers, none of them reaching outside its scope.  Then the team
changes, the way teams do: a project is finished and deleted, Tom moves to
another team, the review bot is replaced.  Every boundary still points at what
is gone: the CI bot's scope now makes every search fail, and Tom's and the
bot's boundaries refuse any edit, even a rename.  An audit checks every
reference against what exists today, the boundaries are repaired (Ana takes
Tom's seat, review-bot-v2 takes the bot's), and the same question works again.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-msc"  # memorylake-usecases / mcp-scope-memory



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
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def by_key(items: list[dict], key: str) -> dict:
    return next(i for i in items if i["key"] == key)


def list_all(cli: CLI, *args: str, scoped: bool = False, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", "50"]
        if token:
            a += ["--continuation-token", token]
        page = cli.run(*a, scoped=scoped, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return list_all(cli, "fact", "list", flag, scope_id, scoped=True, echo=echo)


def attempt(cli: CLI, *args: str, scoped: bool = False) -> dict:
    """Run a command that may fail; return its status line instead of raising."""
    rc, out, err = cli.raw(*args, scoped=scoped)
    m = re.search(r"HTTP (\d{3})", err)
    msg = re.search(r'"message":"([^"]+)"', err)
    code = re.search(r'"error_code":"([^"]+)"', err)
    last = (err.strip().splitlines() or [""])[-1].strip()
    res = dict(rc=rc, status=int(m.group(1)) if m else None, code=code.group(1) if code else None,
               message=(msg.group(1) if msg else last) if rc else "")
    if rc == 0:
        try:
            res["json"] = json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            res["json"] = None
    return res


def failure(r: dict) -> str:
    return f"HTTP {r['status']} {r['code'] or ''} — {r['message']}".replace("  ", " ")


# --------------------------------------------------------------------------- step 2: setup

def find_by_custom_id(cli: CLI, kind: str, key: str, echo: bool = True):
    try:
        return cli.run(kind, "get", cid(key), "--by-custom-id", scoped=kind == "proj", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def bind_actor(cli: CLI, actor_id: str) -> None:
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise


def is_bound(cli: CLI, actor_id: str) -> bool:
    # A sealed (unbound) or deleted actor answers 404 to `fact list`.
    rc, out, err = cli.raw("fact", "list", "--actors", actor_id, "--page-size", "1", scoped=True, echo=False)
    if rc == 0:
        return True
    if "404" in err or "not bound" in err.lower() or "not found" in err.lower():
        return False
    raise CLIError(["fact", "list", "--actors", actor_id], rc, out, err)


def pin(cli: CLI, flag: str, scope_id: str, texts: list[str], echo: bool = True) -> int:
    """`fact add` whichever of these texts the scope does not hold yet; returns how many were added."""
    have = {f.get("fact") for f in list_facts(cli, flag + "s", scope_id, echo=False)}
    missing = [t for t in texts if t not in have]
    for i, t in enumerate(missing):
        cli.run("fact", "add", flag, scope_id, t, scoped=True, echo=echo and i == 0)
    return len(missing)


def ensure_agent(cli: CLI, a: dict) -> dict:
    agent = find_by_custom_id(cli, "agent", a["key"])
    if agent is None:
        agent = cli.run("agent", "create", "--name", a["name"], "--custom-id", cid(a["key"]),
                        "--description", a["description"])
        note(f"created the agent {a['name']} → {agent['id']} (its own actor: {agent['actor_id']})")
    try:
        cli.run("agent", "bind", agent["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return agent


def leftovers(cli: CLI, story: dict) -> list[str]:
    found = []
    for p in story["projects"]:
        if find_by_custom_id(cli, "proj", p["key"], echo=False):
            found.append(f"project {p['name']}")
    for p in story["people"]:
        if find_by_custom_id(cli, "actor", p["key"], echo=False):
            found.append(f"actor {p['display']}")
    for a in story["agents"]:
        if find_by_custom_id(cli, "agent", a["key"], echo=False):
            found.append(f"agent {a['name']}")
    found += [f"boundary {b['name']}" for b in demo_boundaries(cli, echo=False)]
    return found


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    left = leftovers(cli, story)
    if left:
        # The story changes its boundaries for good (step 5 breaks them, step 6 repairs them), so a
        # second run starts from a clean slate instead of resuming half-way.
        note(f"found what an earlier run left ({len(left)}: {', '.join(left[:4])}{'…' if len(left) > 4 else ''}) — "
             "cleaning it up and starting over")
        cleanup(cli, quiet=True)
    elif reset:
        note("--reset: nothing to delete, starting fresh")
    ids: dict = {"names": {}}
    for p in story["projects"]:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                       "--description", p["description"], scoped=True)
        ids[p["key"]] = proj["id"]
        ids["names"][proj["id"]] = p["name"]
    note(f"created {len(story['projects'])} projects: " + ", ".join(p["name"] for p in story["projects"]))
    for p in story["people"]:
        actor = cli.run("actor", "create", "--custom-id", cid(p["key"]), "--display-name", p["display"],
                        "--description", p["role"], "--tags", "demo,platform")
        ids[p["key"]] = actor["id"]
        ids["names"][actor["id"]] = p["display"]
        if p.get("bind", True):
            bind_actor(cli, actor["id"])
    note("created the people; " + ", ".join(p["display"] for p in story["people"] if p.get("bind", True))
         + " are bound to the workspace, " + ", ".join(p["display"] for p in story["people"] if not p.get("bind", True))
         + " is not yet")
    for a in story["agents"]:
        if a.get("later"):
            continue
        ag = ensure_agent(cli, a)
        ids[a["key"]], ids[a["key"] + "_actor"] = ag["id"], ag["actor_id"]
        ids["names"][ag["id"]] = a["name"]
        ids["names"][ag["actor_id"]] = a["name"]

    # One `fact add` per scope is echoed; the rest go the same way.
    n = sum(pin(cli, "--project", ids[p["key"]], p["facts"], echo=i == 0) for i, p in enumerate(story["projects"]))
    n += sum(pin(cli, "--actor", ids[p["key"]], p["facts"], echo=i == 0)
             for i, p in enumerate(x for x in story["people"] if x["facts"]))
    n += sum(pin(cli, "--agent", ids[a["key"]], a["facts"]) for a in story["agents"] if a["facts"] and a["key"] in ids)
    note(f"pinned {n} facts with `fact add`: project notes on the projects, preferences on each person, lessons on the bot")
    emit("setup", "", projects=[dict(key=p["key"], name=p["name"], id=ids[p["key"]], facts=p["facts"]) for p in story["projects"]],
         people=[dict(key=p["key"], name=p["display"], role=p["role"], id=ids[p["key"]], bound=p.get("bind", True),
                      facts=p["facts"]) for p in story["people"]],
         agents=[dict(key=a["key"], name=a["name"], id=ids[a["key"]], actor_id=ids[a["key"] + "_actor"], facts=a["facts"])
                 for a in story["agents"] if a["key"] in ids])
    return ids


# --------------------------------------------------------------------------- step 3: one boundary per tool

def demo_boundaries(cli: CLI, echo: bool = True) -> list[dict]:
    # `boundary get --by-custom-id` is not available; list and match the custom_id.
    return [b for b in list_all(cli, "boundary", "list", scoped=True, echo=echo)
            if (b.get("custom_id") or "").startswith(PREFIX + "-")]


def find_boundary(cli: CLI, key: str, echo: bool = False):
    return next((b for b in demo_boundaries(cli, echo=echo) if b.get("custom_id") == cid(key)), None)


def scope_args(b: dict, ids: dict, story: dict) -> list[str]:
    args = []
    if b.get("projects"):
        args += ["--projects", ",".join(ids[k] for k in b["projects"])]
    if b.get("human"):
        args += ["--human-actor", ids[b["human"]]]
    if b.get("agent"):
        args += ["--agent", ids[b["agent"]]]
    return args


def describe(bnd: dict, ids: dict) -> str:
    names = ids["names"]
    projects = ", ".join(names.get(p, p) for p in bnd.get("project_ids") or []) or "(no project scoping)"
    human = names.get(bnd.get("human_actor_id") or "", bnd.get("human_actor_id") or "—")
    agent = names.get(bnd.get("agent_id") or "", bnd.get("agent_id") or "—")
    return f"projects: {projects} · person: {human} · agent: {agent}"


def boundaries(cli: CLI, ids: dict, story: dict) -> dict:
    made = []
    for b in story["boundaries"]:
        bnd = cli.run("boundary", "create", "--name", b["name"], "--custom-id", cid(b["key"]),
                      *scope_args(b, ids, story), scoped=True)
        ids[b["key"]] = bnd["id"]
        made.append(dict(key=b["key"], tool=b["tool"], **bnd))
    lines = ["\n  One memory store identifier per tool:"]
    for m in made:
        lines.append(f"   {m['id']}  {m['name']}  ← {m['tool']}")
        lines.append(f"     {describe(m, ids)}")
        if m.get("assistant_actor_id"):
            lines.append(f"     assistant_actor_id {m['assistant_actor_id']} (added by the server: the agent's own actor)")
    bad = story["bad_boundary"]
    lines.append(f"\n  A boundary is checked when it is written. {by_key(story['people'], bad['human'])['display']} is not on the workspace yet:")
    emit("text", "\n".join(lines))
    r = attempt(cli, "boundary", "create", "--name", bad["name"], "--custom-id", cid(bad["key"]),
                *scope_args(bad, ids, story), scoped=True)
    if r["rc"] == 0:
        # Not expected; do not leave it behind.
        cli.run("boundary", "delete", r["json"]["id"])
        emit("text", "   ✗ the server accepted it (unexpected) — deleted it again")
    else:
        emit("text", f"   ✓ refused: {failure(r)}")
    res = dict(boundaries=made, refused=r)
    emit("boundaries", "", **res)
    return res


# --------------------------------------------------------------------------- step 4: the same question through each boundary

def owners(cli: CLI, ids: dict, story: dict) -> dict:
    """fact id → (scope key, label), from every scope the demo wrote to. Search hits carry no owner."""
    own = {}
    for p in story["projects"]:
        if ids.get(p["key"]) and ids.get("alive", {}).get(p["key"], True):
            for f in list_facts(cli, "--projects", ids[p["key"]], echo=False):
                own[f["id"]] = (p["key"], p["name"])
    for p in story["people"]:
        if p["facts"] and is_bound(cli, ids[p["key"]]):
            for f in list_facts(cli, "--actors", ids[p["key"]], echo=False):
                own[f["id"]] = (p["key"], p["display"])
    for a in story["agents"]:
        if a["key"] in ids and ids.get("alive", {}).get(a["key"], True):
            for f in list_facts(cli, "--agents", ids[a["key"]], echo=False):
                own[f["id"]] = (a["key"], a["name"])
    return own


def key_of(ids: dict, story: dict, any_id: str):
    for group in ("projects", "people", "agents"):
        for x in story[group]:
            if ids.get(x["key"]) == any_id or ids.get(x["key"] + "_actor") == any_id:
                return x["key"]
    return None


def expand(bnd: dict) -> list[str]:
    """A boundary as search flags: its projects, plus its person and its agent's actor."""
    args = []
    if bnd.get("project_ids"):
        args += ["--projects", ",".join(bnd["project_ids"])]
    actors = [a for a in (bnd.get("human_actor_id"), bnd.get("assistant_actor_id")) if a]
    if actors:
        args += ["--actors", ",".join(actors)]
    return args


def ask_through(cli: CLI, ids: dict, story: dict, key: str, own: dict, top_k: int = 8, q: str | None = None) -> dict:
    bnd = cli.run("boundary", "get", ids[key])
    scope = {key_of(ids, story, p) for p in bnd.get("project_ids") or []}
    scope |= {key_of(ids, story, a) for a in (bnd.get("human_actor_id"), bnd.get("agent_id")) if a}
    r = attempt(cli, "search", q or story["question"], *expand(bnd), "--types", "fact", "--top-k", str(top_k), scoped=True)
    if r["rc"]:
        return dict(key=key, name=bnd["name"], ok=False, error=failure(r), hits=[], outside=0, scope=sorted(s for s in scope if s))
    hits = []
    for f in (r["json"] or {}).get("facts") or []:
        owner = own.get(f.get("id"))
        hits.append(dict(id=f.get("id"), fact=f.get("fact") or "", owner=owner[1] if owner else "outside the demo",
                         inside=bool(owner) and owner[0] in scope))
    return dict(key=key, name=bnd["name"], ok=True, hits=hits, outside=sum(not h["inside"] for h in hits),
                scope=sorted(s for s in scope if s))


def show_answers(answers: list[dict], story: dict, header: str) -> str:
    lines = [f"\n  {header}", f"  Q: {story['question']}"]
    for a in answers:
        lines.append(f"\n  through “{a['name']}”:")
        if not a["ok"]:
            lines.append(f"   ✗ the search fails: {a['error']}")
            continue
        for h in a["hits"]:
            lines.append(f"   {'✓' if h['inside'] else '✗'} [{h['owner']}] {h['fact']}")
        if not a["hits"]:
            lines.append("   (nothing)")
        lines.append(f"   {len(a['hits'])} hit(s) · {a['outside']} from outside this boundary")
    return "\n".join(lines)


def three_tools(cli: CLI, ids: dict, story: dict, header: str, event: str = "answers") -> dict:
    own = owners(cli, ids, story)
    answers = [ask_through(cli, ids, story, b["key"], own) for b in story["boundaries"]]
    text = show_answers(answers, story, header)
    ok = [a for a in answers if a["ok"]]
    text += "\n\n  What each tool saw:"
    for a in answers:
        seen = sorted({h["owner"] for h in a["hits"]})
        text += f"\n   {a['name']:<28} " + (", ".join(seen) if a["ok"] else "(search failed)")
    text += (f"\n\n  {len(ok)} of {len(answers)} tools answered · "
             f"{sum(a['outside'] for a in ok)} hit(s) from outside a tool's boundary")
    emit("text", text)
    res = dict(question=story["question"], answers=answers)
    emit(event, "", **res)
    return res


# --------------------------------------------------------------------------- step 5: the team changes

def changes(cli: CLI, ids: dict, story: dict) -> dict:
    c = story["changes"]
    ids.setdefault("alive", {})
    note(c["migration"])
    cli.run("proj", "delete", ids["migration"], scoped=True)
    ids["alive"]["migration"] = False

    note(c["tom"])
    cli.run("actor", "unbind", "--actor", ids["tom"], scoped=True)

    note(c["bot"])
    v2 = ensure_agent(cli, by_key(story["agents"], "bot2"))
    ids["bot2"], ids["bot2_actor"] = v2["id"], v2["actor_id"]
    ids["names"][v2["id"]] = ids["names"][v2["actor_id"]] = "review-bot-v2"
    lessons = list_facts(cli, "--agents", ids["bot"])
    # A new agent starts with an empty memory (`agent fork` copies config, never memory): copy the lessons.
    copied = pin(cli, "--agent", ids["bot2"], [f["fact"] for f in lessons])
    # A deleted agent's memory stays readable but can no longer be deleted: facts first, then the agent.
    for f in lessons:
        cli.run("fact", "delete", f["id"], "--agent", ids["bot"], scoped=True, echo=False)
    if list_facts(cli, "--agents", ids["bot"], echo=False):
        die("review-bot still holds facts; not deleting it")
    cli.run("agent", "delete", ids["bot"])
    ids["alive"]["bot"] = False
    note(f"copied {copied} lesson(s) to review-bot-v2, deleted {len(lessons)} from review-bot, then review-bot")

    lines = ["\n  What the boundaries say now (`boundary get`):"]
    after = {}
    for b in story["boundaries"]:
        bnd = cli.run("boundary", "get", ids[b["key"]])
        after[b["key"]] = bnd
        lines.append(f"   {bnd['name']}: {describe(bnd, ids)}")
    lines.append("  Nothing was cascaded: the deleted project, the sealed person and the deleted agent are all still referenced.")
    emit("text", "\n".join(lines))

    probes = {}
    lines = ["\n  What that does to the tools:"]
    own = owners(cli, ids, story)
    bot = ask_through(cli, ids, story, "review-bot", own)
    probes["search"] = bot
    lines.append(f"   CI review bot, searching through its boundary → {'✗ ' + bot['error'] if not bot['ok'] else 'still answers'}")
    r = attempt(cli, "boundary", "update", ids["tom-ide"], "--name", "Tom's editor assistant (renamed)")
    probes["rename_tom"] = r
    lines.append(f"   rename Tom's boundary → {'✗ ' + failure(r) if r['rc'] else 'accepted'}")
    r = attempt(cli, "boundary", "update", ids["review-bot"], "--name", "CI review bot (renamed)")
    probes["rename_bot"] = r
    lines.append(f"   rename the CI bot's boundary → {'✗ ' + failure(r) if r['rc'] else 'accepted'}")
    lines.append("  A deleted project lingers silently and breaks every search that uses it; a sealed person or a deleted")
    lines.append("  agent locks the boundary — no edit is accepted until that reference is cleared or replaced.")
    emit("text", "\n".join(lines))
    res = dict(after=after, probes=probes)
    emit("changes", "", **res)
    return res


# --------------------------------------------------------------------------- step 6: audit and repair

def audit(cli: CLI, ids: dict, story: dict | None = None, echo_header: bool = True) -> dict:
    """Check every reference of every demo boundary against what exists today."""
    rows = []
    for bnd in demo_boundaries(cli, echo=echo_header):
        refs = []
        for p in bnd.get("project_ids") or []:
            ok = attempt(cli, "proj", "get", p, scoped=True)["rc"] == 0
            refs.append(dict(kind="project", id=p, name=ids["names"].get(p, p), ok=ok, why="" if ok else "project deleted"))
        if bnd.get("human_actor_id"):
            a = bnd["human_actor_id"]
            ok = is_bound(cli, a)
            refs.append(dict(kind="person", id=a, name=ids["names"].get(a, a), ok=ok, why="" if ok else "not bound to the workspace"))
        if bnd.get("agent_id"):
            g = bnd["agent_id"]
            ok = attempt(cli, "agent", "get", g)["rc"] == 0
            refs.append(dict(kind="agent", id=g, name=ids["names"].get(g, g), ok=ok, why="" if ok else "agent deleted"))
        rows.append(dict(id=bnd["id"], custom_id=bnd.get("custom_id"), name=bnd["name"], refs=refs,
                         ok=all(r["ok"] for r in refs)))
    lines = []
    for r in rows:
        lines.append(f"\n   {'✓' if r['ok'] else '✗'} {r['name']} ({r['id']})")
        for ref in r["refs"]:
            lines.append(f"      {'✓' if ref['ok'] else '✗'} {ref['kind']:<7} {ref['name']}" + (f"  — {ref['why']}" if ref["why"] else ""))
    bad = sum(not x["ok"] for r in rows for x in r["refs"])
    lines.append(f"\n   {len(rows)} boundaries · {bad} stale reference(s)")
    emit("text", "\n".join(lines))
    res = dict(rows=rows, stale=bad)
    emit("audit", "", **res)
    return res


def repair(cli: CLI, ids: dict, story: dict, report: dict) -> dict:
    fixes = []
    for row in report["rows"]:
        if row["ok"]:
            continue
        key = row["custom_id"][len(PREFIX) + 1:]
        plan = story["repairs"].get(key, {})
        args = []
        stale = [r for r in row["refs"] if not r["ok"]]
        if any(r["kind"] == "project" for r in stale):
            keep = [r["id"] for r in row["refs"] if r["kind"] == "project" and r["ok"]]
            # --projects REPLACES the list: name every project to keep.
            args += ["--projects", ",".join(keep)] if keep else ["--clear-projects"]
        if any(r["kind"] == "person" for r in stale):
            args += ["--human-actor", ids[plan["human"]]] if plan.get("human") else ["--clear-human-actor"]
        if any(r["kind"] == "agent" for r in stale):
            args += ["--agent", ids[plan["agent"]]] if plan.get("agent") else ["--clear-agent"]
        if plan.get("name"):
            args += ["--name", plan["name"]]
        bnd = cli.run("boundary", "update", row["id"], *args)
        fixes.append(dict(name=row["name"], now=bnd["name"], args=args, scope=describe(bnd, ids)))
    lines = ["\n  Repaired (one `boundary update` per boundary, same id — the tools keep their identifier):"]
    for f in fixes:
        lines.append(f"   {f['name']} → {f['now']}\n     {f['scope']}")
    emit("text", "\n".join(lines) if fixes else "\n  Nothing to repair.")
    res = dict(fixes=fixes)
    emit("repair", "", **res)
    return res


# --------------------------------------------------------------------------- step 7: after the repair

def after_repair(cli: CLI, ids: dict, story: dict) -> dict:
    res = three_tools(cli, ids, story, "The same question, through the repaired boundaries:", event="answers_after")
    tom = by_key(story["people"], "tom")
    tom_hits = [h for a in res["answers"] for h in a["hits"] if h["owner"] == tom["display"]]
    lines = [f"\n  {tom['display']}'s preferences in any answer: {len(tom_hits)} — his seat went to "
             f"{by_key(story['people'], 'ana')['display']}, his memory did not"]
    emit("text", "\n".join(lines))
    emit("text", "\n  Audit again:")
    report = audit(cli, ids, story, echo_header=False)
    OUT.mkdir(exist_ok=True)
    rows = [dict(id=b["id"], name=b["name"], custom_id=b.get("custom_id"), project_ids=b.get("project_ids"),
                 human_actor_id=b.get("human_actor_id"), agent_id=b.get("agent_id"),
                 assistant_actor_id=b.get("assistant_actor_id")) for b in demo_boundaries(cli, echo=False)]
    (OUT / "boundaries.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    note(f"the boundary ids are in {os.path.relpath(OUT / 'boundaries.json')} — Memory Router takes one as "
         "`?boundary_id=`; an MCP client picks one on the consent screen")
    res.update(audit=report, tom_hits=len(tom_hits))
    return res


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
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"])
    for a in story["agents"]:
        agent = find_by_custom_id(cli, "agent", a["key"], echo=not quiet)
        if agent:
            # A deleted agent's memory stays readable but can no longer be deleted: facts first.
            facts = list_facts(cli, "--agents", agent["id"], echo=False)
            for f in facts:
                cli.run("fact", "delete", f["id"], "--agent", agent["id"], scoped=True, echo=False)
            if list_facts(cli, "--agents", agent["id"], echo=False):
                die(f"{a['name']} still holds facts; not deleting it")
            cli.run("agent", "delete", agent["id"])
            note(f"deleted {len(facts)} fact(s) from {a['name']}, then the agent")
    for p in story["projects"]:
        proj = find_by_custom_id(cli, "proj", p["key"], echo=not quiet)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
    for p in story["people"]:
        actor = find_by_custom_id(cli, "actor", p["key"], echo=not quiet)
        if actor:
            delete_actor(cli, actor["id"])
    note("deleted the boundaries, agents, projects and people")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    banner(2, TOTAL, "Set up — four projects, four people, the review bot, their memories")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)

    banner(3, TOTAL, "One boundary per tool — the memory store identifier each MCP tool connects with")
    boundaries(cli, ids, story)

    banner(4, TOTAL, "One question, three tools — each answer stays inside its boundary")
    three_tools(cli, ids, story, "Each boundary expanded into a search: its projects, its person and its agent's actor.")

    banner(5, TOTAL, "The team changes — a project ends, Tom moves on, the bot is replaced")
    changes(cli, ids, story)

    banner(6, TOTAL, "Audit and repair — every reference checked against what exists today")
    report = audit(cli, ids, story)
    repair(cli, ids, story, report)

    banner(7, TOTAL, "After the repair — the same question works again")
    return after_repair(cli, ids, story)


def lookup_ids(cli: CLI, story: dict) -> dict:
    ids: dict = {"names": {}}
    for p in story["projects"]:
        x = find_by_custom_id(cli, "proj", p["key"], echo=False)
        if x:
            ids[p["key"]] = x["id"]
            ids["names"][x["id"]] = p["name"]
    for p in story["people"]:
        x = find_by_custom_id(cli, "actor", p["key"], echo=False)
        if x:
            ids[p["key"]] = x["id"]
            ids["names"][x["id"]] = p["display"]
    for a in story["agents"]:
        x = find_by_custom_id(cli, "agent", a["key"], echo=False)
        if x:
            ids[a["key"]], ids[a["key"] + "_actor"] = x["id"], x["actor_id"]
            ids["names"][x["id"]] = ids["names"][x["actor_id"]] = a["name"]
    for b in demo_boundaries(cli, echo=False):
        ids[(b.get("custom_id") or "")[len(PREFIX) + 1:]] = b["id"]
    return ids


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "scopes", "cleanup"],
                    help="run = full demo (default); scopes = audit every demo boundary against what exists today; "
                         "cleanup = delete everything")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = {"cleanup": 2, "scopes": 2}.get(args.command, TOTAL)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "scopes":
        banner(2, 2, "Audit — every demo boundary against what exists today")
        audit(cli, lookup_ids(cli, load_story()))
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py scopes` audits the boundaries again; `python3 demo.py cleanup` removes the demo data.")


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

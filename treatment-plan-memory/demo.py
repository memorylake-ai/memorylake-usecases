#!/usr/bin/env python3
"""
Treatment plan memory for solo practitioners — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Mira Lindqvist runs Harbour Line Physio (fictional) on her own.  Jonah Reyes,
54, has been coming in for his right shoulder since August.  His first plan
carried three precautions: no loaded overhead work until the orthopaedic
review clears it, no dry needling because he takes an anticoagulant, and a
pain ceiling of 3/10.

Plans v2 and v3 were drafted by an AI scribe from the previous plan and that
day's visit, then signed.  Each rewrite looked fine on its own.  Between them,
two of the three precautions fell out — nobody lifted them, they just stopped
being written down.  Mira even repeated both at later visits ("the overhead
restriction stays", "GP renewed apixaban").  Plan v3 now says: add overhead
dumbbell press and dry needling.

Mira goes on leave tomorrow.  Theo Adeyemi covers, and is told to follow the
current plan.  Read the current plan and his first session includes both
things the patient must not have.  Read every version and every visit, and
follow each precaution from the day it was imposed: was it ever lifted?  Two
were not.  The one that did change (the pain ceiling, 3/10 → 5/10) changed on
the record, with a reason, and the current plan carries the new value.

Everything is pinned word for word with `fact add`, tagged with metadata
(kind, date, version, precaution id, action).  No model reads anything: the
audit is plain code over one `fact list`.

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

PREFIX = "mlu-tpm"  # memorylake-usecases / treatment-plan-memory


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


def precautions(story: dict) -> dict[str, dict]:
    return {p["pid"]: p for p in story["precautions"]}


def who(story: dict, key: str) -> str:
    return story["clinicians"].get(key, key)


def entry_meta(e: dict) -> dict:
    """Everything but the text goes into metadata. No None values: the server drops keys whose value is null."""
    return {k: v for k, v in e.items() if k != "text" and v is not None}


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



def chart(facts: list[dict]) -> dict:
    """The pinned entries, metadata flattened in, keyed by metadata key."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("key"):
            out[m["key"]] = dict(m, id=f["id"], text=f.get("fact"))
    return out


def read_chart(cli: CLI, ids: dict, echo: bool = True) -> dict:
    """Jonah's whole chart, every page: plans, precaution events, visits, the handoff note."""
    return chart(list_facts(cli, actors=[ids["jonah"]], echo=echo))


def live_keys(facts: list[dict]) -> dict:
    return {(f.get("metadata") or {}).get("key"): f for f in facts if (f.get("metadata") or {}).get("key")}


# --------------------------------------------------------------------------- the audit (plain code, no model)

def plans(mem: dict, day: str) -> list[dict]:
    return sorted((e for e in mem.values() if e.get("kind") == "plan" and e["date"] <= day),
                  key=lambda e: e["version"])


def latest_plan(mem: dict, day: str) -> dict | None:
    ps = plans(mem, day)
    return ps[-1] if ps else None


def events(mem: dict, pid: str, day: str) -> list[dict]:
    """Everything said about one precaution up to `day`: impose / revise / reaffirm / lift, oldest first."""
    return sorted((e for e in mem.values() if e.get("pid") == pid and e.get("action") and e["date"] <= day),
                  key=lambda e: (e["date"], e["key"]))


def audit(story: dict, mem: dict, day: str) -> dict:
    """Follow every precaution from the day it was imposed through every plan version written since.

    In force = imposed and never lifted. The plan of `day` should carry every precaution in force, at its
    current value. One a later plan stopped carrying, with no lift on record, was dropped silently."""
    ps = plans(mem, day)
    cur = ps[-1] if ps else None
    rows = []
    for pid, p in precautions(story).items():
        ev = events(mem, pid, day)
        imposed = next((e for e in ev if e["action"] == "impose"), None)
        if not imposed:
            continue
        lift = next((e for e in ev if e["action"] == "lift"), None)
        changes = [e for e in ev if e["action"] in ("impose", "revise") and e.get("value")]
        value = changes[-1]["value"] if changes else None
        carried = [dict(version=x["version"], date=x["date"], carried=pid in (x.get("precautions") or []))
                   for x in ps if x["date"] >= imposed["date"]]
        dropped = next((x for x in ps if x["date"] >= imposed["date"] and pid not in (x.get("precautions") or [])
                        and not (lift and lift["date"] <= x["date"])), None)
        in_current = bool(cur and pid in (cur.get("precautions") or []))
        reaffirmed = [e for e in ev if e["action"] == "reaffirm"]
        after = [e for e in reaffirmed if dropped and e["date"] >= dropped["date"]]
        if lift:
            verdict = "lifted"
        elif not in_current:
            verdict = "dropped"
        elif value and cur.get("pain_ceiling") and cur["pain_ceiling"] != value:
            verdict = "stale"
        else:
            verdict = "carried"
        rows.append(dict(pid=pid, label=p["label"], blocks=p["blocks"], imposed=imposed["date"],
                         imposed_id=imposed["id"], imposed_text=imposed["text"], value=value,
                         revisions=[dict(date=e["date"], value=e.get("value"), id=e["id"], text=e["text"])
                                    for e in ev if e["action"] == "revise"],
                         lift=lift and dict(date=lift["date"], id=lift["id"]), carried=carried,
                         dropped_in=dropped and dict(version=dropped["version"], date=dropped["date"],
                                                     drafted_by=dropped.get("drafted_by"), id=dropped["id"]),
                         reaffirmed=[dict(date=e["date"], id=e["id"], text=e["text"]) for e in reaffirmed],
                         reaffirmed_after_drop=len(after), in_current=in_current, verdict=verdict,
                         basis=[e["id"] for e in ev]))
    in_force = [r for r in rows if r["verdict"] != "lifted"]
    draft = []
    for item in (cur or {}).get("next") or []:
        hit = [r["pid"] for r in in_force if set(item.get("tags") or []) & set(r["blocks"])]
        draft.append(dict(item=item["item"], tags=item.get("tags") or [], blocked_by=hit))
    return dict(day=day, plan=cur and dict(version=cur["version"], date=cur["date"], id=cur["id"],
                                           drafted_by=cur.get("drafted_by"), text=cur["text"],
                                           precautions=cur.get("precautions") or [],
                                           pain_ceiling=cur.get("pain_ceiling")),
                rows=rows, in_force=len(in_force), missing=[r["pid"] for r in rows if r["verdict"] == "dropped"],
                draft=draft, blocked=sum(1 for d in draft if d["blocked_by"]))


VERDICT = {"carried": "✓ carried", "dropped": "✗ DROPPED", "lifted": "· lifted", "stale": "✗ stale value"}


def show_audit(story: dict, a: dict, title: str, items: bool = True) -> None:
    pl = a["plan"]
    lines = [f"\n    {title}", f"    current plan: v{pl['version']} ({pl['date']}) · carries "
             f"{', '.join(pl['precautions']) or 'no precautions'}",
             f"    {'precaution':<42} {'imposed':<11} {'versions since':<18} {'lifted?':<9} verdict"]
    for r in a["rows"]:
        vs = " ".join(f"v{c['version']}{'✓' if c['carried'] else '✗'}" for c in r["carried"])
        val = f" ({r['value']})" if r["value"] else ""
        lines.append(f"    {r['pid']} {clip(r['label'] + val, 39):<39} {r['imposed']:<11} {vs:<18} "
                     f"{('yes ' + r['lift']['date']) if r['lift'] else 'never':<9} {VERDICT[r['verdict']]}")
    if items and a["draft"]:
        lines.append(f"\n    next session from plan v{pl['version']}, checked against what is in force:")
        for d in a["draft"]:
            lines.append(f"      {'✗ hold' if d['blocked_by'] else '✓ ok  '}  {d['item']:<38} "
                         + (f"breaks {', '.join(d['blocked_by'])}" if d["blocked_by"] else ""))
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = {}
    p = story["project"]
    proj = find_project(cli, p["key"])
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                       "--description", p["description"], scoped=True)
        note(f"created “{p['name']}” → {proj['id']}")
    else:
        note(f"“{p['name']}” already exists → {proj['id']} (pass --reset to start over)")
    ids["practice"] = proj["id"]
    pt = story["patient"]
    a = find_actor(cli, pt["key"])
    if a is None:
        a = cli.run("actor", "create", "--custom-id", cid(pt["key"]), "--display-name", pt["name"],
                    "--description", pt["description"])
        note(f"created {pt['name']} → {a['id']}")
    else:
        note(f"{pt['name']} already exists → {a['id']}")
    try:
        cli.run("actor", "bind", "--actor", a["id"], scoped=True)
    except CLIError as e:
        if not e.has("already", "409", "conflict"):
            raise
    ids["jonah"] = a["id"]
    note("the patient's chart lives on the patient (an actor); the practice project holds the covering handoff")
    emit("setup", "", ids=ids)
    return ids


# --------------------------------------------------------------------------- step 3: the chart

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
    note("one fact per chart entry, word for word, on Jonah's actor; metadata says what it is (plan version, "
         "precaution event, visit), its date, and for plans which precautions they carry")
    live = live_keys(list_facts(cli, actors=[ids["jonah"]], echo=False))
    new = pin(cli, ("--actor", ids["jonah"]), live, [(e["text"], entry_meta(e)) for e in story["entries"]],
              "kind, date, version, precaution id, action")
    if not new:
        note("the chart was pinned by an earlier run; reading it back")
    mem = read_chart(cli, ids)
    missing = [e["key"] for e in story["entries"] if e["key"] not in mem]
    if missing:
        die(f"{len(missing)} chart entr(ies) did not read back: {', '.join(missing)}")
    show_timeline(story, mem)
    emit("intake", "", counts=counts(mem), timeline=timeline(story, mem))
    return mem


def counts(mem: dict) -> dict:
    out = {}
    for e in mem.values():
        out[e["kind"]] = out.get(e["kind"], 0) + 1
    return out


def timeline(story: dict, mem: dict) -> list[dict]:
    ev = []
    for e in sorted(mem.values(), key=lambda e: (e["date"], e["key"])):
        what = e["text"].split("): ", 1)[-1]
        if e["kind"] == "plan":
            what = (f"plan v{e['version']} ({'drafted by the AI scribe, signed' if e.get('drafted_by') == 'scribe' else 'written'}"
                    f" by Mira) — carries {', '.join(e.get('precautions') or []) or 'no precautions'}")
        ev.append(dict(date=e["date"], kind=e["kind"], key=e["key"], pid=e.get("pid"), action=e.get("action"),
                       what=what))
    return ev


def show_timeline(story: dict, mem: dict) -> None:
    c = counts(mem)
    lines = [f"\n  {c.get('plan', 0)} plan versions · {c.get('precaution', 0)} precaution entries · "
             f"{c.get('visit', 0)} visit notes · {c.get('handoff', 0)} handoff note\n"]
    for e in timeline(story, mem):
        tag = f"{e['pid']} {e['action']}" if e["pid"] else e["kind"]
        lines.append(f"    {e['date']}  {tag:<12} {clip(e['what'], 104)}")
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- step 4: the handoff, the usual way

def ask(cli: CLI, ids: dict, q: str, top_k: int = 8, echo: bool = True) -> dict:
    """Any question over Jonah's chart: search's top k."""
    res = cli.run("search", q, "--actors", ids["jonah"], "--types", "fact", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    hits = [dict(rank=f.get("rank") or i, id=f.get("id"), text=f.get("fact"))
            for i, f in enumerate(res.get("facts") or [], 1)]
    return dict(q=q, hits=hits)


def kind_of(mem: dict, fid: str) -> str:
    e = next((e for e in mem.values() if e["id"] == fid), None)
    if not e:
        return "?"
    return f"plan v{e['version']}" if e["kind"] == "plan" else (f"{e['pid']} {e['action']}" if e.get("pid") else e["kind"])


def handoff_step(cli: CLI, ids: dict, story: dict) -> dict:
    day = story["today"]
    note("what a covering clinician gets: the current plan. Read the chart, take the newest plan version")
    mem = read_chart(cli, ids)
    cur = latest_plan(mem, day)
    lines = [f"\n    current plan: v{cur['version']} ({cur['date']}, "
             f"{'drafted by the AI scribe, signed by Mira' if cur.get('drafted_by') == 'scribe' else 'Mira'})",
             f"    “{clip(cur['text'], 400)}”",
             f"\n    precautions it carries: "
             + (", ".join(f"{p} {precautions(story)[p]['label']}" + (f" {cur['pain_ceiling']}" if p == 'P3' else '')
                          for p in cur.get("precautions") or []) or "none"),
             f"\n    Theo's first session ({story['first_cover_visit']}), straight from plan v{cur['version']}:"]
    for item in cur.get("next") or []:
        lines.append(f"      ✓ {item['item']}")
    emit("text", "\n".join(lines))
    q = story["ask_question"]
    note("and Theo asks the chart directly")
    hits = ask(cli, ids, q, top_k=3)["hits"]
    lines = [f"\n    “{q}” — search top {len(hits)}:"]
    for h in hits:
        lines.append(f"      {h['rank']:>2}  {kind_of(mem, h['id']):<14} {clip(h['text'], 100)}")
    top = kind_of(mem, hits[0]["id"]) if hits else None
    lines.append(f"\n  → plan v{cur['version']} schedules {len(cur.get('next') or [])} items and lists "
                 f"{len(cur.get('precautions') or [])} precaution. Search's #1 is " + (top or "nothing")
                 + " — the chart answers when someone thinks to ask; the plan Theo was told to follow gives no reason to")
    emit("text", "\n".join(lines))
    out = dict(plan=dict(version=cur["version"], date=cur["date"], text=cur["text"],
                         precautions=cur.get("precautions") or [], pain_ceiling=cur.get("pain_ceiling"),
                         drafted_by=cur.get("drafted_by")),
               session=[i["item"] for i in cur.get("next") or []], ask=dict(q=q, hits=hits), top=top)
    emit("handoff", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: audit every precaution

def keyword_reading(cli: CLI, ids: dict, mem: dict, cur: dict, word: str) -> dict:
    hits = list_facts(cli, actors=[ids["jonah"]], query=word)
    kinds = [kind_of(mem, f["id"]) for f in hits]
    return dict(word=word, n=len(hits), kinds=kinds, in_current=any(f["id"] == cur["id"] for f in hits))


def audit_step(cli: CLI, ids: dict, story: dict) -> dict:
    day = story["today"]
    note("read the whole chart once, then follow each precaution from the day it was imposed: every plan "
         "version written since, every revise / reaffirm / lift on record")
    mem = read_chart(cli, ids)
    v1 = plans(mem, day)[0]["date"]
    ctl = audit(story, mem, v1)
    show_audit(story, ctl, f"control — the same audit on {v1}, the day plan v1 was written:", items=False)
    ok = sum(1 for r in ctl["rows"] if r["verdict"] == "carried")
    emit("text", f"\n    {'✓' if ok == len(ctl['rows']) else '✗'} {ok}/{len(ctl['rows'])} carried — "
                 f"the audit does not flag a plan that is right")
    v2 = next(p for p in plans(mem, day) if p["version"] == 2)["date"]
    then = audit(story, mem, v2)
    emit("text", f"\n    on {v2}, the day plan v2 was signed, it would already have said: "
                 + (", ".join(f"{p} dropped" for p in then["missing"]) or "nothing missing"))

    a = audit(story, mem, day)
    show_audit(story, a, f"today ({day}):")
    lines = []
    for r in a["rows"]:
        if r["verdict"] == "dropped":
            d = r["dropped_in"]
            by = "the AI scribe's draft" if d.get("drafted_by") == "scribe" else "Mira's rewrite"
            again = "; ".join(f"{x['date']}: {clip(x['text'].split('): ', 1)[-1], 96)}"
                              for x in r["reaffirmed"])
            lines.append(f"\n    ✗ {r['pid']} {r['label']}: imposed {r['imposed']}, never lifted. It fell out in plan "
                         f"v{d['version']} ({d['date']}, {by})" + (f" — and was repeated after that: {again}"
                                                                    if again else ""))
        elif r["revisions"]:
            x = r["revisions"][-1]
            lines.append(f"\n    ✓ {r['pid']} {r['label']}: changed on the record {x['date']} → {x['value']} "
                         f"(“{clip(x['text'].split(' - ', 1)[-1], 70)}”); plan v{a['plan']['version']} carries "
                         f"{a['plan']['pain_ceiling']}")
    emit("text", "\n".join(lines))

    cur = latest_plan(mem, day)
    note("why a keyword check would not catch it: exact substring over the whole chart (`fact list --query`)")
    kw = [keyword_reading(cli, ids, mem, cur, w) for w in ("overhead", "apixaban")]
    lines = []
    for k in kw:
        lines.append(f"    “{k['word']}”: {k['n']} entries ({', '.join(k['kinds'])}) · in plan v{cur['version']}: "
                     f"{'yes' if k['in_current'] else 'no'}")
    o = kw[0]
    lines.append(f"\n  → “does the current plan mention overhead?” — {'yes' if o['in_current'] else 'no'}: plan "
                 f"v{cur['version']} says “add overhead dumbbell press”. A mention is not a precaution; only the "
                 f"precaution's own record says whether it was lifted.")
    blocked = [d for d in a["draft"] if d["blocked_by"]]
    lines.append(f"\n  → {len(a['missing'])} of {len(a['rows'])} precautions were dropped without anyone lifting them; "
                 f"{len(blocked)} of {len(a['draft'])} items in Theo's first session break them.")
    emit("text", "\n".join(lines))
    out = dict(control=dict(day=v1, carried=ok, total=len(ctl["rows"])), v2=dict(day=v2, missing=then["missing"]),
               audit=a, keyword=kw)
    emit("audited", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: write the handoff, with its sources

def handoff_text(story: dict, a: dict) -> str:
    in_force = [r for r in a["rows"] if r["verdict"] != "lifted"]
    pre = "; ".join(f"{r['pid']} {r['label']}" + (f" {r['value']}" if r["value"] else "")
                    + (" (missing from the current plan)" if r["verdict"] == "dropped" else "") for r in in_force)
    hold = "; ".join(f"{d['item']} (breaks {', '.join(d['blocked_by'])})" for d in a["draft"] if d["blocked_by"])
    ok = ", ".join(d["item"] for d in a["draft"] if not d["blocked_by"])
    return (f"Covering handoff for Jonah Reyes ({a['day']}, for {who(story, 'theo')}): precautions in force - {pre}. "
            f"Hold from plan v{a['plan']['version']}: {hold or 'nothing'}. Session {story['first_cover_visit']}: {ok}.")


def write_step(cli: CLI, ids: dict, story: dict, res: dict) -> dict:
    a = res["audit"]
    note("write the handoff to the practice project: one fact, with the ids of every chart entry it rests on in "
         "metadata.basis, then read each one back")
    basis = [a["plan"]["id"]] + [b for r in a["rows"] for b in r["basis"]]
    text = handoff_text(story, a)
    meta = dict(kind="cover-handoff", key=f"cover:{a['day']}", date=a["day"], to="theo", plan=a["plan"]["id"],
                hold=[d["item"] for d in a["draft"] if d["blocked_by"]], basis=basis)
    live = live_keys(list_facts(cli, projects=[ids["practice"]], echo=False))
    have = live.get(meta["key"])
    rewrote = False
    if have and have.get("fact") != text:
        cli.run("fact", "update", have["id"], "--project", ids["practice"], "--text", text, "--metadata",
                json.dumps(meta), scoped=True)
        rewrote = True
    new = pin(cli, ("--project", ids["practice"]), live, [(text, meta)], "kind, date, plan, hold, basis")
    if not new and not rewrote:
        note("the handoff was written by an earlier run; reading it back")
    got = live_keys(list_facts(cli, projects=[ids["practice"]]))[meta["key"]]
    chart_mem = read_chart(cli, ids, echo=False)
    mem = {e["id"]: e for e in chart_mem.values()}
    srcs = (got.get("metadata") or {}).get("basis") or []
    lines = [f"\n    {got['fact']}\n"]
    for b in srcs:
        e = mem.get(b)
        lines.append(f"      ← {b}  {kind_of(chart_mem, b) if e else 'missing':<14} "
                     f"{clip(e['text'] if e else '(not found)', 90)}")
    found = sum(1 for b in srcs if b in mem)
    lines.append(f"\n    {'✓' if found == len(srcs) and srcs else '✗'} {found}/{len(srcs)} cited chart entries read back")
    emit("text", "\n".join(lines))
    out = dict(id=got["id"], handoff=got["fact"], cited=found, cited_total=len(srcs), written=len(new), rewrote=rewrote,
               sources=[dict(id=b, text=mem[b]["text"] if b in mem else None) for b in srcs])
    emit("written", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: plan, audit

def show_plan(story: dict, mem: dict, version: int | None) -> dict:
    ps = plans(mem, "9999")
    p = ps[-1] if version is None else next((x for x in ps if x["version"] == version), None)
    if not p:
        die(f"no plan v{version}; this chart has v1 to v{ps[-1]['version']}")
    emit("text", f"\n  plan v{p['version']} ({p['date']}):\n    {p['text']}\n\n  carries: "
                 f"{', '.join(p.get('precautions') or []) or 'none'}\n  next session: "
                 + "; ".join(i["item"] for i in p.get("next") or []))
    return p


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
    a = find_actor(cli, story["patient"]["key"], echo=False)
    if a:
        n = delete_actor(cli, a["id"])
        note(f"deleted {story['patient']['name']} (actor) and the {n} chart entries on him (facts first, then the actor)")
    proj = find_project(cli, story["project"]["key"], echo=not quiet)
    if proj:
        for f in list_facts(cli, projects=[proj["id"]], echo=False):
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = list_facts(cli, projects=[proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on {proj['id']} after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the practice project and the handoff on it")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — the practice, and the patient as an actor")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "The chart — three plan versions, precautions, visits, pinned word for word")
    intake(cli, ids, story)
    banner(4, TOTAL, "Handoff, the usual way — the covering clinician follows the current plan")
    ho = handoff_step(cli, ids, story)
    banner(5, TOTAL, "Audit — follow every precaution through every version since it was imposed")
    res = audit_step(cli, ids, story)
    banner(6, TOTAL, "Write the handoff back — with the chart entries it rests on")
    wr = write_step(cli, ids, story, res)
    a = res["audit"]
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   current_plan=f"v{ho['plan']['version']}", session=len(ho["session"]), search_top=ho["top"],
                   control=f"{res['control']['carried']}/{res['control']['total']}", v2_missing=res["v2"]["missing"],
                   verdicts={r["pid"]: r["verdict"] for r in a["rows"]}, blocked=a["blocked"],
                   keyword={k["word"]: [k["n"], k["in_current"]] for k in res["keyword"]},
                   cited=f"{wr['cited']}/{wr['cited_total']}", written=wr["written"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    proj = find_project(cli, story["project"]["key"], echo=False)
    a = find_actor(cli, story["patient"]["key"], echo=False)
    if proj is None or a is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(practice=proj["id"], jonah=a["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "plan", "audit", "ask", "cleanup"],
                    help="run = full demo (default); plan [v1|v2|v3] = one plan version (default the current one); "
                         "audit [YYYY-MM-DD] = every precaution checked against the plan of that day (default "
                         "today); ask \"QUESTION\" = search's top 8 over Jonah's chart; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the version, the date or the question")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one argument, e.g. ask \"Can Jonah press overhead?\"")
    if a.command == "plan" and a.args and not re.fullmatch(r"v?\d+", a.args[0]):
        ap.error("plan takes a version, e.g. plan v1")
    if a.command == "audit" and a.args and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.args[0]):
        ap.error("audit takes a date as YYYY-MM-DD, e.g. audit 2026-08-25")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py audit 2026-08-25`, `python3 demo.py plan v1` or "
              "`python3 demo.py ask \"Can Jonah press overhead?\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "plan":
        v = int(a.args[0].lstrip("v")) if a.args else None
        banner(2, 2, f"Plan {'v' + str(v) if v else '(current)'} — as pinned")
        show_plan(story, read_chart(cli, ids), v)
    elif a.command == "audit":
        day = a.args[0] if a.args else story["today"]
        banner(2, 2, f"Audit as of {day} — every precaution against the plan of that day")
        mem = read_chart(cli, ids)
        if not latest_plan(mem, day):
            die(f"there is no plan on or before {day}; the first is {plans(mem, '9999')[0]['date']}")
        show_audit(story, audit(story, mem, day), f"as of {day}:")
    elif a.command == "ask":
        banner(2, 2, "Ask — search's top 8 over Jonah's chart")
        mem = read_chart(cli, ids, echo=False)
        r = ask(cli, ids, a.args[0])
        lines = [f"\n  “{r['q']}” — search top {len(r['hits'])}:"]
        for h in r["hits"]:
            lines.append(f"    {h['rank']:>2}  {kind_of(mem, h['id']):<14} {clip(h['text'], 110)}")
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

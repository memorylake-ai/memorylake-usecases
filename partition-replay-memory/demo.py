#!/usr/bin/env python3
"""partition-replay-memory — a region loses its link to shared memory, keeps working, and replays its writes on reconnect.

Tessane Freight (fictional) runs dispatch agents in two regions, us-east and eu-west, on one shared runbook memory.
At 09:04 eu-west loses its link to MemoryLake for 36 minutes. It keeps working and journals four runbook changes
in a local outbox; us-east, still connected, changes two of the same settings. At 09:40 the link is back and eu-west
replays its outbox. The same replay runs three ways, each in its own copy of the runbook:

  A  replay as-is: one `fact update` per outbox entry
  B  replay as new facts: `fact add`, so conflict detection can compare them with what is there
  C  replay only if unchanged: compare `updated_at` with what eu-west read; write, then check `fact trace`;
     anything that changed meanwhile is held for review

Then: what each runbook says, what `fact trace` shows, what conflict detection flags, and a score.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key.
No model calls: nothing is billed to your MemoryLake balance.
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
import textwrap
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

PREFIX = "mlu-prp"  # memorylake-usecases / partition-replay-memory


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

    # `fact update` is retried too: setting the same text twice is the same as once.
    READ_ONLY = {"get", "list", "trace", "status", "current", "me", "search", "update"}
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
        # A multi-line argument is shown as its first line; it is printed in full
        # once, above the call.
        shown = [a.split("\n")[0] + f" […{a.count(chr(10))} more lines]" if "\n" in a else a for a in shown]
        # `fact add` with many texts: the first two, then a count (display only; every text is sent).
        if shown[:2] == ["fact", "add"] and len(shown) > 12:
            # keep the flags after the texts (--project, --workspace): they say where the facts go
            cut = next((i for i in range(2, len(shown)) if shown[i].startswith("--")), len(shown))
            tail = shown[cut:]
            body = shown[:cut]
            shown = body[:6] + [f"… {len(body) - 6} more texts"] + tail
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
                parsed = self._parse(out)
                # A reply that should be JSON and isn't (seen once on `lib upload`, exit 0): retry what is safe to retry.
                if parsed is not None or not out.strip() or not retryable or attempt >= 4 or args[0] == "router":
                    break
                attempt += 1
                note(f"unexpected reply ({' '.join(out.split())[:80]}); retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            e = CLIError([self.binary, *map(str, args)], rc, out, err)
            if retryable and attempt < 4 and e.has(*self.TRANSIENT):
                attempt += 1
                note(f"network hiccup; retry {attempt}/4 in {2 * attempt}s")
                time.sleep(2 * attempt)
                continue
            raise e
        if self.show_json and out.strip():
            emit("json", out.rstrip())
        parsed = self._parse(out)
        return parsed if parsed is not None else (out if out.strip() else None)

    @staticmethod
    def _parse(out: str):
        """The JSON reply, also when something was printed before it; None when there is no JSON."""
        for start in (0, out.find("{"), out.find("[")):
            if start < 0:
                continue
            try:
                return json.loads(out[start:])
            except json.JSONDecodeError:
                continue
        return None

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False, echo: bool = True):


        try:
            return self.run(*args, scoped=scoped, echo=echo)
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
    env.pop("MEMORYLAKE_API_KEY", None)
    api_key = (api_key or os.environ.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)
    cli.base_url = base_url
    rc, out, _ = cli.raw("fact", "conflict", "--help", echo=False)
    if rc != 0:
        die("this memorylake CLI has no `fact conflict` command; it needs v20261015 or later.\n"
            "  upgrade: curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh")
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
        # `ws list` is newest first: take the team's default workspace, so `run` and `cleanup` land in the same one.
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


# --------------------------------------------------------------------------- settings

TOTAL = 7             # steps in a full run
WINDOW = 150          # seconds to watch for conflicts after the last write (detection lands in 10–110 s)
POLL = 15
GAP = 15              # seconds between B's adds: back-to-back writes push detection back by minutes
ARMS = ("A", "B", "C")
DETECTOR = "latest-source"  # the obvious automatic resolution: keep the newest write


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, arm: str, echo: bool = False):
    return cli.try_run("proj", "get", cid("runbook", arm.lower()), "--by-custom-id", scoped=True, echo=echo)


def paged(cli: CLI, args: list[str], echo: bool = False) -> list[dict]:
    out, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or [] if isinstance(page, dict) else page
        token = page.get("continuation_token") if isinstance(page, dict) else None
        if not token:
            return out


def project_facts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--projects", project, "--page-size", "200"], echo)


def conflicts(cli: CLI, project: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "conflict", "list", "--project", project, "--resolved", "false"], echo)


def delete_fact(cli: CLI, fact_id: str, scope: list[str]) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, *scope, scoped=True, echo=False)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def wrap(text: str, indent: str = "      ", width: int = 96) -> str:
    return textwrap.fill(" ".join(text.split()), width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_on_hyphens=False)


def hhmmss(iso: str) -> str:
    return (iso or "")[11:19]


def story_ts(d: dict, hhmm: str) -> str:
    return f"{d['today']}T{hhmm}:00Z"


def label(d: dict, key: str) -> str:
    return next(f["label"] for f in d["fields"] if f["key"] == key)


def known_texts(d: dict) -> dict:
    """Every text the demo ever writes → (field, value, who wrote it, story time). Writes are verbatim, so exact match."""
    m = {f["base"]: (f["key"], f["value"], "runbook", d["partition"]["read_at"]) for f in d["fields"]}
    for w in d["online_writes"]:
        m[w["text"]] = (w["field"], w["value"], d["regions"]["online"], w["at"])
    for e in d["outbox"]:
        m[e["text"]] = (e["field"], e["value"], d["regions"]["offline"], e["at"])
    return m


def target(d: dict) -> dict:
    """What the runbook should say once both regions' changes are in: per field, the latest decision wins."""
    t = {f["key"]: dict(value=f["value"], who="runbook", at=d["partition"]["read_at"]) for f in d["fields"]}
    for w in [*d["online_writes"], *d["outbox"]]:
        who = d["regions"]["online"] if w in d["online_writes"] else d["regions"]["offline"]
        if w["at"] > t[w["field"]]["at"]:
            t[w["field"]] = dict(value=w["value"], who=who, at=w["at"])
    return t


def touched(d: dict) -> list[str]:
    keys = [e["field"] for e in d["outbox"]] + [w["field"] for w in d["online_writes"]]
    return [f["key"] for f in d["fields"] if f["key"] in keys]


def collided(d: dict) -> set:
    """Fields both regions changed during the partition: the only ones a human needs to look at."""
    return {e["field"] for e in d["outbox"]} & {w["field"] for w in d["online_writes"]}


def check_story() -> None:
    """The rules on the story itself, before any call is made: a wrong rule would misread every run."""
    d = load_data()
    m = known_texts(d)
    if len(m) != len(d["fields"]) + len(d["online_writes"]) + len(d["outbox"]):
        die("story self-check failed: two writes share a text, so a fact could not be told apart")
    t = target(d)
    if t["rate"]["value"] != "80 req/s" or t["retries"]["value"] != "5 retries" or t["hub"]["who"] != "runbook":
        die(f"story self-check failed: target is {t}")
    if collided(d) != {"rate", "cache"}:
        die(f"story self-check failed: collided fields are {collided(d)}")


# --------------------------------------------------------------------------- step 2: setup

def leftovers(cli: CLI) -> bool:
    return any(find_project(cli, arm) for arm in ARMS)


def snapshot(cli: CLI, d: dict, project: str, echo: bool) -> dict:
    """The runbook as a region reads it: field → {id, text, updated_at}."""
    m = known_texts(d)
    snap = {}
    for f in project_facts(cli, project, echo=echo):
        k = m.get(f["fact"])
        if k:
            snap[k[0]] = dict(id=f["id"], text=f["fact"], updated_at=f.get("updated_at"))
    missing = [f["key"] for f in d["fields"] if f["key"] not in snap]
    if missing:
        die(f"the runbook is missing {missing}; run `python3 demo.py cleanup` and try again")
    return snap


def setup(cli: CLI, d: dict) -> tuple[dict, dict]:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {}
    for arm in ARMS:
        # Same project name for all three: only the custom_id says which copy it is.
        world[arm] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("runbook", arm.lower()),
                             scoped=True, echo=True)["id"]
        cli.run("fact", "add", *[f["base"] for f in d["fields"]], "--project", world[arm], scoped=True, echo=True)
    emit("world", "", **world)
    emit("text", f"\n  {d['partition']['read_at']} — both regions read the runbook. eu-west keeps what it read: "
                 "each fact's id, text and updated_at.")
    snaps = {arm: snapshot(cli, d, world[arm], echo=True) for arm in ARMS}
    emit("text", "\n    field               runbook says                     updated_at (copy A)")
    for f in d["fields"]:
        emit("text", f"    {f['label']:<19} {f['value']:<32} {hhmmss(snaps['A'][f['key']]['updated_at'])}")
    emit("snapshot", "", fields=[dict(key=f["key"], label=f["label"], value=f["value"],
                                      updated_at={a: snaps[a][f["key"]]["updated_at"] for a in ARMS}) for f in d["fields"]])
    return world, snaps


def load_world(cli: CLI) -> dict:
    world = {}
    for arm in ARMS:
        p = find_project(cli, arm)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world[arm] = p["id"]
    return world


# --------------------------------------------------------------------------- step 3: the partition

def partition(cli: CLI, d: dict, world: dict, snaps: dict) -> list[dict]:
    on, off = d["regions"]["online"], d["regions"]["offline"]
    p = d["partition"]
    note(f"{p['start']} — {off} loses its link to MemoryLake; it keeps dispatching and journals changes locally")
    emit("text", f"\n  {on} is still connected and changes {len(d['online_writes'])} settings (in all three copies):")
    for w in d["online_writes"]:
        meta = json.dumps({"region": on, "decided_at": story_ts(d, w["at"]), "why": w["why"]})
        for arm in ARMS:
            cli.run("fact", "update", snaps[arm][w["field"]]["id"], "--text", w["text"], "--metadata", meta,
                    "--project", world[arm], scoped=True, echo=True)
        emit("progress", f"    {w['at']}  {on:<8} {label(d, w['field'])}: → {w['value']}   ({w['why']})",
             region=on, field=w["field"], value=w["value"], at=w["at"], why=w["why"])
    emit("text", f"\n  {off} cannot reach memory; its agents journal {len(d['outbox'])} changes in a local outbox "
                 f"(out/outbox.json), each with what it read at {p['read_at']}:")
    base = {f["key"]: f["value"] for f in d["fields"]}
    outbox = []
    for e in d["outbox"]:
        entry = dict(field=e["field"], label=label(d, e["field"]), at=e["at"], decided_at=story_ts(d, e["at"]),
                     text=e["text"], value=e["value"], why=e["why"], read_value=base[e["field"]],
                     base={arm: snaps[arm][e["field"]] for arm in ARMS})
        outbox.append(entry)
        emit("progress", f"    {e['at']}  {off:<8} {entry['label']}: {base[e['field']]} → {e['value']}   ({e['why']})",
             region=off, field=e["field"], value=e["value"], at=e["at"], why=e["why"], read=base[e["field"]],
             read_updated_at=snaps["A"][e["field"]]["updated_at"])
    OUT.mkdir(exist_ok=True)
    (OUT / "outbox.json").write_text(json.dumps(outbox, indent=2), encoding="utf-8")
    note(f"{off} never saw {on}'s changes: its outbox still says the rate limit was 50 req/s and the cache 300 s")
    emit("outbox", "", entries=outbox)
    return outbox


# --------------------------------------------------------------------------- step 4: reconnect and replay

def meta_for(d: dict, e: dict) -> str:
    return json.dumps({"region": d["regions"]["offline"], "decided_at": e["decided_at"], "why": e["why"]})


def replay(cli: CLI, d: dict, world: dict, outbox: list[dict]) -> dict:
    off = d["regions"]["offline"]
    note(f"{d['partition']['end']} — the link is back; {off} replays its outbox three ways")
    res: dict = {arm: [] for arm in ARMS}

    emit("text", f"\n  {d['arms']['A']['title']} — {d['arms']['A']['how']}")
    for e in outbox:
        cli.run("fact", "update", e["base"]["A"]["id"], "--text", e["text"], "--metadata", meta_for(d, e),
                "--project", world["A"], scoped=True, echo=True)
        res["A"].append(dict(field=e["field"], outcome="written"))
        emit("progress", f"    A  {e['label']:<19} → {e['value']:<10} written", arm="A", field=e["field"], outcome="written")

    emit("text", f"\n  {d['arms']['B']['title']} — {d['arms']['B']['how']}")
    for i, e in enumerate(outbox):
        if i:
            time.sleep(GAP)
        r = cli.run("fact", "add", e["text"], "--project", world["B"], scoped=True, echo=True) or {}
        fid = ((r.get("facts") or [{}])[0]).get("id")
        res["B"].append(dict(field=e["field"], outcome="added", fact=fid))
        emit("progress", f"    B  {e['label']:<19} → {e['value']:<10} added as a new fact", arm="B", field=e["field"],
             outcome="added")
    last_write = time.time()

    emit("text", f"\n  {d['arms']['C']['title']} — {d['arms']['C']['how']}")
    for e in outbox:
        b = e["base"]["C"]
        cur = cli.run("fact", "get", b["id"], "--project", world["C"], scoped=True, echo=True) or {}
        if cur.get("updated_at") != b["updated_at"] or cur.get("fact") != b["text"]:
            m = cur.get("metadata") or {}
            who = m.get("region") or "someone"
            at = (m.get("decided_at") or "")[11:16]
            now_value = (known_texts(d).get(cur.get("fact")) or (None, cur.get("fact")))[1]
            row = dict(field=e["field"], outcome="held", now=now_value, by=who, by_at=at,
                       read_updated_at=b["updated_at"], now_updated_at=cur.get("updated_at"), proposed=e["value"],
                       read=e["read_value"], why=e["why"], at=e["at"])
            res["C"].append(row)
            emit("progress", f"    C  {e['label']:<19} HELD — updated_at {hhmmss(b['updated_at'])} → "
                             f"{hhmmss(cur.get('updated_at'))}: now {now_value} ({who}, {at}); {off} wanted {e['value']}",
                 arm="C", field=e["field"], outcome="held", now=now_value, by=who, by_at=at)
            continue
        cli.run("fact", "update", b["id"], "--text", e["text"], "--metadata", meta_for(d, e),
                "--project", world["C"], scoped=True, echo=True)
        tr = cli.run("fact", "trace", b["id"], "--project", world["C"], scoped=True, echo=True) or {}
        mine = [t for t in tr.get("trace") or [] if t.get("event") == "UPDATE" and t.get("new_fact") == e["text"]]
        newest = max(mine, key=lambda t: t.get("timestamp") or "", default={})
        clean = newest.get("old_fact") == b["text"]
        outcome = "written" if clean else "raced"
        res["C"].append(dict(field=e["field"], outcome=outcome, replaced=newest.get("old_fact")))
        emit("progress", f"    C  {e['label']:<19} → {e['value']:<10} written; trace: it replaced "
                         + ("exactly what eu-west read" if clean else "a text eu-west never read — flag it"),
             arm="C", field=e["field"], outcome=outcome)
    held = [r for r in res["C"] if r["outcome"] == "held"]
    (OUT / "held.json").write_text(json.dumps(held, indent=2), encoding="utf-8")
    note(f"A wrote {len(res['A'])}, B added {len(res['B'])}, C wrote "
         f"{sum(r['outcome'] == 'written' for r in res['C'])} and held {len(held)} — no command failed")
    emit("replayed", "", results=res)
    return {"results": res, "last_write": last_write}


# --------------------------------------------------------------------------- step 5: what the runbooks say

def read_runbook(cli: CLI, d: dict, project: str, echo: bool = True) -> dict:
    """field → every live fact about it: [{value, who, at, id, created_at}]."""
    m = known_texts(d)
    out: dict = {f["key"]: [] for f in d["fields"]}
    for f in project_facts(cli, project, echo=echo):
        k = m.get(f["fact"])
        if k:
            out[k[0]].append(dict(value=k[1], who=k[2], at=k[3], id=f["id"], created_at=f.get("created_at")))
        else:
            out.setdefault("unknown", []).append(dict(value=f["fact"], id=f["id"]))
    return out


def says(entries: list[dict]) -> str:
    return " + ".join(e["value"] for e in entries) if entries else "(nothing)"


def blind_overwrites(cli: CLI, d: dict, world: dict, outbox: list[dict], arm: str = "A", echo: bool = True) -> list[dict]:
    """For each replayed update in `arm`: the text it replaced (fact trace) vs the text eu-west had read."""
    m = known_texts(d)
    rows = []
    for e in outbox:
        b = e["base"][arm]
        tr = cli.run("fact", "trace", b["id"], "--project", world[arm], scoped=True, echo=echo) or {}
        mine = [t for t in tr.get("trace") or [] if t.get("event") == "UPDATE" and t.get("new_fact") == e["text"]]
        if not mine:
            continue
        t = max(mine, key=lambda t: t.get("timestamp") or "")
        old = m.get(t.get("old_fact")) or (e["field"], t.get("old_fact"), "?", "?")
        rows.append(dict(field=e["field"], label=e["label"], wrote=e["value"], read=e["read_value"], replaced=old[1],
                         replaced_by=old[2], replaced_at=old[3], blind=t.get("old_fact") != b["text"],
                         at=t.get("timestamp")))
    return rows


def what_memory_says(cli: CLI, d: dict, world: dict, outbox: list[dict]) -> dict:
    t = target(d)
    books = {arm: read_runbook(cli, d, world[arm]) for arm in ARMS}
    emit("text", "\n  The runbook should now say (latest decision wins):")
    emit("text", "    field               should say   from              " + "".join(f"{x:<26}" for x in
                                                                          ("A replay as-is", "B as new facts", "C if unchanged")))
    for k in touched(d):
        cells = []
        for arm in ARMS:
            e = books[arm][k]
            ok = len(e) == 1 and e[0]["value"] == t[k]["value"]
            cells.append(f"{says(e) + (' ✓' if ok else ' ✗'):<26}")
        emit("text", f"    {label(d, k):<19} {t[k]['value']:<12} {t[k]['who'] + ' ' + t[k]['at']:<17} " + "".join(cells).rstrip())
    rows = blind_overwrites(cli, d, world, outbox, "A")
    emit("text", f"\n  fact trace on A — what each replayed update replaced:")
    emit("text", "    field               eu-west read   wrote       replaced")
    for r in rows:
        tag = (f"{r['replaced']} — {r['replaced_by']}'s {r['replaced_at']} change, never seen by eu-west  ← BLIND"
               if r["blind"] else f"{r['replaced']} — what it read")
        emit("text", f"    {r['label']:<19} {r['read']:<14} {r['wrote']:<11} {tag}")
    blind = [r for r in rows if r["blind"]]
    note(f"A: {len(blind)} of {len(rows)} replayed updates overwrote a change made during the partition — "
         "every command succeeded: `fact update` takes no version or if-match")
    emit("memory", "", target=t, books=books, trace=rows, blind=len(blind))
    return {"books": books, "trace": rows}


# --------------------------------------------------------------------------- step 6: conflict detection

def watch_conflicts(cli: CLI, world: dict, since: float) -> dict:
    found: dict = {arm: [] for arm in ARMS}
    seen: dict = {}
    first = True
    while True:
        waited = time.time() - since
        for arm in ARMS:
            found[arm] = conflicts(cli, world[arm], echo=first)
        first = False
        for arm in ARMS:
            if found[arm] and arm not in seen:
                seen[arm] = int(waited)
        line = " · ".join(f"{arm} {len(found[arm])}" for arm in ARMS)
        emit("progress", f"    {int(waited):>3}s after the last write — open conflicts: {line}", waited=int(waited),
             found={arm: len(found[arm]) for arm in ARMS})
        if waited >= WINDOW:
            break
        time.sleep(min(POLL, max(1, WINDOW - waited)))
    found["seen"] = seen
    return found


def conflict_fields(d: dict, c: dict) -> list[str]:
    m = known_texts(d)
    keys = []
    for s in c.get("fact_snapshots") or []:
        k = m.get(s.get("fact_text"))
        if k and k[0] not in keys:
            keys.append(k[0])
    return keys


def conflict_step(cli: CLI, d: dict, world: dict, since: float) -> dict:
    note(f"watching all three copies for {WINDOW} s after B's last write (detection usually lands in 10–110 s)")
    found = watch_conflicts(cli, world, since)
    hot = collided(d)
    result = {"raised": {arm: len(found[arm]) for arm in ARMS}, "conflicts": {}, "resolved": [],
              "first_seen_s": found["seen"]}
    for arm in ARMS:
        rows = []
        for c in found[arm]:
            keys = conflict_fields(d, c)
            real = bool(set(keys) & hot)
            emit("text", f"\n  {arm}: {c.get('category')} · “{c.get('name')}” — "
                         f"{', '.join(label(d, k) for k in keys) or 'unknown facts'}"
                         f"  [{'both regions changed it' if real else 'only eu-west changed it: nothing to review'}]")
            emit("text", wrap(c.get("description") or "", "      “", 100) + "”")
            rows.append(dict(id=c["id"], name=c.get("name"), description=c.get("description"),
                             category=c.get("category"), fields=keys, real=real, fact_ids=c.get("fact_ids") or []))
        result["conflicts"][arm] = rows
    if not found["A"] and not found["C"]:
        note("A and C raise nothing: `fact update` never runs detection — A's overwrites are invisible to it")
    # The obvious automatic resolution: keep the newest write. In B the newest is always eu-west's replay.
    m = known_texts(d)
    t = target(d)
    for c in result["conflicts"]["B"]:
        if c["category"] not in ("m2m", "self"):
            continue
        facts = {f["id"]: f for f in project_facts(cli, world["B"])}
        ids = [i for i in c["fact_ids"] if i in facts]
        if len(ids) < 2:
            continue
        keep = max(ids, key=lambda i: facts[i].get("created_at") or "")
        kept = m.get(facts[keep]["fact"]) or ("?", facts[keep]["fact"], "?", "?")
        emit("text", f"\n  B: resolving “{c['name']}” the {DETECTOR} way — keep the newest fact, forget the rest:")
        cli.run("fact", "conflict", "resolve", c["id"], "--project", world["B"], "--strategy", "keep_fact",
                "--keep-fact-id", keep, scoped=True, echo=True)
        gone = [m.get(facts[i]["fact"], ("?", facts[i]["fact"]))[1] for i in ids if i != keep]
        right = kept[1] == t.get(kept[0], {}).get("value")
        note(f"kept {kept[1]} ({kept[2]}, decided {kept[3]}), forgot {', '.join(gone)} — "
             + ("that is the latest decision" if right else
                f"wrong: {t[kept[0]]['who']} decided {t[kept[0]]['value']} at {t[kept[0]]['at']}, later; "
                f"{kept[2]}'s write is only the newest to arrive"))
        result["resolved"].append(dict(conflict=c["id"], field=kept[0], kept=kept[1], kept_by=kept[2], forgot=gone,
                                       right=right))
    emit("conflicts", "", **result)
    return result


# --------------------------------------------------------------------------- step 7: score

def score(cli: CLI, d: dict, world: dict, replayed: dict, conf: dict) -> dict:
    t = target(d)
    hot = collided(d)
    online = {w["field"]: w["value"] for w in d["online_writes"]}
    clean = {e["field"]: e["value"] for e in d["outbox"] if e["field"] not in hot}
    books = {arm: read_runbook(cli, d, world[arm], echo=True) for arm in ARMS}
    rows = []
    for arm in ARMS:
        b = books[arm]
        right = sum(1 for k in t if len(b[k]) == 1 and b[k][0]["value"] == t[k]["value"])
        ambiguous = [k for k in t if len(b[k]) > 1]
        kept = sum(1 for k, v in online.items() if any(e["value"] == v for e in b[k]) and len(b[k]) == 1)
        landed = sum(1 for k, v in clean.items() if len(b[k]) == 1 and b[k][0]["value"] == v)
        if arm == "C":
            items = [r for r in replayed["results"]["C"] if r["outcome"] in ("held", "raced")]
            review_real = sum(1 for r in items if r["field"] in hot)
        elif arm == "B":
            items = conf["conflicts"]["B"]
            review_real = sum(1 for c in items if c["real"])
        else:
            items = conf["conflicts"]["A"]
            review_real = sum(1 for c in items if c["real"])
        rows.append(dict(arm=arm, title=d["arms"][arm]["title"], right=right, of=len(t), ambiguous=ambiguous,
                         kept=kept, online=len(online), landed=landed, clean=len(clean),
                         review=len(items), review_real=review_real, review_false=len(items) - review_real,
                         says={k: [e["value"] for e in b[k]] for k in t}))
    emit("text", f"\n  After the replay — the target is the latest decision per field:\n")
    emit("text", "    copy                          fields right   us-east changes kept   eu-west changes in   for review (real / not)")
    for r in rows:
        emit("text", f"    {d['arms'][r['arm']]['short']:<29} {r['right']}/{r['of']:<12} {r['kept']}/{r['online']:<20} "
                     f"{r['landed']}/{r['clean']:<18} {r['review']} ({r['review_real']} / {r['review_false']})")
    a, b, c = rows
    note(f"A: lost {a['online'] - a['kept']} of {a['online']} us-east changes with no error and no conflict — "
         "the oldest decisions won because they arrived last")
    nres = len(conf["resolved"])
    wrong = sum(1 for x in conf["resolved"] if not x["right"])
    note(f"B: {b['review']} conflict{'s' if b['review'] != 1 else ''} raised ({b['review_real']} where both regions "
         f"changed the field); resolving {nres} the {DETECTOR} way kept the wrong value {wrong} time"
         f"{'s' if wrong != 1 else ''}")
    note(f"C: {c['landed']}/{c['clean']} clean changes in, {c['kept']}/{c['online']} us-east changes kept, "
         f"{c['review']} held for a person — run `python3 demo.py review`")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, target=t, rows=rows,
                  replay=replayed["results"], conflicts=conf)
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    emit("score", "", **report)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_story()
    banner(2, TOTAL, "Setup — one runbook, three copies; both regions read it at 09:00")
    world, snaps = setup(cli, d)
    banner(3, TOTAL, "09:04–09:40 — eu-west is cut off; both regions keep changing the runbook")
    outbox = partition(cli, d, world, snaps)
    banner(4, TOTAL, "09:40 — the link is back; eu-west replays its outbox three ways")
    replayed = replay(cli, d, world, outbox)
    banner(5, TOTAL, "What each runbook says now — and what fact trace shows")
    what_memory_says(cli, d, world, outbox)
    banner(6, TOTAL, "Conflict detection — what MemoryLake flags")
    conf = conflict_step(cli, d, world, replayed["last_write"])
    banner(7, TOTAL, "Score — which copy ended up right")
    return score(cli, d, world, replayed, conf)


# --------------------------------------------------------------------------- read-only subcommands

def _outbox() -> list[dict]:
    p = OUT / "outbox.json"
    if not p.exists():
        die("no outbox yet; run `python3 demo.py` first")
    return json.loads(p.read_text(encoding="utf-8"))


def show_outbox(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli)
    emit("text", f"  eu-west's outbox (out/outbox.json) — journaled while cut off, with what it had read:\n")
    for e in _outbox():
        cur = cli.run("fact", "get", e["base"]["C"]["id"], "--project", world["C"], scoped=True, echo=True) or {}
        now = (known_texts(d).get(cur.get("fact")) or (None, cur.get("fact")))[1]
        changed = cur.get("updated_at") != e["base"]["C"]["updated_at"]
        emit("text", f"    {e['at']}  {e['label']:<19} {e['read_value']} → {e['value']:<10} read at "
                     f"{hhmmss(e['base']['C']['updated_at'])} · copy C now: {now}"
                     + (f" (updated {hhmmss(cur.get('updated_at'))})" if changed else ""))


def show_trace(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli)
    outbox = _outbox()
    for arm in ("A", "C"):
        rows = blind_overwrites(cli, d, world, outbox, arm, echo=True)
        emit("text", f"\n  copy {arm} — replayed updates and what they replaced:")
        for r in rows:
            emit("text", f"    {r['label']:<19} wrote {r['wrote']:<10} replaced {r['replaced']}"
                         + (f"  ← {r['replaced_by']}'s {r['replaced_at']} change, never read by eu-west" if r["blind"]
                            else "  (what eu-west read)"))
        if not rows:
            emit("text", "    none")


def show_review(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli)
    p = OUT / "held.json"
    held = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    if not held:
        emit("text", "  nothing held for review")
        return
    emit("text", f"  Held for review in copy C — eu-west's change vs what changed while it was cut off:\n")
    for h in held:
        f = next(x for x in d["fields"] if x["key"] == h["field"])
        cur = [e for e in read_runbook(cli, d, world["C"], echo=False)[h["field"]]]
        emit("text", f"  {f['label']}")
        emit("text", f"    eu-west  {h['at']}  {h['read']} → {h['proposed']}   ({h['why']})   — read before the partition")
        emit("text", f"    {h['by']:<8} {h['by_at']}  → {h['now']}   — the runbook now says {says(cur)}")
        later = h["by_at"] > h["at"]
        emit("text", f"    → {h['by']}'s change is the later decision" if later else "    → eu-west's change is the later one")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    nfacts, nproj = 0, 0
    for arm in ARMS:
        p = find_project(cli, arm, echo=not quiet and arm == "A")
        if not p:
            continue
        left = project_facts(cli, p["id"])
        for attempt in range(4):
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda f: delete_fact(cli, f["id"], ["--project", p["id"]]), left))
            nfacts += len(left)
            left = project_facts(cli, p["id"])
            if not left:
                break
            time.sleep(2 * (attempt + 1))
        if left:
            die(f"{len(left)} fact(s) are still in {p['id']} after deleting; run cleanup again")
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet)
        nproj += 1
    if not quiet:
        note(f"deleted {nfacts} facts and {nproj} projects (their conflicts go with them)" if nproj else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "outbox", "held"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "outbox", "trace", "review", "cleanup"],
                    help="run = full demo (default); outbox = eu-west's journal vs the runbook now; trace = what each "
                         "replayed update replaced; review = what copy C held for a person; cleanup = delete everything")
    ap.add_argument("--reset", action="store_true", help="accepted for symmetry: every run starts over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    titles = {"cleanup": "Cleanup — remove everything the demo created", "outbox": "eu-west's outbox",
              "trace": "What each replayed update replaced — fact trace", "review": "Held for review"}
    if args.command in titles:
        banner(2, 2, titles[args.command])
        {"cleanup": cleanup, "outbox": show_outbox, "trace": show_trace, "review": show_review}[args.command](cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py review`, `python3 demo.py trace` or `python3 demo.py outbox`; "
          "`python3 demo.py cleanup` removes the demo data.")


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

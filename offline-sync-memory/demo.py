#!/usr/bin/env python3
"""offline-sync-memory — an edge agent goes offline, then syncs: replaying its transcript lets an older request win.

Fernhill Garden Centres (fictional) runs a kiosk assistant, Sprout, in Store 14. Order memory is one fact per order
on a MemoryLake project. The kiosk loses its connection for 70 seconds (in real time). While it is offline it keeps
answering from a local snapshot and writes customers' change requests down locally; HQ, still online, changes some of
the same orders. Then the kiosk reconnects and syncs — three ways, each on its own copy of the orders project:

  A  replay the transcript  — append the kiosk's conversation with each message's own `--timestamp`;
                              MemoryLake's extractor updates the orders from it
  B  version check          — an outbox of changes; `fact get` each order and apply only if its `updated_at` is
                              still the one in the offline snapshot, else hold the change for review
  C  request vs last write  — the same outbox; `fact trace` each order and apply only if its last write is older
                              than the customer's request, else keep it and log the request as superseded

The extractor applies the replayed messages in the order they arrive, not by their timestamps: a request the kiosk
heard 30 seconds before HQ's change overwrites it ("previously Saturday …"). B never overwrites, but it also holds a
request that came after HQ's change. C keeps the later of the two each time.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a MemoryLake API key.
No model calls: nothing is billed to your MemoryLake balance.
"""
from __future__ import annotations

import argparse
import hashlib
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-ofs"  # memorylake-usecases / offline-sync-memory


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
    rc, out, _ = cli.raw("fact", "trace", "--help", echo=False)
    if rc != 0 or "FORGET" not in out:
        die("this memorylake CLI has no `fact trace` with full history; it needs v20261015 or later.\n"
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

TOTAL = 6             # steps in a full run
ARMS = ("A", "B", "C")
EXTRACT_LIMIT = 240   # seconds to wait for the extractor before calling it deferred
CLOSE_CALL = 2.0      # C: a request and a write closer than this (seconds) are held, not decided by clocks
UNREACHABLE = "http://127.0.0.1:9/openapi/memorylake"   # the kiosk's view of the cloud while it is offline


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def arm_key(arm: str) -> str:
    return f"orders-{arm.lower()}"


def find_project(cli: CLI, key: str = "orders-a", echo: bool = False):
    return cli.try_run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)


def find_actor(cli: CLI, key: str, echo: bool = False):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id", echo=echo)


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


def actor_facts(cli: CLI, actor: str, echo: bool = False) -> list[dict]:
    return paged(cli, ["fact", "list", "--actors", actor, "--page-size", "200"], echo)


def demo_convs(cli: CLI) -> list[dict]:
    return [c for c in paged(cli, ["conv", "list", "--page-size", "100"])
            if (c.get("custom_id") or "").startswith(PREFIX + "-")]


def delete_fact(cli: CLI, fact_id: str, scope: list[str], echo: bool = False) -> None:
    """`fact delete` is safe to repeat: retry on a dropped connection or a 5xx, and a 404 means it is already gone."""
    for attempt in range(5):
        try:
            cli.run("fact", "delete", fact_id, *scope, scoped=True, echo=echo and attempt == 0)
            return
        except CLIError as e:
            if e.has("404", "not found"):
                return
            if attempt == 4 or not e.has(*CLI.TRANSIENT):
                raise
            time.sleep(2 * (attempt + 1))


def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and `fact delete` then
    fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes first, the list must come back empty,
    then the actor. A sealed (unbound) actor answers 404 to `fact list`, so it is bound back first."""
    try:
        found = actor_facts(cli, actor_id)
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        for wait in (1, 2, 4, 8, 0):  # the binding takes a moment to show
            try:
                found = actor_facts(cli, actor_id)
                break
            except CLIError as e2:
                if not e2.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    left = found
    for attempt in range(4):
        pmap(lambda f: delete_fact(cli, f["id"], ["--actor", actor_id]), left)
        left = actor_facts(cli, actor_id)
        if not left:
            break
        time.sleep(2 * (attempt + 1))
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id, echo=False)
    return len(found)


def pmap(fn, items, workers: int = 4) -> list:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, items))


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def hms(iso: str | None) -> str:
    return (iso or "")[11:19]


def parse_ts(iso: str) -> datetime:
    s = iso.replace("Z", "+00:00")
    # Python 3.9's fromisoformat wants 0, 3 or 6 fractional digits.
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
    if m and m.group(2):
        s = m.group(1) + (m.group(2) + "000000")[:7] + m.group(3)
    return datetime.fromisoformat(s)


def save(name: str, obj) -> Path:
    p = OUT / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def load(name: str):
    p = OUT / f"{name}.json"
    if not p.exists():
        die(f"no out/{name}.json yet; run `python3 demo.py` first")
    return json.loads(p.read_text(encoding="utf-8"))


DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DATE_RE = re.compile(r"\b(?:(Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,? (\d{1,2}) October|(2026)-10-(\d{2})|October (\d{1,2}))\b")


def date_of(text: str | None) -> str:
    """The date a fact gives, as "Fri 23 Oct": the first one in the text. A rewrite keeps the old date later, in
    "previously …", so only the first counts. The extractor sometimes writes 2026-10-23 instead."""
    m = DATE_RE.search(text or "")
    if not m:
        return "no date"
    day = int(m.group(2) or m.group(4) or m.group(5))
    return f"{DAYS[datetime(2026, 10, day).weekday()][:3]} {day} Oct"


def short(date: str) -> str:
    """"Friday 23 October" → "Fri 23 Oct"."""
    return date_of(date)


def fact_for(facts: list[dict], order: str) -> dict | None:
    """What the kiosk reads for an order: the newest fact that names it (`fact list --query <order>` is newest first)."""
    hits = [f for f in facts if order in (f.get("fact") or "")]
    return max(hits, key=lambda f: f.get("updated_at") or "") if hits else None


# --------------------------------------------------------------------------- the story's own expectations

def check_story() -> dict:
    """The rules on the story itself, before any call is made: a wrong story would misread every run.
    The truth for each order is its latest change by the time it happened; B and C are deterministic, so what they
    must do is worked out here too. A is measured."""
    d = load_data()
    orders = {o["order"]: o for o in d["orders"]}
    t = sorted(d["timeline"], key=lambda e: e["at"])
    if any(e["order"] not in orders for e in t) or len({e["at"] for e in t}) != len(t):
        die("story self-check failed: unknown order or two events at the same second")
    if max(e["at"] for e in t) >= d["offline_secs"]:
        die("story self-check failed: an event falls after the kiosk reconnects")
    truth = {o: orders[o]["start"] for o in orders}
    hq_at, kiosk = {}, []
    for e in t:
        if e["who"] in ("hq", "kiosk"):
            truth[e["order"]] = e["date"]
        if e["who"] == "hq":
            hq_at[e["order"]] = e["at"]
        if e["who"] == "kiosk":
            kiosk.append(e)
    b_hold = sorted(e["order"] for e in kiosk if e["order"] in hq_at)
    c_hold = sorted(e["order"] for e in kiosk if hq_at.get(e["order"], -1) > e["at"])
    if not b_hold or not c_hold or set(c_hold) == set(b_hold):
        die("story self-check failed: the story should have a collision C holds and one only B holds")
    return dict(truth={o: short(v) for o, v in truth.items()}, b_hold=b_hold, c_hold=c_hold)


# --------------------------------------------------------------------------- step 2: memory

def leftovers(cli: CLI) -> bool:
    return bool(any(find_project(cli, arm_key(a)) for a in ARMS) or find_project(cli, "kiosk-log")
                or find_actor(cli, load_data()["kiosk"]["key"]) or demo_convs(cli))


def setup(cli: CLI, d: dict) -> dict:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    texts = [o["template"].format(date=o["start"]) for o in d["orders"]]
    world = dict(projects={}, ids={})
    for a in ARMS:
        p = cli.run("proj", "create", "--name", f"{d['project_name']} ({d['arms'][a]['short']})", "--custom-id",
                    cid(arm_key(a)), scoped=True, echo=a == "A")["id"]
        world["projects"][a] = p
        r = cli.run("fact", "add", *texts, "--project", p, scoped=True, echo=a == "A") or {}
        facts = r.get("facts") or []
        if len(facts) != len(texts):
            die(f"`fact add` returned {len(facts)} ids for {len(texts)} texts")
        world["ids"][a] = {o["order"]: f["id"] for o, f in zip(d["orders"], facts)}
        save("world", world)   # every object is on disk the moment it exists, so cleanup can find it
    note("the same 4 orders on three copies of the project, one per sync strategy (B and C: same commands, quietly)")
    world["log"] = cli.run("proj", "create", "--name", d["log_project_name"], "--custom-id", cid("kiosk-log"),
                           scoped=True, echo=True)["id"]
    save("world", world)
    k = d["kiosk"]
    world["actor"] = cli.run("actor", "create", "--custom-id", cid(k["key"]), "--display-name", k["display"],
                             "--description", f"{k['role']}. Demo data.", "--tags", "demo", echo=True)["id"]
    save("world", world)
    cli.run("actor", "bind", "--actor", world["actor"], scoped=True, echo=True)
    world["conv_a"] = cli.run("conv", "create", "--custom-id", cid("kiosk-a"), "--project", world["projects"]["A"],
                              "--actors", world["actor"], "--kind", "DIRECT", "--name", "Store 14 kiosk",
                              scoped=True, echo=True)["id"]
    save("world", world)
    world["conv_log"] = cli.run("conv", "create", "--custom-id", cid("kiosk-log"), "--project", world["log"],
                                "--actors", world["actor"], "--kind", "DIRECT", "--name", "Store 14 kiosk transcript",
                                scoped=True, echo=True)["id"]
    save("world", world)
    emit("text", f"\n    {'order':<7} {'customer':<13} {'item':<16} date")
    for o in d["orders"]:
        emit("text", f"    {o['order']:<7} {o['customer']:<13} {o['item']:<16} {o['start']}")
    note("A's kiosk conversation sits on A's orders project, so the extractor reads it there; B and C sync the same "
         "transcript to a separate kiosk-log project, where it cannot touch an order")
    emit("setup", "", orders=d["orders"], **world)
    return world


# --------------------------------------------------------------------------- step 3/4: offline

def snapshot(cli: CLI, world: dict) -> dict:
    """The kiosk's local cache: every order fact with its id and `updated_at`, taken while the network is up."""
    snap = {}
    for i, a in enumerate(ARMS):
        facts = project_facts(cli, world["projects"][a], echo=i == 0)
        snap[a] = {f["id"]: dict(text=f.get("fact"), updated_at=f.get("updated_at")) for f in facts}
    return snap


def hq_edit(cli: CLI, d: dict, world: dict, e: dict) -> str:
    """HQ's console writes the change to every copy at once (`fact update`, same fact id)."""
    o = next(x for x in d["orders"] if x["order"] == e["order"])
    body = o["template"].format(date=e["date"])
    pmap(lambda a: cli.run("fact", "update", world["ids"][a][e["order"]], "--text", body, "--project",
                           world["projects"][a], scoped=True, echo=a == "A"), ARMS, workers=3)
    return body


def offline_read(cli: CLI, world: dict, snap: dict, e: dict) -> dict:
    """The kiosk tries the cloud (it is unreachable), then answers from its snapshot."""
    rc, out, err = cli.raw("fact", "list", "--projects", world["projects"]["A"], "--query", e["order"],
                           "--base-url", UNREACHABLE, scoped=True)
    reason = " ".join((err or out).split())[:110]
    cached = next((v for v in snap["A"].values() if e["order"] in (v["text"] or "")), {})
    return dict(order=e["order"], asks=e["asks"], rc=rc, reason=reason, answer=date_of(cached.get("text")),
                cached_at=snap["taken_at"])


def offline(cli: CLI, d: dict, world: dict, snap: dict) -> dict:
    """The offline window, in real time: the kiosk hears requests, HQ makes changes, a customer asks."""
    t0 = time.time()
    outbox, events, read = [], [], None
    for e in sorted(d["timeline"], key=lambda x: x["at"]):
        wait = t0 + e["at"] - time.time()
        if wait > 0:
            time.sleep(wait)
        at = now_iso()
        if e["who"] == "kiosk":
            entry = dict(order=e["order"], date=e["date"], says=e["says"], at=at, offset=e["at"],
                         seen={a: next(v["updated_at"] for i, v in snap[a].items() if i == world["ids"][a][e["order"]])
                               for a in ARMS})
            outbox.append(entry)
            save("outbox", outbox)
            emit("text", f"    +{e['at']:>2} s  kiosk (offline)  {e['says']}")
            emit("text", f"            → written to the kiosk's local outbox at {hms(at)}: order {e['order']} → {e['date']}")
        elif e["who"] == "hq":
            body = hq_edit(cli, d, world, e)
            emit("text", f"    +{e['at']:>2} s  HQ (online)      {e['why']}")
            emit("text", f"            → `fact update` at {hms(at)}: {body}")
        else:
            emit("text", f"    +{e['at']:>2} s  kiosk (offline)  {e['asks']}")
            read = offline_read(cli, world, snap, e)
            emit("text", f"            → cloud unreachable ({read['reason'][:70]}…)" if read["rc"] else
                         "            → (the cloud answered: the network was not really down)")
            emit("text", f"            → answered from the local snapshot taken at {hms(read['cached_at'])}: "
                         f"{read['answer']}")
            emit("offline_read", "", **read)
        events.append(dict(at=at, offset=e["at"], who=e["who"], order=e["order"], date=e.get("date"),
                           why=e.get("why"), says=e.get("says"), asks=e.get("asks")))
        emit("tick", "", **events[-1])
    wait = t0 + d["offline_secs"] - time.time()
    if wait > 0:
        time.sleep(wait)
    note(f"back online after {d['offline_secs']} s: {len(outbox)} requests in the outbox, HQ changed "
         f"{sum(e['who'] == 'hq' for e in events)} orders meanwhile")
    out = dict(outbox=outbox, events=events, read=read, reconnected=now_iso())
    save("offline", out)
    return out


# --------------------------------------------------------------------------- step 5: sync

def append_transcript(cli: CLI, conv: str, actor: str, outbox: list[dict], echo: bool) -> None:
    """The kiosk's conversation, each message with the time the customer said it."""
    for i, x in enumerate(outbox):
        cli.run("conv", "msg", "append", conv, "--actor", actor, "--custom-id", f"kiosk-{x['order']}",
                "--timestamp", x["at"], "--text", x["says"], scoped=True, echo=echo and i in (0, len(outbox) - 1))


def wait_extraction(cli: CLI, world: dict, before: dict, t_append: float) -> dict:
    """Until the extractor has written to A's orders and the conversation's cook is finished. `--wait` on the append
    can report finished before the new messages are picked up, so the facts themselves are watched."""
    first, finished_polls = True, 0
    while time.time() - t_append < EXTRACT_LIMIT:
        time.sleep(4)
        now = {f["id"]: f.get("updated_at") for f in project_facts(cli, world["projects"]["A"])}
        changed = [i for i in now if before.get(i) != now[i]]
        st = cli.run("conv", "cook-status", world["conv_a"], scoped=True, echo=first) or {}
        first = False
        finished_polls = finished_polls + 1 if st.get("cook_finished") else 0
        if (changed and st.get("cook_finished")) or (finished_polls >= 3 and time.time() - t_append > 60):
            time.sleep(6)   # one cook can write several facts; let the rest land
            return dict(secs=round(time.time() - t_append), changed=len(changed))
        emit("progress", f"    … A: waiting for the extractor ({round(time.time() - t_append)} s)", phase="wait")
    return dict(secs=EXTRACT_LIMIT, changed=0, deferred=True)


def sync_b(cli: CLI, d: dict, world: dict, off: dict) -> dict:
    p, rows = world["projects"]["B"], []
    for i, x in enumerate(off["outbox"]):
        f = cli.run("fact", "get", world["ids"]["B"][x["order"]], "--project", p, scoped=True, echo=i == 0) or {}
        unchanged = f.get("updated_at") == x["seen"]["B"]
        if unchanged:
            o = next(y for y in d["orders"] if y["order"] == x["order"])
            cli.run("fact", "update", f["id"], "--text", o["template"].format(date=x["date"]), "--project", p,
                    scoped=True, echo=True)
        rows.append(dict(order=x["order"], date=x["date"], at=x["at"], seen=x["seen"]["B"], now=f.get("updated_at"),
                         applied=unchanged, held=not unchanged, current=date_of(f.get("fact"))))
        emit("text", f"    B  {x['order']}  updated_at {hms(x['seen']['B'])} in the snapshot, {hms(f.get('updated_at'))} now → "
                     + ("unchanged: applied" if unchanged else f"changed: held for review (keeps {date_of(f.get('fact'))})"))
    held = [r for r in rows if r["held"]]
    if held:
        cli.run("fact", "add", *[f"Review (B, kiosk sync): order {r['order']} was changed at HQ while Store 14's kiosk "
                                 f"was offline; the kiosk's request of {hms(r['at'])} UTC for {r['date']} is on hold."
                                 for r in held], "--project", world["log"], scoped=True, echo=True)
    return dict(rows=rows)


def last_write(cli: CLI, fact_id: str, project: str, echo: bool) -> dict:
    tr = (cli.run("fact", "trace", fact_id, "--project", project, scoped=True, echo=echo) or {}).get("trace") or []
    top = tr[0] if tr else {}
    return dict(at=top.get("timestamp"), event=top.get("event"), source=top.get("source_kind"),
                text=top.get("new_fact"), old=top.get("old_fact"), entries=len(tr))


def sync_c(cli: CLI, d: dict, world: dict, off: dict) -> dict:
    p, rows = world["projects"]["C"], []
    for i, x in enumerate(off["outbox"]):
        w = last_write(cli, world["ids"]["C"][x["order"]], p, echo=i == 0)
        gap = (parse_ts(x["at"]) - parse_ts(w["at"])).total_seconds() if w["at"] else None
        if gap is None or abs(gap) < CLOSE_CALL:
            verdict = "close"
        else:
            verdict = "apply" if gap > 0 else "superseded"
        if verdict == "apply":
            o = next(y for y in d["orders"] if y["order"] == x["order"])
            cli.run("fact", "update", world["ids"]["C"][x["order"]], "--text", o["template"].format(date=x["date"]),
                    "--project", p, scoped=True, echo=True)
        rows.append(dict(order=x["order"], date=x["date"], at=x["at"], last_write=w, gap=gap, verdict=verdict,
                         applied=verdict == "apply", held=verdict != "apply"))
        emit("text", f"    C  {x['order']}  request {hms(x['at'])} vs last write {hms(w['at'])} "
                     f"({w['event']} · {w['source']}) → "
                     + {"apply": "request is newer: applied",
                        "superseded": f"HQ wrote {abs(gap):.0f} s after the request: kept {date_of(w['text'])}, request logged",
                        "close": "too close to call on two clocks: held"}[verdict])
    held = [r for r in rows if r["held"]]
    if held:
        cli.run("fact", "add", *[f"Superseded (C, kiosk sync): order {r['order']}: the kiosk's request of {hms(r['at'])} UTC "
                                 f"for {r['date']} came before HQ's change at {hms(r['last_write']['at'])} UTC; "
                                 f"tell the customer the order stays on {date_of(r['last_write']['text'])}."
                                 for r in held], "--project", world["log"], scoped=True, echo=True)
    return dict(rows=rows)


def sync(cli: CLI, d: dict, world: dict, off: dict) -> dict:
    note("A: the transcript goes to the orders project, each message with its own time; the extractor decides")
    before = {f["id"]: f.get("updated_at") for f in project_facts(cli, world["projects"]["A"])}
    t_a = time.time()
    append_transcript(cli, world["conv_a"], world["actor"], off["outbox"], echo=True)
    note("B and C: the same transcript to the kiosk-log project (audit), then the outbox, one order at a time")
    append_transcript(cli, world["conv_log"], world["actor"], off["outbox"], echo=False)
    b = sync_b(cli, d, world, off)
    c = sync_c(cli, d, world, off)
    a = wait_extraction(cli, world, before, t_a)
    note(f"A: the extractor touched {a['changed']} fact(s) on the orders project, done {a['secs']} s after the first append"
         if not a.get("deferred") else f"A: the extractor had not finished after {EXTRACT_LIMIT} s; scoring what is there")
    out = dict(A=a, B=b, C=c)
    emit("synced", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: score

def message_times(cli: CLI, conv: str) -> dict:
    # `conv msg list` takes no --workspace.
    msgs = (cli.run("conv", "msg", "list", conv, "--page-size", "100", echo=False) or {}).get("items") or []
    return {m["id"]: m.get("timestamp") for m in msgs}


def score(cli: CLI, d: dict, world: dict, off: dict, sy: dict, exp: dict) -> dict:
    truth = exp["truth"]
    finals, rows = {}, {}
    for i, a in enumerate(ARMS):
        finals[a] = project_facts(cli, world["projects"][a], echo=i == 0)
    st = d["arms"]
    emit("text", f"\n    {'order':<6} {'customer':<12} {'should be':<12} " + " ".join(f"{st[a]['short']:<26}" for a in ARMS))
    held = {"B": {r["order"] for r in sy["B"]["rows"] if r["held"]}, "C": {r["order"] for r in sy["C"]["rows"] if r["held"]}}
    for o in d["orders"]:
        cells = []
        for a in ARMS:
            f = fact_for(finals[a], o["order"])
            got = date_of(f.get("fact") if f else None)
            ok = got == truth[o["order"]]
            mark = "✓" if ok else "✗"
            tag = " (held)" if o["order"] in held.get(a, set()) else ""
            rows.setdefault(a, []).append(dict(order=o["order"], got=got, ok=ok, held=bool(tag), fact=f and f.get("fact"),
                                               fact_id=f and f.get("id")))
            cells.append(f"{mark} {got}{tag}")
        emit("text", f"    {o['order']:<6} {o['customer']:<12} {truth[o['order']]:<12} " + " ".join(f"{c:<26}" for c in cells))
    summary = {}
    for a in ARMS:
        r = rows[a]
        wrong = [x["order"] for x in r if not x["ok"]]
        summary[a] = dict(current=sum(x["ok"] for x in r), of=len(r), wrong=wrong,
                          silent=[o for o in wrong if o not in held.get(a, set())],
                          held=sorted(held.get(a, set())),
                          false_holds=sorted(o for o in held.get(a, set()) if o not in exp["c_hold"]))
    emit("text", "    " + "   ".join(f"{st[a]['short']}: {summary[a]['current']}/{summary[a]['of']} current"
                                   + (f", {len(summary[a]['silent'])} overwritten silently" if summary[a]['silent'] else "")
                                   + (f", {len(summary[a]['held'])} held" if summary[a]['held'] else "") for a in ARMS))

    # A: how did the wrong ones get there? Every fact that names the order, the history of the one the kiosk reads,
    # and the time on the message that wrote it. The extractor either rewrites the order's own fact ("previously …")
    # or adds a second fact beside it; both have been seen.
    explain = []
    if summary["A"]["wrong"]:
        mt = message_times(cli, world["conv_a"])
        for k, o in enumerate(summary["A"]["wrong"]):
            row = next(x for x in rows["A"] if x["order"] == o)
            named = sorted((f for f in finals["A"] if o in (f.get("fact") or "")), key=lambda f: f.get("updated_at") or "", reverse=True)
            emit("text", f"\n    A, order {o}: {len(named)} fact(s) name it, newest first (the kiosk reads the first):")
            facts_named = []
            for i, f in enumerate(named):
                tr = (cli.run("fact", "trace", f["id"], "--project", world["projects"]["A"], scoped=True, echo=k == 0 and i == 0) or {}).get("trace") or []
                top = tr[0] if tr else {}
                said = sorted(mt[s] for s in top.get("source_entry_ids") or [] if mt.get(s))
                facts_named.append(dict(id=f["id"], text=f.get("fact"), date=date_of(f.get("fact")), event=top.get("event"),
                                        source=top.get("source_kind"), at=top.get("timestamp"), said=said,
                                        own=f["id"] == world["ids"]["A"][o], old=top.get("old_fact")))
                x = facts_named[-1]
                emit("text", f"      {x['date']:<11} last write {x['event'] or '?'} · {x['source'] or '?'} {hms(x['at'])}"
                             + (f", from a message said at {', '.join(hms(s) for s in said)}" if said else "")
                             + ("  (the order's own fact)" if x["own"] else "  (a new fact)"))
            req = next((x for x in off["outbox"] if x["order"] == o), None)
            hq = next((x for x in off["events"] if x["who"] == "hq" and x["order"] == o), None)
            explain.append(dict(order=o, facts=facts_named, request=req, hq=hq))
            read_by = facts_named[0] if facts_named else {}
            if read_by.get("source") == "COOK" and req and hq and req["at"] < hq["at"]:
                emit("text", f"    the customer asked at {hms(req['at'])}; HQ changed it at {hms(hq['at'])}; the extractor "
                             f"wrote the older request last — the kiosk now reads {row['got']}")
            elif read_by.get("source") != "COOK" and req:
                emit("text", f"    the customer asked at {hms(req['at'])} for {short(req['date'])}; the extractor wrote nothing "
                             f"for this order — the request is gone, the kiosk reads {row['got']}")
    for x in sy["C"]["rows"]:
        if x["verdict"] == "superseded":
            note(f"C kept order {x['order']} on {date_of(x['last_write']['text'])} and logged the {hms(x['at'])} request as "
                 "superseded in the kiosk-log project — the customer gets a call, not a silent change")
    for o in summary["B"]["false_holds"]:
        r = next(x for x in sy["B"]["rows"] if x["order"] == o)
        note(f"B held order {o}: HQ had changed it, so its version moved — but the customer's {hms(r['at'])} request came "
             f"after that change; it waits for a person, and the order still says {r['current']}")
    rd = off.get("read")
    follow = None
    if rd:
        now = date_of((fact_for(finals["C"], rd["order"]) or {}).get("fact"))
        follow = dict(order=rd["order"], told=rd["answer"], now=now, stale=now != rd["answer"])
        note(f"offline answer to order {rd['order']}: told {rd['answer']} from the snapshot; memory now says {now}"
             + (" → the kiosk flags a call-back" if follow["stale"] else " → still right"))

    # What the extractor did with A's transcript, over every fact on A's project: rewrote orders, added facts, nothing.
    tops = pmap(lambda f: ((cli.run("fact", "trace", f["id"], "--project", world["projects"]["A"], scoped=True, echo=False)
                            or {}).get("trace") or [{}])[0], finals["A"])
    own = set(world["ids"]["A"].values())
    shape = dict(rewrote=sum(1 for f, x in zip(finals["A"], tops) if x.get("source_kind") == "COOK" and f["id"] in own),
                 added=sum(1 for f, x in zip(finals["A"], tops) if x.get("source_kind") == "COOK" and f["id"] not in own))
    shape["label"] = ("rewrote the orders' own facts" if shape["rewrote"] and not shape["added"] else
                      "added facts beside the orders'" if shape["added"] and not shape["rewrote"] else
                      "rewrote some, added some" if shape["added"] else "wrote nothing")
    summary["A"]["extractor"] = shape
    note(f"A's extractor {shape['label']} ({shape['rewrote']} rewritten, {shape['added']} added) — "
         f"{summary['A']['current']}/{summary['A']['of']} orders current")

    # The deterministic arms must do what the story says, or the run is misread.
    if sorted(held["B"]) != exp["b_hold"] or sorted(held["C"]) != exp["c_hold"]:
        die(f"B/C disagree with the story: held B {sorted(held['B'])} C {sorted(held['C'])}; expected {exp}")
    if summary["C"]["current"] != summary["C"]["of"]:
        die(f"C should be current on every order: {summary['C']}")
    report = dict(at=now_iso(), prefix=PREFIX, truth=truth, summary=summary, rows=rows, explain=explain,
                  sync=sy, offline=off, follow_up=follow, expected=exp)
    save("report", report)
    emit("score", "", summary=summary, rows=rows, explain=explain, truth=truth, follow_up=follow)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    exp = check_story()
    banner(2, TOTAL, "Memory — 4 orders, on three copies of the project (one per sync strategy)")
    world = setup(cli, d)
    banner(3, TOTAL, "Snapshot, then the network drops")
    snap = snapshot(cli, world)
    snap["taken_at"] = now_iso()
    save("snapshot", snap)
    note(f"the kiosk caches every order locally at {hms(snap['taken_at'])} — its offline memory")
    for a in ARMS:
        note(f"{d['arms'][a]['short']}: {d['arms'][a]['how'].replace('`', '')}")
    emit("snapshot", "", taken_at=snap["taken_at"], orders={o: date_of(v["text"]) for o, v in
                                                              ((o["order"], next(x for x in snap["A"].values() if o["order"] in (x["text"] or "")))
                                                               for o in d["orders"])})
    banner(4, TOTAL, f"Offline for {d['offline_secs']} s — the kiosk hears requests, HQ changes orders")
    off = offline(cli, d, world, snap)
    banner(5, TOTAL, "Reconnect and sync — three ways")
    sy = sync(cli, d, world, off)
    banner(6, TOTAL, "Score — which orders are right, which were overwritten, which wait for a person?")
    return score(cli, d, world, off, sy, exp)


# --------------------------------------------------------------------------- read-only subcommand

def show_timeline(cli: CLI) -> None:
    world, off = load("world"), load("offline")
    d = load_data()
    emit("text", "\n  the offline window (UTC):")
    for e in off["events"]:
        what = {"kiosk": f"kiosk heard: order {e['order']} → {e['date']}", "hq": f"HQ changed: order {e['order']} → {e['date']}",
                "read": f"kiosk answered order {e['order']} from its snapshot"}[e["who"]]
        emit("text", f"    {hms(e['at'])}  +{e['offset']:>2} s  {what}")
    first = True
    for a in ARMS:
        p = world["projects"][a]
        facts = project_facts(cli, p)
        emit("text", f"\n  {d['arms'][a]['short']} — every fact that names an order, newest first, with its history (`fact trace`):")
        for o in d["orders"]:
            named = sorted((f for f in facts if o["order"] in (f.get("fact") or "")), key=lambda f: f.get("updated_at") or "", reverse=True)
            for j, f in enumerate(named):
                tr = (cli.run("fact", "trace", f["id"], "--project", p, scoped=True, echo=first) or {}).get("trace") or []
                first = False
                chain = " → ".join(f"{date_of(x.get('new_fact'))} ({x.get('source_kind')} {hms(x.get('timestamp'))})" for x in reversed(tr))
                emit("text", f"    {o['order'] if j == 0 else '':<6} {chain}")
    log = project_facts(cli, world["log"], echo=True)
    notes = [f["fact"] for f in log if (f.get("fact") or "").startswith(("Review", "Superseded"))]
    emit("text", f"\n  kiosk-log project: {len(notes)} review note(s) written by B and C")
    for n in notes:
        emit("text", f"    {n}")
    a_facts = project_facts(cli, world["projects"]["A"])
    cooked = [f for f in a_facts if any(o["order"] in (f.get("fact") or "") for o in d["orders"])
              and ((cli.run("fact", "trace", f["id"], "--project", world["projects"]["A"], scoped=True, echo=False)
                    or {}).get("trace") or [{}])[0].get("source_kind") == "COOK"]
    note("in A, the extractor's writes (COOK) landed after HQ's (MANUAL), whatever time their messages carry: "
         "it orders by arrival, not by the message's timestamp" if cooked else
         "in A, the extractor wrote nothing from the replayed messages this time: the customers' requests are not in memory")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = demo_convs(cli)
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=not quiet)
    nfacts, nproj = 0, 0
    a = find_actor(cli, d["kiosk"]["key"], echo=not quiet)
    if a:
        nfacts += delete_actor(cli, a["id"])
    for key in [arm_key(x) for x in ARMS] + ["kiosk-log"]:
        p = find_project(cli, key, echo=not quiet and key == "orders-a")
        if not p:
            continue
        left = project_facts(cli, p["id"])
        for attempt in range(4):
            pmap(lambda f: delete_fact(cli, f["id"], ["--project", p["id"]]), left)
            nfacts += len(left)
            left = project_facts(cli, p["id"])
            if not left:
                break
            time.sleep(2 * (attempt + 1))
        if left:
            die(f"{len(left)} fact(s) are still in {p['id']} after deleting; run cleanup again")
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet and key == "orders-a")
        nproj += 1
    if not quiet:
        note(f"deleted {len(convs)} conversations, {nfacts} facts, {1 if a else 0} actor (facts first: a deleted actor's "
             f"facts could never be removed) and {nproj} projects" if (convs or a or nproj) else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "world", "offline", "outbox", "snapshot"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(parents=True, exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "timeline", "cleanup"],
                    help="run = full demo (default); timeline = the offline window against each order's history; "
                         "cleanup = delete everything")
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
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "timeline":
        banner(2, 2, "Timeline — the offline window against each order's history")
        show_timeline(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py timeline`; `python3 demo.py cleanup` removes the demo data.")


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

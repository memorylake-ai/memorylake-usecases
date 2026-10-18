#!/usr/bin/env python3
"""recurring-failure-memory — the same tool failure, four times in a week: memory keeps the lesson, loses the count.

Copperleaf Billing (fictional) runs a nightly billing agent, Tally. Six nights, three tools, and the same failure
four times: the Ledgerline API rate-limits `export_invoices` with HTTP 429. Each night's failure is remembered two ways,
side by side, each on its own MemoryLake project:

  A  run log          — the night's run log goes to a conversation on the runs project; MemoryLake's extractor turns
                        it into memory
  B  failure records  — the agent's tool error handler writes one typed record per failure (`fact add`, then
                        `fact update --metadata` with run, date, tool, error, recovery, outcome)

Before tonight's run, the agent checks its memory for earlier failures of the tool it is about to call, and applies one
rule: the same tool failing with the same error 3 or more times in 7 days is a pattern — stop retrying, open a
systematic fix. The extractor files a repeat of a known failure as the knowledge it already has ("the call can fail
with HTTP 429 …"), not as another event: four failures leave one or two facts and as few history entries, so A never
gets to three. B has four records and would have flagged the pattern on the third night.

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

PREFIX = "mlu-rfm"  # memorylake-usecases / recurring-failure-memory


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
ARMS = ("A", "B")
NIGHT_LIMIT = 120     # seconds to wait for the extractor after a night's run log before moving on


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def arm_key(arm: str) -> str:
    return {"A": "runs", "B": "records"}[arm]


def find_project(cli: CLI, key: str = "runs", echo: bool = False):
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


# --------------------------------------------------------------------------- the story

ISO_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def log_text(r: dict) -> str:
    """One night's run log, as the agent writes it: one message, speaker label first."""
    where = f" on {r['where']}" if r["where"] else ""
    err = f"failed with {r['error']}" if r["error"].startswith("HTTP") else r["error"]
    return (f"Tally (nightly billing agent, run log): Nightly run {r['run']}: the {r['tool']} call to the {r['api']} "
            f"{err}{where}. Recovery: {r['recovery']}; {r['outcome']}.")


def record_text(r: dict) -> str:
    """One typed failure record, as the tool error handler writes it."""
    where = f" at {r['where']}" if r["where"] else ""
    return (f"Tool failure record - run {r['run']} ({r['date']}): tool {r['tool']} ({r['api']}); error: {r['error']}"
            f"{where}; recovery: {r['recovery']}; outcome: {r['outcome']}.")


def record_meta(r: dict) -> dict:
    return {"kind": "tool_failure", "run": r["run"], "date": r["date"], "tool": r["tool"], "api": r["api"],
            "error": r["error"], "recovery": r["recovery"], "outcome": r["outcome"]}


def day(iso: str) -> datetime:
    return datetime.strptime(iso[:10], "%Y-%m-%d")


def in_window(date: str, tonight: str, days: int) -> bool:
    return 0 <= (day(tonight) - day(date)).days <= days


def check_story() -> dict:
    """The truth, from the story itself, before any call: which nights the watched failure happened, on which night
    the rule should first have fired, and which recovery worked last. A wrong story would misread every run."""
    d = load_data()
    w, rule, tonight = d["watch"], d["rule"], d["tonight"]
    hits = [r for r in d["runs"] if r["tool"] == w["tool"] and r["error"].startswith(w["error"])]
    if len(hits) < rule["threshold"] + 1:
        die("story self-check failed: the watched failure should happen more often than the threshold")
    if any(not in_window(r["date"], tonight["date"], rule["window_days"]) for r in hits):
        die("story self-check failed: every occurrence should fall inside tonight's window")
    if len({r["date"] for r in d["runs"]}) != len(d["runs"]):
        die("story self-check failed: two runs on the same date")
    first = hits[rule["threshold"] - 1]
    last = hits[-1]
    if not last.get("worked_with"):
        die("story self-check failed: the last occurrence should name what worked")
    return dict(count=len(hits), dates=[r["date"] for r in hits], runs=[r["run"] for r in hits],
                first_flag=dict(run=first["run"], date=first["date"]), worked_with=last["worked_with"],
                worked_run=last["run"])


# --------------------------------------------------------------------------- reading the two memories

# "batches of 20", "batch size from 50 to 20", "20-invoice batches": the recovery that worked on night 46.
WORKED = re.compile(r"\bbatch(?:es)?\b[^.;]{0,40}?\b20\b|\b20\b[- ](?:\w+[- ])?batch", re.I)


def names_worked(text: str | None) -> bool:
    return bool(text) and bool(WORKED.search(text))


def about_watch(text: str | None, w: dict) -> bool:
    """A fact is about the watched failure if it names the tool (or the invoice export on that API) and the error code.
    The extractor sometimes drops the tool's name ("the nightly invoice export … Ledgerline API"), so both count."""
    t = (text or "").lower()
    api = w["api"].split()[0].lower()
    named = w["tool"].lower() in t or ("invoice export" in t and api in t)
    return named and w["error"].split()[-1] in t


def message_times(cli: CLI, conv: str) -> dict:
    # `conv msg list` takes no --workspace.
    msgs = (cli.run("conv", "msg", "list", conv, "--page-size", "100", echo=False) or {}).get("items") or []
    return {m["id"]: m.get("timestamp") for m in msgs}


def a_reading(facts: list[dict], w: dict) -> dict:
    """What the run-log memory says about the watched failure, read as generously as its text allows: every fact
    about it, and every distinct date written anywhere in those facts (also inside "previously …")."""
    named = sorted((f for f in facts if about_watch(f.get("fact"), w)), key=lambda f: f.get("updated_at") or "", reverse=True)
    dates = sorted({m for f in named for m in ISO_DATE.findall(f.get("fact") or "")})
    return dict(facts=[dict(id=f["id"], text=f.get("fact"), updated_at=f.get("updated_at")) for f in named],
                dates=dates, count=len(dates))


def b_records(cli: CLI, world: dict, w: dict, echo: bool = False) -> list[dict]:
    rows = paged(cli, ["fact", "list", "--projects", world["projects"]["B"], "--query", w["tool"], "--page-size", "100"], echo)
    out = []
    for f in rows:
        m = f.get("metadata") or {}
        if m.get("kind") == "tool_failure" and m.get("tool") == w["tool"] and str(m.get("error", "")).startswith(w["error"]):
            out.append(dict(id=f["id"], text=f.get("fact"), **{k: m.get(k) for k in ("run", "date", "recovery", "outcome")}))
    return sorted(out, key=lambda r: (r["date"] or "", r["run"] or 0))


def b_reading(records: list[dict], d: dict) -> dict:
    tonight, rule = d["tonight"]["date"], d["rule"]
    inside = [r for r in records if r["date"] and in_window(r["date"], tonight, rule["window_days"])]
    return dict(records=records, dates=[r["date"] for r in inside], count=len(inside))


# --------------------------------------------------------------------------- step 2: setup

def leftovers(cli: CLI) -> bool:
    return bool(any(find_project(cli, arm_key(a)) for a in ARMS) or find_actor(cli, load_data()["agent"]["key"])
                or demo_convs(cli))


def setup(cli: CLI, d: dict) -> dict:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    world = dict(projects={})
    names = {"A": d["log_project_name"], "B": d["records_project_name"]}
    for a in ARMS:
        world["projects"][a] = cli.run("proj", "create", "--name", names[a], "--custom-id", cid(arm_key(a)),
                                       scoped=True, echo=True)["id"]
        save("world", world)   # every object is on disk the moment it exists, so cleanup can find it
    g = d["agent"]
    world["actor"] = cli.run("actor", "create", "--custom-id", cid(g["key"]), "--display-name", g["display"],
                             "--description", f"{g['role']}. Demo data.", "--tags", "demo", echo=True)["id"]
    save("world", world)
    cli.run("actor", "bind", "--actor", world["actor"], scoped=True, echo=True)
    world["conv"] = cli.run("conv", "create", "--custom-id", cid("runs"), "--project", world["projects"]["A"],
                            "--actors", world["actor"], "--kind", "DIRECT", "--name", "Tally nightly run log",
                            scoped=True, echo=True)["id"]
    save("world", world)
    note("A: the runs project holds the agent's conversation — the extractor reads it. "
         "B: the records project has no conversation; only the error handler writes to it")
    emit("setup", "", projects=world["projects"], actor=world["actor"], conv=world["conv"],
         names=names, agent=g["display"])
    return world


# --------------------------------------------------------------------------- step 3: six nights

def wait_night(cli: CLI, world: dict, before: dict, t0: float) -> dict:
    """Until the extractor has dealt with the night's message. A repeat may change nothing at all, so "no change" is
    an answer too: the conversation's cook reported finished three polls running and 25 s have passed."""
    finished = 0
    while time.time() - t0 < NIGHT_LIMIT:
        now = {f["id"]: f.get("fact") for f in project_facts(cli, world["projects"]["A"])}
        changed = sorted(i for i in now if before.get(i) != now[i])
        st = cli.run("conv", "cook-status", world["conv"], scoped=True, echo=False) or {}
        finished = finished + 1 if st.get("cook_finished") else 0
        if (changed and finished) or (finished >= 3 and time.time() - t0 >= 25):
            if changed:
                time.sleep(4)   # one cook can write more than one fact; let the rest land
                now = {f["id"]: f.get("fact") for f in project_facts(cli, world["projects"]["A"])}
                changed = sorted(i for i in now if before.get(i) != now[i])
            added = [i for i in changed if i not in before]
            return dict(secs=round(time.time() - t0), added=len(added), rewrote=len(changed) - len(added), facts=now)
        emit("progress", f"      … waiting for the extractor ({round(time.time() - t0)} s)", phase="wait")
        time.sleep(4)
    now = {f["id"]: f.get("fact") for f in project_facts(cli, world["projects"]["A"])}
    changed = [i for i in now if before.get(i) != now[i]]
    return dict(secs=NIGHT_LIMIT, added=sum(1 for i in changed if i not in before),
                rewrote=sum(1 for i in changed if i in before), facts=now, deferred=True)


def nights(cli: CLI, d: dict, world: dict, exp: dict) -> list[dict]:
    w, rule = d["watch"], d["rule"]
    tally, flagged = [], {"A": None, "B": None}
    seen = 0
    facts = {f["id"]: f.get("fact") for f in project_facts(cli, world["projects"]["A"])}
    for i, r in enumerate(d["runs"]):
        watched = r["tool"] == w["tool"] and r["error"].startswith(w["error"])
        seen += watched
        emit("text", f"\n  night {r['run']} · {r['date']} · {r['tool']} ({r['api']}): {r['error']}"
                     + (f" at {r['where']}" if r["where"] else ""))
        emit("text", f"      recovery: {r['recovery']}; {r['outcome']}")
        # B: the error handler's typed record
        f = (cli.run("fact", "add", record_text(r), "--project", world["projects"]["B"], scoped=True, echo=True) or {}).get("facts") or []
        if len(f) != 1:
            die(f"`fact add` returned {len(f)} ids for one record")
        cli.run("fact", "update", f[0]["id"], "--project", world["projects"]["B"], "--metadata",
                json.dumps(record_meta(r)), scoped=True, echo=i == 0)
        # A: the run log, to the conversation the extractor reads
        t0 = time.time()
        cli.run("conv", "msg", "append", world["conv"], "--actor", world["actor"], "--custom-id", f"run-{r['run']}",
                "--timestamp", f"{r['date']}T09:00:00Z", "--text", log_text(r), "--wait", scoped=True, echo=True)
        got = wait_night(cli, world, facts, t0)
        facts = got.pop("facts")
        a = a_reading([dict(id=k, fact=v) for k, v in facts.items()], w)
        b = b_reading(b_records(cli, world, w), d)
        for arm, n in (("A", a["count"]), ("B", b["count"])):
            if flagged[arm] is None and n >= rule["threshold"]:
                flagged[arm] = dict(run=r["run"], date=r["date"])
        shape = (f"{got['added']} new fact(s), {got['rewrote']} rewritten" if got["added"] or got["rewrote"]
                 else "no fact changed")
        emit("text", f"      A run log: extractor done in {got['secs']} s — {shape}"
                     + (" (still running when we moved on)" if got.get("deferred") else ""))
        emit("text", f"      {w['label']} so far: happened {seen} · A remembers {a['count']} · B has {b['count']} record(s)"
                     + (f"   ← B reaches {rule['threshold']} in {rule['window_days']} days: escalate"
                        if flagged["B"] and flagged["B"]["run"] == r["run"] else "")
                     + (f"   ← A reaches {rule['threshold']}" if flagged["A"] and flagged["A"]["run"] == r["run"] else ""))
        row = dict(run=r["run"], date=r["date"], tool=r["tool"], error=r["error"], watched=watched, happened=seen,
                   a=a["count"], a_facts=len(a["facts"]), b=b["count"], extractor=dict(secs=got["secs"], added=got["added"],
                   rewrote=got["rewrote"], deferred=bool(got.get("deferred"))),
                   flag_a=bool(flagged["A"] and flagged["A"]["run"] == r["run"]),
                   flag_b=bool(flagged["B"] and flagged["B"]["run"] == r["run"]))
        tally.append(row)
        save("nights", dict(tally=tally, flagged=flagged))
        emit("night", "", **row, recovery=r["recovery"], outcome=r["outcome"], where=r["where"], api=r["api"])
    return tally


# --------------------------------------------------------------------------- step 4: what memory holds

def memory_view(cli: CLI, d: dict, world: dict) -> dict:
    w = d["watch"]
    a_facts = project_facts(cli, world["projects"]["A"], echo=True)
    emit("text", f"\n  A — the runs project: {len(a_facts)} fact(s) from {len(d['runs'])} run logs")
    for f in sorted(a_facts, key=lambda f: f.get("updated_at") or "", reverse=True):
        mark = "▸" if about_watch(f.get("fact"), w) else " "
        emit("text", textwrap.fill(f.get("fact") or "", 108, initial_indent=f"   {mark} ", subsequent_indent="     "))
    recs = paged(cli, ["fact", "list", "--projects", world["projects"]["B"], "--page-size", "100"], echo=True)
    rows = sorted(((f.get("metadata") or {}) for f in recs), key=lambda m: m.get("run") or 0)
    emit("text", f"\n  B — the records project: {len(recs)} record(s)")
    emit("text", f"     {'run':<4} {'date':<11} {'tool':<16} error")
    for m in rows:
        mark = "▸" if m.get("tool") == w["tool"] and str(m.get("error", "")).startswith(w["error"]) else " "
        emit("text", f"   {mark} {m.get('run', ''):<4} {m.get('date', ''):<11} {m.get('tool', ''):<16} {m.get('error', '')}")
    note(f"▸ = about {w['label']}")
    out = dict(a=[dict(id=f["id"], text=f.get("fact"), about=about_watch(f.get("fact"), w)) for f in a_facts],
               b=[dict(run=m.get("run"), date=m.get("date"), tool=m.get("tool"), error=m.get("error"),
                       recovery=m.get("recovery"), outcome=m.get("outcome")) for m in rows])
    emit("memory", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: tonight's pre-call check

def precheck(cli: CLI, d: dict, world: dict) -> dict:
    w, rule, tn = d["watch"], d["rule"], d["tonight"]
    emit("text", f"\n  night {tn['run']} · {tn['date']} — before calling {tn['tool']} ({tn['api']}), "
                 f"the agent asks its memory: how often has this failed lately, and what worked?")
    emit("text", f"  rule: {rule['text']}")
    # A: everything on the runs project, read as generously as the text allows
    a_all = project_facts(cli, world["projects"]["A"], echo=True)
    a = a_reading(a_all, w)
    for k, f in enumerate(a["facts"]):
        tr = (cli.run("fact", "trace", f["id"], "--project", world["projects"]["A"], scoped=True, echo=k == 0) or {}).get("trace") or []
        f["history"] = [dict(event=x.get("event"), source=x.get("source_kind"), at=x.get("timestamp"),
                             src=x.get("source_entry_ids") or []) for x in tr]
    a["history_entries"] = sum(len(f["history"]) for f in a["facts"])
    # The most generous reading: every history entry of those facts, dated by the message that caused it.
    mt = message_times(cli, world["conv"])
    for f in a["facts"]:
        for x in f["history"]:
            said = sorted(mt[i] for i in x.pop("src") if mt.get(i))
            x["said"] = said[-1][:10] if said else None
    a["trace_dates"] = sorted({x["said"] for f in a["facts"] for x in f["history"] if x.get("said")})
    a["trace_count"] = len(a["trace_dates"])
    a["trace_escalate"] = a["trace_count"] >= rule["threshold"]
    a["escalate"] = a["count"] >= rule["threshold"]
    a["recovery"] = a["facts"][0]["text"] if a["facts"] else None
    emit("text", f"\n  A run log: {len(a['facts'])} fact(s) about it, dates written in them: {', '.join(a['dates']) or 'none'}")
    for f in a["facts"]:
        emit("text", textwrap.fill(f["text"] or "", 108, initial_indent="    · ", subsequent_indent="      "))
        emit("text", f"      history (`fact trace`): " + " → ".join(f"{x['event']}·{x['source']}" + (f" (msg {x['said'][5:]})" if x.get("said") else "")
                                                             for x in reversed(f["history"])))
    emit("text", f"    → {a['count']} failure(s) on record, {a['history_entries']} history entr{'y' if a['history_entries'] == 1 else 'ies'}: "
                 + ("escalate" if a["escalate"] else f"below {rule['threshold']} — retry with the remembered recovery"))
    emit("text", f"    → read through `fact trace` (each history entry dated by its source message): {a['trace_count']} night(s)"
                 + (f" ({', '.join(x[5:] for x in a['trace_dates'])})" if a["trace_dates"] else "")
                 + (" — escalate" if a["trace_escalate"] else f" — below {rule['threshold']}"))
    # B: the typed records, filtered on their metadata
    b = b_reading(b_records(cli, world, w, echo=True), d)
    b["escalate"] = b["count"] >= rule["threshold"]
    ok = [r for r in b["records"] if r["date"] in b["dates"]]
    b["recovery"] = ok[-1]["recovery"] if ok else None
    b["first_flag"] = (dict(run=ok[rule["threshold"] - 1]["run"], date=ok[rule["threshold"] - 1]["date"])
                       if len(ok) >= rule["threshold"] else None)
    emit("text", f"\n  B failure records: {b['count']} record(s) of {w['label']} in the last {rule['window_days']} days")
    for r in b["records"]:
        emit("text", f"    · run {r['run']} · {r['date']} · recovery: {r['recovery']}")
    emit("text", f"    → {b['count']} failures: " + ("escalate — open a systematic fix for the Ledgerline rate limit"
                                                        if b["escalate"] else "below the threshold")
                 + (f"; the rule first held on night {b['first_flag']['run']} ({b['first_flag']['date']})" if b["first_flag"] else ""))
    emit("text", f"    → last recovery that worked (run {ok[-1]['run']}): {b['recovery']}" if ok else "    → no recovery on record")
    out = dict(A=a, B=b)
    emit("precheck", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: score

def score(d: dict, tally: list[dict], pc: dict, exp: dict) -> dict:
    w, rule = d["watch"], d["rule"]
    a, b = pc["A"], pc["B"]
    run_of = {r["date"]: r["run"] for r in d["runs"]}
    flag = dict(A=next((t["run"] for t in tally if t["flag_a"]), None),
                T=run_of.get(a["trace_dates"][rule["threshold"] - 1]) if a["trace_count"] >= rule["threshold"] else None,
                B=next((t["run"] for t in tally if t["flag_b"]), None))
    has = names_worked  # noqa: E731
    md = lambda xs: ", ".join(x[5:] for x in xs) or "none"   # noqa: E731  2026-10-12 -> 10-12
    got = dict(A=dict(count=a["count"], dates=a["dates"], esc=a["escalate"], rec=a["recovery"]),
               T=dict(count=a["trace_count"], dates=a["trace_dates"], esc=a["trace_escalate"], rec=a["recovery"]),
               B=dict(count=b["count"], dates=b["dates"], esc=b["escalate"], rec=b["recovery"]))
    cols = ("A", "T", "B")
    rows = []
    for q, truth, val, ok in (
        ("failures on record", str(exp["count"]), lambda g, c: str(g["count"]), lambda g, c: g["count"] == exp["count"]),
        ("the nights they happened", md(exp["dates"]), lambda g, c: md(g["dates"]), lambda g, c: g["dates"] == exp["dates"]),
        (f"rule ({rule['threshold']}+ in {rule['window_days']} days) holds tonight", "yes",
         lambda g, c: "yes" if g["esc"] else "no", lambda g, c: g["esc"]),
        ("first night the rule held", f"night {exp['first_flag']['run']}",
         lambda g, c: f"night {flag[c]}" if flag[c] else "never", lambda g, c: flag[c] == exp["first_flag"]["run"]),
        ("recovery that worked last", exp["worked_with"],
         lambda g, c: exp["worked_with"] if has(g["rec"]) else "not named", lambda g, c: has(g["rec"])),
    ):
        rows.append(dict(q=q, truth=truth, **{c: val(got[c], c) for c in cols}, **{f"ok{c}": bool(ok(got[c], c)) for c in cols}))
    head = dict(A=d["arms"]["A"]["short"], T="A + fact trace", B=d["arms"]["B"]["short"])
    emit("text", f"\n    {w['label']:<34} {'truth':<28} " + " ".join(f"{head[c]:<28}" for c in cols))
    for r in rows:
        emit("text", f"    {r['q']:<34} {r['truth']:<28} " + " ".join(f"{('✓ ' if r['ok' + c] else '✗ ') + r[c]:<28}" for c in cols).rstrip())
    summary = {c: dict(right=sum(r["ok" + c] for r in rows), of=len(rows)) for c in cols}
    lost = exp["count"] - a["count"]
    note(f"A: {exp['count']} failures → {len(a['facts'])} fact(s) about it and {a['history_entries']} history entr"
         f"{'y' if a['history_entries'] == 1 else 'ies'}; "
         + (f"{lost} of the nights left no date anywhere in the facts' text" if lost > 0 else "every night's date is still in the text"))
    silent = [t["run"] for t in tally if t["watched"] and t["extractor"]["added"] == 0 and t["extractor"]["rewrote"] == 0]
    if silent:
        note(f"A: on night(s) {', '.join(map(str, silent))} the {w['label']} failure changed no fact at all — "
             "the extractor already knew the call can fail that way, so it wrote nothing, and `fact trace` has no entry for it")
    # The deterministic arm must do what the story says, or the run is misread.
    if b["count"] != exp["count"] or b["dates"] != exp["dates"] or flag["B"] != exp["first_flag"]["run"]:
        die(f"B disagrees with the story: {b['count']} records {b['dates']}, first flag {flag['B']}; expected {exp}")
    report = dict(at=now_iso(), prefix=PREFIX, expected=exp, rows=rows, summary=summary, tally=tally, precheck=pc,
                  flag_night=flag, silent_nights=silent)
    save("report", report)
    emit("score", "", rows=rows, summary=summary, flag_night=flag, silent_nights=silent, expected=exp, heads=head,
         a_facts=len(a["facts"]), a_history=a["history_entries"])
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    exp = check_story()
    banner(2, TOTAL, "Setup — a runs project (A) and a failure-records project (B)")
    world = setup(cli, d)
    banner(3, TOTAL, f"Six nights — {len(d['runs'])} run logs to A, {len(d['runs'])} failure records to B")
    tally = nights(cli, d, world, exp)
    banner(4, TOTAL, "What each memory holds")
    memory_view(cli, d, world)
    banner(5, TOTAL, f"Night {d['tonight']['run']}: before calling {d['tonight']['tool']}, check memory for earlier failures")
    pc = precheck(cli, d, world)
    banner(6, TOTAL, "Score — the count, the dates, the escalation, the recovery")
    return score(d, tally, pc, exp)


# --------------------------------------------------------------------------- read-only subcommand

def show_history(cli: CLI) -> None:
    """Every failure of the week against the two memories: is this night still findable?"""
    world = load("world")
    d = load_data()
    a_facts = project_facts(cli, world["projects"]["A"], echo=True)
    recs = paged(cli, ["fact", "list", "--projects", world["projects"]["B"], "--page-size", "100"], echo=True)
    by_run = {(f.get("metadata") or {}).get("run"): f for f in recs}
    lost = 0
    emit("text", f"\n  {'night':<6} {'date':<11} {'failure':<44} {'A: date in a fact about it':<28} B: record")
    for r in d["runs"]:
        w = dict(tool=r["tool"], api=r["api"], error=r["error"].split(" (")[0])
        about = [f for f in a_facts if about_watch(f.get("fact"), w) or (r["tool"] in (f.get("fact") or "") and r["error"][:9] in (f.get("fact") or ""))]
        dated = any(r["date"] in (f.get("fact") or "") for f in about)
        a_cell = "yes" if dated else ("no — other dates only" if about else "no fact about it")
        lost += 0 if dated else 1
        b = by_run.get(r["run"])
        emit("text", f"  {r['run']:<6} {r['date']:<11} {(r['tool'] + ' → ' + r['error'])[:44]:<44} {a_cell:<28} "
                     + (f"yes ({b['id'][-8:]})" if b else "missing"))
    note(f"A: {lost} of {len(d['runs'])} nights cannot be found by date in the run-log memory; B keeps one record per night"
         if lost else f"A: this time every night's date is still written in a fact about it; B keeps one record per night")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = demo_convs(cli)
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=not quiet)
    nfacts, nproj = 0, 0
    a = find_actor(cli, d["agent"]["key"], echo=not quiet)
    if a:
        nfacts += delete_actor(cli, a["id"])
    for x in ARMS:
        p = find_project(cli, arm_key(x), echo=not quiet and x == "A")
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
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet and x == "A")
        nproj += 1
    if not quiet:
        note(f"deleted {len(convs)} conversation(s), {nfacts} facts, {1 if a else 0} actor (facts first: a deleted actor's "
             f"facts could never be removed) and {nproj} projects" if (convs or a or nproj) else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "world", "nights"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(parents=True, exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "history", "cleanup"],
                    help="run = full demo (default); history = every failure of the week against both memories; "
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
    if args.command == "history":
        banner(2, 2, "History — every failure of the week, against both memories")
        show_history(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py history`; `python3 demo.py cleanup` removes the demo data.")


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

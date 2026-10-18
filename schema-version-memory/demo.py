#!/usr/bin/env python3
"""schema-version-memory — a memory schema change: who can still read the records, and what the audit can still see.

Tindlewood Analytics (fictional) runs an account assistant on project memory. Since spring, its v1 app has stored
one record per customer account: a text ("Account Corvane Dental: plan Business, 40 seats.") and metadata that code
reads ({account, plan, seats}). Today v2 ships: `plan` is renamed `tier` and `billing` is added. The same v1 history
sits in three projects, and v2 ships three ways:

  A  ship v2 code, no migration       — the 8 v1 records stay as they are; new accounts get the v2 shape
  B  rename in place                  — `fact update --metadata` gives every v1 record the v2 shape; text untouched
  C  expand, then record              — one `fact update --text … --metadata …` per record: v2 keys next to the v1
                                        key, v2 wording with the v1 phrase kept, and `schema_history` with the v1
                                        fields; new accounts are written with both keys

Then four readers answer the same question — which accounts get priority support — two in code (v2 reads `tier`,
a v1 job still in the rolling deploy reads `plan`) and two as text searches (`fact list --query`). And the audit
asks of every migrated record: when did it change shape, and what were its v1 fields?

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-svm"  # memorylake-usecases / schema-version-memory


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

TOTAL = 7             # steps in a full run
ARMS = ("A", "B", "C")
READERS = ("R1", "R2", "R3", "R4")
GAP = 3.0             # seconds before the migration, so its writes have their own seconds on the server clock


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, arm: str, echo: bool = False):
    return cli.try_run("proj", "get", cid(arm.lower()), "--by-custom-id", scoped=True, echo=echo)


def paged(cli: CLI, args: list[str], echo: bool = False) -> list[dict]:
    out, token = [], None
    while True:
        a = list(args) + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or [] if isinstance(page, dict) else page
        token = page.get("continuation_token") if isinstance(page, dict) else None
        if not token:
            return out


def project_facts(cli: CLI, project: str, echo: bool = False, query: str | None = None) -> list[dict]:
    q = ["--query", query] if query else []
    return paged(cli, ["fact", "list", "--projects", project, *q, "--page-size", "200"], echo)


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


def trace_of(cli: CLI, fact_id: str, project: str, echo: bool = False) -> tuple[dict, list[dict]]:
    """The fact and its history, oldest first."""
    tr = cli.run("fact", "trace", fact_id, "--project", project, scoped=True, echo=echo) or {}
    return tr.get("fact") or {}, sorted(tr.get("trace") or [], key=lambda e: e.get("timestamp") or "")


def pmap(fn, items, workers: int = 4) -> list:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, items))


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def hhmmss(iso: str) -> str:
    return (iso or "")[11:19]


def parse_ts(iso: str) -> datetime:
    """Server timestamps carry up to 6 fractional digits; Python 3.9's fromisoformat wants exactly 3 or 6."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?", iso or "")
    if not m:
        die(f"unexpected timestamp {iso!r}")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    return datetime.strptime(f"{m.group(1)}.{frac}", "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def cap(word: str) -> str:
    return word[:1].upper() + word[1:]


# The three record shapes. The text is what a person (and `fact list --query`) reads; the metadata is what code reads.
def v1_text(name: str, plan: str, seats: int) -> str:
    return f"Account {name}: plan {cap(plan)}, {seats} seats."


def v1_meta(key: str, plan: str, seats: int) -> dict:
    return {"account": key, "plan": plan, "seats": seats}


def v2_text(name: str, tier: str, seats: int, billing: str) -> str:
    return f"Account {name}: tier {cap(tier)}, {seats} seats, billing {billing}."


def v2_meta(key: str, tier: str, seats: int, billing: str) -> dict:
    return {"schema": 2, "account": key, "tier": tier, "seats": seats, "billing": billing}


def expand_text(name: str, tier: str, seats: int, billing: str) -> str:
    """v2 wording, with the v1 phrase kept so v1 readers of the text still find it."""
    return f"Account {name}: tier {cap(tier)} (v1: plan {cap(tier)}), {seats} seats, billing {billing}."


def expand_meta(key: str, tier: str, seats: int, billing: str, history: list | None = None) -> dict:
    m = {"schema": 2, "account": key, "tier": tier, "plan": tier, "seats": seats, "billing": billing}
    if history:
        m["schema_history"] = history
    return m


def names(d: dict) -> dict:
    return {a["key"]: a["name"] for a in d["v1_accounts"] + d["v2_accounts"]}


def final_tier(d: dict) -> dict:
    """account → its tier today, from the story alone."""
    t = {a["key"]: a["plan"] for a in d["v1_accounts"]}
    for c in d["v1_changes"]:
        t[c["key"]] = c["plan"]
    t.update({a["key"]: a["tier"] for a in d["v2_accounts"]})
    return t


def truth(d: dict) -> set:
    return {k for k, v in final_tier(d).items() if v in d["priority"]}


def v1_now(d: dict) -> dict:
    """The v1 accounts as they stand just before v2: key → (plan, seats)."""
    cur = {a["key"]: (a["plan"], a["seats"]) for a in d["v1_accounts"]}
    for c in d["v1_changes"]:
        cur[c["key"]] = (c["plan"], c["seats"])
    return cur


def check_story() -> None:
    """The rules on the story itself, before any call is made: a wrong rule would misread every run."""
    d = load_data()
    t = truth(d)
    v1 = {a["key"] for a in d["v1_accounts"]}
    if len(d["v1_accounts"]) != 8 or len(d["v2_accounts"]) != 4 or len(t) != 6 or len(t & v1) != 4:
        die(f"story self-check failed: priority {sorted(t)}")
    if any(c["key"] not in v1 for c in d["v1_changes"]):
        die("story self-check failed: a v1 change names an unknown account")
    # The text readers match substrings: no account name may contain a tier word.
    for n in names(d).values():
        if re.search(r"(?i)\b(plan|tier|starter|pro|business|enterprise)\b", n):
            die(f"story self-check failed: account name {n!r} contains a reader's word")
    # The expand shape must still answer both text readers.
    s = expand_text("X", "business", 1, "annual")
    if "tier Business" not in s or "plan Business" not in s:
        die("story self-check failed: the expand text lost a reader's phrase")


def save(name: str, obj) -> Path:
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def load(name: str):
    p = OUT / f"{name}.json"
    if not p.exists():
        die(f"no {p.name} yet; run `python3 demo.py` first")
    return json.loads(p.read_text(encoding="utf-8"))


def account_of(f: dict) -> str | None:
    """The account a record is about, read from its text (a text reader has nothing else)."""
    m = re.match(r"Account (.+?):", f.get("fact") or "")
    return m.group(1) if m else None


# --------------------------------------------------------------------------- step 2: v1 writes the accounts

def leftovers(cli: CLI) -> bool:
    return any(find_project(cli, arm) for arm in ARMS)


def write_record(cli: CLI, project: str, text: str, meta: dict, echo: bool = False) -> str:
    """How every version of the app writes an account: the text, then its metadata."""
    r = cli.run("fact", "add", text, "--project", project, scoped=True, echo=echo) or {}
    fid = ((r.get("facts") or [{}])[0]).get("id")
    if not fid:
        die(f"`fact add` returned no id for {text!r}")
    cli.run("fact", "update", fid, "--metadata", json.dumps(meta), "--project", project, scoped=True, echo=echo)
    return fid


def setup(cli: CLI, d: dict) -> tuple[dict, dict]:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    for name in ("world", "truth", "report"):
        if (OUT / f"{name}.json").exists():
            (OUT / f"{name}.json").unlink()
    world = {}
    for arm in ARMS:
        world[arm] = cli.run("proj", "create", "--name", f"{d['project_name']} ({arm})", "--custom-id", cid(arm.lower()),
                             scoped=True, echo=True)["id"]
    emit("world", "", **world)
    note("the same v1 history goes into all three projects; they differ only in how v2 ships (step 4)")
    ids: dict = {arm: {} for arm in ARMS}
    jobs = [(arm, a) for arm in ARMS for a in d["v1_accounts"]]

    def one(job):
        arm, a = job
        first = job == jobs[0]
        ids[arm][a["key"]] = write_record(cli, world[arm], v1_text(a["name"], a["plan"], a["seats"]),
                                          v1_meta(a["key"], a["plan"], a["seats"]), echo=first)
    one(jobs[0])
    pmap(one, jobs[1:])
    emit("text", "\n    account               v1 text                                                v1 metadata")
    for a in d["v1_accounts"]:
        emit("text", f"    {a['name']:<21} {v1_text(a['name'], a['plan'], a['seats']):<54} "
                     f"{json.dumps(v1_meta(a['key'], a['plan'], a['seats']))}")
    note(f"{len(d['v1_accounts'])} accounts × 3 projects, written the v1 way: text + metadata {{account, plan, seats}}")
    emit("setup", "", world=world, accounts=[dict(key=a["key"], name=a["name"], text=v1_text(a["name"], a["plan"], a["seats"]),
                                                  meta=v1_meta(a["key"], a["plan"], a["seats"])) for a in d["v1_accounts"]])
    save("world", dict(world=world, ids=ids))
    return world, ids


# --------------------------------------------------------------------------- step 3: v1-era upgrades

def v1_changes(cli: CLI, d: dict, world: dict, ids: dict) -> None:
    nm = names(d)
    cur = {a["key"]: a for a in d["v1_accounts"]}
    jobs = [(arm, c) for c in d["v1_changes"] for arm in ARMS]

    def one(job):
        arm, c = job
        cli.run("fact", "update", ids[arm][c["key"]], "--text", v1_text(nm[c["key"]], c["plan"], c["seats"]),
                "--metadata", json.dumps(v1_meta(c["key"], c["plan"], c["seats"])), "--project", world[arm],
                scoped=True, echo=job == jobs[0])
    one(jobs[0])
    pmap(one, jobs[1:])
    for c in d["v1_changes"]:
        was = cur[c["key"]]
        emit("progress", f"    {c['when']:<8} {nm[c['key']]:<20} {cap(was['plan'])}, {was['seats']} seats → "
                         f"{cap(c['plan'])}, {c['seats']} seats  ({c['why']})",
             phase="change", key=c["key"], name=nm[c["key"]], was=f"{cap(was['plan'])}, {was['seats']} seats",
             value=f"{cap(c['plan'])}, {c['seats']} seats", when=c["when"], why=c["why"])
    k = d["v1_changes"][0]["key"]
    _, entries = trace_of(cli, ids["A"][k], world["A"], echo=True)
    emit("text", f"\n  fact trace on {nm[k]} — the v1 app's text changes, on the server's clock:")
    rows = []
    for e in entries:
        emit("text", f"    {hhmmss(e.get('timestamp'))}  {e.get('event'):<7} {e.get('new_fact')}")
        rows.append(dict(at=e.get("timestamp"), event=e.get("event"), text=e.get("new_fact")))
    note("the trace keeps every text version; it has no entry for the metadata writes (they were metadata-only)")
    emit("changes", "", key=k, name=nm[k], trace=rows)


# --------------------------------------------------------------------------- step 4: v2 ships — three ways

def migrate(cli: CLI, d: dict, world: dict, ids: dict) -> dict:
    nm = names(d)
    now1 = v1_now(d)
    billing = {a["key"]: a["billing"] for a in d["v1_accounts"]}
    # Ground truth for the audit: every v1 record exactly as it stands before the release.
    before = {}
    for arm in ("B", "C"):
        before[arm] = {f["metadata"].get("account"): dict(id=f["id"], text=f["fact"], meta=f.get("metadata") or {},
                                                          updated_at=f.get("updated_at"))
                       for f in project_facts(cli, world[arm], echo=arm == "B")}
    save("truth", dict(before=before))
    note(f"v1 records read and saved to out/truth.json (the audit's ground truth); waiting {GAP:.0f}s so the release "
         "has its own seconds on the server clock")
    time.sleep(GAP)
    started = datetime.now(timezone.utc).isoformat()
    emit("text", f"\n  v2 ships: `plan` is renamed `tier`, `billing` is added. {len(now1)} v1 records exist in each project.")
    emit("progress", "    A  no migration: the 8 v1 records are left as they are", arm="A", calls=0)
    keys = [a["key"] for a in d["v1_accounts"]]

    def mig_b(k):
        plan, seats = now1[k]
        cli.run("fact", "update", ids["B"][k], "--metadata", json.dumps(v2_meta(k, plan, seats, billing[k])),
                "--project", world["B"], scoped=True, echo=k == keys[0])

    def mig_c(k):
        plan, seats = now1[k]
        hist = [dict(schema=1, fields=dict(plan=plan, seats=seats), until=started, by="v2 release migration")]
        cli.run("fact", "update", ids["C"][k], "--text", expand_text(nm[k], plan, seats, billing[k]),
                "--metadata", json.dumps(expand_meta(k, plan, seats, billing[k], hist)),
                "--project", world["C"], scoped=True, echo=k == keys[0])
    mig_b(keys[0])
    pmap(mig_b, keys[1:])
    emit("progress", f"    B  {len(keys)} × fact update --metadata (v2 shape; text untouched)", arm="B", calls=len(keys))
    mig_c(keys[0])
    pmap(mig_c, keys[1:])
    emit("progress", f"    C  {len(keys)} × fact update --text --metadata (v2 + v1 keys, v1 phrase kept, schema_history)",
         arm="C", calls=len(keys))
    emit("migrated", "", started=started, calls=dict(A=0, B=len(keys), C=len(keys)))
    return dict(started=started)


# --------------------------------------------------------------------------- step 5: v2 signs new accounts

def v2_signups(cli: CLI, d: dict, world: dict, ids: dict) -> None:
    jobs = [(arm, a) for arm in ARMS for a in d["v2_accounts"]]

    def one(job):
        arm, a = job
        if arm == "C":
            text, meta = expand_text(a["name"], a["tier"], a["seats"], a["billing"]), \
                expand_meta(a["key"], a["tier"], a["seats"], a["billing"])
        else:
            text, meta = v2_text(a["name"], a["tier"], a["seats"], a["billing"]), \
                v2_meta(a["key"], a["tier"], a["seats"], a["billing"])
        ids[arm][a["key"]] = write_record(cli, world[arm], text, meta, echo=job in (jobs[0], jobs[-1]))
    one(jobs[0])
    one(jobs[-1])
    pmap(one, jobs[1:-1])
    for a in d["v2_accounts"]:
        emit("progress", f"    {a['name']:<20} {cap(a['tier'])}, {a['seats']} seats, billing {a['billing']}",
             phase="signup", key=a["key"], name=a["name"], tier=cap(a["tier"]), seats=a["seats"], billing=a["billing"])
    note("A and B write the v2 shape only; C writes both keys (and the v1 phrase) until every reader is on v2")
    save("world", dict(load("world"), ids=ids))
    emit("signups", "", accounts=[dict(a, tier=cap(a["tier"])) for a in d["v2_accounts"]])


# --------------------------------------------------------------------------- step 6: four readers

def read_meta(facts: list[dict], field: str, pri: list[str]) -> dict:
    """A reader in code: the account is priority when metadata[field] is a priority tier; no field = unreadable."""
    found, unreadable = set(), []
    for f in facts:
        m = f.get("metadata") or {}
        v = m.get(field)
        if v is None:
            unreadable.append(m.get("account") or account_of(f))
        elif v in pri:
            found.add(m.get("account"))
    return dict(found=found, unreadable=unreadable)


def read_text(cli: CLI, project: str, word: str, pri: list[str], keyof: dict, echo: bool) -> dict:
    found = set()
    for i, p in enumerate(pri):
        for f in project_facts(cli, project, echo=echo and i == 0, query=f"{word} {cap(p)}"):
            k = keyof.get(account_of(f))
            if k:
                found.add(k)
    return dict(found=found, unreadable=[])


def readers(cli: CLI, d: dict, world: dict, echo: bool = True) -> dict:
    pri = d["priority"]
    keyof = {v: k for k, v in names(d).items()}
    t = truth(d)
    res: dict = {}
    for arm in ARMS:
        facts = project_facts(cli, world[arm], echo=echo and arm == "A")
        res[arm] = {
            "R1": read_meta(facts, "tier", pri),
            "R2": read_meta(facts, "plan", pri),
            "R3": read_text(cli, world[arm], "tier", pri, keyof, echo=echo and arm == "A"),
            "R4": read_text(cli, world[arm], "plan", pri, keyof, echo=echo and arm == "A"),
        }
        res[arm]["records"] = len(facts)
    rows = {arm: {r: dict(found=len(res[arm][r]["found"] & t), of=len(t), wrong=sorted(res[arm][r]["found"] - t),
                          missed=sorted(t - res[arm][r]["found"]), unreadable=len(res[arm][r]["unreadable"]))
                  for r in READERS} for arm in ARMS}
    return dict(rows=rows, records={a: res[a]["records"] for a in ARMS})


def show_readers(d: dict, r: dict) -> None:
    nm = names(d)
    t = truth(d)
    emit("text", f"\n  Which accounts get priority support (Business or Enterprise)? The answer is {len(t)} accounts: "
                 + ", ".join(nm[k] for k in sorted(t)) + ".\n")
    emit("text", "    reader                             " + "".join(f"{d['arms'][a]['short']:<22}" for a in ARMS))
    for rk in READERS:
        rd = d["readers"][rk]
        cells = []
        for arm in ARMS:
            x = r["rows"][arm][rk]
            ok = x["found"] == x["of"] and not x["wrong"]
            cell = f"{x['found']}/{x['of']}" + (f", {x['unreadable']} unreadable" if x["unreadable"] else "") + (" ✓" if ok else " ✗")
            cells.append(f"{cell:<22}")
        emit("text", f"    {rk} {rd['name'] + ' (' + rd['code'] + ')':<31} " + "".join(cells).rstrip())
    for arm in ARMS:
        for rk in READERS:
            x = r["rows"][arm][rk]
            if x["missed"]:
                emit("text", f"      {arm} {rk} missed: " + ", ".join(nm[k] for k in x["missed"]))
    emit("readers", "", **r, truth=sorted(t), names=nm)


# --------------------------------------------------------------------------- step 7: the audit

def audit(cli: CLI, d: dict, world: dict, echo: bool = True) -> dict:
    """For each migrated record: does the trace show the schema change, and can the v1 fields be read back?"""
    nm = names(d)
    before = load("truth")["before"]
    out: dict = {}
    rows = {}
    for arm in ("B", "C"):
        rows[arm] = dict(trace=0, fields=0, moved=0, of=0)
        out[arm] = {}
        for i, (k, b) in enumerate(sorted(before[arm].items())):
            fact, entries = trace_of(cli, b["id"], world[arm], echo=echo and i == 0)
            meta = fact.get("metadata") or {}
            upd = fact.get("updated_at")
            newest = entries[-1] if entries else {}
            # The migration is in the trace when an entry turned the v1 text into something else, at the record's last change.
            mig = [e for e in entries if e.get("event") == "UPDATE" and e.get("old_fact") == b["text"]
                   and upd and abs((parse_ts(e["timestamp"]) - parse_ts(upd)).total_seconds()) < 2]
            hist = meta.get("schema_history") or []
            v1_fields = {x: b["meta"].get(x) for x in ("plan", "seats")}
            got = (hist[-1].get("fields") if hist else None)
            ok_fields = got == v1_fields
            moved = bool(upd and b.get("updated_at") and upd != b["updated_at"])
            rows[arm]["of"] += 1
            rows[arm]["trace"] += bool(mig)
            rows[arm]["fields"] += ok_fields
            rows[arm]["moved"] += moved
            out[arm][k] = dict(name=nm[k], updated_at=upd, newest=dict(event=newest.get("event"), at=newest.get("timestamp")),
                               migration_at=mig[0]["timestamp"] if mig else None, entries=len(entries),
                               v1=v1_fields, recovered=got, ok_trace=bool(mig), ok_fields=ok_fields, meta=meta)
    return dict(rows=rows, detail=out)


def show_audit(d: dict, a: dict) -> None:
    for arm in ("B", "C"):
        emit("text", f"\n  {d['arms'][arm]['title']}")
        emit("text", "    account               last changed   newest trace entry      migration in trace   v1 fields read back")
        for k, x in a["detail"][arm].items():
            nt = f"{x['newest']['event']} {hhmmss(x['newest']['at'])}"
            mt = (hhmmss(x["migration_at"]) + " ✓") if x["ok_trace"] else "none ✗"
            rf = (f"plan {x['recovered']['plan']}, {x['recovered']['seats']} seats ✓" if x["ok_fields"]
                  else "— (`plan` is gone) ✗")
            emit("text", f"    {x['name']:<21} {hhmmss(x['updated_at']):<14} {nt:<23} {mt:<20} {rf}")
        emit("audit", "", arm=arm, rows=a["rows"][arm], detail=a["detail"][arm])
    emit("text", "\n    A no migration: nothing to audit — the 8 v1 records were never changed by the release")


def score(d: dict, r: dict, a: dict, started: str) -> dict:
    emit("text", "\n  Score\n")
    emit("text", "    release              R1 tier   R2 plan   R3 \"tier …\"   R4 \"plan …\"   migration in trace   v1 fields kept")
    for arm in ARMS:
        x = r["rows"][arm]
        au = a["rows"].get(arm)
        tail = (f"{au['trace']}/{au['of']:<18} {au['fields']}/{au['of']}" if au else f"{'n/a':<20} n/a")
        emit("text", f"    {d['arms'][arm]['short']:<20} " + "".join(f"{x[k]['found']}/{x[k]['of']:<8}" for k in READERS[:2])
             + f"{x['R3']['found']}/{x['R3']['of']:<12} {x['R4']['found']}/{x['R4']['of']:<12} {tail}")
    b = a["rows"]["B"]
    note(f"B: the release changed {b['moved']}/{b['of']} records (their updated_at moved) and `fact trace` shows "
         f"{b['trace']} of those changes; the v1 `plan` field can be read back for {b['fields']}/{b['of']}")
    note("C: every reader finds every priority account, and each migrated record carries the change in its trace and "
         "its v1 fields in metadata")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, started=started, readers=r["rows"],
                  records=r["records"], audit=a["rows"], audit_detail={k: {kk: {x: y for x, y in vv.items() if x != "meta"}
                                                                          for kk, vv in v.items()} for k, v in a["detail"].items()},
                  truth=sorted(truth(d)))
    save("report", report)
    emit("score", "", **report)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_story()
    banner(2, TOTAL, "Spring — the v1 app writes 8 accounts")
    world, ids = setup(cli, d)
    banner(3, TOTAL, "June → August — two upgrades, written by v1")
    v1_changes(cli, d, world, ids)
    banner(4, TOTAL, "Today — v2 ships: `plan` becomes `tier`, three ways")
    m = migrate(cli, d, world, ids)
    banner(5, TOTAL, "After the release — v2 signs 4 new accounts")
    v2_signups(cli, d, world, ids)
    banner(6, TOTAL, "Four readers, one question — who gets priority support?")
    r = readers(cli, d, world)
    show_readers(d, r)
    banner(7, TOTAL, "The audit — when did each record change shape, and from what?")
    a = audit(cli, d, world)
    show_audit(d, a)
    return score(d, r, a, m["started"])


# --------------------------------------------------------------------------- read-only subcommands

def load_world(cli: CLI) -> dict:
    world = {}
    for arm in ARMS:
        p = find_project(cli, arm)
        if not p:
            die("the demo projects do not exist yet; run `python3 demo.py` first")
        world[arm] = p["id"]
    return world


def show_record(cli: CLI, key: str | None = None) -> None:
    d = load_data()
    nm = names(d)
    key = key or "corvane"
    if key not in nm:
        die(f"unknown account {key!r}; one of: {', '.join(nm)}")
    world = load_world(cli)
    for arm in ARMS:
        f = next((x for x in project_facts(cli, world[arm]) if (x.get("metadata") or {}).get("account") == key), None)
        emit("text", f"\n  {d['arms'][arm]['title']}")
        if not f:
            emit("text", "    (no record with metadata.account = " + key + ")")
            continue
        _, entries = trace_of(cli, f["id"], world[arm], echo=True)
        emit("text", f"    text:        {f['fact']}")
        emit("text", f"    metadata:    {json.dumps(f.get('metadata') or {})}")
        emit("text", f"    created_at:  {hhmmss(f.get('created_at'))}   updated_at: {hhmmss(f.get('updated_at'))}")
        emit("text", "    fact trace:")
        for e in entries:
            emit("text", f"      {hhmmss(e.get('timestamp'))}  {e.get('event'):<7} {e.get('new_fact')}")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    nfacts, nproj = 0, 0
    for arm in ARMS:
        p = find_project(cli, arm, echo=not quiet and arm == "A")
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
        cli.run("proj", "delete", p["id"], scoped=True, echo=not quiet)
        nproj += 1
    if not quiet:
        note(f"deleted {nfacts} facts and {nproj} projects" if nproj else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "truth", "world"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "record", "cleanup"],
                    help="run = full demo (default); record [account] = one account's text, metadata and trace in "
                         "all three projects; cleanup = delete everything")
    ap.add_argument("account", nargs="?", help="for `record`: an account key such as corvane, mistlow or wrenfield")
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
    if args.command == "record":
        banner(2, 2, "Record — one account in all three projects")
        show_record(cli, args.account)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py record mistlow`; `python3 demo.py cleanup` removes the demo data.")


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

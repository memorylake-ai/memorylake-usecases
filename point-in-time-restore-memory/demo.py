#!/usr/bin/env python3
"""point-in-time-restore-memory — a bad agent run rewrites memory; three ways to get the morning back.

Quellbrook (fictional) runs a support agent on project memory. At 02:00 a nightly job backs memory up with
`fact list`. During the day the team changes three facts, adds two, and forgets a customer's phone number on an
erasure request. At 14:52 an agent run follows instructions planted in a customer email and rewrites its own
memory: three facts changed, two forgotten, one added. At 15:20 the same memory (three identical copies) is
restored three ways:

  A  restore last night's backup: forget every live fact, `fact add` the export
  B  restore to 14:51 from `fact trace`, for every fact id in the backup or in `fact list`
  C  the same, for every fact id in the agent's own write journal

Then: what each copy says, what `fact trace` shows for the erased fact, and a score.

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

PREFIX = "mlu-pit"  # memorylake-usecases / point-in-time-restore-memory


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
        die("this memorylake CLI has no `fact trace` for forgotten facts; it needs v20261015 or later.\n"
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
MARGIN = 1.0          # seconds added to the last good write's trace timestamp to get the restore point


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, arm: str, echo: bool = False):
    return cli.try_run("proj", "get", cid("memory", arm.lower()), "--by-custom-id", scoped=True, echo=echo)


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


def hhmmss(iso: str) -> str:
    return (iso or "")[11:19]


def parse_ts(iso: str) -> datetime:
    """Server timestamps carry up to 6 fractional digits; Python 3.9's fromisoformat wants exactly 3 or 6."""
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?", iso or "")
    if not m:
        die(f"unexpected timestamp {iso!r}")
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    return datetime.strptime(f"{m.group(1)}.{frac}", "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def labels(d: dict) -> dict:
    m = {f["key"]: f["label"] for f in d["facts"]}
    for op in [*d["day"], *d["incident"]["ops"]]:
        if op["op"] == "add":
            m[op["key"]] = op["label"]
    return m


def known(d: dict) -> dict:
    """Every text the demo ever writes → (key, value, when). Writes are verbatim, so an exact match identifies a fact."""
    m = {f["text"]: (f["key"], f["value"], "backup") for f in d["facts"]}
    for op in d["day"]:
        if "text" in op:
            m[op["text"]] = (op["key"], op["value"], op["at"])
    for op in d["incident"]["ops"]:
        if "text" in op:
            m[op["text"]] = (op["key"], op["value"], "incident")
    return m


def expected(d: dict) -> dict:
    """What memory said just before the incident: the backup plus the day's changes. Worked out from the story alone."""
    t = {f["key"]: f["value"] for f in d["facts"]}
    for op in d["day"]:
        if op["op"] == "forget":
            t.pop(op["key"], None)
        else:
            t[op["key"]] = op["value"]
    return t


def erased(d: dict) -> list[str]:
    return [op["key"] for op in d["day"] if op["op"] == "forget"]


def row_keys(d: dict) -> list[str]:
    """Rows for the comparison tables: every key the story ever has, in story order."""
    keys = [f["key"] for f in d["facts"]]
    for op in [*d["day"], *d["incident"]["ops"]]:
        if op["key"] not in keys:
            keys.append(op["key"])
    return keys


def check_story() -> None:
    """The rules on the story itself, before any call is made: a wrong rule would misread every run."""
    d = load_data()
    texts = [f["text"] for f in d["facts"]] + [o["text"] for o in [*d["day"], *d["incident"]["ops"]] if "text" in o]
    if len(set(texts)) != len(texts):
        die("story self-check failed: two writes share a text, so a fact could not be told apart")
    t = expected(d)
    if len(t) != 9 or "phone" in t or t["refund"] != "$350" or t["sms"] != "Tallow":
        die(f"story self-check failed: expected state is {t}")


def says(values: list[str]) -> str:
    return " + ".join(values) if values else "(nothing)"


# --------------------------------------------------------------------------- the journal

def journal_path() -> Path:
    return OUT / "journal.json"


def save_journal(j: dict) -> None:
    OUT.mkdir(exist_ok=True)
    tmp = journal_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(j, indent=2), encoding="utf-8")
    os.replace(tmp, journal_path())


def load_journal() -> dict:
    if not journal_path().exists():
        die("no journal yet; run `python3 demo.py` first")
    return json.loads(journal_path().read_text(encoding="utf-8"))


def log(j: dict, arm: str, op: str, key: str, fact_id: str, when: str, text: str = "") -> None:
    """The agent's own write journal: one line per memory write, with the fact id the write touched or created."""
    j["writes"][arm].append(dict(op=op, key=key, id=fact_id, at=when, text=text))


# --------------------------------------------------------------------------- step 2: setup and the nightly backup

def leftovers(cli: CLI) -> bool:
    return any(find_project(cli, arm) for arm in ARMS)


def load_world(cli: CLI) -> dict:
    world = {}
    for arm in ARMS:
        p = find_project(cli, arm)
        if not p:
            die("the demo memory does not exist yet; run `python3 demo.py` first")
        world[arm] = p["id"]
    return world


def setup(cli: CLI, d: dict) -> tuple[dict, dict, dict]:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    world: dict = {}
    ids: dict = {arm: {} for arm in ARMS}
    j = dict(writes={arm: [] for arm in ARMS}, restore_point=None)
    by_text = {f["text"]: f["key"] for f in d["facts"]}
    for arm in ARMS:
        # Same project name for all three: only the custom_id says which copy it is.
        world[arm] = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("memory", arm.lower()),
                             scoped=True, echo=True)["id"]
        r = cli.run("fact", "add", *[f["text"] for f in d["facts"]], "--project", world[arm], scoped=True, echo=True) or {}
        for f in r.get("facts") or []:
            k = by_text.get(f.get("fact"))
            if k:
                ids[arm][k] = f["id"]
                log(j, arm, "add", k, f["id"], "before 02:00", f["fact"])
        missing = [f["key"] for f in d["facts"] if f["key"] not in ids[arm]]
        if missing:
            die(f"`fact add` did not return ids for {missing}; run `python3 demo.py cleanup` and try again")
    emit("world", "", **world)
    save_journal(j)
    emit("text", f"\n  {d['backup']['at']} — the {d['backup']['what']}; one file per copy (out/backup-<copy>.json):")
    for arm in ARMS:
        facts = project_facts(cli, world[arm], echo=True)
        backup = [dict(id=f["id"], text=f["fact"], updated_at=f.get("updated_at")) for f in facts]
        (OUT / f"backup-{arm.lower()}.json").write_text(json.dumps(backup, indent=2), encoding="utf-8")
    emit("text", "\n    fact                  says                  fact id (copy C)")
    lab = labels(d)
    for f in d["facts"]:
        emit("text", f"    {lab[f['key']]:<21} {f['value']:<21} {ids['C'][f['key']]}")
    note(f"backed up {len(d['facts'])} facts per copy, ids included")
    emit("backup", "", facts=[dict(key=f["key"], label=f["label"], value=f["value"], pii=bool(f.get("pii")),
                                   id={a: ids[a][f["key"]] for a in ARMS}) for f in d["facts"]])
    return world, ids, j


# --------------------------------------------------------------------------- step 3: the day

def apply(cli: CLI, d: dict, world: dict, ids: dict, j: dict, op: dict, when: str, meta: dict | None = None) -> None:
    """One write, made in all three copies, and journaled."""
    for arm in ARMS:
        scope = ["--project", world[arm]]
        if op["op"] == "update":
            extra = ["--metadata", json.dumps(meta)] if meta else []
            cli.run("fact", "update", ids[arm][op["key"]], "--text", op["text"], *extra, *scope, scoped=True, echo=True)
            log(j, arm, "update", op["key"], ids[arm][op["key"]], when, op["text"])
        elif op["op"] == "forget":
            cli.run("fact", "delete", ids[arm][op["key"]], *scope, scoped=True, echo=True)
            log(j, arm, "forget", op["key"], ids[arm][op["key"]], when)
        else:
            r = cli.run("fact", "add", op["text"], *scope, scoped=True, echo=True) or {}
            fid = ((r.get("facts") or [{}])[0]).get("id")
            if not fid:
                die(f"`fact add` returned no id for {op['text']!r}")
            ids[arm][op["key"]] = fid
            log(j, arm, "add", op["key"], fid, when, op["text"])
    save_journal(j)


def the_day(cli: CLI, d: dict, world: dict, ids: dict, j: dict) -> str:
    lab = labels(d)
    base = {f["key"]: f["value"] for f in d["facts"]}
    for op in d["day"]:
        meta = {"decided_at": f"{d['today']}T{op['at']}:00Z", "why": op["why"]}
        apply(cli, d, world, ids, j, op, op["at"], meta)
        if op["op"] == "update":
            line = f"{lab[op['key']]}: {base[op['key']]} → {op['value']}"
        elif op["op"] == "forget":
            line = f"{lab[op['key']]}: forgotten"
        else:
            line = f"{lab[op['key']]}: new — {op['value']}"
        emit("progress", f"    {op['at']}  {line:<44} ({op['why']})", phase="day", op=op["op"], key=op["key"],
             at=op["at"], value=op.get("value"), why=op["why"], was=base.get(op["key"]))
    # The restore point comes from the server's own clock: the newest trace entry the day wrote, plus a margin.
    newest = None
    touched = [op["key"] for op in d["day"]]
    for arm in ARMS:
        for k in touched:
            tr = cli.run("fact", "trace", ids[arm][k], "--project", world[arm], scoped=True,
                         echo=(arm == "A" and k == touched[0])) or {}
            for e in tr.get("trace") or []:
                ts = parse_ts(e.get("timestamp"))
                newest = ts if newest is None or ts > newest else newest
    point = iso(newest + timedelta(seconds=MARGIN))
    j["restore_point"] = point
    j["restore_point_story"] = "14:51"
    save_journal(j)
    note(f"the agent's write journal (out/journal.json) has {len(j['writes']['C'])} entries per copy, each with its fact id")
    note(f"the last good write is at {hhmmss(iso(newest))} server time; restore point = {hhmmss(point)} "
         f"(story time 14:51, just before the incident)")
    emit("day", "", restore_point=point, journal={a: len(j["writes"][a]) for a in ARMS})
    time.sleep(2)  # the incident's writes must land after the restore point
    return point


# --------------------------------------------------------------------------- step 4: the incident

def incident(cli: CLI, d: dict, world: dict, ids: dict, j: dict) -> None:
    lab = labels(d)
    inc = d["incident"]
    note(f"{inc['at']} — {inc['what']}")
    cur = expected(d)
    for op in inc["ops"]:
        apply(cli, d, world, ids, j, op, inc["at"])
        if op["op"] == "update":
            line = f"{lab[op['key']]}: {cur.get(op['key'])} → {op['value']}"
        elif op["op"] == "forget":
            line = f"{lab[op['key']]}: forgotten ({cur.get(op['key'])})"
        else:
            line = f"{lab[op['key']]}: new — {op['value']}"
        emit("progress", f"    {inc['at']}  {line}", phase="incident", op=op["op"], key=op["key"], value=op.get("value"),
             was=cur.get(op["key"]))
    m = known(d)
    # The restore point must separate the day from the incident: check the incident's first write on the server clock.
    first = inc["ops"][0]
    tr = cli.run("fact", "trace", ids["C"][first["key"]], "--project", world["C"], scoped=True, echo=False) or {}
    hit = [e for e in tr.get("trace") or [] if e.get("new_fact") == first.get("text")]
    if not hit or parse_ts(hit[0]["timestamp"]) <= parse_ts(j["restore_point"]):
        die("the incident's first write is not after the restore point; run `python3 demo.py cleanup` and try again")
    live = project_facts(cli, world["C"], echo=True)
    now = {}
    for f in live:
        k = m.get(f["fact"])
        if k:
            now.setdefault(k[0], []).append(k[1])
    t = expected(d)
    emit("text", "\n    fact                  before the incident        now (copy C)")
    for k in row_keys(d):
        if k not in t and k not in now:
            continue
        emit("text", f"    {lab[k]:<21} {t.get(k, '(none)'):<26} {says(now.get(k, []))}")
    note(f"`fact list` now shows {len(live)} facts; nothing failed — every write was a valid API call")
    emit("incident", "", now=now)


# --------------------------------------------------------------------------- step 5: restore three ways

def state_at(trace: list[dict], point: datetime) -> str | None:
    """The fact's text at `point`, or None when it did not exist or had been forgotten by then."""
    text = None
    for e in sorted(trace, key=lambda e: e.get("timestamp") or ""):
        if parse_ts(e.get("timestamp")) > point:
            break
        text = None if e.get("event") == "FORGET" else e.get("new_fact")
    return text


def restore_backup(cli: CLI, d: dict, world: dict) -> dict:
    p = world["A"]
    backup = json.loads((OUT / "backup-a.json").read_text(encoding="utf-8"))
    live = project_facts(cli, p, echo=True)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda f: delete_fact(cli, f["id"], ["--project", p]), live))
    emit("progress", f"    A  forgot all {len(live)} live facts", arm="A", action="wipe", n=len(live))
    cli.run("fact", "add", *[b["text"] for b in backup], "--project", p, scoped=True, echo=True)
    emit("progress", f"    A  added the {len(backup)} facts from out/backup-a.json", arm="A", action="load", n=len(backup))
    return dict(arm="A", wiped=len(live), loaded=len(backup))


def restore_trace(cli: CLI, d: dict, world: dict, j: dict, arm: str, point_iso: str) -> dict:
    p = world[arm]
    point = parse_ts(point_iso)
    m = known(d)
    lab = labels(d)
    backup = json.loads((OUT / f"backup-{arm.lower()}.json").read_text(encoding="utf-8"))
    listed = project_facts(cli, p, echo=True)
    j.setdefault("listed_at_restore", {})[arm] = [f["id"] for f in listed]
    save_journal(j)
    journal_ids = list(dict.fromkeys(w["id"] for w in j["writes"][arm]))
    if arm == "B":
        catalog = list(dict.fromkeys([b["id"] for b in backup] + [f["id"] for f in listed]))
        src = f"{len(backup)} in the backup + {len(listed)} in `fact list` → {len(catalog)} distinct"
    else:
        catalog = journal_ids
        src = f"{len(catalog)} distinct in the write journal"
    unseen = [w for w in j["writes"][arm] if w["id"] not in catalog and w["op"] == "add"]
    emit("text", f"\n  {d['arms'][arm]['title']} — fact ids to check: {src}")
    if unseen:
        for w in unseen:
            emit("text", f"    not in the backup and not in `fact list`: {w['id']} ({lab[w['key']]}, added {w['at']}) "
                         "— forgotten, so `fact list` cannot show it")
    rows = []
    for fid in catalog:
        tr = cli.run("fact", "trace", fid, "--project", p, scoped=True, echo=True) or {}
        entries = tr.get("trace") or []
        fact = tr.get("fact") or {}
        want = state_at(entries, point)
        cur = None if fact.get("expired") else fact.get("fact")
        texts = [e.get("new_fact") or e.get("old_fact") for e in entries] + [fact.get("fact")]
        key = next((m[x][0] for x in texts if x in m), "?")
        forgot_at = next((e.get("timestamp") for e in entries if e.get("event") == "FORGET"), None)
        if cur == want:
            action = "keep" if cur else "stays forgotten"
        elif want is None:
            cli.run("fact", "delete", fid, "--project", p, scoped=True, echo=True)
            action = "forget"
        elif cur is None:
            r = cli.run("fact", "add", want, "--project", p, scoped=True, echo=True) or {}
            new = ((r.get("facts") or [{}])[0]).get("id")
            cli.run("fact", "update", new, "--metadata", json.dumps({"restored_from": fid, "restored_to": point_iso}),
                    "--project", p, scoped=True, echo=True)
            action = f"re-add as {new}"
        else:
            cli.run("fact", "update", fid, "--text", want, "--metadata", json.dumps({"restored_to": point_iso}),
                    "--project", p, scoped=True, echo=True)
            action = "update"
        at_point = m.get(want, (None, want))[1] if want else "(none)"
        now = m.get(cur, (None, cur))[1] if cur else "(forgotten)"
        rows.append(dict(id=fid, key=key, at_point=at_point, now=now, action=action, forgot_at=forgot_at))
        emit("progress", f"    {arm}  {lab.get(key, key):<21} at {hhmmss(point_iso)}: {at_point:<26} now: {now:<16} → {action}",
             arm=arm, key=key, at_point=at_point, now=now, action=action, id=fid,
             forgot_at=forgot_at, point=point_iso)
    return dict(arm=arm, catalog=len(catalog), backup=len(backup), listed=len(listed), journal=len(journal_ids),
                unseen=[dict(id=w["id"], key=w["key"], at=w["at"]) for w in unseen], rows=rows)


def restore(cli: CLI, d: dict, world: dict, j: dict, point: str) -> dict:
    note(f"{d['restore_at']} — the incident is found; each copy is restored a different way")
    for arm in ARMS:
        emit("text", f"    {d['arms'][arm]['title']}: {d['arms'][arm]['how']}")
    emit("text", f"\n  {d['arms']['A']['title']}")
    res = {"A": restore_backup(cli, d, world)}
    for arm in ("B", "C"):
        res[arm] = restore_trace(cli, d, world, j, arm, point)
    (OUT / "restore.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    emit("restored", "", **res)
    return res


# --------------------------------------------------------------------------- step 6: what each copy says

def read_memory(cli: CLI, d: dict, project: str, echo: bool = True) -> dict:
    """key → [{value, id, metadata}] for every live fact."""
    m = known(d)
    out: dict = {}
    for f in project_facts(cli, project, echo=echo):
        k = m.get(f["fact"], ("unknown", f["fact"]))
        out.setdefault(k[0], []).append(dict(value=k[1], id=f["id"], metadata=f.get("metadata") or {}))
    return out


def cell_ok(d: dict, key: str, entries: list[dict]) -> bool:
    t = expected(d)
    if key in t:
        return len(entries) == 1 and entries[0]["value"] == t[key]
    return not entries


def what_memory_says(cli: CLI, d: dict, world: dict, j: dict) -> dict:
    t = expected(d)
    lab = labels(d)
    mem = {arm: read_memory(cli, d, world[arm]) for arm in ARMS}
    gone = set(erased(d))
    emit("text", "\n  Each copy should now say what it said at 14:51:\n")
    emit("text", "    fact                  should say                 " + "".join(f"{d['arms'][a]['short']:<28}" for a in ARMS))
    for k in row_keys(d):
        cells = []
        for arm in ARMS:
            e = mem[arm].get(k, [])
            ok = cell_ok(d, k, e)
            mark = " ✓" if ok else (" ✗ RESURRECTED" if k in gone and e else " ✗")
            cells.append(f"{says([x['value'] for x in e]) + mark:<28}")
        should = t.get(k, "(deleted, ER-118)" if k in gone else "(never)")
        emit("text", f"    {lab[k]:<21} {should:<26} " + "".join(cells).rstrip())
    # The erased fact: its history lives on the id that was forgotten; A's copy is a new id with none of it.
    k = erased(d)[0]
    old = next(w["id"] for w in j["writes"]["C"] if w["key"] == k and w["op"] == "forget")
    tr = cli.run("fact", "trace", old, "--project", world["C"], scoped=True, echo=True) or {}
    fg = next((e for e in tr.get("trace") or [] if e.get("event") == "FORGET"), {})
    emit("text", f"\n  fact trace on {lab[k]} in C: FORGET at {hhmmss(fg.get('timestamp'))}, before the restore point "
                 f"{hhmmss(j['restore_point'])} — so the restore leaves it forgotten")
    back = mem["A"].get(k, [])
    trace_a = None
    if back:
        trace_a = cli.run("fact", "trace", back[0]["id"], "--project", world["A"], scoped=True, echo=True) or {}
        events = [e.get("event") for e in trace_a.get("trace") or []]
        emit("text", f"  in A it is back as {back[0]['id']}: trace {' → '.join(reversed(events))} — a new fact, "
                     "with no link to the deletion request")
    emit("memory", "", expected=t, mem=mem, erased=sorted(gone), forget_at=fg.get("timestamp"),
         resurrected=bool(back), point=j["restore_point"])
    return mem


# --------------------------------------------------------------------------- step 7: score

def score(cli: CLI, d: dict, world: dict, j: dict, res: dict) -> dict:
    t = expected(d)
    gone = set(erased(d))
    day_keys = [op["key"] for op in d["day"] if op["op"] != "forget"]
    inc = {op["key"]: op["value"] for op in d["incident"]["ops"] if "value" in op}
    rows = []
    for arm in ARMS:
        mem = read_memory(cli, d, world[arm], echo=True)
        right = sum(1 for k in t if cell_ok(d, k, mem.get(k, [])))
        kept = sum(1 for k in day_keys if cell_ok(d, k, mem.get(k, [])))
        honoured = all(not mem.get(k) for k in gone)
        left = sum(1 for k, v in inc.items() for e in mem.get(k, []) if e["value"] == v)
        extra = sum(len(es) for k, es in mem.items() if k not in t)
        lost = [k for k in t if not mem.get(k)]
        rows.append(dict(arm=arm, title=d["arms"][arm]["title"], short=d["arms"][arm]["short"], right=right, of=len(t),
                         kept=kept, day=len(day_keys), honoured=honoured, incident_left=left, extra=extra, lost=lost,
                         says={k: [e["value"] for e in mem.get(k, [])] for k in row_keys(d)}))
    emit("text", "\n  Restored to 14:51 — the target is what memory said just before the incident:\n")
    emit("text", "    copy                          facts right   today's changes kept   erasure honoured   incident left")
    for r in rows:
        emit("text", f"    {r['short']:<29} {r['right']}/{r['of']:<11} {r['kept']}/{r['day']:<20} "
                     f"{'yes' if r['honoured'] else 'NO':<18} {r['incident_left']}")
    lab = labels(d)
    a, b, c = rows
    note(f"A: back to last night — {a['day'] - a['kept']} of {a['day']} changes made today are gone, and "
         f"{', '.join(lab[k] for k in gone)} is back after an erasure request")
    if b["lost"]:
        note(f"B: {', '.join(lab[k] for k in b['lost'])} is missing — added after the backup, forgotten in the incident: "
             "not in the backup, not in `fact list`, so B never asked `fact trace` about it")
    note(f"C: {c['right']}/{c['of']} — the journal had every id, and `fact trace` reads forgotten facts too")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, restore_point=j["restore_point"],
                  expected=t, rows=rows, restore=res)
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    emit("score", "", **report)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_story()
    banner(2, TOTAL, "Setup — one agent memory, three copies; the 02:00 backup")
    world, ids, j = setup(cli, d)
    banner(3, TOTAL, "09:10–11:40 — the day's changes, journaled; the restore point")
    point = the_day(cli, d, world, ids, j)
    banner(4, TOTAL, "14:52 — a bad agent run rewrites memory")
    incident(cli, d, world, ids, j)
    banner(5, TOTAL, "15:20 — restore three ways")
    res = restore(cli, d, world, j, point)
    banner(6, TOTAL, "What each copy says now — and what fact trace shows")
    what_memory_says(cli, d, world, j)
    banner(7, TOTAL, "Score — which restore got 14:51 back")
    return score(cli, d, world, j, res)


# --------------------------------------------------------------------------- read-only subcommands

def show_journal(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli)
    j = load_journal()
    lab = labels(d)
    listed = set((j.get("listed_at_restore") or {}).get("C") or [])
    backup = {b["id"] for b in json.loads((OUT / "backup-c.json").read_text(encoding="utf-8"))}
    emit("text", f"\n  copy C's write journal (out/journal.json) — every id the agent wrote, and who else can see it:\n")
    emit("text", "    when          op       fact                  id                                         in backup  in fact list at 15:20")
    for w in j["writes"]["C"]:
        emit("text", f"    {w['at']:<13} {w['op']:<8} {lab[w['key']]:<21} {w['id']:<42} "
                     f"{'yes' if w['id'] in backup else '-':<10} {'yes' if w['id'] in listed else '-'}")
    ids = list(dict.fromkeys(w["id"] for w in j["writes"]["C"]))
    blind = [i for i in ids if i not in backup and i not in listed]
    names = ", ".join(lab[next(w["key"] for w in j["writes"]["C"] if w["id"] == i)] for i in blind)
    note(f"{len(ids)} ids in the journal; {len(blind)} in neither the backup nor `fact list` when the restore began"
         + (f": {names}" if blind else ""))


def show_trace(cli: CLI) -> None:
    d = load_data()
    world = load_world(cli)
    j = load_journal()
    lab = labels(d)
    for key in [*erased(d), "sms"]:
        fid = next(w["id"] for w in j["writes"]["C"] if w["key"] == key and w["op"] == "add")
        tr = cli.run("fact", "trace", fid, "--project", world["C"], scoped=True, echo=True) or {}
        emit("text", f"\n  {lab[key]} — {fid} in copy C (restore point {hhmmss(j['restore_point'])}):")
        for e in reversed(tr.get("trace") or []):
            text = e.get("new_fact") or e.get("old_fact") or ""
            side = "before" if parse_ts(e.get("timestamp")) <= parse_ts(j["restore_point"]) else "after"
            emit("text", f"    {hhmmss(e.get('timestamp'))}  {e.get('event'):<7} {'(' + side + ' the restore point)':<26}  {text}")


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
        note(f"deleted {nfacts} facts and {nproj} projects" if nproj else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "journal", "restore", "backup-a", "backup-b", "backup-c"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "journal", "trace", "cleanup"],
                    help="run = full demo (default); journal = the agent's write journal vs the backup and `fact list`; "
                         "trace = the history of the erased fact and the lost one; cleanup = delete everything")
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
    titles = {"cleanup": "Cleanup — remove everything the demo created", "journal": "The agent's write journal",
              "trace": "fact trace — the erased fact and the lost one"}
    if args.command in titles:
        banner(2, 2, titles[args.command])
        {"cleanup": cleanup, "journal": show_journal, "trace": show_trace}[args.command](cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py journal` or `python3 demo.py trace`; "
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

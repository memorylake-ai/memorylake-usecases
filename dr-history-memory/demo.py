#!/usr/bin/env python3
"""dr-history-memory — a disaster-recovery restore that gets every value right and loses the history.

Harrowgate Freight (fictional) runs a dispatch agent on project memory. Over a quarter the team changes four of
its seven facts six times (one of them changes and changes back). A nightly job backs memory up in one of three
formats. At 03:40 a teardown script meant for staging deletes the production memory project: facts and
`fact trace` history are gone with it (404). At 04:05 the backup is restored into a new project, three ways:

  A  values backup: `fact list` export → `fact add` every text
  B  values + version chain: also each fact's version texts from `fact trace` → replay them in order
  C  versions + original times: also each version's original timestamp and fact id → replay, keep them in metadata

Then the DR drill (current values) and the questions an incident review asks: what was it before, how many
times did it change, and when.

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

PREFIX = "mlu-drh"  # memorylake-usecases / dr-history-memory


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

TOTAL = 8             # steps in a full run
ARMS = ("A", "B", "C")
GAP = 2.0             # seconds between two changes, so each version has its own second on the server clock


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def project_name(arm: str | None) -> str:
    return "primary" if arm is None else f"dr-{arm.lower()}"


def find_project(cli: CLI, arm: str | None, echo: bool = False):
    return cli.try_run("proj", "get", cid(project_name(arm)), "--by-custom-id", scoped=True, echo=echo)


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


def trace_of(cli: CLI, fact_id: str, project: str, echo: bool = False) -> tuple[dict, list[dict]]:
    """The fact and its history, oldest first."""
    tr = cli.run("fact", "trace", fact_id, "--project", project, scoped=True, echo=echo) or {}
    return tr.get("fact") or {}, sorted(tr.get("trace") or [], key=lambda e: e.get("timestamp") or "")


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


def labels(d: dict) -> dict:
    return {f["key"]: f["label"] for f in d["facts"]}


def known(d: dict) -> dict:
    """Every text the demo ever writes → (key, value). Writes are verbatim, so an exact match identifies a fact."""
    m = {f["text"]: (f["key"], f["value"]) for f in d["facts"]}
    for c in d["changes"]:
        m[c["text"]] = (c["key"], c["value"])
    return m


def versions(d: dict) -> dict:
    """key → every value the fact ever had, oldest first. Worked out from the story alone."""
    v = {f["key"]: [f["value"]] for f in d["facts"]}
    for c in d["changes"]:
        v[c["key"]].append(c["value"])
    return v


def changed(d: dict) -> list[str]:
    v = versions(d)
    return [f["key"] for f in d["facts"] if len(v[f["key"]]) > 1]


def check_story() -> None:
    """The rules on the story itself, before any call is made: a wrong rule would misread every run."""
    d = load_data()
    v = versions(d)
    if len(d["facts"]) != 7 or len(changed(d)) != 4 or sum(len(x) for x in v.values()) != 13:
        die(f"story self-check failed: {v}")
    if v["oncall"][0] != v["oncall"][-1] or len(set(v["oncall"])) != 2:
        die("story self-check failed: the on-call fact must change and change back")
    firsts = [f["text"] for f in d["facts"]]
    if len(set(firsts)) != len(firsts):
        die("story self-check failed: two facts share a text")


def chain(values: list[str]) -> str:
    return " → ".join(values) if values else "(nothing)"


def delta(a: str, b: str) -> str:
    s = (parse_ts(b) - parse_ts(a)).total_seconds()
    sign = "+" if s >= 0 else "-"
    s = abs(s)
    return f"{sign}{int(s // 60)}m{int(s % 60):02d}s" if s >= 60 else f"{sign}{s:.1f}s"


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


# --------------------------------------------------------------------------- step 2: setup

def leftovers(cli: CLI) -> bool:
    return any(find_project(cli, arm) for arm in (None, *ARMS))


def setup(cli: CLI, d: dict) -> tuple[str, dict]:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    for name in ("backup-a", "backup-b", "backup-c", "truth", "restore", "report"):
        if (OUT / f"{name}.json").exists():
            (OUT / f"{name}.json").unlink()
    primary = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("primary"),
                      scoped=True, echo=True)["id"]
    r = cli.run("fact", "add", *[f["text"] for f in d["facts"]], "--project", primary, scoped=True, echo=True) or {}
    m = known(d)
    ids = {m[f["fact"]][0]: f["id"] for f in r.get("facts") or [] if f.get("fact") in m}
    missing = [f["key"] for f in d["facts"] if f["key"] not in ids]
    if missing:
        die(f"`fact add` did not return ids for {missing}; run `python3 demo.py cleanup` and try again")
    lab = labels(d)
    emit("text", "\n    fact                          says")
    for f in d["facts"]:
        emit("text", f"    {lab[f['key']]:<29} {f['value']}")
    note(f"{len(ids)} facts in {d['project_name']} ({primary})")
    emit("world", "", primary=primary)
    emit("setup", "", primary=primary, facts=[dict(key=f["key"], label=f["label"], value=f["value"], id=ids[f["key"]])
                                              for f in d["facts"]])
    save("restore", dict(primary=primary, ids=ids))
    return primary, ids


# --------------------------------------------------------------------------- step 3: a quarter of changes

def the_changes(cli: CLI, d: dict, primary: str, ids: dict) -> None:
    lab = labels(d)
    cur = {f["key"]: f["value"] for f in d["facts"]}
    note("a quarter of changes, compressed into seconds: each one is a real `fact update`, on the server's clock")
    for i, c in enumerate(d["changes"]):
        if i:
            time.sleep(GAP)
        cli.run("fact", "update", ids[c["key"]], "--text", c["text"], "--project", primary, scoped=True, echo=True)
        emit("progress", f"    {c['when']:<10} {lab[c['key']]:<26} {cur[c['key']]} → {c['value']}  ({c['why']})",
             phase="change", key=c["key"], label=lab[c["key"]], was=cur[c["key"]], value=c["value"], when=c["when"],
             why=c["why"])
        cur[c["key"]] = c["value"]
    k = d["changes"][0]["key"]
    _, entries = trace_of(cli, ids[k], primary, echo=True)
    m = known(d)
    emit("text", f"\n  fact trace on {lab[k]} — every version, on the server's clock:")
    rows = []
    for e in entries:
        v = m.get(e.get("new_fact"), (None, e.get("new_fact")))[1]
        emit("text", f"    {hhmmss(e.get('timestamp'))}  {e.get('event'):<7} {v}")
        rows.append(dict(at=e.get("timestamp"), event=e.get("event"), value=v))
    emit("changes", "", key=k, label=lab[k], trace=rows)


# --------------------------------------------------------------------------- step 4: the nightly backup, three formats

def read_history(cli: CLI, primary: str, echo: bool) -> list[dict]:
    """Every live fact with its full trace, oldest first."""
    out = []
    for i, f in enumerate(project_facts(cli, primary, echo=echo)):
        _, entries = trace_of(cli, f["id"], primary, echo=echo and i == 0)
        out.append(dict(id=f["id"], text=f["fact"], trace=[
            dict(event=e.get("event"), text=e.get("new_fact"), at=e.get("timestamp"), event_id=e.get("source_event_id"))
            for e in entries if e.get("event") in ("ADD", "UPDATE")]))
    return out


def backup(cli: CLI, d: dict, primary: str) -> dict:
    note(f"{d['backup']['at']} — the {d['backup']['what']}, in three formats (one per restore):")
    hist = read_history(cli, primary, echo=True)
    files = {
        "A": [dict(id=h["id"], text=h["text"]) for h in hist],
        "B": [dict(id=h["id"], versions=[t["text"] for t in h["trace"]]) for h in hist],
        "C": [dict(id=h["id"], project=primary, versions=h["trace"]) for h in hist],
    }
    emit("text", "\n    file                 per fact                                                   bytes")
    sizes = {}
    for arm in ARMS:
        p = save(f"backup-{arm.lower()}", files[arm])
        sizes[arm] = p.stat().st_size
        emit("text", f"    out/{p.name:<16} {d['arms'][arm]['backup']:<58} {sizes[arm]:>5}")
    nv = sum(len(h["trace"]) for h in hist)
    note(f"{len(hist)} facts, {nv} versions in their history")
    emit("backup", "", sizes=sizes, facts=len(hist), versions=nv,
         formats={a: d["arms"][a]["backup"] for a in ARMS})
    return files


# --------------------------------------------------------------------------- step 5: the incident

def incident(cli: CLI, d: dict, primary: str, ids: dict) -> dict:
    lab = labels(d)
    # The ground truth for the score: read again, separately from the backups, just before the project goes.
    truth = read_history(cli, primary, echo=False)
    save("truth", dict(primary=primary, facts=truth))
    last = max(t["at"] for h in truth for t in h["trace"])
    note(f"{d['incident']['at']} — {d['incident']['what']}")
    cli.run("proj", "delete", primary, scoped=True, echo=True)
    k = d["changes"][0]["key"]
    try:
        cli.run("fact", "trace", ids[k], "--project", primary, scoped=True, echo=True)
        die("the deleted project still answers `fact trace`; the story needs it gone")
    except CLIError as e:
        msg = next((ln.strip() for ln in (e.stderr + e.stdout).splitlines() if "not found" in ln.lower()), str(e))
        emit("text", f"    → {msg}")
    note(f"{lab[k]}'s history went with the project: the backup is all that is left. "
         f"Last write the project ever had: {hhmmss(last)} server time")
    emit("incident", "", primary=primary, last_write=last)
    return dict(last_write=last)


# --------------------------------------------------------------------------- step 6: restore three ways

def add_one(cli: CLI, text: str, project: str) -> str:
    r = cli.run("fact", "add", text, "--project", project, scoped=True, echo=False) or {}
    fid = ((r.get("facts") or [{}])[0]).get("id")
    if not fid:
        die(f"`fact add` returned no id for {text!r}")
    return fid


def restore(cli: CLI, d: dict, files: dict) -> dict:
    note(f"{d['restore_at']} — each backup is restored into a new project")
    world = {}
    calls = {}
    for arm in ARMS:
        world[arm] = cli.run("proj", "create", "--name", f"{d['project_name']} (DR {arm})",
                             "--custom-id", cid(project_name(arm)), scoped=True, echo=True)["id"]
    emit("world", "", **world)
    # A: one call, every text.
    cli.run("fact", "add", *[b["text"] for b in files["A"]], "--project", world["A"], scoped=True, echo=True)
    calls["A"] = 1
    emit("progress", f"    A  added {len(files['A'])} facts in 1 call", arm="A", facts=len(files["A"]), calls=1)
    # B and C: replay each fact's versions in order. C's last write also carries the original id and times.
    for arm in ("B", "C"):
        n = 0
        echoed = False
        for b in files[arm]:
            vs = b["versions"]
            texts = [v["text"] for v in vs] if arm == "C" else vs
            show = not echoed and len(texts) > 1
            r = cli.run("fact", "add", texts[0], "--project", world[arm], scoped=True, echo=show) or {}
            fid = ((r.get("facts") or [{}])[0]).get("id")
            if not fid:
                die(f"`fact add` returned no id for {texts[0]!r}")
            n += 1
            for i, t in enumerate(texts[1:], 1):
                extra = []
                if arm == "C" and i == len(texts) - 1:
                    extra = ["--metadata", json.dumps(dict(restored_from=dict(project=b["project"], fact_id=b["id"]),
                                                           history=[dict(v=j + 1, event=v["event"], at=v["at"],
                                                                         event_id=v["event_id"])
                                                                    for j, v in enumerate(vs)]))]
                cli.run("fact", "update", fid, "--text", t, *extra, "--project", world[arm], scoped=True, echo=show)
                n += 1
            if arm == "C" and len(texts) == 1:
                meta = dict(restored_from=dict(project=b["project"], fact_id=b["id"]),
                            history=[dict(v=1, event=vs[0]["event"], at=vs[0]["at"], event_id=vs[0]["event_id"])])
                cli.run("fact", "update", fid, "--metadata", json.dumps(meta), "--project", world[arm],
                        scoped=True, echo=False)
                n += 1
            echoed = echoed or show
        calls[arm] = n
        nv = sum(len(b["versions"]) for b in files[arm])
        what = f"replayed {nv} versions into {len(files[arm])} facts" + (
            ", original id + times in metadata" if arm == "C" else "")
        emit("progress", f"    {arm}  {what} ({n} calls)", arm=arm, facts=len(files[arm]), versions=nv, calls=n)
    save("restore", dict(load("restore"), world=world, calls=calls))
    emit("restored", "", world=world, calls=calls)
    return world


# --------------------------------------------------------------------------- step 7: the DR drill

def read_copy(cli: CLI, d: dict, project: str, echo: bool = True) -> dict:
    """key → [{value, id, metadata}] for every live fact."""
    m = known(d)
    out: dict = {}
    for f in project_facts(cli, project, echo=echo):
        k = m.get(f["fact"], ("unknown", f["fact"]))
        out.setdefault(k[0], []).append(dict(value=k[1], id=f["id"], metadata=f.get("metadata") or {}))
    return out


def drill(cli: CLI, d: dict, world: dict) -> dict:
    lab = labels(d)
    now = {k: v[-1] for k, v in versions(d).items()}
    mem = {arm: read_copy(cli, d, world[arm]) for arm in ARMS}
    emit("text", "\n  The DR drill: does each copy say what production said?\n")
    emit("text", "    fact                          should say        " + "".join(f"{d['arms'][a]['short']:<18}" for a in ARMS))
    right = {a: 0 for a in ARMS}
    for f in d["facts"]:
        k = f["key"]
        cells = []
        for arm in ARMS:
            e = mem[arm].get(k, [])
            ok = len(e) == 1 and e[0]["value"] == now[k]
            right[arm] += ok
            cells.append(f"{(' + '.join(x['value'] for x in e) or '(nothing)') + (' ✓' if ok else ' ✗'):<18}")
        emit("text", f"    {lab[k]:<29} {now[k]:<17} " + "".join(cells).rstrip())
    passed = all(right[a] == len(d["facts"]) for a in ARMS)
    note(" · ".join(f"{a}: {right[a]}/{len(d['facts'])}" for a in ARMS) +
         (" — the drill passes for all three restores" if passed else ""))
    emit("drill", "", right=right, of=len(d["facts"]), now=now,
         mem={a: {k: [x["value"] for x in v] for k, v in mem[a].items()} for a in ARMS})
    return mem


# --------------------------------------------------------------------------- step 8: the questions an incident review asks

def answer(cli: CLI, d: dict, arm: str, entry: dict, project: str, echo: bool) -> dict:
    """What one restored copy can say about a fact's past: its versions, the value before now, and when it last changed."""
    m = known(d)
    _, entries = trace_of(cli, entry["id"], project, echo=echo)
    vals = [m.get(e.get("new_fact"), (None, e.get("new_fact")))[1] for e in entries if e.get("event") in ("ADD", "UPDATE")]
    hist = (entry.get("metadata") or {}).get("history") or []
    if hist:
        at, source = hist[-1]["at"], "metadata"
    else:
        at, source = (entries[-1].get("timestamp") if entries else None), "trace"
    return dict(versions=vals, before=vals[-2] if len(vals) > 1 else None, at=at, source=source, id=entry["id"])


def review(cli: CLI, d: dict, world: dict, mem: dict | None = None, echo: bool = True) -> dict:
    lab = labels(d)
    truth = {}
    m = known(d)
    t = load("truth")
    for h in t["facts"]:
        key = m[h["text"]][0]
        truth[key] = dict(versions=[m[x["text"]][1] for x in h["trace"]], at=h["trace"][-1]["at"], id=h["id"])
    last = max(x["at"] for h in t["facts"] for x in h["trace"])
    mem = mem or {arm: read_copy(cli, d, world[arm], echo=False) for arm in ARMS}
    keys = changed(d)
    rows = {a: dict(before=0, count=0, when=0, after_last=0) for a in ARMS}
    out = {}
    for k in keys:
        tr = truth[k]
        emit("text", f"\n  {lab[k]} — production: {chain(tr['versions'])}; last changed {hhmmss(tr['at'])}")
        out[k] = dict(truth=tr, arms={})
        for arm in ARMS:
            e = (mem[arm].get(k) or [None])[0]
            if not e:
                emit("text", f"    {d['arms'][arm]['short']:<13} (missing)")
                continue
            a = answer(cli, d, arm, e, world[arm], echo=echo and k == keys[0])
            ok_before = a["before"] == tr["versions"][-2]
            ok_count = len(a["versions"]) == len(tr["versions"])
            ok_when = a["at"] == tr["at"]
            late = bool(a["at"]) and parse_ts(a["at"]) > parse_ts(last)
            rows[arm]["before"] += ok_before
            rows[arm]["count"] += ok_count
            rows[arm]["when"] += ok_when
            rows[arm]["after_last"] += late
            before = a["before"] or "(none: never changed)"
            when = f"{hhmmss(a['at'])} ({a['source']})"
            if not ok_when:
                when += f" {delta(tr['at'], a['at'])}"
            emit("text", f"    {d['arms'][arm]['short']:<13} {len(a['versions'])} version(s)  "
                         f"before: {before + (' ✓' if ok_before else ' ✗'):<26} "
                         f"changed: {when}{' ✓' if ok_when else ' ✗'}")
            out[k]["arms"][arm] = dict(a, ok_before=ok_before, ok_count=ok_count, ok_when=ok_when, after_last=late)
        emit("history", "", key=k, label=lab[k], truth=tr, arms=out[k]["arms"])
    return dict(rows=rows, of=len(keys), detail=out, last_write=last)


def score(cli: CLI, d: dict, world: dict, mem: dict) -> dict:
    r = review(cli, d, world, mem)
    rows, n = r["rows"], r["of"]
    right = {a: sum(1 for f in d["facts"] if [x["value"] for x in mem[a].get(f["key"], [])] == [versions(d)[f["key"]][-1]])
             for a in ARMS}
    emit("text", f"\n  Score — the drill, then the {n} facts that changed:\n")
    emit("text", "    copy             current values   value before now   version count   when it last changed")
    for a in ARMS:
        emit("text", f"    {d['arms'][a]['short']:<16} {right[a]}/{len(d['facts']):<14} {rows[a]['before']}/{n:<16} "
                     f"{rows[a]['count']}/{n:<13} {rows[a]['when']}/{n}")
    lab = labels(d)
    oc = r["detail"].get("oncall", {}).get("arms", {})
    if oc.get("A"):
        note(f"A: {lab['oncall']} says Lisbon with 1 version — it was Kraków for part of the quarter, "
             "and nothing in the restored project says so")
    if rows["B"]["after_last"]:
        note(f"B: every version is there, but {rows['B']['after_last']}/{n} last changes are dated after the last write "
             f"production ever had ({hhmmss(r['last_write'])}) — a review would read them as changes made during the outage")
    note(f"C: {rows['C']['when']}/{n} — the original times survive in metadata; the restored trace itself is still dated today")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, current=right, rows=rows, of=n,
                  facts=len(d["facts"]), last_write=r["last_write"], detail=r["detail"], world=world)
    save("report", report)
    emit("score", "", **report)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_story()
    banner(2, TOTAL, "Setup — the dispatch agent's project memory")
    primary, ids = setup(cli, d)
    banner(3, TOTAL, "July → October — six changes to four facts")
    the_changes(cli, d, primary, ids)
    banner(4, TOTAL, "02:00 — the nightly backup, in three formats")
    files = backup(cli, d, primary)
    banner(5, TOTAL, "03:40 — the memory project is deleted")
    incident(cli, d, primary, ids)
    banner(6, TOTAL, "04:05 — restore into a new project, three ways")
    world = restore(cli, d, files)
    banner(7, TOTAL, "The DR drill — current values")
    mem = drill(cli, d, world)
    banner(8, TOTAL, "The incident review — before, how often, when")
    return score(cli, d, world, mem)


# --------------------------------------------------------------------------- read-only subcommands

def load_world(cli: CLI) -> dict:
    world = {}
    for arm in ARMS:
        p = find_project(cli, arm)
        if not p:
            die("the restored copies do not exist yet; run `python3 demo.py` first")
        world[arm] = p["id"]
    return world


def show_history(cli: CLI, key: str | None = None) -> None:
    d = load_data()
    world = load_world(cli)
    lab = labels(d)
    keys = [key] if key else changed(d)
    if key and key not in lab:
        die(f"unknown fact {key!r}; one of: {', '.join(lab)}")
    t = load("truth")
    m = known(d)
    for k in keys:
        h = next(x for x in t["facts"] if m[x["text"]][0] == k)
        emit("text", f"\n  {lab[k]}")
        emit("text", f"    production ({h['id']}, from out/truth.json — the project itself is gone):")
        for x in h["trace"]:
            emit("text", f"      {hhmmss(x['at'])}  {x['event']:<7} {m[x['text']][1]}")
        for arm in ARMS:
            e = (read_copy(cli, d, world[arm], echo=False).get(k) or [None])[0]
            if not e:
                continue
            _, entries = trace_of(cli, e["id"], world[arm], echo=True)
            emit("text", f"    {d['arms'][arm]['short']} ({e['id']}), fact trace:")
            for x in entries:
                emit("text", f"      {hhmmss(x.get('timestamp'))}  {x.get('event'):<7} {m.get(x.get('new_fact'), (0, x.get('new_fact')))[1]}")
            hist = (e.get("metadata") or {}).get("history")
            if hist:
                emit("text", "      metadata.history: " + ", ".join(f"v{x['v']} {hhmmss(x['at'])}" for x in hist))


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    nfacts, nproj = 0, 0
    for arm in (None, *ARMS):
        p = find_project(cli, arm, echo=not quiet and arm is None)
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
    for name in ("report", "truth", "restore", "backup-a", "backup-b", "backup-c"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "history", "cleanup"],
                    help="run = full demo (default); history [fact] = production's trace next to each restored copy's; "
                         "cleanup = delete everything")
    ap.add_argument("fact", nargs="?", help="for `history`: cutoff, claims, terms or oncall (default: all four)")
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
        banner(2, 2, "History — production's fact trace next to each restored copy's")
        show_history(cli, args.fact)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py history oncall`; `python3 demo.py cleanup` removes the demo data.")


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

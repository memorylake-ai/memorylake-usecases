#!/usr/bin/env python3
"""behavior-diff-memory — an agent's answers changed between two evals: which memory change caused which?

Sallowfen Outfitters (fictional) runs a support agent on project memory: one fact per policy, a text people read
and metadata the agent's code reads ({key, topic, kind, status, applies_to, op, value}). The agent is plain code:
for a question it reads the rules on the question's topic, keeps the active ones, prefers the most specific, and
answers from the rule's value. Eval 1 runs six questions. Then a week of ordinary memory changes: text edits, new
rules, a deleted promo, one approval (a draft rule set to active — a metadata-only `fact update`) and three review
stamps. Eval 2 runs the same six questions; three answers changed. Three ways to find out why:

  A  diff the time window in `fact trace`   — every entry after eval 1, blamed by topic
  B  diff two full snapshots                — eval 1 saved the whole `fact list`; compare it with today's
  C  diff the memory the answer read        — every answer saved a receipt of the facts it read; compare the two
                                              receipts of a changed answer, then replay the agent with one changed
                                              fact at a time put back to its eval-1 version

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

PREFIX = "mlu-bdm"  # memorylake-usecases / behavior-diff-memory


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
METHODS = ("A", "B", "C")
GAP = 3.0             # seconds after eval 1, so the week's writes have their own seconds on the server clock


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("policies"), "--by-custom-id", scoped=True, echo=echo)


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


def meta_of(rule: dict) -> dict:
    """What the agent's code reads: everything in the rule except its text."""
    return {k: v for k, v in rule.items() if k != "text"}


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


# --------------------------------------------------------------------------- the agent (plain code)

def decide(facts: list[dict], q: dict) -> tuple[str, list[dict], dict | None]:
    """The support agent. Reads every rule on the question's topic (that is its receipt), keeps the active ones,
    prefers the one that names the question's category over `all`, and answers from the rule's value.

    `facts` are memory records: {id, fact, metadata, updated_at}. Returns (answer, receipt, applied record)."""
    read = [f for f in facts if (f.get("metadata") or {}).get("topic") == q["topic"]
            and (f.get("metadata") or {}).get("kind") == "rule"]
    receipt = [dict(id=f["id"], key=f["metadata"].get("key"), text=f.get("fact"), metadata=f.get("metadata"),
                    updated_at=f.get("updated_at")) for f in read]
    active = [f for f in read if f["metadata"].get("status") == "active"
              and f["metadata"].get("applies_to") in (q["category"], "all")]
    if not active:
        return "No record", receipt, None
    active.sort(key=lambda f: f["metadata"].get("applies_to") != q["category"])   # the specific rule first
    rule = active[0]
    m = rule["metadata"]
    op, x = m.get("op"), q["x"]
    if op == "never":
        ans = "No"
    elif op == "always":
        ans = "Yes"
    elif op == "max":
        ans = "Yes" if x <= m.get("value") else "No"
    elif op == "min":
        ans = "Yes" if x >= m.get("value") else "No"
    else:
        ans = "No record"
    return ans, receipt, rule


def check_story() -> None:
    """The rules on the story itself, before any call is made: a wrong rule would misread every run."""
    d = load_data()
    keys = [r["key"] for r in d["rules"]]
    if len(set(keys)) != len(keys) or len(d["questions"]) != 6:
        die("story self-check failed: duplicate rule keys or wrong question count")
    for c in d["changes"]:
        if c["do"] != "add" and c["key"] not in keys:
            die(f"story self-check failed: change on unknown rule {c['key']}")
    # The story's own answers, from the data alone: three questions must change, and each for one reason.
    before, after = local_state(d, apply=False), local_state(d, apply=True)
    changed = [q["id"] for q in d["questions"] if decide(before, q)[0] != decide(after, q)[0]]
    if changed != ["Q1", "Q4", "Q6"]:
        die(f"story self-check failed: changed answers {changed}")


def local_state(d: dict, apply: bool) -> list[dict]:
    """The memory as the story describes it (before / after the week), as records; ids are the rule keys."""
    recs = {r["key"]: dict(id=r["key"], fact=r["text"], metadata=meta_of(r)) for r in d["rules"]}
    if apply:
        for c in d["changes"]:
            k = c["key"]
            if c["do"] == "add":
                recs[k] = dict(id=k, fact=c["rule"]["text"], metadata=meta_of(c["rule"]))
            elif c["do"] == "delete":
                recs.pop(k)
            else:
                if "text" in c:
                    recs[k] = dict(recs[k], fact=c["text"])
                if "meta" in c:
                    recs[k] = dict(recs[k], metadata={**recs[k]["metadata"], **c["meta"]})
    return list(recs.values())


# --------------------------------------------------------------------------- step 2: the policies

def leftovers(cli: CLI) -> bool:
    return bool(find_project(cli))


def write_rule(cli: CLI, project: str, rule: dict, echo: bool = False) -> str:
    """How the app writes a policy: the text, then the metadata the agent reads."""
    r = cli.run("fact", "add", rule["text"], "--project", project, scoped=True, echo=echo) or {}
    fid = ((r.get("facts") or [{}])[0]).get("id")
    if not fid:
        die(f"`fact add` returned no id for {rule['text']!r}")
    cli.run("fact", "update", fid, "--metadata", json.dumps(meta_of(rule)), "--project", project, scoped=True, echo=echo)
    return fid


def setup(cli: CLI, d: dict) -> tuple[str, dict]:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    for name in ("world", "eval1", "eval2", "report"):
        if (OUT / f"{name}.json").exists():
            (OUT / f"{name}.json").unlink()
    project = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("policies"),
                      scoped=True, echo=True)["id"]
    emit("world", "", project=project)
    ids = {}

    def one(rule):
        ids[rule["key"]] = write_rule(cli, project, rule, echo=rule is d["rules"][0])
    one(d["rules"][0])
    pmap(one, d["rules"][1:])
    emit("text", "\n    key          topic             status   text")
    for r in d["rules"]:
        emit("text", f"    {r['key']:<12} {r['topic']:<17} {r['status']:<8} {r['text']}")
    note(f"{len(d['rules'])} policies: text + metadata {{key, topic, kind, status, applies_to, op, value}}; "
         "the agent's code reads the metadata")
    emit("setup", "", project=project, rules=d["rules"])
    save("world", dict(project=project, ids=ids))
    return project, ids


# --------------------------------------------------------------------------- steps 3 and 5: an eval

def run_eval(cli: CLI, d: dict, project: str, n: int) -> dict:
    """Run the six questions on today's memory. Saves the answers, a receipt per answer and the full snapshot."""
    facts = project_facts(cli, project, echo=True)
    rows = []
    emit("text", f"\n    q    answer      rule applied   question")
    for q in d["questions"]:
        ans, receipt, rule = decide(facts, q)
        key = (rule or {}).get("metadata", {}).get("key") if rule else None
        rows.append(dict(id=q["id"], ask=q["ask"], answer=ans, applied=key, receipt=receipt))
        emit("text", f"    {q['id']}   {ans:<11} {key or '—':<14} {q['ask']}")
        emit("asked", "", eval=n, qid=q["id"], ask=q["ask"], answer=ans, applied=key,
             read=[r["key"] for r in receipt])
    # The newest server time in this snapshot: every later write is "after the eval" on the same clock.
    newest = max((f.get("updated_at") or f.get("created_at") or "") for f in facts)
    snap = dict(at=newest, answers=rows, snapshot=[dict(id=f["id"], text=f.get("fact"), metadata=f.get("metadata") or {},
                                                        updated_at=f.get("updated_at")) for f in facts])
    save(f"eval{n}", snap)
    note(f"eval {n}: answers, one receipt per answer (the facts it read, as read) and the full snapshot "
         f"saved to out/eval{n}.json; newest write on the server clock {hhmmss(newest)}")
    emit("eval", "", n=n, at=newest, answers=[{k: v for k, v in r.items() if k != "receipt"} for r in rows])
    return snap


# --------------------------------------------------------------------------- step 4: a week of memory changes

def week(cli: CLI, d: dict, project: str, ids: dict) -> list[dict]:
    time.sleep(GAP)
    snap = {x["id"]: x for x in load("eval1")["snapshot"]}
    log = []
    for i, c in enumerate(d["changes"]):
        k, echo = c["key"], True
        if c["do"] == "add":
            ids[k] = write_rule(cli, project, c["rule"], echo=echo)
            kind = "add"
        elif c["do"] == "delete":
            delete_fact(cli, ids[k], ["--project", project], echo=echo)
            kind = "delete"
        else:
            cur = snap[ids[k]]
            args = ["fact", "update", ids[k]]
            if "text" in c:
                args += ["--text", c["text"]]
            if "meta" in c:
                args += ["--metadata", json.dumps({**cur["metadata"], **c["meta"]})]
            cli.run(*args, "--project", project, scoped=True, echo=echo)
            kind = {"text": "text", "meta": "metadata only", "text+meta": "text + metadata"}[c["do"]]
        log.append(dict(key=k, kind=kind, why=c["why"]))
        emit("progress", f"    {i + 1:>2}. {k:<11} {kind:<16} {c['why']}", phase="change", key=k, change=kind, why=c["why"])
    save("world", dict(load("world"), ids=ids))
    note(f"{len(log)} changes: 4 text edits (one with metadata), 2 new rules, 1 deletion, 4 metadata-only updates")
    emit("week", "", changes=log)
    return log


# --------------------------------------------------------------------------- step 6: behavior diff

def behavior(d: dict, e1: dict, e2: dict) -> list[str]:
    a1 = {r["id"]: r for r in e1["answers"]}
    a2 = {r["id"]: r for r in e2["answers"]}
    emit("text", "\n    q    eval 1      eval 2      question")
    changed = []
    for q in d["questions"]:
        x, y = a1[q["id"]]["answer"], a2[q["id"]]["answer"]
        flag = "  ← changed" if x != y else ""
        emit("text", f"    {q['id']}   {x:<11} {y:<11} {q['ask']}{flag}")
        if x != y:
            changed.append(q["id"])
    note(f"{len(changed)} of {len(d['questions'])} answers changed: {', '.join(changed)}. Same code, same questions: "
         "the difference is in memory")
    emit("behavior", "", changed=changed, rows=[dict(id=q["id"], ask=q["ask"], before=a1[q["id"]]["answer"],
                                                   after=a2[q["id"]]["answer"]) for q in d["questions"]])
    return changed


# --------------------------------------------------------------------------- step 7: three diffs

def replay(records: list[dict], q: dict) -> str:
    return decide(records, q)[0]


def put_back(today: list[dict], before: dict, fid: str, how: str = "full") -> list[dict]:
    """Today's memory with one fact put back to its eval-1 version (or removed, if eval 1 did not have it).
    how = "text": only the text can be put back (all a trace entry carries)."""
    out = [dict(f) for f in today if f["id"] != fid]
    b = before.get(fid)
    if b:
        if how == "text":
            cur = next((f for f in today if f["id"] == fid), None)
            out.append(dict(id=fid, fact=b["text"], metadata=(cur or {}).get("metadata") or b["metadata"]))
        else:
            out.append(dict(id=fid, fact=b["text"], metadata=b["metadata"], updated_at=b["updated_at"]))
    return out


def records_of(snapshot: list[dict]) -> list[dict]:
    return [dict(id=s["id"], fact=s["text"], metadata=s["metadata"], updated_at=s["updated_at"]) for s in snapshot]


def diffs(cli: CLI, d: dict, project: str, ids: dict, changed: list[str]) -> dict:
    e1, e2 = load("eval1"), load("eval2")
    t1 = parse_ts(e1["at"])
    before = {s["id"]: s for s in e1["snapshot"]}
    today = records_of(e2["snapshot"])
    now = {s["id"]: s for s in e2["snapshot"]}
    key_of = {v: k for k, v in ids.items()}
    topic_of = {fid: (now.get(fid) or before.get(fid) or {}).get("metadata", {}).get("topic") for fid in key_of}
    qs = {q["id"]: q for q in d["questions"]}
    a1 = {r["id"]: r for r in e1["answers"]}
    a2 = {r["id"]: r for r in e2["answers"]}

    # Ground truth, from the two snapshots: the facts that alone, put back, give the eval-1 answer.
    every = sorted(set(before) | set(now))
    cause = {qid: [key_of[f] for f in every if replay(put_back(today, before, f), qs[qid]) == a1[qid]["answer"]
                   and (before.get(f) or {}) != (now.get(f) or {})] for qid in changed}

    # A — the time window in `fact trace`: every id we can name (today's list + the ids eval 1 logged).
    catalog = sorted(set(now) | {r["id"] for a in e1["answers"] for r in a["receipt"]} | set(before))
    window = []
    for i, fid in enumerate(catalog):
        _, entries = trace_of(cli, fid, project, echo=i == 0)
        for e in entries:
            if parse_ts(e.get("timestamp")) > t1:
                window.append(dict(id=fid, key=key_of.get(fid), event=e.get("event"), at=e.get("timestamp"),
                                   old=e.get("old_fact"), new=e.get("new_fact"), topic=topic_of.get(fid)))
    # Guard: every text-level change of the week (and only those) is in the window, on the server's clock.
    expect = {c["key"] for c in d["changes"] if c["do"] in ("text", "text+meta", "add", "delete")}
    if {w["key"] for w in window} != expect:
        die(f"the trace window holds {sorted({w['key'] for w in window})}, expected {sorted(expect)}; "
            "the server clock and eval 1's snapshot disagree")
    order = {k: i for i, k in enumerate([r["key"] for r in d["rules"]] + [c["key"] for c in d["changes"] if c["do"] == "add"])}
    window.sort(key=lambda w: (w["at"], order.get(w["key"], 99)))
    emit("text", f"\n  A · fact trace, entries after eval 1 ({hhmmss(e1['at'])}): {len(window)}")
    for w in window:
        emit("text", f"    {hhmmss(w['at'])}  {w['event']:<7} {w['key']:<11} {w['topic'] or '?':<17} {w['new'] or w['old']}")

    # B — two full snapshots.
    snapdiff = []
    for fid in every:
        b, n = before.get(fid), now.get(fid)
        if b == n:
            continue
        what = []
        if not b:
            what.append("added")
        elif not n:
            what.append("deleted")
        else:
            if b["text"] != n["text"]:
                what.append("text")
            mk = sorted(k for k in set(b["metadata"]) | set(n["metadata"]) if b["metadata"].get(k) != n["metadata"].get(k))
            if mk:
                what.append("metadata " + ", ".join(f"{k}: {b['metadata'].get(k)} → {n['metadata'].get(k)}" for k in mk))
            if not what:
                what.append("updated_at only")
        snapdiff.append(dict(id=fid, key=key_of.get(fid), topic=topic_of.get(fid), what="; ".join(what)))
    snapdiff.sort(key=lambda s: order.get(s["key"], 99))
    emit("text", f"\n  B · snapshot diff, eval 1 vs eval 2: {len(snapdiff)} facts changed")
    for s in snapdiff:
        emit("text", f"    {s['key']:<11} {s['topic']:<17} {s['what']}")
    traced = {w["id"] for w in window}
    hidden = [s for s in snapdiff if s["id"] not in traced]
    note(f"{len(hidden)} of these {len(snapdiff)} have no entry in `fact trace`: "
         + ", ".join(s["key"] for s in hidden) + " (metadata-only updates; only updated_at moved)")

    def by_story(fid):
        return order.get(key_of.get(fid), 99)

    # The three attributions, per changed answer.
    res = {}
    for qid in changed:
        q = qs[qid]
        # A: the trace entries on the question's topic. Its replay can only put back text (a trace entry has no metadata).
        a_named = sorted({w["id"] for w in window if w["topic"] == q["topic"]}, key=by_story)
        a_rec = today
        for f in a_named:
            a_rec = put_back(a_rec, before, f, "text")
        a_back = replay(a_rec, q)
        # B: the snapshot changes on the question's topic.
        b_named = sorted({s["id"] for s in snapdiff if s["topic"] == q["topic"]}, key=by_story)
        b_rec = today
        for f in b_named:
            b_rec = put_back(b_rec, before, f)
        b_back = replay(b_rec, q)
        # C: the facts in the two receipts that differ, then one replay per fact.
        r1 = {r["id"]: r for r in a1[qid]["receipt"]}
        r2 = {r["id"]: r for r in a2[qid]["receipt"]}
        moved = sorted((f for f in set(r1) | set(r2) if r1.get(f) != r2.get(f)), key=by_story)
        c_tested = [dict(id=f, key=key_of.get(f), restores=replay(put_back(today, before, f), q) == a1[qid]["answer"])
                    for f in moved]
        c_named = [x["id"] for x in c_tested if x["restores"]]
        truth = set(cause[qid])
        named = dict(A=a_named, B=b_named, C=c_named)
        back = dict(A=a_back, B=b_back, C=a1[qid]["answer"] if c_named else a2[qid]["answer"])
        res[qid] = dict(ask=q["ask"], before=a1[qid]["answer"], after=a2[qid]["answer"], cause=sorted(truth),
                        receipt_moved=[key_of.get(f) for f in moved], tested=c_tested,
                        methods={m: dict(named=[key_of.get(f) for f in named[m]], hit=truth <= {key_of.get(f) for f in named[m]},
                                         exact={key_of.get(f) for f in named[m]} == truth,
                                         replay=back[m], restores=back[m] == a1[qid]["answer"]) for m in METHODS})
    for qid, r in res.items():
        emit("text", f"\n  {qid}  {r['before']} → {r['after']}   {r['ask']}")
        for m in METHODS:
            x = r["methods"][m]
            verdict = "✓" if x["exact"] and x["restores"] else ("◐" if x["hit"] else "✗")
            names_ = ", ".join(x["named"]) or "nothing"
            extra = ""
            if m == "A" and x["exact"] and not x["restores"]:
                extra = "  (right fact; the trace has its old text, not its old metadata, so the replay cannot put it back)"
            emit("text", f"    {d['methods'][m]['short']:<18} names {names_:<34} replay with them put back: {x['replay']:<10} {verdict}{extra}")
        if r["tested"]:
            emit("text", "      C replays one fact at a time: " + ", ".join(
                f"{t['key']} {'→ ' + r['before'] + ' ✓' if t['restores'] else '→ no change'}" for t in r["tested"]))
        emit("attribution", "", qid=qid, **r)
    note("✓ named exactly the cause and the replay gives back the eval-1 answer · ◐ the cause is among the named, "
         "or named but the replay cannot put it back · ✗ the cause is not named")
    unchanged = [q["id"] for q in d["questions"] if q["id"] not in changed]
    noise = dict(A={qid: sorted({w["key"] for w in window if w["topic"] == qs[qid]["topic"]}) for qid in unchanged},
                 B={qid: sorted({s["key"] for s in snapdiff if s["topic"] == qs[qid]["topic"]}) for qid in unchanged})
    for m in ("A", "B"):
        hits = {k: v for k, v in noise[m].items() if v}
        if hits:
            note(f"{m}: memory under {', '.join(hits)} changed too ({'; '.join(f'{k}: ' + ', '.join(v) for k, v in hits.items())}), "
                 "and those answers did not")
    return dict(window=window, snapdiff=snapdiff, hidden=[s["key"] for s in hidden], res=res, noise=noise)


def score(d: dict, x: dict) -> dict:
    emit("text", "\n  Score — three changed answers\n")
    emit("text", "    method               changes listed   named the cause (exactly)   replay gives back eval 1")
    rows = {}
    for m in METHODS:
        listed = {"A": len(x["window"]), "B": len(x["snapdiff"]),
                  "C": sum(len(r["receipt_moved"]) for r in x["res"].values())}[m]
        n = len(x["res"])
        hit = sum(r["methods"][m]["hit"] for r in x["res"].values())
        exact = sum(r["methods"][m]["exact"] for r in x["res"].values())
        back = sum(r["methods"][m]["restores"] for r in x["res"].values())
        rows[m] = dict(listed=listed, hit=hit, exact=exact, restores=back, of=n)
        emit("text", f"    {d['methods'][m]['short']:<20} {listed:<16} {f'{hit}/{n} ({exact}/{n})':<27} {back}/{n}")
    note("C's count is the changed facts inside the receipts of the three changed answers; the replay keeps the ones "
         "that, put back alone, give the eval-1 answer")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, rows=rows, hidden=x["hidden"],
                  window=x["window"], snapdiff=x["snapdiff"], res=x["res"], noise=x["noise"])
    save("report", report)
    emit("score", "", **{k: v for k, v in report.items() if k not in ("window",)})
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    check_story()
    banner(2, TOTAL, "The policies — 10 rules in project memory")
    project, ids = setup(cli, d)
    banner(3, TOTAL, "Eval 1 — six questions, each answer with a receipt")
    run_eval(cli, d, project, 1)
    banner(4, TOTAL, "A week of memory changes — 11 ordinary writes")
    week(cli, d, project, ids)
    banner(5, TOTAL, "Eval 2 — the same six questions")
    run_eval(cli, d, project, 2)
    banner(6, TOTAL, "Behavior diff — which answers changed?")
    changed = behavior(d, load("eval1"), load("eval2"))
    banner(7, TOTAL, "Three memory diffs — which change caused which answer?")
    x = diffs(cli, d, project, ids, changed)
    return score(d, x)


# --------------------------------------------------------------------------- read-only subcommands

def show_receipt(cli: CLI, qid: str | None = None) -> None:
    d = load_data()
    qid = (qid or "Q1").upper()
    q = next((x for x in d["questions"] if x["id"] == qid), None)
    if not q:
        die(f"unknown question {qid!r}; one of: {', '.join(x['id'] for x in d['questions'])}")
    p = find_project(cli, echo=True)
    if not p:
        die("the demo project does not exist yet; run `python3 demo.py` first")
    e1, e2 = load("eval1"), load("eval2")
    a1 = next(r for r in e1["answers"] if r["id"] == qid)
    a2 = next(r for r in e2["answers"] if r["id"] == qid)
    emit("text", f"\n  {qid}  {q['ask']}\n  eval 1: {a1['answer']} (applied {a1['applied'] or '—'})   "
                 f"eval 2: {a2['answer']} (applied {a2['applied'] or '—'})")
    r1 = {r["id"]: r for r in a1["receipt"]}
    r2 = {r["id"]: r for r in a2["receipt"]}
    for fid in sorted(set(r1) | set(r2)):
        x, y = r1.get(fid), r2.get(fid)
        k = (x or y)["key"]
        emit("text", f"\n  {k}  {'(same)' if x == y else '(changed)'}")
        for label, r in (("eval 1", x), ("eval 2", y)):
            if not r:
                emit("text", f"    {label}: —")
                continue
            emit("text", f"    {label}: {r['text']}")
            emit("text", f"            metadata {json.dumps(r['metadata'])}   updated_at {hhmmss(r['updated_at'])}")
        _, entries = trace_of(cli, fid, p["id"], echo=True)
        emit("text", "    fact trace: " + "; ".join(f"{e.get('event')} {hhmmss(e.get('timestamp'))}" for e in entries))


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    nfacts, nproj = 0, 0
    p = find_project(cli, echo=not quiet)
    if p:
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
        note(f"deleted {nfacts} facts and {nproj} project" if nproj else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "eval1", "eval2", "world"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "receipt", "cleanup"],
                    help="run = full demo (default); receipt [Q1…Q6] = one question's two receipts, side by side, "
                         "with the trace of each fact it read; cleanup = delete everything")
    ap.add_argument("question", nargs="?", help="for `receipt`: Q1 … Q6")
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
    if args.command == "receipt":
        banner(2, 2, "Receipt — one question's two answers and the memory each one read")
        show_receipt(cli, args.question)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py receipt Q1`; `python3 demo.py cleanup` removes the demo data.")


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

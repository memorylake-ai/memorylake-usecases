#!/usr/bin/env python3
"""snapshot-fixture-memory — pin the memory an agent's tests read: pinning the fact ids pins nothing.

Tollquay Freight (fictional) runs a quote agent on project memory: one fact per shipping rule, a text people read
and metadata the agent's code reads ({key, topic, kind, status, applies_to, op, value}). The agent is plain code,
and eight tests guard it. The tests need memory, so the team pins a fixture. Three ways, side by side:

  A  no fixture        — the tests read live memory (`fact list`) at test time
  B  pin the fact ids  — the fixture keeps 9 fact ids; the tests load each with `fact get`;
                         the fixture check is: every pinned id still resolves
  C  pin the content   — the fixture keeps every fact as read (id, text, metadata, updated_at, sha256);
                         the tests read only the file; a drift check compares it with live memory, without
                         failing the build

CI #1 passes everywhere. Then teammates do their jobs: five ordinary writes to the rules, no code change. CI #2
runs the same code: A and B go red while B's fixture check says every pinned id resolves — `fact update` keeps the
id, so an id is a name, not a pin. CI #3 ships a real bug (a refactor that refuses a value exactly at a limit):
C reds exactly the two boundary tests; on live memory one of them no longer tests the boundary at all, and another
test turns green because the bug cancels a memory change.

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

PREFIX = "mlu-snp"  # memorylake-usecases / snapshot-fixture-memory


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
STRATS = ("A", "B", "C")


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("rules"), "--by-custom-id", scoped=True, echo=echo)


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


def pmap(fn, items, workers: int = 4) -> list:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, items))


# --------------------------------------------------------------------------- data

def load_data() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def hhmmss(iso: str) -> str:
    return (iso or "")[11:19]


def meta_of(rule: dict) -> dict:
    """What the agent's code reads: everything in the rule except its text."""
    return {k: v for k, v in rule.items() if k != "text"}


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


def digest(text: str, metadata: dict) -> str:
    """The content a test depends on, as one hash: the text and the metadata, canonical JSON."""
    blob = json.dumps({"text": text, "metadata": metadata}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def record(f: dict) -> dict:
    """A memory record as the tests use it, from a `fact list` item or a `fact get` reply."""
    return dict(id=f.get("id"), fact=f.get("fact") if isinstance(f.get("fact"), str) else f.get("text"),
                metadata=f.get("metadata") or {}, updated_at=f.get("updated_at"))


# --------------------------------------------------------------------------- the agent (plain code), two versions

def decide(facts: list[dict], q: dict, version: str = "v1") -> tuple[str, str | None]:
    """The quote agent. Reads the rules on the question's topic, keeps the active ones that apply to the
    question's category (or to `all`), prefers the specific one, and answers from the rule's op and value.

    v2 is a refactor with one bug: a `max` rule compares with < instead of <=, so a value exactly at the
    limit is refused. Returns (answer, key of the rule applied)."""
    active = [f for f in facts if (f.get("metadata") or {}).get("topic") == q["topic"]
              and f["metadata"].get("kind") == "rule" and f["metadata"].get("status") == "active"
              and f["metadata"].get("applies_to") in (q["category"], "all")]
    if not active:
        return "No record", None
    active.sort(key=lambda f: f["metadata"].get("applies_to") != q["category"])   # the specific rule first
    m = active[0]["metadata"]
    op, x, v = m.get("op"), q["x"], m.get("value")
    if op == "never":
        ans = "No"
    elif op == "always":
        ans = "Yes"
    elif op == "max":
        ans = "Yes" if (x <= v if version == "v1" else x < v) else "No"
    elif op == "min":
        ans = "Yes" if x >= v else "No"
    else:
        ans = "No record"
    return ans, m.get("key")


def run_tests(records: list[dict], d: dict, version: str) -> dict:
    out = {}
    for t in d["tests"]:
        ans, key = decide(records, t, version)
        out[t["id"]] = dict(answer=ans, rule=key, ok=ans == t["expect"])
    return out


# --------------------------------------------------------------------------- the story's own expectations

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


def views(d: dict, apply: bool) -> dict:
    """What each strategy's tests read: A all of today's memory, B today's content of the pinned ids, C the pin."""
    pinned = {r["key"] for r in d["rules"]}
    now = local_state(d, apply)
    return dict(A=now, B=[r for r in now if r["id"] in pinned], C=local_state(d, False))


def reds(results: dict) -> list[str]:
    return [tid for tid, r in results.items() if not r["ok"]]


def expected(d: dict) -> dict:
    """Red tests per strategy and CI run, from the story data alone (no calls)."""
    v1, v2 = views(d, False), views(d, True)
    return {"ci1": {s: reds(run_tests(v1[s], d, "v1")) for s in STRATS},
            "ci2": {s: reds(run_tests(v2[s], d, "v1")) for s in STRATS},
            "ci3": {s: reds(run_tests(v2[s], d, "v2")) for s in STRATS}}


def check_story() -> dict:
    """The rules on the story itself, before any call is made: a wrong story would misread every run."""
    d = load_data()
    keys = [r["key"] for r in d["rules"]]
    if len(set(keys)) != len(keys) or len(d["tests"]) != 8:
        die("story self-check failed: duplicate rule keys or wrong test count")
    if any(decide(local_state(d, False), t)[0] != t["expect"] for t in d["tests"]):
        die("story self-check failed: a test's expected answer does not hold on the pinned memory")
    e = expected(d)
    want = {"ci1": dict(A=[], B=[], C=[]),
            "ci2": dict(A=["T1", "T6", "T7", "T8"], B=["T1", "T6", "T8"], C=[]),
            "ci3": dict(A=["T1", "T5", "T7", "T8"], B=["T1", "T5", "T8"], C=["T1", "T5"])}
    if e != want:
        die(f"story self-check failed: expected reds {e}")
    return e


# --------------------------------------------------------------------------- step 2: the rules

def leftovers(cli: CLI) -> bool:
    return bool(find_project(cli))


def write_rule(cli: CLI, project: str, rule: dict, echo: bool = False) -> str:
    """How the app writes a rule: the text, then the metadata the agent reads."""
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
    for name in ("world", "ci1", "ci2", "ci3", "report", "fixtures/ids", "fixtures/snapshot"):
        if (OUT / f"{name}.json").exists():
            (OUT / f"{name}.json").unlink()
    project = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("rules"),
                      scoped=True, echo=True)["id"]
    emit("world", "", project=project)
    ids = {}

    def one(rule):
        ids[rule["key"]] = write_rule(cli, project, rule, echo=rule is d["rules"][0])
    one(d["rules"][0])
    pmap(one, d["rules"][1:])
    emit("text", "\n    key            topic            applies to   status   text")
    for r in d["rules"]:
        emit("text", f"    {r['key']:<14} {r['topic']:<16} {r['applies_to']:<12} {r['status']:<8} {r['text']}")
    note(f"{len(d['rules'])} rules: text + metadata {{key, topic, kind, status, applies_to, op, value}}; "
         "the agent's code reads the metadata")
    emit("setup", "", project=project, rules=d["rules"])
    save("world", dict(project=project, ids=ids))
    return project, ids


# --------------------------------------------------------------------------- step 3: pin the fixtures

def pin(cli: CLI, d: dict, project: str, ids: dict) -> None:
    facts = [record(f) for f in project_facts(cli, project, echo=True)]
    key_of = {v: k for k, v in ids.items()}
    order = {r["key"]: i for i, r in enumerate(d["rules"])}
    facts.sort(key=lambda f: order.get(key_of.get(f["id"]), 99))
    save("fixtures/ids", dict(pinned_at=datetime.now(timezone.utc).isoformat(),
                              ids=[dict(key=key_of.get(f["id"]), id=f["id"]) for f in facts]))
    save("fixtures/snapshot", dict(pinned_at=datetime.now(timezone.utc).isoformat(),
                                   facts=[dict(key=key_of.get(f["id"]), id=f["id"], text=f["fact"], metadata=f["metadata"],
                                               updated_at=f["updated_at"], sha256=digest(f["fact"], f["metadata"]))
                                          for f in facts]))
    emit("text", "\n    key            fact id                                  sha256 (text + metadata)")
    for f in facts:
        emit("text", f"    {key_of.get(f['id']):<14} {f['id']:<40} {digest(f['fact'], f['metadata'])[:16]}…")
    note(f"B pinned {len(facts)} fact ids → out/fixtures/ids.json")
    note(f"C pinned {len(facts)} facts as read (id, text, metadata, updated_at, sha256) → out/fixtures/snapshot.json")
    emit("pinned", "", facts=[dict(key=key_of.get(f["id"]), id=f["id"], sha=digest(f["fact"], f["metadata"])[:16])
                              for f in facts])


# --------------------------------------------------------------------------- a CI run

def load_B(cli: CLI, project: str) -> tuple[list[dict], list[dict]]:
    """B: load every pinned id with `fact get`. Returns (records, the fixture check rows)."""
    pins = load("fixtures/ids")["ids"]

    def one(i_p):
        i, p = i_p
        g = cli.try_run("fact", "get", p["id"], "--project", project, scoped=True, echo=i == 0)
        if g is None:
            return p, None
        return p, record(g.get("fact") if isinstance(g.get("fact"), dict) else g)
    got = pmap(one, list(enumerate(pins)))
    checks = [dict(key=p["key"], id=p["id"], resolves=r is not None, live_id=(r or {}).get("id")) for p, r in got]
    return [r for _, r in got if r], checks


def load_C() -> tuple[list[dict], list[dict]]:
    """C: the pinned file, and its own check: every record's hash still matches its content."""
    snap = load("fixtures/snapshot")["facts"]
    checks = [dict(key=s["key"], ok=digest(s["text"], s["metadata"]) == s["sha256"]) for s in snap]
    return [dict(id=s["id"], fact=s["text"], metadata=s["metadata"], updated_at=s["updated_at"]) for s in snap], checks


def drift(live: list[dict], key_of: dict) -> list[dict]:
    """C's drift check: the pin against live memory, fact by fact. Informational; it does not fail the build."""
    snap = {s["id"]: s for s in load("fixtures/snapshot")["facts"]}
    now = {f["id"]: f for f in live}
    rows = []
    for fid, s in snap.items():
        n = now.get(fid)
        if not n:
            rows.append(dict(key=s["key"], id=fid, what="deleted since the pin"))
            continue
        if digest(n["fact"], n["metadata"]) == s["sha256"]:
            continue
        what = []
        if n["fact"] != s["text"]:
            what.append("text")
        mk = sorted(k for k in set(s["metadata"]) | set(n["metadata"]) if s["metadata"].get(k) != n["metadata"].get(k))
        if mk:
            what.append("metadata " + ", ".join(f"{k}: {s['metadata'].get(k)} → {n['metadata'].get(k)}" for k in mk))
        rows.append(dict(key=s["key"], id=fid, what="; ".join(what)))
    for fid, n in now.items():
        if fid not in snap:
            rows.append(dict(key=key_of.get(fid) or (n["metadata"] or {}).get("key"), id=fid, what="new, not in the pin"))
    return rows


def ci(cli: CLI, d: dict, project: str, ids: dict, n: int, version: str) -> dict:
    """One CI run of the 8 tests under the three fixtures. CI #3 also runs v1 on the same inputs (triage)."""
    key_of = {v: k for k, v in ids.items()}
    live = [record(f) for f in project_facts(cli, project, echo=True)]
    b_recs, b_checks = load_B(cli, project)
    c_recs, c_checks = load_C()
    inputs = dict(A=live, B=b_recs, C=c_recs)
    res = {s: run_tests(inputs[s], d, version) for s in STRATS}
    base = {s: run_tests(inputs[s], d, "v1") for s in STRATS} if version != "v1" else None

    emit("text", f"\n    agent {version}   expect      {'A live':<12} {'B pinned ids':<14} {'C pinned content':<16} test")
    for t in d["tests"]:
        cells = []
        for s in STRATS:
            r = res[s][t["id"]]
            cells.append(f"{'✓' if r['ok'] else '✗'} {r['answer']:<10}")
        emit("text", f"    {t['id']:<10} {t['expect']:<11} {cells[0]:<12} {cells[1]:<14} {cells[2]:<16} {t['ask']}")
    summary = {s: dict(passed=sum(r["ok"] for r in res[s].values()), reds=reds(res[s])) for s in STRATS}
    emit("text", "    " + "   ".join(f"{d['strategies'][s]['short']}: {summary[s]['passed']}/8 pass" for s in STRATS))

    resolved = sum(c["resolves"] for c in b_checks)
    same_ids = sum(c["resolves"] and c["live_id"] == c["id"] for c in b_checks)
    b_ok = resolved == len(b_checks)
    emit("text", f"\n    B fixture check: {resolved}/{len(b_checks)} pinned ids resolve {'✓' if b_ok else '✗'}  "
                 f"({same_ids} come back with the same id)")
    c_ok = all(c["ok"] for c in c_checks)
    emit("text", f"    C fixture check: {sum(c['ok'] for c in c_checks)}/{len(c_checks)} records match their sha256 "
                 f"{'✓' if c_ok else '✗'}")
    dr = drift(live, key_of)
    if dr:
        emit("text", f"    C drift check (does not fail the build): live memory has moved {len(dr)} way(s) since the pin")
        for r in dr:
            emit("text", f"      {r['key']:<14} {r['what']}")
    else:
        emit("text", "    C drift check: live memory matches the pin")

    # The id that B pinned for each red test's rule: same id, other content.
    pin_text = {s["id"]: s for s in load("fixtures/snapshot")["facts"]}
    shown, samples = [], []
    for tid in summary["B"]["reds"]:
        rule = next((r for r in b_recs if r["id"] in pin_text and r["metadata"].get("topic") ==
                     next(t for t in d["tests"] if t["id"] == tid)["topic"] and r["fact"] != pin_text[r["id"]]["text"]), None)
        if rule and rule["id"] not in shown and n == 2:
            shown.append(rule["id"])
            samples.append(dict(tid=tid, key=key_of.get(rule["id"]), id=rule["id"], pinned=pin_text[rule["id"]]["text"],
                                today=rule["fact"]))
            emit("text", f"\n    {tid} under B read {key_of.get(rule['id'])} by its pinned id {rule['id']}")
            emit("text", f"      at the pin: {pin_text[rule['id']]['text']}")
            emit("text", f"      today:      {rule['fact']}")

    triage = None
    if base:
        triage = {}
        emit("text", f"\n    triage: run v1 on the same inputs, same moment — a test whose result differs is the code change")
        for s in STRATS:
            diff = [tid for tid in res[s] if res[s][tid]["answer"] != base[s][tid]["answer"]]
            broke = [tid for tid in diff if not res[s][tid]["ok"]]
            greened = [tid for tid in diff if res[s][tid]["ok"]]
            unexplained = [tid for tid in summary[s]["reds"] if tid not in diff]
            triage[s] = dict(changed_by_code=diff, broke=broke, greened=greened, unexplained=unexplained)
            emit("text", f"    {d['strategies'][s]['short']:<18} red {len(summary[s]['reds'])}: "
                         f"{', '.join(summary[s]['reds']) or '—':<16} code broke {', '.join(broke) or '—':<9} "
                         f"code turned green {', '.join(greened) or '—':<6} red without the code {', '.join(unexplained) or '—'}")
    out = dict(n=n, version=version, results=res, summary=summary, b_checks=b_checks, b_ok=b_ok, c_ok=c_ok,
               drift=dr, triage=triage, base=base)
    save(f"ci{n}", out)
    emit("ci", "", n=n, version=version, summary=summary, b_resolved=resolved, b_total=len(b_checks), b_ok=b_ok,
         c_ok=c_ok, drift=dr, triage=triage, samples=samples,
         rows=[dict(id=t["id"], ask=t["ask"], expect=t["expect"], **{s: res[s][t["id"]] for s in STRATS}) for t in d["tests"]])
    return out


# --------------------------------------------------------------------------- step 4: a week of teammates' writes

def week(cli: CLI, d: dict, project: str, ids: dict) -> list[dict]:
    pins = {s["id"]: s for s in load("fixtures/snapshot")["facts"]}
    log = []
    for i, c in enumerate(d["changes"]):
        k = c["key"]
        if c["do"] == "add":
            ids[k] = write_rule(cli, project, c["rule"], echo=True)
            kind = "new rule"
        elif c["do"] == "delete":
            delete_fact(cli, ids[k], ["--project", project], echo=True)
            kind = "delete"
        else:
            cur = pins[ids[k]]
            args = ["fact", "update", ids[k]]
            if "text" in c:
                args += ["--text", c["text"]]
            if "meta" in c:
                args += ["--metadata", json.dumps({**cur["metadata"], **c["meta"]})]
            cli.run(*args, "--project", project, scoped=True, echo=True)
            kind = {"text": "text", "meta": "metadata only", "text+meta": "text + metadata"}[c["do"]]
        log.append(dict(key=k, kind=kind, who=c["who"], why=c["why"]))
        emit("progress", f"    {i + 1}. {k:<14} {kind:<16} {c['who']:<16} {c['why']}", phase="change", key=k,
             change=kind, who=c["who"], why=c["why"])
    save("world", dict(load("world"), ids=ids))
    note(f"{len(log)} writes by three teammates; nobody touched the agent's code or its tests")
    emit("week", "", changes=log)
    return log


# --------------------------------------------------------------------------- step 7: score

def score(d: dict, exp: dict) -> dict:
    c1, c2, c3 = load("ci1"), load("ci2"), load("ci3")
    got = {f"ci{x['n']}": {s: x["summary"][s]["reds"] for s in STRATS} for x in (c1, c2, c3)}
    if got != exp:
        die(f"the runs disagree with the story's own expectations: got {got}, expected {exp}")
    boundary = [t["id"] for t in d["tests"] if t.get("boundary")]
    rows = {}
    emit("text", "\n    fixture              CI #2 red, code unchanged   fixture check says     "
                 "CI #3 regression tests caught   CI #3 red the code did not cause")
    for s in STRATS:
        t3 = c3["triage"][s]
        check = {"A": "(none)", "B": f"{sum(c['resolves'] for c in c2['b_checks'])}/{len(c2['b_checks'])} ids resolve ✓",
                 "C": f"sha256 ✓, drift {len(c2['drift'])}"}[s]
        caught = [tid for tid in boundary if tid in t3["broke"]]
        rows[s] = dict(flipped=c2["summary"][s]["reds"], check=check, caught=caught, of=len(boundary),
                       greened=t3["greened"], unexplained=t3["unexplained"])
        c_flip = f"{len(rows[s]['flipped'])}/8" + (f" ({', '.join(rows[s]['flipped'])})" if rows[s]["flipped"] else "")
        c_caught = f"{len(caught)}/{len(boundary)} ({', '.join(caught)})"
        c_un = f"{len(t3['unexplained'])}" + (f" ({', '.join(t3['unexplained'])})" if t3["unexplained"] else "")
        emit("text", f"    {d['strategies'][s]['short']:<20} {c_flip:<27} {check:<22} {c_caught:<31} {c_un}")
    note("regression tests = the two boundary tests (T1 at 30 kg, T5 at 14:00); v2 refuses a value exactly at a max limit")
    for s in ("A", "B"):
        if rows[s]["greened"]:
            note(f"{s}: v2 turned {', '.join(rows[s]['greened'])} green — the bug cancelled a memory change, and the "
                 "suite reports it as fixed")
    if "T1" not in rows["A"]["caught"]:
        note("A, B: T1 no longer tests the boundary — live memory moved the limit to 25 kg, so 30 kg is refused by v1 "
             "and v2 alike")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, rows=rows, expected=exp)
    save("report", report)
    emit("score", "", rows=rows)
    return report


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    exp = check_story()
    banner(2, TOTAL, "The rules — 9 rules in project memory")
    project, ids = setup(cli, d)
    banner(3, TOTAL, "Pin the fixtures, CI #1 — agent v1, 8 tests")
    pin(cli, d, project, ids)
    ci(cli, d, project, ids, 1, "v1")
    banner(4, TOTAL, "A week of teammates' writes — 5 changes, no code change")
    week(cli, d, project, ids)
    banner(5, TOTAL, "CI #2 — the same agent v1, the same tests")
    ci(cli, d, project, ids, 2, "v1")
    banner(6, TOTAL, f"CI #3 — agent {d['regression']['version']}: “{d['regression']['commit']}”")
    note(f"the bug: {d['regression']['bug']}")
    ci(cli, d, project, ids, 3, d["regression"]["version"])
    banner(7, TOTAL, "Score — which fixture told the truth?")
    return score(d, exp)


# --------------------------------------------------------------------------- read-only subcommand

def show_drift(cli: CLI) -> None:
    p = find_project(cli, echo=True)
    if not p:
        die("the demo project does not exist yet; run `python3 demo.py` first")
    ids = load("world")["ids"]
    key_of = {v: k for k, v in ids.items()}
    snap = load("fixtures/snapshot")
    live = [record(f) for f in project_facts(cli, p["id"], echo=True)]
    rows = drift(live, key_of)
    pins = {s["id"]: s for s in snap["facts"]}
    now = {f["id"]: f for f in live}
    emit("text", f"\n  pinned at {snap['pinned_at'][:19]}Z: {len(pins)} facts; live now: {len(now)} facts; "
                 f"{len(rows)} difference(s)")
    for r in rows:
        emit("text", f"\n  {r['key']}  {r['id']}  — {r['what']}")
        s, n = pins.get(r["id"]), now.get(r["id"])
        if s:
            emit("text", f"    pinned: {s['text']}\n            metadata {json.dumps(s['metadata'])}")
        if n:
            emit("text", f"    live:   {n['fact']}\n            metadata {json.dumps(n['metadata'])}   updated_at {hhmmss(n['updated_at'])}")
    note("to accept these changes into the fixture, re-pin on purpose (a reviewed commit), not on a red build")


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
    for name in ("report", "ci1", "ci2", "ci3", "world", "fixtures/ids", "fixtures/snapshot"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(parents=True, exist_ok=True)
            f.rename(OUT / "runs" / f"{name.replace('/', '-')}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "drift", "cleanup"],
                    help="run = full demo (default); drift = the pinned fixture against live memory, fact by fact; "
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
    if args.command == "drift":
        banner(2, 2, "Drift — the pinned fixture against live memory")
        show_drift(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py drift`; `python3 demo.py cleanup` removes the demo data.")


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

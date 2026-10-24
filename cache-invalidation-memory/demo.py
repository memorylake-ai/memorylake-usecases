#!/usr/bin/env python3
"""cache-invalidation-memory — cache an agent's memory reads: the project version never sees the extractor's writes.

Harlow Desk (fictional) runs a support agent on project memory. Four questions come in all day (annual refund
window, monthly refunds, export time, support hours), and each answer is a `search` (about 3 s). So the team caches
the answers — three ways, side by side, against a ground truth that searches every time:

  A  TTL 15 min         — keyed by the question's text; an entry is used for 15 minutes
  B  project version    — every read first asks `proj get` for the project's `updated_at` (well under a second);
                          the entry is used while the version is the one it was filled at
  C  fact watermark     — every read first asks `fact list --page-size 1` for the newest fact's `updated_at` and
                          the `total`; the entry is used while both are the same

B looks right: the project's `updated_at` moves on every API write — `fact add`, `fact update` (text or
metadata only), `fact delete`, and a message append. It does not move when MemoryLake's own extractor rewrites a
fact in place from a conversation, about 20 s after the message arrived. The agent answers the next ticket right
after the message, so B refetches (the old value: extraction has not landed yet) and files it under the new
version; then extraction lands, the version stays put, and B serves the old answer as fresh.

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

PREFIX = "mlu-civ"  # memorylake-usecases / cache-invalidation-memory


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
TTL = 15 * 60         # A: seconds an entry is used for
EXTRACT_LIMIT = 240   # seconds to wait for the extractor before calling it deferred


# --------------------------------------------------------------------------- objects

def cid(*parts: str) -> str:
    return "-".join((PREFIX, *parts))


def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("policies"), "--by-custom-id", scoped=True, echo=echo)


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


def hhmmss(iso: str) -> str:
    return (iso or "")[11:19]


def ms(iso: str) -> str:
    return (iso or "")[11:23]


def parse_ts(iso: str) -> datetime:
    # The server trims trailing zeros (".73645"); Python 3.9 only parses 3 or 6 fraction digits.
    iso = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], iso.replace("Z", "+00:00"))
    return datetime.fromisoformat(iso)


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


VALUE_RE = re.compile(r"\b\d{2}:\d{2}-\d{2}:\d{2}\b|\bnet \d+\b|\b\d+[ -](?:day|hour)s?\b", re.I)


def value(text: str | None) -> str:
    """The value an answer gives, for the tables: the first one in the text (a rewrite may keep the old one
    later, in "previously …"). The cache verdicts compare whole texts, never this."""
    if not text:
        return "no record"
    m = VALUE_RE.search(text)
    return m.group(0) if m else text[:24]


# --------------------------------------------------------------------------- the agent's hot path

def answer(cli: CLI, project: str, q: dict, echo: bool = False) -> dict:
    """What the agent answers: the first `search` hit whose text names the question's subject. About 3 s."""
    t0 = time.time()
    r = cli.run("search", q["ask"], "--projects", project, "--types", "fact", "--top-k", "8", scoped=True, echo=echo) or {}
    hit = next((f for f in r.get("facts") or []
                if all(w in (f.get("fact") or f.get("text") or "").lower() for w in q["match"])), None)
    text = (hit.get("fact") or hit.get("text")) if hit else None
    return dict(text=text, id=(hit or {}).get("id"), secs=time.time() - t0)


def version(cli: CLI, arm: str, project: str, echo: bool = False) -> str | None:
    """The validator each cache asks before it uses an entry. A has none."""
    if arm == "B":
        return (cli.run("proj", "get", project, scoped=True, echo=echo) or {}).get("updated_at")
    if arm == "C":
        page = cli.run("fact", "list", "--projects", project, "--page-size", "1", scoped=True, echo=echo) or {}
        top = (page.get("items") or [{}])[0]
        return f"{top.get('updated_at')}|{page.get('total')}"
    return None


def read(cli: CLI, arm: str, q: dict, project: str, cache: dict, echo: bool) -> dict:
    """One cached read. A miss fills the entry with a fresh `search`, under the version read just before it."""
    t0 = time.time()
    v = version(cli, arm, project, echo)
    e = cache.get(q["id"])
    if arm == "A":
        hit = bool(e) and time.time() - e["filled"] < TTL
    else:
        hit = bool(e) and e["version"] == v
    if not hit:
        a = answer(cli, project, q, echo=echo)
        e = cache[q["id"]] = dict(text=a["text"], id=a["id"], version=v, filled=time.time())
    return dict(hit=hit, text=e["text"], id=e["id"], version=v, entry_version=e["version"],
                secs=time.time() - t0, calls=(0 if arm == "A" else 1) + (0 if hit else 1))


def ask_round(cli: CLI, d: dict, project: str, caches: dict, n: int, label: str) -> dict:
    """The four questions, through each cache and straight to `search` (the ground truth), at the same moment."""
    qs = d["questions"]
    jobs = [("live", q) for q in qs] + [(arm, q) for arm in ARMS for q in qs]
    first = {"live": qs[0]["id"], **{arm: qs[0]["id"] for arm in ARMS}}

    def one(job):
        arm, q = job
        echo = q["id"] == first[arm]
        if arm == "live":
            return answer(cli, project, q, echo=echo)
        return read(cli, arm, q, project, caches[arm], echo)
    got = dict(zip([(a, q["id"]) for a, q in jobs], pmap(one, jobs, workers=8)))
    rows = []
    for q in qs:
        live = got[("live", q["id"])]
        row = dict(id=q["id"], ask=q["ask"], short=q["short"], live=live["text"], live_value=value(live["text"]))
        for arm in ARMS:
            r = got[(arm, q["id"])]
            row[arm] = dict(hit=r["hit"], text=r["text"], value=value(r["text"]), stale=r["text"] != live["text"],
                            version=r["version"], secs=round(r["secs"], 2), calls=r["calls"])
        row["live_secs"] = round(live["secs"], 2)
        rows.append(row)
    st = d["arms"]
    emit("text", f"\n    {'question':<22} {'memory says':<13} " + " ".join(f"{st[a]['short']:<22}" for a in ARMS))
    for r in rows:
        cells = []
        for a in ARMS:
            x = r[a]
            cells.append(f"{'✗' if x['stale'] else '✓'} {x['value']:<9} {'hit' if x['hit'] else 'miss':<5}")
        emit("text", f"    {r['short']:<22} {r['live_value']:<13} " + " ".join(f"{c:<22}" for c in cells))
    stale = {a: [r["id"] for r in rows if r[a]["stale"]] for a in ARMS}
    emit("text", "    " + "   ".join(f"{st[a]['short']}: {4 - len(stale[a])}/4 current" for a in ARMS))
    out = dict(n=n, label=label, at=datetime.now(timezone.utc).isoformat(), rows=rows, stale=stale)
    emit("round", "", **out)
    return out


# --------------------------------------------------------------------------- the story's own expectations

def check_story() -> dict:
    """The rules on the story itself, before any call is made: a wrong story would misread every run."""
    d = load_data()
    keys = [p["key"] for p in d["policies"]]
    if len(set(keys)) != len(keys) or len(d["questions"]) != 4:
        die("story self-check failed: duplicate policy keys or wrong question count")
    for q in d["questions"]:
        hits = [p for p in d["policies"] if all(w in p["text"].lower() for w in q["match"])]
        if len(hits) != 1:
            die(f"story self-check failed: {q['id']} matches {len(hits)} policies, not 1")
    if d["admin_edit"]["key"] not in keys or d["retire"]["key"] not in keys or not set(d["watch"]) <= set(keys):
        die("story self-check failed: an edit names an unknown policy")
    for k in d["watch"]:
        p = next(x for x in d["policies"] if x["key"] == k)
        if value(p["text"]) in d["channel"]["text"]:
            die(f"story self-check failed: the channel message repeats {k}'s old value")
    # What the API writes alone must do (the extractor's part is measured, not assumed):
    # after the admin edit only A is behind; after the delete, B and C are current on the retired question.
    return {"r1": dict(A=["Q1"], B=[], C=[]), "r4_retired": dict(A=True, B=False, C=False)}


# --------------------------------------------------------------------------- step 2: memory

def leftovers(cli: CLI) -> bool:
    return bool(find_project(cli) or find_actor(cli, load_data()["billing_lead"]["key"]) or demo_convs(cli))


def setup(cli: CLI, d: dict) -> dict:
    if leftovers(cli):
        note("an earlier run left the demo memory behind; starting over")
        cleanup(cli, quiet=True)
    for f in OUT.glob("*.json"):
        f.unlink()
    project = cli.run("proj", "create", "--name", d["project_name"], "--custom-id", cid("policies"),
                      scoped=True, echo=True)["id"]
    emit("world", "", project=project)
    r = cli.run("fact", "add", *[p["text"] for p in d["policies"]], "--project", project, scoped=True, echo=True) or {}
    facts = r.get("facts") or []
    if len(facts) != len(d["policies"]):
        die(f"`fact add` returned {len(facts)} ids for {len(d['policies'])} texts")
    ids = {p["key"]: f["id"] for p, f in zip(d["policies"], facts)}
    b = d["billing_lead"]
    actor = cli.run("actor", "create", "--custom-id", cid(b["key"]), "--display-name", b["display"],
                    "--description", f"{b['role']}. Demo data.", "--tags", "demo", echo=True)["id"]
    cli.run("actor", "bind", "--actor", actor, scoped=True, echo=True)
    conv = cli.run("conv", "create", "--custom-id", cid("channel"), "--project", project, "--actors", actor,
                   "--kind", "DIRECT", "--name", d["channel"]["name"], scoped=True, echo=True)["id"]
    emit("text", "\n    key              policy")
    for p in d["policies"]:
        emit("text", f"    {p['key']:<16} {p['text']}")
    note(f"{len(ids)} policies pinned as facts; {b['display']} ({b['role']}) posts in the {d['channel']['name']}, "
         "a conversation on the same project")
    world = dict(project=project, ids=ids, actor=actor, conv=conv)
    save("world", world)
    emit("setup", "", policies=d["policies"], **world)
    return world


# --------------------------------------------------------------------------- step 5: the channel message

def snapshot(cli: CLI, project: str) -> dict:
    return {f["id"]: dict(text=f.get("fact"), updated_at=f.get("updated_at")) for f in project_facts(cli, project)}


def wait_extraction(cli: CLI, world: dict, before: dict, t_append: float) -> dict:
    """Until the extractor has written to the project: a fact changed or added, and the cook is finished."""
    first = True
    while time.time() - t_append < EXTRACT_LIMIT:
        time.sleep(4)
        now = snapshot(cli, world["project"])
        changed = [i for i in now if i in before and now[i]["updated_at"] != before[i]["updated_at"]]
        added = [i for i in now if i not in before]
        if changed or added:
            st = cli.run("conv", "cook-status", world["conv"], scoped=True, echo=first) or {}
            first = False
            if st.get("cook_finished"):
                time.sleep(6)   # one cook can write several facts; let the rest land
                now = snapshot(cli, world["project"])
                changed = [i for i in now if i in before and now[i]["updated_at"] != before[i]["updated_at"]]
                added = [i for i in now if i not in before]
                return dict(now=now, changed=changed, added=added, secs=round(time.time() - t_append),
                            removed=[i for i in before if i not in now])
        emit("progress", f"    … waiting for the extractor ({round(time.time() - t_append)} s)", phase="wait")
    return dict(now=snapshot(cli, world["project"]), changed=[], added=[], removed=[], secs=EXTRACT_LIMIT, deferred=True)


def extractor_writes(cli: CLI, world: dict, ext: dict) -> list[dict]:
    """The extractor's writes, from `fact trace`: event, source, time, old → new."""
    key_of = {v: k for k, v in world["ids"].items()}
    rows = []
    for i, fid in enumerate(ext["changed"] + ext["added"]):
        tr = (cli.run("fact", "trace", fid, "--project", world["project"], scoped=True, echo=i == 0) or {}).get("trace") or []
        top = tr[0] if tr else {}
        rows.append(dict(id=fid, key=key_of.get(fid, "(new fact)"), event=top.get("event"), source=top.get("source_kind"),
                         at=top.get("timestamp"), old=top.get("old_fact"), new=top.get("new_fact") or ext["now"][fid]["text"]))
    return rows


def channel(cli: CLI, d: dict, world: dict, caches: dict) -> dict:
    project = world["project"]
    before = snapshot(cli, project)
    v_before = (cli.run("proj", "get", project, scoped=True, echo=True) or {}).get("updated_at")
    ts = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_append = time.time()
    msg = cli.run("conv", "msg", "append", world["conv"], "--actor", world["actor"], "--custom-id", "finance-update",
                  "--timestamp", ts, "--text", d["channel"]["text"], scoped=True, echo=True) or {}
    v_append = (cli.run("proj", "get", project, scoped=True, echo=True) or {}).get("updated_at")
    emit("text", f"\n    project version before the message {ms(v_before)} → after the append {ms(v_append)}"
                 f"{'  (moved: B will refetch)' if v_append != v_before else ''}")
    emit("message", "", body=d["channel"]["text"], v_before=v_before, v_append=v_append)
    note("tickets keep coming: the agent answers the four questions right away, before the extractor has run")
    r2 = ask_round(cli, d, project, caches, 3, "right after the message")
    ext = wait_extraction(cli, world, before, t_append)
    v_after = (cli.run("proj", "get", project, scoped=True, echo=True) or {}).get("updated_at")
    writes = extractor_writes(cli, world, ext) if not ext.get("deferred") else []
    emit("text", f"\n    the extractor wrote {len(writes)} fact(s), {ext['secs']} s after the append (from `fact trace`):")
    for w in writes:
        emit("text", f"      {w['event'] or '?':<7} {w['source'] or '?':<7} {ms(w['at'])}  {w['key']:<14} "
                     f"{value(w['old']) if w['old'] else '—'} → {value(w['new'])}")
    if ext.get("deferred"):
        note(f"the extractor has not written anything after {EXTRACT_LIMIT} s; the next rounds show what that means "
             "(see README: an extraction can leave nothing)")
    latest = max((w["at"] for w in writes if w["at"]), default=None)
    moved = v_after != v_append
    emit("text", f"    project version now {ms(v_after)} — "
                 + ("moved since the append" if moved else
                    f"unchanged since the append{f', {round((parse_ts(latest) - parse_ts(v_after)).total_seconds())} s before the extractor wrote' if latest else ''}"))
    world.update(message=msg.get("id") if isinstance(msg, dict) else None)
    save("world", world)
    out = dict(r2=r2, v_before=v_before, v_append=v_append, v_after=v_after, writes=writes,
               deferred=bool(ext.get("deferred")), secs=ext["secs"], added=len(ext["added"]), changed=len(ext["changed"]))
    emit("extracted", "", **{k: v for k, v in out.items() if k != "r2"})
    return out


# --------------------------------------------------------------------------- step 7: score

def score(d: dict, rounds: list[dict], ch: dict, exp: dict, newest: dict) -> dict:
    st = d["arms"]
    after_warm = [r for r in rounds if r["n"] > 0]
    reads = 4 * len(after_warm)
    rows = {}
    quiet_round = next(r for r in rounds if r["n"] == 1)
    emit("text", f"\n    cache                  stale answers   cache hits   validator calls   s / read: all · quiet minute"
                 f"   (search every time: {avg_live(rounds):.2f} s)")
    for a in ARMS:
        cells = [x[a] for r in after_warm for x in r["rows"]]
        stale = sum(c["stale"] for c in cells)
        hits = sum(c["hit"] for c in cells)
        vcalls = 0 if a == "A" else len(cells)
        avg = sum(c["secs"] for c in cells) / len(cells)
        qavg = sum(x[a]["secs"] for x in quiet_round["rows"]) / 4
        rows[a] = dict(stale=stale, reads=reads, hits=hits, validator_calls=vcalls, avg_secs=round(avg, 2), quiet_secs=round(qavg, 2),
                       stale_by_round={r["n"]: r["stale"][a] for r in after_warm})
        emit("text", f"    {st[a]['short']:<22} {stale:>2} / {reads:<10} {hits:>2} / {reads:<7} {vcalls:>8}          {avg:.2f} · {qavg:.2f}")
    by_n = {r["n"]: r for r in rounds}
    got_r1 = {a: by_n[2]["stale"][a] for a in ARMS}
    retired = next(q["id"] for q in d["questions"] if all(w in next(p["text"] for p in d["policies"]
                                                                       if p["key"] == d["retire"]["key"]).lower() for w in q["match"]))
    got_r4 = {a: retired in by_n[5]["stale"][a] for a in ARMS}
    quiet = {a: all(x[a]["hit"] and not x[a]["stale"] for x in by_n[1]["rows"]) for a in ARMS}
    if not all(quiet.values()):
        die(f"with nothing written every cache should hit and be current: {quiet}")
    if got_r1 != exp["r1"] or got_r4 != exp["r4_retired"]:
        die(f"the API-write rounds disagree with the story: round 1 {got_r1}, round 4 {got_r4}; expected {exp}")
    r3 = by_n[4]
    b_stale = r3["stale"]["B"]
    if b_stale:
        hit_b = all(x["B"]["hit"] for x in r3["rows"] if x["id"] in b_stale)
        note(f"B served {', '.join(b_stale)} from cache after the extractor rewrote them: version unchanged ✓ "
             f"{'(every one a cache hit)' if hit_b else ''} — the version stopped at the message append")
    elif not ch["deferred"]:
        note("B was current this time after the extractor: " + ("the project version moved" if ch["v_after"] != ch["v_append"]
             else "the extractor landed before B refilled"))
    note(f"newest-fact time alone would have missed the retired policy: unchanged {ms(newest['before'])} → "
         f"{ms(newest['after'])}; C caught it because `total` went {newest['total_before']} → {newest['total_after']}")
    report = dict(at=datetime.now(timezone.utc).isoformat(), prefix=PREFIX, rows=rows, rounds=rounds,
                  extractor={k: v for k, v in ch.items() if k != "r2"}, newest=newest, expected=exp)
    save("report", report)
    emit("score", "", rows=rows, extractor=report["extractor"], newest=newest)
    return report


def avg_live(rounds: list[dict]) -> float:
    xs = [x["live_secs"] for r in rounds for x in r["rows"]]
    return sum(xs) / len(xs)


# --------------------------------------------------------------------------- pipeline

def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    d = load_data()
    exp = check_story()
    banner(2, TOTAL, "Memory — 6 support policies, and a billing channel on the same project")
    world = setup(cli, d)
    project = world["project"]
    caches = {a: {} for a in ARMS}
    rounds = []
    banner(3, TOTAL, "Warm the caches — 4 questions, three caches and a direct search")
    for a in ARMS:
        note(f"{d['arms'][a]['short']}: {d['arms'][a]['how'].replace('`', '')}")
    rounds.append(ask_round(cli, d, project, caches, 0, "warm-up"))
    note("a quiet minute: nothing is written, the same four questions come in again")
    rounds.append(ask_round(cli, d, project, caches, 1, "nothing written"))

    e = d["admin_edit"]
    banner(4, TOTAL, "An API write — the admin tool changes a policy")
    cli.run("fact", "update", world["ids"][e["key"]], "--text", e["text"], "--project", project, scoped=True, echo=True)
    note(f"{e['who']}: {e['why']} (`fact update`, same fact id)")
    emit("edit", "", key=e["key"], body=e["text"], who=e["who"], why=e["why"])
    rounds.append(ask_round(cli, d, project, caches, 2, "after the admin edit"))

    banner(5, TOTAL, f"A message in the {d['channel']['name']} — the extractor rewrites two policies")
    ch = channel(cli, d, world, caches)
    rounds.append(ch["r2"])
    r3 = ask_round(cli, d, project, caches, 4, "after the extractor")
    rounds.append(r3)
    for x in r3["rows"]:
        b = x["B"]
        if b["stale"]:
            emit("text", f"    B, {x['short']}: entry filled at version {ms(caches['B'][x['id']]['version'])}, version now "
                         f"{ms(b['version'])} ✓ → served “{b['text']}” — memory says “{x['live']}”")

    r = d["retire"]
    banner(6, TOTAL, "Another API write — a policy is retired")
    top = cli.run("fact", "list", "--projects", project, "--page-size", "1", scoped=True, echo=True) or {}
    delete_fact(cli, world["ids"][r["key"]], ["--project", project], echo=True)
    top2 = cli.run("fact", "list", "--projects", project, "--page-size", "1", scoped=True, echo=True) or {}
    newest = dict(before=(top.get("items") or [{}])[0].get("updated_at") or "", after=(top2.get("items") or [{}])[0].get("updated_at") or "",
                  total_before=top.get("total"), total_after=top2.get("total"))
    note(f"{r['who']}: {r['why']} (`fact delete`)")
    emit("retire", "", **r, **newest)
    rounds.append(ask_round(cli, d, project, caches, 5, "after the retirement"))

    banner(7, TOTAL, "Score — which cache served stale answers?")
    return score(d, rounds, ch, exp, newest)


# --------------------------------------------------------------------------- read-only subcommand

def show_versions(cli: CLI) -> None:
    p = find_project(cli, echo=True)
    if not p:
        die("the demo project does not exist yet; run `python3 demo.py` first")
    key_of = {v: k for k, v in load("world")["ids"].items()} if (OUT / "world.json").exists() else {}
    facts = project_facts(cli, p["id"], echo=True)
    emit("text", f"\n  project version (proj get → updated_at):       {ms(p.get('updated_at'))}")
    emit("text", f"  fact watermark  (newest updated_at | total):   {ms(facts[0]['updated_at']) if facts else '—'} | {len(facts)}\n")
    emit("text", f"  {'key':<16} {'updated_at':<14} {'last write':<24} text")
    for i, f in enumerate(facts):
        tr = (cli.run("fact", "trace", f["id"], "--project", p["id"], scoped=True, echo=i == 0) or {}).get("trace") or []
        top = tr[0] if tr else {}
        who = {"COOK": "the extractor", "MANUAL": "an API call"}.get(top.get("source_kind"), top.get("source_kind") or "?")
        emit("text", f"  {key_of.get(f['id'], '(new fact)'):<16} {ms(f['updated_at']):<14} "
                     f"{(top.get('event') or '?') + ' by ' + who:<24} {f['fact'][:72]}")
    rep = OUT / "report.json"
    if rep.exists():
        x = json.loads(rep.read_text(encoding="utf-8"))["extractor"]
        cooks = [w for w in x.get("writes") or [] if w.get("source") == "COOK"]
        if cooks:
            emit("text", f"\n  in the last run: the project version stopped at the message append, {ms(x['v_append'])}; "
                         f"the extractor wrote {len(cooks)} fact(s) at {ms(max(w['at'] for w in cooks))} and it read "
                         f"{ms(x['v_after'])} after that")
    note("an API call moves the project version; the extractor's writes move only the facts — "
         "watch the facts (newest updated_at + total) to see both")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    d = load_data()
    # `proj delete` does not delete conversations: they go explicitly, found by custom_id.
    convs = demo_convs(cli)
    for c in convs:
        cli.run("conv", "delete", c["id"], scoped=True, echo=not quiet)
    nfacts, nproj = 0, 0
    a = find_actor(cli, d["billing_lead"]["key"], echo=not quiet)
    if a:
        nfacts += delete_actor(cli, a["id"])
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
        note(f"deleted {len(convs)} conversation, {nfacts} facts, {1 if a else 0} actor (facts first: a deleted actor's "
             f"facts could never be removed) and {nproj} project" if (convs or a or nproj) else "nothing to delete")
    # Kept, not deleted: moved to out/runs/, so every run's numbers survive a cleanup.
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    for name in ("report", "world"):
        f = OUT / f"{name}.json"
        if f.exists():
            (OUT / "runs").mkdir(parents=True, exist_ok=True)
            f.rename(OUT / "runs" / f"{name}-{stamp}.json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "versions", "cleanup"],
                    help="run = full demo (default); versions = the project version against each fact's last write; "
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
    if args.command == "versions":
        banner(2, 2, "Versions — the project version against each fact's last write")
        show_versions(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. Try `python3 demo.py versions`; `python3 demo.py cleanup` removes the demo data.")


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

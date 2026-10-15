#!/usr/bin/env python3
"""
Known issues memory — a MemoryLake demo driven entirely by the `memorylake`
CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Pebbleline (fictional) makes a shared-notes app; its support team keeps the
known issues in one project and what it told each customer on that
customer's own actor.  In September and October three customers were given
KI-204's workaround for iPad notes that never reach the desktop: sign out of
the iPad app and sign back in.

Today, 2026-10-15, engineering withdraws that workaround: signing out
discards notes that have not synced yet.  The known issue is updated in
place.  But the old advice is still in three customers' memory, and two of
them write in today.  A reply built from the customer's history alone repeats
the withdrawn step; one built from the known issues alone never tells the
customer that what we said before was wrong.  Read together — the customer's
record, the issue as it stands now, and `fact trace` showing what it said
when the customer was told — the reply retracts the old advice and routes
each ticket correctly.  One `fact list --query` across every customer then
finds who else was told the withdrawn step, and the corrections go back into
their memory.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-kim"  # memorylake-usecases / known-issues-memory

PROJECTS = dict(
    ki=dict(key="known-issues", name="Pebbleline support — known issues",
            description="Team-wide known issues: symptom, where it applies, the current workaround and what was withdrawn. Demo data."),
    tickets=dict(key="tickets", name="Pebbleline support — ticket conversations",
                 description="Past ticket conversations (kept out of the known-issues project). Demo data."),
)


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

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False):
        try:
            return self.run(*args, scoped=scoped)
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


# --------------------------------------------------------------------------- reading scopes

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


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", flag, scope_id], echo=echo)


def find(cli: CLI, kind: str, key: str, echo: bool = True):
    try:
        return cli.run(kind, "get", cid(key), "--by-custom-id", scoped=kind in ("proj", "conv"), echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def bind_actor(cli: CLI, actor_id: str) -> None:
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, key: str, name: str, description: str, tags: str) -> str:
    actor = find(cli, "actor", key)
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", name,
                        "--description", description, "--tags", tags)
        note(f"created {name} → {actor['id']}")
    else:
        note(f"{name} already exists → {actor['id']}")
    bind_actor(cli, actor["id"])
    return actor["id"]


# --------------------------------------------------------------------------- pinning

def pin(cli: CLI, flag: str, scope_id: str, items: list[tuple[str, dict]], what: str) -> dict[str, dict]:
    """Pin the texts the scope does not hold yet — one `fact add` call for all of them, then
    `fact update --metadata` on each (fact add takes no metadata). Returns text → {id, new}."""
    plural = {"--project": "--projects", "--actor": "--actors"}[flag]
    live = {f.get("fact"): f for f in list_facts(cli, plural, scope_id, echo=False)}
    out, missing = {}, []
    for text, meta in items:
        f = live.get(text)
        if f is None:
            missing.append((text, meta))
            continue
        out[text] = dict(id=f["id"], new=False)
        if (f.get("metadata") or {}) != meta:
            cli.run("fact", "update", f["id"], flag, scope_id, "--metadata", json.dumps(meta), scoped=True, echo=False)
    if missing:
        res = cli.run("fact", "add", flag, scope_id, *[t for t, _ in missing], scoped=True) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, flag, scope_id, "--metadata", json.dumps(meta), scoped=True, echo=i == 0)
            out[text] = dict(id=fid, new=True)
        if len(missing) > 1 and what:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way ({what})")
    return out


def customer(story: dict, key: str) -> dict:
    k = key.lower().replace(" ", "").replace("&", "")
    c = next((c for c in story["customers"]
              if c["key"] == k or c["short"].lower().replace(" ", "").replace("&", "") == k), None)
    if c is None:
        die(f"unknown customer {key!r}; data/story.json has: {', '.join(c['key'] for c in story['customers'])}")
    return c


def ki_meta(k: dict) -> dict:
    return dict(kind="known_issue", ki=k["ki"], symptom=k["symptom"], status=k["status"], version=k["version"],
                applies_when=k["applies_when"], workaround=k["workaround"], reported=k["reported"], withdrawn=[])


def withdrawn_meta(story: dict) -> dict:
    k = next(k for k in story["known_issues"] if k["ki"] == story["withdrawal"]["ki"])
    w = story["withdrawal"]
    return dict(ki_meta(k), version=w["version"], workaround=w["workaround"], recovery=w["recovery"],
                withdrawn=[dict(version=k["version"], workaround=w["withdrawn"], on=w["date"], by=w["by"], reason=w["reason"])])


def advice_meta(story: dict, c: dict) -> dict:
    a = c["advice"]
    return dict(kind="advice", ki=a["ki"], version=a["version"], advised=a["advised"], not_ki=a.get("not_ki"),
                ticket=c["ticket"]["id"], date=c["ticket"]["date"], agent=story["agents"]["dana"]["name"])


def known_issues(cli: CLI, ids: dict, echo: bool = True) -> dict[str, dict]:
    """KI number → the known issue as it stands now, read from the pinned facts' metadata (every page)."""
    out = {}
    for f in list_facts(cli, "--projects", ids["ki"], echo=echo):
        m = f.get("metadata") or {}
        if m.get("kind") == "known_issue":
            out[m["ki"]] = dict(m, id=f["id"], text=f.get("fact"))
    return out


def memory_of(cli: CLI, actor_id: str, echo: bool = True) -> list[dict]:
    """Everything on a customer's actor: devices, what we told them (advice / correction), and extracted facts."""
    out = []
    for f in list_facts(cli, "--actors", actor_id, echo=echo):
        m = f.get("metadata") or {}
        out.append(dict(id=f["id"], text=f.get("fact"), kind=m.get("kind") or "extracted", meta=m))
    return out


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    ids = {}
    for k, p in PROJECTS.items():
        proj = find(cli, "proj", p["key"])
        if proj is None:
            proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                           "--description", p["description"], scoped=True)
            note(f"created “{p['name']}” → {proj['id']}")
        else:
            note(f"“{p['name']}” already exists → {proj['id']} (pass --reset to start over)")
        ids[k] = proj["id"]
    for c in story["customers"]:
        ids[c["key"]] = ensure_actor(cli, c["key"], c["name"], c["description"], "customer")
    for k, a in story["agents"].items():
        ids[k] = ensure_actor(cli, a["key"], a["name"], a["description"], "support-agent")
    lines = [f"\n  Known issues           {ids['ki']}  (team-wide: one project every agent reads)",
             f"  Ticket history         {ids['tickets']}  (past conversations live here, not in known issues)"]
    lines += [f"  {c['name']:<22} {ids[c['key']]}  (customer memory: one actor per customer)" for c in story["customers"]]
    lines += [f"  {a['name']:<22} {ids[k]}  (support agent)" for k, a in story["agents"].items()]
    emit("text", "\n".join(lines))
    emit("setup", "", ids=ids, projects={k: p["name"] for k, p in PROJECTS.items()},
         customers=[dict(key=c["key"], name=c["name"], description=c["description"]) for c in story["customers"]],
         agents=[dict(key=k, name=a["name"], description=a["description"]) for k, a in story["agents"].items()])
    return ids


# --------------------------------------------------------------------------- step 3: known issues, as in September

def show_kis(kis: list[dict]) -> None:
    lines = [f"\n  {'issue':<7} {'v':<3} {'status':<13} {'applies to':<16} current workaround"]
    for k in sorted(kis, key=lambda k: k["ki"]):
        lines.append(f"  {k['ki']:<7} v{k['version']:<2} {clip(k['status'], 13):<13} {k['applies_when']:<16} {clip(k['workaround'], 70)}")
        for w in k.get("withdrawn") or []:
            lines.append(f"  {'':<7} {'':<3} v{w['version']} WITHDRAWN {w['on']}: “{w['workaround']}” — {w['reason']}")
        lines.append(f"  {'':<11} {k['id']}")
    emit("text", "\n".join(lines))


def pin_known_issues(cli: CLI, ids: dict, story: dict) -> dict:
    w = story["withdrawal"]
    v1 = next(k for k in story["known_issues"] if k["ki"] == w["ki"])
    # Every run replays today: if an earlier run already withdrew the workaround, put the issue back as it
    # stood in September (a fresh fact, so its trace is the clean ADD → UPDATE of this run).
    for f in list_facts(cli, "--projects", ids["ki"], echo=False):
        m = f.get("metadata") or {}
        if m.get("ki") == w["ki"] and f.get("fact") != v1["text"]:
            note(f"replaying today: an earlier run already withdrew {w['ki']}'s workaround; putting {w['ki']} back as it "
                 "stood in September")
            cli.run("fact", "delete", "--project", ids["ki"], f["id"], scoped=True)
    note(f"{len(story['known_issues'])} known issues as they stood in September go onto the known-issues project in one "
         "`fact add`; each then gets metadata: symptom, where it applies, workaround, version")
    got = pin(cli, "--project", ids["ki"], [(k["text"], ki_meta(k)) for k in story["known_issues"]],
              "symptom, applies to, workaround, version")
    if not any(g["new"] for g in got.values()):
        note("the other known issues were pinned by an earlier run; reading them back")
    kis = known_issues(cli, ids)
    show_kis(list(kis.values()))
    emit("known_issues", "", items=list(kis.values()))
    return kis


# --------------------------------------------------------------------------- step 4: what each customer was told

def list_messages(cli: CLI, conv_id: str) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"] + (["--continuation-token", token] if token else [])
        page = cli.run(*args, echo=False) or {}
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return msgs


def wait_for_cook(cli: CLI, conv_ids: list[str], timeout: int = 600) -> int:
    t0 = last = time.time()
    left, first = list(conv_ids), True
    while left and time.time() - t0 < timeout:
        for cid_ in list(left):
            try:
                status = cli.run("conv", "cook-status", cid_, scoped=True, echo=first) or {}
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                status = {}
            first = False
            if status.get("cook_finished"):
                left.remove(cid_)
        if not left:
            break
        if time.time() - last >= 30:
            emit("progress", f"  … still turning {len(left)} ticket conversation(s) into memory ({int(time.time() - t0)}s)")
            last = time.time()
        time.sleep(5)
    if left:
        note(f"gave up after {timeout}s; {len(left)} conversation(s) still processing server-side")
        return -1
    return int(time.time() - t0)


def file_ticket(cli: CLI, ids: dict, c: dict, first: bool) -> tuple[str, int]:
    t = c["ticket"]
    conv = find(cli, "conv", f"ticket-{c['key']}", echo=False)
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", cid(f"ticket-{c['key']}"), "--project", ids["tickets"],
                       "--actors", f"{ids[c['key']]},{ids['dana']}", "--kind", "DIRECT", "--name", t["title"],
                       "--metadata", f"ticket={t['id']}", "--metadata", f"customer={c['name']}", scoped=True, echo=first)
    done = len(list_messages(cli, conv["id"]))
    speaker = {"customer": ids[c["key"]], "agent": ids["dana"]}
    start = datetime.fromisoformat(t["start"].replace("Z", "+00:00"))
    n = 0
    for i, (k, words) in enumerate(t["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(minutes=2 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cli.run("conv", "msg", "append", conv["id"], "--actor", speaker[k], "--custom-id", f"msg-{i:02d}",
                "--timestamp", ts, "--text", words, scoped=True, echo=first and n == 0)
        n += 1
        emit("turn", "", customer=c["key"], index=i, who=k, line=words)
    return conv["id"], n


def told_table(story: dict, mems: dict) -> list[dict]:
    rows = []
    for c in story["customers"]:
        adv = [m for m in mems[c["key"]] if m["kind"] == "advice"]
        for a in adv:
            m = a["meta"]
            rows.append(dict(customer=c["key"], name=c["name"], ticket=m["ticket"], date=m["date"], ki=m["ki"],
                             version=m["version"], advised=m["advised"], not_ki=m.get("not_ki"), id=a["id"]))
    return rows


def customer_memory(cli: CLI, ids: dict, story: dict) -> dict:
    # Every run replays today: corrections an earlier run wrote are removed first.
    removed = 0
    for c in story["customers"]:
        for m in memory_of(cli, ids[c["key"]], echo=False):
            if m["kind"] == "correction":
                cli.run("fact", "delete", "--actor", ids[c["key"]], m["id"], scoped=True, echo=removed == 0)
                removed += 1
    if removed:
        note(f"replaying today: removed {removed} correction record(s) an earlier run wrote")
    note("each customer's ticket is a DIRECT conversation (customer ↔ Dana) in the ticket-history project; "
         "MemoryLake turns it into memory on the customer's actor")
    convs, appended = [], 0
    for i, c in enumerate(story["customers"]):
        conv_id, n = file_ticket(cli, ids, c, first=i == 0)
        convs.append(conv_id)
        appended += n
    if appended:
        note(f"… {appended} message(s) appended across {len(convs)} ticket conversations the same way")
        secs = wait_for_cook(cli, convs)
        if secs >= 0:
            note(f"the ticket conversations were turned into memory in {secs}s")
    else:
        note("the ticket conversations were filed by an earlier run")
    note("what support told each customer is pinned on the customer's actor word for word, with the ticket, the date, "
         "the known issue and the workaround version it came from — and so is the customer's setup")
    for c in story["customers"]:
        pin(cli, "--actor", ids[c["key"]],
            [(c["devices"], dict(kind="device", ticket=c["ticket"]["id"], date=c["ticket"]["date"])),
             (c["advice"]["text"], advice_meta(story, c))], "")
    mems = {c["key"]: memory_of(cli, ids[c["key"]], echo=i == 0) for i, c in enumerate(story["customers"])}
    rows = told_table(story, mems)
    lines = [f"\n  {'customer':<23} {'ticket':<7} {'date':<11} {'issue':<8} what we told them"]
    for r in rows:
        what = f"v{r['version']}: {r['advised']}" + (f"  (not {r['not_ki']})" if r["not_ki"] else "")
        lines.append(f"  {r['name']:<23} {r['ticket']:<7} {r['date']:<11} {r['ki']:<8} {clip(what, 92)}")
    extracted = {c["key"]: [m for m in mems[c["key"]] if m["kind"] == "extracted"] for c in story["customers"]}
    examples = [(c, m) for c in story["customers"] for m in extracted[c["key"]][:1]][:3]
    lines.append(f"\n  + {sum(map(len, extracted.values()))} fact(s) MemoryLake extracted from the four conversations "
                 "(shown, not relied on)" + (", e.g.:" if examples else ""))
    for c, m in examples:
        lines.append(f"      {c['short']}: {clip(m['text'], 100)}")
    emit("text", "\n".join(lines))
    out = dict(told=rows, appended=appended, conversations=convs, removed=removed,
               extracted={k: len(v) for k, v in extracted.items()})
    emit("customers", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: today, the workaround is withdrawn

def from_workaround(text: str) -> str:
    """The part of a known issue's text that changes: from its first "Workaround" on."""
    i = text.find("Workaround")
    return text[i:] if i >= 0 else text


def trace_of(cli: CLI, ids: dict, fact_id: str, echo: bool = True) -> list[dict]:
    res = cli.run("fact", "trace", fact_id, "--project", ids["ki"], scoped=True, echo=echo) or {}
    return [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                 old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]


def withdraw(cli: CLI, ids: dict, story: dict, kis: dict) -> dict:
    w = story["withdrawal"]
    k = kis[w["ki"]]
    note(f"{story['today']}: {w['by']} withdraws {w['ki']}'s workaround — {w['reason']}. The known issue is updated in "
         "place: new text and metadata in one `fact update`, so the change and its reason are in the fact's trace")
    cli.run("fact", "update", k["id"], "--project", ids["ki"], "--text", w["text"], "--metadata",
            json.dumps(withdrawn_meta(story)), scoped=True)
    kis = known_issues(cli, ids, echo=False)
    show_kis([kis[w["ki"]]])
    trace = trace_of(cli, ids, k["id"])
    lines = [f"\n  fact trace {w['ki']} (newest first):"]
    for e in trace:
        lines.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}")
        if e["old"]:
            lines.append(f"      was: “…{clip(from_workaround(e['old']), 150)}”")
        lines.append(f"      {'now' if e['old'] else 'text'}: “…{clip(from_workaround(e['new']), 150)}”")
    upd = next((e for e in trace if e["event"] == "UPDATE"), None)
    said = (upd or {}).get("old") or ""
    told = [c for c in story["customers"] if c["advice"]["ki"] == w["ki"] and c["advice"]["advised"] in said]
    lines.append(f"\n  The withdrawn step is gone from the known issue. It is still in the trace — and, word for word, in "
                 f"{len(told)} customers' memory: " + ", ".join(c["name"] for c in told) + ".")
    emit("text", "\n".join(lines))
    out = dict(ki=kis[w["ki"]], trace=trace, still_told=[c["key"] for c in told])
    emit("withdrawn", "", **out)
    return kis


# --------------------------------------------------------------------------- step 6: two follow-ups, three readings

LOSS = re.compile(r"\b(gone|lost|missing|disappeared|deleted|vanished)\b", re.I)


def judge(reply: str, c: dict, kis: dict, text: str, mem: list[dict]) -> dict:
    """Three checks on any reply: it does not repeat a withdrawn step, it retracts a withdrawn step this
    customer was given, and it takes the right route (recovery for lost notes, else the current workaround)."""
    withdrawn = {w["workaround"]: (k, w) for k in kis.values() for w in k.get("withdrawn") or []}
    told = [m for m in mem if m["kind"] == "advice" and m["meta"].get("advised") in withdrawn]
    loss = bool(LOSS.search(text))
    # A sentence that names the withdrawn step to retract it ("… has been withdrawn", "do not …") is not repeating it.
    sentences = [x for x in re.split(r"(?<=[.;:])\s+", reply) if x]
    repeats = any(s.lower() in x.lower() and not re.search(r"withdrawn|do not|don't", x, re.I)
                  for s in withdrawn for x in sentences)
    checks = [dict(name="does not repeat a withdrawn step", fail="repeats the withdrawn step", ok=not repeats)]
    if told:
        t = told[0]["meta"]["ticket"]
        checks.append(dict(name=f"retracts what {t} told them", fail=f"never retracts what {t} told them",
                           ok="withdrawn" in reply.lower()))
        k, _ = withdrawn[told[0]["meta"]["advised"]]
        want = "sync journal" if loss else k["workaround"].split(";")[0]
        checks.append(dict(name="routes to recovery (notes lost)" if loss else f"gives {k['ki']}'s current workaround",
                           fail="does not offer to recover the lost notes" if loss else f"does not give {k['ki']}'s current workaround",
                           ok=want.lower() in reply.lower()))
    return dict(checks=checks, ok=all(x["ok"] for x in checks), loss=loss)


def customer_only(cli: CLI, ids: dict, c: dict, text: str, mem: list[dict], echo: bool = True) -> dict:
    """What we told this customer before, nothing else: search the customer's actor, follow the closest advice."""
    res = cli.run("search", text, "--actors", ids[c["key"]], "--types", "fact", "--top-k", "5", scoped=True, echo=echo) or {}
    by_id = {m["id"]: m for m in mem}
    hits, cached = [], 0
    for f in res.get("facts") or []:
        m = by_id.get(f.get("id"))
        if m is None:
            cached += 1
            continue
        hits.append(dict(id=m["id"], kind=m["kind"], text=m["text"], meta=m["meta"]))
    pick = next((h for h in hits if h["kind"] in ("advice", "correction")), None)
    if pick and pick["kind"] == "advice":
        m = pick["meta"]
        reply = f"Following up on {m['ticket']} ({m['date']}): as we advised then, {m['advised']}."
    elif pick:
        reply = pick["text"]
    else:
        reply = "We have no earlier advice on record; could you tell us more?"
    return dict(hits=hits, cached=cached, pick=pick and pick["id"],
                rank=pick and next(i for i, h in enumerate(hits, 1) if h["id"] == pick["id"]), reply=reply)


def ki_only(cli: CLI, ids: dict, kis: dict, text: str, echo: bool = True) -> dict:
    """The known issues as they stand now, nothing about the customer: search the project, give the top issue's workaround."""
    res = cli.run("search", text, "--projects", ids["ki"], "--types", "fact", "--top-k", "3", scoped=True, echo=echo) or {}
    by_id = {k["id"]: k for k in kis.values()}
    hits, cached = [], 0
    for f in res.get("facts") or []:
        k = by_id.get(f.get("id"))
        if k is None:
            cached += 1
            continue
        hits.append(dict(id=k["id"], ki=k["ki"], text=k["text"]))
    top = hits[0]["ki"] if hits else None
    reply = f"This is known issue {top}: {kis[top]['workaround']}." if top else "No known issue matches; escalating."
    return dict(hits=hits, cached=cached, top=top, reply=reply)


def joined(cli: CLI, ids: dict, story: dict, kis: dict, c: dict, text: str, mem: list[dict], echo: bool = True) -> dict:
    """The customer's record × the known issue as it stands now × what it said when they were told (`fact trace`)."""
    loss = bool(LOSS.search(text))
    open_ = []
    for m in mem:
        if m["kind"] != "advice":
            continue
        k = kis.get(m["meta"]["ki"])
        if not k:
            continue
        w = next((w for w in k.get("withdrawn") or [] if w["workaround"] == m["meta"]["advised"]), None)
        corrected = any(x["kind"] == "correction" and x["meta"].get("ki") == k["ki"] and x["meta"].get("version") == k["version"]
                        for x in mem)
        open_.append(dict(advice=m, ki=k, withdrawn=w, corrected=corrected))
    stale = next((o for o in open_ if o["withdrawn"]), None)
    evidence = None
    if stale:
        trace = trace_of(cli, ids, stale["ki"]["id"], echo=echo)
        upd = next((e for e in trace if e["event"] == "UPDATE"), None)
        evidence = dict(event=upd and upd["event"], at=upd and upd["at"],
                        verbatim=bool(upd and stale["advice"]["meta"]["advised"] in upd["old"]))
        m, k, w = stale["advice"]["meta"], stale["ki"], stale["withdrawn"]
        step = k["recovery"] if loss else f"use workaround v{k['version']} instead: {k['workaround']}"
        reply = (f"Correction to {m['ticket']} ({m['date']}): the step we gave you then ({m['advised']}) has been "
                 f"withdrawn, because {w['reason']}. Please do not do it again; {step}.")
    else:
        k = kis.get(next((o["ki"]["ki"] for o in open_), None) or "")
        reply = f"{k['ki']}: {k['workaround']}." if k else "No earlier advice and no matching issue on record; escalating."
    return dict(open=[dict(ticket=o["advice"]["meta"]["ticket"], date=o["advice"]["meta"]["date"], ki=o["ki"]["ki"],
                           told_version=o["advice"]["meta"]["version"], now_version=o["ki"]["version"],
                           withdrawn=bool(o["withdrawn"]), corrected=o["corrected"]) for o in open_],
                evidence=evidence, loss=loss, reply=reply)


def three_readings(cli: CLI, ids: dict, story: dict, kis: dict, c: dict, text: str, echo: bool = True) -> dict:
    mem = memory_of(cli, ids[c["key"]], echo=False)
    a = customer_only(cli, ids, c, text, mem, echo=echo)
    b = ki_only(cli, ids, kis, text, echo=echo)
    j = joined(cli, ids, story, kis, c, text, mem, echo=echo)
    for r in (a, b, j):
        r["judge"] = judge(r["reply"], c, kis, text, mem)
    return dict(customer=c["key"], name=c["name"], text=text, customer_only=a, ki_only=b, joined=j)


def show_readings(r: dict, ticket: str | None = None) -> None:
    lines = [f"\n  {ticket + ' ' if ticket else ''}{r['name']}: “{r['text']}”"]
    a, b, j = r["customer_only"], r["ki_only"], r["joined"]
    lines.append(f"\n    (a) customer history only — `search --actors {r['customer']}`, follow the closest advice"
                 + (f" (#{a['rank']} of top 5)" if a["rank"] else ""))
    lines.append(f"        → “{a['reply']}”")
    lines.append(f"    (b) known issues only — `search --projects <known issues>`, top hit {b['top'] or '—'}")
    lines.append(f"        → “{clip(b['reply'], 170)}”")
    lines.append("    (c) both, joined — the customer's advice records × each issue as it stands now × `fact trace`")
    for o in j["open"]:
        state = (f"told v{o['told_version']}, now v{o['now_version']} — v{o['told_version']} WITHDRAWN" if o["withdrawn"]
                 else f"told v{o['told_version']}, still current")
        lines.append(f"        {o['ticket']} {o['date']} {o['ki']}: {state}" + (" · correction on record" if o["corrected"] else ""))
    if j["evidence"]:
        e = j["evidence"]
        lines.append(f"        fact trace: {e['event']} at {e['at']}; the step they were given is the old text word for word: "
                     f"{'yes' if e['verbatim'] else 'NO'}")
    lines.append(f"        → “{clip(j['reply'], 400)}”")
    for tag, x in (("a", a), ("b", b), ("c", j)):
        fails = [ch["fail"] for ch in x["judge"]["checks"] if not ch["ok"]]
        lines.append(f"    {'✓' if x['judge']['ok'] else '✗'} ({tag}) " + ("passes all " + str(len(x["judge"]["checks"])) + " checks" if not fails else "; ".join(fails)))
    if a["cached"] or b["cached"]:
        lines.append(f"    · {a['cached'] + b['cached']} hit(s) left out: no longer in memory (search answered from its cache)")
    emit("text", "\n".join(lines))


def follow_ups(cli: CLI, ids: dict, story: dict, kis: dict) -> dict:
    note(f"{story['today']}: two customers who were given the withdrawn step write in. {story['agents']['leo']['name']} "
         "(started this week) answers. Three ways to build the reply, each judged by the same three checks")
    rows = []
    for c in story["customers"]:
        if not c.get("today"):
            continue
        r = three_readings(cli, ids, story, kis, c, c["today"]["text"])
        r["ticket"] = c["today"]["id"]
        show_readings(r, c["today"]["id"])
        rows.append(r)
    lines = [f"\n  {'ticket':<7} {'customer':<23} {'(a) history only':<17} {'(b) issues only':<16} (c) joined"]
    for r in rows:
        m = lambda x: "✓" if x["judge"]["ok"] else "✗"
        lines.append(f"  {r['ticket']:<7} {r['name']:<23} {m(r['customer_only']):<17} {m(r['ki_only']):<16} {m(r['joined'])}")
    emit("text", "\n".join(lines))
    out = dict(rows=rows)
    emit("follow_ups", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: who else was told, and the corrections

def recall(cli: CLI, ids: dict, story: dict, kis: dict, echo: bool = True) -> dict:
    """One `fact list --query <issue>` across every customer's actor; metadata says who was given a withdrawn step."""
    w = story["withdrawal"]
    owners = {ids[c["key"]]: c for c in story["customers"]}
    items = pages(cli, ["fact", "list", "--actors", ",".join(owners), "--query", w["ki"]], echo=echo)
    k = kis[w["ki"]]
    gone = {x["workaround"]: x for x in k.get("withdrawn") or []}
    rows = []
    for f in items:
        c = owners.get(((f.get("owner") or {}).get("id")) if isinstance(f.get("owner"), dict) else f.get("owner"))
        m = f.get("metadata") or {}
        kind = m.get("kind") or "extracted"
        if kind == "advice" and m.get("ki") == w["ki"] and m.get("advised") in gone:
            verdict = "told the withdrawn step"
        elif kind == "advice":
            verdict = f"mentions {w['ki']}, but was told {m.get('ki')} ({clip(m.get('advised') or '', 40)})"
        elif kind == "correction":
            verdict = f"corrected on {m.get('date')}"
        else:
            verdict = "extracted from the conversation (not relied on)"
        rows.append(dict(customer=c and c["key"], name=c["name"] if c else "?", id=f["id"], kind=kind, text=f.get("fact"),
                         ticket=m.get("ticket"), date=m.get("date"), verdict=verdict))
    told = sorted({r["customer"] for r in rows if r["verdict"] == "told the withdrawn step"})
    corrected = sorted({r["customer"] for r in rows if r["kind"] == "correction"})
    return dict(query=w["ki"], rows=rows, told=told, corrected=corrected)


def show_recall(rc: dict, story: dict) -> None:
    lines = [f"\n  `fact list --actors <all {len(story['customers'])} customers> --query {rc['query']}` — {len(rc['rows'])} fact(s):"]
    for r in sorted(rc["rows"], key=lambda r: (r["name"], r["kind"] != "advice", r["kind"])):
        lines.append(f"    {r['name']:<23} {r['kind']:<10} {clip(r['verdict'], 70)}")
    names = {c["key"]: c["name"] for c in story["customers"]}
    lines.append("\n  Given the withdrawn step: " + (", ".join(names[k] for k in rc["told"]) or "nobody"))
    lines.append("  Corrected since:          " + (", ".join(names[k] for k in rc["corrected"]) or "nobody yet"))
    emit("text", "\n".join(lines))


def correction_text(story: dict, c: dict, r: dict | None) -> str:
    w, leo = story["withdrawal"], story["agents"]["leo"]["name"]
    a = c["advice"]
    how = f"in reply to {c['today']['id']}" if c.get("today") else "proactive notice, no ticket"
    route = (f"escalated to engineering to recover the lost notes from the iPad's sync journal" if r and r["joined"]["loss"]
             else f"gave workaround v{w['version']}: {w['workaround'].split(';')[0]}")
    return (f"Correction {w['date']} ({leo}, {how}): told {c['name']} that the {w['ki']} step from {c['ticket']['id']} "
            f"({a['advised']}) is withdrawn because {w['reason']}; {route}.")


def come_back(cli: CLI, ids: dict, story: dict, c: dict, record_id: str | None, echo: bool = True) -> dict:
    q = story["come_back_question"]
    res = cli.run("search", q, "--actors", ids[c["key"]], "--types", "fact", "--top-k", "3", scoped=True, echo=echo) or {}
    hits = [dict(id=f.get("id"), text=f.get("fact") or f.get("content"), record=f.get("id") == record_id) for f in res.get("facts") or []]
    rank = next((i for i, h in enumerate(hits, 1) if h["record"]), None)
    return dict(question=q, hits=hits, rank=rank)


def recall_and_correct(cli: CLI, ids: dict, story: dict, kis: dict, fu: dict) -> dict:
    w = story["withdrawal"]
    note(f"who else was given {w['ki']}'s withdrawn step? One `fact list --query` across every customer's actor — "
         "every match, nothing ranked away — then each fact's metadata decides")
    before = recall(cli, ids, story, kis)
    show_recall(before, story)
    note("the corrections go onto each of those customers' actors: the two answered today, and a proactive notice for "
         "the one who has not written in yet")
    recs = []
    by_c = {r["customer"]: r for r in fu["rows"]}
    for c in story["customers"]:
        if c["key"] not in before["told"]:
            continue
        text = correction_text(story, c, by_c.get(c["key"]))
        meta = dict(kind="correction", ki=w["ki"], version=w["version"], date=w["date"], agent=story["agents"]["leo"]["name"],
                    ticket=(c.get("today") or {}).get("id"), corrects=c["ticket"]["id"],
                    route="recovery" if by_c.get(c["key"]) and by_c[c["key"]]["joined"]["loss"] else "workaround")
        got = pin(cli, "--actor", ids[c["key"]], [(text, meta)], "ki, version, corrects")
        recs.append(dict(customer=c["key"], name=c["name"], id=got[text]["id"], text=text))
        emit("text", f"\n  {c['name']}: {got[text]['id']}\n    “{text}”")
    after = recall(cli, ids, story, kis, echo=False)
    left = [k for k in after["told"] if k not in after["corrected"]]
    emit("text", f"\n  Re-read the same way: {len(after['told'])} given the withdrawn step, {len(after['corrected'])} corrected, "
                 f"{len(left)} left to tell.")
    c = next(c for c in story["customers"] if c["key"] in before["told"] and not c.get("today"))
    rec = next(x for x in recs if x["customer"] == c["key"])
    note(f"next week {c['name']} calls about iPad notes again; the agent asks the customer's memory in their own words")
    cb = come_back(cli, ids, story, c, rec["id"])
    lines = [f"\n  “{cb['question']}” — memory of {c['name']}, top 3:"]
    lines += [f"    {i}. {'✓' if h['record'] else '·'} {clip(h['text'], 120)}" for i, h in enumerate(cb["hits"], 1)]
    lines.append(f"    the correction is #{cb['rank']}" if cb["rank"] else "    ✗ the correction is not in the top 3")
    emit("text", "\n".join(lines))
    out = dict(before=before, after=after, records=recs, left=left, come_back=dict(cb, customer=c["key"], name=c["name"]))
    emit("recalled", "", **out)
    return out


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    def facts() -> list:
        out, token = [], None
        while True:
            args = ["fact", "list", "--actors", actor_id, "--page-size", "50"]
            if token:
                args += ["--continuation-token", token]
            page = cli.run(*args, scoped=True, echo=False) or {}
            out.extend(page.get("items") or [])
            token = page.get("continuation_token")
            if not token:
                return out

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
    if found:
        note(f"deleted {len(found)} fact(s) on {actor_id} before the actor")
    return len(found)


def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    for c in story["customers"]:
        conv = find(cli, "conv", f"ticket-{c['key']}", echo=not quiet)
        if conv:
            cli.run("conv", "delete", conv["id"], scoped=True)   # `proj delete` does not delete conversations
    for p in PROJECTS.values():
        proj = find(cli, "proj", p["key"], echo=not quiet)
        if proj:
            cli.run("proj", "delete", proj["id"], scoped=True)
    for key in [c["key"] for c in story["customers"]] + [a["key"] for a in story["agents"].values()]:
        actor = find(cli, "actor", key, echo=not quiet)
        if actor:
            delete_actor(cli, actor["id"])
    note("deleted the four ticket conversations, both projects, the four customers' memory and both support agents")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    w = story["withdrawal"]
    banner(2, TOTAL, "Set up — a known-issues project, a ticket-history project, one actor per customer")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Known issues, as they stood in September — each with its current workaround")
    kis = pin_known_issues(cli, ids, story)
    banner(4, TOTAL, "What each customer was told — four tickets, the advice pinned on each customer's actor")
    cm = customer_memory(cli, ids, story)
    banner(5, TOTAL, f"Today ({story['today']}): {w['ki']}'s workaround is withdrawn — the issue is updated, the trace keeps the old step")
    kis = withdraw(cli, ids, story, kis)
    banner(6, TOTAL, "Two customers write in — history only, known issues only, and both joined")
    fu = follow_ups(cli, ids, story, kis)
    banner(7, TOTAL, "Who else was told? One query across every customer — then the corrections go into memory")
    rc = recall_and_correct(cli, ids, story, kis, fu)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   extracted=cm["extracted"], appended=cm["appended"], removed=cm["removed"],
                   readings={r["customer"]: dict(a=r["customer_only"]["judge"]["ok"], a_rank=r["customer_only"]["rank"],
                                                 b=r["ki_only"]["judge"]["ok"], b_top=r["ki_only"]["top"],
                                                 c=r["joined"]["judge"]["ok"],
                                                 cached=r["customer_only"]["cached"] + r["ki_only"]["cached"])
                             for r in fu["rows"]},
                   recall_rows=len(rc["before"]["rows"]), told=rc["before"]["told"], corrected=rc["after"]["corrected"],
                   left=rc["left"], come_back_rank=rc["come_back"]["rank"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    ids = {}
    for k, p in PROJECTS.items():
        proj = find(cli, "proj", p["key"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[k] = proj["id"]
    for c in story["customers"]:
        a = find(cli, "actor", c["key"], echo=False)
        if a is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        ids[c["key"]] = a["id"]
    return ids


def ask(cli: CLI, ids: dict, key: str, text: str, echo: bool = True) -> dict:
    """Any ticket text, for one customer: the three readings, side by side, each judged."""
    story = load_story()
    c = customer(story, key)
    kis = known_issues(cli, ids, echo=False)
    return three_readings(cli, ids, story, kis, c, text, echo=echo)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "issues", "customer", "recall", "ask", "cleanup"],
                    help="run = full demo (default); issues = the known issues and KI-204's trace; customer NAME = what "
                         "support told a customer; recall = who was given a withdrawn step, and who has been corrected; "
                         "ask NAME \"TICKET TEXT\" = the three readings for any ticket; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="customer (harbor / northfield / pinerowe / oakline) and, for `ask`, the ticket text")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "customer" and len(a.args) != 1:
        ap.error("customer needs a name: harbor, northfield, pinerowe or oakline")
    if a.command == "ask" and len(a.args) != 2:
        ap.error("ask needs a customer and the ticket text, e.g. ask northfield \"Notes from the iPad are missing on the Mac again\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py recall`, `python3 demo.py ask northfield \"Notes from the iPad are not on the "
              "Mac again, should we sign out?\"` or `python3 demo.py issues`; `python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if a.command == "issues":
        banner(2, 2, "Known issues — as they stand now, and KI-204's trace")
        kis = known_issues(cli, ids)
        show_kis(list(kis.values()))
        k = kis.get(story["withdrawal"]["ki"])
        if k:
            lines = [f"\n  fact trace {k['ki']} (newest first):"]
            for e in trace_of(cli, ids, k["id"]):
                lines.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}" + (f"  was: “…{clip(from_workaround(e['old']), 110)}”" if e["old"] else ""))
            emit("text", "\n".join(lines))
    elif a.command == "customer":
        c = customer(story, a.args[0])
        banner(2, 2, f"What support told {c['name']}")
        lines = [f"\n  {c['name']} (`fact list --actors …`):"]
        for m in memory_of(cli, ids[c["key"]]):
            lines.append(f"    {m['kind']:<10} {clip(m['text'], 120)}")
        emit("text", "\n".join(lines))
    elif a.command == "recall":
        banner(2, 2, f"Recall — who was given {story['withdrawal']['ki']}'s withdrawn step, and who has been corrected")
        kis = known_issues(cli, ids, echo=False)
        rc = recall(cli, ids, story, kis)
        show_recall(rc, story)
        k = kis[story["withdrawal"]["ki"]]
        for c in story["customers"]:
            if c["key"] in rc["told"]:
                mark = "✓ corrected" if c["key"] in rc["corrected"] else "✗ not told yet"
                emit("text", f"    {mark:<15} {c['name']}: {c['ticket']['id']} {c['ticket']['date']} — “{c['advice']['advised']}” "
                             f"(withdrawn {', '.join(w['on'] for w in k.get('withdrawn') or []) or '—'})")
    elif a.command == "ask":
        c = customer(story, a.args[0])
        banner(2, 2, f"Ask — a ticket from {c['name']}, three ways")
        show_readings(ask(cli, ids, c["key"], a.args[1]))


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

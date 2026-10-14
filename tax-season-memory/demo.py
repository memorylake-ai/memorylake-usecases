#!/usr/bin/env python3
"""
Tax season memory for accounting teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Lindqvist & Oduya CPAs (fictional) keeps its 2025 tax season in one MemoryLake
project: the client directory, last year's return exports, client emails and
voicemails, workpaper notes and team chat — one fact per record, word for word.
It is October 15, the extended deadline. Tom Brennan is on leave, and Priya
Nair picks up his two Okafor returns: Daniel Okafor Sr. (C-1187) and his son
Daniel Okafor Jr. (C-2290). Both sign their emails "Dan Okafor".

The quick read is what the page calls client context that loads automatically:
`search` the question, take the closest record on that topic that mentions the
client. For the son's Schedule D, the closest records are his father's — "my
$11,800 carryover from last year" — and the son's own export ("no capital loss
carryover") ranks below them, when it makes the top 8 at all. For the father's
estimated tax, the closest
record is the son's email: "$8,400 in total".

Every record is true. Whose it is lives only on the channel it came in by: the
sender address, the calling number, the workpaper binder, the SSN on an IRS
notice. The memory read takes every record that mentions the name (`fact list
--query`, an exact substring, every page) and resolves each channel against the
client directory (`fact list --query <address or number>`): one directory entry
→ that client; none, or no channel at all → nobody's, set aside.

Step 6 files each resolved record into the client's own memory (an actor per
client, `fact add --actor`, the full name and client id in the text and the
resolution in metadata), tags the record in the season file, and leaves the two
records nobody can place unfiled. The same quick read, scoped to the client's
own memory (`search --actors`), then answers three new questions from the right
person.

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-tsm"  # memorylake-usecases / tax-season-memory


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


SOURCE_KEYS = ("client", "channel", "binder", "ssn", "channels")
RESOLVED_KEYS = ("resolved_via", "filed_to", "unresolved")


def record_meta(r: dict) -> dict:
    """What the record itself carries. Exports and directory entries carry the client id; emails and voicemails only the
    address or number they came from; workpapers their binder; IRS notices the SSN. (No null values: the server drops
    keys whose value is null.)"""
    m = dict(key=f"tsm:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], by=r["by"], on=r["on"],
             topic=r["topic"], value=r.get("value") or "")
    m.update({k: r[k] for k in SOURCE_KEYS if r.get(k)})
    return m


# --------------------------------------------------------------------------- reading memory

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


def list_facts(cli: CLI, pid: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (EM-0402, RX-2290-D …), metadata flattened in, plus fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            out[m["record"]] = dict(m, fid=f["id"], body=f.get("fact") or "")
    return out


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def find_actor(cli: CLI, client: str, echo: bool = False):
    try:
        return cli.run("actor", "get", cid(client.lower()), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "tool", "kind", "by", "on", "topic", "value", "client", "channel", "binder",
                                  "ssn", "fid", "body", "rank", "resolved_via", "unresolved", "filed_to")}


def surname_of(story: dict, client: str) -> str:
    return story["clients"][client]["surname"]


def who(story: dict, client: str) -> str:
    c = story["clients"].get(client)
    return f"{c['name']} ({client})" if c else client


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, scope: list[str], top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, *scope, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", tool=m.get("tool") or "?", topic=m.get("topic") or "?"))
    return out


def hit_line(story: dict, h: dict, owner: str = "", mark: str = "") -> str:
    tool = story["tools"].get(h["tool"], h["tool"])
    tag = owner or h.get("client") or ""
    return f"      #{h['rank']:<3} {h['record']:<10} {tool:<16} {tag:<7} {clip(h['body'], 70)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, scope: list[str], mem: dict, q: dict, story: dict, echo: bool = True) -> dict:
    """The page's "client context that loads automatically", done the quick way: search the question, take the first
    hit on the question's topic that mentions the client's name and is not tagged with another client. Hits are read
    from `fact list` by fact id (search can answer from a cache of older text and metadata)."""
    hits = search(cli, q["text"], scope, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    name = surname_of(story, q["client"]).lower()
    for h in hits:
        cur = by_fid.get(h["fid"], h)
        tag = cur.get("client")
        if cur.get("topic") == q["topic"] and name in cur["body"].lower() and tag in (None, "", q["client"]):
            return dict(q=q, hits=hits, used=cur["record"], used_rank=h["rank"], used_body=cur["body"],
                        value=cur.get("value") or "", cached=cached)
    return dict(q=q, hits=hits, used="", used_rank=None, used_body="", value="", cached=cached)


# --------------------------------------------------------------------------- read 2: whose record is it?

def needle_of(r: dict) -> str:
    if r.get("channel"):
        return r["channel"]
    if r.get("ssn"):
        return f"SSN ending {r['ssn']}"
    return ""


def resolve(cli: CLI, pid: str, story: dict, mem: dict, surname: str, echo: bool = True) -> dict[str, dict]:
    """Every record that mentions the name (`fact list --query`, an exact substring, every page), each one resolved to
    a client by what it carries: a client id; a workpaper binder (binders are per client); or an address, number or SSN
    looked up in the client directory (`fact list --query <it>`) — exactly one directory entry, or nobody. Then the other
    way round: every address, number and SSN in those clients' directory entries, looked up in the records' text — a
    record that never uses the name (an IRS notice has only the SSN) is theirs too."""
    named = entries(pages(cli, ["fact", "list", "--projects", pid, "--query", surname], echo=echo))
    found_by: dict[str, list[str]] = {}

    def lookup(n: str) -> list[str]:
        if n not in found_by:
            found_by[n] = sorted({(f.get("metadata") or {}).get("record")
                                  for f in pages(cli, ["fact", "list", "--projects", pid, "--query", n], echo=echo)} - {None})
        return found_by[n]

    out = {}
    for rid in sorted(named, key=lambda x: (mem.get(x, named[x]).get("on", ""), x)):
        r = mem.get(rid, named[rid])
        src = {k: r.get(k) for k in SOURCE_KEYS}
        if r.get("resolved_via"):  # the client tag an earlier run added is not something the record carried
            src.pop("client", None)
        if src.get("client"):
            out[rid] = dict(owner=src["client"], via="client id on the record")
        elif src.get("binder"):
            out[rid] = dict(owner=src["binder"], via=f"workpaper binder {src['binder']}")
        elif needle_of(src):
            n = needle_of(src)
            dirs = [x for x in lookup(n) if mem.get(x, {}).get("kind") == "directory"]
            if len(dirs) == 1:
                out[rid] = dict(owner=mem[dirs[0]]["client"], via=f"{n} → {dirs[0]}")
            elif not dirs:
                out[rid] = dict(owner="", via=f"{n} is in no directory entry")
            else:
                out[rid] = dict(owner="", via=f"{n} is in {len(dirs)} directory entries")
        else:
            out[rid] = dict(owner="", via="no address, number, binder or client id on the record")
    for d in sorted(x for x in out if mem[x].get("kind") == "directory"):
        for n in mem[d].get("channels") or []:
            for rid in lookup(n):
                if rid not in out and mem.get(rid, {}).get("kind") != "directory":
                    out[rid] = dict(owner=mem[d]["client"], via=f"{n} in the text → {d}", unnamed=True)
    for rid, x in out.items():
        x["record"] = rid
        x.setdefault("unnamed", False)
    return out


def story_record(story: dict, rid: str) -> dict:
    return next((r for r in story["records"] if r["id"] == rid), {})


def memory_read(q: dict, mem: dict, res: dict[str, dict]) -> dict:
    """The question's topic, over every record that mentions the name: what belongs to this client, what belongs to
    someone else, and what nobody can place."""
    rows = []
    for rid, x in res.items():
        r = mem[rid]
        if r.get("topic") != q["topic"]:
            continue
        verdict = "used" if x["owner"] == q["client"] else ("set aside" if not x["owner"] else "not this client")
        rows.append(dict(record=rid, tool=r["tool"], value=r.get("value") or "", body=r["body"], owner=x["owner"],
                         via=x["via"], verdict=verdict))
    used = [r for r in rows if r["verdict"] == "used"]
    return dict(rows=rows, used=[r["record"] for r in used], value="; ".join(r["value"] for r in used) or "nothing on file")


def judge(quick: dict, res: dict[str, dict], client: str) -> bool:
    return bool(quick["used"]) and res.get(quick["used"], {}).get("owner") == client


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> str:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    p = story["project"]
    proj = find_project(cli, p["key"], echo=False)
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]), "--description",
                       p["description"], scoped=True)
    else:
        note("the project exists from an earlier run (pass --reset to start over)")
    emit("text", f"\n    project  {p['name']:<52} {proj['id']}")
    emit("setup", "", project=proj["id"], name=p["name"])
    return proj["id"]


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, scope: list[str], live_facts: list[dict], items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the entries the scope does not hold yet (matched by metadata key) — one `fact add` for all of them, then
    `fact update --metadata` on each (fact add takes no metadata). `scope` is ["--project", id] or ["--actor", id].
    Returns key → {id, new}."""
    live = {}
    for f in live_facts:
        k = (f.get("metadata") or {}).get("key")
        if k:
            live[k] = f
    out, missing = {}, []
    for text, meta in items:
        f = live.get(meta["key"])
        if f is None:
            missing.append((text, meta))
        else:
            out[meta["key"]] = dict(id=f["id"], new=False)
    if missing:
        res = cli.run("fact", "add", *scope, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, *scope, "--metadata", json.dumps(meta), scoped=True, echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way")
    return out


def restore_first_day(cli: CLI, pid: str, story: dict) -> int:
    """Every run starts on the morning Priya picks the returns up: records an earlier run tagged with a client are put
    back to what they carried when they came in. (The client files an earlier run made stay; step 6 finds them.)"""
    n = 0
    by_id = {r["id"]: r for r in story["records"]}
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        r = by_id.get(m.get("record"))
        if r and any(k in m for k in RESOLVED_KEYS):
            cli.run("fact", "update", f["id"], "--project", pid, "--metadata", json.dumps(record_meta(r)), scoped=True,
                    echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run tagged {n} record(s) with their client; put back, so this run starts from the same morning")
    return n


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid, story)
    note("one season file: the client directory, last year's return exports, client emails and voicemails, workpaper "
         "notes, team chat and the IRS notice log. One fact per record, word for word, then `fact update --metadata` "
         "with what the record itself carries (client id, sender address, calling number, binder, SSN)")
    got = pin(cli, ["--project", pid], list_facts(cli, pid, echo=False), [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    lines = []
    for tool, label in story["tools"].items():
        rs = [r for r in mem.values() if r["tool"] == tool]
        if rs:
            lines.append(f"    {label:<16} {len(rs):>2}   "
                         + ("client id on every record" if all(r.get("client") for r in rs) else
                            "binder " + ", ".join(sorted({r['binder'] for r in rs if r.get('binder')})) if all(r.get("binder") for r in rs) else
                            ", ".join(sorted({needle_of(r) or 'nothing' for r in rs}))))
    sur = surname_of(story, story["questions"][0]["client"])
    plain = sorted((r for r in mem.values() if not r.get("client") and sur.lower() in r["body"].lower()),
                   key=lambda r: (r["on"], r["record"]))
    lines.append(f"\n  {len(plain)} records that only say “{sur}” — whose they are is only in what they came in by:")
    for r in plain:
        lines.append(f"    {r['record']:<9} {story['tools'][r['tool']]:<14} {clip(needle_of(r) or ('binder ' + r['binder'] if r.get('binder') else '—'), 28):<28} "
                     f"{clip(r['body'], 92)}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['questions'])} open items on two returns. The quick read: `search` the question, take the first hit "
         "on that topic that mentions the client and is not tagged with another client")
    out = []
    for q in story["questions"]:
        r = quick_read(cli, ["--projects", pid], mem, q, story)
        lines = [f"\n  {q['id']} · return {who(story, q['client'])} · {q['title']}", f"  “{q['text']}”",
                 f"  search · top {len(r['hits'])}:"]
        for h in r["hits"]:
            lines.append(hit_line(story, h, mark="   ← used" if h["record"] == r["used"] else ""))
        if r["cached"]:
            lines.append(f"  · {len(r['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if r["used"]:
            lines.append(f"  → {r['used']}: “{clip(r['used_body'], 150)}”")
        lines.append(f"  → quick read: {r['value'] or 'no record on this topic in the top ' + str(len(r['hits']))}")
        emit("text", "\n".join(lines))
        out.append(r)
    emit("quick", "", reads=[dict(q=r["q"], used=r["used"], used_body=r["used_body"], value=r["value"],
                                  hits=[public(h) for h in r["hits"]], cached=r["cached"]) for r in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, mem: dict, quick: list[dict]) -> tuple[list[dict], dict]:
    sur = surname_of(story, story["questions"][0]["client"])
    note(f"the memory read: every record that says “{sur}” (`fact list --query`, an exact substring, every page), each "
         "resolved by what it carries — a client id, a workpaper binder, or an address, number or SSN looked up in the "
         "client directory (`fact list --query`); one directory entry or nobody")
    res = resolve(cli, pid, story, mem, sur)
    un = sum(1 for x in res.values() if x["unnamed"])
    lines = [f"\n  {len(res) - un} records say “{sur}”" + (f"; {un} more {'never uses' if un == 1 else 'never use'} the name but "
             f"{'carries' if un == 1 else 'carry'} an address, number or SSN from their directory entries" if un else "") + ":", f"    {'record':<10} {'came in by':<30} {'whose':<11} how"]
    for rid, x in res.items():
        r = mem[rid]
        lines.append(f"    {rid:<10} {clip(needle_of(r) or ('binder ' + r['binder'] if r.get('binder') else story['tools'][r['tool']]), 30):<30} "
                     f"{x['owner'] or '?':<11} {x['via'] if x['owner'] else 'not confirmed — ' + x['via']}")
    emit("text", "\n".join(lines))
    rows = []
    for r in quick:
        q = r["q"]
        mr = memory_read(q, mem, res)
        ok = judge(r, res, q["client"])
        lines = [f"\n  {q['id']} · return {who(story, q['client'])} · {q['title']}"]
        for x in mr["rows"]:
            mark = "  ← quick read" if x["record"] == r["used"] else ""
            lines.append(f"      {x['record']:<10} {story['tools'][x['tool']]:<14} {clip(x['value'], 34):<34} "
                         f"{x['owner'] or '?':<7} {x['verdict']}{mark}")
        qo = res.get(r["used"], {}).get("owner")
        lines.append(f"  → quick read:  {r['value'] or '—'}" + (f"  ({r['used']}, {who(story, qo) if qo else 'not confirmed'})" if r["used"] else ""))
        lines.append(f"  → memory read: {mr['value']}  ({', '.join(mr['used']) or 'none'})  {'✓ same record' if ok else '✗ differs'}")
        if not ok and q.get("stakes"):
            lines.append(f"  · on the return as the quick read has it: {q['stakes']}")
        emit("text", "\n".join(lines))
        rows.append(dict(q=q, quick=r["value"], used=r["used"], used_owner=qo or "", memory=mr, ok=ok))
    lines = [f"\n  {'item':<5} {'return':<26} {'the quick read says':<34} {'the client’s own records say':<30}", "  " + "-" * 104]
    for x in rows:
        lines.append(f"  {x['q']['id']:<5} {who(story, x['q']['client']):<26} {clip(x['quick'] or '—', 34):<34} "
                     f"{clip(x['memory']['value'], 30):<30} {'✓' if x['ok'] else '✗'}")
    wrong = [x for x in rows if not x["ok"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)}: the closest record with the right name belongs to the other "
                 f"{sur}" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", resolved=list(res.values()),
         rows=[dict(q=x["q"], quick=x["quick"], used=x["used"], used_owner=x["used_owner"], ok=x["ok"],
                    value=x["memory"]["value"], records=x["memory"]["rows"]) for x in rows])
    return rows, res


# --------------------------------------------------------------------------- step 6: file it under the client

def filed_text(r: dict, story: dict, client: str) -> str:
    """The record as it goes into the client's own memory: the full name and client id where it said only the name."""
    name = story["clients"][client]["name"]
    full = f"{name} ({client})"
    body = r["body"]
    if name in body:
        return body
    short = "Dan " + story["clients"][client]["surname"]
    if short in body:
        return body.replace(short, full, 1)
    return f"{full}: {body}"


def ensure_client_actor(cli: CLI, story: dict, client: str) -> str:
    a = find_actor(cli, client)
    c = story["clients"][client]
    if a is None:
        a = cli.run("actor", "create", "--custom-id", cid(client.lower()), "--display-name", f"{c['name']} ({client})",
                    "--description", f"{story['firm']} client {client}. Demo data.")
    try:
        cli.run("actor", "bind", "--actor", a["id"], scoped=True, echo=False)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return a["id"]


def write_back(cli: CLI, pid: str, story: dict, mem: dict, res: dict[str, dict]) -> dict:
    sur = surname_of(story, story["questions"][0]["client"])
    clients = sorted({x["owner"] for x in res.values() if x["owner"]})
    note(f"file each resolved record into its client's own memory — one actor per client, `fact add --actor` with the "
         f"full name and client id in the text, where it came from and how it was resolved in metadata. The season file "
         f"keeps the original record and gets the client tag; records nobody can place are tagged unresolved and filed "
         f"nowhere")
    actors, filed = {}, {}
    for c in clients:
        aid = ensure_client_actor(cli, story, c)
        actors[c] = aid
        items = []
        for rid, x in res.items():
            if x["owner"] != c:
                continue
            r = mem[rid]
            meta = dict(record_meta(story_record(story, rid)), key=f"tsm:{rid}:filed", client=c, resolved_via=x["via"],
                        source_project=pid, source_fact=r["fid"])
            items.append((filed_text(r, story, c), meta))
        got = pin(cli, ["--actor", aid], pages(cli, ["fact", "list", "--actors", aid], echo=False), items, echo=c == clients[0])
        filed[c] = dict(actor=aid, new=sum(1 for g in got.values() if g["new"]), total=len(got))
    tagged = 0
    for rid, x in res.items():
        r = mem[rid]
        if story_record(story, rid).get("client"):
            continue
        meta = record_meta(story_record(story, rid))
        if x["owner"]:
            meta.update(client=x["owner"], resolved_via=x["via"], filed_to=actors[x["owner"]])
        else:
            meta.update(unresolved=x["via"])
        cli.run("fact", "update", r["fid"], "--project", pid, "--metadata", json.dumps(meta), scoped=True, echo=tagged == 0)
        tagged += 1
    if tagged > 1:
        note(f"… and {tagged - 1} more records tagged the same way in the season file")
    if not any(f["new"] for f in filed.values()):
        note("the client files were made by an earlier run; reading them back")
    lines = []
    files = {}
    for c in clients:
        fs = entries(pages(cli, ["fact", "list", "--actors", actors[c]], echo=c == clients[0]))
        files[c] = fs
        lines.append(f"\n  {who(story, c)} — {len(fs)} records in the client's own memory ({actors[c]}):")
        for rid in sorted(fs, key=lambda x: (fs[x]["on"], x)):
            lines.append(f"    {rid:<10} {clip(fs[rid]['body'], 96)}")
    unres = [x for x in res.values() if not x["owner"]]
    lines.append(f"\n  {len(unres)} record(s) filed nowhere — nobody can say which {sur} they are:")
    for x in unres:
        lines.append(f"    {x['record']:<10} {clip(mem[x['record']]['body'], 80)}  ({x['via']})")
    emit("text", "\n".join(lines))
    # Later the same day: new wording (search answers a repeated question from its cache), each client's own memory.
    note("later the same day, three more questions — the same quick read, now scoped to the client's own memory "
         "(`search --actors`). Search gets new facts a few seconds after `fact list` does; each read is retried until "
         "it finds a record on its topic")
    time.sleep(6)
    after = []
    for q in story["followups"]:
        aid = actors.get(q["client"])
        fs = files.get(q["client"], {})
        t1, tries = time.time(), 0
        while True:
            tries += 1
            r = quick_read(cli, ["--actors", aid], fs, q, story, echo=tries == 1)
            if (r["used"] and not r["cached"]) or tries >= 8:
                break
            time.sleep(4)
        others = [h["record"] for h in r["hits"] if res.get(h["record"], {}).get("owner") not in (None, q["client"])]
        mr = memory_read(q, mem, res)
        ok = judge(r, res, q["client"])
        after.append(dict(q=q, used=r["used"], rank=r["used_rank"], value=r["value"], memory=mr["value"], ok=ok,
                          tries=tries, secs=int(time.time() - t1), cached=r["cached"], hits=len(r["hits"]), others=others))
    lines = []
    for a in after:
        q = a["q"]
        lines.append(f"\n  {q['id']} · {who(story, q['client'])} · “{q['text']}”")
        lines.append(f"    {a['hits']} hits in {q['client']}'s memory, {len(a['others'])} from anyone else · used {a['used'] or '—'} "
                     f"(#{a['rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''}): {a['value'] or '—'}  "
                     f"{'✓ same record as the memory read' if a['ok'] else '✗ differs from the memory read: ' + a['memory']}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    ok = sum(1 for a in after if a["ok"])
    lines.append(f"\n  → {ok} of {len(after)}: in the client's own memory, the closest record is the client's")
    emit("text", "\n".join(lines))
    out = dict(actors=actors, files={c: [public(r) for r in fs.values()] for c, fs in files.items()},
               unresolved=[dict(record=x["record"], via=x["via"], body=mem[x["record"]]["body"]) for x in unres],
               after=after, agree=ok)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, client

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def original_mem(mem: dict, story: dict) -> dict:
    """The season file as it came in: client tags an earlier run added are left out (they live in metadata only)."""
    out = {}
    for rid, r in mem.items():
        src = story_record(story, rid)
        out[rid] = {k: v for k, v in r.items() if k not in RESOLVED_KEYS and (k != "client" or src.get("client"))}
    return out


def ask_text(cli: CLI, pid: str, text: str, client: str, topic: str, echo: bool = True) -> dict:
    """Any question about one return, three ways: the quick read over the season file as it came in (name only), the
    memory read (every record with the name, resolved by channel), and — once a run has filed the records — the
    quick read in the client's own memory."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=False))
    q = dict(id="(yours)", client=client, topic=topic, title="", text=text)
    om = original_mem(mem, story)
    quick = quick_read(cli, ["--projects", pid], om, q, story, echo=echo)
    # Search hits carry today's metadata; show each one with only the client tag it came in with.
    quick["hits"] = [dict(h, client=om.get(h["record"], {}).get("client") or "") for h in quick["hits"]]
    res = resolve(cli, pid, story, mem, surname_of(story, client), echo=echo)
    mr = memory_read(q, mem, res)
    out = dict(q=q, hits=[public(h) for h in quick["hits"]], used=quick["used"], used_body=quick["used_body"],
               quick=quick["value"], used_owner=res.get(quick["used"], {}).get("owner", ""), memory=mr,
               ok=judge(quick, res, client), filed=None)
    a = find_actor(cli, client)
    if a:
        fs = entries(pages(cli, ["fact", "list", "--actors", a["id"]], echo=False))
        if fs:
            fr = quick_read(cli, ["--actors", a["id"]], fs, q, story, echo=echo)
            out["filed"] = dict(actor=a["id"], used=fr["used"], value=fr["value"], ok=judge(fr, res, client),
                                hits=[public(h) for h in fr["hits"]])
    return out


def client_text(cli: CLI, pid: str, client: str, echo: bool = True) -> dict:
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=False))
    res = resolve(cli, pid, story, mem, surname_of(story, client), echo=echo)
    mine = [dict(public(mem[rid]), via=x["via"]) for rid, x in res.items() if x["owner"] == client]
    unres = [dict(record=x["record"], via=x["via"], body=mem[x["record"]]["body"]) for x in res.values() if not x["owner"]]
    others = sorted({x["owner"] for x in res.values() if x["owner"] and x["owner"] != client})
    a = find_actor(cli, client)
    filed = len(pages(cli, ["fact", "list", "--actors", a["id"]], echo=False)) if a else 0
    return dict(client=client, name=story["clients"][client]["name"], records=mine, unresolved=unres, others=others,
                actor=a["id"] if a else "", filed=filed)


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
    gone = 0
    for c in story["clients"]:
        a = find_actor(cli, c)
        if a:
            delete_actor(cli, a["id"])
            gone += 1
    proj = find_project(cli, story["project"]["key"], echo=not quiet)
    if not proj and not gone:
        note("nothing to delete")
        emit("cleaned", "")
        return
    if proj:
        facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        for f in facts:
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        note(f"deleted the project and its {len(facts)} facts")
    if gone:
        note(f"deleted {gone} client actor(s) and their filed records")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one project for the season's client file")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: directory, returns, emails, voicemails, notes")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the closest record with the client's name")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — whose record is it?")
    rows, res = memory_review(cli, pid, story, mem, quick)
    banner(6, TOTAL, "File it under the client — then the quick read again")
    wb = write_back(cli, pid, story, mem, res)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   quick={x["q"]["id"]: [x["used"], x["used_owner"], x["quick"]] for x in rows},
                   ranks={q["q"]["id"]: q["used_rank"] for q in quick},
                   hits={q["q"]["id"]: [h["record"] for h in q["hits"]] for q in quick},
                   memory={x["q"]["id"]: x["memory"]["value"] for x in rows},
                   ok={x["q"]["id"]: x["ok"] for x in rows},
                   unresolved=[u["record"] for u in wb["unresolved"]],
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["q"]["id"]: [a["used"], a["rank"], a["tries"], a["ok"], len(a["others"])] for a in wb["after"]},
                   after_agree=wb["agree"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def print_ask(story: dict, r: dict) -> None:
    q = r["q"]
    lines = [f"\n  “{q['text']}” · return {who(story, q['client'])} · topic {q['topic']}",
             f"  the season file, as it came in — top {len(r['hits'])}:"]
    lines += [hit_line(story, h, mark="   ← used" if h["record"] == r["used"] else "") for h in r["hits"]]
    lines.append(f"  → quick read:  {r['quick'] or '—'}" + (f"  ({r['used']}, {who(story, r['used_owner']) if r['used_owner'] else 'not confirmed'})" if r["used"] else ""))
    for x in r["memory"]["rows"]:
        lines.append(f"      {x['record']:<10} {clip(x['value'], 34):<34} {x['owner'] or '?':<7} {x['verdict']:<16} {x['via']}")
    lines.append(f"  → memory read: {r['memory']['value']}  {'✓ same record' if r['ok'] else '✗ differs'}")
    f = r.get("filed")
    if f:
        lines.append(f"  → in {q['client']}'s own memory ({f['actor']}): {f['value'] or '—'} ({f['used'] or 'no record on this topic'})  "
                     f"{'✓' if f['ok'] else '✗'}")
    else:
        lines.append(f"  · {q['client']} has no client file yet — `python3 demo.py` files the records in step 6")
    emit("text", "\n".join(lines))


def main() -> None:
    story = load_story()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "client", "cleanup"],
                    help="run = full demo (default); ask \"QUESTION\" --client C-2290 --topic carryover = the quick read, "
                         "the memory read and the client's own memory side by side; client C-1187 = every record that "
                         "is this client's, with how it was resolved; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask) or the client id (client)")
    ap.add_argument("--client", default="C-2290", choices=["C-1187", "C-2290"], help="ask: whose return the question is for")
    ap.add_argument("--topic", default="carryover", choices=["carryover", "estimates", "state", "notice", "business", "income"],
                    help="ask: what the question is about")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command in ("ask", "client") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. ask \"What carryover goes on Dan Okafor's Schedule D?\" --client "
                 "C-2290 --topic carryover, or client C-1187")
    if a.command == "client" and a.args[0] not in ("C-1187", "C-2290"):
        ap.error("client takes C-1187 (Daniel Okafor Sr.) or C-2290 (Daniel Okafor Jr.)")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py client C-2290`, `python3 demo.py ask \"How much estimated tax did Dan Okafor "
              "send in for 2025?\" --client C-1187 --topic estimates`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — the quick read, the memory read, the client's own memory")
        print_ask(story, ask_text(cli, pid, a.args[0], a.client, a.topic))
    elif a.command == "client":
        banner(2, 2, f"Client — every record that is {a.args[0]}'s")
        c = client_text(cli, pid, a.args[0])
        lines = [f"\n  {c['name']} ({c['client']}) — {len(c['records'])} records resolved to this client:",
                 f"    {'record':<10} {'on':<10} {'topic':<10} {'how':<44} record"]
        for r in sorted(c["records"], key=lambda r: (r["on"], r["record"])):
            lines.append(f"    {r['record']:<10} {r['on']:<10} {r['topic']:<10} {clip(r['via'], 44):<44} {clip(r['body'], 70)}")
        lines.append(f"\n  same name, someone else's: {', '.join(who(story, o) for o in c['others']) or 'none'}")
        lines.append(f"  not confirmed — filed nowhere: " + ("; ".join(f"{u['record']} ({u['via']})" for u in c["unresolved"]) or "none"))
        lines.append(f"  client file: " + (f"{c['filed']} records on {c['actor']}" if c["actor"] else "not made yet (run the demo)"))
        emit("text", "\n".join(lines))


def and_list(xs: list[str]) -> str:
    if len(xs) < 2:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


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

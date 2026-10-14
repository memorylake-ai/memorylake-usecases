#!/usr/bin/env python3
"""
Competitive intel memory for sales teams — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Lumora (fictional) sells team-scheduling software against Pendrel.  Every piece
of competitive intel its sales team logs is one fact in a MemoryLake project,
word for word: analyst newsletters, Slack digests, battle cards, deal notes,
pricing-page captures, loss debriefs.  Each carries metadata: who logged it,
when, the claim it makes, and what it was copied from (`derived_from`).

Ten records are about one question: does Pendrel include SSO on its Business
plan?  Seven say no — "SSO is Enterprise-only from September 1".  Three say
yes.  Three quick reads — search's top 8, a vote over every record, the newest
record — all answer "Enterprise-only".  Walk each record back along
`derived_from` and the seven collapse into one source: an analyst newsletter
of August 18, which printed a correction on August 25.  Four of the seven were
written after that correction was already in memory.  The three "yes" records
are three independent sources: the correction itself, Pendrel's pricing page,
and a loss debrief.  Repetition is not corroboration, and a retraction does
not reach the copies by itself.

Step 6 writes the lineage back (`fact update --metadata` on every copy, and a
new battle card citing its basis), lists the prospects who were told the wrong
thing, and re-runs the same search: the ranking does not move — search does
not read status — but anything that reads the metadata now gets it right.

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
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-cim"  # memorylake-usecases / competitive-intel-memory


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


SHORT = {"sso-enterprise": "ENT-ONLY", "sso-business": "BUSINESS"}


def short(claim: str) -> str:
    return SHORT.get(claim or "", "-")


def record_text(r: dict) -> str:
    return r["text"]


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null, so absent fields are simply left out.
    m = dict(key=f"intel:{r['id']}", record=r["id"], kind=r["kind"], logged_by=r["logged_by"], logged_on=r["logged_on"],
             topic=r["topic"], claim=r.get("claim") or "none", derived_from=list(r.get("derived_from") or []))
    for k in ("account", "source", "corrects", "supersedes"):
        if r.get(k):
            m[k] = r[k]
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


def list_facts(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", project_id], echo=echo)


def find(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (I01 …), metadata flattened in, plus the fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            out[m["record"]] = dict(m, fid=f["id"], body=f.get("fact") or "",
                                    derived_from=list(m.get("derived_from") or []))
    return out


def as_of(mem: dict, day: str, include_day: bool = True) -> dict:
    """The records logged on or before `day` (or strictly before it)."""
    return {k: r for k, r in mem.items() if (r["logged_on"] <= day if include_day else r["logged_on"] < day)}


def kind_name(story: dict, k: str) -> str:
    return story["kinds"].get(k, k)


def who(story: dict, r: dict) -> str:
    return f"{kind_name(story, r['kind'])}{' · ' + r['account'] if r.get('account') else ''}"


# --------------------------------------------------------------------------- the lineage audit (plain code, no model)

def roots_of(mem: dict, rid: str, seen: set | None = None) -> set:
    """Follow derived_from up to the records that were copied from nothing."""
    seen = seen if seen is not None else set()
    if rid in seen:
        return set()
    seen.add(rid)
    r = mem.get(rid)
    if r is None:
        return {rid}  # a parent that is not in memory (yet) counts as its own source
    if not r["derived_from"]:
        return {rid}
    out = set()
    for p in r["derived_from"]:
        out |= roots_of(mem, p, seen)
    return out


def audit(story: dict, mem: dict, day: str) -> dict:
    """Per claim on the topic: records, distinct sources, sources still standing on `day`."""
    view = as_of(mem, day)
    topic = {k: r for k, r in view.items() if r["topic"] == story["topic"] and r["claim"] in story["claims"]}
    retracted = {}  # source record → the correction that withdrew it
    for r in view.values():
        if r["kind"] == "correction" and r.get("corrects"):
            retracted[r["corrects"]] = dict(by=r["record"], on=r["logged_on"])
    claims = {}
    for c in story["claims"]:
        recs = sorted((r for r in topic.values() if r["claim"] == c), key=lambda r: (r["logged_on"], r["record"]))
        roots = sorted({x for r in recs for x in roots_of(view, r["record"])})
        live = [x for x in roots if x not in retracted]
        claims[c] = dict(claim=c, name=story["claims"][c], records=[r["record"] for r in recs], roots=roots, live=live,
                         retracted=[x for x in roots if x in retracted])
    # every copy of a retracted source, and which of them were written after the correction was in memory
    lineage = []
    for r in sorted(topic.values(), key=lambda r: (r["logged_on"], r["record"])):
        rs = roots_of(view, r["record"])
        dead = sorted(x for x in rs if x in retracted)
        if dead and all(x in retracted for x in rs):
            src = dead[0]
            lineage.append(dict(record=r["record"], root=src, by=retracted[src]["by"], since=retracted[src]["on"],
                                after=r["logged_on"] > retracted[src]["on"] and r["record"] != src))
    ranked = sorted(claims.values(), key=lambda c: (-len(c["live"]), -len(c["records"]), c["claim"]))
    verdict = ranked[0]["claim"] if ranked and ranked[0]["live"] else None
    told = []
    for x in lineage:
        r = view[x["record"]]
        if r["kind"] == "deal" and r.get("account"):
            lost = next((d for d in view.values() if d["kind"] == "debrief" and d.get("account") == r["account"]
                         and d["logged_on"] > r["logged_on"] and "lost" in d["body"].lower()), None)
            told.append(dict(account=r["account"], record=r["record"], by=r["logged_by"], on=r["logged_on"],
                             lost=lost["record"] if lost else None, lost_on=lost["logged_on"] if lost else None))
    return dict(day=day, claims=claims, verdict=verdict, retracted=retracted, lineage=lineage, told=told,
                topic_records=sorted(topic, key=lambda k: (topic[k]["logged_on"], k)))


def tree_lines(story: dict, mem: dict, day: str, a: dict) -> list[str]:
    """The topic's records drawn as a forest: each source, then what was copied from it."""
    view = as_of(mem, day)
    ids = set(a["topic_records"])
    kids = {}
    joins = []  # records that cite several others (a new battle card): drawn once, on their own line
    for k in sorted(ids, key=lambda k: (view[k]["logged_on"], k)):
        if len(view[k]["derived_from"]) > 1:
            joins.append(k)
            continue
        for p in view[k]["derived_from"]:
            kids.setdefault(p, []).append(k)
    roots = sorted({x for k in ids for x in roots_of(view, k)}, key=lambda k: (view.get(k, {}).get("logged_on", ""), k))
    lines = []

    def tag(k: str) -> str:
        r = view[k]
        t = f"{k} {r['logged_on']}  {short(r['claim']):<8}  {who(story, r)} ({r['logged_by']})"
        if k in a["retracted"]:
            t += f"   ✗ withdrawn by {a['retracted'][k]['by']} ({a['retracted'][k]['on']})"
        elif r["kind"] == "correction":
            t += f"   corrects {r.get('corrects')}"
        x = next((x for x in a["lineage"] if x["record"] == k), None)
        if x and x["after"]:
            t += "   ← written after the correction"
        return t

    def walk(k: str, prefix: str, last: bool, top: bool) -> None:
        lines.append("    " + (tag(k) if top else prefix + ("└─ " if last else "├─ ") + tag(k)))
        inner = "" if top else prefix + ("   " if last else "│  ")
        ch = kids.get(k, [])
        for i, c in enumerate(ch):
            walk(c, inner, i == len(ch) - 1, False)

    for k in roots:
        if k in view:
            walk(k, "", True, True)
    for k in joins:
        lines.append(f"    {tag(k)}   cites {', '.join(view[k]['derived_from'])}")
    return lines


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> str:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    p = story["project"]
    proj = find(cli, p["key"])
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]),
                       "--description", p["description"], scoped=True)
        note(f"created “{p['name']}” → {proj['id']}")
    else:
        note(f"“{p['name']}” already exists → {proj['id']} (pass --reset to start over)")
    emit("setup", "", project=proj["id"], name=p["name"], description=p["description"])
    return proj["id"]


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, project_id: str, items: list[tuple[str, dict]], what: str, echo: bool = True) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key) — one `fact add` for all of
    them, then `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new}."""
    live = {}
    for f in list_facts(cli, project_id, echo=False):
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
        res = cli.run("fact", "add", "--project", project_id, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, "--project", project_id, "--metadata", json.dumps(meta), scoped=True,
                    echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way ({what})")
    return out


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "kind", "account", "logged_by", "logged_on", "topic", "claim", "derived_from",
                                  "corrects", "supersedes", "source", "status", "retracted_by", "superseded_by", "basis",
                                  "fid", "body")}


def intake(cli: CLI, pid: str, story: dict) -> dict:
    note("one fact per record, word for word, each with metadata: kind, who logged it, when, the claim it makes, "
         "and derived_from — the record(s) it was copied from, as the person logging it said")
    got = pin(cli, pid, [(record_text(r), record_meta(r)) for r in story["records"]],
              "kind, logged_by, logged_on, topic, claim, derived_from")
    if not any(g["new"] for g in got.values()):
        note("the records were pinned by an earlier run; reading them back")
    mem = entries(list_facts(cli, pid))
    view = as_of(mem, story["today"], include_day=False)
    lines = [f"\n  {len(view)} records logged before today ({story['today']}); "
             f"{sum(1 for r in view.values() if r['topic'] == story['topic'])} of them about SSO"]
    for r in sorted(view.values(), key=lambda r: (r["logged_on"], r["record"])):
        src = ("from " + ",".join(r["derived_from"])) if r["derived_from"] else ("corrects " + r["corrects"]) if r.get("corrects") else "-"
        lines.append(f"    {r['record']} {r['logged_on']}  {short(r['claim']):<8} {who(story, r):<38} {src:<12} "
                     f"{clip(r['body'], 64)}")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(view.values(), key=lambda r: (r["logged_on"], r["record"]))],
         kinds=story["kinds"], claims=story["claims"], topic=story["topic"])
    return mem


# --------------------------------------------------------------------------- step 4: three quick reads

def search(cli: CLI, q: str, pid: str, top_k: int, mem: dict, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    by_fid = {r["fid"]: r for r in mem.values()}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        r = by_fid.get(f.get("id")) or {}
        out.append(dict(rank=f.get("rank") or i, score=f.get("score"), fid=f.get("id"), body=f.get("fact"),
                        record=m.get("record") or r.get("record"), claim=m.get("claim") or r.get("claim") or "none",
                        kind=m.get("kind") or r.get("kind"), logged_on=m.get("logged_on") or r.get("logged_on") or "",
                        status=m.get("status") or "", topic=m.get("topic") or r.get("topic")))
    return out


def vote(story: dict, items: list[dict]) -> dict:
    v = {c: 0 for c in story["claims"]}
    for x in items:
        if x.get("claim") in v:
            v[x["claim"]] += 1
    return v


def leader(v: dict) -> str | None:
    top = sorted(v.items(), key=lambda x: (-x[1], x[0]))
    return top[0][0] if top and top[0][1] else None


def hit_lines(story: dict, hits: list[dict], today: str, show_status: bool = False) -> list[str]:
    lines = [f"    {'#':>3}  {'score':>6}  {'claim':<8}  hit"]
    for h in hits:
        late = h["logged_on"] >= today
        st = f"  [{h['status']}]" if show_status and h["status"] else ""
        lines.append(f"    {h['rank']:>3}  {(h['score'] or 0):>6.3f}  {short(h['claim']):<8}  {h['record'] or '?'} "
                     f"{clip(h['body'], 72)}{st}{'   (logged today — not part of this morning’s read)' if late and not show_status else ''}")
    return lines


def quick_reads(cli: CLI, pid: str, story: dict, mem: dict) -> dict:
    day = story["today"]
    view = as_of(mem, day, include_day=False)
    sso = [r for r in view.values() if r["topic"] == story["topic"] and r["claim"] in story["claims"]]
    reads = []

    # 1. ask search, the way a rep's AI tool would
    q = story["question"]
    note(f"read 1 — {story['rep']['name']} asks the way an AI tool would: search “{q}” and go with what most hits say")
    hits = search(cli, q, pid, 8, mem)
    counted = [h for h in hits if h["logged_on"] < day]
    v = vote(story, counted)
    lines = [""] + hit_lines(story, hits, day)
    lines.append(f"\n  → among the hits: Enterprise-only {v['sso-enterprise']} · Business {v['sso-business']}"
                 f" · not about SSO {sum(1 for h in counted if h['claim'] not in story['claims'])}"
                 f" — “{story['claims'].get(leader(v), 'no answer')}”")
    emit("text", "\n".join(lines))
    reads.append(dict(read="search top 8", winner=leader(v), votes=v, hits=hits))

    # 2. a vote over every record
    note("read 2 — read every record instead (`fact list --projects`, every page) and count the claims")
    v2 = vote(story, sso)
    emit("text", f"\n  every SSO record: Enterprise-only {v2['sso-enterprise']} · Business {v2['sso-business']}"
                 f"\n  → {v2['sso-enterprise']} to {v2['sso-business']}: “{story['claims'][leader(v2)]}”")
    reads.append(dict(read="every record, counted", winner=leader(v2), votes=v2))

    # 3. the newest word
    newest = sorted(sso, key=lambda r: (r["logged_on"], r["record"]))[-1]
    note("read 3 — trust the newest record on the topic")
    emit("text", f"\n  newest: {newest['record']} {newest['logged_on']} {who(story, newest)} ({newest['logged_by']})"
                 f"\n    “{clip(newest['body'], 120)}”\n  → “{story['claims'][newest['claim']]}”")
    reads.append(dict(read="newest record", winner=newest["claim"], record=public(newest)))
    out = dict(question=q, reads=reads, all_agree=len({r["winner"] for r in reads}) == 1, winner=reads[0]["winner"])
    emit("quick_reads", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: walk the lineage

def show_audit(story: dict, mem: dict, a: dict, control: dict | None = None) -> None:
    lines = [f"\n  as of {a['day']}: every SSO record, drawn from its source down to its copies"]
    lines += tree_lines(story, mem, a["day"], a)
    lines.append(f"\n    {'claim':<36} {'records':>7} {'sources':>7} {'standing':>8}")
    for c in a["claims"].values():
        lines.append(f"    {c['name']:<36} {len(c['records']):>7} {len(c['roots']):>7} {len(c['live']):>8}"
                     f"   {', '.join(c['live']) or '—'}{'   (withdrawn: ' + ', '.join(c['retracted']) + ')' if c['retracted'] else ''}")
    after = [x["record"] for x in a["lineage"] if x["after"]]
    if a["lineage"]:
        src = a["lineage"][0]["root"]
        lines.append(f"\n  → {len(a['lineage'])} records rest on {src}, withdrawn by {a['retracted'][src]['by']} on "
                     f"{a['retracted'][src]['on']}; " + (f"{len(after)} of them ({', '.join(after)}) were written after that "
                     "correction was already in memory" if after else "none of them was written after the correction"))
    lines.append(f"  → standing sources decide: “{story['claims'].get(a['verdict'], 'no standing source')}”")
    if control:
        cc = control["claims"]
        lines.append(f"\n  control — the same audit as of {control['day']}, before the correction existed:")
        for c in cc.values():
            lines.append(f"    {c['name']:<36} {len(c['records']):>7} records → {len(c['live'])} standing source(s)")
        lines.append(f"    → “{story['claims'].get(control['verdict'], '—')}”, on {len(cc[control['verdict']]['live'])} source"
                     f" — not {len(cc[control['verdict']]['records'])} votes. The audit takes no side; it folds copies back into their source.")
    emit("text", "\n".join(lines))


def provenance(cli: CLI, pid: str, story: dict, quick: dict | None = None) -> dict:
    note("the same `fact list`, read differently: follow derived_from up to the record that was copied from "
         "nothing, and check whether a correction in memory withdrew it — plain code over the metadata, no model")
    mem = entries(list_facts(cli, pid))
    # this morning's memory: today's write-back (step 6) is left out, so a second run reads the same thing
    view = as_of(mem, story["today"], include_day=False)
    a = audit(story, view, story["today"])
    ctl = audit(story, view, story["audit_control_day"])
    show_audit(story, view, a, ctl)
    out = dict(audit=a, control=ctl, tree=tree_lines(story, view, a["day"], a),
               flipped=bool(quick and quick.get("winner") != a["verdict"]))
    emit("provenance", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: write it back

def write_back(cli: CLI, pid: str, story: dict, prov: dict) -> dict:
    day, q = story["today"], story["question"]
    a = prov["audit"]
    mem = entries(list_facts(cli, pid, echo=False))
    marked, already = [], []
    note("mark every copy of the withdrawn source: `fact update --metadata` (the whole object — the old keys plus "
         "status, retracted_by, root). The text stays word for word")
    first = True
    for x in a["lineage"]:
        r = mem[x["record"]]
        if r.get("status") == "retracted-upstream" and r.get("retracted_by") == x["by"]:
            already.append(x["record"])
            continue
        meta = {k: v for k, v in r.items() if k not in ("fid", "body")}
        meta.update(status="retracted-upstream", retracted_by=x["by"], root=x["root"], flagged_on=day)
        cli.run("fact", "update", r["fid"], "--project", pid, "--metadata", json.dumps(meta), scoped=True, echo=first)
        first = False
        marked.append(x["record"])
    if len(marked) > 1:
        note(f"… and {len(marked) - 1} more the same way ({', '.join(marked[1:])})")
    if already and not marked:
        note(f"all {len(already)} copies were marked by an earlier run")
    # the new battle card, citing the sources that are still standing
    v5 = dict(story["card_v5"])
    basis = a["claims"][a["verdict"]]["live"] if a["verdict"] else []
    meta = record_meta(dict(v5, derived_from=basis))
    meta.update(basis=basis)
    pin(cli, pid, [(v5["text"], meta)], "battle card v5")
    mem = entries(list_facts(cli, pid, echo=False))
    old = mem.get(v5["supersedes"])
    if old and old.get("superseded_by") != v5["id"]:
        m = {k: v for k, v in old.items() if k not in ("fid", "body")}
        m.update(superseded_by=v5["id"])
        cli.run("fact", "update", old["fid"], "--project", pid, "--metadata", json.dumps(m), scoped=True)
    mem = entries(list_facts(cli, pid))
    card = mem[v5["id"]]
    cited = [b for b in card.get("basis") or [] if b in mem]
    lines = [f"\n  battle card v5 ({card['record']}, {card['fid']}) — basis {', '.join(card.get('basis') or [])}, "
             f"cited {len(cited)}/{len(card.get('basis') or [])} back to records in memory; supersedes {card.get('supersedes')}:"]
    for b in card.get("basis") or []:
        r = mem.get(b)
        lines.append(f"    {b} {r['logged_on']}  {who(story, r):<34} “{clip(r['body'], 70)}”" if r else f"    {b}  (not in memory)")
    emit("text", "\n".join(lines))

    # who was told the wrong thing
    lines = [f"\n  prospects told “{story['claims']['sso-enterprise']}” — every deal note in the withdrawn lineage:"]
    for t in a["told"]:
        state = f"lost {t['lost_on']} ({t['lost']})" if t["lost"] else "open"
        if t["account"] in story["rep"]["call"]:
            state += f" — {story['rep']['name']} calls them tomorrow"
        lines.append(f"    {t['account']:<24} told {t['on']} by {t['by']:<18} ({t['record']})   {state}")
    emit("text", "\n".join(lines))

    # the same search again
    note("the same search again — after the write-back")
    hits = search(cli, q, pid, 8, mem)
    before = [h["record"] for h in prov.get("search_before") or [] if h["record"] != v5["id"]]
    after = [h["record"] for h in hits if h["record"] != v5["id"]]
    common = [x for x in after if x in before]
    same = common == [x for x in before if x in after]
    card_rank = next((h["rank"] for h in hits if h["record"] == v5["id"]), None)
    lines = [""] + hit_lines(story, hits, day, show_status=True)
    standing = [h for h in hits if h["claim"] in story["claims"] and h["status"] != "retracted-upstream"]
    v = vote(story, standing)
    lines.append(f"\n  → search does not read status: {sum(1 for h in hits if h['status'] == 'retracted-upstream')} of the "
                 f"{len(hits)} hits are marked retracted-upstream and still come back")
    lines.append(f"  → read the metadata with them: standing hits say Business {v['sso-business']} · "
                 f"Enterprise-only {v['sso-enterprise']} — “{story['claims'].get(leader(v), 'no answer')}”")
    emit("text", "\n".join(lines))
    out = dict(marked=marked, already=already, card=public(card), cited=len(cited), told=a["told"], hits=hits,
               same_order=same if before else None, card_rank=card_rank,
               standing_votes=v, answer=leader(v), retracted_hits=sum(1 for h in hits if h["status"] == "retracted-upstream"))
    emit("written", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask

def ask(cli: CLI, pid: str, q: str, echo: bool = True) -> dict:
    """Any question: search's top 8 as it comes back, with each hit's claim and status read from its metadata."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=False))
    hits = search(cli, q, pid, 8, mem, echo=echo)
    a = audit(story, mem, story["today"])
    return dict(q=q, hits=hits, verdict=a["verdict"], verdict_name=story["claims"].get(a["verdict"]),
                lineage=[x["record"] for x in a["lineage"]])


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find(cli, story["project"]["key"], echo=not quiet)
    if proj:
        for f in list_facts(cli, proj["id"], echo=False):
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = list_facts(cli, proj["id"], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True)
    note("deleted the intel project and every record on it")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one project for every piece of competitive intel")
    pid = setup(cli, story, reset)
    cli.ids = dict(project=pid)  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Intake — 14 records: newsletters, Slack, battle cards, deal notes, a pricing page, debriefs")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, f"Three quick reads — “{story['question']}”")
    quick = quick_reads(cli, pid, story, mem)
    banner(5, TOTAL, "Walk the lineage — every record back to its source")
    prov = provenance(cli, pid, story, quick)
    prov["search_before"] = quick["reads"][0]["hits"]
    banner(6, TOTAL, "Write it back — mark the copies, a new battle card, who was told what")
    wb = write_back(cli, pid, story, prov)
    a = prov["audit"]
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   quick={r["read"]: r["winner"] for r in quick["reads"]},
                   search_votes=quick["reads"][0]["votes"], record_votes=quick["reads"][1]["votes"],
                   search_order=[h["record"] for h in quick["reads"][0]["hits"]],
                   claims={c: dict(records=len(x["records"]), sources=len(x["roots"]), standing=len(x["live"]))
                           for c, x in a["claims"].items()},
                   verdict=a["verdict"], control=prov["control"]["verdict"],
                   lineage=[x["record"] for x in a["lineage"]], after=[x["record"] for x in a["lineage"] if x["after"]],
                   told=[t["account"] for t in a["told"]], marked=wb["marked"], already=wb["already"],
                   cited=wb["cited"], search_after=[h["record"] for h in wb["hits"]],
                   retracted_hits=wb["retracted_hits"], answer=wb["answer"], same_order=wb["same_order"],
                   card_rank=wb["card_rank"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_project(cli: CLI) -> str:
    story = load_story()
    proj = find(cli, story["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "lineage", "audit", "ask", "cleanup"],
                    help="run = full demo (default); lineage = every SSO record as a tree, with its status today; "
                         "audit DATE = the lineage audit as of a date (YYYY-MM-DD); ask \"QUESTION\" = search's top 8 "
                         "with each hit's claim and status; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the date (audit) or the question (ask)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one argument, e.g. ask \"Does Pendrel include SSO on its Business plan?\"")
    if a.command == "audit" and (len(a.args) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.args[0])):
        ap.error("audit needs one date, e.g. audit 2026-08-24")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py lineage`, `python3 demo.py audit 2026-08-24` or "
              "`python3 demo.py ask \"Why did we lose Kilnworth Foods?\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command in ("lineage", "audit"):
        day = a.args[0] if a.command == "audit" else story["today"]
        banner(2, 2, f"The SSO records as of {day} — sources, copies, what still stands")
        mem = entries(list_facts(cli, pid))
        au = audit(story, mem, day)
        show_audit(story, mem, au)
        if a.command == "lineage":
            marked = [r["record"] for r in mem.values() if r.get("status") == "retracted-upstream"]
            emit("text", f"\n  marked retracted-upstream in memory: {', '.join(sorted(marked)) or 'none yet'}")
    elif a.command == "ask":
        banner(2, 2, "Ask — what search returns, with each hit's claim and status from its metadata")
        r = ask(cli, pid, a.args[0])
        lines = [f"\n  “{r['q']}” — search top {len(r['hits'])}:"] + hit_lines(story, r["hits"], "9999", show_status=True)
        lines.append(f"\n  standing sources on SSO today: “{r['verdict_name'] or '—'}”"
                     f"{' · copies of a withdrawn source: ' + ', '.join(r['lineage']) if r['lineage'] else ''}")
        emit("text", "\n".join(lines))


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

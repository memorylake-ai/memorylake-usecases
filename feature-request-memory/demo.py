#!/usr/bin/env python3
"""
Feature request memory for small SaaS teams — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Driftline (fictional, six people) sells dispatch software.  Its product manager
plans the Q4 roadmap next week.  Since May, 17 feature requests have come in
through support tickets, sales-call notes, the Canny board and shared Slack
channels, from seven accounts.  Every request is one fact in a MemoryLake
project, pinned word for word, with the account, channel, date, theme and
urgency as metadata; every account is one fact with its plan, ARR and renewal.

Four quick ways to read that memory all say "build dark mode next":
the last two weeks (5 of 6 requests are dark mode — all from one $4,800
account), a keyword count (`fact list --query SSO` finds 1 of the 5 single
sign-on requests, because people write SAML, Okta, Google Workspace, Azure AD),
counting `search` hits (it always returns top-k, and its score is just 1/rank,
so there is nothing to cut on), and counting mentions (dark mode 6, SSO 5).
Read all of it, de-duplicated to accounts and weighted by what those accounts
pay, and single sign-on comes first: five accounts, $120,400 of ARR, two
renewals and a $60,000 pilot blocked on it.  Dark mode is two accounts and
$19,200.  Then two requests arrive today: mentions move both ways, accounts move
only for SSO.

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

PREFIX = "mlu-frm"  # memorylake-usecases / feature-request-memory


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


def money(n: float) -> str:
    return f"${n:,.0f}"


def accounts(story: dict) -> dict[str, dict]:
    return {a["key"]: a for a in story["accounts"]}


def theme_name(story: dict, key: str) -> str:
    return story["themes"].get(key, key)


def theme_key(story: dict, name: str) -> str:
    n = name.lower().strip()
    for k, v in story["themes"].items():
        if n in (k, v.lower()) or n == k.replace("-", " "):
            return k
    die(f"unknown theme {name!r}; this demo has: {', '.join(story['themes'])}")
    return ""


def account_text(a: dict) -> str:
    if a["status"] == "prospect":
        money_part = f"prospect, {a['plan']} worth {a['arr']} dollars a year if it converts"
    else:
        money_part = f"customer on the {a['plan']} plan, {a['arr']} dollars ARR, renews {a['renews']}"
    return f"Account {a['name']}: {money_part}; {a['note']}."


def account_meta(a: dict) -> dict:
    return dict(kind="account", key=f"account:{a['key']}", account=a["key"], name=a["name"], status=a["status"],
                plan=a["plan"], arr=a["arr"], renews=a["renews"])


def request_text(story: dict, r: dict) -> str:
    a = accounts(story)[r["account"]]
    return (f"Feature request {r['id']} from {a['name']} ({story['sources'][r['source']]}, {r['received']}, "
            f"{r['urgency']}): {r['text']}")


def request_meta(r: dict) -> dict:
    return dict(kind="request", key=f"request:{r['id']}", request=r["id"], account=r["account"], source=r["source"],
                received=r["received"], theme=r["theme"], urgency=r["urgency"])


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


def list_facts(cli: CLI, project_id: str, query: str | None = None, echo: bool = True) -> list[dict]:
    args = ["fact", "list", "--projects", project_id] + (["--query", query] if query else [])
    return pages(cli, args, echo=echo)


def find(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def entries(facts: list[dict]) -> dict:
    """The pinned facts, split into accounts (by key) and requests (by fact id), metadata flattened in."""
    accts, reqs = {}, {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("kind") == "account":
            accts[m["account"]] = dict(m, id=f["id"], text=f.get("fact"))
        elif m.get("kind") == "request":
            reqs[f["id"]] = dict(m, id=f["id"], text=f.get("fact"))
    return dict(accounts=accts, requests=reqs)


def upto(reqs: dict, day: str) -> list[dict]:
    """Requests received before `day` (ISO date), oldest first."""
    return sorted((r for r in reqs.values() if r["received"] < day), key=lambda r: (r["received"], r["request"]))


def tally(story: dict, mem: dict, reqs: list[dict]) -> list[dict]:
    """Per theme: mentions, distinct accounts, the ARR of the customers asking, prospect pipeline, blockers."""
    rows = {}
    for r in reqs:
        t = rows.setdefault(r["theme"], dict(theme=r["theme"], name=theme_name(story, r["theme"]), mentions=0,
                                             accounts=[], requests=[], blockers=[]))
        t["mentions"] += 1
        t["requests"].append(r["request"])
        if r["account"] not in t["accounts"]:
            t["accounts"].append(r["account"])
        if r["urgency"] == "blocker":
            t["blockers"].append(r["request"])
    out = []
    for t in rows.values():
        acc = [mem["accounts"].get(k) or {} for k in t["accounts"]]
        t["arr"] = sum(a.get("arr") or 0 for a in acc if a.get("status") == "customer")
        t["pipeline"] = sum(a.get("arr") or 0 for a in acc if a.get("status") == "prospect")
        t["account_names"] = [a.get("name") or k for a, k in zip(acc, t["accounts"])]
        t["renewals"] = sorted({(a.get("renews"), a.get("name")) for a, k in zip(acc, t["accounts"])
                                if a.get("status") == "customer" and any(
                                    r["account"] == k and r["urgency"] == "blocker" and r["theme"] == t["theme"] for r in reqs)
                                and a.get("renews")})
        out.append(t)
    return sorted(out, key=lambda t: (-(t["arr"] + t["pipeline"]), -len(t["accounts"]), t["theme"]))


def leader(rows: list[dict], key) -> dict:
    return sorted(rows, key=lambda t: (-key(t), t["theme"]))[0]


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


def intake(cli: CLI, pid: str, story: dict) -> dict:
    note("one fact per account (plan, ARR, renewal) and one per request, word for word, each with metadata: "
         "account, channel, date, theme, urgency — the theme is what triage assigned when the request came in")
    a = pin(cli, pid, [(account_text(x), account_meta(x)) for x in story["accounts"]], "account, status, plan, arr, renews")
    r = pin(cli, pid, [(request_text(story, x), request_meta(x)) for x in story["requests"]],
            "request, account, source, received, theme, urgency")
    if not any(g["new"] for g in list(a.values()) + list(r.values())):
        note("the requests were pinned by an earlier run; reading them back")
    mem = entries(list_facts(cli, pid))
    reqs = upto(mem["requests"], story["today"])
    lines = [f"\n  {len(mem['accounts'])} accounts · {len(reqs)} requests received before today ({story['today']})"]
    for k, a in sorted(mem["accounts"].items(), key=lambda x: -x[1]["arr"]):
        tag = "prospect " if a["status"] == "prospect" else ""
        lines.append(f"    {a['name']:<19} {a['plan']:<17} {tag + money(a['arr']) + (' ARR' if not tag else ' pipeline'):<20}"
                     f" {('renews ' + a['renews']) if a['renews'] else ''}")
    by_src = {}
    for x in reqs:
        by_src[x["source"]] = by_src.get(x["source"], 0) + 1
    lines.append("\n  requests by channel: " + " · ".join(f"{story['sources'][k]} {n}" for k, n in sorted(by_src.items(), key=lambda x: -x[1])))
    for x in reqs:
        acct = mem["accounts"].get(x["account"], {}).get("name", x["account"])
        lines.append(f"    {x['request']}  {x['received']}  {acct:<19} {x['source']:<6} {x['theme']:<12} "
                     f"{'BLOCKER ' if x['urgency'] == 'blocker' else ''}{clip(x['text'].split('): ', 1)[-1], 70)}")
    emit("text", "\n".join(lines))
    emit("intake", "", accounts=mem["accounts"], requests=reqs, sources=story["sources"], themes=story["themes"])
    return mem


# --------------------------------------------------------------------------- step 4: four quick reads

def search(cli: CLI, q: str, pid: str, top_k: int, mem: dict, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        r = mem["requests"].get(f.get("id"))
        a = next((a for a in mem["accounts"].values() if a["id"] == f.get("id")), None)
        out.append(dict(rank=f.get("rank") or i, score=f.get("score"), id=f.get("id"), text=f.get("fact"),
                        request=(r or {}).get("request"), theme=(r or {}).get("theme"),
                        account=(r or a or {}).get("account"), kind="request" if r else "account" if a else None))
    return out


def quick_reads(cli: CLI, pid: str, story: dict, mem: dict) -> dict:
    day = story["today"]
    reqs = upto(mem["requests"], day)
    truth = {t["theme"]: t for t in tally(story, mem, reqs)}
    reads = []

    # 1. what the team remembers: the last two weeks
    recent = [r for r in reqs if r["received"] >= story["recent_from"]]
    rc = {}
    for r in recent:
        rc[r["theme"]] = rc.get(r["theme"], 0) + 1
    top = sorted(rc.items(), key=lambda x: (-x[1], x[0]))[0]
    note(f"read 1 — what the team remembers: requests since {story['recent_from']} (the same `fact list`, filtered by date)")
    accts = sorted({r["account"] for r in recent if r["theme"] == top[0]})
    names = [mem["accounts"][k]["name"] for k in accts]
    emit("text", f"\n  last two weeks: {len(recent)} requests — " + ", ".join(f"{theme_name(story, k)} {n}" for k, n in
                                                                          sorted(rc.items(), key=lambda x: -x[1]))
         + f"\n  → “{theme_name(story, top[0])} is what everyone is asking for”: {top[1]} of {len(recent)} — "
           f"all from {', '.join(names)} ({money(sum(mem['accounts'][k]['arr'] for k in accts))} ARR)")
    reads.append(dict(read="last two weeks", winner=top[0], detail=f"{top[1]} of {len(recent)} recent requests",
                      counts=rc))

    # 2. keyword count
    note("read 2 — a keyword count: `fact list --query` is an exact, case-insensitive substring match")
    kw = {}
    for i, k in enumerate(story["keyword_checks"]):
        hits = [mem["requests"][f["id"]] for f in list_facts(cli, pid, query=k["query"], echo=True)
                if f["id"] in mem["requests"] and mem["requests"][f["id"]]["received"] < day]
        want = truth[k["theme"]]["requests"]
        got = sorted(h["request"] for h in hits)
        missed = [x for x in want if x not in got]
        kw[k["theme"]] = dict(query=k["query"], found=got, want=want, missed=missed)
        lines = [f"    “{k['query']}” → {len(got)} request(s) {', '.join(got)}   (triage tagged {len(want)} as {theme_name(story, k['theme'])})"]
        for x in missed:
            r = next(r for r in reqs if r["request"] == x)
            lines.append(f"      missed {x}: {clip(r['text'].split('): ', 1)[-1], 90)}")
        emit("text", "\n".join(lines))
    kwin = max(kw.items(), key=lambda x: len(x[1]["found"]))[0]
    emit("text", "\n  → by keyword: " + " · ".join(f"{theme_name(story, k)} {len(v['found'])}" for k, v in kw.items())
         + f" — {theme_name(story, kwin)} leads")
    reads.append(dict(read="keyword count", winner=kwin, detail=", ".join(f"{v['query']}: {len(v['found'])}/{len(v['want'])}" for v in kw.values()),
                      keyword=kw))

    # 3. counting search hits
    c = story["topk_check"]
    note(f"read 3 — ask search and count the hits: an AI tool asking “{c['query']}”")
    hits = search(cli, c["query"], pid, c["top_k"], mem)
    on = [h for h in hits if h["theme"] == c["theme"]]
    lines = [f"\n    {'#':>3}  {'score':>6}  {'theme':<12} hit"]
    for h in hits:
        mark = "✓" if h["theme"] == c["theme"] else "✗"
        lines.append(f"    {h['rank']:>3}  {(h['score'] or 0):>6.3f}  {mark} {(h['theme'] or h['kind'] or '?'):<12} "
                     f"{clip(h['text'], 90)}")
    scores = [round(h["score"] or 0, 3) for h in hits]
    recip = all(abs((h["score"] or 0) - 1 / h["rank"]) < 1e-6 for h in hits if h["rank"])
    lines.append(f"\n  → {len(hits)} hits for “{theme_name(story, c['theme'])}”; {len(on)} of them are export requests "
                 f"({', '.join(h['request'] for h in on)}), {len(hits) - len(on)} are about something else")
    lines.append("    search returns top-k whether or not anything matches, and the score is "
                 + ("exactly 1/rank (" + ", ".join(map(str, scores[:5])) + " …): there is no relevance threshold to cut on"
                    if recip else "not 1/rank this time: " + ", ".join(map(str, scores))))
    emit("text", "\n".join(lines))
    reads.append(dict(read="counting search hits", winner=None, detail=f"{len(hits)} hits, {len(on)} on topic",
                      hits=hits, reciprocal=recip, on_topic=len(on), theme=c["theme"]))

    # 4. counting mentions
    note("read 4 — count every request, all channels, all months (the right data, the wrong unit)")
    by_m = sorted(truth.values(), key=lambda t: (-t["mentions"], t["theme"]))
    emit("text", "\n  mentions: " + " · ".join(f"{t['name']} {t['mentions']}" for t in by_m)
         + f"\n  → {by_m[0]['name']} leads with {by_m[0]['mentions']} — {by_m[0]['mentions']} requests from "
           f"{len(by_m[0]['accounts'])} account(s): {', '.join(by_m[0]['account_names'])}")
    reads.append(dict(read="counting mentions", winner=by_m[0]["theme"], detail=" · ".join(f"{t['theme']} {t['mentions']}" for t in by_m)))
    out = dict(reads=reads, recent_from=story["recent_from"])
    emit("quick_reads", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: the whole memory, by account

def show_table(story: dict, rows: list[dict], prev: dict | None = None) -> None:
    lines = [f"\n    {'theme':<30} {'mentions':>8} {'accounts':>8} {'customer ARR':>19} {'pipeline':>9}  blockers"]
    for t in rows:
        p = (prev or {}).get(t["theme"])
        m = f"{p['mentions']}→{t['mentions']}" if p and p["mentions"] != t["mentions"] else str(t["mentions"])
        a = f"{len(p['accounts'])}→{len(t['accounts'])}" if p and len(p["accounts"]) != len(t["accounts"]) else str(len(t["accounts"]))
        arr = f"{money(p['arr'])}→{money(t['arr'])}" if p and p["arr"] != t["arr"] else money(t["arr"])
        lines.append(f"    {t['name']:<30} {m:>8} {a:>8} {arr:>19} {money(t['pipeline']) if t['pipeline'] else '-':>9}  "
                     f"{len(t['blockers']) or '-'}")
    emit("text", "\n".join(lines))


def by_account(cli: CLI, pid: str, story: dict) -> dict:
    day = story["today"]
    note("read everything instead: `fact list --projects` (every page, not a top-k), then per theme the distinct "
         "accounts asking and what they pay — plain code over the metadata, no model")
    mem = entries(list_facts(cli, pid))
    rows = tally(story, mem, upto(mem["requests"], day))
    show_table(story, rows)
    top, dm = rows[0], next(t for t in rows if t["theme"] == "dark-mode")
    lines = [f"\n  → {top['name']}: {len(top['accounts'])} accounts, {money(top['arr'])} ARR + {money(top['pipeline'])} pipeline, "
             f"{len(top['blockers'])} blocker(s)"]
    for rid in top["blockers"]:
        r = next(r for r in mem["requests"].values() if r["request"] == rid)
        a = mem["accounts"][r["account"]]
        stake = (f"renews {a['renews']}" if a["status"] == "customer" else f"prospect, {money(a['arr'])} pilot")
        lines.append(f"      {rid} {a['name']:<19} {story['sources'][r['source']]:<20} {r['received']}  {stake:<24} "
                     f"{clip(r['text'].split('): ', 1)[-1], 64)}")
    lines.append(f"  → {dm['name']}: {dm['mentions']} mentions from {len(dm['accounts'])} account(s) "
                 f"({', '.join(dm['account_names'])}), {money(dm['arr'])} ARR — rank {rows.index(dm) + 1} of {len(rows)} by ARR")
    emit("text", "\n".join(lines))
    out = dict(day=day, rows=rows, top=top["theme"])
    emit("by_account", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: two requests arrive today

def triage_hint(cli: CLI, pid: str, story: dict, mem: dict, r: dict, echo: bool = True) -> dict:
    """The three nearest requests already in memory; their most common theme is the hint."""
    q = r["text"]
    hits = [h for h in search(cli, q, pid, 3, mem, echo=echo) if h["kind"] == "request"]
    votes = {}
    for h in hits:
        votes[h["theme"]] = votes.get(h["theme"], 0) + 1
    hint = sorted(votes.items(), key=lambda x: (-x[1], x[0]))[0][0] if votes else None
    return dict(request=r["id"], hits=hits, hint=hint, label=r["theme"], agree=hint == r["theme"])


def arrive(cli: CLI, pid: str, story: dict, before: dict) -> dict:
    day = story["today"]
    mem = entries(list_facts(cli, pid, echo=False))
    have = {r["request"] for r in mem["requests"].values()}
    todo = [r for r in story["today_requests"] if r["id"] not in have]
    hints = []
    if not todo:
        note("today's two requests were pinned by an earlier run; reading them back")
    else:
        note("triage first: the three nearest requests already in memory (`search --top-k 3`) — a hint for whoever "
             "tags the theme, printed against the theme they chose")
    for i, r in enumerate(story["today_requests"]):
        a = accounts(story)[r["account"]]
        h = triage_hint(cli, pid, story, mem, r, echo=i == 0) if r in todo else None
        if h:
            hints.append(h)
        lines = [f"\n  {r['id']}  {a['name']} · {story['sources'][r['source']]} · {r['urgency']}: “{r['text']}”"]
        if h:
            near = ", ".join(f"{x['request']} ({x['theme']})" for x in h["hits"])
            lines.append(f"    nearest in memory: {near}")
            lines.append(f"    {'✓' if h['agree'] else '✗'} hint {h['hint']} · tagged {r['theme']}"
                         + ("" if h["agree"] else " — the hint is only a hint; the tag is what gets counted"))
        else:
            lines.append(f"    tagged {r['theme']}")
        emit("text", "\n".join(lines))
    if todo:
        pin(cli, pid, [(request_text(story, r), request_meta(r)) for r in todo], "request, account, source, received, theme, urgency")
    mem = entries(list_facts(cli, pid))
    rows = tally(story, mem, sorted(mem["requests"].values(), key=lambda r: (r["received"], r["request"])))
    prev = {t["theme"]: t for t in before["rows"]}
    show_table(story, rows, prev)
    moved = []
    for r in story["today_requests"]:
        t, p = next(x for x in rows if x["theme"] == r["theme"]), prev[r["theme"]]
        moved.append(dict(theme=r["theme"], name=t["name"], mentions=[p["mentions"], t["mentions"]],
                          accounts=[len(p["accounts"]), len(t["accounts"])], arr=[p["arr"], t["arr"]]))
    lines = []
    for m in moved:
        lines.append(f"  {m['name']:<30} mentions {m['mentions'][0]} → {m['mentions'][1]}   accounts {m['accounts'][0]} → "
                     f"{m['accounts'][1]}   ARR {money(m['arr'][0])} → {money(m['arr'][1])}")
    by_m = sorted(rows, key=lambda t: (-t["mentions"], t["theme"]))[0]
    lines.append(f"\n  → counted by mentions, {by_m['name']} still leads ({by_m['mentions']}); by accounts and ARR, "
                 f"{rows[0]['name']} ({len(rows[0]['accounts'])} accounts, {money(rows[0]['arr'])} + {money(rows[0]['pipeline'])} pipeline)")
    emit("text", "\n" + "\n".join(lines))
    out = dict(day=day, rows=rows, moved=moved, hints=hints, pinned=[r["id"] for r in todo])
    emit("arrived", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: provenance and ask

def provenance(cli: CLI, pid: str, story: dict, theme: str, echo: bool = True) -> dict:
    mem = entries(list_facts(cli, pid, echo=echo))
    reqs = sorted((r for r in mem["requests"].values() if r["theme"] == theme), key=lambda r: (r["received"], r["request"]))
    rows = []
    for r in reqs:
        a = mem["accounts"].get(r["account"], {})
        rows.append(dict(request=r["request"], account=a.get("name"), status=a.get("status"), arr=a.get("arr"),
                         renews=a.get("renews"), source=story["sources"][r["source"]], received=r["received"],
                         urgency=r["urgency"], quote=r["text"].split("): ", 1)[-1], fact=r["id"]))
    return dict(theme=theme, name=theme_name(story, theme), rows=rows)


def show_provenance(p: dict) -> None:
    lines = [f"\n  {p['name']} — {len(p['rows'])} request(s), every one back to its account, channel and date:"]
    for r in p["rows"]:
        lines.append(f"    {r['request']} {r['received']}  {r['account']:<19} {r['source']:<20} "
                     f"{'BLOCKER ' if r['urgency'] == 'blocker' else ''}“{clip(r['quote'], 70)}”")
        lines.append(f"        {r['fact']}  ({r['status']}, {money(r['arr'] or 0)}{', renews ' + r['renews'] if r['renews'] else ''})")
    emit("text", "\n".join(lines))


def ask(cli: CLI, pid: str, q: str, echo: bool = True) -> dict:
    """Any question: search's top 8 as it comes back, then the theme the top request hit belongs to, read in full."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=False))
    hits = search(cli, q, pid, 8, mem, echo=echo)
    first = next((h for h in hits if h["kind"] == "request"), None)
    full = provenance(cli, pid, story, first["theme"], echo=False) if first else None
    return dict(q=q, hits=hits, theme=first["theme"] if first else None, full=full)


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
    note("deleted the request project and every fact on it")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one project for every feature request Driftline has received")
    pid = setup(cli, story, reset)
    cli.ids = dict(project=pid)  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Intake — 7 accounts and 17 requests from tickets, calls, Canny and Slack, one fact each")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "Four quick reads — recent, keyword, search hits, mentions — and what each says to build")
    quick = quick_reads(cli, pid, story, mem)
    banner(5, TOTAL, "Read all of it, by account — who is asking and what they pay")
    full = by_account(cli, pid, story)
    banner(6, TOTAL, f"Two requests arrive today ({story['today']}) — mentions and accounts move differently")
    arr = arrive(cli, pid, story, full)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   quick={r["read"]: r["winner"] or r["detail"] for r in quick["reads"]},
                   keyword={k: len(v["found"]) for k, v in quick["reads"][1]["keyword"].items()},
                   topk=dict(hits=len(quick["reads"][2]["hits"]), on_topic=quick["reads"][2]["on_topic"],
                             reciprocal=quick["reads"][2]["reciprocal"]),
                   top=full["top"], ranking=[t["theme"] for t in full["rows"]],
                   hints=[(h["request"], h["hint"], h["label"]) for h in arr["hints"]],
                   moved={m["theme"]: dict(mentions=m["mentions"], accounts=m["accounts"]) for m in arr["moved"]})
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
    ap.add_argument("command", nargs="?", default="run", choices=["run", "themes", "theme", "ask", "cleanup"],
                    help="run = full demo (default); themes = today's table by account and ARR; theme NAME = every "
                         "request behind one theme, back to its source; ask \"QUESTION\" = search's top 8 and the theme "
                         "behind the first request hit; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the theme (sso, dark-mode, export, slack-alerts, offline) or the question")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("theme", "ask") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. theme sso  /  ask \"Who asked for dark mode?\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py theme sso`, `python3 demo.py ask \"Who asked for dark mode?\"` or "
              "`python3 demo.py themes`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "themes":
        banner(2, 2, "Every theme, by account and ARR — all requests in memory")
        mem = entries(list_facts(cli, pid))
        show_table(story, tally(story, mem, sorted(mem["requests"].values(), key=lambda r: r["received"])))
    elif a.command == "theme":
        k = theme_key(story, a.args[0])
        banner(2, 2, f"{theme_name(story, k)} — every request, back to its source")
        show_provenance(provenance(cli, pid, story, k))
    elif a.command == "ask":
        banner(2, 2, "Ask — what search returns, and the whole theme behind its first request hit")
        r = ask(cli, pid, a.args[0])
        lines = [f"\n  “{r['q']}” — search top {len(r['hits'])} (score = what search returns):"]
        for h in r["hits"]:
            lines.append(f"    {h['rank']:>2} {(h['score'] or 0):.3f}  {(h['theme'] or h['kind'] or '?'):<12} {clip(h['text'], 96)}")
        emit("text", "\n".join(lines))
        if r["full"]:
            show_provenance(r["full"])


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

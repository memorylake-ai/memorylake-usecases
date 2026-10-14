#!/usr/bin/env python3
"""
Investment thesis memory for portfolio managers — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Ardent Hollow Partners (fictional) runs a seven-stock book.  Each holding has
its own MemoryLake project — the way research is usually filed, and the way an
AI tool usually reads it: the thesis, the premises it rests on, earnings and
news notes, conviction updates.  Every record is one fact, word for word, with
metadata: the holding, who logged it, when, and — for a premise — the company
it depends on and the events that would break it or bear it out; for a note,
the companies it is about and the events it reports.

On September 30 an analyst filed a note under Corvane Robotics: Halvern
Logistics, Corvane's largest customer, will build its warehouse automation and
software in-house, freeze new third-party technology contracts, and leave its
leased Rotterdam hub in 2027.  The weekly review goes holding by holding: one
search per project, then the same premise check over that holding's memory.
One holding of seven is flagged — Corvane.  Run the very same check over the
whole book and three are: Tessaly Freight Software and Brightmoor Logistics
REIT each rest a premise on Halvern too, and neither one's memory says a word
about the news — it was filed under Corvane.  Tessaly's "conviction high" was
reaffirmed on October 7, a week after the news was already in memory.

Step 6 files a cross-reference into each blind holding and marks the theses;
the holding-by-holding review then flags three.  The premise check is plain
code over `fact list` and metadata, not a model.

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

PREFIX = "mlu-ivm"  # memorylake-usecases / investment-thesis-memory


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


def holdings(story: dict) -> dict[str, dict]:
    return {h["ticker"]: h for h in story["holdings"]}


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null, so absent fields are simply left out.
    m = dict(key=f"thesis:{r['id']}", record=r["id"], holding=r["holding"], kind=r["kind"], logged_by=r["logged_by"],
             logged_on=r["logged_on"])
    for k in ("conviction", "premise", "entity", "xref_of"):
        if r.get(k):
            m[k] = r[k]
    for k in ("breaks_on", "holds_on", "entities", "events"):
        if k in r:
            m[k] = list(r[k])
    return m


def kind_name(story: dict, k: str) -> str:
    return story["kinds"].get(k, k)


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


def find(cli: CLI, ticker: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(ticker.lower()), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (CRVN-T …), metadata flattened in, plus the fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            r = dict(m, fid=f["id"], body=f.get("fact") or "")
            for k in ("breaks_on", "holds_on", "entities", "events", "broken"):
                r[k] = list(m.get(k) or [])
            out[m["record"]] = r
    return out


def read_book(cli: CLI, pids: dict[str, str], echo: bool = True, echo_all: bool = True) -> dict[str, dict]:
    """Every record of every holding: one `fact list` per project (every page)."""
    mem = {}
    for i, (t, pid) in enumerate(pids.items()):
        mem.update(entries(list_facts(cli, pid, echo=echo and (echo_all or i == 0))))
    if echo and not echo_all and len(pids) > 1:
        note(f"… and the same `fact list` for the other {len(pids) - 1} holdings")
    return mem


def as_of(mem: dict, day: str, include_day: bool = True) -> dict:
    return {k: r for k, r in mem.items() if (r["logged_on"] <= day if include_day else r["logged_on"] < day)}


# --------------------------------------------------------------------------- the premise check (plain code, no model)

def check(story: dict, mem: dict, scope: list[str]) -> list[dict]:
    """Every premise of the holdings in `scope`, tested against every note in `scope` that is about the company the
    premise depends on. The latest note that reports a breaking or a bearing-out event decides."""
    view = {k: r for k, r in mem.items() if r["holding"] in scope}
    notes = [r for r in view.values() if r["events"]]
    out = []
    for p in sorted((r for r in view.values() if r["kind"] == "premise"), key=lambda r: r["record"]):
        hits = []
        for n in notes:
            if p["entity"] not in n["entities"]:
                continue
            b, h = set(n["events"]) & set(p["breaks_on"]), set(n["events"]) & set(p["holds_on"])
            if b or h:
                hits.append(dict(record=n["record"], on=n["logged_on"], filed=n["holding"], effect="breaks" if b else "holds",
                                 events=sorted(b or h), xref_of=n.get("xref_of") or ""))
        hits.sort(key=lambda x: (x["on"], x["effect"] == "breaks", x["record"]))
        last = hits[-1] if hits else None
        status = {"breaks": "broken", "holds": "holding"}.get(last["effect"]) if last else "untested"
        out.append(dict(premise=p["record"], holding=p["holding"], label=p.get("premise") or "", entity=p["entity"],
                        status=status, by=last, evidence=hits, body=p["body"]))
    return out


def conviction(mem: dict, ticker: str) -> dict | None:
    """The latest thesis or conviction update on a holding."""
    c = [r for r in mem.values() if r["holding"] == ticker and r["kind"] in ("thesis", "conviction") and r.get("conviction")]
    return sorted(c, key=lambda r: (r["logged_on"], r["kind"] == "conviction"))[-1] if c else None


def review(story: dict, mem: dict, scope_each: bool) -> list[dict]:
    """One row per holding. scope_each=True: each holding checked against its own memory only (the weekly review);
    False: every holding checked against the whole book."""
    hs = holdings(story)
    book = None if scope_each else check(story, mem, list(hs))
    rows = []
    for t, h in hs.items():
        prem = check(story, mem, [t]) if scope_each else [p for p in book if p["holding"] == t]
        c = conviction(mem, t)
        broken = [p for p in prem if p["status"] == "broken"]
        rows.append(dict(ticker=t, name=h["name"], weight=h["weight"], premises=prem, broken=[p["label"] for p in broken],
                         flagged=bool(broken), conviction=c["conviction"] if c else "", conviction_on=c["logged_on"] if c else "",
                         conviction_record=c["record"] if c else "",
                         blind=[p["label"] for p in broken if p["by"]["filed"] != t and not p["by"]["xref_of"]]))
    return rows


def exposure(story: dict, mem: dict, rows: list[dict]) -> list[dict]:
    """Companies that more than one holding's premises depend on, with the book weight resting on them."""
    hs = holdings(story)
    own = {h["name"] for h in hs.values()}
    ent = {}
    for r in rows:
        for p in r["premises"]:
            if p["entity"] in own:
                continue
            e = ent.setdefault(p["entity"], dict(entity=p["entity"], holdings=[], weight=0, broken=[], holding=[]))
            if r["ticker"] not in e["holdings"]:
                e["holdings"].append(r["ticker"])
                e["weight"] += r["weight"]
            (e["broken"] if p["status"] == "broken" else e["holding"] if p["status"] == "holding" else []).append(p["premise"])
    return sorted((e for e in ent.values() if len(e["holdings"]) > 1), key=lambda e: (-e["weight"], e["entity"]))


def reaffirmed_after(mem: dict, rows: list[dict]) -> list[dict]:
    """Conviction updates that kept a holding at high after the note that broke one of its premises was in memory."""
    out = []
    for r in rows:
        for p in r["premises"]:
            if p["status"] != "broken":
                continue
            for c in mem.values():
                if c["holding"] == r["ticker"] and c["kind"] == "conviction" and c.get("conviction") == "high" \
                        and c["logged_on"] > p["by"]["on"]:
                    out.append(dict(ticker=r["ticker"], record=c["record"], on=c["logged_on"], by=c["logged_by"],
                                    premise=p["label"], evidence=p["by"]["record"], evidence_on=p["by"]["on"],
                                    filed=p["by"]["filed"], days=(datetime.fromisoformat(c["logged_on"])
                                                                  - datetime.fromisoformat(p["by"]["on"])).days,
                                    body=c["body"]))
    return out


def audit(story: dict, mem: dict, day: str, include_day: bool = True) -> dict:
    view = as_of(mem, day, include_day)
    each, book = review(story, view, True), review(story, view, False)
    return dict(day=day, each=each, book=book, exposure=exposure(story, view, book),
                reaffirmed=reaffirmed_after(view, book), weight=sum(h["weight"] for h in story["holdings"]))


# --------------------------------------------------------------------------- printing

MARK = {"broken": "✗", "holding": "✓", "untested": "·"}


def premise_cell(story: dict, p: dict, holding: str) -> str:
    s = f"{p['label']} {p['entity']}: {MARK[p['status']]} {p['status']}"
    if p["by"]:
        s += f" ({p['by']['record']} {p['by']['on']}"
        if p["by"]["filed"] != holding:
            s += f", filed under {p['by']['filed']}"
        s += ")"
    return s


def review_lines(story: dict, rows: list[dict], scope: str) -> list[str]:
    lines = [f"    {'holding':<32} {'wt':>4}  {'conviction':<17} premises"]
    for r in rows:
        conv = f"{r['conviction']} ({r['conviction_on'][5:]})" if r["conviction"] else "-"
        cells = [premise_cell(story, p, r["ticker"]) for p in r["premises"]] or ["-"]
        flag = "  ← FLAGGED" if r["flagged"] else ""
        lines.append(f"    {r['ticker'] + ' ' + r['name']:<32} {r['weight']:>3}%  {conv:<17} {cells[0]}{flag if len(cells) == 1 else ''}")
        for i, c in enumerate(cells[1:], 1):
            lines.append(f"    {'':<32} {'':>4}  {'':<17} {c}{flag if i == len(cells) - 1 else ''}")
    n = sum(1 for r in rows if r["flagged"])
    w = sum(r["weight"] for r in rows if r["flagged"])
    lines.append(f"\n  → {scope}: {n} of {len(rows)} holdings flagged ({w}% of the book)")
    return lines


def exposure_lines(a: dict) -> list[str]:
    lines = [f"    {'company the premises depend on':<32} {'holdings':<18} {'book':>5}  status"]
    for e in a["exposure"]:
        st = f"{len(e['broken'])} premise(s) broken" if e["broken"] else f"{len(e['holding'])} holding"
        lines.append(f"    {e['entity']:<32} {', '.join(e['holdings']):<18} {e['weight']:>4}%  {st}")
    return lines


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "holding", "kind", "logged_by", "logged_on", "conviction", "premise", "entity",
                                  "breaks_on", "holds_on", "entities", "events", "xref_of", "review", "broken", "fid", "body")}


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> dict[str, str]:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    pids, made = {}, []
    for h in story["holdings"]:
        proj = find(cli, h["ticker"], echo=False)
        if proj is None:
            proj = cli.run("proj", "create", "--name", f"{h['ticker']} {h['name']}", "--custom-id", cid(h["ticker"].lower()),
                           "--description", h["description"], scoped=True, echo=not made)
            made.append(h["ticker"])
        pids[h["ticker"]] = proj["id"]
    if len(made) > 1:
        note(f"… and {len(made) - 1} more `proj create` the same way ({', '.join(made[1:])})")
    if len(made) < len(pids):
        note(f"{len(pids) - len(made)} holding project(s) already exist from an earlier run (pass --reset to start over)")
    lines = [""] + [f"    {h['ticker']:<5} {h['name']:<28} {h['weight']:>3}%   {h['analyst']:<16} {pids[h['ticker']]}"
                    for h in story["holdings"]]
    lines.append(f"    {'':<5} {'':<28} {sum(h['weight'] for h in story['holdings']):>3}%   of the book in these seven")
    emit("text", "\n".join(lines))
    emit("setup", "", projects=pids, holdings=story["holdings"])
    return pids


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, project_id: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
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
    return out


def intake(cli: CLI, pids: dict[str, str], story: dict) -> dict:
    note("one fact per record, word for word, filed under its holding's project, each with metadata: who logged it, "
         "when, and — for a premise — the company it depends on and the events that break it or bear it out; "
         "for a note, the companies it is about and the events it reports")
    new, first = 0, True
    for t, pid in pids.items():
        recs = [r for r in story["records"] if r["holding"] == t]
        got = pin(cli, pid, [(r["text"], record_meta(r)) for r in recs], echo=first)
        n = sum(1 for g in got.values() if g["new"])
        if n and first:
            first = False
            note("… then the same `fact add` + `fact update --metadata` for every other holding's records")
        new += n
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    mem = read_book(cli, pids, echo_all=False)
    view = as_of(mem, story["today"], include_day=False)
    lines = [f"\n  {len(view)} records logged before today ({story['today']}):"]
    for t in pids:
        rs = sorted((r for r in view.values() if r["holding"] == t), key=lambda r: (r["logged_on"], r["record"]))
        kinds = {}
        for r in rs:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        lines.append(f"    {t:<5} {len(rs):>2} records   " + " · ".join(f"{v} {kind_name(story, k)}" for k, v in kinds.items()))
    n1 = next((r for r in view.values() if r["kind"] == "news" and "Halvern Logistics" in r["entities"]), None)
    if n1:
        lines.append(f"\n  {n1['record']} {n1['logged_on']} — filed under {n1['holding']} by {n1['logged_by']}:\n    “{n1['body']}”")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(view.values(), key=lambda r: (r["holding"], r["logged_on"], r["record"]))],
         kinds=story["kinds"])
    return mem


# --------------------------------------------------------------------------- step 4: holding by holding

def search(cli: CLI, q: str, pids: list[str], top_k: int, mem: dict, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", ",".join(pids), "--types", "fact", "--top-k", str(top_k), scoped=True,
                  echo=echo) or {}
    by_fid = {r["fid"]: r for r in mem.values()}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        r = by_fid.get(f.get("id")) or {}
        out.append(dict(rank=f.get("rank") or i, score=f.get("score"), fid=f.get("id"), body=f.get("fact"),
                        record=m.get("record") or r.get("record"), holding=m.get("holding") or r.get("holding") or "?",
                        kind=m.get("kind") or r.get("kind") or "", logged_on=m.get("logged_on") or r.get("logged_on") or "",
                        xref_of=m.get("xref_of") or ""))
    return out


def hit_line(h: dict) -> str:
    return f"      #{h['rank']:<2} {h['record'] or '?':<8} {h['logged_on']}  {clip(h['body'], 92)}"


def holding_review(cli: CLI, pids: dict[str, str], story: dict, mem: dict) -> dict:
    day = story["today"]
    view = as_of(mem, day, include_day=False)
    hs = holdings(story)
    note("the weekly review, the way an AI research tool scoped to one holding reads it: search that holding's "
         "project, then run the premise check over that holding's memory")
    searches = {}
    lines = []
    for i, (t, pid) in enumerate(pids.items()):
        q = story["question"].format(name=hs[t]["name"])
        hits = [h for h in search(cli, q, [pid], 8, mem, echo=True) if h["logged_on"] < day]
        searches[t] = hits
        lines.append(f"\n  {t} — “{q}” · top 3 of {len(hits)}:")
        lines += [hit_line(h) for h in hits[:3]]
    emit("text", "\n".join(lines))
    rows = review(story, view, True)
    emit("text", "\n  the premise check, each holding against its own memory:\n" + "\n".join(review_lines(story, rows, "holding by holding")))
    out = dict(rows=rows, searches=searches, flagged=[r["ticker"] for r in rows if r["flagged"]])
    emit("holding_review", "", **out)
    return out


# --------------------------------------------------------------------------- step 5: the whole book

def book_audit(cli: CLI, pids: dict[str, str], story: dict, each: dict | None = None) -> dict:
    note("the same premise check, over the whole book: one `fact list` per holding, every premise tested against "
         "every note about the company it depends on — whichever holding the note was filed under")
    mem = read_book(cli, pids)
    a = audit(story, mem, story["today"], include_day=False)  # this morning's memory: step 6's write-back is left out
    ctl = audit(story, mem, story["control_day"])
    lines = [""] + review_lines(story, a["book"], "the whole book")
    blind = [(r["ticker"], p) for r in a["book"] for p in r["premises"] if p["status"] == "broken" and p["by"]["filed"] != r["ticker"]]
    if blind:
        lines.append(f"  → {len(blind)} of those broken premises rest on a note their own holding's memory does not hold:")
        for t, p in blind:
            lines.append(f"      {t} {p['label']} ({p['entity']}) ← {p['by']['record']} {p['by']['on']}, filed under {p['by']['filed']}")
    lines.append("\n  shared dependencies — companies more than one holding's premises rest on:")
    lines += exposure_lines(a)
    for x in a["reaffirmed"]:
        lines.append(f"\n  → {x['ticker']} conviction reaffirmed high on {x['on']} ({x['record']}, {x['by']}) — {x['days']} days after "
                     f"{x['evidence']} was in memory under {x['filed']}:\n      “{x['body']}”")
    lines.append(f"\n  control — the same book-wide check as of {ctl['day']}, the day before the news:")
    nb = sum(1 for r in ctl["book"] if r["flagged"])
    lines.append(f"    {nb} of {len(ctl['book'])} holdings flagged; already visible: " +
                 "; ".join(f"{e['entity']} under {len(e['holdings'])} holdings ({', '.join(e['holdings'])}), {e['weight']}% of the book"
                           for e in ctl["exposure"]))
    emit("text", "\n".join(lines))
    out = dict(audit=a, control=ctl, flagged=[r["ticker"] for r in a["book"] if r["flagged"]],
               blind=[dict(ticker=t, premise=p["label"], entity=p["entity"], evidence=p["by"]["record"], filed=p["by"]["filed"])
                      for t, p in blind],
               each_flagged=(each or {}).get("flagged"))
    emit("book_audit", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: write it back

def write_back(cli: CLI, pids: dict[str, str], story: dict, book: dict) -> dict:
    day, hs = story["today"], holdings(story)
    a = book["audit"]
    mem = read_book(cli, pids, echo=False)
    note("file a cross-reference into every holding whose premise broke on a note filed elsewhere: `fact add` the "
         "note's text with where it came from, metadata copied from the original plus xref_of")
    xrefs, first = [], True
    for r in a["book"]:
        srcs = {}
        for p in r["premises"]:
            if p["status"] == "broken" and p["by"]["filed"] != r["ticker"]:
                srcs.setdefault(p["by"]["record"], []).append(p["label"])
        for n, (src, labels) in enumerate(sorted(srcs.items()), 1):
            o = mem[src]
            rid = f"{r['ticker']}-X{n}"
            text = (f"Cross-reference ({day}) from {src}, filed {o['logged_on']} under {hs[o['holding']]['name']}: {o['body']} "
                    f"This breaks {hs[r['ticker']]['name']} premise {', '.join(labels)}.")
            rec = dict(id=rid, holding=r["ticker"], kind="xref", logged_by=story["pm"]["name"], logged_on=day,
                       entities=o["entities"], events=o["events"], xref_of=src)
            got = pin(cli, pids[r["ticker"]], [(text, record_meta(rec))], echo=first)
            first = False
            xrefs.append(dict(record=rid, ticker=r["ticker"], xref_of=src, premises=labels,
                              new=any(g["new"] for g in got.values())))
    note("mark every thesis with a broken premise: `fact update --metadata` (the whole object — the old keys plus "
         "review, broken, flagged_on). The text stays word for word")
    marked, already, first = [], [], True
    for r in a["book"]:
        if not r["flagged"]:
            continue
        th = mem.get(f"{r['ticker']}-T")
        if th is None:
            continue
        if th.get("review") == "premise-broken" and sorted(th.get("broken") or []) == sorted(r["broken"]):
            already.append(r["ticker"])
            continue
        # entries() fills absent list keys with [] for reading; leave those out of the object written back
        meta = {k: v for k, v in th.items() if k not in ("fid", "body") and v != []}
        meta.update(review="premise-broken", broken=r["broken"], flagged_on=day)
        cli.run("fact", "update", th["fid"], "--project", pids[r["ticker"]], "--metadata", json.dumps(meta), scoped=True,
                echo=first)
        first = False
        marked.append(r["ticker"])
    if len(marked) > 1:
        note(f"… and {len(marked) - 1} more the same way ({', '.join(marked[1:])})")
    if already and not marked:
        note(f"all {len(already)} theses were marked by an earlier run")
    mem = read_book(cli, pids, echo_all=False)
    lines = [f"\n  cross-references filed: " + ("; ".join(f"{x['record']} ← {x['xref_of']} ({x['ticker']} {', '.join(x['premises'])})"
                                                        for x in xrefs) or "none needed")]
    lines.append("  theses marked premise-broken: " + ", ".join(
        f"{t} ({', '.join(mem[t + '-T'].get('broken') or [])})" for t in pids if mem.get(t + "-T", {}).get("review") == "premise-broken"))
    rows = review(story, mem, True)
    lines += ["\n  the weekly review again, each holding against its own memory — now holding the cross-references:"]
    lines += review_lines(story, rows, "holding by holding, after the write-back")
    emit("text", "\n".join(lines))
    # what the holding-scoped search sees now, for one of the blind holdings
    probe = next((x["ticker"] for x in xrefs), None)
    hits, xrank = [], None
    if probe:
        q = story["question"].format(name=hs[probe]["name"])
        note(f"and the same holding-scoped search for {probe} as this morning")
        hits = search(cli, q, [pids[probe]], 8, mem)
        xrank = next((h["rank"] for h in hits if h["xref_of"]), None)
        lines = [f"\n  {probe} — “{q}” · top {min(3, len(hits))} of {len(hits)}:"] + [hit_line(h) for h in hits[:3]]
        lines.append(f"  → the cross-reference is hit #{xrank}" if xrank else "  → the cross-reference is not in the top 8")
        emit("text", "\n".join(lines))
    out = dict(xrefs=xrefs, marked=marked, already=already, rows=rows, flagged=[r["ticker"] for r in rows if r["flagged"]],
               probe=probe, hits=hits, xref_rank=xrank)
    emit("written", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask

def ask(cli: CLI, pids: dict[str, str], q: str, echo: bool = True) -> dict:
    """Any question over the whole book: search's top 8 across every holding's project, each hit labelled with
    the holding it was filed under (from its metadata)."""
    mem = read_book(cli, pids, echo=False)
    hits = search(cli, q, list(pids.values()), 8, mem, echo=echo)
    return dict(q=q, hits=hits)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    gone = 0
    for h in story["holdings"]:
        proj = find(cli, h["ticker"], echo=not quiet and gone == 0)
        if not proj:
            continue
        for f in list_facts(cli, proj["id"], echo=False):
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = list_facts(cli, proj["id"], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on {h['ticker']} after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        gone += 1
    note(f"deleted {gone} holding project(s) and every record on them")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — one project per holding, seven in the book")
    pids = setup(cli, story, reset)
    cli.ids = dict(projects=pids)  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: theses, premises, earnings and news notes, conviction")
    mem = intake(cli, pids, story)
    banner(4, TOTAL, "The weekly review — holding by holding")
    each = holding_review(cli, pids, story, mem)
    banner(5, TOTAL, "The same check over the whole book")
    book = book_audit(cli, pids, story, each)
    banner(6, TOTAL, "Write it back — cross-reference the blind holdings, mark the theses")
    wb = write_back(cli, pids, story, book)
    a = book["audit"]
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   each_flagged=each["flagged"], book_flagged=book["flagged"],
                   blind=[f"{b['ticker']} {b['premise']}<-{b['evidence']}" for b in book["blind"]],
                   exposure={e["entity"]: dict(holdings=e["holdings"], weight=e["weight"], broken=len(e["broken"]))
                             for e in a["exposure"]},
                   reaffirmed=[f"{x['ticker']} {x['on']} +{x['days']}d" for x in a["reaffirmed"]],
                   control_flagged=[r["ticker"] for r in book["control"]["book"] if r["flagged"]],
                   control_exposure={e["entity"]: e["weight"] for e in book["control"]["exposure"]},
                   search_top1={t: (h[0]["record"] if h else None) for t, h in each["searches"].items()},
                   xrefs=[x["record"] for x in wb["xrefs"]], xrefs_new=[x["record"] for x in wb["xrefs"] if x["new"]],
                   marked=wb["marked"], already=wb["already"], after_flagged=wb["flagged"], xref_rank=wb["xref_rank"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_projects(cli: CLI) -> dict[str, str]:
    story = load_story()
    pids = {}
    for h in story["holdings"]:
        proj = find(cli, h["ticker"], echo=False)
        if proj is None:
            die("the demo data does not exist yet; run `python3 demo.py` first")
        pids[h["ticker"]] = proj["id"]
    return pids


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "exposure", "audit", "ask", "cleanup"],
                    help="run = full demo (default); exposure = the book-wide premise check and shared dependencies "
                         "today; audit DATE = the same, holding by holding and book-wide, as of a date (YYYY-MM-DD); "
                         "ask \"QUESTION\" = search's top 8 across every holding; cleanup = delete everything")
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
        ap.error("ask needs one argument, e.g. ask \"What do we know about Halvern Logistics?\"")
    if a.command == "audit" and (len(a.args) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.args[0])):
        ap.error("audit needs one date, e.g. audit 2026-09-29")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py exposure`, `python3 demo.py audit 2026-09-29` or "
              "`python3 demo.py ask \"What do we know about Halvern Logistics?\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    pids = need_projects(cli)
    if a.command in ("exposure", "audit"):
        day = a.args[0] if a.command == "audit" else story["today"]
        banner(2, 2, f"The premise check as of {day} — holding by holding, then the whole book")
        mem = read_book(cli, pids, echo_all=False)
        au = audit(story, mem, day)
        lines = [""] + review_lines(story, au["each"], "holding by holding") + [""] + review_lines(story, au["book"], "the whole book")
        lines.append("\n  shared dependencies:")
        lines += exposure_lines(au)
        for x in au["reaffirmed"]:
            lines.append(f"  → {x['ticker']} conviction reaffirmed high on {x['on']}, {x['days']} days after {x['evidence']} (filed under {x['filed']})")
        emit("text", "\n".join(lines))
    elif a.command == "ask":
        banner(2, 2, "Ask — search's top 8 across every holding, each hit labelled with where it was filed")
        r = ask(cli, pids, a.args[0])
        lines = [f"\n  “{r['q']}” — top {len(r['hits'])} across {len(pids)} holdings:"]
        lines += [f"    #{h['rank']:<2} {h['holding']:<5} {h['record'] or '?':<8} {h['logged_on']}  {clip(h['body'], 88)}" for h in r["hits"]]
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

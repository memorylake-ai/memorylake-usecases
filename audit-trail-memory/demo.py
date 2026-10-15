#!/usr/bin/env python3
"""
Compliance memory with audit trails — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Halden Mutual (a fictional bank) keeps one MemoryLake project as its Q3
transaction-monitoring casebook: policy, risk ratings, screening items, their
resolutions and every alert closure — one fact per record, word for word, with
the day it happened (event_on) and the day it entered the casebook (logged_on)
in metadata.  Today, 2026-10-15, internal auditor Mara Quist reviews the Q3
alert closures before the regulator's exam.

An audit question is a question about two clocks: what was on the analyst's
file on the day she closed the alert (the record clock), and which of those
records was the current one (the event clock).  Most records enter the casebook
the day they happen.  Late entries do not: branch paper forms typed in weeks
later, a screening inbox cleared in October, a false match checked in September
and written up in October.

Read through one clock, the audit goes wrong — in opposite directions.  The
event clock (the dates written in the records) blames an analyst for news that
reached the casebook three weeks after she closed the alert, and excuses another
for an item that was still open on her file.  The record clock (latest entry
wins) takes a paper form typed in on 8 October as the customer's current rating.
Both clocks together: on file that day (record clock), current among those
(event clock).

Step 6 writes the second clock into each late entry's text (`fact update
--text`; the old text kept in metadata.previous and in `fact trace`), so a read
that only knows what the records say now gets every answer right.

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

PREFIX = "mlu-cat"  # memorylake-usecases / compliance audit trail (audit-trail-memory)
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


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null.
    m = dict(key=f"cat:{r['id']}", record=r["id"], kind=r["kind"], customer=r["customer"], event_on=r["event_on"],
             logged_on=r["logged_on"], by=r["by"])
    for k in ("rating", "rule", "resolves", "signoff"):
        if k in r:
            m[k] = r[k]
    return m


def days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def lag(r: dict) -> int:
    return days(r["event_on"], r["logged_on"])


def is_late(story: dict, r: dict) -> bool:
    return lag(r) > story["late_days"]


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
    """The pinned records by record id (ORN-R1, A-1042 …), metadata flattened in, plus fact id and text."""
    out = {}
    for f in facts:
        m = f.get("metadata") or {}
        if m.get("record"):
            out[m["record"]] = dict(m, fid=f["id"], body=f.get("fact") or "", created=f.get("created_at") or "")
    return out


def find_project(cli: CLI, key: str, echo: bool = True):
    try:
        return cli.run("proj", "get", cid(key), "--by-custom-id", scoped=True, echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


PUBLIC = ("record", "kind", "customer", "event_on", "logged_on", "by", "rating", "rule", "resolves", "signoff", "fid",
          "body", "rank", "previous")


def public(r: dict | None) -> dict | None:
    if r is None:
        return None
    out = {k: r.get(k) for k in PUBLIC}
    if r.get("event_on") and r.get("logged_on"):
        out["lag"] = lag(r)
    return out


# --------------------------------------------------------------------------- the clocks

LATE_RE = re.compile(r"\(Late entry: logged (\d{4}-\d{2}-\d{2})")


def stated_logged(r: dict) -> str | None:
    """The day the record says it entered the casebook — only if its own text says so (step 6 writes it in)."""
    m = LATE_RE.search(r.get("body") or "")
    return m.group(1) if m else None


# Every read below is the same code. A clock is two choices:
#   known(r) — from which day the record counts as being on file;
#   order(r) — which of several records on file is the current one (the latest by this key).
CLOCKS = {
    "event": dict(label="event clock", short="event clock (dates in the records)",
                  known=lambda r: r["event_on"], order=lambda r: (r["event_on"], r["logged_on"])),
    "record": dict(label="record clock", short="record clock (latest entry wins)",
                   known=lambda r: r["logged_on"], order=lambda r: (r["logged_on"], r["event_on"])),
    "both": dict(label="both clocks", short="both clocks (on file that day, current by event)",
                 known=lambda r: r["logged_on"], order=lambda r: (r["event_on"], r["logged_on"])),
    "text": dict(label="what the text says", short="the text: event date, plus a logged date if the record states one",
                 known=lambda r: stated_logged(r) or r["event_on"], order=lambda r: (r["event_on"], r["record"])),
}

ITEMS = ("adverse", "sanctions")


def on_file(mem: dict, customer: str, day: str, clock: str) -> list[dict]:
    known = CLOCKS[clock]["known"]
    return sorted((r for r in mem.values() if r.get("customer") == customer and known(r) <= day),
                  key=lambda r: (r["event_on"], r["record"]))


def rating_at(mem: dict, customer: str, day: str, clock: str) -> dict | None:
    rs = [r for r in on_file(mem, customer, day, clock) if r["kind"] == "rating"]
    return max(rs, key=CLOCKS[clock]["order"]) if rs else None


def open_items(mem: dict, customer: str, day: str, clock: str) -> list[dict]:
    rs = on_file(mem, customer, day, clock)
    closed = {r.get("resolves") for r in rs if r["kind"] == "resolution"}
    return [r for r in rs if r["kind"] in ITEMS and r["record"] not in closed]


def alerts(mem: dict) -> list[dict]:
    return sorted((r for r in mem.values() if r.get("kind") == "alert"), key=lambda r: (r["event_on"], r["record"]))


def customers(story: dict) -> list[str]:
    return list(story["customers"])


def check_alert(mem: dict, a: dict, clock: str) -> dict:
    """TM-4 §3 and §5 for one closure, read through one clock, on the day it was closed."""
    day, cust = a["event_on"], a["customer"]
    rating = rating_at(mem, cust, day, clock)
    items = open_items(mem, cust, day, clock)
    high = bool(rating and rating.get("rating") == "high")
    s3 = "breach" if high and a.get("signoff") == "single" else "ok"
    s5 = "breach" if items else "ok"
    return dict(alert=a["record"], customer=cust, by=a["by"], day=day,
                rating=(rating or {}).get("rating"), rating_record=(rating or {}).get("record"),
                items=[r["record"] for r in items], s3=s3, s5=s5,
                on_file=[r["record"] for r in on_file(mem, cust, day, clock)])


def audit(story: dict, mem: dict, clock: str) -> dict:
    today = story["today"]
    checks = [check_alert(mem, a, clock) for a in alerts(mem)]
    ratings = {}
    for c in customers(story):
        r = rating_at(mem, c, today, clock)
        ratings[c] = dict(rating=(r or {}).get("rating"), record=(r or {}).get("record"))
    answers = {}
    for c in checks:
        answers[f"{c['alert']} §3"] = c["s3"]
        answers[f"{c['alert']} §5"] = c["s5"]
    for c, r in ratings.items():
        answers[f"{c} rating"] = r["rating"]
    findings = [dict(alert=c["alert"], rule=rule, by=c["by"]) for c in checks
                for rule, v in (("TM-4 §3", c["s3"]), ("TM-4 §5", c["s5"])) if v == "breach"]
    return dict(clock=clock, checks=checks, ratings=ratings, answers=answers, findings=findings)


def answer_label(story: dict, key: str) -> str:
    if key.endswith(" rating"):
        return f"{story['customers'][key[:-7]]}: rating today"
    alert, rule = key.split(" ")
    return f"{alert} TM-4 {rule}"


def compare(a: dict, truth: dict) -> list[str]:
    return [k for k in truth["answers"] if a["answers"].get(k) != truth["answers"][k]]


def by_person(findings: list[dict]) -> str:
    out: dict[str, list[str]] = {}
    for f in findings:
        out.setdefault(f["by"], []).append(f"{f['alert']} {f['rule'].split(' ')[1]}")
    return " · ".join(f"{p}: {', '.join(v)}" for p, v in sorted(out.items())) or "none"


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, pid: str, top_k: int = 5, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", event_on=m.get("event_on") or "?",
                        logged_on=m.get("logged_on") or "?"))
    return out


def hit_line(h: dict, day: str | None = None) -> str:
    mark = ""
    if day and h["logged_on"] != "?" and h["logged_on"] > day:
        mark = f"   ← not on file until {h['logged_on']}"
    return f"      #{h['rank']:<2} {h['record']:<10} {h['event_on']:<11} {clip(h['body'], 64)}{mark}"


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
    emit("text", f"\n    project  {p['name']:<56} {proj['id']}")
    emit("setup", "", project=proj["id"], name=p["name"])
    return proj["id"]


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the records the project does not hold yet (matched by metadata key) — one `fact add` for all of them,
    then `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new}."""
    live = {}
    for f in list_facts(cli, pid, echo=False):
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
        res = cli.run("fact", "add", "--project", pid, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, "--project", pid, "--metadata", json.dumps(meta), scoped=True,
                    echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way")
    return out


REWRITE_KEYS = ("previous", "rewritten_on", "rewritten_by")


def restore_audit_morning(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of the audit: records an earlier run rewrote are put back word for word from
    metadata.previous."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous"):
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} record(s); put back, so this run starts from the same morning")
    return n


def casebook_lines(story: dict, mem: dict) -> list[str]:
    lines = [f"\n    {'record':<10} {'happened':<11} {'logged':<11} {'':<12} text", "    " + "-" * 104]
    groups = [("", "policy")] + [(c, story["customers"][c]) for c in customers(story)]
    for cust, label in groups:
        rs = sorted((r for r in mem.values() if r.get("customer") == cust), key=lambda r: (r["event_on"], r["record"]))
        if not rs:
            continue
        lines.append(f"    {label}")
        for r in rs:
            late = f"late +{lag(r)} d" if is_late(story, r) else ""
            lines.append(f"    {r['record']:<10} {r['event_on']:<11} {r['logged_on']:<11} {late:<12} {clip(r['body'], 70)}")
    return lines


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_audit_morning(cli, pid)
    note("one project, one fact per record, word for word; then `fact update --metadata` with the day it happened "
         "(event_on), the day it entered the casebook (logged_on), who logged it, and what kind of record it is")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 searches.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 searches")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    emit("text", "\n".join(casebook_lines(story, mem)))
    late = [r for r in mem.values() if is_late(story, r)]
    late_s = ", ".join(f"{r['record']} +{lag(r)} d" for r in sorted(late, key=lambda r: r["record"]))
    note(f"{len(late)} late entries: the record says when it happened; only metadata.logged_on says when the casebook "
         f"got it ({late_s})")
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["customer"], r["event_on"], r["record"]))],
         late=[r["record"] for r in late], restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: one clock

def question(story: dict, a: dict) -> str:
    return f"What was on file about {story['customers'][a['customer']]} when alert {a['record']} was closed?"


def check_line(c: dict) -> str:
    s3 = f"rating {c['rating'] or '—'} ({c['rating_record'] or '—'}) → §3 {'✗ needed a second reviewer' if c['s3'] == 'breach' else 'ok'}"
    s5 = f"open items {', '.join(c['items']) or 'none'} → §5 {'✗ should have been escalated' if c['s5'] == 'breach' else 'ok'}"
    return f"{s3:<56} {s5}"


def one_clock(cli: CLI, pid: str, story: dict, mem: dict) -> dict:
    note(f"{story['auditor']['name']} checks every Q3 closure against TM-4 §3 (high risk → second reviewer) and §5 "
         "(no closing over an open screening item). First, what retrieval hands her: a search per alert")
    searches = []
    for a in alerts(mem):
        hits = search(cli, question(story, a), pid, echo=not searches)
        lines = [f"\n  {a['record']} · {story['customers'][a['customer']]} · closed {a['event_on']} by {a['by']} — top {len(hits)}:"]
        lines += [hit_line(h, a["event_on"]) for h in hits]
        emit("text", "\n".join(lines))
        searches.append(dict(alert=a["record"], hits=[public(h) for h in hits]))
    if len(searches) > 1:
        note(f"… and {len(searches) - 1} more searches the same way")
    note("every hit's text carries one date: the day it happened. When it reached the casebook is only in "
         "metadata.logged_on (the ← marks above come from it) — the text of a hit does not say")
    reads = {}
    for clock in ("event", "record"):
        a = audit(story, mem, clock)
        reads[clock] = a
        lines = [f"\n  read through the {CLOCKS[clock]['short']}  (every record: `fact list`, every page)"]
        for c in a["checks"]:
            lines.append(f"    {c['alert']:<7} {c['by']:<13} {c['day']}  {check_line(c)}")
        lines.append(f"    rating today: " + " · ".join(f"{story['customers'][k]} {v['rating']} ({v['record']})"
                                                       for k, v in a["ratings"].items()))
        lines.append(f"    → findings: {by_person(a['findings'])}")
        emit("text", "\n".join(lines))
    emit("oneclock", "", searches=searches, reads={k: v for k, v in reads.items()})
    return reads


# --------------------------------------------------------------------------- step 5: both clocks

def both_clocks(cli: CLI, pid: str, story: dict, reads: dict) -> dict:
    note("both clocks: the record clock decides what was on the analyst's file that day; the event clock decides which "
         "of those records was current. Same records, same code — only the two choices differ")
    mem = entries(list_facts(cli, pid))
    truth = audit(story, mem, "both")
    lines = []
    for c in truth["checks"]:
        happened = check_alert(mem, mem[c["alert"]], "event")
        missing = [x for x in happened["on_file"] if x not in c["on_file"]]
        lines.append(f"\n  {c['alert']} · {story['customers'][c['customer']]} · closed {c['day']} by {c['by']}")
        lines.append(f"    on file that day: {', '.join(c['on_file'])}")
        if missing:
            lines.append(f"    happened by then but not on file yet: "
                         + ", ".join(f"{x} (logged {mem[x]['logged_on']})" for x in missing))
        lines.append(f"    {check_line(c)}")
    lines.append(f"\n  rating today: " + " · ".join(f"{story['customers'][k]} {v['rating']} ({v['record']})"
                                                     for k, v in truth["ratings"].items()))
    emit("text", "\n".join(lines))
    rows = []
    for k in truth["answers"]:
        rows.append(dict(key=k, label=answer_label(story, k), event=reads["event"]["answers"][k],
                         record=reads["record"]["answers"][k], both=truth["answers"][k]))
    lines = [f"\n  {'check':<36} {'event clock':<13} {'record clock':<13} {'both clocks':<12}", "  " + "-" * 76]
    for r in rows:
        if r["event"] == r["both"] == r["record"] and not r["key"].endswith("rating") and r["both"] == "ok":
            continue  # nothing to see: all three agree it is fine
        m = lambda v: f"{v}{' ✗' if v != r['both'] else ''}"
        lines.append(f"  {r['label']:<36} {m(r['event']):<13} {m(r['record']):<13} {r['both']:<12}")
    quiet = sum(1 for r in rows if r["event"] == r["both"] == r["record"] and not r["key"].endswith("rating") and r["both"] == "ok")
    lines.append(f"  ({quiet} more checks: all three reads say ok)")
    wrong = {c: compare(reads[c], truth) for c in ("event", "record")}
    lines.append(f"\n  → event clock wrong on {len(wrong['event'])} of {len(rows)}; record clock wrong on "
                 f"{len(wrong['record'])} of {len(rows)}")
    for c in ("event", "record"):
        lines.append(f"    findings by the {CLOCKS[c]['label']:<13} {by_person(reads[c]['findings'])}")
    lines.append(f"    findings by {'both clocks':<17} {by_person(truth['findings'])}")
    emit("text", "\n".join(lines))
    out = dict(truth=truth, rows=rows, wrong=wrong)
    emit("both", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: write it back

CENSUS = "Late entry"


def census(cli: CLI, pid: str, mem: dict) -> list[str]:
    """`fact list --query` is an exact, case-insensitive substring match over every record's text."""
    by_fid = {r["fid"]: r for r in mem.values()}
    items = pages(cli, ["fact", "list", "--projects", pid, "--query", CENSUS])
    return sorted((by_fid.get(f["id"]) or {}).get("record") or "?" for f in items)


def stamp(r: dict) -> str:
    return f"(Late entry: logged {r['logged_on']} by {r['by']}, {lag(r)} days after the event.)"


def write_back(cli: CLI, pid: str, story: dict, truth: dict) -> dict:
    today = story["today"]
    note("write the second clock where every reader will see it: each late entry's text now says when it was logged "
         "(`fact update --text`; the old text goes to metadata.previous, both versions stay in `fact trace`)")
    mem = entries(list_facts(cli, pid, echo=False))
    text_before = audit(story, mem, "text")
    before = census(cli, pid, mem)
    updates = []
    for r in sorted(mem.values(), key=lambda r: r["record"]):
        if not is_late(story, r) or stated_logged(r):
            continue
        meta = {k: r[k] for k in ("key", "record", "kind", "customer", "event_on", "logged_on", "by", "rating", "rule",
                                  "resolves", "signoff") if k in r}
        meta.update(previous=r["body"], rewritten_on=today, rewritten_by=story["auditor"]["name"])
        updates.append((r, f"{r['body']} {stamp(r)}", meta))
    for i, (r, text, meta) in enumerate(updates):
        cli.run("fact", "update", r["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more late entries the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for r, _, _ in updates:
        lines.append(f"\n    {r['record']} now ends “… {clip(mem2[r['record']]['body'][len(r['body']):], 90)}”")
    emit("text", "\n".join(lines))
    after_c = census(cli, pid, mem2)
    emit("text", "\n".join([
        f"\n  `fact list --query '{CENSUS}'` (exact substring, every record): before {len(before)}{' — ' + ', '.join(before) if before else ''}",
        f"  {'':<{len(CENSUS) + 25}}after  {len(after_c)} — {', '.join(after_c) or '—'}"]))
    trace = []
    if updates:
        r = next((u[0] for u in updates if u[0]["kind"] in ITEMS), updates[0][0])
        res = cli.run("fact", "trace", r["fid"], "--project", pid, scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {r['record']} (newest first) — MemoryLake's own record clock for this fact:"]
        for e in trace:
            body = e["new"] if e["event"] != "UPDATE" or len(e["new"]) <= 84 else "… " + e["new"][-82:]
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(body, 84)}")
        emit("text", "\n".join(tl))
    text_after = audit(story, mem2, "text")
    wb, wa = compare(text_before, truth), compare(text_after, truth)
    n = len(truth["answers"])
    emit("text", "\n".join([
        f"\n  a read that only knows what each record's text says (its event date, and a logged date if it states one):",
        f"    before the write-back: wrong on {len(wb)} of {n} ({', '.join(wb) or '—'}) — the same as the event clock",
        f"    after:                 wrong on {len(wa)} of {n}{' (' + ', '.join(wa) + ')' if wa else ''}",
        f"    findings now: {by_person(text_after['findings'])}"]))
    out = dict(updated=[dict(record=r["record"], was=r["body"], now=mem2[r["record"]]["body"], lag=lag(r))
                        for r, _, _ in updates], census=dict(query=CENSUS, before=before, after=after_c),
               trace=trace, before=wb, after=wa, findings=text_after["findings"], total=n)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: asof, ask

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def resolve_customer(story: dict, name: str | None) -> list[str]:
    if not name:
        return customers(story)
    n = name.lower()
    hit = [c for c, label in story["customers"].items() if n in c or n in label.lower()]
    if not hit:
        die(f"no customer matches “{name}”; try one of: {', '.join(story['customers'])}")
    return hit


def asof_view(cli: CLI, pid: str, day: str, customer: str | None = None, echo: bool = True) -> dict:
    """The casebook on `day`: what was on file (record clock) next to what had happened (event clock)."""
    story = load_story()
    try:
        date.fromisoformat(day)
    except ValueError:
        die(f"“{day}” is not a date; use YYYY-MM-DD")
    mem = entries(list_facts(cli, pid, echo=echo))
    out = []
    for c in resolve_customer(story, customer):
        happened = on_file(mem, c, day, "event")
        filed = {r["record"] for r in on_file(mem, c, day, "record")}
        rows = [dict(public(r), on_file=r["record"] in filed) for r in happened]
        # Records logged by `day` that happened later cannot exist; nothing else to add.
        reads = {}
        for clock in ("event", "record", "both"):
            r = rating_at(mem, c, day, clock)
            reads[clock] = dict(rating=(r or {}).get("rating"), record=(r or {}).get("record"),
                                items=[x["record"] for x in open_items(mem, c, day, clock)])
        out.append(dict(customer=c, name=story["customers"][c], rows=rows, reads=reads,
                        stated=[r["record"] for r in happened if stated_logged(r)]))
    return dict(day=day, customers=out)


def ask_text(cli: CLI, pid: str, text: str, day: str | None = None, echo: bool = True) -> dict:
    """Any question: the search hits with both clocks; with a day, which hits were on file that day."""
    mem = entries(list_facts(cli, pid, echo=False))
    hits = search(cli, text, pid, top_k=8, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    rows = []
    for h in hits:
        r = by_fid.get(h["fid"])
        cur = dict(r or h, rank=h["rank"])
        row = public(cur)
        row["cached"] = r is None
        if day:
            row["happened"] = cur["event_on"] <= day
            row["on_file"] = cur["logged_on"] <= day
        rows.append(row)
    return dict(question=text, day=day, hits=rows)


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    proj = find_project(cli, story["project"]["key"], echo=not quiet)
    if not proj:
        note("nothing to delete")
        emit("cleaned", "")
        return
    facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
    for f in facts:
        cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
    left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
    if left:
        die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
    cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
    note(f"deleted the project and its {len(facts)} facts")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, f"Set up — one project for {story['bank']}'s Q3 casebook")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records, each with the day it happened and the day it was logged")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "One clock — the audit read by the dates in the records, then by the order they were logged")
    reads = one_clock(cli, pid, story, mem)
    banner(5, TOTAL, "Both clocks — what was on file that day, and which of it was current")
    both = both_clocks(cli, pid, story, reads)
    banner(6, TOTAL, "Write it back — each late entry says when it was logged, then the plain read again")
    wb = write_back(cli, pid, story, both["truth"])
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   wrong={k: v for k, v in both["wrong"].items()},
                   findings={c: [f"{f['alert']} {f['rule']}" for f in reads[c]["findings"]] for c in ("event", "record")},
                   truth=[f"{f['alert']} {f['rule']}" for f in both["truth"]["findings"]],
                   census=[len(wb["census"]["before"]), len(wb["census"]["after"])],
                   text_before=wb["before"], text_after=wb["after"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def print_asof(v: dict) -> None:
    lines = []
    for c in v["customers"]:
        lines.append(f"\n  {c['name']} on {v['day']}")
        lines.append(f"    {'record':<10} {'happened':<11} {'logged':<11} {'on file?':<9} text")
        for r in c["rows"]:
            mark = "yes" if r["on_file"] else "NO"
            lines.append(f"    {r['record']:<10} {r['event_on']:<11} {r['logged_on']:<11} {mark:<9} {clip(r['body'], 64)}")
        if not c["rows"]:
            lines.append("    (nothing had happened yet)")
        for clock in ("event", "record", "both"):
            x = c["reads"][clock]
            lines.append(f"    {CLOCKS[clock]['label']:<13} rating {x['rating'] or '—'} ({x['record'] or '—'}) · "
                         f"open items {', '.join(x['items']) or 'none'}")
        late = [r["record"] for r in c["rows"] if not r["on_file"]]
        if late:
            lines.append(f"    · {len(late)} record(s) describe something that had happened but was not on file yet: "
                         f"{', '.join(late)}")
    emit("text", "\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "asof", "ask", "cleanup"],
                    help="run = full demo (default); asof DAY [CUSTOMER] = the casebook on a day: what had happened "
                         "and what was on file; ask \"QUESTION\" [--as-of DAY] = search hits with both clocks; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="asof: the day and optionally a customer; ask: the question")
    ap.add_argument("--as-of", dest="as_of", help="ask: mark which hits were on file on this day (YYYY-MM-DD)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command == "asof" and not 1 <= len(a.args) <= 2:
        ap.error("asof needs a day and optionally a customer, e.g. asof 2026-09-18 orion")
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one question, e.g. ask \"Did anyone flag Orion Freight's director?\" --as-of 2026-09-18")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py asof 2026-09-18 orion`, `python3 demo.py ask \"Was anything known about "
              "Orion Freight's director?\" --as-of 2026-09-18`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "asof":
        banner(2, 2, f"As of {a.args[0]} — what had happened, and what was on file")
        print_asof(asof_view(cli, pid, a.args[0], a.args[1] if len(a.args) > 1 else None))
    elif a.command == "ask":
        banner(2, 2, "Ask — search hits with both clocks")
        r = ask_text(cli, pid, a.args[0], a.as_of)
        lines = [f"\n  “{r['question']}” — top {len(r['hits'])}:",
                 f"      {'#':<3} {'record':<10} {'happened':<11} {'logged':<11} {'on file?' if r['day'] else '':<9} text"]
        for h in r["hits"]:
            f = ("yes" if h["on_file"] else "NO" if h["happened"] else "later") if r["day"] else ""
            lines.append(f"      #{h['rank']:<2} {h['record']:<10} {h['event_on']:<11} {h['logged_on']:<11} {f:<9} "
                         f"{clip(h['body'], 60)}")
        if r["day"]:
            n = sum(1 for h in r["hits"] if h["happened"] and not h["on_file"])
            lines.append(f"  · on {r['day']}: {n} of these had happened but were not on file yet")
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

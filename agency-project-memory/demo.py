#!/usr/bin/env python3
"""
Project memory for marketing agencies — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Harbor & Pine (a fictional agency) runs the Ember launch for Fieldstone Coffee
Roasters (a fictional client) out of one MemoryLake project: what every role
wrote in their own tool — Slack, Asana, Notion, Figma, Docs, the client's
email, the finance sheet — one fact per record, word for word, with the tool,
the author and the day it was written in metadata.  Today, 2026-10-15, Priya
Raman takes over the account and preps the Thursday status call.

The quick read is what the page promises — every role's AI reads the same
project state: `search` the question, take the record on that topic, and
answer.  The records are true.  But five of them say "two weeks from today",
"tomorrow", "the same as last quarter", "the new lockup", "until the end of
the month" — and the quick read resolves those against the day it reads them.
The only date it has is the hit's `created_at`, the day the record was synced
into MemoryLake; the day it was written is in `metadata.on`.

The memory read uses the same resolver with one change: the anchor is the
record's own date, and only the records already on file that day count.  "Two
weeks from today", written on 2026-09-30, is 2026-10-14 — yesterday, and Asana
says the page is blocked.  The launch email is the control: its record carries
an absolute date, and the two reads agree.

Step 6 writes each resolution back where the next read will see it (`fact
update --text`, the old text kept in metadata.previous, both versions in `fact
trace`).  The same quick-read code then agrees on every question.

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

PREFIX = "mlu-apm"  # memorylake-usecases / agency-project-memory


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
    m = dict(key=f"apm:{r['id']}", record=r["id"], tool=r["tool"], by=r["by"], role=r["role"], on=r["on"],
             topic=r["topic"], kind=r["kind"])
    for k in ("done", "quarter", "amount", "version"):
        if k in r:
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


def list_facts(cli: CLI, pid: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (SLK-0930, FIN-Q2 …), metadata flattened in, plus fact id, text, created_at."""
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


def public(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {k: r.get(k) for k in ("record", "tool", "by", "role", "on", "topic", "kind", "done", "quarter", "amount",
                                  "version", "fid", "body", "rank", "created", "previous")}


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, pid: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        created=f.get("created_at") or "", record=m.get("record") or "?", tool=m.get("tool") or "?",
                        on=m.get("on") or "?"))
    return out


def hit_line(story: dict, h: dict, mark: str = "") -> str:
    tool = story["tools"].get(h["tool"], h["tool"])
    return f"      #{h['rank']:<3} {h['record']:<9} {tool:<8} {h['on']:<11} {clip(h['body'], 76)}{mark}"


# --------------------------------------------------------------------------- relative references

# Phrases whose meaning depends on the day they are read. One resolver for both reads; only the anchor differs.
RELATIVE = [
    ("two weeks from today", "days", 14),
    ("tomorrow", "days", 1),
    ("the same as last quarter", "last_quarter", 0),
    ("the new lockup", "newest_lockup", 0),
    ("until the end of the month", "end_of_month", 0),
]
ISO = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def day(s: str) -> date:
    return date.fromisoformat(s[:10])


def prev_quarter(d: date) -> str:
    y, q = d.year, (d.month - 1) // 3 + 1
    return f"{y - 1}-Q4" if q == 1 else f"{y}-Q{q - 1}"


def qlabel(q: str) -> str:
    y, n = q.split("-")
    return f"{n} {y}"


def relative_phrase(text: str) -> str | None:
    low = (text or "").lower()
    return next((p for p, _, _ in RELATIVE if p in low), None)


def resolve(text: str, anchor: str, mem: dict) -> dict | None:
    """What the relative reference in `text` points at when read on `anchor`. Things it refers to (last quarter's
    spend, the newest lockup) are looked up among the records already on file that day. None: no relative phrase."""
    phrase = relative_phrase(text)
    if phrase is None:
        return None
    how, n = next((h, n) for p, h, n in RELATIVE if p == phrase)
    a = day(anchor)
    known = [r for r in mem.values() if r.get("on", "9999") <= anchor]
    out = dict(phrase=phrase, anchor=anchor, value=None, absolute=None, via=None)
    if how == "days":
        v = (a + timedelta(days=n)).isoformat()
        out.update(value=v, absolute=f"on {v}")
    elif how == "end_of_month":
        nxt = date(a.year + a.month // 12, a.month % 12 + 1, 1)
        v = (nxt - timedelta(days=1)).isoformat()
        out.update(value=v, absolute=f"until {v}")
    elif how == "last_quarter":
        q = prev_quarter(a)
        r = next((r for r in known if r.get("kind") == "spend" and r.get("quarter") == q), None)
        if r:
            v = f"${int(r['amount']):,}"
            out.update(value=v, absolute=f"{v} (the {qlabel(q)} figure)", via=r["record"])
        else:
            out.update(missing=f"no {qlabel(q)} figure on file")
    elif how == "newest_lockup":
        ls = sorted((r for r in known if r.get("kind") == "lockup"), key=lambda r: r["on"])
        if ls:
            v = f"lockup {ls[-1]['version']}"
            out.update(value=v, absolute=v, via=ls[-1]["record"])
    return out


def literal(kind: str, text: str):
    """The answer written out in the record itself (no relative phrase)."""
    if kind == "date":
        m = ISO.search(text)
        return m.group(1) if m else None
    if kind == "hold":
        m = re.search(r"until (20\d\d-\d\d-\d\d)", text)
        return m.group(1) if m else None
    if kind == "amount":
        m = re.search(r"\$[\d,]+", text)
        return m.group(0) if m else None
    if kind == "lockup":
        m = re.search(r"\blockup (v\d+)\b", text)
        return f"lockup {m.group(1)}" if m else None
    return None


def latest_status(mem: dict, topic: str, today: str) -> dict | None:
    st = sorted((r for r in mem.values() if r.get("kind") == "status" and r.get("topic") == topic and r["on"] <= today),
                key=lambda r: r["on"])
    return st[-1] if st else None


def answer(story: dict, topic: str, plan: dict, res: dict | None, mem: dict) -> dict:
    """Turn a record (and its resolved reference, if any) into the status-call answer. Same code for both reads."""
    today = story["today"]
    kind = story["topics"][topic]["answer"]
    value = res["value"] if res else literal(kind, plan["body"])
    via = mem.get(res["via"]) if res and res.get("via") else None
    out = dict(value=value, key=value, via=public(via), status=None, overdue=0, said="", short=str(value))
    if value is None:
        out["said"] = out["short"] = "can't tell" + (f" ({res['missing']})" if res and res.get("missing") else "")
        return out
    if kind == "date":
        n = (day(value) - day(today)).days
        if n >= 0:
            out["said"] = f"{value} — in {n} day{'s' if n != 1 else ''}"
            out["short"] = f"{value} · in {n} d"
        else:
            st = latest_status(mem, topic, today)
            out["status"] = public(st)
            if st and st.get("done") == "yes":
                out["said"] = f"{value} — done"
            else:
                out["overdue"] = -n
                out["short"] = f"{value} · OVERDUE {-n} d"
                out["said"] = f"{value} — OVERDUE by {-n} day{'s' if n != -1 else ''}" + (
                    f"; latest: {st['record']} ({st['on']}) “{clip(st['body'], 70)}”" if st else "; nothing on file says it happened")
    elif kind == "hold":
        if value >= today:
            out.update(key="hold", said=f"no — on hold until {value}", short=f"no · hold to {value}")
        else:
            out.update(key="free", said=f"yes — the hold ended {value}", short=f"yes · hold ended {value}")
    else:
        out["said"] = value + (f" — {via['record']}: “{clip(via['body'], 90)}”" if via else "")
    return out


def plan_of(mem: dict, topic: str) -> dict | None:
    ps = sorted((r for r in mem.values() if r.get("kind") == "plan" and r.get("topic") == topic), key=lambda r: r["on"])
    return ps[-1] if ps else None


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, q: dict, echo: bool = True) -> dict:
    """The page's "every role's AI reads the same project state", done the quick way: `search` the question, take the
    best-ranked record that states the plan on that topic, resolve whatever it says against today. The record is read
    from `fact list` by fact id (search can answer from a cache of older text)."""
    story = load_story()
    hits = search(cli, q["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    cited = None
    for h in hits:
        cur = by_fid.get(h["fid"])
        if cur and cur.get("kind") == "plan" and cur.get("topic") == q["topic"]:
            cited = dict(cur, rank=h["rank"], hit_created=h["created"])
            break
    if cited is None:
        return dict(question=q, hits=hits, cited=None, res=None, cached=cached,
                    ans=dict(value=None, key=None, short="—", said=f"nothing on {story['topics'][q['topic']]['label'].lower()} in "
                                                         f"search's top {len(hits)}", status=None, via=None, overdue=0))
    res = resolve(cited["body"], story["today"], mem)
    return dict(question=q, hits=hits, cited=cited, res=res, cached=cached, ans=answer(story, q["topic"], cited, res, mem))


# --------------------------------------------------------------------------- read 2: anchored where it was written

def memory_read(mem: dict, q: dict) -> dict:
    """Every record (`fact list`, every page): the plan on the topic, its relative reference resolved on the day it was
    written (metadata.on), against what was on file that day; then the same answer code."""
    story = load_story()
    plan = plan_of(mem, q["topic"])
    if plan is None:
        return dict(plan=None, res=None, ans=dict(value=None, key=None, short="—", said="no plan on file", status=None, via=None,
                                                  overdue=0))
    res = resolve(plan["body"], plan["on"], mem)
    return dict(plan=plan, res=res, ans=answer(story, q["topic"], plan, res, mem))


def res_line(res: dict | None, read_on: str, label: str) -> str:
    if not res:
        return f"  · {label}: no relative phrase — the record says it outright"
    return f"  · {label}: “{res['phrase']}” read on {read_on} → {res['value'] or res.get('missing')}"


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
    emit("text", f"\n    project  {p['name']:<40} {proj['id']}")
    emit("setup", "", project=proj["id"], name=p["name"])
    return proj["id"]


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key) — one `fact add` for all of them,
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


REWRITE_KEYS = ("previous", "rewritten_on", "rewritten_by", "resolved_from", "resolved_on")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of Priya's first status call: records an earlier run rewrote are put back word
    for word from metadata.previous."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous") and m.get("kind") == "plan":
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} record(s); put back, so this run starts from the same morning")
    return n


def census(cli: CLI, pid: str) -> list[dict]:
    """`fact list --query` per relative phrase: an exact, case-insensitive substring match over every record."""
    out = []
    for i, (phrase, _, _) in enumerate(RELATIVE):
        items = pages(cli, ["fact", "list", "--projects", pid, "--query", phrase], echo=i == 0)
        out.append(dict(phrase=phrase, records=[(f.get("metadata") or {}).get("record") or "?" for f in items]))
    if len(RELATIVE) > 1:
        note(f"… and {len(RELATIVE) - 1} more `fact list --query` the same way")
    return out


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("seven tools, one project: one fact per record, word for word, then `fact update --metadata` (tool, author, "
         "role, the day it was written, topic)")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
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
        rs = sorted((r for r in mem.values() if r["tool"] == tool), key=lambda r: r["on"])
        lines.append(f"    {label:<8} {len(rs):>2}   " + " · ".join(r["record"] for r in rs))
    emit("text", "\n".join(lines))
    note("which records mean something different depending on the day they are read? Ask the whole project, one "
         "relative phrase at a time")
    cen = census(cli, pid)
    lines = []
    for c in cen:
        lines.append(f"    “{c['phrase']}”{'':<{28 - len(c['phrase'])}} {len(c['records'])}  {', '.join(c['records'])}")
    lines.append("\n  Two dates on each of them — the day it was written (metadata.on, from the tool) and the day it reached "
                 "MemoryLake (created_at):")
    rel = sorted((r for r in mem.values() if relative_phrase(r["body"])), key=lambda r: r["on"])
    for r in rel:
        lines.append(f"    {r['record']:<9} written {r['on']} by {r['by']:<14} created_at {r['created'][:16]}Z  "
                     f"“{clip(r['body'], 60)}”")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["on"], r["record"]))],
         census=cen, relative=[r["record"] for r in rel], restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{story['lead']['name']} preps the status call: {len(story['questions'])} questions. The quick read: `search` "
         f"the question, take the record on that topic, answer as of today ({story['today']})")
    out = []
    for q in story["questions"]:
        r = quick_read(cli, pid, mem, q)
        lines = [f"\n  {q['id']} “{q['text']}”", f"  search · top {len(r['hits'])}:"]
        c = r["cited"]
        for h in r["hits"]:
            lines.append(hit_line(story, h, "   ← the record" if c and h["record"] == c["record"] else ""))
        if r["cached"]:
            lines.append(f"  · {len(r['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if c:
            lines.append(f"  · {c['record']}: hit created_at {c['hit_created'][:16]}Z — when it was synced, not the day "
                         f"{c['by'].split()[0]} wrote it")
            lines.append(res_line(r["res"], story["today"], "read today"))
        lines.append(f"  → quick read: {r['ans']['said']}")
        emit("text", "\n".join(lines))
        out.append(r)
    emit("quick", "", reads=[dict(question=r["question"], cited=public(r["cited"]),
                                  hit_created=(r["cited"] or {}).get("hit_created", ""), res=r["res"], ans=r["ans"],
                                  hits=[public(h) for h in r["hits"]], cached=r["cached"]) for r in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every record (`fact list`, every page); the same resolver, anchored on the day each record "
         "was written (metadata.on), against what was on file that day")
    mem = entries(list_facts(cli, pid))
    rows = []
    for r in quick:
        q = r["question"]
        m = memory_read(mem, q)
        p = m["plan"]
        lines = [f"\n  {q['id']} {story['topics'][q['topic']]['label']}"]
        if p:
            lines.append(f"    {p['record']} · {story['tools'][p['tool']]} · {p['by']} ({p['role']}) · written {p['on']}")
            lines.append(f"    “{p['body']}”")
            lines.append(res_line(r["res"], story["today"], "quick read, today      "))
            lines.append(res_line(m["res"], p["on"], "memory read, as written"))
        agree = r["ans"]["key"] == m["ans"]["key"]
        lines.append(f"  → quick read:  {r['ans']['said']}")
        lines.append(f"  → memory read: {m['ans']['said']}  {'✓ same' if agree else '✗ differs'}")
        emit("text", "\n".join(lines))
        rows.append(dict(question=q, quick=r["ans"], quick_res=r["res"], cited=r["cited"], plan=p, res=m["res"],
                         memory=m["ans"], agree=agree))
    lines = [f"\n  {'':<4} {'question':<22} {'quick read (today)':<29} {'memory read (as written)':<29}", "  " + "-" * 92]
    for x in rows:
        lines.append(f"  {x['question']['id']:<4} {story['topics'][x['question']['topic']]['label']:<22} "
                     f"{x['quick']['short']:<29} {x['memory']['short']:<29} "
                     f"{'✓ same' if x['agree'] else '✗ differs'}")
    wrong = [x for x in rows if not x["agree"]]
    late = [x for x in rows if x["memory"]["overdue"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)} answers change with the day the record is read"
                 + (f"; {len(late)} of them are overdue, not upcoming ({', '.join(x['question']['id'] for x in late)})" if late else ""))
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(question=x["question"], quick=x["quick"], quick_res=x["quick_res"],
                                  plan=public(x["plan"]), res=x["res"], memory=x["memory"], agree=x["agree"])
                             for x in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def write_back(cli: CLI, pid: str, story: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next read will see it: each relative phrase is replaced by what it meant on the day "
         "it was written (`fact update --text`; the old text goes to metadata.previous)")
    mem = entries(list_facts(cli, pid, echo=False))
    updates = []
    for r in sorted(mem.values(), key=lambda r: r["on"]):
        res = resolve(r["body"], r["on"], mem) if r.get("kind") == "plan" else None
        if not res or not res.get("absolute"):
            continue
        i = r["body"].lower().index(res["phrase"])
        text = r["body"][:i] + res["absolute"] + r["body"][i + len(res["phrase"]):]
        meta = {k: r[k] for k in ("key", "record", "tool", "by", "role", "on", "topic", "kind")}
        meta.update(previous=r["body"], resolved_from=res["phrase"], resolved_on=r["on"], rewritten_on=today,
                    rewritten_by=story["lead"]["name"])
        updates.append((r, text, meta))
    for i, (r, text, meta) in enumerate(updates):
        cli.run("fact", "update", r["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more record(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for r, _, _ in updates:
        lines.append(f"\n    {r['record']} was “{r['body']}”")
        lines.append(f"    {'':<{len(r['record'])}} now “{mem2[r['record']]['body']}”")
    emit("text", "\n".join(lines))
    trace = []
    if updates:
        r = updates[0][0]
        res = cli.run("fact", "trace", r["fid"], "--project", pid, scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {r['record']} (newest first):"]
        for e in trace:
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(e['new'], 90)}")
        emit("text", "\n".join(tl))
    note("the same questions in other words (search caches answers by query text) through the same quick-read code. "
         "Search gets a rewritten fact a few seconds after `fact list` does; each read is retried until no hit is stale")
    time.sleep(6)
    after = []
    for q in story["followups"]:
        t1, tries = time.time(), 0
        while True:
            tries += 1
            r = quick_read(cli, pid, mem2, q, echo=tries == 1)
            if not r["cached"] or tries >= 8:
                break
            time.sleep(4)
        m = memory_read(mem2, q)
        after.append(dict(question=q, cited=(r["cited"] or {}).get("record"), rank=(r["cited"] or {}).get("rank"),
                          quick=r["ans"]["said"], memory=m["ans"]["said"], agree=r["ans"]["key"] == m["ans"]["key"],
                          tries=tries, secs=int(time.time() - t1), cached=r["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['question']['id']:<4} “{a['question']['text']}” → {a['cited'] or '—'}"
                     + (f" (#{a['rank']})" if a["rank"] else ""))
        lines.append(f"    quick read:  {a['quick']}{' (try ' + str(a['tries']) + ')' if a['tries'] > 1 else ''}")
        lines.append(f"    memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees on {agree} of {len(after)}: the records say what they meant, not when "
                 "they were said")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=r["record"], was=r["body"], now=mem2[r["record"]]["body"], phrase=m["resolved_from"],
                             on=r["on"]) for r, _, m in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, timeline

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def ask_text(cli: CLI, pid: str, text: str, topic: str, echo: bool = True) -> dict:
    """Any question on a topic: the quick read and the memory read side by side."""
    mem = entries(list_facts(cli, pid, echo=False))
    q = dict(id="(yours)", topic=topic, text=text)
    r = quick_read(cli, pid, mem, q, echo=echo)
    m = memory_read(mem, q)
    p, before = m["plan"], None
    if p and p.get("previous"):
        # Rewritten by step 6: what the original words read as, on the day they were written and today.
        before = dict(text=p["previous"], today=resolve(p["previous"], load_story()["today"], mem),
                      then=resolve(p["previous"], p["on"], mem))
    return dict(question=q, hits=[public(h) for h in r["hits"]], cited=public(r["cited"]), res=r["res"], quick=r["ans"],
                plan=public(p), memory_res=m["res"], memory=m["ans"], agree=r["ans"]["key"] == m["ans"]["key"],
                before=before)


def timeline(cli: CLI, pid: str) -> list[dict]:
    """Every record with a relative phrase: what it meant the day it was written, and what it reads as today."""
    story = load_story()
    mem = entries(list_facts(cli, pid))
    rows = []
    for r in sorted(mem.values(), key=lambda r: r["on"]):
        phrase = relative_phrase(r["body"])
        was = (r.get("previous") or "") if not phrase else ""
        if not phrase and not (was and relative_phrase(was)):
            continue
        body = r["body"] if phrase else was
        then, now = resolve(body, r["on"], mem), resolve(body, story["today"], mem)
        rows.append(dict(record=r["record"], tool=r["tool"], by=r["by"], on=r["on"], phrase=then["phrase"],
                         then=then["value"], today=now["value"], rewritten=not phrase))
    return rows


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
    banner(2, TOTAL, "Set up — one project for the Ember launch")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records from {len(story['tools'])} tools, and two clocks")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — search the question, answer as of today")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — each record anchored on the day it was written")
    rows = memory_review(cli, pid, story, quick)
    banner(6, TOTAL, "Write it back — what each phrase meant, then the quick read again")
    wb = write_back(cli, pid, story, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   cited={r["question"]["id"]: [(r["cited"] or {}).get("record"), (r["cited"] or {}).get("rank")]
                          for r in quick},
                   quick={x["question"]["id"]: x["quick"]["key"] for x in rows},
                   memory={x["question"]["id"]: x["memory"]["key"] for x in rows},
                   agree=[x["question"]["id"] for x in rows if x["agree"]],
                   cached=[c for r in quick for c in r["cached"]],
                   after={a["question"]["id"]: [a["cited"], a["rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "timeline", "cleanup"],
                    help="run = full demo (default); ask \"QUESTION\" --topic T = the quick read and the memory read of "
                         "any question; timeline = every record with a relative phrase, as written and as read today; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask)")
    story = load_story()
    answerable = [k for k, t in story["topics"].items() if t.get("answer")]
    ap.add_argument("--topic", default="golive", choices=answerable, help="ask: what the question is about")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one question, e.g. ask \"Is the Ember landing page late?\" --topic golive")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py timeline`, `python3 demo.py ask \"Which lockup goes on the Ember bag "
              "labels?\" --topic lockup`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — the quick read and the memory read")
        r = ask_text(cli, pid, a.args[0], a.topic)
        c = r["cited"]
        lines = [f"\n  “{r['question']['text']}” · {story['topics'][a.topic]['label']} — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← the record" if c and h["record"] == c["record"] else "") for h in r["hits"]]
        lines.append(res_line(r["res"], story["today"], "read today"))
        lines.append(f"  → quick read:  {r['quick']['said']}")
        if r["plan"]:
            lines.append(res_line(r["memory_res"], r["plan"]["on"], f"as written ({r['plan']['record']})"))
        lines.append(f"  → memory read: {r['memory']['said']}  {'✓ same' if r['agree'] else '✗ differs'}")
        b = r["before"]
        if b and b["today"]:
            lines.append(f"  · before step 6 rewrote {r['plan']['record']} it said “{clip(b['text'], 90)}”")
            lines.append(f"    “{b['today']['phrase']}” read today → {b['today']['value']}; as written on {r['plan']['on']} → "
                         f"{b['then']['value']}")
        emit("text", "\n".join(lines))
    elif a.command == "timeline":
        banner(2, 2, "Timeline — every relative phrase, as written and as read today")
        rows = timeline(cli, pid)
        lines = [f"\n  {'record':<9} {'written':<11} {'by':<15} {'phrase':<28} {'meant':<12} {'reads today as':<14}",
                 "  " + "-" * 92]
        for r in rows:
            lines.append(f"  {r['record']:<9} {r['on']:<11} {r['by']:<15} {r['phrase']:<28} {str(r['then']):<12} "
                         f"{str(r['today']):<14}{'  (rewritten)' if r['rewritten'] else ''}")
        n = sum(1 for r in rows if r["then"] != r["today"])
        lines.append(f"\n  · {n} of {len(rows)} read differently today than the day they were written")
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

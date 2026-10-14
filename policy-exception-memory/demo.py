#!/usr/bin/env python3
"""
Policy exception memory for HR teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Freight (fictional) runs its HR desk out of one MemoryLake project:
the people handbook (word for word, one fact per section), every decision the
HR desk made this year (the ticket as Hana Sato closed it), and the CPO office's
exception register.  Omar Haddad took over the desk on Monday; four requests
are waiting.

The quick read is what the page promises — the AI remembers how HR handled the
same kind of request before: `search` the request, find a past ticket of the
same kind that was approved for as much or more, and answer "approved before,
approve again".  For Dana's five weeks in Porto it finds HR-2231: Priya Nair,
six weeks in Lisbon, approved.

The ticket is true.  It just isn't a precedent.  The memory read takes the
handbook section on the topic, every decision on it and the exception register
(`fact list`, every page), joins the register to the tickets by ticket id, and
judges the request against the handbook limit: HR-2231 was exception EXC-01,
one-time, signed by the CPO — and five weeks is over the four-week limit.  The
"not a precedent" line was always in memory; it was in a different record from
the one the quick read cited.  The bereavement request is the control: within
the handbook limit, the two reads agree.

Step 6 writes each exception's qualifier onto its ticket (`fact update --text`,
the old text kept in metadata.previous, the change visible in `fact trace`).
The same quick-read code — which always skipped a ticket that says it is
one-time — then agrees with the handbook on the day's later requests.

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

PREFIX = "mlu-pxm"  # memorylake-usecases / policy-exception-memory


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
    m = dict(key=f"pxm:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], topic=r["topic"], by=r["by"], on=r["on"])
    if r["kind"] == "policy":
        m.update(limit=r["limit"], exceptions_by=r["exceptions_by"])
    elif r["kind"] == "decision":
        m.update(subject=r["subject"], value=r["value"], outcome=r["outcome"])
    else:
        m.update(ticket=r["ticket"], policy=r["policy"], scope=r["scope"], approved_by=r["approved_by"])
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
    """The pinned records by record id (POL-4.2, HR-2231, EXC-01 …), metadata flattened in, plus fact id and text."""
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


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "tool", "kind", "topic", "by", "on", "limit", "exceptions_by", "subject",
                                  "value", "outcome", "ticket", "policy", "scope", "approved_by", "exception", "fid",
                                  "body", "rank", "previous")}


def amount(v, unit: str) -> str:
    return f"{v} dollars" if unit == "dollars" else f"{v} {unit if v != 1 else unit.rstrip('s')}"


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, pid: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, "--projects", pid, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", tool=m.get("tool") or "?", kind=m.get("kind") or "?"))
    return out


def hit_line(story: dict, h: dict, mark: str = "") -> str:
    tool = story["tools"].get(h["tool"], h["tool"])
    return f"      #{h['rank']:<3} {h['record']:<8} {tool:<10} {clip(h['body'], 84)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

ONE_TIME = re.compile(r"\bone-time\b|\bnot a precedent\b", re.I)


def says_one_time(body: str) -> bool:
    """What the quick read does honour: a ticket that itself says it is a one-time exception is not a precedent."""
    return bool(ONE_TIME.search(body or ""))


def quick_read(cli: CLI, pid: str, mem: dict, q: dict, echo: bool = True) -> dict:
    """The page's "remembers how HR handled it before", done the quick way: search the request, take the best-ranked
    past ticket of the same kind that was approved for as much or more, and approve again. A ticket whose own text
    says it was a one-time exception is skipped. Tickets are read from `fact list` by fact id (search can answer from
    a cache of older text)."""
    hits = search(cli, q["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    precedent, skipped = None, []
    for h in hits:
        cur = by_fid.get(h["fid"], h)
        if cur.get("kind") != "decision" or cur.get("topic") != q["topic"] or cur.get("outcome") != "approved":
            continue
        if (cur.get("value") or 0) < q["value"]:
            continue
        if says_one_time(cur["body"]):
            skipped.append(cur["record"])
            continue
        precedent = dict(cur, rank=h["rank"])
        break
    if precedent:
        advice = "approve"
        unit = load_story()["topics"][q["topic"]]["unit"]
        said = f"approve — {precedent['record']} approved {precedent['subject']} for {amount(precedent['value'], unit)}"
    else:
        advice = "no precedent"
        said = "no past approval covers it" + (f" ({', '.join(skipped)} say{'s' if len(skipped) == 1 else ''} one-time)"
                                               if skipped else "")
    return dict(ticket=q, hits=hits, precedent=precedent["record"] if precedent else None,
                precedent_rank=precedent["rank"] if precedent else None,
                precedent_body=precedent["body"] if precedent else "", skipped=skipped, advice=advice, said=said,
                cached=cached)


# --------------------------------------------------------------------------- read 2: the handbook, every decision, the register

def basis_of(d: dict, exc: dict | None, pol: dict | None) -> str:
    if exc:
        return f"exception {exc['record']} · {exc['scope']} · {exc['approved_by']}"
    if pol and d.get("value") is not None and d["value"] <= pol["limit"]:
        return f"within {pol['record']}"
    return "over the limit, no exception on file"


def precedents(mem: dict, topic: str) -> dict:
    """The handbook section on the topic, every decision on it, and the register joined to them by ticket id."""
    pol = next((r for r in mem.values() if r.get("kind") == "policy" and r.get("topic") == topic), None)
    excs = {r["ticket"]: r for r in mem.values() if r.get("kind") == "exception" and r.get("topic") == topic}
    rows = []
    for d in sorted((r for r in mem.values() if r.get("kind") == "decision" and r.get("topic") == topic),
                    key=lambda r: r["on"]):
        e = excs.get(d["record"])
        rows.append(dict(record=d["record"], subject=d["subject"], value=d["value"], on=d["on"], body=d["body"],
                         exception=e["record"] if e else None, basis=basis_of(d, e, pol)))
    return dict(topic=topic, policy=pol, rows=rows, exceptions=list(excs.values()))


def judge(story: dict, pc: dict, value) -> tuple[str, str]:
    pol = pc["policy"]
    unit = story["topics"][pc["topic"]]["unit"]
    if pol is None:
        return "?", "no handbook section on this topic"
    if value <= pol["limit"]:
        return "approve", f"approve — {amount(value, unit)} is within {pol['record']} ({amount(pol['limit'], unit)})"
    n = len(pc["exceptions"])
    ids = ", ".join(e["record"] for e in pc["exceptions"])
    return "decline", (f"decline — {amount(value, unit)} is over {pol['record']} ({amount(pol['limit'], unit)}); only a "
                       f"written {pol['exceptions_by']} exception can allow it"
                       + (f" ({n} granted this year: {ids}, each one-time)" if n else ""))


def memory_read(cli: CLI, pid: str, q: dict, echo: bool = True) -> dict:
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    pc = precedents(mem, q["topic"])
    advice, said = judge(story, pc, q["value"])
    return dict(advice=advice, said=said, prec=pc)


def agrees(quick: str, memory: str) -> bool:
    return (quick == "approve") == (memory == "approve")


def prec_lines(story: dict, pc: dict, mark: str = "") -> list[str]:
    unit = story["topics"][pc["topic"]]["unit"]
    pol = pc["policy"]
    lines = []
    if pol:
        lines.append(f"      {pol['record']:<8} handbook   limit {amount(pol['limit'], unit)}; more needs a written "
                     f"{pol['exceptions_by']} exception")
    for r in pc["rows"]:
        tag = "   ← the quick read's precedent" if r["record"] == mark else ""
        lines.append(f"      {r['record']:<8} {r['on']}  {r['subject']:<13} {amount(r['value'], unit):<12} {r['basis']}{tag}")
    return lines


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


REWRITE_KEYS = ("previous", "rewritten_on", "rewritten_by", "exception", "scope")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of Omar's first desk shift: tickets an earlier run rewrote are put back word
    for word from metadata.previous."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous") and m.get("kind") == "decision":
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} ticket(s); put back, so this run starts from the same morning")
    return n


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("three sources, one project: the people handbook, the HR desk's decisions this year and the CPO office's "
         "exception register. One fact per record, word for word, then `fact update --metadata` (topic, amount, "
         "outcome; for the register, the ticket it covers)")
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
        rs = [r for r in mem.values() if r["tool"] == tool]
        lines.append(f"    {label:<11} {len(rs):>2}   " + " · ".join(
            f"{story['topics'][t]['label']} {sum(1 for r in rs if r['topic'] == t)}" for t in story["topics"]
            if any(r["topic"] == t for r in rs)))
    lines.append("\n  The exception register, as the CPO office keeps it:")
    for r in sorted((r for r in mem.values() if r["kind"] == "exception"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<8} → {r['ticket']}  {clip(r['body'], 120)}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} requests waiting. The quick read: `search` the request, take the best-ranked past "
         "ticket of the same kind approved for as much or more, approve again")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']} “{t['text']}”", f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← precedent" if h["record"] == q["precedent"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["precedent"]:
            lines.append(f"  → {q['precedent']}: “{clip(q['precedent_body'], 150)}”")
        lines.append(f"  → quick read: {q['said']}")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], precedent=q["precedent"], precedent_body=q["precedent_body"],
                                  advice=q["advice"], said=q["said"], skipped=q["skipped"],
                                  hits=[public(h) for h in q["hits"]], cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: the handbook section on the topic, every decision on it and the exception register "
         "(`fact list`, every page), joined by ticket id; the request judged against the handbook limit")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        mr = memory_read(cli, pid, t, echo=i == 0)
        unit = story["topics"][t["topic"]]["unit"]
        lines = [f"\n  {t['id']} {t['who']} · {story['topics'][t['topic']]['label']} · asks {amount(t['value'], unit)}"]
        lines += prec_lines(story, mr["prec"], q["precedent"] or "")
        if q["precedent"]:
            hit = next((r for r in mr["prec"]["rows"] if r["record"] == q["precedent"]), None)
            if hit and hit["exception"]:
                exc = next(e for e in mr["prec"]["exceptions"] if e["record"] == hit["exception"])
                lines.append(f"  · {q['precedent']}'s qualifier is in another record — {exc['record']}: "
                             f"“{clip(exc['body'], 170)}”")
        lines.append(f"  → quick read:  {q['said']}")
        lines.append(f"  → memory read: {mr['said']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, precedent=q["precedent"], quick=q["advice"], memory=mr,
                         agree=agrees(q["advice"], mr["advice"])))
    lines = [f"\n  {'req':<4} {'who':<12} {'asks':<12} {'quick read':<24} {'handbook + register':<20}", "  " + "-" * 82]
    for r in rows:
        t = r["ticket"]
        qr = f"{r['quick']}" + (f" ({r['precedent']})" if r["precedent"] else "")
        lines.append(f"  {t['id']:<4} {t['who']:<12} {amount(t['value'], story['topics'][t['topic']]['unit']):<12} "
                     f"{qr:<24} {r['memory']['advice']:<20} {'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)} requests: the quick read approves on a one-time exception, read as "
                 "a precedent" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], precedent=r["precedent"], quick=r["quick"], agree=r["agree"],
                                  memory=dict(advice=r["memory"]["advice"], said=r["memory"]["said"]),
                                  prec=dict(topic=r["memory"]["prec"]["topic"],
                                            policy=public(r["memory"]["prec"]["policy"]) if r["memory"]["prec"]["policy"] else None,
                                            rows=r["memory"]["prec"]["rows"],
                                            exceptions=[public(e) for e in r["memory"]["prec"]["exceptions"]]))
                             for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def qualified_ticket(t: dict, exc: dict, pol: dict, story: dict) -> str:
    unit = story["topics"][t["topic"]]["unit"]
    return (f"{t['body']}. Added {story['today']} from the exception register: one-time exception {exc['record']} to "
            f"{pol['record']}, signed by the {exc['approved_by']} on {exc['on']}; not a precedent, other requests follow "
            f"the handbook limit of {amount(pol['limit'], unit)}.")


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next read will see it: each exception's qualifier goes onto the ticket it covers "
         "(`fact update --text`; the old text goes to metadata.previous, the register id and scope too)")
    updates = []
    for e in sorted((r for r in mem.values() if r["kind"] == "exception"), key=lambda r: r["record"]):
        t = mem.get(e["ticket"])
        pol = mem.get(e["policy"])
        if not t or not pol or says_one_time(t["body"]):
            continue
        text = qualified_ticket(t, e, pol, story)
        meta = {k: t[k] for k in ("key", "record", "tool", "kind", "topic", "by", "on", "subject", "value", "outcome")}
        meta.update(scope=e["scope"], exception=e["record"], previous=t["body"], rewritten_on=today,
                    rewritten_by=story["partner"]["name"])
        updates.append((t, text, meta))
    for i, (t, text, meta) in enumerate(updates):
        cli.run("fact", "update", t["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more ticket(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for t, _, _ in updates:
        lines.append(f"\n    {t['record']} was “{clip(t['body'], 150)}”")
        lines.append(f"    {'':<{len(t['record'])}} now “{mem2[t['record']]['body']}”")
    emit("text", "\n".join(lines))
    trace = []
    if updates:
        t = updates[0][0]
        res = cli.run("fact", "trace", t["fid"], "--project", pid, scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {t['record']} (newest first):"]
        for e in trace:
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(e['new'], 90)}")
        emit("text", "\n".join(tl))
    note("later the same day, three more requests of the same kinds — the quick read again, unchanged code. Search gets "
         "a rewritten fact a few seconds after `fact list` does; each read is retried until no hit is stale")
    time.sleep(6)
    after = []
    for t in story["followups"]:
        t1, tries = time.time(), 0
        while True:
            tries += 1
            q = quick_read(cli, pid, mem2, t, echo=tries == 1)
            if not q["cached"] or tries >= 8:
                break
            time.sleep(4)
        mr = memory_read(cli, pid, t, echo=False)
        after.append(dict(ticket=t, precedent=q["precedent"], skipped=q["skipped"], quick=q["advice"], said=q["said"],
                          memory=mr["advice"], memory_said=mr["said"], agree=agrees(q["advice"], mr["advice"]),
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']} “{a['ticket']['text']}”")
        lines.append(f"    quick read: {a['said']}{' (try ' + str(a['tries']) + ')' if a['tries'] > 1 else ''}")
        lines.append(f"    memory read: {a['memory_said']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the handbook on {agree} of {len(after)}: the ticket it would have "
                 "cited now says, on itself, that it was one-time")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=t["record"], was=t["body"], now=mem2[t["record"]]["body"],
                             exception=m["exception"]) for t, _, m in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, precedents

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def ask_text(cli: CLI, pid: str, text: str, topic: str, value, echo: bool = True) -> dict:
    """Any request: the quick read and the memory read side by side."""
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", who="", topic=topic, value=value, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, t, echo=echo)
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], precedent=q["precedent"],
                precedent_body=q["precedent_body"], quick=q["advice"], quick_said=q["said"], skipped=q["skipped"],
                memory=dict(advice=mr["advice"], said=mr["said"]), prec=mr["prec"], agree=agrees(q["advice"], mr["advice"]))


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
    banner(2, TOTAL, "Set up — one project for the HR desk")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: handbook, HR desk decisions, exception register")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — find a past approval, approve again")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — handbook limit, every decision, the register")
    rows = memory_review(cli, pid, story, quick)
    banner(6, TOTAL, "Write it back — the qualifier onto the ticket, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   precedent={q["ticket"]["id"]: [q["precedent"], q.get("precedent_rank")] for q in quick},
                   quick={r["ticket"]["id"]: r["quick"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["advice"] for r in rows},
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["precedent"], a["skipped"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "precedents", "cleanup"],
                    help="run = full demo (default); ask \"REQUEST\" --topic T --value N = the quick read and the memory "
                         "read of any request; precedents TOPIC = every decision on a topic, with the handbook limit and "
                         "the exception register joined in; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the request (ask) or the topic (precedents)")
    story = load_story()
    ap.add_argument("--topic", default="abroad", choices=list(story["topics"]), help="ask: the kind of request")
    ap.add_argument("--value", type=int, default=0, help="ask: how much is asked (weeks / days / dollars)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command in ("ask", "precedents") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. ask \"Can I work from Porto for 5 weeks?\" --topic abroad "
                 "--value 5, or precedents abroad")
    if a.command == "ask" and a.value <= 0:
        ap.error("ask needs --value N (weeks, days or dollars asked for)")
    if a.command == "precedents" and a.args[0] not in story["topics"]:
        ap.error(f"precedents takes one of: {', '.join(story['topics'])}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py ask \"Can I carry over 9 holiday days?\" --topic carryover --value 9`, "
              "`python3 demo.py precedents abroad`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — the quick read and the memory read")
        r = ask_text(cli, pid, a.args[0], a.topic, a.value)
        lines = [f"\n  “{r['ticket']['text']}” · {story['topics'][a.topic]['label']} · "
                 f"{amount(a.value, story['topics'][a.topic]['unit'])} — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← precedent" if h["record"] == r["precedent"] else "") for h in r["hits"]]
        lines.append(f"  → quick read:  {r['quick_said']}")
        lines += prec_lines(story, r["prec"], r["precedent"] or "")
        lines.append(f"  → memory read: {r['memory']['said']}  {'✓ same' if r['agree'] else '✗ differs'}")
        emit("text", "\n".join(lines))
    elif a.command == "precedents":
        topic = a.args[0]
        banner(2, 2, f"Precedents — every decision on {story['topics'][topic]['label']}")
        mem = entries(list_facts(cli, pid))
        pc = precedents(mem, topic)
        lines = [""] + prec_lines(story, pc)
        for e in pc["exceptions"]:
            lines.append(f"      {e['record']:<8} → {e['ticket']}  {clip(e['body'], 110)}")
        n_exc = sum(1 for r in pc["rows"] if r["exception"])
        lines.append(f"  · {len(pc['rows'])} decision(s); {n_exc} of them one-time exception(s) — not precedents")
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

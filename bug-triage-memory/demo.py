#!/usr/bin/env python3
"""
Bug triage memory for small SaaS teams — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tallyfold (fictional invoicing SaaS, six engineers) keeps every tool's records
in one MemoryLake project: Linear investigations and tasks, GitHub merges, the
deploy log, Sentry alerts, helpdesk tickets.  Priya Nand is on triage duty on
2026-10-14 and three new tickets came in.

The quick read is what a triage assistant does with "new bug reports retrieve
similar prior investigations first": `search` the report, take the closest
investigation, trust its status line — the closed ticket that says "fixed".
It finds the right investigation every time, and two of the three status
lines are stale: INV-31's fix was rolled back on 2026-08-19, INV-44's fix is
merged but does not ship until 3.20.0.  The records that say so live in the
deploy log under a PR number, not under the symptom.  Search's top 8 holds
some of them and misses others (the deploy that decides the third ticket
never comes back), and nothing in a top-8 list says which record is about
this fix, or in what order.  The quick read calls both "fixed → regression"
and would open two new investigations for bugs the team already knows.

The memory read follows the identifiers instead (`fact list --query "PR #482"`,
exact substring, every page): merged → deployed → rolled back → re-land task in
backlog.  The third ticket is the control: its fix was rolled back too, then
relanded and deployed — a real regression, and both reads agree.

Step 6 writes the records' answer into each investigation's status line
(`fact update --text`, the old text kept in metadata.previous), files the
regression as a new investigation, and the same quick-read code then agrees
with the records on the day's later tickets.

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

PREFIX = "mlu-btm"  # memorylake-usecases / bug-triage-memory


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
    return dict(key=f"btm:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], by=r["by"], on=r["on"])


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


def list_facts(cli: CLI, pid: str, query: str = "", echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid] + (["--query", query] if query else []), echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (INV-31, R3181, #8812, …), metadata flattened in, plus the fact id and text."""
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
    return {k: r.get(k) for k in ("record", "tool", "kind", "by", "on", "fid", "body", "rank", "previous", "writeback")}


# --------------------------------------------------------------------------- identifiers and status lines (plain code, no model)

IDENT = re.compile(r"PR #\d+|\b(?:INV|ENG|OPS)-\d+\b")


def idents(text: str) -> list[str]:
    seen = []
    for m in IDENT.finditer(text or ""):
        if m.group(0) not in seen:
            seen.append(m.group(0))
    return seen


def mentions(text: str, ident: str) -> bool:
    """Whole identifier only: `PR #48` must not match `PR #482`."""
    return re.search(re.escape(ident) + r"(?![0-9])", text or "") is not None


def status_line(text: str) -> str:
    m = re.search(r"Status[^:]*:\s*(.*)$", text or "")
    return m.group(1).strip() if m else ""


def release_of(text: str) -> str:
    m = re.search(r"[Rr]elease (\d+\.\d+\.\d+)", text or "")
    return m.group(1) if m else ""


def subject_pr(r: dict) -> str:
    """The PR a GitHub record is about (`PR #533 merged …: relands PR #455` is about #533)."""
    m = re.match(r"(PR #\d+) merged", r.get("body") or "")
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- read 1: the quick read

def quick_verdict(status: str) -> dict:
    """What a triage assistant concludes from the investigation's own status line."""
    s = status.lower()
    if s.startswith("fixed"):
        m = re.search(r"shipped in (\d+\.\d+\.\d+)", status)
        said = f"fixed in {m.group(1)}" if m else "fixed"
        return dict(cls="regression", said=said, action="treat as a regression: open a new investigation")
    if "regression open as" in s:
        m = re.search(r"(INV-\d+)", status)
        return dict(cls="investigating", said=f"regression open as {m.group(1) if m else '?'}",
                    action=f"link to {m.group(1) if m else 'it'}, no new investigation")
    if "not shipped" in s:
        m = re.search(r"ships in (\d+\.\d+\.\d+) on (\d{4}-\d\d-\d\d)", status)
        return dict(cls="pending", said=f"fix not shipped ({m.group(1)} on {m.group(2)})" if m else "fix not shipped",
                    action="known: send the workaround and the ship date")
    if s.startswith("investigating"):
        return dict(cls="investigating", said="under investigation", action="link to it, no new investigation")
    if s.startswith("open"):
        return dict(cls="open", said=clip(status, 40), action="known and open: reopen it, send the workaround")
    return dict(cls="unknown", said=clip(status, 40) or "no status line", action="read the investigation")


VERDICT = {"regression": "regression → new investigation", "open": "known, open → reopen + workaround",
           "pending": "known, fix not shipped → workaround + date", "investigating": "known regression → link to it",
           "unknown": "unclear"}


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
    return f"      #{h['rank']:<3} {h['record']:<8} {tool:<10} {clip(h['body'], 82)}{mark}"


def quick_read(cli: CLI, pid: str, story: dict, mem: dict, t: dict, echo: bool = True) -> dict:
    """The page's step 3, done the quick way: search the report, take the closest prior investigation, believe its
    status line. The text is read from `fact list` by fact id (search can answer from a cache of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    inv = next((h for h in hits if h["kind"] == "investigation"), None)
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    if inv is None:
        return dict(ticket=t, hits=hits, inv=None, status="", verdict=quick_verdict(""), cached=cached)
    cur = by_fid.get(inv["fid"], inv)
    status = status_line(cur["body"])
    return dict(ticket=t, hits=hits, inv=cur["record"], inv_rank=inv["rank"], inv_body=cur["body"], status=status,
                verdict=quick_verdict(status), cached=cached)


# --------------------------------------------------------------------------- read 2: follow the identifiers

def walk(cli: CLI, pid: str, inv: dict, echo: bool = True) -> tuple[dict[str, dict], list[tuple[str, int]]]:
    """Every record that mentions the investigation or the fix it names, and whatever those records name in turn
    (a re-land's PR, a follow-up task) — `fact list --query <id>`, exact substring, every page. A deploy line lists
    other PRs too; those are not followed."""
    found: dict[str, dict] = {}
    queue = [inv["record"]] + [i for i in idents(inv["body"]) if i != inv["record"]]
    asked: list[tuple[str, int]] = []
    while queue and len(asked) < 10:
        ident = queue.pop(0)
        if ident in [a for a, _ in asked]:
            continue
        got = [r for r in entries(list_facts(cli, pid, ident, echo=echo and not asked)).values() if mentions(r["body"], ident)]
        asked.append((ident, len(got)))
        for r in got:
            if r["record"] in found:
                continue
            found[r["record"]] = r
            if r["kind"] in ("merged", "rollback", "task"):
                for nxt in idents(r["body"]):
                    if nxt not in queue and nxt not in [a for a, _ in asked]:
                        queue.append(nxt)
    return found, asked


def pr_state(pr: str, recs: dict[str, dict], day: str, depth: int = 0) -> dict:
    """Where a fix stands on `day`, from the GitHub, deploy-log and tracker records alone."""
    rs = [r for r in recs.values() if mentions(r["body"], pr) and r["on"] <= day]
    merged = [r for r in rs if r["kind"] == "merged" and subject_pr(r) == pr]
    deploys = sorted((r for r in rs if r["kind"] == "deploy"), key=lambda r: r["on"])
    rollbacks = sorted((r for r in rs if r["kind"] == "rollback"), key=lambda r: r["on"])
    sched = [r for r in rs if r["kind"] == "scheduled"]
    relands = [r for r in recs.values() if r["kind"] == "merged" and subject_pr(r) != pr and mentions(r["body"], pr)
               and "reland" in r["body"].lower() and r["on"] <= day]
    used = [r["record"] for r in merged + deploys + rollbacks + sched + relands]
    if depth < 3:
        for r in relands:
            sub = pr_state(subject_pr(r), recs, day, depth + 1)
            if sub["state"] == "live":
                return dict(sub, state="live", via=subject_pr(r), used=used + sub["used"],
                            history=dict(rolled_back=rollbacks[-1]["on"] if rollbacks else "", reland=subject_pr(r)))
    last_d = deploys[-1] if deploys else None
    last_r = rollbacks[-1] if rollbacks else None
    if last_d and (not last_r or last_r["on"] < last_d["on"]):
        return dict(state="live", pr=pr, since=last_d["on"], release=release_of(last_d["body"]), used=used)
    if last_r:
        follow = [i for i in idents(last_r["body"]) if i != pr]
        tasks = [recs[i] for i in follow if i in recs]
        task_state = ""
        for t in tasks:
            m = re.search(r"State:\s*([^.]*)", t["body"])
            task_state = f"{t['record']} ({m.group(1).strip() if m else '?'})"
            used.append(t["record"])
        return dict(state="reverted", pr=pr, since=last_r["on"], release=release_of(last_r["body"]), follow=task_state,
                    used=used)
    if merged and sched:
        m = re.search(r"scheduled for (\d{4}-\d\d-\d\d)", sched[-1]["body"])
        return dict(state="pending", pr=pr, since=merged[-1]["on"], release=release_of(sched[-1]["body"]),
                    ships=m.group(1) if m else "?", used=used)
    if merged:
        return dict(state="merged", pr=pr, since=merged[-1]["on"], used=used)
    return dict(state="unknown", pr=pr, used=used)


def memory_read(cli: CLI, pid: str, mem: dict, inv_id: str, day: str, echo: bool = True) -> dict:
    inv = mem[inv_id]
    recs, asked = walk(cli, pid, inv, echo=echo)
    # An open regression of this investigation already filed? Then this report belongs to it.
    reg = [r for r in recs.values() if r["kind"] == "investigation" and r["record"] != inv_id and r["on"] <= day
           and re.search(r"[Rr]egression of " + re.escape(inv_id) + r"(?![0-9])", r["body"])]
    earlier = sorted((r for r in recs.values() if r["kind"] in ("ticket", "triage") and r["on"] <= day), key=lambda r: r["on"])
    fx = re.search(r"Fix:([^.]*)", inv["body"])
    fixes = [i for i in idents(fx.group(1)) if i.startswith(("PR #", "OPS-"))] if fx else []
    st = dict(state="unknown", used=[])
    if fixes and fixes[0].startswith("OPS-"):
        t = recs.get(fixes[0])
        if t and "done" in t["body"].lower():
            st = dict(state="live", pr=fixes[0], since=t["on"], release="ops change", used=[t["record"]])
    elif fixes:
        st = pr_state(fixes[0], recs, day)
    if reg:
        cls, said = "investigating", f"regression already open as {reg[-1]['record']}"
    elif not fixes:
        cls, said = "investigating", "no fix named yet: under investigation"
    elif st["state"] == "live":
        if st.get("via"):
            said = (f"{st['via']} (re-land of {fixes[0]}, rolled back {st['history']['rolled_back']}) live since "
                    f"{st['release']} on {st['since']}")
        else:
            said = f"{fixes[0]} live since {st['release']} on {st['since']}"
        cls = "regression"
    elif st["state"] == "reverted":
        cls = "open"
        said = f"{fixes[0]} reverted {st['since']} ({st['release']})" + (f"; {st['follow']}" if st.get("follow") else "")
    elif st["state"] == "pending":
        cls, said = "pending", f"{fixes[0]} merged {st['since']}, not shipped: {st['release']} on {st['ships']}"
    elif st["state"] == "merged":
        cls, said = "pending", f"{fixes[0]} merged {st['since']}, no release yet"
    else:
        cls, said = "unknown", "no fix records found"
    used = list(dict.fromkeys(st.get("used") or []))
    if reg:
        used.append(reg[-1]["record"])
    return dict(inv=inv_id, cls=cls, said=said, action=VERDICT[cls], state=st, used=used, asked=asked,
                earlier=[public(r) for r in earlier], records=sorted(recs), reg=reg[-1]["record"] if reg else "",
                workaround=(re.search(r"Workaround:\s*(.*?)\.\s+Status", inv["body"]) or [None, ""])[1])


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

def pin(cli: CLI, pid: str, items: list[tuple[str, dict]], echo: bool = True, live: dict | None = None) -> dict[str, dict]:
    """Pin the entries the project does not hold yet (matched by metadata key) — one `fact add` for all of them,
    then `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new}."""
    if live is None:
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


def restore_triage_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of triage day: what an earlier run wrote back is undone (its new facts
    deleted, rewritten status lines put back word for word from metadata.previous)."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("writeback"):
            cli.run("fact", "delete", "--project", pid, f["id"], scoped=True, echo=n == 0)
            n += 1
        elif m.get("previous"):
            meta = {k: v for k, v in m.items() if k not in ("previous", "status_on", "rewritten_by")}
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run wrote back {n} change(s); undone, so this run starts from the same morning")
    return n


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_triage_day(cli, pid)
    note("five tools, one project: Linear investigations and tasks, GitHub merges, the deploy log, Sentry alerts and "
         "helpdesk tickets. One fact per record, word for word, then `fact update --metadata` (tool, kind, who, when)")
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
        kinds = {}
        for r in rs:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        lines.append(f"    {label:<11} {len(rs):>2}  " + " · ".join(f"{v} {k}" for k, v in kinds.items()))
    lines.append("\n  the investigations, as Linear has them (each one's own status line):")
    for r in sorted((r for r in mem.values() if r["kind"] == "investigation"), key=lambda r: r["record"]):
        title = re.sub(r"^INV-\d+\s+", "", r["body"]).split(". ")[0]
        lines.append(f"    {r['record']:<7} {clip(title, 58):<58}  Status: {clip(status_line(r['body']), 44)}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_triage(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} new tickets this morning. The quick read is what a triage assistant does with "
         "\"retrieve similar prior investigations first\": `search` the report, take the closest investigation, "
         "trust its status line")
    out = []
    for i, t in enumerate(story["incoming"]):
        q = quick_read(cli, pid, story, mem, t)
        lines = [f"\n  {t['id']} {t['customer']} ({t['on']}): “{t['text']}”", f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← closest investigation" if h["record"] == q["inv"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        lines.append(f"  → {q['inv']}: “Status: {q['status']}”")
        lines.append(f"  → quick read: {q['verdict']['said']} → {q['verdict']['action']}")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], inv=q["inv"], status=q["status"], verdict=q["verdict"],
                                  hits=[public(h) for h in q["hits"]], cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_triage(cli: CLI, pid: str, story: dict, mem: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: same matched investigation, then follow every identifier it names — the fix's PR, a "
         "re-land's PR, a follow-up task, earlier tickets — with `fact list --query <id>` (exact substring, every page). "
         "Where the fix stands on the ticket's date comes from the GitHub, deploy-log and tracker records alone")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        if not q["inv"]:
            continue
        mr = memory_read(cli, pid, mem, q["inv"], t["on"], echo=i == 0)
        ranks = {h["record"]: h["rank"] for h in q["hits"]}
        decide = [(r, ranks.get(r)) for r in mr["used"]]
        tl = sorted((mem[r] for r in mr["used"] if r in mem), key=lambda r: r["on"])
        lines = [f"\n  {t['id']} → {q['inv']}   asked: " + ", ".join(f"{a} ({n})" for a, n in mr["asked"])]
        for r in tl:
            lines.append(f"      {r['on']}  {story['tools'][r['tool']]:<10} {r['record']:<7} {clip(r['body'], 84)}")
        if mr["earlier"]:
            lines.append(f"      earlier reports: " + ", ".join(f"{e['record']} {e['by']} ({e['on']})" for e in mr["earlier"]))
        lines.append(f"  → memory read: {mr['said']} → {mr['action']}")
        miss = [r for r, k in decide if k is None]
        lines.append(f"  → the records that decide it, in step 4's top {len(q['hits'])}: "
                     + ", ".join(f"{r} {'#' + str(k) if k else '—'}" for r, k in decide)
                     + (f"  ({len(miss)} of {len(decide)} never came back)" if miss else ""))
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, inv=q["inv"], quick=q["verdict"], memory=mr, decide=decide,
                         agree=q["verdict"]["cls"] == mr["cls"]))
    lines = [f"\n  {'ticket':<7} {'matched':<8} {'quick read (status line)':<38} {'memory read (records)':<54}",
             "  " + "-" * 112]
    for r in rows:
        mark = "✓ same" if r["agree"] else "✗ differs"
        lines.append(f"  {r['ticket']['id']:<7} {r['inv']:<8} {clip(r['quick']['said'] + ' → ' + r['quick']['cls'], 38):<38} "
                     f"{clip(r['memory']['said'] + ' → ' + r['memory']['cls'], 54):<54} {mark}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)} quick reads would open a new investigation for a bug the team "
                 "already knows" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    for r in wrong:
        lines.append(f"      {r['ticket']['id']} {r['ticket']['customer']}: {r['memory']['action']}"
                     + (f" (workaround: {r['memory']['workaround']})" if r["memory"]["workaround"] else ""))
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], inv=r["inv"], quick=r["quick"], agree=r["agree"],
                                  memory={k: r["memory"][k] for k in ("cls", "said", "action", "used", "asked", "earlier",
                                                                      "workaround", "reg")},
                                  timeline=[public(mem[x]) for x in r["memory"]["used"] if x in mem],
                                  decide=r["decide"]) for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def new_status(r: dict, story: dict, reg_id: str) -> str:
    m, st = r["memory"], r["memory"]["state"]
    today = story["today"]
    if m["cls"] == "open":
        follow = f"; re-land: {st['follow']}" if st.get("follow") else ""
        return f"Status ({today}): open - fix {st['pr']} was reverted on {st['since']} ({st['release']}){follow}."
    if m["cls"] == "pending":
        return f"Status ({today}): fix {st['pr']} merged {st['since']}, not shipped - ships in {st['release']} on {st['ships']}."
    if m["cls"] == "regression":
        hist = st.get("history") or {}
        rel = (f" - fix reverted {hist['rolled_back']}, relanded as {st['via']} in {st['release']} on {st['since']}"
               if st.get("via") else f" - fix live since {st['release']} on {st['since']}")
        return f"Status ({today}): regression open as {reg_id}{rel}."
    return ""


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next triage will read it: each investigation's status line is rewritten from its "
         "records (`fact update --text`, the old text kept in metadata.previous); a real regression gets a new "
         "investigation; each ticket gets a triage note linked to its investigation")
    nums = [int(r[4:]) for r in mem if r.startswith("INV-")]
    reg_id = f"INV-{max(nums) + 8 if nums else 1}"
    adds, updates = [], []
    for r in rows:
        inv, m = mem[r["inv"]], r["memory"]
        if m["cls"] in ("open", "pending", "regression"):
            st = new_status(r, story, reg_id)
            body = re.sub(r"Status[^:]*:.*$", st, inv["body"])
            if body != inv["body"]:
                meta = {k: inv[k] for k in ("key", "record", "tool", "kind", "by", "on")}
                meta.update(previous=inv["body"], status_on=today, rewritten_by=story["triager"]["name"])
                updates.append((inv, body, meta))
        if m["cls"] == "regression":
            st = m["state"]
            text = (f"{reg_id} Regression of {r['inv']}: {r['ticket']['text'].rstrip('.')} ({r['ticket']['id']}, "
                    f"{r['ticket']['on']}), although fix {st.get('via') or st.get('pr')} is live since {st['release']} on "
                    f"{st['since']}. Status: investigating.")
            adds.append((text, dict(key=f"btm:{reg_id}", record=reg_id, tool="linear", kind="investigation",
                                    by=story["triager"]["name"], on=today, writeback=True)))
        link = reg_id if m["cls"] == "regression" else r["inv"]
        text = (f"Ticket {r['ticket']['id']} {r['ticket']['customer']} ({r['ticket']['on']}): {VERDICT[m['cls']]}. "
                f"Linked to {link}; {m['said']}.")
        adds.append((text, dict(key=f"btm:triage:{r['ticket']['id']}", record=f"T{r['ticket']['id']}", tool="helpdesk",
                                kind="triage", by=story["triager"]["name"], on=today, writeback=True)))
    for i, (inv, body, meta) in enumerate(updates):
        cli.run("fact", "update", inv["fid"], "--project", pid, "--text", body, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more status line(s) the same way")
    pin(cli, pid, adds, live={})
    mem2 = entries(list_facts(cli, pid))
    lines = ["\n  status lines now:"]
    for inv, _, _ in updates:
        lines.append(f"    {inv['record']:<7} was “{clip(status_line(inv['body']), 50)}”")
        lines.append(f"    {'':<7} now “{status_line(mem2[inv['record']]['body'])}”")
    lines.append(f"  new: " + ", ".join(m["record"] for _, m in adds))
    emit("text", "\n".join(lines))
    # The same quick read on today's later tickets, new wording (search answers a repeated question from its cache).
    note("later the same day, three more tickets about the same bugs — the quick read again, unchanged code. Search "
         "gets new facts a few seconds after `fact list` does; each read is retried until its closest investigation "
         "is the one the memory read names")
    time.sleep(6)
    after = []
    for i, t in enumerate(story["followups"]):
        expect = rows[i]["inv"] if i < len(rows) else None
        want = reg_id if rows[i]["memory"]["cls"] == "regression" else expect
        t1, tries = time.time(), 0
        while True:
            tries += 1
            q = quick_read(cli, pid, story, mem2, t, echo=tries == 1)
            if q["inv"] in (expect, want) or tries >= 8:
                break
            time.sleep(4)
        mr = memory_read(cli, pid, mem2, q["inv"], t["on"], echo=False) if q["inv"] else dict(cls="unknown", said="")
        after.append(dict(ticket=t, inv=q["inv"], quick=q["verdict"], memory=dict(cls=mr["cls"], said=mr["said"]),
                          agree=q["verdict"]["cls"] == mr["cls"], tries=tries, secs=int(time.time() - t1),
                          inv_rank=q.get("inv_rank"), cached=q["cached"]))
    lines = []
    for a in after:
        mark = "✓ same" if a["agree"] else "✗ differs"
        lines.append(f"\n  {a['ticket']['id']} “{a['ticket']['text']}”")
        lines.append(f"    closest investigation {a['inv']} (#{a['inv_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick']['said']} → {a['quick']['cls']} · memory read: {a['memory']['cls']}  {mark}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the records on {agree} of {len(after)}: the status line it trusts "
                 "is the one the records wrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=inv["record"], was=status_line(inv["body"]), now=status_line(mem2[inv["record"]]["body"]))
                        for inv, _, _ in updates],
               added=[m["record"] for _, m in adds], reg=reg_id,
               after=[dict(a, ticket=a["ticket"]) for a in after], agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: triage, trace

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def triage_text(cli: CLI, pid: str, text: str, day: str = "", echo: bool = True) -> dict:
    """Any report text: the quick read and the memory read side by side."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", customer="", on=day or story["today"], text=text)
    q = quick_read(cli, pid, story, mem, t, echo=echo)
    mr = memory_read(cli, pid, mem, q["inv"], t["on"], echo=echo) if q["inv"] else None
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], inv=q["inv"], status=q["status"], quick=q["verdict"],
                memory=({k: mr[k] for k in ("cls", "said", "action", "used", "asked", "workaround")} if mr else None),
                timeline=[public(mem[x]) for x in (mr["used"] if mr else []) if x in mem],
                agree=bool(mr) and q["verdict"]["cls"] == mr["cls"])


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
    banner(2, TOTAL, "Set up — one project for every tool's records")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records from Linear, GitHub, the deploy log, Sentry and the helpdesk")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — closest prior investigation, and its status line")
    quick = quick_triage(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — follow the fix through the records")
    rows = memory_triage(cli, pid, story, mem, quick)
    banner(6, TOTAL, "Write it back — status lines from the records, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   matched={r["ticket"]["id"]: r["inv"] for r in rows},
                   quick={r["ticket"]["id"]: r["quick"]["cls"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["cls"] for r in rows},
                   decide_missing={r["ticket"]["id"]: [x for x, k in r["decide"] if k is None] for r in rows},
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["inv"], a["inv_rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "triage", "trace", "cleanup"],
                    help="run = full demo (default); triage \"REPORT TEXT\" [--on YYYY-MM-DD] = the quick read and the "
                         "memory read of any bug report; trace \"PR #482\" = every record that names an identifier; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the report text (triage) or the identifier (trace)")
    ap.add_argument("--on", default="", help="triage: the report's date (default 2026-10-14)")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("triage", "trace") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. triage \"Invoices show up twice in QuickLedger\" or trace \"PR #482\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py triage \"Customers get two copies of every invoice in QuickLedger\"`, "
              "`python3 demo.py trace \"PR #482\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "triage":
        banner(2, 2, "Triage — the quick read and the memory read")
        r = triage_text(cli, pid, a.args[0], a.on)
        lines = [f"\n  “{r['ticket']['text']}” ({r['ticket']['on']}) — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← closest investigation" if h["record"] == r["inv"] else "") for h in r["hits"]]
        lines.append(f"  → {r['inv']}: “Status: {r['status']}”")
        lines.append(f"  → quick read:  {r['quick']['said']} → {r['quick']['action']}")
        if r["memory"]:
            for x in r["timeline"]:
                lines.append(f"      {x['on']}  {story['tools'][x['tool']]:<10} {x['record']:<7} {clip(x['body'], 80)}")
            lines.append(f"  → memory read: {r['memory']['said']} → {r['memory']['action']}  "
                         f"{'✓ same' if r['agree'] else '✗ differs'}")
        emit("text", "\n".join(lines))
    elif a.command == "trace":
        banner(2, 2, f"Trace — every record that names {a.args[0]}")
        got = [r for r in entries(list_facts(cli, pid, a.args[0])).values() if mentions(r["body"], a.args[0])]
        lines = [f"\n  {len(got)} record(s):"]
        for r in sorted(got, key=lambda r: r["on"]):
            lines.append(f"    {r['on']}  {story['tools'].get(r['tool'], r['tool']):<10} {r['record']:<8} {clip(r['body'], 90)}")
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

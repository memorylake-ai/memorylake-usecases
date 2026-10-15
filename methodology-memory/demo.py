#!/usr/bin/env python3
"""
Methodology memory for consultants — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Halvorsen & Pike (fictional) is a small operations consultancy. Its practice
memory lives in one MemoryLake project: the methodology deck (one entry per
technique, with the evidence behind it), the case library, the library's
policy, and the engagement register the PMO keeps for every engagement.

The quick read is what the page promises: every AI tool reads the methodology.
`search` the scoping question, take the deck entry on that technique, follow
it. The deck says to always open with the Diagnostic Sprint (6 of 6 case
studies implemented), to stand up a steering committee (8 of 8) and to accept a
fixed fee when the client asks (2 of 2).

The counts are right. The denominator is not: a case study is only written
once the client agrees to be a reference, so a stalled engagement never gets
one, and every technique in the library scores 100%. The memory read goes back
to the register (`fact list`, every page): it first measures what the library
covers (implemented 9 of 12, stalled 0 of 8), then compares engagements with
and without each technique. The Diagnostic Sprint: 7 of 12 with it, 5 of 8
without -- no edge. The fixed fee: 2 of 6 with it, 10 of 14 without. The
steering committee (8 of 10 vs 4 of 10) is the control: both reads agree.

Step 6 rewrites the two deck entries from the register (`fact update --text`,
the old text kept in metadata.previous, the change visible in `fact trace`), and
the same quick-read code then agrees with the register on later questions.

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

PREFIX = "mlu-mth"  # memorylake-usecases / methodology-memory

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
    # No null values: the server drops keys whose value is null. Empty lists come back as [].
    m = dict(key=f"mth:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], by=r["by"], on=r["on"])
    if r["kind"] == "engagement":
        m.update(client=r["client"], sector=r["sector"], used=r["used"], outcome=r["outcome"], checked=r["checked"],
                 case=r["case"])
    elif r["kind"] == "case":
        m.update(engagement=r["engagement"], client=r["client"], used=r["used"])
    elif r["kind"] == "method":
        m.update(technique=r["technique"], evidence=r["evidence"], prefer=r["prefer"])
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
    """The pinned records by record id (E-04, CS-02, M-1 …), metadata flattened in, plus fact id and text."""
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
    return {k: r.get(k) for k in ("record", "tool", "kind", "by", "on", "client", "sector", "used", "outcome", "checked",
                                  "case", "engagement", "technique", "evidence", "prefer", "fid", "body", "rank",
                                  "previous")}


# --------------------------------------------------------------------------- the register (plain code, no model)

def p100(k: int, n: int) -> int:
    # Half up, like the web page's Math.round (Python's round(62.5) is 62).
    return int(100 * k / n + 0.5)


def pc(k: int, n: int) -> str:
    return f"{k}/{n} ({p100(k, n)}%)" if n else "—"


def pct(k: int, n: int) -> str:
    return f"{k:>2}/{n:<2} {p100(k, n):>3}%" if n else "    —"


def kind(mem: dict, k: str) -> list[dict]:
    return sorted((r for r in mem.values() if r.get("kind") == k), key=lambda r: (r["on"], r["record"]))


def coverage(mem: dict) -> dict:
    """What the case library holds, measured against the register: per outcome, how many engagements have a case
    study. A case counts only if its record exists and names the engagement back."""
    cases = {r["record"]: r for r in kind(mem, "case")}
    rows = []
    for outcome in ("implemented", "stalled"):
        es = [e for e in kind(mem, "engagement") if e["outcome"] == outcome]
        written = [e for e in es if e.get("case") in cases and cases[e["case"]].get("engagement") == e["record"]]
        rows.append(dict(outcome=outcome, n=len(es), written=len(written),
                         without=[e["record"] for e in es if e not in written]))
    return dict(rows=rows, cases=len(cases))


def technique_table(mem: dict, t: str) -> dict:
    """Engagements with and without the technique, from the register; and what the case library alone says."""
    es = kind(mem, "engagement")
    w = [e for e in es if t in (e.get("used") or [])]
    wo = [e for e in es if t not in (e.get("used") or [])]
    ok = lambda xs: sum(1 for e in xs if e["outcome"] == "implemented")
    by_id = {e["record"]: e for e in es}
    lib = [c for c in kind(mem, "case") if t in (c.get("used") or [])]
    lib_ok = sum(1 for c in lib if (by_id.get(c.get("engagement")) or {}).get("outcome") == "implemented")
    return dict(technique=t, n=len(es),
                with_=dict(k=ok(w), n=len(w), ids=[e["record"] for e in w]),
                without=dict(k=ok(wo), n=len(wo), ids=[e["record"] for e in wo]),
                library=dict(k=lib_ok, n=len(lib), ids=[c["record"] for c in lib]))


def verdict(tb: dict, story: dict) -> dict:
    """`use it` if engagements with the technique were implemented at least `gap` points more often than those
    without, `avoid` if that much less often, `optional` in between, `can't tell` if either side is too small."""
    w, wo, gap = tb["with_"], tb["without"], story["gap"]
    name = story["techniques"][tb["technique"]]
    if w["n"] < story["min_n"] or wo["n"] < story["min_n"]:
        return dict(advice="can't tell", lift=None,
                    why=f"{w['n']} engagement(s) with the {name}, {wo['n']} without — fewer than {story['min_n']} on a side")
    d = p100(w["k"], w["n"]) - p100(wo["k"], wo["n"])  # the difference of the two percentages printed beside it
    moved = f"{abs(d)} point{'s' if abs(d) != 1 else ''} {'better' if d > 0 else 'worse'} with it" if d else "no difference"
    advice = "use it" if d >= gap else "avoid" if d <= -gap else "optional"
    inside = f" — inside ±{gap}" if advice == "optional" else ""
    return dict(advice=advice, lift=d, why=f"with it {pc(w['k'], w['n'])} vs without {pc(wo['k'], wo['n'])}: {moved}{inside}")


def coverage_lines(cov: dict) -> list[str]:
    lines = ["      90-day outcome   engagements   with a case study"]
    for r in cov["rows"]:
        miss = f"   (none for {', '.join(r['without'])})" if r["written"] and r["without"] else ""
        lines.append(f"      {r['outcome']:<16} {r['n']:>6}        {r['written']:>3} of {r['n']:<3}{miss}")
    return lines


def table_lines(tb: dict, story: dict) -> list[str]:
    name = story["techniques"][tb["technique"]]
    lib, w, wo = tb["library"], tb["with_"], tb["without"]
    return [f"      case library, with the {name:<20} {pct(lib['k'], lib['n'])}   {clip(', '.join(lib['ids']), 44)}"
            f"   ← what the deck counted",
            f"      register, with the {name:<24} {pct(w['k'], w['n'])}   {clip(', '.join(w['ids']), 44)}",
            f"      register, without it {'':<22} {pct(wo['k'], wo['n'])}   {clip(', '.join(wo['ids']), 44)}"]


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
    return f"      #{h['rank']:<3} {h['record']:<7} {tool:<19} {clip(h['body'], 74)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, t: dict, echo: bool = True) -> dict:
    """What every AI tool does with methodology memory: search the scoping question, take the deck entry on the
    technique in question, follow it. The entry is read from `fact list` by fact id (search can answer from a cache
    of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    hit = next((h for h in hits if h["kind"] == "method" and by_fid.get(h["fid"], h).get("technique") == t["technique"]),
               None)
    if hit is None:
        return dict(ticket=t, hits=hits, method=None, advice="", said="no deck entry on this technique in the top 8",
                    cached=cached)
    cur = by_fid.get(hit["fid"], hit)
    return dict(ticket=t, hits=hits, method=cur["record"], method_rank=hit["rank"], method_body=cur["body"],
                evidence=cur.get("evidence"), advice=cur.get("prefer") or "", cached=cached,
                said=f"the deck says {cur.get('prefer')}")


# --------------------------------------------------------------------------- read 2: the register, not the library

def memory_read(cli: CLI, pid: str, technique: str, echo: bool = True) -> dict:
    """Every engagement in the register (`fact list`, every page): what the library covers, and the technique
    compared across engagements that used it and engagements that did not."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    tb = technique_table(mem, technique)
    v = verdict(tb, story)
    return dict(technique=technique, coverage=coverage(mem), table=tb, verdict=v, advice=v["advice"],
                said=f"{v['advice']} — {v['why']}")


def agrees(quick: str, memory: str) -> bool:
    return bool(quick) and quick == memory


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


# Every key the write-back adds or changes; restore takes the first group out and puts `prefer` back.
REWRITE_KEYS = ("previous", "previous_prefer", "rewritten_on", "rewritten_by", "basis")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of Tomás's scoping session: deck entries an earlier run rewrote are put back
    word for word from metadata.previous, with the advice they had."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous"):
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            meta["prefer"] = m.get("previous_prefer") or meta.get("prefer")
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} deck entr{'y' if n == 1 else 'ies'}; put back, so this run starts from the same morning")
    return n


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("three sources, one project: the methodology deck, the case library (and its policy), and the PMO's engagement "
         "register. One fact per record, word for word, then `fact update --metadata` (techniques used, 90-day outcome, "
         "case study; for a deck entry: the technique, its evidence and its advice)")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    pol = kind(mem, "policy")
    lines = [f"    {story['tools']['deck']:<20} {len(kind(mem, 'method')):>2}   "
             + " · ".join(f"{r['record']} {story['techniques'][r['technique']]}" for r in kind(mem, "method")),
             f"    {story['tools']['library']:<20} {len(kind(mem, 'case')):>2}   "
             + " ".join(r["record"] for r in kind(mem, "case")) + f"   + policy {' '.join(r['record'] for r in pol)}",
             f"    {story['tools']['register']:<20} {len(kind(mem, 'engagement')):>2}   "
             + f"{kind(mem, 'engagement')[0]['record']} … {kind(mem, 'engagement')[-1]['record']}",
             f"\n  {story['author']['name']}'s methodology deck, as it stands:"]
    for r in sorted(kind(mem, "method"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<4} {r['body']}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} questions on today's {story['client']} scoping sheet. The quick read: `search` the "
         "question, take the deck entry on that technique, follow it")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']} ({story['techniques'][t['technique']]}): “{t['text']}”", f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← the deck entry" if h["record"] == q["method"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["method"]:
            lines.append(f"  → {q['method']}: “{clip(q['method_body'], 170)}”")
        lines.append(f"  → quick read on the {story['techniques'][t['technique']]}: {q['advice'] or '?'}")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], method=q["method"], method_body=q.get("method_body", ""),
                                  advice=q["advice"], said=q["said"], hits=[public(h) for h in q["hits"]],
                                  cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, mem: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every engagement in the register (`fact list`, every page). First, what the case library "
         "covers; then each technique, engagements that used it against engagements that did not")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        mr = memory_read(cli, pid, t["technique"], echo=i == 0)
        if i == 0:
            cov = mr["coverage"]
            pol = kind(mem, "policy")
            lines = ["\n  the case library against the register:"] + coverage_lines(cov)
            if pol:
                lines.append(f"  · {pol[0]['record']}: “{clip(pol[0]['body'], 150)}”")
            lines.append("  · a stalled engagement never gets a case study, so every technique in the library scores 100%")
            emit("text", "\n".join(lines))
            emit("coverage", "", **cov)
        tb, v = mr["table"], mr["verdict"]
        name = story["techniques"][t["technique"]]
        ev = q.get("evidence") or [0, 0]
        lines = [f"\n  {t['id']} · {q['method'] or '—'} counts case studies: {name} {pc(*ev)}"]
        lines += table_lines(tb, story)
        lines.append(f"  · {v['advice']} — {v['why']}")
        lines.append(f"  → quick read:  {q['advice'] or '?'}")
        lines.append(f"  → memory read: {mr['advice']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, method=q["method"], quick=q["advice"], memory=mr, agree=agrees(q["advice"], mr["advice"])))
    lines = [f"\n  {'q':<4} {'technique':<20} {'the deck says':<15} {'the register says':<18}", "  " + "-" * 70]
    for r in rows:
        lines.append(f"  {r['ticket']['id']:<4} {story['techniques'][r['ticket']['technique']]:<20} {r['quick'] or '?':<15} "
                     f"{r['memory']['advice']:<18} {'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)}: the deck counted only engagements that became case studies, and "
                 "only implemented ones do" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], method=r["method"], quick=r["quick"], agree=r["agree"],
                                  memory={k: r["memory"][k] for k in ("technique", "advice", "said", "verdict")},
                                  table=r["memory"]["table"]) for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def rewritten_entry(m: dict, mr: dict, story: dict) -> str:
    """The entry's conclusion and the register counts it rests on."""
    tb, v = mr["table"], mr["verdict"]
    name = story["techniques"][tb["technique"]]
    w, wo = tb["with_"], tb["without"]
    head = (f"Methodology deck, {name} (rewritten by {story['lead']['name']} from the engagement register, "
            f"{story['today']}): ")
    say = {"optional": f"optional, not a rule. {story['hint'][tb['technique']]}",
           "avoid": f"avoid by default. {story['hint'][tb['technique']]}",
           "use it": "use it.", "can't tell": "not enough engagements to say."}[v["advice"]]
    return (head + say + f" In the register, which holds every engagement including stalled ones, clients implemented "
            f"{w['k']} of {w['n']} times with it and {wo['k']} of {wo['n']} without. The case library only holds "
            f"engagements whose client became a reference, so it scores every technique at 100%.")


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next AI tool will read it: each deck entry the register disagrees with is rewritten "
         "from the register (`fact update --text`; the old text, the old advice and the counts go to metadata)")
    updates = []
    for r in rows:
        if r["agree"] or not r["method"]:
            continue
        m = mem[r["method"]]
        text = rewritten_entry(m, r["memory"], story)
        if text == m["body"]:
            continue
        tb = r["memory"]["table"]
        meta = {k: m[k] for k in ("key", "record", "tool", "kind", "by", "on", "technique", "evidence")}
        meta.update(prefer=r["memory"]["advice"], previous=m["body"], previous_prefer=m["prefer"], rewritten_on=today,
                    rewritten_by=story["lead"]["name"],
                    basis=dict(engagements=tb["n"], with_k=tb["with_"]["k"], with_n=tb["with_"]["n"],
                               without_k=tb["without"]["k"], without_n=tb["without"]["n"]))
        updates.append((m, text, meta))
    for i, (m, text, meta) in enumerate(updates):
        cli.run("fact", "update", m["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more deck entr{'y' if len(updates) == 2 else 'ies'} the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for m, _, _ in updates:
        lines.append(f"\n    {m['record']} was “{clip(m['body'], 170)}”")
        lines.append(f"    {'':<{len(m['record'])}} now “{mem2[m['record']]['body']}”")
    emit("text", "\n".join(lines))
    trace = []
    if updates:
        m = updates[0][0]
        res = cli.run("fact", "trace", m["fid"], "--project", pid, scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {m['record']} (newest first):"]
        for e in trace:
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(e['new'], 90)}")
        emit("text", "\n".join(tl))
    # Later the same day, new wording (search answers a repeated question from its cache).
    note("later the same day, three more scoping questions on the same techniques — the quick read again, unchanged "
         "code. Search gets a rewritten fact a few seconds after `fact list` does; each read is retried until its deck "
         "entry is the current text")
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
        mr = memory_read(cli, pid, t["technique"], echo=False)
        after.append(dict(ticket=t, method=q["method"], method_rank=q.get("method_rank"), quick=q["advice"],
                          memory=mr["advice"], agree=agrees(q["advice"], mr["advice"]),
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']} ({story['techniques'][a['ticket']['technique']]}): “{a['ticket']['text']}”")
        lines.append(f"    deck entry {a['method'] or '—'} (#{a['method_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick'] or '?'} · memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the register on {agree} of {len(after)}: the deck entries it "
                 "follows are the ones the register rewrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=m["record"], was=m["body"], now=mem2[m["record"]]["body"],
                             was_prefer=m["prefer"], now_prefer=mem2[m["record"]].get("prefer")) for m, _, _ in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: plan, register

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def plan_text(cli: CLI, pid: str, text: str, technique: str, echo: bool = True) -> dict:
    """Any scoping question about one technique: the quick read and the memory read side by side. If the deck entry
    was rewritten, also what it said before (metadata.previous), so the two reads can still be compared after a run."""
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", technique=technique, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, technique, echo=echo)
    m = mem.get(q["method"]) if q["method"] else None
    before = None
    if m and m.get("previous"):
        before = dict(text=m["previous"], advice=m.get("previous_prefer") or "")
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], method=q["method"], method_body=q.get("method_body", ""),
                evidence=q.get("evidence"), quick=q["advice"], before=before,
                memory={k: mr[k] for k in ("technique", "advice", "said", "verdict")}, coverage=mr["coverage"],
                table=mr["table"], agree=agrees(q["advice"], mr["advice"]))


def register_text(cli: CLI, pid: str, technique: str, echo: bool = True) -> dict:
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    tb = technique_table(mem, technique)
    return dict(table=tb, verdict=verdict(tb, story), coverage=coverage(mem),
                engagements=[public(e) for e in kind(mem, "engagement")])


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
    banner(2, TOTAL, "Set up — one project for the firm's practice memory")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: methodology deck, case library, engagement register")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the deck entry on each technique, and its advice")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — the register, stalled engagements included")
    rows = memory_review(cli, pid, story, mem, quick)
    banner(6, TOTAL, "Write it back — the deck from the register, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   method={q["ticket"]["id"]: [q["method"], q.get("method_rank")] for q in quick},
                   quick={r["ticket"]["id"]: r["quick"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["advice"] for r in rows},
                   coverage=[[c["outcome"], c["written"], c["n"]] for c in rows[0]["memory"]["coverage"]["rows"]] if rows else [],
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["method"], a["method_rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


TECHNIQUES = ["DS", "SC", "FF"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "plan", "register", "cleanup"],
                    help="run = full demo (default); plan \"QUESTION\" --technique DS|SC|FF = the quick read and the "
                         "memory read of any scoping question; register DS|SC|FF = every engagement, with and without "
                         "that technique; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the scoping question (plan) or the technique (register)")
    ap.add_argument("--technique", default="DS", choices=TECHNIQUES, help="plan: the technique the question is about")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("plan", "register") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. plan \"Do we insist on the Diagnostic Sprint?\" --technique DS, "
                 "or register FF")
    if a.command == "register" and a.args[0] not in TECHNIQUES:
        ap.error(f"register takes one of: {', '.join(TECHNIQUES)}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py plan \"Do we insist on the Diagnostic Sprint?\" --technique DS`, "
              "`python3 demo.py register FF`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "plan":
        banner(2, 2, "Plan — the quick read and the memory read")
        r = plan_text(cli, pid, a.args[0], a.technique)
        lines = [f"\n  “{r['ticket']['text']}” · {story['techniques'][a.technique]} — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← the deck entry" if h["record"] == r["method"] else "") for h in r["hits"]]
        if r["method"]:
            lines.append(f"  → {r['method']}: “{clip(r['method_body'], 170)}”")
        lines.append(f"  → quick read:  {r['quick'] or '?'}")
        if r["before"]:
            lines.append(f"  · {r['method']} was rewritten by a run; before that it said “{clip(r['before']['text'], 120)}” "
                         f"→ the quick read would have said: {r['before']['advice']}")
        lines += ["  the case library against the register:"] + coverage_lines(r["coverage"])
        lines += table_lines(r["table"], story)
        lines.append(f"  · {r['memory']['said']}")
        lines.append(f"  → memory read: {r['memory']['advice']}  {'✓ same' if r['agree'] else '✗ differs'}"
                     + (f" (before the rewrite: {'✓ same' if r['before']['advice'] == r['memory']['advice'] else '✗ differs'})"
                        if r["before"] else ""))
        emit("text", "\n".join(lines))
    elif a.command == "register":
        s = register_text(cli, pid, a.args[0])
        tb = s["table"]
        name = story["techniques"][tb["technique"]]
        banner(2, 2, f"Register — every engagement, with and without the {name}")
        lines = [f"\n  {len(s['engagements'])} engagement(s):",
                 f"    {'id':<5} {'kickoff':<10} {'client':<27} {'90-day':<12} {'case':<6} techniques"]
        for e in s["engagements"]:
            mark = "  ●" if a.args[0] in (e["used"] or []) else ""
            lines.append(f"    {e['record']:<5} {e['on']:<10} {clip(e['client'], 27):<27} {e['outcome']:<12} {e['case']:<6} "
                         f"{', '.join(e['used'] or []) or '—'}{mark}")
        lines.append("")
        lines += coverage_lines(s["coverage"])
        lines += table_lines(tb, story)
        lines.append(f"  · {s['verdict']['advice']} — {s['verdict']['why']}")
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

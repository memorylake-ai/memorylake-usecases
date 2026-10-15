#!/usr/bin/env python3
"""
Curriculum memory for small education businesses — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Code School (fictional) runs a 12-week bootcamp. Its Module 3 (data
structures) curriculum lives in one MemoryLake project: the curriculum change
log (what changed, when, in which cohorts, why), every cohort's Module 3
results from the LMS (which changes were live; recursion checkpoint, project
on time, withdrawals) and the retro notes Ruth Ellison left when she handed
the curriculum to Kofi Mensah.

The quick read is what the page calls "reflection memory": `search` the
planning question, take the closest retro note, follow it. Ruth's note on
recursion says the call-stack visualizer fixed it (60% -> 84% the cohort it
shipped) and that the Tuesday lab can go first if TA hours are cut.

The before/after is right. The credit is not: the lab and the visualizer
shipped together. The memory read takes every cohort (`fact list`, every page)
and groups it by which of the two it ran. Two cohorts ran one without the
other (the evening cohort had no TA for the lab; the weekend LMS did not have
the visualizer yet): the visualizer alone stayed at 60%, the lab alone reached
82%. The second note credits the project auto-grader for on-time projects, but
no cohort has ever run the auto-grader without the shorter deadline it shipped
with -- the honest answer there is "can't tell". The third note (diagnostic
quiz, shipped on its own) is the control: both reads agree.

Step 6 rewrites the two notes from the cohorts (`fact update --text`, the old
text kept in metadata.previous, the change visible in `fact trace`), and the
same quick-read code then agrees with the cohorts on the day's later questions.

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

PREFIX = "mlu-cur"  # memorylake-usecases / curriculum-memory


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
    m = dict(key=f"cur:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], by=r["by"], on=r["on"])
    if r["kind"] == "cohort":
        m.update(track=r["track"], live=r["live"], checkpoint=r["checkpoint"], ontime=r["ontime"], withdrew=r["withdrew"])
    elif r["kind"] == "change":
        m.update(change=r["change"], release=r["release"])
    else:
        m.update(credits=r["credits"], bundle=r["bundle"], metric=r["metric"], prefer=r["prefer"])
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
    """The pinned records by record id (C23, CH-LAB, N-1 …), metadata flattened in, plus fact id and text."""
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
    return {k: r.get(k) for k in ("record", "tool", "kind", "by", "on", "track", "live", "checkpoint", "ontime", "withdrew",
                                  "change", "release", "credits", "bundle", "metric", "prefer", "fid", "body", "rank",
                                  "previous")}


# --------------------------------------------------------------------------- the bundle table (plain code, no model)

def pc(k: int, n: int) -> str:
    return f"{k}/{n} ({round(100 * k / n)}%)" if n else "—"


def pct(k: int, n: int) -> str:
    return f"{k}/{n} {round(100 * k / n):>3}%" if n else "  —"


def rate(t) -> float:
    return t[0] / t[1] if t and t[1] else 0.0


def group_name(on: tuple, bundle: list[str], story: dict) -> str:
    if len(bundle) == 1:
        return f"with the {story['changes'][bundle[0]]}" if on[0] else "without it"
    if all(on):
        return "both"
    if not any(on):
        return "neither"
    return f"{story['changes'][bundle[on.index(True)]]} only"


def bundle_table(mem: dict, bundle: list[str], metric: str, story: dict) -> dict:
    """Every cohort, grouped by which of the bundle's changes it had live, with the metric pooled per group. For a
    two-change bundle the groups are neither / A only / B only / both; a group no cohort fell into is kept, empty."""
    cohorts = sorted((r for r in mem.values() if r.get("kind") == "cohort"), key=lambda r: r["on"])
    if len(bundle) == 1:
        keys = [(False,), (True,)]
    else:
        keys = [(False, False), (True, False), (False, True), (True, True)]
    groups = []
    for key in keys:
        cs = [c for c in cohorts if tuple(x in (c.get("live") or []) for x in bundle) == key]
        k = sum(c[metric][0] for c in cs)
        n = sum(c[metric][1] for c in cs)
        groups.append(dict(key=list(key), name=group_name(key, bundle, story), cohorts=[c["record"] for c in cs], k=k, n=n))
    return dict(bundle=bundle, metric=metric, label=story["metrics"][metric], groups=groups, n=len(cohorts))


def lift(tb: dict, g: dict, story: dict) -> int:
    """Points the group moved the metric from the no-change group, signed so that positive is better."""
    base = tb["groups"][0]
    d = round(100 * (rate((g["k"], g["n"])) - rate((base["k"], base["n"]))))
    return d if story["higher_is_better"][tb["metric"]] else -d


def verdicts(tb: dict, story: dict) -> dict:
    """Per change in the bundle: `keep` if a cohort that ran it without the rest of the bundle moved the metric by
    the gap or more, `can go` if such cohorts exist and it did not, `can't tell` if no cohort ever ran it alone."""
    gap = story["gap"][tb["metric"]]
    out = {}
    for i, ch in enumerate(tb["bundle"]):
        key = [j == i for j in range(len(tb["bundle"]))]
        g = next(g for g in tb["groups"] if g["key"] == key)
        if not g["cohorts"]:
            out[ch] = dict(advice="can't tell", why=f"no cohort ever ran the {story['changes'][ch]} without the rest of the bundle")
            continue
        d = lift(tb, g, story)
        who = "alone" if len(tb["bundle"]) > 1 else "live"
        alone = f"{story['changes'][ch]} {who} ({', '.join(g['cohorts'])}): {pc(g['k'], g['n'])}"
        moved = f"{abs(d)} point{'s' if abs(d) != 1 else ''} {'better' if d > 0 else 'worse'}" if d else "no change"
        base = tb["groups"][0]
        out[ch] = dict(advice="keep" if d >= gap else "can go",
                       why=f"{alone} vs {pc(base['k'], base['n'])} {'with neither' if len(tb['bundle']) > 1 else 'without it'}"
                           f" — {moved}", lift=d)
    return out


def table_lines(tb: dict, story: dict, credited: str = "") -> list[str]:
    lines = [f"      {tb['label']}"]
    for g in tb["groups"]:
        val = pct(g["k"], g["n"]) if g["cohorts"] else "no cohort"
        names = ", ".join(g["cohorts"]) or "—"
        mark = ""
        if credited and len(tb["bundle"]) > 1 and g["key"] == [b == credited for b in tb["bundle"]]:
            mark = "  ← the change the note credits, on its own"
        lines.append(f"      {g['name']:<34} {val:>13}   {clip(names, 34):<34}{mark}")
    return lines


def note_compares(tb: dict) -> str:
    """What the note's before/after compared: the no-change group against the whole bundle."""
    a, b = tb["groups"][0], tb["groups"][-1]
    return f"{a['name']} {pc(a['k'], a['n'])} vs {b['name']} {pc(b['k'], b['n'])}"


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
    return f"      #{h['rank']:<3} {h['record']:<11} {tool:<14} {clip(h['body'], 76)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, t: dict, echo: bool = True) -> dict:
    """The page's "reflection memory", done the quick way: search the planning question, take the closest retro note
    that covers the change in question, follow its advice. The note is read from `fact list` by fact id (search can
    answer from a cache of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    note_hit = next((h for h in hits if h["kind"] == "note" and t["change"] in (by_fid.get(h["fid"], h).get("bundle") or [])),
                    None)
    if note_hit is None:
        return dict(ticket=t, hits=hits, note=None, advice="", said="no retro note on this change in the top 8", cached=cached)
    cur = by_fid.get(note_hit["fid"], note_hit)
    advice = (cur.get("prefer") or {}).get(t["change"], "")
    return dict(ticket=t, hits=hits, note=cur["record"], note_rank=note_hit["rank"], note_body=cur["body"],
                bundle=cur.get("bundle") or [], metric=cur.get("metric"), credits=cur.get("credits"), advice=advice,
                cached=cached, said=f"the note says {advice}")


# --------------------------------------------------------------------------- read 2: where the bundle came apart

def memory_read(cli: CLI, pid: str, bundle: list[str], metric: str, change: str, echo: bool = True) -> dict:
    """Every cohort (`fact list`, every page), grouped by which of the bundle's changes it ran, and the call on the
    change in question made only from cohorts that ran it without the rest."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    tb = bundle_table(mem, bundle, metric, story)
    vs = verdicts(tb, story)
    return dict(change=change, table=tb, verdicts=vs, advice=vs[change]["advice"], said=f"{vs[change]['advice']} — {vs[change]['why']}")


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


REWRITE_KEYS = ("previous", "previous_prefer", "previous_credits", "rewritten_on", "rewritten_by", "basis", "cohorts")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of Kofi's first planning session: notes an earlier run rewrote are put back
    word for word from metadata.previous."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous"):
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            meta["prefer"] = m.get("previous_prefer") or meta.get("prefer")
            meta["credits"] = m.get("previous_credits") or meta.get("credits")
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} note(s); put back, so this run starts from the same morning")
    return n


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("three sources, one project: the curriculum change log, every cohort's Module 3 results from the LMS, and the "
         "retro notes Ruth Ellison left. One fact per record, word for word, then `fact update --metadata` (which changes "
         "were live, the three readings; for a note: the bundle it is about and the change it credits)")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    kinds = lambda k: sorted((r for r in mem.values() if r["kind"] == k), key=lambda r: (r["on"], r["record"]))
    lines = [f"    {story['tools']['changelog']:<15} {len(kinds('change')):>2}   "
             + " · ".join(f"{r['record']} {r['release']}" for r in kinds("change")),
             f"    {story['tools']['lms']:<15} {len(kinds('cohort')):>2}   " + " ".join(r["record"] for r in kinds("cohort")),
             f"    {story['tools']['retro']:<15} {len(kinds('note')):>2}",
             "\n  Ruth's retro notes, as she left them:"]
    for r in sorted(kinds("note"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<5} {r['body']}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} questions on today's C26 planning sheet. The quick read: `search` the question, take "
         "the closest retro note on that change, follow its advice")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']} ({story['changes'][t['change']]}): “{t['text']}”", f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← closest note" if h["record"] == q["note"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["note"]:
            lines.append(f"  → {q['note']}: “{clip(q['note_body'], 170)}”")
        lines.append(f"  → quick read on the {story['changes'][t['change']]}: {q['advice'] or '?'}")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], note=q["note"], note_body=q.get("note_body", ""), advice=q["advice"],
                                  said=q["said"], hits=[public(h) for h in q["hits"]], cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every cohort (`fact list`, every page), grouped by which of the note's bundle it ran. A "
         "change gets credit only from cohorts that ran it without the rest; if no cohort ever did, the answer is "
         "\"can't tell\"")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        if not q["note"]:
            continue
        mr = memory_read(cli, pid, q["bundle"], q["metric"], t["change"], echo=i == 0)
        tb = mr["table"]
        names = " + ".join(story["changes"][b] for b in tb["bundle"])
        lines = [f"\n  {t['id']} · {q['note']} is about: {names}"]
        lines += table_lines(tb, story, q["credits"])
        if len(tb["bundle"]) > 1:
            lines.append(f"  · the note's before/after is {note_compares(tb)} — it never looks at the rows in between")
        for ch, v in mr["verdicts"].items():
            lines.append(f"  · {story['changes'][ch]}: {v['advice']} — {v['why']}")
        lines.append(f"  → quick read:  {q['advice']}")
        lines.append(f"  → memory read: {mr['advice']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, note=q["note"], quick=q["advice"], memory=mr, agree=agrees(q["advice"], mr["advice"])))
    lines = [f"\n  {'q':<4} {'change':<28} {'the note says':<15} {'the cohorts say':<16}", "  " + "-" * 74]
    for r in rows:
        lines.append(f"  {r['ticket']['id']:<4} {story['changes'][r['ticket']['change']]:<28} {r['quick']:<15} "
                     f"{r['memory']['advice']:<16} {'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)}: the note credits one change of a bundle the cohorts either split the "
                 "other way or never split at all" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], note=r["note"], quick=r["quick"], agree=r["agree"],
                                  memory={k: r["memory"][k] for k in ("change", "advice", "said", "verdicts")},
                                  table=r["memory"]["table"]) for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def rewritten_note(n: dict, mr: dict, story: dict) -> tuple[str, dict]:
    """The note's conclusion only; the counts (they change with every cohort) go to metadata."""
    tb, vs = mr["table"], mr["verdicts"]
    names = [story["changes"][b] for b in tb["bundle"]]
    advice = {ch: v["advice"] for ch, v in vs.items()}
    head = (f"Curriculum retro note (rewritten by {story['lead']['name']} from the cohort results), {tb['label']}, "
            f"{names[0]} and {names[1]}, shipped together: ")
    if all(a == "can't tell" for a in advice.values()):
        body = (f"no cohort has run one without the other, so the results cannot say which one moved the rate. "
                f"Change one at a time, in one cohort, before dropping either.")
    else:
        keep = [story["changes"][c] for c, a in advice.items() if a == "keep"]
        go = [story["changes"][c] for c, a in advice.items() if a == "can go"]
        body = (f"the {' and the '.join(keep)} carries the gain; cohorts that had it without the "
                f"{' and the '.join(go)} did about as well as with both, and the {' and the '.join(go)} on its own "
                f"did not move the rate. If TA hours are cut, keep the {' and the '.join(keep)}.")
    return head + body, advice


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next planning session will read it: each note that credited one change of a bundle "
         "is rewritten from the cohorts (`fact update --text`; the old text, the old advice and the grouped counts go to "
         "metadata)")
    updates, seen = [], set()
    for r in rows:
        if r["agree"] or r["note"] in seen:
            continue
        seen.add(r["note"])
        n = mem[r["note"]]
        text, advice = rewritten_note(n, r["memory"], story)
        if text == n["body"]:
            continue
        tb = r["memory"]["table"]
        meta = {k: n[k] for k in ("key", "record", "tool", "kind", "by", "on", "bundle", "metric")}
        # No null values (the server drops them): a note that can credit neither change says "none".
        kept = [c for c, a in advice.items() if a == "keep"]
        meta.update(credits=kept[0] if len(kept) == 1 else "none", prefer=advice, previous=n["body"],
                    previous_prefer=n["prefer"], previous_credits=n["credits"], rewritten_on=today,
                    rewritten_by=story["lead"]["name"],
                    basis={g["name"]: dict(cohorts=g["cohorts"], k=g["k"], n=g["n"]) for g in tb["groups"]},
                    cohorts=tb["n"])
        updates.append((n, text, meta))
    for i, (n, text, meta) in enumerate(updates):
        cli.run("fact", "update", n["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more note(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for n, _, _ in updates:
        lines.append(f"\n    {n['record']} was “{clip(n['body'], 170)}”")
        lines.append(f"    {'':<{len(n['record'])}} now “{mem2[n['record']]['body']}”")
    emit("text", "\n".join(lines))
    trace = []
    if updates:
        n = updates[0][0]
        res = cli.run("fact", "trace", n["fid"], "--project", pid, scoped=True) or {}
        trace = [dict(event=e.get("event"), how=e.get("source_kind"), at=(e.get("timestamp") or "")[:19],
                      old=e.get("old_fact") or "", new=e.get("new_fact") or "") for e in res.get("trace") or []]
        tl = [f"\n  fact trace {n['record']} (newest first):"]
        for e in trace:
            tl.append(f"    {e['event']:<7} {e['how']:<7} {e['at']}  {clip(e['new'], 90)}")
        emit("text", "\n".join(tl))
    # Later the same day, new wording (search answers a repeated question from its cache).
    note("later the same day, three more planning questions on the same changes — the quick read again, unchanged code. "
         "Search gets a rewritten fact a few seconds after `fact list` does; each read is retried until its note is the "
         "current text")
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
        mr = memory_read(cli, pid, q["bundle"], q["metric"], t["change"], echo=False) if q["note"] else None
        after.append(dict(ticket=t, note=q["note"], note_rank=q.get("note_rank"), quick=q["advice"],
                          memory=mr["advice"] if mr else "", agree=bool(mr) and agrees(q["advice"], mr["advice"]),
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']} ({story['changes'][a['ticket']['change']]}): “{a['ticket']['text']}”")
        lines.append(f"    closest note {a['note']} (#{a['note_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick']} · memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the cohorts on {agree} of {len(after)}: the notes it follows are "
                 "the ones the cohort results rewrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=n["record"], was=n["body"], now=mem2[n["record"]]["body"],
                             was_prefer=n["prefer"], now_prefer=mem2[n["record"]].get("prefer")) for n, _, _ in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: plan, cohorts

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def plan_text(cli: CLI, pid: str, text: str, change: str, echo: bool = True) -> dict:
    """Any planning question about one change: the quick read and the memory read side by side. If the note was
    rewritten, also what it said before (metadata.previous), so the two reads can still be compared after a run."""
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", change=change, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, q["bundle"], q["metric"], change, echo=echo) if q["note"] else None
    n = mem.get(q["note"]) if q["note"] else None
    before = None
    if n and n.get("previous"):
        before = dict(text=n["previous"], advice=(n.get("previous_prefer") or {}).get(change, ""))
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], note=q["note"], note_body=q.get("note_body", ""),
                quick=q["advice"], before=before,
                memory=({k: mr[k] for k in ("change", "advice", "said", "verdicts")} if mr else None),
                table=mr["table"] if mr else None, credits=q.get("credits"),
                agree=bool(mr) and agrees(q["advice"], mr["advice"]))


def bundle_of(story: dict, change: str) -> list[str]:
    for r in story["records"]:
        if r["kind"] == "note" and change in r["bundle"]:
            return r["bundle"]
    return [change]


def cohorts_text(cli: CLI, pid: str, change: str, echo: bool = True) -> dict:
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    bundle = bundle_of(story, change)
    metric = next(r["metric"] for r in story["records"] if r["kind"] == "note" and r["bundle"] == bundle)
    tb = bundle_table(mem, bundle, metric, story)
    cohorts = sorted((r for r in mem.values() if r.get("kind") == "cohort"), key=lambda r: r["on"])
    return dict(table=tb, verdicts=verdicts(tb, story), cohorts=[public(c) for c in cohorts])


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
    banner(2, TOTAL, "Set up — one project for the Module 3 curriculum")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: change log, cohort results, retro notes")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the closest retro note, and its advice")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — the cohorts where the bundle came apart")
    rows = memory_review(cli, pid, story, quick)
    banner(6, TOTAL, "Write it back — the notes from the cohorts, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   note={q["ticket"]["id"]: [q["note"], q.get("note_rank")] for q in quick},
                   quick={r["ticket"]["id"]: r["quick"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["advice"] for r in rows},
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["note"], a["note_rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


CHANGES = ["LAB", "VIZ", "QUIZ", "GRADER", "DEADLINE"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "plan", "cohorts", "cleanup"],
                    help="run = full demo (default); plan \"QUESTION\" --change LAB|VIZ|QUIZ|GRADER|DEADLINE = the quick "
                         "read and the memory read of any planning question; cohorts LAB|… = every cohort grouped by the "
                         "bundle that change shipped in; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the planning question (plan) or the change (cohorts)")
    ap.add_argument("--change", default="LAB", choices=CHANGES, help="plan: the change the question is about")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("plan", "cohorts") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. plan \"Can the lab go if TA hours are cut?\" --change LAB, "
                 "or cohorts GRADER")
    if a.command == "cohorts" and a.args[0] not in CHANGES:
        ap.error(f"cohorts takes one of: {', '.join(CHANGES)}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py plan \"Can the lab go if TA hours are cut?\" --change LAB`, "
              "`python3 demo.py cohorts GRADER`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "plan":
        banner(2, 2, "Plan — the quick read and the memory read")
        r = plan_text(cli, pid, a.args[0], a.change)
        lines = [f"\n  “{r['ticket']['text']}” · {story['changes'][a.change]} — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← closest note" if h["record"] == r["note"] else "") for h in r["hits"]]
        if r["note"]:
            lines.append(f"  → {r['note']}: “{clip(r['note_body'], 170)}”")
        lines.append(f"  → quick read:  {r['quick'] or '?'}")
        if r["before"]:
            lines.append(f"  · {r['note']} was rewritten by a run; before that it said “{clip(r['before']['text'], 120)}” "
                         f"→ the quick read would have said: {r['before']['advice']}")
        if r["memory"]:
            lines += table_lines(r["table"], story, r["credits"] if not r["before"] else "")
            for ch, v in r["memory"]["verdicts"].items():
                lines.append(f"  · {story['changes'][ch]}: {v['advice']} — {v['why']}")
            lines.append(f"  → memory read: {r['memory']['advice']}  {'✓ same' if r['agree'] else '✗ differs'}"
                         + (f" (before the rewrite: {'✓ same' if r['before']['advice'] == r['memory']['advice'] else '✗ differs'})"
                            if r["before"] else ""))
        emit("text", "\n".join(lines))
    elif a.command == "cohorts":
        s = cohorts_text(cli, pid, a.args[0])
        tb = s["table"]
        banner(2, 2, f"Cohorts — by {' + '.join(story['changes'][b] for b in tb['bundle'])}")
        lines = [f"\n  {len(s['cohorts'])} cohort(s):",
                 f"    {'id':<5} {'started':<10} {'track':<8} {tb['label']:<42} live"]
        for c in s["cohorts"]:
            v = c[tb["metric"]]
            lines.append(f"    {c['record']:<5} {c['on']:<10} {c['track']:<8} {pc(*v):<42} {', '.join(c['live']) or '—'}")
        lines.append("")
        lines += table_lines(tb, story)
        for ch, v in s["verdicts"].items():
            lines.append(f"  · {story['changes'][ch]}: {v['advice']} — {v['why']}")
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

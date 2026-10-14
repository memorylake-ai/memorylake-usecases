#!/usr/bin/env python3
"""
Deal review memory for sales teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tallgrass Software (fictional) sells inventory-planning software to SMB and
enterprise retailers, distributors and clinics.  Its deal reviews live in one
MemoryLake project: every closed deal from the CRM (word for word: segment,
opening play, when the security questionnaire went out, won or lost) and the
review notes Marcus Hale left when he handed the team to Priya Raman.

The quick read is what the page calls "reflection memory for deal patterns":
`search` the deal being reviewed, take the closest review note, follow it.
Marcus's note on the opening play says pilot-first closes more often — 8 of 14
closed deals won with a two-week pilot, 7 of 14 with an exec-sponsor call — and
tells the team to open every deal with a pilot, enterprise included.

The count is right.  The conclusion is not.  The memory read takes every closed
deal (`fact list`, every page) and compares the two plays inside each segment:
the exec-sponsor call wins as often or more in both — SMB 3 of 4 vs 7 of 10,
enterprise 4 of 10 vs 1 of 4.  Pilot-first only wins overall because it was run
on SMB deals 10 of 14 times, and SMB deals close twice as often.  The other
note (send the security questionnaire with the proposal) is the control: it
holds inside both segments, and the two reads agree.

Step 6 rewrites the note that read a segment mix as a play effect (`fact update
--text`, the old text kept in metadata.previous, the change visible in `fact
trace`), and the same quick-read code then agrees with the closed-deal log on
the day's later reviews.

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

PREFIX = "mlu-drv"  # memorylake-usecases / deal-review-memory


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
    m = dict(key=f"drv:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], by=r["by"], on=r["on"])
    if r["kind"] == "deal":
        m.update(account=r["account"], segment=r["segment"], seats=r["seats"], play=r["play"], security=r["security"],
                 outcome=r["outcome"])
    else:
        m.update(about=r["about"], prefer=r["prefer"])
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
    """The pinned records by record id (D-2207, NOTE-PLAY …), metadata flattened in, plus fact id and text."""
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
    return {k: r.get(k) for k in ("record", "tool", "kind", "by", "on", "account", "segment", "seats", "play", "security",
                                  "outcome", "about", "prefer", "fid", "body", "rank", "previous")}


def prefer_for(r: dict, segment: str) -> str:
    """A note's advice for one segment: one answer for every deal, or one per segment once the note is rewritten."""
    p = r.get("prefer") or ""
    return p.get(segment, "") if isinstance(p, dict) else p


# --------------------------------------------------------------------------- the slice (plain code, no model)

def pct(won: int, n: int) -> str:
    return f"{won}/{n} {round(100 * won / n):>3}%" if n else "  —     "


def pc(won: int, n: int) -> str:
    return f"{won}/{n} ({round(100 * won / n)}%)" if n else "—"


def tally(deals: list[dict]) -> tuple[int, int]:
    return sum(1 for d in deals if d["outcome"] == "won"), len(deals)


def slice_by(mem: dict, factor: str, story: dict) -> dict:
    """Every closed deal, by the factor's option — overall and inside each segment — plus the mix: how often each
    option was run in each segment, and how often each segment closes at all."""
    deals = [r for r in mem.values() if r.get("kind") == "deal"]
    segs = story["segments"]
    rows = []
    for opt in story["factors"][factor]["options"]:
        ds = [d for d in deals if d.get(factor) == opt]
        rows.append(dict(option=opt, all=tally(ds), seg={s: tally([d for d in ds if d["segment"] == s]) for s in segs}))
    base = {s: tally([d for d in deals if d["segment"] == s]) for s in segs}
    return dict(factor=factor, label=story["factors"][factor]["label"], n=len(deals), rows=rows, base=base)


def rate(t: tuple[int, int]) -> float:
    return t[0] / t[1] if t[1] else 0.0


def decide(sl: dict, segment: str, gap: int) -> tuple[str, str]:
    """Inside the deal's own segment: the option with the higher win rate, if it leads by `gap` points or more;
    otherwise either option will do."""
    rs = sorted(sl["rows"], key=lambda r: -rate(r["seg"][segment]))
    a, b = rs[0], rs[1]
    lead = round(100 * (rate(a["seg"][segment]) - rate(b["seg"][segment])))
    if lead >= gap:
        return a["option"], f"{a['option']} ({pc(*a['seg'][segment])} vs {pc(*b['seg'][segment])} in {segment})"
    return "either", (f"either ({a['option']} {pc(*a['seg'][segment])} vs {b['option']} "
                      f"{pc(*b['seg'][segment])} in {segment} — under {gap} points apart)")


def overall_pick(sl: dict) -> str:
    return max(sl["rows"], key=lambda r: rate(r["all"]))["option"]


def reversed_in(sl: dict) -> list[str]:
    """The segments in which the overall leader does not lead."""
    top = overall_pick(sl)
    out = []
    for s in sl["base"]:
        best = max(sl["rows"], key=lambda r: (rate(r["seg"][s]), r["option"] != top))
        if best["option"] != top:
            out.append(s)
    return out


def mix_line(sl: dict) -> str:
    segs = list(sl["base"])
    parts = []
    for r in sl["rows"]:
        n = r["all"][1]
        parts.append(f"{r['option']} ran on {segs[0]} deals {r['seg'][segs[0]][1]} of {n} times")
    base = ", ".join(f"{s} deals close {pc(*sl['base'][s])}" for s in segs)
    return "; ".join(parts) + f" — and {base}"


def slice_lines(sl: dict, segment: str = "", quick: str = "", memory: str = "") -> list[str]:
    segs = list(sl["base"])
    lines = [f"      {sl['label']:<22} {'all deals':>11}  " + "  ".join(f"{s:>11}" for s in segs)]
    for r in sl["rows"]:
        mark = ""
        if r["option"] == quick:
            mark += "  ← the note"
        if r["option"] == memory:
            mark += f"  ← memory read ({segment})"
        lines.append(f"      {r['option']:<22} {pct(*r['all']):>11}  " + "  ".join(f"{pct(*r['seg'][s]):>11}" for s in segs) + mark)
    lines.append(f"      {'(every deal)':<22} {pct(sum(sl['base'][s][0] for s in segs), sl['n']):>11}  "
                 + "  ".join(f"{pct(*sl['base'][s]):>11}" for s in segs))
    return lines


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
    return f"      #{h['rank']:<3} {h['record']:<9} {tool:<12} {clip(h['body'], 80)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, t: dict, echo: bool = True) -> dict:
    """The page's "reflection memory for deal patterns", done the quick way: search the deal under review, take the
    closest review note, follow its advice. The note is read from `fact list` by fact id (search can answer from a
    cache of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    note_hit = next((h for h in hits if h["kind"] == "note"), None)
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    if note_hit is None:
        return dict(ticket=t, hits=hits, note=None, advice="", said="no review note in the top 8", cached=cached)
    cur = by_fid.get(note_hit["fid"], note_hit)
    advice = prefer_for(cur, t["segment"])
    return dict(ticket=t, hits=hits, note=cur["record"], note_rank=note_hit["rank"], note_body=cur["body"],
                about=cur.get("about"), advice=advice, cached=cached, said=f"the note says {advice}")


# --------------------------------------------------------------------------- read 2: inside the segment

def memory_read(cli: CLI, pid: str, factor: str, segment: str, echo: bool = True) -> dict:
    """Every closed deal (`fact list`, every page), the note's factor split by segment, the call made inside the
    deal's own segment."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    sl = slice_by(mem, factor, story)
    pick, said = decide(sl, segment, story["gap"])
    return dict(factor=factor, segment=segment, slice=sl, advice=pick, said=said, reversed=reversed_in(sl),
                overall=overall_pick(sl))


def agrees(quick: str, memory: str) -> bool:
    return bool(quick) and (quick == memory or memory == "either")


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


REWRITE_KEYS = ("previous", "previous_prefer", "rewritten_on", "rewritten_by", "basis")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning of Priya's first deal review: notes an earlier run rewrote are put back word
    for word from metadata.previous."""
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
        note(f"an earlier run rewrote {n} note(s); put back, so this run starts from the same morning")
    return n


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("two sources, one project: every closed deal from the CRM and the review notes Marcus Hale left. One fact per "
         "record, word for word, then `fact update --metadata` (segment, opening play, questionnaire timing, outcome)")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    deals = [r for r in mem.values() if r["kind"] == "deal"]
    lines = [f"    {story['tools']['crm']:<14} {len(deals):>2}   "
             + " · ".join(f"{s} {sum(1 for d in deals if d['segment'] == s)}" for s in story["segments"]),
             f"    {story['tools']['review']:<14} {sum(1 for r in mem.values() if r['kind'] == 'note'):>2}",
             "\n  Marcus's notes, as he left them:"]
    for r in sorted((r for r in mem.values() if r["kind"] == "note"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<9} {r['body']}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} open deals on today's review. The quick read: `search` the deal, take the closest "
         "review note, follow its advice")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']} ({t['segment']}): “{t['text']}”", f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← closest note" if h["record"] == q["note"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["note"]:
            lines.append(f"  → {q['note']}: “{clip(q['note_body'], 160)}”")
        lines.append(f"  → quick read: {q['advice'] or '?'}")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], note=q["note"], note_body=q.get("note_body", ""), advice=q["advice"],
                                  said=q["said"], hits=[public(h) for h in q["hits"]], cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every closed deal (`fact list`, every page), the note's factor counted overall and inside "
         "each segment, the call made inside the deal's own segment")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        if not q["note"]:
            continue
        mr = memory_read(cli, pid, q["about"], t["segment"], echo=i == 0)
        sl = mr["slice"]
        lines = [f"\n  {t['id']} {t['account']} · {t['segment']} · {sl['label']}"]
        lines += slice_lines(sl, t["segment"], q["advice"], mr["advice"] if mr["advice"] != "either" else "")
        if mr["reversed"]:
            lines.append(f"  · the note's comparison is the “all deals” column: {mr['overall']} leads there, and leads in "
                         f"neither segment. {mix_line(sl)}")
        else:
            lines.append(f"  · the note holds: {mr['overall']} leads overall and inside every segment")
        lines.append(f"  → quick read:  {q['advice']}")
        lines.append(f"  → memory read: {mr['said']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, note=q["note"], quick=q["advice"], memory=mr, agree=agrees(q["advice"], mr["advice"])))
    lines = [f"\n  {'deal':<5} {'account':<24} {'the note says':<20} {'the closed deals say':<22}", "  " + "-" * 78]
    for r in rows:
        lines.append(f"  {r['ticket']['id']:<5} {r['ticket']['account']:<24} {r['quick']:<20} {r['memory']['advice']:<22} "
                     f"{'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)} reviews: the note sends the deal down the play that wins less often "
                 "in its own segment" if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], note=r["note"], quick=r["quick"], agree=r["agree"],
                                  memory={k: r["memory"][k] for k in ("factor", "segment", "advice", "said", "reversed", "overall")},
                                  slice=r["memory"]["slice"], mix=mix_line(r["memory"]["slice"])) for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def rewritten_note(n: dict, mr: dict, story: dict) -> tuple[str, dict]:
    """The note's conclusion only; the counts (they change with every closed deal) go to metadata."""
    sl = mr["slice"]
    story_gap = story["gap"]
    advice = {s: decide(sl, s, story_gap)[0] for s in story["segments"]}
    opts = [r["option"] for r in sl["rows"]]
    top = mr["overall"]
    other = next(o for o in opts if o != top)
    big = max(sl["base"], key=lambda s: rate(sl["base"][s]))
    parts = []
    for s in story["segments"]:
        a = advice[s]
        parts.append(f"{s} deals: either play." if a == "either" else f"{s} deals: open with the {a}.")
    text = (f"Deal review notes (rewritten by {story['manager']['name']} from the closed-deal log), {sl['label']}, pilot or "
            f"exec-sponsor call: {top} only looks better overall because it was run mostly on {big} deals, which close "
            f"more often anyway. Inside each segment the {other} wins as often or more. " + " ".join(parts))
    return text, advice


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next review will read it: the note that read a segment mix as a play effect is "
         "rewritten from the closed deals (`fact update --text`; the old text, the old advice and the counts go to metadata)")
    updates, seen = [], set()
    for r in rows:
        if r["agree"] or r["note"] in seen:
            continue
        seen.add(r["note"])
        n = mem[r["note"]]
        text, advice = rewritten_note(n, r["memory"], story)
        if text == n["body"]:
            continue
        sl = r["memory"]["slice"]
        meta = {k: n[k] for k in ("key", "record", "tool", "kind", "by", "on", "about")}
        basis = {row["option"]: dict(all=list(row["all"]), **{s: list(v) for s, v in row["seg"].items()}) for row in sl["rows"]}
        meta.update(prefer=advice, previous=n["body"], previous_prefer=n["prefer"], rewritten_on=today,
                    rewritten_by=story["manager"]["name"], basis=basis, deals=sl["n"])
        updates.append((n, text, meta))
    for i, (n, text, meta) in enumerate(updates):
        cli.run("fact", "update", n["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more note(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for n, _, _ in updates:
        lines.append(f"\n    {n['record']} was “{clip(n['body'], 160)}”")
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
    note("later the same day, three more deals of the same kinds — the quick read again, unchanged code. Search gets a "
         "rewritten fact a few seconds after `fact list` does; each read is retried until its note is the current text")
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
        mr = memory_read(cli, pid, q["about"], t["segment"], echo=False) if q["note"] else None
        after.append(dict(ticket=t, note=q["note"], note_rank=q.get("note_rank"), quick=q["advice"],
                          memory=mr["advice"] if mr else "", agree=bool(mr) and agrees(q["advice"], mr["advice"]),
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']} {a['ticket']['account']} · {a['ticket']['segment']}: “{a['ticket']['text']}”")
        lines.append(f"    closest note {a['note']} (#{a['note_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick']} · memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the closed deals on {agree} of {len(after)}: the note it follows "
                 "is the one the closed-deal log rewrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=n["record"], was=n["body"], now=mem2[n["record"]]["body"],
                             was_prefer=n["prefer"], now_prefer=mem2[n["record"]].get("prefer")) for n, _, _ in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: review, slice

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def review_text(cli: CLI, pid: str, text: str, segment: str, echo: bool = True) -> dict:
    """Any deal question: the quick read and the memory read side by side."""
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", account="", segment=segment, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, q["about"], segment, echo=echo) if q["note"] else None
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], note=q["note"], note_body=q.get("note_body", ""),
                quick=q["advice"],
                memory=({k: mr[k] for k in ("factor", "segment", "advice", "said", "reversed", "overall")} if mr else None),
                slice=mr["slice"] if mr else None, mix=mix_line(mr["slice"]) if mr else "",
                agree=bool(mr) and agrees(q["advice"], mr["advice"]))


def slice_text(cli: CLI, pid: str, factor: str, echo: bool = True) -> dict:
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    sl = slice_by(mem, factor, story)
    deals = sorted((r for r in mem.values() if r.get("kind") == "deal"), key=lambda r: r["on"])
    return dict(slice=sl, mix=mix_line(sl), reversed=reversed_in(sl), overall=overall_pick(sl),
                deals=[public(d) for d in deals])


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
    banner(2, TOTAL, "Set up — one project for the deal reviews")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: closed deals and review notes")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the closest review note, and its advice")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — inside each segment")
    rows = memory_review(cli, pid, story, quick)
    banner(6, TOTAL, "Write it back — the note from the closed deals, then the quick read again")
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "review", "slice", "cleanup"],
                    help="run = full demo (default); review \"DEAL QUESTION\" --segment SMB|Enterprise = the quick read and "
                         "the memory read of any deal question; slice play|security = every closed deal by that factor, "
                         "overall and inside each segment; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the deal question (review) or the factor (slice)")
    ap.add_argument("--segment", default="Enterprise", choices=["SMB", "Enterprise"], help="review: the deal's segment")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("review", "slice") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. review \"Open with a pilot or an exec-sponsor call?\" --segment "
                 "Enterprise, or slice play")
    if a.command == "slice" and a.args[0] not in story["factors"]:
        ap.error(f"slice takes one of: {', '.join(story['factors'])}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py review \"Should a 3,000-seat hospital group start with a pilot?\" --segment "
              "Enterprise`, `python3 demo.py slice play`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "review":
        banner(2, 2, "Review — the quick read and the memory read")
        r = review_text(cli, pid, a.args[0], a.segment)
        lines = [f"\n  “{r['ticket']['text']}” · {a.segment} — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← closest note" if h["record"] == r["note"] else "") for h in r["hits"]]
        if r["note"]:
            lines.append(f"  → {r['note']}: “{clip(r['note_body'], 160)}”")
        lines.append(f"  → quick read:  {r['quick'] or '?'}")
        if r["memory"]:
            lines += slice_lines(r["slice"], a.segment, r["quick"],
                                 r["memory"]["advice"] if r["memory"]["advice"] != "either" else "")
            lines.append(f"  → memory read: {r['memory']['said']}  {'✓ same' if r['agree'] else '✗ differs'}")
        emit("text", "\n".join(lines))
    elif a.command == "slice":
        banner(2, 2, f"Slice — every closed deal by {story['factors'][a.args[0]]['label']}")
        s = slice_text(cli, pid, a.args[0])
        lines = [f"\n  {len(s['deals'])} closed deal(s):",
                 f"    {'id':<7} {'closed':<10} {'account':<26} {'segment':<10} {story['factors'][a.args[0]]['label']:<20} outcome"]
        for d in s["deals"]:
            lines.append(f"    {d['record']:<7} {d['on']:<10} {d['account']:<26} {d['segment']:<10} {d[a.args[0]]:<20} {d['outcome']}")
        lines.append("")
        lines += slice_lines(s["slice"])
        lines.append(f"  · overall leader: {s['overall']}; " + (f"it does not lead inside {and_list(s['reversed'])}"
                                                              if s["reversed"] else "it leads inside every segment too"))
        lines.append(f"  · mix: {s['mix']}")
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

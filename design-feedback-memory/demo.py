#!/usr/bin/env python3
"""
Design feedback memory across clients — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Quillon Studio (fictional) keeps its client feedback in one MemoryLake project:
every review round as the client wrote it (Figma comments, email threads, Loom
transcripts), with what the comp showed on that point, and one studio note per
client preference -- the reflection memory the page promises, written by a
senior designer who has since left.

The quick read is what every AI design tool does with it: `search` the brief,
take the client's note, default to it. The notes say Harbor & Pine wants tight
type ("approved with tight type in 6 of 7 rounds"), Lumen Health wants generous
white space (4 of 4) and Vela Outdoor never pairs teal with orange (5 of 5).

The counts are right. Most of them are the note's own output: once a note was
written, the comps showed only what it said, and the client approved the only
option on the table. The memory read goes back to the rounds (`fact list`, every
page) and counts only rounds where the client had a choice -- asked for it
unprompted, or picked it from two options. Harbor & Pine: 1 for (R1), 1 against
(R5 picked airy type when shown both), 5 echoes -> show both. Vela Outdoor: 1
for, 4 echoes -> unconfirmed. Lumen Health is the control: the note was written
after 4 rounds in which the client asked for space each time -> both reads agree.

Step 6 rewrites the two notes from the rounds (`fact update --text`, the old
text kept in metadata.previous, the change visible in `fact trace`), and the
same quick-read code then agrees with the rounds on later briefs.

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

PREFIX = "mlu-dfm"  # memorylake-usecases / design-feedback-memory

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
    m = dict(key=f"dfm:{r['id']}", record=r["id"], kind=r["kind"], client=r["client"], topic=r["topic"],
             tool=r["tool"], by=r["by"], on=r["on"])
    if r["kind"] == "round":
        m.update(round=r["round"], asset=r["asset"], shown=r["shown"], response=r["response"], value=r["value"])
    elif r["kind"] == "note":
        m.update(value=r["value"], prefer=r["prefer"], evidence=r["evidence"], counted=r["counted"])
    return m


def opt(story: dict, topic: str, value: str) -> str:
    return story["topics"][topic]["options"].get(value, value)


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
    """The pinned records by record id (HP-R1, N-HP …), metadata flattened in, plus fact id and text."""
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
    return {k: r.get(k) for k in ("record", "kind", "client", "topic", "tool", "by", "on", "round", "asset", "shown",
                                  "response", "value", "prefer", "evidence", "counted", "fid", "body", "rank",
                                  "previous", "role", "why")}


def kind(mem: dict, k: str) -> list[dict]:
    return sorted((r for r in mem.values() if r.get("kind") == k), key=lambda r: (r["on"], r["record"]))


def note_for(mem: dict, client: str, topic: str):
    return next((r for r in kind(mem, "note") if r["client"] == client and r["topic"] == topic), None)


# --------------------------------------------------------------------------- the rounds (plain code, no model)

def classify(story: dict, mem: dict, note: dict) -> list[dict]:
    """Every review round on the note's client and topic, judged by one question: did the client have a choice?

    - asked:   the client asked for something the comp did not have          → evidence (for or against the note)
    - picked:  the comp showed two or more options and the client picked one → evidence
    - approved, one option, the note's own, after the note was written       → echo: the client approved what the
               note produced; nothing else was on the table, so it says nothing about the preference
    - approved, one option, no note behind it                                 → (weak) evidence for that option
    """
    rounds = [r for r in kind(mem, "round") if r["client"] == note["client"] and r["topic"] == note["topic"]]
    out = []
    for r in rounds:
        shown = r.get("shown") or []
        t = note["topic"]
        comp = " + ".join(opt(story, t, s) for s in shown) + ("" if len(shown) > 1 else " only")
        if r["response"] == "asked":
            role, why = ("for" if r["value"] == note["value"] else "against"), f"asked for {opt(story, t, r['value'])}"
        elif r["response"] == "picked" and len(shown) > 1:
            role, why = ("for" if r["value"] == note["value"] else "against"), f"picked {opt(story, t, r['value'])}"
        elif shown == [note["value"]] and r["on"] > note["on"]:
            role, why = "echo", f"approved — only {note['record']}'s option on the comp"
        else:
            role, why = ("for" if r["value"] == note["value"] else "against"), f"approved {opt(story, t, r['value'])}"
        out.append(dict(r, role=role, why=why, comp=comp))
    return out


def verdict(rows: list[dict]) -> dict:
    """`default to it` if at least two rounds with a choice back the note and none goes against it; `show both` if
    rounds with a choice go both ways; `unconfirmed` if a single round backs it; `drop it` if none does."""
    k = sum(1 for r in rows if r["role"] == "for")
    a = sum(1 for r in rows if r["role"] == "against")
    e = sum(1 for r in rows if r["role"] == "echo")
    if k >= 2 and a == 0:
        advice = "default to it"
    elif k and a:
        advice = "show both"
    elif k == 1 and a == 0:
        advice = "unconfirmed"
    else:
        advice = "drop it"
    ids = lambda role: [r["round"] for r in rows if r["role"] == role]
    why = (f"rounds where the client had a choice: {k} for ({', '.join(ids('for')) or '—'}), "
           f"{a} against ({', '.join(ids('against')) or '—'}); {e} echo{'es' if e != 1 else ''} not counted")
    return dict(advice=advice, k=k, a=a, e=e, why=why, rounds=len(rows))


def round_lines(story: dict, rows: list[dict]) -> list[str]:
    lines = []
    for r in rows:
        tag = {"for": "for", "against": "AGAINST", "echo": "echo"}[r["role"]]
        lines.append(f"      {r['round']:<3} {r['on']}  {story['tools'][r['tool']]:<16} comp: {clip(r['comp'], 28):<28} "
                     f"{clip(r['why'], 42):<42} {tag}")
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
    return f"      #{h['rank']:<3} {h['record']:<6} {tool:<16} {clip(h['body'], 76)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, t: dict, echo: bool = True) -> dict:
    """What every AI design tool does with feedback memory: search the brief, take the client's note on that point,
    default to it. The note is read from `fact list` by fact id (search can answer from a cache of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    hit = next((h for h in hits if by_fid.get(h["fid"], h).get("kind") == "note"
                and by_fid.get(h["fid"], h).get("client") == t["client"]
                and by_fid.get(h["fid"], h).get("topic") == t["topic"]), None)
    if hit is None:
        return dict(ticket=t, hits=hits, note=None, advice="", said="no note on this point in the top 8", cached=cached)
    cur = by_fid.get(hit["fid"], hit)
    return dict(ticket=t, hits=hits, note=cur["record"], note_rank=hit["rank"], note_body=cur["body"],
                evidence=cur.get("evidence"), value=cur.get("value"), advice=cur.get("prefer") or "", cached=cached,
                said=f"the note says {cur.get('prefer')}")


# --------------------------------------------------------------------------- read 2: the rounds, choices only

def memory_read(cli: CLI, pid: str, client: str, topic: str, echo: bool = True) -> dict:
    """Every review round (`fact list`, every page), each judged by whether the client had a choice."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    note = note_for(mem, client, topic)
    if note is None:
        die(f"no studio note for {client} / {topic}")
    rows = classify(story, mem, note)
    v = verdict(rows)
    return dict(client=client, topic=topic, note=note["record"], value=note["value"], rows=rows, verdict=v,
                advice=v["advice"], said=f"{v['advice']} — {v['why']}")


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
    """Every run starts on the morning of the loyalty-app brief: notes an earlier run rewrote are put back word for
    word from metadata.previous, with the advice they had."""
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
    note("three tools and the studio's notes, one project. One fact per record, word for word, then `fact update "
         "--metadata` (client, point, round, date, what the comp showed, how the client answered; for a note: the "
         "preference, its count and its advice)")
    got = pin(cli, pid, [(r["text"], record_meta(r)) for r in story["records"]])
    new = sum(1 for g in got.values() if g["new"])
    if not new:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    mem = entries(list_facts(cli, pid))
    rounds = kind(mem, "round")
    lines = []
    for t in ("figma", "email", "loom"):
        rs = [r for r in rounds if r["tool"] == t]
        lines.append(f"    {story['tools'][t]:<17} {len(rs):>2}   " + " ".join(r["record"] for r in rs))
    lines.append(f"    {story['tools']['notes']:<17} {len(kind(mem, 'note')):>2}   "
                 + " ".join(r["record"] for r in kind(mem, "note")))
    lines.append(f"\n  {story['author']['name']}'s notes, as they stand:")
    for r in sorted(kind(mem, "note"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<5} {r['body']}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["client"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} briefs on today's board. The quick read: `search` the brief, take the client's "
         "note on that point, default to it")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']} ({story['clients'][t['client']]}, {story['topics'][t['topic']]['label']}): “{t['text']}”",
                 f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← the note" if h["record"] == q["note"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["note"]:
            lines.append(f"  → {q['note']}: “{clip(q['note_body'], 170)}”")
            lines.append(f"  → quick read: {q['advice']} ({opt(story, t['topic'], q['value'])})")
        else:
            lines.append("  → quick read: ? (no note on this point in the top 8)")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], note=q["note"], note_body=q.get("note_body", ""),
                                  advice=q["advice"], said=q["said"], hits=[public(h) for h in q["hits"]],
                                  cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, mem: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every review round (`fact list`, every page), one question each — did the client have a "
         "choice? A round where the comp only had what the note says, after the note was written, is the note's own "
         "output being approved: an echo, not evidence")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        mr = memory_read(cli, pid, t["client"], t["topic"], echo=i == 0)
        v = mr["verdict"]
        n = mem[mr["note"]]
        ev = n.get("evidence") or [0, 0]
        lines = [f"\n  {t['id']} · {mr['note']} ({n['on']}) counts {ev[0]} of {ev[1]} rounds for "
                 f"{opt(story, t['topic'], n['value'])}:"]
        lines += round_lines(story, mr["rows"])
        lines.append(f"  · {v['advice']} — {v['why']}")
        lines.append(f"  → quick read:  {q['advice'] or '?'}")
        lines.append(f"  → memory read: {mr['advice']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, note=q["note"], quick=q["advice"], memory=mr, agree=agrees(q["advice"], mr["advice"])))
    lines = [f"\n  {'q':<4} {'client':<21} {'point':<11} {'the note says':<15} {'the rounds say':<15}", "  " + "-" * 76]
    for r in rows:
        t = r["ticket"]
        lines.append(f"  {t['id']:<4} {story['clients'][t['client']]:<21} {story['topics'][t['topic']]['label']:<11} "
                     f"{r['quick'] or '?':<15} {r['memory']['advice']:<15} {'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)}: the notes counted approvals of comps they had shaped themselves"
                 if wrong else f"\n  → the two reads agree on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], note=r["note"], quick=r["quick"], agree=r["agree"],
                                  memory={k: r["memory"][k] for k in ("client", "topic", "note", "advice", "said", "verdict")},
                                  rounds=[public(x) for x in r["memory"]["rows"]]) for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def rewritten_note(n: dict, mr: dict, story: dict) -> str:
    """The note's conclusion and the rounds it rests on — only what changes when the conclusion does."""
    v, t = mr["verdict"], n["topic"]
    rows = mr["rows"]
    name, label = story["clients"][n["client"]], story["topics"][t]["label"]
    head = f"{name}, {label} (rewritten by {story['lead']['name']} from the review rounds, {story['today']}): "
    choice = [r for r in rows if r["role"] in ("for", "against")]
    said = "; ".join(f"{r['round']} {r['why']}" for r in choice)
    other = opt(story, t, next(o for o in story["topics"][t]["options"] if o != n["value"]))
    if v["advice"] == "show both":
        say = (f"show both {opt(story, t, n['value'])} and {other}. Only {len(choice)} of {len(rows)} rounds gave the "
               f"client a choice ({said}).")
    elif v["advice"] == "unconfirmed":
        say = (f"{opt(story, t, n['value'])}, but it rests on one round ({said}); no comp since has shown {other}, so "
               f"nothing has tested it.")
    elif v["advice"] == "drop it":
        say = f"no round with a choice backs {opt(story, t, n['value'])}."
    else:
        say = f"{opt(story, t, n['value'])} ({said})."
    return (head + say + " Rounds where the comp only had what this note said are approvals of the note's own output, "
            "not evidence for it.")


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next AI tool will read it: each note the rounds disagree with is rewritten from the "
         "rounds (`fact update --text`; the old text, the old advice and the counts go to metadata)")
    updates = []
    for r in rows:
        if r["agree"] or not r["note"]:
            continue
        m = mem[r["note"]]
        text = rewritten_note(m, r["memory"], story)
        if text == m["body"]:
            continue
        v = r["memory"]["verdict"]
        meta = {k: m[k] for k in ("key", "record", "kind", "client", "topic", "tool", "by", "on", "value", "evidence",
                                  "counted")}
        meta.update(prefer=v["advice"], previous=m["body"], previous_prefer=m["prefer"], rewritten_on=today,
                    rewritten_by=story["lead"]["name"], basis=dict(rounds=v["rounds"], k=v["k"], a=v["a"], echo=v["e"]))
        updates.append((m, text, meta))
    for i, (m, text, meta) in enumerate(updates):
        cli.run("fact", "update", m["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more note(s) the same way")
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
    note("later the same day, three more briefs for the same clients — the quick read again, unchanged code. Search "
         "gets a rewritten fact a few seconds after `fact list` does; each read is retried until its note is the "
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
        mr = memory_read(cli, pid, t["client"], t["topic"], echo=False)
        after.append(dict(ticket=t, note=q["note"], note_rank=q.get("note_rank"), quick=q["advice"],
                          memory=mr["advice"], agree=agrees(q["advice"], mr["advice"]),
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']} ({story['clients'][a['ticket']['client']]}): “{a['ticket']['text']}”")
        lines.append(f"    note {a['note'] or '—'} (#{a['note_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick'] or '?'} · memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the rounds on {agree} of {len(after)}: the notes it follows are "
                 "the ones the rounds rewrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=m["record"], was=m["body"], now=mem2[m["record"]]["body"],
                             was_prefer=m["prefer"], now_prefer=mem2[m["record"]].get("prefer")) for m, _, _ in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, rounds

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def ask_text(cli: CLI, pid: str, text: str, client: str, echo: bool = True) -> dict:
    """Any brief for one client: the quick read and the memory read side by side. If the note was rewritten, also
    what it said before (metadata.previous), so the two reads can still be compared after a run."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=False))
    topic = next(n["topic"] for n in kind(mem, "note") if n["client"] == client)
    t = dict(id="(yours)", client=client, topic=topic, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, client, topic, echo=echo)
    m = mem.get(q["note"]) if q["note"] else None
    before = None
    if m and m.get("previous"):
        before = dict(text=m["previous"], advice=m.get("previous_prefer") or "")
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], note=q["note"], note_body=q.get("note_body", ""),
                evidence=q.get("evidence"), quick=q["advice"], before=before,
                memory={k: mr[k] for k in ("client", "topic", "note", "advice", "said", "verdict")},
                rounds=[public(x) for x in mr["rows"]], agree=agrees(q["advice"], mr["advice"]),
                label=story["topics"][topic]["label"])


def rounds_text(cli: CLI, pid: str, client: str, echo: bool = True) -> dict:
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=echo))
    n = next(n for n in kind(mem, "note") if n["client"] == client)
    rows = classify(story, mem, n)
    return dict(note=public(n), rows=[public(r) | {"comp": r["comp"]} for r in rows], verdict=verdict(rows))


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
    banner(2, TOTAL, "Set up — one project for the studio's client feedback")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: Figma, email, Loom, and the studio notes")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the client's note on each brief, and its advice")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — every round, counted only where the client had a choice")
    rows = memory_review(cli, pid, story, mem, quick)
    banner(6, TOTAL, "Write it back — the notes from the rounds, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   note={q["ticket"]["id"]: [q["note"], q.get("note_rank")] for q in quick},
                   quick={r["ticket"]["id"]: r["quick"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["advice"] for r in rows},
                   counts={r["ticket"]["id"]: [r["memory"]["verdict"][k] for k in ("k", "a", "e")] for r in rows},
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["note"], a["note_rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


CLIENTS = ["HP", "LH", "VO"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "rounds", "cleanup"],
                    help="run = full demo (default); ask \"BRIEF\" --client HP|LH|VO = the quick read and the memory "
                         "read of any brief; rounds HP|LH|VO = every review round on that client's note, echo or "
                         "evidence; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the brief (ask) or the client (rounds)")
    ap.add_argument("--client", default="HP", choices=CLIENTS, help="ask: the client the brief is for")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("ask", "rounds") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. ask \"Tight or airy headlines for the menu boards?\" --client HP, "
                 "or rounds VO")
    if a.command == "rounds" and a.args[0] not in CLIENTS:
        ap.error(f"rounds takes one of: {', '.join(CLIENTS)}")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py ask \"Tight or airy headlines for the menu boards?\" --client HP`, "
              "`python3 demo.py rounds VO`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — the quick read and the memory read")
        r = ask_text(cli, pid, a.args[0], a.client)
        lines = [f"\n  “{r['ticket']['text']}” · {story['clients'][a.client]}, {r['label']} — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← the note" if h["record"] == r["note"] else "") for h in r["hits"]]
        if r["note"]:
            lines.append(f"  → {r['note']}: “{clip(r['note_body'], 170)}”")
        lines.append(f"  → quick read:  {r['quick'] or '?'}")
        if r["before"]:
            lines.append(f"  · {r['note']} was rewritten by a run; before that it said “{clip(r['before']['text'], 120)}” "
                         f"→ the quick read would have said: {r['before']['advice']}")
        rows = [dict(x) for x in r["rounds"]]
        n = r["memory"]["note"]
        for x in rows:
            x["comp"] = " + ".join(opt(story, r["memory"]["topic"], s) for s in x["shown"] or []) + \
                ("" if len(x["shown"] or []) > 1 else " only")
        lines.append(f"  every round on {n}:")
        lines += round_lines(story, rows)
        lines.append(f"  · {r['memory']['said']}")
        lines.append(f"  → memory read: {r['memory']['advice']}  {'✓ same' if r['agree'] else '✗ differs'}"
                     + (f" (before the rewrite: {'✓ same' if r['before']['advice'] == r['memory']['advice'] else '✗ differs'})"
                        if r["before"] else ""))
        emit("text", "\n".join(lines))
    elif a.command == "rounds":
        s = rounds_text(cli, pid, a.args[0])
        n, v = s["note"], s["verdict"]
        banner(2, 2, f"Rounds — every review round on {n['record']}, echo or evidence")
        lines = [f"\n  {n['record']} ({n['on']}): “{n['body']}”" if not n.get("previous") else
                 f"\n  {n['record']} now: “{n['body']}”\n  {'':<{len(n['record'])}} was: “{n['previous']}”",
                 f"\n  {len(s['rows'])} round(s):"]
        lines += round_lines(story, s["rows"])
        lines.append(f"  · {v['advice']} — {v['why']}")
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

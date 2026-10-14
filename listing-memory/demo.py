#!/usr/bin/env python3
"""
Listing memory for real estate teams — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Realty (a fictional team) keeps one MemoryLake project per listing.
For 14 Alder Court it holds everything the team's tools saw since August — MLS
changes, the seller's disclosure, the HOA's letters, every buyer's agent's
showing feedback, a withdrawn offer, notes — one fact per record, word for
word, with the tool, the author and the day in metadata.  Today, 2026-10-15,
Jordan Pike covers the listing while Rosa Delgado is out, and the seller asks:
should we drop the price again?

The quick read is what the page promises — any agent covering the listing
retrieves its context: `search` the question, count what the showing feedback
in the hits objects to, answer.  The feedback is true.  But none of it says
which listing it was about: $749,000 with the 1998 roof, or $699,000 with a
new roof and a $14,500 HOA assessment.  The quick read counts every objection
against the listing as it is today.

The memory read uses the same counting code with one change: each piece of
feedback is joined to the listing as it was on the day of that showing (the
MLS, permit and HOA records on file that day).  Every "too expensive" was said
at $749,000; nobody has mentioned the roof since it was replaced; the buyers
walking away now are walking away from the assessment.  The kitchen is the
control: nothing about it changed, and the two reads agree.

Step 6 writes the join back where the next read will see it (`fact update
--text`: each piece of feedback now says which listing it was shown at; the old
text is kept in metadata.previous, both versions in `fact trace`).  The same
quick-read code then agrees on every question.

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

PREFIX = "mlu-lsm"  # memorylake-usecases / listing-memory


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


FEEDBACK = ("showing", "offer")


def record_meta(r: dict) -> dict:
    # No null values: the server drops keys whose value is null.
    m = dict(key=f"lsm:{r['id']}", record=r["id"], tool=r["tool"], by=r["by"], role=r["role"], on=r["on"], kind=r["kind"])
    for k in ("field", "label", "objections", "liked", "topic"):
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
    """The pinned records by record id (MLS-0915, SHW-0822 …), metadata flattened in, plus fact id, text, created_at."""
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


PUBLIC = ("record", "tool", "by", "role", "on", "kind", "field", "label", "objections", "liked", "topic", "shown_at",
          "fid", "body", "rank", "created", "previous")


def public(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {k: r.get(k) for k in PUBLIC}


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
    return f"      #{h['rank']:<3} {h['record']:<9} {tool:<9} {h['on']:<11} {clip(h['body'], 72)}{mark}"


# --------------------------------------------------------------------------- the listing, as of a day

def state_at(mem: dict, anchor: str) -> dict[str, dict]:
    """The listing as it stood on `anchor`: for each field (price, roof, HOA) the latest change on file that day."""
    out = {}
    for r in sorted((r for r in mem.values() if r.get("kind") == "listing" and r["on"] <= anchor), key=lambda r: r["on"]):
        out[r["field"]] = r
    return out


def labels(state: dict[str, dict]) -> dict[str, str]:
    return {f: r["label"] for f, r in state.items()}


def version_label(story: dict, shown: dict[str, str]) -> str:
    return " · ".join(shown.get(f, "?") for f in story["fields"])


def shown_at(r: dict, anchor: str, mem: dict) -> tuple[dict[str, str], str]:
    """Which listing a piece of feedback was about. If the record says so itself (metadata.shown_at, written back in
    step 6), that wins; otherwise it is joined to the listing as of `anchor`. Returns (labels, how)."""
    if isinstance(r.get("shown_at"), dict) and r["shown_at"]:
        return dict(r["shown_at"]), "stated"
    return labels(state_at(mem, anchor)), "joined"


def feedback(mem: dict) -> list[dict]:
    return sorted((r for r in mem.values() if r.get("kind") in FEEDBACK), key=lambda r: (r["on"], r["record"]))


def objections(r: dict) -> list[str]:
    o = r.get("objections")
    return list(o) if isinstance(o, list) else []


def answer(story: dict, q: dict, fb: list[dict], anchor_of, mem: dict) -> dict:
    """Count what the feedback objects to, keeping only feedback about the listing as it is today, and answer.
    One function for both reads: `anchor_of(record)` is the only thing that differs (today, or the showing day)."""
    today = story["today"]
    now = labels(state_at(mem, today))
    topic = story["topics"].get(q["topic"], {})
    field = topic.get("field")
    rows, kept = [], []
    for r in fb:
        lab, how = shown_at(r, anchor_of(r), mem)
        if q["kind"] == "top":
            same = all(lab.get(f) == now.get(f) for f in story["fields"])
        elif field:
            same = lab.get(field) == now.get(field)
        else:
            same = True  # nothing about this ever changed: every showing saw the same thing
        rows.append(dict(record=r["record"], on=r["on"], shown=lab, how=how, kept=same, objections=objections(r),
                         liked=list(r.get("liked") or [])))
        if same:
            kept.append(r)
    counts: dict[str, int] = {}
    for r in kept:
        for o in objections(r):
            counts[o] = counts.get(o, 0) + 1
    top_n = max(counts.values()) if counts else 0
    top = sorted(k for k, v in counts.items() if v == top_n) if top_n else []
    n = len(kept)
    lab = lambda t: story["topics"].get(t, {}).get("label", t).lower()
    scope = ("the listing as it is today" if q["kind"] == "top" else
             f"the {story['fields'][field]} it has today ({now.get(field)})" if field else "every showing")
    out = dict(n=n, counts=counts, top=top, rows=rows, scope=scope, key=None, said="", short="")
    if n == 0 and q["kind"] == "cut":
        # Cut only on evidence at today's price; none of the feedback read was given at it.
        out.update(key="hold", said=f"hold the price — none of the feedback read was given at {now.get('price')}; "
                                    "nothing says it is too high", short="hold · price 0/0")
    elif n == 0 and q["kind"] == "still":
        out.update(key="no", said=f"no — none of the feedback read was given since the {lab(q['topic'])} changed",
                   short="no · 0/0")
    elif n == 0:
        out.update(key="none", said=f"none of the feedback read is about {scope}", short="no feedback")
    elif q["kind"] == "cut":
        p = counts.get("price", 0)
        if p and "price" in top:
            out.update(key="cut", said=f"cut again — price is the top objection ({p} of {n} buyers)",
                       short=f"cut again · price {p}/{n}")
        else:
            out.update(key="hold", said=f"hold the price — {p} of {n} buyers at {now.get('price')} objected to it; "
                                        f"top objection: {', '.join(map(lab, top)) or 'none'}",
                       short=f"hold · price {p}/{n}")
    elif q["kind"] == "still":
        k = counts.get(q["topic"], 0)
        what = lab(q["topic"])
        if k:
            out.update(key="yes", said=f"yes — {k} of {n} buyers raised the {what}", short=f"yes · {k}/{n}")
        else:
            liked = sum(1 for r in kept if q["topic"] in (r.get("liked") or []))
            out.update(key="no", said=f"no — 0 of {n} buyers{' since it changed' if field else ''} raised the {what}"
                                      + (f"; {liked} praised it" if liked else ""), short=f"no · 0/{n}")
    elif q["kind"] == "top":
        t = ", ".join(map(lab, top))
        out.update(key="+".join(top), said=f"{t} ({top_n} of {n} buyers)", short=f"{t} · {top_n}/{n}")
    return out


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, q: dict, echo: bool = True) -> dict:
    """The page's "any agent covering a showing retrieves full listing context", done the quick way: `search` the
    question, take the showing feedback in the hits, count it against the listing as it is today. Records are read
    from `fact list` by fact id (search can answer from a cache of older text)."""
    story = load_story()
    hits = search(cli, q["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    fb = [dict(by_fid[h["fid"]], rank=h["rank"]) for h in hits
          if h["fid"] in by_fid and by_fid[h["fid"]].get("kind") in FEEDBACK]
    ans = answer(story, q, fb, lambda r: story["today"], mem)
    return dict(question=q, hits=hits, feedback=[r["record"] for r in fb], cached=cached, ans=ans)


# --------------------------------------------------------------------------- read 2: joined on the showing day

def memory_read(mem: dict, q: dict) -> dict:
    """Every piece of feedback (`fact list`, every page), each joined to the listing as it was on its own day
    (metadata.on); then the same counting code."""
    story = load_story()
    return dict(ans=answer(story, q, feedback(mem), lambda r: r["on"], mem))


def today_read(mem: dict, q: dict) -> dict:
    """Control: every piece of feedback, but joined to today's listing — what the quick read does, without search."""
    story = load_story()
    return dict(ans=answer(story, q, feedback(mem), lambda r: story["today"], mem))


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


REWRITE_KEYS = ("previous", "rewritten_on", "rewritten_by", "shown_at")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on the morning Jordan takes over: feedback an earlier run rewrote is put back word for word
    from metadata.previous."""
    n = 0
    for f in list_facts(cli, pid, echo=False):
        m = f.get("metadata") or {}
        if m.get("previous") and m.get("kind") in FEEDBACK:
            meta = {k: v for k, v in m.items() if k not in REWRITE_KEYS}
            cli.run("fact", "update", f["id"], "--project", pid, "--text", m["previous"], "--metadata", json.dumps(meta),
                    scoped=True, echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run rewrote {n} record(s); put back, so this run starts from the same morning")
    return n


def versions(story: dict, mem: dict) -> list[dict]:
    """The listing's versions: one per day something changed (price, roof, HOA), with the feedback given while it
    stood."""
    days = sorted({r["on"] for r in mem.values() if r.get("kind") == "listing"})
    fb = feedback(mem)
    out = []
    for i, d in enumerate(days):
        end = days[i + 1] if i + 1 < len(days) else "9999"
        changes = sorted((r for r in mem.values() if r.get("kind") == "listing" and r["on"] == d), key=lambda r: r["record"])
        out.append(dict(v=f"V{i + 1}", since=d, label=version_label(story, labels(state_at(mem, d))),
                        changes=[dict(record=r["record"], field=r["field"], label=r["label"]) for r in changes],
                        feedback=[r["record"] for r in fb if d <= r["on"] < end]))
    return out


def intake(cli: CLI, pid: str, story: dict) -> dict:
    restored = restore_first_day(cli, pid)
    note("five tools, one project: one fact per record, word for word, then `fact update --metadata` (tool, author, "
         "role, the day, and for feedback what it objected to)")
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
        lines.append(f"    {label:<9} {len(rs):>2}   " + " · ".join(r["record"] for r in rs))
    emit("text", "\n".join(lines))
    note("the listing changed three times after it went up. The MLS, permit and HOA records say when; the feedback "
         "records do not say which version they saw")
    vs = versions(story, mem)
    lines = []
    for v in vs:
        what = ", ".join(f"{c['record']} {story['fields'][c['field']]} → {c['label']}" for c in v["changes"])
        lines.append(f"    {v['v']}  since {v['since']}  {v['label']:<40} {len(v['feedback'])} feedback")
        lines.append(f"        {what}")
    emit("text", "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["on"], r["record"]))],
         versions=vs, restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def counts_line(story: dict, counts: dict) -> str:
    if not counts:
        return "nothing"
    lab = lambda t: story["topics"].get(t, {}).get("label", t).lower()
    return " · ".join(f"{lab(k)} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def quick_review(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    now = version_label(story, labels(state_at(mem, story["today"])))
    note(f"{story['lead']['name']} preps the call with the seller: {len(story['questions'])} questions. The quick read: "
         f"`search` the question, count what the feedback in the hits objects to — against the listing as it is "
         f"today ({now})")
    out = []
    for q in story["questions"]:
        r = quick_read(cli, pid, mem, q)
        fbs = set(r["feedback"])
        lines = [f"\n  {q['id']} “{q['text']}”", f"  search · top {len(r['hits'])}:"]
        for h in r["hits"]:
            obj = by_record_objections(mem, h["record"])
            lines.append(hit_line(story, h, f"   ← feedback: {', '.join(obj) or 'no objection'}" if h["record"] in fbs else ""))
        if r["cached"]:
            lines.append(f"  · {len(r['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        lines.append(f"  · {len(fbs)} piece(s) of feedback in the hits, all counted against today's listing: "
                     f"{counts_line(story, r['ans']['counts'])}")
        lines.append(f"  → quick read: {r['ans']['said']}")
        emit("text", "\n".join(lines))
        out.append(r)
    emit("quick", "", now=now, reads=[dict(question=r["question"], ans=strip_rows(r["ans"]), feedback=r["feedback"],
                                           hits=[public(h) for h in r["hits"]], cached=r["cached"]) for r in out])
    return out


def by_record_objections(mem: dict, record: str) -> list[str]:
    return objections(mem.get(record) or {})


def strip_rows(ans: dict) -> dict:
    return {k: v for k, v in ans.items() if k != "rows"}


# --------------------------------------------------------------------------- step 5: the memory read

def memory_review(cli: CLI, pid: str, story: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every piece of feedback (`fact list`, every page), each joined to the listing as it was on "
         "the day of that showing — the same counting code")
    mem = entries(list_facts(cli, pid))
    now = labels(state_at(mem, story["today"]))
    lines = [f"\n  {'record':<9} {'shown':<11} {'listing that day':<40} objected to", "  " + "-" * 86]
    joined = []
    for r in feedback(mem):
        lab, how = shown_at(r, r["on"], mem)
        diff = [story["fields"][f] for f in story["fields"] if lab.get(f) != now.get(f)]
        joined.append(dict(record=r["record"], on=r["on"], shown=lab, objections=objections(r), differs=diff))
        lines.append(f"  {r['record']:<9} {r['on']:<11} {version_label(story, lab):<40} "
                     f"{', '.join(objections(r)) or '—'}")
    lines.append(f"\n  today's listing: {version_label(story, now)}")
    emit("text", "\n".join(lines))
    rows = []
    for r in quick:
        q = r["question"]
        m = memory_read(mem, q)
        c = today_read(mem, q)
        a = m["ans"]
        lines = [f"\n  {q['id']} {q['text']}",
                 f"    counted: {a['n']} piece(s) of feedback about {a['scope']} — {counts_line(story, a['counts'])}"]
        left = [x for x in a["rows"] if not x["kept"]]
        if left:
            lines.append(f"    left out: {len(left)} given at another version ({', '.join(x['record'] for x in left)})")
        agree = r["ans"]["key"] == a["key"]
        lines.append(f"  → quick read:  {r['ans']['said']}")
        lines.append(f"  → memory read: {a['said']}  {'✓ same' if agree else '✗ differs'}")
        lines.append(f"    (all {len(c['ans']['rows'])} pieces read against today's listing, no search: {c['ans']['said']})")
        emit("text", "\n".join(lines))
        rows.append(dict(question=q, quick=strip_rows(r["ans"]), memory=strip_rows(a), control=strip_rows(c["ans"]),
                         kept=[x["record"] for x in a["rows"] if x["kept"]], left=[x["record"] for x in left], agree=agree))
    lines = [f"\n  {'':<4} {'question':<46} {'quick read (today)':<24} {'memory read (that day)':<24}", "  " + "-" * 104]
    for x in rows:
        lines.append(f"  {x['question']['id']:<4} {clip(x['question']['text'], 45):<46} {x['quick']['short']:<24} "
                     f"{x['memory']['short']:<24} {'✓ same' if x['agree'] else '✗ differs'}")
    wrong = [x for x in rows if not x["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)} answers change with which listing the feedback is read against; "
                 f"the control column (every record, today's listing) gives the quick read's answer {sum(1 for x in rows if x['control']['key'] == x['quick']['key'])} of {len(rows)} times")
    emit("text", "\n".join(lines))
    emit("memory", "", joined=joined, now=now, rows=rows)
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def price_census(cli: CLI, pid: str, price: str, mem: dict) -> list[str]:
    """`fact list --query` is an exact, case-insensitive substring match over every record's text."""
    by_fid = {r["fid"]: r for r in mem.values()}
    items = pages(cli, ["fact", "list", "--projects", pid, "--query", price])
    return sorted((by_fid.get(f["id"]) or {}).get("record") or "?" for f in items)


def stamp(story: dict, on: str, lab: dict[str, str]) -> str:
    return f"(Shown {on}, when the listing was {lab.get('price')}, {lab.get('roof')}, HOA {lab.get('hoa')}.)"


def write_back(cli: CLI, pid: str, story: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write the join back where the next read will see it: each piece of feedback now says which listing it was "
         "about (`fact update --text`, metadata.shown_at; the old text goes to metadata.previous)")
    mem = entries(list_facts(cli, pid, echo=False))
    price_now = labels(state_at(mem, today)).get("price")
    before = price_census(cli, pid, price_now, mem)
    updates = []
    for r in feedback(mem):
        if r.get("shown_at"):
            continue
        lab = labels(state_at(mem, r["on"]))
        text = f"{r['body']} {stamp(story, r['on'], lab)}"
        meta = {k: r[k] for k in ("key", "record", "tool", "by", "role", "on", "kind", "objections", "liked") if k in r}
        meta.update(shown_at=lab, previous=r["body"], rewritten_on=today, rewritten_by=story["lead"]["name"])
        updates.append((r, text, meta))
    for i, (r, text, meta) in enumerate(updates):
        cli.run("fact", "update", r["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more record(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for r, _, _ in updates[:3]:
        lines.append(f"\n    {r['record']} was “{r['body']}”")
        lines.append(f"    {'':<{len(r['record'])}} now “{mem2[r['record']]['body']}”")
    if len(updates) > 3:
        lines.append(f"\n    … and {len(updates) - 3} more")
    emit("text", "\n".join(lines))
    after_c = price_census(cli, pid, price_now, mem2)
    lines = [f"\n  `fact list --query '{price_now}'` (exact substring, every record): before {len(before)} — "
             f"{', '.join(before) or '—'}",
             f"  {'':<{len(price_now) + 25}}after  {len(after_c)} — {', '.join(after_c) or '—'}",
             f"  · before, no piece of feedback named the price it was given at; a question that names today's price "
             f"could not find the feedback about it"]
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
        after.append(dict(question=q, feedback=r["feedback"], quick=r["ans"]["said"], memory=m["ans"]["said"],
                          agree=r["ans"]["key"] == m["ans"]["key"], tries=tries, secs=int(time.time() - t1),
                          cached=r["cached"], counts=r["ans"]["counts"], n=r["ans"]["n"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['question']['id']:<4} “{a['question']['text']}” → {len(a['feedback'])} feedback in the hits, "
                     f"{a['n']} about today's listing")
        lines.append(f"    quick read:  {a['quick']}{' (try ' + str(a['tries']) + ')' if a['tries'] > 1 else ''}")
        lines.append(f"    memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees on {agree} of {len(after)}: each piece of feedback says which listing it "
                 "was about")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=r["record"], was=r["body"], now=mem2[r["record"]]["body"], on=r["on"])
                        for r, _, _ in updates], census=dict(query=price_now, before=before, after=after_c),
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, versions

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


KINDS = {"price": "cut", "roof": "still", "hoa": "still", "kitchen": "still", "parking": "still", "now": "top"}


def ask_text(cli: CLI, pid: str, text: str, topic: str, echo: bool = True) -> dict:
    """Any question on a topic: the quick read and the memory read side by side."""
    story = load_story()
    mem = entries(list_facts(cli, pid, echo=False))
    q = dict(id="(yours)", topic=topic, kind=KINDS[topic], text=text)
    r = quick_read(cli, pid, mem, q, echo=echo)
    m = memory_read(mem, q)
    # Rewritten by step 6: what the original words gave, read against today's listing.
    raw = {k: dict(v, shown_at=None) for k, v in mem.items()}
    before = None
    if any(v.get("previous") for v in mem.values()):
        before = strip_rows(answer(story, q, feedback(raw), lambda x: story["today"], raw))
    return dict(question=q, hits=[public(h) for h in r["hits"]], feedback=r["feedback"], quick=strip_rows(r["ans"]),
                memory=strip_rows(m["ans"]), agree=r["ans"]["key"] == m["ans"]["key"], before=before,
                now=version_label(story, labels(state_at(mem, story["today"]))))


def versions_view(cli: CLI, pid: str) -> dict:
    """Every piece of feedback: the listing it was given about, against today's listing."""
    story = load_story()
    mem = entries(list_facts(cli, pid))
    now = labels(state_at(mem, story["today"]))
    rows = []
    for r in feedback(mem):
        lab, how = shown_at(r, r["on"], mem)
        diff = [story["fields"][f] for f in story["fields"] if lab.get(f) != now.get(f)]
        rows.append(dict(record=r["record"], on=r["on"], by=r["by"], shown=version_label(story, lab), how=how,
                         differs=diff, objections=objections(r)))
    return dict(now=version_label(story, now), rows=rows, versions=versions(story, mem))


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
    banner(2, TOTAL, f"Set up — one project for {story['listing']}")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records from {len(story['tools'])} tools, and four versions of one house")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — search the question, count the feedback against today's listing")
    quick = quick_review(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — each piece of feedback joined to the listing on its own day")
    rows = memory_review(cli, pid, story, quick)
    banner(6, TOTAL, "Write it back — which listing each piece was about, then the quick read again")
    wb = write_back(cli, pid, story, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   hits={r["question"]["id"]: r["feedback"] for r in quick},
                   quick={x["question"]["id"]: x["quick"]["key"] for x in rows},
                   memory={x["question"]["id"]: x["memory"]["key"] for x in rows},
                   control={x["question"]["id"]: x["control"]["key"] for x in rows},
                   agree=[x["question"]["id"] for x in rows if x["agree"]],
                   cached=[c for r in quick for c in r["cached"]],
                   after={a["question"]["id"]: [a["feedback"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "versions", "cleanup"],
                    help="run = full demo (default); ask \"QUESTION\" --topic T = the quick read and the memory read of "
                         "any question; versions = every piece of feedback, the listing it saw and today's; "
                         "cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask)")
    ap.add_argument("--topic", default="price", choices=sorted(KINDS), help="ask: what the question is about")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    story = load_story()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command == "ask" and len(a.args) != 1:
        ap.error("ask needs one question, e.g. ask \"Would a lower price bring offers?\" --topic price")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py versions`, `python3 demo.py ask \"Would a lower price bring offers?\" "
              "--topic price`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — the quick read and the memory read")
        r = ask_text(cli, pid, a.args[0], a.topic)
        fbs = set(r["feedback"])
        lines = [f"\n  “{r['question']['text']}” · {a.topic} — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← feedback" if h["record"] in fbs else "") for h in r["hits"]]
        lines.append(f"  · today's listing: {r['now']}")
        lines.append(f"  → quick read:  {r['quick']['said']}")
        lines.append(f"  → memory read: {r['memory']['said']}  {'✓ same' if r['agree'] else '✗ differs'}")
        b = r["before"]
        if b:
            lines.append(f"  · before step 6 stamped each piece of feedback with its listing, every record read against "
                         f"today's listing gave: {b['said']}")
        emit("text", "\n".join(lines))
    elif a.command == "versions":
        banner(2, 2, "Versions — every piece of feedback, the listing it saw and today's")
        v = versions_view(cli, pid)
        lines = []
        for x in v["versions"]:
            lines.append(f"  {x['v']}  since {x['since']}  {x['label']:<40} {len(x['feedback'])} feedback")
        lines += [f"\n  {'record':<9} {'shown':<11} {'listing that day':<40} {'differs from today':<22} objected to",
                  "  " + "-" * 100]
        for x in v["rows"]:
            lines.append(f"  {x['record']:<9} {x['on']:<11} {x['shown']:<40} {', '.join(x['differs']) or '—':<22} "
                         f"{', '.join(x['objections']) or '—'}{'  (stated)' if x['how'] == 'stated' else ''}")
        n = sum(1 for x in v["rows"] if x["differs"])
        lines.append(f"\n  today's listing: {v['now']}\n  · {n} of {len(v['rows'])} pieces of feedback were about a "
                     "listing that no longer exists")
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

#!/usr/bin/env python3
"""
Patient context memory for clinicians — a MemoryLake demo driven entirely by
the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Brightwater Family Practice (fictional) keeps one memory per patient: Walter
Brandt's chart lives on his own actor, every entry pinned with its date, the
clinician who signed it and its source.  His allergy list has said "no known
drug allergies" since 2018, reconfirmed at his annual visit on 2026-09-30.

On 2026-10-05 an urgent care clinic started him on cefalexin for sinusitis;
two hours later his lips swelled.  That visit's notes go into his memory, and
MemoryLake's contradiction detector flags the new allergy against the old
"no known drug allergies" line.

Today, 2026-10-12, he is back with Dr. Okafor and the AI scribe drafts an
antibiotic.  Before the order goes out it runs an exact check: `fact list
--query` for every name of the drug's class, across his whole chart.  A
cephalosporin is blocked, citing last week's entries; doxycycline is clear; and
the same check against the chart as it stood on 2026-10-04 lets the
cephalosporin through.  An exact search only matches what it is asked for, so
the check names every member of the class (the triage note says "Keflex", not
"cephalosporin"), while `search` finds things by meaning but returns its top-k.
Each decision is recorded with the ids of the entries it saw.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-pcm"  # memorylake-usecases / patient-context-memory

PROJECT = dict(
    key="clinic",
    name="Brightwater Family Practice — patients",
    description="Clinic-wide notes and order-check records. Demo data.",
)

GAP = 15            # seconds between the urgent care entries and the allergy line, so the detector checks it as it lands
DETECT_WAIT = 150   # upper bound for the detector to answer the new allergy (usually 10–30 s)
SETTLE = 30         # nothing is called "not raised" until the last write is this old
PAGE = 5            # page size for reading the chart back, so the pages are visible




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
                 "error sending request", "connection closed", "temporarily unavailable", "502", "503", "504")

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

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False):
        try:
            return self.run(*args, scoped=scoped)
        except CLIError as e:
            if e.has(*missing):
                return None
            raise


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


def who(story: dict, key: str) -> str:
    return story["clinicians"][key]["name"]


def entry_meta(e: dict, story: dict) -> dict:
    return dict(chart_key=e["key"], kind=e["kind"], entry="current" if e.get("status") else "event",
                date=e["date"], clinician=who(story, e["by"]), source=e["source"])


def all_entries(story: dict) -> list[dict]:
    return story["history"] + story["urgent_care"]["entries"]


def entry_by_text(story: dict) -> dict[str, dict]:
    return {e["text"]: e for e in all_entries(story)}


# --------------------------------------------------------------------------- pacing

class Pace:
    """Keeps the allergy line GAP seconds after the previous write, so the detector checks it as it lands."""

    def __init__(self) -> None:
        self.last = 0.0

    def wait(self) -> None:
        left = GAP - (time.time() - self.last)
        if left > 0:
            time.sleep(left)

    def wrote(self) -> None:
        self.last = time.time()


PACE = Pace()


# --------------------------------------------------------------------------- reading scopes

def pages(cli: CLI, args: list[str], size: int = 50, echo: bool = True) -> list[dict]:
    """All pages of a list command (each page is one call)."""
    items, token = [], None
    while True:
        a = list(args) + ["--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*a, scoped=True, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", flag, scope_id], echo=echo)


def list_conflicts(cli: CLI, patient_id: str, *extra: str, echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "conflict", "list", "--actor", patient_id, *extra], echo=echo)


def find(cli: CLI, kind: str, key: str, echo: bool = True):
    try:
        return cli.run(kind, "get", cid(key), "--by-custom-id", scoped=kind in ("proj", "conv"), echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def bind_actor(cli: CLI, actor_id: str) -> None:
    try:
        cli.run("actor", "bind", "--actor", actor_id, scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, key: str, name: str, description: str, tags: str) -> str:
    actor = find(cli, "actor", key)
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(key), "--display-name", name,
                        "--description", description, "--tags", tags)
        note(f"created {name} → {actor['id']}")
    else:
        note(f"{name} already exists → {actor['id']}")
    bind_actor(cli, actor["id"])
    return actor["id"]


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    proj = find(cli, "proj", PROJECT["key"])
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", cid(PROJECT["key"]),
                       "--description", PROJECT["description"], scoped=True)
        note(f"created the clinic project “{proj['name']}” → {proj['id']}")
    else:
        note(f"clinic project already exists → {proj['id']} (pass --reset to start over)")
    p, w = story["patient"], story["clinicians"]["whitaker"]
    patient = ensure_actor(cli, p["key"], p["name"], p["description"], "patient")
    whitaker = ensure_actor(cli, w["key"], w["name"], w["description"], "clinician")
    ids = dict(project=proj["id"], patient=patient, whitaker=whitaker)
    emit("text", f"\n  Clinic project   {ids['project']}  (clinic-wide records, read by every clinician)"
                 f"\n  {p['name']:<16} {ids['patient']}  (his chart: one memory per patient)"
                 f"\n  {w['name']:<16} {ids['whitaker']}  (urgent care physician, speaks in last week's dictated note)")
    emit("setup", "", ids=ids, patient=p, project=PROJECT["name"])
    return ids


# --------------------------------------------------------------------------- pinning chart entries

def forgotten_texts(cli: CLI, patient_id: str) -> set[str]:
    """Texts an earlier run's resolution forgot: they must not be pinned again on a re-run."""
    out = set()
    for c in list_conflicts(cli, patient_id, "--resolved", "true", echo=False):
        gone = set((c.get("resolve") or {}).get("forgotten_fact_ids") or [])
        if not gone:
            full = cli.run("fact", "conflict", "get", c["id"], "--actor", patient_id, scoped=True, echo=False) or {}
            gone = set((full.get("resolve") or {}).get("forgotten_fact_ids") or [])
        out |= {s.get("fact_text") for s in c.get("fact_snapshots") or [] if s.get("fact_id") in gone}
    return out


def pin_entries(cli: CLI, patient_id: str, entries: list[dict], story: dict, skip: set[str] = frozenset()) -> dict[str, dict]:
    """Pin the entries his chart does not hold yet — one `fact add` call for all of them, then
    `fact update --metadata` on each (fact add takes no metadata). Returns key → {id, new, forgotten}."""
    facts = list_facts(cli, "--actors", patient_id, echo=False)
    live = {f.get("fact"): f for f in facts}
    by_key = {(f.get("metadata") or {}).get("chart_key"): f for f in facts}
    out, missing = {}, []
    for e in entries:
        f = live.get(e["text"]) or by_key.get(e["key"])
        if f is not None:
            out[e["key"]] = dict(id=f["id"], new=False)
            if (f.get("metadata") or {}) != entry_meta(e, story):
                cli.run("fact", "update", f["id"], "--actor", patient_id, "--metadata", json.dumps(entry_meta(e, story)), scoped=True, echo=False)
        elif e["text"] in skip:
            out[e["key"]] = dict(id=None, new=False, forgotten=True)
        else:
            missing.append(e)
    if missing:
        res = cli.run("fact", "add", "--actor", patient_id, *[e["text"] for e in missing], scoped=True) or {}
        PACE.wrote()
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, e in enumerate(missing):
            fid = got.get(e["text"])
            if not fid:
                die(f"`fact add` did not return the entry “{clip(e['text'], 60)}”")
            cli.run("fact", "update", fid, "--actor", patient_id, "--metadata", json.dumps(entry_meta(e, story)),
                    scoped=True, echo=i == 0)
            out[e["key"]] = dict(id=fid, new=True)
        if len(missing) > 1:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way (date, clinician, source, kind)")
    return out


def chart_view(f: dict) -> dict:
    m = f.get("metadata") or {}
    return dict(id=f.get("id"), text=f.get("fact"), key=m.get("chart_key"), kind=m.get("kind") or "extracted",
                entry=m.get("entry") or "extracted", date=m.get("date"), clinician=m.get("clinician"),
                source=m.get("source"), updated_at=f.get("updated_at"))


# --------------------------------------------------------------------------- step 3: chart history

def read_chart_paged(cli: CLI, patient_id: str, size: int = PAGE) -> dict:
    """Read his whole chart back page by page, printing each page and its continuation token."""
    pages_, token, n = [], None, 0
    while True:
        args = ["fact", "list", "--actors", patient_id, "--page-size", str(size)] + (["--continuation-token", token] if token else [])
        page = cli.run(*args, scoped=True) or {}
        items = page.get("items") or []
        token = page.get("continuation_token")
        n += 1
        pages_.append(dict(n=n, count=len(items), token=token, items=[chart_view(f) for f in items]))
        emit("text", f"    page {n}: {len(items)} entr{'y' if len(items) == 1 else 'ies'}"
                     + (f" · continuation_token {token[:16]}…" if token else " · continuation_token null (last page)"))
        if not token or n > 40:
            break
    if pages_ and pages_[-1]["count"] == 0 and len(pages_) > 1:
        note("the last full page still came with a token; the page after it is empty — read until the token is null")
    return dict(pages=pages_, items=[i for p in pages_ for i in p["items"]])


def show_timeline(items: list[dict], header: str) -> None:
    lines = [header]
    cur = [i for i in items if i["entry"] == "current"]
    ev = sorted([i for i in items if i["entry"] == "event"], key=lambda i: i["date"] or "")
    lines.append("\n    Current status (no date in the text; signed date in metadata):")
    for i in sorted(cur, key=lambda i: i["kind"]):
        lines.append(f"      {i['kind']:<12} {clip(i['text'], 70):<72} {i['clinician']}, {i['date']}")
    lines.append("\n    Timeline (events, dated):")
    for i in ev:
        lines.append(f"      {i['date']}  {i['kind']:<12} {clip(i['text'], 96)}")
    other = [i for i in items if i["entry"] == "extracted"]
    if other:
        lines.append(f"\n    + {len(other)} fact(s) MemoryLake extracted from dictated notes")
    emit("text", "\n".join(lines))


def chart_history(cli: CLI, ids: dict, story: dict) -> dict:
    skip = forgotten_texts(cli, ids["patient"])
    note(f"{len(story['history'])} chart entries go onto Walter's own actor in one `fact add`; each then gets "
         "metadata: kind, date, signing clinician, source")
    got = pin_entries(cli, ids["patient"], story["history"], story, skip)
    new = sum(1 for g in got.values() if g.get("new"))
    gone = [k for k, g in got.items() if g.get("forgotten")]
    if not new:
        note("his chart was written by an earlier run; reading it back")
    if gone:
        note(f"{', '.join(gone)} was forgotten by an earlier run's resolution (step 5); not pinned again")
    emit("text", f"\n  `fact list --actors {ids['patient']} --page-size {PAGE}` — his chart, page by page:")
    paged = read_chart_paged(cli, ids["patient"])
    show_timeline(paged["items"], f"\n  Walter Brandt's chart ({len(paged['items'])} entries):")
    out = dict(pages=[dict(n=p["n"], count=p["count"], token=bool(p["token"])) for p in paged["pages"]],
               items=paged["items"], new=new, forgotten=gone)
    emit("chart", "", **out)
    return out


# --------------------------------------------------------------------------- step 4: last week

def conflict_view(c: dict, story: dict) -> dict:
    by_text = entry_by_text(story)
    facts = []
    for s in c.get("fact_snapshots") or []:
        e = by_text.get(s.get("fact_text")) or {}
        facts.append(dict(id=s.get("fact_id"), text=s.get("fact_text"), key=e.get("key"), kind=e.get("kind"),
                          date=e.get("date"), clinician=who(story, e["by"]) if e else None))
    return dict(id=c["id"], name=c.get("name"), description=c.get("description"), category=c.get("category"),
                conflict_type=c.get("conflict_type"), resolved=c.get("resolved"), stale=c.get("stale"),
                fact_ids=c.get("fact_ids") or [], facts=facts, created_at=c.get("created_at"), resolve=c.get("resolve"))


def show_conflicts(conflicts: list[dict], header: str) -> None:
    lines = [header]
    for c in conflicts:
        state = f"resolved ({(c['resolve'] or {}).get('strategy', '…')})" if c["resolved"] else "OPEN"
        lines.append(f"\n  [{c['category']} · {c['conflict_type']}] {c['name']}   {c['id']}  {state}")
        lines.append(f"    {clip(c['description'], 240)}")
        for f in c["facts"]:
            src = f"{f['clinician']}, {f['date']}" if f["clinician"] else "not a chart entry of this story"
            lines.append(f"    {f['id']}  ({src})")
            lines.append(f"      “{clip(f['text'], 110)}”")
    emit("text", "\n".join(lines))


def find_conv(cli: CLI, echo: bool = True):
    return find(cli, "conv", "dictation-1005", echo=echo)


def list_messages(cli: CLI, conv_id: str, echo: bool = True) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"] + (["--continuation-token", token] if token else [])
        page = cli.run(*args, echo=echo and token is None) or {}
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return sorted(msgs, key=lambda m: m.get("sequence_no") or 0)


def wait_for_cook(cli: CLI, conv_id: str, timeout: int = 600) -> int:
    t0 = last = time.time()
    first = True
    while time.time() - t0 < timeout:
        try:
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first) or {}
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            status = {}
        first = False
        if status.get("cook_finished"):
            return int(time.time() - t0)
        if time.time() - last >= 30:
            emit("progress", f"  … still turning the dictated note into memory ({int(time.time() - t0)}s)")
            last = time.time()
        time.sleep(5)
    note(f"gave up after {timeout}s; the note is still processing server-side")
    return -1


def dictation(cli: CLI, ids: dict, story: dict) -> dict:
    d = story["urgent_care"]["dictation"]
    conv = find_conv(cli)
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", cid("dictation-1005"), "--project", ids["project"],
                       "--actors", f"{ids['patient']},{ids['whitaker']}", "--kind", "DIRECT", "--name", d["title"],
                       "--metadata", "source=dictation", "--metadata", f"patient={cid(story['patient']['key'])}", scoped=True)
    done = len(list_messages(cli, conv["id"], echo=False))
    speaker = {"walter": ids["patient"], "whitaker": ids["whitaker"]}
    start = datetime.fromisoformat(d["start"].replace("Z", "+00:00"))
    n = 0
    for i, (k, words) in enumerate(d["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cli.run("conv", "msg", "append", conv["id"], "--actor", speaker[k], "--custom-id", f"msg-{i:02d}",
                "--timestamp", ts, "--text", words, scoped=True, echo=n == 0)
        n += 1
        emit("turn", "", index=i, who=k, line=words)
    if n > 1:
        note(f"… and {n - 1} more message(s) appended the same way")
    if n:
        secs = wait_for_cook(cli, conv["id"])
        if secs >= 0:
            note(f"the dictated note was turned into memory in {secs}s")
    else:
        note("the dictated note was filed by an earlier run")
    terms = [t.lower() for t in story["drug_classes"]["cephalosporin"]]
    extracted = []
    for flag, scope, label in (("--actors", ids["patient"], "Walter"), ("--projects", ids["project"], "clinic project")):
        for f in list_facts(cli, flag, scope, echo=False):
            if not (f.get("metadata") or {}).get("chart_key") and (f.get("metadata") or {}).get("kind") != "decision":
                extracted.append(dict(id=f["id"], text=f.get("fact"), scope=label,
                                      mentions=any(t in (f.get("fact") or "").lower() for t in terms)))
    lines = [f"\n  Extracted from the dictated note: {len(extracted)} fact(s), "
             f"{sum(e['mentions'] for e in extracted)} naming a cephalosporin (shown, not relied on — extraction varies run to run):"]
    for e in extracted:
        lines.append(f"    {'•' if e['mentions'] else '·'} [{e['scope']}] {clip(e['text'], 100)}")
    emit("text", "\n".join(lines))
    return dict(conv=conv["id"], appended=n, extracted=extracted)


def last_week(cli: CLI, ids: dict, story: dict) -> dict:
    uc = story["urgent_care"]
    entries = {e["key"]: e for e in uc["entries"]}
    note(f"{uc['place']}, {uc['date']}: the visit and triage notes are dated events")
    first = pin_entries(cli, ids["patient"], [entries["visit-1005"], entries["triage-1005"]], story)
    allergy = entries["allergy-ceph"]
    note(f"the allergy goes on his current allergy list — written {GAP}s after the previous write, so the "
         "detector checks it as it lands")
    if find_in_chart(cli, ids["patient"], allergy["text"]) is None:
        PACE.wait()
    got = pin_entries(cli, ids["patient"], [allergy], story)
    got.update(first)
    allergy_id = got["allergy-ceph"]["id"]
    emit("urgent_care", "", entries=[dict(key=k, id=got[k]["id"], text=entries[k]["text"], kind=entries[k]["kind"],
                                          clinician=who(story, entries[k]["by"]), date=entries[k]["date"]) for k in entries])

    def naming(cs):
        return [conflict_view(c, story) for c in cs if allergy_id in (c.get("fact_ids") or [])]

    t0 = time.time()
    fresh = got["allergy-ceph"].get("new")
    if fresh:
        note(f"waiting for MemoryLake's contradiction detector on Walter's actor (up to {DETECT_WAIT}s)")
        emit("detector_wait", "", seconds=DETECT_WAIT)
        found, first_poll = [], True
        while time.time() - t0 < DETECT_WAIT:
            time.sleep(5)
            found = naming(list_conflicts(cli, ids["patient"], echo=first_poll))
            first_poll = False
            if found and time.time() - PACE.last >= SETTLE:
                break
    else:
        note("the allergy was recorded by an earlier run; reading what the detector raised then")
        found = naming(list_conflicts(cli, ids["patient"]))
    waited = int(time.time() - t0)
    others = [conflict_view(c, story) for c in list_conflicts(cli, ids["patient"], echo=False)
              if allergy_id not in (c.get("fact_ids") or [])]
    if found:
        show_conflicts(found, f"\n  `fact conflict list --actor {ids['patient']}` — the new allergy against his chart"
                              + (f" (after {waited}s)" if fresh else "") + ":")
    else:
        emit("text", f"\n  ✗ nothing raised on the new allergy" + (f" within {waited}s" if fresh else "")
                     + " — the detector sometimes answers minutes later; check with `python3 demo.py conflicts`")
    if others:
        show_conflicts(others, f"\n  {len(others)} other conflict(s) on Walter's actor (not planned by the story — shown, not hidden):")
    det = dict(conflicts=found, others=others, waited=waited, fresh=bool(fresh), allergy_id=allergy_id)
    emit("detector", "", **det)
    note("the urgent care physician's dictated note is filed too (DIRECT conversation, clinic project)")
    dic = dictation(cli, ids, story)
    emit("dictation", "", conv=dic["conv"], extracted=dic["extracted"])
    return dict(det, dictation=dic)


def find_in_chart(cli: CLI, patient_id: str, text: str):
    return next((f for f in list_facts(cli, "--actors", patient_id, echo=False) if f.get("fact") == text), None)


# --------------------------------------------------------------------------- step 5: reconcile

SUPERSEDED = "Allergies: none recorded at intake (2018-05-14); superseded on 2026-10-05 by the cephalosporin allergy."


def reconcile(cli: CLI, ids: dict, story: dict, det: dict) -> dict:
    """Retire the stale "no known drug allergies" line without losing any record that supports the allergy.

    `keep_fact` keeps one fact and forgets every other fact in the conflict. On a two-fact conflict
    (new allergy vs NKDA) that is exactly right. On a conflict that also names the visit or triage
    note, keeping any one fact would forget the others, so it is never used there."""
    allergy_id = det["allergy_id"]
    nkda_text = next(e["text"] for e in story["history"] if e["key"] == "allergy-nkda")

    def live_ids():
        return {f["id"] for f in list_facts(cli, "--actors", ids["patient"], echo=False)}

    def is_nkda(f):
        return f["key"] == "allergy-nkda"

    open_ = [conflict_view(c, story) for c in list_conflicts(cli, ids["patient"], "--resolved", "false")]
    open_ = [c for c in open_ if allergy_id in c["fact_ids"] or any(is_nkda(f) for f in c["facts"])]
    # The clean pair first: once it has retired the NKDA line, the others name a line that is gone.
    open_.sort(key=lambda c: not (allergy_id in c["fact_ids"] and all(is_nkda(f) for f in c["facts"] if f["id"] != allergy_id)))
    done, dismissed, left = [], [], []
    if not open_:
        note("no open conflict on his allergy list (resolved by an earlier run, or the detector has not answered yet)")
    for c in open_:
        pair = allergy_id in c["fact_ids"] and all(is_nkda(f) for f in c["facts"] if f["id"] != allergy_id)
        keep = allergy_id if allergy_id in c["fact_ids"] else next((f["id"] for f in c["facts"] if not is_nkda(f)), None)
        would = [f for f in c["facts"] if f["id"] != keep]
        lines = [f"\n  {c['id']} ({len(c['facts'])} facts): `keep_fact` keeps one fact and forgets every other fact in the conflict."
                 f" Keeping “{clip(next((f['text'] for f in c['facts'] if f['id'] == keep), ''), 50)}” would forget:"]
        for f in would:
            lines.append(f"    - “{clip(f['text'], 90)}”  ← {'the stale status line' if is_nkda(f) else 'a record that SUPPORTS the allergy'}")
        emit("text", "\n".join(lines))
        if pair:
            r = cli.run("fact", "conflict", "resolve", c["id"], "--actor", ids["patient"], "--strategy", "keep_fact",
                        "--keep-fact-id", allergy_id, scoped=True) or {}
            note(f"two facts, one of them stale: resolved with {r.get('strategy')}, forgotten {', '.join(r.get('forgotten_fact_ids') or []) or 'nothing'}")
            done.append(dict(id=c["id"], name=c["name"], strategy=r.get("strategy"), kept=allergy_id, forgotten=r.get("forgotten_fact_ids") or []))
            continue
        supporting = [f for f in would if not is_nkda(f)]
        gone = [f for f in c["facts"] if is_nkda(f) and f["id"] not in live_ids()]
        if gone:
            cli.run("fact", "conflict", "resolve", c["id"], "--actor", ids["patient"], "--strategy", "dismiss", scoped=True)
            why = "keep_fact would forget a record that supports the allergy; " if supporting else ""
            note(f"not keep_fact: {why}the NKDA line it names is already retired, so it is dismissed (dismiss changes no fact)")
            dismissed.append(dict(id=c["id"], name=c["name"], would_forget=[f["id"] for f in supporting]))
        else:
            left.append(dict(id=c["id"], name=c["name"], would_forget=[f["id"] for f in would if not is_nkda(f)]))
    # No clean pair raised: retire the NKDA line by rewriting it (`fact trace` keeps the old text), then
    # dismiss what is left — the contradiction those conflicts name no longer exists.
    nkda = next((f for f in list_facts(cli, "--actors", ids["patient"], echo=False) if f.get("fact") == nkda_text), None)
    rewritten = None
    if nkda is not None and (left or not det["conflicts"]):
        note("no two-fact conflict retired the NKDA line, so it is rewritten in place (`fact update --text`; the old text stays in `fact trace`)")
        cli.run("fact", "update", nkda["id"], "--actor", ids["patient"], "--text", SUPERSEDED, scoped=True)
        rewritten = nkda["id"]
        for c in left:
            cli.run("fact", "conflict", "resolve", c["id"], "--actor", ids["patient"], "--strategy", "dismiss", scoped=True)
            dismissed.append(dict(c, after="rewrite"))
        left = []
    # The audit trail: the resolved conflict keeps the forgotten line word for word; `fact trace` shows its history.
    resolved = [conflict_view(cli.run("fact", "conflict", "get", c["id"], "--actor", ids["patient"], scoped=True), story)
                for c in list_conflicts(cli, ids["patient"], "--resolved", "true", echo=False)
                if allergy_id in (c.get("fact_ids") or [])]
    trail = []
    for c in resolved:
        rs = c["resolve"] or {}
        for f in c["facts"]:
            if f["id"] in (rs.get("forgotten_fact_ids") or []):
                tr = cli.run("fact", "trace", f["id"], "--actor", ids["patient"], scoped=True) or {}
                hist = tr.get("trace") or []
                trail.append(dict(id=f["id"], text=f["text"], conflict=c["id"], resolved_at=rs.get("created_at"),
                                  history=[dict(action=h.get("action") or h.get("event") or h.get("operation"),
                                                source=h.get("source_kind"), at=h.get("timestamp")) for h in hist]))
    if rewritten:
        tr = cli.run("fact", "trace", rewritten, "--actor", ids["patient"], scoped=True) or {}
        trail.append(dict(id=rewritten, text=nkda_text, conflict=None, resolved_at=None,
                          history=[dict(action=h.get("action"), source=h.get("source_kind"), at=h.get("timestamp")) for h in tr.get("trace") or []]))
    allergies = [chart_view(f) for f in list_facts(cli, "--actors", ids["patient"])
                 if (f.get("metadata") or {}).get("kind") == "allergy"]
    lines = []
    for t in trail:
        if t["conflict"]:
            lines.append(f"\n  Forgotten, not erased — `fact conflict get {t['conflict']}` keeps it word for word:")
            lines.append(f"    “{t['text']}”  (resolved {str(t['resolved_at'])[:19]}Z)")
        else:
            lines.append(f"\n  Rewritten, not erased — the old text was: “{t['text']}”")
        lines.append(f"    `fact trace {t['id']}`: " + " → ".join(f"{h['action']} ({h['source']})" for h in reversed(t["history"])))
    lines.append(f"\n  His allergy list now ({len(allergies)} current line{'s' if len(allergies) != 1 else ''}):")
    for a in allergies:
        lines.append(f"    {a['text']}  — {a['clinician']}, {a['date']}, {a['source']}")
    if dismissed:
        lines.append(f"\n  Dismissed {len(dismissed)} conflict(s) instead of keep_fact: " + "; ".join(d["id"] for d in dismissed))
    emit("text", "\n".join(lines))
    out = dict(resolved=done, dismissed=dismissed, left_open=left, rewritten=rewritten, trail=trail, allergies=allergies)
    emit("reconciled", "", **out)
    return out


# --------------------------------------------------------------------------- step 6: the order check

NEGATED = re.compile(r"\b(no|never had an?|without)\s+(\w+\s+){0,3}?(reaction|allerg\w*)", re.I)


def is_reaction(text: str, story: dict) -> bool:
    t = NEGATED.sub(" ", text or "").lower()
    return any(w in t for w in story["reaction_words"])


def query_chart(cli: CLI, ids: dict, term: str, echo: bool = True) -> list[dict]:
    """`fact list --query` over his actor and the clinic project: exact, case-insensitive substring, every page."""
    out = []
    for f in pages(cli, ["fact", "list", "--actors", ids["patient"], "--projects", ids["project"], "--query", term], echo=echo):
        v = chart_view(f)
        if v["kind"] == "decision":
            continue
        v["owner"] = "Walter" if ids["patient"] in json.dumps(f.get("owner")) else "clinic project"
        out.append(v)
    return out


def check_order(cli: CLI, ids: dict, story: dict, order: dict, echo: bool = True) -> dict:
    terms = story["drug_classes"][order["class"]]
    hits: dict[str, dict] = {}
    by_term = {}
    for t in terms:
        found = query_chart(cli, ids, t, echo=echo)
        by_term[t] = [h["id"] for h in found]
        for h in found:
            hits.setdefault(h["id"], dict(h, terms=[]))["terms"].append(t)
    rows = sorted(hits.values(), key=lambda h: (h["date"] or "9999", h["text"] or ""))
    for h in rows:
        h["evidence"] = h["kind"] == "allergy" or is_reaction(h["text"], story)
    return dict(order=order, terms=terms, by_term=by_term, hits=rows, **verdict_of(rows))


def verdict_of(rows: list[dict], cutoff: str | None = None) -> dict:
    seen = [h for h in rows if cutoff is None or (h["date"] and h["date"] <= cutoff)]
    ev = [h for h in seen if h["evidence"]]
    v = "✗" if ev else "!" if seen else "✓"
    return dict(verdict=v, seen=[h["id"] for h in seen], evidence=[h["id"] for h in ev])


VERDICT = {"✗": "blocked — a recorded reaction to this drug class", "!": "review — the class is mentioned, no reaction recorded",
           "✓": "clear — no record mentions this drug class"}


def show_check(r: dict, cutoff: str | None = None) -> None:
    o = r["order"]
    v = verdict_of(r["hits"], cutoff) if cutoff else r
    lines = [f"\n  {v['verdict']}  {o['drug']} {o['dose']}  ({o['class']}) → {VERDICT[v['verdict']]}"]
    shown = [h for h in r["hits"] if h["id"] in v["seen"]]
    for h in shown:
        src = f"{h['clinician']}, {h['date']}" if h["clinician"] else "extracted from a dictated note"
        lines.append(f"     {'✗' if h['evidence'] else '·'} {clip(h['text'], 92)}")
        lines.append(f"       {src} · matched “{'”, “'.join(h['terms'])}” · {h['id']}")
    if not shown:
        lines.append(f"     no entry matches any of: {', '.join(r['terms'])}")
    emit("text", "\n".join(lines))


def order_check(cli: CLI, ids: dict, story: dict) -> dict:
    v = story["visit"]
    note(f"{story['today']}, {who(story, v['by'])}: {v['reason']}. The AI scribe drafts {len(v['orders'])} orders; "
         "each one is checked with `fact list --query` for every name of its drug class, across his actor and the clinic project")
    results = []
    for o in v["orders"]:
        emit("text", f"\n  {o['drug']} — {o['class']}: {len(story['drug_classes'][o['class']])} names to look for")
        r = check_order(cli, ids, story, o)
        show_check(r)
        results.append(r)
    # Provenance of the deciding entry: who signed it, when, from what.
    blocked = next((r for r in results if r["verdict"] == "✗"), None)
    prov = None
    if blocked:
        first = next(h for h in blocked["hits"] if h["evidence"] and h["kind"] == "allergy") if any(
            h["evidence"] and h["kind"] == "allergy" for h in blocked["hits"]) else next(h for h in blocked["hits"] if h["evidence"])
        f = cli.run("fact", "get", first["id"], "--actor", ids["patient"], scoped=True) or {}
        m = f.get("metadata") or {}
        prov = dict(id=f.get("id"), text=f.get("fact"), clinician=m.get("clinician"), date=m.get("date"), source=m.get("source"),
                    created_at=f.get("created_at"), updated_at=f.get("updated_at"))
        emit("text", f"\n  Why it is blocked (`fact get`): “{prov['text']}”\n"
                     f"    signed by {prov['clinician']} on {prov['date']} · source: {prov['source']} · stored {str(prov['created_at'])[:19]}Z")
    cutoff = "2026-10-04"
    emit("text", f"\n  Control — the same results, keeping only entries dated on or before {cutoff} (his chart before urgent care):")
    control = []
    for r in results:
        show_check(r, cutoff)
        control.append(dict(drug=r["order"]["drug"], **verdict_of(r["hits"], cutoff)))
    note("without last week's entries the cephalosporin goes through: what the scribe sees is what the chart holds")
    out = dict(results=results, provenance=prov, control=control, cutoff=cutoff)
    emit("order_check", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: exact vs semantic

def exact_vs_semantic(cli: CLI, ids: dict, story: dict, check: dict) -> dict:
    r = next(x for x in check["results"] if x["order"]["class"] == "cephalosporin")
    ev = {h["id"]: h for h in r["hits"] if h["evidence"]}
    label = {h["id"]: (h["key"] or "dictated note (extracted)") for h in r["hits"]}
    lines = ["\n  `fact list --query` is exact: each name finds only the entries that contain it.",
             f"    {'name':<14} entries found"]
    for t in r["terms"]:
        ids_ = r["by_term"][t]
        names = [label.get(i, i) for i in ids_]
        shown = [n for n in dict.fromkeys(names)]
        lines.append(f"    {t:<14} {len(ids_)}  " + ", ".join(f"{n} ×{names.count(n)}" if names.count(n) > 1 else n for n in shown))
    only = {}
    for i in ev:
        ts = [t for t in r["terms"] if i in r["by_term"][t]]
        if len(ts) == 1:
            only[i] = ts[0]
    for i, t in only.items():
        lines.append(f"    → “{clip(ev[i]['text'], 70)}” is found only by “{t}”")
    cls = r["by_term"].get("cephalosporin", [])
    lines.append(f"    The class name alone finds {sum(1 for i in ev if i in cls)} of {len(ev)} reaction records; all {len(r['terms'])} names find {len(ev)}.")
    emit("text", "\n".join(lines))
    q = story["semantic_question"]
    res = cli.run("search", q, "--actors", ids["patient"], "--projects", ids["project"], "--types", "fact", "--top-k", "5", scoped=True) or {}
    hits = []
    for f in res.get("facts") or []:
        fid = f.get("id")
        hits.append(dict(id=fid, text=f.get("fact") or f.get("content"), evidence=fid in ev, label=label.get(fid)))
    got = sum(1 for h in hits if h["evidence"])
    lines = [f"\n  `search` is semantic: “{q}” (top 5)"]
    for i, h in enumerate(hits, 1):
        lines.append(f"    {i}. {'✓' if h['evidence'] else '·'} {clip(h['text'], 100)}")
    missed = [ev[i] for i in ev if i not in {h['id'] for h in hits}]
    lines.append(f"    {got} of {len(ev)} reaction records in the top 5; {len(hits) - got} other hit(s)"
                 + (f"; missed: " + "; ".join(clip(m['text'], 50) for m in missed) if missed else ""))
    lines.append("\n  The order check uses the exact list (every page, nothing ranked away); search is for questions nobody wrote a name list for.")
    emit("text", "\n".join(lines))
    out = dict(terms=[dict(term=t, ids=r["by_term"][t], labels=[label.get(i, i) for i in r["by_term"][t]]) for t in r["terms"]],
               only=[dict(id=i, term=t, text=ev[i]["text"]) for i, t in only.items()], evidence=len(ev),
               class_only=sum(1 for i in ev if i in cls), question=q, search=hits, search_found=got)
    emit("exact_vs_semantic", "", **out)
    return out


# --------------------------------------------------------------------------- step 8: decision record

def record_text(story: dict, r: dict) -> str:
    o = r["order"]
    what = {"✗": f"blocked, recorded reaction to a {o['class']}", "!": f"needs review, a {o['class']} is mentioned",
            "✓": f"allowed, no record mentions a {o['class']}"}[r["verdict"]]
    return f"Order check {story['today']} (AI scribe for {who(story, story['visit']['by'])}): {o['drug']} {o['dose']} - {what}."


def decision_record(cli: CLI, ids: dict, story: dict, check: dict) -> dict:
    note("each decision is pinned on the clinic project with the ids and versions (updated_at) of the entries it saw")
    live = {f.get("fact"): f for f in list_facts(cli, "--projects", ids["project"], echo=False)}
    records = []
    for r in check["results"]:
        text = record_text(story, r)
        seen = [dict(id=h["id"], updated_at=h["updated_at"], owner=h["owner"]) for h in r["hits"]]
        meta = dict(kind="decision", order=r["order"]["drug"], verdict=r["verdict"], terms=len(r["terms"]),
                    checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), seen=seen, evidence=r["evidence"])
        f = live.get(text)
        if f is None:
            res = cli.run("fact", "add", "--project", ids["project"], text, scoped=True) or {}
            fid = ((res.get("facts") or [{}])[0]).get("id")
            cli.run("fact", "update", fid, "--project", ids["project"], "--metadata", json.dumps(meta), scoped=True)
        else:
            fid = f["id"]
            meta = f.get("metadata") or meta
            note(f"the {r['order']['drug']} decision was recorded by an earlier run ({meta.get('checked_at')})")
        records.append(dict(id=fid, text=text, meta=meta))
    out = verify_records(cli, ids, records)
    path = write_record(story, ids, check, out)
    out["file"] = os.path.relpath(path)
    emit("text", f"\n  Decision record written to {out['file']}")
    emit("record", "", **out)
    return out


def verify_records(cli: CLI, ids: dict, records: list[dict], echo: bool = True) -> dict:
    """Re-read every entry a decision saw (`fact get`) and compare its version with the recorded one."""
    lines, rows = [], []
    for rec in records:
        m = rec["meta"]
        lines.append(f"\n  {rec['id']}  {m.get('verdict')} {m.get('order')}  (checked {m.get('checked_at')})")
        lines.append(f"    “{clip(rec['text'], 110)}”")
        for i, s in enumerate(m.get("seen") or []):
            scope = ["--actor", ids["patient"]] if s.get("owner") == "Walter" else ["--project", ids["project"]]
            try:
                f = cli.run("fact", "get", s["id"], *scope, scoped=True, echo=echo and i == 0) or {}
            except CLIError as e:
                if not e.has("404", "not found"):
                    raise
                f = {}
            state = "gone" if not f else "unchanged" if f.get("updated_at") == s.get("updated_at") else "changed since"
            rows.append(dict(record=rec["id"], id=s["id"], state=state, text=f.get("fact")))
            lines.append(f"    {'✓' if state == 'unchanged' else '✗'} {state:<13} {s['id']}  {clip(f.get('fact') or '', 70)}")
        if not m.get("seen"):
            lines.append("    (it saw no entry: nothing in the chart named the drug class)")
    emit("text", "\n".join(lines))
    return dict(records=records, rows=rows, unchanged=sum(r["state"] == "unchanged" for r in rows), total=len(rows))


def write_record(story: dict, ids: dict, check: dict, out: dict) -> Path:
    p = story["patient"]
    lines = [f"# Order check — {p['name']}, {story['today']}", "",
             f"{story['clinic']} · {who(story, story['visit']['by'])} · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
             f"from MemoryLake (patient actor `{ids['patient']}`, clinic project `{ids['project']}`).", ""]
    for r in check["results"]:
        o = r["order"]
        lines += [f"## {r['verdict']} {o['drug']} {o['dose']} ({o['class']})", "", f"{VERDICT[r['verdict']]}.", "",
                  "Searched with `fact list --query` for: " + ", ".join(f"`{t}`" for t in r["terms"]), ""]
        for h in r["hits"]:
            src = f"{h['clinician']}, {h['date']}, {h['source']}" if h["clinician"] else "extracted from a dictated note"
            lines.append(f"- {'**reaction** — ' if h['evidence'] else ''}{h['text']} _({src}; `{h['id']}` @ {h['updated_at']})_")
        if not r["hits"]:
            lines.append("- no entry names this class")
        lines.append("")
    lines += ["## Re-verified", "", f"{out['unchanged']} of {out['total']} entries the decisions saw are unchanged.", ""]
    OUT.mkdir(exist_ok=True)
    path = OUT / f"order-check-{p['key']}-{story['today']}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_records(cli: CLI, ids: dict, echo: bool = True) -> list[dict]:
    return [dict(id=f["id"], text=f.get("fact"), meta=f.get("metadata") or {})
            for f in list_facts(cli, "--projects", ids["project"], echo=echo) if (f.get("metadata") or {}).get("kind") == "decision"]


# --------------------------------------------------------------------------- ask: exact and semantic, side by side

def ask(cli: CLI, ids: dict, text: str, echo: bool = True) -> dict:
    exact = query_chart(cli, ids, text, echo=echo)
    res = cli.run("search", text, "--actors", ids["patient"], "--projects", ids["project"], "--types", "fact",
                  "--top-k", "5", scoped=True, echo=echo) or {}
    exact_ids, exact_texts = {h["id"] for h in exact}, {h["text"] for h in exact}
    sem = []
    for f in res.get("facts") or []:
        words = f.get("fact") or f.get("content")
        # Same words under another id would be a second copy of the entry: say so instead of guessing.
        sem.append(dict(id=f.get("id"), text=words, in_exact=f.get("id") in exact_ids,
                        same_text=f.get("id") not in exact_ids and words in exact_texts))
    return dict(text=text, exact=exact, semantic=sem)


def show_ask(r: dict) -> None:
    lines = [f"\n  `fact list --query “{r['text']}”` — every entry containing it ({len(r['exact'])}):"]
    lines += [f"    · {clip(h['text'], 100)}  ({h['clinician'] or 'extracted'}, {h['date'] or '—'})" for h in r["exact"]] or ["    (none)"]
    lines.append(f"\n  `search “{r['text']}”` — the 5 closest by meaning:")
    lines += [f"    {i}. {'=' if h['in_exact'] else '~' if h['same_text'] else '+'} {clip(h['text'], 100)}"
              + (f"  ({h['id']}: same text as an exact hit, different id)" if h["same_text"] else "")
              for i, h in enumerate(r["semantic"], 1)] or ["    (none)"]
    lines.append("    (= also in the exact list · + found by meaning only)")
    emit("text", "\n".join(lines))


# --------------------------------------------------------------------------- cleanup

def delete_actor(cli: CLI, actor_id: str) -> int:
    """Delete an actor's facts, then the actor; returns how many facts were deleted.

    `actor delete` does not take the actor's facts with it: they stay readable and searchable, and
    `fact delete` then fails with ACTOR_INACTIVE, so they can never be removed. Every fact goes
    first, the list must come back empty, then the actor. A sealed (unbound) actor answers 404 to
    `fact list`, so it is bound back first."""
    def facts() -> list:
        out, token = [], None
        while True:
            args = ["fact", "list", "--actors", actor_id, "--page-size", "50"]
            if token:
                args += ["--continuation-token", token]
            page = cli.run(*args, scoped=True, echo=False) or {}
            out.extend(page.get("items") or [])
            token = page.get("continuation_token")
            if not token:
                return out

    try:
        found = facts()
    except CLIError as e:
        if not e.has("404", "not found"):
            raise
        cli.run("actor", "bind", "--actor", actor_id, scoped=True, echo=False)
        # The binding takes a moment to show: `fact list` can still answer 404 right after it.
        for wait in (1, 2, 4, 8, 0):
            try:
                found = facts()
                break
            except CLIError as e:
                if not e.has("404", "not found") or not wait:
                    raise
                time.sleep(wait)
    for f in found:
        cli.run("fact", "delete", "--actor", actor_id, f["id"], scoped=True, echo=False)
    left = facts()
    if left:
        die(f"{len(left)} fact(s) are still on {actor_id}; not deleting it (its memory would become undeletable)")
    cli.run("actor", "delete", actor_id)
    if found:
        note(f"deleted {len(found)} fact(s) on {actor_id} before the actor")
    return len(found)


def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    conv = find_conv(cli, echo=not quiet)
    if conv:
        cli.run("conv", "delete", conv["id"], scoped=True)   # `proj delete` does not delete conversations
    proj = find(cli, "proj", PROJECT["key"], echo=not quiet)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
    for key in (story["patient"]["key"], story["clinicians"]["whitaker"]["key"]):
        actor = find(cli, "actor", key, echo=not quiet)
        if actor:
            delete_actor(cli, actor["id"])
    note("deleted the dictated note, the clinic project, Walter's chart and the urgent care physician")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 8


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — a clinic project, one actor per patient")
    ids = setup(cli, story, reset)
    cli.ids = ids  # type: ignore[attr-defined]  (the web app reads the live ids from here)
    banner(3, TOTAL, "Chart history — dated events and current status, signed and sourced, read back page by page")
    chart = chart_history(cli, ids, story)
    banner(4, TOTAL, f"Last week — urgent care records a new allergy ({story['urgent_care']['date']})")
    lw = last_week(cli, ids, story)
    banner(5, TOTAL, "Reconcile — the stale allergy line, and what keep_fact would forget")
    rec = reconcile(cli, ids, story, lw)
    banner(6, TOTAL, f"Today's order check — the AI scribe's draft against his whole chart ({story['today']})")
    check = order_check(cli, ids, story)
    banner(7, TOTAL, "Exact vs semantic — fact list --query and search, on the same chart")
    evs = exact_vs_semantic(cli, ids, story, check)
    banner(8, TOTAL, "Decision record — what the scribe saw, re-verified")
    record = decision_record(cli, ids, story, check)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   chart=len(chart["items"]), pages=[p["count"] for p in chart["pages"]],
                   detector=dict(fresh=lw["fresh"], waited=lw["waited"], raised=len(lw["conflicts"]), others=len(lw["others"]),
                                 facts=[len(c["facts"]) for c in lw["conflicts"]]),
                   extracted=len(lw["dictation"]["extracted"]), extracted_ceph=sum(e["mentions"] for e in lw["dictation"]["extracted"]),
                   resolved=len(rec["resolved"]), left_open=len(rec["left_open"]),
                   verdicts={r["order"]["drug"]: r["verdict"] for r in check["results"]},
                   control={c["drug"]: c["verdict"] for c in check["control"]},
                   hits={r["order"]["drug"]: len(r["hits"]) for r in check["results"]},
                   only=[o["term"] for o in evs["only"]], class_only=f"{evs['class_only']}/{evs['evidence']}",
                   search=f"{evs['search_found']}/{evs['evidence']}", verified=f"{record['unchanged']}/{record['total']}")
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def need_ids(cli: CLI) -> dict:
    story = load_story()
    proj = find(cli, "proj", PROJECT["key"], echo=False)
    patient = find(cli, "actor", story["patient"]["key"], echo=False)
    if proj is None or patient is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], patient=patient["id"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "chart", "check", "ask", "conflicts", "records", "cleanup"],
                    help="run = full demo (default); chart = his chart, page by page; check DRUG = run the order check for a drug "
                         "named in data/story.json; ask \"TEXT\" = exact list and semantic search side by side; conflicts = every "
                         "conflict on his actor; records = re-verify the recorded decisions; cleanup = delete everything")
    ap.add_argument("arg", nargs="?", help="drug for `check`, text for `ask`")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if args.command in ("check", "ask") and not args.arg:
        ap.error("check needs a drug (e.g. cefuroxime); ask needs text (e.g. \"Keflex\")")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "run":
        run_pipeline(cli, reset=args.reset)
        print("\nDone. Try `python3 demo.py check cefuroxime`, `python3 demo.py ask \"Keflex\"` or `python3 demo.py records`; "
              "`python3 demo.py cleanup` removes the demo data.")
        return
    ids = need_ids(cli)
    if args.command == "chart":
        banner(2, 2, "Walter Brandt's chart, page by page")
        paged = read_chart_paged(cli, ids["patient"])
        show_timeline(paged["items"], f"\n  {len(paged['items'])} entries:")
    elif args.command == "check":
        orders = {o["drug"].lower(): o for o in story["visit"]["orders"]}
        o = orders.get(args.arg.lower())
        if o is None:
            die(f"unknown drug {args.arg!r}; data/story.json names: {', '.join(orders)}")
        banner(2, 2, f"Order check — {o['drug']} against his chart, now")
        show_check(check_order(cli, ids, story, o))
    elif args.command == "ask":
        banner(2, 2, f"Ask — exact and semantic, side by side")
        show_ask(ask(cli, ids, args.arg))
    elif args.command == "conflicts":
        banner(2, 2, "Every conflict on Walter's actor")
        items = [conflict_view(c, story) for c in list_conflicts(cli, ids["patient"])]
        show_conflicts(items, f"\n  {len(items)} conflict(s):") if items else emit("text", "\n  No conflicts on Walter's actor.")
    elif args.command == "records":
        banner(2, 2, "Recorded decisions, re-verified against his chart now")
        recs = read_records(cli, ids)
        verify_records(cli, ids, recs) if recs else emit("text", "\n  No decisions recorded yet.")


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

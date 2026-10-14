#!/usr/bin/env python3
"""
Patient intake memory for small clinics — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Cedar Row Family Clinic (fictional) keeps one memory per patient chart (an actor per chart, every intake form,
nurse note and front desk note pinned word for word) and a front desk registry (one MemoryLake project, one fact
per registration). It is October 15. Lena Okoro checks in for an annual physical with fasting labs. She is new,
the front desk says, and registers her as P-1088.

She is not new. In 2024 and 2025 she came as Lena Marsh, P-0412, and that chart says she fainted during a seated
blood draw (draw lying down), has a latex sensitivity (nitrile gloves), and works nights (first slot of the day,
texts not calls). Today's chart says "Allergies listed on the form: none".

The check-in read is what the page calls intake memory that loads automatically: `search` the question in the
chart she was checked in under. It answers from P-1088 alone. The obvious repair, reading every chart that the
registry finds for her name, pulls in a different Lena Marsh (P-0977, born 1986: seated draw, penicillin, afternoon
slots) and Marcus Okoro, whose registration lists her as his emergency contact.

The identity read takes the identifiers on today's registration — date of birth, mobile, email — and looks each one
up in the registry (`fact list --query`, an exact substring, every page). Each one alone matches someone else: Tomas
Reyes shares her date of birth, Marcus Okoro's registration carries her mobile. The rule is date of birth AND
(mobile OR email); only P-0412 meets it.

Step 6 merges the duplicate chart into the original one: today's two records go onto P-0412 (`fact add --actor`,
with where they came from in metadata), the chart is renamed, the registry entry gets the new name, and the
duplicate actor is deleted (its facts first). The same check-in read, on the merged chart, then answers three new
questions from her own records, and the other Lena Marsh's chart is exactly as it was.

Everything here is a plain CLI command, echoed as it runs.  The companion web app (web/server.py) drives the same
functions and streams the same events.

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

PREFIX = "mlu-pim"  # memorylake-usecases / patient-intake-memory


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


def chart_cid(patient: str) -> str:
    return cid("chart-" + patient.lower())


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


MERGE_KEYS = ("merged_into", "aliases")


def record_meta(r: dict) -> dict:
    """What a chart record carries: which chart it was written on, who wrote it, when, what it is about."""
    return dict(key=f"pim:{r['id']}", record=r["id"], patient=r["patient"], source=r["source"], by=r["by"], on=r["on"],
                topic=r["topic"], value=r["value"])


def registry_meta(pid: str, p: dict) -> dict:
    return dict(key=f"pim:reg:{pid}", record=pid, kind="registry", name=p["name"], dob=p["dob"], phone=p["phone"],
                email=p["email"], registered=p["registered"])


def who(story: dict, pid: str) -> str:
    p = story["patients"].get(pid)
    return f"{p['name']} ({pid})" if p else pid


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


def list_facts(cli: CLI, scope: list[str], echo: bool = True) -> list[dict]:
    """`scope` is ["--projects", id] or ["--actors", id]."""
    return pages(cli, ["fact", "list", *scope], echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """Pinned records by record id (NN-0412, P-0412 …), metadata flattened in, plus fact id and text."""
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


def find_actor(cli: CLI, patient: str, echo: bool = False):
    try:
        return cli.run("actor", "get", chart_cid(patient), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def chart_name(story: dict, pid: str) -> str:
    return f"{story['patients'][pid]['name']} ({pid})"


def ensure_chart(cli: CLI, story: dict, pid: str, echo: bool = True) -> tuple[str, bool]:
    a = find_actor(cli, pid)
    made = False
    if a is None:
        a = cli.run("actor", "create", "--custom-id", chart_cid(pid), "--display-name", chart_name(story, pid),
                    "--description", f"{story['clinic']} patient chart {pid}. Demo data.", echo=echo)
        made = True
    try:
        cli.run("actor", "bind", "--actor", a["id"], scoped=True, echo=False)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return a["id"], made


def public(r: dict) -> dict:
    return {k: r.get(k) for k in ("record", "patient", "source", "by", "on", "topic", "value", "fid", "body", "rank",
                                  "merged_from")}


# --------------------------------------------------------------------------- search

def search(cli: CLI, q: str, scope: list[str], top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", q, *scope, "--types", "fact", "--top-k", str(top_k), scoped=True, echo=echo) or {}
    out = []
    for i, f in enumerate(res.get("facts") or [], 1):
        m = f.get("metadata") or {}
        out.append(dict(m, rank=f.get("rank") or i, fid=f.get("id"), body=f.get("fact") or "",
                        record=m.get("record") or "?", topic=m.get("topic") or "?", patient=m.get("patient") or "?"))
    return out


def hit_line(story: dict, h: dict, mark: str = "") -> str:
    src = story["sources"].get(h.get("source"), h.get("source") or "?")
    return f"      #{h['rank']:<3} {h['record']:<8} {src:<16} {h.get('topic', '?'):<11} {clip(h['body'], 70)}{mark}"


# --------------------------------------------------------------------------- read 1: the check-in read

def checkin_read(cli: CLI, actor_id: str, chart: dict, q: dict, echo: bool = True) -> dict:
    """The page's "intake memory that loads automatically", done the way the front desk does it: search the question in
    the chart the patient was checked in under and take the first hit on the question's topic. Hits are read back from
    `fact list` by fact id (search can answer from a cache of older text)."""
    hits = search(cli, q["text"], ["--actors", actor_id], echo=echo)
    by_fid = {r["fid"]: r for r in chart.values()}
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    on_topic = [dict(by_fid.get(h["fid"], h), rank=h["rank"]) for h in hits if by_fid.get(h["fid"], h).get("topic") == q["topic"]]
    first = on_topic[0] if on_topic else None
    return dict(q=q, hits=hits, on_topic=on_topic, used=first["record"] if first else "",
                used_body=first["body"] if first else "", cached=cached)


def name_hits(cli: CLI, reg_pid: str, names: list[str], echo: bool = True) -> dict[str, list[str]]:
    """Every registration the registry finds for each name: `fact list --query`, an exact substring, every page —
    what typing the name into the front desk search box returns."""
    return {n: sorted({(f.get("metadata") or {}).get("record") for f in
                       list_facts(cli, ["--projects", reg_pid, "--query", n], echo=echo)} - {None}) for n in names}


def topic_rows(charts: dict[str, dict], pids: list[str], topic: str) -> list[dict]:
    rows = [dict(r, chart=pid) for pid in pids for r in charts.get(pid, {}).values() if r.get("topic") == topic]
    return sorted(rows, key=lambda r: (r["on"], r["record"]))


def person_of(r: dict) -> str:
    """Whose record it is: the chart it was first written on (a merged copy keeps where it came from)."""
    return r.get("patient") or r.get("chart") or "?"


# --------------------------------------------------------------------------- read 2: who is this?

def identity(cli: CLI, reg_pid: str, story: dict, reg: dict, pid: str, echo: bool = True) -> dict:
    """Look up each identifier on the patient's registration in the registry (`fact list --query`, an exact substring,
    every page) and apply the rule: date of birth AND (mobile OR email). Names are looked up too, for comparison only."""
    me = reg[pid]
    ids = dict(dob=me["dob"], phone=me["phone"], email=me["email"])
    found = {k: sorted({(f.get("metadata") or {}).get("record") for f in
                        list_facts(cli, ["--projects", reg_pid, "--query", v], echo=echo)} - {None, pid})
             for k, v in ids.items()}
    p = story["patients"][pid]
    names = [p["name"]] + ([p["previous_name"]] if p.get("previous_name") else [])
    by_name = name_hits(cli, reg_pid, names, echo=echo)
    named = sorted({r for rs in by_name.values() for r in rs} - {pid})
    rows = []
    for other in sorted(set(named) | {r for rs in found.values() for r in rs}):
        hit = {k: other in found[k] for k in ids}
        same = hit["dob"] and (hit["phone"] or hit["email"])
        what = [k for k in ("dob", "phone", "email") if hit[k]]
        why = ("date of birth and " + " and ".join({"phone": "mobile", "email": "email"}[k] for k in what if k != "dob")
               if same else
               "date of birth only — no mobile or email in common" if what == ["dob"] else
               ("its text carries the mobile" + ("" if (reg.get(other) or {}).get("phone") == ids["phone"] else
                                                  " — as an emergency contact, not as the patient's own number")
                + "; different date of birth") if what == ["phone"] else
               "email only; different date of birth" if what == ["email"] else
               "found by name only — no identifier in common" if not what else "; ".join(what))
        rows.append(dict(record=other, name=(reg.get(other) or {}).get("name") or "?", dob=hit["dob"], phone=hit["phone"],
                         email=hit["email"], by_name=other in named, same=same, why=why))
    return dict(patient=pid, ids=ids, found=found, names=by_name, rows=rows, same=[r["record"] for r in rows if r["same"]])


# --------------------------------------------------------------------------- step 2: setup

def setup(cli: CLI, story: dict, reset: bool) -> tuple[str, dict[str, str]]:
    if reset:
        note("--reset: deleting everything the demo created, then starting over")
        cleanup(cli, quiet=True)
    p = story["project"]
    proj = find_project(cli, p["key"], echo=False)
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", cid(p["key"]), "--description",
                       p["description"], scoped=True)
    else:
        note("the registry project exists from an earlier run (pass --reset to start over)")
    note("one chart per patient: an actor each, so a question can be scoped to one chart (`search --actors`)")
    actors, made = {}, 0
    for i, pid in enumerate(story["patients"]):
        actors[pid], m = ensure_chart(cli, story, pid, echo=made == 0)
        made += m
    if 1 < made:
        note(f"… and {made - 1} more `actor create` the same way")
    lines = [f"\n    registry  {p['name']:<44} {proj['id']}"]
    for pid, aid in actors.items():
        lines.append(f"    chart     {chart_name(story, pid):<44} {aid}")
    emit("text", "\n".join(lines))
    emit("setup", "", project=proj["id"], name=p["name"], charts=actors)
    return proj["id"], actors


# --------------------------------------------------------------------------- step 3: intake

def pin(cli: CLI, scope: list[str], live_facts: list[dict], items: list[tuple[str, dict]], echo: bool = True) -> dict[str, dict]:
    """Pin the entries the scope does not hold yet (matched by metadata key) — one `fact add` for all of them, then
    `fact update --metadata` on each (fact add takes no metadata). `scope` is ["--project", id] or ["--actor", id].
    Returns key → {id, new}."""
    live = {}
    for f in live_facts:
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
        res = cli.run("fact", "add", *scope, *[t for t, _ in missing], scoped=True, echo=echo) or {}
        got = {f.get("fact"): f.get("id") for f in res.get("facts") or []}
        for i, (text, meta) in enumerate(missing):
            fid = got.get(text)
            if not fid:
                die(f"`fact add` did not return “{clip(text, 60)}”")
            cli.run("fact", "update", fid, *scope, "--metadata", json.dumps(meta), scoped=True, echo=echo and i == 0)
            out[meta["key"]] = dict(id=fid, new=True)
        if len(missing) > 1 and echo:
            note(f"… and {len(missing) - 1} more `fact update --metadata` the same way")
    return out


def restore_morning(cli: CLI, reg_pid: str, story: dict, actors: dict[str, str]) -> int:
    """Every run starts on the morning Lena checks in: what an earlier run merged is put back — the copies on the
    original chart are deleted, the registry entries and chart names return to what they said. (The duplicate chart
    itself was re-created by setup; intake pins its records again.)"""
    n = 0
    for f in list_facts(cli, ["--projects", reg_pid], echo=False):
        m = f.get("metadata") or {}
        p = story["patients"].get(m.get("record"))
        if p and (f.get("fact") != p["registry"] or any(k in m for k in MERGE_KEYS)):
            cli.run("fact", "update", f["id"], "--project", reg_pid, "--text", p["registry"], "--metadata",
                    json.dumps(registry_meta(m["record"], p)), scoped=True, echo=n == 0)
            n += 1
    for pid, aid in actors.items():
        for f in list_facts(cli, ["--actors", aid], echo=False):
            if (f.get("metadata") or {}).get("merged_from"):
                cli.run("fact", "delete", "--actor", aid, f["id"], scoped=True, echo=n == 0)
                n += 1
        a = cli.run("actor", "get", aid, echo=False) or {}
        if a.get("display_name") != chart_name(story, pid):
            cli.run("actor", "update", aid, "--display-name", chart_name(story, pid), echo=n == 0)
            n += 1
    if n:
        note(f"an earlier run merged the charts; {n} change(s) put back, so this run starts from the same morning")
    return n


def read_charts(cli: CLI, actors: dict[str, str], echo: bool = False) -> dict[str, dict]:
    return {pid: entries(list_facts(cli, ["--actors", aid], echo=echo and i == 0)) for i, (pid, aid) in enumerate(actors.items())}


def intake(cli: CLI, reg_pid: str, story: dict, actors: dict[str, str]) -> tuple[dict, dict]:
    restored = restore_morning(cli, reg_pid, story, actors)
    note("the registry: one fact per registration, word for word, then `fact update --metadata` with its fields")
    got = pin(cli, ["--project", reg_pid], list_facts(cli, ["--projects", reg_pid], echo=False),
              [(p["registry"], registry_meta(pid, p)) for pid, p in story["patients"].items()])
    new = sum(1 for g in got.values() if g["new"])
    note("the charts: every intake form, nurse note and front desk note pinned on the chart it was written on")
    for i, (pid, aid) in enumerate(actors.items()):
        items = [(r["text"], record_meta(r)) for r in story["records"] if r["patient"] == pid]
        g = pin(cli, ["--actor", aid], list_facts(cli, ["--actors", aid], echo=False), items, echo=i == 0)
        new += sum(1 for x in g.values() if x["new"])
    if not new and not restored:
        note("the records were pinned by an earlier run; reading them back")
    if new or restored:
        # `fact list` has new facts at once; `search` gets them a few seconds later. Step 4 reads through search.
        note(f"{new + restored} write(s); giving the search index 10 s to catch up before step 4 reads through it")
        time.sleep(10)
    reg = entries(list_facts(cli, ["--projects", reg_pid]))
    charts = read_charts(cli, actors, echo=True)
    lines = [f"\n  the registry — {len(reg)} registrations:",
             f"    {'chart':<7} {'name':<13} {'born':<11} {'mobile':<9} {'email':<25} registered"]
    for pid in sorted(reg, key=lambda x: reg[x]["registered"]):
        r = reg[pid]
        lines.append(f"    {pid:<7} {r['name']:<13} {r['dob']:<11} {r['phone']:<9} {r['email']:<25} {r['registered']}")
    lines.append(f"\n  the charts — {sum(len(c) for c in charts.values())} records:")
    for pid, c in charts.items():
        tops = ", ".join(f"{story['topics'][t]} {sum(1 for r in c.values() if r['topic'] == t)}"
                         for t in story["topics"] if any(r["topic"] == t for r in c.values()))
        lines.append(f"    {chart_name(story, pid):<22} {len(c)} record(s)   {tops}")
    ci = story["checkin"]
    lines.append(f"\n  today, {story['today']}: {chart_name(story, ci)} checks in for an annual physical with fasting labs.")
    for r in sorted(charts[ci].values(), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<8} {story['sources'][r['source']]:<16} {r['value']}")
    emit("text", "\n".join(lines))
    emit("intake", "", registry=[dict(reg[k], fid=reg[k]["fid"]) for k in sorted(reg)],
         charts={pid: [public(r) for r in sorted(c.values(), key=lambda r: (r["on"], r["record"]))] for pid, c in charts.items()},
         restored=restored)
    return reg, charts


# --------------------------------------------------------------------------- step 4: the check-in read

def checkin_review(cli: CLI, reg_pid: str, story: dict, actors: dict[str, str], charts: dict) -> tuple[list[dict], dict]:
    ci = story["checkin"]
    note(f"{len(story['questions'])} questions before today's visit. The check-in read: `search` the question in the chart "
         f"she was checked in under ({ci}) and take the first hit on that topic")
    out = []
    for q in story["questions"]:
        r = checkin_read(cli, actors[ci], charts[ci], q)
        lines = [f"\n  {q['id']} · {q['title']} · “{q['text']}”", f"  search in {ci}'s chart · {len(r['hits'])} hit(s):"]
        for h in r["hits"]:
            lines.append(hit_line(story, h, mark="   ← used" if h["record"] == r["used"] else ""))
        if r["cached"]:
            lines.append(f"  · {len(r['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        lines.append(f"  → check-in read: " + (f"{r['used']}, {r['on_topic'][0].get('value')}" if r["used"] else
                                              f"no {story['topics'][q['topic']]} record on this chart"))
        emit("text", "\n".join(lines))
        out.append(r)
    p = story["patients"][ci]
    names = [p["name"]] + ([p["previous_name"]] if p.get("previous_name") else [])
    note(f"the obvious repair: her form gives a previous surname, so read every chart the registry finds for "
         f"{' or '.join('“' + n + '”' for n in names)} (`fact list --query`, an exact substring, every page)")
    by_name = name_hits(cli, reg_pid, names)
    pids = sorted({x for xs in by_name.values() for x in xs})
    lines = [f"\n  the registry finds {len(pids)} registration(s) for those names:"]
    reg = entries(list_facts(cli, ["--projects", reg_pid], echo=False))
    for x in pids:
        r = reg.get(x, {})
        lines.append(f"    {x:<7} {r.get('name', '?'):<13} born {r.get('dob', '?')}   matched “{', '.join(n for n, xs in by_name.items() if x in xs)}”"
                     + ("" if r.get("name") in names else "  (the name is in the text: " + clip(next((s for s in r.get('body', '').split(' - ') if any(n in s for n in names)), ''), 40) + ")"))
    emit("text", "\n".join(lines))
    named = []
    for q in story["questions"]:
        rows = topic_rows(charts, pids, q["topic"])
        people = sorted({person_of(r) for r in rows})
        lines = [f"\n  {q['id']} · {story['topics'][q['topic']]} across those {len(pids)} charts:"]
        for r in rows:
            lines.append(f"      {r['chart']:<7} {story['patients'][r['chart']]['name']:<13} {r['on']}  {r['value']}")
        lines.append(f"  → name read: {len(rows)} record(s) from {len(people)} different patient(s)" +
                     (" — which of them is her?" if len(people) > 1 else ""))
        emit("text", "\n".join(lines))
        named.append(dict(q=q, rows=[public(dict(r, patient=r["chart"])) for r in rows], people=people))
    emit("checkin", "", reads=[dict(q=r["q"], used=r["used"], used_body=r["used_body"], hits=[public(h) for h in r["hits"]],
                                   cached=r["cached"]) for r in out],
         names=by_name, named=named)
    return out, dict(names=by_name, pids=pids, named=named)


# --------------------------------------------------------------------------- step 5: the identity read

def identity_lines(story: dict, idr: dict) -> list[str]:
    ids = idr["ids"]
    lines = [f"\n  {chart_name(story, idr['patient'])}'s registration: born {ids['dob']} · mobile {ids['phone']} · email {ids['email']}",
             f"    {'chart':<7} {'name':<13} {'born':<6} {'mobile':<7} {'email':<6} {'name?':<6} verdict"]
    yn = lambda b: "✓" if b else "·"
    for r in idr["rows"]:
        lines.append(f"    {r['record']:<7} {r['name']:<13} {yn(r['dob']):<6} {yn(r['phone']):<7} {yn(r['email']):<6} "
                     f"{yn(r['by_name']):<6} {'SAME PERSON' if r['same'] else 'someone else'} — {r['why']}")
    return lines


def identity_review(cli: CLI, reg_pid: str, story: dict, reg: dict, charts: dict, checkin: list[dict]) -> tuple[dict, list[dict]]:
    ci = story["checkin"]
    note(f"the identity read: each identifier on today's registration looked up in the registry (`fact list --query`). "
         f"Rule: {story['rule']}")
    idr = identity(cli, reg_pid, story, reg, ci)
    lines = identity_lines(story, idr)
    lines.append(f"\n  → same person: {', '.join(chart_name(story, x) for x in idr['same']) or 'nobody else'}")
    emit("text", "\n".join(lines))
    pids = [ci] + idr["same"]
    rows = []
    for r in checkin:
        q = r["q"]
        recs = topic_rows(charts, pids, q["topic"])
        missed = [x for x in recs if x["chart"] != ci]
        ok = not missed
        lines = [f"\n  {q['id']} · {q['title']} · her records on {story['topics'][q['topic']]} ({' + '.join(pids)}):"]
        for x in recs:
            lines.append(f"      {x['chart']:<7} {x['on']}  {x['value']:<48}" +
                         ("   ← check-in read" if x["record"] == r["used"] else "   (not on today's chart)" if x["chart"] != ci else ""))
        lines.append(f"  → identity read: {len(recs)} record(s) — " +
                     ("✓ all on today's chart" if ok else f"✗ the check-in read missed {len(missed)}"))
        emit("text", "\n".join(lines))
        rows.append(dict(q=q, used=r["used"], used_body=r["used_body"], used_value=r["on_topic"][0].get("value") if r["used"] else "", records=[public(dict(x, patient=x["chart"])) for x in recs],
                         missed=[x["record"] for x in missed], ok=ok))
    lines = [f"\n  {'item':<5} {'question':<26} {'check-in read (today’s chart)':<44} {'her records':<12}", "  " + "-" * 92]
    for x in rows:
        lines.append(f"  {x['q']['id']:<5} {x['q']['title']:<26} {clip(x['used_value'] if x['used'] else 'nothing on file', 44):<44} "
                     f"{len(x['records']):<3} {'✓' if x['ok'] else '✗ missed ' + str(len(x['missed']))}")
    bad = sum(1 for x in rows if not x["ok"])
    lines.append(f"\n  → {bad} of {len(rows)}: what she told this clinic in 2024 and 2025 is on a chart under her old name"
                 if bad else f"\n  → the check-in read saw all of her records on all {len(rows)}")
    emit("text", "\n".join(lines))
    emit("identity", "", identity=idr, rows=rows)
    return idr, rows


# --------------------------------------------------------------------------- step 6: merge the charts

def merge(cli: CLI, reg_pid: str, story: dict, reg: dict, actors: dict[str, str], charts: dict, idr: dict,
          rows: list[dict]) -> dict:
    ci = story["checkin"]
    if not idr["same"]:
        note("no other registration meets the rule; nothing to merge")
        emit("merge", "", survivor="", after=[], agree=0)
        return dict(survivor="", after=[], agree=0, untouched={})
    survivor = sorted(idr["same"], key=lambda x: reg[x]["registered"])[0]
    sp, dp = story["patients"][survivor], story["patients"][ci]
    others = [x for x in actors if x not in (survivor, ci)]
    before = {x: len(charts.get(x, {})) for x in others}
    note(f"merge the duplicate chart {ci} into the original {survivor}: its records go onto {survivor}'s chart word for word "
         f"(`fact add --actor`, where they came from in metadata), the chart and its registry entry get the new name, the "
         f"duplicate entry points at {survivor}, and the {ci} chart is deleted — its facts first")
    items = []
    for rid, r in sorted(charts[ci].items()):
        meta = dict(record_meta(next(x for x in story["records"] if x["id"] == rid)), key=f"pim:{rid}:merged",
                    merged_from=ci, source_actor=actors[ci], source_fact=r["fid"])
        items.append((r["body"], meta))
    got = pin(cli, ["--actor", actors[survivor]], list_facts(cli, ["--actors", actors[survivor]], echo=False), items)
    new_name = f"{dp['name']}, formerly {sp['name'].split()[-1]} ({survivor})"
    cli.run("actor", "update", actors[survivor], "--display-name", new_name)
    alias = f" Also registered as {ci} ({dp['name']}) on {dp['registered']}; merged into {survivor}."
    cli.run("fact", "update", reg[survivor]["fid"], "--project", reg_pid, "--text", sp["registry"] + alias, "--metadata",
            json.dumps(dict(registry_meta(survivor, sp), aliases=[dp["name"], ci])), scoped=True)
    cli.run("fact", "update", reg[ci]["fid"], "--project", reg_pid, "--metadata",
            json.dumps(dict(registry_meta(ci, dp), merged_into=survivor)), scoped=True)
    deleted = delete_actor(cli, actors[ci])
    merged = entries(list_facts(cli, ["--actors", actors[survivor]]))
    after_counts = {x: len(entries(list_facts(cli, ["--actors", actors[x]], echo=False))) for x in others}
    lines = [f"\n  {new_name} — {len(merged)} records on one chart ({actors[survivor]}):"]
    for r in sorted(merged.values(), key=lambda r: (r["on"], r["record"])):
        lines.append(f"    {r['record']:<8} {r['on']}  {'from ' + r['merged_from'] if r.get('merged_from') else '':<14} {clip(r['body'], 90)}")
    lines.append(f"\n  {ci}'s chart: {deleted} fact(s) deleted, then the actor; its registry entry now says merged_into {survivor}")
    lines.append(f"  everyone else's chart, before → after the merge: "
                 + ", ".join(f"{chart_name(story, x)} {before[x]} → {after_counts[x]}" for x in others))
    emit("text", "\n".join(lines))
    # Later the same day: new wording (search answers a repeated question from its cache), on the merged chart.
    note("later the same day, three more questions — the same check-in read, now on the merged chart. Search gets new "
         "facts a few seconds after `fact list` does; each read is retried until every one of her records on the topic "
         "is in the hits")
    time.sleep(6)
    after = []
    for q in story["followups"]:
        want = {x["body"] for x in topic_rows(charts, [ci, survivor], q["topic"])}
        t1, tries = time.time(), 0
        while True:
            tries += 1
            r = checkin_read(cli, actors[survivor], merged, q, echo=tries == 1)
            seen = {h["body"] for h in r["on_topic"]}
            if (want <= seen and not r["cached"]) or tries >= 8:
                break
            time.sleep(4)
        ok = want <= seen and bool(r["used"])
        after.append(dict(q=q, used=r["used"], used_body=r["used_body"], rank=r["on_topic"][0]["rank"] if r["on_topic"] else None,
                          found=len(want & seen), want=len(want), tries=tries, secs=int(time.time() - t1),
                          cached=r["cached"], hits=len(r["hits"]), ok=ok,
                          on_topic=[public(h) for h in r["on_topic"]]))
    lines = []
    for a in after:
        q = a["q"]
        lines.append(f"\n  {q['id']} · “{q['text']}”")
        for h in a["on_topic"]:
            lines.append(f"      #{h['rank']:<3} {h['record']:<8} {h['on']}  {h.get('value') or clip(h['body'], 60)}")
        lines.append(f"    → {a['found']} of her {a['want']} {story['topics'][q['topic']]} record(s) in the top {a['hits']}"
                     f"{' (try ' + str(a['tries']) + ')' if a['tries'] > 1 else ''}  {'✓' if a['ok'] else '✗'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    ok = sum(1 for a in after if a["ok"])
    lines.append(f"\n  → {ok} of {len(after)}: on the merged chart, the check-in read finds what she told the clinic as {sp['name']}")
    same = all(before[x] == after_counts[x] for x in others)
    lines.append(f"  → the other charts: {'unchanged' if same else 'CHANGED'} — "
                 + ", ".join(f"{x} {before[x]} → {after_counts[x]}" for x in others))
    emit("text", "\n".join(lines))
    out = dict(survivor=survivor, name=new_name, deleted=deleted, chart=[public(r) for r in merged.values()],
               new=sum(1 for g in got.values() if g["new"]), after=after, agree=ok,
               untouched={x: [before[x], after_counts[x]] for x in others})
    emit("merge", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: ask, patient

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def live_charts(cli: CLI, story: dict) -> tuple[dict[str, str], dict[str, dict]]:
    actors = {}
    for pid in story["patients"]:
        a = find_actor(cli, pid)
        if a:
            actors[pid] = a["id"]
    return actors, read_charts(cli, actors)


def ask_text(cli: CLI, reg_pid: str, text: str, topic: str, echo: bool = True) -> dict:
    """Any question before today's visit, three ways: the check-in read on the chart she was checked in under (or, once
    a run has merged it, what that chart held), the name read, and the identity read — plus, after a merge, the check-in
    read on the merged chart."""
    story = load_story()
    ci = story["checkin"]
    q = dict(id="(yours)", topic=topic, title="", text=text)
    reg = entries(list_facts(cli, ["--projects", reg_pid], echo=False))
    actors, charts = live_charts(cli, story)
    out = dict(q=q, checkin=None, was=None, merged=None)
    if ci in actors:
        r = checkin_read(cli, actors[ci], charts[ci], q, echo=echo)
        out["checkin"] = dict(used=r["used"], used_body=r["used_body"], hits=[public(h) for h in r["hits"]], cached=r["cached"])
    # After a merge the duplicate chart is gone; what it held lives on as the copies marked merged_from.
    copies = {pid: {k: dict(v, patient=ci) for k, v in c.items() if v.get("merged_from") == ci} for pid, c in charts.items()}
    survivor = next((pid for pid, c in copies.items() if c), "")
    if survivor:
        was = sorted((r for r in copies[survivor].values() if r["topic"] == topic), key=lambda r: (r["on"], r["record"]))
        out["was"] = dict(survivor=survivor, records=[public(r) for r in was])
    p = story["patients"][ci]
    names = [p["name"]] + ([p["previous_name"]] if p.get("previous_name") else [])
    by_name = name_hits(cli, reg_pid, names, echo=echo)
    npids = sorted({x for xs in by_name.values() for x in xs} - ({ci} if ci not in actors else set()))
    nrows = topic_rows(charts, npids, topic)
    if survivor:  # the merged copies are on the survivor's chart but are not someone else's records
        nrows = [r for r in nrows if not r.get("merged_from")]
    out["named"] = dict(pids=npids, rows=[public(dict(r, patient=r.get("patient") or r["chart"])) for r in nrows],
                        people=sorted({r.get("patient") or r["chart"] for r in nrows}))
    idr = identity(cli, reg_pid, story, reg, ci, echo=echo)
    out["identity"] = idr
    hers = idr["same"] + ([ci] if ci in actors else [])
    mine = topic_rows(charts, hers, topic)
    out["mine"] = [public(dict(r, patient=r.get("patient") or r["chart"])) for r in mine]
    if survivor:
        r = checkin_read(cli, actors[survivor], charts[survivor], q, echo=echo)
        out["merged"] = dict(survivor=survivor, used=r["used"], used_body=r["used_body"],
                             on_topic=[public(h) for h in r["on_topic"]], hits=len(r["hits"]))
    return out


def patient_text(cli: CLI, reg_pid: str, pid: str, echo: bool = True) -> dict:
    story = load_story()
    reg = entries(list_facts(cli, ["--projects", reg_pid], echo=False))
    if pid not in reg:
        die(f"{pid} is not in the registry")
    idr = identity(cli, reg_pid, story, reg, pid, echo=echo)
    a = find_actor(cli, pid)
    n = len(list_facts(cli, ["--actors", a["id"]], echo=False)) if a else 0
    return dict(identity=idr, actor=a["id"] if a else "", name=(a or {}).get("display_name") or "", records=n,
                merged_into=reg[pid].get("merged_into") or "", aliases=reg[pid].get("aliases") or [])


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
    gone = 0
    for pid in story["patients"]:
        a = find_actor(cli, pid)
        if a:
            delete_actor(cli, a["id"])
            gone += 1
    proj = find_project(cli, story["project"]["key"], echo=not quiet)
    if not proj and not gone:
        note("nothing to delete")
        emit("cleaned", "")
        return
    if proj:
        facts = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        for f in facts:
            cli.run("fact", "delete", "--project", proj["id"], f["id"], scoped=True, echo=False)
        left = pages(cli, ["fact", "list", "--projects", proj["id"]], echo=False)
        if left:
            die(f"{len(left)} fact(s) still on the project after deleting; run cleanup again")
        cli.run("proj", "delete", proj["id"], scoped=True, echo=not quiet)
        note(f"deleted the registry project and its {len(facts)} facts")
    if gone:
        note(f"deleted {gone} patient chart(s) and their records")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 6


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    t0 = time.time()
    banner(2, TOTAL, "Set up — a front desk registry and one chart per patient")
    reg_pid, actors = setup(cli, story, reset)
    cli.pid = reg_pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    cli.actors = actors  # type: ignore[attr-defined]
    banner(3, TOTAL, f"Intake — {len(story['patients'])} registrations, {len(story['records'])} chart records")
    reg, charts = intake(cli, reg_pid, story, actors)
    banner(4, TOTAL, "The check-in read — the chart she was checked in under, then her names")
    checkin, named = checkin_review(cli, reg_pid, story, actors, charts)
    banner(5, TOTAL, "The identity read — who is this patient?")
    idr, rows = identity_review(cli, reg_pid, story, reg, charts, checkin)
    banner(6, TOTAL, "Merge the charts — then the check-in read again")
    mg = merge(cli, reg_pid, story, reg, actors, charts, idr, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   checkin={x["q"]["id"]: x["used"] for x in rows},
                   hits={r["q"]["id"]: [h["record"] for h in r["hits"]] for r in checkin},
                   named={n["q"]["id"]: n["people"] for n in named["named"]},
                   same=idr["same"], missed={x["q"]["id"]: x["missed"] for x in rows},
                   ok={x["q"]["id"]: x["ok"] for x in rows},
                   cached=[c for r in checkin for c in r["cached"]],
                   after={a["q"]["id"]: [a["used"], a["rank"], a["found"], a["want"], a["tries"], a["ok"]] for a in mg["after"]},
                   after_agree=mg["agree"], untouched=mg.get("untouched"))
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def print_ask(story: dict, r: dict) -> None:
    q = r["q"]
    ci = story["checkin"]
    t = story["topics"][q["topic"]]
    lines = [f"\n  “{q['text']}” · topic {t}"]
    c = r.get("checkin")
    if c:
        lines.append(f"  the check-in read — search in {ci}'s chart, {len(c['hits'])} hit(s):")
        lines += [hit_line(story, h, mark="   ← used" if h["record"] == c["used"] else "") for h in c["hits"]]
        lines.append(f"  → " + (f"{c['used']}: {next((h.get('value') for h in c['hits'] if h['record'] == c['used']), '')}" if c["used"] else f"no {t} record on this chart"))
    if r.get("was") is not None:
        w = r["was"]
        lines.append(f"  the check-in chart {ci} as it was this morning (merged into {w['survivor']}; its records are the "
                     f"copies marked merged_from {ci}):")
        lines += [f"      {x['record']:<8} {x['on']}  {x['value']}" for x in w["records"]] or [f"      no {t} record"]
        lines.append(f"  → the check-in read this morning: " + (w["records"][0]["value"] if w["records"] else "nothing on file"))
    n = r["named"]
    lines.append(f"  the name read — {len(n['pids'])} chart(s) the registry finds for her names, {len(n['rows'])} {t} record(s) "
                 f"from {len(n['people'])} patient(s):")
    lines += [f"      {x['patient']:<7} {x['on']}  {x['value']}" for x in n["rows"]]
    lines += identity_lines(story, r["identity"])
    lines.append(f"  → her own {t} records (same person: {', '.join(r['identity']['same']) or 'none'}):")
    lines += [f"      {x['patient']:<7} {x['on']}  {x['value']}" for x in r["mine"]] or ["      none"]
    m = r.get("merged")
    if m:
        lines.append(f"  the check-in read on the merged chart {m['survivor']} — {len(m['on_topic'])} {t} record(s) in the top {m['hits']}:")
        lines += [f"      #{h['rank']:<3} {h['record']:<8} {h['on']}  {h['value']}" for h in m["on_topic"]]
    else:
        lines.append(f"  · the charts are not merged yet — `python3 demo.py` merges them in step 6")
    emit("text", "\n".join(lines))


def main() -> None:
    story = load_story()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "ask", "patient", "cleanup"],
                    help="run = full demo (default); ask \"QUESTION\" --topic draw = the check-in read, the name read and "
                         "the identity read side by side; patient P-1088 = which registrations are this patient, by "
                         "identifier; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the question (ask) or the chart id (patient)")
    ap.add_argument("--topic", default="draw", choices=list(story["topics"]), help="ask: what the question is about")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if a.command in ("ask", "patient") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. ask \"Should she lie down for the blood draw?\" --topic draw, "
                 "or patient P-1088")
    if a.command == "patient" and a.args[0] not in story["patients"]:
        ap.error("patient takes one of " + ", ".join(story["patients"]))
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py patient P-1088`, `python3 demo.py ask \"Does she need nitrile gloves?\" "
              "--topic allergy`; `python3 demo.py cleanup` removes the demo data.")
        return
    reg_pid = need_project(cli)
    if a.command == "ask":
        banner(2, 2, "Ask — the check-in read, the name read, the identity read")
        print_ask(story, ask_text(cli, reg_pid, a.args[0], a.topic))
    elif a.command == "patient":
        banner(2, 2, f"Patient — which registrations are {a.args[0]}?")
        c = patient_text(cli, reg_pid, a.args[0])
        lines = identity_lines(story, c["identity"])
        lines.append(f"\n  → same person: {', '.join(c['identity']['same']) or 'nobody else'}")
        lines.append(f"  chart: " + (f"{c['name']} — {c['records']} record(s) on {c['actor']}" if c["actor"] else
                                     f"none — merged into {c['merged_into']}" if c["merged_into"] else "none"))
        if c["aliases"]:
            lines.append(f"  also registered as: {', '.join(c['aliases'])}")
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

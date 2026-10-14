#!/usr/bin/env python3
"""
Referral network memory for small medical practices — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Larkspur Family Medicine (fictional, four clinicians) keeps its referral
network in one MemoryLake project: the provider directory, the EHR referral log
(every event of every referral, word for word) and the office notes Dana
Whitlock left when she stepped down as office manager.  Rosa Mendel starts on
2026-10-14 and has three referrals to place.

The quick read is what an assistant does with "referral decisions retrieve
relevant provider history": `search` the referral, take the closest office
note, follow its advice.  Dana's notes are reflection memory — "Bayside Imaging
is slow for MRI, about two weeks; send MRIs to Northgate".  The number is right:
Bayside's MRI referrals did take 13.6 days on average.  But most of those days
were not Bayside's.  All five came back within two days for missing paperwork,
then sat with the practice for 7 to 10 days before anyone resent them.

The memory read reads every referral to every provider in the specialty
(`fact list --query "<provider>"`, exact substring, every page) and splits each
referral's days by who was holding it: the provider (sent → returned, resent →
seen), the practice (returned → resent), the patient (a rescheduled
appointment).  Bayside's own share is 4.2 days, Northgate's 6.0, and Northgate
is 9 miles further away.  Cardiology is the same story; dermatology is the
control — Lakeview's month really is Lakeview's, and both reads agree.

Step 6 rewrites the notes that blamed the wrong party (`fact update --text`,
the old text kept in metadata.previous, the change visible in `fact trace`),
and the same quick-read code then agrees with the referral log on the day's
later referrals.

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

PREFIX = "mlu-rnm"  # memorylake-usecases / referral-network-memory


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
    m = dict(key=f"rnm:{r['id']}", record=r["id"], tool=r["tool"], kind=r["kind"], by=r["by"], on=r["on"],
             provider=r["provider"], specialty=r["specialty"])
    if r["kind"] == "note":
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


def list_facts(cli: CLI, pid: str, query: str = "", echo: bool = True) -> list[dict]:
    return pages(cli, ["fact", "list", "--projects", pid] + (["--query", query] if query else []), echo=echo)


def entries(facts: list[dict]) -> dict[str, dict]:
    """The pinned records by record id (REF-2207, PRV-BI, NOTE-IMG …), metadata flattened in, plus fact id and text."""
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
    return {k: r.get(k) for k in ("record", "tool", "kind", "by", "on", "provider", "specialty", "prefer", "fid", "body",
                                  "rank", "previous")}


# --------------------------------------------------------------------------- whose days were they (plain code, no model)

DATE = r"(\d{4}-\d\d-\d\d)"


def days(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def split_days(text: str) -> dict | None:
    """One referral's elapsed days, split by who was holding it — read from the referral record's own text.
    provider: sent → returned, resent → seen (minus a patient's reschedule); practice: returned → resent;
    patient: the days a rescheduled appointment moved."""
    sent = re.search(r"Sent " + DATE, text)
    seen = re.search(r"Seen " + DATE, text)
    if not sent or not seen:
        return None
    returns = re.findall(r"Returned " + DATE + r": ([^.]*)\.", text)
    resends = re.findall(r"Resent " + DATE, text)
    if len(resends) < len(returns):
        return None
    ours = sum(days(r[0], s) for r, s in zip(returns, resends))
    patient = sum(days(a, b) for a, b in re.findall(r"rescheduled the " + DATE + r" appointment to " + DATE, text))
    total = days(sent.group(1), seen.group(1))
    return dict(total=total, ours=ours, patient=patient, theirs=total - ours - patient,
                reasons=[r[1].strip() for r in returns], sent=sent.group(1), seen=seen.group(1))


def and_list(xs: list[str]) -> str:
    if len(xs) < 2:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def avg(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 1) if xs else 0.0


def profile(r: dict) -> dict:
    """A directory entry, parsed from its text."""
    miles = re.search(r"(\d+) miles", r["body"])
    plans = re.search(r"In network: ([^.]*)\.", r["body"])
    rule = re.search(r"Intake rule: (.*)$", r["body"])
    return dict(name=r["provider"], specialty=r["specialty"], miles=int(miles.group(1)) if miles else 99,
                plans=[p.strip() for p in plans.group(1).split(",")] if plans else [], rule=rule.group(1) if rule else "")


def provider_history(cli: CLI, pid: str, name: str, echo: bool = True) -> dict:
    """Every referral to one provider (`fact list --query "<name>"`, exact substring, every page), each split."""
    got = entries(list_facts(cli, pid, name, echo=echo))
    refs = []
    for r in sorted(got.values(), key=lambda r: r["on"]):
        if r.get("kind") != "referral" or f"referred to {name} " not in r["body"]:
            continue
        sp = split_days(r["body"])
        if sp:
            refs.append(dict(public(r), **sp))
    reasons: dict[str, int] = {}
    for x in refs:
        for why in x["reasons"]:
            reasons[why] = reasons.get(why, 0) + 1
    return dict(name=name, n=len(refs), refs=refs, raw=avg([x["total"] for x in refs]),
                theirs=avg([x["theirs"] for x in refs]), ours=avg([x["ours"] for x in refs]),
                patient=avg([x["patient"] for x in refs]), returned=sum(1 for x in refs if x["reasons"]), reasons=reasons)


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
    return f"      #{h['rank']:<3} {h['record']:<9} {tool:<13} {clip(h['body'], 78)}{mark}"


# --------------------------------------------------------------------------- read 1: the quick read

def quick_read(cli: CLI, pid: str, mem: dict, t: dict, echo: bool = True) -> dict:
    """The page's "referral decisions retrieve relevant provider history", done the quick way: search the referral,
    take the closest office note, follow its advice. The note is read from `fact list` by fact id (search can answer
    from a cache of older text)."""
    hits = search(cli, t["text"], pid, echo=echo)
    by_fid = {r["fid"]: r for r in mem.values()}
    note_hit = next((h for h in hits if h["kind"] == "note"), None)
    cached = [h["record"] for h in hits if h["fid"] not in by_fid]
    if note_hit is None:
        return dict(ticket=t, hits=hits, note=None, provider="", said="no office note in the top 8", cached=cached)
    cur = by_fid.get(note_hit["fid"], note_hit)
    return dict(ticket=t, hits=hits, note=cur["record"], note_rank=note_hit["rank"], note_body=cur["body"],
                specialty=cur.get("specialty"), provider=cur.get("prefer") or "", cached=cached,
                said=f"the note says send it to {cur.get('prefer')}")


# --------------------------------------------------------------------------- read 2: whose days were they

def memory_read(cli: CLI, pid: str, mem: dict, specialty: str, plan: str, within: int = 0, echo: bool = True) -> dict:
    """Every provider in the specialty (from the directory), every referral to each (from the EHR log), each referral's
    days split by who held it. Pick: in network for the plan, the provider's own days within the deadline if there is
    one, then the shortest own days, then the nearest."""
    provs = [profile(r) for r in mem.values() if r.get("kind") == "provider" and r.get("specialty") == specialty]
    rows = []
    for i, p in enumerate(sorted(provs, key=lambda p: p["name"])):
        h = provider_history(cli, pid, p["name"], echo=echo and i == 0)
        rows.append(dict(h, miles=p["miles"], plans=p["plans"], rule=p["rule"], in_network=plan in p["plans"]))
    ok = [r for r in rows if r["in_network"] and r["n"] and (not within or r["theirs"] <= within)]
    best = min(ok, key=lambda r: (r["theirs"], r["miles"])) if ok else None
    checklist = []
    if best:
        story = load_story()
        checklist = [story["checklist"].get(why, why) for why in sorted(best["reasons"], key=lambda w: -best["reasons"][w])]
    if best is None:
        said = f"no in-network provider for {plan}" + (f" gets patients in within {within} days" if within else "")
    else:
        said = f"{best['name']}: its own share is {best['theirs']} days"
        if best["returned"]:
            said += f"; send it complete ({and_list(checklist)})"
    return dict(specialty=specialty, plan=plan, within=within, providers=rows, provider=best["name"] if best else "",
                said=said, checklist=checklist)


def table_lines(rows: list[dict], quick: str = "", best: str = "") -> list[str]:
    lines = [f"      {'provider':<22} {'n':>2} {'raw':>5}  = {'theirs':>6} + {'ours':>5} + {'patient':>7}  {'mi':>3}  network  returned for"]
    for r in rows:
        mark = "  ← memory read" if r["name"] == best else ""
        mark += "  ← the note" if r["name"] == quick and quick != best else ""
        why = ", ".join(f"{k} ×{v}" for k, v in r["reasons"].items()) or "—"
        lines.append(f"      {r['name']:<22} {r['n']:>2} {r['raw']:>5}  = {r['theirs']:>6} + {r['ours']:>5} + {r['patient']:>7}  "
                     f"{r['miles']:>3}  {'yes' if r['in_network'] else 'NO ':<7}  {clip(why, 50)}{mark}")
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


REWRITE_KEYS = ("previous", "previous_prefer", "rewritten_on", "rewritten_by", "basis")


def restore_first_day(cli: CLI, pid: str) -> int:
    """Every run starts on Rosa's first morning: notes an earlier run rewrote are put back word for word from
    metadata.previous."""
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
    note("three sources, one project: the provider directory, the EHR referral log (every event of every referral) and "
         "the office notes Dana Whitlock left. One fact per record, word for word, then `fact update --metadata`")
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
        lines.append(f"    {label:<14} {len(rs):>2}")
    lines.append("\n  Dana's notes, as she left them:")
    for r in sorted((r for r in mem.values() if r["kind"] == "note"), key=lambda r: r["record"]):
        lines.append(f"    {r['record']:<9} {r['body']}")
    emit("text", "\n" + "\n".join(lines))
    emit("intake", "", records=[public(r) for r in sorted(mem.values(), key=lambda r: (r["tool"], r["on"], r["record"]))],
         restored=restored)
    return mem


# --------------------------------------------------------------------------- step 4: the quick read

def quick_place(cli: CLI, pid: str, story: dict, mem: dict) -> list[dict]:
    note(f"{len(story['incoming'])} referrals to place this morning. The quick read: `search` the referral, take the "
         "closest office note, follow its advice")
    out = []
    for t in story["incoming"]:
        q = quick_read(cli, pid, mem, t)
        lines = [f"\n  {t['id']} ({t['plan']}{', within ' + str(t['within']) + ' days' if t['within'] else ''}): “{t['text']}”",
                 f"  search · top {len(q['hits'])}:"]
        for h in q["hits"]:
            lines.append(hit_line(story, h, "   ← closest note" if h["record"] == q["note"] else ""))
        if q["cached"]:
            lines.append(f"  · {len(q['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
        if q["note"]:
            lines.append(f"  → {q['note']}: “{clip(q['note_body'], 150)}”")
        lines.append(f"  → quick read: send to {q['provider'] or '?'}")
        emit("text", "\n".join(lines))
        out.append(q)
    emit("quick", "", reads=[dict(ticket=q["ticket"], note=q["note"], note_body=q.get("note_body", ""), provider=q["provider"],
                                  said=q["said"], hits=[public(h) for h in q["hits"]], cached=q["cached"]) for q in out])
    return out


# --------------------------------------------------------------------------- step 5: the memory read

def memory_place(cli: CLI, pid: str, story: dict, mem: dict, quick: list[dict]) -> list[dict]:
    note("the memory read: every provider in the note's specialty (directory), every referral to each "
         "(`fact list --query \"<provider>\"`, exact substring, every page), each referral's days split by who held it — "
         "the provider (sent → returned, resent → seen), the practice (returned → resent), the patient (a reschedule)")
    rows = []
    for i, q in enumerate(quick):
        t = q["ticket"]
        if not q["note"]:
            continue
        mr = memory_read(cli, pid, mem, q["specialty"], t["plan"], t["within"], echo=i == 0)
        lines = [f"\n  {t['id']} {t['text']} · {t['plan']}" + (f" · within {t['within']} days" if t["within"] else "")]
        lines += table_lines(mr["providers"], q["provider"], mr["provider"])
        about = mem[q["note"]].get("about")
        a = next((r for r in mr["providers"] if r["name"] == about), None)
        if a and a["ours"] >= 1:
            lines.append(f"  · the note's “{clip(about, 30)} is slow” is the raw column ({a['raw']} days); "
                         f"{a['ours']} of them were ours — {a['returned']} of {a['n']} came back for paperwork and waited with us")
        elif a:
            lines.append(f"  · the note's “{clip(about, 30)} is slow” holds: {a['theirs']} of its {a['raw']} days are its own")
        lines.append(f"  → quick read:  {q['provider']}")
        lines.append(f"  → memory read: {mr['said']}")
        emit("text", "\n".join(lines))
        rows.append(dict(ticket=t, note=q["note"], quick=q["provider"], memory=mr, agree=q["provider"] == mr["provider"]))
    lines = [f"\n  {'referral':<9} {'the note says':<24} {'the referral log says':<24}", "  " + "-" * 70]
    for r in rows:
        lines.append(f"  {r['ticket']['id']:<9} {r['quick']:<24} {r['memory']['provider']:<24} {'✓ same' if r['agree'] else '✗ differs'}")
    wrong = [r for r in rows if not r["agree"]]
    lines.append(f"\n  → {len(wrong)} of {len(rows)} referrals the notes would send to the slower provider, further away — "
                 "and the paperwork that caused the wait would keep going out incomplete" if wrong
                 else f"\n  → the two reads agree on all {len(rows)}")
    for r in wrong:
        lines.append(f"      {r['ticket']['id']}: {r['memory']['provider']}, with {and_list(r['memory']['checklist']) or 'the usual packet'}")
    emit("text", "\n".join(lines))
    emit("memory", "", rows=[dict(ticket=r["ticket"], note=r["note"], quick=r["quick"], agree=r["agree"],
                                  memory={k: r["memory"][k] for k in ("provider", "said", "checklist", "specialty", "plan", "within")},
                                  providers=[{k: v for k, v in p.items() if k != "refs"} | {"refs": [
                                      {k: x[k] for k in ("record", "body", "total", "theirs", "ours", "patient", "reasons")}
                                      for x in p["refs"]]} for p in r["memory"]["providers"]],
                                  about=mem[r["note"]].get("about")) for r in rows])
    return rows


# --------------------------------------------------------------------------- step 6: write it back

def rewritten_note(about: str, mr: dict, story: dict) -> str:
    """The note's conclusion only; the figures (they change with every referral) go to metadata."""
    best = mr["provider"]
    a = next((r for r in mr["providers"] if r["name"] == about), None)
    others = [r["name"] for r in mr["providers"] if r["name"] not in (about, best)]
    kind = {"imaging": "MRI", "cardiology": "echocardiogram", "dermatology": "dermatology"}.get(mr["specialty"], mr["specialty"])
    if best == about and a:
        return (f"Office notes (rewritten by {story['office_manager']['name']} from the referral log): for {kind} referrals, "
                f"{about}'s own turnaround is shorter than {' or '.join(others) or 'the alternatives'}'s. The long waits were "
                f"on our side: referrals came back for missing paperwork and waited with us before they were resent. "
                f"Send {kind} referrals to {about} with {and_list(mr['checklist'])} attached.")
    return ""


def write_back(cli: CLI, pid: str, story: dict, mem: dict, rows: list[dict]) -> dict:
    today = story["today"]
    note("write it back where the next referral will read it: each note that blamed the wrong party is rewritten from "
         "the referral log (`fact update --text`; the old text, the old advice and the figures go to metadata)")
    updates = []
    for r in rows:
        if r["agree"]:
            continue
        n = mem[r["note"]]
        text = rewritten_note(n["about"], r["memory"], story)
        if not text or text == n["body"]:
            continue
        meta = {k: n[k] for k in ("key", "record", "tool", "kind", "by", "on", "provider", "specialty", "about")}
        basis = {p["name"]: dict(n=p["n"], raw=p["raw"], theirs=p["theirs"], ours=p["ours"], patient=p["patient"])
                 for p in r["memory"]["providers"]}
        meta.update(prefer=r["memory"]["provider"], previous=n["body"], previous_prefer=n["prefer"], rewritten_on=today,
                    rewritten_by=story["office_manager"]["name"], basis=basis)
        updates.append((n, text, meta))
    for i, (n, text, meta) in enumerate(updates):
        cli.run("fact", "update", n["fid"], "--project", pid, "--text", text, "--metadata", json.dumps(meta),
                scoped=True, echo=i == 0)
    if len(updates) > 1:
        note(f"… and {len(updates) - 1} more note(s) the same way")
    mem2 = entries(list_facts(cli, pid))
    lines = []
    for n, _, _ in updates:
        lines.append(f"\n    {n['record']} was “{clip(n['body'], 140)}”")
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
    note("later the same day, three more referrals of the same kinds — the quick read again, unchanged code. Search "
         "gets a rewritten fact a few seconds after `fact list` does; each read is retried until its note is the "
         "current text")
    time.sleep(6)
    after = []
    for i, t in enumerate(story["followups"]):
        t1, tries = time.time(), 0
        while True:
            tries += 1
            q = quick_read(cli, pid, mem2, t, echo=tries == 1)
            if not q["cached"] or tries >= 8:
                break
            time.sleep(4)
        mr = memory_read(cli, pid, mem2, q["specialty"], t["plan"], t["within"], echo=False) if q["note"] else None
        after.append(dict(ticket=t, note=q["note"], note_rank=q.get("note_rank"), quick=q["provider"],
                          memory=mr["provider"] if mr else "", agree=bool(mr) and q["provider"] == mr["provider"],
                          tries=tries, secs=int(time.time() - t1), cached=q["cached"]))
    lines = []
    for a in after:
        lines.append(f"\n  {a['ticket']['id']} “{a['ticket']['text']}” · {a['ticket']['plan']}")
        lines.append(f"    closest note {a['note']} (#{a['note_rank']}{', try ' + str(a['tries']) if a['tries'] > 1 else ''})"
                     f" · quick read: {a['quick']} · memory read: {a['memory']}  {'✓ same' if a['agree'] else '✗ differs'}")
        if a["cached"]:
            lines.append(f"    · {len(a['cached'])} hit(s) carry fact ids not in `fact list` now — search answered from its cache")
    agree = sum(1 for a in after if a["agree"])
    lines.append(f"\n  → the quick read now agrees with the referral log on {agree} of {len(after)}: the note it follows "
                 "is the one the referral log rewrote")
    emit("text", "\n".join(lines))
    out = dict(updated=[dict(record=n["record"], was=n["body"], now=mem2[n["record"]]["body"],
                             was_prefer=n["prefer"], now_prefer=mem2[n["record"]].get("prefer")) for n, _, _ in updates],
               trace=trace, after=after, agree=agree)
    emit("writeback", "", **out)
    return out


# --------------------------------------------------------------------------- read-only: refer, provider

def need_project(cli: CLI) -> str:
    proj = find_project(cli, load_story()["project"]["key"], echo=False)
    if proj is None:
        die("the demo data does not exist yet; run `python3 demo.py` first")
    return proj["id"]


def refer_text(cli: CLI, pid: str, text: str, plan: str, within: int = 0, echo: bool = True) -> dict:
    """Any referral text: the quick read and the memory read side by side."""
    mem = entries(list_facts(cli, pid, echo=False))
    t = dict(id="(yours)", patient="", plan=plan, within=within, text=text)
    q = quick_read(cli, pid, mem, t, echo=echo)
    mr = memory_read(cli, pid, mem, q["specialty"], plan, within, echo=echo) if q["note"] else None
    return dict(ticket=t, hits=[public(h) for h in q["hits"]], note=q["note"], note_body=q.get("note_body", ""),
                quick=q["provider"], memory=({k: mr[k] for k in ("provider", "said", "checklist", "specialty")} if mr else None),
                providers=[{k: v for k, v in p.items() if k != "refs"} for p in (mr["providers"] if mr else [])],
                about=mem[q["note"]].get("about") if q["note"] else "",
                agree=bool(mr) and q["provider"] == mr["provider"])


def provider_text(cli: CLI, pid: str, name: str, echo: bool = True) -> dict:
    h = provider_history(cli, pid, name, echo=echo)
    return {k: v for k, v in h.items()}


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
    banner(2, TOTAL, "Set up — one project for the referral network")
    pid = setup(cli, story, reset)
    cli.pid = pid  # type: ignore[attr-defined]  (the web app reads the live id from here)
    banner(3, TOTAL, f"Intake — {len(story['records'])} records: directory, EHR referral log, office notes")
    mem = intake(cli, pid, story)
    banner(4, TOTAL, "The quick read — the closest office note, and its advice")
    quick = quick_place(cli, pid, story, mem)
    banner(5, TOTAL, "The memory read — whose days were they?")
    rows = memory_place(cli, pid, story, mem, quick)
    banner(6, TOTAL, "Write it back — notes from the referral log, then the quick read again")
    wb = write_back(cli, pid, story, mem, rows)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"), seconds=int(time.time() - t0),
                   note={q["ticket"]["id"]: [q["note"], q.get("note_rank")] for q in quick},
                   quick={r["ticket"]["id"]: r["quick"] for r in rows},
                   memory={r["ticket"]["id"]: r["memory"]["provider"] for r in rows},
                   cached=[c for q in quick for c in q["cached"]],
                   after={a["ticket"]["id"]: [a["note"], a["note_rank"], a["tries"], a["agree"]] for a in wb["after"]},
                   after_agree=wb["agree"], trace=[e["event"] for e in wb["trace"]])
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "refer", "provider", "cleanup"],
                    help="run = full demo (default); refer \"REFERRAL TEXT\" --plan PLAN [--within DAYS] = the quick read "
                         "and the memory read of any referral; provider \"Bayside Imaging\" = every referral to a provider, "
                         "split by who held it; cleanup = delete everything")
    ap.add_argument("args", nargs="*", help="the referral text (refer) or the provider name (provider)")
    ap.add_argument("--plan", default="Harbor Health PPO", help="refer: the patient's insurance plan")
    ap.add_argument("--within", type=int, default=0, help="refer: the patient must be seen within this many days")
    ap.add_argument("--reset", action="store_true", help="delete everything the demo created first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    story = load_story()
    if a.command in ("refer", "provider") and len(a.args) != 1:
        ap.error(f"{a.command} needs one argument, e.g. refer \"Knee MRI after a sports injury\" --plan \"Cedar Mutual\" "
                 "or provider \"Bayside Imaging\"")
    total = TOTAL if a.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(a.show_json)
    if a.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if a.command == "run":
        run_pipeline(cli, reset=a.reset)
        print("\nDone. Try `python3 demo.py refer \"Knee MRI after a sports injury\" --plan \"Cedar Mutual\"`, "
              "`python3 demo.py provider \"Bayside Imaging\"`; `python3 demo.py cleanup` removes the demo data.")
        return
    pid = need_project(cli)
    if a.command == "refer":
        banner(2, 2, "Refer — the quick read and the memory read")
        r = refer_text(cli, pid, a.args[0], a.plan, a.within)
        lines = [f"\n  “{r['ticket']['text']}” · {a.plan}" + (f" · within {a.within} days" if a.within else "")
                 + f" — top {len(r['hits'])}:"]
        lines += [hit_line(story, h, "   ← closest note" if h["record"] == r["note"] else "") for h in r["hits"]]
        if r["note"]:
            lines.append(f"  → {r['note']}: “{clip(r['note_body'], 150)}”")
        lines.append(f"  → quick read:  {r['quick'] or '?'}")
        if r["memory"]:
            lines += table_lines(r["providers"], r["quick"], r["memory"]["provider"])
            lines.append(f"  → memory read: {r['memory']['said']}  {'✓ same' if r['agree'] else '✗ differs'}")
        emit("text", "\n".join(lines))
    elif a.command == "provider":
        banner(2, 2, f"Provider — every referral to {a.args[0]}, split by who held it")
        h = provider_text(cli, pid, a.args[0])
        lines = [f"\n  {h['n']} referral(s):", f"    {'id':<9} {'sent':<10} {'seen':<10} {'days':>4} = {'theirs':>6} + {'ours':>4} + {'patient':>7}  returned for"]
        for x in h["refs"]:
            lines.append(f"    {x['record']:<9} {x['sent']:<10} {x['seen']:<10} {x['total']:>4} = {x['theirs']:>6} + {x['ours']:>4} + "
                         f"{x['patient']:>7}  {', '.join(x['reasons']) or '—'}")
        lines.append(f"    {'average':<31} {h['raw']:>4} = {h['theirs']:>6} + {h['ours']:>4} + {h['patient']:>7}")
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

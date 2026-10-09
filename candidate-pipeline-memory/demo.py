#!/usr/bin/env python3
"""
Candidate pipeline memory for recruiting teams — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Halden Robotics is hiring a Senior Controls Engineer.  Two candidates go through
three stages — recruiter screen, technical interview, values interview — each
with a different interviewer.  Every interview is a conversation between the
candidate and the interviewer, and every interviewer's private scorecard is
pinned as a fact on the candidate.  The memory sits on the candidate, so:

  * the debrief sees all three scorecards and the candidate's own words at once;
  * when the recruiter rotates off the requisition, nothing has to be handed over;
  * when a candidate withdraws, `actor unbind` seals his memory in this
    workspace — reads, searches and writes are refused, the actor is kept;
  * when he applies again two months later, `actor bind` brings back exactly
    the same facts, and the new conversation adds to them.

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

PREFIX = "mlu-cpm"  # memorylake-usecases / candidate-pipeline-memory



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

    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "card", "search"}
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
        retryable = any(str(a) in self.READ_ONLY for a in args[:3])
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
        items = (cli.run("ws", "list", "--page-size", "1") or {}).get("items") or []
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

def load_team() -> dict:
    return json.loads((DATA / "team.json").read_text(encoding="utf-8"))


def load_candidates() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((DATA / "candidates").glob("*.json"))]


TEAM = load_team()
PEOPLE = TEAM["people"]
PROJECT = dict(TEAM["project"], custom_id=f"{PREFIX}-{TEAM['project']['custom_id']}")


def person_custom_id(key: str) -> str:
    return f"{PREFIX}-staff-{key}"


def candidate_custom_id(c: dict) -> str:
    # In your ATS integration this is the ATS candidate id; the prefix keeps demo data apart from yours.
    return f"{PREFIX}-{c['candidate_id']}"


def session_custom_id(c: dict, stage: dict) -> str:
    return f"{PREFIX}-{c['key']}-{stage['stage']}"


def all_sessions(c: dict) -> list[dict]:
    return list(c["stages"]) + ([c["reapply"]] if c.get("reapply") else [])


def scorecard_text(stage: dict) -> str:
    who = PEOPLE[stage["interviewer"]]["display"]
    return f"[scorecard · {stage['stage']} · {who} · {stage['date'][:10]}] {stage['scorecard']}"


SCORECARD = re.compile(r"^\[scorecard · ([\w-]+) · ([^·]+?) · (\d{4}-\d{2}-\d{2})\]\s*")


# --------------------------------------------------------------------------- step 2: setup

def ensure_actor(cli: CLI, custom_id: str, display: str, description: str, tags: str) -> str:
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", display,
                        "--type", "HUMAN", "--tags", tags, "--description", description)
        note(f"created actor {display} → {actor['id']}")
    else:
        note(f"actor {display} already exists → {actor['id']}")
    try:
        # Binding is what lets this workspace read and write the actor's memory. Step 6 takes it away again.
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"]


def setup_actors(cli: CLI) -> tuple[dict[str, str], dict[str, str]]:
    staff = {}
    for key, p in PEOPLE.items():
        staff[key] = ensure_actor(cli, person_custom_id(key), p["display"], f"{p['title']}, {TEAM['company']}", p["tags"])
        emit("person", "", key=key, id=staff[key], display=p["display"], title=p["title"])
    cands = {}
    for c in load_candidates():
        cands[c["key"]] = ensure_actor(cli, candidate_custom_id(c), c["display"],
                                       f"Candidate {c['candidate_id']} for {TEAM['role']}", c["tags"])
        emit("candidate", "", key=c["key"], id=cands[c["key"]], display=c["display"], candidate_id=c["candidate_id"])
    return staff, cands


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def ensure_project(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    emit("project", "", id=proj["id"], name=proj["name"])
    return proj["id"]


# --------------------------------------------------------------------------- step 3: the interview stages

def append_session(cli: CLI, project_id: str, c: dict, stage: dict, staff: dict[str, str], cands: dict[str, str]) -> tuple[str, int]:
    """One conversation per stage, between the candidate and that stage's interviewer. Returns (id, turns appended)."""
    cand_id, int_id = cands[c["key"]], staff[stage["interviewer"]]
    custom = session_custom_id(c, stage)
    conv = cli.try_run("conv", "get", custom, "--by-custom-id", scoped=True)
    done = 0
    if conv is not None and project_id not in (conv.get("rw_project_ids") or []):
        cli.run("conv", "delete", conv["id"], scoped=True)
        conv = None
    if conv is None:
        conv = cli.run("conv", "create", "--custom-id", custom, "--project", project_id,
                       "--actors", f"{cand_id},{int_id}", "--kind", "DIRECT",
                       "--name", f"{c['display']} — {stage['title']}",
                       "--metadata", f"stage={stage['stage']}", "--metadata", f"candidate_id={c['candidate_id']}", scoped=True)
    else:
        done = len((cli.run("conv", "msg", "list", conv["id"], "--page-size", "50") or {}).get("items") or [])
    start = datetime.fromisoformat(stage["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
    parent = conv.get("current_message_id")
    for i, (who, text) in enumerate(stage["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(seconds=45 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv["id"], "--actor", cand_id if who == c["key"] else int_id,
                "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", text]
        if parent:
            args += ["--parent", parent]
        parent = cli.run(*args, scoped=True)["id"]
    added = max(0, len(stage["turns"]) - done)
    note(f"{c['display']} — {stage['title']} ({stage['date'][:10]}, with {PEOPLE[stage['interviewer']]['display']}): "
         + (f"{added} message(s) appended" if added else f"already has {done} message(s)"))
    emit("session", "", candidate=c["key"], stage=stage["stage"], title=stage["title"], date=stage["date"][:10],
         interviewer=stage["interviewer"], id=conv["id"], turns=len(stage["turns"]), added=added)
    return conv["id"], added


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> None:
    pending = set(conv_ids)
    t0 = last_report = time.time()
    first = True
    while pending and time.time() - t0 < timeout:
        for cid in sorted(pending):
            try:
                status = cli.run("conv", "cook-status", cid, scoped=True, echo=first)
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                continue
            if status.get("cook_finished"):
                pending.discard(cid)
                emit("cooked", "", id=cid)
        first = False
        if pending:
            if time.time() - last_report >= 30:
                emit("progress", f"  … {len(pending)} conversation(s) still being turned into memory ({int(time.time() - t0)}s)",
                     pending=len(pending), elapsed=int(time.time() - t0))
                last_report = time.time()
            time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} conversation(s) still processing server-side")
    else:
        note(f"memory ready in {int(time.time() - t0)}s")


def pin_scorecard(cli: CLI, c: dict, stage: dict, cand_id: str) -> None:
    # The interviewer's private note becomes a fact on the candidate, stamped with stage, author and date.
    cli.run("fact", "add", "--actor", cand_id, scorecard_text(stage), scoped=True)
    emit("scorecard", "", candidate=c["key"], stage=stage["stage"], interviewer=stage["interviewer"], scorecard=stage["scorecard"])


def run_stages(cli: CLI, project_id: str, staff: dict[str, str], cands: dict[str, str]) -> None:
    candidates = load_candidates()
    rounds = max(len(c["stages"]) for c in candidates)
    for r in range(rounds):
        # Stages of one candidate are cooked in order (a later interview can revise an earlier answer);
        # different candidates are independent, so their stage-r conversations cook side by side.
        batch = []
        for c in candidates:
            if r < len(c["stages"]):
                stage = c["stages"][r]
                cid, added = append_session(cli, project_id, c, stage, staff, cands)
                batch.append((c, stage, cid, added))
        fresh = [(c, stage, cid) for c, stage, cid, added in batch if added]
        if fresh:
            wait_for_memory(cli, [cid for _, _, cid in fresh])
        for c, stage, _ in fresh:  # pin only alongside the turns this run wrote; a resumed run already has the note
            pin_scorecard(cli, c, stage, cands[c["key"]])
        note(f"stage “{batch[0][1]['title']}”: {len(fresh)} scorecard(s) pinned"
             + (f", {len(batch) - len(fresh)} already in memory from an earlier run" if len(fresh) < len(batch) else ""))


# --------------------------------------------------------------------------- reading memory

def fact_date(f: dict) -> str:
    m = SCORECARD.match(f.get("fact", ""))
    if m:
        return m.group(3)
    # Extracted facts carry "(as of D)", or "(as of D, previously … as of D0)" once a later stage revised them.
    m = re.search(r"\(as of (\d{4}-\d{2}-\d{2})", f.get("fact", ""))
    return m.group(1) if m else (f.get("created_at") or "")[:10]


def list_facts(cli: CLI, actor_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", "--actors", actor_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            break
    return sorted(facts, key=fact_date)


def source_of(c: dict, f: dict) -> dict:
    """Which stage a fact came from: the scorecard stamp, or the extracted fact's as-of date."""
    text = f.get("fact", "")
    m = SCORECARD.match(text)
    if m:
        return dict(kind="scorecard", stage=m.group(1), who=m.group(2).strip(), date=m.group(3), text=text[m.end():])
    d = fact_date(f)
    for s in all_sessions(c):
        sd = s["date"][:10]
        nd = (datetime.fromisoformat(sd) + timedelta(days=1)).strftime("%Y-%m-%d")
        # The extractor dates facts in UTC+8, so a 09:00Z conversation can be stamped the next day.
        if d in (sd, nd):
            return dict(kind="said", stage=s["stage"], who=c["display"], date=sd, text=text, revised="previously" in text)
    return dict(kind="said", stage="?", who=c["display"], date=d, text=text, revised="previously" in text)


def label_of(a: dict) -> str:
    if a["kind"] == "scorecard":
        return f"{a['stage']} · {a['who']} · {a['date']}"
    return f"{a['stage']} · said by candidate{' · revises an earlier answer' if a.get('revised') else ''} · {a['date']}"


def search_facts(cli: CLI, actor_ids: list[str], query: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", query, "--actors", ",".join(actor_ids), "--types", "fact", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    return [dict(id=f.get("id"), fact=f.get("fact") or "", score=f.get("score")) for f in res.get("facts") or []]


def matches(text: str, guards: list[str]) -> bool:
    t = text.lower()
    return any(g.lower() in t for g in guards)


def debrief(cli: CLI, c: dict, cand_id: str) -> dict:
    facts = list_facts(cli, cand_id)
    pinned = [f for f in facts if SCORECARD.match(f["fact"])]
    emit("text", f"\n  {c['display']} ({c['candidate_id']}) — {len(facts)} facts on the candidate: "
                 f"{len(pinned)} interviewer scorecards + {len(facts) - len(pinned)} extracted from what they said")
    rows = []
    for q in TEAM["debrief"]:
        if q["key"] == "recommendation":
            # Every scorecard, not a ranked guess: the debrief must hear all three interviewers, not the loudest.
            hits = kept = pinned
            answers = [dict(id=f["id"], **source_of(c, f)) for f in pinned]
            for a in answers:
                a["text"] = re.split(r"(?<=\.)\s", a["text"], maxsplit=1)[0]
        else:
            hits = search_facts(cli, [cand_id], q["query"])
            kept = [h for h in hits if matches(h["fact"], q["guards"])]
            answers = [dict(id=h["id"], **source_of(c, h)) for h in kept]
        lines = [f"\n  {q['question']}"]
        for a in answers:
            lines.append(f"    [{label_of(a)}] {a['text']}")
        if not answers:
            lines.append("    (nothing in memory)")
        if len(hits) > len(kept):
            lines.append(f"    ({len(hits) - len(kept)} other hit(s) left out: they do not mention {', '.join(q['guards'][:3])} …)")
        emit("text", "\n".join(lines))
        stages = sorted({a["stage"] for a in answers})
        rows.append(dict(key=q["key"], question=q["question"], answers=answers, stages=stages, left_out=len(hits) - len(kept)))
    covered = sum(1 for r in rows if r["answers"])
    multi = sum(1 for r in rows if len(r["stages"]) > 1)
    note(f"{covered}/{len(rows)} debrief questions answered from memory; {multi} of them draw on more than one stage")
    emit("debrief", "", candidate=c["key"], display=c["display"], total=len(facts), pinned=len(pinned),
         facts=[dict(id=f["id"], **source_of(c, f)) for f in facts], rows=rows, covered=covered, multi=multi)
    return dict(facts=facts, rows=rows)


# --------------------------------------------------------------------------- step 5: recruiter rotation

def active_candidates(cli: CLI) -> list[dict]:
    """The candidates this workspace may read right now: its ACTIVE bindings tagged `candidate`."""
    out, token = [], None
    while True:
        args = ["actor", "list", "--tags", "candidate", "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=token is None) or {}
        out.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            break
    # Deleted actors linger as INACTIVE bindings; only ACTIVE ones can be searched.
    return [a for a in out if a.get("status") == "ACTIVE" and (a.get("custom_id") or "").startswith(PREFIX)]


def pipeline_search(cli: CLI, cands: dict[str, str], label: str) -> dict:
    h = TEAM["handover"]
    active = active_candidates(cli)
    names = {a["actor_id"]: a["display_name"] for a in active}
    note(f"{label}: {len(active)} candidate(s) bound to this workspace — " + ", ".join(names.values()))
    ids = list(names)
    owner = {}
    for c in load_candidates():
        if cands[c["key"]] in names:
            for f in list_facts(cli, cands[c["key"]], echo=False):
                owner[f["id"]] = c
    hits = search_facts(cli, ids, h["query"], top_k=10) if ids else []
    by = {}
    for x in hits:
        c = owner.get(x["id"])
        if c is None:
            continue
        by.setdefault(c["display"], []).append(source_of(c, x))
    lines = []
    for name in names.values():
        lines.append(f"\n  {name}: {len(by.get(name, []))} hit(s)")
        for a in by.get(name, [])[:4]:
            lines.append(f"    [{label_of(a)}] {a['text'][:150]}")
    emit("text", "\n".join(lines))
    result = dict(label=label, active=[dict(id=k, display=v) for k, v in names.items()],
                  hits={k: v for k, v in by.items()}, query=h["query"])
    emit("pipeline", "", **result)
    return result


def handover(cli: CLI, cands: dict[str, str]) -> dict:
    h = TEAM["handover"]
    frm, to = PEOPLE[h["from"]]["display"], PEOPLE[h["to"]]["display"]
    emit("text", f"\n  {h['date']}: {frm} moves to another team; {to} takes over the requisition.\n"
                 f"  Nothing is handed over by hand — the memory sits on the candidates, not in {frm.split()[0]}'s notes.\n"
                 f"  {to.split()[0]} asks the whole pipeline one question: “{h['query']}”")
    return pipeline_search(cli, cands, f"{to.split()[0]}'s pipeline")


# --------------------------------------------------------------------------- step 6: a candidate withdraws

def refused(cli: CLI, what: str, *args: str) -> dict:
    rc, out, err = cli.raw(*args, scoped=True)
    msg = (err or out).strip().splitlines()
    reason = next((l.strip() for l in msg if "bound" in l.lower() or "not found" in l.lower()), msg[-1] if msg else "")
    reason = re.sub(r"\s*\[[A-Z_]+\]$", "", reason)
    code = re.search(r"HTTP (\d{3})", err or "")
    ok = rc != 0
    note(f"{what}: " + (f"refused (HTTP {code.group(1) if code else '?'}) — {reason}" if ok else "WARNING: still allowed"))
    return dict(what=what, refused=ok, http=code.group(1) if code else None, reason=reason)


def withdraw(cli: CLI, project_id: str, c: dict, cand_id: str) -> dict:
    w = c["withdraw"]
    before = list_facts(cli, cand_id)
    emit("text", f"\n  {w['reason']}\n  Before: {len(before)} facts on {c['display']}. Now unbind the candidate from this workspace:")
    cli.run("actor", "unbind", "--actor", cand_id, scoped=True)
    checks = [
        refused(cli, "read the candidate's facts", "fact", "list", "--actors", cand_id),
        refused(cli, "search the candidate's memory", "search", "compensation", "--actors", cand_id, "--types", "fact"),
        refused(cli, "write a new message as the candidate", "conv", "msg", "append",
                cli.run("conv", "get", session_custom_id(c, c["stages"][0]), "--by-custom-id", scoped=True, echo=False)["id"],
                "--actor", cand_id, "--custom-id", "after-withdrawal", "--text", f"{c['display']} (candidate): hello again"),
    ]
    actor = cli.run("actor", "get", cand_id)
    note(f"the actor itself is kept: {actor['display_name']} ({actor['id']}) — unbind is a pause, not an erasure")
    transcripts = 0
    for s in c["stages"]:
        conv = cli.try_run("conv", "get", session_custom_id(c, s), "--by-custom-id", scoped=True)
        if conv:
            transcripts += len((cli.run("conv", "msg", "list", conv["id"], "--page-size", "50", echo=False) or {}).get("items") or [])
    note(f"the raw interview transcripts are not sealed ({transcripts} messages still listed); "
         "for an erasure request delete the conversations and the actor too (`python3 demo.py cleanup` shows how)")
    # The pipeline keeps a record that the candidate withdrew — on the project, not on the candidate.
    record = f"[pipeline · {w['date']}] {w['reason']}"
    existing = (cli.run("fact", "list", "--projects", project_id, "--page-size", "50", scoped=True, echo=False) or {}).get("items") or []
    if not any(f.get("fact") == record for f in existing):
        cli.run("fact", "add", "--project", project_id, record, scoped=True)
    result = dict(candidate=c["key"], display=c["display"], before=[f["id"] for f in before], checks=checks,
                  kept=actor["id"], transcripts=transcripts, reason=w["reason"], date=w["date"])
    emit("withdraw", "", **result)
    return result


# --------------------------------------------------------------------------- step 7: the candidate comes back

def reapply(cli: CLI, project_id: str, c: dict, cand_id: str, staff: dict[str, str], cands: dict[str, str], before: list[str]) -> dict:
    r = c["reapply"]
    emit("text", f"\n  {r['date'][:10]}: {c['display']} applies again, for “{r['opening']}”. Bind the same actor back:")
    cli.run("actor", "bind", "--actor", cand_id, scoped=True)
    after = list_facts(cli, cand_id)
    same = sorted(f["id"] for f in after) == sorted(before)
    note(f"{len(before)} facts before withdrawal · refused while unbound · {len(after)} after re-binding — "
         + ("identical fact ids, nothing lost" if same else "fact ids differ (see out/)"))
    cid, added = append_session(cli, project_id, c, r, staff, cands)
    if added:
        wait_for_memory(cli, [cid])
        pin_scorecard(cli, c, r, cand_id)
    q = next(x for x in TEAM["debrief"] if x["key"] == "compensation")
    hits = [h for h in search_facts(cli, [cand_id], q["query"]) if matches(h["fact"], q["guards"])]
    answers = [source_of(c, h) for h in hits]
    lines = [f"\n  {q['question']} — September and November, one search:"]
    lines += [f"    [{label_of(a)}] {a['text'][:160]}" for a in answers] or ["    (nothing in memory)"]
    emit("text", "\n".join(lines))
    stages = sorted({a["stage"] for a in answers})
    note(f"answers come from {len(stages)} stage(s): {', '.join(stages)}")
    result = dict(candidate=c["key"], display=c["display"], opening=r["opening"], before=len(before), after=len(after),
                  identical=same, answers=answers, stages=stages)
    emit("reapply", "", **result)
    return result


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI) -> None:
    n = 0
    for c in load_candidates():
        for s in all_sessions(c):
            conv = cli.try_run("conv", "get", session_custom_id(c, s), "--by-custom-id", scoped=True)
            if conv:
                cli.run("conv", "delete", conv["id"], scoped=True)
                n += 1
    note(f"deleted {n} interview conversation(s)")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the pipeline project")
    for c in load_candidates():
        actor = cli.try_run("actor", "get", candidate_custom_id(c), "--by-custom-id")
        if actor:
            # Deleting the actor deletes every fact on it: this is the erasure, unlike step 6's unbind.
            cli.run("actor", "delete", actor["id"])
            note(f"deleted candidate {c['display']} and every fact on them")
    for key, p in PEOPLE.items():
        actor = cli.try_run("actor", "get", person_custom_id(key), "--by-custom-id")
        if actor:
            cli.run("actor", "delete", actor["id"])
    note("deleted the recruiting-team actors")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    if reset:
        note("--reset: removing everything from earlier runs first")
        cleanup(cli)
    banner(2, TOTAL, "Set up — recruiting team, two candidates, one pipeline project")
    staff, cands = setup_actors(cli)
    project_id = ensure_project(cli)

    banner(3, TOTAL, "Three stages, three interviewers — every interview becomes memory on the candidate")
    run_stages(cli, project_id, staff, cands)

    banner(4, TOTAL, "Debrief — three private scorecards and the candidate's own words, in one view")
    report = {"debrief": {}}
    for c in load_candidates():
        report["debrief"][c["key"]] = debrief(cli, c, cands[c["key"]])["rows"]

    banner(5, TOTAL, "Recruiter rotation — the new recruiter asks the whole pipeline one question")
    report["handover"] = handover(cli, cands)

    leaver = next(c for c in load_candidates() if c.get("withdraw"))
    banner(6, TOTAL, f"{leaver['display'].split()[0]} withdraws — unbind seals his memory in this workspace")
    w = withdraw(cli, project_id, leaver, cands[leaver["key"]])
    report["withdraw"] = w
    report["after_withdraw"] = pipeline_search(cli, cands, "the pipeline after the withdrawal")

    banner(7, TOTAL, f"{leaver['display'].split()[0]} applies again — bind brings the same memory back")
    report["reapply"] = reapply(cli, project_id, leaver, cands[leaver["key"]], staff, cands, w["before"])

    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    note(f"full report written to {OUT / 'report.json'}")
    return report


def lookup(cli: CLI) -> dict[str, str]:
    cands = {}
    for c in load_candidates():
        a = cli.try_run("actor", "get", candidate_custom_id(c), "--by-custom-id")
        if a is None:
            die("the demo candidates do not exist yet; run `python3 demo.py` first")
        cands[c["key"]] = a["id"]
    return cands


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "brief", "cleanup"],
                    help="run = full demo (default); brief = print the debrief again (read-only); cleanup = delete everything")
    ap.add_argument("candidate", nargs="?", help="for `brief`: priya or tomas (default: both)")
    ap.add_argument("--reset", action="store_true", help="delete everything from earlier runs and start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = {"cleanup": 2, "brief": 2}.get(args.command, TOTAL)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "brief":
        banner(2, 2, "Debrief — what memory holds on each candidate (read-only)")
        cands = lookup(cli)
        for c in load_candidates():
            if args.candidate in (None, c["key"]):
                debrief(cli, c, cands[c["key"]])
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py brief priya` prints a debrief again; `python3 demo.py cleanup` removes the demo data.")


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

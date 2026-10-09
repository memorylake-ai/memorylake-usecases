#!/usr/bin/env python3
"""
Conflict check memory for a small law firm — a MemoryLake demo driven entirely
by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Hollis & Reyes LLP (fictional) keeps its firm history in MemoryLake: a project
with the matter index pinned as facts and the archived engagement and closing
letters imported as documents, plus one actor per party the firm has ever met
(client, adverse party, witness).

Senior partner Elena Hollis is on leave. Theo Lindqvist, an associate who joined
in September, runs three new-matter intakes. Each one gets two independent checks:

  1. the party check — every party is looked up in per-party memory and searched
     across firm history, and the firm's own rules decide (clear / conflict);
  2. MemoryLake's contradiction detector (`fact conflict`) — when the intake is
     recorded, the server compares it with every fact and document in the firm
     history and raises a conflict when they cannot both be true.

The third intake form says "no prior relationship with Brightline Properties LLC"
(it was filled from a CRM that starts in 2021). The party check flags Brightline
as a former client, and the detector raises two conflicts: one against the 2019
matter record, one against the 2020 closing letter, quoting it. Theo resolves both
(`keep_fact`, `trust_document`), the wrong claim is forgotten, and the resolved
conflicts become the audit trail for the check.

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

PREFIX = "mlu-ccm"  # memorylake-usecases / conflict-check-memory

PROJECT = dict(
    custom_id=f"{PREFIX}-firm-history",
    name="Hollis & Reyes — firm history",
    description="Every matter, party and archived letter of Hollis & Reyes LLP. Demo data.",
)

# The detector runs right after a fact is written to a quiet project. Back-to-back writes are
# checked later, as one batch, minutes later (measured: 6–8 min). So every write to the firm
# history waits until the previous one is GAP seconds old.
GAP = 15
DETECT_WAIT = 45     # how long an intake waits for the detector (it usually answers in 10–20 s)


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

def load_firm() -> dict:
    return json.loads((DATA / "firm.json").read_text(encoding="utf-8"))


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def party_cid(slug: str) -> str:
    return cid(f"party-{slug}")


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def mentions(text: str, name: str) -> bool:
    """Does a fact or a document summary name this party? (‘LLC’, ‘Inc.’ and ‘Co.’ are optional.)"""
    core = re.sub(r"\b(LLC|Inc\.?|Co\.?)$", "", name).strip()
    return core.lower() in (text or "").lower()


# --------------------------------------------------------------------------- pacing

class Pace:
    """Keeps writes to the firm history GAP seconds apart, so the detector checks each one as it lands."""

    def __init__(self) -> None:
        self.last = 0.0

    def wait(self) -> None:
        left = GAP - (time.time() - self.last)
        if left > 0:
            time.sleep(left)

    def wrote(self) -> None:
        self.last = time.time()


PACE = Pace()


# --------------------------------------------------------------------------- step 2: firm history

def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def find_party(cli: CLI, slug: str, echo: bool = True):
    try:
        return cli.run("actor", "get", party_cid(slug), "--by-custom-id", echo=echo)
    except CLIError as e:
        if e.has("404", "not found"):
            return None
        raise


def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", flag, scope_id, "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return facts


def list_documents(cli: CLI, project_id: str, echo: bool = True) -> list[dict]:
    page = cli.run("proj", "doc", "list", "--project", project_id, scoped=True, echo=echo) or {}
    return page.get("items") or []


def list_conflicts(cli: CLI, project_id: str, *extra: str, echo: bool = True) -> list[dict]:
    items, token = [], None
    while True:
        args = ["fact", "conflict", "list", "--project", project_id, "--page-size", "100", *extra]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        items.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return items


def ensure_project(cli: CLI) -> str:
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    return proj["id"]


def import_letters(cli: CLI, project_id: str, firm: dict) -> list[dict]:
    have = {d.get("name") for d in list_documents(cli, project_id)}
    todo = [l for l in firm["letters"] if Path(l["file"]).name not in have]
    if todo:
        ids = []
        for l in todo:
            # `overwrite` keeps one Library item per file across re-runs.
            item = cli.run("lib", "upload", os.path.relpath(DATA / l["file"]), "--on-conflict", "overwrite")
            ids.append(item["item_id"])
            note(f"uploaded {item['name']}")
        t0 = time.time()
        res = cli.run("proj", "doc", "import", "--project", project_id, *ids, "--wait", scoped=True) or {}
        note(f"imported {res.get('success_count', 0)} letter(s) into the firm history ({int(time.time() - t0)}s)")
    else:
        note("both archived letters are already in the firm history")
    docs = list_documents(cli, project_id, echo=False)
    return [dict(id=d.get("id"), name=d.get("name"), status=d.get("status")) for d in docs]


def ensure_party(cli: CLI, p: dict) -> dict:
    actor = find_party(cli, p["slug"])
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", party_cid(p["slug"]), "--display-name", p["name"],
                        "--tags", f"party,{p['role']},{p['status']}", "--description", f"Matter {p['matter']}")
        note(f"created party {p['name']} → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    facts = list_facts(cli, "--actors", actor["id"], echo=False)
    if not any(f.get("fact") == p["fact"] for f in facts):
        cli.run("fact", "add", "--actor", actor["id"], p["fact"], scoped=True)
    return dict(id=actor["id"], name=p["name"], role=p["role"], status=p["status"], matter=p["matter"], fact=p["fact"])


def setup_history(cli: CLI, firm: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting the firm history, the parties and the uploaded letters, then starting over")
        cleanup(cli, quiet=True)
    project_id = ensure_project(cli)
    letters = import_letters(cli, project_id, firm)
    emit("letters", "", project=dict(id=project_id, name=PROJECT["name"]), letters=letters)

    have = {f.get("fact") for f in list_facts(cli, "--projects", project_id)}
    todo = [m for m in firm["matters"] if m not in have]
    by_matter: dict[str, list[dict]] = {}
    for p in firm["parties"]:
        by_matter.setdefault(p["matter"], []).append(p)
    parties: dict[str, dict] = {}
    if todo:
        note(f"pinning {len(todo)} matter record(s), one every {GAP}s — back-to-back writes are checked for "
             "contradictions minutes later, as a batch; one at a time, each is checked as it lands")
    for m in firm["matters"]:
        if m in todo:
            PACE.wait()
            cli.run("fact", "add", "--project", project_id, m, scoped=True)
            PACE.wrote()
            emit("matter", "", record=m)
        # Each matter's parties are created while the next write waits its turn.
        num = re.search(r"Matter (\d{4}-\d{3})", m)
        for p in by_matter.pop(num.group(1) if num else "", []):
            parties[p["slug"]] = ensure_party(cli, p)
            emit("party", "", **parties[p["slug"]])
    for rest in by_matter.values():
        for p in rest:
            parties[p["slug"]] = ensure_party(cli, p)
            emit("party", "", **parties[p["slug"]])
    if not todo:
        note("the matter index is already pinned")
        for m in firm["matters"]:
            emit("matter", "", record=m)

    index = cli.run("actor", "list", "--tags", "party", "--page-size", "100") or {}
    n = len([a for a in index.get("items") or [] if (a.get("custom_id") or "").startswith(PREFIX + "-")])
    open_now = list_conflicts(cli, project_id, "--resolved", "false")
    emit("text", f"\n  Firm history: {len(firm['matters'])} matter records, {len(letters)} archived letters, "
                 f"{n} parties (`actor list --tags party`).\n  Open conflicts in the firm history before today: {len(open_now)}")
    emit("history", "", project=dict(id=project_id, name=PROJECT["name"]), matters=firm["matters"],
         letters=letters, parties=list(parties.values()), open_conflicts=len(open_now))
    return dict(project=project_id, parties=parties)


# --------------------------------------------------------------------------- steps 3–5: intakes

def rule_for(known: dict | None, side: str) -> tuple[str, str]:
    """The firm's own conflict rules (data/firm.json → rules), applied to one party."""
    if known is None:
        return "clear", "no record of this party anywhere in firm history"
    role, status = known["role"], known["status"]
    if side == "adverse" and role == "client":
        return "conflict", f"{status} client (Matter {known['matter']}) would be the adverse party — needs its informed written consent"
    if side == "client" and role == "adverse" and status == "current":
        return "conflict", f"current adverse party in open Matter {known['matter']}"
    if role == "witness":
        return "review", f"witness the firm deposed in Matter {known['matter']} — partner review"
    return "clear", f"known party ({status} {role}, Matter {known['matter']}); no rule applies"


def party_check(cli: CLI, project_id: str, party: dict, side: str, echo: bool = True) -> dict:
    """Per-party memory (is there an actor for this party, and what is pinned on it) + a search of the firm history."""
    actor = find_party(cli, party["slug"], echo=echo)
    pinned = [f.get("fact") for f in list_facts(cli, "--actors", actor["id"], echo=echo)] if actor else []
    known = None
    if actor:
        tags = set(actor.get("tags") or [])
        role = next((t for t in ("client", "adverse", "witness") if t in tags), "")
        status = "current" if "current" in tags else "former"
        known = dict(id=actor["id"], role=role, status=status, matter=(actor.get("description") or "").replace("Matter ", ""))
    res = cli.run("search", party["name"], "--projects", project_id, "--types", "fact", "--top-k", "8",
                  scoped=True, echo=echo) or {}
    facts = [f for f in res.get("facts") or [] if mentions(f.get("fact") or f.get("content") or "", party["name"])]
    other = len(res.get("facts") or []) - len(facts)
    docs_res = cli.run("search", party["name"], "--projects", project_id, "--types", "document", "--top-k", "3",
                       scoped=True, echo=echo) or {}
    docs = [d for d in docs_res.get("documents") or [] if mentions(d.get("document_summary") or "", party["name"])]
    verdict, why = rule_for(known, side)
    return dict(name=party["name"], slug=party["slug"], side=side, known=known, pinned=pinned,
                facts=[f.get("fact") or f.get("content") for f in facts], left_out=other,
                documents=[dict(name=d.get("document_name") or d.get("file_name"), summary=clip(d.get("document_summary") or "", 220)) for d in docs],
                verdict=verdict, why=why)


def show_party(c: dict) -> None:
    mark = {"clear": "✓ clear", "conflict": "✗ CONFLICT", "review": "! review"}[c["verdict"]]
    lines = [f"\n  {c['name']} ({'prospective client' if c['side'] == 'client' else 'adverse party'})  →  {mark}",
             f"    rule: {c['why']}"]
    if c["known"]:
        lines.append(f"    per-party memory: actor {c['known']['id']}  tags party,{c['known']['role']},{c['known']['status']}")
        lines += [f"      · {clip(p, 150)}" for p in c["pinned"]]
    else:
        lines.append("    per-party memory: no actor for this party")
    lines.append(f"    firm history: {len(c['facts'])} matter record(s), {len(c['documents'])} letter(s) name it"
                 + (f"  ({c['left_out']} other hit(s) left out: they do not name this party)" if c["left_out"] else ""))
    lines += [f"      · {clip(f, 150)}" for f in c["facts"]]
    lines += [f"      · [{d['name']}] {clip(d['summary'], 130)}" for d in c["documents"]]
    emit("text", "\n".join(lines))


def intake_fact(cli: CLI, project_id: str, text: str) -> dict | None:
    return next((f for f in list_facts(cli, "--projects", project_id, echo=False) if f.get("fact") == text), None)


def recorded_before(cli: CLI, project_id: str, text: str) -> list[dict]:
    """Conflicts (open or resolved) that already carry this text: the intake was recorded by an earlier run."""
    return [c for c in list_conflicts(cli, project_id, echo=False)
            if any(s.get("fact_text") == text for s in c.get("fact_snapshots") or [])]


def conflict_view(c: dict) -> dict:
    return dict(id=c["id"], name=c.get("name"), description=c.get("description"), category=c.get("category"),
                conflict_type=c.get("conflict_type"), resolved=c.get("resolved"), fact_ids=c.get("fact_ids") or [],
                facts=[dict(id=s.get("fact_id"), text=s.get("fact_text")) for s in c.get("fact_snapshots") or []],
                chunks=[dict(document=k.get("document_name"), document_id=k.get("document_id"), text=(k.get("text") or "").strip())
                        for k in c.get("file_chunks") or []],
                created_at=c.get("created_at"), resolve=c.get("resolve"))


def sentences_naming(text: str, names: list[str]) -> list[str]:
    """The sentences of a document excerpt that name one of these parties (headings and addresses left out)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" in line[:12] or line.endswith(","):
            continue
        out += [s.strip() for s in re.split(r"(?<=[a-z0-9][.!?])\s+", line) if any(mentions(s, n) for n in names)]
    return out


def show_conflicts(conflicts: list[dict], header: str, names: list[str]) -> None:
    lines = [header]
    for c in conflicts:
        lines.append(f"\n  [{c['category']} · {c['conflict_type']}] {c['name']}   {c['id']}")
        lines.append(f"    {clip(c['description'], 260)}")
        for f in c["facts"]:
            lines.append(f"    fact {f['id']}: {clip(f['text'], 240)}")
        for k in c["chunks"]:
            quoted = sentences_naming(k["text"], names) or [clip(k["text"], 200)]
            lines.append(f"    document {k['document']} — the excerpt says:")
            lines += [f"      “{clip(q, 220)}”" for q in quoted]
    emit("text", "\n".join(lines))


def watch_detector(cli: CLI, project_id: str, fact_id: str, want: set[str]) -> list[dict]:
    """Poll `fact conflict list` until conflicts naming this fact show up (all of `want`, if given)."""
    t0, found, first = time.time(), [], True
    while time.time() - t0 < DETECT_WAIT:
        time.sleep(5)
        cur = list_conflicts(cli, project_id, "--resolved", "false", echo=first)
        first = False
        found = [conflict_view(c) for c in cur if fact_id in (c.get("fact_ids") or [])]
        if want and want <= {c["category"] for c in found}:
            break
    emit("detector_wait", "", seconds=int(time.time() - t0))
    return found


def run_intake(cli: CLI, ids: dict, firm: dict, intake: dict) -> dict:
    project_id = ids["project"]
    emit("text", f"\n  New matter: {intake['title']} — {intake['matter']} (intake {intake['date']})"
                 + (f"\n  {intake['form_note']}" if intake.get("form_note") else ""))
    emit("intake", "", key=intake["key"], title=intake["title"], date=intake["date"], matter=intake["matter"],
         record=intake["record"], form_note=intake.get("form_note"))

    # Check 1 — the party check: per-party memory + firm-history search, judged by the firm's rules.
    checks = [party_check(cli, project_id, intake["client"], "client")]
    checks += [party_check(cli, project_id, a, "adverse") for a in intake["adverse"]]
    for c in checks:
        show_party(c)
    verdict = "conflict" if any(c["verdict"] == "conflict" for c in checks) else (
        "review" if any(c["verdict"] == "review" for c in checks) else "clear")
    emit("party_check", "", key=intake["key"], checks=checks, verdict=verdict)
    emit("text", f"\n  Party check: {verdict.upper()}")

    # Check 2 — record the intake in the firm history; the server compares it with everything there.
    existing = intake_fact(cli, project_id, intake["record"])
    earlier = recorded_before(cli, project_id, intake["record"]) if existing is None else []
    if existing is None and earlier:
        note("this intake was recorded and resolved by an earlier run; showing what the detector raised then")
        detected, fact_id, fresh = [conflict_view(c) for c in earlier], None, False
    else:
        if existing is None:
            PACE.wait()
            fact = cli.run("fact", "add", "--project", project_id, intake["record"], scoped=True)
            PACE.wrote()
            fact_id = ((fact or {}).get("facts") or [{}])[0].get("id")
            note(f"intake recorded → {fact_id}; waiting up to {DETECT_WAIT}s for the contradiction detector")
        else:
            fact_id = existing["id"]
            note(f"intake already recorded → {fact_id} (earlier run); reading what the detector raised")
        want = {"m2m", "m2d"} if intake["expect"] == "conflict" and intake.get("corrected") else set()
        if existing is None:
            detected, fresh = watch_detector(cli, project_id, fact_id, want), True
        else:
            detected, fresh = [conflict_view(c) for c in list_conflicts(cli, project_id)
                               if fact_id in (c.get("fact_ids") or [])], False
    if detected:
        show_conflicts(detected, f"\n  The detector raised {len(detected)} conflict(s) naming this intake:",
                       [a["name"] for a in intake["adverse"]])
    else:
        emit("text", f"\n  The detector raised nothing naming this intake"
                     + (f" within {DETECT_WAIT}s." if fresh else ".")
                     + ("" if intake["expect"] == "clear" or intake.get("corrected") else
                        "\n  It looks for statements that cannot both be true. “Penn wants to sue Marlow” contradicts nothing —"
                        "\n  it is a conflict of interest, which is the firm's rule to apply, and the party check applied it."))
    emit("detector", "", key=intake["key"], fact_id=fact_id, conflicts=detected, fresh=fresh)
    return dict(key=intake["key"], title=intake["title"], verdict=verdict, checks=checks, fact_id=fact_id,
                detected=detected)


# --------------------------------------------------------------------------- step 6: resolve

def resolve_intake(cli: CLI, ids: dict, firm: dict, result: dict) -> dict:
    project_id = ids["project"]
    intake = next(i for i in firm["intakes"] if i.get("corrected"))
    open_ = [conflict_view(c) for c in list_conflicts(cli, project_id, "--resolved", "false")
             if any(s.get("fact_text") == intake["record"] for s in c.get("fact_snapshots") or [])]
    done = []
    if not open_:
        note("no open conflict on this intake (resolved by an earlier run, or the detector has not raised one yet)")
    # Fact vs document first: the archived letter wins and the claim is forgotten.
    for c in sorted(open_, key=lambda c: c["category"] != "m2d"):
        if c["category"] == "m2d":
            r = cli.run("fact", "conflict", "resolve", c["id"], "--project", project_id, "--strategy", "trust_document", scoped=True)
        elif c["category"] == "m2m":
            keep = next((f["id"] for f in c["facts"] if f["text"] and f["text"].startswith("Matter ")), None)
            if keep is None:
                note(f"{c['id']}: no matter record among its facts; leaving it open for partner review")
                continue
            r = cli.run("fact", "conflict", "resolve", c["id"], "--project", project_id, "--strategy", "keep_fact",
                        "--keep-fact-id", keep, scoped=True)
        else:
            note(f"{c['id']}: {c['category']} conflict left open for partner review")
            continue
        note(f"{c['category']} resolved with {r.get('strategy')}: forgotten {', '.join(r.get('forgotten_fact_ids') or []) or 'nothing'}")
        done.append(dict(id=c["id"], category=c["category"], strategy=r.get("strategy"),
                         forgotten=r.get("forgotten_fact_ids") or []))

    # The corrected record goes in — it agrees with the firm history, so nothing should be raised.
    corrected = intake_fact(cli, project_id, intake["corrected"])
    new_conflicts = []
    if corrected is None:
        PACE.wait()
        fact = cli.run("fact", "add", "--project", project_id, intake["corrected"], scoped=True)
        PACE.wrote()
        cid_ = ((fact or {}).get("facts") or [{}])[0].get("id")
        note(f"corrected intake recorded → {cid_}; giving the detector {DETECT_WAIT}s")
        new_conflicts = watch_detector(cli, project_id, cid_, set())
    else:
        note("the corrected intake record is already in the firm history")
    facts = list_facts(cli, "--projects", project_id)
    texts = [f.get("fact") for f in facts]
    claim_gone = intake["record"] not in texts
    emit("text", "\n".join([
        f"\n  Firm history now holds {len(facts)} facts.",
        f"  {'✓' if claim_gone else '✗'} the intake form's claim (“no prior relationship with Brightline Properties LLC”) "
        + ("is gone — forgotten by the resolution" if claim_gone else "is still there"),
        f"  {'✓' if intake['corrected'] in texts else '✗'} the corrected record is there: {clip(intake['corrected'], 120)}",
        f"  {'✓' if not new_conflicts else '✗'} the detector raised {len(new_conflicts)} conflict(s) on the corrected record"]))
    out = dict(resolved=done, claim_gone=claim_gone, corrected=intake["corrected"],
               corrected_conflicts=new_conflicts, facts=len(facts))
    emit("resolved", "", **out)
    return out


# --------------------------------------------------------------------------- step 7: audit trail

def audit(cli: CLI, ids: dict, firm: dict, results: list[dict]) -> Path:
    project_id = ids["project"]
    resolved = [conflict_view(cli.run("fact", "conflict", "get", c["id"], "--project", project_id, scoped=True))
                for c in list_conflicts(cli, project_id, "--resolved", "true")]
    still_open = [conflict_view(c) for c in list_conflicts(cli, project_id, "--resolved", "false", echo=False)]
    lines = [f"# Conflict check log — {firm['firm']}", "",
             f"Run by: {firm['associate']['name']}, {firm['associate']['role']}. Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from MemoryLake "
             f"(project `{project_id}`).", "",
             "Two checks per intake: the party check (per-party memory + firm-history search, judged by the firm's rules) "
             "and MemoryLake's contradiction detector (`fact conflict`).", "", "## Firm rules", ""]
    lines += [f"- {r}" for r in firm["rules"]]
    for r in results:
        lines += ["", f"## {r['title']}", "", f"Party check: **{r['verdict'].upper()}**", ""]
        for c in r["checks"]:
            lines.append(f"- {c['name']} ({c['side']}): {c['verdict']} — {c['why']}")
            lines += [f"  - pinned: {p}" for p in c["pinned"]]
            lines += [f"  - firm history: {f}" for f in c["facts"]]
            lines += [f"  - letter: {d['name']}" for d in c["documents"]]
        lines += ["", f"Detector: {len(r['detected'])} conflict(s) naming the intake record"
                  + (f" (`{r['fact_id']}`)" if r["fact_id"] else "")]
    lines += ["", "## Resolved conflicts (from `fact conflict get`)", ""]
    for c in resolved:
        rs = c["resolve"] or {}
        lines += [f"### {c['name']} — `{c['id']}`", "",
                  f"- {c['category']} · {c['conflict_type']} · raised {c['created_at']} · resolved {rs.get('created_at')}",
                  f"- strategy: `{rs.get('strategy')}`" + (f", kept `{rs.get('keep_fact_id')}`" if rs.get("keep_fact_id") else ""),
                  f"- forgotten: {', '.join('`%s`' % i for i in rs.get('forgotten_fact_ids') or []) or 'none'}",
                  f"- why: {c['description']}"]
        lines += [f"- fact `{f['id']}`: {f['text']}" for f in c["facts"]]
        lines += [f"- document {k['document']}: \"{' / '.join(l.strip() for l in k['text'].splitlines() if l.strip())}\"" for k in c["chunks"]]
        lines.append("")
    lines += ["## Still open", ""] + ([f"- `{c['id']}` {c['category']}: {c['name']}" for c in still_open] or ["- none"])
    OUT.mkdir(exist_ok=True)
    path = OUT / f"conflict-check-{firm['today']}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    show = [f"\n  {len(resolved)} resolved conflict(s), {len(still_open)} open:"]
    for c in resolved:
        rs = c["resolve"] or {}
        show.append(f"\n  {c['id']}  [{c['category']}] {c['name']}")
        show.append(f"    raised   {c['created_at'][:19]}Z")
        show.append(f"    resolved {str(rs.get('created_at'))[:19]}Z  strategy {rs.get('strategy')}"
                    + (f"  kept {rs.get('keep_fact_id')}" if rs.get("keep_fact_id") else ""))
        for f in c["facts"]:
            gone = f["id"] in (rs.get("forgotten_fact_ids") or [])
            show.append(f"    {'forgotten' if gone else 'kept     '} {f['id']}: {clip(f['text'], 110)}")
        for k in c["chunks"]:
            show.append(f"    evidence  {k['document']}")
    show.append(f"\n  The snapshots keep the forgotten claim's exact words — the record of what was checked and why.")
    show.append(f"  Audit log written to {os.path.relpath(path)}")
    emit("text", "\n".join(show))
    emit("audit", "", resolved=resolved, open=still_open, file=os.path.relpath(path), markdown="\n".join(lines))
    return path


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    firm = load_firm()
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the firm history project (its facts, documents and conflicts go with it)")
    n = 0
    for p in firm["parties"]:
        actor = find_party(cli, p["slug"], echo=not quiet)
        if actor:
            cli.run("actor", "delete", actor["id"])
            n += 1
    note(f"deleted {n} party actor(s) and their facts")
    names = {Path(l["file"]).name for l in firm["letters"]}
    removed, token = 0, None
    while True:
        args = ["lib", "list", "MY_SPACE", "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=token is None) or {}
        for item in page.get("items") or []:
            if item.get("name") in names:
                cli.run("lib", "delete", item.get("item_id") or item.get("id"))
                removed += 1
        token = page.get("continuation_token")
        if not token:
            break
    note(f"deleted {removed} uploaded letter(s) from the Library")
    emit("cleaned", "")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    firm = load_firm()
    banner(2, TOTAL, "Firm history — matters, archived letters, one actor per party")
    ids = setup_history(cli, firm, reset)
    results = []
    for n, intake in enumerate(firm["intakes"], start=3):
        banner(n, TOTAL, f"Intake {n - 2} — {intake['title']}")
        results.append(run_intake(cli, ids, firm, intake))
    banner(6, TOTAL, "Resolve — the archived letter and the matter record win")
    res = resolve_intake(cli, ids, firm, results[-1])
    banner(7, TOTAL, "Audit trail — what was checked, what was raised, how it was resolved")
    path = audit(cli, ids, firm, results)
    summary = dict(when=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   intakes=[dict(key=r["key"], verdict=r["verdict"], detected=sorted(c["category"] for c in r["detected"]))
                            for r in results], resolved=res["resolved"], claim_gone=res["claim_gone"],
                   corrected_conflicts=len(res["corrected_conflicts"]), audit=str(path.name))
    OUT.mkdir(exist_ok=True)
    with (OUT / "runs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary) + "\n")
    return summary


def show_conflict_list(cli: CLI) -> None:
    proj = find_project(cli)
    if proj is None:
        die("the firm history does not exist yet; run `python3 demo.py` first")
    items = [conflict_view(c) for c in list_conflicts(cli, proj["id"])]
    if not items:
        emit("text", "\n  No conflicts in the firm history.")
    for c in items:
        rs = c["resolve"] or {}
        emit("text", f"\n  {c['id']}  [{c['category']}] {'resolved (' + str(rs.get('strategy') or '…') + ')' if c['resolved'] else 'OPEN'}  {c['name']}"
                     + "".join(f"\n    fact {f['id']}: {clip(f['text'], 110)}" for f in c["facts"])
                     + "".join(f"\n    document {k['document']}" for k in c["chunks"]))


def check_party(cli: CLI, name: str) -> None:
    proj = find_project(cli)
    if proj is None:
        die("the firm history does not exist yet; run `python3 demo.py` first")
    slug = re.sub(r"[^a-z0-9]+", "-", re.sub(r"\b(llc|inc\.?|co\.?)$", "", name.lower().strip())).strip("-")
    known = next((p for p in load_firm()["parties"] if p["slug"] == slug or p["name"].lower() == name.lower()), None)
    c = party_check(cli, proj["id"], dict(name=known["name"] if known else name, slug=known["slug"] if known else slug), "adverse")
    show_party(c)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "conflicts", "check", "cleanup"],
                    help="run = full demo (default); conflicts = list every conflict in the firm history; "
                         "check NAME = party check one name as an adverse party; cleanup = delete everything")
    ap.add_argument("name", nargs="?", help="party name for `check`")
    ap.add_argument("--reset", action="store_true", help="delete the firm history and the parties first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    if args.command == "check" and not args.name:
        ap.error("check needs a party name, e.g. python3 demo.py check \"Brightline Properties LLC\"")
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "conflicts":
        banner(2, 2, "Every conflict in the firm history")
        show_conflict_list(cli)
        return
    if args.command == "check":
        banner(2, 2, f"Party check — {args.name}")
        check_party(cli, args.name)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py conflicts` lists every conflict; `python3 demo.py cleanup` removes the demo data.")


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

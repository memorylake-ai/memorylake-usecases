#!/usr/bin/env python3
"""
Engagement insight memory for consulting firms — a MemoryLake demo driven
entirely by the `memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Calder Lane Advisory closes an engagement with a grocer, Harrow & Finch.  The
close-out debrief is filed twice, word for word:

  * in the engagement's own project, on MemoryLake's built-in default: it keeps
    the client's name, its people and its numbers, for the engagement team;
  * in the firm's insight library, a project whose fact instruction says
    "describe clients by sector and size, never by name; no exact figures".

Same words, two memories.  A leak scan checks every library fact against each
engagement's identifiers.  Then an associate pins a client figure straight into
the library with `fact add` — an instruction steers what extraction records, it
does not filter writes — and the scan catches it.  A new team, starting with a
different grocer, searches only the library and gets the lessons with no client
in them; every lesson traces back to an engagement code, not a client name.

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

PREFIX = "mlu-eim"  # memorylake-usecases / engagement-insight-memory

LIBRARY = dict(
    custom_id=f"{PREFIX}-library",
    name="Calder Lane — insight library",
    description="Firm-level lessons from every engagement. Never names a client. Demo data.",
)


def engagement_project(e: dict) -> dict:
    return dict(custom_id=f"{PREFIX}-{e['code'].lower()}", name=f"{e['code']} — {e['client']}",
                description=f"Engagement memory for {e['client']} ({e['sector']}). Engagement team only. Demo data.")


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


# --------------------------------------------------------------------------- data + the leak scan

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def load_instruction() -> str:
    return (DATA / "library-instruction.md").read_text(encoding="utf-8")


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def closed_engagement(story: dict) -> dict:
    return next(e for e in story["engagements"] if e["code"] == story["debrief"]["engagement"])


def new_engagement(story: dict) -> dict:
    return next(e for e in story["engagements"] if e["code"] != story["debrief"]["engagement"])


# Dates the server adds to a fact ("(as of 2026-09-29)", "on 2026-09-28") are not client figures.
_SERVER_DATES = re.compile(r"\b(?:as of|on)\s+\d{4}-\d{2}-\d{2}\b")


def leaks(text: str, story: dict) -> list[str]:
    """What in this fact could identify a client: any engagement's identifiers (whole words), or any figure.

    A list-based scan only catches what is on the list; the figure rule is the safety net for numbers.
    """
    found = []
    for e in story["engagements"]:
        for word in e["identifiers"]:
            if re.search(r"(?<![A-Za-z])" + re.escape(word) + r"(?![A-Za-z])", text or "", re.I):
                found.append(word)
    rest = _SERVER_DATES.sub("", text or "")
    found += [f"figure “{m}”" for m in re.findall(r"\d+(?:[.,]\d+)?%?", rest)]
    return found


def scan(facts: list[dict], story: dict) -> list[dict]:
    return [dict(id=f["id"], fact=f.get("fact") or "", leaks=leaks(f.get("fact") or "", story)) for f in facts]


def scan_line(rows: list[dict], where: str) -> str:
    bad = [r for r in rows if r["leaks"]]
    if not rows:
        return f"{where}: no facts, so nothing to check"
    if not bad:
        return f"{where}: {len(rows)} fact(s) · 0 name a client or carry a figure"
    return f"{where}: {len(rows)} fact(s) · {len(bad)} name a client or carry a figure"


# --------------------------------------------------------------------------- step 2: setup

def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`; pass it only when the installed CLI still accepts it.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def ensure_actor(cli: CLI, c: dict) -> str:
    actor = cli.try_run("actor", "get", cid(c["custom_id"]), "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", cid(c["custom_id"]), "--display-name", c["display"],
                        *actor_type_args(cli, "HUMAN"), "--tags", "demo,consultant", "--description", c["role"])
        note(f"created actor {c['display']} → {actor['id']}")
    else:
        note(f"actor {c['display']} already exists → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
    return actor["id"]


def find_project(cli: CLI, p: dict):
    return cli.try_run("proj", "get", p["custom_id"], "--by-custom-id", scoped=True)


def ensure_project(cli: CLI, p: dict) -> dict:
    proj = find_project(cli, p)
    if proj is None:
        proj = cli.run("proj", "create", "--name", p["name"], "--custom-id", p["custom_id"],
                       "--description", p["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']}")
    return proj


def find_actor(cli: CLI, key: str):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id")


def get_instruction(cli: CLI, project_id: str, echo: bool = True) -> str:
    res = cli.run("fact", "instruction", "get", "--project", project_id, scoped=True, echo=echo) or {}
    return res.get("fact_instruction") or ""


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting the debriefs, both projects and both consultants, then starting over")
        cleanup(cli, quiet=True)
    eng = closed_engagement(story)
    ids = {c["key"]: ensure_actor(cli, c) for c in story["consultants"]}
    ids["engagement"] = ensure_project(cli, engagement_project(eng))["id"]
    ids["library"] = ensure_project(cli, LIBRARY)["id"]

    before = {k: get_instruction(cli, ids[k]) for k in ("engagement", "library")}
    for k in ("engagement", "library"):
        note(f"{k} project instruction: " + ("set by an earlier run" if before[k] else "empty → MemoryLake's built-in default"))
    mine = load_instruction()
    if before["library"].strip() != mine.strip():
        # Only the library gets a rule. The engagement project stays on the default: it is meant to keep the client.
        cli.run("fact", "instruction", "set", "--project", ids["library"],
                "--file", os.path.relpath(DATA / "library-instruction.md"), scoped=True)
    saved = get_instruction(cli, ids["library"])
    if saved.strip() != mine.strip():
        die("the library's saved instruction differs from data/library-instruction.md")
    emit("text", "\n  The library's fact instruction, read back with `fact instruction get` (the engagement project has none):\n\n"
         + "\n".join("    " + l for l in saved.strip().splitlines()))
    emit("setup", "", consultants=[dict(c, id=ids[c["key"]]) for c in story["consultants"]],
         engagement=dict(id=ids["engagement"], **engagement_project(eng), code=eng["code"]),
         library=dict(id=ids["library"], **LIBRARY), instruction=saved, engagement_instruction=before["engagement"])
    return ids


# --------------------------------------------------------------------------- step 3: the debrief, filed twice

def debrief_conv_id(where: str) -> str:
    return cid(f"debrief-{where}")


def find_conv(cli: CLI, where: str):
    return cli.try_run("conv", "get", debrief_conv_id(where), "--by-custom-id", scoped=True)


def list_messages(cli: CLI, conv_id: str, echo: bool = True) -> list[dict]:
    msgs, token = [], None
    while True:
        args = ["conv", "msg", "list", conv_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, echo=echo and token is None) or {}
        msgs.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            return sorted(msgs, key=lambda m: m.get("sequence_no") or 0)


def ensure_conv(cli: CLI, ids: dict, story: dict, where: str) -> dict:
    conv = find_conv(cli, where)
    if conv is None:
        eng = closed_engagement(story)
        project = ids["engagement"] if where == "engagement" else ids["library"]
        suffix = {"library": " (filed in the library)", "library-refiled": " (filed in the library again)"}.get(where, "")
        # The engagement code goes in metadata, never in the text: that is how a library lesson
        # traces back to its engagement without the library naming the client.
        conv = cli.run("conv", "create", "--custom-id", debrief_conv_id(where), "--project", project,
                       "--actors", ",".join(ids[c["key"]] for c in story["consultants"]), "--kind", "GROUP",
                       "--name", f"{eng['code']} {story['debrief']['title']}{suffix}",
                       "--metadata", f"engagement={eng['code']}", "--metadata", f"filed_in={where}", scoped=True)
    return conv


def append_part(cli: CLI, ids: dict, conv: dict, part_index: int, part: dict, offset: int, done: int, where: str) -> int:
    """Append one part of the debrief; returns how many messages were appended now."""
    start = datetime.fromisoformat(part["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
    n = 0
    for i, (who, words) in enumerate(part["turns"], start=1):
        seq = offset + i
        if seq <= done:
            continue
        ts = (start + timedelta(minutes=i - 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv["id"], "--actor", ids[who], "--custom-id", f"msg-{seq:02d}",
                "--timestamp", ts, "--text", words]
        cli.run(*args, scoped=True)
        n += 1
        emit("turn", "", where=where, index=seq, who=who, line=words)
    return n


def wait_for_memory(cli: CLI, conv_ids: list[str], timeout: int = 600) -> None:
    t0 = last = time.time()
    pending, first = list(conv_ids), True
    while pending and time.time() - t0 < timeout:
        for c in list(pending):
            try:
                status = cli.run("conv", "cook-status", c, scoped=True, echo=first) or {}
            except CLIError as e:
                if not e.has(*cli.TRANSIENT):
                    raise
                status = {}
            if status.get("cook_finished"):
                pending.remove(c)
                emit("cooked", "", id=c, seconds=int(time.time() - t0))
        first = False
        if not pending:
            break
        if time.time() - last >= 30:
            emit("progress", f"  … still turning the debrief into memory ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
            last = time.time()
        time.sleep(5)
    if pending:
        note(f"gave up after {timeout}s; {len(pending)} conversation(s) still processing server-side")
    else:
        note(f"memory ready in {int(time.time() - t0)}s")


def file_debrief(cli: CLI, ids: dict, story: dict) -> dict:
    convs = {w: ensure_conv(cli, ids, story, w) for w in ("engagement", "library")}
    done = {w: len(list_messages(cli, c["id"])) for w, c in convs.items()}
    total = sum(len(p["turns"]) for p in story["debrief"]["parts"])
    for w in convs:
        emit("debrief", "", where=w, id=convs[w]["id"], done=done[w], total=total)
        if done[w]:
            note(f"the {w} copy already holds {done[w]} of {total} message(s)")
    offset = 0
    for pi, part in enumerate(story["debrief"]["parts"]):
        # Each part reaches both projects, then both are cooked before the next part arrives:
        # two extraction batches, so each lesson can be traced to the part of the debrief it came from.
        appended = 0
        for w in ("engagement", "library"):
            appended += append_part(cli, ids, convs[w], pi, part, offset, done[w], w)
        if appended:
            note(f"part {pi + 1} ({part['label']}): {len(part['turns'])} messages filed in both projects")
            wait_for_memory(cli, [c["id"] for c in convs.values()])
        offset += len(part["turns"])
    if all(d >= total for d in done.values()):
        note("the debrief was filed by an earlier run; reading its memory as it is")
    if find_conv(cli, "library-refiled") is None and not list_facts(cli, "--projects", ids["library"]):
        # Rare, and not explained: in 2 of 29 test runs the library's extraction recorded nothing at all
        # from the debrief (no fact on the library, none on the consultants). Say so and file it once more.
        note("the library recorded nothing from this debrief (2 of 29 test runs did this; the cause is not known) — "
             "filing it once more, in a new conversation")
        conv = ensure_conv(cli, ids, story, "library-refiled")
        emit("debrief", "", where="library-refiled", id=conv["id"], done=0, total=total)
        off = 0
        for pi, part in enumerate(story["debrief"]["parts"]):
            append_part(cli, ids, conv, pi, part, off, 0, "library-refiled")
            off += len(part["turns"])
        wait_for_memory(cli, [conv["id"]])
        convs["library-refiled"] = conv
    return {w: c["id"] for w, c in convs.items()}


# --------------------------------------------------------------------------- step 4: same words, two memories

def list_facts(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> list[dict]:
    facts, token = [], None
    while True:
        args = ["fact", "list", flag, scope_id, "--page-size", "50"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        facts.extend(page.get("items") or [])
        token = page.get("continuation_token")
        if not token:
            break
    return facts


def two_memories(cli: CLI, ids: dict, story: dict) -> dict:
    eng = scan(list_facts(cli, "--projects", ids["engagement"]), story)
    lib = scan(list_facts(cli, "--projects", ids["library"]), story)
    people = []
    for c in story["consultants"]:
        people += [dict(r, who=c["display"]) for r in scan(list_facts(cli, "--actors", ids[c["key"]]), story)]

    def block(title: str, rows: list[dict], expect_leaks: bool) -> list[str]:
        out = [f"\n  {title}"]
        for r in rows:
            mark = ("•" if expect_leaks else "✗") if r["leaks"] else ("•" if expect_leaks else "✓")
            out.append(f"   {mark} {r['fact']}")
            if r["leaks"]:
                out.append(f"       ↳ {', '.join(r['leaks'])}")
        return out or [f"\n  {title}\n   (nothing)"]

    lines = block(f"Engagement project ({closed_engagement(story)['code']}, built-in default) — for the engagement team:", eng, True)
    lines += block("Insight library (its own instruction) — for everyone at the firm:", lib, False)
    lines.append("")
    lines.append("  " + scan_line(eng, "engagement") + "   ← expected: this project is the client's")
    lines.append("  " + scan_line(lib, "library   "))
    people_bad = [r for r in people if r["leaks"]]
    lines.append(f"  also on the consultants' own actors: {len(people)} fact(s) (role, working preferences) · "
                 f"{len(people_bad)} name a client")
    removed = []
    for r in lib:
        if r["leaks"]:
            # The instruction is a prompt to the extractor, not a guarantee. Whatever slips through is removed.
            cli.run("fact", "delete", "--project", ids["library"], r["id"], scoped=True)
            removed.append(r)
    if removed:
        lines.append(f"  removed {len(removed)} library fact(s) the scan flagged (the same fix as step 5)")
    if not lib:
        lines.append("  the library holds no lessons from this run; rerun with --reset (in testing every run produced some)")
    emit("text", "\n".join(lines))
    res = dict(engagement=eng, library=lib, people=people, removed=removed,
               eng_leaky=sum(bool(r["leaks"]) for r in eng), lib_leaky=sum(bool(r["leaks"]) for r in lib))
    emit("two_memories", "", **res)
    return res


# --------------------------------------------------------------------------- step 5: the shortcut

def search_facts(cli: CLI, project_id: str, query: str, top_k: int = 8, echo: bool = True) -> list[dict]:
    res = cli.run("search", query, "--projects", project_id, "--types", "fact", "--top-k", str(top_k),
                  scoped=True, echo=echo) or {}
    return [dict(id=f.get("id"), fact=f.get("fact") or "", score=f.get("score")) for f in res.get("facts") or []]


def shortcut(cli: CLI, ids: dict, story: dict) -> dict:
    sc = story["shortcut"]
    who = next(c for c in story["consultants"] if c["key"] == sc["by"])
    note(sc["why"])
    # Clear anything a crashed earlier run left behind, so this step always starts from a clean library.
    for f in list_facts(cli, "--projects", ids["library"], echo=False):
        if f.get("fact") == sc["text"]:
            cli.run("fact", "delete", "--project", ids["library"], f["id"], scoped=True)
    added = cli.run("fact", "add", "--project", ids["library"], sc["text"], scoped=True)
    fid = (added.get("facts") or [{}])[0].get("id")
    stored = cli.run("fact", "get", fid, "--project", ids["library"], scoped=True) or {}
    verbatim = stored.get("fact") == sc["text"]
    q = story["questions"][0]["q"]
    hits = search_facts(cli, ids["library"], q, 5)
    rank = next((i for i, h in enumerate(hits, 1) if h["id"] == fid), None)
    rows = scan(list_facts(cli, "--projects", ids["library"]), story)
    flagged = [r for r in rows if r["leaks"]]
    lines = [f"\n  {who['display']} pinned, with `fact add`:  “{sc['text']}”",
             f"  stored {'word for word' if verbatim else 'rewritten: ' + stored.get('fact', '')} — the instruction steers what extraction records; it does not filter writes",
             f"  and search for the new team's first question puts it at {'rank ' + str(rank) if rank else 'no rank (not in the top 5)'}",
             "\n  The leak scan, run again over the library:"]
    for r in flagged:
        lines.append(f"   ✗ {r['fact']}\n       ↳ {', '.join(r['leaks'])}")
    lines.append("  " + scan_line(rows, "library"))
    for r in flagged:
        cli.run("fact", "delete", "--project", ids["library"], r["id"], scoped=True)
    after = scan(list_facts(cli, "--projects", ids["library"]), story)
    lines.append(f"\n  deleted {len(flagged)} · " + scan_line(after, "library") + (" ✓" if after and not any(r["leaks"] for r in after) else ""))
    emit("text", "\n".join(lines))
    res = dict(note_text=sc["text"], by=who["display"], fact_id=fid, verbatim=verbatim, rank=rank, query=q,
               flagged=flagged, after=after, caught=any(r["id"] == fid for r in flagged))
    emit("shortcut", "", **res)
    return res


# --------------------------------------------------------------------------- step 6: the next engagement

def on_topic(text: str, topic: list[str]) -> bool:
    t = (text or "").lower()
    return any(w.lower() in t for w in topic)


def next_engagement(cli: CLI, ids: dict, story: dict) -> dict:
    new = new_engagement(story)
    note(f"{new['code']} ({new['client']}, {new['sector']}) kicks off; its team searches the library only")
    answers = []
    lines = []
    for item in story["questions"]:
        hits = search_facts(cli, ids["library"], item["q"], 5)
        shown = [dict(h, leaks=leaks(h["fact"], story)) for h in hits if on_topic(h["fact"], item["topic"])]
        left_out = len(hits) - len(shown)
        lines.append(f"\n  Q: {item['q']}")
        for h in shown:
            lines.append(f"   {'✗' if h['leaks'] else '✓'} {h['fact']}" + (f"\n       ↳ {', '.join(h['leaks'])}" if h["leaks"] else ""))
        if not shown:
            lines.append("   (no lesson on this topic in the library)")
        if left_out:
            lines.append(f"   ({left_out} other hit(s) left out: search always returns its closest facts, related or not)")
        answers.append(dict(q=item["q"], hits=shown, left_out=left_out))
    every = [h for a in answers for h in a["hits"]]
    clean = not any(h["leaks"] for h in every)
    lines.append(f"\n  {len(every)} lesson(s) returned · {'none names a client or carries a figure ✓' if clean else 'some carry client identifiers ✗'}")
    # What the library is not: the closed engagement's own project answers the same question with the client in it.
    eng_hits = search_facts(cli, ids["engagement"], story["questions"][0]["q"], 5)
    eng_named = [h for h in eng_hits if leaks(h["fact"], story)]
    lines.append(f"  the same first question in {closed_engagement(story)['code']}'s own project: {len(eng_named)} of {len(eng_hits)} hit(s) "
                 f"name the client or carry a figure — the new team's searches never include it, because it is not in their --projects")
    emit("text", "\n".join(lines))
    res = dict(engagement=new, answers=answers, clean=clean, contrast=dict(hits=len(eng_hits), named=len(eng_named),
               example=(eng_named[0]["fact"] if eng_named else "")))
    emit("next_engagement", "", **res)
    return res


# --------------------------------------------------------------------------- step 7: provenance

def provenance(cli: CLI, ids: dict, story: dict, write: bool = True) -> dict:
    conv = find_conv(cli, "library")
    if conv is None:
        die("the library debrief does not exist yet; run `python3 demo.py` first")
    meta = conv.get("metadata") or {}
    msgs = list_messages(cli, conv["id"])
    by_id = {m["id"]: dict(m, n=i) for i, m in enumerate(msgs, start=1)}
    again = find_conv(cli, "library-refiled")
    if again:
        by_id.update({m["id"]: dict(m, n=i) for i, m in enumerate(list_messages(cli, again["id"]), start=1)})
    names = {ids[c["key"]]: c["display"] for c in story["consultants"]}
    parts, offset = [], 0
    for p in story["debrief"]["parts"]:
        parts.append((offset + 1, offset + len(p["turns"]), p["label"]))
        offset += len(p["turns"])
    rows = []
    for f in list_facts(cli, "--projects", ids["library"], echo=False):
        t = cli.run("fact", "trace", f["id"], "--project", ids["library"], scoped=True) or {}
        entries = t.get("trace") or []
        src_ids = sorted({s for e in entries for s in (e.get("source_entry_ids") or [])})
        src = sorted((by_id[s] for s in src_ids if s in by_id), key=lambda m: m["n"])
        kinds = sorted({e.get("source_kind") or "?" for e in entries})
        ns = [m["n"] for m in src]
        part = next((lab for a, b, lab in parts if ns and a <= min(ns) and max(ns) <= b), "")
        speakers = sorted({names.get(m.get("actor_id") or "", m.get("actor_id") or "?") for m in src})
        rows.append(dict(id=f["id"], fact=f["fact"], kinds=kinds, messages=ns, part=part, speakers=speakers,
                         at=(src[-1].get("timestamp") or "") if src else "",
                         engagement=meta.get("engagement", "?"), leaks=leaks(f["fact"], story)))
    lines = [f"\n  library debrief {conv['id']} · metadata engagement={meta.get('engagement', '?')} · {len(msgs)} messages"
             + (f" (filed again as {again['id']})" if again else "")]
    for r in rows:
        where = (f"msgs {r['messages'][0]}–{r['messages'][-1]}" if len(r["messages"]) > 1 else f"msg {r['messages'][0]}") if r["messages"] else "no message (added by hand)"
        lines.append(f"   {r['fact']}\n     → {r['engagement']} · debrief {where}" + (f" ({r['part']})" if r["part"] else "")
                     + (f" · {', '.join(r['speakers'])}" if r["speakers"] else "") + f" · {'/'.join(r['kinds'])}")
    named = sum(bool(r["leaks"]) for r in rows)
    lines.append(f"\n  {len(rows)} lesson(s), each traced to an engagement code; {named} name a client.")
    lines.append("  The code leads whoever staffs that engagement back to its own project. Projects scope search, they are not an access")
    lines.append("  control: every API key on a team can read every project (see security-review-memory).")
    emit("text", "\n".join(lines))
    if write:
        OUT.mkdir(exist_ok=True)
        md = ["# Insight library — provenance", "",
              f"Library project `{ids['library']}` · debrief conversation `{conv['id']}` (metadata `engagement={meta.get('engagement', '?')}`)", ""]
        for r in rows:
            md.append(f"- {r['fact']}  \n  `{r['id']}` · {r['engagement']} · messages {r['messages']} · {', '.join(r['speakers'])} · {'/'.join(r['kinds'])}")
        (OUT / "insight-provenance.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        (OUT / "insight-provenance.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        note(f"written to {os.path.relpath(OUT / 'insight-provenance.md')} and .json")
    res = dict(conversation=conv["id"], engagement=meta.get("engagement"), rows=rows, named=named)
    emit("provenance", "", **res)
    return res


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    for w in ("engagement", "library", "library-refiled"):
        conv = find_conv(cli, w)
        if conv:
            # `proj delete` does not delete conversations; remove them explicitly.
            cli.run("conv", "delete", conv["id"], scoped=True)
            note(f"deleted the {w} copy of the debrief")
    for p in (engagement_project(closed_engagement(story)), LIBRARY):
        proj = find_project(cli, p)
        if proj:
            # Deleting a project deletes its facts and its fact instruction with it.
            cli.run("proj", "delete", proj["id"], scoped=True)
            note(f"deleted “{p['name']}”, its memory and its instruction")
    for c in story["consultants"]:
        actor = find_actor(cli, c["custom_id"])
        if actor:
            cli.run("actor", "delete", actor["id"])
            note(f"deleted {c['display']} and every fact on that actor")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    eng = closed_engagement(story)
    banner(2, TOTAL, f"Set up — two consultants, the {eng['code']} project, the firm's insight library")
    ids = setup(cli, story, reset)

    banner(3, TOTAL, f"The close-out debrief — the same words, filed in both projects")
    file_debrief(cli, ids, story)

    banner(4, TOTAL, "Same words, two memories — and a leak scan over the library")
    two_memories(cli, ids, story)

    banner(5, TOTAL, "The shortcut — a client figure pinned straight into the library")
    shortcut(cli, ids, story)

    banner(6, TOTAL, f"The next engagement — {new_engagement(story)['code']} searches the library")
    result = next_engagement(cli, ids, story)

    banner(7, TOTAL, "Provenance — every lesson traced to an engagement code, not a client")
    provenance(cli, ids, story)
    return result


def lookup_ids(cli: CLI, story: dict) -> dict:
    ids = {}
    for c in story["consultants"]:
        a = find_actor(cli, c["custom_id"])
        if a:
            ids[c["key"]] = a["id"]
    for key, p in (("engagement", engagement_project(closed_engagement(story))), ("library", LIBRARY)):
        proj = find_project(cli, p)
        if proj:
            ids[key] = proj["id"]
    if "library" not in ids:
        die("the insight library does not exist yet; run `python3 demo.py` first")
    return ids


def show_insights(cli: CLI) -> None:
    story = load_story()
    ids = lookup_ids(cli, story)
    instr = get_instruction(cli, ids["library"])
    emit("text", "\n  The library's instruction:\n\n" + "\n".join("    " + l for l in (instr.strip() or "(empty: built-in default)").splitlines()))
    rows = scan(list_facts(cli, "--projects", ids["library"]), story)
    emit("text", "\n  The library:\n" + "\n".join(f"   {'✗' if r['leaks'] else '✓'} {r['fact']}" for r in rows)
         + "\n\n  " + scan_line(rows, "library"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "insights", "audit", "cleanup"],
                    help="run = full demo (default); insights = the library + leak scan; audit = provenance of every lesson; "
                         "cleanup = delete everything")
    ap.add_argument("--reset", action="store_true", help="delete the debriefs, projects and consultants first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = {"cleanup": 2, "insights": 2, "audit": 2}.get(args.command, TOTAL)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "insights":
        banner(2, 2, "The insight library and its leak scan")
        show_insights(cli)
        return
    if args.command == "audit":
        banner(2, 2, "Provenance — every lesson traced to an engagement code")
        provenance(cli, lookup_ids(cli, load_story()), load_story())
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py insights` scans the library again; `python3 demo.py audit` traces every lesson;"
          " `python3 demo.py cleanup` removes the demo data.")


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

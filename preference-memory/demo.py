#!/usr/bin/env python3
"""
The AI that actually knows you — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Priya Raman, an operations lead, works with an AI assistant in several tools.
Every session is written to MemoryLake as a conversation, and what she tells
the assistant becomes facts on *her* actor: one profile, whichever tool she is in.

On the built-in default, that profile also keeps her family, her diet and her
knee surgery. So she writes her own instruction for what her memory may record
(`fact instruction set`) and removes what it already kept. The next session, in
another tool, records her new work rules and her changed answer length, and none
of the new family or health details. Finally the profile becomes one block that
goes, byte for byte, into a Claude, an OpenAI and a Gemini request.

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
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-pfm"  # memorylake-usecases / preference-memory

PROJECT = dict(
    custom_id=f"{PREFIX}-sessions",
    name="Priya — AI sessions",
    description="Every session Priya has with an AI assistant, in any tool. Demo data.",
)


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

def load_story() -> dict:
    return json.loads((DATA / "story.json").read_text(encoding="utf-8"))


def load_instruction() -> str:
    return (DATA / "instruction.md").read_text(encoding="utf-8")


def cid(name: str) -> str:
    return f"{PREFIX}-{name}"


def matches(text: str, markers: list[str]) -> list[str]:
    """Marker words found in a fact (case-insensitive, whole words)."""
    return [m for m in markers if re.search(r"(?<![A-Za-z])" + re.escape(m) + r"(?![A-Za-z])", text or "", re.I)]


def category_of(text: str, story: dict) -> str | None:
    for c in story["excluded"]:
        if matches(text, c["markers"]):
            return c["key"]
    return None


# --------------------------------------------------------------------------- step 2: setup

def actor_type_args(cli: CLI, actor_type: str) -> list[str]:
    # CLI v20261009 dropped `actor create --type`, but the server still honours the type: an ASSISTANT
    # actor keeps (almost) no facts, a HUMAN one picks up facts from the conversations it is in.
    # Pass the type whenever the installed CLI still accepts it.
    if not hasattr(cli, "has_actor_type"):
        rc, out, _ = cli.raw("actor", "create", "--help", echo=False)
        cli.has_actor_type = rc == 0 and "--type" in out  # type: ignore[attr-defined]
    if not cli.has_actor_type and actor_type != "HUMAN":  # type: ignore[attr-defined]
        note(f"this CLI cannot set an actor type, so this {actor_type} actor is created untyped (server default HUMAN)")
    return ["--type", actor_type] if cli.has_actor_type else []  # type: ignore[attr-defined]


def ensure_actor(cli: CLI, custom_id: str, display: str, actor_type: str, description: str, tags: str) -> str:
    actor = cli.try_run("actor", "get", custom_id, "--by-custom-id")
    if actor is None:
        actor = cli.run("actor", "create", "--custom-id", custom_id, "--display-name", display,
                        *actor_type_args(cli, actor_type), "--tags", tags, "--description", description)
        note(f"created {actor_type.lower()} actor {display} → {actor['id']}")
    else:
        note(f"actor {display} already exists → {actor['id']}")
    try:
        cli.run("actor", "bind", "--actor", actor["id"], scoped=True)
    except CLIError as e:
        if not e.has("409", "already"):
            raise
        note("already bound to this workspace")
    return actor["id"]


def find_project(cli: CLI):
    return cli.try_run("proj", "get", PROJECT["custom_id"], "--by-custom-id", scoped=True)


def find_actor(cli: CLI, key: str):
    return cli.try_run("actor", "get", cid(key), "--by-custom-id")


def find_conv(cli: CLI, s: dict):
    return cli.try_run("conv", "get", cid(s["custom_id"]), "--by-custom-id", scoped=True)


def get_instruction(cli: CLI, flag: str, scope_id: str, echo: bool = True) -> str:
    res = cli.run("fact", "instruction", "get", flag, scope_id, scoped=True, echo=echo) or {}
    return res.get("fact_instruction") or ""


def setup(cli: CLI, story: dict, reset: bool) -> dict:
    if reset:
        note("--reset: deleting Priya, the assistant, the sessions and the project, then starting over")
        cleanup(cli, quiet=True)
    p, a = story["person"], story["assistant"]
    ids = dict(
        person=ensure_actor(cli, cid(p["custom_id"]), p["display"], "HUMAN", p["role"], "demo,person"),
        assistant=ensure_actor(cli, cid(a["custom_id"]), a["display"], "ASSISTANT", a["role"], "demo,assistant"),
    )
    proj = find_project(cli)
    if proj is None:
        proj = cli.run("proj", "create", "--name", PROJECT["name"], "--custom-id", PROJECT["custom_id"],
                       "--description", PROJECT["description"], scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']} (pass --reset to start over)")
    ids["project"] = proj["id"]
    instr = get_instruction(cli, "--actor", ids["person"])
    if instr:
        note("Priya's memory already follows her own instruction (from an earlier run)")
    else:
        note("Priya's fact instruction is empty → MemoryLake uses its built-in default")
    # What does a person's memory record by default? Ask the server for a draft (nothing is saved).
    # Asked here, before any session: in our runs, a draft asked just before `set` was followed by more
    # sessions that ignored the new instruction (3 of 11 against 0 of 12), so the demo keeps them apart.
    ids["draft"] = (cli.run("fact", "instruction", "draft", "--actor", ids["person"], scoped=True) or {}).get("fact_instruction") or ""
    emit("text", "\n  `fact instruction draft` — what a person's memory records, in MemoryLake's words (nothing saved):\n\n"
         + "\n".join("    " + l for l in ids["draft"].strip().splitlines()))
    emit("setup", "", person=dict(id=ids["person"], **p), assistant=dict(id=ids["assistant"], **a),
         project=dict(id=proj["id"], name=proj["name"]), instruction=instr, draft=ids["draft"])
    return ids


# --------------------------------------------------------------------------- sessions

def session_state(cli: CLI, s: dict) -> tuple[dict | None, int]:
    conv = find_conv(cli, s)
    if conv is None:
        return None, 0
    done = len((cli.run("conv", "msg", "list", conv["id"], "--page-size", "50") or {}).get("items") or [])
    return conv, done


def replay_session(cli: CLI, ids: dict, s: dict) -> str:
    conv, done = session_state(cli, s)
    if conv is None:
        # One conversation per session. The tool goes in metadata; Priya is the actor in every one of them,
        # so whichever tool she is in, what she says lands on the same memory.
        conv = cli.run("conv", "create", "--custom-id", cid(s["custom_id"]), "--project", ids["project"],
                       "--actors", f"{ids['person']},{ids['assistant']}", "--kind", "DIRECT",
                       "--name", f"{s['tool']} — {s['title']}", "--metadata", f"tool={s['tool']}", scoped=True)
    else:
        note(f"session “{s['title']}” ({s['tool']}) exists with {done} of {len(s['turns'])} message(s) → {conv['id']}")
    emit("session", "", key=s["key"], id=conv["id"], tool=s["tool"], title=s["title"], date=s["date"],
         turns=len(s["turns"]), done=done)
    start = datetime.fromisoformat(s["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
    parent = conv.get("current_message_id")
    for i, (who, words) in enumerate(s["turns"], start=1):
        if i <= done:
            continue
        ts = (start + timedelta(seconds=40 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        args = ["conv", "msg", "append", conv["id"], "--actor", ids["person"] if who == "user" else ids["assistant"],
                "--custom-id", f"turn-{i:02d}", "--timestamp", ts, "--text", words]
        if parent:
            args += ["--parent", parent]
        msg = cli.run(*args, scoped=True)
        parent = msg["id"]
        emit("turn", "", key=s["key"], index=i, who=who, line=words)
    if done < len(s["turns"]):
        note(f"{len(s['turns']) - done} message(s) appended to the {s['tool']} session")
    return conv["id"]


def wait_for_memory(cli: CLI, conv_id: str, timeout: int = 600) -> None:
    t0 = last = time.time()
    first = True
    while time.time() - t0 < timeout:
        try:
            status = cli.run("conv", "cook-status", conv_id, scoped=True, echo=first) or {}
        except CLIError as e:
            if not e.has(*cli.TRANSIENT):
                raise
            note("cook-status probe failed on the network; will try again")
            status = {}
        first = False
        if status.get("cook_finished"):
            note(f"memory ready in {int(time.time() - t0)}s")
            emit("cooked", "", id=conv_id, seconds=int(time.time() - t0))
            return
        if time.time() - last >= 30:
            emit("progress", f"  … still turning the session into memory ({int(time.time() - t0)}s)", elapsed=int(time.time() - t0))
            last = time.time()
        time.sleep(5)
    note(f"gave up after {timeout}s; the session is still processing server-side")


def fact_actions(cli: CLI, conv_id: str, flag: str, scope_id: str) -> list[dict]:
    """What one conversation did to one memory: ADD / UPDATE / FORGET, with the text before and after."""
    res = cli.run("conv", "fact-actions", conv_id, flag, scope_id, scoped=True) or {}
    out = []
    for x in res.get("items") or []:
        out.append(dict(event=x.get("event"), fact_id=x.get("fact_id"), fact=x.get("new_fact") or "",
                        old=x.get("old_fact") or ""))
    return list(reversed(out))  # oldest first


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


# --------------------------------------------------------------------------- step 4: what the default kept

def receipt(cli: CLI, ids: dict, conv_id: str, story: dict) -> list[dict]:
    rows = []
    for scope, flag, sid in (("Priya", "--actor", ids["person"]), ("project", "--project", ids["project"])):
        for r in fact_actions(cli, conv_id, flag, sid):
            r["scope"] = scope
            r["category"] = category_of(r["fact"], story)
            rows.append(r)
    return rows


def show_default_profile(cli: CLI, ids: dict, conv_id: str, story: dict) -> list[dict]:
    rows = receipt(cli, ids, conv_id, story)
    labels = {c["key"]: c["label"] for c in story["excluded"]}
    personal = [r for r in rows if r["category"]]
    lines = [f"\n  Session 1 ({story['sessions'][0]['tool']}, built-in default) wrote {len(rows)} fact(s):"]
    for r in rows:
        tag = f"PERSONAL · {labels[r['category']]}" if r["category"] else "work"
        where = "" if r["scope"] == "Priya" else "   [on the project]"
        lines.append(f"   {r['event']:<6} [{tag}] {r['fact']}{where}")
    lines.append(f"\n  {len(personal)} of them are about her family, home, health or diet — kept because the default records "
                 f"“identity and background, current life situation”.")
    emit("text", "\n".join(lines))
    emit("default_profile", "", rows=rows, personal=len(personal))
    return rows


# --------------------------------------------------------------------------- step 5: her instruction

def write_instruction(cli: CLI, ids: dict, story: dict, session2_started: bool) -> dict:
    draft = ids.get("draft") or ""
    mine = load_instruction()
    emit("text", "\n  The draft (step 2) described the default. Priya's own version, data/instruction.md:\n\n" + "\n".join("    " + l for l in mine.strip().splitlines()))
    path = os.path.relpath(DATA / "instruction.md")  # relative, so the echoed command reads like yours would
    # The same rule for her profile and for the project that holds her sessions:
    # the server decides on its own which of the two a fact lands on.
    cli.run("fact", "instruction", "set", "--actor", ids["person"], "--file", path, scoped=True)
    cli.run("fact", "instruction", "set", "--project", ids["project"], "--file", path, scoped=True)
    saved = get_instruction(cli, "--actor", ids["person"])
    note("saved: `fact instruction get` returns her text" if saved.strip() == mine.strip()
         else "WARNING: the saved instruction differs from data/instruction.md")
    deleted = []
    if session2_started:
        note("session 2 already ran in an earlier pass; nothing to remove now (it would hide what session 2 recorded)")
    else:
        # An instruction steers what is recorded from now on. What the default already kept stays until removed.
        for flag, del_flag, sid in (("--actors", "--actor", ids["person"]), ("--projects", "--project", ids["project"])):
            for f in list_facts(cli, flag, sid):
                if category_of(f.get("fact", ""), story):
                    cli.run("fact", "delete", del_flag, sid, f["id"], scoped=True)
                    deleted.append(dict(id=f["id"], fact=f["fact"]))
        note(f"removed {len(deleted)} personal fact(s) the default had kept")
    emit("instruction", "", draft=draft, mine=mine, saved=saved, deleted=deleted)
    return dict(draft=draft, mine=mine, deleted=deleted)


# --------------------------------------------------------------------------- step 6: the next session, another tool

def check_session2(cli: CLI, ids: dict, conv_id: str, story: dict) -> dict:
    rows = receipt(cli, ids, conv_id, story)
    lines = [f"\n  Session 2 ({story['sessions'][1]['tool']}, her instruction) — `conv fact-actions`, the receipt:"]
    for r in rows:
        where = "" if r["scope"] == "Priya" else "   [on the project]"
        lines.append(f"   {r['event']:<6} {r['fact']}{where}")
        if r["event"] == "UPDATE" and r["old"]:
            lines.append(f"          was: {r['old']}")
    checks = []
    lines.append("\n  She said it in this session, so was it recorded?")
    for c in story["excluded"]:
        hit = [r for r in rows if r["category"] == c["key"]]
        checks.append(dict(kind="excluded", key=c["key"], label=c["label"], ok=not hit, facts=[r["fact"] for r in hit]))
        lines.append(f"   {'✓' if not hit else '✗'} {c['label']:<22} excluded → {'0 facts recorded' if not hit else f'{len(hit)} recorded:'}")
        lines += [f"        {r['fact']}" for r in hit]
    for e in story["expected_rules"]:
        hit = [r for r in rows if matches(r["fact"], e["markers"])]
        checks.append(dict(kind="rule", key=e["key"], label=e["label"], ok=bool(hit), facts=[r["fact"] for r in hit]))
        lines.append(f"   {'✓' if hit else '✗'} {e['label']:<48} {'recorded' if hit else 'not recorded'}")
    ok = sum(c["ok"] for c in checks)
    lines.append(f"\n  {ok} of {len(checks)} as intended.")
    emit("text", "\n".join(lines))
    emit("session2", "", rows=rows, checks=checks, ok=ok, total=len(checks))
    return dict(rows=rows, checks=checks, ok=ok, total=len(checks))


# --------------------------------------------------------------------------- step 7: the same memory in every model

def background_block(facts: list[dict], display: str) -> str:
    lines = [f"<background-memory owner=\"{display}\">"]
    lines += [f"- {f['fact']}" for f in sorted(facts, key=lambda f: f.get("fact", ""))]
    lines.append("</background-memory>")
    return "\n".join(lines)


def payloads(block: str, story: dict) -> dict[str, dict]:
    ask = story["first_message"]
    return {
        "anthropic": {"model": "YOUR_CLAUDE_MODEL", "max_tokens": 400, "system": block,
                      "messages": [{"role": "user", "content": ask}]},
        "openai": {"model": "YOUR_OPENAI_MODEL",
                   "messages": [{"role": "system", "content": block}, {"role": "user", "content": ask}]},
        "gemini": {"systemInstruction": {"parts": [{"text": block}]},
                   "contents": [{"role": "user", "parts": [{"text": ask}]}]},
    }


def block_in(key: str, payload: dict) -> str:
    """Read the memory block back out of a payload, the way each API would see it."""
    if key == "anthropic":
        return payload["system"]
    if key == "openai":
        return payload["messages"][0]["content"]
    return payload["systemInstruction"]["parts"][0]["text"]


def every_model(cli: CLI, ids: dict, story: dict) -> dict:
    facts = list_facts(cli, "--actors", ids["person"])
    block = background_block(facts, story["person"]["display"])
    emit("text", f"\n  Priya's profile — {len(facts)} facts, read once with `fact list --actors`:\n\n"
         + "\n".join("    " + l for l in block.splitlines()))
    OUT.mkdir(exist_ok=True)
    rows = []
    for t in story["tools"]:
        p = payloads(block, story)[t["key"]]
        (OUT / t["file"]).write_text(json.dumps(p, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        back = json.loads((OUT / t["file"]).read_text(encoding="utf-8"))
        digest = hashlib.sha256(block_in(t["key"], back).encode()).hexdigest()
        rows.append(dict(key=t["key"], label=t["label"], file=f"out/{t['file']}", sha=digest))
    same = len({r["sha"] for r in rows}) == 1
    lines = ["\n  The same block, written into three request bodies (read back from disk):"]
    lines += [f"   {r['label']:<36} {r['file']:<28} sha256 {r['sha'][:16]}…" for r in rows]
    lines.append(f"\n  {'identical in all three' if same else 'WARNING: the blocks differ'} — one memory, whichever model she opens.")
    emit("text", "\n".join(lines))
    (OUT / "background-memory.md").write_text(block + "\n", encoding="utf-8")
    emit("everywhere", "", block=block, facts=len(facts), payloads=rows, same=same, ask=story["first_message"])
    return dict(block=block, payloads=rows, same=same)


def search_person(cli: CLI, actor_id: str, query: str, top_k: int = 8) -> list[dict]:
    res = cli.run("search", query, "--actors", actor_id, "--types", "fact", "--top-k", str(top_k), scoped=True) or {}
    return [dict(id=f.get("id"), fact=f.get("fact"), score=f.get("score")) for f in res.get("facts") or []]


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    story = load_story()
    for s in story["sessions"]:
        conv = find_conv(cli, s)
        if conv:
            # `proj delete` does not delete conversations; remove them explicitly.
            cli.run("conv", "delete", conv["id"], scoped=True)
            note(f"deleted the {s['tool']} session")
    proj = find_project(cli)
    if proj:
        cli.run("proj", "delete", proj["id"], scoped=True)
        note("deleted the sessions project")
    for key, label in ((story["person"]["custom_id"], story["person"]["display"]),
                       (story["assistant"]["custom_id"], story["assistant"]["display"])):
        actor = find_actor(cli, key)
        if actor:
            # Deleting the actor deletes its facts and its fact instruction with it.
            cli.run("actor", "delete", actor["id"])
            note(f"deleted {label}, every fact on that actor and its instruction")


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    story = load_story()
    s1, s2 = story["sessions"]
    banner(2, TOTAL, "Set up — Priya, her assistant, a project for her sessions")
    ids = setup(cli, story, reset)

    banner(3, TOTAL, f"Session 1 in {s1['tool']} — on the built-in default")
    conv2, done2 = session_state(cli, s2)
    if done2:
        conv1 = find_conv(cli, s1)
        if conv1 is None:
            die("session 2 exists without session 1; run with --reset")
        note("session 2 already started in an earlier run, so session 1 is left as it is")
        c1 = conv1["id"]
        emit("session", "", key=s1["key"], id=c1, tool=s1["tool"], title=s1["title"], date=s1["date"],
             turns=len(s1["turns"]), done=len(s1["turns"]))
    else:
        c1 = replay_session(cli, ids, s1)
    wait_for_memory(cli, c1)

    banner(4, TOTAL, "What the default kept — session 1's receipt, fact by fact")
    show_default_profile(cli, ids, c1, story)

    banner(5, TOTAL, "Her instruction — what her memory may record from now on")
    write_instruction(cli, ids, story, session2_started=bool(done2))

    banner(6, TOTAL, f"Session 2 in {s2['tool']} — new rules, a change, and more personal news")
    c2 = replay_session(cli, ids, s2)
    wait_for_memory(cli, c2)
    result = check_session2(cli, ids, c2, story)

    banner(7, TOTAL, "Every model — one profile, the same bytes in Claude, OpenAI and Gemini")
    every_model(cli, ids, story)
    note(f"payloads written to {OUT}/")
    return result


def show_profile(cli: CLI) -> None:
    story = load_story()
    person = find_actor(cli, story["person"]["custom_id"])
    if person is None:
        die("Priya does not exist yet; run `python3 demo.py` first")
    instr = get_instruction(cli, "--actor", person["id"])
    emit("text", "\n  Her fact instruction:\n\n" + "\n".join("    " + l for l in (instr.strip() or "(empty: built-in default)").splitlines()))
    every_model(cli, dict(person=person["id"]), story)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "profile", "cleanup"],
                    help="run = full demo (default); profile = print her instruction and memory block; cleanup = delete everything")
    ap.add_argument("--reset", action="store_true", help="delete Priya, the sessions and the project first, then start over")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = {"cleanup": 2, "profile": 2}.get(args.command, TOTAL)
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "profile":
        banner(2, 2, "Priya's instruction and the memory block every model gets")
        show_profile(cli)
        return
    run_pipeline(cli, reset=args.reset)
    print("\nDone. `python3 demo.py profile` prints her memory block again; `python3 demo.py cleanup` removes the demo data.")


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

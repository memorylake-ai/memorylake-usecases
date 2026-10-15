#!/usr/bin/env python3
"""
Brand voice memory every AI tool reads — a MemoryLake demo driven entirely by the
`memorylake` CLI (https://github.com/memorylake-ai/memorylake-cli).

Story
-----
Tallyleaf (fictional) makes bookkeeping software for independent restaurants.
Its marketing team writes with three AI tools: one on the OpenAI Chat
Completions API, one on the OpenAI Responses API, one on the Anthropic Messages
API.  The brand guide lives in MemoryLake as facts in one project, and every tool
passes the same *boundary* to MemoryRouter (`memorylake router chat|responses|
messages --boundary`), which attaches the memories most relevant to each brief.

The demo writes four briefs through all three tools and checks every draft with
a deterministic brand linter (no '!', no emoji, no banned words, talk to "you",
the email sign-off).  It does this four times: with no memory, on day one (10
facts), a quarter later (the same six rules plus 40 campaign notes, quotes and
product facts), and after the fix (the six rules merged into one voice card that
names every channel).  An inspector asks Claude to quote back what MemoryRouter
put in front of it, so you can see which rules were attached to each brief.

What it shows: retrieval by relevance keeps prompts small, but rules that apply
to *every* brief are not about any one topic.  As the brand memory grows they
fall out of what gets attached, and the drafts go generic again.  One card that
is about writing itself ranks with every brief and brings all the rules with it.

Model wording varies run to run; every draft is linted and printed, and the
README reports totals over all runs, not the best one.

Requirements: Python 3.9+, the memorylake CLI (v20261015 or later) on PATH, and a
MemoryLake API key.  Router calls are billed to your MemoryLake balance (about 65
calls on the smallest models per run).
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
import textwrap
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
STATE_DIR = HERE / ".memorylake-demo"
PROFILE = "memorylake-usecases"
DEFAULT_BASE_URL = "https://app.memorylake.ai/openapi/memorylake"

PREFIX = "mlu-bvm"  # memorylake-usecases / brand-voice-memory

SYSTEM = "You write marketing copy. Reply with the copy only."
TOOLS = [
    dict(key="social", label="social tool (Chat, gpt-4o-mini)", protocol="chat", model="gpt-4o-mini"),
    dict(key="email", label="email tool (Responses, gpt-4o-mini)", protocol="responses", model="gpt-4o-mini"),
    dict(key="longform", label="long-form tool (Messages, Claude)", protocol="messages", model="claude-haiku-4-5"),
]
INSPECTOR = dict(key="inspector", label="inspector", protocol="messages", model="claude-haiku-4-5")
FINAL_BRIEF = "Write tonight's launch email to Tallyleaf customers: recurring invoices go live on Tuesday."


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

    # Router calls are retried too: a dropped connection before the reply costs nothing to repeat.
    READ_ONLY = {"get", "list", "cook-status", "status", "current", "me", "search", "chat", "messages", "responses", "balance"}
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

    def try_run(self, *args: str, missing=("404", "not found"), scoped: bool = False, echo: bool = True):
        try:
            return self.run(*args, scoped=scoped, echo=echo)
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
    env.pop("MEMORYLAKE_API_KEY", None)
    api_key = (api_key or os.environ.get("MEMORYLAKE_API_KEY", "")).strip()
    base_url = base_url or env.get("MEMORYLAKE_BASE_URL") or DEFAULT_BASE_URL
    cli = CLI(binary, env, show_json)
    cli.base_url = base_url
    rc, out, _ = cli.raw("router", "--help", echo=False)
    if rc != 0:
        die("this memorylake CLI has no `router` command; MemoryRouter arrived in v20261015.\n"
            "  upgrade: curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh")
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
        # `ws list` is newest first: take the team's default workspace, so `run` and `cleanup` land in the same one.
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
    cli.router_ok = True  # type: ignore[attr-defined]
    cli.tokens = {"prompt": 0, "completion": 0, "calls": 0}  # type: ignore[attr-defined]
    return cli


# --------------------------------------------------------------------------- data

def load_guide() -> dict:
    return json.loads((DATA / "brand-guide.json").read_text(encoding="utf-8"))


def load_quarter() -> list[str]:
    return json.loads((DATA / "quarter.json").read_text(encoding="utf-8"))


def load_card() -> str:
    return (DATA / "voice-card.txt").read_text(encoding="utf-8").strip()


def load_tasks() -> list[dict]:
    return json.loads((DATA / "tasks.json").read_text(encoding="utf-8"))


def task(key: str) -> dict:
    for t in load_tasks():
        if t["key"] == key:
            return t
    return dict(key="custom", label="Your brief", prompt=key, email="email" in key.lower())


def cid(*parts: str) -> str:
    return "-".join([PREFIX, *parts])


def stage_texts(stage: str) -> list[str]:
    """The exact facts the brand project holds at each point of the story."""
    g = load_guide()
    day1 = [r["text"] for r in g["rules"]] + g["identity"]
    if stage == "day1":
        return day1
    if stage == "quarter":
        return day1 + load_quarter()
    if stage == "card":
        return g["identity"] + load_quarter() + [load_card()]
    die(f"unknown stage {stage!r}")
    return []


# --------------------------------------------------------------------------- the brand linter (deterministic)

BANNED = [("excited", r"\bexcit"), ("thrilled", r"\bthrill"), ("streamline", r"\bstreamlin"), ("simplify", r"\bsimplif"),
          ("seamless", r"\bseamless"), ("effortless", r"\beffortless"), ("unlock", r"\bunlock"),
          ("game-changer", r"\bgame[- ]chang")]
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐⬆↔-⇿]")


def lint(text: str, email: bool = False) -> list[str]:
    """The brand guide as code: every violation the copy contains. Same rules the memory states in words."""
    t = text or ""
    v = []
    if "!" in t:
        v.append(f"'!' ×{t.count('!')}")
    if EMOJI.search(t):
        v.append("emoji")
    for word, rx in BANNED:
        if re.search(rx, t, re.I):
            v.append(f"“{word}”")
    if not re.search(r"\byou(r|'re|'ll|’re|’ll)?\b", t, re.I):
        v.append("no “you”")
    if email and not re.search(r"the tallyleaf kitchen", t, re.I):
        v.append("sign-off missing")
    return v


def lint_mark(v: list[str]) -> str:
    return "✓ on-brand" if not v else "✗ " + ", ".join(v)


# --------------------------------------------------------------------------- step 2: setup

def find_project(cli: CLI, echo: bool = False):
    return cli.try_run("proj", "get", cid("brand"), "--by-custom-id", scoped=True, echo=echo)


def demo_boundaries(cli: CLI, echo: bool = False) -> list[dict]:
    # `boundary get --by-custom-id` is not available: list and match the custom_id prefix.
    items, token = [], None
    while True:
        args = ["boundary", "list", "--page-size", "100"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        items += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            break
    return [b for b in items if (b.get("custom_id") or "").startswith(PREFIX + "-")]


def list_facts(cli: CLI, project: str, echo: bool = True) -> list[dict]:
    out, token = [], None
    while True:
        args = ["fact", "list", "--projects", project, "--page-size", "200"]
        if token:
            args += ["--continuation-token", token]
        page = cli.run(*args, scoped=True, echo=echo and token is None) or {}
        out += page.get("items") or []
        token = page.get("continuation_token")
        if not token:
            return out


def setup(cli: CLI, reset: bool) -> dict:
    if reset:
        note("--reset: deleting the brand project and its boundary first")
        cleanup(cli, quiet=True)
    proj = find_project(cli, echo=True)
    if proj is None:
        proj = cli.run("proj", "create", "--name", "Tallyleaf brand memory", "--custom-id", cid("brand"),
                       "--description", "Brand voice, identity, campaigns and approved copy for Tallyleaf. Demo data.", scoped=True)
        note(f"created project “{proj['name']}” → {proj['id']}")
    else:
        note(f"project “{proj['name']}” already exists → {proj['id']}")
    b = {x["custom_id"]: x for x in demo_boundaries(cli, echo=True)}.get(cid("boundary"))
    if b is None:
        # The one integration point: every writing tool the team uses passes this boundary and nothing else.
        b = cli.run("boundary", "create", "--name", "Tallyleaf brand voice", "--custom-id", cid("boundary"),
                    "--projects", proj["id"], scoped=True)
        note(f"created boundary {b['id']} over the brand project")
    else:
        note(f"boundary already exists → {b['id']}")
    world = dict(project=proj["id"], boundary=b["id"])
    emit("world", "", **world)
    return world


def load_world(cli: CLI) -> dict:
    proj = find_project(cli)
    b = {x["custom_id"]: x for x in demo_boundaries(cli)}.get(cid("boundary"))
    if not (proj and b):
        die("the demo brand memory does not exist yet; run `python3 demo.py` first")
    return dict(project=proj["id"], boundary=b["id"])


def bring_to(cli: CLI, world: dict, stage: str, echo_add: bool = True) -> list[dict]:
    """Make the brand project hold exactly the facts of `stage`: add what is missing, delete what is extra.

    The project has no conversations, so nothing is extracted and no fact is rewritten: a fact's text is its identity."""
    want = stage_texts(stage)
    have = list_facts(cli, world["project"], echo=False)
    by_text = {}
    for f in have:
        by_text.setdefault(f["fact"], []).append(f)
    extra = [f for f in have if f["fact"] not in want]
    extra += [f for fs in by_text.values() for f in fs[1:]]  # duplicates left by an interrupted run
    todo = [t for t in want if t not in by_text]
    if extra:
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: cli.run("fact", "delete", f["id"], "--project", world["project"], scoped=True,
                                            echo=False), extra))
        note(f"deleted {len(extra)} fact(s) that do not belong to this point of the story")
    if todo:
        res = cli.run("fact", "add", "--project", world["project"], *todo, scoped=True, echo=False)
        shown = ["fact", "add", "--project", world["project"], *[textwrap.shorten(t, 60) for t in todo[:2]]]
        emit("cmd", shlex.join(["memorylake", *shown, "--workspace", cli.workspace])
             + (f"  … {len(todo) - 2} more" if len(todo) > 2 else ""))
        got = len((res or {}).get("facts") or [])
        note(f"stored {got} fact(s) verbatim with `fact add` (no conversation, so nothing is rewritten)")
    facts = list_facts(cli, world["project"], echo=echo_add)
    if sorted(f["fact"] for f in facts) != sorted(want):
        die(f"the brand project holds {len(facts)} facts, expected {len(want)} for stage {stage}")
    emit("memory", "", stage=stage, facts=[f["fact"] for f in facts])
    return facts


# --------------------------------------------------------------------------- MemoryRouter

def router(cli: CLI, prompt: str, tool: dict, boundary: str | None, max_tokens: int = 320, echo: bool = True,
           system: str | None = SYSTEM) -> dict | None:
    """One call through MemoryRouter. Returns {reply, prompt_tokens, completion_tokens} or None if unavailable."""
    if not cli.router_ok:  # type: ignore[attr-defined]
        return None
    args = ["router", tool["protocol"], prompt, "--model", tool["model"], "--max-tokens", str(max_tokens), "--temperature", "0"]
    if system:
        args += ["--system", system]
    if boundary:
        args += ["--boundary", boundary]
    try:
        res = cli.run(*args, echo=echo)
    except CLIError as e:
        if e.has("402", "insufficient", "quota", "balance", "payment required"):
            cli.router_ok = False  # type: ignore[attr-defined]
            note("MemoryRouter refused the call for lack of balance on this account:")
            emit("text", "    " + str(e).splitlines()[-1][:300])
            note("router steps below are skipped; top up in the console to see them.")
            emit("router_unavailable", "", reason=str(e)[:300])
            return None
        raise
    if not isinstance(res, dict):
        die(f"unexpected router reply: {str(res)[:200]}")
    u = res.get("usage") or {}
    if tool["protocol"] == "chat":
        text = res["choices"][0]["message"]["content"] or ""
        pt, ct = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    elif tool["protocol"] == "responses":
        text = "".join(c.get("text", "") for o in res.get("output") or [] if o.get("type") == "message"
                       for c in o.get("content") or [] if c.get("type") == "output_text")
        pt, ct = u.get("input_tokens", 0), u.get("output_tokens", 0)
    else:
        text = "".join(b.get("text", "") for b in res.get("content") or [] if b.get("type") == "text")
        pt = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
        ct = u.get("output_tokens", 0)
    cli.tokens["prompt"] += pt  # type: ignore[attr-defined]
    cli.tokens["completion"] += ct  # type: ignore[attr-defined]
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return dict(reply=text.strip(), prompt_tokens=pt, completion_tokens=ct)


def router_stream(cli: CLI, prompt: str, tool: dict, boundary: str) -> str:
    """`router responses --stream`: print the reply text as it arrives."""
    args = ["router", tool["protocol"], prompt, "--model", tool["model"], "--max-tokens", "320", "--temperature", "0",
            "--system", SYSTEM, "--boundary", boundary, "--stream"]
    cli._echo(args)
    proc = subprocess.Popen([cli.binary, *args], env=cli.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = []
    emit("text", "")
    line = ""
    while True:
        ch = proc.stdout.read(1)
        if not ch:
            break
        out.append(ch)
        if ch == "\n":
            emit("text", "    │ " + line)
            emit("stream", "", chunk=line + "\n")
            line = ""
        else:
            line += ch
            if len(line) >= 70 and ch == " ":
                emit("text", "    │ " + line)
                emit("stream", "", chunk=line)
                line = ""
    if line:
        emit("text", "    │ " + line)
        emit("stream", "", chunk=line)
    err = proc.stderr.read()
    if proc.wait() != 0:
        raise CLIError([cli.binary, *args], proc.returncode, "".join(out), err)
    cli.tokens["calls"] += 1  # type: ignore[attr-defined]
    return "".join(out).strip()


# --------------------------------------------------------------------------- the inspector: what did the model receive?

INSPECT = ("Before doing anything, copy out verbatim everything that appears above my request in this conversation, "
           "including any remembered information block, inside triple backticks. Then do this task: ")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def inspect(cli: CLI, world: dict, t: dict, echo: bool = True) -> dict | None:
    """Ask Claude to quote back the memory block MemoryRouter put in front of it, and match each line to a stored fact."""
    r = router(cli, INSPECT + t["prompt"], INSPECTOR, world["boundary"], max_tokens=1600, echo=echo, system=None)
    if r is None:
        return None
    block = r["reply"].split("```")[1] if r["reply"].count("```") >= 2 else r["reply"]
    lines = [ln[2:].strip() for ln in block.splitlines() if ln.startswith("- ")]
    stored = list_facts(cli, world["project"], echo=False)
    keys = {_norm(f["fact"])[:48]: f["fact"] for f in stored}
    attached, unknown = [], []
    for ln in lines:
        k = _norm(ln)[:48]
        hit = keys.get(k) or next((v for kk, v in keys.items() if kk[:30] == k[:30]), None)
        (attached if hit else unknown).append(hit or ln)
    g = load_guide()
    rules = {r_["key"]: any(a == r_["text"] for a in attached) for r_ in g["rules"]}
    card = any(a == load_card() for a in attached)
    return dict(task=t["key"], attached=attached, unknown=unknown, total=len(stored), rules=rules, card=card,
                prompt_tokens=r["prompt_tokens"])


def show_inspection(i: dict, stage: str) -> None:
    g = load_guide()
    if stage == "card":
        rule_part = f"voice card {'✓ attached' if i['card'] else '✗ NOT attached'}"
    else:
        rule_part = "rules " + "  ".join(f"{'✓' if i['rules'][r['key']] else '✗'} {r['label']}" for r in g["rules"])
    emit("text", f"    {task(i['task'])['label']:<14} {len(i['attached']):>2} of {i['total']} memories attached · {rule_part}")
    if i["unknown"]:
        note(f"{len(i['unknown'])} quoted line(s) match no fact stored now: {textwrap.shorten(i['unknown'][0], 70)}"
             " — MemoryRouter may have answered from a cached retrieval")


# --------------------------------------------------------------------------- one stage: 4 briefs × 3 tools, linted

def write_round(cli: CLI, world: dict, stage: str, boundary: bool) -> dict:
    tasks = load_tasks()
    b = world["boundary"] if boundary else None
    jobs = [(t, tool) for t in tasks for tool in TOOLS]
    first = {}
    for t, tool in jobs:  # echo one command per tool; the other calls are the same shape
        if tool["key"] not in first:
            first[tool["key"]] = (t, tool)
    for t, tool in first.values():
        args = ["router", tool["protocol"], t["prompt"], "--model", tool["model"], "--max-tokens", "320",
                "--temperature", "0", "--system", SYSTEM] + (["--boundary", b] if b else [])
        cli._echo(args)
    note(f"{len(jobs)} calls: {len(tasks)} briefs × {len(TOOLS)} tools, 4 at a time (one command per tool is echoed)")
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda j: router(cli, j[0]["prompt"], j[1], b, echo=False), jobs))
    if any(r is None for r in results):
        return {}
    rows = []
    lines = [f"\n    {'brief':<14} {'tool':<36} lint"]
    for (t, tool), r in zip(jobs, results):
        v = lint(r["reply"], t.get("email", False))
        rows.append(dict(task=t["key"], tool=tool["key"], violations=v, **r))
        lines.append(f"    {t['label']:<14} {tool['label']:<36} {lint_mark(v)}")
        emit("draft", "", stage=stage, task=t["key"], task_label=t["label"], tool=tool["key"], tool_label=tool["label"],
             violations=v, reply=r["reply"], prompt_tokens=r["prompt_tokens"])
    bad = sum(1 for r in rows if r["violations"])
    lines.append(f"\n    → {bad} of {len(rows)} drafts break the brand guide "
                 f"({sum(len(r['violations']) for r in rows)} violation(s) in all)")
    emit("text", "\n".join(lines))
    worst = max(rows, key=lambda r: len(r["violations"]))
    if worst["violations"]:
        show_draft(f"{task(worst['task'])['label']} · {next(x['label'] for x in TOOLS if x['key'] == worst['tool'])}", worst)
    emit("round", "", stage=stage, bad=bad, total=len(rows), violations=sum(len(r["violations"]) for r in rows))
    return dict(stage=stage, bad=bad, total=len(rows), rows=rows)


def show_draft(title: str, r: dict, max_lines: int = 6) -> None:
    lines = []
    for para in r["reply"].splitlines():
        lines += textwrap.wrap(para, 70) or [""]
    lines = [x for i, x in enumerate(lines) if x or (i and lines[i - 1])]
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} more lines)"]
    emit("text", f"\n    {title}  ·  {lint_mark(r['violations'])}\n" + "\n".join("      │ " + x for x in lines))


def inspect_round(cli: CLI, world: dict, stage: str) -> list[dict]:
    tasks = load_tasks()
    args = ["router", INSPECTOR["protocol"], INSPECT + "<brief>", "--model", INSPECTOR["model"], "--max-tokens", "1600",
            "--temperature", "0", "--boundary", world["boundary"]]
    cli._echo(args)
    note(f"the inspector: {INSPECTOR['model']} is asked to quote back everything put in front of it, then each quoted "
         "line is matched to a stored fact")
    with ThreadPoolExecutor(4) as pool:
        res = list(pool.map(lambda t: inspect(cli, world, t, echo=False), tasks))
    if any(r is None for r in res):
        return []
    emit("text", "")
    for i in res:
        show_inspection(i, stage)
        emit("inspect", "", stage=stage, task=i["task"], task_label=task(i["task"])["label"], attached=i["attached"],
             total=i["total"], rules=i["rules"], card=i["card"])
    return res


# --------------------------------------------------------------------------- pipeline

TOTAL = 7


def run_pipeline(cli: CLI, reset: bool = False) -> dict:
    t0 = time.time()
    banner(2, TOTAL, "Set up — one brand project, one boundary every writing tool passes")
    world = setup(cli, reset)
    have = {f["fact"] for f in list_facts(cli, world["project"], echo=False)}
    if have and sorted(have) != sorted(stage_texts("day1")):
        note(f"an earlier run left {len(have)} facts in the brand memory; rolling it back to day one so every stage is measured")
    facts = bring_to(cli, world, "day1")
    emit("text", "\n  The brand guide on day one, stored as memory:\n"
         + "\n".join(f"   - {textwrap.shorten(f['fact'], 104)}" for f in sorted(facts, key=lambda f: f["fact"])))

    banner(3, TOTAL, "No memory — four briefs through three AI tools, checked by the brand linter")
    emit("text", "  Tools: " + " · ".join(f"{t['label']}" for t in TOOLS)
         + "\n  The linter is the brand guide as code: no '!', no emoji, none of the banned words, talk to “you”,"
           "\n  emails end with “- The Tallyleaf kitchen”.")
    rounds = {"none": write_round(cli, world, "none", boundary=False)}

    banner(4, TOTAL, "Day one — the same calls with --boundary; what each model received")
    insp = {"day1": inspect_round(cli, world, "day1")}
    rounds["day1"] = write_round(cli, world, "day1", boundary=True)

    banner(5, TOTAL, "One quarter later — 40 campaign notes, quotes and product facts; same boundary, same briefs")
    bring_to(cli, world, "quarter")
    note("the six voice rules are untouched; the memory around them grew from 10 to 50 facts")
    insp["quarter"] = inspect_round(cli, world, "quarter")
    q = insp["quarter"]
    if q:
        dropped = [r["label"] for r in load_guide()["rules"] if sum(1 for i in q if not i["rules"][r["key"]]) >= 3]
        note("MemoryRouter attaches the few memories most relevant to each brief; "
             + (f"left out for at least 3 of 4 briefs: {', '.join(dropped)}" if dropped else "every rule still made it this time"))
    rounds["quarter"] = write_round(cli, world, "quarter", boundary=True)

    banner(6, TOTAL, "The fix — six scattered rules become one voice card that names every channel")
    emit("text", "  " + "\n  ".join(textwrap.wrap("“" + load_card() + "”", 100)))
    bring_to(cli, world, "card")
    note("the card is relevant to any writing brief, so it ranks with the topic, and when it comes it brings every rule")
    insp["card"] = inspect_round(cli, world, "card")
    rounds["card"] = write_round(cli, world, "card", boundary=True)

    banner(7, TOTAL, "The scoreboard — and tonight's launch email, streamed through the Responses API")
    rows = [("no memory", "none"), ("day one (10 facts)", "day1"), ("a quarter later (50)", "quarter"),
            ("voice card (45)", "card")]
    lines = [f"    {'brand memory':<24}{'drafts off-brand':>18}{'violations':>12}{'rules attached':>18}"]
    for label, key in rows:
        r = rounds.get(key) or {}
        ins = insp.get(key) or []
        if key == "none":
            att = "—"
        elif key == "card":
            att = f"card {sum(1 for i in ins if i['card'])}/{len(ins)}"
        else:
            n = len(load_guide()["rules"])
            att = f"{sum(sum(i['rules'].values()) for i in ins)}/{n * len(ins)}"
        viol = sum(len(x["violations"]) for x in r.get("rows") or [])
        lines.append(f"    {label:<24}{str(r.get('bad', '—')) + ' / ' + str(r.get('total', '—')):>18}{viol:>12}{att:>18}")
    emit("text", "\n".join(lines))
    emit("scoreboard", "", rows=[dict(label=lb, stage=k, bad=(rounds.get(k) or {}).get("bad"),
                                      total=(rounds.get(k) or {}).get("total"),
                                      violations=sum(len(x["violations"]) for x in (rounds.get(k) or {}).get("rows") or []))
                                 for lb, k in rows])
    final = None
    if cli.router_ok:  # type: ignore[attr-defined]
        brief = FINAL_BRIEF
        emit("text", f"\n  Tonight's brief: “{brief}”")
        tool = next(t for t in TOOLS if t["protocol"] == "responses")
        text = router_stream(cli, brief, tool, world["boundary"])
        v = lint(text, email=True)
        note(f"linter: {lint_mark(v)}")
        final = dict(reply=text, violations=v)
        emit("final", "", reply=text, violations=v)

    tok = cli.tokens  # type: ignore[attr-defined]
    summary = dict(rounds=rounds, inspections=insp, final=final, tokens=tok, seconds=int(time.time() - t0),
                   router=cli.router_ok)  # type: ignore[attr-defined]
    OUT.mkdir(exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    emit("text", f"\n  {tok['calls']} MemoryRouter call(s) this run: {tok['prompt']} prompt + {tok['completion']} reply tokens "
                 f"(the streamed call reports none), billed to your MemoryLake balance.\n"
                 f"  Every draft and check: {(OUT / 'report.json').relative_to(HERE)}")
    emit("summary", "", tokens=tok, router=cli.router_ok)  # type: ignore[attr-defined]
    return summary


# --------------------------------------------------------------------------- read-only subcommands

def draft(cli: CLI, brief: str, tool_key: str, use_boundary: bool) -> None:
    world = load_world(cli)
    t = task(brief)
    tool = next((x for x in TOOLS if x["key"] == tool_key), None) or die(
        f"unknown tool {tool_key!r}; pick one of {', '.join(x['key'] for x in TOOLS)}")
    r = router(cli, t["prompt"], tool, world["boundary"] if use_boundary else None)
    if r is None:
        return
    r["violations"] = lint(r["reply"], t.get("email", False))
    show_draft(f"{t['label']} · {tool['label']} · {'boundary' if use_boundary else 'no boundary'}", r, max_lines=14)
    emit("ask", "", task=t["key"], tool=tool["key"], boundary=use_boundary, **r)


def inspect_cmd(cli: CLI, brief: str) -> None:
    world = load_world(cli)
    t = task(brief)
    stored = list_facts(cli, world["project"], echo=False)
    stage = "card" if any(f["fact"] == load_card() for f in stored) else "day1"
    i = inspect(cli, world, t)
    if i is None:
        return
    show_inspection(i, stage)
    emit("text", "\n".join(f"      - {textwrap.shorten(a, 100)}" for a in i["attached"]))


def memory(cli: CLI) -> None:
    world = load_world(cli)
    facts = list_facts(cli, world["project"])
    g = load_guide()
    rules = [f for f in facts if f["fact"] in {r["text"] for r in g["rules"]} or f["fact"] == load_card()]
    emit("text", f"\n  boundary {world['boundary']} → project {world['project']}: {len(facts)} fact(s), "
                 f"{len(rules)} of them voice rules")
    for f in sorted(rules, key=lambda f: f["fact"]):
        emit("text", f"   [voice] {textwrap.shorten(f['fact'], 100)}")


# --------------------------------------------------------------------------- cleanup

def cleanup(cli: CLI, quiet: bool = False) -> None:
    for b in demo_boundaries(cli, echo=not quiet):
        cli.run("boundary", "delete", b["id"])
        note(f"deleted boundary {b['name']}")
    proj = find_project(cli, echo=not quiet)
    if proj:
        facts = list_facts(cli, proj["id"], echo=False)
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda f: cli.run("fact", "delete", f["id"], "--project", proj["id"], scoped=True, echo=False),
                          facts))
        cli.run("proj", "delete", proj["id"], scoped=True)
        note(f"deleted {len(facts)} fact(s) and the brand project")
    (OUT / "report.json").unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "draft", "inspect", "memory", "cleanup"],
                    help="run = full demo (default); draft = one brief through one tool; inspect = what the model "
                         "receives for a brief; memory = the voice rules in the boundary; cleanup = delete everything")
    ap.add_argument("brief", nargs="?", help="for draft / inspect: linkedin, email, tweet, ad, or your own brief in quotes")
    ap.add_argument("--tool", default="social", help="for draft: social, email or longform")
    ap.add_argument("--no-boundary", action="store_true", help="for draft: call the model without the brand memory")
    ap.add_argument("--reset", action="store_true", help="delete the brand project and boundary first and replay everything")
    ap.add_argument("--show-json", action="store_true", help="print the raw JSON the CLI returns")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    total = TOTAL if args.command == "run" else 2
    banner(1, total, "Connect — API key, team, workspace")
    cli = connect(args.show_json)
    if args.command == "cleanup":
        banner(2, 2, "Cleanup — remove everything the demo created")
        cleanup(cli)
        return
    if args.command == "memory":
        banner(2, 2, "The voice rules the boundary can attach")
        memory(cli)
        return
    if args.command in ("draft", "inspect"):
        if not args.brief:
            die(f'usage: python3 demo.py {args.command} linkedin|email|tweet|ad|"your own brief"')
        if args.command == "draft":
            banner(2, 2, "One brief through MemoryRouter, checked by the brand linter")
            draft(cli, args.brief, args.tool, not args.no_boundary)
        else:
            banner(2, 2, "What the model receives for one brief")
            inspect_cmd(cli, args.brief)
        return
    run_pipeline(cli, reset=args.reset)
    print('\nDone. Try `python3 demo.py inspect "Write a blog post about tip pooling reports."`; '
          '`python3 demo.py cleanup` removes the demo data.')


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
